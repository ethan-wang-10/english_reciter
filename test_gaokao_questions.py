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
        'recognition_valid_definition': [option['text'] in recognition_allowed for option in recognition['options']],
        'recognition_parallel_form': [True] * 4,
        'context_grammatical': [True] * 4,
        'context_meaning_fits': [option['text'] in context_allowed for option in context['options']],
        'context_quality': {'natural': True, 'decisive_clues': True, 'answer_revealed': False, 'reason_zh': '存在明确线索。'},
        'feedback_quality': _feedback_quality(),
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


def _feedback_quality():
    return {
        'recognition_explanation_correct': True,
        'recognition_options_parallel': True,
        'translation_correct': True,
        'context_explanation_correct': True,
        'answer_matches_headword': True,
        'reason_zh': '译文和解析与最终题目一致。',
    }


def _pool_audited(
    source: dict,
    raw: dict,
    *,
    recognition_valid=None,
    context_fits=None,
    context_grammatical=None,
    context_quality=None,
) -> str:
    pool, error = questions.build_generation_candidate_pool(source, raw)
    assert error == ''
    correct_zh, recognition_rows, recognition_error = questions._recognition_candidate_rows(
        source,
        raw['recognition_distractors'],
    )
    assert recognition_error == ''
    recognition_options = [row['text'] for row in questions._option_rows(
        [row[1] for row in recognition_rows], correct_zh, f"{source['english']}:pool:recognition",
    )[0]]
    context_options = [row['text'] for row in questions._option_rows(
        questions._clean_distinct_list(
            raw['context_distractors'],
            forbidden=[source['context_answer'], source['english']],
            require_cjk=False,
            limit=12,
        ), source['context_answer'], f"{source['english']}:pool:context",
    )[0]]
    valid_recognition = set(recognition_valid or (correct_zh,))
    fitting_context = set(context_fits or (source['context_answer'],))
    grammatical_context = set(context_grammatical or context_options)
    quality = context_quality or {
        'natural': True,
        'decisive_clues': True,
        'answer_revealed': False,
        'reason_zh': '句子自然且存在明确的唯一限定线索。',
    }
    return json.dumps([{
        'item_id': 'q1',
        'recognition_valid_definition': [
            option in valid_recognition for option in recognition_options
        ],
        'recognition_parallel_form': [True for _ in recognition_options],
        'context_grammatical': [
            option in grammatical_context for option in context_options
        ],
        'context_meaning_fits': [
            option in fitting_context for option in context_options
        ],
        'context_quality': quality,
        'feedback_quality': _feedback_quality(),
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


def test_generation_publishes_only_after_independent_audit(private_question_bank):
    source = _source('novel', 'n. 小说')
    calls = []

    def chat(messages, max_tokens):
        calls.append(messages[-1]['content'])
        if '独立英语试题质检员' in messages[-1]['content']:
            return _pool_audited(source, _generated('novel'))
        return json.dumps([_generated('novel')], ensure_ascii=False)

    result = questions.generate_prompt_checked_and_persist([source], chat)

    assert result['generated_words'] == ['novel']
    assert len(calls) == 4
    assert '先在内部生成候选' in calls[0]
    assert '识义盲审' in calls[1]
    assert '语境选词盲审' in calls[2]
    assert '译文与解析' in calls[3]
    bank = questions.load_bank()
    record = bank['questions']['novel']
    assert record['generation_prompt_version'] == questions.GENERATION_PROMPT_VERSION
    assert record['quality_gate'] == questions.INDEPENDENT_AUDIT_QUALITY_GATE
    assert record['audit_version'] == questions.AUDIT_VERSION
    assert questions.get_question('novel', 'recognition') is not None


def test_generation_output_budget_supports_one_ten_word_request() -> None:
    sources = [
        _source(f'word{index}', f'n. 词{index}')
        for index in range(10)
    ]
    max_tokens_seen = []

    def chat(messages, max_tokens):
        max_tokens_seen.append(max_tokens)
        return 'invalid response'

    _, errors = questions.generate_candidate_records(sources, chat)

    assert max_tokens_seen == [10200]
    assert len(errors) == 10


def test_generation_rejects_more_than_ten_words_in_one_request() -> None:
    sources = [
        _source(f'word{index}', f'n. 词{index}')
        for index in range(11)
    ]

    with pytest.raises(ValueError, match='fixed 10-word limit'):
        questions.generate_candidate_records(
            sources,
            lambda messages, max_tokens: pytest.fail('must reject before AI call'),
        )


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
    assert '"recognition_allowed_hanzi_count_min": 1' in prompt
    assert '"recognition_allowed_hanzi_count_max": 4' in prompt
    assert '"required_context_answer_verbatim": "novel"' in prompt
    assert '"context_validation_min_english_word_count": 16' in prompt
    assert '"context_target_english_word_count_min": 22' in prompt
    assert '"context_target_english_word_count_max": 28' in prompt
    assert '至少保证前三个完全相同' in prompt
    assert '精确出现一次' in prompt
    assert '["错误释义甲", "错误释义乙", "错误释义丙"' in prompt
    assert '程序会自动把 recognition_correct_answer_zh 加入识义题' in prompt


def test_generation_prompt_covers_observed_failure_modes() -> None:
    prompt = questions.build_generation_prompt([
        dict(
            _source('abbess', 'n. 女修道院院长'),
            context_answer='abetting',
        ),
    ])

    assert '“女修道院院长”有 6 个汉字' in prompt
    assert 'english 是 abet 而 required_context_answer_verbatim 是 abetting' in prompt
    assert 'miserable 也可能成立' in prompt
    assert 'abjured 的候选不能包含 renounced' in prompt
    assert '“如坐针毡”“心急如焚”“坐卧不宁”' in prompt
    assert '"recognition_allowed_hanzi_count_min": 4' in prompt
    assert '"recognition_allowed_hanzi_count_max": 8' in prompt


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


def test_generation_repair_feedback_is_kept_in_dynamic_input() -> None:
    prompt = questions.build_generation_prompt(
        [_source('a', 'art. 一个')],
        repair_feedback={
            'a': 'context sentence must contain the exact answer once',
        },
    )
    prefix, dynamic_input = prompt.split(
        '输入（必须为以下 JSON 数组中的每个对象输出一个结果，保持原顺序）：\n',
        1,
    )

    assert 'context sentence must contain the exact answer once' not in prefix
    assert (
        '"previous_failure_to_fix": '
        '"context sentence must contain the exact answer once"'
    ) in dynamic_input


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
    questions.persist_prompt_checked_result({'novel': record}, {})

    assert questions.sources_needing_prompt_refresh([source]) == [source]

    approved, _, _ = questions.audit_question_records(
        {'novel': record}, lambda *args: _audited(source, _generated('novel')),
    )
    questions.persist_generation_result(approved, {})

    assert questions.sources_needing_prompt_refresh([source]) == []


def test_pool_audit_selects_safe_backup_distractors() -> None:
    source = _source('benefit', 'n. 益处；好处')
    raw = _generated('benefit')
    raw['recognition_distractors'] = ['优势', '负担', '风险', '限制', '机会', '成本']
    raw['context_distractors'] = [
        'advantage', 'value', 'help', 'burden', 'obstacle', 'damage',
    ]
    pool, error = questions.build_generation_candidate_pool(source, raw)
    assert error == ''

    approved, rejected, retry = questions.audit_generation_candidate_pools(
        {'benefit': pool},
        lambda messages, max_tokens: _pool_audited(
            source,
            raw,
            recognition_valid=('益处', '优势'),
            context_fits=('benefit', 'advantage', 'value', 'help'),
        ),
    )

    assert rejected == {}
    assert retry == {}
    record = approved['benefit']
    recognition_options = {
        option['text'] for option in record['recognition']['options']
    }
    context_options = {option['text'] for option in record['context']['options']}
    assert recognition_options == {'益处', '负担', '风险', '限制'}
    assert context_options == {'benefit', 'burden', 'obstacle', 'damage'}
    assert record['audit_version'] == questions.AUDIT_VERSION


def test_pool_audit_rejects_grammar_only_distractors() -> None:
    source = _source('abash', 'v. 使尴尬')
    source['context_answer'] = 'abashed'
    raw = _generated('abash')
    raw['context_sentence'] = (
        'Her criticism abashed him during the meeting, and he lowered his head '
        'before quietly apologizing to every colleague in the room.'
    )
    raw['context_distractors'] = ['proud', 'happy', 'calm', 'delighted', 'brave', 'relaxed']
    pool, error = questions.build_generation_candidate_pool(source, raw)
    assert error == ''

    approved, rejected, retry = questions.audit_generation_candidate_pools(
        {'abash': pool},
        lambda messages, max_tokens: _pool_audited(
            source,
            raw,
            context_grammatical=('abashed',),
        ),
    )

    assert approved == {}
    assert retry == {}
    assert 'insufficient safe options' in rejected['abash']


def test_pool_audit_uses_compact_arrays_and_observed_counterexamples() -> None:
    source = _source('novel', 'n. 小说')
    raw = _generated('novel')
    pool, error = questions.build_generation_candidate_pool(source, raw)
    assert error == ''
    requests = []

    def chat(messages, max_tokens):
        requests.append((messages[-1]['content'], max_tokens))
        return _pool_audited(source, raw)

    approved, rejected, retry = questions.audit_generation_candidate_pools(
        {'novel': pool},
        chat,
    )

    assert list(approved) == ['novel']
    assert rejected == {}
    assert retry == {}
    prompt, max_tokens = requests[0]
    assert '"recognition_valid_definition"' in prompt
    context_prompt = requests[1][0]
    assert '"context_meaning_fits"' in context_prompt
    assert 'recognition_verdicts' not in prompt
    assert 'cannot ____ his complaining because it wastes time' in context_prompt
    assert 'stone walls、arches 和 ruins' in context_prompt
    assert 'Her criticism ____ him' in context_prompt
    assert max_tokens == 1300


def test_pool_audit_retries_malformed_boolean_array() -> None:
    source = _source('novel', 'n. 小说')
    raw = _generated('novel')
    pool, error = questions.build_generation_candidate_pool(source, raw)
    assert error == ''
    malformed = json.loads(_pool_audited(source, raw))
    malformed[0]['context_meaning_fits'] = [True, False]

    approved, rejected, retry = questions.audit_generation_candidate_pools(
        {'novel': pool},
        lambda messages, max_tokens: json.dumps(malformed, ensure_ascii=False),
    )

    assert approved == {}
    assert rejected == {}
    assert 'expected 4 JSON booleans' in retry['novel']


def test_rejected_pool_is_regenerated_once_before_publish(private_question_bank) -> None:
    source = _source('novel', 'n. 小说')
    raw = _generated('novel')
    generation_calls = 0
    audit_calls = 0
    generation_prompts = []

    def generate_chat(messages, max_tokens):
        nonlocal generation_calls
        generation_calls += 1
        generation_prompts.append(messages[-1]['content'])
        return json.dumps([raw], ensure_ascii=False)

    def audit_chat(messages, max_tokens):
        nonlocal audit_calls
        audit_calls += 1
        quality = None
        if audit_calls <= 2:
            quality = {
                'natural': True,
                'decisive_clues': False,
                'answer_revealed': False,
                'reason_zh': '首轮语境缺少形成唯一答案的决定性线索。',
            }
        return _pool_audited(source, raw, context_quality=quality)

    result = questions.generate_audited_and_persist(
        [source],
        generate_chat,
        audit_chat=audit_chat,
        max_generation_attempts=2,
    )

    assert generation_calls == 2
    assert audit_calls == 5
    assert '"previous_failure_to_fix":' not in generation_prompts[0]
    assert '"previous_failure_to_fix":' in generation_prompts[1]
    assert '首轮语境缺少形成唯一答案的决定性线索' in generation_prompts[1]
    assert result['generated_words'] == ['novel']
    assert result['failed_words'] == []
    assert questions.get_question('novel', 'context') is not None
    assert questions.pending_candidate_pools() == {}


def test_current_pool_failures_are_tagged_for_automatic_retry(
    private_question_bank,
) -> None:
    source = _source('novel', 'n. 小说')
    raw = _generated('novel')
    pool, error = questions.build_generation_candidate_pool(source, raw)
    assert error == ''

    questions.persist_candidate_pool_result({}, {'broken': 'invalid JSON'})
    questions.persist_candidate_pool_result({'novel': pool}, {})
    questions.persist_candidate_pool_audit_result(
        {},
        {'novel': 'ambiguous context'},
        {},
    )

    failures = questions.load_bank()['failures']
    assert failures['broken']['auto_retry_pipeline_version'] == (
        questions.AUTO_RETRY_PIPELINE_VERSION
    )
    assert failures['novel']['auto_retry_pipeline_version'] == (
        questions.AUTO_RETRY_PIPELINE_VERSION
    )


def test_import_candidate_pool_stays_private_until_audit(private_question_bank) -> None:
    source = _source('novel', 'n. 小说')
    raw = _generated('novel')
    pool, error = questions.build_generation_candidate_pool(source, raw)
    assert error == ''

    questions.persist_candidate_pool_result({'novel': pool}, {})

    assert questions.get_question('novel', 'recognition') is None
    assert list(questions.pending_candidate_pools()) == ['novel']

    approved, rejected, retry = questions.audit_generation_candidate_pools(
        {'novel': pool},
        lambda messages, max_tokens: _pool_audited(source, raw),
    )
    questions.persist_candidate_pool_audit_result(approved, rejected, retry)

    assert questions.get_question('novel', 'recognition') is not None
    assert questions.pending_candidate_pools() == {}


def test_stale_pool_audit_cannot_publish_over_new_candidates(private_question_bank) -> None:
    source = _source('novel', 'n. 小说')
    first_raw = _generated('novel')
    first_pool, error = questions.build_generation_candidate_pool(source, first_raw)
    assert error == ''
    questions.persist_candidate_pool_result({'novel': first_pool}, {})

    approved, rejected, retry = questions.audit_generation_candidate_pools(
        {'novel': first_pool},
        lambda messages, max_tokens: _pool_audited(source, first_raw),
    )

    second_raw = _generated('novel')
    second_raw['recognition_distractors'] = [
        '规则', '表格', '风险', '机会', '限制', '责任',
    ]
    second_pool, error = questions.build_generation_candidate_pool(source, second_raw)
    assert error == ''
    questions.persist_candidate_pool_result({'novel': second_pool}, {})

    questions.persist_candidate_pool_audit_result(
        approved,
        rejected,
        retry,
        expected_pools={'novel': first_pool},
    )

    assert questions.get_question('novel', 'recognition') is None
    assert questions.pending_candidate_pools()['novel']['raw'] == second_raw


def test_failure_attempts_increment_without_removing_completed_questions(
    private_question_bank,
):
    source = _source('stable', 'adj. 稳定的')
    record, _ = questions.finalize_generated_questions(source, _generated('stable'))
    approved, _, _ = questions.audit_question_records(
        {'stable': record}, lambda *args: _audited(source, _generated('stable')),
    )
    questions.persist_generation_result(approved, {'pending': 'timeout'})
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


@pytest.mark.parametrize(('field', 'value'), [
    ('context_sentence', 'During class, the teacher wrote benefit on the board and asked every student to explain ____ in detail.'),
    ('context_sentence', {'text': 'benefit'}),
    ('recognition_explanation_zh', ''),
    ('context_translation_zh', ['错误译文']),
    ('context_explanation_zh', 'English only'),
    ('recognition_distractors', [{'text': '风险'}, '负担', '限制']),
    ('context_distractors', ['wrong_1', 'bad2', 'risk 中文']),
])
def test_generation_rejects_malformed_fields(field, value):
    raw = {**_generated('benefit'), field: value}
    record, error = questions.finalize_generated_questions(_source('benefit', 'n. 益处'), raw)
    assert record is None
    assert error


def test_blind_context_cannot_see_headword_or_feedback():
    source = dict(_source('apply', 'v. 申请'), context_answer='applied')
    raw = {**_generated('apply'), 'context_sentence': _generated('applied')['context_sentence']}
    pool, error = questions.build_generation_candidate_pool(source, raw)
    assert not error
    requests = []

    def chat(messages, max_tokens):
        requests.append(messages[-1]['content'])
        return _pool_audited(source, raw)

    approved, rejected, retry = questions.audit_generation_candidate_pools({'apply': pool}, chat)
    assert list(approved) == ['apply']
    assert not rejected and not retry
    assert len(requests) == 3
    context_data = json.loads(requests[1].split('待审数据 JSON：\n')[1])[0]
    assert set(context_data) == {'item_id', 'prompt', 'options'}
    assert 'apply' not in json.dumps(context_data)
    assert '申请' not in requests[1]
    assert {row['text'] for row in context_data['options']} == {'applied', 'choicea', 'choiceb', 'choicec'}
    assert all(set(row) == {'id', 'text'} for row in context_data['options'])


@pytest.mark.parametrize('failed_field', [
    'recognition_explanation_correct', 'recognition_options_parallel', 'translation_correct',
    'context_explanation_correct', 'answer_matches_headword',
])
def test_feedback_failure_blocks_publication(private_question_bank, failed_field):
    source, raw = _source('benefit', 'n. 益处'), _generated('benefit')
    pool, _ = questions.build_generation_candidate_pool(source, raw)
    questions.persist_candidate_pool_result({'benefit': pool}, {})
    captured = []

    def audit(messages, max_tokens):
        captured.append(messages[-1]['content'])
        reply = json.loads(_pool_audited(source, raw))
        reply[0]['feedback_quality'][failed_field] = False
        return json.dumps(reply)

    approved, rejected, retry = questions.audit_generation_candidate_pools({'benefit': pool}, audit)
    assert not approved and not retry
    assert failed_field in rejected['benefit']
    payload = json.loads(captured[-1].split('待审数据 JSON：\n')[1])[0]
    assert payload['context']['translation_zh'] == raw['context_translation_zh']
    assert payload['context']['explanation_zh'] == raw['context_explanation_zh']
    assert payload['recognition']['explanation_zh'] == raw['recognition_explanation_zh']
    assert len(payload['context']['options']) == 4
    questions.persist_candidate_pool_audit_result(approved, rejected, retry, expected_pools={'benefit': pool})
    assert questions.get_question('benefit', 'context') is None


@pytest.mark.parametrize('bad_response', ['missing', 'duplicate', 'malformed'])
def test_partial_audit_retries_only_unreliable_item(bad_response):
    source, raw = _source('benefit', 'n. 益处'), _generated('benefit')
    record, _ = questions.finalize_generated_questions(source, raw)
    calls = []

    def chat(messages, max_tokens):
        items = json.loads(messages[-1]['content'].split('待审数据 JSON：\n')[1])
        calls.append([item['item_id'] for item in items])
        rows = [{**json.loads(_audited(source, raw))[0], 'item_id': item['item_id']} for item in items]
        if len(calls) == 1:
            if bad_response == 'missing':
                rows = rows[:1]
            elif bad_response == 'duplicate':
                rows.append(rows[1])
            else:
                rows[1]['recognition_valid_definition'] = ['true'] * 4
        return json.dumps(rows)

    approved, errors = questions._blind_option_audit(
        {'benefit': record['recognition'], 'second': record['recognition']}, chat, 'recognition',
    )
    assert set(approved) == {'benefit', 'second'}
    assert not errors
    assert calls == [['q1', 'q2'], ['q2']]


def test_failed_audit_resumes_existing_candidate(private_question_bank):
    source, raw = _source('benefit', 'n. 益处'), _generated('benefit')
    generation_calls = []

    def generate(*args):
        generation_calls.append(1)
        return json.dumps([raw])

    first = questions.generate_audited_and_persist([source], generate, audit_chat=lambda *args: None)
    assert first['audit_retry_words'] == ['benefit']
    second = questions.generate_audited_and_persist(
        [source], generate, audit_chat=lambda *args: _pool_audited(source, raw),
    )
    assert second['generated_words'] == ['benefit']
    assert len(generation_calls) == 1


def test_truncated_generation_splits_retry_and_keeps_feedback(private_question_bank):
    sources = [_source(word, 'n. 益处') for word in ('alpha', 'bravo', 'charlie', 'delta')]
    sizes = []

    class Truncated(str):
        finish_reason = 'length'

    def chat(messages, max_tokens):
        items = json.loads(messages[-1]['content'].split('输入（必须为以下 JSON 数组中的每个对象输出一个结果，保持原顺序）：\n')[1])
        sizes.append(len(items))
        if len(sizes) == 1:
            return Truncated('[]')
        assert all('truncated' in item['previous_failure_to_fix'] for item in items)
        return '[]'

    questions.generate_audited_and_persist(sources, chat)
    assert sizes == [4, 2, 2]


def test_feedback_change_invalidates_in_flight_audit(private_question_bank):
    source, raw = _source('benefit', 'n. 益处'), _generated('benefit')
    original, _ = questions.build_generation_candidate_pool(source, raw)
    questions.persist_candidate_pool_result({'benefit': original}, {})
    approved, rejected, retry = questions.audit_generation_candidate_pools(
        {'benefit': original}, lambda *args: _pool_audited(source, raw),
    )
    changed, _ = questions.build_generation_candidate_pool(source, {**raw, 'context_translation_zh': '新译文，必须重新审核。'})
    questions.persist_candidate_pool_result({'benefit': changed}, {})
    questions.persist_candidate_pool_audit_result(approved, rejected, retry, expected_pools={'benefit': original})
    assert questions.get_question('benefit', 'context') is None


def test_publish_cannot_stamp_an_unaudited_record(private_question_bank):
    record, _ = questions.finalize_generated_questions(_source('benefit', 'n. 益处'), _generated('benefit'))
    with pytest.raises(ValueError, match='complete audit'):
        questions.persist_generation_result({'benefit': record}, {})


def test_auto_retry_budget_survives_semantic_regeneration(private_question_bank):
    from datetime import datetime, timedelta, timezone

    source, raw = _source('benefit', 'n. 益处'), _generated('benefit')
    pool, _ = questions.build_generation_candidate_pool(source, raw)
    for attempt in range(questions.AUTO_RETRY_LIMIT):
        questions.persist_candidate_pool_result({'benefit': pool}, {})
        questions.persist_candidate_pool_audit_result({}, {'benefit': 'ambiguous'}, {})
        failure = questions.failure_records()['benefit']
        assert failure['automatic_attempts'] == attempt + 1
    assert failure['manual_review_required'] is True
    assert questions.automatic_retry_queue(datetime.now(timezone.utc) + timedelta(days=2)) == {}


def test_feedback_repair_preserves_approved_stem_and_options(private_question_bank):
    source, raw = _source('benefit', 'n. 益处'), _generated('benefit')
    generation_calls, feedback_calls = [], []

    def generate(messages, max_tokens):
        generation_calls.append(messages[-1]['content'])
        if len(generation_calls) == 1:
            return json.dumps([raw])
        assert '"repair_only_fields": ["context_translation_zh"]' in generation_calls[-1]
        return json.dumps([{
            **raw,
            'context_translation_zh': '修正后的准确译文。',
            'context_sentence': 'A changed and invalid stem.',
            'context_distractors': ['wrong'],
        }])

    def audit(messages, max_tokens):
        reply = json.loads(_pool_audited(source, raw))
        if '校对最终四选项' in messages[-1]['content']:
            feedback_calls.append(1)
            if len(feedback_calls) == 1:
                reply[0]['feedback_quality']['translation_correct'] = False
        return json.dumps(reply)

    result = questions.generate_audited_and_persist([source], generate, audit_chat=audit)
    assert result['generated_words'] == ['benefit']
    published = questions.get_question('benefit', 'context')
    assert published['translation_zh'] == '修正后的准确译文。'
    assert published['prompt'] == raw['context_sentence'].replace('benefit', '____')
    assert len(published['options']) == 4


def test_existing_candidate_with_old_fingerprint_can_be_reaudited(private_question_bank):
    source, raw = _source('benefit', 'n. 益处'), _generated('benefit')
    pool, _ = questions.build_generation_candidate_pool(source, raw)
    questions.persist_candidate_pool_result({'benefit': pool}, {})
    bank = questions.load_bank()
    bank['candidates']['benefit']['candidate_id'] = 'legacy-fingerprint'
    questions._write_bank_unlocked(bank)
    result = questions.generate_audited_and_persist(
        [source], lambda *args: pytest.fail('existing candidate must not be regenerated'),
        audit_chat=lambda *args: _pool_audited(source, raw),
    )
    assert result['generated_words'] == ['benefit']
    assert questions.get_question('benefit', 'context') is not None


def test_new_structural_failure_is_rejected_for_regeneration():
    source, raw = _source('benefit', 'n. 益处'), _generated('benefit')
    pool, _ = questions.build_generation_candidate_pool(source, raw)
    pool['raw']['recognition_explanation_zh'] = ''
    approved, rejected, retry = questions.audit_generation_candidate_pools(
        {'benefit': pool}, lambda *args: pytest.fail('invalid structure must not reach AI'),
    )
    assert not approved and not retry
    assert 'recognition_explanation_zh' in rejected['benefit']


def test_concurrent_candidate_change_is_not_counted_as_published(private_question_bank):
    source, raw = _source('benefit', 'n. 益处'), _generated('benefit')

    def audit(messages, max_tokens):
        if '校对最终四选项' in messages[-1]['content']:
            replacement, _ = questions.build_generation_candidate_pool(
                source, {**raw, 'context_translation_zh': '并发修改后的待审译文。'},
            )
            questions.persist_candidate_pool_result({'benefit': replacement}, {})
        return _pool_audited(source, raw)

    result = questions.generate_audited_and_persist(
        [source], lambda *args: json.dumps([raw]), audit_chat=audit,
    )
    assert result['generated'] == 0
    assert result['audit_retry_words'] == ['benefit']
    assert questions.get_question('benefit', 'context') is None
