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
