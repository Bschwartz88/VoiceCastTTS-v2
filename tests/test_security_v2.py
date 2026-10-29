"""Security boundaries with real loopback HTTP and synthetic project-only fixtures."""
import http.client
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import web_server as ws
import ai_programs
import session_access

class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ws.StudioServer(('127.0.0.1', 0), ws.StudioHandler)
        cls.port = cls.server.server_port
        cls.origin = f'http://127.0.0.1:{cls.port}'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()
    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse(); data = response.read(); conn.close()
        return response, data
    def auth(self, **extra):
        return {'Authorization': 'Bearer ' + self.server.session_token, **extra}
    def test_bootstrap_does_not_disclose_session(self):
        r, data = self.request('GET', '/')
        self.assertEqual(r.status, 200)
        self.assertNotIn(self.server.session_token.encode(), data)
    def test_private_reads_need_session_even_with_forged_origin(self):
        for path in ['/api/config', '/api/lmstudio/chats', '/api/audio/latest.wav', '/output/anything.wav']:
            r, _ = self.request('GET', path, {'Origin': self.origin})
            self.assertEqual(r.status, 401, path)
        r, _ = self.request('GET', '/api/config', self.auth())
        self.assertEqual(r.status, 200)
    def test_invalid_and_other_session_tokens_are_refused(self):
        for token in ['wrong', 'x'*43, '\xe9'*43]:
            r, _ = self.request('GET', '/api/config', {'Authorization': 'Bearer '+token})
            self.assertEqual(r.status, 401)
    def test_cookie_exchange_and_cross_origin_preview(self):
        r, _ = self.request('POST', '/api/session', self.auth(Origin=self.origin))
        self.assertEqual(r.status, 204)
        cookie = r.getheader('Set-Cookie')
        self.assertIn('HttpOnly', cookie); self.assertIn('SameSite=Strict', cookie)
        cookie = cookie.split(';')[0]
        r, _ = self.request('GET', '/api/config', {'Cookie':cookie, 'Sec-Fetch-Site':'same-origin'})
        self.assertEqual(r.status, 200)
        with mock.patch.object(ws.voice_cast, 'generate_voice_preview') as generate:
            r, _ = self.request('GET', '/api/preview_voice?voice=am_eric', {'Cookie':cookie, 'Sec-Fetch-Site':'same-site'})
            self.assertEqual(r.status, 403); generate.assert_not_called()
    def test_unauthorized_headers_rejected_before_body(self):
        with socket.create_connection(('127.0.0.1',self.port),timeout=2) as client:
            client.sendall(f'POST /api/user_settings HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nOrigin: {self.origin}\r\nContent-Length: 1024\r\n\r\n'.encode())
            self.assertIn(b'401', client.recv(4096).split(b'\r\n')[0])
    def test_authenticated_slow_body_has_deadline(self):
        with mock.patch.object(ws,'REQUEST_TIMEOUT',0.15):
            with socket.create_connection(('127.0.0.1',self.port),timeout=2) as client:
                client.sendall(f'POST /api/user_settings HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nOrigin: {self.origin}\r\nAuthorization: Bearer {self.server.session_token}\r\nContent-Length: 10\r\n\r\n'.encode())
                self.assertIn(b'408', client.recv(4096).split(b'\r\n')[0])
    def test_framing_ambiguity_is_refused(self):
        with socket.create_connection(('127.0.0.1',self.port),timeout=2) as client:
            client.sendall(f'POST /api/user_settings HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nOrigin: {self.origin}\r\nAuthorization: Bearer {self.server.session_token}\r\nContent-Length: 0\r\nContent-Length: 1\r\n\r\n'.encode())
            self.assertIn(b'400', client.recv(4096).split(b'\r\n')[0])
    def test_output_and_resolved_containment(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'web/static').mkdir(parents=True); (root/'output').mkdir(); (root/'private').mkdir()
            (root/'output/diagnostic.txt').write_text('synthetic')
            (root/'output/test.wav').write_bytes(b'RIFFsynthetic')
            (root/'private/sentinel.txt').write_text('synthetic private')
            with mock.patch.object(ws,'WEB_DIR',str(root/'web')), mock.patch.object(ws,'OUTPUT_DIR',str(root/'output')):
                self.assertEqual(self.request('GET','/output/diagnostic.txt',self.auth())[0].status,403)
                self.assertEqual(self.request('GET','/output/test.wav',self.auth())[0].status,200)
                self.assertEqual(self.request('GET','/output/%2e%2e/private/sentinel.txt',self.auth())[0].status,403)
                link=root/'web/static/outside'
                if os.name=='nt':
                    import _winapi
                    _winapi.CreateJunction(str(root/'private'),str(link))
                else: link.symlink_to(root/'private',target_is_directory=True)
                try:
                    self.assertNotEqual(self.request('GET','/static/outside/sentinel.txt')[0].status,200)
                finally:
                    if os.name=='nt': os.rmdir(link)
                    else: link.unlink()
    def test_session_file_port_binding_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(session_access,'SESSION_FILE',Path(folder)/'session.json'):
            session_access.write_session(12345,self.server.session_token)
            self.assertEqual(session_access.read_session(12345),self.server.session_token)
            self.assertIsNone(session_access.read_session(12346))
            session_access.clear_session('another-session')
            self.assertTrue(session_access.SESSION_FILE.exists())
            session_access.clear_session(self.server.session_token)
            self.assertFalse(session_access.SESSION_FILE.exists())

class ResponseLimitTests(unittest.TestCase):
    def run_response(self, data, stream=False):
        class Reply(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
            def log_message(self,*args): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Reply)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            return ai_programs.request(f'http://127.0.0.1:{server.server_port}','GET','/',on_line=(lambda line:None) if stream else None)
        finally: server.shutdown();server.server_close()
    def test_response_size_ceiling(self):
        with mock.patch.object(ai_programs,'MAX_RESPONSE_BYTES',32):
            with self.assertRaises(ai_programs.AIProgramError): self.run_response(b'x'*33)
            self.assertEqual(self.run_response(b'x'*32)[0],200)
    def test_stream_line_ceiling(self):
        with mock.patch.object(ai_programs,'MAX_STREAM_LINE_BYTES',32):
            with self.assertRaises(ai_programs.AIProgramError): self.run_response(b'x'*33+b'\n',True)
    def test_stream_cumulative_ceiling(self):
        with mock.patch.object(ai_programs,'MAX_RESPONSE_BYTES',32):
            with self.assertRaises(ai_programs.AIProgramError): self.run_response(b'1234\n'*7,True)

class RetentionTests(unittest.TestCase):
    def test_preview_pruning_leaves_unrelated_files(self):
        import preview_retention as cache
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(cache,'MAX_FILES',1):
            root=Path(folder)
            (root/'keep.txt').write_text('private')
            (root/'preview_old.wav').write_bytes(b'a')
            (root/'preview_new.wav').write_bytes(b'b')
            cache.prune(root,keep=root/'preview_new.wav')
            self.assertTrue((root/'keep.txt').exists())
            self.assertTrue((root/'preview_new.wav').exists())
            self.assertFalse((root/'preview_old.wav').exists())
    def test_spool_pruning_respects_active_lease_and_unknown_content(self):
        import spool_cleanup as cleanup
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(cleanup,'MAX_AGE',-1):
            root=Path(folder); active=root/('a'*32); active.mkdir()
            lease=cleanup.Lease(active)
            try:
                cleanup.prune(root)
                self.assertTrue(active.exists())
            finally:lease.close()
            (active/'00000000.wav').write_bytes(b'a')
            cleanup.prune(root)
            self.assertFalse(active.exists())
            unknown=root/('b'*32);unknown.mkdir();cleanup.Lease(unknown).close()
            (unknown/'keep.txt').write_text('unrelated')
            cleanup.prune(root)
            self.assertTrue((unknown/'keep.txt').exists())
    def test_live_limit_stops_temporary_audio(self):
        import numpy as np
        from live_audio import LiveAudioSpool
        with tempfile.TemporaryDirectory() as folder:
            spool=LiveAudioSpool(folder);spool.begin()
            try:
                spool.bytes_written=256*1024*1024
                with self.assertRaises(OSError):list(spool.append(np.ones(1),1,lambda a,r:b'wave'))
            finally:spool.clear()
