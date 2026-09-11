#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_api_mock.py · mock 本地 http.server 全链测试（方案 §9.3）。

零真实 API 调用（0.1 元/张付费纪律）：mock 提交（返回订单 id）→ 轮询
0→1→2 → 下载 mock 图 → cover-crop 落位。覆盖：
  - 全链三种 kind 联动载荷（bg4k=4K/16:9、bg1080p=2K/16:9、vertical=4K/9:16）
  - raw 不联动；gpt 端点无 _image 后缀；result 四形态防御解析
  - status=3 审核拒绝：中文报错、不重试、不重复提交
  - 付费门：无 --yes 且非 TTY → 0 次提交；--yes → 1 次；batch>50 二次确认
  - URL 失效：下载失败重试 ≤2 次后上抛可行动错误
  - --ref 15 张前置报错 / 14 张通过；空项目目录自建落位；--task-id 恢复
CLI 走 subprocess（stdin=DEVNULL 保证非 TTY）；mock 输入图 ffmpeg 合成（KB 级）。
"""
import glob
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import _loader

IMGEN_DIR = _loader.IMGEN_DIR
IMGEN_CLI = _loader.IMGEN_CLI

postprocess = _loader.load("postprocess")

TEST_KEY = "test-key-0001"


class DetailScriptError(AssertionError):
    """detail 脚本未配置（测试编写错误）。"""


class DetailBusinessError(Exception):
    """注入的业务错误：detail 恒返回 {"code":!=200, "msg":...}（HTTP 200）。"""

    def __init__(self, payload):
        super().__init__(str(payload))
        self.payload = payload


class MockState:
    def __init__(self, images):
        self.lock = threading.Lock()
        self.images = images  # path -> bytes（如 /img/a.png）
        self.submit_count = 0
        self.submits = []  # [(path, payload, headers)]
        self.detail_count = 0
        self.img_gets = 0
        self.missing_gets = 0
        self.detail_script = []  # 依次弹出的 detail data；耗尽重复最后一条
        self.detail_error = None  # 设置后 detail 恒返回该业务错误 JSON

    def next_detail(self):
        with self.lock:
            self.detail_count += 1
            if self.detail_error is not None:
                raise DetailBusinessError(self.detail_error)
            if not self.detail_script:
                raise DetailScriptError("detail_script 未配置")
            if len(self.detail_script) == 1:
                return self.detail_script[0]
            return self.detail_script.pop(0)


def _make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass  # 静默（零 logging 纪律同仓风格）

        def _json(self, obj, status=200):
            data = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except ValueError:
                payload = {}
            with state.lock:
                state.submit_count += 1
                state.submits.append((self.path, payload, dict(self.headers)))
            if self.path in ("/api/async/image_nanoBanana2", "/api/async/image_gpt"):
                self._json({"code": 200, "data": {"id": f"task-{state.submit_count}", "count": "1"}})
            else:
                self._json({"code": 404, "msg": "unknown endpoint"}, status=404)

        def do_GET(self):
            if self.path.startswith("/api/async/detail"):
                try:
                    info = state.next_detail()
                except DetailBusinessError as exc:
                    self._json(exc.payload)  # HTTP 200 + 业务 code（速创语义）
                    return
                except DetailScriptError as exc:
                    self._json({"code": 500, "msg": str(exc)}, status=500)
                    return
                self._json({"code": 200, "data": info})
            elif self.path in state.images:
                with state.lock:
                    state.img_gets += 1
                    data = state.images[self.path]
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif self.path == "/img/missing.png":
                with state.lock:
                    state.missing_gets += 1
                self._json({"error": "expired"}, status=404)
            else:
                self._json({"error": "not found"}, status=404)

    return Handler


class MockSuchuang:
    """本地 mock 速创 API（随机端口；绝不触网）。"""

    def __init__(self, images):
        self.state = MockState(images)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self.state))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def url(self, image_path):
        return self.base + image_path

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _make_png_bytes(width, height, color="0x336699"):
    """ffmpeg 合成纯色 PNG 并读回 bytes（KB 级）。"""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        path = fh.name
    try:
        _loader.make_png(path, width, height, color=color)
        return Path(path).read_bytes()
    finally:
        os.unlink(path)


class ApiMockTestCase(unittest.TestCase):
    """公共脚手架：mock server + 隔离 env 的 CLI subprocess。"""

    @classmethod
    def setUpClass(cls):
        # 四种 kind 对应的 mock 生成结果（尺寸 ≥ 各目标，触发纯降采样）
        cls.images = {
            "/img/a.png": _make_png_bytes(3840, 2160),  # bg4k（4K 16:9 实出模拟）
            "/img/b.png": _make_png_bytes(2752, 1536),  # bg1080p（2K 16:9 实出模拟）
            "/img/c.png": _make_png_bytes(3096, 5504),  # vertical（4K 9:16 实出模拟）
            "/img/d.png": _make_png_bytes(1024, 1024),  # raw 默认 1K
        }
        cls.mock = MockSuchuang(cls.images)

    @classmethod
    def tearDownClass(cls):
        cls.mock.stop()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="imgen_api_test_")
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "proj"
        # 重置 mock 计数（state 是类级共享，跨用例累积会污染绝对值断言）
        state = self.mock.state
        with state.lock:
            state.submit_count = 0
            state.submits = []
            state.detail_count = 0
            state.img_gets = 0
            state.missing_gets = 0
        # 默认 detail 脚本：0（排队）→ 1（生成中）→ 2（成功 + a.png）
        state.detail_script = [
            {"status": 0},
            {"status": 1},
            {"status": 2, "result": [self.mock.url("/img/a.png")]},
        ]

    def run_cli(self, args, tty_stdin=False):
        env = os.environ.copy()
        env.pop("SC_API_KEY", None)
        env.pop("NANOBANANA2_API_KEY", None)
        env["IMGEN_CONFIG"] = os.path.join(self._tmp.name, "no_such_config.json")
        env["IMGEN_API_BASE"] = self.mock.base
        env["IMGEN_POLL_INTERVAL"] = "0.05"
        env["IMGEN_POLL_TIMEOUT"] = "20"
        env["XDG_CONFIG_HOME"] = os.path.join(self._tmp.name, "xdg")  # 隔离真实 key 存储
        return subprocess.run(
            [sys.executable, IMGEN_CLI] + args,
            capture_output=True, text=True, timeout=180,
            stdin=subprocess.DEVNULL if not tty_stdin else None,
            env=env,
        )

    def gen_args(self, prompt, kind, *extra):
        return ["gen", prompt, "--out", str(self.out), "--kind", kind,
                "--key", TEST_KEY, "--yes", *extra]

    def report_files(self):
        return sorted(glob.glob(str(self.out / "cache" / "reports" / "imgen_jobs_*.json")))

    def tmp_png_leftovers(self):
        return sorted(glob.glob(str(self.out / "cache" / "tmp" / "**" / "*.png"), recursive=True))


class FullChainTest(ApiMockTestCase):
    def test_full_chain_bg4k(self):
        proc = self.run_cli(self.gen_args("深色科技感演播室背景", "bg4k"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 提交载荷断言：kind 联动 nano 端点 + size=4K + aspectRatio=16:9 + 裸 key 头
        self.assertEqual(self.mock.state.submit_count, 1)
        path, payload, headers = self.mock.state.submits[0]
        self.assertEqual(path, "/api/async/image_nanoBanana2")
        self.assertEqual(payload["size"], "4K")
        self.assertEqual(payload["aspectRatio"], "16:9")
        self.assertEqual(headers.get("Authorization"), TEST_KEY)  # 裸 key，无 Bearer
        # 轮询 0→1→2
        self.assertEqual(self.mock.state.detail_count, 3)
        # 产物：精确 3840×2160 rgb24；目录自建（out 本不存在）
        final = self.out / "assets" / "bg" / "background_4k.png"
        self.assertTrue(final.is_file())
        info = postprocess.probe_image(final)
        self.assertEqual((info["width"], info["height"], info["pix_fmt"]), (3840, 2160, "rgb24"))
        # stdout 产物路径（管道友好）；stderr 有进度
        self.assertIn("background_4k.png", proc.stdout)
        self.assertIn("提交成功", proc.stderr)
        # 记账 + tmp 清理
        reports = self.report_files()
        self.assertEqual(len(reports), 1)
        report = json.loads(Path(reports[0]).read_text(encoding="utf-8"))
        self.assertTrue(report["entries"][0]["task_id"])
        self.assertEqual(report["entries"][0]["status"], "done")
        self.assertEqual(self.tmp_png_leftovers(), [])

    def test_full_chain_bg1080p_payload_and_output(self):
        self.mock.state.detail_script = [
            {"status": 2, "result": [self.mock.url("/img/b.png")]},
        ]
        proc = self.run_cli(self.gen_args("口播底图", "bg1080p"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        _path, payload, _headers = self.mock.state.submits[0]
        self.assertEqual(payload["size"], "2K")
        self.assertEqual(payload["aspectRatio"], "16:9")
        final = self.out / "assets" / "bg" / "background_1080p.png"
        info = postprocess.probe_image(final)
        self.assertEqual((info["width"], info["height"]), (1920, 1080))

    def test_full_chain_vertical_payload_and_output(self):
        self.mock.state.detail_script = [
            {"status": 2, "result": [self.mock.url("/img/c.png")]},
        ]
        proc = self.run_cli(self.gen_args("竖版口播背景", "vertical"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        _path, payload, _headers = self.mock.state.submits[0]
        self.assertEqual(payload["size"], "4K")
        self.assertEqual(payload["aspectRatio"], "9:16")
        final = self.out / "assets" / "bg" / "background_vertical.png"
        info = postprocess.probe_image(final)
        self.assertEqual((info["width"], info["height"]), (1080, 1920))

    def test_raw_no_linkage(self):
        self.mock.state.detail_script = [
            {"status": 2, "result": [self.mock.url("/img/d.png")]},
        ]
        proc = self.run_cli(self.gen_args("留档原图", "raw"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        _path, payload, _headers = self.mock.state.submits[0]
        self.assertEqual(payload["size"], "1K")  # raw 不联动：默认 1K
        self.assertNotIn("aspectRatio", payload)  # auto 省略
        archived = glob.glob(str(self.out / "assets" / "imgen" / "*.png"))
        self.assertEqual(len(archived), 1)
        self.assertRegex(Path(archived[0]).name, r"^\d{8}T\d{6}Z-1\.png$")
        info = postprocess.probe_image(Path(archived[0]))  # 仅尺寸校验（能读出）
        self.assertEqual((info["width"], info["height"]), (1024, 1024))

    def test_gpt_endpoint_and_payload(self):
        # 防端点回归：gpt = POST /api/async/image_gpt（无 _image 后缀）；size=比例
        self.mock.state.detail_script = [
            {"status": 2, "result": [self.mock.url("/img/d.png")]},
        ]
        proc = self.run_cli(self.gen_args("写实风格配图", "raw", "--model", "gpt-image", "--size", "16:9"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        path, payload, _headers = self.mock.state.submits[0]
        self.assertEqual(path, "/api/async/image_gpt")
        self.assertEqual(payload["size"], "16:9")
        self.assertNotIn("aspectRatio", payload)

    def test_result_wrapper_dict_shape(self):
        # result 四形态防御解析：wrapper dict {"images": [...]}
        self.mock.state.detail_script = [
            {"status": 1},
            {"status": 2, "result": {"images": [self.mock.url("/img/b.png")]}},
        ]
        proc = self.run_cli(self.gen_args("形态测试", "bg1080p"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        final = self.out / "assets" / "bg" / "background_1080p.png"
        self.assertTrue(final.is_file())

    def test_task_id_resume_skips_submit(self):
        # 断线恢复：按 job id 只重轮询+下载+后处理（不产生新提交）
        self.mock.state.detail_script = [
            {"status": 2, "result": [self.mock.url("/img/a.png")]},
        ]
        proc = self.run_cli([
            "gen", "恢复任务", "--out", str(self.out), "--kind", "bg4k",
            "--key", TEST_KEY, "--task-id", "task-resume-1",
        ])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.mock.state.submit_count, 0)  # 零新提交零扣费
        self.assertIn("恢复模式", proc.stderr)
        final = self.out / "assets" / "bg" / "background_4k.png"
        self.assertTrue(final.is_file())


class KindOverrideTest(ApiMockTestCase):
    def test_explicit_2k_with_bg4k_warns_but_proceeds(self):
        # 显式 --size 2K 配 bg4k → 警告文案含插值倍数且照做（用户主权）
        self.mock.state.detail_script = [
            {"status": 2, "result": [self.mock.url("/img/b.png")]},  # 2K 实出 → 真实放大 crop
        ]
        proc = self.run_cli(self.gen_args("低档位测试", "bg4k", "--size", "2K"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("放大", proc.stderr)
        self.assertIn("1.4", proc.stderr)  # 2160/1536≈1.41（方案 §5.2 ~1.406 同源）
        _path, payload, _headers = self.mock.state.submits[0]
        self.assertEqual(payload["size"], "2K")  # 照做
        final = self.out / "assets" / "bg" / "background_4k.png"
        info = postprocess.probe_image(final)
        self.assertEqual((info["width"], info["height"]), (3840, 2160))  # 断言仍精确

    def test_explicit_model_gpt_with_kind_warns(self):
        proc = self.run_cli(self.gen_args("gpt 背景测试", "bg4k", "--model", "gpt-image"))
        # 提交阶段成功与否都应先给出分辨率不受控警告
        self.assertIn("不受控", proc.stderr)


class PayGateTest(ApiMockTestCase):
    def test_no_yes_non_tty_aborts_with_zero_submits(self):
        proc = self.run_cli([
            "gen", "付费门测试", "--out", str(self.out), "--kind", "bg4k", "--key", TEST_KEY,
        ])
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.mock.state.submit_count, 0)  # 0 次提交
        self.assertIn("未产生任何提交", proc.stderr)
        self.assertIn("不退款", proc.stderr)  # 审核拒绝不退款进确认文案
        self.assertEqual(self.report_files(), [])

    def test_yes_submits_once(self):
        proc = self.run_cli(self.gen_args("确认测试", "bg4k"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.mock.state.submit_count, 1)
        self.assertIn("0.1", proc.stderr)  # 费用文案

    def test_bad_key_fails_before_submit(self):
        # --key 指向不存在文件 → 提交前失败（确定性场景；「链上全缺」的解析
        # 行为由 test_secrets.py 全缺用例覆盖——keyring 属系统服务无法在
        # subprocess 里隔离真实机器状态）
        proc = self.run_cli(["gen", "坏key测试", "--out", str(self.out), "--kind", "raw",
                             "--yes", "--key", "/nonexistent/imgen-test-key.txt"])
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.mock.state.submit_count, 0)
        self.assertIn("API Key", proc.stderr)

    def test_batch_over_50_needs_double_confirm_non_tty_aborts(self):
        items = [{"prompt": f"批量第{i}张", "kind": "raw"} for i in range(51)]
        spec = Path(self._tmp.name) / "big_batch.json"
        spec.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        proc = self.run_cli(["batch", str(spec), "--out", str(self.out),
                             "--key", TEST_KEY, "--yes"])  # 即使 --yes 也需二次确认
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.mock.state.submit_count, 0)
        self.assertIn("50", proc.stderr)


class FailurePathTest(ApiMockTestCase):
    def test_status3_rejected_no_retry_no_resubmit(self):
        # 审核拒绝：明确中文报错、立即上抛（detail 不再重试）、不重复提交
        self.mock.state.detail_script = [
            {"status": 0},
            {"status": 3, "message": "此内容可能违反了我们的内容政策"},
        ]
        proc = self.run_cli(self.gen_args("敏感内容测试", "bg4k"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("审核拒绝", proc.stderr)
        self.assertEqual(self.mock.state.submit_count, 1)  # 不重复提交
        self.assertEqual(self.mock.state.detail_count, 2)  # status=3 立即上抛，不再轮询
        self.assertFalse((self.out / "assets" / "bg" / "background_4k.png").exists())
        report = json.loads(Path(self.report_files()[0]).read_text(encoding="utf-8"))
        self.assertEqual(report["entries"][0]["status"], "rejected")

    def test_invalid_url_download_retries_then_raises(self):
        # URL 失效（404）：下载重试 ≤2 次后上抛可行动错误（总请求 = 1+2 = 3）
        self.mock.state.detail_script = [
            {"status": 2, "result": [self.mock.url("/img/missing.png")]},
        ]
        proc = self.run_cli(self.gen_args("失效链接测试", "bg4k"))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.mock.state.missing_gets, 3)  # 初次 + 重试 2 次
        self.assertIn("下载失败", proc.stderr)
        self.assertIn("--task-id", proc.stderr)  # 可行动恢复提示
        self.assertFalse((self.out / "assets" / "bg" / "background_4k.png").exists())
        self.assertEqual(self.tmp_png_leftovers(), [])  # 临时件已清

    def test_submit_4xx_surfaces_immediately(self):
        # detail 返回业务 4xx（如余额不足 402）→ 立即上抛而非轮询到超时
        self.mock.state.detail_script = [{"status": 0}]
        self.mock.state.detail_error = {"code": 402, "msg": "余额不足"}
        try:
            proc = self.run_cli(self.gen_args("余额不足测试", "bg4k"))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("查询失败", proc.stderr)
            self.assertIn("余额不足", proc.stderr)
            # 4xx 不可重试：detail 只查一次即上抛
            self.assertEqual(self.mock.state.detail_count, 1)
        finally:
            self.mock.state.detail_error = None


class RefLimitTest(ApiMockTestCase):
    def test_15_refs_rejected_before_submit(self):
        refs = [f"https://example.com/r{i}.png" for i in range(15)]
        proc = self.run_cli(["gen", "参考图超限", "--out", str(self.out), "--kind", "raw",
                             "--key", TEST_KEY, "--yes", "--ref"] + refs)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.mock.state.submit_count, 0)
        self.assertIn("14", proc.stderr)

    def test_14_refs_pass(self):
        self.mock.state.detail_script = [
            {"status": 2, "result": [self.mock.url("/img/d.png")]},
        ]
        refs = [f"https://example.com/r{i}.png" for i in range(14)]
        proc = self.run_cli(["gen", "参考图满额", "--out", str(self.out), "--kind", "raw",
                             "--key", TEST_KEY, "--yes", "--ref"] + refs)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.mock.state.submit_count, 1)
        _path, payload, _headers = self.mock.state.submits[0]
        self.assertEqual(len(payload["urls"]), 14)


class BatchTest(ApiMockTestCase):
    def test_batch_two_items(self):
        self.mock.state.detail_script = [
            {"status": 2, "result": [self.mock.url("/img/b.png")]},  # bg1080p 条
            {"status": 2, "result": [self.mock.url("/img/d.png")]},  # raw 条
        ]
        spec = Path(self._tmp.name) / "batch.json"
        spec.write_text(json.dumps([
            {"prompt": "批量横版", "kind": "bg1080p"},
            {"prompt": "批量留档", "kind": "raw"},
        ], ensure_ascii=False), encoding="utf-8")
        proc = self.run_cli(["batch", str(spec), "--out", str(self.out),
                             "--key", TEST_KEY, "--yes"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.mock.state.submit_count, 2)
        self.assertIn("0.2", proc.stderr)  # 2 张总价文案
        self.assertTrue((self.out / "assets" / "bg" / "background_1080p.png").is_file())
        self.assertEqual(len(glob.glob(str(self.out / "assets" / "imgen" / "*.png"))), 1)
        report = json.loads(Path(self.report_files()[0]).read_text(encoding="utf-8"))
        self.assertEqual(len(report["entries"]), 2)


if __name__ == "__main__":
    unittest.main()
