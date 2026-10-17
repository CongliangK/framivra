"""Compatibility import; production and experiment consumers share one client."""
from vtx.cef_client import *  # noqa: F401,F403
from vtx import cef_client as _impl


def __getattr__(name):
    return getattr(_impl, name)
