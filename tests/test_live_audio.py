"""Opt-in live audio preserves final samples and reaches HTTP clients before completion."""
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import urllib.request
import unittest
from unittest import mock
import numpy as np
import soundfile as sf
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import voice_cast as vc
import web_server as ws
from live_audio import LiveAudioSpool
CONFIG = {'default_voices':{'Ben':{'voice':'am_eric','speed':1},'Narrator':{'voice':'bm_fable','speed':1}},
          'pacing_seconds':{'narrator_transition_pause':.2}, 'even_volume':False}
SCRIPT = '[Ben] [pause .2s]\n[Narrator] Once.\n[Ben] (whispers) Hi. [pause .3s] Go.'
class Pipeline:
    backend='fake'
    def generate(self,text,voice,speed): return [np.full(2400,.1,dtype=np.float32)]
class LiveAudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old=(vc._LATEST_AUDIO_BYTES,vc._LATEST_AUDIO_META,vc._LATEST_SPEAKER_TRACKS)
        self.addCleanup(lambda:vc.set_latest_audio(*self.old))
        vc.clear_line_cache();self.addCleanup(vc.clear_line_cache)
        patch=mock.patch.object(vc,'get_pipeline',return_value=Pipeline());patch.start();self.addCleanup(patch.stop)
    def test_callback_includes_standalone_and_inline_pauses_and_cached_audio(self):
        for attempt in range(2):
            chunks=[]
            result=vc.render_dialogue_content(SCRIPT,config=CONFIG,audio_callback=lambda a,r:chunks.append(a.copy()))
            self.assertTrue(result['success'])
            master,rate=sf.read(io.BytesIO(vc.get_latest_audio()),dtype='float32')
            sent=np.concatenate(chunks)
            self.assertEqual(len(sent),len(master))
            self.assertLess(float(np.max(np.abs(master-sent))),1/32768+.000001)
            if attempt: self.assertEqual(result['cached_lines'],2)
    def test_stop_does_not_emit_later_lines(self):
        chunks=[]
        result=vc.render_dialogue_content('[Ben] One.\n[Ben] Two.',config=CONFIG,
            audio_callback=lambda a,r:chunks.append(a),should_cancel=lambda:bool(chunks))
        self.assertTrue(result['cancelled']);self.assertEqual(len(chunks),1)
    def test_spool_splits_and_preserves_pcm_and_replaces_only_its_own_directory(self):
        spool=LiveAudioSpool(self.tmp.name);spool.begin();old=spool.current
        unrelated=spool.root/'keep.txt';unrelated.write_text('keep')
        data=np.linspace(-.3,.3,24000*5,dtype=np.float32)
        pieces=list(spool.append(data,24000,vc.audio_to_wav_bytes))
        self.assertEqual(len(pieces),3)
        self.assertTrue(all(p['duration']<=2 for p in pieces))
        actual=np.concatenate([sf.read(spool.current/Path(p['url']).name,dtype='int16')[0] for p in pieces])
        expected=sf.read(io.BytesIO(vc.audio_to_wav_bytes(data,24000)),dtype='int16')[0]
        np.testing.assert_array_equal(actual,expected)
        spool.begin();self.assertFalse(old.exists());self.assertTrue(unrelated.exists())
    def test_spool_refuses_to_delete_unowned_directory(self):
        spool=LiveAudioSpool(self.tmp.name);spool.current=Path(self.tmp.name)
        with self.assertRaises(ValueError):spool.clear()
    def http_render(self, enabled, spool=None):
        spool=spool or LiveAudioSpool(self.tmp.name)
        server=ws.StudioServer(('127.0.0.1',0),ws.StudioHandler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with mock.patch.object(ws,'_LIVE_AUDIO',spool),mock.patch.object(ws,'CONFIG_PATH',str(ROOT/'voice_config.json')),mock.patch.object(ws,'OUTPUT_DIR',self.tmp.name):
                req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/generate',
                    data=json.dumps({'script':'[Ben] One.\n[Ben] Two.','voices':{'Ben':{'voice':'am_eric','speed':1}},'live_listen':enabled}).encode(),headers={'Authorization':f'Bearer {server.session_token}','Content-Type':'application/json','Origin':f'http://127.0.0.1:{server.server_port}'})
                events=[]
                with urllib.request.urlopen(req,timeout=10) as response:
                    for raw in response:
                        if raw.startswith(b'data: '):
                            event=json.loads(raw[6:]);events.append(event)
                            if event['type']=='audio':
                                with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+event['data']['url'],headers={'Authorization':f'Bearer {server.session_token}'}),timeout=5) as wav:
                                    self.assertGreater(len(wav.read()),44)
                return events
        finally:server.shutdown();server.server_close()
    def test_http_audio_precedes_complete_and_urls_are_immediately_readable(self):
        events=self.http_render(True)
        kinds=[e['type'] for e in events]
        self.assertIn('audio',kinds);self.assertLess(kinds.index('audio'),kinds.index('complete'))
        self.assertTrue(events[-1]['result']['success'])
    def test_normal_generation_has_no_spool_or_audio_events(self):
        spool=LiveAudioSpool(self.tmp.name);events=self.http_render(False,spool)
        self.assertIsNone(spool.current)
        self.assertNotIn('audio',[e['type'] for e in events])
        self.assertTrue(events[-1]['result']['success'])
    def test_live_disk_failure_does_not_fail_final_recording(self):
        spool=LiveAudioSpool(self.tmp.name)
        with mock.patch.object(spool,'append',side_effect=OSError('disk full')):
            events=self.http_render(True,spool)
        self.assertIn('listen_error',[e['type'] for e in events]);self.assertTrue(events[-1]['result']['success'])
