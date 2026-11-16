const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, 'static/js/admin.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, 'static/index.html'), 'utf8');
const settle = () => new Promise(setImmediate);
function deferred() { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; }
function adminUI() {
    const elements = new Map();
    const state = { calls: [], token: 'admin-a', routes: [], refreshes: 0 };
    class Element {
        constructor(id) { this.id = id; this.checked = false; this.disabled = false; this.textContent = ''; this.style = {}; this.listeners = {}; }
        addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
        async fire(name) { for (const handler of this.listeners[name] || []) await handler({ target: this }); await settle(); }
    }
    for (const id of ['admin-kokoro-enabled', 'admin-kokoro-status', 'admin-save-kokoro', 'admin-refresh-kokoro', 'admin-logout', 'admin-notice']) {
        assert.ok(html.includes(`id="${id}"`), 'Admin Kokoro controls must exist in the real English page');
        elements.set(id, new Element(id));
    }
    const context = vm.createContext({
        API_BASE: '/api', document: { getElementById: (id) => elements.get(id) || null },
        sessionStorage: { getItem: (key) => key === 'adminToken' ? state.token : null,
            setItem(key, value) { if (key === 'adminToken') state.token = value; }, removeItem(key) { if (key === 'adminToken') state.token = null; } },
        refreshTtsCapabilities: () => { state.refreshes += 1; },
        fetch: async (url, options = {}) => {
            const call = { url, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : undefined, headers: options.headers };
            state.calls.push(call);
            const route = state.routes.shift();
            const result = typeof route === 'function' ? await route(call) : route || { status: 200, data: { kokoro_enabled: false, kokoro_service_ready: false, kokoro_status: 'disabled' } };
            if (result instanceof Error) throw result;
            return { ok: (result.status || 200) < 400, status: result.status || 200, json: async () => result.data || {} };
        },
        console, setTimeout, clearTimeout,
    });
    vm.runInContext(source, context);
    vm.runInContext('bindAdminKokoroOnce()', context);
    return { state, context, field: (id) => elements.get(id),
        render(config) { context.config = config; vm.runInContext('renderAdminKokoroConfig(config)', context); },
        refresh: () => vm.runInContext('refreshAdminKokoroConfig()', context),
        async change(value) { elements.get('admin-kokoro-enabled').checked = value; await elements.get('admin-kokoro-enabled').fire('change'); },
    };
}

test('English admin presents the shared Mandarin and English toggle and renders ready, disabled and fallback states', () => {
    const ui = adminUI();
    assert.equal(ui.field('admin-save-kokoro').disabled, true);
    ui.render({ kokoro_enabled: false, kokoro_service_ready: false, kokoro_status: 'disabled' });
    assert.equal(ui.field('admin-kokoro-enabled').checked, false);
    assert.match(ui.field('admin-kokoro-status').textContent, /关闭.*原有语音/);
    ui.render({ kokoro_enabled: true, kokoro_service_ready: true, kokoro_status: 'ready' });
    assert.equal(ui.field('admin-kokoro-enabled').checked, true);
    assert.match(ui.field('admin-kokoro-status').textContent, /服务可用/);
    ui.render({ kokoro_enabled: true, kokoro_service_ready: false, kokoro_status: 'unavailable' });
    assert.match(ui.field('admin-kokoro-status').textContent, /暂不可用.*备用语音/);
    assert.match(html, /统一控制英语、语文的服务器朗读/);
});

test('saving sends only kokoro_enabled with the admin bearer and refreshes current speech capability without exposing other config', async () => {
    const ui = adminUI(); ui.render({ kokoro_enabled: false, kokoro_service_ready: false });
    await ui.change(true);
    ui.state.routes.push({ data: { message: 'saved', kokoro_enabled: true, kokoro_service_ready: true, kokoro_status: 'ready' } });
    await ui.field('admin-save-kokoro').fire('click');
    assert.equal(ui.state.calls.length, 1);
    assert.deepEqual(ui.state.calls[0].body, { kokoro_enabled: true });
    assert.equal(ui.state.calls[0].method, 'PATCH');
    assert.equal(ui.state.calls[0].url, '/api/admin/config');
    assert.equal(ui.state.calls[0].headers.Authorization, 'Bearer admin-a');
    assert.equal(ui.state.refreshes, 1);
    assert.match(ui.field('admin-notice').textContent, /已保存.*下次朗读/);
    assert.equal(ui.field('admin-save-kokoro').disabled, false);
});

test('a save failure preserves the selected choice, allows retry and blocks duplicate pending saves', async () => {
    const ui = adminUI(); ui.render({ kokoro_enabled: false, kokoro_service_ready: false }); await ui.change(true);
    const gate = deferred(); ui.state.routes.push(() => gate.promise);
    const pending = ui.field('admin-save-kokoro').fire('click'); await settle();
    assert.equal(ui.field('admin-kokoro-enabled').disabled, true);
    await ui.field('admin-save-kokoro').fire('click'); assert.equal(ui.state.calls.length, 1);
    gate.resolve({ status: 503, data: { error: '无法保存配置' } }); await pending;
    assert.equal(ui.field('admin-kokoro-enabled').checked, true);
    assert.equal(ui.field('admin-save-kokoro').disabled, false);
    assert.match(ui.field('admin-kokoro-status').textContent, /尚未确认保存.*保留/);
    ui.state.routes.push({ data: { kokoro_enabled: true, kokoro_service_ready: false, kokoro_status: 'unavailable' } });
    await ui.field('admin-save-kokoro').fire('click');
    assert.match(ui.field('admin-kokoro-status').textContent, /备用语音/);
});

test('loading errors retain an existing form and a late response after logout cannot restore the old admin view', async () => {
    const ui = adminUI(); ui.render({ kokoro_enabled: false, kokoro_service_ready: false }); await ui.change(true);
    ui.state.routes.push({ status: 503, data: { error: 'config error' } }); await ui.refresh();
    assert.equal(ui.field('admin-kokoro-enabled').checked, true);
    assert.match(ui.field('admin-kokoro-status').textContent, /载入失败.*保留/);
    const gate = deferred(); ui.state.routes.push(() => gate.promise);
    const pending = ui.refresh(); await settle();
    ui.state.token = null; await ui.field('admin-logout').fire('click');
    gate.resolve({ data: { kokoro_enabled: true, kokoro_service_ready: true } }); await pending;
    assert.equal(ui.field('admin-kokoro-enabled').checked, false);
    assert.equal(ui.field('admin-save-kokoro').disabled, true);
    assert.match(ui.field('admin-kokoro-status').textContent, /重新登录/);
    ui.state.token = 'admin-b'; vm.runInContext('bindAdminKokoroOnce()', ui.context);
    ui.state.routes.push({ data: { kokoro_enabled: false, kokoro_service_ready: false } }); await ui.refresh();
    assert.equal(ui.field('admin-kokoro-enabled').checked, false);
    assert.equal(ui.field('admin-save-kokoro').disabled, false);
});

test('an expired administrator token cannot leave the controls looking like an unfinished successful save', async () => {
    const ui = adminUI(); ui.render({ kokoro_enabled: false, kokoro_service_ready: false }); await ui.change(true);
    ui.state.routes.push({ status: 401, data: { error: 'expired' } });
    await ui.field('admin-save-kokoro').fire('click');
    assert.equal(ui.state.token, null);
    assert.equal(ui.field('admin-save-kokoro').disabled, true);
    assert.match(ui.field('admin-kokoro-status').textContent, /登录已失效.*重新登录/);
    assert.equal(ui.state.refreshes, 0);
});
