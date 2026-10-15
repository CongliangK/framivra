"""Child-process fixture for archive/transaction safety, not product acceptance.

Only host-level Windows shim/PATH integration is opted out via the existing
wizard choice. FFmpeg detection/enforcement, downloads, SHA256, extraction,
locking, rollback and the payload's init/doctor processes remain real. The CI
candidate's separate real-payload install still exercises host integration.
Run with sys.executable, never rely on executable shebang support on Windows.
"""
import importlib.util
from pathlib import Path
import sys
from unittest import mock


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

    with mock.patch.object(installer, "write_shims", sandbox_shims):
        return installer.main(argv)


if __name__ == "__main__":
    sys.exit(main())
