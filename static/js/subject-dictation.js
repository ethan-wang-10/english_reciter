(function () {
    'use strict';

    const el = (id) => document.getElementById(id);
    if (!el('dictation-panel')) return;
    const PREFIX = '/subjects/chinese/dictation';
    const portal = () => window.subjectPortal;
    const session = () => portal()?.getSession() || {};
    const learner = () => {
        const account = session();
        return account.account?.is_parent ? account.account.child_username : account.account?.username || account.username;
    };
    const request = (path, options) => portal().apiRequest(PREFIX + path, options);
    const cloneWords = (words) => words.map((word) => ({ text: word.text, spoken_text: word.spoken_text || word.text }));
    let generation = 0, requestSequence = 0, lessonSequence = 0;
    let boundStudent = '', boundToken = '', active = false, loaded = false, loading = false, saving = false;
    let books = [], lessons = [], summaries = new Map(), expandedUnits = new Set();
    let bookId = '', lesson = null, wordlist = null, raw = '', conflict = null;
    let run = null, voices = [], speechEpoch = 0, speaking = false, speechMode = '', utterance = null;
    let repeatTimer = null, watchdog = null, suppressSpaceUp = false;

    function node(tag, text = '', className = '') {
        const result = document.createElement(tag);
        if (text) result.textContent = text;
        if (className) result.className = className;
        return result;
    }
    function message(text = '', error = false) {
        const target = el('dictation-message');
        target.textContent = text;
        target.hidden = !text;
        target.classList.toggle('feedback-error', error);
        target.classList.toggle('feedback-status', !error);
    }
    function stateLabel(text) { el('dictation-save-state').textContent = text; }
    function capture(scoped = false) {
        return { generation, token: boundToken, student: boundStudent,
            book: scoped ? bookId : null, lesson: scoped ? lesson?.id : null };
    }
    function current(ctx) {
        return ctx.generation === generation && ctx.token === boundToken && ctx.student === boundStudent
            && ctx.token === session().token && ctx.student === learner() && session().subject === 'chinese'
            && (ctx.book === null || ctx.book === bookId) && (ctx.lesson === null || ctx.lesson === lesson?.id);
    }
    function signedIn() {
        return !!session().token && !!learner() && session().subject === 'chinese';
    }
    function backupKey(student = boundStudent, book = bookId, id = lesson?.id) {
        return `itorange-dictation-draft:${student}:${book}:${id}`;
    }
    function normalized(text) { return String(text).normalize('NFC').trim().replace(/\s+/gu, ' '); }
    function formatWords(words) {
        return words.map((word) => word.spoken_text && word.spoken_text !== word.text
            ? `${word.text}｜${word.spoken_text}` : word.text).join('\n');
    }
    function parseWords(value) {
        if (value.length > 25000) throw new Error('词表内容过长，请控制在 25000 个字符以内。');
        const entries = [], seen = new Set();
        const aliases = new Map((wordlist?.words || []).map((word) => [word.text, word.spoken_text || word.text]));
        let duplicates = 0;
        function add(text, spoken, explicit = false) {
            text = normalized(text);
            spoken = normalized(spoken || '');
            if (!text && !explicit) return;
            if (!text || Array.from(text).length > 40) throw new Error('每个词语须为 1～40 个字符，请检查输入。');
            if (/[\p{Cc}\p{Cf}\p{Cs}]/u.test(text + spoken)) throw new Error('词语中不能包含控制字符或不可见字符。');
            if (!/[\p{L}\p{N}]/u.test(text)) throw new Error('词语中至少需要一个汉字、字母或数字。');
            if (Array.from(spoken).length > 100) throw new Error('每个朗读提示最多 100 个字符，请缩短提示。');
            if (seen.has(text)) { duplicates += 1; return; }
            seen.add(text);
            entries.push({ text, spoken_text: spoken || (explicit ? text : aliases.get(text) || text) });
        }
        for (const line of value.split(/\r?\n|\r/u)) {
            const separator = line.search(/[|｜]/u);
            if (separator >= 0) add(line.slice(0, separator), line.slice(separator + 1), true);
            else for (const item of line.split(/[\s,，、;；]+/u)) add(item);
        }
        if (entries.length > 200) throw new Error('每篇课文最多保存 200 个不同词语，请分课整理。');
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
            if (!conflict && unchanged(parseWords(raw).words)) { removeBackup(); return; }
        } catch (_) { /* Invalid input must remain recoverable too. */ }
        try {
            localStorage.setItem(backupKey(), JSON.stringify({ student: boundStudent, book_id: bookId,
                lesson_id: lesson.id, raw, base_revision: conflict?.baseRevision ?? wordlist.revision }));
        } catch (_) { stateLabel('本机空间不足，请及时保存词表'); }
    }
    function restoreDraft() {
        try {
            const draft = JSON.parse(localStorage.getItem(backupKey()) || 'null');
            if (!draft || draft.student !== boundStudent || draft.book_id !== bookId || draft.lesson_id !== lesson.id
                || typeof draft.raw !== 'string' || draft.raw.length > 25000 || !Number.isSafeInteger(draft.base_revision)
                || draft.base_revision < 0) return;
            if (draft.raw === formatWords(wordlist.words || [])) { removeBackup(); return; }
            raw = draft.raw;
            if (draft.base_revision !== wordlist.revision) {
                conflict = { localRaw: raw, baseRevision: draft.base_revision, cloud: wordlist };
                message('这课的云端词表已更新。本机未保存的内容已保留，请先复制并对照。', true);
                stateLabel('本机内容已保留，等待对照');
            } else {
                stateLabel('已恢复本机未保存词表');
                message('已恢复本机尚未保存的词语，确认后点击保存。');
            }
        } catch (_) { /* A corrupt optional backup must never replace the cloud list. */ }
    }
    function renderResume() {
        el('dictation-resume-banner').hidden = !run || run.completed;
        if (run && !run.completed) el('dictation-resume-label').textContent = `还有一轮默写未结束：第 ${run.index + 1} 个／共 ${run.words.length} 个。`;
    }
    function showView(name) {
        el('dictation-home').hidden = name !== 'home';
        el('dictation-player').hidden = name !== 'player';
        el('dictation-finish').hidden = name !== 'finish';
        if (name === 'home') renderResume();
    }
    function renderControls() {
        const blocked = loading || saving;
        el('dictation-book').disabled = blocked;
        el('dictation-add-lesson').disabled = blocked;
        el('dictation-custom-title').disabled = blocked;
        el('dictation-custom-number').disabled = blocked;
        for (const button of el('dictation-lessons').querySelectorAll('button')) button.disabled = blocked;
        el('dictation-words').disabled = blocked || !!conflict;
        el('dictation-save').disabled = blocked || !!conflict || !wordlist;
        el('dictation-start').disabled = blocked || !!conflict || !wordlist;
        el('dictation-conflict').hidden = !conflict;
        el('dictation-keep-cloud').disabled = saving || loading;
        el('dictation-confirm-cloud').disabled = saving || loading;
    }
    function renderWords() {
        const container = el('dictation-word-preview');
        container.replaceChildren();
        try {
            const result = parseWords(raw);
            el('dictation-word-count').textContent = `${result.words.length} 个不同词语${result.duplicates ? `，已合并 ${result.duplicates} 个重复项` : ''} · 每课最多 200 个`;
            for (const word of result.words) {
                const chip = node('div', '', 'dictation-word-chip');
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
        } catch (error) { el('dictation-word-count').textContent = error.message; }
    }
    function renderDirectory() {
        const container = el('dictation-lessons');
        for (const details of container.querySelectorAll('details')) {
            const key = details.dataset.unit;
            if (details.open) expandedUnits.add(key); else expandedUnits.delete(key);
        }
        container.replaceChildren();
        const groups = new Map();
        for (const item of lessons) {
            const group = item.custom ? '我的补充课文' : item.unit || '课文目录';
            if (!groups.has(group)) groups.set(group, []);
            groups.get(group).push(item);
        }
        for (const [unit, items] of groups) {
            const details = node('details', '', 'dictation-unit');
            const unitKey = `${bookId}:${unit}`;
            details.dataset.unit = unitKey;
            details.open = expandedUnits.has(unitKey);
            const summary = node('summary', unit);
            summary.appendChild(node('span', `${items.length} 篇`));
            details.appendChild(summary);
            for (const item of items) {
                const button = node('button', '', 'dictation-lesson');
                button.type = 'button';
                const title = `${item.number ? `${item.number} ` : ''}${item.title}`;
                button.setAttribute('aria-label', item.number ? `第${item.number}课 ${item.title}` : item.title);
                button.setAttribute('aria-pressed', String(lesson?.id === item.id));
                button.appendChild(node('span', title));
                const count = summaries.get(item.id)?.word_count || 0;
                button.appendChild(node('small', count ? `${count} 词` : '待录入'));
                button.addEventListener('click', () => selectLesson(item));
                details.appendChild(button);
            }
            container.appendChild(details);
        }
        renderControls();
    }
    function renderEditor() {
        el('dictation-select-prompt').hidden = !!wordlist;
        el('dictation-editor').hidden = !wordlist;
        el('dictation-lesson-title').textContent = lesson ? `${lesson.number ? `${lesson.number} ` : ''}${lesson.title}` : '';
        el('dictation-words').value = raw;
        el('dictation-cloud-preview').hidden = true;
        el('dictation-cloud-preview').textContent = '';
        el('dictation-discard-confirm').hidden = true;
        renderWords();
        renderControls();
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
        loading = true;
        el('dictation-book').value = bookId;
        const book = books.find((item) => item.id === bookId);
        el('dictation-edition').textContent = book ? [book.edition, book.catalog_note || book.source_note || book.description || ''].filter(Boolean).join(' · ') : '';
        renderDirectory(); renderEditor();
        const ctx = capture(true), sequence = ++requestSequence;
        try {
            const [directory, lists] = await Promise.all([
                request(`/lessons?book_id=${encodeURIComponent(bookId)}`),
                request(`/wordlists?book_id=${encodeURIComponent(bookId)}`),
            ]);
            if (!current(ctx) || sequence !== requestSequence) return;
            lessons = Array.isArray(directory.items) ? directory.items : [];
            summaries = new Map((lists.items || []).map((item) => [item.lesson_id, item]));
            message(lessons.length ? '' : '这本课本暂时没有目录，可以添加自己的课文。');
        } catch (error) {
            if (current(ctx) && sequence === requestSequence) {
                loaded = false;
                message(error.message || '课文目录暂时未能读取，请重新打开生词默写。', true);
            }
        } finally {
            if (current(ctx) && sequence === requestSequence) { loading = false; renderDirectory(); renderControls(); }
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
            if (!data.wordlist || !Array.isArray(data.wordlist.words) || !Number.isSafeInteger(data.wordlist.revision)) throw new Error('词表读取结果不完整，请重试。');
            wordlist = data.wordlist;
            raw = formatWords(wordlist.words);
            summaries.set(item.id, wordlist);
            stateLabel(wordlist.revision ? '已保存' : '还没有词语');
            restoreDraft();
        } catch (error) {
            if (current(ctx) && sequence === lessonSequence) message(error.message || '词表暂时未能读取，请重新选择课文。', true);
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
            removeBackup(); stateLabel(wordlist.revision ? '已保存' : '还没有词语'); return true;
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
            message(parsed.duplicates ? `词表已保存，重复词语已合并，共 ${wordlist.words.length} 个。` : '词表已保存。');
            return true;
        } catch (error) {
            if (!current(ctx)) return false;
            if (error.status === 409) {
                conflict = { localRaw: raw, baseRevision: wordlist.revision, cloud: null };
                stateLabel('本机内容已保留，等待对照');
                message('另一页面已经更新这课词表。你的输入已留在本机，请先复制并对照云端词表。', true);
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
            if (!result.wordlist || !Array.isArray(result.wordlist.words) || !Number.isSafeInteger(result.wordlist.revision)) throw new Error('云端词表暂未读取完整，请重试。');
            conflict.cloud = result.wordlist;
            el('dictation-cloud-preview').textContent = formatWords(result.wordlist.words) || '（云端词表为空）';
            el('dictation-cloud-preview').hidden = false;
            return result.wordlist;
        } catch (error) { if (current(ctx)) message(error.message || '暂时无法读取云端词表，本机内容仍保留着。', true); return null; }
        finally { if (current(ctx)) { loading = false; renderControls(); } }
    }
    async function copyLocal() {
        if (!conflict) return;
        const original = conflict.localRaw, ctx = capture(true);
        try {
            await navigator.clipboard.writeText(original);
            if (current(ctx)) message('本机词表已复制，可以粘贴到笔记中保留。');
        } catch (_) {
            if (!current(ctx)) return;
            const preview = el('dictation-cloud-preview');
            preview.textContent = original;
            preview.hidden = false;
            message('浏览器未允许自动复制。本机词表已显示在下方，请选中文字复制。', true);
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
        stateLabel('已载入云端词表'); message('已使用云端词表，可以继续编辑。');
        renderEditor(); renderDirectory();
    }
    async function addLesson() {
        if (loading || saving || !bookId || !signedIn()) return;
        const title = normalized(el('dictation-custom-title').value);
        const numberRaw = el('dictation-custom-number').value.trim();
        const number = numberRaw ? Number(numberRaw) : null;
        if (!title || Array.from(title).length > 80) { message('请填写 1～80 字的课文名称。', true); return; }
        if (numberRaw && (!Number.isInteger(number) || number < 1 || number > 999)) { message('课号请填写 1～999 的整数，也可以留空。', true); return; }
        preserveDraft();
        const ctx = capture(true);
        loading = true; renderControls(); message();
        let created = null;
        try {
            const result = await request('/lessons', { method: 'POST', body: { book_id: bookId, title, number } });
            if (!current(ctx)) return;
            if (!result.lesson?.id) throw new Error('课文添加结果未确认，请重试。');
            created = result.lesson;
            if (!lessons.some((item) => item.id === created.id)) lessons.push(created);
            el('dictation-custom-title').value = '';
            el('dictation-custom-number').value = '';
            expandedUnits.add(`${bookId}:我的补充课文`);
        } catch (error) { if (current(ctx)) message(error.message || '暂时未能添加课文，请重试。', true); }
        finally { if (current(ctx)) { loading = false; renderDirectory(); } }
        if (created && current(ctx)) await selectLesson(created);
    }

    function mandarinVoice(voice) {
        const lang = String(voice.lang || '').toLowerCase().replace(/_/g, '-');
        if (/^(yue|zh-(hk|mo))($|-)/u.test(lang) || /cantonese|粤语|廣東話|广东话/iu.test(voice.name || '')) return false;
        return /^cmn($|-)/u.test(lang) || /^zh($|-(cn|tw|sg|hans|hant)($|-))/u.test(lang);
    }
    function voiceRank(voice) {
        const lang = String(voice.lang || '').toLowerCase().replace(/_/g, '-');
        return (/^(zh-cn|cmn|zh-hans)/u.test(lang) ? 100 : 0) + (voice.localService ? 10 : 0) + (voice.default ? 1 : 0);
    }
    function detectVoices() {
        const selected = el('dictation-voice').value;
        try { voices = (window.speechSynthesis?.getVoices() || []).filter(mandarinVoice).sort((a, b) => voiceRank(b) - voiceRank(a)); }
        catch (_) { voices = []; }
        const select = el('dictation-voice');
        select.replaceChildren();
        if (voices.length) {
            for (const voice of voices) {
                const option = node('option', `${voice.name}（${voice.lang}）`);
                option.value = voice.voiceURI || voice.name;
                select.appendChild(option);
            }
            select.value = voices.some((voice) => (voice.voiceURI || voice.name) === selected) ? selected : voices[0].voiceURI || voices[0].name;
        } else {
            const option = node('option', '暂未检测到普通话语音'); option.value = ''; select.appendChild(option); select.value = '';
        }
        select.disabled = !voices.length;
        el('dictation-voice-status').textContent = voices.length
            ? '使用设备普通话语音，按正常语速朗读。多音字请先试听，必要时填写朗读提示。'
            : '未检测到普通话语音。请在设备的语音设置中添加普通话语音，再重新检测；也可换用支持中文朗读的浏览器。';
    }
    function selectedVoice(uri) {
        return voices.find((voice) => (voice.voiceURI || voice.name) === uri) || voices[0] || null;
    }
    function stopSpeech() {
        const interruptedPlayer = speaking && speechMode === 'player';
        speechEpoch += 1;
        clearTimeout(repeatTimer); clearTimeout(watchdog);
        repeatTimer = null; watchdog = null; utterance = null;
        speaking = false; speechMode = '';
        if (interruptedPlayer && run) { run.read = false; run.status = '播放已暂停，点播放重听本词'; }
        try { window.speechSynthesis?.cancel(); } catch (_) { /* A missing voice engine has no queue to cancel. */ }
    }
    function renderPlayer() {
        if (!run) return;
        el('dictation-player-title').textContent = run.number ? `第 ${run.number} 课 · 生词默写` : '生词默写';
        el('dictation-position').textContent = `第 ${run.index + 1} 个 / 共 ${run.words.length} 个`;
        el('dictation-progress').max = run.words.length;
        el('dictation-progress').value = run.index;
        el('dictation-play-status').textContent = run.status || (run.read ? '写好后，继续下一个' : '准备好后，点播放');
        el('dictation-player-settings').textContent = `正常语速 · 每词 ${run.repetitions} 遍${run.shuffled ? ' · 已打乱顺序' : ''}`;
        el('dictation-play').textContent = run.read ? '再听一遍' : '播放本词';
        el('dictation-play').disabled = speaking;
        el('dictation-next').disabled = speaking || !run.read;
        el('dictation-next').textContent = run.index === run.words.length - 1 ? '完成并对照' : '下一个';
    }
    function speakWords(word, repetitions, mode) {
        if (!active || !current(capture()) || (mode === 'player' && (!run || run.completed || el('dictation-player').hidden))) return;
        if (speaking && mode === 'player') return;
        const chosen = selectedVoice(mode === 'player' ? run.voiceURI : el('dictation-voice').value);
        if (!window.speechSynthesis || !window.SpeechSynthesisUtterance || !chosen) {
            message('设备还没有可用的普通话语音，请添加普通话语音后重新检测，再点播放。', true);
            if (mode === 'player') { run.read = false; run.status = '普通话语音未就绪，请重新检测'; renderPlayer(); }
            return;
        }
        stopSpeech();
        speaking = true; speechMode = mode;
        const epoch = speechEpoch, ctx = capture(), running = run;
        let completed = 0;
        if (mode === 'player') { run.read = false; run.status = '正在朗读，请听清…'; renderPlayer(); }
        else message('正在试听…');
        const valid = () => epoch === speechEpoch && current(ctx) && active
            && (mode !== 'player' || run === running && !run.completed && !el('dictation-player').hidden);
        function fail() {
            if (!valid()) return;
            stopSpeech();
            if (mode === 'player' && run === running) {
                run.read = false; run.status = '这次未能读完，请点播放重试'; renderPlayer();
            }
            message('语音没有正常读完，当前位置已保留。请检查设备声音后点播放重试。', true);
        }
        function speakOne() {
            if (!valid()) return;
            let currentUtterance;
            try {
                currentUtterance = new window.SpeechSynthesisUtterance(word.spoken_text || word.text);
                utterance = currentUtterance;
                currentUtterance.voice = chosen;
                currentUtterance.lang = 'zh-CN';
                currentUtterance.rate = 1;
                currentUtterance.pitch = 1;
                currentUtterance.volume = 1;
                currentUtterance.onerror = () => { if (utterance === currentUtterance) fail(); };
                currentUtterance.onend = () => {
                    if (!valid() || utterance !== currentUtterance) return;
                    clearTimeout(watchdog); watchdog = null; utterance = null;
                    completed += 1;
                    if (completed < repetitions) {
                        if (mode === 'player') { run.status = '稍后再读一遍…'; renderPlayer(); }
                        repeatTimer = setTimeout(() => { repeatTimer = null; speakOne(); }, 700);
                    } else {
                        speaking = false; speechMode = '';
                        if (mode === 'player') { run.read = true; run.status = '读完了，写好后继续'; renderPlayer(); }
                        else message('试听完成，可以按需要调整朗读提示。');
                    }
                };
                watchdog = setTimeout(fail, 45000);
                window.speechSynthesis.resume?.();
                window.speechSynthesis.speak(currentUtterance);
            } catch (_) { fail(); }
        }
        speakOne();
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
        run = { ...settings, words: snapshot, index: 0, read: false, completed: false, status: '准备好后，点播放' };
        message(); showView('player'); renderPlayer();
    }
    async function start() {
        if (loading || saving || !wordlist || conflict) return;
        const ctx = capture(true);
        if (!await save() || !current(ctx)) return;
        const words = cloneWords(wordlist.words);
        if (!words.length) { message('先录入至少一个词语，再开始默写。', true); return; }
        makeRun(words, { number: lesson.number, title: lesson.title, repetitions: Number(el('dictation-repetitions').value) === 1 ? 1 : 2,
            shuffled: el('dictation-shuffle').checked, voiceURI: el('dictation-voice').value });
    }
    function playCurrent() {
        if (run && !run.completed && !speaking) speakWords(run.words[run.index], run.repetitions, 'player');
    }
    function next() {
        if (!run || run.completed || speaking || !run.read || !active || !current(capture())) return;
        if (run.index === run.words.length - 1) { finish(false); return; }
        run.index += 1; run.read = false; run.status = '正在准备下一词…';
        renderPlayer(); playCurrent();
    }
    function finish(early) {
        if (!run || !current(capture())) return;
        stopSpeech(); run.completed = true;
        el('dictation-finish-label').textContent = `${run.title} · ${run.words.length} 个词语${early ? '，本轮已提前结束' : ''}。`;
        const list = el('dictation-review-list'); list.replaceChildren();
        for (const [index, word] of run.words.entries()) {
            const label = node('label', '', 'dictation-review-word');
            const checkbox = node('input'); checkbox.type = 'checkbox'; checkbox.value = String(index);
            checkbox.addEventListener('change', () => {
                el('dictation-retry-selected').disabled = !Array.from(list.querySelectorAll('input')).some((item) => item.checked);
            });
            label.append(checkbox, node('span', word.text)); list.appendChild(label);
        }
        el('dictation-retry-selected').disabled = true;
        message(); showView('finish');
    }
    function retry(all) {
        if (!run?.completed || !active || !current(capture())) return;
        const indices = Array.from(el('dictation-review-list').querySelectorAll('input')).filter((item) => all || item.checked).map((item) => Number(item.value));
        const words = indices.map((index) => run.words[index]).filter(Boolean);
        if (!words.length) return;
        makeRun(words, { number: run.number, title: run.title, repetitions: run.repetitions, shuffled: run.shuffled, voiceURI: run.voiceURI });
    }
    function backHome() { stopSpeech(); showView('home'); renderControls(); }
    function leaveView() { preserveDraft(); active = false; stopSpeech(); showView('home'); }
    function reset() {
        preserveDraft(); active = false; stopSpeech();
        generation += 1; requestSequence += 1; lessonSequence += 1;
        boundStudent = ''; boundToken = ''; loaded = false; loading = false; saving = false;
        books = []; lessons = []; summaries = new Map(); expandedUnits = new Set();
        bookId = ''; lesson = null; wordlist = null; raw = ''; conflict = null; run = null;
        el('dictation-book').replaceChildren(); el('dictation-lessons').replaceChildren();
        el('dictation-edition').textContent = ''; el('dictation-review-list').replaceChildren();
        el('dictation-custom-title').value = ''; el('dictation-custom-number').value = '';
        el('dictation-finish-label').textContent = ''; el('dictation-player-title').textContent = '';
        el('dictation-repetitions').value = '2'; el('dictation-shuffle').checked = false;
        suppressSpaceUp = false; message(); stateLabel(''); showView('home'); renderEditor();
    }
    async function open() {
        if (!signedIn()) { reset(); return; }
        if (boundStudent !== learner() || boundToken !== session().token) {
            reset(); boundStudent = learner(); boundToken = session().token;
        }
        active = true; showView('home'); detectVoices();
        if (loaded || loading) return;
        const ctx = capture(), sequence = ++requestSequence;
        loading = true; renderControls();
        try {
            const result = await request('/books');
            if (!current(ctx) || sequence !== requestSequence) return;
            books = Array.isArray(result.items) ? result.items : [];
            el('dictation-book').replaceChildren();
            for (const book of books) {
                const option = node('option', book.title); option.value = book.id; el('dictation-book').appendChild(option);
            }
            loaded = true; loading = false;
            if (books.length) await loadBook(books[0].id);
            else message('课本目录暂未准备好，请稍后再来。');
        } catch (error) { if (current(ctx)) message(error.message || '课本暂未能读取，请重新打开生词默写。', true); }
        finally { if (current(ctx)) { loading = false; renderControls(); } }
    }

    el('dictation-book').addEventListener('change', () => { if (!loading && !saving) return loadBook(el('dictation-book').value); });
    el('dictation-add-lesson').addEventListener('click', addLesson);
    el('dictation-words').addEventListener('input', () => {
        if (loading || saving || conflict || !wordlist || !current(capture(true))) return;
        raw = el('dictation-words').value; preserveDraft(); renderWords();
        try { stateLabel(unchanged(parseWords(raw).words) ? '已保存' : '本机有修改，待保存'); }
        catch (_) { stateLabel('本机内容已保留，请检查输入'); }
    });
    el('dictation-save').addEventListener('click', save);
    el('dictation-start').addEventListener('click', start);
    el('dictation-copy-local').addEventListener('click', copyLocal);
    el('dictation-load-cloud').addEventListener('click', inspectCloud);
    el('dictation-keep-cloud').addEventListener('click', () => { if (conflict) el('dictation-discard-confirm').hidden = false; });
    el('dictation-confirm-cloud').addEventListener('click', acceptCloud);
    el('dictation-refresh-voices').addEventListener('click', () => { detectVoices(); message(voices.length ? '普通话语音已就绪，可以试听或开始默写。' : '暂未检测到普通话语音，请先在设备中添加语音。', !voices.length); });
    el('dictation-play').addEventListener('click', playCurrent);
    el('dictation-next').addEventListener('click', next);
    el('dictation-player-exit').addEventListener('click', backHome);
    el('dictation-resume').addEventListener('click', () => {
        if (!run || run.completed || !current(capture())) return;
        message(); showView('player'); renderPlayer();
    });
    el('dictation-end-run').addEventListener('click', () => finish(true));
    el('dictation-retry-selected').addEventListener('click', () => retry(false));
    el('dictation-retry-all').addEventListener('click', () => retry(true));
    el('dictation-finish-back').addEventListener('click', () => { run = null; backHome(); });
    function spaceTarget(event) {
        if (!active || !run || run.completed || el('dictation-panel').hidden || el('dictation-player').hidden || document.hidden || !signedIn()
                || session().token !== boundToken || learner() !== boundStudent) return false;
        if (!(event.code === 'Space' || event.key === ' ' || event.key === 'Spacebar') || event.isComposing || event.keyCode === 229
                || event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return false;
        const target = event.target;
        if (target?.isContentEditable || target?.closest?.('input, textarea, select, [contenteditable="true"]')) return false;
        const button = target?.closest?.('button, a, summary');
        return !button || button.id === 'dictation-play' || button.id === 'dictation-next';
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
    window.subjectDictation = { open, leaveView, reset };
})();
