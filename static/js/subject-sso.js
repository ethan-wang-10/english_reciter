/* One-time PKCE handoff. Ordinary login tokens never enter a URL. */
(() => {
    'use strict';
    const PENDING_KEY = 'earthturtle_subject_sso_pending';
    const SUBJECT_TOKEN_KEY = 'earthturtle_subject_token';
    const SUBJECT_USERNAME_KEY = 'earthturtle_subject_username';
    const MAX_FLOW_AGE_MS = 5 * 60 * 1000;
    const el = (id) => document.getElementById(id);

    function base64url(bytes) {
        return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }

    function randomValue() {
        return base64url(crypto.getRandomValues(new Uint8Array(32)));
    }

    async function challengeFor(verifier) {
        const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
        return base64url(new Uint8Array(digest));
    }

    async function jsonRequest(path, options = {}) {
        const response = await fetch(path, { cache: 'no-store', credentials: 'omit', ...options });
        let data;
        try { data = await response.json(); } catch (_) { throw new Error('登录服务暂不可用，请稍后重试。'); }
        if (!response.ok) throw new Error(data.error || '登录切换失败，请重新登录。');
        return data;
    }

    function post(path, body, token) {
        return jsonRequest(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
            body: JSON.stringify(body),
        });
    }

    function home(config) {
        return config.sites[config.current_site].url;
    }

    function fail(error, config) {
        if (el('sso-title')) el('sso-title').textContent = '暂时无法切换学科';
        if (el('sso-status')) el('sso-status').textContent = error.message || '请返回学科首页重新登录。';
        const link = el('sso-return');
        if (link) {
            link.href = home(config);
            link.hidden = false;
        }
    }

    function sessionToken(site) {
        return localStorage.getItem(site === 'english' ? 'token' : SUBJECT_TOKEN_KEY);
    }

    function storeSession(site, session) {
        const english = site === 'english';
        const tokenKey = english ? 'token' : SUBJECT_TOKEN_KEY;
        const usernameKey = english ? 'username' : SUBJECT_USERNAME_KEY;
        localStorage.setItem(tokenKey, session.access_token);
        localStorage.setItem(usernameKey, session.username);
        if (english) {
            if (session.is_parent) {
                localStorage.setItem('session_is_parent', '1');
                localStorage.setItem('session_child_username', session.child_username || '');
            } else {
                localStorage.removeItem('session_is_parent');
                localStorage.removeItem('session_child_username');
            }
        }
    }

    async function start(config) {
        if (!crypto.subtle) throw new Error('请使用 HTTPS 页面切换学科。');
        const state = randomValue();
        const verifier = randomValue();
        const challenge = await challengeFor(verifier);
        sessionStorage.setItem(PENDING_KEY, JSON.stringify({
            state, verifier, source: config.source, target: config.current_site, createdAt: Date.now(),
        }));
        const authorize = new URL('/auth/subject-authorize', config.sites[config.source].url);
        authorize.searchParams.set('target', config.current_site);
        authorize.searchParams.set('state', state);
        authorize.searchParams.set('code_challenge', challenge);
        location.replace(authorize.href);
    }

    async function authorize(config) {
        const token = sessionToken(config.current_site);
        if (!token) throw new Error('登录已失效，请返回学科首页重新登录。');
        const result = await post('/api/auth/sso/ticket', {
            target: config.target, code_challenge: config.code_challenge,
        }, token);
        if (typeof result.code !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(result.code)) {
            throw new Error('登录服务返回无效，请重新切换学科。');
        }
        const callback = new URL('/auth/subject-callback', config.sites[config.target].url);
        callback.searchParams.set('code', result.code);
        callback.searchParams.set('state', config.state);
        location.replace(callback.href);
    }

    async function callback(config) {
        const params = new URLSearchParams(location.search);
        // Remove the short-lived code before any fetch or error is displayed.
        history.replaceState(null, '', location.pathname);
        const code = params.get('code');
        const state = params.get('state');
        let pending;
        try { pending = JSON.parse(sessionStorage.getItem(PENDING_KEY) || 'null'); } catch (_) { pending = null; }
        if (!pending || pending.state !== state || pending.target !== config.current_site
                || !config.sites[pending.source] || typeof pending.verifier !== 'string'
                || !Number.isFinite(pending.createdAt) || Date.now() - pending.createdAt > MAX_FLOW_AGE_MS
                || pending.createdAt > Date.now() + 1000 || !/^[A-Za-z0-9_-]{43}$/.test(code || '')) {
            throw new Error('登录切换校验失败，请从原学科重新切换。');
        }
        sessionStorage.removeItem(PENDING_KEY);
        const session = await post('/api/auth/sso/exchange', {
            code, code_verifier: pending.verifier, target: pending.target,
        });
        if (typeof session.access_token !== 'string' || typeof session.username !== 'string') {
            throw new Error('登录服务返回无效，请重新登录。');
        }
        storeSession(config.current_site, session);
        location.replace(home(config));
    }

    async function navigate(target) {
        const config = await jsonRequest('/api/auth/sso/sites');
        const destination = config.sites[target] || Object.values(config.sites).find((site) => site.url === target || site.url.replace(/\/$/, '') === target);
        if (!destination) throw new Error('目标学科站点无效。');
        if (destination.id === config.current_site) {
            location.assign(destination.url);
            return;
        }
        const url = new URL('/auth/subject-start', destination.url);
        url.searchParams.set('from', config.current_site);
        location.assign(url.href);
    }

    window.subjectSSO = { navigate };
    const node = el('subject-sso-config');
    if (node) {
        let config;
        try { config = JSON.parse(node.textContent); } catch (_) { return; }
        const handler = { start, authorize, callback }[config.mode];
        if (handler) Promise.resolve().then(() => handler(config)).catch((error) => fail(error, config));
    }
})();
