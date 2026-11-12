// 全局状态
let token = localStorage.getItem('token');
let passwordResetToken = new URLSearchParams(window.location.search).get('reset_token') || '';
/** 当前弹窗中的系统广播 id，用于确认已读 */
let systemBroadcastPendingId = null;
let username = localStorage.getItem('username');
/** 家长登录：服务端以孩子身份操作数据；界面显示 childUsername */
let isParentSession = localStorage.getItem('session_is_parent') === '1';
let childUsername = localStorage.getItem('session_child_username') || '';
let accountSessionGeneration = 0;
let authenticationRequestSequence = 0;

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
let reviewAdvanceTimer = null;
let pendingReviewAdvance = null;
const REVIEW_SECTION_SIZE = 30;
let reviewSectionStartIndex = 0;
let reviewSectionStartCorrectAttempts = 0;
let reviewSectionStartWrongAttempts = 0;
let reviewSectionStartMasteredCount = 0;
let reviewSectionCompletedCount = 0;
/** 立即错题复习期间保存的主轮现场；错题只练一轮后原样恢复。 */
let immediateRemedialReturnState = null;

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
/** 导入后服务端可能已调整今日任务；返回复习页时必须重新获取。 */
let reviewDataStaleAfterImport = false;
/** 连续点击朗读：取消上一轮 Piper 请求、音频与浏览器合成 */
let ttsPlaybackGeneration = 0;
let ttsFetchAbort = null;
let ttsPiperAudio = null;
let ttsPiperObjectUrl = null;
const LISTENING_PLAYBACK_START_TIMEOUT_MS = 10000;
const WEB_SPEECH_START_TIMEOUT_MS = 4000;

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

/** 本轮达到题型尝试上限仍未答对的单词（去重顺序）；一轮结束后用于生成下一轮错题复习 */
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
let sessionRestoredRemedialWords = 0;
let sessionTotalWrongAttempts = 0;
let sessionNewMastered = [];
let sessionExerciseStats = {};
let sessionImmediateRemedialRounds = 0;
let sessionBonusPracticeXp = 0;
let currentTodayTaskPlan = null;
let reviewQuestionStartedAt = 0;
let pendingReviewSubmission = null;
let activeReviewSubmissionPromise = null;
let reviewSessionGeneration = 0;
let reviewDataRequestSequence = 0;
let listeningPlaybackAttempt = 0;

function captureReviewContext() {
    return {
        token,
        username,
        generation: reviewSessionGeneration,
        accountGeneration: accountSessionGeneration,
    };
}

function captureAccountContext() {
    return { token, username, generation: accountSessionGeneration };
}

function isAccountContextCurrent(context) {
    return Boolean(
        context && context.token === token && context.username === username &&
        context.generation === accountSessionGeneration
    );
}

function isReviewContextCurrent(context) {
    return Boolean(
        context &&
        context.token === token &&
        context.username === username &&
        context.accountGeneration === accountSessionGeneration &&
        context.generation === reviewSessionGeneration
    );
}

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
    const lifetimeXp = Number(g.lifetime_xp ?? g.total_xp) || 0;
    const balanceXp = Number(g.xp_balance ?? g.total_xp) || 0;
    const xpTitle = `累计经验 ${formatNumber(lifetimeXp)} XP；可用余额 ${formatNumber(balanceXp)} XP。点击查看收支历史`;
    const makeupHint = makeupAvailable
        ? `；可用 ${formatNumber(Number(g.makeup_checkin.cost_xp) || 0)} XP 补救 ${g.makeup_checkin.target_date || '断点'}`
        : '';
    if (lv && xp && st) {
        lv.textContent = `Lv.${g.level}`;
        xp.textContent = `${formatNumber(lifetimeXp)} XP`;
        xp.title = xpTitle;
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
        mxp.textContent = `${formatNumber(lifetimeXp)} XP`;
        mxp.title = xpTitle;
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
let dailySummaryRequestSequence = 0;
let xpHistoryRequestSequence = 0;

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
    const stats = statusData.stats || {};
    const mastered = Number(stats.mastered_words) || 0;
    const stable = Number(stats.mastered_stable) || 0;
    const reinforcement = Number(stats.mastered_reinforcement) || 0;
    const task = stats.today_task || {};
    const taskTotal = Number(task.total) || 0;
    const taskCompleted = Number(task.completed) || 0;
    const taskRemaining = Number(task.remaining) || 0;

    const goal = g.monthly_checkin_goal;
    const goalMonth = g.monthly_checkin_goal_month;
    const monthKey = g.month_key;
    let monthLine = '';
    if (goal != null && goalMonth === monthKey) {
        const md = Number(g.monthly_checkin_goal_progress_days) || 0;
        const gNum = Number(goal);
        monthLine = `<li>设置目标后新增打卡 <strong>${formatNumber(md)}</strong> / ${formatNumber(gNum)} 天</li>`;
    }

    const checkLine = done
        ? `<li>今日打卡 <strong>已完成</strong>（已答对 ${formatNumber(todayC)} 词）</li>`
        : `<li>今日打卡 <strong>进行中</strong>（已答对 ${formatNumber(todayC)} / ${formatNumber(minC)} 词）</li>`;

    body.innerHTML =
        `<p class="daily-summary-date">${escapeHtml(formatDailySummaryDateLabel())}</p>` +
        '<ul class="daily-summary-list">' +
        `<li>今日答对 <strong>${formatNumber(todayC)}</strong> 词</li>` +
        `<li>今日获得 <strong>${formatNumber(xpToday)}</strong> XP</li>` +
        `<li>可用 XP 余额 <strong>${formatNumber(Number(g.xp_balance ?? g.total_xp) || 0)}</strong></li>` +
        checkLine +
        dailySummaryStreakLineHtml(streak) +
        (streakMax > 0
            ? `<li>历史最高连续打卡 <strong>${formatNumber(streakMax)}</strong> 天</li>`
            : '') +
        monthLine +
        `<li>今日任务 <strong>${formatNumber(taskCompleted)} / ${formatNumber(taskTotal)}</strong>（剩余 ${formatNumber(taskRemaining)} 词）</li>` +
        `<li>今日仍待复习（含逾期）<strong>${formatNumber(dueToday)}</strong> 词</li>` +
        `<li>累计已掌握 <strong>${formatNumber(mastered)}</strong> 词（稳定 ${formatNumber(stable)} · 待巩固 ${formatNumber(reinforcement)}）</li>` +
        '</ul>';
}

function closeDailySummaryPopover() {
    dailySummaryRequestSequence++;
    const pop = document.getElementById('daily-summary-popover');
    if (!pop || !dailySummaryPopoverOpen) return;
    dailySummaryPopoverOpen = false;
    pop.hidden = true;
    document.getElementById('username-display')?.setAttribute('aria-expanded', 'false');
    document.getElementById('daily-summary-mobile-btn')?.setAttribute('aria-expanded', 'false');
}

async function openDailySummaryPopover() {
    const accountContext = captureAccountContext();
    const requestSequence = ++dailySummaryRequestSequence;
    const isCurrent = () => isAccountContextCurrent(accountContext) && dailySummaryPopoverOpen && requestSequence === dailySummaryRequestSequence;
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
        if (!isCurrent()) return;
        lastGamificationProfile = g;
        updateGamificationNav(g);
        if (isSettingsOverlayOpen() && lastGamificationProfile) {
            updateSettingsCheckinHintFromProfile(lastGamificationProfile);
            updateMakeupCheckinDialogFromProfile(lastGamificationProfile);
            updateSettingsMonthlyGoalBonusNotice(lastGamificationProfile);
        }
        renderDailySummaryBody(g, ws);
    } catch (e) {
        if (!isCurrent()) return;
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
    xpHistoryRequestSequence++;
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
    const balance = Number(data?.xp_balance) || 0;
    const lifetime = Number(data?.lifetime_xp) || 0;
    return (
        '<div class="xp-history-summary">' +
        `<div class="xp-history-stat"><span class="xp-history-stat-label">可用余额</span><span class="xp-history-stat-value">${formatNumber(balance)} XP</span></div>` +
        `<div class="xp-history-stat"><span class="xp-history-stat-label">累计经验</span><span class="xp-history-stat-value">${formatNumber(lifetime)} XP</span></div>` +
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
    const accountContext = captureAccountContext();
    const requestSequence = ++xpHistoryRequestSequence;
    const isCurrent = () => isAccountContextCurrent(accountContext) && xpHistoryModalOpen && requestSequence === xpHistoryRequestSequence;
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
        if (!isCurrent()) return;
        renderXpHistoryBody(data);
    } catch (e) {
        if (!isCurrent()) return;
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

async function refreshGamification(accountContext = captureAccountContext()) {
    try {
        const g = await apiRequest('/gamification');
        if (!isAccountContextCurrent(accountContext)) return null;
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
            `额外奖励：从设置目标后新增 ${g} 个有效打卡日，可一次性获得 ${formatNumber(bonusXp)} XP（${g}×${per}）；设置目标前的打卡不计入该目标。`;
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
    const maxGoalDays = Math.max(0, Number(s.monthly_checkin_goal_max_days) || 0);
    const suggested = Math.min(
        maxGoalDays,
        Math.max(1, Number(s.monthly_checkin_goal_suggested_days) || maxGoalDays)
    );
    const canEdit = s.monthly_checkin_goal_can_edit !== false && maxGoalDays > 0;
    const input = document.getElementById('settings-month-goal');
    const lockHint = document.getElementById('settings-month-goal-lock-hint');
    const saveGoalBtn = document.getElementById('settings-month-goal-save');
    if (input) {
        input.max = String(maxGoalDays || 1);
        input.placeholder = maxGoalDays > 0
            ? `最多 ${maxGoalDays} 天（只统计设置后的新打卡）`
            : '本月已无可设置日期';
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
    const monthDays = Number(s.month_valid_checkin_days) || 0;
    const days = Number(s.monthly_checkin_goal_progress_days) || 0;
    const goal = s.monthly_checkin_goal;
    const fill = document.getElementById('settings-month-goal-fill');
    const text = document.getElementById('settings-month-goal-text');
    if (goal != null && goal !== '') {
        const g = Number(goal);
        const pct = g > 0 ? Math.min(100, Math.round((days / g) * 100)) : 0;
        if (fill) fill.style.width = `${pct}%`;
        if (text) text.textContent = `设置目标后新增有效打卡 ${days} / ${g} 天`;
    } else {
        if (fill) fill.style.width = '0%';
        if (text) text.textContent = `本月有效打卡 ${monthDays} 天（未设置目标）`;
    }

    const pool = s.monthly_pool || {};
    const poolHint = document.getElementById('settings-pool-hint');
    const joinBtn = document.getElementById('settings-pool-join');
    if (poolHint) {
        const prepD = pool.preparation_last_day || 5;
        const stD = pool.competition_start_day || 6;
        let t = `${pool.month || ''} 奖池共 ${formatNumber(pool.pool_xp || 0)} XP，${pool.participant_count || 0} 人参与。可用余额 ${formatNumber(Number(s.xp_balance ?? s.total_xp) || 0)} XP。`;
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

