async function loadParentLearningSettings(accountContext = captureAccountContext()) {
    if (!isAccountContextCurrent(accountContext)) return null;
    const settings = await apiRequest('/user/learning-settings');
    if (!isAccountContextCurrent(accountContext)) return null;
    const dailyInput = document.getElementById('settings-daily-review-limit');
    if (dailyInput) {
        dailyInput.max = String(settings.max_daily_review_limit || 300);
        dailyInput.value = String(settings.daily_review_limit ?? 120);
    }
    return settings;
}

const AVATAR_CROP_VIEW = 280;
let currentNavAvatarUrl = '';
let avatarViewPreviouslyFocused = null;
let avatarViewTrigger = null;
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

function resetGamificationNav() {
    lastGamificationProfile = null;
    updateGamificationNav({
        level: 1,
        total_xp: 0,
        streak: 0,
        today_correct_count: 0,
        check_in_min_correct: 5,
        check_in_done_today: false,
        makeup_checkin: { available: false },
    });
}

function avatarOriginalDisplayUrl(url) {
    if (!url) return '';
    const sep = url.indexOf('?') >= 0 ? '&' : '?';
    return `${url}${sep}t=${Date.now()}`;
}

function closeAvatarViewModal() {
    const modal = document.getElementById('avatar-view-modal');
    const img = document.getElementById('avatar-view-image');
    if (!modal || modal.style.display === 'none') return;
    modal.style.display = 'none';
    modal.setAttribute('aria-hidden', 'true');
    modal.classList.remove('is-leaderboard-view');
    avatarViewTrigger?.setAttribute('aria-expanded', 'false');
    if (img) {
        img.onload = null;
        img.onerror = null;
        img.removeAttribute('src');
    }
    if (avatarViewPreviouslyFocused instanceof HTMLElement) {
        avatarViewPreviouslyFocused.focus();
    }
    avatarViewPreviouslyFocused = null;
    avatarViewTrigger = null;
}

function showAvatarViewModal(options = {}) {
    const modal = document.getElementById('avatar-view-modal');
    const title = document.getElementById('avatar-view-title');
    const img = document.getElementById('avatar-view-image');
    const placeholder = document.getElementById('avatar-view-placeholder');
    const actions = document.getElementById('avatar-view-actions');
    const input = document.getElementById('avatar-view-replace-input');
    const message = document.getElementById('avatar-view-message');
    const details = document.getElementById('avatar-view-leaderboard-details');
    const closeButton = document.getElementById('avatar-view-close');
    if (!modal || !img || !placeholder) return;

    const leaderboard = options.leaderboard || null;
    const allowReplace = options.allowReplace === true;
    avatarViewPreviouslyFocused = document.activeElement;
    avatarViewTrigger = options.trigger instanceof HTMLElement ? options.trigger : null;
    if (title) title.textContent = options.title || '头像';
    if (actions) actions.hidden = !allowReplace;
    if (input) {
        input.disabled = !allowReplace;
        input.value = '';
    }
    if (message) {
        message.textContent = options.message || '';
        message.hidden = !options.message;
    }
    if (details) {
        details.hidden = !leaderboard;
        if (leaderboard) {
            const rank = Math.max(1, Number(leaderboard.rank) || 1);
            const rewardXp = Math.max(0, Number(leaderboard.rewardXp) || 0);
            const rankEl = document.getElementById('avatar-view-rank');
            const kindEl = document.getElementById('avatar-view-period-kind');
            const periodXpEl = document.getElementById('avatar-view-period-xp');
            const rewardXpEl = document.getElementById('avatar-view-reward-xp');
            const rewardStateEl = document.getElementById('avatar-view-reward-state');
            const periodLabelEl = document.getElementById('avatar-view-period-label');
            if (rankEl) {
                rankEl.className = `avatar-view-rank rank-${Math.min(rank, 3)}`;
                rankEl.textContent = `第 ${rank} 名`;
            }
            if (kindEl) kindEl.textContent = leaderboard.kind || '';
            if (periodXpEl) periodXpEl.textContent = `${formatNumber(leaderboard.periodXp || 0)} XP`;
            if (rewardXpEl) {
                rewardXpEl.textContent = rewardXp > 0 ? `+${formatNumber(rewardXp)} XP` : '无额外奖励';
            }
            if (rewardStateEl) {
                rewardStateEl.textContent = rewardXp > 0 ? '奖励已发放' : '本期无额外奖励';
                rewardStateEl.classList.toggle('is-empty', rewardXp <= 0);
            }
            if (periodLabelEl) periodLabelEl.textContent = leaderboard.periodLabel || '';
        }
    }

    img.hidden = true;
    placeholder.hidden = false;
    img.alt = options.imageAlt || '头像大图';
    const originalUrl = avatarOriginalDisplayUrl(options.avatarUrl || '');
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

    modal.classList.toggle('is-leaderboard-view', Boolean(leaderboard));
    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');
    avatarViewTrigger?.setAttribute('aria-expanded', 'true');
    closeButton?.focus();
}

function openAvatarViewModal() {
    const viewedUsername = isParentSession && childUsername ? childUsername : username;
    showAvatarViewModal({
        title: isParentSession && childUsername ? `${childUsername} 的头像` : '我的头像',
        avatarUrl: currentNavAvatarUrl,
        imageAlt: viewedUsername ? `${viewedUsername} 的头像大图` : '当前头像大图',
        allowReplace: !isParentSession,
        message: isParentSession ? '家长查看模式不能替换学生头像' : '',
        trigger: document.getElementById('nav-user-avatar-wrap'),
    });
}

function openLeaderboardAvatarModal(entry, podium, kind, trigger) {
    showAvatarViewModal({
        title: `${entry.username || '用户'} 的奖励`,
        avatarUrl: entry.avatar_url || '',
        imageAlt: `${entry.username || '用户'} 的头像大图`,
        trigger,
        leaderboard: {
            rank: entry.rank,
            kind: `上期${kind}`,
            periodXp: entry.period_xp,
            rewardXp: entry.reward_xp,
            periodLabel: podium.period_label || podium.period_id || '',
        },
    });
}

async function refreshNavUserAvatar(accountContext = captureAccountContext()) {
    if (!token || !username) {
        if (isAccountContextCurrent(accountContext)) updateNavUserAvatar(null);
        return;
    }
    try {
        const s = await apiRequest('/user/avatar-meta');
        if (!isAccountContextCurrent(accountContext)) return;
        updateNavUserAvatar(s && s.avatar_url);
    } catch (_) {
        if (isAccountContextCurrent(accountContext)) updateNavUserAvatar(null);
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
    if (img) {
        img.onload = null;
        img.onerror = null;
        img.removeAttribute('src');
    }
    if (avatarCrop.objectUrl) {
        URL.revokeObjectURL(avatarCrop.objectUrl);
        avatarCrop.objectUrl = null;
    }
    avatarCrop.dragging = false;
}

function openAvatarCropModal(file) {
    const accountContext = captureAccountContext();
    if (!file || !file.type.startsWith('image/')) {
        setSettingsMessage('请选择图片文件', true);
        return;
    }
    closeAvatarCropModal();
    const img = document.getElementById('avatar-crop-img');
    const overlay = document.getElementById('avatar-crop-overlay');
    if (!img || !overlay) return;
    const objectUrl = URL.createObjectURL(file);
    avatarCrop.objectUrl = objectUrl;
    img.onload = () => {
        if (!isAccountContextCurrent(accountContext) || avatarCrop.objectUrl !== objectUrl) return;
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
        if (!isAccountContextCurrent(accountContext) || avatarCrop.objectUrl !== objectUrl) return;
        setSettingsMessage('无法加载图片', true);
        closeAvatarCropModal();
    };
    img.src = avatarCrop.objectUrl;
}

async function confirmAvatarCropUpload() {
    const accountContext = captureAccountContext();
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
    if (!isAccountContextCurrent(accountContext)) return;
    if (!uploadBlob || uploadBlob.size < 20) {
        uploadBlob = await new Promise((resolve) => {
            canvas.toBlob((b) => resolve(b), 'image/jpeg', 0.9);
        });
        if (!isAccountContextCurrent(accountContext)) return;
    }
    if (!uploadBlob) {
        setSettingsMessage('无法生成图片', true);
        return;
    }
    const isJpeg = uploadBlob.type === 'image/jpeg';
    closeAvatarCropModal();
    try {
        const uploaded = await postAvatarFile(uploadBlob, isJpeg ? 'avatar.jpg' : 'avatar.webp', accountContext);
        if (!isAccountContextCurrent(accountContext)) return;
        updateNavUserAvatar(uploaded && uploaded.avatar_url);
        if (isSettingsOverlayOpen()) {
            await loadUserSettingsPanel();
            if (!isAccountContextCurrent(accountContext)) return;
            setSettingsMessage('头像已更新');
        } else {
            showMainBanner('头像已更新');
        }
    } catch (err) {
        if (!isAccountContextCurrent(accountContext)) return;
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

let settingsRequestSequence = 0;
let settingsPendingRequestSequence = 0;

async function loadUserSettingsPanel() {
    const accountContext = captureAccountContext();
    const requestSequence = ++settingsRequestSequence;
    const isCurrent = () => isAccountContextCurrent(accountContext) && requestSequence === settingsRequestSequence;
    const studentBlocks = document.getElementById('settings-student-blocks');
    const parentBlock = document.getElementById('settings-parent-block');
    const title = document.getElementById('settings-title');
    if (studentBlocks) studentBlocks.style.display = 'none';
    if (parentBlock) parentBlock.style.display = 'none';
    setSettingsMessage('');
    try {
        await loadAccountEmail(accountContext);
    } catch (e) {
        if (!isCurrent()) return;
        setSettingsMessage(e.message || '邮箱信息加载失败', true);
    }
    if (!isCurrent()) return;
    if (isParentSession) {
        if (title) title.textContent = '配置';
        if (studentBlocks) studentBlocks.style.display = 'none';
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
        try {
            await Promise.all([
                loadParentLearningSettings(accountContext),
                loadSettingsPendingWordsBlock(accountContext),
            ]);
            if (!isCurrent()) return;
            if (parentBlock) parentBlock.style.display = '';
        } catch (e) {
            if (!isCurrent()) return;
            setSettingsMessage(e.message || '配置加载失败', true);
        }
        return;
    }
    if (title) title.textContent = '用户设置';
    if (parentBlock) parentBlock.style.display = 'none';
    try {
        const [s, poolState] = await Promise.all([
            apiRequest('/user/settings'),
            apiRequest('/monthly-pool'),
        ]);
        if (!isCurrent()) return;
        s.monthly_pool = poolState;
        renderUserSettingsPanel(s);
        lastGamificationProfile = s;
        updateGamificationNav(s);
        if (studentBlocks) studentBlocks.style.display = '';
    } catch (e) {
        if (!isCurrent()) return;
        setSettingsMessage(e.message || '加载失败', true);
    }
    resetSettingsPendingWordsCollapse();
    if (isCurrent()) await loadSettingsPendingWordsBlock(accountContext);
}

async function loadAccountEmail(accountContext = captureAccountContext()) {
    if (!isAccountContextCurrent(accountContext)) return null;
    const data = await apiRequest('/user/email');
    if (!isAccountContextCurrent(accountContext)) return null;
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
async function loadSettingsPendingWordsBlock(accountContext = captureAccountContext()) {
    if (!isAccountContextCurrent(accountContext)) return;
    const requestSequence = ++settingsPendingRequestSequence;
    const isCurrent = () => isAccountContextCurrent(accountContext) && requestSequence === settingsPendingRequestSequence;
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
        if (!isCurrent()) return;
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
        if (!isCurrent()) return;
        if (batchToolbar) batchToolbar.hidden = true;
        listEl.innerHTML = `<p class="settings-hint settings-message-error">${escapeHtml(
            e.message || '加载失败',
        )}</p>`;
    }
}

async function removePendingWordsInBatches(selected, accountContext = captureAccountContext(), onProgress = () => {}) {
    const batchSize = 200;
    let removed = 0;
    const removedEnglish = [];
    let plan = null;
    for (let index = 0; index < selected.length; index += batchSize) {
        if (!isAccountContextCurrent(accountContext)) return null;
        const chunk = selected.slice(index, index + batchSize);
        onProgress(Math.min(index + chunk.length, selected.length));
        const result = await apiRequest('/words/pending/remove', {
            method: 'POST',
            body: JSON.stringify({ english: chunk }),
        });
        if (!isAccountContextCurrent(accountContext)) return null;
        removed += Number(result?.removed) || 0;
        if (Array.isArray(result?.removed_english)) removedEnglish.push(...result.removed_english);
        if (result?.plan) plan = result.plan;
    }
    return { removed, removedEnglish, plan };
}

async function postAvatarFile(file, filename = 'avatar.webp', accountContext = captureAccountContext()) {
    if (!isAccountContextCurrent(accountContext)) throw new Error('登录账号已变更，已取消头像上传');
    const fd = new FormData();
    fd.append('file', file, filename);
    const headers = {};
    if (accountContext.token) headers.Authorization = `Bearer ${accountContext.token}`;
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
