import json
from pathlib import Path
import tempfile
import unittest
from dns32 import demo

class RecoveryTests(unittest.TestCase):
    def test_interrupted_run_is_not_claimed_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            item={'id':'test','phase':'starting','output':out,'process':object(),'thread':object()}
            demo.save_job(item)
            result=demo.restore_job(out)
            self.assertEqual(result['phase'],'failed')
            self.assertIn('restarted',result['error'])
            persisted=json.loads((out/'job.json').read_text())
            self.assertNotIn('process',persisted)
            self.assertEqual(persisted['phase'],'failed')

    def test_completed_run_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            demo.save_job({'id':'test','phase':'complete','exit_code':0,'output':out})
            self.assertEqual(demo.restore_job(out)['phase'],'complete')

    def test_stop_prevents_new_job_until_cleanup_finishes(self):
        from unittest.mock import Mock,patch
        with patch.object(demo,'job',{'phase':'stopped','thread':Mock(is_alive=lambda:True)}):
            with self.assertRaisesRegex(ValueError,'stopping'):
                demo.start('fibonacci','bind',True)
