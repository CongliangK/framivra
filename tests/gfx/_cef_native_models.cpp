// Independent CEF-free regression models; compiled by the exact contract suite.
// Each model executes production code, with only the browser/event transport
// substituted. No native Chromium rasterization or performance result is implied.
#if defined(VTX_MODEL_ORCHESTRATION)
// === Paired browser orchestration: actual serve/parser/OnPaint + fake CEF. ===
#include "frame_sync_machine.hpp"
#include "js_checkpoint.hpp"
#include "passive_snapshot.hpp"
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <functional>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <sstream>
#include <vector>
namespace vtx_cef {
  namespace fsync = vtx_cef::fsync;
  template<class T> struct CefRefPtr : std::shared_ptr<T>{
    using std::shared_ptr<T>::shared_ptr;
    CefRefPtr(T*p):std::shared_ptr<T>(p){
    }
    CefRefPtr()=default;
  };
  struct CefMessageArgs {
    void SetInt(int, int) {}
    void SetString(int, const std::string&) {}
  };
  struct CefProcessMessage {
    CefMessageArgs args;
    static CefRefPtr<CefProcessMessage> Create(const char*) { return new CefProcessMessage; }
    CefMessageArgs* GetArgumentList() { return &args; }
  };
  constexpr int PID_RENDERER = 0;
  constexpr char kCheck[] = "fake-check-transport";
  struct CefBrowser;
  struct WorkerClient;
  struct RunState;
  struct PumpDriver;
  static uint64_t tick=100;
  uint64_t NowUs(){
    return tick;
  }
  static std::vector<std::function<void()>> tasks;
  static std::vector<CefRefPtr<CefBrowser>> browsers;
  static std::vector<std::string> trace;
  static std::function<void(RunState&)> fence_hook;
  static bool withhold_unsigned_second=false, stale_same_url_load=false, wrong_unsigned_ready=false, unsigned_content_mismatch=false;
  static bool fail_context_creation = false, fail_browser_creation = false;
  static int create_delay_turns = 0, context_creations = 0, browser_creations = 0;
  static int contexts_alive = 0;
  static int transient_unsigned_mismatch_paints = 0;
  static bool close_before_adoption = false;
  static bool drop_closed_fence_reply = false;
  static bool unsigned_cross_batch_pattern = false, unsigned_pattern_stabilizes = false;
  static int active_unsigned_pattern_paints = 0, unsigned_four_sample_mask = -1;
  static int check_count=0;
  void ck(bool x,const char*s){
    ++check_count;
    if(!x)throw std::runtime_error(s);
  }
  struct Options {
    int width=32,height=24,capture_fps=120;
  };
  struct ServeOptions {
    Options cef;
    std::string sync_mode="n",sync_canary="exact",sync_fault="none",url_template="file:///page?f={f}";
    std::string reference_context = "shared";
    int frame_budget_ms=20,sync_ready_timeout_ms=20,sync_pump_timeout_ms=20;
  };
  constexpr uint16_t kStatusOk=0,kStatusTimeout=1,kStatusSizeMismatch=2,kStatusJsErr=3,kStatusDuplicate=4;
  constexpr int PET_VIEW=0;
  struct RunState {
    std::mutex mu;
    std::atomic<bool> loaded{false}
    ,closed{false};
    std::atomic<uint64_t> paints{0}
    ,load_starts{0};
    CefRefPtr<CefBrowser> browser;
    JsErrorGate js_error;
    std::string last_error;
    bool passive_snapshot_enabled = true;
    uint64_t passive_snapshot_copied_paints = 0;
    uint64_t passive_snapshot_copied_bytes = 0;
    uint64_t passive_snapshot_dropped_paints = 0;
    int SetPassiveSnapshotDemand(bool enabled) {
      std::lock_guard<std::mutex> lock(mu);
      return SetPassiveSnapshotDemandLocked(*this, enabled);
    }
    std::vector<std::string> sync_ready_queue;
    std::vector<uint8_t> buffer,js_paint_candidate;
    int width=32,height=24,js_paint_w=0,js_paint_h=0;
    fsync::FrameSyncStateMachine*sync_machine=nullptr;
    uint8_t*sync_dst=nullptr;
    bool sync_captured=false,sync_size_mismatch=false,sync_capture_armed=false,js_paint_pending=false;
    uint32_t sync_actual_w=0,sync_actual_h=0;
    fsync::PaintVerdict sync_last_verdict;
  };
  struct WorkerClient {
    std::shared_ptr<RunState> owned;
    RunState*s;
    int requested_capture_fps;
    WorkerClient(const Options* opt,std::shared_ptr<RunState> st):owned(st),s(st.get()),requested_capture_fps(opt->capture_fps){
    };
  };
  struct CefWindowInfo {
    bool shared_texture_enabled=false,external_begin_frame_enabled=false;
    void SetAsWindowless(int){
    }
  };
  struct CefBrowserSettings {
    int background_color=0,windowless_frame_rate=0;
  };
  struct String {
    std::string s;
    std::string ToString()const{
      return s;
    }
  };
  struct FakeFrame {
    CefBrowser*b;
    void LoadURL(const std::string&);
    void ExecuteJavaScript(const std::string&,const std::string&,int);
    String GetURL();
    void SendProcessMessage(int, CefRefPtr<CefProcessMessage>);
  };
  struct FakeHost {
    CefBrowser*b;
    void Invalidate(int){
    }
    void SendExternalBeginFrame();
    void CloseBrowser(bool);
  };
  struct CefBrowser {
    RunState*s;
    CefRefPtr<WorkerClient> client;
    std::string name,url="about:blank";
    bool loading=false,relocated=false,ready=false;
    int f=0;
    uint32_t gen=0,req=0;
    int navigation_count=0,paint_requests=0,postready_paints=0;
    std::string fault="none";
    FakeFrame frame{this};
    FakeHost host{this};
    CefBrowser(RunState*st,std::string n):s(st),name(n){
    };
    FakeFrame*GetMainFrame(){
      ck(!s->closed, "GetMainFrame after OnBeforeClose");
      return &frame;
    }
    FakeHost*GetHost(){
      ck(!s->closed, "GetHost after OnBeforeClose");
      return &host;
    }
    bool IsLoading(){
      ck(!s->closed, "IsLoading after OnBeforeClose");
      return loading;
    }
  };
  struct CefRequestContextSettings {};
  struct CefRequestContext {
    CefRequestContext() { ++contexts_alive; }
    ~CefRequestContext() { --contexts_alive; }
    static CefRefPtr<CefRequestContext> CreateContext(
        const CefRequestContextSettings&, std::nullptr_t) {
      ++context_creations;
      if (fail_context_creation) return nullptr;
      return new CefRequestContext;
    }
  };
  void CompleteCreation(CefRefPtr<WorkerClient> client, std::string url,
                        CefRefPtr<CefRequestContext> context, int turns) {
    if (turns > 0) {
      tasks.push_back([client, url, context, turns] {
        CompleteCreation(client, url, context, turns - 1);
      });
      return;
    }
    auto browser = CefRefPtr<CefBrowser>(new CefBrowser(client->s, "unsigned"));
    browser->client = client;
    browsers.push_back(browser);
    client->s->browser = browser;  // Actual OnAfterCreated ownership handoff.
    trace.push_back("created:unsigned");
    if (close_before_adoption) {
      // A terminal callback can occur in the same native pump batch, before
      // ExactBaselineCapture has ever retained the OnAfterCreated handle.
      client->s->closed = true;
      client->s->browser = nullptr;
      trace.push_back("closed-before-adopt:unsigned");
      return;
    }
    browser->GetMainFrame()->LoadURL(url);
  }
  struct CefBrowserHost {
    static bool CreateBrowser(CefWindowInfo&, CefRefPtr<WorkerClient> client,
                              const std::string& url, CefBrowserSettings& settings,
                              std::nullptr_t, CefRefPtr<CefRequestContext> context) {
      ++browser_creations;
      ck(settings.background_color == 0, "unsigned alpha settings");
      ck(settings.windowless_frame_rate == client->requested_capture_fps,
         "unsigned requested capture FPS reaches CEF settings");
      ck(url != "about:blank", "async create navigates directly to target");
      trace.push_back("create:unsigned");
      if (fail_browser_creation) return false;
      const int delay = create_delay_turns;
      tasks.push_back([client, url, context, delay] {
        CompleteCreation(client, url, context, delay);
      });
      return true;
    }
  };
  struct PumpDriver {
    RunState*state;
    void Once(){
      tick+=100;
      auto batch=std::move(tasks);
      tasks.clear();
      for (auto&t:batch)t(); }
    void WaitForWork(uint64_t deadline){
      tick=std::min(deadline,tick+100); }
    bool Until(const std::function<bool()>&done,int ms){
      const auto deadline=tick+ms*1000ull;
      if (done()) return true;
      while (tick < deadline) {
        Once();
        if (done()) return true;
      }
      return false;
    }
  };
  void PrepareJsDocument(RunState&s,const std::string&url){
    s.js_error=JsErrorGate{
    };
    s.js_error.expected_url=url;
    s.js_paint_pending=false;
  }
  void FakeFrame::SendProcessMessage(int process, CefRefPtr<CefProcessMessage>) {
    ck(process == PID_RENDERER, "native observer message targets renderer");
    trace.push_back("fence:" + b->name);
    auto* browser = b;
    tasks.push_back([browser] {
      auto& state = *browser->s;
      if (fence_hook) fence_hook(state);
      if (drop_closed_fence_reply && state.closed) return;
      // Only the reply transport is simulated. Actual CheckJsCheckpoint below
      // owns closed-state, deadline, sequence and sticky-error acceptance.
      state.js_error.done = state.js_error.sent;
      state.js_error.installed = true;
      state.js_error.token = "native-model-token";
      state.js_error.frame_id = browser->name;
    });
  }
// PRODUCTION_JS_CHECKPOINT
  std::string BuildSyncBridgeJs(uint32_t gen,uint32_t req,int f,bool mode_n,bool,bool,const std::string&fault,bool baseline=false,bool=false){
    return "bridge "+std::to_string(gen)+" "+std::to_string(req)+" "+std::to_string(f)+" "+std::to_string(mode_n)+" "+std::to_string(baseline)+" "+fault;
  }
  static std::vector<uint8_t> Original(int f){
    std::vector<uint8_t>p(32*24*4);
    for (size_t i=0; i<p.size(); i+=4){
      p[i]=static_cast<uint8_t>((i+f)%127);
      p[i+1]=static_cast<uint8_t>((i/4+f)%127);
      p[i+2]=static_cast<uint8_t>((i/17+f)%127);
      p[i+3]=127;
    }
    return p;
  }
  static void Draw(std::vector<uint8_t>&p,int f,uint32_t req,bool phase){
    for (int y=0; y<8; ++y){
      uint32_t t=(y<4?f:y<6?req&65535:req>>16)^(phase?65535:0);
      uint32_t hi=t>>8,lo=t&255;
      for (int x=0; x<8; ++x){
        int px=(phase?24:0)+x;
        auto q=&p[(y*32+px)*4];
        q[0]=(hi^lo^0xa5)&255;
        q[1]=lo;
        q[2]=hi;
        q[3]=255;
        px=(phase?0:24)+x;
        q=&p[((16+y)*32+px)*4];
        q[0]=(16*lo+22*hi+0xc3)&255;
        q[1]=hi;
        q[2]=lo;
        q[3]=255;
      }
    }
  }
  void SimPaint(RunState*,const std::vector<uint8_t>&);
  void FakeHost::SendExternalBeginFrame(){
    ++b->paint_requests;
    auto*browser=b;
    tasks.push_back([browser]{
      auto&s=*browser->s;
auto p=Original(browser->f);
if ((unsigned_cross_batch_pattern || unsigned_four_sample_mask >= 0) &&
    browser->name == "unsigned" && browser->ready && s.passive_snapshot_enabled) {
  const int sample = ++active_unsigned_pattern_paints;
  // Collected pairs M,E then E,M: the second pair's latest mismatch must be
  // examined, not skipped by accepting the E using the previous pair's streak.
  const bool mismatch = unsigned_four_sample_mask >= 0
      ? (sample > 4 || (unsigned_four_sample_mask & (1 << (sample - 1))) != 0)
      : (sample == 1 || sample == 4 || (!unsigned_pattern_stabilizes && sample > 4));
  if (mismatch) p[0] ^= 1;
}

if (browser->name == "unsigned" &&
        (unsigned_content_mismatch || (browser->ready && transient_unsigned_mismatch_paints-- > 0)))
      p[0] ^= 1;
if(browser->ready&&browser->name=="unsigned"&&withhold_unsigned_second&&browser->postready_paints++>0)return;
if(browser->name=="signed"&&browser->ready)Draw(p,browser->f,browser->req,browser->relocated);
SimPaint(&s,p);
    }
    );
  }
  void FakeHost::CloseBrowser(bool){
    ck(!b->s->closed.load(), "no CloseBrowser after OnBeforeClose");
    auto*browser=b;
    trace.push_back("close:"+b->name);
    tasks.push_back([browser]{
      browser->s->closed=true;
browser->s->browser=nullptr;
    }
    );
  }
  String FakeFrame::GetURL(){
    return {
      b->url
    };
  }
  void FakeFrame::LoadURL(const std::string&url){
    trace.push_back("load:"+b->name);
    ++b->navigation_count;
    b->loading=true;
    b->ready=false;
    b->relocated=false;
    auto*browser=b;
    auto commit=[browser,url]{
      browser->url=url;
      browser->loading=false;
      ++browser->s->load_starts;
      browser->s->loaded=true;
      trace.push_back("commit:"+browser->name);
    };
    if(stale_same_url_load&&browser->navigation_count>1)tasks.push_back([browser,commit]{
      browser->loading=false;
browser->s->loaded=true;
tasks.push_back(commit);
    }
    );
    else tasks.push_back(commit);
  }
  void FakeFrame::ExecuteJavaScript(const std::string&js,const std::string&name,int){
    trace.push_back("js:"+b->name+":"+name);
    ck(b->s->load_starts.load()==static_cast<uint64_t>(b->navigation_count),"stale load callback cannot inject bridge");
    auto*browser=b;
    if(js=="window.__VTX_RELOCATE();"){
      tasks.push_back([browser]{
        browser->relocated=true;
      }
      );
      return;
    }
    std::istringstream in(js);
    std::string tag;
    bool n,baseline;
    in>>tag>>b->gen>>b->req>>b->f>>n>>baseline>>b->fault;
    ck(tag=="bridge","only bridge script expected");
    tasks.push_back([browser,baseline]{
      if(browser->fault=="no_ready")return;
auto event=[&](const char*phase){
        return std::string("{\"phase\":\"")+phase+"\",\"gen\":"+std::to_string(browser->gen)+",\"req\":"+std::to_string(browser->req+(baseline&&wrong_unsigned_ready?1:0))+",\"f\":"+std::to_string(browser->f)+"}";
      };
browser->ready=true;
browser->relocated=false;
browser->postready_paints=0;
if(!baseline)browser->s->sync_ready_queue.push_back(event("assets_ready"));
browser->s->sync_ready_queue.push_back(event(baseline?"baseline_ready":"canary_set"));
    }
    );
  }

// PRODUCTION_NATIVE_ORCHESTRATION

struct Fixture {
  RunState state;
  PumpDriver pump{&state};
  CefRefPtr<CefBrowser> signed_browser;
  fsync::FrameSyncStateMachine machine;
  ExactBaselineCapture baseline;
  ServeOptions options;
  std::vector<uint8_t> dst=std::vector<uint8_t>(32*24*4,0xcc);
  Fixture(bool resident=false){
    tasks.clear();
    browsers.clear();
    trace.clear();
    fence_hook=nullptr;
    withhold_unsigned_second=stale_same_url_load=wrong_unsigned_ready=unsigned_content_mismatch=false;
    fail_context_creation = fail_browser_creation = false;
    create_delay_turns = context_creations = browser_creations = 0;
    transient_unsigned_mismatch_paints = 0;
    close_before_adoption = false;
    drop_closed_fence_reply = false;
    unsigned_cross_batch_pattern = unsigned_pattern_stabilizes = false;
    active_unsigned_pattern_paints = 0;
    unsigned_four_sample_mask = -1;
    ck(contexts_alive == 0, "prior fixture released isolated context");
    signed_browser=new CefBrowser(&state,"signed");
    browsers.push_back(signed_browser);
    state.browser=signed_browser;
    state.sync_machine=&machine;
    if(resident){
      options.sync_mode="p";
      options.url_template="file:///page?f=0";
      PrepareJsDocument(state,options.url_template);
      signed_browser->GetMainFrame()->LoadURL(options.url_template);
      pump.Once();
    }
  }
  SyncProduceResult run(uint32_t req,int frame){
    auto r=ProduceFrameSync(state,pump,signed_browser,machine,baseline,options,options.sync_mode=="p"?1:req,req,frame,32,24,dst.data());
    if(r.status==0)machine.Reset(NowUs(),"harness_request_done");
    return r;
  }
  void close(){
    ck(baseline.Close(pump),"unsigned close");
    if (!state.closed) signed_browser->GetHost()->CloseBrowser(true);
    ck(pump.Until([&]{
      return state.closed.load();
    }
    ,5),"signed close");
  }
};
int tests(){
  {
    Fixture x;
    auto r=x.run(1,7);
    ck(r.status==0&&r.canary_frame==7,"N successful paired proof");
    ck(x.dst==Original(7),"N exact output");
    ck(trace[0]=="create:unsigned"&&trace[1]=="load:signed","N navigation issued together before pump");
    ck(browsers.size()==2,"only paired browsers");
    ck(browsers[0]->navigation_count==1&&browsers[1]->navigation_count==1,"one navigation per browser");
    stale_same_url_load=true;
    r=x.run(2,7);
    ck(r.status==0,"same frame new req succeeds");
    ck(browsers.size()==2&&browsers[0]->navigation_count==2&&browsers[1]->navigation_count==2,"N reuse browsers but fresh documents");
    x.close();
  }
  {
    Fixture x(true);
    auto r=x.run(1,8);
    ck(r.status==0&&x.dst==Original(8),"P first proof");
    r=x.run(2,9);
    ck(r.status==0&&x.dst==Original(9),"P next hook proof");
    ck(browsers[0]->navigation_count==1&&browsers[1]->navigation_count==1,"P navigate once each");
    x.close();
  }
  {
    Fixture x(true);
    x.options.sync_fault="no_ready";
    auto r=x.run(1,2);
    ck(r.status==kStatusTimeout,"P failed readiness");
    x.options.sync_fault="none";
    for (auto&b:browsers){
      b->ready=true; b->s->sync_ready_queue.push_back("{\"phase\":\"baseline_ready\",\"gen\":1,\"req\":1,\"f\":2}"); }
    r=x.run(2,3);
    ck(r.status==kStatusTimeout,"P poisoned must restart");
    ck(std::string(x.baseline.failure_reason())=="resident_request_previously_failed","P poison reason");
    ck(x.machine.counters().requests_accepted==0,"P fail cannot accept");
    x.close();
  }
  {
    Fixture x;
    withhold_unsigned_second=true;
    auto r=x.run(1,5);
    ck(r.status==kStatusTimeout,"one unsigned sample insufficient");
    ck(x.machine.counters().requests_accepted==0,"one unsigned no S5");
    ck(std::all_of(x.dst.begin(),x.dst.end(),[](auto c){
      return c==0xcc;
    }
    ),"one unsigned untouched dst");
    x.close();
  }
  {
    Fixture x;
    fence_hook=[&](RunState&s){
      if(x.machine.needs_baseline_veto()){
        s.buffer.assign(32*24*4,0xee);
        ++s.paints;
      }
    };
    auto r=x.run(1,6);
    ck(r.status==0&&x.dst==Original(6),"fence cannot mutate frozen unsigned samples or signed candidate");
    x.close();
  }
  {
    Fixture x;
    fence_hook=[&](RunState&s){
      if(x.machine.needs_baseline_veto()&&&s!=&x.state)s.js_error.failed=true;
    };
    auto r=x.run(1,6);
    ck(r.status==kStatusJsErr,"unsigned postpaint error rejected");
    ck(x.machine.counters().requests_accepted==0,"unsigned error no S5");
    x.close();
  }
  {
    Fixture x;
    fence_hook=[&](RunState&s){
      if(x.machine.needs_baseline_veto() && &s==&x.state && browsers.size()>1)browsers[1]->s->js_error.failed=true;
    };
    auto r=x.run(1,6);
    ck(r.status==kStatusJsErr,"unsigned error during final signed fence rejected");
    ck(x.machine.counters().requests_accepted==0,"cross-browser late error no S5");
    x.close();
  }
  {
    Fixture x;
    wrong_unsigned_ready=true;
    auto r=x.run(1,6);
    ck(r.status==kStatusTimeout,"foreign baseline readiness ignored");
    ck(x.machine.counters().requests_accepted==0,"foreign baseline no S5");
    x.close();
  }
  {
    Fixture x;
    unsigned_content_mismatch=true;
    auto r=x.run(1,6);
    ck(r.status==kStatusTimeout,"stable unsigned mismatch never weakens equality");
    ck(x.machine.counters().requests_accepted==0,"stable mismatch no S5");
    ck(std::all_of(x.dst.begin(),x.dst.end(),[](auto c){
      return c==0xcc;
    }
    ),"mismatch untouched dst");
    x.close();
  }
  {
    Fixture x;
    x.options.reference_context = "isolated";
    auto r = x.run(1, 7);
    ck(r.status == kStatusOk && x.dst == Original(7), "isolated exact output");
    ck(context_creations == 1 && contexts_alive == 1, "one private context retained");
    r = x.run(2, 8);
    ck(r.status == kStatusOk && context_creations == 1, "reuse same private context");
    x.close();
    ck(contexts_alive == 0, "context released before shutdown");
  }
  {
    Fixture x;
    x.options.reference_context = "isolated";
    fail_context_creation = true;
    auto r = x.run(1, 7);
    ck(r.status == kStatusTimeout, "isolated context failure is explicit");
    ck(browser_creations == 0 && browsers.size() == 1, "no fallback to shared browser");
    ck(x.machine.counters().requests_accepted == 0, "context failure no S5");
    x.close();
  }
  {
    Fixture x;
    x.options.reference_context = "isolated";
    fail_browser_creation = true;
    auto r = x.run(1, 7);
    ck(r.status == kStatusTimeout && contexts_alive == 1, "created context but browser failed");
    ck(browser_creations == 1 && browsers.size() == 1, "browser failure does not retry shared");
    x.close();
    ck(contexts_alive == 0, "browser create failure releases context before shutdown");
  }
  {
    Fixture x;
    x.options.reference_context = "isolated";
    create_delay_turns = 300;  // More than the ORIGINAL 20 ms load budget.
    const auto begin = NowUs();
    auto r = x.run(1, 7);
    ck(r.status == kStatusTimeout, "late async creation cannot extend load deadline");
    ck(NowUs() - begin < 21000, "creation timeout remains bounded");
    ck(x.machine.counters().requests_accepted == 0, "late creation cannot accept output");
    x.close();  // Creation arrives during close; must still close that browser.
    ck(browsers.size() == 2 && browsers[1]->s->closed, "late-created browser closed");
    ck(contexts_alive == 0, "late-created context released after close");
  }
  {
    Fixture x;
    x.options.reference_context = "isolated";
    create_delay_turns = 100000;  // Never arrives within bounded close budget.
    auto r = x.run(1, 7);
    ck(r.status == kStatusTimeout, "never-created browser request timeout");
    ck(!x.baseline.Close(x.pump), "pending browser creation forbids clean CEF shutdown");
    ck(x.machine.counters().requests_accepted == 0, "pending creation no S5");
    tasks.clear();  // Simulate process teardown; never execute abandoned callbacks.
    x.signed_browser->GetHost()->CloseBrowser(true);
    ck(x.pump.Until([&] { return x.state.closed.load(); }, 5), "signed browser closes independently");
  }
  {
    Fixture x;
    auto r = x.run(1, 7);
    ck(r.status == kStatusOk, "idle gate setup accepted");
    auto& reference = *browsers[1]->s;
    ck(!reference.passive_snapshot_enabled, "two samples disable passive copies");
    const auto reference_copies = reference.passive_snapshot_copied_paints;
    const auto reference_drops = reference.passive_snapshot_dropped_paints;
    const auto signed_copies = x.state.passive_snapshot_copied_paints;
    const auto signed_drops = x.state.passive_snapshot_dropped_paints;
    const auto pixels = Original(7);
    for (int i = 0; i < 25; ++i) {
      SimPaint(&reference, pixels);
      SimPaint(&x.state, pixels);
    }
    ck(reference.passive_snapshot_copied_paints == reference_copies,
       "unsigned idle callbacks cannot copy a full frame");
    ck(reference.passive_snapshot_dropped_paints == reference_drops + 25,
       "unsigned idle callbacks retain actual sequence accounting");
    ck(x.state.passive_snapshot_copied_paints == signed_copies &&
       x.state.passive_snapshot_dropped_paints == signed_drops + 25,
       "signed idle callbacks cannot take fallback full copies");
    ck(reference.buffer.empty() && x.state.buffer.empty(), "idle snapshots never retain stale bytes");
    x.close();
  }
  {
    Fixture x;
    transient_unsigned_mismatch_paints = 2;
    auto r = x.run(1, 7);
    ck(r.status == kStatusOk && x.dst == Original(7), "mismatch rearms and later exact paints accept");
    auto& reference = *browsers[1]->s;
    ck(reference.passive_snapshot_copied_paints >= 4, "retry requires fresh native samples");
    ck(!reference.passive_snapshot_enabled && reference.buffer.empty(),
       "successful retry returns gate to paused state");
    x.close();
  }
  {
    Fixture x;
    x.options.reference_context = "isolated";
    auto r = x.run(1, 7);
    ck(r.status == kStatusOk, "preclosed cleanup setup");
    browsers[1]->s->closed = true;  // OnBeforeClose delivered independently.
    x.close();
    ck(contexts_alive == 0, "preclosed reference releases context without host methods");
  }
  {
    Fixture x;
    x.options.reference_context = "isolated";
    close_before_adoption = true;
    const auto begin = NowUs();
    auto r = x.run(1, 7);
    ck(r.status == kStatusTimeout, "closed-before-adopt request cannot succeed");
    ck(NowUs() - begin < 2000, "terminal callback aborts without load deadline wait");
    ck(browsers.size() == 2 && browsers[1]->s->closed && !browsers[1]->s->browser,
       "closed callback cleared the only pending browser handle");
    x.close();
    ck(contexts_alive == 0, "closed-before-adopt releases private context promptly");
    ck(x.machine.counters().requests_accepted == 0, "closed-before-adopt never S5");
  }
  {
    Fixture x(true);
    x.state.closed = true;
    const auto fences = trace.size();
    ck(!CheckJsCheckpoint(x.state, x.pump, x.signed_browser, NowUs() + 10000),
       "already closed observer cannot start a fence");
    ck(trace.size() == fences, "closed observer sends no browser message");
    x.close();
  }
  for (bool close_signed : {false, true}) {
    Fixture x;
    fence_hook = [&](RunState& state) {
      if ((&state == &x.state) == close_signed) {
        state.closed = true;
        state.browser = nullptr;
      }
    };
    const auto r = x.run(1, 6);
    ck(r.status != kStatusOk, "closure during load fence rejects request");
    ck(x.machine.counters().requests_accepted == 0, "closed load fence never S5");
    ck(std::all_of(x.dst.begin(), x.dst.end(), [](auto c) { return c == 0xcc; }),
       "closed load fence leaves output untouched");
    x.close();
  }
  {
    Fixture x;
    fence_hook = [&](RunState& state) {
      if (x.machine.needs_baseline_veto() && &state == &x.state && browsers.size() > 1) {
        browsers[1]->s->closed = true;
        browsers[1]->s->browser = nullptr;
      }
    };
    const auto r = x.run(1, 6);
    ck(r.status != kStatusOk, "unsigned closure during final signed fence rejects request");
    ck(x.machine.counters().requests_accepted == 0, "cross-browser closure never S5");
    ck(std::all_of(x.dst.begin(), x.dst.end(), [](auto c) { return c == 0xcc; }),
       "cross-browser closure leaves output untouched");
    x.close();
  }
  {
    Fixture x(true);
    drop_closed_fence_reply = true;
    fence_hook = [](RunState& state) { state.closed = true; state.browser = nullptr; };
    const auto begin = NowUs();
    ck(!CheckJsCheckpoint(x.state, x.pump, x.signed_browser, begin + 10000),
       "closure without renderer reply rejects checkpoint");
    ck(NowUs() - begin < 1000, "terminal closure wakes checkpoint without consuming deadline");
    ck(!x.state.js_error.failed, "browser closure is not fabricated JS exception");
    x.close();
  }
  {
    Fixture x;
    fence_hook = [&](RunState& state) {
      if (&state != &x.state) { x.state.closed = true; x.state.browser = nullptr; }
    };
    const auto r = x.run(1, 6);
    ck(r.status != kStatusOk, "signed closure during unsigned load fence rejects request");
    ck(x.machine.counters().requests_accepted == 0, "cross-browser load closure never S5");
    x.close();
  }
  {
    Fixture x;
    ck(x.run(1, 6).status == kStatusOk, "closed reference retry setup");
    browsers[1]->s->closed = true;
    browsers[1]->s->browser = nullptr;
    std::fill(x.dst.begin(), x.dst.end(), 0xcc);
    const auto accepted = x.machine.counters().requests_accepted;
    ck(x.run(2, 7).status != kStatusOk, "closed reference cannot navigate again");
    ck(x.machine.counters().requests_accepted == accepted, "closed reference retry never S5");
    ck(std::all_of(x.dst.begin(), x.dst.end(), [](auto c) { return c == 0xcc; }),
       "closed reference retry leaves output untouched");
    x.close();
  }
  for (int fps : {1, 30, 60, 120, 240}) {
    Fixture x;
    x.options.cef.capture_fps = fps;
    ck(x.run(1, 6).status == kStatusOk && x.dst == Original(6),
       "requested unsigned capture rate preserves exact native proof");
    x.close();
  }
  for (bool resident : {false, true}) {
    Fixture x(resident);
    unsigned_cross_batch_pattern = true;
    const auto r = x.run(1, 6);
    ck(r.status == kStatusTimeout, "known latest mismatch vetoes cross-batch equal streak");
    ck(active_unsigned_pattern_paints >= 4, "M,E / E,M native observations actually delivered");
    ck(x.machine.counters().requests_accepted == 0, "known latest veto mismatch never S5");
    ck(std::all_of(x.dst.begin(), x.dst.end(), [](auto c) { return c == 0xcc; }),
       "known latest mismatch leaves output untouched");
    x.close();
  }
  {
    Fixture x;
    unsigned_cross_batch_pattern = unsigned_pattern_stabilizes = true;
    const auto r = x.run(1, 6);
    ck(r.status == kStatusOk && x.dst == Original(6),
       "mixed baseline pairs can recover when a complete later pair agrees");
    ck(active_unsigned_pattern_paints >= 6,
       "M,E / E,M cannot accept before the later complete E,E pair");
    x.close();
  }
  // Exhaust the first two batches. Later samples mismatch forever, so only
  // E,E in one COMPLETE pair can accept; cross-pair equality is insufficient.
  for (int mask = 0; mask < 16; ++mask) {
    Fixture x;
    unsigned_four_sample_mask = mask;
    const bool first_pair_equal = (mask & 3) == 0;
    const bool can_accept = first_pair_equal || (mask & 12) == 0;
    const auto r = x.run(1, 6);
    ck(r.status == (can_accept ? kStatusOk : kStatusTimeout),
       "all16 E/M patterns accept only a complete equal pair");
    ck(x.machine.counters().requests_accepted == (can_accept ? 1u : 0u),
       "all16 E/M patterns preserve exact S5 count");
    if (can_accept) {
      ck(x.dst == Original(6), "all16 accepted patterns retain signed bytes");
      ck(active_unsigned_pattern_paints == (first_pair_equal ? 2 : 4),
         "all16 accepted patterns consume the complete selected native pair");
    } else {
      ck(std::all_of(x.dst.begin(), x.dst.end(), [](auto c) { return c == 0xcc; }),
         "all16 rejected patterns leave output untouched");
    }
    x.close();
  }
  std::printf("EXTRACTED_NATIVE_MODEL_PASS checks=%d scenarios=49 (fake CEF events, actual production orchestration/FSM)\n",check_count);
  return 0;
}
}
int main(){
try{
  return vtx_cef::tests();
}
catch(const std::exception&e){
  std::fprintf(stderr,"FAIL %s\n",e.what());
  for (auto& entry : vtx_cef::trace) {
    std::fprintf(stderr, "%s\n", entry.c_str());
  }
  return 1;
}
}

#elif defined(VTX_MODEL_EXACT_BYTES)
// === Pixel proof: actual FSM + independently generated/corrupted pixels. ===
#include "frame_sync_machine.hpp"
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <stdexcept>
#include <vector>
using namespace vtx_cef::fsync;
static int checks=0;
void ck(bool x,const char* s){
  ++checks;
  if(!x)throw std::runtime_error(s);
}
struct Scenario{
  FrameSyncStateMachine m;
  RequestDesc d;
  PageEvent id;
  uint32_t w,h;
  size_t stride;
  std::vector<uint8_t> original,one,two,dst;
  Scenario(uint32_t W=32,uint32_t H=24,size_t pad=0,uint32_t req=0xfedcba98,int f=0x1234):w(W),h(H),stride(W*4+pad),original(stride*H),dst(W*H*4,0xcc){
    for (uint32_t y=0; y<h; ++y)for (uint32_t x=0; x<w; ++x){
      auto p=&original[y*stride+x*4];
      p[3]=(x*17+y*31)%256;
      for (int c=0; c<3; ++c)p[c]=p[3]?((x*37+y*11+c*29)%(p[3]+1)):0;
    }
    one=two=original;
    draw(one,req,f,false);
    draw(two,req,f,true);
    d.document_gen=23;
    d.request_id=req;
    d.target_frame_index=f;
    d.canary=CanaryMode::kExact;
    d.expected_w=w;
    d.expected_h=h;
    id.document_gen=d.document_gen;
    id.request_id=req;
    id.target_frame_index=f;
    ck(m.BeginRequest(d,1),"begin");
    id.phase=PageEvent::Phase::kAssetsReady;
    ck(m.NotifyPageEvent(id,2),"assets");
    id.phase=PageEvent::Phase::kCanarySet;
    ck(m.NotifyPageEvent(id,3),"canary");
    ck(m.EnterPumping(4),"pumping");
  }
  void draw(std::vector<uint8_t>& b,uint32_t req,int f,bool relocated){
    for (uint32_t y=0; y<8; ++y){
      int token=(y<4?f:y<6?req&65535:req>>16)^(relocated?65535:0);
      int hi=token>>8,lo=token&255;
      for (uint32_t x=0; x<8; ++x){
        uint32_t xx=relocated?w-8+x:x;
        uint8_t*p=&b[y*stride+xx*4];
        p[0]=(hi^lo^0xa5)&255;
        p[1]=lo;
        p[2]=hi;
        p[3]=255;
        xx=relocated?x:w-8+x;
        p=&b[(h-8+y)*stride+xx*4];
        p[0]=(16*lo+22*hi+0xc3)&255;
        p[1]=hi;
        p[2]=lo;
        p[3]=255;
      }
    }
  }
  PaintVerdict feed(const std::vector<uint8_t>&v){
    return m.FeedPaint(v.data(),stride,w,h,dst.data(),10);
  }
  PaintVerdict veto(const std::vector<uint8_t>&v,uint64_t seq){
    return m.FeedExactBaselinePaint(id,v.data(),stride,w,h,dst.data(),20,seq);
  }
  void signs(){
    ck(m.PaintMayAdvance(one.data(),stride,w,h),"phase1 classifier");
    ck(feed(one).disposition!=PaintDisposition::kAccepted,"phase1 cannot publish");
    ck(!m.PaintMayAdvance(one.data(),stride,w,h),"phase1 replay classifier");
    ck(feed(one).disposition==PaintDisposition::kRejectedStale,"phase1 replay rejection");
    ck(m.PaintMayAdvance(two.data(),stride,w,h),"phase2 classifier");
    ck(feed(two).disposition!=PaintDisposition::kAccepted,"two signatures no veto cannot publish");
    ck(m.needs_baseline_veto(),"pending signed proof");
  }
  void untouched(){
    ck(std::all_of(dst.begin(),dst.end(),[](auto x){
      return x==0xcc;
    }
    ),"premature dst write");
  }
  void matches(){
    for (uint32_t y=0; y<h; ++y)ck(std::equal(dst.begin()+y*w*4,dst.begin()+(y+1)*w*4,original.begin()+y*stride),"output not signed exact original");
  }
};
int main(){
  try{
    for (auto dims: {
      std::pair<uint32_t,uint32_t>{
        16,16
      }
      ,{
        17,19
      }
      ,{
        32,24
      }
      ,{
        64,48
      }
    }
    ){
      for(auto pad:{
        0,7
      }
      ){
        Scenario s(dims.first,dims.second,pad); s.signs(); s.untouched();
        ck(s.veto(s.original,101).disposition!=PaintDisposition::kAccepted,"no ready arm");
        ck(s.m.ArmExactBaseline(s.id,100,11),"ready arm");
        ck(!s.m.ArmExactBaseline(s.id,0,11),"ready cannot rearm");
        ck(s.veto(s.original,100).disposition!=PaintDisposition::kAccepted,"pre-ready paint");
        ck(s.veto(s.original,101).disposition!=PaintDisposition::kAccepted,"one equal insufficient");
        ck(s.veto(s.original,101).disposition!=PaintDisposition::kAccepted,"duplicate paint rejected");
        ck(s.veto(s.original,102).disposition!=PaintDisposition::kAccepted,"duplicate reset consecutive");
        s.untouched();
        auto v=s.veto(s.original,103);
        ck(v.disposition==PaintDisposition::kAccepted&&v.decoded_frame==0x1234&&v.decoded_request_id==0xfedcba98,"decoded frame and full request");
        s.matches();
        ck(s.veto(s.original,104).disposition!=PaintDisposition::kAccepted,"late baseline");
      }
    }
    // All bytes, not a handful of corner samples: every one-byte veto mismatch fails closed.
    for (size_t byte=0; byte<64*48*4; ++byte){
      Scenario s(64,48);
      s.signs();
      ck(s.m.ArmExactBaseline(s.id,100,11),"arm");
      auto bad=s.original;
      bad[byte]^=1;
      ck(s.veto(bad,101).disposition!=PaintDisposition::kAccepted,"bad1");
      ck(s.veto(bad,102).disposition!=PaintDisposition::kAccepted,"bad2");
      s.untouched();
    }
    // Every covered proof byte, all channels in all three bands, in both phases.
    for (int phase=0; phase<2; ++phase)for (int corner=0; corner<2; ++corner)for (uint32_t y=0; y<8; ++y)for (uint32_t x=0; x<8; ++x)for (int c=0; c<4; ++c){
      Scenario s;
      if(phase)s.feed(s.one);
      auto bad=phase?s.two:s.one;
      uint32_t xx=(phase?(corner?0:s.w-8):(corner?s.w-8:0))+x;
      uint32_t yy=(corner?s.h-8:0)+y;
      bad[yy*s.stride+xx*4+c]^=1;
      ck(!s.m.PaintMayAdvance(bad.data(),s.stride,s.w,s.h),"corrupt signature prefilter");
      ck(s.feed(bad).disposition==PaintDisposition::kRejectedStale,"corrupt signature rejection");
      s.untouched();
    }
    // Same frame, different request must never advance either signed phase.
    for (uint32_t old:{
      1u,0xba98u,0xfedc0000u,0xffffffffu
    }
    ){
      Scenario s; auto bad=s.original; s.draw(bad,old,s.d.target_frame_index,false);
      ck(!s.m.PaintMayAdvance(bad.data(),s.stride,s.w,s.h),"old request");
      ck(s.feed(bad).disposition==PaintDisposition::kRejectedStale,"old request feed");
    }
    // Owned first-paint storage transfers, while caller buffer reuse cannot mutate proof.
    for (int trial=0; trial<100; ++trial){
      Scenario s;
      auto first=s.one;
      const uint8_t* transferred=first.data();
      auto original_first=first;
      ck(s.m.FeedOwnedPaint(first,s.w,s.h,s.dst.data(),10).disposition!=PaintDisposition::kAccepted,"owned first no publish");
      ck(first.data()!=transferred,"owned first transferred storage");
      first.assign(original_first.size(),0x71);
      auto corrupt=s.two;
      corrupt[(10*s.w+10)*4]^=1;
      const auto before=corrupt;
      ck(s.m.FeedOwnedPaint(corrupt,s.w,s.h,s.dst.data(),11).disposition==PaintDisposition::kRejectedStale,"owned content drift");
      ck(corrupt==before,"reject cannot consume caller storage");
      auto second=s.two;
      ck(s.m.FeedOwnedPaint(second,s.w,s.h,s.dst.data(),12).disposition!=PaintDisposition::kAccepted,"owned second still no publish");
      second.assign(second.size(),0x12);
      ck(s.m.ArmExactBaseline(s.id,100,13),"owned arm");
      ck(s.veto(s.original,101).disposition!=PaintDisposition::kAccepted,"owned veto1");
      ck(s.veto(s.original,102).disposition==PaintDisposition::kAccepted,"owned veto2");
      s.matches();
    }
    Scenario r;
    r.m.FailRequest("test",30);
    auto d=r.d;
    --d.request_id;
    ck(!r.m.BeginRequest(d,31),"historical request replay");
    d.request_id=0;
    ck(!r.m.BeginRequest(d,32),"zero request");
    std::printf("INDEPENDENT_EXACT_PASS checks=%d veto_bytes=12288 signature_bytes=1024 geometries=8\n",checks);
    return 0;
  }
  catch(const std::exception&e){
    std::fprintf(stderr,"FAIL: %s checks=%d\n",e.what(),checks);
    return 1;
  }
}

#elif defined(VTX_MODEL_PUMP)
// === External message pump: actual PumpDriver + fake CEF work callback. ===
#include "external_pump_schedule.hpp"
#include <atomic>
#include <functional>
#include <thread>
#include <cstdio>
#include <stdexcept>
namespace vtx_cef {
  struct RunState{
    ExternalPumpSchedule pump_schedule;
  };
  struct PumpDriver{
    RunState*state;
    void Once();
    void WaitForWork(uint64_t,uint64_t=1000);
    bool Until(const std::function<bool()>&,int);
  };
  static std::function<void()> callback;
  static int calls=0;
  void CefDoMessageLoopWork(){
    ++calls;
    if(callback)callback();
  }
  void ck(bool b,const char*s){
    if(!b)throw std::runtime_error(s);
  }
  // PRODUCTION_PUMP_IMPLEMENTATION
  int test(){
    RunState s;
    PumpDriver p{&s};
    callback=[]{
    };
    ck(p.Until([]{
      return true;
    }
    ,50)&&calls==0,"already done no extra pump");
    ck(!p.Until([]{
      return false;
    }
    ,0)&&calls==0,"zero budget cannot pump");
    bool done=false;
    callback=[&]{
      done=true;
    };
    ck(p.Until([&]{
      return done;
    }
    ,50)&&calls==1,"complete in callback no extra pump");
    callback=[&]{
      s.pump_schedule.Schedule(1,ExternalPumpSchedule::NowUs());
    };
    p.Once();
    ck(s.pump_schedule.deadline_us()!=ExternalPumpSchedule::kNoDeadline,"reentrant schedule retained");
    callback=[]{
    };
    s.pump_schedule.Schedule(500,ExternalPumpSchedule::NowUs());
    auto scheduled=s.pump_schedule.deadline_us();
    std::atomic<bool> worker_done{false};
    std::thread worker([&]{
      std::this_thread::sleep_for (std::chrono::milliseconds(1)); worker_done.store(true,std::memory_order_release); s.pump_schedule.NotifyWork();
    }
    );
    auto before=ExternalPumpSchedule::NowUs();
    ck(p.Until([&]{
      return worker_done.load(std::memory_order_acquire);
    }
    ,1000),"native completion wake");
    auto elapsed=ExternalPumpSchedule::NowUs()-before;
    worker.join();
    ck(elapsed<100000,"native completion no 500ms timer wait");
    ck(s.pump_schedule.deadline_us()==scheduled,"native wake cannot replace CEF timer");
    // Repeated predicate-to-sleep races; producer signal may arrive on either side of the wait.
    for (int i=0; i<200; ++i){
      worker_done=false;
      std::thread t([&]{
        worker_done=true;
s.pump_schedule.NotifyWork();
      }
      );
      ck(p.Until([&]{
        return worker_done.load();
      }
      ,100),"completion race liveness");
      t.join();
    }
    std::printf("EXTRACTED_PUMP_PASS native-wake elapsed-us=%llu races=200 (not CEF perf evidence)\n",(unsigned long long)elapsed);
    return 0;
  }
}
int main(){
  try{
    return vtx_cef::test();
  }
  catch(const std::exception&e){
    std::fprintf(stderr,"FAIL %s\n",e.what());
    return 1;
  }
}

#elif defined(VTX_MODEL_LIFECYCLE)
// === Actual serve early-close helper, with deterministic asynchronous CEF. ===
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <vector>
namespace vtx_cef {
template<class T> using CefRefPtr = std::shared_ptr<T>;
struct CefBrowser;
struct RunState {
  std::mutex mu;
  std::atomic<bool> closed{false};
  CefRefPtr<CefBrowser> browser;
};
struct CefBrowser {
  RunState* state;
  int* close_calls;
  std::vector<std::function<void()>>* tasks;
  bool closes = true;
  CefBrowser* GetHost() {
    if (state->closed) throw std::runtime_error("GetHost after OnBeforeClose");
    return this;
  }
  void CloseBrowser(bool force) {
    if (!force || state->closed) throw std::runtime_error("invalid close");
    ++*close_calls;
    if (closes) tasks->push_back([state = state] {
      state->closed = true;
      state->browser = nullptr;
    });
  }
};
struct PumpDriver {
  std::vector<std::function<void()>> tasks;
  int waits = 0;
  int ticks = 0;
  bool Until(const std::function<bool()>& done, int timeout_ms) {
    ++waits;
    // A deterministic tick is one simulated millisecond; never sleeps.
    const int deadline = ticks + timeout_ms;
    while (!done() && ticks < deadline) {
      ++ticks;
      auto batch = std::move(tasks);
      tasks.clear();
      for (auto& task : batch) task();
    }
    return done();
  }
};
// PRODUCTION_LIFECYCLE_IMPLEMENTATION
static int checks = 0;
static void ck(bool value, const char* what) {
  ++checks;
  if (!value) throw std::runtime_error(what);
}
struct Fixture {
  RunState state;
  PumpDriver pump;
  CefRefPtr<CefBrowser> browser;
  int close_calls = 0;
  int shutdowns = 0;
  void create(bool closes = true, bool own = true) {
    state.browser = std::make_shared<CefBrowser>(
        CefBrowser{&state, &close_calls, &pump.tasks, closes});
    if (own) browser = state.browser;
  }
  bool finish(bool requested) {
    if (!CloseServeBrowser(state, pump, browser, requested)) return false;
    ck(!browser && !state.browser, "release all browser handles before shutdown");
    ++shutdowns;
    return true;
  }
};
int test() {
  {
    Fixture x;
    ck(x.finish(false), "rejected creation has no browser to close");
    ck(x.close_calls == 0 && x.pump.waits == 0 && x.shutdowns == 1,
       "rejected creation does not wait 30 seconds");
  }
  {
    Fixture x;
    x.create();
    ck(x.finish(true), "known browser closes");
    ck(x.close_calls == 1 && x.shutdowns == 1, "known browser closed once");
  }
  {
    Fixture x;
    x.pump.tasks.push_back([&] { x.create(true, false); });
    ck(x.finish(true), "late OnAfterCreated adopted and closed");
    ck(x.close_calls == 1 && x.pump.waits == 2 && x.shutdowns == 1,
       "late browser callback observed before close wait");
  }
  {
    Fixture x;
    ck(!x.finish(true), "unresolved async creation is not closed");
    ck(x.shutdowns == 0 && x.close_calls == 0 && x.pump.ticks == 5000,
       "unresolved creation never calls shutdown and remains bounded");
  }
  {
    Fixture x;
    x.create(false);
    ck(!x.finish(true), "missing OnBeforeClose fails");
    ck(x.shutdowns == 0 && x.close_calls == 1 && x.pump.ticks == 5000,
       "missing close callback never calls shutdown and remains bounded");
    ck(x.browser && x.state.browser, "failed closure preserves live handles");
  }
  {
    Fixture x;
    x.create();
    x.state.closed = true;
    x.state.browser = nullptr;
    ck(x.finish(true), "already closed browser releases stale local handle");
    ck(x.close_calls == 0 && x.pump.waits == 0,
       "already closed browser receives no illegal methods");
  }
  {
    Fixture x;
    x.pump.tasks.push_back([&] { x.state.closed = true; });
    ck(x.finish(true), "browser that closes during async wait is terminal");
    ck(x.close_calls == 0 && x.shutdowns == 1,
       "terminal callback does not issue another close");
  }
  {
    Fixture x;
    x.create(true, false);
    ck(x.finish(false), "callback-created browser closed even on false return");
    ck(x.close_calls == 1 && x.shutdowns == 1,
       "observed browser takes precedence over creation return code");
  }
  std::printf("EXTRACTED_LIFECYCLE_PASS checks=%d scenarios=8 (fake CEF callbacks, actual close helper)\n", checks);
  return 0;
}
}
int main(int argc, char**) {
  // Execute the real fail-stop primitive in a disposable subprocess. It must
  // neither return nor unwind callback-owning stack objects while CEF is live.
  if (argc > 1) {
    struct Sentinel { ~Sentinel() { std::fputs("UNSAFE_UNWIND\n", stderr); } } sentinel;
    vtx_cef::ExitUnclosedServe(17);
  }
  try { return vtx_cef::test(); }
  catch (const std::exception& error) {
    std::fprintf(stderr, "FAIL %s\n", error.what());
    return 1;
  }
}


#elif defined(VTX_MODEL_PASSIVE)
// === Passive snapshot helper: invalid pointers, sample floors and copy counts. ===

#include "passive_snapshot.hpp"
#include <cassert>
#include <vector>
struct State {
  bool passive_snapshot_enabled = true;
  uint64_t passive_snapshot_copied_paints = 0;
  uint64_t passive_snapshot_copied_bytes = 0;
  uint64_t passive_snapshot_dropped_paints = 0;
  std::atomic<int> paints{0};
  std::vector<uint8_t> buffer;
  int width = 0, height = 0;
};
int main() {
  using namespace vtx_cef;
  State s;
  const uint8_t first[] = {1, 2, 3, 4, 5, 6, 7, 8};
  const uint8_t second[] = {8, 7, 6, 5, 4, 3, 2, 1};
  CapturePassiveSnapshotLocked(s, first, 2, 1, false);
  assert(s.paints == 1 && s.buffer == std::vector<uint8_t>(first, first + 8));
  assert(s.width == 2 && s.height == 1);
  assert(s.passive_snapshot_copied_paints == 1 && s.passive_snapshot_copied_bytes == 8);
  assert(SetPassiveSnapshotDemandLocked(s, false) == 1);
  assert(s.buffer.empty() && s.width == 0 && s.height == 0);
  // A dropped callback must not dereference even a deliberately invalid source.
  CapturePassiveSnapshotLocked(s, reinterpret_cast<const uint8_t*>(1), 1920, 1080, false);
  assert(s.paints == 2 && s.buffer.empty() && s.passive_snapshot_dropped_paints == 1);
  const int readiness_floor = SetPassiveSnapshotDemandLocked(s, true);
  assert(readiness_floor == 2 && s.buffer.empty());
  CapturePassiveSnapshotLocked(s, first, 2, 1, false);
  const int sequence_one = s.paints;
  std::vector<uint8_t> owned_one;
  owned_one.swap(s.buffer);
  CapturePassiveSnapshotLocked(s, second, 2, 1, false);
  const int sequence_two = s.paints;
  std::vector<uint8_t> owned_two;
  owned_two.swap(s.buffer);
  assert(sequence_one > readiness_floor && sequence_two > sequence_one);
  assert(owned_one == std::vector<uint8_t>(first, first + 8));
  assert(owned_two == std::vector<uint8_t>(second, second + 8));
  // Both samples remain owned after idle paints; do not request a third copy.
  assert(SetPassiveSnapshotDemandLocked(s, false) == sequence_two);
  CapturePassiveSnapshotLocked(s, nullptr, 1920, 1080, false);
  assert(s.buffer.empty() && s.paints == sequence_two + 1);
  assert(s.passive_snapshot_copied_paints == 3 && s.passive_snapshot_copied_bytes == 24);
  // A mismatched veto can re-arm with a fresh sequence floor, never stale bytes.
  const int retry_floor = SetPassiveSnapshotDemandLocked(s, true);
  assert(retry_floor > sequence_two && s.buffer.empty());
  CapturePassiveSnapshotLocked(s, second, 2, 1, false);
  assert(s.paints > retry_floor && !s.buffer.empty());
  // Signed idle capture never takes the generic snapshot, even if enabled.
  CapturePassiveSnapshotLocked(s, reinterpret_cast<const uint8_t*>(1), 1920, 1080, true);
  assert(s.buffer.empty() && s.width == 0 && s.height == 0);
  assert(s.passive_snapshot_copied_paints == 4 && s.passive_snapshot_copied_bytes == 32);
  assert(s.passive_snapshot_dropped_paints == 3);
  // Bad geometry cannot leave an old snapshot available under a fresh sequence.
  CapturePassiveSnapshotLocked(s, first, 0, 1, false);
  assert(s.buffer.empty() && s.passive_snapshot_dropped_paints == 4);
}


#elif defined(VTX_MODEL_SERVE_FAILURES) || defined(VTX_MODEL_ONESHOT)
// === Actual RunServe / one-shot control flow with shared fake CEF events. ===
// Serve uses real socketpair I/O and SCM_RIGHTS, with fake captures/SHM allocation.
// One-shot retains screenshot/probe/JSON control flow with a fake PNG sink.
// Neither model runs Chromium; production cleanup and failure decisions execute.
#include "ipc_protocol.hpp"
#include "frame_sync_machine.hpp"
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <condition_variable>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <functional>
#include <memory>
#include <map>
#include <fstream>
#include <chrono>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#if defined(__APPLE__)
#define VTX_SEND_FLAGS 0
#else
#define VTX_SEND_FLAGS MSG_NOSIGNAL
#endif
namespace vtx_cef {
static std::string scenario;
static std::atomic<uint64_t> tick{1};
static int creations = 0, pending_creations = 0, closes = 0, natural_closes = 0, shutdowns = 0, live = 0;
static int requested_capture_fps = 120;
[[maybe_unused]] static int captures = 0, encodes = 0, commits = 0, baseline_closes = 0;
[[maybe_unused]] static int clients = 0, page_probes = 0, gpu_probes = 0, begin_frames = 0;
static void ck(bool value, const char* what) {
  if (!value) {
    std::fprintf(stderr, "FAIL %s\n", what);
    std::_Exit(91);  // Never unwind a model assertion through the joinable IPC peer.
  }
}
uint64_t NowUs() { return tick.load(); }
template<class T> struct CefRefPtr : std::shared_ptr<T> {
  using std::shared_ptr<T>::shared_ptr;
  CefRefPtr() = default;
  CefRefPtr(T* pointer) : std::shared_ptr<T>(pointer) {}
  CefRefPtr& operator=(T* pointer) { this->reset(pointer); return *this; }
};
struct CefString {
  std::string value;
  void FromString(const std::string& text) { value = text; }
  std::string ToString() const { return value; }
};
struct Options {
  int width = 16, height = 16, capture_fps = 120;
  bool serve_mode = false, probe = true, probe_gpu_page = true;
  std::string mode = "a0", ozone_platform;
};
struct RunState;
struct CefBrowser {
  RunState* state;
  CefString url;
  CefBrowser* GetHost();
  CefBrowser* GetMainFrame() { return GetHost(); }
  bool IsLoading() { return false; }
  CefString GetURL() { return url; }
  void LoadURL(const CefString& input);
  void CloseBrowser(bool);
  void SendExternalBeginFrame();
  void ExecuteJavaScript(const std::string&, const std::string&, int);
  void LoadURL(const char* input) { CefString value; value.FromString(input); LoadURL(value); }
};
struct RunState {
  std::mutex mu;
  std::atomic<bool> closed{false}, loaded{false};
  std::atomic<uint64_t> load_starts{0};
  std::atomic<int> paints{0};
  CefRefPtr<CefBrowser> browser;
  struct { void NotifyWork() {} } pump_schedule;
  struct { uint32_t sent = 0; int renderer_pid = 123; } js_error;
  bool sync_capture_armed = false, capture_armed = false;
  fsync::FrameSyncStateMachine* sync_machine = nullptr;
  std::vector<uint8_t> cap_staging, buffer;
  int width = 0, height = 0;
  std::string page_probe, gpu_page_probe, last_error;
  uint32_t cap_expected_w = 0, cap_expected_h = 0;
  uint8_t* cap_dst = nullptr;
};
static std::vector<std::pair<uint64_t, std::function<void()>>> tasks;
struct PumpDriver {
  RunState* state;
  void Once() {
    tick += 1000;
    auto batch = std::move(tasks);
    tasks.clear();
    for (auto& task : batch) {
      if (task.first <= tick) task.second();
      else tasks.push_back(std::move(task));
    }
    // Let the real encoder thread make progress while CEF time is simulated.
    std::this_thread::yield();
  }
  bool Until(const std::function<bool()>& done, int timeout_ms) {
    const auto deadline = tick + static_cast<uint64_t>(timeout_ms) * 1000;
    while (!done() && tick < deadline) Once();
    return done();
  }
};
CefBrowser* CefBrowser::GetHost() {
  if (state->closed) {
    std::fputs("FAIL browser method after OnBeforeClose\n", stderr);
    std::_Exit(93);  // Model assertion must not unwind through a joinable peer.
  }
  return this;
}
static void NaturalClose(RunState* state) {
  ++natural_closes; --live;
  state->closed = true;
  state->browser = nullptr;
}
void CefBrowser::LoadURL(const CefString& input) {
  url = input;
#if defined(VTX_MODEL_ONESHOT)
  if (scenario == "gpu_load_closed") {
    tasks.emplace_back(tick, [state = state] { NaturalClose(state); });
  } else if (scenario != "gpu_load_timeout") {
    ++state->load_starts;
    state->loaded = true;
    if (scenario == "gpu_settle_closed") {
      tasks.emplace_back(tick + 2000, [state = state] { NaturalClose(state); });
    }
  }
#else
  if (scenario == "resident_closed") {
    tasks.emplace_back(tick, [state = state] {
      ++state->load_starts;
      state->loaded = true;
      ++natural_closes; --live;
      state->closed = true;
      state->browser = nullptr;
    });
  } else if (scenario != "resident_timeout") {
    ++state->load_starts;
    state->loaded = true;
  }
#endif
}
void CefBrowser::CloseBrowser(bool force) {
  ck(force && !state->closed, "invalid CloseBrowser");
  ++closes;
  if (scenario == "close_timeout") return;
  tasks.emplace_back(tick, [state = state] {
    --live;
    state->closed = true;
    state->browser = nullptr;
  });
}
struct CefMainArgs { CefMainArgs(int, char**) {} };
struct CefSettings {};
struct WorkerApp { WorkerApp(const Options*, RunState*) {} };
struct WorkerClient {
  RunState* state;
  WorkerClient(const Options*, RunState* input) : state(input) { ++clients; }
  ~WorkerClient() { --clients; }
};
CefSettings MakeWorkerSettings(const std::string&) { return {}; }
bool CefInitialize(CefMainArgs&, CefSettings&, CefRefPtr<WorkerApp>, std::nullptr_t) {
  return scenario != "initialize_false";
}
void CefShutdown() {
  if (live != 0 || pending_creations != 0 || !tasks.empty()) {
    std::fputs("FAIL CefShutdown before pending/live browser closed\n", stderr);
    std::_Exit(92);  // Deterministic assertion, no abort/core or unsafe unwinding.
  }
  ck(clients == 0, "release client owner before CefShutdown");
  ++shutdowns;
}
using CefClient = WorkerClient;
struct CefWindowInfo {
  bool shared_texture_enabled = true, external_begin_frame_enabled = false;
  void SetAsWindowless(int) {}
};
struct CefBrowserSettings { int background_color = 1, windowless_frame_rate = 0; };
struct CefBrowserHost {
static bool CreateBrowser(CefWindowInfo& window, CefRefPtr<CefClient> client,
                          const CefString& url, CefBrowserSettings& settings,
                          std::nullptr_t, std::nullptr_t) {
  ck(settings.windowless_frame_rate == requested_capture_fps,
     "signed requested capture FPS reaches actual CEF settings");
  ck(settings.background_color == 0 && !window.shared_texture_enabled &&
     window.external_begin_frame_enabled, "signed alpha/external-frame policy unchanged");
  if (scenario == "create_false") return false;
  ++pending_creations;
  if (scenario == "create_missing") return true;
  const uint64_t creation_delay = scenario == "create_late" ?
#if defined(VTX_MODEL_ONESHOT)
      4000 : 0;
#else
      30001000 : 0;
#endif
  tasks.emplace_back(tick + creation_delay, [client, url] {
    --pending_creations;
    ++creations;
    ++live;
    client->state->browser = new CefBrowser{client->state, url};
    client->state->loaded = true;
#if defined(VTX_MODEL_ONESHOT)
    if (scenario == "load_timeout" || scenario == "load_closed") client->state->loaded = false;
    if (scenario == "load_closed" || scenario == "settle_closed") {
      tasks.emplace_back(tick + 2000, [state = client->state] { NaturalClose(state); });
    }
#endif
    if (scenario == "create_closed") {
      ++natural_closes; --live;
      client->state->closed = true;
      client->state->browser = nullptr;
    }
  });
  return true;
}
};
// PRODUCTION_SERVE_BROWSER_CREATOR
void PrepareJsDocument(RunState&, const std::string&) {}
struct PngEncodeOptions {};
struct PngEncodeWorkspace {};
struct PngBuffer {
  std::vector<uint8_t> bytes{1, 2, 3};
  const uint8_t* data() const { return bytes.data(); }
  size_t size() const { return bytes.size(); }
};
bool EncodePngBgraPremultiplied(const uint8_t*, int, int, size_t,
    const PngEncodeOptions&, PngEncodeWorkspace*, PngBuffer*, uint64_t*) {
  ++encodes;
  if (scenario == "encode_throw") throw std::runtime_error("fake encoder failure");
  return true;
}
bool AtomicWriteBytes(const std::string&, const uint8_t*, size_t) {
  if (scenario == "write_false") return false;
  ++commits;
  return true;
}
struct ServeOptions {
  Options cef;
  int ipc_fd = -1;
  std::string ipc_socket, url_template = "file:///test?f={f}", root_cache_path;
  int idle_timeout_ms = 2000, slot_wait_timeout_ms = 5, frame_budget_ms = 3;
  int encode_threads = 2, fault_abort_after_frames = -1;
  PngEncodeOptions png;
  bool sync = false;
  std::string sync_mode = "n", sync_log, sync_canary = "exact", reference_context = "shared";
};
constexpr uint32_t kRingSlots = 4;
struct ShmRingWriter {
  std::FILE* backing = nullptr;
  struct { uint32_t frame_bytes = 1024; } layout;
  struct { struct { std::atomic<uint32_t> seq{1}; } gates[4]; } ctrl;
  FrameSlotHeader header{};
  std::array<uint8_t, 1024> pixels{};
  ~ShmRingWriter() { if (backing) std::fclose(backing); }
  static std::unique_ptr<ShmRingWriter> Create(uint32_t, uint32_t, uint64_t, std::string* error) {
    if (scenario == "ring_failure") { *error = "injected allocation failure"; return {}; }
    auto ring = std::make_unique<ShmRingWriter>();
    ring->backing = std::tmpfile();
    ck(ring->backing != nullptr, "temporary fd for actual SCM_RIGHTS");
    return ring;
  }
  const auto& geometry() const { return layout; }
  int fd() const { return ::fileno(backing); }
  int ClaimSlot(uint64_t, uint64_t*) { return 0; }
  uint8_t* SlotData(uint32_t) { return pixels.data(); }
  FrameSlotHeader* SlotHeader(uint32_t) { return &header; }
  void AbandonSlot(uint32_t) {}
  void PublishSlot(uint32_t) {}
  auto* control() { return &ctrl; }
};
#if defined(VTX_MODEL_SERVE_FAILURES)
struct ExactBaselineCapture {
  bool Close(PumpDriver& pump) {
    ++baseline_closes;
    if (scenario == "baseline_closes_signed") {
      tasks.emplace_back(tick, [state = pump.state] { NaturalClose(state); });
      pump.Once();
    }
    return true;
  }
  void PoisonResident() {}
  std::string TimingFields() const { return {}; }
};
// PRODUCTION_SERVE_RESULT_STRUCTS
ProduceResult ProduceFrame(RunState&, PumpDriver&, CefRefPtr<CefBrowser>,
    const std::string&, int32_t frame, const ServeOptions&, uint32_t gen) {
  ++captures;
  ProduceResult result;
  result.width = result.height = 16;
  result.document_gen = gen;
  result.canary_frame = frame;
  if (scenario == "capture_failure") result.status = kStatusTimeout;
  return result;
}
SyncProduceResult ProduceFrameSync(RunState&, PumpDriver&, CefRefPtr<CefBrowser>,
    fsync::FrameSyncStateMachine&, ExactBaselineCapture&, const ServeOptions&,
    uint32_t, uint32_t, int32_t, uint32_t, uint32_t, uint8_t*) {
  throw std::runtime_error("unexpected fake sync capture");
}
// PRODUCTION_SERVE_TRANSPORT_POOL
// PRODUCTION_SERVE_FAILURE_CONTROL_FLOW
int test(int argc, char** argv) {
  ck(argc == 3 || argc == 4, "scenario, private temp root and optional FPS arguments");
  scenario = argv[1];
  ServeOptions options;
  requested_capture_fps = options.cef.capture_fps = argc == 4 ? std::stoi(argv[3]) : 120;
  options.root_cache_path = argv[2];
  if (scenario == "idle_timeout") options.idle_timeout_ms = 5;
  if (scenario == "sync_log_failure" || scenario == "resident_timeout" ||
      scenario == "resident_closed" || scenario == "ring_failure") {
    options.sync = true;
    options.sync_log = std::string(argv[2]) +
        (scenario == "sync_log_failure" ? "/missing/sync.jsonl" : "/sync.jsonl");
  }
  if (scenario == "resident_timeout" || scenario == "resident_closed") {
    options.sync_mode = "p";
    options.url_template = "file:///test?f=0";
  }
  int pair[2];
  ck(::socketpair(AF_UNIX, SOCK_STREAM, 0, pair) == 0, "real socketpair");
  options.ipc_fd = pair[0];
  if (scenario == "hello_failure") ck(::shutdown(pair[1], SHUT_RD) == 0, "peer refuses hello");
  std::exception_ptr peer_error;
  int received_fd = -1;
  int replies = 0;
  uint16_t status = 99;
  std::thread peer([&] {
    try {
      timeval timeout{2, 0};
      ::setsockopt(pair[1], SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
      HelloInfo hello{};
      iovec iov{&hello, sizeof(hello)};
      char ancillary[CMSG_SPACE(sizeof(int))]{};
      msghdr message{};
      message.msg_iov = &iov; message.msg_iovlen = 1;
      message.msg_control = ancillary; message.msg_controllen = sizeof(ancillary);
      const auto count = ::recvmsg(pair[1], &message, 0);
      if (count <= 0) return;  // Setup failed before Hello; main verifies branch.
      ck(count == sizeof(hello) && hello.magic == kHelloMagic, "real native Hello");
      auto* fd_message = CMSG_FIRSTHDR(&message);
      ck(fd_message && fd_message->cmsg_level == SOL_SOCKET &&
         fd_message->cmsg_type == SCM_RIGHTS, "real native descriptor attachment");
      std::memcpy(&received_fd, CMSG_DATA(fd_message), sizeof(int));
      ck(::fcntl(received_fd, F_GETFD) >= 0, "received descriptor is live");
      ::close(received_fd);
      received_fd = -2;
      if (scenario == "idle_timeout") return;  // Socket remains owned by main.
      FrameRequest request{};
      request.magic = scenario == "invalid_request" ? 0 : kRequestMagic;
      request.protocol_version = kProtocolVersion;
      request.request_id = 7;
      request.frame_index = 2;
      std::strcpy(request.out_png_path, "frame0.png");
      const bool render = scenario == "success" || scenario == "capture_failure" ||
                          scenario == "encode_throw" || scenario == "write_false";
      if (scenario == "partial_request" || scenario == "invalid_request" || render) {
        const size_t size = scenario == "partial_request" ? 13 : sizeof(request);
        ck(SendAll(pair[1], &request, size), "send real request");
      }
      ck(::shutdown(pair[1], SHUT_WR) == 0, "real host EOF");
      if (render) {
        FrameSlotHeader receipt{};
        ck(RecvAllOrShort(pair[1], &receipt, sizeof(receipt), 2000) == 1, "real native receipt");
        ck(receipt.magic == kSlotMagic && receipt.request_id == 7 &&
           receipt.target_frame_index == 2, "receipt bound to requested frame");
        status = receipt.status_code;
        ++replies;
      }
    } catch (...) { peer_error = std::current_exception(); }
  });
  const int result = RunServe(options, argc, argv);
  ::close(pair[0]);  // Some pre-Hello error branches leave process-owned IPC open.
  peer.join();
  ::close(pair[1]);
  if (received_fd >= 0) ::close(received_fd);
  if (peer_error) std::rethrow_exception(peer_error);
  const bool initialized = scenario != "initialize_false";
  ck(shutdowns == (initialized ? 1 : 0), "exactly one safe shutdown after initialized session");
  ck(live == 0 && closes + natural_closes == creations, "all created browsers closed");
  if (natural_closes) ck(tick < 1000000, "terminal startup callback aborts without deadline wait");
  const bool before_hello = scenario == "initialize_false" || scenario == "create_false" ||
      scenario == "create_late" || scenario == "create_closed" || scenario == "sync_log_failure" ||
      scenario == "resident_timeout" || scenario == "resident_closed" || scenario == "ring_failure";
  const int expected = before_hello ? 1 : scenario == "idle_timeout" ? 5 :
      scenario == "partial_request" || scenario == "hello_failure" ? 4 :
      scenario == "invalid_request" ? 3 : 0;
  ck(result == expected, "expected native failure exit code");
  const bool render = scenario == "success" || scenario == "capture_failure" ||
                      scenario == "encode_throw" || scenario == "write_false";
  ck(replies == (render ? 1 : 0), "only complete accepted requests receive one receipt");
  ck(captures == (render ? 1 : 0), "protocol/setup failures cannot start capture");
  ck(commits == (scenario == "success" ? 1 : 0), "failed requests cannot publish stale output");
  if (render) ck(status == (scenario == "success" ? kStatusOk :
      scenario == "capture_failure" ? kStatusTimeout : kStatusJsErr), "failure receipt status");
  if (!before_hello && scenario != "hello_failure") ck(received_fd == -2, "hello fd was owned and closed");
  std::printf("EXTRACTED_SERVE_FAILURE_PASS scenario=%s rc=%d closes=%d shutdowns=%d captures=%d commits=%d\n",
      scenario.c_str(), result, closes, shutdowns, captures, commits);
  return 0;
}
#else  // VTX_MODEL_ONESHOT
using Clock = std::chrono::steady_clock;
constexpr const char* kWorkerImplementation = "oneshot-model";
#define CEF_VERSION "fake-cef"
#define VTX_CHROME_VERSION_STRING "fake-chromium"
struct RunOptions {
  Options cef;
  std::string url = "file:///one-shot", out_png, out_json;
  int timeout_ms = 3, begin_frames = 3, hold_ms = 20;
};
std::string BuildPageProbeJs() { return "page"; }
std::string BuildGpuPageProbeJs() { return "gpu"; }
void CefBrowser::ExecuteJavaScript(const std::string& js, const std::string&, int) {
  GetHost();
  const bool gpu = js == "gpu";
  if (gpu) ++gpu_probes; else ++page_probes;
  if (scenario == (gpu ? "gpu_probe_closed" : "probe_closed")) {
    tasks.emplace_back(tick, [state = state] { NaturalClose(state); });
  } else if (scenario != "probe_timeout" || gpu) {
    tasks.emplace_back(tick, [state = state, gpu] {
      (gpu ? state->gpu_page_probe : state->page_probe) = "{}";
    });
  }
}
void CefBrowser::SendExternalBeginFrame() {
  GetHost();
  ++begin_frames;
  if (scenario == "frame_closed" || (scenario == "hold_closed" && begin_frames > 1)) {
    tasks.emplace_back(tick, [state = state] { NaturalClose(state); });
  } else if (scenario != "no_paint") {
    tasks.emplace_back(tick, [state = state] {
      ++state->paints;
      state->width = state->height = 16;
      state->buffer.assign(16 * 16 * 4, 255);
    });
  }
}
void UnpremultiplyToRgba(const uint8_t* src, size_t size, uint8_t* dst) {
  std::copy(src, src + size, dst);
}
bool WritePngRgba(const std::string& path, const uint8_t*, int width, int height,
                  const PngEncodeOptions&) {
  ++encodes;
  ck(width == 16 && height == 16, "normal screenshot geometry survives");
  if (scenario == "write_false") return false;
  std::ofstream file(path, std::ios::binary);
  file << "mock PNG sink";
  ++commits;
  return file.good();
}
// PRODUCTION_ONESHOT_JSON_HELPERS
// PRODUCTION_ONESHOT_CLOSE_HELPER
int OneShot(const RunOptions& opt, const std::filesystem::path& root_cache,
            int argc, char** argv) {
  const bool owns_cache = false;
  const PngEncodeOptions png_opt;
  const std::map<std::string, std::string> launch_env;
// PRODUCTION_ONESHOT_CONTROL_FLOW
int test(int argc, char** argv) {
  ck(argc == 3, "one-shot scenario and private temp root");
  scenario = argv[1];
  RunOptions options;
  options.out_png = std::string(argv[2]) + "/frame.png";
  options.out_json = std::string(argv[2]) + "/result.json";
  const int result = OneShot(options, argv[2], argc, argv);
  ck(shutdowns == (scenario == "initialize_false" ? 0 : 1), "one safe shutdown");
  ck(live == 0 && tasks.empty() && clients == 0, "all CEF owners and callbacks drained");
  ck(closes + natural_closes == creations, "every created browser closes exactly once");
  const bool early = scenario == "initialize_false" || scenario == "create_false" ||
      scenario == "create_late" || scenario == "create_closed" || scenario == "load_timeout";
  const bool terminal = scenario.find("_closed") != std::string::npos;
  const int expected = scenario == "create_late" || scenario == "load_timeout" ? 124 :
      early || terminal || scenario == "write_false" || scenario == "no_paint" ? 1 : 0;
  ck(result == expected, "one-shot preserves failure status");
  const bool before_image = early || scenario == "load_closed" || scenario == "settle_closed" ||
      scenario == "probe_closed" || scenario == "frame_closed" || scenario == "no_paint";
  ck(encodes == (before_image ? 0 : 1), "terminal early callback cannot publish screenshot");
  ck(commits == (before_image || scenario == "write_false" ? 0 : 1), "failed PNG never commits");
  if (terminal) ck(tick < 1000000, "terminal callback aborts without waiting for timeout");
  if (!early && !terminal) {
    std::ifstream file(options.out_json);
    const std::string json((std::istreambuf_iterator<char>(file)), {});
    ck(json.find("\"exit_code\": " + std::to_string(expected)) != std::string::npos,
       "normal JSON result retained");
  }
  if (scenario == "success") {
    ck(page_probes == 1 && gpu_probes == 1 && begin_frames >= 2,
       "normal screenshot, GPU probe and hold behavior retained");
  }
  std::printf("EXTRACTED_ONESHOT_PASS scenario=%s rc=%d closes=%d shutdowns=%d commits=%d\n",
      scenario.c_str(), result, closes, shutdowns, commits);
  return 0;
}
#endif

}
int main(int argc, char** argv) {
  try { return vtx_cef::test(argc, argv); }
  catch (const std::exception& error) {
    std::fprintf(stderr, "FAIL %s\n", error.what());
    return 91;
  }
}

#else
#error "Select one VTX_MODEL_* model through the exact contract test runner."
#endif
