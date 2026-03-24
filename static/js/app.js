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

/** 最近一次 GET /api/gamification 结果，用于成就展示 */
let lastGamificationProfile = null;

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

function updateGamificationNav(g) {
    if (!g) return;
    const lv = document.getElementById('ng-level');
    const xp = document.getElementById('ng-xp');
    const st = document.getElementById('ng-streak');
    if (!lv || !xp || !st) return;
    lv.textContent = `Lv.${g.level}`;
    xp.textContent = `${formatNumber(g.total_xp)} XP`;
    st.textContent = `🔥 ${g.streak}`;
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
            return `<tr class="${me}">
                <td>${escapeHtml(r.rank)}</td>
                <td>${escapeHtml(r.username)}${r.is_viewer ? ' <span class="lb-you">我</span>' : ''}</td>
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
        const data = await apiRequest('/leaderboard');
        renderLeaderboardTable(data.leaderboard);
        renderAchievementsGrid(lastGamificationProfile);
    } catch (e) {
        const wrap = document.getElementById('leaderboard-table-wrap');
        if (wrap) {
            wrap.innerHTML = `<p class="leaderboard-empty">${escapeHtml(e.message || '加载失败')}</p>`;
        }
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

function showMessage(message, type = 'success') {
    const messageDiv = document.getElementById('import-message');
    if (!messageDiv) return;
    messageDiv.textContent = message;
    messageDiv.className = `message ${type}`;
    messageDiv.style.display = '';
    setTimeout(() => {
        messageDiv.className = 'message';
        messageDiv.style.display = 'none';
    }, 3000);
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

    const wordLength = target.length || word.english.length;
    container.dataset.wordLength = String(wordLength);
    container.dataset.currentInput = '';

    for (let i = 0; i < wordLength; i++) {
        const charSpan = document.createElement('span');
        charSpan.className = 'underline-char empty';
        charSpan.dataset.index = String(i);
        container.appendChild(charSpan);
    }

    capture.value = '';

    const syncFromCapture = () => {
        let v = capture.value.replace(/[^a-zA-Z]/g, '').toLowerCase();
        if (v.length > wordLength) {
            v = v.slice(0, wordLength);
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
    
    const wordLength = parseInt(container.dataset.wordLength);
    const currentInput = container.dataset.currentInput || '';
    
    const charSpans = container.querySelectorAll('.underline-char');
    charSpans.forEach((span, index) => {
        if (index < currentInput.length) {
            // 有字符
            span.textContent = currentInput[index];
            span.className = 'underline-char filled';
        } else {
            // 无字符
            span.textContent = '';
            span.className = 'underline-char empty';
        }
    });
}

// 获取当前输入值
function getCurrentInput() {
    const container = document.getElementById('underline-input');
    if (!container) return '';
    return container.dataset.currentInput || '';
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
function speakEnglishInBrowser(text) {
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
        u.onend = () => focusWordCapture(0);
        u.onerror = () => focusWordCapture(0);
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
    
    // 提取英文部分（支持 _ 和 → 两种分隔符）
    let enText = exampleText.split(' → ')[0].split('_')[0];
    if (!enText) {
        enText = exampleText;
    }
    enText = enText.trim();
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
}

function showMainPage() {
    document.getElementById('login-page').classList.remove('active');
    document.getElementById('main-page').classList.add('active');
    const gl = document.getElementById('admin-gear-login');
    if (gl) gl.style.display = 'none';
    document.getElementById('username-display').textContent = username;
    
    loadStats();
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
    const navItem = document.querySelector(`[data-page="${sectionId}"]`);
    if (navItem) {
        navItem.classList.add('active');
    }
    
    if (sectionId === 'review') {
        loadReviewList();
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
    } catch (error) {
        showMainBanner('加载统计失败，请稍后重试');
    }
    try {
        await refreshGamification();
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

/** 一轮题目做完：无错题则结束；有错题则自动进入下一轮错题复习，直到本轮零错题 */
function onPassComplete() {
    if (wrongWordsOrder.length === 0) {
        showFinalComplete();
        return;
    }
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
    // 'field' 保持原来的出现顺序

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

function showFinalComplete() {
    document.getElementById('review-box').style.display = 'none';
    document.getElementById('review-complete').style.display = 'block';
    const titleEl = document.getElementById('review-complete-title');
    const descEl = document.getElementById('review-complete-desc');
    const summaryEl = document.getElementById('review-session-summary');
    const isBonus = reviewSessionMode === 'bonus';
    if (titleEl) {
        titleEl.textContent = isBonus ? '加练完成！' : '今日复习完成！';
    }
    if (descEl) {
        descEl.textContent = isBonus
            ? '本次随机加练已完成（含错题巩固）。'
            : '恭喜！今日待复习已全部完成（含错题巩固）。';
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
    wrongWordsOrder = [];
    wrongWordsInThisPass = new Set();
    wrongRoundNumber = 0;
    wordMap = new Map();
    renderWrongPanel();
    updateWrongRoundLabel();
}

async function loadReviewList() {
    try {
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
            }
            if (gm.new_achievements && gm.new_achievements.length) {
                msgText += ` · 新成就：${gm.new_achievements.map((x) => x.title).join('、')}`;
            }
            updateGamificationNav({
                level: gm.level,
                total_xp: gm.total_xp,
                streak: gm.streak
            });
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

// ==================== 进度功能 ====================

async function loadProgress() {
    try {
        const data = await apiRequest('/words/status');
        
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
            return `
            <div class="word-item">
                <div class="word-item-info">
                    <div class="word-item-english">${escapeHtml(word.english)}${phoneticHtml}</div>
                    <div class="word-item-chinese">${escapeHtml(word.chinese)}</div>
                    <div class="word-item-next-review">下次复习：${nextLine}</div>
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
    const chunk = 500;
    let added = 0;
    let skipped = 0;
    let invalid = 0;
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
        }
        let msg = `成功加入 ${added} 个新单词`;
        if (skipped) msg += `，已跳过 ${skipped} 个重复`;
        if (invalid) msg += `，${invalid} 条无效已忽略`;
        showMessage(msg, 'success');
        // 清空已选
        wbState.selected.clear();
        wbState.selectedMap.clear();
        updateWordbankSelectedCount();
        renderWordbankList();
        loadStats();
    } catch (error) {
        showMessage(error.message, 'error');
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
            showMessage(data.message || '未在词库中找到匹配词汇', 'error');
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
    if (!ta) return;
    const raw = ta.value.trim();
    if (!raw) {
        showMessage('请先输入单词列表', 'error');
        return;
    }
    const level = levelSel ? levelSel.value : '';
    const btn = document.getElementById('import-vocab-btn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '处理中…';
    }
    try {
        const data = await apiRequest('/wordbank/csv/import-words', {
            method: 'POST',
            body: JSON.stringify({ words: raw, level, also_add_to_queue: true })
        });
        showMessage(data.message || '词汇导入成功', 'success');
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
        adminGearMain.addEventListener('click', () => openAdminOverlay());
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
    
    // 初始化页面
    if (token && username) {
        showMainPage();
    } else {
        showLoginPage();
    }
});
