const assert = require('node:assert/strict');
const { webcrypto } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'static/js/admin.js'), 'utf8');
const storageKey = 'adminQuestionAuthoringDraftV1';

function workbench(initialStorage = {}) {
    const storage = new Map(Object.entries({ adminToken: 'secret-admin-token', ...initialStorage }));
    const elements = new Map();
    const fields = [
        'kind', 'worker-id', 'level', 'limit', 'ttl', 'request-id', 'job-id', 'submission-id',
        'input', 'output', 'controls', 'status', 'pending', 'claim', 'get', 'renew', 'release', 'submit', 'new',
    ];
    for (const id of [...fields.map((field) => `admin-authoring-${field}`), 'admin-logout']) {
        elements.set(id, {
            value: '', textContent: '', disabled: false, dataset: {}, listeners: new Map(),
            addEventListener(event, handler) {
                if (!this.listeners.has(event)) this.listeners.set(event, []);
                this.listeners.get(event).push(handler);
            },
            async dispatch(event) {
                for (const handler of this.listeners.get(event) || []) await handler();
            },
        });
    }
    for (const [field, value] of Object.entries({ kind: 'generation', level: '高中', limit: '3', ttl: '3600' })) {
        elements.get(`admin-authoring-${field}`).value = value;
    }
    const calls = [];
    const state = {
        respond: () => ({ ok: true, status: 200, json: async () => ({ job_id: 'job-a', status: 'active', items: [] }) }),
    };
    const context = vm.createContext({
        crypto: webcrypto, Uint8Array, TextEncoder, URLSearchParams, API_BASE: '/api',
        document: { getElementById: (id) => elements.get(id) || null },
        sessionStorage: {
            getItem: (key) => storage.get(key) || null,
            setItem: (key, value) => storage.set(key, value),
            removeItem: (key) => storage.delete(key),
        },
        fetch(url, options) {
            calls.push({ url, options });
            return state.respond(url, options);
        },
    });
    vm.runInContext(source, context);
    context.bindAdminAuthoringOnce();
    const field = (name) => elements.get(`admin-authoring-${name}`);
    field('worker-id').value = 'generator-a';
    return { context, field, storage, calls, state, elements };
}

test('claim uses current browser token and preserves explicit claim ID', async () => {
    const ui = workbench();
    ui.field('request-id').value = 'claim-existing-a';
    await ui.context.runAdminAuthoringAction('claim');
    assert.equal(ui.calls.length, 1);
    assert.equal(ui.calls[0].url, '/api/admin/gaokao/authoring/claims');
    assert.equal(ui.calls[0].options.headers.Authorization, 'Bearer secret-admin-token');
    assert.equal(ui.calls[0].options.redirect, 'error');
    assert.deepEqual(JSON.parse(ui.calls[0].options.body), {
        kind: 'generation', worker_id: 'generator-a', level: '高中', limit: 3,
        request_id: 'claim-existing-a', ttl_seconds: 3600,
    });
    assert.equal(ui.field('job-id').value, 'job-a');
    assert.equal(ui.field('request-id').value, 'claim-existing-a');
    assert.equal(ui.field('controls').disabled, false);
    assert.ok(!ui.field('output').value.includes('secret-admin-token'));
    assert.ok(!ui.storage.get(storageKey).includes('secret-admin-token'));
});

test('pending and existing-job requests use the selected worker and correct paths', async () => {
    const ui = workbench();
    ui.field('kind').value = 'context_blind';
    ui.field('worker-id').value = 'reviewer-a';
    await ui.context.runAdminAuthoringAction('pending');
    const query = new URL(ui.calls[0].url, 'https://example.test').searchParams;
    assert.equal(query.get('kind'), 'context_blind');
    assert.equal(query.get('worker_id'), 'reviewer-a');
    ui.field('job-id').value = 'job-a';
    await ui.context.runAdminAuthoringAction('get');
    assert.equal(ui.calls[1].url, '/api/admin/gaokao/authoring/claims/job-a?worker_id=reviewer-a');
    await ui.context.runAdminAuthoringAction('renew');
    assert.deepEqual(JSON.parse(ui.calls[2].options.body), { worker_id: 'reviewer-a', ttl_seconds: 3600 });
    await ui.context.runAdminAuthoringAction('release');
    assert.equal(ui.calls[3].url, '/api/admin/gaokao/authoring/claims/job-a/release');
    assert.deepEqual(JSON.parse(ui.calls[3].options.body), { worker_id: 'reviewer-a' });
});

test('duplicate clicks cannot issue a second in-flight request', async () => {
    const ui = workbench();
    let finish;
    ui.state.respond = () => new Promise((resolve) => { finish = resolve; });
    const pending = ui.context.runAdminAuthoringAction('claim');
    assert.equal(ui.field('controls').disabled, true);
    await ui.context.runAdminAuthoringAction('claim');
    assert.equal(ui.calls.length, 1);
    finish({ ok: true, status: 200, json: async () => ({ job_id: 'job-a', items: [] }) });
    await pending;
    assert.equal(ui.field('controls').disabled, false);
});

test('network failure and page restoration keep IDs and JSON draft', async () => {
    const ui = workbench();
    ui.field('job-id').value = 'job-a';
    const claimId = ui.field('request-id').value;
    const submissionId = ui.field('submission-id').value;
    const input = JSON.stringify({ items: [{ item_id: 'item-a', result: { english: 'abandon' } }] });
    ui.field('input').value = input;
    ui.state.respond = () => { throw new Error('network unavailable'); };
    await ui.context.runAdminAuthoringAction('submit');
    assert.equal(ui.calls.length, 1);
    assert.equal(ui.field('request-id').value, claimId);
    assert.equal(ui.field('submission-id').value, submissionId);
    assert.equal(ui.field('input').value, input);
    assert.equal(ui.field('status').dataset.state, 'error');
    const restored = workbench(Object.fromEntries(ui.storage));
    assert.equal(restored.field('request-id').value, claimId);
    assert.equal(restored.field('submission-id').value, submissionId);
    assert.equal(restored.field('input').value, input);
    assert.equal(restored.field('job-id').value, 'job-a');
});

test('submit preserves whole batch and keeps completed receipt available for retry', async () => {
    const ui = workbench();
    ui.field('job-id').value = 'job-a';
    ui.field('submission-id').value = 'submission-a';
    const items = [1, 2].map((number) => ({ item_id: `item-${number}`, result: { english: `word-${number}` } }));
    ui.field('input').value = JSON.stringify({ items });
    ui.state.respond = () => ({ ok: true, status: 200, json: async () => ({ job_id: 'job-a', status: 'completed', items }) });
    await ui.context.runAdminAuthoringAction('submit');
    assert.deepEqual(JSON.parse(ui.calls[0].options.body), { worker_id: 'generator-a', submission_id: 'submission-a', items });
    assert.equal(ui.field('submission-id').value, 'submission-a');
    assert.equal(ui.field('job-id').value, 'job-a');
    assert.equal(JSON.parse(ui.field('output').value).status, 'completed');
});

test('malformed, duplicate, and oversized submission JSON never calls the API', async () => {
    const ui = workbench();
    ui.field('job-id').value = 'job-a';
    const id = ui.field('submission-id').value;
    const invalid = [
        '{invalid', JSON.stringify({ items: [] }), JSON.stringify({ items: [], approved: true }),
        JSON.stringify({ items: [{ item_id: 'a', result: [] }] }),
        JSON.stringify({ items: [{ item_id: 'a', result: {} }, { item_id: 'a', result: {} }] }),
        JSON.stringify({ items: [{ item_id: 'a', result: { text: 'x'.repeat(256 * 1024) } }] }),
    ];
    for (const input of invalid) {
        ui.field('input').value = input;
        await ui.context.runAdminAuthoringAction('submit');
        assert.equal(ui.field('input').value, input);
        assert.equal(ui.field('submission-id').value, id);
        assert.equal(ui.field('status').dataset.state, 'error');
    }
    assert.equal(ui.calls.length, 0);
});

test('only explicit new batch replaces IDs and clears result draft', async () => {
    const ui = workbench();
    const claimId = ui.field('request-id').value;
    const submissionId = ui.field('submission-id').value;
    ui.field('job-id').value = 'job-a';
    ui.field('output').value = 'prior receipt';
    await ui.field('new').dispatch('click');
    assert.notEqual(ui.field('request-id').value, claimId);
    assert.notEqual(ui.field('submission-id').value, submissionId);
    assert.equal(ui.field('job-id').value, '');
    assert.equal(ui.field('output').value, '');
    assert.equal(ui.field('worker-id').value, 'generator-a');
    assert.deepEqual(JSON.parse(ui.field('input').value), { items: [] });
    assert.equal(ui.calls.length, 0);
});

test('HTTP 401 redacts captured token after auth helper clears the session token', async () => {
    const ui = workbench();
    ui.state.respond = () => ({ ok: false, status: 401, json: async () => ({ error: 'expired secret-admin-token' }) });
    await ui.context.runAdminAuthoringAction('claim');
    assert.equal(ui.storage.has('adminToken'), false);
    assert.ok(ui.storage.has(storageKey));
    assert.ok(!ui.field('output').value.includes('secret-admin-token'));
    assert.ok(!ui.field('status').textContent.includes('secret-admin-token'));
    assert.ok(!ui.storage.get(storageKey).includes('secret-admin-token'));
});

test('logout clears drafts and an old in-flight response cannot restore them', async () => {
    const ui = workbench();
    let finish;
    ui.state.respond = () => new Promise((resolve) => { finish = resolve; });
    const pending = ui.context.runAdminAuthoringAction('claim');
    await ui.elements.get('admin-logout').dispatch('click');
    assert.equal(ui.storage.has(storageKey), false);
    assert.equal(ui.field('worker-id').value, '');
    finish({ ok: true, status: 200, json: async () => ({ job_id: 'old-job', items: [] }) });
    await pending;
    assert.equal(ui.storage.has(storageKey), false);
    assert.equal(ui.field('job-id').value, '');
    assert.equal(ui.field('output').value, '');
});

test('binding twice does not register duplicate command handlers', async () => {
    const ui = workbench();
    ui.context.bindAdminAuthoringOnce();
    await ui.field('claim').dispatch('click');
    assert.equal(ui.calls.length, 1);
});
