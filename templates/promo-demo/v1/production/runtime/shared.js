/* Framivra 120-second editable source candidate. Independent review and actual engine acceptance remain separate.
MIT License

Copyright (c) 2026 ruochongliang

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
*/
(function(global){'use strict';
const factories={"assets/boundaries.json": function(module,exports,require){
module.exports = {"format":"framivra-boundary-contract/1","status":"DESIGN_TARGETS; final dynamic/pixel verification unrun","unit":"1280x720 author coordinates","pairs":[{"frames":[299,300],"checkpoint":"wing-cover","required_samples":[298,299,300,301],"persistent_state":["B wing shape/color/direction"],"owners":["T07","T08"],"camera_contract":"Opening foreground override is identical on299/300. Covered rebase may occur only underneath full wing.","R03_svg_pair":["round03/boundaries/f0299.svg","round03/boundaries/f0300.svg"],"geometry_rule":"Both boundary frames resolve one shared checkpoint. Compare actual rendered frames and two-frame motion on each side; equality of source IDs is insufficient.","status":"ACTUAL_FRAMES_UNRUN"},{"frames":[719,720],"checkpoint":"three-ribbons","required_samples":[718,719,720,721],"persistent_state":["A/L/R order","single grasp","F folded","P-C receivers already visible"],"owners":["T03","T04","T05","T06","T07","T08","T11"],"camera_contract":"No camera reset. Continue same diagonal camera/pose through this boundary.","R03_svg_pair":["round03/boundaries/f0719.svg","round03/boundaries/f0720.svg"],"geometry_rule":"Both boundary frames resolve one shared checkpoint. Compare actual rendered frames and two-frame motion on each side; equality of source IDs is insufficient.","status":"ACTUAL_FRAMES_UNRUN"},{"frames":[1199,1200],"checkpoint":"closed-gate","required_samples":[1198,1199,1200,1201],"persistent_state":["G on P-C receivers","B at left origin","F on gate","bar closing"],"owners":["T02","T03","T04","T05","T06","T07","T08"],"camera_contract":"Pillar occlusion finishes before1199. Side view same at1199/1200.","R03_svg_pair":["round03/boundaries/f1199.svg","round03/boundaries/f1200.svg"],"geometry_rule":"Both boundary frames resolve one shared checkpoint. Compare actual rendered frames and two-frame motion on each side; equality of source IDs is insufficient.","status":"ACTUAL_FRAMES_UNRUN"},{"frames":[1679,1680],"checkpoint":"released-reel","required_samples":[1678,1679,1680,1681],"persistent_state":["Q unlocked and feed moving","F on free end","B settled","upright G on same P-C","shared P-A hinge"],"owners":["T02","T03","T04","T05","T06","T07","T08"],"camera_contract":"Paper-level camera state carries through; Q velocity does not reset.","R03_svg_pair":["round03/boundaries/f1679.svg","round03/boundaries/f1680.svg"],"geometry_rule":"Both boundary frames resolve one shared checkpoint. Compare actual rendered frames and two-frame motion on each side; equality of source IDs is insufficient.","status":"ACTUAL_FRAMES_UNRUN"},{"frames":[2159,2160],"checkpoint":"first-fold","required_samples":[2158,2159,2160,2161],"persistent_state":["same buckle","Q paper meets P-A","B pulls F","G fixed on P-C","P-C not yet sliding"],"owners":["T02","T03","T04","T05","T06","T07","T08"],"camera_contract":"Same first-fold checkpoint and tangent; no world crossfade.","R03_svg_pair":["round03/boundaries/f2159.svg","round03/boundaries/f2160.svg"],"geometry_rule":"Both boundary frames resolve one shared checkpoint. Compare actual rendered frames and two-frame motion on each side; equality of source IDs is insufficient.","status":"ACTUAL_FRAMES_UNRUN"},{"frames":[2759,2760],"checkpoint":"complete-world","required_samples":[2758,2759,2760,2761],"persistent_state":["all S6 objects","F complete","same B/operator positions","P-A/P-B settled"],"owners":["T02","T03","T04","T05","T06","T07","T08","T09"],"camera_contract":"Camera settled. S7 offsets are exactly0 at2760, separation begins after.","R03_svg_pair":["round03/boundaries/f2759.svg","round03/boundaries/f2760.svg"],"geometry_rule":"Both boundary frames resolve one shared checkpoint. Compare actual rendered frames and two-frame motion on each side; equality of source IDs is insufficient.","status":"ACTUAL_FRAMES_UNRUN"},{"frames":[3239,3240],"checkpoint":"ending-bridge","required_samples":[3238,3239,3240,3241],"persistent_state":["reassembled miniature world","one F","same descending B","source slips exited"],"owners":["T02","T03","T04","T05","T06","T07","T08","T09"],"camera_contract":"One common retreat; frame/body transform identical at3239/3240.","R03_svg_pair":["round03/boundaries/f3239.svg","round03/boundaries/f3240.svg"],"geometry_rule":"Both boundary frames resolve one shared checkpoint. Compare actual rendered frames and two-frame motion on each side; equality of source IDs is insufficient.","status":"ACTUAL_FRAMES_UNRUN"}],"rig_rules":{"B":"One global B rig returns transform, wing articulation, feet state and resolved occluders. T07/T08 use it unchanged; only drawing ownership differs.","G_P_C":"P-C root carries receivers, A, R, L mount/linkage and gate. After respective seating cues all mounting coordinates remain fixed. G is never re-created or independently rescaled during2250–2400.","fixed_W_guide":"World-fixed reference, not a child of P-C. For2250–2400 measure P-C motion relative to W-guide before camera projection. Common camera may move both on screen.","P_hinges":"P-C↔P-A and P-A↔P-B shared edges each have one identity and computed endpoint pair. Connected faces reference those endpoints rather than interpolate duplicate vertices. P-C remains flat; P-A finishes its opening before P-C slides; P-B opens after2520.","F":"One corner set with four persistent IDs and shared edge lengths. Folded reserve must be visible before material is paid out. S7 absence clears all F pixels and its shadows/trails.","Q":"Shelf, stop, axle and feed share one physical assembly. Stop clears at1590 before rotation1620; Q remains in T04 through3600 and never becomes the original red-stock reel.","S7":"Apply offsets to original groups, not redrawn illustration. At full separation: back(-70,-54), middle(-54,-2), front(+18,+25), operator(-54,-2), F(+30,-7), B(+100,-37). Hold other states2940–3090.","S8":"Miniature world transforms original T02/T03/T04/T06/T09 only; exclude B and F before scaling. Final targets F[140,181,1000,397], B[1053,143,.8,0,6], foot y181.4, frame top181."},"camera_design":{"S1":"Use exact embedded opening curves through299; do not reinterpret contacts. Foreground approach uses source frameState projection.","S2_to_S6":"R03 poses are screen-space composition targets; solve world-rig and shared camera together. Do not interpolate each screen polygon as a different world or independently reset pages at scenes. Side→top and top→side rebases only behind the stated real card/pillar occlusion.","S4":"Keep start/platform/gate in view through1260. Camera follow displacement must be smaller than B displacement during flight so the arc remains visible. One convex flight arc1320–1515; finite settle1515–1560.","S6":"P-C slide2250–2400 occurs in fixed W-guide world coordinates. Camera retreats as world opens; dynamic scale/translation must be one shared function. No camera angle/perspective/3D added.","S7":"No camera motion2940–3090; complete-world state is the baseline.","S8":"Only small continuous retreat3240–3420; camera and world fully settled by3480.","unresolved_proof":"R06 must solve interpolation against these endpoint contracts and test all frames for shared hinges/contact order. R03 static targets do not prove a rigid-motion solution; if a target conflicts, make a bounded reviewed design correction, never hide a jump."},"R03_contact_anchors":{"S2_wing_card":{"bird_translation":[765,465],"scale":1.5,"local_wing_tip":[-91,-100],"world_contact":[628.5,315]},"S5_beak_F":{"bird_translation":[670,449],"scale":0.8,"world_contact":[782,422.6]},"S5_F_buckle_P_A":{"f1950":{"F_lower_endpoint":[788.52223994,447.34133082],"shared_buckle_P_A_anchor":[792,463]},"f2159_2160":{"F_lower_endpoint":[879.17140594,378.10166427],"shared_buckle_P_A_anchor":[867,360]},"connection":"Continuous 5-unit red paper tongue joins the exact black buckle/P-A anchor; beak-to-F contact retained."},"S8_foot_F":{"bird_translation":[1053,143],"scale":0.8,"foot_baseline":181.4,"frame_top":181}},"S5_connection_rule":"At f1950 the F lower tongue and buckle/P-A anchor are already continuously connected. f1965 is final seating/response, not first contact. Keep a shared material tongue endpoint from the revised f1950 chain into the revised f2159/2160 chain.","S6_support_visibility":{"before_foreground_occlusion":[2280,2400],"occluded_target_frame":2450,"after_foreground_occlusion":[2610,2700],"requirement":"Show both feet seated on their original P-C receivers before and after the temporary foreground fold occlusion; do not treat occlusion as removal or new assembly."}};
},
"assets/cues.json": function(module,exports,require){
module.exports = {"format":"framivra-film-design-cues/1","status":"AUTHOR_CANDIDATE; downstream implementation and independent review pending","project":{"fps":30,"width":1920,"height":1080,"duration_frames":3600,"duration_seconds":120,"viewBox":[0,0,1280,720],"audio_sample_rate":48000,"samples_per_frame":1600,"samples_per_stem":5760000},"scenes":[{"id":"S1","name":"做出鸟","in":0,"out":300,"duration_frames":300,"beats":[{"id":"S1.01.die-descent","in":0,"out":18,"action":"刀模覆盖红纸后接触；头料和嘴片预先在载体上","camera_mode":"close-follow"},{"id":"S1.02.cut-and-waste","in":18,"out":55,"action":"刀抬起；f28 起完整互补废框落走，两片翼留原位","camera_mode":"close-follow"},{"id":"S1.03.body-imprint","in":55,"out":100,"action":"沿输送带到印站；f67 实际印面接触后墨身持续存在","camera_mode":"close-follow"},{"id":"S1.04.trim-head","in":100,"out":141,"action":"f109 切鞋修边，两片互补头料边角随后落走","camera_mode":"close-follow"},{"id":"S1.05.eye-contact","in":141,"out":163,"action":"f150 点眼；工具完全退开后才进入识别停顿","camera_mode":"close-follow"},{"id":"S1.06.recognition-hold","in":163,"out":190,"action":"完整侧影27帧；无标签覆盖，无重复弹跳","camera_mode":"settled-close"},{"id":"S1.07.lift-and-reveal","in":190,"out":264,"action":"先离载体再拉远工坊；操作者一次短回应","camera_mode":"opening-world-reveal"},{"id":"S1.08.wing-cover","in":264,"out":300,"action":"同一翼由现有鸟坐标放大覆画；不是红色矩形","camera_mode":"opening-wing-foreground"}]},{"id":"S2","name":"找到材料","in":300,"out":720,"duration_frames":420,"beats":[{"id":"S2.01.wing-uncover","in":300,"out":345,"action":"f300 承接红翼轮廓，向右上退开；f330 起卡与 F 料槽可见","camera_mode":"covered-rebase-to-side"},{"id":"S2.02.touch-and-flip","in":345,"out":390,"action":"f365 翼A尖接 A 卡右上角；f366 才开始绕左公共边翻卡","camera_mode":"side-touch"},{"id":"S2.03.land-and-reveal-L","in":390,"out":435,"action":"A 卡放平才露 L，R 仍从叠底可见","camera_mode":"side-table"},{"id":"S2.04.push-card-chain","in":435,"out":525,"action":"手推 A，A 接 L，L 接 R；近卡遮挡中转为平面俯视","camera_mode":"card-occluded-turn"},{"id":"S2.05.staggered-cards","in":525,"out":600,"action":"三卡沿邻槽错开行进；鸟绕到右侧自由端","camera_mode":"top-down-pull"},{"id":"S2.06.place-folded-F","in":600,"out":630,"action":"操作者把已在料槽的四角折叠 F 放上汇合纸舌","camera_mode":"top-down-pull"},{"id":"S2.07.pull-ribbons","in":630,"out":690,"action":"f630 卡/带 owner 无缝交接；鸟咬住纸舌将三条带拉出","camera_mode":"pull-wide"},{"id":"S2.08.reveal-receivers","in":690,"out":720,"action":"既有 P-C/折边/接收座在前方显露；抓点和 A/L/R 次序保持","camera_mode":"pull-wide"}]},{"id":"S3","name":"把材料放进时间","in":720,"out":1200,"duration_frames":480,"beats":[{"id":"S3.01.tauten-and-hook","in":720,"out":810,"action":"三条 Bézier 纸带受拉变直；f795 把公共自由端挂圆钉","camera_mode":"ribbon-follow"},{"id":"S3.02.arch-first","in":810,"out":900,"action":"A 转交 T03，左脚先到，右脚悬空；一条真实短源标签沿带排字","camera_mode":"ribbon-gate"},{"id":"S3.03.rail-lock","in":900,"out":960,"action":"R 滑向 P-C，f930 接管且锁脚距；两处真实上下交错裁切","camera_mode":"ribbon-gate"},{"id":"S3.04.signal-opens","in":960,"out":1050,"action":"L 沿杆升起；f1005 转交 T03，f1049 门栓完全打开；引线连带到门","camera_mode":"ribbon-gate"},{"id":"S3.05.hang-F-and-read","in":1050,"out":1140,"action":"鸟挂 F 于门顶；读一条真实 clip 的 src/dst_in/dur；路径文字收平","camera_mode":"source-read"},{"id":"S3.06.marker-becomes-lever","in":1140,"out":1200,"action":"墨标折杆接凸轮；门栓回落；柱遮挡中回侧景，纸带收至 P-C 下","camera_mode":"pillar-occluded-side"}]},{"id":"S4","name":"穿过时机","in":1200,"out":1680,"duration_frames":480,"beats":[{"id":"S4.01.read-three-positions","in":1200,"out":1260,"action":"保留起台、门洞、右落台；关门使鸟停住","camera_mode":"gate-established"},{"id":"S4.02.gate-rise-and-compress","in":1260,"out":1320,"action":"凸轮/连杆先开门；鸟下沉、翼收但保留两红尖；f1305 低点","camera_mode":"gate-anticipation"},{"id":"S4.03.launch-and-catch-F","in":1320,"out":1380,"action":"f1320 蹬离，f1350–1352 翼根碰 F 纸舌，f1353 后门挂点空","camera_mode":"gate-flight"},{"id":"S4.04.cross-gate","in":1380,"out":1470,"action":"单峰飞行弧线，共同 body/wing mask；仅短历史翼影","camera_mode":"gate-flight"},{"id":"S4.05.land-and-settle","in":1470,"out":1560,"action":"f1515 脚先接平台，身体有限前冲；f1545 落稳，F 延迟落下","camera_mode":"landing"},{"id":"S4.06.release-Q","in":1560,"out":1620,"action":"f1572 支撑板下压，f1590 止挡离卷，f1620 卷才开始转","camera_mode":"release-close"},{"id":"S4.07.feed-paper-transition","in":1620,"out":1680,"action":"Q 向左放带，F 与鸟位置保留；镜头降到带高","camera_mode":"paper-level"}]},{"id":"S5","name":"让声音接住动作","in":1680,"out":2160,"duration_frames":480,"beats":[{"id":"S5.01.reveal-real-waveform","in":1680,"out":1770,"action":"先见连续 Q 纸带；f1710 才揭示实际 PCM 的本段 envelope","camera_mode":"paper-tracking"},{"id":"S5.02.tap-tongue","in":1770,"out":1830,"action":"f1800 足尖碰第一纸舌，纸舌压下并有限回起","camera_mode":"paper-tracking"},{"id":"S5.03.open-buckle-cover","in":1830,"out":1890,"action":"f1860 喙拨第二宽纸片，f1875 露连接扣","camera_mode":"paper-tracking"},{"id":"S5.04.connect-and-pull","in":1890,"out":2010,"action":"f1950 喙/F 纸舌与扣/P-A 已连续相接，f1965 完成压紧响应；第三纸边受力张开","camera_mode":"buckle-close"},{"id":"S5.05.quiet-glide","in":2010,"out":2100,"action":"三秒相对稳定滑行，Q 慢下来，G/P-C/折边仍可追溯；实际 PCM 如实低密","camera_mode":"quiet-glide"},{"id":"S5.06.lift-first-fold","in":2100,"out":2160,"action":"f2115 F 带动扣，f2130 P-A 从旧铰边开始开；P-C/G 尚不动","camera_mode":"fold-close"}]},{"id":"S6","name":"展开一个世界","in":2160,"out":2760,"duration_frames":600,"beats":[{"id":"S6.01.flatten-front-wing","in":2160,"out":2250,"action":"P-A 沿同一铰边翻平；P-C 平放、门始终直立","camera_mode":"fold-follow"},{"id":"S6.02.carrier-start","in":2250,"out":2280,"action":"前翼张力传到 P-C；P-C/G 开始相对固定 W-guide 滑动","camera_mode":"fold-follow"},{"id":"S6.03.carrier-slide-and-brace","in":2280,"out":2400,"action":"操作者先接支撑再扶稳；P-C/G 到止点；前中后景遮挡持续改变","camera_mode":"world-pullback"},{"id":"S6.04.unfold-F-climax","in":2400,"out":2520,"action":"唯一最大动作：左下、牵引右上、余两角/边依次展开；鸟从门后到前","camera_mode":"world-release"},{"id":"S6.05.open-back-wing","in":2520,"out":2610,"action":"框接横梁，梁滑开，P-B 沿旧铰边展开；L 随连杆抬到灯位","camera_mode":"world-pullback"},{"id":"S6.06.settle-world","in":2610,"out":2700,"action":"纸面落平、框线收直、局部柔影收窄；鸟共 mask 穿支架再上抬","camera_mode":"world-ease-out"},{"id":"S6.07.remember-complete-world","in":2700,"out":2760,"action":"完整原图60帧，允许小收稳，绝不提前拆层","camera_mode":"complete-world-hold"}]},{"id":"S7","name":"看见它怎样组成","in":2760,"out":3240,"duration_frames":480,"beats":[{"id":"S7.01.pull-source-tab","in":2760,"out":2820,"action":"鸟轻拉框外纸耳，原景开始有限平面分离","camera_mode":"locked-wide"},{"id":"S7.02.separate-and-label","in":2820,"out":2910,"action":"窄剥离 mask；绑定引线和两张真实源笺，标签按间距重排","camera_mode":"locked-wide"},{"id":"S7.03.read-original","in":2910,"out":2970,"action":"分层姿态稳定；一笺加真实字段，一笺只源名；f2940–2969 为比较基准","camera_mode":"locked-wide"},{"id":"S7.04.withdraw-F","in":2970,"out":2985,"action":"F 全部边/角沿右纸缝退出，其他层冻结","camera_mode":"locked-wide"},{"id":"S7.05.F-absent","in":2985,"out":3015,"action":"F 30帧零贡献，不留影/边/拖尾；标签保持","camera_mode":"locked-wide"},{"id":"S7.06.restore-F","in":3015,"out":3030,"action":"沿原路恢复；f3030 F 全部复位","camera_mode":"locked-wide"},{"id":"S7.07.withdraw-middle","in":3030,"out":3045,"action":"完整 T03 含 G、P-C、支撑/灯/影一起退出；不是只藏拱","camera_mode":"locked-wide"},{"id":"S7.08.middle-absent","in":3045,"out":3075,"action":"T03 30帧零贡献；F、鸟、操作者、标签不动","camera_mode":"locked-wide"},{"id":"S7.09.restore-middle","in":3075,"out":3090,"action":"同一路径恢复；f3090 恢复全部中景","camera_mode":"locked-wide"},{"id":"S7.10.reassemble-original","in":3090,"out":3180,"action":"引线/源笺退出，原物件沿路归位；操作者扶边，鸟牵右上角","camera_mode":"locked-wide"},{"id":"S7.11.frame-to-ending","in":3180,"out":3240,"action":"F 扩向片尾边界，世界收回框内，鸟沿右边下降","camera_mode":"ending-bridge"}]},{"id":"S8","name":"归位","in":3240,"out":3600,"duration_frames":360,"beats":[{"id":"S8.01.frame-settle","in":3240,"out":3330,"action":"承接同一框/鸟/世界；左上先停，右下受鸟牵引归位","camera_mode":"ending-small-retreat"},{"id":"S8.02.perch-and-wordmark","in":3330,"out":3420,"action":"f3350 脚接右上框角，框一次小下压复位；f3360 名称开始整体显露","camera_mode":"ending-small-retreat"},{"id":"S8.03.recognize-brand","in":3420,"out":3480,"action":"字标稳定；鸟一次收翼抬头；缩小世界无第二只 B/第二个 F","camera_mode":"ending-hold"},{"id":"S8.04.resolved-hold","in":3480,"out":3600,"action":"四秒安定，末帧完整；音尾必须在3600前解决","camera_mode":"ending-hold"}]}],"contacts":[{"id":"cut","frame":18,"sample_at_48khz":28800,"audio_role":"T19","meaning":"die touches full stock","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"body","frame":67,"sample_at_48khz":107200,"audio_role":"T19","meaning":"body die touches and ink persists","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"trim","frame":109,"sample_at_48khz":174400,"audio_role":"T19","meaning":"two cut shoes touch head stock","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"eye","frame":150,"sample_at_48khz":240000,"audio_role":"T19","meaning":"punch contacts eye point","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"recognize","frame":163,"sample_at_48khz":260800,"audio_role":"T20","meaning":"clear silhouette begins","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"lift","frame":190,"sample_at_48khz":304000,"audio_role":"T20","meaning":"bird leaves support","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"wing_card","frame":365,"sample_at_48khz":584000,"audio_role":"T19","meaning":"wing-A tip reaches card corner","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"push_A","frame":450,"sample_at_48khz":720000,"audio_role":"T19","meaning":"operator hand contacts A","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"A_meets_L","frame":474,"sample_at_48khz":758400,"audio_role":"T19","meaning":"A trailing edge pushes L","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"L_meets_R","frame":498,"sample_at_48khz":796800,"audio_role":"T19","meaning":"L pushes R","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"F_on_strip","frame":615,"sample_at_48khz":984000,"audio_role":"T19","meaning":"reserve frame placed on paper tab","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"grasp","frame":630,"sample_at_48khz":1008000,"audio_role":"T19","meaning":"beak grips common tab","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"hook_receiver","frame":795,"sample_at_48khz":1272000,"audio_role":"T19","meaning":"tab catches receiver pin","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"A_left_seat","frame":855,"sample_at_48khz":1368000,"audio_role":"T19","meaning":"left arch foot contacts seat; right still above","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"R_locks_feet","frame":945,"sample_at_48khz":1512000,"audio_role":"T19","meaning":"R locks two foot positions","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"gate_open","frame":1049,"sample_at_48khz":1678400,"audio_role":"T19","meaning":"L-driven bar reaches passable state","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"F_hang","frame":1100,"sample_at_48khz":1760000,"audio_role":"T19","meaning":"same folded F catches gate top","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"launch","frame":1320,"sample_at_48khz":2112000,"audio_role":"T20","meaning":"support release before wing lift","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"F_wing_catch","frame":1351,"sample_at_48khz":2161600,"audio_role":"T19","meaning":"paper tongue touches wing root","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"cross_apex","frame":1434,"sample_at_48khz":2294400,"audio_role":"T20","meaning":"bird passes high gate safe zone","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"land","frame":1515,"sample_at_48khz":2424000,"audio_role":"T19","meaning":"foot touches right platform","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"plate_down","frame":1572,"sample_at_48khz":2515200,"audio_role":"T19","meaning":"foot pressure lowers support plate","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"stop_clear","frame":1590,"sample_at_48khz":2544000,"audio_role":"T19","meaning":"Q stop clears reel","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"Q_turn","frame":1620,"sample_at_48khz":2592000,"audio_role":"T19","meaning":"reel begins only after release","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"tap_tongue","frame":1800,"sample_at_48khz":2880000,"audio_role":"T19","meaning":"foot touches first paper tongue","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"beak_flip","frame":1860,"sample_at_48khz":2976000,"audio_role":"T19","meaning":"beak flips second wide paper tab","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"beak_F","frame":1950,"sample_at_48khz":3120000,"audio_role":"T19","meaning":"beak touches F tongue at buckle","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"buckle_seated","frame":1965,"sample_at_48khz":3144000,"audio_role":"T19","meaning":"already-connected buckle finishes seating response; first visible continuity is f1950","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"F_tension","frame":2115,"sample_at_48khz":3384000,"audio_role":"T19","meaning":"same frame tongue takes tension","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"P_A_starts","frame":2130,"sample_at_48khz":3408000,"audio_role":"T19","meaning":"first shared fold opens","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"P_C_slide","frame":2250,"sample_at_48khz":3600000,"audio_role":"T19","meaning":"tension reaches carrier along fixed guide","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"operator_brace","frame":2310,"sample_at_48khz":3696000,"audio_role":"T19","meaning":"hand contacts support before settling","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"carrier_stop","frame":2400,"sample_at_48khz":3840000,"audio_role":"T19","meaning":"carrier reaches stop without foot detachment","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"F_unfold","frame":2400,"sample_at_48khz":3840000,"audio_role":"T20","meaning":"maximum single release begins","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"F_beam_contact","frame":2520,"sample_at_48khz":4032000,"audio_role":"T19","meaning":"frame edge meets beam and opens P-B","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"final_support","frame":2600,"sample_at_48khz":4160000,"audio_role":"T19","meaning":"operator seats final support","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"source_tab","frame":2775,"sample_at_48khz":4440000,"audio_role":"T19","meaning":"bird pulls explanation tab","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"F_withdraw","frame":2970,"sample_at_48khz":4752000,"audio_role":"T19","meaning":"frame comparison starts","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"F_restore","frame":3030,"sample_at_48khz":4848000,"audio_role":"T19","meaning":"frame fully restored","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"middle_restore","frame":3090,"sample_at_48khz":4944000,"audio_role":"T19","meaning":"middle fully restored","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"perch","frame":3350,"sample_at_48khz":5360000,"audio_role":"T19","meaning":"feet touch actual frame","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"brand","frame":3360,"sample_at_48khz":5376000,"audio_role":"T20","meaning":"wordmark enters the settled world","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"},{"id":"resolve","frame":3480,"sample_at_48khz":5568000,"audio_role":"T20","meaning":"final four-second resolution begins","audio_status":"EVENT_REQUIREMENT_ONLY_NO_SOUND_CREATED"}],"opening_reference":{"source":"html-opening-preview/candidate-r3/templates/promo-demo/v1/preview/cues.json","sha256":"ea2bd569f0b02f0d4c5d647a0a5448ef7037f9188d93d6ffa8cf575e3ae80d69","embedded":{"fps":30,"duration_frames":300,"viewBox":[0,0,1280,720],"contacts":{"cut":18,"body":67,"trim":109,"eye":150},"windows_inclusive":{"waste":[18,54],"waste_separation":[28,54],"head_offcuts":[109,138],"head_offcut_fall":[112,139],"recognition_hold":[163,189],"lift":[190,218],"world_reveal":[190,239],"approach":[264,299],"wave":[219,245]},"keys":{"hero_x":[[0,280],[12,350],[28,350],[56,650],[77,650],[100,950],[119,950],[141,1250],[189,1250],[239,1490],[260,1510],[299,1510]],"camera_zoom":[[0,2.35],[28,2.05],[70,1.8],[138,1.58],[184,1.5],[238,0.65],[299,0.65]],"camera_x_reveal":[[184,1270],[238,1010],[299,1010]],"camera_y":[[0,401],[138,390],[184,390],[238,326],[299,326]],"bird_y":[[0,440],[189,440],[203,427],[218,335],[239,210],[263,191]],"bird_angle":[[0,0],[192,0],[215,-9],[239,-4],[263,0]],"wing_angle":[[194,0],[205,-22],[216,11],[231,-13],[245,4],[263,0]]},"presses":{"cut":[0,18,32,230],"body":[57,67,79,159],"trim":[99,109,121,1],"eye":[141,150,162,132]},"first_cut_material":{"stock_bounds":[240,336,406,526],"contact_cover":[236,332,410,530],"stock_area":31540,"wing_area":6065,"waste_area":25475},"pose_frames":[0,37,110,176,239,279]},"interval_conversion":"Original opening windows_inclusive [a,b] become [a,b+1) when exported; contacts unchanged."},"shared_runtime_contract":{"geometry_units":"1280x720 author viewBox; uniform output scale1.5","frame_domain":"global integer f in [0,3600); local f in [0,clip.dur)","local_to_global":"global_f = entry.global_start_frame + local_f","scene_frame":"find scene by in <= global_f < out; scene_f = global_f - scene.in","input_sanitization":"Finite numeric input floored, then clamped to [0,dur-1]; NaN/nonfinite =>0. Validate entry start/dur as exact recipe values. Never clamp global_f at299.","determinism":"Pure state from global_f + immutable inputs. No elapsed time, requestAnimationFrame clock, Math.random, or accumulated previous-frame state. Repeated/reverse/shuffled f must produce identical state.","camera":"One cameraFromGlobalFrame result applies to all geometry in the shared world. Intentional foreground wing override [264,345) uses the same B transform on both bird pages. Source slips and subtitles use screen-space safe areas.","mask_contract":"Each HTML page recreates its own SVG definitions from shared resolved world geometry. No cross-document url(#id), no duplicate opaque gate painted on bird pages. Body and wings consume bit-identical B visibility regions; retain page-specific ID prefixes to avoid collisions.","no_new_renderer_protocol":true,"renderer_path":"Keep current navigation-based page protocol; expose existing __vtx_render(local_f) for deterministic testing only. Do not declare navfree eligibility, static spans, beginFrame or canvas-export fast paths."},"pose_targets":{"semantics":"R03 screen-space composition checkpoints [x,y,scale,body_degrees,wingA_degrees]. These are targets, not independently interpolable mechanical vertices. Current R03 review may revise targets.","bird":[{"frame":299,"rig":"opening-reference","target":null},{"frame":300,"rig":"opening-reference","target":null},{"frame":365,"rig":"B","target":[765,465,1.5,0,0]},{"frame":719,"rig":"B","target":[1032,306,0.8,-2,0]},{"frame":720,"rig":"B","target":[1032,306,0.8,-2,0]},{"frame":879,"rig":"B","target":[1032,306,0.8,-2,0]},{"frame":1049,"rig":"B","target":[1032,306,0.8,-2,0]},{"frame":1199,"rig":"B","target":[400,490,0.9,0,0]},{"frame":1200,"rig":"B","target":[400,490,0.9,0,0]},{"frame":1305,"rig":"B","target":[400,489,0.9,8,12]},{"frame":1434,"rig":"B","target":[756,344,1,-5,0]},{"frame":1545,"rig":"B","target":[1005,447,0.82,3,0]},{"frame":1679,"rig":"B","target":[951,450,0.82,0,0]},{"frame":1680,"rig":"B","target":[951,450,0.82,0,0]},{"frame":1950,"rig":"B","target":[670,449,0.8,0,0]},{"frame":2159,"rig":"B","target":[834,346,0.8,-5,0]},{"frame":2160,"rig":"B","target":[834,346,0.8,-5,0]},{"frame":2450,"rig":"B","target":[996,269,0.82,-9,0]},{"frame":2759,"rig":"B","target":[850,245,0.86,-3,0]},{"frame":2760,"rig":"B","target":[850,245,0.86,-3,0]},{"frame":2940,"rig":"B","target":[950,208,0.86,-3,0]},{"frame":2995,"rig":"B","target":[950,208,0.86,-3,0]},{"frame":3060,"rig":"B","target":[950,208,0.86,-3,0]},{"frame":3239,"rig":"B","target":[1117,404,0.64,78,0]},{"frame":3240,"rig":"B","target":[1117,404,0.64,78,0]},{"frame":3599,"rig":"B","target":[1053,143,0.8,0,6]}]},"owner_handoffs":[{"object":"stock→wings","frame":18,"outgoing":"T06","incoming":"T08","rule":"T06 stops solid red stock at f18 and owns complement only; T08 begins cut wings at f18. Tool covers contact region."},{"object":"A/L/R cards→ribbons","frame":630,"outgoing":"T10","incoming":"T11","rule":"One identical card/glyph silhouette at transfer; continued tail reserve unfolds. T10 last local299 is global629; T11 local0 is630."},{"object":"A-arch","frame":810,"outgoing":"T11","incoming":"T03","rule":"Transfer same rigid glyph while it starts rising; carrier stays T11."},{"object":"R-rail","frame":930,"outgoing":"T11","incoming":"T03","rule":"Transfer at seating movement; after f945 local mounting to P-C is invariant."},{"object":"L-signal","frame":1005,"outgoing":"T11","incoming":"T03","rule":"Transfer on the same connecting rod; no second cap."},{"object":"residual physical ribbons","frame":1200,"outgoing":"T11","incoming":"T04","rule":"All remaining strips have settled into physical feed/transport before exit. T04 continuation uses identical shared ribbon endpoint geometry at1199/1200, never an extra R glyph."}],"explanatory_withdrawals":[{"track":"T05","exit":[2970,2985],"zero_contribution":[2985,3015],"return":[3015,3030],"restored_frame":3030,"sample_frames":[2969,2995,3030]},{"track":"T03","exit":[3030,3045],"zero_contribution":[3045,3075],"return":[3075,3090],"restored_frame":3090,"sample_frames":[3030,3060,3090]}],"global_holds":[{"in":163,"out":190,"meaning":"bird recognition; no titles"},{"in":2010,"out":2100,"meaning":"quiet glide; not all layers absent"},{"in":2700,"out":2760,"meaning":"complete-world memory; no early separation"},{"in":2940,"out":3090,"meaning":"all nonselected visual state/camera/labels frozen during ordered comparisons"},{"in":3480,"out":3600,"meaning":"resolved readable ending; may be fully static after last settle"}],"captions":[{"in":195,"out":255,"text":"从一张纸，做出一个画面。"},{"in":420,"out":600,"text":"把散开的材料，放到一起。"},{"in":900,"out":1080,"text":"把每一层，放进时间。"},{"in":1410,"out":1590,"text":"动作，发生在该发生的那一帧。"},{"in":1830,"out":2010,"text":"让声音，接住每次变化。"},{"in":2460,"out":2640,"text":"让不同的层，组成同一个世界。"},{"in":2940,"out":3120,"text":"这幅画面，来自你能修改的工程。"},{"in":3330,"out":3450,"text":"让画面，各就各位。"}],"audio_status":"FULL_SCORE_UNAPPROVED; abstract event/stem requirements only","workload_status":"Nominal scheduled frames only; no GPU, memory, elapsed-time or actual pixel claims"};
},
"assets/fonts/brand-glyphs.json": function(module,exports,require){
module.exports = {"format":"framivra-glyph-paths/1","family":"Noto Sans SC","style":"SemiBold","weight":600,"reviewStatus":"AUTHOR_CANDIDATE_NOT_INDEPENDENTLY_APPROVED","unitsPerEm":1000,"ascent":1160,"descent":-288,"provenance":{"fontBasename":"NotoSansSC-VF.ttf","fontSha256":"a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da","sourceKind":"existing-local-approved-R03-reference-font","sourceRelativePath":"visual-design/fonts/NotoSansSC-VF.ttf","license":"OFL-1.1","licenseBasename":"Noto-Sans-SC-OFL.txt","licenseSha256":"1c05c68c34f9708415aada51f17e1b0092d2cea709bf4a94cd38114f9e73d7d9","conversion":"fontTools 4.61.1 instantiateVariableFont wght=600 + SVGPathPen; original glyph outlines and advances","text":"Framivra"},"glyphs":{"F":{"glyphName":"F","advance":576,"bounds":[93.9189453125,0,540.2127685546875,738.7308349609375],"path":"M93.9189453125 0V738.7308349609375H540.2127685546875V628.0057373046875H226.065185546875V418.8118896484375H493.92333984375V308.416748046875H226.065185546875V0Z"},"r":{"glyphName":"r","advance":423,"bounds":[82.8885498046875,0,424.0753173828125,568.8118896484375],"path":"M82.8885498046875 0V555.141845703125H189.943603515625L200.6339111328125 456.141845703125H203.303955078125Q233.32421875 510.1722412109375 275.00946044921875 539.4920654296875Q316.6947021484375 568.8118896484375 361.0145263671875 568.8118896484375Q383.0550537109375 568.8118896484375 397.40020751953125 565.8068237304688Q411.745361328125 562.8017578125 424.0753173828125 557.141845703125L401.6744384765625 443.736572265625Q386.664306640625 448.0665283203125 374.3140869140625 450.40155029296875Q361.9638671875 452.736572265625 343.9537353515625 452.736572265625Q311.6339111328125 452.736572265625 275.31915283203125 426.93194580078125Q239.00439453125 401.1273193359375 214.0347900390625 337.8682861328125V0Z"},"a":{"glyphName":"a","advance":583,"bounds":[53.2691650390625,-13.6700439453125,509.6541748046875,568.8118896484375],"path":"M216.659912109375 -13.6700439453125Q168.2894287109375 -13.6700439453125 131.44427490234375 7.02532958984375Q94.59912109375 27.720703125 73.93414306640625 64.5810546875Q53.2691650390625 101.44140625 53.2691650390625 149.4312744140625Q53.2691650390625 238.7713623046875 130.9088134765625 287.62158203125Q208.5484619140625 336.4718017578125 378.5079345703125 355.1317138671875Q377.8277587890625 384.7308349609375 368.66278076171875 409.1497802734375Q359.497802734375 433.5687255859375 337.86322021484375 448.17291259765625Q316.2286376953125 462.777099609375 278.6497802734375 462.777099609375Q237.3907470703125 462.777099609375 198.421142578125 446.8074951171875Q159.4515380859375 430.837890625 122.060791015625 408.15771484375L74.578857421875 496.12158203125Q105.6497802734375 515.4920654296875 141.520263671875 531.8270874023438Q177.3907470703125 548.162109375 217.90594482421875 558.4869995117188Q258.421142578125 568.8118896484375 302.0911865234375 568.8118896484375Q372.1317138671875 568.8118896484375 418.01739501953125 540.9768676757812Q463.903076171875 513.141845703125 486.77862548828125 459.76629638671875Q509.6541748046875 406.3907470703125 509.6541748046875 328.939208984375V0H402.59912109375L392.9088134765625 61.2894287109375H388.5687255859375Q351.9189453125 29.6295166015625 308.779296875 7.979736328125Q265.6396484375 -13.6700439453125 216.659912109375 -13.6700439453125ZM258.5224609375 89.6845703125Q292.12158203125 89.6845703125 320.3704833984375 105.14910888671875Q348.619384765625 120.6136474609375 378.5079345703125 149.20263671875V272.538330078125Q304.04052734375 263.8885498046875 259.9920654296875 247.75396728515625Q215.943603515625 231.619384765625 197.32928466796875 209.50506591796875Q178.7149658203125 187.3907470703125 178.7149658203125 159.141845703125Q178.7149658203125 122.263427734375 201.31915283203125 105.9739990234375Q223.92333984375 89.6845703125 258.5224609375 89.6845703125Z"},"m":{"glyphName":"m","advance":953,"bounds":[82.8885498046875,0,876.425537109375,568.8118896484375],"path":"M82.8885498046875 0V555.141845703125H189.943603515625L200.6339111328125 479.182373046875H203.303955078125Q238.6339111328125 516.162109375 279.9537353515625 542.4869995117188Q321.2735595703125 568.8118896484375 374.32421875 568.8118896484375Q435.303955078125 568.8118896484375 471.484130859375 542.8321533203125Q507.664306640625 516.8524169921875 526.6947021484375 469.5325927734375Q566.9942626953125 511.822021484375 609.8191528320312 540.3169555664062Q652.64404296875 568.8118896484375 705.6845703125 568.8118896484375Q793.4052734375 568.8118896484375 834.9154052734375 510.93634033203125Q876.425537109375 453.060791015625 876.425537109375 347.7105712890625V0H745.6092529296875V331.31982421875Q745.6092529296875 399.55859375 725.159912109375 427.817626953125Q704.7105712890625 456.07666015625 660.8118896484375 456.07666015625Q635.2127685546875 456.07666015625 606.603515625 439.12225341796875Q577.9942626953125 422.1678466796875 545.065185546875 387.5889892578125V0H414.2489013671875V331.31982421875Q414.2489013671875 399.55859375 393.6295166015625 427.817626953125Q373.0101318359375 456.07666015625 329.1114501953125 456.07666015625Q303.84228515625 456.07666015625 274.73809814453125 439.12225341796875Q245.6339111328125 422.1678466796875 214.0347900390625 387.5889892578125V0Z"},"i":{"glyphName":"i","advance":296,"bounds":[70.578857421875,0,227.00439453125,800.64404296875],"path":"M82.8885498046875 0V555.141845703125H214.0347900390625V0ZM148.7916259765625 651.95947265625Q113.3603515625 651.95947265625 91.9696044921875 672.340087890625Q70.578857421875 692.720703125 70.578857421875 727.1317138671875Q70.578857421875 760.2127685546875 91.9696044921875 780.4284057617188Q113.3603515625 800.64404296875 148.7916259765625 800.64404296875Q183.222900390625 800.64404296875 205.1136474609375 780.4284057617188Q227.00439453125 760.2127685546875 227.00439453125 727.1317138671875Q227.00439453125 692.720703125 205.1136474609375 672.340087890625Q183.222900390625 651.95947265625 148.7916259765625 651.95947265625Z"},"v":{"glyphName":"v","advance":560,"bounds":[13.6700439453125,0,546.80615234375,555.141845703125],"path":"M206.299560546875 0 13.6700439453125 555.141845703125H146.80615234375L233.9537353515625 267.4052734375Q245.943603515625 226.72509765625 257.59844970703125 184.53985595703125Q269.2532958984375 142.3546142578125 280.9132080078125 100.6541748046875H285.583251953125Q297.5731201171875 142.3546142578125 309.22796630859375 184.53985595703125Q320.8828125 226.72509765625 331.8726806640625 267.4052734375L420.020263671875 555.141845703125H546.80615234375L358.5167236328125 0Z"}}};
},
"assets/fonts/source-label-glyphs.json": function(module,exports,require){
module.exports = {"format":"framivra-glyph-paths/1","family":"Noto Sans Mono","style":"Regular","reviewStatus":"AUTHOR_CANDIDATE_NOT_INDEPENDENTLY_APPROVED","unitsPerEm":1000,"ascent":1069,"descent":-293,"provenance":{"fontBasename":"NotoSansMono-Regular.ttf","fontSha256":"6b692c4b6d15ccf59f1c1fe8d11cb8a92f51960f3e9f1f523781755a3af7e29f","sourceKind":"installed-local-font","license":"OFL-1.1","licenseBasename":"Noto-OFL-copyright.txt","licenseSha256":"075f91382875da1c4b491f1facf2586f55448705133264d98ce15cab6211b125","conversion":"fontTools SVGPathPen 4.61.1; unchanged glyph outlines; ASCII U+0020..U+007E"},"glyphs":{"0":{"glyphName":"zero","advance":600,"bounds":[60,-10,540,724],"path":"M299 -10Q174 -10 117.0 87.5Q60 185 60 358Q60 474 84.0 555.5Q108 637 160.5 680.5Q213 724 299 724Q425 724 482.5 628.0Q540 532 540 358Q540 243 516.0 160.5Q492 78 439.0 34.0Q386 -10 299 -10ZM144 358Q144 314 147.5 275.5Q151 237 158 206L395 607Q358 645 299 645Q218 645 181.0 574.5Q144 504 144 358ZM299 69Q381 69 420.0 139.5Q459 210 459 358Q459 462 439 528L196 118Q233 69 299 69Z"},"1":{"glyphName":"one","advance":600,"bounds":[97,0,520,714],"path":"M133 0V60L283 70V446Q283 488 284.5 538.5Q286 589 287 622Q271 613 258.0 607.0Q245 601 219 588L136 545L97 613L296 714H369V70L520 60V0Z"},"2":{"glyphName":"two","advance":600,"bounds":[70,0,545,724],"path":"M70 0V76L260 263Q334 337 375.5 397.0Q417 457 417 524Q417 588 382.0 616.5Q347 645 293 645Q246 645 204.5 626.0Q163 607 127 578L77 640Q118 675 172.0 699.5Q226 724 296 724Q392 724 449.5 673.0Q507 622 507 527Q507 446 464.0 376.5Q421 307 344 233L191 84V80H545V0Z"},"3":{"glyphName":"three","advance":600,"bounds":[74,-10,525,724],"path":"M261 -10Q218 -10 168.0 -1.5Q118 7 74 29V114Q118 92 169.0 80.5Q220 69 267 69Q354 69 394.0 106.0Q434 143 434 203Q434 271 386.5 300.0Q339 329 253 329H179V409H253Q330 409 371.0 445.0Q412 481 412 539Q412 589 380.5 617.0Q349 645 288 645Q237 645 194.5 627.5Q152 610 122 590L77 651Q115 681 170.0 702.5Q225 724 294 724Q398 724 450.5 674.0Q503 624 503 547Q503 475 462.0 431.0Q421 387 354 376V373Q525 350 525 198Q525 138 497.0 91.0Q469 44 411.0 17.0Q353 -10 261 -10Z"},"4":{"glyphName":"four","advance":600,"bounds":[50,0,550,718],"path":"M360 0V160H50V234L344 718H446V237H550V160H446V0ZM137 237H360V415Q360 452 361.0 492.0Q362 532 363.5 568.0Q365 604 366 626H362Q355 607 341.5 581.0Q328 555 318 538Z"},"5":{"glyphName":"five","advance":600,"bounds":[80,-10,519,714],"path":"M257 -10Q210 -10 162.0 0.0Q114 10 80 29V114Q114 92 158.0 80.5Q202 69 249 69Q333 69 380.0 104.5Q427 140 427 214Q427 289 380.5 327.0Q334 365 256 365Q228 365 196.0 360.5Q164 356 139 350L95 378L122 714H478V634H199L180 428Q201 433 231.5 435.5Q262 438 285 438Q350 438 403.0 414.0Q456 390 487.5 341.5Q519 293 519 220Q519 115 450.5 52.5Q382 -10 257 -10Z"},"6":{"glyphName":"six","advance":600,"bounds":[60,-10,540,724],"path":"M308 -10Q194 -10 127.0 69.0Q60 148 60 308Q60 385 73.5 459.0Q87 533 123.0 593.0Q159 653 224.0 688.5Q289 724 392 724Q412 724 438.5 722.0Q465 720 481 715V636Q464 642 441.0 645.0Q418 648 395 648Q323 648 276.0 625.5Q229 603 202.0 564.0Q175 525 163.0 473.5Q151 422 149 364H155Q175 402 216.5 427.0Q258 452 322 452Q422 452 481.0 394.0Q540 336 540 230Q540 118 477.5 54.0Q415 -10 308 -10ZM307 65Q372 65 413.0 103.5Q454 142 454 230Q454 304 417.5 343.0Q381 382 310 382Q262 382 225.5 362.0Q189 342 169.0 311.0Q149 280 149 247Q149 204 166.5 162.0Q184 120 219.5 92.5Q255 65 307 65Z"},"7":{"glyphName":"seven","advance":600,"bounds":[60,0,540,714],"path":"M174 0 449 634H60V714H540V643L269 0Z"},"8":{"glyphName":"eight","advance":600,"bounds":[68,-10,531,724],"path":"M295 -10Q192 -10 130.0 40.5Q68 91 68 179Q68 230 88.0 266.5Q108 303 141.0 329.5Q174 356 214 374Q165 403 128.0 446.5Q91 490 91 554Q91 610 120.0 648.0Q149 686 197.0 705.0Q245 724 301 724Q357 724 404.0 705.0Q451 686 479.5 648.0Q508 610 508 554Q508 488 469.5 446.5Q431 405 373 378Q417 356 453.0 328.5Q489 301 510.0 265.0Q531 229 531 180Q531 125 502.0 82.0Q473 39 420.5 14.5Q368 -10 295 -10ZM301 414Q351 436 386.0 466.5Q421 497 421 549Q421 598 388.0 623.5Q355 649 299 649Q244 649 211.0 624.0Q178 599 178 549Q178 512 196.0 487.0Q214 462 242.0 445.0Q270 428 301 414ZM298 63Q369 63 406.5 97.5Q444 132 444 185Q444 238 405.0 270.5Q366 303 287 340Q226 313 191.0 275.0Q156 237 156 180Q156 129 190.0 96.0Q224 63 298 63Z"},"9":{"glyphName":"nine","advance":600,"bounds":[60,-10,540,724],"path":"M217 -10Q194 -10 170.0 -7.5Q146 -5 129 -1V78Q164 66 213 66Q281 66 326.0 88.5Q371 111 397.5 150.5Q424 190 436.5 241.0Q449 292 451 350H445Q425 313 382.5 287.5Q340 262 278 262Q178 262 119.0 320.5Q60 379 60 488Q60 597 123.0 660.5Q186 724 294 724Q368 724 423.0 689.5Q478 655 509.0 582.0Q540 509 540 391Q540 337 532.0 280.5Q524 224 503.5 172.0Q483 120 446.5 79.0Q410 38 353.5 14.0Q297 -10 217 -10ZM287 332Q334 332 371.0 350.5Q408 369 429.5 402.0Q451 435 451 477Q451 503 441.5 532.5Q432 562 413.0 588.5Q394 615 364.0 632.0Q334 649 293 649Q226 649 186.0 609.0Q146 569 146 487Q146 406 183.5 369.0Q221 332 287 332Z"}," ":{"glyphName":"space","advance":600,"bounds":null,"path":""},"!":{"glyphName":"exclam","advance":600,"bounds":[238,-10,362,714],"path":"M262 202 246 714H355L339 202ZM300 -10Q274 -10 256.0 6.0Q238 22 238 58Q238 96 256.0 111.0Q274 126 300 126Q326 126 344.0 111.0Q362 96 362 58Q362 22 344.0 6.0Q326 -10 300 -10Z"},"\"":{"glyphName":"quotedbl","advance":600,"bounds":[160,456,440,714],"path":"M365 456 345 714H440L420 456ZM180 456 160 714H255L235 456Z"},"#":{"glyphName":"numbersign","advance":600,"bounds":[20,0,580,714],"path":"M101 0 139 209H20V276H152L184 436H57V502H196L236 714H308L268 502H392L432 714H501L461 502H580V436H448L417 276H546V209H404L363 0H292L333 209H210L171 0ZM223 276H346L377 436H254Z"},"$":{"glyphName":"dollar","advance":600,"bounds":[80,-58,520,759],"path":"M263 34Q213 35 167.5 43.5Q122 52 90 67V152Q124 136 170.5 124.0Q217 112 263 111V328Q172 357 126.0 398.0Q80 439 80 515Q80 590 129.5 635.0Q179 680 263 686V759H327V687Q377 686 417.0 675.5Q457 665 493 650L466 576Q434 589 399.0 598.5Q364 608 327 611V395Q391 376 434.0 354.5Q477 333 498.5 299.5Q520 266 520 212Q520 142 469.5 95.5Q419 49 327 38V-58H263ZM263 609Q213 605 189.5 579.5Q166 554 166 515Q166 477 187.0 454.5Q208 432 263 417ZM327 116Q383 123 408.5 148.5Q434 174 434 214Q434 252 411.5 271.5Q389 291 327 307Z"},"%":{"glyphName":"percent","advance":600,"bounds":[0,-9,600,722],"path":"M142 383Q81 383 40.5 427.5Q0 472 0 553Q0 634 38.5 678.0Q77 722 144 722Q205 722 245.5 678.0Q286 634 286 553Q286 472 247.0 427.5Q208 383 142 383ZM83 0 439 714H516L160 0ZM143 441Q177 441 194.0 468.0Q211 495 211 553Q211 612 194.0 637.5Q177 663 143 663Q109 663 91.5 637.5Q74 612 74 553Q74 495 91.5 468.0Q109 441 143 441ZM456 -9Q395 -9 354.5 35.5Q314 80 314 161Q314 242 352.5 286.0Q391 330 458 330Q519 330 559.5 286.0Q600 242 600 161Q600 80 561.0 35.5Q522 -9 456 -9ZM457 49Q491 49 508.0 76.0Q525 103 525 161Q525 220 508.0 245.5Q491 271 457 271Q423 271 405.5 245.5Q388 220 388 161Q388 103 405.5 76.0Q423 49 457 49Z"},"&":{"glyphName":"ampersand","advance":600,"bounds":[39,-10,581,725],"path":"M224 -10Q135 -10 87.0 43.0Q39 96 39 176Q39 227 57.0 266.0Q75 305 104.5 336.0Q134 367 170 394Q142 434 120.5 480.5Q99 527 99 578Q99 644 139.0 684.5Q179 725 249 725Q319 725 359.0 684.5Q399 644 399 580Q399 536 381.0 500.5Q363 465 334.5 435.5Q306 406 275 381L415 207Q434 242 446.5 278.5Q459 315 464 349H547Q537 292 517.0 238.5Q497 185 469 140L581 0H474L416 74Q377 35 329.0 12.5Q281 -10 224 -10ZM181 576Q181 542 195.0 505.5Q209 469 230 439Q262 464 290.0 499.0Q318 534 318 580Q318 616 299.0 636.0Q280 656 249 656Q218 656 199.5 635.0Q181 614 181 576ZM125 181Q125 133 154.0 100.0Q183 67 234 67Q273 67 306.5 86.5Q340 106 368 137L216 333Q180 303 152.5 267.0Q125 231 125 181Z"},"'":{"glyphName":"quotesingle","advance":600,"bounds":[252,456,347,714],"path":"M272 456 252 714H347L327 456Z"},"(":{"glyphName":"parenleft","advance":600,"bounds":[200,-158,430,714],"path":"M347 -158Q271 -70 235.5 41.5Q200 153 200 274Q200 396 235.5 509.5Q271 623 347 714H430Q360 620 324.5 507.0Q289 394 289 275Q289 159 324.5 47.5Q360 -64 429 -158Z"},")":{"glyphName":"parenright","advance":600,"bounds":[170,-158,400,714],"path":"M253.0 714.0Q329.0 626.0 364.5 514.5Q400.0 403.0 400.0 282.0Q400.0 160.0 364.5 46.5Q329.0 -67.0 253.0 -158.0H170.0Q240.0 -64.0 275.5 49.0Q311.0 162.0 311.0 281.0Q311.0 397.0 275.5 508.5Q240.0 620.0 171.0 714.0Z"},"*":{"glyphName":"asterisk","advance":600,"bounds":[90,334,513,746],"path":"M381 334 300 489 220 335 146 382 252 516 90 537 104 612 274 569 260 746H339L327 569L498 616L513 534L352 514L458 381Z"},"+":{"glyphName":"plus","advance":600,"bounds":[80,121,520,585],"path":"M265 121V317H80V388H265V585H337V388H520V317H337V121Z"},",":{"glyphName":"comma","advance":600,"bounds":[211,-140,380,129],"path":"M211 -140Q227 -75 242.0 -1.5Q257 72 265 129H372L380 117Q365 59 338.5 -10.5Q312 -80 285 -140Z"},"-":{"glyphName":"hyphen","advance":600,"bounds":[150,230,450,310],"path":"M150 230V310H450V230Z"},".":{"glyphName":"period","advance":600,"bounds":[238,-10,362,126],"path":"M300 -10Q274 -10 256.0 6.0Q238 22 238 58Q238 96 256.0 111.0Q274 126 300 126Q326 126 344.0 111.0Q362 96 362 58Q362 22 344.0 6.0Q326 -10 300 -10Z"},"/":{"glyphName":"slash","advance":600,"bounds":[120,0,480,714],"path":"M120 0 394 714H480L206 0Z"},":":{"glyphName":"colon","advance":600,"bounds":[238,-10,362,546],"path":"M301 410Q275 410 257.0 426.0Q239 442 239 478Q239 516 257.0 531.0Q275 546 301 546Q327 546 344.5 531.0Q362 516 362 478Q362 442 344.5 426.0Q327 410 301 410ZM300 -10Q274 -10 256.0 6.0Q238 22 238 58Q238 96 256.0 111.0Q274 126 300 126Q326 126 343.5 111.0Q361 96 361 58Q361 22 343.5 6.0Q326 -10 300 -10Z"},";":{"glyphName":"semicolon","advance":600,"bounds":[211,-140,385,546],"path":"M323 410Q297 410 279.0 426.0Q261 442 261 478Q261 516 279.0 531.0Q297 546 323 546Q349 546 367.0 531.0Q385 516 385 478Q385 442 367.0 426.0Q349 410 323 410ZM211 -140Q227 -75 242.0 -1.5Q257 72 265 129H372L380 117Q365 59 338.5 -10.5Q312 -80 285 -140Z"},"<":{"glyphName":"less","advance":600,"bounds":[90,116,510,608],"path":"M510.0 116.0V194.0L184.0 351.0L510.0 530.0V608.0L90.0 373.0V323.0Z"},"=":{"glyphName":"equal","advance":600,"bounds":[70,217,530,487],"path":"M70 407V487H530V407ZM70 217V297H530V217Z"},">":{"glyphName":"greater","advance":600,"bounds":[90,116,510,608],"path":"M90 116V194L416 351L90 530V608L510 373V323Z"},"?":{"glyphName":"question","advance":600,"bounds":[90,-10,510,724],"path":"M230 220Q230 258 239.0 286.0Q248 314 270.5 339.5Q293 365 331 392Q365 416 385.5 437.0Q406 458 415.5 483.0Q425 508 425 545Q425 589 392.5 617.5Q360 646 298 646Q246 646 204.5 633.5Q163 621 122 602L90 677Q131 695 178.5 709.5Q226 724 293 724Q394 724 452.0 676.5Q510 629 510 549Q510 502 498.0 468.0Q486 434 461.5 406.5Q437 379 398 350Q359 321 339.5 300.5Q320 280 314.0 260.0Q308 240 308 211V202H230ZM273 -10Q247 -10 229.0 6.0Q211 22 211 58Q211 96 229.0 111.0Q247 126 273 126Q299 126 317.0 111.0Q335 96 335 58Q335 22 317.0 6.0Q299 -10 273 -10Z"},"@":{"glyphName":"at","advance":600,"bounds":[15,-89,585,714],"path":"M324 -89Q224 -89 155.0 -42.0Q86 5 50.5 91.5Q15 178 15 294Q15 417 50.0 511.5Q85 606 152.5 660.0Q220 714 319 714Q400 714 459.5 671.0Q519 628 552.0 548.0Q585 468 585 357Q585 311 578.5 267.0Q572 223 558.5 187.0Q545 151 522.5 129.5Q500 108 467 108Q433 108 414.0 135.0Q395 162 390 196H388Q374 159 346.0 133.5Q318 108 276 108Q215 108 182.0 160.0Q149 212 149 296Q149 362 170.0 412.5Q191 463 229.5 491.5Q268 520 321 520Q357 520 392.0 512.5Q427 505 446 497L435 294Q434 286 434.0 275.5Q434 265 434 257Q434 205 445.0 188.0Q456 171 469 171Q487 171 498.0 196.5Q509 222 513.5 264.5Q518 307 518 358Q518 497 462.5 572.5Q407 648 318 648Q242 648 189.5 604.0Q137 560 110.5 481.5Q84 403 84 297Q84 149 145.0 63.5Q206 -22 327 -22Q377 -22 419.0 -11.0Q461 0 496 16V-49Q460 -67 419.0 -78.0Q378 -89 324 -89ZM289 171Q326 171 341.0 213.5Q356 256 360 323L367 447Q357 451 344.0 454.0Q331 457 317 457Q284 457 264.5 433.0Q245 409 236.0 372.0Q227 335 227 294Q227 229 244.0 200.0Q261 171 289 171Z"},"A":{"glyphName":"A","advance":600,"bounds":[20,0,580,717],"path":"M20 0 252 717H347L580 0H488L420 219H179L111 0ZM270 512 204 299H395L329 512Q324 529 315.0 560.0Q306 591 299 622Q293 591 284.0 560.0Q275 529 270 512Z"},"B":{"glyphName":"B","advance":600,"bounds":[95,0,545,714],"path":"M95 0V714H287Q397 714 461.0 674.5Q525 635 525 539Q525 469 491.0 429.0Q457 389 397 379V374Q438 367 471.5 349.0Q505 331 525.0 297.0Q545 263 545 207Q545 110 484.0 55.0Q423 0 317 0ZM295 412Q370 412 402.0 440.5Q434 469 434 527Q434 586 399.5 611.0Q365 636 280 636H180V412ZM301 78Q387 78 420.0 114.0Q453 150 453 207Q453 265 418.5 299.5Q384 334 295 334H180V78Z"},"C":{"glyphName":"C","advance":600,"bounds":[66,-10,550,724],"path":"M365 -10Q260 -10 194.0 37.0Q128 84 97.0 166.5Q66 249 66 357Q66 470 101.5 552.5Q137 635 204.5 679.5Q272 724 369 724Q421 724 466.5 713.0Q512 702 550 682L516 609Q485 626 445.5 635.0Q406 644 369 644Q267 644 212.5 570.0Q158 496 158 357Q158 224 207.0 148.5Q256 73 367 73Q401 73 440.5 80.5Q480 88 519 104V19Q479 4 442.5 -3.0Q406 -10 365 -10Z"},"D":{"glyphName":"D","advance":600,"bounds":[70,0,555,714],"path":"M70 0V714H243Q338 714 408.0 674.0Q478 634 516.5 556.5Q555 479 555 364Q555 183 467.5 91.5Q380 0 223 0ZM214 78Q342 78 402.5 150.0Q463 222 463 361Q463 503 401.5 569.5Q340 636 233 636H160V78Z"},"E":{"glyphName":"E","advance":600,"bounds":[95,0,515,714],"path":"M95 0V714H515V634H185V413H496V333H185V80H515V0Z"},"F":{"glyphName":"F","advance":600,"bounds":[95,0,515,714],"path":"M95 0V714H515V634H185V382H496V302H185V0Z"},"G":{"glyphName":"G","advance":600,"bounds":[45,-10,530,724],"path":"M340 -10Q194 -10 119.5 81.0Q45 172 45 346Q45 525 123.0 624.5Q201 724 352 724Q403 724 444.0 713.0Q485 702 522 682L490 607Q465 623 431.0 633.0Q397 643 354 643Q245 643 191.0 566.5Q137 490 137 350Q137 270 156.5 206.5Q176 143 221.5 106.5Q267 70 344 70Q376 70 400.0 75.0Q424 80 443 85V297H313V377H530V27Q493 12 444.5 1.0Q396 -10 340 -10Z"},"H":{"glyphName":"H","advance":600,"bounds":[70,0,530,714],"path":"M70 0V714H160V413H440V714H530V0H440V333H160V0Z"},"I":{"glyphName":"I","advance":600,"bounds":[110,0,490,714],"path":"M110 0V62L254 75V639L110 652V714H490V652L344 639V75L490 62V0Z"},"J":{"glyphName":"J","advance":600,"bounds":[100,-10,465,714],"path":"M252 -10Q214 -10 171.0 -2.0Q128 6 100 19V104Q123 94 164.0 83.5Q205 73 245 73Q304 73 339.5 108.5Q375 144 375 229V714H465V217Q465 108 411.0 49.0Q357 -10 252 -10Z"},"K":{"glyphName":"K","advance":600,"bounds":[70,0,550,714],"path":"M70 0V714H160V330Q177 357 195.0 385.0Q213 413 234 441L438 714H541L302 391L550 0H447L241 330L160 237V0Z"},"L":{"glyphName":"L","advance":600,"bounds":[115,0,515,714],"path":"M115 0V714H205V83H515V0Z"},"M":{"glyphName":"M","advance":600,"bounds":[50,0,550,714],"path":"M50 0V714H179L298 295H301L420 714H550V0H465V387Q465 446 467.5 508.5Q470 571 477 624H473L341 178H258L125 625H121Q126 596 129.0 554.5Q132 513 133.5 469.0Q135 425 135 387V0Z"},"N":{"glyphName":"N","advance":600,"bounds":[70,0,530,714],"path":"M70 0V714H189L449 119H452Q447 178 445.0 226.5Q443 275 443 336V714H530V0H421L148 622H144Q149 566 153.0 510.0Q157 454 157 398V0Z"},"O":{"glyphName":"O","advance":600,"bounds":[45,-10,555,726],"path":"M299 -10Q210 -10 154.0 36.0Q98 82 71.5 165.0Q45 248 45 357Q45 467 71.5 550.0Q98 633 154.5 679.5Q211 726 300 726Q389 726 445.5 680.0Q502 634 528.5 551.5Q555 469 555 359Q555 248 528.0 165.0Q501 82 444.5 36.0Q388 -10 299 -10ZM301 70Q391 70 427.0 146.5Q463 223 463 358Q463 494 427.0 570.0Q391 646 302 646Q211 646 174.0 569.0Q137 492 137 358Q137 223 174.0 146.5Q211 70 301 70Z"},"P":{"glyphName":"P","advance":600,"bounds":[95,0,545,714],"path":"M95 0V714H299Q427 714 486.0 659.5Q545 605 545 505Q545 446 520.5 393.0Q496 340 439.5 307.5Q383 275 286 275H185V0ZM276 353Q365 353 409.0 387.0Q453 421 453 500Q453 574 413.5 605.0Q374 636 291 636H185V353Z"},"Q":{"glyphName":"Q","advance":600,"bounds":[45,-180,555,726],"path":"M299 -10Q210 -10 154.0 36.0Q98 82 71.5 165.0Q45 248 45 357Q45 467 71.5 550.0Q98 633 154.5 679.5Q211 726 300 726Q389 726 445.5 680.0Q502 634 528.5 551.5Q555 469 555 359Q555 242 525.0 156.0Q495 70 432 27L541 -180H441L353 -5Q328 -10 299 -10ZM301 70Q391 70 427.0 146.5Q463 223 463 358Q463 494 427.0 570.0Q391 646 302 646Q211 646 174.0 569.0Q137 492 137 358Q137 223 174.0 146.5Q211 70 301 70Z"},"R":{"glyphName":"R","advance":600,"bounds":[95,0,569,714],"path":"M95 0V714H262Q513 714 513 514Q513 458 495.0 419.5Q477 381 446.5 356.5Q416 332 378 318L569 0H469L297 292H185V0ZM259 370Q347 370 383.5 404.5Q420 439 420 506Q420 577 381.5 606.5Q343 636 257 636H185V370Z"},"S":{"glyphName":"S","advance":600,"bounds":[85,-10,529,724],"path":"M281 -10Q224 -10 170.5 0.5Q117 11 85 27V110Q122 94 175.0 80.5Q228 67 283 67Q437 67 437 192Q437 226 423.0 246.0Q409 266 373.5 284.5Q338 303 274 331Q232 350 192.0 373.0Q152 396 126.5 434.0Q101 472 101 534Q101 626 162.0 675.0Q223 724 329 724Q383 724 431.0 712.5Q479 701 515 684L486 609Q452 625 410.0 634.5Q368 644 327 644Q269 644 231.0 619.0Q193 594 193 532Q193 497 207.5 475.0Q222 453 254.0 436.5Q286 420 336 398Q393 374 436.5 349.5Q480 325 504.5 289.0Q529 253 529 191Q529 96 466.5 43.0Q404 -10 281 -10Z"},"T":{"glyphName":"T","advance":600,"bounds":[45,0,555,714],"path":"M255 0V634H45V714H555V634H345V0Z"},"U":{"glyphName":"U","advance":600,"bounds":[70,-10,530,714],"path":"M298 -10Q216 -10 165.5 22.5Q115 55 92.5 114.5Q70 174 70 254V714H160V259Q160 203 172.5 160.0Q185 117 215.5 93.5Q246 70 300 70Q357 70 387.5 95.0Q418 120 429.0 163.5Q440 207 440 260V714H530V252Q530 178 508.0 118.5Q486 59 435.0 24.5Q384 -10 298 -10Z"},"V":{"glyphName":"V","advance":600,"bounds":[25,0,575,714],"path":"M255 0 25 714H119L258 250Q272 206 282.5 164.5Q293 123 301 87H304Q312 122 322.5 164.5Q333 207 346 253L480 714H575L349 0Z"},"W":{"glyphName":"W","advance":600,"bounds":[25,0,575,714],"path":"M105 0 25 714H108Q116 635 123.5 556.5Q131 478 138 399Q142 361 145.5 316.0Q149 271 152.0 228.0Q155 185 157.0 150.5Q159 116 159 98H161Q165 121 172.5 157.5Q180 194 188.5 233.5Q197 273 204 304L258 536H344L400 302Q409 268 417.5 229.0Q426 190 433.5 155.5Q441 121 444 98H446Q447 122 449.5 169.0Q452 216 456.0 277.0Q460 338 465 402Q472 480 478.5 558.0Q485 636 492 714H575L503 0H406L327 328Q317 373 310.5 405.5Q304 438 302 450H300Q299 438 293.5 406.0Q288 374 277 328L201 0Z"},"X":{"glyphName":"X","advance":600,"bounds":[35,0,565,714],"path":"M35 0 258 369 50 714H147L300 441L455 714H550L343 368L565 0H472L301 296L131 0Z"},"Y":{"glyphName":"Y","advance":600,"bounds":[35,0,565,714],"path":"M257 0V278L35 714H134L301 353L469 714H565L343 278V0Z"},"Z":{"glyphName":"Z","advance":600,"bounds":[95,0,505,714],"path":"M95 0V71L412 636H115V714H505V643L192 80H505V0Z"},"[":{"glyphName":"bracketleft","advance":600,"bounds":[240,-158,465,714],"path":"M240 -158V714H465V642H326V-86H465V-158Z"},"\\":{"glyphName":"backslash","advance":600,"bounds":[120,0,480,714],"path":"M480.0 0.0 206.0 714.0H120.0L394.0 0.0Z"},"]":{"glyphName":"bracketright","advance":600,"bounds":[135,-148,360,724],"path":"M360.0 724.0V-148.0H135.0V-76.0H274.0V652.0H135.0V724.0Z"},"^":{"glyphName":"asciicircum","advance":600,"bounds":[50,267,550,719],"path":"M50 267 264 719H314L550 267H472L290 626L128 267Z"},"_":{"glyphName":"underscore","advance":600,"bounds":[0,-154,600,-74],"path":"M0 -154V-74H600V-154Z"},"`":{"glyphName":"grave","advance":600,"bounds":[189,606,390,766],"path":"M331 606Q308 624 279.0 652.5Q250 681 225.5 709.5Q201 738 189 756V766H294Q305 744 321.5 716.5Q338 689 356.5 663.0Q375 637 390 618V606Z"},"a":{"glyphName":"a","advance":600,"bounds":[70,-10,510,546],"path":"M246 -10Q167 -10 118.5 29.0Q70 68 70 147Q70 234 132.5 277.0Q195 320 313 323L428 326V348Q428 420 392.0 446.5Q356 473 295 473Q252 473 213.0 462.0Q174 451 139 431L108 499Q146 518 192.5 532.0Q239 546 300 546Q401 546 455.5 500.5Q510 455 510 359V0H438L428 74H424Q391 32 348.0 11.0Q305 -10 246 -10ZM261 63Q306 63 344.0 79.5Q382 96 405.0 129.5Q428 163 428 214V262L332 259Q236 256 195.5 228.0Q155 200 155 150Q155 104 184.0 83.5Q213 63 261 63Z"},"b":{"glyphName":"b","advance":600,"bounds":[80,-10,545,760],"path":"M329 -10Q270 -10 229.5 13.5Q189 37 166 68H160L152 0H80V760H167V576Q167 548 166.5 521.0Q166 494 163 466H167Q188 498 228.0 522.0Q268 546 329 546Q426 546 485.5 476.0Q545 406 545 269Q545 131 485.0 60.5Q425 -10 329 -10ZM312 63Q385 63 419.0 116.0Q453 169 453 269Q453 367 419.0 420.0Q385 473 311 473Q242 473 204.5 420.5Q167 368 167 271Q167 170 202.5 116.5Q238 63 312 63Z"},"c":{"glyphName":"c","advance":600,"bounds":[85,-10,505,546],"path":"M338 -10Q220 -10 152.5 54.5Q85 119 85 264Q85 367 118.5 429.0Q152 491 211.5 518.5Q271 546 347 546Q394 546 432.5 536.5Q471 527 505 509L478 436Q446 453 416.0 460.5Q386 468 351 468Q259 468 218.0 417.5Q177 367 177 266Q177 159 220.0 113.0Q263 67 348 67Q392 67 431.0 76.5Q470 86 499 99V20Q469 6 425.0 -2.0Q381 -10 338 -10Z"},"d":{"glyphName":"d","advance":600,"bounds":[55,-10,520,760],"path":"M274 -10Q175 -10 115.0 59.5Q55 129 55 271Q55 405 112.0 475.5Q169 546 284 546Q340 546 376.0 524.0Q412 502 431 468H437Q435 482 433.0 505.0Q431 528 431 547V760H520V0H446L436 72H432Q410 36 372.5 13.0Q335 -10 274 -10ZM291 63Q362 63 396.5 112.5Q431 162 431 247V267Q431 326 418.0 372.5Q405 419 375.0 446.0Q345 473 294 473Q224 473 185.5 421.0Q147 369 147 262Q147 63 291 63Z"},"e":{"glyphName":"e","advance":600,"bounds":[60,-10,540,546],"path":"M337 -10Q208 -10 134.0 61.5Q60 133 60 266Q60 354 92.0 416.5Q124 479 181.0 512.5Q238 546 314 546Q419 546 479.5 478.0Q540 410 540 292V251H151Q153 181 177.5 140.0Q202 99 244.5 81.5Q287 64 343 64Q387 64 430.0 76.0Q473 88 516 108V28Q470 8 424.0 -1.0Q378 -10 337 -10ZM450 323Q447 393 412.5 433.0Q378 473 314 473Q247 473 205.5 432.5Q164 392 153 323Z"},"f":{"glyphName":"f","advance":600,"bounds":[66,0,570,765],"path":"M241 0V461H66V523L241 536V597Q241 661 265.5 697.5Q290 734 334.0 749.5Q378 765 436 765Q480 765 513.0 759.0Q546 753 570 745L550 676Q530 682 500.0 686.5Q470 691 441 691Q389 691 360.0 669.0Q331 647 331 585V536H533V461H331V0Z"},"g":{"glyphName":"g","advance":600,"bounds":[55,-245,520,546],"path":"M276 -245Q221 -245 172.5 -234.5Q124 -224 83 -207V-127Q123 -150 175.5 -161.5Q228 -173 273 -173Q364 -173 400.0 -130.5Q436 -88 436 -14V5Q436 17 436.5 28.0Q437 39 439 62H435Q407 21 369.5 5.5Q332 -10 279 -10Q173 -10 114.0 62.5Q55 135 55 265Q55 396 113.5 471.0Q172 546 274 546Q328 546 365.0 528.5Q402 511 431 470H436L448 536H520V-14Q520 -129 461.0 -187.0Q402 -245 276 -245ZM296 63Q363 63 401.0 106.5Q439 150 439 246V267Q439 377 400.0 425.0Q361 473 293 473Q217 473 182.0 419.5Q147 366 147 268Q147 168 183.5 115.5Q220 63 296 63Z"},"h":{"glyphName":"h","advance":600,"bounds":[80,0,525,760],"path":"M80 0V760H170V544Q170 525 168.5 496.0Q167 467 166 450H171Q197 500 240.5 523.0Q284 546 339 546Q422 546 473.5 499.5Q525 453 525 350V0H435V332Q435 405 404.0 439.0Q373 473 316 473Q249 473 209.5 421.5Q170 370 170 278V0Z"},"i":{"glyphName":"i","advance":600,"bounds":[80,0,540,760],"path":"M307 633Q281 633 262.5 649.0Q244 665 244 696Q244 729 262.5 744.5Q281 760 307 760Q333 760 351.5 744.5Q370 729 370 696Q370 665 351.5 649.0Q333 633 307 633ZM80 0V60L271 75V461L96 474V536H361V75L540 60V0Z"},"j":{"glyphName":"j","advance":600,"bounds":[60,-245,405,760],"path":"M342 633Q316 633 298.0 649.0Q280 665 280 697Q280 729 297.5 744.5Q315 760 342 760Q368 760 386.5 744.5Q405 729 405 697Q405 664 386.0 648.5Q367 633 342 633ZM192 -245Q161 -245 123.5 -240.0Q86 -235 60 -228V-149Q86 -158 119.0 -164.0Q152 -170 182 -170Q249 -170 278.0 -137.0Q307 -104 307 -37V461L132 474V536H397V-52Q397 -145 343.5 -195.0Q290 -245 192 -245Z"},"k":{"glyphName":"k","advance":600,"bounds":[105,0,570,760],"path":"M105 0V760H195V394Q195 363 192.5 320.5Q190 278 188 243H190Q209 268 226.5 288.5Q244 309 263 331L448 536H556L343 305L570 0H466L283 252L195 167V0Z"},"l":{"glyphName":"l","advance":600,"bounds":[80,0,540,760],"path":"M80 0V60L276 75V685L86 698V760H366V75L540 60V0Z"},"m":{"glyphName":"m","advance":600,"bounds":[50,0,551,546],"path":"M50 0V536H112L125 477H130Q150 517 174.0 531.5Q198 546 229 546Q270 546 292.5 526.5Q315 507 327 469H330Q347 506 372.5 526.0Q398 546 440 546Q502 546 526.5 498.5Q551 451 551 350V0H471V335Q471 398 462.5 433.0Q454 468 417 468Q341 468 341 268V0H261V335Q261 399 249.5 433.5Q238 468 201 468Q161 468 145.5 414.0Q130 360 130 268V0Z"},"n":{"glyphName":"n","advance":600,"bounds":[80,0,525,546],"path":"M80 0V536H152L165 457H170Q193 503 238.5 524.5Q284 546 345 546Q426 546 475.5 500.5Q525 455 525 358V0H435V342Q435 410 407.5 441.5Q380 473 319 473Q237 473 203.5 421.0Q170 369 170 275V0Z"},"o":{"glyphName":"o","advance":600,"bounds":[55,-10,545,546],"path":"M301 -10Q231 -10 175.5 20.5Q120 51 87.5 113.5Q55 176 55 270Q55 363 86.5 424.0Q118 485 173.0 515.5Q228 546 298 546Q368 546 424.0 515.5Q480 485 512.5 423.5Q545 362 545 267Q545 174 513.0 112.5Q481 51 426.0 20.5Q371 -10 301 -10ZM300 63Q378 63 416.0 118.5Q454 174 454 269Q454 364 415.5 418.5Q377 473 299 473Q221 473 182.5 418.5Q144 364 144 269Q144 174 183.0 118.5Q222 63 300 63Z"},"p":{"glyphName":"p","advance":600,"bounds":[80,-240,545,546],"path":"M80 -240V536H154L165 463H169Q192 502 230.5 524.0Q269 546 329 546Q427 546 486.0 476.0Q545 406 545 269Q545 132 486.0 61.0Q427 -10 329 -10Q270 -10 231.0 14.0Q192 38 169 69H163Q169 30 169 -11V-240ZM310 63Q385 63 419.0 114.5Q453 166 453 271Q453 374 419.5 423.5Q386 473 309 473Q236 473 202.5 425.5Q169 378 169 289V269Q169 174 199.5 118.5Q230 63 310 63Z"},"q":{"glyphName":"q","advance":600,"bounds":[55,-240,520,546],"path":"M431 -11Q431 9 432.0 30.0Q433 51 434 72H431Q409 38 371.5 14.0Q334 -10 274 -10Q177 -10 116.0 60.5Q55 131 55 267Q55 359 83.5 421.0Q112 483 161.5 514.5Q211 546 275 546Q334 546 371.5 522.0Q409 498 431 464H435L448 536H520V-240H431ZM290 63Q431 63 431 247V267Q431 364 399.5 418.5Q368 473 288 473Q215 473 181.0 420.0Q147 367 147 266Q147 174 181.5 118.5Q216 63 290 63Z"},"r":{"glyphName":"r","advance":600,"bounds":[62,0,572,546],"path":"M62 0V60L225 75V461L78 474V536H297L309 438H313Q341 491 374.5 518.5Q408 546 463 546Q497 546 523.5 540.0Q550 534 572 524L548 445Q532 454 510.0 460.5Q488 467 463 467Q420 467 387.0 440.0Q354 413 335.0 368.0Q316 323 316 269V75L472 60V0Z"},"s":{"glyphName":"s","advance":600,"bounds":[105,-10,495,546],"path":"M279 -10Q229 -10 183.5 -1.0Q138 8 105 24V107Q140 91 182.5 77.5Q225 64 284 64Q350 64 377.5 84.5Q405 105 405 144Q405 165 395.5 178.5Q386 192 359.0 206.0Q332 220 280 242Q228 265 191.0 285.0Q154 305 134.0 332.5Q114 360 114 403Q114 474 168.5 510.0Q223 546 320 546Q364 546 404.0 538.0Q444 530 487 512L460 442Q420 459 386.5 465.0Q353 471 320 471Q258 471 229.5 455.0Q201 439 201 405Q201 383 213.5 368.0Q226 353 254.5 339.0Q283 325 330 305Q383 283 420.0 264.0Q457 245 476.0 218.5Q495 192 495 146Q495 70 440.5 30.0Q386 -10 279 -10Z"},"t":{"glyphName":"t","advance":600,"bounds":[56,-10,516,679],"path":"M402 -10Q345 -10 300.0 5.0Q255 20 228.5 58.0Q202 96 202 163V461H56V523L202 539L220 679H292V536H501V461H292V180Q292 119 319.0 91.0Q346 63 408 63Q434 63 464.0 68.0Q494 73 516 78V6Q492 0 463.0 -5.0Q434 -10 402 -10Z"},"u":{"glyphName":"u","advance":600,"bounds":[75,-10,520,536],"path":"M261 -10Q166 -10 120.5 37.5Q75 85 75 179V536H165V193Q165 129 191.5 96.0Q218 63 283 63Q366 63 398.0 115.0Q430 167 430 260V536H520V0H448L435 72H430Q402 27 359.5 8.5Q317 -10 261 -10Z"},"v":{"glyphName":"v","advance":600,"bounds":[45,0,554,536],"path":"M243 0 45 536H137L249 218Q260 186 272.5 151.0Q285 116 298 76H301Q314 116 327.5 151.5Q341 187 351 218L460 536H554L354 0Z"},"w":{"glyphName":"w","advance":600,"bounds":[16,0,585,536],"path":"M102 0 16 536H100L139 271Q145 234 147.5 205.0Q150 176 152.5 146.0Q155 116 157 75H160Q163 92 168.5 122.5Q174 153 181.5 188.5Q189 224 198 254L250 435H349L399 254Q408 224 416.0 189.5Q424 155 430.5 124.5Q437 94 439 75H442Q445 115 447.0 144.5Q449 174 452.0 203.0Q455 232 461 271L503 536H585L497 0H389L341 188Q331 229 320.0 270.0Q309 311 301 351H298Q290 311 278.5 270.0Q267 229 256 188L207 0Z"},"x":{"glyphName":"x","advance":600,"bounds":[45,0,554,536],"path":"M45 0 248 274 55 536H156L300 335L444 536H545L351 274L554 0H453L300 213L146 0Z"},"y":{"glyphName":"y","advance":600,"bounds":[45,-245,554,536],"path":"M136 -245Q116 -245 97.0 -242.5Q78 -240 64 -237V-168Q76 -170 92.5 -172.0Q109 -174 125 -174Q171 -174 196.0 -151.5Q221 -129 241 -78L270 -5L45 536H133L261 221Q274 190 286.5 152.0Q299 114 309 79H312Q323 116 335.0 153.5Q347 191 357 222L462 536H554L329 -70Q310 -122 287.5 -161.5Q265 -201 229.5 -223.0Q194 -245 136 -245Z"},"z":{"glyphName":"z","advance":600,"bounds":[90,0,510,536],"path":"M90 0V61L405 467H106V536H502V465L197 76H510V0Z"},"{":{"glyphName":"braceleft","advance":600,"bounds":[140,-158,460,714],"path":"M460 -158Q368 -157 315.0 -118.0Q262 -79 262 1V148Q262 199 230.5 220.5Q199 242 140 242V315Q199 316 230.5 337.0Q262 358 262 408V556Q262 636 317.0 675.0Q372 714 460 714V642Q408 641 378.5 619.0Q349 597 349 548V404Q349 301 240 282V276Q349 257 349 154V7Q349 -42 378.0 -63.5Q407 -85 460 -86Z"},"|":{"glyphName":"bar","advance":600,"bounds":[264,-240,337,760],"path":"M264 -240V760H337V-240Z"},"}":{"glyphName":"braceright","advance":600,"bounds":[140,-158,460,714],"path":"M140.0 714.0Q232.0 713.0 285.0 674.0Q338.0 635.0 338.0 555.0V408.0Q338.0 357.0 369.5 335.5Q401.0 314.0 460.0 314.0V241.0Q401.0 240.0 369.5 219.0Q338.0 198.0 338.0 148.0V0.0Q338.0 -80.0 283.0 -119.0Q228.0 -158.0 140.0 -158.0V-86.0Q192.0 -85.0 221.5 -63.0Q251.0 -41.0 251.0 8.0V152.0Q251.0 255.0 360.0 274.0V280.0Q251.0 299.0 251.0 402.0V549.0Q251.0 598.0 222.0 619.5Q193.0 641.0 140.0 642.0Z"},"~":{"glyphName":"asciitilde","advance":600,"bounds":[74,287,524,462],"path":"M74 287Q77 316 83.0 346.5Q89 377 102.0 403.5Q115 430 137.5 446.0Q160 462 196 462Q231 462 258.5 445.0Q286 428 321 396Q342 377 362.0 361.5Q382 346 403 346Q431 346 444.0 376.5Q457 407 464 462H524Q521 436 514.5 406.0Q508 376 495.0 349.0Q482 322 459.5 304.5Q437 287 402 287Q367 287 338.5 305.0Q310 323 282 350Q259 373 239.0 388.0Q219 403 196 403Q167 403 152.5 374.5Q138 346 133 287Z"}}};
},
"assets/geometry.json": function(module,exports,require){
module.exports = {"units":"1280x720 viewBox; multiply by 1.5 for 1920x1080","bird":{"wing_a":[[-91,-100],[-57,13],[25,3]],"wing_b":[[-28,-100],[32,-8],[49,-24]],"body":[[-100,15],[-50,-5],[5,-17],[53,-12],[86,9],[56,25],[0,38],[-62,32]],"tail":[[-70,8],[-149,-11],[-115,27],[-151,58],[-62,31]],"head":[[28,22],[56,-66],[112,-66],[112,-8],[84,22]],"beak":[[109,-47],[140,-33],[109,-25]],"eye":{"center":[93,-43],"radius":4.5},"wing_a_hinge":[-15,-6]},"A":{"path":"M-80 110V-15A80 80 0 0 1 80-15V110H52V-15A52 52 0 0 0-52-15V110Z","receiver_y":110},"R":{"double_rail_y":[0,18],"rail_x":[-80,80],"crossbar_x":[-55,0,55],"owner":"P-C / mobile G threshold"},"W-guide":{"fixed_world":true,"rail_endpoints":[[182,567],[1097,567]],"second_y":584,"wheel_centers_y":602},"P-C":{"local_carrier_rect":[-110,110,250,14],"receiver_rectangles":[[-91,107,42,8],[49,107,42,8]],"gate_world_f2450_and_f2759":{"translation":[650,379],"scale":1.06,"rotation":0}},"P_f2159":{"P_A":[[608,496],[738,496],[938,360],[808,360]],"P_B":[[808,360],[938,360],[1110,499],[980,499]],"shared_hinge":[[808,360],[938,360]],"note":"Screen-space target; motion must use a shared local hinge graph."},"F":{"corner_polygon":[[0,0],[78,0],[78,16],[20,16],[20,72],[0,72]],"corner_count":4,"wide_frame":[170,104,940,452],"final_frame":[140,181,1000,397]},"contacts":{"S2_wing_card":{"bird_translation":[765,465],"scale":1.5,"local_wing_tip":[-91,-100],"world_contact":[628.5,315]},"S5_beak_F":{"bird_translation":[670,449],"scale":0.8,"world_contact":[782,422.6]},"S5_F_buckle_P_A":{"f1950":{"F_lower_endpoint":[788.52223994,447.34133082],"shared_buckle_P_A_anchor":[792,463]},"f2159_2160":{"F_lower_endpoint":[879.17140594,378.10166427],"shared_buckle_P_A_anchor":[867,360]},"connection":"Continuous 5-unit red paper tongue joins the exact black buckle/P-A anchor; beak-to-F contact retained."},"S8_foot_F":{"bird_translation":[1053,143],"scale":0.8,"foot_baseline":181.4,"frame_top":181}},"E_windows":{"E01":[810,1140],"E02":[2280,2700],"E03":[2820,3090]},"S7_absent_holds":{"F":[2985,3015],"middle":[3045,3075]},"production_cues":"NOT_CREATED_BY_R03"};
},
"assets/gfx-workload.json": function(module,exports,require){
module.exports = {"status":"DESIGN_REQUIREMENTS_NOT_MEASURED","default_profile":"film","diagnostic_profiles_only":true,"families":[{"id":"path_deformation","windows":[[630,1200],[2100,2700]],"owners":["T06","T11"],"varying_properties":["ribbon Bézier control points","shared paper hinge vertices","mask boundary paths"],"off_control":"Same frame and pose with recorded rigid simplified paths; labels mark diagnostic, never substitute as final.","diagnostic_switch_id":"path_deformation"},{"id":"material_masks","windows":[[810,1140],[1380,1470],[2280,2700],[2820,2910]],"owners":["T03","T05","T06","T07","T08","T11"],"varying_properties":["two ribbon weave clip/mask boundaries","common gate/foreground occluder silhouette","local paper peel boundary"],"off_control":"Bypass only named local occlusion, leaving geometry/time unchanged. Expected spatial errors are evidence of function, not acceptable film.","diagnostic_switch_id":"material_masks"},{"id":"text_path_layout","windows":[[810,1050],[2820,2910]],"owners":["T14"],"varying_properties":["one actual short source label path","two-column to two-row source slip layout"],"off_control":"Freeze text path/layout at documented start state; text content stays exact. No per-frame forced reflow.","diagnostic_switch_id":null},{"id":"leader_geometry","windows":[[960,1050],[2820,2910],[3090,3150]],"owners":["T12"],"varying_properties":["object-to-label bound endpoints","routed elbow/control points"],"off_control":"Freeze leader endpoints to expose detachment, not replace objects.","diagnostic_switch_id":null},{"id":"text_reveal","windows":[[810,840],[2820,2880],[3360,3420]],"owners":["T14","T15"],"varying_properties":["text clip boundary, no glyph deformation"],"off_control":"Show exact same glyphs in their stable location from start.","diagnostic_switch_id":"text_reveal"},{"id":"gradient_alpha_reveal","windows":[[810,1140],[1710,1770],[2160,2700],[2820,2910]],"owners":["T06","T11","T13"],"varying_properties":["local seam gradient coordinate/extent","waveform inlet reveal edge","paper contact overlap"],"off_control":"Hard-edge local mask replacing soft edge; same mask footprint and all geometry unchanged.","diagnostic_switch_id":"gradient_alpha_reveal"},{"id":"local_blur","windows":[[2280,2700]],"owners":["T03"],"varying_properties":["two small contact patch widths/positions driven by settle; sigma chosen≤2.6 authorunits","tightly bounded filter region"],"off_control":"Keep exact patch geometry, opacity0.09, bounds and time; remove feGaussianBlur only. No whole-scene blur.","diagnostic_switch_id":"local_blur"},{"id":"translucent_trails","windows":[[1404,1452]],"owners":["T08"],"varying_properties":["one wing silhouette computed from global_f-3","shared current gate mask"],"off_control":"Remove historical silhouette only; main B stays sharp. opacity≤0.09, no post-1451 residue.","diagnostic_switch_id":"translucent_trails"},{"id":"real_waveform","windows":[[1710,2160]],"owners":["T13"],"varying_properties":["visible true envelope segment/current global cue tick"],"off_control":"No fake substitute; use T13 removal-control as distinct test.","diagnostic_switch_id":null}],"E01":{"window":[810,1140],"meaning":"Three deforming carriers, two crossings, real source textPath and completed gate. Text stabilizes1050–1140."},"E02":{"window":[2280,2700],"meaning":"Sustained14-second shared hinge/camera/occluder/reveal work. Eight active gfx, not a forced twelve-layer claim."},"E03":{"window":[2820,3090],"meaning":"Complex original scene with source layouts and ordered full removals. Intentional stable holds included; no constant-motion fiction."},"comparisons":"Keep source/media/fonts/dimensions/fps/frame set/backend/device and cache state recorded. effects.json is a declared cache dependency. Change one family per copy and record changed input identity. Never report one average from unequal runs.","forbidden_claims":["No navfree/static-span/fast-path eligibility is asserted","No GPU utilization, memory, fps, elapsed time, PNG size or render load was measured","No geometry count or effect API name is evidence of expensive execution","Diagnostic variants are not official acceptance renders"],"diagnostic_switches":{"path_deformation":true,"material_masks":true,"text_reveal":true,"translucent_trails":true,"gradient_alpha_reveal":true,"local_blur":true}};
},
"assets/media-bindings.json": function(module,exports,require){
module.exports = {"status":"ACTUAL_NATIVE_AUDIO_ONLY_REFERENCE_LISTENING_AND_FULL_RENDER_PENDING","waveform":{"format":"framivra-waveform/1","provenance":{"kind":"actual-pcm","stage":"framivra-audio-stage","sha256":"ed1053b0b803e33830d4d2e0c42c09294de7c16b7c361da57ac028a11bab17d8","source":"out/audio.wav","source_codec":"pcm_s16le","pcm_sha256":"6dc62e318d0f571dc7d614c72ee1eb52b99b1870b08a4d5f998e0b08213cd9c8","pcm_hash_representation":"native-rate decoded interleaved little-endian float32; no resampling or gain change","sample_rate":48000,"channels":2,"sample_frames":5760000,"mix_recipe_sha256":"e3ab364eb2b510c5958e710e9761fd73ff881e9c800101571cd6275d198a0ae6"},"analysis_frame_window":[1680,2160],"analysis_sample_window":[2688000,3456000],"bins":480,"amplitude_mapping":{"kind":"stereo-channel-union","fixed_scale":1,"definition":"min/max of native decoded PCM across both channels; value=max(abs(min),abs(max)); full scale always 1. No per-bin or excerpt normalization."},"samples":[{"frame":1680,"sample_start":2688000,"sample_end":2689600,"min":-0.454498291015625,"max":0.5989990234375,"value":0.5989990234375},{"frame":1681,"sample_start":2689600,"sample_end":2691200,"min":-0.447509765625,"max":0.589447021484375,"value":0.589447021484375},{"frame":1682,"sample_start":2691200,"sample_end":2692800,"min":-0.34576416015625,"max":0.459991455078125,"value":0.459991455078125},{"frame":1683,"sample_start":2692800,"sample_end":2694400,"min":-0.59088134765625,"max":0.5989990234375,"value":0.5989990234375},{"frame":1684,"sample_start":2694400,"sample_end":2696000,"min":-0.59326171875,"max":0.5989990234375,"value":0.5989990234375},{"frame":1685,"sample_start":2696000,"sample_end":2697600,"min":-0.4959716796875,"max":0.470367431640625,"value":0.4959716796875},{"frame":1686,"sample_start":2697600,"sample_end":2699200,"min":-0.5989990234375,"max":0.598388671875,"value":0.5989990234375},{"frame":1687,"sample_start":2699200,"sample_end":2700800,"min":-0.5989990234375,"max":0.5989990234375,"value":0.5989990234375},{"frame":1688,"sample_start":2700800,"sample_end":2702400,"min":-0.451995849609375,"max":0.478790283203125,"value":0.478790283203125},{"frame":1689,"sample_start":2702400,"sample_end":2704000,"min":-0.43585205078125,"max":0.5299072265625,"value":0.5299072265625},{"frame":1690,"sample_start":2704000,"sample_end":2705600,"min":-0.355133056640625,"max":0.464874267578125,"value":0.464874267578125},{"frame":1691,"sample_start":2705600,"sample_end":2707200,"min":-0.292724609375,"max":0.32525634765625,"value":0.32525634765625},{"frame":1692,"sample_start":2707200,"sample_end":2708800,"min":-0.2655029296875,"max":0.2567138671875,"value":0.2655029296875},{"frame":1693,"sample_start":2708800,"sample_end":2710400,"min":-0.149932861328125,"max":0.1800537109375,"value":0.1800537109375},{"frame":1694,"sample_start":2710400,"sample_end":2712000,"min":-0.15179443359375,"max":0.13836669921875,"value":0.15179443359375},{"frame":1695,"sample_start":2712000,"sample_end":2713600,"min":-0.10296630859375,"max":0.122406005859375,"value":0.122406005859375},{"frame":1696,"sample_start":2713600,"sample_end":2715200,"min":-0.08343505859375,"max":0.0848388671875,"value":0.0848388671875},{"frame":1697,"sample_start":2715200,"sample_end":2716800,"min":-0.079498291015625,"max":0.070556640625,"value":0.079498291015625},{"frame":1698,"sample_start":2716800,"sample_end":2718400,"min":-0.047149658203125,"max":0.050018310546875,"value":0.050018310546875},{"frame":1699,"sample_start":2718400,"sample_end":2720000,"min":-0.046539306640625,"max":0.04119873046875,"value":0.046539306640625},{"frame":1700,"sample_start":2720000,"sample_end":2721600,"min":-0.027008056640625,"max":0.041168212890625,"value":0.041168212890625},{"frame":1701,"sample_start":2721600,"sample_end":2723200,"min":-0.0244140625,"max":0.02227783203125,"value":0.0244140625},{"frame":1702,"sample_start":2723200,"sample_end":2724800,"min":-0.020172119140625,"max":0.0191650390625,"value":0.020172119140625},{"frame":1703,"sample_start":2724800,"sample_end":2726400,"min":-0.01641845703125,"max":0.01434326171875,"value":0.01641845703125},{"frame":1704,"sample_start":2726400,"sample_end":2728000,"min":-0.014678955078125,"max":0.01312255859375,"value":0.014678955078125},{"frame":1705,"sample_start":2728000,"sample_end":2729600,"min":-0.010772705078125,"max":0.0086669921875,"value":0.010772705078125},{"frame":1706,"sample_start":2729600,"sample_end":2731200,"min":-0.008453369140625,"max":0.006500244140625,"value":0.008453369140625},{"frame":1707,"sample_start":2731200,"sample_end":2732800,"min":-0.0064697265625,"max":0.00616455078125,"value":0.0064697265625},{"frame":1708,"sample_start":2732800,"sample_end":2734400,"min":-0.00537109375,"max":0.004150390625,"value":0.00537109375},{"frame":1709,"sample_start":2734400,"sample_end":2736000,"min":-0.004119873046875,"max":0.00408935546875,"value":0.004119873046875},{"frame":1710,"sample_start":2736000,"sample_end":2737600,"min":-0.002716064453125,"max":0.0025634765625,"value":0.002716064453125},{"frame":1711,"sample_start":2737600,"sample_end":2739200,"min":-0.00250244140625,"max":0.002471923828125,"value":0.00250244140625},{"frame":1712,"sample_start":2739200,"sample_end":2740800,"min":-0.0015869140625,"max":0.001922607421875,"value":0.001922607421875},{"frame":1713,"sample_start":2740800,"sample_end":2742400,"min":-0.00128173828125,"max":0.001220703125,"value":0.00128173828125},{"frame":1714,"sample_start":2742400,"sample_end":2744000,"min":-0.001190185546875,"max":0.001129150390625,"value":0.001190185546875},{"frame":1715,"sample_start":2744000,"sample_end":2745600,"min":-0.001007080078125,"max":0.00079345703125,"value":0.001007080078125},{"frame":1716,"sample_start":2745600,"sample_end":2747200,"min":-0.001068115234375,"max":0.000640869140625,"value":0.001068115234375},{"frame":1717,"sample_start":2747200,"sample_end":2748800,"min":-0.000823974609375,"max":0.000946044921875,"value":0.000946044921875},{"frame":1718,"sample_start":2748800,"sample_end":2750400,"min":-0.0013427734375,"max":0.001678466796875,"value":0.001678466796875},{"frame":1719,"sample_start":2750400,"sample_end":2752000,"min":-0.003204345703125,"max":0.003448486328125,"value":0.003448486328125},{"frame":1720,"sample_start":2752000,"sample_end":2753600,"min":-0.006378173828125,"max":0.006378173828125,"value":0.006378173828125},{"frame":1721,"sample_start":2753600,"sample_end":2755200,"min":-0.00384521484375,"max":0.00445556640625,"value":0.00445556640625},{"frame":1722,"sample_start":2755200,"sample_end":2756800,"min":-0.002655029296875,"max":0.00299072265625,"value":0.00299072265625},{"frame":1723,"sample_start":2756800,"sample_end":2758400,"min":-0.00555419921875,"max":0.005096435546875,"value":0.00555419921875},{"frame":1724,"sample_start":2758400,"sample_end":2760000,"min":-0.003997802734375,"max":0.005218505859375,"value":0.005218505859375},{"frame":1725,"sample_start":2760000,"sample_end":2761600,"min":-0.001068115234375,"max":0.001373291015625,"value":0.001373291015625},{"frame":1726,"sample_start":2761600,"sample_end":2763200,"min":-0.00244140625,"max":0.001922607421875,"value":0.00244140625},{"frame":1727,"sample_start":2763200,"sample_end":2764800,"min":-0.00177001953125,"max":0.002655029296875,"value":0.002655029296875},{"frame":1728,"sample_start":2764800,"sample_end":2766400,"min":-0.0013427734375,"max":0.00091552734375,"value":0.0013427734375},{"frame":1729,"sample_start":2766400,"sample_end":2768000,"min":-0.00189208984375,"max":0.001800537109375,"value":0.00189208984375},{"frame":1730,"sample_start":2768000,"sample_end":2769600,"min":-0.00164794921875,"max":0.001312255859375,"value":0.00164794921875},{"frame":1731,"sample_start":2769600,"sample_end":2771200,"min":0,"max":0,"value":0},{"frame":1732,"sample_start":2771200,"sample_end":2772800,"min":0,"max":0,"value":0},{"frame":1733,"sample_start":2772800,"sample_end":2774400,"min":0,"max":0,"value":0},{"frame":1734,"sample_start":2774400,"sample_end":2776000,"min":0,"max":0,"value":0},{"frame":1735,"sample_start":2776000,"sample_end":2777600,"min":0,"max":0,"value":0},{"frame":1736,"sample_start":2777600,"sample_end":2779200,"min":0,"max":0,"value":0},{"frame":1737,"sample_start":2779200,"sample_end":2780800,"min":0,"max":0,"value":0},{"frame":1738,"sample_start":2780800,"sample_end":2782400,"min":0,"max":0,"value":0},{"frame":1739,"sample_start":2782400,"sample_end":2784000,"min":0,"max":0,"value":0},{"frame":1740,"sample_start":2784000,"sample_end":2785600,"min":-0.367279052734375,"max":0.39788818359375,"value":0.39788818359375},{"frame":1741,"sample_start":2785600,"sample_end":2787200,"min":-0.34765625,"max":0.39544677734375,"value":0.39544677734375},{"frame":1742,"sample_start":2787200,"sample_end":2788800,"min":-0.244384765625,"max":0.28546142578125,"value":0.28546142578125},{"frame":1743,"sample_start":2788800,"sample_end":2790400,"min":-0.18890380859375,"max":0.20361328125,"value":0.20361328125},{"frame":1744,"sample_start":2790400,"sample_end":2792000,"min":-0.148712158203125,"max":0.14764404296875,"value":0.148712158203125},{"frame":1745,"sample_start":2792000,"sample_end":2793600,"min":-0.117523193359375,"max":0.11199951171875,"value":0.117523193359375},{"frame":1746,"sample_start":2793600,"sample_end":2795200,"min":-0.094635009765625,"max":0.083160400390625,"value":0.094635009765625},{"frame":1747,"sample_start":2795200,"sample_end":2796800,"min":-0.074371337890625,"max":0.062408447265625,"value":0.074371337890625},{"frame":1748,"sample_start":2796800,"sample_end":2798400,"min":-0.05853271484375,"max":0.0472412109375,"value":0.05853271484375},{"frame":1749,"sample_start":2798400,"sample_end":2800000,"min":-0.04595947265625,"max":0.037017822265625,"value":0.04595947265625},{"frame":1750,"sample_start":2800000,"sample_end":2801600,"min":-0.0369873046875,"max":0.0283203125,"value":0.0369873046875},{"frame":1751,"sample_start":2801600,"sample_end":2803200,"min":-0.029083251953125,"max":0.021728515625,"value":0.029083251953125},{"frame":1752,"sample_start":2803200,"sample_end":2804800,"min":-0.02288818359375,"max":0.01678466796875,"value":0.02288818359375},{"frame":1753,"sample_start":2804800,"sample_end":2806400,"min":-0.017974853515625,"max":0.013092041015625,"value":0.017974853515625},{"frame":1754,"sample_start":2806400,"sample_end":2808000,"min":-0.014190673828125,"max":0.010284423828125,"value":0.014190673828125},{"frame":1755,"sample_start":2808000,"sample_end":2809600,"min":-0.011444091796875,"max":0.00799560546875,"value":0.011444091796875},{"frame":1756,"sample_start":2809600,"sample_end":2811200,"min":-0.009033203125,"max":0.006378173828125,"value":0.009033203125},{"frame":1757,"sample_start":2811200,"sample_end":2812800,"min":-0.007110595703125,"max":0.0050048828125,"value":0.007110595703125},{"frame":1758,"sample_start":2812800,"sample_end":2814400,"min":-0.005584716796875,"max":0.00390625,"value":0.005584716796875},{"frame":1759,"sample_start":2814400,"sample_end":2816000,"min":-0.004547119140625,"max":0.003021240234375,"value":0.004547119140625},{"frame":1760,"sample_start":2816000,"sample_end":2817600,"min":-0.0035400390625,"max":0.002410888671875,"value":0.0035400390625},{"frame":1761,"sample_start":2817600,"sample_end":2819200,"min":-0.002838134765625,"max":0.00189208984375,"value":0.002838134765625},{"frame":1762,"sample_start":2819200,"sample_end":2820800,"min":-0.002227783203125,"max":0.001495361328125,"value":0.002227783203125},{"frame":1763,"sample_start":2820800,"sample_end":2822400,"min":-0.001800537109375,"max":0.00115966796875,"value":0.001800537109375},{"frame":1764,"sample_start":2822400,"sample_end":2824000,"min":-0.001434326171875,"max":0.000885009765625,"value":0.001434326171875},{"frame":1765,"sample_start":2824000,"sample_end":2825600,"min":-0.001129150390625,"max":0.000732421875,"value":0.001129150390625},{"frame":1766,"sample_start":2825600,"sample_end":2827200,"min":-0.000885009765625,"max":0.00054931640625,"value":0.000885009765625},{"frame":1767,"sample_start":2827200,"sample_end":2828800,"min":-0.000701904296875,"max":0.00042724609375,"value":0.000701904296875},{"frame":1768,"sample_start":2828800,"sample_end":2830400,"min":-0.000579833984375,"max":0.000335693359375,"value":0.000579833984375},{"frame":1769,"sample_start":2830400,"sample_end":2832000,"min":-0.00048828125,"max":0.000244140625,"value":0.00048828125},{"frame":1770,"sample_start":2832000,"sample_end":2833600,"min":-0.0003662109375,"max":0.00018310546875,"value":0.0003662109375},{"frame":1771,"sample_start":2833600,"sample_end":2835200,"min":-0.000274658203125,"max":0.0001220703125,"value":0.000274658203125},{"frame":1772,"sample_start":2835200,"sample_end":2836800,"min":-0.000213623046875,"max":0.0001220703125,"value":0.000213623046875},{"frame":1773,"sample_start":2836800,"sample_end":2838400,"min":-0.000213623046875,"max":0.00006103515625,"value":0.000213623046875},{"frame":1774,"sample_start":2838400,"sample_end":2840000,"min":-0.000213623046875,"max":0.00018310546875,"value":0.000213623046875},{"frame":1775,"sample_start":2840000,"sample_end":2841600,"min":-0.00030517578125,"max":0.000274658203125,"value":0.00030517578125},{"frame":1776,"sample_start":2841600,"sample_end":2843200,"min":-0.000885009765625,"max":0.00103759765625,"value":0.00103759765625},{"frame":1777,"sample_start":2843200,"sample_end":2844800,"min":-0.001739501953125,"max":0.00146484375,"value":0.001739501953125},{"frame":1778,"sample_start":2844800,"sample_end":2846400,"min":-0.001495361328125,"max":0.001983642578125,"value":0.001983642578125},{"frame":1779,"sample_start":2846400,"sample_end":2848000,"min":0,"max":0,"value":0},{"frame":1780,"sample_start":2848000,"sample_end":2849600,"min":0,"max":0,"value":0},{"frame":1781,"sample_start":2849600,"sample_end":2851200,"min":0,"max":0,"value":0},{"frame":1782,"sample_start":2851200,"sample_end":2852800,"min":0,"max":0,"value":0},{"frame":1783,"sample_start":2852800,"sample_end":2854400,"min":0,"max":0,"value":0},{"frame":1784,"sample_start":2854400,"sample_end":2856000,"min":0,"max":0,"value":0},{"frame":1785,"sample_start":2856000,"sample_end":2857600,"min":0,"max":0,"value":0},{"frame":1786,"sample_start":2857600,"sample_end":2859200,"min":0,"max":0,"value":0},{"frame":1787,"sample_start":2859200,"sample_end":2860800,"min":0,"max":0,"value":0},{"frame":1788,"sample_start":2860800,"sample_end":2862400,"min":0,"max":0,"value":0},{"frame":1789,"sample_start":2862400,"sample_end":2864000,"min":0,"max":0,"value":0},{"frame":1790,"sample_start":2864000,"sample_end":2865600,"min":0,"max":0,"value":0},{"frame":1791,"sample_start":2865600,"sample_end":2867200,"min":0,"max":0,"value":0},{"frame":1792,"sample_start":2867200,"sample_end":2868800,"min":0,"max":0,"value":0},{"frame":1793,"sample_start":2868800,"sample_end":2870400,"min":0,"max":0,"value":0},{"frame":1794,"sample_start":2870400,"sample_end":2872000,"min":0,"max":0,"value":0},{"frame":1795,"sample_start":2872000,"sample_end":2873600,"min":0,"max":0,"value":0},{"frame":1796,"sample_start":2873600,"sample_end":2875200,"min":0,"max":0,"value":0},{"frame":1797,"sample_start":2875200,"sample_end":2876800,"min":0,"max":0,"value":0},{"frame":1798,"sample_start":2876800,"sample_end":2878400,"min":0,"max":0,"value":0},{"frame":1799,"sample_start":2878400,"sample_end":2880000,"min":0,"max":0,"value":0},{"frame":1800,"sample_start":2880000,"sample_end":2881600,"min":-0.5989990234375,"max":0.585662841796875,"value":0.5989990234375},{"frame":1801,"sample_start":2881600,"sample_end":2883200,"min":-0.33258056640625,"max":0.307769775390625,"value":0.33258056640625},{"frame":1802,"sample_start":2883200,"sample_end":2884800,"min":-0.1370849609375,"max":0.140869140625,"value":0.140869140625},{"frame":1803,"sample_start":2884800,"sample_end":2886400,"min":-0.054046630859375,"max":0.052825927734375,"value":0.054046630859375},{"frame":1804,"sample_start":2886400,"sample_end":2888000,"min":-0.01873779296875,"max":0.017974853515625,"value":0.01873779296875},{"frame":1805,"sample_start":2888000,"sample_end":2889600,"min":0,"max":0,"value":0},{"frame":1806,"sample_start":2889600,"sample_end":2891200,"min":0,"max":0,"value":0},{"frame":1807,"sample_start":2891200,"sample_end":2892800,"min":0,"max":0,"value":0},{"frame":1808,"sample_start":2892800,"sample_end":2894400,"min":0,"max":0,"value":0},{"frame":1809,"sample_start":2894400,"sample_end":2896000,"min":0,"max":0,"value":0},{"frame":1810,"sample_start":2896000,"sample_end":2897600,"min":0,"max":0,"value":0},{"frame":1811,"sample_start":2897600,"sample_end":2899200,"min":0,"max":0,"value":0},{"frame":1812,"sample_start":2899200,"sample_end":2900800,"min":0,"max":0,"value":0},{"frame":1813,"sample_start":2900800,"sample_end":2902400,"min":0,"max":0,"value":0},{"frame":1814,"sample_start":2902400,"sample_end":2904000,"min":0,"max":0,"value":0},{"frame":1815,"sample_start":2904000,"sample_end":2905600,"min":0,"max":0,"value":0},{"frame":1816,"sample_start":2905600,"sample_end":2907200,"min":0,"max":0,"value":0},{"frame":1817,"sample_start":2907200,"sample_end":2908800,"min":0,"max":0,"value":0},{"frame":1818,"sample_start":2908800,"sample_end":2910400,"min":0,"max":0,"value":0},{"frame":1819,"sample_start":2910400,"sample_end":2912000,"min":0,"max":0,"value":0},{"frame":1820,"sample_start":2912000,"sample_end":2913600,"min":0,"max":0,"value":0},{"frame":1821,"sample_start":2913600,"sample_end":2915200,"min":0,"max":0,"value":0},{"frame":1822,"sample_start":2915200,"sample_end":2916800,"min":0,"max":0,"value":0},{"frame":1823,"sample_start":2916800,"sample_end":2918400,"min":0,"max":0,"value":0},{"frame":1824,"sample_start":2918400,"sample_end":2920000,"min":0,"max":0,"value":0},{"frame":1825,"sample_start":2920000,"sample_end":2921600,"min":0,"max":0,"value":0},{"frame":1826,"sample_start":2921600,"sample_end":2923200,"min":0,"max":0,"value":0},{"frame":1827,"sample_start":2923200,"sample_end":2924800,"min":0,"max":0,"value":0},{"frame":1828,"sample_start":2924800,"sample_end":2926400,"min":0,"max":0,"value":0},{"frame":1829,"sample_start":2926400,"sample_end":2928000,"min":0,"max":0,"value":0},{"frame":1830,"sample_start":2928000,"sample_end":2929600,"min":-0.4107666015625,"max":0.36083984375,"value":0.4107666015625},{"frame":1831,"sample_start":2929600,"sample_end":2931200,"min":-0.4158935546875,"max":0.354705810546875,"value":0.4158935546875},{"frame":1832,"sample_start":2931200,"sample_end":2932800,"min":-0.307464599609375,"max":0.25604248046875,"value":0.307464599609375},{"frame":1833,"sample_start":2932800,"sample_end":2934400,"min":-0.234893798828125,"max":0.193359375,"value":0.234893798828125},{"frame":1834,"sample_start":2934400,"sample_end":2936000,"min":-0.180267333984375,"max":0.14654541015625,"value":0.180267333984375},{"frame":1835,"sample_start":2936000,"sample_end":2937600,"min":-0.14300537109375,"max":0.11175537109375,"value":0.14300537109375},{"frame":1836,"sample_start":2937600,"sample_end":2939200,"min":-0.114593505859375,"max":0.083770751953125,"value":0.114593505859375},{"frame":1837,"sample_start":2939200,"sample_end":2940800,"min":-0.092071533203125,"max":0.0650634765625,"value":0.092071533203125},{"frame":1838,"sample_start":2940800,"sample_end":2942400,"min":-0.072509765625,"max":0.050811767578125,"value":0.072509765625},{"frame":1839,"sample_start":2942400,"sample_end":2944000,"min":-0.05865478515625,"max":0.0399169921875,"value":0.05865478515625},{"frame":1840,"sample_start":2944000,"sample_end":2945600,"min":-0.047576904296875,"max":0.031524658203125,"value":0.047576904296875},{"frame":1841,"sample_start":2945600,"sample_end":2947200,"min":-0.03857421875,"max":0.0244140625,"value":0.03857421875},{"frame":1842,"sample_start":2947200,"sample_end":2948800,"min":-0.03131103515625,"max":0.019439697265625,"value":0.03131103515625},{"frame":1843,"sample_start":2948800,"sample_end":2950400,"min":-0.02471923828125,"max":0.0155029296875,"value":0.02471923828125},{"frame":1844,"sample_start":2950400,"sample_end":2952000,"min":-0.020050048828125,"max":0.012451171875,"value":0.020050048828125},{"frame":1845,"sample_start":2952000,"sample_end":2953600,"min":-0.0162353515625,"max":0.009979248046875,"value":0.0162353515625},{"frame":1846,"sample_start":2953600,"sample_end":2955200,"min":-0.013214111328125,"max":0.008056640625,"value":0.013214111328125},{"frame":1847,"sample_start":2955200,"sample_end":2956800,"min":-0.01068115234375,"max":0.006317138671875,"value":0.01068115234375},{"frame":1848,"sample_start":2956800,"sample_end":2958400,"min":-0.0086669921875,"max":0.005096435546875,"value":0.0086669921875},{"frame":1849,"sample_start":2958400,"sample_end":2960000,"min":-0.006866455078125,"max":0.004119873046875,"value":0.006866455078125},{"frame":1850,"sample_start":2960000,"sample_end":2961600,"min":-0.005584716796875,"max":0.003326416015625,"value":0.005584716796875},{"frame":1851,"sample_start":2961600,"sample_end":2963200,"min":-0.0045166015625,"max":0.002685546875,"value":0.0045166015625},{"frame":1852,"sample_start":2963200,"sample_end":2964800,"min":-0.003692626953125,"max":0.002166748046875,"value":0.003692626953125},{"frame":1853,"sample_start":2964800,"sample_end":2966400,"min":-0.00299072265625,"max":0.001739501953125,"value":0.00299072265625},{"frame":1854,"sample_start":2966400,"sample_end":2968000,"min":-0.00238037109375,"max":0.001373291015625,"value":0.00238037109375},{"frame":1855,"sample_start":2968000,"sample_end":2969600,"min":-0.00189208984375,"max":0.00115966796875,"value":0.00189208984375},{"frame":1856,"sample_start":2969600,"sample_end":2971200,"min":-0.001556396484375,"max":0.00091552734375,"value":0.001556396484375},{"frame":1857,"sample_start":2971200,"sample_end":2972800,"min":-0.00128173828125,"max":0.000701904296875,"value":0.00128173828125},{"frame":1858,"sample_start":2972800,"sample_end":2974400,"min":-0.001007080078125,"max":0.000579833984375,"value":0.001007080078125},{"frame":1859,"sample_start":2974400,"sample_end":2976000,"min":-0.0008544921875,"max":0.00048828125,"value":0.0008544921875},{"frame":1860,"sample_start":2976000,"sample_end":2977600,"min":-0.58319091796875,"max":0.5989990234375,"value":0.5989990234375},{"frame":1861,"sample_start":2977600,"sample_end":2979200,"min":-0.3817138671875,"max":0.364105224609375,"value":0.3817138671875},{"frame":1862,"sample_start":2979200,"sample_end":2980800,"min":-0.200531005859375,"max":0.199920654296875,"value":0.200531005859375},{"frame":1863,"sample_start":2980800,"sample_end":2982400,"min":-0.1005859375,"max":0.1015625,"value":0.1015625},{"frame":1864,"sample_start":2982400,"sample_end":2984000,"min":-0.0506591796875,"max":0.050048828125,"value":0.0506591796875},{"frame":1865,"sample_start":2984000,"sample_end":2985600,"min":-0.025665283203125,"max":0.025146484375,"value":0.025665283203125},{"frame":1866,"sample_start":2985600,"sample_end":2987200,"min":-0.01226806640625,"max":0.0126953125,"value":0.0126953125},{"frame":1867,"sample_start":2987200,"sample_end":2988800,"min":-0.00152587890625,"max":0.001007080078125,"value":0.00152587890625},{"frame":1868,"sample_start":2988800,"sample_end":2990400,"min":-0.003204345703125,"max":0.003204345703125,"value":0.003204345703125},{"frame":1869,"sample_start":2990400,"sample_end":2992000,"min":-0.004364013671875,"max":0.0040283203125,"value":0.004364013671875},{"frame":1870,"sample_start":2992000,"sample_end":2993600,"min":-0.0028076171875,"max":0.001617431640625,"value":0.0028076171875},{"frame":1871,"sample_start":2993600,"sample_end":2995200,"min":0,"max":0,"value":0},{"frame":1872,"sample_start":2995200,"sample_end":2996800,"min":0,"max":0,"value":0},{"frame":1873,"sample_start":2996800,"sample_end":2998400,"min":0,"max":0,"value":0},{"frame":1874,"sample_start":2998400,"sample_end":3000000,"min":0,"max":0,"value":0},{"frame":1875,"sample_start":3000000,"sample_end":3001600,"min":0,"max":0,"value":0},{"frame":1876,"sample_start":3001600,"sample_end":3003200,"min":0,"max":0,"value":0},{"frame":1877,"sample_start":3003200,"sample_end":3004800,"min":0,"max":0,"value":0},{"frame":1878,"sample_start":3004800,"sample_end":3006400,"min":0,"max":0,"value":0},{"frame":1879,"sample_start":3006400,"sample_end":3008000,"min":0,"max":0,"value":0},{"frame":1880,"sample_start":3008000,"sample_end":3009600,"min":0,"max":0,"value":0},{"frame":1881,"sample_start":3009600,"sample_end":3011200,"min":0,"max":0,"value":0},{"frame":1882,"sample_start":3011200,"sample_end":3012800,"min":0,"max":0,"value":0},{"frame":1883,"sample_start":3012800,"sample_end":3014400,"min":0,"max":0,"value":0},{"frame":1884,"sample_start":3014400,"sample_end":3016000,"min":0,"max":0,"value":0},{"frame":1885,"sample_start":3016000,"sample_end":3017600,"min":0,"max":0,"value":0},{"frame":1886,"sample_start":3017600,"sample_end":3019200,"min":0,"max":0,"value":0},{"frame":1887,"sample_start":3019200,"sample_end":3020800,"min":0,"max":0,"value":0},{"frame":1888,"sample_start":3020800,"sample_end":3022400,"min":0,"max":0,"value":0},{"frame":1889,"sample_start":3022400,"sample_end":3024000,"min":0,"max":0,"value":0},{"frame":1890,"sample_start":3024000,"sample_end":3025600,"min":0,"max":0,"value":0},{"frame":1891,"sample_start":3025600,"sample_end":3027200,"min":0,"max":0,"value":0},{"frame":1892,"sample_start":3027200,"sample_end":3028800,"min":0,"max":0,"value":0},{"frame":1893,"sample_start":3028800,"sample_end":3030400,"min":0,"max":0,"value":0},{"frame":1894,"sample_start":3030400,"sample_end":3032000,"min":0,"max":0,"value":0},{"frame":1895,"sample_start":3032000,"sample_end":3033600,"min":0,"max":0,"value":0},{"frame":1896,"sample_start":3033600,"sample_end":3035200,"min":0,"max":0,"value":0},{"frame":1897,"sample_start":3035200,"sample_end":3036800,"min":0,"max":0,"value":0},{"frame":1898,"sample_start":3036800,"sample_end":3038400,"min":0,"max":0,"value":0},{"frame":1899,"sample_start":3038400,"sample_end":3040000,"min":0,"max":0,"value":0},{"frame":1900,"sample_start":3040000,"sample_end":3041600,"min":0,"max":0,"value":0},{"frame":1901,"sample_start":3041600,"sample_end":3043200,"min":0,"max":0,"value":0},{"frame":1902,"sample_start":3043200,"sample_end":3044800,"min":0,"max":0,"value":0},{"frame":1903,"sample_start":3044800,"sample_end":3046400,"min":0,"max":0,"value":0},{"frame":1904,"sample_start":3046400,"sample_end":3048000,"min":0,"max":0,"value":0},{"frame":1905,"sample_start":3048000,"sample_end":3049600,"min":0,"max":0,"value":0},{"frame":1906,"sample_start":3049600,"sample_end":3051200,"min":0,"max":0,"value":0},{"frame":1907,"sample_start":3051200,"sample_end":3052800,"min":0,"max":0,"value":0},{"frame":1908,"sample_start":3052800,"sample_end":3054400,"min":0,"max":0,"value":0},{"frame":1909,"sample_start":3054400,"sample_end":3056000,"min":0,"max":0,"value":0},{"frame":1910,"sample_start":3056000,"sample_end":3057600,"min":0,"max":0,"value":0},{"frame":1911,"sample_start":3057600,"sample_end":3059200,"min":0,"max":0,"value":0},{"frame":1912,"sample_start":3059200,"sample_end":3060800,"min":0,"max":0,"value":0},{"frame":1913,"sample_start":3060800,"sample_end":3062400,"min":0,"max":0,"value":0},{"frame":1914,"sample_start":3062400,"sample_end":3064000,"min":0,"max":0,"value":0},{"frame":1915,"sample_start":3064000,"sample_end":3065600,"min":0,"max":0,"value":0},{"frame":1916,"sample_start":3065600,"sample_end":3067200,"min":0,"max":0,"value":0},{"frame":1917,"sample_start":3067200,"sample_end":3068800,"min":0,"max":0,"value":0},{"frame":1918,"sample_start":3068800,"sample_end":3070400,"min":0,"max":0,"value":0},{"frame":1919,"sample_start":3070400,"sample_end":3072000,"min":0,"max":0,"value":0},{"frame":1920,"sample_start":3072000,"sample_end":3073600,"min":-0.56488037109375,"max":0.42547607421875,"value":0.56488037109375},{"frame":1921,"sample_start":3073600,"sample_end":3075200,"min":-0.557708740234375,"max":0.427490234375,"value":0.557708740234375},{"frame":1922,"sample_start":3075200,"sample_end":3076800,"min":-0.443359375,"max":0.31219482421875,"value":0.443359375},{"frame":1923,"sample_start":3076800,"sample_end":3078400,"min":-0.5989990234375,"max":0.549285888671875,"value":0.5989990234375},{"frame":1924,"sample_start":3078400,"sample_end":3080000,"min":-0.524993896484375,"max":0.538543701171875,"value":0.538543701171875},{"frame":1925,"sample_start":3080000,"sample_end":3081600,"min":-0.413970947265625,"max":0.40130615234375,"value":0.413970947265625},{"frame":1926,"sample_start":3081600,"sample_end":3083200,"min":-0.405242919921875,"max":0.59088134765625,"value":0.59088134765625},{"frame":1927,"sample_start":3083200,"sample_end":3084800,"min":-0.35296630859375,"max":0.41619873046875,"value":0.41619873046875},{"frame":1928,"sample_start":3084800,"sample_end":3086400,"min":-0.244110107421875,"max":0.342254638671875,"value":0.342254638671875},{"frame":1929,"sample_start":3086400,"sample_end":3088000,"min":-0.35809326171875,"max":0.29608154296875,"value":0.35809326171875},{"frame":1930,"sample_start":3088000,"sample_end":3089600,"min":-0.301666259765625,"max":0.24639892578125,"value":0.301666259765625},{"frame":1931,"sample_start":3089600,"sample_end":3091200,"min":-0.205596923828125,"max":0.15869140625,"value":0.205596923828125},{"frame":1932,"sample_start":3091200,"sample_end":3092800,"min":-0.156219482421875,"max":0.10986328125,"value":0.156219482421875},{"frame":1933,"sample_start":3092800,"sample_end":3094400,"min":-0.11016845703125,"max":0.084686279296875,"value":0.11016845703125},{"frame":1934,"sample_start":3094400,"sample_end":3096000,"min":-0.07733154296875,"max":0.07012939453125,"value":0.07733154296875},{"frame":1935,"sample_start":3096000,"sample_end":3097600,"min":-0.063873291015625,"max":0.056121826171875,"value":0.063873291015625},{"frame":1936,"sample_start":3097600,"sample_end":3099200,"min":-0.045562744140625,"max":0.038238525390625,"value":0.045562744140625},{"frame":1937,"sample_start":3099200,"sample_end":3100800,"min":-0.034942626953125,"max":0.032196044921875,"value":0.034942626953125},{"frame":1938,"sample_start":3100800,"sample_end":3102400,"min":-0.02581787109375,"max":0.02117919921875,"value":0.02581787109375},{"frame":1939,"sample_start":3102400,"sample_end":3104000,"min":-0.021484375,"max":0.0167236328125,"value":0.021484375},{"frame":1940,"sample_start":3104000,"sample_end":3105600,"min":-0.01849365234375,"max":0.013031005859375,"value":0.01849365234375},{"frame":1941,"sample_start":3105600,"sample_end":3107200,"min":-0.014129638671875,"max":0.01251220703125,"value":0.014129638671875},{"frame":1942,"sample_start":3107200,"sample_end":3108800,"min":-0.010498046875,"max":0.010528564453125,"value":0.010528564453125},{"frame":1943,"sample_start":3108800,"sample_end":3110400,"min":-0.00836181640625,"max":0.009063720703125,"value":0.009063720703125},{"frame":1944,"sample_start":3110400,"sample_end":3112000,"min":-0.007171630859375,"max":0.00732421875,"value":0.00732421875},{"frame":1945,"sample_start":3112000,"sample_end":3113600,"min":-0.005584716796875,"max":0.00421142578125,"value":0.005584716796875},{"frame":1946,"sample_start":3113600,"sample_end":3115200,"min":-0.0045166015625,"max":0.003448486328125,"value":0.0045166015625},{"frame":1947,"sample_start":3115200,"sample_end":3116800,"min":-0.003662109375,"max":0.002593994140625,"value":0.003662109375},{"frame":1948,"sample_start":3116800,"sample_end":3118400,"min":-0.0028076171875,"max":0.00225830078125,"value":0.0028076171875},{"frame":1949,"sample_start":3118400,"sample_end":3120000,"min":-0.002227783203125,"max":0.001983642578125,"value":0.002227783203125},{"frame":1950,"sample_start":3120000,"sample_end":3121600,"min":-0.5989990234375,"max":0.573944091796875,"value":0.5989990234375},{"frame":1951,"sample_start":3121600,"sample_end":3123200,"min":-0.4210205078125,"max":0.394622802734375,"value":0.4210205078125},{"frame":1952,"sample_start":3123200,"sample_end":3124800,"min":-0.25250244140625,"max":0.250152587890625,"value":0.25250244140625},{"frame":1953,"sample_start":3124800,"sample_end":3126400,"min":-0.13946533203125,"max":0.142303466796875,"value":0.142303466796875},{"frame":1954,"sample_start":3126400,"sample_end":3128000,"min":-0.07904052734375,"max":0.0777587890625,"value":0.07904052734375},{"frame":1955,"sample_start":3128000,"sample_end":3129600,"min":-0.04522705078125,"max":0.04425048828125,"value":0.04522705078125},{"frame":1956,"sample_start":3129600,"sample_end":3131200,"min":-0.024627685546875,"max":0.024749755859375,"value":0.024749755859375},{"frame":1957,"sample_start":3131200,"sample_end":3132800,"min":-0.013763427734375,"max":0.014129638671875,"value":0.014129638671875},{"frame":1958,"sample_start":3132800,"sample_end":3134400,"min":-0.00653076171875,"max":0.00531005859375,"value":0.00653076171875},{"frame":1959,"sample_start":3134400,"sample_end":3136000,"min":-0.007568359375,"max":0.008453369140625,"value":0.008453369140625},{"frame":1960,"sample_start":3136000,"sample_end":3137600,"min":-0.003204345703125,"max":0.0030517578125,"value":0.003204345703125},{"frame":1961,"sample_start":3137600,"sample_end":3139200,"min":-0.001617431640625,"max":0.00146484375,"value":0.001617431640625},{"frame":1962,"sample_start":3139200,"sample_end":3140800,"min":-0.0018310546875,"max":0.001983642578125,"value":0.001983642578125},{"frame":1963,"sample_start":3140800,"sample_end":3142400,"min":-0.001007080078125,"max":0.001007080078125,"value":0.001007080078125},{"frame":1964,"sample_start":3142400,"sample_end":3144000,"min":-0.001312255859375,"max":0.00164794921875,"value":0.00164794921875},{"frame":1965,"sample_start":3144000,"sample_end":3145600,"min":-0.319122314453125,"max":0.308929443359375,"value":0.319122314453125},{"frame":1966,"sample_start":3145600,"sample_end":3147200,"min":-0.09002685546875,"max":0.09356689453125,"value":0.09356689453125},{"frame":1967,"sample_start":3147200,"sample_end":3148800,"min":-0.016571044921875,"max":0.01611328125,"value":0.016571044921875},{"frame":1968,"sample_start":3148800,"sample_end":3150400,"min":-0.000701904296875,"max":0.000640869140625,"value":0.000701904296875},{"frame":1969,"sample_start":3150400,"sample_end":3152000,"min":-0.0001220703125,"max":0.000244140625,"value":0.000244140625},{"frame":1970,"sample_start":3152000,"sample_end":3153600,"min":0,"max":0,"value":0},{"frame":1971,"sample_start":3153600,"sample_end":3155200,"min":0,"max":0,"value":0},{"frame":1972,"sample_start":3155200,"sample_end":3156800,"min":0,"max":0,"value":0},{"frame":1973,"sample_start":3156800,"sample_end":3158400,"min":0,"max":0,"value":0},{"frame":1974,"sample_start":3158400,"sample_end":3160000,"min":0,"max":0,"value":0},{"frame":1975,"sample_start":3160000,"sample_end":3161600,"min":0,"max":0,"value":0},{"frame":1976,"sample_start":3161600,"sample_end":3163200,"min":0,"max":0,"value":0},{"frame":1977,"sample_start":3163200,"sample_end":3164800,"min":0,"max":0,"value":0},{"frame":1978,"sample_start":3164800,"sample_end":3166400,"min":0,"max":0,"value":0},{"frame":1979,"sample_start":3166400,"sample_end":3168000,"min":0,"max":0,"value":0},{"frame":1980,"sample_start":3168000,"sample_end":3169600,"min":0,"max":0,"value":0},{"frame":1981,"sample_start":3169600,"sample_end":3171200,"min":0,"max":0,"value":0},{"frame":1982,"sample_start":3171200,"sample_end":3172800,"min":0,"max":0,"value":0},{"frame":1983,"sample_start":3172800,"sample_end":3174400,"min":0,"max":0,"value":0},{"frame":1984,"sample_start":3174400,"sample_end":3176000,"min":0,"max":0,"value":0},{"frame":1985,"sample_start":3176000,"sample_end":3177600,"min":0,"max":0,"value":0},{"frame":1986,"sample_start":3177600,"sample_end":3179200,"min":0,"max":0,"value":0},{"frame":1987,"sample_start":3179200,"sample_end":3180800,"min":0,"max":0,"value":0},{"frame":1988,"sample_start":3180800,"sample_end":3182400,"min":0,"max":0,"value":0},{"frame":1989,"sample_start":3182400,"sample_end":3184000,"min":0,"max":0,"value":0},{"frame":1990,"sample_start":3184000,"sample_end":3185600,"min":0,"max":0,"value":0},{"frame":1991,"sample_start":3185600,"sample_end":3187200,"min":0,"max":0,"value":0},{"frame":1992,"sample_start":3187200,"sample_end":3188800,"min":0,"max":0,"value":0},{"frame":1993,"sample_start":3188800,"sample_end":3190400,"min":0,"max":0,"value":0},{"frame":1994,"sample_start":3190400,"sample_end":3192000,"min":0,"max":0,"value":0},{"frame":1995,"sample_start":3192000,"sample_end":3193600,"min":0,"max":0,"value":0},{"frame":1996,"sample_start":3193600,"sample_end":3195200,"min":0,"max":0,"value":0},{"frame":1997,"sample_start":3195200,"sample_end":3196800,"min":0,"max":0,"value":0},{"frame":1998,"sample_start":3196800,"sample_end":3198400,"min":0,"max":0,"value":0},{"frame":1999,"sample_start":3198400,"sample_end":3200000,"min":0,"max":0,"value":0},{"frame":2000,"sample_start":3200000,"sample_end":3201600,"min":0,"max":0,"value":0},{"frame":2001,"sample_start":3201600,"sample_end":3203200,"min":0,"max":0,"value":0},{"frame":2002,"sample_start":3203200,"sample_end":3204800,"min":0,"max":0,"value":0},{"frame":2003,"sample_start":3204800,"sample_end":3206400,"min":0,"max":0,"value":0},{"frame":2004,"sample_start":3206400,"sample_end":3208000,"min":0,"max":0,"value":0},{"frame":2005,"sample_start":3208000,"sample_end":3209600,"min":0,"max":0,"value":0},{"frame":2006,"sample_start":3209600,"sample_end":3211200,"min":0,"max":0,"value":0},{"frame":2007,"sample_start":3211200,"sample_end":3212800,"min":0,"max":0,"value":0},{"frame":2008,"sample_start":3212800,"sample_end":3214400,"min":0,"max":0,"value":0},{"frame":2009,"sample_start":3214400,"sample_end":3216000,"min":0,"max":0,"value":0},{"frame":2010,"sample_start":3216000,"sample_end":3217600,"min":-0.0408935546875,"max":0.029266357421875,"value":0.0408935546875},{"frame":2011,"sample_start":3217600,"sample_end":3219200,"min":-0.039886474609375,"max":0.02728271484375,"value":0.039886474609375},{"frame":2012,"sample_start":3219200,"sample_end":3220800,"min":-0.023284912109375,"max":0.01458740234375,"value":0.023284912109375},{"frame":2013,"sample_start":3220800,"sample_end":3222400,"min":-0.0146484375,"max":0.008148193359375,"value":0.0146484375},{"frame":2014,"sample_start":3222400,"sample_end":3224000,"min":-0.009857177734375,"max":0.0047607421875,"value":0.009857177734375},{"frame":2015,"sample_start":3224000,"sample_end":3225600,"min":-0.00653076171875,"max":0.002716064453125,"value":0.00653076171875},{"frame":2016,"sample_start":3225600,"sample_end":3227200,"min":-0.00445556640625,"max":0.001556396484375,"value":0.00445556640625},{"frame":2017,"sample_start":3227200,"sample_end":3228800,"min":-0.0030517578125,"max":0.001068115234375,"value":0.0030517578125},{"frame":2018,"sample_start":3228800,"sample_end":3230400,"min":-0.002105712890625,"max":0.000732421875,"value":0.002105712890625},{"frame":2019,"sample_start":3230400,"sample_end":3232000,"min":-0.00152587890625,"max":0.00054931640625,"value":0.00152587890625},{"frame":2020,"sample_start":3232000,"sample_end":3233600,"min":-0.00103759765625,"max":0.0003662109375,"value":0.00103759765625},{"frame":2021,"sample_start":3233600,"sample_end":3235200,"min":-0.000732421875,"max":0.000274658203125,"value":0.000732421875},{"frame":2022,"sample_start":3235200,"sample_end":3236800,"min":-0.00054931640625,"max":0.000213623046875,"value":0.00054931640625},{"frame":2023,"sample_start":3236800,"sample_end":3238400,"min":-0.000396728515625,"max":0.0001220703125,"value":0.000396728515625},{"frame":2024,"sample_start":3238400,"sample_end":3240000,"min":-0.00030517578125,"max":0.000091552734375,"value":0.00030517578125},{"frame":2025,"sample_start":3240000,"sample_end":3241600,"min":-0.000244140625,"max":0.00006103515625,"value":0.000244140625},{"frame":2026,"sample_start":3241600,"sample_end":3243200,"min":-0.00018310546875,"max":0.00006103515625,"value":0.00018310546875},{"frame":2027,"sample_start":3243200,"sample_end":3244800,"min":-0.0001220703125,"max":0.000030517578125,"value":0.0001220703125},{"frame":2028,"sample_start":3244800,"sample_end":3246400,"min":-0.000091552734375,"max":0.000030517578125,"value":0.000091552734375},{"frame":2029,"sample_start":3246400,"sample_end":3248000,"min":-0.00006103515625,"max":0,"value":0.00006103515625},{"frame":2030,"sample_start":3248000,"sample_end":3249600,"min":-0.00006103515625,"max":0,"value":0.00006103515625},{"frame":2031,"sample_start":3249600,"sample_end":3251200,"min":-0.000030517578125,"max":0,"value":0.000030517578125},{"frame":2032,"sample_start":3251200,"sample_end":3252800,"min":-0.000030517578125,"max":0,"value":0.000030517578125},{"frame":2033,"sample_start":3252800,"sample_end":3254400,"min":-0.000030517578125,"max":0,"value":0.000030517578125},{"frame":2034,"sample_start":3254400,"sample_end":3256000,"min":-0.000030517578125,"max":0,"value":0.000030517578125},{"frame":2035,"sample_start":3256000,"sample_end":3257600,"min":-0.000030517578125,"max":0,"value":0.000030517578125},{"frame":2036,"sample_start":3257600,"sample_end":3259200,"min":-0.000030517578125,"max":0,"value":0.000030517578125},{"frame":2037,"sample_start":3259200,"sample_end":3260800,"min":0,"max":0,"value":0},{"frame":2038,"sample_start":3260800,"sample_end":3262400,"min":0,"max":0,"value":0},{"frame":2039,"sample_start":3262400,"sample_end":3264000,"min":0,"max":0,"value":0},{"frame":2040,"sample_start":3264000,"sample_end":3265600,"min":0,"max":0,"value":0},{"frame":2041,"sample_start":3265600,"sample_end":3267200,"min":0,"max":0,"value":0},{"frame":2042,"sample_start":3267200,"sample_end":3268800,"min":0,"max":0,"value":0},{"frame":2043,"sample_start":3268800,"sample_end":3270400,"min":0,"max":0,"value":0},{"frame":2044,"sample_start":3270400,"sample_end":3272000,"min":0,"max":0,"value":0},{"frame":2045,"sample_start":3272000,"sample_end":3273600,"min":0,"max":0,"value":0},{"frame":2046,"sample_start":3273600,"sample_end":3275200,"min":0,"max":0,"value":0},{"frame":2047,"sample_start":3275200,"sample_end":3276800,"min":0,"max":0,"value":0},{"frame":2048,"sample_start":3276800,"sample_end":3278400,"min":0,"max":0,"value":0},{"frame":2049,"sample_start":3278400,"sample_end":3280000,"min":0,"max":0,"value":0},{"frame":2050,"sample_start":3280000,"sample_end":3281600,"min":0,"max":0,"value":0},{"frame":2051,"sample_start":3281600,"sample_end":3283200,"min":0,"max":0,"value":0},{"frame":2052,"sample_start":3283200,"sample_end":3284800,"min":0,"max":0,"value":0},{"frame":2053,"sample_start":3284800,"sample_end":3286400,"min":0,"max":0,"value":0},{"frame":2054,"sample_start":3286400,"sample_end":3288000,"min":0,"max":0,"value":0},{"frame":2055,"sample_start":3288000,"sample_end":3289600,"min":0,"max":0,"value":0},{"frame":2056,"sample_start":3289600,"sample_end":3291200,"min":0,"max":0,"value":0},{"frame":2057,"sample_start":3291200,"sample_end":3292800,"min":0,"max":0,"value":0},{"frame":2058,"sample_start":3292800,"sample_end":3294400,"min":0,"max":0,"value":0},{"frame":2059,"sample_start":3294400,"sample_end":3296000,"min":0,"max":0,"value":0},{"frame":2060,"sample_start":3296000,"sample_end":3297600,"min":0,"max":0,"value":0},{"frame":2061,"sample_start":3297600,"sample_end":3299200,"min":0,"max":0,"value":0},{"frame":2062,"sample_start":3299200,"sample_end":3300800,"min":0,"max":0,"value":0},{"frame":2063,"sample_start":3300800,"sample_end":3302400,"min":0,"max":0,"value":0},{"frame":2064,"sample_start":3302400,"sample_end":3304000,"min":0,"max":0,"value":0},{"frame":2065,"sample_start":3304000,"sample_end":3305600,"min":0,"max":0,"value":0},{"frame":2066,"sample_start":3305600,"sample_end":3307200,"min":0,"max":0,"value":0},{"frame":2067,"sample_start":3307200,"sample_end":3308800,"min":0,"max":0,"value":0},{"frame":2068,"sample_start":3308800,"sample_end":3310400,"min":0,"max":0,"value":0},{"frame":2069,"sample_start":3310400,"sample_end":3312000,"min":0,"max":0,"value":0},{"frame":2070,"sample_start":3312000,"sample_end":3313600,"min":0,"max":0,"value":0},{"frame":2071,"sample_start":3313600,"sample_end":3315200,"min":0,"max":0,"value":0},{"frame":2072,"sample_start":3315200,"sample_end":3316800,"min":0,"max":0,"value":0},{"frame":2073,"sample_start":3316800,"sample_end":3318400,"min":0,"max":0,"value":0},{"frame":2074,"sample_start":3318400,"sample_end":3320000,"min":0,"max":0,"value":0},{"frame":2075,"sample_start":3320000,"sample_end":3321600,"min":0,"max":0,"value":0},{"frame":2076,"sample_start":3321600,"sample_end":3323200,"min":0,"max":0,"value":0},{"frame":2077,"sample_start":3323200,"sample_end":3324800,"min":0,"max":0,"value":0},{"frame":2078,"sample_start":3324800,"sample_end":3326400,"min":0,"max":0,"value":0},{"frame":2079,"sample_start":3326400,"sample_end":3328000,"min":0,"max":0,"value":0},{"frame":2080,"sample_start":3328000,"sample_end":3329600,"min":-0.000030517578125,"max":0,"value":0.000030517578125},{"frame":2081,"sample_start":3329600,"sample_end":3331200,"min":-0.000030517578125,"max":0,"value":0.000030517578125},{"frame":2082,"sample_start":3331200,"sample_end":3332800,"min":-0.00006103515625,"max":0.000030517578125,"value":0.00006103515625},{"frame":2083,"sample_start":3332800,"sample_end":3334400,"min":-0.000091552734375,"max":0.00006103515625,"value":0.000091552734375},{"frame":2084,"sample_start":3334400,"sample_end":3336000,"min":-0.000091552734375,"max":0.00006103515625,"value":0.000091552734375},{"frame":2085,"sample_start":3336000,"sample_end":3337600,"min":0,"max":0,"value":0},{"frame":2086,"sample_start":3337600,"sample_end":3339200,"min":0,"max":0,"value":0},{"frame":2087,"sample_start":3339200,"sample_end":3340800,"min":0,"max":0,"value":0},{"frame":2088,"sample_start":3340800,"sample_end":3342400,"min":0,"max":0,"value":0},{"frame":2089,"sample_start":3342400,"sample_end":3344000,"min":0,"max":0,"value":0},{"frame":2090,"sample_start":3344000,"sample_end":3345600,"min":0,"max":0,"value":0},{"frame":2091,"sample_start":3345600,"sample_end":3347200,"min":0,"max":0,"value":0},{"frame":2092,"sample_start":3347200,"sample_end":3348800,"min":0,"max":0,"value":0},{"frame":2093,"sample_start":3348800,"sample_end":3350400,"min":0,"max":0,"value":0},{"frame":2094,"sample_start":3350400,"sample_end":3352000,"min":0,"max":0,"value":0},{"frame":2095,"sample_start":3352000,"sample_end":3353600,"min":0,"max":0,"value":0},{"frame":2096,"sample_start":3353600,"sample_end":3355200,"min":0,"max":0,"value":0},{"frame":2097,"sample_start":3355200,"sample_end":3356800,"min":0,"max":0,"value":0},{"frame":2098,"sample_start":3356800,"sample_end":3358400,"min":0,"max":0,"value":0},{"frame":2099,"sample_start":3358400,"sample_end":3360000,"min":0,"max":0,"value":0},{"frame":2100,"sample_start":3360000,"sample_end":3361600,"min":-0.37908935546875,"max":0.29669189453125,"value":0.37908935546875},{"frame":2101,"sample_start":3361600,"sample_end":3363200,"min":-0.436279296875,"max":0.33416748046875,"value":0.436279296875},{"frame":2102,"sample_start":3363200,"sample_end":3364800,"min":-0.376983642578125,"max":0.30072021484375,"value":0.376983642578125},{"frame":2103,"sample_start":3364800,"sample_end":3366400,"min":-0.294769287109375,"max":0.241180419921875,"value":0.294769287109375},{"frame":2104,"sample_start":3366400,"sample_end":3368000,"min":-0.234588623046875,"max":0.192352294921875,"value":0.234588623046875},{"frame":2105,"sample_start":3368000,"sample_end":3369600,"min":-0.18695068359375,"max":0.152313232421875,"value":0.18695068359375},{"frame":2106,"sample_start":3369600,"sample_end":3371200,"min":-0.15289306640625,"max":0.12841796875,"value":0.15289306640625},{"frame":2107,"sample_start":3371200,"sample_end":3372800,"min":-0.12982177734375,"max":0.109100341796875,"value":0.12982177734375},{"frame":2108,"sample_start":3372800,"sample_end":3374400,"min":-0.10638427734375,"max":0.0927734375,"value":0.10638427734375},{"frame":2109,"sample_start":3374400,"sample_end":3376000,"min":-0.088134765625,"max":0.0789794921875,"value":0.088134765625},{"frame":2110,"sample_start":3376000,"sample_end":3377600,"min":-0.07281494140625,"max":0.067169189453125,"value":0.07281494140625},{"frame":2111,"sample_start":3377600,"sample_end":3379200,"min":-0.060821533203125,"max":0.058990478515625,"value":0.060821533203125},{"frame":2112,"sample_start":3379200,"sample_end":3380800,"min":-0.0506591796875,"max":0.050048828125,"value":0.0506591796875},{"frame":2113,"sample_start":3380800,"sample_end":3382400,"min":-0.04248046875,"max":0.042510986328125,"value":0.042510986328125},{"frame":2114,"sample_start":3382400,"sample_end":3384000,"min":-0.035614013671875,"max":0.0360107421875,"value":0.0360107421875},{"frame":2115,"sample_start":3384000,"sample_end":3385600,"min":-0.515228271484375,"max":0.490753173828125,"value":0.515228271484375},{"frame":2116,"sample_start":3385600,"sample_end":3387200,"min":-0.277008056640625,"max":0.255279541015625,"value":0.277008056640625},{"frame":2117,"sample_start":3387200,"sample_end":3388800,"min":-0.132293701171875,"max":0.1109619140625,"value":0.132293701171875},{"frame":2118,"sample_start":3388800,"sample_end":3390400,"min":-0.06353759765625,"max":0.052154541015625,"value":0.06353759765625},{"frame":2119,"sample_start":3390400,"sample_end":3392000,"min":-0.034332275390625,"max":0.02703857421875,"value":0.034332275390625},{"frame":2120,"sample_start":3392000,"sample_end":3393600,"min":-0.0130615234375,"max":0.013702392578125,"value":0.013702392578125},{"frame":2121,"sample_start":3393600,"sample_end":3395200,"min":-0.01104736328125,"max":0.011566162109375,"value":0.011566162109375},{"frame":2122,"sample_start":3395200,"sample_end":3396800,"min":-0.009307861328125,"max":0.009735107421875,"value":0.009735107421875},{"frame":2123,"sample_start":3396800,"sample_end":3398400,"min":-0.0078125,"max":0.008270263671875,"value":0.008270263671875},{"frame":2124,"sample_start":3398400,"sample_end":3400000,"min":-0.006591796875,"max":0.0069580078125,"value":0.0069580078125},{"frame":2125,"sample_start":3400000,"sample_end":3401600,"min":-0.005767822265625,"max":0.005889892578125,"value":0.005889892578125},{"frame":2126,"sample_start":3401600,"sample_end":3403200,"min":-0.0048828125,"max":0.00494384765625,"value":0.00494384765625},{"frame":2127,"sample_start":3403200,"sample_end":3404800,"min":-0.00408935546875,"max":0.004150390625,"value":0.004150390625},{"frame":2128,"sample_start":3404800,"sample_end":3406400,"min":-0.00347900390625,"max":0.0035400390625,"value":0.0035400390625},{"frame":2129,"sample_start":3406400,"sample_end":3408000,"min":-0.002899169921875,"max":0.0030517578125,"value":0.0030517578125},{"frame":2130,"sample_start":3408000,"sample_end":3409600,"min":-0.5989990234375,"max":0.5989990234375,"value":0.5989990234375},{"frame":2131,"sample_start":3409600,"sample_end":3411200,"min":-0.579833984375,"max":0.492889404296875,"value":0.579833984375},{"frame":2132,"sample_start":3411200,"sample_end":3412800,"min":-0.57550048828125,"max":0.426849365234375,"value":0.57550048828125},{"frame":2133,"sample_start":3412800,"sample_end":3414400,"min":-0.463287353515625,"max":0.34808349609375,"value":0.463287353515625},{"frame":2134,"sample_start":3414400,"sample_end":3416000,"min":-0.391876220703125,"max":0.269439697265625,"value":0.391876220703125},{"frame":2135,"sample_start":3416000,"sample_end":3417600,"min":-0.303253173828125,"max":0.21826171875,"value":0.303253173828125},{"frame":2136,"sample_start":3417600,"sample_end":3419200,"min":-0.25634765625,"max":0.178009033203125,"value":0.25634765625},{"frame":2137,"sample_start":3419200,"sample_end":3420800,"min":-0.20538330078125,"max":0.15045166015625,"value":0.20538330078125},{"frame":2138,"sample_start":3420800,"sample_end":3422400,"min":-0.170654296875,"max":0.122528076171875,"value":0.170654296875},{"frame":2139,"sample_start":3422400,"sample_end":3424000,"min":-0.1435546875,"max":0.103057861328125,"value":0.1435546875},{"frame":2140,"sample_start":3424000,"sample_end":3425600,"min":-0.113494873046875,"max":0.087158203125,"value":0.113494873046875},{"frame":2141,"sample_start":3425600,"sample_end":3427200,"min":-0.094024658203125,"max":0.070465087890625,"value":0.094024658203125},{"frame":2142,"sample_start":3427200,"sample_end":3428800,"min":-0.077484130859375,"max":0.058807373046875,"value":0.077484130859375},{"frame":2143,"sample_start":3428800,"sample_end":3430400,"min":-0.062255859375,"max":0.0489501953125,"value":0.062255859375},{"frame":2144,"sample_start":3430400,"sample_end":3432000,"min":-0.05133056640625,"max":0.039703369140625,"value":0.05133056640625},{"frame":2145,"sample_start":3432000,"sample_end":3433600,"min":-0.042266845703125,"max":0.032989501953125,"value":0.042266845703125},{"frame":2146,"sample_start":3433600,"sample_end":3435200,"min":-0.033843994140625,"max":0.02740478515625,"value":0.033843994140625},{"frame":2147,"sample_start":3435200,"sample_end":3436800,"min":-0.027984619140625,"max":0.022125244140625,"value":0.027984619140625},{"frame":2148,"sample_start":3436800,"sample_end":3438400,"min":-0.023040771484375,"max":0.01837158203125,"value":0.023040771484375},{"frame":2149,"sample_start":3438400,"sample_end":3440000,"min":-0.018524169921875,"max":0.015289306640625,"value":0.018524169921875},{"frame":2150,"sample_start":3440000,"sample_end":3441600,"min":-0.01519775390625,"max":0.0123291015625,"value":0.01519775390625},{"frame":2151,"sample_start":3441600,"sample_end":3443200,"min":-0.01251220703125,"max":0.01019287109375,"value":0.01251220703125},{"frame":2152,"sample_start":3443200,"sample_end":3444800,"min":-0.010040283203125,"max":0.008453369140625,"value":0.010040283203125},{"frame":2153,"sample_start":3444800,"sample_end":3446400,"min":-0.008331298828125,"max":0.0068359375,"value":0.008331298828125},{"frame":2154,"sample_start":3446400,"sample_end":3448000,"min":-0.006866455078125,"max":0.005645751953125,"value":0.006866455078125},{"frame":2155,"sample_start":3448000,"sample_end":3449600,"min":-0.005523681640625,"max":0.00469970703125,"value":0.005523681640625},{"frame":2156,"sample_start":3449600,"sample_end":3451200,"min":-0.00457763671875,"max":0.003753662109375,"value":0.00457763671875},{"frame":2157,"sample_start":3451200,"sample_end":3452800,"min":-0.003692626953125,"max":0.003143310546875,"value":0.003692626953125},{"frame":2158,"sample_start":3452800,"sample_end":3454400,"min":-0.003021240234375,"max":0.0025634765625,"value":0.003021240234375},{"frame":2159,"sample_start":3454400,"sample_end":3456000,"min":-0.00250244140625,"max":0.00213623046875,"value":0.00250244140625}]},"audio_approved":false};
},
"assets/motion-contract.json": function(module,exports,require){
module.exports = {"status":"AUTHOR_TIMING_AND_TARGET_CURVES; no final motion/pixels claimed","ease_functions":{"linear":"u","smoothstep":"u*u*(3-2*u)","smootherstep":"u*u*u*(u*(6*u-15)+10)"},"curve_time":"For key-to-key segment [a,b], u=clamp((global_f-a)/(b-a),0,1). Actual scene scheduling stays half-open. Evaluate exact endpoint at next segment start.","opening":"Use unchanged embedded opening key interpolation and projection.","S2_touch":{"contact_frame":365,"card_flip_start":366,"hinge":"same left card edge; never stretch A glyph","target":{"bird_translation":[765,465],"scale":1.5,"local_wing_tip":[-91,-100],"world_contact":[628.5,315]}},"S4_flight":{"kind":"piecewise cubic Bezier for B root in author-space; one shared camera projection","segments":[{"from_frame":1320,"to_frame":1434,"points":[[400,489],[475,440],[650,344],[756,344]]},{"from_frame":1434,"to_frame":1515,"points":[[756,344],[831.3157894736842,344],[965,390],[1005,447]]}],"apex_frame":1434,"tangent_note":"Vertical derivative0 at shared apex; horizontal derivatives match when divided by segment durations114 and81.","pose_targets":"Use shared bird pose target keys for rotation/scale/wing. Feet at1515 must be solved against actual platform top; do not hide a gap with body motion.","settle_keys":[{"frame":1515,"root":[1005,447]},{"frame":1530,"root":[1013,449]},{"frame":1545,"root":[1005,447]},{"frame":1560,"root":[1005,447]}],"settle_rule":"One finite forward overshoot; contact foot is constrained to support while body/head settle."},"S5_connection":{"f1950":{"F_lower_endpoint":[788.52223994,447.34133082],"shared_buckle_P_A_anchor":[792,463]},"f2159_2160":{"F_lower_endpoint":[879.17140594,378.10166427],"shared_buckle_P_A_anchor":[867,360]},"connection":"Continuous 5-unit red paper tongue joins the exact black buckle/P-A anchor; beak-to-F contact retained."},"S6_carrier":{"coordinate_space":"W-guide-fixed local workshop coordinates before common camera","slide_progress_keys":[[2160,0],[2250,0],[2280,0.12],[2400,1],[2700,1]],"gate_mount":"All A/R/L/G/receiver local coordinates under same P-C root; pure translation only2250–2400. World displacement must be solved with shared hinge rig, not inferred from independent static screen polygons.","fold_phases":[{"object":"P-A","start":2130,"end":2250},{"object":"P-B","start":2520,"end":2610}],"support_visibility":{"before_foreground_occlusion":[2280,2400],"occluded_target_frame":2450,"after_foreground_occlusion":[2610,2700],"requirement":"Show both feet seated on their original P-C receivers before and after the temporary foreground fold occlusion; do not treat occlusion as removal or new assembly."}},"S7_separation":{"progress_keys":[[2759,0],[2760,0],[2820,0.72],[2910,1],[3090,1],[3180,0]],"ease":"smoothstep","group_offsets_at_one":{"T02":[-70,-54],"T03":[-54,-2],"T04":[18,25],"T06":[18,25],"T09_operator":[-54,-2],"T05":[30,-7],"B":[100,-37]},"withdrawal_rule":"Separate selected owner after base offsets; zero-render entire selected contribution for exact absent interval. Nonselected state sampled from frozen2940 baseline until3090."},"ending":{"final_frame":3599,"F_rect":[140,181,1000,397],"B":[1053,143,0.8,0,6],"mini_world_matrix":[0.39,0,0,0.39,400.4,336.25],"mini_excludes":["B","F"],"wordmark_origin":[280,393],"wordmark_size":108,"wordmark_weight":600,"wordmark_reveal":[3360,3420],"fully_settled_frame":3480},"runtime_rule":"Scene authors may solve bounded geometry against these contacts and poses, but shared cue/frame/window changes must be integrated once and independently reviewed."};
},
"assets/opening-cues.json": function(module,exports,require){
module.exports = {"fps":30,"duration_frames":300,"viewBox":[0,0,1280,720],"contacts":{"cut":18,"body":67,"trim":109,"eye":150},"windows_inclusive":{"waste":[18,54],"waste_separation":[28,54],"head_offcuts":[109,138],"head_offcut_fall":[112,139],"recognition_hold":[163,189],"lift":[190,218],"world_reveal":[190,239],"approach":[264,299],"wave":[219,245]},"keys":{"hero_x":[[0,280],[12,350],[28,350],[56,650],[77,650],[100,950],[119,950],[141,1250],[189,1250],[239,1490],[260,1510],[299,1510]],"camera_zoom":[[0,2.35],[28,2.05],[70,1.8],[138,1.58],[184,1.5],[238,0.65],[299,0.65]],"camera_x_reveal":[[184,1270],[238,1010],[299,1010]],"camera_y":[[0,401],[138,390],[184,390],[238,326],[299,326]],"bird_y":[[0,440],[189,440],[203,427],[218,335],[239,210],[263,191]],"bird_angle":[[0,0],[192,0],[215,-9],[239,-4],[263,0]],"wing_angle":[[194,0],[205,-22],[216,11],[231,-13],[245,4],[263,0]]},"presses":{"cut":[0,18,32,230],"body":[57,67,79,159],"trim":[99,109,121,1],"eye":[141,150,162,132]},"first_cut_material":{"stock_bounds":[240,336,406,526],"contact_cover":[236,332,410,530],"stock_area":31540,"wing_area":6065,"waste_area":25475},"pose_frames":[0,37,110,176,239,279]};
},
"assets/provider-review.json": function(module,exports,require){
module.exports = {"status":"EXACT_PROVIDER_STATIC_OUTLINES_INDEPENDENTLY_REVIEWED","receipt_path":"production/reviews/font-outlines.json","receipt_sha256":"87ace0539db488dd48022b0139a01dd0a2b53e65c2120f210d8a46a5d92dc6be","scope":"Exact static glyph-outline providers and bounded consumer functions only","providers":{"sourceLabels":{"status":"PASS","provider_path":"src/assets/fonts/source-label-glyphs.json","provider_sha256":"2acef4fe4abb53af646c4bfb02a9bd35855cbec460fc537d4c6e218dd18ab7a0","license_path":"notices/Noto-Mono-OFL-copyright.txt","license_sha256":"075f91382875da1c4b491f1facf2586f55448705133264d98ce15cab6211b125","report_sha256":"89e74ae4c5b43d296f0a4a7f9e2ab5c192e10b3f4d5d3e88dbbe2096c668f70d"},"brand":{"status":"PASS","provider_path":"src/assets/fonts/brand-glyphs.json","provider_sha256":"52decbe0b6aacd419910781b034dc0232f8cdc8d15ae3404948035830a0a68a9","license_path":"notices/Noto-SC-OFL.txt","license_sha256":"1c05c68c34f9708415aada51f17e1b0092d2cea709bf4a94cd38114f9e73d7d9","report_sha256":"89e74ae4c5b43d296f0a4a7f9e2ab5c192e10b3f4d5d3e88dbbe2096c668f70d"}}};
},
"assets/source-bindings.json": function(module,exports,require){
module.exports = {"status":"RECIPE_BINDINGS; resolve from final actual timeline and compare at R06/R17","resolve_rule":"Find video track by exact name and clip by src. Require unique match; copy fields verbatim. Short visible label maps to real source path; do not invent runtime stats.","items":[{"id":"S3.strip","track_id":"T11","track_name":"temporal-ribbons","clip_src":"gfx/T11_0630_1200.html","window":[810,1050],"fields":["src"],"short_display_label":"A-arch","expected_projection":{"src":"gfx/T11_0630_1200.html"},"object_id":"A-arch","visible_text_rule":"Use basename for src, exact integer for dur/dst_in. Label identifies group when group-owned; never say A is a standalone track."},{"id":"S3.gate","track_id":"T03","track_name":"middle-gate-carrier","clip_src":"gfx/T03_0690_3600.html","window":[1050,1140],"fields":["src","dst_in","dur"],"short_display_label":"T03","expected_projection":{"src":"gfx/T03_0690_3600.html","dst_in":690,"dur":2910},"object_id":"A-arch","visible_text_rule":"Use basename for src, exact integer for dur/dst_in. Label identifies group when group-owned; never say A is a standalone track."},{"id":"S7.middle","track_id":"T03","track_name":"middle-gate-carrier","clip_src":"gfx/T03_0690_3600.html","window":[2820,3150],"fields":["src","dur"],"short_display_label":"T03","expected_projection":{"src":"gfx/T03_0690_3600.html","dur":2910},"object_id":"A-arch","visible_text_rule":"Use basename for src, exact integer for dur/dst_in. Label identifies group when group-owned; never say A is a standalone track."},{"id":"S7.frame","track_id":"T05","track_name":"continuous-red-frame","clip_src":"gfx/T05_0330_3600.html","window":[2820,3150],"fields":["src"],"short_display_label":"T05","expected_projection":{"src":"gfx/T05_0330_3600.html"},"object_id":"F-frame","visible_text_rule":"Use basename for src, exact integer for dur/dst_in. Label identifies group when group-owned; never say A is a standalone track."}]};
},
"assets/timeline-blueprint.json": function(module,exports,require){
module.exports = {"version":2,"project":{"fps":30,"width":1920,"height":1080,"duration_frames":3600,"cache_dependencies":["runtime/shared.js","runtime/effects.json","production/cues.json","production/source-bindings.json","design/geometry.json","design/shapes.svg"]},"sources":[{"id":"paper","path":"media/paper-background.mp4"},{"id":"T17","path":"audio/T17.wav"},{"id":"T18","path":"audio/T18.wav"},{"id":"T19","path":"audio/T19.wav"},{"id":"T20","path":"audio/T20.wav"}],"video_tracks":[{"name":"visible-paper-background","kind":"broll","clips":[{"src":"paper","src_in":0,"src_out":300,"dst_in":0,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":300,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":600,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":900,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":1200,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":1500,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":1800,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":2100,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":2400,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":2700,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":3000,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":3300,"dur":300}]},{"name":"far-workshop","kind":"gfx","clips":[{"src":"gfx/T02_0000_0690.html","dst_in":0,"dur":690},{"src":"gfx/T02_1140_3600.html","dst_in":1140,"dur":2460}]},{"name":"middle-gate-carrier","kind":"gfx","clips":[{"src":"gfx/T03_0690_3600.html","dst_in":690,"dur":2910}]},{"name":"transport-reel-guide","kind":"gfx","clips":[{"src":"gfx/T04_0000_3600.html","dst_in":0,"dur":3600}]},{"name":"stock-offcuts-fold-planes","kind":"gfx","clips":[{"src":"gfx/T06_0000_0139.html","dst_in":0,"dur":139},{"src":"gfx/T06_0690_3600.html","dst_in":690,"dur":2910}]},{"name":"continuous-red-frame","kind":"gfx","clips":[{"src":"gfx/T05_0330_3600.html","dst_in":330,"dur":3270}]},{"name":"bird-wings","kind":"gfx","clips":[{"src":"gfx/T08_0018_3600.html","dst_in":18,"dur":3582}]},{"name":"bird-body-head","kind":"gfx","clips":[{"src":"gfx/T07_0000_3600.html","dst_in":0,"dur":3600}]},{"name":"tools-and-operator","kind":"gfx","clips":[{"src":"gfx/T09_0000_0630.html","dst_in":0,"dur":630},{"src":"gfx/T09_2280_3600.html","dst_in":2280,"dur":1320}]},{"name":"ingredient-cards","kind":"gfx","clips":[{"src":"gfx/T10_0330_0630.html","dst_in":330,"dur":300}]},{"name":"temporal-ribbons","kind":"gfx","clips":[{"src":"gfx/T11_0630_1200.html","dst_in":630,"dur":570}]},{"name":"real-pcm-waveform","kind":"gfx","clips":[{"src":"gfx/T13_1710_2160.html","dst_in":1710,"dur":450,"cache_dependencies":["audio/waveform-S5.json"]}]},{"name":"source-leaders","kind":"gfx","clips":[{"src":"gfx/T12_0960_1140.html","dst_in":960,"dur":180},{"src":"gfx/T12_2820_3180.html","dst_in":2820,"dur":360}]},{"name":"actual-source-fields","kind":"gfx","clips":[{"src":"gfx/T14_0810_1140.html","dst_in":810,"dur":330},{"src":"gfx/T14_2820_3150.html","dst_in":2820,"dur":330}]},{"name":"brand-wordmark","kind":"gfx","clips":[{"src":"gfx/T15_3360_3600.html","dst_in":3360,"dur":240}]}],"subtitles":[{"in":195,"out":255,"text":"从一张纸，做出一个画面。"},{"in":420,"out":600,"text":"把散开的材料，放到一起。"},{"in":900,"out":1080,"text":"把每一层，放进时间。"},{"in":1410,"out":1590,"text":"动作，发生在该发生的那一帧。"},{"in":1830,"out":2010,"text":"让声音，接住每次变化。"},{"in":2460,"out":2640,"text":"让不同的层，组成同一个世界。"},{"in":2940,"out":3120,"text":"这幅画面，来自你能修改的工程。"},{"in":3330,"out":3450,"text":"让画面，各就各位。"}],"audio_tracks":[{"name":"harmony-melody","events":[{"src":"T17","src_in":0,"in":0,"out":3600,"volume":1,"loop":false}]},{"name":"rhythmic-motion","events":[{"src":"T18","src_in":0,"in":0,"out":3600,"volume":1,"loop":false}]},{"name":"tactile-contact","events":[{"src":"T19","src_in":0,"in":0,"out":3600,"volume":1,"loop":false}]},{"name":"character-signature","events":[{"src":"T20","src_in":0,"in":0,"out":3600,"volume":1,"loop":false}]}]};
},
"assets/timeline.json": function(module,exports,require){
module.exports = {"version":2,"project":{"fps":30,"width":1920,"height":1080,"duration_frames":3600,"cache_dependencies":["runtime/shared.js","runtime/effects.json","production/cues.json","production/source-bindings.json","design/geometry.json","design/shapes.svg","production/caption-font.json","src/assets/provider-review.json","production/reviews/font-outlines.json","src/assets/fonts/source-label-glyphs.json","notices/Noto-Mono-OFL-copyright.txt","notices/Noto-CJK-copyright.txt","audio/stems.json","audio/native-postmix.json","audio/waveform-S5.json","audio/native-audio-provenance.json","src/assets/fonts/brand-glyphs.json","notices/Noto-SC-OFL.txt"]},"sources":[{"id":"paper","path":"media/paper-background.mp4"},{"id":"T17","path":"audio/T17.flac"},{"id":"T18","path":"audio/T18.flac"},{"id":"T19","path":"audio/T19.flac"},{"id":"T20","path":"audio/T20.flac"}],"video_tracks":[{"name":"visible-paper-background","kind":"broll","clips":[{"src":"paper","src_in":0,"src_out":300,"dst_in":0,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":300,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":600,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":900,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":1200,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":1500,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":1800,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":2100,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":2400,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":2700,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":3000,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":3300,"dur":300}]},{"name":"far-workshop","kind":"gfx","clips":[{"src":"gfx/T02_0000_0690.html","dst_in":0,"dur":690},{"src":"gfx/T02_1140_3600.html","dst_in":1140,"dur":2460}]},{"name":"middle-gate-carrier","kind":"gfx","clips":[{"src":"gfx/T03_0690_3600.html","dst_in":690,"dur":2910}]},{"name":"transport-reel-guide","kind":"gfx","clips":[{"src":"gfx/T04_0000_3600.html","dst_in":0,"dur":3600}]},{"name":"stock-offcuts-fold-planes","kind":"gfx","clips":[{"src":"gfx/T06_0000_0139.html","dst_in":0,"dur":139},{"src":"gfx/T06_0690_3600.html","dst_in":690,"dur":2910}]},{"name":"continuous-red-frame","kind":"gfx","clips":[{"src":"gfx/T05_0330_3600.html","dst_in":330,"dur":3270}]},{"name":"bird-wings","kind":"gfx","clips":[{"src":"gfx/T08_0018_3600.html","dst_in":18,"dur":3582}]},{"name":"bird-body-head","kind":"gfx","clips":[{"src":"gfx/T07_0000_3600.html","dst_in":0,"dur":3600}]},{"name":"tools-and-operator","kind":"gfx","clips":[{"src":"gfx/T09_0000_0630.html","dst_in":0,"dur":630},{"src":"gfx/T09_2280_3600.html","dst_in":2280,"dur":1320}]},{"name":"ingredient-cards","kind":"gfx","clips":[{"src":"gfx/T10_0330_0630.html","dst_in":330,"dur":300}]},{"name":"temporal-ribbons","kind":"gfx","clips":[{"src":"gfx/T11_0630_1200.html","dst_in":630,"dur":570}]},{"name":"real-pcm-waveform","kind":"gfx","clips":[{"src":"gfx/T13_1710_2160.html","dst_in":1710,"dur":450,"cache_dependencies":["audio/waveform-S5.json"]}]},{"name":"source-leaders","kind":"gfx","clips":[{"src":"gfx/T12_0960_1140.html","dst_in":960,"dur":180},{"src":"gfx/T12_2820_3180.html","dst_in":2820,"dur":360}]},{"name":"actual-source-fields","kind":"gfx","clips":[{"src":"gfx/T14_0810_1140.html","dst_in":810,"dur":330},{"src":"gfx/T14_2820_3150.html","dst_in":2820,"dur":330}]},{"name":"brand-wordmark","kind":"gfx","clips":[{"src":"gfx/T15_3360_3600.html","dst_in":3360,"dur":240}]}],"subtitles":[{"in":195,"out":255,"text":"从一张纸，做出一个画面。"},{"in":420,"out":600,"text":"把散开的材料，放到一起。"},{"in":900,"out":1080,"text":"把每一层，放进时间。"},{"in":1410,"out":1590,"text":"动作，发生在该发生的那一帧。"},{"in":1830,"out":2010,"text":"让声音，接住每次变化。"},{"in":2460,"out":2640,"text":"让不同的层，组成同一个世界。"},{"in":2940,"out":3120,"text":"这幅画面，来自你能修改的工程。"},{"in":3330,"out":3450,"text":"让画面，各就各位。"}],"audio_tracks":[{"name":"harmony-melody","events":[{"src":"T17","src_in":0,"in":0,"out":3600,"volume":1,"loop":false}]},{"name":"rhythmic-motion","events":[{"src":"T18","src_in":0,"in":0,"out":3600,"volume":1,"loop":false}]},{"name":"tactile-contact","events":[{"src":"T19","src_in":0,"in":0,"out":3600,"volume":1,"loop":false}]},{"name":"character-signature","events":[{"src":"T20","src_in":0,"in":0,"out":3600,"volume":1,"loop":false}]}]};
},
"assets/track-map.json": function(module,exports,require){
module.exports = {"format":"framivra-film-track-map/1","status":"AUTHOR_DESIGN; no media generated","counts":{"semantic":20,"video_tracks":15,"gfx_tracks":14,"native_subtitle_streams":1,"audio_tracks":4},"bottom_to_top_video_ids":["T01","T02","T03","T04","T06","T05","T08","T07","T09","T10","T11","T13","T12","T14","T15"],"tracks":[{"id":"T01","name":"visible-paper-background","kind":"broll","role":"Paper ground only; no silent-source audio counted.","windows":[{"in":0,"out":3600}],"scheduled_frames":3600,"remove_control":{"window":[0,300],"expected_difference":"Transparent negative space becomes black without the visible paper.","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED","video_array_index":0},{"id":"T02","name":"far-workshop","kind":"gfx","role":"Far beams, stock rack, fixed backdrop. Recalled stationary station silhouettes in S6–S8 use the original geometry. No gate, guide, bird, operator or opaque ground.","windows":[{"in":0,"out":690},{"in":1140,"out":3600}],"scheduled_frames":3150,"remove_control":{"window":[2520,2610],"expected_difference":"The expanded workshop loses its back layer and original station context.","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED","clips":[{"id":"T02.01","src":"gfx/T02_0000_0690.html","dst_in":0,"dur":690,"global_start_frame":0,"local_range":[0,690],"last_local_frame":689,"last_global_frame":689},{"id":"T02.02","src":"gfx/T02_1140_3600.html","dst_in":1140,"dur":2460,"global_start_frame":1140,"local_range":[0,2460],"last_local_frame":2459,"last_global_frame":3599}],"video_array_index":1},{"id":"T03","name":"middle-gate-carrier","kind":"gfx","role":"P-C, two receivers; single A/R/L ownership after their transfers; gate bar/cam/linkage, middle braces, lamp, and their two contact patches. Operator is excluded.","windows":[{"in":690,"out":3600}],"scheduled_frames":2910,"remove_control":{"window":[2910,2970],"expected_difference":"Entire upright gate/carrier/linkage contribution disappears; also verify S7 zero hold [3045,3075).","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED","clips":[{"id":"T03.01","src":"gfx/T03_0690_3600.html","dst_in":690,"dur":2910,"global_start_frame":690,"local_range":[0,2910],"last_local_frame":2909,"last_global_frame":3599}],"video_array_index":2},{"id":"T04","name":"transport-reel-guide","kind":"gfx","role":"Original conveyor and fixed W-guide, origin/landing supports, Q shelf/stop/lever, Q reel and physical feed paper/tongues/buckle. Does not own waveform or P-A/P-B.","windows":[{"in":0,"out":3600}],"scheduled_frames":3600,"remove_control":{"window":[1680,1770],"expected_difference":"The released Q, feed ribbon and physical support vanish, breaking the cause of S5.","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED","clips":[{"id":"T04.01","src":"gfx/T04_0000_3600.html","dst_in":0,"dur":3600,"global_start_frame":0,"local_range":[0,3600],"last_local_frame":3599,"last_global_frame":3599}],"video_array_index":3},{"id":"T05","name":"continuous-red-frame","kind":"gfx","role":"One four-corner F: reserve tray, placement, carried fold, gate hang, wing catch, buckle, unfolding frame and final frame. Never S1 waste.","windows":[{"in":330,"out":3600}],"scheduled_frames":3270,"remove_control":{"window":[2910,2970],"expected_difference":"The composition loses its actual red perimeter; also verify S7 zero hold [2985,3015).","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED","clips":[{"id":"T05.01","src":"gfx/T05_0330_3600.html","dst_in":330,"dur":3270,"global_start_frame":330,"local_range":[0,3270],"last_local_frame":3269,"last_global_frame":3599}],"video_array_index":5},{"id":"T06","name":"stock-offcuts-fold-planes","kind":"gfx","role":"Original red stock before f18, complementary waste [18,55), head offcuts [109,139); later connected P-A/P-B with shared hinge seams. P-C belongs T03.","windows":[{"in":0,"out":139},{"in":690,"out":3600}],"scheduled_frames":3049,"remove_control":{"window":[28,50],"expected_difference":"Cutting loses the complementary waste; second probe [2280,2400) loses visible unfolding paper planes.","status":"REQUIRED_NOT_RUN","additional_windows":[[2280,2400]]},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED","clips":[{"id":"T06.01","src":"gfx/T06_0000_0139.html","dst_in":0,"dur":139,"global_start_frame":0,"local_range":[0,139],"last_local_frame":138,"last_global_frame":138},{"id":"T06.02","src":"gfx/T06_0690_3600.html","dst_in":690,"dur":2910,"global_start_frame":690,"local_range":[0,2910],"last_local_frame":2909,"last_global_frame":3599}],"video_array_index":4},{"id":"T07","name":"bird-body-head","kind":"gfx","role":"Only body/head/beak/eye/tail and optional feet; staged imprint and trim. Initial head/beak exist. Shared B pose/camera/mask with T08.","windows":[{"in":0,"out":3600}],"scheduled_frames":3600,"remove_control":{"window":[163,190],"expected_difference":"The recognizable bird body, raised head, eye and beak are absent.","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED","clips":[{"id":"T07.01","src":"gfx/T07_0000_3600.html","dst_in":0,"dur":3600,"global_start_frame":0,"local_range":[0,3600],"last_local_frame":3599,"last_global_frame":3599}],"video_array_index":7},{"id":"T08","name":"bird-wings","kind":"gfx","role":"Only the two original red wings, shared B pose/occlusion; physical wing cover transition and one bounded historical silhouette.","windows":[{"in":18,"out":3600}],"scheduled_frames":3582,"remove_control":{"window":[264,300],"expected_difference":"The physical wing-borne transition and two raised wings disappear.","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED","clips":[{"id":"T08.01","src":"gfx/T08_0018_3600.html","dst_in":18,"dur":3582,"global_start_frame":18,"local_range":[0,3582],"last_local_frame":3581,"last_global_frame":3599}],"video_array_index":6},{"id":"T09","name":"tools-and-operator","kind":"gfx","role":"Opening active tool heads and one operator; card-chain force and F placement; S6 support hand and same operator through ending. Far recalled idle tools are T02 after the occluded scene relocation.","windows":[{"in":0,"out":630},{"in":2280,"out":3600}],"scheduled_frames":1950,"remove_control":{"window":[355,390],"expected_difference":"The operator at the material table disappears; required causal probe [435,525) removes the pushing hand.","status":"REQUIRED_NOT_RUN","additional_windows":[[435,525]]},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED","clips":[{"id":"T09.01","src":"gfx/T09_0000_0630.html","dst_in":0,"dur":630,"global_start_frame":0,"local_range":[0,630],"last_local_frame":629,"last_global_frame":629},{"id":"T09.02","src":"gfx/T09_2280_3600.html","dst_in":2280,"dur":1320,"global_start_frame":2280,"local_range":[0,1320],"last_local_frame":1319,"last_global_frame":3599}],"video_array_index":8},{"id":"T10","name":"ingredient-cards","kind":"gfx","role":"A/L/R cards, their rigid glyphs and folded trailing reserves until the f630 handoff to T11. No duplicate cards in S7.","windows":[{"in":330,"out":630}],"scheduled_frames":300,"remove_control":{"window":[390,435],"expected_difference":"The stack and L reveal lose the origin of the later gate materials.","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED","clips":[{"id":"T10.01","src":"gfx/T10_0330_0630.html","dst_in":330,"dur":300,"global_start_frame":330,"local_range":[0,300],"last_local_frame":299,"last_global_frame":629}],"video_array_index":9},{"id":"T11","name":"temporal-ribbons","kind":"gfx","role":"Three distinct deforming carriers and single glyphs until A810/R930/L1005 transfer to T03; two weave crossings and persistent reserves. Residual strips settle at P-C before exit.","windows":[{"in":630,"out":1200}],"scheduled_frames":570,"remove_control":{"window":[900,960],"expected_difference":"The visible staggered material/time relation and weave disappear.","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED","clips":[{"id":"T11.01","src":"gfx/T11_0630_1200.html","dst_in":630,"dur":570,"global_start_frame":630,"local_range":[0,570],"last_local_frame":569,"last_global_frame":1199}],"video_array_index":10},{"id":"T12","name":"source-leaders","kind":"gfx","role":"Bound source→object leaders, endpoints and small slip backplates. No text; no fake application controls. S7 slips do not become extra material cards.","windows":[{"in":960,"out":1140},{"in":2820,"out":3180}],"scheduled_frames":540,"remove_control":{"window":[2820,2910],"expected_difference":"Source labels lose the spatial correspondence to actual contributions.","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED","clips":[{"id":"T12.01","src":"gfx/T12_0960_1140.html","dst_in":960,"dur":180,"global_start_frame":960,"local_range":[0,180],"last_local_frame":179,"last_global_frame":1139},{"id":"T12.02","src":"gfx/T12_2820_3180.html","dst_in":2820,"dur":360,"global_start_frame":2820,"local_range":[0,360],"last_local_frame":359,"last_global_frame":3179}],"video_array_index":12},{"id":"T13","name":"real-pcm-waveform","kind":"gfx","role":"Actual PCM-derived [1680,2160) envelope on Q feed; moving clip reveal/current cue tick. No synthetic waveform or extra waveform in S7.","windows":[{"in":1710,"out":2160}],"scheduled_frames":450,"remove_control":{"window":[1770,1890],"expected_difference":"The paper loses its real signal shape. Requires audible approved PCM; missing input is a blocker, never a fake empty completed track.","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED","clips":[{"id":"T13.01","src":"gfx/T13_1710_2160.html","dst_in":1710,"dur":450,"global_start_frame":1710,"local_range":[0,450],"last_local_frame":449,"last_global_frame":2159}],"video_array_index":11},{"id":"T14","name":"actual-source-fields","kind":"gfx","role":"Short real source id on textPath then selected actual timeline fields; two S7 source slips. No scene title and no duplicate caption wording.","windows":[{"in":810,"out":1140},{"in":2820,"out":3150}],"scheduled_frames":660,"remove_control":{"window":[2910,2970],"expected_difference":"Actual source/clip information becomes unreadable while the picture remains.","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED","clips":[{"id":"T14.01","src":"gfx/T14_0810_1140.html","dst_in":810,"dur":330,"global_start_frame":810,"local_range":[0,330],"last_local_frame":329,"last_global_frame":1139},{"id":"T14.02","src":"gfx/T14_2820_3150.html","dst_in":2820,"dur":330,"global_start_frame":2820,"local_range":[0,330],"last_local_frame":329,"last_global_frame":3149}],"video_array_index":13},{"id":"T15","name":"brand-wordmark","kind":"gfx","role":"Framivra vector wordmark from supplied Noto Sans SC weight600 glyph paths; no second miniature B/F, download badge or platform claim.","windows":[{"in":3360,"out":3600}],"scheduled_frames":240,"remove_control":{"window":[3450,3540],"expected_difference":"The finished image loses its product name.","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED","clips":[{"id":"T15.01","src":"gfx/T15_3360_3600.html","dst_in":3360,"dur":240,"global_start_frame":3360,"local_range":[0,240],"last_local_frame":239,"last_global_frame":3599}],"video_array_index":14},{"id":"T16","name":"native-captions","kind":"native-subtitle-stream","role":"One native inline subtitle stream, burned via existing ASS path; not a sixteenth video track.","windows":[{"in":195,"out":255},{"in":420,"out":600},{"in":900,"out":1080},{"in":1410,"out":1590},{"in":1830,"out":2010},{"in":2460,"out":2640},{"in":2940,"out":3120},{"in":3330,"out":3450}],"scheduled_frames":1260,"remove_control":{"window":[900,1080],"expected_difference":"The separate Chinese meaning statement disappears.","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED"},{"id":"T17","name":"harmony-melody","kind":"audio","role":"Abstract full-film harmony/melody role; timbre, notes and arrangement unapproved.","windows":[{"in":0,"out":3600}],"scheduled_frames":3600,"remove_control":{"window":[300,600],"expected_difference":"Remove audible harmony/melodic narrative; prove by isolated and full-mix listening.","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED"},{"id":"T18","name":"rhythmic-motion","kind":"audio","role":"Abstract rhythmic propulsion with intentional rests; no fixed tempo/instrument chosen.","windows":[{"in":0,"out":3600}],"scheduled_frames":3600,"remove_control":{"window":[810,960],"expected_difference":"Remove audible rhythmic propulsion in its selected active passage.","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED"},{"id":"T19","name":"tactile-contact","kind":"audio","role":"Original tactile action feedback at actual contact cues; a contact is not a separate track.","windows":[{"in":0,"out":3600}],"scheduled_frames":3600,"remove_control":{"window":[0,180],"expected_difference":"Cut/imprint/trim/eye material contact feedback is audibly lost.","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED"},{"id":"T20","name":"character-signature","kind":"audio","role":"Distinct short character/return gesture role, not a duplicate of T17.","windows":[{"in":0,"out":3600}],"scheduled_frames":3600,"remove_control":{"window":[190,264],"expected_difference":"Hero lift loses its independently audible character gesture.","status":"REQUIRED_NOT_RUN"},"status":"DESIGN_ONLY_NOT_RENDERED_OR_AUDITIONED"}],"entry_contract":{"geometry_units":"1280x720 author viewBox; uniform output scale1.5","frame_domain":"global integer f in [0,3600); local f in [0,clip.dur)","local_to_global":"global_f = entry.global_start_frame + local_f","scene_frame":"find scene by in <= global_f < out; scene_f = global_f - scene.in","input_sanitization":"Finite numeric input floored, then clamped to [0,dur-1]; NaN/nonfinite =>0. Validate entry start/dur as exact recipe values. Never clamp global_f at299.","determinism":"Pure state from global_f + immutable inputs. No elapsed time, requestAnimationFrame clock, Math.random, or accumulated previous-frame state. Repeated/reverse/shuffled f must produce identical state.","camera":"One cameraFromGlobalFrame result applies to all geometry in the shared world. Intentional foreground wing override [264,345) uses the same B transform on both bird pages. Source slips and subtitles use screen-space safe areas.","mask_contract":"Each HTML page recreates its own SVG definitions from shared resolved world geometry. No cross-document url(#id), no duplicate opaque gate painted on bird pages. Body and wings consume bit-identical B visibility regions; retain page-specific ID prefixes to avoid collisions.","no_new_renderer_protocol":true,"renderer_path":"Keep current navigation-based page protocol; expose existing __vtx_render(local_f) for deterministic testing only. Do not declare navfree eligibility, static spans, beginFrame or canvas-export fast paths."},"project_cache_dependencies":["runtime/shared.js","runtime/effects.json","production/cues.json","production/source-bindings.json","design/geometry.json","design/shapes.svg"],"clip_dependency_rule":"List all actually loaded local files including scene modules/font/license-linked font inputs and waveform JSON at build time; not transitive by assumption. Add waveform only to T13 unless genuinely shared. Effects file always declared. No remote assets.","paper":{"source_id":"paper","consumer_path":"media/paper-background.mp4","producer_existing_path":"html-opening-preview/candidate-r3/templates/promo-demo/v1/preview/paper-background.mp4","sha256":"fc42e42426548fcde8b296c1844a36da8daf63fd5a66224ef6dca5fe16155664","clip_recipe":[{"src":"paper","src_in":0,"src_out":300,"dst_in":0,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":300,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":600,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":900,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":1200,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":1500,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":1800,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":2100,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":2400,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":2700,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":3000,"dur":300},{"src":"paper","src_in":0,"src_out":300,"dst_in":3300,"dur":300}],"audio":"Do not create audio event for the compatibility silence stream."},"missing_assets_rule":"Do not create empty HTML, placeholder WAV or random envelope to report completed20. Builder must reject absent final source assets. The blueprint below is schema-shaped design only.","z_rule":"Use this fixed array order. Any locally lower object that passes in front is handled by the upper page excluding shared occluder geometry. Tools/strip/F pages must also obey contact masks. Maskless copies are diagnostics only.","audio_rule":"Four full120-second stems with sample-accurate internal rests. Timeline events use one source each, no looping. Unity interchange volume is a placeholder export convention, not an approved mix decision."};
},
"runtime/data.js": function(module,exports,require){
'use strict';
const {deepFreeze}=require('./math.js');
module.exports=deepFreeze({
  cues:require('../assets/cues.json'), geometry:require('../assets/geometry.json'), trackMap:require('../assets/track-map.json'),
  sourceBindings:require('../assets/source-bindings.json'), actualTimeline:require('../assets/timeline.json'), timelineBlueprint:require('../assets/timeline-blueprint.json'),
  boundaries:require('../assets/boundaries.json'), motion:require('../assets/motion-contract.json'), workload:require('../assets/gfx-workload.json'),
  waveform:require('../assets/media-bindings.json').waveform, fonts:require('./fonts.js')
});

},
"runtime/effects.js": function(module,exports,require){
'use strict';
const S = require('./svg.js');
const {finite,clamp01,deepFreeze} = require('./math.js');
const DEFAULTS=deepFreeze({path_deformation:true,material_masks:true,text_reveal:true,translucent_trails:true,gradient_alpha_reveal:true,local_blur:true});
function options(overrides={},diagnostic=false) {
  for(const [key,value] of Object.entries(overrides)) {
    if(!Object.hasOwn(DEFAULTS,key) || typeof value!=='boolean')throw new TypeError(`Unknown or nonboolean effect ${key}`);
    if(value!==true && !diagnostic)throw new Error('Effect-off controls require an explicit diagnostic profile');
  }
  return deepFreeze({...DEFAULTS,...overrides});
}
function bounds(b) {
  if(!Array.isArray(b)||b.length!==4)throw new TypeError('Effect bounds must be [x,y,width,height]');
  b.forEach(v=>finite(v)); if(!(b[2]>0 && b[3]>0))throw new RangeError('Positive effect bounds required');
  return {x:b[0],y:b[1],width:b[2],height:b[3]};
}
function mask(id,b,occluders,enabled=true) {
  const region=bounds(b);
  return S.node('mask',{id,...region,maskUnits:'userSpaceOnUse',maskContentUnits:'userSpaceOnUse','mask-type':'luminance'},[
    S.rect(...b,'white'),...(enabled ? [S.group(occluders,{fill:'black'})]:[])
  ]);
}
// Structural coverage preserves real material ordering/contact in every diagnostic.
// Only optional workload masks may be disabled with material_masks.
function structuralSilhouette(node) {
  if(typeof node==='string'||!['g','path','polygon','polyline','rect','circle','ellipse','line'].includes(node.tag))throw new TypeError('Structural occluder must be resolved vector geometry');
  const attrs={...node.attrs};
  // Explicit child paint overrides inherited group paint in luminance masks.
  // Preserve transparent holes and shape/transform data, but make every painted
  // silhouette opaque black. Clone immutable input nodes; never alter the film art.
  if(attrs.fill && attrs.fill!=='none')attrs.fill='black';
  if(attrs.stroke && attrs.stroke!=='none')attrs.stroke='black';
  delete attrs.opacity;delete attrs['fill-opacity'];delete attrs['stroke-opacity'];
  if(attrs.mask||attrs.filter||attrs['clip-path'])throw new TypeError('Structural occluders require resolved plain geometry');
  return S.node(node.tag,attrs,node.children.map(structuralSilhouette));
}
function structuralMask(id,b,occluders) { return mask(id,b,occluders.map(structuralSilhouette),true); }
function contactPatch({id,bounds: b,sigma=2.6,shape,enabled=true}) {
  finite(sigma); if(sigma<0||sigma>2.6)throw new RangeError('Contact blur sigma must be in [0,2.6] author units');
  const region=bounds(b);
  if(region.width>160 || region.height>64)throw new RangeError('Contact filter must remain locally bounded (<=160x64 author units)');
  return {defs:enabled?[S.node('filter',{id,...region,filterUnits:'userSpaceOnUse','color-interpolation-filters':'sRGB'},[S.node('feGaussianBlur',{stdDeviation:sigma})])]:[],
    nodes:[S.group(shape,{opacity:.09,...(enabled?{filter:`url(#${id})`}:{})})]};
}
function seamGradient({id,bounds:b,enabled=true}) {
  const [x,y,w,h]=b; bounds(b);
  if(w>300||h>160)throw new RangeError('Seam gradient exceeds bounded local footprint');
  const stops=enabled?[[0,.1],[1,0]]:[[0,.1],[.5,.1],[.5,0],[1,0]];
  return S.node('linearGradient',{id,gradientUnits:'userSpaceOnUse',x1:x,y1:y,x2:x+w,y2:y},stops.map(([offset,opacity])=>S.node('stop',{offset,'stop-color':'#252B29','stop-opacity':opacity})));
}
function textReveal({id,bounds:b,progress,enabled=true}) {
  bounds(b); const width=b[2]*(enabled?clamp01(progress):1);
  return S.node('clipPath',{id,clipPathUnits:'userSpaceOnUse'},[S.rect(b[0],b[1],width,b[3],'white')]);
}
const deform=(enabled,actual,rigid)=>enabled?actual:rigid;
module.exports={DEFAULTS,options,bounds,mask,structuralMask,contactPatch,seamGradient,textReveal,deform};

},
"runtime/entries.js": function(module,exports,require){
'use strict';
const {deepFreeze}=require('./math.js');
const DATA=require('./data.js');
function createEntries(trackMap,timeline) {
  const entries=[],gfxTracks=trackMap.tracks.filter(t=>t.kind==='gfx');
  if(gfxTracks.length!==14)throw new Error('R04 recipe must contain exactly 14 GFX roles');
  for(const track of gfxTracks) {
    const matches=timeline.video_tracks.filter(t=>t.name===track.name && t.kind==='gfx');
    if(matches.length!==1)throw new Error(`Unique real timeline track required: ${track.name}`);
    if(timeline.video_tracks[track.video_array_index]!==matches[0])throw new Error(`Paint order mismatch for ${track.id}`);
    if(matches[0].clips.length!==track.clips.length)throw new Error(`Unexpected timeline clips in ${track.name}`);
    for(const clip of track.clips) {
      const actual=matches[0].clips.filter(c=>c.src===clip.src);
      if(actual.length!==1 || actual[0].dst_in!==clip.dst_in || actual[0].dur!==clip.dur)throw new Error(`Clip recipe drift: ${clip.id}`);
      if(!Number.isInteger(clip.dur)||clip.dur<=0||!Number.isInteger(clip.global_start_frame)||clip.global_start_frame!==clip.dst_in||clip.dst_in<0||clip.dst_in+clip.dur>3600)throw new Error(`Invalid clip window: ${clip.id}`);
      if(!/^gfx\/T\d{2}_\d{4}_\d{4}\.html$/.test(clip.src))throw new Error(`Unexpected entry path: ${clip.src}`);
      entries.push({entryId:clip.id,layerId:track.id,role:track.name,src:clip.src,windowStart:clip.global_start_frame,duration:clip.dur,paintIndex:track.video_array_index});
    }
  }
  if(entries.length!==19||new Set(entries.map(e=>e.entryId)).size!==19||new Set(entries.map(e=>e.src)).size!==19)throw new Error('Expected 19 unique GFX entries');
  return deepFreeze(entries);
}
function bindSources(timeline,recipes) {
  return deepFreeze(recipes.items.map(item=>{
    const tracks=timeline.video_tracks.filter(t=>t.name===item.track_name);
    if(tracks.length!==1)throw new Error(`Source binding track is not unique: ${item.id}`);
    const clips=tracks[0].clips.filter(c=>c.src===item.clip_src);
    if(clips.length!==1)throw new Error(`Source binding clip is not unique: ${item.id}`);
    const fields={};
    for(const key of item.fields) {
      if(!['src','dst_in','dur'].includes(key)||!Object.hasOwn(clips[0],key))throw new Error(`Unsupported binding ${key}`);
      fields[key]=clips[0][key];
    }
    return {...item,fields,displayFields:Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,k==='src'?v.split('/').pop():String(v)]))};
  }));
}
const ENTRIES=createEntries(DATA.trackMap,DATA.actualTimeline);
function getEntry(id) { const found=ENTRIES.find(e=>e.entryId===id); if(!found)throw new RangeError(`Unknown clip entry ${id}`);return found; }
function frameNumber(value,duration) {
  if(!Number.isInteger(duration)||duration<1)throw new RangeError('Positive integer clip duration required');
  if(typeof value!=='string' && typeof value!=='number' && value!==null && value!==undefined)return 0;
  const n=Number(value); return Number.isFinite(n)?Math.min(duration-1,Math.max(0,Math.floor(n))):0;
}
function frameContext(entryId,value) {
  const entry=getEntry(entryId),localFrame=frameNumber(value,entry.duration),globalFrame=entry.windowStart+localFrame;
  const scene=DATA.cues.scenes.find(s=>globalFrame>=s.in&&globalFrame<s.out);
  if(!scene)throw new RangeError('Frame must resolve exactly one scene');
  return deepFreeze({entry,localFrame,globalFrame,localSceneFrame:globalFrame-scene.in,scene});
}
module.exports={ENTRIES,createEntries,bindSources,getEntry,frameNumber,frameContext};

},
"runtime/fonts.js": function(module,exports,require){
'use strict';
const review=require('../assets/provider-review.json');
function provider(id,raw){const approved=review.providers[id]?.status==='PASS';return {...raw,reviewStatus:approved?'INDEPENDENTLY_APPROVED':raw.reviewStatus};}
module.exports={sourceLabels:provider('sourceLabels',require('../assets/fonts/source-label-glyphs.json')),brand:provider('brand',require('../assets/fonts/brand-glyphs.json'))};

},
"runtime/geometry.js": function(module,exports,require){
'use strict';
const M=require('./math.js');
const S=require('./svg.js');
const E=require('./effects.js');
const G=M.deepFreeze(require('../assets/geometry.json'));
const PALETTE=M.deepFreeze({paper:'#F1EDE1',ink:'#252B29',red:'#D9533B',grey:'#C3C6BB',mid:'#90998E',amber:'#D9AE53',pale:'#DCDCCF',stock:'#CBC5B3'});
// Original bird points and hinge are from candidate-r3 / R03, not a replacement logo.
function birdLocal(part,{stage=4,wing=0,feet=false}={}) {
  if(!['body','wings'].includes(part))throw new TypeError('Bird part must be body or wings');
  if(!Number.isInteger(stage)||stage<0||stage>4)throw new RangeError('Bird stage must be integer 0..4');
  M.finite(wing); const b=G.bird, c=PALETTE;
  if(part==='wings') return stage<1?[]:[S.polygon(b.wing_a.map(p=>M.transform(p,M.rotate(wing,b.wing_a_hinge))),c.red),S.polygon(b.wing_b,c.red)];
  return [
    ...(stage>=2?[S.polygon(b.body,c.ink),S.polygon(b.tail,c.ink)]:[]),
    S.polygon(b.beak,c.amber),
    S.polygon(stage<3?[[28,-66],[112,-66],[112,22],[28,22]]:b.head,c.stock),
    ...(stage>=4?[S.circle(...b.eye.center,b.eye.radius,c.ink)]:[]),
    ...(feet?[S.line([[8,33],[11,48],[29,48]],c.ink,3),S.line([[-19,32],[-18,48],[-1,48]],c.ink,3)]:[])
  ];
}
function silhouette(node) {
  if(typeof node==='string')throw new TypeError('Text is not an occluder silhouette');
  if(!['g','path','polygon','polyline','rect','circle','ellipse','line'].includes(node.tag))throw new TypeError('Occluder must be vector geometry');
  const attrs={...node.attrs};
  if(attrs.fill!=='none')attrs.fill='black';
  if(attrs.stroke && attrs.stroke!=='none')attrs.stroke='black';
  delete attrs.opacity; delete attrs['fill-opacity']; delete attrs['stroke-opacity'];
  if(attrs.mask||attrs.filter||attrs['clip-path'])throw new TypeError('Occluder silhouettes must have resolved plain geometry');
  return S.node(node.tag,attrs,node.children.map(silhouette));
}
// Mask is outside the B transform, in the same world/screen space as its occluders.
// The outer world camera is applied once by runtime.renderScene, equally to both pages.
function birdContribution({bird,id,effects,part,history=null}) {
  if(!bird)return {defs:[],world:[],screen:[]};
  if(!['world','screen'].includes(bird.space))throw new TypeError('Bird space must be world or screen');
  const occluders=(bird.occluders||[]).map(silhouette), maskId=id('whole-bird-visible');
  const clip=E.structuralMask(maskId,bird.maskBounds||[-4096,-4096,8192,8192],occluders);
  const shape=S.group(birdLocal(part,bird),{transform:S.matrixString(M.poseMatrix(bird.pose))});
  const shapes=[shape];
  if(part==='wings' && history && effects.translucent_trails) {
    if(history.space!==bird.space)throw new Error('Historical wing must use the current coordinate space');
    shapes.unshift(S.group(birdLocal('wings',history),{transform:S.matrixString(M.poseMatrix(history.pose)),opacity:.09,'data-history':'global-minus-3'}));
  }
  return {defs:[clip],world:bird.space==='world'?[S.group(shapes,{mask:`url(#${maskId})`})]:[],screen:bird.space==='screen'?[S.group(shapes,{mask:`url(#${maskId})`})]:[]};
}
function carrierAssembly(rootMatrix) {
  M.matrix(rootMatrix); const root=[...rootMatrix];
  return M.deepFreeze({carrierMatrix:root,gateMatrix:root,receiverMatrix:root,thresholdRMatrix:root,signalMountMatrix:root,fixedGuideMatrix:[...M.IDENTITY],
    guide:G['W-guide'],carrierRect:G['P-C'].local_carrier_rect,receivers:G['P-C'].receiver_rectangles});
}
// Project a material panel about an immutable common edge. Width is material width;
// its projected depth is cos(angle)*width. This is flat SVG, not a 3-D render API.
function hingePanels(sharedEdge,panels) {
  if(!Array.isArray(sharedEdge)||sharedEdge.length!==2)throw new TypeError('Two hinge endpoints required');
  sharedEdge.forEach(M.point); const edge=sharedEdge.map(p=>[...p]);
  const dx=edge[1][0]-edge[0][0],dy=edge[1][1]-edge[0][1],length=Math.hypot(dx,dy);
  if(!length)throw new RangeError('Hinge edge has zero length');
  const normal=[-dy/length,dx/length],faces={};
  for(const [name,{width,angle,side=1}] of Object.entries(panels)) {
    M.finite(width);M.finite(angle);if(width<=0||![-1,1].includes(side))throw new RangeError('Invalid hinge panel');
    const depth=side*width*Math.cos(angle*Math.PI/180),offset=normal.map(n=>n*depth);
    faces[name]={hinge:edge,materialWidth:width,angle,points:[edge[0],edge[1],edge[1].map((v,i)=>v+offset[i]),edge[0].map((v,i)=>v+offset[i])]};
  }
  return M.deepFreeze({hinge:edge,faces});
}
function forceChain({frameEndpoint,paperAnchor}) {
  M.point(frameEndpoint);M.point(paperAnchor); const anchor=[...paperAnchor];
  return M.deepFreeze({frameEndpoint:[...frameEndpoint],buckleAnchor:anchor,paperAnchor:anchor,tongueEnd:anchor,tongue:[[...frameEndpoint],anchor],tongueWidth:5});
}
function forceChainShapes(chain) {
  return {tongue:S.line(chain.tongue,PALETTE.red,chain.tongueWidth),buckle:S.rect(chain.buckleAnchor[0]-7,chain.buckleAnchor[1]-4,14,8,PALETTE.ink)};
}
function waveformPoints(envelope,{startFrame,endFrame,x,y,width,height}) {
  if(!envelope || envelope.provenance?.kind!=='actual-pcm' || !/^[0-9a-f]{64}$/.test(envelope.provenance.sha256||''))throw new Error('Verified real PCM envelope required; no synthetic fallback');
  if(!Number.isInteger(startFrame)||!Number.isInteger(endFrame)||endFrame<=startFrame)throw new RangeError('Half-open waveform interval required');
  [x,y,width,height].forEach(v=>M.finite(v));
  const samples=envelope.samples.filter(s=>s.frame>=startFrame&&s.frame<endFrame);
  if(!samples.length)throw new Error('PCM envelope has no samples in requested window');
  return samples.map(s=>{if(!Number.isInteger(s.frame)||Math.abs(M.finite(s.value))>1)throw new TypeError('Invalid PCM envelope sample');return [x+width*(s.frame-startFrame)/(endFrame-startFrame),y-height*s.value];});
}
module.exports={G,PALETTE,birdLocal,birdContribution,carrierAssembly,hingePanels,forceChain,forceChainShapes,waveformPoints,silhouette};

},
"runtime/math.js": function(module,exports,require){
'use strict';
// Key interpolation follows the original MIT-licensed opening preview.
function finite(value, name = 'number') {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
  return value;
}
const clamp01 = value => Math.min(1, Math.max(0, finite(value)));
const mix = (a, b, u) => finite(a) + (finite(b) - a) * finite(u);
const smoothstep = value => { const u = clamp01(value); return u*u*(3-2*u); };
const progress = (frame, start, end) => {
  if (!(end > start)) throw new RangeError('Progress interval must have positive length');
  return clamp01((finite(frame) - finite(start)) / (finite(end) - start));
};
function keys(frame, values) {
  if (!Array.isArray(values) || !values.length) throw new TypeError('Key list required');
  values.forEach(([f,v],i) => { finite(f); finite(v); if(i && f <= values[i-1][0]) throw new RangeError('Keys must strictly increase'); });
  finite(frame);
  if (frame <= values[0][0]) return values[0][1];
  for (let i=1;i<values.length;i++) {
    const [fa,a] = values[i-1], [fb,b] = values[i];
    if (frame <= fb) return mix(a,b,smoothstep(progress(frame,fa,fb)));
  }
  return values[values.length-1][1];
}
function point(p) {
  if (!Array.isArray(p) || p.length !== 2) throw new TypeError('Expected [x,y]');
  p.forEach(v => finite(v)); return p;
}
function matrix(m) {
  if (!Array.isArray(m) || m.length !== 6) throw new TypeError('Expected affine matrix of six numbers');
  m.forEach(v => finite(v)); return m;
}
const IDENTITY = Object.freeze([1,0,0,1,0,0]);
function multiply(a,b) {
  matrix(a); matrix(b);
  return [a[0]*b[0]+a[2]*b[1],a[1]*b[0]+a[3]*b[1],a[0]*b[2]+a[2]*b[3],a[1]*b[2]+a[3]*b[3],a[0]*b[4]+a[2]*b[5]+a[4],a[1]*b[4]+a[3]*b[5]+a[5]];
}
const translate = (x,y) => [1,0,0,1,finite(x),finite(y)];
const scale = value => [finite(value),0,0,value,0,0];
function rotate(degrees, pivot=[0,0]) {
  point(pivot); const a = finite(degrees)*Math.PI/180, c=Math.cos(a), s=Math.sin(a);
  return multiply(translate(...pivot),multiply([c,s,-s,c,0,0],translate(-pivot[0],-pivot[1])));
}
function transform(p,m) { point(p); matrix(m); return [m[0]*p[0]+m[2]*p[1]+m[4],m[1]*p[0]+m[3]*p[1]+m[5]]; }
function poseMatrix({x,y,scale: z=1,angle=0}) {
  if (!(z>0)) throw new RangeError('Bird scale must be positive');
  return multiply(translate(x,y),multiply(rotate(angle),scale(z)));
}
function cameraMatrix({x,y,z}) {
  if (!(z>0)) throw new RangeError('Camera zoom must be positive');
  return [finite(z),0,0,z,640-finite(x)*z,360-finite(y)*z];
}
function cubic(points,u) {
  if (points.length!==4) throw new TypeError('Cubic needs four control points');
  points.forEach(point); u=clamp01(u); const v=1-u;
  return [0,1].map(i=>v*v*v*points[0][i]+3*v*v*u*points[1][i]+3*v*u*u*points[2][i]+u*u*u*points[3][i]);
}
function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) { Object.values(value).forEach(deepFreeze); Object.freeze(value); }
  return value;
}
module.exports = {finite,clamp01,mix,smoothstep,progress,keys,point,matrix,IDENTITY,multiply,translate,scale,rotate,transform,poseMatrix,cameraMatrix,cubic,deepFreeze};

},
"runtime/opening-state.js": function(module,exports,require){
'use strict';
// Adapted from Framivra candidate-r3 opening.html (MIT, copyright 2026 ruochongliang).
// Preserve the original opening curve math. This helper is not the S1 scene implementation.
const C=require('../assets/opening-cues.json');
const {keys,mix,smoothstep,progress,deepFreeze,cameraMatrix}=require('./math.js');
const ramp=(f,a,b)=>smoothstep(progress(f,a,b));
const down=(f,[start,hit,end,travel])=>f<=hit?travel*ramp(f,start,hit):travel*(1-ramp(f,hit,end));
function openingState(f) {
  if(!Number.isInteger(f)||f<0||f>299)throw new RangeError('Opening helper accepts explicit reference frame 0..299 only');
  const hx=keys(f,C.keys.hero_x),approach=f>=C.windows_inclusive.approach[0];
  const camera={z:keys(f,C.keys.camera_zoom),x:f<184?hx+20:keys(f,C.keys.camera_x_reveal),y:keys(f,C.keys.camera_y)};
  const stage=f<C.contacts.cut?0:f<C.contacts.body?1:f<C.contacts.trim?2:f<C.contacts.eye?3:4;
  const u=ramp(f,...C.windows_inclusive.approach),scale=mix(.65,35,u*u),t=ramp(f,...C.windows_inclusive.waste_separation);
  return deepFreeze({f,hx,camera,cameraMatrix:cameraMatrix(camera),stage,approach,
    bird:{space:approach?'screen':'world',pose:{x:approach?mix(965,640+41*scale,u):hx,y:approach?mix(272,360+28*scale,u):keys(f,C.keys.bird_y),angle:approach?0:keys(f,C.keys.bird_angle),scale:approach?scale:1},wing:approach||f<194?0:keys(f,C.keys.wing_angle),stage,feet:false,occluders:[]},
    waste:{visible:f>=18&&f<=54,x:350-85*t,y:440+360*t,angle:-12*t},offcuts:{visible:f>=109&&f<139,t:ramp(f,...C.windows_inclusive.head_offcut_fall)},
    presses:{cut:102+down(f,C.presses.cut),body:240+down(f,C.presses.body),trim:down(f,C.presses.trim),eye:265+down(f,C.presses.eye)},wave:f>=219&&f<=245?18*Math.sin(Math.PI*ramp(f,219,245)):0});
}
module.exports={openingState};

},
"runtime/runtime.js": function(module,exports,require){
'use strict';
const DATA=require('./data.js');
const M=require('./math.js');
const S=require('./svg.js');
const E=require('./effects.js');
const G=require('./geometry.js');
const OPENING=require('./opening-state.js');
const ENTRY=require('./entries.js');
const REGISTRY=require('../scenes/index.js');
const EMPTY=M.deepFreeze({defs:[],world:[],screen:[]});
function stateFrame(globalFrame) { return globalFrame>=2940 && globalFrame<3090 ? 2940 : globalFrame; }
function absent(layerId,globalFrame) {
  return DATA.cues.explanatory_withdrawals.some(w=>w.track===layerId&&globalFrame>=w.zero_contribution[0]&&globalFrame<w.zero_contribution[1]);
}
function fragment(value) {
  if(!value||!['defs','world','screen'].every(k=>Array.isArray(value[k])))throw new TypeError('Scene must return {defs:[],world:[],screen:[]} SVG nodes');
  // Serialize now to reject invalid node trees before composing any DOM.
  S.serialize(value.defs);S.serialize(value.world);S.serialize(value.screen);
  return value;
}
/** Shared layer renderer. See contract.d.ts. ctx is frame-local and read-only. */
function renderScene(ctx,{globalFrame,localSceneFrame,layerId}) {
  if(globalFrame!==ctx.globalFrame||localSceneFrame!==ctx.localSceneFrame||layerId!==ctx.entry.layerId)throw new Error('Mismatched scene invocation');
  if(absent(layerId,globalFrame))return EMPTY;
  if(layerId==='T07'||layerId==='T08') {
    let history=null;
    if(layerId==='T08' && globalFrame>=1404 && globalFrame<1452 && ctx.effects.translucent_trails)history=ctx.sharedAt(globalFrame-3).bird;
    return G.birdContribution({bird:ctx.state.bird,part:layerId==='T07'?'body':'wings',id:ctx.id,effects:ctx.effects,history});
  }
  return fragment(ctx.sceneModule.renderScene(ctx,{globalFrame,localSceneFrame,layerId}));
}
function createRuntime({scenes=REGISTRY,diagnostic=false,effects={},allowScaffold=false}={}) {
  const flags=E.options(effects,diagnostic);
  if(allowScaffold && !diagnostic)throw new Error('Incomplete scaffolds require explicit diagnostic mode');
  for(const item of DATA.cues.scenes)if(!scenes[item.id]||scenes[item.id].sceneId!==item.id)throw new Error(`Missing isolated scene module: ${item.id}`);
  const sharedBase=M.deepFreeze({assets:DATA,math:M,svg:S,geometry:G,effectHelpers:E,opening:OPENING,effects:flags});
  function sharedAt(globalFrame) {
    if(!Number.isInteger(globalFrame)||globalFrame<0||globalFrame>=3600)throw new RangeError('Global frame must be integer 0..3599');
    const frame=stateFrame(globalFrame),scene=DATA.cues.scenes.find(s=>frame>=s.in&&frame<s.out),sceneModule=scenes[scene.id];
    if(!sceneModule.implemented && !allowScaffold)throw new Error(`UNIMPLEMENTED_SCENE:${scene.id}`);
    const state=sceneModule.resolveSceneState(sharedBase,{globalFrame:frame,localSceneFrame:frame-scene.in});
    if(!state||!Object.hasOwn(state,'bird'))throw new TypeError(`${scene.id} must explicitly supply bird state (or null)`);
    M.matrix(state.cameraMatrix);
    return M.deepFreeze(state);
  }
  function renderFrame(entryId,input,{paper=false}={}) {
    const f=ENTRY.frameContext(entryId,input),sceneModule=scenes[f.scene.id],state=sharedAt(f.globalFrame);
    const ctx=M.deepFreeze({...sharedBase,...f,state,sceneModule,stateFrame:stateFrame(f.globalFrame),sharedAt,id:S.ids(`film-${entryId.replace('.','-')}`),sourceBindingStatus:'ACTUAL_FINAL_TIMELINE',sourceFields:ENTRY.bindSources(DATA.actualTimeline,DATA.sourceBindings)});
    const result=fragment(renderScene(ctx,{globalFrame:f.globalFrame,localSceneFrame:f.localSceneFrame,layerId:f.entry.layerId}));
    const nodes=[...(paper?[S.rect(0,0,1280,720,G.PALETTE.paper,{'data-standalone-paper':'diagnostic-only'})]:[]),
      ...(result.defs.length?[S.node('defs',{},result.defs)]:[]),
      ...(result.world.length?[S.group(result.world,{transform:S.matrixString(state.cameraMatrix),'data-space':'world'})]:[]),
      ...(result.screen.length?[S.group(result.screen,{'data-space':'screen'})]:[])];
    return M.deepFreeze({entryId,layerId:f.entry.layerId,localFrame:f.localFrame,globalFrame:f.globalFrame,localSceneFrame:f.localSceneFrame,sceneId:f.scene.id,
      stateFrame:ctx.stateFrame,implemented:sceneModule.implemented===true,profile:diagnostic?(allowScaffold?'draft-source':'diagnostic'):'film',effects:flags,fragment:S.serialize(nodes),nodes});
  }
  return Object.freeze({renderFrame,sharedAt,entries:ENTRY.ENTRIES,effects:flags});
}
// Navigation ?f= is the production protocol; this synchronous setter is its exact mirror.
// Merely exposing it never enables navfree, static spans, beginFrame or canvas export.
function mount(document,window,entryId,options={}) {
  ENTRY.getEntry(entryId); const runtime=createRuntime(options),root=document.getElementById('scene');
  if(!root)throw new Error('Missing #scene SVG root');
  const paper=new URLSearchParams(window.location.search).get('paper')==='1';
  function render(localFrame) {
    const result=runtime.renderFrame(entryId,localFrame,{paper});
    root.innerHTML=result.fragment;
    root.setAttribute('data-frame',String(result.localFrame));
    root.setAttribute('data-global-frame',String(result.globalFrame));
    root.setAttribute('data-scene',result.sceneId);
    root.setAttribute('data-profile',result.profile);
    root.setAttribute('data-status',result.implemented?'scene-implemented-unverified':'candidate-not-production');
    return result.localFrame;
  }
  window.__vtx_render=render;
  render(new URLSearchParams(window.location.search).get('f'));
  return runtime;
}
module.exports={createRuntime,renderScene,mount,stateFrame,absent};

},
"runtime/svg.js": function(module,exports,require){
'use strict';
const {finite,point,matrix,deepFreeze} = require('./math.js');
// Small immutable SVG tree, with a stable serializer and an optional real DOM adapter.
// No parser, active SVG, inline handlers, stylesheet or external references.
const TAGS = new Set('g defs path polygon polyline rect circle ellipse line clipPath mask linearGradient stop filter feGaussianBlur text textPath use title desc'.split(' '));
const ATTRS = new Set('id d points x y x1 y1 x2 y2 cx cy r rx ry width height fill stroke stroke-width stroke-linecap stroke-linejoin fill-rule clip-rule opacity fill-opacity stroke-opacity transform clip-path mask filter maskUnits maskContentUnits mask-type clipPathUnits filterUnits gradientUnits offset stop-color stop-opacity stdDeviation color-interpolation-filters font-family font-size font-weight text-anchor dominant-baseline href startOffset textLength lengthAdjust'.split(' '));
const NUMERIC = new Set('x y x1 y1 x2 y2 cx cy r rx ry width height opacity fill-opacity stroke-opacity stroke-width stop-opacity stdDeviation font-size textLength'.split(' '));
const SVG_NS = 'http://www.w3.org/2000/svg';
const number = n => { finite(n); return Object.is(n,-0) ? '0' : String(n); };
const escape = s => String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
function attrValue(name,value) {
  if (NUMERIC.has(name) && typeof value==='number') return number(value);
  if (typeof value !== 'string' && typeof value !== 'number') throw new TypeError(`Invalid ${name}`);
  if (typeof value==='number') finite(value);
  const str=String(value);
  if (name==='href' && !/^#[A-Za-z][\w.-]*$/.test(str)) throw new TypeError('SVG href must be document-local');
  if (/url\s*\(/i.test(str) && !/^url\(#[A-Za-z][\w.-]*\)$/.test(str)) throw new TypeError('Only local SVG URL references allowed');
  if (/javascript:|data:|https?:|file:/i.test(str)) throw new TypeError('External SVG resources forbidden');
  return str;
}
function node(tag,attrs={},children=[]) {
  if (!TAGS.has(tag)) throw new TypeError(`Unsupported SVG tag ${tag}`);
  const clean={};
  for(const key of Object.keys(attrs).sort()) {
    if (!ATTRS.has(key) && !/^data-[a-z][a-z0-9-]*$/.test(key)) throw new TypeError(`Unsupported SVG attribute ${key}`);
    if(attrs[key]!==undefined && attrs[key]!==null) clean[key]=attrValue(key,attrs[key]);
  }
  const items=(Array.isArray(children)?children:[children]).flat(Infinity).filter(x=>x!==null && x!==undefined);
  for(const item of items) if(typeof item!=='string' && (!item || !TAGS.has(item.tag))) throw new TypeError('Invalid SVG child');
  return deepFreeze({tag,attrs:clean,children:items});
}
function serialize(item) {
  if (Array.isArray(item)) return item.map(serialize).join('');
  if (typeof item === 'string') return escape(item);
  if (!item || !TAGS.has(item.tag)) throw new TypeError('SVG fragment must contain SVG nodes');
  return `<${item.tag}${Object.keys(item.attrs).sort().map(k=>` ${k}="${escape(attrValue(k,item.attrs[k]))}"`).join('')}>${item.children.map(serialize).join('')}</${item.tag}>`;
}
function toDOM(document,item) {
  if(Array.isArray(item)) { const fragment=document.createDocumentFragment(); item.forEach(n=>fragment.appendChild(toDOM(document,n))); return fragment; }
  if(typeof item==='string') return document.createTextNode(item);
  const element=document.createElementNS(SVG_NS,item.tag);
  Object.keys(item.attrs).sort().forEach(k=>element.setAttribute(k,item.attrs[k]));
  item.children.forEach(child=>element.appendChild(toDOM(document,child))); return element;
}
const points = values => values.map(p=>{point(p);return p.map(number).join(',');}).join(' ');
const matrixString = value => { matrix(value); return `matrix(${value.map(number).join(' ')})`; };
const group=(children,attrs={})=>node('g',attrs,children);
const polygon=(p,fill,attrs={})=>node('polygon',{points:points(p),fill,...attrs});
const line=(p,stroke,width=3,attrs={})=>node('polyline',{points:points(p),fill:'none',stroke,'stroke-width':width,'stroke-linejoin':'round',...attrs});
const rect=(x,y,width,height,fill,attrs={})=>node('rect',{x,y,width,height,fill,...attrs});
const circle=(cx,cy,r,fill,attrs={})=>node('circle',{cx,cy,r,fill,...attrs});
const path=(d,fill='none',attrs={})=>node('path',{d,fill,...attrs});
function pathPoints(points,close=false) { return points.map((p,i)=>`${i?'L':'M'}${number(point(p)[0])} ${number(p[1])}`).join(' ')+(close?' Z':''); }
function cubicPath(control) { if(control.length!==4)throw new TypeError('Cubic needs four points'); return `M${points([control[0]])} C${points(control.slice(1))}`; }
function ids(prefix) {
  if(!/^[A-Za-z][\w.-]*$/.test(prefix))throw new TypeError('Unsafe page ID prefix');
  return name=>{if(!/^[A-Za-z][\w.-]*$/.test(name))throw new TypeError('Unsafe SVG ID');return `${prefix}-${name}`;};
}
module.exports={node,serialize,toDOM,group,polygon,line,rect,circle,path,pathPoints,cubicPath,points,matrixString,ids,number};

},
"scenes/01-opening.js": function(module,exports,require){
'use strict';
// S1: exact v03 opening geometry, partitioned into the approved sparse GFX roles.
// Adapted from Framivra candidate-r3 opening.html (MIT, copyright 2026 ruochongliang).
// Shared runtime alone paints B in T07/T08. This module owns material and workshop.
const sceneId = 'S1';
const implemented = true;
const STOCK_RECT = Object.freeze([[-110,-104],[56,-104],[56,86],[-110,86]].map(Object.freeze));
const EMPTY = Object.freeze({defs:Object.freeze([]),world:Object.freeze([]),screen:Object.freeze([])});

function headOffcuts(s) {
  if (!s.offcuts.visible) return [];
  const x=950-20*s.offcuts.t,y=440+260*s.offcuts.t;
  return [[[x+28,y-66],[x+56,y-66],[x+28,y+22]],[[x+112,y-8],[x+112,y+22],[x+84,y+22]]];
}

function resolveSceneState(ctx, {globalFrame, localSceneFrame}) {
  if (globalFrame !== localSceneFrame) throw new RangeError('S1 requires matching global/local frame');
  const opening = ctx.opening.openingState(globalFrame);
  // One solved immutable state supplies every layer, never query/entry or prior-frame data.
  // T06 owns the original offcuts below B. Their resolved silhouettes cut out
  // both shared B pages, preserving the source's offcuts-over-B paint order.
  const bird=opening.offcuts.visible?{...opening.bird,occluders:headOffcuts(opening).map(ps=>ctx.svg.polygon(ps,'black'))}:opening.bird;
  return ctx.math.deepFreeze({cameraMatrix:opening.cameraMatrix,bird,
    mechanics:{opening,recognition:globalFrame>=163&&globalFrame<190,
      contactFrames:{cut:18,body:67,trim:109,eye:150},
      stockBounds:[240,336,406,526],cutCover:[236,opening.presses.cut,410,opening.presses.cut+198],
      bodyImprintOrigin:[650,opening.presses.body+41],eyeTip:[1343,opening.presses.eye]}});
}

// Source Python/HTML used ties-to-even stroke quantization at 1.5 delivery scale.
function roundEven(n) { const a=Math.floor(n),frac=n-a;return frac===.5?a+(a%2):Math.round(n); }
function renderScene(ctx, {globalFrame, localSceneFrame, layerId}) {
  if (globalFrame!==localSceneFrame || globalFrame<0 || globalFrame>=300) throw new RangeError('S1 frame outside [0,300)');
  if (!['T02','T04','T06','T09'].includes(layerId)) return EMPTY;
  const s=ctx.state.mechanics.opening,f=s.f,S=ctx.svg,M=ctx.math,G=ctx.geometry.G.bird,C=ctx.geometry.PALETTE;
  const {paper:PAPER,ink:INK,red:RED,grey:GREY,mid:MID,amber:AMBER,pale:PALE,stock:STOCK}=C;
  const world=[],defs=[];
  const poly=(ps,color,attrs={})=>world.push(S.polygon(ps,color,attrs));
  // Keep rectangle vertices identical to the source oracle, including winding.
  const rect=(x,y,w,h,color)=>poly([[x,y],[x+w,y],[x+w,y+h],[x,y+h]],color);
  const circle=(x,y,r,color)=>world.push(S.circle(x,y,r,color));
  const line=(ps,color=INK,width=3)=>world.push(S.line(ps,color,Math.max(1,roundEven(width*s.camera.z*1.5))/(s.camera.z*1.5)));
  const transformPoints=(points,matrix)=>points.map(p=>M.transform(p,matrix));

  if(layerId==='T02') {
    rect(-450,70,2400,13,GREY);
    for(const x of [120,625,1180,1780]) rect(x,84,9,478,PALE);
    rect(1550,118,270,12,INK);
    [74,105,91].forEach((h,i)=>rect(1570+i*65,118-h,46,h,[RED,INK,AMBER][i]));
    line([[1450,83],[1450,162]],INK,3);
    poly([[1408,162],[1492,162],[1470,189],[1430,189]],MID);
  }

  if(layerId==='T04') {
    // Original red supply reel is not the later Q sound-paper reel.
    circle(-90,432,158,INK);circle(-90,432,124,RED);circle(-90,432,28,PAPER);
    rect(-90,371,460,150,RED);
    rect(-220,340,2160,200,PALE);rect(-220,356,2160,169,PAPER);
    for(let x=-400;x<2000;x+=95){const xx=x+(s.hx-280)%95;line([[xx,531],[xx+38,531]],MID,2);}
    line([[-220,543],[1940,543]],INK,8);line([[-220,570],[1940,570]],MID,4);
    for(let x=-150;x<1950;x+=180){
      circle(x,587,27,INK);circle(x,587,9,MID);
      const a=((f*5+x)%360)*Math.PI/180,xx=Math.cos(a)*20,yy=Math.sin(a)*20;
      line([[x-xx,587-yy],[x+xx,587+yy]],PAPER,3);
    }
    rect(115,615,18,180,INK);rect(1640,615,18,180,INK);
    if(f>=190){
      // The original pale vacated-stock impression remains on the conveyor.
      // This is fixed support material, never a second animated B or red wing.
      const footprint=[G.wing_a,G.wing_b,G.body,G.tail,[[28,22],[56,-66],[112,-66],[112,-47],[140,-33],[112,-25],[112,-8],[84,22]]];
      for(const ps of footprint)poly(ps.map(([x,y])=>[1250+x,440+y]),PALE,{'data-part':'vacated-stock-impression'});
    }
  }

  if(layerId==='T06') {
    if(s.stage===0)poly(transformPoints(STOCK_RECT,M.poseMatrix(s.bird.pose)),RED,{'data-part':'uncut-stock'});
    if(s.waste.visible){
      const w=s.waste,wm=M.poseMatrix({x:w.x,y:w.y,angle:w.angle,scale:1});
      const contours=[STOCK_RECT,G.wing_a,G.wing_b].map(ps=>S.pathPoints(transformPoints(ps,wm),true));
      world.push(S.path(contours.join(' '),RED,{'data-part':'complementary-waste','fill-rule':'evenodd'}));
    }
    if(s.offcuts.visible){
      headOffcuts(s).forEach((ps,i)=>poly(ps,STOCK,{'data-part':i?'head-offcut-b':'head-offcut-a'}));
    }
  }

  if(layerId==='T09') {
    const y=s.presses.cut;
    rect(266,84,20,240,GREY);rect(420,84,20,240,GREY);
    rect(236,y,174,198,INK);rect(240,y+4,166,190,MID);
    for(const tri of [G.wing_a,G.wing_b])line([...tri,tri[0]].map(([x,yy])=>[350+x,y+108+yy]),PAPER,2);
    rect(332,72,20,Math.max(20,y-72),MID);
    rect(566,105,155,76,INK);rect(581,119,125,9,MID);line([[644,181],[644,253]],INK,14);
    const y2=s.presses.body;
    rect(495,y2-15,251,31,INK);rect(632,203,22,Math.max(38,y2-203),MID);
    poly(G.body.map(([x,y])=>[650+x,y2+41+y]),MID);poly(G.tail.map(([x,y])=>[650+x,y2+41+y]),MID);
    const k=s.presses.trim;
    rect(1025,170,18,137,GREY);
    poly([[978,374],[1006,374],[978,462]].map(([x,y])=>[x-30*(1-k),y-100*(1-k)]),MID);
    poly([[1062,432],[1062,462],[1034,462]].map(([x,y])=>[x+30*(1-k),y-100*(1-k)]),MID);
    rect(1312,147,64,74,INK);rect(1328,221,31,38,MID);
    const y4=s.presses.eye;
    rect(1338,251,10,Math.max(4,y4-251),INK);circle(1343,y4,6,INK);
    circle(1740,311,30,INK);poly([[1711,350],[1764,350],[1784,452],[1696,452]],MID);
    line([[1712,450],[1699,539]],INK,13);line([[1766,450],[1783,539]],INK,13);
    line([[1680,543],[1706,543]],INK,10);line([[1774,543],[1800,543]],INK,10);
    line([[1710,366],[1679,394],[1651,369-s.wave]],INK,10);line([[1763,367],[1804,389],[1820,355]],INK,10);
    circle(1820,346,9,RED);line([[1820,355],[1843,473]],INK,4);
    if(s.approach){
      // T09 is above the two B tracks. A nonpainting knockout restores the source
      // foreground draw order. It is semantic ordering, not an E01/E02 mask effect.
      // Apply the inverse common camera only to the screen-space B occluder;
      // runtime still projects all workshop geometry exactly once.
      const c=ctx.state.cameraMatrix,inv=[1/c[0],0,0,1/c[3],-c[4]/c[0],-c[5]/c[3]];
      const cutout=ctx.geometry.silhouette(S.group([
        ...ctx.geometry.birdLocal('wings',s.bird),...ctx.geometry.birdLocal('body',s.bird)
      ],{transform:S.matrixString(M.multiply(inv,M.poseMatrix(s.bird.pose)))}));
      const id=ctx.id('foreground-b-order');
      defs.push(ctx.effectHelpers.mask(id,[-4096,-4096,8192,8192],[cutout],true));
      return M.deepFreeze({defs,world:[S.group(world,{mask:`url(#${id})`,'data-part':'tools-behind-foreground-b'})],screen:[]});
    }
  }
  return M.deepFreeze({defs,world,screen:[]});
}
module.exports=Object.freeze({sceneId,implemented,resolveSceneState,renderScene});

},
"scenes/02-materials.js": function(module,exports,require){
'use strict';
// S2 / R09 + bounded R20 placement staging revision. All state is solved from the global integer frame; no page owns a clock.
// Geometry follows approved R03, with the final tab attached to the actual rotated beak.
const sceneId='S2';
const implemented=true;
const FINAL_PATHS={
 A:{start:[145,460],curves:[[[310,465],[362,342],[548,370]],[[734,398],[766,399],[902,280]]],edge:[913,326],back:[[[773,445],[666,448],[538,416]],[[410,384],[306,509],[150,508]]]},
 L:{start:[160,534],curves:[[[310,529],[490,326],[622,366]],[[754,406],[801,406],[938,338]]],edge:[948,383],back:[[[807,452],[744,430],[615,411]],[[486,392],[321,575],[164,580]]]},
 R:{start:[188,602],curves:[[[376,602],[452,509],[591,446]],[[730,383],[758,322],[935,419]]],edge:[950,465],back:[[[773,368],[715,506],[585,492]],[[455,478],[371,648],[193,649]]]}
};
const NAMES=['A','L','R'];
const INITIAL={A:{x:215,y:445,glyph:[290,420,.4]},L:{x:325,y:515,glyph:[400,503,.55]},R:{x:365,y:585,glyph:[435,605,.55]}};
const FINAL_GLYPHS={A:[270,438,.4],L:[355,500,.55],R:[395,583,.55]};
const TOP_OFFSETS={A:[-90,35],L:[-55,22],R:[-30,12]};
function offsetPath(p,v){const q=a=>[a[0]+v[0],a[1]+v[1]];return {start:q(p.start),curves:p.curves.map(c=>c.map(q)),edge:q(p.edge),back:p.back.map(c=>c.map(q))};}
function ramp(M,f,a,b){return M.smoothstep(M.progress(f,a,b));}
function lerpPoint(M,a,b,u){return [M.mix(a[0],b[0],u),M.mix(a[1],b[1],u)];}
// Fixed two-link manipulator. The bend branch reproduces the neutral elbow
// exactly; no changing bone length is used to maintain a distant contact.
const ARM_LENGTHS=[Math.hypot(42,16),Math.hypot(42,12)];
function solveArm(shoulder,hand){
 const [l1,l2]=ARM_LENGTHS,dx=hand[0]-shoulder[0],dy=hand[1]-shoulder[1],d=Math.hypot(dx,dy);
 if(d>l1+l2+1e-8||d<Math.abs(l1-l2)-1e-8)throw new RangeError('S2 placement is outside fixed arm reach');
 const a=(l1*l1-l2*l2+d*d)/(2*d),h=Math.sqrt(Math.max(0,l1*l1-a*a));
 return {shoulder,elbow:[shoulder[0]+a*dx/d+h*dy/d,shoulder[1]+a*dy/d-h*dx/d],hand};
}
function pathData(S,p){return `M${S.points([p.start])} C${S.points(p.curves[0])} C${S.points(p.curves[1])} L${S.points([p.edge])} C${S.points(p.back[0])} C${S.points(p.back[1])} Z`;}
function solveStrip(M,name,u){
 const q=FINAL_PATHS[name],a=INITIAL[name];
 const start=[a.x,a.y],end=[a.x+170,a.y-9],bottomEnd=[a.x+174,a.y+37],bottomStart=[a.x+4,a.y+46];
 const short={start,curves:[[[a.x+39,a.y+2],[a.x+63,a.y-4],[a.x+94,a.y-3]],[[a.x+126,a.y-2],[a.x+148,a.y-7],end]],edge:bottomEnd,back:[[[a.x+144,a.y+42],[a.x+121,a.y+45],[a.x+94,a.y+43]],[[a.x+62,a.y+43],[a.x+38,a.y+48],bottomStart]]};
 return {start:lerpPoint(M,short.start,q.start,u),curves:q.curves.map((c,i)=>c.map((p,j)=>lerpPoint(M,short.curves[i][j],p,u))),edge:lerpPoint(M,short.edge,q.edge,u),back:q.back.map((c,i)=>c.map((p,j)=>lerpPoint(M,short.back[i][j],p,u)))};
}
function glyph(ctx,name,pose,attrs={}){
 const {svg:S,geometry:G}=ctx,c=G.PALETTE;
 let nodes;
 if(name==='A')nodes=[S.path(G.G.A.path,c.mid)];
 else if(name==='L')nodes=[S.path('M-33 0A33 33 0 0 1 33 0Z',c.amber),S.rect(-3,0,6,88,c.ink),S.circle(0,87,6,c.ink)];
 else nodes=[S.line([[-80,0],[80,0]],c.ink,5),S.line([[-80,18],[80,18]],c.ink,5),...[-55,0,55].map(x=>S.line([[x,-3],[x,21]],c.ink,4))];
 return S.group(nodes,{transform:S.matrixString(ctx.math.poseMatrix({x:pose[0],y:pose[1],scale:pose[2],angle:pose[3]||0})),'data-material-glyph':name,...attrs});
}
function foldedFrame(ctx,state){
 const {svg:S,math:M,geometry:G}=ctx,c=G.PALETTE;
 const nodes=[S.line([[0,36],[9,40],[13,26],[22,30],[26,16],[35,20],[39,6]],c.red,3,{'data-folded-reserve':'F'})];
 for(let i=3;i>=0;i--)nodes.push(S.group([S.polygon(G.G.F.corner_polygon,c.red),S.polygon([[20,16],[30,16],[20,26]],c.paper)],{transform:S.matrixString(M.poseMatrix({x:i*13,y:-i*10,scale:.5,angle:0})),'data-frame-corner':String(i)}));
 return S.group(nodes,{transform:S.matrixString(M.poseMatrix(state.pose)),'data-object':'F-folded'});
}
function sideCard(ctx,name,state){
 const {svg:S,math:M,geometry:G}=ctx,c=G.PALETTE;
 if(name==='A')return S.group([
  S.polygon([[445,315],[628.5,315],[660,445],[445,475]],c.pale,{'data-card-face':'A'}),
  glyph(ctx,'A',[540,389,.58]),
  S.line([[445,315],[445,475]],c.mid,1.5,{'data-hinge':'A-left'})
 ],{transform:S.matrixString(state.matrix),'data-card':'A'});
 return S.group([S.rect(-85,-95,170,160,c.pale),S.rect(-77,-87,154,144,c.paper),glyph(ctx,name,[0,-9,.8]),S.line([[-65,50],[65,50]],c.mid,1.5)],{transform:S.matrixString(state.matrix),'data-card':name});
}
function resolveSceneState(ctx,{globalFrame:f,localSceneFrame}){
 const {math:M,svg:S,geometry:G}=ctx,c=G.PALETTE;
 if(!Number.isInteger(f)||f<300||f>=720||localSceneFrame!==f-300)throw new RangeError('S2 requires global [300,720) and its exact local frame');
 const opening=ctx.opening.openingState(299),cameraU=ramp(M,f,301,319),coverEnd=345;
 const cameraMatrix=opening.cameraMatrix.map((v,i)=>M.mix(v,M.IDENTITY[i],cameraU));
 let bird;
 if(f<coverEnd){
  const z=M.keys(f,[[300,35],[316,32],[326,27],[345,1.1]]),u=ramp(M,f,326,345);
  const p={x:M.mix(640+41*z,885,u),y:M.mix(360+28*z,260,u),scale:z,angle:0};
  bird={space:'screen',pose:p,wing:0,stage:4,feet:false,occluders:[]};
 }else{
  const pose={x:M.keys(f,[[345,885],[365,765],[390,790],[420,790],[450,748],[480,620],[525,460],[555,580],[600,920],[629,960],[630,960],[719,1032]]),y:M.keys(f,[[345,260],[365,465],[390,454],[420,472.2],[450,472.2],[480,480],[525,570],[555,502],[600,365],[629,395],[630,395],[719,306]]),scale:M.keys(f,[[345,1.1],[365,1.5],[390,1.25],[420,1.1],[480,.92],[525,.82],[600,.8],[719,.8]]),angle:M.keys(f,[[345,0],[365,0],[390,-4],[420,0],[450,0],[480,-7],[525,-12],[555,-10],[600,-2],[719,-2]])};
  bird={space:'world',maskBounds:[-256,-256,2048,1536],pose,stage:4,wing:M.keys(f,[[345,-5],[365,0],[378,-12],[420,4],[450,4],[480,-16],[525,10],[555,-15],[600,0],[719,0]]),feet:f>=408&&f<456,occluders:[]};
 }
 if(f===300)bird=opening.bird;
 // A alone rotates about its LEFT physical edge. The same projection applies to
 // every glyph and face point, avoiding independently animated printed symbols.
 const fold=180*ramp(M,f,365,405),r=fold*Math.PI/180,dx=M.keys(f,[[300,0],[435,0],[450,29.75],[465,79.75],[480,139.75]]);
 const aProjection=[Math.cos(r),-.22*Math.sin(r),0,1,445*(1-Math.cos(r))+dx,445*.22*Math.sin(r)];
 const aLeft=M.transform([660,445],aProjection),aPress=M.transform([445,430],aProjection);
 const lx=M.keys(f,[[300,564],[450,564],[465,614],[480,674]]),rx=M.keys(f,[[300,602],[465,602],[480,662]]);
 const cards={
  A:{matrix:aProjection,hinge:[[445+dx,315],[445+dx,475]],angle:fold},
  L:{matrix:M.poseMatrix({x:lx,y:409,scale:1.05,angle:M.keys(f,[[300,-4],[420,-4],[435,0]])})},
  R:{matrix:M.poseMatrix({x:rx,y:421,scale:1.05,angle:M.keys(f,[[300,-7],[420,-7],[435,0]])})}
 };
 // The same near A card passes the lens; no replacement matte. The projection
 // changes while that real card fills the view, then reveals a top-down plane.
 const foreground=f>=480&&f<525;
 const fullCover=[-13,0,0,13,7660,-4775];
 const approach=ramp(M,f,480,491);
 const coverMatrix=aProjection.map((v,i)=>M.mix(v,fullCover[i],approach));
 const edgePath=points=>{
  const [a,b,c,d]=points,ab=lerpPoint(M,a,b,.5),cd=lerpPoint(M,c,d,.5);
  const seg=(p,q)=>[lerpPoint(M,p,q,1/3),lerpPoint(M,p,q,2/3),q];
  return {start:a,curves:[seg(a,ab),seg(ab,b)],edge:c,back:[seg(c,cd),seg(cd,d)]};
 };
 const coverBase=edgePath([[445,315],[628.5,315],[660,445],[445,475]].map(p=>M.transform(p,coverMatrix)));
 const target=offsetPath(solveStrip(M,'A',0),TOP_OFFSETS.A),uncoverU=M.progress(f,505,525),uncover=uncoverU*uncoverU*uncoverU*(uncoverU*(uncoverU*6-15)+10);
 const nearGlyph=[INITIAL.A.glyph[0]+TOP_OFFSETS.A[0],INITIAL.A.glyph[1]+TOP_OFFSETS.A[1],.4];
 const nearCard={path:{start:lerpPoint(M,coverBase.start,target.start,uncover),curves:coverBase.curves.map((p,i)=>p.map((v,j)=>lerpPoint(M,v,target.curves[i][j],uncover))),edge:lerpPoint(M,coverBase.edge,target.edge,uncover),back:coverBase.back.map((p,i)=>p.map((v,j)=>lerpPoint(M,v,target.back[i][j],uncover)))},glyph:[...lerpPoint(M,M.transform([540,389],coverMatrix),nearGlyph,uncover),M.mix(Math.abs(coverMatrix[0])*.58,.4,uncover)]};
 const top=f>=500;
 const pull=ramp(M,f,630,719),deform=ctx.effects.path_deformation?pull:0;
 const strips=Object.fromEntries(NAMES.map((name,index)=>{
  const travel=ramp(M,f,525+index*10,570+index*15),offset=TOP_OFFSETS[name].map(v=>v*(1-travel));
  return [name,{path:offsetPath(solveStrip(M,name,deform),offset),glyph:INITIAL[name].glyph.map((v,i)=>M.mix(v,FINAL_GLYPHS[name][i],pull)+(i<2?offset[i]:0))}];
 }));
 const beak=M.transform([140,-33],M.poseMatrix(bird.pose));
 // The endpoint uses the true rotated beak. R03's rounded comment [1144,280]
 // was not the geometry result of its own -2 degree pose.
 const tabRoot=lerpPoint(M,[938,398],[1042,281],pull);
 const framePose={x:M.keys(f,[[300,1080],[480,1080],[499,1080],[500,1080],[600,1080],[620,931],[629,931],[630,931],[719,1008]]),y:M.keys(f,[[300,512],[480,512],[499,512],[500,562],[600,562],[610,510],[620,429],[629,429],[630,429],[719,322]]),scale:M.keys(f,[[300,.6],[629,.65],[719,.65]]),angle:0};
 const planU=ramp(M,f,500,525),exit=ramp(M,f,630,690);
 let opx=M.keys(f,[[300,1080],[390,1080],[435,180],[480,300],[499,300],[500,1155],[600,1155],[629,1335]]);
 let opy=top?560:430,arm;
 // The operator follows the carried reserve, places it at620, releases, then
 // withdraws behind the actual tray backstop before the fixed T09 out630.
 if(f>=585&&f<600)opx=M.mix(1155,1140,ramp(M,f,585,600));
 if(f>=600&&f<=620){opx=framePose.x+60;opy=M.mix(560,500,ramp(M,f,600,620));}
 if(f>620&&f<630){opx=M.mix(991,1065,ramp(M,f,620,629));opy=500;}
 let hand=[opx+67,opy-61];
 if(f>=420&&f<480)hand=lerpPoint(M,[247,369],aLeft,ramp(M,f,420,435));
 if(f>=585&&f<=600)hand=lerpPoint(M,[opx+67,opy-61],[framePose.x,framePose.y+20*framePose.scale],ramp(M,f,585,600));
 if(f>600&&f<=620)hand=[framePose.x,framePose.y+20*framePose.scale];
 if(f>=585&&f<=620)arm=solveArm([opx-17,opy-57],hand);
 if(f>620&&f<630){
  const contactScale=M.keys(620,[[300,.6],[629,.65],[719,.65]]);
  hand=[opx-60,429+20*contactScale];
  arm=solveArm([opx-17,opy-57],hand);
 }
 // A hinged backstop is attached to the existing F tray. It rises only once
 // the reserve has cleared its left edge, and exits with that same tray.
 const backstop=f>612&&f<690?{x:1000+360*exit,y:585-210*ramp(M,f,612,620),width:155,height:210*ramp(M,f,612,620)}:null;
 // The low guide is the same workshop rail. Side structures leave by movement,
 // not opacity. The fixed-guide endpoints arrive at the approved f719 target.
 const guide={x:M.mix(100,120,planU),y:M.mix(525,666,planU),width:M.mix(1070,1080,planU),top:planU};
 const supportReveal=ramp(M,f,690,719);
 const carrier={matrix:[1.1,0,0,1.1,932,261+(1-supportReveal)*-520],root:[932,261,1.1],rect:G.G['P-C'].local_carrier_rect,receivers:G.G['P-C'].receiver_rectangles,peg:[1042,281]};
 const foldPlanes={hinge:[[-110,124],[140,124]],a:[[-110,124],[140,124],[132,132],[-102,132]],b:[[-102,132],[132,132],[124,140],[-94,140]],rearHinge:[[-102,132],[132,132]]};
 // Whole-body/whole-wing visibility consumes this one resolved geometry. The
 // foreground card is ABOVE both B pages; use identical occluders in each.
 if(foreground){
  const cardFace=S.path(pathData(S,nearCard.path),'black');
  bird={...bird,occluders:[cardFace]};
 }
 return M.deepFreeze({cameraMatrix,bird,mechanics:{cards,foldAngle:fold,wingCardContact:[628.5,315],actualWingTip:M.transform(M.transform([-91,-100],M.rotate(bird.wing,[-15,-6])),M.poseMatrix(bird.pose)),operator:{x:opx,y:opy,hand,...(arm?{arm}:{})},...(backstop?{backstop}:{}),push:{handContact:aLeft,aToL:aPress,lToR:[600,462],lStarted:f>450,rStarted:f>465},top,foreground,coverMatrix,nearCard,strips,pull,beak,tabRoot,grasp:f>=630,frame:{pose:framePose,cornerIds:[0,1,2,3],inTray:f<600},guide,carrier,foldPlanes,planU,exit,supportReveal},handoff:{cardOwner:f<630?'T10':'T11',ribbonOrder:['A','L','R'],singleGrasp:f>=630}});
}
function farWorkshop(ctx,m){
 const {svg:S,math:M,geometry:G}=ctx,c=G.PALETTE;
 // It physically passes upward during the overhead reveal and leaves its window
 // before T02 ends. No opaque ground and no crossfade to a new title card.
 const y=-850*m.planU;
 return S.group([S.rect(82,132,1110,8,c.grey),...[140,430,832,1138].map(x=>S.rect(x,140,6,335,c.pale)),S.rect(980,235,185,7,c.ink),S.rect(1000,183,30,52,c.red),S.rect(1049,165,30,70,c.ink),S.rect(1098,194,30,41,c.amber),S.line([[185,140],[185,203]],c.ink,2),S.polygon([[161,203],[209,203],[198,220],[172,220]],c.mid)],{transform:S.matrixString(M.translate(0,y)),'data-object':'far-workshop'});
}
function transport(ctx,m){
 const {svg:S,math:M,geometry:G}=ctx,c=G.PALETTE,g=m.guide,u=g.top,parts=[];
 const sideY=(1-u)*0+u*440;
 parts.push(S.group([S.rect(100,478,1070,47,c.pale),S.rect(100,485,1070,34,c.paper),S.rect(175,580,10,90,c.ink),S.rect(1070,580,10,90,c.ink),...Array.from({length:11},(_,i)=>{let x=125+i*100;return S.group([S.circle(x,562,15,c.ink),S.circle(x,562,5,c.mid),S.line([[x-10,559],[x+10,565]],c.paper,2)]);})],{transform:S.matrixString(M.translate(0,sideY)),'data-object':'conveyor-side'}));
 parts.push(S.line([[g.x,g.y],[g.x+g.width,g.y]],u===1?c.mid:c.ink,M.mix(5,3,u)),S.line([[g.x,g.y+M.mix(17,13,u)],[g.x+g.width,g.y+M.mix(17,13,u)]],u===1?c.grey:c.mid,3));
 for(const t of [.15,.4,.65,.9])parts.push(S.line([[g.x+g.width*t,g.y+13],[g.x+g.width*t,g.y+25],[g.x+g.width*t+13,g.y+25]],c.mid,2));
 if(m.top){
  parts.push(S.rect(171,160,9,400,c.pale,{'data-object':'fixed-upright'}));
  const slotShift=-1200*m.exit;
  for(let i=0;i<3;i++){let x=170+i*62+slotShift,y=490+i*66;
   parts.push(S.line([[x-45,y+17],[x+245,y-31]],c.grey,1.5,{'data-paper-slot':NAMES[i]}),S.line([[x-43,y+24],[x+247,y-24]],c.grey,1.5));
  }
 }
 const trayY=M.mix(535,585,m.planU),trayX=1000+360*m.exit;
 if(m.backstop){const b=m.backstop;parts.push(S.rect(b.x,b.y,b.width,b.height,c.pale,{'data-object':'F-tray-backstop'}),S.line([[b.x,b.y],[b.x+b.width,b.y],[b.x+b.width,trayY]],c.grey,1.5),S.line([[b.x,trayY],[b.x+b.width,trayY]],c.mid,2,{'data-hinge':'tray-backstop'}));}
 parts.push(S.rect(trayX,trayY,155,9,c.mid,{'data-object':'F-reserve-tray'}),S.line([[trayX, trayY],[trayX,trayY-15],[trayX+155,trayY-15],[trayX+155,trayY]],c.grey,2));
 return S.group(parts,{'data-object':'W-guide-and-transport'});
}
function operator(ctx,m){
 const {svg:S,math:M,geometry:G}=ctx,c=G.PALETTE,o=m.operator;
 const shoulders=o.arm?o.arm.shoulder:[o.x-17,o.y-57],elbow=o.arm?o.arm.elbow:[M.mix(shoulders[0],o.hand[0],.5),Math.min(shoulders[1],o.hand[1])-12];
 return S.group([S.circle(o.x,o.y-94.5,18.9,c.ink),S.polygon([[o.x-18.9,o.y-69.3],[o.x+18.9,o.y-69.3],[o.x+30.6,o.y-3.6],[o.x-27.9,o.y-3.6]],c.mid),S.line([[o.x-17,o.y-2.7],[o.x-26.1,o.y+54.9],[o.x-42.3,o.y+54.9]],c.ink,8.1),S.line([[o.x+19.8,o.y-2.7],[o.x+27.9,o.y+54.9],[o.x+44.1,o.y+54.9]],c.ink,8.1),S.line([shoulders,elbow,o.hand],c.ink,6.3),S.circle(...o.hand,3.5,c.ink,{'data-contact':'operator-hand'}),S.line([[o.x+19,o.y-57],[o.x+45,o.y-31],[o.x+67,o.y-61]],c.ink,6.3)],{'data-object':'operator'});
}
function ribbonShapes(ctx,m,{omitA=false}={}){
 const {svg:S,math:M,geometry:G,effectHelpers:E,effects}=ctx,c=G.PALETTE,defs=[],nodes=[];
 const paths=Object.fromEntries(NAMES.map(n=>[n,pathData(S,m.strips[n].path)]));
 const u=m.pull;
 // These bounded underpasses become active as the actual strip paths cross.
 const regionA=S.path('M546 365Q590 355 640 371L640 419Q590 401 546 414Z','black');
 const regionR=S.path('M675 392Q710 382 745 385L745 428Q710 432 675 438Z','black');
 const aMask=ctx.id('A-under-L'),rMask=ctx.id('R-under-L'),rClip=ctx.id('R-ink-clip'),rOver=ctx.id('R-over-L');
 const weave=M.smoothstep(M.clamp01((u-.45)/.55)),maskActive=weave>0;
 const maskA=S.group([regionA],{transform:S.matrixString([weave,0,0,1,590*(1-weave),0])}),maskR=S.group([regionR],{transform:S.matrixString([weave,0,0,1,710*(1-weave),0])});
 defs.push(E.mask(aMask,[-20,-20,1320,760],maskActive?[maskA]:[],effects.material_masks),E.mask(rMask,[-20,-20,1320,760],maskActive?[maskR]:[],effects.material_masks),S.node('clipPath',{id:rClip,clipPathUnits:'userSpaceOnUse'},[S.path(paths.R,'white')]),S.node('clipPath',{id:rOver,clipPathUnits:'userSpaceOnUse'},[S.rect(802.5-37.5*weave,340,75*weave,160,'white')]));
 nodes.push(S.path(paths.R,c.stock,{'data-strip':'R'}),S.path(paths.L,c.paper,{stroke:c.mid,'stroke-width':1.5,'data-strip':'L'}));
 if(!omitA)nodes.push(S.path(paths.A,c.pale,{mask:`url(#${aMask})`,'data-strip':'A'}));
 if(maskActive)nodes.push(S.path(paths.R,c.stock,{'clip-path':`url(#${rOver})`,'data-overpass':'R'}));
 // R's two printed rails follow its carrier, but its rigid identity glyph does
 // not stretch. Both curved lines use exactly the carrier's solved projection.
 const r=m.strips.R.path;
 const ink=[13,28].map(offset=>{
  // Apply the same small printed-rail offsets to the current solved carrier.
  // Interpolating toward the final absolute path a second time would pull ink
  // ahead of its paper during the extension and is intentionally avoided.
  const p={start:[r.start[0]+2,r.start[1]+offset],curves:[
   r.curves[0].map((v,i)=>[v[0]+[1,3,0][i],v[1]+offset+[0,1,1][i]]),
   r.curves[1].map((v,i)=>[v[0]+[-3,5,8][i],v[1]+offset+1])
  ]};
  return S.path(`M${S.points([p.start])} C${S.points(p.curves[0])} C${S.points(p.curves[1])}`,'none',{stroke:c.ink,'stroke-width':3});
 });
 nodes.push(S.group(ink,{'clip-path':`url(#${rClip})`,mask:`url(#${rMask})`,'data-object':'R-printed-rails'}));
 for(const name of NAMES){
  if(omitA&&name==='A')continue;
  const p=m.strips[name].path.start,q=lerpPoint(M,[p[0]+46,p[1]+4],{A:[191,464],L:[206,540],R:[239,608]}[name],u);
  nodes.push(S.line([q,[q[0]+21,q[1]+20],[q[0]+42,q[1]],[q[0]+64,q[1]+20]],c.mid,1.5,{'data-fold-reserve':name}));
  nodes.push(glyph(ctx,name,m.strips[name].glyph));
 }
 if(maskActive){
  const id1=ctx.id('seam-A'),id2=ctx.id('seam-R');
  defs.push(E.seamGradient({id:id1,bounds:[515,413,105,55],enabled:effects.gradient_alpha_reveal}),E.seamGradient({id:id2,bounds:[620,462,130,48],enabled:effects.gradient_alpha_reveal}));
  nodes.push(S.path('M515 413C561 415 588 446 617 449L618 468C582 463 556 436 515 433Z',`url(#${id1})`,{opacity:weave}),S.path('M620 487C672 481 711 477 741 462L747 481C709 496 676 503 622 506Z',`url(#${id2})`,{opacity:weave}));
 }
 if(m.top){
  const tabEnd=m.grasp?m.beak:[1071.0104259131926,364.70733853601575];
  for(const name of ['A','R','L']){if(omitA&&name==='A')continue;
   const e=m.strips[name].path.edge;
   nodes.push(S.line([[e[0]-11,e[1]-23],m.tabRoot],c.stock,name==='L'?4:3,{'data-object':`${name}-paper-tongue`}));
  }
  nodes.push(S.line([m.tabRoot,tabEnd],c.stock,4,{'data-contact':'single-beak-grasp','data-grasped':m.grasp?'true':'false'}));
 }
 return {defs,nodes};
}
function foregroundBirdMask(ctx,nodes,defs){
 const {svg:S,math:M,geometry:G,effectHelpers:E,state}=ctx;
 const id=ctx.id('foreground-B-knockout');
 const b=state.bird,shape=S.group([...G.birdLocal('wings',b),...G.birdLocal('body',b)].map(G.silhouette),{transform:S.matrixString(M.poseMatrix(b.pose))});
 defs.push(E.structuralMask(id,b.maskBounds||[-4096,-4096,8192,8192],[shape]));
 return [S.group(nodes,{mask:`url(#${id})`})];
}
function renderScene(ctx,{globalFrame:f,localSceneFrame,layerId}){
 const {svg:S,math:M,geometry:G,state}=ctx,c=G.PALETTE,m=state.mechanics,defs=[],world=[],screen=[];
 if(layerId==='T02'&&f<690)world.push(farWorkshop(ctx,m));
 if(layerId==='T04')world.push(transport(ctx,m));
 if(layerId==='T05'&&f>=330){
  if(f>=620){const p=m.frame.pose;world.push(S.line([m.tabRoot,[p.x,p.y+36*p.scale]],c.stock,3,{'data-object':'F-paper-tongue'}));}
  world.push(foldedFrame(ctx,m.frame));
 }
 if(layerId==='T03'&&f>=690){
  const q=m.carrier;
  world.push(S.group([S.rect(...q.rect,c.stock,{'data-object':'P-C'}),...q.receivers.map((r,i)=>S.rect(...r,c.ink,{'data-receiver':String(i)})),S.circle(-108,118,4,c.mid,{'data-object':'P-C-bearing'})],{transform:S.matrixString(q.matrix),'data-assembly':'P-C-receivers'}));
 }
 if(layerId==='T06'&&f>=690){
  world.push(S.group([S.polygon(m.foldPlanes.a,c.pale,{'data-object':'P-A'}),S.polygon(m.foldPlanes.b,c.pale,{'data-object':'P-B'}),S.line(m.foldPlanes.hinge,c.mid,1,{'data-hinge':'P-C-P-A'}),S.line(m.foldPlanes.rearHinge,c.mid,1,{'data-hinge':'P-A-P-B'})],{transform:S.matrixString(m.carrier.matrix)}));
 }
 if(layerId==='T09'&&f<630){
  let actor=operator(ctx,m);
  if(m.backstop){
   const b=m.backstop,id=ctx.id('operator-behind-tray');
   // Structural mask is the exact filled tray plane, always opaque black.
   // Optional material-mask diagnostics must not reveal a body through a wall.
   defs.push(ctx.effectHelpers.structuralMask(id,[-256,-256,2048,1536],[S.rect(b.x,b.y,b.width,b.height,'black')]));
   actor=S.group([actor],{mask:`url(#${id})`,'data-occlusion':'F-tray-backstop'});
  }
  world.push(...foregroundBirdMask(ctx,[actor],defs));
 }
 if(layerId==='T10'&&f>=330&&f<630){
  if(!m.top){
   let cardNodes=[sideCard(ctx,'R',m.cards.R),sideCard(ctx,'L',m.cards.L)];
   if(!m.foreground)cardNodes.push(sideCard(ctx,'A',m.cards.A));
   world.push(...foregroundBirdMask(ctx,cardNodes,defs));
  }else{const ribbon=ribbonShapes(ctx,m,{omitA:m.foreground});defs.push(...ribbon.defs);world.push(...foregroundBirdMask(ctx,ribbon.nodes,defs));}
  if(m.foreground){
   // A single card owns both faces. No duplicate A symbol in the top view until
   // the near card has left the lens and its same head rejoins the carrier.
   const face=S.group([S.path(pathData(S,m.nearCard.path),c.pale,{'data-card-face':'A'}),glyph(ctx,'A',m.nearCard.glyph)],{'data-card':'A','data-projection':'near-card'});
   world.push(face);
  }
 }
 if(layerId==='T11'&&f>=630){const ribbon=ribbonShapes(ctx,m);defs.push(...ribbon.defs);world.push(...foregroundBirdMask(ctx,ribbon.nodes,defs));}
 return M.deepFreeze({defs,world,screen});
}
module.exports=Object.freeze({sceneId,implemented,resolveSceneState,renderScene});

},
"scenes/03-time-ribbons.js": function(module,exports,require){
'use strict';
/** S3 / R10: actual frame-pure SVG geometry. R06 core is read-only.
 * Production remains fail-closed: approved source typography and final timeline
 * readback are absent. `implemented` may only change at the integrated readiness gate.
 */
const sceneId='S3';
const implemented=false;
const geometryImplemented=true;
const readiness=Object.freeze({geometry:'AUTHOR_IMPLEMENTED_UNREVIEWED', typography:'CANDIDATE_PATH_PROVIDER_PENDING_INDEPENDENT_REVIEW', sourceBinding:'BLUEPRINT_ONLY', browserPixels:'NOT_RUN', fullFilm:'NOT_RUN'});
const OWNERS=Object.freeze({A:810,R:930,L:1005});
const INITIAL=Object.freeze({A:{x:270,y:438,scale:.4,angle:0},L:{x:355,y:500,scale:.55,angle:0},R:{x:395,y:583,scale:.55,angle:0}});
const STRIPS=Object.freeze({
 A:[[145,460],[310,465],[362,342],[548,370],[734,398],[766,399],[902,280],[913,326],[773,445],[666,448],[538,416],[410,384],[306,509],[150,508]],
 L:[[160,534],[310,529],[490,326],[622,366],[754,406],[801,406],[938,338],[948,383],[807,452],[744,430],[615,411],[486,392],[321,575],[164,580]],
 R:[[188,602],[376,602],[452,509],[591,446],[730,383],[758,322],[935,419],[950,465],[773,368],[715,506],[585,492],[455,478],[371,648],[193,649]]
});
const clamp=(M,f,a,b)=>M.smoothstep(M.progress(f,a,b));
function poseMix(M,a,b,u){return Object.fromEntries(['x','y','scale','angle'].map(k=>[k,M.mix(a[k],b[k],u)]));}
function cubicOutline(S,p){
 return `M${S.points([p[0]])} C${S.points(p.slice(1,4))} C${S.points(p.slice(4,7))} L${S.points([p[7]])} C${S.points(p.slice(8,11))} C${S.points(p.slice(11,14))} Z`;
}
function localPose(M,matrix){return {x:matrix[4],y:matrix[5],scale:Math.hypot(matrix[0],matrix[1]),angle:Math.atan2(matrix[1],matrix[0])*180/Math.PI};}
function resolveSceneState(ctx,{globalFrame:f,localSceneFrame}){
 const M=ctx.math,S=ctx.svg,G=ctx.geometry;
 if(!Number.isInteger(f)||f<720||f>=1200||localSceneFrame!==f-720)throw new RangeError('S3 requires exact global [720,1200) and matching scene frame');
 const side=clamp(M,f,1140,1198), pan=clamp(M,f,1140,1198);
 const cameraMatrix=[1,0,0,1,-20*pan,-16*pan];
 const gatePose={x:M.mix(932,750,side),y:M.mix(261,386,side),scale:M.mix(1.1,1.8,side),angle:0};
 const gateMatrix=M.poseMatrix(gatePose),carrier=G.carrierAssembly(gateMatrix);
 // One rigid A, tilted around its retained left foot. The right foot stays clear
 // until the R locking operation. All three symbols use conformal transforms only.
 const tilt=M.keys(f,[[720,0],[810,-8],[930,-8],[945,0],[1199,0]]);
 const aSeated=M.multiply(gateMatrix,M.rotate(tilt,[-66,110]));
 const aTarget=localPose(M,aSeated);
 const aApproach=clamp(M,f,780,855);
 const aPose=poseMix(M,INITIAL.A,aTarget,aApproach);
 aPose.y-=90*Math.sin(Math.PI*aApproach);
 const rSeat=M.transform([0,116],gateMatrix),rTarget={x:rSeat[0],y:rSeat[1]+(1-clamp(M,f,930,945))*20,scale:gatePose.scale*1.35,angle:0};
 const rPose=poseMix(M,INITIAL.R,rTarget,clamp(M,f,900,945));
 const lPoint=M.transform([0,M.mix(-80,-118,clamp(M,f,1005,1049))],gateMatrix);
 const lPose=poseMix(M,INITIAL.L,{x:lPoint[0],y:lPoint[1],scale:gatePose.scale*.68,angle:0},clamp(M,f,960,1005));
 const glyphs={A:{owner:f<810?'T11':'T03',pose:aPose,matrix:M.poseMatrix(aPose)},R:{owner:f<930?'T11':'T03',pose:rPose,matrix:M.poseMatrix(rPose)},L:{owner:f<1005?'T11':'T03',pose:lPose,matrix:M.poseMatrix(lPose)}};
 const Fstart={x:1008,y:322,scale:.65,angle:0};
 const FhangPoint=M.transform([80/1.8,(201-386)/1.8],gateMatrix);
 const Fhang={x:FhangPoint[0],y:FhangPoint[1],scale:.65,angle:0};
 const Fpose=poseMix(M,Fstart,Fhang,clamp(M,f,1070,1100));
 const Fmatrix=M.poseMatrix(Fpose),Fend=M.transform([0,36],Fmatrix);
 const gateAnchorLocal=[80/1.8,-15-Math.sqrt(80*80-(80/1.8)**2)];
 const FgateAnchor=M.transform(gateAnchorLocal,gateMatrix);
 const baseBird={x:1032,y:306,scale:.8,angle:-2};
 const beakOffset=M.transform([140,-33],M.multiply(M.rotate(-2),M.scale(.8)));
 const pinLocal=[M.mix(100,101,side),M.mix(20/1.1,112,side)],pin=M.transform(pinLocal,gateMatrix);
 let birdPose={...baseBird};
 if(f<=795)birdPose=poseMix(M,baseBird,{...baseBird,x:pin[0]-beakOffset[0],y:pin[1]-beakOffset[1]},clamp(M,f,750,795));
 else if(f<855)birdPose=poseMix(M,{...baseBird,x:pin[0]-beakOffset[0],y:pin[1]-beakOffset[1]},baseBird,clamp(M,f,795,855));
 else if(f>=1050&&f<1070)birdPose=poseMix(M,baseBird,{...baseBird,x:Fstart.x-beakOffset[0],y:Fstart.y-beakOffset[1]},clamp(M,f,1050,1070));
 else if(f>=1070&&f<=1100)birdPose={...baseBird,x:Fpose.x-beakOffset[0],y:Fpose.y-beakOffset[1]};
 else if(f>1100){
  const prior={...baseBird,x:Fhang.x-beakOffset[0],y:Fhang.y-beakOffset[1]};
  const end={x:420,y:506,scale:.9,angle:0};
  birdPose=poseMix(M,prior,end,clamp(M,f,1100,1198));
  birdPose.y-=65*Math.sin(Math.PI*clamp(M,f,1100,1198));
 }
 const birdBeak=M.transform([140,-33],M.poseMatrix(birdPose));
 // A complete shared visibility region, never a wing-only correction.
 const occluders=f>=1140&&f<1185?[S.group([S.rect(52,-12,28,122,G.PALETTE.mid)],{transform:S.matrixString(gateMatrix)})]:[];
 const bird={pose:birdPose,space:'world',stage:4,wing:M.keys(f,[[720,0],[795,0],[825,-6],[855,0],[1049,0],[1070,-4],[1100,0],[1145,-8],[1180,0],[1199,0]]),feet:f>=1188,occluders,maskBounds:[-256,-256,2048,1536]};
 const pull=M.keys(f,[[720,0],[795,1],[879,0],[945,.8],[1049,0],[1100,.6],[1140,.15],[1199,.15]]);
 const wave=M.keys(f,[[720,0],[810,-1],[879,0],[930,1],[1005,-.6],[1049,0],[1120,.8],[1140,.2],[1199,.2]]);
 const strips={};
 for(const [index,name] of ['A','L','R'].entries()){
  const p=STRIPS[name].map(([x,y],i)=>{
   // Paired top/bottom controls share offsets; material width is not stretched.
   const rank=({0:0,1:1,2:2,3:3,4:4,5:5,6:6,7:6,8:5,9:4,10:3,11:2,12:1,13:0})[i];
   const envelope=Math.sin(rank*Math.PI/6);
   return [x+pull*envelope*(20-index*5),y+envelope*(wave*(12+index*4)+pull*(-18+index*10))];
  });
  strips[name]={points:ctx.effectHelpers.deform(ctx.effects.path_deformation,p,STRIPS[name]),rigidPoints:STRIPS[name]};
 }
 const AfootLeft=M.transform([-66,110],glyphs.A.matrix),AfootRight=M.transform([66,110],glyphs.A.matrix);
 const seats=[M.transform([-66,110],gateMatrix),M.transform([66,110],gateMatrix)];
 const barAngle=M.keys(f,[[720,0],[1005,0],[1049,-36.7],[1140,-36.7],[1198,0],[1199,0]]);
 const barStart=[-63,-9],barEnd=M.transform([47,-9],M.rotate(barAngle,barStart));
 const markerProgress=clamp(M,f,810,945),markerX=M.mix(230,862.7,markerProgress);
 const markerFree=[[markerX,447],[markerX,568]], markerFinal=[M.transform(barStart,gateMatrix),M.transform([47,-9],gateMatrix)];
 const marker=markerFree.map((p,i)=>p.map((v,j)=>M.mix(v,markerFinal[i][j],clamp(M,f,915,945))));
 return M.deepFreeze({cameraMatrix,bird,mechanics:{gatePose,gateMatrix,carrier,glyphs,strips,pin,pinLocal,tabEnd:f<=795?birdBeak:pin,birdBeak,F:{pose:Fpose,matrix:Fmatrix,lowerEndpoint:Fend,gateAnchor:FgateAnchor,hung:f>=1100,grasped:f>=1070&&f<=1100},Afeet:[AfootLeft,AfootRight],seats,rightFootGap:seats[1][1]-AfootRight[1],bar:{angle:barAngle,start:barStart,end:barEnd,length:110},marker,stripRetraction:clamp(M,f,1140,1198),side,sourceGuide:{points:[[310,461],[365,447+(ctx.effects.path_deformation?wave*7:0)],[415,430+(ctx.effects.path_deformation?wave*4:0)],[475,411]],flatProgress:clamp(M,f,1020,1050)},sourceReadiness:readiness,guide:{x:120,y:666,width:1080}}});
}
function glyphLocal(ctx,name){
 const {svg:S,geometry:G}=ctx,c=G.PALETTE;let shapes;
 if(name==='A')shapes=[S.path(G.G.A.path,c.mid)];
 if(name==='L')shapes=[S.path('M-33 0A33 33 0 0 1 33 0Z',c.amber),S.rect(-3,0,6,88,c.ink),S.circle(0,87,6,c.ink)];
 if(name==='R')shapes=[...G.G.R.double_rail_y.map(y=>S.line([[-80,y],[80,y]],c.ink,5)),...G.G.R.crossbar_x.map(x=>S.line([[x,-3],[x,21]],c.ink,4))];
 return shapes;
}
function glyph(ctx,name,matrix){
 const S=ctx.svg;return S.group(glyphLocal(ctx,name),{transform:S.matrixString(matrix),'data-object':name,'data-glyph-geometry':'approved-r03','data-owner':ctx.entry.layerId});
}
function foldedFrame(ctx,matrix){
 const {svg:S,math:M,geometry:G}=ctx,c=G.PALETTE;
 const corners=[3,2,1,0].map(i=>S.group([S.polygon(G.G.F.corner_polygon,c.red),S.polygon([[20,16],[30,16],[20,26]],c.paper)],{transform:S.matrixString(M.multiply(M.translate(i*13,-i*10),M.scale(.5))),'data-corner':`F-${i}`}));
 return S.group([S.line([[0,36],[9,40],[13,26],[22,30],[26,16],[35,20],[39,6]],c.red,3),...corners],{transform:S.matrixString(matrix),'data-object':'F','data-owner':'T05'});
}
function platform(ctx,x,y){const S=ctx.svg,c=ctx.geometry.PALETTE;return [S.rect(x-90,y,180,12,c.stock),S.line([[x-75,y+12],[x-50,y+100]],c.ink,8),S.line([[x+75,y+12],[x+50,y+100]],c.ink,8)];}
function reel(ctx,x,y,z){
 const S=ctx.svg,c=ctx.geometry.PALETTE;
 return S.group([S.circle(0,0,77,c.ink),S.circle(0,0,68,c.stock),S.circle(0,0,48,c.paper),S.circle(0,0,36,c.pale),S.path('M-59-22A63 63 0 0 1 52-33 M-47 36A57 57 0 0 0 46 37','none',{stroke:c.mid,'stroke-width':2}),S.circle(0,0,12,c.ink)],{transform:S.matrixString(ctx.math.poseMatrix({x,y,scale:z,angle:0})),'data-object':'Q','data-state':'locked'});
}

// Integrator-owned dependency; this author module neither fetches fonts nor uses
// host text metrics. Glyph outlines stay rigid while their baselines follow the
// cubic's measured arc. Candidate bytes do not imply font/source approval.
const sourceGlyphDependency=Object.freeze({api:'ctx.assets.fonts.sourceLabels',format:'framivra-glyph-paths/1',assetSha256:'2acef4fe4abb53af646c4bfb02a9bd35855cbec460fc537d4c6e218dd18ab7a0',fontSha256:'6b692c4b6d15ccf59f1c1fe8d11cb8a92f51960f3e9f1f523781755a3af7e29f',licenseSha256:'075f91382875da1c4b491f1facf2586f55448705133264d98ce15cab6211b125'});
function sourceGlyphs(ctx,provider,text,curve,size,offsetY){
 const {svg:S,math:M,geometry:G}=ctx;
 if(provider.format!==sourceGlyphDependency.format||!Number.isFinite(provider.unitsPerEm)||provider.unitsPerEm<=0||provider.provenance?.fontSha256!==sourceGlyphDependency.fontSha256||provider.provenance?.licenseSha256!==sourceGlyphDependency.licenseSha256)throw new Error('S3_SOURCE_GLYPH_PROVIDER_IDENTITY_MISMATCH');
 const scale=size/provider.unitsPerEm,table=[{t:0,p:M.cubic(curve,0),s:0}];
 for(let i=1;i<=128;i++){const p=M.cubic(curve,i/128),prev=table[i-1];table.push({t:i/128,p,s:prev.s+Math.hypot(p[0]-prev.p[0],p[1]-prev.p[1])});}
 let distance=0;const nodes=[];
 for(const char of text){
  const glyph=provider.glyphs?.[char];
  if(!glyph||!Number.isFinite(glyph.advance)||glyph.advance<=0||typeof glyph.path!=='string'||(char!==' '&&(!glyph.path||!Array.isArray(glyph.bounds)||glyph.bounds.length!==4||!glyph.bounds.every(Number.isFinite))))throw new Error('S3_SOURCE_GLYPH_MISSING:'+char);
  const advance=glyph.advance*scale,center=distance+advance/2;
  if(distance+advance>table[128].s)throw new Error('S3_SOURCE_LABEL_EXCEEDS_CURVE');
  let i=1;while(table[i].s<center)i++;
  const a=table[i-1],b=table[i],t=M.mix(a.t,b.t,(center-a.s)/(b.s-a.s)),p=M.cubic(curve,t),v=1-t;
  const tangent=[0,1].map(j=>3*v*v*(curve[1][j]-curve[0][j])+6*v*t*(curve[2][j]-curve[1][j])+3*t*t*(curve[3][j]-curve[2][j]));
  const angle=Math.atan2(tangent[1],tangent[0])*180/Math.PI;
  const matrix=M.multiply(M.translate(p[0],p[1]+offsetY),M.multiply(M.rotate(angle),M.multiply(M.translate(-advance/2,0),[scale,0,0,-scale,0,0])));
  if(glyph.path)nodes.push(S.path(glyph.path,G.PALETTE.ink,{transform:S.matrixString(matrix),'data-source-glyph':char,'data-glyph-bounds':glyph.bounds.join(',')}));
  distance+=advance;
 }
 return S.group(nodes,{'data-source-label':text,'data-glyph-provider':provider.family,'data-glyph-review':provider.reviewStatus||'UNKNOWN'});
}

function renderScene(ctx,{globalFrame:f,localSceneFrame,layerId}){
 if(f<720||f>=1200||localSceneFrame!==f-720)throw new RangeError('Invalid S3 render frame');
 const {svg:S,math:M,geometry:G,effectHelpers:E}=ctx,c=G.PALETTE,k=ctx.state.mechanics;
 const out={defs:[],world:[],screen:[]},id=ctx.id;
 if(layerId==='T07'||layerId==='T08')return out; // mandatory shared bird dispatcher
 if(layerId==='T03'){
  const nodes=[S.rect(...G.G['P-C'].local_carrier_rect,c.stock,{'data-object':'P-C'}),...G.G['P-C'].receiver_rectangles.map((r,i)=>S.rect(...r,c.ink,{'data-object':`receiver-${i}`})),S.circle(-108,118,4,c.mid)];
  out.world.push(S.group(nodes,{transform:S.matrixString(k.gateMatrix),'data-owner':'T03','data-assembly':'G-P-C'}));
  for(const name of ['A','R','L'])if(k.glyphs[name].owner==='T03')out.world.push(glyph(ctx,name,k.glyphs[name].matrix));
  if(f>=795)out.world.push(S.group([S.line([[100,110],k.pinLocal],c.ink,2),S.circle(...k.pinLocal,8/1.1,c.ink)],{transform:S.matrixString(k.gateMatrix),'data-object':'receiver-pin-mount'}));
  if(f>=1100)out.world.push(S.circle(...k.F.gateAnchor,3*k.gatePose.scale,c.ink,{'data-object':'F-hang-pin'}));
  if(f>=945){
   const endpoint=k.bar.end;
   const bar=[S.line([k.bar.start,endpoint],c.ink,7),S.circle(-63,-9,7,c.ink),S.line([endpoint,[72,35],[101,112]],c.ink,2),S.circle(101,112,15,c.ink),S.circle(106,108,4,c.amber)];
   out.world.push(S.group(bar,{transform:S.matrixString(k.gateMatrix),'data-object':'gate-bar-linkage','data-rigid-bar-length':110}));
  }

 }
 if(layerId==='T04'){
  if(f<1198)out.world.push(S.rect(171,160+720*k.side,9,400,c.pale,{'data-object':'W-ribbon-upright'}));
  const guide=k.guide;
  out.world.push(S.group([S.line([[guide.x,guide.y],[guide.x+guide.width,guide.y]],c.mid,3),S.line([[guide.x,guide.y+13],[guide.x+guide.width,guide.y+13]],c.grey,3),...[.15,.4,.65,.9].map(u=>S.line([[guide.x+guide.width*u,guide.y+13],[guide.x+guide.width*u,guide.y+25],[guide.x+guide.width*u+13,guide.y+25]],c.mid,2))],{'data-object':'W-guide','data-fixed-world':'true'}));
  if(f>=1140){
   const enter=clamp(M,f,1140,1198),left=M.mix(-220,350,enter),right=M.mix(1490,1060,enter),qx=M.mix(1490,1145,enter);
   out.world.push(S.group(platform(ctx,left,549.2),{'data-object':'origin-support'}),S.group(platform(ctx,right,506),{'data-object':'landing-support'}));
   const dx=right-1060;
   out.world.push(S.polygon([[970+dx,502.36],[1150+dx,502.36],[1150+dx,518],[970+dx,518]],c.stock,{'data-object':'landing-pressure-plate'}),S.line([[970+dx,502.36],[1150+dx,502.36]],c.mid,1.5),S.line([[1014+dx,502.36],[1014+dx,502.36],[1030+dx,506],[1097+dx,467]],c.ink,3,{'data-object':'Q-release-lever'}),S.circle(1030+dx,506,4,c.ink),S.line([[1097+dx,467],[1097+dx,467]],c.ink,2,{'data-object':'Q-stop-link'}),reel(ctx,qx,440,.72),S.line([[qx-47.52,496.16],[qx+47.52,496.16]],c.ink,7,{'data-object':'Q-shelf'}),S.polygon([[1097+dx,467],[1082.6+dx,490.76],[1106.36+dx,490.76]],c.pale,{'data-object':'Q-stop'}));
  }
 }
 if(layerId==='T06'){
  // P-C owns the common edge. These narrow folded reserves remain present before
  // the later S5/S6 hinge release; they are never substituted for the carrier.
  out.world.push(S.group([S.polygon([[-110,124],[140,124],[132,132],[-102,132]],c.pale,{'data-object':'P-A-folded'}),S.polygon([[-102,132],[132,132],[124,140],[-94,140]],c.pale,{'data-object':'P-B-folded'}),S.line([[-110,124],[140,124]],c.mid,1),S.line([[-102,132],[132,132]],c.mid,1)],{transform:S.matrixString(k.gateMatrix),'data-owner':'T06'}));
 }
 if(layerId==='T05'){
  const shapes=[];
  if(f>=1100)shapes.push(S.line([k.F.lowerEndpoint,k.F.gateAnchor],c.red,3,{'data-object':'F-gate-attachment'}));
  else shapes.push(S.line([k.pin,k.F.lowerEndpoint],c.stock,3,{'data-object':'F-carry-attachment'}));
  shapes.push(foldedFrame(ctx,k.F.matrix));
  if(f>=1100){const mask=id('F-shared-gate-mask');out.defs.push(E.structuralMask(mask,[-256,-256,2048,1536],[]));out.world.push(S.group(shapes,{mask:`url(#${mask})`,'data-owner':'T05'}));}
  else out.world.push(...shapes);
 }
 if(layerId==='T11'){
  if(f>=1198)return out;
  const A=cubicOutline(S,k.strips.A.points),L=cubicOutline(S,k.strips.L.points),R=cubicOutline(S,k.strips.R.points);
  const dA=id('A-under-L'),dR=id('strip-underpass'),over=id('R-over-L'),lineClip=id('ribbon-line-clip'),exit=id('strips-retract'),birdMask=id('ribbon-behind-bird');
  const birdShape=S.group([...G.birdLocal('wings',ctx.state.bird),...G.birdLocal('body',ctx.state.bird)],{transform:S.matrixString(M.poseMatrix(ctx.state.bird.pose))});
  const frontMaterials=[G.silhouette(birdShape)];
  if(f>=810){
   frontMaterials.push(G.silhouette(S.group([S.rect(...G.G['P-C'].local_carrier_rect,c.stock),...G.G['P-C'].receiver_rectangles.map(r=>S.rect(...r,c.ink))],{transform:S.matrixString(k.gateMatrix)})));
   for(const name of ['A','R','L'])if(k.glyphs[name].owner==='T03')frontMaterials.push(G.silhouette(S.group(glyphLocal(ctx,name),{transform:S.matrixString(k.glyphs[name].matrix)})));
  }
  out.defs.push(E.structuralMask(birdMask,[-256,-256,2048,1536],frontMaterials));
  const weaveA=k.strips.A.points[3][1]-STRIPS.A[3][1],weaveR=k.strips.R.points[3][1]-STRIPS.R[3][1];
  const Aunder=S.path('M546 365Q590 355 640 371L640 419Q590 401 546 414Z','black');
  const Runder=S.path('M675 392Q710 382 745 385L745 428Q710 432 675 438Z','black');
  out.defs.push(E.mask(dA,[100,250,1020,420],[S.group([Aunder],{transform:S.matrixString(M.translate(0,weaveA))})],ctx.effects.material_masks),E.mask(dR,[100,250,1020,420],[S.group([Runder],{transform:S.matrixString(M.translate(0,weaveR))})],ctx.effects.material_masks),S.node('clipPath',{id:over,clipPathUnits:'userSpaceOnUse'},[S.rect(765,340+weaveR,75,160,'white')]),S.node('clipPath',{id:lineClip,clipPathUnits:'userSpaceOnUse'},[S.path(R,'white')]),S.node('clipPath',{id:exit,clipPathUnits:'userSpaceOnUse'},[S.rect(1280*k.stripRetraction,0,1280*(1-k.stripRetraction),720,'white')]));
  const shapes=[S.path(R,c.stock,{'data-strip':'R'}),S.path(L,c.paper,{stroke:c.mid,'stroke-width':1.5,'data-strip':'L'}),S.path(A,c.pale,{mask:`url(#${dA})`,'data-strip':'A'}),...(ctx.effects.material_masks?[S.path(R,c.stock,{'clip-path':`url(#${over})`,'data-weave':'R-over-L'})]:[])];
  const rp=k.strips.R.points;
  const rail=(dy)=>`M${rp[0][0]+2},${rp[0][1]+dy} C${rp[1][0]+1},${rp[1][1]+dy} ${rp[2][0]+3},${rp[2][1]+dy+1} ${rp[3][0]},${rp[3][1]+dy+1} C${rp[4][0]-3},${rp[4][1]+dy+1} ${rp[5][0]+5},${rp[5][1]+dy+1} ${rp[6][0]+8},${rp[6][1]+dy+1}`;
  shapes.push(S.group([S.path(rail(13),'none',{stroke:c.ink,'stroke-width':3}),S.path(rail(28),'none',{stroke:c.ink,'stroke-width':3})],{'clip-path':`url(#${lineClip})`,mask:`url(#${dR})`,'data-object':'R-carrier-lines'}));
  const seams=[{name:'weave-seam-A',bounds:[515,413,105,55],d:'M515 413C561 415 588 446 617 449L618 468C582 463 556 436 515 433Z'},{name:'weave-seam-R',bounds:[620,462,130,48],d:'M620 487C672 481 711 477 741 462L747 481C709 496 676 503 622 506Z'}];
  for(const seam of seams){out.defs.push(E.seamGradient({id:id(seam.name),bounds:[seam.bounds[0],seam.bounds[1],seam.bounds[2]+weaveA*.5,seam.bounds[3]],enabled:ctx.effects.gradient_alpha_reveal}));shapes.push(S.path(seam.d,`url(#${id(seam.name)})`));}
  for(const [x,y] of [[161,472],[176,548],[209,616]])shapes.push(S.line([[x+30,y-8],[x+51,y+12],[x+72,y-8],[x+94,y+12]],c.mid,1.5,{'data-object':'ribbon-fold-reserve'}));
  for(const name of ['A','L','R'])if(k.glyphs[name].owner==='T11')shapes.push(glyph(ctx,name,k.glyphs[name].matrix));
  if(f>=810&&f<945)shapes.push(S.line(k.marker,c.ink,7,{'data-object':'time-marker-becomes-bar'}));
  shapes.push(S.line([[902,303],k.pin],c.stock,3),S.line([[939,442],k.pin],c.stock,3),S.line([[937,360],k.pin],c.stock,4),S.line([k.pin,k.tabEnd],c.stock,4,{'data-object':'single-grasp-tab'}));
  out.world.push(S.group([S.group(shapes,{mask:`url(#${birdMask})`})],{'clip-path':`url(#${exit})`,'data-owner':'T11','data-rich-window':f>=810&&f<1140?'E01':'outside'}));
  if(f>1140){
   const u=clamp(M,f,1140,1198),length=u*110,dy=12*(1-u),foldMask=id('residual-under-folds');
   const foldedOccluder=S.group([S.polygon([[-110,124],[140,124],[124,140],[-94,140]],'black')],{transform:S.matrixString(k.gateMatrix)});
   out.defs.push(E.structuralMask(foldMask,[-256,-256,2048,1536],[foldedOccluder]));
   out.world.push(S.group([S.group([S.line([[-95,127+dy],[-95+length*.2,135+dy],[-95+length*.4,127+dy],[-95+length*.6,135+dy],[-95+length*.8,127+dy],[-95+length,135+dy]],c.stock,5)],{transform:S.matrixString(k.gateMatrix),'data-object':'residual-strips-folding-under-PC'})],{mask:`url(#${foldMask})`}));
  }
 }
 if(layerId==='T12'&&f>=960&&f<1140){
  const anchor=M.transform([-66,40],k.glyphs.A.matrix),start=[475,411-125*k.sourceGuide.flatProgress],u=clamp(M,f,960,985),tip=start.map((v,i)=>M.mix(v,anchor[i],u));
  out.world.push(S.line([start,[(start[0]+tip[0])*.5,tip[1]],tip],c.mid,1.5,{'data-object':'source-to-A-leader'}),S.circle(...tip,3,c.ink));
 }
 if(layerId==='T14'&&f>=810&&f<1140){
  const source=ctx.sourceFields.find(x=>x.id===(f<1050?'S3.strip':'S3.gate'));
  if(!source)throw new Error('S3 approved source binding recipe missing');
  const p=k.sourceGuide.points.map((v,i)=>[v[0],M.mix(v[1],435,k.sourceGuide.flatProgress)]);
  out.defs.push(S.path(S.cubicPath(p),'none',{id:id('source-text-path'),'data-source-binding':source.id,'data-source-binding-status':ctx.sourceBindingStatus,'data-source-src':source.fields.src}),E.textReveal({id:id('source-text-reveal'),bounds:[300,386,520,90],progress:clamp(M,f,810,840),enabled:ctx.effects.text_reveal}));
  const provider=ctx.assets?.fonts?.sourceLabels;
  if(provider){
   const fields=source.displayFields;
   if(!fields||typeof fields.src!=='string'||fields.src!==source.fields.src.split('/').pop())throw new Error('S3_SOURCE_BINDING_DISPLAY_MISMATCH');
   const labels=f<1050?[fields.src]:['src: '+fields.src,'dst_in: '+fields.dst_in,'dur: '+fields.dur];
   if(f>=1050&&['dst_in','dur'].some(key=>!Number.isInteger(source.fields[key])||fields[key]!==String(source.fields[key])))throw new Error('S3_SOURCE_BINDING_NUMBER_MISMATCH');
   const text=labels.map((label,i)=>sourceGlyphs(ctx,provider,label,p,M.mix(12,11.5,k.sourceGuide.flatProgress),i*18));
   out.world.push(S.group(text,{transform:S.matrixString(M.translate(0,-125*k.sourceGuide.flatProgress)),'clip-path':`url(#${id('source-text-reveal')})`,'data-owner':'T14','data-source-binding':source.id,'data-source-binding-status':ctx.sourceBindingStatus,'data-source-text-status':'CANDIDATE_GLYPHS_UNREVIEWED'}));
  }
  // Without the explicit provider only diagnostic defs remain. No machine-font
  // fallback is possible, and implemented=false keeps normal film mode closed.
 }
 if(layerId==='T02'&&f>=1140){
  const y=M.mix(-430,0,clamp(M,f,1140,1198));
  const shapes=[S.rect(102,113,1110,8,c.grey),...[160,450,852,1158].map(x=>S.rect(x,121,6,335,c.pale)),S.rect(1000,216,185,7,c.ink),S.rect(1020,164,30,52,c.red),S.rect(1069,146,30,70,c.ink),S.rect(1118,175,30,41,c.amber),S.line([[205,121],[205,184]],c.ink,2),S.polygon([[181,184],[229,184],[218,201],[192,201]],c.mid)];
  out.world.push(S.group(shapes,{transform:S.matrixString(M.translate(0,y)),'data-owner':'T02','data-object':'W-far'}));
 }
 return out;
}
module.exports=Object.freeze({sceneId,implemented,geometryImplemented,readiness,sourceGlyphDependency,resolveSceneState,renderScene});

},
"scenes/04-gate-flight.js": function(module,exports,require){
'use strict';
// R11 / S4. Pure frame-resolved paper mechanics. No browser, clock, or accumulated state.
// Read-only source authority: R06 ebce8d57…, R03 revision02, R04/R05 faba9ce8….
// The small support/rig corrections are recorded in evidence/S4-REPORT.md.
const sceneId='S4';
const implemented=true;
const OFFSET=Object.freeze([20,16]);
const FOOT_POINTS=Object.freeze([[11,48],[29,48],[-18,48],[-1,48]]);
const FLIGHT=Object.freeze([
  Object.freeze([[400,489],[475,440],[650,344],[756,344]].map(Object.freeze)),
  Object.freeze([[756,344],[831.3157894736842,344],[965,390],[1005,447]].map(Object.freeze))
]);
function at(ctx,f,ks){return ctx.math.keys(f,ks);}
function world(p){return [p[0]+OFFSET[0],p[1]+OFFSET[1]];}
function matrix(ctx,x,y,scale=1,angle=0){return ctx.math.poseMatrix({x,y,scale,angle});}
function birdsFeet(ctx,pose){const mat=ctx.math.poseMatrix(pose);return FOOT_POINTS.map(p=>ctx.math.transform(p,mat));}
function flightPoint(ctx,f){
  if(f<=1434)return ctx.math.cubic(FLIGHT[0],ctx.math.progress(f,1320,1434));
  return ctx.math.cubic(FLIGHT[1],ctx.math.progress(f,1434,1515));
}
function birdPose(ctx,f){
  let x,y,scale,angle,wing,feet;
  if(f<1320){
    x=at(ctx,f,[[1200,400],[1220,407],[1242,400],[1260,400],[1320,400]]);
    angle=at(ctx,f,[[1200,0],[1220,-2],[1242,0],[1260,0],[1300,8],[1312,8],[1320,0]]);
    scale=.9; wing=at(ctx,f,[[1200,0],[1260,0],[1300,12],[1312,12],[1320,12]]); feet=true;
    // The lowest toe is constrained to the existing origin's thin upper contact strip.
    const relative=FOOT_POINTS.map(p=>ctx.math.transform(p,matrix(ctx,0,0,scale,angle)));
    y=angle===0?490:533.2-Math.max(...relative.map(p=>p[1]));
  }else if(f<1515){
    [x,y]=flightPoint(ctx,f);
    scale=at(ctx,f,[[1320,.9],[1434,1],[1515,.82]]);
    angle=at(ctx,f,[[1320,0],[1362,-10],[1434,-5],[1470,1],[1515,0]]);
    // Support is released before the first upstroke. Only the original A hinge moves.
    wing=at(ctx,f,[[1320,12],[1326,12],[1350,-7],[1380,5],[1434,0],[1470,-5],[1515,0]]);
    feet=f>=1503;
  }else{
    x=at(ctx,f,[[1515,1005],[1530,1013],[1545,1005],[1560,1005],[1590,972],[1620,951],[1679,951]]);
    y=at(ctx,f,[[1515,447],[1530,449],[1545,447],[1571,447],[1572,447.15],[1590,450],[1679,450]]);
    scale=.82; angle=at(ctx,f,[[1515,0],[1530,5],[1545,3],[1560,0],[1590,0],[1679,0]]);
    wing=at(ctx,f,[[1515,0],[1530,3],[1545,0],[1679,0]]); feet=true;
  }
  return {pose:{x:x+20,y:y+16,scale,angle},stage:4,space:'world',wing,feet};
}
function gateOccluders(ctx,gateMatrix,bar){
  const S=ctx.svg,c=ctx.geometry.PALETTE;
  return [S.group([
    // Same foreground post for head, body, wings, feet, historical wing, and carried F.
    S.rect(52,-12,28,122,c.mid,{'data-part':'G-front-post'}),
    S.line([bar.pivot,bar.endpoint],c.ink,7,{'data-part':'G-open-bar'})
  ],{transform:S.matrixString(gateMatrix)})];
}
function resolveSceneState(ctx,{globalFrame:f,localSceneFrame}){
  if(!Number.isInteger(f)||f<1200||f>=1680||localSceneFrame!==f-1200)throw new RangeError('S4 requires a matching frame in [1200,1680)');
  const M=ctx.math;
  const cameraMatrix=[1,0,0,1,
    -20+at(ctx,f,[[1200,0],[1320,0],[1434,-12],[1560,-16],[1679,0]]),
    -16+at(ctx,f,[[1200,0],[1320,0],[1434,-8],[1560,-12],[1679,0]])];
  const carrier=ctx.geometry.carrierAssembly(matrix(ctx,750,386,1.8));
  const barAngle=at(ctx,f,[[1200,0],[1260,0],[1314,-98],[1470,-98],[1560,-48],[1679,-48]]);
  const barPivot=[-63,-9],barEndpoint=M.transform([47,-9],M.rotate(barAngle,barPivot));
  const gateOpen=M.clamp01(-barAngle/98);
  const camAngle=at(ctx,f,[[1200,0],[1260,110],[1314,250],[1470,250],[1560,310],[1679,310]]);
  const camPin=M.transform([106,108],M.rotate(camAngle,[101,112]));
  const gate={bar:{pivot:barPivot,endpoint:barEndpoint,length:110,angle:barAngle},cam:{center:[101,112],pin:camPin,angle:camAngle},
    signal:{mount:[0,-58.84],capY:-118-18*gateOpen},hang:M.transform([80/1.8,-15-Math.sqrt(6400-(80/1.8)**2)],carrier.gateMatrix),hangOccupied:f<=1352};
  const bird=birdPose(ctx,f);
  bird.occluders=f>=1380&&f<1515?gateOccluders(ctx,carrier.gateMatrix,gate.bar):[];
  bird.maskBounds=[-256,-256,2048,1536];
  const feet=birdsFeet(ctx,bird.pose);
  // The small spring plate is the actual support. Its top follows the contacting sole
  // after landing; the fixed shelf and legs beneath it never jump or change identity.
  const landed=f>=1515;
  const plateAngle=landed?bird.pose.angle:0;
  const sole=landed?feet[0]:world([1014.02,486.36]);
  const slope=Math.tan(plateAngle*Math.PI/180);
  const plateTop=x=>sole[1]+slope*(x-sole[0]);
  const plate=[world([950,0]),world([1130,0])].map(p=>[p[0],plateTop(p[0])]);
  const plateDown=at(ctx,f,[[1200,0],[1571,0],[1572,.15],[1590,1],[1679,1]]);
  const leverAngle=-18*plateDown;
  const leverPivot=world([1010,490]);
  const leverInput=M.transform(world([994,486.36]),M.rotate(leverAngle,leverPivot));
  const leverOutput=M.transform(world([1077,451]),M.rotate(leverAngle,leverPivot));
  const release=M.progress(f,1589,1619);
  const stopLocked=world([1077,451]);
  const stopEnd=world([1039.32,457.84]);
  const stopOrigin=[M.mix(stopLocked[0],stopEnd[0],release),M.mix(stopLocked[1],stopEnd[1],release)];
  const stop= {points:[stopOrigin,[stopOrigin[0]-14.4,stopOrigin[1]+23.76],[stopOrigin[0]+9.36,stopOrigin[1]+23.76]],
    locked:f<1590,cleared:f>=1590,origin:stopOrigin,lockedOrigin:stopLocked};
  const spin=f<1620?0:(f-1620)*1.5;
  const feedProgress=M.progress(f,1620,1679);
  const reel={center:world([1125,424]),scale:.72,rotation:spin,angularVelocity:f>=1620?1.5:0,stop,
    feedProgress,curveProgress:M.progress(f,1620,1640),feedEdgeX:(f<1640?ctx.math.cubic([[1117,373],[1015,376],[1030,480],[930,498]],M.progress(f,1620,1640))[0]:930-(f-1640)*395/39)+20,
    feedVelocity:f>=1640?-395/39:0};
  // One persistent four-corner reserve. Catch is three actual overlap frames; release
  // of the hanging root occurs only after that contact, never by an owner swap.
  const catchBird=birdPose(ctx,1351),catchPoint=M.transform([-15,-6],M.poseMatrix(catchBird.pose));
  const wingRoot=M.transform([-15,-6],M.poseMatrix(bird.pose));
  let fx,fy,fs,fa;
  if(f<=1352){[fx,fy]=world([810,185]);fs=.65;fa=0;}
  else{
    fx=at(ctx,f,[[1352,830],[1380,691],[1434,670],[1515,1060],[1545,1085],[1605,1066],[1679,1066]]);
    fy=at(ctx,f,[[1352,201],[1380,330],[1434,419],[1515,493],[1545,500],[1605,514],[1679,514]]);
    fs=at(ctx,f,[[1352,.65],[1434,.6],[1545,.55],[1679,.55]]);
    fa=at(ctx,f,[[1352,0],[1380,-35],[1434,-25],[1515,16],[1545,0],[1679,0]]);
  }
  const FMatrix=matrix(ctx,fx,fy,fs,fa),FRoot=[fx,fy],FEnd=M.transform([0,36],FMatrix);
  const carried=f>=1350&&f<1640;
  const gateAnchor=ctx.math.transform([80/1.8,-15-Math.sqrt(6400-(80/1.8)**2)],carrier.gateMatrix);
  const freeTongueEnd=f<1350?[ctx.math.mix(FEnd[0],catchPoint[0],ctx.math.smoothstep(ctx.math.progress(f,1260,1350))),ctx.math.mix(FEnd[1],catchPoint[1],ctx.math.smoothstep(ctx.math.progress(f,1260,1350)))]:(f<1640?wingRoot:[M.mix(wingRoot[0],FRoot[0],M.smoothstep(M.progress(f,1640,1655))),M.mix(wingRoot[1],FRoot[1],M.smoothstep(M.progress(f,1640,1655)))]);
  const frame={matrix:FMatrix,pose:{x:fx,y:fy,scale:fs,angle:fa},root:FRoot,lowerEndpoint:FEnd,
    tongueEnd:freeTongueEnd,gateAnchor,wingRoot,catchPoint,cornerIds:['F-0','F-1','F-2','F-3'],
    attachment:f<=1352?'gate':(f<1640?'wing-root':'feed-paper'),contact:carried,
    feedAnchor:f>=1640?world(M.cubic([[1138,423],[1081,421],[1054,531],[934,548]],.59).map((v,i)=>v-(i===1?1:0))):null};
  const edgeA=[[-110,124],[140,124]],edgeB=[[-102,132],[132,132]];
  const folds={hinge:edgeA,edgeA,edgeB,faces:{P_A:{hinge:edgeA,points:[...edgeA,edgeB[1],edgeB[0]]},P_B:{hinge:edgeB,points:[...edgeB,[124,140],[-94,140]]}}};
  // Nested precreased connector reveals a 130-unit subset of the existing P-C
  // edge. The wide outer reserve remains visible. This is a bounded flat-projection
  // design correction, not a claim of three-dimensional material conservation.
  const connectorProgress=M.smoothstep(M.progress(f,1620,1679));
  const carrierEdge=[[662,609.2],[792,609.2]];
  const paBase=carrierEdge.map(p=>[p[0]-34*connectorProgress,p[1]-97.2*connectorProgress]);
  const paDirection=[200/Math.hypot(200,136),-136/Math.hypot(200,136)];
  const pbDirection=[172/Math.hypot(172,139),139/Math.hypot(172,139)];
  const sharedEdge=paBase.map(p=>p.map((v,i)=>v+paDirection[i]*8*connectorProgress));
  const pbEnd=sharedEdge.map(p=>p.map((v,i)=>v+pbDirection[i]*16*connectorProgress));
  const paper={carrierEdge,connector:[carrierEdge[0],carrierEdge[1],paBase[1],paBase[0]],paBase,sharedEdge,pbEnd,
    pa:[paBase[0],paBase[1],sharedEdge[1],sharedEdge[0]],pb:[sharedEdge[0],sharedEdge[1],pbEnd[1],pbEnd[0]],
    progress:connectorProgress,paDirection,pbDirection};
  return M.deepFreeze({cameraMatrix,bird,mechanics:{carrier,gate,origin:{x:350,y:551,top:549.2,width:180},landing:{shelfY:506,plate,plateAngle,sole,feet,contact:landed},
    lever:{pivot:leverPivot,input:leverInput,output:leverOutput,angle:leverAngle,pressure:plateDown,plateJoint:[leverInput[0],plateTop(leverInput[0])]},
    reel,frame,folds,paper,fixedGuide:{x:120,y:666,width:1080},contactOrder:{catch:f>=1350,unhook:f>=1353,land:landed,plate:f>=1572,stop:f>=1590,turn:f>=1620}}});
}
function far(ctx){const S=ctx.svg,c=ctx.geometry.PALETTE;return S.group([
  S.rect(82,97,1110,8,c.grey),...[140,430,832,1138].map(x=>S.rect(x,105,6,335,c.pale)),
  S.rect(980,200,185,7,c.ink),S.rect(1000,148,30,52,c.red),S.rect(1049,130,30,70,c.ink),S.rect(1098,159,30,41,c.amber),
  S.line([[185,105],[185,168]],c.ink,2),S.polygon([[161,168],[209,168],[198,185],[172,185]],c.mid)
],{transform:'translate(20 16)','data-object':'W-far'});}
function gateShapes(ctx,m){
  const S=ctx.svg,c=ctx.geometry.PALETTE,g=m.gate;
  const rail=[S.line([[-80,0],[80,0]],c.ink,5),S.line([[-80,18],[80,18]],c.ink,5),...[-55,0,55].map(x=>S.line([[x,-3],[x,21]],c.ink,4))];
  return S.group([
    S.group([S.rect(-110,110,250,14,c.stock),...ctx.geometry.G['P-C'].receiver_rectangles.map(r=>S.rect(...r,c.ink)),S.circle(-108,118,4,c.mid)],{'data-object':'P-C-carrier'}),
    S.path(ctx.geometry.G.A.path,c.mid,{'data-object':'A-arch'}),
    S.group(rail,{transform:'translate(0 116) scale(1.35)','data-object':'R-rail'}),
    S.group([S.path('M-22.44 0 A22.44 22.44 0 0 1 22.44 0 Z',c.amber),S.rect(-2.04,0,4.08,-58.16-g.signal.capY,c.ink)],{transform:`translate(0 ${g.signal.capY})`,'data-object':'L-signal'}),
    S.circle(...g.signal.mount,4.08,c.ink),S.line([g.bar.pivot,g.bar.endpoint],c.ink,7,{'data-object':'G-bar'}),S.circle(...g.bar.pivot,7,c.ink),
    S.line([g.bar.endpoint,[72,35],g.cam.center],c.ink,2,{'data-object':'G-linkage'}),
    S.circle(...g.cam.center,15,c.ink),S.circle(...g.cam.pin,4,c.amber),S.circle(...g.hang.map((v,i)=>(v-[750,386][i])/1.8),3,c.ink,{'data-object':'F-hang-pin'})
  ],{transform:S.matrixString(m.carrier.carrierMatrix),'data-object':'G-assembly'});
}
function transport(ctx,m){
  const S=ctx.svg,c=ctx.geometry.PALETTE,r=m.reel,guide=m.fixedGuide;
  const n=[S.group([S.line([[guide.x,guide.y],[guide.x+guide.width,guide.y]],c.mid,3),S.line([[guide.x,guide.y+13],[guide.x+guide.width,guide.y+13]],c.grey,3),...[.15,.4,.65,.9].map(u=>S.line([[guide.x+guide.width*u,guide.y+13],[guide.x+guide.width*u,guide.y+25],[guide.x+guide.width*u+13,guide.y+25]],c.mid,2))],{'data-object':'W-guide'})];
  for(const [x,y] of [[330,533.2],[1040,490]])n.push(S.group([S.rect(x-90,y,180,12,c.stock),S.line([[x-75,y+12],[x-50,y+100]],c.ink,8),S.line([[x+75,y+12],[x+50,y+100]],c.ink,8)],{transform:'translate(20 16)','data-object':x===330?'origin-support':'landing-support'}));
  const plate=m.landing.plate;
  n.push(S.polygon([plate[0],plate[1],[plate[1][0],518],[plate[0][0],518]],c.stock,{'data-object':'landing-pressure-plate'}));
  n.push(S.line(plate,c.mid,1.5),S.line([m.lever.plateJoint,m.lever.input,m.lever.pivot,m.lever.output],c.ink,3,{'data-object':'Q-release-lever'}),S.circle(...m.lever.pivot,4,c.ink));
  n.push(S.line([m.lever.output,r.stop.origin],c.ink,2,{'data-object':'Q-stop-link'}));
  n.push(S.group([S.circle(0,0,77,c.ink),S.circle(0,0,68,c.stock),S.circle(0,0,48,c.paper),S.circle(0,0,36,c.pale),
    S.group([S.path('M-59 -22 A63 63 0 0 1 52 -33 M-47 36 A57 57 0 0 0 46 37','none',{stroke:c.mid,'stroke-width':2})],{transform:`rotate(${r.rotation})`,'data-object':'Q-rotating-stock'}),S.circle(0,0,12,c.ink)
  ],{transform:S.matrixString(matrix(ctx,...r.center,r.scale)),'data-object':'Q-reel'}));
  n.push(S.line([[r.center[0]-66*r.scale,r.center[1]+78*r.scale],[r.center[0]+66*r.scale,r.center[1]+78*r.scale]],c.ink,7,{'data-object':'Q-shelf'}));
  n.push(S.polygon(r.stop.points,c.pale,{'data-object':'Q-stop'}));
  // A continuous physical ribbon emerges from the same reel. No waveform is invented.
  if(r.feedProgress>0){
    const e=r.feedEdgeX-20,u=r.curveProgress;
    function partial(points){
      const mix=(a,b)=>a.map((v,i)=>ctx.math.mix(v,b[i],u));
      const a=mix(points[0],points[1]),b=mix(points[1],points[2]),d=mix(points[2],points[3]),ab=mix(a,b),bd=mix(b,d);
      return [points[0],a,ab,mix(ab,bd)];
    }
    const upper=partial([[1117,373],[1015,376],[1030,480],[930,498]]),lower=partial([[1138,423],[1081,421],[1054,531],[934,548]]);
    const P=p=>p.map(S.number).join(' ');
    const topEnd=u<1?upper[3]:[e,498],bottomEnd=u<1?lower[3]:[e,548];
    const d=`M${P(upper[0])} C${P(upper[1])} ${P(upper[2])} ${P(upper[3])} L${P(topEnd)} L${P(bottomEnd)} L${P(lower[3])} C${P(lower[2])} ${P(lower[1])} ${P(lower[0])} Z`;
    n.push(S.path(d,c.pale,{transform:'translate(20 16)','data-object':'Q-feed-paper'}));
    for(let i=0;i<3;i++){
      const x=e+90+i*130;
      if(x<927)n.push(S.line([[x+20,517],[x+28,522],[x+25,557]],c.mid,1.2,{'data-object':`Q-feed-crease-${i}`}));
    }
  }
  return n;
}
function frameShapes(ctx,m){
  const S=ctx.svg,c=ctx.geometry.PALETTE,F=m.frame;
  const corners=[];
  for(let i=3;i>=0;i--)corners.push(S.group([
    S.polygon(ctx.geometry.G.F.corner_polygon,c.red),S.polygon([[20,16],[30,16],[20,26]],c.paper)
  ],{transform:`translate(${i*13} ${-i*10}) scale(.5)`,'data-object':`F-${i}`}));
  return [...(F.feedAnchor?[S.line([F.root,F.feedAnchor],c.red,3,{'data-object':'F-feed-attachment'})]:[]),...(m.gate.hangOccupied?[S.line([F.lowerEndpoint,F.gateAnchor],c.red,3,{'data-object':'F-gate-attachment'})]:[]),
    S.line([F.tongueEnd,m.gate.hangOccupied?F.lowerEndpoint:F.root],c.red,3,{'data-object':'F-continuous-tongue'}),
    S.group([S.line([[0,36],[9,40],[13,26],[22,30],[26,16],[35,20],[39,6]],c.red,3),...corners],{transform:S.matrixString(F.matrix),'data-object':'F-folded-reserve'})];
}
function renderScene(ctx,{globalFrame,localSceneFrame,layerId}){
  const S=ctx.svg,c=ctx.geometry.PALETTE,m=ctx.state.mechanics;
  const defs=[],nodes=[];
  if(layerId==='T02')nodes.push(far(ctx));
  if(layerId==='T03')nodes.push(gateShapes(ctx,m));
  if(layerId==='T04')nodes.push(...transport(ctx,m));
  if(layerId==='T05'){
    const maskId=ctx.id('F-shared-gate-mask');
    defs.push(ctx.effectHelpers.structuralMask(maskId,ctx.state.bird.maskBounds,ctx.state.bird.occluders.map(ctx.geometry.silhouette)));
    nodes.push(S.group(frameShapes(ctx,m),{mask:`url(#${maskId})`,'data-owner':'T05'}));
  }
  if(layerId==='T06')nodes.push(S.group([
    S.polygon(m.folds.faces.P_A.points,c.pale,{'data-object':'P-A-folded'}),S.polygon(m.folds.faces.P_B.points,c.pale,{'data-object':'P-B-folded'}),
    S.line(m.folds.edgeA,c.mid,1,{'data-object':'P-A-hinge'}),S.line(m.folds.edgeB,c.mid,1,{'data-object':'P-B-hinge'})
  ],{transform:S.matrixString(m.carrier.carrierMatrix),'data-owner':'T06'}));
  if(layerId==='T06'&&m.paper.progress>0){
    const p=m.paper;
    nodes.push(S.polygon(p.connector,c.pale,{'data-object':'P-C-to-P-A-connector'}),
      S.line([p.carrierEdge[0],p.paBase[0]],c.mid,2),S.line([p.carrierEdge[1],p.paBase[1]],c.mid,2),
      S.polygon(p.pa,c.pale,{'data-object':'P-A'}),S.polygon(p.pb,c.stock,{'data-object':'P-B'}),
      S.line([...p.pa,p.pa[0]],c.mid,2),S.line(p.sharedEdge,c.mid,2));
  }
  // T07/T08 are intentionally absent: runtime owns ALL parts of the same original B.
  return ctx.math.deepFreeze({defs,world:nodes,screen:[]});
}
module.exports=Object.freeze({sceneId,implemented,resolveSceneState,renderScene});

},
"scenes/05-sound-paper.js": function(module,exports,require){
'use strict';
/** S5 visual author candidate. Native PCM binding and user audio audition are reported separately.
 * World coordinates inherit S4; static R03 background re-layout is NOT a camera.
 * Only this module owns S5 state. T07/T08 remain mandatory shared-runtime bird pages.
 */
const sceneId='S5', implemented=true, PICKUP_FRAME=1920;
const WAVEFORM_DEPENDENCY=Object.freeze({status:'ACTUAL_PCM_NOT_BOUND_AUDITION_PENDING',owner:'T13',window:[1710,2160],analysisWindow:[1680,2160],fps:30,sampleRate:48000,samplesPerFrame:1600,missing:'No actual PCM envelope bound; no invented signal, cue ticks or audio-readiness claim',required:'actual-pcm SHA256 provenance, frame-indexed envelope, user audio audition and real removal comparison'});
const O=[20,16], CAMERA=[1,0,0,1,-20,-16];
const S4_FIXED={"gate":{"bar":{"pivot":[-63,-9],"endpoint":[10.604366699474404,-90.74593080251336],"length":110,"angle":-48},"cam":{"center":[101,112],"pin":[101.1497602759568,105.59862734565897],"angle":310},"signal":{"mount":[0,-58.84],"capY":-126.81632653061224},"hang":[830,201],"hangOccupied":false},"landing":{"shelfY":506,"plate":[[970,505.36],[1150,505.36]],"plateAngle":0,"sole":[980.02,505.36],"feet":[[980.02,505.36],[994.78,505.36],[956.24,505.36],[970.18,505.36]],"contact":true},"lever":{"pivot":[1030,506],"input":[1013.6582738797529,507.4824261906848],"output":[1081.6691238111525,448.20465724136756],"angle":-18,"pressure":1,"plateJoint":[1013.6582738797529,505.36]}};

const plus=(a,b)=>[a[0]+b[0],a[1]+b[1]], sub=(a,b)=>[a[0]-b[0],a[1]-b[1]], mul=(p,s)=>p.map(x=>x*s), wp=p=>plus(p,O);
function resolveSceneState(ctx,{globalFrame:f,localSceneFrame}) {
 if(!Number.isInteger(f)||f<1680||f>=2160||localSceneFrame!==f-1680)throw new RangeError('S5 requires matching frame in [1680,2160)');
 const M=ctx.math, smooth=(a,b)=>M.smoothstep(M.progress(f,a,b)), key=k=>M.keys(f,k);
 // Feed deformation is material payout from a fixed axle, never a replacement reel.
 const payout=smooth(1680,1768), lift=smooth(2130,2159), pull=smooth(2115,2159);
 const gate={pose:{x:750,y:386,scale:1.8,angle:0},barAngle:-48};
 const assembly=ctx.geometry.carrierAssembly(M.poseMatrix(gate.pose));
 const carrierEdge=[[642,593.2],[772,593.2]].map(wp);
 const paBase=[[608,496],[738,496]].map(wp);
 // Every panel edge has one pair of endpoint objects, consumed by both faces.
 // Oblique author-space projection: material length stays fixed while fold depth changes.
 const paWidth=300, paFinal=[200,-136], paLen=Math.hypot(...paFinal), paDirection=mul(paFinal,1/paLen);
 const paDepth=M.mix(8,paLen,lift), paAngle=Math.acos(paDepth/paWidth)*180/Math.PI;
 const sharedEdge=paBase.map(p=>plus(p,mul(paDirection,paDepth)));
 const pbWidth=260,pbFinal=[172,139],pbLen=Math.hypot(...pbFinal),pbDirection=mul(pbFinal,1/pbLen);
 const pbDepth=M.mix(16,pbLen,lift),pbAngle=Math.acos(pbDepth/pbWidth)*180/Math.PI;
 const pbEnd=sharedEdge.map(p=>plus(p,mul(pbDirection,pbDepth)));
 const pa=[paBase[0],paBase[1],sharedEdge[1],sharedEdge[0]],pb=[sharedEdge[0],sharedEdge[1],pbEnd[1],pbEnd[0]];
 // F and B use exact approved contact targets. F rests on the feed until pickup.
 // B approaches the stationary reserve, then carries it into the f1950 buckle contact.
 const birdPose={x:key([[1680,971],[1770,640],[1800,330.8],[1830,530],[1860,735],[1890,610],[1920,748],[1950,690],[2010,690],[2100,702],[2159,854]]),
 y:key([[1680,466],[1770,438],[1800,447.6],[1830,425],[1860,477.4],[1890,458],[1920,501.4],[1950,465],[2010,465],[2100,465],[2159,362]]),
 scale:key([[1680,.82],[1770,.8],[2159,.8]]),angle:key([[1680,0],[1770,-3],[1800,0],[1830,-4],[1860,0],[1890,0],[2100,0],[2159,-5]])};
 const beak=M.transform([140,-33],M.poseMatrix(birdPose));
 const framePose={x:key([[1680,1066],[1770,925],[1890,860],[1920,860],[1950,802],[2010,802],[2100,814],[2159,887]]),
 y:key([[1680,514],[1770,488],[1890,475],[1920,475],[1950,439],[2010,439],[2100,439],[2159,368]]),
 scale:key([[1680,.55],[1890,.65],[1920,.65],[1950,.7],[2100,.7],[2159,.8]]),angle:key([[1680,0],[1890,-5],[1920,-5],[1950,-15],[2100,-15],[2159,-25]])};
 const frameMatrix=M.poseMatrix(framePose),frameEndpoint=M.transform([0,36],frameMatrix);
 const anchor=wp([key([[1680,792],[1950,792],[2010,792],[2100,804],[2159,867]]),key([[1680,463],[2100,463],[2159,360]])]);
 // Connected material tab: starts folded, then seats onto the free P-A edge.
 const tabRoot=plus(sharedEdge[0],mul(sub(sharedEdge[1],sharedEdge[0]),59/130));
 const tabReserve=plus(mul(plus(tabRoot,anchor),.5),[0,14*(1-lift)]);
 const chain=ctx.geometry.forceChain({frameEndpoint,paperAnchor:anchor});
 const linked=f>=1950,seat=smooth(1950,1965);
 const tap=key([[1680,0],[1799,0],[1800,0],[1806,1],[1818,.15],[1830,0],[2159,0]]);
 const cover=key([[1680,0],[1860,0],[1875,1],[2159,1]]);
 // The contact points are derived from B, not independent decorative marks.
 const footTip=M.transform([29,48],M.poseMatrix(birdPose));
 const firstHinge=[wp([280,490]),wp([334,490])];
 const firstTip=wp([334,470+18*tap]);
 // In the touching frame the right foot is physically constrained to the tongue tip.
 if(f>=1794&&f<=1806) {
   const u=M.smoothstep(M.progress(f,1794,1800))*(1-M.smoothstep(M.progress(f,1800,1806)));
   const desired=wp([334,470+18*tap]); birdPose.x+=u*(desired[0]-footTip[0]);birdPose.y+=u*(desired[1]-footTip[1]);
 }
 const resolvedBeak=M.transform([140,-33],M.poseMatrix(birdPose));
 const secondHinge=[wp([757,490]),wp([827,490])];
 const coverContact=wp([827,435]);
 if(f>=1854&&f<=1866) {
   const u=M.smoothstep(M.progress(f,1854,1860))*(1-M.smoothstep(M.progress(f,1860,1866)));
   birdPose.x+=u*(coverContact[0]-resolvedBeak[0]);birdPose.y+=u*(coverContact[1]-resolvedBeak[1]);
 }
 const feedU=M.progress(f,1679,1768),feedT=89,feedV=-395/39;
 const feedLeft=(2*feedU**3-3*feedU**2+1)*535+(feedU**3-2*feedU**2+feedU)*feedT*feedV+(-2*feedU**3+3*feedU**2)*90;
 const feed={payout,left:feedLeft,velocity:((6*feedU**2-6*feedU)*535+(3*feedU**2-4*feedU+1)*feedT*feedV+(-6*feedU**2+6*feedU)*90)/feedT,top:M.mix(498,447,payout),bottom:M.mix(548,540,payout),joinX:M.mix(930,899,payout),sourceTop:wp([1117,373]),sourceBottom:wp([1138,423])};
 // Integral of piecewise linear angular velocity. Keeps a nonzero S4 boundary tangent.
 function integral(t,a,b,v0,v1){const d=Math.max(0,Math.min(t,b)-a);return d<=0?0:v0*d+(v1-v0)*d*d/(2*(b-a));}
 const reelAngle=88.5+1.5*Math.min(Math.max(f-1679,0),286)+integral(f,1965,2010,1.5,.8)+integral(f,2010,2100,.8,.12)+Math.max(f-2100,0)*.12;
 const reelVelocity=f<1965?1.5:f<2010?M.mix(1.5,.8,M.progress(f,1965,2010)):f<2100?M.mix(.8,.12,M.progress(f,2010,2100)):.12;
 const coverPoints=[secondHinge[0],secondHinge[1],wp([827+cover*55,435+cover*55]),wp([757+cover*55,435+cover*55])];
 // Incoming F reserve really touches the released ribbon through its short tongue.
 // Project its moving root to the actual lower feed cubic, then collapse the tongue
 // when the root is inside the ribbon. The first endpoint is exactly S4's f1679 anchor.
 const feedLower=[[1138,423],[1081,421],[M.mix(1054,1050,payout),M.mix(531,540,payout)],[M.mix(934,907,payout),feed.bottom]];
 const screenF=[framePose.x-20,framePose.y-16];
 let feedAnchor=null;
 if(f<PICKUP_FRAME){let lowerY=feed.bottom;if(screenF[0]>feedLower[3][0]){let a=0,b=1;for(let n=0;n<32;n++){const t=(a+b)/2;if(M.cubic(feedLower,t)[0]>screenF[0])a=t;else b=t;}lowerY=M.cubic(feedLower,(a+b)/2)[1];}
 const projected=wp([screenF[0],Math.min(screenF[1],lowerY-1)]),incoming=wp(M.cubic(feedLower,.59).map((v,i)=>v-(i===1?1:0))),u=smooth(1680,1700);feedAnchor=incoming.map((v,i)=>M.mix(v,projected[i],u));}
 const occluders=[];
 // The paper peel may cover only its actual material silhouette; whole B shares it.
 if(f>=2130)occluders.push(ctx.svg.polygon(pa,'black'));
 return M.deepFreeze({cameraMatrix:CAMERA.slice(),bird:{pose:birdPose,space:'world',stage:4,wing:key([[1680,0],[1740,-9],[1770,2],[1800,7],[1830,-5],[1860,4],[1890,0],[2159,0]]),feet:f<1830,occluders,maskBounds:[0,0,1600,900]},
 mechanics:{gate,assembly,landing:S4_FIXED.landing,lever:S4_FIXED.lever,carrierEdge,connector:[carrierEdge[0],carrierEdge[1],paBase[1],paBase[0]],paper:{paBase,sharedEdge,pbEnd,pa,pb,paWidth,pbWidth,paAngle,pbAngle,paDirection,pbDirection,tabRoot,tabReserve,anchor},
 frame:{pose:framePose,matrix:frameMatrix,feedAnchor,beak:M.transform([140,-33],M.poseMatrix(birdPose)),grasp:M.transform([0,0],frameMatrix),chain,linked,seat},
 reel:{pose:{x:1145,y:440,scale:.72,angle:0},angle:reelAngle,velocity:reelVelocity,unlocked:true},guide:{x:120,y:666,width:1080,fixedMatrix:M.IDENTITY},feed,
 tongues:{firstHinge,firstTip,tap,secondHinge,coverPoints,cover,coverContact},quiet:f>=2010&&f<2100,lift,pull},
 dependencies:{waveform:ctx.assets.waveform?{...WAVEFORM_DEPENDENCY,status:'ACTUAL_NATIVE_PCM_BOUND_AUDITION_PENDING',missing:null,provenance:ctx.assets.waveform.provenance,required:'User audio audition and final engine waveform/removal verification remain pending'}:WAVEFORM_DEPENDENCY,audio:ctx.assets.waveform?'ACTUAL_NATIVE_AUDIO_BOUND_USER_AUDITION_PENDING':'ACTUAL_NATIVE_AUDIO_NOT_BOUND_USER_AUDITION_PENDING'},revision:'S5-preserve-S4-world-v1'});
}
function foldedFrame(ctx,frame) {
 const S=ctx.svg,M=ctx.math,C=ctx.geometry.PALETTE,corner=ctx.geometry.G.F.corner_polygon;
 return S.group([S.line([[0,36],[9,40],[13,26],[22,30],[26,16],[35,20],[39,6]],C.red,3),...[3,2,1,0].map(i=>S.group([S.polygon(corner,C.red),S.polygon([[20,16],[30,16],[20,26]],C.paper)],{transform:S.matrixString(M.multiply(M.translate(i*13,-i*10),M.scale(.5))),'data-object':`F-corner-${i}`}))],{transform:S.matrixString(frame.matrix),'data-object':'F-folded'});
}
function drawGate(ctx,m){
  const S=ctx.svg,c=ctx.geometry.PALETTE,g=S4_FIXED.gate;
  const rail=[S.line([[-80,0],[80,0]],c.ink,5),S.line([[-80,18],[80,18]],c.ink,5),...[-55,0,55].map(x=>S.line([[x,-3],[x,21]],c.ink,4))];
  return S.group([
    S.group([S.rect(-110,110,250,14,c.stock),...ctx.geometry.G['P-C'].receiver_rectangles.map(r=>S.rect(...r,c.ink)),S.circle(-108,118,4,c.mid)],{'data-object':'P-C-carrier'}),
    S.path(ctx.geometry.G.A.path,c.mid,{'data-object':'A-arch'}),
    S.group(rail,{transform:'translate(0 116) scale(1.35)','data-object':'R-rail'}),
    S.group([S.path('M-22.44 0 A22.44 22.44 0 0 1 22.44 0 Z',c.amber),S.rect(-2.04,0,4.08,-58.16-g.signal.capY,c.ink)],{transform:`translate(0 ${g.signal.capY})`,'data-object':'L-signal'}),
    S.circle(...g.signal.mount,4.08,c.ink),S.line([g.bar.pivot,g.bar.endpoint],c.ink,7,{'data-object':'G-bar'}),S.circle(...g.bar.pivot,7,c.ink),
    S.line([g.bar.endpoint,[72,35],g.cam.center],c.ink,2,{'data-object':'G-linkage'}),
    S.circle(...g.cam.center,15,c.ink),S.circle(...g.cam.pin,4,c.amber),S.circle(...[830,239.26696362323386].map((v,i)=>(v-[750,386][i])/1.8),3,c.ink,{'data-object':'F-hang-pin'})
  ],{transform:S.matrixString(m.assembly.carrierMatrix),'data-object':'G-assembly'});
}
function drawFar(ctx) {const S=ctx.svg,C=ctx.geometry.PALETTE;return S.group([S.rect(82,97,1110,8,C.grey),...[140,430,832,1138].map(x=>S.rect(x,105,6,335,C.pale)),S.rect(980,200,185,7,C.ink),S.rect(1000,148,30,52,C.red),S.rect(1049,130,30,70,C.ink),S.rect(1098,159,30,41,C.amber),S.line([[185,105],[185,168]],C.ink,2),S.polygon([[161,168],[209,168],[198,185],[172,185]],C.mid)],{transform:'translate(20 16)','data-object':'W-far'});}
function drawReel(ctx,r) {const S=ctx.svg,C=ctx.geometry.PALETTE,M=ctx.math;return S.group([S.group([S.circle(0,0,77,C.ink),S.circle(0,0,68,C.stock),S.circle(0,0,48,C.paper),S.circle(0,0,36,C.pale),S.path('M-59-22A63 63 0 0 1 52-33 M-47 36A57 57 0 0 0 46 37','none',{stroke:C.mid,'stroke-width':2})],{transform:S.matrixString(M.rotate(r.angle)),'data-object':'Q-rotor'}),S.circle(0,0,12,C.ink),S.line([[-66,78],[66,78]],C.ink,7),S.polygon([[-119,47],[-139,80],[-106,80]],C.pale)],{transform:S.matrixString(M.poseMatrix(r.pose)),'data-object':'Q-reel-assembly'});}
function drawTransport(ctx,m){
  const S=ctx.svg,c=ctx.geometry.PALETTE,r={center:[1145,440],scale:.72,rotation:m.reel.angle,stop:{origin:[1059.32,473.84],points:[[1059.32,473.84],[1044.9199999999998,497.59999999999997],[1068.6799999999998,497.59999999999997]]}},guide=m.guide;
  const n=[S.group([S.line([[guide.x,guide.y],[guide.x+guide.width,guide.y]],c.mid,3),S.line([[guide.x,guide.y+13],[guide.x+guide.width,guide.y+13]],c.grey,3),...[.15,.4,.65,.9].map(u=>S.line([[guide.x+guide.width*u,guide.y+13],[guide.x+guide.width*u,guide.y+25],[guide.x+guide.width*u+13,guide.y+25]],c.mid,2))],{'data-object':'W-guide'})];
  for(const [x,y] of [[330,533.2],[1040,490]])n.push(S.group([S.rect(x-90,y,180,12,c.stock),S.line([[x-75,y+12],[x-50,y+100]],c.ink,8),S.line([[x+75,y+12],[x+50,y+100]],c.ink,8)],{transform:'translate(20 16)','data-object':x===330?'origin-support':'landing-support'}));
  const plate=m.landing.plate;
  n.push(S.polygon([plate[0],plate[1],[plate[1][0],518],[plate[0][0],518]],c.stock,{'data-object':'landing-pressure-plate'}));
  n.push(S.line(plate,c.mid,1.5),S.line([m.lever.plateJoint,m.lever.input,m.lever.pivot,m.lever.output],c.ink,3,{'data-object':'Q-release-lever'}),S.circle(...m.lever.pivot,4,c.ink));
  n.push(S.line([m.lever.output,r.stop.origin],c.ink,2,{'data-object':'Q-stop-link'}));
  n.push(S.group([S.circle(0,0,77,c.ink),S.circle(0,0,68,c.stock),S.circle(0,0,48,c.paper),S.circle(0,0,36,c.pale),
    S.group([S.path('M-59 -22 A63 63 0 0 1 52 -33 M-47 36 A57 57 0 0 0 46 37','none',{stroke:c.mid,'stroke-width':2})],{transform:`rotate(${r.rotation})`,'data-object':'Q-rotating-stock'}),S.circle(0,0,12,c.ink)
  ],{transform:S.matrixString(ctx.math.poseMatrix({x:r.center[0],y:r.center[1],scale:r.scale,angle:0})),'data-object':'Q-reel'}));
  n.push(S.line([[r.center[0]-66*r.scale,r.center[1]+78*r.scale],[r.center[0]+66*r.scale,r.center[1]+78*r.scale]],c.ink,7,{'data-object':'Q-shelf'}));
  n.push(S.polygon(r.stop.points,c.pale,{'data-object':'Q-stop'}));
  return n;
}
function feedPath(ctx,feed,inset=0) {const M=ctx.math,n=ctx.svg.number;const p=feed.payout,left=feed.left+inset,top=feed.top+inset,bottom=feed.bottom-inset,j=feed.joinX;
 // Source mouth and terminal tangent stay attached to the same fixed Q assembly.
 const a=[1117,373],b=[1138,423]; return `M${a[0]+20} ${a[1]+16}C${M.mix(1015,1000,p)+20} ${M.mix(376,376,p)+16} ${M.mix(1030,1008,p)+20} ${top-18+16} ${j+20} ${top+16}L${left+20} ${top+16}V${bottom+16}L${M.mix(934,907,p)+20} ${bottom+16}C${M.mix(1054,1050,p)+20} ${M.mix(531,540,p)+16} ${1081+20} ${421+16} ${b[0]+20} ${b[1]+16}Z`;}
function renderScene(ctx,{globalFrame:f,layerId}) {
 const S=ctx.svg,C=ctx.geometry.PALETTE,E=ctx.effectHelpers,M=ctx.math,m=ctx.state.mechanics,id=ctx.id,defs=[],world=[];
 if(layerId==='T02')world.push(drawFar(ctx));
 if(layerId==='T03')world.push(drawGate(ctx,m));
 if(layerId==='T04') {
  world.push(...drawTransport(ctx,m));
  const feed=E.deform(ctx.effects.path_deformation,m.feed,{...m.feed,payout:0,left:535,top:498,bottom:548,joinX:930});
  world.push(S.path(feedPath(ctx,feed),C.pale,{'data-object':'Q-feed'}));
  for(let i=0;i<3;i++){const x=feed.left+90+i*130;if(x<927)world.push(S.line([[x+20,517],[x+28,522],[x+25,557]],C.mid,1.2,{'data-object':`Q-feed-crease-${i}`}));}
  if(m.feed.payout>0)world.push(S.path(feedPath(ctx,feed,10),C.paper,{opacity:m.feed.payout,'data-object':'Q-feed-inset'}));
  const t=m.tongues,maskId=id('buckle-cover');
  defs.push(E.structuralMask(maskId,[700,400,240,160],[S.polygon(t.coverPoints,'black')]));
  // New details are present as fold reserves, uncovered by physical payout, not fading props.
  const clip=id('feed-detail-clip');defs.push(S.node('clipPath',{id:clip,clipPathUnits:'userSpaceOnUse'},[S.rect(M.mix(1138,90,M.smoothstep(M.progress(f,1680,1768)))+20,350,1200,240,'white')]));
  const buckle= S.group([S.line([plus(m.paper.anchor,[-101,33]),plus(m.paper.anchor,[-54,33]),plus(m.paper.anchor,[-35,0]),m.paper.anchor],C.ink,5),S.circle(...plus(m.paper.anchor,[-54,33]),6,C.ink),S.circle(...m.paper.anchor,5,C.ink),S.circle(...m.paper.anchor,2,C.red)],{mask:`url(#${maskId})`,'data-object':'Q-P-buckle'});
  world.push(S.group([S.polygon([wp([280,490]),wp([280,470+18*t.tap]),wp([312,451+32*t.tap]),t.firstTip,wp([334,490])],C.stock,{'data-object':'first-tongue'}),S.line(t.firstHinge,C.mid,2),buckle,S.polygon(t.coverPoints,C.pale,{'data-object':'second-cover',stroke:C.mid,'stroke-width':1.5,'stroke-opacity':1-t.cover}),S.line(t.secondHinge,C.mid,2)],{'clip-path':`url(#${clip})`}));
  if(f>=1950)world.push(S.circle(...m.paper.anchor,5+2*(1-m.frame.seat),C.ink,{'data-object':'seating-response'}),S.circle(...m.paper.anchor,2,C.red));
 }
 if(layerId==='T06') {
  world.push(S.group([S.polygon([[-110,124],[140,124],[132,132],[-102,132]],C.pale,{'data-object':'P-A-folded'}),S.polygon([[-102,132],[132,132],[124,140],[-94,140]],C.pale,{'data-object':'P-B-folded'}),S.line([[-110,124],[140,124]],C.mid,1,{'data-object':'P-A-hinge'}),S.line([[-102,132],[132,132]],C.mid,1,{'data-object':'P-B-hinge'})],{transform:S.matrixString(m.assembly.carrierMatrix),'data-owner':'T06'}));
  const p=m.paper,pa=E.deform(ctx.effects.path_deformation,p.pa,[p.paBase[0],p.paBase[1],plus(p.paBase[1],[8,0]),plus(p.paBase[0],[8,0])]);
  world.push(S.polygon(m.connector,C.pale,{'data-object':'P-C-to-P-A-connector'}),S.line([m.carrierEdge[0],p.paBase[0]],C.mid,2),S.line([m.carrierEdge[1],p.paBase[1]],C.mid,2),S.polygon(pa,C.pale,{'data-object':'P-A'}),S.polygon(p.pb,C.stock,{'data-object':'P-B'}),S.line([...pa,pa[0]],C.mid,2),S.line(p.sharedEdge,C.mid,2));
  const seam=id('paper-seam'),edge=p.sharedEdge;defs.push(E.seamGradient({id:seam,bounds:[edge[0][0],edge[0][1],130,13],enabled:ctx.effects.gradient_alpha_reveal}));
  world.push(S.polygon([edge[0],edge[1],plus(edge[1],[9,11]),plus(edge[0],[9,11])],`url(#${seam})`,{'data-object':'P-A-P-B-seam',opacity:M.smoothstep(M.progress(f,1680,1710))}));
  world.push(S.group([S.polygon([p.tabRoot,p.tabReserve,p.anchor,plus(p.anchor,[5,3]),plus(p.tabReserve,[5,3]),plus(p.tabRoot,[5,3])],C.pale,{'data-object':'P-A-buckle-tab'}),S.line([p.tabRoot,p.tabReserve,p.anchor],C.mid,1.5)],{opacity:M.smoothstep(M.progress(f,1680,1710))}));
 }
 if(layerId==='T05') {
  world.push(foldedFrame(ctx,m.frame));
  if(m.frame.feedAnchor)world.push(S.line([[m.frame.pose.x,m.frame.pose.y],m.frame.feedAnchor],C.red,3,{'data-object':'F-feed-attachment'}));
  // The grasp tongue cannot precede physical pickup; no long line appears at approach.
  if(f>=PICKUP_FRAME)world.push(S.line([m.frame.grasp,m.frame.beak],C.red,5,{'data-object':'beak-F-grasp'}));
  if(m.frame.linked) {const q=ctx.geometry.forceChainShapes(m.frame.chain);world.push(S.group([q.tongue],{'data-object':'F-to-buckle-tongue'}));}
 }
 // T13 uses only the bound native PCM; the user's audio audition remains separate.
 if(layerId==='T13' && ctx.assets.waveform) {
  const points=ctx.geometry.waveformPoints(ctx.assets.waveform,{startFrame:1680,endFrame:2160,x:110,y:500,width:807,height:28});
  const feed=E.deform(ctx.effects.path_deformation,m.feed,{...m.feed,payout:0,left:535,top:498,bottom:548,joinX:930});
  const reveal=id('real-pcm-reveal'),paperClip=id('real-pcm-feed'),covered=id('real-pcm-covered');
  const entering=M.smoothstep(M.progress(f,1710,1770)),leaving=M.smoothstep(M.progress(f,2100,2159));
  // The trailing edge withdraws the unchanged signal before the T13 clip ends.
  // Both edges live in the same paper-local coordinates as the original PCM points.
  const start=110+807*leaving,end=110+807*entering;
  defs.push(S.node('clipPath',{id:reveal,clipPathUnits:'userSpaceOnUse'},[S.rect(start,460,Math.max(0,end-start),80,'white')]),
   S.node('clipPath',{id:paperClip,clipPathUnits:'userSpaceOnUse'},[S.path(feedPath(ctx,feed),'white')]));
  const p=m.paper,pa=E.deform(ctx.effects.path_deformation,p.pa,[p.paBase[0],p.paBase[1],plus(p.paBase[1],[8,0]),plus(p.paBase[0],[8,0])]);
  const bird=ctx.state.bird,t=m.tongues;
  // T13 paints above these pages, so restore their actual material silhouettes here.
  defs.push(E.structuralMask(covered,[0,350,1280,260],[S.polygon(pa,'black'),S.polygon(p.pb,'black'),S.polygon(m.connector,'black'),
   S.line([...pa,pa[0]],'black',2),S.line(p.sharedEdge,'black',2),S.line([m.carrierEdge[0],p.paBase[0]],'black',2),S.line([m.carrierEdge[1],p.paBase[1]],'black',2),
   S.polygon([p.tabRoot,p.tabReserve,p.anchor,plus(p.anchor,[5,3]),plus(p.tabReserve,[5,3]),plus(p.tabRoot,[5,3])],'black'),S.line([p.tabRoot,p.tabReserve,p.anchor],'black',1.5),
   S.polygon([wp([280,490]),wp([280,470+18*t.tap]),wp([312,451+32*t.tap]),t.firstTip,wp([334,490])],'black'),S.polygon(t.coverPoints,'black'),
   S.group([...ctx.geometry.birdLocal('body',bird),...ctx.geometry.birdLocal('wings',bird)],{transform:S.matrixString(M.poseMatrix(bird.pose))}),foldedFrame(ctx,m.frame)]));
  // Follow the feed's leading edge/top during payout, with no amplitude remapping.
  const onPaper=S.group([S.line(points,C.ink,1.4,{'data-object':'actual-pcm-envelope'})],{transform:S.matrixString(M.translate(feed.left-90,feed.top-447)),'clip-path':`url(#${reveal})`,'data-object':'paper-local-pcm'});
  world.push(S.group([onPaper],{'clip-path':`url(#${paperClip})`,mask:`url(#${covered})`,'data-object':'Q-feed-pcm'}));
 }
 return M.deepFreeze({defs,world,screen:[]});
}
module.exports=Object.freeze({sceneId,implemented,resolveSceneState,renderScene,WAVEFORM_DEPENDENCY,drawGate,drawFar,drawReel,foldedFrame,feedPath,drawTransport});

},
"scenes/06-unfold-world.js": function(module,exports,require){
'use strict';
/** S6 actual flat-vector scene. Canonical S4/S5 world is retained; R03's
 * incompatible independently rescaled workshop targets are deliberately not used.
 * The two paper joints are computed once from an articulated planar chart. This
 * is an author-space fold projection, NOT a claim of a rigid 3-D material solve.
 */
const sceneId='S6';
const implemented=true;
const START=2160, END=2760, SETTLED=2700;
// Frozen canonical S4/S5 mechanical checkpoint, adopted without shared edits.
const S4_FIXED={"gate":{"bar":{"pivot":[-63,-9],"endpoint":[10.604366699474404,-90.74593080251336],"length":110,"angle":-48},"cam":{"center":[101,112],"pin":[101.1497602759568,105.59862734565897],"angle":310},"signal":{"mount":[0,-58.84],"capY":-126.81632653061224},"hang":[830,239.26696362323386],"hangOccupied":false},"landing":{"shelfY":506,"plate":[[970,505.36],[1150,505.36]],"plateAngle":0,"sole":[980.02,505.36],"feet":[[980.02,505.36],[994.78,505.36],[956.24,505.36],[970.18,505.36]],"contact":true},"lever":{"pivot":[1030,506],"input":[1013.6582738797529,507.4824261906848],"output":[1081.6691238111525,448.20465724136756],"angle":-18,"pressure":1,"plateJoint":[1013.6582738797529,505.36]}};
const C={paper:'#F1EDE1',ink:'#252B29',red:'#D9533B',grey:'#C3C6BB',mid:'#90998E',amber:'#D9AE53',pale:'#DCDCCF',stock:'#CBC5B3'};
const add=(a,b)=>[a[0]+b[0],a[1]+b[1]];
const sub=(a,b)=>[a[0]-b[0],a[1]-b[1]];
const mul=(a,t)=>[a[0]*t,a[1]*t];
const lerp=(a,b,t)=>[a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t];
const deg=r=>r*180/Math.PI;
function worldFromScreen(p,camera){return [(p[0]-camera[4])/camera[0],(p[1]-camera[5])/camera[3]];}
function cubicEdge(S,a,b,bend){
  const d=sub(b,a), n=Math.hypot(...d), normal=n?[-d[1]/n,d[0]/n]:[0,0];
  const p=add(lerp(a,b,1/3),mul(normal,bend)),q=add(lerp(a,b,2/3),mul(normal,bend));
  return {control:[a,p,q,b],d:S.cubicPath([a,p,q,b])};
}
function facePath(S,hinge,far,bend=0){
  // A shared hinge remains straight. The non-load-bearing outer paper lip curls.
  const e=cubicEdge(S,far[1],hinge[1],bend);
  return {d:`M${S.points([hinge[0]])} L${S.points([far[0],far[1]])} C${S.points(e.control.slice(1))} Z`,lip:e.control};
}
function planePolygon(M,hinge,far,lip){
  return [hinge[0],far[0],far[1],...Array.from({length:25},(_,i)=>M.cubic(lip,(i+1)/25))];
}
function sampleEdgeRoute(M,hAB,hCA,frame){
  // The physical buckle slides along the existing paper rim, never jumps to a
  // detached screen position. Its three-way endpoint is shared with F's tongue.
  if(frame<=2400)return lerp(hAB[0],hAB[1],59/130);
  const route=[lerp(hAB[0],hAB[1],59/130),hAB[0],hCA[0],hCA[1]];
  const lengths=route.slice(1).map((p,i)=>Math.hypot(...sub(p,route[i]))),total=lengths.reduce((a,b)=>a+b,0);
  let remaining=M.progress(frame,2400,2520)*total;
  for(let i=0;i<lengths.length;i++){
    if(remaining<=lengths[i])return lerp(route[i],route[i+1],M.smoothstep(remaining/lengths[i]));
    remaining-=lengths[i];
  }
  return hCA[1];
}
function resolveSceneState(ctx,{globalFrame,localSceneFrame}){
  const M=ctx.math,S=ctx.svg,G=ctx.geometry;
  if(!Number.isInteger(globalFrame)||globalFrame<START||globalFrame>=END)throw new RangeError('S6 frame must be in [2160,2760)');
  if(localSceneFrame!==globalFrame-START)throw new Error('S6 local/global frame mismatch');
  const f=Math.min(globalFrame,SETTLED), k=(a)=>M.keys(f,a), prog=(a,b)=>M.smoothstep(M.progress(f,a,b));
  // A single camera for every owner page; world-fixed guide and reel never rebase.
  const z=k([[2160,1],[2250,1.015],[2400,.88],[2610,.75],[2700,.72]]);
  const cameraMatrix=[z,0,0,z,k([[2160,-20],[2250,-24],[2400,-1],[2700,2]]),k([[2160,-16],[2250,-24],[2400,44],[2700,101.08]])];
  const endCamera=[.72,0,0,.72,2,101.08];
  const slide=k([[2160,0],[2250,0],[2280,.12],[2400,1],[2700,1]]);
  const gatePose={x:750+150*slide,y:386,scale:1.8,angle:0};
  const carrier=G.carrierAssembly(M.poseMatrix(gatePose));
  // P-C's connector is part of its one flat support, not another independently
  // scaled gate. Its actual hinge shares endpoint objects with P-A.
  const opening=prog(2160,2250);
  const caStartLocal=[(628-750)/1.8,(512-386)/1.8];
  const caEndWorld=worldFromScreen([215,530],endCamera);
  const caEndLocal=[(caEndWorld[0]-900)/1.8,(caEndWorld[1]-386)/1.8];
  const ca0=M.transform(lerp(caStartLocal,caEndLocal,opening),carrier.carrierMatrix);
  const edgeAngle=M.mix(0,90,opening), projectedEdge=M.mix(130,47/.72,opening);
  const edgeVector=[Math.cos(edgeAngle*Math.PI/180)*projectedEdge,Math.sin(edgeAngle*Math.PI/180)*projectedEdge];
  const hCA=[ca0,add(ca0,edgeVector)];
  const aSpan=lerp([200,-136],[413/.72,0],opening);
  const hAB=[add(hCA[0],aSpan),add(hCA[1],aSpan)];
  // P-B stays folded until its declared 2520 release. Its inherited support moves
  // as P-A opens; its own opening parameter remains exactly zero through2520.
  const bOpen=prog(2520,2610), inherited=lerp([172,139],[320,-210],opening);
  const bSpan=lerp(inherited,[387/.72,20/.72],bOpen);
  const bFree=[add(hAB[0],bSpan),add(hAB[1],bSpan)];
  const curlPhase=Math.sin(Math.PI*M.progress(f,2400,2610));
  const settle=Math.sin(Math.PI*M.progress(f,2610,2700))*(1-M.progress(f,2610,2700));
  const ripple=(f>=2280&&f<2700)?Math.sin((f-2280)*Math.PI/80)*(1-M.progress(f,2610,2700)):0;
  const aBend=ctx.effects.path_deformation?(f>=2280?3.2*ripple:0):0;
  const bBend=ctx.effects.path_deformation?(-145*curlPhase-9*settle+4*ripple):0;
  const aPath=facePath(S,hCA,hAB,aBend),bPath=facePath(S,hAB,bFree,bBend);
  const pcInner=[[662+150*slide,609.2],[792+150*slide,609.2]];
  const planes={PC:{hinge:hCA,points:[pcInner[0],pcInner[1],hCA[1],hCA[0]]},
    PA:{hinges:[hCA,hAB],points:[hCA[0],hCA[1],hAB[1],hAB[0]],path:aPath.d,lip:aPath.lip,polygon:planePolygon(M,hCA,hAB,aPath.lip)},
    PB:{hinge:hAB,farEdge:bFree,points:[hAB[0],hAB[1],bFree[1],bFree[0]],path:bPath.d,lip:bPath.lip,polygon:planePolygon(M,hAB,bFree,bPath.lip)},
    joints:{PC_PA:hCA,PA_PB:hAB}, projection:{materialEdge:130,projectedEdge,edgeAngle,aOpening:opening,bOpening:bOpen,aSpan,bSpan}};
  // The same original B; no independent page transforms. Body scale stays in a
  // narrow world-space range, so the common retreat genuinely makes B smaller.
  const birdPose={x:k([[2160,854],[2250,1015],[2350,1120],[2450,1155],[2520,1230],[2610,1260],[2700,(850-2)/.72]]),
    y:k([[2160,362],[2250,320],[2350,355],[2450,235],[2520,188],[2610,185],[2700,(245-101.08)/.72]]),
    scale:k([[2160,.8],[2450,.82],[2700,.86]]),angle:k([[2160,-5],[2250,-12],[2350,-5],[2450,-9],[2610,-4],[2700,-3]])};
  const beak=M.transform([140,-33],M.poseMatrix(birdPose));
  const foldRoot={x:k([[2160,887],[2280,1035],[2400,1135]]),y:k([[2160,368],[2280,350],[2400,280]]),scale:.8,angle:k([[2160,-25],[2280,-18],[2400,-12]])};
  const foldMatrix=M.poseMatrix(foldRoot), deploy=prog(2400,2520);
  const finalCorners=[{p:[170,556],a:-90},{p:[170,104],a:0},{p:[1110,104],a:90},{p:[1110,556],a:180}];
  const releasedCorners=[{p:[184,552],a:-90},{p:[182,112],a:0},{p:[1098,242],a:95},{p:[1080,491],a:160}];
  const corners=finalCorners.map((target,i)=>{
    const folded=M.transform([i*13,-i*10],foldMatrix), mid=worldFromScreen(releasedCorners[i].p,cameraMatrix), end=worldFromScreen(target.p,endCamera);
    let p,a,s;
    if(f<=2400){p=folded;a=foldRoot.angle;s=.4;}
    else if(f<2450){const u=prog(2400,2450);p=lerp(folded,mid,u);a=M.mix(foldRoot.angle,releasedCorners[i].a,u);s=M.mix(.4,i===3?.8:1,u);}
    else {const u=prog(2450,2700);p=lerp(mid,end,u);a=M.mix(releasedCorners[i].a,target.a,u);s=M.mix(i===3?.8:1,1,u);}
    return {id:`F-corner-${i}`,origin:p,angle:a,scale:s,matrix:M.poseMatrix({x:p[0],y:p[1],scale:s,angle:a})};
  });
  const lower=corners[0];
  const chainMatrix=f<=2400?foldMatrix:M.poseMatrix({x:lower.origin[0],y:lower.origin[1],angle:lower.angle,scale:M.mix(.8,lower.scale,prog(2400,2450))});
  const frameEndpoint=M.transform([0,36],chainMatrix);
  const chain=G.forceChain({frameEndpoint,paperAnchor:sampleEdgeRoute(M,hAB,hCA,f)});
  const birdOccluders=[S.polygon(planes.PA.points,C.paper,{'data-occluder':'P-A-paper'})];
  if(f>=2280&&f<2400)birdOccluders.push(S.group([S.rect(52,-12,28,122,C.mid)],{transform:S.matrixString(carrier.gateMatrix),'data-occluder':'G-near-column'}));
  if(f>=2280)birdOccluders.push(S.path(bPath.d,C.paper,{'data-occluder':'P-B-foreground'}));
  const gateOccluders=[S.path(bPath.d,C.paper,{'data-occluder':'P-B-foreground'})];
  const lampLift=prog(2520,2610);
  const lamp={cap:[M.mix(0,155,lampLift),M.mix(S4_FIXED.gate.signal.capY,-68,lampLift)],scale:.68,lift:lampLift,mount:S4_FIXED.gate.signal.mount};
  const feet=[[-66,110],[66,110]].map(p=>M.transform(p,carrier.gateMatrix));
  const receivers=[[-70,110],[70,110]].map(p=>M.transform(p,carrier.receiverMatrix));
  // Q, guide and both original shelves remain in their incoming world coordinates.
  const q={x:1145,y:440,scale:.72,rotation:617.73+.12*(f-2160)-.06*(f-2160)*(f-2160)/540};
  const guide={x:120,y:666,width:1080,secondY:679,bracketY:691,matrix:[1,0,0,1,0,0]};
  const feedEnd=lerp([110,556],hCA[1],prog(2160,2280));
  const feedBend=ctx.effects.path_deformation?8*ripple+8*curlPhase:0,fu=prog(2160,2280);
  const feedPoint=(a,b)=>lerp(a,b,fu);
  const ft=feedPoint([110,463],[feedEnd[0],feedEnd[1]-31]),fb=feedPoint([110,556],feedEnd);
  const feedJoinTop=feedPoint([919,463],[1110,feedEnd[1]-31]);
  const feedJoinBottom=feedPoint([927,556],[1112,feedEnd[1]]);
  const c1=feedPoint([1020,392],[1255,393]),c2=feedPoint([1028,445],[1255,feedEnd[1]-31+feedBend]);
  const c3=feedPoint([1070,556],[1240,feedEnd[1]+feedBend]),c4=feedPoint([1101,437],[1268,439]);
  const feedPath=`M1137 389 C${S.points([c1,c2,feedJoinTop])} L${S.points([ft,fb,feedJoinBottom])} C${S.points([c3,c4,[1158,439]])} Z`;
  const ift=add(ft,[10,10]),ifb=add(fb,[10,-10]),ijt=add(feedJoinTop,[0,10]),ijb=add(feedJoinBottom,[0,-10]);
  const feedInsetPath=`M1137 389 C${S.points([c1,add(c2,[0,10]),ijt])} L${S.points([ift,ifb,ijb])} C${S.points([c3,c4,[1158,439]])} Z`;
  const stationReveal=prog(2280,2520), operatorEnter=prog(2280,2310), handHold=prog(2280,2340)*(1-prog(2400,2490));
  const operator={x:k([[2160,775],[2280,775],[2400,790],[2520,785],[2700,720]]),y:568,scale:.73,enter:operatorEnter,handHold};
  const seamWidth=f<2280?11:7+6*(1-prog(2610,2700))+3*Math.abs(ripple);
  return M.deepFreeze({cameraMatrix,bird:{pose:birdPose,space:'world',stage:4,wing:k([[2160,0],[2280,9],[2400,-7],[2450,0],[2520,10],[2610,-3],[2700,0]]),feet:false,occluders:birdOccluders,maskBounds:[-500,-400,2500,1800]},
    mechanics:{carrier,gatePose,gateOccluders,feet,receivers,planes,forceChain:chain,paperAttachment:chain.paperAnchor,q,guide,feedPath,feedInsetPath,feedEnd,operator,lamp,frame:{corners,foldRoot,foldMatrix,deploy,beak,pullPoint:f<=2400?[foldRoot.x,foldRoot.y]:lerp([foldRoot.x,foldRoot.y],corners[2].origin,prog(2400,2450))},
      stationReveal,seamWidth,ripple,curlPhase,settle,slide,effectiveFrame:f,complete:f===SETTLED,projectionStatus:'PLANAR_SHARED_ANCHOR_GRAPH_NOT_RIGID_MATERIAL_CERTIFICATION'},
    boundaryRevision:'S4_S5_CANONICAL_FIXED_WORLD',sceneId});
}
function line(S,p,c=C.ink,w=2,attrs={}){return S.line(p,c,w,attrs);}
function groupPose(S,M,children,p,attrs={}){return S.group(children,{transform:S.matrixString(M.poseMatrix(p)),...attrs});}
function frameCorner(S,G){return [S.polygon(G.G.F.corner_polygon,C.red),S.polygon([[20,16],[30,16],[20,26]],C.paper)];}
function drawFar(ctx) {const S=ctx.svg,C=ctx.geometry.PALETTE;return S.group([S.rect(82,97,1110,8,C.grey),...[140,430,832,1138].map(x=>S.rect(x,105,6,335,C.pale)),S.rect(980,200,185,7,C.ink),S.rect(1000,148,30,52,C.red),S.rect(1049,130,30,70,C.ink),S.rect(1098,159,30,41,C.amber),S.line([[185,105],[185,168]],C.ink,2),S.polygon([[161,168],[209,168],[198,185],[172,185]],C.mid)],{transform:'translate(20 16)','data-object':'W-far'});}
function operatorShapes(ctx,m){
  const S=ctx.svg,M=ctx.math,o=m.operator,arm=lerp([74,-68],[(m.feet[0][0]-o.x)/o.scale,(m.feet[0][1]-o.y)/o.scale],o.handHold);
  const shapes=[S.circle(0,-105,21,C.ink),S.polygon([[-21,-77],[21,-77],[34,-4],[-31,-4]],C.mid),line(S,[[-19,-3],[-29,61],[-47,61]],C.ink,9),line(S,[[22,-3],[31,61],[49,61]],C.ink,9),line(S,[[-19,-64],[-51,-33],[-71,-49]],C.ink,7),line(S,[[21,-63],[50,-34],arm],C.ink,7)];
  return groupPose(S,M,shapes,{x:o.x,y:o.y,scale:o.scale,angle:0},{'data-object':'W-operator'});
}
function toolStations(ctx,m){
  const S=ctx.svg,M=ctx.math,G=ctx.geometry,stroke={'stroke':C.paper,'stroke-width':3};
  const cut=[S.rect(303,218,6,207,C.grey),S.rect(393,218,6,207,C.grey),S.rect(310,270,76,114,C.ink),S.rect(316,276,64,102,C.mid),S.rect(342,218,10,52,C.mid),groupPose(S,M,[S.polygon(G.G.bird.wing_a,'none',stroke),S.polygon(G.G.bird.wing_b,'none',stroke)],{x:353,y:331,scale:.35,angle:0}),line(S,[[295,426],[406,426]],C.ink,4)];
  const ink=[S.rect(463,246,76,46,C.ink),S.rect(469,253,64,5,C.mid),line(S,[[501,292],[501,335]],C.ink,8),S.rect(456,334,94,9,C.ink),groupPose(S,M,[S.polygon(G.G.bird.body,C.mid),S.polygon(G.G.bird.tail,C.mid)],{x:510,y:345,scale:.35,angle:0}),line(S,[[450,377],[553,377]],C.mid,3)];
  const eye=[S.rect(1203,320,28,40,C.ink),S.rect(1210,360,14,16,C.mid),line(S,[[1217,376],[1217,414]],C.ink,3),S.circle(1217,414,4,C.ink),S.rect(1187,435,64,5,C.stock),line(S,[[1190,441],[1190,477]],C.mid,3),line(S,[[1245,441],[1245,477]],C.mid,3)];
  return [S.group(cut,{'data-object':'W-cut-station','data-function':'guided-die-receiving-bed'}),S.group(ink,{'data-object':'W-ink-station','data-function':'reservoir-shaft-body-stamp-bed'}),S.group(eye,{'data-object':'W-eye-station','data-function':'bottle-shaft-punch-bed'})];
}
function gateShapes(ctx,m){
  const S=ctx.svg,M=ctx.math,g=S4_FIXED.gate,l=m.lamp.cap;
  const rail=[line(S,[[-80,0],[80,0]],C.ink,5),line(S,[[-80,18],[80,18]],C.ink,5),...[-55,0,55].map(x=>line(S,[[x,-3],[x,21]],C.ink,4))];
  const rodEndY=M.mix(-58.16-g.signal.capY,59.84,m.lamp.lift);
  const b=[S.group([S.rect(-110,110,250,14,C.stock),...ctx.geometry.G['P-C'].receiver_rectangles.map(r=>S.rect(...r,C.ink)),S.circle(-108,118,4,C.mid)],{'data-object':'P-C-carrier'}),
    S.path(ctx.geometry.G.A.path,C.mid,{'data-object':'A-arch'}),S.group(rail,{transform:'translate(0 116) scale(1.35)','data-object':'R-rail'}),
    S.group([S.path('M-22.44 0 A22.44 22.44 0 0 1 22.44 0 Z',C.amber),S.rect(-2.04,0,4.08,rodEndY,C.ink)],{transform:`translate(${l[0]} ${l[1]})`,'data-object':'L-signal'}),
    S.circle(...g.signal.mount,4.08,C.ink),line(S,[g.bar.pivot,g.bar.endpoint],C.ink,7,{'data-object':'G-bar'}),S.circle(...g.bar.pivot,7,C.ink),
    line(S,[g.bar.endpoint,[72,35],g.cam.center],C.ink,2,{'data-object':'G-linkage'}),S.circle(...g.cam.center,15,C.ink),S.circle(...g.cam.pin,4,C.amber),
    S.circle(...g.hang.map((v,i)=>(v-[750,386][i])/1.8),3,C.ink,{'data-object':'F-hang-pin'})];
  if(m.lamp.lift>0)b.push(line(S,[[l[0],l[1]+rodEndY],g.signal.mount],C.ink,2,{'data-object':'L-original-mount-link'}));
  return S.group(b,{transform:S.matrixString(m.carrier.gateMatrix),'data-object':'G-assembly'});
}

function drawCanonicalTransport(ctx,m){
  const S=ctx.svg,c=ctx.geometry.PALETTE,r={center:[1145,440],scale:.72,rotation:m.q.rotation,stop:{origin:[1059.32,473.84],points:[[1059.32,473.84],[1044.9199999999998,497.59999999999997],[1068.6799999999998,497.59999999999997]]}},guide=m.guide;
  const n=[S.group([S.line([[guide.x,guide.y],[guide.x+guide.width,guide.y]],c.mid,3),S.line([[guide.x,guide.y+13],[guide.x+guide.width,guide.y+13]],c.grey,3),...[.15,.4,.65,.9].map(u=>S.line([[guide.x+guide.width*u,guide.y+13],[guide.x+guide.width*u,guide.y+25],[guide.x+guide.width*u+13,guide.y+25]],c.mid,2))],{'data-object':'W-guide'})];
  for(const [x,y] of [[330,533.2],[1040,490]])n.push(S.group([S.rect(x-90,y,180,12,c.stock),S.line([[x-75,y+12],[x-50,y+100]],c.ink,8),S.line([[x+75,y+12],[x+50,y+100]],c.ink,8)],{transform:'translate(20 16)','data-object':x===330?'origin-support':'landing-support'}));
  const plate=S4_FIXED.landing.plate;
  n.push(S.polygon([plate[0],plate[1],[plate[1][0],518],[plate[0][0],518]],c.stock,{'data-object':'landing-pressure-plate'}));
  n.push(S.line(plate,c.mid,1.5),S.line([S4_FIXED.lever.plateJoint,S4_FIXED.lever.input,S4_FIXED.lever.pivot,S4_FIXED.lever.output],c.ink,3,{'data-object':'Q-release-lever'}),S.circle(...S4_FIXED.lever.pivot,4,c.ink));
  n.push(S.line([S4_FIXED.lever.output,r.stop.origin],c.ink,2,{'data-object':'Q-stop-link'}));
  n.push(S.group([S.circle(0,0,77,c.ink),S.circle(0,0,68,c.stock),S.circle(0,0,48,c.paper),S.circle(0,0,36,c.pale),
    S.group([S.path('M-59 -22 A63 63 0 0 1 52 -33 M-47 36 A57 57 0 0 0 46 37','none',{stroke:c.mid,'stroke-width':2})],{transform:`rotate(${r.rotation})`,'data-object':'Q-rotating-stock'}),S.circle(0,0,12,c.ink)
  ],{transform:S.matrixString(ctx.math.poseMatrix({x:r.center[0],y:r.center[1],scale:r.scale,angle:0})),'data-object':'Q-reel'}));
  n.push(S.line([[r.center[0]-66*r.scale,r.center[1]+78*r.scale],[r.center[0]+66*r.scale,r.center[1]+78*r.scale]],c.ink,7,{'data-object':'Q-shelf'}));
  n.push(S.polygon(r.stop.points,c.pale,{'data-object':'Q-stop'}));
  return n;
}
function renderScene(ctx,{globalFrame,localSceneFrame,layerId}){
  const S=ctx.svg,M=ctx.math,E=ctx.effectHelpers,G=ctx.geometry,m=ctx.state.mechanics,defs=[],world=[],screen=[];
  const f=m.effectiveFrame;
  if(layerId==='T02'){
    world.push(drawFar(ctx));
    const rid=ctx.id('station-reveal');
    defs.push(S.node('clipPath',{id:rid,clipPathUnits:'userSpaceOnUse'},[S.rect(270,205,1000,Math.max(.001,280*m.stationReveal),C.paper)]));
    if(m.stationReveal>0)world.push(S.group(toolStations(ctx,m),{'clip-path':`url(#${rid})`,'data-object':'W-remembered-stations'}));
  } else if(layerId==='T03'){
    const maskId=ctx.id('carrier-foreground-visible');
    defs.push(E.structuralMask(maskId,[-300,-300,2400,1600],f>=2280?m.gateOccluders:[]));
    const parts=[gateShapes(ctx,m)];
    if(f>=2280&&f<2700){
      m.feet.forEach((p,i)=>{
        const w=92+18*(1-M.progress(f,2280,2700))+3*Math.abs(m.ripple),h=5+1.5*m.curlPhase;
        const patch=E.contactPatch({id:ctx.id(`foot-contact-${i}`),bounds:[p[0]-w/2-9,p[1]-6,w+18,h+19],sigma:2.6-1.3*M.smoothstep(M.progress(f,2610,2700)),shape:S.rect(p[0]-w/2,p[1]-2,w,h,C.ink),enabled:ctx.effects.local_blur});
        defs.push(...patch.defs);parts.push(...patch.nodes);
      });
    }
    world.push(f>=2280?S.group(parts,{mask:`url(#${maskId})`,'data-layer-role':'middle-gate-carrier'}):parts);
  } else if(layerId==='T04'){
    world.push(...drawCanonicalTransport(ctx,m));
    world.push(S.path(m.feedPath,C.pale,{'data-object':'Q-continuous-feed'}));
    const shift=mul(sub(m.feedEnd,[110,556]),M.smoothstep(M.progress(f,2160,2280)));
    for(let i=0;i<3;i++)world.push(line(S,[[200+130*i,517],[208+130*i,522],[205+130*i,557]].map(p=>add(p,shift)),C.mid,1.2,{'data-object':`Q-feed-crease-${i}`}));
    world.push(S.path(m.feedInsetPath,C.paper,{'data-object':'Q-feed-inset'}));
    world.push(S.group([S.polygon([[300,506],[300,486],[332,467],[354,486],[354,506]],C.stock,{'data-object':'first-tongue'}),line(S,[[300,506],[354,506]],C.mid,2),line(S,[[777,506],[847,506]],C.mid,2)],{transform:S.matrixString(M.translate(...shift))}));
    const j=m.forceChain.paperAnchor;
    world.push(S.group([line(S,[add(j,[-101,33]),add(j,[-54,33]),add(j,[-35,0]),j],C.ink,5),S.circle(...add(j,[-54,33]),6,C.ink),S.circle(...j,5,C.ink),S.circle(...j,2,C.red)],{'data-object':'Q-P-buckle'}));
  } else if(layerId==='T06'){
    world.push(S.group([S.polygon([[-110,124],[140,124],[132,132],[-102,132]],C.pale,{'data-object':'P-A-folded-reserve'}),S.polygon([[-102,132],[132,132],[124,140],[-94,140]],C.pale,{'data-object':'P-B-folded-reserve'}),line(S,[[-110,124],[140,124]],C.mid,1),line(S,[[-102,132],[132,132]],C.mid,1)],{transform:S.matrixString(m.carrier.carrierMatrix),'data-owner':'T06'}));
    const paperMask=ctx.id('A-under-B'),seamId=ctx.id('shared-fold-seam');
    defs.push(E.structuralMask(paperMask,[-300,-300,2400,1600],f>=2280?[S.path(m.planes.PB.path,C.paper)]:[]));
    world.push(S.polygon(m.planes.PC.points,C.pale,{'data-object':'P-C-to-P-A-connector'}),line(S,[m.planes.PC.points[0],m.planes.joints.PC_PA[0]],C.mid,2),line(S,[m.planes.PC.points[1],m.planes.joints.PC_PA[1]],C.mid,2));
    world.push(S.path(m.planes.PA.path,C.pale,{'data-object':'P-A'}),S.path(m.planes.PB.path,C.stock,{'data-object':'P-B'}));
    const aOutline=line(S,[...m.planes.joints.PC_PA,...m.planes.joints.PA_PB.toReversed(),m.planes.joints.PC_PA[0]],C.mid,2);
    world.push(f>=2280?S.group([aOutline],{mask:`url(#${paperMask})`}):aOutline,line(S,m.planes.joints.PA_PB,C.mid,2));
    const h=m.planes.joints.PA_PB,v=sub(h[1],h[0]),len=Math.hypot(...v);
    defs.push(E.seamGradient({id:seamId,bounds:[0,0,Math.min(260,len),m.seamWidth],enabled:ctx.effects.gradient_alpha_reveal}));
    world.push(S.group([S.polygon([[0,0],[len,0],[len+9*(1-m.planes.projection.aOpening),m.seamWidth],[9*(1-m.planes.projection.aOpening),m.seamWidth]],`url(#${seamId})`)],{transform:S.matrixString(M.multiply(M.translate(...h[0]),M.rotate(deg(Math.atan2(v[1],v[0]))))),'data-effect':'local-paper-seam'}));
  } else if(layerId==='T05'){
    const maskId=ctx.id('frame-front-paper-mask');
    defs.push(E.structuralMask(maskId,[-300,-300,2400,1600],f>=2280?[S.path(m.planes.PB.path,C.paper)]:[]));
    const pieces=[];
    // Every corner has the same persistent ID, including the four folded reserves.
    if(f<=2400){
      pieces.push(S.group([line(S,[[0,36],[9,40],[13,26],[22,30],[26,16],[35,20],[39,6]],C.red,3),...[3,2,1,0].map(i=>S.group(frameCorner(S,G),{transform:S.matrixString(M.multiply(M.translate(i*13,-i*10),M.scale(.5))),'data-object':`F-corner-${i}`}))],{transform:S.matrixString(m.frame.foldMatrix),'data-object':'F-folded'}));
    } else {
      if(f<2520){
        const reserve=[[0,36],[9,40],[13,26],[22,30],[26,16],[35,20],[39,6]].map(p=>M.transform(p,m.frame.corners[2].matrix));
        pieces.push(line(S,reserve,C.red,3,{opacity:1-M.smoothstep(M.progress(f,2400,2520)),'data-object':'F-folded-reserve'}));
      }
      for(const corner of m.frame.corners.toReversed())pieces.push(S.group(frameCorner(S,G),{transform:S.matrixString(corner.matrix),'data-object':corner.id}));
    }
    if(f>2400){
      const cs=m.frame.corners;
      // Clockwise edges use transformed endpoints of the original four corner
      // arms. Control points change, not merely the group's CSS transform.
      const order=[1,2,3,0];
      for(let j=0;j<4;j++){
        const a=cs[order[j]],b=cs[order[(j+1)%4]],start=M.transform([72,7],a.matrix),end=M.transform([7,72],b.matrix);
        const bend=(1-M.smoothstep(M.progress(f,2450,2700)))*(j%2?32:-32);
        const actual=cubicEdge(S,start,end,bend),rigid=cubicEdge(S,start,end,0),edge=ctx.effects.path_deformation?actual:rigid;
        const revealId=ctx.id(`F-edge-${j}-reveal`),reveal=M.smoothstep(M.progress(f,2400+j*15,2505+j*15));
        const box=[Math.min(start[0],end[0])-80,Math.min(start[1],end[1])-80,Math.abs(end[0]-start[0])+160,Math.abs(end[1]-start[1])+160];
        // Actual local reveal mask follows the edge, with one dark folded portion.
        const cut=lerp(start,end,reveal),normal=sub(end,start),n=Math.hypot(...normal)||1,axis=[normal[0]/n,normal[1]/n],perp=[-axis[1],axis[0]];
        const revealPoly=[add(add(start,mul(axis,-100)),mul(perp,-100)),add(cut,mul(perp,-100)),add(cut,mul(perp,100)),add(add(start,mul(axis,-100)),mul(perp,100))];
        defs.push(S.node('mask',{id:revealId,x:box[0],y:box[1],width:box[2],height:box[3],maskUnits:'userSpaceOnUse','mask-type':'luminance'},[S.rect(...box,'black'),S.polygon(revealPoly,'white')]));
        pieces.push(S.path(edge.d,'none',{stroke:C.red,'stroke-width':5,'stroke-linecap':'round',...(ctx.effects.material_masks?{mask:`url(#${revealId})`}:{}),'data-object':`F-edge-${j}`}));
      }
    }
    if(f<2520){const release=M.smoothstep(M.progress(f,2490,2520));pieces.push(line(S,[m.frame.pullPoint,lerp(m.frame.beak,m.frame.pullPoint,release)],C.red,5,{'data-object':'B-F-pull-tab'}));}
    pieces.push(G.forceChainShapes(m.forceChain).tongue);
    world.push(f>=2280?S.group(pieces,{mask:`url(#${maskId})`,'data-layer-role':'continuous-red-frame'}):pieces);
  } else if(layerId==='T09'&&f>=2280){
    // This sparse owner enters as the same operator comes from behind the support.
    const rid=ctx.id('operator-support-reveal');
    defs.push(S.node('clipPath',{id:rid,clipPathUnits:'userSpaceOnUse'},[S.rect(m.operator.x-110,m.operator.y-110,220,Math.max(.001,180*m.operator.enter),C.paper)]));
    world.push(S.group([operatorShapes(ctx,m)],{'clip-path':`url(#${rid})`}));
  }
  // T07/T08 are exclusively emitted by the mandatory shared runtime bird path.
  return {defs,world:world.flat(),screen};
}
module.exports=Object.freeze({sceneId,implemented,resolveSceneState,renderScene});

},
"scenes/07-source-layers.js": function(module,exports,require){
'use strict';
/** S7 reuses the actual S6 world. Production remains fail-closed until the
 * approved glyph asset, actual final timeline binding and review are integrated.
 */
function withWorld(WORLD){
if(!WORLD||WORLD.sceneId!=='S6'||typeof WORLD.resolveSceneState!=='function'||typeof WORLD.renderScene!=='function')throw new Error('S7 requires the S6 scene contract');
const sceneId='S7',implemented=false,START=2760,END=3240;
const PERSISTENT=['T02','T03','T04','T05','T06','T09'];
const INK='#252B29',PAPER='#F1EDE1',MID='#90998E',RED='#D9533B';
const EMPTY=Object.freeze({defs:Object.freeze([]),world:Object.freeze([]),screen:Object.freeze([])});
const add=(a,b)=>[a[0]+b[0],a[1]+b[1]];
const sub=(a,b)=>[a[0]-b[0],a[1]-b[1]];
const mul=(a,b)=>[a[0]*b,a[1]*b];
const lerp=(a,b,t)=>[a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t];
function inverse(m){
  const det=m[0]*m[3]-m[1]*m[2];
  if(!Number.isFinite(det)||Math.abs(det)<1e-12)throw new Error('Singular S7 transform');
  return [m[3]/det,-m[1]/det,-m[2]/det,m[0]/det,(m[2]*m[5]-m[3]*m[4])/det,(m[1]*m[4]-m[0]*m[5])/det];
}
function stateFrame(f){return f>=2940&&f<3090?2940:f;}
function absent(layer,f){return (layer==='T05'&&f>=2985&&f<3015)||(layer==='T03'&&f>=3045&&f<3075);}
function withdrawal(M,layer,f){
  const q=layer==='T05'?[2970,2985,3015,3030,1]:layer==='T03'?[3030,3045,3075,3090,-1]:null;
  if(!q||f<q[0]||f>=q[3])return [0,0];
  const u=f<q[1]?M.smoothstep(M.progress(f,q[0],q[1])):f<q[2]?1:1-M.smoothstep(M.progress(f,q[2],q[3]));
  return [q[4]*1800*u,0];
}
function resolveSceneState(ctx,{globalFrame,localSceneFrame}){
  if(!Number.isInteger(globalFrame)||globalFrame<START||globalFrame>=END)throw new RangeError('S7 frame must be in [2760,3240)');
  if(localSceneFrame!==globalFrame-START)throw new Error('S7 local/global frame mismatch');
  if(!WORLD.implemented)throw new Error('S7 requires integrated actual S6; scaffold substitution forbidden');
  const M=ctx.math,S=ctx.svg,f=stateFrame(globalFrame);
  const base=WORLD.resolveSceneState(ctx,{globalFrame:2759,localSceneFrame:599});
  const cameraMatrix=base.cameraMatrix,camInv=inverse(cameraMatrix);
  const separation=M.keys(f,ctx.assets.motion.S7_separation.progress_keys);
  const regroup=M.smoothstep(M.progress(f,3090,3180));
  const ending=M.smoothstep(M.progress(f,3180,3239)),miniScale=M.mix(1,.66,ending);
  // One miniature transform for original world only. B and F are excluded.
  const miniScreen=[miniScale,0,0,miniScale,640*(1-miniScale),485*(1-miniScale)];
  const miniWorld=ending===0?M.IDENTITY:M.multiply(camInv,M.multiply(miniScreen,cameraMatrix));
  const recipe=ctx.assets.motion.S7_separation.group_offsets_at_one,matrices={},offsets={};
  for(const layer of PERSISTENT){
    const offset=mul(recipe[layer==='T09'?'T09_operator':layer],separation);
    offsets[layer]=offset;
    const translated=separation===0?M.IDENTITY:M.multiply(camInv,M.multiply(M.translate(...offset),cameraMatrix));
    matrices[layer]=layer==='T05'?translated:M.multiply(miniWorld,translated);
  }
  // Same corner IDs/rotations/scale; inherited S6 draws their real joining paths.
  const cornerTargets=[[150,569],[150,156],[1130,156],[1130,569]];
  const corners=base.mechanics.frame.corners.map((corner,i)=>{
    if(ending===0)return corner;
    const screen=M.transform(corner.origin,cameraMatrix),p=M.transform(lerp(screen,cornerTargets[i],ending),camInv);
    return {...corner,origin:p,matrix:M.poseMatrix({x:p[0],y:p[1],scale:corner.scale,angle:corner.angle})};
  });
  const baseBird=base.bird,baseScreen=M.transform([baseBird.pose.x,baseBird.pose.y],cameraMatrix);
  let birdScreen=add(baseScreen,mul(recipe.B,separation));
  if(f>=3090){
    const beakOffset=M.transform([140,-33],M.poseMatrix({x:0,y:0,scale:baseBird.pose.scale*cameraMatrix[0],angle:-3}));
    const contactRoot=sub([1110,104],beakOffset);
    birdScreen=lerp(add(baseScreen,recipe.B),contactRoot,regroup);
    if(f>=3180)birdScreen=lerp(contactRoot,[1045,248],ending);
  }
  const root=M.transform(birdScreen,camInv);
  const bird=globalFrame===2760?baseBird:{...baseBird,pose:{...baseBird.pose,x:root[0],y:root[1]},wing:M.mix(baseBird.wing,6,ending),
    occluders:baseBird.occluders.map(n=>S.group([n],{transform:S.matrixString(matrices.T06)}))};
  // A narrow changing peel follows the existing P-A/P-B material hinge.
  const peelU=M.progress(f,2820,2910),peelWidth=15*Math.sin(Math.PI*peelU);
  const h=base.mechanics.planes.joints.PA_PB,edge=sub(h[1],h[0]);
  const length=Math.hypot(...edge),normal=[edge[1]/length,-edge[0]/length];
  const center=lerp(h[0],h[1],peelU),reach=length*.32;
  const pa=add(center,mul(edge,-.32)),pb=add(center,mul(edge,.32));
  const peelPath=ctx.effects.path_deformation?`M${S.points([pa])} C${S.points([add(pa,mul(normal,peelWidth)),add(pb,mul(normal,peelWidth)),pb])} L${S.points([add(pb,mul(normal,-1)),add(pa,mul(normal,-1))])} Z`:
    S.pathPoints([pa,add(pa,mul(normal,peelWidth*.5)),add(pb,mul(normal,peelWidth*.5)),pb],true);
  const support=f>3090&&f<3180?Math.sin(Math.PI*M.progress(f,3090,3180)):base.mechanics.operator.handHold;
  const mechanics={...base.mechanics,operator:{...base.mechanics.operator,handHold:support},frame:{...base.mechanics.frame,corners}};
  const originalEndpoint=M.transform([0,36],corners[0].matrix),originalAnchor=base.mechanics.forceChain.paperAnchor;
  const miniAnchor=M.transform(originalAnchor,miniWorld);
  // Existing tongue retracts onto its lower-left reserve during ending retreat.
  // It is never duplicated into the miniature or relabeled as another F.
  const paperAnchor=ending>0?lerp(miniAnchor,add(originalEndpoint,[10,10]),M.smoothstep(M.progress(f,3180,3210))):originalAnchor;
  mechanics.forceChain=ending===0?base.mechanics.forceChain:ctx.geometry.forceChain({frameEndpoint:originalEndpoint,paperAnchor});
  return M.deepFreeze({...base,cameraMatrix,bird,mechanics,sceneId,
    sourceLayers:{stateFrame:f,separation,regroup,ending,offsets,matrices,miniScreen,miniWorld,
      originalScene:'S6',originalFrame:2759,peel:{path:peelPath,width:peelWidth,center,normal,reach},
      labelExit:M.smoothstep(M.progress(f,3090,3150)),leaderExit:M.smoothstep(M.progress(f,3090,3180))},
    productionReadiness:'INCOMPLETE_FONT_REVIEW_AND_ACTUAL_FINAL_SOURCE_BINDING'});
}
function remapMask(ctx,node,layerMatrix){
  const S=ctx.svg,M=ctx.math,front=ctx.state.sourceLayers.matrices.T06,name=node.attrs?.id||'';
  if(node.tag==='mask'&&(name.endsWith('-carrier-foreground-visible')||name.endsWith('-frame-front-paper-mask'))){
    const relative=M.multiply(inverse(layerMatrix),front);
    return S.node(node.tag,node.attrs,[node.children[0],S.group(node.children.slice(1),{transform:S.matrixString(relative)})]);
  }
  return node;
}
function renderInherited(ctx,args){
  const S=ctx.svg,M=ctx.math,E=ctx.effectHelpers,layer=args.layerId,sl=ctx.state.sourceLayers;
  const inherited=WORLD.renderScene(ctx,args),ownMatrix=sl.matrices[layer];
  const extra=withdrawal(M,layer,args.globalFrame),camInv=inverse(ctx.state.cameraMatrix);
  const withdrawMatrix=M.multiply(camInv,M.multiply(M.translate(...extra),ctx.state.cameraMatrix));
  const currentMatrix=M.multiply(withdrawMatrix,ownMatrix);
  const defs=inherited.defs.map(n=>remapMask(ctx,n,currentMatrix));
  let shapes=inherited.world;
  if(layer==='T06'&&sl.peel.width>1e-10){
    const maskId=ctx.id('s7-local-paper-peel'),gradId=ctx.id('s7-peel-alpha');
    defs.push(E.mask(maskId,[-300,-300,2400,1600],[S.path(sl.peel.path,'black')],ctx.effects.material_masks));
    shapes=[S.group(shapes,{mask:`url(#${maskId})`,'data-effect':'bounded-material-peel'})];
    const p=sl.peel.center,w=Math.max(2,sl.peel.width);
    defs.push(E.seamGradient({id:gradId,bounds:[p[0],p[1]-sl.peel.reach,w,2*sl.peel.reach],enabled:ctx.effects.gradient_alpha_reveal}));
    shapes.push(S.path(sl.peel.path,`url(#${gradId})`,{'data-effect':'bounded-peel-alpha'}));
  }
  if(layer==='T05'){
    const f=sl.stateFrame,pull=M.smoothstep(M.progress(f,2760,2790))*(1-M.smoothstep(M.progress(f,2850,2910)));
    if(pull>0){
      const corner=ctx.state.mechanics.frame.corners[2].origin;
      const target=M.transform(M.transform([140,-33],M.poseMatrix(ctx.state.bird.pose)),inverse(ownMatrix));
      const start=add(corner,[-8,15]),end=lerp(start,target,pull);
      const controls=ctx.effects.path_deformation?[start,add(start,[-28,35]),add(end,[20,-6]),end]:[start,lerp(start,end,1/3),lerp(start,end,2/3),end];
      shapes=[...shapes,S.path(S.cubicPath(controls),'none',{stroke:RED,'stroke-width':3,'data-object':'F-source-paper-ear'})];
    }
  }
  return {defs,world:[S.group(shapes,{transform:S.matrixString(currentMatrix),'data-s7-source-owner':layer})],screen:inherited.screen};
}
function sourceRecord(ctx,id){
  const records=ctx.sourceFields?.filter(x=>x.id===id)||[];
  if(records.length!==1)throw new Error(`S7 needs one bound source record: ${id}`);
  const value=records[0],track=id==='S7.middle'?'T03':'T05';
  if(value.track_id!==track||typeof value.fields?.src!=='string'||!value.fields.src||value.fields.src!==value.clip_src)throw new Error(`Invalid source binding: ${id}`);
  if(id==='S7.middle'&&(!Number.isInteger(value.fields.dur)||value.fields.dur<=0))throw new Error('S7 duration requires its bound integer field');
  return value;
}
function sourceLayout(ctx){
  const M=ctx.math,sl=ctx.state.sourceLayers,f=sl.stateFrame;
  const reflow=M.smoothstep(M.progress(f,2820,2910)),exit=sl.labelExit,camera=ctx.state.cameraMatrix;
  const middleAnchor=M.transform(M.transform([-50,-76],ctx.state.mechanics.carrier.gateMatrix),M.multiply(camera,sl.matrices.T03));
  const frameAnchor=M.transform(M.transform(ctx.state.mechanics.frame.corners[2].origin,sl.matrices.T05),camera);
  // Paper slips follow source-owner anchors. They are not fake application UI.
  const middleOrigin=add(middleAnchor,[-205-470*exit,-113-95*exit]);
  // Keep the slip above both red wing tips; it must not become an accidental
  // opaque foreground mask over the required shared B silhouette.
  const frameOrigin=add(frameAnchor,[-270+430*exit,11-70*exit]);
  const middleBox=[...middleOrigin,M.mix(344,226,reflow),M.mix(34,57,reflow)],frameBox=[...frameOrigin,226,34];
  const fields=sourceRecord(ctx,'S7.middle'),frame=sourceRecord(ctx,'S7.frame');
  return M.deepFreeze({reflow,exit,middle:{id:'S7.middle',owner:'T03',anchor:middleAnchor,bounds:middleBox,
      lines:[{text:`src: ${fields.fields.src.split('/').pop()}`,x:middleBox[0]+10,y:middleBox[1]+22,size:12.5},
        {text:`dur: ${fields.fields.dur}`,x:middleBox[0]+M.mix(240,10,reflow),y:middleBox[1]+M.mix(22,44,reflow),size:12.5,
          reveal:M.smoothstep(M.progress(f,2880,2910))}],fields:fields.fields},
    frame:{id:'S7.frame',owner:'T05',anchor:frameAnchor,bounds:frameBox,
      lines:[{text:frame.fields.src.split('/').pop(),x:frameBox[0]+10,y:frameBox[1]+22,size:12.5}],fields:frame.fields}});
}
function leader(ctx,item,exit){
  const S=ctx.svg,b=item.bounds,end=item.owner==='T03'?[b[0]+b[2]-16,b[1]+b[3]]:[b[0]+b[2],b[1]+b[3]/2],anchor=item.anchor;
  const controls=item.owner==='T03'?[anchor,add(anchor,[-18,-20]),add(end,[15,27]),end]:[anchor,add(anchor,[34,10]),add(end,[28,-3]),end];
  return S.group([S.path(S.cubicPath(controls),'none',{stroke:MID,'stroke-width':1.25,'data-source-binding':item.id}),
    S.circle(...anchor,2.4,INK),S.rect(...b,PAPER,{'data-source-slip':item.id}),
    S.line([[b[0],b[1]+b[3]],[b[0]+b[2],b[1]+b[3]]],MID,.8)],{opacity:1-exit,'data-owner':item.owner});
}
function fontProvider(ctx){
  const font=ctx.assets.fonts?.sourceLabels;
  if(!font)return null;
  if(font.format!=='framivra-glyph-paths/1'||!(font.unitsPerEm>0)||!font.glyphs||font.provenance?.license!=='OFL-1.1'||
    !/^[a-f0-9]{64}$/.test(font.provenance.fontSha256||'')||!/^[a-f0-9]{64}$/.test(font.provenance.licenseSha256||''))throw new Error('Unproven S7 glyph-path provider');
  return font;
}
function glyphRun(ctx,font,{text,x,y,size}){
  const S=ctx.svg,nodes=[],scale=size/font.unitsPerEm;
  let cursor=x,minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  for(const character of text){
    const glyph=font.glyphs[character];
    if(!glyph||!Number.isFinite(glyph.advance)||glyph.advance<=0||typeof glyph.path!=='string')throw new Error(`Missing verified source-label glyph U+${character.codePointAt(0).toString(16)}`);
    if(glyph.path){
      if(!Array.isArray(glyph.bounds)||glyph.bounds.length!==4||!glyph.bounds.every(Number.isFinite))throw new Error('Unbounded glyph outline');
      const [a,b,c,d]=glyph.bounds;
      minX=Math.min(minX,cursor+a*scale);maxX=Math.max(maxX,cursor+c*scale);
      minY=Math.min(minY,y-d*scale);maxY=Math.max(maxY,y-b*scale);
      nodes.push(S.path(glyph.path,INK,{transform:S.matrixString([scale,0,0,-scale,cursor,y])}));
    }
    cursor+=glyph.advance*scale;
  }
  return {node:S.group(nodes,{'data-bound-text':text,'data-font-sha256':font.provenance.fontSha256}),
    bounds:minX===Infinity?[x,y,0,0]:[minX,minY,maxX-minX,maxY-minY],advance:cursor-x};
}
function sourceReadiness(ctx){
  const font=fontProvider(ctx),actual=ctx.sourceBindingStatus==='ACTUAL_FINAL_TIMELINE';
  return {geometryImplemented:true,typographyAvailable:!!font,typographyApproved:font?.reviewStatus==='INDEPENDENTLY_APPROVED',
    actualFinalBinding:actual,productionImplemented:false,
    blockers:[...(!font?['APPROVED_LOCAL_GLYPH_PROVIDER_REQUIRED']:font.reviewStatus!=='INDEPENDENTLY_APPROVED'?['GLYPH_PROVIDER_INDEPENDENT_REVIEW_REQUIRED']:[]),
      ...(!actual?['ACTUAL_FINAL_TIMELINE_SOURCE_BINDING_REQUIRED']:[]),'SCENE_REVIEW_AND_INTEGRATOR_READINESS_GATE_REQUIRED']};
}
function renderScene(ctx,args){
  const {globalFrame,layerId}=args;
  if(absent(layerId,globalFrame))return EMPTY;
  if(PERSISTENT.includes(layerId))return renderInherited(ctx,args);
  // Shared runtime is the ONLY owner of T07/T08. No B glyph appears here.
  if(layerId!=='T12'&&layerId!=='T14')return EMPTY;
  if(globalFrame<2820||globalFrame>=3180||(layerId==='T14'&&globalFrame>=3150))return EMPTY;
  const S=ctx.svg,M=ctx.math,E=ctx.effectHelpers,layout=sourceLayout(ctx),f=ctx.state.sourceLayers.stateFrame;
  const entry=M.smoothstep(M.progress(f,2820,2850));
  if(layerId==='T12')return {defs:[],world:[],screen:[S.group([leader(ctx,layout.middle,ctx.state.sourceLayers.leaderExit),leader(ctx,layout.frame,ctx.state.sourceLayers.leaderExit)],{opacity:entry})]};
  const font=fontProvider(ctx);
  if(!font)return EMPTY; // No system-font fallback or invented outline substitute.
  const defs=[],screen=[];
  for(const item of [layout.middle,layout.frame]){
    const id=ctx.id(`${item.owner}-source-text-reveal`);
    defs.push(E.textReveal({id,bounds:item.bounds,progress:M.progress(f,2820,2880),enabled:ctx.effects.text_reveal}));
    const nodes=item.lines.map(line=>{
      const run=glyphRun(ctx,font,line),[x,y,w,h]=run.bounds,b=item.bounds;
      if(x<b[0]-.001||y<b[1]-.001||x+w>b[0]+b[2]+.001||y+h>b[1]+b[3]+.001)throw new Error(`Source glyph bounds escape ${item.id}`);
      return S.group([run.node],{opacity:line.reveal??1});
    });
    screen.push(S.group(nodes,{'clip-path':`url(#${id})`,opacity:1-layout.exit,
      'data-source-binding':item.id,'data-source-binding-status':ctx.sourceBindingStatus||'UNBOUND',
      'data-typography-status':font.reviewStatus||'UNREVIEWED'}));
  }
  return {defs,world:[],screen};
}
return Object.freeze({sceneId,implemented,resolveSceneState,renderScene,sourceLayout,sourceReadiness,glyphRun,withdrawal,withWorld});
}
// Default final-bundle path. withWorld is a pure dependency factory for isolated
// author tests and downstream scenes; it does not mutate shared module state.
module.exports=withWorld(require('./06-unfold-world.js'));

},
"scenes/08-resolve.js": function(module,exports,require){
'use strict';
/** S8: one inherited world, one inherited F, and the mandatory shared B rig.
 * Default dependencies are final-bundle relative paths. withSourceScene is a
 * pure dependency factory for isolated candidate tests, never a global override.
 * Typography remains fail-closed until the exact local outline asset is reviewed.
 */
function withSourceScene(SOURCE){
if(!SOURCE||SOURCE.sceneId!=='S7'||typeof SOURCE.resolveSceneState!=='function'||typeof SOURCE.renderScene!=='function')throw new Error('S8 requires the S7 scene contract');
const sceneId='S8',implemented=false,START=3240,END=3600;
const PERSISTENT=Object.freeze(['T02','T03','T04','T05','T06','T09']);
const MINI=Object.freeze(['T02','T03','T04','T06','T09']);
const EMPTY=Object.freeze({defs:Object.freeze([]),world:Object.freeze([]),screen:Object.freeze([])});
const BRAND='Framivra',INK='#252B29';
const add=(a,b)=>[a[0]+b[0],a[1]+b[1]],lerp=(a,b,t)=>a.map((v,i)=>v+(b[i]-v)*t);
function inverse(m){
  const d=m[0]*m[3]-m[1]*m[2];
  if(!Number.isFinite(d)||Math.abs(d)<1e-12)throw new Error('Singular S8 transform');
  return [m[3]/d,-m[1]/d,-m[2]/d,m[0]/d,(m[2]*m[5]-m[3]*m[4])/d,(m[1]*m[4]-m[0]*m[5])/d];
}
function resolveSceneState(ctx,{globalFrame,localSceneFrame}){
  if(!Number.isInteger(globalFrame)||globalFrame<START||globalFrame>=END)throw new RangeError('S8 frame must be in [3240,3600)');
  if(localSceneFrame!==globalFrame-START)throw new Error('S8 local/global frame mismatch');
  const M=ctx.math,S=ctx.svg,f=Math.min(globalFrame,3480),p=(a,b)=>M.smoothstep(M.progress(f,a,b));
  const base=SOURCE.resolveSceneState(ctx,{globalFrame:3239,localSceneFrame:479});
  if(!base.sourceLayers||!base.mechanics?.frame?.corners||!base.bird)throw new Error('S8 requires the actual S7 f3239 world; scaffold substitution forbidden');
  const start=f===3240,C0=base.cameraMatrix,cameraScale=M.mix(C0[0],.70,p(3240,3420));
  const fixedFocus=M.transform([640,360],inverse(C0));
  const cameraMatrix=start?C0:[cameraScale,0,0,cameraScale,640-cameraScale*fixedFocus[0],360-cameraScale*fixedFocus[1]];
  const invCamera=inverse(cameraMatrix),retreat=p(3240,3420);
  // One affine chart for ALL five remembered world owners. The original B/F are
  // excluded by owner before the chart is applied; there is no miniature clone.
  // Canonical S6 has a deeper original buckle/guide than the R03 illustration.
  // A 0.375 final chart (rather than obsolete0.39) keeps every real world owner
  // inside the inherited lower rail while preserving the intended center650.
  const nominalMini=ctx.assets.motion.ending.mini_world_matrix;
  const miniTarget=[.375,0,0,.375,nominalMini[4]+640*(nominalMini[0]-.375),nominalMini[5]];
  const miniScreen=start?base.sourceLayers.miniScreen:lerp(base.sourceLayers.miniScreen,miniTarget,retreat);
  const miniWorld=start?base.sourceLayers.miniWorld:M.multiply(invCamera,M.multiply(miniScreen,C0));
  const matrices=start?base.sourceLayers.matrices:Object.fromEntries(PERSISTENT.map(role=>[role,role==='T05'?M.IDENTITY:miniWorld]));
  const targets=[[140,578],[140,181],[1140,181],[1140,578]],stop=[3310,3290,3330,3330];
  // A single finite pressure pulse, exactly zero at first contact and after3372.
  const press=f>=3350&&f<=3372?3.2*Math.sin(Math.PI*(f-3350)/22)**2:0;
  const corners=start?base.mechanics.frame.corners:base.mechanics.frame.corners.map((corner,i)=>{
    let screen=lerp(M.transform(corner.origin,C0),targets[i],p(3240,stop[i]));
    if(i===2)screen=add(screen,[0,press]);
    const origin=M.transform(screen,invCamera);
    return {...corner,origin,matrix:M.poseMatrix({x:origin[0],y:origin[1],scale:corner.scale,angle:corner.angle})};
  });
  const topRight=M.transform(corners[2].origin,cameraMatrix);
  const support={owner:'T05',object:'F-corner-2',
    x0:topRight[0]-72*corners[2].scale*cameraScale,x1:topRight[0],y:topRight[1],
    thickness:20*corners[2].scale*cameraScale,contactFrame:3350};
  const baseRoot=M.transform([base.bird.pose.x,base.bird.pose.y],C0);
  const screenScale=M.mix(base.bird.pose.scale*C0[0],.78,p(3240,3330));
  // The real S7 checkpoint is below the top rail. A small rise puts B above the
  // same corner, followed by a decreasing-height approach and f3350 foot contact.
  // Final x1105/.78 is an explicit bounded correction to the obsolete R03 target:
  // both entire soles fit the inherited 0.70x corner and the beak stays <=1216.
  const finalX=1105,landingY=support.y-48*screenScale;
  let rootScreen;
  if(f<=3300)rootScreen=M.cubic([baseRoot,add(baseRoot,[25,-30]),[1092,108],[1105,122]],p(3240,3300));
  else rootScreen=[finalX,M.mix(122,landingY,p(3300,3350))];
  const angle=M.keys(f,[[3240,base.bird.pose.angle],[3275,-8],[3300,-4],[3330,0],[3480,0]]);
  const root=M.transform(rootScreen,invCamera);
  const baseMiniInverse=inverse(base.sourceLayers.matrices.T06);
  const occlusionDelta=M.multiply(miniWorld,baseMiniInverse);
  const bird=start?base.bird:{...base.bird,
    pose:{x:root[0],y:root[1],scale:screenScale/cameraScale,angle},
    feet:f>=3330,wing:M.keys(f,[[3240,base.bird.wing],[3260,-12],[3285,11],[3310,-6],[3330,4],[3350,6],[3420,6],[3450,0],[3480,6]]),
    occluders:base.bird.occluders.map(n=>S.group([n],{transform:S.matrixString(occlusionDelta)}))};
  const frameEndpoint=M.transform([0,36],corners[0].matrix);
  const chain=start?base.mechanics.forceChain:ctx.geometry.forceChain({frameEndpoint,paperAnchor:add(frameEndpoint,[10,10])});
  const mechanics=start?base.mechanics:{...base.mechanics,frame:{...base.mechanics.frame,corners},forceChain:chain};
  const footLocal=[[-18,48],[-1,48],[11,48],[29,48]],birdScreen=M.multiply(cameraMatrix,M.poseMatrix(bird.pose));
  const feet=footLocal.map(q=>M.transform(q,birdScreen));
  const sourceLayers=start?base.sourceLayers:{...base.sourceLayers,stateFrame:f,miniScreen,miniWorld,matrices};
  return M.deepFreeze({...base,sceneId,cameraMatrix,bird,mechanics,sourceLayers,
    ending:{effectiveFrame:f,miniatureOwners:MINI,frameOwner:'T05',birdOwners:['T07','T08'],
      inheritedCheckpoint:3239,retreat,press,support,feet,contact:f>=3350,
      wordmark:{text:BRAND,origin:[280,393],size:108,weight:600,reveal:M.progress(f,3360,3420),
        revealWindow:[3360,3420],stableWindow:[3420,3600]},quietHold:f>=3480,
      designCorrections:['CONTINUOUS_RISE_FROM_ACTUAL_S7_THEN_DESCENT_TO_CONTACT','BOTH_SOLES_SUPPORTED_BY_INHERITED_RIGHT_CORNER','FINAL_MINIATURE_SCALE_0_375_FITS_CANONICAL_WORLD_INSIDE_FRAME']},
    productionReadiness:'INCOMPLETE_BRAND_PROVIDER_REVIEW_AND_FINAL_INTEGRATION'});
}
function fontProvider(ctx){
  const font=ctx.assets.fonts?.brand;
  if(!font)return null;
  if(font.format!=='framivra-glyph-paths/1'||font.family!=='Noto Sans SC'||font.weight!==600||!(font.unitsPerEm>0)||
    font.provenance?.license!=='OFL-1.1'||font.provenance.text!==BRAND||
    !/^[0-9a-f]{64}$/.test(font.provenance.fontSha256||'')||!/^[0-9a-f]{64}$/.test(font.provenance.licenseSha256||''))throw new Error('Unproven S8 weight-600 brand outline provider');
  return font;
}
function brandRun(ctx,font){
  const S=ctx.svg,mark=ctx.state.ending.wordmark,scale=mark.size/font.unitsPerEm,nodes=[];
  let cursor=mark.origin[0],minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  for(const ch of BRAND){
    const glyph=font.glyphs?.[ch];
    if(!glyph||!Number.isFinite(glyph.advance)||glyph.advance<=0||typeof glyph.path!=='string'||!glyph.path||
       !Array.isArray(glyph.bounds)||glyph.bounds.length!==4||!glyph.bounds.every(Number.isFinite))throw new Error(`Missing verified brand glyph: ${ch}`);
    const [a,b,c,d]=glyph.bounds,y=mark.origin[1];
    minX=Math.min(minX,cursor+a*scale);maxX=Math.max(maxX,cursor+c*scale);
    minY=Math.min(minY,y-d*scale);maxY=Math.max(maxY,y-b*scale);
    nodes.push(S.path(glyph.path,INK,{transform:S.matrixString([scale,0,0,-scale,cursor,y])}));
    cursor+=glyph.advance*scale;
  }
  const bounds=[minX,minY,maxX-minX,maxY-minY];
  if(minX<64||minY<36||maxX>1216||maxY>600)throw new Error('Brand outline escapes film safety bounds');
  return {node:S.group(nodes,{'data-brand':BRAND,'data-font-sha256':font.provenance.fontSha256,'data-typography-status':font.reviewStatus||'UNREVIEWED'}),bounds,advance:cursor-mark.origin[0]};
}
function endingReadiness(ctx){
  const font=fontProvider(ctx);
  return {geometryImplemented:true,typographyAvailable:!!font,typographyApproved:font?.reviewStatus==='INDEPENDENTLY_APPROVED',productionImplemented:false,
    blockers:[...(!font?['APPROVED_LOCAL_BRAND_GLYPH_PROVIDER_REQUIRED']:font.reviewStatus!=='INDEPENDENTLY_APPROVED'?['BRAND_GLYPH_PROVIDER_INDEPENDENT_REVIEW_REQUIRED']:[]),
      'SCENE_REVIEW_AND_INTEGRATOR_READINESS_GATE_REQUIRED'],actualFinalAudio:'UNRUN',actualEngine:'UNRUN',performance:'UNRUN'};
}
function renderScene(ctx,args){
  const {layerId,globalFrame}=args;
  if(PERSISTENT.includes(layerId))return SOURCE.renderScene(ctx,args);
  // T07/T08 are rendered exclusively by the unchanged shared runtime.
  if(layerId!=='T15'||globalFrame<3360)return EMPTY;
  const font=fontProvider(ctx);
  if(!font)return EMPTY; // Explicitly no browser font lookup or fallback lettering.
  const S=ctx.svg,run=brandRun(ctx,font),id=ctx.id('S8-wordmark-reveal'),b=run.bounds;
  const clip=ctx.effectHelpers.textReveal({id,bounds:[b[0]-1,b[1]-1,b[2]+2,b[3]+2],progress:ctx.state.ending.wordmark.reveal,enabled:ctx.effects.text_reveal});
  return {defs:[clip],world:[],screen:[S.group([run.node],{'clip-path':`url(#${id})`,'data-owner':'T15'})]};
}
return Object.freeze({sceneId,implemented,resolveSceneState,renderScene,endingReadiness,brandRun,withSourceScene});
}
module.exports=withSourceScene(require('./07-source-layers.js'));

},
"scenes/index.js": function(module,exports,require){
'use strict';
module.exports = Object.freeze({
  S1: require('./01-opening.js'),
  S2: require('./02-materials.js'),
  S3: require('./03-time-ribbons.js'),
  S4: require('./04-gate-flight.js'),
  S5: require('./05-sound-paper.js'),
  S6: require('./06-unfold-world.js'),
  S7: require('./07-source-layers.js'),
  S8: require('./08-resolve.js'),
});

}};
const loaded=Object.create(null);
function resolve(parent,request){if(!request.startsWith('.'))throw new Error('Only bundled local modules');const parts=parent.split('/').slice(0,-1);for(const item of request.split('/')){if(item==='.'||item==='')continue;if(item==='..')parts.pop();else parts.push(item);}return parts.join('/');}
function load(id){if(loaded[id])return loaded[id].exports;if(!factories[id])throw new Error('Unknown bundled module '+id);const module={exports:{}};loaded[id]=module;factories[id](module,module.exports,request=>load(resolve(id,request)));return module.exports;}
global.FilmRuntime=Object.freeze(load('runtime/runtime.js'));
})(window);
