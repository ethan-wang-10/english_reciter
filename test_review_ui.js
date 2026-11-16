const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'static/js/app.js'), 'utf8');

function review(t, exerciseType = 'spelling', attemptLimit = 3, includeFinalAttempt = true) {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    class Element {
        dataset = {};
        style = {};
        hidden = true;
        textContent = '';
        classList = { add() {}, remove() {}, toggle() {}, contains: () => true };
        setAttribute() {}
        appendChild() {}
        querySelectorAll() { return []; }
        closest() { return null; }
    }
    const elements = new Map([
        'review-section', 'submit-answer', 'word-message', 'current-word-english',
        'underline-input', 'mobile-word-capture', 'semantic-question', 'semantic-question-feedback',
    ].map((id) => [id, new Element()]));
    const word = { english: 'job', exercise_type: exerciseType, _listeningAudioPlayed: true };
    const state = { calls: [], shown: 0, correct: false };
    const context = vm.createContext({
        Element, URLSearchParams, performance, setTimeout, clearTimeout,
        window: { location: { search: '' } },
        localStorage: { getItem: () => null },
        document: {
            getElementById: (id) => elements.get(id) || null,
            createElement: () => new Element(),
            querySelectorAll: () => [],
            addEventListener() {},
        },
        word,
    });
    vm.runInContext(source, context);
    Object.assign(context, {
        apiRequest: async (url, options) => {
            const body = JSON.parse(options.body);
            state.calls.push(body);
            const result = {
                correct: state.correct,
                message: state.correct ? 'Correct' : 'Incorrect',
                attempt_number: body.attempt_number,
                attempt_limit: attemptLimit,
                answer_feedback: { explanation_zh: 'Answer explanation' },
            };
            if (includeFinalAttempt) result.final_attempt = body.attempt_number >= attemptLimit;
            return result;
        },
        createReviewEventId: () => `event-${state.calls.length}`,
        focusWordCapture() {},
        renderWrongPanel() {},
        loadStats() {},
        showCurrentWord: () => { state.shown += 1; },
        isSettingsOverlayOpen: () => false,
        isRemedialOfferModalOpen: () => false,
    });
    vm.runInContext('currentReviewList = [word, { english: "next" }]', context);
    context.initializeUnderlineInputForTarget(word, word.english);
    const field = (id) => elements.get(id);
    return {
        context, state, field,
        index: () => vm.runInContext('currentReviewIndex', context),
        async answer() {
            word._selectedOptionId = 'wrong';
            const capture = field('mobile-word-capture');
            capture.value = '';
            for (const char of 'bad') {
                capture.value += char;
                capture.oninput({ inputType: 'insertText', data: char, isTrusted: true });
            }
            assert.equal(await context.submitAnswer(), true);
        },
        enter(repeat = false) {
            const event = {
                key: 'Enter', repeat, target: field('mobile-word-capture'),
                preventDefault() { this.defaultPrevented = true; },
            };
            if (['spelling', 'listening'].includes(exerciseType)) {
                field('mobile-word-capture').onkeydown(event);
            } else {
                context.handleSemanticQuestionKeydown(event);
            }
            assert.equal(event.defaultPrevented, true);
        },
    };
}

for (const [exerciseType, limit] of [
    ['spelling', 3], ['listening', 3], ['recognition', 2], ['context', 2], ['context', 3],
]) {
    test(`${exerciseType} waits after ${limit} errors until a fresh Enter press`, async (t) => {
        const ui = review(t, exerciseType, limit);
        for (let attempt = 1; attempt <= limit; attempt += 1) {
            await ui.answer();
            t.mock.timers.tick(60000);
            assert.equal(ui.index(), 0);
            assert.equal(ui.state.shown, 0);
            assert.equal(ui.field('word-message').style.display, 'block');
        }
        assert.equal(ui.field('submit-answer').dataset.reviewAction, 'next');
        assert.equal(ui.field('submit-answer').disabled, false);
        assert.equal(ui.field('word-message').textContent, 'Incorrect');
        if (['recognition', 'context'].includes(exerciseType)) {
            assert.equal(ui.field('semantic-question-feedback').hidden, false);
            assert.equal(ui.field('semantic-question-feedback').textContent, 'Answer explanation');
        } else {
            assert.equal(ui.field('current-word-english').textContent, 'job');
        }
        assert.equal(await ui.context.submitAnswer(), false);
        ui.enter(true);
        assert.equal(ui.index(), 0);
        ui.enter();
        assert.equal(ui.index(), 1);
        assert.equal(ui.state.shown, 1);
        t.mock.timers.tick(60000);
        assert.equal(ui.index(), 1);
        assert.equal(ui.state.calls.length, limit);
    });
}

test('local attempt limit also waits when final_attempt is absent', async (t) => {
    const ui = review(t, 'spelling', 3, false);
    for (let attempt = 0; attempt < 3; attempt += 1) await ui.answer();
    t.mock.timers.tick(60000);
    assert.equal(ui.index(), 0);
    assert.equal(ui.field('submit-answer').dataset.reviewAction, 'next');
    assert.equal(ui.context.finishPendingReviewAdvance(), true);
    assert.equal(ui.context.finishPendingReviewAdvance(), false);
    assert.equal(ui.index(), 1);
    assert.equal(ui.state.shown, 1);
});

test('correct answers still advance automatically', async (t) => {
    const ui = review(t);
    ui.state.correct = true;
    await ui.answer();
    assert.equal(ui.index(), 0);
    t.mock.timers.tick(60000);
    assert.equal(ui.index(), 1);
    assert.equal(ui.state.shown, 1);
});

test('manual advance cancels the timer for a correct answer', async (t) => {
    const ui = review(t);
    ui.state.correct = true;
    await ui.answer();
    ui.enter();
    t.mock.timers.tick(60000);
    assert.equal(ui.index(), 1);
    assert.equal(ui.state.shown, 1);
});

test('pending feedback cannot advance a replacement review session', async (t) => {
    const ui = review(t);
    for (let attempt = 0; attempt < 3; attempt += 1) await ui.answer();
    vm.runInContext('reviewSessionGeneration += 1', ui.context);
    assert.equal(ui.context.finishPendingReviewAdvance(), false);
    assert.equal(ui.index(), 0);
    assert.equal(ui.state.shown, 0);
});


const settleDailyTts = () => new Promise(setImmediate);

function dailyTtsUI({ parent = false } = {}) {
    const state = { calls: [], capabilities: { piper: false, kokoro: false }, spoken: [], audios: [], played: [], urls: new Map(), revoked: [], focused: 0, logins: 0 };
    const timers = new Map(); let clock = 0, timerId = 0;
    const button = { disabled: false, dataset: {}, attributes: {}, classes: new Set(['btn-speak']),
        setAttribute(name, value) { this.attributes[name] = String(value); }, removeAttribute(name) { delete this.attributes[name]; } };
    button.classList = { contains: (name) => button.classes.has(name), add: (name) => button.classes.add(name), remove: (name) => button.classes.delete(name) };
    const storage = new Map([['token', parent ? 'parent-token' : 'alice-token'], ['username', parent ? 'alice_parent' : 'alice']]);
    if (parent) { storage.set('session_is_parent', '1'); storage.set('session_child_username', 'alice'); }
    const synthesis = { getVoices: () => [{ name: 'English device', lang: 'en-US' }], cancel() {}, resume() {},
        speak(utterance) { state.spoken.push(utterance); utterance.onstart?.(); } };
    class Audio {
        constructor(url) { this.src = url; state.audios.push(this); }
        play() { state.played.push(this); this.paused = false; return Promise.resolve(); }
        pause() { this.paused = true; }
        removeAttribute(name) { if (name === 'src') this.src = ''; }
        load() {}
    }
    class ObjectURL extends URL {
        static createObjectURL(blob) { assert.ok(blob instanceof Blob); const url = 'blob:daily-tts-' + (state.urls.size + state.revoked.length + 1); state.urls.set(url, blob); return url; }
        static revokeObjectURL(url) { state.urls.delete(url); state.revoked.push(url); }
    }
    const jsonResponse = (data, status = 200) => ({ status, ok: status >= 200 && status < 300, text: async () => JSON.stringify(data) });
    const audioResponse = (status = 200) => ({ status, ok: status >= 200 && status < 300,
        headers: { get: () => 'audio/wav' }, blob: async () => new Blob(['RIFF-server-recording-'.repeat(20)], { type: 'audio/wav' }) });
    const context = vm.createContext({
        window: { location: { search: '' }, speechSynthesis: synthesis },
        document: { querySelectorAll: (selector) => selector === '.btn-speak' ? [button] : [], getElementById: () => null },
        localStorage: { getItem: (key) => storage.get(key) || null },
        URL: ObjectURL, URLSearchParams, Blob, AbortController, Audio, SpeechSynthesisUtterance: class { constructor(text) { this.text = text; } },
        console, performance,
        setTimeout(callback, delay = 0) { const id = ++timerId; timers.set(id, { at: clock + Number(delay), callback }); return id; },
        clearTimeout(id) { timers.delete(id); },
        async fetch(url, options = {}) {
            const call = { url, method: options.method || 'GET', headers: options.headers || {}, signal: options.signal,
                body: options.body ? JSON.parse(options.body) : null };
            state.calls.push(call);
            if (url === '/api/tts/capabilities') return state.capabilityFetch ? state.capabilityFetch(call) : jsonResponse(state.capabilities, state.capabilityStatus || 200);
            if (url === '/api/words/speak-audio') return state.audioFetch ? state.audioFetch(call) : audioResponse(state.audioStatus || 200);
            assert.equal(url, '/api/words/speak', 'Speech never calls unrelated learning or mutation routes');
            return jsonResponse({ ok: true });
        },
        focusWordCapture() { state.focused += 1; },
        showLoginPage() { state.logins += 1; },
    });
    for (const part of ['core.js', 'api.js', 'review-input.js']) vm.runInContext(fs.readFileSync(path.join(__dirname, 'static/js/app', part), 'utf8'), context);
    context.clearAccountSession = () => { vm.runInContext("token = null; username = ''; accountSessionGeneration += 1", context); };
    context.showLoginPage = () => { state.logins += 1; context.stopSpeakPlayback(); context.endTtsSpeakUi(); };
    return { state, context, button, jsonResponse,
        async flush() { await settleDailyTts(); await settleDailyTts(); },
        async tick(milliseconds) {
            const target = clock + milliseconds;
            for (let count = 0; count < 100; count += 1) {
                const next = [...timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
                if (!next) { clock = target; await this.flush(); return; }
                clock = next[1].at; timers.delete(next[0]); next[1].callback(); await this.flush();
            }
            throw new Error('Daily speech timers did not settle');
        },
    };
}

test('daily English pronunciation checks the current provider each time and ignores a prefetched blob after the admin toggle', { timeout: 3000 }, async () => {
    const ui = dailyTtsUI(); let completed = 0, started = 0;
    const staleBlob = new Blob(['RIFF-old-kokoro-'.repeat(20)], { type: 'audio/wav' });
    const speak = () => ui.context.speakEnglishPreferred('Read the book.', () => { completed += 1; }, ui.button,
        { piperBlob: staleBlob, onPlaybackStart: () => { started += 1; } });
    assert.equal(await speak(), true); await ui.tick(0);
    assert.equal(ui.state.spoken.length, 1); assert.equal(ui.state.played.length, 0); ui.state.spoken.at(-1).onend();
    for (const capabilities of [{ piper: false, kokoro: true }, { piper: true, kokoro: false }]) {
        ui.state.capabilities = capabilities;
        assert.equal(await speak(), true);
        const request = ui.state.calls.at(-1); assert.equal(request.url, '/api/words/speak-audio'); assert.equal(request.method, 'POST');
        assert.deepEqual(request.body, { text: 'Read the book.' }); assert.equal(request.headers.Authorization, 'Bearer alice-token');
        const audio = ui.state.played.at(-1); const playedBlob = ui.state.urls.get(audio.src);
        assert.notStrictEqual(playedBlob, staleBlob); assert.match(await playedBlob.text(), /server-recording/);
        audio.onended();
    }
    ui.state.capabilities = { piper: false, kokoro: false }; assert.equal(await speak(), true); await ui.tick(0);
    assert.equal(ui.state.spoken.length, 2); ui.state.spoken.at(-1).onend();
    const capabilities = ui.state.calls.filter((call) => call.url === '/api/tts/capabilities');
    assert.equal(capabilities.length, 4); assert.ok(capabilities.every((call) => call.method === 'GET' && call.signal && call.headers.Authorization === 'Bearer alice-token'));
    assert.equal(ui.state.calls.filter((call) => call.url === '/api/words/speak-audio').length, 2);
    assert.equal(completed, 4); assert.equal(started, 4); assert.equal(ui.button.disabled, false); assert.equal(ui.state.urls.size, 0);
});

test('daily English capability deadlines, cancellation and authorization failure cannot trigger stale audio or bypass the parent restriction', { timeout: 3000 }, async () => {
    const ui = dailyTtsUI(); let resolve; let completed = 0;
    ui.state.capabilityFetch = () => new Promise((yes) => { resolve = yes; });
    const pending = ui.context.speakEnglishPreferred('First phrase.', () => { completed += 1; }, ui.button);
    await ui.flush(); await ui.tick(999); assert.equal(ui.state.spoken.length, 0);
    await ui.tick(1); assert.equal(await pending, true); await ui.tick(0);
    assert.equal(ui.state.calls[0].signal.aborted, true); assert.equal(ui.state.spoken.length, 1);
    resolve(ui.jsonResponse({ piper: false, kokoro: true })); await ui.flush();
    assert.equal(ui.state.played.length, 0); assert.equal(ui.state.spoken.length, 1); ui.state.spoken[0].onend(); assert.equal(completed, 1);
    const canceled = ui.context.speakEnglishPreferred('Canceled phrase.', () => { completed += 1; }, ui.button); await ui.flush();
    const aborted = ui.state.calls.at(-1); ui.context.stopSpeakPlayback(); assert.equal(aborted.signal.aborted, true);
    resolve(ui.jsonResponse({ piper: true, kokoro: false })); assert.equal(await canceled, false); await ui.tick(2000);
    assert.equal(ui.state.played.length, 0); assert.equal(ui.state.spoken.length, 1); assert.equal(completed, 1);
    const switched = ui.context.speakEnglishPreferred('Old account phrase.', () => { completed += 1; }, ui.button); await ui.flush();
    vm.runInContext("token = 'bob-token'; username = 'bob'; accountSessionGeneration += 1", ui.context);
    resolve(ui.jsonResponse({ piper: true, kokoro: true })); assert.equal(await switched, false); await ui.tick(2000);
    assert.equal(ui.state.played.length, 0); assert.equal(ui.state.spoken.length, 1); assert.equal(completed, 1);
    for (const status of [401, 403]) {
        const denied = dailyTtsUI(); denied.state.capabilityStatus = status;
        assert.equal(await denied.context.speakEnglishPreferred('Private phrase.', () => { completed += 1; }, denied.button), false);
        await denied.tick(2000); assert.equal(denied.state.spoken.length, 0); assert.equal(denied.state.played.length, 0);
        assert.equal(denied.state.calls.some((call) => call.url === '/api/words/speak'), false); assert.equal(denied.button.disabled, false);
        assert.equal(denied.state.logins, status === 401 ? 1 : 0, 'Capability requests preserve the existing host session-expiry behavior');
    }
    const parent = dailyTtsUI({ parent: true }); parent.state.capabilities = { piper: true, kokoro: true }; parent.state.audioStatus = 403;
    assert.equal(await parent.context.speakEnglishPreferred('Child word.', () => { completed += 1; }, parent.button), false); await parent.tick(2000);
    assert.equal(parent.state.calls.at(-1).headers.Authorization, 'Bearer parent-token');
    assert.equal(parent.state.spoken.length, 0); assert.equal(parent.state.played.length, 0); assert.equal(parent.state.calls.some((call) => call.url === '/api/words/speak'), false);
    assert.equal(completed, 1);
});
