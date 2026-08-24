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
        'recognition_distractors': ['负担', '风险', '限制'],
        'recognition_explanation_zh': f'{english} 的核心含义辨析。',
        'context_sentence': (
            f'During today\'s vocabulary lesson, the teacher clearly wrote {english} '
            'on the board for everyone to study carefully afterward.'
        ),
        'context_translation_zh': f'在今天的词汇课上，老师清楚地把 {english} 写在黑板上给大家看。',
        'context_distractors': ['choicea', 'choiceb', 'choicec'],
        'context_explanation_zh': f'句中需要 {english}。',
    }


def _audited(
    source: dict,
    raw: dict,
    *,
    recognition_acceptable=None,
    context_acceptable=None,
) -> str:
    record, error = questions.finalize_generated_questions(source, raw)
    assert error == ''
    recognition = record['recognition']
    context = record['context']
    recognition_answer = next(
        option['text']
        for option in recognition['options']
        if option['id'] == recognition['answer_option_id']
    )
    context_answer = next(
        option['text']
        for option in context['options']
        if option['id'] == context['answer_option_id']
    )
    recognition_allowed = set(recognition_acceptable or (recognition_answer,))
    context_allowed = set(context_acceptable or (context_answer,))
    return json.dumps([{
        'item_id': 'q1',
        'recognition_verdicts': [
            {
                'option': option['text'],
                'acceptable': option['text'] in recognition_allowed,
                'reason_zh': '逐项代入后的质检理由。',
            }
            for option in recognition['options']
        ],
        'context_verdicts': [
            {
                'option': option['text'],
                'acceptable': option['text'] in context_allowed,
                'reason_zh': '逐项代入后的质检理由。',
            }
            for option in context['options']
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


def test_recognition_uses_one_balanced_core_sense_per_option():
    record, error = questions.finalize_generated_questions(
        _source('benefit', 'n. 益处；好处；优势'),
        _generated('benefit'),
    )

    assert error == ''
    options = [option['text'] for option in record['recognition']['options']]
    assert '益处' in options
    assert all(not any(separator in text for separator in '；;、/') for text in options)
    lengths = [len(text) for text in options]
    assert max(lengths) - min(lengths) <= 2


def test_recognition_rejects_option_length_leak():
    raw = _generated('benefit')
    raw['recognition_distractors'] = ['负担', '风险', '极其复杂的长期限制']

    record, error = questions.finalize_generated_questions(
        _source('benefit', 'n. 益处；好处'),
        raw,
    )

    assert record is None
    assert 'option lengths differ' in error


def test_generation_is_reentrant_after_partial_batch_success(private_question_bank):
    apple = _source('apple', 'n. 苹果')
    book = _source('book', 'n. 书')
    calls = []

    def partial_chat(messages, max_tokens):
        if '独立英语试题质检员' in messages[-1]['content']:
            return _audited(apple, _generated('apple'))
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
            return _audited(book, _generated('book'))
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


def test_prompt_checked_generation_publishes_with_one_ai_call(private_question_bank):
    source = _source('novel', 'n. 小说')
    calls = []

    def chat(messages, max_tokens):
        calls.append(messages[-1]['content'])
        return json.dumps([_generated('novel')], ensure_ascii=False)

    result = questions.generate_prompt_checked_and_persist([source], chat)

    assert result['generated_words'] == ['novel']
    assert len(calls) == 1
    assert '先在内部生成候选' in calls[0]
    bank = questions.load_bank()
    record = bank['questions']['novel']
    assert record['generation_prompt_version'] == questions.GENERATION_PROMPT_VERSION
    assert record['quality_gate'] == questions.SELF_CHECK_QUALITY_GATE
    assert 'audit_version' not in record
    assert questions.get_question('novel', 'recognition') is not None


def test_generation_output_budget_supports_one_thirty_word_request() -> None:
    sources = [
        _source(f'word{index}', f'n. 词{index}')
        for index in range(30)
    ]
    max_tokens_seen = []

    def chat(messages, max_tokens):
        max_tokens_seen.append(max_tokens)
        return 'invalid response'

    _, errors = questions.generate_candidate_records(sources, chat)

    assert max_tokens_seen == [28200]
    assert len(errors) == 30


def test_generation_diagnostics_include_raw_output_and_validation_trace() -> None:
    source = _source('novel', 'n. 小说')
    raw = _generated('novel')
    raw['recognition_distractors'] = ['小说', '诗歌']
    events = []

    records, errors = questions.generate_candidate_records(
        [source],
        lambda messages, max_tokens: json.dumps([raw], ensure_ascii=False),
        diagnostic=events.append,
    )

    assert records == {}
    assert errors == {
        'novel': 'recognition requires three distinct Chinese distractors with one sense each',
    }
    assert [event['event'] for event in events] == [
        'request',
        'response',
        'parse',
        'validation',
    ]
    assert events[0]['prompt_chars'] == len(events[0]['prompt'])
    assert events[1]['raw_response'] == json.dumps([raw], ensure_ascii=False)
    validation = events[-1]
    assert validation['recognition']['raw_count'] == 2
    assert validation['recognition']['accepted_item_count'] == 1
    assert validation['recognition']['items'][0]['duplicate_or_real_sense'] is True
    assert validation['context']['answer_match_count'] == 1
    assert validation['context']['sentence_word_count'] >= 16


def test_generation_prompt_exposes_machine_checkable_constraints() -> None:
    prompt = questions.build_generation_prompt([
        _source('novel', 'n. 小说；长篇故事'),
    ])

    assert '"recognition_correct_answer_zh": "小说"' in prompt
    assert '"forbidden_recognition_senses_zh": ["小说", "长篇故事"]' in prompt
    assert '"recognition_required_hanzi_count": 2' in prompt
    assert '"context_min_english_word_count": 16' in prompt
    assert '至少保证其中 3 个与指定汉字数相同' in prompt
    assert '精确答案只出现一次' in prompt
    assert '["错误释义甲", "错误释义乙", "错误释义丙"' in prompt
    assert '程序会自动把 recognition_correct_answer_zh 加入识义题' in prompt


def test_recognition_core_sense_strips_long_part_of_speech_prefix() -> None:
    assert questions._recognition_core_sense('article 一个') == '一个'
    assert questions._recognition_core_sense('determiner: 这些') == '这些'


def test_recognition_uses_later_candidates_when_earlier_values_are_invalid() -> None:
    raw = _generated('abhor')
    raw['recognition_distractors'] = [
        '憎恶',
        '赞美',
        '极其复杂的长期限制',
        '忽视',
        '允许',
        '赞美',
    ]

    record, error = questions.finalize_generated_questions(
        _source('abhor', 'v. 憎恶'),
        raw,
    )

    assert error == ''
    option_texts = {option['text'] for option in record['recognition']['options']}
    assert option_texts == {'憎恶', '赞美', '忽视', '允许'}


def test_generation_prompt_keeps_dynamic_input_after_cacheable_rules() -> None:
    first = questions.build_generation_prompt([_source('novel', 'n. 小说')])
    second = questions.build_generation_prompt([_source('apple', 'n. 苹果')])
    input_marker = '输入（必须为以下 JSON 数组中的每个对象输出一个结果，保持原顺序）：\n'

    first_prefix, first_input = first.split(input_marker, 1)
    second_prefix, second_input = second.split(input_marker, 1)

    assert first_prefix == second_prefix
    assert '规则：' in first_prefix
    assert '输出前必须逐对象检查' in first_prefix
    assert '"english": "novel"' in first_input
    assert '"english": "apple"' in second_input


def test_prompt_checked_result_exposes_failure_reasons(private_question_bank) -> None:
    source = _source('novel', 'n. 小说')

    result = questions.generate_prompt_checked_and_persist(
        [source],
        lambda messages, max_tokens: 'invalid response',
    )

    assert result['failure_errors'] == {
        'novel': 'AI response is missing a valid JSON array',
    }


def test_prompt_version_refresh_is_resumable(private_question_bank):
    source = _source('novel', 'n. 小说')
    record, error = questions.finalize_generated_questions(source, _generated('novel'))
    assert error == ''
    questions.persist_generation_result({'novel': record}, {})

    assert questions.sources_needing_prompt_refresh([source]) == [source]

    questions.persist_prompt_checked_result({'novel': record}, {})

    assert questions.sources_needing_prompt_refresh([source]) == []


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


def test_context_with_fifteen_words_is_rejected_consistently_with_prompt():
    raw = _generated('get')
    raw['context_sentence'] = (
        'Today our teacher asked everyone to get one useful reference book from the library immediately.'
    )

    record, error = questions.finalize_generated_questions(
        _source('get', 'v. 获得；得到'),
        raw,
    )

    assert record is None
    assert 'too short to disambiguate' in error


def test_semantic_audit_rejects_a_long_context_with_multiple_valid_answers():
    raw = _generated('get')
    raw['context_sentence'] = (
        'After work today, I decided that I need to get a new book for class tomorrow.'
    )
    raw['context_distractors'] = ['find', 'buy', 'read']

    def chat(messages, max_tokens):
        if '独立英语试题质检员' not in messages[-1]['content']:
            return json.dumps([raw], ensure_ascii=False)
        return _audited(
            _source('get', 'v. 获得；得到'),
            raw,
            context_acceptable=('get', 'find', 'buy', 'read'),
        )

    records, errors = questions.generate_question_records(
        [_source('get', 'v. 获得；得到')],
        chat,
    )

    assert records == {}
    assert 'semantic audit rejected context' in errors['get']
    assert 'buy' in errors['get']


def test_semantic_audit_rejects_synonymous_recognition_distractor():
    source = _source('benefit', 'n. 益处；好处')
    raw = _generated('benefit')
    raw['recognition_distractors'] = ['优势', '负担', '风险']
    record, error = questions.finalize_generated_questions(source, raw)
    assert error == ''

    approved, rejected, retry_errors = questions.audit_question_records(
        {'benefit': record},
        lambda messages, max_tokens: _audited(
            source,
            raw,
            recognition_acceptable=('益处', '优势'),
        ),
    )

    assert approved == {}
    assert retry_errors == {}
    assert 'semantic audit rejected recognition' in rejected['benefit']
    assert '优势' in rejected['benefit']


def test_central_audit_retries_without_regenerating_candidate(private_question_bank):
    source = _source('apple', 'n. 苹果')
    generation_calls = 0

    def generate_chat(messages, max_tokens):
        nonlocal generation_calls
        generation_calls += 1
        return json.dumps([_generated('apple')], ensure_ascii=False)

    generated = questions.generate_candidates_and_persist([source], generate_chat)

    assert generated['generated_words'] == ['apple']
    assert generation_calls == 1
    bank = questions.load_bank()
    assert 'apple' in bank['candidates']
    assert 'apple' not in bank['questions']
    assert questions.get_question('apple', 'recognition') is None

    retry = questions.audit_candidates_and_persist(
        lambda messages, max_tokens: 'not valid json',
    )

    assert retry['retry_words'] == ['apple']
    bank = questions.load_bank()
    assert bank['candidates']['apple']['audit_attempts'] == 1
    assert 'apple' not in bank['questions']

    approved = questions.audit_candidates_and_persist(
        lambda messages, max_tokens: _audited(source, _generated('apple')),
    )

    assert approved['approved_words'] == ['apple']
    assert generation_calls == 1
    bank = questions.load_bank()
    assert 'apple' not in bank['candidates']
    assert bank['questions']['apple']['audit_version'] == questions.AUDIT_VERSION
    assert questions.get_question('apple', 'recognition') is not None


def test_rejected_candidate_is_recorded_and_can_be_regenerated(private_question_bank):
    source = _source('benefit', 'n. 益处；好处')
    raw = _generated('benefit')
    raw['recognition_distractors'] = ['优势', '负担', '风险']
    questions.generate_candidates_and_persist(
        [source],
        lambda messages, max_tokens: json.dumps([raw], ensure_ascii=False),
    )

    result = questions.audit_candidates_and_persist(
        lambda messages, max_tokens: _audited(
            source,
            raw,
            recognition_acceptable=('益处', '优势'),
        ),
    )

    assert result['rejected_words'] == ['benefit']
    bank = questions.load_bank()
    assert 'benefit' not in bank['candidates']
    assert bank['rejections']['benefit']['record']['word_key'] == 'benefit'
    assert questions.missing_sources([source]) == [source]


def test_old_v2_record_without_current_audit_is_not_served(private_question_bank):
    source = _source('apple', 'n. 苹果')
    record, error = questions.finalize_generated_questions(source, _generated('apple'))
    assert error == ''
    questions.QUESTION_BANK_FILE.write_text(
        json.dumps({
            'schema': questions.BANK_SCHEMA,
            'version': questions.BANK_VERSION,
            'questions': {'apple': record},
        }, ensure_ascii=False),
        encoding='utf-8',
    )
    questions._cache = None
    questions._cache_mtime_ns = -1

    assert questions.approved_question_count() == 0
    assert questions.get_question('apple', 'recognition') is None
    assert questions.missing_sources([source]) == [source]


def test_old_pending_candidate_does_not_block_balanced_regeneration(private_question_bank):
    source = _source('apple', 'n. 苹果')
    record, error = questions.finalize_generated_questions(source, _generated('apple'))
    assert error == ''
    record.pop('recognition_format_version')
    questions.QUESTION_BANK_FILE.write_text(
        json.dumps({
            'schema': questions.BANK_SCHEMA,
            'version': questions.BANK_VERSION,
            'candidates': {'apple': {'record': record}},
        }, ensure_ascii=False),
        encoding='utf-8',
    )
    questions._cache = None
    questions._cache_mtime_ns = -1

    assert questions.has_pending_candidate('apple') is False
    assert questions.pending_candidate_records() == {}
    assert questions.missing_sources([source]) == [source]
