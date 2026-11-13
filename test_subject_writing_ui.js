const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

const html = fs.readFileSync(path.join(__dirname, 'static/subject.html'), 'utf8');
const source = fs.readFileSync(path.join(__dirname, 'static/js/subject-writing.js'), 'utf8');
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

function writingUI({ storage = new Map(), isParent = false, seed = [] } = {}) {
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

    function makeAttempt(data = emptyData(), suffix = String(state.attempts.size + 1)) {
        const id = 'writing_' + suffix.padStart(32, '0');
        const attempt = { id, lesson_id: state.lesson.id, lesson_snapshot: clone(state.lesson), data: clone(data), revision: 1,
            status: 'in_progress', created_at: '2026-11-13T10:00:00+08:00', updated_at: '2026-11-13T10:00:00+08:00', completed_at: null };
        state.attempts.set(id, attempt);
        state.versions.set(id, [{ id: 'version_' + suffix.padStart(32, '0'), label: '开始练习', revision: 1, created_at: attempt.created_at, data: clone(data), lesson_snapshot: clone(state.lesson) }]);
        return attempt;
    }
    seed.forEach((data, index) => makeAttempt(data, String(index + 1)));
    const versionSummaries = (id) => state.versions.get(id).map(({ data, lesson_snapshot, ...summary }) => summary);
    function payload(attempt) { return { attempt: clone(attempt), versions: clone(versionSummaries(attempt.id)), read_only: state.session.account.is_parent }; }
    function defaultReply(call) {
        const relative = call.url.slice(prefix.length);
        if (relative === '/lessons') return { items: [clone(state.lesson)], count: 1 };
        if (relative === '/lessons/' + state.lesson.id) return { lesson: clone(state.lesson) };
        if (relative === '/attempts' && call.method === 'GET') return { items: [...state.attempts.values()].map((attempt) => ({ id: attempt.id, lesson_id: attempt.lesson_id, title: attempt.lesson_snapshot.title, lesson_title: attempt.lesson_snapshot.title, stage: attempt.data.stage, revision: attempt.revision, status: attempt.status, updated_at: attempt.updated_at, completed_at: attempt.completed_at })), count: state.attempts.size, read_only: state.session.account.is_parent };
        if (relative === '/attempts' && call.method === 'POST') return payload(makeAttempt());
        const match = relative.match(/^\/attempts\/(writing_[a-f0-9]{32})(?:\/(.*))?$/); assert.ok(match, 'Unexpected writing route: ' + call.url);
        const attempt = state.attempts.get(match[1]); assert.ok(attempt, 'Attempt missing: ' + match[1]);
        const action = match[2];
        if (call.method === 'GET' && !action) return payload(attempt);
        if (call.method === 'GET' && action?.startsWith('versions/')) return { version: clone(state.versions.get(attempt.id).find((version) => version.id === action.slice(9))) };
        assert.equal(call.body.base_revision, attempt.revision, 'Writing saves must use the latest server revision');
        attempt.revision += 1;
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
            const call = { url, method: options.method || 'GET', body: typeof options.body === 'string' ? JSON.parse(options.body) : clone(options.body) };
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
    return { state, storage, context, field, api: window.subjectWriting, root, makeAttempt,
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
