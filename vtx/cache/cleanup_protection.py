"""Read-only cleanup protection for author files and explicit timeline inputs.

Only declared path fields are inspected, never arbitrary cache metadata or HTML.
JSON is not a blanket protected suffix: producer certificates remain reclaimable.
"""
import json
import os
from pathlib import Path

from ..pipeline.infra import ProjectPathError


AUTHOR_SUFFIXES = frozenset({
    '.py', '.sh', '.md', '.rst', '.txt', '.html', '.htm', '.css', '.js',
    '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.svg', '.ass', '.srt', '.vtt',
    '.yaml', '.yml', '.toml', '.xml', '.ipynb', '.blend', '.kra', '.psd',
})


class CleanupProtection:
    def __init__(self, c, *, allow_missing=False):
        self.root = Path(os.path.abspath(c.root))
        self.references = set()
        self.directories = set()
        timelines = set(self.root.glob('timeline*.json'))
        timelines.add(Path(c.tl_path))
        for timeline in sorted(timelines):
            try:
                data = json.loads(timeline.read_text(encoding='utf-8'))
                self._timeline(data, allow_missing=allow_missing)
            except (OSError, ValueError, TypeError, RuntimeError) as error:
                raise ProjectPathError(
                    f'清理保护无法确认 {timeline.name} 的显式引用：{error}；'
                    '已拒绝回收，请修复时间线/源/依赖后重试') from error

    def _reference(self, value, *, allow_missing):
        if not isinstance(value, str) or not value or '\x00' in value:
            raise ValueError(f'非法路径引用：{value!r}')
        path = Path(os.path.abspath(self.root / value))
        resolved = path.resolve()
        if not allow_missing and not resolved.exists():
            raise ValueError(f'源或依赖不存在：{value}')
        for candidate in (path, resolved):
            if any(root == candidate or root in candidate.parents
                   for root in (self.root / 'cache', self.root / 'out')):
                self.references.add(candidate)
                if candidate.is_dir():
                    self.directories.add(candidate)

    def _dependencies(self, obj, *, allow_missing):
        values = obj.get('cache_dependencies', [])
        if not isinstance(values, list):
            raise ValueError('cache_dependencies 必须是路径列表')
        for value in values:
            self._reference(value, allow_missing=allow_missing)

    def _timeline(self, data, *, allow_missing):
        if not isinstance(data, dict):
            raise ValueError('时间线须为 JSON 对象')
        project = data.get('project', {})
        sources = data.get('sources', [])
        tracks = data.get('video_tracks', [])
        if (not isinstance(project, dict) or not isinstance(sources, list)
                or not isinstance(tracks, list)):
            raise ValueError('project/sources/video_tracks 形状无效')
        self._dependencies(project, allow_missing=allow_missing)
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError('sources 条目须为对象')
            self._reference(source.get('path'), allow_missing=allow_missing)
        for track in tracks:
            if not isinstance(track, dict) or not isinstance(track.get('clips', []), list):
                raise ValueError('video_tracks/clips 形状无效')
            for clip in track.get('clips', []):
                if not isinstance(clip, dict):
                    raise ValueError('clip 须为对象')
                if track.get('kind') == 'gfx':
                    self._reference(clip.get('src'), allow_missing=allow_missing)
                self._dependencies(clip, allow_missing=allow_missing)
        if data.get('subtitles_src') not in (None, ''):
            self._reference(data['subtitles_src'], allow_missing=allow_missing)

    def protects(self, path):
        path = Path(os.path.abspath(path))
        if path in self.references or any(parent in self.directories for parent in path.parents):
            return True
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            return False
        if not relative.parts or relative.parts[0] not in ('cache', 'out'):
            return False
        # Existing producer-owned text, not author input (explicit references
        # above always win). Keep these exact names reclaimable for compatibility.
        if relative.as_posix() in ('cache/subs.ass', 'cache/subs_draft.ass'):
            return False
        if (len(relative.parts) == 3 and relative.parts[0] == 'cache'
                and relative.parts[1] in ('vseg', 'vseg_draft', 'vseg_qc')
                and relative.parts[2] == 'list.txt'):
            return False
        return path.suffix.lower() in AUTHOR_SUFFIXES


def protection(c, *, refresh=False, allow_missing=False):
    if refresh or not hasattr(c, '_cleanup_protection'):
        c._cleanup_protection = CleanupProtection(c, allow_missing=allow_missing)
    return c._cleanup_protection
