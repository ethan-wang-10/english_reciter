"""Grade-five curriculum papers remain consistent across online exams and media."""

from collections import Counter
import json
from pathlib import Path
import re
import runpy
import wave

import pytest

import builtin_exam_catalog as catalog
from exam_preparation import ExamPreparationStore, grade_attempt


ROOT = Path(__file__).resolve().parent
PAPERS = [
    ('midterm', 'U1-U4', range(1, 5)),
    ('final', 'U5-U10', range(5, 11)),
]
PUBLIC_QUESTION_FIELDS = {'id', 'type', 'prompt', 'options', 'audio_url'}
PRIVATE_FIELDS = {'answer', 'accepted_answers', 'explanation', 'listening_text', 'audio_file'}
QUESTION_MIX = Counter(listening_choice=10, fill_blank=12, rewrite=8, reading_choice=10)


def source(kind):
    path = ROOT / f'builtin_exams/shanghai-grade5-term1-{kind}.json'
    return json.loads(path.read_text(encoding='utf-8'))


def no_generation(*_args):
    pytest.fail('Published curriculum papers must not generate questions or speech')


def compact(value):
    return re.sub(r'\s+', ' ', value).strip()


@pytest.mark.parametrize('kind,scope,units', PAPERS)
def test_grade5_scope_knowledge_and_fixed_question_mix(kind, scope, units):
    data = source(kind)
    paper = catalog.public_paper(data['id'])
    assert (paper['grade'], paper['term']) == (5, 1)
    assert scope in paper['name']
    assert '五年级上册' in paper['name']
    assert paper['question_count'] == 40
    assert paper['listening_count'] == 10
    assert not PRIVATE_FIELDS.intersection(paper)
    questions = catalog.questions(data['id'])
    assert Counter(question['type'] for question in questions) == QUESTION_MIX
    assert [question['type'] for question in questions] == (
        ['listening_choice'] * 10 + ['fill_blank'] * 12
        + ['rewrite'] * 8 + ['reading_choice'] * 10
    )
    assert len({question['id'] for question in questions}) == len(questions)
    sections = {section['id']: section for section in data['knowledge']['sections']}
    unit_ids = {key for key in sections if re.fullmatch(r'u\d+', key)}
    assert unit_ids == {f'u{number}' for number in units}
    if kind == 'midterm':
        assert 'traditional games' in sections['u2']['title'].lower()
        assert 'amazing plants' in sections['u3']['title'].lower()
    for unit_id in unit_ids:
        section = sections[unit_id]
        assert section['blocks'], unit_id
        assert all(block['title'] and block['items'] for block in section['blocks']), unit_id
    material = catalog.knowledge(data['id'])
    assert material['knowledge'] == data['knowledge']
    assert material['pdf_url'].endswith('/' + data['source_file'])
    assert material['exam_pdf_url'].endswith('/' + data['exam_file'])


@pytest.mark.parametrize('kind,_scope,_units', PAPERS)
def test_grade5_issued_attempts_hide_keys_and_scripts_and_accept_correct_variants(tmp_path, kind, _scope, _units):
    data = source(kind)
    store = ExamPreparationStore(tmp_path / 'exam.sqlite3')
    attempt = store.start_attempt(store.get_paper(data['id']), False, no_generation)
    original = catalog.questions(data['id'])
    answers = {}
    for issued, private in zip(attempt['questions'], original, strict=True):
        assert set(issued) <= PUBLIC_QUESTION_FIELDS
        assert not PRIVATE_FIELDS.intersection(issued)
        assert issued['prompt'] == private['prompt']
        if private['type'] in ('fill_blank', 'rewrite'):
            assert private['answer'] in private['accepted_answers']
            for variant in private['accepted_answers']:
                formatted = '  ' + variant.upper().replace(' ', '   ') + '  '
                result = grade_attempt({**attempt, 'questions': [private]}, {private['id']: formatted})
                assert result['score'] == 100, (private['id'], variant)
            answers[issued['id']] = private['accepted_answers'][-1]
        else:
            answers[issued['id']] = private['answer']
        if private['type'] == 'listening_choice':
            assert issued['audio_url'].startswith('/static/exam-preparation/audio/')
            assert private['listening_text'] not in json.dumps(attempt, ensure_ascii=False)
        else:
            assert 'audio_url' not in issued
    assert store.get_attempt(attempt['id'])['attempt'] == attempt
    result = store.submit_attempt(attempt['id'], answers)
    assert (result['score'], result['correct_count'], result['total']) == (100, 40, 40)
    assert store.mistake_count(data['id']) == 0


@pytest.mark.parametrize('kind,_scope,_units', PAPERS)
def test_grade5_choice_keys_and_reading_passages_match_the_published_questions(kind, _scope, _units):
    data = source(kind)
    passages = {passage['id']: passage for passage in data['reading_passages']}
    assert len(passages) == len(data['reading_passages'])
    used = set()
    letters = Counter()
    for question in data['questions']:
        assert question['prompt'].strip() and question['explanation'].strip(), question['id']
        if question['type'] not in ('listening_choice', 'reading_choice'):
            continue
        options = question['options']
        assert len(options) == 4, question['id']
        assert len({option['id'] for option in options}) == 4, question['id']
        assert len({compact(option['text']).casefold() for option in options}) == 4, question['id']
        ids = [option['id'] for option in options]
        assert question['answer'] in ids, question['id']
        letters[ids.index(question['answer'])] += 1
        if question['type'] == 'reading_choice':
            passage = passages[question['passage_id']]
            used.add(passage['id'])
            assert compact(passage['text']) in compact(question['prompt']), question['id']
            assert compact(question['question_text']) in compact(question['prompt']), question['id']
    assert used == set(passages)
    assert set(letters) == {0, 1, 2, 3}
    assert max(letters.values()) - min(letters.values()) <= 2, letters


def test_all_four_builtin_papers_remain_available_and_new_audio_paths_do_not_collide():
    old = {'builtin-shanghai-grade4-u1-u4', 'builtin-shanghai-grade4-u5-u10-final'}
    new = {source(kind)['id'] for kind, _scope, _units in PAPERS}
    listed = {paper['id'] for paper in catalog.list_papers()}
    assert listed == old | new
    assert len(new) == 2
    paths = [question['audio_file'] for paper_id in listed
             for question in catalog.questions(paper_id) if question['type'] == 'listening_choice']
    assert len(paths) == 40
    assert len(set(paths)) == len(paths)


@pytest.mark.parametrize('kind,_scope,_units', PAPERS)
def test_grade5_handbook_and_exam_pdfs_are_real_published_assets(kind, _scope, _units):
    data = source(kind)
    for name in ('source_file', 'exam_file'):
        path = ROOT / 'static/exam-preparation' / data[name]
        content = path.read_bytes()
        assert content.startswith(b'%PDF-'), path.name
        assert b'%%EOF' in content[-100:], path.name
        assert len(content) > 20000, path.name


@pytest.mark.parametrize('kind,scope,_units', PAPERS)
def test_grade5_pdf_text_uses_the_right_grade_scope_and_includes_answer_and_audio_scripts(kind, scope, _units):
    pypdf = pytest.importorskip('pypdf')
    data = source(kind)
    texts = {}
    for key in ('source_file', 'exam_file'):
        reader = pypdf.PdfReader(ROOT / 'static/exam-preparation' / data[key])
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        texts[key] = compact(text)
        assert '上海五年级上册英语' in texts[key]
        assert scope in texts[key]
        assert '上海四年级上册' not in texts[key]
    for section in data['knowledge']['sections']:
        assert compact(section['title']) in texts['source_file']
    assert '参考答案与解析' in texts['exam_file']
    assert '听力稿' in texts['exam_file']
    for question in data['questions']:
        if question['type'] == 'listening_choice':
            assert compact(question['listening_text']) in texts['exam_file'], question['id']


@pytest.mark.parametrize('kind,_scope,_units', PAPERS)
def test_grade5_ten_fixed_wavs_have_the_expected_pcm_format_and_audible_content(kind, _scope, _units):
    data = source(kind)
    listening = [question for question in catalog.questions(data['id']) if question['type'] == 'listening_choice']
    assert len(listening) == 10
    assert len({question['audio_url'] for question in listening}) == 10
    for question in listening:
        recording = ROOT / question['audio_url'].lstrip('/')
        with wave.open(str(recording)) as audio:
            assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 22050)
            duration = audio.getnframes() / audio.getframerate()
            assert 5 < duration < 180, (recording.name, duration)
            assert any(audio.readframes(audio.getnframes())), recording.name


def test_generic_material_builder_labels_follow_each_catalog_instead_of_hardcoded_grade_four(monkeypatch):
    pytest.importorskip('reportlab')
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    labels = runpy.run_path(str(ROOT / 'scripts/build_builtin_exam_materials.py'))['labels']
    assert labels({'grade': 5, 'term': 1, 'name': '五上期中卷', 'scope': 'U1-U4'}) == (
        '上海五年级上册英语', '期中', 'U1-U4',
    )
    assert labels({'grade': 5, 'term': 2, 'name': '五下期末卷', 'scope': 'U5-U10'}) == (
        '上海五年级下册英语', '期末', 'U5-U10',
    )
    assert labels({'grade': 4, 'term': 2, 'name': '四下期中卷', 'scope': 'U1-U4'}) == (
        '上海四年级下册英语', '期中', 'U1-U4',
    )
