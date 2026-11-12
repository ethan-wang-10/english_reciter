const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, 'static/js/chat-sidebar.js'), 'utf8')
    .replace(/\}\)\(\);\s*$/, 'window.chatTest = { loadInitial, loadOlder, pollChatOnce, startChatPoll, stopChatPoll, sendChat, onChatLogout, openChatSidebar, handleInboundChatMessage }; })();');

class Element {
    constructor() {
        this.children = [];
        this.dataset = {};
        this.value = '';
        this.scrollTop = 0;
        this.scrollHeight = 500;
        const classes = new Set();
        this.classList = {
            add: (...values) => values.forEach((value) => classes.add(value)),
            remove: (...values) => values.forEach((value) => classes.delete(value)),
            contains: (value) => classes.has(value),
            toggle: () => {},
        };
    }
    set innerHTML(value) { this.html = value; this.children = []; }
    get innerHTML() { return this.html || ''; }
    appendChild(child) { this.children.push(child); }
    insertBefore(child) { this.children.unshift(child); }
    get firstChild() { return this.children[0]; }
    querySelector(selector) { return selector === '.chat-msg' ? this.children[0] || null : null; }
    querySelectorAll() { return this.children; }
    setAttribute() {}
    focus() {}
}

function browser() {
    const elements = new Map();
    for (const name of ['chat-log', 'chat-sidebar', 'main-page', 'chat-input', 'chat-send', 'chat-sidebar-backdrop']) {
        elements.set(name, new Element());
    }
    elements.get('main-page').classList.add('active');
    const state = { requests: [], timers: [], listeners: new Map() };
    const context = vm.createContext({
        token: null, username: 'alice', API_BASE: '/api', AbortController,
        escapeHtml: String, escapeRegExp: String,
        window: {},
        document: {
            hidden: false, readyState: 'loading', body: new Element(),
            getElementById: (id) => elements.get(id) || null,
            querySelector: () => null,
            createElement: () => new Element(),
            addEventListener: (event, callback) => state.listeners.set(event, callback),
        },
        setTimeout: (callback, delay) => { state.timers.push({ callback, delay }); return state.timers.length; },
        clearTimeout() {},
        fetch: (url, options) => new Promise((resolve, reject) => state.requests.push({ url, options, resolve, reject })),
    });
    vm.runInContext(source, context);
    context.token = 'token-a';
    const reply = (index, messages) => state.requests[index].resolve({ ok: true, json: async () => ({ messages }) });
    return { context, state, elements, reply, chat: context.window.chatTest };
}

const message = (id) => ({ id, ts: '2026-10-29T10:00:00+08:00', from: 'bobby', body: id, mentions: [] });

test('open chat uses short polling without occupying a streaming request', async () => {
    const ui = browser();
    ui.chat.stopChatPoll();
    const opening = ui.chat.openChatSidebar();
    ui.reply(0, [message('a')]);
    await opening;
    assert.equal(ui.state.requests.length, 2);
    ui.reply(1, []);
    await new Promise(setImmediate);
    assert.ok(ui.state.requests.every((request) => request.url.includes('/chat/messages')));
    assert.ok(ui.state.timers.some((timer) => timer.delay === 3000));
});

test('older pages retain chronological order when prepended', async () => {
    const ui = browser();
    ui.chat.stopChatPoll();
    const initial = ui.chat.loadInitial();
    ui.reply(0, Array.from({ length: 50 }, (_, index) => message(`new-${index}`)));
    await initial;
    const older = ui.chat.loadOlder();
    ui.reply(1, [message('old-1'), message('old-2'), message('old-3')]);
    await older;
    assert.deepEqual(ui.elements.get('chat-log').children.slice(0, 4).map((node) => node.dataset.id),
        ['old-1', 'old-2', 'old-3', 'new-0']);
});

test('poll responses from a previous generation cannot modify a reopened history', async () => {
    const ui = browser();
    ui.chat.stopChatPoll();
    const poll = ui.chat.pollChatOnce();
    const initial = ui.chat.loadInitial();
    ui.reply(1, [message('current')]);
    await initial;
    ui.reply(0, [message('stale')]);
    await poll;
    assert.deepEqual(ui.elements.get('chat-log').children.map((node) => node.dataset.id), ['current']);
});

test('account changes discard history and send responses from the previous account', async () => {
    const ui = browser();
    ui.chat.stopChatPoll();
    const initial = ui.chat.loadInitial();
    ui.elements.get('chat-input').value = 'old draft';
    const sending = ui.chat.sendChat();
    ui.chat.onChatLogout();
    ui.context.token = 'token-b';
    ui.context.username = 'bobby';
    ui.elements.get('chat-input').value = 'new draft';
    ui.reply(0, [message('private-old')]);
    ui.state.requests[1].resolve({ ok: true, json: async () => message('sent-old') });
    await Promise.all([initial, sending]);
    assert.equal(ui.elements.get('chat-input').value, 'new draft');
    assert.deepEqual(ui.elements.get('chat-log').children, []);
});

test('hidden tabs and an initial load do not start competing polls', async () => {
    const ui = browser();
    ui.chat.stopChatPoll();
    ui.context.document.hidden = true;
    await ui.chat.pollChatOnce();
    assert.equal(ui.state.requests.length, 0);
    ui.context.document.hidden = false;
    const initial = ui.chat.loadInitial();
    ui.chat.startChatPoll();
    assert.equal(ui.state.requests.length, 1);
    ui.reply(0, []);
    await initial;
});

test('sending cannot skip messages that arrived between the last poll and the POST', async () => {
    const ui = browser();
    ui.elements.get('chat-sidebar').classList.add('chat-sidebar--open');
    const initial = ui.chat.loadInitial();
    ui.reply(0, [message('p0')]);
    await initial;
    ui.elements.get('chat-input').value = 'p2';
    const sending = ui.chat.sendChat();
    ui.state.requests[1].resolve({ ok: true, json: async () => ({ ...message('p2'), from: 'alice' }) });
    await new Promise(setImmediate);
    assert.match(ui.state.requests[2].url, /after_id=p0/);
    ui.reply(2, [message('p1'), { ...message('p2'), from: 'alice' }]);
    await sending;
    assert.deepEqual(ui.elements.get('chat-log').children.map((node) => node.dataset.id), ['p0', 'p1', 'p2']);
    const poll = ui.chat.pollChatOnce();
    assert.match(ui.state.requests[3].url, /after_id=p2/);
    ui.reply(3, []);
    await poll;
});
