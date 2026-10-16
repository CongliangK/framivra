"""Opt-in source-level diagnostics; never a second timeline validator."""
import json
import math


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


def key_path(parent, key):
    """Bracket notation remains unambiguous for arbitrary user keys."""
    return parent + '[' + json.dumps(str(key), ensure_ascii=False) + ']'


class DiagnosticMessage(str):
    """String-compatible plugin/subtitle error carrying structured evidence."""
    def __new__(cls, message, **metadata):
        obj = super().__new__(cls, message)
        obj.metadata = metadata
        return obj


def emit(c, message, *, json_path='$', rule_id=None, measured=None,
         threshold=None, severity='error', suggested_actions=None, status=None,
         source=None):
    collector = getattr(c, 'diagnostics', None)
    if collector is None:
        return
    inherited = getattr(message, 'metadata', {})
    operations = {
        'validate.source_exists': 'restore_resource', 'validate.gfx_exists': 'restore_resource',
        'validate.source_fps': 'normalize_media_fps', 'validate.source_audio': 'select_audio_source',
        'validate.source_structure': 'provide_source_fields', 'validate.track_empty': 'add_clip',
        'validate.clip_overlap': 'resolve_overlap', 'validate.audio_overlap': 'resolve_overlap',
        'validate.clip_bounds': 'adjust_interval', 'validate.subtitle_bounds': 'adjust_interval',
        'validate.audio_event_bounds': 'adjust_interval', 'validate.clip_source': 'choose_source',
        'validate.audio_source': 'choose_source', 'validate.clip_source_range': 'provide_source_range',
        'validate.clip_edl': 'align_clip_duration', 'validate.clip_video': 'select_video_source',
        'validate.clip_source_bounds': 'trim_source_range', 'validate.audio_source_bounds': 'trim_source_range',
        'validate.clip_fade': 'reduce_fades', 'validate.audio_track_structure': 'provide_object',
        'validate.audio_events_structure': 'provide_event_list', 'validate.audio_event_required': 'provide_field',
        'subtitle.source_exists': 'restore_resource', 'subtitle.source_parse': 'repair_subtitle_file',
        'subtitle.source_type': 'provide_subtitle_path', 'subtitle.source_exclusive': 'choose_subtitle_source',
        'load.timeline_file': 'restore_resource', 'load.timeline_json': 'repair_json',
        'load.required_field': 'provide_field', 'validate.required_field': 'provide_field',
        'audit.required_field': 'provide_field', 'qc.required_field': 'provide_field', 'load.iterable': 'provide_iterable',
        'load.cache_dependencies': 'provide_dependency_list',
        'load.cache_dependency': 'restore_dependency',
    }
    effective_rule = inherited.get('rule_id', rule_id)
    effective_path = inherited.get('json_path', json_path)
    default_action = {'op': operations.get(effective_rule, 'review'), 'params': {
        'json_path': effective_path, 'measured': inherited.get('measured', measured),
        'constraint': inherited.get('threshold', threshold)}}
    record = dict(json_path=json_path, rule_id=rule_id or 'unknown',
                  measured=measured, threshold=threshold, severity=severity,
                  suggested_actions=suggested_actions or [default_action],
                  status=status or ('fail' if severity == 'error' else ('warn' if severity == 'warning' else 'info')),
                  message=str(message))
    if source is not None:
        record['source'] = source
    record.update(inherited)
    collector.append(_finite(record))


def subtitle_location(c, index):
    """Resolved indices map to the external resource, never fake inline entries."""
    if getattr(c, 'subtitles_origin', None):
        return '$.subtitles_src', dict(c.subtitles_origin, index=index)
    return f'$.subtitles[{index}]', None


class DiagnosticValueError(ValueError):
    def __init__(self, message, **metadata):
        super().__init__(message)
        self.metadata = metadata


def mark_handled(error):
    """Mark this exact producer exception, never unrelated later failures."""
    error._vtx_diagnostic_handled = True


def required_field(c, obj, key, parent, *, rule='load.required_field', source=None):
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError) as error:
        emit(c, str(error), json_path=parent if source else key_path(parent, key), rule_id=rule,
             measured={'container_type': type(obj).__name__}, threshold={'required_key': key},
             source=dict(source, field=key) if source else None)
        mark_handled(error)
        raise


def indexed(c, value, path):
    # Preserve Python's existing iteration contract, including empty dictionaries.
    try:
        return enumerate(iter(value))
    except TypeError as error:
        emit(c, str(error), json_path=path, rule_id='load.iterable',
             measured={'type': type(value).__name__}, threshold={'iterable': True})
        mark_handled(error)
        raise


def source_structure(source, index):
    """The existing validate source predicate, evaluated only by validation."""
    if (not isinstance(source, dict) or not isinstance(source.get('id'), str)
            or not source['id'] or not isinstance(source.get('path'), str) or not source['path']):
        return DiagnosticMessage(
            f"sources[{index}]: 条目须为含非空 id/path 字符串的对象",
            json_path=f'$.sources[{index}]', rule_id='validate.source_structure',
            measured={'value': source}, threshold={'required': ['id', 'path'], 'type': 'nonempty strings'})
    return None


def producer_exit(code=1):
    failure = SystemExit(code)
    mark_handled(failure)
    raise failure


def operation(c, function, *, path, rule, operands=None, source=None,
              exceptions=(TypeError, ValueError, KeyError, IndexError,
                          ZeroDivisionError, OverflowError, AttributeError),
              status='fail'):
    """Observe one native operation at its producer; never replay or infer cause.

    Callers must exclude I/O and external callbacks from ``function``. Operand
    paths describe provenance, not a claim that any particular value is wrong.
    Nested annotated accesses retain their more specific original diagnostic.
    """
    try:
        return function()
    except exceptions as error:
        if not getattr(error, '_vtx_diagnostic_handled', False):
            emit(c, str(error), json_path=path, rule_id=rule,
                 measured={'exception_type': type(error).__name__,
                           'exception_message': str(error), 'operands': operands},
                 threshold={'operation_must_complete': True}, source=source,
                 status=status, suggested_actions=[{'op': 'review_operand', 'params': {
                     'json_path': path, 'operands': operands}}])
            mark_handled(error)
        raise
