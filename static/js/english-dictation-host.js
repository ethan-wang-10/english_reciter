/* Account and API bridge for the standalone English dictation workbench. */
(() => {
    'use strict';
    const stale = () => Object.assign(new Error('登录账号已变更，请重新打开生词默写。'), { stale: true });
    window.englishDictationHost = {
        getSession() {
            const context = captureAccountContext();
            return { ...context, student: sessionStudentUsername(), isParent: isParentSession };
        },
        async apiRequest(path, options = {}) {
            const context = captureAccountContext();
            const body = options.body !== undefined && typeof options.body !== 'string'
                ? JSON.stringify(options.body) : options.body;
            try {
                const result = await apiRequest(path, { ...options, body });
                if (!isAccountContextCurrent(context)) throw stale();
                return result;
            } catch (error) {
                if (!isAccountContextCurrent(context)) throw stale();
                throw error;
            }
        },
        stopOtherSpeech() {
            if (typeof stopSpeakPlayback === 'function') stopSpeakPlayback();
            document.querySelectorAll('.btn-speak[data-tts-was-disabled]').forEach((button) => {
                button.disabled = button.dataset.ttsWasDisabled === '1';
                delete button.dataset.ttsWasDisabled;
                button.classList.remove('btn-speak-loading');
                button.removeAttribute('aria-busy');
            });
        },
    };
})();
