"""Font-default portability guards: resolution, explicit-path rejection, VF weights.

Prevents the MG suites from going green merely because this machine happens to
have a font at a path the renderers hardcode. Locks three contracts:
- each renderer's default candidates resolve to an existing file (or raise);
- an explicit nonexistent font_path is rejected, never silently replaced;
- variable-font weight normalization applies only to the distro default VF
  paths, never to a caller's own font file, and fails loudly if the requested
  instance cannot be applied.
"""
from pathlib import Path
import importlib.util
import json
import shutil
import tempfile
import unittest

from PIL import Image, ImageChops

from tests.gfx._mg import (
    CJK_FONT_CANDIDATES,
    LATIN_FONT_CANDIDATES,
    SERIF_CJK_FONT_CANDIDATES,
    load_renderer,
)

ROOT = Path(__file__).resolve().parents[2]

# (style dir, renderer file, sans/serif) for every PIL-based MG renderer.
RENDERERS = [
    ('aurora-glass', 'render_aurora_glass.py', 'sans'),
    ('bauhaus', 'render_bauhaus.py', 'sans'),
    ('cel-boil', 'render_cel.py', 'sans'),
    ('collage', 'render_collage.py', 'sans'),
    ('flat-vector', 'render_flat_vector.py', 'sans'),
    ('hud', 'render_hud.py', 'sans'),
    ('isometric', 'render_isometric.py', 'sans'),
    ('line-art', 'render_line_art.py', 'serif'),
    ('liquid', 'render_liquid.py', 'sans'),
    ('morph', 'render_morph.py', 'sans'),
    ('pixel', 'render_pixel.py', 'sans'),
    ('sticker', 'render_sticker.py', 'sans'),
    ('synthwave', 'render_synthwave.py', 'sans'),
    ('variety-captions', 'captions.py', 'sans'),
]


class FontDefaultsSyncTests(unittest.TestCase):
    def test_renderer_candidates_match_test_fixtures(self):
        """Every renderer ships the same candidate lists the tests rely on."""
        for style, script, kind in RENDERERS:
            module = load_renderer(
                'mg_sync_' + style.replace('-', '_'),
                ROOT / 'templates/mg-styles/styles' / style / 'scripts' / script,
            )
            cjk_attr = (
                '_DEFAULT_SERIF_CJK_FONTS' if kind == 'serif' else '_DEFAULT_CJK_FONTS'
            )
            expected_cjk = (
                SERIF_CJK_FONT_CANDIDATES if kind == 'serif' else CJK_FONT_CANDIDATES
            )
            self.assertEqual(
                tuple(getattr(module, cjk_attr)), expected_cjk,
                f'{style}/scripts/{script} {cjk_attr} drifted from tests/gfx/_mg.py',
            )
            if hasattr(module, '_DEFAULT_LATIN_FONTS'):
                self.assertEqual(
                    tuple(module._DEFAULT_LATIN_FONTS), LATIN_FONT_CANDIDATES,
                    f'{style}/scripts/{script} _DEFAULT_LATIN_FONTS drifted from tests/gfx/_mg.py',
                )
            self.assertEqual(
                tuple(Path(p) for p in module._VF_DEFAULT_WEIGHTS),
                (CJK_FONT_CANDIDATES[-1], SERIF_CJK_FONT_CANDIDATES[-1]),
                f'{style}/scripts/{script} _VF_DEFAULT_WEIGHTS keys drifted from the VF candidates',
            )

    def test_default_candidates_are_distinct_absolute_paths(self):
        for candidates in (CJK_FONT_CANDIDATES, SERIF_CJK_FONT_CANDIDATES, LATIN_FONT_CANDIDATES):
            self.assertTrue(all(p.is_absolute() for p in candidates))
            self.assertEqual(len(set(candidates)), len(candidates))


class FontResolutionTests(unittest.TestCase):
    def test_default_resolution_returns_existing_file(self):
        """morph._font_file with no explicit path must pick an existing candidate."""
        morph = load_renderer(
            'mg_font_morph',
            ROOT / 'templates/mg-styles/styles/morph/scripts/render_morph.py',
        )
        data = json.loads(
            (ROOT / 'templates/mg-styles/styles/morph/assets/examples/morph-growth.json').read_text()
        )
        resolved = morph._font_file(data)
        self.assertIn(Path(resolved), CJK_FONT_CANDIDATES + LATIN_FONT_CANDIDATES)
        self.assertTrue(Path(resolved).is_file())

    def test_explicit_nonexistent_font_path_is_rejected(self):
        morph = load_renderer(
            'mg_font_morph2',
            ROOT / 'templates/mg-styles/styles/morph/scripts/render_morph.py',
        )
        data = json.loads(
            (ROOT / 'templates/mg-styles/styles/morph/assets/examples/morph-growth.json').read_text()
        )
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / 'nonexistent-font.ttf'
            with self.assertRaises(ValueError):
                morph.validate_morph_content(data, 240, str(missing))


class VFWeightTests(unittest.TestCase):
    """Same real VF path: implicit default gets the template weight, explicit
    keeps the font's default instance — proven through actual rendering."""

    def _vf(self):
        return CJK_FONT_CANDIDATES[-1] if CJK_FONT_CANDIDATES[-1].is_file() else None

    def _data(self):
        return json.loads(
            (ROOT / 'templates/mg-styles/styles/morph/assets/examples/morph-growth.json').read_text()
        )

    def _morph(self, ident):
        return load_renderer(
            'mg_font_' + ident,
            ROOT / 'templates/mg-styles/styles/morph/scripts/render_morph.py',
        )

    def test_implicit_default_resolves_with_vf_weight(self):
        vf = self._vf()
        if vf is None:
            self.skipTest('default VF font not installed')
        morph = self._morph('vf_implicit')
        resolved = morph._font_file(self._data())
        self.assertEqual(str(resolved), str(vf))
        self.assertEqual(getattr(resolved, 'default_weight', None), 'Bold')

    def test_explicit_same_vf_path_stays_plain(self):
        vf = self._vf()
        if vf is None:
            self.skipTest('default VF font not installed')
        morph = self._morph('vf_explicit')
        resolved = morph._font_file(self._data(), str(vf))
        self.assertEqual(str(resolved), str(vf))
        self.assertIsNone(getattr(resolved, 'default_weight', None))

    def test_rendered_frames_differ_between_implicit_and_explicit(self):
        vf = self._vf()
        if vf is None:
            self.skipTest('default VF font not installed')
        morph = self._morph('vf_render')
        data = self._data()
        # Both paths validate and render against the SAME real VF file.
        implicit = morph.render_morph_frame(data, 0, 240)
        explicit = morph.render_morph_frame(data, 0, 240, str(vf))
        diff = ImageChops.difference(implicit, explicit)
        self.assertIsNotNone(diff.getbbox())

    def test_explicit_renamed_vf_copy_still_unvaried(self):
        vf = self._vf()
        if vf is None:
            self.skipTest('default VF font not installed')
        morph = self._morph('vf_copy')
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / 'my-own-NotoSansCJK-VF.ttc'
            shutil.copyfile(vf, copy)
            resolved = morph._font_file(self._data(), str(copy))
            self.assertIsNone(getattr(resolved, 'default_weight', None))
            explicit = morph.render_morph_frame(self._data(), 0, 240, str(copy))
            same_path = morph.render_morph_frame(self._data(), 0, 240, str(vf))
            self.assertIsNone(ImageChops.difference(explicit, same_path).getbbox())


if __name__ == '__main__':
    unittest.main()
