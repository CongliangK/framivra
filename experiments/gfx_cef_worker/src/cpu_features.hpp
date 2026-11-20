#ifndef GFX_CEF_WORKER_CPU_FEATURES_H_
#define GFX_CEF_WORKER_CPU_FEATURES_H_

#if defined(_WIN32)
#include <windows.h>
#endif

namespace vtx_cef {

#if defined(__x86_64__) || defined(__i386__)
inline bool CpuHasAvx2() {
#if defined(_WIN32)
  // Windows reports an OS-usable feature, not just the CPU capability bit.
  // clang-cl's GNU builtin would link an unavailable __cpu_model runtime.
  return ::IsProcessorFeaturePresent(PF_AVX2_INSTRUCTIONS_AVAILABLE) != FALSE;
#else
  return __builtin_cpu_supports("avx2");
#endif
}
#endif

}  // namespace vtx_cef
#endif
