"""Shared SoundFont shorthand parsing for render projection and input identity."""
import os


def parse_instrument_shorthand(value):
    """Return (reference, bank, program), or None for an unadorned reference.

    A native absolute path can have a drive colon; only the two suffix
    separators participate in the shorthand. Non-integers still raise
    ValueError; out-of-range numbers leave the reference unadorned as before.
    """
    drive, tail = os.path.splitdrive(value) if os.path.isabs(value) else ("", value)
    if tail.count(":") != 2:
        return None
    name, bank, program = tail.rsplit(":", 2)
    bank, program = int(bank), int(program)
    if 0 <= bank <= 128 and 0 <= program <= 127:
        return drive + name, bank, program
    return None
