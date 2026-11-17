"""Project constraints and exact frame clocks.

ALLOWED_FPS is mirrored by timeline.schema.json and preview.html; the
three-surface contract is guarded by tests/media/test_fps_constraints.py.
Decimal NTSC labels are aliases, not approximate clocks. Audio positions use
rounded absolute frame endpoints, never rounded samples-per-frame products.
Resolution is enforced by schema/preview, not Ctx (small render fixtures).
"""
from fractions import Fraction

AUDIO_SAMPLE_RATE = 48000
ALLOWED_FPS = (24, 25, 30, 48, 50, 60, 100, 120,
               23.976, 29.97, 47.952, 59.94, 119.88,
               "24000/1001", "30000/1001", "48000/1001", "60000/1001", "120000/1001")
ALLOWED_SIZES = ((1920, 1080),)
_NTSC = {23.976: Fraction(24000, 1001), 29.97: Fraction(30000, 1001),
         47.952: Fraction(48000, 1001), 59.94: Fraction(60000, 1001),
         119.88: Fraction(120000, 1001)}


def fps_error(fps):
    """Validate a stored project rate; bool is not a JSON number here."""
    if isinstance(fps, bool) or not isinstance(fps, (int, float, str)) or fps not in ALLOWED_FPS:
        return (f"project.fps={fps!r} 无效：仅支持 "
                + ", ".join(map(str, ALLOWED_FPS))
                + " fps；NTSC 小数标签按精确 N/1001 时基解释")
    return None


def parse_fps(fps):
    """Exact rate for stored labels or normalized internal numeric rates.

    Ctx validates stored values with fps_error before normalizing. Internal
    callers may also pass Fraction or float(exact_rate), without losing the
    NTSC identity at the FFmpeg boundary.
    """
    if isinstance(fps, Fraction):
        return fps
    if not isinstance(fps, bool) and isinstance(fps, (int, float, str)):
        for label, rate in _NTSC.items():
            if fps == label or fps == float(rate) or fps == str(rate):
                return rate
        if fps in ALLOWED_FPS or (isinstance(fps, str) and
                                  fps in {str(f) for f in ALLOWED_FPS if isinstance(f, int)}):
            return Fraction(fps)
    raise ValueError(fps_error(fps))


def fps_expression(fps):
    """FFmpeg-compatible exact rate (also stable in cache identities)."""
    return str(parse_fps(fps))


def frame_sample(frame, fps):
    """Nearest 48kHz sample at an absolute integer frame, ties toward +inf."""
    value = Fraction(frame) * AUDIO_SAMPLE_RATE / parse_fps(fps)
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def size_error(width, height):
    """Resolution gate for schema/preview; Ctx permits small render fixtures."""
    if (width, height) not in ALLOWED_SIZES:
        allowed = "、".join(f"{w}x{h}" for w, h in ALLOWED_SIZES)
        return (f"project.width/height={width!r}x{height!r} 无效："
                f"分辨率仅允许 {allowed}")
    return None
