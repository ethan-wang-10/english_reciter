(() => {
    'use strict';

    const TOKEN_KEY = 'earthturtle_subject_token';
    const USERNAME_KEY = 'earthturtle_subject_username';
    const sections = ['papers', 'knowledge', 'records', 'account'];
    const gradeLabels = ['一年级', '二年级', '三年级', '四年级', '五年级', '预备班（六年级）', '初一（七年级）', '初二（八年级）', '初三（九年级）', '高一', '高二', '高三'];
    const el = (id) => document.getElementById(id);
    let config;
    try {
        config = JSON.parse(el('subject-site-config').textContent);
    } catch (_) {
        el('auth-error').hidden = false;
        el('auth-error').textContent = '页面配置加载失败，请刷新后重试。';
        return;
    }
    if (!config || !['chinese', 'math'].includes(config.id)) return;
    if (config.id === 'chinese') sections.splice(2, 0, 'writing', 'dictation');

    function readStorage(key) {
        try { return localStorage.getItem(key) || ''; } catch (_) { return ''; }
    }

    function writeStorage(key, value) {
        try {
            if (value) localStorage.setItem(key, value);
            else localStorage.removeItem(key);
        } catch (_) { /* The current page can still work without persistent storage. */ }
    }

    let token = readStorage(TOKEN_KEY);
    let username = readStorage(USERNAME_KEY);
    let account = null;
    let generation = 0;
    let bootstrapRequest = 0;
    let sectionRequest = 0;
    let authenticationRequest = 0;
    let authBusy = false;
    let logoutBusy = false;
    let activeSection = 'papers';
    let resetToken = '';
    let resources = { papers: [], knowledge: [], records: [] };
    const context = () => ({ generation, token });
    const current = (ctx) => ctx.generation === generation && ctx.token === token;
    const staleError = () => Object.assign(new Error('账号状态已更新。'), { stale: true });

    function feedback(id, text = '') {
        const node = el(id);
        node.textContent = text;
        node.hidden = !text;
    }

    function setAuthBusy(value) {
        authBusy = value;
        el('auth-view').setAttribute('aria-busy', String(value));
        el('auth-view').querySelectorAll('input, button').forEach((node) => { node.disabled = value; });
    }

    function showAuth(view = 'login', clearMessages = true) {
        document.body.dataset.view = 'auth';
        el('auth-view').hidden = false;
        el('learning-view').hidden = true;
        el('signed-in-actions').hidden = true;
        el('session-loading').hidden = true;
        ['login', 'register', 'forgot', 'reset'].forEach((name) => {
            el(`${name}-form`).hidden = name !== view;
        });
        el('auth-tabs').hidden = !['login', 'register'].includes(view);
        ['login', 'register'].forEach((name) => {
            const selected = name === view;
            el(`${name}-tab`).classList.toggle('active', selected);
            el(`${name}-tab`).setAttribute('aria-selected', String(selected));
            el(`${name}-tab`).tabIndex = selected ? 0 : -1;
        });
        if (clearMessages) {
            feedback('auth-error');
            feedback('auth-status');
        }
    }

    function clearSession(message = '', persist = true) {
        window.subjectDictation?.reset();
        window.subjectPractice?.reset();
        window.subjectWriting?.reset();
        generation += 1;
        bootstrapRequest += 1;
        sectionRequest += 1;
        authenticationRequest += 1;
        token = '';
        username = '';
        account = null;
        resources = { papers: [], knowledge: [], records: [] };
        if (persist) {
            writeStorage(TOKEN_KEY, '');
            writeStorage(USERNAME_KEY, '');
        }
        el('header-username').textContent = '';
        el('login-password').value = '';
        ['account-username', 'account-email', 'account-role', 'account-child'].forEach((id) => { el(id).textContent = '—'; });
        ['papers-list', 'knowledge-list', 'records-list'].forEach((id) => el(id).replaceChildren());
        showAuth('login');
        setAuthBusy(false);
        if (message) feedback('auth-status', message);
    }

    async function apiRequest(path, options = {}) {
        const ctx = context();
        const authenticated = options.auth !== false;
        const headers = { Accept: 'application/json', ...(options.headers || {}) };
        if (authenticated && token) headers.Authorization = `Bearer ${token}`;
        let body = options.body;
        if (body !== undefined && typeof body !== 'string') body = JSON.stringify(body);
        if (body !== undefined) headers['Content-Type'] = 'application/json';
        const url = path.startsWith('/api/') ? path : `/api${path.startsWith('/') ? path : `/${path}`}`;
        const response = await fetch(url, {
            method: options.method || 'GET', headers, body,
            credentials: 'same-origin', cache: 'no-store',
        });
        let data;
        try { data = await response.json(); } catch (_) { data = {}; }
        if (!current(ctx)) throw staleError();
        if (!response.ok) {
            if (response.status === 401 && authenticated && ctx.token) clearSession('登录已过期，请重新登录。');
            const error = new Error(data.error || data.detail || `请求失败（${response.status}），请稍后重试。`);
            error.status = response.status;
            error.code = data.code;
            error.current_revision = data.current_revision;
            throw error;
        }
        return data;
    }

    function safeResourceUrl(value) {
        if (typeof value !== 'string' || !value) return '';
        try {
            const url = new URL(value, location.origin);
            return url.origin === location.origin && ['http:', 'https:'].includes(url.protocol) ? url.href : '';
        } catch (_) { return ''; }
    }

    function subjectTargets() {
        const subjects = Array.isArray(config.subjects) ? config.subjects : [];
        const result = subjects.filter((item) => item && typeof item.id === 'string' && typeof item.name === 'string' && typeof item.url === 'string');
        if (config.english_url && !result.some((item) => item.id === 'english')) {
            result.unshift({ id: 'english', name: '英语', url: config.english_url });
        }
        return result;
    }

    async function switchSubject(target) {
        if (!target || target.id === config.id) return;
        if (window.subjectSSO && typeof window.subjectSSO.navigate === 'function' && token) {
            const ctx = context();
            try {
                feedback('portal-error');
                feedback('portal-status', `正在前往${target.name}…`);
                await window.subjectSSO.navigate(target.id, { token, username, account });
            } catch (error) {
                if (current(ctx)) {
                    feedback('portal-status');
                    feedback('portal-error', error.message || '切换学科失败，请稍后重试。');
                }
            }
            return;
        }
        let url;
        try { url = new URL(target.url); } catch (_) { return; }
        if (!['http:', 'https:'].includes(url.protocol)) return;
        window.location.assign(url.href);
    }

    function renderSubjectLinks() {
        for (const id of ['header-subjects', 'account-subjects']) {
            const list = el(id);
            list.replaceChildren();
            for (const target of subjectTargets()) {
                let url;
                try { url = new URL(target.url); } catch (_) { continue; }
                if (!['http:', 'https:'].includes(url.protocol)) continue;
                const link = document.createElement('a');
                link.href = url.href;
                link.textContent = target.name;
                if (target.id === config.id) link.setAttribute('aria-current', 'page');
                link.addEventListener('click', (event) => {
                    event.preventDefault();
                    void switchSubject(target);
                });
                list.appendChild(link);
            }
        }
    }

    function displayAccount() {
        const displayed = account?.display_name || account?.username || username;
        el('header-username').textContent = displayed;
        el('header-avatar').textContent = displayed ? [...displayed][0] : '同';
        el('header-account').setAttribute('aria-label', `我的账号：${displayed}`);
        el('account-username').textContent = account?.username || username || '—';
        el('account-email').textContent = account?.email || '未绑定邮箱';
        el('account-role').textContent = account?.is_parent ? '家长账号' : '学生账号';
        el('account-child-row').hidden = !account?.is_parent;
        el('account-child').textContent = account?.child_username || '—';
        el('records-description').textContent = account?.is_parent
            ? `查看${account.child_username || '关联学生'}在${config.name}学科的学习情况。`
            : '完成考卷后，可以在这里回顾学习情况。';
    }

    function updateSummary(summary = {}) {
        const count = (value, fallback) => Number.isSafeInteger(value) && value >= 0 ? value : fallback;
        el('paper-count').textContent = String(count(summary.paper_count, resources.papers.length));
        el('knowledge-count').textContent = String(count(summary.knowledge_count, resources.knowledge.length));
        el('record-count').textContent = String(count(summary.record_count, resources.records.length));
    }

    function metadata(item) {
        const parts = [];
        if (Number.isInteger(item.grade) && gradeLabels[item.grade - 1]) parts.push(gradeLabels[item.grade - 1]);
        if (item.term === 1 || item.term === 2) parts.push(item.term === 1 ? '上册' : '下册');
        if (item.exam_kind === 'midterm') parts.push('期中');
        else if (item.exam_kind === 'final') parts.push('期末');
        return parts.join(' · ');
    }

    function renderResources(name) {
        const items = resources[name];
        const list = el(`${name}-list`);
        list.replaceChildren();
        const visible = name === 'papers' ? items.filter((item) => {
            const grade = el('paper-grade-filter').value;
            const term = el('paper-term-filter').value;
            const phase = el('paper-phase-filter').value;
            return (!grade || String(item.grade) === grade) && (!term || String(item.term) === term) && (!phase || item.exam_kind === phase);
        }) : items;
        el(`${name}-result-count`).textContent = `共 ${visible.length} ${name === 'records' ? '条' : '份'}`;
        el(`${name}-empty`).hidden = visible.length > 0;
        if (name === 'papers') {
            el('papers-empty-title').textContent = items.length ? '暂时没有符合条件的考卷' : '内置考卷正在准备中';
            el('papers-empty-description').textContent = items.length
                ? '试试其他年级、学期或考试类型。'
                : '考卷上线后，会按年级和学期收录在这里。下次来，继续发现新的学习内容。';
        }
        for (const item of visible) {
            const card = document.createElement('article');
            card.className = name === 'records' ? 'record-row' : 'resource-card';
            const content = document.createElement('div');
            if (metadata(item)) {
                const meta = document.createElement('p');
                meta.className = 'resource-meta';
                meta.textContent = metadata(item);
                content.appendChild(meta);
            }
            const heading = document.createElement('h3');
            heading.textContent = String(item.name || item.title || item.paper_title || (name === 'records' ? '学习记录' : '学习材料'));
            content.appendChild(heading);
            if (item.scope) {
                const scope = document.createElement('p');
                scope.className = 'resource-meta';
                scope.textContent = `范围：${item.scope}`;
                content.appendChild(scope);
            }
            const description = item.description || (name === 'records' ? String(item.submitted_at || '').replace('T', ' ').slice(0, 16) : '');
            if (description) {
                const text = document.createElement('p');
                text.textContent = String(description);
                content.appendChild(text);
            }
            const url = safeResourceUrl(item.url || item.pdf_url);
            if (url) {
                const link = document.createElement('a');
                link.href = url;
                link.textContent = name === 'papers' ? '查看考卷 →' : '打开阅读 →';
                content.appendChild(link);
            }
            if (name === 'papers' && item.interactive) {
                const start = document.createElement('button');
                start.type = 'button';
                start.className = 'primary-button resource-start';
                start.textContent = account?.is_parent ? '请使用学生账号练习' : '开始练习';
                start.disabled = !!account?.is_parent;
                start.addEventListener('click', () => void window.subjectPractice?.start(item.id));
                content.appendChild(start);
            }
            if (name === 'papers' && item.knowledge_id) {
                const knowledge = document.createElement('button');
                knowledge.type = 'button';
                knowledge.className = 'text-button resource-knowledge';
                knowledge.textContent = '知识点梳理';
                knowledge.addEventListener('click', () => {
                    showSection('knowledge');
                    const target = el('knowledge-list').querySelector(`[data-knowledge-id="${item.knowledge_id}"]`);
                    const details = target?.querySelector('details');
                    if (details) details.open = true;
                    target?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
                });
                content.appendChild(knowledge);
            }
            if (name === 'records' && /^[a-f0-9]{32}$/.test(item.id || '')) {
                const review = document.createElement('button');
                review.type = 'button';
                review.className = 'text-button';
                review.textContent = '查看答卷与解析';
                review.addEventListener('click', () => void window.subjectPractice?.showResult(item.id));
                content.appendChild(review);
            }
            if (name === 'knowledge' && Array.isArray(item.sections) && item.sections.length) {
                card.setAttribute('data-knowledge-id', item.id);
                const details = document.createElement('details');
                details.className = 'knowledge-details';
                const summary = document.createElement('summary');
                summary.textContent = '展开知识点';
                details.appendChild(summary);
                for (const section of item.sections) {
                    const title = document.createElement('h4');
                    title.textContent = section.title;
                    details.appendChild(title);
                    const items = document.createElement('ul');
                    for (const value of section.items || []) {
                        const line = document.createElement('li');
                        line.textContent = value;
                        items.appendChild(line);
                    }
                    details.appendChild(items);
                }
                content.appendChild(details);
            }
            if (name === 'records' && item.kind === 'writing' && /^writing_[a-f0-9]{32}$/.test(item.id || '')) {
                const review = document.createElement('button');
                review.type = 'button';
                review.className = 'text-button';
                review.textContent = '查看作文与修改';
                review.addEventListener('click', () => {
                    showSection('writing');
                    void window.subjectWriting?.openAttempt(item.id);
                });
                content.appendChild(review);
            }
            card.appendChild(content);
            if (name === 'records' && typeof item.score === 'number' && Number.isFinite(item.score)) {
                const score = document.createElement('span');
                score.className = 'record-score';
                score.textContent = `${item.score} 分`;
                card.appendChild(score);
            }
            list.appendChild(card);
        }
    }

    function showSection(name, focus = false) {
        if (!sections.includes(name) || !token || !account) return;
        window.subjectPractice?.leaveView();
        if (name !== 'writing') window.subjectWriting?.leaveView();
        if (name !== 'dictation') window.subjectDictation?.leaveView();
        activeSection = name;
        sections.forEach((section) => {
            const selected = section === name;
            el(`${section}-panel`).hidden = !selected;
            el(`${section}-tab`).classList.toggle('active', selected);
            el(`${section}-tab`).setAttribute('aria-selected', String(selected));
            el(`${section}-tab`).tabIndex = selected ? 0 : -1;
        });
        if (focus) el(`${name}-tab`).focus({ preventScroll: true });
        if (name === 'writing') void window.subjectWriting?.open();
        if (name === 'dictation') void window.subjectDictation?.open();
    }

    async function refreshSection(name) {
        showSection(name);
        const requestId = ++sectionRequest;
        feedback('portal-status');
        if (name === 'account' || name === 'writing' || name === 'dictation' || !token || !account) return;
        const ctx = context();
        feedback('portal-error');
        feedback('portal-status', '正在加载…');
        try {
            const data = await apiRequest(`/subjects/${config.id}/${name}`);
            if (!current(ctx) || requestId !== sectionRequest) return;
            resources[name] = Array.isArray(data.items) ? data.items.filter((item) => item && typeof item === 'object') : [];
            renderResources(name);
            updateSummary();
        } catch (error) {
            if (current(ctx) && requestId === sectionRequest) feedback('portal-error', error.message || '内容加载失败，请稍后重试。');
        } finally {
            if (current(ctx) && requestId === sectionRequest) feedback('portal-status');
        }
    }

    async function refreshRecords() {
        if (!token || !account) return;
        const ctx = context();
        try {
            const result = await apiRequest(`/subjects/${config.id}/records`);
            if (!current(ctx)) return;
            resources.records = Array.isArray(result.items) ? result.items : [];
            renderResources('records');
            updateSummary();
        } catch (error) {
            if (current(ctx)) feedback('portal-error', '作品已保存，学习记录暂未刷新，可稍后重新打开查看。');
        }
    }

    async function bootstrap() {
        if (!token) return;
        const ctx = context();
        const requestId = ++bootstrapRequest;
        el('session-loading').hidden = false;
        try {
            const data = await apiRequest(`/subjects/${config.id}/bootstrap`);
            if (!current(ctx) || requestId !== bootstrapRequest) return;
            if (!data.account || typeof data.account.username !== 'string' || !data.account.username) {
                throw new Error('账号信息加载失败，请稍后重试。');
            }
            account = data.account;
            username = account.username || username;
            writeStorage(USERNAME_KEY, username);
            for (const name of ['papers', 'knowledge', 'records']) {
                resources[name] = Array.isArray(data[name]) ? data[name].filter((item) => item && typeof item === 'object') : [];
                renderResources(name);
            }
            updateSummary(data.summary || {});
            displayAccount();
            el('auth-view').hidden = true;
            el('learning-view').hidden = false;
            document.body.dataset.view = 'learning';
            el('signed-in-actions').hidden = false;
            el('session-loading').hidden = true;
            feedback('portal-error');
            showSection(activeSection);
            void window.subjectPractice?.refreshActive();
        } catch (error) {
            if (current(ctx) && requestId === bootstrapRequest) {
                showAuth('login', false);
                feedback('auth-error', error.message || '登录状态检查失败，请稍后重试。');
            }
        } finally {
            if (current(ctx) && requestId === bootstrapRequest) el('session-loading').hidden = true;
        }
    }

    async function acceptSession(data) {
        if (!data || typeof data.access_token !== 'string' || !data.access_token) throw new Error('登录返回无效，请重试。');
        generation += 1;
        token = data.access_token;
        username = typeof data.username === 'string' ? data.username : '';
        account = null;
        resources = { papers: [], knowledge: [], records: [] };
        activeSection = 'papers';
        writeStorage(TOKEN_KEY, token);
        writeStorage(USERNAME_KEY, username);
        el('login-password').value = '';
        await bootstrap();
    }

    async function submitAuthentication(event, path, body) {
        event.preventDefault();
        if (authBusy) return;
        const ctx = context();
        const requestId = ++authenticationRequest;
        setAuthBusy(true);
        feedback('auth-error');
        feedback('auth-status');
        try {
            const data = await apiRequest(path, { method: 'POST', body, auth: false });
            if (!current(ctx)) return;
            await acceptSession(data);
        } catch (error) {
            if (!error.stale && requestId === authenticationRequest) feedback('auth-error', error.message || '操作失败，请稍后重试。');
        } finally {
            if (requestId === authenticationRequest) setAuthBusy(false);
        }
    }

    async function logout() {
        if (logoutBusy) return;
        logoutBusy = true;
        const logoutToken = token;
        el('subject-logout').disabled = true;
        clearSession();
        setAuthBusy(true);
        try {
            if (logoutToken) {
                const response = await fetch('/api/auth/logout', {
                    method: 'POST', headers: { Authorization: `Bearer ${logoutToken}`, Accept: 'application/json' },
                    credentials: 'same-origin', cache: 'no-store',
                });
                if (!response.ok && response.status !== 401) throw new Error('退出请求未完成。');
            }
            if (!token) feedback('auth-status', '已退出登录。');
        } catch (_) {
            if (!token) feedback('auth-status', '本机已退出。网络恢复后，如需结束其他设备的会话，请重新登录后退出。');
        } finally {
            logoutBusy = false;
            el('subject-logout').disabled = false;
            setAuthBusy(false);
        }
    }

    function configurePage() {
        document.body.dataset.subject = config.id;
        el('writing-tab').hidden = config.id !== 'chinese';
        el('dictation-tab').hidden = config.id !== 'chinese';
        document.title = config.title || `${config.name}学习空间`;
        const character = config.id === 'math' ? '数' : '语';
        el('header-subject-name').textContent = config.name;
        el('auth-brand-title').textContent = `${config.name}学习`;
        el('auth-subject-badge').textContent = `${config.name} · 学习空间`;
        el('dashboard-subject-badge').textContent = `${config.name} · 学习空间`;
        el('footer-subject').textContent = `${config.name}学习空间`;
        el('illustration-character').textContent = character;
        el('dashboard-character').textContent = character;
        el('auth-heading').textContent = config.id === 'math' ? '想明白每一步，解开每一道题。' : '读懂每一篇，写好每一笔。';
        if (config.description) {
            el('auth-description').textContent = config.description;
            el('dashboard-description').textContent = config.description;
        }
        document.querySelector('meta[name="theme-color"]').setAttribute('content', config.id === 'math' ? '#1cb0f6' : '#8b5cf6');
        for (const [stage, start, end] of [['小学', 1, 5], ['初中', 6, 9], ['高中', 10, 12]]) {
            const group = document.createElement('optgroup');
            group.label = stage;
            for (let grade = start; grade <= end; grade += 1) {
                const option = document.createElement('option');
                option.value = String(grade);
                option.textContent = gradeLabels[grade - 1];
                group.appendChild(option);
            }
            el('paper-grade-filter').appendChild(group);
        }
        renderSubjectLinks();
    }

    configurePage();
    el('login-tab').addEventListener('click', () => showAuth('login'));
    el('register-tab').addEventListener('click', () => showAuth('register'));
    el('auth-tabs').addEventListener('keydown', (event) => {
        if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || authBusy) return;
        event.preventDefault();
        const next = el('login-tab').getAttribute('aria-selected') === 'true' ? 'register' : 'login';
        showAuth(next);
        el(`${next}-tab`).focus();
    });
    el('forgot-open').addEventListener('click', () => showAuth('forgot'));
    document.querySelectorAll('.back-to-login').forEach((button) => button.addEventListener('click', () => showAuth('login')));
    el('login-form').addEventListener('submit', (event) => submitAuthentication(event, '/auth/login', {
        username: el('login-username').value.trim(), password: el('login-password').value,
    }));
    el('register-form').addEventListener('submit', (event) => {
        event.preventDefault();
        if (authBusy) return;
        if (el('register-password').value !== el('register-password-confirm').value) {
            feedback('auth-error', '两次输入的密码不一致。');
            return;
        }
        return submitAuthentication(event, '/auth/register', {
            username: el('register-username').value.trim(), password: el('register-password').value,
            email: el('register-email').value.trim(), invite_code: el('register-invite').value.trim(),
        });
    });
    el('forgot-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        if (authBusy) return;
        const ctx = context();
        setAuthBusy(true);
        feedback('auth-error');
        feedback('auth-status');
        try {
            const data = await apiRequest('/auth/forgot-password', { method: 'POST', body: { email: el('forgot-email').value.trim() }, auth: false });
            if (current(ctx)) feedback('auth-status', data.message || '如果邮箱已绑定账号，重置邮件将在几分钟内发送。');
        } catch (error) {
            if (current(ctx)) feedback('auth-error', error.message || '邮件发送失败，请稍后重试。');
        } finally { if (current(ctx)) setAuthBusy(false); }
    });
    el('reset-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        if (authBusy) return;
        if (el('reset-password').value !== el('reset-password-confirm').value) {
            feedback('auth-error', '两次输入的密码不一致。');
            return;
        }
        const ctx = context();
        setAuthBusy(true);
        feedback('auth-error');
        feedback('auth-status');
        try {
            const data = await apiRequest('/auth/reset-password', {
                method: 'POST', auth: false,
                body: { token: resetToken, password: el('reset-password').value, password_confirm: el('reset-password-confirm').value },
            });
            if (!current(ctx)) return;
            resetToken = '';
            clearSession();
            el('reset-form').reset();
            feedback('auth-status', data.message || '密码已更新，请使用新密码登录。');
        } catch (error) {
            if (current(ctx)) feedback('auth-error', error.message || '密码重置失败，请重新申请重置链接。');
        } finally { setAuthBusy(false); }
    });
    document.querySelectorAll('[data-section]').forEach((button) => {
        button.addEventListener('click', () => void refreshSection(button.dataset.section));
    });
    el('learning-tabs').addEventListener('keydown', (event) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const index = sections.indexOf(activeSection);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? sections.length - 1
            : (index + (event.key === 'ArrowRight' ? 1 : -1) + sections.length) % sections.length;
        showSection(sections[next], true);
        void refreshSection(sections[next]);
    });
    el('header-account').addEventListener('click', () => showSection('account', true));
    el('subject-logout').addEventListener('click', logout);
    ['paper-grade-filter', 'paper-term-filter', 'paper-phase-filter'].forEach((id) => {
        el(id).addEventListener('change', () => renderResources('papers'));
    });
    window.addEventListener('storage', (event) => {
        if (event.key === null) {
            if (readStorage(TOKEN_KEY) !== token) clearSession('', false);
        } else if (event.key === TOKEN_KEY && event.newValue !== token) {
            clearSession('', false);
            if (event.newValue) {
                token = event.newValue;
                username = readStorage(USERNAME_KEY);
                void bootstrap();
            }
        }
    });

    window.subjectPortal = {
        apiRequest, acceptSession, switchSubject, showSection, refreshSection, refreshRecords,
        getSession: () => ({ token, username, account, subject: config.id }),
        refresh: bootstrap,
    };
    const url = new URL(location.href);
    const invite = url.searchParams.get('invite');
    resetToken = url.searchParams.get('reset_token') || '';
    if (invite || resetToken) {
        url.searchParams.delete('invite');
        url.searchParams.delete('reset_token');
        history.replaceState(null, '', url.pathname + url.search + url.hash);
    }
    if (resetToken) showAuth('reset');
    else if (invite && !token) {
        el('register-invite').value = invite;
        showAuth('register');
    } else if (token) void bootstrap();
    else showAuth('login');
})();
