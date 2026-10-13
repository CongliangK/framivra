"""A05 / issue #24: isolated destructive-operation regression tests.

Run: python3 -m unittest tests.safety（模块方式）或
    python3 -m unittest discover -s tests/safety -t .（发现方式）——两者
经下方 load_tests 协议聚合出同一测试集（A-3 修复：此前模块方式发现 0 例）。
Pending A01/A03 fixes: VTX_SAFETY_EXPECT_FIXED=1 enables correct-behavior
assertions. run_all_tests.sh 的 discover -s tests -t . 递归进入本包时同样
经由 load_tests 聚合。
"""
import os


def load_tests(loader, tests, pattern):
    """Aggregate test*.py submodules so module-mode discovery sees them (A-3).

    top_level_dir 锚定仓根：无论模块方式（pattern=None）还是 discover 方式
    （pattern="test*.py"），子模块一律以 tests.safety.test_* 导入，两种
    方式得到同一测试集且互不重复导入。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))
    if pattern is None:
        pattern = "test*.py"
    suite = loader.discover(here, pattern, repo)
    suite.addTests(tests)
    return suite
