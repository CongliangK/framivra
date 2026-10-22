"""Opt-in acceptance correlation, independent of acceptance/1 and cache identity.

The private bridge is correlation under the trusted exporter boundary, not
attestation. No request can select an output path, executable, or command.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import uuid

from .events import observation_warning, strict_json_loads

REQUEST_SCHEMA = 'vtx.observation-request/1'
SCHEMA = 'vtx.execution-links/1'
DIRECTORY = '.vtx-observation'
SIDECAR = 'artifacts/execution-links.json'
ROLES = ('all_until_mux', 'qc_strict')
MAX_REQUEST = 4096


def _uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('noncanonical observation UUID')
    return value


def validate_request(request, index=None, export_id=None):
    if not isinstance(request, dict) or set(request) != {
            'schema_version', 'export_id', 'nonce', 'command_index', 'role'}:
        raise ValueError('invalid observation request fields')
    if request['schema_version'] != REQUEST_SCHEMA:
        raise ValueError('unsupported observation request schema')
    _uuid(request['export_id'])
    _uuid(request['nonce'])
    i = request['command_index']
    if type(i) is not int or i not in (0, 1) or request['role'] != ROLES[i]:
        raise ValueError('observation command role mismatch')
    if index is not None and i != index or export_id is not None and request['export_id'] != export_id:
        raise ValueError('observation export/index mismatch')
    return request


def _directory(path):
    """Open every component with no-follow; descriptors pin the selected tree."""
    path = Path(os.path.abspath(path))
    if not hasattr(os, 'O_NOFOLLOW') or os.open not in os.supports_dir_fd:
        raise OSError('safe observation filesystem operations unavailable')
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        return fd
    except BaseException:
        os.close(fd)
        raise


def _private(fd, directory=False):
    info = os.fstat(fd)
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError('nonregular observation entry')
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('observation entry must be private and owned')
    if not directory and info.st_nlink != 1:
        raise ValueError('linked observation entry')


def _bridge(project):
    fd = _directory(Path(project) / DIRECTORY)
    try:
        _private(fd, directory=True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_at(fd, name, limit=None):
    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        _private(child)
        with os.fdopen(child, 'rb', closefd=False) as stream:
            data = stream.read() if limit is None else stream.read(limit + 1)
        if limit is not None and len(data) > limit:
            raise ValueError('oversized observation request')
        return data
    finally:
        os.close(child)


def _read_request(fd, index=None, export_id=None):
    data = _read_at(fd, 'request.json', MAX_REQUEST)
    request = strict_json_loads(data, duplicate='duplicate observation JSON key',
                               nonfinite='nonfinite observation value')
    return validate_request(request, index, export_id)


def _initialize(project):
    """Exclusive reservation prevents copied inputs from supplying requests."""
    fd = _directory(project)
    try:
        os.mkdir(DIRECTORY, mode=0o700, dir_fd=fd)
        bridge = os.open(DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            _private(bridge, directory=True)
            os.mkdir('responses', mode=0o700, dir_fd=bridge)
        finally:
            os.close(bridge)
    finally:
        os.close(fd)


def initialize(project):
    """Unavailable instrumentation must not prevent the legacy CPU build."""
    try:
        _initialize(project)
        return True
    except (OSError, ValueError):
        observation_warning('[vtx] observation bridge unavailable; execution linkage incomplete')
        return False


def prepare_request(project, export_id, index, *, enabled=True):
    request = validate_request({'schema_version': REQUEST_SCHEMA, 'export_id': export_id,
        'nonce': str(uuid.uuid4()), 'command_index': index, 'role': ROLES[index]})
    if not enabled:
        return request
    fd = None
    temporary = request['nonce'] + '.tmp'
    try:
        fd = _bridge(project)
        if index == 0:
            try:
                os.stat('request.json', dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError('initial observation request collision')
        else:
            _read_request(fd, index - 1, export_id)
        child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600, dir_fd=fd)
        with os.fdopen(child, 'w', encoding='utf-8') as stream:
            json.dump(request, stream, sort_keys=True, allow_nan=False)
            stream.write('\n')
        os.replace(temporary, 'request.json', src_dir_fd=fd, dst_dir_fd=fd)
    except (OSError, ValueError):
        observation_warning('[vtx] observation request unavailable; execution linkage incomplete')
    finally:
        if fd is not None:
            os.close(fd)
    return request


def open_response(timeline_path, command_role, *, dry=False):
    """Return (exclusive text stream, request), or (None, None) without writes.

    Invalid/absent requests disable observation without changing render behavior.
    The caller owns the returned stream and echoes request in the run start.
    """
    if dry or command_role not in ROLES:
        return None, None
    project = Path(timeline_path).absolute().parent
    if not os.path.lexists(project / DIRECTORY):
        return None, None
    fd = responses = None
    try:
        fd = _bridge(project)
        request = _read_request(fd)
        if request['role'] != command_role:
            raise ValueError('observation role mismatch')
        responses = os.open('responses', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        _private(responses, directory=True)
        child = os.open(request['nonce'] + '.jsonl',
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600, dir_fd=responses)
        return os.fdopen(child, 'w', encoding='utf-8'), request
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError, TypeError, AttributeError):
        observation_warning("[vtx] observation request unavailable; execution linkage incomplete")
        return None, None
    finally:
        if responses is not None:
            os.close(responses)
        if fd is not None:
            os.close(fd)


def response_bytes(project, request):
    fd = _bridge(project)
    responses = None
    try:
        if _read_request(fd) != request:
            return None
        responses = os.open('responses', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        _private(responses, directory=True)
        try:
            return _read_at(responses, request['nonce'] + '.jsonl')
        except FileNotFoundError:
            return None
    finally:
        if responses is not None:
            os.close(responses)
        os.close(fd)


def _evidence(data, request, exit_code):
    if data is None:
        return {'run_id': None, 'evidence_status': 'incomplete', 'reason': 'start_unobserved'}, []
    from .events import read_events
    parsed = read_events(data)
    events = parsed['events']
    starts = [event for event in events if event['event'] == 'run_start']
    if starts and starts[0].get('extensions', {}).get('observation') != request:
        raise ValueError('execution response does not match issued request')
    if not starts:
        return {'run_id': None, 'evidence_status': 'incomplete', 'reason': 'start_unobserved'}, events
    run_id = _uuid(starts[0]['run_id'])
    if run_id == request['export_id']:
        raise ValueError('execution and export identities must be distinct')
    if parsed['complete'] and parsed['exit_code'] != exit_code:
        raise ValueError('execution terminal disagrees with subprocess exit')
    return {'run_id': run_id, 'evidence_status': 'complete' if parsed['complete'] else 'incomplete',
            'reason': None if parsed['complete'] else parsed['reason']}, events


def retain_attempt(project, output, request, launch_status, exit_code, *, enabled=True):
    """Retain only the issued request and its selected child response."""
    from ..inspection.acceptance import write_json
    index = request['command_index']
    request_path = f'artifacts/execution-requests/{index}.json'
    (Path(output) / request_path).parent.mkdir(exist_ok=True)
    write_json(Path(output) / request_path, request)
    try:
        data = response_bytes(project, request) if enabled else None
    except (OSError, ValueError):
        observation_warning('[vtx] observation response unavailable; execution linkage incomplete')
        data = None
    response = None
    if data is not None:
        response = f'artifacts/execution-events/{request["nonce"]}.jsonl'
        (Path(output) / response).parent.mkdir(exist_ok=True)
        with (Path(output) / response).open('xb') as stream:
            stream.write(data)
    evidence, _ = _evidence(data, request, exit_code)
    if launch_status == 'launch_error' and evidence['evidence_status'] == 'complete':
        raise ValueError('launch error cannot claim completed execution')
    return {'command_index': index, 'role': ROLES[index], 'launch_status': launch_status,
            'exit_code': exit_code, 'request': request_path, 'response': response, **evidence}


def not_attempted(index):
    return {'command_index': index, 'role': ROLES[index], 'launch_status': 'not_attempted',
            'exit_code': None, 'request': None, 'response': None, 'run_id': None,
            'evidence_status': 'not_attempted', 'reason': 'not_attempted'}


def seal(output, export_id, rows):
    from ..inspection.acceptance import write_json
    write_json(Path(output) / SIDECAR, {'schema_version': SCHEMA, 'export_id': export_id,
                                      'commands': rows})


def verify_links(package, manifest, receipt):
    """Verify correlation after acceptance's inventory/hash checks, never execute."""
    from ..inspection.acceptance import read_json, keys, regular
    inventory = manifest['files']
    references = [(name, row['observed_by']) for name, row in receipt.get('stages', {}).items()
                  if 'observed_by' in row]
    bridge_files = {name for name in inventory if name.startswith(
        ('artifacts/execution-requests/', 'artifacts/execution-events/'))}
    if SIDECAR not in inventory:
        if bridge_files or references:
            raise ValueError('execution linkage evidence lacks sidecar')
        return 'unknown'
    sidecar = read_json(Path(package) / SIDECAR)
    keys(sidecar, 'schema_version export_id commands', 'execution links')
    if sidecar['schema_version'] != SCHEMA:
        raise ValueError('unsupported execution links schema')
    if sidecar['export_id'] != manifest['run_id']:
        raise ValueError('execution export identity mismatch')
    rows = sidecar['commands']
    if not isinstance(rows, list) or len(rows) != 2:
        raise ValueError('execution links require two command accounting rows')
    executions = manifest['build']['executions']
    selected = set()
    ids = set()
    events_by_id = {}
    complete = True
    for index, row in enumerate(rows):
        keys(row, 'command_index role launch_status exit_code request response run_id evidence_status reason',
             'execution linkage row')
        if type(row['command_index']) is not int or row['command_index'] != index or row['role'] != ROLES[index]:
            raise ValueError('execution linkage command mismatch')
        if index >= len(executions):
            if row != not_attempted(index):
                raise ValueError('unattempted command claims execution evidence')
            continue
        if row['launch_status'] not in ('returned', 'launch_error') or type(row['exit_code']) is not int or row['exit_code'] != executions[index]['exit_code']:
            raise ValueError('execution linkage launch/exit mismatch')
        if row['launch_status'] == 'launch_error' and row['exit_code'] != -1:
            raise ValueError('launch error disagrees with legacy exit')
        request_path = f'artifacts/execution-requests/{index}.json'
        if row['request'] != request_path or request_path not in inventory:
            raise ValueError('missing or unsafe execution request evidence')
        selected.add(request_path)
        request = validate_request(read_json(Path(package) / request_path), index, manifest['run_id'])
        data = None
        if row['response'] is not None:
            response = f'artifacts/execution-events/{request["nonce"]}.jsonl'
            if row['response'] != response or response not in inventory or response in selected:
                raise ValueError('missing, colliding or unsafe execution response')
            selected.add(response)
            data = regular(Path(package) / response).read_bytes()
        evidence, events = _evidence(data, request, row['exit_code'])
        if any(row[key] != value for key, value in evidence.items()):
            raise ValueError('execution linkage claim disagrees with events')
        if row['launch_status'] == 'launch_error' and evidence['evidence_status'] == 'complete':
            raise ValueError('launch error cannot claim completed execution')
        run_id = evidence['run_id']
        if run_id is not None:
            if run_id in ids:
                raise ValueError('child execution IDs must be distinct')
            ids.add(run_id)
        complete = complete and evidence['evidence_status'] == 'complete'
        for event in events:
            events_by_id[tuple(event['event_id'])] = event
    if selected != bridge_files:
        raise ValueError('unselected execution bridge evidence')
    for stage, ref in references:
        keys(ref, 'run_id task_id event_id', 'stage observation')
        event_id = ref['event_id']
        if not isinstance(event_id, list) or len(event_id) != 2 or not isinstance(event_id[0], str) or type(event_id[1]) is not int:
            raise ValueError('invalid stage observation event identity')
        _uuid(ref['run_id'])
        _uuid(ref['task_id'])
        if ref['run_id'] != event_id[0] or ref['run_id'] not in ids or event_id[1] < 1:
            raise ValueError('stage observation references an unobserved execution')
        event = events_by_id.get(tuple(event_id))
        if event is None:
            # A post-commit sink failure can leave a genuine receipt reference
            # whose promised event never reached disk. Never certify completeness.
            complete = False
            continue
        if event['run_id'] != ref['run_id'] or event['task_id'] != ref['task_id'] or event.get('extensions', {}).get('receipt_stage') != stage or event['event'] != 'receipt_commit' or event['stage'] != stage or event['scope'] != 'stage':
            raise ValueError('stage observation disagrees with receipt commit')
        expected = 'succeeded' if receipt['stages'][stage].get('ok') is True else 'failed'
        if event['outcome'] != expected:
            raise ValueError('stage receipt outcome disagrees with observation')
    return 'complete' if complete else 'incomplete'
