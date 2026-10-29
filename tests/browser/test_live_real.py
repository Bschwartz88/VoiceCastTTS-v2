"""One end-to-end local Kokoro/Chrome live-listening smoke test."""
import json,sys,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from test_voice_folders import BROWSER,SKIP_REASON,StudioPageCase
ROOT=HERE.parents[1]
HAS_MODEL=all((ROOT/f).exists() for f in ('kokoro-v1.0.onnx','voices-v1.0.bin'))
SCRIPT='[Narrator] The hallway was quiet.\n[Ben] Are you sure this is the right door?\n[Narrator] He stopped and listened.\n[Ben] Wait! I think someone is inside.\n[Narrator] A key turned slowly in the lock.\n[Ben] Oh, there you are. We were getting worried.'
@unittest.skipIf(BROWSER is None or not HAS_MODEL,SKIP_REASON or 'Local Kokoro model missing')
class RealLiveListeningTests(StudioPageCase):
    def test_real_kokoro_starts_before_completion_and_finishes_with_downloads(self):
        self.b.js("characters=[{id:'real_ben',name:'Ben',gender:'Male',voice:'am_eric',speed:1}]; renderCharacterCards(); "
                  f'document.getElementById("scriptInput").value={json.dumps(SCRIPT)}; '
                  'document.getElementById("liveListenCheckbox").checked=true; window._realLive=generateAudio(); true')
        try:
            self.b.wait_for('liveListening && liveListening.sources.size > 0 && document.getElementById("generateBtn").disabled',timeout=90)
            self.assertEqual(self.b.js('liveListening.context.state'),'running')
            self.b.wait_for('document.getElementById("progressTitle").innerText === "Synthesis Complete"',timeout=120)
            self.assertTrue(self.b.js('_realLive'))
            self.assertEqual(self.b.js('document.querySelectorAll("#transcriptList .transcript-line").length'),6)
            self.assertTrue(self.b.js('document.getElementById("downloadLink").href.includes("/api/audio/latest.wav")'))
            self.assertTrue(self.b.js('document.getElementById("downloadZipLink").href.includes("/api/audio/tracks.zip")'))
            self.assertEqual(self.b.messages('exception')+self.b.messages('error'),[])
        finally:
            self.b.js('stopLiveListening(); true')
            self.web_server._LIVE_AUDIO.clear()
