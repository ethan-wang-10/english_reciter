let leaderboardScope = 'total';
let leaderboardTabsBound = false;
let leaderboardRequestSequence = 0;
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
    const top = Array.isArray(p.top) ? p.top : [];
    if (top.length === 0) {
        wrap.innerHTML = '';
        return;
    }
    const displayTop = top.slice(0, 3);
    const podiumUsernames = new Set(
        top.map((entry) => String(entry && entry.username ? entry.username : '')),
    );
    const audience = (Array.isArray(apiData.leaderboard) ? apiData.leaderboard : [])
        .filter((entry) => {
            const username = String(entry && entry.username ? entry.username : '');
            return username && Number(entry.rank) > 3 && !podiumUsernames.has(username);
        })
        .slice(0, 12);
    const overflowHint = top.length > displayTop.length
        ? ` · 并列获奖共 ${formatNumber(top.length)} 人`
        : '';
    const cards = displayTop
        .map((t, i) => {
            const rx = Number(t.reward_xp) || 0;
            const av = t.avatar_url
                ? `<img class="lb-podium-avatar" src="${escapeHtml(avatarDisplayUrl(t.avatar_url, 160))}" alt="" width="80" height="80" loading="lazy" />`
                : '<span class="lb-podium-avatar lb-avatar-placeholder" aria-hidden="true">👤</span>';
            const rk = Number(t.rank) || i + 1;
            const rewardText = rx > 0 ? `+${formatNumber(rx)} XP` : '无额外奖励';
            const crown = rk === 1
                ? `<svg class="lb-podium-crown" viewBox="698 225 107 68" aria-hidden="true" focusable="false">
                    <path d="M704 274l9-31 24 21 18-33 15 36 28-17-5 35z" fill="#f5c63f" stroke="#684a36" stroke-width="4" stroke-linejoin="round"></path>
                    <circle cx="713" cy="244" r="5" fill="#f0755d"></circle>
                    <circle cx="755" cy="232" r="5" fill="#69bca3"></circle>
                    <circle cx="798" cy="251" r="5" fill="#f0755d"></circle>
                </svg>`
                : '';
            return `<article class="leaderboard-podium-entry rank-${rk}">
                <div class="lb-podium-person">
                    ${crown}
                    <button type="button" class="lb-podium-avatar-button" data-lb-podium-index="${i}" aria-label="查看 ${escapeHtml(t.username)} 的头像和奖励信息" aria-haspopup="dialog" aria-expanded="false" aria-controls="avatar-view-modal">
                        ${av}
                    </button>
                    <div class="lb-podium-name">${escapeHtml(t.username)}</div>
                    <div class="lb-podium-meta">${escapeHtml(formatNumber(t.period_xp))} XP</div>
                </div>
                <div class="lb-podium-block">
                    <strong class="lb-podium-place">第 ${rk} 名</strong>
                    <span class="lb-podium-reward">${escapeHtml(rewardText)}</span>
                </div>
            </article>`;
        })
        .join('');
    const audienceFaces = ['😊', '😄', '🤩', '🥳', '😎', '🤗'];
    const audienceShirts = [
        '#e98555', '#65b38e', '#61b28b', '#ef765c', '#72b65e', '#e9a23f',
        '#e86c57', '#75b95d', '#5f9fd0', '#e96757', '#e89b43', '#62a8d2',
    ];
    const audienceSlots = [
        { x: 14.865, row: 'front' }, { x: 85.135, row: 'front' },
        { x: 20.270, row: 'back' }, { x: 79.730, row: 'back' },
        { x: 2.703, row: 'front' }, { x: 97.297, row: 'front' },
        { x: 8.108, row: 'back' }, { x: 91.892, row: 'back' },
        { x: 27.027, row: 'front' }, { x: 72.973, row: 'front' },
        { x: 32.432, row: 'back' }, { x: 67.568, row: 'back' },
    ];
    const audiencePeopleHtml = audience.map((entry, i) => {
        const slot = audienceSlots[i];
        const isBack = slot.row === 'back';
        const avatar = entry.avatar_url
            ? `<img class="lb-audience-avatar" src="${escapeHtml(avatarDisplayUrl(entry.avatar_url, 96))}" alt="" width="48" height="48" loading="lazy" />`
            : `<span class="lb-audience-avatar lb-audience-face">${audienceFaces[i % audienceFaces.length]}</span>`;
        return `<span class="lb-audience-person ${isBack ? 'is-back' : 'is-front'}" style="--lb-audience-x:${slot.x.toFixed(3)}%;--lb-audience-shirt:${audienceShirts[i % audienceShirts.length]}" title="${escapeHtml(entry.username)}">
            <span class="lb-audience-arm lb-audience-arm--left"></span>
            <span class="lb-audience-arm lb-audience-arm--right"></span>
            <span class="lb-audience-torso"><span class="lb-audience-collar"></span><span class="lb-audience-stripe"></span></span>
            <span class="lb-audience-head">${avatar}</span>
        </span>`;
    }).join('');
    const audienceHtml = audience.length
        ? `<div class="leaderboard-podium-audience" aria-hidden="true">${audiencePeopleHtml}</div>`
        : '';
    const celebrationHtml = `<div class="lb-podium-confetti" aria-hidden="true">
        <span class="lb-podium-confetti-piece is-left-outer"></span>
        <span class="lb-podium-confetti-piece is-left-inner"></span>
        <span class="lb-podium-confetti-piece is-right-inner"></span>
        <span class="lb-podium-confetti-piece is-right-outer"></span>
    </div>
    <div class="lb-podium-celebration" aria-hidden="true">
        <span class="lb-podium-ribbon lb-podium-ribbon--left"></span>
        <strong>恭喜获奖</strong>
        <span class="lb-podium-ribbon lb-podium-ribbon--right"></span>
    </div>`;
    wrap.innerHTML =
        `<div class="leaderboard-podium">` +
        `<div class="leaderboard-podium-head">` +
        `<div><h3 class="leaderboard-podium-title">上期${kind}三甲</h3>` +
        `<p class="leaderboard-podium-sub">${escapeHtml(p.period_label || p.period_id || '')}${escapeHtml(overflowHint)}</p></div>` +
        `<span class="leaderboard-podium-status">奖励已结算</span>` +
        `</div>` +
        `<div class="leaderboard-podium-scene">${celebrationHtml}${audienceHtml}` +
        `<div class="leaderboard-podium-stage podium-count-${displayTop.length}">${cards}</div>` +
        `</div>` +
        `</div>`;

    wrap.querySelectorAll('[data-lb-podium-index]').forEach((button) => {
        button.addEventListener('click', () => {
            const entry = displayTop[Number(button.getAttribute('data-lb-podium-index'))];
            if (entry) openLeaderboardAvatarModal(entry, p, kind, button);
        });
    });
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
    const accountContext = captureAccountContext();
    const scope = leaderboardScope;
    const requestSequence = ++leaderboardRequestSequence;
    const isCurrent = () => isAccountContextCurrent(accountContext) && requestSequence === leaderboardRequestSequence;
    const loading = document.getElementById('leaderboard-loading');
    if (loading) loading.style.display = 'block';
    try {
        const data = await apiRequest('/leaderboard?scope=' + encodeURIComponent(scope));
        if (!isCurrent()) return;
        document.querySelectorAll('[data-lb-scope]').forEach((b) => {
            const s = b.getAttribute('data-lb-scope');
            const on = s === scope;
            b.classList.toggle('active', on);
            b.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        const sc = data.scope || scope;
        renderLeaderboardTable(data.leaderboard, sc, data, { expanded: false });
        renderLeaderboardPodium(data);
    } catch (e) {
        if (!isCurrent()) return;
        const wrap = document.getElementById('leaderboard-table-wrap');
        if (wrap) {
            wrap.innerHTML = `<p class="leaderboard-empty">${escapeHtml(e.message || '加载失败')}</p>`;
        }
        const podium = document.getElementById('leaderboard-podium-wrap');
        if (podium) podium.innerHTML = '';
    } finally {
        if (isCurrent() && loading) loading.style.display = 'none';
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
    const accountContext = captureAccountContext();
    const scope = leaderboardScope;
    const requestSequence = ++leaderboardRequestSequence;
    const isCurrent = () => isAccountContextCurrent(accountContext) && requestSequence === leaderboardRequestSequence;
    bindLeaderboardTabs();
    const loading = document.getElementById('leaderboard-loading');
    if (loading) loading.style.display = 'block';
    try {
        await refreshGamification(accountContext);
        if (!isCurrent()) return;
        const introEl = document.getElementById('leaderboard-intro');
        if (introEl) {
            introEl.textContent = pickRandom(LEADERBOARD_INTRO_ROASTS);
        }
        const [data, pool, pkBoard] = await Promise.all([
            apiRequest('/leaderboard?scope=' + encodeURIComponent(scope)),
            apiRequest('/monthly-pool'),
            apiRequest('/challenges/monthly-pk-board'),
        ]);
        if (!isCurrent()) return;
        document.querySelectorAll('[data-lb-scope]').forEach((b) => {
            const s = b.getAttribute('data-lb-scope');
            const on = s === scope;
            b.classList.toggle('active', on);
            b.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        renderMonthlyPoolRace(pool);
        renderMonthlyPkBoard(pkBoard);
        const sc = data.scope || scope;
        renderLeaderboardTable(data.leaderboard, sc, data, { expanded: false });
        renderLeaderboardPodium(data);
        renderAchievementsGrid(lastGamificationProfile);
        _markSectionLoaded('leaderboard');
    } catch (e) {
        if (!isCurrent()) return;
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
        if (isCurrent() && loading) loading.style.display = 'none';
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

