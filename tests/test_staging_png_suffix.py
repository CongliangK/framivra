"""Fixed staging extensions preserve transaction behavior on both platform paths."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from vtx import fstxn
from vtx.infra import ProjectPathError


class StagingSuffix(unittest.TestCase):
    def test_default_and_png_native_and_legacy(self):
        for legacy in (False,True):
            for suffix in (None,'.png'):
                with self.subTest(legacy=legacy,suffix=suffix),tempfile.TemporaryDirectory() as d:
                    root=Path(d);fstxn.bind_project_root(root);target=root/'frame.png'
                    with patch.object(fstxn,'_FD_WRITE_SUPPORT',not legacy):
                        with fstxn.locked_staging(target,**({} if suffix is None else {'suffix':suffix})) as temporary:
                            self.assertEqual(temporary.parent,target.parent)
                            self.assertEqual(temporary.suffix,suffix or '.tmp')
                            temporary.write_bytes(b'complete')
                            self.assertFalse(target.exists())
                    self.assertEqual(target.read_bytes(),b'complete')
                    self.assertFalse(temporary.exists())

    def test_abort_retains_target_and_cleans_png(self):
        for legacy in (False,True):
            with self.subTest(legacy=legacy),tempfile.TemporaryDirectory() as d:
                root=Path(d);fstxn.bind_project_root(root);target=root/'frame.png';target.write_bytes(b'old')
                with patch.object(fstxn,'_FD_WRITE_SUPPORT',not legacy),self.assertRaisesRegex(RuntimeError,'abort'):
                    with fstxn.locked_staging(target,suffix='.png') as temporary:
                        temporary.write_bytes(b'partial');raise RuntimeError('abort')
                self.assertEqual(target.read_bytes(),b'old');self.assertFalse(temporary.exists())

    def test_suffix_paths_rejected_before_any_creation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);fstxn.bind_project_root(root)
            for suffix in ('../victim','/escape','.png/escape','.jpeg',''):
                with self.subTest(suffix=suffix),self.assertRaises(ValueError):
                    with fstxn.locked_staging(root/'frame.png',suffix=suffix):pass
            self.assertEqual(list(root.iterdir()),[])

    @unittest.skipUnless(fstxn._probe_fd_writes(),'requires fd-anchored platform')
    def test_png_inode_exchange_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);fstxn.bind_project_root(root);target=root/'frame.png';target.write_bytes(b'old')
            with self.assertRaisesRegex(ProjectPathError, "临时文件身份已变化"):
                with fstxn.locked_staging(target,suffix='.png') as temporary:
                    other=root/'replacement';other.write_bytes(b'attacker');os.replace(other,temporary)
            self.assertEqual(target.read_bytes(),b'old');self.assertFalse(temporary.exists())
