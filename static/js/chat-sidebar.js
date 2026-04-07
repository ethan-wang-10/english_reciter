/**
 * 全站聊天室：侧栏 + REST 历史 + SSE 实时（依赖 app.js 的全局 token、API_BASE、escapeHtml、escapeRegExp、sessionStudentUsername）
 */
(function () {
    const base = typeof API_BASE !== "undefined" ? API_BASE : "/api";

    let chatEventSource = null;
    let chatReconnectTimer = null;
    let chatBackoffMs = 1000;
    const chatSeenIds = new Set();
    let chatLoadingOlder = false;
    let chatHasMoreOlder = true;

    function me() {
        return typeof sessionStudentUsername === "function" ? sessionStudentUsername() : username;
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

    function appendMessageEl(msg, atBottom) {
        const log = document.getElementById("chat-log");
        if (!log || !msg || !msg.id) return;
        if (chatSeenIds.has(msg.id)) return;
        chatSeenIds.add(msg.id);

        const row = document.createElement("div");
        row.className = "chat-msg" + (msg.from === me() ? " chat-msg--mine" : "");
        row.dataset.id = msg.id;
        const who = escapeHtml(msg.from || "");
        const when = formatChatTime(msg.ts);
        const bodyHtml = formatChatBody(msg.body || "", msg.mentions);
        row.innerHTML =
            '<div class="chat-msg-meta"><span class="chat-msg-from">' +
            who +
            '</span><span class="chat-msg-ts">' +
            when +
            "</span></div>" +
            '<div class="chat-msg-body">' +
            bodyHtml +
            "</div>";

        if (atBottom) {
            log.appendChild(row);
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

    async function fetchMissedThenReconnect() {
        const last = getLastMessageId();
        if (last && token) {
            try {
                const r = await fetch(
                    `${base}/chat/messages?after_id=${encodeURIComponent(last)}&limit=100`,
                    { headers: { Authorization: "Bearer " + token } }
                );
                if (r.ok) {
                    const data = await r.json();
                    (data.messages || []).forEach((m) => {
                        appendMessageIfNew(m, true);
                    });
                    scrollChatToBottom();
                }
            } catch (_) {}
        }
        const side = document.getElementById("chat-sidebar");
        if (side && side.classList.contains("chat-sidebar--open")) {
            connectChatSSE();
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

    function connectChatSSE() {
        if (!token) return;
        disconnectChatSSE();
        const url =
            base.replace(/\/$/, "") +
            "/chat/stream?access_token=" +
            encodeURIComponent(token);
        chatEventSource = new EventSource(url);
        chatBackoffMs = 1000;
        chatEventSource.addEventListener("message", (e) => {
            try {
                const msg = JSON.parse(e.data);
                appendMessageIfNew(msg, true);
                const empty = document.querySelector("#chat-log .chat-log-empty");
                if (empty) empty.remove();
                scrollChatToBottom();
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

    async function openChatSidebar() {
        const side = document.getElementById("chat-sidebar");
        const bd = document.getElementById("chat-sidebar-backdrop");
        if (!side || !bd) return;
        bd.hidden = false;
        side.classList.add("chat-sidebar--open");
        side.setAttribute("aria-hidden", "false");
        bd.classList.add("chat-sidebar-backdrop--visible");
        document.body.classList.add("chat-sidebar-open");
        await loadInitial();
        connectChatSSE();
        const ta = document.getElementById("chat-input");
        if (ta) setTimeout(() => ta.focus(), 200);
    }

    function closeChatSidebar() {
        const side = document.getElementById("chat-sidebar");
        const bd = document.getElementById("chat-sidebar-backdrop");
        if (side) {
            side.classList.remove("chat-sidebar--open");
            side.setAttribute("aria-hidden", "true");
        }
        if (bd) {
            bd.classList.remove("chat-sidebar-backdrop--visible");
            bd.hidden = true;
        }
        document.body.classList.remove("chat-sidebar-open");
        disconnectChatSSE();
    }

    async function sendChat() {
        const ta = document.getElementById("chat-input");
        const btn = document.getElementById("chat-send");
        if (!ta || !token) return;
        const body = (ta.value || "").trim();
        if (!body) return;
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
                alert(data.error || "发送失败");
                return;
            }
            appendMessageIfNew(data, true);
            const empty = document.querySelector("#chat-log .chat-log-empty");
            if (empty) empty.remove();
            ta.value = "";
            scrollChatToBottom();
        } catch (e) {
            alert(e.message || "网络错误");
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    function bind() {
        document.getElementById("chat-sidebar-btn")?.addEventListener("click", () => void openChatSidebar());
        document.getElementById("mobile-open-chat")?.addEventListener("click", () => {
            if (typeof closeMobileMoreSheet === "function") closeMobileMoreSheet();
            void openChatSidebar();
        });
        document.getElementById("chat-sidebar-close")?.addEventListener("click", closeChatSidebar);
        document.getElementById("chat-sidebar-backdrop")?.addEventListener("click", closeChatSidebar);
        document.getElementById("chat-send")?.addEventListener("click", () => void sendChat());
        const ta = document.getElementById("chat-input");
        ta?.addEventListener("keydown", (e) => {
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
            const side = document.getElementById("chat-sidebar");
            if (side && side.classList.contains("chat-sidebar--open")) closeChatSidebar();
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bind);
    } else {
        bind();
    }
})();
