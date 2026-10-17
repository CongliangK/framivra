"""tests/test_demo_fixture.py — standard-demo 冻结 fixture 合同与反例（issue #177 / #91-1）。

两层：
  - 合同/反例单测（无需 ffmpeg）：manifest 结构验证、严格 JSON（重复键/非有限数）、
    prepare 输出目录边界（已有目录/symlink 组件/仓内/HOME/缓存/缺父目录）、
    路径逃逸拒绝、preflight 只读且区分 prepare/render 依赖。
  - 真实 FFmpeg 生成测试（VTX_TEST_DEMO_TOOLCHAIN=1 显式 opt-in，绑定冻结
    工具链）：真实 prepare 两份全新目录 → encoded
    hash 相等 + 生产 validate exit0 + verify 以仓内冻结锚通过 + 篡改反例全拒。

渲染/preview/QC/CEF 结论保持 UNRUN；本测试不做性能测量。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import scripts.demo as demo  # noqa: E402  (namespace package under repo root)

FFMPEG = os.environ.get('VTX_TEST_FFMPEG') or shutil.which('ffmpeg')

# 真实生成测试绑定冻结工具链（ffmpeg 8.1.3 双锚）。CI 的 ubuntu ffmpeg 永远
# unsupported，故该类不注册进 CI strict ffmpeg 零 skip 批次，而是独立的
# demo-toolchain 批次（scripts/ci_checks.py gate 'demo-toolchain'）：仅在
# VTX_TEST_DEMO_TOOLCHAIN=1 且工具链与冻结清单匹配时执行；普通本地全量跑
# 该类以 skip 形式列出（原因见装饰器），不会静默消失——ci_checks 的
# media_inventory 源码扫描按 VTX_TEST_DEMO_TOOLCHAIN 锚点强制登记该类。
DEMO_TOOLCHAIN = bool(os.environ.get('VTX_TEST_DEMO_TOOLCHAIN'))


class ContractTests(unittest.TestCase):
    """不依赖 ffmpeg 的合同与反例。"""

    def test_frozen_manifest_valid(self):
        manifest = demo.load_frozen_manifest()
        self.assertEqual(manifest['benchmark_id'], 'framivra-standard-demo')
        demo.validate_manifest(manifest)

    def test_manifest_rejects_unknown_benchmark_and_bad_hash(self):
        with self.assertRaises(demo.DemoError):
            demo.validate_manifest({'benchmark_id': 'other', 'schema_version': 1})
        manifest = demo.load_frozen_manifest()
        broken = json.loads(json.dumps(manifest))
        broken['profiles']['quick']['files']['v01.mp4']['sha256'] = 'zz'
        with self.assertRaises(demo.DemoError):
            demo.validate_manifest(broken)

    def test_strict_json_rejects_duplicate_keys_and_nonfinite(self):
        with self.assertRaises(demo.DemoError):
            demo.load_json_strict('{"a": 1, "a": 2}')
        with self.assertRaises(demo.DemoError):
            demo.load_json_strict('{"a": NaN}')

    def test_prepare_output_path_boundaries(self):
        with tempfile.TemporaryDirectory(prefix='demo177-t-') as tmp:
            tmp = Path(tmp)
            existing = tmp / 'exists'
            existing.mkdir()
            linkparent = tmp / 'link'
            linkparent.symlink_to(tmp, target_is_directory=True)
            cases = [
                existing,                                   # 已有目录
                linkparent / 'new',                         # symlink 组件
                tmp / 'missing' / 'new',                    # 父目录不存在
                Path(REPO) / 'evil-out',                    # 仓内
                Path.home() / 'demo177-evil',               # HOME 内
                tmp / '',                                   # 空路径展开后仍拒：父=已存在 tmp？不，空值拒
            ]
            for index, case in enumerate(cases):
                raw = str(case) if index < len(cases) - 1 else '  '
                with self.assertRaises(demo.DemoError, msg=raw):
                    demo.check_output_path(raw)

    def test_verify_path_escape_rejected(self):
        with tempfile.TemporaryDirectory(prefix='demo177-t-') as tmp:
            base = Path(tmp)
            with self.assertRaises(demo.DemoError):
                demo.safe_relpath(base, '/etc/passwd')
            with self.assertRaises(demo.DemoError):
                demo.safe_relpath(base, '../outside')
            hidden = base / 'link.txt'
            hidden.symlink_to('/etc/hostname')
            with self.assertRaises(demo.DemoError):
                demo.safe_relpath(base, 'link.txt')

    def test_preflight_readonly_and_split_deps(self):
        result = subprocess.run(
            [sys.executable, 'scripts/demo.py', 'preflight', '--profile', 'quick'],
            capture_output=True, cwd=REPO)
        # rc 语义（issue #177，docs/demo-fixture.md）：preflight 是只读诊断，
        # 检查本身可执行并完整输出 JSON 即 rc 0——与 ffmpeg 是否在 PATH 无关；
        # 依赖可用性反映在 checks[].ok / prepare_ready，硬失败闭口在 prepare。
        self.assertEqual(result.returncode, 0)
        report = json.loads(result.stdout)
        self.assertEqual(report['schema_version'], 1)
        self.assertTrue(report['read_only'])
        self.assertTrue(report['prepare_supported'])
        prepare_keys = ('python', 'ffmpeg', 'ffprobe', 'profile', 'fonts',
                        'benchmark_manifest')
        self.assertEqual(report['prepare_ready'],
                         all(report['checks'][k]['ok'] for k in prepare_keys))
        for key in prepare_keys:
            self.assertIn('ok', report['checks'][key])
        render = report['render']
        self.assertFalse(render['ready'])
        self.assertIsNone(render['cef'])
        self.assertIn('CEF', render['cef_reason'])
        for field in ('render', 'preview', 'qc', 'cef', 'visual'):
            self.assertIn(field, report['unrun'])

    def test_preflight_gfx_stress_is_reference_only(self):
        result = subprocess.run(
            [sys.executable, 'scripts/demo.py', 'preflight',
             '--profile', 'gfx-stress'],
            capture_output=True, cwd=REPO)
        self.assertEqual(result.returncode, 0)
        report = json.loads(result.stdout)
        self.assertFalse(report['prepare_supported'])
        self.assertIsNone(report['prepare_ready'])

    def test_gfx_stress_references_frozen_suite_only(self):
        profile = demo.load_json_file_strict(
            demo.PROFILE_DIRS['gfx-stress'] / 'profile.json')
        reference = profile['reference']
        suite = demo.ROOT / reference['suite_manifest']
        self.assertEqual(demo.sha256_file(suite), reference['suite_manifest_sha256'])
        self.assertNotIn('S8', reference['scenarios'])
        for rel, digest in reference['page_sha256'].items():
            self.assertEqual(demo.sha256_file(demo.ROOT / rel), digest)


@unittest.skipUnless(DEMO_TOOLCHAIN, 'real-generation tests bind the frozen '
                     'demo toolchain: run the strict demo-toolchain batch via '
                     '`python3 scripts/ci_checks.py demo-toolchain` in a '
                     'frozen-toolchain (ffmpeg 8.1.3 dual-anchor) environment')
class RealGenerationTests(unittest.TestCase):
    """真实 FFmpeg 生成：确定性、生产 validate、冻结锚 verify、篡改反例。

    只在显式 opt-in（VTX_TEST_DEMO_TOOLCHAIN=1）时注册；本类一旦执行就
    不允许 skip——工具链不匹配冻结清单是硬失败并给出可诊断原因。
    """

    @classmethod
    def setUpClass(cls):
        if not FFMPEG:
            raise AssertionError(
                'VTX_TEST_DEMO_TOOLCHAIN=1 requires ffmpeg on PATH or '
                'VTX_TEST_FFMPEG (frozen demo toolchain environment)')
        cls.tmp = tempfile.mkdtemp(prefix='demo177-real-')
        cls.projects = []
        for profile in ('quick', 'standard'):
            for run in (1, 2):
                output = Path(cls.tmp) / ('%s-%d' % (profile, run))
                cls.projects.append((profile, output))
                result = subprocess.run(
                    [sys.executable, 'scripts/demo.py', 'prepare',
                     '--profile', profile, '--output', str(output),
                     '--ffmpeg', FFMPEG],
                    capture_output=True, cwd=REPO)
                if result.returncode != 0:
                    stderr = result.stderr.decode('utf-8', 'replace')
                    if 'toolchain ffmpeg identity' in stderr:
                        # 该批次已显式 opt-in 冻结工具链：不匹配=硬失败（不
                        # skip，不静默当通过）。确定性生成证据只能由冻结工具
                        # 链环境提供，原因随 stderr 一并给出以便诊断。
                        raise AssertionError(
                            'demo fixture toolchain unsupported: ffmpeg/ffprobe '
                            'identity != frozen manifest (this batch requires '
                            'the frozen 8.1.3 dual-anchor toolchain): %s'
                            % stderr.strip())
                    raise AssertionError('%s prepare failed: %s'
                                         % (profile, result.stderr))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_f1_validate_exit0_and_raw_logs_kept(self):
        for _, output in self.projects:
            realization = json.loads((output / 'realization.json').read_text())
            stages = {s['stage']: s.get('exit_code') for s in realization['stages']}
            self.assertEqual(stages['validate'], 0)
            self.assertEqual(stages['generate-media'], 0)
            self.assertEqual(stages['hash-anchor-verify'], 0)
            self.assertTrue((output / 'logs' / 'validate.stdout.json').exists())
            agent = json.loads((output / 'logs' / 'validate.stdout.json')
                               .read_text())
            self.assertEqual(agent['exit_code'], 0)
            # UNRUN 字段不进 PASS 统计
            self.assertTrue(all(v == 'UNRUN'
                                for v in realization['results'].values()))

    def test_f2_same_toolchain_outputs_hash_equal(self):
        for profile in ('quick', 'standard'):
            manifest = demo.load_frozen_manifest()['profiles'][profile]
            media = sorted(manifest['decoded'])
            for name in media:
                first = Path(self.tmp) / ('%s-1' % profile) / name
                second = Path(self.tmp) / ('%s-2' % profile) / name
                digest = demo.sha256_file(first)
                self.assertEqual(digest, demo.sha256_file(second))
                self.assertEqual(digest, manifest['files'][name]['sha256'])

    def test_f2_verify_decoded_specs_against_frozen_anchor(self):
        for profile in ('quick', 'standard'):
            output = Path(self.tmp) / ('%s-1' % profile)
            result = subprocess.run(
                [sys.executable, 'scripts/demo.py', 'verify',
                 '--project', str(output), '--ffmpeg', FFMPEG],
                capture_output=True, cwd=REPO)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['decoded_checks']['status'], 'checked')
            for name, check in report['decoded_checks']['media'].items():
                self.assertEqual(check['mismatched_fields'], [])

    def test_f3_verify_rejects_tampering(self):
        clean = Path(self.tmp) / 'standard-1'
        with tempfile.TemporaryDirectory(prefix='demo177-tamper-') as tmp:
            tampered = Path(tmp)
            # 视频字节篡改
            case = shutil.copytree(clean, tampered / 'video')
            data = bytearray((case / 'a.mp4').read_bytes())
            data[len(data) // 2] ^= 0xFF
            (case / 'a.mp4').write_bytes(data)
            # 字体依赖替换
            case = shutil.copytree(clean, tampered / 'font')
            shutil.copyfile(clean / 'a.mp4',
                            case / 'fonts' / 'NotoSansSC-VF.ttf')
            # 版本篡改
            case = shutil.copytree(clean, tampered / 'version')
            realization = json.loads((case / 'realization.json').read_text())
            realization['profile_version'] = 99
            (case / 'realization.json').write_text(json.dumps(realization))
            # 生成器身份篡改
            case = shutil.copytree(clean, tampered / 'generator')
            realization = json.loads((case / 'realization.json').read_text())
            realization['generator']['sha256'] = '0' * 64
            (case / 'realization.json').write_text(json.dumps(realization))
            # 同 stat 字节替换
            case = shutil.copytree(clean, tampered / 'samestat')
            target = case / 'b.mp4'
            stat = target.stat()
            data = bytearray(target.read_bytes())
            data[-10:-5] = b'[\x00\x00\x00['
            target.write_bytes(data)
            os.utime(target, (stat.st_atime, stat.st_mtime))
            for name in ('video', 'font', 'version', 'generator', 'samestat'):
                result = subprocess.run(
                    [sys.executable, 'scripts/demo.py', 'verify',
                     '--project', str(tampered / name), '--ffmpeg', FFMPEG],
                    capture_output=True, cwd=REPO)
                self.assertNotEqual(result.returncode, 0, name)
            # 未污染对照仍通过
            control = shutil.copytree(clean, tampered / 'control')
            result = subprocess.run(
                [sys.executable, 'scripts/demo.py', 'verify',
                 '--project', str(control), '--ffmpeg', FFMPEG],
                capture_output=True, cwd=REPO)
            self.assertEqual(result.returncode, 0)

    def test_f4_prepare_negative_diagnostics_no_boundary_writes(self):
        with tempfile.TemporaryDirectory(prefix='demo177-neg-') as tmp:
            base = Path(tmp)
            existing = base / 'exists'
            existing.mkdir()
            link = base / 'link'
            link.symlink_to(base, target_is_directory=True)
            before = sorted(str(p) for p in base.rglob('*'))
            cases = [
                ['--output', str(existing)],
                ['--output', str(link / 'new')],
                ['--output', str(base / 'missing' / 'new')],
            ]
            for case in cases:
                result = subprocess.run(
                    [sys.executable, 'scripts/demo.py', 'prepare',
                     '--profile', 'quick', *case, '--ffmpeg', FFMPEG],
                    capture_output=True, cwd=REPO)
                self.assertNotEqual(result.returncode, 0, case)
                self.assertIn('error', result.stderr.decode('utf-8', 'replace'))
            after = sorted(str(p) for p in base.rglob('*'))
            self.assertEqual([p for p in after if 'new' in p], [])


if __name__ == '__main__':
    unittest.main()
