const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'static/js/app.js'), 'utf8');

test('parent navigation keeps word browsing and the quick form adds a private pending word', async () => {
    const inputEnglish = { value: '  look   after  ', focus() {} };
    const inputChinese = { value: ' 照顾 ' };
    const form = { hidden: true, reset() { inputEnglish.value = ''; inputChinese.value = ''; } };
    const submit = { disabled: false };
    const status = { hidden: true };
    const fields = {
        'parent-add-word-form': form,
        'parent-add-word-english': inputEnglish,
        'parent-add-word-chinese': inputChinese,
        'parent-add-word-submit': submit,
        'parent-add-word-status': status,
    };
    const pages = { review: { style: {} }, discover: { style: {} }, textbook: { style: {} } };
    const context = vm.createContext({
        URLSearchParams,
        window: { location: { search: '' } },
        localStorage: { getItem: (key) => ({ token: 'parent-token', username: 'alice_parent', session_is_parent: '1' }[key] || null) },
        document: {
            getElementById: (id) => fields[id] || null,
            querySelectorAll: (selector) => {
                const page = pages[selector.match(/data-page="([a-z]+)"/)?.[1]];
                return page ? [page] : [];
            },
            addEventListener() {},
        },
    });
    vm.runInContext(source, context);
    const calls = [];
    context.apiRequest = async (endpoint, options) => {
        calls.push({ endpoint, method: options.method, body: JSON.parse(options.body) });
        return { added: 1 };
    };
    context.loadStats = () => {};
    context.loadProgress = () => {};
    context.applyParentNavMode();
    assert.equal(form.hidden, false);
    assert.equal(pages.review.style.display, 'none');
    assert.notEqual(pages.discover.style.display, 'none');
    assert.notEqual(pages.textbook.style.display, 'none');

    const event = { preventDefault() {}, currentTarget: form };
    const pending = context.addParentPendingWord(event);
    event.currentTarget = null;
    await pending;
    assert.deepEqual(calls, [{ endpoint: '/words/import-json', method: 'POST', body: [{ english: 'look after', chinese: '照顾' }] }]);
    assert.match(status.textContent, /已加入学生的待复习/);
    assert.equal(inputEnglish.value, '');
    assert.equal(submit.disabled, false);
});

test('startup renders learning before deferred rewards and requests the lightweight bootstrap', async () => {
    const elements = new Map();
    class Element {
        hidden = true;
        textContent = '';
        style = {};
        classes = new Set();
        classList = {
            add: (name) => this.classes.add(name),
            remove: (name) => this.classes.delete(name),
            toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name),
        };
        setAttribute() {}
    }
    const field = (id) => {
        if (!elements.has(id)) elements.set(id, new Element());
        return elements.get(id);
    };
    let resolveBootstrap;
    let resolveRewards;
    let idle;
    const events = [];
    const context = vm.createContext({
        URLSearchParams,
        window: { location: { search: '' } },
        localStorage: { getItem: (key) => ({ token: 'alice-token', username: 'alice' }[key] || null) },
        document: { getElementById: field, querySelector: () => new Element(), addEventListener() {} },
    });
    vm.runInContext(source, context);
    Object.assign(context, {
        apiRequest: (endpoint) => {
            assert.match(endpoint, /^\/bootstrap\?.*include_gamification=0/);
            return new Promise((resolve) => { resolveBootstrap = resolve; });
        },
        reviewQueueEndpoint: (endpoint) => `${endpoint}?new_words_first=1`,
        applyBootstrapPayload: () => events.push('bootstrap-applied'),
        applyParentNavMode() {}, startPkInvitePolling() {},
        showSection: (section) => { events.push(section); context.setReviewLoading(false); },
        runWhenIdle: (callback) => { idle = callback; },
        refreshGamification: () => {
            events.push('rewards-started');
            return new Promise((resolve) => { resolveRewards = resolve; });
        },
        refreshPkInviteIndicator: () => events.push('pk-started'),
    });
    const opening = context.showMainPage();
    assert.equal(field('review-loading-status').hidden, false);
    assert.equal(field('review-section').classes.has('is-loading'), true);
    resolveBootstrap({ summary: {}, plan: 'free', avatar_url: null, tts_capabilities: { piper: false }, gamification: null });
    await opening;
    assert.deepEqual(events, ['bootstrap-applied', 'review']);
    assert.equal(field('review-loading-status').hidden, true);
    const background = idle();
    assert.deepEqual(events, ['bootstrap-applied', 'review', 'rewards-started']);
    resolveRewards({});
    await background;
    assert.equal(events.at(-1), 'pk-started');
});
