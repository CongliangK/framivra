"""R7（2026-09-15）：合成代码摘要 AST 归一化 —— 注释/docstring 提交不再击穿段缓存。

背景事故：R5 收口提交 4e4b756 对 composite.py 仅改注释（「行为零变化」），
_graph_code_digest 原实现哈希原始源码 → 全部 vseg 段键翻转 → 全片 ~900s 冷重建。
"""
import ast as ast_mod
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vtx.composite import _graph_code_digest, _normalized_ast_src


SRC = '''\
def f(a):
    # 计算注释
    """文档字符串"""
    if a > 0:
        return a * 2
    return 0
'''

SRC_COMMENT_ONLY = '''\
def f(a):  # 换行注释变了
    """文档字符串也变了"""

    if a>0:   # 格式/空白变化
        return a*2
    return 0
'''

SRC_CODE_CHANGED = '''\
def f(a):
    if a > 0:
        return a * 3
    return 0
'''


class TestNormalizedAst(unittest.TestCase):
    def test_comment_docstring_format_do_not_flip(self):
        self.assertEqual(_normalized_ast_src(SRC), _normalized_ast_src(SRC_COMMENT_ONLY))

    def test_code_change_flips(self):
        self.assertNotEqual(_normalized_ast_src(SRC), _normalized_ast_src(SRC_CODE_CHANGED))

    def test_syntax_error_falls_back_to_raw(self):
        # parse 失败退回原始字节：保守失效优于崩溃
        self.assertEqual(_normalized_ast_src("def ("), "def (")

    def test_digest_is_stable_shape(self):
        d = _graph_code_digest()
        self.assertEqual(len(d), 12)
        self.assertEqual(d, _graph_code_digest())   # lru_cache 一致

    def test_indented_method_source_is_normalized(self):
        """实测轮回归（2026-09-15）：inspect.getsource 对类方法返回带类体缩进的
        源码，漏 dedent 时 ast.parse 抛 SyntaxError → 回退返回原样 → 归一化在
        真实合成函数上整体空转（摘要退化为旧原始字节哈希）。"""
        indented = (
            "    def m(self):\n"
            "        # 注释\n"
            "        \"\"\"doc\"\"\"\n"
            "        return self.x\n"
        )
        # 归一化必须真的发生（≠原始文本），且缩进无关
        self.assertNotEqual(_normalized_ast_src(indented), indented)
        self.assertEqual(_normalized_ast_src(indented),
                         _normalized_ast_src("def m(self):\n    return self.x\n"))

    def test_real_synth_funcs_digest_not_raw(self):
        """五个合成函数是类方法（带缩进）：真实 _graph_code_digest 不得退化为
        原始字节哈希（空转形态的端到端断言）。"""
        import inspect
        from vtx import composite as vc
        raw = "".join(inspect.getsource(getattr(vc.RenderPlan, n))
                      for n in vc._SYNTH_FUNCS)
        import hashlib
        raw_digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        self.assertNotEqual(_graph_code_digest(), raw_digest)


class TestColdRebuildHints(unittest.TestCase):
    """vtx diff 磁盘侧提示：摘要漂移 / 段缓存被 gc。"""

    def test_hint_on_digest_drift(self):
        import tempfile
        from vtx.timeline_diff import _cold_rebuild_hints
        with tempfile.TemporaryDirectory() as td:
            vdir = Path(td) / "cache" / "vseg"
            vdir.mkdir(parents=True)
            (vdir / ".graph_digest").write_text("deadbeefcafe")
            (vdir / "seg00_000000_x.mp4").write_bytes(b"x")
            hints = _cold_rebuild_hints(Path(td))
            self.assertTrue(any("冷重建" in h for h in hints), hints)
            # 摘要一致且有段文件 → 无提示
            (vdir / ".graph_digest").write_text(_graph_code_digest())
            self.assertEqual(_cold_rebuild_hints(Path(td)), [])

    def test_hint_on_empty_vseg(self):
        import tempfile
        from vtx.timeline_diff import _cold_rebuild_hints
        with tempfile.TemporaryDirectory() as td:
            vdir = Path(td) / "cache" / "vseg"
            vdir.mkdir(parents=True)
            hints = _cold_rebuild_hints(Path(td))
            self.assertTrue(any("冷建" in h for h in hints), hints)

    def test_hint_on_gc_removed_vseg_dir(self):
        """红队必修回归：gc 对 vseg 是整目录 rmtree——目录不存在必须报冷建，
        不得静默（原实现恰好在该最常见形态下漏报）。"""
        import tempfile
        from vtx.timeline_diff import _cold_rebuild_hints
        with tempfile.TemporaryDirectory() as td:
            hints = _cold_rebuild_hints(Path(td))
            self.assertTrue(any("不存在" in h and "冷建" in h for h in hints), hints)


if __name__ == "__main__":
    unittest.main()
