import json, os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'vtx'))
from infra import Ctx
from cache_state import project_fingerprint

class CacheIdentity(unittest.TestCase):
    def test_content_not_timestamp_and_declared_dependencies(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);src=root/'a';src.write_bytes(b'AAAA');css=root/'style.css';css.write_bytes(b'1111')
            tl={'project':{'cache_dependencies':['style.css']},'sources':[{'path':'a'}],'video_tracks':[]}
            before=project_fingerprint(root,tl);stamp=src.stat();src.write_bytes(b'BBBB');os.utime(src,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            self.assertNotEqual(before,project_fingerprint(root,tl))
            before=project_fingerprint(root,tl);css.write_bytes(b'2222');self.assertNotEqual(before,project_fingerprint(root,tl))
            tl['project']['cache_dependencies']=['missing'];self.assertRaises(ValueError,project_fingerprint,root,tl)
    def test_receipt_invalidation_and_graphics_identity(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'timeline.json'
            tl={'project':{'fps':30,'duration_frames':180,'width':1920,'height':1080},'sources':[],'video_tracks':[]}
            p.write_text(json.dumps(tl));c=Ctx(p)
            self.assertNotEqual(c.gfx_dir({'src':'a/same.html','dur':30}),c.gfx_dir({'src':'b/same.html','dur':30}))
            self.assertNotEqual(c.gfx_dir({'src':'a/same.html','dur':30}),c.gfx_dir({'src':'a/same.html','dur':60}))
            for stage in ['prepare','audio','video','mux','qc']:c.receipt(stage,True)
            c.begin_stage('prepare');self.assertRaises(RuntimeError,c.begin_stage,'qc')
            for stage in ['prepare','audio','video','mux','qc']:c.receipt(stage,True)
            tl['subtitles']=[{'in':0,'out':30,'text':'new'}];p.write_text(json.dumps(tl));new=Ctx(p)
            self.assertNotEqual(c.cache,new.cache);self.assertRaises(RuntimeError,new.begin_stage,'qc')
            new.receipt('validate',True);rec=json.loads((root/'out/receipt.json').read_text());self.assertEqual(list(rec['stages']),['validate'])

if __name__=='__main__':unittest.main()
