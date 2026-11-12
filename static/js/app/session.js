// ==================== 认证功能 ====================

function activateAccountSession(data) {
    accountSessionGeneration++;
    resetAccountSessionView();
    token = data.access_token;
    username = data.username;
    setSessionParentFlags(!!data.is_parent, data.child_username || '');
    localStorage.setItem('token', token);
    localStorage.setItem('username', username);
}

function clearAccountSession() {
    accountSessionGeneration++;
    authenticationRequestSequence++;
    token = null;
    username = null;
    setSessionParentFlags(false, '');
    localStorage.removeItem('token');
    localStorage.removeItem('username');
    clearWordbankCsvDiscoveryCache();
}

async function login(loginUsername, password) {
    const authenticationSequence = ++authenticationRequestSequence;
    try {
        const formData = new FormData();
        formData.append('username', loginUsername);
        formData.append('password', password);

        const response = await fetch(`${API_BASE}/auth/login`, {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        if (authenticationSequence !== authenticationRequestSequence) return;
        
        if (!response.ok) {
            throw new Error(data.error || data.detail || '登录失败');
        }

        activateAccountSession(data);
        clearInviteRegistrationState();
        void showMainPage();
    } catch (error) {
        if (authenticationSequence !== authenticationRequestSequence) return;
        showError(error.message);
    }
}

async function register(registrationUsername, password, email, inviteCode) {
    const authenticationSequence = ++authenticationRequestSequence;
    try {
        const data = await apiRequest('/auth/register', {
            method: 'POST',
            body: JSON.stringify({
                username: registrationUsername,
                password,
                email,
                invite_code: inviteCode
            })
        });
        if (authenticationSequence !== authenticationRequestSequence) return;
        activateAccountSession(data);
        clearInviteRegistrationState();
        void showMainPage();
    } catch (error) {
        if (authenticationSequence !== authenticationRequestSequence) return;
        showError(error.message);
    }
}

async function logout() {
    if (logout.inProgress) return;
    const accountContext = captureAccountContext();
    const logoutToken = token;
    const logoutBtn = document.getElementById('logout-btn');
    const originalLabel = logoutBtn?.textContent || '退出';
    logout.inProgress = true;
    if (logoutBtn) {
        logoutBtn.disabled = true;
        logoutBtn.textContent = '保存中…';
    }

    try {
        let saved = true;
        if (activeReviewSubmissionPromise) {
            showMainBanner('正在保存当前作答，保存完成后将自动退出');
            saved = await activeReviewSubmissionPromise;
        } else if (pendingReviewSubmission) {
            showMainBanner('正在确认上一次作答结果，确认完成后将自动退出');
            saved = await submitAnswer();
        }
        if (!saved || pendingReviewSubmission) {
            showMainBanner('当前作答尚未保存，请重试提交后再退出');
            return;
        }
        if (!isAccountContextCurrent(accountContext)) return;

        clearAccountSession();
        preloadedReviewData = null;
        reviewDataStaleAfterImport = false;
        closeSettings();
        showLoginPage();

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
        /* 保存失败时保留会话；注销请求失败时本地会话已清除。 */
    } finally {
        logout.inProgress = false;
        if (logoutBtn) {
            logoutBtn.disabled = false;
            logoutBtn.textContent = originalLabel;
        }
    }
}
logout.inProgress = false;


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

function resetAccountSessionView() {
    if (typeof window.chatRoomOnLogout === 'function') window.chatRoomOnLogout();
    resetInviteCardState();
    preloadedReviewData = null;
    reviewDataStaleAfterImport = false;
    resetActiveReviewSession();
    resetAccountListViews();
    closeSettings();
    closeAvatarCropModal();
    closeAvatarViewModal();
    closeMakeupCheckinDialog();
    userPlan = 'free';
    articleAiExtractAvailable = false;
    articleAiExtractEnabled = false;
    serverPiperAvailable = false;
    resetArticleImportPickUI();
    importVocabBusy = false;
    const vocabInput = document.getElementById('import-vocab-textarea');
    if (vocabInput) vocabInput.value = '';
    const vocabButton = document.getElementById('import-vocab-btn');
    if (vocabButton) {
        vocabButton.disabled = false;
        vocabButton.textContent = '词汇导入';
    }
    closeDailySummaryPopover();
    closeXpHistoryModal();
    lastGamificationProfile = null;
    const usernameDisplay = document.getElementById('username-display');
    if (usernameDisplay) usernameDisplay.textContent = '';
    document.querySelector('#main-page .nav-user')?.removeAttribute('aria-label');
    resetGamificationNav();
    updateNavUserAvatar(null);
    updatePkNavBadgesFromDuels([]);
    closePkHubModal();
    stopPkInvitePolling();
}

function showLoginPage() {
    accountSessionGeneration++;
    resetAccountSessionView();
    document.getElementById('login-page').classList.add('active');
    document.getElementById('main-page').classList.remove('active');
    const gl = document.getElementById('admin-gear-login');
    if (gl) gl.style.display = '';
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
    const masteredDetailEl = document.getElementById('mastered-status-detail');
    if (reviewEl) reviewEl.textContent = String(due);
    if (masteredEl) masteredEl.textContent = String(stats.mastered_words || 0);
    if (masteredDetailEl) {
        const mastered = Number(stats.mastered_words) || 0;
        const reinforcement = Number(stats.mastered_reinforcement) || 0;
        const stable = Number.isFinite(Number(stats.mastered_stable))
            ? Number(stats.mastered_stable)
            : Math.max(0, mastered - reinforcement);
        masteredDetailEl.textContent = `稳定 ${stable} · 待巩固 ${reinforcement}`;
    }
    renderTodayProgressStat(stats.today_task || {});
    if (document.getElementById('progress-section')?.classList.contains('active')) {
        renderLearningMap(stats.mastered_words || 0);
    }
}

function renderTodayProgressStat(progress) {
    const valueEl = document.getElementById('today-progress-count');
    const detailEl = document.getElementById('today-progress-detail');
    const total = Math.max(0, Number(progress?.total) || 0);
    const completed = Math.max(0, Math.min(total, Number(progress?.completed) || 0));
    const remaining = Math.max(0, Number(progress?.remaining) || total - completed);
    const estimate = Math.max(
        0,
        Number(progress?.estimated_minutes) || (remaining ? Math.max(1, Math.round(remaining * 0.6)) : 0),
    );
    if (valueEl) valueEl.textContent = `${completed}/${total}`;
    if (detailEl) {
        detailEl.textContent = remaining
            ? `剩余 ${remaining} 词 · 约 ${estimate} 分钟`
            : total > 0
              ? '今日任务已完成'
              : '今日暂无任务';
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
        reviewDataStaleAfterImport = false;
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
    const accountContext = captureAccountContext();
    const reviewContext = captureReviewContext();
    document.getElementById('login-page').classList.remove('active');
    document.getElementById('main-page').classList.add('active');
    const gl = document.getElementById('admin-gear-login');
    if (gl) gl.style.display = 'none';
    preloadedReviewData = null;
    let boot = null;
    if (token) {
        try {
            boot = await apiRequest(reviewQueueEndpoint('/bootstrap'));
            if (!isReviewContextCurrent(reviewContext)) return;
            applyBootstrapPayload(boot);
        } catch (bootErr) {
            if (!isReviewContextCurrent(reviewContext)) return;
            try {
                const d = await apiRequest('/auth/session');
                if (!isReviewContextCurrent(reviewContext)) return;
                setSessionParentFlags(!!d.is_parent, d.child_username || '');
                maybeShowSystemBroadcast(d.system_broadcast);
            } catch (_) {
                /* 保留本地 session 标记 */
            }
        }
    }
    if (!isReviewContextCurrent(reviewContext)) return;
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
        refreshNavUserAvatar(accountContext);
    }
    startPkInvitePolling();
    if (!boot || !boot.plan) {
        await loadUserPlan(accountContext);
    } else if (!boot.tts_capabilities) {
        await refreshTtsCapabilities(accountContext);
    }
    if (!isReviewContextCurrent(reviewContext)) return;
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

async function loadUserPlan(accountContext = captureAccountContext()) {
    try {
        const data = await apiRequest('/user/plan');
        if (!isAccountContextCurrent(accountContext)) return;
        userPlan = data.plan || 'free';
        articleAiExtractAvailable = data.article_ai_extract_available === true;
        articleAiExtractEnabled = data.article_ai_extract_enabled === true;
        updatePlanUI();
    } catch (_) {
        if (!isAccountContextCurrent(accountContext)) return;
        userPlan = 'free';
        articleAiExtractEnabled = false;
    }
    await refreshTtsCapabilities(accountContext);
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
        if (reviewDataStaleAfterImport || !restoreActiveReviewSession()) {
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


