#!/usr/bin/env python3
"""aup3_extract.py — 解析 Audacity 3.x .aup3 工程（SQLite）并提取音轨为 WAV。

格式依据（Audacity au3/libraries/au3-project-file-io/ProjectSerializer.cpp）：
  dict blob: [FT_CharSize=0][char_width] + 重复{ FT_Name=15, u16 id, u16 bytelen, 原生宽字符字节 }
  doc  blob: token 流，字段一律小端定宽：
    1 StartTag: u16 name_id          2 EndTag: u16 name_id
    3 String: u16 id, i32 bytelen, bytes
    4 Int: u16 id, i32               5 Bool: u16 id, u8
    6 Long: u16 id, i32              7 LongLong: u16 id, i64
    8 SizeT: u16 id, u32             9 Float: u16 id, f32, i32 digits
    10 Double: u16 id, f64, i32 digits
    11 Data: i32 len, bytes          12 Raw: i32 len, bytes
    13 Push / 14 Pop                 16 Blob: u16 id, i32 len, bytes
采样数据在 sampleblocks 表（blockid 主键，samples blob = 原生 PCM）。
"""
import argparse
import json
import os
import sqlite3
import struct
import sys

FT = {0: 'CharSize', 1: 'StartTag', 2: 'EndTag', 3: 'String', 4: 'Int', 5: 'Bool',
      6: 'Long', 7: 'LongLong', 8: 'SizeT', 9: 'Float', 10: 'Double', 11: 'Data',
      12: 'Raw', 13: 'Push', 14: 'Pop', 15: 'Name', 16: 'Blob'}


class R:
    def __init__(self, b):
        self.b, self.i = b, 0

    def u8(self):
        v = self.b[self.i]; self.i += 1; return v

    def u16(self):
        v = struct.unpack_from('<H', self.b, self.i)[0]; self.i += 2; return v

    def u32(self):
        v = struct.unpack_from('<I', self.b, self.i)[0]; self.i += 4; return v

    def i32(self):
        v = struct.unpack_from('<i', self.b, self.i)[0]; self.i += 4; return v

    def i64(self):
        v = struct.unpack_from('<q', self.b, self.i)[0]; self.i += 8; return v

    def f32(self):
        v = struct.unpack_from('<f', self.b, self.i)[0]; self.i += 4; return v

    def f64(self):
        v = struct.unpack_from('<d', self.b, self.i)[0]; self.i += 8; return v

    def raw(self, n):
        v = self.b[self.i:self.i + n]; self.i += n; return v


def parse_dict(blob):
    r = R(blob)
    t, charw = r.u8(), r.u8()
    assert t == 0, 'dict must start with FT_CharSize'
    codec = {4: 'utf-32-le', 2: 'utf-16-le', 1: 'utf-8'}[charw]
    names = {}
    while r.i < len(blob):
        assert r.u8() == 15, f'FT_Name expected at {r.i}'
        did, ln = r.u16(), r.u16()
        names[did] = r.raw(ln).decode(codec)
    return names


def parse_doc(blob, names, charw):
    codec = {4: 'utf-32-le', 2: 'utf-16-le', 1: 'utf-8'}[charw]
    r = R(blob)
    events = []
    while r.i < len(blob):
        t = r.u8()
        if t == 1:
            events.append(('start', names.get(r.u16())))
        elif t == 2:
            events.append(('end', names.get(r.u16())))
        elif t in (13, 14):
            events.append((FT[t],))
        elif t in (11, 12):
            ln = r.i32()
            events.append((FT[t], r.raw(ln).decode(codec, 'replace')))
        elif t == 3:
            nid, ln = r.u16(), r.i32()
            events.append(('attr', names.get(nid, nid), r.raw(ln).decode(codec, 'replace')))
        elif t in (4, 6):
            nid = r.u16()
            events.append(('attr', names.get(nid, nid), str(r.i32())))
        elif t == 7:
            nid = r.u16()
            events.append(('attr', names.get(nid, nid), str(r.i64())))
        elif t == 8:
            nid = r.u16()
            events.append(('attr', names.get(nid, nid), str(r.u32())))
        elif t == 5:
            nid = r.u16()
            events.append(('attr', names.get(nid, nid), bool(r.u8())))
        elif t in (9, 10):
            nid = r.u16()
            val = r.f32() if t == 9 else r.f64()
            r.i32()  # digits
            events.append(('attr', names.get(nid, nid), val))
        elif t == 16:
            nid, ln = r.u16(), r.i32()
            events.append(('blob', names.get(nid, nid), r.raw(ln)))
        else:
            raise ValueError(f'unknown token {t} (0x{t:02x}) at offset {r.i - 1}')
    return events


def build_tree(events):
    """start/end/attr 事件 → 嵌套节点树 {tag, attrs, children}"""
    root = {'tag': '#root', 'attrs': {}, 'children': []}
    stack = [root]
    for ev in events:
        if ev[0] == 'start':
            node = {'tag': ev[1], 'attrs': {}, 'children': []}
            stack[-1]['children'].append(node)
            stack.append(node)
        elif ev[0] == 'end':
            stack.pop()
        elif ev[0] == 'attr':
            stack[-1]['attrs'][ev[1]] = ev[2]
    assert len(stack) == 1, 'unbalanced tags'
    return root


def findall(node, tag, out=None):
    if out is None:
        out = []
    for c in node['children']:
        if c['tag'] == tag:
            out.append(c)
        findall(c, tag, out)
    return out


def write_wav_float32(path, samples_f32, rate):
    """单声道 float32 WAV（WAVE_FORMAT_IEEE_FLOAT, tag=3），零第三方依赖。"""
    data = samples_f32.astype('<f4').tobytes()
    with open(path, 'wb') as f:
        f.write(b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVE')
        f.write(b'fmt ' + struct.pack('<IHHIIHH', 16, 3, 1, rate, rate * 4, 4, 32))
        f.write(b'data' + struct.pack('<I', len(data)))
        f.write(data)


def extract_all(aup3_path, out_dir):
    """轨道 → WAV；未被引用的 sampleblocks → orphans/ WAV。返回报告 dict。"""
    import numpy as np
    con = sqlite3.connect(f'file:{aup3_path}?mode=ro', uri=True)
    dictb, docb = con.execute('SELECT dict, doc FROM project WHERE id=1').fetchone()
    names = parse_dict(dictb)
    root = build_tree(parse_doc(docb, names, dictb[1]))
    blobs = {bid: samples for bid, samples in
             con.execute('SELECT blockid, samples FROM sampleblocks')}
    report = {'tracks': [], 'orphans': []}
    used = set()
    for ti, wt in enumerate(findall(root, 'wavetrack'), 1):
        rate = int(float(wt['attrs'].get('rate', 44100)))
        tname = wt['attrs'].get('name', f'track{ti}')
        for ci, wc in enumerate(findall(wt, 'waveclip'), 1):
            parts = []
            for seq in findall(wc, 'sequence'):
                for wb in sorted(findall(seq, 'waveblock'),
                                 key=lambda n: int(float(n['attrs'].get('start', 0)))):
                    bid = int(wb['attrs']['blockid'])
                    used.add(bid)
                    parts.append(np.frombuffer(blobs[bid], dtype='<f4'))
            if not parts:
                continue
            audio = np.concatenate(parts)
            fname = f"track{ti:02d}_{tname.replace('/', '_')}" + (f".clip{ci}" if ci > 1 else '') + '.wav'
            path = os.path.join(out_dir, fname)
            write_wav_float32(path, audio, rate)
            report['tracks'].append({
                'file': fname, 'track': tname, 'clip': wc['attrs'].get('name'),
                'timeline_offset_s': wc['attrs'].get('offset'),
                'rate': rate, 'samples': int(audio.size),
                'duration_s': round(audio.size / rate, 3),
                'peak': round(float(np.abs(audio).max()), 4),
                'rms': round(float(np.sqrt(np.mean(audio ** 2))), 5),
            })
    os.makedirs(os.path.join(out_dir, 'orphans'), exist_ok=True)
    for bid in sorted(set(blobs) - used):
        audio = np.frombuffer(blobs[bid], dtype='<f4')
        path = os.path.join(out_dir, 'orphans', f'orphan_block{bid:02d}.wav')
        write_wav_float32(path, audio, 44100)
        report['orphans'].append({
            'blockid': bid, 'file': f'orphans/orphan_block{bid:02d}.wav',
            'samples': int(audio.size), 'duration_s': round(audio.size / 44100, 3),
            'peak': round(float(np.abs(audio).max()), 4),
            'rms': round(float(np.sqrt(np.mean(audio ** 2))), 5),
        })
    with open(os.path.join(out_dir, 'extract_report.json'), 'w') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return report


def main(aup3_path, out_dir, dump=False):
    os.makedirs(out_dir, exist_ok=True)
    con = sqlite3.connect(f'file:{aup3_path}?mode=ro', uri=True)
    dictb, docb = con.execute('SELECT dict, doc FROM project WHERE id=1').fetchone()
    names = parse_dict(dictb)
    charw = dictb[1]
    events = parse_doc(docb, names, charw)
    if dump:
        with open(os.path.join(out_dir, 'doc_events.txt'), 'w') as f:
            for ev in events:
                f.write(repr(ev) + '\n')
    root = build_tree(events)

    info = {'tracks': []}
    for wt in findall(root, 'wavetrack'):
        wt_info = {
            'name': wt['attrs'].get('name'),
            'rate': wt['attrs'].get('rate'),
            'gain': wt['attrs'].get('gain'),
            'pan': wt['attrs'].get('pan'),
            'channel': wt['attrs'].get('channel'),
            'linked': wt['attrs'].get('linked'),
            'sampleformat': wt['attrs'].get('sampleformat'),
            'clips': [],
        }
        for wc in findall(wt, 'waveclip'):
            clip = {'attrs': dict(wc['attrs']), 'sequences': []}
            for seq in findall(wc, 'sequence'):
                seqd = {'attrs': dict(seq['attrs']), 'blocks': []}
                for bf in findall(seq, 'blockfile'):
                    seqd['blocks'].append(dict(bf['attrs']))
                clip['sequences'].append(seqd)
            wt_info['clips'].append(clip)
        info['tracks'].append(wt_info)

    # sampleblocks 全量取出备用
    blocks = {}
    for bid, fmt, smin, smax, srms, s256, s64k, samples in con.execute(
            'SELECT blockid, sampleformat, summin, summax, sumrms, summary256, summary64k, samples FROM sampleblocks'):
        blocks[bid] = {'fmt': fmt, 'summin': smin, 'summax': smax, 'sumrms': srms,
                       'nbytes': len(samples), 'samples': samples}
    info['block_table'] = {k: {kk: vv for kk, vv in v.items() if kk != 'samples'} for k, v in blocks.items()}
    with open(os.path.join(out_dir, 'structure.json'), 'w') as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps(info, ensure_ascii=False, indent=1)[:4000])
    print(f"\nblocks in table: {len(blocks)}, events: {len(events)}, wrote structure.json")
    if dump:
        print('wrote doc_events.txt')
    print('\n===== EXTRACT =====')
    print(json.dumps(extract_all(aup3_path, out_dir), ensure_ascii=False, indent=1))


def build_arg_parser():
    """统一参数面（A4）：位置参数顺序与旧版 sys.argv 手工解析完全一致，
    --dump 语义不变；--help/-h 由 argparse 提供中文描述。"""
    ap = argparse.ArgumentParser(
        prog='aup3_extract.py',
        description='解析 Audacity 3.x .aup3 工程（SQLite）并提取音轨为 WAV',
        epilog='输出：逐轨 float32 WAV + orphans/（未被引用块）+ structure.json + extract_report.json')
    ap.add_argument('aup3', help='.aup3 工程文件路径（Audacity 3.x 单文件工程，不支持旧版 .aup XML）')
    ap.add_argument('out_dir', nargs='?', default='.', help='输出目录（默认当前目录）')
    ap.add_argument('--dump', action='store_true', help='额外导出 doc token 事件流到 <out_dir>/doc_events.txt')
    return ap


if __name__ == '__main__':
    _args = build_arg_parser().parse_args()
    try:
        main(_args.aup3, _args.out_dir, dump=_args.dump)
    except sqlite3.DatabaseError as e:  # 文件不存在(mode=ro)/非 SQLite/缺 project 表等
        sys.exit(f'aup3_extract: 无法读取工程 {_args.aup3}：{e}\n'
                 '（确认是 Audacity 3.x 的 .aup3 文件且未损坏）')
    except OSError as e:  # 输出目录不可写等文件系统错误
        sys.exit(f'aup3_extract: 文件/目录访问失败：{e}')
