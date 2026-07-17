import json

import pytest

import gaokao_questions as questions


@pytest.fixture
def private_question_bank(monkeypatch, tmp_path):
    monkeypatch.setattr(questions, 'QUESTION_BANK_FILE', tmp_path / 'gaokao_questions_v2.json')
    monkeypatch.setattr(questions, 'QUESTION_BANK_LOCK_FILE', tmp_path / '.questions.lock')
    monkeypatch.setattr(questions, '_cache', None)
    monkeypatch.setattr(questions, '_cache_mtime_ns', -1)
    return tmp_path


def _source(english: str, chinese: str) -> dict:
    return {
        'english': english,
        'chinese': chinese,
        'level': '高中',
        'phonetic': f'/{english}/',
        'pos': 'n',
        'context_sentence': f'This sentence needs ____ for {english}.',
        'context_answer': english,
        'context_cn': f'这个句子需要{chinese}。',
        'source_hash': f'hash-{english}',
    }


def _generated(english: str) -> dict:
    return {
        'english': english,
        'recognition_distractors': ['错误释义甲', '错误释义乙', '错误释义丙'],
        'recognition_explanation_zh': f'{english} 的核心含义辨析。',
        'context_sentence': (
            f'During today\'s vocabulary lesson, the teacher clearly wrote {english} '
            'on the board for everyone.'
        ),
        'context_translation_zh': f'在今天的词汇课上，老师清楚地把 {english} 写在黑板上给大家看。',
        'context_distractors': ['choicea', 'choiceb', 'choicec'],
        'context_explanation_zh': f'句中需要 {english}。',
    }


def _audited(english: str, *acceptable: str) -> str:
    allowed = set(acceptable or (english,))
    return json.dumps([{
        'item_id': 'q1',
        'option_verdicts': [
            {
                'option': option,
                'acceptable': option in allowed,
                'reason_zh': '逐项代入后的质检理由。',
            }
            for option in [english, 'choicea', 'choiceb', 'choicec']
        ],
    }], ensure_ascii=False)


def test_source_masks_existing_example_once():
    source = questions.source_from_wordbank_row({
        'english': 'apply',
        'chinese': 'v. 申请；应用',
        'level': '高中',
        'example1': 'She applied for the course yesterday.',
        'example1_form': 'applied',
        'example1_cn': '她昨天申请了这门课程。',
    })

    assert source is not None
    assert source['context_sentence'] == 'She ____ for the course yesterday.'
    assert source['context_answer'] == 'applied'


def test_public_question_never_exposes_answer_or_post_answer_feedback():
    record, error = questions.finalize_generated_questions(
        _source('benefit', 'n. 益处；好处'),
        _generated('benefit'),
    )

    assert error == ''
    context = questions.public_question(record['context'])
    assert 'answer_option_id' not in context
    assert 'translation_zh' not in context
    assert 'explanation_zh' not in context
    assert questions.check_answer(record['context'], record['context']['answer_option_id'])


def test_generation_is_reentrant_after_partial_batch_success(private_question_bank):
    apple = _source('apple', 'n. 苹果')
    book = _source('book', 'n. 书')
    calls = []

    def partial_chat(messages, max_tokens):
        if '独立英语试题质检员' in messages[-1]['content']:
            return _audited('apple')
        calls.append(messages[-1]['content'])
        return json.dumps([_generated('apple')], ensure_ascii=False)

    first = questions.generate_and_persist([apple, book], partial_chat)
    assert first['generated_words'] == ['apple']
    assert first['failed_words'] == ['book']
    bank = questions.load_bank()
    assert 'apple' in bank['questions']
    assert bank['failures']['book']['attempts'] == 1

    def resumed_chat(messages, max_tokens):
        if '独立英语试题质检员' in messages[-1]['content']:
            return _audited('book')
        calls.append(messages[-1]['content'])
        return json.dumps([_generated('book')], ensure_ascii=False)

    second = questions.generate_and_persist([apple, book], resumed_chat)
    assert second['pending'] == 1
    assert second['generated_words'] == ['book']
    assert '"english": "book"' in calls[-1]
    assert '"english": "apple"' not in calls[-1]
    bank = questions.load_bank()
    assert set(bank['questions']) == {'apple', 'book'}
    assert 'book' not in bank['failures']

    changed_book = dict(book, source_hash='hash-book-updated')
    assert questions.missing_sources([apple, changed_book]) == [changed_book]


def test_failure_attempts_increment_without_removing_completed_questions(
    private_question_bank,
):
    source = _source('stable', 'adj. 稳定的')
    record, _ = questions.finalize_generated_questions(source, _generated('stable'))
    questions.persist_generation_result({'stable': record}, {'pending': 'timeout'})
    questions.persist_generation_result({}, {'pending': 'connection reset'})

    bank = questions.load_bank()
    assert 'stable' in bank['questions']
    assert bank['failures']['pending']['attempts'] == 2
    assert bank['failures']['pending']['last_error'] == 'connection reset'


def test_invalid_or_duplicate_distractors_are_rejected():
    raw = _generated('clear')
    raw['recognition_distractors'] = ['清楚的', '清楚的', '']

    record, error = questions.finalize_generated_questions(
        _source('clear', 'adj. 清楚的'),
        raw,
    )

    assert record is None
    assert 'three distinct Chinese distractors' in error


def test_short_generic_context_is_rejected_before_it_reaches_learners():
    raw = _generated('get')
    raw['context_sentence'] = 'I need to get a new book.'
    raw['context_distractors'] = ['find', 'buy', 'read']

    record, error = questions.finalize_generated_questions(
        _source('get', 'v. 获得；得到'),
        raw,
    )

    assert record is None
    assert 'too short to disambiguate' in error


def test_semantic_audit_rejects_a_long_context_with_multiple_valid_answers():
    raw = _generated('get')
    raw['context_sentence'] = (
        'After work today, I decided that I need to get a new book for class.'
    )
    raw['context_distractors'] = ['find', 'buy', 'read']

    def chat(messages, max_tokens):
        if '独立英语试题质检员' not in messages[-1]['content']:
            return json.dumps([raw], ensure_ascii=False)
        return json.dumps([{
            'item_id': 'q1',
            'option_verdicts': [
                {'option': 'get', 'acceptable': True, 'reason_zh': '可以表示得到书。'},
                {'option': 'find', 'acceptable': True, 'reason_zh': '可以表示找到书。'},
                {'option': 'buy', 'acceptable': True, 'reason_zh': '可以表示买书。'},
                {'option': 'read', 'acceptable': True, 'reason_zh': '可以表示读书。'},
            ],
        }], ensure_ascii=False)

    records, errors = questions.generate_question_records(
        [_source('get', 'v. 获得；得到')],
        chat,
    )

    assert records == {}
    assert 'semantic audit rejected context' in errors['get']
    assert 'buy' in errors['get']
