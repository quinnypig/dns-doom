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

class AccessTests(unittest.TestCase):
    def setUp(self):
        import threading
        from unittest.mock import patch
        self.patch=patch.object(demo,'ACCESS_TOKEN','new-interactive-key')
        self.patch.start()
        self.server=demo.ThreadingHTTPServer(('127.0.0.1',0),demo.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.patch.stop()

    def request(self,path,method='GET',cookie=None):
        import http.client
        conn=http.client.HTTPConnection(*self.server.server_address)
        headers={'Cookie':cookie} if cookie else {}
        conn.request(method,path,headers=headers)
        response=conn.getresponse()
        result=(response.status,dict(response.getheaders()),response.read())
        conn.close()
        return result

    def test_anonymous_can_view_but_cannot_run_or_stop(self):
        self.assertEqual(self.request('/')[0],200)
        code,_,body=self.request('/api/status')
        self.assertEqual(code,200)
        self.assertFalse(json.loads(body)['can_control'])
        for path in ('/api/run','/api/stop'):
            self.assertEqual(self.request(path,'POST')[0],403)

    def test_old_link_silently_redirects_and_clears_cookie(self):
        code,headers,_=self.request('/?access=old-key',cookie='dnsdemo=old-key')
        self.assertEqual(code,303)
        self.assertEqual(headers['Location'],'/')
        self.assertIn('Max-Age=0',headers['Set-Cookie'])
        self.assertEqual(self.request('/')[0],200)
        self.assertEqual(self.request('/api/stop','POST','dnsdemo=old-key')[0],403)

    def test_new_key_grants_control_without_leaking_into_status(self):
        code,headers,_=self.request('/?access=new-interactive-key')
        self.assertEqual(code,303)
        cookie=headers['Set-Cookie'].split(';')[0]
        code,_,body=self.request('/api/status',cookie=cookie)
        self.assertTrue(json.loads(body)['can_control'])
        self.assertNotIn(b'new-interactive-key',body)
        from unittest.mock import patch
        with patch.object(demo,'job',None):
            self.assertEqual(self.request('/api/stop','POST',cookie)[0],409)

    def test_missing_configured_key_does_not_enable_anonymous_controls(self):
        from unittest.mock import patch
        with patch.object(demo,'ACCESS_TOKEN',''):
            self.assertEqual(self.request('/api/run','POST')[0],403)
