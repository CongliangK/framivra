# vtx_elec_output_helper 构建系统（issue #113 ELEC-02：Electron 输出桥原生组件）。
#
# 设计约束（镜像 gfx_cef_worker 纪律）：
# - 零网络、零 CEF 依赖：本组件只复用 #111 交付的纯原生源码
#   （pixel_ops/png_writer + third_party spng/fpng），以只读方式引用
#   ../../gfx_cef_worker/ 下的源文件与头文件路径——绝不写入该目录。
# - 产物隔离在本目录 build/（.gitignore 在册）。
# - 编译面与 CEF 侧一致：C++20、-O3 -march=x86-64-v3 -Wall -Wextra -Werror
#   （第三方上游源 -w 豁免 + fpng 的 -msse4.1 -mpclmul，同 CEF CMakeLists）。
cmake_minimum_required(VERSION 3.21)
project(vtx_elec_output_helper_build LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  add_compile_options(-O3 -Wall -Wextra -Werror)
  if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64)$")
    add_compile_options(-march=x86-64-v3)
  else()
    message(WARNING "非 x86_64 平台：跳过 -march=x86-64-v3")
  endif()
endif()

# ---- #111 源码复用面（只读引用）----
set(CEF_SRC "${CMAKE_CURRENT_SOURCE_DIR}/../../gfx_cef_worker/src")
set(VTX_THIRD_PARTY "${CMAKE_CURRENT_SOURCE_DIR}/../../gfx_cef_worker/third_party")
if(NOT EXISTS "${CEF_SRC}/pixel_ops.cpp")
  message(FATAL_ERROR "未找到 gfx_cef_worker 源码（${CEF_SRC}）——worktree 布局异常")
endif()
set(SPNG_SOURCES "${VTX_THIRD_PARTY}/spng/spng.c")
set(FPNG_SOURCES "${VTX_THIRD_PARTY}/fpng/fpng.cpp")
set_source_files_properties(${SPNG_SOURCES} PROPERTIES COMPILE_OPTIONS "-w")
set_source_files_properties(${FPNG_SOURCES}
                            PROPERTIES COMPILE_OPTIONS "-w;-msse4.1;-mpclmul")

set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")

# ---- vtx_elec_output_helper（输出桥原生编码助手）----
add_executable(vtx_elec_output_helper
               src/canary_v2.cpp
               src/output_helper.cpp
               ${CEF_SRC}/pixel_ops.cpp
               ${CEF_SRC}/png_writer.cpp
               ${SPNG_SOURCES} ${FPNG_SOURCES})
target_include_directories(vtx_elec_output_helper PRIVATE
                           src "${CEF_SRC}"
                           "${VTX_THIRD_PARTY}/spng" "${VTX_THIRD_PARTY}/fpng")
find_package(Threads REQUIRED)
find_package(ZLIB REQUIRED)
target_link_libraries(vtx_elec_output_helper PRIVATE Threads::Threads ZLIB::ZLIB)

# ---- 可选：把 #111 的 pixel_ops_selftest 一并编进本目录（同样只读引用源码），
#      供 #113 在同一构建面复核 #111 算子穷举/编码基准。----
add_executable(pixel_ops_selftest_elec
               ${CEF_SRC}/pixel_ops_selftest.cpp
               ${CEF_SRC}/pixel_ops.cpp
               ${CEF_SRC}/png_writer.cpp
               ${SPNG_SOURCES} ${FPNG_SOURCES})
target_include_directories(pixel_ops_selftest_elec PRIVATE
                           "${CEF_SRC}"
                           "${VTX_THIRD_PARTY}/spng" "${VTX_THIRD_PARTY}/fpng")
target_link_libraries(pixel_ops_selftest_elec PRIVATE Threads::Threads ZLIB::ZLIB)
