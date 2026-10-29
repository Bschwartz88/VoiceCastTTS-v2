"""Behavioral regressions for on-demand exports, waveform payloads, and render failures."""
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock
import numpy as np
import soundfile as sf
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import voice_cast as vc

CONFIG = {'default_voices': {'Narrator': {'voice': 'bm_fable', 'speed': 1.0},
                            'Ben': {'voice': 'am_eric', 'speed': 1.0}},
          'pacing_seconds': {'narrator_transition_pause': 0.2}, 'even_volume': False}
SCRIPT = '[Narrator] Once.\n[Ben] Hello.'

class FakePipeline:
    backend = 'fake'
    def __init__(self): self.calls = []
    def generate(self, text, voice, speed):
        self.calls.append((text, voice, speed))
        return [np.full(2400, 0.1, dtype=np.float32)]

class AudioEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.saved = (vc._LATEST_AUDIO_BYTES, vc._LATEST_AUDIO_META, vc._LATEST_SPEAKER_TRACKS)
        vc.clear_line_cache()
        self.pipeline = FakePipeline()
        self.patch = mock.patch.object(vc, 'get_pipeline', return_value=self.pipeline)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(lambda: vc.set_latest_audio(self.saved[0], self.saved[1], self.saved[2]))
        self.addCleanup(vc.clear_line_cache)

    def render(self, script=SCRIPT):
        return vc.render_dialogue_content(script, config=CONFIG)

    def test_listening_only_encodes_the_master(self):
        with mock.patch.object(vc, 'audio_to_wav_bytes', wraps=vc.audio_to_wav_bytes) as encode:
            result = self.render()
            self.assertEqual(encode.call_count, 1)
            self.assertEqual(list(vc.get_latest_speaker_tracks()), ['Narrator', 'Ben'])
            self.assertEqual(encode.call_count, 1, 'Listing available downloads must not build them')
            track = vc.get_speaker_track_audio('ben')
            self.assertEqual(encode.call_count, 2, 'Only the requested speaker is encoded')
        samples, rate = sf.read(io.BytesIO(track))
        self.assertEqual(rate, 24000)
        self.assertTrue(np.all(samples[:2400] == 0))
        self.assertGreater(float(np.max(np.abs(samples[-2400:]))), .05)
        self.assertEqual(len(result['tracks']), 2)

    def test_single_speaker_track_reuses_master_bytes(self):
        self.render('[Ben] Hello.')
        self.assertIs(vc.get_speaker_track_audio('Ben'), vc.get_latest_audio())

    def test_zip_tracks_remain_aligned_and_sum_to_master(self):
        self.render()
        master, _ = sf.read(io.BytesIO(vc.get_latest_audio()))
        with zipfile.ZipFile(io.BytesIO(vc.get_all_tracks_zip_bytes())) as archive:
            self.assertEqual(set(archive.namelist()), {'full_dialogue.wav', 'track_Narrator.wav', 'track_Ben.wav'})
            narrator, _ = sf.read(io.BytesIO(archive.read('track_Narrator.wav')))
            ben, _ = sf.read(io.BytesIO(archive.read('track_Ben.wav')))
        np.testing.assert_array_equal(narrator + ben, master)

    def test_older_track_snapshot_survives_another_render(self):
        self.render()
        tracks = vc.get_latest_speaker_tracks()
        self.render('[Ben] Next.')
        self.assertEqual(list(tracks), ['Narrator', 'Ben'])
        self.assertGreater(len(tracks['Ben']), len(vc.get_latest_audio()))

    def test_pipeline_failure_does_not_report_dry_run_success(self):
        vc.set_latest_audio(b'previous audio')
        with mock.patch.object(vc, 'get_pipeline', side_effect=RuntimeError('test load failure')):
            result = self.render()
        self.assertFalse(result['success'])
        self.assertIn('engine', result['error'].lower())
        self.assertEqual(vc.get_latest_audio(), b'previous audio')

    def test_missing_audio_dependencies_is_an_explicit_failure(self):
        with mock.patch.object(vc, 'AUDIO_LIBS_AVAILABLE', False):
            self.assertFalse(self.render()['success'])

    def test_fine_speed_changes_are_not_cache_collisions(self):
        for speed in (1.001, 1.004):
            config = {**CONFIG, 'default_voices': {'Ben': {'voice': 'am_eric', 'speed': speed}}}
            vc.render_dialogue_content('[Ben] Hello.', config=config)
        self.assertEqual([r[2] for r in self.pipeline.calls], [1.001, 1.004])

    def test_elapsed_time_includes_model_loading_and_wav_encoding(self):
        clock = [100.0]
        def load(**kwargs): clock[0] += 2; return self.pipeline
        encode = vc.audio_to_wav_bytes
        def write(*args, **kwargs): clock[0] += 3; return encode(*args, **kwargs)
        with mock.patch.object(vc.time, 'perf_counter', side_effect=lambda: clock[0]), \
             mock.patch.object(vc, 'get_pipeline', side_effect=load), \
             mock.patch.object(vc, 'audio_to_wav_bytes', side_effect=write):
            result = self.render('[Ben] Hello.')
        self.assertGreaterEqual(result['elapsed_seconds'], 5.0)

    def test_waveform_is_bounded_and_includes_the_last_sample(self):
        audio = np.zeros(10001, dtype=np.float32)
        audio[0], audio[-1] = .5, -.75
        peaks = vc.waveform_peaks(audio, bins=16)
        self.assertEqual(len(peaks), 16)
        self.assertAlmostEqual(peaks[0], .5)
        self.assertAlmostEqual(peaks[-1], .75)
        self.assertEqual(audio[-1], -.75)
        self.assertEqual(vc.waveform_peaks(np.array([], dtype=np.float32)), [])

    def test_render_sends_small_waveform_data(self):
        result = self.render()
        self.assertTrue(result['waveform_peaks'])
        self.assertLessEqual(len(result['waveform_peaks']), 2048)
        self.assertLess(len(json.dumps(result['waveform_peaks'])), 100000)

if __name__ == '__main__': unittest.main()
