// Native renderer observation; no page-visible reporting API.
#ifndef VTX_CEF_JS_ERROR_OBSERVER_H_
#define VTX_CEF_JS_ERROR_OBSERVER_H_
#include <cstdint>
#include <string>
#include "js_checkpoint.hpp"
#include "include/cef_render_process_handler.h"
namespace vtx_cef {
struct RunState;
struct PumpDriver;
CefRefPtr<CefRenderProcessHandler> MakeJsErrorObserver();
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
