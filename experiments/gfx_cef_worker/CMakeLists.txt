# vtx_cef_worker 构建系统（issue #108：CEF 独立 Worker 构建系统、版本锁与产物隔离）
#
# 设计约束：
# - 平台版本锁在 cef_lock*.json，二进制获取/校验/解包由各平台构建脚本负责；
#   本文件不做任何网络获取（可复现性：无隐式下载）。
# - CEF_ROOT 指向解包后的官方分发包目录（cef_binary_*，.gitignore 隔离）。
#   构建脚本显式传 -DCEF_ROOT=...；独立调用本文件时可自动推断唯一候选。
# - 编译面：C++20；-O3 -march=x86-64-v3 -Wall -Wextra -Werror（工单要求，
#   经目录级 add_compile_options 全域生效：wrapper 与 vtx_cef_worker 一致；
#   CEF 上游 flag 集叠加同语义的 -O3/-Wall/-Wextra/-Werror 与 wrapper 头文件
#   所需的 -Wno-* 豁免）。
# - RPATH=$ORIGIN：运行时直接加载与可执行同目录的 libcef.so 等分发包文件
#   （由 build_cef_worker.sh 布置到 build/bin/；libEGL.so/libGLESv2.so 在
#   CEF Linux 分发包中不存在，见 build_cef_worker.sh 的布置清单说明）。
cmake_minimum_required(VERSION 3.21)
# C 语言需启用：CEF 官方 cef_variables.cmake(Linux) 会跑 CHECK_C_COMPILER_FLAG
project(vtx_cef_worker_build LANGUAGES C CXX)
if(APPLE)
  enable_language(OBJCXX)
elseif(WIN32)
  if(NOT MSVC OR NOT MSVC_CXX_ARCHITECTURE_ID STREQUAL "x64")
    message(FATAL_ERROR "CEF Windows minimal requires an x64 MSVC ABI compiler (MSVC or clang-cl).")
  endif()
  if(CMAKE_CONFIGURATION_TYPES OR NOT CMAKE_BUILD_TYPE STREQUAL "Release")
    message(FATAL_ERROR "CEF Windows minimal is Release-only; use build_windows.py.")
  endif()
  enable_language(RC)
  # Keep the worker, CEF wrapper, selftests and zlib on the same static CRT.
  set(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreaded)
  set(CEF_RUNTIME_LIBRARY_FLAG "" CACHE STRING "CRT selected by CMake" FORCE)
endif()

# ---- CEF_ROOT：上游分发包根目录 ----
if(NOT DEFINED CEF_ROOT)
  file(GLOB _cef_candidates "${CMAKE_CURRENT_SOURCE_DIR}/cef_binary_*")
  list(LENGTH _cef_candidates _cef_count)
  if(NOT _cef_count EQUAL 1)
    message(FATAL_ERROR
            "CEF_ROOT 未指定且无法唯一推断（cef_binary_* 候选数=${_cef_count}）。"
            "请先运行 build_cef_worker.sh，或显式传 -DCEF_ROOT=<解包目录>。")
  endif()
  list(GET _cef_candidates 0 CEF_ROOT)
  message(STATUS "CEF_ROOT 自动推断: ${CEF_ROOT}")
endif()
if(NOT IS_DIRECTORY "${CEF_ROOT}/libcef_dll")
  message(FATAL_ERROR "CEF_ROOT 不是有效分发包（缺 libcef_dll/）: ${CEF_ROOT}")
endif()

# ---- CEF 官方 cmake 配置（cef_variables/cef_macros + wrapper 源）----
list(APPEND CMAKE_MODULE_PATH "${CEF_ROOT}/cmake")
find_package(CEF REQUIRED)

# ---- 上游 flag 集与工单要求对齐 ----
if(WIN32)
  # Both the application and wrapper use the same C++/CRT exception model.
  list(REMOVE_ITEM CEF_COMPILER_DEFINES _HAS_EXCEPTIONS=0)
  # Statically import the side-by-side CEF DLL under /DEPENDENTLOADFLAG below,
  # rather than asking the delay-load helper to search for it at call time.
  list(REMOVE_ITEM CEF_EXE_LINKER_FLAGS /DELAYLOAD:libcef.dll)
  if(CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
    list(REMOVE_ITEM CEF_COMPILER_FLAGS /MP /GF)
    # Match the upstream Clang exemptions for CEF's generated wrapper code.
    list(APPEND CEF_CXX_COMPILER_FLAGS /clang:-Wno-missing-field-initializers
                                       /clang:-Wno-undefined-var-template)
  endif()
  add_compile_definitions(NOMINMAX WIN32_LEAN_AND_MEAN)
  add_compile_options("$<$<COMPILE_LANGUAGE:C,CXX>:/utf-8;/O2>"
                      "$<$<COMPILE_LANGUAGE:CXX>:/EHsc;/Zc:__cplusplus>")
else()
# 1) 上游对 x86_64 追加保守的 -march=x86-64，命令行后到者生效会压过工单的
#    -march=x86-64-v3；且 CMake 对重复 flag 去重，无法靠再追加覆盖 → 从
#    CEF 列表移除基线 march，全域统一 v3（下方目录级 add_compile_options）。
list(REMOVE_ITEM CEF_COMPILER_FLAGS -march=x86-64)
# 2) 上游 -Wl,-rpath,. 会让 cwd 抢在 $ORIGIN 之前命中劫持的 libcef.so，
#    违背工单"直接加载同目录"语义 → 移除。
list(REMOVE_ITEM CEF_LINKER_FLAGS -Wl,-rpath,.)
# 3) 链接解包目录内 libcef.so 时 CMake 会把该目录的绝对路径写进 build
#    RPATH（携带本机路径，破坏产物可迁移性）→ 转为 $ORIGIN 相对路径。
set(CMAKE_BUILD_RPATH_USE_ORIGIN TRUE)
# 4) CEF 154 appends -O2 to Release target flags after our directory -O3.
#    Keep all other upstream release/security flags and leave Debug unchanged.
list(REMOVE_ITEM CEF_COMPILER_FLAGS_RELEASE -O2)
list(APPEND CEF_COMPILER_FLAGS_RELEASE -O3)
endif()

# ---- 语言标准：C++20 ----
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# ---- 工单要求的编译面（GNU/Clang）----
if(NOT WIN32 AND CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  add_compile_options(-O3 -Wall -Wextra -Werror)
  if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64)$")
    add_compile_options(-march=x86-64-v3)
  else()
    message(WARNING "非 x86_64 平台：跳过 -march=x86-64-v3")
  endif()
endif()

# ---- libcef_dll_wrapper（上游 CMakeLists，经 SET_LIBRARY_TARGET_PROPERTIES
#      注入 CEF 官方 flag 集 + 头文件 -Wno-* 豁免）----
add_subdirectory("${CEF_LIBCEF_DLL_WRAPPER_PATH}"
                 "${CMAKE_BINARY_DIR}/libcef_dll_wrapper")

# ---- libcef: link the official Windows import library or Linux shared library ----
if(WIN32)
  add_library(libcef SHARED IMPORTED)
  set_target_properties(libcef PROPERTIES
    IMPORTED_IMPLIB "${CEF_LIB_RELEASE}"
    IMPORTED_LOCATION "${CEF_BINARY_DIR_RELEASE}/libcef.dll")
elseif(NOT APPLE)
  add_library(libcef SHARED IMPORTED)
  set_target_properties(libcef PROPERTIES
    IMPORTED_LOCATION "${CEF_LIB_RELEASE}"
    IMPORTED_SONAME "libcef.so")
endif()

# ---- vtx_cef_worker 可执行 ----
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
# issue #109：app_handler（A0/A1 开关注入 + OSR Client + 后端探针）、
# png_writer（最小 PNG 编码，系统 zlib）。
# issue #110：ipc_protocol/shm_ring（4-Slot SHM 环 + 定长协议，头文件库）、
# frame_pipeline（紧凑拷贝/金丝雀解码/SIMD 逆预乘）、serve（帧服务模式）。
# issue #111：pixel_ops（AVX2/NEON 逆预乘正式算子）、png_writer 升级双编码
# 后端 —— 内嵌第三方源码 third_party/spng（libspng v0.7.3，C 源）与
# third_party/fpng（fpng，C++ 源）：单文件上游源、以 -w 关上游告警面
# （上游代码不按本仓 -Werror 纪律维护；版本/哈希锚定 third_party_locks.json）。
# issue #111：pixel_ops（AVX2/NEON 逆预乘正式算子）、png_writer 升级双编码
#   后端 —— 内嵌第三方源码 third_party/spng（libspng v0.7.3，C 源）与
#   third_party/fpng（fpng，C++ 源）：单文件上游源、以 -w 关上游告警面
#   （上游代码不按本仓 -Werror 纪律维护；版本/哈希锚定 third_party_locks.json）。
# issue #114：frame_sync_machine（五阶段单调世代状态机 + v2 双像素 Canary
#   闸门，无 CEF 依赖 —— serve 与原生自检共用同一判定核心）。
set(VTX_THIRD_PARTY "${CMAKE_CURRENT_SOURCE_DIR}/third_party")
set(SPNG_SOURCES "${VTX_THIRD_PARTY}/spng/spng.c")
set(FPNG_SOURCES "${VTX_THIRD_PARTY}/fpng/fpng.cpp")
# 上游源按其自述要求补指令集（fpng 的 SSE4.1/PCLMUL intrinsic 运行时分派，
# 但编译 TU 需 -msse4.1 -mpclmul；二者不在 -march=x86-64-v3 集合内）；
# -w 关上游告警面（上游不按本仓 -Werror 纪律维护）。
if(WIN32)
  add_compile_definitions(SPNG_STATIC)
  set_source_files_properties(${SPNG_SOURCES} ${FPNG_SOURCES}
    PROPERTIES COMPILE_OPTIONS "/w" COMPILE_DEFINITIONS "_CRT_SECURE_NO_WARNINGS")
  if(CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
    set_property(SOURCE ${FPNG_SOURCES} APPEND PROPERTY
      COMPILE_OPTIONS "/clang:-msse4.1;/clang:-mpclmul")
  endif()
else()
  set_source_files_properties(${SPNG_SOURCES} PROPERTIES COMPILE_OPTIONS "-w")
  if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64)$")
    set_source_files_properties(${FPNG_SOURCES} PROPERTIES COMPILE_OPTIONS "-w;-msse4.1;-mpclmul")
  else()
    set_source_files_properties(${FPNG_SOURCES} PROPERTIES COMPILE_OPTIONS "-w")
  endif()
  # fpng owns temporary vectors; keep their unwind cleanup for bad_alloc.
  set_property(SOURCE ${FPNG_SOURCES} APPEND PROPERTY COMPILE_OPTIONS "-fexceptions")
endif()
add_executable(vtx_cef_worker src/main.cpp src/app_handler.cpp
                             src/frame_pipeline.cpp src/pixel_ops.cpp
                             src/png_writer.cpp src/serve.cpp
                             src/frame_sync_machine.cpp src/js_error_observer.cpp
                             ${SPNG_SOURCES} ${FPNG_SOURCES})
# serve.cpp translates allocation/encoding exceptions to failed requests. The
# writer owns the spng context/output while vector allocation or a progressive
# row callback can throw, so its RAII cleanup also needs unwind tables. CEF's
# upstream -fno-exceptions remains unchanged for other translation units.
if(NOT WIN32)
  set_source_files_properties(src/serve.cpp src/png_writer.cpp
                              PROPERTIES COMPILE_OPTIONS "-fexceptions")
  # CEF disables thread-safe local statics on POSIX; restore them for worker
  # TUs whose dynamic LUT/backend/filter initializers can run concurrently.
  set_property(SOURCE src/pixel_ops.cpp src/png_writer.cpp APPEND
               PROPERTY COMPILE_OPTIONS "-fthreadsafe-statics")
endif()
# CEF 官方 exe 属性宏：CEF flag 集（含 -std=c++20 -O3 -Wall -Wextra -Werror，
# 与目录级工单 flag 同语义）+ include 路径。
SET_EXECUTABLE_TARGET_PROPERTIES(vtx_cef_worker)
find_package(Threads REQUIRED)
if(WIN32)
  if(NOT IS_DIRECTORY "${VTX_ZLIB_ROOT}" OR NOT EXISTS "${VTX_ZLIB_ROOT}/zlib.h")
    message(FATAL_ERROR "Set VTX_ZLIB_ROOT to the verified zlib source or use build_windows.py.")
  endif()
  set(ZLIB_BUILD_SHARED OFF)
  set(ZLIB_BUILD_STATIC ON)
  set(ZLIB_BUILD_TESTING OFF)
  set(ZLIB_INSTALL OFF)
  add_subdirectory("${VTX_ZLIB_ROOT}" "${CMAKE_BINARY_DIR}/zlib" EXCLUDE_FROM_ALL)
  set(VTX_ZLIB_TARGET ZLIB::ZLIBSTATIC)
else()
  find_package(ZLIB REQUIRED)
  set(VTX_ZLIB_TARGET ZLIB::ZLIB)
endif()
target_include_directories(vtx_cef_worker PRIVATE
                          "${VTX_THIRD_PARTY}/spng" "${VTX_THIRD_PARTY}/fpng")
target_link_libraries(vtx_cef_worker PRIVATE
                      libcef_dll_wrapper Threads::Threads
                      ${VTX_ZLIB_TARGET} ${CMAKE_DL_LIBS})
# RPATH=$ORIGIN：与可执行同目录的 CEF 运行时优先于系统路径。
# BUILD_WITH_INSTALL_RPATH：以 INSTALL_RPATH 替代 build RPATH，避免链接
# 解包目录内 libcef.so 时 CMake 把该目录的本机绝对路径写入产物。
if(APPLE)
  set_target_properties(vtx_cef_worker PROPERTIES
                        BUILD_WITH_INSTALL_RPATH TRUE
                        INSTALL_RPATH "@executable_path/../Frameworks")
elseif(NOT WIN32)
  set_target_properties(vtx_cef_worker PROPERTIES
                        BUILD_WITH_INSTALL_RPATH TRUE INSTALL_RPATH "$ORIGIN")
endif()

# ---- shm_ring_selftest：4-Slot 环/协议原生自检（无 CEF 依赖；tests 门控
#      消费，亦支持 --dump-ring 供 Python 解析器跨语言锁定偏移表）。----
if(NOT WIN32)  # POSIX SHM diagnostic; Windows IPC is exercised via inherited pipes.
  add_executable(shm_ring_selftest src/shm_ring_selftest.cpp
                                  src/frame_pipeline.cpp src/pixel_ops.cpp)
  target_link_libraries(shm_ring_selftest PRIVATE Threads::Threads)
endif()

# ---- pixel_ops_selftest：#111 算子/双编码器/原子落盘原生自检与基准
#      （无 CEF 依赖；(c,a) 全域穷举 + 双编码器往返 + --bench 性能证据；
#      tests/gfx/test_cef_worker_pixel_ops.py 门控消费）。----
add_executable(pixel_ops_selftest src/pixel_ops_selftest.cpp
                                 src/pixel_ops.cpp src/png_writer.cpp
                                 ${SPNG_SOURCES} ${FPNG_SOURCES})
target_include_directories(pixel_ops_selftest PRIVATE
                          "${VTX_THIRD_PARTY}/spng" "${VTX_THIRD_PARTY}/fpng")
target_link_libraries(pixel_ops_selftest PRIVATE Threads::Threads ${VTX_ZLIB_TARGET})

# ---- frame_sync_selftest：#114 状态机原生对抗自检（无 CEF 依赖；强制注入
#      旧 paint/跨世代同帧/重复请求/签名半更新/超时等确定性 fixture；
#      --jsonl-out 供 Python 跨语言全量 JSON 校验；
#      tests/gfx/test_cef_frame_sync.py 门控消费）。----
add_executable(frame_sync_selftest src/frame_sync_selftest.cpp
                                  src/frame_sync_machine.cpp
                                  src/frame_pipeline.cpp src/pixel_ops.cpp)
target_link_libraries(frame_sync_selftest PRIVATE Threads::Threads)

# Deterministic fence deadline policy (synthetic timestamps; no CEF/retries).
add_executable(js_checkpoint_selftest src/js_checkpoint_selftest.cpp)

if(APPLE)
  target_sources(vtx_cef_worker PRIVATE src/mac_runtime.mm)
  set_source_files_properties(src/mac_runtime.mm PROPERTIES COMPILE_OPTIONS "-Wno-unused-parameter;-Wno-missing-field-initializers")
  target_link_libraries(vtx_cef_worker PRIVATE "-framework Cocoa")
  set_target_properties(vtx_cef_worker PROPERTIES MACOSX_BUNDLE TRUE
    MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_SOURCE_DIR}/mac/Info.plist")
elseif(WIN32)
  # Keep the console subsystem (wmain and inherited pipes). CEF disables the
  # default linker manifest; embed our app/compatibility manifest as resource 1.
  target_sources(vtx_cef_worker PRIVATE windows/vtx_cef_worker.rc)
  target_include_directories(vtx_cef_worker PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/windows")
  set_property(SOURCE windows/vtx_cef_worker.rc APPEND PROPERTY OBJECT_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/windows/vtx_cef_worker.exe.manifest")
  # LOAD_LIBRARY_SEARCH_APPLICATION_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32.
  target_link_options(vtx_cef_worker PRIVATE /DEPENDENTLOADFLAG:0xA00)
  target_link_libraries(vtx_cef_worker PRIVATE libcef ${CEF_STANDARD_LIBS})
else()
  target_link_libraries(vtx_cef_worker PRIVATE libcef)
endif()
