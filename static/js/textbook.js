// ==================== 课文学习 ====================

/** @type {any[] | null} */
let textbookCatalogCache = null;
/** @type {{ corpusId: string, jsonPath: string, title: string } | null} */
let textbookReaderContext = null;
const textbookWordCache = new Map();
/** 词库无词条时：疑难词是否拦截（与 surface 对齐的小写 key） */
const textbookTroubleBlockedCache = new Map();
/** 同一 lemma 正在进行的单次查词请求（与预取并行时去重） */
const textbookLookupInflight = new Map();
/** 本次会话内：隐式词形命中类型 plural / past / ing / contraction（用于气泡提示） */
const textbookImplicitMorphHint = new Map();
let textbookTooltipToken = null;
/** 鼠标离开词后延迟关闭气泡，便于移入气泡点击「智能还原」 */
let textbookTooltipHideTimer = null;
/** 同一 lemma 整段导入流程互斥（含查词与 VIP 词汇导入） */
const textbookLemmaImportBusy = new Set();
/** 词库无该词时，限制重复点击/请求（毫秒时间戳） */
const textbookLemmaMissNotBefore = new Map();
/** 非 VIP：某词「智能还原」已尝试仍无词条时记录，刷新页面前不再显示该按钮 */
const textbookNlpFreeExhausted = new Set();

/** 过滤 LRC 中的课次标题行（如 Lesson 3 / 第3课），非正文 */
function isTextbookMetadataLine(line) {
    const en = String(line.english || '').trim();
    const zh = String(line.chinese || '').trim();
    if (/^lesson\s+\d+!?\s*$/i.test(en)) return true;
    if (/^第\d+课$/.test(zh) && /^lesson\s+\d+/i.test(en)) return true;
    return false;
}

function clearTextbookTooltipHideTimer() {
    if (textbookTooltipHideTimer) {
        clearTimeout(textbookTooltipHideTimer);
        textbookTooltipHideTimer = null;
    }
}

function scheduleHideTextbookTooltip(delayMs) {
    clearTextbookTooltipHideTimer();
    textbookTooltipHideTimer = setTimeout(() => {
        textbookTooltipHideTimer = null;
        hideTextbookTooltip();
    }, delayMs);
}

function hideTextbookTooltip() {
    clearTextbookTooltipHideTimer();
    const tip = document.getElementById('textbook-word-tooltip');
    if (tip) {
        tip.hidden = true;
        tip.textContent = '';
    }
    document.querySelectorAll('.tb-token--active').forEach((el) => el.classList.remove('tb-token--active'));
    textbookTooltipToken = null;
}

/** 气泡可交互：移入气泡取消关闭 */
function ensureTextbookTooltipHoverBridge() {
    const tip = document.getElementById('textbook-word-tooltip');
    if (!tip || tip.dataset.tbHoverBridge) return;
    tip.dataset.tbHoverBridge = '1';
    tip.addEventListener('mouseenter', () => clearTextbookTooltipHideTimer());
    tip.addEventListener('mouseleave', (e) => {
        const rel = e.relatedTarget;
        if (rel && textbookTooltipToken && (textbookTooltipToken === rel || textbookTooltipToken.contains(rel))) {
            return;
        }
        scheduleHideTextbookTooltip(220);
    });
}

let textbookTooltipScrollBound = false;

function ensureTextbookTooltipScrollReposition() {
    if (textbookTooltipScrollBound) return;
    textbookTooltipScrollBound = true;
    window.addEventListener(
        'scroll',
        () => {
            const tip = document.getElementById('textbook-word-tooltip');
            if (!tip || tip.hidden || !textbookTooltipToken) return;
            positionTextbookTooltipNearEl(textbookTooltipToken);
        },
        true,
    );
}

/** 将释义气泡锚定在单词下方（或上方若空间不足）；坐标均为视口，气泡须挂在非 transform 的 section 外 */
function positionTextbookTooltipNearEl(anchorEl) {
    const tip = document.getElementById('textbook-word-tooltip');
    if (!tip || tip.hidden || !anchorEl) return;
    void tip.offsetWidth;
    const tr = tip.getBoundingClientRect();
    const r = anchorEl.getBoundingClientRect();
    const margin = 8;
    const gap = 2;
    let left = r.left + r.width / 2 - tr.width / 2;
    let top = r.bottom + gap;
    if (left < margin) left = margin;
    if (left + tr.width > window.innerWidth - margin) {
        left = window.innerWidth - margin - tr.width;
    }
    if (top + tr.height > window.innerHeight - margin) {
        top = r.top - tr.height - gap;
    }
    if (top < margin) top = margin;
    tip.style.left = `${Math.round(left)}px`;
    tip.style.top = `${Math.round(top)}px`;
}

function showTextbookTooltip(html, anchorEl) {
    const tip = document.getElementById('textbook-word-tooltip');
    if (!tip) return;
    ensureTextbookTooltipHoverBridge();
    ensureTextbookTooltipScrollReposition();
    tip.innerHTML = html;
    tip.hidden = false;
    tip.style.left = '0';
    tip.style.top = '0';
    requestAnimationFrame(() => {
        requestAnimationFrame(() => positionTextbookTooltipNearEl(anchorEl));
    });
}

/** 导入结果提示：优先显示在单词旁气泡内，无锚点时退回顶栏 */
function showTextbookImportFeedback(anchorEl, message, variant) {
    const text = String(message || '').trim();
    if (!text) return;
    if (!anchorEl) {
        showMainBanner(text);
        return;
    }
    const cls =
        variant === 'error'
            ? 'tb-tip-feedback tb-tip-feedback--error'
            : variant === 'warn'
              ? 'tb-tip-feedback tb-tip-feedback--warn'
              : 'tb-tip-feedback tb-tip-feedback--ok';
    showTextbookTooltip(`<div class="${cls}">${escapeHtml(text)}</div>`, anchorEl);
    textbookTooltipToken = anchorEl;
    anchorEl.classList.add('tb-token--active');
}

function normalizeTextbookLemmaKey(lemma) {
    return String(lemma || '')
        .trim()
        .toLowerCase()
        .replace(/\u2019/g, "'")
        .replace(/\u2018/g, "'");
}

/** 解析 /wordbank/csv/search 响应（含 per_surface=1 的批量预取） */
function hydrateTextbookFromSearchResponse(data) {
    const sh = data.surface_hits;
    if (sh != null && typeof sh === 'object') {
        const sb = data.surface_blocked || {};
        for (const k of Object.keys(sh)) {
            textbookWordCache.set(k, sh[k] || null);
            if (Object.prototype.hasOwnProperty.call(sb, k)) textbookTroubleBlockedCache.set(k, !!sb[k]);
        }
        const inlp = data.implicit_lemma_nlp_resolution || {};
        const ip = data.implicit_plural_resolution || {};
        const ipt = data.implicit_past_resolution || {};
        const iing = data.implicit_ing_resolution || {};
        const icon = data.implicit_contraction_resolution || {};
        const isuf = data.implicit_suffix_resolution || {};
        for (const k of Object.keys(sh)) {
            if (sh[k]) {
                if (inlp[k]) textbookImplicitMorphHint.set(k, 'lemma_nlp');
                else if (ipt[k]) textbookImplicitMorphHint.set(k, 'past');
                else if (iing[k]) textbookImplicitMorphHint.set(k, 'ing');
                else if (icon[k]) textbookImplicitMorphHint.set(k, 'contraction');
                else if (isuf[k]) textbookImplicitMorphHint.set(k, 'suffix');
                else if (ip[k]) textbookImplicitMorphHint.set(k, 'plural');
                else textbookImplicitMorphHint.delete(k);
            } else {
                textbookImplicitMorphHint.delete(k);
            }
        }
        return;
    }
}

/**
 * 课文查词：默认仅词库 + 规则词形（nlp=0，不调用 spaCy）。
 * opts.nlp === true 时在用户确认「智能还原」后调用，走 spaCy。
 */
async function textbookLookupWord(lemma, opts = {}) {
    const k = normalizeTextbookLemmaKey(lemma);
    if (!k || k.length < 2) return null;
    const wantNlp = opts.nlp === true;
    if (textbookWordCache.has(k)) {
        const row = textbookWordCache.get(k);
        if (row != null || !wantNlp) return row;
    }
    const inflightKey = wantNlp ? `${k}\0nlp` : k;
    if (textbookLookupInflight.has(inflightKey)) return textbookLookupInflight.get(inflightKey);
    const p = (async () => {
        try {
            const nlp = wantNlp ? '1' : '0';
            const data = await apiRequest(
                `/wordbank/csv/search?q=${encodeURIComponent(k)}&per_surface=1&nlp=${nlp}&heuristics=1`,
            );
            hydrateTextbookFromSearchResponse(data);
            return textbookWordCache.has(k) ? textbookWordCache.get(k) : null;
        } catch (_) {
            return null;
        } finally {
            textbookLookupInflight.delete(inflightKey);
        }
    })();
    textbookLookupInflight.set(inflightKey, p);
    return p;
}

function buildTextbookMissingTooltipHtml(lemma, k, sub, opts = {}) {
    const exhausted =
        opts.nlpFreeExhausted === true || (userPlan !== 'paid' && textbookNlpFreeExhausted.has(k));
    const base =
        `<div class="tb-tip-en">${escapeHtml(lemma)}</div>` +
        `<div class="tb-tip-zh">${escapeHtml(sub)}</div>`;
    if (exhausted) {
        return (
            base +
            `<p class="tb-tip-meta tb-tip-nlp-done">未找到匹配词条。刷新页面前无法再次使用智能还原。</p>`
        );
    }
    return (
        base +
        `<div class="tb-tip-actions">` +
        `<button type="button" class="tb-tip-nlp-btn" data-tb-nlp="${escapeHtml(k)}">智能还原（词典，较慢）</button>` +
        `</div>` +
        `<div class="tb-tip-meta tb-tip-nlp-hint">不规则词形需加载语言模型，请按需点击</div>`
    );
}

async function runTextbookNlpResolve(lemma, anchorEl) {
    const k = normalizeTextbookLemmaKey(lemma);
    if (userPlan !== 'paid' && textbookNlpFreeExhausted.has(k)) return;
    ensureTextbookTooltipHoverBridge();
    const loadingHtml =
        userPlan === 'paid'
            ? '<div class="tb-tip-meta tb-tip-loading">词典还原中（较慢）…</div>'
            : '<div class="tb-tip-meta tb-tip-loading">正在查询…</div>';
    showTextbookTooltip(loadingHtml, anchorEl);
    const row = await textbookLookupWord(lemma, { nlp: true });
    if (row) {
        showTextbookTooltip(
            buildTextbookTooltipHtmlFromRow(row, lemma, textbookImplicitMorphHint.get(k) || null),
            anchorEl,
        );
    } else {
        const sub = await textbookSubtextWhenMissingInWordbank(lemma, k);
        if (userPlan !== 'paid') {
            textbookNlpFreeExhausted.add(k);
            showTextbookTooltip(buildTextbookMissingTooltipHtml(lemma, k, sub, { nlpFreeExhausted: true }), anchorEl);
        } else {
            showTextbookTooltip(
                `<div class="tb-tip-en">${escapeHtml(lemma)}</div>` +
                    `<div class="tb-tip-zh">${escapeHtml(sub)}</div>` +
                    `<p class="tb-tip-meta tb-tip-nlp-done">智能还原后仍未匹配词库。</p>`,
                anchorEl,
            );
        }
    }
}

/** 课文打开后批量预取本页生词，悬停时多数已命中缓存 */
async function textbookPrefetchLessonWords(rootEl) {
    const keys = new Set();
    rootEl.querySelectorAll('.textbook-token--word[data-lemma]').forEach((el) => {
        const k = normalizeTextbookLemmaKey(el.getAttribute('data-lemma'));
        if (k && k.length >= 2) keys.add(k);
    });
    const list = [...keys].filter((k) => !textbookWordCache.has(k));
    if (list.length === 0) return;
    const CHUNK = 100;
    for (let i = 0; i < list.length; i += CHUNK) {
        const chunk = list.slice(i, i + CHUNK);
        const q = chunk.join(',');
        try {
            const data = await apiRequest(
                `/wordbank/csv/search?q=${encodeURIComponent(q)}&per_surface=1&nlp=0&heuristics=1`,
            );
            hydrateTextbookFromSearchResponse(data);
        } catch (_) {
            /* 预取失败不阻塞阅读 */
        }
    }
}

async function textbookSubtextWhenMissingInWordbank(lemma, k, touchHint) {
    let sub = touchHint
        ? `词库暂无；短按可导入${userPlan === 'paid' ? '（AI 生成）' : '（需 VIP）'}`
        : `词库暂无，点击可尝试导入${userPlan === 'paid' ? '（VIP 自动 AI 生成）' : '（需 VIP）'}`;
    if (textbookTroubleBlockedCache.has(k)) {
        if (textbookTroubleBlockedCache.get(k)) {
            sub = '词库暂无；该词已列入疑难词，请联系管理员配置映射';
        }
        return sub;
    }
    try {
        const ts = await apiRequest(`/wordbank/csv/trouble-status?q=${encodeURIComponent(lemma)}`);
        if (ts && ts.blocked) {
            sub = '词库暂无；该词已列入疑难词，请联系管理员配置映射';
            textbookTroubleBlockedCache.set(k, true);
        } else {
            textbookTroubleBlockedCache.set(k, false);
        }
    } catch (_) {
        /* ignore */
    }
    return sub;
}

/** 课文表面形与词库词条不一致时（管理员映射或隐式复数/过去式/-ing/'s/'ve），第一行显示课文中的词，第二行显示 → 原形 */
function buildTextbookTooltipHtmlFromRow(row, surfaceLemma, morphKind) {
    const en = String(row.english || '').trim();
    const normSurf = String(surfaceLemma || '')
        .trim()
        .toLowerCase()
        .replace(/\u2019/g, "'")
        .replace(/\u2018/g, "'");
    const enL = en.toLowerCase();
    const ph = row.phonetic
        ? `<div class="tb-tip-meta">${escapeHtml(row.phonetic)} · ${escapeHtml(row.level || '')}</div>`
        : '';
    if (normSurf && enL && normSurf !== enL) {
        const implicitTag =
            morphKind === 'lemma_nlp'
                ? '<span class="tb-tip-hint">（词形还原）</span>'
                : morphKind === 'past'
                  ? '<span class="tb-tip-hint">（隐式过去式）</span>'
                  : morphKind === 'ing'
                    ? '<span class="tb-tip-hint">（隐式进行时）</span>'
                    : morphKind === 'contraction'
                      ? "<span class=\"tb-tip-hint\">（隐式 's / 've）</span>"
                      : morphKind === 'plural'
                        ? '<span class="tb-tip-hint">（隐式去复数）</span>'
                        : morphKind === 'suffix'
                          ? '<span class="tb-tip-hint">（词形推断）</span>'
                          : '';
        return (
            `<div class="tb-tip-en">${escapeHtml(surfaceLemma)}</div>` +
            `<div class="tb-tip-meta">→ ${escapeHtml(en)}${implicitTag}</div>` +
            `<div class="tb-tip-zh">${escapeHtml(row.chinese)}</div>` +
            ph
        );
    }
    return (
        `<div class="tb-tip-en">${escapeHtml(en)}</div>` +
        `<div class="tb-tip-zh">${escapeHtml(row.chinese)}</div>` +
        ph
    );
}

function buildImportItemFromCsvRow(w) {
    const ex = (w.example1 || w.example || '');
    const exCn = (w.example1_cn || '');
    const example = ex ? (exCn ? `${ex}_${exCn}` : ex) : '';
    return {
        english: w.english,
        chinese: w.chinese,
        example: example || undefined,
    };
}

async function importWordFromTextbookLemma(lemma, anchorEl) {
    const raw = String(lemma || '').trim();
    if (!raw) return;
    const k = raw.toLowerCase();
    if (textbookLemmaImportBusy.has(k)) return;

    const missCooldownMs = 4500;

    textbookLemmaImportBusy.add(k);
    try {
        let row = await textbookLookupWord(k);
        if (!row) {
            row = await textbookLookupWord(k, { nlp: true });
        }
        if (row) {
            try {
                const data = await apiRequest('/words/import-json', {
                    method: 'POST',
                    body: JSON.stringify([buildImportItemFromCsvRow(row)]),
                });
                const added = data.added || 0;
                const skipped = data.skipped_duplicate || 0;
                if (added > 0) {
                    showTextbookImportFeedback(anchorEl, `「${row.english}」已加入待复习`, 'ok');
                } else if (skipped > 0) {
                    showTextbookImportFeedback(anchorEl, `「${row.english}」已在学习列表中`, 'warn');
                } else {
                    showTextbookImportFeedback(anchorEl, data.message || '导入完成', 'ok');
                }
                loadStats();
            } catch (e) {
                showTextbookImportFeedback(anchorEl, e.message || '导入失败', 'error');
            }
            return;
        }

        const notBefore = textbookLemmaMissNotBefore.get(k) || 0;
        if (Date.now() < notBefore) {
            showTextbookImportFeedback(anchorEl, '请稍候再试', 'warn');
            return;
        }
        textbookLemmaMissNotBefore.set(k, Date.now() + missCooldownMs);

        if (userPlan !== 'paid') {
            showTextbookImportFeedback(
                anchorEl,
                '该词不在现有词库中。开通 VIP 后可自动通过词汇导入加入词库与待复习。',
                'warn',
            );
            return;
        }

        try {
            const ts = await apiRequest(`/wordbank/csv/trouble-status?q=${encodeURIComponent(k)}`);
            if (ts && ts.blocked) {
                showTextbookImportFeedback(
                    anchorEl,
                    '该词已列入疑难词库，暂不再调用 AI 生成；请等待管理员配置「表面形→词汇原形」映射后再试。',
                    'warn',
                );
                return;
            }
        } catch (_) {
            /* 继续尝试导入 */
        }

        try {
            const data = await apiRequest('/wordbank/csv/import-words', {
                method: 'POST',
                body: JSON.stringify({
                    words: k,
                    also_add_to_queue: true,
                }),
            });
            const msg = data.message || '已完成';
            showTextbookImportFeedback(anchorEl, msg, 'ok');
            textbookWordCache.delete(k);
            textbookImplicitMorphHint.delete(k);
            textbookTroubleBlockedCache.delete(k);
            loadStats();
        } catch (e) {
            showTextbookImportFeedback(anchorEl, e.message || '词汇导入失败', 'error');
        }
    } finally {
        textbookLemmaImportBusy.delete(k);
    }
}

function tokenizeTextbookEnglish(text) {
    const t = String(text || '');
    const parts = [];
    const re = /[a-zA-Z']+|\s+|[^a-zA-Z']+/g;
    let m;
    while ((m = re.exec(t)) !== null) {
        const raw = m[0];
        const isWord = /^[a-zA-Z']+$/.test(raw);
        let lemma = raw;
        if (isWord) {
            lemma = raw.toLowerCase().replace(/^'+|'+$/g, '');
        }
        parts.push({ raw, isWord, lemma });
    }
    return parts;
}

function renderTextbookEnglishTokens(english, lineIndex) {
    const parts = tokenizeTextbookEnglish(english);
    return parts
        .map((p, i) => {
            if (!p.isWord || p.lemma.length < 2) {
                return `<span class="textbook-token textbook-token--space">${escapeHtml(p.raw)}</span>`;
            }
            return (
                `<span class="textbook-token textbook-token--word" tabindex="0" ` +
                `data-lemma="${escapeHtml(p.lemma)}" data-line="${lineIndex}" data-idx="${i}">${escapeHtml(p.raw)}</span>`
            );
        })
        .join('');
}

function bindTextbookReaderInteractions(root) {
    const tip = document.getElementById('textbook-word-tooltip');
    const canHover = typeof window.matchMedia === 'function' && window.matchMedia('(hover: hover)').matches;

    root.querySelectorAll('.textbook-token--word').forEach((el) => {
        let longPressFired = false;
        let pressTimer = null;

        el.addEventListener('pointerdown', (e) => {
            if (e.pointerType !== 'touch') return;
            longPressFired = false;
            clearTimeout(pressTimer);
            const lemma = el.getAttribute('data-lemma');
            const cx = e.clientX;
            const cy = e.clientY;
            pressTimer = setTimeout(async () => {
                pressTimer = null;
                longPressFired = true;
                if (!lemma) return;
                clearTextbookTooltipHideTimer();
                el.classList.add('tb-token--active');
                textbookTooltipToken = el;
                const k = normalizeTextbookLemmaKey(lemma);
                if (!textbookWordCache.has(k)) {
                    showTextbookTooltip(
                        '<div class="tb-tip-meta tb-tip-loading">释义加载中…</div>',
                        el,
                    );
                }
                const row = await textbookLookupWord(lemma);
                if (row) {
                    showTextbookTooltip(
                        buildTextbookTooltipHtmlFromRow(row, lemma, textbookImplicitMorphHint.get(lemma.toLowerCase()) || null),
                        el,
                    );
                } else {
                    const sub = await textbookSubtextWhenMissingInWordbank(lemma, k, true);
                    showTextbookTooltip(
                        buildTextbookMissingTooltipHtml(lemma, k, sub, {
                            nlpFreeExhausted: userPlan !== 'paid' && textbookNlpFreeExhausted.has(k),
                        }),
                        el,
                    );
                }
            }, 480);
        });

        el.addEventListener('pointerup', () => {
            clearTimeout(pressTimer);
            pressTimer = null;
        });
        el.addEventListener('pointercancel', () => {
            clearTimeout(pressTimer);
            pressTimer = null;
        });

        el.addEventListener('mouseenter', async (ev) => {
            if (!canHover) return;
            const lemma = el.getAttribute('data-lemma');
            if (!lemma) return;
            clearTextbookTooltipHideTimer();
            el.classList.add('tb-token--active');
            textbookTooltipToken = el;
            const k = normalizeTextbookLemmaKey(lemma);
            if (!textbookWordCache.has(k)) {
                showTextbookTooltip(
                    '<div class="tb-tip-meta tb-tip-loading">释义加载中…</div>',
                    el,
                );
            }
            const row = await textbookLookupWord(lemma);
            if (row) {
                showTextbookTooltip(
                    buildTextbookTooltipHtmlFromRow(row, lemma, textbookImplicitMorphHint.get(lemma.toLowerCase()) || null),
                    el,
                );
            } else {
                const sub = await textbookSubtextWhenMissingInWordbank(lemma, k);
                showTextbookTooltip(
                    buildTextbookMissingTooltipHtml(lemma, k, sub, {
                        nlpFreeExhausted: userPlan !== 'paid' && textbookNlpFreeExhausted.has(k),
                    }),
                    el,
                );
            }
        });

        el.addEventListener('mouseleave', (e) => {
            if (!canHover) return;
            if (textbookTooltipToken !== el) return;
            const rel = e.relatedTarget;
            if (rel && tip && (tip === rel || tip.contains(rel))) return;
            scheduleHideTextbookTooltip(240);
        });

        el.addEventListener('mousemove', () => {
            if (textbookTooltipToken === el && tip && !tip.hidden) {
                positionTextbookTooltipNearEl(el);
            }
        });

        el.addEventListener('click', (ev) => {
            ev.stopPropagation();
            const lemma = el.getAttribute('data-lemma');
            if (!lemma) return;
            if (longPressFired) {
                longPressFired = false;
                return;
            }
            void importWordFromTextbookLemma(lemma, el);
        });

        el.addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter' || ev.key === ' ') {
                ev.preventDefault();
                const lemma = el.getAttribute('data-lemma');
                if (lemma) void importWordFromTextbookLemma(lemma, el);
            }
        });
    });

    ensureTextbookNlpButtonDelegation();
}

function ensureTextbookNlpButtonDelegation() {
    if (ensureTextbookNlpButtonDelegation._done) return;
    ensureTextbookNlpButtonDelegation._done = true;
    document.addEventListener('click', (e) => {
        const btn = e.target.closest && e.target.closest('.tb-tip-nlp-btn');
        if (!btn || !textbookTooltipToken) return;
        const tip = document.getElementById('textbook-word-tooltip');
        if (!tip || !tip.contains(btn)) return;
        e.preventDefault();
        e.stopPropagation();
        const kk = btn.getAttribute('data-tb-nlp');
        if (!kk) return;
        const nk = normalizeTextbookLemmaKey(kk);
        if (userPlan !== 'paid' && textbookNlpFreeExhausted.has(nk)) return;
        const anchor = textbookTooltipToken;
        const lemmaRaw = anchor.getAttribute('data-lemma') || kk;
        void runTextbookNlpResolve(lemmaRaw, anchor);
    });
}

let textbookDocClickBound = false;
function ensureTextbookDocClickClose() {
    if (textbookDocClickBound) return;
    textbookDocClickBound = true;
    document.addEventListener('click', (e) => {
        const t = e.target;
        if (t && t.closest && t.closest('.textbook-token--word')) return;
        if (t && t.closest && t.closest('.textbook-word-tooltip')) return;
        hideTextbookTooltip();
    });
}

function renderTextbookReader(data) {
    const reader = document.getElementById('textbook-reader');
    const catalogWrap = document.getElementById('textbook-catalog-wrap');
    if (!reader || !catalogWrap) return;

    const title = escapeHtml(data.title || data.filename || '课文');
    const rawLines = Array.isArray(data.lines) ? data.lines : [];
    const lines = rawLines.filter((line) => !isTextbookMetadataLine(line));

    const blocks = lines
        .map((line, idx) => {
            const en = String(line.english || '').trim();
            const zh = String(line.chinese || '').trim();
            const zhId = `textbook-zh-${idx}`;
            return (
                `<div class="textbook-line" data-line-index="${idx}">` +
                `<div class="textbook-line-row">` +
                `<button type="button" class="btn-speak textbook-speak-line" data-idx="${idx}" title="朗读本句" aria-label="朗读句子">🔊</button>` +
                `<div class="textbook-en-line">${renderTextbookEnglishTokens(en, idx)}</div>` +
                `</div>` +
                `<div class="textbook-zh-wrap">` +
                `<button type="button" class="textbook-zh-toggle" data-zh-target="${zhId}" aria-expanded="false">显示翻译</button>` +
                `<div id="${zhId}" class="textbook-zh-text" hidden>${escapeHtml(zh)}</div>` +
                `</div>` +
                `</div>`
            );
        })
        .join('');

    reader.innerHTML =
        `<div class="textbook-reader-head">` +
        `<button type="button" class="textbook-back-btn" id="textbook-back-btn">← 课文列表</button>` +
        `<h3 class="textbook-reader-title">${title}</h3>` +
        `</div>` +
        `<div class="textbook-lines">${blocks}</div>`;

    catalogWrap.style.display = 'none';
    reader.style.display = 'block';

    reader.querySelectorAll('.textbook-speak-line').forEach((btn) => {
        btn.addEventListener('click', () => {
            const i = parseInt(btn.getAttribute('data-idx') || '-1', 10);
            const line = lines[i];
            if (line && line.english) speakEnglishInBrowser(line.english, () => {});
        });
    });

    reader.querySelectorAll('.textbook-zh-toggle').forEach((btn) => {
        btn.addEventListener('click', () => {
            const id = btn.getAttribute('data-zh-target');
            const zhEl = id && document.getElementById(id);
            if (!zhEl) return;
            const open = zhEl.hasAttribute('hidden');
            if (open) {
                zhEl.removeAttribute('hidden');
                btn.setAttribute('aria-expanded', 'true');
                btn.textContent = '隐藏翻译';
            } else {
                zhEl.setAttribute('hidden', '');
                btn.setAttribute('aria-expanded', 'false');
                btn.textContent = '显示翻译';
            }
        });
    });

    bindTextbookReaderInteractions(reader);
    ensureTextbookDocClickClose();
    void textbookPrefetchLessonWords(reader);

    document.getElementById('textbook-back-btn')?.addEventListener('click', () => {
        hideTextbookTooltip();
        reader.style.display = 'none';
        catalogWrap.style.display = '';
        textbookReaderContext = null;
        reader.innerHTML = '';
    });
}

async function openTextbookLesson(corpusId, jsonPath) {
    hideTextbookTooltip();
    textbookReaderContext = { corpusId, jsonPath };
    const reader = document.getElementById('textbook-reader');
    if (reader) {
        reader.style.display = 'block';
        reader.innerHTML = '<p class="textbook-catalog-loading">加载课文中…</p>';
        document.getElementById('textbook-catalog-wrap').style.display = 'none';
    }
    try {
        const params = new URLSearchParams({ corpus: corpusId, path: jsonPath });
        const data = await apiRequest(`/textbooks/lesson?${params}`);
        renderTextbookReader(data);
    } catch (e) {
        showMainBanner(e.message || '加载失败');
        const catalogWrap = document.getElementById('textbook-catalog-wrap');
        if (reader) reader.style.display = 'none';
        if (catalogWrap) catalogWrap.style.display = '';
        if (reader) reader.innerHTML = '';
    }
}

/** 普通用户每册课文列表最多展示篇数；VIP（paid）展示全部 */
const TEXTBOOK_FREE_UNITS_PER_BOOK = 10;

function renderTextbookCatalog(corpora) {
    const root = document.getElementById('textbook-catalog');
    if (!root) return;

    if (!corpora.length) {
        root.innerHTML = '<p class="textbook-catalog-empty">暂无教材数据。可在 static/wordbanks/textbooks/index.json 中配置。</p>';
        return;
    }

    const isVip = userPlan === 'paid';
    const hintTop =
        !isVip
            ? `<p class="textbook-catalog-hint" role="note">普通用户每册仅展示前 ${TEXTBOOK_FREE_UNITS_PER_BOOK} 篇课文；<strong>VIP</strong> 可查看全部。</p>`
            : '';

    const html = corpora
        .map((c) => {
            const manifest = c.manifest || {};
            const books = Array.isArray(manifest.books) ? manifest.books : [];
            const bookHtml = books
                .map((b) => {
                    const key = escapeHtml(b.key || '');
                    const label = `${escapeHtml(b.bookName || '')} ${escapeHtml(b.bookLevel || '')}`.trim() || key;
                    const units = Array.isArray(b.units) ? b.units : [];
                    const unitsShown = isVip ? units : units.slice(0, TEXTBOOK_FREE_UNITS_PER_BOOK);
                    const unitBtns = unitsShown
                        .map((u) => {
                            const jp = String(u.json || '').trim();
                            if (!jp) return '';
                            const label = escapeHtml(u.title || u.filename || jp);
                            return (
                                `<button type="button" class="textbook-unit-btn" data-corpus="${escapeHtml(c.id)}" ` +
                                `data-json-path="${escapeHtml(jp)}">${label}</button>`
                            );
                        })
                        .join('');
                    if (!unitBtns) return '';
                    return (
                        `<div class="textbook-book-block">` +
                        `<div class="textbook-book-label">${label}</div>` +
                        `<div class="textbook-unit-grid">${unitBtns}</div>` +
                        `</div>`
                    );
                })
                .join('');
            if (!bookHtml) {
                return `<div class="textbook-corpus-block"><h3 class="textbook-corpus-title">${escapeHtml(c.title)}</h3><p class="textbook-catalog-empty">该教材 manifest 中暂无课文条目。</p></div>`;
            }
            return `<div class="textbook-corpus-block"><h3 class="textbook-corpus-title">${escapeHtml(c.title)}</h3>${bookHtml}</div>`;
        })
        .join('');

    root.innerHTML = hintTop + html;

    root.querySelectorAll('.textbook-unit-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            const corpusId = btn.getAttribute('data-corpus');
            const jsonPath = btn.getAttribute('data-json-path');
            if (corpusId && jsonPath) void openTextbookLesson(corpusId, jsonPath);
        });
    });
}

async function loadTextbookSection() {
    const root = document.getElementById('textbook-catalog');
    if (!root) return;

    if (textbookCatalogCache) {
        renderTextbookCatalog(textbookCatalogCache);
        return;
    }

    root.innerHTML = '<p class="textbook-catalog-loading"><span class="loading-dots">加载教材目录</span></p>';
    try {
        const data = await apiRequest('/textbooks/catalog');
        const corpora = Array.isArray(data.corpora) ? data.corpora : [];
        textbookCatalogCache = corpora;
        renderTextbookCatalog(corpora);
    } catch (e) {
        root.innerHTML = `<p class="textbook-catalog-empty">${escapeHtml(e.message || '加载失败')}</p>`;
        showMainBanner(e.message || '教材目录加载失败');
    }
}
