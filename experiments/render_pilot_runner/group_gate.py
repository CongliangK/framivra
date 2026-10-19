"""Experimental pre-capture group-state check; keeps production navigation intact."""
from contextlib import contextmanager
from urllib.parse import urlparse, parse_qs
from .contract import require

STATE_EXPRESSION = """(()=>{const s=window.__promo_gfx_group_status;const failure=document.getElementById('group-failure');return {status:s&&s.status,globalFrame:s&&s.globalFrame,query:location.search,ready:document.readyState,failureHidden:!!failure&&getComputedStyle(failure).display==='none',members:['T08','T07'].map(id=>{const e=document.getElementById('group-'+id);return e?{id,active:e.getAttribute('data-active'),global:e.getAttribute('data-global-frame'),local:e.getAttribute('data-frame'),status:e.getAttribute('data-status')}:null})}})()"""


def check_state(state, navigation_url, allowed_frames):
    query=parse_qs(urlparse(navigation_url or '').query,keep_blank_values=True)
    require(set(query)=={'f'} and len(query['f'])==1,'Group request query mismatch')
    value=query['f'][0]
    require(value.isdigit() and str(int(value))==value,'Invalid group requested frame')
    frame=int(value)
    require(frame in allowed_frames,'Unrequested group capture')
    require(isinstance(state,dict) and state.get('status')=='RENDERED_UNVERIFIED'
            and type(state.get('globalFrame')) is int and state['globalFrame']==frame,
            'Group error/missing/stale state')
    require(state.get('query')=='?f='+str(frame) and state.get('ready')=='complete'
            and state.get('failureHidden') is True,'Group not ready or failure plate visible')
    members=state.get('members')
    require(isinstance(members,list) and len(members)==2,'Missing group member state')
    for m,ident,start in zip(members,('T08','T07'),(18,0)):
        active=frame>=start
        require(isinstance(m,dict) and m.get('id')==ident and m.get('global')==str(frame)
                and m.get('active')==str(active).lower(),'Group member activation/global mismatch')
        if active:
            require(m.get('local')==str(frame-start) and m.get('status')=='scene-implemented-unverified',
                    'Group member local frame/implementation mismatch')
        else:
            require(m.get('local') is None and m.get('status') is None,'Inactive member rendered')
    return frame


@contextmanager
def group_capture_gate(gfx,trace,allowed_frames,observations):
    original=gfx.Tab
    class GroupCheckedTab(original):
        def call(self,method,timeout=30,**params):
            if method=='Page.captureScreenshot':
                try:
                    require(not trace.errors,'Group arm already failed')
                    state=self.eval(STATE_EXPRESSION)
                    frame=check_state(state,getattr(self,'_pilot_url',None),allowed_frames)
                    observations.append({'global':frame,'status':'CHECKED_BEFORE_CAPTURE'})
                except BaseException as error:
                    observations.append({'status':'REJECTED_BEFORE_CAPTURE','error':str(error)})
                    trace.fail('group-pre-capture-state',error)
                    raise
            return super().call(method,timeout=timeout,**params)
    gfx.Tab=GroupCheckedTab
    try:yield
    finally:gfx.Tab=original
