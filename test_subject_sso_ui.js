const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { createHash, webcrypto } = require('node:crypto');

const source = fs.readFileSync(path.join(__dirname, 'static/js/subject-sso.js'), 'utf8');
const PENDING = 'earthturtle_subject_sso_pending';
const SUBJECT_TOKEN = 'earthturtle_subject_token';
const SUBJECT_USERNAME = 'earthturtle_subject_username';
const CODE = 'c'.repeat(42) + 'A';
const VERIFIER = 'v'.repeat(43);
const STATE = 's'.repeat(43);
const SOURCE_TOKEN = 'long-lived-source-bearer-secret';
const ISSUED_TOKEN = 'long-lived-new-bearer-secret';
const NOW = 1794556800000;
const sites = {
    english: { id: 'english', name: '英语', url: 'https://english.example.test/' },
    chinese: { id: 'chinese', name: '语文', url: 'https://chinese.example.test/' },
    math: { id: 'math', name: '数学', url: 'https://math.example.test/' },
};

function config(mode, current = 'chinese', extra = {}) {
    return { mode, current_site: current, sites, ...extra };
}

function storage(values = new Map()) {
    return {
        values,
        getItem: (key) => values.has(key) ? values.get(key) : null,
        setItem: (key, value) => values.set(key, String(value)),
        removeItem: (key) => values.delete(key),
    };
}

function pending(extra = {}) {
    return { state: STATE, verifier: VERIFIER, source: 'english', target: 'chinese', createdAt: NOW, ...extra };
}

function response(data, status = 200) {
    return { ok: status >= 200 && status < 300, status, json: async () => data };
}

function ui(options = {}) {
    const cfg = options.config || null;
    const local = storage(options.local || new Map());
    const session = storage(options.session || new Map());
    const events = [], requests = [], navigation = [];
    const fields = new Map([
        ['sso-title', { textContent: '正在切换学科' }],
        ['sso-status', { textContent: 'loading' }],
        ['sso-return', { href: '/', hidden: true }],
    ]);
    if (cfg) fields.set('subject-sso-config', { textContent: JSON.stringify(cfg) });
    const initial = options.url || new URL('/auth/subject-' + (cfg?.mode || 'start'), sites[cfg?.current_site || 'english'].url).href;
    let active = new URL(initial);
    const location = {
        get href() { return active.href; },
        get search() { return active.search; },
        get pathname() { return active.pathname; },
        replace(url) { active = new URL(url, active); navigation.push({ method: 'replace', url: active.href }); events.push('replace'); },
        assign(url) { active = new URL(url, active); navigation.push({ method: 'assign', url: active.href }); events.push('assign'); },
    };
    const history = {
        replaceState(_state, _unused, url) {
            active = new URL(url, active);
            events.push('clean-url');
        },
    };
    const context = vm.createContext({
        window: {},
        document: { getElementById: (id) => fields.get(id) || null },
        localStorage: local, sessionStorage: session, location, history,
        crypto: options.crypto === undefined ? webcrypto : options.crypto,
        TextEncoder, Uint8Array, URL, URLSearchParams,
        btoa: (value) => Buffer.from(value, 'binary').toString('base64'),
        Date: class extends Date { static now() { return options.now === undefined ? NOW : options.now; } },
        fetch: async (endpoint, requestOptions = {}) => {
            const entry = { endpoint, options: requestOptions,
                body: requestOptions.body ? JSON.parse(requestOptions.body) : null,
                urlAtRequest: active.href, pendingAtRequest: session.getItem(PENDING) };
            requests.push(entry);
            events.push('fetch:' + endpoint);
            if (options.fetch) return options.fetch(entry);
            if (endpoint === '/api/auth/sso/sites') return response({ sites, current_site: options.currentSite || 'english' });
            if (endpoint === '/api/auth/sso/ticket') return response({ code: CODE, expires_in: 60 }, 201);
            if (endpoint === '/api/auth/sso/exchange') return response(options.issuedSession || {
                access_token: ISSUED_TOKEN, username: 'alice', is_parent: false, child_username: null,
            });
            throw new Error('Unexpected request: ' + endpoint);
        },
    });
    vm.runInContext(source, context);
    return { api: context.window.subjectSSO, local, session, requests, navigation, events,
        location, field: (id) => fields.get(id),
        async settle() {
            const deadline = Date.now() + 2000;
            while (!navigation.length && fields.get('sso-status').textContent === 'loading') {
                assert.ok(Date.now() < deadline, 'SSO bridge did not navigate or report its error');
                await new Promise(setImmediate);
            }
        },
    };
}

function callbackUI(options = {}) {
    const target = options.target || 'chinese';
    return ui({ config: config('callback', target),
        session: new Map([[PENDING, JSON.stringify(pending({ target }))]]),
        url: sites[target].url + 'auth/subject-callback?code=' + CODE + '&state=' + STATE,
        ...options });
}

function assertNoSessionInURL(navigation) {
    for (const item of navigation) {
        assert.equal(item.url.includes(SOURCE_TOKEN), false);
        assert.equal(item.url.includes(ISSUED_TOKEN), false);
        assert.equal(new URL(item.url).searchParams.has('access_token'), false);
        assert.equal(new URL(item.url).searchParams.has('token'), false);
    }
}

test('target start creates real random PKCE/state and redirects only to the configured source', async () => {
    const view = ui({ config: config('start', 'math', { source: 'english' }),
        local: new Map([[SUBJECT_TOKEN, SOURCE_TOKEN]]) });
    await view.settle();
    assert.equal(view.requests.length, 0);
    const stored = JSON.parse(view.session.getItem(PENDING));
    assert.match(stored.state, /^[A-Za-z0-9_-]{43}$/);
    assert.match(stored.verifier, /^[A-Za-z0-9_-]{43}$/);
    assert.notEqual(stored.state, stored.verifier);
    assert.equal(stored.target, 'math');
    assert.equal(stored.source, 'english');
    assert.equal(stored.createdAt, NOW);
    const target = new URL(view.navigation[0].url);
    assert.equal(target.origin, new URL(sites.english.url).origin);
    assert.equal(target.pathname, '/auth/subject-authorize');
    assert.equal(target.searchParams.get('target'), 'math');
    assert.equal(target.searchParams.get('state'), stored.state);
    assert.equal(target.searchParams.get('code_challenge'), createHash('sha256').update(stored.verifier).digest('base64url'));
    assert.equal(target.searchParams.has('code_verifier'), false);
    assertNoSessionInURL(view.navigation);
    const another = ui({ config: config('start', 'math', { source: 'english' }) });
    await another.settle();
    assert.notEqual(JSON.parse(another.session.getItem(PENDING)).state, stored.state);
});

test('start fails visibly when secure digest support is unavailable', async () => {
    const view = ui({ config: config('start', 'math', { source: 'english' }), crypto: { subtle: null } });
    await view.settle();
    assert.equal(view.navigation.length, 0);
    assert.match(view.field('sso-status').textContent, /HTTPS/);
    assert.equal(view.field('sso-return').href, sites.math.url);
    assert.equal(view.field('sso-return').hidden, false);
});

test('start does not redirect when the browser cannot save its pending verifier', async () => {
    class UnavailableStorage extends Map {
        set() { throw new Error('浏览器无法保存登录切换状态'); }
    }
    const view = ui({ config: config('start', 'math', { source: 'english' }), session: new UnavailableStorage() });
    await view.settle();
    assert.equal(view.navigation.length, 0);
    assert.equal(view.requests.length, 0);
    assert.match(view.field('sso-status').textContent, /无法保存/);
});

for (const current of ['english', 'chinese', 'math']) {
    test(`authorize on ${current} sends its own bearer only in the header and redirects with code/state`, async () => {
        const target = current === 'math' ? 'english' : 'math';
        const challenge = createHash('sha256').update(VERIFIER).digest('base64url');
        const local = new Map([['token', current === 'english' ? SOURCE_TOKEN : 'unrelated-English-session'],
            [SUBJECT_TOKEN, current === 'english' ? 'unrelated-subject-session' : SOURCE_TOKEN]]);
        const view = ui({ config: config('authorize', current, { target, state: STATE, code_challenge: challenge }), local });
        await view.settle();
        assert.equal(view.requests.length, 1);
        const request = view.requests[0];
        assert.equal(request.endpoint, '/api/auth/sso/ticket');
        assert.equal(request.options.headers.Authorization, 'Bearer ' + SOURCE_TOKEN);
        assert.equal(request.options.credentials, 'omit');
        assert.deepEqual(request.body, { target, code_challenge: challenge });
        const callback = new URL(view.navigation[0].url);
        assert.equal(callback.origin, new URL(sites[target].url).origin);
        assert.equal(callback.pathname, '/auth/subject-callback');
        assert.deepEqual([...callback.searchParams.keys()].sort(), ['code', 'state']);
        assert.equal(callback.searchParams.get('code'), CODE);
        assert.equal(callback.searchParams.get('state'), STATE);
        assertNoSessionInURL(view.navigation);
    });
}

test('authorize with no source login makes no network request and offers its own canonical home', async () => {
    const view = ui({ config: config('authorize', 'english', { target: 'math', state: STATE, code_challenge: 'a'.repeat(43) }) });
    await view.settle();
    assert.equal(view.requests.length, 0);
    assert.equal(view.navigation.length, 0);
    assert.match(view.field('sso-status').textContent, /登录已失效/);
    assert.equal(view.field('sso-return').href, sites.english.url);
});

test('authorize refuses an invalid ticket returned by the server without constructing a callback', async () => {
    const view = ui({ config: config('authorize', 'english', { target: 'math', state: STATE, code_challenge: 'a'.repeat(43) }),
        local: new Map([['token', SOURCE_TOKEN]]), fetch: async () => response({ code: SOURCE_TOKEN }) });
    await view.settle();
    assert.equal(view.navigation.length, 0);
    assert.match(view.field('sso-status').textContent, /返回无效/);
});

test('callback removes code from the URL and consumes pending state before its exchange request', async () => {
    const view = callbackUI();
    await view.settle();
    assert.equal(view.events[0], 'clean-url');
    assert.equal(view.requests.length, 1);
    const request = view.requests[0];
    assert.equal(request.endpoint, '/api/auth/sso/exchange');
    assert.equal(new URL(request.urlAtRequest).search, '');
    assert.equal(request.pendingAtRequest, null);
    assert.deepEqual(request.body, { code: CODE, code_verifier: VERIFIER, target: 'chinese' });
    assert.equal(request.options.headers.Authorization, undefined);
    assert.equal(view.session.getItem(PENDING), null);
    assert.equal(view.local.getItem(SUBJECT_TOKEN), ISSUED_TOKEN);
    assert.equal(view.local.getItem(SUBJECT_USERNAME), 'alice');
    assert.equal(view.local.getItem('token'), null);
    assert.equal(view.navigation[0].url, sites.chinese.url);
    assertNoSessionInURL(view.navigation);
});

const invalidPending = [
    ['missing pending state', null],
    ['different state', pending({ state: 'x'.repeat(43) })],
    ['stale state', pending({ createdAt: NOW - 300001 })],
    ['future state', pending({ createdAt: NOW + 1001 })],
    ['wrong target', pending({ target: 'math' })],
    ['unknown source', pending({ source: 'not-a-site' })],
    ['missing verifier', pending({ verifier: null })],
    ['malformed timestamp', pending({ createdAt: 'now' })],
];
for (const [name, value] of invalidPending) {
    test(`callback rejects ${name} before exchanging or persisting credentials`, async () => {
        const session = new Map(value ? [[PENDING, JSON.stringify(value)]] : []);
        const view = callbackUI({ session });
        await view.settle();
        assert.equal(view.events[0], 'clean-url');
        assert.equal(view.location.search, '');
        assert.equal(view.requests.length, 0);
        assert.equal(view.navigation.length, 0);
        assert.equal(view.local.getItem(SUBJECT_TOKEN), null);
        assert.match(view.field('sso-status').textContent, /校验失败/);
    });
}

for (const search of ['?code=' + CODE, '?state=' + STATE, '?code=invalid&state=' + STATE]) {
    test(`callback rejects invalid callback parameters ${search} before exchange`, async () => {
        const view = callbackUI({ url: sites.chinese.url + 'auth/subject-callback' + search });
        await view.settle();
        assert.equal(view.requests.length, 0);
        assert.equal(view.location.search, '');
        assert.match(view.field('sso-status').textContent, /校验失败/);
    });
}

test('a callback reload cannot exchange again after pending state was consumed', async () => {
    const first = callbackUI();
    await first.settle();
    assert.equal(first.requests.length, 1);
    const reloaded = callbackUI({ session: first.session.values });
    await reloaded.settle();
    assert.equal(reloaded.requests.length, 0);
    assert.match(reloaded.field('sso-status').textContent, /校验失败/);
});

test('English callback restores parent flags while keeping unrelated subject-origin keys untouched', async () => {
    const local = new Map([[SUBJECT_TOKEN, 'unrelated-subject-session']]);
    const view = callbackUI({ target: 'english', config: config('callback', 'english'), local,
        session: new Map([[PENDING, JSON.stringify(pending({ target: 'english', source: 'math' }))]]),
        issuedSession: { access_token: ISSUED_TOKEN, username: 'alice_parent', is_parent: true, child_username: 'alice' } });
    await view.settle();
    assert.equal(view.local.getItem('token'), ISSUED_TOKEN);
    assert.equal(view.local.getItem('username'), 'alice_parent');
    assert.equal(view.local.getItem('session_is_parent'), '1');
    assert.equal(view.local.getItem('session_child_username'), 'alice');
    assert.equal(view.local.getItem(SUBJECT_TOKEN), 'unrelated-subject-session');
    assert.equal(view.navigation[0].url, sites.english.url);
    assertNoSessionInURL(view.navigation);
});

test('English student callback clears parent flags left by a previous account', async () => {
    const local = new Map([['session_is_parent', '1'], ['session_child_username', 'previous-child']]);
    const view = callbackUI({ target: 'english', config: config('callback', 'english'), local,
        session: new Map([[PENDING, JSON.stringify(pending({ target: 'english', source: 'math' }))]]) });
    await view.settle();
    assert.equal(view.local.getItem('session_is_parent'), null);
    assert.equal(view.local.getItem('session_child_username'), null);
    assert.equal(view.local.getItem('username'), 'alice');
});

test('exchange rejection consumes local pending state but leaves existing target credentials intact', async () => {
    const local = new Map([[SUBJECT_TOKEN, 'existing-target-session'], [SUBJECT_USERNAME, 'bob']]);
    const view = callbackUI({ local, fetch: async () => response({ error: '登录切换已失效' }, 400) });
    await view.settle();
    assert.equal(view.session.getItem(PENDING), null);
    assert.equal(view.local.getItem(SUBJECT_TOKEN), 'existing-target-session');
    assert.equal(view.local.getItem(SUBJECT_USERNAME), 'bob');
    assert.equal(view.navigation.length, 0);
    assert.match(view.field('sso-status').textContent, /已失效/);
});

test('callback does not save incomplete or malformed session responses', async () => {
    const view = callbackUI({ issuedSession: { access_token: ISSUED_TOKEN } });
    await view.settle();
    assert.equal(view.local.getItem(SUBJECT_TOKEN), null);
    assert.equal(view.navigation.length, 0);
    assert.match(view.field('sso-status').textContent, /返回无效/);
});

for (const target of ['math', sites.math.url, sites.math.url.replace(/\/$/, '')]) {
    test(`navigate accepts configured target ${target} and carries only its source id`, async () => {
        const view = ui();
        await view.api.navigate(target);
        assert.equal(view.requests[0].endpoint, '/api/auth/sso/sites');
        const destination = new URL(view.navigation[0].url);
        assert.equal(destination.origin, new URL(sites.math.url).origin);
        assert.equal(destination.pathname, '/auth/subject-start');
        assert.deepEqual([...destination.searchParams], [['from', 'english']]);
        assertNoSessionInURL(view.navigation);
    });
}

test('navigating to the current configured site simply opens its canonical home', async () => {
    const view = ui({ currentSite: 'math' });
    await view.api.navigate('math');
    assert.deepEqual(view.navigation, [{ method: 'assign', url: sites.math.url }]);
});

for (const target of ['https://attacker.example/', '//attacker.example', 'javascript:alert(1)', 'https://math.example.test/evil', 'unknown']) {
    test(`navigate rejects non-allowlisted target ${target}`, async () => {
        const view = ui();
        await assert.rejects(view.api.navigate(target), /目标学科站点无效/);
        assert.equal(view.navigation.length, 0);
    });
}

test('sites lookup errors and malformed JSON never navigate or expose credentials', async () => {
    const denied = ui({ fetch: async () => response({ error: '站点配置暂不可用' }, 503) });
    await assert.rejects(denied.api.navigate('math'), /站点配置暂不可用/);
    assert.equal(denied.navigation.length, 0);
    const broken = ui({ fetch: async () => ({ ok: true, json: async () => { throw new Error('not json'); } }) });
    await assert.rejects(broken.api.navigate('math'), /登录服务暂不可用/);
    assert.equal(broken.navigation.length, 0);
});

for (const [sourceSite, targetSite] of [['english', 'chinese'], ['chinese', 'math'], ['math', 'english']]) {
    test(`complete ${sourceSite} to ${targetSite} handoff retains only target state across source navigation`, async () => {
        const targetStart = ui({ config: config('start', targetSite, { source: sourceSite }) });
        await targetStart.settle();
        const authorizeURL = new URL(targetStart.navigation[0].url);
        const sourceLocal = new Map([[sourceSite === 'english' ? 'token' : SUBJECT_TOKEN, SOURCE_TOKEN]]);
        const sourceAuthorize = ui({ config: config('authorize', sourceSite, {
            target: authorizeURL.searchParams.get('target'),
            state: authorizeURL.searchParams.get('state'),
            code_challenge: authorizeURL.searchParams.get('code_challenge'),
        }), local: sourceLocal, url: authorizeURL.href });
        await sourceAuthorize.settle();
        assert.equal(sourceAuthorize.session.getItem(PENDING), null);
        const targetCallback = ui({ config: config('callback', targetSite),
            session: targetStart.session.values, url: sourceAuthorize.navigation[0].url });
        await targetCallback.settle();
        assert.equal(targetCallback.requests[0].endpoint, '/api/auth/sso/exchange');
        assert.equal(createHash('sha256').update(targetCallback.requests[0].body.code_verifier).digest('base64url'),
            authorizeURL.searchParams.get('code_challenge'));
        assert.equal(targetCallback.local.getItem(targetSite === 'english' ? 'token' : SUBJECT_TOKEN), ISSUED_TOKEN);
        assert.equal(targetCallback.local.getItem(targetSite === 'english' ? 'username' : SUBJECT_USERNAME), 'alice');
        assert.equal(sourceAuthorize.local.getItem(sourceSite === 'english' ? 'token' : SUBJECT_TOKEN), SOURCE_TOKEN);
        assert.equal(targetCallback.session.getItem(PENDING), null);
        assert.equal(targetCallback.navigation[0].url, sites[targetSite].url);
        assertNoSessionInURL([...targetStart.navigation, ...sourceAuthorize.navigation, ...targetCallback.navigation]);
    });
}
