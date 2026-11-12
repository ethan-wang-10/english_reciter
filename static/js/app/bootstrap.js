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

    const newWordsFirstCb = document.getElementById('review-new-words-first');
    if (newWordsFirstCb) {
        newWordsFirstCb.checked = getNewWordsFirstEnabled();
        newWordsFirstCb.addEventListener('change', () => {
            localStorage.setItem(
                REVIEW_NEW_WORDS_FIRST_STORAGE_KEY,
                newWordsFirstCb.checked ? '1' : '0',
            );
            if (reviewSessionMode === 'daily' && wrongRoundNumber === 0) {
                preloadedReviewData = null;
                void loadReviewList();
            } else {
                reorderRemainingReviewWords();
            }
        });
    }

    const inflectionCb = document.getElementById('review-test-inflection');
    if (inflectionCb) {
        inflectionCb.checked = localStorage.getItem(REVIEW_TEST_INFLECTION_STORAGE_KEY) === '1';
        inflectionCb.addEventListener('change', () => {
            if (isSubmitting || isAdvancing || pendingReviewSubmission) {
                inflectionCb.checked = localStorage.getItem(REVIEW_TEST_INFLECTION_STORAGE_KEY) === '1';
                return;
            }
            localStorage.setItem(REVIEW_TEST_INFLECTION_STORAGE_KEY, inflectionCb.checked ? '1' : '0');
            const word = currentReviewList[currentReviewIndex];
            if (word && document.getElementById('review-section')?.classList.contains('active')) {
                void showCurrentWord();
            }
        });
    }

    const directSubmitCb = document.getElementById('review-number-direct-submit');
    if (directSubmitCb) {
        directSubmitCb.checked = getReviewNumberDirectSubmitEnabled();
        directSubmitCb.addEventListener('change', () => {
            localStorage.setItem(
                REVIEW_NUMBER_DIRECT_SUBMIT_STORAGE_KEY,
                directSubmitCb.checked ? '1' : '0',
            );
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
            clearAccountSession();
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
    initDesktopMoreNavigation();
    document.querySelectorAll('.nav-item[data-page]').forEach(item => {
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
        const accountContext = captureAccountContext();
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
            const result = await removePendingWordsInBatches(selected, accountContext, (processed) => {
                if (btn && selected.length > pendingRmBatch) {
                    btn.textContent = `删除中… (${processed}/${selected.length})`;
                }
            });
            if (!result || !isAccountContextCurrent(accountContext)) return;
            const { removed: totalRemoved, removedEnglish: removedEnglishAcc, plan: latestPlan } = result;
            setSettingsMessage(
                totalRemoved > 0
                    ? `已从待复习移除 ${totalRemoved} 个词`
                    : '未能移除（可能已不在待复习中）',
                !totalRemoved,
            );
            document.getElementById('settings-message')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            await loadSettingsPendingWordsBlock(accountContext);
            if (!isAccountContextCurrent(accountContext)) return;
            await syncMainPageAfterPendingWordsRemoved(totalRemoved, removedEnglishAcc, latestPlan);
        } catch (err) {
            if (!isAccountContextCurrent(accountContext)) return;
            setSettingsMessage(err.message || '移除失败', true);
            document.getElementById('settings-message')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        } finally {
            if (isAccountContextCurrent(accountContext) && btn) {
                btn.disabled = false;
                if (prevLabel) btn.textContent = prevLabel;
            }
            if (isAccountContextCurrent(accountContext)) syncSettingsPendingBatchToolbar();
        }
    });
    document.getElementById('settings-pending-words-refresh')?.addEventListener('click', async () => {
        const accountContext = captureAccountContext();
        const b = document.getElementById('settings-pending-words-refresh');
        if (b) b.disabled = true;
        try {
            await loadSettingsPendingWordsBlock(accountContext);
            if (!isAccountContextCurrent(accountContext)) return;
            setSettingsMessage('已刷新', false);
        } catch (e) {
            if (!isAccountContextCurrent(accountContext)) return;
            setSettingsMessage(e.message || '刷新失败', true);
        } finally {
            if (isAccountContextCurrent(accountContext) && b) b.disabled = false;
        }
    });
    document.getElementById('settings-parent-pw-save')?.addEventListener('click', async () => {
        const accountContext = captureAccountContext();
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
            if (!isAccountContextCurrent(accountContext)) return;
            setSettingsMessage('密码已更新');
            const n1 = document.getElementById('settings-parent-pw-new');
            const n2 = document.getElementById('settings-parent-pw-confirm');
            if (n1) n1.value = '';
            if (n2) n2.value = '';
        } catch (e) {
            if (!isAccountContextCurrent(accountContext)) return;
            setSettingsMessage(e.message || '保存失败', true);
        }
    });
    document.getElementById('settings-learning-limits-save')?.addEventListener('click', async () => {
        const accountContext = captureAccountContext();
        const dailyInput = document.getElementById('settings-daily-review-limit');
        const dailyLimit = Number(dailyInput?.value);
        if (!Number.isInteger(dailyLimit) || dailyLimit < 1 || dailyLimit > 300) {
            setSettingsMessage('每日任务上限须为 1–300 的整数', true);
            return;
        }
        const button = document.getElementById('settings-learning-limits-save');
        if (button) button.disabled = true;
        try {
            const settings = await apiRequest('/user/learning-settings', {
                method: 'PATCH',
                body: JSON.stringify({
                    daily_review_limit: dailyLimit,
                }),
            });
            if (!isAccountContextCurrent(accountContext)) return;
            setSettingsMessage('学习设置已保存，将从下一次生成每日任务时生效');
        } catch (e) {
            if (!isAccountContextCurrent(accountContext)) return;
            setSettingsMessage(e.message || '学习设置保存失败', true);
        } finally {
            if (isAccountContextCurrent(accountContext) && button) button.disabled = false;
        }
    });
    document.getElementById('settings-email-save')?.addEventListener('click', async () => {
        const accountContext = captureAccountContext();
        const input = document.getElementById('settings-email');
        const button = document.getElementById('settings-email-save');
        const email = input?.value?.trim() || '';
        if (button) button.disabled = true;
        try {
            const data = await apiRequest('/user/email', {
                method: 'PATCH',
                body: JSON.stringify({ email }),
            });
            if (!isAccountContextCurrent(accountContext)) return;
            if (input) input.value = data.email || '';
            setSettingsEmailMessage(data.message || '邮箱已保存');
        } catch (e) {
            if (!isAccountContextCurrent(accountContext)) return;
            setSettingsEmailMessage(e.message || '保存失败', true);
        } finally {
            if (isAccountContextCurrent(accountContext) && button) button.disabled = false;
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
        const accountContext = captureAccountContext();
        try {
            await deleteAvatarApi();
            if (!isAccountContextCurrent(accountContext)) return;
            setSettingsMessage('已移除头像');
            await loadUserSettingsPanel();
        } catch (err) {
            if (!isAccountContextCurrent(accountContext)) return;
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
        const accountContext = captureAccountContext();
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
            if (!isAccountContextCurrent(accountContext)) return;
            let msg = '本月目标已保存';
            if (res.monthly_goal_bonus_just_granted_xp > 0) {
                msg += ` · 已发放打卡目标奖励 +${formatNumber(res.monthly_goal_bonus_just_granted_xp)} XP`;
            }
            setSettingsMessage(msg);
            await loadUserSettingsPanel();
        } catch (err) {
            if (!isAccountContextCurrent(accountContext)) return;
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
        const accountContext = captureAccountContext();
        try {
            await apiRequest('/monthly-pool/join', { method: 'POST', body: '{}' });
            if (!isAccountContextCurrent(accountContext)) return;
            setSettingsMessage('已加入本月奖池');
            await loadUserSettingsPanel();
        } catch (err) {
            if (!isAccountContextCurrent(accountContext)) return;
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
        submitBtn.addEventListener('click', () => {
            if (!finishPendingReviewAdvance()) void submitAnswer();
        });
    }
    document.addEventListener('keydown', handleSemanticQuestionKeydown);
    document.getElementById('review-section-continue')?.addEventListener('click', () => {
        continueReviewSection();
    });
    document.getElementById('review-section-wrong-now')?.addEventListener('click', () => {
        startImmediateWrongReview();
    });
    document.getElementById('review-section-progress')?.addEventListener('click', () => {
        showSection('progress');
    });

    const reviewBox = document.getElementById('review-box');
    if (reviewBox) {
        reviewBox.addEventListener('click', (e) => {
            if (e.target.closest('button')) return;
            focusWordCapture(0);
        });
        reviewBox.addEventListener('copy', blockGuardedReviewCopy);
        reviewBox.addEventListener('cut', blockGuardedReviewCopy);
        reviewBox.addEventListener('dragstart', blockGuardedReviewCopy);
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
    document.getElementById('import-ocr-apply')?.addEventListener('click', applyImportOcrReview);
    document.getElementById('import-ocr-add-line')?.addEventListener('click', () => {
        importOcrRows.push({ text: '', alternatives: [], needs_review: true, box: null });
        importOcrReviewDirty = true;
        renderImportOcrRows();
        document.querySelector('#import-ocr-review-lines .import-ocr-review-line:last-child input')?.focus();
    });
    document.getElementById('import-ocr-english-only')?.addEventListener('change', (event) => {
        const handwriting = document.getElementById('import-ocr-handwriting');
        if (handwriting) handwriting.disabled = !event.target.checked;
    });
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

    document.getElementById('wrong-words-review-now')?.addEventListener('click', () => {
        startImmediateWrongReview();
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
