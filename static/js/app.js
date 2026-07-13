// 全局状态
let token = localStorage.getItem('token');
let passwordResetToken = new URLSearchParams(window.location.search).get('reset_token') || '';
/** 当前弹窗中的系统广播 id，用于确认已读 */
let systemBroadcastPendingId = null;
let username = localStorage.getItem('username');
/** 家长登录：服务端以孩子身份操作数据；界面显示 childUsername */
let isParentSession = localStorage.getItem('session_is_parent') === '1';
let childUsername = localStorage.getItem('session_child_username') || '';

function setSessionParentFlags(isParent, child) {
    isParentSession = !!isParent;
    childUsername = child || '';
    if (isParentSession) {
        localStorage.setItem('session_is_parent', '1');
        localStorage.setItem('session_child_username', childUsername);
    } else {
        localStorage.removeItem('session_is_parent');
        localStorage.removeItem('session_child_username');
    }
}

/** 排行榜/奖池「我」的标识（学生用户名） */
function sessionStudentUsername() {
    return isParentSession && childUsername ? childUsername : username;
}
let currentReviewList = [];
let currentReviewIndex = 0;
let currentErrorCount = 0; // 当前单词错误次数
let currentRevealedCount = 0; // 当前单词已揭示字母数
let isSubmitting = false; // 防止重复提交（修复一闪而过bug）
let isAdvancing = false;  // 防止重复推进到下一题

/** 用户套餐类型: 'free' | 'paid'（paid 对应 VIP 权益，展示文案统一为 VIP） */
let userPlan = 'free';
/** 服务端是否配置了 DeepSeek；来自 /api/user/plan */
let articleAiExtractAvailable = false;
/** 管理后台是否开启「AI 文章分词」；来自 /api/user/plan */
let articleAiExtractEnabled = false;
/** 服务端是否配置 Piper（神经语音 WAV）；来自 /api/tts/capabilities */
let serverPiperAvailable = false;
/** 首屏 bootstrap 预取的复习列表，供 showSection('review') 消费，避免重复请求。 */
let preloadedReviewData = null;
/** 连续点击朗读：取消上一轮 Piper 请求、音频与浏览器合成 */
let ttsPlaybackGeneration = 0;
let ttsFetchAbort = null;
let ttsPiperAudio = null;
let ttsPiperObjectUrl = null;

/** 朗读进行中：禁用所有 .btn-speak；triggerButton 显示加载样式 */
function beginTtsSpeakUi(triggerButton) {
    document.querySelectorAll('.btn-speak').forEach((btn) => {
        btn.dataset.ttsWasDisabled = btn.disabled ? '1' : '0';
        btn.disabled = true;
        btn.setAttribute('aria-busy', 'true');
    });
    const el =
        triggerButton && triggerButton.classList && triggerButton.classList.contains('btn-speak')
            ? triggerButton
            : null;
    if (el) el.classList.add('btn-speak-loading');
}

function endTtsSpeakUi() {
    document.querySelectorAll('.btn-speak').forEach((btn) => {
        const was = btn.dataset.ttsWasDisabled === '1';
        delete btn.dataset.ttsWasDisabled;
        btn.disabled = was;
        btn.removeAttribute('aria-busy');
        btn.classList.remove('btn-speak-loading');
    });
}

/** 本轮 3 次尝试均错的单词（去重顺序）；一轮结束后用于生成下一轮错题复习 */
let wrongWordsInThisPass = new Set();
let wrongWordsOrder = [];
/** 当前会话中见过的单词对象，供错题轮从内存取词 */
let wordMap = new Map();
/** 0=今日待复习；≥1 表示第几轮错题复习 */
let wrongRoundNumber = 0;

/** 当次复习会话统计（loadReviewList 重置，showFinalComplete 展示） */
let sessionInitialMainWords = 0;
let sessionMainCorrect = 0;
let sessionMainFailedThree = 0;
let sessionRemedialCorrect = 0;
let sessionTotalWrongAttempts = 0;
let sessionNewMastered = [];

/** daily=今日待复习；bonus=无待复习时的随机加练 */
let reviewSessionMode = 'daily';

/** 主轮结束后在弹框中选择「稍后再说」未进入错题巩固（用于总结文案） */
let sessionSkippedRemedialAfterMain = false;

/** 最近一次 GET /api/gamification 结果，用于成就展示 */
let lastGamificationProfile = null;

/** PK 邀约角标轮询（主界面登录后启动，登出/回登录页停止） */
let pkInvitePollTimer = null;
let pkInviteRefreshInFlight = false;
const PK_INVITE_POLL_MS = 3600000;
const pkDuelRespondInFlight = new Set();
let pkDuelSendInFlight = false;

// API 基础 URL
const API_BASE = '/api';

// ---- 性能优化：页面切换数据缓存 & 滚动位置记忆 ----
const _sectionLastLoad = {};
const _SECTION_STALE_MS = 30000;
function _isSectionFresh(id) {
    return _sectionLastLoad[id] && (Date.now() - _sectionLastLoad[id] < _SECTION_STALE_MS);
}
function _markSectionLoaded(id) {
    _sectionLastLoad[id] = Date.now();
}
function _invalidateSectionCache(id) {
    delete _sectionLastLoad[id];
}

const _sectionScrollPos = {};
let _currentSectionId = 'review';

function runWhenIdle(fn, timeout = 1500) {
    if (typeof window !== 'undefined' && typeof window.requestIdleCallback === 'function') {
        window.requestIdleCallback(() => fn(), { timeout });
    } else {
        setTimeout(fn, Math.min(timeout, 1200));
    }
}

function _saveScrollPos(sectionId) {
    const el = document.getElementById(sectionId + '-section');
    if (el) _sectionScrollPos[sectionId] = el.scrollTop || window.scrollY;
}
function _restoreScrollPos(sectionId) {
    const saved = _sectionScrollPos[sectionId];
    if (saved > 0) {
        requestAnimationFrame(() => {
            const el = document.getElementById(sectionId + '-section');
            if (el && el.scrollTop !== undefined) {
                el.scrollTop = saved;
            } else {
                window.scrollTo(0, saved);
            }
        });
    }
}

function escapeHtml(text) {
    if (text == null || text === undefined) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function escapeRegExp(str) {
    return String(str).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function isAbortError(error) {
    return !!error && (error.name === 'AbortError' || error.code === 20);
}

/** 将焦点放回透明输入层（真正可输入的元素），便于连打与重试 */
function focusWordCapture(delayMs = 0) {
    const run = () => {
        const rb = document.getElementById('review-box');
        if (!rb || rb.style.display === 'none') return;
        const sec = document.getElementById('review-section');
        if (!sec || !sec.classList.contains('active')) return;
        const el = document.getElementById('mobile-word-capture');
        if (!el) return;
        try {
            el.focus({ preventScroll: true });
        } catch (_) {
            el.focus();
        }
        requestAnimationFrame(() => {
            if (document.activeElement !== el) {
                try {
                    el.focus({ preventScroll: true });
                } catch (_) {
                    el.focus();
                }
            }
        });
    };
    if (delayMs > 0) {
        setTimeout(run, delayMs);
    } else {
        requestAnimationFrame(run);
    }
}

/** 学习进度：展示下次复习日期与距今天数（API 已提供 ISO 日期与 remaining_days） */
function formatNextReviewLine(word) {
    const iso = word.next_review_date;
    if (!iso) return '—';
    const datePart = String(iso).slice(0, 10);
    const rd = typeof word.remaining_days === 'number' ? word.remaining_days : 0;
    let hint = '';
    if (rd === 0) {
        hint = '今天';
    } else if (rd < 0) {
        hint = `已逾期 ${Math.abs(rd)} 天`;
    } else {
        hint = `还有 ${rd} 天`;
    }
    return `${datePart} · ${hint}`;
}

function formatNumber(n) {
    const x = Number(n);
    if (Number.isNaN(x)) return '0';
    return x.toLocaleString('zh-CN');
}

/** 从例句字段中取英文部分（优先 `_` / `→` 前一段；与复习页朗读一致） */
function englishFromExampleField(exampleText) {
    if (!exampleText || exampleText === '暂无例句') return '';
    return String(exampleText).split(' → ')[0].split('_')[0].trim();
}

/** 复习例句英文是否与当前「本题」例句一致（用于多义多条展示时标出挖空那条） */
function reviewExampleEnglishMatchesQuiz(exEn, quizEn) {
    const a = (exEn || '').trim();
    const b = (quizEn || '').trim();
    if (!a || !b) return false;
    return a === b || a.replace(/\s+/g, ' ') === b.replace(/\s+/g, ' ');
}

/** 与原先复习区单条展示一致：挖空待填词 */
function formatReviewQuizExampleDisplay(word) {
    let exampleText = word.example || '暂无例句';
    if (exampleText === '暂无例句') return exampleText;
    const parts = exampleText.split('_');
    if (parts.length >= 2) {
        let englishPart = parts[0];
        const maskTarget = (word.example_form || '').trim() || word.english;
        const regex = new RegExp(`\\b${escapeRegExp(maskTarget)}\\b`, 'gi');
        englishPart = englishPart.replace(regex, '_'.repeat(maskTarget.length));
        if (maskTarget !== word.english) {
            const regex2 = new RegExp(`\\b${escapeRegExp(word.english)}\\b`, 'gi');
            englishPart = englishPart.replace(regex2, '_'.repeat(word.english.length));
        }
        return englishPart + ' → ' + parts.slice(1).join('_');
    }
    const maskTarget = (word.example_form || '').trim() || word.english;
    const regex = new RegExp(`\\b${escapeRegExp(maskTarget)}\\b`, 'gi');
    return exampleText.replace(regex, '_'.repeat(maskTarget.length));
}

/** 词库返回多条例句时，渲染多行（本题那条保持挖空，其余完整展示） */
function buildReviewExamplesDisplayHtml(word) {
    const exs = word.examples;
    if (!Array.isArray(exs) || exs.length < 2) return null;
    const quizEn = englishFromExampleField(word.example || '');
    let quizIdx = exs.findIndex((ex) =>
        reviewExampleEnglishMatchesQuiz((ex.en || '').trim(), quizEn),
    );
    if (quizIdx < 0) quizIdx = 0;
    const chunks = exs.map((ex, idx) => {
        const en = (ex.en || '').trim();
        const cn = (ex.cn || '').trim();
        let lineText;
        if (idx === quizIdx) {
            lineText = formatReviewQuizExampleDisplay(word);
        } else {
            lineText = cn ? `${en} → ${cn}` : en;
        }
        const cls =
            idx === quizIdx ? 'review-example-line review-example-line--quiz' : 'review-example-line';
        return `<div class="${cls}">${escapeHtml(lineText)}</div>`;
    });
    return `<div class="review-example-lines">${chunks.join('')}</div>`;
}

/** 导航连续火苗：0 天时灰色「熄灭」样式（见 .ng-streak-flame--out） */
function ngStreakPillInnerHtml(streak, makeupAvailable = false) {
    const n = Math.max(0, Number(streak) || 0);
    const out = n === 0;
    const flameClass = out ? 'ng-streak-flame ng-streak-flame--out' : 'ng-streak-flame';
    const badge = makeupAvailable
        ? '<span class="ng-streak-makeup-badge" aria-hidden="true">补</span>'
        : '';
    return `<span class="${flameClass}" aria-hidden="true">🔥</span><span class="ng-streak-num">${formatNumber(n)}</span>${badge}`;
}

/** 排行榜「连续」列：显示当前连续，并在括号里补充历史最高连续 */
function lbStreakCellInnerHtml(streak, streakMax) {
    const n = Math.max(0, Number(streak) || 0);
    const hasMax = streakMax !== undefined && streakMax !== null;
    const rawMax = Number(streakMax);
    const maxN = Number.isFinite(rawMax) ? Math.max(0, rawMax) : 0;
    const shownMax = hasMax ? Math.max(n, maxN) : 0;
    const out = n === 0;
    const flameClass = out ? 'lb-streak-flame lb-streak-flame--out' : 'lb-streak-flame';
    const maxHtml =
        shownMax > 0
            ? ` <span class="lb-streak-max">（最高 ${escapeHtml(formatNumber(shownMax))}）</span>`
            : '';
    return `<span class="${flameClass}" aria-hidden="true">🔥</span> <span class="lb-streak-val">${escapeHtml(formatNumber(n))}</span>${maxHtml}`;
}

function streakDiagnosticsHintFromRow(r) {
    const streak = Math.max(0, Number(r?.streak) || 0);
    const maxRecord = Math.max(0, Number(r?.streak_max_record) || 0);
    const minCorrect = Math.max(1, Number(r?.check_in_min_correct) || 5);
    const start = r?.streak_current_start_date;
    const end = r?.streak_current_end_date;
    const parts = [];
    if (start && end) {
        parts.push(`当前连续 ${formatNumber(streak)} 天：${start} 至 ${end}`);
    } else {
        parts.push(`当前连续 ${formatNumber(streak)} 天`);
    }
    if (maxRecord > 0) {
        parts.push(`历史最高 ${formatNumber(maxRecord)} 天`);
    }
    const gap = r?.streak_gap_before_current_date;
    if (gap && maxRecord > streak) {
        const cnt = Number(r?.streak_gap_before_current_correct_count) || 0;
        const xp = Number(r?.streak_gap_before_current_daily_xp) || 0;
        parts.push(
            `系统认为 ${gap} 未达到有效打卡：答对 ${formatNumber(cnt)}/${formatNumber(minCorrect)}，当日 XP ${formatNumber(xp)}`,
        );
    }
    return parts.join('；');
}

/** 今日小结「当前连续」一行：0 天时灰色火苗 */
function dailySummaryStreakLineHtml(streak) {
    const n = Math.max(0, Number(streak) || 0);
    const out = n === 0;
    const flameClass = out ? 'ds-streak-flame ds-streak-flame--out' : 'ds-streak-flame';
    return (
        `<li class="daily-summary-streak${out ? ' daily-summary-streak--out' : ''}">` +
        `当前连续有效打卡 ` +
        `<span class="ds-streak-inline">` +
        `<span class="${flameClass}" aria-hidden="true">🔥</span>` +
        `<strong class="ds-streak-num">${escapeHtml(formatNumber(n))}</strong> 天` +
        `</span></li>`
    );
}

function updateGamificationNav(g) {
    if (!g) return;
    const lv = document.getElementById('ng-level');
    const xp = document.getElementById('ng-xp');
    const st = document.getElementById('ng-streak');
    const ck = document.getElementById('ng-checkin');
    const minC = Number(g.check_in_min_correct) || 5;
    const todayC = Number(g.today_correct_count) || 0;
    const checkInDone = !!g.check_in_done_today || (minC > 0 && todayC >= minC);
    const ckText = checkInDone ? '✓ 打卡完成' : `打卡 ${todayC}/${minC}`;
    const ckTitle = checkInDone
        ? `今日已有效打卡（已答对 ${todayC} 词）`
        : `今日答对 ${todayC} 词，有效打卡需 ${minC} 词`;
    const streakN = Math.max(0, Number(g.streak) || 0);
    const streakTitle =
        Number(g.streak_max_record) > 0
            ? `当前连续 ${streakN} 天（有效打卡：每日至少答对 ${minC} 词）；中断后归零；历史最高 ${formatNumber(g.streak_max_record)} 天`
            : `当前连续（有效打卡：每日至少答对 ${minC} 词）；中断后归零`;
    const sd = g.streak_diagnostics || {};
    const gap = sd.gap_before_current_date;
    const gapHint =
        gap && Number(g.streak_max_record) > streakN
            ? `；系统认为 ${gap} 未达到有效打卡：答对 ${formatNumber(sd.gap_before_current_correct_count || 0)}/${formatNumber(minC)}，当日 XP ${formatNumber(sd.gap_before_current_daily_xp || 0)}`
            : '';
    const makeupAvailable = !!(g.makeup_checkin && g.makeup_checkin.available === true);
    const makeupHint = makeupAvailable
        ? `；可用 ${formatNumber(Number(g.makeup_checkin.cost_xp) || 0)} XP 补救 ${g.makeup_checkin.target_date || '断点'}`
        : '';
    if (lv && xp && st) {
        lv.textContent = `Lv.${g.level}`;
        xp.textContent = `${formatNumber(g.total_xp)} XP`;
        st.innerHTML = ngStreakPillInnerHtml(g.streak, makeupAvailable);
        st.classList.toggle('ng-streak--out', streakN === 0);
        st.classList.toggle('ng-streak--makeup', makeupAvailable);
        st.title = `${streakTitle}${gapHint}${makeupHint}。点击查看补救规则`;
        st.setAttribute('aria-label', `${streakTitle}${gapHint}${makeupHint}。点击查看补救规则`);
    }
    if (ck) {
        ck.textContent = ckText;
        ck.title = ckTitle;
        ck.classList.toggle('ng-checkin-done', checkInDone);
    }
    const mlv = document.getElementById('mobile-ng-level');
    const mxp = document.getElementById('mobile-ng-xp');
    const mst = document.getElementById('mobile-ng-streak');
    const mck = document.getElementById('mobile-ng-checkin');
    if (mlv && mxp && mst) {
        mlv.textContent = `Lv.${g.level}`;
        mxp.textContent = `${formatNumber(g.total_xp)} XP`;
        mst.innerHTML = ngStreakPillInnerHtml(g.streak, makeupAvailable);
        mst.classList.toggle('ng-streak--out', streakN === 0);
        mst.classList.toggle('ng-streak--makeup', makeupAvailable);
        mst.title = `${streakTitle}${gapHint}${makeupHint}。点击查看补救规则`;
        mst.setAttribute('aria-label', `${streakTitle}${gapHint}${makeupHint}。点击查看补救规则`);
    }
    if (mck) {
        mck.textContent = ckText;
        mck.title = ckTitle;
        mck.classList.toggle('ng-checkin-done', checkInDone);
    }
}

let dailySummaryPopoverOpen = false;
let xpHistoryModalOpen = false;

function formatDailySummaryDateLabel() {
    const d = new Date();
    return d.toLocaleDateString('zh-CN', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        weekday: 'long',
    });
}

function renderDailySummaryBody(g, statusData) {
    const body = document.getElementById('daily-summary-body');
    if (!body) return;
    if (!g || !statusData) {
        body.innerHTML = '<p class="daily-summary-empty">暂无数据</p>';
        return;
    }
    const minC = Number(g.check_in_min_correct) || 5;
    const todayC = Number(g.today_correct_count) || 0;
    const done = !!g.check_in_done_today;
    const xpToday = Number(g.daily_xp_today) || 0;
    const streak = Number(g.streak) || 0;
    const streakMax = Number(g.streak_max_record) || 0;
    const words = Array.isArray(statusData.words) ? statusData.words : null;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const dueToday = words
        ? words.filter((w) => {
              const nd = new Date(w.next_review_date);
              nd.setHours(0, 0, 0, 0);
              return nd <= today;
          }).length
        : Number(statusData.due_count ?? statusData.stats?.due_count ?? 0);
    const round =
        statusData.stats && statusData.stats.current_round != null
            ? statusData.stats.current_round + 1
            : '—';
    const mastered = statusData.stats ? statusData.stats.mastered_words : 0;

    const goal = g.monthly_checkin_goal;
    const goalMonth = g.monthly_checkin_goal_month;
    const monthKey = g.month_key;
    let monthLine = '';
    if (goal != null && goalMonth === monthKey) {
        const md = Number(g.month_valid_checkin_days) || 0;
        const gNum = Number(goal);
        monthLine = `<li>本月有效打卡 <strong>${formatNumber(md)}</strong> / ${formatNumber(gNum)} 天</li>`;
    }

    const checkLine = done
        ? `<li>今日打卡 <strong>已完成</strong>（已答对 ${formatNumber(todayC)} 词）</li>`
        : `<li>今日打卡 <strong>进行中</strong>（已答对 ${formatNumber(todayC)} / ${formatNumber(minC)} 词）</li>`;

    body.innerHTML =
        `<p class="daily-summary-date">${escapeHtml(formatDailySummaryDateLabel())}</p>` +
        '<ul class="daily-summary-list">' +
        `<li>今日答对 <strong>${formatNumber(todayC)}</strong> 词</li>` +
        `<li>今日获得 <strong>${formatNumber(xpToday)}</strong> XP</li>` +
        checkLine +
        dailySummaryStreakLineHtml(streak) +
        (streakMax > 0
            ? `<li>历史最高连续打卡 <strong>${formatNumber(streakMax)}</strong> 天</li>`
            : '') +
        monthLine +
        `<li>当前复习轮次 <strong>第 ${escapeHtml(String(round))} 轮</strong></li>` +
        `<li>今日仍待复习（含逾期）<strong>${formatNumber(dueToday)}</strong> 词</li>` +
        `<li>累计已掌握 <strong>${formatNumber(mastered)}</strong> 词</li>` +
        '</ul>';
}

function closeDailySummaryPopover() {
    const pop = document.getElementById('daily-summary-popover');
    if (!pop || !dailySummaryPopoverOpen) return;
    dailySummaryPopoverOpen = false;
    pop.hidden = true;
    document.getElementById('username-display')?.setAttribute('aria-expanded', 'false');
    document.getElementById('daily-summary-mobile-btn')?.setAttribute('aria-expanded', 'false');
}

async function openDailySummaryPopover() {
    const pop = document.getElementById('daily-summary-popover');
    const body = document.getElementById('daily-summary-body');
    if (!pop || !body) return;
    dailySummaryPopoverOpen = true;
    pop.hidden = false;
    document.getElementById('username-display')?.setAttribute('aria-expanded', 'true');
    document.getElementById('daily-summary-mobile-btn')?.setAttribute('aria-expanded', 'true');
    body.innerHTML = '<p class="daily-summary-loading">加载中…</p>';
    try {
        const [g, ws] = await Promise.all([apiRequest('/gamification'), apiRequest('/words/summary')]);
        lastGamificationProfile = g;
        updateGamificationNav(g);
        if (isSettingsOverlayOpen() && lastGamificationProfile) {
            updateSettingsCheckinHintFromProfile(lastGamificationProfile);
            updateMakeupCheckinDialogFromProfile(lastGamificationProfile);
            updateSettingsMonthlyGoalBonusNotice(lastGamificationProfile);
        }
        renderDailySummaryBody(g, ws);
    } catch (e) {
        body.innerHTML = `<p class="daily-summary-error">${escapeHtml(e.message || '加载失败')}</p>`;
    }
}

function toggleDailySummaryPopover() {
    if (dailySummaryPopoverOpen) {
        closeDailySummaryPopover();
    } else {
        void openDailySummaryPopover();
    }
}

function setupDailySummaryPopover() {
    const wrap = document.getElementById('nav-user-summary-wrap');
    const ubtn = document.getElementById('username-display');
    const mbtn = document.getElementById('daily-summary-mobile-btn');
    const onTrigger = (e) => {
        e.preventDefault();
        e.stopPropagation();
        toggleDailySummaryPopover();
    };
    ubtn?.addEventListener('click', onTrigger);
    mbtn?.addEventListener('click', onTrigger);

    document.addEventListener('click', (e) => {
        if (!dailySummaryPopoverOpen || !wrap) return;
        if (wrap.contains(e.target)) return;
        closeDailySummaryPopover();
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && dailySummaryPopoverOpen) {
            closeDailySummaryPopover();
        }
    });
}

function setXpHistoryTriggerExpanded(expanded) {
    document.getElementById('ng-xp')?.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    document.getElementById('mobile-ng-xp')?.setAttribute('aria-expanded', expanded ? 'true' : 'false');
}

function closeXpHistoryModal() {
    const modal = document.getElementById('xp-history-modal');
    if (!modal || !xpHistoryModalOpen) return;
    xpHistoryModalOpen = false;
    modal.style.display = 'none';
    modal.setAttribute('aria-hidden', 'true');
    setXpHistoryTriggerExpanded(false);
}

function formatXpHistoryDate(dateIso) {
    const d = new Date(`${String(dateIso).slice(0, 10)}T00:00:00`);
    if (Number.isNaN(d.getTime())) return String(dateIso || '');
    return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric', weekday: 'short' });
}

function formatXpHistoryMonth(monthKey) {
    const parts = String(monthKey || '').split('-');
    if (parts.length !== 2) return monthKey || '';
    return `${parts[0]}年${Number(parts[1])}月`;
}

function formatSignedXp(n) {
    const value = Number(n) || 0;
    if (value > 0) return `+${formatNumber(value)} XP`;
    if (value < 0) return `-${formatNumber(Math.abs(value))} XP`;
    return '0 XP';
}

function xpHistoryValueClass(n) {
    const value = Number(n) || 0;
    if (value > 0) return 'xp-history-value--positive';
    if (value < 0) return 'xp-history-value--negative';
    return 'xp-history-value--neutral';
}

function xpHistorySourceText(sources) {
    const rows = Array.isArray(sources) ? sources.filter((s) => Number(s.xp) !== 0) : [];
    if (!rows.length) return '明细：暂无';
    const labels = rows.slice(0, 2).map((s) => `${s.label || 'XP'} ${formatSignedXp(s.xp)}`);
    const more = rows.length > 2 ? ` +${rows.length - 2}项` : '';
    return `明细：${labels.join(' / ')}${more}`;
}

function buildXpHistorySummaryHtml(data) {
    const income = Number(data?.total_income_xp ?? data?.total_xp) || 0;
    const expense = Number(data?.total_expense_xp) || 0;
    const net = Number(data?.net_xp ?? income - expense) || 0;
    return (
        '<div class="xp-history-summary">' +
        `<div class="xp-history-stat"><span class="xp-history-stat-label">收入</span><span class="xp-history-stat-value xp-history-value--positive">+${formatNumber(income)} XP</span></div>` +
        `<div class="xp-history-stat"><span class="xp-history-stat-label">支出</span><span class="xp-history-stat-value xp-history-value--negative">-${formatNumber(expense)} XP</span></div>` +
        `<div class="xp-history-stat"><span class="xp-history-stat-label">净变化</span><span class="xp-history-stat-value ${xpHistoryValueClass(net)}">${formatSignedXp(net)}</span></div>` +
        '</div>'
    );
}

function renderXpHistoryBody(data) {
    const body = document.getElementById('xp-history-body');
    if (!body) return;
    const entries = Array.isArray(data?.entries) ? data.entries : [];
    const range =
        data?.start_date && data?.end_date
            ? `${escapeHtml(data.start_date)} 至 ${escapeHtml(data.end_date)}，仅显示有 XP 收支的日期`
            : '仅显示有 XP 收支的日期';
    if (!entries.length) {
        body.innerHTML =
            buildXpHistorySummaryHtml(data || {}) +
            `<p class="xp-history-range">${range}</p>` +
            '<p class="xp-history-empty">最近 2 个月还没有 XP 收支记录。</p>';
        return;
    }

    const maxXp = Math.max(...entries.map((row) => Math.abs(Number(row.net_xp ?? row.xp) || 0)), 1);
    const groups = new Map();
    entries.forEach((row) => {
        const key = String(row.date || '').slice(0, 7) || 'unknown';
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(row);
    });
    const monthHtml = Array.from(groups.entries())
        .map(([monthKey, rows]) => {
            const monthTotal = rows.reduce((sum, row) => sum + (Number(row.net_xp ?? row.xp) || 0), 0);
            const rowsHtml = rows
                .map((row) => {
                    const xp = Number(row.net_xp ?? row.xp) || 0;
                    const pct = Math.max(4, Math.min(100, Math.round((Math.abs(xp) / maxXp) * 100)));
                    const flowClass =
                        xp > 0
                            ? 'xp-history-row--positive'
                            : xp < 0
                              ? 'xp-history-row--negative'
                              : 'xp-history-row--neutral';
                    return (
                        `<li class="xp-history-row ${flowClass}">` +
                        `<span class="xp-history-date">${escapeHtml(formatXpHistoryDate(row.date))}</span>` +
                        `<span class="xp-history-bar" aria-hidden="true"><span class="xp-history-bar-fill" style="width:${pct}%"></span></span>` +
                        `<span class="xp-history-xp ${xpHistoryValueClass(xp)}">${formatSignedXp(xp)}</span>` +
                        `<span class="xp-history-sources" title="${escapeHtml(xpHistorySourceText(row.sources))}">${escapeHtml(xpHistorySourceText(row.sources))}</span>` +
                        '</li>'
                    );
                })
                .join('');
            return (
                '<section class="xp-history-month">' +
                `<h4 class="xp-history-month-title"><span>${escapeHtml(formatXpHistoryMonth(monthKey))}</span><span class="xp-history-month-total ${xpHistoryValueClass(monthTotal)}">${formatSignedXp(monthTotal)}</span></h4>` +
                `<ul class="xp-history-list">${rowsHtml}</ul>` +
                '</section>'
            );
        })
        .join('');

    body.innerHTML =
        buildXpHistorySummaryHtml(data || {}) +
        `<p class="xp-history-range">${range}</p>` +
        `<div class="xp-history-scroll">${monthHtml}</div>`;
}

async function openXpHistoryModal() {
    const modal = document.getElementById('xp-history-modal');
    const body = document.getElementById('xp-history-body');
    if (!modal || !body) return;
    closeDailySummaryPopover();
    xpHistoryModalOpen = true;
    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');
    setXpHistoryTriggerExpanded(true);
    body.innerHTML = '<p class="xp-history-loading">加载中…</p>';
    try {
        const data = await apiRequest('/gamification/xp-history');
        renderXpHistoryBody(data);
    } catch (e) {
        body.innerHTML = `<p class="xp-history-error">${escapeHtml(e.message || '加载失败')}</p>`;
    }
}

function setupXpHistoryModal() {
    const open = (e) => {
        e.preventDefault();
        e.stopPropagation();
        void openXpHistoryModal();
    };
    document.getElementById('ng-xp')?.addEventListener('click', open);
    document.getElementById('mobile-ng-xp')?.addEventListener('click', open);
    document.getElementById('xp-history-close')?.addEventListener('click', closeXpHistoryModal);
    document.getElementById('xp-history-modal')?.addEventListener('click', (e) => {
        if (e.target.classList.contains('xp-history-backdrop')) closeXpHistoryModal();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && xpHistoryModalOpen) closeXpHistoryModal();
    });
}

function openMobileMoreSheet() {
    const sheet = document.getElementById('mobile-more-sheet');
    const btn = document.getElementById('mobile-more-btn');
    if (sheet) {
        sheet.classList.add('is-open');
        sheet.setAttribute('aria-hidden', 'false');
    }
    if (btn) btn.setAttribute('aria-expanded', 'true');
}

function closeMobileMoreSheet() {
    const sheet = document.getElementById('mobile-more-sheet');
    const btn = document.getElementById('mobile-more-btn');
    if (sheet) {
        sheet.classList.remove('is-open');
        sheet.setAttribute('aria-hidden', 'true');
    }
    if (btn) btn.setAttribute('aria-expanded', 'false');
}

/** 键盘占高阈值（px）：超过则认为软键盘已弹出，用于隐藏底栏、压缩复习页 */
const KEYBOARD_OPEN_INSET_THRESHOLD = 72;
/** 低于此值才移除 keyboard-open（滞回，避免惯性滚动时 inset 在阈值附近抖动导致底栏反复显隐） */
const KEYBOARD_OPEN_INSET_THRESHOLD_CLOSE = 40;

/** 上次写入的键盘占位（px），减少无意义的 style 写入 */
let lastKeyboardInsetRounded = -9999;

/**
 * 键盘开着时，用户手指/惯性滚动会触发 visualViewport.resize 与 inset 微变；
 * 反复改 --keyboard-inset 会改变 #main-page 的 padding-bottom 与可滚高度，导致整页上下跳。
 * 在「用户正在滚动」期间暂停写入，仅在停顿后或 focus 时强制同步。
 * 仅用于「今日复习」页（html.is-review-section），避免影响导入等其它页面。
 */
let keyboardInsetUpdateMuted = false;
let keyboardInsetMuteSettleTimer = null;
/** 脚本 scrollBy 对齐题干后短时间内忽略 scroll 监听，避免误进 mute */
let ignoreKeyboardInsetScrollMuteUntil = 0;

/** 根据 visualViewport 估算键盘占用高度，供 #main-page 底部 padding 抬高可滚动区域 */
function updateVisualViewportKeyboardInset(force = false) {
    if (
        keyboardInsetUpdateMuted &&
        !force &&
        document.documentElement.classList.contains('is-review-section')
    ) {
        return;
    }
    const vv = window.visualViewport;
    const html = document.documentElement;
    if (!vv) {
        if (lastKeyboardInsetRounded !== 0) {
            html.style.setProperty('--keyboard-inset', '0px');
            lastKeyboardInsetRounded = 0;
        }
        html.classList.remove('keyboard-open');
        return;
    }
    const inset = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
    const rounded = Math.round(inset);
    if (Math.abs(rounded - lastKeyboardInsetRounded) >= 2) {
        html.style.setProperty('--keyboard-inset', `${rounded}px`);
        lastKeyboardInsetRounded = rounded;
    }

    /* 滞回：中间带不切换，避免 iOS 滑动时 offsetTop/height 微变导致 keyboard-open 闪烁 */
    if (rounded >= KEYBOARD_OPEN_INSET_THRESHOLD) {
        html.classList.add('keyboard-open');
    } else if (rounded <= KEYBOARD_OPEN_INSET_THRESHOLD_CLOSE) {
        html.classList.remove('keyboard-open');
    }
}

function isTextLikeField(el) {
    if (!el) return false;
    if (el.tagName === 'TEXTAREA') return true;
    if (el.tagName !== 'INPUT') return false;
    const type = String(el.type || 'text').toLowerCase();
    return ['text', 'password', 'search', 'email', 'tel', 'url', 'number'].includes(type) || type === '';
}

/**
 * 将复习题干对齐到当前视觉视口上部（相对 layout 视口 + visualViewport.offsetTop），
 * 避免仅用 scrollIntoView(nearest) 把题干顶出屏幕。
 */
function scrollReviewContentIntoVisualViewport() {
    const reviewContent = document.querySelector('#review-box .review-content');
    if (!reviewContent) return;

    const run = () => {
        const vv = window.visualViewport;
        const margin = 10;
        if (vv) {
            const rect = reviewContent.getBoundingClientRect();
            const targetTop = vv.offsetTop + margin;
            const dy = rect.top - targetTop;
            if (Math.abs(dy) > 2) {
                ignoreKeyboardInsetScrollMuteUntil = Date.now() + 450;
                window.scrollBy({ top: dy, left: 0, behavior: 'auto' });
            }
            return;
        }
        ignoreKeyboardInsetScrollMuteUntil = Date.now() + 450;
        reviewContent.scrollIntoView({ block: 'start', behavior: 'auto' });
    };

    /* 一帧后再对齐，等 keyboard-open 等 class 触发布局；勿同步连跑两次 scrollBy，易与惯性滚动打架 */
    requestAnimationFrame(run);
}

/** 复习透明输入层或导入区输入框聚焦时，将输入区滚入可视范围 */
function scrollFocusedInputIntoViewIfNeeded() {
    const el = document.activeElement;
    if (!el || !isTextLikeField(el)) return;
    if (el.id === 'mobile-word-capture') {
        const rb = document.getElementById('review-box');
        if (rb && rb.style.display !== 'none') {
            scrollReviewContentIntoVisualViewport();
        }
        return;
    }
    if (el.closest('#import-section')) {
        el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'auto' });
    }
}

function setupVisualViewportKeyboardAvoid() {
    const vv = window.visualViewport;
    if (!vv) return;

    let rafInset = null;
    /** 仅更新键盘占位与 keyboard-open，不在此路径调用 scrollBy（软键盘开着时 resize 会连发，与 scrollReviewContentIntoVisualViewport 叠加会导致整页上下抖） */
    const scheduleInsetOnly = () => {
        if (rafInset != null) return;
        rafInset = requestAnimationFrame(() => {
            rafInset = null;
            updateVisualViewportKeyboardInset(false);
        });
    };

    /** 仅在今日复习页：键盘已弹出时用户滚动期间暂停改 padding，避免与惯性滚动打架 */
    const bumpKeyboardInsetScrollMute = () => {
        if (!document.documentElement.classList.contains('is-review-section')) return;
        if (!document.documentElement.classList.contains('keyboard-open')) return;
        if (Date.now() < ignoreKeyboardInsetScrollMuteUntil) return;
        keyboardInsetUpdateMuted = true;
        clearTimeout(keyboardInsetMuteSettleTimer);
        keyboardInsetMuteSettleTimer = setTimeout(() => {
            keyboardInsetUpdateMuted = false;
            updateVisualViewportKeyboardInset(true);
        }, 380);
    };

    vv.addEventListener('resize', scheduleInsetOnly);
    window.addEventListener('resize', scheduleInsetOnly);
    scheduleInsetOnly();

    window.addEventListener('touchmove', bumpKeyboardInsetScrollMute, { passive: true });
    window.addEventListener('scroll', bumpKeyboardInsetScrollMute, { passive: true, capture: true });

    document.getElementById('main-page')?.addEventListener('focusin', (e) => {
        const t = e.target;
        if (!isTextLikeField(t)) return;
        if (t.id !== 'mobile-word-capture' && !t.closest('#import-section')) return;
        keyboardInsetUpdateMuted = false;
        clearTimeout(keyboardInsetMuteSettleTimer);
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                updateVisualViewportKeyboardInset(true);
                scrollFocusedInputIntoViewIfNeeded();
            });
        });
    }, true);
}

const WRONG_ASIDE_EXPANDED_KEY = 'wrongWordsAsideExpanded';

function syncWrongWordsAsideExpandedState() {
    const aside = document.getElementById('wrong-words-aside');
    const btn = document.getElementById('wrong-words-toggle');
    if (!aside || !btn) return;
    const narrow = window.matchMedia('(max-width: 900px)').matches;
    if (!narrow) {
        aside.classList.remove('is-expanded');
        btn.setAttribute('aria-expanded', 'true');
        return;
    }
    const expanded = sessionStorage.getItem(WRONG_ASIDE_EXPANDED_KEY) === '1';
    aside.classList.toggle('is-expanded', expanded);
    btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
}

function setupWrongWordsAsideToggle() {
    const aside = document.getElementById('wrong-words-aside');
    const btn = document.getElementById('wrong-words-toggle');
    if (!aside || !btn) return;

    btn.addEventListener('click', () => {
        if (!window.matchMedia('(max-width: 900px)').matches) return;
        const next = !aside.classList.contains('is-expanded');
        aside.classList.toggle('is-expanded', next);
        btn.setAttribute('aria-expanded', next ? 'true' : 'false');
        sessionStorage.setItem(WRONG_ASIDE_EXPANDED_KEY, next ? '1' : '0');
    });

    window.addEventListener('resize', () => {
        syncWrongWordsAsideExpandedState();
    });
    syncWrongWordsAsideExpandedState();
}

async function refreshGamification() {
    try {
        const g = await apiRequest('/gamification');
        lastGamificationProfile = g;
        updateGamificationNav(g);
        const opt = document.getElementById('leaderboard-opt-in');
        if (opt && typeof g.leaderboard_opt_in === 'boolean') {
            opt.checked = g.leaderboard_opt_in;
        }
        return g;
    } catch (_) {
        return null;
    }
}

function setSettingsMessage(text, isError) {
    const el = document.getElementById('settings-message');
    if (!el) return;
    el.textContent = text || '';
    el.classList.toggle('settings-message-error', !!isError);
}

function openSettings() {
    const ov = document.getElementById('settings-overlay');
    if (ov) {
        ov.style.display = 'flex';
        ov.setAttribute('aria-hidden', 'false');
    }
    loadUserSettingsPanel();
}

function closeSettings() {
    const ov = document.getElementById('settings-overlay');
    if (ov) {
        ov.style.display = 'none';
        ov.setAttribute('aria-hidden', 'true');
    }
}

function isSettingsOverlayOpen() {
    const ov = document.getElementById('settings-overlay');
    return Boolean(ov && ov.style.display !== 'none' && ov.getAttribute('aria-hidden') !== 'true');
}

/** 仅更新设置面板「今日打卡」文案（字段与 GET /gamification 一致） */
function updateSettingsCheckinHintFromProfile(g) {
    if (!g) return;
    const hint = document.getElementById('settings-checkin-hint');
    if (!hint) return;
    const minC = Number(g.check_in_min_correct) || 5;
    const tc = Number(g.today_correct_count) || 0;
    const done = g.check_in_done_today;
    hint.textContent = `今日已答对 ${tc} 词；有效打卡需至少 ${minC} 词。${done ? '今日已有效打卡。' : '继续加油。'}`;
}

let makeupCheckinDialogOpen = false;

function setMakeupEntrypointExpanded(open) {
    document.querySelectorAll('.js-makeup-checkin-open').forEach((el) => {
        el.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
}

function renderMakeupCheckinDialog(g, options = {}) {
    const status = document.getElementById('makeup-checkin-status');
    const rules = document.getElementById('makeup-checkin-rules');
    const btn = document.getElementById('makeup-checkin-buy');
    if (!status || !rules || !btn) return;
    if (options.loading) {
        status.textContent = '正在刷新补救状态…';
        rules.textContent = '补救只延续连续火苗，不计入本月目标、奖池或 PK。';
        btn.disabled = true;
        btn.textContent = '加载中…';
        return;
    }
    if (options.error) {
        status.textContent = options.error;
        rules.textContent = '请稍后再试；补救购买成功前不会扣除 XP。';
        btn.disabled = true;
        btn.textContent = '暂不可用';
        return;
    }
    const m = g && g.makeup_checkin;
    if (!m) {
        status.textContent = '暂无补打卡数据。';
        rules.textContent = '补救只延续连续火苗，不计入本月目标、奖池或 PK。';
        btn.disabled = true;
        btn.textContent = '暂不可用';
        return;
    }
    const code = String(m.reason_code || '');
    const cost = Number(m.cost_xp) || 0;
    const target = m.target_date ? String(m.target_date) : '昨天';
    const currentXp = Number(m.current_xp) || 0;
    const daysAgo = Math.max(1, Number(m.days_ago) || 1);
    const targetLabel = daysAgo === 1 ? '昨天' : `${formatNumber(daysAgo)} 天前`;
    const todayDone = !!g.check_in_done_today;
    const todayRule = todayDone
        ? '今天已完成真实打卡。'
        : `今天仍需正常答对 ${formatNumber(Number(g.check_in_min_correct) || 5)} 词。`;
    const limitText =
        Number(m.monthly_limit) > 0
            ? `本月已用 ${formatNumber(Number(m.month_makeups_used) || 0)} / ${formatNumber(Number(m.monthly_limit))} 次。`
            : '';
    rules.textContent =
        '规则：必须从昨天开始逐日向过去补；越早越贵。补救只延续连续火苗，不计入本月目标、奖池或 PK。';
    if (code === 'available') {
        status.textContent =
            `当前顺序目标是 ${target}（${targetLabel}），可消耗 ${formatNumber(cost)} XP 补救连续火苗；` +
            `${todayRule} ${limitText}`;
        btn.disabled = false;
    } else if (code === 'insufficient_xp') {
        status.textContent =
            `当前顺序目标是 ${target}（${targetLabel}），需要 ${formatNumber(cost)} XP；当前 ${formatNumber(currentXp)} XP。` +
            `先练习攒分后再补。`;
        btn.disabled = true;
    } else {
        status.textContent = m.reason || '当前不可补打卡。';
        btn.disabled = true;
    }
    btn.textContent = cost > 0 ? `用 ${formatNumber(cost)} XP 补 ${target}` : `用 XP 补 ${target}`;
}

function updateMakeupCheckinDialogFromProfile(g) {
    if (!makeupCheckinDialogOpen) return;
    renderMakeupCheckinDialog(g);
}

async function openMakeupCheckinDialog() {
    if (isParentSession) return;
    closeDailySummaryPopover();
    closeMobileMoreSheet();
    const modal = document.getElementById('makeup-checkin-modal');
    if (!modal) return;
    makeupCheckinDialogOpen = true;
    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');
    setMakeupEntrypointExpanded(true);
    if (lastGamificationProfile) {
        renderMakeupCheckinDialog(lastGamificationProfile);
    } else {
        renderMakeupCheckinDialog(null, { loading: true });
    }
    try {
        const g = await apiRequest('/gamification');
        lastGamificationProfile = g;
        updateGamificationNav(g);
        renderMakeupCheckinDialog(g);
    } catch (err) {
        renderMakeupCheckinDialog(null, { error: err.message || '补救状态加载失败' });
    }
}

function closeMakeupCheckinDialog() {
    const modal = document.getElementById('makeup-checkin-modal');
    if (!modal || !makeupCheckinDialogOpen) return;
    makeupCheckinDialogOpen = false;
    modal.style.display = 'none';
    modal.setAttribute('aria-hidden', 'true');
    setMakeupEntrypointExpanded(false);
}

async function buyMakeupCheckinFromDialog() {
    const offer = lastGamificationProfile && lastGamificationProfile.makeup_checkin;
    const cost = Number(offer && offer.cost_xp) || 0;
    if (!offer || offer.available !== true || cost <= 0) return;
    const btn = document.getElementById('makeup-checkin-buy');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '补救中…';
    }
    try {
        const res = await apiRequest('/gamification/makeup-checkin', {
            method: 'POST',
            body: '{}',
        });
        lastGamificationProfile = { ...(lastGamificationProfile || {}), ...res };
        updateGamificationNav(lastGamificationProfile);
        updateSettingsCheckinHintFromProfile(lastGamificationProfile);
        updateSettingsMonthlyGoalBonusNotice(lastGamificationProfile);
        renderMakeupCheckinDialog(lastGamificationProfile);
        const purchase = res.makeup_checkin_purchase || {};
        let msg = `已消耗 ${formatNumber(Number(purchase.cost_xp) || cost)} XP 补救连续打卡`;
        if (purchase.new_achievements && purchase.new_achievements.length) {
            msg += ` · 新成就：${purchase.new_achievements.map((x) => x.title).join('、')}`;
        }
        showMainBanner(msg);
        if (isSettingsOverlayOpen()) {
            await loadUserSettingsPanel();
        }
    } catch (err) {
        renderMakeupCheckinDialog(lastGamificationProfile, { error: err.message || '补打卡失败' });
    }
}

/** 月度打卡「额外奖励」说明（仅影响目标天数×30，不影响日常练习与其它积分） */
function updateSettingsMonthlyGoalBonusNotice(s) {
    const el = document.getElementById('settings-goal-bonus-hint');
    if (!el) return;
    const goal = s.monthly_checkin_goal;
    const per = Number(s.checkin_goal_xp_per_day) || 30;
    if (goal == null || goal === '') {
        el.hidden = true;
        el.textContent = '';
        return;
    }
    const g = Number(goal);
    const bonusXp = Number.isFinite(g) ? g * per : 0;
    el.hidden = false;
    if (s.monthly_goal_bonus_awarded_this_month) {
        el.textContent = `本月打卡目标额外奖励（${formatNumber(bonusXp)} XP）已发放。`;
    } else {
        el.textContent =
            `额外奖励：当月有效打卡满 ${g} 天后一次性获得 ${formatNumber(bonusXp)} XP（${g}×${per}）；若本月未达标则不发放该额外奖励；练习、奖池与 PK 等其它积分照常。`;
    }
}

function renderDuelRow(d, me) {
    const other = d.from_user === me ? d.target_user : d.from_user;
    const role = d.from_user === me ? '发起 →' : '← 收到';
    const id = String(d.id || '');
    let actions = '';
    if (d.status === 'pending' && d.target_user === me) {
        actions =
            '<span class="settings-duel-actions">' +
            `<button type="button" class="btn btn-primary settings-duel-btn" data-duel-id="${escapeHtml(id)}" data-duel-accept="1">接受</button> ` +
            `<button type="button" class="btn btn-secondary settings-duel-btn" data-duel-id="${escapeHtml(id)}" data-duel-accept="0">拒绝</button>` +
            '</span>';
    }
    let statusLabel = String(d.status || '');
    if (d.settled) {
        if (d.tie) statusLabel = '平局（已退回赌注）';
        else if (d.winner === me) statusLabel = '已结算 · 胜';
        else statusLabel = '已结算 · 负';
    } else if (d.status === 'active') {
        statusLabel = '进行中';
    } else if (d.status === 'declined') {
        statusLabel = '已拒绝';
    } else if (d.status === 'expired') {
        statusLabel = '已过期';
    } else if (d.status === 'pending') {
        statusLabel = '待处理';
    }
    const wager = Number(d.wager_xp) || 0;
    const monthBit =
        d.status === 'pending' ? '待接受' : d.month ? String(d.month) : '';
    const inviteExpiry =
        d.status === 'pending' && d.expires_at
            ? `<div class="settings-duel-pk-range">邀约有效期至 ${escapeHtml(
                  String(d.expires_at).replace('T', ' ').slice(0, 16),
              )}</div>`
            : '';
    const pkRange =
        d.pk_stats_start_date && d.pk_stats_end_date
            ? `<div class="settings-duel-pk-range">PK 计分区间：${escapeHtml(d.pk_stats_start_date)} ～ ${escapeHtml(
                  d.pk_stats_end_date,
              )}（双方同意次日）</div>`
            : '';
    const pkDays =
        d.status === 'active' && d.pk_checkin_days && typeof d.pk_checkin_days === 'object'
            ? (() => {
                  const a = d.from_user;
                  const b = d.target_user;
                  if (!a || !b) return '';
                  const na = Number(d.pk_checkin_days[a] ?? d.pk_checkin_days[String(a)]) || 0;
                  const nb = Number(d.pk_checkin_days[b] ?? d.pk_checkin_days[String(b)]) || 0;
                  return `<div class="settings-duel-pk-range">计分区间内有效打卡：${escapeHtml(
                      String(a),
                  )} ${na} 天 · ${escapeHtml(String(b))} ${nb} 天</div>`;
              })()
            : '';
    return `<li class="settings-duel-item">
    <span class="settings-duel-meta">${escapeHtml(role)} ${escapeHtml(other)} · ${wager} XP · ${escapeHtml(monthBit)}</span>
    <span class="settings-duel-status">${escapeHtml(statusLabel)}</span>
    ${inviteExpiry}
    ${pkRange}
    ${pkDays}
    ${actions}
  </li>`;
}

function getPendingIncomingDuels(duels) {
    if (!Array.isArray(duels) || !username) return [];
    return duels.filter((d) => d.status === 'pending' && d.target_user === username);
}

/** 进行中、尚未结算的 1v1 PK（用于顶栏 PK 角标与状态弹窗） */
function getPkOngoingDuels(duels) {
    if (!Array.isArray(duels) || !username) return [];
    return duels.filter((d) => d.status === 'active' && !d.settled);
}

/** 顶栏 PK 角标：待处理邀约 + 进行中（合并原 💬 与 PK 两处提示） */
function updatePkNavBadgesFromDuels(duels) {
    const pending = getPendingIncomingDuels(duels).length;
    const ongoing = getPkOngoingDuels(duels).length;
    const n = pending + ongoing;
    const pairs = [
        [document.getElementById('ng-pk-badge'), document.getElementById('ng-pk-active')],
        [document.getElementById('mobile-ng-pk-badge'), document.getElementById('mobile-ng-pk-active')],
    ];
    pairs.forEach(([badge, btn]) => {
        if (!btn) return;
        if (badge) {
            if (n > 0) {
                badge.hidden = false;
                badge.setAttribute('aria-hidden', 'false');
                badge.textContent = n > 9 ? '9+' : String(n);
            } else {
                badge.hidden = true;
                badge.setAttribute('aria-hidden', 'true');
                badge.textContent = '';
            }
        }
        const parts = [];
        if (ongoing > 0) parts.push(`${ongoing} 场进行中`);
        if (pending > 0) parts.push(`${pending} 条待处理邀约`);
        btn.setAttribute('aria-label', parts.length ? `PK：${parts.join('，')}` : '1v1 打卡 PK');
    });
}

async function refreshPkInviteIndicator() {
    if (!token || !username) {
        updatePkNavBadgesFromDuels([]);
        return;
    }
    if (isParentSession) {
        updatePkNavBadgesFromDuels([]);
        return;
    }
    if (pkInviteRefreshInFlight) return;
    pkInviteRefreshInFlight = true;
    try {
        const data = await apiRequest('/challenges');
        const list = data.challenges || [];
        updatePkNavBadgesFromDuels(list);
    } catch (_) {
        /* ignore */
    } finally {
        pkInviteRefreshInFlight = false;
    }
}

function stopPkInvitePolling() {
    if (pkInvitePollTimer != null) {
        clearInterval(pkInvitePollTimer);
        pkInvitePollTimer = null;
    }
}

function tickPkInvitePoll() {
    if (!token || !username) return;
    const main = document.getElementById('main-page');
    if (!main || !main.classList.contains('active')) return;
    if (document.hidden) return;
    void refreshPkInviteIndicator();
}

function startPkInvitePolling() {
    stopPkInvitePolling();
    if (!token || !username || isParentSession) return;
    pkInvitePollTimer = window.setInterval(tickPkInvitePoll, PK_INVITE_POLL_MS);
}

/** 将挑战分为四组：待处理 → 进行中 → 已完成 → 已拒绝（顺序由渲染侧保证） */
function partitionDuelsForPkHub(duels) {
    const pending = [];
    const active = [];
    const done = [];
    const declined = [];
    if (!Array.isArray(duels)) return { pending, active, done, declined };
    for (const d of duels) {
        const st = d.status;
        if (st === 'pending') {
            pending.push(d);
        } else if (st === 'declined') {
            declined.push(d);
        } else if (d.settled === true || st === 'expired') {
            done.push(d);
        } else if (st === 'active') {
            active.push(d);
        } else {
            done.push(d);
        }
    }
    return { pending, active, done, declined };
}

function renderPkHubChallengesGroupedHtml(duels, me) {
    const groups = partitionDuelsForPkHub(duels);
    const order = [
        { key: 'pending', title: '待处理' },
        { key: 'active', title: '进行中' },
        { key: 'done', title: '已完成' },
        { key: 'declined', title: '已拒绝' },
    ];
    let html = '';
    for (const { key, title } of order) {
        const items = groups[key];
        const listContent = items.length
            ? items.map((d) => renderDuelRow(d, me)).join('')
            : '<li class="settings-duel-empty">暂无</li>';
        html += `<div class="pk-hub-duel-group" data-pk-group="${escapeHtml(key)}">
    <h5 class="pk-hub-duel-group-title">${escapeHtml(title)}</h5>
    <ul class="settings-duel-list pk-hub-duel-sublist">${listContent}</ul>
  </div>`;
    }
    return html;
}

function populatePkHubFromProfile(s) {
    const sel = document.getElementById('pk-hub-duel-wager');
    if (sel && Array.isArray(s.wager_tiers) && s.wager_tiers.length) {
        const tiers = s.wager_tiers.map(Number);
        const preferred = tiers.includes(100) ? 100 : tiers[0];
        sel.innerHTML = tiers
            .map((w) => {
                const n = Number(w);
                const label = n === 0 ? '无赌注' : `${n} XP`;
                const optSel = n === preferred ? ' selected' : '';
                return `<option value="${n}"${optSel}>${label}</option>`;
            })
            .join('');
    }

    const root = document.getElementById('pk-hub-challenges-root');
    if (root) {
        root.innerHTML = Array.isArray(s.duels)
            ? renderPkHubChallengesGroupedHtml(s.duels, username)
            : renderPkHubChallengesGroupedHtml([], username);
    }

    const duelSel = document.getElementById('pk-hub-duel-target-select');
    if (duelSel && Array.isArray(s.duel_opponents)) {
        const prev = duelSel.value;
        duelSel.innerHTML =
            '<option value="">选择对手</option>' +
            s.duel_opponents.map((u) => `<option value="${escapeHtml(u)}">${escapeHtml(u)}</option>`).join('');
        if (prev && s.duel_opponents.includes(prev)) duelSel.value = prev;
    }
}

function openPkHubModal() {
    const modal = document.getElementById('pk-hub-modal');
    if (modal) {
        modal.style.display = 'flex';
        modal.setAttribute('aria-hidden', 'false');
    }
}

function closePkHubModal() {
    const modal = document.getElementById('pk-hub-modal');
    if (modal) {
        modal.style.display = 'none';
        modal.setAttribute('aria-hidden', 'true');
    }
    const err = document.getElementById('pk-hub-error');
    if (err) {
        err.style.display = 'none';
        err.textContent = '';
    }
}

async function loadPkHubModalBody() {
    const loading = document.getElementById('pk-hub-loading');
    const content = document.getElementById('pk-hub-content');
    const errEl = document.getElementById('pk-hub-error');
    if (loading) loading.hidden = false;
    if (content) content.hidden = true;
    if (errEl) {
        errEl.style.display = 'none';
        errEl.textContent = '';
    }
    try {
        const s = await apiRequest('/user/settings');
        populatePkHubFromProfile(s);
        updatePkNavBadgesFromDuels(s.duels);
        if (loading) loading.hidden = true;
        if (content) content.hidden = false;
    } catch (e) {
        if (loading) loading.hidden = true;
        if (content) content.hidden = true;
        if (errEl) {
            errEl.textContent = e.message || '加载失败';
            errEl.style.display = 'block';
        }
    }
}

function renderUserSettingsPanel(s) {
    setSettingsMessage('');
    const prev = document.getElementById('settings-avatar-preview');
    const ph = document.getElementById('settings-avatar-placeholder');
    if (prev && ph) {
        if (s.avatar_url) {
            prev.src = `${avatarDisplayUrl(s.avatar_url, 128)}&t=${Date.now()}`;
            prev.hidden = false;
            ph.hidden = true;
        } else {
            prev.removeAttribute('src');
            prev.hidden = true;
            ph.hidden = false;
        }
    }
    updateNavUserAvatar(s.avatar_url);
    updateSettingsCheckinHintFromProfile(s);
    updateMakeupCheckinDialogFromProfile(s);
    updateSettingsMonthlyGoalBonusNotice(s);
    const dim = Number(s.month_days_in_month) || 31;
    const suggested = Math.min(
        dim,
        Math.max(1, Number(s.monthly_checkin_goal_suggested_days) || dim)
    );
    const canEdit = s.monthly_checkin_goal_can_edit !== false;
    const input = document.getElementById('settings-month-goal');
    const lockHint = document.getElementById('settings-month-goal-lock-hint');
    const saveGoalBtn = document.getElementById('settings-month-goal-save');
    if (input) {
        input.max = String(dim);
        input.placeholder = `默认 ${suggested}（今日至月末共 ${suggested} 天）`;
        input.disabled = !canEdit;
        const hasGoal =
            s.monthly_checkin_goal != null && s.monthly_checkin_goal !== '';
        if (hasGoal) {
            input.value = String(s.monthly_checkin_goal);
        } else if (canEdit) {
            input.value = String(suggested);
        } else {
            input.value = '';
        }
    }
    if (lockHint) {
        if (!canEdit) {
            lockHint.hidden = false;
            lockHint.textContent = '本月打卡目标已修改过，每月仅可改一次，下月再调整。';
        } else {
            lockHint.hidden = true;
            lockHint.textContent = '';
        }
    }
    if (saveGoalBtn) {
        saveGoalBtn.disabled = !canEdit;
    }
    const days = Number(s.month_valid_checkin_days) || 0;
    const goal = s.monthly_checkin_goal;
    const fill = document.getElementById('settings-month-goal-fill');
    const text = document.getElementById('settings-month-goal-text');
    if (goal != null && goal !== '') {
        const g = Number(goal);
        const pct = g > 0 ? Math.min(100, Math.round((days / g) * 100)) : 0;
        if (fill) fill.style.width = `${pct}%`;
        if (text) text.textContent = `本月有效打卡 ${days} / ${g} 天`;
    } else {
        if (fill) fill.style.width = '0%';
        if (text) text.textContent = `本月有效打卡 ${days} 天（未设置目标）`;
    }

    const pool = s.monthly_pool || {};
    const poolHint = document.getElementById('settings-pool-hint');
    const joinBtn = document.getElementById('settings-pool-join');
    if (poolHint) {
        const prepD = pool.preparation_last_day || 5;
        const stD = pool.competition_start_day || 6;
        let t = `${pool.month || ''} 奖池共 ${formatNumber(pool.pool_xp || 0)} XP，${pool.participant_count || 0} 人参与。`;
        if (pool.joined) t += ' 你已加入。';
        if (pool.preparation_phase || pool.phase === 'preparation') {
            t += ` 当前为准备期（1～${prepD} 日可报名）；${stD} 日起比赛正式开始。`;
        } else {
            t += ` 比赛进行中（赛跑进度仅计 ${stD} 日及以后有效打卡）。`;
        }
        if (!pool.join_window_open) {
            t += ` 报名窗口为每月 1～${pool.join_window_last_day || 5} 日。`;
        }
        poolHint.textContent = t;
    }
    if (joinBtn) {
        joinBtn.disabled = !!pool.joined || !pool.join_window_open;
        joinBtn.textContent = pool.joined ? '已加入本月奖池' : `加入奖池（${pool.fee_xp || 150} XP）`;
    }

    updatePkNavBadgesFromDuels(s.duels);
    renderInviteSettingsPanel(s);
}

const AVATAR_CROP_VIEW = 280;
let currentNavAvatarUrl = '';
let avatarViewPreviouslyFocused = null;
let avatarCrop = {
    objectUrl: null,
    iw: 0,
    ih: 0,
    scale: 1,
    imgX: 0,
    imgY: 0,
    coverScale: 1,
    dragging: false,
    dragStartX: 0,
    dragStartY: 0,
    startImgX: 0,
    startImgY: 0,
};

/** 列表/缩略图用较小尺寸，减轻传输 */
function avatarDisplayUrl(url, w) {
    if (!url) return '';
    const sep = url.indexOf('?') >= 0 ? '&' : '?';
    return `${url}${sep}w=${w}`;
}

function updateNavUserAvatar(avatarUrl) {
    currentNavAvatarUrl = avatarUrl || '';
    const img = document.getElementById('nav-user-avatar-img');
    const ph = document.getElementById('nav-user-avatar-ph');
    if (!img || !ph) return;
    if (avatarUrl) {
        img.src = `${avatarDisplayUrl(avatarUrl, 64)}&t=${Date.now()}`;
        img.hidden = false;
        ph.hidden = true;
    } else {
        img.removeAttribute('src');
        img.hidden = true;
        ph.hidden = false;
    }
}

function avatarOriginalDisplayUrl(url) {
    if (!url) return '';
    const sep = url.indexOf('?') >= 0 ? '&' : '?';
    return `${url}${sep}t=${Date.now()}`;
}

function closeAvatarViewModal() {
    const modal = document.getElementById('avatar-view-modal');
    const trigger = document.getElementById('nav-user-avatar-wrap');
    const img = document.getElementById('avatar-view-image');
    if (!modal || modal.style.display === 'none') return;
    modal.style.display = 'none';
    modal.setAttribute('aria-hidden', 'true');
    trigger?.setAttribute('aria-expanded', 'false');
    if (img) {
        img.onload = null;
        img.onerror = null;
        img.removeAttribute('src');
    }
    if (avatarViewPreviouslyFocused instanceof HTMLElement) {
        avatarViewPreviouslyFocused.focus();
    }
    avatarViewPreviouslyFocused = null;
}

function openAvatarViewModal() {
    const modal = document.getElementById('avatar-view-modal');
    const trigger = document.getElementById('nav-user-avatar-wrap');
    const title = document.getElementById('avatar-view-title');
    const img = document.getElementById('avatar-view-image');
    const placeholder = document.getElementById('avatar-view-placeholder');
    const actions = document.getElementById('avatar-view-actions');
    const input = document.getElementById('avatar-view-replace-input');
    const message = document.getElementById('avatar-view-message');
    const closeButton = document.getElementById('avatar-view-close');
    if (!modal || !img || !placeholder) return;

    avatarViewPreviouslyFocused = document.activeElement;
    if (title) {
        title.textContent = isParentSession && childUsername ? `${childUsername} 的头像` : '我的头像';
    }
    if (actions) actions.hidden = isParentSession;
    if (input) {
        input.disabled = isParentSession;
        input.value = '';
    }
    if (message) {
        message.textContent = isParentSession ? '家长查看模式不能替换学生头像' : '';
    }

    img.hidden = true;
    placeholder.hidden = false;
    const originalUrl = avatarOriginalDisplayUrl(currentNavAvatarUrl);
    if (originalUrl) {
        img.onload = () => {
            img.hidden = false;
            placeholder.hidden = true;
            img.onload = null;
            img.onerror = null;
        };
        img.onerror = () => {
            img.hidden = true;
            placeholder.hidden = false;
            img.onload = null;
            img.onerror = null;
        };
        img.src = originalUrl;
    } else {
        img.removeAttribute('src');
    }

    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');
    trigger?.setAttribute('aria-expanded', 'true');
    closeButton?.focus();
}

async function refreshNavUserAvatar() {
    if (!token || !username) {
        updateNavUserAvatar(null);
        return;
    }
    try {
        const s = await apiRequest('/user/avatar-meta');
        updateNavUserAvatar(s && s.avatar_url);
    } catch (_) {
        updateNavUserAvatar(null);
    }
}

let currentInviteCard = {
    code: '',
    registerUrl: '',
    imageUrl: '',
    blob: null,
    file: null,
};
const INVITE_REGISTRATION_SESSION_KEY = 'pending_invite_registration_code';
let pendingInviteRegistrationCode = '';
let inviteCreateInFlight = false;
let inviteUnusedPayload = { invites: [] };
const inviteUnusedById = new Map();
let inviteSessionGeneration = 0;
let inviteModalGeneration = 0;

function normalizeInviteCodeForLink(raw) {
    const code = String(raw || '').trim();
    return /^[A-Za-z0-9_-]{4,64}$/.test(code) ? code : '';
}

function persistInviteRegistrationState(code) {
    pendingInviteRegistrationCode = normalizeInviteCodeForLink(code);
    try {
        if (pendingInviteRegistrationCode) {
            sessionStorage.setItem(INVITE_REGISTRATION_SESSION_KEY, pendingInviteRegistrationCode);
        } else {
            sessionStorage.removeItem(INVITE_REGISTRATION_SESSION_KEY);
        }
    } catch (_) {
        /* 受限浏览器中仍保留当前页面内的预填状态。 */
    }
}

function restoreInviteRegistrationState() {
    try {
        persistInviteRegistrationState(sessionStorage.getItem(INVITE_REGISTRATION_SESSION_KEY));
    } catch (_) {
        /* ignore */
    }
    return pendingInviteRegistrationCode;
}

function buildInviteRegistrationLink(baseUrl, rawCode) {
    const code = normalizeInviteCodeForLink(rawCode);
    if (!code) return '';
    let url;
    try {
        url = new URL(baseUrl || '/', window.location.origin);
    } catch (_) {
        url = new URL('/', window.location.origin);
    }
    if (url.protocol !== 'http:' && url.protocol !== 'https:') {
        url = new URL('/', window.location.origin);
    }
    const hashParams = new URLSearchParams(url.hash.replace(/^#/, ''));
    hashParams.delete('invite_code');
    hashParams.set('invite', code);
    url.hash = hashParams.toString();
    return url.toString();
}

function inviteWebsiteUrl(baseUrl) {
    let url;
    try {
        url = new URL(baseUrl || '/', window.location.origin);
    } catch (_) {
        url = new URL('/', window.location.origin);
    }
    if (url.protocol !== 'http:' && url.protocol !== 'https:') {
        url = new URL('/', window.location.origin);
    }
    url.search = '';
    url.hash = '';
    return url.toString();
}

function captureInviteRegistrationFromUrl() {
    let url;
    try {
        url = new URL(window.location.href);
    } catch (_) {
        return '';
    }

    const hashParams = new URLSearchParams(url.hash.replace(/^#/, ''));
    const rawCode = [
        hashParams.get('invite'),
        hashParams.get('invite_code'),
        url.searchParams.get('invite'),
        url.searchParams.get('invite_code'),
    ].find((candidate) => normalizeInviteCodeForLink(candidate)) || '';
    const hasInviteParam = hashParams.has('invite') || hashParams.has('invite_code') ||
        url.searchParams.has('invite') || url.searchParams.has('invite_code');
    if (!hasInviteParam) return '';

    hashParams.delete('invite');
    hashParams.delete('invite_code');
    url.searchParams.delete('invite');
    url.searchParams.delete('invite_code');
    url.hash = hashParams.toString();
    const normalizedCode = normalizeInviteCodeForLink(rawCode);
    if (normalizedCode) {
        persistInviteRegistrationState(normalizedCode);
    } else {
        clearInviteRegistrationState();
    }
    try {
        window.history.replaceState(
            window.history.state,
            document.title,
            `${url.pathname}${url.search}${url.hash}`
        );
    } catch (_) {
        /* 地址栏清理失败不应阻断注册预填。 */
    }
    return normalizedCode;
}

function clearInviteRegistrationState() {
    persistInviteRegistrationState('');
    const inviteInput = document.getElementById('reg-invite');
    if (inviteInput) inviteInput.value = '';
    if (document.getElementById('login-form')) activateAuthTab('login');
}

function currentInviteFlow() {
    return {
        sessionGeneration: inviteSessionGeneration,
        modalGeneration: inviteModalGeneration,
        token,
    };
}

function inviteSessionIsCurrent(flow) {
    return !!flow &&
        !!token &&
        flow.token === token &&
        flow.sessionGeneration === inviteSessionGeneration;
}

function inviteFlowIsCurrent(flow) {
    return inviteSessionIsCurrent(flow) && flow.modalGeneration === inviteModalGeneration;
}

function renderInviteSettingsPanel(s) {
    const hint = document.getElementById('settings-invite-hint');
    const btn = document.getElementById('settings-invite-create');
    const latest = document.getElementById('settings-invite-latest');
    const history = document.getElementById('settings-invite-history');
    if (!hint || !btn) return;
    const limit = Math.max(0, Number(s.invite_quota_limit) || 0);
    const used = Math.max(0, Number(s.invite_quota_used) || 0);
    const remaining = Math.max(0, Number(s.invite_quota_remaining) || 0);
    const invites = Array.isArray(s.invites) ? s.invites : [];
    const hasUnused = invites.some((inv) => inv && inv.status === 'unused' && inv.selectable);
    hint.textContent = `已生成 ${formatNumber(used)} / ${formatNumber(limit)} 个邀请码，剩余 ${formatNumber(remaining)} 个。`;
    btn.disabled = inviteCreateInFlight || (remaining <= 0 && !hasUnused);
    btn.textContent = hasUnused ? '打开邀请图片' : remaining > 0 ? '生成邀请图片' : '邀请次数已用完';
    if (latest && !latest.dataset.hasInvite) {
        latest.hidden = true;
        latest.innerHTML = '';
    }
    if (history) {
        if (!invites.length) {
            history.innerHTML = '<p class="settings-hint settings-invite-empty">还没有生成过邀请码。</p>';
        } else {
            const rows = invites.slice(0, 5).map((inv) => {
                const status = inv.status === 'used'
                    ? `已被 ${escapeHtml(inv.used_by || '用户')} 使用`
                    : inv.selectable === false ? '未使用（需原图片）' : '未使用';
                return `<div class="settings-invite-row"><span>${escapeHtml(inv.created_at || '—')}</span><strong>${status}</strong></div>`;
            }).join('');
            history.innerHTML = rows;
        }
    }
}

function updateInviteQuotaInSettings(data) {
    const hint = document.getElementById('settings-invite-hint');
    const btn = document.getElementById('settings-invite-create');
    if (!hint || !btn || !data) return;
    const limit = Math.max(0, Number(data.invite_quota_limit) || 0);
    const used = Math.max(0, Number(data.invite_quota_used) || 0);
    const remaining = Math.max(0, Number(data.invite_quota_remaining) || 0);
    hint.textContent = `已生成 ${formatNumber(used)} / ${formatNumber(limit)} 个邀请码，剩余 ${formatNumber(remaining)} 个。`;
    btn.disabled = false;
    btn.textContent = '打开邀请图片';
}

function formatInviteCreatedAt(raw) {
    const value = String(raw || '').trim();
    if (!value) return '创建时间未知';
    return value.replace('T', ' ').replace(/\+08:00$/, '');
}

function syncInviteSettingsButton(payload, fallback = {}) {
    const btn = document.getElementById('settings-invite-create');
    if (!btn) return;
    const hasQuota = payload && Object.prototype.hasOwnProperty.call(payload, 'invite_quota_remaining');
    if (!hasQuota) {
        btn.disabled = !!fallback.disabled;
        btn.textContent = fallback.text || '打开邀请图片';
        return;
    }
    const rows = Array.isArray(payload.invites) ? payload.invites : [];
    const hasSelectable = !!currentInviteCard.code ||
        rows.some((row) => row && row.selectable && row.invite_code);
    const remaining = Math.max(0, Number(payload.invite_quota_remaining) || 0);
    btn.disabled = inviteCreateInFlight || (remaining <= 0 && !hasSelectable);
    btn.textContent = hasSelectable
        ? '打开邀请图片'
        : remaining > 0 ? '生成邀请图片' : '邀请次数已用完';
}

function syncInviteModalActions() {
    const unavailable = inviteCreateInFlight || !currentInviteCard.code || !currentInviteCard.imageUrl;
    const copy = document.getElementById('invite-card-copy');
    const share = document.getElementById('invite-card-share');
    const download = document.getElementById('invite-card-download');
    const modal = document.getElementById('invite-card-modal');
    if (copy) copy.disabled = unavailable;
    if (share) share.disabled = unavailable;
    if (download) {
        download.setAttribute('aria-disabled', unavailable ? 'true' : 'false');
        download.tabIndex = unavailable ? -1 : 0;
    }
    if (modal) modal.setAttribute('aria-busy', inviteCreateInFlight ? 'true' : 'false');
}

function renderInviteUnusedList(payload, statusMessage = '') {
    const list = document.getElementById('invite-card-unused-list');
    const count = document.getElementById('invite-card-unused-count');
    const status = document.getElementById('invite-card-unused-status');
    const createBtn = document.getElementById('invite-card-create-new');
    if (!list || !count || !status) return;

    inviteUnusedPayload = payload && typeof payload === 'object' ? payload : { invites: [] };
    const rows = Array.isArray(inviteUnusedPayload.invites)
        ? inviteUnusedPayload.invites.filter((row) => row && row.selectable && row.invite_code)
        : [];
    inviteUnusedById.clear();
    list.replaceChildren();

    rows.forEach((row, index) => {
        const id = String(row.id || `invite-${index}`);
        inviteUnusedById.set(id, row);
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'invite-card-unused-item';
        button.dataset.inviteId = id;
        button.disabled = inviteCreateInFlight;
        const isCurrent = !!row.invite_code && row.invite_code === currentInviteCard.code;
        button.setAttribute('aria-pressed', isCurrent ? 'true' : 'false');
        button.setAttribute('aria-label', `选择邀请码 ${row.invite_code}`);

        const code = document.createElement('strong');
        code.className = 'invite-card-unused-code';
        code.textContent = String(row.invite_code);
        const created = document.createElement('span');
        created.className = 'invite-card-unused-time';
        created.textContent = formatInviteCreatedAt(row.created_at);
        const mark = document.createElement('em');
        mark.className = 'invite-card-unused-mark';
        mark.textContent = isCurrent ? '当前' : '选择';
        button.append(code, created, mark);
        list.appendChild(button);
    });

    count.textContent = rows.length > 0 ? `${rows.length} 个可用` : '暂无可用记录';
    if (statusMessage) {
        status.textContent = statusMessage;
    } else if (!rows.length) {
        status.textContent = '暂无可选择的历史邀请码。';
    } else {
        status.textContent = '';
    }

    if (createBtn) {
        const remaining = Math.max(0, Number(inviteUnusedPayload.invite_quota_remaining) || 0);
        createBtn.disabled = inviteCreateInFlight || remaining <= 0;
        createBtn.textContent = remaining > 0 ? `生成新邀请码（剩余 ${remaining}）` : '生成额度已用完';
    }
    syncInviteModalActions();
}

async function loadInviteUnusedList(flow = currentInviteFlow()) {
    const status = document.getElementById('invite-card-unused-status');
    if (status) status.textContent = '正在加载未使用邀请码…';
    const data = await apiRequest('/user/invites/unused');
    if (!inviteFlowIsCurrent(flow)) return null;
    renderInviteUnusedList(data);
    return data;
}

function roundRectPath(ctx, x, y, w, h, r) {
    const radius = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.arcTo(x + w, y, x + w, y + h, radius);
    ctx.arcTo(x + w, y + h, x, y + h, radius);
    ctx.arcTo(x, y + h, x, y, radius);
    ctx.arcTo(x, y, x + w, y, radius);
    ctx.closePath();
}

function loadImageForCanvas(src) {
    return new Promise((resolve) => {
        if (!src) {
            resolve(null);
            return;
        }
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => resolve(null);
        img.src = src;
    });
}

function drawCenteredText(ctx, text, x, y, maxWidth, lineHeight, maxLines) {
    const source = String(text || '');
    const chars = Array.from(source);
    const lines = [];
    let line = '';
    chars.forEach((ch) => {
        const next = line + ch;
        if (ctx.measureText(next).width > maxWidth && line) {
            lines.push(line);
            line = ch;
        } else {
            line = next;
        }
    });
    if (line) lines.push(line);
    const visible = lines.slice(0, maxLines);
    if (lines.length > maxLines && visible.length) {
        let last = visible[visible.length - 1];
        while (last.length > 1 && ctx.measureText(`${last}…`).width > maxWidth) {
            last = last.slice(0, -1);
        }
        visible[visible.length - 1] = `${last}…`;
    }
    visible.forEach((ln, idx) => {
        ctx.fillText(ln, x, y + idx * lineHeight);
    });
}

async function buildInviteCardImage({ code, registerUrl, inviterName, avatarUrl }) {
    const w = 900;
    const h = 1200;
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#f6fbf3';
    ctx.fillRect(0, 0, w, h);

    const bg = ctx.createLinearGradient(0, 0, w, h);
    bg.addColorStop(0, '#e8f8ff');
    bg.addColorStop(0.52, '#f7ffe9');
    bg.addColorStop(1, '#fff4d8');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, w, h);

    ctx.fillStyle = 'rgba(255, 255, 255, 0.92)';
    roundRectPath(ctx, 70, 70, 760, 1060, 48);
    ctx.fill();
    ctx.strokeStyle = '#58cc02';
    ctx.lineWidth = 8;
    ctx.stroke();

    ctx.fillStyle = '#3c3c3c';
    ctx.textAlign = 'center';
    ctx.font = '800 64px "Plus Jakarta Sans", "Segoe UI", sans-serif';
    ctx.fillText('智能英语背诵', w / 2, 190);
    ctx.font = '700 32px "Plus Jakarta Sans", "Segoe UI", sans-serif';
    ctx.fillStyle = '#46a302';
    ctx.fillText('每天一点点，单词变简单', w / 2, 242);

    const avatar = await loadImageForCanvas(avatarUrl ? `${avatarDisplayUrl(avatarUrl, 256)}&t=${Date.now()}` : '');
    const ax = w / 2;
    const ay = 382;
    const ar = 92;
    ctx.save();
    ctx.beginPath();
    ctx.arc(ax, ay, ar, 0, Math.PI * 2);
    ctx.closePath();
    ctx.clip();
    if (avatar) {
        const side = Math.min(avatar.naturalWidth || avatar.width, avatar.naturalHeight || avatar.height);
        const sx = ((avatar.naturalWidth || avatar.width) - side) / 2;
        const sy = ((avatar.naturalHeight || avatar.height) - side) / 2;
        ctx.drawImage(avatar, sx, sy, side, side, ax - ar, ay - ar, ar * 2, ar * 2);
    } else {
        ctx.fillStyle = '#e4ffce';
        ctx.fillRect(ax - ar, ay - ar, ar * 2, ar * 2);
        ctx.font = '900 78px "Segoe UI Emoji", sans-serif';
        ctx.fillStyle = '#46a302';
        ctx.fillText('👤', ax, ay + 28);
    }
    ctx.restore();
    ctx.strokeStyle = '#58cc02';
    ctx.lineWidth = 8;
    ctx.beginPath();
    ctx.arc(ax, ay, ar, 0, Math.PI * 2);
    ctx.stroke();

    ctx.fillStyle = '#3c3c3c';
    ctx.font = '800 38px "Plus Jakarta Sans", "Segoe UI", sans-serif';
    drawCenteredText(ctx, `${inviterName || '好友'} 邀请你一起背单词`, w / 2, 530, 680, 48, 2);

    ctx.fillStyle = '#777777';
    ctx.font = '700 27px "Plus Jakarta Sans", "Segoe UI", sans-serif';
    drawCenteredText(ctx, '注册时填写下方邀请码，开启你的英语复习计划', w / 2, 635, 660, 38, 2);

    ctx.fillStyle = '#fff9e6';
    roundRectPath(ctx, 165, 710, 570, 160, 32);
    ctx.fill();
    ctx.strokeStyle = '#ffc800';
    ctx.lineWidth = 5;
    ctx.stroke();
    ctx.fillStyle = '#777777';
    ctx.font = '800 26px "Plus Jakarta Sans", "Segoe UI", sans-serif';
    ctx.fillText('邀请码', w / 2, 765);
    ctx.fillStyle = '#2f7f02';
    ctx.font = '900 64px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
    ctx.fillText(String(code || ''), w / 2, 835);

    ctx.fillStyle = '#f0f9ff';
    roundRectPath(ctx, 165, 925, 570, 92, 28);
    ctx.fill();
    ctx.strokeStyle = '#1cb0f6';
    ctx.lineWidth = 4;
    ctx.stroke();
    ctx.fillStyle = '#1899d6';
    ctx.font = '800 26px "Plus Jakarta Sans", "Segoe UI", sans-serif';
    ctx.fillText('打开网站注册', w / 2, 962);
    ctx.font = '700 22px "Plus Jakarta Sans", "Segoe UI", sans-serif';
    drawCenteredText(ctx, registerUrl || window.location.origin, w / 2, 996, 520, 28, 1);

    ctx.fillStyle = '#777777';
    ctx.font = '700 22px "Plus Jakarta Sans", "Segoe UI", sans-serif';
    ctx.fillText('邀请码仅可使用一次', w / 2, 1080);

    const blob = await new Promise((resolve) => canvas.toBlob((b) => resolve(b), 'image/png'));
    if (!blob) throw new Error('无法生成邀请图片');
    return {
        blob,
        url: URL.createObjectURL(blob),
    };
}

async function selectInviteCodeForCard({ code, registerUrl }, flow = currentInviteFlow()) {
    if (!inviteFlowIsCurrent(flow)) return null;
    const normalizedCode = normalizeInviteCodeForLink(code);
    if (!normalizedCode) throw new Error('邀请码不可用');
    const studentName = sessionStudentUsername();
    const avatarUrl = studentName ? `/api/user/avatar/${encodeURIComponent(studentName)}` : null;
    const targetUrl = buildInviteRegistrationLink(registerUrl, normalizedCode);
    const card = await buildInviteCardImage({
        code: normalizedCode,
        registerUrl: inviteWebsiteUrl(registerUrl),
        inviterName: studentName,
        avatarUrl,
    });
    if (!inviteFlowIsCurrent(flow)) {
        URL.revokeObjectURL(card.url);
        return null;
    }
    let file = null;
    try {
        file = new File([card.blob], `invite-${normalizedCode}.png`, { type: 'image/png' });
    } catch (_) {
        file = null;
    }
    const previousUrl = currentInviteCard.imageUrl;
    currentInviteCard = {
        code: normalizedCode,
        registerUrl: targetUrl,
        imageUrl: card.url,
        blob: card.blob,
        file,
    };
    if (previousUrl) URL.revokeObjectURL(previousUrl);
    showInviteCardModal(currentInviteCard);
    renderInviteUnusedList(inviteUnusedPayload);
    return currentInviteCard;
}

function clearCurrentInviteCard() {
    if (currentInviteCard.imageUrl) URL.revokeObjectURL(currentInviteCard.imageUrl);
    currentInviteCard = {
        code: '',
        registerUrl: '',
        imageUrl: '',
        blob: null,
        file: null,
    };
    const img = document.getElementById('invite-card-preview');
    const download = document.getElementById('invite-card-download');
    if (img) {
        img.removeAttribute('src');
        img.hidden = true;
    }
    if (download) {
        download.removeAttribute('href');
        download.download = 'invite.png';
    }
    syncInviteModalActions();
}

function resetInviteCardState() {
    inviteSessionGeneration += 1;
    inviteModalGeneration += 1;
    inviteCreateInFlight = false;
    clearCurrentInviteCard();
    inviteUnusedPayload = { invites: [] };
    inviteUnusedById.clear();
    const modal = document.getElementById('invite-card-modal');
    const list = document.getElementById('invite-card-unused-list');
    const count = document.getElementById('invite-card-unused-count');
    const status = document.getElementById('invite-card-unused-status');
    const message = document.getElementById('invite-card-message');
    const latest = document.getElementById('settings-invite-latest');
    if (modal) {
        modal.style.display = 'none';
        modal.setAttribute('aria-hidden', 'true');
        modal.setAttribute('aria-busy', 'false');
    }
    if (list) list.replaceChildren();
    if (count) count.textContent = '';
    if (status) status.textContent = '';
    if (message) message.textContent = '';
    if (latest) {
        latest.hidden = true;
        latest.innerHTML = '';
        delete latest.dataset.hasInvite;
    }
}

function closeInviteCardModal() {
    inviteModalGeneration += 1;
    const modal = document.getElementById('invite-card-modal');
    if (modal) {
        modal.style.display = 'none';
        modal.setAttribute('aria-hidden', 'true');
    }
    const msg = document.getElementById('invite-card-message');
    if (msg) msg.textContent = '';
}

function showInviteCardModal(payload) {
    const modal = document.getElementById('invite-card-modal');
    const dialog = modal?.querySelector('.invite-card-dialog');
    const img = document.getElementById('invite-card-preview');
    const dl = document.getElementById('invite-card-download');
    const msg = document.getElementById('invite-card-message');
    if (!modal || !img || !dl) return;
    if (payload && payload.imageUrl) {
        img.hidden = false;
        img.src = payload.imageUrl;
        dl.href = payload.imageUrl;
        dl.download = `invite-${payload.code || 'code'}.png`;
    } else {
        img.hidden = true;
        img.removeAttribute('src');
        dl.removeAttribute('href');
        dl.download = 'invite.png';
    }
    if (msg) msg.textContent = '';
    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');
    if (dialog) dialog.scrollTop = 0;
    syncInviteModalActions();
}

function renderLatestInviteInSettings(data) {
    const latest = document.getElementById('settings-invite-latest');
    if (!latest || !data || !data.invite_code) return;
    latest.dataset.hasInvite = '1';
    latest.hidden = false;
    latest.innerHTML =
        `<div class="settings-invite-code-line"><span>新邀请码</span><strong>${escapeHtml(data.invite_code)}</strong></div>` +
        `<p class="settings-hint">${escapeHtml(data.hint || '未使用前可在邀请窗口中再次选择。')}</p>`;
}

function refreshInviteSettingsAfterCreate(data, flow) {
    apiRequest('/user/invites')
        .then((fresh) => {
            if (!inviteFlowIsCurrent(flow)) return;
            const history = document.getElementById('settings-invite-history');
            if (!history) return;
            renderInviteSettingsPanel({
                ...fresh,
                invite_quota_limit: data.invite_quota_limit,
                invite_quota_used: data.invite_quota_used,
                invite_quota_remaining: data.invite_quota_remaining,
            });
            renderLatestInviteInSettings(data);
        })
        .catch(() => {});
}

async function createNewInviteCard(flow = currentInviteFlow()) {
    if (!inviteFlowIsCurrent(flow)) return null;
    const data = await apiRequest('/user/invites', {
        method: 'POST',
        body: '{}',
    });
    if (!inviteFlowIsCurrent(flow)) return null;
    const selected = await selectInviteCodeForCard({
        code: data.invite_code,
        registerUrl: data.invite_register_url || window.location.origin,
    }, flow);
    if (!selected || !inviteFlowIsCurrent(flow)) return null;
    renderLatestInviteInSettings(data);
    updateInviteQuotaInSettings(data);
    setSettingsMessage('新邀请码和邀请图片已生成');
    refreshInviteSettingsAfterCreate(data, flow);
    try {
        await loadInviteUnusedList(flow);
    } catch (_) {
        if (inviteFlowIsCurrent(flow)) {
            renderInviteUnusedList(inviteUnusedPayload, '邀请码列表刷新失败，请稍后重试。');
        }
    }
    return data;
}

async function createInviteCardFromSettings() {
    if (inviteCreateInFlight) return;
    const btn = document.getElementById('settings-invite-create');
    const prevText = btn ? btn.textContent : '';
    const prevDisabled = btn ? btn.disabled : false;
    closeMobileMoreSheet();
    if (isParentSession) {
        showMainBanner('家长账户不能生成邀请码');
        return;
    }
    if (btn) {
        btn.disabled = true;
        btn.textContent = '打开中…';
    }
    const flow = currentInviteFlow();
    inviteCreateInFlight = true;
    let finalStatus = '';
    setSettingsMessage('');
    showInviteCardModal(currentInviteCard);
    renderInviteUnusedList(inviteUnusedPayload, '正在加载未使用邀请码…');
    try {
        const data = await loadInviteUnusedList(flow);
        if (!data || !inviteFlowIsCurrent(flow)) return;
        const rows = Array.isArray(data.invites) ? data.invites : [];
        const reusable =
            rows.find((row) => row.invite_code === currentInviteCard.code && row.selectable) ||
            rows.find((row) => row && row.selectable && row.invite_code);
        if (reusable) {
            const selected = await selectInviteCodeForCard({
                code: reusable.invite_code,
                registerUrl: data.invite_register_url || window.location.origin,
            }, flow);
            if (!selected || !inviteFlowIsCurrent(flow)) return;
            setSettingsMessage('已打开未使用的邀请码');
        } else if (Math.max(0, Number(data.invite_quota_remaining) || 0) > 0) {
            await createNewInviteCard(flow);
        } else {
            clearCurrentInviteCard();
            showInviteCardModal(currentInviteCard);
            renderInviteUnusedList(data);
            finalStatus = '没有可重新显示的邀请码，且生成额度已用完。';
            setSettingsMessage('没有可重新显示的邀请码，且生成额度已用完', true);
        }
    } catch (err) {
        if (!inviteFlowIsCurrent(flow)) return;
        const msg = err.message || '邀请窗口打开失败';
        finalStatus = msg;
        clearCurrentInviteCard();
        showInviteCardModal(currentInviteCard);
        setSettingsMessage(msg, true);
        showMainBanner(msg);
    } finally {
        if (inviteSessionIsCurrent(flow)) {
            inviteCreateInFlight = false;
            if (inviteFlowIsCurrent(flow)) renderInviteUnusedList(inviteUnusedPayload, finalStatus);
            syncInviteSettingsButton(inviteUnusedPayload, {
                disabled: prevDisabled,
                text: prevText || '打开邀请图片',
            });
            syncInviteModalActions();
        }
    }
}

async function createNewInviteFromModal() {
    if (inviteCreateInFlight) return;
    const flow = currentInviteFlow();
    inviteCreateInFlight = true;
    let finalStatus = '';
    renderInviteUnusedList(inviteUnusedPayload, '正在生成新邀请码…');
    try {
        const data = await createNewInviteCard(flow);
        if (!data || !inviteFlowIsCurrent(flow)) return;
        finalStatus = '新邀请码已选中。';
    } catch (err) {
        if (!inviteFlowIsCurrent(flow)) return;
        const msg = err.message || '生成失败';
        const modalMsg = document.getElementById('invite-card-message');
        if (modalMsg) modalMsg.textContent = msg;
        finalStatus = msg;
    } finally {
        if (inviteSessionIsCurrent(flow)) {
            inviteCreateInFlight = false;
            if (inviteFlowIsCurrent(flow)) renderInviteUnusedList(inviteUnusedPayload, finalStatus);
            syncInviteSettingsButton(inviteUnusedPayload);
            syncInviteModalActions();
        }
    }
}

async function selectUnusedInviteFromModal(inviteId) {
    if (inviteCreateInFlight) return;
    const row = inviteUnusedById.get(String(inviteId || ''));
    if (!row || !row.selectable || !row.invite_code) return;
    const flow = currentInviteFlow();
    inviteCreateInFlight = true;
    let finalStatus = '';
    renderInviteUnusedList(inviteUnusedPayload, '正在切换邀请图片…');
    try {
        const selected = await selectInviteCodeForCard({
            code: row.invite_code,
            registerUrl: inviteUnusedPayload.invite_register_url || window.location.origin,
        }, flow);
        if (!selected || !inviteFlowIsCurrent(flow)) return;
        finalStatus = `已选择邀请码 ${row.invite_code}`;
    } catch (err) {
        if (!inviteFlowIsCurrent(flow)) return;
        finalStatus = err.message || '切换失败';
    } finally {
        if (inviteSessionIsCurrent(flow)) {
            inviteCreateInFlight = false;
            if (inviteFlowIsCurrent(flow)) renderInviteUnusedList(inviteUnusedPayload, finalStatus);
            syncInviteSettingsButton(inviteUnusedPayload);
            syncInviteModalActions();
        }
    }
}

function copyTextWithLegacyFallback(text) {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.top = '-1000px';
    textarea.style.opacity = '0';
    textarea.style.fontSize = '16px';
    document.body.appendChild(textarea);
    let copied = false;
    try {
        textarea.focus();
        textarea.select();
        textarea.setSelectionRange(0, textarea.value.length);
        copied = typeof document.execCommand === 'function' && document.execCommand('copy');
    } catch (_) {
        copied = false;
    } finally {
        textarea.remove();
    }
    return copied;
}

async function writeTextToClipboard(text) {
    try {
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            await navigator.clipboard.writeText(text);
            return true;
        }
    } catch (_) {
        /* 继续尝试兼容旧 WebView 的复制方式。 */
    }
    return copyTextWithLegacyFallback(text);
}

async function copyInviteCodeFromModal() {
    const msg = document.getElementById('invite-card-message');
    if (inviteCreateInFlight || !currentInviteCard.code) return;
    const inviteLink = currentInviteCard.registerUrl ||
        buildInviteRegistrationLink(window.location.origin, currentInviteCard.code);
    if (!inviteLink) return;
    try {
        const copied = await writeTextToClipboard(inviteLink);
        if (!copied) throw new Error('copy unavailable');
        if (msg) msg.textContent = '邀请链接已复制';
    } catch (_) {
        if (msg) msg.textContent = `邀请链接：${inviteLink}`;
    }
}

async function shareInviteCardFromModal() {
    const msg = document.getElementById('invite-card-message');
    if (inviteCreateInFlight || !currentInviteCard.code || !currentInviteCard.blob) return;
    const inviteLink = currentInviteCard.registerUrl ||
        buildInviteRegistrationLink(window.location.origin, currentInviteCard.code);
    const shareText = `点击链接直接注册（邀请码已填好）：${inviteLink}`;
    try {
        if (
            currentInviteCard.file &&
            navigator.canShare &&
            navigator.canShare({ files: [currentInviteCard.file] })
        ) {
            await navigator.share({
                files: [currentInviteCard.file],
                title: '邀请你一起背单词',
                text: shareText,
            });
            if (msg) msg.textContent = '已打开系统分享';
        } else if (navigator.share) {
            await navigator.share({
                title: '邀请你一起背单词',
                text: `邀请码：${currentInviteCard.code}`,
                url: inviteLink,
            });
            if (msg) msg.textContent = '已打开系统分享';
        } else {
            if (msg) msg.textContent = '当前浏览器不支持直接分享，请保存图片后发送';
        }
    } catch (err) {
        if (err && err.name === 'AbortError') return;
        if (msg) msg.textContent = '分享失败，请保存图片后发送';
    }
}

function applyAvatarCropTransform() {
    const img = document.getElementById('avatar-crop-img');
    if (!img || !avatarCrop.iw) return;
    const w = avatarCrop.iw * avatarCrop.scale;
    const h = avatarCrop.ih * avatarCrop.scale;
    img.style.width = `${w}px`;
    img.style.height = `${h}px`;
    img.style.left = `${avatarCrop.imgX}px`;
    img.style.top = `${avatarCrop.imgY}px`;
}

function clampAvatarCrop() {
    const { scale, iw, ih } = avatarCrop;
    const vw = AVATAR_CROP_VIEW;
    const fw = iw * scale;
    const fh = ih * scale;
    avatarCrop.imgX = Math.min(0, Math.max(vw - fw, avatarCrop.imgX));
    avatarCrop.imgY = Math.min(0, Math.max(vw - fh, avatarCrop.imgY));
}

function onAvatarCropZoomInput() {
    const zoomEl = document.getElementById('avatar-crop-zoom');
    const raw = zoomEl && zoomEl.value != null ? parseInt(zoomEl.value, 10) : 100;
    const pct = (Number.isFinite(raw) ? raw : 100) / 100;
    const z = Math.max(1, Math.min(3, pct));
    const vw = AVATAR_CROP_VIEW;
    const cx = vw / 2;
    const cy = vw / 2;
    const prevScale = avatarCrop.scale;
    avatarCrop.scale = avatarCrop.coverScale * z;
    const ix = (cx - avatarCrop.imgX) / prevScale;
    const iy = (cy - avatarCrop.imgY) / prevScale;
    avatarCrop.imgX = cx - ix * avatarCrop.scale;
    avatarCrop.imgY = cy - iy * avatarCrop.scale;
    clampAvatarCrop();
    applyAvatarCropTransform();
}

function closeAvatarCropModal() {
    const overlay = document.getElementById('avatar-crop-overlay');
    if (overlay) {
        overlay.style.display = 'none';
        overlay.setAttribute('aria-hidden', 'true');
    }
    const img = document.getElementById('avatar-crop-img');
    if (img) img.removeAttribute('src');
    if (avatarCrop.objectUrl) {
        URL.revokeObjectURL(avatarCrop.objectUrl);
        avatarCrop.objectUrl = null;
    }
    avatarCrop.dragging = false;
}

function openAvatarCropModal(file) {
    if (!file || !file.type.startsWith('image/')) {
        setSettingsMessage('请选择图片文件', true);
        return;
    }
    closeAvatarCropModal();
    avatarCrop.objectUrl = URL.createObjectURL(file);
    const img = document.getElementById('avatar-crop-img');
    const overlay = document.getElementById('avatar-crop-overlay');
    if (!img || !overlay) return;
    img.onload = () => {
        avatarCrop.iw = img.naturalWidth;
        avatarCrop.ih = img.naturalHeight;
        if (!avatarCrop.iw || !avatarCrop.ih) {
            setSettingsMessage('无法读取图片尺寸', true);
            closeAvatarCropModal();
            return;
        }
        const vw = AVATAR_CROP_VIEW;
        avatarCrop.coverScale = Math.max(vw / avatarCrop.iw, vw / avatarCrop.ih);
        avatarCrop.scale = avatarCrop.coverScale;
        const fw = avatarCrop.iw * avatarCrop.scale;
        const fh = avatarCrop.ih * avatarCrop.scale;
        avatarCrop.imgX = (vw - fw) / 2;
        avatarCrop.imgY = (vw - fh) / 2;
        const zoomEl = document.getElementById('avatar-crop-zoom');
        if (zoomEl) zoomEl.value = '100';
        applyAvatarCropTransform();
        overlay.style.display = 'flex';
        overlay.setAttribute('aria-hidden', 'false');
        img.onload = null;
    };
    img.onerror = () => {
        setSettingsMessage('无法加载图片', true);
        closeAvatarCropModal();
    };
    img.src = avatarCrop.objectUrl;
}

async function confirmAvatarCropUpload() {
    const img = document.getElementById('avatar-crop-img');
    if (!img || !img.complete || !avatarCrop.iw) return;
    const vw = AVATAR_CROP_VIEW;
    const out = 512;
    const canvas = document.createElement('canvas');
    canvas.width = out;
    canvas.height = out;
    const ctx = canvas.getContext('2d');
    const { scale, imgX, imgY, iw, ih } = avatarCrop;
    const sx = (0 - imgX) / scale;
    const sy = (0 - imgY) / scale;
    const sw = vw / scale;
    try {
        ctx.drawImage(img, sx, sy, sw, sw, 0, 0, out, out);
    } catch (err) {
        setSettingsMessage('裁剪失败，请重试', true);
        return;
    }
    let uploadBlob = await new Promise((resolve) => {
        canvas.toBlob((b) => resolve(b), 'image/webp', 0.88);
    });
    if (!uploadBlob || uploadBlob.size < 20) {
        uploadBlob = await new Promise((resolve) => {
            canvas.toBlob((b) => resolve(b), 'image/jpeg', 0.9);
        });
    }
    if (!uploadBlob) {
        setSettingsMessage('无法生成图片', true);
        return;
    }
    const isJpeg = uploadBlob.type === 'image/jpeg';
    closeAvatarCropModal();
    try {
        const uploaded = await postAvatarFile(uploadBlob, isJpeg ? 'avatar.jpg' : 'avatar.webp');
        updateNavUserAvatar(uploaded && uploaded.avatar_url);
        if (isSettingsOverlayOpen()) {
            await loadUserSettingsPanel();
            setSettingsMessage('头像已更新');
        } else {
            showMainBanner('头像已更新');
        }
    } catch (err) {
        if (isSettingsOverlayOpen()) {
            setSettingsMessage(err.message || '上传失败', true);
        } else {
            showMainBanner(err.message || '上传失败');
        }
    }
}

function bindAvatarCropUi() {
    const vp = document.getElementById('avatar-crop-viewport');
    const zoom = document.getElementById('avatar-crop-zoom');
    document.getElementById('avatar-crop-cancel')?.addEventListener('click', () => closeAvatarCropModal());
    document.getElementById('avatar-crop-backdrop')?.addEventListener('click', () => closeAvatarCropModal());
    document.getElementById('avatar-crop-ok')?.addEventListener('click', () => confirmAvatarCropUpload());
    zoom?.addEventListener('input', () => onAvatarCropZoomInput());
    if (!vp) return;
    vp.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        avatarCrop.dragging = true;
        avatarCrop.dragStartX = e.clientX;
        avatarCrop.dragStartY = e.clientY;
        avatarCrop.startImgX = avatarCrop.imgX;
        avatarCrop.startImgY = avatarCrop.imgY;
        try {
            vp.setPointerCapture(e.pointerId);
        } catch (_) {
            /* ignore */
        }
    });
    vp.addEventListener('pointermove', (e) => {
        if (!avatarCrop.dragging) return;
        avatarCrop.imgX = avatarCrop.startImgX + (e.clientX - avatarCrop.dragStartX);
        avatarCrop.imgY = avatarCrop.startImgY + (e.clientY - avatarCrop.dragStartY);
        clampAvatarCrop();
        applyAvatarCropTransform();
    });
    const endDrag = () => {
        avatarCrop.dragging = false;
    };
    vp.addEventListener('pointerup', endDrag);
    vp.addEventListener('pointercancel', endDrag);
}

async function loadUserSettingsPanel() {
    const studentBlocks = document.getElementById('settings-student-blocks');
    const parentBlock = document.getElementById('settings-parent-block');
    const title = document.getElementById('settings-title');
    setSettingsMessage('');
    try {
        await loadAccountEmail();
    } catch (e) {
        setSettingsMessage(e.message || '邮箱信息加载失败', true);
    }
    if (isParentSession) {
        if (title) title.textContent = '配置';
        if (studentBlocks) studentBlocks.style.display = 'none';
        if (parentBlock) parentBlock.style.display = '';
        const hint = document.getElementById('settings-parent-login-hint');
        if (hint) {
            hint.textContent = childUsername
                ? `正在查看学生「${childUsername}」的学习数据。家长登录名为 ${username}。`
                : '';
        }
        const n1 = document.getElementById('settings-parent-pw-new');
        const n2 = document.getElementById('settings-parent-pw-confirm');
        if (n1) n1.value = '';
        if (n2) n2.value = '';
        resetSettingsPendingWordsCollapse();
        await loadSettingsPendingWordsBlock();
        return;
    }
    if (title) title.textContent = '用户设置';
    if (studentBlocks) studentBlocks.style.display = '';
    if (parentBlock) parentBlock.style.display = 'none';
    try {
        const [s, poolState] = await Promise.all([
            apiRequest('/user/settings'),
            apiRequest('/monthly-pool'),
        ]);
        s.monthly_pool = poolState;
        renderUserSettingsPanel(s);
        lastGamificationProfile = s;
        updateGamificationNav(s);
    } catch (e) {
        setSettingsMessage(e.message || '加载失败', true);
    }
    resetSettingsPendingWordsCollapse();
    await loadSettingsPendingWordsBlock();
}

async function loadAccountEmail() {
    const data = await apiRequest('/user/email');
    const input = document.getElementById('settings-email');
    if (input) input.value = data.email || '';
    setSettingsEmailMessage('');
    return data;
}

function setSettingsEmailMessage(message, isError = false) {
    const el = document.getElementById('settings-email-message');
    if (!el) return;
    el.textContent = message || '';
    el.classList.toggle('is-error', !!isError);
}

function resetSettingsPendingWordsCollapse() {
    const panel = document.getElementById('settings-pending-words-panel');
    const btn = document.getElementById('settings-pending-words-toggle');
    if (panel) panel.hidden = true;
    if (btn) {
        btn.setAttribute('aria-expanded', 'false');
        btn.classList.remove('is-open');
    }
}

function syncSettingsPendingBatchToolbar() {
    const list = document.getElementById('settings-pending-words-list');
    const all = document.getElementById('settings-pending-select-all');
    if (!list || !all) return;
    const cbs = list.querySelectorAll('.settings-pending-cb');
    const n = cbs.length;
    let checked = 0;
    cbs.forEach((cb) => {
        if (cb.checked) checked += 1;
    });
    all.checked = n > 0 && checked === n;
    all.indeterminate = checked > 0 && checked < n;
    const countEl = document.getElementById('settings-pending-selected-count');
    const delBtn = document.getElementById('settings-pending-words-delete-selected');
    if (countEl) countEl.textContent = `已选 ${checked} 项`;
    if (delBtn) delBtn.disabled = checked === 0;
}

/** 配置页：列出当前学生待复习词并可移除（家长登录可删；学生仅未开通家长账户时可删） */
async function loadSettingsPendingWordsBlock() {
    const listEl = document.getElementById('settings-pending-words-list');
    const leadEl = document.getElementById('settings-pending-words-lead');
    const toggleText = document.getElementById('settings-pending-words-toggle-text');
    const batchToolbar = document.getElementById('settings-pending-batch-toolbar');
    if (!listEl) return;
    const panelEl = document.getElementById('settings-pending-words-panel');
    const expandedPanel = panelEl && !panelEl.hidden;
    const expandLabel =
        isParentSession && childUsername
            ? '点击展开：查看并管理该学生的待复习单词'
            : '点击展开：查看列表并管理待复习单词';
    if (toggleText) {
        toggleText.dataset.expandLabel = expandLabel;
        toggleText.textContent = expandedPanel ? '点击收起' : expandLabel;
    }
    if (leadEl) {
        leadEl.textContent =
            isParentSession && childUsername
                ? `管理学生「${childUsername}」的待复习列表。移除后该词不再出现在待复习中（已掌握词不受影响）。`
                : '管理你的待复习列表。移除后该词不再出现在待复习中（已掌握词不受影响）。';
    }
    if (batchToolbar) batchToolbar.hidden = true;
    listEl.innerHTML = '<p class="settings-hint">加载中…</p>';
    try {
        const data = await apiRequest('/words/pending');
        const canRemove = data.can_remove_pending !== false;
        if (leadEl && !isParentSession && !canRemove) {
            leadEl.textContent =
                '已开通家长账户，待复习词汇仅可由家长登录后在「配置」中管理；学生账号无法在此移除。';
        } else if (leadEl && canRemove) {
            leadEl.textContent =
                (isParentSession && childUsername
                    ? `管理学生「${childUsername}」的待复习列表。移除后该词不再出现在待复习中（已掌握词不受影响）。`
                    : '管理你的待复习列表。移除后该词不再出现在待复习中（已掌握词不受影响）。') +
                ' 勾选后点击「删除选中」可批量移除。';
        }
        const words = Array.isArray(data.words) ? data.words : [];
        if (batchToolbar) batchToolbar.hidden = !(canRemove && words.length > 0);
        if (words.length === 0) {
            listEl.innerHTML =
                '<p class="settings-hint settings-pending-words-empty">当前没有待复习单词。</p>';
            return;
        }
        const rowCb = (enc) =>
            canRemove
                ? `<input type="checkbox" class="settings-pending-cb" data-english="${enc}" aria-label="选择该词" />`
                : '';
        listEl.innerHTML = words
            .map((w) => {
                const en = String(w.english || '');
                const enc = encodeURIComponent(en);
                const zh = escapeHtml(w.chinese || '');
                const enDisp = escapeHtml(en);
                const rd =
                    typeof w.remaining_days === 'number'
                        ? `距下次复习 ${w.remaining_days} 天`
                        : '';
                const meta = [rd].filter(Boolean).join(' · ');
                return (
                    `<div class="settings-pending-row${canRemove ? ' settings-pending-row--batch' : ''}" role="listitem">` +
                    (canRemove ? `<div class="settings-pending-cb-wrap">${rowCb(enc)}</div>` : '') +
                    `<div class="settings-pending-row-main">` +
                    `<div class="settings-pending-line1">` +
                    `<strong class="settings-pending-en">${enDisp}</strong>` +
                    `<span class="settings-pending-zh">${zh}</span>` +
                    `</div>` +
                    (meta
                        ? `<div class="settings-pending-meta">${escapeHtml(meta)}</div>`
                        : '') +
                    `</div>` +
                    `</div>`
                );
            })
            .join('');
        if (canRemove && batchToolbar) syncSettingsPendingBatchToolbar();
    } catch (e) {
        if (batchToolbar) batchToolbar.hidden = true;
        listEl.innerHTML = `<p class="settings-hint settings-message-error">${escapeHtml(
            e.message || '加载失败',
        )}</p>`;
    }
}

async function postAvatarFile(file, filename = 'avatar.webp') {
    const fd = new FormData();
    fd.append('file', file, filename);
    const headers = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(`${API_BASE}/user/avatar`, { method: 'POST', headers, body: fd });
    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || err.detail || '上传失败');
    }
    return response.json();
}

async function deleteAvatarApi() {
    const headers = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(`${API_BASE}/user/avatar`, { method: 'DELETE', headers });
    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || err.detail || '移除失败');
    }
    return response.json();
}

function renderMonthlyPoolRace(pool) {
    const wrap = document.getElementById('monthly-pool-race-wrap');
    if (!wrap) return;
    if (!pool) {
        wrap.innerHTML = '';
        return;
    }
    const prepDay = pool.preparation_last_day || 5;
    const startDay = pool.competition_start_day || 6;
    const maxDays = Number(pool.competition_days_max) || 0;
    const runners = Array.isArray(pool.runners) ? pool.runners : [];
    const isPrep = pool.phase === 'preparation' || pool.preparation_phase;
    const poolXp = formatNumber(pool.pool_xp || 0);
    const fee = pool.fee_xp || 150;

    const su = sessionStudentUsername();
    let joinBtnHtml = '';
    if (isParentSession) {
        if (pool.joined) {
            joinBtnHtml = '<span class="mp-race-badge mp-race-badge--go">学生已报名</span>';
        }
    } else if (pool.join_window_open && !pool.joined) {
        joinBtnHtml = `<button type="button" class="btn btn-primary mp-race-join" id="leaderboard-pool-join">加入奖池（${fee} XP）</button>`;
    } else if (pool.joined) {
        joinBtnHtml = '<span class="mp-race-badge mp-race-badge--go">已报名</span>';
    }

    const desc = isPrep
        ? `每月 1～${prepDay} 日为准备期（可支付 ${fee} XP 加入奖池）；第 ${startDay} 日起比赛正式开始，赛跑进度仅统计当月 ${startDay} 日及之后的有效打卡天数。`
        : `本月比赛进行中。进度 = ${startDay} 日及以后的有效打卡天数 ÷ 本月可比赛天数（${maxDays}）。`;

    let lanesHtml = '';
    if (runners.length === 0) {
        lanesHtml = `<p class="mp-race-empty">暂无参赛者。请在每月 1～${prepDay} 日准备期内加入奖池。</p>`;
    } else {
        lanesHtml = runners
            .map((r) => {
                const uname = r.username || '';
                const u = escapeHtml(uname);
                const p = Math.max(0, Math.min(1, Number(r.progress) || 0));
                const pct = Math.round(p * 100);
                const cd = Number(r.competition_days) || 0;
                const me = uname === su ? ' mp-lane-me' : '';
                const av = r.avatar_url
                    ? `<img src="${escapeHtml(avatarDisplayUrl(r.avatar_url, 64))}" alt="" width="28" height="28" loading="lazy" />`
                    : '<span class="mp-lane-avatar-ph" aria-hidden="true">👤</span>';
                const you = uname === su ? ' <span class="lb-you">我</span>' : '';
                return `<div class="mp-lane${me}">
                    <div class="mp-lane-user">${av}<span class="mp-lane-name">${u}${you}</span></div>
                    <div class="mp-lane-track">
                        <div class="mp-lane-fill" style="width:${pct}%"></div>
                        <div class="mp-lane-pin" style="left:${pct}%">🏃</div>
                    </div>
                    <div class="mp-lane-days">${cd}/${maxDays}天</div>
                </div>`;
            })
            .join('');
    }

    const badge = isPrep
        ? '<span class="mp-race-badge mp-race-badge--prep">准备期</span>'
        : '<span class="mp-race-badge mp-race-badge--go">比赛进行中</span>';

    wrap.innerHTML = `
<div class="mp-race">
  <div class="mp-race-head">
    <div class="mp-race-title">月度群体挑战</div>
    ${badge}
    <span style="font-weight:800;color:var(--text-secondary);font-size:0.9rem">${pool.participant_count || 0} 人 · 奖池 ${poolXp} XP</span>
    ${joinBtnHtml}
    <div class="mp-race-desc">${desc}</div>
  </div>
  <div class="mp-race-body">
    <div class="mp-race-lanes">${lanesHtml}</div>
    <div class="mp-race-finish">
      <div class="mp-race-pool-ico">🏆</div>
      <div class="mp-race-pool-xp">${poolXp} XP</div>
    </div>
  </div>
</div>`;

    const btn = document.getElementById('leaderboard-pool-join');
    if (btn) {
        btn.addEventListener('click', async () => {
            try {
                await apiRequest('/monthly-pool/join', { method: 'POST', body: '{}' });
                showMainBanner('已加入本月奖池');
                await loadLeaderboardSection();
            } catch (e) {
                showMainBanner(e.message || '加入失败');
            }
        });
    }
}

/** 从候选数组随机取一条（吐槽文案轮换用） */
function pickRandom(arr) {
    if (!arr || arr.length === 0) return '';
    return arr[Math.floor(Math.random() * arr.length)];
}

/** 排行榜页顶部说明：每次打开页随机一条 */
/** 排行榜 Tab：总榜 / 周榜 / 月榜（与 /api/leaderboard?scope= 同步） */
let leaderboardScope = 'total';
let leaderboardTabsBound = false;
/** 最近一次渲染排行榜表格时的数据，供展开/收起时复用 */
let lastLeaderboardTablePayload = null;

const LEADERBOARD_INTRO_ROASTS = [
    '先选总榜 / 周榜 / 月榜，领奖台和主榜紧跟在 Tab 下面；主榜下面是月度群体赛跑，再往下是每月 PK 风云榜——先看本月对线，再翻上月旧账。',
    'XP 榜是面子工程，PK 榜是里子工程：主榜比总分，再往下 PK 区比打卡天数谁能把对面气笑。',
    '温馨提示：排行榜解决不了人生，但能解决「我到底有没有在学」的焦虑——主榜、群体赛、PK 从上到下一条龙围观。',
    '上面 Tab 定范围，主榜看完往下：赛跑图一乐，再往下 PK 区有人赢积分有人赢对面少打一天卡。',
    '数据不会说谎，除非你没打卡。XP 主榜、群体赛、PK 榜三连——总有一个让你坐不住。',
    '学习这件事，要么自己卷，要么拉人一起卷；主榜下面的 PK 榜专门公示第二种。',
    '别光盯着主榜：月度赛在主榜下面众筹内卷，1v1 PK 再往下，请按需食用。',
];

const PK_BOARD_TITLE_ROASTS = [
    '每月 PK 风云榜',
    '本月擂台弹幕区',
    '1v1 打卡公开处刑榜',
    '友谊的小船停靠站',
];

const PK_BOARD_TAGLINE_ROASTS = [
    '专治「我学了但我不说」：上月谁把谁按在打卡天数上摩擦，本月谁还在互相瞪眼，都在这里公示。输了不丢人，不点发起才亏。',
    '这里没有「差不多学了」，只有「打卡天数比你多」。本月战况在上，上月旧账在下，一键围观。',
    '约战之前请三思：输的是赌注，赢的是可以在心里默念三遍「我打卡了」的权利。',
    '本榜不提供心理辅导，只提供冷冰冰的有效打卡对比。觉得扎心说明来对地方了。',
    '别人晒步数你晒打卡天数——PK 区欢迎一切良性（或恶性）竞争。',
    '温馨提示：平局不是默契，是系统看你们一样菜（划掉）一样努力。',
];

const PK_SECTION_PREV_SUFFIX_ROASTS = [
    '旧账已结清',
    '服务器已记仇完毕',
    '恩怨写入数据库',
    '瓜已熟透可食用',
    '战绩存档，不服下个月',
];

const PK_SECTION_CUR_SUFFIX_ROASTS = [
    '火线吃瓜区',
    '实时对线中',
    '战况随时更新',
    '赌注已冻结，人还在卷',
    '当前赛季现场',
];

const PK_EMPTY_SETTLED_ROASTS = [
    '上月擂台比图书馆还安静——要么全员佛系，要么全在别的赛道偷偷上分。',
    '上月居然一场没有？合理怀疑大家都在假装没看见顶栏的 PK。',
    '空空如也。不是世界和平，是大家都把火药留到本月了。',
    '上月无人约战——建议反思：是友谊太铁，还是赌注不够刺激。',
    '此处本应锣鼓喧天，实际上连表情包都没人发。',
];

const PK_EMPTY_ONGOING_ROASTS = [
    '本月居然没人约战？点顶栏 PK 发起一场，让友谊在赌注里升华一下。',
    '本月擂台闲置中。再不动手，下个月吐槽文案都要重复了。',
    '零场进行中——是给对手留面子，还是给自己留退路？',
    '静悄悄，未必在憋大招，也可能真的在摸鱼。',
    '暂无对线：适合补刀（划掉）补打卡。',
];

/** 进行中 PK：押韵、诙谐短句（仅在有底栏时展示） */
const PK_ONGOING_BLURBS = [
    '月底见分晓，谁卷谁知道。',
    '押注已锁死，打卡别摆烂。',
    '天数对对碰，谁怂谁认怂。',
    '战线拉很长，摸鱼会上墙。',
    '有效打卡算，谁多谁好办。',
    '平局若出现，赌注各自还。',
    '你追我赶跑，闹钟要调好。',
    '嘴硬不算数，系统只认天。',
];

function monthlyPkAvatarHtml(uname, compact) {
    const u = String(uname || '').trim();
    const sz = compact ? 32 : 40;
    const wParam = compact ? 40 : 48;
    const wrapOpen = '<span class="monthly-pk-av-wrap">';
    if (!u) {
        return wrapOpen + '<span class="monthly-pk-av-ph" aria-hidden="true">👤</span></span>';
    }
    const path = '/api/user/avatar/' + encodeURIComponent(u);
    const src = typeof avatarDisplayUrl === 'function' ? avatarDisplayUrl(path, wParam) : path + '?w=' + wParam;
    return (
        wrapOpen +
        `<img class="monthly-pk-av" src="${escapeHtml(src)}" alt="" width="${sz}" height="${sz}" loading="lazy" decoding="async" ` +
        `onerror="this.onerror=null;this.closest('.monthly-pk-av-wrap').classList.add('monthly-pk-av--fallback');this.removeAttribute('src');" />` +
        '<span class="monthly-pk-av-ph" aria-hidden="true">👤</span></span>'
    );
}

function renderMonthlyPkBoard(board) {
    const wrap = document.getElementById('monthly-pk-board-wrap');
    if (!wrap) return;
    if (!board || typeof board !== 'object') {
        wrap.innerHTML = '';
        return;
    }
    const viewer = sessionStudentUsername() || username || '';
    const prevM = board.prev_month || '';
    const curM = board.current_month || '';
    const settled = Array.isArray(board.settled_last_month) ? board.settled_last_month : [];
    const ongoing = Array.isArray(board.ongoing_this_month) ? board.ongoing_this_month : [];

    const meClass = (u) => (u && viewer && u === viewer ? ' monthly-pk-user-me' : '');

    const fmtDuel = (d, isOngoing) => {
        const a = d.from_user || '';
        const b = d.target_user || '';
        const wager = Number(d.wager_xp) || 0;
        const days = d.pk_checkin_days || {};
        const da = Number(days[a] ?? days[String(a)]) || 0;
        const db = Number(days[b] ?? days[String(b)]) || 0;
        const winner = d.winner;
        const tie = d.tie;
        const compact = !isOngoing;

        let statusClass = 'monthly-pk-status--ongoing';
        let statusIcon = '⚡';
        let statusText = '进行中';

        if (isOngoing) {
            /* blurb 仅进行中展示 */
        } else if (tie) {
            statusClass = 'monthly-pk-status--tie';
            statusIcon = '🤝';
            statusText = '平局';
        } else if (winner) {
            statusClass = 'monthly-pk-status--win';
            statusIcon = '🏆';
            statusText = `${winner} 胜`;
        } else {
            statusClass = 'monthly-pk-status--done';
            statusIcon = '✓';
            statusText = '已结算';
        }

        const cardClass = isOngoing ? 'monthly-pk-card monthly-pk-card--ongoing' : 'monthly-pk-card monthly-pk-card--settled';
        const blurbHtml = isOngoing ? pickRandom(PK_ONGOING_BLURBS) : '';
        const blurbBlock = blurbHtml ? `<p class="monthly-pk-blurb">${blurbHtml}</p>` : '';

        return (
            `<article class="${cardClass}">` +
            `<div class="monthly-pk-faceoff" aria-label="${escapeHtml(a)} 对 ${escapeHtml(b)}">` +
            `<div class="monthly-pk-player">` +
            monthlyPkAvatarHtml(a, compact) +
            `<span class="monthly-pk-name${meClass(a)}">${escapeHtml(a)}</span>` +
            `</div>` +
            `<div class="monthly-pk-vs-mid">` +
            `<span class="monthly-pk-score" title="有效打卡天数">${escapeHtml(String(da))} : ${escapeHtml(
                String(db),
            )}</span>` +
            `<span class="monthly-pk-wager" title="赌注">押 ${escapeHtml(String(wager))} XP</span>` +
            `<span class="monthly-pk-status ${statusClass}"><span class="monthly-pk-status-ic" aria-hidden="true">${statusIcon}</span>${escapeHtml(
                statusText,
            )}</span>` +
            `</div>` +
            `<div class="monthly-pk-player">` +
            monthlyPkAvatarHtml(b, compact) +
            `<span class="monthly-pk-name${meClass(b)}">${escapeHtml(b)}</span>` +
            `</div>` +
            `</div>` +
            blurbBlock +
            `</article>`
        );
    };

    const settledHtml = settled.length
        ? settled.map((d) => fmtDuel(d, false)).join('')
        : `<p class="monthly-pk-empty">${escapeHtml(pickRandom(PK_EMPTY_SETTLED_ROASTS))}</p>`;

    const ongoingHtml = ongoing.length
        ? ongoing.map((d) => fmtDuel(d, true)).join('')
        : `<p class="monthly-pk-empty">${escapeHtml(pickRandom(PK_EMPTY_ONGOING_ROASTS))}</p>`;

    const boardTitle = escapeHtml(pickRandom(PK_BOARD_TITLE_ROASTS));
    const tagline = escapeHtml(pickRandom(PK_BOARD_TAGLINE_ROASTS));
    const prevSuffix = escapeHtml(pickRandom(PK_SECTION_PREV_SUFFIX_ROASTS));
    const curSuffix = escapeHtml(pickRandom(PK_SECTION_CUR_SUFFIX_ROASTS));

    wrap.innerHTML =
        `<div class="monthly-pk-board">` +
        `<div class="monthly-pk-head">` +
        `<h3 class="monthly-pk-title">${boardTitle}</h3>` +
        `<p class="monthly-pk-tagline">${tagline}</p>` +
        `</div>` +
        `<section class="monthly-pk-section monthly-pk-section--ongoing" aria-labelledby="monthly-pk-cur-title">` +
        `<h4 id="monthly-pk-cur-title" class="monthly-pk-section-title">🔥 ${escapeHtml(curM)} ${curSuffix}</h4>` +
        `<div class="monthly-pk-list monthly-pk-list--ongoing">${ongoingHtml}</div>` +
        `</section>` +
        `<section class="monthly-pk-section monthly-pk-section--prev" aria-labelledby="monthly-pk-prev-title">` +
        `<h4 id="monthly-pk-prev-title" class="monthly-pk-section-title monthly-pk-section-title--sub">📜 ${escapeHtml(
            prevM,
        )} ${prevSuffix}</h4>` +
        `<div class="monthly-pk-list monthly-pk-list--settled">${settledHtml}</div>` +
        `</section>` +
        `</div>`;
}

function leaderboardTableRowHtml(r, sc) {
    const me = r.is_viewer ? 'leaderboard-row-me' : '';
    const av = r.avatar_url
        ? `<img class="lb-avatar" src="${escapeHtml(avatarDisplayUrl(r.avatar_url, 64))}" alt="" width="32" height="32" loading="lazy" />`
        : '<span class="lb-avatar lb-avatar-placeholder" aria-hidden="true">👤</span>';
    const xpVal = sc === 'total' ? r.total_xp : r.period_xp;
    const streakHint = streakDiagnosticsHintFromRow(r);
    return `<tr class="${me}">
                <td>${escapeHtml(r.rank)}</td>
                <td class="lb-user-cell">${av}<span class="lb-username">${escapeHtml(r.username)}${r.is_viewer ? ' <span class="lb-you">我</span>' : ''}</span></td>
                <td>Lv.${escapeHtml(r.level)}</td>
                <td>${escapeHtml(formatNumber(xpVal))}</td>
                <td class="lb-streak-cell" title="${escapeHtml(streakHint)}">${lbStreakCellInnerHtml(r.streak, r.streak_max_record)}</td>
                <td>${escapeHtml(r.achievements_count)}</td>
            </tr>`;
}

function leaderboardTableEllipsisRowHtml(hiddenCount) {
    const hint =
        hiddenCount > 0
            ? `中间还有 ${hiddenCount} 人`
            : '···';
    return `<tr class="leaderboard-row-ellipsis">
                <td colspan="6">${escapeHtml(hint)}</td>
            </tr>`;
}

/** 折叠模式下要显示的行下标（已排序）；若人数不超过 3 则全部展示。 */
function leaderboardCollapsedRowIndices(rows) {
    const n = rows.length;
    if (n <= 3) {
        return [...Array(n).keys()];
    }
    const set = new Set([0, 1, 2]);
    const vi = rows.findIndex((r) => r.is_viewer);
    if (vi >= 0 && vi > 2) {
        set.add(vi);
    }
    return Array.from(set).sort((a, b) => a - b);
}

function renderLeaderboardPodium(apiData) {
    const wrap = document.getElementById('leaderboard-podium-wrap');
    if (!wrap) return;
    const scope = apiData && apiData.scope ? apiData.scope : 'total';
    if (scope === 'total' || !apiData.podium_last_period) {
        wrap.innerHTML = '';
        return;
    }
    const p = apiData.podium_last_period;
    const kind = scope === 'week' ? '周榜' : '月榜';
    const medals = ['🥇', '🥈', '🥉'];
    const top = Array.isArray(p.top) ? p.top : [];
    if (top.length === 0) {
        wrap.innerHTML = '';
        return;
    }
    const cards = top
        .map((t, i) => {
            const rx = Number(t.reward_xp) || 0;
            const rxNote = rx > 0 ? ` · 奖励 +${rx} XP` : '';
            const av = t.avatar_url
                ? `<img class="lb-podium-avatar" src="${escapeHtml(avatarDisplayUrl(t.avatar_url, 96))}" alt="" width="48" height="48" loading="lazy" />`
                : '<span class="lb-podium-avatar lb-avatar-placeholder" aria-hidden="true">👤</span>';
            const rk = Number(t.rank) || i + 1;
            return `<div class="leaderboard-podium-card rank-${rk}">
                <div class="lb-podium-medal">${medals[i] || '🏅'}</div>
                ${av}
                <div class="lb-podium-name">${escapeHtml(t.username)}</div>
                <div class="lb-podium-meta">${escapeHtml(formatNumber(t.period_xp))} XP${escapeHtml(rxNote)}</div>
            </div>`;
        })
        .join('');
    wrap.innerHTML =
        `<div class="leaderboard-podium">` +
        `<h3 class="leaderboard-podium-title">上期${kind}三甲</h3>` +
        `<p class="leaderboard-podium-sub">${escapeHtml(p.period_label || p.period_id || '')}</p>` +
        `<div class="leaderboard-podium-cards">${cards}</div>` +
        `</div>`;
}

function renderLeaderboardTable(rows, scope, meta, options) {
    const wrap = document.getElementById('leaderboard-table-wrap');
    if (!wrap) return;
    const sc = scope === 'week' || scope === 'month' ? scope : 'total';
    const m = meta && typeof meta === 'object' ? meta : {};
    lastLeaderboardTablePayload = { rows, scope, meta: m };
    const expanded = options && options.expanded === true;

    if (!rows || rows.length === 0) {
        lastLeaderboardTablePayload = null;
        wrap.innerHTML =
            '<p class="leaderboard-empty">暂无排行数据。开启「在排行榜中展示」并学习后即可上榜。</p>';
        return;
    }

    const xpTh = sc === 'total' ? '累计 XP' : '本期 XP';
    const head =
        '<table class="leaderboard-table"><thead><tr>' +
        `<th>排名</th><th>用户</th><th>等级</th><th>${xpTh}</th><th>连续</th><th>成就</th>` +
        '</tr></thead><tbody>';

    let body = '';
    if (expanded || rows.length <= 3) {
        body = rows.map((r) => leaderboardTableRowHtml(r, sc)).join('');
    } else {
        const idx = leaderboardCollapsedRowIndices(rows);
        const shown = new Set(idx);
        const hiddenCount = rows.length - shown.size;
        const parts = [];
        for (let i = 0; i < idx.length; i++) {
            if (i > 0 && idx[i] > idx[i - 1] + 1) {
                parts.push(leaderboardTableEllipsisRowHtml(hiddenCount));
            }
            parts.push(leaderboardTableRowHtml(rows[idx[i]], sc));
        }
        body = parts.join('');
    }

    const showToggle = rows.length > 3;
    const toolbar = showToggle
        ? `<div class="leaderboard-table-toolbar">
            <button type="button" class="btn btn-secondary leaderboard-expand-toggle" aria-expanded="${expanded ? 'true' : 'false'}" data-lb-expand="${expanded ? '0' : '1'}">
                ${expanded ? '收起' : `展开全部（共 ${rows.length} 人）`}
            </button>
        </div>`
        : '';

    const footer =
        sc !== 'total' && (m.period_label || m.period_sum_end)
            ? `<p class="leaderboard-period-hint">${escapeHtml(m.period_label || '')}${
                  m.period_sum_end ? ` · 统计截至 ${escapeHtml(m.period_sum_end)}` : ''
              }</p>`
            : '';
    wrap.innerHTML = head + body + '</tbody></table>' + toolbar + footer;

    const btn = wrap.querySelector('.leaderboard-expand-toggle');
    if (btn) {
        btn.addEventListener('click', () => {
            const wantExpand = btn.getAttribute('data-lb-expand') === '1';
            if (!lastLeaderboardTablePayload) return;
            renderLeaderboardTable(
                lastLeaderboardTablePayload.rows,
                lastLeaderboardTablePayload.scope,
                lastLeaderboardTablePayload.meta,
                { expanded: wantExpand },
            );
        });
    }
}

function bindLeaderboardTabs() {
    if (leaderboardTabsBound) return;
    const tablist = document.querySelector('.leaderboard-scope-tabs');
    if (!tablist) return;
    leaderboardTabsBound = true;
    tablist.addEventListener('click', async (e) => {
        const btn = e.target.closest('[data-lb-scope]');
        if (!btn) return;
        const scope = btn.getAttribute('data-lb-scope');
        if (!scope || scope === leaderboardScope) return;
        leaderboardScope = scope;
        await reloadLeaderboardTableOnly();
    });
}

async function reloadLeaderboardTableOnly() {
    const loading = document.getElementById('leaderboard-loading');
    if (loading) loading.style.display = 'block';
    try {
        const data = await apiRequest('/leaderboard?scope=' + encodeURIComponent(leaderboardScope));
        document.querySelectorAll('[data-lb-scope]').forEach((b) => {
            const s = b.getAttribute('data-lb-scope');
            const on = s === leaderboardScope;
            b.classList.toggle('active', on);
            b.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        const sc = data.scope || leaderboardScope;
        renderLeaderboardTable(data.leaderboard, sc, data, { expanded: false });
        renderLeaderboardPodium(data);
    } catch (e) {
        const wrap = document.getElementById('leaderboard-table-wrap');
        if (wrap) {
            wrap.innerHTML = `<p class="leaderboard-empty">${escapeHtml(e.message || '加载失败')}</p>`;
        }
        const podium = document.getElementById('leaderboard-podium-wrap');
        if (podium) podium.innerHTML = '';
    } finally {
        if (loading) loading.style.display = 'none';
    }
}

function renderAchievementsGrid(g) {
    const grid = document.getElementById('achievements-grid');
    if (!grid) return;
    if (!g || !Array.isArray(g.achievements_all)) {
        grid.innerHTML = '<p class="achievements-empty">暂无成就数据</p>';
        return;
    }
    grid.innerHTML = g.achievements_all.map((a) => {
        const locked = !a.unlocked;
        const cls = locked ? 'achievement-card locked' : 'achievement-card';
        const when = a.unlocked_at
            ? `<span class="ach-when">${escapeHtml(String(a.unlocked_at).slice(0, 10))}</span>`
            : '';
        return `<div class="${cls}">
            <div class="ach-icon">${escapeHtml(a.icon || '🏅')}</div>
            <div class="ach-body">
                <div class="ach-title">${escapeHtml(a.title)}</div>
                <div class="ach-desc">${escapeHtml(a.desc)}</div>
                ${when}
            </div>
        </div>`;
    }).join('');
}

async function loadLeaderboardSection() {
    bindLeaderboardTabs();
    const loading = document.getElementById('leaderboard-loading');
    if (loading) loading.style.display = 'block';
    try {
        await refreshGamification();
        const introEl = document.getElementById('leaderboard-intro');
        if (introEl) {
            introEl.textContent = pickRandom(LEADERBOARD_INTRO_ROASTS);
        }
        const [data, pool, pkBoard] = await Promise.all([
            apiRequest('/leaderboard?scope=' + encodeURIComponent(leaderboardScope)),
            apiRequest('/monthly-pool'),
            apiRequest('/challenges/monthly-pk-board'),
        ]);
        document.querySelectorAll('[data-lb-scope]').forEach((b) => {
            const s = b.getAttribute('data-lb-scope');
            const on = s === leaderboardScope;
            b.classList.toggle('active', on);
            b.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        renderMonthlyPoolRace(pool);
        renderMonthlyPkBoard(pkBoard);
        const sc = data.scope || leaderboardScope;
        renderLeaderboardTable(data.leaderboard, sc, data, { expanded: false });
        renderLeaderboardPodium(data);
        renderAchievementsGrid(lastGamificationProfile);
        _markSectionLoaded('leaderboard');
    } catch (e) {
        const wrap = document.getElementById('leaderboard-table-wrap');
        if (wrap) {
            wrap.innerHTML = `<p class="leaderboard-empty">${escapeHtml(e.message || '加载失败')}</p>`;
        }
        const podium = document.getElementById('leaderboard-podium-wrap');
        if (podium) podium.innerHTML = '';
        const raceWrap = document.getElementById('monthly-pool-race-wrap');
        if (raceWrap) raceWrap.innerHTML = '';
        const pkWrap = document.getElementById('monthly-pk-board-wrap');
        if (pkWrap) pkWrap.innerHTML = '';
    } finally {
        if (loading) loading.style.display = 'none';
    }
}

function showMainBanner(message) {
    const el = document.getElementById('main-error-banner');
    if (!el) return;
    el.textContent = message || '';
    el.style.display = message ? 'block' : 'none';
    if (message) {
        setTimeout(() => {
            el.style.display = 'none';
            el.textContent = '';
        }, 5000);
    }
}

// ==================== 工具函数 ====================

function showError(message) {
    const errorDiv = document.getElementById('auth-error');
    errorDiv.textContent = message;
    errorDiv.style.display = 'block';
    setTimeout(() => {
        errorDiv.style.display = 'none';
    }, 3000);
}

function showAuthStatus(message) {
    const status = document.getElementById('auth-status');
    if (!status) return;
    status.textContent = message || '';
    status.style.display = message ? 'block' : 'none';
}

function switchAuthView(view) {
    const forms = {
        login: document.getElementById('login-form'),
        register: document.getElementById('register-form'),
        forgot: document.getElementById('forgot-password-form'),
        reset: document.getElementById('reset-password-form'),
    };
    Object.entries(forms).forEach(([name, form]) => {
        if (form) form.style.display = name === view ? 'block' : 'none';
    });
    const tabs = document.querySelector('.tabs');
    if (tabs) tabs.style.display = view === 'login' || view === 'register' ? 'flex' : 'none';
    document.querySelectorAll('.tab').forEach((tab) => {
        tab.classList.toggle('active', tab.dataset.tab === view);
    });
    const error = document.getElementById('auth-error');
    if (error) error.style.display = 'none';
    showAuthStatus('');
}

/** 词汇导入（VIP）接口返回拼装成可读说明（在服务端 message 基础上补充词条明细） */
function buildVocabImportFeedback(data) {
    let msg = data.message || '处理完成';
    const q = data.queue_result;
    if (q) {
        if (q.skipped_duplicate && q.skipped_duplicate > 0) {
            const dw = q.skipped_duplicate_words;
            if (Array.isArray(dw) && dw.length) {
                const show = dw.slice(0, 22);
                msg += ` 待复习重复：${show.join('、')}${dw.length > 22 ? '…' : ''}`;
            }
        }
        if (q.skipped_invalid && q.skipped_invalid > 0) {
            msg += ` 无效 ${q.skipped_invalid} 条已忽略。`;
        }
    }
    const aw = data.already_in_v2_words || data.already_in_csv_words;
    if (Array.isArray(aw) && aw.length) {
        const show = aw.slice(0, 18);
        msg += ` 新词库（words_v2）已有：${show.join('、')}${aw.length > 18 ? '…' : ''}`;
    }
    if (Array.isArray(data.failed) && data.failed.length) {
        const show = data.failed.slice(0, 10);
        msg += ` AI 生成失败（已记入疑难词）：${show.join('、')}${data.failed.length > 10 ? '…' : ''}`;
    }
    if (Array.isArray(data.blocked_surfaces) && data.blocked_surfaces.length) {
        const show = data.blocked_surfaces.slice(0, 12);
        msg += ` 疑难词（未调 AI）：${show.join('、')}${data.blocked_surfaces.length > 12 ? '…' : ''}`;
    }
    return msg;
}

let importResultModalPending = false;
let importResultModalOnClose = null;

function hideImportResultModalShell() {
    const modal = document.getElementById('import-result-modal');
    if (modal) {
        modal.style.display = 'none';
        modal.setAttribute('aria-hidden', 'true');
    }
    importResultModalPending = false;
    const card = document.getElementById('import-result-card');
    if (card) card.classList.remove('import-result-card--error');
}

function closeImportResultModal() {
    const fn = importResultModalOnClose;
    importResultModalOnClose = null;
    hideImportResultModalShell();
    if (typeof fn === 'function') fn();
}

/**
 * 统一导入结果弹窗：variant=stats 为待复习导入统计；variant=text 为长文案（词汇导入、错误提示等）
 */
function openImportResultModal(opts) {
    const {
        title = '导入结果',
        variant = 'stats',
        stats = {},
        text = '',
        isError = false,
        onClose = null,
    } = opts || {};
    const modal = document.getElementById('import-result-modal');
    const titleEl = document.getElementById('import-result-title');
    const statsWrap = document.getElementById('import-result-stats-wrap');
    const textWrap = document.getElementById('import-result-text-wrap');
    const textEl = document.getElementById('import-result-text');
    const card = document.getElementById('import-result-card');
    if (!modal) return;

    if (titleEl) titleEl.textContent = title;
    if (card) card.classList.toggle('import-result-card--error', !!isError);

    if (variant === 'stats') {
        if (statsWrap) statsWrap.hidden = false;
        if (textWrap) textWrap.hidden = true;
        const { total = 0, added = 0, skipped = 0, invalid = 0, dupWords = [] } = stats;
        const nSubmit = document.getElementById('import-result-n-submit');
        const nAdded = document.getElementById('import-result-n-added');
        const nDup = document.getElementById('import-result-n-dup');
        const nInvalid = document.getElementById('import-result-n-invalid');
        const dupList = document.getElementById('import-result-dup-list');
        if (nSubmit) nSubmit.textContent = String(total);
        if (nAdded) nAdded.textContent = String(added);
        if (nDup) nDup.textContent = String(skipped);
        if (nInvalid) nInvalid.textContent = String(invalid);
        if (dupList) {
            if (dupWords && dupWords.length) {
                dupList.hidden = false;
                const show = dupWords.slice(0, 40);
                dupList.textContent = `已在待复习列表中的词（示例）：${show.join('、')}${dupWords.length > 40 ? '…' : ''}`;
            } else {
                dupList.hidden = true;
                dupList.textContent = '';
            }
        }
    } else {
        if (statsWrap) statsWrap.hidden = true;
        if (textWrap) textWrap.hidden = false;
        if (textEl) textEl.textContent = text;
    }

    importResultModalOnClose = typeof onClose === 'function' ? onClose : null;
    importResultModalPending = true;
    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');
}

function showImportNotice(text, { title = '提示', isError = false } = {}) {
    openImportResultModal({
        variant: 'text',
        title,
        text: String(text || ''),
        isError,
    });
}

function initImportResultModal() {
    const ok = document.getElementById('import-result-ok');
    const modal = document.getElementById('import-result-modal');
    if (ok && !ok.dataset.bound) {
        ok.dataset.bound = '1';
        ok.addEventListener('click', () => closeImportResultModal());
    }
    if (modal && !modal.dataset.bound) {
        modal.dataset.bound = '1';
        modal.addEventListener('click', (e) => {
            if (
                e.target.classList.contains('import-result-backdrop') ||
                e.target.classList.contains('article-import-result-backdrop')
            ) {
                closeImportResultModal();
            }
        });
    }
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape' || !importResultModalPending) return;
        closeImportResultModal();
    });
}

const REVIEW_PHONETIC_STORAGE_KEY = 'english_reciter_review_show_phonetic';
const REVIEW_TEST_INFLECTION_STORAGE_KEY = 'english_reciter_review_test_inflection';

function getReviewTestInflectionEnabled() {
    const cb = document.getElementById('review-test-inflection');
    return Boolean(cb && cb.checked);
}

function updateReviewPhoneticDisplay(word) {
    const phEl = document.getElementById('current-word-phonetic');
    const cb = document.getElementById('review-show-phonetic');
    if (!phEl) return;
    const show = cb && cb.checked && word && String(word.phonetic || '').trim();
    if (show) {
        phEl.textContent = String(word.phonetic).trim();
        phEl.hidden = false;
    } else {
        phEl.textContent = '';
        phEl.hidden = true;
    }
}

// 生成提示字符串
function getHintString(word, revealedCount) {
    const wordText = (word._targetAnswer || word.english || '');
    if (revealedCount >= wordText.length) {
        return wordText;
    }
    const revealedPart = wordText.substring(0, revealedCount);
    const hiddenPart = '_'.repeat(wordText.length - revealedCount);
    return revealedPart + hiddenPart;
}

// 复习输入：仅字母/数字需用户键入；空格、撇号、连字符等作为固定占位显示
function isUnderlineTypeableChar(ch) {
    return /[a-zA-Z0-9]/.test(ch);
}

function applyTargetCasingToTypedChar(targetChar, typedLower) {
    if (!typedLower) return '';
    if (/[0-9]/.test(targetChar)) return typedLower;
    if (targetChar === targetChar.toUpperCase() && targetChar !== targetChar.toLowerCase()) {
        return typedLower.toUpperCase();
    }
    return typedLower.toLowerCase();
}

// 初始化下划线显示 + 透明输入层（桌面/移动端统一，可唤起软键盘）
function initializeUnderlineInput(word) {
    const target = (word.english || '').trim();
    initializeUnderlineInputForTarget(word, target);
}

function initializeUnderlineInputForTarget(word, target) {
    const container = document.getElementById('underline-input');
    const capture = document.getElementById('mobile-word-capture');
    if (!container || !capture) return;

    container.innerHTML = '';

    const chars = [...(target || '')];
    let typeableCount = 0;
    for (let i = 0; i < chars.length; i++) {
        const ch = chars[i];
        const charSpan = document.createElement('span');
        charSpan.dataset.index = String(i);
        if (isUnderlineTypeableChar(ch)) {
            typeableCount++;
            charSpan.className = 'underline-char empty';
            charSpan.dataset.role = 'letter';
            charSpan.dataset.targetChar = ch;
        } else {
            charSpan.className = 'underline-char fixed';
            charSpan.dataset.role = 'fixed';
            if (ch === ' ') {
                charSpan.classList.add('underline-fixed-space');
                charSpan.textContent = '\u00a0';
            } else {
                charSpan.textContent = ch;
            }
        }
        container.appendChild(charSpan);
    }

    container.dataset.targetText = target;
    container.dataset.wordLength = String(typeableCount);
    container.dataset.currentInput = '';

    capture.value = '';

    const syncFromCapture = () => {
        let v = capture.value.replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
        if (v.length > typeableCount) {
            v = v.slice(0, typeableCount);
        }
        capture.value = v;
        container.dataset.currentInput = v;
        updateUnderlineDisplay();
    };

    capture.oninput = syncFromCapture;
    capture.onkeydown = (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            submitAnswer();
        }
    };

    focusWordCapture(50);
    focusWordCapture(200);
}

// 更新下划线显示
function updateUnderlineDisplay() {
    const container = document.getElementById('underline-input');
    if (!container) return;

    const currentInput = container.dataset.currentInput || '';

    const letterSpans = container.querySelectorAll('.underline-char[data-role="letter"]');
    letterSpans.forEach((span, index) => {
        const tgt = span.dataset.targetChar || '';
        if (index < currentInput.length) {
            const c = currentInput[index];
            span.textContent = applyTargetCasingToTypedChar(tgt, c);
            span.className = 'underline-char filled';
        } else {
            span.textContent = '';
            span.className = 'underline-char empty';
        }
    });
}

// 获取当前输入值（含固定占位符；与下方 target 一致以便后端校验）
function getCurrentInput() {
    const container = document.getElementById('underline-input');
    if (!container) return '';
    const target = container.dataset.targetText || '';
    const typed = container.dataset.currentInput || '';
    let li = 0;
    let out = '';
    for (const ch of [...target]) {
        if (isUnderlineTypeableChar(ch)) {
            const c = li < typed.length ? typed[li] : '';
            out += c ? applyTargetCasingToTypedChar(ch, c) : '';
            li++;
        } else {
            out += ch;
        }
    }
    return out;
}

// 清空下划线输入
function clearUnderlineInput() {
    const container = document.getElementById('underline-input');
    const capture = document.getElementById('mobile-word-capture');
    if (!container) return;
    container.dataset.currentInput = '';
    if (capture) {
        capture.value = '';
    }
    updateUnderlineDisplay();
}

function stopSpeakPlayback() {
    ttsPlaybackGeneration++;
    if (ttsFetchAbort) {
        try {
            ttsFetchAbort.abort();
        } catch (_) {
            /* ignore */
        }
        ttsFetchAbort = null;
    }
    if (ttsPiperAudio) {
        try {
            ttsPiperAudio.pause();
            ttsPiperAudio.removeAttribute('src');
            ttsPiperAudio.load();
        } catch (_) {
            /* ignore */
        }
        ttsPiperAudio = null;
    }
    if (ttsPiperObjectUrl) {
        try {
            URL.revokeObjectURL(ttsPiperObjectUrl);
        } catch (_) {
            /* ignore */
        }
        ttsPiperObjectUrl = null;
    }
    try {
        if (typeof window.speechSynthesis !== 'undefined') {
            window.speechSynthesis.cancel();
        }
    } catch (_) {
        /* ignore */
    }
}

/** 暂停当前 Piper / 浏览器语音（不递增 generation，供课文全文朗读等场景） */
function pauseTtsPlayback() {
    try {
        if (ttsPiperAudio) {
            ttsPiperAudio.pause();
        }
    } catch (_) {
        /* ignore */
    }
    try {
        if (typeof window.speechSynthesis !== 'undefined' && window.speechSynthesis.speaking) {
            window.speechSynthesis.pause();
        }
    } catch (_) {
        /* ignore */
    }
}

/** 恢复暂停的朗读；若无暂停中的 Piper/语音则返回 false */
function resumeTtsPlayback() {
    try {
        if (ttsPiperAudio && ttsPiperAudio.paused) {
            void ttsPiperAudio.play();
            return true;
        }
    } catch (_) {
        /* ignore */
    }
    try {
        if (typeof window.speechSynthesis !== 'undefined' && window.speechSynthesis.paused) {
            window.speechSynthesis.resume();
            return true;
        }
    } catch (_) {
        /* ignore */
    }
    return false;
}

// 浏览器端朗读（远程访问时服务端 say 只在服务器出声，用户听不到）
// Android Chrome：语音列表异步加载、合成队列常处于 paused，需 resume + voiceschanged 后再 speak
/** @param {string} text @param {(() => void) | undefined} onEnd 朗读结束回调；省略时恢复复习输入框焦点 @param {number} [gen] 与 ttsPlaybackGeneration 对齐，防连点混音 */
function speakEnglishInBrowser(text, onEnd, gen) {
    const raw = String(text || '').trim().slice(0, 500);
    if (!raw) return false;
    if (typeof window.speechSynthesis === 'undefined') {
        return false;
    }
    const safe = [...raw]
        .filter((c) => {
            const cp = c.codePointAt(0);
            return (cp >= 32 && cp !== 127) || c === '\n' || c === '\t';
        })
        .join('')
        .trim()
        .slice(0, 500);
    if (!safe) return false;
    if (gen !== undefined && gen !== ttsPlaybackGeneration) return false;

    const synth = window.speechSynthesis;

    const pickEnglishVoice = () => {
        const voices = synth.getVoices();
        return (
            voices.find((v) => v.lang && /^en-us\b/i.test(String(v.lang))) ||
            voices.find((v) => v.lang && /^en\b/i.test(String(v.lang))) ||
            voices.find((v) => v.lang && String(v.lang).toLowerCase().startsWith('en'))
        );
    };

    const doSpeak = () => {
        if (gen !== undefined && gen !== ttsPlaybackGeneration) return;
        try {
            synth.cancel();
        } catch (_) {
            /* ignore */
        }
        try {
            synth.resume();
        } catch (_) {
            /* ignore */
        }

        const u = new SpeechSynthesisUtterance(safe);
        u.lang = 'en-US';
        const en = pickEnglishVoice();
        if (en) u.voice = en;
        u.rate = 0.95;
        const baseDone = typeof onEnd === 'function' ? onEnd : () => focusWordCapture(0);
        const onDone = () => {
            if (gen !== undefined && gen !== ttsPlaybackGeneration) return;
            baseDone();
        };
        u.onend = onDone;
        u.onerror = onDone;
        u.onstart = () => {
            try {
                synth.resume();
            } catch (_) {
                /* ignore */
            }
        };
        synth.speak(u);
        [50, 200].forEach((ms) => {
            setTimeout(() => {
                try {
                    synth.resume();
                } catch (_) {
                    /* ignore */
                }
            }, ms);
        });
    };

    let voices = synth.getVoices();
    if (voices.length === 0) {
        let settled = false;
        const runOnce = () => {
            if (gen !== undefined && gen !== ttsPlaybackGeneration) return;
            if (settled) return;
            settled = true;
            synth.removeEventListener('voiceschanged', runOnce);
            setTimeout(doSpeak, 0);
        };
        synth.addEventListener('voiceschanged', runOnce);
        setTimeout(runOnce, 600);
        return true;
    }

    setTimeout(doSpeak, 0);
    return true;
}

/** 仅请求 Piper WAV，不播放；供课文全文预取下一句等场景 */
async function fetchPiperAudioBlob(text, signal) {
    const raw = String(text || '').trim().slice(0, 500);
    if (!raw) return null;
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    let response;
    try {
        response = await fetch(`${API_BASE}/words/speak-audio`, {
            method: 'POST',
            headers,
            body: JSON.stringify({ text: raw }),
            signal,
        });
    } catch (_) {
        return null;
    }
    if (!response.ok) return null;
    const ct = response.headers.get('content-type') || '';
    if (!ct.includes('audio') && !ct.includes('octet-stream')) return null;
    let blob;
    try {
        blob = await response.blob();
    } catch (_) {
        return null;
    }
    if (!blob || blob.size < 100) return null;
    return blob;
}

/** 播放已拿到的 Piper WAV Blob；失败返回 false（不调用 onEnd） */
async function playPiperBlob(blob, onEnd, gen) {
    if (!blob || blob.size < 100) return false;
    const url = URL.createObjectURL(blob);
    if (gen !== undefined && gen !== ttsPlaybackGeneration) {
        try {
            URL.revokeObjectURL(url);
        } catch (_) {
            /* ignore */
        }
        return false;
    }
    const audio = new Audio(url);
    ttsPiperObjectUrl = url;
    ttsPiperAudio = audio;
    const cleanup = () => {
        try {
            URL.revokeObjectURL(url);
        } catch (_) {
            /* ignore */
        }
        if (ttsPiperAudio === audio) ttsPiperAudio = null;
        if (ttsPiperObjectUrl === url) ttsPiperObjectUrl = null;
        if (gen !== undefined && gen !== ttsPlaybackGeneration) return;
        if (typeof onEnd === 'function') onEnd();
    };
    audio.onended = cleanup;
    audio.onerror = cleanup;
    try {
        await audio.play();
    } catch (_) {
        try {
            URL.revokeObjectURL(url);
        } catch (_) {
            /* ignore */
        }
        if (ttsPiperAudio === audio) ttsPiperAudio = null;
        if (ttsPiperObjectUrl === url) ttsPiperObjectUrl = null;
        return false;
    }
    return true;
}

/** 使用服务端 Piper 返回的 WAV；失败返回 false（不调用 onEnd） */
async function speakEnglishViaPiperApi(text, onEnd, gen) {
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const ac = new AbortController();
    ttsFetchAbort = ac;
    let response;
    try {
        response = await fetch(`${API_BASE}/words/speak-audio`, {
            method: 'POST',
            headers,
            body: JSON.stringify({ text }),
            signal: ac.signal,
        });
    } catch (e) {
        if (ttsFetchAbort === ac) ttsFetchAbort = null;
        return false;
    }
    if (ttsFetchAbort === ac) ttsFetchAbort = null;
    if (gen !== undefined && gen !== ttsPlaybackGeneration) return false;
    if (!response.ok) return false;
    const ct = response.headers.get('content-type') || '';
    if (!ct.includes('audio') && !ct.includes('octet-stream')) return false;
    let blob;
    try {
        blob = await response.blob();
    } catch (_) {
        return false;
    }
    if (gen !== undefined && gen !== ttsPlaybackGeneration) return false;
    if (!blob || blob.size < 100) return false;
    return playPiperBlob(blob, onEnd, gen);
}

/** 优先 Piper，其次 Web Speech，最后服务端 say（仅服务器本机扬声器）
 * @param {string} text
 * @param {(() => void) | undefined} onEnd
 * @param {HTMLElement | null | undefined} triggerButton 触发朗读的 .btn-speak，用于加载态
 * @param {{ piperBlob?: Blob } | undefined} opts 可选：课文全文预取的 Piper Blob，跳过网络请求 */
async function speakEnglishPreferred(text, onEnd, triggerButton, opts = {}) {
    stopSpeakPlayback();
    endTtsSpeakUi();
    const myGen = ttsPlaybackGeneration;
    const raw = String(text || '').trim().slice(0, 500);
    if (!raw) {
        if (typeof onEnd === 'function') onEnd();
        return false;
    }
    beginTtsSpeakUi(triggerButton || null);
    const baseDone = typeof onEnd === 'function' ? onEnd : () => focusWordCapture(0);
    const done = () => {
        if (myGen !== ttsPlaybackGeneration) return;
        endTtsSpeakUi();
        baseDone();
    };
    if (serverPiperAvailable) {
        const pre = opts && opts.piperBlob;
        if (pre && pre.size > 100) {
            const okPre = await playPiperBlob(pre, done, myGen);
            if (okPre) return true;
        }
        const ok = await speakEnglishViaPiperApi(raw, done, myGen);
        if (ok) return true;
    }
    if (speakEnglishInBrowser(raw, done, myGen)) {
        return true;
    }
    try {
        await apiRequest('/words/speak', {
            method: 'POST',
            body: JSON.stringify({ text: raw }),
        });
        if (myGen !== ttsPlaybackGeneration) return false;
        done();
        return true;
    } catch (_) {
        if (myGen !== ttsPlaybackGeneration) return false;
        done();
        return false;
    }
}

async function refreshTtsCapabilities() {
    serverPiperAvailable = false;
    if (!token) return;
    try {
        const data = await apiRequest('/tts/capabilities');
        serverPiperAvailable = data && data.piper === true;
    } catch (_) {
        serverPiperAvailable = false;
    }
}

// 朗读例句
async function speakExample() {
    const word = currentReviewList[currentReviewIndex];
    if (!word) return;
    
    // 使用原始例句，而不是隐藏后的版本
    const exampleText = word.example;
    if (!exampleText || exampleText === '暂无例句') {
        return;
    }

    let enText = englishFromExampleField(exampleText);
    if (!enText) enText = String(exampleText).trim();
    if (!enText) return;

    await speakEnglishPreferred(enText, () => focusWordCapture(0), document.getElementById('speak-example-btn'));
}

/** 解析 JSON 响应；失败时抛出可读错误（Safari 对 SyntaxError 常显示为 “The string did not match the expected pattern.”） */
function parseApiJsonBody(text) {
    if (text == null || text === '') {
        return null;
    }
    try {
        return JSON.parse(text);
    } catch {
        throw new Error(
            '无法解析服务器响应（常见原因：网关超时、502/504，或返回了网页而非 JSON）。请稍后重试；批量词汇导入时可先减少单次词数。'
        );
    }
}

// API 请求
async function apiRequest(endpoint, options = {}) {
    const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData;
    const headers = {
        ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
        ...options.headers
    };
    
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }
    
    const response = await fetch(`${API_BASE}${endpoint}`, {
        ...options,
        headers
    });
    
    const text = await response.text();
    const data = parseApiJsonBody(text);

    if (!response.ok) {
        const error = data && typeof data === 'object' ? data : {};

        if (response.status === 401) {
            token = null;
            username = null;
            setSessionParentFlags(false, '');
            localStorage.removeItem('token');
            localStorage.removeItem('username');
            clearWordbankCsvDiscoveryCache();
            showLoginPage();
        }

        throw new Error(error.error || error.detail || '请求失败');
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
            wordbankCsvDiscoveryCache = { key: pathKey, etag, data };
        } catch (_) {
            /* 后台刷新失败不影响已缓存数据 */
        }
    })();
}

async function fetchWordbankCsvForDiscoveryNetwork(level, pathKey) {
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers.Authorization = `Bearer ${token}`;
    const cached = wordbankCsvDiscoveryCache;
    if (cached.key === pathKey && cached.etag && cached.data) {
        headers['If-None-Match'] = cached.etag;
    }
    const response = await fetch(`${API_BASE}${pathKey}`, {
        headers,
        cache: 'no-store',
    });
    if (response.status === 304) {
        if (cached.data && cached.key === pathKey) return cached.data;
        clearWordbankCsvDiscoveryCache();
        return fetchWordbankCsvForDiscovery(level);
    }
    if (response.status === 401) {
        token = null;
        username = null;
        setSessionParentFlags(false, '');
        localStorage.removeItem('token');
        localStorage.removeItem('username');
        clearWordbankCsvDiscoveryCache();
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
    wordbankCsvDiscoveryCache = { key: pathKey, etag, data };
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
        wordbankDiscoveryCsvInflight.delete(pathKey);
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

// ==================== 认证功能 ====================

async function login(loginUsername, password) {
    try {
        const formData = new FormData();
        formData.append('username', loginUsername);
        formData.append('password', password);

        const response = await fetch(`${API_BASE}/auth/login`, {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.error || data.detail || '登录失败');
        }

        token = data.access_token;
        username = data.username;
        setSessionParentFlags(!!data.is_parent, data.child_username || '');

        localStorage.setItem('token', token);
        localStorage.setItem('username', username);

        clearInviteRegistrationState();
        void showMainPage();
    } catch (error) {
        showError(error.message);
    }
}

async function register(username, password, email, inviteCode) {
    try {
        const data = await apiRequest('/auth/register', {
            method: 'POST',
            body: JSON.stringify({
                username,
                password,
                email,
                invite_code: inviteCode
            })
        });
        token = data.access_token;
        username = data.username;
        setSessionParentFlags(!!data.is_parent, data.child_username || '');
        localStorage.setItem('token', token);
        localStorage.setItem('username', username);
        clearInviteRegistrationState();
        void showMainPage();
    } catch (error) {
        showError(error.message);
    }
}

async function logout() {
    const logoutToken = token;
    if (typeof window.chatRoomOnLogout === 'function') window.chatRoomOnLogout();
    token = null;
    username = null;
    setSessionParentFlags(false, '');
    localStorage.removeItem('token');
    localStorage.removeItem('username');
    clearWordbankCsvDiscoveryCache();
    preloadedReviewData = null;
    resetArticleImportPickUI();
    closeSettings();
    showLoginPage();

    try {
        if (logoutToken) {
            await fetch(`${API_BASE}/auth/logout`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${logoutToken}`,
                    'Content-Type': 'application/json'
                }
            });
        }
    } catch (e) {
        /* 网络错误仍清除本地会话 */
    }
}


// ==================== 页面切换 ====================

function activateAuthTab(tabName) {
    const selectedTab = tabName === 'register' ? 'register' : 'login';
    switchAuthView(selectedTab);
}

function applyPendingInviteRegistration() {
    if (!pendingInviteRegistrationCode || (token && username)) return false;
    const inviteInput = document.getElementById('reg-invite');
    if (!inviteInput) return false;
    activateAuthTab('register');
    inviteInput.value = pendingInviteRegistrationCode;
    return true;
}

function handleInviteRegistrationNavigation() {
    const inviteCode = captureInviteRegistrationFromUrl();
    if (!inviteCode) return;
    if (token && username) {
        clearInviteRegistrationState();
        showMainBanner('当前已登录，邀请链接仅供新用户注册');
        return;
    }
    showLoginPage();
}

function showLoginPage() {
    resetInviteCardState();
    preloadedReviewData = null;
    document.getElementById('login-page').classList.add('active');
    document.getElementById('main-page').classList.remove('active');
    const gl = document.getElementById('admin-gear-login');
    if (gl) gl.style.display = '';
    document.querySelector('#main-page .nav-user')?.removeAttribute('aria-label');
    updateNavUserAvatar(null);
    updatePkNavBadgesFromDuels([]);
    closePkHubModal();
    stopPkInvitePolling();
    applyPendingInviteRegistration();
}

function applyParentNavMode() {
    const hide = isParentSession;
    ['review', 'discover', 'textbook'].forEach((page) => {
        document.querySelectorAll(`.nav-item[data-page="${page}"]`).forEach((el) => {
            el.style.display = hide ? 'none' : '';
        });
        document.querySelectorAll(`.mobile-tab[data-page="${page}"]`).forEach((el) => {
            el.style.display = hide ? 'none' : '';
        });
        document.querySelectorAll(`.mobile-more-link[data-page="${page}"]`).forEach((el) => {
            el.style.display = hide ? 'none' : '';
        });
    });
    document.querySelectorAll('#ng-pk-active, #mobile-ng-pk-active').forEach((el) => {
        if (el) el.style.display = hide ? 'none' : '';
    });
    document.querySelectorAll('.js-invite-create').forEach((el) => {
        if (el) el.style.display = hide ? 'none' : '';
    });
    const lb = document.getElementById('leaderboard-opt-in');
    const lab = lb?.closest('label');
    if (lab) lab.style.display = hide ? 'none' : '';
}

function hideSystemBroadcastModal() {
    const m = document.getElementById('system-broadcast-modal');
    if (m) {
        m.style.display = 'none';
        m.setAttribute('aria-hidden', 'true');
    }
    systemBroadcastPendingId = null;
}

function showSystemBroadcastModal(payload) {
    if (!payload || !payload.id || !payload.message) return;
    systemBroadcastPendingId = payload.id;
    const body = document.getElementById('system-broadcast-body');
    if (body) body.textContent = payload.message;
    const m = document.getElementById('system-broadcast-modal');
    if (m) {
        m.style.display = 'flex';
        m.setAttribute('aria-hidden', 'false');
    }
}

function maybeShowSystemBroadcast(raw) {
    if (!raw || !raw.id || !raw.message) return;
    showSystemBroadcastModal(raw);
}

function applySummaryStats(summary) {
    const stats = summary && summary.stats ? summary.stats : {};
    const due =
        typeof summary?.due_count === 'number'
            ? summary.due_count
            : typeof stats.due_count === 'number'
              ? stats.due_count
              : 0;
    const reviewEl = document.getElementById('review-count');
    const masteredEl = document.getElementById('mastered-count');
    const roundEl = document.getElementById('round-count');
    if (reviewEl) reviewEl.textContent = String(due);
    if (masteredEl) masteredEl.textContent = String(stats.mastered_words || 0);
    if (roundEl) roundEl.textContent = String((Number(stats.current_round) || 0) + 1);
    if (document.getElementById('progress-section')?.classList.contains('active')) {
        renderLearningMap(stats.mastered_words || 0);
    }
}

function applyBootstrapPayload(boot) {
    if (!boot || typeof boot !== 'object') return;
    if (boot.session) {
        setSessionParentFlags(!!boot.session.is_parent, boot.session.child_username || '');
        maybeShowSystemBroadcast(boot.session.system_broadcast);
    }
    if (boot.summary) {
        applySummaryStats(boot.summary);
    }
    if (boot.review && Array.isArray(boot.review.words)) {
        preloadedReviewData = boot.review;
    }
    if (boot.gamification) {
        lastGamificationProfile = boot.gamification;
        updateGamificationNav(boot.gamification);
        const opt = document.getElementById('leaderboard-opt-in');
        if (opt && typeof boot.gamification.leaderboard_opt_in === 'boolean') {
            opt.checked = boot.gamification.leaderboard_opt_in;
        }
    }
    updateNavUserAvatar(boot.avatar_url || null);
    if (boot.plan) {
        userPlan = boot.plan || 'free';
        articleAiExtractAvailable = boot.article_ai_extract_available === true;
        articleAiExtractEnabled = boot.article_ai_extract_enabled === true;
        updatePlanUI();
    }
    if (boot.tts_capabilities && typeof boot.tts_capabilities.piper === 'boolean') {
        serverPiperAvailable = boot.tts_capabilities.piper;
    }
}

async function showMainPage() {
    document.getElementById('login-page').classList.remove('active');
    document.getElementById('main-page').classList.add('active');
    const gl = document.getElementById('admin-gear-login');
    if (gl) gl.style.display = 'none';
    preloadedReviewData = null;
    let boot = null;
    if (token) {
        try {
            boot = await apiRequest('/bootstrap');
            applyBootstrapPayload(boot);
        } catch (bootErr) {
            try {
                const d = await apiRequest('/auth/session');
                setSessionParentFlags(!!d.is_parent, d.child_username || '');
                maybeShowSystemBroadcast(d.system_broadcast);
            } catch (_) {
                /* 保留本地 session 标记 */
            }
        }
    }
    const udisp = document.getElementById('username-display');
    if (udisp) {
        udisp.textContent =
            isParentSession && childUsername ? `${childUsername}（家长查看）` : username;
    }
    document.querySelector('#main-page .nav-user')?.setAttribute(
        'aria-label',
        isParentSession && childUsername
            ? `家长查看 ${childUsername}`
            : username
              ? `当前用户 ${username}`
              : ''
    );

    applyParentNavMode();

    if (!boot || !boot.summary) {
        loadStats();
    }
    if (!boot || !('avatar_url' in boot)) {
        refreshNavUserAvatar();
    }
    startPkInvitePolling();
    if (!boot || !boot.plan) {
        loadUserPlan();
    }
    if (isParentSession) {
        showSection('progress');
    } else {
        showSection('review');
    }
    runWhenIdle(() => {
        void refreshPkInviteIndicator();
        if (typeof window.chatRoomEnsureSse === 'function') window.chatRoomEnsureSse();
    }, 5000);
}

async function loadUserPlan() {
    try {
        const data = await apiRequest('/user/plan');
        userPlan = data.plan || 'free';
        articleAiExtractAvailable = data.article_ai_extract_available === true;
        articleAiExtractEnabled = data.article_ai_extract_enabled === true;
        updatePlanUI();
    } catch (_) {
        userPlan = 'free';
        articleAiExtractEnabled = false;
    }
    await refreshTtsCapabilities();
}

function updatePlanUI() {
    const hint = document.getElementById('article-plan-hint');
    if (hint) {
        if (userPlan === 'paid') {
            hint.textContent = '（VIP：课文导入统一使用 spaCy 分词；管理员可在后台开启 AI 分词）';
            hint.className = 'plan-hint vip';
        } else {
            hint.textContent = '（免费版：按空格分词；可勾选智能还原匹配不规则词形）';
            hint.className = 'plan-hint free';
        }
    }
    const importNcHint = document.getElementById('import-nc-lesson-hint');
    if (importNcHint) {
        importNcHint.style.display = '';
        if (userPlan === 'paid') {
            importNcHint.textContent = '选择课文后，课文英文全文将填入下方文本框；再次选择其它课文会替换当前内容。';
        } else {
            importNcHint.textContent =
                '下拉列表与「课文学习」一致：非 VIP 每册仅列出前 10 篇。选择后课文英文全文会填入下方；再次选择会替换当前内容。';
        }
    }
    const spacyWrap = document.getElementById('import-article-spacy-wrap');
    const vipExtractWrap = document.getElementById('import-article-vip-extract-wrap');
    const aiRadio = document.getElementById('import-article-extract-ai');
    const spacyModeRadio = document.getElementById('import-article-extract-spacy');
    const adminTok = typeof sessionStorage !== 'undefined' ? sessionStorage.getItem('adminToken') : null;
    const showVipAiChoice =
        userPlan === 'paid' &&
        articleAiExtractEnabled &&
        articleAiExtractAvailable &&
        !!adminTok;
    if (spacyWrap) {
        if (userPlan === 'paid') {
            spacyWrap.hidden = true;
        } else {
            spacyWrap.hidden = false;
        }
    }
    if (vipExtractWrap) {
        if (showVipAiChoice) {
            vipExtractWrap.hidden = false;
            if (aiRadio && spacyModeRadio) {
                aiRadio.disabled = false;
                spacyModeRadio.checked = true;
                aiRadio.checked = false;
            }
        } else {
            vipExtractWrap.hidden = true;
            if (aiRadio && spacyModeRadio) {
                spacyModeRadio.checked = true;
                aiRadio.checked = false;
            }
        }
    }

    const vocabPanel = document.getElementById('import-vocab-panel');
    const vocabLocked = document.getElementById('import-vocab-locked');
    const vocabBtn = document.getElementById('import-vocab-btn');
    if (vocabPanel) {
        if (userPlan === 'paid') {
            if (vocabLocked) vocabLocked.style.display = 'none';
            if (vocabBtn) vocabBtn.style.display = '';
        } else {
            if (vocabLocked) vocabLocked.style.display = 'block';
            if (vocabBtn) vocabBtn.style.display = 'none';
        }
    }
    if (textbookCatalogCache && document.getElementById('textbook-section')?.classList.contains('active')) {
        renderTextbookCatalog(textbookCatalogCache);
    }
    if (document.getElementById('import-section')?.classList.contains('active')) {
        void ensureImportNceLessonOptions();
    }
}

/** 窄屏复习页专用样式（见 style.css html.is-review-section） */
function syncReviewSectionChromeClass() {
    const rs = document.getElementById('review-section');
    const on = !!(rs && rs.classList.contains('active'));
    document.documentElement.classList.toggle('is-review-section', on);
}

function showSection(sectionId) {
    closeDailySummaryPopover();

    _saveScrollPos(_currentSectionId);

    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    const sectionElement = document.getElementById(sectionId + '-section');
    if (sectionElement) {
        sectionElement.classList.add('active');
    }

    document.querySelectorAll('.nav-item').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.mobile-tab').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.mobile-more-link').forEach(btn => btn.classList.remove('active'));

    document.querySelectorAll(`.nav-item[data-page="${sectionId}"]`).forEach(el => el.classList.add('active'));
    document.querySelectorAll(`.mobile-tab[data-page="${sectionId}"]`).forEach(el => el.classList.add('active'));
    document.querySelectorAll(`.mobile-more-link[data-page="${sectionId}"]`).forEach(el => el.classList.add('active'));

    const moreBtn = document.getElementById('mobile-more-btn');
    if (moreBtn && (sectionId === 'progress' || sectionId === 'mastered' || sectionId === 'discover' || sectionId === 'textbook')) {
        moreBtn.classList.add('active');
    }

    closeMobileMoreSheet();
    _currentSectionId = sectionId;
    syncReviewSectionChromeClass();

    if (sectionId === 'review') {
        if (!restoreActiveReviewSession()) {
            loadReviewList();
        }
    } else if (sectionId === 'discover') {
        prefetchDiscoveryWordbankForSearch();
        loadDiscovery();
    } else if (sectionId === 'textbook') {
        loadTextbookSection();
    } else if (sectionId === 'progress') {
        if (_isSectionFresh('progress')) { _restoreScrollPos('progress'); return; }
        loadProgress();
    } else if (sectionId === 'mastered') {
        if (_isSectionFresh('mastered')) { _restoreScrollPos('mastered'); return; }
        loadMastered();
    } else if (sectionId === 'leaderboard') {
        if (_isSectionFresh('leaderboard')) { _restoreScrollPos('leaderboard'); return; }
        loadLeaderboardSection();
    } else if (sectionId === 'import') {
        void ensureImportNceLessonOptions();
    }
}


// ==================== 统计功能 ====================

async function loadStats() {
    _invalidateSectionCache('progress');
    _invalidateSectionCache('mastered');
    _invalidateSectionCache('leaderboard');
    try {
        const data = await apiRequest('/words/summary');
        applySummaryStats(data);
    } catch (error) {
        showMainBanner('加载统计失败，请稍后重试');
    }
    try {
        await refreshGamification();
        if (isSettingsOverlayOpen() && lastGamificationProfile) {
            updateSettingsCheckinHintFromProfile(lastGamificationProfile);
            updateSettingsMonthlyGoalBonusNotice(lastGamificationProfile);
        }
    } catch (_) {
        /* 积分接口失败不影响复习 */
    }
}

function normalizeEnglishKey(s) {
    return String(s || '').trim().toLowerCase();
}

/**
 * 从当前复习列表中移除若干词后，新下标：仍指向原「当前题」或其后第一个未被删词；若已无题则等于 length（由 showCurrentWord → onPassComplete 收尾）。
 */
function computeNewReviewIndexAfterWordRemoval(oldList, oldIdx, removedSet, newList) {
    if (newList.length === 0) return 0;
    if (oldIdx >= oldList.length) return newList.length;
    for (let j = oldIdx; j < oldList.length; j++) {
        const w = oldList[j];
        if (!removedSet.has(normalizeEnglishKey(w.english))) {
            const idx = newList.findIndex(
                (x) => normalizeEnglishKey(x.english) === normalizeEnglishKey(w.english),
            );
            if (idx >= 0) return idx;
        }
    }
    return newList.length;
}

/**
 * 从当前复习会话中抠掉已在服务端删除的待复习词，尽量保留题序与进度（不整表重载）。
 * @param {string[]} removedEnglishList 服务端返回的 removed_english（可合并多批）
 */
function applyPendingRemovalToReviewSession(removedEnglishList) {
    if (!Array.isArray(removedEnglishList) || removedEnglishList.length === 0) return;

    const removedSet = new Set(
        removedEnglishList.map((s) => normalizeEnglishKey(s)).filter(Boolean),
    );
    if (removedSet.size === 0) return;

    const reviewBox = document.getElementById('review-box');
    const reviewComplete = document.getElementById('review-complete');
    const modal = document.getElementById('remedial-offer-modal');
    const modalOpen = modal && modal.style.display === 'flex';

    const pruneWordMapRemoved = () => {
        for (const [k] of [...wordMap.entries()]) {
            if (removedSet.has(normalizeEnglishKey(k))) {
                wordMap.delete(k);
            }
        }
    };

    const wrongBefore = wrongWordsOrder.length;
    wrongWordsOrder = wrongWordsOrder.filter((en) => !removedSet.has(normalizeEnglishKey(en)));
    wrongWordsInThisPass = new Set(wrongWordsOrder);
    sessionMainFailedThree = Math.max(0, sessionMainFailedThree - (wrongBefore - wrongWordsOrder.length));
    pruneWordMapRemoved();

    if (modalOpen && wrongRoundNumber === 0 && reviewSessionMode === 'daily') {
        const countEl = document.getElementById('remedial-offer-count');
        if (countEl) countEl.textContent = String(wrongWordsOrder.length);
        renderRemedialOfferWrongList();
        if (wrongWordsOrder.length === 0) {
            closeRemedialOfferModal();
            showFinalComplete();
        }
        return;
    }

    const sessionEndedUi =
        !modalOpen &&
        reviewComplete &&
        reviewComplete.style.display !== 'none' &&
        reviewBox &&
        reviewBox.style.display === 'none';
    if (sessionEndedUi) {
        return;
    }

    const finishMainRoundEmpty = () => {
        if (wrongWordsOrder.length > 0) {
            if (reviewBox) reviewBox.style.display = 'none';
            if (reviewComplete) reviewComplete.style.display = 'none';
            showRemedialOfferModal();
        } else {
            showFinalComplete();
        }
    };

    const applyListUpdate = (oldList, oldIdx) => {
        const newList = oldList.filter((w) => !removedSet.has(normalizeEnglishKey(w.english)));
        const removedInList = oldList.length - newList.length;
        if (removedInList === 0) return null;
        currentReviewList = newList;
        currentReviewIndex = computeNewReviewIndexAfterWordRemoval(oldList, oldIdx, removedSet, newList);
        currentReviewList.forEach((w) => wordMap.set(w.english, w));
        isSubmitting = false;
        isAdvancing = false;
        return newList;
    };

    if (reviewSessionMode === 'bonus') {
        const oldList = currentReviewList;
        const oldIdx = currentReviewIndex;
        const removedInList = oldList.filter((w) => removedSet.has(normalizeEnglishKey(w.english))).length;
        if (removedInList === 0) {
            renderWrongPanel();
            return;
        }
        sessionInitialMainWords = Math.max(0, sessionInitialMainWords - removedInList);
        const newList = applyListUpdate(oldList, oldIdx);
        if (!newList) return;
        if (newList.length === 0) {
            showFinalComplete();
            return;
        }
        if (reviewBox) reviewBox.style.display = 'block';
        if (reviewComplete) reviewComplete.style.display = 'none';
        showReviewEmptyActions(false);
        renderWrongPanel();
        updateWrongRoundLabel();
        void showCurrentWord();
        return;
    }

    if (reviewSessionMode !== 'daily') return;

    if (wrongRoundNumber === 0) {
        const oldList = currentReviewList;
        const oldIdx = currentReviewIndex;
        const removedInMain = oldList.filter((w) => removedSet.has(normalizeEnglishKey(w.english))).length;
        if (removedInMain === 0) {
            renderWrongPanel();
            updateWrongRoundLabel();
            return;
        }
        sessionInitialMainWords = Math.max(0, sessionInitialMainWords - removedInMain);
        const newList = applyListUpdate(oldList, oldIdx);
        if (!newList) return;
        if (newList.length === 0) {
            finishMainRoundEmpty();
            return;
        }
        if (reviewBox) reviewBox.style.display = 'block';
        if (reviewComplete) reviewComplete.style.display = 'none';
        showReviewEmptyActions(false);
        renderWrongPanel();
        updateWrongRoundLabel();
        void showCurrentWord();
        return;
    }

    const oldList = currentReviewList;
    const oldIdx = currentReviewIndex;
    const removedInList = oldList.filter((w) => removedSet.has(normalizeEnglishKey(w.english))).length;
    if (removedInList === 0) {
        renderWrongPanel();
        updateWrongRoundLabel();
        return;
    }
    const newList = applyListUpdate(oldList, oldIdx);
    if (!newList) return;
    if (newList.length === 0) {
        wrongWordsOrder = [];
        wrongWordsInThisPass = new Set();
        showFinalComplete();
        return;
    }
    if (reviewBox) reviewBox.style.display = 'block';
    if (reviewComplete) reviewComplete.style.display = 'none';
    renderWrongPanel();
    updateWrongRoundLabel();
    void showCurrentWord();
}

/**
 * 待复习队列在服务端减少后，同步导航统计与当前页中依赖该队列的视图（否则需整页刷新才一致）。
 * @param {string[]} [removedEnglishList] 服务端实际移除的词，用于复习页就地抠词；不传则仅刷新统计与其它页。
 */
async function syncMainPageAfterPendingWordsRemoved(removedCount, removedEnglishList) {
    try {
        await loadStats();
    } catch (_) {
        /* loadStats 内部已提示 */
    }
    if (!(removedCount > 0)) return;
    try {
        if (document.getElementById('review-section')?.classList.contains('active')) {
            if (Array.isArray(removedEnglishList) && removedEnglishList.length > 0) {
                applyPendingRemovalToReviewSession(removedEnglishList);
            } else {
                await loadReviewList();
            }
        }
        if (document.getElementById('discover-section')?.classList.contains('active') && discoveryModeToday) {
            await loadDiscovery();
        }
    } catch (_) {
        /* apply / loadReviewList / loadDiscovery 内部已提示 */
    }
}

// ==================== 复习功能 ====================

function recordWrongAttempt(word) {
    const en = word.english;
    if (!wrongWordsInThisPass.has(en)) {
        wrongWordsInThisPass.add(en);
        wrongWordsOrder.push(en);
    }
    wordMap.set(en, word);
    renderWrongPanel();
}

function buildWrongWordItemHtml(w) {
    return `<span class="ww-en">${escapeHtml(w.english)}</span><span class="ww-zh">${escapeHtml(w.chinese)}</span>`;
}

function renderWrongPanel() {
    const ul = document.getElementById('wrong-words-list');
    const empty = document.getElementById('wrong-words-empty');
    if (!ul || !empty) return;
    ul.innerHTML = '';
    for (const en of wrongWordsOrder) {
        const w = wordMap.get(en);
        if (!w) continue;
        const li = document.createElement('li');
        li.innerHTML = buildWrongWordItemHtml(w);
        ul.appendChild(li);
    }
    empty.style.display = wrongWordsOrder.length === 0 ? 'block' : 'none';
    const badge = document.getElementById('wrong-words-count-badge');
    if (badge) {
        badge.textContent = String(wrongWordsOrder.length);
    }
}

function renderRemedialOfferWrongList() {
    const wrap = document.getElementById('remedial-offer-words');
    const ul = document.getElementById('remedial-offer-words-list');
    const empty = document.getElementById('remedial-offer-words-empty');
    const count = document.getElementById('remedial-offer-list-count');
    if (!wrap || !ul || !empty) return;

    ul.innerHTML = '';
    let rendered = 0;
    for (const en of wrongWordsOrder) {
        const w = wordMap.get(en);
        if (!w) continue;
        const li = document.createElement('li');
        li.innerHTML = buildWrongWordItemHtml(w);
        ul.appendChild(li);
        rendered += 1;
    }
    if (count) count.textContent = String(rendered);
    wrap.hidden = rendered === 0;
    ul.hidden = rendered === 0;
    empty.hidden = rendered !== 0;
}

function isRemedialOfferModalOpen() {
    const modal = document.getElementById('remedial-offer-modal');
    return !!(modal && modal.style.display === 'flex');
}

function hasActiveReviewSession() {
    const hasPendingCard = currentReviewList.length > 0 && currentReviewIndex < currentReviewList.length;
    return hasPendingCard || wrongWordsOrder.length > 0 || wrongRoundNumber > 0 || isRemedialOfferModalOpen();
}

function restoreActiveReviewSession() {
    if (!hasActiveReviewSession()) return false;

    showReviewEmptyActions(false);
    renderWrongPanel();
    updateWrongRoundLabel();

    const reviewBox = document.getElementById('review-box');
    const reviewComplete = document.getElementById('review-complete');
    if (isRemedialOfferModalOpen()) {
        if (reviewBox) reviewBox.style.display = 'none';
        if (reviewComplete) reviewComplete.style.display = 'none';
        renderRemedialOfferWrongList();
        return true;
    }

    if (currentReviewList.length > 0 && currentReviewIndex < currentReviewList.length) {
        if (reviewBox) reviewBox.style.display = 'block';
        if (reviewComplete) reviewComplete.style.display = 'none';
        focusWordCapture(60);
        return true;
    }

    if (wrongWordsOrder.length > 0) {
        showRemedialOfferModal();
        return true;
    }

    return false;
}

function updateWrongRoundLabel() {
    const el = document.getElementById('wrong-round-label');
    if (!el) return;
    if (wrongRoundNumber === 0) {
        el.textContent = '同一单词 3 次尝试均错后会出现在这里';
    } else {
        el.textContent = `错题复习 · 第 ${wrongRoundNumber} 轮`;
    }
}

function resetSessionReviewStats() {
    sessionInitialMainWords = 0;
    sessionMainCorrect = 0;
    sessionMainFailedThree = 0;
    sessionRemedialCorrect = 0;
    sessionTotalWrongAttempts = 0;
    sessionNewMastered = [];
}

function hideReviewSessionSummary() {
    const box = document.getElementById('review-session-summary');
    if (!box) return;
    box.innerHTML = '';
    box.hidden = true;
}

function showReviewEmptyActions(show) {
    const el = document.getElementById('review-empty-actions');
    if (!el) return;
    el.hidden = !show;
}

/**
 * 当次复习结束时的文字总结（主轮 / 错题巩固 / 新掌握 / 命中率等）
 * @param {number} remedialRoundsDone 结束时处于第几轮错题复习（0 表示未进入错题轮）
 * @param {boolean} isBonus 是否为无待复习时的随机加练
 */
function buildReviewSessionSummaryHtml(remedialRoundsDone, isBonus) {
    const n = sessionInitialMainWords;
    const mainOk = sessionMainCorrect;
    const mainFailed = sessionMainFailedThree;
    const remedialOk = sessionRemedialCorrect;
    const wrongTries = sessionTotalWrongAttempts;
    const correctTries = mainOk + remedialOk;
    const totalTries = correctTries + wrongTries;
    const accPct = totalTries > 0 ? Math.round((correctTries / totalTries) * 1000) / 10 : 0;

    const parts = [];
    if (isBonus) {
        parts.push(
            `<div class="review-summary-section"><strong>加练主轮</strong>：本轮共 ${n} 个词；` +
            `答对 ${mainOk} 个；${mainFailed} 个曾 3 次均未答对并进入错题巩固。（答对仅计复习次数）</div>`
        );
    } else {
        parts.push(
            `<div class="review-summary-section"><strong>主轮</strong>：今日待复习共 ${n} 个词；` +
            `在本轮流程中答对 ${mainOk} 个；${mainFailed} 个曾 3 次均未答对并进入错题巩固。</div>`
        );
    }

    if (remedialRoundsDone > 0) {
        parts.push(
            `<div class="review-summary-section"><strong>错题巩固</strong>：共完成 ${remedialRoundsDone} 轮；` +
            `错题轮累计答对 ${remedialOk} 次（含同一词多次练习）。</div>`
        );
    } else if (mainFailed > 0 && sessionSkippedRemedialAfterMain) {
        parts.push(
            `<div class="review-summary-section"><strong>错题巩固</strong>：主轮有 ${mainFailed} 个词 3 次均未答对，你已选择「稍后再说」，本次未进行错题巩固。</div>`
        );
    } else {
        parts.push(
            `<div class="review-summary-section"><strong>错题巩固</strong>：未触发，所有词在主轮已过关。</div>`
        );
    }

    const masteredUnique = [...new Set(sessionNewMastered)];
    if (masteredUnique.length > 0) {
        const names = masteredUnique.map((en) => escapeHtml(en)).join('、');
        parts.push(`<div class="review-summary-section"><strong>新掌握</strong>：${names}</div>`);
    }

    parts.push(
        `<div class="review-summary-section"><strong>本次作答</strong>：共 ${totalTries} 次提交，` +
        `其中答错 ${wrongTries} 次，答对率约 ${accPct}%。</div>`
    );

    let tip = '';
    if (isBonus) {
        if (wrongTries === 0 && mainFailed === 0) {
            tip = '加练全对，词汇保持活跃。';
        } else {
            tip = '加练不影响正常排期；需要时可随时再点「随机加练」。';
        }
    } else if (wrongTries === 0 && mainFailed === 0) {
        tip = '全对通过，保持节奏即可。';
    } else if (mainFailed > 0 && sessionSkippedRemedialAfterMain) {
        tip = '可随时点击「继续学习」再次进入今日复习流程；错题仍会在后续排期中再次出现。';
    } else if (mainFailed > 0 || remedialRoundsDone > 1) {
        tip = '错题已巩固完成；不熟悉的词可在「学习进度」里查看下次复习时间。';
    } else if (wrongTries > 0) {
        tip = '有拼写失误属正常，间隔复习会帮助巩固。';
    }

    if (tip) {
        parts.push(`<p class="review-summary-tip">${escapeHtml(tip)}</p>`);
    }

    return `<div class="review-summary-inner">${parts.join('')}</div>`;
}

function getWrongOrder() {
    const radios = document.querySelectorAll('input[name="wrong-order"]');
    for (const r of radios) {
        if (r.checked) return r.value;
    }
    return 'random';
}

/** Fisher-Yates 原地打乱数组 */
function shuffleArray(arr) {
    for (let i = arr.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [arr[i], arr[j]] = [arr[j], arr[i]];
    }
}

/**
 * 按当前排序设置重排 currentReviewList 中「还没做的」部分（index 之后的），
 * 已经做过的（index 之前）保持不动。
 */
function applyWrongOrderToRemaining() {
    if (!wrongRoundNumber) return; // 主轮不干预
    const done = currentReviewList.slice(0, currentReviewIndex);
    let remaining = currentReviewList.slice(currentReviewIndex);
    if (remaining.length <= 1) return;
    if (getWrongOrder() === 'random') {
        shuffleArray(remaining);
    } else {
        // 'field'：按单词在 wrongWordsOrder 中原始出现顺序排
        // 用 wordMap 记录出现先后（以英文为键，值为首次出现的序号）
        const orderIndex = new Map();
        let idx = 0;
        for (const [en] of wordMap) {
            if (!orderIndex.has(en)) orderIndex.set(en, idx++);
        }
        remaining.sort((a, b) => (orderIndex.get(a.english) ?? 9999) - (orderIndex.get(b.english) ?? 9999));
    }
    currentReviewList = [...done, ...remaining];
}

/** 进入下一轮错题复习（主轮结束后需用户确认时由弹框调用，或错题轮结束后自动调用） */
function enterRemedialRound() {
    wrongRoundNumber += 1;
    const n = wrongWordsOrder.length;
    const msg = wrongRoundNumber === 1
        ? `本轮有 ${n} 个单词 3 次均未答对，即将开始错题复习`
        : `进入第 ${wrongRoundNumber} 轮错题复习（${n} 个单词）`;
    showMainBanner(msg);

    let orderedList = wrongWordsOrder.map((en) => wordMap.get(en)).filter(Boolean);
    if (getWrongOrder() === 'random') {
        shuffleArray(orderedList);
    }

    currentReviewList = orderedList;
    wrongWordsOrder = [];
    wrongWordsInThisPass = new Set();

    if (currentReviewList.length === 0) {
        showFinalComplete();
        return;
    }

    currentReviewIndex = 0;
    document.getElementById('review-box').style.display = 'block';
    document.getElementById('review-complete').style.display = 'none';
    renderWrongPanel();
    updateWrongRoundLabel();
    setTimeout(() => showCurrentWord(), 400);
}

function closeRemedialOfferModal() {
    const modal = document.getElementById('remedial-offer-modal');
    if (modal) {
        modal.style.display = 'none';
        modal.setAttribute('aria-hidden', 'true');
    }
}

/** 当日主轮结束且有错题：弹框询问，不自动进入错题复习 */
function showRemedialOfferModal() {
    const n = wrongWordsOrder.length;
    const countEl = document.getElementById('remedial-offer-count');
    if (countEl) countEl.textContent = String(n);
    renderRemedialOfferWrongList();
    const modal = document.getElementById('remedial-offer-modal');
    if (modal) {
        modal.style.display = 'flex';
        modal.setAttribute('aria-hidden', 'false');
    }
    document.getElementById('review-box').style.display = 'none';
    document.getElementById('review-complete').style.display = 'none';
}

function onRemedialOfferAccept() {
    closeRemedialOfferModal();
    sessionSkippedRemedialAfterMain = false;
    enterRemedialRound();
}

function onRemedialOfferDecline() {
    closeRemedialOfferModal();
    wrongWordsOrder = [];
    wrongWordsInThisPass = new Set();
    sessionSkippedRemedialAfterMain = true;
    showFinalComplete();
}

/** 一轮题目做完：无错题则结束；主轮有错题时弹框确认；错题轮之间仍自动进入下一轮 */
function onPassComplete() {
    if (wrongWordsOrder.length === 0) {
        showFinalComplete();
        return;
    }
    if (wrongRoundNumber === 0 && reviewSessionMode === 'daily') {
        showRemedialOfferModal();
        return;
    }
    enterRemedialRound();
}

function showFinalComplete() {
    document.getElementById('review-box').style.display = 'none';
    document.getElementById('review-complete').style.display = 'block';
    const titleEl = document.getElementById('review-complete-title');
    const descEl = document.getElementById('review-complete-desc');
    const summaryEl = document.getElementById('review-session-summary');
    const isBonus = reviewSessionMode === 'bonus';
    if (titleEl) {
        if (!isBonus && sessionSkippedRemedialAfterMain) {
            titleEl.textContent = '今日主轮复习完成';
        } else {
            titleEl.textContent = isBonus ? '加练完成！' : '今日复习完成！';
        }
    }
    if (descEl) {
        if (!isBonus && sessionSkippedRemedialAfterMain) {
            descEl.textContent = '你已选择暂不进行错题巩固，可随时点击「继续学习」再次进入复习。';
        } else {
            descEl.textContent = isBonus
                ? '本次随机加练已完成（含错题巩固）。'
                : '恭喜！今日待复习已全部完成（含错题巩固）。';
        }
    }
    const remedialRoundsDone = wrongRoundNumber;
    showReviewEmptyActions(false);
    if (summaryEl) {
        summaryEl.innerHTML = buildReviewSessionSummaryHtml(remedialRoundsDone, isBonus);
        summaryEl.hidden = false;
    }
    wrongWordsOrder = [];
    wrongWordsInThisPass = new Set();
    wrongRoundNumber = 0;
    sessionSkippedRemedialAfterMain = false;
    renderWrongPanel();
    updateWrongRoundLabel();
    loadStats();
}

function showInitialEmptyReview() {
    document.getElementById('review-box').style.display = 'none';
    document.getElementById('review-complete').style.display = 'block';
    const titleEl = document.getElementById('review-complete-title');
    const descEl = document.getElementById('review-complete-desc');
    if (titleEl) titleEl.textContent = '今日暂无待复习';
    if (descEl) {
        descEl.textContent = '目前没有到期的复习任务。你可以随机加练 5 个词保持手感，或去导入新词。';
    }
    hideReviewSessionSummary();
    reviewSessionMode = 'daily';
    showReviewEmptyActions(true);
    sessionSkippedRemedialAfterMain = false;
    wrongWordsOrder = [];
    wrongWordsInThisPass = new Set();
    wrongRoundNumber = 0;
    wordMap = new Map();
    renderWrongPanel();
    updateWrongRoundLabel();
}

async function loadReviewList() {
    try {
        sessionSkippedRemedialAfterMain = false;
        wrongWordsInThisPass = new Set();
        wrongWordsOrder = [];
        wrongRoundNumber = 0;
        wordMap = new Map();
        isSubmitting = false;
        isAdvancing = false;
        resetSessionReviewStats();
        hideReviewSessionSummary();
        reviewSessionMode = 'daily';

        const data = preloadedReviewData || await apiRequest('/words/review');
        preloadedReviewData = null;
        currentReviewList = data.words;
        currentReviewIndex = 0;
        currentReviewList.forEach((w) => wordMap.set(w.english, w));
        sessionInitialMainWords = currentReviewList.length;

        if (currentReviewList.length === 0) {
            showInitialEmptyReview();
        } else {
            showReviewEmptyActions(false);
            document.getElementById('review-box').style.display = 'block';
            document.getElementById('review-complete').style.display = 'none';
            renderWrongPanel();
            updateWrongRoundLabel();
            showCurrentWord();
        }
    } catch (error) {
        showMainBanner('加载复习列表失败，请稍后重试');
    }
}

async function startBonusReview() {
    try {
        const data = await apiRequest('/words/extra-review');
        if (!data.words || data.words.length === 0) {
            showMainBanner('词库为空，请先导入单词');
            return;
        }
        sessionSkippedRemedialAfterMain = false;
        wrongWordsInThisPass = new Set();
        wrongWordsOrder = [];
        wrongRoundNumber = 0;
        wordMap = new Map();
        isSubmitting = false;
        isAdvancing = false;
        resetSessionReviewStats();
        hideReviewSessionSummary();
        reviewSessionMode = 'bonus';

        currentReviewList = data.words;
        currentReviewIndex = 0;
        currentReviewList.forEach((w) => wordMap.set(w.english, w));
        sessionInitialMainWords = currentReviewList.length;

        showReviewEmptyActions(false);
        document.getElementById('review-box').style.display = 'block';
        document.getElementById('review-complete').style.display = 'none';
        renderWrongPanel();
        updateWrongRoundLabel();
        showCurrentWord();
    } catch (error) {
        showMainBanner('加载加练列表失败，请稍后重试');
    }
}

async function showCurrentWord() {
    if (currentReviewIndex >= currentReviewList.length) {
        onPassComplete();
        return;
    }
    
    const word = currentReviewList[currentReviewIndex];
    
    // 重置状态
    currentErrorCount = 0;
    currentRevealedCount = 0;
    isSubmitting = false;
    isAdvancing = false;

    // 未勾选「考察语态」：只考词库原形；勾选：须拼例句中实际形式（与 CSV example*_form / pick 一致）
    const lemma = (word.english || '').trim();
    const inflected = (word.example_form || '').trim() || lemma;
    const targetAnswer = getReviewTestInflectionEnabled() ? inflected : lemma;
    word._targetAnswer = targetAnswer;
    
    const dueHint = document.getElementById('review-due-hint');
    if (dueHint) {
        if (reviewSessionMode === 'bonus') {
            dueHint.hidden = false;
            dueHint.className = 'review-due-hint review-due-hint-bonus';
            dueHint.textContent = '随机加练（不改变掌握进度与排期）';
        } else if (word.is_carryover) {
            dueHint.hidden = false;
            dueHint.className = 'review-due-hint review-due-hint-carryover';
            const d = Number(word.carryover_days) > 0 ? Number(word.carryover_days) : 1;
            dueHint.textContent = `此前未按计划完成 · 已逾期 ${d} 天（排在今日排期之后）`;
        } else {
            dueHint.hidden = false;
            dueHint.className = 'review-due-hint review-due-hint-scheduled';
            dueHint.textContent = '今日排期复习';
        }
    }

    // 显示中文意思；多义词条：释义单独一行，例句在下方（见 .review-content--polyseme）
    const rcInner = document.getElementById('review-content-inner');
    if (rcInner) {
        rcInner.classList.toggle('review-content--polyseme', !!word.review_polyseme);
    }
    document.getElementById('current-word-chinese').textContent = word.chinese;
    const maxSucc = word.max_success_count != null ? word.max_success_count : 8;
    document.getElementById('current-word-progress').textContent = `${word.success_count}/${maxSucc}`;
    
    const exEl = document.getElementById('current-word-example');
    const multiHtml = buildReviewExamplesDisplayHtml(word);
    if (multiHtml) {
        exEl.innerHTML = multiHtml;
    } else {
        exEl.textContent = formatReviewQuizExampleDisplay(word);
    }
    
    // 提示字符串基于本题目标词长度（原形或句中形式）
    const hintString = getHintStringForTarget(targetAnswer, currentRevealedCount);
    document.getElementById('current-word-english').textContent = hintString;
    updateReviewPhoneticDisplay(word);
    
    // 绑定朗读按钮事件
    const speakBtn = document.getElementById('speak-example-btn');
    if (speakBtn) {
        speakBtn.onclick = speakExample;
    }
    
    // 初始化下划线输入框（基于 targetAnswer 的长度）
    initializeUnderlineInputForTarget(word, targetAnswer);
    focusWordCapture(0);

    // 清空消息
    document.getElementById('word-message').style.display = 'none';
    clearReviewWordMessageExtra();
}

/** 根据 target（变形或原形）生成提示字符串 */
function getHintStringForTarget(target, revealedCount) {
    if (!target) return '';
    if (revealedCount >= target.length) return target;
    return target.substring(0, revealedCount) + '_'.repeat(target.length - revealedCount);
}

/** 清空「其余义项」桌面端气泡（复习提交区） */
function clearReviewWordMessageExtra() {
    const el = document.getElementById('word-message-extra');
    if (!el) return;
    el.innerHTML = '';
    el.classList.remove('word-message-extra--show');
}

/**
 * 答对后展示其余义项 + 例句（仅 CSS 宽屏断点可见，移动端不占位）
 * @param {Array<{ zh: string, example_en?: string, example_cn?: string }>} items
 */
function renderReviewOtherSensesExtra(items) {
    const el = document.getElementById('word-message-extra');
    if (!el) return;
    if (!items || !items.length) {
        clearReviewWordMessageExtra();
        return;
    }
    let html = '<div class="word-message-extra-title">其余义项</div>';
    let n = 0;
    for (let i = 0; i < items.length; i += 1) {
        const it = items[i];
        const zh = String(it.zh || '').trim();
        if (!zh) continue;
        n += 1;
        html += '<div class="word-message-extra-item">';
        html += `<div class="word-message-extra-zh">${escapeHtml(zh)}</div>`;
        const en = String(it.example_en || '').trim();
        const cn = String(it.example_cn || '').trim();
        if (en || cn) {
            html += '<div class="word-message-extra-ex">';
            if (en) html += `<span class="word-message-extra-en">${escapeHtml(en)}</span>`;
            if (en && cn) html += ' ';
            if (cn) html += `<span class="word-message-extra-cn">${escapeHtml(cn)}</span>`;
            html += '</div>';
        }
        html += '</div>';
    }
    if (n === 0) {
        clearReviewWordMessageExtra();
        return;
    }
    el.innerHTML = html;
    el.classList.add('word-message-extra--show');
}

async function submitAnswer() {
    if (isSubmitting || isAdvancing) return;
    const answer = getCurrentInput();
    const word = currentReviewList[currentReviewIndex];
    
    if (!answer) {
        focusWordCapture(0);
        return;
    }

    isSubmitting = true;
    const submitBtn = document.getElementById('submit-answer');
    if (submitBtn) submitBtn.classList.add('btn-submit-loading');
    
    try {
        const result = await apiRequest('/words/practice', {
            method: 'POST',
            body: JSON.stringify({
                word_id: word.english,
                answer: answer,
                remedial: wrongRoundNumber > 0 && reviewSessionMode !== 'bonus',
                bonus_practice: reviewSessionMode === 'bonus',
                test_inflection: getReviewTestInflectionEnabled(),
            })
        });

        if (result.word) {
            Object.assign(word, result.word);
            wordMap.set(word.english, word);
        }

        if (!result.correct) {
            sessionTotalWrongAttempts += 1;
        } else {
            if (wrongRoundNumber === 0) {
                sessionMainCorrect += 1;
            } else {
                sessionRemedialCorrect += 1;
            }
            if (result.message && String(result.message).includes('已掌握')) {
                sessionNewMastered.push(word.english);
            }
        }

        const messageDiv = document.getElementById('word-message');
        let msgText = result.message;
        clearReviewWordMessageExtra();
        if (result.correct && result.gamification) {
            const gm = result.gamification;
            const gainedXp = Number(gm.xp_gained) || 0;
            const checkinBonusXp = Number(gm.checkin_bonus_xp) || 0;
            if (gainedXp > 0) {
                msgText += ` +${formatNumber(gainedXp)} XP（累计 ${formatNumber(gm.total_xp)} · Lv.${gm.level} · 连续 ${gm.streak} 天）`;
            } else {
                msgText += `（累计 ${formatNumber(gm.total_xp)} · Lv.${gm.level} · 连续 ${gm.streak} 天）`;
            }
            if (checkinBonusXp > 0) {
                msgText += ` · 今日打卡奖励 +${formatNumber(checkinBonusXp)} XP`;
            }
            if (gm.monthly_goal_bonus_xp > 0) {
                msgText += ` · 打卡目标额外奖励 +${formatNumber(gm.monthly_goal_bonus_xp)} XP`;
            }
            if (gm.new_achievements && gm.new_achievements.length) {
                msgText += ` · 新成就：${gm.new_achievements.map((x) => x.title).join('、')}`;
            }
            lastGamificationProfile = { ...(lastGamificationProfile || {}), ...gm };
            if (gm.monthly_goal_bonus_xp > 0) {
                lastGamificationProfile.monthly_goal_bonus_awarded_this_month = true;
            }
            updateGamificationNav(lastGamificationProfile);
            if (isSettingsOverlayOpen()) {
                updateSettingsCheckinHintFromProfile(lastGamificationProfile);
                updateMakeupCheckinDialogFromProfile(lastGamificationProfile);
                updateSettingsMonthlyGoalBonusNotice(lastGamificationProfile);
            }
        }
        messageDiv.textContent = msgText;
        messageDiv.className = `word-message ${result.correct ? 'success' : 'error'}`;
        messageDiv.style.display = 'block';
        if (result.correct && result.other_senses_extra && result.other_senses_extra.length) {
            renderReviewOtherSensesExtra(result.other_senses_extra);
        }
        if (submitBtn) submitBtn.classList.remove('btn-submit-loading');
        
        const targetAnswer = word._targetAnswer || word.english;
        if (result.correct) {
            // 答案正确，显示完整答案，然后进入下一个单词（多义：多留 1 秒便于读完释义+例句）
            document.getElementById('current-word-english').textContent = targetAnswer;
            isAdvancing = true;
            const advanceMs = word.review_polyseme ? 2500 : 1500;
            setTimeout(() => {
                isSubmitting = false;
                isAdvancing = false;
                currentReviewIndex++;
                showCurrentWord();
                loadStats();
            }, advanceMs);
        } else {
            // 答案错误
            currentErrorCount++;
            
            // 每次错误多揭示一个字母
            if (currentRevealedCount < targetAnswer.length) {
                currentRevealedCount++;
            }
            
            if (currentErrorCount >= 3) {
                // 3 次尝试均错：记入本轮错题栏
                if (wrongRoundNumber === 0) {
                    sessionMainFailedThree += 1;
                }
                recordWrongAttempt(word);
                document.getElementById('current-word-english').textContent = targetAnswer;
                isAdvancing = true;
                setTimeout(() => {
                    isSubmitting = false;
                    isAdvancing = false;
                    currentReviewIndex++;
                    showCurrentWord();
                    loadStats();
                }, 1500);
            } else {
                // 还有尝试机会，更新提示字符串
                const targetAnswer = word._targetAnswer || word.english;
                const hintString = getHintStringForTarget(targetAnswer, currentRevealedCount);
                document.getElementById('current-word-english').textContent = hintString;
                
                // 显示剩余次数
                messageDiv.textContent = `${result.message} (还剩 ${3 - currentErrorCount} 次尝试机会)`;
                // 清空下划线输入框，让用户重新输入
                clearUnderlineInput();
                isSubmitting = false;
                focusWordCapture(0);
                focusWordCapture(100);
            }
        }
    } catch (error) {
        isSubmitting = false;
        isAdvancing = false;
        if (submitBtn) submitBtn.classList.remove('btn-submit-loading');
        const msg = error.message || '提交失败，请重试';
        const reviewSection = document.getElementById('review-section');
        const messageDiv = document.getElementById('word-message');
        if (reviewSection && reviewSection.classList.contains('active') && messageDiv) {
            clearReviewWordMessageExtra();
            messageDiv.textContent = msg;
            messageDiv.className = 'word-message error';
            messageDiv.style.display = 'block';
            focusWordCapture(0);
        } else {
            showError(msg);
        }
    }
}

// ==================== 学习地图（按已掌握词数里程碑） ====================

/** 里程碑：level = 级别，n = 至少已掌握词数（终极 4000） */
const LEARNING_MAP_ROW_SIZE = 4;

const LEARNING_MAP_MILESTONES = [
    { level: 1, n: 0, title: '启程', icon: '🌱' },
    { level: 2, n: 50, title: '词汇新星', icon: '⭐' },
    { level: 3, n: 150, title: '稳步积累', icon: '🌿' },
    { level: 4, n: 300, title: '进阶之路', icon: '🎯' },
    { level: 5, n: 600, title: '词汇能手', icon: '🏆' },
    { level: 6, n: 1000, title: '精通', icon: '💎' },
    { level: 7, n: 1500, title: '专家', icon: '👑' },
    { level: 8, n: 2000, title: '大师', icon: '🎖️' },
    { level: 9, n: 2500, title: '传奇', icon: '🏅' },
    { level: 10, n: 3000, title: '史诗', icon: '✨' },
    { level: 11, n: 3500, title: '神话', icon: '🌠' },
    { level: 12, n: 4000, title: '词海终极', icon: '🌟' },
];

/** 学习地图里程碑（与 XP 的 Lv. 区分，使用「阶」） */
function learningMapStageLabel(level) {
    return `${level} 阶`;
}

function learningMapRenderNode(ms, m, nextGoal) {
    const unlocked = m >= ms.n;
    const isNext = Boolean(nextGoal && ms.n === nextGoal.n);
    const stateClass = unlocked ? 'learning-map-node--unlocked' : 'learning-map-node--locked';
    const nextClass = isNext ? ' learning-map-node--next' : '';
    const countLabel = ms.n === 0 ? '起点' : `${formatNumber(ms.n)} 词`;
    const stage = learningMapStageLabel(ms.level);
    const aria = `第 ${ms.level} 阶 ${ms.title}，目标 ${ms.n === 0 ? '0' : formatNumber(ms.n)} 词，${
        unlocked ? '已达成' : '未达成'
    }`;
    const veilHtml = unlocked
        ? ''
        : '<div class="learning-map-node-veil" aria-hidden="true"></div>';
    return `
        <div class="learning-map-node ${stateClass}${nextClass}" role="group" aria-label="${escapeHtml(aria)}">
            <div class="learning-map-node-box">
                <span class="learning-map-node-level">${stage}</span>
                <div class="learning-map-node-bubble" aria-hidden="true">
                    <span class="learning-map-node-icon">${ms.icon}</span>
                </div>
                <span class="learning-map-node-count">${countLabel}</span>
                <span class="learning-map-node-title">${escapeHtml(ms.title)}</span>
            </div>
            ${veilHtml}
        </div>
    `;
}

function renderLearningMap(masteredWords) {
    const root = document.getElementById('learning-map');
    const hint = document.getElementById('learning-map-hint');
    if (!root) return;

    const m = Math.max(0, Number(masteredWords) || 0);

    let nextGoal = null;
    for (const ms of LEARNING_MAP_MILESTONES) {
        if (ms.n > 0 && m < ms.n) {
            nextGoal = ms;
            break;
        }
    }

    if (hint) {
        if (nextGoal) {
            const remain = nextGoal.n - m;
            hint.textContent = `当前已掌握 ${formatNumber(m)} 词 · 下一目标 ${learningMapStageLabel(
                nextGoal.level,
            )}「${nextGoal.title}」（${formatNumber(nextGoal.n)} 词）还需 ${formatNumber(remain)} 词 · 终极 ${learningMapStageLabel(
                12,
            )} 为 ${formatNumber(4000)} 词`;
        } else {
            hint.textContent = `当前已掌握 ${formatNumber(m)} 词 · 已达成 ${learningMapStageLabel(
                12,
            )} 词海终极（${formatNumber(4000)} 词），继续保持！`;
        }
    }

    const list = LEARNING_MAP_MILESTONES;
    const rowsHtml = [];
    for (let start = 0; start < list.length; start += LEARNING_MAP_ROW_SIZE) {
        const chunk = list.slice(start, start + LEARNING_MAP_ROW_SIZE);
        const rowIndex = Math.floor(start / LEARNING_MAP_ROW_SIZE);
        const dir = rowIndex % 2 === 0 ? 'ltr' : 'rtl';
        const parts = [];
        chunk.forEach((ms, i) => {
            parts.push(learningMapRenderNode(ms, m, nextGoal));
            if (i < chunk.length - 1) {
                parts.push('<div class="lm-seg lm-seg--h" aria-hidden="true"></div>');
            }
        });
        rowsHtml.push(`<div class="learning-map-row learning-map-row--${dir}">${parts.join('')}</div>`);
        if (start + LEARNING_MAP_ROW_SIZE < list.length) {
            const nextRowRtl = (rowIndex + 1) % 2 === 1;
            rowsHtml.push(
                `<div class="lm-row-join lm-row-join--${nextRowRtl ? 'to-rtl' : 'to-ltr'}" aria-hidden="true">
                    <div class="lm-row-join-curve"></div>
                </div>`,
            );
        }
    }

    root.innerHTML = `<div class="learning-map-rows">${rowsHtml.join('')}</div>`;
}

// ==================== 进度功能 ====================

function _renderProgressWordItem(word) {
    const nextLine = escapeHtml(formatNextReviewLine(word));
    const phoneticHtml = word.phonetic ? `<span class="word-item-phonetic">${escapeHtml(word.phonetic)}</span>` : '';
    const coTag = word.is_carryover
        ? `<span class="word-item-carryover-tag">遗留${word.carryover_days ? ` · 逾期${word.carryover_days}天` : ''}</span>`
        : '';
    return `<div class="word-item">
        <div class="word-item-info">
            <div class="word-item-english">${escapeHtml(word.english)}${phoneticHtml}</div>
            <div class="word-item-chinese">${escapeHtml(word.chinese)}</div>
            <div class="word-item-next-review">下次复习：${nextLine} ${coTag}</div>
        </div>
        <div class="word-item-stats">
            <div class="word-stat">
                <div class="word-stat-value">${escapeHtml(word.success_count)}/${escapeHtml(word.max_success_count)}</div>
                <div class="word-stat-label">掌握进度</div>
            </div>
            <div class="word-stat">
                <div class="word-stat-value">${escapeHtml(word.review_count)}</div>
                <div class="word-stat-label">复习次数</div>
            </div>
        </div>
    </div>`;
}

const _LIST_BATCH_SIZE = 50;

function _batchRenderList(container, items, renderFn) {
    let rendered = 0;
    function renderBatch() {
        const slice = items.slice(rendered, rendered + _LIST_BATCH_SIZE);
        if (!slice.length) return;
        container.insertAdjacentHTML('beforeend', slice.map(renderFn).join(''));
        rendered += slice.length;
    }
    renderBatch();
    if (rendered < items.length) {
        const io = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting && rendered < items.length) {
                renderBatch();
                if (rendered >= items.length) { io.disconnect(); sentinel.remove(); }
            }
        }, { rootMargin: '200px' });
        const sentinel = document.createElement('div');
        sentinel.className = 'section-loading-hint';
        sentinel.textContent = '加载更多…';
        container.appendChild(sentinel);
        io.observe(sentinel);
    }
}

async function loadProgress() {
    const wordList = document.getElementById('word-list');
    const statsEl = document.getElementById('progress-stats');
    if (wordList) wordList.innerHTML = '<p class="section-loading-hint">加载中…</p>';
    try {
        const data = await apiRequest('/words/status');

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
                    <div class="stat-label">平均复习次数</div>
                    <div class="stat-value">${data.stats.avg_review_count.toFixed(1)}</div>
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
        showMainBanner('加载进度失败，请稍后重试');
    }
}

// ==================== 已掌握功能 ====================

function _renderMasteredWordItem(word) {
    const phoneticHtml = word.phonetic ? `<span class="word-item-phonetic">${escapeHtml(word.phonetic)}</span>` : '';
    return `<div class="word-item">
        <div class="word-item-info">
            <div class="word-item-english">${escapeHtml(word.english)}${phoneticHtml}</div>
            <div class="word-item-chinese">${escapeHtml(word.chinese)}</div>
        </div>
        <div class="word-item-stats">
            <div class="word-stat">
                <div class="word-stat-value">${escapeHtml(word.review_count)}</div>
                <div class="word-stat-label">复习次数</div>
            </div>
        </div>
    </div>`;
}

async function loadMastered() {
    const container = document.getElementById('mastered-list');
    if (container) container.innerHTML = '<p class="section-loading-hint">加载中…</p>';
    try {
        const data = await apiRequest('/words/mastered');

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
        const added = data.added || 0;
        const skipped = data.skipped_duplicate || 0;
        if (added > 0) {
            discoveryPendingKeys.add(k);
            showMainBanner(`「${w.english}」已加入待复习`);
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
                discoveryPendingKeys = new Set((st.words || []).map((x) => discoveryEnglishKey(x.english)));
                discoveryMasteredKeys = new Set((masteredRes.words || []).map((x) => discoveryEnglishKey(x.english)));
            } catch (_) {
                /* 保持本地缓存 */
            }
            showMainBanner('该词已在待复习列表或已掌握中，无需重复导入。');
            loadStats();
            renderDiscoveryCard();
            return;
        }
        showMainBanner(data.message || '未能加入待复习');
    } catch (err) {
        showMainBanner(err.message || '导入失败');
    } finally {
        discoveryImportBusy = false;
        if (btn.isConnected) {
            btn.disabled = false;
            btn.textContent = prevLabel;
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
    const emptyEl = document.getElementById('discovery-empty');
    const rootEl = document.getElementById('discovery-root');
    const searchQ = (document.getElementById('discovery-search')?.value || '').trim();
    if (searchQ) {
        await loadDiscoverySearchFromQuery(searchQ);
        return;
    }
    cancelDiscoverySearchRequest();
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
            const part = items.slice(i, i + chunk);
            const data = await apiRequest('/words/import-json', {
                method: 'POST',
                body: JSON.stringify(part)
            });
            added += data.added || 0;
            skipped += data.skipped_duplicate || 0;
            invalid += data.skipped_invalid || 0;
            if (Array.isArray(data.skipped_duplicate_words)) {
                dupWords.push(...data.skipped_duplicate_words);
            }
        }
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
        showImportNotice(error.message || '导入失败', { isError: true });
        wbState.importBusy = false;
        if (importBtn) {
            importBtn.disabled = false;
            importBtn.textContent = '将选中的词加入待复习';
        }
    } finally {
        if (importBtn && !importResultModalPending) {
            importBtn.disabled = false;
            importBtn.textContent = '将选中的词加入待复习';
        }
        if (!importResultModalPending) {
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
let importVocabBusy = false;
/** @type {Set<number>} */
let articleImportSelectedIdx = new Set();
/** 导入成功且结果对话框已打开时，finally 不再恢复「确认导入」按钮 */
function resetArticleImportPickUI() {
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
            const part = items.slice(i, i + chunk);
            const res = await apiRequest('/words/import-json', {
                method: 'POST',
                body: JSON.stringify(part),
            });
            added += res.added || 0;
            skipped += res.skipped_duplicate || 0;
            invalid += res.skipped_invalid || 0;
            if (Array.isArray(res.skipped_duplicate_words)) {
                dupWords.push(...res.skipped_duplicate_words);
            }
        }
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
                resetArticleImportPickUI();
                loadStats();
                articleConfirmImportBusy = false;
            },
        });
    } catch (error) {
        showImportNotice(error.message || '导入失败', { isError: true });
        articleConfirmImportBusy = false;
        if (btnA) {
            btnA.disabled = false;
            btnA.textContent = '确认导入';
        }
    } finally {
        if (articleImportPickMode && !importResultModalPending) {
            if (btnA) {
                btnA.disabled = false;
                btnA.textContent = '确认导入';
            }
        }
        if (!importResultModalPending) {
            articleConfirmImportBusy = false;
        }
    }
}

async function importFromArticle() {
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
        const data = await apiRequest('/words/import-from-article', {
            method: 'POST',
            body: JSON.stringify(body),
            headers: extraHeaders,
        });

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
        renderArticleUnmatched([]);
        showImportNotice(error.message || '提取失败', { isError: true });
        if (resultDiv) resultDiv.style.display = 'none';
    } finally {
        if (btnA) btnA.disabled = false;
        articleImportBusy = false;
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

/** 从图片 OCR 填入「从文章导入」文本框（整段 raw_text） */
async function runImportOcrToTextarea(file) {
    if (importOcrBusy) return;
    if (!file || !file.size) {
        showImportNotice('请选择有效的图片文件', { isError: true });
        return;
    }
    importOcrBusy = true;
    const btn = document.getElementById('import-ocr-pick-img-btn');
    const ta = document.getElementById('import-article-textarea');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '识别中…';
    }
    const fd = new FormData();
    fd.append('file', file);
    try {
        const data = await apiRequest('/wordbank/ocr-extract', { method: 'POST', body: fd });
        const raw = data.raw_text != null ? String(data.raw_text).trim() : '';
        if (ta) {
            ta.value = raw;
        }
        if (!raw) {
            showImportNotice(
                '未识别到文字。可换一张更清晰的图片，或检查服务端是否已安装 Tesseract。',
                { title: '图片识别完成', isError: false }
            );
        } else {
            showImportNotice('已填入识别文本，可编辑后点击「从文章提取词汇」。', {
                title: '图片识别完成',
                isError: false,
            });
        }
    } catch (error) {
        showImportNotice(error.message || '识别失败', { title: '图片识别失败', isError: true });
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '选择图片…';
        }
        importOcrBusy = false;
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

async function importVocabToCSV() {
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
        const data = await apiRequest('/wordbank/csv/import-words', {
            method: 'POST',
            body: JSON.stringify({ words: wordsPayload, level, also_add_to_queue: alsoAddToQueue })
        });
        openImportResultModal({
            variant: 'text',
            title: '词汇导入结果',
            text: buildVocabImportFeedback(data),
            onClose: () => {
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
        showImportNotice(error.message || '导入失败', { isError: true });
        importVocabBusy = false;
        if (btn) {
            btn.disabled = false;
            btn.textContent = '词汇导入';
        }
    } finally {
        if (btn && !importResultModalPending) {
            btn.disabled = false;
            btn.textContent = '词汇导入';
        }
        if (!importResultModalPending) {
            importVocabBusy = false;
        }
    }
}

// ==================== 事件监听与初始化 ====================

document.addEventListener('DOMContentLoaded', function() {
    mountDeferredAppShell();
    syncReviewSectionChromeClass();
    setupVisualViewportKeyboardAvoid();
    setupWrongWordsAsideToggle();
    setupDailySummaryPopover();
    setupXpHistoryModal();

    document.querySelectorAll('.btn-toggle-pw').forEach((btn) => {
        btn.addEventListener('click', () => {
            const targetId = btn.getAttribute('data-target');
            const inp = targetId && document.getElementById(targetId);
            if (!inp) return;
            const show = inp.type === 'password';
            inp.type = show ? 'text' : 'password';
            btn.setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
        });
    });

    // 预加载语音列表（Android 等环境首次 getVoices() 可能为空）
    if (typeof window.speechSynthesis !== 'undefined') {
        const prime = () => {
            try {
                window.speechSynthesis.getVoices();
            } catch (_) {
                /* ignore */
            }
        };
        prime();
        window.speechSynthesis.addEventListener('voiceschanged', prime);
    }

    const phoneticCb = document.getElementById('review-show-phonetic');
    if (phoneticCb) {
        phoneticCb.checked = localStorage.getItem(REVIEW_PHONETIC_STORAGE_KEY) === '1';
        phoneticCb.addEventListener('change', () => {
            localStorage.setItem(REVIEW_PHONETIC_STORAGE_KEY, phoneticCb.checked ? '1' : '0');
            const word = currentReviewList[currentReviewIndex];
            if (word) updateReviewPhoneticDisplay(word);
        });
    }

    const inflectionCb = document.getElementById('review-test-inflection');
    if (inflectionCb) {
        inflectionCb.checked = localStorage.getItem(REVIEW_TEST_INFLECTION_STORAGE_KEY) === '1';
        inflectionCb.addEventListener('change', () => {
            localStorage.setItem(REVIEW_TEST_INFLECTION_STORAGE_KEY, inflectionCb.checked ? '1' : '0');
            const word = currentReviewList[currentReviewIndex];
            if (word && document.getElementById('review-section')?.classList.contains('active')) {
                void showCurrentWord();
            }
        });
    }

    // 登录表单
    const loginForm = document.getElementById('login-form');
    if (loginForm) {
        loginForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const username = document.getElementById('login-username').value;
            const password = document.getElementById('login-password').value;
            await login(username, password);
        });
    }
    
    // 注册表单
    const registerForm = document.getElementById('register-form');
    if (registerForm) {
        registerForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const username = document.getElementById('reg-username').value;
            const password = document.getElementById('reg-password').value;
            const passwordConfirm = document.getElementById('reg-password-confirm').value;
            const email = document.getElementById('reg-email').value || null;
            const inviteCode = (document.getElementById('reg-invite') || {}).value || '';

            if (password !== passwordConfirm) {
                showError('两次密码输入不一致');
                return;
            }
            if (!inviteCode.trim()) {
                showError('请填写邀请码');
                return;
            }

            await register(username, password, email, inviteCode.trim());
        });
    }

    document.getElementById('forgot-password-open')?.addEventListener('click', () => {
        const loginUsername = document.getElementById('login-username')?.value?.trim() || '';
        const forgotEmail = document.getElementById('forgot-email');
        if (forgotEmail && loginUsername.includes('@')) forgotEmail.value = loginUsername;
        switchAuthView('forgot');
        forgotEmail?.focus();
    });
    document.querySelectorAll('.auth-back-login').forEach((button) => {
        button.addEventListener('click', () => switchAuthView('login'));
    });
    document.getElementById('forgot-password-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const email = document.getElementById('forgot-email')?.value?.trim() || '';
        const submit = e.currentTarget.querySelector('button[type="submit"]');
        if (submit) submit.disabled = true;
        try {
            const data = await apiRequest('/auth/forgot-password', {
                method: 'POST',
                body: JSON.stringify({ email }),
            });
            showAuthStatus(data.message || '如果该邮箱已绑定账号，重置邮件将在几分钟内发送');
        } catch (error) {
            showError(error.message || '发送失败');
        } finally {
            if (submit) submit.disabled = false;
        }
    });
    document.getElementById('reset-password-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const password = document.getElementById('reset-password')?.value || '';
        const passwordConfirm = document.getElementById('reset-password-confirm')?.value || '';
        if (password !== passwordConfirm) {
            showError('两次密码输入不一致');
            return;
        }
        const submit = e.currentTarget.querySelector('button[type="submit"]');
        if (submit) submit.disabled = true;
        try {
            const data = await apiRequest('/auth/reset-password', {
                method: 'POST',
                body: JSON.stringify({
                    token: passwordResetToken,
                    password,
                    password_confirm: passwordConfirm,
                }),
            });
            token = null;
            username = null;
            setSessionParentFlags(false, '');
            localStorage.removeItem('token');
            localStorage.removeItem('username');
            passwordResetToken = '';
            window.history.replaceState({}, '', window.location.pathname);
            switchAuthView('login');
            showAuthStatus(data.message || '密码已重置，请使用新密码登录');
        } catch (error) {
            showError(error.message || '重置失败');
        } finally {
            if (submit) submit.disabled = false;
        }
    });
    
    // Tab 切换
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            activateAuthTab(tab.dataset.tab);
        });
    });
    
    // 导航切换
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', () => {
            const page = item.dataset.page;
            showSection(page);
        });
    });

    document.querySelectorAll('.mobile-tab[data-page]').forEach(item => {
        item.addEventListener('click', () => {
            const page = item.dataset.page;
            showSection(page);
        });
    });

    const mobileMoreBtn = document.getElementById('mobile-more-btn');
    if (mobileMoreBtn) {
        mobileMoreBtn.addEventListener('click', () => {
            const sheet = document.getElementById('mobile-more-sheet');
            if (sheet && sheet.classList.contains('is-open')) {
                closeMobileMoreSheet();
            } else {
                openMobileMoreSheet();
            }
        });
    }
    document.getElementById('mobile-more-backdrop')?.addEventListener('click', closeMobileMoreSheet);
    document.getElementById('mobile-more-close')?.addEventListener('click', closeMobileMoreSheet);
    document.querySelectorAll('.mobile-more-link').forEach((item) => {
        item.addEventListener('click', () => {
            const page = item.dataset.page;
            if (page) showSection(page);
        });
    });
    document.getElementById('mobile-open-settings')?.addEventListener('click', () => {
        closeMobileMoreSheet();
        openSettings();
    });

    document.querySelectorAll('.js-pk-hub-open').forEach((btn) => {
        btn.addEventListener('click', () => {
            void (async () => {
                openPkHubModal();
                await loadPkHubModalBody();
            })();
        });
    });
    document.getElementById('pk-hub-backdrop')?.addEventListener('click', closePkHubModal);
    document.getElementById('pk-hub-close')?.addEventListener('click', closePkHubModal);

    document.getElementById('settings-backdrop')?.addEventListener('click', closeSettings);
    document.getElementById('settings-close')?.addEventListener('click', closeSettings);
    document.getElementById('settings-pending-words-toggle')?.addEventListener('click', () => {
        const panel = document.getElementById('settings-pending-words-panel');
        const btn = document.getElementById('settings-pending-words-toggle');
        const span = document.getElementById('settings-pending-words-toggle-text');
        if (!panel || !btn) return;
        const opening = panel.hidden;
        panel.hidden = !opening;
        btn.setAttribute('aria-expanded', opening ? 'true' : 'false');
        btn.classList.toggle('is-open', opening);
        if (span) {
            if (opening) {
                span.textContent = '点击收起';
            } else {
                span.textContent =
                    span.dataset.expandLabel || '点击展开：查看列表并管理待复习单词';
            }
        }
    });
    document.getElementById('settings-pending-words-list')?.addEventListener('change', (e) => {
        const t = e.target;
        if (t && t.classList && t.classList.contains('settings-pending-cb')) {
            syncSettingsPendingBatchToolbar();
        }
    });
    document.getElementById('settings-pending-select-all')?.addEventListener('change', (e) => {
        const on = e.target.checked;
        document.querySelectorAll('#settings-pending-words-list .settings-pending-cb').forEach((cb) => {
            cb.checked = on;
        });
        syncSettingsPendingBatchToolbar();
    });
    document.getElementById('settings-pending-words-delete-selected')?.addEventListener('click', async () => {
        const selected = [];
        document.querySelectorAll('#settings-pending-words-list .settings-pending-cb:checked').forEach((cb) => {
            let en = '';
            try {
                en = decodeURIComponent(cb.getAttribute('data-english') || '');
            } catch (_) {
                return;
            }
            if (en) selected.push(en);
        });
        if (!selected.length) return;
        if (
            !confirm(
                `确定从待复习列表移除已选中的 ${selected.length} 个词吗？\n（不会删除已掌握词汇）`,
            )
        ) {
            return;
        }
        const btn = document.getElementById('settings-pending-words-delete-selected');
        const pendingRmBatch = 200;
        const prevLabel = btn ? btn.textContent : '';
        if (btn) {
            btn.disabled = true;
            if (selected.length > pendingRmBatch) {
                btn.textContent = '删除中…';
            }
        }
        try {
            let totalRemoved = 0;
            const removedEnglishAcc = [];
            for (let i = 0; i < selected.length; i += pendingRmBatch) {
                const chunk = selected.slice(i, i + pendingRmBatch);
                if (btn && selected.length > pendingRmBatch) {
                    btn.textContent = `删除中… (${Math.min(i + chunk.length, selected.length)}/${selected.length})`;
                }
                const res = await apiRequest('/words/pending/remove', {
                    method: 'POST',
                    body: JSON.stringify({ english: chunk }),
                });
                const n = res && typeof res.removed === 'number' ? res.removed : 0;
                totalRemoved += n;
                const part = res && Array.isArray(res.removed_english) ? res.removed_english : [];
                removedEnglishAcc.push(...part);
            }
            setSettingsMessage(
                totalRemoved > 0
                    ? `已从待复习移除 ${totalRemoved} 个词`
                    : '未能移除（可能已不在待复习中）',
                !totalRemoved,
            );
            document.getElementById('settings-message')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            await loadSettingsPendingWordsBlock();
            await syncMainPageAfterPendingWordsRemoved(totalRemoved, removedEnglishAcc);
        } catch (err) {
            setSettingsMessage(err.message || '移除失败', true);
            document.getElementById('settings-message')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        } finally {
            if (btn) {
                btn.disabled = false;
                if (prevLabel) btn.textContent = prevLabel;
            }
            syncSettingsPendingBatchToolbar();
        }
    });
    document.getElementById('settings-pending-words-refresh')?.addEventListener('click', async () => {
        const b = document.getElementById('settings-pending-words-refresh');
        if (b) b.disabled = true;
        try {
            await loadSettingsPendingWordsBlock();
            setSettingsMessage('已刷新', false);
        } catch (e) {
            setSettingsMessage(e.message || '刷新失败', true);
        } finally {
            if (b) b.disabled = false;
        }
    });
    document.getElementById('settings-parent-pw-save')?.addEventListener('click', async () => {
        const p1 = document.getElementById('settings-parent-pw-new')?.value?.trim() || '';
        const p2 = document.getElementById('settings-parent-pw-confirm')?.value?.trim() || '';
        if (p1.length < 6) {
            setSettingsMessage('密码至少 6 个字符', true);
            return;
        }
        if (p1 !== p2) {
            setSettingsMessage('两次输入的密码不一致', true);
            return;
        }
        try {
            await apiRequest('/auth/parent-password', {
                method: 'PATCH',
                body: JSON.stringify({ password: p1, password_confirm: p2 }),
            });
            setSettingsMessage('密码已更新');
            const n1 = document.getElementById('settings-parent-pw-new');
            const n2 = document.getElementById('settings-parent-pw-confirm');
            if (n1) n1.value = '';
            if (n2) n2.value = '';
        } catch (e) {
            setSettingsMessage(e.message || '保存失败', true);
        }
    });
    document.getElementById('settings-email-save')?.addEventListener('click', async () => {
        const input = document.getElementById('settings-email');
        const button = document.getElementById('settings-email-save');
        const email = input?.value?.trim() || '';
        if (button) button.disabled = true;
        try {
            const data = await apiRequest('/user/email', {
                method: 'PATCH',
                body: JSON.stringify({ email }),
            });
            if (input) input.value = data.email || '';
            setSettingsEmailMessage(data.message || '邮箱已保存');
        } catch (e) {
            setSettingsEmailMessage(e.message || '保存失败', true);
        } finally {
            if (button) button.disabled = false;
        }
    });
    document.getElementById('settings-avatar-input')?.addEventListener('change', (e) => {
        const inp = e.target;
        const f = inp.files && inp.files[0];
        inp.value = '';
        if (!f) return;
        openAvatarCropModal(f);
    });
    document.getElementById('nav-user-avatar-wrap')?.addEventListener('click', openAvatarViewModal);
    document.getElementById('avatar-view-backdrop')?.addEventListener('click', closeAvatarViewModal);
    document.getElementById('avatar-view-close')?.addEventListener('click', closeAvatarViewModal);
    document.getElementById('avatar-view-replace-input')?.addEventListener('change', (e) => {
        const input = e.target;
        const file = input.files && input.files[0];
        input.value = '';
        if (!file || isParentSession) return;
        closeAvatarViewModal();
        openAvatarCropModal(file);
    });
    bindAvatarCropUi();
    document.getElementById('settings-avatar-remove')?.addEventListener('click', async () => {
        try {
            await deleteAvatarApi();
            setSettingsMessage('已移除头像');
            await loadUserSettingsPanel();
        } catch (err) {
            setSettingsMessage(err.message || '操作失败', true);
        }
    });
    document.querySelectorAll('.js-invite-create, #settings-invite-create').forEach((btn) => {
        btn.addEventListener('click', () => {
            void createInviteCardFromSettings();
        });
    });
    document.getElementById('invite-card-backdrop')?.addEventListener('click', closeInviteCardModal);
    document.getElementById('invite-card-close')?.addEventListener('click', closeInviteCardModal);
    document.getElementById('invite-card-copy')?.addEventListener('click', () => {
        void copyInviteCodeFromModal();
    });
    document.getElementById('invite-card-share')?.addEventListener('click', () => {
        void shareInviteCardFromModal();
    });
    document.getElementById('invite-card-download')?.addEventListener('click', (event) => {
        if (event.currentTarget.getAttribute('aria-disabled') === 'true') event.preventDefault();
    });
    document.getElementById('invite-card-create-new')?.addEventListener('click', () => {
        void createNewInviteFromModal();
    });
    document.getElementById('invite-card-unused-list')?.addEventListener('click', (event) => {
        const target = event.target instanceof Element
            ? event.target.closest('.invite-card-unused-item[data-invite-id]')
            : null;
        if (!target || target.disabled) return;
        void selectUnusedInviteFromModal(target.dataset.inviteId);
    });
    document.getElementById('settings-month-goal-save')?.addEventListener('click', async () => {
        const inp = document.getElementById('settings-month-goal');
        const raw = inp && inp.value.trim();
        try {
            let body;
            if (raw === '') {
                body = { monthly_checkin_goal: null };
            } else {
                const n = parseInt(raw, 10);
                if (Number.isNaN(n)) {
                    setSettingsMessage('请输入有效天数', true);
                    return;
                }
                body = { monthly_checkin_goal: n };
            }
            const res = await apiRequest('/gamification', { method: 'PATCH', body: JSON.stringify(body) });
            let msg = '本月目标已保存';
            if (res.monthly_goal_bonus_just_granted_xp > 0) {
                msg += ` · 已发放打卡目标奖励 +${formatNumber(res.monthly_goal_bonus_just_granted_xp)} XP`;
            }
            setSettingsMessage(msg);
            await loadUserSettingsPanel();
        } catch (err) {
            setSettingsMessage(err.message || '保存失败', true);
        }
    });
    document.querySelectorAll('.js-makeup-checkin-open').forEach((btn) => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            void openMakeupCheckinDialog();
        });
    });
    document.getElementById('makeup-checkin-close')?.addEventListener('click', closeMakeupCheckinDialog);
    document.getElementById('makeup-checkin-cancel')?.addEventListener('click', closeMakeupCheckinDialog);
    document.getElementById('makeup-checkin-backdrop')?.addEventListener('click', closeMakeupCheckinDialog);
    document.getElementById('makeup-checkin-buy')?.addEventListener('click', () => {
        void buyMakeupCheckinFromDialog();
    });
    document.getElementById('settings-pool-join')?.addEventListener('click', async () => {
        try {
            await apiRequest('/monthly-pool/join', { method: 'POST', body: '{}' });
            setSettingsMessage('已加入本月奖池');
            await loadUserSettingsPanel();
        } catch (err) {
            setSettingsMessage(err.message || '加入失败', true);
        }
    });
    document.getElementById('pk-hub-duel-send')?.addEventListener('click', async () => {
        if (pkDuelSendInFlight) return;
        const tin = document.getElementById('pk-hub-duel-target-select');
        const t = (tin && tin.value) || '';
        const w = document.getElementById('pk-hub-duel-wager');
        const wager = w ? parseInt(w.value, 10) : 0;
        if (!t.trim()) {
            showMainBanner('请选择对手');
            return;
        }
        const btn = document.getElementById('pk-hub-duel-send');
        const oldText = btn ? btn.textContent : '';
        pkDuelSendInFlight = true;
        if (btn) {
            btn.disabled = true;
            btn.textContent = '发送中…';
        }
        try {
            await apiRequest('/challenges', {
                method: 'POST',
                body: JSON.stringify({ target_username: t.trim(), wager_xp: wager }),
            });
            showMainBanner('挑战已发出');
            if (tin) tin.value = '';
            await loadPkHubModalBody();
            if (isSettingsOverlayOpen()) await loadUserSettingsPanel();
        } catch (err) {
            showMainBanner(err.message || '发起失败');
        } finally {
            pkDuelSendInFlight = false;
            if (btn) {
                btn.disabled = false;
                if (oldText) btn.textContent = oldText;
            }
        }
    });
    document.getElementById('pk-hub-challenges-root')?.addEventListener('click', async (e) => {
        const btn = e.target.closest('.settings-duel-btn[data-duel-id]');
        if (!btn) return;
        const id = btn.getAttribute('data-duel-id');
        const accept = btn.getAttribute('data-duel-accept') === '1';
        if (!id) return;
        if (pkDuelRespondInFlight.has(id)) return;
        pkDuelRespondInFlight.add(id);
        const root = document.getElementById('pk-hub-challenges-root');
        const peerButtons = root
            ? Array.from(root.querySelectorAll('.settings-duel-btn[data-duel-id]')).filter(
                  (b) => b.getAttribute('data-duel-id') === id,
              )
            : [btn];
        peerButtons.forEach((b) => {
            b.dataset.pkWasDisabled = b.disabled ? '1' : '0';
            b.disabled = true;
        });
        const oldText = btn.textContent;
        btn.textContent = accept ? '接受中…' : '拒绝中…';
        try {
            await apiRequest(`/challenges/${encodeURIComponent(id)}/respond`, {
                method: 'POST',
                body: JSON.stringify({ accept }),
            });
            showMainBanner(accept ? '已接受挑战' : '已拒绝');
            await loadPkHubModalBody();
            if (isSettingsOverlayOpen()) await loadUserSettingsPanel();
        } catch (err) {
            showMainBanner(err.message || '操作失败');
            peerButtons.forEach((b) => {
                b.disabled = b.dataset.pkWasDisabled === '1';
                delete b.dataset.pkWasDisabled;
            });
            btn.textContent = oldText;
        } finally {
            pkDuelRespondInFlight.delete(id);
        }
    });
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        const crop = document.getElementById('avatar-crop-overlay');
        if (crop && crop.style.display !== 'none') {
            closeAvatarCropModal();
            return;
        }
        const avatarViewModal = document.getElementById('avatar-view-modal');
        if (avatarViewModal && avatarViewModal.style.display !== 'none') {
            closeAvatarViewModal();
            return;
        }
        const pkHubModal = document.getElementById('pk-hub-modal');
        if (pkHubModal && pkHubModal.style.display !== 'none') {
            closePkHubModal();
            return;
        }
        const inviteModal = document.getElementById('invite-card-modal');
        if (inviteModal && inviteModal.style.display !== 'none') {
            closeInviteCardModal();
            return;
        }
        const makeupModal = document.getElementById('makeup-checkin-modal');
        if (makeupModal && makeupModal.style.display !== 'none') {
            closeMakeupCheckinDialog();
            return;
        }
        const ov = document.getElementById('settings-overlay');
        if (ov && ov.style.display !== 'none') closeSettings();
    });

    const lbOptIn = document.getElementById('leaderboard-opt-in');
    if (lbOptIn) {
        lbOptIn.addEventListener('change', async (e) => {
            const checked = e.target.checked;
            try {
                await apiRequest('/gamification', {
                    method: 'PATCH',
                    body: JSON.stringify({ leaderboard_opt_in: checked })
                });
                showMainBanner(checked ? '已参与排行榜展示' : '已隐藏，排行榜中不再展示你的数据');
                await loadLeaderboardSection();
            } catch (err) {
                e.target.checked = !checked;
                showMainBanner(err.message || '更新失败');
            }
        });
    }
    
    // 退出登录
    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', () => { logout(); });
    }
    
    // 提交答案（mousedown 阻止按钮抢走焦点，便于连续输入）
    const submitBtn = document.getElementById('submit-answer');
    if (submitBtn) {
        submitBtn.addEventListener('mousedown', (e) => {
            e.preventDefault();
        });
        submitBtn.addEventListener('click', submitAnswer);
    }

    const reviewBox = document.getElementById('review-box');
    if (reviewBox) {
        reviewBox.addEventListener('click', (e) => {
            if (e.target.closest('button')) return;
            focusWordCapture(0);
        });
    }
    
    // 下划线输入框的Enter键已经在initializeUnderlineInput中处理
    
    initImportNceLessonSelect();
    initImportArticlePickDelegation();
    initImportArticleSelectAllCheckbox();
    initImportResultModal();
    const importArticleBtn = document.getElementById('import-article-btn');
    if (importArticleBtn) {
        importArticleBtn.addEventListener('click', importFromArticle);
    }
    const importOcrPickImgBtn = document.getElementById('import-ocr-pick-img-btn');
    const importOcrFileInput = document.getElementById('import-ocr-file-input');
    if (importOcrPickImgBtn && importOcrFileInput) {
        importOcrPickImgBtn.addEventListener('click', () => importOcrFileInput.click());
        importOcrFileInput.addEventListener('change', () => {
            const f = importOcrFileInput.files && importOcrFileInput.files[0];
            importOcrFileInput.value = '';
            if (f) void runImportOcrToTextarea(f);
        });
    }
    initImportVocabTextareaNormalize();
    const importVocabBtn = document.getElementById('import-vocab-btn');
    if (importVocabBtn) {
        importVocabBtn.addEventListener('click', importVocabToCSV);
    }
    document.getElementById('admin-words-user')?.addEventListener('change', () => loadAdminUserWords());
    document.getElementById('admin-words-status')?.addEventListener('change', () => loadAdminUserWords());
    document.getElementById('admin-words-refresh')?.addEventListener('click', () => loadAdminUserWords());
    document.getElementById('admin-words-select-all')?.addEventListener('change', (e) => {
        const on = e.target.checked;
        document.querySelectorAll('.admin-word-cb').forEach((cb) => { cb.checked = on; });
    });
    document.getElementById('admin-words-tbody')?.addEventListener('click', (e) => {
        const btn = e.target.closest('.admin-word-delete');
        if (!btn) return;
        const en = btn.getAttribute('data-english');
        if (en) adminConfirmDeleteWords([en]);
    });
    document.getElementById('admin-words-delete-selected')?.addEventListener('click', () => {
        const list = [...document.querySelectorAll('.admin-word-cb:checked')]
            .map((cb) => cb.getAttribute('data-english'))
            .filter(Boolean);
        if (!list.length) {
            showAdminNotice('请先勾选要删除的单词');
            return;
        }
        adminConfirmDeleteWords(list);
    });

    // 错题顺序切换：实时对剩余题目重新排列
    document.querySelectorAll('input[name="wrong-order"]').forEach((radio) => {
        radio.addEventListener('change', () => {
            applyWrongOrderToRemaining();
        });
    });

    document.getElementById('remedial-offer-accept')?.addEventListener('click', onRemedialOfferAccept);
    document.getElementById('remedial-offer-decline')?.addEventListener('click', onRemedialOfferDecline);
    document.getElementById('remedial-offer-modal')?.addEventListener('click', (e) => {
        if (e.target.classList.contains('remedial-offer-backdrop')) {
            onRemedialOfferDecline();
        }
    });

    document.getElementById('system-broadcast-ok')?.addEventListener('click', async () => {
        const id = systemBroadcastPendingId;
        if (!id) {
            hideSystemBroadcastModal();
            return;
        }
        try {
            if (token) {
                await apiRequest('/auth/broadcast/ack', {
                    method: 'POST',
                    body: JSON.stringify({ id }),
                });
            }
        } catch (_) {
            /* 仍关闭，避免界面卡住 */
        }
        hideSystemBroadcastModal();
    });

    initWordbankPanel();
    
    // 继续学习
    const reviewMoreBtn = document.getElementById('review-more');
    if (reviewMoreBtn) {
        reviewMoreBtn.addEventListener('click', () => {
            loadReviewList();
        });
    }

    const bonusReviewBtn = document.getElementById('bonus-review-btn');
    if (bonusReviewBtn) {
        bonusReviewBtn.addEventListener('click', () => {
            startBonusReview();
        });
    }

    // 管理员入口
    const adminGearLogin = document.getElementById('admin-gear-login');
    const adminGearMain = document.getElementById('admin-gear-main');
    if (adminGearLogin) {
        adminGearLogin.addEventListener('click', () => openAdminOverlay());
    }
    if (adminGearMain) {
        adminGearMain.addEventListener('click', () => openSettings());
    }
    document.getElementById('admin-close')?.addEventListener('click', () => closeAdminOverlay());
    document.getElementById('admin-overlay')?.addEventListener('click', (e) => {
        if (e.target.id === 'admin-overlay') {
            closeAdminOverlay();
        }
    });
    document.getElementById('admin-login-submit')?.addEventListener('click', async () => {
        const u = document.getElementById('admin-username')?.value?.trim() || '';
        const p = document.getElementById('admin-password')?.value || '';
        showAdminNotice('');
        try {
            const res = await fetch(`${API_BASE}/admin/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: u, password: p })
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(data.error || (res.status === 503 ? '未配置管理员' : '登录失败'));
            }
            setAdminToken(data.access_token);
            await loadAdminDashboard();
            updatePlanUI();
        } catch (e) {
            showAdminNotice(e.message || '登录失败');
        }
    });
    document.getElementById('admin-gen-invite')?.addEventListener('click', async () => {
        showAdminNotice('');
        try {
            const data = await apiAdminRequest('/admin/invites', { method: 'POST' });
            const box = document.getElementById('admin-invite-once');
            if (box) {
                box.style.display = 'block';
                box.innerHTML = `<p><strong>新邀请码（仅显示一次）：</strong></p><p class="admin-code-display">${escapeHtml(data.invite_code)}</p><p class="admin-hint">${escapeHtml(data.hint || '')}</p>`;
            }
            const inv = await apiAdminRequest('/admin/invites');
            renderAdminInvites(inv.invites);
        } catch (e) {
            showAdminNotice(e.message || '生成失败');
        }
    });
    document.getElementById('admin-save-deepseek')?.addEventListener('click', async () => {
        const keyInput = document.getElementById('admin-deepseek-key');
        const newKey = (keyInput ? keyInput.value : '').trim();
        showAdminNotice('');
        try {
            await apiAdminRequest('/admin/config', {
                method: 'PATCH',
                body: JSON.stringify({ deepseek_api_key: newKey })
            });
            if (keyInput) keyInput.value = '';
            showAdminNotice(newKey ? 'API Key 已保存' : 'API Key 已清除');
            const cfg = await apiAdminRequest('/admin/config').catch(() => null);
            renderAdminDeepseekStatus(cfg);
            void loadUserPlan();
        } catch (e) {
            showAdminNotice(e.message || '保存失败');
        }
    });

    document.getElementById('admin-save-article-ai')?.addEventListener('click', async () => {
        const cb = document.getElementById('admin-article-ai-extract');
        showAdminNotice('');
        try {
            await apiAdminRequest('/admin/config', {
                method: 'PATCH',
                body: JSON.stringify({ article_ai_extract_enabled: cb ? cb.checked : false })
            });
            showAdminNotice('AI 文章分词开关已保存');
            const cfg = await apiAdminRequest('/admin/config').catch(() => null);
            renderAdminDeepseekStatus(cfg);
            void loadUserPlan();
        } catch (e) {
            showAdminNotice(e.message || '保存失败');
        }
    });

    document.getElementById('admin-wordbank-csv-upload')?.addEventListener('click', async () => {
        const input = document.getElementById('admin-wordbank-csv-file');
        const file = input && input.files && input.files[0];
        showAdminNotice('');
        if (!file) {
            showAdminNotice('请先选择 words.csv 文件');
            return;
        }
        if (!file.name.toLowerCase().endsWith('.csv')) {
            showAdminNotice('请选择 .csv 文件');
            return;
        }
        const fd = new FormData();
        fd.append('file', file, file.name);
        try {
            const data = await apiAdminMultipart('/admin/wordbank/csv/incremental-upload', fd);
            const st = data.stats || {};
            showAdminNotice(
                `${data.message || '完成'}：新增 ${st.added ?? '—'} 条，覆盖同词 ${st.replaced ?? '—'} 条，合并后共 ${st.final_count ?? '—'} 条。`
            );
            if (input) input.value = '';
        } catch (e) {
            showAdminNotice(e.message || '上传失败');
        }
    });

    document.getElementById('admin-logout')?.addEventListener('click', async () => {
        try {
            const at = getAdminToken();
            if (at) {
                await fetch(`${API_BASE}/admin/logout`, {
                    method: 'POST',
                    headers: {
                        Authorization: `Bearer ${at}`,
                        'Content-Type': 'application/json'
                    }
                });
            }
        } catch (_) {
            /* ignore */
        }
        setAdminToken(null);
        showAdminLoginPanel();
        showAdminNotice('');
        updatePlanUI();
    });
    
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) return;
        const main = document.getElementById('main-page');
        if (!main || !main.classList.contains('active')) return;
        if (!token || !username) return;
        void refreshPkInviteIndicator();
    });

    restoreInviteRegistrationState();

    // 同一页面内打开邀请链接时不会重新加载，需要监听哈希变化。
    window.addEventListener('hashchange', handleInviteRegistrationNavigation);

    // 邀请码进入内存后立即清理地址栏，避免一次性邀请码留在浏览历史中。
    const incomingInviteCode = captureInviteRegistrationFromUrl();

    // 初始化页面
    if (passwordResetToken) {
        showLoginPage();
        switchAuthView('reset');
    } else if (token && username) {
        void showMainPage().finally(() => {
            if (token && username) {
                clearInviteRegistrationState();
                if (incomingInviteCode) {
                    showMainBanner('当前已登录，邀请链接仅供新用户注册');
                }
            }
        });
    } else {
        showLoginPage();
    }
});
