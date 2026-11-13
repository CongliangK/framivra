"""Global cache policy, snapshotted once per rendering operation.

Configuration has one home: pluginlib.config (VTX_CONFIG, then the installed
repository's config.local.json). A policy is never a timeline field, a producer
certificate, permission to delete old files, or permission to skip input gates.
"""
from dataclasses import dataclass

from ..pluginlib.config import load_config

MODES = ('off', 'core', 'aggressive')
DEFAULT_MODE = 'core'


@dataclass(frozen=True)
class CachePolicy:
    mode: str = DEFAULT_MODE

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError('cache.mode must be off, core, or aggressive')

    @property
    def reuse_graphics(self):
        return self.mode != 'off'

    @property
    def compress_graphics(self):
        return self.mode == 'core'

    @property
    def reuse_media(self):
        return self.mode == 'aggressive'

    @property
    def pass_retention(self):
        return 'all' if self.mode == 'aggressive' else 'none'


def read_policy(config=None):
    """Missing means core; an explicitly malformed policy is never ignored."""
    config = load_config() if config is None else config
    if not isinstance(config, dict):
        raise ValueError('cache configuration must be a JSON object')
    cache = config.get('cache', {})
    if not isinstance(cache, dict):
        raise ValueError('cache must be a JSON object')
    mode = cache.get('mode', DEFAULT_MODE)
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError('cache.mode must be off, core, or aggressive')
    return CachePolicy(mode)


def policy_for(c):
    """Freeze config at the first operation boundary, not per worker or frame."""
    policy = getattr(c, 'cache_policy', None)
    if policy is None:
        policy = read_policy()
        c.cache_policy = policy
    return policy


def graphics_root(c):
    """Persistent canonical page location, or private scratch in off mode."""
    return getattr(c, 'gfx_cache', c.cache)


def storage_root(c):
    """Stable project cache root for ledgers and capacity accounting."""
    return getattr(c, 'cache_store', c.cache)
