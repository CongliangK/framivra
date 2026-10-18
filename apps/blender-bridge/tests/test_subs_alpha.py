"""tests/test_subs_alpha.py — subs_alpha.py 契约测试（fail-hard 指引 + 成功产物）。

背景（plugins/blender-bridge 缺陷1修复的前置件）：blender-bridge 插件的 finish
钩子在 cache/subs_alpha.mov 缺失时自动调本脚本生成。此处锁两件契约：
  ① 缺 cache/subs.ass → exit 非零 + 中文指引（不吞错误）；
  ② 成功路径产出 ProRes 4444 透明轨（需 ffmpeg，skipUnless 守护）。
运行：cd apps/blender-bridge && env python3 -m unittest discover -s tests -t .
"""
import json
import subprocess
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
SCRIPT = APP / "subs_alpha.py"


def _write_timeline(root: Path, subs: bool = True):
    tl = {"version": 2,
          "project": {"fps": 30, "width": 320, "height": 180, "duration_frames": 30},
          "sources": [], "video_tracks": [],
          "subtitles": [{"in": 0, "out": 30, "text": "测试"}] if subs else [],
          "audio_tracks": []}
    p = root / "timeline.json"
    p.write_text(__import__("json").dumps(tl, ensure_ascii=False), encoding="utf-8")
    return p


class MissingAssFailsHard(unittest.TestCase):
    def test_missing_ass_exits_nonzero_with_guidance(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write_timeline(root)
            r = subprocess.run([sys.executable, str(SCRIPT), str(root / "timeline.json")],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertNotEqual(r.returncode, 0, "缺 subs.ass 必须 exit 非零（不吞）")
            combined = r.stderr + r.stdout
            self.assertIn("subs.ass", combined, "错误须点名缺失文件")
            self.assertIn("字幕", combined, "错误须给出中文指引")


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
class HappyPath(unittest.TestCase):
    def _write_ass(self, root):
        (root / "cache").mkdir()
        (root / "cache" / "subs.ass").write_text(
                "[Script Info]\nScriptType: v4.00+\n"
                "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
                "SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
                "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
                "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                "Style: Default,Arial,18,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
                "0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1\n"
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
                "Effect, Text\n"
                "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,测试字幕\n",
                encoding="utf-8")

    def test_generates_prores_alpha_track(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "工程 space [comma,semi;]"
            root.mkdir()
            _write_timeline(root)
            self._write_ass(root)
            r = subprocess.run([sys.executable, str(SCRIPT), str(root / "timeline.json")],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            mov = root / "cache" / "subs_alpha.mov"
            self.assertTrue(mov.is_file(), "产物须落盘 cache/subs_alpha.mov")
            pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                 "-show_entries", "stream=codec_name,pix_fmt",
                                 "-of", "csv=p=0", str(mov)],
                                capture_output=True, text=True)
            self.assertIn("prores", pr.stdout, r"须为 ProRes（4444 带 alpha）")
            # alpha 平面断言取 yuva444p 前缀：pix_fmt 因 ffmpeg 构建报告
            # yuva444p10le/yuva444p12le（ProRes 4444 解码侧位深），关键是有 a（alpha）
            self.assertIn("yuva444p", pr.stdout, "须带 alpha 平面（yuva444p*）")

    def test_symlink_timeline_reads_and_writes_link_project_cache(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "A", Path(d) / "B"
            a.mkdir()
            b.mkdir()
            target = _write_timeline(a)
            timeline = b / "timeline.json"
            timeline.symlink_to(target)
            # A 没有 ASS：若 resolve 穿透，脚本会报缺失；B 有 live ASS。
            self._write_ass(b)
            r = subprocess.run([sys.executable, str(SCRIPT), str(timeline)],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((b / "cache" / "subs_alpha.mov").is_file())
            self.assertFalse((a / "cache").exists())
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                    "stream=codec_type:format=duration", "-of", "json",
                                    str(b / "cache" / "subs_alpha.mov")],
                                   capture_output=True, text=True)
            self.assertEqual(probe.returncode, 0, probe.stderr)
            info = json.loads(probe.stdout)
            self.assertEqual(info["streams"][0]["codec_type"], "video")
            self.assertGreater(float(info["format"]["duration"]), 0)
            if shutil.which("blender"):
                # json2blend 也必须从 B 导入该轨，而不是跳到 A/cache。
                blend = b / "preview.blend"
                export = subprocess.run([
                    "blender", "--background", "--factory-startup", "--python-exit-code", "1",
                    "--python", str(APP / "json2blend.py"), "--", str(timeline), str(blend)],
                    capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(export.returncode, 0, export.stderr + export.stdout)
                self.assertIn("字幕轨 有", export.stdout)
                self.assertTrue(blend.is_file())


if __name__ == "__main__":
    unittest.main()
