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
const REVIEW_NEW_WORDS_FIRST_STORAGE_KEY = 'english_reciter_review_new_words_first';
const REVIEW_NUMBER_DIRECT_SUBMIT_STORAGE_KEY = 'english_reciter_review_number_direct_submit';
const REVIEW_MANUAL_SPELLING_MESSAGE = '请手动逐字输入，不能粘贴或拖入答案。';

function getReviewTestInflectionEnabled() {
    const cb = document.getElementById('review-test-inflection');
    return Boolean(cb && cb.checked);
}

function getReviewNumberDirectSubmitEnabled() {
    return localStorage.getItem(REVIEW_NUMBER_DIRECT_SUBMIT_STORAGE_KEY) === '1';
}

function updateReviewPhoneticDisplay(word) {
    const phEl = document.getElementById('current-word-phonetic');
    const cb = document.getElementById('review-show-phonetic');
    if (!phEl) return;
    const show =
        cb &&
        cb.checked &&
        word &&
        word.exercise_type !== 'listening' &&
        String(word.phonetic || '').trim();
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

function sanitizeUnderlineTypedValue(value, maxLength = Infinity) {
    let v = String(value || '').replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
    if (Number.isFinite(maxLength) && maxLength >= 0 && v.length > maxLength) {
        v = v.slice(0, maxLength);
    }
    return v;
}

function isBlockedUnderlineInputType(inputType) {
    return [
        'insertFromPaste',
        'insertFromPasteAsQuotation',
        'insertFromDrop',
        'insertReplacementText',
        'insertFromYank',
    ].includes(String(inputType || ''));
}

function showManualSpellingInputMessage() {
    const messageDiv = document.getElementById('word-message');
    if (messageDiv) {
        messageDiv.textContent = REVIEW_MANUAL_SPELLING_MESSAGE;
        messageDiv.className = 'word-message error';
        messageDiv.style.display = 'block';
    }
}

function rejectAssistedUnderlineInput(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    const container = document.getElementById('underline-input');
    const capture = document.getElementById('mobile-word-capture');
    if (container) {
        container.dataset.blockedAssist = '1';
        container.dataset.currentInput = '';
        container.dataset.typedCharCount = '0';
    }
    if (capture) {
        capture.value = '';
    }
    updateUnderlineDisplay();
    showManualSpellingInputMessage();
    focusWordCapture(50);
    return false;
}

function restoreUnderlineInputFromState(value, typedCharCount = null, blockedAssist = false) {
    const container = document.getElementById('underline-input');
    const capture = document.getElementById('mobile-word-capture');
    if (!container || !capture) return;
    const maxLength = Number(container.dataset.wordLength || 0);
    const v = sanitizeUnderlineTypedValue(value, maxLength);
    capture.value = v;
    container.dataset.currentInput = v;
    const n = Number(typedCharCount);
    container.dataset.typedCharCount = String(
        Number.isFinite(n) && n >= v.length ? Math.floor(n) : v.length,
    );
    container.dataset.blockedAssist = blockedAssist ? '1' : '0';
    updateUnderlineDisplay();
}

function isUnderlineInputManualEnough() {
    const container = document.getElementById('underline-input');
    if (!container) return true;
    const current = container.dataset.currentInput || '';
    const typed = Number(container.dataset.typedCharCount || 0);
    return container.dataset.blockedAssist !== '1' && Number.isFinite(typed) && typed >= current.length;
}

function selectionIntersectsElement(selection, element) {
    if (!selection || !element) return false;
    for (let i = 0; i < selection.rangeCount; i += 1) {
        const range = selection.getRangeAt(i);
        try {
            if (range.intersectsNode(element)) return true;
        } catch (_) {
            if (element.contains(selection.anchorNode) || element.contains(selection.focusNode)) {
                return true;
            }
        }
    }
    return false;
}

function blockGuardedReviewCopy(event) {
    const selection = window.getSelection ? window.getSelection() : null;
    const guarded = [
        document.getElementById('current-word-english'),
        document.getElementById('new-word-study-english'),
        ...document.querySelectorAll('.new-word-study-example-en'),
    ].filter(Boolean);
    const target = event.target;
    const touchesGuardedSelection = guarded.some((el) => selectionIntersectsElement(selection, el));
    const startsOnGuardedText = Boolean(
        target && target.closest && target.closest('#current-word-english, #new-word-study-english, .new-word-study-example-en'),
    );
    if (!touchesGuardedSelection && !startsOnGuardedText) return;
    event.preventDefault();
    if (event.clipboardData) {
        event.clipboardData.setData('text/plain', '');
    }
    showManualSpellingInputMessage();
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
    container.dataset.typedCharCount = '0';
    container.dataset.blockedAssist = '0';

    capture.value = '';
    capture.readOnly = false;
    capture.setAttribute(
        'aria-label',
        word.exercise_type === 'listening' ? '输入听写的英文单词' : '输入英文单词',
    );
    capture.setAttribute('aria-describedby', 'review-exercise-type');

    const syncFromCapture = (event) => {
        const previous = container.dataset.currentInput || '';
        const inputType = String(event?.inputType || '');
        const inserted = sanitizeUnderlineTypedValue(event?.data || '');
        let v = sanitizeUnderlineTypedValue(capture.value, typeableCount);
        const grewBy = v.length - previous.length;
        if (
            isBlockedUnderlineInputType(inputType)
            || inserted.length > 1
            || grewBy > 1
            || (event && event.isTrusted === false && v !== previous)
        ) {
            rejectAssistedUnderlineInput(event);
            return;
        }
        capture.value = v;
        container.dataset.currentInput = v;
        container.dataset.blockedAssist = '0';
        if (grewBy > 0) {
            const typedCount = Number(container.dataset.typedCharCount || 0);
            container.dataset.typedCharCount = String(
                (Number.isFinite(typedCount) ? typedCount : 0) + grewBy,
            );
        }
        updateUnderlineDisplay();
    };

    capture.onbeforeinput = (e) => {
        const inserted = sanitizeUnderlineTypedValue(e.data || '');
        if (isBlockedUnderlineInputType(e.inputType) || inserted.length > 1) {
            rejectAssistedUnderlineInput(e);
        }
    };
    capture.onpaste = rejectAssistedUnderlineInput;
    capture.ondrop = rejectAssistedUnderlineInput;
    capture.ondragover = (e) => {
        e.preventDefault();
    };
    capture.oninput = syncFromCapture;
    capture.onkeydown = (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            if (!finishPendingReviewAdvance()) void submitAnswer();
            return;
        }
        const activeWord = currentReviewList[currentReviewIndex];
        if (
            e.code === 'Space'
            && activeWord?.exercise_type === 'listening'
            && !isSubmitting
            && !isAdvancing
        ) {
            e.preventDefault();
            void speakExample();
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
    container.dataset.typedCharCount = '0';
    container.dataset.blockedAssist = '0';
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
function speakEnglishInBrowser(text, onEnd, gen, playbackCallbacks = {}) {
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

    let speechSettled = false;
    let startWatchdog = null;
    const clearStartWatchdog = () => {
        if (startWatchdog !== null) {
            clearTimeout(startWatchdog);
            startWatchdog = null;
        }
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
            if (speechSettled) return;
            speechSettled = true;
            clearStartWatchdog();
            if (gen !== undefined && gen !== ttsPlaybackGeneration) return;
            baseDone();
        };
        u.onend = onDone;
        u.onerror = () => {
            if (speechSettled) return;
            if (gen !== undefined && gen !== ttsPlaybackGeneration) {
                speechSettled = true;
                clearStartWatchdog();
                return;
            }
            if (typeof playbackCallbacks.onError === 'function') {
                playbackCallbacks.onError();
            }
            onDone();
        };
        u.onstart = () => {
            if (speechSettled) return;
            if (gen !== undefined && gen !== ttsPlaybackGeneration) return;
            clearStartWatchdog();
            if (typeof playbackCallbacks.onStart === 'function') {
                playbackCallbacks.onStart();
            }
            try {
                synth.resume();
            } catch (_) {
                /* ignore */
            }
        };
        startWatchdog = setTimeout(() => {
            if (speechSettled) return;
            if (gen !== undefined && gen !== ttsPlaybackGeneration) {
                speechSettled = true;
                return;
            }
            try {
                synth.cancel();
            } catch (_) {
                /* ignore */
            }
            if (typeof playbackCallbacks.onError === 'function') {
                playbackCallbacks.onError();
            }
            onDone();
        }, WEB_SPEECH_START_TIMEOUT_MS);
        try {
            synth.speak(u);
        } catch (_) {
            if (typeof playbackCallbacks.onError === 'function') {
                playbackCallbacks.onError();
            }
            onDone();
            return;
        }
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
            if (gen !== undefined && gen !== ttsPlaybackGeneration) {
                settled = true;
                synth.removeEventListener('voiceschanged', runOnce);
                return;
            }
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
async function playPiperBlob(blob, onEnd, gen, onPlaybackError) {
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
    const cleanup = (notifyEnd = true) => {
        try {
            URL.revokeObjectURL(url);
        } catch (_) {
            /* ignore */
        }
        if (ttsPiperAudio === audio) ttsPiperAudio = null;
        if (ttsPiperObjectUrl === url) ttsPiperObjectUrl = null;
        if (gen !== undefined && gen !== ttsPlaybackGeneration) return;
        if (notifyEnd && typeof onEnd === 'function') onEnd();
    };
    audio.onended = () => cleanup(true);
    audio.onerror = () => {
        const isCurrent = gen === undefined || gen === ttsPlaybackGeneration;
        cleanup(false);
        if (isCurrent && typeof onPlaybackError === 'function') onPlaybackError();
    };
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
async function speakEnglishViaPiperApi(text, onEnd, gen, onPlaybackError) {
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
    return playPiperBlob(blob, onEnd, gen, onPlaybackError);
}

/** 优先 Piper，其次 Web Speech，最后服务端 say（仅服务器本机扬声器）
 * @param {string} text
 * @param {(() => void) | undefined} onEnd
 * @param {HTMLElement | null | undefined} triggerButton 触发朗读的 .btn-speak，用于加载态
 * @param {{ piperBlob?: Blob, clientAudioOnly?: boolean, onPlaybackStart?: () => void, onPlaybackError?: () => void } | undefined} opts 播放选项 */
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
            const okPre = await playPiperBlob(pre, done, myGen, opts.onPlaybackError);
            if (myGen !== ttsPlaybackGeneration) return false;
            if (okPre) {
                if (typeof opts.onPlaybackStart === 'function') opts.onPlaybackStart();
                return true;
            }
        }
        const ok = await speakEnglishViaPiperApi(raw, done, myGen, opts.onPlaybackError);
        if (myGen !== ttsPlaybackGeneration) return false;
        if (ok) {
            if (typeof opts.onPlaybackStart === 'function') opts.onPlaybackStart();
            return true;
        }
    }
    if (myGen !== ttsPlaybackGeneration) return false;
    if (speakEnglishInBrowser(raw, done, myGen, {
        onStart: opts.onPlaybackStart,
        onError: opts.onPlaybackError,
    })) {
        return true;
    }
    if (opts.clientAudioOnly) {
        if (myGen === ttsPlaybackGeneration) endTtsSpeakUi();
        return false;
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

async function refreshTtsCapabilities(accountContext = captureAccountContext()) {
    if (!isAccountContextCurrent(accountContext)) return;
    if (!token) {
        serverPiperAvailable = false;
        return;
    }
    try {
        const data = await apiRequest('/tts/capabilities');
        if (!isAccountContextCurrent(accountContext)) return;
        serverPiperAvailable = data && data.piper === true;
    } catch (_) {
        if (isAccountContextCurrent(accountContext)) serverPiperAvailable = false;
    }
}

function downgradeListeningSession(failedWord) {
    if (
        !failedWord ||
        currentReviewList[currentReviewIndex] !== failedWord ||
        failedWord.exercise_type !== 'listening' ||
        isSubmitting ||
        isAdvancing
    ) return;
    listeningPlaybackAttempt += 1;
    stopSpeakPlayback();
    endTtsSpeakUi();
    let downgraded = 0;
    for (let index = currentReviewIndex; index < currentReviewList.length; index += 1) {
        const word = currentReviewList[index];
        word._audioAvailableAtRender = false;
        word._listeningAudioPlayed = false;
        if (word.exercise_type === 'listening') {
            word.exercise_type = 'spelling';
            downgraded += 1;
        }
    }
    if (downgraded && currentTodayTaskPlan?.exercise_mix) {
        const mix = { ...currentTodayTaskPlan.exercise_mix };
        mix.listening = Math.max(0, (Number(mix.listening) || 0) - downgraded);
        mix.spelling = (Number(mix.spelling) || 0) + downgraded;
        currentTodayTaskPlan.exercise_mix = mix;
        renderTodayTaskPlan(currentTodayTaskPlan);
    }
    showMainBanner('当前设备无法播放听写音频，已切换为拼写');
    void showCurrentWord();
}

// 朗读例句
async function speakExample() {
    const word = currentReviewList[currentReviewIndex];
    if (!word) return;

    if (word.exercise_type === 'listening') {
        const playbackAttempt = ++listeningPlaybackAttempt;
        let playbackFailed = false;
        let startWatchdog = null;
        const clearWatchdog = () => {
            if (startWatchdog !== null) {
                clearTimeout(startWatchdog);
                startWatchdog = null;
            }
        };
        const isCurrentPlaybackAttempt = () => Boolean(
            playbackAttempt === listeningPlaybackAttempt &&
            currentReviewList[currentReviewIndex] === word &&
            word.exercise_type === 'listening'
        );
        const handlePlaybackFailure = () => {
            if (!isCurrentPlaybackAttempt()) {
                clearWatchdog();
                return;
            }
            if (playbackFailed) return;
            playbackFailed = true;
            clearWatchdog();
            downgradeListeningSession(word);
        };
        startWatchdog = setTimeout(
            handlePlaybackFailure,
            LISTENING_PLAYBACK_START_TIMEOUT_MS,
        );
        const accepted = await speakEnglishPreferred(
            word.english,
            () => focusWordCapture(0),
            document.getElementById('speak-example-btn'),
            {
                clientAudioOnly: true,
                onPlaybackStart: () => {
                    clearWatchdog();
                    if (isCurrentPlaybackAttempt()) {
                        word._listeningAudioPlayed = true;
                    }
                },
                onPlaybackError: handlePlaybackFailure,
            },
        );
        if (!accepted) handlePlaybackFailure();
        return;
    }

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

