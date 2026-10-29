"""Real Chrome Web Audio integration with local deterministic speech fixtures."""
import os,sys,tempfile,time,unittest
from pathlib import Path
from unittest import mock
import numpy as np
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from test_voice_folders import BROWSER,SKIP_REASON,StudioPageCase
import voice_cast as vc
from live_audio import LiveAudioSpool
class SlowPipeline:
    backend='fake'
    def generate(self,text,voice,speed):
        time.sleep(.15)
        return [(.05*np.sin(np.arange(19200,dtype=np.float32)*2*np.pi*220/24000)).astype(np.float32)]
@unittest.skipIf(BROWSER is None,SKIP_REASON)
class LiveListeningPageTests(StudioPageCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.live_dir=tempfile.TemporaryDirectory()
        cls.patches=[mock.patch.object(vc,'get_pipeline',return_value=SlowPipeline()),
                     mock.patch.object(cls.web_server,'OUTPUT_DIR',cls.live_dir.name),
                     mock.patch.object(cls.web_server,'_LIVE_AUDIO',LiveAudioSpool(cls.live_dir.name))]
        for patch in cls.patches:patch.start()
        cls.old=(vc._LATEST_AUDIO_BYTES,vc._LATEST_AUDIO_META,vc._LATEST_SPEAKER_TRACKS)
    @classmethod
    def tearDownClass(cls):
        cls.b.js('stopLiveListening(); document.getElementById("mainAudio").pause(); true')
        super().tearDownClass()
        for patch in reversed(cls.patches):patch.stop()
        vc.set_latest_audio(*cls.old);vc.clear_line_cache();cls.live_dir.cleanup()
    def setUp(self):
        vc.clear_line_cache()
        self.b.js('stopLiveListening(); document.getElementById("mainAudio").pause(); true')
    def start(self,lines=8,live=True):
        script='\n'.join(f'[Ben] Test line number {i}.' for i in range(lines))
        import json
        self.b.js(f'document.getElementById("scriptInput").value={json.dumps(script)}; '
                  f'document.getElementById("liveListenCheckbox").checked={str(live).lower()}; '
                  'autoDetectSpeakers(true); window._liveRun=generateAudio(); true')
    def first(self):
        try:
            self.b.wait_for('liveListening && liveListening.sources.size > 0 && document.getElementById("generateBtn").disabled',timeout=5)
        except Exception:
            print('LIVE DIAGNOSTIC:', self.b.js('({status:document.getElementById("liveListenStatus").innerText, live:!!liveListening, closed:liveListening?.closed, ctx:liveListening?.context.state, queue:liveListening?.queue.length, errors: typeof LiveListening})'), self.b.console)
            raise
    def complete(self):
        self.b.wait_for('document.getElementById("progressTitle").innerText === "Synthesis Complete"',timeout=20)
        self.assertTrue(self.b.js('_liveRun'))
    def test_01_initially_opted_out_and_chrome_profile_is_scoped(self):
        self.assertFalse(self.b.js('document.getElementById("liveListenCheckbox").checked'))
        self.assertEqual(Path(self.b.profile).resolve().parent,Path(os.environ['VOICECAST_TEST_PROFILE_ROOT']).resolve())
        self.assertEqual(Path(BROWSER).name.lower(),'chrome.exe')
    def test_02_audio_starts_before_completion_and_drains_once(self):
        self.start();self.first()
        self.assertEqual(self.b.js('liveListening.context.state'),'running')
        self.assertLessEqual(self.b.js('liveListening.nextTime - liveListening.context.currentTime'),6.06)
        self.complete()
        self.assertFalse(self.b.js('liveListening.closed'))
        self.assertIn('Listening continues',self.b.js('document.getElementById("progressStatus").innerText'))
        self.b.wait_for('liveListening.closed',timeout=20)
        self.assertEqual(self.b.js('liveListening.context.state'),'closed')
        self.assertTrue(self.b.js('document.getElementById("mainAudio").paused'))
        self.assertTrue(self.b.js('document.getElementById("mainAudio").src.includes("/api/audio/latest.wav")'))
        self.b.screenshot(str(HERE/'screenshots'/'lab_live_listening_chrome.png'))
    def test_03_unchecking_stops_only_listening(self):
        self.start(20);self.first()
        self.b.js('window._previousLive=liveListening; document.getElementById("liveListenCheckbox").click(); true')
        self.assertTrue(self.b.js('liveListening === null && _previousLive.closed'))
        self.assertTrue(self.b.js('document.getElementById("generateBtn").disabled'))
        self.complete()
    def test_04_stop_cancels_preview_and_render(self):
        self.start(20);self.first();self.b.js('stopRender(); true')
        self.b.wait_for('document.getElementById("progressTitle").innerText === "Stopped"',timeout=15)
        self.assertTrue(self.b.js('liveListening === null'))
        self.assertFalse(self.b.js('_liveRun'))
    def test_05_master_play_stops_live_and_starts_completed_audio(self):
        self.start();self.first();self.complete()
        self.b.js('window._previousLive=liveListening; toggleMasterPlay(); true')
        self.b.wait_for('!document.getElementById("mainAudio").paused',timeout=10)
        self.assertTrue(self.b.js('liveListening === null && _previousLive.closed'))
    def test_06_normal_generation_keeps_existing_behavior(self):
        self.start(2,False);self.complete()
        self.assertTrue(self.b.js('liveListening === null'))
        self.assertIsNone(self.web_server._LIVE_AUDIO.current)
        self.assertTrue(self.b.js('document.getElementById("mainAudio").paused'))
    def test_99_no_browser_errors(self):
        self.assertEqual(self.b.messages('exception')+self.b.messages('error'),[])
