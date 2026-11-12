(() => {
    const base = '/exam-preparation';
    const typeNames = { spelling: '单词默写', translation: '句子中译英', recognition: '英文识义', context: '语境选词', fill_blank: '语法填空', rewrite: '句型转换', reading_choice: '阅读选择', listening_choice: '听力选择' };
    const gradeLabels = ['一年级', '二年级', '三年级', '四年级', '五年级', '预备班（六年级）', '初一（七年级）', '初二（八年级）', '初三（九年级）', '高一', '高二', '高三'];
    let papers = [];
    let selectedPaper = '';
    let gradeFilter = 'all';
    let termFilter = 'all';
    const expandedGroups = new Map();
    const paperChoices = new Map();
    let editingPaper = '';
    let newPaperDraft = null;
    let sentenceRows = [];
    let sentenceFieldId = 0;
    let wordRemoveButtons = [];
    let attempt = null;
    let result = null;
    let answers = {};
    let index = 0;
    let busy = false;
    let importBusy = false;
    let importFeedback = '';
    let generation = 0;
    let listRequest = 0;
    let draftKey = '';
    let knowledgeReturnButton = null;
    const el = (id) => document.getElementById(id);
    const context = () => ({ account: captureAccountContext(), generation });
    const current = (ctx) => ctx.generation === generation && isAccountContextCurrent(ctx.account);
    const canImportWords = () => typeof userPlan !== 'undefined' && userPlan === 'paid';
    const isWrittenQuestion = (question) => ['spelling', 'translation', 'fill_blank', 'rewrite'].includes(question.type);
    const paperGrade = (paper) => Number.isInteger(paper?.grade) && paper.grade >= 1 && paper.grade <= 12 ? String(paper.grade) : '';
    const gradeLabel = (value) => gradeLabels[Number(value) - 1] || '未分类';
    const paperTerm = (paper) => paper?.term === 1 || paper?.term === 2 ? String(paper.term) : '';
    const termLabel = (value) => value === '1' ? '上册' : value === '2' ? '下册' : '未分学期';
    const collectionKey = (paper) => `${paperGrade(paper)}:${paperTerm(paper)}`;
    const collectionLabel = (grade, term) => `${gradeLabel(grade)}${term ? termLabel(term) : (grade ? '（未分学期）' : '')}`;
    const paperContents = (paper) => paper.builtin ? `${paper.question_count} 道题` : [
        paper.word_count ? `${paper.word_count} 个单词` : '',
        paper.sentence_count ? `${paper.sentence_count} 道句子题` : '',
    ].filter(Boolean).join(' · ');

    function editorValue() {
        return {
            name: el('exam-paper-name').value,
            grade: el('exam-paper-grade').value ? Number(el('exam-paper-grade').value) : null,
            term: el('exam-paper-term').value ? Number(el('exam-paper-term').value) : null,
            words: el('exam-words').value,
            sentences: sentenceRows.map((row) => ({ chinese: row.chinese.value, answers: row.answers.value.split(/\r?\n/) })),
        };
    }

    function wordTokens(text) {
        return text.split(/[,，、;；\r\n]+/).map((word) => word.trim().replace(/\s+/g, ' ')
            .replace(/[’‘]/g, "'")).filter(Boolean);
    }

    function uniqueWords(text) {
        const words = new Map();
        wordTokens(text).forEach((word) => {
            const key = word.toLowerCase();
            if (!words.has(key)) words.set(key, word);
        });
        return [...words.values()].sort((left, right) => left.localeCompare(right, 'en', { sensitivity: 'base' }));
    }

    function wordEditorStatus(text = '', error = false) {
        const node = el('exam-word-editor-status');
        node.textContent = text;
        node.hidden = !text;
        node.classList.toggle('exam-message-error', error);
        node.setAttribute('role', error ? 'alert' : 'status');
    }

    function renderWordChips() {
        const list = el('exam-word-chips');
        list.replaceChildren();
        wordRemoveButtons = [];
        if (!editingPaper) return;
        const words = uniqueWords(el('exam-words').value);
        el('exam-words').value = words.join(', ');
        el('exam-word-chips-empty').hidden = words.length > 0;
        words.forEach((word, position) => {
            const chip = document.createElement('span');
            chip.className = 'exam-word-chip';
            const label = document.createElement('span');
            label.textContent = word;
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'exam-word-chip-remove';
            remove.textContent = '×';
            remove.disabled = busy;
            remove.setAttribute('aria-label', `删除 ${word}`);
            remove.addEventListener('click', () => {
                if (busy) return;
                el('exam-words').value = words.filter((item) => item !== word).join(', ');
                hideMissingImport();
                renderWordChips();
                updateWordCount();
                wordEditorStatus(`已移除 ${word}，保存修改后生效。`);
                (wordRemoveButtons[Math.min(position, wordRemoveButtons.length - 1)] || el('exam-add-words')).focus({ preventScroll: true });
            });
            wordRemoveButtons.push(remove);
            chip.append(label, remove);
            list.appendChild(chip);
        });
    }

    function addEditorWords(focus = true) {
        if (busy || !editingPaper) return false;
        const input = el('exam-add-words');
        const additions = wordTokens(input.value);
        if (!additions.length) {
            wordEditorStatus('请先输入要添加的单词或词组。', true);
            return false;
        }
        const invalid = additions.find((word) => word.length > 120 || !/^[a-z0-9 .'-]+$/i.test(word) || !/[a-z]/i.test(word));
        if (invalid) {
            wordEditorStatus(`请检查英文单词或词组：${invalid.slice(0, 60)}`, true);
            input.focus();
            return false;
        }
        const previous = uniqueWords(el('exam-words').value);
        const words = uniqueWords([...previous, ...additions].join(', '));
        if (words.length > 300 || words.join(', ').length > 20000) {
            wordEditorStatus('每张考卷最多 300 个不同的单词，单词内容最多 20000 个字符。', true);
            input.focus();
            return false;
        }
        const added = words.length - previous.length;
        const duplicates = additions.length - added;
        el('exam-words').value = words.join(', ');
        input.value = '';
        hideMissingImport();
        renderWordChips();
        updateWordCount();
        wordEditorStatus(`已添加 ${added} 个单词。${duplicates ? `已忽略 ${duplicates} 个重复项。` : ''}`);
        if (focus) input.focus({ preventScroll: true });
        return true;
    }

    function updateEditor() {
        el('exam-editor-title').textContent = editingPaper ? '编辑考卷' : '新建考卷';
        el('exam-editor-hint').hidden = !editingPaper;
        el('exam-cancel-edit').hidden = !editingPaper;
        el('exam-save-paper').textContent = editingPaper ? '保存修改' : '保存并生成考卷';
        el('exam-edit-paper').textContent = editingPaper ? '正在旁边编辑' : '编辑考卷';
        el('exam-edit-paper').disabled = busy || !!editingPaper || !selectedPaper;
        el('exam-words-raw').hidden = !!editingPaper;
        el('exam-word-editor').hidden = !editingPaper;
        el('exam-add-words').disabled = busy;
        el('exam-add-word-button').disabled = busy;
        wordRemoveButtons.forEach((button) => { button.disabled = busy; });
        el('exam-sentence-count').textContent = `${sentenceRows.length} 道`;
        el('exam-add-sentence').disabled = busy || sentenceRows.length >= 100;
        el('exam-start').disabled = busy || !!editingPaper || !selectedPaper || isParentSession;
        const selected = papers.find((paper) => paper.id === selectedPaper);
        el('exam-edit-paper').hidden = !!selected?.builtin;
        el('exam-include-choices').disabled = busy || isParentSession || !!selected?.builtin || !selected?.word_count;
        ['exam-import-words', 'exam-import-level', 'exam-import-add-to-queue', 'exam-import-dismiss'].forEach((id) => {
            el(id).disabled = busy;
        });
        el('exam-import-submit').disabled = busy || !canImportWords() || !el('exam-import-words').value.trim();
        el('exam-import-submit').textContent = importBusy ? '导入处理中…' : '导入并保存考卷';
        el('exam-import-locked').hidden = canImportWords();
    }

    function importStatus(text = '', error = false) {
        const node = el('exam-import-status');
        node.textContent = text;
        node.hidden = !text;
        node.classList.toggle('exam-message-error', error);
        node.setAttribute('role', error ? 'alert' : 'status');
    }

    function hideMissingImport() {
        el('exam-import-panel').hidden = true;
        el('exam-import-words').value = '';
        el('exam-import-level').value = '';
        el('exam-import-add-to-queue').checked = false;
        importFeedback = '';
        importStatus();
    }

    function showMissingImport(words) {
        el('exam-import-panel').hidden = false;
        el('exam-import-words').value = words.join('\n');
        importStatus(importFeedback ? `${importFeedback} 以下单词仍缺少释义，请检查拼写后重试。` : '', !!importFeedback);
        updateEditor();
        el('exam-import-panel').scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
    }

    async function waitForExamImportJob(jobId, ctx) {
        const deadline = Date.now() + 15 * 60 * 1000;
        while (current(ctx) && Date.now() < deadline) {
            await new Promise((resolve) => setTimeout(resolve, 1200));
            if (!current(ctx)) return null;
            const job = await apiRequest(`/wordbank/import-jobs/${encodeURIComponent(jobId)}`);
            if (!current(ctx)) return null;
            if (job.status === 'succeeded' && job.result) return job.result;
            if (job.status === 'failed') throw new Error(job.error || '后台导入失败，请重试');
        }
        if (!current(ctx)) return null;
        throw new Error('后台导入仍在进行，请稍后重试保存考卷');
    }

    async function importMissingWords() {
        if (busy) return;
        if (!canImportWords()) {
            importStatus('词汇导入仅限 VIP 用户使用，请联系管理员升级账号。', true);
            return;
        }
        const tokens = [...new Set(parseVocabImportTokens(el('exam-import-words').value))];
        if (!tokens.length) {
            importStatus('请先输入需要导入的单词或词组。', true);
            return;
        }
        const ctx = context();
        importBusy = true;
        setBusy(true);
        importStatus('正在导入单词，当前考卷内容已保留…');
        try {
            let data = await apiRequest('/wordbank/csv/import-words', {
                method: 'POST', body: JSON.stringify({
                    words: vocabImportWordsForApi(tokens), level: el('exam-import-level').value,
                    also_add_to_queue: el('exam-import-add-to-queue').checked,
                }),
            });
            if (!current(ctx)) return;
            if (data.job_id) {
                importStatus('已提交后台导入，完成后会继续保存考卷，请稍候…');
                data = await waitForExamImportJob(data.job_id, ctx);
            }
            if (!current(ctx) || !data) return;
            if (Number(data.queue_result?.added) > 0 && typeof invalidateReviewDataAfterImport === 'function') {
                invalidateReviewDataAfterImport();
            }
            if (typeof clearWordbankCsvDiscoveryCache === 'function') clearWordbankCsvDiscoveryCache();
            const failed = [...new Set(['failed', 'blocked_surfaces', 'invalid_surfaces']
                .flatMap((key) => Array.isArray(data[key]) ? data[key] : []).filter((word) => typeof word === 'string'))];
            importFeedback = failed.length ? `未能导入：${failed.join('、')}。` : '导入处理完成。';
            importBusy = false;
            setBusy(false);
            await savePaper({ preventDefault() {} });
        } catch (error) {
            if (current(ctx)) importStatus(error.message || '导入失败，请重试', true);
        } finally {
            if (current(ctx)) {
                importBusy = false;
                setBusy(false);
            }
        }
    }

    function addSentence(value = { chinese: '', answers: [] }) {
        const row = document.createElement('div');
        row.className = 'exam-sentence-row';
        const head = document.createElement('div');
        head.className = 'exam-sentence-head';
        const title = document.createElement('strong');
        title.textContent = `句子题 ${sentenceRows.length + 1}`;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.disabled = busy;
        remove.className = 'btn btn-secondary exam-remove-sentence';
        remove.textContent = '移除';
        remove.setAttribute('aria-label', `移除句子题 ${sentenceRows.length + 1}`);
        head.append(title, remove);
        row.appendChild(head);
        const fields = {};
        for (const [key, labelText, placeholder] of [
            ['chinese', '中文题干', '例如：我每天步行去学校。'],
            ['answers', '英文参考答案（每行一个，最多 10 个译法）', 'I walk to school every day.\nI go to school on foot every day.'],
        ]) {
            const label = document.createElement('label');
            const input = document.createElement('textarea');
            input.id = `exam-sentence-field-${++sentenceFieldId}`;
            label.htmlFor = input.id;
            label.textContent = labelText;
            input.rows = key === 'chinese' ? 2 : 3;
            input.maxLength = key === 'chinese' ? 500 : 5009;
            input.required = true;
            input.disabled = busy;
            input.placeholder = placeholder;
            input.value = key === 'chinese' ? value.chinese : value.answers.join('\n');
            input.setAttribute('aria-describedby', 'exam-sentence-grading-hint');
            input.addEventListener('invalid', () => { el('exam-sentences-section').open = true; });
            if (key === 'answers') {
                input.spellcheck = false;
                input.setAttribute('autocapitalize', 'none');
                input.setAttribute('autocorrect', 'off');
            }
            fields[key] = input;
            row.append(label, input);
        }
        const entry = { ...fields, remove };
        sentenceRows.push(entry);
        remove.addEventListener('click', () => {
            if (busy) return;
            const values = editorValue().sentences;
            values.splice(sentenceRows.indexOf(entry), 1);
            renderSentences(values);
        });
        el('exam-sentence-list').appendChild(row);
        updateEditor();
        return fields.chinese;
    }

    function renderSentences(values) {
        sentenceRows = [];
        el('exam-sentence-list').replaceChildren();
        values.forEach((value) => addSentence(value));
        updateEditor();
    }

    function fillEditor(value) {
        hideMissingImport();
        el('exam-paper-name').value = value?.name || '';
        el('exam-paper-grade').value = value ? paperGrade(value) : (gradeFilter === 'all' ? '' : gradeFilter);
        el('exam-paper-term').value = value ? paperTerm(value) : (termFilter === 'all' ? '' : termFilter);
        el('exam-words').value = value?.words || '';
        el('exam-add-words').value = '';
        wordEditorStatus();
        renderWordChips();
        renderSentences(value?.sentences || []);
        el('exam-sentences-section').open = sentenceRows.length > 0;
        updateWordCount();
    }

    function editPaper() {
        if (busy || editingPaper) return;
        const paper = papers.find((item) => item.id === selectedPaper);
        if (!paper || paper.builtin) return;
        newPaperDraft = editorValue();
        editingPaper = paper.id;
        fillEditor({ name: paper.name, grade: paper.grade, term: paper.term, words: paper.words.map((word) => word.english).join(', '), sentences: paper.sentences || [] });
        message(`正在编辑“${paper.name}”，修改后请保存。`);
        el('exam-editor-title').scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
        el('exam-add-words').focus({ preventScroll: true });
    }

    function finishEditing() {
        editingPaper = '';
        fillEditor(newPaperDraft);
        newPaperDraft = null;
    }

    function message(text = '', error = false) {
        const node = el('exam-preparation-message');
        if (!node) return;
        node.textContent = text;
        node.hidden = !text;
        node.classList.toggle('exam-message-error', error);
        node.setAttribute('role', error ? 'alert' : 'status');
    }

    function showView(view) {
        if (view !== 'attempt') stopListeningAudio();
        hideAbandonConfirmation();
        ['setup', 'attempt', 'result', 'knowledge'].forEach((name) => {
            el(`exam-${name}-view`).hidden = name !== view;
        });
    }

    function setBusy(value) {
        busy = value;
        if (value) el('exam-listening-audio').pause?.();
        updateMistakeControls();
        ['exam-save-paper', 'exam-start', 'exam-previous', 'exam-next', 'exam-submit',
            'exam-abandon', 'exam-abandon-confirm', 'exam-abandon-cancel',
            'exam-words', 'exam-paper-name', 'exam-paper-grade', 'exam-paper-term', 'exam-grade-filter', 'exam-term-filter', 'exam-cancel-edit', 'exam-back-to-papers', 'exam-knowledge-back'].forEach((id) => {
            const button = el(id);
            if (button) button.disabled = value;
        });
        const input = el('exam-spelling-answer');
        if (input) input.disabled = value;
        el('exam-choice-options')?.querySelectorAll('button').forEach((button) => { button.disabled = value; });
        el('exam-paper-list').querySelectorAll('button').forEach((button) => { button.disabled = value; });
        sentenceRows.forEach((row) => {
            row.chinese.disabled = value;
            row.answers.disabled = value;
            row.remove.disabled = value;
        });
        if (!value) {
            el('exam-previous').disabled = !attempt || index === 0;
        }
        updateEditor();
    }

    function storeDraft() {
        if (!attempt || !draftKey) return;
        try {
            sessionStorage.setItem(draftKey, JSON.stringify({ id: attempt.id, answers, index }));
        } catch (_) { /* An exam can still run when browser storage is unavailable. */ }
    }

    function clearDraft() {
        try { if (draftKey) sessionStorage.removeItem(draftKey); } catch (_) { /* Storage is optional. */ }
    }

    function readDraft() {
        try { return JSON.parse(sessionStorage.getItem(draftKey) || 'null'); } catch (_) { return null; }
    }

    function hideAbandonConfirmation() {
        el('exam-abandon-confirmation').hidden = true;
        el('exam-abandon').setAttribute('aria-expanded', 'false');
    }

    function requestAbandon() {
        if (busy || !attempt || result) return;
        const reviewing = attempt.mode === 'wrong_review';
        el('exam-abandon-title').textContent = reviewing ? '确认放弃本次复习？' : '确认放弃本次考试？';
        el('exam-abandon-description').textContent = reviewing
            ? '放弃后将清除本次答题进度，返回选择考卷；已保存的错题会保留。'
            : '放弃后将清除本次答题进度，返回选择考卷；不会生成成绩。';
        el('exam-abandon-confirmation').hidden = false;
        el('exam-abandon').setAttribute('aria-expanded', 'true');
        el('exam-listening-audio').pause?.();
        el('exam-abandon-cancel').focus({ preventScroll: true });
    }

    function abandonExam() {
        if (busy || !attempt || result || el('exam-abandon-confirmation').hidden) return;
        const reviewing = attempt.mode === 'wrong_review';
        selectedPaper = attempt.paper_id;
        // Ignore any older restore response that could bring this attempt back.
        generation += 1;
        listRequest += 1;
        clearDraft();
        attempt = null;
        result = null;
        answers = {};
        index = 0;
        el('exam-spelling-answer').value = '';
        el('exam-choice-options').replaceChildren();
        ['exam-attempt-name', 'exam-question-prompt', 'exam-question-position', 'exam-answer-count'].forEach((id) => {
            el(id).textContent = '';
        });
        el('exam-answer-progress').value = 0;
        showView('setup');
        renderPapers();
        setBusy(false);
        message(reviewing ? '已放弃本次复习，已保存的错题仍可继续复习。' : '已放弃本次考试，未生成成绩。可以重新选择考卷。');
        el('exam-start').focus({ preventScroll: true });
    }

    function gradeOptions(select, filter = false) {
        select.replaceChildren();
        const counts = new Map();
        const available = papers.filter((paper) => !filter || termFilter === 'all' || paperTerm(paper) === termFilter);
        available.forEach((paper) => {
            const grade = paperGrade(paper);
            counts.set(grade, (counts.get(grade) || 0) + 1);
        });
        const option = (parent, value, label) => {
            const node = document.createElement('option');
            node.value = value;
            node.textContent = label;
            parent.appendChild(node);
        };
        if (filter) option(select, 'all', `全部年级（${available.length}）`);
        else option(select, '', '未分类');
        for (const [stage, start, end] of [['小学', 1, 5], ['初中', 6, 9], ['高中', 10, 12]]) {
            const group = document.createElement('optgroup');
            group.label = stage;
            for (let grade = start; grade <= end; grade += 1) {
                option(group, String(grade), gradeLabel(grade) + (filter ? `（${counts.get(String(grade)) || 0}）` : ''));
            }
            select.appendChild(group);
        }
        if (filter) option(select, '', `未分类（${counts.get('') || 0}）`);
    }

    function renderPapers() {
        const controls = el('exam-custom-controls');
        controls.hidden = true;
        el('exam-custom-controls-home').appendChild(controls);
        const list = el('exam-paper-list');
        list.replaceChildren();
        gradeOptions(el('exam-grade-filter'), true);
        el('exam-grade-filter').value = gradeFilter;
        el('exam-term-filter').value = termFilter;
        const visible = papers.filter((paper) => (gradeFilter === 'all' || paperGrade(paper) === gradeFilter)
            && (termFilter === 'all' || paperTerm(paper) === termFilter));
        const scope = gradeFilter === 'all'
            ? `全部年级${termFilter === 'all' ? '' : ` · ${termLabel(termFilter)}`}`
            : termFilter === 'all' ? gradeLabel(gradeFilter) : collectionLabel(gradeFilter, termFilter);
        el('exam-grade-summary').textContent = `${scope} · 已收录 ${visible.length} 份考卷`;
        el('exam-paper-empty').hidden = visible.length > 0;
        el('exam-paper-empty').textContent = gradeFilter === 'all' && termFilter === 'all'
            ? '还没有考卷，先添加单词或句子题并保存一张吧。'
            : `${scope}暂无考卷，可在新建考卷中选择对应年级和学期后收录。`;
        el('exam-paper-settings').hidden = visible.length === 0;
        if (!visible.some((paper) => paper.id === selectedPaper)) selectedPaper = visible[0]?.id || '';
        const groups = new Map();
        const keys = [...new Set(visible.map(collectionKey))].sort((left, right) => {
            const rank = (key) => {
                const [grade, term] = key.split(':');
                return (Number(grade) || 13) * 3 + (Number(term) || 3);
            };
            return rank(left) - rank(right);
        });
        const selectedKey = collectionKey(visible.find((paper) => paper.id === selectedPaper));
        keys.forEach((key) => {
            const [grade, term] = key.split(':');
            const group = document.createElement('details');
            group.className = 'exam-grade-group';
            group.open = expandedGroups.get(key) ?? key === selectedKey;
            group.addEventListener('toggle', () => expandedGroups.set(key, group.open));
            const heading = document.createElement('summary');
            heading.className = 'exam-grade-heading';
            heading.textContent = collectionLabel(grade, term);
            const count = document.createElement('span');
            count.textContent = `${visible.filter((paper) => collectionKey(paper) === key).length} 份`;
            heading.appendChild(count);
            group.appendChild(heading);
            groups.set(key, group);
            list.appendChild(group);
        });
        for (const paper of visible) {
            const group = groups.get(collectionKey(paper));
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'exam-paper-card';
            button.disabled = busy;
            button.setAttribute('aria-pressed', String(paper.id === selectedPaper));
            const title = document.createElement('strong');
            title.textContent = paper.name;
            const detail = document.createElement('span');
            detail.textContent = paper.builtin ? `${paperContents(paper)}${paper.listening_count ? ` · 听力 ${paper.listening_count} 题` : ''} · 建议 ${paper.time_minutes} 分钟` : `${paperContents(paper)} · ${paper.created_at.slice(0, 10)}`;
            const score = document.createElement('span');
            score.textContent = paper.attempt_count
                ? `最近 ${paper.last_score} 分 · 最高 ${paper.best_score} 分 · 已考 ${paper.attempt_count} 次`
                : '还未考试';
            if (paper.mistake_count) score.textContent += ` · 待复习错题 ${paper.mistake_count} 题`;
            button.append(title, detail, score);
            button.addEventListener('click', () => {
                if (busy) return;
                selectedPaper = paper.id;
                renderPapers();
            });
            if (paper.builtin) {
                const badge = document.createElement('span');
                badge.className = 'exam-paper-badge';
                badge.textContent = '内置考卷';
                button.appendChild(badge);
                const row = document.createElement('div');
                row.className = 'exam-builtin-row';
                const knowledgeButton = document.createElement('button');
                knowledgeButton.type = 'button';
                knowledgeButton.className = 'btn btn-secondary';
                knowledgeButton.textContent = '知识点梳理';
                knowledgeButton.disabled = busy;
                knowledgeButton.setAttribute('aria-label', `${paper.name}：知识点梳理`);
                knowledgeButton.setAttribute('aria-controls', 'exam-knowledge-view');
                knowledgeButton.addEventListener('click', () => openKnowledge(paper.id, knowledgeButton));
                row.append(button, knowledgeButton);
                group.appendChild(row);
            } else {
                const row = document.createElement('div');
                row.className = 'exam-custom-paper';
                row.appendChild(button);
                if (paper.id === selectedPaper) {
                    controls.hidden = false;
                    controls.setAttribute('aria-label', `${paper.name}：考卷设置`);
                    row.appendChild(controls);
                }
                group.appendChild(row);
            }
        }
        const selected = papers.find((paper) => paper.id === selectedPaper);
        el('exam-paper-preview').textContent = selected ? selected.words.map((word) => word.english).join(', ') : '';
        el('exam-paper-preview').hidden = !selected?.word_count;
        const sentences = selected?.sentences || [];
        el('exam-paper-sentence-preview').hidden = sentences.length === 0;
        el('exam-paper-sentence-preview').textContent = sentences.map((sentence, i) => `${i + 1}. ${sentence.chinese}`).join('\n');
        el('exam-parent-hint').hidden = !isParentSession;
        el('exam-include-choices').checked = !selected?.builtin && paperChoices.get(selectedPaper) === true;
        el('exam-choice-toggle').hidden = !!selected?.builtin;
        el('exam-choice-hint').hidden = !!selected?.builtin;
        el('exam-builtin-hint').hidden = !selected?.builtin;
        el('exam-builtin-hint').textContent = selected?.builtin ? `${selected.description} ${selected.source_note || ''}` : '';
        updateEditor();
        updateMistakeControls();
    }

    function updateMistakeControls() {
        const selected = papers.find((paper) => paper.id === selectedPaper);
        const count = Math.max(0, Number(selected?.mistake_count) || 0);
        el('exam-paper-mistake-count').textContent = count ? `待复习错题 ${count} 题` : '暂无待复习错题';
        el('exam-review-paper-mistakes').disabled = busy || isParentSession || !selectedPaper || count === 0;
        el('exam-review-paper-mistakes').textContent = count ? `复习错题（${count} 题）` : '复习错题';
        const remaining = Math.max(0, Number(result?.mistake_count) || 0);
        el('exam-review-mistakes').hidden = !result || remaining === 0 || isParentSession;
        el('exam-review-mistakes').disabled = busy || isParentSession;
        el('exam-review-mistakes').textContent = `复习错题（${remaining} 题）`;
    }

    function applyResult(data) {
        result = {
            ...data.result,
            mistake_count: Math.max(0, Number(data.mistake_count ?? data.result.mistake_count ?? data.result.wrong_count) || 0),
        };
        const paper = papers.find((item) => item.id === result.paper_id);
        if (paper) paper.mistake_count = result.mistake_count;
    }

    async function refreshPapers() {
        const ctx = context();
        const request = ++listRequest;
        const data = await apiRequest(`${base}/papers`);
        if (!current(ctx) || request !== listRequest) return;
        papers = [...data.papers, ...(data.builtin_papers || [])];
        renderPapers();
    }

    async function openKnowledge(paperId, button) {
        if (busy) return;
        const ctx = context();
        setBusy(true);
        message('正在加载知识点梳理…');
        try {
            const data = await apiRequest(`${base}/papers/${encodeURIComponent(paperId)}/knowledge`);
            if (!current(ctx)) return;
            knowledgeReturnButton = button;
            el('exam-knowledge-title').textContent = data.knowledge.title;
            el('exam-knowledge-intro').textContent = data.knowledge.intro;
            el('exam-knowledge-pdf').href = data.pdf_url;
            el('exam-knowledge-pdf').textContent = data.exam_pdf_url ? '阅读知识点复习手册（PDF） ↗' : '阅读完整复习手册（PDF，含自测与参考答案） ↗';
            el('exam-knowledge-exam-pdf').hidden = !data.exam_pdf_url;
            el('exam-knowledge-exam-pdf').href = data.exam_pdf_url || '';
            const content = el('exam-knowledge-content');
            content.replaceChildren();
            data.knowledge.sections.forEach((section, index) => {
                const panel = document.createElement('details');
                panel.open = index === 0;
                const summary = document.createElement('summary');
                summary.textContent = section.title;
                panel.appendChild(summary);
                section.blocks.forEach((block) => {
                    const heading = document.createElement('h4');
                    heading.textContent = block.title;
                    const list = document.createElement('ul');
                    block.items.forEach((item) => {
                        const line = document.createElement('li');
                        line.textContent = item;
                        list.appendChild(line);
                    });
                    panel.append(heading, list);
                });
                content.appendChild(panel);
            });
            message();
            showView('knowledge');
            el('exam-knowledge-view').scrollIntoView?.({ behavior: 'smooth', block: 'start' });
            el('exam-knowledge-title').focus({ preventScroll: true });
        } catch (error) {
            if (current(ctx)) message(error.message || '加载知识点失败，请重试', true);
        } finally {
            if (current(ctx)) setBusy(false);
        }
    }

    async function open() {
        draftKey = `english-reciter-exam:${sessionStudentUsername()}`;
        if (attempt || result) return;
        const ctx = context();
        showView('setup');
        message();
        el('exam-paper-empty').textContent = '正在加载考卷…';
        try {
            await refreshPapers();
            if (!current(ctx)) return;
            const draft = readDraft();
            if (draft?.id && !isParentSession && !busy && !attempt && !result) {
                const data = await apiRequest(`${base}/attempts/${encodeURIComponent(draft.id)}?supports_listening=1`);
                if (!current(ctx) || busy || attempt || result) return;
                if (data.result) {
                    applyResult(data);
                    clearDraft();
                    renderResult();
                } else {
                    attempt = data.attempt;
                    answers = draft.answers && typeof draft.answers === 'object' ? draft.answers : {};
                    index = Math.max(0, Math.min(attempt.questions.length - 1, Number(draft.index) || 0));
                    renderQuestion(false);
                    message(attempt.mode === 'wrong_review' ? '已恢复上次未完成的错题复习。' : '已恢复上次未交卷的考试。');
                }
            }
        } catch (error) {
            if (!current(ctx)) return;
            if (error.status === 404) clearDraft();
            el('exam-paper-empty').textContent = '暂无可显示的考卷。';
            message(error.message || '加载考卷失败，请重新进入后重试', true);
        }
    }

    async function savePaper(event) {
        event.preventDefault();
        if (busy) return;
        if (editingPaper && el('exam-add-words').value.trim() && !addEditorWords(false)) return;
        const ctx = context();
        const paperId = editingPaper;
        const content = editorValue();
        content.sentences = content.sentences.map((sentence) => ({
            chinese: sentence.chinese, answers: sentence.answers.map((answer) => answer.trim()).filter(Boolean),
        }));
        setBusy(true);
        message(paperId ? '正在保存修改…' : '正在生成考卷…');
        try {
            const data = await apiRequest(`${base}/papers${paperId ? `/${encodeURIComponent(paperId)}` : ''}`, {
                method: paperId ? 'PUT' : 'POST', body: JSON.stringify(content),
            });
            if (!current(ctx)) return;
            selectedPaper = data.paper.id;
            if (gradeFilter !== 'all') gradeFilter = paperGrade(data.paper);
            if (termFilter !== 'all') termFilter = paperTerm(data.paper);
            expandedGroups.set(collectionKey(data.paper), true);
            if (paperId) finishEditing();
            else fillEditor(null);
            papers = [data.paper, ...papers.filter((paper) => paper.id !== data.paper.id)];
            renderPapers();
            message(`“${data.paper.name}”${paperId ? '已更新' : '已保存'}，共 ${paperContents(data.paper)}。${data.duplicates_removed ? `已合并 ${data.duplicates_removed} 个重复单词。` : ''}`);
            await refreshPapers();
        } catch (error) {
            if (current(ctx)) {
                const missing = Array.isArray(error.missing_words) ? error.missing_words : [];
                if (missing.length) {
                    message('部分单词缺少中文释义，可在下方导入后继续保存考卷。', true);
                    showMissingImport(missing);
                } else {
                    message(error.message || '保存考卷失败，请重试', true);
                }
            }
        } finally {
            if (current(ctx)) setBusy(false);
        }
    }

    async function startExam() {
        if (busy || editingPaper || !selectedPaper || isParentSession) return;
        const ctx = context();
        const selected = papers.find((paper) => paper.id === selectedPaper);
        const includeChoices = !selected?.builtin && !!selected?.word_count && paperChoices.get(selectedPaper) === true;
        setBusy(true);
        message('正在准备考试…');
        try {
            const data = await apiRequest(`${base}/papers/${encodeURIComponent(selectedPaper)}/attempts`, {
                method: 'POST', body: JSON.stringify({ include_choices: includeChoices, ...(selected?.builtin ? { supports_listening: true } : {}) }),
            });
            if (!current(ctx)) return;
            attempt = data.attempt;
            result = null;
            answers = {};
            index = 0;
            storeDraft();
            renderQuestion();
            if (attempt.builtin) {
                message(`本次为内置固定考卷，共 ${attempt.questions.length} 题${attempt.listening_count ? `（含听力 ${attempt.listening_count} 题）` : ''}，建议 ${attempt.time_minutes} 分钟完成。填空只写空缺部分，句型转换写完整句子。`);
                return;
            }
            const missing = attempt.word_count * 2 - attempt.choice_count;
            const translationInfo = attempt.sentence_count ? `本次包含 ${attempt.sentence_count} 道句子中译英题。` : '';
            message(translationInfo + (attempt.include_choices
                ? `本次考试包含 ${attempt.word_count} 道默写题和 ${attempt.choice_count} 道选择题。${missing ? `有 ${missing} 道选择题暂不可用，默写题已全部保留。` : ''}`
                : '根据中文填写英文答案，完成后交卷查看成绩。'));
        } catch (error) {
            if (current(ctx)) message(error.message || '开始考试失败，请重试', true);
        } finally {
            if (current(ctx)) {
                setBusy(false);
                focusAnswer();
            }
        }
    }

    async function startMistakeReview(paperId) {
        if (busy || isParentSession || !paperId) return;
        const ctx = context();
        setBusy(true);
        message('正在准备错题复习…');
        try {
            const data = await apiRequest(`${base}/papers/${encodeURIComponent(paperId)}/mistake-reviews`, {
                method: 'POST', body: JSON.stringify({ supports_listening: true }),
            });
            if (!current(ctx)) return;
            attempt = data.attempt;
            result = null;
            answers = {};
            index = 0;
            storeDraft();
            renderQuestion();
            message(`本次复习 ${attempt.questions.length} 道错题。答对后移出待复习列表，答错的题会继续保留。`);
        } catch (error) {
            if (!current(ctx)) return;
            if (error.status === 409) {
                const paper = papers.find((item) => item.id === paperId);
                if (paper) paper.mistake_count = 0;
                if (result?.paper_id === paperId) result.mistake_count = 0;
                updateMistakeControls();
            }
            message(error.message || '加载错题失败，请重试', error.status !== 409);
        } finally {
            if (current(ctx)) {
                setBusy(false);
                focusAnswer();
            }
        }
    }

    function answeredCount() {
        return attempt.questions.filter((question) => String(answers[question.id] || '').trim()).length;
    }

    function updateProgress() {
        const count = answeredCount();
        el('exam-answer-progress').max = attempt.questions.length;
        el('exam-answer-progress').value = count;
        el('exam-answer-count').textContent = `已答 ${count} / ${attempt.questions.length} 题`;
        const action = attempt.mode === 'wrong_review' ? '提交复习' : '交卷';
        el('exam-submit').textContent = count === attempt.questions.length
            ? `${action}并查看成绩` : `${action}（${attempt.questions.length - count} 题未答）`;
    }

    function focusAnswer() {
        if (!attempt || busy || !el('exam-preparation-section').classList.contains('active')) return;
        const question = attempt.questions[index];
        const target = question.type === 'listening_choice' ? el('exam-listening-audio') : isWrittenQuestion(question)
            ? el('exam-spelling-answer')
            : el('exam-choice-options').querySelector('[aria-pressed="true"]') || el('exam-choice-options').querySelector('button');
        target?.focus({ preventScroll: true });
    }

    function stopListeningAudio() {
        const audio = el('exam-listening-audio');
        if (!audio) return;
        audio.onerror = null;
        audio.pause?.();
        audio.removeAttribute?.('src');
        audio.load?.();
        el('exam-listening-wrap').hidden = true;
        el('exam-listening-error').hidden = true;
    }

    function renderQuestion(focus = true) {
        showView('attempt');
        stopListeningAudio();
        const question = attempt.questions[index];
        if (question.type === 'listening_choice') {
            el('exam-listening-wrap').hidden = false;
            const audio = el('exam-listening-audio');
            if (typeof question.audio_url === 'string' && question.audio_url.startsWith('/static/exam-preparation/audio/')) {
                audio.src = question.audio_url;
                audio.onerror = () => {
                    if (attempt?.questions[index]?.id === question.id) el('exam-listening-error').hidden = false;
                };
            } else {
                el('exam-listening-error').hidden = false;
            }
        }
        el('exam-attempt-name').textContent = attempt.paper_name;
        el('exam-abandon').textContent = attempt.mode === 'wrong_review' ? '放弃本次复习' : '放弃本次考试';
        el('exam-question-position').textContent = `第 ${index + 1} / ${attempt.questions.length} 题`;
        el('exam-question-type').textContent = typeNames[question.type];
        el('exam-question-prompt').textContent = question.prompt;
        el('exam-spelling-wrap').hidden = !isWrittenQuestion(question);
        el('exam-choice-options').hidden = isWrittenQuestion(question);
        const input = el('exam-spelling-answer');
        input.value = isWrittenQuestion(question) ? answers[question.id] || '' : '';
        input.maxLength = question.type === 'spelling' ? 200 : 500;
        el('exam-answer-label').textContent = ({ translation: '请将中文句子译成英文', fill_blank: '请填写空缺部分', rewrite: '请写出转换后的完整句子' })[question.type] || '请默写英文单词或短语';
        el('exam-answer-hint').hidden = question.type !== 'translation';
        const options = el('exam-choice-options');
        options.replaceChildren();
        for (const [optionIndex, option] of (question.options || []).entries()) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'exam-choice';
            button.textContent = `${String.fromCharCode(65 + optionIndex)}. ${option.text}`;
            button.setAttribute('aria-pressed', String(answers[question.id] === option.id));
            button.addEventListener('click', () => {
                if (busy) return;
                answers[question.id] = option.id;
                options.querySelectorAll('button').forEach((node) => node.setAttribute('aria-pressed', String(node === button)));
                storeDraft();
                updateProgress();
            });
            options.appendChild(button);
        }
        el('exam-previous').disabled = busy || index === 0;
        el('exam-next').hidden = index === attempt.questions.length - 1;
        el('exam-next').disabled = busy;
        updateProgress();
        if (focus) focusAnswer();
    }

    function moveQuestion(offset) {
        if (busy || !attempt) return;
        index = Math.max(0, Math.min(attempt.questions.length - 1, index + offset));
        storeDraft();
        renderQuestion();
    }

    async function submitExam() {
        if (busy || !attempt) return;
        const ctx = context();
        setBusy(true);
        message('正在批改考卷…');
        try {
            const data = await apiRequest(`${base}/attempts/${encodeURIComponent(attempt.id)}/submit`, {
                method: 'POST', body: JSON.stringify({ answers }),
            });
            if (!current(ctx)) return;
            applyResult(data);
            clearDraft();
            message();
            renderResult();
        } catch (error) {
            if (current(ctx)) message(error.message || '交卷失败，答案已保留，请重试', true);
        } finally {
            if (current(ctx)) setBusy(false);
        }
    }

    function renderResult() {
        showView('result');
        const reviewing = result.mode === 'wrong_review';
        el('exam-result-heading').textContent = reviewing ? '错题复习完成' : '考试完成';
        el('exam-result-name').textContent = result.paper_name;
        el('exam-result-score').textContent = `${result.score} 分`;
        el('exam-result-summary').textContent = `共 ${result.total} 题，答对 ${result.correct_count} 题，答错 ${result.wrong_count} 题。`;
        el('exam-result-perfect').hidden = result.wrong_count > 0;
        el('exam-mistake-status').textContent = result.mistake_count
            ? `错题已保存，待复习 ${result.mistake_count} 题。返回考卷后也可以继续复习。`
            : reviewing ? '错题已全部巩固，继续保持！' : '当前没有待复习错题。';
        updateMistakeControls();
        el('exam-wrong-title').textContent = `错题回顾（${result.wrong_count} 题）`;
        const list = el('exam-wrong-list');
        list.replaceChildren();
        for (const question of result.questions.filter((row) => !row.correct)) {
            const card = document.createElement('article');
            card.className = 'exam-wrong-card';
            const title = document.createElement('h4');
            title.textContent = ['translation', 'fill_blank', 'rewrite', 'reading_choice', 'listening_choice'].includes(question.type) ? typeNames[question.type] : `${question.english} · ${typeNames[question.type]}`;
            card.appendChild(title);
            [question.prompt, `你的答案：${question.user_answer || '未作答'}`, `正确答案：${question.correct_answer}`,
                question.translation, question.explanation].filter(Boolean).forEach((text) => {
                const line = document.createElement('p');
                line.textContent = text;
                card.appendChild(line);
            });
            list.appendChild(card);
        }
        el('exam-result-heading').focus({ preventScroll: true });
    }

    function updateWordCount() {
        const count = uniqueWords(el('exam-words').value).length;
        el('exam-word-count').textContent = `${editingPaper ? '已选' : '已输入'} ${count} 个不同的单词 · 最多 300 个`;
    }

    function reset() {
        generation += 1;
        listRequest += 1;
        clearDraft();
        papers = [];
        selectedPaper = '';
        gradeFilter = 'all';
        termFilter = 'all';
        expandedGroups.clear();
        paperChoices.clear();
        editingPaper = '';
        newPaperDraft = null;
        knowledgeReturnButton = null;
        attempt = null;
        result = null;
        answers = {};
        index = 0;
        draftKey = '';
        busy = false;
        importBusy = false;
        if (!el('exam-create-form')) return;
        el('exam-create-form').reset();
        fillEditor(null);
        el('exam-include-choices').checked = false;
        el('exam-spelling-answer').value = '';
        el('exam-choice-options').replaceChildren();
        el('exam-wrong-list').replaceChildren();
        el('exam-knowledge-content').replaceChildren();
        el('exam-knowledge-title').textContent = '知识点梳理';
        el('exam-knowledge-intro').textContent = '';
        el('exam-knowledge-pdf').href = '';
        el('exam-knowledge-exam-pdf').href = '';
        ['exam-attempt-name', 'exam-question-prompt', 'exam-result-name', 'exam-result-score', 'exam-result-summary', 'exam-mistake-status'].forEach((id) => {
            el(id).textContent = '';
        });
        showView('setup');
        message();
        updateWordCount();
        renderPapers();
        setBusy(false);
    }

    window.examPreparation = { open, reset };
    document.addEventListener('DOMContentLoaded', () => {
        gradeOptions(el('exam-paper-grade'));
        gradeOptions(el('exam-grade-filter'), true);
        el('exam-grade-filter').value = gradeFilter;
        el('exam-term-filter').value = termFilter;
        const filterPapers = () => {
            if (busy) return;
            gradeFilter = el('exam-grade-filter').value;
            termFilter = el('exam-term-filter').value;
            if (!editingPaper && !el('exam-paper-name').value && !el('exam-words').value && !sentenceRows.length) {
                el('exam-paper-grade').value = gradeFilter === 'all' ? '' : gradeFilter;
                el('exam-paper-term').value = termFilter === 'all' ? '' : termFilter;
            }
            renderPapers();
        };
        el('exam-grade-filter').addEventListener('change', filterPapers);
        el('exam-term-filter').addEventListener('change', filterPapers);
        if (typeof MutationObserver === 'function') {
            new MutationObserver(() => {
                if (!el('exam-preparation-section').classList.contains('active')) el('exam-listening-audio').pause?.();
            }).observe(el('exam-preparation-section'), { attributes: true, attributeFilter: ['class'] });
        }
        el('exam-create-form').addEventListener('submit', savePaper);
        el('exam-knowledge-back').addEventListener('click', () => {
            if (busy) return;
            showView('setup');
            message();
            knowledgeReturnButton?.focus({ preventScroll: true });
        });
        el('exam-edit-paper').addEventListener('click', editPaper);
        el('exam-include-choices').addEventListener('change', () => {
            const selected = papers.find((paper) => paper.id === selectedPaper);
            if (busy || isParentSession || selected?.builtin || !selected?.word_count) return;
            paperChoices.set(selectedPaper, el('exam-include-choices').checked);
        });
        el('exam-cancel-edit').addEventListener('click', () => {
            if (busy) return;
            finishEditing();
            message('已取消编辑。');
        });
        el('exam-add-sentence').addEventListener('click', () => {
            if (busy || sentenceRows.length >= 100) return;
            addSentence().focus();
        });
        el('exam-words').addEventListener('input', () => {
            hideMissingImport();
            updateWordCount();
        });
        el('exam-add-word-button').addEventListener('click', () => addEditorWords());
        el('exam-add-words').addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !event.repeat) {
                event.preventDefault();
                addEditorWords();
            }
        });
        el('exam-import-words').addEventListener('input', updateEditor);
        el('exam-import-submit').addEventListener('click', importMissingWords);
        el('exam-import-dismiss').addEventListener('click', () => {
            if (!busy) hideMissingImport();
        });
        el('exam-start').addEventListener('click', startExam);
        el('exam-review-paper-mistakes').addEventListener('click', () => startMistakeReview(selectedPaper));
        el('exam-review-mistakes').addEventListener('click', () => startMistakeReview(result?.paper_id));
        el('exam-previous').addEventListener('click', () => moveQuestion(-1));
        el('exam-next').addEventListener('click', () => moveQuestion(1));
        el('exam-submit').addEventListener('click', submitExam);
        el('exam-abandon').addEventListener('click', requestAbandon);
        el('exam-abandon-confirm').addEventListener('click', abandonExam);
        el('exam-abandon-cancel').addEventListener('click', () => {
            if (busy) return;
            hideAbandonConfirmation();
            el('exam-abandon').focus({ preventScroll: true });
        });
        el('exam-spelling-answer').addEventListener('input', (event) => {
            if (!attempt || busy) return;
            answers[attempt.questions[index].id] = event.target.value;
            storeDraft();
            updateProgress();
        });
        el('exam-spelling-answer').addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !event.isComposing && !event.repeat) {
                event.preventDefault();
                if (attempt && index < attempt.questions.length - 1) moveQuestion(1);
            }
        });
        el('exam-back-to-papers').addEventListener('click', () => {
            if (busy) return;
            selectedPaper = result.paper_id;
            attempt = null;
            result = null;
            answers = {};
            void open();
        });
    });
})();
