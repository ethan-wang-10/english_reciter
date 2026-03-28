// ==================== 课文学习 ====================

/** @type {any[] | null} */
let textbookCatalogCache = null;
/** @type {{ corpusId: string, jsonPath: string, title: string } | null} */
let textbookReaderContext = null;
const textbookWordCache = new Map();
let textbookTooltipToken = null;
/** 同一 lemma 整段导入流程互斥（含查词与 VIP 词汇导入） */
const textbookLemmaImportBusy = new Set();
/** 词库无该词时，限制重复点击/请求（毫秒时间戳） */
const textbookLemmaMissNotBefore = new Map();

/** 过滤 LRC 中的课次标题行（如 Lesson 3 / 第3课），非正文 */
function isTextbookMetadataLine(line) {
    const en = String(line.english || '').trim();
    const zh = String(line.chinese || '').trim();
    if (/^lesson\s+\d+!?\s*$/i.test(en)) return true;
    if (/^第\d+课$/.test(zh) && /^lesson\s+\d+/i.test(en)) return true;
    return false;
}

function hideTextbookTooltip() {
    const tip = document.getElementById('textbook-word-tooltip');
    if (tip) {
        tip.hidden = true;
        tip.textContent = '';
    }
    document.querySelectorAll('.tb-token--active').forEach((el) => el.classList.remove('tb-token--active'));
    textbookTooltipToken = null;
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

async function textbookLookupWord(lemma) {
    const k = String(lemma || '').trim().toLowerCase();
    if (!k || k.length < 2) return null;
    if (textbookWordCache.has(k)) return textbookWordCache.get(k);
    try {
        const params = new URLSearchParams({ q: k });
        const data = await apiRequest(`/wordbank/csv/search?${params}`);
        const words = Array.isArray(data.words) ? data.words : [];
        const row = words[0] || null;
        // 命中与未命中均缓存（含已有映射仍无词条），避免同一词反复悬停打接口
        textbookWordCache.set(k, row);
        return row;
    } catch (_) {
        return null;
    }
}

/** 课文表面形与词库词条不一致时（全局映射），第一行显示课文中的词，第二行显示 → 原形 */
function buildTextbookTooltipHtmlFromRow(row, surfaceLemma) {
    const en = String(row.english || '').trim();
    const surf = String(surfaceLemma || '').trim().toLowerCase();
    const enL = en.toLowerCase();
    const ph = row.phonetic
        ? `<div class="tb-tip-meta">${escapeHtml(row.phonetic)} · ${escapeHtml(row.level || '')}</div>`
        : '';
    if (surf && enL && surf !== enL) {
        return (
            `<div class="tb-tip-en">${escapeHtml(surfaceLemma)}</div>` +
            `<div class="tb-tip-meta">→ ${escapeHtml(en)}</div>` +
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
        const row = await textbookLookupWord(k);
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
                el.classList.add('tb-token--active');
                textbookTooltipToken = el;
                const row = await textbookLookupWord(lemma);
                if (row) {
                    showTextbookTooltip(buildTextbookTooltipHtmlFromRow(row, lemma), el);
                } else {
                    let sub = `词库暂无；短按可导入${userPlan === 'paid' ? '（AI 生成）' : '（需 VIP）'}`;
                    try {
                        const ts = await apiRequest(`/wordbank/csv/trouble-status?q=${encodeURIComponent(lemma)}`);
                        if (ts && ts.blocked) {
                            sub = '词库暂无；该词已列入疑难词，请联系管理员配置映射';
                        }
                    } catch (_) {
                        /* ignore */
                    }
                    showTextbookTooltip(
                        `<div class="tb-tip-en">${escapeHtml(lemma)}</div>` + `<div class="tb-tip-zh">${escapeHtml(sub)}</div>`,
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
            el.classList.add('tb-token--active');
            textbookTooltipToken = el;
            const row = await textbookLookupWord(lemma);
            if (row) {
                showTextbookTooltip(buildTextbookTooltipHtmlFromRow(row, lemma), el);
            } else {
                let sub = `词库暂无，点击可尝试导入${userPlan === 'paid' ? '（VIP 自动 AI 生成）' : '（需 VIP）'}`;
                try {
                    const ts = await apiRequest(`/wordbank/csv/trouble-status?q=${encodeURIComponent(lemma)}`);
                    if (ts && ts.blocked) {
                        sub = '词库暂无；该词已列入疑难词，请联系管理员配置映射';
                    }
                } catch (_) {
                    /* ignore */
                }
                showTextbookTooltip(
                    `<div class="tb-tip-en">${escapeHtml(lemma)}</div>` + `<div class="tb-tip-zh">${escapeHtml(sub)}</div>`,
                    el,
                );
            }
        });

        el.addEventListener('mouseleave', () => {
            if (!canHover) return;
            if (textbookTooltipToken === el) hideTextbookTooltip();
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
}

let textbookDocClickBound = false;
function ensureTextbookDocClickClose() {
    if (textbookDocClickBound) return;
    textbookDocClickBound = true;
    document.addEventListener('click', (e) => {
        const t = e.target;
        if (t && t.closest && t.closest('.textbook-token--word')) return;
        if (t && t.id === 'textbook-word-tooltip') return;
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
