#!/usr/bin/env python3
"""Build and verify kernel wheel + sdist in fresh venvs, outside the checkout.

Test tooling only: a private builder venv installs build/setuptools/wheel.
Use --wheelhouse for an offline dependency source. Artifacts, venvs, command logs
and report.json stay in a uniquely-created temporary workspace printed at exit;
this command never writes build/ or egg-info into the source checkout. Apps,
plugin execution code and workflow skills are intentionally not wheel contents.
"""
import argparse
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[2]
RESOURCES = {
    "timeline.schema.json": "vtx/timeline.schema.json",
    "preview.html": "vtx/preview.html",
    "pluginlib/builtin_registry.json": "plugins/registry.json",
}


def check_installed(expected_version, expected_hashes, forbidden_roots):
    """Execute inside the installed venv; any missing/version/path drift fails."""
    import hashlib
    import importlib.metadata
    import importlib.resources
    import json
    from pathlib import Path
    import sys
    import vt
    import vtx
    from vtx.pluginlib import registry

    prefix = Path(sys.prefix).resolve()
    paths = {"vt": Path(vt.__file__).resolve(), "vtx": Path(vtx.__file__).resolve()}
    for name, path in paths.items():
        if not path.is_relative_to(prefix):
            raise RuntimeError(f"{name} was imported outside the fresh venv: {path}")
    for forbidden in forbidden_roots:
        root = Path(forbidden).resolve()
        if any(Path(p).resolve().is_relative_to(root) for p in sys.path if p):
            raise RuntimeError(f"source checkout leaked into sys.path: {root}")
    if vtx._DIST_NAME != "framivra":
        raise RuntimeError("distribution name differs from framivra")
    versions = {"metadata": importlib.metadata.version("framivra"),
                "runtime": vtx.__version__, "vt": vt._read_version()}
    if any(value != expected_version for value in versions.values()):
        raise RuntimeError(f"installed version mismatch: expected {expected_version}, got {versions}")
    hashes = {}
    for name, expected_hash in expected_hashes.items():
        data = importlib.resources.files("vtx").joinpath(name).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_hash:
            raise RuntimeError(f"installed resource mismatch: {name}")
        hashes[name] = digest
    schema = json.loads(importlib.resources.files("vtx").joinpath("timeline.schema.json").read_text())
    if schema.get("type") != "object" or "project" not in schema.get("properties", {}):
        raise RuntimeError("installed timeline schema is not the kernel contract")
    preview = importlib.resources.files("vtx").joinpath("preview.html").read_text()
    if "<!doctype html>" not in preview.lower() and "<!DOCTYPE html>" not in preview:
        raise RuntimeError("installed preview is not an HTML document")
    declarations = registry.load_static_registry()["plugins"]
    if not declarations:
        raise RuntimeError("installed official plugin declarations are missing")
    states = {name: registry.plugin_state(name) for name in declarations}
    if any(state != "not_installed" for state in states.values()):
        raise RuntimeError(f"kernel-only wheel unexpectedly activated plugins: {states}")
    return {"prefix": str(prefix), "imports": {k: str(p) for k, p in paths.items()},
            "versions": versions, "resources": hashes, "plugin_states": states}


def clean_env(home, executable_dir=None):
    env = dict(os.environ)
    # Do not inherit a checkout/Python/conda path or user's enabled plugins.
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONPYCACHEPREFIX", "VIRTUAL_ENV",
                "VTX_CONFIG", "IMGEN_CONFIG"):
        env.pop(key, None)
    env.update(HOME=str(home), USERPROFILE=str(home), PYTHONNOUSERSITE="1",
               PYTHONDONTWRITEBYTECODE="1", VTX_DOCTOR_OFFLINE="1",
               VTX_CONFIG=str(home / "empty-config.json"))
    if executable_dir:
        env["PATH"] = str(executable_dir) + os.pathsep + env.get("PATH", "")
    return env


def venv_paths(path):
    directory = path / ("Scripts" if os.name == "nt" else "bin")
    return directory / ("python.exe" if os.name == "nt" else "python"), directory


def stage_source(source, target):
    """Copy only authored kernel build inputs; never build in the checkout."""
    target.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE", "VERSION", "vt.py"):
        shutil.copy2(source / name, target / name)
    for name in ("setup.py", "MANIFEST.in"):
        if (source / name).is_file():
            shutil.copy2(source / name, target / name)
    shutil.copytree(source / "vtx", target / "vtx",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (target / "plugins").mkdir()
    shutil.copy2(source / "plugins/registry.json", target / "plugins/registry.json")


def unpack_sdist(archive, destination):
    # Even a locally generated archive must not escape the isolated workspace.
    with tarfile.open(archive, "r:gz") as stream:
        members = stream.getmembers()
        for member in members:
            parts = Path(member.name).parts
            target = (destination / member.name).resolve()
            if (not parts or Path(member.name).is_absolute() or ".." in parts
                    or not target.is_relative_to(destination.resolve())
                    or not (member.isfile() or member.isdir())):
                raise RuntimeError(f"unsafe sdist member: {member.name}")
        # Members are explicitly validated above, including links/devices.
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with stream.extractfile(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    roots = list(destination.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise RuntimeError("sdist must have exactly one source directory")
    return roots[0]


def smoke(source=ROOT, wheelhouse=None):
    source = Path(source).resolve()
    text = (source / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^\"]+)"\s*$', text)
    if not match:
        raise RuntimeError("pyproject project.version is missing")
    expected = (source / "VERSION").read_text(encoding="utf-8").strip()
    if expected != match.group(1):
        raise RuntimeError("VERSION and pyproject disagree before packaging")
    hashes = {name: hashlib.sha256((source / path).read_bytes()).hexdigest()
              for name, path in RESOURCES.items()}
    workspace = Path(tempfile.mkdtemp(prefix="framivra-package-smoke-")).resolve()
    report = {"source": str(source), "workspace": str(workspace), "version": expected,
              "scope": "kernel-only; optional apps/plugin executables/skills excluded",
              "commands": [], "installations": [], "ok": False}
    home = workspace / "home"
    home.mkdir()
    (home / "empty-config.json").write_text("{}", encoding="utf-8")
    outside = workspace / "outside-checkout"
    outside.mkdir()
    env = clean_env(home)

    def run(args, cwd=outside, environment=env, expected_exit=0):
        entry = {"argv": [str(x) for x in args], "cwd": str(cwd)}
        number = len(report["commands"])
        log = workspace / f"command-{number:02d}.log"
        entry["log"] = str(log)
        report["commands"].append(entry)
        with log.open("w", encoding="utf-8") as output:
            proc = subprocess.run(entry["argv"], cwd=cwd, env=environment,
                                  stdout=output, stderr=subprocess.STDOUT, timeout=300)
        entry["exit_code"] = proc.returncode
        if proc.returncode != expected_exit:
            raise RuntimeError(f"command failed ({proc.returncode}, expected {expected_exit}), see {log}: {entry['argv']}")
        return log.read_text(encoding="utf-8")

    try:
        staged = workspace / "staged-source"
        stage_source(source, staged)
        builder = workspace / "builder"
        venv.EnvBuilder(with_pip=True).create(builder)
        python, binary_dir = venv_paths(builder)
        build_env = clean_env(home, binary_dir)
        install_tools = [python, "-I", "-m", "pip", "install"]
        if wheelhouse:
            install_tools += ["--no-index", "--find-links", str(Path(wheelhouse).resolve())]
        run(install_tools + ["build>=1.2,<2", "setuptools>=61", "wheel>=0.38"], environment=build_env)
        dist = workspace / "dist"
        run([python, "-I", "-m", "build", "--no-isolation", "--wheel", "--sdist",
             "--outdir", dist, staged], environment=build_env)
        wheels, sdists = list(dist.glob("*.whl")), list(dist.glob("*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            raise RuntimeError("build did not produce exactly one wheel and one sdist")
        unpacked = workspace / "unpacked-sdist"
        unpacked.mkdir()
        sdist_source = unpack_sdist(sdists[0], unpacked)
        rebuilt = workspace / "sdist-wheel"
        run([python, "-I", "-m", "build", "--no-isolation", "--wheel", "--outdir", rebuilt,
             sdist_source], environment=build_env)
        rebuilt_wheels = list(rebuilt.glob("*.whl"))
        if len(rebuilt_wheels) != 1:
            raise RuntimeError("sdist rebuild did not produce exactly one wheel")
        report["artifacts"] = {"wheel": str(wheels[0]), "sdist": str(sdists[0]),
                               "sdist_wheel": str(rebuilt_wheels[0])}
        # Hide the staged source too: installation and commands cannot import it.
        for old, new in ((staged, workspace / "withheld-source"),
                         (sdist_source, unpacked / "withheld-sdist-source")):
            # Only move build inputs created in this uniquely-owned workspace.
            if (not old.resolve().is_relative_to(workspace)
                    or not new.resolve().is_relative_to(workspace)
                    or new.exists()):
                raise RuntimeError("unsafe staged source move")
            old.rename(new)
        template = outside / "timeline-template.json"
        template.write_text(json.dumps({"version": 2, "project": {"fps": 30, "width": 64,
            "height": 48, "duration_frames": 30}, "sources": [], "video_tracks": [],
            "audio_tracks": [], "subtitles": []}), encoding="utf-8")
        verification = (inspect.getsource(check_installed) + "\nprint(json.dumps(check_installed("
                        + repr(expected) + ", " + repr(hashes) + ", "
                        + repr([str(source), str(workspace / "withheld-source"),
                                str(unpacked / "withheld-sdist-source")]) + ")))\n")
        # inspect source uses its own imports; json here is just the output helper.
        verification = "import json\n" + verification
        for kind, artifact in (("wheel", wheels[0]), ("sdist-rebuilt-wheel", rebuilt_wheels[0])):
            installed = workspace / ("installed-" + kind)
            venv.EnvBuilder(with_pip=True).create(installed)
            runtime, executable_dir = venv_paths(installed)
            installed_env = clean_env(home, executable_dir)
            run([runtime, "-I", "-m", "pip", "install", "--no-index", "--no-deps", artifact],
                environment=installed_env)
            installation = json.loads(run([runtime, "-I", "-c", verification], environment=installed_env))
            installation["source_kind"] = kind
            installation["artifact"] = str(artifact)
            report["installations"].append(installation)
            project = outside / kind
            project.mkdir()
            timeline = project / "timeline.json"
            shutil.copy2(template, timeline)
            vt = executable_dir / ("vt.exe" if os.name == "nt" else "vt")
            vtx = executable_dir / ("vtx.exe" if os.name == "nt" else "vtx")
            run([vt, "version"], environment=installed_env)
            json.loads(run([vt, "doctor", "--json"], environment=installed_env))
            run([vt, "plugins", "list"], environment=installed_env)
            # A real kernel operation, not only --help: no sources/media required.
            run([vtx, "-t", timeline, "--dry-run", "validate"], environment=installed_env)
            if (project / "cache").exists() or (project / "out").exists():
                raise RuntimeError("dry-run validate wrote cache/out")
            run([vtx, "-t", timeline, "validate"], environment=installed_env)
            run([vtx, "-t", timeline, "subs", "check"], environment=installed_env)
            unsupported = project / "needs-plugin.json"
            data = json.loads(timeline.read_text(encoding="utf-8"))
            data["blender_bridge"] = True
            unsupported.write_text(json.dumps(data), encoding="utf-8")
            rejection = run([vtx, "-t", unsupported, "validate"],
                            environment=installed_env, expected_exit=1)
            if "未安装" not in rejection:
                raise RuntimeError("kernel-only installation did not reject an uninstalled official plugin")
            # Preserve core-only config behavior and demonstrate no apps traceback.
            # Dependencies are checked with the actual local tools, not fake binaries.
            init = json.loads(run([vt, "init", "--json"], environment=installed_env))
            if not init["vtx_cli"]["ok"]:
                raise RuntimeError("installed init could not execute the packaged vtx CLI")
            if (Path(installation["imports"]["vt"]).parent / "config.local.json").exists():
                raise RuntimeError("init wrote configuration into the installation")
        report["ok"] = True
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        (workspace / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"package_smoke report: {workspace / 'report.json'}", flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT, help="source tree to stage (never built in place)")
    parser.add_argument("--wheelhouse", type=Path, help="offline wheels for build/setuptools/wheel and their dependencies")
    args = parser.parse_args(argv)
    try:
        result = smoke(args.source, args.wheelhouse)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"package_smoke FAIL: {error}", file=sys.stderr)
        return 1
    print(f"package_smoke PASS: {result['version']}, wheel and sdist-rebuilt wheel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
