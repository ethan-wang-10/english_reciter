// 全局状态
let token = localStorage.getItem('token');
let username = localStorage.getItem('username');
let currentReviewList = [];
let currentReviewIndex = 0;
let currentErrorCount = 0; // 当前单词错误次数
let currentRevealedCount = 0; // 当前单词已揭示字母数
let isSubmitting = false; // 防止重复提交（修复一闪而过bug）
let isAdvancing = false;  // 防止重复推进到下一题

/** 用户套餐类型: 'free' | 'paid' */
let userPlan = 'free';

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
const PK_INVITE_POLL_MS = 3600000;

// API 基础 URL
const API_BASE = '/api';

function escapeHtml(text) {
    if (text == null || text === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}

function escapeRegExp(str) {
    return String(str).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
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

function updateGamificationNav(g) {
    if (!g) return;
    const lv = document.getElementById('ng-level');
    const xp = document.getElementById('ng-xp');
    const st = document.getElementById('ng-streak');
    const ck = document.getElementById('ng-checkin');
    const minC = Number(g.check_in_min_correct) || 5;
    const todayC = Number(g.today_correct_count) || 0;
    const ckText = `打卡 ${todayC}/${minC}`;
    if (lv && xp && st) {
        lv.textContent = `Lv.${g.level}`;
        xp.textContent = `${formatNumber(g.total_xp)} XP`;
        st.textContent = `🔥 ${g.streak}`;
    }
    if (ck) {
        ck.textContent = ckText;
        ck.classList.toggle('ng-checkin-done', !!g.check_in_done_today);
    }
    const mlv = document.getElementById('mobile-ng-level');
    const mxp = document.getElementById('mobile-ng-xp');
    const mst = document.getElementById('mobile-ng-streak');
    const mck = document.getElementById('mobile-ng-checkin');
    if (mlv && mxp && mst) {
        mlv.textContent = `Lv.${g.level}`;
        mxp.textContent = `${formatNumber(g.total_xp)} XP`;
        mst.textContent = `🔥 ${g.streak}`;
    }
    if (mck) {
        mck.textContent = ckText;
        mck.classList.toggle('ng-checkin-done', !!g.check_in_done_today);
    }
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

/** 根据 visualViewport 估算键盘占用高度，供 #main-page 底部 padding 抬高可滚动区域 */
function updateVisualViewportKeyboardInset() {
    const vv = window.visualViewport;
    if (!vv) {
        document.documentElement.style.setProperty('--keyboard-inset', '0px');
        return;
    }
    const inset = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
    document.documentElement.style.setProperty('--keyboard-inset', `${inset}px`);
}

function isTextLikeField(el) {
    if (!el) return false;
    if (el.tagName === 'TEXTAREA') return true;
    if (el.tagName !== 'INPUT') return false;
    const type = String(el.type || 'text').toLowerCase();
    return ['text', 'password', 'search', 'email', 'tel', 'url', 'number'].includes(type) || type === '';
}

/** 复习透明输入层或导入区输入框聚焦时，将输入区滚入可视范围 */
function scrollFocusedInputIntoViewIfNeeded() {
    const el = document.activeElement;
    if (!el || !isTextLikeField(el)) return;
    if (el.id === 'mobile-word-capture') {
        const wrap = el.closest('.underline-input-wrapper');
        if (wrap) {
            wrap.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'auto' });
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

    let raf = null;
    const schedule = () => {
        if (raf != null) return;
        raf = requestAnimationFrame(() => {
            raf = null;
            updateVisualViewportKeyboardInset();
            scrollFocusedInputIntoViewIfNeeded();
        });
    };

    vv.addEventListener('resize', schedule);
    vv.addEventListener('scroll', schedule);
    window.addEventListener('resize', schedule);
    schedule();

    document.getElementById('main-page')?.addEventListener('focusin', (e) => {
        const t = e.target;
        if (!isTextLikeField(t)) return;
        if (t.id !== 'mobile-word-capture' && !t.closest('#import-section')) return;
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                updateVisualViewportKeyboardInset();
                scrollFocusedInputIntoViewIfNeeded();
            });
        });
    }, true);
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
    return `<li class="settings-duel-item">
    <span class="settings-duel-meta">${escapeHtml(role)} ${escapeHtml(other)} · ${wager} XP · ${escapeHtml(monthBit)}</span>
    <span class="settings-duel-status">${escapeHtml(statusLabel)}</span>
    ${inviteExpiry}
    ${pkRange}
    ${actions}
  </li>`;
}

function getPendingIncomingDuels(duels) {
    if (!Array.isArray(duels) || !username) return [];
    return duels.filter((d) => d.status === 'pending' && d.target_user === username);
}

function updatePkInviteBadgeFromDuels(duels) {
    const badge = document.getElementById('pk-invite-badge');
    const btn = document.getElementById('pk-invite-btn');
    if (!badge || !btn) return;
    const n = getPendingIncomingDuels(duels).length;
    if (n > 0) {
        badge.hidden = false;
        badge.setAttribute('aria-hidden', 'false');
        badge.textContent = n > 9 ? '9+' : String(n);
        btn.setAttribute('aria-label', `PK 邀约，${n} 条待处理`);
    } else {
        badge.hidden = true;
        badge.setAttribute('aria-hidden', 'true');
        badge.textContent = '';
        btn.setAttribute('aria-label', 'PK 邀约');
    }
}

async function refreshPkInviteIndicator() {
    if (!token || !username) {
        updatePkInviteBadgeFromDuels([]);
        return;
    }
    try {
        const data = await apiRequest('/challenges');
        updatePkInviteBadgeFromDuels(data.challenges || []);
    } catch (_) {
        /* ignore */
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
    if (!token || !username) return;
    pkInvitePollTimer = window.setInterval(tickPkInvitePoll, PK_INVITE_POLL_MS);
}

function renderPkInviteCard(d) {
    const from = escapeHtml(d.from_user || '');
    const wager = Number(d.wager_xp) || 0;
    const id = escapeHtml(String(d.id || ''));
    const exp =
        d.expires_at && d.status === 'pending'
            ? `<p class="pk-invite-expiry">邀约有效期至 ${escapeHtml(
                  String(d.expires_at).replace('T', ' ').slice(0, 16),
              )}</p>`
            : '';
    const wagerLabel = wager === 0 ? '无赌注' : `${wager} XP`;
    return `<div class="pk-invite-item" data-duel-id="${id}">
    <p class="pk-invite-lead"><strong>${from}</strong> 向你发起 1v1 打卡 PK</p>
    <p class="pk-invite-meta">赌注：${escapeHtml(wagerLabel)}</p>
    ${exp}
    <div class="pk-invite-item-actions">
      <button type="button" class="btn btn-secondary pk-invite-respond-btn" data-duel-id="${id}" data-duel-accept="0">拒绝</button>
      <button type="button" class="btn btn-primary pk-invite-respond-btn" data-duel-id="${id}" data-duel-accept="1">同意</button>
    </div>
  </div>`;
}

function openPkInviteModal() {
    const modal = document.getElementById('pk-invite-modal');
    if (modal) {
        modal.style.display = 'flex';
        modal.setAttribute('aria-hidden', 'false');
    }
}

function closePkInviteModal() {
    const modal = document.getElementById('pk-invite-modal');
    if (modal) {
        modal.style.display = 'none';
        modal.setAttribute('aria-hidden', 'true');
    }
}

async function loadPkInviteModalBody() {
    const body = document.getElementById('pk-invite-body');
    if (!body) return;
    body.innerHTML = '<p class="pk-invite-loading">加载中…</p>';
    const data = await apiRequest('/challenges');
    const list = data.challenges || [];
    updatePkInviteBadgeFromDuels(list);
    const pending = getPendingIncomingDuels(list);
    if (!pending.length) {
        body.innerHTML = '<p class="pk-invite-empty">暂无待处理的 1v1 PK 邀约。</p>';
        return;
    }
    body.innerHTML = pending.map((d) => renderPkInviteCard(d)).join('');
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
    updateSettingsMonthlyGoalBonusNotice(s);
    const dim = Number(s.month_days_in_month) || 31;
    const input = document.getElementById('settings-month-goal');
    if (input) {
        input.max = String(dim);
        input.placeholder = `1～${dim}`;
        input.value =
            s.monthly_checkin_goal != null && s.monthly_checkin_goal !== ''
                ? String(s.monthly_checkin_goal)
                : '';
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

    const sel = document.getElementById('settings-duel-wager');
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

    const list = document.getElementById('settings-duel-list');
    if (list && Array.isArray(s.duels)) {
        list.innerHTML = s.duels.length
            ? s.duels.map((d) => renderDuelRow(d, username)).join('')
            : '<li class="settings-duel-empty">暂无挑战</li>';
    }

    const duelSel = document.getElementById('settings-duel-target-select');
    if (duelSel && Array.isArray(s.duel_opponents)) {
        const prev = duelSel.value;
        duelSel.innerHTML =
            '<option value="">选择对手</option>' +
            s.duel_opponents.map((u) => `<option value="${escapeHtml(u)}">${escapeHtml(u)}</option>`).join('');
        if (prev && s.duel_opponents.includes(prev)) duelSel.value = prev;
    }

    updatePkInviteBadgeFromDuels(s.duels);
}

const AVATAR_CROP_VIEW = 280;
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

async function refreshNavUserAvatar() {
    if (!token || !username) {
        updateNavUserAvatar(null);
        return;
    }
    try {
        const s = await apiRequest('/user/settings');
        updateNavUserAvatar(s && s.avatar_url);
    } catch (_) {
        updateNavUserAvatar(null);
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
        await postAvatarFile(uploadBlob, isJpeg ? 'avatar.jpg' : 'avatar.webp');
        setSettingsMessage('头像已更新');
        await loadUserSettingsPanel();
    } catch (err) {
        setSettingsMessage(err.message || '上传失败', true);
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

    let joinBtnHtml = '';
    if (pool.join_window_open && !pool.joined) {
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
                const me = uname === username ? ' mp-lane-me' : '';
                const av = r.avatar_url
                    ? `<img src="${escapeHtml(avatarDisplayUrl(r.avatar_url, 64))}" alt="" width="28" height="28" loading="lazy" />`
                    : '<span class="mp-lane-avatar-ph" aria-hidden="true">👤</span>';
                const you = uname === username ? ' <span class="lb-you">我</span>' : '';
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

function renderLeaderboardTable(rows) {
    const wrap = document.getElementById('leaderboard-table-wrap');
    if (!wrap) return;
    if (!rows || rows.length === 0) {
        wrap.innerHTML =
            '<p class="leaderboard-empty">暂无排行数据。开启「在排行榜中展示」并学习后即可上榜。</p>';
        return;
    }
    const head =
        '<table class="leaderboard-table"><thead><tr>' +
        '<th>排名</th><th>用户</th><th>等级</th><th>XP</th><th>连续</th><th>成就</th>' +
        '</tr></thead><tbody>';
    const body = rows
        .map((r) => {
            const me = r.is_viewer ? 'leaderboard-row-me' : '';
            const av = r.avatar_url
                ? `<img class="lb-avatar" src="${escapeHtml(avatarDisplayUrl(r.avatar_url, 64))}" alt="" width="32" height="32" loading="lazy" />`
                : '<span class="lb-avatar lb-avatar-placeholder" aria-hidden="true">👤</span>';
            return `<tr class="${me}">
                <td>${escapeHtml(r.rank)}</td>
                <td class="lb-user-cell">${av}<span class="lb-username">${escapeHtml(r.username)}${r.is_viewer ? ' <span class="lb-you">我</span>' : ''}</span></td>
                <td>Lv.${escapeHtml(r.level)}</td>
                <td>${escapeHtml(formatNumber(r.total_xp))}</td>
                <td>🔥 ${escapeHtml(r.streak)}</td>
                <td>${escapeHtml(r.achievements_count)}</td>
            </tr>`;
        })
        .join('');
    wrap.innerHTML = head + body + '</tbody></table>';
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
    const loading = document.getElementById('leaderboard-loading');
    if (loading) loading.style.display = 'block';
    try {
        await refreshGamification();
        const [data, pool] = await Promise.all([
            apiRequest('/leaderboard'),
            apiRequest('/monthly-pool'),
        ]);
        renderMonthlyPoolRace(pool);
        renderLeaderboardTable(data.leaderboard);
        renderAchievementsGrid(lastGamificationProfile);
    } catch (e) {
        const wrap = document.getElementById('leaderboard-table-wrap');
        if (wrap) {
            wrap.innerHTML = `<p class="leaderboard-empty">${escapeHtml(e.message || '加载失败')}</p>`;
        }
        const raceWrap = document.getElementById('monthly-pool-race-wrap');
        if (raceWrap) raceWrap.innerHTML = '';
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

function showMessage(message, type = 'success', durationMs = 3000) {
    const messageDiv = document.getElementById('import-message');
    if (!messageDiv) return;
    messageDiv.textContent = message;
    messageDiv.className = `message ${type}`;
    messageDiv.style.display = '';
    setTimeout(() => {
        messageDiv.className = 'message';
        messageDiv.style.display = 'none';
    }, durationMs);
}

/** 词汇导入（付费）接口返回拼装成可读说明（在服务端 message 基础上补充词条明细） */
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
    const aw = data.already_in_csv_words;
    if (Array.isArray(aw) && aw.length) {
        const show = aw.slice(0, 18);
        msg += ` 系统词库已有：${show.join('、')}${aw.length > 18 ? '…' : ''}`;
    }
    if (Array.isArray(data.failed) && data.failed.length) {
        const show = data.failed.slice(0, 10);
        msg += ` AI 生成失败：${show.join('、')}${data.failed.length > 10 ? '…' : ''}`;
    }
    return msg;
}

const REVIEW_PHONETIC_STORAGE_KEY = 'english_reciter_review_show_phonetic';

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

// 获取当前输入值（含固定占位符，与词库原形一致以便后端校验）
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

// 浏览器端朗读（远程访问时服务端 say 只在服务器出声，用户听不到）
// Android Chrome：语音列表异步加载、合成队列常处于 paused，需 resume + voiceschanged 后再 speak
/** @param {string} text @param {(() => void) | undefined} onEnd 朗读结束回调；省略时恢复复习输入框焦点 */
function speakEnglishInBrowser(text, onEnd) {
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
        const onDone = typeof onEnd === 'function' ? onEnd : () => focusWordCapture(0);
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

    if (speakEnglishInBrowser(enText)) {
        return;
    }

    try {
        await apiRequest('/words/speak', {
            method: 'POST',
            body: JSON.stringify({
                text: enText
            })
        });
        focusWordCapture(0);
    } catch (error) {
        focusWordCapture(0);
    }
}

// API 请求
async function apiRequest(endpoint, options = {}) {
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };
    
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }
    
    const response = await fetch(`${API_BASE}${endpoint}`, {
        ...options,
        headers
    });
    
    if (!response.ok) {
        const error = await response.json();

        if (response.status === 401 || response.status === 403) {
            token = null;
            username = null;
            localStorage.removeItem('token');
            localStorage.removeItem('username');
            showLoginPage();
        }

        throw new Error(error.error || error.detail || '请求失败');
    }
    
    return response.json();
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
        
        localStorage.setItem('token', token);
        localStorage.setItem('username', username);
        
        showMainPage();
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
        localStorage.setItem('token', token);
        localStorage.setItem('username', username);
        showMainPage();
    } catch (error) {
        showError(error.message);
    }
}

async function logout() {
    try {
        if (token) {
            await fetch(`${API_BASE}/auth/logout`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                }
            });
        }
    } catch (e) {
        /* 网络错误仍清除本地会话 */
    }
    token = null;
    username = null;
    localStorage.removeItem('token');
    localStorage.removeItem('username');

    closeSettings();
    showLoginPage();
}

// ==================== 管理员 ====================

function getAdminToken() {
    return sessionStorage.getItem('adminToken');
}

function setAdminToken(t) {
    if (t) {
        sessionStorage.setItem('adminToken', t);
    } else {
        sessionStorage.removeItem('adminToken');
    }
}

async function apiAdminRequest(endpoint, options = {}) {
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };
    const at = getAdminToken();
    if (at) {
        headers.Authorization = `Bearer ${at}`;
    }
    const response = await fetch(`${API_BASE}${endpoint}`, {
        ...options,
        headers
    });
    let data = {};
    try {
        data = await response.json();
    } catch (_) {
        /* ignore */
    }
    if (!response.ok) {
        if (response.status === 401) {
            setAdminToken(null);
        }
        throw new Error(data.error || data.detail || '请求失败');
    }
    return data;
}

function showAdminNotice(msg) {
    const el = document.getElementById('admin-notice');
    if (!el) return;
    el.textContent = msg || '';
    el.style.display = msg ? 'block' : 'none';
}

function showAdminLoginPanel() {
    const lp = document.getElementById('admin-login-panel');
    const db = document.getElementById('admin-dashboard');
    if (lp) lp.style.display = 'block';
    if (db) db.style.display = 'none';
}

function showAdminDashboardPanel() {
    const lp = document.getElementById('admin-login-panel');
    const db = document.getElementById('admin-dashboard');
    if (lp) lp.style.display = 'none';
    if (db) db.style.display = 'block';
}

function renderAdminUsers(users) {
    const tbody = document.getElementById('admin-users-tbody');
    if (!tbody) return;
    tbody.innerHTML = (users || []).map((u) => {
        const en = u.enabled !== false;
        const chk = en ? 'checked' : '';
        const plan = u.plan || 'free';
        const planLabel = plan === 'paid' ? '<span class="plan-badge-paid">付费</span>' : '<span class="plan-badge-free">免费</span>';
        return `
            <tr>
                <td>${escapeHtml(u.username)}</td>
                <td>${escapeHtml(u.pending_words)}</td>
                <td>${escapeHtml(u.mastered_words)}</td>
                <td>${planLabel}</td>
                <td>${en ? '正常' : '已禁用'}</td>
                <td>
                    <label class="admin-toggle">
                        <input type="checkbox" data-admin-user="${escapeHtml(u.username)}" ${chk} />
                        启用
                    </label>
                </td>
                <td>
                    <button type="button" class="btn-admin-pw" data-admin-set-password="${escapeHtml(u.username)}">设置密码</button>
                    <button type="button" class="btn-admin-plan" data-admin-set-plan="${escapeHtml(u.username)}" data-current-plan="${escapeHtml(plan)}">${plan === 'paid' ? '降为免费' : '升为付费'}</button>
                </td>
            </tr>`;
    }).join('');

    tbody.querySelectorAll('input[data-admin-user]').forEach((inp) => {
        inp.addEventListener('change', async () => {
            const un = inp.getAttribute('data-admin-user');
            const want = inp.checked;
            try {
                await apiAdminRequest(`/admin/users/${encodeURIComponent(un)}/enabled`, {
                    method: 'PATCH',
                    body: JSON.stringify({ enabled: want })
                });
                await loadAdminDashboard();
            } catch (e) {
                showAdminNotice(e.message || '操作失败');
                inp.checked = !want;
            }
        });
    });

    tbody.querySelectorAll('[data-admin-set-password]').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const un = btn.getAttribute('data-admin-set-password');
            const p1 = window.prompt(`为用户「${un}」设置新密码（至少6位）`, '');
            if (p1 === null) return;
            const p2 = window.prompt('请再次输入新密码', '');
            if (p2 === null) return;
            if (p1 !== p2) {
                showAdminNotice('两次输入的密码不一致');
                return;
            }
            if (p1.length < 6) {
                showAdminNotice('密码至少6个字符');
                return;
            }
            showAdminNotice('');
            try {
                await apiAdminRequest(`/admin/users/${encodeURIComponent(un)}/password`, {
                    method: 'PATCH',
                    body: JSON.stringify({ password: p1 })
                });
                showAdminNotice('密码已更新，该用户需重新登录');
            } catch (e) {
                showAdminNotice(e.message || '设置失败');
            }
        });
    });

    tbody.querySelectorAll('[data-admin-set-plan]').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const un = btn.getAttribute('data-admin-set-plan');
            const cur = btn.getAttribute('data-current-plan');
            const newPlan = cur === 'paid' ? 'free' : 'paid';
            showAdminNotice('');
            try {
                await apiAdminRequest(`/admin/users/${encodeURIComponent(un)}/plan`, {
                    method: 'PATCH',
                    body: JSON.stringify({ plan: newPlan })
                });
                await loadAdminDashboard();
                showAdminNotice(`用户 ${un} 已设置为${newPlan === 'paid' ? '付费' : '免费'}版`);
            } catch (e) {
                showAdminNotice(e.message || '设置失败');
            }
        });
    });
}

function renderAdminInvites(invites) {
    const tbody = document.getElementById('admin-invites-tbody');
    if (!tbody) return;
    tbody.innerHTML = (invites || []).map((inv) => {
        const st = inv.status === 'used' ? '已使用' : '未使用';
        return `
            <tr>
                <td>${escapeHtml(inv.created_at || '—')}</td>
                <td>${escapeHtml(st)}</td>
                <td>${escapeHtml(inv.used_by || '—')}</td>
            </tr>`;
    }).join('');
}

function populateAdminWordsUserSelect(users) {
    const sel = document.getElementById('admin-words-user');
    if (!sel) return;
    const prev = sel.value;
    sel.innerHTML = (users || []).map((u) => {
        const un = u.username;
        return `<option value="${escapeHtml(un)}">${escapeHtml(un)}</option>`;
    }).join('');
    if (prev && [...sel.options].some((o) => o.value === prev)) {
        sel.value = prev;
    } else if (sel.options.length) {
        sel.selectedIndex = 0;
    }
}

function renderAdminWordsTable(words) {
    const tbody = document.getElementById('admin-words-tbody');
    const emptyEl = document.getElementById('admin-words-empty');
    const selAll = document.getElementById('admin-words-select-all');
    if (selAll) selAll.checked = false;
    if (!tbody) return;
    if (!words || !words.length) {
        tbody.innerHTML = '';
        if (emptyEl) emptyEl.style.display = 'block';
        return;
    }
    if (emptyEl) emptyEl.style.display = 'none';
    tbody.innerHTML = words.map((w) => {
        const en = escapeHtml(w.english);
        const stLabel = w.status === 'mastered' ? '已掌握' : '待复习';
        return `
            <tr>
                <td class="admin-words-col-cb"><input type="checkbox" class="admin-word-cb" data-english="${escapeHtml(w.english)}" /></td>
                <td>${en}</td>
                <td>${escapeHtml(w.chinese)}</td>
                <td>${escapeHtml(stLabel)}</td>
                <td><button type="button" class="btn btn-danger-outline admin-word-delete" data-english="${escapeHtml(w.english)}">删除</button></td>
            </tr>`;
    }).join('');
}

async function loadAdminUserWords() {
    const sel = document.getElementById('admin-words-user');
    const user = sel && sel.value;
    const tbody = document.getElementById('admin-words-tbody');
    const emptyEl = document.getElementById('admin-words-empty');
    if (!user) {
        if (tbody) tbody.innerHTML = '';
        if (emptyEl) emptyEl.style.display = 'block';
        return;
    }
    if (emptyEl) emptyEl.style.display = 'none';
    try {
        const status = (document.getElementById('admin-words-status') || {}).value || 'all';
        const q = ((document.getElementById('admin-words-q') || {}).value || '').trim();
        const params = new URLSearchParams({ status, q });
        const data = await apiAdminRequest(`/admin/users/${encodeURIComponent(user)}/words?${params}`);
        renderAdminWordsTable(data.words || []);
    } catch (e) {
        showAdminNotice(e.message || '加载失败');
        renderAdminWordsTable([]);
    }
}

async function adminConfirmDeleteWords(englishList) {
    const sel = document.getElementById('admin-words-user');
    const user = sel && sel.value;
    if (!user || !englishList.length) return;
    const preview = englishList.slice(0, 5).join('、');
    const more = englishList.length > 5 ? ` 等共 ${englishList.length} 个` : '';
    const ok = window.confirm(`确定从用户「${user}」的学词数据中永久删除：${preview}${more}？\n\n此操作不可恢复。`);
    if (!ok) return;
    await adminDeleteUserWords(englishList);
}

async function adminDeleteUserWords(englishList) {
    const sel = document.getElementById('admin-words-user');
    const user = sel && sel.value;
    if (!user || !englishList || !englishList.length) return;
    showAdminNotice('');
    try {
        const data = await apiAdminRequest(`/admin/users/${encodeURIComponent(user)}/words`, {
            method: 'DELETE',
            body: JSON.stringify({ english: englishList })
        });
        const parts = [];
        if (data.removed) parts.push(`已删除 ${data.removed} 个`);
        if (data.not_found && data.not_found.length) parts.push(`未找到 ${data.not_found.length} 个`);
        showAdminNotice(parts.join('；') || '完成');
        await loadAdminDashboard();
    } catch (e) {
        showAdminNotice(e.message || '删除失败');
    }
}

async function loadAdminDashboard() {
    const [usersRes, invRes, cfgRes] = await Promise.all([
        apiAdminRequest('/admin/users'),
        apiAdminRequest('/admin/invites'),
        apiAdminRequest('/admin/config').catch(() => null),
    ]);
    renderAdminUsers(usersRes.users);
    renderAdminInvites(invRes.invites);
    renderAdminDeepseekStatus(cfgRes);
    populateAdminWordsUserSelect(usersRes.users);
    await loadAdminUserWords();
    showAdminDashboardPanel();
}

function renderAdminDeepseekStatus(cfg) {
    const el = document.getElementById('admin-deepseek-status');
    if (!el) return;
    if (!cfg) {
        el.textContent = '无法读取配置';
        return;
    }
    if (cfg.deepseek_api_key_set) {
        el.textContent = `当前已配置 API Key（${cfg.deepseek_api_key_preview}）。付费版功能可正常使用。`;
        el.style.color = 'var(--primary-dark)';
    } else {
        el.textContent = '尚未配置 DeepSeek API Key。付费版功能（文章AI提取、词汇导入）将不可用。';
        el.style.color = 'var(--error-color)';
    }
}

async function openAdminOverlay() {
    const ov = document.getElementById('admin-overlay');
    if (!ov) return;
    ov.style.display = 'flex';
    ov.setAttribute('aria-hidden', 'false');
    showAdminNotice('');
    const once = document.getElementById('admin-invite-once');
    if (once) {
        once.style.display = 'none';
        once.textContent = '';
    }

    try {
        const st = await fetch(`${API_BASE}/admin/status`).then((r) => r.json());
        if (!st.admin_configured) {
            showAdminNotice('服务器未配置管理员：请设置环境变量 ADMIN_USERNAME 与 ADMIN_PASSWORD，或 ADMIN_PASSWORD_HASH。');
            showAdminLoginPanel();
            const lp = document.getElementById('admin-login-panel');
            if (lp) lp.style.display = 'none';
            const db = document.getElementById('admin-dashboard');
            if (db) db.style.display = 'none';
            return;
        }
    } catch (_) {
        /* 忽略 */
    }

    const at = getAdminToken();
    if (at) {
        try {
            await loadAdminDashboard();
            return;
        } catch (_) {
            setAdminToken(null);
        }
    }
    showAdminLoginPanel();
}

function closeAdminOverlay() {
    const ov = document.getElementById('admin-overlay');
    if (!ov) return;
    ov.style.display = 'none';
    ov.setAttribute('aria-hidden', 'true');
}

// ==================== 页面切换 ====================

function showLoginPage() {
    document.getElementById('login-page').classList.add('active');
    document.getElementById('main-page').classList.remove('active');
    const gl = document.getElementById('admin-gear-login');
    if (gl) gl.style.display = '';
    document.querySelector('#main-page .nav-user')?.removeAttribute('aria-label');
    updateNavUserAvatar(null);
    updatePkInviteBadgeFromDuels([]);
    closePkInviteModal();
    stopPkInvitePolling();
}

function showMainPage() {
    document.getElementById('login-page').classList.remove('active');
    document.getElementById('main-page').classList.add('active');
    const gl = document.getElementById('admin-gear-login');
    if (gl) gl.style.display = 'none';
    const udisp = document.getElementById('username-display');
    if (udisp) udisp.textContent = username;
    document.querySelector('#main-page .nav-user')?.setAttribute(
        'aria-label',
        username ? `当前用户 ${username}` : ''
    );

    loadStats();
    refreshNavUserAvatar();
    void refreshPkInviteIndicator();
    startPkInvitePolling();
    // 获取用户套餐
    loadUserPlan();
    // 默认展示「今日复习」区块；须拉取列表，否则会一直显示 index.html 里的占位词（如 apple）
    showSection('review');
}

async function loadUserPlan() {
    try {
        const data = await apiRequest('/user/plan');
        userPlan = data.plan || 'free';
        updatePlanUI();
    } catch (_) {
        userPlan = 'free';
    }
}

function updatePlanUI() {
    const hint = document.getElementById('article-plan-hint');
    if (hint) {
        if (userPlan === 'paid') {
            hint.textContent = '（付费版：使用 AI 智能提取单词原形）';
            hint.className = 'plan-hint paid';
        } else {
            hint.textContent = '（免费版：按空格分词匹配词库）';
            hint.className = 'plan-hint free';
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
}

function showSection(sectionId) {
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
    if (moreBtn && (sectionId === 'progress' || sectionId === 'mastered' || sectionId === 'discover')) {
        moreBtn.classList.add('active');
    }

    closeMobileMoreSheet();

    if (sectionId === 'review') {
        loadReviewList();
    } else if (sectionId === 'discover') {
        loadDiscovery();
    } else if (sectionId === 'progress') {
        loadProgress();
    } else if (sectionId === 'mastered') {
        loadMastered();
    } else if (sectionId === 'leaderboard') {
        loadLeaderboardSection();
    }
}

// ==================== 统计功能 ====================

async function loadStats() {
    try {
        const data = await apiRequest('/words/status');

        document.getElementById('review-count').textContent = data.words.filter(w => 
            new Date(w.next_review_date) <= new Date()
        ).length;

        document.getElementById('mastered-count').textContent = data.stats.mastered_words;
        document.getElementById('round-count').textContent = data.stats.current_round + 1;

        if (document.getElementById('progress-section')?.classList.contains('active')) {
            renderLearningMap(data.stats.mastered_words);
        }
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

function renderWrongPanel() {
    const ul = document.getElementById('wrong-words-list');
    const empty = document.getElementById('wrong-words-empty');
    if (!ul || !empty) return;
    ul.innerHTML = '';
    for (const en of wrongWordsOrder) {
        const w = wordMap.get(en);
        if (!w) continue;
        const li = document.createElement('li');
        li.innerHTML = `<span class="ww-en">${escapeHtml(w.english)}</span><span class="ww-zh">${escapeHtml(w.chinese)}</span>`;
        ul.appendChild(li);
    }
    empty.style.display = wrongWordsOrder.length === 0 ? 'block' : 'none';
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

        const data = await apiRequest('/words/review');
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

    // 本题仅填写单词原形（english），例句中可为变形，挖空仍按变形匹配
    const targetAnswer = (word.english || '').trim();
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

    // 显示中文意思
    document.getElementById('current-word-chinese').textContent = word.chinese;
    const maxSucc = word.max_success_count != null ? word.max_success_count : 8;
    document.getElementById('current-word-progress').textContent = `${word.success_count}/${maxSucc}`;
    
    // 处理例句：隐藏目标词（可能是变形）
    let exampleText = word.example || '暂无例句';
    if (exampleText !== '暂无例句') {
        const parts = exampleText.split('_');
        if (parts.length >= 2) {
            let englishPart = parts[0];
            // 隐藏变形或原形
            const maskTarget = (word.example_form || '').trim() || word.english;
            const regex = new RegExp(`\\b${escapeRegExp(maskTarget)}\\b`, 'gi');
            englishPart = englishPart.replace(regex, '_'.repeat(maskTarget.length));
            // 如果没有替换到，也尝试原形
            if (maskTarget !== word.english) {
                const regex2 = new RegExp(`\\b${escapeRegExp(word.english)}\\b`, 'gi');
                englishPart = englishPart.replace(regex2, '_'.repeat(word.english.length));
            }
            exampleText = englishPart + ' → ' + parts.slice(1).join('_');
        } else {
            const maskTarget = (word.example_form || '').trim() || word.english;
            const regex = new RegExp(`\\b${escapeRegExp(maskTarget)}\\b`, 'gi');
            exampleText = exampleText.replace(regex, '_'.repeat(maskTarget.length));
        }
    }
    
    document.getElementById('current-word-example').textContent = exampleText;
    
    // 提示字符串基于原形长度
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
}

/** 根据 target（变形或原形）生成提示字符串 */
function getHintStringForTarget(target, revealedCount) {
    if (!target) return '';
    if (revealedCount >= target.length) return target;
    return target.substring(0, revealedCount) + '_'.repeat(target.length - revealedCount);
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
    
    try {
        const result = await apiRequest('/words/practice', {
            method: 'POST',
            body: JSON.stringify({
                word_id: word.english,
                answer: answer,
                remedial: wrongRoundNumber > 0 && reviewSessionMode !== 'bonus',
                bonus_practice: reviewSessionMode === 'bonus'
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
        if (result.correct && result.gamification) {
            const gm = result.gamification;
            if (gm.xp_gained > 0) {
                msgText += ` +${gm.xp_gained} XP（累计 ${formatNumber(gm.total_xp)} · Lv.${gm.level} · 连续 ${gm.streak} 天）`;
            } else {
                msgText += `（累计 ${formatNumber(gm.total_xp)} · Lv.${gm.level} · 连续 ${gm.streak} 天）`;
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
                updateSettingsCheckinHintFromProfile(gm);
                updateSettingsMonthlyGoalBonusNotice(lastGamificationProfile);
            }
        }
        messageDiv.textContent = msgText;
        messageDiv.className = `word-message ${result.correct ? 'success' : 'error'}`;
        messageDiv.style.display = 'block';
        
        const targetAnswer = word._targetAnswer || word.english;
        if (result.correct) {
            // 答案正确，显示完整答案，然后进入下一个单词
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
        const msg = error.message || '提交失败，请重试';
        const reviewSection = document.getElementById('review-section');
        const messageDiv = document.getElementById('word-message');
        if (reviewSection && reviewSection.classList.contains('active') && messageDiv) {
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

function learningMapRenderNode(ms, m, nextGoal) {
    const unlocked = m >= ms.n;
    const isNext = Boolean(nextGoal && ms.n === nextGoal.n);
    const stateClass = unlocked ? 'learning-map-node--unlocked' : 'learning-map-node--locked';
    const nextClass = isNext ? ' learning-map-node--next' : '';
    const countLabel = ms.n === 0 ? '起点' : `${formatNumber(ms.n)} 词`;
    const aria = `Lv.${ms.level} ${ms.title}，目标 ${ms.n === 0 ? '0' : formatNumber(ms.n)} 词，${
        unlocked ? '已达成' : '未达成'
    }`;
    const veilHtml = unlocked
        ? ''
        : '<div class="learning-map-node-veil" aria-hidden="true"></div>';
    return `
        <div class="learning-map-node ${stateClass}${nextClass}" role="group" aria-label="${escapeHtml(aria)}">
            <div class="learning-map-node-box">
                <span class="learning-map-node-level">Lv.${ms.level}</span>
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
            hint.textContent = `当前已掌握 ${formatNumber(m)} 词 · 下一目标 Lv.${nextGoal.level}「${nextGoal.title}」（${formatNumber(
                nextGoal.n,
            )} 词）还需 ${formatNumber(remain)} 词 · 终极 Lv.12 为 ${formatNumber(4000)} 词`;
        } else {
            hint.textContent = `当前已掌握 ${formatNumber(m)} 词 · 已达成 Lv.12 词海终极（${formatNumber(
                4000,
            )} 词），继续保持！`;
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

async function loadProgress() {
    try {
        const data = await apiRequest('/words/status');

        renderLearningMap(data.stats.mastered_words);

        // 显示统计
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
        document.getElementById('progress-stats').innerHTML = statsHtml;
        
        // 显示单词列表
        const listHtml = data.words.map((word) => {
            const nextLine = escapeHtml(formatNextReviewLine(word));
            const phoneticHtml = word.phonetic ? `<span class="word-item-phonetic">${escapeHtml(word.phonetic)}</span>` : '';
            const coTag = word.is_carryover
                ? `<span class="word-item-carryover-tag">遗留${word.carryover_days ? ` · 逾期${word.carryover_days}天` : ''}</span>`
                : '';
            return `
            <div class="word-item">
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
            </div>
        `;
        }).join('');
        
        document.getElementById('word-list').innerHTML = listHtml || '<p style="padding: 20px; text-align: center; color: #999;">暂无单词</p>';
    } catch (error) {
        showMainBanner('加载进度失败，请稍后重试');
    }
}

// ==================== 已掌握功能 ====================

async function loadMastered() {
    try {
        const data = await apiRequest('/words/mastered');
        
        const listHtml = data.words.map(word => {
            const phoneticHtml = word.phonetic ? `<span class="word-item-phonetic">${escapeHtml(word.phonetic)}</span>` : '';
            return `
            <div class="word-item">
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
            </div>
        `;
        }).join('');
        
        document.getElementById('mastered-list').innerHTML = listHtml || '<p style="padding: 20px; text-align: center; color: #999;">暂无已掌握单词</p>';
    } catch (error) {
        showMainBanner('加载已掌握列表失败，请稍后重试');
    }
}

// ==================== 单词学习（Discovery Card） ====================

let discoveryDeck = [];
let discoveryIndex = 0;
/** 「今日单词」模式：牌组仅为今日复习列表 */
let discoveryModeToday = false;

const DISCOVERY_LEVEL_STORAGE_KEY = 'english_reciter_discovery_level';

/** 选择具体难度时，待复习词也须属于该难度（与系统词库 level 一致） */
const DISCOVERY_BANK_LEVELS = new Set(['小学', '初中', '高中', 'GRE']);

function discoveryExamplesFromCsvRow(row) {
    const out = [];
    for (const k of ['1', '2']) {
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

async function loadDiscovery() {
    const emptyEl = document.getElementById('discovery-empty');
    const rootEl = document.getElementById('discovery-root');
    const level = String(getDiscoverySelectedLevel() || '').trim();
    discoveryModeToday = false;
    try {
        if (level === 'today') {
            discoveryModeToday = true;
            const rev = await apiRequest('/words/review');
            const list = rev.words || [];
            discoveryDeck = list.map((w) => ({
                english: w.english,
                chinese: w.chinese,
                phonetic: w.phonetic || '',
                examples: buildPendingDiscoveryExamples(w),
                source: 'today',
                next_review_date: w.scheduled_due_date,
            }));
            if (discoveryIndex >= discoveryDeck.length) discoveryIndex = 0;

            if (discoveryDeck.length === 0) {
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
            renderDiscoveryCard();
            return;
        }

        const csvPath = level ? `/wordbank/csv?level=${encodeURIComponent(level)}` : '/wordbank/csv';
        const [st, wb] = await Promise.all([apiRequest('/words/status'), apiRequest(csvPath)]);

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
    const examples = getDiscoveryExamplesForCard(w);
    const exampleBlock = discoveryTwoExampleSlotsHtml(examples);
    const html = `
      <article class="discovery-card" aria-label="单词卡片 ${escapeHtml(w.english)}">
        <div class="discovery-card-body">
          <div class="discovery-card-word-row">
            <h3 class="discovery-card-word">${escapeHtml(w.english)}</h3>
            <button type="button" class="btn-speak discovery-speak-word" title="朗读单词" aria-label="朗读 ${escapeHtml(w.english)}">🔊</button>
          </div>
          ${phonBlock}
          ${exampleBlock}
        </div>
      </article>
    `;
    wrap.innerHTML = html;
    if (counter) {
        const n = discoveryDeck.length;
        const i = discoveryIndex + 1;
        if (discoveryModeToday && w.source === 'today') {
            counter.textContent = `${i} / ${n}（今日复习）`;
        } else {
            counter.textContent = `${i} / ${n}`;
        }
    }

    const speakBtn = wrap.querySelector('.discovery-speak-word');
    if (speakBtn) {
        speakBtn.addEventListener('click', () => {
            speakEnglishInBrowser(w.english, () => {});
        });
    }
    wrap.querySelectorAll('.discovery-speak-example').forEach((btn) => {
        const si = btn.getAttribute('data-example-slot');
        const seg = si != null ? examples[parseInt(si, 10)] : null;
        if (!seg || !seg.en) return;
        btn.addEventListener('click', () => {
            speakEnglishInBrowser(seg.en, () => {});
        });
    });
}

/** 固定 2 条例句槽位，减少切换单词时卡片高度跳动 */
function discoveryTwoExampleSlotsHtml(examples) {
    const slots = [];
    for (let idx = 0; idx < 2; idx++) {
        const ex = examples[idx];
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
    if (!q) {
        wbState.filtered = [];
        wbState.loading = false;
        renderWordbankMeta();
        renderWordbankList();
        return;
    }
    wbState.loading = true;
    renderWordbankMeta();
    try {
        const params = new URLSearchParams({ q });
        const data = await apiRequest(`/wordbank/csv/search?${params}`);
        wbState.filtered = Array.isArray(data.words) ? data.words : [];
    } catch (e) {
        wbState.filtered = [];
        showMessage(e.message || '搜索失败', 'error');
    } finally {
        wbState.loading = false;
    }
    renderWordbankMeta();
    renderWordbankList();
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
    if (!wbState.selected.size) {
        showMessage('请先勾选单词', 'error');
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
        showMessage('没有可导入的词条（请重新搜索并勾选）', 'error');
        return;
    }
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
        const parts = [];
        if (added > 0) parts.push(`新加入 ${added} 个`);
        if (skipped > 0) parts.push(`已有 ${skipped} 个在学习列表中（重复）`);
        if (invalid > 0) parts.push(`${invalid} 条无效已忽略`);
        let msg = parts.join('；') || '完成';
        if (dupUnique.length) {
            const show = dupUnique.slice(0, 28);
            msg += `。重复词条：${show.join('、')}${dupUnique.length > 28 ? '…' : ''}`;
        }
        const msgType = added > 0 ? 'success' : 'info';
        showMessage(msg, msgType, 6500);
        // 清空已选
        wbState.selected.clear();
        wbState.selectedMap.clear();
        updateWordbankSelectedCount();
        renderWordbankList();
        loadStats();
    } catch (error) {
        showMessage(error.message, 'error');
    } finally {
        if (importBtn) {
            importBtn.disabled = false;
            importBtn.textContent = '将选中的词加入待复习';
        }
    }
}

// ==================== 导入功能 ====================

async function importFromArticle() {
    const ta = document.getElementById('import-article-textarea');
    if (!ta) return;
    const text = ta.value.trim();
    if (!text) {
        showMessage('请先粘贴文章内容', 'error');
        return;
    }
    const btn = document.getElementById('import-article-btn');
    if (btn) btn.disabled = true;
    const resultDiv = document.getElementById('article-import-result');
    if (resultDiv) {
        resultDiv.style.display = 'block';
        resultDiv.innerHTML = '<span class="loading-dots">正在提取词汇…</span>';
    }
    try {
        const data = await apiRequest('/words/import-from-article', {
            method: 'POST',
            body: JSON.stringify({ text })
        });

        const words = Array.isArray(data.words) ? data.words : [];
        const method = data.method === 'deepseek' ? '（AI提取）' : '（空格分词）';

        if (words.length === 0) {
            const st = data.stats;
            let hint = data.message || '未在词库中找到匹配词汇';
            if (st && typeof st.lemmas_total === 'number') {
                hint += `（从文章识别 ${st.lemmas_total} 个不重复英文词，词库中均无匹配）`;
            }
            showMessage(hint, 'error', 5500);
            if (resultDiv) resultDiv.style.display = 'none';
            return;
        }

        // 将提取到的词汇追加进选框，并自动全选（保留原有已选中的词）
        const newKeys = new Set(words.map(w => wordbankKey(w.english)));
        for (const w of words) {
            const k = wordbankKey(w.english);
            wbState.selected.add(k);
            wbState.selectedMap.set(k, w);
        }
        // 合并显示列表：原有 filtered 中已选中的词 + 新导入的词（去重）
        const existingSelected = wbState.filtered.filter(w => {
            const k = wordbankKey(w.english);
            return wbState.selected.has(k) && !newKeys.has(k);
        });
        wbState.filtered = [...existingSelected, ...words];
        wbState.filter = `[文章导入：${words.length} 词]`;

        // 同步更新搜索框显示，让用户知道当前列表来源
        const searchInput = document.getElementById('wordbank-search');
        if (searchInput) {
            searchInput.value = '';
            searchInput.placeholder = `已从文章导入 ${words.length} 个词，可继续搜索…`;
        }

        updateWordbankSelectedCount();
        renderWordbankMeta();
        renderWordbankList();

        // 滚动到词库选框顶部
        const panel = document.getElementById('wordbank-panel');
        if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });

        // 显示提取结果摘要
        if (resultDiv) {
            resultDiv.style.display = 'block';
            const totalSelected = wbState.selected.size;
            const extraHint = totalSelected > words.length ? `（含之前已选的 ${totalSelected - words.length} 词，共 ${totalSelected} 词已勾选）` : '';
            resultDiv.innerHTML =
                `<p class="article-result-title">✓ 已提取 ${words.length} 个词汇 ${method}${extraHint}，请确认后点击「将选中的词加入待复习」。</p>`;
        }
        const st = data.stats;
        if (st && typeof st.lemmas_total === 'number') {
            const modeLabel = data.method === 'deepseek' ? 'AI 提取原形' : '按空格分词';
            showMessage(
                `已从文章得到 ${st.lemmas_total} 个不重复英文词（${modeLabel}）。其中 ${st.matched_in_csv} 个在系统词库中有词条；另有 ${st.not_in_csv} 个词库中暂无。当前已勾选 ${words.length} 条，确认后点击「将选中的词加入待复习」。`,
                'success',
                7000
            );
        }
        ta.value = '';
    } catch (error) {
        showMessage(error.message || '提取失败', 'error');
        if (resultDiv) resultDiv.style.display = 'none';
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function importVocabToCSV() {
    if (userPlan !== 'paid') {
        showMessage('词汇导入功能仅限付费版用户使用', 'error');
        return;
    }
    const ta = document.getElementById('import-vocab-textarea');
    const levelSel = document.getElementById('import-vocab-level');
    const addToQueueCb = document.getElementById('import-vocab-add-to-queue');
    if (!ta) return;
    const raw = ta.value.trim();
    if (!raw) {
        showMessage('请先输入单词列表', 'error');
        return;
    }
    const level = levelSel ? levelSel.value : '';
    const alsoAddToQueue = addToQueueCb ? !!addToQueueCb.checked : true;
    const btn = document.getElementById('import-vocab-btn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '处理中…';
    }
    try {
        const data = await apiRequest('/wordbank/csv/import-words', {
            method: 'POST',
            body: JSON.stringify({ words: raw, level, also_add_to_queue: alsoAddToQueue })
        });
        showMessage(buildVocabImportFeedback(data), 'success', 9000);
        ta.value = '';
        if (levelSel) levelSel.value = '';
        loadStats();
    } catch (error) {
        showMessage(error.message || '导入失败', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '词汇导入';
        }
    }
}

// ==================== 事件监听与初始化 ====================

document.addEventListener('DOMContentLoaded', function() {
    setupVisualViewportKeyboardAvoid();

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
    
    // Tab 切换
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            
            const tabName = tab.dataset.tab;
            document.getElementById('login-form').style.display = tabName === 'login' ? 'block' : 'none';
            document.getElementById('register-form').style.display = tabName === 'register' ? 'block' : 'none';
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

    document.getElementById('pk-invite-btn')?.addEventListener('click', () => {
        void (async () => {
            openPkInviteModal();
            const body = document.getElementById('pk-invite-body');
            if (body) body.innerHTML = '<p class="pk-invite-loading">加载中…</p>';
            try {
                await loadPkInviteModalBody();
            } catch (err) {
                if (body) {
                    body.innerHTML = `<p class="pk-invite-empty" style="color:var(--error-dark)">${escapeHtml(
                        err.message || '加载失败',
                    )}</p>`;
                }
            }
        })();
    });
    document.getElementById('pk-invite-backdrop')?.addEventListener('click', closePkInviteModal);
    document.getElementById('pk-invite-close')?.addEventListener('click', closePkInviteModal);
    document.getElementById('pk-invite-body')?.addEventListener('click', async (e) => {
        const btn = e.target.closest('.pk-invite-respond-btn[data-duel-id]');
        if (!btn) return;
        const id = btn.getAttribute('data-duel-id');
        const accept = btn.getAttribute('data-duel-accept') === '1';
        if (!id) return;
        btn.disabled = true;
        document.querySelectorAll('.pk-invite-respond-btn').forEach((b) => {
            b.disabled = true;
        });
        try {
            await apiRequest(`/challenges/${encodeURIComponent(id)}/respond`, {
                method: 'POST',
                body: JSON.stringify({ accept }),
            });
            showMainBanner(accept ? '已接受 PK 邀约' : '已拒绝 PK 邀约');
            await refreshPkInviteIndicator();
            if (isSettingsOverlayOpen()) await loadUserSettingsPanel();
            await loadPkInviteModalBody();
            const b = document.getElementById('pk-invite-body');
            if (b && b.querySelector('.pk-invite-empty')) {
                closePkInviteModal();
            }
        } catch (err) {
            showMainBanner(err.message || '操作失败');
        } finally {
            document.querySelectorAll('.pk-invite-respond-btn').forEach((x) => {
                x.disabled = false;
            });
        }
    });

    document.getElementById('settings-backdrop')?.addEventListener('click', closeSettings);
    document.getElementById('settings-close')?.addEventListener('click', closeSettings);
    document.getElementById('settings-avatar-input')?.addEventListener('change', (e) => {
        const inp = e.target;
        const f = inp.files && inp.files[0];
        inp.value = '';
        if (!f) return;
        openAvatarCropModal(f);
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
    document.getElementById('settings-pool-join')?.addEventListener('click', async () => {
        try {
            await apiRequest('/monthly-pool/join', { method: 'POST', body: '{}' });
            setSettingsMessage('已加入本月奖池');
            await loadUserSettingsPanel();
        } catch (err) {
            setSettingsMessage(err.message || '加入失败', true);
        }
    });
    document.getElementById('settings-duel-send')?.addEventListener('click', async () => {
        const tin = document.getElementById('settings-duel-target-select');
        const t = (tin && tin.value) || '';
        const w = document.getElementById('settings-duel-wager');
        const wager = w ? parseInt(w.value, 10) : 0;
        if (!t.trim()) {
            setSettingsMessage('请选择对手', true);
            return;
        }
        try {
            await apiRequest('/challenges', {
                method: 'POST',
                body: JSON.stringify({ target_username: t.trim(), wager_xp: wager }),
            });
            setSettingsMessage('挑战已发出');
            if (tin) tin.value = '';
            await loadUserSettingsPanel();
        } catch (err) {
            setSettingsMessage(err.message || '发起失败', true);
        }
    });
    document.getElementById('settings-duel-list')?.addEventListener('click', async (e) => {
        const btn = e.target.closest('.settings-duel-btn[data-duel-id]');
        if (!btn) return;
        const id = btn.getAttribute('data-duel-id');
        const accept = btn.getAttribute('data-duel-accept') === '1';
        if (!id) return;
        try {
            await apiRequest(`/challenges/${encodeURIComponent(id)}/respond`, {
                method: 'POST',
                body: JSON.stringify({ accept }),
            });
            setSettingsMessage(accept ? '已接受挑战' : '已拒绝');
            await loadUserSettingsPanel();
        } catch (err) {
            setSettingsMessage(err.message || '操作失败', true);
        }
    });
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        const crop = document.getElementById('avatar-crop-overlay');
        if (crop && crop.style.display !== 'none') {
            closeAvatarCropModal();
            return;
        }
        const pkModal = document.getElementById('pk-invite-modal');
        if (pkModal && pkModal.style.display !== 'none') {
            closePkInviteModal();
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
    
    const importArticleBtn = document.getElementById('import-article-btn');
    if (importArticleBtn) {
        importArticleBtn.addEventListener('click', importFromArticle);
    }
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
            // 刷新状态显示
            const cfg = await apiAdminRequest('/admin/config').catch(() => null);
            renderAdminDeepseekStatus(cfg);
        } catch (e) {
            showAdminNotice(e.message || '保存失败');
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
    });
    
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) return;
        const main = document.getElementById('main-page');
        if (!main || !main.classList.contains('active')) return;
        if (!token || !username) return;
        void refreshPkInviteIndicator();
    });

    // 初始化页面
    if (token && username) {
        showMainPage();
    } else {
        showLoginPage();
    }
});
