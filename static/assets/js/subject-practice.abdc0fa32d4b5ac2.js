(() => {
    'use strict';
    const portal = window.subjectPortal;
    const el = (id) => document.getElementById(id);
    if (!portal || !el('subject-practice-view')) return;
    const names = { choice: '选择题', fill: '填空题', calculation: '计算题', application: '应用题' };
    let attempt = null;
    let currentAttempt = null;
    let result = null;
    let answers = {};
    let workings = {};
    let index = 0;
    let revision = 0;
    let generation = 0;
    let busy = false;
    let timer = null;
    let saveQueue = Promise.resolve();
    let savedRevision = 0;
    let localKey = '';
    let abandonId = '';
    const clone = (value) => JSON.parse(JSON.stringify(value));
    const context = () => ({ generation, token: portal.getSession().token });
    const current = (ctx) => ctx.generation === generation && ctx.token === portal.getSession().token;
    const path = () => `/subjects/${portal.getSession().subject}`;

    function message(text = '', error = false) {
        el('practice-message').textContent = text;
        el('practice-message').hidden = !text;
        el('practice-message').classList.toggle('feedback-error', error);
    }

    function setBusy(value) {
        busy = value;
        el('subject-practice-view').querySelectorAll('button, input, textarea').forEach((node) => { node.disabled = value; });
        if (!value && attempt) el('practice-previous').disabled = index === 0;
    }

    function clearBackup() {
        try { if (localKey) sessionStorage.removeItem(localKey); } catch (_) { /* Server drafts remain available. */ }
    }

    function backup() {
        if (!attempt || !localKey) return;
        try { sessionStorage.setItem(localKey, JSON.stringify({ id: attempt.id, answers, workings, index, revision })); }
        catch (_) { /* Online autosave works without browser storage. */ }
    }

    function acceptAttempt(data) {
        const account = portal.getSession();
        localKey = `itorange-practice:${account.subject}:${account.username}`;
        attempt = data;
        currentAttempt = data;
        result = null;
        answers = data.answers || {};
        workings = data.workings || {};
        index = Math.max(0, Math.min(data.questions.length - 1, data.index || 0));
        revision = data.revision || 0;
        savedRevision = revision;
        try {
            const pending = JSON.parse(sessionStorage.getItem(localKey) || 'null');
            if (pending?.id === data.id && Number.isInteger(pending.revision) && pending.revision > revision
                    && pending.answers && pending.workings && Number.isInteger(pending.index)) {
                answers = pending.answers;
                workings = pending.workings;
                index = Math.max(0, Math.min(data.questions.length - 1, pending.index));
                revision = pending.revision;
            }
        } catch (_) { /* A malformed local backup cannot replace the server draft. */ }
        backup();
        renderQuestion();
        if (revision > savedRevision) queueSave();
    }

    function showView() {
        portal.showSection('papers');
        for (const name of ['papers', 'knowledge', 'records', 'account']) el(`${name}-panel`).hidden = true;
        el('subject-practice-view').hidden = false;
        el('practice-abandon-confirmation').hidden = true;
        el('practice-submit-confirmation').hidden = true;
        el('practice-attempt').hidden = !!result;
        el('practice-result').hidden = !result;
    }

    function completed(question) {
        const value = answers[question.id];
        return question.type === 'choice' ? !!value : question.fields.every((field) => String(value?.[field.id] || '').trim());
    }

    function progress() {
        if (!attempt) return;
        const done = attempt.questions.filter(completed).length;
        el('practice-progress').max = attempt.questions.length;
        el('practice-progress').value = done;
        el('practice-progress-text').textContent = `已答 ${done} / ${attempt.questions.length} 题`;
        const grid = el('practice-question-grid');
        grid.replaceChildren();
        attempt.questions.forEach((question, position) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.textContent = String(position + 1);
            button.className = completed(question) ? 'answered' : '';
            button.setAttribute('aria-current', position === index ? 'step' : 'false');
            button.setAttribute('aria-label', `第 ${position + 1} 题${question.difficulty === 'challenge' ? '，思维拓展' : ''}${completed(question) ? '，已作答' : '，未完成'}`);
            button.classList.toggle('challenge', question.difficulty === 'challenge');
            button.disabled = busy;
            button.addEventListener('click', () => move(position));
            grid.appendChild(button);
        });
    }

    function changed() {
        revision += 1;
        backup();
        progress();
        el('practice-save-state').textContent = '正在保存答题进度…';
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => { timer = null; void queueSave(); }, 450);
    }

    function queueSave() {
        if (!attempt || result || revision <= savedRevision) return saveQueue;
        if (timer) { clearTimeout(timer); timer = null; }
        const ctx = context();
        const id = attempt.id;
        const body = { answers: clone(answers), workings: clone(workings), index, revision };
        const endpoint = `${path()}/attempts/${id}`;
        saveQueue = saveQueue.catch(() => {}).then(async () => {
            if (!current(ctx) || attempt?.id !== id || result || body.revision <= savedRevision) return;
            try {
                const saved = await portal.apiRequest(endpoint, { method: 'PUT', body });
                if (!current(ctx) || attempt?.id !== id || result) return;
                savedRevision = Math.max(savedRevision, saved.revision);
                el('practice-save-state').textContent = savedRevision >= revision ? '答题进度已保存，可稍后继续。' : '正在保存答题进度…';
                el('practice-retry-save').hidden = true;
            } catch (error) {
                if (current(ctx) && attempt?.id === id && !result) {
                    el('practice-save-state').textContent = error.message || '保存失败，答案仍保留在当前页面。';
                    el('practice-retry-save').hidden = false;
                }
            }
        });
        return saveQueue;
    }

    function renderQuestion() {
        showView();
        const question = attempt.questions[index];
        el('practice-title').textContent = attempt.paper_title;
        el('practice-kind').textContent = '在线练习';
        el('practice-scope').textContent = `范围：${attempt.scope} · 建议 ${attempt.time_minutes} 分钟`;
        el('practice-question-position').textContent = `第 ${index + 1} / ${attempt.questions.length} 题 · ${question.difficulty === 'challenge' ? '思维拓展' : names[question.type]}`;
        el('practice-question-points').textContent = `${question.points} 分`;
        el('practice-question-prompt').textContent = question.prompt;
        el('practice-hints').hidden = !question.hints?.length;
        el('practice-hints').open = false;
        el('practice-hint-items').replaceChildren();
        for (const text of question.hints || []) {
            const line = document.createElement('li');
            line.textContent = text;
            el('practice-hint-items').appendChild(line);
        }
        el('practice-grading-note').textContent = attempt.grading_note;
        const diagram = el('practice-diagram');
        diagram.hidden = !question.diagram_url;
        if (question.diagram_url) {
            diagram.src = question.diagram_url;
            diagram.alt = question.diagram_alt;
        } else diagram.removeAttribute('src');
        const options = el('practice-choice-options');
        const fields = el('practice-answer-fields');
        options.replaceChildren();
        fields.replaceChildren();
        if (question.type === 'choice') {
            question.options.forEach((option, position) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'practice-choice';
                button.textContent = `${String.fromCharCode(65 + position)}. ${option.text}`;
                button.setAttribute('aria-pressed', String(answers[question.id] === option.id));
                button.disabled = busy;
                button.addEventListener('click', () => {
                    if (busy || !attempt) return;
                    answers[question.id] = option.id;
                    options.querySelectorAll('button').forEach((node) => node.setAttribute('aria-pressed', String(node === button)));
                    changed();
                });
                options.appendChild(button);
            });
        } else {
            for (const field of question.fields) {
                const wrap = document.createElement('div');
                wrap.className = 'practice-field';
                const label = document.createElement('label');
                const id = `practice-input-${question.id}-${field.id}`;
                label.setAttribute('for', id);
                label.textContent = field.label + (field.unit ? `（${field.unit}）` : '') + ` · ${field.points} 分`;
                const input = document.createElement('input');
                input.id = id;
                input.type = 'text';
                input.maxLength = 120;
                input.autocomplete = 'off';
                input.inputMode = ['text', 'fraction'].includes(field.format) ? 'text' : 'decimal';
                input.value = answers[question.id]?.[field.id] || '';
                input.disabled = busy;
                input.addEventListener('input', () => {
                    if (busy || !attempt) return;
                    answers[question.id] = { ...(answers[question.id] || {}), [field.id]: input.value };
                    changed();
                });
                input.addEventListener('blur', () => void queueSave());
                wrap.appendChild(label);
                wrap.appendChild(input);
                if (field.hint) {
                    const hint = document.createElement('p');
                    hint.className = 'practice-hint';
                    hint.textContent = field.hint;
                    wrap.appendChild(hint);
                }
                fields.appendChild(wrap);
            }
        }
        el('practice-working-wrap').hidden = !['calculation', 'application'].includes(question.type);
        el('practice-working').value = workings[question.id] || '';
        el('practice-previous').disabled = busy || index === 0;
        el('practice-next').hidden = index === attempt.questions.length - 1;
        el('practice-save-state').textContent = revision > savedRevision ? '正在保存答题进度…' : '答题进度已保存，可稍后继续。';
        progress();
        el('practice-question-prompt').scrollIntoView?.({ block: 'nearest' });
    }

    function move(position) {
        if (busy || !attempt) return;
        index = Math.max(0, Math.min(position, attempt.questions.length - 1));
        changed();
        renderQuestion();
        void queueSave();
    }

    async function refreshActive() {
        const session = portal.getSession();
        el('practice-resume-banner').hidden = true;
        if (!session.token || !session.account || session.account.is_parent) return;
        const ctx = context();
        try {
            const data = await portal.apiRequest(`${path()}/attempts/current`);
            if (!current(ctx)) return;
            currentAttempt = data.attempt;
            el('practice-resume-banner').hidden = !currentAttempt;
            if (currentAttempt) el('practice-resume-label').textContent = `未完成：${currentAttempt.paper_title}`;
        } catch (error) {
            if (current(ctx) && error.status !== 404) {
                el('portal-error').textContent = error.message || '未完成练习加载失败，请刷新重试。';
                el('portal-error').hidden = false;
            }
        }
    }

    async function start(paperId) {
        if (busy || !portal.getSession().account || portal.getSession().account.is_parent) return;
        generation += 1;
        const ctx = context();
        setBusy(true);
        try {
            const data = await portal.apiRequest(`${path()}/papers/${paperId}/attempts`, { method: 'POST', body: {} });
            if (!current(ctx)) return;
            acceptAttempt(data.attempt);
            message();
        } catch (error) {
            if (current(ctx)) {
                el('portal-error').textContent = error.message || '开始练习失败，请重试。';
                el('portal-error').hidden = false;
                void refreshActive();
            }
        } finally { if (current(ctx)) setBusy(false); }
    }

    async function resume() {
        if (busy || !currentAttempt) return;
        generation += 1;
        const ctx = context();
        setBusy(true);
        try {
            const data = await portal.apiRequest(`${path()}/attempts/${currentAttempt.id}`);
            if (current(ctx)) { acceptAttempt(data.attempt); message(); }
        } catch (error) { if (current(ctx)) message(error.message, true); }
        finally { if (current(ctx)) setBusy(false); }
    }

    function confirmAbandon(id) {
        if (busy || !id) return;
        abandonId = id;
        if (!attempt || attempt.id !== id) {
            acceptAttempt(currentAttempt);
        }
        el('practice-abandon-confirmation').hidden = false;
        el('practice-abandon-cancel').focus({ preventScroll: true });
    }

    async function abandon() {
        if (busy || !abandonId) return;
        const ctx = context();
        const id = abandonId;
        setBusy(true);
        try {
            await portal.apiRequest(`${path()}/attempts/${id}/abandon`, { method: 'POST', body: {} });
            if (!current(ctx)) return;
            generation += 1;
            clearBackup();
            if (timer) clearTimeout(timer);
            attempt = currentAttempt = null;
            result = null;
            abandonId = '';
            answers = {};
            workings = {};
            setBusy(false);
            await portal.refreshSection('papers');
            el('practice-resume-banner').hidden = true;
            el('portal-status').textContent = '已放弃本次练习，未生成成绩。';
            el('portal-status').hidden = false;
        } catch (error) { if (current(ctx)) message(error.message, true); }
        finally { if (current(ctx)) setBusy(false); }
    }

    function requestSubmit() {
        if (busy || !attempt) return;
        const remaining = attempt.questions.filter((question) => !completed(question)).length;
        el('practice-submit-warning').textContent = remaining
            ? `还有 ${remaining} 题未完成。未填写的答题栏计 0 分，确认交卷吗？`
            : '交卷后本次答案将固定，并显示成绩和解析。确认交卷吗？';
        el('practice-submit-confirmation').hidden = false;
        el('practice-submit-cancel').focus({ preventScroll: true });
    }

    async function submit() {
        if (busy || !attempt) return;
        const ctx = context();
        const id = attempt.id;
        if (timer) { clearTimeout(timer); timer = null; }
        setBusy(true);
        message('正在判分…');
        try {
            await queueSave();
            if (!current(ctx)) return;
            const data = await portal.apiRequest(`${path()}/attempts/${id}/submit`, { method: 'POST', body: { answers: clone(answers), workings: clone(workings) } });
            if (!current(ctx)) return;
            clearBackup();
            attempt = currentAttempt = null;
            result = data.result;
            el('practice-resume-banner').hidden = true;
            renderResult();
            message();
        } catch (error) { if (current(ctx)) message(error.message || '交卷失败，答案已保留，请重试。', true); }
        finally { if (current(ctx)) setBusy(false); }
    }

    function renderResult() {
        showView();
        el('practice-title').textContent = result.paper_title;
        el('practice-kind').textContent = '练习完成';
        el('practice-scope').textContent = result.submitted_at.slice(0, 10);
        el('practice-result-score').textContent = `${result.score} / 100 分`;
        el('practice-result-summary').textContent = `共 ${result.total} 题，全对 ${result.correct_count} 题，部分答对 ${result.partial_count} 题。`;
        if (result.challenge_max_score) el('practice-result-summary').textContent += ` 基础 ${result.base_score} / ${result.base_max_score} 分，拓展 ${result.challenge_score} / ${result.challenge_max_score} 分。`;
        el('practice-result-note').textContent = result.grading_note;
        const list = el('practice-result-questions');
        list.replaceChildren();
        result.questions.forEach((question, position) => {
            const card = document.createElement('details');
            card.className = question.correct ? 'practice-review correct' : 'practice-review incorrect';
            card.open = !question.correct;
            const title = document.createElement('summary');
            title.textContent = `第 ${position + 1} 题 · ${question.earned_points} / ${question.points} 分${question.correct ? ' · 答对' : ''}`;
            card.appendChild(title);
            const line = (text, css = '') => {
                const node = document.createElement('p');
                node.textContent = text;
                node.className = css;
                card.appendChild(node);
            };
            line(question.prompt, 'practice-review-prompt');
            if (question.type === 'choice') {
                line(`你的答案：${question.user_answer}`);
                line(`参考答案：${question.correct_answer}`);
            } else for (const field of question.fields) {
                line(`${field.label}${field.unit ? `（${field.unit}）` : ''}：你的答案 ${field.user_answer || '未作答'}；参考答案 ${field.correct_answer}（${field.earned_points} / ${field.points} 分）`);
            }
            if (question.working) line(`你的解题过程：${question.working}`);
            line(`解析：${question.explanation}`, 'practice-explanation');
            list.appendChild(card);
        });
        el('practice-title').focus({ preventScroll: true });
    }

    async function showResult(id) {
        if (busy) return;
        generation += 1;
        const ctx = context();
        setBusy(true);
        try {
            const data = await portal.apiRequest(`${path()}/records/${id}`);
            if (!current(ctx)) return;
            result = data.result;
            renderResult();
            message();
        } catch (error) {
            if (current(ctx)) {
                el('portal-error').textContent = error.message || '答卷加载失败，请重试。';
                el('portal-error').hidden = false;
            }
        } finally { if (current(ctx)) setBusy(false); }
    }

    function leaveView() { void queueSave(); el('subject-practice-view').hidden = true; }
    function reset() {
        generation += 1;
        if (timer) clearTimeout(timer);
        timer = null;
        clearBackup();
        attempt = currentAttempt = result = null;
        answers = {};
        workings = {};
        abandonId = localKey = '';
        revision = savedRevision = index = 0;
        busy = false;
        saveQueue = Promise.resolve();
        leaveView();
        el('practice-resume-banner').hidden = true;
        el('practice-answer-fields').replaceChildren();
        el('practice-choice-options').replaceChildren();
        el('practice-result-questions').replaceChildren();
        for (const id of ['practice-title', 'practice-scope', 'practice-question-prompt', 'practice-result-score', 'practice-result-summary', 'practice-save-state']) el(id).textContent = '';
        el('practice-working').value = '';
        el('practice-diagram').removeAttribute('src');
    }

    el('practice-back').addEventListener('click', () => { if (!busy) { void queueSave(); void portal.refreshSection(result ? 'records' : 'papers'); void refreshActive(); } });
    el('practice-working').addEventListener('input', () => { if (!busy && attempt) { workings[attempt.questions[index].id] = el('practice-working').value; changed(); } });
    el('practice-previous').addEventListener('click', () => move(index - 1));
    el('practice-next').addEventListener('click', () => move(index + 1));
    el('practice-submit').addEventListener('click', requestSubmit);
    el('practice-submit-confirm').addEventListener('click', submit);
    el('practice-submit-cancel').addEventListener('click', () => { if (!busy) el('practice-submit-confirmation').hidden = true; });
    el('practice-abandon').addEventListener('click', () => confirmAbandon(attempt?.id));
    el('practice-resume-abandon').addEventListener('click', () => confirmAbandon(currentAttempt?.id));
    el('practice-abandon-confirm').addEventListener('click', abandon);
    el('practice-abandon-cancel').addEventListener('click', () => { if (!busy && attempt) renderQuestion(); });
    el('practice-resume').addEventListener('click', resume);
    el('practice-retry-save').addEventListener('click', () => void queueSave());
    window.subjectPractice = { start, showResult, refreshActive, leaveView, reset, flush: queueSave };
    if (portal.getSession().account) void refreshActive();
})();
