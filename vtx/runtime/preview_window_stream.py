"""Private fixed-wheel first-frame wire; never an ordinary preview CLI mode."""
import base64
import hashlib
import io
import json
import re
import struct

from .preview_window_isolation import IsolationError

ABI = 'framivra.preview-window.first-frame/v1'
NONCE = re.compile(r'[0-9a-f]{32}')
FIRST, FINAL = b'F', b'W'
_HEADER = struct.Struct('>cI')


def pack(kind, payload, limit):
    body = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('ascii')
    if not 0 < len(body) < limit:
        raise IsolationError('preview record exceeds the wire limit')
    return _HEADER.pack(kind, len(body)) + body


def _object(pairs):
    result = dict(pairs)
    if len(pairs) != len(result):
        raise IsolationError('duplicate preview record field')
    return result


def _reject_constant(_):
    raise IsolationError('nonfinite preview record field')


class Decoder:
    def __init__(self, limit, nonce, on_first):
        self.limit, self.nonce, self.on_first = limit, nonce, on_first
        self.buffer = bytearray()
        self.header = None
        self.first_seen = False
        self.final = None

    def feed(self, chunk):
        self.buffer.extend(chunk)
        while self.buffer:
            if self.final is not None:
                raise IsolationError('unexpected bytes after isolated terminal record')
            if self.header is None:
                if len(self.buffer) < _HEADER.size:
                    return
                kind, size = _HEADER.unpack_from(self.buffer)
                if (kind not in (FIRST, FINAL) or not 0 < size < self.limit
                        or kind == FIRST and self.first_seen):
                    raise IsolationError('invalid isolated preview record header')
                self.header = kind, size
                del self.buffer[:_HEADER.size]
            kind, size = self.header
            if len(self.buffer) < size:
                return
            try:
                envelope = json.loads(self.buffer[:size].decode('ascii'), object_pairs_hook=_object,
                                      parse_constant=_reject_constant)
            except (ValueError, RecursionError) as error:
                raise IsolationError('invalid isolated preview record encoding') from error
            del self.buffer[:size]
            self.header = None
            if (not isinstance(envelope, dict) or envelope.keys() != {'nonce', 'result'}
                    or envelope['nonce'] != self.nonce or not isinstance(envelope['result'], dict)):
                raise IsolationError('unexpected isolated preview record identity')
            if kind == FIRST:
                self.first_seen = True
                self.on_first(envelope['result'])
            else:
                self.final = envelope['result']

    def finish(self):
        if self.header is not None or self.buffer or self.final is None:
            raise IsolationError('missing or truncated isolated terminal record')
        return self.final


def worker_first(facts, environment):
    return {key: facts[key] for key in ('frame', 'window', 'segment', 'content_version', 'input_fingerprint',
                                        'vpts', 'width', 'height', 'bytes', 'sha256')} | {
        'fps': {'num': facts['fps'].numerator, 'den': facts['fps'].denominator},
        'base64': base64.b64encode(facts['png']).decode('ascii'), 'environment': environment,
    }


def _canonical_png(data):
    # All chunks/CRCs and pixels have already passed the full PNG gate. Only
    # RGB8 image data is authorized, not the encoder's ancillary metadata.
    if data[24:29] != b'\x08\x02\x00\x00\x00':
        raise IsolationError('first frame must be noninterlaced RGB8 PNG')
    chunks, offset = [data[:8]], 8
    while offset < len(data):
        length, tag = struct.unpack_from('>I4s', data, offset)
        end = offset + 12 + length
        if tag in (b'IHDR', b'IDAT', b'IEND'):
            chunks.append(data[offset:end])
        offset = end
    return b''.join(chunks)


def verify_first(record, *, start, end, segment_end, rate, version, fingerprint, limit):
    from ..render.gfx_cli import _png_stream_invalid_reason

    expected = {'frame': start, 'window': [start, end], 'segment': [start, segment_end],
                'fps': {'num': rate.numerator, 'den': rate.denominator}, 'content_version': version,
                'input_fingerprint': fingerprint, 'vpts': 0, 'width': 1920, 'height': 1080}
    image_fields = {'base64', 'bytes', 'sha256', 'environment'}
    # JSON spellings enforce exact recursive numeric types (True != 1 here).
    if (record.keys() != expected.keys() | image_fields
            or any(json.dumps(record[key], sort_keys=True) != json.dumps(value, sort_keys=True)
                   for key, value in expected.items())
            or not isinstance(record['environment'], str)
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', record['environment'])
            or type(record['bytes']) is not int or not 0 < record['bytes'] < limit
            or not isinstance(record['base64'], str)
            or len(record['base64']) != 4 * ((record['bytes'] + 2) // 3)):
        raise IsolationError('unexpected first-frame identity, position or size')
    data = base64.b64decode(record['base64'], validate=True)
    if (len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']
            or _png_stream_invalid_reason(io.BytesIO(data), len(data), width=1920, height=1080)):
        raise IsolationError('first-frame PNG does not match the bounded image proof')
    data = _canonical_png(data)
    return {'schema': ABI, 'provisional': True, 'mime': 'image/png',
            **{key: expected[key] for key in ('frame', 'window', 'segment', 'fps', 'width', 'height', 'content_version')},
            'byte_length': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'base64': base64.b64encode(data).decode('ascii')}


_WINDOW_FIELDS = {'schema', 'ok', 'content_version', 'start', 'end', 'fps', 'width', 'height',
                  'audio_sample_rate', 'audio_channels', 'mime', 'byte_length', 'sha256', 'base64', '_input_environment'}


def verify_final(result, first, environment):
    # Existing settled result/epoch remains authoritative. For this new wire,
    # additionally require it to be the exact same request, with no extra fields.
    if (first is None or result.keys() != _WINDOW_FIELDS or result['_input_environment'] != environment
            or result['content_version'] != first['content_version']
            or (result['start'], result['end']) != tuple(first['window']) or result['fps'] != first['fps']
            or (result['width'], result['height']) != (first['width'], first['height'])):
        raise IsolationError('isolated final window does not match the provisional first frame')
