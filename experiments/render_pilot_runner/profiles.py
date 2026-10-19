"""One fixed, opt-in local window; no arbitrary window or deadline overrides."""
from .contract import (BASE_COMMIT, BASE_TREE, PLAN_SHA256, PROFILE, WIDTH, HEIGHT,
                       FPS, DEADLINE_SECONDS, mapped, require)

CONTINUOUS300 = 'continuous300-v1'
POLL120 = 'poll-observation120-v1'
SCHEMA = 'render-pilot/2'
ENTRY_KEYS = ('entryId', 'layerId', 'src', 'windowStart', 'duration', 'paintIndex')
ENTRY_VALUES = (
    ('T08.01', 'T08', 'gfx/T08_0018_3600.html', 18, 3582, 6),
    ('T07.01', 'T07', 'gfx/T07_0000_3600.html', 0, 3600, 7),
)
RESOURCE_POLICY = {
    'observation': 'DISABLED', 'thresholds': 'UNFROZEN',
    'promotion': 'UNKNOWN',
    'reason': 'Current Mac budget and missing-sample policy require separate freezing',
}


def validate_selection(schema, target_profile, mode, group_plan):
    require((schema, target_profile, mode, group_plan) in (
            (SCHEMA, CONTINUOUS300, 'fast-png', None),
            (SCHEMA, POLL120, 'poll-observation', None)),
            'Unsupported schema/target_profile/mode/group combination')


def continuous_plan(bound, mode, group_plan, target_profile):
    validate_selection(SCHEMA, target_profile, mode, group_plan)
    require(target_profile == CONTINUOUS300, 'continuous_plan accepts only continuous300-v1')
    require([tuple(e.get(k) for k in ENTRY_KEYS) for e in bound['entries']] == list(ENTRY_VALUES),
            'continuous300 requires the exact original entries, windows and paint order')
    targets = list(range(1380, 1680))
    jobs = [{'arm': arm, 'batch': 'continuous', 'entry': entry, 'project': bound['root'],
             'mapping': mapped(entry, targets), 'expected_rendered': 300}
            for arm in ('baseline', 'candidate') for entry in bound['entries']]
    return {'schema': SCHEMA, 'status': 'UNRUN',
            'base': {'commit': BASE_COMMIT, 'tree': BASE_TREE},
            'today_plan_sha256': PLAN_SHA256, 'mode': mode, 'bound': bound,
            'profile': PROFILE, 'geometry': [WIDTH, HEIGHT, FPS],
            'deadline_seconds': DEADLINE_SECONDS, 'target_profile': target_profile,
            'targets': targets, 'batches': {'continuous': targets}, 'jobs': jobs,
            'group_plan': None, 'group_plan_sha256': None, 'group_verified': None,
            'expected_targets_per_arm': 600, 'expected_rgba_pairs': 600,
            'runtime_identity': 'limited-callable-source-binding/1',
            'resource_policy': dict(RESOURCE_POLICY),
            'quality_scope': 'two entries in one ten-second window only; full scene/audio/all UNRUN'}


def poll_plan(bound, mode, group_plan, target_profile=POLL120):
    from .contract import build_plan
    from experiments.fast_png_pilot.poll_observer import LIMITS, CLOCK_KIND
    validate_selection(SCHEMA, target_profile, mode, group_plan)
    require(target_profile == POLL120, 'poll_plan accepts only poll-observation120-v1')
    require([tuple(e.get(k) for k in ENTRY_KEYS) for e in bound['entries']] == list(ENTRY_VALUES),
            'poll observation requires the exact original entries, windows and paint order')
    plan = build_plan(bound, 'fast-png')
    plan.update(schema=SCHEMA, mode=mode, target_profile=POLL120,
                expected_targets_per_arm=238, expected_rgba_pairs=238,
                runtime_identity='limited-callable-source-binding/1', resource_policy=dict(RESOURCE_POLICY),
                poll_observation={'baseline':False,'candidate':True,
                                  'schema':'navigation-poll-observation/1',
                                  'clock_kind':CLOCK_KIND,'limits':dict(LIMITS)},
                capture_modes={'baseline':'baseline','candidate':'baseline'},
                quality_scope='original 120-global two-entry window; exact PNG only; observation disturbance, not speed qualification')
    return plan
