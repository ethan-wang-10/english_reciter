const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function fixture() {
    const marked = [false, true].map((disabled) => ({
        disabled: true,
        dataset: { ttsWasDisabled: disabled ? '1' : '0' },
        classList: { remove(value) { assert.equal(value, 'btn-speak-loading'); } },
        removeAttribute(value) { assert.equal(value, 'aria-busy'); },
    }));
    const state = { generation: 1, requests: [], stops: 0, marked };
    const context = vm.createContext({
        window: {}, isParentSession: true,
        captureAccountContext: () => ({ token: 'parent-token', username: 'parent', generation: state.generation }),
        sessionStudentUsername: () => 'child',
        isAccountContextCurrent: (snapshot) => snapshot.generation === state.generation,
        apiRequest: async (url, options) => { state.requests.push({ url, options }); return state.response?.(); },
        stopSpeakPlayback: () => { state.stops++; },
        document: { querySelectorAll(selector) {
            assert.equal(selector, '.btn-speak[data-tts-was-disabled]');
            return marked;
        } },
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, 'static/js/english-dictation-host.js'), 'utf8'), context);
    return { host: context.window.englishDictationHost, state };
}

test('English host uses child identity and serializes request bodies exactly once', async () => {
    const { host, state } = fixture();
    assert.equal(host.getSession().student, 'child');
    assert.equal(host.getSession().token, 'parent-token');
    const body = { base_revision: 0, words: [{ text: "Don't run, please." }] };
    await host.apiRequest('/subjects/english/dictation/wordlists/book/list', { method: 'PUT', body });
    assert.equal(state.requests[0].options.body, JSON.stringify(body));
    await host.apiRequest('/path', { method: 'POST', body: JSON.stringify(body) });
    assert.equal(state.requests[1].options.body, JSON.stringify(body));
});

test('a successful response arriving after account change cannot update the new account', async () => {
    const { host, state } = fixture();
    let resolve;
    state.response = () => new Promise((done) => { resolve = done; });
    const request = host.apiRequest('/path');
    state.generation++;
    resolve({ secret: 'old child data' });
    await assert.rejects(request, (error) => error.stale === true);
});

test('current HTTP errors survive while errors from a previous account are stale', async () => {
    const { host, state } = fixture();
    const conflict = Object.assign(new Error('conflict'), { status: 409 });
    state.response = () => { throw conflict; };
    await assert.rejects(host.apiRequest('/path'), (error) => error === conflict);
    state.response = async () => { state.generation++; throw conflict; };
    await assert.rejects(host.apiRequest('/path'), (error) => error.stale === true);
});

test('stopping other speech restores only controls marked by an active reading', () => {
    const { host, state } = fixture();
    host.stopOtherSpeech();
    assert.equal(state.stops, 1);
    assert.equal(state.marked[0].disabled, false);
    assert.equal(state.marked[1].disabled, true);
    assert.equal('ttsWasDisabled' in state.marked[0].dataset, false);
});
