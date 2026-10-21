"""Current timeline preview loading and UI routing; real JavaScript, no browser claims."""
import json
import re
import subprocess
import unittest
from pathlib import Path

PREVIEW_HTML = (Path(__file__).resolve().parents[2] / 'vtx/preview.html').read_text()


def _constant(name):
    return json.loads(re.search(r'const ' + name + r' = (.*);', PREVIEW_HTML).group(1))


class PreviewContract(unittest.TestCase):
    def test_preview_loads_only_v2_without_mutating_current_contract(self):
        loader = PREVIEW_HTML[PREVIEW_HTML.index("function load("):
                              PREVIEW_HTML.index("function validate(")]
        validators = PREVIEW_HTML[PREVIEW_HTML.index("function validate("):
                                  PREVIEW_HTML.index("function render(")]
        script = """
const assert = require('node:assert/strict');
const CONSTRAINTS = %s, SAMPLE = %s;
let TL = null, renders = 0, fits = 0;
const nodes = {}, handlers = {}, HINT = 'details', alerts = [];
const $ = id => nodes[id] ||= {value: '', innerHTML: '', click() {this.clicked = true;},
  showModal() {this.open = true;}, close() {this.open = false;}};
const document = {addEventListener: (event, fn) => handlers[event] = fn};
const alert = message => alerts.push(message);
const render = () => renders++, fit = () => fits++;
%s
%s
(async () => {
const current = JSON.parse(JSON.stringify(SAMPLE));
for (const track of current.video_tracks)
  for (const clip of track.clips)
    for (const key of ['scale', 'x', 'y', 'opacity', 'fade_in_frames', 'fade_out_frames'])
      delete clip[key];
const before = JSON.stringify(current);
load(current);
assert.equal(TL, current);
assert.equal(JSON.stringify(current), before);
assert.equal(validate(current).filter(issue => issue.lv === 'err').length, 0);
assert.equal(durF(current), current.project.duration_frames);
for (const old of [null, [], 'text', {}, {version: 1, overlays: [{src: 'old.html', in: 0, out: 30}]},
                   {version: 3}, {version: '2'}]) {
  assert.throws(() => load(old), /version: 2/);
  loadText(JSON.stringify(old));
  assert.match(alerts.at(-1), /version: 2/);
  assert.equal(TL, current);
}
loadText('{bad json');
assert.match(alerts.at(-1), /时间线载入失败/);
assert.equal(TL, current);
assert.equal(renders, 1);
assert.equal(fits, 1);
loadText(JSON.stringify(SAMPLE));
assert.deepEqual(TL, SAMPLE);
assert.equal(renders, 2);
assert.equal(fits, 2);
$("bOpen").onclick(); assert.equal($("file").clicked, true);
$("bPaste").onclick(); assert.equal($("dlg").open, true);
$("paste").value = JSON.stringify({version: 1});
$("bLoad").onclick(); assert.equal($("dlg").open, false);
assert.deepEqual(TL, SAMPLE); assert.match(alerts.at(-1), /version: 2/);
const edited = {...SAMPLE, _meta: {note: 'current input preserved'}};
$("paste").value = JSON.stringify(edited); $("bLoad").onclick();
assert.deepEqual(TL, edited);
$("bSample").onclick(); assert.deepEqual(TL, SAMPLE);
$("file").onchange({target: {files: [{text: async () => JSON.stringify(edited)}]}});
await Promise.resolve(); assert.deepEqual(TL, edited);
let prevented = false;
handlers.drop({preventDefault() {prevented = true;},
  dataTransfer: {files: [{text: async () => JSON.stringify(SAMPLE)}]}});
await Promise.resolve(); assert.equal(prevented, true); assert.deepEqual(TL, SAMPLE);
for (const button of ["bCancel", "bX"]) {
  $("bPaste").onclick(); $(button).onclick(); assert.equal($("dlg").open, false);
}
})().catch(error => {console.error(error); process.exitCode = 1;});
""" % (json.dumps(_constant("CONSTRAINTS")), json.dumps(_constant("SAMPLE")),
       loader, validators)
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
