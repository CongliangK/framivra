"""Measure current WAV; validate before replacing and invalidate consumers."""
import argparse, hashlib, json, math, os, re, subprocess, sys, tempfile, wave
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vtx'))
from cache_state import DOWNSTREAM

def run(args):
    r = subprocess.run(list(map(str, args)), capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode:
        raise RuntimeError(r.stderr[-3000:])
    return r.stderr

def measure(ffmpeg, path):
    log = run([ffmpeg, '-hide_banner', '-nostats', '-i', path, '-af', 'ebur128=peak=true', '-f', 'null', '-'])
    values = re.findall(r'I:\s+(-?[\d.]+|[-+]?inf) LUFS', log)
    if not values or not math.isfinite(float(values[-1])) or float(values[-1]) <= -70:
        raise ValueError('Cannot normalize silent or unmeasurable audio')
    return float(values[-1])

def duration(path):
    with wave.open(str(path), 'rb') as audio:
        return audio.getnframes() / audio.getframerate()

def atomic_json(path, data):
    fd, name = tempfile.mkstemp(prefix='.post-receipt-', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

def process(project, ffmpeg='ffmpeg', target=-14):
    if not math.isfinite(target):
        raise ValueError('Target must be finite')
    source = Path(project).resolve() / 'out' / 'audio.wav'
    receipt = source.parent / 'receipt.json'
    before = measure(ffmpeg, source)  # raw_i is before stages.audio applies gain_db
    seconds = duration(source)
    gain = target - before
    fd, name = tempfile.mkstemp(prefix='.post-audio-', suffix='.wav', dir=source.parent)
    os.close(fd)
    candidate = Path(name)
    try:
        run([ffmpeg, '-y', '-v', 'error', '-i', source, '-af', f'volume={gain:.3f}dB,alimiter=limit=0.599:level=false:latency=true', '-c:a', 'pcm_s16le', '-ar', '48000', candidate])
        after = measure(ffmpeg, candidate)
        if abs(after - target) > 1.5:
            raise ValueError(f'Loudness {after} outside target {target} +/- 1.5 LU')
        if abs(duration(candidate) - seconds) > 1 / 48000 + 1e-9:
            raise ValueError('Post-processing changed audio duration')
        result = {'input_i': before, 'output_i': after, 'gain_db': gain, 'target': target, 'sha256': hashlib.sha256(candidate.read_bytes()).hexdigest()}
        record = json.loads(receipt.read_text(encoding='utf-8')) if receipt.exists() else None
        audio_stage = None
        if record is not None:
            stages = record['stages']
            audio_stage = stages.pop('audio', None)
            for stage in DOWNSTREAM['audio']:
                stages.pop(stage, None)
            atomic_json(receipt, record)  # interruption cannot leave stale PASS
        os.replace(candidate, source)
        if record is not None and audio_stage is not None:
            audio_stage['post_loudness'] = result
            record['stages']['audio'] = audio_stage
            atomic_json(receipt, record)
        return result
    finally:
        candidate.unlink(missing_ok=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', nargs='?', default='.')
    parser.add_argument('--ffmpeg', default=os.environ.get('FFMPEG', 'ffmpeg'))
    parser.add_argument('--target', type=float, default=float(os.environ.get('TARGET_LUFS', '-14')))
    args = parser.parse_args()
    try:
        print(json.dumps(process(args.project, args.ffmpeg, args.target), ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, KeyError, wave.Error) as exc:
        print(f'FATAL: {exc}', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
