"""Built-in producer/receipt identity and source acceptance regressions."""
import contextlib
import errno
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

from tests.test_bgm_render_plugin import _Base, _bgm, _context
from apps.bgm import identity
from apps.bgm.bgm_gen.ref_slots import UnsafeTrackRef
from vtx.cache.cache_state import receipt_fingerprint, verify_input_identity


class ProducerIdentity(_Base):
    def skip_unsupported_symlink(self, exc):
        if getattr(exc, "winerror", None) in (5, 1314) or exc.errno in (
                errno.EACCES, errno.EPERM, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP):
            self.skipTest(f"symlinks unavailable on this platform: {exc}")
        raise exc

    def state(self, c):
        return (_bgm.param_digest(c, "bgm_main"), _bgm.render_digest(c, "bgm_main"),
                receipt_fingerprint(c))

    def test_runtime_and_ref_change_all_identities_same_context(self):
        c = self._ctx(True)
        before = self.state(c)
        track = json.loads(self.ref_file.read_text(encoding="utf-8"))
        track["notes"].append([64, 1, 1, 90])
        self.ref_file.write_text(json.dumps(track), encoding="utf-8")
        after = self.state(c)
        self.assertTrue(all(a != b for a, b in zip(before, after)))
        with self.assertRaises(Exception):
            verify_input_identity(c)
        runtime = Path(os.environ["MUSIC_RUNTIME"])
        runtime.write_text('{"secret": "never-publish-plaintext"}', encoding="utf-8")
        newer = self.state(c)
        self.assertTrue(all(a != b for a, b in zip(after, newer)))
        self.assertNotIn("never-publish-plaintext", json.dumps(_bgm.input_identity(c)))

    def test_invalid_ref_slots_not_opened_by_full_producer(self):
        sentinel = self.root / "sentinel.json"
        sentinel.write_text('{"engine":"sample"}', encoding="utf-8")
        c = mock.Mock()
        c.src_path.return_value = self.proj
        original_open = Path.open
        opened = []
        def record_open(path, *args, **kwargs):
            opened.append(path)
            return original_open(path, *args, **kwargs)
        for version, tracks in [
            ("1", [{"$ref": "../sentinel.json"}]),
            ("2", [{"$ref": "../sentinel.json", "engine": "sample"},
                   {"nested": {"$ref": "../sentinel.json"}},
                   {"$ref": "  \t"}]),
        ]:
            with self.subTest(version=version):
                self.proj.write_text(json.dumps({"meta": {"spec_version": version},
                                                 "tracks": tracks}), encoding="utf-8")
                opened.clear()
                with mock.patch.object(Path, "open", record_open):
                    producer = _bgm._producer_identity(c, "bgm_main")
                self.assertEqual(producer["assets"], {})
                self.assertNotIn(sentinel.resolve(), [p.resolve() for p in opened])
                self.assertNotIn(self.proj.parent / "  \t", opened)

    def test_ref_static_boundary_and_missing_transition(self):
        outside = self.root / "sentinel.json"
        outside.write_text('{"engine":"sample"}', encoding="utf-8")
        sibling = self.root / "music-other"
        sibling.mkdir()
        (sibling / "track.json").write_text('{}', encoding="utf-8")
        local_dir = self.proj.parent / "folder"
        local_dir.mkdir()
        c = mock.Mock()
        def project(ref):
            self.proj.write_text(json.dumps({"meta": {"spec_version": 2},
                                              "tracks": [{"$ref": ref}]}), encoding="utf-8")
        def check(ref):
            project(ref)
            original_open = Path.open
            def guarded(path, *args, **kwargs):
                if path != self.proj:
                    raise AssertionError(f"unexpected content open: {path}")
                return original_open(path, *args, **kwargs)
            with mock.patch.object(Path, "open", guarded), \
                 mock.patch.object(Path, "rglob", side_effect=AssertionError("directory scan")):
                with self.assertRaises(UnsafeTrackRef):
                    _bgm._dep_sigs(c, {}, self.proj)
                with self.assertRaises(UnsafeTrackRef):
                    identity.asset_dependencies(self.proj, {})
        bad = ["../sentinel.json", str(outside), "../music-other/track.json", "folder"]
        if os.name == "nt":
            bad += [r"\rooted.json", r"C:relative.json", r"C:\absolute.json",
                    r"\\server\share\track.json", r"\\?\C:\track.json"]
        for ref in bad:
            with self.subTest(ref=ref):
                check(ref)
        from apps.bgm.bgm_gen.core.project import _read_ref_track
        project("strings_part.json")
        context = self._ctx(True)
        for ref in ("folder/../strings_part.json", "strings_part.json/../strings_part.json"):
            project(ref)
            track, error = _read_ref_track(str(self.proj.parent), ref)
            self.assertIsNone(error)
            self.assertEqual(track["name"], "strings")
            self.assertEqual(identity.asset_dependencies(self.proj, {}), [])
            self.assertNotEqual(next(iter(_bgm._dep_sigs(c, {}, self.proj).values())), "MISSING")
            original = self.ref_file.read_text(encoding="utf-8")
            before = (_bgm.param_digest(context, "bgm_main"), _bgm.render_digest(context, "bgm_main"))
            self.ref_file.write_text('{"name":"changed"}', encoding="utf-8")
            after = (_bgm.param_digest(context, "bgm_main"), _bgm.render_digest(context, "bgm_main"))
            self.assertTrue(all(a != b for a, b in zip(before, after)))
            self.ref_file.write_text(original, encoding="utf-8")
        context = self._ctx(True)
        project("missing.json")
        digest_before = _bgm.render_digest(context, "bgm_main")
        missing = _bgm._dep_sigs(c, {}, self.proj)
        self.assertEqual(missing, _bgm._dep_sigs(c, {}, self.proj))
        self.assertEqual(list(missing.values()), ["MISSING"])
        self.assertEqual(identity.asset_dependencies(self.proj, {}), [])
        (self.proj.parent / "missing.json").write_text('{}', encoding="utf-8")
        self.assertNotEqual(missing, _bgm._dep_sigs(c, {}, self.proj))
        self.assertNotEqual(digest_before, _bgm.render_digest(context, "bgm_main"))
        with mock.patch("apps.bgm.bgm_gen.ref_slots.os.stat", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                _bgm._dep_sigs(c, {}, self.proj)
            with self.assertRaises(PermissionError):
                identity.asset_dependencies(self.proj, {})

    def test_ref_nonmissing_io_errors_are_not_missing(self):
        original_resolve, original_stat, original_open = Path.resolve, os.stat, Path.open
        target = str(self.ref_file.resolve())
        for code in (errno.EACCES, errno.EIO):
            def fail_resolve(path, *args, **kwargs):
                if str(path) == target:
                    raise OSError(code, "injected resolve error")
                return original_resolve(path, *args, **kwargs)
            def fail_stat(path, *args, **kwargs):
                if str(path) == target:
                    raise OSError(code, "injected stat error")
                return original_stat(path, *args, **kwargs)
            def fail_open(path, *args, **kwargs):
                if str(path) == target:
                    raise OSError(code, "injected open error")
                return original_open(path, *args, **kwargs)
            with self.subTest(errno=code, method="resolve"), mock.patch.object(Path, "resolve", fail_resolve):
                for entry in (lambda: _bgm._dep_sigs(None, {}, self.proj),
                              lambda: identity.asset_dependencies(self.proj, {})):
                    with self.assertRaises(OSError) as exc:
                        entry()
                    self.assertEqual(exc.exception.errno, code)
            def resolved_target(path, *args, **kwargs):
                if str(path) == target:
                    self.assertTrue(kwargs.get("strict"))
                    return Path(target)
                return original_resolve(path, *args, **kwargs)
            with self.subTest(errno=code, method="stat"), \
                 mock.patch.object(Path, "resolve", resolved_target), \
                 mock.patch("apps.bgm.bgm_gen.ref_slots.os.stat", side_effect=fail_stat) as stat_call:
                for entry in (lambda: _bgm._dep_sigs(None, {}, self.proj),
                              lambda: identity.asset_dependencies(self.proj, {})):
                    stat_call.reset_mock()
                    with self.assertRaises(OSError) as exc:
                        entry()
                    self.assertEqual(exc.exception.errno, code)
                    self.assertEqual([call for call in stat_call.call_args_list
                                      if call.args and str(call.args[0]) == target], [mock.call(target)])
            with self.subTest(errno=code, method="open"), mock.patch.object(Path, "open", fail_open):
                with self.assertRaises(OSError) as exc:
                    _bgm._dep_sigs(None, {}, self.proj)
                self.assertEqual(exc.exception.errno, code)
                # Asset parsing retains its previous optional-read behavior.
                self.assertEqual(identity.asset_dependencies(self.proj, {}), [])

    def test_fifo_ref_rejected_without_open(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("POSIX FIFO required")
        fifo = self.proj.parent / "fifo"
        os.mkfifo(fifo)
        self.proj.write_text(json.dumps({"meta": {"spec_version": 2},
                                        "tracks": [{"$ref": "fifo"}]}), encoding="utf-8")
        original_open = Path.open
        def guarded(path, *args, **kwargs):
            if path != self.proj:
                raise AssertionError(f"unexpected content open: {path}")
            return original_open(path, *args, **kwargs)
        with mock.patch.object(Path, "open", guarded):
            with self.assertRaises(UnsafeTrackRef):
                _bgm._dep_sigs(None, {}, self.proj)
            with self.assertRaises(UnsafeTrackRef):
                identity.asset_dependencies(self.proj, {})

    def test_windows_junction_outside_is_rejected_before_open(self):
        if os.name != "nt":
            self.skipTest("Windows junction test")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "track.json").write_text('{}', encoding="utf-8")
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", "link", "..\\outside"],
                                cwd=self.proj.parent, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.proj.write_text(json.dumps({"meta": {"spec_version": 2},
                                        "tracks": [{"$ref": "link/track.json"}]}), encoding="utf-8")
        original_open = Path.open
        def guarded(path, *args, **kwargs):
            if path != self.proj:
                raise AssertionError(f"unexpected content open: {path}")
            return original_open(path, *args, **kwargs)
        with mock.patch.object(Path, "open", guarded), \
             mock.patch.object(Path, "rglob", side_effect=AssertionError("directory scan")):
            with self.assertRaises(UnsafeTrackRef):
                _bgm._dep_sigs(None, {}, self.proj)
            with self.assertRaises(UnsafeTrackRef):
                identity.asset_dependencies(self.proj, {})
        from apps.bgm.bgm_gen.core.project import _read_ref_track
        self.proj.write_text(json.dumps({"meta": {"spec_version": 2},
                                        "tracks": [{"$ref": "strings_part.json"}]}), encoding="utf-8")
        context = self._ctx(True)
        ref = "link/../strings_part.json"
        self.proj.write_text(json.dumps({"meta": {"spec_version": 2},
                                        "tracks": [{"$ref": ref}]}), encoding="utf-8")
        track, error = _read_ref_track(str(self.proj.parent), ref)
        self.assertIsNone(error)
        self.assertEqual(track["name"], "strings")
        self.assertEqual(identity.asset_dependencies(self.proj, {}), [])
        before = (_bgm.param_digest(context, "bgm_main"), _bgm.render_digest(context, "bgm_main"))
        self.ref_file.write_text('{"name":"changed"}', encoding="utf-8")
        after = (_bgm.param_digest(context, "bgm_main"), _bgm.render_digest(context, "bgm_main"))
        self.assertTrue(all(a != b for a, b in zip(before, after)))

    def test_ref_symlink_boundaries_and_project_lexical_parent(self):
        local = self.proj.parent
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "track.json").write_text('{"engine":"sample"}', encoding="utf-8")
        try:
            (local / "out-link").symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skip_unsupported_symlink(exc)
        (local / "in-link.json").symlink_to(self.ref_file)
        (local / "project-link.json").symlink_to(outside / "project.json")
        (outside / "project.json").write_text(json.dumps({"meta": {"spec_version": 2},
                                                           "tracks": [{"$ref": "in-link.json"}]}), encoding="utf-8")
        project_link = local / "project-link.json"
        self.assertEqual(identity.asset_dependencies(project_link, {}), [])
        self.assertNotEqual(list(_bgm._dep_sigs(None, {}, project_link).values()), ["MISSING"])
        for ref in ("out-link/track.json",):
            with self.subTest(ref=ref):
                self.proj.write_text(json.dumps({"meta": {"spec_version": 2},
                                                 "tracks": [{"$ref": ref}]}), encoding="utf-8")
                with mock.patch.object(Path, "rglob", side_effect=AssertionError("directory scan")):
                    with self.assertRaises(UnsafeTrackRef):
                        _bgm._dep_sigs(None, {}, self.proj)
                    with self.assertRaises(UnsafeTrackRef):
                        identity.asset_dependencies(self.proj, {})

    def test_symlink_dotdot_matches_loader_and_both_digests(self):
        from apps.bgm.bgm_gen.core.project import _read_ref_track
        local = self.proj.parent
        deep = local / "deep" / "nested"
        deep.mkdir(parents=True)
        link = local / "link"
        try:
            link.symlink_to(deep, target_is_directory=True)
        except OSError as exc:
            self.skip_unsupported_symlink(exc)
        loaded = local / "track.json"
        other = local / "deep" / "track.json"
        loaded.write_text('{"name":"loader"}', encoding="utf-8")
        other.write_text('{"name":"other"}', encoding="utf-8")
        context = self._ctx(True)
        self.proj.write_text(json.dumps({"meta": {"spec_version": 2},
                                        "tracks": [{"$ref": "link/../track.json"}]}), encoding="utf-8")
        track, error = _read_ref_track(str(local), "link/../track.json")
        self.assertIsNone(error)
        self.assertEqual(track["name"], "loader")
        before = (_bgm.param_digest(context, "bgm_main"), _bgm.render_digest(context, "bgm_main"))
        loaded.write_text('{"name":"changed"}', encoding="utf-8")
        after = (_bgm.param_digest(context, "bgm_main"), _bgm.render_digest(context, "bgm_main"))
        self.assertTrue(all(a != b for a, b in zip(before, after)))

    def test_inline_and_referenced_assets_remain_bound(self):
        samples = self.root / "samples"
        sfz = self.root / "assets" / "sfz"
        sf2 = self.root / "bank.sf2"
        samples.mkdir()
        sfz.mkdir(parents=True)
        sf2.write_bytes(b"bank")
        self.ref_file.write_text(json.dumps({"engine": "sf2", "instrument": "bank:1:2"}), encoding="utf-8")
        self.proj.write_text(json.dumps({"meta": {"spec_version": 2}, "tracks": [
            {"$ref": self.ref_file.name}, {"engine": "sample"}, {"engine": "sfz"}]}), encoding="utf-8")
        runtime = {"samples_root": str(samples), "assets_root": str(sfz.parent),
                   "sf2": {"bank": str(sf2)}}
        self.assertEqual(set(identity.asset_dependencies(self.proj, runtime)),
                         {str(samples), str(sfz), str(sf2)})
        self.ref_file.write_text("bad JSON", encoding="utf-8")
        self.assertEqual(set(identity.asset_dependencies(self.proj, runtime)), {str(samples), str(sfz)})
        self.ref_file.unlink()
        self.assertEqual(set(identity.asset_dependencies(self.proj, runtime)), {str(samples), str(sfz)})

    def test_absolute_sf2_shorthand_projects_and_binds_the_same_file(self):
        from apps.bgm.bgm_gen.core.project import build_render_plan, load_project, track_engine_spec

        sf2 = self.root / "音色 bank.sf2"
        source = identity.APP_ROOT / "examples" / "synth.json"
        for path in dict.fromkeys((str(sf2), sf2.as_posix())):
            for suffix, overrides, expected in (
                (":128:127", {}, (128, 127)),
                (":1:2", {"bank": 3, "program": 4}, (3, 4)),
                ("", {}, (0, 0)),
            ):
                with self.subTest(path=path, suffix=suffix, overrides=overrides):
                    sf2.write_bytes(b"original asset bytes")
                    project = json.loads(source.read_text(encoding="utf-8"))
                    project["tracks"][0].update(engine="sf2", instrument=path + suffix, **overrides)
                    self.proj.write_text(json.dumps(project), encoding="utf-8")
                    loaded = load_project(self.proj)
                    track = build_render_plan(loaded)["tracks"][0]
                    payload = track_engine_spec(track, loaded, "unused.wav")["payload"]
                    dependencies = identity.asset_dependencies(self.proj, {})
                    self.assertEqual((payload["sf2"], payload["bank"], payload["program"]),
                                     (path, *expected))
                    self.assertEqual(dependencies, [path])
                    c = self._ctx(True)
                    before = self.state(c)
                    sf2.write_bytes(b"replaced asset bytes")
                    after = self.state(c)
                    self.assertTrue(all(a != b for a, b in zip(before, after)))

    def test_master_reference_changes_all_identities_same_context(self):
        reference = self.root / "reference.wav"
        reference.write_bytes(b"first reference audio")
        project = json.loads((identity.APP_ROOT / "examples" / "synth.json").read_text())
        # Matchering resolves relative references against caller cwd, not the
        # project directory. Do not introduce a different project-path contract.
        project["master"] = {"chain": "matchering", "ref": os.path.relpath(reference)}
        self.proj.write_text(json.dumps(project), encoding="utf-8")
        c = self._ctx(True)
        before = self.state(c)
        self.assertEqual(identity.asset_dependencies(self.proj, {}), [str(reference)])
        reference.write_bytes(b"different reference audio content")
        after = self.state(c)
        self.assertTrue(all(a != b for a, b in zip(before, after)))
        with self.assertRaises(Exception):
            verify_input_identity(c)

    def test_child_python_and_numpy_change_all_identities_same_context(self):
        venv = self.root / "child-env"
        bindir = venv / ("Scripts" if os.name == "nt" else "bin")
        bindir.mkdir(parents=True)
        child = bindir / ("python.exe" if os.name == "nt" else "python")
        implementation = self.root / "child-implementation"
        implementation.write_bytes(b"original interpreter")
        implementation.chmod(0o755)
        if os.name == "nt":
            child.write_bytes(implementation.read_bytes())
            implementation = child
        else:
            child.symlink_to(implementation)
        dist = venv / "lib" / "python3.13" / "site-packages" / "numpy-1.dist-info"
        dist.mkdir(parents=True)
        record = dist / "RECORD"
        record.write_text("first numpy installation", encoding="utf-8")
        configs = [({"python_system": str(child)}, "qc"),
                   ({"matchering": {"python": str(child)}}, "matchering"),
                   ({"demucs": {"python": str(child)}}, "demucs"),
                   ({"python_torch": str(child)}, "matchering")]
        for config, name in configs:
            with self.subTest(config=config):
                Path(os.environ["MUSIC_RUNTIME"]).write_text(json.dumps(config), encoding="utf-8")
                implementation.write_bytes(b"original interpreter")
                c = self._ctx(True)
                before = self.state(c)
                producer = _bgm._producer_identity(c, "bgm_main")
                selected = producer["child_pythons"][name]
                self.assertEqual(selected["tool"]["selected"], str(child))
                self.assertEqual(selected["tool"]["path"], str(implementation.resolve()))
                self.assertTrue(selected["numpy_distribution"])
                implementation.write_bytes(b"replaced interpreter, different size")
                after = self.state(c)
                self.assertTrue(all(a != b for a, b in zip(before, after)))
                self.assertEqual(producer["tools"], _bgm._producer_identity(c, "bgm_main")["tools"])
                with self.assertRaises(Exception):
                    verify_input_identity(c)
                # Installed NumPy records must also be rebound in this same Ctx.
                record.write_text("updated numpy " + name, encoding="utf-8")
                newer = self.state(c)
                self.assertTrue(all(a != b for a, b in zip(after, newer)))

    def test_app_code_and_descriptor_change_all_identities(self):
        c = self._ctx(True)
        with mock.patch.object(identity, "code_identity", return_value="implementation-a"):
            before = self.state(c)
        with mock.patch.object(identity, "code_identity", return_value="implementation-b"):
            after = self.state(c)
        self.assertTrue(all(a != b for a, b in zip(before, after)))
        root = self.root / "app"
        (root / "bgm_gen" / "descriptors").mkdir(parents=True)
        (root / "workflows").mkdir()
        (root / "bgm.py").write_text("# entry", encoding="utf-8")
        descriptor = root / "bgm_gen" / "descriptors" / "synth.json"
        descriptor.write_text('{"version": 1}', encoding="utf-8")
        before = identity.code_identity(root)
        descriptor.write_text('{"version": 2}', encoding="utf-8")
        self.assertNotEqual(identity.code_identity(root), before)
        helper = root / "bgm_gen" / "ref_slots.py"
        before = identity.code_identity(root)
        helper.write_text("# new filter\n", encoding="utf-8")
        self.assertNotEqual(identity.code_identity(root), before)

    def test_helper_package_layouts_and_plugin_missing_app(self):
        repo = identity.APP_ROOT.parent.parent
        code = '''import builtins, importlib, sys
original = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == "apps.bgm" or name.startswith("apps.bgm."):
        raise ImportError("app unavailable")
    return original(name, *args, **kwargs)
builtins.__import__ = blocked
plugin = importlib.import_module("plugins.bgm-render")
try:
    plugin._track_refs({})
except ImportError:
    pass
else:
    raise AssertionError("missing app must fail on use")
builtins.__import__ = original
sys.path.insert(0, sys.argv[1])
a = importlib.import_module("bgm_gen.ref_slots")
b = importlib.import_module("apps.bgm.bgm_gen.ref_slots")
project = {"meta": {"spec_version": 2}, "tracks": [{"$ref": " x.json"}]}
assert a.track_ref_slots(project) == b.track_ref_slots(project) == [(0, " x.json")]
assert "numpy" not in sys.modules
'''
        subprocess.run([sys.executable, "-c", code, str(identity.APP_ROOT)],
                       cwd=str(repo), check=True)

    def test_other_project_only_invalidates_own_render_and_receipt(self):
        second = self.root / "music" / "other.json"
        second.write_text('{"tracks": []}', encoding="utf-8")
        c = _context(self.root, self._tl_srcs(
            {"id": "bgm_main", "path": "music/ep.json", "bgm": True},
            {"id": "other", "path": "music/other.json", "bgm": True}))
        before = self.state(c)
        other = _bgm.render_digest(c, "other")
        second.write_text('{"tracks": [], "name": "changed"}', encoding="utf-8")
        after = self.state(c)
        self.assertEqual(before[:2], after[:2])
        self.assertNotEqual(before[2], after[2])
        self.assertNotEqual(other, _bgm.render_digest(c, "other"))

    def test_actual_ffmpeg_not_overwritten_by_runtime_tool(self):
        env = dict(os.environ)
        Path(env["MUSIC_RUNTIME"]).write_text(
            json.dumps({"tools": {"ffmpeg": "/configured/ffmpeg"}}), encoding="utf-8")
        def tool(command, env, **kwargs):
            return {"command": command, "stamp": stamps.get(command, 1)}
        stamps = {"ffmpeg": 1}
        with mock.patch.object(identity, "_tool_identity", side_effect=tool):
            before, _ = identity.execution_identity([sys.executable], env)
            stamps["ffmpeg"] = 2
            after, _ = identity.execution_identity([sys.executable], env)
        self.assertNotEqual(before, after)
        self.assertEqual(before["configured_tools"], after["configured_tools"])

    @mock.patch.dict(os.environ, {"PATHEXT": ".CMD;.EXE"})
    def test_windows_configured_fluidsynth_dll_rekeys_render_and_receipt(self):
        if os.name != 'nt':
            self.skipTest('native Windows DLL stat versus executable search')
        library = self.root / '音源 FluidSynth.dll'
        runtime = Path(os.environ['MUSIC_RUNTIME'])
        runtime.write_text(json.dumps({'tools': {'fluidsynth_lib': str(library)}}), encoding='utf-8')
        env = dict(os.environ)
        def library_identity():
            data, _ = identity.execution_identity([sys.executable], env)
            return data['configured_tools']['fluidsynth_lib']

        self.assertEqual(library_identity(), {'command': str(library), 'state': 'missing'})
        # The identity collector never loads or executes this synthetic file.
        library.write_bytes(b'first DLL placeholder')
        self.assertIsNone(shutil.which(str(library)))
        self.assertEqual(Path(library_identity()['selected']), library.absolute())
        c = self._ctx(True)
        before = self.state(c)
        replacement = self.root / 'next.dll'
        replacement.write_bytes(b'second DLL placeholder with a different size')
        replacement.replace(library)
        self.assertEqual(library_identity()['size'], library.stat().st_size)
        self.assertTrue(all(old != new for old, new in zip(before, self.state(c))))
        self.assertEqual(self._calls(), [])
        self.assertFalse((self.root / 'cache').exists())

    @mock.patch.dict(os.environ, {"PATHEXT": ".CMD;.EXE"})
    def test_windows_bare_media_identity_ignores_wrappers_and_rekeys_on_exe_replacement(self):
        if os.name != "nt":
            self.skipTest("native Windows CreateProcess lookup")
        wrappers, native = self.root / "wrappers", self.root / "native"
        wrappers.mkdir()
        native.mkdir()
        system = Path(os.environ["SystemRoot"]) / "System32"
        for command in ("ffmpeg", "ffprobe"):
            for name in (command + ".cmd", command + ".exe.cmd"):
                (wrappers / name).write_text("@exit /b 17\n", encoding="ascii")
            shutil.copyfile(system / "cmd.exe", native / (command + ".exe"))
        path = os.pathsep.join((str(wrappers), str(native), os.environ["PATH"]))
        with mock.patch.dict(os.environ, {"PATH": path}):
            for command in ("ffmpeg", "ffprobe"):
                self.assertEqual(Path(shutil.which(command)), wrappers / (command + ".cmd"))
                result = subprocess.run([command, "/d", "/c", "echo native"],
                                        capture_output=True, check=True)
                self.assertEqual(result.stdout.strip(), b"native")
            c = self._ctx(True)
            for command in ("ffmpeg", "ffprobe"):
                executable = native / (command + ".exe")
                execution, _ = identity.execution_identity([sys.executable], dict(os.environ))
                self.assertEqual(Path(execution["tools"][command]["selected"]), executable)
                before = self.state(c)
                replacement = native / "next.exe"
                shutil.copyfile(system / "where.exe", replacement)
                replacement.replace(executable)
                after = self.state(c)
                self.assertTrue(all(old != new for old, new in zip(before, after)), command)

            # Managed launches suppress the implicit current directory. That
            # must affect identity too, even when a local .exe actually exists.
            caller = Path.cwd()
            local = self.root / 'caller'
            local.mkdir()
            shutil.copyfile(system / 'cmd.exe', local / 'ffmpeg.exe')
            try:
                os.chdir(local)
                with mock.patch.dict(os.environ, {'NoDefaultCurrentDirectoryInExePath': '1'}):
                    execution, _ = identity.execution_identity([sys.executable], dict(os.environ))
                self.assertEqual(Path(execution['tools']['ffmpeg']['selected']), native / 'ffmpeg.exe')
            finally:
                os.chdir(caller)

    def test_venvs_sharing_python_have_distinct_numpy_identity(self):
        if os.name == "nt":
            self.skipTest("POSIX venv executable symlink layout")
        commands = []
        for name, record in (("venv-a", "numpy-a"), ("venv-b", "numpy-b")):
            root = self.root / name
            (root / "bin").mkdir(parents=True)
            py = root / "bin" / "python"
            py.symlink_to(sys.executable)
            dist = root / "lib" / "python3.13" / "site-packages" / "numpy-1.dist-info"
            dist.mkdir(parents=True)
            (dist / "RECORD").write_text(record, encoding="utf-8")
            commands.append([str(py)])
        first, _ = identity.execution_identity(commands[0], dict(os.environ))
        second, _ = identity.execution_identity(commands[1], dict(os.environ))
        self.assertEqual(first["tools"]["python"]["path"], second["tools"]["python"]["path"])
        self.assertNotEqual(first["tools"]["python"]["selected"], second["tools"]["python"]["selected"])
        self.assertTrue(first["numpy_distribution"])
        self.assertNotEqual(first["numpy_distribution"], second["numpy_distribution"])

    def test_runtime_error_does_not_render_or_publish(self):
        Path(os.environ["MUSIC_RUNTIME"]).write_text("not JSON", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "bgm-render.*input_identity"):
            self._ctx(True)
        self.assertEqual(self._calls(), [])
        self.assertFalse((self.root / "cache").exists())

    def test_source_without_bgm_does_not_import_music_identity(self):
        with mock.patch.object(identity, "execution_identity", side_effect=AssertionError("music loaded")):
            self._ctx()

    def test_mutation_during_render_does_not_publish_complete(self):
        c = self._ctx(True)
        shared = _bgm.render_cache_dir(c, "bgm_main")
        original = _bgm._run_bgm
        def change_after_render(*args):
            result = original(*args)
            track = json.loads(self.ref_file.read_text(encoding="utf-8"))
            track["notes"].append([64, 1, 1, 90])
            self.ref_file.write_text(json.dumps(track), encoding="utf-8")
            return result
        with mock.patch.object(_bgm, "_run_bgm", side_effect=change_after_render):
            with self.assertRaisesRegex(RuntimeError, "输入在渲染期间变化"):
                _bgm._ensure_shared_render(c, "bgm_main")
        self.assertFalse((shared / "_complete.json").exists())

    def test_master_qc_failure_is_rejected_but_stem_remains_mixing_input(self):
        c = self._ctx(True)
        shared, result = _bgm._ensure_shared_render(c, "bgm_main")
        result["qc_ok"] = False
        with mock.patch.object(_bgm, "_ensure_shared_render", return_value=(shared, result)), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _bgm.prepare_source(c, "bgm_main")
            stem = self._ctx({"output": "stem:strings"})
            self.assertTrue(_bgm.prepare_source(stem, "bgm_main").is_file())
