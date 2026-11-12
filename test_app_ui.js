const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'static/js/app.js'), 'utf8');
const textbookSource = fs.readFileSync(path.join(__dirname, 'static/js/textbook.js'), 'utf8');
const sourceParts = [...source.matchAll(/^\/\/ Source: (.+)$/gm)]
    .map((match) => fs.readFileSync(path.join(__dirname, match[1]), 'utf8'));

class Element {
    constructor() {
        this.nodes = [];
        this.listeners = new Map();
        this.style = {};
        this.dataset = {};
        this.isConnected = true;
        this.classList = { add() {}, remove() {}, contains: () => false, toggle() {} };
    }
    set innerHTML(value) { this.nodes = [value]; this.generatedButton = null; }
    get innerHTML() { return this.nodes.map((node) => typeof node === 'string' ? node : node.innerHTML).join(''); }
    appendChild(node) { node.parentNode = this; this.nodes.push(node); }
    insertAdjacentHTML(position, html) {
        if (position === 'beforeend') this.nodes.push(html);
        else {
            assert.equal(position, 'beforebegin');
            this.parentNode.nodes.splice(this.parentNode.nodes.indexOf(this), 0, html);
        }
    }
    remove() {
        if (!this.parentNode) return;
        this.parentNode.nodes.splice(this.parentNode.nodes.indexOf(this), 1);
        this.parentNode = null;
    }
    addEventListener(event, callback) { this.listeners.set(event, callback); }
    click() { return this.listeners.get('click')?.(); }
    querySelector(selector) {
        if (selector !== 'button') return null;
        if (!this.innerHTML.includes('<button')) return null;
        if (!this.generatedButton) this.generatedButton = new Element();
        return this.generatedButton;
    }
    setAttribute() {}
    removeAttribute() {}
}

function browser({ observer = true, textbook = false, modular = false } = {}) {
    const elements = new Map();
    const storage = new Map([['token', 'token-a'], ['username', 'alice']]);
    const observers = [];
    const state = { requests: [], banners: [] };
    const context = vm.createContext({
        URLSearchParams, FormData, AbortController, setTimeout, clearTimeout, setInterval, clearInterval,
        requestAnimationFrame: (callback) => callback(),
        window: { location: { search: '', hash: '', pathname: '/' } },
        localStorage: {
            getItem: (key) => storage.get(key) || null,
            setItem: (key, value) => storage.set(key, value),
            removeItem: (key) => storage.delete(key),
        },
        document: {
            addEventListener() {},
            createElement: () => new Element(),
            getElementById: (id) => elements.get(id) || null,
            querySelector: () => null,
            querySelectorAll: () => [],
        },
    });
    if (observer) {
        context.IntersectionObserver = class {
            constructor(callback) { this.callback = callback; this.disconnected = false; observers.push(this); }
            observe(target) { this.target = target; }
            disconnect() { this.disconnected = true; }
            intersect() { this.callback([{ isIntersecting: true }]); }
        };
    }
    for (const script of modular ? sourceParts : [source]) vm.runInContext(script, context);
    if (textbook) vm.runInContext(textbookSource, context);
    context.showMainBanner = (message) => state.banners.push(message);
    context.renderLearningMap = () => {};
    context._renderProgressWordItem = (word) => `[${word.english}]`;
    context._renderMasteredWordItem = (word) => `[${word.english}]`;
    state.actualApiRequest = context.apiRequest;
    context.apiRequest = (endpoint, options) => new Promise((resolve, reject) => state.requests.push({ endpoint, options, resolve, reject }));
    function element(id) {
        if (!elements.has(id)) elements.set(id, new Element());
        return elements.get(id);
    }
    return { context, storage, observers, state, element, run: (code) => vm.runInContext(code, context) };
}

function progressData(english) {
    return { stats: { total_words: 1, mastered_words: 0, avg_mastery_percent: 0 }, words: [{ english }] };
}

test('registration updates the active account before opening the main page', async () => {
    const ui = browser();
    let submitted;
    let activeAccount;
    ui.context.apiRequest = async (endpoint, options) => {
        assert.equal(endpoint, '/auth/register');
        submitted = JSON.parse(options.body);
        return { username: 'bob', access_token: 'token-b', is_parent: false };
    };
    ui.context.clearInviteRegistrationState = () => {};
    ui.context.resetAccountSessionView = () => {};
    ui.context.showMainPage = () => { activeAccount = ui.run('captureAccountContext()'); };
    await ui.context.register('bob', 'password', 'bob@example.test', 'invite');
    assert.equal(submitted.username, 'bob');
    assert.equal(activeAccount.username, 'bob');
    assert.equal(activeAccount.token, 'token-b');
    assert.equal(ui.storage.get('username'), 'bob');
});

test('split sources preserve the classic global callback contract', () => {
    const ui = browser({ modular: true, textbook: true });
    assert.equal(sourceParts.length, 9);
    for (const name of ['login', 'logout', 'showSection', 'submitAnswer', 'loadProgress',
        'wordbankImportSelected', 'confirmArticleImportFromPicks', 'openTextbookLesson']) {
        assert.equal(typeof ui.context[name], 'function', name);
    }
    assert.equal(ui.run('captureAccountContext().username'), 'alice');
});

test('overlapping login responses cannot restore an older account', async () => {
    const ui = browser();
    const pending = [];
    const opened = [];
    ui.context.fetch = () => new Promise((resolve) => pending.push(resolve));
    ui.context.resetAccountSessionView = () => {};
    ui.context.clearInviteRegistrationState = () => {};
    ui.context.showMainPage = () => opened.push(ui.run('username'));
    const first = ui.context.login('older', 'password');
    const second = ui.context.login('latest', 'password');
    pending[1]({ ok: true, json: async () => ({ access_token: 'latest-token', username: 'latest' }) });
    await second;
    pending[0]({ ok: true, json: async () => ({ access_token: 'older-token', username: 'older' }) });
    await first;
    assert.deepEqual(opened, ['latest']);
    assert.equal(ui.storage.get('username'), 'latest');
});

test('session generations invalidate stale work even when the same credentials return', () => {
    const ui = browser();
    ui.context.resetAccountSessionView = () => {};
    const account = ui.context.captureAccountContext();
    const review = ui.context.captureReviewContext();
    ui.context.clearAccountSession();
    ui.context.activateAccountSession({ access_token: 'token-a', username: 'alice' });
    assert.equal(ui.context.isAccountContextCurrent(account), false);
    assert.equal(ui.context.isReviewContextCurrent(review), false);
    assert.equal(ui.context.isAccountContextCurrent(ui.context.captureAccountContext()), true);
});

test('an old API 401 cannot expire a reactivated session with identical credentials', async () => {
    const ui = browser();
    ui.context.resetAccountSessionView = () => {};
    let respond;
    ui.context.fetch = () => new Promise((resolve) => { respond = resolve; });
    const request = ui.state.actualApiRequest('/user/plan');
    ui.context.activateAccountSession({ access_token: 'token-a', username: 'alice' });
    respond({ ok: false, status: 401, text: async () => '{"error":"expired"}' });
    await assert.rejects(request, /expired/);
    assert.equal(ui.run('token'), 'token-a');
    assert.equal(ui.storage.get('username'), 'alice');
});

test('all list batches stay ordered ahead of the loading sentinel', () => {
    const ui = browser();
    const container = ui.element('word-list');
    const words = Array.from({ length: 237 }, (_, index) => index);
    ui.context._batchRenderList(container, words, (index) => `[${index}]`);
    const io = ui.observers[0];
    assert.equal(container.innerHTML, words.slice(0, 50).map((index) => `[${index}]`).join(''));
    for (let batch = 0; batch < 4; batch++) {
        assert.equal(container.nodes.at(-1), io.target);
        io.intersect();
    }
    assert.equal(container.innerHTML, words.map((index) => `[${index}]`).join(''));
    assert.equal(io.disconnected, true);
    assert.equal(io.target.parentNode, null);
});

test('list loading remains available without IntersectionObserver', () => {
    const ui = browser({ observer: false });
    const container = ui.element('word-list');
    ui.context._batchRenderList(container, Array.from({ length: 120 }, (_, index) => index), (index) => `[${index}]`);
    const button = container.nodes.at(-1);
    assert.equal(button.type, 'button');
    button.click();
    button.click();
    assert.match(container.innerHTML, /\[119\]$/);
    assert.equal(button.parentNode, null);
});

test('replacing a list disconnects its observer and ignores queued callbacks', () => {
    const ui = browser();
    const container = ui.element('word-list');
    ui.context._batchRenderList(container, Array.from({ length: 150 }, () => 'old'), (word) => word);
    const oldObserver = ui.observers[0];
    ui.context.cancelBatchRenderList(container);
    container.innerHTML = '';
    ui.context._batchRenderList(container, ['new'], (word) => word);
    oldObserver.intersect();
    assert.equal(oldObserver.disconnected, true);
    assert.equal(container.innerHTML, 'new');
});

for (const view of ['Progress', 'Mastered']) {
    const id = view === 'Progress' ? 'word-list' : 'mastered-list';
    test(`${view} ignores an older response after a newer refresh`, async () => {
        const ui = browser();
        const container = ui.element(id);
        const old = ui.context[`load${view}`]();
        const latest = ui.context[`load${view}`]();
        ui.state.requests[1].resolve(progressData('new'));
        await latest;
        ui.state.requests[0].resolve(progressData('old'));
        await old;
        assert.equal(container.innerHTML, '[new]');
    });

    test(`${view} ignores responses from a previous account`, async () => {
        const ui = browser();
        const container = ui.element(id);
        const pending = ui.context[`load${view}`]();
        ui.run("token = 'token-b'; username = 'bob';");
        container.innerHTML = 'bob-view';
        ui.state.requests[0].resolve(progressData('alice-private-word'));
        await pending;
        assert.equal(container.innerHTML, 'bob-view');
    });

    test(`${view} shows a retry control after a failed request`, async () => {
        const ui = browser();
        const container = ui.element(id);
        const pending = ui.context[`load${view}`]();
        ui.state.requests[0].reject(new Error('offline'));
        await pending;
        assert.ok(!container.innerHTML.includes('加载中'));
        const retry = container.querySelector('button');
        assert.ok(retry);
        retry.click();
        assert.equal(ui.state.requests.length, 2);
        ui.state.requests[1].resolve(progressData('retried'));
        await new Promise((resolve) => setImmediate(resolve));
        assert.equal(container.innerHTML, '[retried]');
    });
}

test('account reset clears private lists, freshness, scroll and observers', () => {
    const ui = browser();
    const container = ui.element('word-list');
    ui.context._batchRenderList(container, Array.from({ length: 150 }, () => 'alice-word'), (word) => word);
    ui.run("_markSectionLoaded('progress'); _sectionScrollPos.progress = 123;");
    assert.ok(ui.run("_isSectionFresh('progress')"));
    ui.context.resetAccountListViews();
    assert.equal(container.innerHTML, '');
    assert.ok(!ui.run("_isSectionFresh('progress')"));
    assert.equal(ui.run('_sectionScrollPos.progress'), undefined);
    assert.equal(ui.observers[0].disconnected, true);
    ui.observers[0].intersect();
    assert.equal(container.innerHTML, '');
});

test('leaderboard keeps the latest selected scope when requests finish out of order', async () => {
    const ui = browser();
    const rendered = [];
    const loading = ui.element('leaderboard-loading');
    ui.context.renderLeaderboardTable = (rows, scope) => rendered.push(scope);
    ui.context.renderLeaderboardPodium = () => {};
    ui.run("leaderboardScope = 'week';");
    const old = ui.context.reloadLeaderboardTableOnly();
    ui.run("leaderboardScope = 'month';");
    const latest = ui.context.reloadLeaderboardTableOnly();
    ui.state.requests[1].resolve({ leaderboard: [], scope: 'month' });
    await latest;
    ui.state.requests[0].resolve({ leaderboard: [], scope: 'week' });
    await old;
    assert.deepEqual(rendered, ['month']);
    assert.equal(loading.style.display, 'none');
});

test('an old leaderboard failure cannot replace the new account view', async () => {
    const ui = browser();
    const container = ui.element('leaderboard-table-wrap');
    const pending = ui.context.reloadLeaderboardTableOnly();
    ui.run("token = 'token-b'; username = 'bob';");
    container.innerHTML = 'bob-leaderboard';
    ui.state.requests[0].reject(new Error('alice request failed'));
    await pending;
    assert.equal(container.innerHTML, 'bob-leaderboard');
});

test('closing a daily summary prevents its pending response from changing the navbar', async () => {
    const ui = browser();
    ui.element('daily-summary-popover');
    ui.element('daily-summary-body');
    let rendered = 0;
    ui.context.updateGamificationNav = () => rendered++;
    const pending = ui.context.openDailySummaryPopover();
    ui.context.closeDailySummaryPopover();
    ui.state.requests[0].resolve({ total_xp: 123 });
    ui.state.requests[1].resolve({});
    await pending;
    assert.equal(rendered, 0);
    assert.equal(ui.run('lastGamificationProfile'), null);
});

test('XP history ignores a response after the account changes', async () => {
    const ui = browser();
    ui.element('xp-history-modal');
    ui.element('xp-history-body');
    let rendered = false;
    ui.context.renderXpHistoryBody = () => { rendered = true; };
    const pending = ui.context.openXpHistoryModal();
    ui.run("token = 'token-b'; username = 'bob';");
    ui.state.requests[0].resolve({ entries: [] });
    await pending;
    assert.equal(rendered, false);
});

test('an old discovery search cannot overwrite membership from the latest search', async () => {
    const ui = browser();
    ui.context.getDiscoveryWordbankRows = async () => [];
    const old = ui.context.loadDiscoverySearchFromQuery('old');
    const latest = ui.context.loadDiscoverySearchFromQuery('new');
    ui.state.requests[2].resolve({ words: [{ english: 'new-pending' }] });
    ui.state.requests[3].resolve({ words: [{ english: 'new-mastered' }] });
    await latest;
    ui.state.requests[0].resolve({ words: [{ english: 'old-pending' }] });
    ui.state.requests[1].resolve({ words: [{ english: 'old-mastered' }] });
    await old;
    assert.deepEqual(Array.from(ui.run('discoveryPendingKeys')), ['new-pending']);
    assert.deepEqual(Array.from(ui.run('discoveryMasteredKeys')), ['new-mastered']);
});

test('discovery discards a previous account daily word response', async () => {
    const ui = browser();
    ui.context.getDiscoverySelectedLevel = () => 'today';
    const pending = ui.context.loadDiscovery();
    ui.run("token = 'token-b'; username = 'bob';");
    ui.context.resetAccountListViews();
    ui.state.requests[0].resolve({ words: [{ english: 'alice-private-word' }] });
    ui.state.requests[1].resolve({ words: [] });
    await pending;
    assert.equal(ui.run('discoveryDeck.length'), 0);
    assert.equal(ui.run('discoveryPendingKeys.size'), 0);
});

test('article imports stop sending later batches when the account changes', async () => {
    const ui = browser();
    ui.context.fixtureWords = Array.from({ length: 1001 }, (_, index) => ({ english: `word-${index}`, chinese: 'definition' }));
    ui.run('articleImportWords = fixtureWords; articleImportSelectedIdx = new Set(fixtureWords.map((_, index) => index));');
    let resultShown = false;
    ui.context.openImportResultModal = () => { resultShown = true; };
    const pending = ui.context.confirmArticleImportFromPicks();
    assert.equal(JSON.parse(ui.state.requests[0].options.body).length, 500);
    ui.run("token = 'token-b'; username = 'bob';");
    ui.state.requests[0].resolve({ added: 500 });
    await pending;
    assert.equal(ui.state.requests.length, 1);
    assert.equal(resultShown, false);
});

test('background job polling checks the original account before every request', async () => {
    const ui = browser();
    ui.context.setTimeout = (callback) => {
        ui.run("token = 'token-b'; username = 'bob';");
        callback();
    };
    await assert.rejects(ui.context.waitForVocabImportJob('alice-job'), /登录账号已变更/);
    assert.equal(ui.state.requests.length, 0);
});

test('textbook imports cannot write to an account that changed during lookup', async () => {
    const ui = browser({ textbook: true });
    let finishLookup;
    ui.context.textbookLookupWord = () => new Promise((resolve) => { finishLookup = resolve; });
    const pending = ui.context.importWordFromTextbookLemma('apple', new Element());
    ui.run("token = 'token-b'; username = 'bob';");
    finishLookup({ english: 'apple', chinese: 'definition' });
    await pending;
    assert.equal(ui.state.requests.length, 0);
});

test('textbook VIP imports recheck the account after trouble status lookup', async () => {
    const ui = browser({ textbook: true });
    ui.run("userPlan = 'paid';");
    ui.context.textbookLookupWord = async () => null;
    const pending = ui.context.importWordFromTextbookLemma('apple', new Element());
    await new Promise((resolve) => setImmediate(resolve));
    assert.match(ui.state.requests[0].endpoint, /trouble-status/);
    ui.run("token = 'token-b'; username = 'bob';");
    ui.state.requests[0].resolve({ blocked: false });
    await pending;
    assert.equal(ui.state.requests.length, 1);
});

test('textbook lessons keep the most recently opened lesson', async () => {
    const ui = browser({ textbook: true });
    ui.element('textbook-reader');
    ui.element('textbook-catalog-wrap');
    const rendered = [];
    ui.context.textbookFullReadAbort = () => {};
    ui.context.renderTextbookReader = (data) => rendered.push(data.title);
    const old = ui.context.openTextbookLesson('nce', 'old.json');
    const latest = ui.context.openTextbookLesson('nce', 'new.json');
    ui.state.requests[1].resolve({ title: 'new' });
    await latest;
    ui.state.requests[0].resolve({ title: 'old' });
    await old;
    assert.deepEqual(rendered, ['new']);
});

test('old textbook lookups cannot hydrate or clear a new account request', async () => {
    const ui = browser({ textbook: true });
    let hydrated = 0;
    ui.context.hydrateTextbookFromSearchResponse = () => hydrated++;
    ui.context.textbookFullReadAbort = () => {};
    const old = ui.context.textbookLookupWord('apple');
    ui.run("token = 'token-b'; username = 'bob';");
    ui.context.resetTextbookAccountState();
    const latest = ui.context.textbookLookupWord('apple');
    ui.state.requests[0].resolve({});
    await old;
    assert.equal(hydrated, 0);
    assert.equal(ui.run('textbookLookupInflight.size'), 1);
    ui.state.requests[1].resolve({});
    await latest;
    assert.equal(hydrated, 1);
    assert.equal(ui.run('textbookLookupInflight.size'), 0);
});

test('a late wordbank 401 cannot log out a newer account', async () => {
    const ui = browser();
    let finish;
    let loginShown = false;
    ui.context.fetch = () => new Promise((resolve) => { finish = resolve; });
    ui.context.showLoginPage = () => { loginShown = true; };
    const pending = ui.context.fetchWordbankCsvForDiscoveryNetwork('', '/wordbank/csv?fields=minimal');
    ui.run("token = 'token-b'; username = 'bob';");
    finish({ status: 401, ok: false });
    await assert.rejects(pending, /登录账号已变更/);
    assert.equal(ui.run('token'), 'token-b');
    assert.equal(ui.run('username'), 'bob');
    assert.equal(loginShown, false);
});

test('background wordbank revalidation cannot restore a cleared cache', async () => {
    const ui = browser();
    let finish;
    ui.context.fetch = () => new Promise((resolve) => { finish = resolve; });
    ui.run("wordbankCsvDiscoveryCache = { key: '/wordbank/csv?fields=minimal', etag: 'old', data: { words: [] } };");
    ui.context.maybeRevalidateWordbankDiscoveryCache('/wordbank/csv?fields=minimal');
    ui.context.clearWordbankCsvDiscoveryCache();
    finish({ status: 200, ok: true, headers: { get: () => 'new' }, json: async () => ({ words: [{ english: 'stale' }] }) });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(ui.run('wordbankCsvDiscoveryCache.key'), '');
    assert.equal(ui.run('wordbankCsvDiscoveryCache.data'), null);
});

for (const outcome of ['succeeded', 'failed']) {
    test(`textbook waits for a queued VIP import to finish as ${outcome}`, async () => {
        const ui = browser({ textbook: true });
        ui.run("userPlan = 'paid';");
        ui.context.setTimeout = (callback) => callback();
        ui.context.textbookLookupWord = async () => null;
        const feedback = [];
        let statsLoads = 0;
        let invalidated = 0;
        ui.context.showTextbookImportFeedback = (anchor, message, kind) => feedback.push({ message, kind });
        ui.context.loadStats = () => statsLoads++;
        ui.context.invalidateReviewDataAfterImport = () => invalidated++;
        const pending = ui.context.importWordFromTextbookLemma('apple', new Element());
        await new Promise((resolve) => setImmediate(resolve));
        ui.state.requests[0].resolve({ blocked: false });
        await new Promise((resolve) => setImmediate(resolve));
        assert.equal(ui.state.requests[1].options.method, 'POST');
        ui.state.requests[1].resolve({ job_id: 'queued-job' });
        await new Promise((resolve) => setImmediate(resolve));
        assert.equal(ui.state.requests[2].endpoint, '/wordbank/import-jobs/queued-job');
        assert.ok(feedback.every((row) => row.kind !== 'ok'));
        assert.equal(statsLoads, 0);
        ui.state.requests[2].resolve(outcome === 'succeeded'
            ? { status: 'succeeded', result: { message: 'imported', queue_result: { added: 1 } } }
            : { status: 'failed', error: 'generation failed' });
        await pending;
        assert.equal(feedback.at(-1).kind, outcome === 'succeeded' ? 'ok' : 'error');
        assert.equal(statsLoads, outcome === 'succeeded' ? 1 : 0);
        assert.equal(invalidated, outcome === 'succeeded' ? 1 : 0);
    });
}

test('returning to the login page also clears chat for expired sessions', () => {
    const ui = browser({ textbook: true });
    ui.element('login-page');
    ui.element('main-page');
    let chatResets = 0;
    ui.context.window.chatRoomOnLogout = () => chatResets++;
    ui.context.showLoginPage();
    assert.equal(chatResets, 1);
});

test('avatar encoding cannot upload to an account that changed before toBlob completed', async () => {
    const ui = browser();
    const image = ui.element('avatar-crop-img');
    image.complete = true;
    ui.run('avatarCrop.iw = 1000; avatarCrop.ih = 1000; avatarCrop.scale = 1; avatarCrop.imgX = 0; avatarCrop.imgY = 0;');
    let finishEncoding;
    ui.context.document.createElement = () => ({
        getContext: () => ({ drawImage() {} }),
        toBlob: (callback) => { finishEncoding = callback; },
    });
    let uploads = 0;
    ui.context.postAvatarFile = async () => uploads++;
    const pending = ui.context.confirmAvatarCropUpload();
    ui.run("token = 'token-b'; username = 'bob';");
    finishEncoding({ size: 100, type: 'image/webp' });
    await pending;
    assert.equal(uploads, 0);
});

test('avatar uploads reject a stale account before sending a request', async () => {
    const ui = browser();
    const account = ui.context.captureAccountContext();
    ui.run("token = 'token-b'; username = 'bob';");
    let sent = false;
    ui.context.fetch = async () => { sent = true; };
    await assert.rejects(ui.context.postAvatarFile(new Blob(['image']), 'avatar.webp', account), /登录账号已变更/);
    assert.equal(sent, false);
});

test('account email ignores a response from the previous account', async () => {
    const ui = browser();
    const input = ui.element('settings-email');
    const pending = ui.context.loadAccountEmail();
    ui.run("token = 'token-b'; username = 'bob';");
    input.value = 'bob@example.test';
    ui.state.requests[0].resolve({ email: 'alice@example.test' });
    await pending;
    assert.equal(input.value, 'bob@example.test');
});

test('settings loading stops its chained requests after the account changes', async () => {
    const ui = browser();
    const pending = ui.context.loadUserSettingsPanel();
    assert.equal(ui.state.requests[0].endpoint, '/user/email');
    ui.run("token = 'token-b'; username = 'bob';");
    ui.state.requests[0].resolve({ email: 'alice@example.test' });
    await pending;
    assert.equal(ui.state.requests.length, 1);
});

test('pending word removal stops later destructive batches after an account change', async () => {
    const ui = browser();
    const selected = Array.from({ length: 401 }, (_, index) => `word-${index}`);
    const pending = ui.context.removePendingWordsInBatches(selected);
    assert.equal(JSON.parse(ui.state.requests[0].options.body).english.length, 200);
    ui.run("token = 'token-b'; username = 'bob';");
    ui.state.requests[0].resolve({ removed: 200, removed_english: selected.slice(0, 200) });
    assert.equal(await pending, null);
    assert.equal(ui.state.requests.length, 1);
});

test('wordbank imports stop after an account change and cannot reset the new import', async () => {
    const ui = browser();
    const button = ui.element('wordbank-import-btn');
    ui.context.fixtureWords = Array.from({ length: 1001 }, (_, index) => ({ english: `alice-${index}`, chinese: 'definition' }));
    ui.run('wbState.selected = new Set(fixtureWords.map((word) => word.english)); wbState.selectedMap = new Map(fixtureWords.map((word) => [word.english, word]));');
    const results = [];
    ui.context.openImportResultModal = (data) => results.push(data);
    const old = ui.context.wordbankImportSelected();
    assert.equal(JSON.parse(ui.state.requests[0].options.body).length, 500);
    ui.run("token = 'token-b'; username = 'bob';");
    ui.context.resetAccountListViews();
    assert.equal(ui.run('wbState.selected.size'), 0);
    assert.equal(ui.run('wbState.selectedMap.size'), 0);
    assert.equal(button.disabled, false);
    ui.run("wbState.selected.add('bob-only'); wbState.selectedMap.set('bob-only', { english: 'bob-only', chinese: 'definition' });");
    const latest = ui.context.wordbankImportSelected();
    ui.state.requests[0].resolve({ added: 500 });
    await old;
    assert.equal(ui.state.requests.length, 2);
    assert.equal(results.length, 0);
    assert.equal(ui.run('wbState.importBusy'), true);
    assert.equal(button.disabled, true);
    assert.deepEqual(Array.from(ui.run('wbState.selected')), ['bob-only']);
    ui.state.requests[1].resolve({ added: 1 });
    await latest;
    assert.equal(results.length, 1);
    assert.equal(results[0].stats.total, 1);
});

test('OCR reset releases its controls and an old completion cannot unlock a new OCR job', async () => {
    const ui = browser({ textbook: true });
    ui.element('login-page');
    ui.element('main-page');
    const username = ui.element('username-display');
    username.textContent = 'alice';
    const button = ui.element('import-ocr-pick-img-btn');
    const textarea = ui.element('import-article-textarea');
    const controls = ['import-article-btn', 'import-nc-lesson-select', 'import-ocr-english-only', 'import-ocr-handwriting']
        .map((id) => ui.element(id));
    controls.forEach((control) => { control.disabled = false; });
    const file = new Blob(['image'], { type: 'image/png' });
    const old = ui.context.runImportOcrToTextarea(file);
    assert.equal(ui.run('importOcrBusy'), true);
    assert.equal(button.disabled, true);
    ui.run("token = 'token-b'; username = 'bob';");
    ui.context.showLoginPage();
    assert.equal(ui.run('importOcrBusy'), false);
    assert.equal(button.disabled, false);
    assert.equal(textarea.disabled, false);
    assert.equal(username.textContent, '');
    assert.ok(controls.every((control) => !control.disabled));
    const latest = ui.context.runImportOcrToTextarea(file);
    ui.state.requests[0].resolve({ raw_text: 'alice-private-text', review_lines: [] });
    await old;
    assert.equal(ui.run('importOcrBusy'), true);
    assert.equal(button.disabled, true);
    assert.ok(controls.every((control) => control.disabled));
    assert.equal(textarea.value, '');
    ui.state.requests[1].resolve({ raw_text: 'bob-text', review_lines: [] });
    await latest;
    assert.equal(textarea.value, 'bob-text');
    assert.equal(ui.run('importOcrBusy'), false);
    assert.equal(button.disabled, false);
    assert.ok(controls.every((control) => !control.disabled));
});
