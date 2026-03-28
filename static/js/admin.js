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
        const planLabel = plan === 'paid' ? '<span class="plan-badge-vip">VIP</span>' : '<span class="plan-badge-free">免费</span>';
        const pchk = u.parent_account_enabled ? 'checked' : '';
        const parentLogin = `${escapeHtml(u.username)}_parent`;
        return `
            <tr>
                <td>${escapeHtml(u.username)}</td>
                <td>${escapeHtml(u.pending_words)}</td>
                <td>${escapeHtml(u.mastered_words)}</td>
                <td>${planLabel}</td>
                <td>${en ? '正常' : '已禁用'}</td>
                <td>
                    <label class="admin-toggle" title="登录名为 用户名_parent，默认密码 123123">
                        <input type="checkbox" data-admin-parent="${escapeHtml(u.username)}" ${pchk} ${en ? '' : 'disabled'} />
                        开启
                    </label>
                    <span class="admin-parent-login-hint" style="font-size:12px;color:#666;display:block;margin-top:4px;">${parentLogin}</span>
                    <button type="button" class="btn btn-secondary btn-admin-parent-pw" style="margin-top:6px;font-size:12px;padding:4px 8px;"
                        data-admin-parent-password="${escapeHtml(u.username)}"
                        ${u.parent_account_enabled && en ? '' : 'disabled'}>家长密码</button>
                </td>
                <td>
                    <label class="admin-toggle">
                        <input type="checkbox" data-admin-user="${escapeHtml(u.username)}" ${chk} />
                        启用
                    </label>
                </td>
                <td>
                    <button type="button" class="btn-admin-pw" data-admin-set-password="${escapeHtml(u.username)}">设置密码</button>
                    <button type="button" class="btn-admin-plan" data-admin-set-plan="${escapeHtml(u.username)}" data-current-plan="${escapeHtml(plan)}">${plan === 'paid' ? '降为免费' : '升为 VIP'}</button>
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

    tbody.querySelectorAll('input[data-admin-parent]').forEach((inp) => {
        inp.addEventListener('change', async () => {
            const un = inp.getAttribute('data-admin-parent');
            const want = inp.checked;
            try {
                const res = await apiAdminRequest(`/admin/users/${encodeURIComponent(un)}/parent`, {
                    method: 'PATCH',
                    body: JSON.stringify({ enabled: want })
                });
                await loadAdminDashboard();
                if (want && res.default_password_hint) {
                    showAdminNotice(
                        `家长账户已创建：登录名 ${un}_parent，默认密码 ${res.default_password_hint}`
                    );
                } else {
                    showAdminNotice(want ? '家长账户已开启' : '家长账户已关闭');
                }
            } catch (e) {
                showAdminNotice(e.message || '操作失败');
                inp.checked = !want;
            }
        });
    });

    tbody.querySelectorAll('[data-admin-parent-password]').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const un = btn.getAttribute('data-admin-parent-password');
            const p1 = window.prompt(`为学生「${un}」的家长账户（${un}_parent）设置新密码（至少6位）`, '');
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
                await apiAdminRequest(`/admin/users/${encodeURIComponent(un)}/parent-password`, {
                    method: 'PATCH',
                    body: JSON.stringify({ password: p1 })
                });
                showAdminNotice('家长密码已更新，该家长需重新登录');
            } catch (e) {
                showAdminNotice(e.message || '设置失败');
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
                showAdminNotice(`用户 ${un} 已设置为${newPlan === 'paid' ? 'VIP' : '免费'}版`);
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

let adminTroublesHandlersBound = false;

function bindAdminTroublesOnce() {
    if (adminTroublesHandlersBound) return;
    adminTroublesHandlersBound = true;
    document.getElementById('admin-troubles-refresh')?.addEventListener('click', () => {
        void loadAdminTroubles();
    });
    document.getElementById('admin-lemma-map-save')?.addEventListener('click', () => {
        void adminSaveLemmaMapping();
    });
    document.getElementById('admin-troubles-difficult-tbody')?.addEventListener('click', async (e) => {
        const btn = e.target && e.target.closest && e.target.closest('[data-remove-difficult]');
        if (!btn) return;
        const surface = btn.getAttribute('data-surface');
        if (!surface) return;
        if (!window.confirm(`从疑难词列表中移除「${surface}」？（不添加映射时用户可能再次触发 AI）`)) return;
        showAdminNotice('');
        try {
            await apiAdminRequest('/admin/wordbank/troubles/difficult', {
                method: 'DELETE',
                body: JSON.stringify({ surface }),
            });
            await loadAdminTroubles();
        } catch (err) {
            showAdminNotice(err.message || '删除失败');
        }
    });
    document.getElementById('admin-troubles-mapping-tbody')?.addEventListener('click', async (e) => {
        const btn = e.target && e.target.closest && e.target.closest('[data-remove-mapping]');
        if (!btn) return;
        const surface = btn.getAttribute('data-surface');
        if (!surface) return;
        if (!window.confirm(`删除映射「${surface}」？`)) return;
        showAdminNotice('');
        try {
            await apiAdminRequest('/admin/wordbank/troubles/mapping', {
                method: 'DELETE',
                body: JSON.stringify({ surface }),
            });
            await loadAdminTroubles();
        } catch (err) {
            showAdminNotice(err.message || '删除失败');
        }
    });
}

function renderAdminTroublesDifficult(items) {
    const tbody = document.getElementById('admin-troubles-difficult-tbody');
    const emptyEl = document.getElementById('admin-troubles-difficult-empty');
    if (!tbody) return;
    if (!items || !items.length) {
        tbody.innerHTML = '';
        if (emptyEl) emptyEl.style.display = 'block';
        return;
    }
    if (emptyEl) emptyEl.style.display = 'none';
    tbody.innerHTML = items.map((it) => {
        const s = escapeHtml(it.surface || '');
        return `
            <tr>
                <td>${s}</td>
                <td>${escapeHtml(String(it.attempts != null ? it.attempts : '—'))}</td>
                <td>${escapeHtml(it.last_attempt || it.added_at || '—')}</td>
                <td>
                    <button type="button" class="btn btn-secondary" data-fill-map="${s}">填入映射表单</button>
                    <button type="button" class="btn btn-danger-outline" data-remove-difficult data-surface="${s}">移除</button>
                </td>
            </tr>`;
    }).join('');

    tbody.querySelectorAll('[data-fill-map]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const surf = btn.getAttribute('data-fill-map');
            const i1 = document.getElementById('admin-lemma-map-surface');
            const i2 = document.getElementById('admin-lemma-map-canonical');
            if (i1) i1.value = surf || '';
            if (i2) i2.value = '';
            i2 && i2.focus();
        });
    });
}

function renderAdminTroublesMapping(items) {
    const tbody = document.getElementById('admin-troubles-mapping-tbody');
    const emptyEl = document.getElementById('admin-troubles-mapping-empty');
    if (!tbody) return;
    if (!items || !items.length) {
        tbody.innerHTML = '';
        if (emptyEl) emptyEl.style.display = 'block';
        return;
    }
    if (emptyEl) emptyEl.style.display = 'none';
    tbody.innerHTML = items.map((it) => {
        const s = escapeHtml(it.surface || '');
        return `
            <tr>
                <td>${s}</td>
                <td>${escapeHtml(it.lemma || '')}</td>
                <td><button type="button" class="btn btn-danger-outline" data-remove-mapping data-surface="${s}">删除映射</button></td>
            </tr>`;
    }).join('');
}

async function adminSaveLemmaMapping() {
    const i1 = document.getElementById('admin-lemma-map-surface');
    const i2 = document.getElementById('admin-lemma-map-canonical');
    const surface = (i1 && i1.value || '').trim();
    const lemma = (i2 && i2.value || '').trim();
    if (!surface || !lemma) {
        showAdminNotice('请填写表面形与词汇原形');
        return;
    }
    showAdminNotice('');
    try {
        await apiAdminRequest('/admin/wordbank/troubles/mapping', {
            method: 'POST',
            body: JSON.stringify({ surface, lemma }),
        });
        if (i1) i1.value = '';
        if (i2) i2.value = '';
        await loadAdminTroubles();
        showAdminNotice('映射已保存');
    } catch (e) {
        showAdminNotice(e.message || '保存失败');
    }
}

async function loadAdminTroubles() {
    bindAdminTroublesOnce();
    try {
        const data = await apiAdminRequest('/admin/wordbank/troubles');
        renderAdminTroublesDifficult(data.difficult || []);
        renderAdminTroublesMapping(data.mappings || []);
    } catch (e) {
        showAdminNotice(e.message || '加载疑难词失败');
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
    await loadAdminTroubles();
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
        el.textContent = `当前已配置 API Key（${cfg.deepseek_api_key_preview}）。VIP 功能可正常使用。`;
        el.style.color = 'var(--primary-dark)';
    } else {
        el.textContent = '尚未配置 DeepSeek API Key。VIP 功能（文章 AI 提取、词汇导入）将不可用。';
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
