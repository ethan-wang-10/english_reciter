"""Curriculum paper consistency and the actual offline media shipped to students."""

from collections import Counter
from pathlib import Path
import wave

import builtin_exam_catalog as catalog
from exam_preparation import ExamPreparationStore


ROOT = Path(__file__).resolve().parent
PAPER_ID = 'builtin-shanghai-grade4-u5-u10-final'


def test_final_scope_and_question_mix_are_fixed_and_gradable(tmp_path):
    paper = catalog.public_paper(PAPER_ID)
    assert paper['question_count'] == 40
    assert paper['listening_count'] == 10
    assert paper['time_minutes'] == 45
    assert 'U5-U10' in paper['name']
    questions = catalog.questions(PAPER_ID)
    assert Counter(question['type'] for question in questions) == {
        'listening_choice': 10, 'fill_blank': 12, 'rewrite': 8, 'reading_choice': 10,
    }
    store = ExamPreparationStore(tmp_path / 'exams.sqlite3')
    attempt = store.start_attempt(store.get_paper(PAPER_ID), False, lambda *args: None)
    assert all(set(question) <= {'id', 'type', 'prompt', 'options', 'audio_url'} for question in attempt['questions'])
    answers = {issued['id']: original['answer'] for issued, original in zip(attempt['questions'], questions, strict=True)}
    assert store.submit_attempt(attempt['id'], answers)['score'] == 100


def test_final_knowledge_covers_the_six_requested_units_and_retains_the_midterm():
    material = catalog.knowledge(PAPER_ID)
    ids = {section['id'] for section in material['knowledge']['sections']}
    assert {f'u{index}' for index in range(5, 11)} <= ids
    assert not ids.intersection({'u1', 'u2', 'u3', 'u4'})
    assert material['pdf_url'].endswith('shanghai-grade4-u5-u10-review.pdf')
    assert material['exam_pdf_url'].endswith('shanghai-grade4-u5-u10-final.pdf')
    midterm = catalog.public_paper('builtin-shanghai-grade4-u1-u4')
    assert midterm['question_count'] == 26 and midterm['listening_count'] == 10
    assert len([question for question in catalog.questions(midterm['id']) if question['type'] != 'listening_choice']) == 16


def test_final_pdfs_and_all_ten_recordings_are_real_nonempty_assets():
    material = catalog.knowledge(PAPER_ID)
    for url in (material['pdf_url'], material['exam_pdf_url']):
        document = ROOT / url.lstrip('/')
        content = document.read_bytes()
        assert content.startswith(b'%PDF-') and b'%%EOF' in content[-100:]
        assert len(content) > 20000
    for question in catalog.questions(PAPER_ID):
        if question['type'] != 'listening_choice':
            continue
        recording = ROOT / question['audio_url'].lstrip('/')
        with wave.open(str(recording)) as audio:
            assert audio.getnchannels() == 1
            assert audio.getsampwidth() == 2
            assert audio.getframerate() == 22050
            duration = audio.getnframes() / audio.getframerate()
            assert 5 < duration < 180, (recording.name, duration)
            assert any(audio.readframes(audio.getnframes())), recording.name


def test_midterm_has_its_own_complete_exam_pdf_and_ten_distinct_recordings():
    paper_id = 'builtin-shanghai-grade4-u1-u4'
    material = catalog.knowledge(paper_id)
    document = ROOT / material['exam_pdf_url'].lstrip('/')
    assert document.read_bytes().startswith(b'%PDF-')
    recordings = [question for question in catalog.questions(paper_id) if question['type'] == 'listening_choice']
    assert len(recordings) == 10
    for question in recordings:
        assert '/shanghai-grade4-midterm/' in question['audio_url']
        with wave.open(str(ROOT / question['audio_url'].lstrip('/'))) as audio:
            assert audio.getnframes() > 5 * audio.getframerate()
            assert audio.getframerate() == 22050
