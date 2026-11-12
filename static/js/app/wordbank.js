// ==================== 进度功能 ====================

function _renderProgressWordItem(word) {
    const nextLine = escapeHtml(formatNextReviewLine(word));
    const phoneticHtml = word.phonetic ? `<span class="word-item-phonetic">${escapeHtml(word.phonetic)}</span>` : '';
    const coTag = word.is_carryover
        ? `<span class="word-item-carryover-tag">遗留${word.carryover_days ? ` · 逾期${word.carryover_days}天` : ''}</span>`
        : '';
    const reinforcementTag = word.memory_status === 'reinforcement'
        ? '<span class="word-item-carryover-tag">待巩固</span>'
        : '';
    const masteryPercent = Number(word.mastery?.overall_percent);
    const masteryValue = Number.isFinite(masteryPercent)
        ? `${Math.round(masteryPercent)}%`
        : `${word.success_count}/${word.max_success_count}`;
    const masteryTitle = Number.isFinite(masteryPercent)
        ? `${masteryDimensionText(word, 'recognition', '识义')} · ${masteryDimensionText(word, 'context', '语境')} · ${masteryDimensionText(word, 'spelling', '拼写')} · ${masteryDimensionText(word, 'listening', '听写')}`
        : '';
    const masteryDetail = Number.isFinite(masteryPercent)
        ? `<div class="word-stat-detail">${escapeHtml(masteryTitle)}</div>`
        : '';
    return `<div class="word-item">
        <div class="word-item-info">
            <div class="word-item-english">${escapeHtml(word.english)}${phoneticHtml}</div>
            <div class="word-item-chinese">${escapeHtml(word.chinese)}</div>
            <div class="word-item-next-review">下次复习：${nextLine} ${coTag}${reinforcementTag}</div>
        </div>
        <div class="word-item-stats">
            <div class="word-stat">
                <div class="word-stat-value"${masteryTitle ? ` title="${escapeHtml(masteryTitle)}"` : ''}>${escapeHtml(masteryValue)}</div>
                <div class="word-stat-label">多维掌握</div>
                ${masteryDetail}
            </div>
            <div class="word-stat">
                <div class="word-stat-value">${escapeHtml(word.review_count)}</div>
                <div class="word-stat-label">复习次数</div>
            </div>
        </div>
    </div>`;
}

const _LIST_BATCH_SIZE = 50;
const _listRenderCleanup = new WeakMap();
let progressRequestSequence = 0;
let masteredRequestSequence = 0;

function cancelBatchRenderList(container) {
    if (container) _listRenderCleanup.get(container)?.();
}

function resetAccountListViews() {
    progressRequestSequence++;
    masteredRequestSequence++;
    leaderboardRequestSequence++;
    settingsRequestSequence++;
    settingsPendingRequestSequence++;
    lastLeaderboardTablePayload = null;
    cancelDiscoverySearchRequest();
    clearTimeout(discoverySearchDebounceTimer);
    discoveryDeck = [];
    discoveryIndex = 0;
    discoveryPendingKeys = new Set();
    discoveryMasteredKeys = new Set();
    discoveryImportBusy = false;
    clearTimeout(wbState.searchTimer);
    wbState.searchSeq++;
    wbState.searchAbort?.abort();
    wbState.searchAbort = null;
    wbState.filtered = [];
    wbState.filter = '';
    wbState.selected.clear();
    wbState.selectedMap.clear();
    wbState.loading = false;
    wbState.importBusy = false;
    const wordbankSearch = document.getElementById('wordbank-search');
    if (wordbankSearch) wordbankSearch.value = '';
    const wordbankImportButton = document.getElementById('wordbank-import-btn');
    if (wordbankImportButton) {
        wordbankImportButton.disabled = false;
        wordbankImportButton.textContent = '将选中的词加入待复习';
    }
    updateWordbankSelectedCount();
    renderWordbankMeta();
    renderWordbankList();
    if (typeof resetTextbookAccountState === 'function') resetTextbookAccountState();
    Object.keys(_sectionLastLoad).forEach((key) => delete _sectionLastLoad[key]);
    Object.keys(_sectionScrollPos).forEach((key) => delete _sectionScrollPos[key]);
    ['word-list', 'progress-stats', 'mastered-list', 'learning-map', 'learning-map-hint',
        'leaderboard-table-wrap', 'leaderboard-podium-wrap', 'monthly-pool-race-wrap',
        'monthly-pk-board-wrap', 'achievements-grid', 'daily-summary-body', 'xp-history-body',
        'discovery-card-wrap', 'discovery-counter', 'discovery-search-suggestions',
        'settings-pending-words-list', 'settings-message', 'settings-email-message'].forEach((id) => {
        const container = document.getElementById(id);
        if (container) {
            cancelBatchRenderList(container);
            container.innerHTML = '';
        }
    });
    const email = document.getElementById('settings-email');
    if (email) email.value = '';
    document.querySelectorAll('#settings-overlay input[type="password"]').forEach((input) => { input.value = ''; });
    ['settings-pending-words-refresh', 'settings-learning-limits-save', 'settings-email-save'].forEach((id) => {
        const button = document.getElementById(id);
        if (button) button.disabled = false;
    });
    const deletePendingButton = document.getElementById('settings-pending-words-delete-selected');
    if (deletePendingButton) {
        deletePendingButton.disabled = true;
        deletePendingButton.textContent = '删除选中';
    }
}

function _batchRenderList(container, items, renderFn) {
    cancelBatchRenderList(container);
    let rendered = 0;
    let observer = null;
    let sentinel = null;
    let cancelled = false;
    const cleanup = () => {
        cancelled = true;
        observer?.disconnect();
        sentinel?.remove();
        _listRenderCleanup.delete(container);
    };
    _listRenderCleanup.set(container, cleanup);
    function renderBatch() {
        if (cancelled) return;
        const slice = items.slice(rendered, rendered + _LIST_BATCH_SIZE);
        if (!slice.length) return;
        const html = slice.map(renderFn).join('');
        if (sentinel) sentinel.insertAdjacentHTML('beforebegin', html);
        else container.insertAdjacentHTML('beforeend', html);
        rendered += slice.length;
        if (rendered >= items.length) cleanup();
    }
    renderBatch();
    if (rendered < items.length) {
        sentinel = document.createElement('button');
        sentinel.type = 'button';
        sentinel.className = 'btn btn-secondary';
        sentinel.textContent = '加载更多';
        sentinel.addEventListener('click', renderBatch);
        container.appendChild(sentinel);
        if (typeof IntersectionObserver !== 'undefined') {
            observer = new IntersectionObserver((entries) => {
                if (entries.some((entry) => entry.isIntersecting)) renderBatch();
            }, { rootMargin: '200px' });
            observer.observe(sentinel);
        }
    }
}

async function loadProgress() {
    const accountContext = captureAccountContext();
    const requestSequence = ++progressRequestSequence;
    const isCurrent = () => isAccountContextCurrent(accountContext) && requestSequence === progressRequestSequence;
    const wordList = document.getElementById('word-list');
    const statsEl = document.getElementById('progress-stats');
    cancelBatchRenderList(wordList);
    if (wordList) wordList.innerHTML = '<p class="section-loading-hint">加载中…</p>';
    try {
        const data = await apiRequest('/words/status');
        if (!isCurrent()) return;

        renderLearningMap(data.stats.mastered_words);

        const statsHtml = `
            <div class="stat-card">
                <div class="stat-icon">📝</div>
                <div class="stat-content">
                    <div class="stat-label">总单词数</div>
                    <div class="stat-value">${data.stats.total_words}</div>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">✅</div>
                <div class="stat-content">
                    <div class="stat-label">已掌握</div>
                    <div class="stat-value">${data.stats.mastered_words}</div>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">📊</div>
                <div class="stat-content">
                    <div class="stat-label">平均掌握度</div>
                    <div class="stat-value">${Math.round(Number(data.stats.avg_mastery_percent) || 0)}%</div>
                </div>
            </div>
        `;
        if (statsEl) statsEl.innerHTML = statsHtml;

        if (wordList) {
            wordList.innerHTML = '';
            if (data.words.length === 0) {
                wordList.innerHTML = '<p style="padding: 20px; text-align: center; color: #999;">暂无单词</p>';
            } else {
                _batchRenderList(wordList, data.words, _renderProgressWordItem);
            }
        }
        _markSectionLoaded('progress');
    } catch (error) {
        if (!isCurrent()) return;
        if (wordList) {
            wordList.innerHTML = '<p class="section-loading-hint" role="status">加载进度失败，请重试</p><button type="button" class="btn btn-secondary">重试</button>';
            wordList.querySelector('button')?.addEventListener('click', () => void loadProgress());
        }
        showMainBanner('加载进度失败，请稍后重试');
    }
}

// ==================== 已掌握功能 ====================

function _renderMasteredWordItem(word) {
    const phoneticHtml = word.phonetic ? `<span class="word-item-phonetic">${escapeHtml(word.phonetic)}</span>` : '';
    const masteryPercent = Number(word.mastery?.overall_percent);
    const masteryDetail = `${masteryDimensionText(word, 'recognition', '识义')} · ${masteryDimensionText(word, 'context', '语境')} · ${masteryDimensionText(word, 'spelling', '拼写')} · ${masteryDimensionText(word, 'listening', '听写')}`;
    const masteryHtml = Number.isFinite(masteryPercent)
        ? `<div class="word-stat">
                <div class="word-stat-value">${Math.round(masteryPercent)}%</div>
                <div class="word-stat-label">多维掌握</div>
                <div class="word-stat-detail">${escapeHtml(masteryDetail)}</div>
            </div>`
        : '';
    const memoryStatusHtml = word.memory_status === 'reinforcement'
        ? '<span class="word-item-carryover-tag">待巩固</span>'
        : '<span class="word-item-stable-tag">稳定</span>';
    return `<div class="word-item">
        <div class="word-item-info">
            <div class="word-item-english">${escapeHtml(word.english)}${phoneticHtml}${memoryStatusHtml}</div>
            <div class="word-item-chinese">${escapeHtml(word.chinese)}</div>
        </div>
        <div class="word-item-stats">
            ${masteryHtml}
            <div class="word-stat">
                <div class="word-stat-value">${escapeHtml(word.review_count)}</div>
                <div class="word-stat-label">复习次数</div>
            </div>
        </div>
    </div>`;
}

async function loadMastered() {
    const accountContext = captureAccountContext();
    const requestSequence = ++masteredRequestSequence;
    const isCurrent = () => isAccountContextCurrent(accountContext) && requestSequence === masteredRequestSequence;
    const container = document.getElementById('mastered-list');
    cancelBatchRenderList(container);
    if (container) container.innerHTML = '<p class="section-loading-hint">加载中…</p>';
    try {
        const data = await apiRequest('/words/mastered');
        if (!isCurrent()) return;

        if (container) {
            container.innerHTML = '';
            if (data.words.length === 0) {
                container.innerHTML = '<p style="padding: 20px; text-align: center; color: #999;">暂无已掌握单词</p>';
            } else {
                _batchRenderList(container, data.words, _renderMasteredWordItem);
            }
        }
        _markSectionLoaded('mastered');
    } catch (error) {
        if (!isCurrent()) return;
        if (container) {
            container.innerHTML = '<p class="section-loading-hint" role="status">加载已掌握列表失败，请重试</p><button type="button" class="btn btn-secondary">重试</button>';
            container.querySelector('button')?.addEventListener('click', () => void loadMastered());
        }
        showMainBanner('加载已掌握列表失败，请稍后重试');
    }
}

// ==================== 单词学习（Discovery Card） ====================

let discoveryDeck = [];
let discoveryIndex = 0;
/** 用于「加入复习」：当前用户待复习 / 已掌握词条（小写英文键），与 loadDiscovery 同步刷新 */
let discoveryPendingKeys = new Set();
let discoveryMasteredKeys = new Set();
let discoveryImportBusy = false;
/** 「今日单词」模式：牌组仅为今日复习列表 */
let discoveryModeToday = false;
/** 搜索框有内容且使用全词库 /wordbank/csv/search 结果作为牌组 */
let discoverySearchMode = false;
let discoverySearchDebounceTimer = null;
let discoverySearchRequestSeq = 0;
let discoverySearchAbort = null;

/** 单词学习搜索：本地前缀/子串匹配（全量 CSV），上限避免一次渲染过多 */
const DISCOVERY_SEARCH_MAX_DECK = 280;
const DISCOVERY_SUGGESTIONS_MAX = 18;

function setDiscoverySearchListboxOpen(open) {
    const input = document.getElementById('discovery-search');
    if (input) input.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function hideDiscoverySearchSuggestions() {
    const box = document.getElementById('discovery-search-suggestions');
    if (!box) return;
    box.hidden = true;
    box.innerHTML = '';
    setDiscoverySearchListboxOpen(false);
}

function cancelDiscoverySearchRequest() {
    discoverySearchRequestSeq++;
    if (!discoverySearchAbort) return;
    try {
        discoverySearchAbort.abort();
    } catch (_) {
        /* ignore */
    }
    discoverySearchAbort = null;
}

function renderDiscoverySearchSuggestions(rows) {
    const box = document.getElementById('discovery-search-suggestions');
    if (!box) return;
    const slice = rows.slice(0, DISCOVERY_SUGGESTIONS_MAX);
    if (!slice.length) {
        box.hidden = true;
        box.innerHTML = '';
        setDiscoverySearchListboxOpen(false);
        return;
    }
    box.hidden = false;
    setDiscoverySearchListboxOpen(true);
    box.innerHTML = slice
        .map((row) => {
            const en = String(row.english || '').trim();
            const zh = String(row.chinese || '').trim();
            const enc = encodeURIComponent(en);
            const zhLine = zh ? `<span class="discovery-suggest-zh">${escapeHtml(zh)}</span>` : '';
            return `<button type="button" class="discovery-suggest-item" role="option" data-english="${enc}">
      <span class="discovery-suggest-en">${escapeHtml(en)}</span>${zhLine}
    </button>`;
        })
        .join('');
}

/**
 * 从全量词库行中按英文前缀 / 子串 / 中文包含匹配（支持 appl → apple）。
 */
function discoveryMatchWordbankRows(q, rows) {
    const raw = String(q || '').trim();
    if (!raw) return [];
    const t = raw.toLowerCase();
    const hasLatin = /[a-z]/i.test(raw);
    const scored = [];
    for (const row of rows) {
        const en = String(row.english || '').trim();
        if (!en) continue;
        const el = en.toLowerCase();
        const zh = String(row.chinese || '');
        let score = 0;
        if (hasLatin) {
            if (el.startsWith(t)) {
                score = 2000 - Math.min(el.length, 80);
            } else if (el.includes(t)) {
                score = 1000;
            } else if (zh.includes(raw)) {
                score = 400;
            }
        } else if (zh.includes(raw)) {
            score = 1500;
        }
        if (score > 0) scored.push({ row, score });
    }
    scored.sort((a, b) => {
        if (b.score !== a.score) return b.score - a.score;
        return String(a.row.english).localeCompare(String(b.row.english), 'en');
    });
    return scored.map((x) => x.row);
}

async function getDiscoveryWordbankRows() {
    const data = await fetchWordbankCsvForDiscovery('');
    return Array.isArray(data.words) ? data.words : [];
}

function initDiscoverySearchSuggestUI() {
    const wrap = document.querySelector('.discovery-search-wrap');
    const input = document.getElementById('discovery-search');
    const sug = document.getElementById('discovery-search-suggestions');
    if (!wrap || !input || !sug) return;
    sug.addEventListener('mousedown', (e) => {
        if (e.target.closest('.discovery-suggest-item')) e.preventDefault();
    });
    sug.addEventListener('click', (e) => {
        const btn = e.target.closest('.discovery-suggest-item');
        if (!btn || !sug.contains(btn)) return;
        let en = '';
        try {
            en = decodeURIComponent(btn.getAttribute('data-english') || '');
        } catch (_) {
            return;
        }
        if (!en) return;
        const idx = discoveryDeck.findIndex(
            (w) => String(w.english).toLowerCase() === en.toLowerCase(),
        );
        if (idx >= 0) {
            discoveryIndex = idx;
            renderDiscoveryCard();
        }
        input.value = en;
        hideDiscoverySearchSuggestions();
    });
    input.addEventListener('focus', () => {
        const v = input.value.trim();
        if (v) void loadDiscovery();
    });
    input.addEventListener('blur', () => {
        setTimeout(() => hideDiscoverySearchSuggestions(), 200);
    });
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') hideDiscoverySearchSuggestions();
    });
    document.addEventListener('click', (e) => {
        if (wrap.contains(e.target)) return;
        hideDiscoverySearchSuggestions();
    });
}

const DISCOVERY_LEVEL_STORAGE_KEY = 'english_reciter_discovery_level';

/** 选择具体难度时，待复习词也须属于该难度（与系统词库 level 一致） */
const DISCOVERY_BANK_LEVELS = new Set(['小学', '初中', '高中', 'GRE']);

function discoveryExamplesFromCsvRow(row) {
    const out = [];
    for (const k of ['1', '2', '3', '4', '5', '6', '7', '8']) {
        const en = String(row[`example${k}`] || '').trim();
        const cn = String(row[`example${k}_cn`] || '').trim();
        if (en || cn) out.push({ en, cn });
    }
    return out;
}

function buildPendingDiscoveryExamples(w) {
    if (Array.isArray(w.examples) && w.examples.length) {
        return w.examples
            .map((e) => ({
                en: String(e.en || '').trim(),
                cn: String(e.cn || '').trim(),
            }))
            .filter((e) => e.en || e.cn);
    }
    const single = String(w.example || '').trim();
    if (!single) return [];
    const i = single.indexOf('_');
    if (i === -1) return [{ en: single, cn: '' }];
    return [{ en: single.slice(0, i).trim(), cn: single.slice(i + 1).trim() }];
}

function getDiscoveryExamplesForCard(w) {
    if (Array.isArray(w.examples) && w.examples.length) {
        return w.examples
            .map((e) => ({
                en: String(e.en || '').trim(),
                cn: String(e.cn || '').trim(),
            }))
            .filter((e) => e.en || e.cn);
    }
    const single = String(w.example || '').trim();
    if (!single) return [];
    const i = single.indexOf('_');
    if (i === -1) return [{ en: single, cn: '' }];
    return [{ en: single.slice(0, i).trim(), cn: single.slice(i + 1).trim() }];
}

function discoveryEnglishKey(en) {
    return String(en || '')
        .trim()
        .toLowerCase();
}

function buildDiscoveryImportPayload(w) {
    const exList = getDiscoveryExamplesForCard(w);
    let example = '';
    if (exList.length) {
        const e0 = exList[0];
        if (e0.en && e0.cn) example = `${e0.en}_${e0.cn}`;
        else if (e0.en) example = e0.en;
    }
    const english = String(w.english || '').trim();
    const chinese = String(w.chinese || '').trim();
    const item = { english, chinese };
    if (example) item.example = example;
    return item;
}

function getDiscoveryImportButtonState(w) {
    const k = discoveryEnglishKey(w.english);
    const src = w.source;
    if (src === 'pending' || src === 'today') {
        return {
            variant: 'in_queue',
            label: '待复习中',
            banner: '该词已在待复习列表中，无需重复导入。',
        };
    }
    if (discoveryMasteredKeys.has(k)) {
        return {
            variant: 'mastered',
            label: '已掌握',
            banner: '该词已在「已掌握」列表中。',
        };
    }
    if (discoveryPendingKeys.has(k)) {
        return {
            variant: 'in_queue',
            label: '待复习中',
            banner: '该词已在待复习列表中，无需重复导入。',
        };
    }
    return {
        variant: 'add',
        label: '加入复习',
        banner: '',
    };
}

async function handleDiscoveryImportClick(e) {
    const accountContext = captureAccountContext();
    const btn = e.currentTarget;
    const w = discoveryDeck[discoveryIndex];
    if (!w || discoveryImportBusy) return;
    const st = getDiscoveryImportButtonState(w);
    if (st.variant !== 'add') {
        showMainBanner(st.banner);
        return;
    }
    const zh = String(w.chinese || '').trim();
    if (!zh) {
        showMainBanner('该词条缺少中文释义，无法加入待复习。');
        return;
    }
    const k = discoveryEnglishKey(w.english);
    discoveryImportBusy = true;
    const prevLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = '导入中…';
    try {
        const payload = buildDiscoveryImportPayload(w);
        const data = await apiRequest('/words/import-json', {
            method: 'POST',
            body: JSON.stringify([payload]),
        });
        if (!isAccountContextCurrent(accountContext)) return;
        const added = data.added || 0;
        const skipped = data.skipped_duplicate || 0;
        if (added > 0) {
            discoveryPendingKeys.add(k);
            invalidateReviewDataAfterImport();
            const todayHint = Number(data.added_to_today) > 0 ? '并加入今日任务' : '待复习';
            showMainBanner(`「${w.english}」已加入${todayHint}`);
            loadStats();
            renderDiscoveryCard();
            return;
        }
        if (skipped > 0) {
            try {
                const [st, masteredRes] = await Promise.all([
                    apiRequest('/words/status'),
                    apiRequest('/words/mastered'),
                ]);
                if (!isAccountContextCurrent(accountContext)) return;
                discoveryPendingKeys = new Set((st.words || []).map((x) => discoveryEnglishKey(x.english)));
                discoveryMasteredKeys = new Set((masteredRes.words || []).map((x) => discoveryEnglishKey(x.english)));
            } catch (_) {
                /* 保持本地缓存 */
            }
            if (!isAccountContextCurrent(accountContext)) return;
            showMainBanner('该词已在待复习列表或已掌握中，无需重复导入。');
            loadStats();
            renderDiscoveryCard();
            return;
        }
        showMainBanner(data.message || '未能加入待复习');
    } catch (err) {
        if (!isAccountContextCurrent(accountContext)) return;
        showMainBanner(err.message || '导入失败');
    } finally {
        if (isAccountContextCurrent(accountContext)) {
            discoveryImportBusy = false;
            if (btn.isConnected) {
                btn.disabled = false;
                btn.textContent = prevLabel;
            }
        }
    }
}

function getDiscoverySelectedLevel() {
    const active = document.querySelector('.discovery-level-btn.is-active');
    if (!active) return '';
    return active.getAttribute('data-discovery-level') ?? '';
}

function initDiscoveryLevelButtons() {
    const group = document.querySelector('.discovery-level-buttons');
    if (!group) return;
    const buttons = group.querySelectorAll('.discovery-level-btn');
    if (!buttons.length) return;
    let saved = '';
    try {
        saved = localStorage.getItem(DISCOVERY_LEVEL_STORAGE_KEY);
        if (saved === null) saved = '';
    } catch (_) {
        saved = '';
    }
    buttons.forEach((btn) => {
        const v = btn.getAttribute('data-discovery-level') ?? '';
        if (v === saved) btn.classList.add('is-active');
    });
    if (!group.querySelector('.discovery-level-btn.is-active')) {
        buttons[0].classList.add('is-active');
    }
    buttons.forEach((btn) => {
        btn.setAttribute('aria-pressed', btn.classList.contains('is-active') ? 'true' : 'false');
    });
    group.addEventListener('click', (e) => {
        const t = e.target.closest('.discovery-level-btn');
        if (!t || !group.contains(t)) return;
        buttons.forEach((b) => {
            b.classList.remove('is-active');
            b.setAttribute('aria-pressed', 'false');
        });
        t.classList.add('is-active');
        t.setAttribute('aria-pressed', 'true');
        try {
            localStorage.setItem(DISCOVERY_LEVEL_STORAGE_KEY, t.getAttribute('data-discovery-level') ?? '');
        } catch (_) {
            /* ignore */
        }
        const ds = document.getElementById('discovery-search');
        if (ds) ds.value = '';
        discoverySearchMode = false;
        discoveryIndex = 0;
        loadDiscovery();
    });
}

function discoverySortPending(a, b) {
    const da = new Date(a.next_review_date).getTime();
    const db = new Date(b.next_review_date).getTime();
    if (da !== db) return da - db;
    return String(a.english).localeCompare(String(b.english), 'en');
}

async function loadDiscoverySearchFromQuery(q) {
    const accountContext = captureAccountContext();
    const emptyEl = document.getElementById('discovery-empty');
    const rootEl = document.getElementById('discovery-root');
    const sug = document.getElementById('discovery-search-suggestions');
    const trimmed = String(q || '').trim();
    const seq = ++discoverySearchRequestSeq;
    if (discoverySearchAbort) {
        try {
            discoverySearchAbort.abort();
        } catch (_) {
            /* ignore */
        }
    }
    const controller = new AbortController();
    discoverySearchAbort = controller;
    discoverySearchMode = true;
    discoveryModeToday = false;
    if (sug && !discoveryWordbankFullCsvCached()) {
        sug.hidden = false;
        sug.innerHTML = '<p class="discovery-suggest-hint">加载词库…</p>';
        setDiscoverySearchListboxOpen(true);
    }
    try {
        const [allRows, st, masteredRes] = await Promise.all([
            getDiscoveryWordbankRows(),
            apiRequest('/words/status', { signal: controller.signal }),
            apiRequest('/words/mastered', { signal: controller.signal }),
        ]);
        if (!isAccountContextCurrent(accountContext) || seq !== discoverySearchRequestSeq) return;
        discoveryPendingKeys = new Set((st.words || []).map((x) => discoveryEnglishKey(x.english)));
        discoveryMasteredKeys = new Set((masteredRes.words || []).map((x) => discoveryEnglishKey(x.english)));
        if (seq !== discoverySearchRequestSeq) return;
        const matched = discoveryMatchWordbankRows(trimmed, allRows);
        if (seq !== discoverySearchRequestSeq) return;
        const capped = matched.slice(0, DISCOVERY_SEARCH_MAX_DECK);
        discoveryDeck = [];
        for (const row of capped) {
            const en = String(row.english || '').trim();
            if (!en) continue;
            discoveryDeck.push({
                english: en,
                chinese: String(row.chinese || '').trim(),
                phonetic: String(row.phonetic || '').trim(),
                examples: discoveryExamplesFromCsvRow(row),
                source: 'wordbank',
                chinese_sense_lines: Array.isArray(row.chinese_sense_lines) ? row.chinese_sense_lines : undefined,
            });
        }
        discoveryIndex = 0;
        if (seq !== discoverySearchRequestSeq) return;
        renderDiscoverySearchSuggestions(matched);
        if (discoveryDeck.length === 0) {
            if (emptyEl) {
                emptyEl.style.display = 'block';
                emptyEl.innerHTML =
                    '<p>未在词库中找到匹配词条，请尝试其它关键词或清空搜索框。</p>';
            }
            if (rootEl) rootEl.style.display = 'none';
            return;
        }
        if (seq !== discoverySearchRequestSeq) return;
        if (emptyEl) emptyEl.style.display = 'none';
        if (rootEl) rootEl.style.display = 'block';
        renderDiscoveryCard();
    } catch (error) {
        if (!isAccountContextCurrent(accountContext)) return;
        if (seq !== discoverySearchRequestSeq) return;
        if (isAbortError(error)) return;
        discoveryPendingKeys = new Set();
        discoveryMasteredKeys = new Set();
        discoverySearchMode = false;
        discoveryDeck = [];
        discoveryIndex = 0;
        hideDiscoverySearchSuggestions();
        const wrap = document.getElementById('discovery-card-wrap');
        if (wrap) wrap.innerHTML = '';
        if (emptyEl) {
            emptyEl.style.display = 'block';
            emptyEl.innerHTML = '<p>搜索失败，请稍后重试。</p>';
        }
        if (rootEl) rootEl.style.display = 'none';
        showMainBanner('搜索失败，请稍后重试');
    } finally {
        if (discoverySearchAbort === controller) {
            discoverySearchAbort = null;
        }
    }
}

async function loadDiscovery() {
    const accountContext = captureAccountContext();
    const emptyEl = document.getElementById('discovery-empty');
    const rootEl = document.getElementById('discovery-root');
    const searchQ = (document.getElementById('discovery-search')?.value || '').trim();
    if (searchQ) {
        await loadDiscoverySearchFromQuery(searchQ);
        return;
    }
    cancelDiscoverySearchRequest();
    const requestSequence = discoverySearchRequestSeq;
    const isCurrent = () => isAccountContextCurrent(accountContext) && requestSequence === discoverySearchRequestSeq;
    hideDiscoverySearchSuggestions();
    discoverySearchMode = false;
    const level = String(getDiscoverySelectedLevel() || '').trim();
    discoveryModeToday = false;
    try {
        if (level === 'today') {
            discoveryModeToday = true;
            const [rev, masteredRes] = await Promise.all([
                apiRequest('/words/review'),
                apiRequest('/words/mastered'),
            ]);
            if (!isCurrent()) return;
            discoveryMasteredKeys = new Set((masteredRes.words || []).map((x) => discoveryEnglishKey(x.english)));
            const list = rev.words || [];
            discoveryPendingKeys = new Set(list.map((x) => discoveryEnglishKey(x.english)));
            discoveryDeck = list.map((w) => ({
                english: w.english,
                chinese: w.chinese,
                phonetic: w.phonetic || '',
                examples: buildPendingDiscoveryExamples(w),
                source: 'today',
                next_review_date: w.scheduled_due_date,
                chinese_sense_lines: Array.isArray(w.chinese_sense_lines) ? w.chinese_sense_lines : undefined,
            }));
            if (discoveryIndex >= discoveryDeck.length) discoveryIndex = 0;

            if (discoveryDeck.length === 0) {
                discoveryPendingKeys = new Set();
                if (emptyEl) {
                    emptyEl.style.display = 'block';
                    emptyEl.innerHTML =
                        '<p>今日暂无需要复习的单词。可稍后再试，或切换到「全部」等其它词库。</p>';
                }
                if (rootEl) rootEl.style.display = 'none';
                return;
            }
            if (emptyEl) emptyEl.style.display = 'none';
            if (rootEl) rootEl.style.display = 'block';
            // 今日单词与复习页使用同源列表，默认顺序一致；进入时自动乱序一次，避免与复习顺序重合
            if (discoveryDeck.length >= 2) {
                discoveryShuffle();
            } else {
                renderDiscoveryCard();
            }
            return;
        }

        const [st, wb, masteredRes] = await Promise.all([
            apiRequest('/words/status'),
            fetchWordbankCsvForDiscovery(level),
            apiRequest('/words/mastered'),
        ]);
        if (!isCurrent()) return;
        discoveryPendingKeys = new Set((st.words || []).map((x) => discoveryEnglishKey(x.english)));
        discoveryMasteredKeys = new Set((masteredRes.words || []).map((x) => discoveryEnglishKey(x.english)));

        let pending = [];
        for (const w of st.words || []) {
            pending.push({
                english: w.english,
                chinese: w.chinese,
                phonetic: w.phonetic || '',
                level: String(w.level || '').trim(),
                examples: buildPendingDiscoveryExamples(w),
                source: 'pending',
                next_review_date: w.next_review_date,
                chinese_sense_lines: Array.isArray(w.chinese_sense_lines) ? w.chinese_sense_lines : undefined,
            });
        }
        if (DISCOVERY_BANK_LEVELS.has(level)) {
            pending = pending.filter((p) => p.level === level);
        }
        pending.sort(discoverySortPending);

        const pendingKeys = new Set(pending.map((x) => String(x.english).toLowerCase()));
        const wordbankPart = [];
        for (const row of wb.words || []) {
            const en = String(row.english || '').trim();
            if (!en) continue;
            const k = en.toLowerCase();
            if (pendingKeys.has(k)) continue;
            wordbankPart.push({
                english: en,
                chinese: String(row.chinese || '').trim(),
                phonetic: String(row.phonetic || '').trim(),
                examples: discoveryExamplesFromCsvRow(row),
                source: 'wordbank',
                chinese_sense_lines: Array.isArray(row.chinese_sense_lines) ? row.chinese_sense_lines : undefined,
            });
        }
        wordbankPart.sort((a, b) => a.english.localeCompare(b.english, 'en'));

        discoveryDeck = pending.concat(wordbankPart);
        if (discoveryIndex >= discoveryDeck.length) discoveryIndex = 0;

        if (discoveryDeck.length === 0) {
            if (emptyEl) {
                emptyEl.style.display = 'block';
                emptyEl.innerHTML =
                    '<p>暂无可用词条。请先到「导入单词」添加待复习内容，或调整难度后重试。</p>';
            }
            if (rootEl) rootEl.style.display = 'none';
            return;
        }
        if (emptyEl) emptyEl.style.display = 'none';
        if (rootEl) rootEl.style.display = 'block';
        renderDiscoveryCard();
    } catch (_) {
        if (!isCurrent()) return;
        discoveryPendingKeys = new Set();
        discoveryMasteredKeys = new Set();
        if (emptyEl) {
            emptyEl.style.display = 'block';
            emptyEl.innerHTML = '<p>加载失败，请稍后重试。</p>';
        }
        if (rootEl) rootEl.style.display = 'none';
        showMainBanner('加载单词学习失败，请稍后重试');
    }
}

function renderDiscoveryCard() {
    const wrap = document.getElementById('discovery-card-wrap');
    const counter = document.getElementById('discovery-counter');
    if (!wrap) return;
    if (discoveryDeck.length === 0) {
        wrap.innerHTML = '';
        if (counter) counter.textContent = '';
        return;
    }
    if (discoveryIndex >= discoveryDeck.length) discoveryIndex = 0;
    if (discoveryIndex < 0) discoveryIndex = discoveryDeck.length - 1;
    const w = discoveryDeck[discoveryIndex];
    const phon = (w.phonetic || '').trim();
    const phonBlock =
        phon.length > 0
            ? `<p class="discovery-card-phonetic word-item-phonetic">${escapeHtml(phon)}</p>`
            : '<p class="discovery-card-phonetic discovery-card-phonetic--empty">音标暂无</p>';
    const lines = w.chinese_sense_lines;
    const polysemeStack =
        Array.isArray(lines) && lines.length > 1 ? discoveryPolysemeSenseExampleHtml(w) : '';
    const senseBlock = polysemeStack ? '' : discoveryChineseSenseHtml(w);
    const examples = getDiscoveryExamplesForCard(w);
    const exampleBlock = polysemeStack ? '' : discoveryExampleSlotsHtml(examples);
    const importState = getDiscoveryImportButtonState(w);
    const importBtnClass =
        importState.variant === 'add'
            ? 'discovery-import-btn discovery-import-btn--add'
            : 'discovery-import-btn discovery-import-btn--muted';
    const importAria =
        importState.variant === 'add'
            ? `将 ${escapeHtml(w.english)} 加入待复习`
            : importState.label === '已掌握'
              ? `${escapeHtml(w.english)} 已掌握`
              : `${escapeHtml(w.english)} 已在待复习中`;
    const html = `
      <article class="discovery-card" aria-label="单词卡片 ${escapeHtml(w.english)}">
        <div class="discovery-card-body">
          <div class="discovery-card-word-row">
            <h3 class="discovery-card-word">${escapeHtml(w.english)}</h3>
            <div class="discovery-card-word-actions">
              <button type="button" class="btn-speak discovery-speak-word" title="朗读单词" aria-label="朗读 ${escapeHtml(w.english)}">🔊</button>
              <button type="button" class="${importBtnClass}" title="${escapeHtml(importState.label)}" aria-label="${importAria}">${escapeHtml(
        importState.label,
    )}</button>
            </div>
          </div>
          ${phonBlock}
          ${polysemeStack || `${senseBlock}${exampleBlock}`}
        </div>
      </article>
    `;
    wrap.innerHTML = html;
    if (counter) {
        const n = discoveryDeck.length;
        const i = discoveryIndex + 1;
        if (discoverySearchMode) {
            counter.textContent = `${i} / ${n}（全词库搜索）`;
        } else if (discoveryModeToday && w.source === 'today') {
            counter.textContent = `${i} / ${n}（今日复习）`;
        } else {
            counter.textContent = `${i} / ${n}`;
        }
    }

    const speakBtn = wrap.querySelector('.discovery-speak-word');
    if (speakBtn) {
        speakBtn.addEventListener('click', (e) => {
            void speakEnglishPreferred(w.english, () => {}, e.currentTarget);
        });
    }
    const importBtn = wrap.querySelector('.discovery-import-btn');
    if (importBtn) {
        importBtn.addEventListener('click', (e) => {
            void handleDiscoveryImportClick(e);
        });
    }
    wrap.querySelectorAll('.discovery-speak-example').forEach((btn) => {
        const si = btn.getAttribute('data-example-slot');
        const seg = si != null ? examples[parseInt(si, 10)] : null;
        if (!seg || !seg.en) return;
        btn.addEventListener('click', () => {
            void speakEnglishPreferred(seg.en, () => {}, btn);
        });
    });
}

/** 单义或合并释义一行；多义（多条 chinese_sense_lines）由 discoveryPolysemeSenseExampleHtml 展示 */
function discoveryChineseSenseHtml(w) {
    const ch = String(w.chinese || '').trim();
    if (!ch) return '';
    return `<p class="discovery-card-chinese">${escapeHtml(ch)}</p>`;
}

/**
 * 例句槽位：至少 2 格避免高度跳动；多义 v2 可有最多 8 条例句。
 */
function discoveryExampleSlotsHtml(examples) {
    const filled = Array.isArray(examples)
        ? examples
              .map((e) => ({
                  en: String(e.en || '').trim(),
                  cn: String(e.cn || '').trim(),
              }))
              .filter((e) => e.en || e.cn)
        : [];
    const slotCount = Math.min(8, Math.max(filled.length, 2));
    const slots = [];
    for (let idx = 0; idx < slotCount; idx++) {
        const ex = filled[idx];
        if (ex && (ex.en || ex.cn)) {
            const speakable = Boolean(ex.en);
            const enLine = ex.en
                ? `<p class="discovery-example-line discovery-example-line--en">${escapeHtml(ex.en)}</p>`
                : '';
            const cnLine = ex.cn
                ? `<p class="discovery-example-line discovery-example-line--cn">${escapeHtml(ex.cn)}</p>`
                : '';
            slots.push(`<div class="discovery-example-block">
      <div class="discovery-example-block-body">
        ${enLine}
        ${cnLine}
      </div>
      <button type="button" class="btn-speak discovery-speak-example" data-example-slot="${idx}" title="朗读例句" aria-label="朗读例句 ${
          idx + 1
      }" ${speakable ? '' : 'disabled'}>🔊</button>
    </div>`);
        } else {
            slots.push(
                '<div class="discovery-example-block discovery-example-block--placeholder" aria-hidden="true"></div>',
            );
        }
    }
    return `<div class="discovery-examples">${slots.join('')}</div>`;
}

/** 多义词条：每个义项与对应例句、朗读同一竖向块内展示（与 example 槽位下标一致） */
function discoveryPolysemeSenseExampleHtml(w) {
    const lines = w.chinese_sense_lines;
    if (!Array.isArray(lines) || lines.length <= 1) return '';
    const examples = getDiscoveryExamplesForCard(w);
    const chunks = lines.map((raw, idx) => {
        const defLine = `${idx + 1}. ${String(raw).trim()}`;
        const ex = examples[idx] || { en: '', cn: '' };
        const en = String(ex.en || '').trim();
        const cn = String(ex.cn || '').trim();
        const enLine = en
            ? `<p class="discovery-example-line discovery-example-line--en">${escapeHtml(en)}</p>`
            : '';
        const cnLine = cn
            ? `<p class="discovery-example-line discovery-example-line--cn">${escapeHtml(cn)}</p>`
            : '';
        const speakable = Boolean(en);
        const emptyHint =
            !en && !cn
                ? '<p class="discovery-sense-example-missing">暂无该义项例句</p>'
                : '';
        return `<div class="discovery-sense-example-block">
      <p class="discovery-sense-example-def">${escapeHtml(defLine)}</p>
      <div class="discovery-example-block">
        <div class="discovery-example-block-body">
          ${enLine}
          ${cnLine}
          ${emptyHint}
        </div>
        <button type="button" class="btn-speak discovery-speak-example" data-example-slot="${idx}" title="朗读例句" aria-label="朗读义项 ${idx + 1} 例句" ${
            speakable ? '' : 'disabled'
        }>🔊</button>
      </div>
    </div>`;
    });
    return `<div class="discovery-polyseme-stack">${chunks.join('')}</div>`;
}

function discoveryGo(delta) {
    if (discoveryDeck.length === 0) return;
    discoveryIndex = (discoveryIndex + delta + discoveryDeck.length) % discoveryDeck.length;
    renderDiscoveryCard();
}

function discoveryShuffle() {
    if (discoveryDeck.length < 2) return;
    for (let i = discoveryDeck.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [discoveryDeck[i], discoveryDeck[j]] = [discoveryDeck[j], discoveryDeck[i]];
    }
    discoveryIndex = 0;
    renderDiscoveryCard();
}

// ==================== 系统词库（全局搜索，无难度tab） ====================

/**
 * wbState.selected: Set of "english_lower" keys
 * wbState.selectedMap: Map of "english_lower" -> word object
 */
const wbState = {
    filtered: [],
    filter: '',
    selected: new Set(),
    selectedMap: new Map(),
    searchTimer: null,
    loading: false,
    searchAbort: null,
    searchSeq: 0,
    importBusy: false,
};

function wordbankKey(en) {
    return String(en || '').trim().toLowerCase();
}

function updateWordbankSelectedCount() {
    const el = document.getElementById('wordbank-selected-count');
    if (el) el.textContent = `已选 ${wbState.selected.size} 词`;
}

function renderWordbankMeta() {
    const el = document.getElementById('wordbank-meta');
    if (!el) return;
    const q = (wbState.filter || '').trim();
    if (wbState.loading) {
        el.textContent = '搜索中…';
        return;
    }
    if (!q && wbState.filtered.length === 0) {
        el.textContent = '请输入搜索词';
        return;
    }
    el.textContent = `找到 ${wbState.filtered.length} 条匹配词条`;
}

function renderWordbankList() {
    const container = document.getElementById('wordbank-list');
    if (!container) return;
    const q = (wbState.filter || '').trim();

    if (!q && wbState.filtered.length === 0) {
        container.innerHTML = '<p class="wordbank-empty wordbank-hint-text">请在上方搜索框输入单词进行搜索</p>';
        return;
    }

    if (wbState.loading) {
        container.innerHTML = '<p class="wordbank-empty">搜索中…</p>';
        return;
    }

    const LEVEL_COLORS = { '小学': 'primary', '初中': 'junior', '高中': 'senior', 'GRE': 'gre' };

    // 先渲染已选中但当前搜索结果中不显示的词（保持勾选状态可见）
    const filteredKeys = new Set(wbState.filtered.map(w => wordbankKey(w.english)));
    const extraSelected = [];
    for (const [k, w] of wbState.selectedMap.entries()) {
        if (!filteredKeys.has(k)) {
            extraSelected.push(w);
        }
    }

    const allToShow = [...wbState.filtered, ...extraSelected];

    const html = allToShow
        .map((w) => {
            const k = wordbankKey(w.english);
            const checked = wbState.selected.has(k) ? 'checked' : '';
            const levelCls = LEVEL_COLORS[w.level] ? `wb-level-${LEVEL_COLORS[w.level]}` : 'wb-level-other';
            const levelTag = w.level ? `<span class="wb-bank-tag ${levelCls}">${escapeHtml(w.level)}</span>` : '';
            const ex1 = (w.example1 || w.example || '').split('_')[0].trim();
            const exTag = ex1 ? `<span class="wb-ex">${escapeHtml(ex1.slice(0, 60))}…</span>` : '';
            return (
                `<div class="wordbank-row" role="listitem">` +
                `<label>` +
                `<input type="checkbox" class="wordbank-cb" data-k="${escapeHtml(k)}" ${checked} />` +
                `${levelTag}` +
                `<span class="wb-en">${escapeHtml(w.english)}</span>` +
                `<span class="wb-zh">${escapeHtml(w.chinese)}</span>` +
                `${exTag}` +
                `</label></div>`
            );
        })
        .join('');
    container.innerHTML = html || '<p class="wordbank-empty">无匹配词条</p>';
    container.querySelectorAll('.wordbank-cb').forEach((cb) => {
        cb.addEventListener('change', () => {
            const k = cb.dataset.k;
            if (!k) return;
            if (cb.checked) {
                wbState.selected.add(k);
                // 找到对应 word 对象存入 map
                const w = allToShow.find(x => wordbankKey(x.english) === k);
                if (w) wbState.selectedMap.set(k, w);
            } else {
                wbState.selected.delete(k);
                wbState.selectedMap.delete(k);
            }
            updateWordbankSelectedCount();
        });
    });
}

async function doWordbankSearch(q) {
    const seq = ++wbState.searchSeq;
    if (wbState.searchAbort) {
        try {
            wbState.searchAbort.abort();
        } catch (_) {
            /* ignore */
        }
        wbState.searchAbort = null;
    }
    if (!q) {
        wbState.filtered = [];
        wbState.loading = false;
        renderWordbankMeta();
        renderWordbankList();
        return;
    }
    const controller = new AbortController();
    wbState.searchAbort = controller;
    wbState.loading = true;
    renderWordbankMeta();
    try {
        const params = new URLSearchParams({ q, heuristics: '0', surface_first: '1' });
        const data = await apiRequest(`/wordbank/csv/search?${params}`, { signal: controller.signal });
        if (seq !== wbState.searchSeq) return;
        wbState.filtered = Array.isArray(data.words) ? data.words : [];
    } catch (e) {
        if (seq !== wbState.searchSeq || isAbortError(e)) return;
        wbState.filtered = [];
        showImportNotice(e.message || '搜索失败', { isError: true });
    } finally {
        if (wbState.searchAbort === controller) {
            wbState.searchAbort = null;
        }
        if (seq === wbState.searchSeq) {
            wbState.loading = false;
        }
    }
    if (seq === wbState.searchSeq) {
        renderWordbankMeta();
        renderWordbankList();
    }
}

function initWordbankPanel() {
    const search = document.getElementById('wordbank-search');
    if (search) {
        search.addEventListener('input', () => {
            wbState.filter = search.value;
            // 用户开始输入，重置文章导入的 placeholder
            search.placeholder = '搜索单词，例如：apple 或 apple, banana, curious';
            if (wbState.searchTimer) clearTimeout(wbState.searchTimer);
            wbState.searchTimer = setTimeout(() => {
                doWordbankSearch((wbState.filter || '').trim());
            }, 350);
        });
    }

    document.getElementById('discovery-prev')?.addEventListener('click', () => discoveryGo(-1));
    document.getElementById('discovery-next')?.addEventListener('click', () => discoveryGo(1));
    document.getElementById('discovery-shuffle')?.addEventListener('click', () => discoveryShuffle());

    initDiscoveryLevelButtons();
    const discoverySearch = document.getElementById('discovery-search');
    if (discoverySearch) {
        discoverySearch.addEventListener('input', () => {
            if (discoverySearchDebounceTimer) clearTimeout(discoverySearchDebounceTimer);
            discoverySearchDebounceTimer = setTimeout(() => {
                void loadDiscovery();
            }, 280);
        });
    }
    initDiscoverySearchSuggestUI();

    document.getElementById('wordbank-select-filtered')?.addEventListener('click', () => {
        for (const w of wbState.filtered) {
            const k = wordbankKey(w.english);
            wbState.selected.add(k);
            wbState.selectedMap.set(k, w);
        }
        updateWordbankSelectedCount();
        renderWordbankList();
    });

    document.getElementById('wordbank-clear')?.addEventListener('click', () => {
        wbState.selected.clear();
        wbState.selectedMap.clear();
        updateWordbankSelectedCount();
        renderWordbankList();
    });

    document.getElementById('wordbank-import-btn')?.addEventListener('click', wordbankImportSelected);
}

async function wordbankImportSelected() {
    const accountContext = captureAccountContext();
    if (wbState.importBusy) return;
    if (!wbState.selected.size) {
        showImportNotice('请先勾选单词', { isError: true });
        return;
    }
    const items = [];
    for (const [k, w] of wbState.selectedMap.entries()) {
        if (!w) continue;
        const ex = (w.example1 || w.example || '');
        const exCn = (w.example1_cn || '');
        const example = ex ? (exCn ? `${ex}_${exCn}` : ex) : '';
        items.push({
            english: w.english,
            chinese: w.chinese,
            example: example || undefined,
        });
    }
    // 如果 selectedMap 没有完整 word 对象（兼容旧逻辑），跳过
    if (!items.length) {
        showImportNotice('没有可导入的词条（请重新搜索并勾选）', { isError: true });
        return;
    }
    wbState.importBusy = true;
    const importBtn = document.getElementById('wordbank-import-btn');
    if (importBtn) {
        importBtn.disabled = true;
        importBtn.textContent = '导入中…';
    }
    const chunk = 500;
    let added = 0;
    let skipped = 0;
    let invalid = 0;
    const dupWords = [];
    try {
        for (let i = 0; i < items.length; i += chunk) {
            if (!isAccountContextCurrent(accountContext)) return;
            const part = items.slice(i, i + chunk);
            const data = await apiRequest('/words/import-json', {
                method: 'POST',
                body: JSON.stringify(part)
            });
            if (!isAccountContextCurrent(accountContext)) return;
            added += data.added || 0;
            skipped += data.skipped_duplicate || 0;
            invalid += data.skipped_invalid || 0;
            if (Array.isArray(data.skipped_duplicate_words)) {
                dupWords.push(...data.skipped_duplicate_words);
            }
        }
        if (added > 0) invalidateReviewDataAfterImport();
        const dupUnique = [...new Set(dupWords)];
        openImportResultModal({
            variant: 'stats',
            title: '导入结果',
            stats: {
                total: items.length,
                added,
                skipped,
                invalid,
                dupWords: dupUnique,
            },
            onClose: () => {
                if (!isAccountContextCurrent(accountContext)) return;
                wbState.selected.clear();
                wbState.selectedMap.clear();
                updateWordbankSelectedCount();
                renderWordbankList();
                loadStats();
                const b = document.getElementById('wordbank-import-btn');
                if (b) {
                    b.disabled = false;
                    b.textContent = '将选中的词加入待复习';
                }
                wbState.importBusy = false;
            },
        });
    } catch (error) {
        if (!isAccountContextCurrent(accountContext)) return;
        showImportNotice(error.message || '导入失败', { isError: true });
        wbState.importBusy = false;
        if (importBtn) {
            importBtn.disabled = false;
            importBtn.textContent = '将选中的词加入待复习';
        }
    } finally {
        if (isAccountContextCurrent(accountContext) && importBtn && !importResultModalPending) {
            importBtn.disabled = false;
            importBtn.textContent = '将选中的词加入待复习';
        }
        if (isAccountContextCurrent(accountContext) && !importResultModalPending) {
            wbState.importBusy = false;
        }
    }
}

// ==================== 导入功能 ====================

/** 与课文学习页一致：非 VIP 每册仅列出前 N 篇课文 */
const IMPORT_NCE_FREE_UNITS_PER_BOOK = 10;

let importNceCatalogLoaded = false;
let importNceCatalogPlan = null;

async function ensureImportNceLessonOptions() {
    const sel = document.getElementById('import-nc-lesson-select');
    if (!sel) return;
    if (importNceCatalogLoaded && importNceCatalogPlan === userPlan) return;

    sel.innerHTML = '<option value="">加载课文中…</option>';
    sel.disabled = true;
    try {
        const data = await apiRequest('/textbooks/catalog');
        const corpora = Array.isArray(data.corpora) ? data.corpora : [];
        const isVip = userPlan === 'paid';
        const frag = document.createDocumentFragment();
        const def = document.createElement('option');
        def.value = '';
        def.textContent = '— 新概念课文（可选）—';
        frag.appendChild(def);

        for (const c of corpora) {
            const manifest = c.manifest || {};
            const books = Array.isArray(manifest.books) ? manifest.books : [];
            for (const b of books) {
                const bookLabel = `${b.bookName || ''} ${b.bookLevel || ''}`.trim() || String(b.key || '');
                const units = Array.isArray(b.units) ? b.units : [];
                const unitsShown = isVip ? units : units.slice(0, IMPORT_NCE_FREE_UNITS_PER_BOOK);
                for (const u of unitsShown) {
                    const jp = String(u.json || '').trim();
                    if (!jp) continue;
                    const opt = document.createElement('option');
                    opt.value = `${c.id}\t${jp}`;
                    const title = u.title || u.filename || jp;
                    opt.textContent = `${bookLabel} · ${title}`;
                    frag.appendChild(opt);
                }
            }
        }

        sel.innerHTML = '';
        sel.appendChild(frag);
        importNceCatalogLoaded = true;
        importNceCatalogPlan = userPlan;
    } catch (e) {
        sel.innerHTML = '';
        const err = document.createElement('option');
        err.value = '';
        err.textContent = '（教材目录加载失败）';
        sel.appendChild(err);
        showMainBanner(e.message || '教材目录加载失败');
        importNceCatalogLoaded = false;
    } finally {
        sel.disabled = false;
    }
}

async function onImportNceLessonChange() {
    const sel = document.getElementById('import-nc-lesson-select');
    const ta = document.getElementById('import-article-textarea');
    if (!sel || !ta) return;
    const raw = sel.value;
    if (!raw) return;
    clearImportOcrReview();

    if (articleImportPickMode) {
        resetArticleImportPickUI();
    }

    const tab = raw.indexOf('\t');
    if (tab === -1) return;
    const corpusId = raw.slice(0, tab);
    const path = raw.slice(tab + 1);
    if (!corpusId || !path) return;

    ta.value = '';
    sel.disabled = true;
    ta.disabled = true;
    try {
        const params = new URLSearchParams({ corpus: corpusId, path });
        const data = await apiRequest(`/textbooks/lesson?${params}`);
        const lines = Array.isArray(data.lines) ? data.lines : [];
        const english = lines.map((l) => String(l.english || '').trim()).filter(Boolean);
        ta.value = english.join('\n');
    } catch (e) {
        showImportNotice(e.message || '加载课文失败', { isError: true });
        ta.value = '';
    } finally {
        sel.disabled = false;
        ta.disabled = false;
        sel.value = '';
    }
}

function initImportNceLessonSelect() {
    const sel = document.getElementById('import-nc-lesson-select');
    if (!sel || sel.dataset.bound === '1') return;
    sel.dataset.bound = '1';
    sel.addEventListener('change', () => {
        void onImportNceLessonChange();
    });
}

/** 文章提取后：气泡圈选，确认后再写入待复习 */
let articleImportPickMode = false;
let articleImportWords = [];
let articleImportBusy = false;
let articleConfirmImportBusy = false;
let importOcrBusy = false;
let importOcrUiCleanup = null;
let importOcrVersion = 0;
let importOcrRows = [];
let importOcrImage = null;
let importOcrImageUrl = '';
let importOcrReviewDirty = false;
let importVocabBusy = false;
/** @type {Set<number>} */
let articleImportSelectedIdx = new Set();
/** 导入成功且结果对话框已打开时，finally 不再恢复「确认导入」按钮 */
function resetArticleImportPickUI() {
    clearImportOcrReview();
    articleImportPickMode = false;
    articleImportBusy = false;
    articleConfirmImportBusy = false;
    articleImportWords = [];
    articleImportSelectedIdx.clear();
    importResultModalOnClose = null;
    hideImportResultModalShell();
    const taA = document.getElementById('import-article-textarea');
    if (taA) {
        taA.value = '';
        taA.style.display = '';
        taA.disabled = false;
    }
    const wrap = document.getElementById('import-article-pick-wrap');
    const pick = document.getElementById('import-article-pick');
    const btnA = document.getElementById('import-article-btn');
    if (wrap) wrap.hidden = true;
    if (pick) pick.innerHTML = '';
    if (btnA) {
        btnA.textContent = '从文章提取词汇';
        btnA.disabled = false;
    }
    const resultDiv = document.getElementById('article-import-result');
    if (resultDiv) {
        resultDiv.style.display = 'none';
        resultDiv.innerHTML = '';
    }
    renderArticleUnmatched([]);
    updatePlanUI();
}

function renderArticleUnmatched(lemmas) {
    const wrap = document.getElementById('import-article-unmatched-wrap');
    const el = document.getElementById('import-article-unmatched');
    if (!wrap || !el) return;
    const list = Array.isArray(lemmas)
        ? lemmas.filter((x) => x != null && String(x).trim() !== '')
        : [];
    if (!list.length) {
        wrap.hidden = true;
        el.innerHTML = '';
        return;
    }
    wrap.hidden = false;
    el.innerHTML = list
        .map((w, i) =>
            (i > 0 ? '<span class="import-article-unmatched-sep">, </span>' : '') +
            `<span class="import-article-unmatched-chip">${escapeHtml(String(w))}</span>`
        )
        .join('');
}

function syncImportArticleSelectAllCheckbox() {
    const cb = document.getElementById('import-article-select-all-cb');
    if (!cb || !articleImportWords.length) return;
    const n = articleImportSelectedIdx.size;
    const total = articleImportWords.length;
    cb.checked = n === total;
    cb.indeterminate = n > 0 && n < total;
}

function renderArticleImportPick() {
    const pick = document.getElementById('import-article-pick');
    if (!pick) return;
    pick.innerHTML = articleImportWords
        .map((w, i) => {
            const sel = articleImportSelectedIdx.has(i);
            const cls = sel ? 'import-article-chip import-article-chip--selected' : 'import-article-chip';
            return (
                `<button type="button" class="${cls}" data-idx="${i}" aria-pressed="${sel ? 'true' : 'false'}">` +
                `${escapeHtml(String(w.english || ''))}</button>`
            );
        })
        .join('');
    syncImportArticleSelectAllCheckbox();
}

function initImportArticlePickDelegation() {
    const pick = document.getElementById('import-article-pick');
    if (!pick || pick.dataset.bound === '1') return;
    pick.dataset.bound = '1';
    pick.addEventListener('click', (e) => {
        const b = e.target.closest('.import-article-chip');
        if (!b || !pick.contains(b)) return;
        const idx = parseInt(b.getAttribute('data-idx'), 10);
        if (Number.isNaN(idx) || idx < 0 || idx >= articleImportWords.length) return;
        if (articleImportSelectedIdx.has(idx)) articleImportSelectedIdx.delete(idx);
        else articleImportSelectedIdx.add(idx);
        renderArticleImportPick();
    });
}

function initImportArticleSelectAllCheckbox() {
    const cb = document.getElementById('import-article-select-all-cb');
    if (!cb || cb.dataset.bound === '1') return;
    cb.dataset.bound = '1';
    cb.addEventListener('change', () => {
        if (!articleImportWords.length) return;
        if (cb.checked) {
            articleImportSelectedIdx = new Set(articleImportWords.map((_, i) => i));
        } else {
            articleImportSelectedIdx.clear();
        }
        renderArticleImportPick();
    });
}

function applyArticleExtractResult(words, data) {
    clearImportOcrReview();
    const ta = document.getElementById('import-article-textarea');
    const wrap = document.getElementById('import-article-pick-wrap');
    const btnA = document.getElementById('import-article-btn');
    const resultDiv = document.getElementById('article-import-result');
    const vipW = document.getElementById('import-article-vip-extract-wrap');
    const spacyW = document.getElementById('import-article-spacy-wrap');
    articleImportPickMode = true;
    articleImportWords = words;
    articleImportSelectedIdx = new Set(words.map((_, i) => i));
    if (ta) {
        ta.value = '';
        ta.style.display = 'none';
    }
    if (vipW) vipW.hidden = true;
    if (spacyW) spacyW.hidden = true;
    if (wrap) wrap.hidden = false;
    renderArticleImportPick();
    if (btnA) btnA.textContent = '确认导入';
    let method = '（空格分词）';
    if (data.method === 'deepseek') method = '（AI 分词）';
    else if (data.method === 'spacy') method = '（spaCy 分词）';
    const un = Array.isArray(data.unmatched_lemmas) ? data.unmatched_lemmas : [];
    const usedSpacy = data.use_spacy === true;
    if (resultDiv) {
        resultDiv.style.display = 'block';
        const extra =
            un.length > 0 ? `另有 ${un.length} 个词库未匹配，见上方灰字列表。` : '';
        let spacyHint = '';
        if (userPlan !== 'paid' && un.length > 0 && !usedSpacy) {
            spacyHint =
                ' <span class="article-result-spacy-hint">若需匹配不规则词形，可勾选「智能还原」后重新提取。</span>';
        }
        resultDiv.innerHTML =
            `<p class="article-result-title">已提取 ${words.length} 个词库匹配词 ${method}。${extra}${spacyHint}点击单词可取消圈选，最下方「确认导入」加入待复习。</p>`;
    }
    renderArticleUnmatched(un);
}

async function confirmArticleImportFromPicks() {
    const accountContext = captureAccountContext();
    if (articleConfirmImportBusy) return;
    if (!articleImportSelectedIdx.size) {
        showImportNotice('请至少圈选一个词', { isError: true });
        return;
    }
    const items = [];
    const ordered = [...articleImportSelectedIdx].sort((a, b) => a - b);
    for (const i of ordered) {
        const w = articleImportWords[i];
        if (!w) continue;
        const ex = w.example1 || w.example || '';
        const exCn = w.example1_cn || '';
        const example = ex ? (exCn ? `${ex}_${exCn}` : ex) : '';
        items.push({
            english: w.english,
            chinese: w.chinese,
            example: example || undefined,
        });
    }
    if (!items.length) {
        showImportNotice('没有可导入的词条', { isError: true });
        return;
    }
    articleConfirmImportBusy = true;
    const btnA = document.getElementById('import-article-btn');
    if (btnA) {
        btnA.disabled = true;
        btnA.textContent = '导入中…';
    }
    const chunk = 500;
    let added = 0;
    let skipped = 0;
    let invalid = 0;
    const dupWords = [];
    try {
        for (let i = 0; i < items.length; i += chunk) {
            if (!isAccountContextCurrent(accountContext)) return;
            const part = items.slice(i, i + chunk);
            const res = await apiRequest('/words/import-json', {
                method: 'POST',
                body: JSON.stringify(part),
            });
            if (!isAccountContextCurrent(accountContext)) return;
            added += res.added || 0;
            skipped += res.skipped_duplicate || 0;
            invalid += res.skipped_invalid || 0;
            if (Array.isArray(res.skipped_duplicate_words)) {
                dupWords.push(...res.skipped_duplicate_words);
            }
        }
        if (added > 0) invalidateReviewDataAfterImport();
        const dupUnique = [...new Set(dupWords)];
        openImportResultModal({
            variant: 'stats',
            title: '导入结果',
            stats: {
                total: items.length,
                added,
                skipped,
                invalid,
                dupWords: dupUnique,
            },
            onClose: () => {
                if (!isAccountContextCurrent(accountContext)) return;
                resetArticleImportPickUI();
                loadStats();
                articleConfirmImportBusy = false;
            },
        });
    } catch (error) {
        if (!isAccountContextCurrent(accountContext)) return;
        showImportNotice(error.message || '导入失败', { isError: true });
        articleConfirmImportBusy = false;
        if (btnA) {
            btnA.disabled = false;
            btnA.textContent = '确认导入';
        }
    } finally {
        if (isAccountContextCurrent(accountContext) && articleImportPickMode && !importResultModalPending) {
            if (btnA) {
                btnA.disabled = false;
                btnA.textContent = '确认导入';
            }
        }
        if (isAccountContextCurrent(accountContext) && !importResultModalPending) {
            articleConfirmImportBusy = false;
        }
    }
}

async function importFromArticle() {
    const accountContext = captureAccountContext();
    if (importOcrBusy) return;
    if (importOcrReviewDirty) {
        showImportNotice('校对内容尚未应用。', { title: '识别校对', isError: true });
        return;
    }
    if (articleImportPickMode) {
        await confirmArticleImportFromPicks();
        return;
    }
    if (articleImportBusy) return;
    const prefix = 'import-article';
    const ta = document.getElementById('import-article-textarea');
    if (!ta) return;
    const text = ta.value.trim();
    if (!text) {
        showImportNotice('请先粘贴英文，或通过课文 / 图片识别填入下方文本框', { isError: true });
        return;
    }
    articleImportBusy = true;
    const btnA = document.getElementById('import-article-btn');
    if (btnA) btnA.disabled = true;
    const resultDiv = document.getElementById('article-import-result');
    renderArticleUnmatched([]);
    if (resultDiv) {
        resultDiv.style.display = 'block';
        resultDiv.innerHTML = '<span class="loading-dots">正在提取词汇…</span>';
    }
    try {
        const spacyCb = document.getElementById(`${prefix}-use-spacy-cb`);
        const use_spacy = userPlan === 'paid' ? true : !!(spacyCb && spacyCb.checked);
        const vipAiWrap = document.getElementById(`${prefix}-vip-extract-wrap`);
        const useAiExtract =
            userPlan === 'paid' &&
            vipAiWrap &&
            !vipAiWrap.hidden &&
            document.getElementById(`${prefix}-extract-ai`)?.checked === true;
        const body =
            userPlan === 'paid'
                ? { text, extract_mode: useAiExtract ? 'ai' : 'spacy' }
                : { text, use_spacy };
        const extraHeaders = {};
        if (useAiExtract) {
            const at = sessionStorage.getItem('adminToken');
            if (at) extraHeaders['X-Admin-Token'] = at;
        }
        let data = await apiRequest('/words/import-from-article', {
            method: 'POST',
            body: JSON.stringify(body),
            headers: extraHeaders,
        });
        if (!isAccountContextCurrent(accountContext)) return;
        if (data.job_id) {
            if (btnA) btnA.textContent = '后台提取中…';
            data = await waitForVocabImportJob(data.job_id, accountContext);
        }
        if (!isAccountContextCurrent(accountContext)) return;

        const words = Array.isArray(data.words) ? data.words : [];

        if (words.length === 0) {
            const un = Array.isArray(data.unmatched_lemmas) ? data.unmatched_lemmas : [];
            renderArticleUnmatched(un);
            const st = data.stats;
            let hint = data.message || '未在词库中找到匹配词汇';
            if (st && typeof st.lemmas_total === 'number') {
                hint += `（从文章识别 ${st.lemmas_total} 个不重复英文词，词库中均无匹配）`;
            }
            if (
                userPlan !== 'paid' &&
                !use_spacy &&
                un.length > 0
            ) {
                hint +=
                    ' 可勾选「用智能还原（spaCy）尝试匹配词形」后再次点击「从文章提取词汇」重试。';
            } else if (
                userPlan === 'paid' &&
                data.extract_mode === 'ai' &&
                data.method === 'deepseek' &&
                un.length > 0
            ) {
                hint += ' 可尝试改用「spaCy 分词」后再次提取。';
            }
            showImportNotice(hint, { title: '未匹配到词库', isError: true });
            if (resultDiv) resultDiv.style.display = 'none';
            return;
        }

        applyArticleExtractResult(words, data);
    } catch (error) {
        if (!isAccountContextCurrent(accountContext)) return;
        renderArticleUnmatched([]);
        showImportNotice(error.message || '提取失败', { isError: true });
        if (resultDiv) resultDiv.style.display = 'none';
    } finally {
        if (isAccountContextCurrent(accountContext)) {
            if (btnA) btnA.disabled = false;
            articleImportBusy = false;
        }
    }
}

/**
 * 词汇导入：根据输入形态解析为词条数组。
 * - 多行：每行一条（可含空格词组，如 New York）；行内若有逗号则再按逗号拆。
 * - 单行：有逗号/顿号等则按标点拆；否则整行算一条（保留空格词组，如 anything else）。多词请用逗号分隔。
 */
function parseVocabImportTokens(raw) {
    if (raw == null || typeof raw !== 'string') return [];
    const text = raw.replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
    if (!text) return [];
    const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);
    const delimInLine = /[,，;；、]/;
    const pushFromLine = (line, out) => {
        if (delimInLine.test(line)) {
            line.split(/[,，;；、]+/).forEach((p) => {
                const t = p.trim();
                if (t) out.push(t);
            });
        } else {
            out.push(line.trim());
        }
    };
    const tokens = [];
    if (lines.length === 1) {
        pushFromLine(lines[0], tokens);
    } else {
        for (const line of lines) {
            if (delimInLine.test(line)) {
                line.split(/[,，;；、]+/).forEach((p) => {
                    const t = p.trim();
                    if (t) tokens.push(t);
                });
            } else if (line) {
                tokens.push(line);
            }
        }
    }
    return tokens;
}

/** 文本框展示：一词/词组一行 */
function formatVocabImportTextarea(tokens) {
    return Array.isArray(tokens) ? tokens.join('\n') : '';
}

/** 后端 /wordbank/csv/import-words 仍按中英文逗号切分，与 DeepSeek 批处理入参一致 */
function vocabImportWordsForApi(tokens) {
    return tokens.join(', ');
}

function applyImportVocabTextareaNormalize() {
    const ta = document.getElementById('import-vocab-textarea');
    if (!ta) return;
    const tokens = parseVocabImportTokens(ta.value);
    const n = formatVocabImportTextarea(tokens);
    const cur = ta.value.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    if (cur !== n) ta.value = n;
}

function clearImportOcrReview() {
    importOcrUiCleanup?.();
    importOcrBusy = false;
    importOcrVersion += 1;
    importOcrRows = [];
    importOcrImage = null;
    importOcrReviewDirty = false;
    if (importOcrImageUrl) URL.revokeObjectURL(importOcrImageUrl);
    importOcrImageUrl = '';
    const review = document.getElementById('import-ocr-review');
    if (review) review.hidden = true;
    document.getElementById('import-ocr-review-lines')?.replaceChildren();
    document.getElementById('import-ocr-original')?.removeAttribute('href');
    const status = document.getElementById('import-ocr-status');
    if (status) status.hidden = true;
}

function setImportOcrStatus(text) {
    const status = document.getElementById('import-ocr-status');
    if (status) {
        status.textContent = text;
        status.hidden = !text;
    }
}

function renderImportOcrRows() {
    const list = document.getElementById('import-ocr-review-lines');
    if (!list) return;
    list.replaceChildren();
    document.getElementById('import-ocr-review-count').textContent = `${importOcrRows.length} 行`;
    importOcrRows.forEach((row, index) => {
        const line = document.createElement('div');
        line.className = 'import-ocr-review-line';
        const canvas = document.createElement('canvas');
        canvas.width = 288;
        canvas.height = 128;
        canvas.setAttribute('role', 'img');
        canvas.setAttribute('aria-label', `第 ${index + 1} 行原图`);
        const context = canvas.getContext('2d');
        if (context && importOcrImage && Array.isArray(row.box) && row.box.length === 4) {
            const image = importOcrImage;
            const [x, y, w, h] = row.box;
            const sx = Math.max(0, x * image.naturalWidth - 6);
            const sy = Math.max(0, y * image.naturalHeight - 6);
            const sw = Math.min(image.naturalWidth - sx, w * image.naturalWidth + 12);
            const sh = Math.min(image.naturalHeight - sy, h * image.naturalHeight + 12);
            const scale = Math.min(canvas.width / sw, canvas.height / sh);
            context.drawImage(image, sx, sy, sw, sh,
                (canvas.width - sw * scale) / 2, (canvas.height - sh * scale) / 2, sw * scale, sh * scale);
        }
        const fields = document.createElement('div');
        fields.className = 'import-ocr-review-fields';
        const input = document.createElement('input');
        input.type = 'text';
        input.value = row.text;
        input.placeholder = '待补充';
        input.spellcheck = false;
        input.autocomplete = 'off';
        input.setAttribute('autocapitalize', 'none');
        input.lang = 'en';
        input.setAttribute('aria-label', `第 ${index + 1} 行英文`);
        input.setAttribute('aria-invalid', row.needs_review ? 'true' : 'false');
        input.addEventListener('input', () => {
            row.text = input.value;
            row.needs_review = false;
            input.setAttribute('aria-invalid', 'false');
            importOcrReviewDirty = true;
        });
        fields.append(input);
        if (row.alternatives?.length) {
            const select = document.createElement('select');
            select.setAttribute('aria-label', `第 ${index + 1} 行候选`);
            select.add(new Option('候选词', ''));
            row.alternatives.forEach((text) => select.add(new Option(text, text)));
            select.addEventListener('change', () => {
                if (!select.value) return;
                row.text = select.value;
                row.needs_review = false;
                input.value = row.text;
                input.setAttribute('aria-invalid', 'false');
                importOcrReviewDirty = true;
                select.value = '';
            });
            fields.append(select);
        }
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'import-ocr-delete';
        remove.textContent = '×';
        remove.title = `删除第 ${index + 1} 行`;
        remove.setAttribute('aria-label', remove.title);
        remove.addEventListener('click', () => {
            importOcrRows.splice(index, 1);
            importOcrReviewDirty = true;
            renderImportOcrRows();
        });
        line.append(canvas, fields, remove);
        list.append(line);
    });
}

async function showImportOcrReview(rows, file, version) {
    if (!Array.isArray(rows) || !rows.length) return;
    importOcrRows = rows.map((row) => ({ ...row }));
    importOcrImageUrl = URL.createObjectURL(file);
    const image = new Image();
    image.src = importOcrImageUrl;
    await image.decode().catch(() => {});
    if (version !== importOcrVersion) return;
    importOcrImage = image.naturalWidth ? image : null;
    document.getElementById('import-ocr-original').href = importOcrImageUrl;
    renderImportOcrRows();
    const review = document.getElementById('import-ocr-review');
    review.hidden = false;
    review.open = true;
}

function applyImportOcrReview() {
    const text = importOcrRows.map((row) => row.text.trim()).filter(Boolean).join('\n');
    const textarea = document.getElementById('import-article-textarea');
    if (textarea) textarea.value = text;
    importOcrReviewDirty = false;
    document.getElementById('import-ocr-review').open = false;
    setImportOcrStatus('校对已应用');
}

async function runImportOcrToTextarea(file) {
    const accountContext = captureAccountContext();
    if (importOcrBusy || articleImportBusy || articleConfirmImportBusy) return;
    if (!file || !file.size) {
        showImportNotice('请选择有效的图片文件', { isError: true });
        return;
    }
    if (articleImportPickMode) resetArticleImportPickUI();
    clearImportOcrReview();
    const version = importOcrVersion;
    const isCurrent = () => isAccountContextCurrent(accountContext) && version === importOcrVersion;
    importOcrBusy = true;
    const btn = document.getElementById('import-ocr-pick-img-btn');
    const ta = document.getElementById('import-article-textarea');
    const lockedControls = ['import-article-btn', 'import-nc-lesson-select', 'import-ocr-english-only', 'import-ocr-handwriting']
        .map((id) => document.getElementById(id)).filter(Boolean);
    const previousDisabled = lockedControls.map((control) => control.disabled);
    const restoreUi = () => {
        if (importOcrUiCleanup !== restoreUi) return;
        lockedControls.forEach((control, index) => { control.disabled = previousDisabled[index]; });
        if (ta) ta.disabled = false;
        if (btn) {
            btn.disabled = false;
            btn.textContent = '选择图片…';
        }
        importOcrBusy = false;
        importOcrUiCleanup = null;
    };
    importOcrUiCleanup = restoreUi;
    lockedControls.forEach((control) => { control.disabled = true; });
    if (ta) ta.disabled = true;
    if (btn) {
        btn.disabled = true;
        btn.textContent = '识别中…';
    }
    const fd = new FormData();
    fd.append('file', file);
    const englishOnly = document.getElementById('import-ocr-english-only')?.checked !== false;
    fd.append('english_only', englishOnly ? '1' : '0');
    fd.append('handwriting', document.getElementById('import-ocr-handwriting')?.checked !== false ? '1' : '0');
    setImportOcrStatus('正在识别…');
    try {
        const data = await apiRequest('/wordbank/ocr-extract', { method: 'POST', body: fd });
        if (!isCurrent()) return;
        const raw = data.raw_text != null ? String(data.raw_text).trim() : '';
        if (ta) {
            ta.value = raw;
        }
        const localExtract = document.getElementById('import-article-extract-spacy');
        if (localExtract) localExtract.checked = true;
        await showImportOcrReview(data.review_lines, file, version);
        if (!isCurrent()) return;
        if (!raw && !data.review_lines?.length) {
            setImportOcrStatus('未识别到文字');
            showImportNotice(
                '未识别到文字。可换一张更清晰的图片，或检查服务端 OCR 依赖。',
                { title: '图片识别完成', isError: false }
            );
        } else {
            const count = (data.review_lines || []).filter((row) => row.needs_review).length;
            const resultStatus = data.handwriting && !data.enhancement_applied
                ? '增强未完成，已保留原图结果' : '识别完成';
            setImportOcrStatus(count ? `${resultStatus}，${count} 行待校对` : resultStatus);
        }
    } catch (error) {
        if (!isCurrent()) return;
        setImportOcrStatus('识别失败');
        showImportNotice(error.message || '识别失败', { title: '图片识别失败', isError: true });
    } finally {
        if (isCurrent()) restoreUi();
    }
}

function initImportVocabTextareaNormalize() {
    const ta = document.getElementById('import-vocab-textarea');
    if (!ta || ta.dataset.vocabNormalizeBound === '1') return;
    ta.dataset.vocabNormalizeBound = '1';
    ta.addEventListener('blur', () => applyImportVocabTextareaNormalize());
    ta.addEventListener('paste', () => {
        setTimeout(() => applyImportVocabTextareaNormalize(), 0);
    });
}

async function waitForVocabImportJob(jobId, accountContext = captureAccountContext()) {
    const deadline = Date.now() + (15 * 60 * 1000);
    while (Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 1200));
        if (!isAccountContextCurrent(accountContext)) throw new Error('登录账号已变更，已停止查询后台任务');
        const job = await apiRequest(`/wordbank/import-jobs/${encodeURIComponent(jobId)}`);
        if (!isAccountContextCurrent(accountContext)) throw new Error('登录账号已变更，已停止查询后台任务');
        if (job.status === 'succeeded' && job.result) return job.result;
        if (job.status === 'failed') {
            throw new Error(job.error || '后台导入失败');
        }
    }
    throw new Error('后台导入仍在进行，请稍后重试');
}

async function importVocabToCSV() {
    const accountContext = captureAccountContext();
    if (importVocabBusy) return;
    if (userPlan !== 'paid') {
        showImportNotice('词汇导入功能仅限 VIP 用户使用', { title: '无法导入', isError: true });
        return;
    }
    const ta = document.getElementById('import-vocab-textarea');
    const levelSel = document.getElementById('import-vocab-level');
    const addToQueueCb = document.getElementById('import-vocab-add-to-queue');
    if (!ta) return;
    applyImportVocabTextareaNormalize();
    const tokens = parseVocabImportTokens(ta.value);
    if (!tokens.length) {
        showImportNotice('请先输入单词列表', { isError: true });
        return;
    }
    const wordsPayload = vocabImportWordsForApi(tokens);
    const level = levelSel ? levelSel.value : '';
    const alsoAddToQueue = addToQueueCb ? !!addToQueueCb.checked : true;
    const btn = document.getElementById('import-vocab-btn');
    importVocabBusy = true;
    if (btn) {
        btn.disabled = true;
        btn.textContent = '处理中…';
    }
    try {
        let data = await apiRequest('/wordbank/csv/import-words', {
            method: 'POST',
            body: JSON.stringify({ words: wordsPayload, level, also_add_to_queue: alsoAddToQueue })
        });
        if (!isAccountContextCurrent(accountContext)) return;
        if (data.job_id) {
            if (btn) btn.textContent = '后台处理中…';
            data = await waitForVocabImportJob(data.job_id, accountContext);
        }
        if (!isAccountContextCurrent(accountContext)) return;
        if (Number(data.queue_result?.added) > 0) invalidateReviewDataAfterImport();
        openImportResultModal({
            variant: 'text',
            title: '词汇导入结果',
            text: buildVocabImportFeedback(data),
            onClose: () => {
                if (!isAccountContextCurrent(accountContext)) return;
                loadStats();
                const b = document.getElementById('import-vocab-btn');
                if (b) {
                    b.disabled = false;
                    b.textContent = '词汇导入';
                }
                importVocabBusy = false;
            },
        });
        ta.value = '';
        if (levelSel) levelSel.value = '';
    } catch (error) {
        if (!isAccountContextCurrent(accountContext)) return;
        showImportNotice(error.message || '导入失败', { isError: true });
        importVocabBusy = false;
        if (btn) {
            btn.disabled = false;
            btn.textContent = '词汇导入';
        }
    } finally {
        if (isAccountContextCurrent(accountContext) && btn && !importResultModalPending) {
            btn.disabled = false;
            btn.textContent = '词汇导入';
        }
        if (isAccountContextCurrent(accountContext) && !importResultModalPending) {
            importVocabBusy = false;
        }
    }
}

