/* Guided composition: the student writes; prompts help them make decisions. */
(() => {
    'use strict';
    const el = (id) => document.getElementById(id);
    if (!el('writing-panel')) return;
    const PREFIX = '/subjects/chinese/writing';
    const STAGES = ['审题选材', '搭提纲', '写重点段', '连成初稿', '分轮修改', '回看成果'];
    const PURPOSES = ['交代人物、时间和游戏', '让读者看见有趣的一刻', '交代游戏结束，留下感受'];
    const copy = (value) => JSON.parse(JSON.stringify(value));
    const portal = () => window.subjectPortal;
    const session = () => portal()?.getSession() || {};
    const student = (value = session()) => value.account?.is_parent ? value.account.child_username : value.account?.username || value.username;
    const request = (path, options) => portal().apiRequest(PREFIX + path, options);
    let generation = 0;
    let viewRequest = 0;
    let attempt = null;
    let data = null;
    let versions = [];
    let lesson = null;
    let readOnly = false;
    let viewStage = 0;
    let furthestStage = 0;
    let sequence = 0;
    let savedSequence = 0;
    let timer = null;
    let queue = Promise.resolve();
    let conflict = false;
    let navigationBusy = false;
    let completing = false;
    let selectedVersion = null;
    let localRecovery = null;
    const capture = () => ({ generation, token: session().token, student: student(), attemptId: attempt?.id });
    const current = (ctx, checkAttempt = true) => ctx.generation === generation && ctx.token === session().token
        && ctx.student === student() && (!checkAttempt || ctx.attemptId === attempt?.id);
    const writeAllowed = () => !!attempt && !readOnly && attempt.status !== 'completed';
    const editable = () => writeAllowed() && !localRecovery;
    const backupKey = (ctx = capture()) => `itorange-writing-draft:${ctx.student}:${ctx.attemptId}`;
    const orderedText = (state, paragraphs) => state.outline.map((card) => paragraphs?.[card.id] || '').filter((text) => text.trim()).join('\n\n');
    function validState(value) {
        const object = (item) => !!item && typeof item === 'object' && !Array.isArray(item);
        const strings = (item, keys) => object(item) && keys.every((key) => typeof item[key] === 'string');
        const maps = ['paragraphs', 'structure_paragraphs', 'final_paragraphs'];
        return object(value) && Number.isInteger(value.stage) && value.stage >= 0 && value.stage <= 5
            && ['material', 'personal'].includes(value.mode)
            && strings(value.planning, ['situation', 'main_idea', 'materials', 'ending'])
            && Array.isArray(value.outline) && value.outline.length <= 8
            && value.outline.every((card) => object(card) && /^p[0-9a-z_-]{0,31}$/i.test(card.id)
                && strings(card, ['content', 'purpose']) && typeof card.focus === 'boolean')
            && maps.every((key) => object(value[key]) && Object.keys(value[key]).length <= 32
                && Object.entries(value[key]).every(([id, text]) => /^p[0-9a-z_-]{0,31}$/i.test(id) && typeof text === 'string'))
            && object(value.checks) && ['structure', 'language'].every((key) => Array.isArray(value.checks[key]) && value.checks[key].every((text) => typeof text === 'string'))
            && strings(value.revision_notes, ['structure', 'language']) && strings(value.reflection, ['learned', 'next_time'])
            && object(value.no_change) && ['structure', 'language'].every((key) => typeof value.no_change[key] === 'boolean')
            && ['structure', 'language'].includes(value.review_round)
            && typeof value.structure_started === 'boolean' && typeof value.language_started === 'boolean';
    }
    function sameState(left, right) {
        const ordered = (value) => Array.isArray(value) ? value.map(ordered) : value && typeof value === 'object'
            ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, ordered(value[key])])) : value;
        return JSON.stringify(ordered(left)) === JSON.stringify(ordered(right));
    }

    function node(tag, text = '', className = '') {
        const result = document.createElement(tag);
        if (text) result.textContent = text;
        if (className) result.className = className;
        return result;
    }
    function message(text = '', error = false) {
        const target = el('writing-message');
        target.textContent = text;
        target.hidden = !text;
        target.classList.toggle('feedback-error', error);
        target.classList.toggle('feedback-status', !error);
    }
    function saveState(text) { el('writing-save-state').textContent = text; }
    function button(text, handler, options = {}) {
        const result = node('button', text, 'writing-button' + (options.light ? ' writing-button-light' : '') + (options.small ? ' writing-button-small' : ''));
        result.type = 'button';
        result.disabled = !!options.disabled;
        if (options.label) result.setAttribute('aria-label', options.label);
        result.addEventListener('click', handler);
        return result;
    }
    function note(text, parent, className = 'writing-note') { parent.appendChild(node('p', text, className)); }

    function writeBackup(ctx = capture()) {
        if (!editable() || !ctx.student || !ctx.attemptId || !current(ctx)) return;
        try {
            localStorage.setItem(backupKey(ctx), JSON.stringify({ student: ctx.student, attempt_id: attempt.id,
                base_revision: attempt.revision, data: copy(data), saved_at: Date.now() }));
        } catch (_) { saveState('正在保存到云端'); }
    }
    function removeBackup(ctx = capture()) {
        try { localStorage.removeItem(backupKey(ctx)); } catch (_) { /* Storage is optional. */ }
    }
    function readBackup() {
        if (!writeAllowed()) return null;
        try {
            const value = JSON.parse(localStorage.getItem(backupKey()) || 'null');
            if (!value || value.student !== student() || value.attempt_id !== attempt.id || !Number.isInteger(value.base_revision)
                    || value.base_revision < 1 || !validState(value.data)) return null;
            if (sameState(value.data, data)) { removeBackup(); return null; }
            return value;
        } catch (_) { return null; }
    }
    function changed() {
        if (!editable()) return;
        sequence += 1;
        writeBackup();
        saveState(conflict ? '本机内容已保留，等待对照' : '有修改，准备保存…');
        clearTimeout(timer);
        if (!conflict) timer = setTimeout(() => { void flush().catch(() => {}); }, 1000);
    }
    function apiOptions(body) { return { method: 'POST', body }; }

    function flush(label = '', complete = false) {
        clearTimeout(timer);
        timer = null;
        const ctx = capture();
        if (!editable()) return Promise.resolve(true);
        queue = queue.catch(() => {}).then(async () => {
            if (!current(ctx) || !editable()) return false;
            if (conflict) throw new Error('请先保留本机内容，再载入最新稿进行对照。');
            if (!label && !complete && sequence === savedSequence) return true;
            const sentSequence = sequence;
            const sent = copy(data);
            const body = { base_revision: attempt.revision, data: sent };
            const path = complete ? `/attempts/${attempt.id}/complete` : label ? `/attempts/${attempt.id}/checkpoints` : `/attempts/${attempt.id}`;
            if (label) body.label = label;
            saveState('正在保存…');
            try {
                const result = await request(path, { method: label || complete ? 'POST' : 'PATCH', body });
                if (!current(ctx)) return false;
                if (!result.attempt || !Number.isInteger(result.attempt.revision)) throw new Error('保存结果未确认，请重试。');
                // Keep edits made while the request was in flight. Only the server revision advances.
                attempt = { ...result.attempt, data };
                versions = Array.isArray(result.versions) ? result.versions : versions;
                savedSequence = sentSequence;
                if (sequence === sentSequence) {
                    removeBackup(ctx);
                    saveState(complete ? '作品已保存' : '已保存');
                } else {
                    writeBackup(ctx);
                    saveState('还有修改，准备保存…');
                }
                return true;
            } catch (error) {
                if (!current(ctx)) return false;
                writeBackup(ctx);
                if (error.status === 409) {
                    conflict = true;
                    el('writing-conflict').hidden = false;
                    saveState('本机内容已保留');
                    message('另一页面已有更新。你输入的内容已留在本机，请先复制并对照最新稿。', true);
                } else {
                    saveState('本机待同步');
                    message(error.message || '暂时没能保存到云端，本机内容已保留。请重试保存。', true);
                }
                throw error;
            }
        });
        return queue;
    }

    function field(parent, label, value, update, options = {}) {
        const wrapper = node('label', '', 'writing-field');
        if (options.id) wrapper.setAttribute('for', options.id);
        wrapper.appendChild(node('span', label));
        const input = node(options.single ? 'input' : 'textarea');
        if (options.id) input.id = options.id;
        if (options.single) input.type = 'text';
        else input.rows = options.rows || 3;
        input.value = value || '';
        input.maxLength = options.maxLength || 2000;
        input.disabled = !editable() || completing;
        input.autocomplete = 'off';
        if (options.placeholder) input.placeholder = options.placeholder;
        if (options.list) input.setAttribute('list', options.list);
        input.addEventListener('input', () => { if (!editable() || completing) return; update(input.value); changed(); });
        input.addEventListener('blur', () => { if (editable() && !conflict) void flush().catch(() => {}); });
        wrapper.appendChild(input);
        if (options.help) wrapper.appendChild(node('small', options.help));
        parent.appendChild(wrapper);
        return input;
    }
    function check(parent, label, checked, update, id) {
        const wrapper = node('label', '', 'writing-check');
        const input = node('input');
        input.type = 'checkbox';
        input.checked = !!checked;
        input.disabled = !editable() || completing;
        if (id) input.id = id;
        input.addEventListener('change', () => { if (!editable() || completing) return; update(input.checked); changed(); });
        wrapper.append(input, node('span', label));
        parent.appendChild(wrapper);
        return input;
    }

    function stageGuide() { return lesson?.stages?.[viewStage] || {}; }
    function renderHints() {
        const container = el('writing-hints');
        container.replaceChildren();
        const guide = stageGuide();
        const hints = Array.isArray(guide.hints) ? guide.hints : [];
        for (const [index, hint] of hints.entries()) {
            const details = node('details', '', 'writing-hint');
            details.appendChild(node('summary', hint.title || `提示 ${index + 1}`));
            details.appendChild(node('p', hint.text || '先想一想，再用自己的话写下来。'));
            container.appendChild(details);
        }
        if (Array.isArray(lesson?.reference_outline)) {
            const reference = node('details', '', 'writing-hint');
            reference.appendChild(node('summary', '需要时再看：一种提纲安排'));
            note('这只是给定材料的一种安排。自己的经历可以采用其他合理顺序。', reference);
            const list = node('ol');
            for (const item of lesson.reference_outline) {
                list.appendChild(node('li', `${item.focus ? '★ ' : ''}${item.content}；作用：${item.purpose}`));
            }
            reference.appendChild(list);
            container.appendChild(reference);
        }
    }
    function renderSteps() {
        const steps = el('writing-steps');
        steps.replaceChildren();
        STAGES.forEach((title, index) => {
            const step = node('button', '', 'writing-step');
            step.type = 'button';
            step.append(node('span', String(index + 1)), node('span', title));
            if (index === viewStage) step.setAttribute('aria-current', 'step');
            step.disabled = navigationBusy || (writeAllowed() && index > furthestStage);
            step.addEventListener('click', () => {
                if (writeAllowed() && index > furthestStage) return;
                void moveStage(index, false);
            });
            steps.appendChild(step);
        });
    }
    function planning(parent) {
        const modes = node('div', '', 'writing-mode-picker');
        for (const [value, label] of [['material', '用给定材料练习'], ['personal', '写自己的真实经历']]) {
            const wrapper = node('label');
            const radio = node('input');
            radio.type = 'radio'; radio.name = 'writing-mode'; radio.value = value;
            radio.checked = data.mode === value; radio.disabled = !editable();
            radio.addEventListener('change', () => { if (editable()) { data.mode = value; changed(); renderStage(); } });
            wrapper.append(radio, node('span', label)); modes.appendChild(wrapper);
        }
        parent.appendChild(modes);
        if (data.mode === 'material') {
            const material = node('div', '', 'writing-material');
            material.appendChild(node('strong', '练习情境'));
            const list = node('ul');
            for (const text of lesson.materials || []) list.appendChild(node('li', typeof text === 'string' ? text : text.text || ''));
            material.appendChild(list);
            note('这是材料练习，不要求把它当作自己真实发生的经历。', material);
            parent.appendChild(material);
        } else note('选一件自己记得清楚的小事。没有记住的细节可以先空着，不必编造。', parent);
        const fields = [
            ['situation', '谁和谁，什么时候，玩了什么？', '关键词或一句短句即可。'],
            ['main_idea', '你最想让读者觉得这次游戏怎样？为什么？', '别急着写长句，先说清“有趣在哪里”。'],
            ['materials', '准备写哪些材料？哪些可以先不写？（选填）', '把要用的动作、说的话或反应记下来。'],
            ['ending', '游戏怎样结束？你想留下什么感受？（选填）', '让结尾接得上前面的事情。'],
        ];
        for (const [key, title, help] of fields) field(parent, title, data.planning[key], (value) => { data.planning[key] = value; }, { id: 'writing-plan-' + key, help });
    }
    function newCard(id) { return { id: id || 'p_' + Math.random().toString(36).slice(2, 12), content: '', purpose: '', focus: false }; }
    function allParagraphIds() {
        return [...new Set(['paragraphs', 'structure_paragraphs', 'final_paragraphs'].flatMap((key) => Object.keys(data[key] || {})))];
    }
    function renderOrphans(parent) {
        const visibleIds = new Set(data.outline.map((card) => card.id));
        const orphanIds = allParagraphIds().filter((id) => !visibleIds.has(id)
            && ['paragraphs', 'structure_paragraphs', 'final_paragraphs'].some((key) => data[key]?.[id]?.trim()));
        if (!orphanIds.length) return;
        const details = node('details', '', 'writing-orphans');
        details.appendChild(node('summary', `未放入当前文章的文字（${orphanIds.length} 段）`));
        note('删掉提纲卡片时，这些文字仍会保留；恢复卡片后可继续使用。它们暂不计入当前全文。', details);
        for (const id of orphanIds) {
            const article = node('article');
            const text = data.final_paragraphs[id] || data.structure_paragraphs[id] || data.paragraphs[id];
            article.appendChild(node('pre', text));
            if (editable()) article.appendChild(button('恢复到提纲', () => {
                if (data.outline.length >= 8) return message('当前已有 8 张卡片，可以先合并或调整现有卡片。', true);
                data.outline.push(newCard(id)); changed(); renderStage();
            }, { light: true, small: true, label: `恢复段落 ${id} 到提纲` }));
            details.appendChild(article);
        }
        parent.appendChild(details);
    }
    function outline(parent) {
        note('每段先写关键词，再想一想这一段的作用。把最值得展开的段落标上 ★。段数可以自己调整。', parent);
        const suggestions = node('datalist'); suggestions.id = 'writing-purpose-options';
        for (const text of PURPOSES) { const option = node('option'); option.value = text; suggestions.appendChild(option); }
        parent.appendChild(suggestions);
        const list = node('div', '', 'writing-outline-list');
        data.outline.forEach((card, index) => {
            const article = node('article', '', 'writing-outline-card' + (card.focus ? ' is-focus' : ''));
            const toolbar = node('div', '', 'writing-card-toolbar');
            toolbar.appendChild(node('strong', `第 ${index + 1} 段${card.focus ? ' ★' : ''}`));
            const controls = node('div', '', 'writing-card-buttons');
            for (const [offset, title] of [[-1, '上移'], [1, '下移']]) controls.appendChild(button(title, () => {
                const next = index + offset;
                if (!editable() || next < 0 || next >= data.outline.length) return;
                [data.outline[index], data.outline[next]] = [data.outline[next], data.outline[index]];
                changed(); renderStage();
            }, { light: true, small: true, disabled: !editable() || index + offset < 0 || index + offset >= data.outline.length, label: `${title}第 ${index + 1} 段` }));
            controls.appendChild(button('删除', () => {
                if (!editable()) return;
                data.outline.splice(index, 1); changed(); renderStage();
            }, { light: true, small: true, disabled: !editable(), label: `删除第 ${index + 1} 段卡片，已写文字会保留` }));
            toolbar.appendChild(controls); article.appendChild(toolbar);
            field(article, '这一段准备写什么？', card.content, (value) => { card.content = value; }, { id: 'writing-outline-content-' + card.id, rows: 2 });
            field(article, '想让读者知道什么？', card.purpose, (value) => { card.purpose = value; }, { id: 'writing-outline-purpose-' + card.id, single: true, list: 'writing-purpose-options', placeholder: '可以选提示，也可以自己写' });
            const focus = check(article, '★ 这一段重点展开', card.focus, (value) => {
                card.focus = value; article.classList.toggle('is-focus', value);
            }, 'writing-outline-focus-' + card.id);
            focus.parentElement?.classList.add?.('writing-focus-label');
            list.appendChild(article);
        });
        parent.appendChild(list);
        const add = button('＋ 增加一段', () => { if (!editable() || data.outline.length >= 8) return; data.outline.push(newCard()); changed(); renderStage(); }, { light: true, disabled: !editable() || data.outline.length >= 8 });
        add.classList.add('writing-add-card'); parent.appendChild(add);
        renderOrphans(parent);
    }
    function articlePreview(parent, map, title = '通读我现在的全文') {
        const details = node('details', '', 'writing-preview');
        details.appendChild(node('summary', title));
        details.appendChild(node('div', orderedText(data, map) || '写下自己的段落后，全文会在这里连起来。', 'writing-article-preview'));
        parent.appendChild(details);
    }
    function paragraphs(parent, key, focusOnly = false) {
        const cards = focusOnly ? data.outline.filter((card) => card.focus) : data.outline;
        if (!cards.length) {
            note(focusOnly ? '先回到“搭提纲”，给最值得展开的一段标上 ★。' : '先在提纲里安排一段，再开始写。', parent);
            parent.appendChild(button('回到搭提纲', () => void moveStage(1, false), { light: true }));
        }
        for (const card of cards) {
            const index = data.outline.indexOf(card);
            const section = node('section', '', 'writing-paragraph-card');
            const heading = node('div', '', 'writing-paragraph-heading');
            heading.appendChild(node('span', `第 ${index + 1} 段`));
            if (card.focus) heading.appendChild(node('span', '重点展开', 'writing-focus-badge'));
            section.appendChild(heading);
            note(`提纲：${card.content || '还没有关键词'}${card.purpose ? '\n作用：' + card.purpose : ''}`, section, 'writing-outline-note');
            const count = node('p', `${(data[key][card.id] || '').length} 字`, 'writing-count');
            field(section, focusOnly ? '把这一刻写给读者看' : '用自己的话写这一段', data[key][card.id], (value) => {
                data[key][card.id] = value; count.textContent = `${value.length} 字`;
            }, { id: 'writing-paragraph-' + card.id, rows: focusOnly ? 6 : 5, maxLength: 6000,
                help: card.focus ? '选有用的动作、对话、观察或想法，不必每种都写。' : '简要交代也可以，不用每段写得一样长。' });
            section.appendChild(count); parent.appendChild(section);
        }
        renderOrphans(parent);
        articlePreview(parent, data[key]);
    }
    function revision(parent) {
        const round = data.review_round === 'language' ? 'language' : 'structure';
        const controls = node('div', '', 'writing-rounds');
        for (const [value, label] of [['structure', '第一轮 · 内容与顺序'], ['language', '第二轮 · 表达与字词']]) {
            const tab = button(label, () => {
                if (editable()) {
                    if (value === 'language' && !data.language_started) {
                        data.final_paragraphs = copy(data.structure_paragraphs); data.language_started = true;
                    }
                    data.review_round = value; changed();
                } else data.review_round = value;
                renderStage();
            }, { light: true, disabled: navigationBusy });
            tab.setAttribute('aria-selected', String(value === round)); controls.appendChild(tab);
        }
        parent.appendChild(controls);
        note(round === 'structure' ? '先看事情是否连贯、重点是否清楚。一次选一两处修改就好。'
            : '再看句意、重复、词语和标点。已经清楚的句子可以保留。', parent);
        if (editable() && round === 'structure' && !data.structure_started) {
            data.structure_paragraphs = copy(data.paragraphs); data.structure_started = true; changed();
        }
        if (editable() && round === 'language' && !data.language_started) {
            data.final_paragraphs = copy(data.structure_paragraphs); data.language_started = true; changed();
        }
        const key = round === 'structure' ? 'structure_paragraphs' : 'final_paragraphs';
        paragraphs(parent, key);
        const review = node('div', '', 'writing-review-checks');
        review.appendChild(node('h4', '我读过，也检查过'));
        const defaults = round === 'structure'
            ? [['topic', '材料与题目相合'], ['order', '别人能跟着顺序读明白'], ['focus', '重点一刻有具体内容'], ['ending', '结尾接得上前文']]
            : [['clear', '句子意思清楚'], ['repeat', '重复的话有必要'], ['words', '再看看词语、错别字和标点']];
        for (const [id, label] of defaults) check(review, label, data.checks[round].includes(id), (checked) => {
            data.checks[round] = checked ? [...new Set([...data.checks[round], id])] : data.checks[round].filter((value) => value !== id);
        }, 'writing-check-' + id);
        parent.appendChild(review);
        field(parent, '这轮我改了哪里？为什么？', data.revision_notes[round], (value) => { data.revision_notes[round] = value; }, { id: 'writing-revision-note', rows: 2, help: '说清一处修改即可。检查后没有需要修改的地方，可以选下面这一项。' });
        check(parent, '本轮检查后不用修改', data.no_change[round], (value) => { data.no_change[round] = value; }, 'writing-no-change');
        if (round === 'structure' && data.language_started) note('表达稿已经写过了。这里的修改会保留在内容稿中，稍后请再对照表达稿，不会自动覆盖它。', parent);
    }
    function comparison(parent, before, after) {
        const grid = node('div', '', 'writing-compare');
        const left = node('section'); const right = node('section');
        left.appendChild(node('h4', '我的初稿')); right.appendChild(node('h4', '我的修改稿'));
        for (const card of data.outline) {
            const original = before?.[card.id] || ''; const revised = after?.[card.id] || '';
            left.appendChild(node('p', original || '（这一段还没有写）'));
            right.appendChild(node('p', revised || '（这一段还没有写）', original !== revised ? 'writing-changed' : ''));
        }
        grid.append(left, right); parent.appendChild(grid);
    }
    function reflection(parent) {
        if (attempt.status === 'completed') {
            const complete = node('div', '', 'writing-completion');
            complete.appendChild(node('h3', '这次练习完成了'));
            note('提纲、初稿和修改过程都已保存。下次可以换一种游戏，再自己试着安排重点。', complete);
            if (!session().account?.is_parent) complete.appendChild(button('同一题，再练一次', () => void startLesson(attempt.lesson_id)));
            parent.appendChild(complete);
        }
        note('看看两份文字。用一句话说清一处修改，以及它为什么更容易让读者看明白。', parent);
        comparison(parent, data.paragraphs, data.final_paragraphs);
        field(parent, '我这次学会了什么？', data.reflection.learned, (value) => { data.reflection.learned = value; }, { id: 'writing-reflection-learned', rows: 3, help: '可以从“我补上了……”“我删掉了……”“我调整了……”开始。' });
        field(parent, '换一种游戏再写时，我想注意什么？（选填）', data.reflection.next_time, (value) => { data.reflection.next_time = value; }, { id: 'writing-reflection-next_time', rows: 2 });
        articlePreview(parent, data.final_paragraphs, '阅读我的整篇作品');
    }

    function renderStage() {
        if (!attempt || !data) return;
        const guide = stageGuide();
        el('writing-title').textContent = lesson?.title || '一次有趣的课间游戏';
        el('writing-stage-number').textContent = `第 ${viewStage + 1} 步 / 共 6 步`;
        el('writing-stage-title').textContent = guide.title || STAGES[viewStage];
        el('writing-stage-goal').textContent = guide.goal || guide.question || '';
        el('writing-read-only').hidden = !readOnly && attempt.status !== 'completed';
        el('writing-read-only').textContent = session().account?.is_parent ? '家长查看模式：可以看过程和版本，正文由学生编辑。'
            : attempt.status === 'completed' ? '作品已保存。可以查看过程，或再开一次练习。' : '当前为只读查看。';
        renderSteps(); renderHints();
        const main = el('writing-stage-content'); main.replaceChildren();
        if (viewStage === 0) planning(main);
        else if (viewStage === 1) outline(main);
        else if (viewStage === 2) paragraphs(main, 'paragraphs', true);
        else if (viewStage === 3) paragraphs(main, 'paragraphs');
        else if (viewStage === 4) revision(main);
        else reflection(main);
        el('writing-prev').disabled = navigationBusy || viewStage === 0;
        el('writing-prev').textContent = viewStage === 4 && data.review_round === 'language' ? '返回内容修改' : '上一步';
        el('writing-next').hidden = viewStage === 5;
        el('writing-next').disabled = navigationBusy || !!localRecovery;
        el('writing-next').textContent = viewStage === 4 && data.review_round === 'structure' ? '完成内容修改，检查表达' : '完成本步，继续';
        el('writing-complete').hidden = viewStage !== 5 || !editable();
        el('writing-complete').disabled = navigationBusy;
        el('writing-save-exit').textContent = editable() ? '保存并退出' : '返回练习列表';
        el('writing-save-exit').disabled = navigationBusy;
    }
    function validateStage() {
        if (viewStage === 0 && (!data.planning.situation.trim() || !data.planning.main_idea.trim())) return '先用关键词说清谁在玩什么，以及你最想写出的意思。';
        if (viewStage === 1 && (!data.outline.some((card) => card.content.trim()) || !data.outline.some((card) => card.focus && card.content.trim()))) return '先安排至少一段内容，再给最想展开的一段标上 ★。段数由你决定。';
        if (viewStage === 2 && !data.outline.some((card) => card.focus && data.paragraphs[card.id]?.trim())) return '先给重点段写一点自己的内容，短句也可以。';
        if (viewStage === 3 && !orderedText(data, data.paragraphs).trim()) return '先写下自己的初稿，再开始修改。';
        if (viewStage === 4) {
            const round = data.review_round;
            if (!orderedText(data, round === 'structure' ? data.structure_paragraphs : data.final_paragraphs).trim()) return '这份稿子还没有文字，请先写一点内容。';
            if (!data.revision_notes[round].trim() && !data.no_change[round]) return '用一句话说说这轮改了哪里；如果检查后不用改，可以勾选“本轮检查后不用修改”。';
        }
        return '';
    }
    async function moveStage(next, validate = true) {
        if (!attempt || navigationBusy || next === viewStage) return;
        if (localRecovery && next > furthestStage) return message('先决定继续本机草稿，还是使用云端这一份。', true);
        if (!editable()) { viewStage = Math.max(0, Math.min(5, next)); renderStage(); return; }
        if (validate && next > viewStage) { const error = validateStage(); if (error) return message(error, true); }
        navigationBusy = true; renderSteps();
        const ctx = capture();
        try {
            let label = '';
            if (viewStage === 1 && next > 1) label = '提纲';
            if (viewStage === 3 && next > 3) {
                label = '初稿';
                if (!data.structure_started) { data.structure_paragraphs = copy(data.paragraphs); data.structure_started = true; changed(); }
            }
            if (viewStage === 4 && next > 4) label = '修改稿';
            await flush(label);
            if (!current(ctx)) return;
            viewStage = Math.max(0, Math.min(5, next)); furthestStage = Math.max(furthestStage, viewStage); data.stage = viewStage; changed();
            await flush();
            if (!current(ctx)) return;
            message(); renderStage();
            el('writing-stage-title').scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
        } catch (error) { if (current(ctx)) message(error.message || '请先保存，再继续。', true); }
        finally { if (current(ctx)) { navigationBusy = false; renderStage(); } }
    }
    async function nextStage() {
        if (!attempt || navigationBusy) return;
        if (localRecovery) return message('先决定继续本机草稿，还是使用云端这一份。', true);
        if (!editable()) return moveStage(Math.min(5, viewStage + 1), false);
        const error = validateStage(); if (error) return message(error, true);
        if (viewStage === 4 && data.review_round === 'structure') {
            navigationBusy = true;
            const ctx = capture();
            try {
                if (!data.language_started) { data.final_paragraphs = copy(data.structure_paragraphs); data.language_started = true; }
                data.review_round = 'language'; changed();
                await flush('内容修改稿');
                if (current(ctx)) { message(); renderStage(); }
            } catch (error) { if (current(ctx)) message(error.message, true); }
            finally { if (current(ctx)) { navigationBusy = false; renderStage(); } }
            return;
        }
        return moveStage(Math.min(5, viewStage + 1));
    }
    async function finish() {
        if (!editable() || navigationBusy) return;
        if (!data.reflection.learned.trim()) return message('用一句话说说这次学会了什么，再保存作品。', true);
        const ctx = capture(); navigationBusy = true; completing = true; renderStage();
        try {
            await flush('', true);
            if (!current(ctx)) return;
            readOnly = true; viewStage = 5; message('作品和修改过程已经保存。', false); renderStage();
            void portal()?.refreshRecords?.();
        } catch (error) { if (current(ctx)) message(error.message || '作品还没有保存完成，请重试。', true); }
        finally { if (current(ctx)) { navigationBusy = false; completing = false; renderStage(); } }
    }

    function applyAttempt(result) {
        if (!result.attempt || !validState(result.attempt.data) || !result.attempt.lesson_snapshot) throw new Error('练习内容读取失败，请刷新后重试。');
        attempt = result.attempt;
        data = copy(attempt.data);
        lesson = attempt.lesson_snapshot;
        versions = Array.isArray(result.versions) ? result.versions : [];
        readOnly = !!result.read_only || !!session().account?.is_parent || attempt.status === 'completed';
        viewStage = attempt.status === 'completed' ? 5 : data.stage;
        furthestStage = viewStage;
        sequence = 0; savedSequence = 0; conflict = false; navigationBusy = false; completing = false;
        el('writing-conflict').hidden = true;
        el('writing-history').hidden = true;
        el('writing-home').hidden = true; el('writing-editor').hidden = false;
        saveState(attempt.status === 'completed' ? '作品已保存' : readOnly ? '只读查看' : '已保存');
        localRecovery = readBackup();
        el('writing-local-recovery').hidden = !localRecovery;
        el('writing-recover-local').textContent = localRecovery && localRecovery.base_revision !== attempt.revision
            ? '查看本机草稿并对照' : '继续本机草稿';
        if (el('writing-use-server-confirm')) el('writing-use-server-confirm').hidden = true;
        renderStage(); el('writing-title').focus?.({ preventScroll: true });
    }
    async function openAttempt(id) {
        const ctx = capture(); const requestId = ++viewRequest;
        clearTimeout(timer);
        if (editable() && sequence !== savedSequence) {
            try { await flush(); } catch (_) { return; }
        }
        if (!current(ctx, false)) return;
        message('正在打开练习…');
        try {
            const result = await request(`/attempts/${encodeURIComponent(id)}`);
            if (!current(ctx, false) || requestId !== viewRequest) return;
            applyAttempt(result); message();
        } catch (error) { if (current(ctx, false) && requestId === viewRequest) message(error.message || '练习加载失败，请重试。', true); }
    }
    async function startLesson(id) {
        if (session().account?.is_parent || navigationBusy) return;
        const ctx = capture(); const requestId = ++viewRequest;
        navigationBusy = true; message('正在准备练习…');
        try {
            if (editable() && sequence !== savedSequence) await flush();
            const result = await request('/attempts', apiOptions({ lesson_id: id }));
            if (!current(ctx, false) || requestId !== viewRequest) return;
            applyAttempt(result); message('可以先写关键词，慢慢把想法安排清楚。');
        } catch (error) { if (current(ctx, false) && requestId === viewRequest) message(error.message || '开始练习失败，请重试。', true); }
        finally { if (current(ctx, false)) navigationBusy = false; }
    }
    async function open() {
        if (session().subject !== 'chinese' || !session().token || !session().account) return;
        const ctx = capture(); const requestId = ++viewRequest;
        clearTimeout(timer);
        if (editable() && sequence !== savedSequence) void flush().catch(() => {});
        el('writing-home').hidden = false; el('writing-editor').hidden = true; el('writing-history').hidden = true;
        message('正在加载作文练习…');
        try {
            const [lessons, attempts] = await Promise.all([request('/lessons'), request('/attempts')]);
            if (!current(ctx, false) || requestId !== viewRequest) return;
            const list = el('writing-lessons'); list.replaceChildren();
            for (const item of lessons.items || []) {
                const card = node('article', '', 'writing-lesson-card');
                const info = node('div');
                info.append(node('span', '第 1 题 · 搭框架到修改', 'writing-lesson-meta'), node('h3', item.title));
                note(item.goal || item.description || '把一件事按顺序安排清楚，把有趣的一刻写具体。', info);
                note(item.time_hint || '可以分几次完成，每次专心练一小步。', info);
                card.appendChild(info);
                if (!session().account?.is_parent) card.appendChild(button('开始一次新练习', () => void startLesson(item.id)));
                list.appendChild(card);
            }
            const history = el('writing-attempt-list'); history.replaceChildren();
            for (const item of attempts.items || []) {
                const card = node('article', '', 'writing-attempt-card');
                const info = node('div'); info.appendChild(node('h4', item.title || item.lesson_title || '一次有趣的课间游戏'));
                note(`${item.status === 'completed' ? '已完成' : '进行中 · ' + STAGES[item.stage ?? item.data?.stage ?? 0]}${item.updated_at ? ' · ' + item.updated_at.replace('T', ' ').slice(0, 16) : ''}`, info);
                const actions = node('div'); actions.appendChild(button(item.status === 'completed' || session().account?.is_parent ? '查看作品与过程' : '继续练习', () => void openAttempt(item.id), { light: true }));
                card.append(info, actions); history.appendChild(card);
            }
            if (!(attempts.items || []).length) history.appendChild(node('p', session().account?.is_parent ? '关联学生还没有开始作文练习。' : '还没有练习记录，从上面的第 1 题开始吧。', 'writing-empty'));
            message();
        } catch (error) { if (current(ctx, false) && requestId === viewRequest) message(error.message || '作文练习加载失败，请重试。', true); }
    }
    async function exit() {
        if (navigationBusy) return;
        const ctx = capture(); navigationBusy = true;
        try {
            await flush();
            if (!current(ctx)) return;
            while (editable() && sequence !== savedSequence && current(ctx)) await flush();
            if (!current(ctx)) return;
            navigationBusy = false;
            await open();
        } catch (error) { if (current(ctx)) message(error.message || '内容还未同步，请重试保存。', true); }
        finally { if (current(ctx)) { navigationBusy = false; if (!el('writing-editor').hidden) renderStage(); } }
    }
    function leaveView() {
        if (editable() && sequence !== savedSequence && !conflict) void flush().catch(() => {});
        clearTimeout(timer);
        el('writing-history').hidden = true;
    }
    function reset() {
        // The user-scoped local backup survives an interrupted save; visible private content does not.
        if (editable() && sequence !== savedSequence) { writeBackup(); void flush().catch(() => {}); }
        clearTimeout(timer);
        generation += 1; viewRequest += 1;
        attempt = null; data = null; lesson = null; versions = []; selectedVersion = null; localRecovery = null; furthestStage = 0;
        sequence = 0; savedSequence = 0; conflict = false; navigationBusy = false; completing = false; queue = Promise.resolve();
        for (const id of ['writing-lessons', 'writing-attempt-list', 'writing-stage-content', 'writing-hints', 'writing-steps', 'writing-version-preview']) el(id).replaceChildren();
        el('writing-title').textContent = ''; el('writing-save-state').textContent = '';
        el('writing-version-list').replaceChildren();
        if (el('writing-draft-copy')) { el('writing-draft-copy').value = ''; el('writing-draft-copy').remove?.(); }
        if (el('writing-use-server-confirm')) el('writing-use-server-confirm').hidden = true;
        el('writing-editor').hidden = true; el('writing-home').hidden = false;
        el('writing-history').hidden = true; el('writing-conflict').hidden = true; el('writing-local-recovery').hidden = true;
        message();
    }

    function versionPreview(state) {
        const parent = el('writing-version-preview'); parent.replaceChildren();
        parent.appendChild(node('h4', '选材与想法'));
        for (const [key, title] of [['situation', '写谁，什么时候，玩什么'], ['main_idea', '想表达的意思'], ['materials', '准备使用的材料'], ['ending', '准备怎样结尾']]) {
            if (state.planning[key]) note(`${title}：${state.planning[key]}`, parent);
        }
        parent.appendChild(node('h4', '提纲'));
        state.outline.forEach((card, index) => note(`${index + 1}${card.focus ? ' ★' : ''}  ${card.content || '（未填写）'}${card.purpose ? '\n作用：' + card.purpose : ''}`, parent));
        for (const [key, title] of [['paragraphs', '初稿'], ['structure_paragraphs', '内容修改稿'], ['final_paragraphs', '修改稿']]) {
            const text = orderedText(state, state[key]);
            if (text) { parent.appendChild(node('h4', title)); note(text, parent); }
            const ids = new Set(state.outline.map((card) => card.id));
            const orphans = Object.entries(state[key]).filter(([id, value]) => !ids.has(id) && value.trim());
            if (orphans.length) {
                parent.appendChild(node('h4', `未归入提纲的文字 · ${title}`));
                for (const [id, value] of orphans) note(`段落 ${id}\n${value}`, parent);
            }
        }
        if (state.reflection.learned) { parent.appendChild(node('h4', '我的收获')); note(state.reflection.learned, parent); }
        if (state.reflection.next_time) { parent.appendChild(node('h4', '下一次想注意')); note(state.reflection.next_time, parent); }
    }
    async function loadVersion(id) {
        if (!attempt || !id) return;
        const ctx = capture(); selectedVersion = null;
        el('writing-version-restore').hidden = true; el('writing-restore-confirmation').hidden = true;
        el('writing-version-preview').textContent = '正在读取版本…';
        try {
            const result = await request(`/attempts/${attempt.id}/versions/${encodeURIComponent(id)}`);
            if (!current(ctx) || el('writing-version-list').value !== id) return;
            selectedVersion = result.version;
            versionPreview(selectedVersion.data);
            el('writing-version-restore').hidden = !editable();
        } catch (error) { if (current(ctx)) el('writing-version-preview').textContent = error.message || '版本读取失败。'; }
    }
    async function showHistory() {
        if (!attempt || navigationBusy) return;
        const ctx = capture();
        try {
            await flush();
            if (!current(ctx)) return;
            const result = await request(`/attempts/${attempt.id}`);
            if (!current(ctx)) return;
            versions = result.versions || [];
            const select = el('writing-version-list'); select.replaceChildren();
            for (const version of [...versions].reverse()) {
                const option = node('option', `${version.label} · ${String(version.created_at || '').replace('T', ' ').slice(0, 16)}`);
                option.value = version.id; select.appendChild(option);
            }
            el('writing-history').hidden = false;
            if (versions.length) { select.value = versions[0].id; await loadVersion(select.value); }
            else el('writing-version-preview').textContent = '完成提纲或初稿后，可以在这里看到关键版本。';
            select.focus?.();
        } catch (error) { if (current(ctx)) message(error.message || '请先保存当前内容。', true); }
    }
    async function restoreVersion() {
        if (!editable() || !selectedVersion || navigationBusy) return;
        const ctx = capture(); navigationBusy = true;
        try {
            await flush();
            const result = await request(`/attempts/${attempt.id}/restore`, apiOptions({ base_revision: attempt.revision, version_id: selectedVersion.id }));
            if (!current(ctx)) return;
            applyAttempt(result); message('已恢复所选版本，原来的版本也保留着。');
        } catch (error) { if (current(ctx)) { if (error.status === 409) { writeBackup(ctx); conflict = true; el('writing-conflict').hidden = false; } message(error.message || '恢复失败，请重试。', true); } }
        finally { if (current(ctx)) navigationBusy = false; }
    }
    async function copyDraft() {
        if (!data) return;
        const draft = localRecovery?.data || data;
        const unused = [];
        const assigned = new Set(draft.outline.map((card) => card.id));
        for (const [key, title] of [['paragraphs', '初稿'], ['structure_paragraphs', '内容修改稿'], ['final_paragraphs', '修改稿']]) {
            for (const [id, value] of Object.entries(draft[key])) {
                if (!assigned.has(id) && value.trim()) unused.push(`【未归入提纲的文字 · ${title} · ${id}】\n${value}`);
            }
        }
        const text = [lesson?.title, `练习方式：${draft.mode === 'personal' ? '自己的真实经历' : '给定材料练习'}`,
            '【选材与想法】', `谁在什么时候玩什么：${draft.planning.situation}`, `想表达的意思：${draft.planning.main_idea}`,
            `准备使用的材料：${draft.planning.materials}`, `准备怎样结尾：${draft.planning.ending}`,
            '【我的提纲】', ...draft.outline.map((card, i) => `${i + 1}${card.focus ? ' ★' : ''} ${card.content}\n作用：${card.purpose}`),
            '\n【初稿】', orderedText(draft, draft.paragraphs), '\n【内容修改稿】', orderedText(draft, draft.structure_paragraphs),
            '\n【修改稿】', orderedText(draft, draft.final_paragraphs), ...unused,
            '\n【修改说明】', `内容：${draft.revision_notes.structure}${draft.no_change.structure ? '（本轮检查后不用改）' : ''}`,
            `表达：${draft.revision_notes.language}${draft.no_change.language ? '（本轮检查后不用改）' : ''}`,
            '\n【我的收获】', draft.reflection.learned, '\n【下一次想注意】', draft.reflection.next_time].join('\n');
        try {
            if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
            await navigator.clipboard.writeText(text); message('已复制本机这一份，可以粘贴保存后再载入最新稿。');
        } catch (_) {
            let field = el('writing-draft-copy');
            if (!field) { field = node('textarea', '', 'writing-draft-copy'); field.id = 'writing-draft-copy'; el('writing-conflict').appendChild(field); }
            field.value = text; field.readOnly = true; field.focus?.(); field.select?.();
            message('复制框已展开，请选中并复制内容后，再载入最新稿。');
        }
    }

    el('writing-next').addEventListener('click', () => void nextStage());
    el('writing-prev').addEventListener('click', () => {
        if (viewStage === 4 && data?.review_round === 'language') {
            data.review_round = 'structure'; if (editable()) changed(); renderStage();
        } else void moveStage(Math.max(0, viewStage - 1), false);
    });
    el('writing-save-exit').addEventListener('click', () => void exit());
    el('writing-complete').addEventListener('click', () => void finish());
    el('writing-history-open').addEventListener('click', () => void showHistory());
    el('writing-history-close').addEventListener('click', () => { el('writing-history').hidden = true; el('writing-history-open').focus?.(); });
    el('writing-version-list').addEventListener('change', () => void loadVersion(el('writing-version-list').value));
    el('writing-version-restore').addEventListener('click', () => { if (editable()) el('writing-restore-confirmation').hidden = false; });
    el('writing-version-restore-cancel').addEventListener('click', () => { el('writing-restore-confirmation').hidden = true; });
    el('writing-version-restore-confirm').addEventListener('click', () => void restoreVersion());
    el('writing-copy-draft').addEventListener('click', () => void copyDraft());
    el('writing-conflict-load').addEventListener('click', () => {
        if (!attempt) return;
        // A conflicting backup remains available until the student explicitly chooses a version.
        conflict = false; sequence = savedSequence; void openAttempt(attempt.id);
    });
    el('writing-recover-local').addEventListener('click', () => {
        if (!writeAllowed() || !localRecovery) return;
        if (localRecovery.base_revision !== attempt.revision) {
            conflict = true;
            el('writing-conflict').hidden = false;
            message('本机稿和云端稿的版本不同。当前显示云端稿；可复制本机稿，再明确选择要保留的内容。', true);
            renderStage(); return;
        }
        data = copy(localRecovery.data); viewStage = data.stage; furthestStage = Math.max(furthestStage, viewStage); localRecovery = null;
        el('writing-local-recovery').hidden = true; changed(); renderStage();
    });
    el('writing-keep-server').addEventListener('click', () => {
        if (!writeAllowed() || !localRecovery) return;
        let confirmation = el('writing-use-server-confirm');
        if (!confirmation) {
            confirmation = node('div', '', 'writing-local-recovery');
            confirmation.id = 'writing-use-server-confirm';
            note('这会放弃本机尚未同步的修改。需要保留时，先复制本机内容，再选择云端稿。', confirmation);
            const discard = button('确认放弃本机未同步修改', () => {
                if (!writeAllowed() || !localRecovery) return;
                removeBackup(); localRecovery = null; conflict = false;
                el('writing-local-recovery').hidden = true; el('writing-conflict').hidden = true; confirmation.hidden = true;
                saveState('已保存'); message(); renderStage();
            }, { light: true });
            discard.id = 'writing-confirm-server';
            confirmation.appendChild(discard);
            confirmation.appendChild(button('先保留本机稿', () => { confirmation.hidden = true; }, { light: true }));
            el('writing-local-recovery').appendChild(confirmation);
        }
        confirmation.hidden = false;
    });
    document.addEventListener?.('keydown', (event) => { if (event.key === 'Escape' && !el('writing-history').hidden) { el('writing-history').hidden = true; el('writing-history-open').focus?.(); } });
    window.addEventListener('beforeunload', () => { if (editable() && sequence !== savedSequence) writeBackup(); });
    window.subjectWriting = { open, openAttempt, leaveView, reset };
})();
