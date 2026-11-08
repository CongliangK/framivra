"""Normalize this CLI process's inherited standard streams, not its launcher.

A POSIX parent may hand exec a nonblocking pipe or socket. Python's buffered
stdio is not a nonblocking protocol: a large JSON write can end in EAGAIN and
leave a truncated response. The CLI owns its standard descriptors for its
remaining lifetime. Importing the library alone changes no descriptor flags.
"""
import io
import os
import sys


def ensure_blocking_stdio():
    """Clear only O_NONBLOCK on real POSIX fd 0/1/2; preserve other flags.

    Python-level replacement/captured streams without a standard descriptor are
    left alone. Real descriptor inspection/set failures propagate, rather than
    silently proceeding with an output transport known to be unsafe.
    """
    if os.name != "posix":
        return
    seen = set()
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            fd = stream.fileno()
        except (AttributeError, io.UnsupportedOperation, ValueError):
            continue
        if type(fd) is not int or fd not in (0, 1, 2) or fd in seen:
            continue
        seen.add(fd)
        if not os.get_blocking(fd):
            os.set_blocking(fd, True)
