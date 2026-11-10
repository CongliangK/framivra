"""Shared MG test execution, without caching modules or changing skip policy."""
import importlib.util
from pathlib import Path

# Cross-distro default font candidates, kept in lockstep with the candidate
# lists in templates/mg-styles/styles/*/scripts renderers.
CJK_FONT_CANDIDATES = (
    Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'),
    Path('/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc'),
    Path('/usr/share/fonts/google-noto-sans-cjk-vf-fonts/NotoSansCJK-VF.ttc'),
)
SERIF_CJK_FONT_CANDIDATES = (
    Path('/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc'),
    Path('/usr/share/fonts/noto-cjk/NotoSerifCJK-Regular.ttc'),
    Path('/usr/share/fonts/google-noto-serif-cjk-vf-fonts/NotoSerifCJK-VF.ttc'),
)
LATIN_FONT_CANDIDATES = (
    Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),
    Path('/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf'),
    Path('/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf'),
    Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
    Path('/usr/share/fonts/dejavu/DejaVuSans.ttf'),
    Path('/usr/share/fonts/liberation-sans-fonts/LiberationSans-Regular.ttf'),
)


def find_font(*candidates):
    """First existing candidate, else None so callers keep their skip policy."""
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_renderer(name, path):
    """Load a fresh renderer under the caller's existing module identity."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

