(() => {
    const base = '/exam-preparation';
    const typeNames = { spelling: '单词默写', recognition: '英文识义', context: '语境选词' };
    let papers = [];
    let selectedPaper = '';
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
        ['exam-save-paper', 'exam-start', 'exam-previous', 'exam-next', 'exam-submit'].forEach((id) => {
            const button = el(id);
            if (button) button.disabled = value;
        });
        const input = el('exam-spelling-answer');
        if (input) input.disabled = value;
        el('exam-choice-options')?.querySelectorAll('button').forEach((button) => { button.disabled = value; });
        if (!value) {
            el('exam-start').disabled = !selectedPaper || isParentSession;
            el('exam-previous').disabled = !attempt || index === 0;
        }
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
        el('exam-paper-empty').textContent = '还没有考卷，先输入考试单词并保存一张吧。';
        el('exam-paper-settings').hidden = papers.length === 0;
        if (!papers.some((paper) => paper.id === selectedPaper)) selectedPaper = papers[0]?.id || '';
        for (const paper of papers) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'exam-paper-card';
            button.setAttribute('aria-pressed', String(paper.id === selectedPaper));
            const title = document.createElement('strong');
            title.textContent = paper.name;
            const detail = document.createElement('span');
            detail.textContent = `${paper.word_count} 个单词 · ${paper.created_at.slice(0, 10)}`;
            const score = document.createElement('span');
            score.textContent = paper.attempt_count
                ? `最近 ${paper.last_score} 分 · 最高 ${paper.best_score} 分 · 已考 ${paper.attempt_count} 次`
                : '还未考试';
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
        el('exam-parent-hint').hidden = !isParentSession;
        el('exam-include-choices').disabled = isParentSession;
        el('exam-start').disabled = busy || !selectedPaper || isParentSession;
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
                    result = data.result;
                    clearDraft();
                    renderResult();
                } else {
                    attempt = data.attempt;
                    answers = draft.answers && typeof draft.answers === 'object' ? draft.answers : {};
                    index = Math.max(0, Math.min(attempt.questions.length - 1, Number(draft.index) || 0));
                    renderQuestion(false);
                    message('已恢复上次未交卷的考试。');
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
        const wordInput = el('exam-words');
        const nameInput = el('exam-paper-name');
        const words = wordInput.value;
        const name = nameInput.value;
        setBusy(true);
        message('正在生成考卷…');
        try {
            const data = await apiRequest(`${base}/papers`, {
                method: 'POST', body: JSON.stringify({ name, words }),
            });
            if (!current(ctx)) return;
            selectedPaper = data.paper.id;
            if (wordInput.value === words && nameInput.value === name) {
                el('exam-create-form').reset();
                updateWordCount();
            }
            message(`“${data.paper.name}”已保存，共 ${data.paper.word_count} 个单词。${data.duplicates_removed ? `已合并 ${data.duplicates_removed} 个重复单词。` : ''}`);
            await refreshPapers();
        } catch (error) {
            if (current(ctx)) message(error.message || '保存考卷失败，请重试', true);
        } finally {
            if (current(ctx)) setBusy(false);
        }
    }

    async function startExam() {
        if (busy || !selectedPaper || isParentSession) return;
        const ctx = context();
        setBusy(true);
        message('正在准备考试…');
        try {
            const data = await apiRequest(`${base}/papers/${encodeURIComponent(selectedPaper)}/attempts`, {
                method: 'POST', body: JSON.stringify({ include_choices: el('exam-include-choices').checked }),
            });
            if (!current(ctx)) return;
            attempt = data.attempt;
            result = null;
            answers = {};
            index = 0;
            storeDraft();
            renderQuestion();
            const missing = attempt.word_count * 2 - attempt.choice_count;
            message(attempt.include_choices
                ? `本次考试包含 ${attempt.word_count} 道默写题和 ${attempt.choice_count} 道选择题。${missing ? `有 ${missing} 道选择题暂不可用，默写题已全部保留。` : ''}`
                : '根据中文释义默写英文单词或短语，完成后交卷查看成绩。');
        } catch (error) {
            if (current(ctx)) message(error.message || '开始考试失败，请重试', true);
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
        el('exam-submit').textContent = count === attempt.questions.length
            ? '交卷并查看成绩' : `交卷（${attempt.questions.length - count} 题未答）`;
    }

    function focusAnswer() {
        if (!attempt || busy || !el('exam-preparation-section').classList.contains('active')) return;
        const question = attempt.questions[index];
        const target = question.type === 'spelling'
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
        el('exam-spelling-wrap').hidden = question.type !== 'spelling';
        el('exam-choice-options').hidden = question.type === 'spelling';
        const input = el('exam-spelling-answer');
        input.value = question.type === 'spelling' ? answers[question.id] || '' : '';
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
            result = data.result;
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
        el('exam-result-name').textContent = result.paper_name;
        el('exam-result-score').textContent = `${result.score} 分`;
        el('exam-result-summary').textContent = `共 ${result.total} 题，答对 ${result.correct_count} 题，答错 ${result.wrong_count} 题。`;
        el('exam-result-perfect').hidden = result.wrong_count > 0;
        el('exam-wrong-title').textContent = `错题回顾（${result.wrong_count} 题）`;
        const list = el('exam-wrong-list');
        list.replaceChildren();
        for (const question of result.questions.filter((row) => !row.correct)) {
            const card = document.createElement('article');
            card.className = 'exam-wrong-card';
            const title = document.createElement('h4');
            title.textContent = `${question.english} · ${typeNames[question.type]}`;
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
        attempt = null;
        result = null;
        answers = {};
        index = 0;
        draftKey = '';
        busy = false;
        if (!el('exam-create-form')) return;
        el('exam-create-form').reset();
        el('exam-include-choices').checked = false;
        el('exam-spelling-answer').value = '';
        el('exam-choice-options').replaceChildren();
        el('exam-wrong-list').replaceChildren();
        ['exam-attempt-name', 'exam-question-prompt', 'exam-result-name', 'exam-result-score', 'exam-result-summary'].forEach((id) => {
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
        el('exam-words').addEventListener('input', updateWordCount);
        el('exam-start').addEventListener('click', startExam);
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
