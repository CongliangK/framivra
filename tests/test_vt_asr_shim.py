#!/usr/bin/env python3
"""vt.py ASR 降级 shim 的 base_url 校验回归（issue #141 红队 F2）。

红队实测缺陷：shim 旧实现 `":18081" in str(base_url).rsplit("/", 2)[-1]` 为
字符串包含判断——trailing slash（末段为空）、带路径（末段为 "asr"）、大小写
scheme（rsplit 后丢掉 host 段）等形态漏拒，校验失败仍发 GET /health。
本测试覆盖红队实测 4 形态 + 与 independence.validate_base_url 的语义等价性，
并以子进程跑 vt.py init（--json、死代理兜底）断言校验失败时零请求。

铁律：绝不向真实 18081 发任何字节——子进程用例设置 http(s)_proxy 指向本机
丢弃端口，任何意外请求只会本地连接拒绝后走「未运行」分支被断言捕获。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "asr"))

import vt  # noqa: E402
import independence  # noqa: E402


def _raises(fn, *a):
    try:
        fn(*a)
    except independence.AsrIndependenceError:
        return "independence"
    except vt._AsrIndShim.AsrIndependenceError:
        return "shim"
    return None


class ShimBaseUrlSemanticsTest(unittest.TestCase):
    """红队实测 4 形态 + 等价性语料（shim ↔ independence 单一语义）。"""

    BAD = [
        "http://127.0.0.1:18081/",          # trailing slash（旧实现末段为空 → 漏拒）
        "http://127.0.0.1:18081/asr",       # 带路径（旧实现末段为 "asr" → 漏拒）
        "127.0.0.1:18081",                  # 裸 host:port（非 http:// 形式亦须拒绝）
        "HTTP://127.0.0.1:18081/",          # 大小写 scheme（旧实现丢 host 段 → 漏拒）
        "http://localhost:18081",
        "http://127.0.0.1:18081",
        "http://[::1]:18081/",
        "https://127.0.0.1:18081",          # 非 http scheme
        "http://127.0.0.1:十八",            # 端口非法
        "",
        None,
    ]
    GOOD = [
        "http://127.0.0.1:18085",
        "http://127.0.0.1:18085/",
        "http://127.0.0.1:18085/asr",
        "http://localhost:19001",
    ]

    def test_shim_rejects_ime_forms(self):
        for url in self.BAD:
            with self.subTest(url=url):
                self.assertIsNotNone(_raises(vt._AsrIndShim.validate_base_url, url),
                                     "shim 漏拒：%r" % (url,))

    def test_shim_accepts_project_forms(self):
        for url in self.GOOD:
            with self.subTest(url=url):
                vt._AsrIndShim.validate_base_url(url)  # 不得 raise

    def test_shim_equivalent_to_independence(self):
        """shim 与单一接口逐条同判（raise/通过 双向一致）——防降级面漂移。"""
        for url in self.BAD + self.GOOD:
            with self.subTest(url=url):
                self.assertEqual(
                    _raises(vt._AsrIndShim.validate_base_url, url) is not None,
                    _raises(independence.validate_base_url, url) is not None,
                    "shim 与 independence 判定不一致：%r" % (url,))

    def test_shim_asr_enabled_parity(self):
        """issue #142：shim.asr_enabled 与 independence 严格解析逐条同判。"""
        cases = [{}, {"asr": {}}, {"asr": {"enabled": True}},
                 {"asr": {"enabled": "true"}}, {"asr": {"enabled": "TRUE"}},
                 {"asr": {"enabled": "maybe"}}, {"asr": {"enabled": 1}},
                 {"asr": {"enabled": "0"}}, {"asr": {"enabled": None}},
                 {"asr": {"enabled": [True]}}, {"asr": None}, 5]
        for cfg in cases:
            with self.subTest(cfg=cfg):
                self.assertEqual(vt._AsrIndShim.asr_enabled(cfg),
                                 independence.asr_enabled(cfg))

    def test_shim_preflight_legacy_keys(self):
        with self.assertRaises(vt._AsrIndShim.AsrIndependenceError):
            vt._AsrIndShim.preflight_config({"asr": {"ime_root": "/x"}})
        vt._AsrIndShim.preflight_config({"asr": {}})   # 干净配置通过


class DoctorNoRequestOnBadBaseUrlTest(unittest.TestCase):
    """校验失败时绝不发 GET /health：子进程跑 vt.py init --json，VTX_CONFIG 指向
    base_url=IME 端口带路径形态；死代理兜底确保任何意外请求不触真实 18081。"""

    def test_init_rejects_before_any_request(self):
        with tempfile.TemporaryDirectory(prefix="vtx-shim-") as td:
            # init 会在仓根生成被 gitignore 的 config.local.json（若缺）——
            # 快照/还原，测试后不残留工作区状态。
            repo_cfg = ROOT / "config.local.json"
            pre_existing = repo_cfg.exists()
            cfg = Path(td) / "config.local.json"
            cfg.write_text(json.dumps(
                {"asr": {"base_url": "http://127.0.0.1:18081/asr"}}),
                encoding="utf-8")
            env = dict(os.environ)
            env["VTX_CONFIG"] = str(cfg)
            env["VTX_DOCTOR_OFFLINE"] = "1"
            # 兜底：任何意外出网请求只会在本机丢弃端口连接拒绝（不触真实 18081）
            env["http_proxy"] = env["https_proxy"] = "http://127.0.0.1:9"
            env["HTTP_PROXY"] = env["HTTPS_PROXY"] = "http://127.0.0.1:9"
            r = subprocess.run([sys.executable, str(ROOT / "vt.py"), "init", "--json"],
                               env=env, capture_output=True, text=True, timeout=180)
            out = r.stdout + r.stderr
            self.assertIn("独立性校验失败", out, "未出现独立性拒绝诊断：%s" % out)
            self.assertNotIn('"asr_sidecar": {\n      "ok": true', out)
            if not pre_existing and repo_cfg.exists():
                repo_cfg.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
