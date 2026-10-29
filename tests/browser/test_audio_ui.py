"""Small browser regressions for compact waveforms and truthful completion reporting."""
import json
import sys
import unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from test_voice_folders import BROWSER, SKIP_REASON, StudioPageCase

@unittest.skipIf(BROWSER is None, SKIP_REASON)
class AudioPageTests(StudioPageCase):
    def test_01_waveform_needs_no_audio_fetch_or_decode(self):
        result = self.b.js("""(() => {
          const original = window.fetch;
          let calls = 0;
          window.fetch = () => { calls++; throw new Error('unexpected waveform request'); };
          try {
            loadWaveformData([0, 0.2, 0.5, 1, 0.1]);
            return {calls, source: currentWaveformPeaks.length, bars: cachedPeaks.length};
          } finally { window.fetch = original; }
        })()""")
        self.assertEqual(result['calls'],0)
        self.assertEqual(result['source'],5)
        self.assertGreater(result['bars'],20)
        self.b.js("computePeaks(); drawWaveform(.5); true")

    def test_02_clear_releases_waveform_data(self):
        self.b.js('clearOutput(); true')
        self.assertTrue(self.b.js('currentWaveformPeaks === null && cachedPeaks === null'))

    def test_03_validation_is_not_reported_as_generated_audio(self):
        self.b.js("document.getElementById('scriptInput').value = '[Narrator] Hello.'; true")
        event = 'data: ' + json.dumps({'type':'complete','result':{'success':True,'dry_run':True}}) + '\n\n'
        expression = """(async () => {
          const original = window.fetch;
          window.fetch = async () => new Response(EVENT, {headers:{'Content-Type':'text/event-stream'}});
          try { return await generateAudio(); }
          finally { window.fetch = original; }
        })()""".replace('EVENT',json.dumps(event))
        self.assertFalse(self.b.js(expression))
        self.assertEqual(self.b.js("document.getElementById('progressTitle').innerText"),'Generation failed')
        self.assertEqual(self.b.messages('exception'),[])
