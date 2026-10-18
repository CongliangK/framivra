#include "mac_runtime.h"
#import <Cocoa/Cocoa.h>
#include "include/cef_application_mac.h"
#include "include/wrapper/cef_library_loader.h"
#include <cstdlib>
#include <cstring>
@interface VtxApplication : NSApplication<CefAppProtocol> {
  BOOL handling_;
}
@end
@implementation VtxApplication
- (BOOL)isHandlingSendEvent { return handling_; }
- (void)setHandlingSendEvent:(BOOL)value { handling_ = value; }
- (void)sendEvent:(NSEvent*)event {
  CefScopedSendingEvent scope;
  [super sendEvent:event];
}
@end
namespace vtx_cef {
MacRuntime::MacRuntime(int argc, char** argv) {
  pool_ = [[NSAutoreleasePool alloc] init];
  auto* loader = new CefScopedLibraryLoader;
  loader_ = loader;
  bool helper = false;
  for (int i = 1; i < argc; ++i) {
    if (std::strncmp(argv[i], "--type=", 7) == 0) helper = true;
  }
  if (!(helper ? loader->LoadInHelper() : loader->LoadInMain())) std::exit(1);
  if (!helper) {
    [VtxApplication sharedApplication];
    [NSApp setActivationPolicy:NSApplicationActivationPolicyProhibited];
  }
}
MacRuntime::~MacRuntime() {
  delete static_cast<CefScopedLibraryLoader*>(loader_);
  [static_cast<NSAutoreleasePool*>(pool_) drain];
}
}
