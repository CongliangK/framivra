// frame_sync_selftest.cpp — #114 五阶段状态机原生对抗自检（无 CEF 依赖）。
//
// 强制注入面（工单 DoD：全部为确定性 fixture，非概率性）：
//   旧 paint（错帧签名）、跨 generation 同帧（旧世代字节在新请求早期窗口
//   重放）、重复请求（request_id 重放）、签名半更新（P1 新 P2 旧 / 反之）、
//   泵送超时、S4 前早期 paint、S5 后晚到 paint、尺寸不符、晚到就绪回调
//   （三元组错配）、无上报原像素的邻域兜底显形。
// 运行：frame_sync_selftest [--jsonl-out=<path>] [--dump-signatures]
//   --jsonl-out=<path>     供 Python 侧跨语言全量 JSON 校验消费；
//   --dump-signatures      输出 65,536 行 "f b g r a b g r a"（P1/P2 clean
//                          预乘签名，空格分隔）到 stdout 后退出 0 —— 与
//                          vtx/render/frame_canary.py 全域逐字节比对（协议 C++ 镜像
//                          与唯一 home 的冻结一致性证据）。
// 退出 0 = ALL PASS。
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <future>
#include <limits>
#include <string>
#include <vector>

#include "external_pump_schedule.hpp"
#include "frame_sync_machine.hpp"

using namespace vtx_cef;
using namespace vtx_cef::fsync;

namespace {

int g_fail = 0;
int g_pass = 0;

void Check(bool ok, const char* what) {
  if (ok) {
    ++g_pass;
    std::printf("PASS %s\n", what);
  } else {
    ++g_fail;
    std::printf("FAIL %s\n", what);
  }
}

// ---------------------------------------------------------------- 帧缓冲 fixture

struct FrameBuf {
  std::vector<uint8_t> px;
  uint32_t w = 0;
  uint32_t h = 0;
  explicit FrameBuf(uint32_t width, uint32_t height)
      : px(static_cast<size_t>(width) * height * 4u, 0), w(width), h(height) {
    // 底纹：(x ^ y) 变化保证行 0 与行 1、列 0 与列 1 互异（邻域复制可检）。
    for (uint32_t y = 0; y < h; ++y) {
      for (uint32_t x = 0; x < w; ++x) {
        SetPx(x, y, Bgra{static_cast<uint8_t>(x ^ y), static_cast<uint8_t>(x),
                         static_cast<uint8_t>(y ^ 0x5A), 255});
      }
    }
  }
  size_t Off(uint32_t x, uint32_t y) const {
    return (static_cast<size_t>(y) * w + x) * 4u;
  }
  void SetPx(uint32_t x, uint32_t y, Bgra c) {
    std::memcpy(&px[Off(x, y)], &c, 4);
  }
  Bgra GetPx(uint32_t x, uint32_t y) const {
    Bgra c;
    std::memcpy(&c, &px[Off(x, y)], 4);
    return c;
  }
  // v2 clean 双像素 Canary：P1@(0,0) + P2@(1,0)（预乘存储字节）。
  void DrawCanaryClean(int f) {
    SetPx(0, 0, SignatureBgraPremulP1(f));
    SetPx(1, 0, SignatureBgraPremulP2(f));
  }
  // Exact proof: frame16 + request32 inside the original 8x8 footprint.
  void DrawExact(int f, uint32_t request, bool relocated) {
    const int mask = relocated ? 65535 : 0;
    for (uint32_t y = 0; y < 8; ++y) {
      const int value = (y < 4 ? f : y < 6 ? static_cast<int>(request & 65535u)
                                          : static_cast<int>(request >> 16u)) ^ mask;
      uint8_t one[3], two[3];
      SignatureRgbP1(true, value, one);
      SignatureRgbP2(true, value, two);
      for (uint32_t x = 0; x < 8; ++x) {
        SetPx(relocated ? w - 8 + x : x, y,
              Bgra{one[2], one[1], one[0], 255});
        SetPx(relocated ? x : w - 8 + x, h - 8 + y,
              Bgra{two[2], two[1], two[0], 255});
      }
    }
  }

  // 诊断双对角 8x8 Canary（不透明 → 预乘恒等）。
  void DrawCanaryDiag(int f) {
    uint8_t p1[3], p2[3];
    SignatureRgbP1(true, f, p1);
    SignatureRgbP2(true, f, p2);
    for (uint32_t dy = 0; dy < 8; ++dy) {
      for (uint32_t dx = 0; dx < 8; ++dx) {
        SetPx(dx, dy, Bgra{p1[2], p1[1], p1[0], kDiagAlpha});
        SetPx(w - 1 - dx, h - 1 - dy, Bgra{p2[2], p2[1], p2[0], kDiagAlpha});
      }
    }
  }
};

// fixture 时点由测试显式传入（状态机判定与真实时钟解耦）。

struct Harness {
  FrameSyncStateMachine m;
  std::vector<std::string> lines;

  Harness() { m.SetEventSink([this](const std::string& l) { lines.push_back(l); }); }

  // 标准推进：S1 →（assets）→ S2 →（canary+原像素）→ S3。
  bool AdvanceToS3(const RequestDesc& d, const uint8_t orig_rgba[2][4],
                   uint64_t now) {
    if (!m.BeginRequest(d, now)) return false;
    PageEvent a;
    a.phase = PageEvent::Phase::kAssetsReady;
    a.document_gen = d.document_gen;
    a.request_id = d.request_id;
    a.target_frame_index = d.target_frame_index;
    if (!m.NotifyPageEvent(a, now + 100)) return false;
    PageEvent c = a;
    c.phase = PageEvent::Phase::kCanarySet;
    if (orig_rgba != nullptr) {
      c.has_reported_originals = true;
      std::memcpy(c.original_rgba, orig_rgba, sizeof(c.original_rgba));
    }
    return m.NotifyPageEvent(c, now + 200);
  }
};

// ---------------------------------------------------------------- 场景

// ① Happy path：S0→S5 全链 + 上报原像素恢复（≠邻域）+ JSONL 转移序列。
void TestHappyPathClean() {
  Harness t;
  const int f = 0x1234;
  // 上报原像素（RGBA 直通；A=255 → 预乘恒等）：刻意 ≠ 行 1 邻域底纹。
  const uint8_t orig[2][4] = {{10, 20, 30, 255}, {40, 50, 60, 255}};
  RequestDesc d;
  d.document_gen = 1;
  d.request_id = 1;
  d.target_frame_index = f;
  d.drive = DriveMode::kModeN;
  d.canary = CanaryMode::kClean;
  d.expected_w = 64;
  d.expected_h = 48;
  Check(t.AdvanceToS3(d, orig, 1000), "happy: S0→S1→S2→S3 推进");
  Check(t.m.EnterPumping(1200), "happy: S3→S4");
  FrameBuf buf(64, 48);
  const Bgra keep1 = buf.GetPx(1, 0);   // 保存底纹（canary 覆盖前的位置本值
  const Bgra keep2 = buf.GetPx(1, 1);   // 由上报原像素替代表达）
  (void)keep1;
  (void)keep2;
  buf.DrawCanaryClean(f);
  FrameBuf expect = buf;  // 未恢复对照（canary 仍在）
  std::vector<uint8_t> dst(64 * 48 * 4, 0xAA);
  PaintVerdict v = t.m.FeedPaint(buf.px.data(), 64 * 4, 64, 48, dst.data(),
                                 1500);
  Check(v.disposition == PaintDisposition::kAccepted, "happy: 接受");
  Check(v.decoded_frame == f, "happy: decoded 来自解码结果 == 目标帧");
  Check(v.restored && v.restore_from_reported, "happy: 上报原像素恢复");
  Check(t.m.state() == State::S5_ONPAINT_VERIFIED, "happy: S5");
  Bgra r1, r2;
  std::memcpy(&r1, &dst[0], 4);
  std::memcpy(&r2, &dst[4], 4);
  const Bgra want1{30, 20, 10, 255};  // RGBA(10,20,30,255) → BGRA
  const Bgra want2{60, 50, 40, 255};
  Check(r1 == want1 && r2 == want2, "happy: 恢复字节 == 上报原像素");
  Check(std::memcmp(&dst[0], &expect.px[0], 8) != 0,
        "happy: 恢复后 ≠ canary 残留");
  const Bgra n1 = FrameBuf(64, 48).GetPx(1, 0);  // 邻域行 1 底纹
  Check(r1 != n1, "happy: 恢复 ≠ 邻域复制（上报优先，非通用邻域法）");
  Check(v.accepted_elapsed_us_since_ready == 300,
        "happy: accepted_elapsed_us_since_ready = S3→S5（300us）");
  t.m.Reset(2000, "done");
  // JSONL 序列断言
  const char* want_seq[] = {"request_begin", "state_transition",
                            "state_transition", "state_transition",
                            "state_transition", "request_end"};
  size_t idx = 0;
  for (const char* wv : want_seq) {
    bool found = false;
    for (; idx < t.lines.size(); ++idx) {
      if (t.lines[idx].find(std::string("\"event\":\"") + wv + "\"") !=
          std::string::npos) {
        found = true;
        ++idx;
        break;
      }
    }
    if (!found) {
      Check(false, "happy: JSONL 事件序列完备");
      return;
    }
  }
  Check(true, "happy: JSONL 事件序列完备");
  bool has_elapsed = false, has_rej = false;
  for (const auto& l : t.lines) {
    if (l.find("\"accepted_elapsed_us_since_ready\":300") != std::string::npos)
      has_elapsed = true;
    if (l.find("\"rejected_stale_paint_count\":0") != std::string::npos)
      has_rej = true;
  }
  Check(has_elapsed && has_rej, "happy: JSONL 含时序与拒收计数字段");
}

// ② S4 前早期 paint 丢弃（含跨世代同帧字节重放）+ 缓冲不被污染。
void TestEarlyPaintDropped() {
  Harness t;
  const int f = 0x0BEE;
  const uint8_t orig[2][4] = {{1, 2, 3, 200}, {4, 5, 6, 210}};
  RequestDesc d;
  d.document_gen = 3;
  d.request_id = 9;
  d.target_frame_index = f;
  d.expected_w = 32;
  d.expected_h = 16;
  Check(t.m.BeginRequest(d, 100), "early: S1 开局");
  FrameBuf buf(32, 16);
  buf.DrawCanaryClean(f);  // 即便字节精确匹配目标签名——S4 前一律丢弃
  std::vector<uint8_t> dst(32 * 16 * 4, 0xAA);
  PaintVerdict v = t.m.FeedPaint(buf.px.data(), 32 * 4, 32, 16, dst.data(),
                                 110);
  Check(v.disposition == PaintDisposition::kEarlyDropped,
        "early: S1 期间 paint 丢弃");
  Check(t.m.counters().early_paint_dropped == 1, "early: 计数 1");
  bool untouched = true;
  for (uint8_t byte : dst) {
    if (byte != 0xAA) untouched = false;
  }
  Check(untouched, "early: 丢弃帧不写目标缓冲");
  // 推进到 S2/S3 各再喂一次（S2：assets 已到）
  PageEvent a;
  a.phase = PageEvent::Phase::kAssetsReady;
  a.document_gen = 3;
  a.request_id = 9;
  a.target_frame_index = f;
  Check(t.m.NotifyPageEvent(a, 120), "early: S1→S2");
  v = t.m.FeedPaint(buf.px.data(), 32 * 4, 32, 16, dst.data(), 130);
  Check(v.disposition == PaintDisposition::kEarlyDropped &&
            t.m.counters().early_paint_dropped == 2,
        "early: S2 期间 paint 丢弃");
  PageEvent c = a;
  c.phase = PageEvent::Phase::kCanarySet;
  c.has_reported_originals = true;
  std::memcpy(c.original_rgba, orig, sizeof(c.original_rgba));
  Check(t.m.NotifyPageEvent(c, 140), "early: S2→S3");
  v = t.m.FeedPaint(buf.px.data(), 32 * 4, 32, 16, dst.data(), 150);
  Check(v.disposition == PaintDisposition::kEarlyDropped &&
            t.m.counters().early_paint_dropped == 3,
        "early: S3 期间 paint 丢弃");
  Check(t.m.state() == State::S3_JS_FRAME_APPLIED_AND_CANARY_SET,
        "early: 状态不受早期 paint 影响");
  // 转正：S4 → 接受
  Check(t.m.EnterPumping(160), "early: S3→S4");
  v = t.m.FeedPaint(buf.px.data(), 32 * 4, 32, 16, dst.data(), 170);
  Check(v.disposition == PaintDisposition::kAccepted, "early: S4 后接受");
  t.m.Reset(200, "done");
}

// ③ 旧 paint 拒收（错帧签名）+ 归因 + 继续泵送后接受。
void TestStalePaintReject() {
  Harness t;
  const int f = 450;
  RequestDesc d;
  d.document_gen = 1;
  d.request_id = 1;
  d.target_frame_index = f;
  d.expected_w = 32;
  d.expected_h = 16;
  Check(t.AdvanceToS3(d, nullptr, 0), "stale: 推进到 S3");
  Check(t.m.EnterPumping(10), "stale: S4");
  FrameBuf old(32, 16);
  old.DrawCanaryClean(f - 1);  // 旧帧（滞后 1 帧）
  std::vector<uint8_t> dst(32 * 16 * 4, 0xAA);
  PaintVerdict v = t.m.FeedPaint(old.px.data(), 32 * 4, 32, 16, dst.data(), 20);
  Check(v.disposition == PaintDisposition::kRejectedStale,
        "stale: 旧帧签名拒收");
  Check(v.decoded_frame == f - 1, "stale: 拒收归因 rendered=f-1");
  Check(t.m.counters().rejected_stale_paint == 1, "stale: 拒收计数 1");
  Check(t.m.state() == State::S4_COMPOSITOR_PUMPING,
        "stale: 拒收后保持 S4 继续泵送");
  FrameBuf good(32, 16);
  good.DrawCanaryClean(f);
  v = t.m.FeedPaint(good.px.data(), 32 * 4, 32, 16, dst.data(), 30);
  Check(v.disposition == PaintDisposition::kAccepted, "stale: 后续正确帧接受");
  Check(t.m.counters().rejected_stale_paint == 1, "stale: 计数不复位漂移");
  t.m.Reset(40, "done");
}

// ④ 签名半更新：P1 新 P2 旧 / P1 旧 P2 新 —— 双像素闸门下均不可接受。
void TestHalfSignature() {
  Harness t;
  const int f = 900;
  RequestDesc d;
  d.document_gen = 1;
  d.request_id = 1;
  d.target_frame_index = f;
  d.expected_w = 32;
  d.expected_h = 16;
  Check(t.AdvanceToS3(d, nullptr, 0), "half: 推进到 S3");
  Check(t.m.EnterPumping(10), "half: S4");
  std::vector<uint8_t> dst(32 * 16 * 4, 0xAA);
  {
    FrameBuf buf(32, 16);
    buf.SetPx(0, 0, SignatureBgraPremulP1(f));      // P1 = 新
    buf.SetPx(1, 0, SignatureBgraPremulP2(f - 1));  // P2 = 旧（半更新）
    PaintVerdict v = t.m.FeedPaint(buf.px.data(), 32 * 4, 32, 16, dst.data(),
                                   20);
    Check(v.disposition == PaintDisposition::kRejectedStale && v.half_signature,
          "half: P1新P2旧 拒收（half_signature）");
  }
  {
    FrameBuf buf(32, 16);
    buf.SetPx(0, 0, SignatureBgraPremulP1(f - 1));  // P1 = 旧
    buf.SetPx(1, 0, SignatureBgraPremulP2(f));      // P2 = 新（半更新）
    PaintVerdict v = t.m.FeedPaint(buf.px.data(), 32 * 4, 32, 16, dst.data(),
                                   30);
    Check(v.disposition == PaintDisposition::kRejectedStale && v.half_signature,
          "half: P1旧P2新 拒收（half_signature）");
  }
  Check(t.m.counters().rejected_stale_paint == 2, "half: 拒收计数 2");
  Check(t.m.state() == State::S4_COMPOSITOR_PUMPING, "half: 仍在 S4");
  FrameBuf good(32, 16);
  good.DrawCanaryClean(f);
  PaintVerdict v = t.m.FeedPaint(good.px.data(), 32 * 4, 32, 16, dst.data(), 40);
  Check(v.disposition == PaintDisposition::kAccepted, "half: 完整签名接受");
  t.m.Reset(50, "done");
}

// ⑤ 跨 generation 同帧：旧世代同帧号字节在 S1 早期窗口重放 → 早期丢弃
//    （帧号签名不编码世代 —— 隔离由状态机时序围栏承担）；旧世代晚到
//    就绪回调三元组错配拒收。
void TestCrossGenSameFrame() {
  Harness t;
  const int f = 7;
  RequestDesc d1;
  d1.document_gen = 1;
  d1.request_id = 1;
  d1.target_frame_index = f;
  d1.expected_w = 32;
  d1.expected_h = 16;
  Check(t.AdvanceToS3(d1, nullptr, 0), "xgen: gen1 到 S3");
  Check(t.m.EnterPumping(10), "xgen: gen1 S4");
  FrameBuf buf(32, 16);
  buf.DrawCanaryClean(f);
  std::vector<uint8_t> dst1(32 * 16 * 4, 0);
  Check(t.m.FeedPaint(buf.px.data(), 32 * 4, 32, 16, dst1.data(), 20)
            .disposition == PaintDisposition::kAccepted,
        "xgen: gen1 接受");
  t.m.Reset(30, "done");

  RequestDesc d2 = d1;
  d2.document_gen = 2;
  d2.request_id = 2;
  Check(t.m.BeginRequest(d2, 100), "xgen: gen2 开局（同帧号 f=7）");
  // —— 旧世代同帧字节重放：与期望签名逐字节全等，但处于 S1 → 早期丢弃。
  std::vector<uint8_t> dst2(32 * 16 * 4, 0xAA);
  PaintVerdict v = t.m.FeedPaint(buf.px.data(), 32 * 4, 32, 16, dst2.data(),
                                 110);
  Check(v.disposition == PaintDisposition::kEarlyDropped,
        "xgen: 旧世代同帧字节在 S1 被早期围栏丢弃");
  bool untouched = true;
  for (uint8_t byte : dst2) {
    if (byte != 0xAA) untouched = false;
  }
  Check(untouched, "xgen: 旧世代字节不污染目标缓冲");
  // —— 旧世代晚到就绪回调（gen=1, req=1）→ 三元组错配拒收。
  PageEvent stale_cb;
  stale_cb.phase = PageEvent::Phase::kAssetsReady;
  stale_cb.document_gen = 1;
  stale_cb.request_id = 1;
  stale_cb.target_frame_index = f;
  Check(!t.m.NotifyPageEvent(stale_cb, 120),
        "xgen: 旧世代就绪回调拒收");
  Check(t.m.counters().late_ready_cb_rejected == 1, "xgen: 晚到回调计数 1");
  Check(t.m.state() == State::S1_NAV_OR_EVAL_ISSUED, "xgen: 状态不被旧回调推进");
  // —— gen2 正常完成。
  Check(t.AdvanceToS3(d2, nullptr, 200) == false, "xgen: AdvanceToS3 需重开");
  // （上面 AdvanceToS3 从 S1 重新 BeginRequest 会因非 S0 拒绝 —— 手工推进：）
  PageEvent a;
  a.phase = PageEvent::Phase::kAssetsReady;
  a.document_gen = 2;
  a.request_id = 2;
  a.target_frame_index = f;
  Check(t.m.NotifyPageEvent(a, 210), "xgen: gen2 S1→S2");
  PageEvent c = a;
  c.phase = PageEvent::Phase::kCanarySet;
  Check(t.m.NotifyPageEvent(c, 220), "xgen: gen2 S2→S3");
  Check(t.m.EnterPumping(230), "xgen: gen2 S4");
  v = t.m.FeedPaint(buf.px.data(), 32 * 4, 32, 16, dst2.data(), 240);
  Check(v.disposition == PaintDisposition::kAccepted, "xgen: gen2 接受");
  Check(t.m.counters().early_paint_dropped >= 1 &&
            t.m.counters().late_ready_cb_rejected == 1,
        "xgen: 世代隔离证据（early fence + 回调拒收）");
  t.m.Reset(250, "done");
}

// ⑥ 重复请求：在途重发 + 终态后重发均拒绝；新 request_id 可开局。
void TestDuplicateRequest() {
  Harness t;
  RequestDesc d;
  d.document_gen = 5;
  d.request_id = 77;
  d.target_frame_index = 12;
  d.expected_w = 32;
  d.expected_h = 16;
  Check(t.m.BeginRequest(d, 0), "dup: 首次开局");
  Check(!t.m.BeginRequest(d, 10), "dup: 在途同 request_id 拒绝");
  Check(t.m.counters().duplicate_request_rejected == 1, "dup: 计数 1");
  Check(t.m.state() == State::S1_NAV_OR_EVAL_ISSUED, "dup: 状态不变");
  Check(t.AdvanceToS3(RequestDesc{}, nullptr, 0) == false, "dup: 非法请求拒绝");
  // 完成 77 号后重发 → 仍拒绝（重放守卫）。
  PageEvent a;
  a.phase = PageEvent::Phase::kAssetsReady;
  a.document_gen = 5;
  a.request_id = 77;
  a.target_frame_index = 12;
  Check(t.m.NotifyPageEvent(a, 20), "dup: S2");
  PageEvent c = a;
  c.phase = PageEvent::Phase::kCanarySet;
  Check(t.m.NotifyPageEvent(c, 30), "dup: S3");
  Check(t.m.EnterPumping(40), "dup: S4");
  FrameBuf buf(32, 16);
  buf.DrawCanaryClean(12);
  std::vector<uint8_t> dst(32 * 16 * 4, 0);
  Check(t.m.FeedPaint(buf.px.data(), 32 * 4, 32, 16, dst.data(), 50)
            .disposition == PaintDisposition::kAccepted,
        "dup: 77 号完成");
  t.m.Reset(60, "done");
  Check(!t.m.BeginRequest(d, 70), "dup: 终态后同 request_id 重发拒绝");
  Check(t.m.counters().duplicate_request_rejected == 2, "dup: 计数 2");
  RequestDesc d2 = d;
  d2.request_id = 78;
  Check(t.m.BeginRequest(d2, 80), "dup: 新 request_id 可开局");
  t.m.Reset(90, "abort");
}

// ⑦ 泵送超时：FailRequest 终态 + 可复用。
void TestPumpTimeout() {
  Harness t;
  RequestDesc d;
  d.document_gen = 1;
  d.request_id = 1;
  d.target_frame_index = 300;
  d.expected_w = 32;
  d.expected_h = 16;
  Check(t.AdvanceToS3(d, nullptr, 0), "timeout: 到 S3");
  Check(t.m.EnterPumping(10), "timeout: S4");
  FrameBuf stale(32, 16);
  stale.DrawCanaryClean(299);
  std::vector<uint8_t> dst(32 * 16 * 4, 0);
  for (int i = 0; i < 5; ++i) {
    t.m.FeedPaint(stale.px.data(), 32 * 4, 32, 16, dst.data(), 20 + i);
  }
  Check(t.m.counters().rejected_stale_paint == 5, "timeout: 持续拒收计数");
  Check(t.m.FailRequest("pump_timeout", 5010), "timeout: FailRequest 终态");
  Check(t.m.state() == State::S0_IDLE, "timeout: 回 S0 可复用");
  Check(t.m.counters().requests_failed == 1, "timeout: 失败计数 1");
  bool has_timeout_event = false;
  for (const auto& l : t.lines) {
    if (l.find("\"reason\":\"pump_timeout\"") != std::string::npos)
      has_timeout_event = true;
  }
  Check(has_timeout_event, "timeout: JSONL 记录超时原因");
  RequestDesc d2 = d;
  d2.request_id = 2;
  Check(t.AdvanceToS3(d2, nullptr, 6000), "timeout: 下一请求可复用");
  t.m.Reset(7000, "abort");
}

// ⑧ 尺寸不符：拒收不拷贝；正确尺寸后接受。
void TestSizeMismatch() {
  Harness t;
  RequestDesc d;
  d.document_gen = 1;
  d.request_id = 1;
  d.target_frame_index = 21;
  d.expected_w = 32;
  d.expected_h = 16;
  Check(t.AdvanceToS3(d, nullptr, 0), "size: 到 S3");
  Check(t.m.EnterPumping(10), "size: S4");
  FrameBuf wrong(31, 16);  // 宽不符
  wrong.DrawCanaryClean(21);
  std::vector<uint8_t> dst(32 * 16 * 4, 0xAA);
  PaintVerdict v = t.m.FeedPaint(wrong.px.data(), 31 * 4, 31, 16, dst.data(),
                                 20);
  Check(v.disposition == PaintDisposition::kSizeMismatch, "size: 宽不符拒收");
  Check(t.m.counters().size_mismatch_paints == 1, "size: 计数 1");
  FrameBuf good(32, 16);
  good.DrawCanaryClean(21);
  v = t.m.FeedPaint(good.px.data(), 32 * 4, 32, 16, dst.data(), 30);
  Check(v.disposition == PaintDisposition::kAccepted, "size: 正确尺寸接受");
  t.m.Reset(40, "done");
}

// ⑨ 无上报原像素 → 邻域兜底显形（restore_from_reported=false）。
void TestNeighborFallbackExplicit() {
  Harness t;
  RequestDesc d;
  d.document_gen = 1;
  d.request_id = 1;
  d.target_frame_index = 33;
  d.expected_w = 32;
  d.expected_h = 16;
  Check(t.AdvanceToS3(d, nullptr, 0), "neighbor: 到 S3（无上报）");
  Check(t.m.EnterPumping(10), "neighbor: S4");
  FrameBuf buf(32, 16);
  buf.DrawCanaryClean(33);
  std::vector<uint8_t> dst(32 * 16 * 4, 0);
  PaintVerdict v = t.m.FeedPaint(buf.px.data(), 32 * 4, 32, 16, dst.data(), 20);
  Check(v.disposition == PaintDisposition::kAccepted, "neighbor: 接受");
  Check(v.restored && !v.restore_from_reported,
        "neighbor: 兜底路径显形（非上报源）");
  FrameBuf ref(32, 16);
  const Bgra nb1 = ref.GetPx(0, 1), nb2 = ref.GetPx(1, 1);
  Bgra g1, g2;
  std::memcpy(&g1, &dst[0], 4);
  std::memcpy(&g2, &dst[4], 4);
  Check(g1 == nb1 && g2 == nb2,
        "neighbor: 底纹行间同色 fixture 下邻域等值成立（协议允许的特例）");
  t.m.Reset(30, "done");
}

// ⑩ 诊断模式：双对角 8x8 接受 + 块内撕裂/错帧拒收。
void TestDiagnosticMode() {
  Harness t;
  RequestDesc d;
  d.document_gen = 1;
  d.request_id = 1;
  d.target_frame_index = 0x0F0E;
  d.canary = CanaryMode::kDiagnostic;
  d.expected_w = 64;
  d.expected_h = 48;
  Check(t.AdvanceToS3(d, nullptr, 0), "diag: 到 S3");
  Check(t.m.EnterPumping(10), "diag: S4");
  FrameBuf buf(64, 48);
  buf.DrawCanaryDiag(0x0F0E);
  std::vector<uint8_t> dst(64 * 48 * 4, 0);
  PaintVerdict v = t.m.FeedPaint(buf.px.data(), 64 * 4, 64, 48, dst.data(), 20);
  Check(v.disposition == PaintDisposition::kAccepted &&
            v.decoded_frame == 0x0F0E,
        "diag: 双角 8x8 签名接受");
  t.m.Reset(30, "done");

  // 撕裂带：左上块上半 f、下半 f-1（半帧）→ 拒收。
  Harness t2;
  Check(t2.AdvanceToS3(d, nullptr, 0), "diag: torn 到 S3");
  Check(t2.m.EnterPumping(10), "diag: torn S4");
  FrameBuf torn(64, 48);
  torn.DrawCanaryDiag(0x0F0E);
  {
    uint8_t p1[3];
    SignatureRgbP1(true, 0x0F0D, p1);
    for (uint32_t dx = 0; dx < 8; ++dx) {
      for (uint32_t dy = 0; dy < 4; ++dy) {  // 块内上半换成 f-1 → 色带
        torn.SetPx(dx, dy, Bgra{p1[2], p1[1], p1[0], kDiagAlpha});
      }
    }
  }
  std::vector<uint8_t> dst2(64 * 48 * 4, 0);
  v = t2.m.FeedPaint(torn.px.data(), 64 * 4, 64, 48, dst2.data(), 20);
  Check(v.disposition == PaintDisposition::kRejectedStale,
        "diag: 块内撕裂带拒收（宁拒不猜）");
  // 右下角错帧 → 拒收。
  FrameBuf br_old(64, 48);
  br_old.DrawCanaryDiag(0x0F0E);
  {
    uint8_t p2[3];
    SignatureRgbP2(true, 0x0F0D, p2);
    for (uint32_t dx = 0; dx < 8; ++dx) {
      for (uint32_t dy = 0; dy < 8; ++dy) {
        br_old.SetPx(br_old.w - 1 - dx, br_old.h - 1 - dy,
                     Bgra{p2[2], p2[1], p2[0], kDiagAlpha});
      }
    }
  }
  v = t2.m.FeedPaint(br_old.px.data(), 64 * 4, 64, 48, dst2.data(), 30);
  Check(v.disposition == PaintDisposition::kRejectedStale &&
            v.decoded_frame == 0x0F0D,
        "diag: 右下错帧拒收并归因");
  t2.m.Reset(40, "done");
}

// ⑪ 冻结公式已知值向量 + 预乘解码闭环（含 P1 单像素孪生帧 23167/23168）。
void TestFormulaVectors() {
  bool inverse_exact = true;
  for (int value = 0; value < 256; ++value) {
    std::vector<int> exhaustive;
    for (int c = 0; c < 256; ++c) {
      if ((508 * c + 255) / 510 == value) exhaustive.push_back(c);
    }
    inverse_exact = inverse_exact && PremulCands(static_cast<uint8_t>(value)) == exhaustive;
  }
  Check(inverse_exact, "formula: constant-time closed inverse == exhaustive 256x256 forward map");
  const Bgra z1 = SignatureBgraPremulP1(0);
  Check(z1 == Bgra{90, 0, 0, 254}, "formula: f=0 P1={90,0,0,254}");
  const Bgra z2 = SignatureBgraPremulP2(0);
  Check(z2 == Bgra{60, 0, 0, 254}, "formula: f=0 P2={60,0,0,254}");
  const Bgra m1 = SignatureBgraPremulP1(0xFFFF);
  // 255 直向 × A=254 预乘 → 254（半向上舍入；非恒等）
  Check(m1 == Bgra{90, 254, 254, 254}, "formula: f=0xFFFF P1={90,254,254,254}");
  const Bgra m2 = SignatureBgraPremulP2(0xFFFF);
  Check(m2 == Bgra{22, 254, 254, 254}, "formula: f=0xFFFF P2={22,254,254,254}");
  // 预乘舍入口径：floor((2C·254+255)/510)（与 vtx/render/frame_canary 一致）。
  Check(PremulRound(90, 254) == 90 && PremulRound(22, 254) == 22 &&
            PremulRound(255, 254) == 254 && PremulRound(1, 254) == 1,
        "formula: 预乘半向上舍入向量");
  // 解码闭环：期望签名字节反解候选交集 == {f}（联合孪生全域空）。
  const int probes[] = {0, 1, 0x1234, 0xFFFF, 23167, 23168, 450, 33665};
  bool all_ok = true;
  for (int f : probes) {
    const Bgra e1 = SignatureBgraPremulP1(f);
    const Bgra e2 = SignatureBgraPremulP2(f);
    std::vector<int> s1, s2, inter;
    DecodeCandidatesP1(&e1.b, &s1);
    DecodeCandidatesP2(&e2.b, &s2);
    std::set_intersection(s1.begin(), s1.end(), s2.begin(), s2.end(),
                          std::back_inserter(inter));
    if (inter.size() != 1 || inter[0] != f) {
      all_ok = false;
    }
  }
  Check(all_ok, "formula: 双像素联合解码全域探针单例闭环");
  // 陈旧帧归因：f-1 的观感反解单例 == f-1（拒收归因可信性）。
  {
    const Bgra e1 = SignatureBgraPremulP1(449);
    const Bgra e2 = SignatureBgraPremulP2(449);
    std::vector<int> s1, s2, inter;
    DecodeCandidatesP1(&e1.b, &s1);
    DecodeCandidatesP2(&e2.b, &s2);
    std::set_intersection(s1.begin(), s1.end(), s2.begin(), s2.end(),
                          std::back_inserter(inter));
    Check(inter.size() == 1 && inter[0] == 449,
          "formula: 陈旧观感归因单例（wrong_frame 可靠口径）");
  }
}

// ⑫ S5 后晚到 paint 丢弃。
void TestLatePaintAfterAccept() {
  Harness t;
  RequestDesc d;
  d.document_gen = 1;
  d.request_id = 1;
  d.target_frame_index = 60;
  d.expected_w = 32;
  d.expected_h = 16;
  Check(t.AdvanceToS3(d, nullptr, 0), "late: 到 S3");
  Check(t.m.EnterPumping(10), "late: S4");
  FrameBuf buf(32, 16);
  buf.DrawCanaryClean(60);
  std::vector<uint8_t> dst(32 * 16 * 4, 0);
  Check(t.m.FeedPaint(buf.px.data(), 32 * 4, 32, 16, dst.data(), 20)
            .disposition == PaintDisposition::kAccepted,
        "late: 接受");
  PaintVerdict v = t.m.FeedPaint(buf.px.data(), 32 * 4, 32, 16, dst.data(), 30);
  Check(v.disposition == PaintDisposition::kLateDropped,
        "late: S5 后晚到 paint 丢弃");
  Check(t.m.counters().late_paint_dropped == 1, "late: 计数 1");
  t.m.Reset(40, "done");
}

// ⑬ 非法转移面：S0 直接 EnterPumping / canary_set 前置 / 帧号越域。
void TestIllegalTransitions() {
  Harness t;
  RequestDesc d;
  d.document_gen = 1;
  d.request_id = 1;
  d.target_frame_index = 5;
  d.expected_w = 32;
  d.expected_h = 16;
  Check(!t.m.EnterPumping(0), "illegal: S0 EnterPumping 拒绝");
  Check(t.m.BeginRequest(d, 0), "illegal: 开局");
  PageEvent c;
  c.phase = PageEvent::Phase::kCanarySet;
  c.document_gen = 1;
  c.request_id = 1;
  c.target_frame_index = 5;
  Check(!t.m.NotifyPageEvent(c, 10), "illegal: S1 直收 canary_set 拒绝（严格序）");
  Check(t.m.counters().illegal_transition_rejected >= 1, "illegal: 计数");
  RequestDesc bad = d;
  bad.request_id = 9;
  bad.target_frame_index = 70000;  // 越域
  t.m.Reset(20, "abort");
  Check(!t.m.BeginRequest(bad, 30), "illegal: 帧号越域拒绝");
  RequestDesc small = d;
  small.request_id = 10;
  small.canary = CanaryMode::kDiagnostic;
  small.expected_w = 4;  // 诊断轨需 ≥8x8
  small.expected_h = 4;
  Check(!t.m.BeginRequest(small, 40), "illegal: 诊断轨画幅不足拒绝");
}

void TestNativeExact() {
  Harness t;
  RequestDesc d;
  d.document_gen = 9; d.request_id = 7; d.target_frame_index = 258;
  d.canary = CanaryMode::kExact; d.expected_w = 64; d.expected_h = 48;
  FrameBuf original(64, 48), first = original, second = original;
  PageEvent identity;
  identity.document_gen = d.document_gen;
  identity.request_id = d.request_id;
  identity.target_frame_index = d.target_frame_index;
  Check(t.AdvanceToS3(d, nullptr, 1000), "exact: S3 without page originals");
  Check(t.m.ArmExactBaseline(identity, 0, 1250) &&
        !t.m.ArmExactBaseline(identity, 0, 1251),
        "exact: unsigned readiness cannot be replaced/re-armed");
  Check(std::any_of(t.lines.begin(), t.lines.end(), [](const auto& line) {
          return line.find("\"event\":\"exact_baseline_veto_armed\"") != std::string::npos &&
                 line.find("\"role\":\"equality_veto_only\"") != std::string::npos &&
                 line.find("\"restoration_source\":false") != std::string::npos;
        }), "exact: public audit contract preserves unsigned veto-only role");
  Check(t.m.EnterPumping(1300), "exact: S4");
  first.DrawExact(258, d.request_id, false);
  second.DrawExact(258, d.request_id, true);
  std::vector<uint8_t> dst(original.px.size(), 0xAA);
  auto feed = [&](FrameBuf& b) {
    return t.m.FeedPaint(b.px.data(), 64 * 4, 64, 48, dst.data(), 1500);
  };
  Check(feed(original).disposition == PaintDisposition::kRejectedStale,
        "exact: equal unbound baseline is NOT target-frame proof");
  Check(feed(first).disposition != PaintDisposition::kAccepted,
        "exact: first verified paint NOT deliverable");
  Check(t.m.needs_relocation(), "exact: native relocation requested");
  Check(feed(first).disposition == PaintDisposition::kRejectedStale,
        "exact: queued first-paint replay rejected");
  auto drift = second;
  drift.SetPx(20, 20, Bgra{123, 45, 67, 255});
  Check(feed(drift).disposition == PaintDisposition::kRejectedStale,
        "exact: unoccluded content drift rejected");
  auto torn = second;
  torn.SetPx(63, 0, Bgra{1, 2, 3, 255});
  Check(feed(torn).disposition == PaintDisposition::kRejectedStale,
        "exact: half-updated second probe rejected");
  Check(feed(second).disposition != PaintDisposition::kAccepted && t.m.needs_baseline_veto(),
        "exact: both signed phases still require independent full-byte veto");
  Check(t.m.FeedExactBaselinePaint(identity, original.px.data(), 64 * 4,
        64, 48, dst.data(), 1550, 1).disposition != PaintDisposition::kAccepted,
        "exact: one native unsigned equality insufficient");
  const auto v = t.m.FeedExactBaselinePaint(identity, original.px.data(), 64 * 4,
                                           64, 48, dst.data(), 1560, 2);
  Check(v.disposition == PaintDisposition::kAccepted && v.decoded_frame == 258,
        "exact: both decoded tokens establish original frame");
  Check(v.restored && !v.restore_from_reported, "exact: native restoration only");
  Check(dst == original.px, "exact: EVERY BGRA byte restored (nonuniform corners)");
  t.m.Reset(1600, "done");

  // Preserve every original equality-veto negative: EACH corner, interior,
  // and missing reference. All signed phases agree in every fixture below.
  const uint32_t points[][2] = {{0, 0}, {63, 0}, {0, 47}, {63, 47}, {20, 20}};
  for (size_t i = 0; i <= 5; ++i) {
    Harness reject;
    FrameBuf wrong = original;
    if (i < 5) wrong.px[wrong.Off(points[i][0], points[i][1])] ^= 1;
    Check(reject.AdvanceToS3(d, nullptr, 2000),
          "exact veto: S3 with mismatched or absent baseline");
    if (i < 5) Check(reject.m.ArmExactBaseline(identity, 0, 2250), "exact veto: native readiness");
    Check(reject.m.EnterPumping(2300), "exact veto: S4");
    std::vector<uint8_t> untouched(original.px.size(), 0xAA);
    reject.m.FeedPaint(first.px.data(), 64 * 4, 64, 48, untouched.data(), 2400);
    const auto denied = reject.m.FeedPaint(second.px.data(), 64 * 4, 64, 48,
                                          untouched.data(), 2500);
    Check(denied.disposition != PaintDisposition::kAccepted && !reject.m.accepted(),
          "exact veto: matching signatures cannot bypass baseline");
    Check(untouched == std::vector<uint8_t>(original.px.size(), 0xAA),
          "exact veto: neither candidate nor baseline published on rejection");
    Check(reject.m.counters().requests_accepted == 0, "exact veto: zero S5 acceptances");
    for (int sample = 1; sample <= 3; ++sample) {
      const auto veto = reject.m.FeedExactBaselinePaint(identity,
          wrong.px.data(), 64 * 4, 64, 48, untouched.data(), 2600 + sample, sample);
      Check(veto.disposition != PaintDisposition::kAccepted &&
            untouched == std::vector<uint8_t>(original.px.size(), 0xAA),
            "exact veto: persistent one-byte/corner veto or missing baseline stays closed");
    }
    if (i < 5) Check(std::any_of(reject.lines.begin(), reject.lines.end(), [](const auto& line) {
          return line.find("\"event\":\"exact_baseline_mismatch\"") != std::string::npos &&
                 line.find("\"reason\":\"probe_content_perturbation\"") != std::string::npos &&
                 line.find("\"cause_unproven\":true") != std::string::npos;
        }), "exact veto: mismatch audit preserves contract without claiming an unproven cause");
  }

  // An early stable blank never establishes identity and can only delay/veto.
  // Two later fresh native equalities can certify the frozen signed candidate.
  Harness resample;
  FrameBuf blank(64, 48);
  std::fill(blank.px.begin(), blank.px.end(), 0);
  Check(resample.AdvanceToS3(d, nullptr, 3000) &&
        resample.m.ArmExactBaseline(identity, 0, 3250), "resample: initial blank is only a veto");
  Check(resample.m.EnterPumping(3300), "resample: S4");
  std::fill(dst.begin(), dst.end(), 0xAA);
  uint64_t sequence = 0;
  auto sample = [&](const PageEvent& id, const FrameBuf& b) {
    return resample.m.FeedExactBaselinePaint(id, b.px.data(), 64 * 4,
                                            64, 48, dst.data(), 3600, ++sequence);
  };
  Check(sample(identity, original).disposition != PaintDisposition::kAccepted,
        "resample: unsigned equality before signed phases cannot accept");
  resample.m.FeedPaint(first.px.data(), 64 * 4, 64, 48, dst.data(), 3400);
  Check(sample(identity, original).disposition != PaintDisposition::kAccepted,
        "resample: first signature only cannot accept");
  resample.m.FeedPaint(second.px.data(), 64 * 4, 64, 48, dst.data(), 3500);
  Check(resample.m.needs_baseline_veto(), "resample: freeze fully signed candidate");
  Check(sample(identity, blank).disposition != PaintDisposition::kAccepted &&
        sample(identity, blank).disposition != PaintDisposition::kAccepted,
        "resample: two initial stable blanks cannot accept signed target");
  Check(sample(identity, original).disposition != PaintDisposition::kAccepted,
        "resample: one equal paint insufficient");
  auto stale = identity; ++stale.document_gen;
  Check(sample(stale, original).disposition != PaintDisposition::kAccepted,
        "resample: foreign identity rejected and consecutive equality reset");
  Check(sample(identity, original).disposition != PaintDisposition::kAccepted,
        "resample: equality restarts after foreign identity");
  Check(sample(identity, blank).disposition != PaintDisposition::kAccepted,
        "resample: stale blank resets consecutive equality");
  Check(sample(identity, original).disposition != PaintDisposition::kAccepted,
        "resample: equality restarts after differing paint");
  Check(resample.m.FeedExactBaselinePaint(identity, nullptr, 64 * 4,
      64, 48, dst.data(), 3600, ++sequence).disposition != PaintDisposition::kAccepted,
        "resample: bad buffer cannot accept");
  Check(sample(identity, original).disposition != PaintDisposition::kAccepted &&
        dst == std::vector<uint8_t>(original.px.size(), 0xAA),
        "resample: destination untouched until TWO consecutive full equalities");
  const auto accepted = sample(identity, original);
  Check(accepted.disposition == PaintDisposition::kAccepted &&
        accepted.decoded_frame == 258 && accepted.restored &&
        !accepted.restore_from_reported && dst == original.px,
        "resample: exact signed output/frame identity, not baseline bytes or neighbors");
  std::fill(dst.begin(), dst.end(), 0xAA);
  Check(sample(identity, original).disposition != PaintDisposition::kAccepted &&
        dst == std::vector<uint8_t>(original.px.size(), 0xAA),
        "resample: late S5 baseline cannot republish");
  resample.m.Reset(3700, "done");
  ++d.request_id;
  Check(resample.AdvanceToS3(d, nullptr, 4000), "resample: new request");
  Check(resample.m.EnterPumping(4300) && !resample.m.needs_baseline_veto(),
        "resample: pending candidate cleared across request");
  Check(sample(identity, original).disposition != PaintDisposition::kAccepted,
        "resample: previous candidate/identity cannot accept new request");
  resample.m.FailRequest("done", 4400);
}

void TestExactBaselineBatchBoundary() {
  for (const auto drive : {DriveMode::kModeN, DriveMode::kModeP}) {
    Harness t;
    RequestDesc d;
    d.document_gen = 9; d.request_id = 7; d.target_frame_index = 258;
    d.canary = CanaryMode::kExact; d.drive = drive;
    d.expected_w = 32; d.expected_h = 24;
    PageEvent id;
    id.document_gen = d.document_gen; id.request_id = d.request_id;
    id.target_frame_index = d.target_frame_index;
    FrameBuf original(32, 24), first = original, second = original, mismatch = original;
    first.DrawExact(d.target_frame_index, d.request_id, false);
    second.DrawExact(d.target_frame_index, d.request_id, true);
    mismatch.px.back() ^= 1;
    const std::vector<uint8_t> untouched(original.px.size(), 0xAA);
    auto dst = untouched;
    Check(t.AdvanceToS3(d, nullptr, 1000) &&
          t.m.ArmExactBaseline(id, 10, 1250) && t.m.EnterPumping(1300),
          "batch veto: native-ready request");
    t.m.FeedPaint(first.px.data(), 32 * 4, 32, 24, dst.data(), 1400);
    t.m.FeedPaint(second.px.data(), 32 * 4, 32, 24, dst.data(), 1500);
    uint64_t now = 1600;
    auto sample = [&](const FrameBuf& frame, uint64_t sequence) {
      return t.m.FeedExactBaselinePaint(id, frame.px.data(), 32 * 4, 32, 24,
                                        dst.data(), ++now, sequence);
    };
    Check(sample(mismatch, 11).disposition != PaintDisposition::kAccepted &&
          sample(original, 12).disposition != PaintDisposition::kAccepted,
          "batch veto: M,E pair cannot accept");
    t.m.DiscardExactBaselinePartialMatch();
    Check(sample(original, 13).disposition != PaintDisposition::kAccepted &&
          sample(mismatch, 14).disposition != PaintDisposition::kAccepted &&
          !t.m.accepted() && dst == untouched,
          "batch veto: next E,M pair must inspect latest mismatch, not accept cross-batch E,E");
    t.m.DiscardExactBaselinePartialMatch();
    Check(t.m.needs_baseline_veto() && !t.m.ArmExactBaseline(id, 0, ++now),
          "batch veto: reset preserves candidate and one-time readiness binding");
    Check(sample(original, 10).disposition != PaintDisposition::kAccepted &&
          sample(original, 14).disposition != PaintDisposition::kAccepted && dst == untouched,
          "batch veto: reset cannot lower readiness floor or replay last native paint");
    Check(sample(original, 15).disposition != PaintDisposition::kAccepted &&
          sample(original, 16).disposition == PaintDisposition::kAccepted && dst == original.px,
          "batch veto: fresh E,E pair still accepts exact signed reconstruction");
  }
}

void TestRequestBoundExact() {
  for (const auto drive : {DriveMode::kModeN, DriveMode::kModeP}) {
    Harness t;
    RequestDesc d;
    d.document_gen = 17; d.request_id = 0x89ABCDEFu;
    d.target_frame_index = 0x1234; d.drive = drive;
    d.canary = CanaryMode::kExact;
    d.expected_w = 64; d.expected_h = 48;
    FrameBuf original(64, 48);
    // All four corners contain nonuniform native premultiplied alpha bytes.
    for (uint32_t y = 0; y < original.h; ++y) {
      for (uint32_t x = 0; x < original.w; ++x) {
        const auto a = static_cast<uint8_t>((x * 17 + y * 29) & 255);
        original.SetPx(x, y, Bgra{static_cast<uint8_t>(x % (a + 1u)),
            static_cast<uint8_t>(y % (a + 1u)), static_cast<uint8_t>((x ^ y) % (a + 1u)), a});
      }
    }
    FrameBuf first = original, second = original;
    first.DrawExact(d.target_frame_index, d.request_id, false);
    second.DrawExact(d.target_frame_index, d.request_id, true);
    const std::vector<uint8_t> sentinel(original.px.size(), 0xAA);
    auto dst = sentinel;
    PageEvent id;
    id.document_gen = d.document_gen; id.request_id = d.request_id;
    id.target_frame_index = d.target_frame_index;
    auto sample = [&](const PageEvent& identity, const FrameBuf& b, uint64_t seq) {
      return t.m.FeedExactBaselinePaint(identity, b.px.data(), 64 * 4,
                                       64, 48, dst.data(), 1600 + seq, seq);
    };
    auto feed = [&](const FrameBuf& b) {
      return t.m.FeedPaint(b.px.data(), 64 * 4, 64, 48, dst.data(), 1500);
    };
    Check(!t.m.ArmExactBaseline(id, 100, 1), "request-bound exact: S0 readiness cannot arm");
    Check(t.AdvanceToS3(d, nullptr, 1000), "request-bound exact: no serial baseline or navigation required by core");
    Check(sample(id, original, 1).disposition != PaintDisposition::kAccepted,
          "exact: initial baseline cannot bypass two signed proof phases");
    Check(!t.m.PaintMayAdvance(first.px.data(), 64 * 4, 64, 48),
          "request-bound exact: positive signature before S4 still early");
    Check(t.m.EnterPumping(1300), "request-bound exact: enter S4");
    Check(sample(id, original, 101).disposition != PaintDisposition::kAccepted && dst == sentinel,
          "request-bound exact: unsigned equality before signed phases cannot publish");
    Check(t.m.PaintMayAdvance(first.px.data(), 64 * 4, 64, 48),
          "request-bound exact: first positive candidate requires renderer fence");
    const auto one = feed(first);
    Check(one.disposition != PaintDisposition::kAccepted && one.decoded_request_id == d.request_id &&
          one.decoded_frame == d.target_frame_index && t.m.needs_relocation(),
          "request-bound exact: native first probe decodes frame AND all32 request bits, not S5");
    Check(!t.m.PaintMayAdvance(first.px.data(), 64 * 4, 64, 48) &&
          feed(first).disposition == PaintDisposition::kRejectedStale,
          "request-bound exact: queued first-phase bytes cannot satisfy relocation");
    FrameBuf wrong = original;
    wrong.DrawExact(d.target_frame_index, d.request_id - 1u, true);
    Check(!t.m.PaintMayAdvance(wrong.px.data(), 64 * 4, 64, 48) &&
          feed(wrong).disposition == PaintDisposition::kRejectedStale,
          "request-bound exact: same frame stale request token rejected");
    wrong = original;
    wrong.DrawExact(d.target_frame_index - 1, d.request_id, true);
    const auto wrong_frame = feed(wrong);
    Check(wrong_frame.disposition == PaintDisposition::kRejectedStale &&
          wrong_frame.decoded_frame == d.target_frame_index - 1,
          "request-bound exact: right request cannot substitute for decoded target frame");
    wrong = second;
    wrong.px[wrong.Off(20, 20)] ^= 1;
    Check(t.m.PaintMayAdvance(wrong.px.data(), 64 * 4, 64, 48) &&
          feed(wrong).disposition == PaintDisposition::kRejectedStale && dst == sentinel,
          "request-bound exact: positive signature is NOT full-content verification");
    Check(feed(second).disposition != PaintDisposition::kAccepted &&
          t.m.needs_baseline_veto() && dst == sentinel,
          "request-bound exact: both signed phases freeze output but do not publish");
    Check(!t.m.PaintMayAdvance(second.px.data(), 64 * 4, 64, 48),
          "request-bound exact: frozen candidate never recopies/re-fences signed paints");
    Check(sample(id, original, 101).disposition != PaintDisposition::kAccepted &&
          sample(id, original, 102).disposition != PaintDisposition::kAccepted,
          "request-bound exact: samples without native readiness arm cannot accept");
    auto foreign = id; ++foreign.document_gen;
    Check(!t.m.ArmExactBaseline(foreign, 100, 1600), "request-bound exact: wrong-generation readiness rejected");
    Check(t.m.ArmExactBaseline(id, 100, 1601) &&
          !t.m.ArmExactBaseline(id, 0, 1602),
          "request-bound exact: readiness floor armed exactly once, cannot be rewound");
    Check(sample(id, original, 0).disposition != PaintDisposition::kAccepted &&
          sample(id, original, 100).disposition != PaintDisposition::kAccepted,
          "request-bound exact: unsequenced and pre-ready native paints rejected");
    Check(sample(id, original, 101).disposition != PaintDisposition::kAccepted,
          "request-bound exact: one distinct post-ready equality insufficient");
    Check(sample(id, original, 101).disposition != PaintDisposition::kAccepted &&
          sample(id, original, 102).disposition != PaintDisposition::kAccepted,
          "request-bound exact: duplicate native paint resets consecutive evidence");
    wrong = original; wrong.px[wrong.Off(63, 47) + 3] ^= 1;
    Check(sample(id, wrong, 103).disposition != PaintDisposition::kAccepted &&
          sample(id, original, 104).disposition != PaintDisposition::kAccepted,
          "request-bound exact: corner alpha mismatch resets full-byte veto");
    Check(sample(foreign, original, 105).disposition != PaintDisposition::kAccepted &&
          sample(id, original, 106).disposition != PaintDisposition::kAccepted && dst == sentinel,
          "request-bound exact: foreign identity resets equality, output remains untouched");
    const auto accepted = sample(id, original, 107);
    Check(accepted.disposition == PaintDisposition::kAccepted &&
          accepted.decoded_frame == d.target_frame_index &&
          accepted.decoded_request_id == d.request_id && dst == original.px &&
          accepted.restored && !accepted.restore_from_reported,
          "request-bound exact: two native equalities publish exact signed BGRA/alpha and decoded identity");
    const auto source = drive == DriveMode::kModeP ? "persistent_unsigned" : "concurrent_unsigned_navigation";
    Check(std::any_of(t.lines.begin(), t.lines.end(), [&](const auto& line) {
            return line.find(source) != std::string::npos &&
                   line.find("\"consecutive_equal_paints\":2") != std::string::npos;
          }), "request-bound exact: audit labels actual independent baseline source");
    dst = sentinel;
    Check(sample(id, original, 108).disposition != PaintDisposition::kAccepted && dst == sentinel,
          "request-bound exact: late baseline cannot republish accepted bytes");
    t.m.Reset(1800, "done");
    const uint32_t old_request = d.request_id;
    ++d.request_id;
    Check(t.AdvanceToS3(d, nullptr, 2000) && t.m.EnterPumping(2300),
          "request-bound exact: repeated same frame starts a new request-token domain");
    Check(!t.m.PaintMayAdvance(first.px.data(), 64 * 4, 64, 48) &&
          feed(first).disposition == PaintDisposition::kRejectedStale &&
          !t.m.needs_relocation() && !t.m.needs_baseline_veto(),
          "request-bound exact: previous same-frame signed paint cannot advance new request");
    t.m.FailRequest("fixture", 2400);
    d.request_id = old_request;
    Check(!t.m.BeginRequest(d, 2500), "request-bound exact: non-adjacent request replay rejected after failure");
    d.request_id = std::numeric_limits<uint32_t>::max();
    Check(t.m.BeginRequest(d, 2600), "request-bound exact: full32 request range allowed");
    t.m.Reset(2700, "fixture");
    d.request_id = 0;
    Check(!t.m.BeginRequest(d, 2800), "request-bound exact: request wrap to zero fails closed");
    d.request_id = 1;
    Check(!t.m.BeginRequest(d, 2900), "request-bound exact: request wrap cannot reuse old proof token");
  }
}

void TestPrefilterAndProbeCorruption() {
  for (const auto mode : {CanaryMode::kClean, CanaryMode::kDiagnostic, CanaryMode::kExact}) {
    Harness t;
    RequestDesc d;
    d.document_gen = 1; d.request_id = 0x12345678u;
    d.target_frame_index = 0x2345; d.expected_w = 32; d.expected_h = 24;
    d.canary = mode;
    FrameBuf good(32, 24);
    if (mode == CanaryMode::kClean) good.DrawCanaryClean(d.target_frame_index);
    else if (mode == CanaryMode::kDiagnostic) good.DrawCanaryDiag(d.target_frame_index);
    else good.DrawExact(d.target_frame_index, d.request_id, false);
    std::vector<uint8_t> dst(good.px.size(), 0xAA);
    Check(!t.m.PaintMayAdvance(good.px.data(), 32 * 4, 32, 24) &&
          t.m.FeedPaint(good.px.data(), 32 * 4, 32, 24, dst.data(), 0).disposition == PaintDisposition::kLateDropped,
          "prefilter: S0 is late (never early or positive)");
    Check(t.AdvanceToS3(d, nullptr, 1) && t.m.EnterPumping(301), "prefilter: signed test state");
    const auto before = t.m.request_stats();
    const auto events = t.lines.size();
    Check(t.m.PaintMayAdvance(good.px.data(), 32 * 4, 32, 24) &&
          t.m.request_stats().paints_fed == before.paints_fed && t.lines.size() == events &&
          t.m.state() == State::S4_COMPOSITOR_PUMPING,
          "prefilter: positive classification is pure, not acceptance/state advance");
    Check(!t.m.PaintMayAdvance(nullptr, 32 * 4, 32, 24) &&
          !t.m.PaintMayAdvance(good.px.data(), 7, 32, 24) &&
          !t.m.PaintMayAdvance(good.px.data(), 32 * 4 - 1, 32, 24) &&
          !t.m.PaintMayAdvance(good.px.data(), 32 * 4, 31, 24),
          "prefilter: null/short stride/wrong dimensions rejected without reading probes");
    bool all_rejected = true;
    for (size_t i = 0; i < good.px.size(); ++i) {
      const size_t pixel = i / 4, x = pixel % 32, y = pixel / 32;
      const bool probe = mode == CanaryMode::kClean ? i < 8 :
          ((x < 8 && y < 8) || (x >= 24 && y >= 16));
      if (!probe) continue;
      auto bad = good; bad.px[i] ^= 1;
      if (t.m.PaintMayAdvance(bad.px.data(), 32 * 4, 32, 24) ||
          t.m.FeedPaint(bad.px.data(), 32 * 4, 32, 24, dst.data(), 400).disposition == PaintDisposition::kAccepted ||
          t.m.needs_relocation() || t.m.state() != State::S4_COMPOSITOR_PUMPING) {
        all_rejected = false; break;
      }
    }
    Check(all_rejected && dst == std::vector<uint8_t>(good.px.size(), 0xAA),
          "prefilter: every single probe-byte corruption rejects without output/state advance");
    Check(t.m.FeedPaint(good.px.data(), 8, 32, 24, dst.data(), 500).disposition == PaintDisposition::kRejectedStale,
          "prefilter: FeedPaint enforces full row stride, not only eight bytes");
    t.m.FailRequest("fixture", 600);
  }
}

void TestOwnedExactPaint() {
  Harness t;
  RequestDesc d;
  d.document_gen = 1; d.request_id = 0xFEDCBA98u; d.target_frame_index = 65535;
  d.expected_w = 16; d.expected_h = 16;
  d.canary = CanaryMode::kExact;
  FrameBuf original(16, 16), first = original, second = original;
  first.DrawExact(d.target_frame_index, d.request_id, false);
  second.DrawExact(d.target_frame_index, d.request_id, true);
  auto owned = first.px;
  const auto* allocation = owned.data();
  std::vector<uint8_t> dst(original.px.size(), 0xAA);
  Check(t.AdvanceToS3(d, nullptr, 0), "owned: ready");
  Check(t.m.FeedOwnedPaint(owned, 16, 16, dst.data(), 300).disposition == PaintDisposition::kEarlyDropped &&
        owned.data() == allocation && owned == first.px,
        "owned: early candidate retains storage/content, never borrowed by verifier");
  Check(t.m.EnterPumping(400), "owned: S4");
  auto malformed = owned; malformed.pop_back();
  Check(t.m.FeedOwnedPaint(malformed, 16, 16, dst.data(), 401).disposition == PaintDisposition::kRejectedStale &&
        !t.m.needs_relocation(), "owned: wrong vector length rejects before signature reads");
  const auto one = t.m.FeedOwnedPaint(owned, 16, 16, dst.data(), 500);
  Check(one.disposition != PaintDisposition::kAccepted && t.m.needs_relocation() && owned.empty(),
        "owned: fenced first native storage transferred, not whole-frame copied");
  // The caller may immediately recycle its now-empty storage; the retained
  // immutable first frame must remain independent of the caller's vector.
  owned.assign(original.px.size(), 0xEE);
  auto second_owned = second.px;
  const auto* second_allocation = second_owned.data();
  Check(t.m.FeedOwnedPaint(second_owned, 16, 16, dst.data(), 600).disposition != PaintDisposition::kAccepted &&
        t.m.needs_baseline_veto() && second_owned.data() == second_allocation && second_owned == second.px,
        "owned: phase2 keeps caller storage and freezes reconstructed original buffer");
  PageEvent id; id.document_gen = d.document_gen; id.request_id = d.request_id;
  id.target_frame_index = d.target_frame_index;
  Check(t.m.ArmExactBaseline(id, 10, 700), "owned: baseline ready floor");
  Check(t.m.FeedExactBaselinePaint(id, original.px.data(), 16 * 4, 16, 16,
        dst.data(), 800, 11).disposition != PaintDisposition::kAccepted,
        "owned: transferred storage still requires first veto");
  Check(t.m.FeedExactBaselinePaint(id, original.px.data(), 16 * 4, 16, 16,
        dst.data(), 900, 12).disposition == PaintDisposition::kAccepted && dst == original.px,
        "owned: byte-exact output survives caller reuse and all-corner minimum geometry");
}

void TestExternalPumpSchedule() {
  using S = ExternalPumpSchedule;
  Check(S::DeadlineUs(1234, 0) == 1234 && S::DeadlineUs(1234, -1) == 1234 &&
        S::DeadlineUs(1234, 9) == 10234, "pump: immediate/negative/delayed deadline policy");
  Check(S::DeadlineUs(1234, std::numeric_limits<int64_t>::max()) == S::kNoDeadline - 1 &&
        S::DeadlineUs(S::kNoDeadline, 1) == S::kNoDeadline - 1,
        "pump: overflowing delays saturate without becoming immediate/no-work");
  S s;
  Check(s.deadline_us() == S::kNoDeadline, "pump: initially no scheduled work");
  s.Schedule(0, 100); s.Schedule(20, 200);
  Check(s.deadline_us() == 20200, "pump: positive delay replaces/cancels previous schedule (CEF contract)");
  s.ConsumeDue(300);
  Check(s.deadline_us() == 20200, "pump: early explicit pump preserves future timer");
  s.ConsumeDue(20200); s.Schedule(0, 20201);
  Check(s.deadline_us() == 20201, "pump: callback after consume remains pending (reentrant CEF work)");
  s.ConsumeDue(20201);
  Check(s.deadline_us() == S::kNoDeadline, "pump: due work consumed once");
  const auto now = S::NowUs();
  s.Schedule(10000, now);
  s.NotifyWork();
  // Sticky notification must survive arrival BEFORE WaitUntil starts.
  s.WaitUntil(now + 1000000);
  Check(s.deadline_us() == S::DeadlineUs(now, 10000),
        "pump: native completion wake does not erase/replace CEF future timer");
  S asynchronous;
  auto waiter = std::async(std::launch::async, [&] {
    asynchronous.WaitUntil(S::NowUs() + 3000000);
  });
  asynchronous.Schedule(0, S::NowUs());
  const bool woken = waiter.wait_for(std::chrono::seconds(1)) == std::future_status::ready;
  if (!woken) asynchronous.NotifyWork();
  waiter.get();
  Check(woken, "pump: other-thread immediate callback wakes without lost-before-wait race");
  S completion;
  auto encoder_waiter = std::async(std::launch::async, [&] {
    completion.WaitUntil(S::NowUs() + 3000000);
  });
  completion.NotifyWork();
  const bool encoder_woken = encoder_waiter.wait_for(std::chrono::seconds(1)) == std::future_status::ready;
  if (!encoder_woken) completion.Schedule(0, S::NowUs());
  encoder_waiter.get();
  Check(encoder_woken && completion.deadline_us() == S::kNoDeadline,
        "pump: native encoder completion independently wakes predicate waiter");
}

}  // namespace

int main(int argc, char* argv[]) {
  std::string jsonl_out;
  bool dump_signatures = false;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg.rfind("--jsonl-out=", 0) == 0 && arg.size() > 12) {
      jsonl_out = arg.substr(12);
    } else if (arg == "--dump-signatures") {
      dump_signatures = true;
    } else {
      std::fprintf(stderr, "未知参数: %s\n", arg.c_str());
      return 2;
    }
  }
  if (dump_signatures) {
    // 全域签名转储（65,536 行）：每行 "f P1.b P1.g P1.r P1.a P2.b P2.g
    // P2.r P2.a"（clean 预乘口径）。消费方：tests/gfx/test_cef_frame_sync.py
    // 与 vtx/render/frame_canary.py 逐行比对（C++ 镜像 == 协议 home）。
    std::vector<std::string> lines;
    lines.reserve(kFrameMax + 1);
    char buf[64];
    for (int f = 0; f <= static_cast<int>(kFrameMax); ++f) {
      const Bgra p1 = SignatureBgraPremulP1(f);
      const Bgra p2 = SignatureBgraPremulP2(f);
      std::snprintf(buf, sizeof(buf), "%d %u %u %u %u %u %u %u %u\n", f,
                    p1.b, p1.g, p1.r, p1.a, p2.b, p2.g, p2.r, p2.a);
      lines.push_back(buf);
    }
    for (const auto& l : lines) {
      std::fputs(l.c_str(), stdout);
    }
    return 0;
  }

  TestRequestBoundExact();
  TestPrefilterAndProbeCorruption();
  TestExternalPumpSchedule();
  TestOwnedExactPaint();
  TestNativeExact();
  TestExactBaselineBatchBoundary();
  TestHappyPathClean();
  TestEarlyPaintDropped();
  TestStalePaintReject();
  TestHalfSignature();
  TestCrossGenSameFrame();
  TestDuplicateRequest();
  TestPumpTimeout();
  TestSizeMismatch();
  TestNeighborFallbackExplicit();
  TestDiagnosticMode();
  TestFormulaVectors();
  TestLatePaintAfterAccept();
  TestIllegalTransitions();

  // --jsonl-out：跑一段混合场景把 JSONL 落盘，供 Python 跨语言全量校验。
  if (!jsonl_out.empty()) {
    Harness t;
    RequestDesc d;
    d.document_gen = 42;
    d.request_id = 1;
    d.target_frame_index = 1234;
    d.expected_w = 32;
    d.expected_h = 16;
    const uint8_t orig[2][4] = {{9, 8, 7, 255}, {6, 5, 4, 255}};
    (void)t.AdvanceToS3(d, orig, 100);
    (void)t.m.EnterPumping(300);
    FrameBuf stale(32, 16);
    stale.DrawCanaryClean(1233);
    std::vector<uint8_t> dst(32 * 16 * 4, 0);
    (void)t.m.FeedPaint(stale.px.data(), 32 * 4, 32, 16, dst.data(), 400);
    FrameBuf good(32, 16);
    good.DrawCanaryClean(1234);
    (void)t.m.FeedPaint(good.px.data(), 32 * 4, 32, 16, dst.data(), 500);
    t.m.Reset(600, "done");
    std::ofstream fh(jsonl_out, std::ios::binary);
    for (const auto& l : t.lines) {
      fh << l;
    }
  }

  std::printf("%s: %d passed, %d failed\n",
              g_fail == 0 ? "ALL PASS" : "SELFTEST FAIL", g_pass, g_fail);
  return g_fail == 0 ? 0 : 1;
}
