// frame_sync_machine.cpp — 五阶段单调世代状态机实现（issue #114 CEF-03a）。
//
// 纯 C++（无 CEF 依赖）：原生自检 frame_sync_selftest 与 serve 真实路径
// 共用同一判定核心，防「自检一套、线上另一套」的漂移。
#include "frame_sync_machine.hpp"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <limits>

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
  // Exact inverse of floor((508*C + 255) / 510), not an approximate inverse
  // alpha divide: 510*v - 255 <= 508*C <= 510*v + 254. The closed interval
  // contains at most two integers. Exhaustively checked against all 256*C
  // forward values in the native selftest; frozen clean signatures unchanged.
  const int lower = (510 * static_cast<int>(v) + 252) / 508;
  const int upper = std::min(255, (510 * static_cast<int>(v) + 254) / 508);
  std::vector<int> out;
  if (lower <= upper) {
    out.reserve(static_cast<size_t>(upper - lower + 1));
    for (int c = lower; c <= upper; ++c) out.push_back(c);
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

namespace {

bool ValidPaintBuffer(const uint8_t* src, size_t stride, uint32_t width,
                      uint32_t height) {
  if (!src || !width || !height) return false;
  const size_t limit = std::numeric_limits<size_t>::max();
  // 32 位平台 width*4 可能回绕;64 位平台此回检恒假但不触发恒真/恒假比较告警。
  const size_t row = static_cast<size_t>(width) * 4u;
  if (row < width) return false;
  return stride >= row && (height - 1u) <= (limit - row) / stride;
}

// Each row band independently carries a checksum, and EVERY byte in the band
// must agree. No averaging/sample-only check: one corrupted/half-updated pixel
// prevents the whole native paint from becoming a signed candidate.
bool DecodeProbeBand(const uint8_t* src, size_t stride, uint32_t rows,
                     bool p2, int32_t* token) {
  const uint8_t b = src[0], g = src[1], r = src[2], a = src[3];
  if (a != kDiagAlpha) return false;
  for (uint32_t y = 0; y < rows; ++y) {
    for (int x = 0; x < kDiagSize; ++x) {
      if (std::memcmp(src + y * stride + x * 4u, src, 4u) != 0) return false;
    }
  }
  if (p2) {
    if (((kP2MulR * r + kP2MulG * g + kDiag2AddKey) & 255) != b) return false;
    *token = (g << 8) | r;
  } else {
    if (((r ^ g ^ kDiagXorKey) & 255) != b) return false;
    *token = (r << 8) | g;
  }
  return true;
}

bool DecodeRequestProbe(const uint8_t* src, size_t stride, bool p2,
                        int32_t* frame, uint32_t* request) {
  int32_t lo = -1, hi = -1;
  if (!DecodeProbeBand(src, stride, 4, p2, frame) ||
      !DecodeProbeBand(src + 4u * stride, stride, 2, p2, &lo) ||
      !DecodeProbeBand(src + 6u * stride, stride, 2, p2, &hi)) return false;
  *request = static_cast<uint32_t>(lo) | (static_cast<uint32_t>(hi) << 16u);
  return true;
}

}  // namespace

bool DecodeDiagBlock(const uint8_t* src, size_t stride, uint32_t width,
                     uint32_t height, bool bottom_right, int32_t* frame_out) {
  if (width < static_cast<uint32_t>(kDiagSize) ||
      height < static_cast<uint32_t>(kDiagSize) || !frame_out ||
      !ValidPaintBuffer(src, stride, width, height)) return false;
  const size_t x = bottom_right ? width - kDiagSize : 0;
  const size_t y = bottom_right ? height - kDiagSize : 0;
  return DecodeProbeBand(src + y * stride + x * 4u, stride, kDiagSize,
                         bottom_right, frame_out);
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
  if ((desc.canary == CanaryMode::kExact) &&
      (desc.request_id == 0 || desc.request_id <= exact_request_high_water_)) {
    ++counters_.duplicate_request_rejected;
    Emit("duplicate_request_rejected", now_us, nullptr, nullptr,
         "\"reason\":\"exact request token reused or wrapped\"");
    return false;
  }
  if (desc.target_frame_index < 0 ||
      static_cast<uint32_t>(desc.target_frame_index) > kFrameMax ||
      desc.expected_w < 2 || desc.expected_h < 2 ||
      desc.expected_w > std::numeric_limits<size_t>::max() / 4u / desc.expected_h ||
      (desc.canary != CanaryMode::kClean &&
       (desc.expected_w < static_cast<uint32_t>(kDiagSize) ||
        desc.expected_h < static_cast<uint32_t>(kDiagSize))) ||
      (desc.canary == CanaryMode::kExact &&
       (desc.expected_w < 2 * kDiagSize || desc.expected_h < 2 * kDiagSize))) {
    Emit("invalid_request", now_us, nullptr, nullptr,
         "\"reason\":\"frame/w/h out of domain\"");
    return false;
  }
  desc_ = desc;
  if (desc.canary == CanaryMode::kExact) exact_request_high_water_ = desc.request_id;
  exact_first_verified_ = false;
  exact_candidate_pending_ = false;
  exact_candidate_decoded_frame_ = -1;
  exact_baseline_equal_paints_ = 0;
  exact_baseline_last_paint_ = 0;
  exact_baseline_ready_paint_ = 0;
  exact_baseline_ready_ = false;
  exact_candidate_decoded_request_id_ = 0;
  exact_first_.clear();
  exact_candidate_.clear();
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
  last_accepted_elapsed_us_ = 0;
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

// Shared negative-only signature classifier for zero-copy rejection and the
// fenced FeedPaint path. It NEVER changes state, counters or output bytes.
bool FrameSyncStateMachine::DecodeSignedProbes(const uint8_t* src, size_t stride,
    uint32_t width, uint32_t height, PaintVerdict* v) const {
  const bool second = desc_.canary == CanaryMode::kExact && exact_first_verified_;
  const uint8_t* p1 = src + (second ? static_cast<size_t>(width - kDiagSize) * 4u : 0u);
  const uint8_t* p2 = src + static_cast<size_t>(height - kDiagSize) * stride +
      (second ? 0u : static_cast<size_t>(width - kDiagSize) * 4u);
  int32_t frame1 = -1, frame2 = -1;
  uint32_t req1 = 0, req2 = 0;
  const bool one = (desc_.canary == CanaryMode::kExact)
      ? DecodeRequestProbe(p1, stride, false, &frame1, &req1)
      : DecodeProbeBand(p1, stride, kDiagSize, false, &frame1);
  const bool two = (desc_.canary == CanaryMode::kExact)
      ? DecodeRequestProbe(p2, stride, true, &frame2, &req2)
      : DecodeProbeBand(p2, stride, kDiagSize, true, &frame2);
  if (second) {
    if (one) { frame1 ^= 0xFFFF; req1 ^= 0xFFFFFFFFu; }
    if (two) { frame2 ^= 0xFFFF; req2 ^= 0xFFFFFFFFu; }
  }
  v->half_signature = one != two;
  // Decode frame/identity from native bytes, NEVER echo them from RequestDesc.
  v->decoded_frame = one && two && frame1 == frame2 ? frame1 : -1;
  if ((desc_.canary == CanaryMode::kExact) && one && two && req1 == req2)
    v->decoded_request_id = req1;
  const bool match = one && two && frame1 == desc_.target_frame_index &&
      frame2 == desc_.target_frame_index &&
      (desc_.canary != CanaryMode::kExact ||
       (req1 == desc_.request_id && req2 == desc_.request_id));
  if (!match) {
    // Preserve useful wrong-frame attribution (a disagreeing corner is proof
    // of rejection only; it cannot become an accepted frame identity).
    if (one && frame1 != desc_.target_frame_index) v->decoded_frame = frame1;
    else if (two && frame2 != desc_.target_frame_index) v->decoded_frame = frame2;
  }
  return match;
}

bool FrameSyncStateMachine::PaintMayAdvance(const uint8_t* src, size_t stride,
    uint32_t width, uint32_t height) const {
  if (state_ != State::S4_COMPOSITOR_PUMPING || exact_candidate_pending_ ||
      width != desc_.expected_w || height != desc_.expected_h ||
      !ValidPaintBuffer(src, stride, width, height)) return false;
  if (desc_.canary == CanaryMode::kClean) {
    const Bgra one = SignatureBgraPremulP1(desc_.target_frame_index);
    const Bgra two = SignatureBgraPremulP2(desc_.target_frame_index);
    return std::memcmp(src, &one, 4u) == 0 && std::memcmp(src + 4u, &two, 4u) == 0;
  }
  PaintVerdict candidate;
  return DecodeSignedProbes(src, stride, width, height, &candidate);
}

// ---------------------------------------------------------------- OnPaint 闸门

PaintVerdict FrameSyncStateMachine::FeedPaint(const uint8_t* src, size_t stride,
    uint32_t width, uint32_t height, uint8_t* dst, uint64_t now_us) {
  return FeedPaintImpl(src, stride, width, height, dst, now_us, nullptr);
}

PaintVerdict FrameSyncStateMachine::FeedOwnedPaint(std::vector<uint8_t>& src,
    uint32_t width, uint32_t height, uint8_t* dst, uint64_t now_us) {
  const bool compact = height &&
      width <= std::numeric_limits<size_t>::max() / 4u / height &&
      src.size() == static_cast<size_t>(width) * height * 4u;
  return FeedPaintImpl(compact ? src.data() : nullptr,
      compact ? static_cast<size_t>(width) * 4u : 0, width, height, dst, now_us,
      compact ? &src : nullptr);
}

PaintVerdict FrameSyncStateMachine::FeedPaintImpl(const uint8_t* src, size_t stride,
    uint32_t width, uint32_t height, uint8_t* dst, uint64_t now_us,
    std::vector<uint8_t>* owned) {
  PaintVerdict v;
  ++req_paints_;
  if (state_ > State::S0_IDLE && state_ < State::S4_COMPOSITOR_PUMPING) {
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
  if (!ValidPaintBuffer(src, stride, width, height)) {
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

  if (exact_candidate_pending_) {
    v.disposition = PaintDisposition::kEarlyDropped;
    return v;  // freeze the signed reconstruction while independently resampling
  }
  const int want = desc_.target_frame_index;
  std::string reject_reason;

  if (desc_.canary != CanaryMode::kClean) {
    if (DecodeSignedProbes(src, stride, width, height, &v)) {
      v.disposition = PaintDisposition::kAccepted;  // not S5 until full checks below
    } else {
      ++counters_.rejected_stale_paint;
      ++req_rejected_;
      v.disposition = PaintDisposition::kRejectedStale;
      reject_reason = "stale_paint:probe_signature_or_identity";
      Emit("paint", now_us, nullptr, nullptr,
           "\"disposition\":\"rejected_stale_paint\",\"reason\":\"" +
               reject_reason + "\",\"decoded_frame\":" +
               std::to_string(v.decoded_frame) +
               ",\"decoded_request_id\":" + std::to_string(v.decoded_request_id) +
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

  if (desc_.canary == CanaryMode::kExact) {
    if (!exact_first_verified_) {
      if (owned) {
        exact_first_.swap(*owned);  // immutable post-fence native storage
      } else {
        exact_first_.resize(static_cast<size_t>(width) * height * 4u);
        CopyCompactRows(src, stride, exact_first_.data(), width, height);
      }
      exact_first_verified_ = true;
      v.disposition = PaintDisposition::kEarlyDropped;  // not deliverable yet
      Emit("exact_first_verified", now_us, nullptr, nullptr,
           "\"decoded_frame\":" + std::to_string(v.decoded_frame) +
               ",\"decoded_request_id\":" + std::to_string(v.decoded_request_id));
      return v;
    }
    // Compare every byte outside the FOUR probe rectangles. A changing page
    // (CSS animation, asynchronous DOM, backdrop interaction, etc.) fails closed.
    for (uint32_t y = 0; y < height; ++y) {
      const bool edge = y < kDiagSize || y >= height - kDiagSize;
      const size_t left = edge ? kDiagSize * 4u : 0;
      const size_t count = static_cast<size_t>(edge ? width - 2 * kDiagSize : width) * 4u;
      if (std::memcmp(src + y * stride + left,
                      exact_first_.data() + static_cast<size_t>(y) * width * 4u + left,
                      count) != 0) {
        v.disposition = PaintDisposition::kRejectedStale;
        ++req_rejected_;
        ++counters_.rejected_stale_paint;
        Emit("exact_content_mismatch", now_us, nullptr, nullptr);
        return v;
      }
    }
    // First paint owns the unoccluded TR/BL pixels; second owns TL/BR.
    // Restore actual BGRA_PREMUL bytes before the existing alpha/PNG pipeline.
    // Both signatures and every unoccluded byte are verified. Exact always
    // freezes at this point, so no later signed paint needs the first buffer.
    // Transfer it and patch only the two covered 8x8 corners (512 bytes total).
    exact_candidate_.swap(exact_first_);
    for (uint32_t y = 0; y < height; ++y) {
      if (y < kDiagSize || y >= height - kDiagSize) {
        const size_t x = y < kDiagSize ? 0 : width - kDiagSize;
        std::memcpy(exact_candidate_.data() +
                        (static_cast<size_t>(y) * width + x) * 4u,
                    src + y * stride + x * 4u, kDiagSize * 4u);
      }
    }
    // Identical probe side-effects in BOTH signed phases cannot certify
    // noninterference. Freeze the reconstruction; the separate unsigned
    // document must supply TWO fresh full-frame equalities, including every
    // corner and alpha byte. Its pixels never supply output or frame identity.
    exact_candidate_pending_ = true;
    exact_candidate_decoded_frame_ = v.decoded_frame;
    exact_candidate_decoded_request_id_ = v.decoded_request_id;
    v.disposition = PaintDisposition::kEarlyDropped;  // not deliverable yet
    Emit("exact_baseline_pending", now_us, nullptr, nullptr,
         "\"reason\":\"awaiting_independent_unsigned_veto\"");
    return v;
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
  return FinalizeAccepted(v, now_us);
}

bool FrameSyncStateMachine::ArmExactBaseline(const PageEvent& identity,
    uint64_t ready_paint_sequence, uint64_t now_us) {
  if (desc_.canary != CanaryMode::kExact ||
      state_ == State::S0_IDLE || state_ == State::S5_ONPAINT_VERIFIED ||
      exact_baseline_ready_ || identity.document_gen != desc_.document_gen ||
      identity.request_id != desc_.request_id ||
      identity.target_frame_index != desc_.target_frame_index) {
    Emit("exact_baseline_ready_rejected", now_us, nullptr, nullptr);
    return false;
  }
  exact_baseline_ready_ = true;
  exact_baseline_ready_paint_ = ready_paint_sequence;
  exact_baseline_last_paint_ = ready_paint_sequence;
  Emit("exact_baseline_veto_armed", now_us, nullptr, nullptr,
       "\"role\":\"equality_veto_only\",\"restoration_source\":false,"
       "\"after_native_paint_sequence\":" + std::to_string(ready_paint_sequence));
  return true;
}

PaintVerdict FrameSyncStateMachine::FeedExactBaselinePaint(
    const PageEvent& identity, const uint8_t* src, size_t stride,
    uint32_t width, uint32_t height, uint8_t* dst, uint64_t now_us,
    uint64_t paint_sequence) {
  PaintVerdict v;
  v.disposition = PaintDisposition::kRejectedStale;
  if (!needs_baseline_veto() || desc_.canary != CanaryMode::kExact ||
      identity.document_gen != desc_.document_gen ||
      identity.request_id != desc_.request_id ||
      identity.target_frame_index != desc_.target_frame_index ||
      !src || width != desc_.expected_w || height != desc_.expected_h ||
      !ValidPaintBuffer(src, stride, width, height)) {
    exact_baseline_equal_paints_ = 0;
    return v;
  }
  if (!exact_baseline_ready_ || paint_sequence == 0 ||
      paint_sequence <= exact_baseline_ready_paint_ ||
      paint_sequence <= exact_baseline_last_paint_) {
    exact_baseline_equal_paints_ = 0;
    Emit("exact_baseline_paint_replayed", now_us, nullptr, nullptr,
         "\"paint_sequence\":" + std::to_string(paint_sequence));
    return v;
  }
  exact_baseline_last_paint_ = paint_sequence;
  for (uint32_t y = 0; y < height; ++y) {
    if (std::memcmp(src + y * stride,
        exact_candidate_.data() + static_cast<size_t>(y) * width * 4u,
        static_cast<size_t>(width) * 4u) != 0) {
      exact_baseline_equal_paints_ = 0;
      ++req_rejected_;
      ++counters_.rejected_stale_paint;
      // Retain the public veto-category event consumed by native acceptance
      // tests/reports. A mismatch alone cannot distinguish probe perturbation
      // from an unsigned page whose rasterization has not caught up yet.
      Emit("exact_baseline_mismatch", now_us, nullptr, nullptr,
           "\"reason\":\"probe_content_perturbation\","
           "\"observation\":\"native_full_frame_mismatch\",\"cause_unproven\":true,"
           "\"mismatch_row\":" + std::to_string(y) +
           ",\"paint_sequence\":" + std::to_string(paint_sequence));
      return v;
    }
  }
  if (++exact_baseline_equal_paints_ < 2) {
    return v;
  }
  Emit("exact_baseline_equal", now_us, nullptr, nullptr,
       std::string("\"role\":\"equality_veto_only\",\"baseline_source\":\"") +
       (desc_.drive == DriveMode::kModeP ? "persistent_unsigned" :
                                        "concurrent_unsigned_navigation") +
       "\",\"consecutive_equal_paints\":2,\"compared_bytes\":" +
       std::to_string(exact_candidate_.size()));
  const uint64_t t0 = NowUs();
  if (dst) std::memcpy(dst, exact_candidate_.data(), exact_candidate_.size());
  v.disposition = PaintDisposition::kAccepted;
  v.decoded_frame = exact_candidate_decoded_frame_;
  v.decoded_request_id = exact_candidate_decoded_request_id_;
  v.restored = true;
  v.capture_us = NowUs() - t0;
  exact_candidate_pending_ = false;
  return FinalizeAccepted(v, now_us);
}

PaintVerdict FrameSyncStateMachine::FinalizeAccepted(PaintVerdict v, uint64_t now_us) {
  v.accepted_elapsed_us_since_ready = now_us - t_canary_set_us_;
  last_accepted_elapsed_us_ = v.accepted_elapsed_us_since_ready;
  state_ = State::S5_ONPAINT_VERIFIED;
  ++counters_.requests_accepted;
  Emit("state_transition", now_us, StateName(State::S4_COMPOSITOR_PUMPING),
       StateName(State::S5_ONPAINT_VERIFIED),
       "\"disposition\":\"accepted\",\"decoded_frame\":" +
           std::to_string(v.decoded_frame) +
           ",\"decoded_request_id\":" + std::to_string(v.decoded_request_id) +
           ",\"proof_binding\":\"" +
           ((desc_.canary == CanaryMode::kExact) ? "frame16_request32_dual_probe" : "frame16_dual_probe") +
           "\",\"accepted_elapsed_us_since_ready\":" +
           std::to_string(v.accepted_elapsed_us_since_ready) +
           ",\"rejected_stale_paint_count\":" + std::to_string(req_rejected_) +
           ",\"restore_source\":" +
           std::string(desc_.canary == CanaryMode::kExact ? "\"native_cross_capture\"" : v.restore_from_reported ? "\"reported_original\""
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
