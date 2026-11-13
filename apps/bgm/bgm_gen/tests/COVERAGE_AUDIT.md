# COVERAGE_AUDIT · v3 新测试七组覆盖审计（R4-W2，2026-08-23 定稿）

> 任务：对照 PLAN_v3 §6 覆盖矩阵逐设计点查漏（§5 R4-W2 工作包）。
> **范围**：本 worktree（v3-r4-w2）现有 **六组** = test_descriptors / test_events /
> test_edit / test_dual_layout / test_show + test_catalog（后者为 R1-W1 随描述符
> 交付改写的既有文件，按 R1-W1 测试交付物一并审计）；**W1 两组**
> （test_smf_import / test_fate_data+render）**W1 交付、主线合流后复核**（§7 列
> 设计点备核，不由 W2 代写）。
> 计数口径（三档回归实测，pytest 参数化展开数）：**fast 258 / slow 10 / service 3
> = 271**（fast 258 = 残留审计误记 256 的订正 257 + 本次新增 catalog 同步锁 1；
> 257↔256 差 = test_dual_layout 版本门 4 键参数化 vs 3 个函数体的口径差）。
>
> 图例：✅=已覆盖（附用例 id）；➕=本次审计补测；✔=缺口已修复并加锁；
> ⚠=缺口/漂移（转主线）；↩=依赖主树产物（out/ gitignored），worktree 显式
> skip/环境性 fail、主树后跑。

## 0. 结论摘要

| 项 | 数 |
|---|---|
| §6 矩阵设计点（六组内，含通用规范条目） | 96（残留审计口径；逐表见 §1-§6） |
| 已覆盖（含跨文件覆盖） | 93 |
| 本次补测/修复（M） | **3**：test_events note_off vel=0 字节锚点；test_descriptors validate_param_value 直测；**F-1 修复=timbres.md 重落盘 + test_catalog 同步锁** |
| 缺口/漂移转主线（K） | **3**：F-2 qc 输入不存在报错形态偏原始；F-3 test_show fate fixture 复跑（依赖 W1）；F-4 W1 两组文件未到（§7，合流后复核） |
| 依赖主树产物后跑 | 5 处（§8：3 skip + 2 环境性 fail） |

另：任务 2 交付 **test_cli_negative.py 6 例**（stem/master/qc 负分支，清遗留债务 3），
负例清单与断言口径见该文件 docstring 与 §9。

## 0.5 三档回归实测（2026-08-23，本 worktree）

| 档 | 结果 | 耗时 | 备注 |
|---|---|---|---|
| fast | **258 passed** / 13 deselected | **69.2s**（≤75s 预算内，贴边） | R3 回归锁四件全绿（NaN 拒绝/XOR 门/undo 漂移/diff 数值折叠） |
| slow | 6 passed / 2 skipped / **2 failed（环境性）** | 25.0s | fail=test_engines 两例缺 demucs 输入（§8，主树在位后跑；guard 实测 peak RSS 0.49GB）；skip=descriptors demucs 探针+eden_track 交付物（显式理由） |
| service | 3 skipped | 0.1s | ComfyUI 下线（127.0.0.1:8188 不可达）→ 显式 skip 属设计行为（PLAN §10.6） |

## 1. test_descriptors.py（fast 57 / slow 2 / service 1）

| §6/§3.1 设计点 | 用例 | 状态 |
|---|---|---|
| schema 自洽：7 份加载/字段齐/参数 id 唯一/semver | test_descriptor_registry_self_consistent | ✅ |
| enum 必伴 stepped（C1） | 同上（内嵌断言） | ✅ |
| events 消费矩阵（synth/sample 全拒；sf2 拒 KS/PC；sfz 全集） | 同上 | ✅ |
| invariants 注册双向锁（INVARIANTS==INVARIANT_IDS） | 同上 + base.py assert | ✅ |
| coverage tier A/B/C + evidence/probes/gaps 强制 | 同上；sfz 锁 B 档 | ✅ |
| harvest 事实表 vs 磁盘深一致（防手工漂移） | test_descriptor_files_match_harvest_facts | ✅（矩阵外附加锁） |
| validate_payload 正例（12 组跨 7 引擎） | test_validate_payload_positive | ✅ |
| validate_payload 负例（单字段 27 组：required/type/enum/越界/NaN/inf） | test_validate_payload_negative | ✅ |
| invariants 正负（synth params 键集 / sample 双入口 / master matchering ref） | POS/NEG 参数化 | ✅ |
| 描述符缺失响亮报错 | test_validate_payload_missing_descriptor_fails_loud | ✅ |
| §3.6 params 槽单键校验 API（validate_param_value 同源复用） | test_validate_param_value_params_slot_api | ➕ 本次补测 |
| 行为探针 §3.1 语义（exit0+出参 7 字段+非零字节+determinism 分级） | test_behavior_probe ×9（synth/sf2/sfz/sample/master） | ✅ |
| 探针取值 {default, 边界近点}；program 合法组合 | PROBES（fm_ep 边界 ratio/idx/bright_decay+bpm200；sf2 bank128） | ✅ |
| 确定性双渲 sha 一致 | test_behavior_probe double_render=True 各例 | ✅ |
| doctor：error 拉红 / warn 黄灯不拉红（C2） | test_doctor_check_descriptors_error_flips_ok / …_warn_does_not_flip_ok | ✅ |
| doctor 报告含 descriptor.\<eng\> 与 .binary_pin 项；pin 恒 warn | test_doctor_report_contains_descriptor_items | ✅ |
| doctor 与 base 结构检查同源（损坏描述符显形，R1-B·D1） | test_doctor_load_descriptors_full_parity | ✅ |
| content_hash_key 键组装（payload/sr/channels/engine/三版本） | test_content_hash_key_semantics | ✅ |
| show engines --json 7 引擎字段齐 + text 表 | test_show_engines_json_and_text | ✅ |
| [slow] demucs 探针（缺输入显式 skip） | test_demucs_probe | ✅ ↩（worktree 无输入→skip） |
| [slow] master matchering+ref 条件组合 | test_master_matchering_probe | ✅（本 worktree 输入在位，已实跑过） |
| [service] ai 探针=注入点被 /object_info 接受 | test_ai_probe_params_accepted_by_object_info | ✅ ↩（ComfyUI down→skip） |

## 2. test_events.py（fast 36 / slow 4）

| §6/§3.2 设计点 | 用例 | 状态 |
|---|---|---|
| event_sort_key 全序（类型序/pitch 升序/插入序；t 压过类型序） | test_event_sort_key_total_order | ✅ |
| 同 tick 浮点边界 note_off 先于 on（R2-A·1 回归） | test_same_tick_float_boundary_note_off_before_on | ✅ |
| bank-PC 复合 CC0/32 紧贴 0xC0 前（C3a；bank128=GM 打击） | test_bank_pc_composite_cc0_cc32_before_pc | ✅ |
| 相邻同 tick 双 KS on/off/on/off +1 tick 错开（P8） | test_adjacent_same_tick_double_ks_stagger | ✅ |
| KS off tick 三段钳制 + beat0 边界 | test_keyswitch_off_tick_clamped_by_next_ks | ✅ |
| cc round-half-up（防 banker's）+ clamp | test_cc_round_half_up_and_clamp | ✅ |
| 弯音 14bit 线性映射 + clamp + bend_range 变量 | test_pitch_bend_14bit_roundtrip_and_clamp | ✅ |
| ppq 入参（384 整数倍） | test_ppq_param_effective | ✅ |
| write_notes_smf .mid 直接 sha 锚点（旧路径字节不动，红线⑤） | test_write_notes_smf_mid_sha_anchor（双锚：ch0/ch9+prog） | ✅ |
| notes_to_events 形状（_dur/_on_t） | test_notes_to_events_shape | ✅ |
| note_off 编码 velocity=0（与 Trk.note 出口一致，§3.2 类型表） | test_note_off_velocity_zero_byte_anchor | ➕ 本次补测（新旧路径 0x80 第 2 数据字节恒 0，此前各字节锚点均未断言该字节） |
| XOR 双向（并存拒/皆无拒） | NEG xor_both / xor_none | ✅ |
| 悬空 on / off 无 on / 同 pitch 交叠 / vel=0 | NEG dangling_on / off_no_on / overlap / vel_zero | ✅ |
| synth/sample 轨事件全拒 | NEG synth_any_event / sample_note_event | ✅ |
| sf2 KS 拒 / sf2 PC 拒 | NEG sf2_keyswitch / sf2_program_change | ✅ |
| sfz 弯音缺 bend_range 拒 | NEG sfz_bend_no_decl | ✅ |
| params 未知键/越界/投影锁定键（bpm、events）/跨引擎错键 | NEG params_unknown_key / out_of_range / locked_key / cross_engine_key / events_locked | ✅ |
| bend_range 非正拒 | NEG bend_range_nonpositive | ✅ |
| 未知事件类型闭集拒 / ctrl 非整数 | NEG event_unknown_type / event_bad_cc_ctrl | ✅ |
| events-only 轨缺省 clip=note_off 最大 beat+4 拍；投影 notes/payload.events | test_v2_events_only_track_default_clip_derivation | ✅ |
| 混装轨正例（notes+cc+bend+params.gain/channel；sfz 三类事件） | test_v2_mixed_track_and_params_positive | ✅ |
| events-only 轨渲染出声（sfz 全链） | test_events_only_track_renders_sound_via_sfz | ✅ |
| 混装 cc 不扰音符字节（新旧路径位级） | test_mixed_track_cc_does_not_disturb_note_bytes | ✅ |
| note-only 事件轨 payload 无 events 键（防重复编码） | test_events_only_track_note_only_projects_no_events_key | ✅ |
| v1 工程 sha 不变 | test_reassembly_deep_comparison_lock（逐字段）+ 跨文件 test_cli mix.flac sha 锚点 + test_dual_layout 缺省 render 逐位一致 | ✅ |
| 重组装深比较（minimal+baselines 全部 track spec+eden_v2 只读） | test_reassembly_deep_comparison_lock | ✅ ↩（eden_v2 段主树全量） |
| [slow] VPO CC1 渐强 RMS 单调 | test_slow_cc1_crescendo_vpo_strings | ✅ |
| [slow] 弯音微分音+量程证据（bend_range_evidence.json） | test_slow_pitch_bend_microtonal_and_range_evidence | ✅ |
| [slow] KS sw_last 探针（回退判据文案） | test_slow_keyswitch_sw_last_vpo | ✅ |
| [slow] sf2 params.gain 生效（RMS 差 4-8dB） | test_slow_sf2_params_gain_effective | ✅ |

## 3. test_edit.py（fast 32）

| §6/§3.4 设计点 | 用例 | 状态 |
|---|---|---|
| insert 带索引=前插（splice）/无索引=append/漂移纪律提示 | test_insert_note_splice_before_view_index_and_append | ✅ |
| insert 负例族（索引非整数/越界/payload 与 copy-of 互斥/缺内容/target 语法/clip 不支持/window 仅 copy-of） | test_insert_negative | ✅ |
| v1+events 编辑门 + 消费矩阵（synth 拒） | test_insert_events_v1_gate_and_v2_ok | ✅ |
| set 深度合并（嵌套对象合并非替换）/未知键拒（含 params 未知键描述符口径） | test_set_deep_merge_and_unknown_key | ✅ |
| 标量 target + master.qc 子对象合并 + matchering 再校验兜底 | test_set_scalar_targets_and_master_qc | ✅ |
| 窗口 set：beat/sec 换算等价/events 容器/容器无 window 拒 | test_set_window_notes_events_and_sec_equivalence | ✅ |
| 修饰器 transpose/quantize/scale（dur 同步缩放） | test_set_window_modifiers_transpose_quantize_scale | ✅ |
| 元素级合并（note 行/event 字段；事件未知字段按最终 type 闭集拒） | test_set_note_and_event_element_merge | ✅ |
| move 跨轨/窗口/载体不一致拒/轨重排 | test_move_cross_track_and_window / test_move_track_reorder | ✅ |
| delete 变体（params 键/clip→推导/automation/轨/sections/窗口/master.qc 键/无匹配报错） | test_delete_variants | ✅ |
| copy-of：同载体段落复制/跨形式 notes↔events/轨副本（C7 折叠） | test_copy_of_same_carrier_and_cross_form | ✅ |
| copy-of 有损负例（vel=0/交叠/控制事件/单个事件） | test_copy_of_negative_lossy | ✅ |
| 折叠固定顺序 transpose→scale→quantize（half-up） | test_insert_fold_transpose_quantize_scale + test_cli_modifier_string_parsing_and_half_up | ✅ |
| rename 内联四处引用（automation/sidechain targets+trigger/stem 不改名注记） | test_rename_inline_references | ✅ |
| rename $ref：外部轨文件重命名+undo 字节复原 | test_rename_ref_layout_file_and_undo | ✅ |
| $ref 删轨孤儿保留+注记+undo | test_delete_ref_track_leaves_orphan_noted | ✅ |
| undo manifest 格式契约（seq/desc/files/ts/sha256/快照目录名） | test_undo_stack_manifest_format_and_restore | ✅ |
| undo 漂移检测负例 + --force 才过 | test_undo_drift_detection_negative_and_force | ✅ |
| undo 写失败自动恢复（只读目录；M-1 成功/失败分支分离断言） | test_undo_write_failure_autorecover | ✅ |
| undo 上限 50 FIFO 淘汰 | test_undo_limit_50_fifo | ✅ |
| 再校验失败不写盘不入栈 / no-op 不入栈 | test_revalidate_no_half_write_and_noop | ✅ |
| split/merge 薄包装 + undo（含 undo split 删新建外部文件）+ 失败回滚 | test_split_merge_wrappers_and_undo | ✅ |
| CLI 修饰器手写报错（--transpose 非数/--scale 非正） | test_cli_modifier_string_parsing_and_half_up | ✅ |
| CLI 负例参数化（target 语法/window 语法/payload JSON/未知键/--to 缺失） | test_cli_negative_parametrized | ✅ |
| $ref 来源标注（stderr 附 (tracks/x.json)） | test_cli_ref_source_annotation_negative | ✅ |
| CLI 正例 --json 结构化 diff / undo --list 双形态 / 空栈 undo | test_cli_positive_json_and_undo_list | ✅ |
| BGM_CRASH_AFTER=N 原子写窗口注入（137/半状态/undo 复原） | test_cli_crash_injection_atomic_window | ✅ |
| eden_v2 十种真实修改（临时副本，正本 sha 不动，红线④） | test_eden_v2_ten_real_edits | ✅ ↩（out/eden_v2 本 worktree 在位可跑） |
| NaN/Infinity 全链拒（M-2 回归锁） | test_nan_infinity_rejected_everywhere | ✅ |
| XOR 门 events 键缺席哑轨拒（M-3 回归锁） | test_xor_gate_key_absent_dumb_track_rejected | ✅ |
| $ref show diff 扁平快照名回退（M-4 回归锁） | test_ref_project_show_diff_finds_flat_snapshot | ✅ |

## 4. test_dual_layout.py（fast 17 / slow 1；17=版本门 4 键参数化展开，残留版 16 为函数体口径）

| §6/§3.3 设计点 | 用例 | 状态 |
|---|---|---|
| $ref 装载正例 + 来源映射（\_ref_src）+ 校验报错路径附来源 | test_ref_load_positive_and_source_annotation | ✅ |
| 拒绝：绝对路径 / 嵌套 / 兄弟字段 | test_ref_reject_absolute_path / _nested_ref / _sibling_fields | ✅ |
| v1 版本前置门四键参数化（$ref/events/params/bend_range；先查版本再展开，P7） | test_v1_version_gate_rejects_v2_track_keys | ✅ |
| v2 接受 + v1 仍拒（联动语义） | test_v2_events_params_accepted_v1_still_rejected | ✅ |
| save 连续两次字节稳定 + 派生键白名单过滤（含外部文件） | test_split_merge_idempotent_and_save_byte_stable | ✅ |
| split↔merge 幂等（save 字节相等）+ merge 缺基准清晰报错 | 同上 | ✅ |
| split 位级一致（minimal，fast 判据⑤） | test_split_render_bitwise_and_snapshot_selfcontained | ✅ |
| $ref 快照=内联展开 + 独立重渲位级一致（M6 判据） | 同上 | ✅ |
| 显式 bank/program merge 不丢（R1-A·D-1 回归，位级） | test_merge_save_preserves_explicit_bank_program_bitwise | ✅ |
| SameFileError 回归（正本在产物目录；正本不坏） | test_samefile_regression_and_hard_gate | ✅ |
| BGM_NO_INCREMENTAL=1 硬闸赢过 flag + 日志一行（读点 render_project 内） | 同上 | ✅ |
| 增量：冷跑/改一轨仅该轨重渲/其余 skip 计数/边车结构 | test_incremental_hit_miss_default_bitwise_bpm_miss | ✅ |
| 缺省（无 flag）与全量逐位一致 | 同上 | ✅ |
| 增量结果=全量位级一致（确定性锁） | 同上 | ✅ |
| 仅改 bpm 全未命中（M2/F7 负例） | 同上 | ✅ |
| 边车丢失+stem 写坏 → 重跑不假命中（P4 崩溃窗口） | test_incremental_missing_sidecar_rerenders | ✅ |
| 元测试：conftest env 注入生效（通用规范③） | test_conftest_env_injection_active | ✅ |
| 元测试：无 marker 收集期 fail（通用规范①/判据⑨，P-14） | test_marker_discipline_collection_fails_on_unmarked | ✅ |
| 通用规范④ 增量用例自清残留边车 | 实现口径=helpers.tmpdir（BGM_TMP_ROOT 按进程隔离，r4smoke_<pid>）——比"固定 tmpdir 自清"更强：两增量用例各自新目录，天然无跨 run 残留 | ✅（口径注记） |
| 通用规范⑤ 子进程解释器=helpers.py3() | test_cli_negative._bgm 与全部 CLI 用例统一 [helpers.py3(), "-m", "bgm_gen", ...] | ✅（口径注记） |
| [slow] eden_v2 全量 split 位级一致 | test_eden_v2_split_bitwise | ✅ ↩（本 worktree out/eden_v2 在位可跑；本轮 slow 实跑 passed） |

## 5. test_show.py（fast 39）

| §6/§3.5 设计点 | 用例 | 状态 |
|---|---|---|
| structure --json schema + text 非空 | test_show_structure_schema_and_text | ✅ |
| structure $ref 来源标注（ref_src + text 内路径） | test_show_structure_ref_src_annotation | ✅ |
| notes 规范排序 (beat,pitch) + index=编辑寻址（文件序≠视图序） | test_show_notes_canonical_order_index | ✅ |
| notes 窗口 beat/sec + 窗口不重编号 | test_show_notes_window_beat_sec_index_stability | ✅ |
| notes 投影 note 事件（projected 标记/无 index） | test_show_notes_projects_note_events | ✅ |
| parse_window 负例族 + 库形参 (a,b) | test_parse_window_errors_and_library_form | ✅ |
| show cc 显示编码值 half-up（63.5→64；text 只显编码值，JSON 双值） | test_show_cc_encoded_value_round_half_up | ✅ |
| show cc 消费 track_cc_curve（禁自写排序/过滤，逐点对拍） | test_show_cc_consumes_track_cc_curve | ✅ |
| show events notes→on/off 投影（标记/内部键剥离/pitch 升序） | test_show_events_notes_projection_marks | ✅ |
| show events 规范序=event_sort_key（单一实现共用）+ 真实事件 index | test_show_events_canonical_order_and_index | ✅ |
| show events 窗口过滤（独立直算对拍） | test_show_events_window | ✅ |
| show diff 空栈/有栈（unchanged/changed/removed/snapshot_missing）+ --undo 选择/未命中 | test_show_diff_empty_stack / test_show_diff_with_stack_states_and_undo_select | ✅ |
| CLI 各叶 --json 可解析 + text 非空 | test_show_cli_json_leaves_parseable / test_show_cli_text_nonempty | ✅ |
| CLI 错误口径（stderr+exit1+无 Traceback，5 组） | test_show_cli_error_stderr_no_traceback | ✅ |
| 子进程端到端（helpers.run） | test_show_cli_subprocess_end_to_end | ✅ |
| 盲测 ≥3 题型×每类 ≥5 题（实际 3×6+配额）/seed 固定复现/ground truth 独立 json.load 直算 | test_show_blind_question（参数化）+ test_show_blind_bank_shape_and_reproducible | ✅ |
| 盲测 events-only 轨配额（R3-B·1 修复） | _make_blind_questions 确定性补题 | ✅ |
| R4 追加：fate 工程 fixture 复跑（判据②） | — | ⚠ F-3（依赖 W1 命运工程交付后补；转主线排入 W1 验收清单） |

## 6. test_catalog.py（fast 6，R1-W1 随描述符改写 + R4-W2 同步锁）

| 设计点 | 用例 | 状态 |
|---|---|---|
| synth 6 音色 + params 键 | test_synth_six_timbres | ✅ |
| sf2 双源/GM 128 程序表/速记 | test_sf2_fonts | ✅ |
| sfz 四库计数=磁盘复数=预期（独立 os.walk） | test_sfz_counts_match_disk | ✅ |
| sample 三源计数（kenney/drums_gdc/mixkit） | test_sample_section_nonempty | ✅ |
| JSON 可序列化 + md 段标题 + **payload 段由描述符驱动（参数名全出现）** | test_json_serializable_and_md_sections | ✅（§6"唯一预期改动"落位） |
| 盘内 catalog/timbres.{md,json} 与描述符同步（--write 重落盘活数据） | test_disk_catalog_fresh_render_sync | ✔ **F-1 已修复+已锁**：漂移实测成立（盘内 sf2/sfz payload 段 v1.0.0 落后描述符 v1.1.0，R2 bump 后未重落盘）→ 本轮 `python3 -m bgm_gen catalog --write` 重落盘（diff 最小：仅 2 版本行 + events/bend_range 参数行 ×2 引擎，md +8/-4、json 2 处 payload 串），并补同步锁（fresh build_catalog/render_markdown 对拍盘内，`generated` 日期行归一——跨天不误红） |

## 7. W1 两组（W1 交付、主线合流后复核；设计点清单备核）

| 文件 | §6 设计点（验收要点） |
|---|---|
| test_smf_import.py（fast） | Mutopia 实测锚定（8295 音符/1013 四分拍/恒 192 qBPM/384 PPQN/13 轨）；running status 合成用例；双 tempo 并列取先合成用例；dur=0 钳制；未配对 off 告警 |
| test_fate_data.py（fast） | gen_fate 重跑输出到 tmp 与 out/fate_mvt1 对拍**逐字节一致**（防债务 4 前科，仅覆盖 gen_fate）；四断言（KS pitch ∉ 音符域/重合处低音提琴 ≤ 大提琴（移调后）/note 落 VPO 键位/全轨静音间隙 ≤ silent_window_max_s 减余量） |
| test_fate_render.py（slow） | 自渲全链 → mix.flac **sha 锚定（必选）** + qc.json ok=true + normalization_type=="linear"（§4.3） |
| （联动）test_show fate fixture 复跑 | F-3：fate 工程入位后以 fixture 复跑盲测（R4 判据②）——随 W1 交付一并复核 |

## 8. 依赖主树产物（out/ gitignored）后跑清单（本轮 slow 实测附证）

worktree out/ 为最小子集拷贝：eden_v2 / minimal / smoke\{master,sample,sf2,synth\} 在位；
**out/smoke/demucs/ 与 out/eden_track/ 不在位**（主树均确认在位，如
demucs_in10s.wav 1.7MB @主树 out/smoke/demucs/）。本轮 slow 实测：

| 测试 | worktree 行为（本轮实测） | 主树后跑 |
|---|---|---|
| test_descriptors::test_demucs_probe | 显式 skip（有守卫，理由=worktree 无探针输入） | ✅ 需跑 |
| test_engines::test_demucs_10s_four_stems | **FAIL（环境性，assert 分轨输入缺失——非代码回归，失败信息自带路径）** | ✅ 需跑（输入主树在位） |
| test_engines::test_master_loudnorm_ebur128 | 同上（输入=同 demucs wav） | ✅ 需跑 |
| test_eden_repro::test_eden_bitwise_repro | 显式 skip（eden_track 交付物不在位） | ✅ 需跑 |
| test_events::test_reassembly_deep_comparison_lock（eden_v2 段） | 本 worktree eden_v2 在位已全跑 | 已覆盖 |
| test_dual_layout::test_eden_v2_split_bitwise（slow） | 本 worktree **已实跑 passed** | 已覆盖 |
| test_show fate 复跑 / test_smf_import / test_fate_\* | 文件未到（W1 交付、主线合流后复核） | 合流后 |

注记（交主线，不越界改）：test_engines 两例 slow 对缺失输入用 `assert os.path.isfile`
而非 `pytest.skip`——在无产物环境跑 `-m slow` 会 FAIL 而非显式 skip，与同仓
"缺输入显式 skip 计数"惯例（test_descriptors::test_demucs_probe）不一致；是否统一由主线裁定。

## 9. 任务 1 附带记录：cli 负分支报错形态（人工实测 2026-08-23）

| 分支 | exit | stderr 形态 | 评价 |
|---|---|---|---|
| stem 输入不存在 | 1 | `stem 失败：demucs payload.input 不存在：'<path>'` | 干净（引擎语义守卫先于 runtime/GPU 探测） |
| master 输入不存在 | 1 | `master 失败：master payload.input 不存在：'<path>'` | 干净 |
| master --mode 非法 | 2 | argparse usage + `invalid choice: 'bogus' (choose from loudnorm, matchering)` | 干净（choices 拦截，自解释） |
| master matchering 缺 ref | 1 | `master 失败：master payload 校验失败：matchering 模式缺 payload.ref：None` | 干净（描述符 invariant，引擎前拦，不触 comfyui env） |
| qc 输入不存在 | 1 | `qc 失败：Command '[ffmpeg … <path> …]' returned non-zero exit status 254`（前置 ffmpeg 自身错误行） | **F-2：可定位（路径在命令列表内可见）、无 Traceback，但为 CalledProcessError 包装而非手写报错**——形态偏原始，是否在 qc_report/measure 前置 isfile 检查由主线裁定（cli.py/core 未越界改） |
| qc --project 不存在 | 1 | `qc 失败：[Errno 2] No such file or directory: '<path>'`（load_project 前置，未触测量） | 干净（FileNotFoundError 消息自带路径） |

## 10. README/conftest 文档漂移（任务 4 处置，本轮已落盘）

- `-m ""` **陷阱实测**：`pytest -m ""` 经 conftest 的 `(getoption("-m") or "").strip()`
  判空后落入默认分支 → **fast-only（13 deselected）**，并非 README/conftest docstring
  原声称的"全量三档"。真全量 = `-m "fast or slow or service"`（271 collected）。
  已修：tests/README.md 运行方式段改为真全量写法 + 陷阱注记块；conftest.py
  docstring 同句纠正（**docstring-only，零行为改动**——判空逻辑本身不动）。
- tests/README.md 其余漂移同步修：标题锚 PLAN_v3 §6/AGENTS §四；marker 纪律
  （收集期 fail + 元测试名）入文；fast 计数 68→258、预算 ≤75s；三档语义表补
  v3 内容（w2a 7→8 条含 master；slow/service 补 v3 探针）；分层用例表拆
  v1/v3 两段——v1 侧订正 test_assets 24→30、test_engines 计数与 watchdog 锁、
  test_catalog 5→6；v3 侧新列七组（descriptors/events/edit/dual_layout/show/
  cli_negative + fate 三件"W1 交付、主线合流后入位"注记）；产物目录
  /tmp/r4smoke/ → **BGM_TMP_ROOT 进程隔离 r4smoke_<pid>** + 陈旧目录自动清理。
- 残留审计中断点披露：上轮残留版本文件声称"已修 README/conftest"，实际
  **未落盘**（两文件 git 干净、内容仍旧口径）——本轮复核发现后补齐。
  残留版另有一处计数误记（fast 256，实为 257——参数化展开口径），已订正。
