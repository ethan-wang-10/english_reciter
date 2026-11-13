const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, 'static/subject.html'), 'utf8');
const source = fs.readFileSync(path.join(__dirname, 'static/js/subject-portal.js'), 'utf8');
const TOKEN = 'earthturtle_subject_token';
const USERNAME = 'earthturtle_subject_username';
const settle = () => new Promise(setImmediate);

function deferred() {
    let resolve;
    const promise = new Promise((callback) => { resolve = callback; });
    return { promise, resolve };
}

function subjectUI({ subject = 'chinese', storage = new Map(), href } = {}) {
    const elements = new Map();
    const state = { calls: [], navigations: [], storageWrites: [], windowListeners: {}, routes: {} };
    const account = { username: 'alice', email: 'alice@example.com', display_name: 'Alice', is_parent: false, child_username: '', plan: 'free' };
    state.account = account;
    state.resources = { papers: [], knowledge: [], records: [] };

    class Element {
        constructor(tagName = 'div', attributes = {}) {
            this.tagName = tagName;
            this.attributes = { ...attributes };
            this.value = attributes.value || '';
            this.hidden = Object.hasOwn(attributes, 'hidden');
            this.disabled = Object.hasOwn(attributes, 'disabled');
            this.children = [];
            this.listeners = {};
            this.dataset = {};
            this.ownText = '';
            for (const [name, value] of Object.entries(attributes)) {
                if (name.startsWith('data-')) this.dataset[name.slice(5)] = value;
            }
            const classes = new Set((attributes.class || '').split(/\s+/).filter(Boolean));
            this.classList = {
                toggle(name, value) {
                    const enabled = value === undefined ? !classes.has(name) : value;
                    if (enabled) classes.add(name); else classes.delete(name);
                },
                contains: (name) => classes.has(name),
            };
        }
        set textContent(value) { this.ownText = String(value); this.children = []; }
        get textContent() { return this.ownText + this.children.map((child) => child.textContent).join(''); }
        set innerHTML(_value) { throw new Error('Untrusted resource content must use safe DOM nodes'); }
        get innerHTML() { throw new Error('Tests should inspect the DOM nodes'); }
        appendChild(child) { this.children.push(child); child.parentElement = this; return child; }
        replaceChildren(...children) { this.children = []; this.ownText = ''; children.forEach((child) => this.appendChild(child)); }
        setAttribute(name, value) { this.attributes[name] = String(value); }
        getAttribute(name) { return this.attributes[name] ?? null; }
        removeAttribute(name) { delete this.attributes[name]; if (name === 'src') this.src = ''; }
        addEventListener(name, callback) { this.listeners[name] = callback; }
        querySelectorAll(selector) {
            const tests = selector.split(',').map((value) => value.trim());
            const matches = (node) => tests.some((query) => {
                if (query.startsWith('.')) return (node.attributes.class || '').split(/\s+/).includes(query.slice(1));
                if (query.startsWith('[')) {
                    const attribute = query.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
                    return !!attribute && Object.hasOwn(node.attributes, attribute[1])
                        && (attribute[2] === undefined || node.attributes[attribute[1]] === attribute[2]);
                }
                return node.tagName === query;
            });
            return this.children.flatMap((child) => [...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)]);
        }
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
        focus() { state.focused = this; }
        reset() { this.querySelectorAll('input').forEach((node) => { node.value = ''; }); }
        async fire(name, extra = {}) {
            const result = this.listeners[name]?.({ preventDefault() {}, target: this, ...extra });
            await result;
            await settle();
            return result;
        }
    }

    const root = new Element('document');
    const stack = [root];
    const voidTags = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);
    for (const token of html.match(/<!--[\s\S]*?-->|<[^>]+>|[^<]+/g)) {
        if (token.startsWith('<!--') || token.startsWith('<!')) continue;
        if (token.startsWith('</')) { stack.pop(); continue; }
        if (token.startsWith('<')) {
            const tag = token.match(/^<([a-z0-9-]+)/i)?.[1].toLowerCase();
            if (!tag) continue;
            const attributes = {};
            for (const match of token.slice(tag.length + 1, -1).matchAll(/([a-zA-Z_:][\w:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) {
                attributes[match[1]] = match[2] ?? match[3] ?? match[4] ?? '';
            }
            const node = new Element(tag, attributes);
            stack.at(-1).appendChild(node);
            if (attributes.id) elements.set(attributes.id, node);
            if (tag === 'body') state.body = node;
            if (tag === 'meta' && attributes.name === 'theme-color') state.themeColor = node;
            if (!voidTags.has(tag) && !token.endsWith('/>')) stack.push(node);
        } else {
            stack.at(-1).ownText += token.replace(/\s+/g, ' ');
        }
    }
    const origin = `https://${subject}.itorange.online`;
    const config = {
        id: subject, name: subject === 'chinese' ? '语文' : '数学', title: subject === 'chinese' ? '语文学习空间' : '数学学习空间',
        description: '按年级整理学习内容。', origin,
        subjects: [
            { id: 'chinese', name: '语文', url: 'https://chinese.itorange.online/' },
            { id: 'math', name: '数学', url: 'https://math.itorange.online/' },
        ], english_url: 'https://english.itorange.online/', auth_origin: 'https://english.itorange.online',
    };
    elements.get('subject-site-config').textContent = JSON.stringify(config);
    const location = { origin, href: href || origin + '/', assign: (url) => state.navigations.push(url) };
    const window = { addEventListener: (name, callback) => { state.windowListeners[name] = callback; }, location };
    function bootstrapPayload() {
        return { subject: config, account: { ...state.account }, summary: {
            paper_count: state.resources.papers.length,
            knowledge_count: state.resources.knowledge.length,
            record_count: state.resources.records.length,
        }, ...state.resources };
    }
    state.bootstrapPayload = bootstrapPayload;
    const context = vm.createContext({
        window, URL, location,
        setTimeout, clearTimeout,
        sessionStorage: (() => {
            const values = new Map();
            return { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
        })(),
        history: { replaceState(_state, _unused, value) { location.href = new URL(value, origin).href; state.replacedUrl = value; } },
        document: {
            body: state.body, title: '',
            getElementById: (id) => elements.get(id),
            createElement: (tagName) => new Element(tagName),
            querySelectorAll: (selector) => root.querySelectorAll(selector),
            querySelector: (selector) => selector === 'meta[name="theme-color"]' ? state.themeColor : null,
        },
        localStorage: {
            getItem: (key) => storage.get(key) || null,
            setItem(key, value) { state.storageWrites.push({ key, value }); storage.set(key, value); },
            removeItem(key) { state.storageWrites.push({ key, value: null }); storage.delete(key); },
        },
        fetch: async (url, options = {}) => {
            const call = { url, method: options.method || 'GET', headers: options.headers || {}, body: options.body ? JSON.parse(options.body) : undefined };
            state.calls.push(call);
            let response;
            if (state.routes[url]) response = await state.routes[url](call);
            else if (url === '/api/auth/login' || url === '/api/auth/register') {
                state.account = { ...state.account, username: call.body.username };
                response = { data: { access_token: `${call.body.username}-token`, username: call.body.username } };
            } else if (url.endsWith('/bootstrap')) response = { data: bootstrapPayload() };
            else if (url === '/api/auth/logout') response = { data: { message: '已退出登录' } };
            else if (url === '/api/auth/forgot-password') response = { data: { message: '如果邮箱已绑定，邮件将发送。' } };
            else if (url === '/api/auth/reset-password') response = { data: { message: '密码已重置' } };
            else {
                const section = url.split('/').at(-1);
                if (!Object.hasOwn(state.resources, section)) throw new Error(`Unexpected request: ${url}`);
                response = { data: { items: state.resources[section], count: state.resources[section].length } };
            }
            const status = response.status || 200;
            return { status, ok: status >= 200 && status < 300, json: async () => response.data || {} };
        },
    });
    vm.runInContext(source, context);
    return {
        state, context, storage, api: window.subjectPortal, config,
        field: (id) => elements.get(id),
        async flush() { await settle(); await settle(); },
        async login(name = 'alice') {
            elements.get('login-username').value = name;
            elements.get('login-password').value = 'existing-password';
            await elements.get('login-form').fire('submit');
        },
    };
}

function descendant(node, tagName) { return node.querySelectorAll(tagName); }

test('existing account logs into the subject workspace without initializing English learning data', async () => {
    const storage = new Map([['token', 'legacy-english-token'], ['username', 'english-user']]);
    const ui = subjectUI({ storage });
    await ui.login();
    assert.deepEqual(ui.state.calls.map((call) => call.url), ['/api/auth/login', '/api/subjects/chinese/bootstrap']);
    assert.deepEqual(ui.state.calls[0].body, { username: 'alice', password: 'existing-password' });
    assert.equal(ui.state.calls[1].headers.Authorization, 'Bearer alice-token');
    assert.equal(ui.field('auth-view').hidden, true, ui.field('auth-error').textContent);
    assert.equal(ui.field('learning-view').hidden, false);
    assert.equal(ui.storage.get(TOKEN), 'alice-token');
    assert.equal(ui.storage.get(USERNAME), 'alice');
    assert.equal(ui.storage.get('token'), 'legacy-english-token');
    assert.equal(ui.storage.get('username'), 'english-user');
    for (const section of ['papers', 'knowledge', 'records']) {
        assert.equal(ui.field(`${section}-list`).children.length, 0);
        assert.equal(ui.field(`${section}-empty`).hidden, false);
    }
    assert.equal(ui.field('paper-count').textContent, '0');
    assert.equal(ui.field('account-username').textContent, 'alice');
});

for (const subject of ['chinese', 'math']) {
    test(`a persisted subject session restores ${subject} using only that subject's bootstrap`, async () => {
        const ui = subjectUI({ subject, storage: new Map([[TOKEN, 'alice-token'], [USERNAME, 'alice']]) });
        await ui.flush();
        assert.deepEqual(ui.state.calls.map((call) => call.url), [`/api/subjects/${subject}/bootstrap`]);
        assert.equal(ui.field('learning-view').hidden, false, ui.field('auth-error').textContent);
        assert.equal(ui.context.document.body.dataset.subject, subject);
        assert.equal(ui.api.getSession().username, 'alice');
    });
}

test('invalid login stays on the form and can be retried with the same account', async () => {
    const ui = subjectUI();
    ui.state.routes['/api/auth/login'] = () => ({ status: 401, data: { error: '用户名或密码错误' } });
    await ui.login();
    assert.equal(ui.field('auth-view').hidden, false);
    assert.equal(ui.field('learning-view').hidden, true);
    assert.match(ui.field('auth-error').textContent, /用户名或密码错误/);
    assert.equal(ui.field('login-username').value, 'alice');
    assert.equal(ui.field('login-password').disabled, false);
    delete ui.state.routes['/api/auth/login'];
    await ui.login();
    assert.equal(ui.field('learning-view').hidden, false);
    assert.equal(ui.state.calls.filter((call) => call.url.endsWith('/bootstrap')).length, 1);
});

test('a temporary bootstrap network error preserves the token and allows an explicit retry', async () => {
    const ui = subjectUI();
    ui.state.routes['/api/subjects/chinese/bootstrap'] = () => { throw new Error('网络暂不可用'); };
    await ui.login();
    assert.match(ui.field('auth-error').textContent, /网络暂不可用/);
    assert.equal(ui.field('login-password').disabled, false);
    assert.equal(ui.storage.get(TOKEN), 'alice-token');
    delete ui.state.routes['/api/subjects/chinese/bootstrap'];
    await ui.api.refresh();
    assert.equal(ui.field('learning-view').hidden, false);
    assert.equal(ui.state.calls.filter((call) => call.url === '/api/auth/login').length, 1);
});

test('an expired authenticated request clears private content and both subject storage keys', async () => {
    const ui = subjectUI();
    ui.state.resources.records = [{ paper_title: 'Alice 私有成绩', score: 88 }];
    await ui.login();
    assert.equal(ui.field('records-list').children.length, 1);
    ui.state.routes['/api/subjects/chinese/records'] = () => ({ status: 401, data: { error: '会话过期' } });
    await ui.field('records-tab').fire('click');
    assert.equal(ui.field('learning-view').hidden, true);
    assert.equal(ui.field('auth-view').hidden, false);
    assert.equal(ui.field('records-list').children.length, 0);
    assert.equal(ui.field('account-username').textContent, '—');
    assert.equal(ui.storage.has(TOKEN), false);
    assert.equal(ui.storage.has(USERNAME), false);
    assert.equal(ui.api.getSession().account, null);
});

test('logout ignores a pending bootstrap response instead of restoring the previous account', async () => {
    const ui = subjectUI();
    await ui.login();
    const pending = deferred();
    ui.state.routes['/api/subjects/chinese/bootstrap'] = () => pending.promise;
    const refreshing = ui.api.refresh();
    await ui.field('subject-logout').fire('click');
    pending.resolve({ data: { ...ui.state.bootstrapPayload(), records: [{ paper_title: '旧成绩', score: 100 }] } });
    await refreshing;
    assert.equal(ui.field('learning-view').hidden, true);
    assert.equal(ui.field('auth-view').hidden, false);
    assert.equal(ui.field('records-list').children.length, 0);
    assert.equal(ui.field('header-username').textContent, '');
    assert.equal(ui.api.getSession().token, '');
    assert.equal(ui.state.calls.find((call) => call.url === '/api/auth/logout').headers.Authorization, 'Bearer alice-token');
});

test('logout also invalidates an unfinished login response', async () => {
    const ui = subjectUI();
    const pending = deferred();
    ui.state.routes['/api/auth/login'] = () => pending.promise;
    const loggingIn = ui.login();
    await settle();
    await ui.field('subject-logout').fire('click');
    pending.resolve({ data: { access_token: 'late-token', username: 'alice' } });
    await loggingIn;
    assert.equal(ui.api.getSession().token, '');
    assert.equal(ui.field('learning-view').hidden, true);
    assert.equal(ui.state.calls.some((call) => call.url.endsWith('/bootstrap')), false);
});

test('switching accounts ignores a late record response for the previous account', async () => {
    const ui = subjectUI();
    await ui.login();
    const pending = deferred();
    ui.state.routes['/api/subjects/chinese/records'] = () => pending.promise;
    await ui.field('records-tab').fire('click');
    ui.state.account = { ...ui.state.account, username: 'bob', display_name: 'Bob' };
    await ui.api.acceptSession({ access_token: 'bob-token', username: 'bob' });
    pending.resolve({ data: { items: [{ paper_title: 'Alice 私有成绩', score: 100 }] } });
    await ui.flush();
    assert.equal(ui.api.getSession().username, 'bob');
    assert.equal(ui.field('header-username').textContent, 'Bob');
    assert.equal(ui.field('records-list').children.length, 0);
});

test('subject switching dispatches the target ID to SSO without putting a bearer token in a URL', async () => {
    const ui = subjectUI();
    await ui.login();
    const switched = [];
    ui.context.window.subjectSSO = { navigate: async (...args) => switched.push(args) };
    await ui.api.switchSubject({ id: 'math', name: '数学', url: 'https://math.itorange.online/' });
    assert.equal(switched.length, 1);
    assert.equal(switched[0][0], 'math');
    assert.equal(switched[0][1].token, 'alice-token');
    assert.deepEqual(ui.state.navigations, []);
    assert.equal(ui.state.calls.some((call) => call.url.includes('alice-token')), false);
    await ui.api.switchSubject({ id: 'chinese', name: '语文', url: ui.config.origin });
    assert.equal(switched.length, 1);
});

test('paper filtering uses exam_kind together with grade and term', async () => {
    const ui = subjectUI();
    ui.state.resources.papers = [
        { id: 'a', title: '四上期中', grade: 4, term: 1, exam_kind: 'midterm', phase: 'final' },
        { id: 'b', title: '五上期中', grade: 5, term: 1, exam_kind: 'midterm' },
        { id: 'c', title: '四下期末', grade: 4, term: 2, exam_kind: 'final' },
    ];
    await ui.login();
    assert.equal(ui.field('papers-list').children.length, 3);
    ui.field('paper-phase-filter').value = 'midterm';
    await ui.field('paper-phase-filter').fire('change');
    assert.equal(ui.field('papers-list').children.length, 2);
    ui.field('paper-grade-filter').value = '4';
    ui.field('paper-term-filter').value = '1';
    await ui.field('paper-grade-filter').fire('change');
    assert.equal(ui.field('papers-list').children.length, 1);
    assert.equal(descendant(ui.field('papers-list').children[0], 'h3')[0].textContent, '四上期中');
    assert.match(ui.field('papers-list').children[0].textContent, /期中/);
});

test('knowledge sections and record titles render as safe text and reflect the parent account', async () => {
    const ui = subjectUI();
    ui.state.account = { ...ui.state.account, username: 'alice_parent', display_name: '家长', is_parent: true, child_username: 'alice' };
    const unsafe = '<img src=x onerror=alert(1)>';
    ui.state.resources.knowledge = [{ title: '单元知识', sections: [{ title: unsafe, items: ['<script>alert(1)</script>', '正常知识点'] }], url: 'javascript:alert(1)' }];
    ui.state.resources.records = [{ paper_title: '四年级单元自测', score: 87.5, submitted_at: '2026-11-13' }];
    await ui.login('alice_parent');
    await ui.field('knowledge-tab').fire('click');
    const knowledge = ui.field('knowledge-list').children[0];
    assert.equal(descendant(knowledge, 'details').length, 1);
    assert.equal(descendant(knowledge, 'h4')[0].textContent, unsafe);
    assert.equal(descendant(knowledge, 'li')[0].textContent, '<script>alert(1)</script>');
    assert.equal(descendant(knowledge, 'a').length, 0);
    assert.equal(descendant(knowledge, 'img').length, 0);
    await ui.field('records-tab').fire('click');
    const record = ui.field('records-list').children[0];
    assert.equal(descendant(record, 'h3')[0].textContent, '四年级单元自测');
    assert.match(record.textContent, /87\.5 分/);
    assert.match(ui.field('records-description').textContent, /alice/);
    assert.equal(ui.field('account-child').textContent, 'alice');
});

test('registration validates matching passwords, sends the invite, and removes invite data from the page URL', async () => {
    const ui = subjectUI({ href: 'https://chinese.itorange.online/?invite=one-time-invite&from=math' });
    assert.equal(ui.field('register-form').hidden, false);
    assert.equal(ui.field('register-invite').value, 'one-time-invite');
    assert.equal(ui.state.replacedUrl, '/?from=math');
    ui.field('register-username').value = 'new_student';
    ui.field('register-email').value = 'student@example.com';
    ui.field('register-password').value = 'secure-password';
    ui.field('register-password-confirm').value = 'other-password';
    await ui.field('register-form').fire('submit');
    assert.equal(ui.state.calls.length, 0);
    assert.match(ui.field('auth-error').textContent, /不一致/);
    ui.field('register-password-confirm').value = 'secure-password';
    await ui.field('register-form').fire('submit');
    assert.deepEqual(ui.state.calls[0].body, { username: 'new_student', password: 'secure-password', email: 'student@example.com', invite_code: 'one-time-invite' });
    assert.equal(ui.field('learning-view').hidden, false);
});

test('a reset link strips its token from navigation and submits it only to the shared reset endpoint', async () => {
    const ui = subjectUI({ href: 'https://chinese.itorange.online/?reset_token=one-time-reset&from=email' });
    assert.equal(ui.field('reset-form').hidden, false);
    assert.equal(ui.state.replacedUrl, '/?from=email');
    ui.field('reset-password').value = 'new-password';
    ui.field('reset-password-confirm').value = 'new-password';
    await ui.field('reset-form').fire('submit');
    assert.equal(ui.state.calls.length, 1);
    assert.equal(ui.state.calls[0].url, '/api/auth/reset-password');
    assert.deepEqual(ui.state.calls[0].body, { token: 'one-time-reset', password: 'new-password', password_confirm: 'new-password' });
    assert.equal(ui.field('login-form').hidden, false);
    assert.equal(ui.field('reset-password').value, '');
});

test('a token change from another tab restores that account without writing the token back to storage', async () => {
    const ui = subjectUI();
    await ui.login();
    const tokenWrites = ui.state.storageWrites.filter((entry) => entry.key === TOKEN).length;
    ui.storage.set(TOKEN, 'bob-token');
    ui.storage.set(USERNAME, 'bob');
    ui.state.account = { ...ui.state.account, username: 'bob', display_name: 'Bob' };
    ui.state.windowListeners.storage({ key: TOKEN, newValue: 'bob-token' });
    await ui.flush();
    assert.equal(ui.api.getSession().username, 'bob');
    assert.equal(ui.field('header-username').textContent, 'Bob');
    assert.equal(ui.storage.get(TOKEN), 'bob-token');
    assert.equal(ui.state.storageWrites.filter((entry) => entry.key === TOKEN).length, tokenWrites);
    ui.storage.clear();
    ui.state.windowListeners.storage({ key: null });
    assert.equal(ui.field('learning-view').hidden, true);
    assert.equal(ui.api.getSession().token, '');
});

const practiceSource = fs.readFileSync(path.join(__dirname, 'static/js/subject-practice.js'), 'utf8');
const mathCatalog = JSON.parse(fs.readFileSync(path.join(__dirname, 'subject_content/math/catalog.json'), 'utf8'));
const mathPaper = JSON.parse(fs.readFileSync(path.join(__dirname, `subject_content/math/papers/${mathCatalog.papers[0].id}.json`), 'utf8'));
const copy = (value) => JSON.parse(JSON.stringify(value));

function practiceUI({ pending = false, parent = false } = {}) {
    const ui = subjectUI({ subject: 'math' });
    ui.state.account.is_parent = parent;
    ui.state.account.child_username = parent ? 'child' : '';
    ui.state.resources.papers = copy(mathCatalog.papers);
    ui.state.resources.knowledge = copy(mathCatalog.knowledge);
    const questions = mathPaper.questions.map((question) => {
        const publicQuestion = {};
        for (const key of ['id', 'type', 'prompt', 'points', 'options', 'diagram_url', 'diagram_alt', 'difficulty', 'hints']) {
            if (Object.hasOwn(question, key)) publicQuestion[key] = copy(question[key]);
        }
        if (question.fields) publicQuestion.fields = question.fields.map((field) => {
            const result = {};
            for (const key of ['id', 'label', 'format', 'unit', 'hint', 'points']) if (Object.hasOwn(field, key)) result[key] = field[key];
            return result;
        });
        return publicQuestion;
    });
    const initial = { id: 'a'.repeat(32), paper_id: mathPaper.id, paper_title: mathPaper.title,
        status: 'in_progress', questions, answers: {}, workings: {}, index: 0, revision: 0,
        started_at: '2026-11-13T12:00:00+08:00', time_minutes: 45, scope: mathPaper.scope, grading_note: mathPaper.grading_note };
    ui.state.activeAttempt = pending ? { ...copy(initial), answers: { q09: { answer: '724' } }, index: 8, revision: 1 } : null;
    ui.state.resultPayload = {
        id: initial.id, paper_id: mathPaper.id, paper_title: mathPaper.title, score: 100, max_score: 100,
        base_score: 90, base_max_score: 90, challenge_score: 10, challenge_max_score: 10,
        total: 32, correct_count: 32, partial_count: 0, submitted_at: '2026-11-13T13:00:00+08:00',
        grading_note: mathPaper.grading_note, status: 'submitted',
        questions: mathPaper.questions.map((question) => ({
            ...questions.find((q) => q.id === question.id), correct: true, earned_points: question.points,
            user_answer: question.type === 'choice' ? question.options.find((option) => option.id === question.answer).text : '',
            correct_answer: question.type === 'choice' ? question.options.find((option) => option.id === question.answer).text : '',
            fields: (question.fields || []).map((field) => ({ ...field, user_answer: field.answer, correct_answer: field.answer, correct: true, earned_points: field.points })),
            explanation: question.explanation, working: '',
        })),
    };
    const endpoint = `/api/subjects/math/attempts/${initial.id}`;
    ui.state.routes['/api/subjects/math/attempts/current'] = () => ({ data: { attempt: copy(ui.state.activeAttempt) } });
    ui.state.routes[`/api/subjects/math/papers/${mathPaper.id}/attempts`] = () => {
        if (ui.state.startReply) return ui.state.startReply();
        ui.state.activeAttempt ||= copy(initial);
        return { data: { attempt: copy(ui.state.activeAttempt) } };
    };
    ui.state.routes[endpoint] = (call) => {
        if (call.method === 'GET') return { data: { attempt: copy(ui.state.activeAttempt) } };
        if (ui.state.saveReply) return ui.state.saveReply(call);
        if (!ui.state.activeAttempt) return { status: 409, data: { error: '这份练习已结束' } };
        Object.assign(ui.state.activeAttempt, copy(call.body));
        return { data: { revision: call.body.revision } };
    };
    ui.state.routes[endpoint + '/abandon'] = () => {
        ui.state.activeAttempt = null;
        return { data: { ok: true } };
    };
    ui.state.routes[endpoint + '/submit'] = (call) => {
        if (ui.state.submitReply) return ui.state.submitReply(call);
        ui.state.activeAttempt = null;
        ui.state.resources.records = [{ id: initial.id, paper_title: mathPaper.title, score: 100, submitted_at: '2026-11-13', status: 'submitted' }];
        return { data: { result: copy(ui.state.resultPayload) } };
    };
    ui.state.routes[`/api/subjects/math/records/${initial.id}`] = () => ({ data: { result: copy(ui.state.resultPayload) } });
    vm.runInContext(practiceSource, ui.context);
    ui.practice = ui.context.window.subjectPractice;
    ui.inputs = () => ui.field('practice-answer-fields').querySelectorAll('input');
    ui.jump = (number) => ui.field('practice-question-grid').children[number - 1].fire('click');
    ui.start = () => ui.practice.start(mathPaper.id);
    void ui.api.acceptSession({ access_token: 'alice-token', username: 'alice' });
    return ui;
}

test('unfinished math practice is offered on the list without forcing the user into the old question', async () => {
    const ui = practiceUI({ pending: true });
    await ui.flush();
    assert.equal(ui.field('papers-panel').hidden, false);
    assert.equal(ui.field('subject-practice-view').hidden, true);
    assert.equal(ui.field('practice-resume-banner').hidden, false);
    await ui.field('practice-resume').fire('click');
    assert.equal(ui.field('subject-practice-view').hidden, false);
    assert.match(ui.field('practice-question-position').textContent, /9 \/ 32/);
    assert.equal(ui.inputs()[0].value, '724');
});

test('math card starts online practice and preserves typed answers across navigation and server save', async () => {
    const ui = practiceUI();
    await ui.flush();
    const card = ui.field('papers-list').children[0];
    await card.querySelectorAll('button').find((button) => button.textContent === '开始练习').fire('click');
    assert.equal(ui.field('practice-question-prompt').textContent, mathPaper.questions[0].prompt);
    await ui.field('practice-choice-options').children[1].fire('click');
    await ui.jump(9);
    ui.inputs()[0].value = '724';
    await ui.inputs()[0].fire('input');
    await ui.practice.flush();
    assert.deepEqual(ui.state.activeAttempt.answers, { q01: 'b', q09: { answer: '724' } });
    await ui.jump(10);
    await ui.jump(9);
    assert.equal(ui.inputs()[0].value, '724');
    await ui.practice.flush();
});

test('challenge questions keep optional hints closed and save both answer fields independently', async () => {
    const ui = practiceUI();await ui.flush();await ui.start();await ui.jump(31);
    assert.match(ui.field('practice-question-position').textContent, /思维拓展/);
    assert.equal(ui.field('practice-hints').hidden, false);
    assert.equal(ui.field('practice-hints').open, false);
    assert.equal(ui.field('practice-hint-items').children.length, 2);
    const inputs = ui.inputs();
    inputs[0].value = '12';await inputs[0].fire('input');
    inputs[1].value = '11';await inputs[1].fire('input');
    await ui.practice.flush();
    assert.deepEqual(ui.state.activeAttempt.answers.q31, { divisor: '12', remainder: '11' });
    assert.match(ui.field('practice-answer-fields').children[0].textContent, /3 分/);
    assert.equal(ui.field('practice-result').hidden, true);
});

test('failed autosave retains browser recovery and a retry persists it', async () => {
    const ui = practiceUI();await ui.flush();await ui.start();await ui.jump(9);
    ui.state.saveReply = () => ({ status: 503, data: { error: '网络暂时中断' } });
    ui.inputs()[0].value = '724';await ui.inputs()[0].fire('input');await ui.practice.flush();
    assert.equal(ui.field('practice-retry-save').hidden, false);
    assert.match(ui.context.sessionStorage.getItem('itorange-practice:math:alice'), /724/);
    ui.state.saveReply = null;
    await ui.field('practice-retry-save').fire('click');await ui.practice.flush();
    assert.equal(ui.state.activeAttempt.answers.q09.answer, '724');
});

test('submit failure retains answers and retry shows separate basic and challenge scores plus a saved record', async () => {
    const ui = practiceUI();await ui.flush();await ui.start();
    await ui.field('practice-choice-options').children[1].fire('click');
    ui.state.submitReply = () => ({ status: 503, data: { error: '交卷网络中断' } });
    await ui.field('practice-submit').fire('click');
    assert.match(ui.field('practice-submit-warning').textContent, /31/);
    await ui.field('practice-submit-confirm').fire('click');
    assert.equal(ui.field('practice-attempt').hidden, false);
    assert.equal(ui.field('practice-submit').disabled, false);
    const first = ui.state.calls.find((call) => call.url.endsWith('/submit')).body;
    ui.state.submitReply = null;
    await ui.field('practice-submit-confirm').fire('click');
    const second = ui.state.calls.filter((call) => call.url.endsWith('/submit'))[1].body;
    assert.deepEqual(first, second);
    assert.equal(ui.field('practice-result').hidden, false);
    assert.match(ui.field('practice-result-summary').textContent, /基础 90 \/ 90.*拓展 10 \/ 10/);
    assert.equal(ui.context.sessionStorage.getItem('itorange-practice:math:alice'), null);
    await ui.field('practice-back').fire('click');
    assert.equal(ui.field('record-count').textContent, '1');
});

test('cancelling abandonment from the recovery banner restores the existing answer instead of blanking it', async () => {
    const ui = practiceUI({ pending: true });await ui.flush();
    await ui.field('practice-resume-abandon').fire('click');
    assert.equal(ui.field('practice-abandon-confirmation').hidden, false);
    await ui.field('practice-abandon-cancel').fire('click');
    assert.equal(ui.inputs()[0].value, '724');
    assert.equal(ui.field('practice-abandon-confirmation').hidden, true);
});

test('abandonment clears recovery without grading and late draft responses cannot reopen it', async () => {
    const ui = practiceUI();await ui.flush();await ui.start();
    const pending = deferred();ui.state.saveReply = () => pending.promise;
    await ui.field('practice-choice-options').children[0].fire('click');
    const saving = ui.practice.flush();await settle();
    await ui.field('practice-abandon').fire('click');
    await ui.field('practice-abandon-confirm').fire('click');
    pending.resolve({ data: { revision: 1 } });await saving;await ui.flush();
    assert.equal(ui.field('subject-practice-view').hidden, true);
    assert.equal(ui.field('papers-panel').hidden, false);
    assert.equal(ui.field('practice-resume-banner').hidden, true);
    assert.equal(ui.context.sessionStorage.getItem('itorange-practice:math:alice'), null);
    assert.equal(ui.state.calls.some((call) => call.url.endsWith('/submit')), false);
});

test('logout while starting an online paper ignores late questions and clears private UI', async () => {
    const ui = practiceUI();await ui.flush();const pending = deferred();
    ui.state.startReply = () => pending.promise;
    const starting = ui.start();await settle();
    await ui.field('subject-logout').fire('click');
    pending.resolve({ data: { attempt: { id: 'a'.repeat(32), paper_title: 'Private late paper', questions: [] } } });
    await starting;
    assert.equal(ui.field('learning-view').hidden, true);
    assert.equal(ui.field('subject-practice-view').hidden, true);
    assert.equal(ui.field('practice-question-prompt').textContent, '');
});

test('parents can read submitted answers while start controls stay disabled', async () => {
    const ui = practiceUI({ parent: true });await ui.flush();
    const card = ui.field('papers-list').children[0];
    const start = card.querySelectorAll('button').find((button) => button.textContent.includes('学生账号'));
    assert.equal(start.disabled, true);
    await ui.start();
    assert.equal(ui.state.calls.some((call) => call.url.endsWith('/attempts')), false);
    await ui.practice.showResult('a'.repeat(32));
    assert.equal(ui.field('practice-result').hidden, false);
    assert.match(ui.field('practice-result-score').textContent, /100/);
});
