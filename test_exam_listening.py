"""Fixed listening media stays available without exposing listening scripts."""

from copy import deepcopy
from functools import wraps
import json
import sqlite3

import pytest
from flask import Flask

import builtin_exam_catalog as catalog
import exam_mistakes
from exam_preparation import ExamPreparationError, ExamPreparationStore, public_attempt
from exam_preparation_routes import register_exam_preparation_routes


PAPER_ID = 'builtin-listening-test'


def listening_question(index, transcript, answer):
    return {
        'id': f'l{index:02d}', 'type': 'listening_choice',
        'prompt': '听录音，选择人物所在的地点。',
        'english': f'Listening {index}', 'chinese': f'听力第 {index} 题',
        'options': [
            {'id': 'a', 'text': 'The library.'}, {'id': 'b', 'text': 'The classroom.'},
            {'id': 'c', 'text': 'The hall.'}, {'id': 'd', 'text': 'The park.'},
        ],
        'answer': answer, 'explanation': '根据录音中的地点判断。',
        'listening_text': transcript,
        'audio_file': f'shanghai-grade4-final/listen-{index:02d}.wav',
        'audio_url': 'https://untrusted.example/ignored.wav',
    }


@pytest.fixture
def listening_catalog(monkeypatch, tmp_path):
    directory = tmp_path / 'catalog'
    directory.mkdir()
    paper = {
        'id': PAPER_ID, 'name': '听力测试卷', 'grade': 4, 'edition': '测试材料',
        'description': '固定听力题。', 'source_note': '独立测试材料。', 'time_minutes': 10,
        'source_file': 'listening-study.pdf', 'exam_file': 'listening-exam.pdf',
        'knowledge': {'title': '听力知识点', 'intro': '听人物和地点。', 'sections': []},
        'questions': [
            listening_question(1, 'Ben is in the library.', 'a'),
            listening_question(2, 'Ben is in the classroom.', 'b'),
        ],
    }
    filename = directory / 'listening-test.json'
    filename.write_text(json.dumps(paper, ensure_ascii=False))
    monkeypatch.setattr(catalog, 'CATALOG_DIR', directory)
    catalog._catalog.cache_clear()
    yield paper, filename
    catalog._catalog.cache_clear()


def rewrite_catalog(filename, paper):
    filename.write_text(json.dumps(paper, ensure_ascii=False))
    catalog._catalog.cache_clear()


def private_attempt(store, attempt_id):
    with sqlite3.connect(store.path) as connection:
        return json.loads(connection.execute(
            'SELECT attempt_json FROM exam_attempts WHERE id=?', (attempt_id,),
        ).fetchone()[0])


def no_generation(*args):
    pytest.fail('A fixed listening exam must not generate questions or speech')


def test_listening_metadata_exposes_counts_and_local_media_without_script_or_answer(listening_catalog, tmp_path):
    paper, _ = listening_catalog
    metadata = catalog.public_paper(PAPER_ID)
    assert metadata['listening_count'] == 2
    assert metadata['question_count'] == 2
    assert 'questions' not in metadata and 'listening_text' not in metadata

    store = ExamPreparationStore(tmp_path / 'exams.sqlite3')
    attempt = store.start_attempt(store.get_paper(PAPER_ID), False, no_generation)
    assert attempt['listening_count'] == 2
    assert attempt['choice_count'] == 2
    assert attempt['include_choices'] is True
    for index, question in enumerate(attempt['questions'], 1):
        assert set(question) == {'id', 'type', 'prompt', 'options', 'audio_url'}
        assert question['audio_url'] == f'/static/exam-preparation/audio/shanghai-grade4-final-normal/listen-{index:02d}.wav'
    serialized = json.dumps(attempt)
    for question in paper['questions']:
        assert question['listening_text'] not in serialized
    assert store.get_attempt(attempt['id'])['attempt'] == attempt
    private = private_attempt(store, attempt['id'])
    assert private['questions'][0]['listening_text'] == 'Ben is in the library.'
    assert private['questions'][0]['audio_file'] == 'shanghai-grade4-final/listen-01.wav'
    assert private['questions'][0]['answer'] == 'a'


def test_public_attempt_never_treats_non_listening_audio_fields_as_playable_media():
    attempt = {'id': 'sample', 'questions': [{
        'id': 'spelling', 'type': 'spelling', 'prompt': '苹果', 'answer': 'apple',
        'audio_url': 'https://untrusted.example/private.wav', 'listening_text': 'Private text.',
    }]}
    assert public_attempt(attempt)['questions'] == [{'id': 'spelling', 'type': 'spelling', 'prompt': '苹果'}]


@pytest.mark.parametrize('filename', [
    '../listen.wav', '/listen.wav', 'folder/../listen.wav', 'folder/./listen.wav',
    'folder//listen.wav', r'folder\listen.wav', 'https://example.com/listen.wav',
    'listen.wav?answer=a', 'listen.wav#answer', '%2e%2e/listen.wav', 'listen.mp3', '', None,
])
def test_catalog_rejects_audio_paths_outside_the_fixed_local_wav_namespace(listening_catalog, filename):
    paper, path = listening_catalog
    paper['questions'][0]['audio_file'] = filename
    rewrite_catalog(path, paper)
    with pytest.raises(ValueError, match='audio filename'):
        catalog.list_papers()


@pytest.mark.parametrize('transcript', [None, '', ' \n ', 42])
def test_catalog_requires_a_nonempty_private_listening_script(listening_catalog, transcript):
    paper, path = listening_catalog
    paper['questions'][0]['listening_text'] = transcript
    rewrite_catalog(path, paper)
    with pytest.raises(ValueError, match='Missing listening text'):
        catalog.list_papers()


def test_optional_exam_download_is_separate_from_the_knowledge_handbook(listening_catalog):
    paper, filename = listening_catalog
    knowledge = catalog.knowledge(PAPER_ID)
    assert knowledge['pdf_url'] == '/static/exam-preparation/listening-study.pdf'
    assert knowledge['exam_pdf_url'] == '/static/exam-preparation/listening-exam.pdf'
    assert 'questions' not in knowledge and 'listening_text' not in knowledge
    del paper['exam_file']
    rewrite_catalog(filename, paper)
    assert 'exam_pdf_url' not in catalog.knowledge(PAPER_ID)


@pytest.mark.parametrize('field', ['source_file', 'exam_file'])
@pytest.mark.parametrize('filename', ['../paper.pdf', r'folder\paper.pdf', '/paper.pdf', 'https:paper.pdf', 'paper%2e.pdf', 'paper.pdf?x=1', 'paper' + chr(0) + '.pdf', None])
def test_catalog_rejects_unsafe_pdf_filenames(listening_catalog, field, filename):
    paper, path = listening_catalog
    paper[field] = filename
    rewrite_catalog(path, paper)
    with pytest.raises(ValueError, match='handbook filename'):
        catalog.list_papers()


def test_distinct_recordings_do_not_merge_mistakes_when_question_and_options_match(listening_catalog, tmp_path):
    store = ExamPreparationStore(tmp_path / 'exams.sqlite3')
    paper = store.get_paper(PAPER_ID)
    attempt = store.start_attempt(paper, False, no_generation)
    result = store.submit_attempt(attempt['id'], {})
    assert (result['score'], result['wrong_count']) == (0, 2)
    assert store.mistake_count(PAPER_ID) == 2
    review = store.start_mistake_review(PAPER_ID)
    assert review['listening_count'] == 2
    assert review['choice_count'] == 2
    assert {question['audio_url'] for question in review['questions']} == {
        question['audio_url'] for question in attempt['questions']
    }
    assert all('listening_text' not in question for question in review['questions'])
    private = private_attempt(store, review['id'])
    answers = {question['id']: question['answer'] for question in private['questions']}
    assert store.submit_attempt(review['id'], answers)['score'] == 100
    assert store.mistake_count(PAPER_ID) == 0
    summary = next(item for item in store.list_papers() if item['id'] == PAPER_ID)
    assert (summary['attempt_count'], summary['last_score']) == (1, 0)


def test_listening_identity_follows_the_script_instead_of_media_version_or_option_order(listening_catalog):
    questions = catalog.questions(PAPER_ID)
    first, other = questions
    assert exam_mistakes.question_key(first) != exam_mistakes.question_key(other)
    equivalent = deepcopy(first)
    equivalent['audio_file'] = 'shanghai-grade4-final/listen-new-recording.wav'
    equivalent['audio_url'] = '/static/exam-preparation/audio/shanghai-grade4-final/listen-new-recording.wav'
    equivalent['listening_text'] = ' BEN   IS IN THE LIBRARY. '
    equivalent['options'].reverse()
    assert exam_mistakes.question_key(first) == exam_mistakes.question_key(equivalent)


def test_listening_results_and_original_audio_snapshots_stay_private_between_users(listening_catalog, tmp_path):
    paper, filename = listening_catalog
    alice = ExamPreparationStore(tmp_path / 'alice' / 'exams.sqlite3')
    bob = ExamPreparationStore(tmp_path / 'bob' / 'exams.sqlite3')
    attempt = alice.start_attempt(alice.get_paper(PAPER_ID), False, no_generation)
    original_audio = [question['audio_url'] for question in attempt['questions']]
    paper['questions'][0]['audio_file'] = 'shanghai-grade4-final/listen-revised.wav'
    paper['questions'][0]['listening_text'] = 'A revised listening story.'
    rewrite_catalog(filename, paper)
    assert [question['audio_url'] for question in alice.get_attempt(attempt['id'])['attempt']['questions']] == original_audio
    with pytest.raises(ExamPreparationError) as error:
        bob.get_attempt(attempt['id'])
    assert error.value.status == 404
    with pytest.raises(ExamPreparationError) as error:
        bob.submit_attempt(attempt['id'], {})
    assert error.value.status == 404
    assert next(item for item in bob.list_papers() if item['id'] == PAPER_ID)['attempt_count'] == 0


def test_written_only_metadata_remains_compatible_without_audio_or_exam_download(listening_catalog):
    paper, filename = listening_catalog
    paper['questions'] = [{
        'id': 'written', 'type': 'fill_blank', 'prompt': 'There ___ a museum.',
        'english': 'Fill in the blank', 'chinese': '填空', 'answer': 'is',
        'accepted_answers': ['is'], 'explanation': '单数名词使用 is。',
    }]
    del paper['exam_file']
    rewrite_catalog(filename, paper)
    assert catalog.public_paper(PAPER_ID)['listening_count'] == 0
    assert 'exam_pdf_url' not in catalog.knowledge(PAPER_ID)
    assert all('audio_url' not in question for question in catalog.questions(PAPER_ID))


def test_older_clients_are_prompted_to_refresh_before_receiving_unplayable_questions(listening_catalog, tmp_path):
    app = Flask(__name__)
    app.config.update(TESTING=True)

    def authenticated(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            return function('alice', *args, **kwargs)
        return wrapped

    register_exam_preparation_routes(app, authenticated, lambda function: function,
                                    lambda: tmp_path, lambda *args: {}, no_generation)
    client = app.test_client()
    endpoint = f'/api/exam-preparation/papers/{PAPER_ID}'
    rejected = client.post(endpoint + '/attempts', json={})
    assert rejected.status_code == 409
    assert '刷新' in rejected.get_json()['error']
    store = ExamPreparationStore(tmp_path / 'alice/exam_preparation.sqlite3')
    with sqlite3.connect(store.path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM exam_attempts').fetchone()[0] == 0
    invalid = client.post(endpoint + '/attempts', json={'supports_listening': 'true'})
    assert invalid.status_code == 400
    issued = client.post(endpoint + '/attempts', json={'supports_listening': True})
    assert issued.status_code == 201
    attempt = issued.get_json()['attempt']
    assert attempt['listening_count'] == 2
    restore = f"/api/exam-preparation/attempts/{attempt['id']}"
    assert client.get(restore).status_code == 409
    assert client.get(restore + '?supports_listening=1').status_code == 200
    store.submit_attempt(attempt['id'], {})
    rejected_review = client.post(endpoint + '/mistake-reviews', json={})
    assert rejected_review.status_code == 409
    with sqlite3.connect(store.path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM exam_attempts').fetchone()[0] == 1
    review = client.post(endpoint + '/mistake-reviews', json={'supports_listening': True})
    assert review.status_code == 201
    assert review.get_json()['attempt']['listening_count'] == 2


def test_withdrawn_catalog_entry_does_not_become_an_empty_personal_paper(listening_catalog, tmp_path):
    _, filename = listening_catalog
    store = ExamPreparationStore(tmp_path / 'exams.sqlite3')
    attempt = store.start_attempt(store.get_paper(PAPER_ID), False, no_generation)
    original = private_attempt(store, attempt['id'])
    filename.unlink()
    catalog._catalog.cache_clear()
    assert store.list_papers() == []
    with pytest.raises(ExamPreparationError) as error:
        store.get_paper(PAPER_ID)
    assert error.value.status == 404
    assert store.get_attempt(attempt['id'])['attempt'] == attempt
    answers = {question['id']: question['answer'] for question in original['questions']}
    assert store.submit_attempt(attempt['id'], answers)['score'] == 100
