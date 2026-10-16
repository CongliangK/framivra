// Trusted renderer-only HTML error observer and finite capture checkpoints.
#ifndef VTX_CEF_JS_ERROR_OBSERVER_H_
#define VTX_CEF_JS_ERROR_OBSERVER_H_

#include <cstdint>
#include <string>
#include "include/cef_render_process_handler.h"

namespace vtx_cef {
struct RunState;
struct PumpDriver;

// No V8 function or native handler is installed on a page-visible object.
CefRefPtr<CefRenderProcessHandler> MakeJsErrorObserver();

struct JsErrorGate {
  std::string expected_url;
  std::string token;
  std::string frame_id;
  bool installed = false;
  bool failed = false;
  uint32_t sent = 0;  // lifetime monotonic: never reused across navigations
  uint32_t done = 0;
};

void PrepareJsDocument(RunState& state, const std::string& url);
// A renderer task fence, not a claim that arbitrary future page work is safe.
// Its deadline belongs to the caller's existing load/ready/paint budget.
bool CheckJsCheckpoint(RunState& state, PumpDriver& pump,
                       CefRefPtr<CefBrowser> browser, uint64_t deadline);
bool ReceiveJsErrorMessage(RunState& state, CefRefPtr<CefBrowser> browser,
                           CefRefPtr<CefFrame> frame,
                           CefRefPtr<CefProcessMessage> message);
}  // namespace vtx_cef
#endif
