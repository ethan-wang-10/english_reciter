"""Wordbank HTTP handlers with explicit application services."""
from __future__ import annotations

from flask import Blueprint


def register_routes(app, services):
    blueprint = Blueprint('wordbank', __name__)

    @blueprint.route('/api/words/import-json', methods=['POST'])
    @services.token_required
    def import_words_json(username):
        """家长粘贴学习数据格式的 JSON，合并到当前用户的待复习词库。"""
        if not services._rate_allow(f'import_json:{username}', services._RATE_MAX_IMPORT_JSON):
            return (services.jsonify({'error': '导入过于频繁，请稍后再试'}), 429)
        items, err = services._parse_import_json_body(services.request)
        if err:
            return (services.jsonify({'error': err}), 400)
        if not items:
            return (services.jsonify({'error': '单词列表为空'}), 400)
        if len(items) > 5000:
            return (services.jsonify({'error': '单次最多导入 5000 条'}), 400)
        norm_items: services.List[dict] = []
        for it in items:
            if not isinstance(it, dict):
                norm_items.append(it)
                continue
            row = dict(it)
            en = str(row.get('english', '')).strip()
            if en:
                row['english'] = services._normalize_import_english_surface(en)[:500]
            norm_items.append(row)
        try:
            with services.user_reciter_session(username) as reciter:
                result = reciter.add_words_from_dicts(norm_items)
            n = result['added']
            skipped = result['skipped_duplicate']
            invalid = result['skipped_invalid']
            msg = f'成功加入 {n} 个新单词'
            if skipped:
                msg += f'，已跳过 {skipped} 个重复'
            if invalid:
                msg += f'，{invalid} 条无效已忽略'
            services.logger.info('用户 %s JSON 导入: added=%s dup=%s invalid=%s', username, n, skipped, invalid)
            return (services.jsonify({'message': msg, **result}), 200)
        except Exception as e:
            services.logger.error(f'JSON 导入失败: {e}')
            return (services.jsonify({'error': '导入失败，请检查 JSON 格式'}), 500)

    @blueprint.route('/api/textbooks/catalog', methods=['GET'])
    @services.token_required
    def textbooks_catalog(username):
        """课文学习：返回教材索引及各套 manifest（如 nce/manifest.json）。"""
        idx = services._textbooks_load_index()
        corpora_out: services.List[dict] = []
        for c in idx.get('corpora') or []:
            cid = str(c.get('id', '')).strip()
            title = str(c.get('title', '')).strip()
            manifest_rel = str(c.get('manifestRelativePath', '')).strip()
            if not cid or not manifest_rel:
                continue
            mp = (services.STATIC_WB_DIR / manifest_rel).resolve()
            if not str(mp).startswith(str(services.STATIC_WB_DIR.resolve())) or not mp.is_file():
                continue
            try:
                with open(mp, 'r', encoding='utf-8') as f:
                    mdata = services.json.load(f)
            except Exception as e:
                services.logger.warning('读取教材 manifest %s 失败: %s', manifest_rel, e)
                mdata = {}
            corpora_out.append({'id': cid, 'title': title or cid, 'manifest': mdata})
        return (services.jsonify({'schema': idx.get('schema'), 'corpora': corpora_out}), 200)

    @blueprint.route('/api/textbooks/lesson', methods=['GET'])
    @services.token_required
    def textbooks_lesson(username):
        """课文学习：按 corpus id + 相对于该教材根目录的 json 路径返回课文。"""
        corpus_id = services.request.args.get('corpus', '').strip()
        rel_path = services.request.args.get('path', '').strip()
        if not corpus_id or not rel_path:
            return (services.jsonify({'error': '缺少 corpus 或 path 参数'}), 400)
        idx = services._textbooks_load_index()
        corpus_root: services.Optional[services.Path] = None
        manifest_rel: str = ''
        for c in idx.get('corpora') or []:
            if str(c.get('id', '')).strip() != corpus_id:
                continue
            manifest_rel = str(c.get('manifestRelativePath', '')).strip()
            if manifest_rel:
                corpus_root = services._corpus_root_from_manifest_relative(manifest_rel)
            break
        if corpus_root is None:
            return (services.jsonify({'error': '无效的教材'}), 400)
        lesson_path = services._textbooks_resolve_lesson_file(corpus_root, rel_path)
        if lesson_path is None:
            return (services.jsonify({'error': '无效的课文路径'}), 400)
        if not services.is_paid_user(username) and manifest_rel:
            mp = (services.STATIC_WB_DIR / manifest_rel).resolve()
            if str(mp).startswith(str(services.STATIC_WB_DIR.resolve())) and mp.is_file():
                try:
                    with open(mp, 'r', encoding='utf-8') as f:
                        manifest_data = services.json.load(f)
                    uidx = services._textbooks_unit_index_for_path(manifest_data, rel_path)
                    if uidx is not None and uidx >= services.TEXTBOOK_FREE_UNITS_PER_BOOK:
                        return (services.jsonify({'error': f'普通用户每册仅可学习前 {services.TEXTBOOK_FREE_UNITS_PER_BOOK} 篇课文，升级 VIP 后可查看全部'}), 403)
                except Exception as e:
                    services.logger.warning('课文权限校验读取 manifest 失败: %s', e)
        try:
            with open(lesson_path, 'r', encoding='utf-8') as f:
                data = services.json.load(f)
        except Exception as e:
            services.logger.warning('读取课文 JSON 失败: %s', e)
            return (services.jsonify({'error': '课文读取失败'}), 500)
        return (services.jsonify(data), 200)

    @blueprint.route('/api/wordbank/csv', methods=['GET'])
    @services.token_required
    def get_wordbank_csv(username):
        """返回系统词库扁平行（与 ``merge_wordbank_rows_for_search`` 一致：v2 优先，同键覆盖 CSV）。

        Query:
        - level: 可选，按难度过滤
        - fields: ``full``（默认）或 ``minimal``（省略 example*_form 等，供单词学习等场景）
        支持 If-None-Match / ETag，内容未变时返回 304（ETag 含 words.csv 与 words_v2.json 的 mtime）。
        """
        level = services.request.args.get('level', '').strip()
        fields_mode = services.request.args.get('fields', 'full').strip().lower()
        if fields_mode not in ('full', 'minimal'):
            fields_mode = 'full'
        rows, _ = services.merge_wordbank_rows_for_search(level)
        count = len(rows)
        try:
            csv_mtime = services.WORDS_CSV_FILE.stat().st_mtime if services.WORDS_CSV_FILE.exists() else 0.0
        except OSError:
            csv_mtime = 0.0
        try:
            v2_mtime = services.wordbank_v2.WORDS_V2_FILE.stat().st_mtime if services.wordbank_v2.WORDS_V2_FILE.exists() else 0.0
        except OSError:
            v2_mtime = 0.0
        _etag_seed = f'{csv_mtime:.9f}\x00{v2_mtime:.9f}\x00{level}\x00{fields_mode}\x00{count}'.encode('utf-8')
        etag_digest = services.hashlib.sha256(_etag_seed).hexdigest()[:32]
        etag = f'W/"wbcsv-{etag_digest}"'
        inm = (services.request.headers.get('If-None-Match') or '').strip()
        if inm == etag:
            resp = services.Response(status=304)
            resp.headers['ETag'] = etag
            return resp
        if fields_mode == 'minimal':
            out_rows = [services._wordbank_csv_row_minimal(r) for r in rows]
        else:
            out_rows = rows
        resp = services.jsonify({'words': out_rows, 'count': count})
        resp.headers['ETag'] = etag
        return (resp, 200)

    @blueprint.route('/api/wordbank/csv/search', methods=['GET'])
    @services.token_required
    def search_wordbank_csv(username):
        """在 CSV 词汇表中搜索（支持英文/中文，逗号分隔多词）。

        - ``heuristics=1``：启用缩写/复数/-ed/-ing/派生等快速规则。
        - ``heuristics=0``：关闭（仅表面形、管理员映射与 spaCy）。
        - 未指定 ``heuristics`` 时：``per_surface=1``（课文学习逐词）默认开启启发式；否则默认关闭（导入页词库搜索）。
        - ``surface_first=1``（导入页「从词库搜索」）：英文词先仅用表面形与管理员映射匹配，未命中再启用 spaCy 原型匹配。
        """
        if not services._rate_allow(f'wordbank_search:{username}', services._RATE_MAX_WORDBANK_SEARCH):
            return (services.jsonify({'error': '词库搜索过于频繁，请稍后再试'}), 429)
        q = services.request.args.get('q', '').strip()
        level = services.request.args.get('level', '').strip()
        per_surface = services.request.args.get('per_surface', '').strip().lower() in ('1', 'true', 'yes')
        surface_first = services.request.args.get('surface_first', '').strip().lower() in ('1', 'true', 'yes', 'on')
        use_spacy = services.request.args.get('nlp', '1').strip().lower() not in ('0', 'false', 'no', 'off')
        raw_heur = (services.request.args.get('heuristics') or '').strip().lower()
        if raw_heur in ('1', 'true', 'yes', 'on'):
            use_heuristics = True
        elif raw_heur in ('0', 'false', 'no', 'off'):
            use_heuristics = False
        else:
            use_heuristics = per_surface
        if not q:
            return (services.jsonify({'words': [], 'count': 0, 'lemma_resolution': {}, 'implicit_lemma_nlp_resolution': {}, 'implicit_plural_resolution': {}, 'implicit_past_resolution': {}, 'implicit_ing_resolution': {}, 'implicit_contraction_resolution': {}, 'implicit_suffix_resolution': {}, 'surface_hits': {}, 'surface_blocked': {}, 'nlp_enabled': use_spacy, 'heuristics_enabled': use_heuristics, 'surface_first': surface_first}), 200)
        if len(q) > 2000:
            return (services.jsonify({'error': '搜索内容过长，请减少后重试'}), 400)
        terms = services._dedupe_preserve_order([services._normalize_apostrophe_token(t.strip()) for t in services.re.split('[,，]', q) if t.strip()])
        if len(terms) > 64:
            return (services.jsonify({'error': '单次最多搜索 64 个词'}), 400)
        mappings = services.get_wordbank_lemma_mappings()
        rows, csv_row_keys = services.merge_wordbank_rows_for_search(level)
        lemma_resolution: services.Dict[str, str] = {}
        implicit_lemma_nlp_resolution: services.Dict[str, str] = {}
        implicit_plural_resolution: services.Dict[str, str] = {}
        implicit_past_resolution: services.Dict[str, str] = {}
        implicit_ing_resolution: services.Dict[str, str] = {}
        implicit_contraction_resolution: services.Dict[str, str] = {}
        implicit_suffix_resolution: services.Dict[str, str] = {}
        surface_hits: services.Dict[str, services.Optional[dict]] = {}
        surface_blocked: services.Dict[str, bool] = {}
        difficult: services.Dict[str, object] = {}
        if per_surface:
            with services._TROUBLES_LOCK:
                difficult = dict(services._read_troubles_unlocked().get('difficult') or {})
        spacy_lemma_map: services.Optional[services.Dict[str, str]] = None
        if use_spacy:
            batch_terms = [t for t in terms if services.re.match('[a-z]', t)]
            if batch_terms:
                spacy_lemma_map = services._spacy_lemma_map_for_surfaces(batch_terms)
                if not spacy_lemma_map:
                    spacy_lemma_map = None

        def _resolve_term_hit(t: str) -> Tuple[Optional[str], Optional[str]]:
            """解析英文检索词在词库中的命中原形；surface_first 时先表面形与映射，未命中再 spaCy。"""
            if surface_first:
                h1, k1 = services._first_lemma_in_csv_with_kind(t, mappings, csv_row_keys, False, None, use_heuristics)
                if h1 is not None or not use_spacy:
                    return (h1, k1)
                return services._first_lemma_in_csv_with_kind(t, mappings, csv_row_keys, True, spacy_lemma_map, use_heuristics)
            return services._first_lemma_in_csv_with_kind(t, mappings, csv_row_keys, use_spacy, spacy_lemma_map, use_heuristics)
        term_hits: services.Dict[str, services.Optional[str]] = {}
        for term in terms:
            if services.re.match('[a-z]', term):
                hit, kind = _resolve_term_hit(term)
                term_hits[term] = hit
                if per_surface and term not in surface_hits:
                    if hit is None:
                        surface_hits[term] = None
                        surface_blocked[term] = term in difficult
                    else:
                        surface_hits[term] = services.lookup_csv_word(hit)
                        surface_blocked[term] = False
                if hit is not None and hit != term:
                    lemma_resolution[term] = hit
                    if term not in mappings:
                        if kind == 'lemma_nlp':
                            implicit_lemma_nlp_resolution[term] = hit
                        elif kind == 'plural':
                            implicit_plural_resolution[term] = hit
                        elif kind == 'past':
                            implicit_past_resolution[term] = hit
                        elif kind == 'ing':
                            implicit_ing_resolution[term] = hit
                        elif kind == 'contraction':
                            implicit_contraction_resolution[term] = hit
                        elif kind == 'suffix':
                            implicit_suffix_resolution[term] = hit
        _nk = services.wordbank_v2.normalize_english_key
        english_target_keys: services.Set[str] = set()
        chinese_terms: services.List[str] = []
        for term in terms:
            if services.re.match('[a-z]', term):
                if surface_first:
                    hit = term_hits.get(term)
                    if hit is not None:
                        english_target_keys.add(_nk(hit))
                    continue
                for candidate, _kind in services._iter_csv_lemma_candidates(term, mappings, use_spacy, spacy_lemma_map, use_heuristics):
                    english_target_keys.add(_nk(candidate))
            else:
                chinese_terms.append(term)
        result = []
        seen = set()
        for row in rows:
            en = _nk(row.get('english', ''))
            zh = row.get('chinese', '')
            matched = en in english_target_keys or any((term in zh for term in chinese_terms))
            if matched and en not in seen:
                seen.add(en)
                result.append(row)
        out: services.Dict[str, object] = {'words': result, 'count': len(result), 'lemma_resolution': lemma_resolution, 'implicit_lemma_nlp_resolution': implicit_lemma_nlp_resolution, 'implicit_plural_resolution': implicit_plural_resolution, 'implicit_past_resolution': implicit_past_resolution, 'implicit_ing_resolution': implicit_ing_resolution, 'implicit_contraction_resolution': implicit_contraction_resolution, 'implicit_suffix_resolution': implicit_suffix_resolution}
        if per_surface:
            out['surface_hits'] = surface_hits
            out['surface_blocked'] = surface_blocked
        out['nlp_enabled'] = use_spacy
        out['heuristics_enabled'] = use_heuristics
        out['surface_first'] = surface_first
        return (services.jsonify(out), 200)

    @blueprint.route('/api/words/import-from-article', methods=['POST'])
    @services.token_required
    def import_from_article(username):
        """
        从文章文本提取单词，返回匹配词条列表（不直接加入待复习）：
        - 免费版：按空格分词后去查 CSV；可勾选 use_spacy 在匹配步用 spaCy
        - VIP：默认 extract_mode=spacy；extract_mode=ai 仅当管理后台开启且请求携带有效管理员 token
        前端拿到词条列表后注入选框，让用户确认后再加入待复习。
        """
        internal_job = bool(getattr(services.g, 'import_job_execution', False))
        if not internal_job and (not services._rate_allow(f'article_import:{username}', services._RATE_MAX_ARTICLE_IMPORT)):
            return (services.jsonify({'error': '文章提取过于频繁，请稍后再试'}), 429)
        data = services.request.get_json(silent=True) or {}
        text = str(data.get('text', '')).strip()
        if not text:
            return (services.jsonify({'error': '文章内容不能为空'}), 400)
        if len(text) > 20000:
            return (services.jsonify({'error': '文章内容过长（最多20000字符）'}), 400)
        plan = services.get_user_plan(username)
        extract_mode = ''
        if plan == 'paid':
            has_ds = bool(services.get_deepseek_api_key())
            raw_mode = str(data.get('extract_mode', '') or '').strip().lower()
            if raw_mode in ('ai', 'deepseek'):
                admin_tok = services._admin_token_from_request()
                if not services._article_ai_extract_enabled():
                    return (services.jsonify({'error': '未在管理后台开启「AI 文章分词」，VIP 默认使用 spaCy 分词'}), 403)
                if not has_ds:
                    return (services.jsonify({'error': '未配置 DeepSeek API，无法使用 AI 分词'}), 400)
                if not internal_job and (not services.verify_admin_token(admin_tok)):
                    return (services.jsonify({'error': 'AI 分词仅限管理员：请先登录管理后台并保持会话有效'}), 403)
                extract_mode = 'ai'
            else:
                extract_mode = 'spacy'
            if services._import_jobs_async_enabled() and (not internal_job):
                job = services._import_job_store().enqueue('article_extract', username, {'text': text, 'extract_mode': extract_mode})
                services._IMPORT_JOB_WAKE_EVENT.set()
                return (services.jsonify({'job_id': job['job_id'], 'status': job['status'], 'message': '文章已提交后台提取'}), 202)
            if extract_mode == 'spacy':
                lemmas = services.spacy_extract_lemmas_from_article(text)
                if lemmas is None:
                    return (services.jsonify({'error': 'spaCy 模型未就绪，请安装 en_core_web_sm 或稍后重试'}), 500)
                method = 'spacy'
            else:
                lemmas = services.deepseek_extract_lemmas(text)
                if lemmas is None:
                    return (services.jsonify({'error': 'DeepSeek API 调用失败，请稍后重试'}), 500)
                method = 'deepseek'
            services._require_import_ownership()
            use_spacy = True
        else:
            raw_us = data.get('use_spacy', False)
            if isinstance(raw_us, str):
                use_spacy = raw_us.strip().lower() in ('1', 'true', 'yes', 'on')
            else:
                use_spacy = bool(raw_us)
            raw_words = services.re.findall("[a-zA-Z']+", text)
            lemmas = list({w.lower().strip("'") for w in raw_words if len(w) >= 2})
            method = 'simple'
        csv_set = services.get_wordbank_english_set()
        mappings = services.get_wordbank_lemma_mappings()
        unique_lemmas = list(dict.fromkeys(lemmas))
        use_spacy_match = use_spacy
        if plan == 'paid' and extract_mode == 'spacy':
            use_spacy_match = False
        spacy_lemma_map: services.Optional[services.Dict[str, str]] = None
        if use_spacy_match:
            spacy_lemma_map = services._spacy_lemma_map_for_surfaces(unique_lemmas)
        unmatched_lemmas: services.List[str] = []
        matched_effective: services.List[str] = []
        seen_eff = set()
        matched_surface_count = 0
        for w in unique_lemmas:
            eff = services._first_lemma_in_csv(w, mappings, csv_set, use_spacy_match, spacy_lemma_map, use_heuristics=False)
            if eff is None:
                unmatched_lemmas.append(w)
            else:
                matched_surface_count += 1
                if eff not in seen_eff:
                    seen_eff.add(eff)
                    matched_effective.append(eff)
        stats = {'lemmas_total': len(unique_lemmas), 'matched_in_csv': matched_surface_count, 'not_in_csv': len(unmatched_lemmas)}
        if not matched_effective:
            empty_out: services.Dict[str, object] = {'message': '未在词库中找到匹配词汇', 'method': method, 'words': [], 'stats': stats, 'unmatched_lemmas': unmatched_lemmas, 'use_spacy': use_spacy_match}
            if plan == 'paid':
                empty_out['extract_mode'] = extract_mode
            return (services.jsonify(empty_out), 200)
        words = []
        for en in matched_effective:
            row = services.lookup_csv_word(en)
            if row:
                words.append(row)
        stats['matched_in_csv'] = len(words)
        ok_out: services.Dict[str, object] = {'message': f'从文章提取到 {len(words)} 个匹配词汇，请勾选后加入待复习', 'method': method, 'words': words, 'stats': stats, 'unmatched_lemmas': unmatched_lemmas, 'use_spacy': use_spacy_match}
        if plan == 'paid':
            ok_out['extract_mode'] = extract_mode
        return (services.jsonify(ok_out), 200)

    @blueprint.route('/api/wordbank/ocr-extract', methods=['POST'])
    @services.token_required
    def wordbank_ocr_extract(username):
        """默认离线英文手写识别，返回可编辑的原图读法与词库候选。"""
        if not services._rate_allow(f'ocr:{username}', services._RATE_MAX_OCR):
            return (services.jsonify({'error': '图片识别过于频繁，请稍后再试'}), 429)
        if not services._ocr_stack_ready():
            return (services.jsonify({'error': '服务器未启用图片识别：请安装 Pillow 与 RapidOCR/ONNX Runtime，或安装 Tesseract 降级引擎'}), 503)
        if 'file' not in services.request.files:
            return (services.jsonify({'error': '缺少 file 字段'}), 400)
        f = services.request.files['file']
        if not f or not f.filename:
            return (services.jsonify({'error': '未选择文件'}), 400)
        ct = (f.mimetype or '').lower()
        if ct not in ('image/jpeg', 'image/png', 'image/webp', 'image/gif'):
            return (services.jsonify({'error': '仅支持 JPEG、PNG、WebP、GIF'}), 400)
        try:
            f.stream.seek(0)
        except (OSError, AttributeError, TypeError):
            pass
        try:
            im = services._prepare_image_for_ocr(f.stream)
        except ValueError as e:
            return (services.jsonify({'error': str(e)}), 400)
        except Exception as e:
            services.logger.exception('OCR 图片解析失败: %s', e)
            return (services.jsonify({'error': '无法解析图片'}), 400)
        english_only = (services.request.form.get('english_only', '1') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
        handwriting = english_only and (services.request.form.get('handwriting', '1') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
        review_lines = []
        enhancement_applied = False
        try:
            if handwriting:
                raw_text, ocr_engine, review_lines, enhancement_applied = services._recognize_handwriting(im)
            else:
                raw_text, ocr_engine = services._recognize_ocr_image(im)
        except services.OCRUnavailableError as e:
            return (services.jsonify({'error': f'服务器未启用可用的图片识别引擎：{e}'}), 503)
        except services.TesseractNotFoundError:
            return (services.jsonify({'error': 'RapidOCR 不可用，且未检测到降级引擎 Tesseract。请检查 OCR 依赖安装。'}), 503)
        except Exception as e:
            services.logger.exception('OCR 识别失败: %s', e)
            return (services.jsonify({'error': '文字识别失败'}), 500)
        if english_only:
            raw_text = services._english_only_ocr_text(raw_text)
        tokens = services._english_tokens_from_ocr_text(raw_text)
        return (services.jsonify({'raw_text': (raw_text or '').strip(), 'tokens': tokens, 'ocr_engine': ocr_engine, 'english_only': english_only, 'handwriting': handwriting, 'enhancement_applied': enhancement_applied, 'review_lines': review_lines}), 200)

    @blueprint.route('/api/wordbank/csv/import-words', methods=['POST'])
    @services.token_required
    def import_vocab_to_csv(username):
        """
        词汇导入功能（仅 VIP）：
        - 接收逗号分隔的单词列表
        - 管理员映射（表面形 -> 原形）优先；未映射时先用 spaCy 校验词形是否可识别，不通过则跳过
        - 未映射时：名词「简单复数」表面形（如 apples）规范为原形（apple）再写入词库与待复习；不规则复数（feet 等）保持表面形；动词/形容词不因 lemma 误收成原形（避免 are→be）
        - 疑难词（AI 曾失败）不再重复调用 DeepSeek，直至管理员配置映射或删除记录
        - 仅当 **words_v2.json** 中尚无该英文键时调用 DeepSeek 生成并写入 v2；仅在旧 ``words.csv`` 中有仍会生成并写入 v2。已存在于 v2 则跳过生成。
        - 新写入 v2 的词会继续生成高考题候选，并自动审查；通过后写入私有题库正式区。
        - 可选 also_add_to_queue（默认 True）：是否将词加入当前用户待复习；为 False 时仅写词库
        """
        internal_job = bool(getattr(services.g, 'import_job_execution', False))
        if not internal_job:
            if not services._rate_allow(f'vocab_import:{username}', services._RATE_MAX_VOCAB_IMPORT):
                return (services.jsonify({'error': '词汇导入过于频繁，请稍后再试'}), 429)
            if not services.is_paid_user(username):
                return (services.jsonify({'error': '词汇导入功能仅限 VIP 用户使用'}), 403)
        body = services.request.get_json(silent=True) or {}
        raw = str(body.get('words', '')).strip()
        level_hint = str(body.get('level', '')).strip()
        also_queue = bool(body.get('also_add_to_queue', True))
        if not raw:
            return (services.jsonify({'error': '单词列表不能为空'}), 400)
        input_surfaces = [w.strip().lower() for w in services.re.split('[,，]', raw) if w.strip()]
        if not input_surfaces:
            return (services.jsonify({'error': '未解析到有效单词'}), 400)
        if len(input_surfaces) > 500:
            return (services.jsonify({'error': '单次最多处理 500 个单词'}), 400)
        if services._import_jobs_async_enabled() and (not internal_job):
            job = services._import_job_store().enqueue('vocab_import', username, {'words': raw, 'level': level_hint, 'also_add_to_queue': also_queue})
            services._IMPORT_JOB_WAKE_EVENT.set()
            return (services.jsonify({'job_id': job['job_id'], 'status': job['status'], 'message': f'已提交 {len(input_surfaces)} 个单词，正在后台处理'}), 202)
        restored_rows, restored_records, restored_errors = services._restore_import_batches()
        restored_keys = {services.wordbank_v2.normalize_english_key(row['english']) for row in restored_rows}
        v2_keys = services.wordbank_v2.get_v2_english_key_set()
        with services._TROUBLES_LOCK:
            tdoc = services._read_troubles_unlocked()
            mappings = dict(tdoc.get('mappings') or {})
            difficult = dict(tdoc.get('difficult') or {})
        uniq_for_spacy = list(dict.fromkeys((s for s in input_surfaces if s not in mappings)))
        lemma_map: services.Optional[services.Dict[str, str]] = None
        if uniq_for_spacy and services._wordbank_lemma_spacy_enabled() and (services._get_spacy_nlp() is not None):
            lemma_map = services._spacy_lemma_map_for_surfaces(uniq_for_spacy)
            if not lemma_map:
                lemma_map = None
        invalid_surfaces: services.List[str] = []
        surface_to_target: services.Dict[str, str] = {}
        for s in input_surfaces:
            if not services._vocab_import_spacy_accepts_surface(s, mappings, lemma_map):
                invalid_surfaces.append(s)
                continue
            surface_to_target[s] = mappings[s] if s in mappings else services._normalize_import_english_surface(s)
        already_in_v2 = [s for s in input_surfaces if s in surface_to_target
                         and surface_to_target[s] in v2_keys and surface_to_target[s] not in restored_keys]
        to_generate_unique: services.List[str] = []
        seen_tg = set()
        for s in input_surfaces:
            if s not in surface_to_target:
                continue
            t = surface_to_target[s]
            if t in v2_keys:
                continue
            if s in difficult:
                continue
            if s in seen_tg:
                continue
            seen_tg.add(s)
            to_generate_unique.append(s)
        blocked_surfaces: services.List[str] = []
        for s in input_surfaces:
            if s not in surface_to_target:
                continue
            t = surface_to_target[s]
            if t in v2_keys:
                continue
            if s in difficult:
                blocked_surfaces.append(s)
        blocked_surfaces = services._dedupe_preserve_order(blocked_surfaces)
        to_generate = to_generate_unique
        if to_generate and (not services.get_deepseek_api_key()):
            return (services.jsonify({'error': '服务端未配置 DEEPSEEK_API_KEY，无法使用此功能'}), 503)
        generated_entries: services.List[dict] = list(restored_rows)
        failed_surfaces: services.List[str] = []
        gaokao_generated_records: services.Dict[str, dict] = dict(restored_records)
        gaokao_generation_errors: services.Dict[str, str] = dict(restored_errors)
        if to_generate:
            wordbank_so_far = set(v2_keys)
            gen_key_to_surface: services.Dict[str, str] = {}
            for s in to_generate:
                gen_key_to_surface[surface_to_target[s]] = s
            for i in range(0, len(to_generate), services._GAOKAO_IMPORT_BATCH_WORDS):
                services._require_import_ownership()
                batch_surfaces = to_generate[i:i + services._GAOKAO_IMPORT_BATCH_WORDS]
                batch_words = [surface_to_target[s] for s in batch_surfaces]
                with services.gaokao_backfill.generation_job_lock(blocking=False) as acquired:
                    if not acquired:
                        return (services.jsonify({'error': '题目任务正在处理，请稍后重试；此前已导入的批次已保存'}), 409)
                    protected = services.gaokao_questions.externally_authored_words(batch_words)
                    for include_gaokao in (True, False):
                        batch = [word for word in batch_words if (services.gaokao_questions.normalize_word(word) not in protected) == include_gaokao]
                        if not batch:
                            continue
                        entries = services.deepseek_generate_word_entries_v2(batch, level=level_hint, include_gaokao_candidate=include_gaokao)
                        services._require_import_ownership()
                        batch_lower = {b.lower() for b in batch}
                        if entries is not None:
                            rows, success = services.accumulate_valid_deepseek_v2_entries(entries, level_hint=level_hint, v2_so_far=wordbank_so_far, batch_lower=batch_lower)
                            if rows:
                                generated_records, generation_errors = services.finalize_combined_gaokao_candidates(entries, rows)
                                services._save_import_batch(rows, generated_records, generation_errors)
                                try:
                                    _, skipped_dup = services.wordbank_v2.append_words_v2_entries(rows)
                                    services.wordbank_v2.invalidate_words_v2_cache()
                                    services.invalidate_merge_wordbank_rows_cache()
                                    if skipped_dup:
                                        services.logger.info('words_v2 批次落盘，跳过已存在键: %s', skipped_dup[:20])
                                except Exception as e:
                                    services.logger.error('写入 words_v2.json 失败（本批落盘）: %s', e)
                                    return (services.jsonify({'error': f'写入新词库失败（此前批次若已成功则已保存）: {e}'}), 500)
                                try:
                                    if generated_records or generation_errors:
                                        services.gaokao_questions.persist_candidate_pool_result(generated_records, generation_errors)
                                    gaokao_generated_records.update(generated_records)
                                    gaokao_generation_errors.update(generation_errors)
                                except Exception as exc:
                                    services.logger.exception('新词已写入 words_v2，但高考题发布失败: %s', exc)
                                    for row in rows:
                                        key = services.gaokao_questions.normalize_word(row.get('english'))
                                        if key and key not in protected:
                                            gaokao_generation_errors[key] = f'combined question persistence failed: {exc}'
                                generated_entries.extend(rows)
                                wordbank_so_far = services.wordbank_v2.get_v2_english_key_set()
                            miss_lemmas = [b for b in batch if b.lower() not in success]
                        else:
                            miss_lemmas = list(batch)
                        for key in miss_lemmas:
                            surf = gen_key_to_surface.get(key, key)
                            failed_surfaces.append(surf)
            failed_surfaces = services._dedupe_preserve_order(failed_surfaces)
            if failed_surfaces:
                services._require_import_ownership()
                services.record_surfaces_to_difficult(failed_surfaces)
        queue_result = None
        if also_queue:
            services._require_import_ownership()
            items_to_queue = []
            for s in already_in_v2:
                t = surface_to_target[s]
                row = services.lookup_csv_word(t)
                if row:
                    picked = services.pick_example_for_word(row, row.get('english') or '')
                    items_to_queue.append({'english': picked['english'], 'chinese': picked['chinese'], 'example': picked['example']})
            for entry in generated_entries:
                flat = services.wordbank_v2.v2_entry_to_flat_csv_row(entry)
                picked = services.pick_example_for_word(flat, flat.get('english') or '')
                items_to_queue.append({'english': picked['english'], 'chinese': picked['chinese'], 'example': picked['example']})
            if items_to_queue:
                try:
                    with services.user_reciter_session(username) as reciter:
                        queue_result = reciter.add_words_from_dicts(items_to_queue)
                except Exception as e:
                    services.logger.error('加入待复习失败: %s', e)
        services._require_import_ownership()
        try:
            gaokao_result = services._publish_combined_gaokao_questions_for_new_entries(generated_entries, gaokao_generated_records, gaokao_generation_errors)
        except Exception as exc:
            gaokao_words = sorted({services.gaokao_questions.normalize_word(entry.get('english')) for entry in generated_entries if services.gaokao_questions.normalize_word(entry.get('english'))})
            services.logger.exception('新词已入库，但自动生成高考题流程失败: %s', exc)
            gaokao_result = {'requested': len(generated_entries), 'eligible': len(gaokao_words), 'candidates_generated': 0, 'approved': 0, 'already_published': 0, 'rejected': 0, 'generation_failed': len(gaokao_words), 'audit_retry': 0, 'skipped_no_source': 0, 'generated_words': [], 'approved_words': [], 'rejected_words': [], 'generation_failed_words': gaokao_words, 'audit_retry_words': [], 'skipped_words': []}
        msg = f'处理 {len(input_surfaces)} 个单词：{len(generated_entries)} 个新词已写入新词库（words_v2.json）'
        if already_in_v2:
            msg += f'，{len(already_in_v2)} 个已在新词库（words_v2.json）中，未再生成'
        if invalid_surfaces:
            inv = services._dedupe_preserve_order(invalid_surfaces)
            msg += f'，{len(inv)} 个未通过词形校验（已跳过）'
        if blocked_surfaces:
            msg += f'，{len(blocked_surfaces)} 个疑难词（已跳过 AI 生成）'
        if failed_surfaces:
            msg += f'，{len(failed_surfaces)} 个生成失败已记入疑难词'
        if gaokao_result['approved']:
            msg += f"；已按题库规则生成并发布高考题 {gaokao_result['approved']} 个"
        if gaokao_result['already_published']:
            msg += f"；{gaokao_result['already_published']} 个已有可用高考题"
        if gaokao_result['rejected']:
            msg += f"；{gaokao_result['rejected']} 个高考题候选未通过语义审查"
        gaokao_deferred = len({*gaokao_result['generation_failed_words'], *gaokao_result['audit_retry_words'], *gaokao_result['skipped_words']})
        if gaokao_deferred:
            msg += f'；{gaokao_deferred} 个高考题待后台自动补全'
        if queue_result:
            msg += f"；已加入待复习 {queue_result.get('added', 0)} 个"
        elif not also_queue:
            msg += '；未加入待复习（仅写入词库）'
        return (services.jsonify({'message': msg, 'new_in_csv': len(generated_entries), 'already_in_v2': len(already_in_v2), 'already_in_v2_words': already_in_v2, 'already_in_csv': len(already_in_v2), 'already_in_csv_words': already_in_v2, 'failed': failed_surfaces, 'blocked_surfaces': blocked_surfaces, 'invalid_surfaces': services._dedupe_preserve_order(invalid_surfaces), 'queue_result': queue_result, 'also_add_to_queue': also_queue, 'gaokao_questions': gaokao_result}), 200)

    @blueprint.route('/api/wordbank/import-jobs/<job_id>', methods=['GET'])
    @services.token_required
    def get_vocab_import_job(username, job_id: str):
        if not services.re.fullmatch('[0-9a-f]{32}', str(job_id or '')):
            return (services.jsonify({'error': '导入任务不存在'}), 404)
        job = services._import_job_store().get(job_id, username)
        if not job:
            return (services.jsonify({'error': '导入任务不存在'}), 404)
        return (services.jsonify(job), 200)

    @blueprint.route('/api/wordbank/csv/trouble-status', methods=['GET'])
    @services.token_required
    def wordbank_trouble_status(username):
        """
        课文/导入前查询：某表面形是否被疑难词拦截、是否已有管理员映射。
        """
        q = services.request.args.get('q', '').strip().lower()
        if not q:
            return (services.jsonify({'blocked': False, 'mapped_to': None, 'in_difficult': False, 'in_csv': False, 'resolved_lemma': None}), 200)
        with services._TROUBLES_LOCK:
            tdoc = services._read_troubles_unlocked()
            mappings = dict(tdoc.get('mappings') or {})
            difficult = dict(tdoc.get('difficult') or {})
        csv_set = services.get_wordbank_english_set()
        hit = services._first_lemma_in_csv(q, mappings, csv_set, use_spacy=True, use_heuristics=False)
        if hit is not None:
            in_csv = True
            resolved_lemma = hit
            mapped_to = hit if hit != q else None
        else:
            in_csv = False
            resolved_lemma = services._lemma_for_vocab_not_in_csv(q, mappings, use_heuristics=False)
            mapped_to = resolved_lemma if resolved_lemma != q else None
        blocked = q in difficult and (not in_csv)
        return (services.jsonify({'blocked': blocked, 'mapped_to': mapped_to, 'in_difficult': q in difficult, 'in_csv': in_csv, 'resolved_lemma': resolved_lemma}), 200)

    @blueprint.route('/api/wordbank/community', methods=['GET'])
    @services.token_required
    def get_community_wordbank(username):
        """返回社区待审区（家长贡献）；正式词条仍以 words_v2.json 为准。"""
        with services._community_wb_guard():
            data = services._read_community_file_unlocked()
        return (services.jsonify({'schema': data.get('schema'), 'phase': 'community', 'label': data.get('label', '社区词库（待审核）'), 'count': len(data.get('words') or []), 'words': data.get('words') or []}), 200)

    @blueprint.route('/api/words/pending', methods=['GET'])
    @services.token_required
    def list_pending_words_for_settings(username):
        """待复习单词列表（配置页；家长登录时可删；学生仅在未开通家长账户时可删）。"""
        is_parent = getattr(services.g, 'is_parent', False)
        has_parent = services.student_has_enabled_parent_account(username)
        can_remove = bool(is_parent or not has_parent)
        try:
            with services.user_reciter_session(username) as reciter:
                today_d = services.china_today()
                out: services.List[dict] = []
                for w in reciter.all_words:
                    csv_row = services.lookup_csv_word(w.english)
                    nd = w.next_review_date
                    out.append({'english': w.english, 'chinese': w.chinese, 'phonetic': csv_row.get('phonetic', '') if csv_row else '', 'next_review_date': nd.isoformat(), 'remaining_days': (nd - today_d).days, 'review_count': w.review_count})
                return (services.jsonify({'words': out, 'count': len(out), 'can_remove_pending': can_remove}), 200)
        except Exception as e:
            services.logger.error('列出待复习失败: %s', e)
            return (services.jsonify({'error': '服务器内部错误'}), 500)

    @blueprint.route('/api/words/pending/remove', methods=['POST'])
    @services.token_required
    def remove_pending_words_api(username):
        """从待复习中移除单词（不删除已掌握词）。家长登录可删；学生仅在未开通家长账户时可删。"""
        if not services._rate_allow(f'pending_rm:{services._client_ip()}', 120):
            return (services.jsonify({'error': '请求过于频繁，请稍后再试'}), 429)
        is_parent = getattr(services.g, 'is_parent', False)
        if not is_parent and services.student_has_enabled_parent_account(username):
            return (services.jsonify({'error': '已开通家长账户，待复习词汇请由家长登录后在配置中管理'}), 403)
        body = services.request.get_json(silent=True) or {}
        raw = body.get('english')
        if isinstance(raw, str):
            english_list = [raw]
        elif isinstance(raw, list):
            english_list = raw
        else:
            return (services.jsonify({'error': '请提供 english 字段（字符串或字符串数组）'}), 400)
        seen = set()
        keys_ordered: services.List[str] = []
        for e in english_list:
            s = str(e or '').strip()
            if not s:
                continue
            k = s.lower()
            if k not in seen:
                seen.add(k)
                keys_ordered.append(s)
        if not keys_ordered:
            return (services.jsonify({'error': '未提供有效单词'}), 400)
        if len(keys_ordered) > 200:
            return (services.jsonify({'error': '单次最多移除 200 条'}), 400)
        try:
            with services.user_reciter_session(username) as reciter:
                result = reciter.remove_pending_words_by_english(keys_ordered)
                task_bundle = reciter.get_today_learning_plan(listening_available=services._reliable_listening_available())
                result['plan'] = task_bundle['plan']
                reciter.save_learning_data(backup=False)
            services._invalidate_user_reciter_cache(username)
            services.logger.info('用户 %s 待复习移除: removed=%s not_found=%s', username, result.get('removed'), len(result.get('not_found') or []))
            return (services.jsonify(result), 200)
        except Exception as e:
            services.logger.error('待复习移除失败: %s', e)
            return (services.jsonify({'error': '服务器内部错误'}), 500)

    @blueprint.route('/api/wordbank/community/import-simple', methods=['POST'])
    @services.token_required
    def community_import_simple(username):
        """
        家长简易导入：单词 + 例句 + 译文 → 写入社区待审区。
        若英文已出现在正式/旧版系统词库，或已在社区待审区中，则拒绝/跳过并返回明细。
        可选 also_add_to_queue：同时将新词加入当前用户待复习。
        """
        if not services._rate_allow(f'comm_import:{services._client_ip()}', 40):
            return (services.jsonify({'error': '导入请求过于频繁，请稍后再试'}), 429)
        body = services.request.get_json(silent=True) or {}
        text = str(body.get('text', '')).strip()
        also_queue = bool(body.get('also_add_to_queue'))
        rows, parse_err = services.parse_simple_parent_import_text(text)
        if parse_err:
            return (services.jsonify({'error': parse_err}), 400)
        if len(rows) > 500:
            return (services.jsonify({'error': '单次最多导入 500 条'}), 400)
        system_keys = services.load_system_wordbank_english_lower()
        added_entries: services.List[dict] = []
        rejected_in_system: services.List[str] = []
        skipped_duplicate_community: services.List[str] = []
        skipped_invalid = 0
        with services._community_wb_guard():
            data = services._read_community_file_unlocked()
            words: services.List[dict] = list(data.get('words') or [])
            comm_keys = {services._community_word_key(w.get('english')) for w in words if w.get('english')}
            for row in rows:
                en = str(row.get('english', '')).strip()[:500]
                zh = str(row.get('chinese', '')).strip()[:500]
                ex_raw = row.get('example')
                ex = str(ex_raw).strip()[:4000] if ex_raw is not None else ''
                if not en or not zh:
                    skipped_invalid += 1
                    continue
                en = services._normalize_import_english_surface(en)
                if len(en) > 500:
                    en = en[:500]
                key = services._community_word_key(en)
                if key in system_keys:
                    rejected_in_system.append(en)
                    continue
                if key in comm_keys:
                    skipped_duplicate_community.append(en)
                    continue
                entry = {'english': en, 'chinese': zh, 'example': ex or None, 'added_by': username, 'added_at': services.china_now_iso(timespec='seconds'), 'status': 'pending', 'promotion_attempts': 0, 'last_attempt_at': None, 'last_error': None, 'promoted_at': None, 'promoted_word_key': None}
                words.append(entry)
                comm_keys.add(key)
                added_entries.append(entry)
            data['words'] = words
            if added_entries:
                services._write_community_file_atomic(data)
        queue_result: services.Optional[dict] = None
        queue_error: services.Optional[str] = None
        if also_queue and added_entries:
            to_queue = [{'english': e['english'], 'chinese': e['chinese'], 'example': e.get('example')} for e in added_entries]
            try:
                with services.user_reciter_session(username) as reciter:
                    queue_result = reciter.add_words_from_dicts(to_queue)
            except Exception as e:
                services.logger.error('简易导入后加入待复习失败: %s', e)
                queue_error = str(e)
        msg_parts = [f'共享词库新增 {len(added_entries)} 个单词']
        if rejected_in_system:
            msg_parts.append(f'{len(rejected_in_system)} 个因已在系统词库中未加入')
        if skipped_duplicate_community:
            msg_parts.append(f'{len(skipped_duplicate_community)} 个已在共享词库中')
        if skipped_invalid:
            msg_parts.append(f'{skipped_invalid} 条缺少单词或译文已忽略')
        msg = '；'.join(msg_parts) + '。'
        if queue_result:
            msg += f" 待复习：新加 {queue_result.get('added', 0)}，跳过重复 {queue_result.get('skipped_duplicate', 0)}。"
        if queue_error:
            msg += ' 共享词库已保存，但加入待复习失败，请稍后在共享词库中勾选导入。'
        services.logger.info('用户 %s 共享词库简易导入: added=%s sys=%s dup=%s invalid=%s queue=%s', username, len(added_entries), len(rejected_in_system), len(skipped_duplicate_community), skipped_invalid, bool(queue_result))
        payload: dict = {'message': msg, 'added_to_community': len(added_entries), 'rejected_in_system': rejected_in_system, 'skipped_duplicate_community': skipped_duplicate_community, 'skipped_invalid': skipped_invalid}
        if queue_result:
            payload['queue_added'] = queue_result.get('added')
            payload['queue_skipped_duplicate'] = queue_result.get('skipped_duplicate')
            payload['queue_skipped_invalid'] = queue_result.get('skipped_invalid')
        if queue_error:
            payload['queue_error'] = queue_error
        return (services.jsonify(payload), 200)

    app.register_blueprint(blueprint)
    return {
        'import_words_json': import_words_json,
        'textbooks_catalog': textbooks_catalog,
        'textbooks_lesson': textbooks_lesson,
        'get_wordbank_csv': get_wordbank_csv,
        'search_wordbank_csv': search_wordbank_csv,
        'import_from_article': import_from_article,
        'wordbank_ocr_extract': wordbank_ocr_extract,
        'import_vocab_to_csv': import_vocab_to_csv,
        'get_vocab_import_job': get_vocab_import_job,
        'wordbank_trouble_status': wordbank_trouble_status,
        'get_community_wordbank': get_community_wordbank,
        'list_pending_words_for_settings': list_pending_words_for_settings,
        'remove_pending_words_api': remove_pending_words_api,
        'community_import_simple': community_import_simple,
    }
