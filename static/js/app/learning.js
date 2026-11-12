// ==================== 统计功能 ====================

async function loadStats() {
    const accountContext = captureAccountContext();
    _invalidateSectionCache('progress');
    _invalidateSectionCache('mastered');
    _invalidateSectionCache('leaderboard');
    try {
        const data = await apiRequest('/words/summary');
        if (!isAccountContextCurrent(accountContext)) return;
        applySummaryStats(data);
    } catch (error) {
        if (!isAccountContextCurrent(accountContext)) return;
        showMainBanner('加载统计失败，请稍后重试');
    }
    try {
        await refreshGamification(accountContext);
        if (!isAccountContextCurrent(accountContext)) return;
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
async function syncMainPageAfterPendingWordsRemoved(removedCount, removedEnglishList, plan = null) {
    const accountContext = captureAccountContext();
    try {
        await loadStats();
    } catch (_) {
        /* loadStats 内部已提示 */
    }
    if (!isAccountContextCurrent(accountContext)) return;
    if (!(removedCount > 0)) return;
    if (plan) renderTodayTaskPlan(plan);
    try {
        if (document.getElementById('review-section')?.classList.contains('active')) {
            if (Array.isArray(removedEnglishList) && removedEnglishList.length > 0) {
                applyPendingRemovalToReviewSession(removedEnglishList);
            } else {
                await loadReviewList();
            }
        }
        if (!isAccountContextCurrent(accountContext)) return;
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

function canStartImmediateWrongReview() {
    return Boolean(
        reviewSessionMode === 'daily'
        && wrongRoundNumber === 0
        && !immediateRemedialReturnState
        && wrongWordsOrder.length > 0
        && !isRemedialOfferModalOpen()
    );
}

function updateImmediateWrongReviewButtons() {
    const enabled = canStartImmediateWrongReview();
    const title = enabled ? '先看词，再拼写；本轮不计答对率' : '当前没有可立即复习的错题';
    ['wrong-words-review-now', 'review-section-wrong-now'].forEach((id) => {
        const button = document.getElementById(id);
        if (!button) return;
        button.disabled = !enabled;
        button.title = title;
    });
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
    updateImmediateWrongReviewButtons();
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

    if (isReviewSectionBreakOpen()) {
        if (reviewBox) reviewBox.style.display = 'none';
        if (reviewComplete) reviewComplete.style.display = 'none';
        setTimeout(() => document.getElementById('review-section-continue')?.focus(), 0);
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
        el.textContent = '同一单词达到本题尝试上限仍未答对后会出现在这里';
    } else {
        el.textContent = `错题复习 · 第 ${wrongRoundNumber} 轮`;
    }
}

function resetSessionReviewStats() {
    sessionBonusPracticeXp = 0;
    sessionInitialMainWords = 0;
    sessionMainCorrect = 0;
    sessionMainFailedThree = 0;
    sessionRemedialCorrect = 0;
    sessionRestoredRemedialWords = 0;
    sessionTotalWrongAttempts = 0;
    sessionNewMastered = [];
    sessionExerciseStats = {};
    sessionImmediateRemedialRounds = 0;
}

function resetActiveReviewSession() {
    listeningPlaybackAttempt += 1;
    stopSpeakPlayback();
    endTtsSpeakUi();
    reviewSessionGeneration += 1;
    reviewDataRequestSequence += 1;
    currentReviewList = [];
    currentReviewIndex = 0;
    currentErrorCount = 0;
    currentRevealedCount = 0;
    cancelPendingReviewAdvance();
    isSubmitting = false;
    isAdvancing = false;
    wrongWordsInThisPass = new Set();
    wrongWordsOrder = [];
    wordMap = new Map();
    wrongRoundNumber = 0;
    immediateRemedialReturnState = null;
    reviewSessionMode = 'daily';
    sessionSkippedRemedialAfterMain = false;
    currentTodayTaskPlan = null;
    reviewQuestionStartedAt = 0;
    pendingReviewSubmission = null;
    const capture = document.getElementById('mobile-word-capture');
    if (capture) capture.readOnly = false;
    document.getElementById('submit-answer')?.classList.remove('btn-submit-loading');
    const inflectionInput = document.getElementById('review-test-inflection');
    if (inflectionInput) inflectionInput.disabled = false;
    resetSessionReviewStats();
    resetReviewSectionProgress();
    closeRemedialOfferModal();
    const panel = document.getElementById('today-task-panel');
    if (panel) panel.hidden = true;
}

function reviewExerciseLabel(exerciseType) {
    const labels = {
        recognition: '英文识义',
        context: '语境选词',
        spelling: '拼写',
        listening: '听写',
    };
    return labels[exerciseType] || '拼写';
}

function isSemanticExercise(exerciseType) {
    return exerciseType === 'recognition' || exerciseType === 'context';
}

function selectSemanticOption(word, button, { focus = false } = {}) {
    const options = document.getElementById('semantic-question-options');
    if (!word || !options || !button || button.disabled || isSubmitting || isAdvancing) return false;
    word._selectedOptionId = button.dataset.optionId || '';
    options.querySelectorAll('.semantic-option').forEach((row) => {
        const selected = row === button;
        row.classList.toggle('is-selected', selected);
        row.setAttribute('aria-checked', selected ? 'true' : 'false');
        row.tabIndex = selected ? 0 : -1;
    });
    const submit = document.getElementById('submit-answer');
    if (submit && submit.dataset.reviewAction !== 'next') {
        submit.disabled = !word._selectedOptionId;
    }
    if (focus) button.focus({ preventScroll: true });
    return Boolean(word._selectedOptionId);
}

function setReviewSubmitButtonState(action = 'submit', { disabled = false } = {}) {
    const submit = document.getElementById('submit-answer');
    if (!submit) return;
    const isNext = action === 'next';
    submit.dataset.reviewAction = isNext ? 'next' : 'submit';
    submit.textContent = isNext ? '下一题' : '提交';
    submit.disabled = isNext ? false : Boolean(disabled);
    submit.setAttribute('aria-keyshortcuts', 'Enter');
}

function sessionCorrectAttemptCount() {
    return sessionMainCorrect;
}

function isReviewSectionBreakOpen() {
    const panel = document.getElementById('review-section-break');
    return Boolean(panel && !panel.hidden);
}

function resetReviewSectionProgress() {
    reviewSectionStartIndex = currentReviewIndex;
    reviewSectionStartCorrectAttempts = sessionCorrectAttemptCount();
    reviewSectionStartWrongAttempts = sessionTotalWrongAttempts;
    reviewSectionStartMasteredCount = sessionNewMastered.length;
    reviewSectionCompletedCount = 0;
    const panel = document.getElementById('review-section-break');
    if (panel) panel.hidden = true;
}

function showReviewSectionBreak() {
    const completedWords = currentReviewIndex - reviewSectionStartIndex;
    if (
        completedWords < REVIEW_SECTION_SIZE
        || currentReviewIndex >= currentReviewList.length
    ) return false;

    reviewSectionCompletedCount += 1;
    const correctAttempts = sessionCorrectAttemptCount() - reviewSectionStartCorrectAttempts;
    const wrongAttempts = sessionTotalWrongAttempts - reviewSectionStartWrongAttempts;
    const totalAttempts = correctAttempts + wrongAttempts;
    const accuracy = totalAttempts > 0 ? Math.round((correctAttempts / totalAttempts) * 100) : 0;
    const mastered = new Set(sessionNewMastered.slice(reviewSectionStartMasteredCount)).size;
    const remaining = Math.max(0, currentReviewList.length - currentReviewIndex);
    const scope = wrongRoundNumber > 0
        ? '错题巩固'
        : (reviewSessionMode === 'bonus' ? '随机加练' : '今日复习');

    const title = document.getElementById('review-section-break-title');
    const desc = document.getElementById('review-section-break-desc');
    if (title) title.textContent = `第 ${reviewSectionCompletedCount} 小节完成`;
    if (desc) desc.textContent = `${scope}已暂停，休息一下再继续；本轮还剩 ${remaining} 词。`;
    const words = document.getElementById('review-section-break-words');
    const accuracyEl = document.getElementById('review-section-break-accuracy');
    const masteredEl = document.getElementById('review-section-break-mastered');
    if (words) words.textContent = String(completedWords);
    if (accuracyEl) accuracyEl.textContent = wrongRoundNumber > 0 ? '不计' : `${accuracy}%`;
    if (masteredEl) masteredEl.textContent = String(mastered);

    const reviewBox = document.getElementById('review-box');
    const reviewComplete = document.getElementById('review-complete');
    const panel = document.getElementById('review-section-break');
    if (reviewBox) reviewBox.style.display = 'none';
    if (reviewComplete) reviewComplete.style.display = 'none';
    if (panel) panel.hidden = false;
    setTimeout(() => document.getElementById('review-section-continue')?.focus(), 0);
    return true;
}

function continueReviewSection() {
    if (!isReviewSectionBreakOpen()) return false;
    const panel = document.getElementById('review-section-break');
    const reviewBox = document.getElementById('review-box');
    if (panel) panel.hidden = true;
    if (reviewBox) reviewBox.style.display = 'block';
    reviewSectionStartIndex = currentReviewIndex;
    reviewSectionStartCorrectAttempts = sessionCorrectAttemptCount();
    reviewSectionStartWrongAttempts = sessionTotalWrongAttempts;
    reviewSectionStartMasteredCount = sessionNewMastered.length;
    void showCurrentWord();
    return true;
}

function cancelPendingReviewAdvance() {
    if (reviewAdvanceTimer !== null) clearTimeout(reviewAdvanceTimer);
    reviewAdvanceTimer = null;
    pendingReviewAdvance = null;
}

function finishPendingReviewAdvance() {
    const pending = pendingReviewAdvance;
    if (!pending) return false;
    cancelPendingReviewAdvance();
    if (
        !isReviewContextCurrent(pending.reviewContext)
        || currentReviewIndex !== pending.submissionIndex
        || currentReviewList[pending.submissionIndex] !== pending.word
    ) return false;
    isSubmitting = false;
    isAdvancing = false;
    currentReviewIndex += 1;
    if (!showReviewSectionBreak()) void showCurrentWord();
    void loadStats();
    return true;
}

function scheduleReviewAdvance(reviewContext, submissionIndex, word, delayMs = null) {
    cancelPendingReviewAdvance();
    isAdvancing = true;
    pendingReviewAdvance = { reviewContext, submissionIndex, word };
    setReviewSubmitButtonState('next');
    // Final wrong answers stay visible until an explicit click or Enter press.
    if (delayMs === null) return;
    reviewAdvanceTimer = setTimeout(
        () => finishPendingReviewAdvance(),
        Math.max(0, Number(delayMs) || 0),
    );
}

function reviewFeedbackDelayMs(word, result, { isSemantic = false } = {}) {
    const feedback = result?.answer_feedback || {};
    const semanticDetailLength = [feedback.translation_zh, feedback.explanation_zh]
        .map((value) => String(value || '').trim().length)
        .reduce((total, length) => total + length, 0);
    const detailSteps = Math.min(3, Math.ceil(semanticDetailLength / 70));
    if (isSemantic) {
        const base = result?.correct ? 1400 : 2600;
        const maximum = result?.correct ? 3000 : 4200;
        return Math.min(maximum, base + detailSteps * 350);
    }

    const answerLength = String(word?._targetAnswer || word?.english || '').length;
    const longAnswerExtra = answerLength > 10 ? 300 : 0;
    if (!result?.correct) return 1800 + longAnswerExtra;
    const hasExtendedMeaning = Boolean(
        word?.review_polyseme
        || (Array.isArray(result?.other_senses_extra) && result.other_senses_extra.length > 0),
    );
    return 1000 + longAnswerExtra + (hasExtendedMeaning ? 1100 : 0);
}

function handleSemanticQuestionKeydown(event) {
    if (
        event.defaultPrevented
        || event.isComposing
        || event.ctrlKey
        || event.metaKey
        || event.altKey
    ) return;
    const reviewSection = document.getElementById('review-section');
    if (
        !reviewSection?.classList.contains('active')
        || isSettingsOverlayOpen()
        || xpHistoryModalOpen
        || isRemedialOfferModalOpen()
    ) return;
    const target = event.target instanceof Element ? event.target : null;
    const typingTarget = target?.closest('input, textarea, select, [contenteditable="true"]');
    const actionableTarget = target?.closest('button, a[href], [role="button"]');

    const newWordStudy = document.getElementById('new-word-study');
    const newWordStudyStart = document.getElementById('new-word-study-start');
    if (
        event.key === 'Enter'
        && newWordStudy?.hidden === false
        && newWordStudyStart
        && !newWordStudyStart.disabled
    ) {
        // The spelling input can retain focus while its row is hidden for the study card.
        if (typingTarget && !typingTarget.closest('[hidden]')) return;
        if (
            actionableTarget
            && actionableTarget !== newWordStudyStart
            && !actionableTarget.closest('[hidden]')
        ) return;
        event.preventDefault();
        newWordStudyStart.click();
        return;
    }

    if (typingTarget) return;

    if (event.key === 'Enter' && isReviewSectionBreakOpen()) {
        event.preventDefault();
        continueReviewSection();
        return;
    }

    const word = currentReviewList[currentReviewIndex];
    if (!word) return;
    if (event.key === 'Enter' && isAdvancing && pendingReviewAdvance) {
        event.preventDefault();
        if (event.repeat) return;
        finishPendingReviewAdvance();
        return;
    }
    if (
        event.code === 'Space'
        && word.exercise_type === 'listening'
        && !isSubmitting
        && !isAdvancing
    ) {
        event.preventDefault();
        void speakExample();
        return;
    }

    const panel = document.getElementById('semantic-question');
    if (!isSemanticExercise(word.exercise_type) || !panel || panel.hidden) return;
    const buttons = Array.from(panel.querySelectorAll('.semantic-option'));
    if (!buttons.length) return;

    if (/^[1-4]$/.test(event.key)) {
        if (event.repeat) return;
        const button = buttons[Number(event.key) - 1];
        if (!button || button.disabled) return;
        event.preventDefault();
        const selected = selectSemanticOption(word, button);
        if (selected && getReviewNumberDirectSubmitEnabled()) void submitAnswer();
        return;
    }

    if (['ArrowLeft', 'ArrowUp', 'ArrowRight', 'ArrowDown'].includes(event.key)) {
        if (isSubmitting || isAdvancing) return;
        const enabledButtons = buttons.filter((button) => !button.disabled);
        if (!enabledButtons.length) return;
        const selectedIndex = enabledButtons.findIndex(
            (button) => button.dataset.optionId === word._selectedOptionId,
        );
        const direction = event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1;
        const nextIndex = selectedIndex < 0
            ? (direction > 0 ? 0 : enabledButtons.length - 1)
            : (selectedIndex + direction + enabledButtons.length) % enabledButtons.length;
        const button = enabledButtons[nextIndex];
        if (!button) return;
        event.preventDefault();
        selectSemanticOption(word, button, { focus: true });
        return;
    }

    if (event.key === 'Enter') {
        event.preventDefault();
        if (!word._selectedOptionId || isSubmitting || isAdvancing) return;
        void submitAnswer();
    }
}

function getNewWordsFirstEnabled() {
    return localStorage.getItem(REVIEW_NEW_WORDS_FIRST_STORAGE_KEY) === '1';
}

function reviewQueueEndpoint(path) {
    const enabled = getNewWordsFirstEnabled() ? '1' : '0';
    return `${path}?new_words_first=${enabled}`;
}

function invalidateReviewDataAfterImport() {
    preloadedReviewData = null;
    reviewDataStaleAfterImport = true;
}

function reviewWordHasNoLearningAttempt(word) {
    const masteryAttempts = Object.values(word?.mastery?.by_type || {})
        .reduce(
            (total, dimension) => total + Math.max(0, Number(dimension?.attempts) || 0),
            0,
        );
    return (
        (Number(word?.review_count) || 0) <= 0
        && (Number(word?.success_count) || 0) <= 0
        && masteryAttempts <= 0
    );
}

function reviewReasonOrder(word, newWordsFirst = getNewWordsFirstEnabled()) {
    if (word?.task_remedial) return -1;
    const reason = String(word?.task_reason || 'due');
    if (newWordsFirst && reviewWordHasNoLearningAttempt(word)) return 0;
    const base = {
        overdue: 2,
        weak: 3,
        reinforcement: 4,
        due: 5,
        maintenance: 6,
        new: 7,
    };
    return base[reason] ?? 5;
}

function orderReviewWords(words, newWordsFirst = getNewWordsFirstEnabled()) {
    return (Array.isArray(words) ? words : [])
        .map((word, index) => ({ word, index }))
        .sort((a, b) => (
            reviewReasonOrder(a.word, newWordsFirst) - reviewReasonOrder(b.word, newWordsFirst)
            || a.index - b.index
        ))
        .map((row) => row.word);
}

function partitionRestoredReviewWords(words) {
    const ordered = orderReviewWords(words);
    return {
        mainWords: ordered.filter((word) => !word.task_remedial),
        remedialWords: ordered.filter((word) => word.task_remedial),
    };
}

function reorderRemainingReviewWords() {
    if (reviewSessionMode !== 'daily' || wrongRoundNumber > 0) return;
    const currentWord = currentReviewList[currentReviewIndex];
    const canReorderCurrent = Boolean(
        currentWord
        && currentErrorCount === 0
        && !currentWord._selectedOptionId
        && !getCurrentInput()
        && !pendingReviewSubmission
        && !isSubmitting
        && !isAdvancing
    );
    const keepThrough = Math.min(
        currentReviewList.length,
        currentReviewIndex + (canReorderCurrent ? 0 : 1),
    );
    const fixed = currentReviewList.slice(0, keepThrough);
    const remaining = orderReviewWords(currentReviewList.slice(keepThrough));
    currentReviewList = fixed.concat(remaining);
    if (canReorderCurrent && currentReviewList[currentReviewIndex] !== currentWord) {
        void showCurrentWord();
    }
}

function reviewAudioAvailable() {
    return Boolean(
        serverPiperAvailable ||
        (
            typeof window.speechSynthesis !== 'undefined' &&
            typeof window.SpeechSynthesisUtterance === 'function'
        )
    );
}

function adaptReviewDataToAudioCapability(data) {
    if (!data) return;
    const audioAvailable = reviewAudioAvailable();
    let downgraded = 0;
    (data.words || []).forEach((word) => {
        word._audioAvailableAtRender = audioAvailable;
        if (!audioAvailable && word.exercise_type === 'listening') {
            word.exercise_type = 'spelling';
            downgraded += 1;
        }
    });
    if (downgraded && data.plan?.exercise_mix) {
        const mix = { ...data.plan.exercise_mix };
        mix.listening = Math.max(0, (Number(mix.listening) || 0) - downgraded);
        mix.spelling = (Number(mix.spelling) || 0) + downgraded;
        data.plan = { ...data.plan, exercise_mix: mix };
    }
}

function renderReviewMasteryProgress(word) {
    const progressEl = document.getElementById('current-word-progress');
    if (!progressEl || !word) return;
    const masteryPercent = Number(word.mastery?.overall_percent);
    if (Number.isFinite(masteryPercent)) {
        const overallPercent = clampMasteryPercent(masteryPercent);
        const recognition = masteryDimensionText(word, 'recognition', '识义');
        const context = masteryDimensionText(word, 'context', '语境');
        const spelling = masteryDimensionText(word, 'spelling', '拼写');
        const listening = masteryDimensionText(word, 'listening', '听写');
        const reinforcement = word.memory_status === 'reinforcement';
        const statusHtml = reinforcement
            ? '<span class="word-progress-status">待巩固</span>'
            : '';
        progressEl.classList.remove('word-progress-legacy');
        progressEl.classList.add('word-progress-mastery');
        progressEl.innerHTML = `
            <span class="word-progress-overall">
                <span class="word-progress-overall-label">掌握进度${statusHtml}</span>
                <span class="word-progress-overall-track" aria-hidden="true">
                    <span class="word-progress-overall-fill" style="width: ${overallPercent}%"></span>
                </span>
                <strong class="word-progress-overall-value">${overallPercent}%</strong>
            </span>
            <span class="word-progress-dimensions">
                ${renderReviewMasteryDimension(word, 'recognition', '识义')}
                ${renderReviewMasteryDimension(word, 'context', '语境')}
                ${renderReviewMasteryDimension(word, 'spelling', '拼写')}
            </span>
        `;
        const statusPrefix = reinforcement ? '待巩固，' : '';
        progressEl.setAttribute('role', 'progressbar');
        progressEl.setAttribute('aria-valuemin', '0');
        progressEl.setAttribute('aria-valuemax', '100');
        progressEl.setAttribute('aria-valuenow', String(overallPercent));
        progressEl.setAttribute(
            'aria-label',
            `${statusPrefix}掌握进度 ${overallPercent}%，${recognition}，${context}，${spelling}`,
        );
        progressEl.title = `英文识义 ${masteryDimensionValue(word, 'recognition')} · 语境选词 ${masteryDimensionValue(word, 'context')} · 拼写 ${masteryDimensionValue(word, 'spelling')} · ${listening}`;
        return;
    }
    const maxSuccess = word.max_success_count != null ? word.max_success_count : 8;
    progressEl.classList.remove('word-progress-mastery');
    progressEl.classList.add('word-progress-legacy');
    progressEl.textContent = `${word.success_count}/${maxSuccess}`;
    progressEl.setAttribute('role', 'status');
    progressEl.setAttribute('aria-label', `掌握次数 ${word.success_count}/${maxSuccess}`);
    progressEl.removeAttribute('aria-valuemin');
    progressEl.removeAttribute('aria-valuemax');
    progressEl.removeAttribute('aria-valuenow');
    progressEl.removeAttribute('title');
}

function clampMasteryPercent(value) {
    const percent = Number(value);
    if (!Number.isFinite(percent)) return 0;
    return Math.max(0, Math.min(100, Math.round(percent)));
}

function masteryDimensionPercent(word, exerciseType) {
    const dimension = word?.mastery?.by_type?.[exerciseType];
    if (dimension?.available === false) return null;
    return clampMasteryPercent(dimension?.percent);
}

function renderReviewMasteryDimension(word, exerciseType, label) {
    const percent = masteryDimensionPercent(word, exerciseType);
    if (percent === null) {
        return `
            <span class="word-progress-dimension is-unavailable">
                <span>${label}</span>
                <span class="word-progress-unavailable">未提供</span>
            </span>
        `;
    }
    return `
        <span class="word-progress-dimension">
            <span>${label}</span>
            <span class="word-progress-mini-track" aria-hidden="true">
                <span class="word-progress-mini-fill" style="width: ${percent}%"></span>
            </span>
            <span class="word-progress-dimension-value">${percent}%</span>
        </span>
    `;
}

function masteryDimensionValue(word, exerciseType) {
    const percent = masteryDimensionPercent(word, exerciseType);
    return percent === null ? '未提供' : `${percent}%`;
}

function masteryDimensionText(word, exerciseType, label) {
    return `${label} ${masteryDimensionValue(word, exerciseType)}`;
}

function createReviewEventId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return window.crypto.randomUUID();
    }
    return `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function renderTodayTaskPlan(plan) {
    currentTodayTaskPlan = plan && typeof plan === 'object' ? { ...plan } : null;
    const panel = document.getElementById('today-task-panel');
    if (!panel) return;
    const total = Number(currentTodayTaskPlan?.total) || 0;
    if (reviewSessionMode === 'bonus' || total <= 0) {
        panel.hidden = true;
        return;
    }

    const completed = Math.max(0, Math.min(total, Number(currentTodayTaskPlan.completed) || 0));
    const remaining = Math.max(0, Number(currentTodayTaskPlan.remaining) || total - completed);
    const estimate = Math.max(0, Number(currentTodayTaskPlan.estimated_minutes) || 0);
    const backlog = Math.max(0, Number(currentTodayTaskPlan.backlog_after_task) || 0);
    const progress = document.getElementById('today-task-progress');
    const bar = document.getElementById('today-task-progress-bar');
    const progressText = document.getElementById('today-task-progress-text');
    const estimateEl = document.getElementById('today-task-estimate');
    const mixEl = document.getElementById('today-task-mix');
    const percent = total > 0 ? Math.round((completed / total) * 100) : 0;

    panel.hidden = false;
    if (progress) {
        progress.setAttribute('aria-valuemax', String(total));
        progress.setAttribute('aria-valuenow', String(completed));
    }
    if (bar) bar.style.width = `${percent}%`;
    if (progressText) progressText.textContent = `${completed}/${total}`;
    if (estimateEl) {
        estimateEl.textContent = `${remaining} 词 · 约 ${estimate} 分钟${backlog ? ` · 待后续安排 ${backlog} 词` : ''}`;
    }
    if (mixEl) {
        mixEl.innerHTML = '';
        const buckets = currentTodayTaskPlan.buckets || {};
        const exerciseMix = currentTodayTaskPlan.exercise_mix || {};
        const calibrations = currentTodayTaskPlan.calibrations || {};
        const bucketLabels = [
            ['overdue', '逾期'],
            ['due', '到期'],
            ['weak', '薄弱'],
            ['reinforcement', '待巩固'],
            ['maintenance', '保持'],
            ['new', '新词'],
        ];
        const rows = bucketLabels
            .map(([key, label]) => [label, Math.max(0, Number(buckets[key]) || 0)])
            .filter(([, count]) => count > 0)
            .map(([label, count]) => `${label} ${count}`);
        const legacyCalibration = Math.max(0, Number(calibrations.legacy) || 0);
        if (legacyCalibration > 0) rows.push(`历史能力校准 ${legacyCalibration}`);
        ['recognition', 'context', 'spelling', 'listening'].forEach((type) => {
            const count = Math.max(0, Number(exerciseMix[type]) || 0);
            if (count > 0) rows.push(`${reviewExerciseLabel(type)} ${count}`);
        });
        rows.forEach((text) => {
            const span = document.createElement('span');
            span.textContent = text;
            mixEl.appendChild(span);
        });
    }
}

function updateTodayTaskProgress(progress) {
    if (!currentTodayTaskPlan || !progress) return;
    const total = Number(progress.total);
    const completed = Number(progress.completed);
    const remaining = Number(progress.remaining);
    if (Number.isFinite(total)) currentTodayTaskPlan.total = total;
    if (Number.isFinite(completed)) currentTodayTaskPlan.completed = completed;
    if (Number.isFinite(remaining)) currentTodayTaskPlan.remaining = remaining;
    currentTodayTaskPlan.estimated_minutes = remaining > 0 ? Math.max(1, Math.round(remaining * 0.6)) : 0;
    renderTodayProgressStat(currentTodayTaskPlan);
    renderTodayTaskPlan(currentTodayTaskPlan);
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
    const totalRemedialRounds = remedialRoundsDone + sessionImmediateRemedialRounds;
    const correctTries = mainOk;
    const totalTries = correctTries + wrongTries;
    const accPct = totalTries > 0 ? Math.round((correctTries / totalTries) * 1000) / 10 : 0;

    const parts = [];
    if (isBonus) {
        parts.push(
            `<div class="review-summary-section"><strong>加练主轮</strong>：本轮共 ${n} 个词；` +
            `答对 ${mainOk} 个；${mainFailed} 个达到尝试上限仍未答对并进入错题巩固。（答对仅计复习次数）</div>`
        );
    } else if (n > 0) {
        parts.push(
            `<div class="review-summary-section"><strong>主轮</strong>：今日待复习共 ${n} 个词；` +
            `在本轮流程中答对 ${mainOk} 个；${mainFailed} 个达到尝试上限仍未答对并进入错题巩固。</div>`
        );
    }

    if (totalRemedialRounds > 0) {
        parts.push(
            `<div class="review-summary-section"><strong>错题巩固</strong>：共完成 ${totalRemedialRounds} 轮；` +
            `错题轮累计答对 ${remedialOk} 次（不计入答对率）。</div>`
        );
    } else if (sessionRestoredRemedialWords > 0 || remedialOk > 0) {
        parts.push(
            `<div class="review-summary-section"><strong>错题巩固</strong>：继续完成 ${sessionRestoredRemedialWords} 个待巩固词；` +
            `本次答对 ${remedialOk} 个。</div>`
        );
    } else if (mainFailed > 0 && sessionSkippedRemedialAfterMain) {
        parts.push(
            `<div class="review-summary-section"><strong>错题巩固</strong>：主轮有 ${mainFailed} 个词达到尝试上限仍未答对，你已选择「稍后再说」，本次未进行错题巩固。</div>`
        );
    } else if (n > 0 || isBonus) {
        parts.push(
            `<div class="review-summary-section"><strong>错题巩固</strong>：未触发，所有词在主轮已过关。</div>`
        );
    }

    const masteredUnique = [...new Set(sessionNewMastered)];
    if (masteredUnique.length > 0) {
        const names = masteredUnique.map((en) => escapeHtml(en)).join('、');
        parts.push(`<div class="review-summary-section"><strong>新掌握</strong>：${names}</div>`);
    }

    const abilityRows = Object.entries(sessionExerciseStats)
        .filter(([, value]) => value && value.attempts > 0)
        .map(([exerciseType, value]) => {
            const pct = Math.round((value.correct / value.attempts) * 100);
            return `${reviewExerciseLabel(exerciseType)} ${pct}%`;
        });
    if (abilityRows.length) {
        parts.push(
            `<div class="review-summary-section"><strong>能力表现</strong>：${abilityRows.map(escapeHtml).join(' · ')}</div>`
        );
    }

    parts.push(
        `<div class="review-summary-section"><strong>计分作答</strong>：共 ${totalTries} 次提交，` +
        `其中答错 ${wrongTries} 次，答对率约 ${accPct}%（错题巩固不计）。</div>`
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
    } else if (mainFailed > 0 || totalRemedialRounds > 1) {
        tip = '错题已巩固完成；不熟悉的词可在「学习进度」里查看下次复习时间。';
    } else if (wrongTries > 0) {
        tip = '偶尔答错很正常，间隔复习会帮助巩固。';
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

function prepareRemedialStudyWord(word) {
    const existingStudy = word?.study || {};
    const english = String(existingStudy.english || word?.english || '').trim();
    const chinese = String(existingStudy.chinese || word?.chinese || '').trim();
    const studyExamples = Array.isArray(existingStudy.examples) && existingStudy.examples.length
        ? existingStudy.examples
        : (Array.isArray(word?.examples) ? word.examples : []);
    return {
        ...word,
        english,
        chinese,
        exercise_type: 'spelling',
        question: null,
        question_required: false,
        task_remedial: true,
        study: {
            english,
            chinese,
            phonetic: String(existingStudy.phonetic || word?.phonetic || '').trim(),
            examples: studyExamples.map((example) => ({ ...example })),
        },
        _remedialStudy: true,
        _introSeen: false,
        _questionPromise: null,
        _selectedOptionId: '',
        _eliminatedOptionIds: [],
    };
}

async function restorePausedReviewWord(returnState) {
    const word = currentReviewList[currentReviewIndex];
    if (!word) {
        onPassComplete();
        return;
    }
    await showCurrentWord();
    if (
        immediateRemedialReturnState
        || currentReviewList[currentReviewIndex] !== word
        || isReviewSectionBreakOpen()
    ) return;

    currentErrorCount = returnState.currentErrorCount;
    currentRevealedCount = returnState.currentRevealedCount;
    const studyPanel = document.getElementById('new-word-study');
    if (!isSemanticExercise(word.exercise_type) && (!studyPanel || studyPanel.hidden)) {
        const targetAnswer = word._targetAnswer || word.english;
        document.getElementById('current-word-english').textContent = getHintStringForTarget(
            targetAnswer,
            currentRevealedCount,
        );
        restoreUnderlineInputFromState(
            returnState.inputValue || '',
            returnState.inputTypedCharCount,
            returnState.inputBlockedAssist === true,
        );
    }
}

function finishImmediateWrongReview() {
    const returnState = immediateRemedialReturnState;
    if (!returnState) return false;

    const failedWords = wrongWordsOrder
        .map((english) => wordMap.get(english))
        .filter(Boolean);
    immediateRemedialReturnState = null;
    sessionImmediateRemedialRounds += 1;
    currentReviewList = returnState.currentReviewList;
    currentReviewIndex = returnState.currentReviewIndex;
    currentErrorCount = returnState.currentErrorCount;
    currentRevealedCount = returnState.currentRevealedCount;
    wrongRoundNumber = returnState.wrongRoundNumber;
    wordMap = returnState.wordMap;
    failedWords.forEach((word) => wordMap.set(word.english, word));
    wrongWordsOrder = failedWords.map((word) => word.english);
    wrongWordsInThisPass = new Set(wrongWordsOrder);
    reviewSectionStartIndex = returnState.reviewSectionStartIndex;
    reviewSectionStartCorrectAttempts = returnState.reviewSectionStartCorrectAttempts;
    reviewSectionStartWrongAttempts = returnState.reviewSectionStartWrongAttempts;
    reviewSectionStartMasteredCount = returnState.reviewSectionStartMasteredCount;
    reviewSectionCompletedCount = returnState.reviewSectionCompletedCount;

    const sectionBreak = document.getElementById('review-section-break');
    const reviewBox = document.getElementById('review-box');
    const reviewComplete = document.getElementById('review-complete');
    if (reviewComplete) reviewComplete.style.display = 'none';
    renderWrongPanel();
    updateWrongRoundLabel();
    showMainBanner(
        failedWords.length > 0
            ? `本轮错题复习完成，${failedWords.length} 个词仍需巩固，已返回原进度`
            : '本轮错题复习完成，已返回原进度',
    );

    if (returnState.sectionBreakOpen) {
        if (reviewBox) reviewBox.style.display = 'none';
        if (sectionBreak) sectionBreak.hidden = false;
        setTimeout(() => document.getElementById('review-section-continue')?.focus(), 0);
    } else {
        if (sectionBreak) sectionBreak.hidden = true;
        if (reviewBox) reviewBox.style.display = 'block';
        void restorePausedReviewWord(returnState);
    }
    return true;
}

function startImmediateWrongReview() {
    if (!canStartImmediateWrongReview()) return false;
    if (isSubmitting || isAdvancing) {
        showMainBanner('当前题正在提交，请稍后再开始错题复习');
        return false;
    }

    const remedialWords = wrongWordsOrder
        .map((english) => wordMap.get(english))
        .filter(Boolean)
        .map(prepareRemedialStudyWord);
    if (remedialWords.length === 0) {
        wrongWordsOrder = [];
        wrongWordsInThisPass = new Set();
        renderWrongPanel();
        return false;
    }

    const capture = document.getElementById('mobile-word-capture');
    const underline = document.getElementById('underline-input');
    immediateRemedialReturnState = {
        currentReviewList,
        currentReviewIndex,
        currentErrorCount,
        currentRevealedCount,
        wrongRoundNumber,
        wordMap: new Map(wordMap),
        inputValue: String(capture?.value || ''),
        inputTypedCharCount: Number(underline?.dataset?.typedCharCount || 0),
        inputBlockedAssist: underline?.dataset?.blockedAssist === '1',
        sectionBreakOpen: isReviewSectionBreakOpen(),
        reviewSectionStartIndex,
        reviewSectionStartCorrectAttempts,
        reviewSectionStartWrongAttempts,
        reviewSectionStartMasteredCount,
        reviewSectionCompletedCount,
    };

    cancelPendingReviewAdvance();
    currentReviewList = remedialWords;
    currentReviewIndex = 0;
    currentErrorCount = 0;
    currentRevealedCount = 0;
    wrongRoundNumber = 1;
    wrongWordsOrder = [];
    wrongWordsInThisPass = new Set();
    remedialWords.forEach((word) => wordMap.set(word.english, word));
    resetReviewSectionProgress();
    document.getElementById('review-box').style.display = 'block';
    document.getElementById('review-complete').style.display = 'none';
    renderWrongPanel();
    updateWrongRoundLabel();
    showMainBanner(`开始复习 ${remedialWords.length} 个错题：先看词，再拼写（不计答对率）`);
    void showCurrentWord();
    return true;
}

/** 进入下一轮错题复习（主轮结束后需用户确认时由弹框调用，或错题轮结束后自动调用） */
function enterRemedialRound() {
    wrongRoundNumber += 1;
    const n = wrongWordsOrder.length;
    const msg = wrongRoundNumber === 1
        ? `本轮有 ${n} 个单词达到尝试上限仍未答对，即将开始错题复习`
        : `进入第 ${wrongRoundNumber} 轮错题复习（${n} 个单词）`;
    showMainBanner(msg);

    let orderedList = wrongWordsOrder
        .map((en) => wordMap.get(en))
        .filter(Boolean)
        .map(prepareRemedialStudyWord);
    if (getWrongOrder() === 'random') {
        shuffleArray(orderedList);
    }

    currentReviewList = orderedList;
    currentReviewList.forEach((word) => wordMap.set(word.english, word));
    wrongWordsOrder = [];
    wrongWordsInThisPass = new Set();

    if (currentReviewList.length === 0) {
        showFinalComplete();
        return;
    }

    currentReviewIndex = 0;
    resetReviewSectionProgress();
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
    if (immediateRemedialReturnState) {
        finishImmediateWrongReview();
        return;
    }
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
    immediateRemedialReturnState = null;
    const sectionBreak = document.getElementById('review-section-break');
    if (sectionBreak) sectionBreak.hidden = true;
    document.getElementById('review-box').style.display = 'none';
    document.getElementById('review-complete').style.display = 'block';
    const titleEl = document.getElementById('review-complete-title');
    const descEl = document.getElementById('review-complete-desc');
    const summaryEl = document.getElementById('review-session-summary');
    const isBonus = reviewSessionMode === 'bonus';
    const backlogAfterTask = isBonus
        ? 0
        : Math.max(0, Number(currentTodayTaskPlan?.backlog_after_task) || 0);
    if (titleEl) {
        if (!isBonus && sessionSkippedRemedialAfterMain) {
            titleEl.textContent = '今日主轮复习完成';
        } else if (backlogAfterTask > 0) {
            titleEl.textContent = '今日任务完成！';
        } else {
            titleEl.textContent = isBonus ? '加练完成！' : '今日复习完成！';
        }
    }
    if (descEl) {
        if (!isBonus && sessionSkippedRemedialAfterMain) {
            descEl.textContent = '你已选择暂不进行错题巩固，可随时点击「继续学习」再次进入复习。';
        } else if (backlogAfterTask > 0) {
            descEl.textContent = `今日任务已完成，另有 ${backlogAfterTask} 个到期词将按后续任务安排。`;
        } else {
            descEl.textContent = isBonus
                ? sessionBonusPracticeXp > 0
                    ? `本次随机加练已完成（含错题巩固），获得 ${formatNumber(sessionBonusPracticeXp)} XP。`
                    : '本次随机加练已完成（含错题巩固）。'
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
    immediateRemedialReturnState = null;
    const sectionBreak = document.getElementById('review-section-break');
    if (sectionBreak) sectionBreak.hidden = true;
    document.getElementById('review-box').style.display = 'none';
    document.getElementById('review-complete').style.display = 'block';
    const titleEl = document.getElementById('review-complete-title');
    const descEl = document.getElementById('review-complete-desc');
    const completedToday =
        Number(currentTodayTaskPlan?.total) > 0 && Number(currentTodayTaskPlan?.remaining) === 0;
    const backlog = Math.max(0, Number(currentTodayTaskPlan?.backlog_after_task) || 0);
    if (titleEl) titleEl.textContent = completedToday ? '今日任务已完成' : '今日暂无待复习';
    if (descEl) {
        descEl.textContent = completedToday
            ? backlog > 0
                ? `今天的计划已完成，另有 ${backlog} 个到期词将按后续任务安排。`
                : '今天的计划已完成，可以随机加练 10 个词保持手感。'
            : '目前没有到期的复习任务。你可以随机加练 10 个词保持手感，或去导入新词。';
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
    setReviewLoading(true);
    reviewSessionGeneration += 1;
    cancelPendingReviewAdvance();
    pendingReviewSubmission = null;
    const captureInput = document.getElementById('mobile-word-capture');
    if (captureInput) captureInput.readOnly = false;
    document.getElementById('submit-answer')?.classList.remove('btn-submit-loading');
    const reviewContext = captureReviewContext();
    const requestSequence = ++reviewDataRequestSequence;
    try {
        sessionSkippedRemedialAfterMain = false;
        immediateRemedialReturnState = null;
        wrongWordsInThisPass = new Set();
        wrongWordsOrder = [];
        wrongRoundNumber = 0;
        wordMap = new Map();
        isSubmitting = false;
        isAdvancing = false;
        resetSessionReviewStats();
        hideReviewSessionSummary();
        reviewSessionMode = 'daily';

        const data = preloadedReviewData || await apiRequest(reviewQueueEndpoint('/words/review'));
        if (
            !isReviewContextCurrent(reviewContext) ||
            requestSequence !== reviewDataRequestSequence
        ) return;
        preloadedReviewData = null;
        reviewDataStaleAfterImport = false;
        adaptReviewDataToAudioCapability(data);
        renderTodayTaskPlan(data.plan || null);
        const restored = partitionRestoredReviewWords(data.words);
        currentReviewList = restored.mainWords;
        currentReviewIndex = 0;
        resetReviewSectionProgress();
        [...restored.mainWords, ...restored.remedialWords].forEach((word) => {
            wordMap.set(word.english, word);
        });
        wrongWordsOrder = restored.remedialWords.map((word) => word.english);
        wrongWordsInThisPass = new Set(wrongWordsOrder);
        sessionRestoredRemedialWords = restored.remedialWords.length;
        sessionInitialMainWords = restored.mainWords.length;

        if (currentReviewList.length === 0) {
            if (wrongWordsOrder.length > 0) {
                showReviewEmptyActions(false);
                renderWrongPanel();
                updateWrongRoundLabel();
                showRemedialOfferModal();
            } else {
                showInitialEmptyReview();
            }
        } else {
            showReviewEmptyActions(false);
            document.getElementById('review-box').style.display = 'block';
            document.getElementById('review-complete').style.display = 'none';
            renderWrongPanel();
            updateWrongRoundLabel();
            showCurrentWord();
        }
    } catch (error) {
        if (
            !isReviewContextCurrent(reviewContext) ||
            requestSequence !== reviewDataRequestSequence
        ) return;
        showMainBanner('加载复习列表失败，请稍后重试');
        document.getElementById('review-box').style.display = 'none';
    } finally {
        if (isReviewContextCurrent(reviewContext) && requestSequence === reviewDataRequestSequence) {
            setReviewLoading(false);
        }
    }
}

async function startBonusReview() {
    reviewSessionGeneration += 1;
    cancelPendingReviewAdvance();
    pendingReviewSubmission = null;
    const captureInput = document.getElementById('mobile-word-capture');
    if (captureInput) captureInput.readOnly = false;
    document.getElementById('submit-answer')?.classList.remove('btn-submit-loading');
    const reviewContext = captureReviewContext();
    const requestSequence = ++reviewDataRequestSequence;
    try {
        const data = await apiRequest('/words/extra-review');
        if (
            !isReviewContextCurrent(reviewContext) ||
            requestSequence !== reviewDataRequestSequence
        ) return;
        if (!data.words || data.words.length === 0) {
            showMainBanner('词库为空，请先导入单词');
            return;
        }
        adaptReviewDataToAudioCapability(data);
        sessionSkippedRemedialAfterMain = false;
        immediateRemedialReturnState = null;
        wrongWordsInThisPass = new Set();
        wrongWordsOrder = [];
        wrongRoundNumber = 0;
        wordMap = new Map();
        isSubmitting = false;
        isAdvancing = false;
        resetSessionReviewStats();
        hideReviewSessionSummary();
        reviewSessionMode = 'bonus';
        renderTodayTaskPlan(null);

        currentReviewList = data.words;
        currentReviewIndex = 0;
        resetReviewSectionProgress();
        currentReviewList.forEach((w) => wordMap.set(w.english, w));
        sessionInitialMainWords = currentReviewList.length;

        showReviewEmptyActions(false);
        document.getElementById('review-box').style.display = 'block';
        document.getElementById('review-complete').style.display = 'none';
        renderWrongPanel();
        updateWrongRoundLabel();
        showCurrentWord();
    } catch (error) {
        if (
            !isReviewContextCurrent(reviewContext) ||
            requestSequence !== reviewDataRequestSequence
        ) return;
        showMainBanner('加载加练列表失败，请稍后重试');
    }
}

async function loadSemanticQuestionForWord(word) {
    if (word.question || !isSemanticExercise(word.exercise_type)) return;
    if (!word._questionPromise) {
        word._questionPromise = apiRequest('/words/question', {
            method: 'POST',
            body: JSON.stringify({
                word_id: word.english,
                task_id: word.task_id || '',
                task_item_id: word.task_item_id || '',
                exercise_type: word.exercise_type,
            }),
        }).finally(() => {
            word._questionPromise = null;
        });
    }
    const result = await word._questionPromise;
    if (result.fallback) {
        const previousType = word.exercise_type;
        word.exercise_type = result.exercise_type || 'spelling';
        word.question_required = false;
        if (result.word) {
            Object.assign(word, result.word);
            wordMap.set(word.english, word);
        }
        word._questionFallbackMessage = result.message || '';
        if (currentTodayTaskPlan?.exercise_mix) {
            const mix = { ...currentTodayTaskPlan.exercise_mix };
            mix[previousType] = Math.max(0, (Number(mix[previousType]) || 0) - 1);
            mix[word.exercise_type] = (Number(mix[word.exercise_type]) || 0) + 1;
            currentTodayTaskPlan.exercise_mix = mix;
            renderTodayTaskPlan(currentTodayTaskPlan);
        }
        return;
    }
    word.question = result.question;
    word.question_required = false;
}

function renderSemanticQuestion(word) {
    const question = word.question || {};
    const panel = document.getElementById('semantic-question');
    const prompt = document.getElementById('semantic-question-prompt');
    const options = document.getElementById('semantic-question-options');
    const feedback = document.getElementById('semantic-question-feedback');
    if (!panel || !prompt || !options || !feedback) return;

    panel.hidden = false;
    prompt.textContent = word.exercise_type === 'recognition'
        ? '选择最准确的中文释义'
        : String(question.prompt || '请选择最符合语境的词');
    options.innerHTML = '';
    feedback.textContent = '';
    feedback.hidden = true;
    word._selectedOptionId = '';
    word._eliminatedOptionIds = [];
    const capture = document.getElementById('mobile-word-capture');
    if (capture && document.activeElement === capture) capture.blur();
    (question.options || []).forEach((option, index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'semantic-option';
        button.dataset.optionId = String(option.id || '');
        button.dataset.optionIndex = String(index);
        button.setAttribute('role', 'radio');
        button.setAttribute('aria-checked', 'false');
        button.setAttribute('aria-keyshortcuts', String(index + 1));
        const optionText = String(option.text || '');
        button.dataset.optionLabel = optionText;
        button.setAttribute('aria-label', `选项 ${index + 1}：${optionText}`);
        button.tabIndex = index === 0 ? 0 : -1;

        const shortcut = document.createElement('span');
        shortcut.className = 'semantic-option-shortcut';
        shortcut.setAttribute('aria-hidden', 'true');
        shortcut.textContent = String(index + 1);
        const text = document.createElement('span');
        text.className = 'semantic-option-text';
        text.textContent = optionText;
        const status = document.createElement('span');
        status.className = 'semantic-option-status';
        status.setAttribute('aria-hidden', 'true');
        status.hidden = true;
        button.append(shortcut, text, status);
        button.addEventListener('click', () => {
            selectSemanticOption(word, button);
        });
        options.appendChild(button);
    });
}

function renderSemanticAnswerFeedback(word, result) {
    const panel = document.getElementById('semantic-question');
    const feedback = document.getElementById('semantic-question-feedback');
    const answerFeedback = result.answer_feedback || {};
    if (!panel || !feedback) return;
    panel.querySelectorAll('.semantic-option').forEach((button, index) => {
        const optionId = button.dataset.optionId || '';
        const isCorrect = optionId === answerFeedback.correct_option_id;
        const isWrong = optionId === word._selectedOptionId && !isCorrect;
        const wasEliminated = Array.isArray(word._eliminatedOptionIds)
            && word._eliminatedOptionIds.includes(optionId);
        button.disabled = true;
        button.classList.toggle('is-correct', isCorrect);
        button.classList.toggle('is-wrong', isWrong || wasEliminated);
        const status = button.querySelector('.semantic-option-status');
        if (status) {
            status.textContent = isCorrect ? '正确' : (isWrong ? '错误' : (wasEliminated ? '已排除' : ''));
            status.hidden = !status.textContent;
        }
        const optionText = button.dataset.optionLabel || '';
        const stateText = isCorrect
            ? '，正确答案'
            : (isWrong ? '，你的答案，错误' : (wasEliminated ? '，已排除' : ''));
        button.setAttribute('aria-label', `选项 ${index + 1}：${optionText}${stateText}`);
    });
    const lines = [];
    if (answerFeedback.translation_zh) lines.push(`译文：${answerFeedback.translation_zh}`);
    if (answerFeedback.explanation_zh) lines.push(answerFeedback.explanation_zh);
    feedback.textContent = lines.join('\n');
    feedback.hidden = lines.length === 0;
}

function resetSemanticOptionsForRetry(word) {
    const eliminatedIds = new Set(
        Array.isArray(word._eliminatedOptionIds) ? word._eliminatedOptionIds : [],
    );
    if (word._selectedOptionId) eliminatedIds.add(word._selectedOptionId);
    word._eliminatedOptionIds = [...eliminatedIds];
    word._selectedOptionId = '';
    let firstEnabled = null;
    document.querySelectorAll('#semantic-question-options .semantic-option').forEach((button, index) => {
        const eliminated = eliminatedIds.has(button.dataset.optionId || '');
        button.disabled = eliminated;
        button.classList.remove('is-selected', 'is-correct');
        button.classList.toggle('is-wrong', eliminated);
        button.setAttribute('aria-checked', 'false');
        button.tabIndex = -1;
        const status = button.querySelector('.semantic-option-status');
        if (status) {
            status.textContent = eliminated ? '已排除' : '';
            status.hidden = !eliminated;
        }
        const optionText = button.dataset.optionLabel || '';
        button.setAttribute(
            'aria-label',
            `选项 ${index + 1}：${optionText}${eliminated ? '，已排除' : ''}`,
        );
        if (!eliminated && !firstEnabled) firstEnabled = button;
    });
    if (firstEnabled) {
        firstEnabled.tabIndex = 0;
        firstEnabled.focus({ preventScroll: true });
    }
    const feedback = document.getElementById('semantic-question-feedback');
    if (feedback) {
        feedback.textContent = '';
        feedback.hidden = true;
    }
    setReviewSubmitButtonState('submit', { disabled: true });
}

function shouldShowNewWordStudy(word) {
    if (wrongRoundNumber > 0 && word?._remedialStudy && word._introSeen !== true) {
        return true;
    }
    return Boolean(
        reviewSessionMode === 'daily'
        && wrongRoundNumber === 0
        && word?.task_reason === 'new'
        && Number(word?.task_attempts || 0) === 0
        && word?.study
        && word._introSeen !== true
    );
}

function renderNewWordStudy(word) {
    const study = word.study || {};
    const isRemedialStudy = Boolean(word._remedialStudy && wrongRoundNumber > 0);
    const panel = document.getElementById('new-word-study');
    const semanticPanel = document.getElementById('semantic-question');
    const defExampleWrap = document.getElementById('review-def-example-wrap');
    const inputRow = document.querySelector('#review-content-inner .word-input');
    const modeEl = document.getElementById('review-exercise-type');
    const dueHint = document.getElementById('review-due-hint');
    if (!panel) return;

    panel.hidden = false;
    panel.classList.toggle('is-remedial', isRemedialStudy);
    if (semanticPanel) semanticPanel.hidden = true;
    if (defExampleWrap) defExampleWrap.hidden = true;
    if (inputRow) inputRow.hidden = true;
    if (modeEl) {
        modeEl.textContent = isRemedialStudy ? '错题预习' : '新词学习';
        modeEl.classList.remove('is-listening', 'is-semantic');
    }
    if (dueHint) {
        dueHint.hidden = false;
        dueHint.className = isRemedialStudy
            ? 'review-due-hint review-due-hint-carryover'
            : 'review-due-hint review-due-hint-scheduled';
        dueHint.textContent = isRemedialStudy ? '错题复习 · 本轮不计答对率' : '今日新词';
    }

    const kicker = panel.querySelector('.new-word-study-kicker');
    if (kicker) kicker.textContent = isRemedialStudy ? '先看词' : '首次学习';

    const english = String(study.english || word.english || '').trim();
    document.getElementById('current-word-english').textContent = english;
    const headerPhonetic = document.getElementById('current-word-phonetic');
    if (headerPhonetic) headerPhonetic.hidden = true;
    document.getElementById('new-word-study-english').textContent = english;
    const phonetic = document.getElementById('new-word-study-phonetic');
    if (phonetic) {
        phonetic.textContent = String(study.phonetic || word.phonetic || '').trim();
        phonetic.hidden = !phonetic.textContent;
    }
    document.getElementById('new-word-study-chinese').textContent = String(study.chinese || '').trim();

    const examplesEl = document.getElementById('new-word-study-examples');
    if (examplesEl) {
        examplesEl.innerHTML = '';
        (Array.isArray(study.examples) ? study.examples : []).slice(0, 2).forEach((example) => {
            const row = document.createElement('div');
            const en = String(example?.en || '').trim();
            const cn = String(example?.cn || '').trim();
            if (en) {
                const enEl = document.createElement('div');
                enEl.className = 'new-word-study-example-en';
                enEl.textContent = en;
                row.appendChild(enEl);
            }
            if (cn) {
                const cnEl = document.createElement('div');
                cnEl.className = 'new-word-study-example-cn';
                cnEl.textContent = cn;
                row.appendChild(cnEl);
            }
            if (row.childNodes.length) examplesEl.appendChild(row);
        });
        examplesEl.hidden = examplesEl.childNodes.length === 0;
    }

    const speak = document.getElementById('new-word-study-speak');
    if (speak) {
        speak.hidden = !reviewAudioAvailable();
        speak.onclick = () => speakEnglishPreferred(english, () => {}, speak, { clientAudioOnly: true });
    }
    const start = document.getElementById('new-word-study-start');
    if (start) {
        start.textContent = isRemedialStudy ? '开始拼写' : '开始作答';
        start.onclick = () => {
            word._introSeen = true;
            panel.hidden = true;
            void showCurrentWord();
        };
    }
    renderReviewMasteryProgress(word);
    document.getElementById('word-message').style.display = 'none';
    clearReviewWordMessageExtra();
    reviewQuestionStartedAt = 0;
}

async function showCurrentWord() {
    listeningPlaybackAttempt += 1;
    stopSpeakPlayback();
    endTtsSpeakUi();
    if (currentReviewIndex >= currentReviewList.length) {
        onPassComplete();
        return;
    }
    
    const word = currentReviewList[currentReviewIndex];
    pendingReviewSubmission = null;
    
    // 重置状态
    currentErrorCount = 0;
    currentRevealedCount = 0;
    isSubmitting = false;
    isAdvancing = false;
    setReviewSubmitButtonState('submit', {
        disabled: isSemanticExercise(word.exercise_type),
    });

    const newWordStudy = document.getElementById('new-word-study');
    if (shouldShowNewWordStudy(word)) {
        renderNewWordStudy(word);
        return;
    }
    if (newWordStudy) newWordStudy.hidden = true;

    if (isSemanticExercise(word.exercise_type) && !word.question) {
        const semanticPanel = document.getElementById('semantic-question');
        const semanticPrompt = document.getElementById('semantic-question-prompt');
        const semanticOptions = document.getElementById('semantic-question-options');
        const inputRow = document.querySelector('#review-content-inner .word-input');
        if (semanticPanel) semanticPanel.hidden = false;
        if (semanticPrompt) semanticPrompt.textContent = '正在准备题目…';
        if (semanticOptions) semanticOptions.innerHTML = '';
        if (inputRow) inputRow.hidden = true;
        document.getElementById('review-def-example-wrap').hidden = true;
        document.getElementById('current-word-english').textContent = '正在准备题目';
        try {
            await loadSemanticQuestionForWord(word);
        } catch (error) {
            if (currentReviewList[currentReviewIndex] !== word) return;
            if (semanticPrompt) semanticPrompt.textContent = error.message || '题目加载失败';
            if (semanticOptions) {
                const retry = document.createElement('button');
                retry.type = 'button';
                retry.className = 'btn btn-secondary semantic-question-retry';
                retry.textContent = '重试加载';
                retry.addEventListener('click', () => showCurrentWord());
                semanticOptions.appendChild(retry);
            }
            return;
        }
        if (currentReviewList[currentReviewIndex] !== word) return;
    }

    // 未勾选「考察语态」：只考词库原形；勾选：须拼例句中实际形式（与 CSV example*_form / pick 一致）
    const lemma = (word.english || '').trim();
    const inflected = (word.example_form || '').trim() || lemma;
    const targetAnswer =
        wrongRoundNumber === 0
        && word.exercise_type !== 'listening'
        && getReviewTestInflectionEnabled()
            ? inflected
            : lemma;
    word._targetAnswer = targetAnswer;

    const isListening = word.exercise_type === 'listening';
    const isSemantic = isSemanticExercise(word.exercise_type);
    if (isListening) word._listeningAudioPlayed = false;
    const modeEl = document.getElementById('review-exercise-type');
    if (modeEl) {
        modeEl.textContent = reviewExerciseLabel(word.exercise_type);
        modeEl.classList.toggle('is-listening', isListening);
        modeEl.classList.toggle('is-semantic', isSemantic);
    }
    const inflectionInput = document.getElementById('review-test-inflection');
    const inflectionToggle = inflectionInput?.closest('label');
    const phoneticInput = document.getElementById('review-show-phonetic');
    const phoneticToggle = phoneticInput?.closest('label');
    const directSubmitToggle = document.getElementById('review-number-direct-submit-toggle');
    const fixedRemedialSpelling = wrongRoundNumber > 0;
    if (inflectionInput) inflectionInput.disabled = isListening || isSemantic || fixedRemedialSpelling;
    if (inflectionToggle) {
        inflectionToggle.style.display = (isListening || isSemantic || fixedRemedialSpelling) ? 'none' : '';
    }
    if (phoneticToggle) phoneticToggle.style.display = word.exercise_type === 'context' ? 'none' : '';
    if (directSubmitToggle) directSubmitToggle.hidden = !isSemantic;
    
    const dueHint = document.getElementById('review-due-hint');
    if (dueHint) {
        if (reviewSessionMode === 'bonus') {
            dueHint.hidden = false;
            dueHint.className = 'review-due-hint review-due-hint-bonus';
            dueHint.textContent = '随机加练（不改变掌握进度与排期）';
        } else if (wrongRoundNumber > 0 || word.task_remedial) {
            dueHint.hidden = false;
            dueHint.className = 'review-due-hint review-due-hint-carryover';
            dueHint.textContent = '错题巩固（不计答对率）';
        } else if (word.task_calibration === 'legacy') {
            dueHint.hidden = false;
            dueHint.className = 'review-due-hint review-due-hint-scheduled';
            dueHint.textContent = '历史学习进度能力校准';
        } else if (word.task_reason === 'new') {
            dueHint.hidden = false;
            dueHint.className = 'review-due-hint review-due-hint-scheduled';
            dueHint.textContent = '今日新词';
        } else if (word.task_reason === 'maintenance') {
            dueHint.hidden = false;
            dueHint.className = 'review-due-hint review-due-hint-scheduled';
            dueHint.textContent = '长期记忆保持复习';
        } else if (word.task_reason === 'reinforcement') {
            dueHint.hidden = false;
            dueHint.className = 'review-due-hint review-due-hint-carryover';
            dueHint.textContent = '记忆稳定性待巩固';
        } else if (word.task_reason === 'weak') {
            dueHint.hidden = false;
            dueHint.className = 'review-due-hint review-due-hint-scheduled';
            dueHint.textContent = '薄弱能力巩固';
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
        rcInner.classList.toggle('review-content--polyseme', !!word.review_polyseme && !isListening && !isSemantic);
        rcInner.classList.toggle('review-mode-listening', isListening);
        rcInner.classList.toggle('review-mode-semantic', isSemantic);
    }
    const chineseEl = document.getElementById('current-word-chinese');
    chineseEl.textContent = word.chinese;
    chineseEl.hidden = isSemantic;
    renderReviewMasteryProgress(word);
    
    const exEl = document.getElementById('current-word-example');
    const multiHtml = buildReviewExamplesDisplayHtml(word);
    if (multiHtml) {
        exEl.innerHTML = multiHtml;
    } else {
        exEl.textContent = formatReviewQuizExampleDisplay(word);
    }
    exEl.hidden = isListening || isSemantic;
    
    // 提示字符串基于本题目标词长度（原形或句中形式）
    const hintString = getHintStringForTarget(targetAnswer, currentRevealedCount);
    document.getElementById('current-word-english').textContent = isSemantic
        ? (word.exercise_type === 'recognition' ? (word.question?.prompt || word.english) : '语境选词')
        : hintString;
    updateReviewPhoneticDisplay(word);
    if (word.exercise_type === 'context') {
        const phonetic = document.getElementById('current-word-phonetic');
        if (phonetic) phonetic.hidden = true;
    }
    
    // 绑定朗读按钮事件
    const speakBtn = document.getElementById('speak-example-btn');
    if (speakBtn) {
        speakBtn.onclick = speakExample;
        speakBtn.hidden = isSemantic;
        speakBtn.title = isListening ? '朗读单词' : '朗读例句';
        speakBtn.setAttribute('aria-label', isListening ? '朗读单词' : '朗读例句');
    }
    
    // 初始化下划线输入框（基于 targetAnswer 的长度）
    const semanticPanel = document.getElementById('semantic-question');
    const defExampleWrap = document.getElementById('review-def-example-wrap');
    const inputRow = document.querySelector('#review-content-inner .word-input');
    const underlineWrapper = document.querySelector('#review-content-inner .underline-input-wrapper');
    if (semanticPanel) semanticPanel.hidden = !isSemantic;
    if (defExampleWrap) defExampleWrap.hidden = isSemantic;
    if (inputRow) inputRow.hidden = false;
    if (underlineWrapper) underlineWrapper.hidden = isSemantic;
    setReviewSubmitButtonState('submit', { disabled: isSemantic });
    if (isSemantic) {
        renderSemanticQuestion(word);
    } else {
        initializeUnderlineInputForTarget(word, targetAnswer);
        focusWordCapture(0);
    }

    // 清空消息
    document.getElementById('word-message').style.display = 'none';
    clearReviewWordMessageExtra();
    if (word._questionFallbackMessage) {
        const message = document.getElementById('word-message');
        message.textContent = word._questionFallbackMessage;
        message.className = 'word-message';
        message.style.display = 'block';
        word._questionFallbackMessage = '';
    }
    reviewQuestionStartedAt = performance.now();
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
    if (activeReviewSubmissionPromise) return activeReviewSubmissionPromise;
    const submissionPromise = submitAnswerRequest();
    activeReviewSubmissionPromise = submissionPromise;
    try {
        return await submissionPromise;
    } finally {
        if (activeReviewSubmissionPromise === submissionPromise) {
            activeReviewSubmissionPromise = null;
        }
    }
}

async function submitAnswerRequest() {
    if (isSubmitting || isAdvancing) return false;
    const word = currentReviewList[currentReviewIndex];
    if (word?.exercise_type === 'listening' && word._listeningAudioPlayed !== true) {
        const messageDiv = document.getElementById('word-message');
        if (messageDiv) {
            messageDiv.textContent = '请先播放本题音频';
            messageDiv.className = 'word-message error';
            messageDiv.style.display = 'block';
        }
        void speakExample();
        return false;
    }
    const existingSubmission =
        pendingReviewSubmission &&
        pendingReviewSubmission.wordKey === (word?.task_item_id || word?.english)
            ? pendingReviewSubmission
            : null;
    const isSemantic = isSemanticExercise(word?.exercise_type);
    const answer = existingSubmission
        ? existingSubmission.answer
        : (isSemantic ? String(word?._selectedOptionId || '') : getCurrentInput());
    
    if (!answer) {
        focusWordCapture(0);
        return false;
    }
    if (!isSemantic && !isUnderlineInputManualEnough()) {
        rejectAssistedUnderlineInput();
        return false;
    }

    isSubmitting = true;
    const submitBtn = document.getElementById('submit-answer');
    if (submitBtn) submitBtn.classList.add('btn-submit-loading');
    const inflectionInput = document.getElementById('review-test-inflection');
    if (inflectionInput) inflectionInput.disabled = true;
    const captureInput = document.getElementById('mobile-word-capture');
    if (captureInput) captureInput.readOnly = true;
    const reviewContext = captureReviewContext();
    const submissionIndex = currentReviewIndex;
    
    try {
        const activeExerciseType = word.exercise_type || 'spelling';
        if (!existingSubmission) {
            const reviewEventId = createReviewEventId();
            const elapsedMs = reviewQuestionStartedAt > 0
                ? Math.max(0, Math.round(performance.now() - reviewQuestionStartedAt))
                : 0;
            pendingReviewSubmission = {
                wordKey: word.task_item_id || word.english,
                answer,
                sendCount: 0,
                body: {
                    word_id: word.english,
                    answer,
                    question_id: isSemantic ? String(word.question?.question_id || '') : '',
                    selected_option_id: isSemantic ? answer : '',
                    remedial:
                        reviewSessionMode !== 'bonus' &&
                        (wrongRoundNumber > 0 || word.task_remedial === true),
                    bonus_practice: reviewSessionMode === 'bonus',
                    bonus_session_id:
                        reviewSessionMode === 'bonus' ? String(word.bonus_session_id || '') : '',
                    test_inflection: wrongRoundNumber === 0 && getReviewTestInflectionEnabled(),
                    task_id: word.task_id || '',
                    task_item_id: word.task_item_id || '',
                    exercise_type: activeExerciseType,
                    attempt_number: currentErrorCount + 1,
                    hint_count: currentRevealedCount,
                    elapsed_ms: elapsedMs,
                    review_event_id: reviewEventId,
                    audio_available: Boolean(word._audioAvailableAtRender),
                },
            };
        }
        pendingReviewSubmission.sendCount += 1;
        const isSubmissionRetry = pendingReviewSubmission.sendCount > 1;
        const submission = pendingReviewSubmission;
        const result = await apiRequest('/words/practice', {
            method: 'POST',
            body: JSON.stringify(submission.body),
        });
        if (
            !isReviewContextCurrent(reviewContext) ||
            currentReviewIndex !== submissionIndex ||
            currentReviewList[submissionIndex] !== word
        ) return false;
        pendingReviewSubmission = null;
        if (captureInput) captureInput.readOnly = false;

        if (result.word) {
            Object.assign(word, result.word);
            if (Number.isFinite(Number(result.attempt_number))) {
                word.task_attempts = Number(result.attempt_number);
            }
            word.exercise_type = result.exercise_type || activeExerciseType;
            word.task_remedial = result.task_remedial ?? word.task_remedial;
            wordMap.set(word.english, word);
            renderReviewMasteryProgress(word);
        }

        if (result.task_progress) updateTodayTaskProgress(result.task_progress);

        const countedResponse = result.recorded !== false || isSubmissionRetry || result.replayed === true;
        const isWrongReviewAttempt = wrongRoundNumber > 0 || result.remedial === true;
        if (countedResponse && !isWrongReviewAttempt) {
            const exerciseType = result.exercise_type || word.exercise_type || 'spelling';
            const typeStats = sessionExerciseStats[exerciseType] || { attempts: 0, correct: 0 };
            typeStats.attempts += 1;
            if (result.correct) typeStats.correct += 1;
            sessionExerciseStats[exerciseType] = typeStats;
        }

        if (countedResponse) {
            if (!result.correct) {
                if (!isWrongReviewAttempt) sessionTotalWrongAttempts += 1;
            } else {
                if (!isWrongReviewAttempt) {
                    sessionMainCorrect += 1;
                } else {
                    sessionRemedialCorrect += 1;
                }
                if (result.mastered_now) {
                    sessionNewMastered.push(word.english);
                }
            }
        }

        const messageDiv = document.getElementById('word-message');
        let msgText = result.message;
        clearReviewWordMessageExtra();
        if (isSemantic && (result.correct || result.final_attempt === true)) {
            renderSemanticAnswerFeedback(word, result);
        }
        if (result.correct && result.gamification) {
            const gm = result.gamification;
            if (reviewSessionMode === 'bonus') {
                sessionBonusPracticeXp = Math.max(sessionBonusPracticeXp, Number(gm.answer_xp_gained) || 0);
            }
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
            if (!isSemantic) {
                document.getElementById('current-word-english').textContent = targetAnswer;
            }
            scheduleReviewAdvance(
                reviewContext,
                submissionIndex,
                word,
                reviewFeedbackDelayMs(word, result, { isSemantic }),
            );
        } else {
            // 答案错误
            currentErrorCount++;
            const rawAttemptLimit = Number(result.attempt_limit);
            const attemptLimit = Number.isFinite(rawAttemptLimit) && rawAttemptLimit > 0
                ? Math.floor(rawAttemptLimit)
                : (isSemantic ? 2 : 3);
            const finalAttempt = typeof result.final_attempt === 'boolean'
                ? result.final_attempt
                : currentErrorCount >= attemptLimit;
            
            // 每次错误多揭示一个字母
            if (!isSemantic && currentRevealedCount < targetAnswer.length) {
                currentRevealedCount++;
            }
            
            if (finalAttempt) {
                // 达到当前题型尝试上限：记入本轮错题栏
                if (wrongRoundNumber === 0 && !result.remedial) {
                    sessionMainFailedThree += 1;
                }
                recordWrongAttempt(word);
                if (!isSemantic) {
                    document.getElementById('current-word-english').textContent = targetAnswer;
                }
                scheduleReviewAdvance(
                    reviewContext,
                    submissionIndex,
                    word,
                );
            } else {
                // 还有尝试机会，更新提示字符串
                if (!isSemantic) {
                    const targetAnswer = word._targetAnswer || word.english;
                    const hintString = getHintStringForTarget(targetAnswer, currentRevealedCount);
                    document.getElementById('current-word-english').textContent = hintString;
                }
                
                // 选择题首次答错只提示错误，不泄露答案；其他题型继续显示剩余次数。
                const rawAttemptNumber = Number(result.attempt_number);
                const attemptInCycle = Number.isFinite(rawAttemptNumber) && rawAttemptNumber > 0
                    ? ((Math.floor(rawAttemptNumber) - 1) % attemptLimit) + 1
                    : currentErrorCount;
                const remainingAttempts = Math.max(0, attemptLimit - attemptInCycle);
                messageDiv.textContent = isSemantic
                    ? result.message
                    : `${result.message} (还剩 ${remainingAttempts} 次尝试机会)`;
                // 清空下划线输入框，让用户重新输入
                if (isSemantic) {
                    resetSemanticOptionsForRetry(word);
                } else {
                    clearUnderlineInput();
                }
                const inflectionInput = document.getElementById('review-test-inflection');
                if (inflectionInput) inflectionInput.disabled = true;
                reviewQuestionStartedAt = performance.now();
                isSubmitting = false;
                if (!isSemantic) {
                    focusWordCapture(0);
                    focusWordCapture(100);
                }
            }
        }
        return true;
    } catch (error) {
        if (
            !isReviewContextCurrent(reviewContext) ||
            currentReviewIndex !== submissionIndex ||
            currentReviewList[submissionIndex] !== word
        ) return false;
        isSubmitting = false;
        isAdvancing = false;
        if (submitBtn) submitBtn.classList.remove('btn-submit-loading');
        setReviewSubmitButtonState('submit', {
            disabled: isSemantic && !word._selectedOptionId,
        });
        const keepForRetry =
            !Number.isFinite(Number(error.status)) ||
            Number(error.status) >= 500 ||
            error.status === 408 ||
            error.status === 429;
        if (!keepForRetry) pendingReviewSubmission = null;
        if (captureInput) captureInput.readOnly = keepForRetry;
        const baseMessage = error.message || '提交失败，请重试';
        const msg = keepForRetry
            ? `${baseMessage}；再次提交将确认上次作答结果`
            : baseMessage;
        const reviewSection = document.getElementById('review-section');
        const messageDiv = document.getElementById('word-message');
        if (inflectionInput) {
            inflectionInput.disabled =
                Boolean(pendingReviewSubmission) ||
                word.exercise_type === 'listening' ||
                currentErrorCount > 0;
        }
        if (reviewSection && reviewSection.classList.contains('active') && messageDiv) {
            clearReviewWordMessageExtra();
            messageDiv.textContent = msg;
            messageDiv.className = 'word-message error';
            messageDiv.style.display = 'block';
            if (!isSemantic) focusWordCapture(0);
        } else {
            showError(msg);
        }
        return false;
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

