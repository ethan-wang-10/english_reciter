(() => {
    const base = '/exam-preparation';
    const typeNames = { spelling: '单词默写', translation: '句子中译英', recognition: '英文识义', context: '语境选词' };
    let papers = [];
    let selectedPaper = '';
    let editingPaper = '';
    let newPaperDraft = null;
    let sentenceRows = [];
    let sentenceFieldId = 0;
    let attempt = null;
    let result = null;
    let answers = {};
    let index = 0;
    let busy = false;
    let generation = 0;
    let listRequest = 0;
    let draftKey = '';
    const el = (id) => document.getElementById(id);
    const context = () => ({ account: captureAccountContext(), generation });
    const current = (ctx) => ctx.generation === generation && isAccountContextCurrent(ctx.account);
    const isWrittenQuestion = (question) => ['spelling', 'translation'].includes(question.type);
    const paperContents = (paper) => [
        paper.word_count ? `${paper.word_count} 个单词` : '',
        paper.sentence_count ? `${paper.sentence_count} 道句子题` : '',
    ].filter(Boolean).join(' · ');

    function editorValue() {
        return {
            name: el('exam-paper-name').value,
            words: el('exam-words').value,
            sentences: sentenceRows.map((row) => ({ chinese: row.chinese.value, answers: row.answers.value.split(/\r?\n/) })),
        };
    }

    function updateEditor() {
        el('exam-editor-title').textContent = editingPaper ? '编辑考卷' : '新建考卷';
        el('exam-editor-hint').hidden = !editingPaper;
        el('exam-cancel-edit').hidden = !editingPaper;
        el('exam-save-paper').textContent = editingPaper ? '保存修改' : '保存并生成考卷';
        el('exam-edit-paper').textContent = editingPaper ? '正在旁边编辑' : '编辑考卷';
        el('exam-edit-paper').disabled = busy || !!editingPaper || !selectedPaper;
        el('exam-sentence-count').textContent = `${sentenceRows.length} 道`;
        el('exam-add-sentence').disabled = busy || sentenceRows.length >= 100;
        el('exam-start').disabled = busy || !!editingPaper || !selectedPaper || isParentSession;
        const selected = papers.find((paper) => paper.id === selectedPaper);
        el('exam-include-choices').disabled = busy || isParentSession || !selected?.word_count;
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
        el('exam-paper-name').value = value?.name || '';
        el('exam-words').value = value?.words || '';
        renderSentences(value?.sentences || []);
        el('exam-sentences-section').open = sentenceRows.length > 0;
        updateWordCount();
    }

    function editPaper() {
        if (busy || editingPaper) return;
        const paper = papers.find((item) => item.id === selectedPaper);
        if (!paper) return;
        newPaperDraft = editorValue();
        editingPaper = paper.id;
        fillEditor({ name: paper.name, words: paper.words.map((word) => word.english).join(', '), sentences: paper.sentences || [] });
        message(`正在编辑“${paper.name}”，修改后请保存。`);
        el('exam-editor-title').scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
        el('exam-paper-name').focus({ preventScroll: true });
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
        ['setup', 'attempt', 'result'].forEach((name) => {
            el(`exam-${name}-view`).hidden = name !== view;
        });
    }

    function setBusy(value) {
        busy = value;
        updateMistakeControls();
        ['exam-save-paper', 'exam-start', 'exam-previous', 'exam-next', 'exam-submit',
            'exam-words', 'exam-paper-name', 'exam-cancel-edit', 'exam-back-to-papers'].forEach((id) => {
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

    function renderPapers() {
        const list = el('exam-paper-list');
        list.replaceChildren();
        el('exam-paper-empty').hidden = papers.length > 0;
        el('exam-paper-empty').textContent = '还没有考卷，先添加单词或句子题并保存一张吧。';
        el('exam-paper-settings').hidden = papers.length === 0;
        if (!papers.some((paper) => paper.id === selectedPaper)) selectedPaper = papers[0]?.id || '';
        for (const paper of papers) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'exam-paper-card';
            button.disabled = busy;
            button.setAttribute('aria-pressed', String(paper.id === selectedPaper));
            const title = document.createElement('strong');
            title.textContent = paper.name;
            const detail = document.createElement('span');
            detail.textContent = `${paperContents(paper)} · ${paper.created_at.slice(0, 10)}`;
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
            list.appendChild(button);
        }
        const selected = papers.find((paper) => paper.id === selectedPaper);
        el('exam-paper-preview').textContent = selected ? selected.words.map((word) => word.english).join(', ') : '';
        el('exam-paper-preview').hidden = !selected?.word_count;
        const sentences = selected?.sentences || [];
        el('exam-paper-sentence-preview').hidden = sentences.length === 0;
        el('exam-paper-sentence-preview').textContent = sentences.map((sentence, i) => `${i + 1}. ${sentence.chinese}`).join('\n');
        el('exam-parent-hint').hidden = !isParentSession;
        el('exam-include-choices').disabled = busy || isParentSession || !selected?.word_count;
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
        papers = data.papers;
        renderPapers();
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
                const data = await apiRequest(`${base}/attempts/${encodeURIComponent(draft.id)}`);
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
            if (paperId) finishEditing();
            else fillEditor(null);
            papers = [data.paper, ...papers.filter((paper) => paper.id !== data.paper.id)];
            renderPapers();
            message(`“${data.paper.name}”${paperId ? '已更新' : '已保存'}，共 ${paperContents(data.paper)}。${data.duplicates_removed ? `已合并 ${data.duplicates_removed} 个重复单词。` : ''}`);
            await refreshPapers();
        } catch (error) {
            if (current(ctx)) message(error.message || '保存考卷失败，请重试', true);
        } finally {
            if (current(ctx)) setBusy(false);
        }
    }

    async function startExam() {
        if (busy || editingPaper || !selectedPaper || isParentSession) return;
        const ctx = context();
        const includeChoices = !el('exam-include-choices').disabled && el('exam-include-choices').checked;
        setBusy(true);
        message('正在准备考试…');
        try {
            const data = await apiRequest(`${base}/papers/${encodeURIComponent(selectedPaper)}/attempts`, {
                method: 'POST', body: JSON.stringify({ include_choices: includeChoices }),
            });
            if (!current(ctx)) return;
            attempt = data.attempt;
            result = null;
            answers = {};
            index = 0;
            storeDraft();
            renderQuestion();
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
                method: 'POST', body: '{}',
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
        const target = isWrittenQuestion(question)
            ? el('exam-spelling-answer')
            : el('exam-choice-options').querySelector('[aria-pressed="true"]') || el('exam-choice-options').querySelector('button');
        target?.focus({ preventScroll: true });
    }

    function renderQuestion(focus = true) {
        showView('attempt');
        const question = attempt.questions[index];
        el('exam-attempt-name').textContent = attempt.paper_name;
        el('exam-question-position').textContent = `第 ${index + 1} / ${attempt.questions.length} 题`;
        el('exam-question-type').textContent = typeNames[question.type];
        el('exam-question-prompt').textContent = question.prompt;
        el('exam-spelling-wrap').hidden = !isWrittenQuestion(question);
        el('exam-choice-options').hidden = isWrittenQuestion(question);
        const input = el('exam-spelling-answer');
        input.value = isWrittenQuestion(question) ? answers[question.id] || '' : '';
        input.maxLength = question.type === 'translation' ? 500 : 200;
        el('exam-answer-label').textContent = question.type === 'translation' ? '请将中文句子译成英文' : '请默写英文单词或短语';
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
            title.textContent = question.type === 'translation' ? typeNames[question.type] : `${question.english} · ${typeNames[question.type]}`;
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
        const words = el('exam-words').value.toLowerCase().split(/[,，、;；\r\n]+/)
            .map((word) => word.trim().replace(/\s+/g, ' ')).filter(Boolean);
        el('exam-word-count').textContent = `已输入 ${new Set(words).size} 个不同的单词 · 最多 300 个`;
    }

    function reset() {
        generation += 1;
        listRequest += 1;
        clearDraft();
        papers = [];
        selectedPaper = '';
        editingPaper = '';
        newPaperDraft = null;
        attempt = null;
        result = null;
        answers = {};
        index = 0;
        draftKey = '';
        busy = false;
        if (!el('exam-create-form')) return;
        el('exam-create-form').reset();
        fillEditor(null);
        el('exam-include-choices').checked = false;
        el('exam-spelling-answer').value = '';
        el('exam-choice-options').replaceChildren();
        el('exam-wrong-list').replaceChildren();
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
        el('exam-create-form').addEventListener('submit', savePaper);
        el('exam-edit-paper').addEventListener('click', editPaper);
        el('exam-cancel-edit').addEventListener('click', () => {
            if (busy) return;
            finishEditing();
            message('已取消编辑。');
        });
        el('exam-add-sentence').addEventListener('click', () => {
            if (busy || sentenceRows.length >= 100) return;
            addSentence().focus();
        });
        el('exam-words').addEventListener('input', updateWordCount);
        el('exam-start').addEventListener('click', startExam);
        el('exam-review-paper-mistakes').addEventListener('click', () => startMistakeReview(selectedPaper));
        el('exam-review-mistakes').addEventListener('click', () => startMistakeReview(result?.paper_id));
        el('exam-previous').addEventListener('click', () => moveQuestion(-1));
        el('exam-next').addEventListener('click', () => moveQuestion(1));
        el('exam-submit').addEventListener('click', submitExam);
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
