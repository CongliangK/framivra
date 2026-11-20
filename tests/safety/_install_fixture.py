"""Child-process fixture for archive/transaction safety, not product acceptance.

Only host-level Windows shim/PATH integration is opted out via the existing
wizard choice. FFmpeg detection/enforcement, downloads, SHA256, extraction,
locking, rollback and the payload's init/doctor processes remain real. The CI
candidate's separate real-payload install still exercises host integration.
Run with sys.executable, never rely on executable shebang support on Windows.
"""
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import sys
from urllib.parse import urlsplit
from unittest import mock


def dependency_fixtures(directory, home):
    """Create only temporary FFmpeg/curl fixtures; never expose host winget.

    Both archive and provenance integration suites use the same native Windows
    transport. The caller supplies its owned sandbox and passes the returned
    environment to installer children; no host environment is changed here.
    """
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg = directory / ("ffmpeg.cmd" if os.name == "nt" else "ffmpeg")
    if os.name == "nt":
        ffmpeg.write_bytes(b"@echo off\r\necho ffmpeg version 6.1 fixture\r\n")
        curl = Path(os.environ["SystemRoot"]) / "System32" / "curl.exe"
        if not curl.is_file():
            raise RuntimeError("local HTTP fixture needs Windows system curl.exe")
        shutil.copyfile(curl, directory / "curl.exe")
    else:
        ffmpeg.write_text("#!/bin/sh\nprintf 'ffmpeg version 6.1 fixture\\n'\n", encoding="utf-8")
        ffmpeg.chmod(0o755)
        curl = shutil.which("curl")
        if curl is None:
            raise RuntimeError("local HTTP fixture needs native curl")
        wrapper = directory / "curl"
        wrapper.write_text('#!/bin/sh\nexec %s -q --noproxy "*" "$@"\n' % shlex.quote(curl),
                           encoding="utf-8")
        wrapper.chmod(0o755)
    return ffmpeg, {"PATH": str(directory), "CURL_HOME": str(home),
                    "NO_PROXY": "*", "no_proxy": "*"}


def load_installer():
    path = Path(__file__).resolve().parents[2] / "scripts" / "install.py"
    spec = importlib.util.spec_from_file_location("vt_install_py", path)
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    return installer


def main(argv=None):
    installer = load_installer()
    write_shims = installer.write_shims

    def sandbox_shims(target, wizard):
        # CLI non-interactive defaults would otherwise write the real HKCU PATH.
        # Keep the production skip branch, rather than faking registry success.
        wizard.choices["shims_path"] = False
        return write_shims(target, wizard)

    with ExitStack() as patches:
        patches.enter_context(mock.patch.object(installer, "write_shims", sandbox_shims))
        origin = os.environ.get('SAFETY_INSTALL_LOOPBACK')
        if origin:
            endpoint = urlsplit(origin)
            if (endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1'
                    or endpoint.username is not None or endpoint.port is None):
                raise AssertionError('installer fixture requires an owned loopback listener')

            run = installer.subprocess.run
            curl = Path(os.environ['PATH']).resolve() / ('curl.exe' if os.name == 'nt' else 'curl')
            if not curl.is_file():
                raise AssertionError('installer fixture is missing its owned curl')

            def pinned_run(argv, *args, **kwargs):
                if isinstance(argv, (tuple, list)) and argv and Path(argv[0]).stem.casefold() == 'curl':
                    if (len(argv) not in (3, 5) or argv[1] != '-fsSL'
                            or (len(argv) == 5 and argv[3] != '-o')):
                        raise AssertionError('unexpected installer fixture curl command')
                    parsed = urlsplit(argv[2])
                    if (parsed.scheme, parsed.netloc) != (endpoint.scheme, endpoint.netloc):
                        raise AssertionError('installer fixture refused a URL outside its listener')
                    # Pin Windows executable lookup, ignore user curl config and
                    # proxies, and reject redirects away from the owned listener.
                    argv = [str(curl), '-q', '--noproxy', '*', '--max-redirs', '0', *argv[1:]]
                return run(argv, *args, **kwargs)

            patches.enter_context(mock.patch.object(installer.subprocess, 'run', pinned_run))
            for name in ('_winget_install', 'undo_path', 'undo_path_quiet'):
                patches.enter_context(mock.patch.object(installer, name,
                    side_effect=AssertionError('installer fixture refused host integration')))
        return installer.main(argv)


if __name__ == "__main__":
    sys.exit(main())
