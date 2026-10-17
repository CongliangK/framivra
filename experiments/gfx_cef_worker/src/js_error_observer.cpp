#include "js_error_observer.h"
#include "app_handler.h"
#include "shm_ring.hpp"
#include "include/cef_parser.h"
#include "include/cef_process_message.h"
#include "include/cef_task.h"
#include "include/cef_v8.h"
#include <cstdio>
#include <map>
#include <memory>

namespace vtx_cef {
namespace {
constexpr char kCheck[] = "vtx.native.js.check";
constexpr char kReply[] = "vtx.native.js.reply";
constexpr char kError[] = "vtx.native.js.error";
struct Document {
  CefRefPtr<CefV8Context> context;
  CefRefPtr<CefFrame> frame;
  std::string url, token;
  bool active = true, installed = false, failed = false;
  std::string detail;
};
void Send(CefRefPtr<CefFrame> frame, const std::shared_ptr<Document>& d,
          const char* name, int seq, bool identity) {
  auto msg = CefProcessMessage::Create(name);
  auto a = msg->GetArgumentList();
  a->SetInt(0, seq);
  a->SetString(1, d ? d->token : "");
  a->SetString(2, d ? d->url : "");
  a->SetBool(3, identity && d && d->installed);
  a->SetBool(4, !identity || !d || d->failed);
  a->SetString(5, d ? d->detail : "observer_context_missing");
  frame->SendProcessMessage(PID_BROWSER, msg);
}
class Report final : public CefV8Handler {
 public:
  explicit Report(const std::shared_ptr<Document>& d) : document_(d) {}
  bool Execute(const CefString&, CefRefPtr<CefV8Value>,
               const CefV8ValueList& args, CefRefPtr<CefV8Value>&,
               CefString&) override {
    auto d = document_.lock();
    auto current = CefV8Context::GetCurrentContext();
    if (!d || !d->active || !current || !current->IsSame(d->context) ||
        !d->frame->IsMain() || d->frame->GetURL().ToString() != d->url ||
        args.size() != 3 || !args[0]->IsObject() || !args[1]->IsString() || !args[2]->IsString()) return true;
    // isTrusted is the browser's unforgeable own event property. The closure
    // never exposes this native function; dispatchEvent cannot fabricate trust.
    auto trusted = args[0]->GetValue("isTrusted");
    auto type = args[1];
    if (!trusted || !trusted->IsBool() || !trusted->GetBoolValue() ||
        !type || !type->IsString()) return true;
    const auto kind = type->GetStringValue().ToString();
    if (kind != "error" && kind != "unhandledrejection") return true;
    // Resource load events are not JavaScript ErrorEvents.
    auto message = args[2];
    d->failed = true;
    d->detail = kind;
    if (kind == "error") d->detail += ": " + message->GetStringValue().ToString().substr(0, 512);
    // Do not coerce rejection reasons: a page-owned getter/toString may throw.
    Send(d->frame, d, kError, 0, true);
    return true;
  }
 private:
  std::weak_ptr<Document> document_;
  IMPLEMENT_REFCOUNTING(Report);
};
// Two native renderer turns, independent of page timers/MessageChannel/console.
// Retain only native document identity; release invalidates all queued fences.
class Fence final : public CefTask {
 public:
  Fence(std::shared_ptr<Document> d, CefRefPtr<CefFrame> frame,
        std::string url, int seq, bool second = false)
      : d_(std::move(d)), frame_(frame), url_(std::move(url)),
        seq_(seq), second_(second) {}
  void Execute() override {
    if (!second_) {
      CefPostTask(TID_RENDERER, new Fence(d_, frame_, url_, seq_, true));
      return;
    }
    const bool identity = d_ && d_->active && d_->context && d_->context->IsValid() &&
        d_->frame->GetIdentifier() == frame_->GetIdentifier() && frame_->IsMain() &&
        d_->url == url_ && frame_->GetURL().ToString() == url_;
    Send(frame_, d_, kReply, seq_, identity);
  }
 private:
  std::shared_ptr<Document> d_;
  CefRefPtr<CefFrame> frame_;
  std::string url_;
  int seq_;
  bool second_;
  IMPLEMENT_REFCOUNTING(Fence);
};
class Observer final : public CefRenderProcessHandler {
 public:
  void OnContextCreated(CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame,
                        CefRefPtr<CefV8Context> context) override {
    if (!frame->IsMain()) return;
    auto d = std::make_shared<Document>();
    d->frame = frame; d->context = context; d->url = frame->GetURL().ToString();
    d->token = std::to_string(browser->GetIdentifier()) + ":" + std::to_string(++serial_);
    auto& previous = documents_[browser->GetIdentifier()];
    if (previous) previous->active = false;
    previous = d;
    CefRefPtr<CefV8Value> factory;
    CefRefPtr<CefV8Exception> exception;
    // OnContextCreated precedes document inline scripts. Register capture
    // listeners first, retaining pristine addEventListener and native report
    // ONLY in lexical closures. No global property/extension/Node bridge.
    const bool entered = context->Enter();
    const bool evaluated = entered && context->Eval(R"JS((function(report){
      'use strict';
      const add = EventTarget.prototype.addEventListener;
      const apply = Reflect.apply;
      const message = Object.getOwnPropertyDescriptor(ErrorEvent.prototype, 'message').get;
      apply(add, window, ['error', function(e){
        // Intrinsic getter rejects resource events without executing page code.
        let text; try { text = apply(message, e, []); } catch (_) { return; }
        report(e, 'error', text);
      }, true]);
      apply(add, window, ['unhandledrejection', function(e){
        report(e, 'unhandledrejection', '');
      }, true]);
      return true;
    }))JS", "vtx_native_js_observer", 1, factory, exception);
    if (evaluated && factory && factory->IsFunction()) {
      CefV8ValueList args{CefV8Value::CreateFunction("nativeReport", new Report(d))};
      auto result = factory->ExecuteFunctionWithContext(context, context->GetGlobal(), args);
      d->installed = result && result->IsBool() && result->GetBoolValue();
      if (!d->installed && factory->HasException()) exception = factory->GetException();
    }
    if (!d->installed) {
      d->failed = true;
      d->detail = "observer_install_failed entered=" + std::to_string(entered) +
          " evaluated=" + std::to_string(evaluated) +
          " factory=" + std::to_string(factory && factory->IsFunction());
      if (exception) d->detail += ": " + exception->GetMessage().ToString();
    }
    if (entered) context->Exit();
  }
  void OnContextReleased(CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame,
                         CefRefPtr<CefV8Context> context) override {
    if (!frame->IsMain()) return;
    auto it = documents_.find(browser->GetIdentifier());
    if (it != documents_.end() && it->second->context->IsSame(context)) {
      it->second->active = false;
      it->second->context = nullptr;
      documents_.erase(it);
    }
  }
  bool OnProcessMessageReceived(CefRefPtr<CefBrowser> browser,
       CefRefPtr<CefFrame> frame, CefProcessId source,
       CefRefPtr<CefProcessMessage> message) override {
    if (source != PID_BROWSER || message->GetName() != kCheck) return false;
    auto a = message->GetArgumentList();
    if (!frame->IsMain() || a->GetSize() != 2) return true;
    auto it = documents_.find(browser->GetIdentifier());
    auto d = it == documents_.end() ? nullptr : it->second;
    CefPostTask(TID_RENDERER, new Fence(d, frame, a->GetString(1).ToString(), a->GetInt(0)));
    return true;
  }
 private:
  uint64_t serial_ = 0;
  std::map<int, std::shared_ptr<Document>> documents_;
  IMPLEMENT_REFCOUNTING(Observer);
};
}  // namespace
CefRefPtr<CefRenderProcessHandler> MakeJsErrorObserver() { return new Observer; }
void PrepareJsDocument(RunState& state, const std::string& url) {
  const auto sent = state.js_error.sent;
  state.js_error = JsErrorGate{};
  state.js_error.sent = sent;
  state.js_error.expected_url = url;
  state.last_error.clear();
  state.js_paint_pending = false;
}
bool ReceiveJsErrorMessage(RunState& state, CefRefPtr<CefBrowser> browser,
    CefRefPtr<CefFrame> frame, CefRefPtr<CefProcessMessage> message) {
  const auto name = message->GetName().ToString();
  if (name != kReply && name != kError) return false;
  auto& g = state.js_error;
  auto a = message->GetArgumentList();
  if (!frame || !frame->IsMain() || !browser->IsSame(state.browser ? state.browser : browser) ||
      a->GetSize() != 6 || g.expected_url.empty()) return true;
  if (a->GetString(2).ToString() != g.expected_url ||
      frame->GetURL().ToString() != g.expected_url) return true;
  if (name == kReply) {
    if (static_cast<uint32_t>(a->GetInt(0)) != g.sent) return true;
    if (!g.token.empty() && (g.token != a->GetString(1).ToString() ||
        g.frame_id != frame->GetIdentifier().ToString())) g.failed = true;
    g.token = a->GetString(1).ToString();
    g.frame_id = frame->GetIdentifier().ToString();
    g.installed = a->GetBool(3);
    g.done = g.sent;
    g.failed = g.failed || !g.installed || a->GetBool(4);
  } else {
    // Pre-binding errors are included in the first fence's sticky snapshot.
    if (g.token.empty() || g.token != a->GetString(1).ToString() ||
        g.frame_id != frame->GetIdentifier().ToString()) return true;
    g.failed = true;
  }
  if (g.failed) {
    state.last_error = "JS_ERR " + a->GetString(5).ToString();
    std::fprintf(stderr, "vtx_cef_worker: %s @ %s context=%s\n",
                 state.last_error.c_str(), g.expected_url.c_str(), g.token.c_str());
  }
  return true;
}
bool CheckJsCheckpoint(RunState& state, PumpDriver& pump,
    CefRefPtr<CefBrowser> browser, uint64_t deadline) {
  auto& g = state.js_error;
  if (g.failed || g.expected_url.empty() ||
      JsCheckpointRemainingMs(NowUs(), deadline) <= 0) return false;
  // Signed int is CEF's transport field. Never reuse a sequence on overflow.
  if (g.sent >= 0x7fffffffU) { g.failed = true; return false; }
  ++g.sent;
  auto message = CefProcessMessage::Create(kCheck);
  auto a = message->GetArgumentList();
  a->SetInt(0, static_cast<int>(g.sent)); a->SetString(1, g.expected_url);
  browser->GetMainFrame()->SendProcessMessage(PID_RENDERER, message);
  const auto remaining = JsCheckpointRemainingMs(NowUs(), deadline);
  const bool completed = remaining > 0 &&
      pump.Until([&] { return g.failed || g.done == g.sent; }, remaining);
  const auto result = JsCheckpointResult(g, NowUs(), deadline);
  if (completed && result == JsCheckpointOutcome::kPassed) return true;
  // No authenticated observer error: this is a protocol budget failure, NOT a
  // JS exception. In particular a stale paint can consume the final fraction
  // of a millisecond while entering its fence. Never poison sticky JS state.
  if (!g.failed) state.last_error = "TIMEOUT observer_checkpoint_budget";
  return false;
}
}  // namespace vtx_cef
