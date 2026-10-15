// shm_ring_selftest.cpp — 4-Slot SHM 环与协议原生自检（issue #110）。
//
// 无 CEF 依赖（tests/test_cef_worker_shm.py 门控调用；构建产物与
// vtx_cef_worker 同目录）。用例面（工单 DoD 的原生层）：
//   1. layout          偏移表/几何断言（与 ipc_protocol.hpp 静态断言互证）
//   2. state_machine   EMPTY→WRITING→READY→EMPTY 状态机走查（含 Abandon）
//   3. backpressure    4 槽全满：第 5 槽申请超时让路 + 计时入账；释放后恢复
//   4. stride_compact  源步长 padding ≠ width*4 → 槽内紧凑无斜纹
//   5. stale_run_id    陈旧 run_id 槽头被消费端校验拒收
//   6. unpremultiply   SIMD 路径 vs 标量参考：全部 (c,a) 组合穷举一致 +
//                      BGRA→RGBA 重排/不透明快路径抽检
//   7. canary          金丝雀编码/解码往返（含非法像素拒收）
//   8. dump_ring       --dump-ring=<path>：已知图案环字节落盘（Python
//                      cef_client.py 解析器跨语言锁定）
// 退出码：全部 PASS=0；任一 FAIL=1。
#include <sys/wait.h>
#include <unistd.h>

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "frame_pipeline.h"
#include "ipc_protocol.hpp"
#include "shm_ring.hpp"

namespace {

int g_fail = 0;

void Check(bool ok, const char* name, const char* detail) {
  if (ok) {
    std::printf("PASS %-18s %s\n", name, detail ? detail : "");
  } else {
    ++g_fail;
    std::printf("FAIL %-18s %s\n", name, detail ? detail : "");
  }
}

// 对角图案：像素 (x,y) = B=(x^y)&255 G=x&255 R=y&255 A=255 —— 行/列均
// 变化，步长错位即显形；不透明（预乘==直通）。
void FillDiagonalPattern(uint8_t* dst, uint32_t w, uint32_t h,
                         size_t dst_stride) {
  for (uint32_t y = 0; y < h; ++y) {
    uint8_t* row = dst + dst_stride * y;
    for (uint32_t x = 0; x < w; ++x) {
      row[x * 4 + 0] = static_cast<uint8_t>((x ^ y) & 255);
      row[x * 4 + 1] = static_cast<uint8_t>(x & 255);
      row[x * 4 + 2] = static_cast<uint8_t>(y & 255);
      row[x * 4 + 3] = 255;
    }
  }
}

bool RowEquals(const uint8_t* a, const uint8_t* b, uint32_t w) {
  return std::memcmp(a, b, static_cast<size_t>(w) * 4u) == 0;
}

void TestLayout() {
  const auto geo = vtx_cef::RingGeometry::ForSize(64, 48);
  Check(geo.control_bytes == sizeof(vtx_cef::RingControl) &&
            geo.control_bytes == 384,
        "layout", "RingControl=384B");
  Check(geo.frame_bytes == 64 * 48 * 4 && geo.slot_stride % 64 == 0 &&
            geo.total_bytes ==
                geo.control_bytes + geo.slot_stride * vtx_cef::kRingSlots,
        "layout", "slot_stride 64 对齐 + 总尺寸");
  // FrameSlotHeader 偏移表抽查（编译期静态断言的运行期互证）。
  vtx_cef::FrameSlotHeader h;
  std::memset(&h, 0, sizeof(h));
  h.magic = vtx_cef::kSlotMagic;
  h.t_disk_commit_us = 0x1122334455667788ull;
  Check(*reinterpret_cast<const uint64_t*>(
            reinterpret_cast<const uint8_t*>(&h) + 80) ==
            0x1122334455667788ull,
        "layout", "t_disk_commit_us 偏移 80（小端线上口径）");
}

void TestStateMachine() {
  std::string err;
  auto ring = vtx_cef::ShmRingWriter::Create(64, 48, 0xABCD, &err);
  Check(ring != nullptr, "state_machine", err.c_str());
  if (ring == nullptr) {
    return;
  }
  // 创建即解绑：/dev/shm 不应有本环名（名字已不可知——以 fd 映射直证存在）。
  uint64_t bp = 0;
  const int slot = ring->ClaimSlot(1000, &bp);
  Check(slot >= 0 && bp == 0, "state_machine", "EMPTY→WRITING 首槽零等待");
  Check(ring->control()->gates[static_cast<uint32_t>(slot)].state.load() ==
            static_cast<uint32_t>(vtx_cef::SlotState::kWriting),
        "state_machine", "WRITING 门可见");
  ring->AbandonSlot(static_cast<uint32_t>(slot));
  Check(ring->control()->gates[static_cast<uint32_t>(slot)].state.load() ==
            static_cast<uint32_t>(vtx_cef::SlotState::kEmpty),
        "state_machine", "WRITING→EMPTY（Abandon 错误路径）");
  const int slot2 = ring->ClaimSlot(1000, &bp);
  Check(slot2 == slot, "state_machine", "弃置槽可复用");
  auto* hdr = ring->SlotHeader(static_cast<uint32_t>(slot2));
  std::memset(hdr, 0, sizeof(*hdr));
  hdr->magic = vtx_cef::kSlotMagic;
  hdr->protocol_version = vtx_cef::kProtocolVersion;
  hdr->run_id = 0xABCD;  // 与环 run_id 一致（读者校验面）
  FillDiagonalPattern(ring->SlotData(static_cast<uint32_t>(slot2)), 64, 48,
                      vtx_cef::CompactStride(64));
  const uint32_t seq_before =
      ring->control()->gates[static_cast<uint32_t>(slot2)].seq.load();
  ring->PublishSlot(static_cast<uint32_t>(slot2));
  Check(ring->control()->gates[static_cast<uint32_t>(slot2)].state.load(
            std::memory_order_acquire) ==
            static_cast<uint32_t>(vtx_cef::SlotState::kReady) &&
        ring->control()->gates[static_cast<uint32_t>(slot2)].seq.load() ==
            seq_before + 1 &&
        ring->control()->published_total.load() == 1,
        "state_machine", "WRITING→READY（release）+ seq/published 计数");

  // 消费端（跨进程零拷贝读语义）：fork 子进程 mmap 同一 fd？——同进程以
  // View::Attach(fd) 模拟（dup fd 独立映射同一实体）。
  auto view = vtx_cef::ShmRingView::Attach(ring->fd(), &err);
  Check(view != nullptr, "state_machine", err.c_str());
  if (view == nullptr) {
    return;
  }
  const int ready = view->WaitReady(1000);
  Check(ready == slot2 && view->ValidateHeader(static_cast<uint32_t>(ready)),
        "state_machine", "读者见 READY 且槽头校验通过");
  Check(RowEquals(view->SlotData(static_cast<uint32_t>(ready)),
                  ring->SlotData(static_cast<uint32_t>(ready)), 64),
        "state_machine", "读者映射数据逐字节可见（零拷贝同一实体）");
  view->Release(static_cast<uint32_t>(ready));
  Check(ring->control()->gates[static_cast<uint32_t>(ready)].state.load() ==
            static_cast<uint32_t>(vtx_cef::SlotState::kEmpty) &&
        ring->control()->released_total.load() == 1,
        "state_machine", "READY→EMPTY（读者释放）+ released 计数");
}

void TestBackpressure() {
  std::string err;
  auto ring = vtx_cef::ShmRingWriter::Create(32, 32, 7, &err);
  Check(ring != nullptr, "backpressure", err.c_str());
  if (ring == nullptr) {
    return;
  }
  uint64_t bp = 0;
  int slots[vtx_cef::kRingSlots];
  for (uint32_t i = 0; i < vtx_cef::kRingSlots; ++i) {
    slots[i] = ring->ClaimSlot(1000, &bp);
  }
  bool all_claimed = true;
  for (auto s : slots) {
    all_claimed = all_claimed && s >= 0;
  }
  Check(all_claimed, "backpressure", "4 槽全占（满环）");
  const uint64_t t0 = vtx_cef::NowUs();
  const int extra = ring->ClaimSlot(120'000, &bp);  // 120ms 超时
  const uint64_t waited = vtx_cef::NowUs() - t0;
  Check(extra == -1 && bp >= 100'000 && waited >= 100'000,
        "backpressure", "第 5 槽申请超时让路（不死等）+ 等待计时入账");
  Check(ring->control()->backpressure_wait_us_total.load() >= bp,
        "backpressure", "环级背压累计入账");
  // 释放一槽后恢复供给。
  ring->AbandonSlot(static_cast<uint32_t>(slots[1]));  // WRITING 弃置=释放
  const int again = ring->ClaimSlot(50'000, &bp);
  Check(again == slots[1], "backpressure", "释放一槽后供给恢复（复用同槽）");
}

void TestStrideCompact() {
  // 源步长 = width*4 + 64（模拟对齐/dirtyRects 外包框步长）→ 紧凑无斜纹。
  const uint32_t w = 40, h = 24;
  const size_t src_stride = w * 4u + 64;
  std::vector<uint8_t> src(src_stride * h, 0xA5);
  FillDiagonalPattern(src.data(), w, h, src_stride);
  std::vector<uint8_t> compact(static_cast<size_t>(w) * h * 4u);
  vtx_cef::CopyCompactRows(src.data(), src_stride, compact.data(), w, h);
  bool exact = true;
  for (uint32_t y = 0; y < h && exact; ++y) {
    exact = RowEquals(src.data() + src_stride * y,
                      compact.data() + static_cast<size_t>(w) * 4u * y, w);
  }
  Check(exact, "stride_compact", "padding 步长源逐行紧凑（零斜纹）");
  // 紧凑源整块路径。
  std::vector<uint8_t> compact2(static_cast<size_t>(w) * h * 4u);
  vtx_cef::CopyCompactRows(compact.data(), w * 4u, compact2.data(), w, h);
  Check(compact == compact2, "stride_compact", "紧凑源整块拷贝等价");
}

void TestStaleRunId() {
  std::string err;
  auto ring = vtx_cef::ShmRingWriter::Create(16, 16, 0x1111, &err);
  auto view = vtx_cef::ShmRingView::Attach(ring->fd(), &err);
  Check(ring != nullptr && view != nullptr, "stale_run_id", "建环+附着");
  if (ring == nullptr || view == nullptr) {
    return;
  }
  uint64_t bp = 0;
  const int s = ring->ClaimSlot(1000, &bp);
  auto* hdr = ring->SlotHeader(static_cast<uint32_t>(s));
  std::memset(hdr, 0, sizeof(*hdr));
  hdr->magic = vtx_cef::kSlotMagic;
  hdr->protocol_version = vtx_cef::kProtocolVersion;
  hdr->run_id = 0x9999;  // 陈旧 run（上一生命周期残帧）
  ring->PublishSlot(static_cast<uint32_t>(s));
  Check(view->WaitReady(1000) == s &&
            !view->ValidateHeader(static_cast<uint32_t>(s)),
        "stale_run_id", "陈旧 run_id 槽头被拒收");
}

void TestUnpremultiply() {
  // 穷举全部 (c,a)：
  bool all_ok = true;
  for (int a = 0; a <= 255 && all_ok; ++a) {
    for (int c = 0; c <= 255; ++c) {
      const uint8_t want = vtx_cef::UnpremultiplyScalar(
          static_cast<uint8_t>(c), static_cast<uint8_t>(a));
      // 单像素三通道同值送 SIMD 面（BGRA→RGBA 重排后逐通道核对）。
      const uint8_t px[4] = {static_cast<uint8_t>(c), static_cast<uint8_t>(c),
                             static_cast<uint8_t>(c), static_cast<uint8_t>(a)};
      uint8_t out[4];
      vtx_cef::UnpremultiplyToRgba(px, 4, out);
      if (out[0] != want || out[1] != want || out[2] != want || out[3] != a) {
        all_ok = false;
        std::printf("  c=%d a=%d want=%d got=(%d,%d,%d,%d)\n", c, a, want,
                    out[0], out[1], out[2], out[3]);
        break;
      }
    }
  }
  Check(all_ok, "unpremultiply", "SIMD==标量：全部 (c,a)∈[0,255]² 穷举一致");
  // 通道重排抽检：BGRA(1,2,3,255) → RGBA(3,2,1,255)。
  const uint8_t px[4] = {1, 2, 3, 255};
  uint8_t out[4];
  vtx_cef::UnpremultiplyToRgba(px, 4, out);
  Check(out[0] == 3 && out[1] == 2 && out[2] == 1 && out[3] == 255,
        "unpremultiply", "BGRA→RGBA 重排（不透明快路径）");
  // 部分透明混合块（触发通用 gather 路径）：与标量逐字节对照。
  std::vector<uint8_t> bgra, want;
  for (int a = 0; a <= 255; a += 5) {
    for (int c = 0; c <= 255; c += 3) {
      bgra.push_back(static_cast<uint8_t>(c));       // B
      bgra.push_back(static_cast<uint8_t>(c / 2));   // G
      bgra.push_back(static_cast<uint8_t>(255 - c)); // R
      bgra.push_back(static_cast<uint8_t>(a));       // A
    }
  }
  // 块内含 alpha=255 → 快路径不触发？补一块全透明+部分透明混合（通用路径）。
  bgra.insert(bgra.end(), {10, 20, 30, 128, 40, 50, 60, 0});
  std::vector<uint8_t> rgba(bgra.size());
  vtx_cef::UnpremultiplyToRgba(bgra.data(), bgra.size(), rgba.data());
  bool mix_ok = true;
  for (size_t i = 0; i + 3 < bgra.size(); i += 4) {
    const uint8_t a = bgra[i + 3];
    if (rgba[i] != vtx_cef::UnpremultiplyScalar(bgra[i + 2], a) ||
        rgba[i + 1] != vtx_cef::UnpremultiplyScalar(bgra[i + 1], a) ||
        rgba[i + 2] != vtx_cef::UnpremultiplyScalar(bgra[i], a) ||
        rgba[i + 3] != a) {
      mix_ok = false;
      break;
    }
  }
  Check(mix_ok, "unpremultiply", "混合 alpha 块通用路径逐字节一致");
}

void TestCanary() {
  const uint32_t w = 8, h = 8;
  std::vector<uint8_t> frame(static_cast<size_t>(w) * h * 4u, 0);
  auto put = [&](uint32_t f) {
    frame[0] = static_cast<uint8_t>((f >> 8) & 255);  // B
    frame[1] = static_cast<uint8_t>(f & 255);         // G
    frame[2] = 0x43;                                  // R ('C')
    frame[3] = 255;                                   // A
  };
  int32_t got = -1;
  put(0x2103);
  Check(vtx_cef::DecodeCanaryFrameIndex(frame.data(), w, h, &got) &&
            got == 0x2103,
        "canary", "f=0x2103 编码→解码往返");
  put(0xFFFF);
  Check(vtx_cef::DecodeCanaryFrameIndex(frame.data(), w, h, &got) &&
            got == 0xFFFF,
        "canary", "f=0xFFFF 边界");
  frame[2] = 0x42;  // 魔位破坏
  Check(!vtx_cef::DecodeCanaryFrameIndex(frame.data(), w, h, &got),
        "canary", "魔位不符拒收");
  frame[2] = 0x43;
  frame[3] = 254;  // 半透明（预乘≠直通，解码面不适用）
  Check(!vtx_cef::DecodeCanaryFrameIndex(frame.data(), w, h, &got),
        "canary", "非不透明像素拒收");
}

// --dump-ring：写 2 帧已知图案（Python 解析器跨语言锁定用）。
//   槽 0：f=0x2103 金丝雀 + 对角图案（64x48）
//   槽 1：f=0x0007 金丝雀 + 对角图案
//   槽 2/3：留空（EMPTY）
int DumpRing(const std::string& path) {
  std::string err;
  auto ring = vtx_cef::ShmRingWriter::Create(64, 48, 0xCAFEF00D, &err);
  if (ring == nullptr) {
    std::fprintf(stderr, "dump_ring: %s\n", err.c_str());
    return 1;
  }
  const uint32_t fs[2] = {0x2103, 0x0007};
  for (int i = 0; i < 2; ++i) {
    uint64_t bp = 0;
    const int s = ring->ClaimSlot(1000, &bp);
    if (s != i) {
      std::fprintf(stderr, "dump_ring: 槽序异常 %d\n", s);
      return 1;
    }
    auto* hdr = ring->SlotHeader(static_cast<uint32_t>(s));
    std::memset(hdr, 0, sizeof(*hdr));
    hdr->magic = vtx_cef::kSlotMagic;
    hdr->protocol_version = vtx_cef::kProtocolVersion;
    hdr->status_code = vtx_cef::kStatusOk;
    hdr->run_id = 0xCAFEF00Dull;
    hdr->document_gen = static_cast<uint32_t>(i + 1);
    hdr->request_id = 100 + static_cast<uint32_t>(i);
    hdr->target_frame_index = static_cast<int32_t>(fs[i]);
    hdr->canary_frame_index = static_cast<int32_t>(fs[i]);
    hdr->width = 64;
    hdr->height = 48;
    hdr->stride = 64 * 4u;
    hdr->pixel_format = vtx_cef::kPixBgraPremul;
    hdr->color_space = vtx_cef::kColorSpaceSrgb;
    hdr->rejected_paints = static_cast<uint16_t>(i);
    hdr->t_ready_wait_us = 1000ull * static_cast<uint64_t>(i + 1);
    hdr->t_capture_raw_us = 2000ull * static_cast<uint64_t>(i + 1);
    uint8_t* data = ring->SlotData(static_cast<uint32_t>(s));
    FillDiagonalPattern(data, 64, 48, vtx_cef::CompactStride(64));
    // 金丝雀像素（覆盖 (0,0)）：B=(f>>8) G=f R=0x43 A=255
    data[0] = static_cast<uint8_t>((fs[i] >> 8) & 255);
    data[1] = static_cast<uint8_t>(fs[i] & 255);
    data[2] = 0x43;
    data[3] = 255;
    ring->PublishSlot(static_cast<uint32_t>(s));
  }
  if (!ring->DumpToFile(path)) {
    std::fprintf(stderr, "dump_ring: 写出失败 %s\n", path.c_str());
    return 1;
  }
  std::printf("dump_ring: %s（%llu 字节；槽0 f=0x2103 槽1 f=0x0007 run_id=0xCAFEF00D）\n",
              path.c_str(),
              static_cast<unsigned long long>(ring->total_bytes()));
  return 0;
}

}  // namespace

int main(int argc, char* argv[]) {
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg.compare(0, 12, "--dump-ring=") == 0) {
      return DumpRing(arg.substr(12));
    }
    std::fprintf(stderr, "shm_ring_selftest: 未知参数 %s\n", arg.c_str());
    return 2;
  }
  std::printf("== shm_ring_selftest（#110 4-Slot 环 + 定长协议原生自检）==\n");
  TestLayout();
  TestStateMachine();
  TestBackpressure();
  TestStrideCompact();
  TestStaleRunId();
  TestUnpremultiply();
  TestCanary();
  std::printf("== %s（fail=%d）==\n", g_fail == 0 ? "ALL PASS" : "FAIL", g_fail);
  return g_fail == 0 ? 0 : 1;
}
