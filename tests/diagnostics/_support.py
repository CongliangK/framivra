"""Fresh synthetic timelines and per-test temporary roots; no TestCases."""
import tempfile
from pathlib import Path


def timeline():
    return {'version': 2, 'project': {'fps': 30, 'width': 160, 'height': 90,
            'duration_frames': 90}, 'sources': [], 'video_tracks': [],
            'audio_tracks': [], 'subtitles': []}


def temporary_root(test):
    temporary = tempfile.TemporaryDirectory()
    test.addCleanup(temporary.cleanup)
    return Path(temporary.name)
