"""Speaker identities and delivery annotations across all story entry paths; no models needed."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lm_studio
import voice_cast
from speakability import clean_for_speech
from speaker_labels import split_speaker_label

CASES = json.loads((ROOT / 'tests/speaker_label_cases.json').read_text(encoding='utf-8'))

class SpeakerLabelTests(unittest.TestCase):
    def test_shared_cases(self):
        for case in CASES:
            with self.subTest(case['input']):
                self.assertEqual(split_speaker_label(case['input']), {'name': case['name'], 'cue': case['cue']})

    def test_structured_answer_keeps_one_character_and_directions(self):
        answer = {'title': 'Test', 'cast': [{'name': 'Ben (male, 30)', 'voice_type': 'Male'}],
                  'lines': [{'speaker': 'Ben (whispering)', 'text': 'Ben: Come here.'},
                            {'speaker': 'Ben (angry)', 'text': '(looks behind him) Not now.'}]}
        result = lm_studio.parse_structured_reply(json.dumps(answer))
        self.assertEqual(result['lines'], [{'speaker': 'Ben', 'text': '(whispering) Come here.'},
                                          {'speaker': 'Ben', 'text': '(angrily) (looks behind him) Not now.'}])
        self.assertEqual(lm_studio.script_cast(result['lines'], result['cast']), [{'name': 'Ben', 'voice_type': 'Male'}])
        self.assertEqual([clean_for_speech(line['text']) for line in result['lines']], ['Come here.', 'Not now.'])

    def test_plain_and_truncated_answers_agree(self):
        expected = [{'speaker': 'Ben', 'text': '(angrily) Go.'}]
        self.assertEqual(lm_studio.clean_plain_reply('[Ben (angry)] Ben: Go.'), expected)
        self.assertEqual(lm_studio.salvage_lines('{"lines":[{"speaker":"Ben (angry)","text":"Ben: Go."},'), expected)

    def test_raw_script_and_detection_agree_without_format_button(self):
        script = '[Ben (angry)] Go.\n[Ben (whispering)] Stay.\n[Speaker 1 (Lila)] Hello.'
        self.assertEqual(voice_cast.parse_script(script), [('Ben', '(angrily) Go.'),
                         ('Ben', '(whispering) Stay.'), ('Lila', 'Hello.')])
        self.assertEqual(voice_cast.extract_speakers(script), ['Ben', 'Lila'])

    def test_repeated_raw_or_normalized_names_are_not_spoken(self):
        self.assertEqual(voice_cast.parse_script('[Ben (angry)] Ben (angry): Go.'), [('Ben', '(angrily) Go.')])
        self.assertEqual(voice_cast.parse_script('[Ben (angry)] Ben: Go.'), [('Ben', '(angrily) Go.')])

    def test_numbered_metadata_is_not_a_new_name(self):
        self.assertEqual(lm_studio.clean_speaker('Speaker 1 (Female, 30)'), 'Speaker 1')
        self.assertEqual(lm_studio.clean_speaker('Speaker 1 (Lila)'), 'Lila')

    def test_render_uses_the_assigned_voice_for_annotated_tags(self):
        result = voice_cast.render_dialogue_content('[Ben (angry)] Go.\n[Ben (whispers)] Stay.',
            config={'default_voices': {'Ben': {'voice': 'am_eric', 'speed': 1.0}}}, dry_run=True)
        self.assertEqual([(r['speaker'], r['voice'], r['text']) for r in result['lines']],
                         [('Ben', 'am_eric', 'Go.'), ('Ben', 'am_eric', 'Stay.')])

    def test_old_cast_labels_keep_their_assigned_voice(self):
        config = {'default_voices': {'Other': {'voice': 'af_sky', 'speed': 1.0},
                  'Ben (angry)': {'voice': 'am_eric', 'speed': .91}}}
        result = voice_cast.render_dialogue_content('[Ben (angry)] Go.', config=config, dry_run=True)
        self.assertEqual((result['lines'][0]['voice'], result['lines'][0]['speed']), ('am_eric', .91))

if __name__ == '__main__':
    unittest.main()
