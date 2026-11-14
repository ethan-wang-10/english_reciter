(function () {
    'use strict';

    const el = (id) => document.getElementById(id);
    if (!el('english-dictation-panel')) return;
    const PREFIX = '/subjects/english/dictation';
    const portal = () => window.englishDictationHost;
    const session = () => portal()?.getSession() || {};
    const learner = () => session().student || '';
    const requests = new Set();
    async function request(path, options = {}) {
        const controller = new AbortController(); requests.add(controller);
        try { return await portal().apiRequest(PREFIX + path, { ...options, signal: controller.signal }); }
        finally { requests.delete(controller); }
    }
    const cloneSources = (sources) => (sources || []).map((source) => ({ book_id: source.book_id,
        lesson_id: source.lesson_id, title: source.title, number: source.number }));
    const cloneWords = (words) => words.map((word) => ({ text: word.text, spoken_text: word.spoken_text || word.text,
        ...(word.sources ? { sources: cloneSources(word.sources) } : {}) }));
    let generation = 0, requestSequence = 0, lessonSequence = 0, viewSequence = 0;
    let boundStudent = '', boundToken = '', boundHostGeneration, active = false, loaded = false, loading = false, saving = false;
    let books = [], lessons = [], summaries = new Map(), expandedUnits = new Set();
    let bookId = '', lesson = null, wordlist = null, raw = '', conflict = null;
    let run = null, voices = [], voiceChosen = false, speechEpoch = 0, speaking = false, speechMode = '', utterance = null;
    let repeatTimer = null, watchdog = null, suppressSpaceUp = false;
    let homeMode = 'lessons', mistakeItems = [], mistakeSelections = new Set(), mistakeExpanded = new Set();
    let mistakeLoading = false, mistakeLoaded = false, mistakeDeleting = false, mistakeSequence = 0;
    let mistakeStatus = '', mistakeStatusError = false;
    let audioFetch = null, playerAudio = null, audioUrl = '', cachedAudio = null, fetchTimer = null;

    function visible() { return active && el('english-dictation-section').classList.contains('active') && !el('english-dictation-section').hidden; }

    function sourceName(source) { return `${source.number ? `${source.number}. ` : ''}${source.title || '我的清单'}`; }
    function sourceKey(source, word) { return JSON.stringify([source.book_id, source.lesson_id, word.text, word.spoken_text || word.text]); }
    function mergeRunWords(words) {
        const merged = new Map();
        for (const word of words) {
            const key = JSON.stringify([word.text, word.spoken_text || word.text]);
            if (!merged.has(key)) merged.set(key, { text: word.text, spoken_text: word.spoken_text || word.text, sources: [] });
            const target = merged.get(key);
            for (const source of cloneSources(word.sources)) {
                if (!target.sources.some((item) => item.book_id === source.book_id && item.lesson_id === source.lesson_id)) target.sources.push(source);
            }
        }
        return Array.from(merged.values());
    }
    function mistakeRunWords() {
        return mergeRunWords(mistakeItems.filter((item) => mistakeSelections.has(item.lesson_id)).flatMap((item) =>
            item.words.map((word) => ({ text: word.text, spoken_text: word.spoken_text || word.text,
                sources: [{ book_id: item.book_id, lesson_id: item.lesson_id, title: item.lesson.title, number: item.lesson.number }] }))));
    }
    function setMistakeStatus(text, error = false) {
        mistakeStatus = text; mistakeStatusError = error;
        el('english-dictation-mistakes-status').textContent = text;
        el('english-dictation-mistakes-status').classList.toggle('english-dictation-status-error', error);
    }
    function renderMode() {
        el('english-dictation-mode-lessons').setAttribute('aria-pressed', String(homeMode === 'lessons'));
        el('english-dictation-mode-mistakes').setAttribute('aria-pressed', String(homeMode === 'mistakes'));
        el('english-dictation-lesson-workspace').hidden = homeMode !== 'lessons';
        el('english-dictation-mistakes-home').hidden = homeMode !== 'mistakes';
    }

    function node(tag, text = '', className = '') {
        const result = document.createElement(tag);
        if (text) result.textContent = text;
        if (className) result.className = className;
        return result;
    }
    function message(text = '', error = false) {
        const target = el('english-dictation-message');
        target.textContent = text;
        target.hidden = !text;
        target.classList.toggle('feedback-error', error);
        target.classList.toggle('feedback-status', !error);
    }
    function stateLabel(text) { el('english-dictation-save-state').textContent = text; }
    function capture(scoped = false) {
        return { generation, token: boundToken, student: boundStudent, hostGeneration: boundHostGeneration,
            book: scoped ? bookId : null, lesson: scoped ? lesson?.id : null };
    }
    function current(ctx) {
        return ctx.generation === generation && ctx.token === boundToken && ctx.student === boundStudent
            && ctx.token === session().token && ctx.student === learner() && ctx.hostGeneration === session().generation
            && (ctx.book === null || ctx.book === bookId) && (ctx.lesson === null || ctx.lesson === lesson?.id);
    }
    function signedIn() {
        return !!session().token && !!learner();
    }
    function backupKey(student = boundStudent, book = bookId, id = lesson?.id) {
        return `itorange-english-dictation-draft:${student}:${book}:${id}`;
    }
    function normalized(text) { return String(text).normalize('NFC').trim().replace(/\s+/gu, ' '); }
    function formatWords(words) {
        return words.map((word) => word.text).join('\n');
    }
    function parseWords(value) {
        if (value.length > 120000) throw new Error('清单内容过长，请控制在 120000 个字符以内。');
        const entries = [], seen = new Set();
        const aliases = new Map((wordlist?.words || []).map((word) => [word.text, word.spoken_text || word.text]));
        let duplicates = 0;
        for (const line of value.split(/\r?\n|\r/u)) {
            const text = normalized(line);
            if (!text) continue;
            if (Array.from(text).length > 500) throw new Error('每行最多 500 个字符，请将过长的内容分行。');
            if (/[\p{Cc}\p{Cf}\p{Cs}]/u.test(text)) throw new Error('条目中不能包含控制字符或不可见字符。');
            if (!/[\p{L}\p{N}]/u.test(text)) throw new Error('每条至少需要一个字母、汉字或数字。');
            if (seen.has(text)) { duplicates += 1; continue; }
            seen.add(text);
            entries.push({ text, spoken_text: aliases.get(text) || text });
        }
        if (entries.length > 200) throw new Error('每份清单最多保存 200 条，请分组整理。');
        return { words: entries, duplicates };
    }
    function unchanged(words) {
        return !!wordlist && JSON.stringify(words) === JSON.stringify(cloneWords(wordlist.words || []));
    }
    function removeBackup() {
        if (!boundStudent || !bookId || !lesson) return;
        try { localStorage.removeItem(backupKey()); } catch (_) { /* Cloud saving still works without local storage. */ }
    }
    function preserveDraft() {
        if (!boundStudent || !bookId || !lesson || !wordlist) return;
        try {
            if (!conflict && unchanged(parseWords(raw).words)) { removeBackup(); return true; }
        } catch (_) { /* Invalid input must remain recoverable too. */ }
        try {
            localStorage.setItem(backupKey(), JSON.stringify({ student: boundStudent, book_id: bookId,
                lesson_id: lesson.id, raw, base_revision: conflict?.baseRevision ?? wordlist.revision }));
            return true;
        } catch (_) { stateLabel('本机空间不足，请及时保存到云端'); return false; }
    }
    function restoreDraft() {
        try {
            const draft = JSON.parse(localStorage.getItem(backupKey()) || 'null');
            if (!draft || draft.student !== boundStudent || draft.book_id !== bookId || draft.lesson_id !== lesson.id
                || typeof draft.raw !== 'string' || draft.raw.length > 120000 || !Number.isSafeInteger(draft.base_revision)
                || draft.base_revision < 0) return;
            if (draft.raw === formatWords(wordlist.words || [])) { removeBackup(); return; }
            raw = draft.raw;
            if (draft.base_revision !== wordlist.revision) {
                conflict = { localRaw: raw, baseRevision: draft.base_revision, cloud: wordlist };
                message('这份清单的云端内容已更新。本机未保存的内容已保留，请先复制并对照。', true);
                stateLabel('本机内容已保留，等待对照');
            } else {
                stateLabel('已恢复本机未保存清单内容');
                message('已恢复本机尚未保存的条目，确认后点击保存。');
            }
        } catch (_) { /* A corrupt optional backup must never replace the cloud list. */ }
    }
    function renderResume() {
        el('english-dictation-resume-banner').hidden = !run;
        el('english-dictation-end-run').hidden = !run || run.completed;
        el('english-dictation-resume').textContent = run?.completed ? '返回上次对照' : '继续这次默写';
        if (run) el('english-dictation-resume-label').textContent = run.completed ? '上一轮已经结束，可以继续对照、保存错题或重练。'
            : `还有一轮默写未结束：第 ${run.index + 1} 条／共 ${run.words.length} 条。`;
    }
    function showView(name) {
        el('english-dictation-home').hidden = name !== 'home';
        el('english-dictation-player').hidden = name !== 'player';
        el('english-dictation-finish').hidden = name !== 'finish';
        if (name === 'home') renderResume();
    }
    function renderControls() {
        const blocked = loading || saving;
        el('english-dictation-book').disabled = blocked || mistakeLoading || mistakeDeleting;
        el('english-dictation-mode-lessons').disabled = blocked;
        el('english-dictation-mode-mistakes').disabled = blocked;
        el('english-dictation-add-lesson').disabled = blocked;
        el('english-dictation-custom-title').disabled = blocked;
        el('english-dictation-custom-number').disabled = blocked;
        for (const button of el('english-dictation-lessons').querySelectorAll('button')) button.disabled = blocked;
        el('english-dictation-words').disabled = blocked || !!conflict;
        el('english-dictation-save').disabled = blocked || !!conflict || !wordlist;
        el('english-dictation-start').disabled = blocked || !!conflict || !wordlist;
        el('english-dictation-conflict').hidden = !conflict;
        el('english-dictation-keep-cloud').disabled = saving || loading;
        el('english-dictation-confirm-cloud').disabled = saving || loading;
        renderMistakeControls();
    }
    function renderWords() {
        const container = el('english-dictation-word-preview');
        container.replaceChildren();
        try {
            const result = parseWords(raw);
            el('english-dictation-word-count').textContent = `${result.words.length} 条不同内容${result.duplicates ? `，已合并 ${result.duplicates} 个重复项` : ''} · 每份清单最多 200 条`;
            for (const word of result.words) {
                const chip = node('div', '', 'english-dictation-word-chip');
                const label = node('span', word.text);
                if (word.spoken_text !== word.text) label.appendChild(node('small', `朗读：${word.spoken_text}`));
                const play = node('button', '试听');
                play.type = 'button';
                play.setAttribute('aria-label', `试听 ${word.text}`);
                play.addEventListener('click', () => {
                    if (!active || loading || saving || !signedIn()) return;
                    speakWords(word, 1, 'preview');
                });
                chip.append(label, play);
                container.appendChild(chip);
            }
        } catch (error) { el('english-dictation-word-count').textContent = error.message; }
    }
    function renderDirectory() {
        const container = el('english-dictation-lessons');
        for (const details of container.querySelectorAll('details')) {
            const key = details.dataset.unit;
            if (details.open) expandedUnits.add(key); else expandedUnits.delete(key);
        }
        container.replaceChildren();
        const groups = new Map();
        for (const item of lessons) {
            const group = item.custom ? '我的其他清单' : item.unit || '清单目录';
            if (!groups.has(group)) groups.set(group, []);
            groups.get(group).push(item);
        }
        for (const [unit, items] of groups) {
            const details = node('details', '', 'english-dictation-unit');
            const unitKey = `${bookId}:${unit}`;
            details.dataset.unit = unitKey;
            details.open = expandedUnits.has(unitKey);
            const summary = node('summary', unit);
            summary.appendChild(node('span', `${items.length} 份`));
            details.appendChild(summary);
            for (const item of items) {
                const button = node('button', '', 'english-dictation-lesson');
                button.type = 'button';
                const title = `${item.number ? `${item.number} ` : ''}${item.title}`;
                button.setAttribute('aria-label', `选择清单 ${item.title}`);
                button.dataset.lessonId = item.id;
                button.setAttribute('aria-pressed', String(lesson?.id === item.id));
                button.appendChild(node('span', title));
                const count = summaries.get(item.id)?.word_count || 0;
                button.appendChild(node('small', count ? `${count} 条` : '待录入'));
                button.addEventListener('click', () => selectLesson(item));
                details.appendChild(button);
            }
            container.appendChild(details);
        }
        renderControls();
    }
    function renderEditor() {
        el('english-dictation-select-prompt').hidden = !!wordlist;
        el('english-dictation-editor').hidden = !wordlist;
        el('english-dictation-lesson-title').textContent = lesson ? `${lesson.number ? `${lesson.number} ` : ''}${lesson.title}` : '';
        el('english-dictation-words').value = raw;
        el('english-dictation-cloud-preview').hidden = true;
        el('english-dictation-cloud-preview').textContent = '';
        el('english-dictation-discard-confirm').hidden = true;
        renderWords();
        renderControls();
    }
    function renderMistakeControls() {
        const blocked = loading || saving || mistakeLoading || mistakeDeleting;
        const selected = mistakeItems.filter((item) => mistakeSelections.has(item.lesson_id));
        const count = mistakeRunWords().length;
        el('english-dictation-mistakes-refresh').disabled = blocked || !bookId;
        el('english-dictation-mistakes-select-all').disabled = blocked || !mistakeItems.length;
        el('english-dictation-mistakes-clear-selection').disabled = blocked || !selected.length;
        el('english-dictation-mistakes-start').disabled = blocked || !mistakeLoaded || !count;
        el('english-dictation-mistakes-selection').textContent = `已选 ${selected.length} 份清单，共 ${count} 条朗读内容（相同条目与读法合并）`;
        for (const input of el('english-dictation-mistakes-groups').querySelectorAll('input')) input.disabled = blocked;
        for (const button of el('english-dictation-mistakes-groups').querySelectorAll('button')) button.disabled = blocked;
    }
    function renderMistakes() {
        const container = el('english-dictation-mistakes-groups');
        for (const details of container.querySelectorAll('details')) {
            if (details.open) mistakeExpanded.add(details.dataset.lesson); else mistakeExpanded.delete(details.dataset.lesson);
        }
        container.replaceChildren();
        const total = mistakeItems.reduce((sum, item) => sum + item.words.length, 0);
        el('english-dictation-mistakes-count').textContent = mistakeLoaded ? `(${total})` : '';
        el('english-dictation-mistakes-empty').hidden = !mistakeLoaded || mistakeLoading || !!total;
        for (const item of mistakeItems) {
            const details = node('details', '', 'english-dictation-mistake-group');
            const key = `${item.book_id}:${item.lesson_id}`;
            details.dataset.lesson = key;
            details.open = mistakeExpanded.has(key);
            const summary = node('summary');
            const label = node('label', '', 'english-dictation-mistake-lesson');
            const checkbox = node('input'); checkbox.type = 'checkbox'; checkbox.value = item.lesson_id;
            checkbox.checked = mistakeSelections.has(item.lesson_id);
            checkbox.setAttribute('aria-label', `选择${sourceName(item.lesson)}的错题加入本轮`);
            checkbox.addEventListener('click', (event) => { event.stopPropagation(); });
            checkbox.addEventListener('change', () => {
                if (mistakeLoading || mistakeDeleting || !current(capture())) return;
                if (checkbox.checked) mistakeSelections.add(item.lesson_id); else mistakeSelections.delete(item.lesson_id);
                renderMistakeControls();
            });
            label.append(checkbox, node('span', sourceName(item.lesson)));
            summary.append(label, node('small', `${item.words.length} 条`));
            details.appendChild(summary);
            const chips = node('div', '', 'english-dictation-mistake-words');
            for (const word of item.words) {
                const chip = node('div', '', 'english-dictation-word-chip');
                const text = node('span', word.text);
                if (word.spoken_text && word.spoken_text !== word.text) text.appendChild(node('small', `朗读：${word.spoken_text}`));
                const remove = node('button', '×', 'english-dictation-remove-mistake');
                remove.type = 'button';
                remove.setAttribute('aria-label', `从${sourceName(item.lesson)}移除 ${word.text}`);
                remove.setAttribute('title', `从错题本移除“${word.text}”`);
                remove.addEventListener('click', () => removeMistake(item, word));
                chip.append(text, remove); chips.appendChild(chip);
            }
            details.appendChild(chips); container.appendChild(details);
        }
        setMistakeStatus(mistakeStatus, mistakeStatusError);
        renderMistakeControls();
    }
    async function loadMistakes() {
        if (!bookId || mistakeLoading || mistakeDeleting || !current(capture())) return;
        const ctx = capture(), requestedBook = bookId, sequence = ++mistakeSequence;
        mistakeLoading = true;
        setMistakeStatus('正在读取错题本…'); renderMistakeControls(); renderControls();
        try {
            const result = await request(`/mistakes?book_id=${encodeURIComponent(requestedBook)}`);
            if (!current(ctx) || requestedBook !== bookId || sequence !== mistakeSequence) return;
            if (!Array.isArray(result.items) || result.items.some((item) => item.book_id !== requestedBook || !item.lesson?.id || !Array.isArray(item.words))) throw new Error('错题本读取结果不完整，请点击刷新重试。');
            mistakeItems = result.items.filter((item) => item.words.length).map((item) => ({ ...item, lesson: { ...item.lesson }, words: cloneWords(item.words) }));
            mistakeSelections = new Set(Array.from(mistakeSelections).filter((id) => mistakeItems.some((item) => item.lesson_id === id)));
            mistakeLoaded = true;
            setMistakeStatus(mistakeItems.length ? `共 ${mistakeItems.length} 份清单，${mistakeItems.reduce((sum, item) => sum + item.words.length, 0)} 条错题。展开清单可查看或移除条目。` : '');
        } catch (error) {
            if (current(ctx) && requestedBook === bookId && sequence === mistakeSequence) {
                mistakeLoaded = false;
                setMistakeStatus(error.message || '暂时无法读取错题本，请点击刷新重试。', true);
            }
        } finally {
            if (current(ctx) && requestedBook === bookId && sequence === mistakeSequence) {
                mistakeLoading = false; renderMistakes(); renderControls();
            }
        }
    }
    async function setMode(mode) {
        if (loading || saving || !current(capture())) return;
        preserveDraft(); stopSpeech(); homeMode = mode; renderMode();
        if (mode === 'mistakes') await loadMistakes();
    }
    async function removeMistake(item, word) {
        if (loading || saving || mistakeLoading || mistakeDeleting || !active || !current(capture()) || item.book_id !== bookId) return;
        const ctx = capture(), requestedBook = item.book_id;
        mistakeDeleting = true;
        setMistakeStatus(`正在从${sourceName(item.lesson)}移除“${word.text}”…`); renderControls();
        try {
            const result = await request(`/mistakes/${encodeURIComponent(item.book_id)}/${encodeURIComponent(item.lesson_id)}`, {
                method: 'DELETE', body: { words: [word.text] },
            });
            if (!current(ctx) || requestedBook !== bookId) return;
            if (!Number.isInteger(result.removed_count) || result.removed_count < 0) throw new Error('移除结果未确认，请再次点击该条目的 × 重试。');
            mistakeItems = mistakeItems.map((group) => group.lesson_id === item.lesson_id
                ? { ...group, words: group.words.filter((entry) => entry.text !== word.text) } : group).filter((group) => group.words.length);
            if (!mistakeItems.some((group) => group.lesson_id === item.lesson_id)) mistakeSelections.delete(item.lesson_id);
            if (run) for (const entry of run.words) for (const source of entry.sources || []) {
                if (source.book_id === item.book_id && source.lesson_id === item.lesson_id && entry.text === word.text) run.savedMistakes.delete(sourceKey(source, entry));
            }
            setMistakeStatus(`已从${sourceName(item.lesson)}移除“${word.text}”。原清单内容仍可继续默写。`);
        } catch (error) { if (current(ctx) && requestedBook === bookId) setMistakeStatus(`${error.message || '暂时未能移除。'} 请再点该条目旁的 × 重试。`, true); }
        finally { if (current(ctx) && requestedBook === bookId) { mistakeDeleting = false; renderMistakes(); renderControls(); } }
    }
    function playbackSettings() {
        return { repetitions: Number(el('english-dictation-repetitions').value) === 1 ? 1 : 2,
            shuffled: el('english-dictation-shuffle').checked, voiceURI: el('english-dictation-voice').value, voiceExplicit: voiceChosen };
    }
    function startMistakes() {
        if (loading || saving || mistakeLoading || mistakeDeleting || !mistakeLoaded || !active || !current(capture())) return;
        const words = mistakeRunWords();
        if (!words.length) { setMistakeStatus('先勾选至少一份有错题的清单，再开始复习。', true); return; }
        makeRun(words, { ...playbackSettings(), title: '错题本复习', kind: 'mistakes', book_id: bookId, lesson_id: null,
            number: null, lessonCount: mistakeItems.filter((item) => mistakeSelections.has(item.lesson_id)).length });
    }
    async function loadBook(nextBook) {
        preserveDraft();
        stopSpeech();
        bookId = nextBook;
        lesson = null;
        wordlist = null;
        raw = '';
        conflict = null;
        lessons = [];
        summaries = new Map();
        mistakeSequence += 1; mistakeItems = []; mistakeSelections = new Set();
        mistakeLoaded = false; mistakeLoading = false; mistakeDeleting = false; mistakeStatus = ''; mistakeStatusError = false;
        loading = true;
        el('english-dictation-book').value = bookId;
        const book = books.find((item) => item.id === bookId);
        el('english-dictation-edition').textContent = book ? [book.edition, book.catalog_note || book.source_note || book.description || ''].filter(Boolean).join(' · ') : '';
        renderDirectory(); renderEditor(); renderMistakes(); renderMode();
        const ctx = capture(true), sequence = ++requestSequence;
        try {
            const [directory, lists] = await Promise.all([
                request(`/lessons?book_id=${encodeURIComponent(bookId)}`),
                request(`/wordlists?book_id=${encodeURIComponent(bookId)}`),
            ]);
            if (!current(ctx) || sequence !== requestSequence) return;
            lessons = Array.isArray(directory.items) ? directory.items : [];
            for (const item of lessons.filter((entry) => entry.id === 'default-list')) expandedUnits.add(`${bookId}:${item.unit || '清单目录'}`);
            summaries = new Map((lists.items || []).map((item) => [item.lesson_id, item]));
            message(lessons.length ? '' : '这个文件夹暂时没有清单，可以添加自己的清单。');
        } catch (error) {
            if (current(ctx) && sequence === requestSequence) {
                loaded = false;
                message(error.message || '清单目录暂时未能读取，请重新打开生词默写。', true);
            }
        } finally {
            if (current(ctx) && sequence === requestSequence) { loading = false; renderDirectory(); renderControls(); }
        }
        if (current(ctx) && sequence === requestSequence && homeMode === 'mistakes') await loadMistakes();
        else if (current(ctx) && sequence === requestSequence && lessons.length) {
            await selectLesson(lessons.find((item) => item.id === 'default-list') || lessons[0]);
        }
    }
    async function selectLesson(item) {
        if (loading || saving || !signedIn() || !item) return;
        preserveDraft(); stopSpeech();
        lesson = item;
        wordlist = null; raw = ''; conflict = null;
        loading = true;
        message(); stateLabel('正在读取…'); renderEditor(); renderDirectory();
        const ctx = capture(true), sequence = ++lessonSequence;
        try {
            const data = await request(`/wordlists/${encodeURIComponent(bookId)}/${encodeURIComponent(item.id)}`);
            if (!current(ctx) || sequence !== lessonSequence) return;
            if (!data.wordlist || !Array.isArray(data.wordlist.words) || !Number.isSafeInteger(data.wordlist.revision)) throw new Error('清单内容读取结果不完整，请重试。');
            wordlist = data.wordlist;
            raw = formatWords(wordlist.words);
            summaries.set(item.id, wordlist);
            stateLabel(wordlist.revision ? '已保存' : '还没有条目');
            restoreDraft();
        } catch (error) {
            if (current(ctx) && sequence === lessonSequence) message(error.message || '清单内容暂时未能读取，请重新选择清单。', true);
        } finally {
            if (current(ctx) && sequence === lessonSequence) { loading = false; renderEditor(); renderDirectory(); }
        }
    }
    async function save() {
        if (!wordlist || loading || saving || conflict || !signedIn()) return false;
        let parsed;
        try { parsed = parseWords(raw); }
        catch (error) { preserveDraft(); message(error.message, true); return false; }
        if (unchanged(parsed.words)) {
            removeBackup(); stateLabel(wordlist.revision ? '已保存' : '还没有条目'); return true;
        }
        preserveDraft();
        const ctx = capture(true), sentRaw = raw, sentWords = cloneWords(parsed.words);
        saving = true; stateLabel('正在保存…'); renderControls();
        try {
            const result = await request(`/wordlists/${encodeURIComponent(bookId)}/${encodeURIComponent(lesson.id)}`, {
                method: 'PUT', body: { base_revision: wordlist.revision, words: sentWords },
            });
            if (!current(ctx)) return false;
            if (!result.wordlist || !Number.isSafeInteger(result.wordlist.revision) || !Array.isArray(result.wordlist.words)) throw new Error('保存结果未确认，请重试保存。');
            wordlist = result.wordlist;
            summaries.set(lesson.id, wordlist);
            // Controls are frozen while saving, but retain any unexpected new input defensively.
            if (raw === sentRaw) { raw = formatWords(wordlist.words); removeBackup(); }
            else preserveDraft();
            stateLabel(raw === formatWords(wordlist.words) ? '已保存' : '本机有修改，待保存');
            message(parsed.duplicates ? `清单内容已保存，重复条目已合并，共 ${wordlist.words.length} 个。` : '清单内容已保存。');
            return true;
        } catch (error) {
            if (!current(ctx)) return false;
            if (error.status === 409) {
                conflict = { localRaw: raw, baseRevision: wordlist.revision, cloud: null };
                stateLabel('本机内容已保留，等待对照');
                message('另一页面已经更新这份清单内容。你的输入已留在本机，请先复制并对照云端清单内容。', true);
            } else {
                stateLabel('本机待保存');
                message(error.message || '暂时未能保存，请保留本页并重试。', true);
            }
            preserveDraft();
            return false;
        } finally {
            if (current(ctx)) { saving = false; renderEditor(); renderDirectory(); }
        }
    }
    async function inspectCloud() {
        if (!conflict || loading || saving) return null;
        const ctx = capture(true), original = conflict;
        loading = true; renderControls();
        try {
            const result = await request(`/wordlists/${encodeURIComponent(bookId)}/${encodeURIComponent(lesson.id)}`);
            if (!current(ctx) || conflict !== original) return null;
            if (!result.wordlist || !Array.isArray(result.wordlist.words) || !Number.isSafeInteger(result.wordlist.revision)) throw new Error('云端清单内容暂未读取完整，请重试。');
            conflict.cloud = result.wordlist;
            el('english-dictation-cloud-preview').textContent = formatWords(result.wordlist.words) || '（云端清单内容为空）';
            el('english-dictation-cloud-preview').hidden = false;
            return result.wordlist;
        } catch (error) { if (current(ctx)) message(error.message || '暂时无法读取云端清单内容，本机内容仍保留着。', true); return null; }
        finally { if (current(ctx)) { loading = false; renderControls(); } }
    }
    async function copyLocal() {
        if (!conflict) return;
        const original = conflict.localRaw, ctx = capture(true);
        try {
            await navigator.clipboard.writeText(original);
            if (current(ctx)) message('本机清单内容已复制，可以粘贴到笔记中保留。');
        } catch (_) {
            if (!current(ctx)) return;
            const preview = el('english-dictation-cloud-preview');
            preview.textContent = original;
            preview.hidden = false;
            message('浏览器未允许自动复制。本机清单内容已显示在下方，请选中文字复制。', true);
        }
    }
    async function acceptCloud() {
        if (!conflict || loading || saving) return;
        const ctx = capture(true), original = conflict;
        // Re-read immediately before accepting so the editor receives the latest revision.
        const latest = await inspectCloud();
        if (!latest || !current(ctx) || conflict !== original) return;
        wordlist = latest;
        summaries.set(lesson.id, wordlist);
        raw = formatWords(wordlist.words);
        conflict = null; removeBackup();
        stateLabel('已载入云端清单内容'); message('已使用云端清单内容，可以继续编辑。');
        renderEditor(); renderDirectory();
    }
    async function addLesson() {
        if (loading || saving || !bookId || !signedIn()) return;
        const title = normalized(el('english-dictation-custom-title').value);
        const numberRaw = el('english-dictation-custom-number').value.trim();
        const number = numberRaw ? Number(numberRaw) : null;
        if (!title || Array.from(title).length > 80) { message('请填写 1～80 字的清单名称。', true); return; }
        if (numberRaw && (!Number.isInteger(number) || number < 1 || number > 999)) { message('序号请填写 1～999 的整数，也可以留空。', true); return; }
        preserveDraft();
        const ctx = capture(true);
        loading = true; renderControls(); message();
        let created = null;
        try {
            const result = await request('/lessons', { method: 'POST', body: { book_id: bookId, title, number } });
            if (!current(ctx)) return;
            if (!result.lesson?.id) throw new Error('清单添加结果未确认，请重试。');
            created = result.lesson;
            if (!lessons.some((item) => item.id === created.id)) lessons.push(created);
            el('english-dictation-custom-title').value = '';
            el('english-dictation-custom-number').value = '';
            expandedUnits.add(`${bookId}:我的其他清单`);
        } catch (error) { if (current(ctx)) message(error.message || '暂时未能添加清单，请重试。', true); }
        finally { if (current(ctx)) { loading = false; renderDirectory(); } }
        if (created && current(ctx)) await selectLesson(created);
    }

    function englishVoice(voice) { return /^en(?:-|$)/iu.test(String(voice.lang || '').replace(/_/g, '-')); }
    function voiceRank(voice) {
        const name = voice.name || '';
        const natural = /samantha|\balex\b|\bdaniel\b|google.*english|microsoft|natural|enhanced|premium/iu.test(name);
        const novelty = /\b(albert|bahh|bells|boing|bubbles|cellos|hysterical|jester|junior|organ|trinoids|whisper|zarvox)\b|bad news|good news/iu.test(name);
        return (natural ? 250 : 0) - (novelty ? 500 : 0)
            + (/^en-us$/iu.test(voice.lang || '') ? 100 : 0) + (voice.localService ? 10 : 0) + (voice.default ? 1 : 0);
    }
    function detectVoices() {
        const selected = el('english-dictation-voice').value;
        try { voices = (window.speechSynthesis?.getVoices() || []).filter(englishVoice).sort((a, b) => voiceRank(b) - voiceRank(a)); }
        catch (_) { voices = []; }
        const select = el('english-dictation-voice'); select.replaceChildren();
        if (voices.length) {
            for (const voice of voices) {
                const option = node('option', `${voice.name}（${voice.lang}）`); option.value = voice.voiceURI || voice.name; select.appendChild(option);
            }
            select.value = voiceChosen && voices.some((voice) => (voice.voiceURI || voice.name) === selected) ? selected : voices[0].voiceURI || voices[0].name;
        } else {
            const option = node('option', '未检测到设备英语语音'); option.value = ''; select.appendChild(option); select.value = '';
        }
        select.disabled = !voices.length;
        el('english-dictation-voice-status').textContent = voices.length
            ? '优先使用英语朗读；需要时使用所选设备英语语音备用。始终按正常语速播放。'
            : '优先使用英语朗读。设备暂时没有英语备用语音，可在系统中添加 English 语音后重新检测。';
    }
    function selectedVoice(uri) { return voices.find((voice) => (voice.voiceURI || voice.name) === uri) || voices[0] || null; }
    function releaseAudio() {
        const audio = playerAudio; playerAudio = null;
        if (audio) { audio.onended = null; audio.onerror = null; try { audio.pause(); } catch (_) { /* Already stopped. */ } }
        if (audioUrl) { try { URL.revokeObjectURL(audioUrl); } catch (_) { /* Optional cleanup. */ } audioUrl = ''; }
    }
    function stopSpeech() {
        const interruptedPlayer = speaking && speechMode === 'player';
        speechEpoch += 1;
        clearTimeout(repeatTimer); clearTimeout(watchdog); clearTimeout(fetchTimer);
        repeatTimer = null; watchdog = null; fetchTimer = null;
        if (audioFetch) { audioFetch.abort(); audioFetch = null; }
        if (utterance) { utterance.onend = null; utterance.onerror = null; }
        utterance = null; releaseAudio(); speaking = false; speechMode = '';
        if (interruptedPlayer && run) { run.read = false; run.status = '播放已暂停，点播放重听本项'; }
        try { window.speechSynthesis?.cancel(); } catch (_) { /* No speech queue. */ }
    }
    function cancelRequests() { for (const controller of requests) controller.abort(); requests.clear(); }
    function renderPlayer() {
        if (!run) return;
        el('english-dictation-player-title').textContent = run.kind === 'mistakes' ? '英语错题复习' : '英语纸笔默写';
        el('english-dictation-position').textContent = `第 ${run.index + 1} 条 / 共 ${run.words.length} 条`;
        el('english-dictation-progress').max = run.words.length;
        el('english-dictation-progress').value = run.index;
        el('english-dictation-play-status').textContent = run.status || (run.read ? '写好后，继续下一条' : '准备好后，点播放');
        el('english-dictation-player-settings').textContent = `正常语速 · 每条 ${run.repetitions} 遍${run.shuffled ? ' · 已打乱顺序' : ''}`;
        el('english-dictation-play').textContent = run.read ? '再听一遍' : '播放本项';
        el('english-dictation-play').disabled = speaking;
        el('english-dictation-next').disabled = speaking || !run.read;
        el('english-dictation-next').textContent = run.index === run.words.length - 1 ? '完成并对照' : '下一条';
    }
    function speakWords(word, repetitions, mode) {
        if (!visible() || !current(capture()) || (mode === 'player' && (!run || run.completed || el('english-dictation-player').hidden))) return;
        if (speaking && mode === 'player') return;
        stopSpeech(); portal()?.stopOtherSpeech?.();
        speaking = true; speechMode = mode;
        const epoch = speechEpoch, ctx = capture(), running = run;
        const text = word.spoken_text || word.text;
        let completed = 0, fallbackStarted = false;
        if (mode === 'player') { run.read = false; run.status = '正在准备朗读…'; renderPlayer(); }
        else message('正在准备试听…');
        const valid = () => epoch === speechEpoch && current(ctx) && visible()
            && (mode !== 'player' || run === running && !run.completed && !el('english-dictation-player').hidden);
        function status(text) { if (mode === 'player' && valid()) { run.status = text; renderPlayer(); } }
        function fail(detail = '') {
            if (!valid()) return;
            stopSpeech();
            if (mode === 'player' && run === running) { run.read = false; run.status = '这次未能读完，请点播放重试'; renderPlayer(); }
            message(detail || '英语朗读没有正常读完，当前位置已保留。请检查设备声音后点播放重试。', true);
        }
        function done() {
            if (!valid()) return;
            clearTimeout(repeatTimer); clearTimeout(watchdog); clearTimeout(fetchTimer);
            repeatTimer = null; watchdog = null; fetchTimer = null;
            if (utterance) { utterance.onend = null; utterance.onerror = null; }
            utterance = null; releaseAudio(); speaking = false; speechMode = ''; speechEpoch += 1;
            if (mode === 'player') { run.read = true; run.heard.add(run.index); run.status = '读完了，写好后继续'; renderPlayer(); }
            else message('试听完成，确认后就可以开始默写。');
        }
        function readingEnded(repeat) {
            if (!valid()) return;
            clearTimeout(watchdog); watchdog = null; completed += 1;
            if (completed < repetitions) {
                status('稍后再读一遍…');
                repeatTimer = setTimeout(() => { repeatTimer = null; if (valid()) repeat(); }, 700);
            } else done();
        }
        function fallback() {
            if (!valid() || fallbackStarted) return;
            fallbackStarted = true; completed = 0; cachedAudio = null;
            clearTimeout(repeatTimer); clearTimeout(watchdog); repeatTimer = null; watchdog = null; releaseAudio();
            const chosen = selectedVoice(mode === 'player' ? run.voiceExplicit ? run.voiceURI : '' : voiceChosen ? el('english-dictation-voice').value : '');
            if (!window.speechSynthesis || !window.SpeechSynthesisUtterance || !chosen) {
                fail('英语朗读暂时不可用，设备也没有英语备用语音。当前位置已保留，请稍后重试或添加 English 语音。'); return;
            }
            message('正在使用设备英语语音，按正常语速朗读。');
            function speakOne() {
                if (!valid()) return;
                const spoken = new window.SpeechSynthesisUtterance(text); utterance = spoken;
                spoken.voice = chosen; spoken.lang = chosen.lang || 'en-US'; spoken.rate = 1; spoken.pitch = 1; spoken.volume = 1;
                spoken.onend = () => {
                    if (!valid() || utterance !== spoken) return;
                    spoken.onend = null; spoken.onerror = null; utterance = null; readingEnded(speakOne);
                };
                spoken.onerror = () => { if (valid() && utterance === spoken) fail(); };
                watchdog = setTimeout(() => { if (utterance === spoken) fail(); }, 120000);
                status('正在朗读，请听清…');
                try { window.speechSynthesis.resume?.(); window.speechSynthesis.speak(spoken); } catch (_) { fail(); }
            }
            speakOne();
        }
        function playBlob(blob) {
            if (!valid()) return;
            if (!window.Audio || !URL.createObjectURL) { fallback(); return; }
            let audio, audioPass = 0;
            try {
                audioUrl = URL.createObjectURL(blob); audio = new window.Audio(); playerAudio = audio;
                audio.src = audioUrl; audio.preload = 'auto'; audio.playbackRate = 1; audio.defaultPlaybackRate = 1;
            } catch (_) { fallback(); return; }
            function playOne() {
                if (!valid() || playerAudio !== audio) return;
                const pass = ++audioPass;
                try {
                    audio.currentTime = 0;
                    audio.onended = () => {
                        if (!valid() || playerAudio !== audio || audioPass !== pass) return;
                        audioPass += 1; audio.onended = null; audio.onerror = null; readingEnded(playOne);
                    };
                    audio.onerror = () => { if (valid() && playerAudio === audio && audioPass === pass) fallback(); };
                    watchdog = setTimeout(() => { if (playerAudio === audio && audioPass === pass) fallback(); }, 120000);
                    status('正在朗读，请听清…');
                    const playing = audio.play();
                    if (playing?.catch) playing.catch(() => { if (valid() && playerAudio === audio) fallback(); });
                } catch (_) { fallback(); }
            }
            playOne();
        }
        async function fetchAudio() {
            if (cachedAudio?.student === ctx.student && cachedAudio.token === ctx.token && cachedAudio.text === text) { playBlob(cachedAudio.blob); return; }
            const controller = new AbortController(); audioFetch = controller;
            fetchTimer = setTimeout(() => controller.abort(), 30000);
            try {
                const response = await fetch('/api/subjects/english/dictation/audio', {
                    method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'audio/wav', Authorization: `Bearer ${ctx.token}` },
                    body: JSON.stringify({ text }), signal: controller.signal, credentials: 'same-origin', cache: 'no-store',
                });
                if (!valid()) return;
                if (response.status === 401 || response.status === 403) { fail('登录状态已失效，请重新登录后继续默写。'); return; }
                if (!response.ok) throw new Error('Audio unavailable');
                const type = response.headers?.get?.('Content-Type') || '';
                if (type && !/^audio\/(wav|x-wav|wave|vnd\.wave)(;|$)/iu.test(type)) throw new Error('Invalid audio response');
                const blob = await response.blob();
                if (!valid()) return;
                if (!blob || !blob.size) throw new Error('Empty audio');
                cachedAudio = { student: ctx.student, token: ctx.token, text, blob };
                clearTimeout(fetchTimer); fetchTimer = null;
                if (audioFetch === controller) audioFetch = null;
                playBlob(blob);
            } catch (_) { if (valid()) fallback(); }
            finally {
                if (epoch === speechEpoch) { clearTimeout(fetchTimer); fetchTimer = null; }
                if (audioFetch === controller) audioFetch = null;
            }
        }
        void fetchAudio();
    }
    function makeRun(words, settings) {
        stopSpeech();
        let snapshot = cloneWords(words);
        if (settings.shuffled) {
            for (let i = snapshot.length - 1; i > 0; i -= 1) {
                const j = Math.floor(Math.random() * (i + 1));
                [snapshot[i], snapshot[j]] = [snapshot[j], snapshot[i]];
            }
        }
        run = { ...settings, words: snapshot, index: 0, read: false, heard: new Set(), checked: new Set(),
            savedMistakes: new Set(settings.savedMistakes || []), mistakeSaveBusy: false, saveError: false, saveMessage: '',
            completed: false, status: '准备好后，点播放' };
        message(); showView('player'); renderPlayer();
    }
    async function start() {
        if (loading || saving || !wordlist || conflict || !visible()) return;
        const ctx = capture(true), startingView = viewSequence;
        if (!await save() || !current(ctx) || startingView !== viewSequence || !visible()) return;
        const source = { book_id: bookId, lesson_id: lesson.id, title: lesson.title, number: lesson.number };
        const words = cloneWords(wordlist.words).map((word) => ({ ...word, sources: cloneSources([source]) }));
        if (!words.length) { message('先录入至少一条，再开始默写。', true); return; }
        makeRun(words, { ...playbackSettings(), number: lesson.number, title: lesson.title, kind: 'lesson',
            book_id: source.book_id, lesson_id: source.lesson_id });
    }
    function playCurrent() {
        if (run && !run.completed && !speaking) speakWords(run.words[run.index], run.repetitions, 'player');
    }
    function next() {
        if (!run || run.completed || speaking || !run.read || !active || !current(capture())) return;
        if (run.index === run.words.length - 1) { finish(false); return; }
        run.index += 1; run.read = false; run.status = '正在准备下一项…';
        renderPlayer(); playCurrent();
    }
    function finish(early) {
        if (!run || !current(capture())) return;
        stopSpeech(); run.completed = true; run.early = !!early;
        renderFinish(); message(); showView('finish');
    }
    function selectedReviewWords() {
        return run ? run.words.filter((_, index) => run.checked.has(index) && run.heard.has(index)) : [];
    }
    function pendingMistakeGroups() {
        const groups = new Map();
        for (const word of selectedReviewWords()) for (const source of word.sources || []) {
            if (run.savedMistakes.has(sourceKey(source, word))) continue;
            const key = JSON.stringify([source.book_id, source.lesson_id]);
            if (!groups.has(key)) groups.set(key, { book_id: source.book_id, lesson_id: source.lesson_id, words: new Map() });
            groups.get(key).words.set(word.text, { text: word.text, spoken_text: word.spoken_text || word.text });
        }
        return Array.from(groups.values()).map((group) => ({ ...group, words: Array.from(group.words.values()) }));
    }
    function renderFinishControls() {
        if (!run) return;
        const selected = selectedReviewWords(), pending = pendingMistakeGroups();
        const busy = run.mistakeSaveBusy;
        el('english-dictation-save-mistakes').disabled = busy || !selected.length || !pending.length;
        el('english-dictation-save-mistakes').textContent = busy ? '正在加入…' : selected.length && !pending.length ? '已加入错题本'
            : run.saveError ? '重试加入错题本' : '加入错题本';
        el('english-dictation-mistakes-save-state').textContent = run.saveMessage || (!selected.length ? '尚未选择错题'
            : pending.length ? `已勾选 ${selected.length} 条，点击“加入错题本”才会保存。` : `所选 ${selected.length} 条已加入错题本。`);
        el('english-dictation-mistakes-save-state').classList.toggle('english-dictation-status-error', run.saveError);
        el('english-dictation-retry-selected').disabled = busy || !selected.length;
        el('english-dictation-retry-all').disabled = busy;
        el('english-dictation-finish-back').disabled = busy;
        for (const input of el('english-dictation-review-list').querySelectorAll('input')) input.disabled = busy || !run.heard.has(Number(input.value));
    }
    function renderFinish() {
        if (!run) return;
        el('english-dictation-finish-label').textContent = `${run.title}${run.kind === 'mistakes' ? ` · ${run.lessonCount} 份清单` : ''} · ${run.words.length} 条${run.early ? '，本轮已提前结束；未听完的条目仅供对照，不能标错' : ''}。`;
        const list = el('english-dictation-review-list'); list.replaceChildren();
        for (const [index, word] of run.words.entries()) {
            const label = node('label', '', 'english-dictation-review-word');
            const checkbox = node('input'); checkbox.type = 'checkbox'; checkbox.value = String(index);
            checkbox.checked = run.checked.has(index);
            checkbox.setAttribute('aria-label', `将“${word.text}”标为本次错题`);
            const heard = run.heard.has(index);
            checkbox.disabled = !heard || run.mistakeSaveBusy;
            label.classList.toggle('english-dictation-word-unheard', !heard);
            checkbox.addEventListener('change', () => {
                if (!run || run.mistakeSaveBusy || !run.heard.has(index) || !current(capture())) return;
                if (checkbox.checked) run.checked.add(index); else run.checked.delete(index);
                run.saveMessage = ''; run.saveError = false; renderFinishControls();
            });
            const text = node('span', word.text);
            text.appendChild(node('small', (word.sources || []).map(sourceName).join('、'), 'english-dictation-review-origin'));
            if (!heard) text.appendChild(node('small', '未听完，不能标错', 'english-dictation-review-origin'));
            label.append(checkbox, text); list.appendChild(label);
        }
        renderFinishControls();
    }
    async function saveMistakes() {
        if (!run?.completed || run.mistakeSaveBusy || !active || !current(capture())) return;
        const selected = selectedReviewWords(), items = pendingMistakeGroups();
        if (!selected.length || !items.length) return;
        const total = items.reduce((sum, item) => sum + item.words.length, 0);
        if (total > 2000 || items.length > 100 || items.some((item) => item.words.length > 1000)) {
            run.saveError = true; run.saveMessage = '一次最多保存 2000 条清单错题、100 份清单，每份清单最多 1000 条。勾选已保留，请减少本次选择后分批保存。'; renderFinishControls(); return;
        }
        if (selected.some((word) => !word.sources?.length)) {
            run.saveError = true; run.saveMessage = '部分条目缺少清单来源，尚未保存。请返回对应清单重新默写。'; renderFinishControls(); return;
        }
        const ctx = capture(), target = run;
        const sentKeys = selected.flatMap((word) => (word.sources || []).map((source) => sourceKey(source, word)));
        run.mistakeSaveBusy = true; run.saveError = false; run.saveMessage = '正在保存错题，请稍候…'; renderFinishControls();
        try {
            const result = await request('/mistakes', { method: 'POST', body: { items } });
            if (!current(ctx) || run !== target) return;
            if (!Number.isInteger(result.added_count) || !Number.isInteger(result.existing_count)
                    || result.added_count < 0 || result.existing_count < 0 || result.added_count + result.existing_count !== total) throw new Error('保存结果未确认，请重试。重复提交不会重复收录。');
            sentKeys.forEach((key) => target.savedMistakes.add(key));
            target.saveMessage = `已加入错题本：新增 ${result.added_count} 条，已有 ${result.existing_count} 条。`;
            target.saveError = false;
            if (items.some((item) => item.book_id === bookId)) mistakeLoaded = false;
        } catch (error) {
            if (!current(ctx) || run !== target) return;
            target.saveError = true; target.saveMessage = `${error.message || '暂时未能保存。'} 勾选已保留，请点击“重试加入错题本”。`;
        } finally { if (current(ctx) && run === target) { target.mistakeSaveBusy = false; renderFinishControls(); } }
    }
    function retry(all) {
        if (!run?.completed || run.mistakeSaveBusy || !active || !current(capture())) return;
        const words = all ? run.words : selectedReviewWords();
        if (!words.length) return;
        makeRun(words, { number: run.number, title: run.title, repetitions: run.repetitions, shuffled: run.shuffled, voiceURI: run.voiceURI,
            voiceExplicit: run.voiceExplicit, kind: run.kind, book_id: run.book_id, lesson_id: run.lesson_id, lessonCount: run.lessonCount, savedMistakes: run.savedMistakes });
    }
    function backHome() { stopSpeech(); showView('home'); renderControls(); }
    function leaveView() { preserveDraft(); viewSequence += 1; active = false; stopSpeech(); cancelRequests(); showView('home'); }
    function reset() {
        preserveDraft(); active = false; stopSpeech(); cancelRequests(); cachedAudio = null;
        generation += 1; requestSequence += 1; lessonSequence += 1; viewSequence += 1;
        boundStudent = ''; boundToken = ''; boundHostGeneration = undefined; loaded = false; loading = false; saving = false;
        books = []; lessons = []; summaries = new Map(); expandedUnits = new Set();
        bookId = ''; lesson = null; wordlist = null; raw = ''; conflict = null; run = null; voiceChosen = false;
        mistakeSequence += 1; homeMode = 'lessons'; mistakeItems = []; mistakeSelections = new Set(); mistakeExpanded = new Set();
        mistakeLoading = false; mistakeLoaded = false; mistakeDeleting = false; mistakeStatus = ''; mistakeStatusError = false;
        el('english-dictation-book').replaceChildren(); el('english-dictation-lessons').replaceChildren();
        el('english-dictation-edition').textContent = ''; el('english-dictation-review-list').replaceChildren();
        el('english-dictation-custom-title').value = ''; el('english-dictation-custom-number').value = '';
        el('english-dictation-finish-label').textContent = ''; el('english-dictation-player-title').textContent = '';
        el('english-dictation-mistakes-save-state').textContent = '尚未选择错题';
        el('english-dictation-mistakes-save-state').classList.remove('english-dictation-status-error');
        el('english-dictation-save-mistakes').textContent = '加入错题本'; el('english-dictation-save-mistakes').disabled = true;
        el('english-dictation-repetitions').value = '2'; el('english-dictation-shuffle').checked = false;
        suppressSpaceUp = false; message(); stateLabel(''); showView('home'); renderMode(); renderMistakes(); renderEditor();
    }
    async function open() {
        if (!signedIn()) { reset(); return; }
        if (boundStudent !== learner() || boundToken !== session().token || boundHostGeneration !== session().generation) {
            reset(); boundStudent = learner(); boundToken = session().token; boundHostGeneration = session().generation;
        }
        stopSpeech(); viewSequence += 1; active = true; showView('home'); renderMode(); detectVoices();
        if (loaded || loading) { if (loaded && homeMode === 'mistakes') await loadMistakes(); return; }
        const ctx = capture(), sequence = ++requestSequence;
        loading = true; renderControls();
        try {
            const result = await request('/books');
            if (!current(ctx) || sequence !== requestSequence) return;
            books = Array.isArray(result.items) ? result.items : [];
            el('english-dictation-book').replaceChildren();
            for (const book of books) {
                const option = node('option', book.title); option.value = book.id; el('english-dictation-book').appendChild(option);
            }
            loaded = true; loading = false;
            if (books.length) await loadBook(books[0].id);
            else message('文件夹目录暂未准备好，请稍后再来。');
        } catch (error) { if (current(ctx)) message(error.message || '文件夹暂未能读取，请重新打开生词默写。', true); }
        finally { if (current(ctx)) { loading = false; renderControls(); } }
    }

    el('english-dictation-book').addEventListener('change', () => { if (!loading && !saving && !mistakeLoading && !mistakeDeleting) return loadBook(el('english-dictation-book').value); });
    el('english-dictation-mode-lessons').addEventListener('click', () => setMode('lessons'));
    el('english-dictation-mode-mistakes').addEventListener('click', () => setMode('mistakes'));
    el('english-dictation-mistakes-refresh').addEventListener('click', loadMistakes);
    el('english-dictation-mistakes-select-all').addEventListener('click', () => {
        if (loading || saving || mistakeLoading || mistakeDeleting || !current(capture())) return;
        mistakeSelections = new Set(mistakeItems.map((item) => item.lesson_id)); renderMistakes();
    });
    el('english-dictation-mistakes-clear-selection').addEventListener('click', () => {
        if (loading || saving || mistakeLoading || mistakeDeleting || !current(capture())) return;
        mistakeSelections = new Set(); renderMistakes();
    });
    el('english-dictation-mistakes-start').addEventListener('click', startMistakes);
    el('english-dictation-add-lesson').addEventListener('click', addLesson);
    el('english-dictation-words').addEventListener('input', () => {
        if (loading || saving || conflict || !wordlist || !current(capture(true))) return;
        raw = el('english-dictation-words').value; const backedUp = preserveDraft(); renderWords();
        try { stateLabel(backedUp === false ? '本机空间不足，请及时保存到云端' : unchanged(parseWords(raw).words) ? '已保存' : '本机有修改，待保存'); }
        catch (_) { stateLabel(backedUp === false ? '本机空间不足，检查输入后请及时保存' : '本机内容已保留，请检查输入'); }
    });
    el('english-dictation-save').addEventListener('click', save);
    el('english-dictation-start').addEventListener('click', start);
    el('english-dictation-copy-local').addEventListener('click', copyLocal);
    el('english-dictation-load-cloud').addEventListener('click', inspectCloud);
    el('english-dictation-keep-cloud').addEventListener('click', () => { if (conflict) el('english-dictation-discard-confirm').hidden = false; });
    el('english-dictation-confirm-cloud').addEventListener('click', acceptCloud);
    el('english-dictation-voice').addEventListener('change', () => { voiceChosen = true; });
    el('english-dictation-refresh-voices').addEventListener('click', () => { detectVoices(); message(voices.length ? '设备英语备用语音已就绪，可以试听或开始默写。' : '设备暂时没有英语备用语音，仍可尝试正常英语朗读。'); });
    el('english-dictation-play').addEventListener('click', playCurrent);
    el('english-dictation-next').addEventListener('click', next);
    el('english-dictation-player-exit').addEventListener('click', backHome);
    el('english-dictation-resume').addEventListener('click', () => {
        if (!run || !current(capture())) return;
        message();
        if (run.completed) { renderFinish(); showView('finish'); }
        else { showView('player'); renderPlayer(); }
    });
    el('english-dictation-end-run').addEventListener('click', () => finish(true));
    el('english-dictation-retry-selected').addEventListener('click', () => retry(false));
    el('english-dictation-retry-all').addEventListener('click', () => retry(true));
    el('english-dictation-save-mistakes').addEventListener('click', saveMistakes);
    el('english-dictation-finish-back').addEventListener('click', () => { if (!run?.mistakeSaveBusy) backHome(); });
    function spaceTarget(event) {
        if (!visible() || !run || run.completed || el('english-dictation-panel').hidden || el('english-dictation-player').hidden || document.hidden || !current(capture())) return false;
        if (!(event.code === 'Space' || event.key === ' ' || event.key === 'Spacebar') || event.isComposing || event.keyCode === 229
                || event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return false;
        const target = event.target;
        if (target?.isContentEditable || target?.closest?.('input, textarea, select, [contenteditable="true"]')) return false;
        const button = target?.closest?.('button, a, summary');
        return !button || button.id === 'english-dictation-play' || button.id === 'english-dictation-next';
    }
    document.addEventListener('keydown', (event) => {
        if (!spaceTarget(event)) return;
        event.preventDefault(); event.stopPropagation(); suppressSpaceUp = true;
        if (event.repeat || speaking) return;
        if (run.read) next(); else playCurrent();
    }, true);
    document.addEventListener('keyup', (event) => {
        if (!(event.code === 'Space' || event.key === ' ' || event.key === 'Spacebar') || !suppressSpaceUp) return;
        event.preventDefault(); event.stopPropagation(); suppressSpaceUp = false;
    }, true);
    window.addEventListener('pagehide', leaveView);
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) { preserveDraft(); stopSpeech(); if (run && !run.completed) renderPlayer(); }
    });
    if (window.speechSynthesis?.addEventListener) window.speechSynthesis.addEventListener('voiceschanged', detectVoices);
    else if (window.speechSynthesis) window.speechSynthesis.onvoiceschanged = detectVoices;
    detectVoices();
    window.englishDictation = { open, leaveView, reset };
})();
