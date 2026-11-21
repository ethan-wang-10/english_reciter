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
    const request = (path, options = {}) => portal().apiRequest(PREFIX + path, { ...options, headers: { ...(options.headers || {}), 'X-Writing-Flow': 'guided-v2' } });
    let generation = 0;
    let viewRequest = 0;
    let attempt = null;
    let data = null;
    let versions = [];
    let lesson = null;
    const teachingText = (key, fallback) => lesson?.teaching?.[key] || fallback;
    const materialLabels = () => lesson?.teaching?.material_labels || { source: '素材来源', activity: '玩了什么', moment: '记得的一刻', feeling: '当时感受' };
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
    let guidedView = 'question';
    let guidedStep = 0;
    let guidedExample = 'total';
    let guidedFullExample = false;
    let guidedHelpExamples = false;
    let editingEnding = false;
    let guidanceConfirming = false;
    let transitionBusy = false;
    let abandonBusy = false;
    const lessonFlows = new Map();
    const capture = () => ({ generation, token: session().token, student: student(), attemptId: attempt?.id });
    const current = (ctx, checkAttempt = true) => ctx.generation === generation && ctx.token === session().token
        && ctx.student === student() && (!checkAttempt || ctx.attemptId === attempt?.id);
    const writeAllowed = () => !!attempt && !readOnly && attempt.status === 'in_progress';
    const editable = () => writeAllowed() && !localRecovery;
    const backupKey = (ctx = capture()) => `itorange-writing-draft:${ctx.student}:${ctx.attemptId}`;
    const orderedText = (state, paragraphs) => state.outline.map((card) => paragraphs?.[card.id] || '').filter((text) => text.trim()).join('\n\n');
    function validGuidance(value) {
        const object = (item) => !!item && typeof item === 'object' && !Array.isArray(item);
        const steps = (items) => Array.isArray(items) && items.every((item) => Number.isInteger(item) && item >= 0 && item <= 8);
        return object(value) && value.version === 1 && Number.isInteger(value.step) && value.step >= 0 && value.step <= 8
            && steps(value.confirmed) && steps(value.review) && value.review.every((item) => value.confirmed.includes(item))
            && ['theme','customTheme','structure','ownDetail','sentence','paragraph'].every((key) => typeof value[key] === 'string')
            && object(value.slots) && object(value.notes) && ['begin','middle','end'].every((key) => Array.isArray(value.slots[key])
                && value.slots[key].every((id) => typeof id === 'string') && typeof value.notes[key] === 'string')
            && Array.isArray(value.details) && value.details.every((id) => typeof id === 'string') && typeof value.endingOkay === 'boolean';
    }
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
            && typeof value.structure_started === 'boolean' && typeof value.language_started === 'boolean'
            && (value.guidance === undefined || validGuidance(value.guidance))
            && (value.coaching === undefined || validCoaching(value.coaching));
    }
    function validCoaching(value) {
        const object = (item) => !!item && typeof item === 'object' && !Array.isArray(item);
        return object(value) && value.version === 1 && object(value.material) && Number.isInteger(value.material.step)
            && value.material.step >= 0 && value.material.step <= 4 && ['', 'personal', 'scenario'].includes(value.material.source)
            && ['activity', 'moment', 'feeling'].every((key) => typeof value.material[key] === 'string')
            && object(value.comparison) && Array.isArray(value.comparison.choices) && value.comparison.choices.every((id) => typeof id === 'string')
            && typeof value.comparison.reason === 'string' && object(value.evidence)
            && ['structure', 'language'].every((round) => object(value.evidence[round]) && ['before', 'after', 'reason'].every((key) => typeof value.evidence[round][key] === 'string'));
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
        result.disabled = !!options.disabled || transitionBusy || abandonBusy;
        if (options.label) result.setAttribute('aria-label', options.label);
        result.addEventListener('click', (event) => { if (transitionBusy || abandonBusy) return; return handler(event); });
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
        input.disabled = !editable() || completing || guidanceConfirming || transitionBusy || abandonBusy;
        input.autocomplete = 'off';
        if (options.placeholder) input.placeholder = options.placeholder;
        if (options.list) input.setAttribute('list', options.list);
        input.addEventListener('input', () => { if (!editable() || completing || guidanceConfirming || transitionBusy || abandonBusy) return; update(input.value); changed(); });
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
        input.disabled = !editable() || completing || guidanceConfirming || transitionBusy || abandonBusy;
        if (id) input.id = id;
        input.addEventListener('change', () => { if (!editable() || completing || guidanceConfirming || transitionBusy || abandonBusy) return; update(input.checked); changed(); });
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
        if (isGuided()) return renderGuidedSteps();
        const steps = el('writing-steps');
        steps.classList.remove('writing-guided-steps');
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
            radio.checked = data.mode === value; radio.disabled = !editable() || transitionBusy || abandonBusy;
            radio.addEventListener('change', () => { if (editable() && !transitionBusy && !abandonBusy) { data.mode = value; changed(); renderStage(); } });
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
        const fields = lesson.teaching ? [
            ['situation', '谁在什么时候，遇到了什么事？', '可沿用原提纲，先写关键词。'],
            ['main_idea', '通过这件事，我想表达什么？', '想一想，人物的反应由哪些行动引起。'],
            ['materials', '准备写哪些材料？哪些可以先不写？（选填）', '把真实的想法、动作或对话记下来。'],
            ['ending', '事情怎样结束？怎样回应题目？（选填）', '让结尾接得上前面的事情。'],
        ] : [
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
        const purposes = lesson.teaching ? ['交代起因，带出选择', '写清自己的想法与行动', '回应题目，点明感受'] : PURPOSES;
        for (const text of purposes) { const option = node('option'); option.value = text; suggestions.appendChild(option); }
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
        const wholeRevision = isGuided() && ['structure_paragraphs', 'final_paragraphs'].includes(key);
        const cards = wholeRevision ? [{ id: 'p_full', focus: false }]
            : focusOnly ? data.outline.filter((card) => card.focus) : data.outline;
        if (!cards.length) {
            note(focusOnly ? '先回到“搭提纲”，给最值得展开的一段标上 ★。' : '先在提纲里安排一段，再开始写。', parent);
            parent.appendChild(button('回到搭提纲', () => void moveStage(1, false), { light: true }));
        }
        for (const card of cards) {
            const index = data.outline.indexOf(card);
            const section = node('section', '', 'writing-paragraph-card');
            const heading = node('div', '', 'writing-paragraph-heading');
            heading.appendChild(node('span', wholeRevision
                ? key === 'structure_paragraphs' ? '我的内容修改稿（全文）' : '我的表达修改稿（全文）'
                : `第 ${index + 1} 段`));
            if (card.focus && !wholeRevision) heading.appendChild(node('span', '重点展开', 'writing-focus-badge'));
            section.appendChild(heading);
            if (!wholeRevision) note(`提纲：${card.content || '还没有关键词'}${card.purpose ? '\n作用：' + card.purpose : ''}`, section, 'writing-outline-note');
            const count = node('p', `${(data[key][card.id] || '').length} 字`, 'writing-count');
            const label = wholeRevision
                ? key === 'structure_paragraphs' ? '通读全文，调整内容和顺序' : '通读全文，检查表达和字词'
                : focusOnly ? '把这一刻写给读者看' : '用自己的话写这一段';
            field(section, label, data[key][card.id], (value) => {
                data[key][card.id] = value; count.textContent = `${value.length} 字`; updateCoachingEvidence();
            }, { id: 'writing-paragraph-' + card.id, rows: wholeRevision ? 9 : focusOnly ? 6 : 5, maxLength: 6000,
                help: wholeRevision ? '这里是整篇文章。一次改一两处，已经清楚的地方可以保留。'
                    : card.focus ? '选有用的动作、对话、观察或想法，不必每种都写。' : '简要交代也可以，不用每段写得一样长。' });
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
        if (isCoached()) renderCoachingEvidence(parent, round);
        else {
            field(parent, '这轮我改了哪里？为什么？', data.revision_notes[round], (value) => { data.revision_notes[round] = value; }, { id: 'writing-revision-note', rows: 2, help: '说清一处修改即可。检查后没有需要修改的地方，可以选下面这一项。' });
            check(parent, '本轮检查后不用修改', data.no_change[round], (value) => { data.no_change[round] = value; }, 'writing-no-change');
        }
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
            note(teachingText('completion_note', '提纲、初稿和修改过程都已保存。下次可以换一种游戏，再自己试着安排重点。'), complete);
            if (!session().account?.is_parent) complete.appendChild(button('同一题，再练一次', () => void startLesson(attempt.lesson_id)));
            parent.appendChild(complete);
        }
        note('看看两份文字。用一句话说清一处修改，以及它为什么更容易让读者看明白。', parent);
        comparison(parent, data.paragraphs, data.final_paragraphs);
        field(parent, '我这次学会了什么？', data.reflection.learned, (value) => { data.reflection.learned = value; }, { id: 'writing-reflection-learned', rows: 3, help: '可以从“我补上了……”“我删掉了……”“我调整了……”开始。' });
        field(parent, teachingText('reflection_next', '换一种游戏再写时，我想注意什么？（选填）'), data.reflection.next_time, (value) => { data.reflection.next_time = value; }, { id: 'writing-reflection-next_time', rows: 2 });
        articlePreview(parent, data.final_paragraphs, '阅读我的整篇作品');
    }

    function renderStage() {
        if (!attempt || !data) return;
        const guided = isGuided();
        el('writing-editor').classList.toggle('writing-guided', guided);
        el('writing-workspace')?.classList.toggle('writing-guided-workspace', guided);
        if (el('writing-guided-recap')) el('writing-guided-recap').hidden = !guided || viewStage > 3;
        if (guided && viewStage <= 3) return renderGuidedStage();
        const guide = stageGuide();
        el('writing-title').textContent = lesson?.title || '一次有趣的课间游戏';
        el('writing-stage-number').textContent = guided
            ? viewStage === 4 ? `修改阶段 · ${data.review_round === 'language' ? '第二轮表达与字词' : '第一轮内容与顺序'}` : '回看成果 · 保存作品'
            : `第 ${viewStage + 1} 步 / 共 6 步`;
        el('writing-stage-title').textContent = guide.title || STAGES[viewStage];
        el('writing-stage-goal').textContent = guide.goal || guide.question || '';
        el('writing-read-only').hidden = !readOnly && attempt.status !== 'completed';
        el('writing-read-only').textContent = session().account?.is_parent ? '家长查看模式：可以看过程和版本，正文由学生编辑。'
            : attempt.status === 'abandoned' ? '这份练习已放弃，已保存内容和版本仍可查看。' : attempt.status === 'completed' ? '作品已保存。可以查看过程，或再开一次练习。' : '当前为只读查看。';
        renderSteps(); renderHints();
        const helpAside = el('writing-hints').parentElement;
        if (helpAside) helpAside.hidden = guided;
        const main = el('writing-stage-content'); main.replaceChildren();
        if (viewStage === 0) planning(main);
        else if (viewStage === 1) outline(main);
        else if (viewStage === 2) paragraphs(main, 'paragraphs', true);
        else if (viewStage === 3) paragraphs(main, 'paragraphs');
        else if (viewStage === 4) revision(main);
        else reflection(main);
        if (guided && viewStage === 4) addGuidedHelpLink(main, '需要这一轮修改的帮助？');
        if (guided && guidedView === 'help') renderGuidedSupport(main);
        el('writing-prev').disabled = navigationBusy || viewStage === 0;
        el('writing-prev').textContent = viewStage === 4 && data.review_round === 'language' ? '返回内容修改' : '上一步';
        el('writing-next').hidden = viewStage === 5;
        el('writing-next').disabled = navigationBusy || !!localRecovery;
        el('writing-next').textContent = guided && guidedView !== 'question' ? '返回当前问题' : viewStage === 4 && data.review_round === 'structure' ? '完成内容修改，检查表达' : '完成本步，继续';
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
            if (isCoached()) { const issue = coachingEvidenceIssue(round); if (issue) return issue; }
            if (!data.revision_notes[round].trim() && !data.no_change[round]) return '用一句话说说这轮改了哪里；如果检查后不用改，可以勾选“本轮检查后不用修改”。';
        }
        return '';
    }
    async function moveStage(next, validate = true) {
        if (!attempt || navigationBusy || abandonBusy || next === viewStage) return;
        if (localRecovery && next > furthestStage) return message('先决定继续本机草稿，还是使用云端这一份。', true);
        if (!editable()) { viewStage = Math.max(0, Math.min(5, next)); renderStage(); return; }
        if (validate && next > viewStage) { const error = validateStage(); if (error) return message(error, true); }
        navigationBusy = true; transitionBusy = true; renderStage();
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
        finally { if (current(ctx)) { navigationBusy = false; transitionBusy = false; renderStage(); } }
    }
    async function nextStage() {
        if (!attempt || navigationBusy || abandonBusy) return;
        if (isGuided() && guidedView !== 'question') { guidedView = 'question'; renderStage(); return; }
        if (isGuided() && viewStage <= 3) return confirmGuidedStep();
        if (localRecovery) return message('先决定继续本机草稿，还是使用云端这一份。', true);
        if (!editable()) return moveStage(Math.min(5, viewStage + 1), false);
        const error = validateStage(); if (error) return message(error, true);
        if (viewStage === 4 && data.review_round === 'structure') {
            navigationBusy = true; transitionBusy = true; renderStage();
            const ctx = capture();
            try {
                if (!data.language_started) { data.final_paragraphs = copy(data.structure_paragraphs); data.language_started = true; }
                data.review_round = 'language'; changed();
                await flush('内容修改稿');
                if (current(ctx)) { message(); renderStage(); }
            } catch (error) { if (current(ctx)) message(error.message, true); }
            finally { if (current(ctx)) { navigationBusy = false; transitionBusy = false; renderStage(); } }
            return;
        }
        return moveStage(Math.min(5, viewStage + 1));
    }
    async function finish() {
        if (!editable() || navigationBusy || abandonBusy) return;
        if (isGuided() && guidedPending() !== null) { const pending = guidedPending(); openGuidedStep(pending); message('还有问题需要确认或复核，文字都会保留。', true); return; }
        if (isCoached()) for (const round of ['structure', 'language']) {
            const issue = coachingEvidenceIssue(round);
            if (issue) { viewStage = 4; data.stage = 4; data.review_round = round; guidedView = 'question'; changed(); renderStage(); message(issue, true); return; }
        }
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
        guidedStep = data.guidance?.step || 0; guidedView = 'question'; guidedFullExample = false; editingEnding = false; guidanceConfirming = false; transitionBusy = false;
        versions = Array.isArray(result.versions) ? result.versions : [];
        readOnly = !!result.read_only || !!session().account?.is_parent || attempt.status !== 'in_progress';
        viewStage = attempt.status === 'completed' ? 5 : data.stage;
        furthestStage = viewStage;
        sequence = 0; savedSequence = 0; conflict = false; navigationBusy = false; completing = false;
        el('writing-conflict').hidden = true;
        el('writing-history').hidden = true;
        el('writing-home').hidden = true; el('writing-editor').hidden = false;
        saveState(attempt.status === 'abandoned' ? '练习已放弃，内容保留' : attempt.status === 'completed' ? '作品已保存' : readOnly ? '只读查看' : '已保存');
        localRecovery = readBackup();
        el('writing-local-recovery').hidden = !localRecovery;
        el('writing-recover-local').textContent = localRecovery && localRecovery.base_revision !== attempt.revision
            ? '查看本机草稿并对照' : '继续本机草稿';
        if (el('writing-use-server-confirm')) el('writing-use-server-confirm').hidden = true;
        renderStage(); el('writing-title').focus?.({ preventScroll: true });
    }
    async function openAttempt(id) {
        if (abandonBusy || navigationBusy) return;
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
        if (session().account?.is_parent || navigationBusy || abandonBusy) return;
        const ctx = capture(); const requestId = ++viewRequest;
        navigationBusy = true; message('正在准备练习…');
        try {
            if (editable() && sequence !== savedSequence) await flush();
            let flow = lessonFlows.get(id);
            if (flow === undefined) { const loaded = await request('/lessons/' + encodeURIComponent(id)); flow = loaded.lesson?.coaching?.version === 1 ? 2 : loaded.lesson?.guidance?.version === 1 ? 1 : 0; }
            const result = await request('/attempts', apiOptions({ lesson_id: id, ...(flow === 1 || flow === 2 ? { flow_version: flow } : {}) }));
            if (!current(ctx, false) || requestId !== viewRequest) return;
            applyAttempt(result); message('可以先写关键词，慢慢把想法安排清楚。');
        } catch (error) { if (current(ctx, false) && requestId === viewRequest) message(error.message || '开始练习失败，请重试。', true); }
        finally { if (current(ctx, false)) navigationBusy = false; }
    }
    async function open() {
        if (abandonBusy) return;
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
            for (const [index, item] of (lessons.items || []).entries()) {
                lessonFlows.set(item.id, [1, 2].includes(item.flow_version) ? item.flow_version : 0);
                const card = node('article', '', 'writing-lesson-card');
                const info = node('div');
                info.append(node('span', `第 ${index + 1} 题 · 搭框架到修改`, 'writing-lesson-meta'), node('h3', item.title));
                note(item.goal || item.description || '把一件事按顺序安排清楚，把有趣的一刻写具体。', info);
                note(item.time_hint || '可以分几次完成，每次专心练一小步。', info);
                card.appendChild(info);
                if (!session().account?.is_parent) card.appendChild(button('开始一次新练习', () => void startLesson(item.id)));
                list.appendChild(card);
            }
            const history = el('writing-attempt-list'); history.replaceChildren();
            const visibleAttempts = (attempts.items || []).filter((item) => item.status !== 'abandoned');
            for (const item of visibleAttempts) {
                const card = node('article', '', 'writing-attempt-card');
                card.setAttribute('data-attempt-id', item.id);
                const info = node('div'); info.appendChild(node('h4', item.title || item.lesson_title || '一次有趣的课间游戏'));
                const savedStage = item.stage ?? item.data?.stage ?? 0;
                note(`${item.status === 'completed' ? '已完成' : '进行中 · ' + (item.guidance_step !== undefined && savedStage <= 3 ? guidedStepTitle(item.guidance_step) : STAGES[savedStage])}${item.updated_at ? ' · ' + item.updated_at.replace('T', ' ').slice(0, 16) : ''}`, info);
                const actions = node('div'); actions.appendChild(button(item.status === 'completed' || session().account?.is_parent ? '查看作品与过程' : '继续练习', () => void openAttempt(item.id), { light: true }));
                if (item.status === 'in_progress' && !session().account?.is_parent && !attempts.read_only) {
                    const abandon = button('放弃练习', () => requestAbandon(item, card, ctx), { light: true });
                    abandon.classList.add('writing-abandon');
                    abandon.setAttribute('aria-label', '放弃练习：' + (item.title || item.lesson_title || '作文练习'));
                    actions.appendChild(abandon);
                }
                card.append(info, actions); history.appendChild(card);
            }
            if (!visibleAttempts.length) history.appendChild(node('p', session().account?.is_parent ? '关联学生还没有开始作文练习。' : '还没有练习记录，从上面的第 1 题开始吧。', 'writing-empty'));
            message();
        } catch (error) { if (current(ctx, false) && requestId === viewRequest) message(error.message || '作文练习加载失败，请重试。', true); }
    }
    function freezeWritingHome(value) {
        for (const id of ['writing-home', 'writing-editor']) {
            el(id).querySelectorAll('button, input, textarea, select').forEach((control) => { control.disabled = value; });
        }
        if (!value && attempt && !el('writing-editor').hidden) renderStage();
    }
    function requestAbandon(item, card, listContext) {
        if (abandonBusy || navigationBusy || session().account?.is_parent || !current(listContext, false) || item.status !== 'in_progress') return;
        if (card.querySelector('.writing-abandon-confirmation')) return;
        const confirmation = node('div', '', 'writing-abandon-confirmation');
        confirmation.setAttribute('role', 'group');
        confirmation.setAttribute('aria-label', '确认放弃作文练习');
        confirmation.appendChild(node('strong', '确认放弃这次练习？'));
        note('放弃后不再出现在“我的练习”中，可以重新开始；已保存内容和版本会保留。', confirmation);
        const status = node('p', '', 'writing-abandon-status'); status.setAttribute('role', 'status');
        const actions = node('div', '', 'writing-abandon-confirm-actions');
        const cancel = button('继续保留', () => { if (!abandonBusy) confirmation.remove(); }, { light: true });
        const confirm = button('确认放弃', () => abandonAttempt(item, confirmation, status, listContext));
        confirm.classList.add('writing-abandon-confirm');
        actions.append(cancel, confirm); confirmation.append(status, actions); card.appendChild(confirmation);
        cancel.focus?.({ preventScroll: true });
    }
    async function abandonAttempt(item, confirmation, status, listContext) {
        if (abandonBusy || navigationBusy || session().account?.is_parent || !current(listContext, false) || item.status !== 'in_progress') return;
        const ctx = capture(); const activeTarget = attempt?.id === item.id;
        if (activeTarget && (localRecovery || conflict)) {
            status.textContent = '这台设备上还有需要对照的草稿。请先继续练习并处理草稿，再决定放弃。';
            return;
        }
        abandonBusy = true; viewRequest += 1; clearTimeout(timer); timer = null;
        freezeWritingHome(true); status.textContent = '正在保存并放弃…';
        let succeeded = false;
        try {
            if (activeTarget && editable()) {
                await flush();
                while (current(ctx) && editable() && sequence !== savedSequence) await flush();
            }
            if (!current(ctx, false)) return;
            const revision = activeTarget ? attempt.revision : item.revision;
            const result = await request(`/attempts/${encodeURIComponent(item.id)}/abandon`, apiOptions({ base_revision: revision }));
            if (!current(ctx, false)) return;
            if (!result.attempt || result.attempt.id !== item.id || result.attempt.status !== 'abandoned') throw new Error('放弃结果未确认，请重试。');
            if (activeTarget) {
                attempt = { ...result.attempt, data };
                readOnly = true; localRecovery = null; sequence = savedSequence;
                versions = Array.isArray(result.versions) ? result.versions : versions;
            }
            removeBackup({ ...ctx, attemptId: item.id });
            succeeded = true;
            abandonBusy = false; freezeWritingHome(false);
            await open();
            if (current(ctx, false)) message('已放弃本次练习，已保存内容和版本会保留。可以开始一次新练习。');
        } catch (error) {
            if (!current(ctx, false)) return;
            abandonBusy = false; freezeWritingHome(false);
            if (error.status === 409) {
                await open();
                if (current(ctx, false)) message('练习内容或状态已有更新，未自动放弃。请核对最新内容后再决定。', true);
            } else {
                status.textContent = error.message || '暂时未能放弃，练习仍然保留，可以重试。';
            }
        } finally {
            if (current(ctx, false)) {
                abandonBusy = false;
                if (!succeeded && confirmation.parentElement) freezeWritingHome(false);
            }
        }
    }

    async function exit() {
        if (navigationBusy || abandonBusy) return;
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
        guidedView = 'question'; guidedStep = 0; guidedFullExample = false; editingEnding = false; guidanceConfirming = false; transitionBusy = false; abandonBusy = false; lessonFlows.clear();
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
        if (state.guidance) {
            renderGuidanceSummary(parent, state, '这版的主旨与提纲');
        } else {
            parent.appendChild(node('h4', '选材与想法'));
            for (const [key, title] of [['situation', '写谁，什么时候，玩什么'], ['main_idea', '想表达的意思'], ['materials', '准备使用的材料'], ['ending', '准备怎样结尾']]) {
                if (state.planning[key]) note(`${title}：${state.planning[key]}`, parent);
            }
            parent.appendChild(node('h4', '提纲'));
            state.outline.forEach((card, index) => note(`${index + 1}${card.focus ? ' ★' : ''}  ${card.content || '（未填写）'}${card.purpose ? '\n作用：' + card.purpose : ''}`, parent));
        }
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
        if (!attempt || navigationBusy || abandonBusy) return;
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
        if (!editable() || !selectedVersion || navigationBusy || abandonBusy) return;
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
        const guidanceText = draft.guidance ? guidanceExport(draft) : '';
        const text = [lesson?.title, guidanceText, `练习方式：${draft.mode === 'personal' ? '自己的真实经历' : '给定材料练习'}`,
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

    function isCoached(state = data) { return isGuided() && lesson?.coaching?.version === 1 && state?.coaching?.version === 1; }
    function coachingMaterialReady(state = data) {
        const value = state.coaching?.material;
        return !!value && ['personal', 'scenario'].includes(value.source)
            && ['activity', 'moment', 'feeling'].every((key) => value[key].trim());
    }
    function coachingMaterialEdited() { guidedEdited(0); changed(); }
    function renderCoachingMaterial(parent) {
        const value = data.coaching.material, config = lesson.coaching.material;
        const keys = ['source', 'activity', 'moment', 'feeling'];
        const names = materialLabels();
        const card = node('section', '', 'writing-coaching-card'); card.id = 'writing-material-question';
        if (value.step === 4) {
            card.appendChild(node('h4', '我的素材关键词'));
            for (const key of keys) {
                const summary = key === 'source' ? (config.sources.find((item) => item.id === value.source)?.label || '还没有选择') : value[key];
                const row = node('div', '', 'writing-material-recap-row'); row.appendChild(node('p', `${names[key]}：${summary || '还可以再想想'}`));
                const edit = button('回看', () => { value.step = keys.indexOf(key); if (editable()) changed(); renderStage(); }, { light: true, small: true });
                edit.id = 'writing-material-edit-' + key; edit.setAttribute('aria-label', '回看' + names[key]); row.appendChild(edit); card.appendChild(row);
            }
            note(value.source === 'personal' ? '下面可以选一个方向，也可以自己说。只用符合真实经历的材料，不必编出没有发生过的细节。'
                : '这是给定情境练习。下面可以选方向，也可以组合关键词说出自己的主旨，不必把情境说成亲身经历。', card);
            parent.appendChild(card); return true;
        }
        const step = Math.max(0, Math.min(3, value.step)), key = keys[step];
        const question = config.steps.find((item) => item.id === key) || {};
        card.appendChild(node('span', `素材回想 ${step + 1} / 4`, 'writing-coaching-caption'));
        card.appendChild(node('h4', question.question || names[key]));
        if (question.help) note(question.help, card);
        if (key === 'source') {
            const options = node('div', '', 'writing-guided-options');
            for (const item of config.sources) {
                const choice = guidedChoice(options, item.id, item.label, value.source === item.id, () => {
                    if (value.source !== item.id) { value.source = item.id; data.mode = item.id === 'personal' ? 'personal' : 'material'; coachingMaterialEdited(); }
                    renderStage();
                }, item.help || '', 'material-source');
                choice.id = 'writing-material-source-' + item.id;
            }
            card.appendChild(options);
        } else {
            note(value.source === 'personal' ? '这些只是回忆方向，符合你的经历才选；也可以完全用自己的话。'
                : '下面是给定情境的关键词，可选几项再补自己的话。', card);
            const suggestions = config.suggestions?.[value.source]?.[key] || [];
            const chips = node('div', '', 'writing-material-suggestions');
            for (const [index, text] of suggestions.entries()) {
                const choice = button('＋ ' + text, () => {
                    if (!editable() || value[key].includes(text)) return;
                    const combined = [value[key].trim(), text].filter(Boolean).join('，');
                    if (combined.length > 2000) return message('素材关键词已经比较长，请先自己整理一下，原有文字都保留着。', true);
                    value[key] = combined; coachingMaterialEdited(); renderStage();
                }, { light: true, small: true, disabled: !editable() });
                choice.id = `writing-material-suggestion-${key}-${index}`; choice.dataset.value = text; chips.appendChild(choice);
            }
            card.appendChild(chips);
            field(card, '我的关键词或短句', value[key], (text) => {
                value[key] = text; guidedEdited(0);
                const next = el('writing-material-next'); if (next) next.disabled = !text.trim();
            }, {
                id: 'writing-material-' + key, rows: 2, maxLength: 2000, placeholder: '可以选提示再补充，也可以直接写。',
                help: '只记录素材，不会替你填写主旨或正文。' });
        }
        const nav = node('div', '', 'writing-material-navigation');
        const previous = button('上一小问', () => { value.step = Math.max(0, step - 1); if (editable()) changed(); renderStage(); }, { light: true, disabled: step === 0 });
        previous.id = 'writing-material-prev'; nav.appendChild(previous);
        const next = button(step === 3 ? '素材想好了，去定主旨' : '记下了，下一小问', () => {
            if (!editable()) { value.step = Math.min(4, step + 1); renderStage(); return; }
            if (key === 'source' ? !['personal', 'scenario'].includes(value.source) : !value[key].trim()) {
                message('先选一个方向或记下几个关键词，再继续。', true); return;
            }
            value.step = Math.min(4, step + 1); changed(); message(); renderStage();
        }, { disabled: editable() && (key === 'source' ? !value.source : !value[key].trim()) });
        next.id = 'writing-material-next'; nav.appendChild(next); card.appendChild(nav); parent.appendChild(card);
        return false;
    }
    function renderCoachingComparison(parent) {
        const config = lesson.coaching.comparison, value = data.coaching.comparison;
        const card = node('section', '', 'writing-coaching-card'); card.id = 'writing-comparison';
        card.appendChild(node('h4', config.title || '先看看哪些细节有帮助'));
        if (data.coaching.material.source === 'personal') note(teachingText('comparison_notice', '下面是猜词情境的写法例子，不是你的经历。可以只借鉴写法，再回到自己的游戏写句子。'), card);
        if (config.description) note(config.description, card);
        if (config.plain) card.appendChild(node('p', config.plain, 'writing-comparison-plain'));
        note(config.question || '哪些短句让你看见具体动作、对话或反应？可以多选，没有唯一答案。', card);
        const options = node('div', '', 'writing-guided-options');
        for (const item of config.options || []) {
            const choice = guidedChoice(options, item.id, item.text, value.choices.includes(item.id), () => {
                value.choices = value.choices.includes(item.id) ? value.choices.filter((id) => id !== item.id) : [...value.choices, item.id];
                guidedEdited(5); changed(); renderStage();
            }, item.detail || '', 'coaching-comparison');
            choice.id = 'writing-comparison-' + item.id;
        }
        card.appendChild(options);
        field(card, '我注意到的作用或理由（选填）', value.reason, (text) => { value.reason = text; guidedEdited(5); }, {
            id: 'writing-comparison-reason', rows: 2, help: config.prompt || '可以只说一点。参考不会自动写进你的句子。' });
        const help = node('details', '', 'writing-hint'); help.appendChild(node('summary', '需要时再看：这些细节各帮了什么忙？'));
        for (const item of config.options || []) if (item.why) note(`${item.detail || '细节'}：${item.why}`, help);
        card.appendChild(help); parent.appendChild(card);
    }
    function coachingArticle(map) {
        const ordered = data.outline.map((card) => card.id), extra = Object.keys(map || {}).filter((id) => !ordered.includes(id)).sort();
        return [...ordered, ...extra].map((id) => map?.[id] || '').filter((text) => text.trim()).join('\n\n');
    }
    function coachingEvidenceIssue(round) {
        if (!isCoached() || data.no_change[round]) return '';
        const evidence = data.coaching.evidence[round];
        if (!evidence.before.trim()) return '请从本轮之前的文章摘出一处原句或片段。';
        if (!evidence.after.trim()) return '请记下这一处改后的真实文字。';
        if (!evidence.reason.trim()) return '请用一句话说说这处修改为什么有帮助。';
        const before = coachingArticle(round === 'structure' ? data.paragraphs : data.structure_paragraphs);
        const after = coachingArticle(round === 'structure' ? data.structure_paragraphs : data.final_paragraphs);
        if (!before.includes(evidence.before.trim())) return '原句还未在本轮之前的文章中找到，请先核对或从原稿摘取真实文字。';
        if (!after.includes(evidence.after.trim())) return '改句还未在本轮正文中找到，请先核对正文；证据卡不会自动替换文章。';
        return '';
    }
    function updateCoachingEvidence() {
        if (!isCoached() || viewStage !== 4) return;
        const round = data.review_round, previous = el('writing-evidence-previous-text'), currentText = el('writing-evidence-current-text');
        if (previous) previous.textContent = coachingArticle(round === 'structure' ? data.paragraphs : data.structure_paragraphs) || '还没有文字。';
        if (currentText) currentText.textContent = coachingArticle(round === 'structure' ? data.structure_paragraphs : data.final_paragraphs) || '还没有文字。';
        const status = el('writing-evidence-status');
        if (status) status.textContent = data.no_change[round] ? '本轮不用修改，原有文字与记录都保留。'
            : coachingEvidenceIssue(round) || '已能在两份文章中找到这处修改，请再读一遍它是否表达了你的意思。';
    }
    function renderCoachingEvidence(parent, round) {
        const config = lesson.coaching.evidence, value = data.coaching.evidence[round];
        const card = node('section', '', 'writing-coaching-card'); card.id = 'writing-evidence'; card.dataset.round = round;
        card.appendChild(node('h4', config.title || '留下这一处修改'));
        note(config.rounds?.[round]?.question || config.description || '', card);
        check(card, '本轮检查后不用修改', data.no_change[round], (checked) => { data.no_change[round] = checked; renderStage(); }, 'writing-no-change');
        const article = node('details', '', 'writing-evidence-articles'); article.appendChild(node('summary', '对照本轮之前与当前的文章，摘取真实文字'));
        const grid = node('div', '', 'writing-compare');
        for (const [id, title] of [['previous', '本轮之前'], ['current', '当前正文']]) {
            const section = node('section'); section.appendChild(node('h4', title)); const text = node('pre'); text.id = `writing-evidence-${id}-text`; section.appendChild(text); grid.appendChild(section);
        }
        article.appendChild(grid); card.appendChild(article);
        if (!data.no_change[round]) {
            note(config.rounds?.[round]?.help || '这里记录一处真实修改，不会自动替换正文。', card);
            field(card, '改前：从本轮之前的文章摘一句或一个片段', value.before, (text) => { value.before = text; updateCoachingEvidence(); }, { id: 'writing-evidence-before', rows: 2, maxLength: 6000 });
            field(card, '改后：对应的当前正文文字', value.after, (text) => { value.after = text; updateCoachingEvidence(); }, { id: 'writing-evidence-after', rows: 2, maxLength: 6000,
                help: '这张卡和全文分别保存。请回上方正文手动修改，再核对这里的文字。' });
            field(card, '为什么这样改？', value.reason, (text) => { value.reason = text; data.revision_notes[round] = text; updateCoachingEvidence(); }, { id: 'writing-evidence-reason', rows: 2,
                help: '这一处说明同时记作本轮修改说明，不需要再写一遍。' });
        } else note('已经清楚的地方可以保留，不用为了留下记录而强改。以前写过的修改卡也不会删除。', card);
        const status = node('p', '', 'writing-evidence-status'); status.id = 'writing-evidence-status'; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); card.appendChild(status);
        parent.appendChild(card); updateCoachingEvidence();
    }
    function renderMaterialContext(parent) {
        if (!isCoached()) return;
        const value = data.coaching.material;
        const card = node('div', '', 'writing-material-context'); card.id = 'writing-material-context';
        card.appendChild(node('strong', value.source === 'personal' ? '我自己的素材' : '我用的练习情境'));
        note([value.activity, value.moment, value.feeling].filter((text) => text.trim()).join('；') || '还可以回主旨页补充素材。', card);
        if (value.source === 'personal') note(teachingText('personal_notice', '猜词、兔子和青蛙卡片只是写法例子，可以不选。请用自己实际记得的材料，不必编出不存在的细节。'), card);
        parent.appendChild(card);
    }
    function coachingExport(state) {
        if (!isCoached(state)) return '';
        const value = state.coaching;
        const source = lesson.coaching.material.sources.find((item) => item.id === value.material.source)?.label || value.material.source;
        const labels = materialLabels();
        return ['【素材回想】', `${labels.source}：${source}`, `${labels.activity}：${value.material.activity}`, `${labels.moment}：${value.material.moment}`, `${labels.feeling}：${value.material.feeling}`,
            '【短例比较：只作写法参考】', ...value.comparison.choices.map((id) => lesson.coaching.comparison.options.find((item) => item.id === id)?.text || id), value.comparison.reason,
            ...['structure', 'language'].flatMap((round) => [`【${round === 'structure' ? '内容与顺序' : '表达与字词'}修改证据】`,
                state.no_change[round] ? '本轮检查后不用修改' : '', `改前：${value.evidence[round].before}`, `改后：${value.evidence[round].after}`, `理由：${value.evidence[round].reason}`])].join('\n');
    }
    function renderCoachingSummary(parent, state) {
        if (!isCoached(state)) return;
        const text = node('div', coachingExport(state), 'writing-coaching-summary'); parent.appendChild(text);
    }

    const GUIDED_TITLES = ['主旨立意', '文章结构', '安排开头', '中间重点', '安排结尾', '细节与句子', '写重点段', '连成全文', '点题回看'];
    const GUIDED_DEPENDENCIES = { 0: [1, 2, 3, 4, 5, 6, 7, 8], 1: [2, 3, 4, 6, 7, 8], 2: [7, 8], 3: [5, 6, 7, 8], 4: [7, 8], 5: [6, 7, 8], 6: [7, 8], 7: [8], 8: [] };
    function isGuided() { return !!lesson?.guidance && lesson.guidance.version === 1 && !!data?.guidance; }
    function guidedConfig() { return lesson.guidance; }
    function preferredGuidedExample() {
        const examples = guidedConfig().examples || [];
        return examples.find((item) => item.id === guidance().structure)?.id || examples[0]?.id || 'total';
    }
    function guidance() { return data.guidance; }
    function guidedStepTitle(step) { return GUIDED_TITLES[step] || '主旨立意'; }
    function guidedTheme(state = data) {
        const value = state.guidance;
        return value.customTheme.trim() || guidedConfig().themes.find((item) => item.id === value.theme)?.text || '';
    }
    function guidedStructure(state = data) {
        return guidedConfig().structures.find((item) => item.id === state.guidance.structure)
            || { name: '还没有选择结构', roles: { begin: '先安排开头要写什么', middle: '找出准备展开的重点', end: '想想怎样留下感受' } };
    }
    function guidedFrame(key, state = data) {
        return [...state.guidance.slots[key].map((id) => guidedConfig().options[key].find((item) => item.id === id)?.text).filter(Boolean),
            ...(state.guidance.notes[key].trim() ? [state.guidance.notes[key].trim()] : [])];
    }
    function guidedDetails(state = data) {
        return [...new Set([...state.guidance.details.map((id) => guidedConfig().details.find((item) => item.id === id)?.text).filter(Boolean),
            ...(isCoached(state) ? state.coaching.comparison.choices.map((id) => lesson.coaching.comparison.options.find((item) => item.id === id)?.text).filter(Boolean) : []),
            ...(state.guidance.ownDetail.trim() ? [state.guidance.ownDetail.trim()] : [])])];
    }
    function guidedStageFor(step) { return step === 0 ? 0 : step <= 4 ? 1 : step <= 6 ? 2 : 3; }
    function guidedValid(step, state = data) {
        if (!state.guidance) return false;
        if (step === 0) return !!guidedTheme(state) && (!isCoached(state) || coachingMaterialReady(state));
        if (step === 1) return guidedConfig().structures.some((item) => item.id === state.guidance.structure);
        if (step >= 2 && step <= 4) return guidedFrame(({ 2: 'begin', 3: 'middle', 4: 'end' })[step], state).length > 0;
        if (step === 5) return !!state.guidance.sentence.trim() && (!isCoached(state) || state.coaching.comparison.choices.some((id) => lesson.coaching.comparison.options.some((item) => item.id === id)));
        if (step === 6) return !!state.guidance.paragraph.trim();
        if (step === 7) return !!state.paragraphs.p_full?.trim();
        return state.guidance.endingOkay;
    }
    function guidedPending(before = 9) {
        const value = guidance();
        for (let index = 0; index < before; index += 1) {
            if (!value.confirmed.includes(index) || value.review.includes(index) || !guidedValid(index)) return index;
        }
        return null;
    }
    function guidedStatus(index) {
        if (guidance().review.includes(index)) return ['review', '待复核', '↻'];
        if (guidance().confirmed.includes(index) && guidedValid(index)) return ['done', '已确认', '✓'];
        return guidedSummary(index).trim() ? ['filled', '已填写，待确认', '•'] : ['empty', '未开始', '○'];
    }
    function guidedSummary(index, state = data) {
        if (index === 0) return [isCoached(state) ? [lesson.coaching.material.sources.find((item) => item.id === state.coaching.material.source)?.label,
            state.coaching.material.activity, state.coaching.material.moment, state.coaching.material.feeling].filter(Boolean).join('；') : '', guidedTheme(state)].filter(Boolean).join('\n');
        if (index === 1) return state.guidance.structure ? guidedStructure(state).name : '';
        if (index >= 2 && index <= 4) return guidedFrame(({ 2: 'begin', 3: 'middle', 4: 'end' })[index], state).join('；');
        if (index === 5) return [state.guidance.sentence || guidedDetails(state).join('、'), isCoached(state) && state.coaching.comparison.choices.length
            ? '已比较：' + state.coaching.comparison.choices.map((id) => lesson.coaching.comparison.options.find((item) => item.id === id)?.detail || id).join('、') : ''].filter(Boolean).join('\n');
        if (index === 6) return state.guidance.paragraph;
        if (index === 7) return state.paragraphs.p_full || '';
        return state.guidance.endingOkay ? '结尾已经回到主旨，或照应前文' : '';
    }
    function guidedEdited(step) {
        const affected = [step, ...(GUIDED_DEPENDENCIES[step] || [])];
        guidance().review = [...new Set([...guidance().review, ...guidance().confirmed.filter((item) => affected.includes(item))])];
        if (step < 8) guidance().endingOkay = false;
        renderGuidedSteps(); updateGuidedFooter();
    }
    function updateGuidedFooter() {
        if (!isGuided() || viewStage > 3) return;
        const help = guidedView !== 'question';
        el('writing-next').hidden = false;
        el('writing-next').disabled = navigationBusy || guidanceConfirming || !!localRecovery || (!readOnly && !help && !guidedValid(guidedStep));
        el('writing-next').textContent = help ? '返回当前问题' : readOnly ? '查看下一步'
            : editingEnding && guidedStep === 7 ? '结尾改好了，回看点题'
                : guidedConfig().steps[guidedStep]?.confirm_label || '确认这一题，继续';
        el('writing-prev').disabled = navigationBusy || guidanceConfirming || (!help && guidedStep === 0);
        el('writing-prev').textContent = help ? '返回当前问题' : '上一步';
        el('writing-complete').hidden = true;
        el('writing-save-exit').disabled = navigationBusy || guidanceConfirming;
        el('writing-save-exit').textContent = writeAllowed() ? '保存并退出' : '返回练习列表';
    }
    function renderGuidedSteps() {
        const container = el('writing-steps'); container.replaceChildren();
        container.classList.add('writing-guided-steps');
        GUIDED_TITLES.forEach((title, index) => {
            const [kind, status, mark] = guidedStatus(index);
            const step = node('button', '', 'writing-step writing-guided-step');
            step.type = 'button'; step.id = 'writing-guided-step-' + index;
            step.disabled = navigationBusy || guidanceConfirming;
            step.setAttribute('data-status', kind);
            step.setAttribute('aria-label', `切换到第${index + 1}步${title}，${status}`);
            if (viewStage <= 3 && guidedStep === index) step.setAttribute('aria-current', 'step');
            step.append(node('span', String(index + 1), 'writing-guided-step-number'), node('span', title), node('span', mark, 'writing-guided-step-status'));
            step.addEventListener('click', () => openGuidedStep(index)); container.appendChild(step);
        });
        const after = node('div', '', 'writing-after-guidance');
        for (const [mode, title] of [['structure', '内容修改'], ['language', '表达修改'], ['reflect', '回看完成']]) {
            const available = readOnly || (guidedPending() === null && data.structure_started
                && (mode === 'structure' || data.language_started)
                && (mode !== 'reflect' || !!data.final_paragraphs.p_full?.trim()));
            const target = button(title, () => openGuidedRevision(mode), { light: true, disabled: navigationBusy || guidanceConfirming || !available });
            target.id = 'writing-guided-review-' + mode;
            if ((mode === 'reflect' && viewStage === 5) || (viewStage === 4 && data.review_round === mode)) target.setAttribute('aria-current', 'step');
            after.appendChild(target);
        }
        container.appendChild(after);
        const progress = node('progress'); progress.id = 'writing-guided-progress'; progress.max = 9;
        progress.value = guidance().confirmed.filter((index) => !guidance().review.includes(index) && guidedValid(index)).length;
        progress.setAttribute('aria-label', '已确认的初稿问题');
        container.appendChild(progress);
    }
    function openGuidedStep(index) {
        if (!isGuided() || navigationBusy || guidanceConfirming || abandonBusy || index < 0 || index > 8) return;
        guidedStep = index; guidedView = 'question'; guidedFullExample = false; guidedHelpExamples = false; editingEnding = false;
        viewStage = guidedStageFor(index);
        if (editable()) { guidance().step = index; data.stage = viewStage; changed(); }
        renderStage();
    }
    function openGuidedRevision(mode) {
        if (!isGuided() || navigationBusy || guidanceConfirming || abandonBusy) return;
        if (editable()) {
            const pending = guidedPending();
            if (pending !== null) { openGuidedStep(pending); message(`还需要确认“${GUIDED_TITLES[pending]}”，已经写的文字都会保留。`, true); return; }
        }
        viewStage = mode === 'reflect' ? 5 : 4;
        if (mode !== 'reflect') data.review_round = mode;
        guidedView = 'question';
        if (editable()) { data.stage = viewStage; changed(); }
        renderStage();
    }
    function guidedChoice(parent, id, title, selected, handler, help = '', prefix = 'choice') {
        const choice = node('button', '', 'writing-guided-option');
        choice.type = 'button'; choice.id = `writing-guided-${prefix}-${id}`;
        choice.disabled = !editable() || navigationBusy || guidanceConfirming || completing || transitionBusy || abandonBusy;
        choice.setAttribute('aria-pressed', String(!!selected));
        choice.appendChild(node('span', selected ? '✓' : '○', 'writing-guided-choice-mark'));
        const description = node('span', '', 'writing-guided-option-text'); description.appendChild(node('span', title));
        if (help) description.appendChild(node('small', help));
        choice.appendChild(description);
        choice.addEventListener('click', () => {
            if (!editable() || navigationBusy || guidanceConfirming || completing || transitionBusy || abandonBusy) return;
            handler();
            el(choice.id)?.focus?.({ preventScroll: true });
        });
        parent.appendChild(choice); return choice;
    }
    function addGuidedHelpLink(parent, title) {
        const link = button(title || '需要一点帮助？', () => {
            guidedView = 'help'; guidedFullExample = false; guidedHelpExamples = false;
            guidedExample = guidedStep === 7 ? 'outline' : preferredGuidedExample();
            renderStage();
        }, { light: true });
        link.classList.add('writing-guided-help-link'); link.id = 'writing-guided-help-open'; parent.appendChild(link);
    }
    function guidedTextField(parent, label, key, options = {}) {
        const value = options.get ? options.get() : guidance()[key];
        return field(parent, label, value, (text) => {
            const previousTheme = key === 'customTheme' ? guidedTheme() : null;
            if (options.set) options.set(text); else guidance()[key] = text;
            if (key !== 'customTheme' || guidedTheme() !== previousTheme) guidedEdited(options.step ?? guidedStep);
            if (key === 'customTheme' && el('writing-guided-adopted-theme')) el('writing-guided-adopted-theme').textContent = guidedTheme() || '还没有确定';
        }, { id: 'writing-guided-' + key, rows: options.rows || 3, single: !!options.single,
            maxLength: options.maximum || 2000, placeholder: options.placeholder || '', help: options.help || '' });
    }
    function guidedFrameTabs(parent) {
        const tabs = node('div', '', 'writing-guided-frame-tabs');
        [['begin', '开头', 2], ['middle', '中间', 3], ['end', '结尾', 4]].forEach(([part, title, step]) => {
            const tab = button(title, () => openGuidedStep(step), { light: true, disabled: navigationBusy || guidanceConfirming });
            tab.id = 'writing-guided-frame-' + part;
            if (guidedStep === step) tab.setAttribute('aria-current', 'step');
            tabs.appendChild(tab);
        });
        parent.appendChild(tabs);
    }
    function renderGuidedQuestion(parent) {
        const value = guidance(); const config = guidedConfig();
        if (isCoached() && guidedStep > 0) renderMaterialContext(parent);
        if (guidedStep === 0) {
            if (isCoached() && !renderCoachingMaterial(parent)) return;
            const options = node('div', '', 'writing-guided-options');
            for (const theme of config.themes) guidedChoice(options, theme.id, theme.text, value.theme === theme.id, () => {
                const previous = guidedTheme(); value.theme = theme.id;
                if (guidedTheme() !== previous) guidedEdited(0);
                changed(); renderStage();
                if (value.customTheme.trim()) message('参考方向已切换。你写的主旨已保留，当前仍采用自己的说法。');
            }, '', 'theme');
            parent.appendChild(options);
            guidedTextField(parent, '改写成我自己的主旨（可选）', 'customTheme', { rows: 2,
                placeholder: teachingText('theme_placeholder', '用一句话说出，这次游戏给你什么感受。'), help: '填写后采用自己的这句话；不填写时采用上面选择的方向。' });
            const adopted = node('p', '当前采用：', 'writing-guided-context');
            const text = node('strong', guidedTheme() || '还没有确定'); text.id = 'writing-guided-adopted-theme'; adopted.appendChild(text); parent.appendChild(adopted);
            addGuidedHelpLink(parent, '不太明白“主旨”？');
        } else if (guidedStep === 1) {
            const options = node('div', '', 'writing-guided-options');
            for (const structure of config.structures) guidedChoice(options, structure.id, structure.name, value.structure === structure.id, () => {
                if (value.structure !== structure.id) { value.structure = structure.id; guidedEdited(1); changed(); }
                renderStage();
            }, structure.short, 'structure');
            parent.appendChild(options); addGuidedHelpLink(parent, '看看这几种结构的区别');
        } else if (guidedStep >= 2 && guidedStep <= 4) {
            const part = ({ 2: 'begin', 3: 'middle', 4: 'end' })[guidedStep];
            const names = { begin: '开头', middle: '中间', end: '结尾' };
            guidedFrameTabs(parent);
            const frame = node('section', '', 'writing-guided-current-frame');
            frame.setAttribute('aria-label', '当前' + names[part] + '框');
            frame.appendChild(node('h4', names[part] + ' · 选项和自己的话放在一起'));
            note(guidedStructure().roles[part], frame);
            const options = node('div', '', 'writing-guided-options');
            for (const item of config.options[part]) guidedChoice(options, item.id, item.text, value.slots[part].includes(item.id), () => {
                value.slots[part] = part === 'middle' ? value.slots[part].includes(item.id) ? [] : [item.id]
                    : value.slots[part].includes(item.id) ? value.slots[part].filter((id) => id !== item.id) : [...value.slots[part], item.id];
                guidedEdited(guidedStep); changed(); renderStage();
            }, '', 'material');
            frame.appendChild(options);
            guidedTextField(frame, `给${names[part]}补充自己的材料（可选）`, 'note-' + part, {
                rows: 2, get: () => value.notes[part], set: (text) => { value.notes[part] = text; },
                help: '选项和自己写的材料可以并用，也可以不选卡片、只写自己的想法。这里先写关键词或短句。' });
            parent.appendChild(frame); addGuidedHelpLink(parent, '看看这一部分怎样写');
        } else if (guidedStep === 5) {
            if (isCoached()) renderCoachingComparison(parent);
            const options = node('div', '', 'writing-guided-options');
            for (const item of config.details) guidedChoice(options, item.id, item.text, value.details.includes(item.id), () => {
                value.details = value.details.includes(item.id) ? value.details.filter((id) => id !== item.id) : [...value.details, item.id];
                guidedEdited(5); changed(); renderStage();
                if (value.sentence.trim()) message('参考细节已调整，你写的句子保留着，可以按需要自己修改。');
            }, item.kind, 'detail');
            if (isCoached()) {
                const extra = node('details', '', 'writing-hint'); extra.id = 'writing-coaching-detail-keywords';
                extra.appendChild(node('summary', '需要时再选细节关键词')); extra.appendChild(options); parent.appendChild(extra);
            } else parent.appendChild(options);
            guidedTextField(parent, '再加一个自己的细节关键词（可选）', 'ownDetail', { single: true, placeholder: '例如：皱着眉、急得直摆手。' });
            guidedTextField(parent, '把有用的细节写成一句自己的话', 'sentence', { maximum: 6000,
                help: '可以组合参考细节，也可以完全自己写。改选卡片不会替你改写已经输入的句子。' });
            addGuidedHelpLink(parent, '不知道怎样从细节写成句子？');
        } else if (guidedStep === 6) {
            const summary = node('div', '', 'writing-guided-related');
            note('我选的重点：' + (guidedFrame('middle').join('、') || '可以先写，再回“中间”补充。'), summary);
            note('可用细节：' + (guidedDetails().join('、') || '用自己记得的动作、对话或反应。'), summary); parent.appendChild(summary);
            guidedTextField(parent, '围绕这个重点写我的段落', 'paragraph', { rows: 7, maximum: 6000,
                help: '先展开这一刻，开头和结尾稍后再接。' });
            if (value.sentence.trim() && editable()) parent.appendChild(button('把我写的这句话接进来', () => insertOwnGuidedText('sentence', 'paragraph'), { light: true }));
            addGuidedHelpLink(parent, '看看范文怎样展开这一段');
        } else if (guidedStep === 7) {
            const theme = node('p', '我的主旨：', 'writing-guided-context'); theme.appendChild(node('strong', guidedTheme() || '还没有确定，可以点“主旨”回去补充。')); parent.appendChild(theme);
            guidedTextField(parent, '我的整篇初稿', 'draft', { rows: 10, maximum: 6000,
                get: () => data.paragraphs.p_full || '', set: (text) => { data.paragraphs.p_full = text; },
                help: editingEnding ? '其他文字保留着，可以只调整结尾，再回看点题。' : '把自己的重点段和前后内容连起来，不必把每张材料卡都写进文章。' });
            if (value.paragraph.trim() && editable() && !editingEnding) parent.appendChild(button('把写好的重点段接进来', () => insertOwnGuidedText('paragraph', 'draft'), { light: true }));
            addGuidedHelpLink(parent, editingEnding ? '看看点题与照应的方法' : '看看我的提纲或范文');
        } else {
            const theme = node('p', '我的主旨：', 'writing-guided-context'); theme.appendChild(node('strong', guidedTheme() || '还没有确定')); parent.appendChild(theme);
            note('我的初稿末尾', parent);
            const draft = (data.paragraphs.p_full || '').trim();
            parent.appendChild(node('div', draft ? (draft.length > 160 ? '…' : '') + draft.slice(-160) : '还没有初稿。可以先点“全文”补写。', 'writing-guided-reading'));
            const options = node('div', '', 'writing-guided-options');
            guidedChoice(options, 'okay', '结尾已经点题，或照应了前文', value.endingOkay, () => {
                value.endingOkay = !value.endingOkay; guidedEdited(8); changed(); renderStage();
            }, '', 'ending');
            options.appendChild(button('我想再改一改结尾', () => {
                if (editable()) { value.endingOkay = false; changed(); }
                openGuidedStep(7); editingEnding = true; renderStage();
            }, { light: true, disabled: navigationBusy }));
            parent.appendChild(options); addGuidedHelpLink(parent, '看看点题与照应的方法');
        }
    }
    function insertOwnGuidedText(from, to) {
        if (!editable() || guidanceConfirming || navigationBusy) return;
        const source = guidance()[from];
        const previous = to === 'draft' ? data.paragraphs.p_full || '' : guidance()[to];
        if (!source.trim() || previous.includes(source)) return;
        const combined = [previous, source].filter((text) => text.trim()).join(to === 'draft' ? '\n\n' : '\n');
        if (combined.length > 6000) return message('已有文字已保留。可以先自己调整，再接入这段内容。', true);
        if (to === 'draft') data.paragraphs.p_full = combined; else guidance()[to] = combined;
        guidedEdited(to === 'draft' ? 7 : 6); changed(); renderStage();
    }
    function renderGuidanceSummary(parent, state = data, title = '已经定下的安排') {
        parent.appendChild(node('h4', title));
        note('主旨：' + guidedTheme(state), parent);
        note('结构：' + guidedStructure(state).name, parent);
        for (const [part, name] of [['begin', '开头'], ['middle', '中间'], ['end', '结尾']]) {
            note(name + '：' + (guidedFrame(part, state).join('；') || '还没有安排'), parent);
        }
        if (state.guidance.sentence) { parent.appendChild(node('h4', '我写的句子')); note(state.guidance.sentence, parent); }
        if (state.guidance.paragraph) { parent.appendChild(node('h4', '我的重点段')); note(state.guidance.paragraph, parent); }
        renderCoachingSummary(parent, state);
    }
    function guidanceExport(state) {
        return ['【主旨与结构】', guidedTheme(state), guidedStructure(state).name,
            ...[['begin', '开头'], ['middle', '中间'], ['end', '结尾']].map(([key, name]) => `${name}：${guidedFrame(key, state).join('；')}`),
            '【细节与句子】', guidedDetails(state).join('、'), state.guidance.sentence, '【重点段】', state.guidance.paragraph,
            '【确认情况】', state.guidance.confirmed.map((step) => guidedStepTitle(step)).join('、'),
            '需要再复核：' + state.guidance.review.map((step) => guidedStepTitle(step)).join('、'), coachingExport(state)].filter(Boolean).join('\n');
    }
    function renderGuidedSupport(parent) {
        parent.replaceChildren();
        const back = button('← 回到我的输入', () => { guidedView = 'question'; renderStage(); }, { light: true }); back.id = 'writing-guided-help-back'; parent.appendChild(back);
        if (isCoached()) renderMaterialContext(parent);
        if (guidedView === 'recap') {
            parent.appendChild(node('h3', '已经填写的内容'));
            for (let index = 0; index < 9; index += 1) {
                const summary = guidedSummary(index);
                if (!summary.trim()) continue;
                const item = button(`${GUIDED_TITLES[index]} · ${guidedStatus(index)[1]}`, () => openGuidedStep(index), { light: true });
                item.appendChild(node('small', summary)); item.classList.add('writing-guided-recap-item'); parent.appendChild(item);
            }
            return;
        }
        const method = (title, text) => { const part = node('section', '', 'writing-guided-method'); part.appendChild(node('h4', title)); note(text, part); parent.appendChild(part); };
        if (viewStage === 4) {
            method(data.review_round === 'structure' ? '先看内容和顺序' : '再看表达和字词', data.review_round === 'structure'
                ? '对照主旨和提纲，找一两处：重点有没有写清楚，顺序是否合适，结尾能否接住前文。'
                : '把句子读给自己听，检查重复是否必要、用词是否清楚，再看错别字和标点。已经清楚的句子可以保留。');
            return;
        }
        const topicHelp = lesson.teaching?.help?.[guidedStep];
        if (topicHelp && !guidedHelpExamples && !(guidedStep === 7 && guidedExample === 'outline')) {
            for (const block of topicHelp) method(block.title, block.text);
            if (guidedStep === 5) note('我选的细节：' + (guidedDetails().join('、') || '可以只用自己记得的细节。'), parent);
            if ([2, 3, 4, 6, 8].includes(guidedStep)) parent.appendChild(button('再看看这一部分的写法示例', () => {
                guidedHelpExamples = true; guidedExample = preferredGuidedExample(); guidedFullExample = false; renderStage();
            }, { light: true }));
            return;
        }
        if (!topicHelp && guidedStep === 0) {
            method('主旨，就是想让读者感受到的意思', '给定情境：课间，我和同桌玩猜词。我比画兔子，同桌猜成青蛙，我们都笑了。铃响后，游戏结束。');
            note('写的事是“课间猜词”；想表达的意思可以是“游戏很有趣”，也可以是“猜错的误会带来快乐”。自己的主旨填写后会优先采用。', parent); return;
        }
        if (!topicHelp && guidedStep === 1) {
            for (const item of guidedConfig().structures) method(item.name, item.short);
            note('开头、中间、结尾是位置；总分总、分总是内容之间的关系。总分总的中间，也可以按事情顺序写。', parent); return;
        }
        if (!topicHelp && guidedStep === 5) {
            method('把参考细节，变成自己的一句话', '可以从“我把____”或“他一边____，一边____”开始，也可以不用句架，先把一个动作或反应说清楚。');
            note('你选的细节：' + (guidedDetails().join('、') || '也可以不选卡片，直接写自己的细节。'), parent); return;
        }
        if (!topicHelp && !guidedHelpExamples && (guidedStep === 8 || (guidedStep === 7 && editingEnding))) {
            method('直接点题', '这场课间猜词游戏，真是又短又有趣。');
            method('回到一个细节', '回到座位时，我还想起他那声“青蛙”，嘴角又翘了起来。');
            method('照应开头的感受', '开头写“笑个不停”，结尾可以回到游戏留下的笑声。');
            parent.appendChild(button('再看看范文怎样收尾', () => { guidedHelpExamples = true; guidedExample = preferredGuidedExample(); renderStage(); }, { light: true }));
            return;
        }
        if (guidedStep === 7 && guidedExample === 'outline') {
            renderGuidanceSummary(parent);
            if (lesson.teaching && Array.isArray(lesson.reference_outline)) {
                const reference = node('details', '', 'writing-hint'); reference.appendChild(node('summary', '对照原提纲：一种安排'));
                for (const item of lesson.reference_outline) note(`${item.focus ? '★ ' : ''}${item.content}（${item.purpose}）`, reference);
                parent.appendChild(reference);
            }
            parent.appendChild(button('再看看范文怎么安排', () => { guidedExample = preferredGuidedExample(); guidedHelpExamples = !!lesson.teaching; guidedFullExample = false; renderStage(); }, { light: true })); return;
        }
        const examples = guidedConfig().examples || [];
        const tabs = node('div', '', 'writing-guided-example-tabs');
        for (const item of examples) {
            const tab = button(item.title, () => { guidedExample = item.id; guidedFullExample = false; renderStage(); }, { light: true });
            tab.setAttribute('aria-pressed', String(guidedExample === item.id)); tabs.appendChild(tab);
        }
        parent.appendChild(tabs);
        const selected = examples.find((item) => item.id === guidedExample) || examples[0];
        const index = guidedStep === 8 || guidedStep === 4 || (guidedStep === 7 && editingEnding) ? 2 : guidedStep === 2 ? 0 : 1;
        if (lesson.teaching?.example_note) note(lesson.teaching.example_note, parent);
        const paragraphs = selected?.paragraphs || [];
        for (const part of guidedFullExample ? paragraphs : paragraphs.slice(index, index + 1)) {
            const section = node('section', '', 'writing-guided-example'); section.appendChild(node('span', part.role));
            section.appendChild(node('p', part.text)); note('写法提示：' + part.hint, section); parent.appendChild(section);
        }
        if (!guidedFullExample) parent.appendChild(button('我想阅读全文', () => { guidedFullExample = true; renderStage(); }, { light: true }));
        else if (guidedConfig().transfer) { method(guidedConfig().transfer.title || '换一种游戏再试', guidedConfig().transfer.text); note(guidedConfig().transfer.hint || '', parent); }
    }
    function renderGuidedStage() {
        const value = guidance(); const step = guidedConfig().steps[guidedStep] || {};
        const helpAside = el('writing-hints').parentElement; if (helpAside) helpAside.hidden = true;
        el('writing-title').textContent = lesson.title;
        el('writing-stage-number').textContent = `已确认 ${value.confirmed.filter((id) => !value.review.includes(id) && guidedValid(id)).length} / 9 · 当前步骤 ${guidedStep + 1} / 9`;
        el('writing-stage-title').textContent = guidedView === 'help' ? '当前问题的帮助' : guidedView === 'recap' ? '回看已填内容'
            : editingEnding && guidedStep === 7 ? '只改结尾，让它接住你的主旨。' : step.question || GUIDED_TITLES[guidedStep];
        el('writing-stage-goal').textContent = guidedView === 'question' ? step.description || step.phase || '' : '读完后回到自己的输入，文字都会保留。';
        if (guidedView === 'question' && isCoached() && guidedStep === 0 && data.coaching.material.step < 4) {
            el('writing-stage-title').textContent = '主旨立意 · 先回想素材';
            el('writing-stage-goal').textContent = '先回想一件具体的事，一次只想一小问；素材记下后，再确定主旨。';
        }
        el('writing-read-only').hidden = !readOnly;
        el('writing-read-only').textContent = session().account?.is_parent ? '家长查看模式：可以看过程和版本，正文由学生编辑。' : attempt.status === 'abandoned' ? '这份练习已放弃，已保存内容和版本仍可查看。' : '作品已保存，可以回看每一步。';
        renderGuidedSteps();
        const main = el('writing-stage-content'); main.replaceChildren();
        if (guidedView === 'question') renderGuidedQuestion(main); else renderGuidedSupport(main);
        if (guidedView === 'question' && value.review.includes(guidedStep)) note('前面的内容有调整。这里的选择和文字已保留，确认后会取消“待复核”标记。', main, 'writing-guided-review-note');
        let recap = el('writing-guided-recap');
        if (!recap) { recap = button('已填的内容', () => { guidedView = 'recap'; renderStage(); }, { light: true }); recap.id = 'writing-guided-recap'; el('writing-history-open').parentElement.appendChild(recap); }
        recap.hidden = guidedView !== 'question' || !GUIDED_TITLES.some((_, index) => guidedSummary(index).trim());
        updateGuidedFooter();
    }
    async function confirmGuidedStep() {
        if (!attempt || navigationBusy || guidanceConfirming || abandonBusy) return;
        if (!editable()) { if (!localRecovery) openGuidedStep(Math.min(8, guidedStep + 1)); return; }
        if (!guidedValid(guidedStep)) return message('先完成当前问题，可以用选项，也可以用自己的话。', true);
        const value = guidance();
        if (guidedStep === 8) {
            const pending = guidedPending(8);
            if (pending !== null) { openGuidedStep(pending); message(`还需要确认“${GUIDED_TITLES[pending]}”。其他文字都会保留，可以用上方步骤切换。`, true); return; }
        }
        const ctx = capture(); const completedStep = guidedStep;
        navigationBusy = true; guidanceConfirming = true;
        value.confirmed = [...new Set([...value.confirmed, completedStep])];
        value.review = value.review.filter((item) => item !== completedStep);
        if (completedStep === 5 && !value.paragraph.trim()) value.paragraph = value.sentence;
        if (completedStep === 6 && !data.paragraphs.p_full?.trim()) data.paragraphs.p_full = value.paragraph;
        if (completedStep === 7 && editingEnding) { editingEnding = false; value.endingOkay = false; }
        changed(); renderStage();
        try {
            const label = completedStep === 4 ? '提纲' : completedStep === 8 ? '初稿' : '';
            if (completedStep === 8 && !data.structure_started) { data.structure_paragraphs = copy(data.paragraphs); data.structure_started = true; changed(); }
            await flush(label);
            if (!current(ctx)) return;
            if (completedStep === 8) { viewStage = 4; data.stage = 4; data.review_round = 'structure'; guidedView = 'question'; }
            else { guidedStep = completedStep + 1; value.step = guidedStep; viewStage = guidedStageFor(guidedStep); data.stage = viewStage; }
            changed(); await flush();
            if (current(ctx)) message(completedStep === 8 ? '初稿九个问题已确认。接下来先修改内容，再检查表达。' : '');
        } catch (error) { if (current(ctx)) message(error.message || '请先保存，再继续。', true); }
        finally { if (current(ctx)) { navigationBusy = false; guidanceConfirming = false; renderStage(); } }
    }

    el('writing-next').addEventListener('click', () => void nextStage());
    el('writing-prev').addEventListener('click', () => {
        if (isGuided()) {
            if (guidedView !== 'question') { guidedView = 'question'; renderStage(); return; }
            if (viewStage <= 3) { if (guidedStep > 0) openGuidedStep(guidedStep - 1); return; }
            if (viewStage === 4 && data.review_round !== 'language') { openGuidedStep(8); return; }
        }
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
        data = copy(localRecovery.data); guidedStep = data.guidance?.step || 0; guidedView = 'question'; viewStage = data.stage; furthestStage = Math.max(furthestStage, viewStage); localRecovery = null;
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
