#!/usr/bin/env python3
"""Opt-in hosted Issue85 experiment. No network or media work on import.

Only the exact publisher object below is eligible. Exit 2 means BLOCKED, never
acceptance. App-cache cold is not OS-cache cold. Original no-reread remains unmet.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import time
import urllib.request

URL = 'https://svs.gsfc.nasa.gov/vis/a020000/a020300/a020374/XRISM_S1_4k_30fps_ProRes.mov'
PROVENANCE = {
    'object': URL, 'publisher': 'https://svs.gsfc.nasa.gov/20374/',
    'rights': ['https://svs.gsfc.nasa.gov/help/',
               'https://www.nasa.gov/nasa-brand-center/images-and-media/'],
    'credit': "NASA's Goddard Space Flight Center Conceptual Image Lab",
    'use': 'Engineering fixture; no NASA endorsement; no third-party music authorized',
}
MIN_BYTES, MAX_BYTES = 1_000_000_000, 1_300_000_000
META_CAP = 8 * 1024 * 1024
BUDGET_ROOT = None
COMMAND_REPORTS = []


class Blocked(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Blocked('Unexpected redirect: ' + newurl)


def request(method='GET', headers=None):
    return urllib.request.build_opener(NoRedirect()).open(
        urllib.request.Request(URL, method=method, headers=headers or {}), timeout=30)


def head():
    with request('HEAD') as response:
        headers = dict(response.headers.items())
        size = int(response.headers.get('Content-Length', '0'))
        if response.status != 200 or not MIN_BYTES <= size <= MAX_BYTES:
            raise Blocked('Original size/status outside qualification budget')
        if 'video/' not in response.headers.get('Content-Type', ''):
            raise Blocked('Unexpected media Content-Type')
        validator = response.headers.get('ETag')
        return {'bytes': size, 'etag': validator if validator and not validator.startswith('W/') else None, 'headers': headers,
                'final_url': response.url, 'time_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}


class Ranges:
    def __init__(self, info):
        self.info, self.total = info, 0
        self.deadline = time.monotonic() + 180

    def get(self, start, length):
        if length <= 0 or start < 0 or start + length > self.info['bytes']:
            raise Blocked('Invalid atom range')
        if time.monotonic() > self.deadline:
            raise Blocked('Metadata qualification time budget exceeded')
        if self.total + length + 1 > META_CAP:
            raise Blocked('MOV metadata exceeds 8 MiB cap')
        headers = {'Range': f'bytes={start}-{start+length-1}'}
        if self.info['etag']:
            headers['If-Match'] = self.info['etag']
        with request(headers=headers) as response:
            expected = f"bytes {start}-{start+length-1}/{self.info['bytes']}"
            if response.status != 206 or response.headers.get('Content-Range') != expected:
                raise Blocked('Server did not honor bounded range')
            if self.info['etag'] and response.headers.get('ETag') != self.info['etag']:
                raise Blocked('Object changed during qualification')
            data = response.read(length + 1)
        self.total += len(data)
        if len(data) != length:
            raise Blocked('Incorrect range length')
        return data


def metadata_file(info, path, fetch):
    """Keep atom offsets; holes are never evidence of payload/integrity or size."""
    offset, found, atoms = 0, set(), []
    with path.open('xb') as output:
        output.truncate(info['bytes'])
        while offset < info['bytes']:
            if len(atoms) >= 4096:
                raise Blocked('Excessive top-level atoms')
            header = fetch(offset, min(16, info['bytes'] - offset))
            if len(header) < 8:
                raise Blocked('Truncated MOV atom')
            size, kind = struct.unpack('>I4s', header[:8])
            if size == 1:
                if len(header) < 16:
                    raise Blocked('Truncated extended atom')
                size = struct.unpack('>Q', header[8:16])[0]
            elif size == 0:
                size = info['bytes'] - offset
            if size < (16 if header[:4] == b'\0\0\0\1' else 8) or offset + size > info['bytes']:
                raise Blocked('Invalid MOV atom size')
            atoms.append({'offset': offset, 'bytes': size, 'type': kind.decode('ascii', 'replace')})
            output.seek(offset)
            output.write(header[:min(size, len(header))])
            if kind in (b'ftyp', b'moov'):
                output.seek(offset)
                output.write(fetch(offset, size))
                found.add(kind)
            offset += size
    if not {b'ftyp', b'moov'} <= found:
        raise Blocked('Missing MOV metadata atoms')
    return atoms


def command(args, timeout=180):
    if BUDGET_ROOT is not None:
        from issue85_media_runner import monitored_run
        log = BUDGET_ROOT / f'ingest-{len(COMMAND_REPORTS):03d}.log'
        env = {'PATH':os.environ.get('PATH', os.defpath), 'LC_ALL':'C.UTF-8',
               'HOME':str(BUDGET_ROOT / 'home')}
        result = monitored_run(args, BUDGET_ROOT, env, log, timeout, BUDGET_ROOT)
        COMMAND_REPORTS.append(result)
        if result['exit_code'] != 0 or result['timed_out'] or result['budget_exceeded']:
            raise Blocked(f'Ingestion command failed or exceeded budget: {result}')
        return log.read_text()
    # Pure helper use in tiny tests does not activate the hosted resource runner.
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=timeout)
    if result.returncode:
        raise Blocked(f'Command failed: {args[0]}: {result.stderr[-2000:]}')
    return result.stdout


def probe(path):
    return json.loads(command(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                               '-of', 'json', str(path)]))


def qualify(meta):
    videos = [s for s in meta['streams'] if s.get('codec_type') == 'video']
    audio = [s for s in meta['streams'] if s.get('codec_type') == 'audio']
    if len(videos) != 1 or audio:
        raise Blocked('Expected one video and no audio; unexpected audio requires rights review')
    v = videos[0]
    if v.get('codec_name') != 'prores':
        raise Blocked('Publisher ProRes codec mismatch')
    if (v.get('width'), v.get('height')) != (3840, 2160):
        raise Blocked('Publisher dimension mismatch')
    if v.get('r_frame_rate') != '30/1' or v.get('avg_frame_rate') != '30/1':
        raise Blocked('Native exact 30 fps not established')
    duration = float(v.get('duration', 'nan'))
    if not math.isfinite(duration) or not 20 <= duration <= 60:
        raise Blocked('Known video duration must be 20–60 seconds for this fixed experiment')
    frames = int(v.get('nb_frames', '0'))
    if frames != round(duration * 30) or abs(frames / 30 - duration) > 0.001:
        raise Blocked('Video frame count/duration mismatch')
    return {'duration': duration, 'frames': frames, 'width':3840, 'height':2160,
            'fps':'30/1', 'audio_streams':0, 'codec':v.get('codec_name'), 'pixel_format':v.get('pix_fmt')}


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def acquire(info, path):
    started, count, digest = time.monotonic(), 0, hashlib.sha256()
    headers = {'If-Match':info['etag']} if info['etag'] else {}
    with request(headers=headers) as response, path.open('xb') as output:
        if response.status != 200 or (info['etag'] and response.headers.get('ETag') != info['etag']):
            raise Blocked('Object changed before acquisition')
        if int(response.headers.get('Content-Length', '0')) != info['bytes']:
            raise Blocked('Acquisition length changed')
        while True:
            block = response.read(min(1024 * 1024, info['bytes'] - count + 1))
            if not block:
                break
            count += len(block)
            if count > info['bytes'] or time.monotonic() - started > 1200:
                raise Blocked('Acquisition budget exceeded')
            output.write(block)
            digest.update(block)
    if count != info['bytes']:
        raise Blocked('Incomplete media acquisition')
    return {'seconds':time.monotonic()-started, 'bytes':count, 'sha256':digest.hexdigest()}


def normalize(original, target, qualified):
    started = time.monotonic()
    args = ['ffmpeg','-v','error','-nostdin','-i',str(original),'-f','lavfi','-i',
            'sine=frequency=997:sample_rate=48000', '-map','0:v:0','-map','1:a:0',
            '-c:v','copy','-af','volume=0.25','-ac','2','-c:a','pcm_s16le',
            '-t',str(qualified['duration']),str(target)]
    command(args, 300)
    hashes = []
    for path in (original, target):
        hashes.append(command(['ffmpeg','-v','error','-nostdin','-i',str(path),'-map','0:v:0',
                               '-c:v','copy','-f','hash','-hash','sha256','-'], 300).strip())
    if hashes[0] != hashes[1]:
        raise Blocked('Video packet payload changed by stream-copy')
    out = probe(target)
    v = next(s for s in out['streams'] if s['codec_type'] == 'video')
    if (v.get('nb_frames'), v.get('r_frame_rate'), v.get('avg_frame_rate'),v.get('width'),v.get('height')) != (
            str(qualified['frames']), '30/1', '30/1',3840,2160):
        raise Blocked('Normalized video metadata changed')
    if abs(float(v['duration']) - qualified['duration']) > .001:
        raise Blocked('Normalized video duration changed')
    target_sha = sha(target)
    return {'command':args, 'seconds_including_verification':time.monotonic()-started,
            'audio_origin':'FFmpeg generated 997 Hz sine, default 1/8 amplitude scaled 0.25, stereo 48 kHz PCM; test signal, not NASA audio',
            'video_payload_sha256':hashes[0], 'sha256':target_sha, 'bytes':target.stat().st_size,
            'probe':out}


def main():
    global BUDGET_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-hosted', action='store_true', required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    BUDGET_ROOT = args.output.resolve()
    (BUDGET_ROOT / 'home').mkdir()
    report = {'status':'BLOCKED', 'provenance':PROVENANCE, 'original_no_reread':'UNMET',
              'scope':'Declared runtime source identities only; not full dependency/reproducibility certification'}
    try:
        if shutil.disk_usage(args.output).free < 5 * 1024**3:
            raise Blocked('Need at least 5 GiB free scratch')
        for tool in ('ffmpeg','ffprobe'):
            report[tool] = command([tool,'-version'])
        qualification_start = time.monotonic()
        info = report['head'] = head()
        ranges = Ranges(info)
        sparse = args.output / 'metadata-only.mov'
        # Ranged metadata is only a preliminary screen, never final qualification.
        # HTTP access denials propagate; unsupported ranges/metadata can fall back
        # to the explicitly bounded full acquisition of this same official URL.
        try:
            report['atoms'] = metadata_file(info, sparse, ranges.get)
            report['metadata_probe'] = probe(sparse)
            report['preliminary_qualified'] = qualify(report['metadata_probe'])
        except (Blocked, ValueError, KeyError, subprocess.SubprocessError) as exc:
            report['preliminary_gap'] = str(exc)
        report['metadata_transfer_bytes'] = ranges.total
        report['qualification_seconds'] = time.monotonic() - qualification_start
        if sparse.exists():
            sparse.unlink()  # Only our own incomplete metadata scratch.
        original = args.output / 'original.mov'
        report['acquisition'] = acquire(info, original)
        report['original_probe'] = probe(original)
        qualified = report['qualified'] = qualify(report['original_probe'])
        if info['etag'] and report.get('preliminary_qualified', qualified) != qualified:
            raise Blocked('Strong-validator metadata differs after acquisition')
        normalized = args.output / 'normalized.mov'
        report['normalization'] = normalize(original, normalized, qualified)
        from issue85_media_runner import run_experiment
        report['experiment'] = run_experiment(args.baseline, args.candidate, normalized, args.output / 'runs')
        integrity_start = time.monotonic()
        report['post_experiment_normalized_sha256'] = sha(normalized)
        report['post_experiment_integrity_seconds'] = time.monotonic() - integrity_start
        if report['post_experiment_normalized_sha256'] != report['normalization']['sha256']:
            raise Blocked('Shared normalized source not restored to its original complete bytes')
        report['status'] = report['experiment']['status']
    except (RuntimeError, OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as exc:
        report['blocker'] = str(exc)
    finally:
        report['ingestion_process_measurements'] = COMMAND_REPORTS
        # Small reports only are published by the workflow; media remains ephemeral.
        (args.output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    return 0 if report['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
