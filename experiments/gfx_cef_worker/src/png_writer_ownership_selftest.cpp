// Issue #207: CEF-free ownership regression for the real PNG writer + libspng.
// GNU ld --wrap is test-only; no production hooks or custom spng allocator.
// Run through VTX_TEST_PNG_OWNERSHIP=1 tests.gfx.test_cef_worker_pixel_ops.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <new>
#include <string>
#include <vector>

#include "png_writer.h"
#include "spng.h"

namespace {
enum class Fault { kNone, kContext, kOption, kEncode, kGet, kEmpty, kAssign };
struct Tracking {
  Fault fault = Fault::kNone;
  spng_ctx* context = nullptr;
  void* buffer = nullptr;
  int contexts = 0, contexts_freed = 0, context_destroy_calls = 0;
  int buffers = 0, buffers_freed = 0;
};
Tracking g_track;
bool g_tracking = false;
bool g_fail_next_new = false;
int g_failures = 0;
}

extern "C" {
spng_ctx* __real_spng_ctx_new(int);
void __real_spng_ctx_free(spng_ctx*);
int __real_spng_set_option(spng_ctx*, enum spng_option, int);
int __real_spng_encode_image(spng_ctx*, const void*, size_t, int, int);
void* __real_spng_get_png_buffer(spng_ctx*, size_t*, int*);
void __real_free(void*);

spng_ctx* __wrap_spng_ctx_new(int flags) {
  if (g_tracking && g_track.fault == Fault::kContext) return nullptr;
  spng_ctx* ctx = __real_spng_ctx_new(flags);
  if (g_tracking && ctx != nullptr) {
    g_track.context = ctx;
    ++g_track.contexts;
  }
  return ctx;
}
void __wrap_spng_ctx_free(spng_ctx* ctx) {
  if (g_tracking && ctx != nullptr && ctx == g_track.context) {
    ++g_track.context_destroy_calls;
  }
  __real_spng_ctx_free(ctx);
}
int __wrap_spng_set_option(spng_ctx* ctx, enum spng_option option, int value) {
  if (g_tracking && g_track.fault == Fault::kOption) return SPNG_EINVAL;
  return __real_spng_set_option(ctx, option, value);
}
int __wrap_spng_encode_image(spng_ctx* ctx, const void* image, size_t len,
                            int fmt, int flags) {
  const int result = __real_spng_encode_image(ctx, image, len, fmt, flags);
  // Fail after real encoding allocated output, but before ownership transfer.
  if (g_tracking && g_track.fault == Fault::kEncode && result == 0)
    return SPNG_EINVAL;
  return result;
}
void* __wrap_spng_get_png_buffer(spng_ctx* ctx, size_t* len, int* error) {
  // A failed getter must not first transfer ownership and then hide the pointer.
  if (g_tracking && g_track.fault == Fault::kGet) {
    *len = 0;
    *error = SPNG_EBADSTATE;
    return nullptr;
  }
  void* buffer = __real_spng_get_png_buffer(ctx, len, error);
  if (g_tracking && buffer != nullptr) {
    g_track.buffer = buffer;
    ++g_track.buffers;
    if (g_track.fault == Fault::kEmpty) *len = 0;  // synthetic adapter boundary
    if (g_track.fault == Fault::kAssign) g_fail_next_new = true;
  }
  return buffer;
}
void __wrap_free(void* pointer) {
  if (g_tracking && pointer != nullptr && pointer == g_track.context) {
    ++g_track.contexts_freed;  // Observe actual allocator free, not just ctx_free.
    g_track.context = nullptr;
  }
  if (g_tracking && pointer != nullptr && pointer == g_track.buffer) {
    ++g_track.buffers_freed;
    g_track.buffer = nullptr;
  }
  __real_free(pointer);
}
}

// The empty output vector's allocation is the first C++ allocation after the
// successful getter. Inject one bad_alloc there, never actual memory pressure.
void* operator new(std::size_t size) {
  if (g_fail_next_new) {
    g_fail_next_new = false;
    throw std::bad_alloc();
  }
  if (void* pointer = std::malloc(size == 0 ? 1 : size)) return pointer;
  throw std::bad_alloc();
}
void* operator new[](std::size_t size) { return ::operator new(size); }
void operator delete(void* pointer) noexcept { std::free(pointer); }
void operator delete[](void* pointer) noexcept { std::free(pointer); }
void operator delete(void* pointer, std::size_t) noexcept { std::free(pointer); }
void operator delete[](void* pointer, std::size_t) noexcept { std::free(pointer); }

namespace {
void Check(bool ok, const char* name) {
  std::printf("%s %s\n", ok ? "PASS" : "FAIL", name);
  if (!ok) ++g_failures;
}
struct Result {
  bool returned = false, threw = false;
  Tracking resources;
  bool balanced() const {
    return resources.contexts == resources.contexts_freed &&
           resources.contexts == resources.context_destroy_calls &&
           resources.buffers == resources.buffers_freed;
  }
};
Result Encode(const uint8_t* rgba, int w, int h,
              const vtx_cef::PngEncodeOptions& options,
              std::vector<uint8_t>* png, Fault fault = Fault::kNone) {
  g_track = Tracking{};
  g_track.fault = fault;
  g_tracking = true;
  Result result;
  try {
    result.returned = vtx_cef::EncodePngRgba(rgba, w, h, options, png);
  } catch (const std::bad_alloc&) {
    result.threw = true;
  }
  result.resources = g_track;  // Measure BEFORE harness cleanup of broken code.
  g_fail_next_new = false;
  g_tracking = false;
  // Baseline/mutant failures are cleaned after measurement so thousands of
  // regression calls cannot themselves cause OOM. This is not a LSan test.
  if (g_track.context != nullptr) __real_spng_ctx_free(g_track.context);
  if (g_track.buffer != nullptr) __real_free(g_track.buffer);
  return result;
}

std::vector<uint8_t> MakeFrame(int width, int height, int kind) {
  std::vector<uint8_t> rgba(static_cast<size_t>(width) * height * 4);
  uint32_t state = 0x12345678;
  for (int y = 0; y < height; ++y) {
    for (int x = 0; x < width; ++x) {
      auto* pixel = rgba.data() + (static_cast<size_t>(y) * width + x) * 4;
      for (int c = 0; c < 3; ++c) {
        state = state * 1664525u + 1013904223u;
        pixel[c] = kind == 3 ? static_cast<uint8_t>(state >> 24)
                            : static_cast<uint8_t>(x * 13 + y * 7 + c * 73);
      }
      pixel[3] = kind == 0 || kind == 3 ? 255
                 : kind == 2 ? 0 : static_cast<uint8_t>(x + y * 17);
    }
  }
  return rgba;
}
bool DecodeMatches(const std::vector<uint8_t>& png,
                   const std::vector<uint8_t>& rgba, int width, int height) {
  std::unique_ptr<spng_ctx, decltype(&spng_ctx_free)> ctx(spng_ctx_new(0),
                                                        &spng_ctx_free);
  if (!ctx || spng_set_png_buffer(ctx.get(), png.data(), png.size()) != 0)
    return false;
  spng_ihdr ihdr{};
  size_t len = 0;
  if (spng_get_ihdr(ctx.get(), &ihdr) != 0 ||
      ihdr.width != static_cast<uint32_t>(width) ||
      ihdr.height != static_cast<uint32_t>(height) ||
      ihdr.bit_depth != 8 || ihdr.color_type != 6 ||
      spng_decoded_image_size(ctx.get(), SPNG_FMT_RGBA8, &len) != 0 ||
      len != rgba.size()) return false;
  std::vector<uint8_t> decoded(len);
  return spng_decode_image(ctx.get(), decoded.data(), len, SPNG_FMT_RGBA8, 0) == 0
         && decoded == rgba;
}
void TestErrors() {
  const auto rgba = MakeFrame(17, 13, 1);
  vtx_cef::PngEncodeOptions options;
  struct ErrorCase { const char* name; Fault fault; int contexts, buffers; };
  const ErrorCase cases[] = {
      {"context_failure", Fault::kContext, 0, 0},
      {"option_failure", Fault::kOption, 1, 0},
      {"encode_failure_after_allocation", Fault::kEncode, 1, 0},
      {"getter_failure_retains_context_ownership", Fault::kGet, 1, 0},
      {"non_null_zero_length_rejected_and_freed", Fault::kEmpty, 1, 1},
      {"assign_bad_alloc_releases_context_and_buffer", Fault::kAssign, 1, 1},
  };
  for (const auto& tc : cases) {
    std::vector<uint8_t> png;  // capacity=0 forces assign to allocate
    const Result r = Encode(rgba.data(), 17, 13, options, &png, tc.fault);
    const bool expected_throw = tc.fault == Fault::kAssign;
    Check(!r.returned && r.threw == expected_throw && png.empty() &&
          r.resources.contexts == tc.contexts &&
          r.resources.buffers == tc.buffers && r.balanced(), tc.name);
    std::printf("COUNTS %s contexts=%d/%d destroy_calls=%d buffers=%d/%d threw=%d\n", tc.name,
                r.resources.contexts, r.resources.contexts_freed,
                r.resources.context_destroy_calls,
                r.resources.buffers, r.resources.buffers_freed, r.threw);
  }
  for (int level : {0, 4}) {
    options.level = level;
    std::vector<uint8_t> png{1, 2, 3};
    const Result r = Encode(rgba.data(), 17, 13, options, &png);
    Check(!r.returned && !r.threw && r.balanced() &&
          r.resources.contexts == 0 && png == std::vector<uint8_t>({1, 2, 3}),
          "invalid_level_preserves_output");
  }
  options.level = 1;
  options.spng_filter = static_cast<vtx_cef::SpngFilter>(1);
  std::vector<uint8_t> png{1, 2, 3};
  const Result invalid_filter = Encode(rgba.data(), 17, 13, options, &png);
  Check(!invalid_filter.returned && !invalid_filter.threw &&
        invalid_filter.resources.contexts == 1 && invalid_filter.balanced() &&
        png == std::vector<uint8_t>({1, 2, 3}), "invalid_filter_releases_context");
  options = vtx_cef::PngEncodeOptions{};
  const Result invalid[] = {Encode(nullptr, 17, 13, options, &png),
      Encode(rgba.data(), 17, 13, options, nullptr),
      Encode(rgba.data(), 0, 13, options, &png),
      Encode(rgba.data(), 17, -1, options, &png)};
  bool ok = png == std::vector<uint8_t>({1, 2, 3});
  for (const auto& r : invalid)
    ok = ok && !r.returned && !r.threw && r.balanced() && r.resources.contexts == 0;
  Check(ok, "invalid_arguments_preserve_output_without_allocation");
}

const vtx_cef::SpngFilter kFilters[] = {vtx_cef::SpngFilter::kNone,
    vtx_cef::SpngFilter::kSub, vtx_cef::SpngFilter::kUp,
    vtx_cef::SpngFilter::kSubUp, vtx_cef::SpngFilter::kAll};
void TestPixels(const std::string& output) {
  const struct { int width, height, kind; } cases[] = {
      {1, 1, 0}, {61, 13, 0}, {64, 48, 1}, {17, 31, 2}, {65, 47, 3}};
  int failures = 0, samples = 0;
  for (size_t i = 0; i < std::size(cases); ++i) {
    const auto& tc = cases[i];
    const auto frame = MakeFrame(tc.width, tc.height, tc.kind);
    const std::string raw_name = "input_" + std::to_string(i) + ".rgba";
    if (!output.empty() && !vtx_cef::AtomicWriteBytes(
        output + "/" + raw_name, frame.data(), frame.size())) ++failures;
    for (int level = 1; level <= 3; ++level) {
      for (auto filter : kFilters) {
        vtx_cef::PngEncodeOptions options;
        options.level = level;
        options.spng_filter = filter;
        std::vector<uint8_t> png, again;
        const Result r = Encode(frame.data(), tc.width, tc.height, options, &png);
        const Result rr = Encode(frame.data(), tc.width, tc.height, options, &again);
        if (!r.returned || r.threw || !r.balanced() || r.resources.contexts != 1 ||
            r.resources.buffers != 1 || !rr.returned || rr.threw || !rr.balanced() ||
            png != again || !DecodeMatches(png, frame, tc.width, tc.height)) ++failures;
        const std::string name = "sample_" + std::to_string(i) + "_l" +
            std::to_string(level) + "_f" + std::to_string(static_cast<int>(filter)) + ".png";
        if (!output.empty()) {
          if (!vtx_cef::AtomicWriteBytes(output + "/" + name, png.data(), png.size()))
            ++failures;
          std::printf("SAMPLE\t%s\t%d\t%d\t%s\n", name.c_str(), tc.width,
                      tc.height, raw_name.c_str());
        }
        ++samples;
      }
    }
  }
  std::printf("PIXELS samples=%d failures=%d\n", samples, failures);
  Check(samples == 75 && failures == 0, "pixels_all_levels_filters_and_determinism");
}
void TestRepeated() {
  const auto rgba = MakeFrame(64, 48, 1);
  vtx_cef::PngEncodeOptions options;
  std::vector<uint8_t> reused;
  int failures = 0, contexts = 0, freed_contexts = 0, buffers = 0, freed_buffers = 0;
  for (int i = 0; i < 2000; ++i) {
    options.level = 1 + i % 3;
    options.spng_filter = kFilters[i % 5];
    std::vector<uint8_t> fresh;
    auto* out = i % 2 == 0 ? &reused : &fresh;
    const Result r = Encode(rgba.data(), 64, 48, options, out);
    contexts += r.resources.contexts;
    freed_contexts += r.resources.contexts_freed;
    buffers += r.resources.buffers;
    freed_buffers += r.resources.buffers_freed;
    if (!r.returned || r.threw || !r.balanced() ||
        r.resources.contexts != 1 || r.resources.buffers != 1 ||
        !DecodeMatches(*out, rgba, 64, 48)) ++failures;
  }
  std::printf("REPEATED iterations=2000 contexts=%d/%d buffers=%d/%d failures=%d\n",
              contexts, freed_contexts, buffers, freed_buffers, failures);
  Check(failures == 0, "repeated_fresh_and_reused_output_vectors");
}
}

int main(int argc, char** argv) {
  if (argc > 2) return 2;
  TestErrors();
  TestPixels(argc == 2 ? argv[1] : "");
  TestRepeated();
  std::printf("OWNERSHIP %s failures=%d\n", g_failures == 0 ? "ALL PASS" : "FAIL",
              g_failures);
  return g_failures == 0 ? 0 : 1;
}
