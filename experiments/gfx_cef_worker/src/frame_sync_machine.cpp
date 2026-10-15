// frame_sync_machine.cpp — 五阶段单调世代状态机实现（issue #114 CEF-03a）。
//
// 纯 C++（无 CEF 依赖）：原生自检 frame_sync_selftest 与 serve 真实路径
// 共用同一判定核心，防「自检一套、线上另一套」的漂移。
#include "frame_sync_machine.hpp"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>

#include "frame_pipeline.h"  // CopyCompactRows（紧凑拷贝，CEF 无关）

namespace vtx_cef {
namespace fsync {

// ---------------------------------------------------------------- 工具

namespace {

// 单调时钟（本文件自持，不引 shm_ring 依赖面）。
uint64_t NowUs() {
  return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::microseconds>(
                                   std::chrono::steady_clock::now()
                                       .time_since_epoch())
                                   .count());
}


std::string JsonEscape(const char* s) {
  std::string out;
  for (const char* p = s; *p != '\0'; ++p) {
    const auto c = static_cast<unsigned char>(*p);
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (c < 0x20) {
          char buf[8];
          std::snprintf(buf, sizeof(buf), "\\u%04x", c);
          out += buf;
        } else {
          out += static_cast<char>(c);
        }
    }
  }
  return out;
}

const char* PhaseName(PageEvent::Phase p) {
  return p == PageEvent::Phase::kAssetsReady ? "assets_ready" : "canary_set";
}

}  // namespace

const char* StateName(State s) {
  switch (s) {
    case State::S0_IDLE: return "S0_IDLE";
    case State::S1_NAV_OR_EVAL_ISSUED: return "S1_NAV_OR_EVAL_ISSUED";
    case State::S2_ASSETS_FONTS_READY: return "S2_ASSETS_FONTS_READY";
    case State::S3_JS_FRAME_APPLIED_AND_CANARY_SET:
      return "S3_JS_FRAME_APPLIED_AND_CANARY_SET";
    case State::S4_COMPOSITOR_PUMPING: return "S4_COMPOSITOR_PUMPING";
    case State::S5_ONPAINT_VERIFIED: return "S5_ONPAINT_VERIFIED";
  }
  return "?";
}

const char* DriveModeName(DriveMode m) {
  return m == DriveMode::kModeN ? "n" : "p";
}

// ---------------------------------------------------------------- 签名编解码

void SignatureRgbP1(bool diag, int f, uint8_t* rgb) {
  const int hi = (f >> 8) & 0xFF;
  const int lo = f & 0xFF;
  const int key = diag ? kDiagXorKey : kCleanXorKey;
  rgb[0] = static_cast<uint8_t>(hi);
  rgb[1] = static_cast<uint8_t>(lo);
  rgb[2] = static_cast<uint8_t>((hi ^ lo ^ key) & 0xFF);
}

void SignatureRgbP2(bool diag, int f, uint8_t* rgb) {
  const int hi = (f >> 8) & 0xFF;
  const int lo = f & 0xFF;
  const int key = diag ? kDiag2AddKey : kClean2AddKey;
  rgb[0] = static_cast<uint8_t>(lo);   // 通道互换
  rgb[1] = static_cast<uint8_t>(hi);
  rgb[2] = static_cast<uint8_t>((kP2MulR * lo + kP2MulG * hi + key) & 0xFF);
}

std::vector<int> PremulCands(uint8_t v) {
  // 精确反像：{C ∈ [0,255] : floor((508C+255)/510) == v}。暴力扫描 256 值，
  // 与闭区间解析解逐值一致（selftest 交叉验证）——朴素 floor/ceil 直除法
  // 会引入假候选（约半数帧号校验和碰撞歧义，#105b 拦截）。
  std::vector<int> out;
  for (int c = 0; c <= 255; ++c) {
    if ((2 * c * 254 + 255) / 510 == static_cast<int>(v)) {
      out.push_back(c);
    }
  }
  return out;
}

Bgra SignatureBgraPremulP1(int f) {
  uint8_t rgb[3];
  SignatureRgbP1(false, f, rgb);
  return Bgra{PremulRound(rgb[2], kCleanAlpha), PremulRound(rgb[1], kCleanAlpha),
              PremulRound(rgb[0], kCleanAlpha), kCleanAlpha};
}

Bgra SignatureBgraPremulP2(int f) {
  uint8_t rgb[3];
  SignatureRgbP2(false, f, rgb);
  return Bgra{PremulRound(rgb[2], kCleanAlpha), PremulRound(rgb[1], kCleanAlpha),
              PremulRound(rgb[0], kCleanAlpha), kCleanAlpha};
}

void DecodeCandidatesP1(const uint8_t px_bgra[4], std::vector<int>* out) {
  out->clear();
  for (int b : PremulCands(px_bgra[0])) {
    for (int g : PremulCands(px_bgra[1])) {
      for (int r : PremulCands(px_bgra[2])) {
        if (((r ^ g ^ kCleanXorKey) & 0xFF) == b) {
          out->push_back(((r & 0xFF) << 8) | (g & 0xFF));
        }
      }
    }
  }
  std::sort(out->begin(), out->end());  // set_intersection 需有序输入
}

void DecodeCandidatesP2(const uint8_t px_bgra[4], std::vector<int>* out) {
  out->clear();
  for (int b : PremulCands(px_bgra[0])) {
    for (int g : PremulCands(px_bgra[1])) {
      for (int r : PremulCands(px_bgra[2])) {
        if (((kP2MulR * r + kP2MulG * g + kClean2AddKey) & 0xFF) == b) {
          out->push_back(((g & 0xFF) << 8) | (r & 0xFF));
        }
      }
    }
  }
  std::sort(out->begin(), out->end());  // set_intersection 需有序输入
}

bool DecodeDiagBlock(const uint8_t* compact_bgra, size_t stride, uint32_t width,
                     uint32_t height, bool bottom_right, int32_t* frame_out) {
  if (width < static_cast<uint32_t>(kDiagSize) ||
      height < static_cast<uint32_t>(kDiagSize) ||
      compact_bgra == nullptr || stride < 8) {
    return false;
  }
  const uint32_t ox =
      bottom_right ? width - static_cast<uint32_t>(kDiagSize) : 0;
  const uint32_t oy =
      bottom_right ? height - static_cast<uint32_t>(kDiagSize) : 0;
  // 诊断块 α=255 → 预乘恒等，直接按直向公式解码。整块须同色（foreign/
  // 撕裂带/校验和撕裂一律 false —— 宁拒不猜）。
  const uint8_t b0 = compact_bgra[oy * stride + ox * 4 + 0];
  const uint8_t g0 = compact_bgra[oy * stride + ox * 4 + 1];
  const uint8_t r0 = compact_bgra[oy * stride + ox * 4 + 2];
  const uint8_t a0 = compact_bgra[oy * stride + ox * 4 + 3];
  if (a0 != kDiagAlpha) {
    return false;
  }
  for (int y = 0; y < kDiagSize; ++y) {
    const uint8_t* row =
        compact_bgra + (oy + static_cast<uint32_t>(y)) * stride + ox * 4;
    for (int x = 0; x < kDiagSize; ++x) {
      const uint8_t* px = row + x * 4;
      if (px[0] != b0 || px[1] != g0 || px[2] != r0 || px[3] != a0) {
        return false;  // 块内杂色：撕裂带（半更新）或非 Canary 结构
      }
    }
  }
  int frame;
  if (bottom_right) {
    frame = (g0 << 8) | r0;  // P2：G=hi、R=lo（通道互换）
    if (((kP2MulR * r0 + kP2MulG * g0 + kDiag2AddKey) & 0xFF) != b0) {
      return false;  // 校验和撕裂
    }
  } else {
    frame = (r0 << 8) | g0;  // P1：R=hi、G=lo
    if (((r0 ^ g0 ^ kDiagXorKey) & 0xFF) != b0) {
      return false;  // 校验和撕裂
    }
  }
  *frame_out = frame;
  return true;
}
// ---------------------------------------------------------------- 事件

void FrameSyncStateMachine::Emit(const std::string& event, uint64_t now_us,
                                 const char* from, const char* to,
                                 const std::string& extra) {
  if (!sink_) {
    return;
  }
  char head[512];
  std::snprintf(head, sizeof(head),
                "{\"seq\":%llu,\"ts_us\":%llu,\"event\":\"%s\","
                "\"drive_mode\":\"%s\",\"document_gen\":%u,"
                "\"request_id\":%u,\"target_frame_index\":%d,"
                "\"state\":\"%s\",\"from\":\"%s\",\"to\":\"%s\"",
                static_cast<unsigned long long>(++seq_),
                static_cast<unsigned long long>(now_us),
                JsonEscape(event.c_str()).c_str(),
                DriveModeName(desc_.drive), desc_.document_gen,
                desc_.request_id, desc_.target_frame_index, StateName(state_),
                from != nullptr ? from : "", to != nullptr ? to : "");
  std::string line(head);
  if (!extra.empty()) {
    line += ",";
    line += extra;
  }
  line += "}\n";
  sink_(line);
}

// ---------------------------------------------------------------- 生命周期

bool FrameSyncStateMachine::BeginRequest(const RequestDesc& desc,
                                         uint64_t now_us) {
  if ((has_last_request_ && desc.request_id == last_request_id_) ||
      (state_ != State::S0_IDLE && desc.request_id == desc_.request_id)) {
    // 重复请求（同 request_id 重发）：拒绝并保持现状态 —— 状态机不因
    // 重放而重置进度（对抗面：重复请求注入）。
    ++counters_.duplicate_request_rejected;
    Emit("duplicate_request_rejected", now_us, nullptr, nullptr,
         "\"reason\":\"request_id replayed\"");
    return false;
  }
  if (state_ != State::S0_IDLE) {
    ++counters_.illegal_transition_rejected;
    Emit("illegal_transition", now_us, StateName(state_), nullptr,
         "\"reason\":\"BeginRequest requires S0_IDLE\"");
    return false;
  }
  if (desc.target_frame_index < 0 ||
      static_cast<uint32_t>(desc.target_frame_index) > kFrameMax ||
      desc.expected_w < 2 || desc.expected_h < 2 ||
      (desc.canary == CanaryMode::kDiagnostic &&
       (desc.expected_w < static_cast<uint32_t>(kDiagSize) ||
        desc.expected_h < static_cast<uint32_t>(kDiagSize)))) {
    Emit("invalid_request", now_us, nullptr, nullptr,
         "\"reason\":\"frame/w/h out of domain\"");
    return false;
  }
  desc_ = desc;
  state_ = State::S1_NAV_OR_EVAL_ISSUED;
  t_begin_us_ = now_us;
  t_assets_us_ = 0;
  t_canary_set_us_ = 0;
  t_pump_start_us_ = 0;
  has_reported_originals_ = false;
  std::memset(reported_original_bgra_, 0, sizeof(reported_original_bgra_));
  req_early_ = 0;
  req_rejected_ = 0;
  req_paints_ = 0;
  ++counters_.requests_begun;
  Emit("request_begin", now_us, StateName(State::S0_IDLE),
       StateName(State::S1_NAV_OR_EVAL_ISSUED),
       "\"width\":" + std::to_string(desc.expected_w) +
           ",\"height\":" + std::to_string(desc.expected_h));
  return true;
}

bool FrameSyncStateMachine::NotifyPageEvent(const PageEvent& ev,
                                            uint64_t now_us) {
  // 三元组核对：不匹配的回调 = 旧世代晚到回调（跨导航/重启世代隔离证据面）。
  if (state_ == State::S0_IDLE || state_ >= State::S4_COMPOSITOR_PUMPING ||
      ev.document_gen != desc_.document_gen ||
      ev.request_id != desc_.request_id ||
      ev.target_frame_index != desc_.target_frame_index) {
    ++counters_.late_ready_cb_rejected;
    Emit("ready_cb_rejected", now_us, nullptr, nullptr,
         std::string("\"reason\":\"foreign triple (gen/req/f) callback\","
                     "\"cb_phase\":\"") +
             PhaseName(ev.phase) + "\",\"cb_gen\":" +
             std::to_string(ev.document_gen) + ",\"cb_req\":" +
             std::to_string(ev.request_id) + ",\"cb_f\":" +
             std::to_string(ev.target_frame_index));
    return false;
  }
  if (ev.phase == PageEvent::Phase::kAssetsReady) {
    if (state_ != State::S1_NAV_OR_EVAL_ISSUED) {
      ++counters_.illegal_transition_rejected;
      Emit("illegal_transition", now_us, StateName(state_), nullptr,
           "\"reason\":\"assets_ready requires S1\"");
      return false;
    }
    state_ = State::S2_ASSETS_FONTS_READY;
    t_assets_us_ = now_us;
    Emit("state_transition", now_us, StateName(State::S1_NAV_OR_EVAL_ISSUED),
         StateName(State::S2_ASSETS_FONTS_READY),
         "\"phase\":\"assets_ready\",\"t_assets_wait_us\":" +
             std::to_string(now_us - t_begin_us_));
    return true;
  }
  // canary_set：S2 → S3（就绪候选，非提交完成证明）。
  if (state_ != State::S2_ASSETS_FONTS_READY) {
    ++counters_.illegal_transition_rejected;
    Emit("illegal_transition", now_us, StateName(state_), nullptr,
         "\"reason\":\"canary_set requires S2\"");
    return false;
  }
  state_ = State::S3_JS_FRAME_APPLIED_AND_CANARY_SET;
  t_canary_set_us_ = now_us;  // accepted_elapsed_us_since_ready 基准
  has_reported_originals_ = ev.has_reported_originals;
  for (int i = 0; i < 2; ++i) {
    // 页面上报 RGBA 直通 → 预乘存储口径（与 OnPaint 缓冲同域）。
    reported_original_bgra_[i][0] = PremulRound(ev.original_rgba[i][2],
                                                ev.original_rgba[i][3]);
    reported_original_bgra_[i][1] = PremulRound(ev.original_rgba[i][1],
                                                ev.original_rgba[i][3]);
    reported_original_bgra_[i][2] = PremulRound(ev.original_rgba[i][0],
                                                ev.original_rgba[i][3]);
    reported_original_bgra_[i][3] = ev.original_rgba[i][3];
  }
  Emit("state_transition", now_us, StateName(State::S2_ASSETS_FONTS_READY),
       StateName(State::S3_JS_FRAME_APPLIED_AND_CANARY_SET),
       std::string("\"phase\":\"canary_set\",\"t_ready_wait_us\":") +
           std::to_string(now_us - t_begin_us_) +
           ",\"has_reported_originals\":" +
           (ev.has_reported_originals ? "true" : "false"));
  return true;
}

bool FrameSyncStateMachine::EnterPumping(uint64_t now_us) {
  if (state_ != State::S3_JS_FRAME_APPLIED_AND_CANARY_SET) {
    ++counters_.illegal_transition_rejected;
    Emit("illegal_transition", now_us, StateName(state_), nullptr,
         "\"reason\":\"EnterPumping requires S3\"");
    return false;
  }
  state_ = State::S4_COMPOSITOR_PUMPING;
  t_pump_start_us_ = now_us;
  Emit("state_transition", now_us,
       StateName(State::S3_JS_FRAME_APPLIED_AND_CANARY_SET),
       StateName(State::S4_COMPOSITOR_PUMPING));
  return true;
}

// ---------------------------------------------------------------- OnPaint 闸门

PaintVerdict FrameSyncStateMachine::FeedPaint(const uint8_t* src, size_t stride,
                                              uint32_t width, uint32_t height,
                                              uint8_t* dst, uint64_t now_us) {
  PaintVerdict v;
  ++req_paints_;
  if (state_ < State::S4_COMPOSITOR_PUMPING) {
    // 工单：S1–S3 期间收到的异步残余 Paint（CEF #4166 软件缓冲同步重放的
    // 旧帧尾随 Paint）→ 直接丢弃并递增 early_paint_dropped。
    ++counters_.early_paint_dropped;
    ++req_early_;
    v.disposition = PaintDisposition::kEarlyDropped;
    Emit("paint", now_us, nullptr, nullptr,
         "\"disposition\":\"early_paint_dropped\",\"early_paint_dropped\":" +
             std::to_string(counters_.early_paint_dropped));
    return v;
  }
  if (state_ != State::S4_COMPOSITOR_PUMPING) {
    // S5/S0：本请求已完成（或无在途请求）——晚到 Paint 一律丢弃。
    ++counters_.late_paint_dropped;
    v.disposition = PaintDisposition::kLateDropped;
    Emit("paint", now_us, nullptr, nullptr,
         "\"disposition\":\"late_paint_dropped\"");
    return v;
  }
  if (src == nullptr || stride < 8) {
    ++counters_.rejected_stale_paint;
    ++req_rejected_;
    v.disposition = PaintDisposition::kRejectedStale;
    Emit("paint", now_us, nullptr, nullptr,
         "\"disposition\":\"rejected_stale_paint\",\"reason\":\"bad_buffer\"");
    return v;
  }
  if (width != desc_.expected_w || height != desc_.expected_h) {
    ++counters_.size_mismatch_paints;
    v.disposition = PaintDisposition::kSizeMismatch;
    Emit("paint", now_us, nullptr, nullptr,
         "\"disposition\":\"size_mismatch\",\"actual_w\":" +
             std::to_string(width) + ",\"actual_h\":" +
             std::to_string(height));
    return v;
  }

  const int want = desc_.target_frame_index;
  std::string reject_reason;

  if (desc_.canary == CanaryMode::kDiagnostic) {
    // —— 诊断轨：双对角 8×8 块核验（块内杂色=半更新/撕裂 → 拒收）——
    int32_t tl = -1;
    int32_t br = -1;
    const bool tl_ok =
        DecodeDiagBlock(src, stride, width, height, false, &tl);
    const bool br_ok =
        DecodeDiagBlock(src, stride, width, height, true, &br);
    if (tl_ok && br_ok && tl == want && br == want) {
      v.disposition = PaintDisposition::kAccepted;
      v.decoded_frame = tl;  // 取自解码结果（非请求值回显）
    } else {
      ++counters_.rejected_stale_paint;
      ++req_rejected_;
      v.disposition = PaintDisposition::kRejectedStale;
      v.half_signature = (tl_ok != br_ok);  // 单角可解码=半更新形态
      // 归因方向：优先报「与期望不符的那一角」解码值（错帧证据面）。
      v.decoded_frame = -1;
      if (tl_ok && tl != want) {
        v.decoded_frame = tl;
      } else if (br_ok && br != want) {
        v.decoded_frame = br;
      } else if (tl_ok) {
        v.decoded_frame = tl;
      } else if (br_ok) {
        v.decoded_frame = br;
      }
      reject_reason = !tl_ok || !br_ok
                          ? "stale_paint:diag_block_not_uniform"
                          : ("stale_paint:rendered=" + std::to_string(tl));
      Emit("paint", now_us, nullptr, nullptr,
           "\"disposition\":\"rejected_stale_paint\",\"reason\":\"" +
               reject_reason + "\",\"decoded_frame\":" +
               std::to_string(v.decoded_frame) +
               ",\"rejected_stale_paint_count\":" +
               std::to_string(req_rejected_));
      return v;
    }
  } else {
    // —— Clean 轨：双像素逐字节全等判据（accept_paint_bgra_inplace 口径）。
    const Bgra px1{src[0], src[1], src[2], src[3]};
    const Bgra px2{src[4], src[5], src[6], src[7]};
    const Bgra exp1 = SignatureBgraPremulP1(want);
    const Bgra exp2 = SignatureBgraPremulP2(want);
    const bool m1 = px1 == exp1;
    const bool m2 = px2 == exp2;
    if (!(m1 && m2)) {
      // 拒收归因：单像素候选交集（P2 自定位 + 联合孪生全域空 → 单例归因
      // 可信）。半更新（恰一者命中）单列 —— 双像素闸门下不可接受。
      ++counters_.rejected_stale_paint;
      ++req_rejected_;
      v.disposition = PaintDisposition::kRejectedStale;
      v.half_signature = m1 != m2;
      std::vector<int> s1, s2;
      DecodeCandidatesP1(src, &s1);
      DecodeCandidatesP2(src + 4, &s2);
      std::vector<int> inter;
      std::set_intersection(s1.begin(), s1.end(), s2.begin(), s2.end(),
                            std::back_inserter(inter));
      if (s1.empty() || s2.empty()) {
        reject_reason = "stale_paint:no_signature";
      } else if (inter.size() == 1) {
        v.decoded_frame = inter[0];
        reject_reason = "stale_paint:rendered=" + std::to_string(inter[0]);
      } else if (inter.empty()) {
        reject_reason = v.half_signature
                            ? "stale_paint:half_updated_signature"
                            : "stale_paint:attribution_incompatible";
      } else {
        reject_reason = "stale_paint:attribution_ambiguous";
      }
      Emit("paint", now_us, nullptr, nullptr,
           "\"disposition\":\"rejected_stale_paint\",\"reason\":\"" +
               reject_reason + "\",\"decoded_frame\":" +
               std::to_string(v.decoded_frame) + ",\"half_signature\":" +
               (v.half_signature ? "true" : "false") +
               ",\"rejected_stale_paint_count\":" +
               std::to_string(req_rejected_));
      return v;
    }
    // 防御分支（联合孪生全域空 —— 穷举背书；触发即协议常数被破坏）：
    // 观测字节虽全等，解码交集仍须单例 {f}。
    std::vector<int> s1, s2, inter;
    DecodeCandidatesP1(src, &s1);
    DecodeCandidatesP2(src + 4, &s2);
    std::set_intersection(s1.begin(), s1.end(), s2.begin(), s2.end(),
                          std::back_inserter(inter));
    if (inter.size() != 1 || inter[0] != want) {
      ++counters_.rejected_stale_paint;
      ++req_rejected_;
      v.disposition = PaintDisposition::kRejectedStale;
      reject_reason = "ambiguous_signature:twin";
      Emit("paint", now_us, nullptr, nullptr,
           "\"disposition\":\"rejected_stale_paint\",\"reason\":\"" +
               reject_reason + "\",\"rejected_stale_paint_count\":" +
               std::to_string(req_rejected_));
      return v;
    }
    v.disposition = PaintDisposition::kAccepted;
    v.decoded_frame = inter[0];  // receipt canary 来源：真实解码结果
  }

  // ================= 接受路径：紧凑拷贝 + 原地恢复两个绑定原像素 ==========
  const uint64_t t0 = NowUs();
  if (dst != nullptr) {
    CopyCompactRows(src, stride, dst, width, height);
    if (desc_.canary == CanaryMode::kClean) {
      // 恢复源优先级（工单）：1) 与本请求绑定、canary_set 时页面上报并经
      // 双像素验签绑定世代的原像素；2) 行 1 邻域兜底（仅 fixture 证明等值
      // 时成立 —— restore_from_reported=false 显形，不得通用化）。
      if (has_reported_originals_) {
        std::memcpy(dst + 0, reported_original_bgra_[0], 4);
        std::memcpy(dst + 4, reported_original_bgra_[1], 4);
        v.restore_from_reported = true;
      } else {
        std::memcpy(dst + 0, dst + static_cast<size_t>(width) * 4u, 4);
        std::memcpy(dst + 4, dst + static_cast<size_t>(width) * 4u + 4, 4);
        v.restore_from_reported = false;
      }
      v.restored = true;
    }
  }
  v.capture_us = NowUs() - t0;
  v.accepted_elapsed_us_since_ready = now_us - t_canary_set_us_;
  last_accepted_elapsed_us_ = v.accepted_elapsed_us_since_ready;
  state_ = State::S5_ONPAINT_VERIFIED;
  ++counters_.requests_accepted;
  Emit("state_transition", now_us, StateName(State::S4_COMPOSITOR_PUMPING),
       StateName(State::S5_ONPAINT_VERIFIED),
       "\"disposition\":\"accepted\",\"decoded_frame\":" +
           std::to_string(v.decoded_frame) +
           ",\"accepted_elapsed_us_since_ready\":" +
           std::to_string(v.accepted_elapsed_us_since_ready) +
           ",\"rejected_stale_paint_count\":" + std::to_string(req_rejected_) +
           ",\"restore_source\":" +
           std::string(v.restore_from_reported ? "\"reported_original\""
                                               : "\"neighbor_row1\""));
  return v;
}

bool FrameSyncStateMachine::FailRequest(const char* reason, uint64_t now_us) {
  if (state_ == State::S0_IDLE) {
    return false;
  }
  ++counters_.requests_failed;
  Emit("request_end", now_us, StateName(state_), StateName(State::S0_IDLE),
       std::string("\"status\":\"failed\",\"reason\":\"") +
           JsonEscape(reason != nullptr ? reason : "?") +
           "\",\"rejected_stale_paint_count\":" + std::to_string(req_rejected_) +
           ",\"early_paint_dropped\":" + std::to_string(req_early_) +
           ",\"paints_fed\":" + std::to_string(req_paints_));
  last_request_id_ = desc_.request_id;  // 重复请求守卫（终态记账）
  has_last_request_ = true;
  state_ = State::S0_IDLE;
  return true;
}

void FrameSyncStateMachine::Reset(uint64_t now_us, const char* reason) {
  if (state_ == State::S0_IDLE) {
    return;
  }
  Emit("request_end", now_us, StateName(state_), StateName(State::S0_IDLE),
       std::string("\"status\":\"") +
           (state_ == State::S5_ONPAINT_VERIFIED ? "accepted" : "aborted") +
           "\",\"reason\":\"" + JsonEscape(reason != nullptr ? reason : "?") +
           "\",\"accepted_elapsed_us_since_ready\":" +
           std::to_string(state_ == State::S5_ONPAINT_VERIFIED
                              ? last_accepted_elapsed_us_
                              : 0) +
           ",\"rejected_stale_paint_count\":" + std::to_string(req_rejected_) +
           ",\"early_paint_dropped\":" + std::to_string(req_early_) +
           ",\"paints_fed\":" + std::to_string(req_paints_));
  last_request_id_ = desc_.request_id;
  has_last_request_ = true;
  state_ = State::S0_IDLE;
}

}  // namespace fsync
}  // namespace vtx_cef
