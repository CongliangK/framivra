// Native renderer observation; no page-visible reporting API.
#ifndef VTX_CEF_JS_ERROR_OBSERVER_H_
#define VTX_CEF_JS_ERROR_OBSERVER_H_
#include <cstdint>
#include <string>
#include "include/cef_render_process_handler.h"
namespace vtx_cef {
struct RunState;
struct PumpDriver;
CefRefPtr<CefRenderProcessHandler> MakeJsErrorObserver();
struct JsErrorGate {
  std::string expected_url;
  std::string token;
  std::string frame_id;
  bool installed = false;
  bool failed = false;
  uint32_t sent = 0;  // lifetime monotonic, including same-URL navigations
  uint32_t done = 0;
};
void PrepareJsDocument(RunState& state, const std::string& url);
// Finite renderer task fence, bounded by the caller's existing deadline.
// This is NOT a guarantee about arbitrary future page work.
bool CheckJsCheckpoint(RunState& state, PumpDriver& pump,
                       CefRefPtr<CefBrowser> browser, uint64_t deadline);
bool ReceiveJsErrorMessage(RunState& state, CefRefPtr<CefBrowser> browser,
                           CefRefPtr<CefFrame> frame,
                           CefRefPtr<CefProcessMessage> message);
}  // namespace vtx_cef
#endif
