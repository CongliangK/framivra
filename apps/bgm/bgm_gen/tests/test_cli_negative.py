"""cli stem/master/qc 负分支补测 [fast]（PLAN_v3 §5 R4-W2；AGENTS §五.3 遗留债务 3 清除）。

覆盖与现有报错形态（2026-08-23 人工预跑实测，本文件只锁不改 cli.py）：
- bgm stem 输入不存在        → exit 1，stderr `stem 失败：demucs payload.input 不存在：<path>`
                              （demucs 引擎语义守卫先于 runtime/GPU 探测，负例恒快）
- bgm master 输入不存在      → exit 1，stderr `master 失败：master payload.input 不存在：<path>`
- bgm master --mode 非法     → exit 2（argparse choices 拦截），stderr usage + invalid choice
- bgm master matchering 缺 ref → exit 1，stderr 描述符 invariant
                              `master payload 校验失败：matchering 模式缺 payload.ref`
                              （validate_payload 在引擎实例化前拦，不跑 comfyui 子进程）
- bgm qc 输入不存在          → exit 1，stderr `qc 失败：输入文件不存在：<path>`
                              （R4 主线 f7ba0cc 落地 qc_report 前置检查——原为
                              measure_wav 兜底转码 CalledProcessError 包装，路径仅在
                              命令列表内可见；COVERAGE_AUDIT F-2 裁定的修复，本例
                              断言保持子串口径兼容新旧形态）
- bgm qc --project 不存在    → exit 1，stderr `qc 失败：[Errno 2] No such file or directory:
                              <path>`（load_project 前置，未触测量）

统一断言口径（同 test_cli 负例族）：exit≠0 + stderr 含子命令名/路径等可定位信息 +
无 Traceback。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402


def _bgm(*args, timeout=120):
    return helpers.run([helpers.py3(), "-m", "bgm_gen", *args],
                       cwd=helpers.WORKSPACE, timeout=timeout)


def _assert_no_traceback(r, ctx):
    assert "Traceback" not in (r.stderr or ""), \
        f"{ctx} 抛了裸 Traceback（应走可定位报错）：\n{r.stderr[-1500:]}"


def _absent(name):
    """临时目录内构造不存在的绝对路径（路径可复现、不污染仓库）。"""
    return os.path.join(helpers.tmpdir("cli_neg"), name)


def _tiny_wav(name="tiny.wav"):
    """matchering 缺 ref 用真实存在的小 wav（isfile 门要放行 input 才能单测 ref 门）。"""
    import numpy as np
    path = os.path.join(helpers.tmpdir("cli_neg"), name)
    if not os.path.isfile(path):
        sig = 0.3 * np.sin(2 * np.pi * 220.0 * np.arange(4410) / 4410.0)
        helpers.write_wav(path, sig, sr=4410, channels=1)
    return path


# ------------------------------------------------------------------ stem
@pytest.mark.fast
def test_stem_input_missing():
    p = _absent("absent_stem.wav")
    r = _bgm("stem", p)
    assert r.returncode != 0, "stem 输入不存在应当失败却 exit 0"
    _assert_no_traceback(r, "stem 输入不存在")
    assert "stem 失败" in r.stderr and "不存在" in r.stderr, r.stderr[-800:]
    assert p in r.stderr, f"报错缺输入路径 {p}：\n{r.stderr[-800:]}"


# ---------------------------------------------------------------- master
@pytest.mark.fast
def test_master_input_missing():
    p = _absent("absent_master.wav")
    r = _bgm("master", p)
    assert r.returncode != 0
    _assert_no_traceback(r, "master 输入不存在")
    assert "master 失败" in r.stderr and "不存在" in r.stderr, r.stderr[-800:]
    assert p in r.stderr, f"报错缺输入路径 {p}：\n{r.stderr[-800:]}"


@pytest.mark.fast
def test_master_invalid_mode_argparse_rejects():
    """--mode 非法由 argparse choices 拦截（exit 2 + usage + invalid choice，无 Traceback）。"""
    r = _bgm("master", _tiny_wav(), "--mode", "bogus")
    assert r.returncode != 0
    _assert_no_traceback(r, "master --mode 非法")
    assert "invalid choice" in r.stderr and "bogus" in r.stderr, r.stderr[-800:]
    assert "loudnorm" in r.stderr            # usage 行列合法枚举，报错自解释


@pytest.mark.fast
def test_master_matchering_missing_ref():
    """matchering 模式缺 --ref：描述符 invariant（base.validate_payload）在引擎前拦。"""
    r = _bgm("master", _tiny_wav(), "--mode", "matchering")
    assert r.returncode != 0
    _assert_no_traceback(r, "master matchering 缺 ref")
    assert "master 失败" in r.stderr, r.stderr[-800:]
    assert "matchering" in r.stderr and "payload.ref" in r.stderr, r.stderr[-800:]


# -------------------------------------------------------------------- qc
@pytest.mark.fast
def test_qc_input_missing():
    p = _absent("absent_qc.wav")
    r = _bgm("qc", p)
    assert r.returncode != 0
    _assert_no_traceback(r, "qc 输入不存在")
    assert "qc 失败" in r.stderr, r.stderr[-800:]
    assert p in r.stderr, f"报错缺输入路径 {p}：\n{r.stderr[-800:]}"


@pytest.mark.fast
def test_qc_project_missing():
    """--project 不存在：load_project 前置失败（未触 ffmpeg 测量，负例恒快）。"""
    p = _absent("absent_project.json")
    r = _bgm("qc", _tiny_wav(), "--project", p)
    assert r.returncode != 0
    _assert_no_traceback(r, "qc --project 不存在")
    assert "qc 失败" in r.stderr, r.stderr[-800:]
    assert p in r.stderr, f"报错缺工程路径 {p}：\n{r.stderr[-800:]}"


@pytest.mark.fast
def test_render_invalid_parallel_env_clean():
    """v3.4 验收 M-2 / 终审 A·M-2 补测：BGM_MAX_PARALLEL 非法值 →
    exit 1 + `render 失败：BGM_MAX_PARALLEL …` 干净报错 + 无 Traceback
    （ResourceError 捕获此前仅手工验证）。"""
    out = os.path.join(helpers.tmpdir("cli_neg"), "render_res")
    r = helpers.run(
        [helpers.py3(), "-m", "bgm_gen", "render",
         os.path.join(helpers.EXAMPLES_DIR, "minimal.json"), "-o", out],
        cwd=helpers.WORKSPACE, timeout=120,
        env_extra={"BGM_MAX_PARALLEL": "abc"})
    assert r.returncode != 0, (r.stdout[-400:], r.stderr[-400:])
    assert "render 失败" in r.stderr and "BGM_MAX_PARALLEL" in r.stderr, r.stderr[-400:]
    _assert_no_traceback(r, "render 非法 BGM_MAX_PARALLEL")
