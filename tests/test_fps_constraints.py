"""B02 / issue #26 / F-04 / #4：fps/分辨率约束三面一致性（2026-10-13）。

三面与单一来源：
  - 单一取值点 vtx/constraints.py：ALLOWED_FPS=(30,)（D24 换基，全管线固定
    30fps 精确，≠29.97；NTSC 23.976/29.97/59.94、PAL 25/50、电影 24、高帧率
    60 一律拒绝）、ALLOWED_SIZES=((1920,1080),)、AUDIO_SAMPLE_RATE=48000
    （30fps → 每帧整 1600 采样，atrim 采样网格不漂移）；
  - 结构权威 timeline.schema.json：project.fps enum [30] / width,height const
    ——代码允许集的静态镜像，本测试解析比对防漂移；
  - 机制权威 vtx/infra.py Ctx 前置门：经 fps_error() 判定（数值等值 30.0
    归一 int 30）；分辨率门有意不设在 infra（降采样渲染测试夹具依赖，见
    constraints.py 模块头），由 schema/preview 两面把门；
  - 预览权威 vtx/preview.html：内嵌 CONSTRAINTS 镜像（唯一同步抄写点）+
    validate() 据此判 fps 与分辨率 + SAMPLE 示例用允许值（24fps 时代示例
    与 ||24 缺省回退均为本单清除标的）。

边界矩阵是契约快照：将来扩 fps 白名单须同步改 constraints/schema/preview
三面与本文件 EXPECTED_ALLOWED——这正是 drift-guard 的用意。

文件合并（2026-10-13 工单 B02 对抗+测试合并）：原 tests/test_constraints.py
（17 例，同单重叠文件）已并入本文件为语义超集后删除——生产代码注释
（constraints.py / infra.py / timeline.schema.json $comment）均指向本文件名，
故本文件为存续侧。并入的独有覆盖：schema/preview 锚点互指、validate() 真在
消费镜像、infra.fps_error 接线同一性、Ctx 级类型错值拒绝与 NTSC 文案、
分辨率三面矩阵、可选 jsonschema 真校验器交叉验证、极值/类型边界扩面
（0/-30/1/120/48000/29.972 等「整除 48000 陷阱」与 "30"/bool/None 类型错值）。
"""
import json, re, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # repo 根（v2 §1.4 包导入）

from vtx.constraints import (ALLOWED_FPS, ALLOWED_SIZES, AUDIO_SAMPLE_RATE,
                             fps_error, size_error)

REPO = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((REPO / "vtx" / "timeline.schema.json").read_text(encoding="utf-8"))
PREVIEW_HTML = (REPO / "vtx" / "preview.html").read_text(encoding="utf-8")

try:
    import jsonschema   # 可选交叉验证（并入自 test_constraints.py）：真校验器
except ImportError:     # 跑整条时间线；环境无则跳过，不作为门
    jsonschema = None

# B02 边界矩阵：允许集=仅 30（数值等值 30.0 另测）。覆盖 NTSC/PAL/电影/高帧率。
BOUNDARY_FPS = [23.976, 24, 25, 29.97, 30, 50, 59.94, 60]
EXPECTED_ALLOWED = {30}

# 极值/类型边界（并入自 test_constraints.py）：整除陷阱（1/120/48000 恰整除
# 48000——旧 infra 门会放行的值）、近 30 值（29.972/29.97003）、零/负、非整数
# float、类型错值（"30"/bool/None/list）。三面同判一律拒绝；30.0 为唯一放行
# 的数值等值边界（在列表内明示）。
EXTRA_FPS = [30.0, 0, -30, 1, 120, 48000, 29.972, 29.97003, 30.5,
             "30", True, False, None, [30], float("nan"), float("inf")]

# 分辨率三面矩阵（并入自 test_constraints.py，并并入本文件原 code 面独有值）。
SIZE_BOUNDARIES = [
    ((1920, 1080), True),
    ((3840, 2160), False),   # 4K 非本契约分辨率（4K 经投稿链另出，不走 timeline 契约值）
    ((2560, 1440), False),
    ((1920, 1200), False),   # 16:10 同宽异高
    ((1280, 720), False),
    ((640, 360), False),
    ((1080, 1920), False),   # 竖版（宽高对调）
    ((1920, 0), False),      # 零维
    ((0, 1080), False),
]


def _num_in(v, allowed):
    """数值等值成员判定：JSON Schema enum 数学等值语义 / JS number 单一模型
    的 Python 同构（30.0 == 30 → 放行；"30"/bool/None 与数字永不相等）。"""
    return any(v == a for a in allowed)


def _schema_fps_values():
    node = SCHEMA["properties"]["project"]["properties"]["fps"]
    assert "enum" in node, "project.fps 须为 enum（B02：与 ALLOWED_FPS 集合一致）"
    return node["enum"]


def _schema_sizes():
    p = SCHEMA["properties"]["project"]["properties"]

    def vals(node, key):
        if "enum" in node:
            return node["enum"]
        assert "const" in node, f"project.{key} 须为 enum 或 const"
        return [node["const"]]

    ws, hs = vals(p["width"], "width"), vals(p["height"], "height")
    assert len(ws) == len(hs), "width/height 允许值数量须一致（成对语义）"
    return list(zip(ws, hs))


def _schema_size_ok(width, height):
    """schema 面尺寸判定复刻：width/height 各自 const/enum 数值等值且成对。"""
    return any(width == w and height == h for w, h in _schema_sizes())


def _preview_constraints():
    m = re.search(r"const\s+CONSTRAINTS\s*=\s*(\{[^;]*?\})\s*;", PREVIEW_HTML)
    assert m, "preview.html 须内嵌 CONSTRAINTS 镜像（唯一同步抄写点）"
    return json.loads(m.group(1))


def _preview_size_ok(width, height):
    """preview 面尺寸判定复刻：与 validate() 内 CONSTRAINTS.sizes.some 同语义。"""
    return any([width, height] == s for s in _preview_constraints()["sizes"])


def _preview_sample():
    m = re.search(r"const\s+SAMPLE\s*=\s*(\{[^;]*?\})\s*;", PREVIEW_HTML)
    assert m, "preview.html 须内嵌 SAMPLE 示例"
    return json.loads(m.group(1))


def _write_tl(root, fps, width=1920, height=1080):
    tl = {"version": 2,
          "project": {"fps": fps, "width": width, "height": height,
                      "duration_frames": 30},
          "sources": [], "video_tracks": [], "subtitles": [], "audio_tracks": []}
    p = Path(root) / "timeline.json"
    p.write_text(json.dumps(tl), encoding="utf-8")
    return p


class AllowedSetContract(unittest.TestCase):
    """明示允许集（单一来源现值快照）。"""

    def test_allowed_fps_is_30_only(self):
        self.assertEqual(tuple(ALLOWED_FPS), (30,))

    def test_allowed_sizes_is_1080p_only(self):
        self.assertEqual(tuple(ALLOWED_SIZES), ((1920, 1080),))

    def test_audio_sample_rate_frozen(self):
        # 并入自 test_constraints.py：采样率常量同为契约冻结值
        self.assertEqual(AUDIO_SAMPLE_RATE, 48000)

    def test_allowed_fps_divides_sample_rate(self):
        for f in ALLOWED_FPS:
            self.assertEqual(AUDIO_SAMPLE_RATE % f, 0,
                             f"fps={f} 须整除 {AUDIO_SAMPLE_RATE}（atrim 网格不漂移）")

    def test_size_error_matrix(self):
        self.assertIsNone(size_error(1920, 1080))
        for wh in [(3840, 2160), (640, 360), (1080, 1920), (1920, 0), (0, 1080)]:
            self.assertIsNotNone(size_error(*wh), f"{wh} 应被拒绝")


class CodeFaceBoundaryMatrix(unittest.TestCase):
    """代码面（constraints.fps_error，infra 门同源）边界判定。"""

    def test_boundary_matrix(self):
        for f in BOUNDARY_FPS + EXTRA_FPS:
            err = fps_error(f)
            self.assertEqual(err is None, _num_in(f, EXPECTED_ALLOWED),
                             f"fps={f}: fps_error 判定与允许集不符（msg={err!r}）")

    def test_numeric_equal_30_float_passes(self):
        self.assertIsNone(fps_error(30.0))   # 30.0 ≡ 30（三面数学等值语义）

    def test_garbage_rejected(self):
        for bad in [True, False, "30", None, [30], 0, -30, 30.5,
                    float("nan"), float("inf")]:
            self.assertIsNotNone(fps_error(bad), f"{bad!r} 应被拒绝")


class SchemaFace(unittest.TestCase):
    """结构面（timeline.schema.json 静态镜像）漂移守护。"""

    def test_fps_enum_equals_code_allowed_set(self):
        self.assertEqual(_schema_fps_values(), list(ALLOWED_FPS))

    def test_size_consts_mirror_code_allowed_set(self):
        self.assertEqual(_schema_sizes(), [tuple(s) for s in ALLOWED_SIZES])

    def test_schema_anchors_point_to_single_source(self):
        # 并入自 test_constraints.py：description/$comment 互指单一来源
        # （文档锚点防再漂移——静态文件无法「引用」，以锚点+比对双守）
        self.assertIn("constraints.py",
                      SCHEMA["properties"]["project"]["properties"]["fps"]["description"])
        self.assertIn("constraints.py", SCHEMA.get("$comment", ""))

    def test_boundary_matrix(self):
        vals = _schema_fps_values()
        for f in BOUNDARY_FPS + EXTRA_FPS:
            self.assertEqual(_num_in(f, vals), _num_in(f, EXPECTED_ALLOWED),
                             f"fps={f}: schema enum 判定与允许集不符")

    def test_numeric_equal_30_float_membership(self):
        self.assertTrue(_num_in(30.0, _schema_fps_values()))


class PreviewFace(unittest.TestCase):
    """预览面（preview.html 内嵌镜像与示例）。"""

    def test_constraints_mirror_equals_code(self):
        c = _preview_constraints()
        self.assertEqual(c["fps"], list(ALLOWED_FPS))
        self.assertEqual([tuple(s) for s in c["sizes"]],
                         [tuple(s) for s in ALLOWED_SIZES])
        self.assertEqual(c["audio_sample_rate"], AUDIO_SAMPLE_RATE)

    def test_validate_consumes_mirror(self):
        # 并入自 test_constraints.py：镜像必须是活数据——validate() 真在消费
        # CONSTRAINTS（防镜像退化为无人引用的死数据后静默漂移）
        self.assertRegex(PREVIEW_HTML, r"CONSTRAINTS\.fps\.includes\(P\.fps\)")
        self.assertRegex(PREVIEW_HTML, r"CONSTRAINTS\.sizes\.some\(")

    def test_sample_uses_allowed_project(self):
        P = _preview_sample()["project"]
        self.assertTrue(_num_in(P["fps"], ALLOWED_FPS),
                        "SAMPLE 示例 fps 须在允许集内（B02：示例不再用 24fps）")
        self.assertIn((P["width"], P["height"]),
                      [tuple(s) for s in ALLOWED_SIZES])
        # 示例与镜像自洽：preview 打开示例即「校验通过」
        self.assertTrue(_num_in(P["fps"], _preview_constraints()["fps"]))

    def test_no_stale_24fps_fallback(self):
        self.assertIsNone(re.search(r"\|\|\s*24\b", PREVIEW_HTML),
                          "B02 症状回归：不得残留 ||24 缺省回退（统一 CONSTRAINTS.fps[0]）")
        self.assertIn("||CONSTRAINTS.fps[0]", PREVIEW_HTML)

    def test_boundary_matrix(self):
        vals = _preview_constraints()["fps"]
        for f in BOUNDARY_FPS + EXTRA_FPS:
            self.assertEqual(_num_in(f, vals), _num_in(f, EXPECTED_ALLOWED),
                             f"fps={f}: preview CONSTRAINTS 判定与允许集不符")


class InfraFace(unittest.TestCase):
    """机制面（vtx/infra.py Ctx 前置门，判定同源 constraints.fps_error）。"""

    def test_fps_30_constructs(self):
        from vtx.infra import Ctx
        with tempfile.TemporaryDirectory() as d:
            c = Ctx(_write_tl(d, 30))
            self.assertEqual(c.fps, 30)
            self.assertIsInstance(c.fps, int)
            self.assertEqual(c.spf_samples, 1600)
            self.assertIsInstance(c.spf_samples, int)

    def test_fps_30_float_normalized_to_int(self):
        from vtx.infra import Ctx
        with tempfile.TemporaryDirectory() as d:
            c = Ctx(_write_tl(d, 30.0))
            self.assertEqual(c.fps, 30)
            self.assertIsInstance(c.fps, int)   # 30.0 归一，spf_samples 恒整型
            self.assertEqual(c.spf_samples, 1600)
            self.assertIsInstance(c.spf_samples, int)

    def test_gate_wired_to_single_source(self):
        # 并入自 test_constraints.py：硬门无影子实现——infra 直接消费
        # constraints.fps_error（同一函数对象，非复刻）
        from vtx import constraints, infra
        self.assertIs(infra.fps_error, constraints.fps_error)

    def test_garbage_rejected_by_ctx_gate(self):
        # 并入自 test_constraints.py：Ctx 级类型错值拒绝（JSON 解析后可能到达
        # 门的真实类型），报错须指路唯一允许值 30
        from vtx.infra import Ctx
        for bad in (0, "30", True, [30]):
            with self.subTest(fps=bad):
                with tempfile.TemporaryDirectory() as d:
                    with self.assertRaises(ValueError, msg=f"fps={bad!r} 应被 Ctx 拒绝") as cm:
                        Ctx(_write_tl(d, bad))
                    self.assertIn("30", str(cm.exception))

    def test_boundary_matrix_matches_fps_error(self):
        from vtx.infra import Ctx
        for f in BOUNDARY_FPS:
            with tempfile.TemporaryDirectory() as d:
                if fps_error(f) is None:
                    c = Ctx(_write_tl(d, f))    # 放行面不得抛
                    self.assertEqual(c.fps, int(f))
                else:
                    with self.assertRaises(ValueError, msg=f"fps={f} 应被 Ctx 拒绝"):
                        Ctx(_write_tl(d, f))

    def test_reject_message_names_fixed_timebase(self):
        from vtx.infra import Ctx
        for f in (24, 29.97, 23.976, 60):
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(ValueError) as cm:
                    Ctx(_write_tl(d, f))
                # 拒绝消息须指向固定时基（含 "30"），不复述旧「整除 48000 即可」口径
                self.assertIn("30", str(cm.exception),
                              f"fps={f} 拒绝消息须指向固定时基 30fps")

    def test_ntsc_message_mentions_non_integer(self):
        # 并入自 test_constraints.py：NTSC 非整数帧率报错须点名「非整数」
        from vtx.infra import Ctx
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError) as cm:
                Ctx(_write_tl(d, 29.97))
            self.assertIn("非整数", str(cm.exception))

    def test_no_resolution_gate_by_design(self):
        """infra 面有意不设分辨率门（降采样渲染测试夹具依赖，constraints.py
        模块头裁定）；分辨率门设在 schema/preview 两面。"""
        from vtx.infra import Ctx
        with tempfile.TemporaryDirectory() as d:
            c = Ctx(_write_tl(d, 30, width=640, height=360))
            self.assertEqual((c.w, c.h), (640, 360))


class ThreeFaceConsistency(unittest.TestCase):
    """三面同判总门：同一帧率值在 schema enum / constraints.fps_error（infra
    门据此）/ preview CONSTRAINTS 的判定必须一致——B02 修复标的。"""

    def test_boundary_matrix_identical_across_faces(self):
        schema_vals = _schema_fps_values()
        preview_vals = _preview_constraints()["fps"]
        for f in BOUNDARY_FPS + EXTRA_FPS:
            expected = _num_in(f, EXPECTED_ALLOWED)
            faces = {
                "constraints.fps_error": fps_error(f) is None,
                "schema.enum": _num_in(f, schema_vals),
                "preview.CONSTRAINTS": _num_in(f, preview_vals),
            }
            bad = {k: v for k, v in faces.items() if v != expected}
            self.assertFalse(bad,
                             f"fps={f}: 三面判定漂移 {bad}（期望 allowed={expected}）")


class ThreeFaceSizeConsistency(unittest.TestCase):
    """分辨率三面矩阵（并入自 test_constraints.py）：同一 (width,height) 在
    schema const / constraints.size_error / preview CONSTRAINTS.sizes 的判定
    必须一致且符合 SIZE_BOUNDARIES。"""

    def test_size_matrix_three_surfaces_agree(self):
        for (w, h), expected in SIZE_BOUNDARIES:
            with self.subTest(size=f"{w}x{h}"):
                self.assertEqual(_schema_size_ok(w, h), expected, msg="schema 面")
                self.assertEqual(size_error(w, h) is None, expected, msg="来源面")
                self.assertEqual(_preview_size_ok(w, h), expected, msg="preview 面")


@unittest.skipIf(jsonschema is None, "jsonschema 未安装（静态解析面已覆盖）")
class JsonSchemaCrossCheck(unittest.TestCase):
    """可选交叉验证（并入自 test_constraints.py）：真校验器跑整条时间线，
    总判定与矩阵一致。nan/inf 非 JSON 合法值，不入真校验器（Python 语义面
    已覆盖）。"""

    @staticmethod
    def _tl(fps):
        return {"version": 2, "project": {"fps": fps, "width": 1920,
                                          "height": 1080, "duration_frames": 90},
                "sources": [{"id": "a", "path": "a.mp4"}],
                "video_tracks": [{"name": "v", "kind": "broll",
                                  "clips": [{"src": "a", "src_in": 0,
                                             "src_out": 30, "dst_in": 0, "dur": 30}]}],
                "subtitles": [],
                "audio_tracks": [{"name": "bed",
                                  "events": [{"src": "a", "in": 0, "out": 30,
                                              "volume": 0.8}]}]}

    def test_real_validator_matches_expected_matrix(self):
        values = [v for v in BOUNDARY_FPS + EXTRA_FPS
                  if v == v and v != float("inf")]   # 滤 nan/inf
        for fps in values:
            with self.subTest(fps=fps):
                try:
                    jsonschema.validate(self._tl(fps), SCHEMA)
                    ok = True
                except jsonschema.ValidationError:
                    ok = False
                self.assertEqual(ok, _num_in(fps, EXPECTED_ALLOWED))


if __name__ == "__main__":
    unittest.main()
