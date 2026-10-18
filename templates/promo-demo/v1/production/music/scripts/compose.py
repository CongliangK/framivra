#!/usr/bin/env python3
"""Project-only original 120-second score. Does not modify Scoreweft."""
from pathlib import Path
import argparse, json, hashlib, math
ROOT=Path(__file__).resolve().parents[1]
SR=48000; SPF=1600; N=5760000
PALETTE={
 'nylon':dict(program='nylon',params=dict(bright=.42,pick=.23,body=.6,decay_ref=1.8),role='T17',gain_db=-1.,pan=-.18),
 'bass':dict(program='bass',params={},role='T18',gain_db=-16.5,pan=0.),
 'kick':dict(program='kick',params={},role='T18',gain_db=-20.0,pan=0.),
 'knock':dict(program='fm_ep',params=dict(ratio=2.71,idx=1.05,bright_decay=.025),role='T19',gain_db=-16.5,pan=.22),
 'motif':dict(program='fm_ep',params=dict(ratio=1.,idx=.70,bright_decay=.055),role='T20',gain_db=-12.5,pan=.12),
}
CH={
 'D6':[50,57,62,66,71], 'Bm7':[47,54,62,66,69], 'G9':[43,55,62,66,69],
 'A9':[45,57,61,64,71], 'Em9':[40,55,62,66,71], 'Fsm':[42,57,61,66,69],
 'Dsus':[50,57,62,64,69], 'Asus':[45,57,62,64,69], 'G6':[43,55,59,62,64],
 'Dmaj':[50,57,61,66,69], 'B7':[47,54,59,63,69], 'A6':[45,57,61,66,69],
}
# Explicitly chosen, non-looped 60-bar harmonic form. None means no arpeggio grid.
FORM=[
 None,None,None,None,None,
 'D6','Bm7','G9','A9','Em9','Fsm','Asus',
 'D6','Dmaj','Bm7','G6','Em9','A9','Asus','Asus',
 None,None,'D6','G9','A6','D6','Em9','Asus',
 None,None,None,None,None,None,None,None,
 'D6','Fsm','G9','A9','D6','Bm7','G9','D6','G6','D6',
 None,None,None,None,None,None,None,None,
 None,None,None,None,None,None,
]
assert len(FORM)==60
SCENES=[('S1',0,300),('S2',300,720),('S3',720,1200),('S4',1200,1680),('S5',1680,2160),('S6',2160,2760),('S7',2760,3240),('S8',3240,3600)]
PATTERNS=[
 [0,.75,1.5,2.5,3.25], [0,.5,1.75,2.25,3.5], [0,1.,1.5,2.75],
 [.25,1.,1.75,2.5,3.5], [0,.75,2.,2.5], [0,.5,1.25,2.,3.],
 [0,1.5,2.25,3.0], [.25,.75,1.5,2.,2.75,3.5],
]
def dump(p,x): p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(x,indent=2,ensure_ascii=False)+'\n')
def build():
 events=[]
 def add(voice,f,p,d=.25,v=72,g=0.,pan=None,tag='',cue=None):
  sample=round(f*SPF)
  assert 0<=sample<N and .09<=d<=3
  row=dict(id=f'e{len(events):04d}',voice=voice,role=PALETTE[voice]['role'],sample=sample,
   frame=f,seconds=sample/SR,pitch=p,duration_seconds=d,velocity=v,gain_db=g,
   pan=PALETTE[voice]['pan'] if pan is None else pan,phrase=tag)
  if cue: row['picture_cue']=cue
  events.append(row)
 def pluck(f,p,v=72,d=.25,g=0.,tag=''):add('nylon',f,p,d,v,g,tag=tag)
 def chord(f,name,v=62,d=.6,spread=2.0,g=0.,tag=''):
  for j,p in enumerate(CH[name][1:]): pluck(f+j*spread,p,v-[0,7,4,12][j],d,g,tag)
 def mot(f,notes,steps=15,v=76,tag='',cue=None,g=0):
  for i,p in enumerate(notes): add('motif',f+i*steps,p,[.32,.26,.54,.4][i%4],v-[0,7,3,10][i%4],g,tag=tag,cue=cue if i==0 else None)
 # Four contacts are accompanied by quiet fragments, not a regular beat bed.
 for f,p,v in [(0,50,48),(36,57,48),(82,64,51),(125,66,46)]:pluck(f,p,v,.35,-3,'opening question')
 mot(190,[74,78,81],15,79,'first lift','lift')
 add('motif',255,78,.3,59,tag='answer before wing cover')
 pluck(222,57,55,.3,-2,'opening release');pluck(251,62,48,.25,-2,'opening release')
 # Every bar uses its own phrase pattern, voicing order, velocity contour, and role entry.
 for b,name in enumerate(FORM):
  if name is None:continue
  f=b*60;notes=CH[name]
  if b<12: section='find materials';energy=.82+.025*(b-5);pattern=PATTERNS[(b-5)%8]
  elif b<20:section='assembly';energy=1.;pattern=PATTERNS[(b+1)%8]
  elif b<28:section='flight and release';energy=.94 if b<25 else .7;pattern=PATTERNS[(b+3)%8]
  else: section='world unfolds';energy=[.75,.84,.93,1.02,1.14,1.08,.98,.87,.71,.52][b-36];pattern=PATTERNS[(b+2)%8]
  # Source-read (35–38 s), late mechanism release, and world hold intentionally thin.
  if b in (17,18): pattern=[0,1.75];energy=.64
  if b in (26,27):pattern=[.5,2.5];energy=.60
  if b==45:pattern=[0];energy=.48
  order=([0,2,1,3,4,2] if b%3==0 else [0,1,3,2,4,1] if b%3==1 else [1,0,2,4,3,2])
  for j,pos in enumerate(pattern):
   pitch=notes[order[j%6]]
   if b in (40,41,42) and j>1:pitch+=12
   vel=round([78,58,71,63,74,54][j%6]*energy)
   d=[.25,.18,.3,.22][(b+j)%4]
   pluck(f+pos*15,pitch,vel,d,tag=f'{section}: bar {b+1}')
  # Occasional offbeat answering dyad, never an unchanged four-bar loop.
  if b in (6,9,13,16,22,24,37,39,40,42):
   pluck(f+41.25,notes[3],round(48*energy),.20,-2,f'{section}: light answer')
  # Bass and dry pulse are one semantic rhythmic stem.
  if b not in (17,18,26,27,45):
   root=notes[0]-12
   if root<28:root+=12
   pulses=([0,2.5] if b<8 else [0,1.75,3] if b<12 else [0,1.5,2.75] if b<17 else [0,2.5])
   if b in (22,23,24):pulses=[0,2.]
   if 36<=b<=39:pulses=[0,2.5] if b<38 else [0,1.5,2.75]
   if 40<=b<=42:pulses=[0,1.,2.,3.25]
   if b in (43,44):pulses=[0,2.5] if b==43 else [0]
   for j,beat in enumerate(pulses):
    add('bass',f+beat*15,root+(12 if j==1 and b%2 else 0),[.34,.24,.3][j%3],round([77,52,64,58][j]*energy),tag=f'{section}: bass phrase')
    # Bass may anticipate; kick answers later and does not duplicate every note.
    if j!=1 or b in (13,15,40,42):add('kick',f+beat*15,36,1.2,round([82,45,67,52][j]*energy),tag=f'{section}: low pulse')
 # Character phrases bridge story actions. They are not a continuous lead loop.
 mot(390,[78,76,74],11.25,65,'card discovery answer')
 mot(630,[71,74,78],15,70,'ribbon pull')
 mot(810,[69,74,78],22.5,68,'arch rising')
 mot(1015,[73,76,81],11.25,68,'gate signal answer')
 # Closed gate, tension and release: no regular rhythm before 44 seconds.
 pluck(1200,57,58,.6,-2,'closed gate suspension');pluck(1230,64,49,.5,-2,'closed gate suspension')
 add('bass',1260,33,.52,55,-3,tag='compression tension')
 pluck(1282.5,61,45,.3,-2,'hold breath');pluck(1305,64,53,.18,-1,'lowest compression')
 mot(1320,[74,78,81],15,84,'single flight launch','launch')
 add('motif',1434,86,.65,75,-1,tag='flight apex',cue='cross_apex')
 mot(1515,[81,78,74],10,65,'landing answer')
 # S5: exposed gestures and two soft harmonic questions, with no kick grid.
 chord(1680,'G6',51,.5,3,-2,'paper reveal')
 pluck(1740,62,45,.40,-4,'space before first tap')
 pluck(1830,59,43,.45,-4,'space before second gesture')
 chord(1920,'Dsus',47,.45,3,-3,'third gesture preparation')
 # Relative quiet is real PCM, not a drawn waveform. One low-amplitude note keeps texture.
 pluck(2010,69,20,1.6,-16,'intentional quiet glide')
 pluck(2100,50,48,.45,-2,'re-enter from silence');pluck(2130,57,54,.3,-1,'first fold')
 # Expanded opening motif gives the film its only maximum at actual unfolding.
 mot(2160,[74,78],22.5,62,'world-build hint',g=-2)
 mot(2280,[71,74,78],15,70,'world-build answer')
 mot(2400,[74,78,81,86],15,86,'world-release maximum','F_unfold',g=1)
 mot(2475,[85,81,78],15,75,'expanded return after maximum')
 mot(2580,[79,78,74],22.5,65,'settle world',g=-2)
 chord(2700,'D6',46,1.1,1.5,-5,'remember complete world')
 # Source reading: a suspended line and a deliberately ordered pair of absence gestures.
 for f,p,d,v in [(2775,57,.8,41),(2835,66,.7,42),(2900,62,.6,37),(2940,69,.55,34),
                 (3030,62,.4,36),(3090,66,.5,38),(3150,57,.55,42),(3200,64,.4,38)]:
  pluck(f,p,v,d,-5,'source reading and regrouping')
 add('motif',2970,78,.20,39,-8,tag='frame withdrawal')
 add('motif',3030,78,.22,43,-7,tag='frame restored')
 add('motif',3045,71,.20,35,-8,tag='middle absence begins')
 add('motif',3090,71,.22,41,-7,tag='middle restored')
 # Single final return phrase, then two quiet cadential answers rather than a new chorus.
 mot(3240,[81,78,74],22.5,63,'return to frame',g=-2)
 chord(3300,'G6',49,.6,2,-2,'approach perch')
 add('bass',3300,31,.5,43,-6,tag='final low motion')
 mot(3360,[74,78,81],15,62,'brand answer','brand',g=-3)
 chord(3412.5,'A6',45,.65,2,-3,'cadence approach')
 chord(3480,'D6',43,2.35,1.25,-3,'final warm resolution')
 add('bass',3480,38,1.4,35,-8,tag='final low resolution')
 add('motif',3480,74,2.55,46,-4,tag='last resolved signature',cue='resolve')
 # Tactile library uses the same FM source but different pitch, duration and strength.
 cues=json.loads((ROOT/'inputs/cues.json').read_text())
 contacts=[c for c in cues['contacts'] if c['audio_role']=='T19']
 shaped={
 'cut':(74,.12,86,-.24),'body':(62,.20,88,.05),'trim':(81,.10,74,-.10),'eye':(86,.09,65,.24),
 'tap_tongue':(74,.18,87,-.20),'beak_flip':(81,.25,78,.20),'beak_F':(66,.30,89,.04),
 'buckle_seated':(78,.11,40,.09), 'F_tension':(69,.20,63,-.1),'P_A_starts':(74,.26,70,.12),
 'land':(62,.23,81,.05),'carrier_stop':(62,.25,84,0.),'F_beam_contact':(74,.16,75,.14),
 'F_withdraw':(81,.12,48,-.18),'F_restore':(81,.13,52,-.18),'middle_restore':(74,.16,52,.12),
 'perch':(74,.18,65,.05),
 }
 for i,c in enumerate(contacts):
  pitch,d,v,pan=shaped.get(c['id'],([74,78,81,69,76][i%5],[.12,.18,.15][i%3],[64,58,70,61][i%4],[-.18,.0,.18][i%3]))
  add('knock',c['frame'],pitch,d,v,pan=pan,tag='physical action: '+c['meaning'],cue=c['id'])
 # R04: separate the low nylon attacks from bass; keep cue times and tactile articulation.
 nylon_starts={e['sample'] for e in events if e['voice']=='nylon'}
 for e in events:
  if e['voice']=='nylon' and e['pitch']<55:e['gain_db']-=3.0
  if e['voice']=='kick' and e['sample'] in nylon_starts:e['gain_db']-=1.5
 events.sort(key=lambda e:(e['sample'],e['role'],e['id']))
 for i,e in enumerate(events):e['id']=f'e{i:04d}'
 assert all(e['sample']==e['frame']*SPF for e in events)
 # No authored event can produce an unfinished natural tail at the film edge.
 for e in events:
  tail=.9 if e['voice']=='nylon' else 0
  assert e['sample']+round((e['duration_seconds']+tail)*SR) < N,(e,'crosses end')
 return dict(format='framivra-original-score/1',title='One Frame Workshop',development_status='USER_REQUESTED_FULL_ARRANGEMENT; FINAL_LISTENING_PENDING',
   format_audio=dict(sample_rate=SR,channels=2,sample_frames=N,fps=30,samples_per_frame=SPF),
   clock=dict(base_bpm=120,description='120 BPM reference grid with half-time, free gaps, off-grid fixed contacts, and phrase-density changes; no claim of constant perceptual tempo'),
   voice_processing={'nylon':{'attack_start_gain':.35,'attack_recovery_ms_below55':55,'attack_recovery_ms_atleast55':35,'curve':'smoothstep','purpose':'Reduce pick transient before role mixing; original synth parameters unchanged'}},palette=PALETTE,scenes=[dict(id=s,start_frame=a,end_frame=b) for s,a,b in SCENES],
   harmonic_form=FORM,events=events,
   render_contract=dict(unit_velocity=100,event_velocity_application='Linear post-one-shot gain velocity/100, before role sum. Unit one-shot generated by unchanged Scoreweft synth.',
    mix='unity sum of four jointly mastered PCM24 stems',no_loop=True,no_core_edits=True,
    final_fade=[118.5,119.9],master_target_lufs=-14.5,master_target_true_peak_db=-2.0,final_delivery_trim_db=-.3),
   held_windows=[dict(name='recognition',frames=[163,190]),dict(name='quiet glide',frames=[2010,2100]),
    dict(name='whole world',frames=[2700,2760]),dict(name='source reading',frames=[2760,3240]),dict(name='ending',frames=[3480,3600])])
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,default=ROOT/'score.json');args=ap.parse_args();x=build();dump(args.out,x)
 # Secondary editable note projection: it does not replace the exact-sample recipe.
 tracks=[]
 for voice,c in PALETTE.items():
  tracks.append(dict(name=voice,engine='synth',instrument=c['program'],params={'params':c['params']},
   notes=[[e['pitch'],e['seconds']*2,e['duration_seconds']*2,e['velocity']] for e in x['events'] if e['voice']==voice],
   gain_db=c['gain_db'],pan=c['pan'],bus=[],clip=dict(start_beat=0,end_beat=240)))
 p=dict(name=x['title']+' (editing projection)',meta=dict(spec_version='2',sr=SR,channels=2,bpm=120,key='D major',timesig='4/4',duration_hint=120),
 tracks=tracks,sections=[dict(name=s,start_beat=a/15,length_beat=(b-a)/15) for s,a,b in SCENES],automation=[],
 master=dict(chain='loudnorm',target_lufs=-14.5,true_peak_db=-2,qc=dict(duration_min_s=120,silent_window_max_s=2.5)))
 dump(args.out.with_name('scoreweft-editable-projection.json'),p)
 print(json.dumps(dict(events=len(x['events']),roles={r:sum(e['role']==r for e in x['events']) for r in ['T17','T18','T19','T20']},output=str(args.out))))
if __name__=='__main__':main()
