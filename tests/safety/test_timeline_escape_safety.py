"""issue #23（A04 / P0 安全止损）：恶意时间线写路径逃逸对抗集。

威胁模型：不可信 timeline.json 的资产字段（gfx clip 的 src / 根级 subtitles_src）
试图把系统写路径引到白名单根之外——../ 穿越、绝对路径、符号链接三种载体，
目标均为「工作目录外的哨兵文件」。全部必须被拒绝且哨兵字节不变，且不得留下
半途快照（部分写入本身也是脏写）。

写根（cache/out 符号链接指向根外）与事务锁文件（预置 <目标名>.lock 符号链接）
同为本批收口面——回执/manifest 走 fstxn，锁路径也应受护（工单原文）。

对照组（不回归验收项）：
  - 外部素材引用（绝对/相对路径，项目外）在读取侧照常工作（validate/gc）；
  - 项目内符号链接（cache 根/资产目录指向项目内真实目录）的写命令照常工作，
    修复不得扩大成「拒绝一切符号链接」。
"""
try:
    from ._support import SandboxCase
except ImportError:  # unittest discover -s tests/safety
    from _support import SandboxCase


class SnapshotTimelineEscape(SandboxCase):
    """snapshot() 相对路径安全校验（工单点名）的 CLI 面对抗。"""

    def gfx(self, src):
        self.tl["video_tracks"] = [
            {"name": "gfx", "kind": "gfx",
             "clips": [{"src": src, "dst_in": 0, "dur": 30}]}]
        self.save_timeline()

    def test_dotdot_gfx_src_cannot_overwrite_outside_snapshot_dir(self):
        # ../ 穿越：落点 dst/../payload.html = cache/snapshots/payload.html
        # （项目内但在本次快照目录之外）——unfixed 行为会 copy2 覆盖该文件。
        payload = self.put(self.sandbox / "payload.html", b"attacker text")
        victim = self.put(self.root / "cache/snapshots/payload.html",
                          b"existing snapshot asset")
        self.gfx("../payload.html")
        result = self.run_vtx("snapshot")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("快照落点", result.stdout)
        self.assertIn("issue #23", result.stdout)
        self.assertEqual(victim.read_bytes(), b"existing snapshot asset")
        # 预置 victim 所在的 cache/snapshots 保留原样：不得新增任何快照目录
        self.assertEqual(list((self.root / "cache/snapshots").iterdir()), [victim])
        self.assertEqual(payload.read_bytes(), b"attacker text")
        self.assert_outside_unchanged()

    def test_deep_dotdot_targeting_outside_project_is_refused(self):
        # 深度穿越直指项目外（快照目录 4 级之上）——引用串照搬到落点即逃逸项目根。
        victim = self.put(self.sandbox / "victim.html", b"outside victim")
        self.gfx("../../../../../../victim.html")
        result = self.run_vtx("snapshot")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("快照落点", result.stdout)
        self.assertEqual(victim.read_bytes(), b"outside victim")
        self.assert_outside_unchanged()

    def test_absolute_asset_is_rejected_before_partial_writes(self):
        # 绝对路径：dst / "/abs" 整体替换前缀。必须先拒后写——半途快照（已写入
        # timeline.json 再崩）同样是脏写。CLI 面预先建好 cache/out 空目录以隔离
        # 命令自身的既定 mkdir 行为，树比对因此只反映本命令的安全语义。
        (self.root / "cache").mkdir(exist_ok=True)
        (self.root / "out").mkdir(exist_ok=True)
        self.gfx(str(self.sentinel))
        before = self.tree(self.sandbox)
        result = self.run_vtx("snapshot")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(str(self.sentinel), result.stdout)
        self.assertEqual(self.tree(self.sandbox), before,
                         "不安全绝对路径必须在任何写入之前被拒绝")
        self.assert_outside_unchanged()

    def test_symlinked_asset_outside_project_is_rejected(self):
        # 根内符号链接指向根外：快照不得把外部内容读穿进项目（含去向报错）。
        (self.root / "gfx").mkdir()
        (self.root / "gfx/linked.html").symlink_to(self.sentinel)
        self.gfx("gfx/linked.html")
        result = self.run_vtx("snapshot")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("快照文本资产", result.stdout)
        self.assertIn(str(self.sentinel), result.stdout)
        self.assert_outside_unchanged()

    def test_subtitles_src_escape_is_rejected(self):
        # 素材字段塞输出路径的另一载体：根级 subtitles_src 同为快照文本资产。
        victim = self.put(self.sandbox / "victim.ass", b"subtitle victim")
        self.tl["subtitles_src"] = "../victim.ass"
        self.save_timeline()
        result = self.run_vtx("snapshot")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("issue #23", result.stdout)
        self.assertEqual(victim.read_bytes(), b"subtitle victim")
        self.assert_outside_unchanged()

    def test_preplanted_snapshots_dir_symlink_cannot_redirect_writes(self):
        # 预置 cache/snapshots → 根外 目录符号链接：整棵快照会被 mkdir/copy2 写穿
        # 到链接目标——快照目录自身先收口在 cache 内。
        (self.root / "cache").mkdir(exist_ok=True)
        (self.root / "cache/snapshots").symlink_to(self.outside,
                                                   target_is_directory=True)
        self.gfx("gfx/card.html")
        self.put(self.root / "gfx/card.html", b"<html>fixture</html>")
        before = self.tree(self.outside)
        result = self.run_vtx("snapshot")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("快照目录", result.stdout)
        self.assertEqual(self.tree(self.outside), before)


class WriteRootEscape(SandboxCase):
    """cache/out 写根与事务锁文件的符号链接逃逸（工单：锁路径也应受护）。"""

    def test_symlinked_out_root_blocks_receipt_writes(self):
        # out → 根外：validate 的 begin_stage/receipt 会把 receipt.json 与
        # .lock 落到项目外——统一写根门必须在首个写动作之前拒绝。
        (self.root / "out").symlink_to(self.outside, target_is_directory=True)
        before = self.tree(self.outside)
        result = self.run_vtx("validate")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("out 写根", result.stdout)
        self.assertIn("issue #23", result.stdout)
        self.assertEqual(self.tree(self.outside), before,
                         "回执/锁文件不得经符号链接写到项目外")

    def test_symlinked_cache_root_blocks_write_commands(self):
        (self.root / "cache").symlink_to(self.outside, target_is_directory=True)
        before = self.tree(self.outside)
        result = self.run_vtx("snapshot")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("cache 写根", result.stdout)
        self.assertEqual(self.tree(self.outside), before)

    def test_receipt_lock_symlink_cannot_spawn_outside(self):
        # 预置 out/receipt.json.lock → 根外（断链）：fstxn 的 open("a+b") 跟随
        # 符号链接、缺失即创建——不收口就会在项目外制造锁文件。
        (self.root / "out").mkdir(exist_ok=True)
        (self.root / "out/receipt.json.lock").symlink_to(
            self.outside / "spawn.lock")
        result = self.run_vtx("validate")
        self.assertNotEqual(result.returncode, 0,
                            result.stdout + result.stderr)
        self.assertIn("事务锁文件", result.stderr)
        self.assertIn("issue #23", result.stderr)
        self.assertFalse((self.outside / "spawn.lock").exists(),
                         "锁文件不得在项目外被创建")
        self.assertFalse((self.root / "out/receipt.json").exists(),
                         "锁守卫触发时回执不得落盘")
        self.assert_outside_unchanged()


class LegitUseControl(SandboxCase):
    """对照组：外部素材引用与项目内符号链接不得被误杀（验收原文）。"""

    def test_external_relative_and_absolute_assets_still_read(self):
        # 用户把素材放在项目外、以相对/绝对路径引用：读侧（validate 的 gfx html
        # 存在性检查、gc 的 gfx_key 字节读取）照常放行。
        shared = self.put(self.sandbox / "shared.html", b"<html>external</html>")
        for src in ("../shared.html", str(shared)):
            with self.subTest(src=src):
                self.tl["video_tracks"] = [
                    {"name": "gfx", "kind": "gfx",
                     "clips": [{"src": src, "dst_in": 0, "dur": 30}]}]
                self.save_timeline()
                self.assert_ok(self.run_vtx("validate"))
                self.assert_ok(self.run_vtx("gc"))
                self.assertEqual(shared.read_bytes(), b"<html>external</html>")
        self.assert_outside_unchanged()

    def test_inside_project_symlinked_cache_root_allows_write_commands(self):
        # cache 根符号链接指向项目内真实目录：resolve 后仍在项目根内 = 允许
        # （不回归正常软链接用法；指向外才拒，见 WriteRootEscape）。
        store = self.put(self.root / "realstore/.keep", b"store")
        (self.root / "cache").symlink_to(self.root / "realstore",
                                         target_is_directory=True)
        self.assert_ok(self.run_vtx("validate"))
        self.assertEqual(store.read_bytes(), b"store")
        self.assert_outside_unchanged()

    def test_inside_project_symlinked_asset_dir_snapshot_works(self):
        # 资产目录符号链接指向项目内：快照照常收录（链接解析后仍在项目根内），
        # 修复不得扩大成拒绝一切符号链接资产。
        self.put(self.root / "gfxreal/card.html", b"<html>via link</html>")
        (self.root / "gfxlink").symlink_to(self.root / "gfxreal",
                                           target_is_directory=True)
        self.tl["video_tracks"] = [
            {"name": "gfx", "kind": "gfx",
             "clips": [{"src": "gfxlink/card.html", "dst_in": 0, "dur": 30}]}]
        self.save_timeline()
        result = self.run_vtx("snapshot")
        self.assert_ok(result)
        copies = list((self.root / "cache/snapshots").glob("*/gfxlink/card.html"))
        self.assertEqual(len(copies), 1, "项目内符号链接资产应被正常快照")
        self.assertEqual(copies[0].read_bytes(), b"<html>via link</html>")
        self.assert_outside_unchanged()
