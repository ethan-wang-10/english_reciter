/**
 * 全站聊天室：侧栏 + REST 历史 + SSE 实时（依赖 app.js 的全局 token、API_BASE、escapeHtml、escapeRegExp、sessionStudentUsername）
 */
(function () {
    const base = typeof API_BASE !== "undefined" ? API_BASE : "/api";

    let chatEventSource = null;
    let chatReconnectTimer = null;
    let chatBackoffMs = 1000;
    let chatSseConnecting = false;
    /** 侧栏折叠时用轮询代替 SSE，降低长连接与解析开销（毫秒） */
    let chatPollTimer = null;
    const CHAT_COLLAPSED_POLL_MS = 20000;
    const CHAT_COLLAPSED_POLL_BACKOFF_MAX_MS = 120000;
    let chatPollInFlight = false;
    let chatPollBackoffMs = CHAT_COLLAPSED_POLL_MS;
    let chatPollAbort = null;
    let chatPollGeneration = 0;
    const chatSeenIds = new Set();
    let chatLoadingOlder = false;
    let chatHasMoreOlder = true;
    let chatSendInFlight = false;

    /** 最新消息 id（用于 SSE 断线补拉；收起侧栏时 DOM 可能未更新） */
    let lastChatMessageId = null;
    /** 侧栏收起时未读条数（不含自己发的） */
    let chatUnreadCount = 0;
    /** 侧栏收起时是否有人 @ 我 */
    let chatMentionPending = false;

    let atSuggestTimer = null;
    let atSuggestUsers = [];
    let atSuggestIndex = 0;
    let atSuggestFetchId = 0;

    /** 常用表情（Unicode），点击插入到输入框光标处 */
    const CHAT_EMOJI_LIST = [
        "😀",
        "😃",
        "😄",
        "😁",
        "😅",
        "😂",
        "🤣",
        "😊",
        "😍",
        "🥰",
        "😘",
        "😋",
        "😎",
        "🤩",
        "🥳",
        "😏",
        "😴",
        "🤔",
        "😮",
        "😢",
        "😭",
        "😤",
        "👍",
        "👎",
        "👌",
        "✌️",
        "🙏",
        "👏",
        "💪",
        "🔥",
        "✨",
        "💯",
        "❤️",
        "💔",
        "💬",
        "🎉",
        "🎁",
        "🏆",
        "⭐",
        "☀️",
        "🌙",
        "☕",
        "🍀",
        "📚",
        "✅",
        "❌",
        "❓",
        "💡",
        "🚀",
        "👋",
        "🤷",
        "🤦",
        "💀",
        "👀",
        "🙈",
        "🍕",
        "🍰",
        "🐶",
        "🐱",
        "🌈",
        "🌸",
        "🎵",
        "🎮",
        "📱",
        "💻",
        "🧧",
        "🎃",
        "🎄",
        "🥟",
        "🍜",
        "🍻",
        "🧋",
        "🍿",
        "🧁",
        "🍦",
        "🍩",
        "🫶",
        "🫡",
        "🥹",
        "🫠",
        "🤌",
        "🆗",
        "⚡",
        "🌊",
        "🍉",
        "🍓",
        "🥑",
        "🍳",
        "🍲",
        "🍱",
        "🎂",
        "🍪",
        "🍼",
        "🍵",
        "🍺",
        "🥤",
        "🥢",
        "🌶️",
        "🐰",
        "🐻",
        "🐼",
        "🐸",
        "🦄",
        "🐝",
        "🦋",
        "🐳",
        "🐧",
        "🦆",
        "🌺",
        "🌻",
        "🍁",
        "🍂",
        "💎",
        "🎀",
        "🎈",
        "🎊",
        "🔔",
        "📌",
        "📎",
        "✏️",
        "📝",
        "🔒",
        "🔑",
        "🤝",
        "🫰",
        "💅",
        "🧘",
        "🏃",
        "🚴",
        "🎯",
        "🎲",
        "🃏",
        "🀄",
        "♠️",
        "♥️",
        "♦️",
        "♣️",
    ];

    /** @ 提及：光标前从最后一个 @ 到光标为前缀（不含空格） */
    function getChatMentionState(text, cursor) {
        const before = text.slice(0, cursor);
        const at = before.lastIndexOf("@");
        if (at === -1) return null;
        if (at > 0 && /[a-zA-Z0-9_]/.test(before[at - 1])) return null;
        const after = before.slice(at + 1);
        if (/\s/.test(after)) return null;
        if (!/^[a-zA-Z0-9_]*$/.test(after)) return null;
        if (after.length > 32) return null;
        return { atIndex: at, query: after };
    }

    function hideChatAtSuggest() {
        const box = document.getElementById("chat-at-suggest");
        if (box) {
            box.hidden = true;
            box.innerHTML = "";
        }
        atSuggestUsers = [];
        atSuggestIndex = 0;
    }

    /** 当列表已渲染但内存数组不同步时，从 DOM 拉回用户名（避免键盘导航条件不满足） */
    function syncChatAtSuggestUsersFromDom() {
        const box = document.getElementById("chat-at-suggest");
        if (!box || box.hidden) return;
        const list = Array.from(box.querySelectorAll("[data-chat-at-user]"))
            .map((b) => b.getAttribute("data-chat-at-user"))
            .filter(Boolean);
        if (list.length) atSuggestUsers = list;
    }

    function renderChatAtSuggest() {
        hideChatEmojiPanel();
        const box = document.getElementById("chat-at-suggest");
        if (!box) return;
        if (!atSuggestUsers.length) {
            box.hidden = false;
            box.innerHTML = '<div class="chat-at-suggest-empty">无匹配用户</div>';
            return;
        }
        if (atSuggestIndex >= atSuggestUsers.length) atSuggestIndex = 0;
        if (atSuggestIndex < 0) atSuggestIndex = atSuggestUsers.length - 1;
        box.hidden = false;
        box.innerHTML = atSuggestUsers
            .map((u, i) => {
                const active = i === atSuggestIndex ? " chat-at-option--active" : "";
                return (
                    '<button type="button" class="chat-at-option' +
                    active +
                    '" role="option" data-chat-at-user="' +
                    escapeHtml(u) +
                    '">' +
                    escapeHtml(u) +
                    "</button>"
                );
            })
            .join("");
        box.querySelectorAll("[data-chat-at-user]").forEach((btn) => {
            btn.addEventListener("mousedown", (ev) => {
                ev.preventDefault();
                const u = btn.getAttribute("data-chat-at-user");
                if (u) applyChatMention(u);
            });
        });
        const activeEl = box.querySelector(".chat-at-option--active");
        if (activeEl) activeEl.scrollIntoView({ block: "nearest", inline: "nearest" });
    }

    function applyChatMention(username) {
        const ta = document.getElementById("chat-input");
        if (!ta || !username) return;
        const st = getChatMentionState(ta.value, ta.selectionStart);
        if (!st) return;
        const end = ta.selectionStart;
        const before = ta.value.slice(0, st.atIndex);
        const after = ta.value.slice(end);
        const insert = "@" + username + " ";
        ta.value = before + insert + after;
        const pos = before.length + insert.length;
        ta.selectionStart = ta.selectionEnd = pos;
        ta.focus();
        hideChatAtSuggest();
    }

    function hideChatEmojiPanel() {
        const panel = document.getElementById("chat-emoji-panel");
        const btn = document.getElementById("chat-emoji-btn");
        if (panel) panel.hidden = true;
        if (btn) btn.setAttribute("aria-expanded", "false");
    }

    function buildChatEmojiPanel() {
        const panel = document.getElementById("chat-emoji-panel");
        if (!panel || panel.querySelector(".chat-emoji-grid")) return;
        const grid = document.createElement("div");
        grid.className = "chat-emoji-grid";
        CHAT_EMOJI_LIST.forEach((emoji) => {
            const b = document.createElement("button");
            b.type = "button";
            b.className = "chat-emoji-cell";
            b.setAttribute("aria-label", "插入表情 " + emoji);
            b.textContent = emoji;
            b.addEventListener("mousedown", (ev) => {
                ev.preventDefault();
                insertChatEmoji(emoji);
            });
            grid.appendChild(b);
        });
        panel.appendChild(grid);
    }

    function insertChatEmoji(emoji) {
        const ta = document.getElementById("chat-input");
        if (!ta || !emoji) return;
        const max = parseInt(ta.getAttribute("maxlength") || "2000", 10);
        const val = ta.value || "";
        const start = ta.selectionStart;
        const end = ta.selectionEnd;
        const next = val.slice(0, start) + emoji + val.slice(end);
        if (next.length > max) return;
        ta.value = next;
        const pos = start + emoji.length;
        ta.selectionStart = ta.selectionEnd = pos;
        ta.focus();
    }

    function toggleChatEmojiPanel() {
        const panel = document.getElementById("chat-emoji-panel");
        const btn = document.getElementById("chat-emoji-btn");
        if (!panel || !btn) return;
        if (panel.hidden) {
            hideChatAtSuggest();
            buildChatEmojiPanel();
            panel.hidden = false;
            btn.setAttribute("aria-expanded", "true");
        } else {
            hideChatEmojiPanel();
        }
    }

    function scheduleChatAtSuggest() {
        clearTimeout(atSuggestTimer);
        atSuggestTimer = setTimeout(() => void runChatAtSuggest(), 180);
    }

    async function runChatAtSuggest() {
        const ta = document.getElementById("chat-input");
        if (!ta || !token) return;
        const st = getChatMentionState(ta.value, ta.selectionStart);
        if (!st) {
            hideChatAtSuggest();
            return;
        }
        const myFetch = ++atSuggestFetchId;
        try {
            const r = await fetch(
                `${base}/chat/users?q=${encodeURIComponent(st.query)}`,
                { headers: { Authorization: "Bearer " + token } }
            );
            if (myFetch !== atSuggestFetchId) return;
            if (!r.ok) {
                hideChatAtSuggest();
                return;
            }
            const data = await r.json();
            atSuggestUsers = Array.isArray(data.users) ? data.users : [];
            atSuggestIndex = 0;
            renderChatAtSuggest();
        } catch (_) {
            if (myFetch === atSuggestFetchId) hideChatAtSuggest();
        }
    }

    function me() {
        return typeof sessionStudentUsername === "function" ? sessionStudentUsername() : username;
    }

    function isChatSidebarOpen() {
        const side = document.getElementById("chat-sidebar");
        return !!(side && side.classList.contains("chat-sidebar--open"));
    }

    function mentionsMe(msg) {
        const m = me();
        if (!m || !msg || !Array.isArray(msg.mentions)) return false;
        return msg.mentions.indexOf(m) >= 0;
    }

    function clearChatUnreadState() {
        chatUnreadCount = 0;
        chatMentionPending = false;
        updateChatBadges();
    }

    function updateChatBadges() {
        const trig = document.getElementById("chat-side-trigger");
        const badge = document.getElementById("chat-side-badge");
        const mb = document.getElementById("mobile-chat-badge");
        const n = chatUnreadCount;
        const hasAt = chatMentionPending;

        if (trig) {
            trig.classList.toggle("btn-gear-chat--at", hasAt);
            trig.title = hasAt ? "聊天室：有人 @ 你" : n > 0 ? "聊天室：新消息" : "聊天室";
        }
        if (badge) {
            if (n > 0 || hasAt) {
                badge.hidden = false;
                badge.classList.toggle("btn-gear-badge--at", hasAt);
                badge.textContent = hasAt ? (n > 1 ? "@" + (n > 99 ? "99+" : n) : "@") : n > 99 ? "99+" : String(n);
            } else {
                badge.hidden = true;
                badge.classList.remove("btn-gear-badge--at");
                badge.textContent = "";
            }
        }
        if (mb) {
            if (n > 0 || hasAt) {
                mb.hidden = false;
                mb.classList.toggle("mobile-chat-badge--at", hasAt);
                mb.textContent = hasAt ? (n > 1 ? "@" + (n > 99 ? "99+" : n) : "@") : n > 99 ? "99+" : String(n);
            } else {
                mb.hidden = true;
                mb.classList.remove("mobile-chat-badge--at");
                mb.textContent = "";
            }
        }
    }

    function handleInboundChatMessage(msg) {
        if (!msg || !msg.id) return;
        if (chatSeenIds.has(msg.id)) return;
        lastChatMessageId = msg.id;

        const mine = msg.from === me();
        const open = isChatSidebarOpen();

        if (mine) {
            if (open) {
                appendMessageIfNew(msg, true);
                const empty = document.querySelector("#chat-log .chat-log-empty");
                if (empty) empty.remove();
                scrollChatToBottom();
            } else {
                chatSeenIds.add(msg.id);
            }
            return;
        }

        if (open) {
            appendMessageIfNew(msg, true);
            const empty = document.querySelector("#chat-log .chat-log-empty");
            if (empty) empty.remove();
            scrollChatToBottom();
        } else {
            chatSeenIds.add(msg.id);
            chatUnreadCount++;
            if (mentionsMe(msg)) chatMentionPending = true;
            updateChatBadges();
        }
    }

    function formatChatBody(body, mentions) {
        let s = escapeHtml(body);
        const list = Array.isArray(mentions) ? mentions : [];
        for (const u of list) {
            if (!u) continue;
            const re = new RegExp("@" + escapeRegExp(u) + "(?![a-zA-Z0-9_])", "g");
            s = s.replace(
                re,
                '<span class="chat-mention">@' + escapeHtml(u) + "</span>"
            );
        }
        return s;
    }

    function formatChatTime(ts) {
        if (!ts) return "";
        try {
            const d = new Date(ts);
            if (Number.isNaN(d.getTime())) return escapeHtml(String(ts));
            return escapeHtml(
                d.toLocaleString(undefined, {
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                })
            );
        } catch (_) {
            return escapeHtml(String(ts));
        }
    }

    /** 与顶栏头像同源：/api/user/avatar/{user}?w= */
    function chatAvatarUrlForUser(uname) {
        const path = "/api/user/avatar/" + encodeURIComponent(String(uname || "").trim());
        if (typeof avatarDisplayUrl === "function") {
            return avatarDisplayUrl(path, 48);
        }
        return path + "?w=48";
    }

    function chatAvatarHtml(uname) {
        const url = chatAvatarUrlForUser(uname);
        return (
            '<div class="chat-msg-avatar">' +
            '<img class="chat-msg-avatar-img" src="' +
            escapeHtml(url) +
            '" alt="" width="40" height="40" loading="lazy" decoding="async" onerror="this.onerror=null;this.parentElement.classList.add(\'chat-msg-avatar--fallback\');this.removeAttribute(\'src\');" />' +
            '<span class="chat-msg-avatar-ph" aria-hidden="true">👤</span>' +
            "</div>"
        );
    }

    function appendMessageEl(msg, atBottom) {
        const log = document.getElementById("chat-log");
        if (!log || !msg || !msg.id) return;
        if (chatSeenIds.has(msg.id)) return;
        chatSeenIds.add(msg.id);

        const row = document.createElement("div");
        const isMine = msg.from === me();
        row.className = "chat-msg" + (isMine ? " chat-msg--mine" : "");
        row.dataset.id = msg.id;
        const who = escapeHtml(msg.from || "");
        const when = formatChatTime(msg.ts);
        const bodyHtml = formatChatBody(msg.body || "", msg.mentions);
        const av = chatAvatarHtml(msg.from || "");
        row.innerHTML =
            '<div class="chat-msg-row">' +
            (isMine ? "" : av) +
            '<div class="chat-msg-main">' +
            '<div class="chat-msg-name">' +
            who +
            "</div>" +
            '<div class="chat-msg-body">' +
            bodyHtml +
            "</div>" +
            '<div class="chat-msg-time">' +
            when +
            "</div>" +
            "</div>" +
            (isMine ? av : "") +
            "</div>";

        if (atBottom) {
            log.appendChild(row);
            if (msg.id) lastChatMessageId = msg.id;
        } else {
            log.insertBefore(row, log.firstChild);
        }
    }

    function appendMessageIfNew(msg, atBottom) {
        if (!msg || !msg.id || chatSeenIds.has(msg.id)) return;
        appendMessageEl(msg, atBottom !== false);
    }

    function scrollChatToBottom() {
        const log = document.getElementById("chat-log");
        if (!log) return;
        log.scrollTop = log.scrollHeight;
    }

    function getFirstMessageId() {
        const log = document.getElementById("chat-log");
        const first = log && log.querySelector(".chat-msg");
        return first ? first.dataset.id : null;
    }

    function getLastMessageId() {
        const log = document.getElementById("chat-log");
        const rows = log && log.querySelectorAll(".chat-msg");
        if (!rows || !rows.length) return null;
        return rows[rows.length - 1].dataset.id;
    }

    async function loadInitial() {
        if (!token) return;
        const log = document.getElementById("chat-log");
        if (!log) return;
        log.innerHTML = '<p class="chat-log-loading">加载中…</p>';
        chatSeenIds.clear();
        chatHasMoreOlder = true;
        disconnectChatSSE();
        try {
            const r = await fetch(`${base}/chat/messages?limit=50`, {
                headers: { Authorization: "Bearer " + token },
            });
            if (!r.ok) {
                const err = await r.json().catch(() => ({}));
                log.innerHTML =
                    '<p class="chat-log-error">' +
                    escapeHtml(err.error || "加载失败") +
                    "</p>";
                return;
            }
            const data = await r.json();
            const msgs = data.messages || [];
            log.innerHTML = "";
            msgs.forEach((m) => appendMessageIfNew(m, true));
            if (msgs.length === 0) {
                log.innerHTML = '<p class="chat-log-empty">暂无消息，说点什么吧。</p>';
            }
            scrollChatToBottom();
            chatHasMoreOlder = msgs.length >= 50;
            if (msgs.length) lastChatMessageId = msgs[msgs.length - 1].id;
        } catch (e) {
            log.innerHTML =
                '<p class="chat-log-error">' + escapeHtml(String(e.message || "网络错误")) + "</p>";
        }
    }

    async function loadOlder() {
        if (!token || chatLoadingOlder || !chatHasMoreOlder) return;
        const beforeId = getFirstMessageId();
        if (!beforeId) return;
        chatLoadingOlder = true;
        const log = document.getElementById("chat-log");
        const prevScroll = log ? log.scrollHeight - log.scrollTop : 0;
        try {
            const r = await fetch(
                `${base}/chat/messages?before_id=${encodeURIComponent(beforeId)}&limit=50`,
                { headers: { Authorization: "Bearer " + token } }
            );
            if (!r.ok) return;
            const data = await r.json();
            const msgs = data.messages || [];
            if (msgs.length === 0) {
                chatHasMoreOlder = false;
                return;
            }
            const hadEmpty = log && log.querySelector(".chat-log-empty");
            if (hadEmpty) hadEmpty.remove();
            msgs.forEach((m) => appendMessageIfNew(m, false));
            if (log) log.scrollTop = log.scrollHeight - prevScroll;
            if (msgs.length < 50) chatHasMoreOlder = false;
        } finally {
            chatLoadingOlder = false;
        }
    }

    function disconnectChatSSE() {
        clearTimeout(chatReconnectTimer);
        chatReconnectTimer = null;
        if (chatEventSource) {
            chatEventSource.close();
            chatEventSource = null;
        }
    }

    function stopChatPoll() {
        chatPollGeneration++;
        if (chatPollTimer) {
            clearTimeout(chatPollTimer);
            chatPollTimer = null;
        }
        if (chatPollAbort) {
            chatPollAbort.abort();
            chatPollAbort = null;
        }
        chatPollInFlight = false;
        chatPollBackoffMs = CHAT_COLLAPSED_POLL_MS;
    }

    /**
     * 折叠状态下仅定期拉取新消息，更新未读 / @ 提醒（不维持 SSE）。
     * 首次无游标时拉一页历史并视为已读，避免把旧消息算成未读。
     */
    async function pollChatOnce() {
        if (chatPollInFlight) return;
        if (!token) return;
        if (isChatSidebarOpen()) return;
        const main = document.getElementById("main-page");
        if (!main || !main.classList.contains("active")) return;
        const controller = new AbortController();
        chatPollAbort = controller;
        chatPollInFlight = true;
        let ok = false;
        try {
            if (!lastChatMessageId) {
                const r = await fetch(`${base}/chat/messages?limit=50`, {
                    headers: { Authorization: "Bearer " + token },
                    signal: controller.signal,
                });
                if (!r.ok) return;
                if (isChatSidebarOpen()) return;
                const data = await r.json();
                const msgs = data.messages || [];
                for (const m of msgs) {
                    if (m && m.id) chatSeenIds.add(m.id);
                }
                if (msgs.length) lastChatMessageId = msgs[msgs.length - 1].id;
                ok = true;
                return;
            }
            const r = await fetch(
                `${base}/chat/messages?after_id=${encodeURIComponent(lastChatMessageId)}&limit=100`,
                {
                    headers: { Authorization: "Bearer " + token },
                    signal: controller.signal,
                }
            );
            if (!r.ok) return;
            if (isChatSidebarOpen()) return;
            const data = await r.json();
            for (const m of data.messages || []) {
                handleInboundChatMessage(m);
            }
            ok = true;
        } catch (_) {
        } finally {
            if (chatPollAbort === controller) {
                chatPollAbort = null;
                chatPollInFlight = false;
                chatPollBackoffMs = ok
                    ? CHAT_COLLAPSED_POLL_MS
                    : Math.min(chatPollBackoffMs * 2, CHAT_COLLAPSED_POLL_BACKOFF_MAX_MS);
            }
        }
    }

    function scheduleNextChatPoll(delayMs, generation) {
        if (generation !== chatPollGeneration) return;
        if (chatPollTimer) {
            clearTimeout(chatPollTimer);
            chatPollTimer = null;
        }
        if (!token) return;
        if (isChatSidebarOpen()) return;
        chatPollTimer = setTimeout(async () => {
            chatPollTimer = null;
            await pollChatOnce();
            if (generation === chatPollGeneration) {
                scheduleNextChatPoll(chatPollBackoffMs, generation);
            }
        }, delayMs);
    }

    function startChatPoll() {
        stopChatPoll();
        if (!token) return;
        const main = document.getElementById("main-page");
        if (!main || !main.classList.contains("active")) return;
        if (isChatSidebarOpen()) return;
        const generation = ++chatPollGeneration;
        void (async () => {
            await pollChatOnce();
            scheduleNextChatPoll(chatPollBackoffMs, generation);
        })();
    }

    async function fetchMissedThenReconnect() {
        const last = lastChatMessageId || getLastMessageId();
        if (last && token) {
            try {
                const r = await fetch(
                    `${base}/chat/messages?after_id=${encodeURIComponent(last)}&limit=100`,
                    { headers: { Authorization: "Bearer " + token } }
                );
                if (r.ok) {
                    const data = await r.json();
                    (data.messages || []).forEach((m) => handleInboundChatMessage(m));
                    if (isChatSidebarOpen()) scrollChatToBottom();
                }
            } catch (_) {}
        }
        const main = document.getElementById("main-page");
        if (main && main.classList.contains("active") && token) {
            ensureChatSse();
        }
    }

    function scheduleChatReconnect() {
        clearTimeout(chatReconnectTimer);
        chatReconnectTimer = setTimeout(() => {
            chatReconnectTimer = null;
            void fetchMissedThenReconnect();
        }, chatBackoffMs);
        chatBackoffMs = Math.min(chatBackoffMs * 2, 30000);
    }

    async function fetchChatStreamToken() {
        const r = await fetch(`${base}/chat/stream-token`, {
            method: "POST",
            headers: { Authorization: "Bearer " + token },
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok || !data.stream_token) {
            throw new Error(data.error || "无法建立聊天连接");
        }
        return data.stream_token;
    }

    async function connectChatSSE() {
        if (!token) return;
        if (chatEventSource) return;
        if (chatSseConnecting) return;
        chatSseConnecting = true;
        let streamToken = "";
        try {
            streamToken = await fetchChatStreamToken();
        } catch (_) {
            chatSseConnecting = false;
            scheduleChatReconnect();
            return;
        }
        chatSseConnecting = false;
        if (!token || chatEventSource || !isChatSidebarOpen()) return;
        const url =
            base.replace(/\/$/, "") +
            "/chat/stream?stream_token=" +
            encodeURIComponent(streamToken);
        chatEventSource = new EventSource(url);
        chatBackoffMs = 1000;
        chatEventSource.addEventListener("message", (e) => {
            try {
                const msg = JSON.parse(e.data);
                handleInboundChatMessage(msg);
            } catch (_) {}
        });
        chatEventSource.onerror = () => {
            if (chatEventSource) {
                chatEventSource.close();
                chatEventSource = null;
            }
            scheduleChatReconnect();
        };
    }

    function ensureChatSse() {
        if (!token) {
            stopChatPoll();
            disconnectChatSSE();
            return;
        }
        const main = document.getElementById("main-page");
        if (!main || !main.classList.contains("active")) {
            stopChatPoll();
            disconnectChatSSE();
            return;
        }
        if (isChatSidebarOpen()) {
            stopChatPoll();
            void connectChatSSE();
        } else {
            disconnectChatSSE();
            startChatPoll();
        }
    }

    function onChatLogout() {
        stopChatPoll();
        disconnectChatSSE();
        lastChatMessageId = null;
        chatSeenIds.clear();
        clearChatUnreadState();
    }

    async function openChatSidebar() {
        const side = document.getElementById("chat-sidebar");
        const bd = document.getElementById("chat-sidebar-backdrop");
        const trig = document.getElementById("chat-side-trigger");
        if (!side || !bd) return;
        stopChatPoll();
        clearChatUnreadState();
        bd.hidden = false;
        side.classList.add("chat-sidebar--open");
        side.setAttribute("aria-hidden", "false");
        bd.classList.add("chat-sidebar-backdrop--visible");
        document.body.classList.add("chat-sidebar-open");
        if (trig) trig.setAttribute("aria-expanded", "true");
        await loadInitial();
        await connectChatSSE();
        const ta = document.getElementById("chat-input");
        if (ta) setTimeout(() => ta.focus(), 200);
    }

    function closeChatSidebar() {
        const side = document.getElementById("chat-sidebar");
        const bd = document.getElementById("chat-sidebar-backdrop");
        const trig = document.getElementById("chat-side-trigger");
        if (side) {
            side.classList.remove("chat-sidebar--open");
            side.setAttribute("aria-hidden", "true");
        }
        if (bd) {
            bd.classList.remove("chat-sidebar-backdrop--visible");
            bd.hidden = true;
        }
        document.body.classList.remove("chat-sidebar-open");
        if (trig) trig.setAttribute("aria-expanded", "false");
        hideChatAtSuggest();
        hideChatEmojiPanel();
        ensureChatSse();
    }

    let _chatErrorTimer = null;
    function showChatError(msg) {
        clearTimeout(_chatErrorTimer);
        const compose = document.querySelector(".chat-compose");
        if (!compose) return;
        let el = compose.querySelector(".chat-compose-error");
        if (!el) {
            el = document.createElement("div");
            el.className = "chat-compose-error";
            compose.insertBefore(el, compose.firstChild);
        }
        el.textContent = msg || "发送失败";
        _chatErrorTimer = setTimeout(() => { if (el.parentNode) el.remove(); }, 4000);
    }

    async function sendChat() {
        const ta = document.getElementById("chat-input");
        const btn = document.getElementById("chat-send");
        if (!ta || !token) return;
        if (chatSendInFlight) return;
        const body = (ta.value || "").trim();
        if (!body) return;
        chatSendInFlight = true;
        if (btn) btn.disabled = true;
        try {
            const r = await fetch(`${base}/chat/messages`, {
                method: "POST",
                headers: {
                    Authorization: "Bearer " + token,
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ body }),
            });
            const data = await r.json().catch(() => ({}));
            if (!r.ok) {
                showChatError(data.error || "发送失败");
                return;
            }
            appendMessageIfNew(data, true);
            const empty = document.querySelector("#chat-log .chat-log-empty");
            if (empty) empty.remove();
            ta.value = "";
            hideChatAtSuggest();
            hideChatEmojiPanel();
            scrollChatToBottom();
        } catch (e) {
            showChatError(e.message || "网络错误");
        } finally {
            chatSendInFlight = false;
            if (btn) btn.disabled = false;
        }
    }

    function bind() {
        document.getElementById("chat-side-trigger")?.addEventListener("click", () => void openChatSidebar());
        document.getElementById("mobile-open-chat")?.addEventListener("click", () => {
            if (typeof closeMobileMoreSheet === "function") closeMobileMoreSheet();
            void openChatSidebar();
        });
        document.getElementById("chat-sidebar-close")?.addEventListener("click", closeChatSidebar);
        document.getElementById("chat-sidebar-backdrop")?.addEventListener("click", closeChatSidebar);
        document.getElementById("chat-send")?.addEventListener("click", () => void sendChat());
        document.getElementById("chat-emoji-btn")?.addEventListener("click", (e) => {
            e.stopPropagation();
            toggleChatEmojiPanel();
        });
        document.addEventListener("mousedown", (e) => {
            const panel = document.getElementById("chat-emoji-panel");
            const btn = document.getElementById("chat-emoji-btn");
            if (!panel || panel.hidden) return;
            const t = e.target;
            if (btn && (btn === t || btn.contains(t))) return;
            if (panel.contains(t)) return;
            hideChatEmojiPanel();
        });
        const ta = document.getElementById("chat-input");
        ta?.addEventListener("input", () => {
            scheduleChatAtSuggest();
        });
        ta?.addEventListener("keyup", (e) => {
            if (e.key === "ArrowLeft" || e.key === "ArrowRight" || e.key === "Home" || e.key === "End") {
                scheduleChatAtSuggest();
            }
        });
        ta?.addEventListener("blur", () => {
            setTimeout(() => hideChatAtSuggest(), 200);
        });
        ta?.addEventListener("keydown", (e) => {
            const emoPanel = document.getElementById("chat-emoji-panel");
            if (emoPanel && !emoPanel.hidden && e.key === "Escape") {
                e.preventDefault();
                hideChatEmojiPanel();
                return;
            }
            const box = document.getElementById("chat-at-suggest");
            const hasVisibleOptions = !!(box && !box.hidden && box.querySelector(".chat-at-option"));
            if (hasVisibleOptions) syncChatAtSuggestUsersFromDom();
            const suggestOpen = hasVisibleOptions && atSuggestUsers.length > 0;

            const isArrowDown =
                e.key === "ArrowDown" || e.key === "Down" || e.code === "ArrowDown";
            const isArrowUp = e.key === "ArrowUp" || e.key === "Up" || e.code === "ArrowUp";

            if (suggestOpen && (isArrowDown || isArrowUp)) {
                e.preventDefault();
                if (isArrowDown) {
                    atSuggestIndex = Math.min(atSuggestIndex + 1, atSuggestUsers.length - 1);
                } else {
                    atSuggestIndex = Math.max(atSuggestIndex - 1, 0);
                }
                renderChatAtSuggest();
                return;
            }
            if (suggestOpen && e.key === "Enter") {
                e.preventDefault();
                const u = atSuggestUsers[atSuggestIndex];
                if (u) applyChatMention(u);
                return;
            }
            if (suggestOpen && e.key === "Tab" && atSuggestUsers.length) {
                e.preventDefault();
                const u = atSuggestUsers[atSuggestIndex];
                if (u) applyChatMention(u);
                return;
            }
            if (box && !box.hidden && e.key === "Escape") {
                e.preventDefault();
                hideChatAtSuggest();
                return;
            }
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void sendChat();
            }
        });
        document.getElementById("chat-log")?.addEventListener("scroll", () => {
            const log = document.getElementById("chat-log");
            if (!log || log.scrollTop > 80) return;
            void loadOlder();
        });
        document.addEventListener("keydown", (e) => {
            if (e.key !== "Escape") return;
            const emo = document.getElementById("chat-emoji-panel");
            if (emo && !emo.hidden) {
                hideChatEmojiPanel();
                e.stopPropagation();
                return;
            }
            const box = document.getElementById("chat-at-suggest");
            if (box && !box.hidden && box.querySelector(".chat-at-option, .chat-at-suggest-empty")) {
                hideChatAtSuggest();
                e.stopPropagation();
                return;
            }
            const side = document.getElementById("chat-sidebar");
            if (side && side.classList.contains("chat-sidebar--open")) closeChatSidebar();
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bind);
    } else {
        bind();
    }

    window.chatRoomEnsureSse = ensureChatSse;
    window.chatRoomOnLogout = onChatLogout;

    if (typeof token !== "undefined" && token && document.getElementById("main-page")?.classList.contains("active")) {
        ensureChatSse();
    }
})();
