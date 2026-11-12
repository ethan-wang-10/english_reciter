"""Learning HTTP handlers with explicit application services."""
from __future__ import annotations

from flask import Blueprint


def register_routes(app, services):
    blueprint = Blueprint('learning', __name__)

    @blueprint.route('/api/words/status', methods=['GET'])
    @services.token_required
    def get_status(username):
        """获取学习状态"""
        try:
            with services.user_reciter_session(username) as reciter:
                all_words = []
                today_d = services.china_today()
                for w in reciter.all_words:
                    csv_row = services.lookup_csv_word(w.english)
                    nd = w.next_review_date
                    is_co = nd < today_d
                    examples_list: services.List[dict] = []
                    if csv_row:
                        examples_list = services.examples_from_csv_row(csv_row)
                    if not examples_list and getattr(w, 'example', None):
                        raw = (w.example or '').strip()
                        if raw:
                            if '_' in raw:
                                a, b = raw.split('_', 1)
                                examples_list = [{'en': a.strip(), 'cn': b.strip()}]
                            else:
                                examples_list = [{'en': raw, 'cn': ''}]
                    ex_text = ''
                    if examples_list:
                        fe = examples_list[0]
                        ex_text = services.merged_example_from_pair(fe.get('en', ''), fe.get('cn', ''))
                    display_zh = w.chinese
                    if csv_row and (csv_row.get('chinese') or '').strip():
                        display_zh = (csv_row.get('chinese') or '').strip()
                    row_payload = {'english': w.english, 'chinese': display_zh, 'phonetic': csv_row.get('phonetic', '') if csv_row else '', 'level': (csv_row.get('level') or '').strip() if csv_row else '', 'example': ex_text, 'examples': examples_list, 'success_count': w.success_count, 'max_success_count': reciter.config.MAX_SUCCESS_COUNT, 'review_round': w.review_round, 'review_count': w.review_count, 'next_review_date': nd.isoformat(), 'remaining_days': (nd - today_d).days, 'is_carryover': is_co, 'carryover_days': (today_d - nd).days if is_co else 0, **reciter.review_state_payload(w)}
                    if csv_row:
                        csl = csv_row.get('chinese_sense_lines')
                        if isinstance(csl, list) and csl:
                            row_payload['chinese_sense_lines'] = [str(x).strip() for x in csl if str(x).strip()]
                    all_words.append(row_payload)
                overview_stats = services._summary_payload_from_reciter(reciter)['stats']
                stats = {**overview_stats, 'total_words': len(all_words), 'mastered_words': len(reciter.mastered_words), 'avg_review_count': sum((w['review_count'] for w in all_words)) / len(all_words) if all_words else 0, 'avg_mastery_percent': sum((w['mastery']['overall_percent'] for w in all_words)) / len(all_words) if all_words else 0}
                return (services.jsonify({'words': all_words, 'stats': stats}), 200)
        except Exception as e:
            services.logger.error(f'获取状态失败: {e}')
            return (services.jsonify({'error': '服务器内部错误'}), 500)

    @blueprint.route('/api/words/summary', methods=['GET'])
    @services.token_required
    def get_words_summary(username):
        """获取导航与首屏所需轻量统计，避免为统计拉取完整单词列表。"""
        try:
            with services.user_reciter_session(username) as reciter:
                return (services.jsonify(services._summary_payload_from_reciter(reciter)), 200)
        except Exception as e:
            services.logger.error(f'获取轻量统计失败: {e}')
            return (services.jsonify({'error': '服务器内部错误'}), 500)

    @blueprint.route('/api/words/review', methods=['GET'])
    @services.token_required
    def get_review_list(username):
        """获取今日复习列表（从CSV中补充 example_form、随机选择例句）"""
        try:
            listening_available = services._reliable_listening_available()
            with services.user_reciter_session(username) as reciter:
                task_bundle = reciter.get_today_learning_plan(listening_available=listening_available, new_words_first=services._new_words_first_requested())
                payload = services._review_words_payload(reciter, task_bundle['words'], task_bundle, listening_available=listening_available)
                reciter.save_learning_data(backup=False)
                return (services.jsonify(payload), 200)
        except Exception as e:
            services.logger.error(f'获取复习列表失败: {e}')
            return (services.jsonify({'error': '服务器内部错误'}), 500)

    @blueprint.route('/api/words/extra-review', methods=['GET'])
    @services.token_required
    def get_extra_review_list(username):
        """今日无待复习时：按复习次数最少优先、同层随机抽取 10 个加练词。"""
        try:
            with services.user_reciter_session(username) as reciter:
                task_bundle = reciter.get_today_learning_plan(listening_available=services._reliable_listening_available())
                if int((task_bundle.get('plan') or {}).get('remaining') or 0) > 0:
                    return (services.jsonify({'error': '请先完成今日学习任务，再开始随机加练'}), 409)
                bonus_session_id, picked = reciter.create_bonus_practice_session(services.BONUS_PRACTICE_WORD_COUNT)
                words = []
                for w in picked:
                    nd = w.next_review_date
                    state_payload = reciter.review_state_payload(w)
                    item = {'english': w.english, 'chinese': w.chinese, 'success_count': w.success_count, 'max_success_count': reciter.config.MAX_SUCCESS_COUNT, 'review_count': w.review_count, 'example': w.example, 'example_form': '', 'scheduled_due_date': nd.isoformat(), 'is_carryover': False, 'carryover_days': 0, 'examples': [], 'task_id': '', 'task_item_id': '', 'task_reason': 'bonus', 'bonus_session_id': bonus_session_id, 'exercise_type': 'spelling', 'mastery': state_payload['mastery'], 'scheduler': state_payload['scheduler']}
                    csv_row = services.lookup_csv_word(w.english)
                    if csv_row:
                        if (csv_row.get('chinese') or '').strip():
                            item['chinese'] = (csv_row.get('chinese') or '').strip()
                        services.apply_review_display_from_wordbank(item, csv_row, w.english)
                        csl = csv_row.get('chinese_sense_lines')
                        if isinstance(csl, list) and csl:
                            item['chinese_sense_lines'] = [str(x).strip() for x in csl if str(x).strip()]
                    words.append(item)
                reciter.save_learning_data(backup=False)
                return (services.jsonify({'words': words, 'count': len(words), 'bonus_session_id': bonus_session_id}), 200)
        except Exception as e:
            services.logger.error(f'获取加练列表失败: {e}')
            return (services.jsonify({'error': '服务器内部错误'}), 500)

    @blueprint.route('/api/words/question', methods=['POST'])
    @services.token_required
    @services.parent_forbidden
    def get_or_generate_word_question(username):
        """Load an approved private-bank question or fall back without blocking."""
        data = services.request.get_json(silent=True)
        if not isinstance(data, dict):
            return (services.jsonify({'error': '无效的JSON数据'}), 400)
        word_id = str(data.get('word_id') or '').strip()
        task_id = str(data.get('task_id') or '').strip()
        task_item_id = str(data.get('task_item_id') or '').strip()
        requested_type = str(data.get('exercise_type') or '').strip()
        if not word_id or not task_id or (not task_item_id):
            return (services.jsonify({'error': '今日任务参数不完整，请刷新后重试'}), 400)
        if requested_type not in services.gaokao_questions.QUESTION_TYPES:
            return (services.jsonify({'error': '当前题型不需要加载选择题'}), 400)
        try:
            with services.user_reciter_session(username) as reciter:
                task_item = reciter.resolve_daily_task_item(task_id, task_item_id, '')
                if not task_item:
                    return (services.jsonify({'error': '今日任务已更新，请刷新后继续'}), 409)
                exercise_type = str(task_item.get('exercise_type') or '')
                if exercise_type != requested_type:
                    return (services.jsonify({'error': '当前任务题型已更新，请刷新后继续'}), 409)
                word = reciter.find_word(task_item.get('word_key', ''), include_mastered=True)
                if not word:
                    return (services.jsonify({'error': '单词未找到'}), 404)
                question = services.gaokao_questions.get_question(word.english, exercise_type, source=services._current_gaokao_source(word.english))
                if not question:
                    reciter.mark_exercise_unavailable(word, exercise_type)
                    task_item['exercise_type'] = 'spelling'
                    task_item.pop('question_id', None)
                    task_item['question_fallback_reason'] = 'question_not_approved'
                    reciter.save_learning_data(backup=False)
                    fallback_word = services._spelling_fallback_word_payload(word)
                    state_payload = reciter.review_state_payload(word)
                    fallback_word['mastery'] = state_payload['mastery']
                    fallback_word['memory_status'] = state_payload['memory_status']
                    return (services.jsonify({'fallback': True, 'exercise_type': 'spelling', 'message': '选择题尚未生成或不可用，已自动切换为拼写练习', 'word': fallback_word}), 200)
                task_item['question_id'] = question['question_id']
                task_item.pop('question_fallback_reason', None)
                reciter.save_learning_data(backup=False)
                return (services.jsonify({'fallback': False, 'generated': False, 'exercise_type': exercise_type, 'question': services.gaokao_questions.public_question(question)}), 200)
        except Exception as exc:
            services._invalidate_user_reciter_cache(username)
            services.logger.error('加载高考选择题失败: user=%s word=%s error=%s', username, word_id, exc)
            return (services.jsonify({'error': '题目加载失败，请稍后重试'}), 500)

    @blueprint.route('/api/words/practice', methods=['POST'])
    @services.token_required
    @services.parent_forbidden
    def practice_word(username):
        """练习单词"""
        try:
            data = services.request.get_json()
            if not data:
                return (services.jsonify({'error': '无效的JSON数据'}), 400)
            word_id = str(data.get('word_id') or '').strip()
            answer = str(data.get('answer') or '').strip()
            selected_option_id = str(data.get('selected_option_id') or '').strip()
            question_id = str(data.get('question_id') or '').strip()
            remedial = data.get('remedial') is True
            bonus_practice = data.get('bonus_practice') is True
            bonus_session_id = str(data.get('bonus_session_id') or '').strip()[:96]
            test_inflection = data.get('test_inflection') is True
            task_id = str(data.get('task_id') or '').strip()
            task_item_id = str(data.get('task_item_id') or '').strip()
            requested_exercise_type = str(data.get('exercise_type') or 'spelling').strip()
            review_event_id = str(data.get('review_event_id') or '').strip()[:96]
            audio_available = data.get('audio_available') is True
            try:
                attempt_number = max(1, min(1000, int(data.get('attempt_number') or 1)))
            except (TypeError, ValueError, OverflowError):
                attempt_number = 1
            try:
                hint_count = max(0, min(100, int(data.get('hint_count') or 0)))
            except (TypeError, ValueError, OverflowError):
                hint_count = 0
            try:
                elapsed_ms = max(0, min(600000, int(data.get('elapsed_ms') or 0)))
            except (TypeError, ValueError, OverflowError):
                elapsed_ms = 0
            if not word_id:
                return (services.jsonify({'error': '单词ID不能为空'}), 400)
            if bool(task_id) != bool(task_item_id):
                return (services.jsonify({'error': '今日任务参数不完整，请刷新后重试'}), 400)
            if not review_event_id:
                return (services.jsonify({'error': '缺少作答事件标识，请刷新后重试'}), 400)
            if not task_id and (not bonus_practice):
                return (services.jsonify({'error': '请从今日任务进入练习'}), 409)
            if bonus_practice and (not bonus_session_id):
                return (services.jsonify({'error': '加练会话已失效，请重新获取随机加练'}), 409)
            with services.user_reciter_session(username) as reciter:
                task_item = None
                if task_id and (not bonus_practice):
                    task_item = reciter.resolve_daily_task_item(task_id, task_item_id, '' if requested_exercise_type in services.gaokao_questions.QUESTION_TYPES else word_id, review_event_id)
                    if not task_item:
                        return (services.jsonify({'error': '今日任务已更新，请刷新后继续'}), 409)
                if bonus_practice:
                    word = reciter.resolve_bonus_practice_word(bonus_session_id, word_id, review_event_id)
                    if not word:
                        return (services.jsonify({'error': '加练题目不属于当前会话，请重新获取随机加练'}), 409)
                else:
                    word = reciter.find_word(task_item.get('word_key', '') if task_item else word_id, include_mastered=True)
                if word in reciter.mastered_words and (not (bonus_practice or task_item or remedial)):
                    word = None
                if not word:
                    return (services.jsonify({'error': '单词未找到'}), 404)
                if remedial and requested_exercise_type == 'spelling':
                    exercise_type = 'spelling'
                else:
                    exercise_type = str(task_item.get('exercise_type') or 'spelling') if task_item else requested_exercise_type
                if exercise_type == 'listening' and (not audio_available):
                    exercise_type = 'spelling'
                if exercise_type not in services.EXERCISE_TYPES:
                    exercise_type = 'spelling'
                if task_item and exercise_type not in services.gaokao_questions.QUESTION_TYPES and (reciter.word_state_key(word_id) != reciter.word_state_key(word)):
                    return (services.jsonify({'error': '当前任务单词已更新，请刷新后继续'}), 409)
                if exercise_type == 'listening':
                    test_inflection = False
                answer_feedback = None
                if exercise_type in services.gaokao_questions.QUESTION_TYPES:
                    if not task_item or not question_id or (not selected_option_id):
                        return (services.jsonify({'error': '选择题作答参数不完整，请重新加载题目'}), 400)
                    question = services.gaokao_questions.get_question(word.english, exercise_type, source=services._current_gaokao_source(word.english))
                    bound_question_id = str(task_item.get('question_id') or '')
                    if not question or question.get('question_id') != question_id or bound_question_id != question_id:
                        return (services.jsonify({'error': '题目版本已更新，请重新加载题目'}), 409)
                    valid_option_ids = {str(option.get('id') or '') for option in question.get('options') or [] if isinstance(option, dict)}
                    if selected_option_id not in valid_option_ids:
                        return (services.jsonify({'error': '所选答案无效，请重新选择'}), 400)
                    is_correct = services.gaokao_questions.check_answer(question, selected_option_id)
                    submission_fingerprint = services.hashlib.sha256(f'{question_id}\x00{selected_option_id}'.encode('utf-8')).hexdigest()
                    test_inflection = False
                else:
                    if not answer:
                        return (services.jsonify({'error': '答案不能为空'}), 400)
                    submitted = answer.strip().lower()
                    lemma = word.english.strip().lower()
                    if test_inflection:
                        csv_row = services.lookup_csv_word(word.english)
                        if csv_row:
                            picked = services.pick_example_for_word(csv_row, word.english)
                            eff = (picked.get('example_form') or '').strip().lower()
                            expected = eff if eff else lemma
                        else:
                            expected = lemma
                        is_correct = submitted == expected
                    else:
                        is_correct = submitted == lemma
                    submission_fingerprint = services.hashlib.sha256(submitted.encode('utf-8')).hexdigest()
                applied = reciter.apply_scored_review_attempt(word, exercise_type=exercise_type, correct=is_correct, task_item=task_item, event_id=review_event_id, elapsed_ms=elapsed_ms, hint_count=hint_count, attempt_number=attempt_number, remedial=remedial, bonus_practice=bonus_practice, audio_available=audio_available, submission_fingerprint=submission_fingerprint)
                if task_item and applied['recorded'] and (not audio_available):
                    task_item['exercise_type'] = exercise_type
                if bonus_practice and is_correct:
                    if not reciter.complete_bonus_practice_word(bonus_session_id, word_id, review_event_id):
                        return (services.jsonify({'error': '加练题目已完成，请重新获取随机加练'}), 409)
                reciter.save_learning_data(backup=False)
                recorded = applied['recorded']
                message = applied['message']
                mastered_now = applied['mastered_now']
                if exercise_type in services.gaokao_questions.QUESTION_TYPES:
                    if not is_correct and (not applied['final_attempt']):
                        message = '答案不正确'
                    if is_correct or applied['final_attempt']:
                        answer_feedback = services.gaokao_questions.answer_explanation(question)
                gam_payload = None
                if is_correct and (recorded or review_event_id):
                    pkw, pkm = services._pk_stats_for_gamification(username)
                    bonus_session_completed = bool(bonus_practice and reciter.is_bonus_practice_completion_event(bonus_session_id, word_id, review_event_id))
                    gam_payload = services.gamification_mod.award_correct_answer(services.DATA_DIR, username, bonus_practice=bonus_practice, remedial=applied['remedial'], old_success_count=applied['old_success_count'], new_success_count=applied['new_success_count'], mastered_now=mastered_now, mastered_words=len(reciter.mastered_words), pk_wins=pkw, pk_matches=pkm, event_id=services.hashlib.sha256(f'{reciter.word_state_key(word)}\x00{review_event_id}'.encode('utf-8')).hexdigest(), event_scope=reciter.word_state_key(word), bonus_session_completed=bonus_session_completed)
                body = {'correct': is_correct, 'message': message, 'recorded': recorded, 'mastered_now': mastered_now, 'remedial': applied['remedial'], 'attempt_number': applied['attempt_number'], 'attempt_limit': applied['attempt_limit'], 'final_attempt': applied['final_attempt'], 'task_remedial': bool(task_item and (task_item.get('phase') == 'remedial' or reciter.task_attempt_count(task_item) >= applied['attempt_limit'])), 'exercise_type': exercise_type, 'task_progress': reciter.daily_task_progress(), 'word': {'english': word.english, 'chinese': word.chinese, 'success_count': word.success_count, 'next_review_date': word.next_review_date.isoformat(), **reciter.review_state_payload(word)}}
                if gam_payload is not None:
                    body['gamification'] = gam_payload
                if answer_feedback is not None:
                    body['answer_feedback'] = answer_feedback
                if is_correct:
                    extra_rows = services.other_v2_sense_extra_for_review_slot(word.english)
                    if extra_rows:
                        body['other_senses_extra'] = extra_rows
                return (services.jsonify(body), 200)
        except services.ReviewEventConflict:
            return (services.jsonify({'error': '作答事件与原请求不一致，请重新提交'}), 409)
        except services.PersistentStateLoadError:
            services._invalidate_user_reciter_cache(username)
            raise
        except Exception as e:
            services._invalidate_user_reciter_cache(username)
            services.logger.error(f'练习单词失败: {e}')
            return (services.jsonify({'error': '服务器内部错误'}), 500)

    @blueprint.route('/api/words/speak', methods=['POST'])
    @services.token_required
    @services.parent_forbidden
    def speak_text(username):
        """朗读文本（跨平台支持）
        
        - macOS: 使用系统 say 命令
        - Linux/Windows: 如果 say 命令不存在则静默跳过
        """
        try:
            data = services.request.get_json()
            if not data:
                return (services.jsonify({'error': '无效的JSON数据'}), 400)
            text = data.get('text', '').strip()
            if not text:
                return (services.jsonify({'error': '文本不能为空'}), 400)
            safe_text = services.sanitize_tts_text(text)
            if not safe_text:
                return (services.jsonify({'error': '文本无效或过长'}), 400)
            if services.shutil.which('say') is None:
                services.logger.debug(f'用户 {username} 尝试朗读但 say 命令不可用')
                return (services.jsonify({'message': '语音播放不可用，已跳过'}), 200)
            try:
                services.subprocess.run(['say', safe_text], capture_output=True, text=True, timeout=30)
            except services.subprocess.TimeoutExpired:
                services.logger.warning(f'用户 {username} 的朗读超时')
                return (services.jsonify({'message': '朗读超时，音频过长'}), 200)
            except Exception as e:
                services.logger.error(f'朗读执行失败: {e}')
                return (services.jsonify({'message': '朗读执行失败'}), 200)
            return (services.jsonify({'message': '朗读完成'}), 200)
        except Exception as e:
            services.logger.error(f'朗读失败: {e}')
            return (services.jsonify({'error': '服务器内部错误'}), 500)

    @blueprint.route('/api/words/speak-audio', methods=['POST'])
    @services.token_required
    @services.parent_forbidden
    def speak_text_audio(username):
        """使用 Piper 合成英文 WAV 并返回，供浏览器播放（远程可用）。"""
        if not services._rate_allow(f'tts_audio:{username}', services._RATE_MAX_TTS_AUDIO):
            return (services.jsonify({'error': '朗读请求过于频繁，请稍后再试'}), 429)
        try:
            data = services.request.get_json()
            if not data:
                return (services.jsonify({'error': '无效的JSON数据'}), 400)
            text = data.get('text', '').strip()
            if not text:
                return (services.jsonify({'error': '文本不能为空'}), 400)
            safe_text = services.sanitize_tts_text(text)
            if not safe_text:
                return (services.jsonify({'error': '文本无效或过长'}), 400)
            if services.piper_runtime_ready is None or not services.piper_runtime_ready():
                return (services.jsonify({'error': 'Piper 未配置'}), 503)
            if services.piper_synthesize_wav is None:
                return (services.jsonify({'error': 'Piper 不可用'}), 503)
            wav = services.piper_synthesize_wav(safe_text)
            if not wav:
                return (services.jsonify({'error': '语音合成失败'}), 503)
            response = services.Response(wav, mimetype='audio/wav')
            if services.piper_last_result_metadata is not None:
                metadata = services.piper_last_result_metadata()
                response.headers['X-TTS-Cache'] = 'HIT' if metadata.get('cache_hit') else 'MISS'
                response.headers['Server-Timing'] = f"piper;dur={float(metadata.get('duration_ms') or 0):.1f}"
            return response
        except Exception as e:
            services.logger.error(f'Piper 朗读失败: {e}')
            return (services.jsonify({'error': '服务器内部错误'}), 500)

    @blueprint.route('/api/words/mastered', methods=['GET'])
    @services.token_required
    def get_mastered_words(username):
        """获取已掌握单词"""
        try:
            with services.user_reciter_session(username) as reciter:
                words = []
                for w in reciter.mastered_words:
                    review_state = reciter.get_review_state(w)
                    state_payload = reciter.review_state_payload(w)
                    csv_row = services.lookup_csv_word(w.english)
                    ex_text = ''
                    if csv_row:
                        picked = services.pick_example_for_word(csv_row, w.english)
                        ex_text = (picked.get('example') or '').strip()
                    if not ex_text and getattr(w, 'example', None):
                        ex_text = (w.example or '').strip()
                    words.append({'english': w.english, 'chinese': w.chinese, 'phonetic': csv_row.get('phonetic', '') if csv_row else '', 'example': ex_text, 'review_count': w.review_count, 'mastered_date': review_state.get('mastered_date') or w.next_review_date.isoformat(), 'memory_status': str(review_state.get('memory_status') or 'stable'), **state_payload})
                return (services.jsonify({'words': words, 'count': len(words)}), 200)
        except Exception as e:
            services.logger.error(f'获取已掌握单词失败: {e}')
            return (services.jsonify({'error': '服务器内部错误'}), 500)

    app.register_blueprint(blueprint)
    return {
        'get_status': get_status,
        'get_words_summary': get_words_summary,
        'get_review_list': get_review_list,
        'get_extra_review_list': get_extra_review_list,
        'get_or_generate_word_question': get_or_generate_word_question,
        'practice_word': practice_word,
        'speak_text': speak_text,
        'speak_text_audio': speak_text_audio,
        'get_mastered_words': get_mastered_words,
    }
