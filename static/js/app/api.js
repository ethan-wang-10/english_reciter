// API 请求
async function apiRequest(endpoint, options = {}) {
    const accountContext = captureAccountContext();
    const requestToken = accountContext.token;
    const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData;
    const headers = {
        ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
        ...options.headers
    };
    
    if (requestToken) {
        headers['Authorization'] = `Bearer ${requestToken}`;
    }
    
    const response = await fetch(`${API_BASE}${endpoint}`, {
        ...options,
        headers
    });
    
    const text = await response.text();
    const data = parseApiJsonBody(text);

    if (!response.ok) {
        const error = data && typeof data === 'object' ? data : {};

        if (response.status === 401 && isAccountContextCurrent(accountContext)) {
            clearAccountSession();
            showLoginPage();
        }

        const requestError = new Error(error.error || error.detail || '请求失败');
        requestError.status = response.status;
        requestError.code = error.code || '';
        requestError.missing_words = Array.isArray(error.missing_words)
            ? error.missing_words.filter((word) => typeof word === 'string' && word.trim()) : [];
        throw requestError;
    }
    
    return data;
}

/** 单词学习：会话内缓存 + If-None-Match，配合服务端 fields=minimal 与 ETag */
let wordbankCsvDiscoveryCache = { key: '', etag: '', data: null };

/** 同一 URL 仅保留一个进行中的 fetch，避免预取与搜索并发重复下载 */
const wordbankDiscoveryCsvInflight = new Map();

/** 内存命中后节流后台校验（若词库文件更新则刷新缓存） */
const discoveryWordbankRevalidateState = { lastAt: 0, minIntervalMs: 60_000 };

function clearWordbankCsvDiscoveryCache() {
    wordbankCsvDiscoveryCache = { key: '', etag: '', data: null };
    wordbankDiscoveryCsvInflight.clear();
    discoveryWordbankRevalidateState.lastAt = 0;
}

/**
 * 全量 minimal 词库是否已在内存（用于搜索时是否展示「加载词库」）
 */
function discoveryWordbankFullCsvCached() {
    const c = wordbankCsvDiscoveryCache;
    return (
        c.key === '/wordbank/csv?fields=minimal' &&
        c.data &&
        Array.isArray(c.data.words)
    );
}

function maybeRevalidateWordbankDiscoveryCache(pathKey) {
    const accountContext = captureAccountContext();
    const c = wordbankCsvDiscoveryCache;
    if (c.key !== pathKey || !c.etag) return;
    const now = Date.now();
    if (now - discoveryWordbankRevalidateState.lastAt < discoveryWordbankRevalidateState.minIntervalMs) {
        return;
    }
    discoveryWordbankRevalidateState.lastAt = now;
    void (async () => {
        try {
            const headers = { 'Content-Type': 'application/json' };
            if (token) headers.Authorization = `Bearer ${token}`;
            if (wordbankCsvDiscoveryCache.key !== pathKey || !wordbankCsvDiscoveryCache.etag) return;
            headers['If-None-Match'] = wordbankCsvDiscoveryCache.etag;
            const response = await fetch(`${API_BASE}${pathKey}`, {
                headers,
                cache: 'no-store',
            });
            if (response.status === 304 || response.status === 401) return;
            if (!response.ok) return;
            const etag = response.headers.get('ETag') || '';
            const data = await response.json();
            if (!isAccountContextCurrent(accountContext) || wordbankCsvDiscoveryCache !== c) return;
            wordbankCsvDiscoveryCache = { key: pathKey, etag, data };
        } catch (_) {
            /* 后台刷新失败不影响已缓存数据 */
        }
    })();
}

async function fetchWordbankCsvForDiscoveryNetwork(level, pathKey) {
    const accountContext = captureAccountContext();
    const headers = { 'Content-Type': 'application/json' };
    if (accountContext.token) headers.Authorization = `Bearer ${accountContext.token}`;
    const cached = wordbankCsvDiscoveryCache;
    if (cached.key === pathKey && cached.etag && cached.data) {
        headers['If-None-Match'] = cached.etag;
    }
    const response = await fetch(`${API_BASE}${pathKey}`, {
        headers,
        cache: 'no-store',
    });
    if (!isAccountContextCurrent(accountContext)) throw new Error('登录账号已变更，已取消词库加载');
    if (response.status === 304) {
        if (cached.data && cached.key === pathKey) return cached.data;
        clearWordbankCsvDiscoveryCache();
        return fetchWordbankCsvForDiscovery(level);
    }
    if (response.status === 401) {
        clearAccountSession();
        showLoginPage();
        const error = await response.json().catch(() => ({}));
        throw new Error(error.error || error.detail || '请求失败');
    }
    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.error || error.detail || '请求失败');
    }
    const etag = response.headers.get('ETag') || '';
    const data = await response.json();
    if (isAccountContextCurrent(accountContext) && wordbankCsvDiscoveryCache === cached) {
        wordbankCsvDiscoveryCache = { key: pathKey, etag, data };
    }
    return data;
}

/**
 * 拉取系统词库（仅 discovery 使用）：与后端 v2+CSV 合并结果一致，minimal 字段、按 level 缓存、304 复用内存。
 * 全量词库在内存命中时同步返回，避免每次搜索再发 HTTP（304 仍有 RTT）。
 * @param {string} level 难度或空字符串表示全部
 */
async function fetchWordbankCsvForDiscovery(level) {
    const params = new URLSearchParams();
    if (level) params.set('level', level);
    params.set('fields', 'minimal');
    const pathKey = `/wordbank/csv?${params.toString()}`;
    const cached = wordbankCsvDiscoveryCache;

    if (cached.key === pathKey && cached.data && cached.etag) {
        maybeRevalidateWordbankDiscoveryCache(pathKey);
        return Promise.resolve(cached.data);
    }

    if (wordbankDiscoveryCsvInflight.has(pathKey)) {
        return wordbankDiscoveryCsvInflight.get(pathKey);
    }

    const p = fetchWordbankCsvForDiscoveryNetwork(level, pathKey).finally(() => {
        if (wordbankDiscoveryCsvInflight.get(pathKey) === p) wordbankDiscoveryCsvInflight.delete(pathKey);
    });
    wordbankDiscoveryCsvInflight.set(pathKey, p);
    return p;
}

/** 进入单词学习页时后台预取全量词库，减少首次输入搜索时的冷启动等待 */
function prefetchDiscoveryWordbankForSearch() {
    if (!token) return;
    void fetchWordbankCsvForDiscovery('').catch(() => {});
}

function mountDeferredAppShell() {
    const t = document.getElementById('deferred-app-shell');
    const app = document.getElementById('app');
    if (!t || !app) return;
    app.appendChild(t.content.cloneNode(true));
    t.remove();
}

