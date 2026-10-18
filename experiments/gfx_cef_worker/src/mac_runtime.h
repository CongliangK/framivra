#pragma once
namespace vtx_cef {
class MacRuntime {
 public:
  MacRuntime(int argc, char** argv);
  ~MacRuntime();
 private:
  void* pool_;
  void* loader_;
};
}
