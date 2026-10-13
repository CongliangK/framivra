"""Build-time resources: the authored plugin declaration stays in plugins/."""
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py


class BuildPy(_build_py):
    def run(self):
        super().run()
        source = Path(__file__).resolve().parent / "plugins" / "registry.json"
        target = Path(self.build_lib) / "vtx" / "pluginlib" / "builtin_registry.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        self.copy_file(str(source), str(target))


setup(cmdclass={"build_py": BuildPy})
