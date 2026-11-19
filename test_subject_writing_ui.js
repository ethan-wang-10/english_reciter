const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

const html = fs.readFileSync(path.join(__dirname, 'static/subject.html'), 'utf8');
const source = fs.readFileSync(path.join(__dirname, 'static/js/subject-writing.js'), 'utf8');
const COACHED_LESSON = JSON.parse(fs.readFileSync(path.join(__dirname, 'subject_content/chinese/writing/lesson-01-game.json'), 'utf8'));
// Saved guided-v1 attempts keep their original lesson and must remain usable when
// the catalogue moves on to a newer teaching flow.
const GUIDED_LESSON = JSON.parse(JSON.stringify(COACHED_LESSON));
GUIDED_LESSON.version = 2;
delete GUIDED_LESSON.coaching;
const prefix = '/subjects/chinese/writing';
const settle = () => new Promise(setImmediate);
const clone = (value) => value === undefined ? undefined : JSON.parse(JSON.stringify(value));

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

function emptyData() {
    return {
        stage: 0, mode: 'material',
        planning: { situation: '', main_idea: '', materials: '', ending: '' },
        outline: ['p1', 'p2', 'p3'].map((id) => ({ id, content: '', purpose: '', focus: false })),
        paragraphs: {}, structure_paragraphs: {}, final_paragraphs: {},
        structure_started: false, language_started: false, review_round: 'structure',
        checks: { structure: [], language: [] }, revision_notes: { structure: '', language: '' },
        reflection: { learned: '', next_time: '' }, no_change: { structure: false, language: false },
    };
}

function completedData() {
    const data = emptyData();
    data.stage = 5;
    data.planning = { situation: '课间我和同桌玩猜词。', main_idea: '猜错的时候很有趣。', materials: '比画兔子，猜成青蛙。', ending: '上课回座位。' };
    data.outline = [
        { id: 'p1', content: '交代课间游戏', purpose: '介绍开始', focus: false },
        { id: 'p2', content: '比画和猜错', purpose: '写出有趣', focus: true },
        { id: 'p3', content: '回到座位', purpose: '照应有趣', focus: false },
    ];
    data.paragraphs = { p1: '下课了，我和同桌玩猜词游戏。', p2: '我做动作，他猜错了，真好玩。', p3: '上课铃响，我们回到座位。' };
    data.structure_paragraphs = { ...data.paragraphs, p2: '我竖起两根手指，他大喊：“青蛙！”我笑了。' };
    data.final_paragraphs = { ...data.structure_paragraphs, p3: '回到座位，我还想着他那声“青蛙”。' };
    data.structure_started = data.language_started = true;
    data.review_round = 'language';
    data.revision_notes = { structure: '补上比画的动作。', language: '删去重复评价，让结尾照应前文。' };
    data.reflection = { learned: '用动作和对话写出重点。', next_time: '试着自己挑选细节。' };
    return data;
}

function writingUI({ storage = new Map(), isParent = false, seed = [], lesson: requestedLesson } = {}) {
    const elements = new Map();
    const timers = new Map();
    let timerId = 0;
    const state = { calls: [], routes: {}, windowListeners: {}, clipboard: '', section: 'writing', attempts: new Map(), versions: new Map() };
    state.session = { token: 'alice-token', username: isParent ? 'alice_parent' : 'alice', subject: 'chinese',
        account: { username: isParent ? 'alice_parent' : 'alice', is_parent: isParent, child_username: isParent ? 'alice' : null } };
    state.lesson = {
        id: 'grade4-interesting-recess-game', version: 1, title: '一次有趣的课间游戏',
        description: '先搭提纲，再写重点，最后自己修改。', grade: 4, goal: '把一件事按顺序安排清楚', time_hint: '可以分几次完成',
        materials: ['下课铃响了', '我和同桌玩猜词游戏', '我比画兔子，同桌猜成青蛙', '上课铃响了'],
        stages: ['审题选材', '搭提纲', '写重点段', '连成初稿', '分轮修改', '回看成果'].map((title, index) => ({
            id: `stage-${index}`, title, goal: '今天只练一个小目标', question: '读者需要知道什么？',
            hints: [{ title: '先想一想', text: '写自己的关键词。' }, { title: '换个角度', text: '看看动作和对话。' }, { title: '局部示例', text: '请自己选择有用的细节。' }],
            checklist: [{ id: 'clear', label: '我能说明白' }],
        })),
        reference_outline: [{ content: '交代游戏', purpose: '告诉读者谁在玩', focus: false }, { content: '有趣的一刻', purpose: '展开重点', focus: true }],
    };
    if (requestedLesson) state.lesson = clone(requestedLesson);

    class Element {
        constructor(tag = 'div', attributes = {}) {
            this.tagName = tag.toLowerCase(); this.attributes = { ...attributes }; this.children = []; this.parentElement = null;
            this.value = attributes.value || ''; this.hidden = Object.hasOwn(attributes, 'hidden'); this.disabled = Object.hasOwn(attributes, 'disabled');
            this.checked = Object.hasOwn(attributes, 'checked'); this.open = Object.hasOwn(attributes, 'open'); this.listeners = {}; this.ownText = ''; this.dataset = {};
            this._classes = new Set((attributes.class || '').split(/\s+/).filter(Boolean));
            this.classList = { contains: (name) => this._classes.has(name),
                add: (...names) => { names.forEach((name) => this._classes.add(name)); this.syncClasses(); },
                remove: (...names) => { names.forEach((name) => this._classes.delete(name)); this.syncClasses(); },
                toggle: (name, enabled) => { enabled = enabled ?? !this._classes.has(name); if (enabled) this._classes.add(name); else this._classes.delete(name); this.syncClasses(); return enabled; } };
            for (const [name, value] of Object.entries(attributes)) if (name.startsWith('data-')) this.dataset[this.dataName(name)] = value;
            if (attributes.id) this.id = attributes.id;
        }
        dataName(name) { return name.slice(5).replace(/-([a-z])/g, (_, value) => value.toUpperCase()); }
        syncClasses() { this.attributes.class = [...this._classes].join(' '); }
        set className(value) { this._classes = new Set(String(value).split(/\s+/).filter(Boolean)); this.syncClasses(); }
        get className() { return this.attributes.class || ''; }
        set id(value) { this.attributes.id = String(value); elements.set(String(value), this); }
        get id() { return this.attributes.id || ''; }
        set textContent(value) { this.ownText = String(value); this.children.forEach((child) => { child.parentElement = null; }); this.children = []; }
        get textContent() { return this.ownText + this.children.map((child) => child.textContent).join(''); }
        get innerText() { return this.textContent; }
        set innerHTML(_value) { throw new Error('Writing content must be rendered with safe DOM nodes'); }
        append(...children) { children.forEach((child) => this.appendChild(typeof child === 'string' ? Object.assign(new Element('text'), { textContent: child }) : child)); }
        appendChild(child) { child.parentElement?.removeChild(child); this.children.push(child); child.parentElement = this; return child; }
        removeChild(child) { const index = this.children.indexOf(child); if (index >= 0) this.children.splice(index, 1); child.parentElement = null; return child; }
        replaceChildren(...children) { this.children.forEach((child) => { child.parentElement = null; }); this.children = []; this.ownText = ''; this.append(...children); }
        remove() { this.parentElement?.removeChild(this); }
        setAttribute(name, value) { this.attributes[name] = String(value); if (name === 'id') this.id = value; if (name === 'class') this.className = value; if (name.startsWith('data-')) this.dataset[this.dataName(name)] = String(value); }
        getAttribute(name) { return this.attributes[name] ?? null; }
        removeAttribute(name) { delete this.attributes[name]; }
        matches(selector) {
            const attr = [...selector.matchAll(/\[([\w-]+)(?:=["']?([^\]"']+)["']?)?\]/g)];
            if (attr.some(([, name, value]) => !Object.hasOwn(this.attributes, name) || value !== undefined && this.attributes[name] !== value)) return false;
            let simple = selector.replace(/\[[^\]]+\]/g, '');
            if (simple.includes(':checked')) { if (!this.checked) return false; simple = simple.replace(':checked', ''); }
            const id = simple.match(/#([\w-]+)/)?.[1]; if (id && this.id !== id) return false;
            const classes = [...simple.matchAll(/\.([\w-]+)/g)].map((match) => match[1]); if (classes.some((name) => !this._classes.has(name))) return false;
            const tag = simple.match(/^[a-z][\w-]*/i)?.[0]; return !tag || this.tagName === tag.toLowerCase();
        }
        querySelectorAll(selector) { const queries = selector.split(',').map((value) => value.trim()); return this.children.flatMap((child) => [...(queries.some((query) => child.matches(query)) ? [child] : []), ...child.querySelectorAll(selector)]); }
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
        closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
        contains(other) { return other === this || this.children.some((child) => child.contains(other)); }
        addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
        focus() { state.focused = this; }
        scrollIntoView() {}
        select() { state.selected = this; }
        setSelectionRange() {}
        async fire(name, extra = {}) {
            let stopped = false;
            const event = { preventDefault() {}, stopPropagation() { stopped = true; }, target: this, ...extra };
            for (let node = this; node; node = node.parentElement) {
                event.currentTarget = node;
                for (const callback of node.listeners[name] || []) await callback(event);
                if (stopped) break;
            }
            await settle(); await settle();
        }
    }

    const root = new Element('document'); const stack = [root];
    const voidTags = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);
    for (const token of html.match(/<!--[\s\S]*?-->|<[^>]+>|[^<]+/g) || []) {
        if (token.startsWith('<!--') || token.startsWith('<!')) continue;
        if (token.startsWith('</')) { stack.pop(); continue; }
        if (token.startsWith('<')) {
            const tag = token.match(/^<([a-z0-9-]+)/i)?.[1].toLowerCase(); if (!tag) continue;
            const attributes = {};
            for (const match of token.slice(tag.length + 1, -1).matchAll(/([a-zA-Z_:][\w:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) attributes[match[1]] = match[2] ?? match[3] ?? match[4] ?? '';
            const node = new Element(tag, attributes); stack.at(-1).appendChild(node);
            if (!voidTags.has(tag) && !token.endsWith('/>')) stack.push(node);
        } else stack.at(-1).ownText += token.replace(/\s+/g, ' ');
    }
    const field = (id) => { const node = elements.get(id); return node && root.contains(node) ? node : null; };
    assert.ok(field('writing-panel'), 'The composition panel must be integrated into the real subject page');
    field('subject-site-config').textContent = JSON.stringify({ id: 'chinese' });

    function freshData() {
        const data = emptyData();
        if (state.lesson.guidance) {
            data.outline = [{ id: 'p_full', content: '', purpose: '整篇初稿（开头—重点—结尾）', focus: true }];
            data.guidance = {
                version: 1, step: 0, confirmed: [], review: [], theme: '', customTheme: '', structure: '',
                slots: { begin: [], middle: [], end: [] }, notes: { begin: '', middle: '', end: '' },
                details: [], ownDetail: '', sentence: '', paragraph: '', endingOkay: false,
            };
        }
        if (state.lesson.coaching) {
            data.coaching = {
                version: 1,
                material: { step: 0, source: '', activity: '', moment: '', feeling: '' },
                comparison: { choices: [], reason: '' },
                evidence: {
                    structure: { before: '', after: '', reason: '' },
                    language: { before: '', after: '', reason: '' },
                },
            };
        }
        return data;
    }
    function makeAttempt(data = freshData(), suffix = String(state.attempts.size + 1)) {
        const id = 'writing_' + suffix.padStart(32, '0');
        const attempt = { id, lesson_id: state.lesson.id, lesson_snapshot: clone(state.lesson), data: clone(data), revision: 1,
            status: 'in_progress', created_at: '2026-11-13T10:00:00+08:00', updated_at: '2026-11-13T10:00:00+08:00', completed_at: null };
        state.attempts.set(id, attempt);
        state.versions.set(id, [{ id: 'version_' + suffix.padStart(32, '0'), label: '开始练习', revision: 1, created_at: attempt.created_at, data: clone(data), lesson_snapshot: clone(state.lesson) }]);
        return attempt;
    }
    seed.forEach((data, index) => makeAttempt(data, String(index + 1)));
    const versionSummaries = (id) => state.versions.get(id).map(({ data, lesson_snapshot, ...summary }) => summary);
    function payload(attempt) { return { attempt: clone(attempt), versions: clone(versionSummaries(attempt.id)), read_only: state.session.account.is_parent || attempt.status === 'abandoned' }; }
    function defaultReply(call) {
        const relative = call.url.slice(prefix.length);
        if (relative === '/lessons') {
            const item = Object.fromEntries(['id', 'version', 'title', 'grade', 'description', 'goal', 'time_hint'].filter((key) => key in state.lesson).map((key) => [key, clone(state.lesson[key])]));
            if (state.lesson.coaching) item.flow_version = 2;
            else if (state.lesson.guidance) item.flow_version = state.lesson.guidance.version;
            return { items: [item], count: 1 };
        }
        if (relative === '/lessons/' + state.lesson.id) return { lesson: clone(state.lesson) };
        if (relative === '/attempts' && call.method === 'GET') return { items: [...state.attempts.values()].filter((attempt) => attempt.status !== 'abandoned').map((attempt) => ({ id: attempt.id, lesson_id: attempt.lesson_id, title: attempt.lesson_snapshot.title, lesson_title: attempt.lesson_snapshot.title, stage: attempt.data.stage, revision: attempt.revision, status: attempt.status, updated_at: attempt.updated_at, completed_at: attempt.completed_at })), count: state.attempts.size, read_only: state.session.account.is_parent };
        if (relative === '/attempts' && call.method === 'POST') {
            const guided = [1, 2].includes(call.body.flow_version);
            if (guided) assert.equal(state.lesson.guidance?.version, 1);
            const attempt = makeAttempt(guided ? freshData() : emptyData());
            if (!guided) delete attempt.lesson_snapshot.guidance;
            if (call.body.flow_version !== 2) {
                delete attempt.lesson_snapshot.coaching;
                delete attempt.data.coaching;
            }
            const initialVersion = state.versions.get(attempt.id)[0];
            initialVersion.data = clone(attempt.data);
            initialVersion.lesson_snapshot = clone(attempt.lesson_snapshot);
            return payload(attempt);
        }
        const match = relative.match(/^\/attempts\/(writing_[a-f0-9]{32})(?:\/(.*))?$/); assert.ok(match, 'Unexpected writing route: ' + call.url);
        const attempt = state.attempts.get(match[1]); assert.ok(attempt, 'Attempt missing: ' + match[1]);
        const action = match[2];
        if (call.method === 'GET' && !action) return payload(attempt);
        if (call.method === 'GET' && action?.startsWith('versions/')) return { version: clone(state.versions.get(attempt.id).find((version) => version.id === action.slice(9))) };
        assert.equal(call.body.base_revision, attempt.revision, 'Writing saves must use the latest server revision');
        attempt.revision += 1;
        if (action === 'abandon') { attempt.status = 'abandoned'; return payload(attempt); }
        if (action === 'restore') attempt.data = clone(state.versions.get(attempt.id).find((version) => version.id === call.body.version_id).data);
        else attempt.data = clone(call.body.data);
        if (action === 'complete') { attempt.status = 'completed'; attempt.completed_at = '2026-11-13T10:30:00+08:00'; }
        if (action === 'checkpoints' || action === 'complete' || action === 'restore') state.versions.get(attempt.id).push({ id: 'version_' + String(state.versions.get(attempt.id).length + 1).padStart(32, '0'), label: call.body.label || (action === 'complete' ? '完成作品' : '恢复版本'), revision: attempt.revision, created_at: '2026-11-13T10:20:00+08:00', data: clone(attempt.data) });
        return payload(attempt);
    }
    state.defaultReply = defaultReply;

    const window = { addEventListener(name, callback) { (state.windowListeners[name] ||= []).push(callback); } };
    window.subjectPortal = {
        getSession: () => clone(state.session),
        showSection: (section) => { state.section = section; },
        refreshSection: async (section) => { state.section = section; },
        async apiRequest(url, options = {}) {
            url = url.replace(/^\/api/, '');
            const call = { url, method: options.method || 'GET', headers: clone(options.headers || {}), body: typeof options.body === 'string' ? JSON.parse(options.body) : clone(options.body) };
            state.calls.push(call);
            const response = state.routes[url] ? await state.routes[url](call) : defaultReply(call);
            return clone(response);
        },
    };
    const context = vm.createContext({
        window, document: { getElementById: field, createElement: (tag) => new Element(tag), querySelectorAll: (query) => root.querySelectorAll(query), querySelector: (query) => root.querySelector(query), addEventListener(name, callback) { if (name === 'DOMContentLoaded') callback(); }, activeElement: null },
        localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value)), removeItem: (key) => storage.delete(key) },
        navigator: { clipboard: { writeText: async (text) => { state.clipboard = text; } } },
        crypto: webcrypto, URL, Blob, TextEncoder, structuredClone, Date, console,
        setTimeout(callback, _delay) { const id = ++timerId; timers.set(id, callback); return id; }, clearTimeout(id) { timers.delete(id); },
        queueMicrotask, requestAnimationFrame: (callback) => callback(),
    });
    vm.runInContext(source, context);
    return { state, storage, context, field, api: window.subjectWriting, root, makeAttempt, freshData,
        async flush() { await settle(); await settle(); },
        async timers() { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach((callback) => callback()); await settle(); await settle(); },
        buttons(text) { return root.querySelectorAll('button').filter((button) => button.textContent.includes(text)); },
        async type(id, value, event = 'input') { const node = field(id); assert.ok(node, 'Missing input: ' + id); node.value = value; await node.fire(event); },
    };
}

test('the writing home lists unfinished work without forcing it open and allows a second draft', async () => {
    const ui = writingUI({ seed: [emptyData()] });
    await ui.api.open();
    assert.equal(ui.field('writing-home').hidden, false);
    assert.equal(ui.field('writing-editor').hidden, true);
    assert.equal(ui.state.calls.some((call) => /\/attempts\/writing_/.test(call.url)), false);
    assert.match(ui.field('writing-attempt-list').textContent, /继续练习/);
    await ui.field('writing-lessons').querySelector('button').fire('click');
    assert.equal(ui.state.attempts.size, 2);
    assert.equal(ui.field('writing-editor').hidden, false);
    assert.equal(ui.field('writing-plan-situation').value, '');
    assert.equal(ui.state.calls.filter((call) => call.method === 'POST' && call.url.endsWith('/attempts')).length, 1);
});

test('autosave persists the learner text and a fresh page can recover it from the server', async () => {
    const ui = writingUI({ seed: [emptyData()] });
    const attempt = [...ui.state.attempts.values()][0];
    await ui.api.openAttempt(attempt.id);
    await ui.type('writing-plan-situation', '课间，我和同桌玩猜词游戏。');
    await ui.timers();
    const saved = ui.state.attempts.get(attempt.id);
    assert.equal(saved.data.planning.situation, '课间，我和同桌玩猜词游戏。');
    assert.equal(saved.revision, 2);
    assert.equal(ui.storage.size, 0, 'An acknowledged save does not leave an obsolete recovery copy');
    const fresh = writingUI({ seed: [saved.data] });
    await fresh.api.openAttempt(attempt.id);
    assert.equal(fresh.field('writing-plan-situation').value, saved.data.planning.situation);
});

test('edits during an in-flight save stay visible and are serialized with the next server revision', async () => {
    const ui = writingUI({ seed: [emptyData()] });
    const attempt = [...ui.state.attempts.values()][0];
    await ui.api.openAttempt(attempt.id);
    const gate = deferred();
    let first;
    ui.state.routes[prefix + '/attempts/' + attempt.id] = (call) => {
        if (call.method === 'PATCH' && !first) { first = call; return gate.promise; }
        return ui.state.defaultReply(call);
    };
    await ui.type('writing-plan-situation', '先写下第一句话。');
    await ui.timers();
    assert.ok(first);
    await ui.type('writing-plan-situation', '保存时我又补上了一句。');
    await ui.timers();
    assert.equal(ui.state.calls.filter((call) => call.method === 'PATCH').length, 1, 'Only one save may be in flight');
    gate.resolve(ui.state.defaultReply(first));
    await ui.flush();
    await ui.timers();
    const saves = ui.state.calls.filter((call) => call.method === 'PATCH');
    assert.deepEqual(saves.map((call) => call.body.base_revision), [1, 2]);
    assert.equal(saves[1].body.data.planning.situation, '保存时我又补上了一句。');
    assert.equal(ui.field('writing-plan-situation').value, '保存时我又补上了一句。');
    assert.equal(ui.state.attempts.get(attempt.id).data.planning.situation, '保存时我又补上了一句。');
});

test('save and exit waits for the acknowledged save before returning to the list', async () => {
    const ui = writingUI({ seed: [emptyData()] });
    const attempt = [...ui.state.attempts.values()][0];
    await ui.api.openAttempt(attempt.id);
    const gate = deferred(); let saving;
    ui.state.routes[prefix + '/attempts/' + attempt.id] = (call) => {
        if (call.method === 'PATCH') { saving = call; return gate.promise; }
        return ui.state.defaultReply(call);
    };
    await ui.type('writing-plan-situation', '退出前要保存的内容。');
    await ui.field('writing-save-exit').fire('click');
    assert.ok(saving);
    assert.equal(ui.field('writing-editor').hidden, false);
    assert.equal(ui.field('writing-home').hidden, true);
    gate.resolve(ui.state.defaultReply(saving));
    await ui.flush();
    assert.equal(ui.field('writing-home').hidden, false);
    assert.equal(ui.field('writing-editor').hidden, true);
    assert.equal(ui.state.attempts.get(attempt.id).data.planning.situation, '退出前要保存的内容。');
});

test('a failed save keeps the local recovery copy and editing view until retry succeeds', async () => {
    const ui = writingUI({ seed: [emptyData()] });
    const attempt = [...ui.state.attempts.values()][0];
    await ui.api.openAttempt(attempt.id);
    ui.state.routes[prefix + '/attempts/' + attempt.id] = (call) => {
        if (call.method === 'PATCH') throw new Error('网络暂时中断');
        return ui.state.defaultReply(call);
    };
    await ui.type('writing-plan-situation', '不能丢失的选材。');
    await ui.timers();
    await ui.field('writing-save-exit').fire('click');
    assert.equal(ui.field('writing-editor').hidden, false);
    assert.match(ui.field('writing-save-state').textContent, /本机|同步/);
    assert.ok([...ui.storage.values()].some((value) => value.includes('不能丢失的选材')));
    assert.equal(ui.field('writing-plan-situation').value, '不能丢失的选材。');
    delete ui.state.routes[prefix + '/attempts/' + attempt.id];
    await ui.field('writing-save-exit').fire('click');
    assert.equal(ui.field('writing-home').hidden, false);
    assert.equal(ui.storage.size, 0);
});

test('a conflict stops automatic writes and preserves the full local plan for copying and comparison', async () => {
    const ui = writingUI({ seed: [emptyData()] });
    const attempt = [...ui.state.attempts.values()][0];
    attempt.data.paragraphs.parked = '这是一段还没放回提纲的文字。';
    await ui.api.openAttempt(attempt.id);
    ui.state.routes[prefix + '/attempts/' + attempt.id] = (call) => {
        if (call.method === 'PATCH') throw Object.assign(new Error('另一页面已有更新'), { status: 409, code: 'conflict', current_revision: 2 });
        return ui.state.defaultReply(call);
    };
    await ui.type('writing-plan-situation', '我要保留的独立选材。');
    await ui.type('writing-plan-main_idea', '我最想表达的是一次误会带来的快乐。');
    await ui.timers();
    assert.equal(ui.field('writing-conflict').hidden, false);
    const before = ui.state.calls.filter((call) => call.method === 'PATCH').length;
    await ui.type('writing-plan-ending', '回到座位还在笑。');
    await ui.timers();
    assert.equal(ui.state.calls.filter((call) => call.method === 'PATCH').length, before);
    await ui.field('writing-copy-draft').fire('click');
    assert.match(ui.state.clipboard, /我要保留的独立选材/);
    assert.match(ui.state.clipboard, /一次误会带来的快乐/);
    assert.match(ui.state.clipboard, /回到座位还在笑/);
    assert.match(ui.state.clipboard, /还没放回提纲的文字/);
    attempt.revision = 2;
    attempt.data.planning.situation = '另一页面已保存的选材。';
    await ui.field('writing-conflict-load').fire('click');
    assert.equal(ui.field('writing-plan-situation').value, '另一页面已保存的选材。');
    assert.equal(ui.field('writing-local-recovery').hidden, false);
    assert.ok([...ui.storage.values()].some((value) => value.includes('我要保留的独立选材')));
});

test('a late attempt response cannot reveal the previous student after account reset', async () => {
    const data = emptyData(); data.planning.situation = 'Alice 的私有写作内容。';
    const ui = writingUI({ seed: [data] });
    const attempt = [...ui.state.attempts.values()][0];
    const gate = deferred();
    ui.state.routes[prefix + '/attempts/' + attempt.id] = () => gate.promise;
    const opening = ui.api.openAttempt(attempt.id);
    await ui.flush();
    ui.api.reset();
    ui.state.session = { token: 'bob-token', username: 'bob', subject: 'chinese', account: { username: 'bob', is_parent: false } };
    gate.resolve({ attempt: clone(attempt), versions: [], read_only: false });
    await opening;
    assert.equal(ui.field('writing-editor').hidden, true);
    assert.equal(ui.field('writing-title').textContent, '');
    assert.doesNotMatch(ui.field('writing-panel').textContent, /Alice 的私有写作内容/);
});

test('a reset retains an unsynced student-scoped backup and an old save cannot replace the new account view', async () => {
    const ui = writingUI({ seed: [emptyData()] });
    const alice = [...ui.state.attempts.values()][0];
    await ui.api.openAttempt(alice.id);
    const gate = deferred(); let pending;
    ui.state.routes[prefix + '/attempts/' + alice.id] = (call) => {
        if (call.method === 'PATCH') { pending = call; return gate.promise; }
        return ui.state.defaultReply(call);
    };
    await ui.type('writing-plan-situation', 'Alice 未同步的草稿。');
    await ui.timers();
    ui.api.reset();
    ui.state.session = { token: 'bob-token', username: 'bob', subject: 'chinese', account: { username: 'bob', is_parent: false } };
    const bob = ui.makeAttempt();
    bob.data.planning.situation = 'Bob 自己的内容。';
    await ui.api.openAttempt(bob.id);
    gate.resolve(ui.state.defaultReply(pending));
    await ui.flush();
    assert.equal(ui.field('writing-plan-situation').value, 'Bob 自己的内容。');
    assert.equal(ui.field('writing-local-recovery').hidden, true);
    assert.ok([...ui.storage.entries()].some(([key, value]) => key.includes('alice') && value.includes('Alice 未同步的草稿')));
    assert.equal(ui.state.calls.filter((call) => call.method === 'PATCH').length, 1);
});

test('a parent may read the linked student work and history but cannot save or start a draft', async () => {
    const ui = writingUI({ isParent: true, seed: [emptyData()] });
    const attempt = [...ui.state.attempts.values()][0];
    await ui.api.open();
    assert.equal(ui.field('writing-lessons').querySelectorAll('button').length, 0);
    await ui.api.openAttempt(attempt.id);
    assert.match(ui.field('writing-read-only').textContent, /家长/);
    assert.equal(ui.field('writing-plan-situation').disabled, true);
    await ui.type('writing-plan-situation', '程序触发的家长代写');
    await ui.timers();
    await ui.field('writing-history-open').fire('click');
    assert.equal(ui.field('writing-history').hidden, false);
    assert.equal(ui.field('writing-version-restore').hidden, true);
    assert.equal(ui.state.calls.some((call) => call.method !== 'GET'), false);
    assert.equal(attempt.data.planning.situation, '');
});

test('deleting an outline card preserves its text and restoring it uses the same paragraph identity', async () => {
    const data = completedData(); data.stage = 1;
    data.paragraphs.p2 = '<script>保留这些原话，不执行</script>';
    const ui = writingUI({ seed: [data] });
    const attempt = [...ui.state.attempts.values()][0];
    await ui.api.openAttempt(attempt.id);
    const remove = ui.root.querySelectorAll('button').find((button) => button.getAttribute('aria-label')?.startsWith('删除第 2 段'));
    assert.ok(remove);
    await remove.fire('click');
    await ui.timers();
    assert.equal(attempt.data.outline.some((card) => card.id === 'p2'), false);
    assert.equal(attempt.data.paragraphs.p2, '<script>保留这些原话，不执行</script>');
    const restore = ui.root.querySelectorAll('button').find((button) => button.getAttribute('aria-label') === '恢复段落 p2 到提纲');
    assert.ok(restore);
    await restore.fire('click');
    await ui.timers();
    assert.equal(attempt.data.outline.some((card) => card.id === 'p2'), true);
    assert.equal(attempt.data.paragraphs.p2, '<script>保留这些原话，不执行</script>');
});

test('the two revision rounds save distinct checkpoints while preserving the original draft', async () => {
    const data = completedData(); data.stage = 1; data.review_round = 'structure';
    data.structure_started = data.language_started = false;
    data.structure_paragraphs = {}; data.final_paragraphs = {};
    data.revision_notes = { structure: '', language: '' }; data.reflection.learned = '';
    const ui = writingUI({ seed: [data] });
    const attempt = [...ui.state.attempts.values()][0];
    const original = clone(data.paragraphs);
    await ui.api.openAttempt(attempt.id);
    await ui.field('writing-next').fire('click');
    await ui.field('writing-next').fire('click');
    await ui.field('writing-next').fire('click');
    assert.match(ui.field('writing-next').textContent, /内容修改/);
    await ui.type('writing-paragraph-p2', '我竖起两根手指，同桌喊出了青蛙。');
    await ui.type('writing-revision-note', '补上一个具体动作。');
    await ui.field('writing-next').fire('click');
    assert.equal(ui.field('writing-paragraph-p2').value, '我竖起两根手指，同桌喊出了青蛙。');
    await ui.type('writing-paragraph-p2', '“青蛙！”他喊。我还没解释，就先笑了。');
    await ui.type('writing-revision-note', '删去重复评价。');
    await ui.field('writing-next').fire('click');
    assert.equal(ui.field('writing-complete').hidden, false);
    assert.deepEqual(attempt.data.paragraphs, original);
    assert.equal(attempt.data.final_paragraphs.p2, '“青蛙！”他喊。我还没解释，就先笑了。');
    const labels = ui.state.versions.get(attempt.id).map((version) => version.label);
    assert.ok(['提纲', '初稿', '内容修改稿', '修改稿'].every((label) => labels.includes(label)), labels.join(','));
    await ui.field('writing-complete').fire('click');
    assert.equal(attempt.status, 'in_progress', 'Reflection remains part of the learning task');
    assert.match(ui.field('writing-message').textContent, /学会/);
});

test('completion preserves the comparison and lets the learner begin another independent practice', async () => {
    const ui = writingUI({ seed: [completedData()] });
    const attempt = [...ui.state.attempts.values()][0];
    await ui.api.openAttempt(attempt.id);
    assert.match(ui.field('writing-stage-content').textContent, /我的初稿/);
    assert.match(ui.field('writing-stage-content').textContent, /我的修改稿/);
    await ui.field('writing-complete').fire('click');
    assert.equal(attempt.status, 'completed');
    assert.equal(ui.field('writing-reflection-learned').disabled, true);
    assert.equal(ui.field('writing-complete').hidden, true);
    assert.match(ui.field('writing-stage-content').textContent, /这次练习完成了/);
    assert.equal(ui.state.calls.some((call) => call.body && Object.hasOwn(call.body, 'score')), false);
    await ui.buttons('同一题，再练一次')[0].fire('click');
    assert.equal(ui.state.attempts.size, 2);
    assert.equal(ui.field('writing-plan-situation').value, '');
    assert.equal(attempt.status, 'completed');
});

test('restoring a history snapshot requires confirmation and keeps both versions', async () => {
    const old = emptyData(); old.planning.situation = '原来的选材。';
    const ui = writingUI({ seed: [old] });
    const attempt = [...ui.state.attempts.values()][0];
    attempt.data.planning.situation = '修改后的选材。'; attempt.revision = 4;
    await ui.api.openAttempt(attempt.id);
    await ui.field('writing-history-open').fire('click');
    assert.match(ui.field('writing-version-preview').textContent, /提纲/);
    await ui.field('writing-version-restore').fire('click');
    assert.equal(ui.field('writing-restore-confirmation').hidden, false);
    assert.equal(ui.state.calls.some((call) => call.url.endsWith('/restore')), false);
    await ui.field('writing-version-restore-confirm').fire('click');
    assert.equal(ui.field('writing-plan-situation').value, '原来的选材。');
    assert.equal(attempt.revision, 5);
    assert.equal(ui.state.versions.get(attempt.id).length, 2);
    assert.equal(ui.state.versions.get(attempt.id)[0].data.planning.situation, '原来的选材。');
});

test('a version-restore conflict retains the current local text before loading the newer cloud draft', async () => {
    const data = emptyData(); data.planning.situation = '我当前正在看的选材。';
    const ui = writingUI({ seed: [data] });
    const attempt = [...ui.state.attempts.values()][0];
    await ui.api.openAttempt(attempt.id);
    await ui.field('writing-history-open').fire('click');
    await ui.field('writing-version-restore').fire('click');
    ui.state.routes[prefix + '/attempts/' + attempt.id + '/restore'] = () => {
        throw Object.assign(new Error('另一页面已有新稿'), { status: 409, code: 'conflict', current_revision: 2 });
    };
    await ui.field('writing-version-restore-confirm').fire('click');
    assert.equal(ui.field('writing-conflict').hidden, false);
    assert.ok([...ui.storage.values()].some((value) => value.includes('我当前正在看的选材')), 'Restore conflicts must retain a recoverable local copy too');
    assert.equal(ui.field('writing-plan-situation').value, '我当前正在看的选材。');
});

test('unvisited steps cannot bypass the current task while completed steps remain available', async () => {
    const ui = writingUI({ seed: [emptyData()] });
    const attempt = [...ui.state.attempts.values()][0];
    await ui.api.openAttempt(attempt.id);
    const finalStep = ui.field('writing-steps').children[5];
    assert.equal(finalStep.disabled, true);
    await finalStep.fire('click');
    assert.ok(ui.field('writing-plan-situation'));
    assert.equal(ui.state.calls.some((call) => call.method !== 'GET'), false);
    await ui.type('writing-plan-situation', '课间玩猜词游戏。');
    await ui.type('writing-plan-main_idea', '猜错的时候最有趣。');
    await ui.field('writing-next').fire('click');
    assert.ok(ui.field('writing-outline-content-p1'));
    assert.equal(ui.field('writing-steps').children[0].disabled, false);
    assert.equal(ui.field('writing-steps').children[2].disabled, true);
    await ui.field('writing-steps').children[0].fire('click');
    assert.equal(ui.field('writing-plan-situation').value, '课间玩猜词游戏。');
    assert.equal(ui.field('writing-steps').children[1].disabled, false);
});

test('discarding a recovered local draft requires a second explicit choice', async () => {
    const storage = new Map();
    const first = writingUI({ storage, seed: [emptyData()] });
    const attempt = [...first.state.attempts.values()][0];
    await first.api.openAttempt(attempt.id);
    first.state.routes[prefix + '/attempts/' + attempt.id] = () => { throw new Error('断网'); };
    await first.type('writing-plan-situation', '这份未同步的本机稿应保留。');
    await first.timers();
    const second = writingUI({ storage, seed: [emptyData()] });
    await second.api.openAttempt(attempt.id);
    assert.equal(second.field('writing-local-recovery').hidden, false);
    await second.field('writing-keep-server').fire('click');
    assert.equal(storage.size, 1);
    assert.equal(second.field('writing-use-server-confirm').hidden, false);
    await second.buttons('先保留本机稿')[0].fire('click');
    assert.equal(storage.size, 1);
    assert.equal(second.field('writing-use-server-confirm').hidden, true);
    await second.field('writing-keep-server').fire('click');
    await second.field('writing-confirm-server').fire('click');
    assert.equal(storage.size, 0);
    assert.equal(second.field('writing-local-recovery').hidden, true);
    assert.equal(second.field('writing-plan-situation').value, '');
});

test('completion freezes editing until the saved work is acknowledged', async () => {
    const ui = writingUI({ seed: [completedData()] });
    const attempt = [...ui.state.attempts.values()][0];
    await ui.api.openAttempt(attempt.id);
    const gate = deferred(); let completing;
    ui.state.routes[prefix + '/attempts/' + attempt.id + '/complete'] = (call) => { completing = call; return gate.promise; };
    await ui.field('writing-complete').fire('click');
    assert.ok(completing);
    assert.equal(ui.field('writing-reflection-learned').disabled, true);
    assert.equal(attempt.status, 'in_progress');
    await ui.type('writing-reflection-learned', '保存时不能偷偷覆盖的新文字');
    gate.resolve(ui.state.defaultReply(completing));
    await ui.flush();
    assert.equal(attempt.status, 'completed');
    assert.equal(attempt.data.reflection.learned, '用动作和对话写出重点。');
    assert.equal(ui.field('writing-reflection-learned').value, '用动作和对话写出重点。');
});



test('save and exit either blocks late typing or waits until the latest text is acknowledged', async () => {
    const ui = writingUI({ seed: [emptyData()] });
    const attempt = [...ui.state.attempts.values()][0];
    await ui.api.openAttempt(attempt.id);
    const first = deferred(); const second = deferred();
    const saves = [];
    ui.state.routes[prefix + '/attempts/' + attempt.id] = (call) => {
        if (call.method !== 'PATCH') return ui.state.defaultReply(call);
        saves.push(call);
        return saves.length === 1 ? first.promise : second.promise;
    };
    await ui.type('writing-plan-situation', '点击退出时的第一份内容。');
    await ui.field('writing-save-exit').fire('click');
    assert.equal(saves.length, 1);
    const input = ui.field('writing-plan-situation');
    const typingBlocked = input.disabled || input.readOnly;
    if (!typingBlocked) await ui.type('writing-plan-situation', '保存等待期间补写的最后一句。');
    first.resolve(ui.state.defaultReply(saves[0]));
    await ui.flush();
    if (!typingBlocked) {
        assert.equal(saves.length, 2, 'The accepted late edit must be sent using the acknowledged revision');
        assert.equal(saves[1].body.base_revision, 2);
        assert.equal(saves[1].body.data.planning.situation, '保存等待期间补写的最后一句。');
        assert.equal(ui.field('writing-editor').hidden, false, 'Returning to the list must wait for the last accepted edit');
        assert.equal(ui.field('writing-home').hidden, true);
        second.resolve(ui.state.defaultReply(saves[1]));
        await ui.flush();
    }
    assert.equal(ui.field('writing-home').hidden, false);
    assert.equal(ui.field('writing-editor').hidden, true);
    assert.equal(attempt.data.planning.situation, typingBlocked ? '点击退出时的第一份内容。' : '保存等待期间补写的最后一句。');
    assert.equal(ui.storage.size, 0, 'Save and exit only completes once the accepted content is on the server');
});


test('a pending recovery with an older base cannot be edited into or copied as the newer cloud draft', async () => {
    const storage = new Map();
    const oldData = emptyData();
    oldData.paragraphs.parked = '本机原稿中未归入提纲的那段文字。';
    const first = writingUI({ storage, seed: [oldData] });
    const original = [...first.state.attempts.values()][0];
    await first.api.openAttempt(original.id);
    first.state.routes[prefix + '/attempts/' + original.id] = () => { throw new Error('断网，暂时无法同步'); };
    await first.type('writing-plan-situation', '本机尚未同步的原始选材。');
    await first.type('writing-plan-main_idea', '本机想保留的原始主题。');
    await first.timers();
    const before = [...storage.entries()];
    assert.equal(before.length, 1);
    assert.equal(JSON.parse(before[0][1]).base_revision, 1);

    const cloudData = emptyData();
    cloudData.planning.situation = '另一个页面已经保存的云端选材。';
    cloudData.planning.main_idea = '云端较新的主题。';
    cloudData.paragraphs.parked = '云端另写的段落。';
    const second = writingUI({ storage, seed: [cloudData] });
    const cloud = [...second.state.attempts.values()][0];
    cloud.revision = 7;
    await second.api.openAttempt(cloud.id);
    assert.equal(second.field('writing-local-recovery').hidden, false);
    assert.equal(second.field('writing-plan-situation').disabled, true);
    assert.equal(second.field('writing-plan-situation').value, '另一个页面已经保存的云端选材。');
    await second.type('writing-plan-situation', '程序触发输入也不能覆盖旧备份');
    await second.timers();
    assert.equal(second.state.calls.some((call) => call.method !== 'GET'), false);
    assert.deepEqual([...storage.entries()], before, 'The original backup and its old base revision must remain intact');

    await second.field('writing-recover-local').fire('click');
    assert.equal(second.field('writing-conflict').hidden, false);
    assert.equal(second.field('writing-plan-situation').disabled, true);
    assert.equal(second.field('writing-plan-situation').value, '另一个页面已经保存的云端选材。');
    await second.field('writing-copy-draft').fire('click');
    assert.match(second.state.clipboard, /本机尚未同步的原始选材/);
    assert.match(second.state.clipboard, /本机想保留的原始主题/);
    assert.match(second.state.clipboard, /本机原稿中未归入提纲的那段文字/);
    assert.doesNotMatch(second.state.clipboard, /云端较新的主题|云端另写的段落/);
    assert.deepEqual([...storage.entries()], before);
    assert.equal(cloud.revision, 7);
    assert.equal(cloud.data.planning.situation, '另一个页面已经保存的云端选材。');

    await second.field('writing-keep-server').fire('click');
    assert.deepEqual([...storage.entries()], before, 'The first click only asks for an explicit discard choice');
    await second.field('writing-confirm-server').fire('click');
    await second.timers();
    assert.equal(storage.size, 0);
    assert.equal(second.field('writing-local-recovery').hidden, true);
    assert.equal(second.field('writing-conflict').hidden, true);
    assert.equal(second.field('writing-plan-situation').disabled, false);
    assert.equal(second.field('writing-plan-situation').value, '另一个页面已经保存的云端选材。');
    assert.equal(second.state.calls.some((call) => call.method !== 'GET'), false);
});

function guidedData(step = 0, filled = false) {
    const data = emptyData();
    data.outline = [{ id: 'p_full', content: '', purpose: '整篇初稿（开头—重点—结尾）', focus: true }];
    data.guidance = {
        version: 1, step, confirmed: [], review: [], theme: '', customTheme: '', structure: '',
        slots: { begin: [], middle: [], end: [] }, notes: { begin: '', middle: '', end: '' },
        details: [], ownDetail: '', sentence: '', paragraph: '', endingOkay: false,
    };
    data.stage = step === 0 ? 0 : step <= 4 ? 1 : step <= 6 ? 2 : 3;
    if (filled) {
        Object.assign(data.guidance, {
            confirmed: Array.from({ length: 9 }, (_, index) => index), theme: 'mixup', structure: 'sequence',
            slots: { begin: ['start'], middle: ['frog'], end: ['echo'] },
            notes: { begin: '课间我和同桌猜词。', middle: '猜错后的反应。', end: '回座位仍想笑。' },
            details: ['ears'], ownDetail: '我急得直摆手。', sentence: '我把两根手指竖起来，他大喊：“青蛙！”',
            paragraph: '我把两根手指竖起来，他大喊：“青蛙！”我还没有说答案，先笑了。', endingOkay: true,
        });
        data.paragraphs.p_full = '下课，我和同桌玩猜词。\n\n我比画兔子，他猜成青蛙，我们都笑了。\n\n回到座位，我还想着那声“青蛙”。';
    }
    return data;
}

async function openGuided(step = 0, { data = guidedData(step), ...options } = {}) {
    const ui = writingUI({ lesson: GUIDED_LESSON, ...options });
    const attempt = ui.makeAttempt(data);
    await ui.api.openAttempt(attempt.id);
    return { ui, attempt };
}

function coachedData(step = 0, filled = false) {
    const data = guidedData(step, filled);
    data.coaching = {
        version: 1,
        material: { step: filled ? 4 : 0, source: '', activity: '', moment: '', feeling: '' },
        comparison: { choices: [], reason: '' },
        evidence: {
            structure: { before: '', after: '', reason: '' },
            language: { before: '', after: '', reason: '' },
        },
    };
    if (filled) {
        Object.assign(data.coaching.material, {
            source: 'personal', activity: '课间我和同桌在座位旁玩猜词。',
            moment: '我比画兔子，他猜成了青蛙。', feeling: '先惊讶，后来忍不住笑。',
        });
        data.coaching.comparison = { choices: ['action', 'reaction'], reason: '我能看见手指的位置和他猜错后的反应。' };
    }
    return data;
}

async function openCoached(step = 0, { data = coachedData(step), ...options } = {}) {
    const ui = writingUI({ lesson: COACHED_LESSON, ...options });
    const attempt = ui.makeAttempt(data);
    await ui.api.openAttempt(attempt.id);
    return { ui, attempt };
}

function revisionData(round = 'structure') {
    const data = coachedData(8, true);
    data.stage = 4; data.review_round = round; data.structure_started = true;
    data.structure_paragraphs = { ...data.paragraphs };
    if (round === 'language') {
        data.language_started = true;
        data.final_paragraphs = { ...data.paragraphs };
    }
    return data;
}

async function guidedNav(ui, step) {
    const node = ui.field('writing-guided-step-' + step);
    assert.ok(node, 'Missing guided navigation step ' + step);
    await node.fire('click');
}

function writes(ui) { return ui.state.calls.filter((call) => call.method !== 'GET'); }

test('the real version-two lesson negotiates its guided flow while old summaries still create classic drafts', async () => {
    assert.equal(GUIDED_LESSON.guidance.version, 1);
    assert.equal(GUIDED_LESSON.guidance.steps.length, 9);
    const ui = writingUI({ lesson: GUIDED_LESSON });
    await ui.api.open();
    await ui.field('writing-lessons').querySelector('button').fire('click');
    const start = ui.state.calls.find((call) => call.method === 'POST' && call.url.endsWith('/attempts'));
    assert.equal(start.body.flow_version, 1);
    assert.equal(start.headers['X-Writing-Flow'], 'guided-v2');
    assert.ok(ui.field('writing-guided-customTheme'));
    const current = [...ui.state.attempts.values()][0];
    assert.equal(current.data.outline[0].id, 'p_full');
    assert.deepEqual(current.data.guidance.confirmed, []);
    await ui.type('writing-guided-customTheme', '这是我自己想到的主旨。');
    await ui.timers();
    const guidedSaves = ui.state.calls.filter((call) => call.method === 'PATCH');
    assert.ok(guidedSaves.length > 0);
    assert.ok(guidedSaves.every((call) => call.headers['X-Writing-Flow'] === 'guided-v2'));

    const old = writingUI({ lesson: GUIDED_LESSON });
    old.state.routes[prefix + '/lessons'] = () => ({ items: [{ id: GUIDED_LESSON.id, title: GUIDED_LESSON.title }], count: 1 });
    await old.api.open();
    await old.field('writing-lessons').querySelector('button').fire('click');
    const classic = [...old.state.attempts.values()][0];
    assert.equal(classic.data.guidance, undefined);
    assert.equal(classic.lesson_snapshot.guidance, undefined);
    assert.ok(old.field('writing-plan-situation'));
});

test('the version-three catalogue creates the enhanced flow without changing saved guided-v1 work', async () => {
    assert.equal(COACHED_LESSON.version, 3);
    assert.equal(COACHED_LESSON.guidance.version, 1);
    assert.equal(COACHED_LESSON.coaching.version, 1);
    const ui = writingUI({ lesson: COACHED_LESSON });
    await ui.api.open();
    await ui.field('writing-lessons').querySelector('button').fire('click');
    const request = ui.state.calls.find((call) => call.method === 'POST' && call.url.endsWith('/attempts'));
    assert.equal(request.body.flow_version, 2);
    assert.equal(request.headers['X-Writing-Flow'], 'guided-v2');
    const fresh = [...ui.state.attempts.values()][0];
    assert.equal(fresh.data.coaching.version, 1);
    assert.deepEqual(fresh.data.coaching.comparison.choices, []);

    const old = writingUI({ lesson: COACHED_LESSON });
    const previous = old.makeAttempt(guidedData(5, true));
    previous.lesson_snapshot = clone(GUIDED_LESSON);
    await old.api.openAttempt(previous.id);
    assert.ok(old.field('writing-guided-sentence'));
    assert.equal(previous.data.coaching, undefined);
    assert.equal(previous.lesson_snapshot.coaching, undefined);
    await old.type('writing-guided-sentence', '旧稿仍然按原来的九问继续写。');
    await old.timers();
    assert.equal(previous.data.guidance.sentence, '旧稿仍然按原来的九问继续写。');
    assert.equal(previous.data.coaching, undefined, 'Opening an old draft must not force new teaching tasks into it');
});

test('material recall unfolds one small question at a time before showing theme references beside the learner own words', async () => {
    const { ui, attempt } = await openCoached();
    assert.equal(ui.field('writing-stage-title').textContent, '主旨立意 · 先回想素材');
    assert.match(ui.field('writing-stage-goal').textContent, /一次只想一小问/);
    assert.ok(ui.field('writing-material-source-personal'));
    assert.equal(ui.field('writing-material-activity'), null);
    assert.equal(ui.field('writing-guided-customTheme'), null);
    assert.equal(ui.field('writing-next').disabled, true);
    await ui.field('writing-material-source-personal').fire('click');
    await ui.field('writing-material-next').fire('click');
    assert.ok(ui.field('writing-material-activity'));
    assert.equal(ui.field('writing-material-moment'), null);
    await ui.type('writing-material-activity', '课间我和小林在教室里玩猜词。');
    await ui.field('writing-material-next').fire('click');
    assert.equal(ui.field('writing-material-activity'), null);
    assert.ok(ui.field('writing-material-moment'));
    await ui.type('writing-material-moment', '我比画兔子，他猜成青蛙。');
    await ui.field('writing-material-next').fire('click');
    assert.equal(ui.field('writing-material-moment'), null);
    assert.ok(ui.field('writing-material-feeling'));
    await ui.type('writing-material-feeling', '我先惊讶，接着忍不住笑。');
    await ui.field('writing-material-next').fire('click');
    assert.equal(ui.field('writing-stage-title').textContent, COACHED_LESSON.guidance.steps[0].question);
    assert.equal(ui.field('writing-stage-goal').textContent, COACHED_LESSON.guidance.steps[0].description);
    assert.equal(ui.field('writing-material-feeling'), null);
    assert.ok(ui.field('writing-guided-theme-mixup'));
    assert.ok(ui.field('writing-guided-customTheme'), 'References and the learner own description share the same question page');
    await ui.field('writing-guided-theme-mixup').fire('click');
    await ui.type('writing-guided-customTheme', '我想写同伴配合时的意外和快乐。');
    await ui.timers();
    assert.equal(ui.field('writing-guided-adopted-theme').textContent, '我想写同伴配合时的意外和快乐。');
    assert.deepEqual(attempt.data.coaching.material, {
        step: 4, source: 'personal', activity: '课间我和小林在教室里玩猜词。',
        moment: '我比画兔子，他猜成青蛙。', feeling: '我先惊讶，接着忍不住笑。',
    });
    assert.equal(attempt.data.paragraphs.p_full || '', '', 'Recalling material must not generate a draft');
});

test('looking back at material keeps confirmed work available while a changed source preserves later writing for review', async () => {
    const data = coachedData(0, true);
    data.structure_started = data.language_started = true;
    data.structure_paragraphs = { p_full: '学生后来自己写过的内容修改稿。' };
    data.final_paragraphs = { p_full: '学生后来自己写过的表达修改稿。' };
    const { ui, attempt } = await openCoached(0, { data });
    await ui.field('writing-material-edit-source').fire('click');
    await ui.timers();
    assert.deepEqual(attempt.data.guidance.review, [], 'Opening an earlier question is not itself a material change');
    assert.equal(ui.field('writing-guided-review-structure').disabled, false,
        'Looking back at a material question cannot invalidate nine already-confirmed answers');
    await ui.field('writing-guided-review-structure').fire('click');
    assert.equal(ui.field('writing-paragraph-p_full').value, data.structure_paragraphs.p_full,
        'The learner can return to the saved revision without answering the material questions again');
    assert.deepEqual(attempt.data.guidance.review, []);
    await guidedNav(ui, 0);
    await ui.field('writing-material-source-scenario').fire('click');
    await ui.timers();
    assert.equal(attempt.data.coaching.material.source, 'scenario');
    assert.equal(attempt.data.coaching.material.activity, data.coaching.material.activity);
    assert.equal(attempt.data.guidance.paragraph, data.guidance.paragraph);
    assert.deepEqual(attempt.data.paragraphs, data.paragraphs);
    assert.deepEqual(attempt.data.structure_paragraphs, data.structure_paragraphs);
    assert.deepEqual(attempt.data.final_paragraphs, data.final_paragraphs);
    assert.deepEqual([...attempt.data.guidance.review].sort((a, b) => a - b), Array.from({ length: 9 }, (_, i) => i));
    assert.equal(attempt.data.guidance.endingOkay, false);
});

test('material suggestions follow the chosen source and append only to the current recollection', async () => {
    const data = coachedData(0, true);
    data.coaching.material.step = 1;
    data.coaching.material.activity = '上次课间我和小林玩';
    data.guidance.customTheme = '我想写两个人一起尝试的快乐。';
    const { ui, attempt } = await openCoached(0, { data });
    const firstPersonal = COACHED_LESSON.coaching.material.suggestions.personal.activity[0];
    assert.equal(ui.field('writing-material-suggestion-activity-0').dataset.value, firstPersonal);
    await ui.field('writing-material-suggestion-activity-0').fire('click');
    await ui.field('writing-material-suggestion-activity-0').fire('click');
    assert.equal(ui.field('writing-material-activity').value, data.coaching.material.activity + '，' + firstPersonal);
    await ui.field('writing-material-prev').fire('click');
    await ui.field('writing-material-source-scenario').fire('click');
    await ui.field('writing-material-next').fire('click');
    const firstScenario = COACHED_LESSON.coaching.material.suggestions.scenario.activity[0];
    assert.equal(ui.field('writing-material-suggestion-activity-0').dataset.value, firstScenario);
    assert.equal(ui.field('writing-material-activity').value, data.coaching.material.activity + '，' + firstPersonal,
        'Switching the source does not substitute the scenario for a remembered event');
    await ui.timers();
    assert.equal(attempt.data.guidance.customTheme, data.guidance.customTheme);
    assert.equal(attempt.data.guidance.paragraph, data.guidance.paragraph);
    assert.deepEqual(attempt.data.paragraphs, data.paragraphs);
});

test('a personal event stays visible in later questions and can advance using own material without selecting the scenario', async () => {
    const data = coachedData(2, true);
    Object.assign(data.coaching.material, { activity: '我和小林在走廊跳绳。', moment: '绳子绊住鞋子，我们停下重新来。', feeling: '有点着急，成功后很开心。' });
    data.guidance.slots = { begin: [], middle: [], end: [] };
    data.guidance.notes = { begin: '下课后我和小林到走廊跳绳。', middle: '', end: '' };
    data.paragraphs.p_full = '我已经用自己的跳绳经历写下的初稿。';
    const { ui, attempt } = await openCoached(2, { data });
    const context = ui.field('writing-material-context').textContent;
    assert.ok(context.includes(data.coaching.material.activity));
    assert.ok(context.includes(data.coaching.material.moment));
    assert.ok(context.includes(data.coaching.material.feeling));
    assert.match(context, /只是写法例子，可以不选/);
    await ui.field('writing-next').fire('click');
    assert.ok(ui.field('writing-guided-note-middle'));
    await ui.type('writing-guided-note-middle', '我想写两个人怎样重新配合好。');
    await ui.field('writing-next').fire('click');
    assert.ok(ui.field('writing-guided-note-end'));
    assert.deepEqual(attempt.data.guidance.slots, { begin: [], middle: [], end: [] });
    assert.equal(attempt.data.paragraphs.p_full, data.paragraphs.p_full);
    assert.deepEqual(attempt.data.coaching.material, data.coaching.material);
});

test('detail comparison accepts several reasoned choices without grading or inserting the reference sentences', async () => {
    const data = coachedData(5, true);
    data.coaching.comparison = { choices: [], reason: '' };
    data.guidance.sentence = '';
    const { ui, attempt } = await openCoached(5, { data });
    assert.ok(ui.field('writing-guided-sentence'));
    assert.ok(ui.field('writing-comparison-reason'));
    const extraDetails = ui.field('writing-coaching-detail-keywords');
    assert.ok(extraDetails, 'The older keyword cards remain available when needed');
    assert.equal(extraDetails.open, false, 'The optional keyword cards start folded to keep comparison choices clear');
    assert.equal(ui.field('writing-guided-detail-ears').closest('details'), extraDetails);
    await ui.field('writing-comparison-action').fire('click');
    await ui.field('writing-comparison-reaction').fire('click');
    await ui.timers();
    assert.deepEqual(attempt.data.coaching.comparison.choices, ['action', 'reaction']);
    assert.equal(ui.field('writing-guided-sentence').value, '');
    assert.equal(ui.field('writing-next').disabled, true, 'Reference selections cannot stand in for the learner own sentence');
    assert.doesNotMatch(ui.field('writing-stage-content').textContent, /回答正确|回答错误|标准答案：|正确答案是/);
    await ui.field('writing-comparison-action').fire('click');
    await ui.field('writing-comparison-dialogue').fire('click');
    await ui.type('writing-guided-sentence', '我举着手指等答案，小林忽然喊出“青蛙”。');
    assert.equal(ui.field('writing-next').disabled, false, 'Explaining a comparison choice is optional');
    await ui.field('writing-next').fire('click');
    assert.deepEqual([...attempt.data.coaching.comparison.choices].sort(), ['dialogue', 'reaction']);
    assert.equal(attempt.data.coaching.comparison.reason, '');
    assert.equal(attempt.data.guidance.sentence, '我举着手指等答案，小林忽然喊出“青蛙”。');
    assert.equal(attempt.data.guidance.paragraph, data.guidance.paragraph, 'An existing learner paragraph is not replaced by a comparison option');
    assert.equal(attempt.data.paragraphs.p_full, data.paragraphs.p_full);
    const availableDetails = ui.field('writing-stage-content').querySelector('.writing-guided-related').textContent;
    for (const id of ['dialogue', 'reaction']) {
        const selected = COACHED_LESSON.coaching.comparison.options.find((option) => option.id === id);
        assert.ok(availableDetails.includes(selected.text), 'The next paragraph keeps the selected writing reference visible');
    }
    assert.equal(availableDetails.includes(COACHED_LESSON.coaching.comparison.options.find((option) => option.id === 'action').text), false);
});

test('both revision rounds save exact change evidence while the learner edits the article separately', async () => {
    const data = revisionData();
    const original = data.paragraphs.p_full;
    const contentDraft = original.replace('我比画兔子', '我把两根手指竖在头顶，比画兔子');
    const expressionDraft = contentDraft.replace('我们都笑了', '我笑得手指都放不下来');
    const { ui, attempt } = await openCoached(8, { data });
    assert.match(ui.field('writing-evidence-previous-text').textContent, /我比画兔子/);
    await ui.type('writing-evidence-before', '我比画兔子');
    await ui.type('writing-evidence-after', '我把两根手指竖在头顶，比画兔子');
    await ui.type('writing-evidence-reason', '让读者看见我到底怎样比画。');
    assert.equal(ui.field('writing-paragraph-p_full').value, original, 'The evidence card cannot rewrite the article');
    await ui.field('writing-next').fire('click');
    assert.equal(attempt.data.review_round, 'structure', 'The claimed new fragment must actually be in the edited draft');
    assert.match(ui.field('writing-message').textContent, /改句|正文/);
    await ui.type('writing-paragraph-p_full', contentDraft);
    await ui.type('writing-evidence-before', '初稿里没有出现过的原句');
    await ui.field('writing-next').fire('click');
    assert.equal(attempt.data.review_round, 'structure', 'A before fragment must also be traceable in the previous article');
    assert.match(ui.field('writing-message').textContent, /原句|原稿/);
    await ui.type('writing-evidence-before', '我比画兔子');
    assert.equal(ui.field('writing-next').disabled, false);
    await ui.field('writing-next').fire('click');
    assert.equal(attempt.data.review_round, 'language');
    assert.equal(ui.field('writing-paragraph-p_full').value, contentDraft);
    await ui.type('writing-evidence-before', '我们都笑了');
    await ui.type('writing-evidence-after', '我笑得手指都放不下来');
    await ui.type('writing-evidence-reason', '用具体反应表现快乐，读者更容易感受到。');
    assert.equal(ui.field('writing-paragraph-p_full').value, contentDraft);
    await ui.type('writing-paragraph-p_full', expressionDraft);
    await ui.field('writing-next').fire('click');
    assert.equal(attempt.data.stage, 5);
    assert.deepEqual(attempt.data.coaching.evidence, {
        structure: { before: '我比画兔子', after: '我把两根手指竖在头顶，比画兔子', reason: '让读者看见我到底怎样比画。' },
        language: { before: '我们都笑了', after: '我笑得手指都放不下来', reason: '用具体反应表现快乐，读者更容易感受到。' },
    });
    assert.equal(attempt.data.revision_notes.structure, attempt.data.coaching.evidence.structure.reason);
    assert.equal(attempt.data.revision_notes.language, attempt.data.coaching.evidence.language.reason);
    assert.equal(attempt.data.paragraphs.p_full, original);
    assert.equal(attempt.data.structure_paragraphs.p_full, contentDraft);
    assert.equal(attempt.data.final_paragraphs.p_full, expressionDraft);
    const contentVersion = ui.state.versions.get(attempt.id).find((version) => version.label === '内容修改稿');
    const expressionVersion = ui.state.versions.get(attempt.id).find((version) => version.label === '修改稿');
    assert.deepEqual(contentVersion.data.coaching.evidence.structure, attempt.data.coaching.evidence.structure);
    assert.deepEqual(expressionVersion.data.coaching.evidence, attempt.data.coaching.evidence);
});

test('checking that no change is needed permits both rounds without inventing edits or evidence', async () => {
    const data = revisionData();
    const { ui, attempt } = await openCoached(8, { data });
    await ui.field('writing-next').fire('click');
    assert.equal(attempt.data.review_round, 'structure');
    assert.equal(writes(ui).some((call) => call.url.endsWith('/checkpoints')), false);
    for (const round of ['structure', 'language']) {
        const checkbox = ui.field('writing-no-change');
        checkbox.checked = true; await checkbox.fire('change');
        assert.equal(ui.field('writing-next').disabled, false);
        await ui.field('writing-next').fire('click');
        assert.equal(attempt.data.no_change[round], true);
        assert.deepEqual(attempt.data.coaching.evidence[round], { before: '', after: '', reason: '' });
    }
    assert.equal(attempt.data.stage, 5);
    assert.equal(attempt.data.paragraphs.p_full, data.paragraphs.p_full);
    assert.equal(attempt.data.final_paragraphs.p_full, data.paragraphs.p_full);
});

test('enhanced history previews and restores material, comparison and both evidence rounds together', async () => {
    const data = revisionData('language');
    data.coaching.evidence = {
        structure: { before: '我比画兔子', after: '我比画兔子', reason: '历史版本的内容修改理由。' },
        language: { before: '我们都笑了', after: '我们都笑了', reason: '历史版本的表达修改理由。' },
    };
    const { ui, attempt } = await openCoached(8, { data });
    attempt.data.coaching.material.moment = '后来换用的另一件事。';
    attempt.data.coaching.comparison.reason = '后来才写的比较理由。';
    attempt.data.coaching.evidence.structure.reason = '后来才写的内容理由。';
    attempt.data.coaching.evidence.language.reason = '后来才写的表达理由。';
    attempt.revision = 7;
    await ui.api.openAttempt(attempt.id);
    await ui.field('writing-history-open').fire('click');
    const preview = ui.field('writing-version-preview').textContent;
    for (const text of [data.coaching.material.moment, data.coaching.comparison.reason,
        data.coaching.evidence.structure.reason, data.coaching.evidence.language.reason]) assert.ok(preview.includes(text));
    await ui.field('writing-version-restore').fire('click');
    await ui.field('writing-version-restore-confirm').fire('click');
    assert.deepEqual(attempt.data.coaching, data.coaching);
    assert.equal(ui.field('writing-evidence-reason').value, data.coaching.evidence.language.reason);
    assert.equal(ui.state.versions.get(attempt.id).length, 2);
    assert.equal(attempt.revision, 8);
});

test('all nine questions can be visited freely without confirming answers or showing multiple question editors', async () => {
    const { ui, attempt } = await openGuided();
    for (let step = 0; step < 9; step += 1) {
        assert.equal(ui.field('writing-guided-step-' + step).disabled, false);
        await guidedNav(ui, step);
        assert.equal(ui.field('writing-stage-title').textContent, GUIDED_LESSON.guidance.steps[step].question);
        const text = ui.field('writing-stage-content').textContent;
        if (step !== 0) assert.equal(ui.field('writing-guided-customTheme'), null);
        if (step !== 5) assert.equal(ui.field('writing-guided-sentence'), null);
        if (step !== 7) assert.equal(ui.field('writing-guided-draft'), null);
        assert.equal(typeof text, 'string');
    }
    await ui.timers();
    assert.deepEqual(attempt.data.guidance.confirmed, []);
    assert.equal(writes(ui).some((call) => call.url.endsWith('/checkpoints') || call.url.endsWith('/complete')), false);
});

test('the learner own theme wins over reference choices and only a changed adopted meaning needs review', async () => {
    const data = guidedData(0, true); data.guidance.customTheme = '我想写同桌猜错时带给我的快乐。';
    const { ui, attempt } = await openGuided(0, { data });
    await ui.field('writing-guided-theme-together').fire('click');
    await ui.timers();
    assert.equal(ui.field('writing-guided-adopted-theme').textContent, data.guidance.customTheme);
    assert.deepEqual(attempt.data.guidance.review, []);
    assert.equal(attempt.data.guidance.endingOkay, true);
    await ui.type('writing-guided-customTheme', '  ' + data.guidance.customTheme + '  ');
    await ui.timers();
    assert.deepEqual(attempt.data.guidance.review, [], 'Whitespace alone does not change the adopted meaning');
    await ui.type('writing-guided-customTheme', '我更想表达同伴愿意配合的快乐。');
    await ui.timers();
    assert.ok(attempt.data.guidance.review.includes(0) && attempt.data.guidance.review.includes(8));
    assert.equal(attempt.data.paragraphs.p_full, data.paragraphs.p_full);
    assert.equal(attempt.data.guidance.endingOkay, false);
});

test('material choices and own notes coexist in the current frame and the middle keeps one chosen focus', async () => {
    const data = guidedData(2); data.guidance.theme = 'fun'; data.guidance.structure = 'total'; data.guidance.confirmed = [0, 1];
    const { ui, attempt } = await openGuided(2, { data });
    assert.ok(ui.field('writing-guided-material-start'));
    assert.ok(ui.field('writing-guided-note-begin'));
    await ui.field('writing-guided-material-start').fire('click');
    await ui.type('writing-guided-note-begin', '我和同桌在座位旁约好玩。');
    await ui.field('writing-next').fire('click');
    assert.deepEqual(attempt.data.guidance.slots.begin, ['start']);
    assert.equal(attempt.data.guidance.notes.begin, '我和同桌在座位旁约好玩。');
    assert.ok(attempt.data.guidance.confirmed.includes(2));
    assert.ok(ui.field('writing-guided-material-frog'));
    await ui.field('writing-guided-material-frog').fire('click');
    await ui.field('writing-guided-material-laugh').fire('click');
    await ui.type('writing-guided-note-middle', '我想写自己先一愣又笑起来。');
    await ui.timers();
    assert.deepEqual(attempt.data.guidance.slots.middle, ['laugh']);
    assert.equal(attempt.data.guidance.notes.middle, '我想写自己先一愣又笑起来。');
    await guidedNav(ui, 4);
    await ui.type('writing-guided-note-end', '回到座位时，我还记着那一声。');
    assert.equal(ui.field('writing-next').disabled, false, 'An own-material answer can stand without a card');
});

test('details remain beside the sentence editor and cannot replace a learner-written sentence', async () => {
    const { ui, attempt } = await openGuided(5);
    await ui.field('writing-guided-detail-ears').fire('click');
    await ui.type('writing-guided-ownDetail', '我急得直摆手。');
    assert.ok(ui.field('writing-guided-detail-reaction'));
    assert.ok(ui.field('writing-guided-sentence'));
    assert.equal(ui.field('writing-next').disabled, true);
    await ui.field('writing-next').fire('click');
    assert.equal(ui.field('writing-guided-sentence').value, '');
    await ui.type('writing-guided-sentence', '我竖起手指，同桌喊出了“青蛙”。');
    await ui.field('writing-next').fire('click');
    assert.equal(ui.field('writing-guided-paragraph').value, '我竖起手指，同桌喊出了“青蛙”。');
    assert.equal(attempt.data.guidance.paragraph, attempt.data.guidance.sentence);
    assert.ok(attempt.data.guidance.confirmed.includes(5));
});

test('own sentences and paragraphs carry forward once without replacing existing work or duplicating manual insertions', async () => {
    const data = guidedData(5); data.guidance.sentence = '我的新句子。'; data.guidance.paragraph = '我已经写过的重点段。'; data.paragraphs.p_full = '我已经写过的整篇初稿。';
    const { ui, attempt } = await openGuided(5, { data });
    await ui.field('writing-next').fire('click');
    assert.equal(ui.field('writing-guided-paragraph').value, '我已经写过的重点段。');
    await ui.type('writing-guided-paragraph', '我后来自己调整的重点段。');
    await ui.field('writing-next').fire('click');
    assert.equal(ui.field('writing-guided-draft').value, '我已经写过的整篇初稿。');
    await ui.buttons('把写好的重点段接进来')[0].fire('click');
    await ui.buttons('把写好的重点段接进来')[0].fire('click');
    await ui.timers();
    assert.equal(attempt.data.paragraphs.p_full, '我已经写过的整篇初稿。\n\n我后来自己调整的重点段。');
    await guidedNav(ui, 5);
    await ui.type('writing-guided-sentence', '又改了一次自己的句子。');
    await ui.field('writing-next').fire('click');
    assert.equal(ui.field('writing-guided-paragraph').value, '我后来自己调整的重点段。');
    const blank = guidedData(6); blank.guidance.paragraph = '只在空白全文中初次带入的自己的段落。';
    const fresh = await openGuided(6, { data: blank });
    await fresh.ui.field('writing-next').fire('click');
    assert.equal(fresh.ui.field('writing-guided-draft').value, blank.guidance.paragraph);
});

test('changed upstream material preserves downstream prose, marks only dependent confirmations and catches unfinished questions at the end', async () => {
    const data = guidedData(3, true);
    const { ui, attempt } = await openGuided(3, { data });
    await ui.field('writing-guided-material-laugh').fire('click');
    await ui.timers();
    assert.deepEqual([...attempt.data.guidance.review].sort((a, b) => a - b), [3, 5, 6, 7, 8]);
    assert.equal(attempt.data.paragraphs.p_full, data.paragraphs.p_full);
    assert.equal(attempt.data.guidance.paragraph, data.guidance.paragraph);
    await guidedNav(ui, 8);
    await ui.field('writing-guided-ending-okay').fire('click');
    await ui.field('writing-next').fire('click');
    assert.equal(ui.field('writing-stage-title').textContent, GUIDED_LESSON.guidance.steps[3].question);
    assert.equal(writes(ui).some((call) => call.url.endsWith('/complete')), false);
    const missing = guidedData(8, true); missing.guidance.confirmed = missing.guidance.confirmed.filter((step) => step !== 2);
    const unfinished = await openGuided(8, { data: missing });
    await unfinished.ui.field('writing-next').fire('click');
    assert.equal(unfinished.ui.field('writing-stage-title').textContent, GUIDED_LESSON.guidance.steps[2].question);
    assert.ok(!unfinished.attempt.data.guidance.confirmed.includes(2));
});

test('help temporarily replaces the question editor and examples never paste into the learner draft', async () => {
    const data = guidedData(7, true); data.paragraphs.p_full = '<script>这是我自己已经写好的全文，不执行。</script>';
    const { ui, attempt } = await openGuided(7, { data });
    await ui.field('writing-guided-help-open').fire('click');
    assert.equal(ui.field('writing-guided-draft'), null);
    assert.ok(ui.field('writing-guided-help-back'));
    await ui.buttons('再看看范文怎么安排')[0].fire('click');
    await ui.buttons('我想阅读全文')[0].fire('click');
    assert.match(ui.field('writing-stage-content').textContent, /写法提示/);
    await ui.field('writing-next').fire('click');
    assert.equal(ui.field('writing-guided-draft').value, data.paragraphs.p_full);
    await ui.timers();
    assert.equal(attempt.data.paragraphs.p_full, data.paragraphs.p_full);
    assert.deepEqual(attempt.data.guidance.confirmed, data.guidance.confirmed);
});

test('guided autosave serializes in-flight theme edits and the restored page keeps the final own theme', async () => {
    const { ui, attempt } = await openGuided();
    const gate = deferred(); let first;
    ui.state.routes[prefix + '/attempts/' + attempt.id] = (call) => {
        if (call.method === 'PATCH' && !first) { first = call; return gate.promise; }
        return ui.state.defaultReply(call);
    };
    await ui.type('writing-guided-customTheme', '先写下的主旨。'); await ui.timers();
    await ui.type('writing-guided-customTheme', '等待保存时补充的最终主旨。'); await ui.timers();
    assert.equal(ui.state.calls.filter((call) => call.method === 'PATCH').length, 1);
    gate.resolve(ui.state.defaultReply(first)); await ui.flush(); await ui.timers();
    const saves = ui.state.calls.filter((call) => call.method === 'PATCH');
    assert.deepEqual(saves.map((call) => call.body.base_revision), [1, 2]);
    assert.equal(attempt.data.guidance.customTheme, '等待保存时补充的最终主旨。');
    assert.equal(ui.field('writing-guided-customTheme').value, '等待保存时补充的最终主旨。');
    const restored = await openGuided(0, { data: attempt.data });
    assert.equal(restored.ui.field('writing-guided-customTheme').value, '等待保存时补充的最终主旨。');
});

test('a guided conflict keeps canonical draft and guided notes in the account-scoped recovery copy', async () => {
    const data = guidedData(7, true);
    const { ui, attempt } = await openGuided(7, { data });
    ui.state.routes[prefix + '/attempts/' + attempt.id] = (call) => {
        if (call.method === 'PATCH') throw Object.assign(new Error('新版本冲突'), { status: 409, code: 'conflict', current_revision: 4 });
        return ui.state.defaultReply(call);
    };
    await ui.type('writing-guided-draft', '我在本机补写的全文。'); await ui.timers();
    assert.equal(ui.field('writing-conflict').hidden, false);
    await ui.field('writing-copy-draft').fire('click');
    assert.match(ui.state.clipboard, /我在本机补写的全文/);
    assert.match(ui.state.clipboard, /我把两根手指竖起来/);
    assert.ok([...ui.storage.entries()].some(([key, value]) => key.includes('alice') && value.includes('我在本机补写的全文')));
    const gate = deferred();
    const stale = writingUI({ lesson: GUIDED_LESSON }); const old = stale.makeAttempt(data);
    stale.state.routes[prefix + '/attempts/' + old.id] = () => gate.promise;
    const opening = stale.api.openAttempt(old.id); await stale.flush(); stale.api.reset();
    stale.state.session = { token: 'bob-token', username: 'bob', subject: 'chinese', account: { username: 'bob', is_parent: false } };
    gate.resolve({ attempt: clone(old), versions: [], read_only: false }); await opening;
    assert.equal(stale.field('writing-editor').hidden, true);
    assert.equal(stale.field('writing-stage-content').textContent, '');
});

test('an enhanced-flow conflict preserves and exports material, comparison and both evidence rounds', async () => {
    const data = coachedData(7, true);
    data.coaching.evidence = {
        structure: { before: '我做了动作。', after: '我竖起两根手指。', reason: '让读者看见手指的位置。' },
        language: { before: '真好玩，真有趣。', after: '我笑得手指都放不下来。', reason: '用反应代替重复评价。' },
    };
    const { ui, attempt } = await openCoached(7, { data });
    ui.state.routes[prefix + '/attempts/' + attempt.id] = (call) => {
        if (call.method === 'PATCH') throw Object.assign(new Error('另一页面已更新'), { status: 409, code: 'conflict', current_revision: 9 });
        return ui.state.defaultReply(call);
    };
    await ui.type('writing-guided-draft', '新版流程中尚未同步的学生原文。');
    await ui.timers();
    assert.equal(ui.field('writing-conflict').hidden, false);
    const backup = JSON.parse(ui.storage.get(`itorange-writing-draft:alice:${attempt.id}`));
    assert.deepEqual(backup.data.coaching, data.coaching);
    assert.equal(backup.data.paragraphs.p_full, '新版流程中尚未同步的学生原文。');
    await ui.field('writing-copy-draft').fire('click');
    for (const text of [data.coaching.material.activity, data.coaching.material.moment, data.coaching.material.feeling,
        data.coaching.comparison.reason, ...Object.values(data.coaching.evidence).flatMap(Object.values)]) {
        assert.ok(ui.state.clipboard.includes(text), 'Conflict copying must include the student-owned teaching notes: ' + text);
    }
    assert.equal(ui.state.calls.filter((call) => call.method === 'PATCH').length, 1);
});

test('an enhanced-flow local backup can recover all teaching notes without leaking through a late account response', async () => {
    const data = coachedData(7, true);
    data.coaching.evidence.structure = { before: '原来的内容。', after: '本机记录的内容修改。', reason: '交代了猜错的原因。' };
    const first = await openCoached(7, { data });
    first.ui.state.routes[prefix + '/attempts/' + first.attempt.id] = (call) => {
        if (call.method === 'PATCH') throw new Error('连接暂时中断');
        return first.ui.state.defaultReply(call);
    };
    await first.ui.type('writing-guided-draft', '断网后学生继续写的整篇初稿。');
    await first.ui.timers();
    const recovered = await openCoached(7, { data, storage: first.ui.storage });
    assert.equal(recovered.ui.field('writing-local-recovery').hidden, false);
    await recovered.ui.field('writing-recover-local').fire('click');
    assert.equal(recovered.ui.field('writing-guided-draft').value, '断网后学生继续写的整篇初稿。');
    await recovered.ui.timers();
    assert.deepEqual(recovered.attempt.data.coaching, data.coaching);

    const gate = deferred();
    const stale = writingUI({ lesson: COACHED_LESSON });
    const old = stale.makeAttempt(data);
    stale.state.routes[prefix + '/attempts/' + old.id] = () => gate.promise;
    const opening = stale.api.openAttempt(old.id); await stale.flush();
    stale.api.reset();
    stale.state.session = { token: 'bob-token', username: 'bob', subject: 'chinese', account: { username: 'bob', is_parent: false } };
    gate.resolve({ attempt: clone(old), versions: [], read_only: false }); await opening;
    assert.equal(stale.field('writing-editor').hidden, true);
    assert.equal(stale.field('writing-stage-content').textContent, '');
    assert.equal(writes(stale).length, 0);
});

test('a parent may navigate all guided questions and help without confirming or modifying the student work', async () => {
    const { ui, attempt } = await openGuided(0, { data: guidedData(0, true), isParent: true });
    const original = clone(attempt.data);
    assert.equal(ui.field('writing-guided-customTheme').disabled, true);
    await ui.field('writing-guided-theme-fun').fire('click');
    await guidedNav(ui, 7);
    assert.equal(ui.field('writing-guided-draft').disabled, true);
    await ui.type('writing-guided-draft', '家长程序触发的代写');
    await ui.field('writing-guided-help-open').fire('click');
    await ui.field('writing-guided-help-back').fire('click');
    await ui.timers();
    assert.equal(writes(ui).length, 0);
    assert.deepEqual(attempt.data, original);
});

test('parent viewing protects the new source, comparison and revision-evidence controls from writing', async () => {
    const source = await openCoached(0, { data: coachedData(0, true), isParent: true });
    const sourceData = clone(source.attempt.data);
    await source.ui.field('writing-material-edit-source').fire('click');
    assert.equal(source.ui.field('writing-material-source-scenario').disabled, true);
    await source.ui.field('writing-material-source-scenario').fire('click');
    await guidedNav(source.ui, 5);
    assert.equal(source.ui.field('writing-comparison-dialogue').disabled, true);
    assert.equal(source.ui.field('writing-comparison-reason').disabled, true);
    await source.ui.field('writing-comparison-dialogue').fire('click');
    await source.ui.type('writing-comparison-reason', '家长触发的比较理由');
    await source.ui.timers();
    assert.equal(writes(source.ui).length, 0);
    assert.deepEqual(source.attempt.data, sourceData);

    const revision = await openCoached(8, { data: revisionData(), isParent: true });
    const revisionDataBefore = clone(revision.attempt.data);
    for (const part of ['before', 'after', 'reason']) {
        assert.equal(revision.ui.field('writing-evidence-' + part).disabled, true);
        await revision.ui.type('writing-evidence-' + part, '家长触发的修改证据');
    }
    await revision.ui.timers();
    assert.equal(writes(revision.ui).length, 0);
    assert.deepEqual(revision.attempt.data, revisionDataBefore);
});

test('an evidence save in flight preserves later edits and locks its fields during a round checkpoint', async () => {
    const data = revisionData();
    const current = data.paragraphs.p_full.replace('我比画兔子', '我举起手指比画兔子');
    const { ui, attempt } = await openCoached(8, { data });
    await ui.type('writing-paragraph-p_full', current);
    await ui.type('writing-evidence-before', '我比画兔子');
    await ui.type('writing-evidence-after', '我举起手指比画兔子');
    const saveGate = deferred(); let firstSave;
    ui.state.routes[prefix + '/attempts/' + attempt.id] = (call) => {
        if (call.method === 'PATCH' && !firstSave) { firstSave = call; return saveGate.promise; }
        return ui.state.defaultReply(call);
    };
    await ui.type('writing-evidence-reason', '先写下的一点理由。'); await ui.timers();
    await ui.type('writing-evidence-reason', '等候保存时补充：读者可以看见手指的动作。'); await ui.timers();
    assert.equal(ui.state.calls.filter((call) => call.method === 'PATCH').length, 1);
    saveGate.resolve(ui.state.defaultReply(firstSave)); await ui.flush(); await ui.timers();
    const saves = ui.state.calls.filter((call) => call.method === 'PATCH');
    assert.deepEqual(saves.map((call) => call.body.base_revision), [1, 2]);
    assert.equal(attempt.data.coaching.evidence.structure.reason, '等候保存时补充：读者可以看见手指的动作。');
    assert.equal(ui.field('writing-evidence-reason').value, '等候保存时补充：读者可以看见手指的动作。');

    const checkpointGate = deferred(); let checkpoint;
    ui.state.routes[prefix + '/attempts/' + attempt.id + '/checkpoints'] = (call) => { checkpoint = call; return checkpointGate.promise; };
    await ui.field('writing-next').fire('click');
    assert.ok(checkpoint);
    assert.equal(checkpoint.body.data.coaching.evidence.structure.reason, '等候保存时补充：读者可以看见手指的动作。');
    for (const part of ['before', 'after', 'reason']) {
        assert.equal(ui.field('writing-evidence-' + part).disabled, true);
        await ui.type('writing-evidence-' + part, '确认途中不应被接受的新证据');
    }
    checkpointGate.resolve(ui.state.defaultReply(checkpoint)); await ui.flush();
    assert.equal(attempt.data.review_round, 'language');
    assert.deepEqual(attempt.data.coaching.evidence.structure, checkpoint.body.data.coaching.evidence.structure);
});

test('nine confirmed guided questions feed the existing two revision rounds and immutable completed work', async () => {
    const data = guidedData(8, true); data.guidance.confirmed = Array.from({ length: 8 }, (_, step) => step);
    const { ui, attempt } = await openGuided(8, { data });
    const original = data.paragraphs.p_full;
    await ui.field('writing-next').fire('click');
    assert.ok(ui.field('writing-paragraph-p_full'));
    assert.equal(ui.field('writing-paragraph-p_full').value, original);
    await ui.type('writing-paragraph-p_full', original + '\n我补上了一次动作的反应。');
    await ui.type('writing-revision-note', '内容稿补上反应。');
    await ui.field('writing-next').fire('click');
    assert.match(ui.field('writing-paragraph-p_full').value, /动作的反应/);
    await ui.type('writing-paragraph-p_full', original + '\n我删掉重复评价，保留这个反应。');
    await ui.type('writing-revision-note', '表达稿删去重复。');
    await ui.field('writing-next').fire('click');
    await ui.type('writing-reflection-learned', '我能把有趣写成具体的一刻。');
    await ui.field('writing-complete').fire('click');
    assert.equal(attempt.status, 'completed');
    assert.equal(attempt.data.paragraphs.p_full, original);
    assert.match(attempt.data.final_paragraphs.p_full, /删掉重复评价/);
    assert.deepEqual([...attempt.data.guidance.confirmed].sort((a, b) => a - b), Array.from({ length: 9 }, (_, step) => step));
    assert.deepEqual(attempt.data.guidance.review, []);
    const labels = ui.state.versions.get(attempt.id).map((version) => version.label);
    assert.ok(['初稿', '内容修改稿', '修改稿', '完成作品'].every((label) => labels.includes(label)));
    await guidedNav(ui, 7);
    assert.equal(ui.field('writing-guided-draft').disabled, true);
    await ui.type('writing-guided-draft', '完成后不允许替换原稿'); await ui.timers();
    assert.equal(attempt.data.paragraphs.p_full, original);
});

test('restoring a guided snapshot previews and retains its guided answers and original canonical draft', async () => {
    const data = guidedData(0, true); data.guidance.customTheme = '历史版本中自己的主旨。';
    const { ui, attempt } = await openGuided(0, { data });
    attempt.data.guidance.customTheme = '后来保存的主旨。'; attempt.revision = 5;
    await ui.api.openAttempt(attempt.id);
    await ui.field('writing-history-open').fire('click');
    assert.match(ui.field('writing-version-preview').textContent, /历史版本中自己的主旨/);
    await ui.field('writing-version-restore').fire('click');
    await ui.field('writing-version-restore-confirm').fire('click');
    assert.equal(ui.field('writing-guided-customTheme').value, '历史版本中自己的主旨。');
    assert.equal(attempt.data.paragraphs.p_full, data.paragraphs.p_full);
    assert.equal(ui.state.versions.get(attempt.id).length, 2);
    assert.equal(attempt.revision, 6);
});

test('a slow content-to-expression checkpoint freezes old fields and carries the last accepted content into the expression draft', async () => {
    const data = guidedData(8, true);
    data.stage = 4; data.review_round = 'structure'; data.structure_started = true;
    data.structure_paragraphs = { p_full: data.paragraphs.p_full };
    const { ui, attempt } = await openGuided(8, { data });
    const finalContent = '内容确认前，我自己补全的最后一处动作和反应。';
    await ui.type('writing-paragraph-p_full', finalContent);
    await ui.type('writing-revision-note', '我补上了具体动作。');
    const gate = deferred(); let checkpoint;
    ui.state.routes[prefix + '/attempts/' + attempt.id + '/checkpoints'] = (call) => { checkpoint = call; return gate.promise; };
    await ui.field('writing-next').fire('click');
    assert.ok(checkpoint);
    assert.equal(checkpoint.body.label, '内容修改稿');
    assert.equal(checkpoint.body.data.structure_paragraphs.p_full, finalContent);
    assert.equal(ui.field('writing-paragraph-p_full').disabled, true);
    assert.equal(ui.field('writing-revision-note').disabled, true);
    await ui.type('writing-paragraph-p_full', '确认请求途中不应被接受的新段落');
    await ui.type('writing-revision-note', '请求途中不应被接受的新说明');
    await ui.timers();
    assert.equal(ui.state.calls.filter((call) => call.method === 'PATCH').length, 0);
    gate.resolve(ui.state.defaultReply(checkpoint));
    await ui.flush();
    assert.equal(attempt.data.structure_paragraphs.p_full, finalContent);
    assert.equal(attempt.data.final_paragraphs.p_full, finalContent);
    assert.equal(attempt.data.revision_notes.structure, '我补上了具体动作。');
    assert.equal(attempt.data.review_round, 'language');
    assert.equal(ui.field('writing-paragraph-p_full').value, finalContent);
    assert.equal(ui.field('writing-paragraph-p_full').disabled, false);
    assert.equal(ui.field('writing-revision-note').disabled, false);
});

test('a slow expression-to-review transition keeps the expression draft frozen until both checkpoint and position saves are acknowledged', async () => {
    const data = guidedData(8, true);
    data.stage = 4; data.review_round = 'language'; data.structure_started = data.language_started = true;
    data.structure_paragraphs = { p_full: data.paragraphs.p_full };
    data.final_paragraphs = { p_full: data.paragraphs.p_full };
    const { ui, attempt } = await openGuided(8, { data });
    const finalExpression = '表达确认前，我自己删去重复评价后的最后一稿。';
    await ui.type('writing-paragraph-p_full', finalExpression);
    await ui.type('writing-revision-note', '删去了一句重复的话。');
    const checkpointGate = deferred(); const positionGate = deferred();
    let checkpoint; let position;
    ui.state.routes[prefix + '/attempts/' + attempt.id + '/checkpoints'] = (call) => { checkpoint = call; return checkpointGate.promise; };
    ui.state.routes[prefix + '/attempts/' + attempt.id] = (call) => {
        if (call.method === 'PATCH') { position = call; return positionGate.promise; }
        return ui.state.defaultReply(call);
    };
    await ui.field('writing-next').fire('click');
    assert.ok(checkpoint);
    assert.equal(checkpoint.body.label, '修改稿');
    assert.equal(ui.field('writing-paragraph-p_full').disabled, true);
    await ui.type('writing-paragraph-p_full', '等候修改稿确认时不能污染最终稿');
    checkpointGate.resolve(ui.state.defaultReply(checkpoint));
    await ui.flush();
    assert.ok(position, 'The current position must also be persisted before moving to review');
    assert.equal(position.body.base_revision, 2);
    assert.equal(position.body.data.stage, 5);
    assert.equal(position.body.data.final_paragraphs.p_full, finalExpression);
    assert.equal(ui.field('writing-paragraph-p_full').disabled, true);
    await ui.type('writing-paragraph-p_full', '等候位置保存时也不能污染最终稿');
    positionGate.resolve(ui.state.defaultReply(position));
    await ui.flush();
    assert.equal(attempt.data.final_paragraphs.p_full, finalExpression);
    assert.equal(attempt.data.revision_notes.language, '删去了一句重复的话。');
    assert.equal(attempt.data.paragraphs.p_full, data.paragraphs.p_full);
    assert.equal(ui.field('writing-reflection-learned').disabled, false);
    assert.match(ui.field('writing-stage-content').textContent, /我自己删去重复评价后的最后一稿/);
});

function abandonControls(ui, id) {
    const card = ui.field('writing-attempt-list').querySelector(`[data-attempt-id="${id}"]`);
    assert.ok(card, 'The unfinished attempt should have its own card');
    return { card, abandon: card.querySelector('.writing-abandon'), confirmation: () => card.querySelector('.writing-abandon-confirmation') };
}

async function confirmAbandon(ui, controls) {
    await controls.abandon.fire('click');
    const confirmation = controls.confirmation();
    assert.ok(confirmation, 'Abandonment requires an inline confirmation');
    const confirm = confirmation.querySelectorAll('button').find((button) => button.textContent === '确认放弃');
    await confirm.fire('click');
    await ui.flush();
}

test('abandonment can be cancelled without posting and then removes only the chosen unfinished draft', async () => {
    const ui = writingUI({ seed: [emptyData(), emptyData()] });
    const [first, second] = [...ui.state.attempts.values()];
    const original = clone(first.data); const originalVersions = clone(ui.state.versions.get(first.id));
    const firstBackup = `itorange-writing-draft:alice:${first.id}`;
    const secondBackup = `itorange-writing-draft:alice:${second.id}`;
    ui.storage.set(firstBackup, 'first local draft'); ui.storage.set(secondBackup, 'second local draft');
    await ui.api.open();
    const controls = abandonControls(ui, first.id);
    await controls.abandon.fire('click');
    assert.match(controls.confirmation().textContent, /不再出现在.*我的练习.*已保存内容和版本会保留/);
    await controls.confirmation().querySelectorAll('button').find((button) => button.textContent === '继续保留').fire('click');
    assert.equal(controls.confirmation(), null);
    assert.equal(ui.state.calls.some((call) => call.url.endsWith('/abandon')), false);
    await confirmAbandon(ui, controls);
    const call = ui.state.calls.find((call) => call.url.endsWith('/abandon'));
    assert.deepEqual(call.body, { base_revision: 1 });
    assert.equal(call.headers['X-Writing-Flow'], 'guided-v2');
    assert.equal(first.status, 'abandoned'); assert.deepEqual(first.data, original);
    assert.deepEqual(ui.state.versions.get(first.id), originalVersions, 'The original text and versions remain saved');
    assert.equal(ui.field('writing-attempt-list').querySelector(`[data-attempt-id="${first.id}"]`), null);
    assert.ok(ui.field('writing-attempt-list').querySelector(`[data-attempt-id="${second.id}"]`));
    assert.equal(ui.storage.has(firstBackup), false); assert.equal(ui.storage.get(secondBackup), 'second local draft');
    assert.match(ui.field('writing-message').textContent, /已放弃/);
});

test('a failed abandonment preserves the confirmation and local backup so the student can retry', async () => {
    const ui = writingUI({ seed: [emptyData()] }); const attempt = [...ui.state.attempts.values()][0];
    const key = `itorange-writing-draft:alice:${attempt.id}`; ui.storage.set(key, 'unsynced draft');
    let requests = 0;
    ui.state.routes[prefix + '/attempts/' + attempt.id + '/abandon'] = (call) => {
        requests += 1;
        assert.equal(ui.storage.get(key), 'unsynced draft', 'Local data is retained until a successful response');
        if (requests === 1) throw new Error('网络暂时中断');
        return ui.state.defaultReply(call);
    };
    await ui.api.open(); const controls = abandonControls(ui, attempt.id);
    await confirmAbandon(ui, controls);
    assert.equal(attempt.status, 'in_progress'); assert.ok(controls.confirmation());
    assert.match(controls.confirmation().textContent, /网络暂时中断/);
    assert.equal(ui.storage.get(key), 'unsynced draft');
    await controls.confirmation().querySelector('.writing-abandon-confirm').fire('click'); await ui.flush();
    assert.equal(requests, 2); assert.equal(attempt.status, 'abandoned'); assert.equal(ui.storage.has(key), false);
});

test('an abandonment conflict refreshes the draft list without automatically retrying or discarding the local copy', async () => {
    const ui = writingUI({ seed: [emptyData()] }); const attempt = [...ui.state.attempts.values()][0];
    const key = `itorange-writing-draft:alice:${attempt.id}`; ui.storage.set(key, 'retain this local copy');
    ui.state.routes[prefix + '/attempts/' + attempt.id + '/abandon'] = () => {
        attempt.revision = 7;
        throw Object.assign(new Error('内容已有更新'), { status: 409, code: 'conflict', current_revision: 7 });
    };
    await ui.api.open(); await confirmAbandon(ui, abandonControls(ui, attempt.id));
    assert.equal(ui.state.calls.filter((call) => call.url.endsWith('/abandon')).length, 1);
    assert.equal(attempt.status, 'in_progress'); assert.equal(ui.storage.get(key), 'retain this local copy');
    const refreshed = abandonControls(ui, attempt.id); assert.equal(refreshed.confirmation(), null);
    assert.match(ui.field('writing-message').textContent, /未自动放弃.*核对/);
    assert.ok(ui.state.calls.filter((call) => call.url === prefix + '/attempts' && call.method === 'GET').length >= 2);
});

test('parent and completed-attempt cards do not offer abandonment', async () => {
    const parent = writingUI({ isParent: true, seed: [emptyData()] }); await parent.api.open();
    assert.equal(parent.field('writing-attempt-list').querySelector('.writing-abandon'), null);
    const student = writingUI({ seed: [completedData()] });
    [...student.state.attempts.values()][0].status = 'completed'; await student.api.open();
    assert.equal(student.field('writing-attempt-list').querySelector('.writing-abandon'), null);
    assert.equal(student.state.calls.some((call) => call.url.endsWith('/abandon')), false);
});

test('an abandonment in flight locks draft navigation and cannot affect a newly signed-in account', async () => {
    const ui = writingUI({ seed: [emptyData()] }); const attempt = [...ui.state.attempts.values()][0];
    const key = `itorange-writing-draft:alice:${attempt.id}`; ui.storage.set(key, 'alice local copy');
    const gate = deferred(); let pending;
    ui.state.routes[prefix + '/attempts/' + attempt.id + '/abandon'] = (call) => { pending = call; return gate.promise; };
    await ui.api.open(); const controls = abandonControls(ui, attempt.id);
    await controls.abandon.fire('click');
    const confirming = controls.confirmation().querySelector('.writing-abandon-confirm').fire('click'); await ui.flush();
    assert.ok(pending); assert.ok(ui.field('writing-home').querySelectorAll('button').every((button) => button.disabled));
    const calls = ui.state.calls.length;
    await ui.api.openAttempt(attempt.id);
    await ui.field('writing-lessons').querySelector('button').fire('click');
    assert.equal(ui.state.calls.length, calls, 'No second draft can be opened or started during abandonment');
    ui.api.reset(); ui.state.session = { token: 'bob-token', username: 'bob', subject: 'chinese', account: { username: 'bob', is_parent: false } };
    gate.resolve(ui.state.defaultReply(pending)); await confirming; await ui.flush();
    assert.equal(ui.storage.get(key), 'alice local copy', 'A late response must not clear another account recovery data');
    assert.equal(ui.field('writing-attempt-list').children.length, 0); assert.equal(ui.field('writing-message').textContent, '');
});

test('abandoning the active hidden draft waits for autosave and uses the acknowledged revision', async () => {
    const ui = writingUI({ seed: [emptyData()] }); const attempt = [...ui.state.attempts.values()][0];
    await ui.api.openAttempt(attempt.id);
    const gate = deferred(); let saving;
    ui.state.routes[prefix + '/attempts/' + attempt.id] = (call) => {
        if (call.method === 'PATCH' && !saving) { saving = call; return gate.promise; }
        return ui.state.defaultReply(call);
    };
    await ui.type('writing-plan-situation', '这段输入需要先保存。'); await ui.timers(); assert.ok(saving);
    await ui.api.open(); const controls = abandonControls(ui, attempt.id);
    await controls.abandon.fire('click');
    const confirming = controls.confirmation().querySelector('.writing-abandon-confirm').fire('click'); await ui.flush();
    assert.equal(ui.state.calls.some((call) => call.url.endsWith('/abandon')), false);
    assert.equal(ui.field('writing-plan-situation').disabled, true);
    await ui.type('writing-plan-situation', '不应接受放弃中的迟到输入');
    gate.resolve(ui.state.defaultReply(saving)); await confirming; await ui.flush(); await ui.timers();
    const abandoning = ui.state.calls.find((call) => call.url.endsWith('/abandon'));
    assert.deepEqual(abandoning.body, { base_revision: 2 });
    assert.equal(attempt.data.planning.situation, '这段输入需要先保存。'); assert.equal(attempt.status, 'abandoned');
    const abandonedIndex = ui.state.calls.indexOf(abandoning);
    assert.equal(ui.state.calls.slice(abandonedIndex + 1).some((call) => call.method === 'PATCH'), false, 'Autosave cannot keep writing after abandonment');
});

test('a previously abandoned attempt stays readonly even if an older response omits the readonly flag', async () => {
    const ui = writingUI({ seed: [emptyData()] }); const attempt = [...ui.state.attempts.values()][0]; attempt.status = 'abandoned';
    ui.state.routes[prefix + '/attempts/' + attempt.id] = () => ({ attempt: clone(attempt), versions: [], read_only: false });
    await ui.api.openAttempt(attempt.id);
    assert.equal(ui.field('writing-plan-situation').disabled, true); assert.match(ui.field('writing-read-only').textContent, /已放弃/);
    await ui.type('writing-plan-situation', '不应写入已放弃的练习'); await ui.timers();
    assert.equal(ui.state.calls.some((call) => call.method === 'PATCH'), false);
});
