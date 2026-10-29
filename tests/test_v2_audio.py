"""v2 leveling behavior for brief replies, ordinary variation and explicit cues."""
import io,sys,unittest
from pathlib import Path
from unittest import mock
import numpy as np
import soundfile as sf
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import voice_cast as vc
class VariedPipeline:
    backend='fake'
    def generate(self,text,voice,speed):
        amplitude=.03 if 'Yes' in text else .3 if 'No' in text else .12
        samples=2400 if 'Yes' in text else 9307
        return [np.full(samples,amplitude,dtype=np.float32)]
class V2VolumeTests(unittest.TestCase):
    def setUp(self):
        self.old=(vc._LATEST_AUDIO_BYTES,vc._LATEST_AUDIO_META,vc._LATEST_SPEAKER_TRACKS)
        self.addCleanup(lambda:vc.set_latest_audio(*self.old))
        vc.clear_line_cache();self.addCleanup(vc.clear_line_cache)
        patch=mock.patch.object(vc,'get_pipeline',return_value=VariedPipeline());patch.start();self.addCleanup(patch.stop)
    def render_levels(self,script,even=True,level='natural'):
        result=vc.render_dialogue_content(script,config={'default_voices':{'Ben':{'voice':'am_eric','speed':1}},'even_volume':even,'performance':level})
        self.assertTrue(result['success'])
        audio,rate=sf.read(io.BytesIO(vc.get_latest_audio()),dtype='float32')
        return [vc.speech_loudness_dbfs(audio[round(line['start']*rate):round(line['end']*rate)],rate) for line in result['lines']]
    def test_short_and_long_ordinary_lines_match_with_even_volume(self):
        for level in ('off','natural','lively'):
            levels=self.render_levels('[Ben] Yes.\n[Ben] No!\n[Ben] Really?',level=level)
            self.assertLess(max(levels)-min(levels),.05,level)
            for db in levels:self.assertAlmostEqual(db,vc.TARGET_SPEECH_DBFS,delta=.05)
    def test_turning_even_volume_off_preserves_source_difference(self):
        levels=self.render_levels('[Ben] Yes.\n[Ben] No!',even=False,level='off')
        self.assertGreater(abs(levels[0]-levels[1]),18)
    def test_intentional_quiet_and_shout_are_preserved(self):
        levels=self.render_levels('[Ben] Yes.\n[Ben] (whispers) Yes.\n[Ben] (shouts) Yes.')
        self.assertAlmostEqual(levels[1]-levels[0],-10,delta=.1)
        self.assertAlmostEqual(levels[2]-levels[0],5,delta=.1)
    def test_subframe_and_partial_frame_speech_are_measured_without_mutation(self):
        for size in (1,120,479,481,9307):
            original=np.full(size,.03,dtype=np.float32);before=original.copy()
            out=vc.level_line(original)
            self.assertAlmostEqual(vc.speech_loudness_dbfs(out),vc.TARGET_SPEECH_DBFS,delta=.001)
            np.testing.assert_array_equal(original,before)
    def test_silence_does_not_change_measured_speech_level(self):
        signal=np.full(4800,.1,dtype=np.float32)
        padded=np.concatenate((np.zeros(480),signal,np.zeros(960)))
        self.assertAlmostEqual(vc.speech_loudness_dbfs(signal),vc.speech_loudness_dbfs(padded),places=5)
