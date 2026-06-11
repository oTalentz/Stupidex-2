/* Stupidex client v2 — Workspaces, drag&drop, file tree, git clone */
marked.setOptions({ breaks: true, gfm: true, highlight: (code, lang) => {
    try { return lang && hljs.getLanguage(lang) ? hljs.highlight(code, { language: lang }).value : hljs.highlightAuto(code).value; }
    catch { return code; }
}});

const $ = (id) => document.getElementById(id);

const els = {
    sidebar: $("sidebar"),
    sessionList: $("session-list"),
    newChatBtn: $("new-chat-btn"),
    openSettings: $("open-settings"),

    workspacePanel: $("workspace-panel"),
    workspaceList: $("workspace-list"),
    treeContainer: $("tree-container"),
    refreshWs: $("ws-refresh"),
    uploadBtn: $("upload-btn"),
    cloneBtn: $("clone-btn"),
    newWsBtn: $("new-ws-btn"),
    fileInput: $("file-input"),

    sessionTitle: $("session-title"),
    messages: $("messages"),
    form: $("form"),
    input: $("input"),
    sendBtn: $("send-btn"),
    stopBtn: $("stop-btn"),
    status: $("status"),

    providerBadge: $("provider-badge"),
    modelBadge: $("model-badge"),

    settingsModal: $("settings-modal"),
    closeSettings: $("close-settings"),
    cancelSettings: $("cancel-settings"),
    saveSettings: $("save-settings"),
    providerSelect: $("provider-select"),
    apiKeyField: $("api-key-field"),
    apiKeyInput: $("api-key-input"),
    apiKeyStatus: $("api-key-status"),
    apiKeyHint: $("api-key-hint"),
    toggleKeyBtn: $("toggle-key-visibility"),
    modelInput: $("model-input"),

    themeToggle: $("theme-toggle"),

    cloneModal: $("clone-modal"),
    closeClone: $("close-clone"),
    cancelClone: $("cancel-clone"),
    confirmClone: $("confirm-clone"),
    cloneUrl: $("clone-url"),
    cloneBranch: $("clone-branch"),
    cloneName: $("clone-name"),
    cloneStatus: $("clone-status"),

    fileModal: $("file-modal"),
    closeFile: $("close-file"),
    fileModalTitle: $("file-modal-title"),
    fileModalContent: $("file-modal-content"),

    dropOverlay: $("drop-overlay"),
};

let state = {
    providers: [],
    config: { provider: "deepseek-v4-flash", model: "deepseek-v4-flash", has_api_key: true },
    workspaces: { workspaces: [], active_id: null },
    sessions: [],
    currentSessionId: null,
    busy: false,
    abortController: null,
    tree: [],
    dropCounter: 0,
};

function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function fmtSize(n) {
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1024 / 1024).toFixed(1) + " MB";
}

function fmtTime(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    const now = new Date();
    if (d.toDateString() === now.toDateString())
        return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return d.toLocaleDateString();
}

function fmtTokens(n) {
    if (n < 1000) return n;
    return (n / 1000).toFixed(1) + "k";
}

// ============================================================
// SESSIONS
// ============================================================

async function loadSessions() {
    const r = await fetch("/api/sessions");
    state.sessions = await r.json();
    renderSessions();
}

function renderSessions() {
    els.sessionList.innerHTML = "";
    for (const s of state.sessions) {
        const li = document.createElement("div");
        li.className = "session-item" + (s.id === state.currentSessionId ? " active" : "");
        if (s.pinned) li.classList.add("pinned");

        if (s.pinned) {
            const pin = document.createElement("span");
            pin.className = "pin-icon";
            pin.textContent = "★";
            pin.title = "Fixada";
            li.appendChild(pin);
        }

        const title = document.createElement("div");
        title.className = "title";
        title.textContent = s.title || "Nova conversa";
        title.title = `${s.title}\n${s.message_count} mensagens · ${fmtTime(s.updated_at)}`;
        li.appendChild(title);

        const more = document.createElement("button");
        more.className = "delete";
        more.textContent = "⋯";
        more.title = "Mais ações";
        more.addEventListener("click", (e) => {
            e.stopPropagation();
            showSessionMenu(s, li);
        });
        li.appendChild(more);

        li.addEventListener("click", () => openSession(s.id));
        els.sessionList.appendChild(li);
    }
}

function showSessionMenu(session, anchorEl) {
    // Reuse a single floating menu
    let menu = document.getElementById("session-menu");
    if (menu) menu.remove();
    menu = document.createElement("div");
    menu.id = "session-menu";
    menu.className = "context-menu";
    menu.innerHTML = `
        <button data-act="rename"><span>✎</span> Renomear</button>
        <button data-act="pin"><span>${session.pinned ? "☆" : "★"}</span> ${session.pinned ? "Desafixar" : "Fixar"}</button>
        <button data-act="export-md"><span>↓</span> Exportar Markdown</button>
        <button data-act="export-json"><span>↓</span> Exportar JSON</button>
        <button data-act="clear"><span>⌫</span> Limpar mensagens</button>
        <button data-act="archive"><span>${session.archived ? "↩" : "↓"}</span> ${session.archived ? "Reabrir" : "Arquivar"}</button>
        <button data-act="delete" class="danger"><span>×</span> Apagar permanentemente</button>
    `;
    const rect = anchorEl.getBoundingClientRect();
    menu.style.position = "fixed";
    menu.style.left = `${rect.right - 180}px`;
    menu.style.top = `${rect.bottom + 2}px`;
    document.body.appendChild(menu);

    menu.addEventListener("click", async (e) => {
        const btn = e.target.closest("button");
        if (!btn) return;
        const act = btn.dataset.act;
        menu.remove();
        if (act === "rename") {
            const t = prompt("Novo título:", session.title);
            if (t) { await fetch(`/api/sessions/${session.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: t }) }); await loadSessions(); }
        } else if (act === "pin") {
            await fetch(`/api/sessions/${session.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pinned: !session.pinned }) });
            await loadSessions();
        } else if (act === "export-md" || act === "export-json") {
            const fmt = act === "export-md" ? "md" : "json";
            window.open(`/api/sessions/${session.id}/export?format=${fmt}`);
        } else if (act === "clear") {
            if (!confirm("Limpar todas as mensagens desta conversa? (a sessão será mantida)")) return;
            await fetch(`/api/sessions/${session.id}/clear`, { method: "POST" });
            if (state.currentSessionId === session.id) { els.messages.innerHTML = ""; renderWelcome(); }
        } else if (act === "archive") {
            await fetch(`/api/sessions/${session.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ archived: !session.archived }) });
            if (state.currentSessionId === session.id && session.archived === false) { state.currentSessionId = null; els.messages.innerHTML = ""; renderWelcome(); els.sessionTitle.textContent = "Stupidex"; }
            await loadSessions();
        } else if (act === "delete") {
            await deleteSession(session.id);
        }
    });

    const closeOnOutside = (e) => {
        if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener("click", closeOnOutside); }
    };
    setTimeout(() => document.addEventListener("click", closeOnOutside), 0);
}

async function newSession() {
    const r = await fetch("/api/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
    });
    if (!r.ok) return;
    const s = await r.json();
    state.sessions.unshift(s);
    state.currentSessionId = s.id;
    renderSessions();
    renderWelcome();
    els.sessionTitle.textContent = s.title;
    els.input.focus();
}

async function openSession(id) {
    if (state.busy) return;
    state.currentSessionId = id;
    renderSessions();
    await loadMessages(id);
    const s = state.sessions.find(x => x.id === id);
    if (s) els.sessionTitle.textContent = s.title;
}

async function deleteSession(id) {
    if (!confirm("Apagar essa conversa?")) return;
    await fetch(`/api/sessions/${id}`, { method: "DELETE" });
    state.sessions = state.sessions.filter(s => s.id !== id);
    if (state.currentSessionId === id) {
        state.currentSessionId = null;
        renderWelcome();
        els.sessionTitle.textContent = "Stupidex";
    }
    renderSessions();
}

// ============================================================
// MESSAGES
// ============================================================

async function loadMessages(sid) {
    const r = await fetch(`/api/sessions/${sid}/messages`);
    const msgs = await r.json();
    renderMessages(msgs);
}

function renderWelcome() {
    els.messages.innerHTML = "";
    const wrap = document.createElement("div");
    wrap.className = "welcome";
    const hasWs = state.workspaces.active_id;
    wrap.innerHTML = hasWs ? `
        <h2>Como posso ajudar hoje?</h2>
        <p>Estou com o workspace <strong>${escapeHtml(state.workspaces.workspaces.find(w => w.id === hasWs)?.name || "ativo")}</strong> carregado. Peça uma alteração, descreva um bug, ou me peça para explorar o código.</p>
    ` : `
        <h2>Como posso ajudar hoje?</h2>
        <p>Antes de tudo, faça <strong>upload</strong> de arquivos ou <strong>clone um repositório</strong> no painel à esquerda. Aí posso ler, editar e rodar o código pra você.</p>
    `;
    if (!hasWs) {
        wrap.innerHTML += `
            <div class="suggestions">
                <div class="suggestion" data-action="upload">📤 Enviar arquivos</div>
                <div class="suggestion" data-action="clone">⎘ Clonar repositório</div>
                <div class="suggestion" data-action="new">+ Criar workspace vazio</div>
                <div class="suggestion" data-action="example">💬 Exemplo de pergunta</div>
            </div>
        `;
        wrap.querySelectorAll(".suggestion").forEach(s => {
            s.addEventListener("click", () => {
                const a = s.dataset.action;
                if (a === "upload") els.uploadBtn.click();
                else if (a === "clone") els.cloneBtn.click();
                else if (a === "new") els.newWsBtn.click();
                else if (a === "example") {
                    els.input.value = "Liste os arquivos do projeto e me dê um resumo.";
                    els.input.focus();
                    autoSize();
                }
            });
        });
    } else {
        wrap.innerHTML += `
            <div class="suggestions">
                <div class="suggestion">📂 Resumir o projeto</div>
                <div class="suggestion">🐛 Procurar bugs</div>
                <div class="suggestion">📝 Criar README</div>
                <div class="suggestion">🧪 Rodar testes</div>
            </div>
        `;
        const prompts = [
            "Resuma a estrutura deste projeto e me diga o que ele faz.",
            "Procure bugs óbvios nos arquivos Python.",
            "Crie um README.md básico para o projeto.",
            "Rode os testes e me diga o que falhou.",
        ];
        wrap.querySelectorAll(".suggestion").forEach((s, i) => {
            s.addEventListener("click", () => {
                els.input.value = prompts[i];
                els.input.focus();
                autoSize();
            });
        });
    }
    els.messages.appendChild(wrap);
}

function renderMessages(msgs) {
    els.messages.innerHTML = "";
    if (!msgs.length) { renderWelcome(); return; }
    const inner = document.createElement("div");
    inner.className = "messages-inner";
    els.messages.appendChild(inner);

    let i = 0;
    while (i < msgs.length) {
        const m = msgs[i];
        if (m.role === "user") {
            const userBubble = buildUserBubble(m.content);
            attachUserActions(userBubble, m.content);
            inner.appendChild(userBubble);
            i++;
        } else if (m.role === "assistant" && m.type === "text") {
            const aBubble = buildAssistantBubble(m);
            // Convert to row so we can attach actions; we re-attach the bubble inside
            const row = document.createElement("div");
            row.className = "message assistant";
            row.innerHTML = `<div class="avatar assistant">S</div><div class="bubble"></div>`;
            row.querySelector(".bubble").innerHTML = aBubble.querySelector(".bubble").innerHTML;
            attachAssistantActions(row);
            inner.appendChild(row);
            // Re-highlight code blocks
            row.querySelectorAll("pre code").forEach(block => { try { hljs.highlightElement(block); } catch {} });
            i++;
        } else if (m.role === "assistant" && m.type === "tool_call") {
            const group = buildAssistantGroup(m, msgs, i);
            attachAssistantActions(group.el);
            inner.appendChild(group.el);
            i = group.next;
        } else if (m.role === "tool") {
            inner.appendChild(buildToolResultBlock(m, null));
            i++;
        } else {
            i++;
        }
    }
    scrollToBottom();
}

function buildUserBubble(text) {
    const row = document.createElement("div");
    row.className = "message user";
    row.innerHTML = `<div class="avatar user">U</div><div class="bubble">${DOMPurify.sanitize(marked.parse(text || ""))}</div>`;
    return row;
}

function buildAssistantBubble(m) {
    const row = document.createElement("div");
    row.className = "message assistant";
    row.innerHTML = `<div class="avatar assistant">S</div><div class="bubble">${DOMPurify.sanitize(marked.parse(m.content || ""))}</div>`;
    return row;
}

function buildAssistantGroup(m, all, start) {
    const row = document.createElement("div");
    row.className = "message assistant";
    row.innerHTML = `<div class="avatar assistant">S</div><div class="bubble"></div>`;
    const bubble = row.querySelector(".bubble");
    if (m.content && m.content.trim()) {
        const text = document.createElement("div");
        text.innerHTML = DOMPurify.sanitize(marked.parse(m.content));
        bubble.appendChild(text);
    }
    const calls = m.tool_calls || [];
    const toolMap = {};
    let j = start + 1;
    while (j < all.length && all[j].role === "tool") {
        if (all[j].tool_call_id) toolMap[all[j].tool_call_id] = all[j];
        j++;
    }
    if (calls.length) {
        for (const c of calls) bubble.appendChild(buildToolCallBlock(c, toolMap[c.id]));
    }
    return { el: row, next: j };
}

function buildToolCallBlock(call, result) {
    const block = document.createElement("div");
    block.className = "tool-block";
    let headerLabel = call.name || "(tool)";
    if (call.arguments && call.arguments !== "{}") {
        try {
            const parsed = JSON.parse(call.arguments);
            const first = Object.entries(parsed).find(([k]) => k !== "working_dir" && k !== "cwd");
            if (first) headerLabel = `${call.name} ${first[0]}=${JSON.stringify(first[1])}`;
        } catch {}
    }
    if (result && result.metadata && result.metadata.error) block.classList.add("tool-error");
    if (!result) block.classList.add("tool-pending");

    const header = document.createElement("div");
    header.className = "tool-block-header";
    header.innerHTML = `
        ${result && result.metadata && result.metadata.error ? '<span class="tool-error-icon">✕</span>' :
          result ? '<span style="color:var(--success)">✓</span>' : '<span class="tool-spinner"></span>'}
        <span>${escapeHtml(headerLabel)}</span>
        <span class="arrow">▾</span>
    `;
    header.addEventListener("click", () => block.classList.toggle("collapsed"));
    block.appendChild(header);

    const body = document.createElement("div");
    body.className = "tool-block-body";
    let argsText = "";
    if (call.arguments && call.arguments !== "{}") {
        try { argsText = JSON.stringify(JSON.parse(call.arguments), null, 2); } catch { argsText = call.arguments; }
    }
    if (argsText) {
        const argsEl = document.createElement("pre");
        const argsCode = document.createElement("code");
        argsCode.textContent = argsText;
        argsEl.appendChild(argsCode);
        body.appendChild(argsEl);
    }
    if (result) {
        const sep = document.createElement("div");
        sep.className = "tool-args";
        sep.textContent = "output:";
        body.appendChild(sep);
        const out = document.createElement("pre");
        const text = result.content || "(empty)";
        const truncated = text.length > 4000 ? text.slice(0, 4000) + `\n... (truncated, ${text.length} chars total)` : text;
        const outCode = document.createElement("code");
        outCode.textContent = truncated;
        out.appendChild(outCode);
        body.appendChild(out);
    } else {
        const sep = document.createElement("div");
        sep.className = "tool-args";
        sep.textContent = "running…";
        body.appendChild(sep);
    }
    block.appendChild(body);
    return block;
}

function scrollToBottom() { els.messages.scrollTop = els.messages.scrollHeight; }

// ============================================================
// STREAMING
// ============================================================

async function sendMessage() {
    if (state.busy) return;
    const text = els.input.value.trim();
    if (!text) return;
    if (!state.currentSessionId) await newSession();
    const sid = state.currentSessionId;
    els.input.value = "";
    autoSize();

    appendUserMessage(text, { editable: true });
    const assistantRow = appendAssistantPlaceholder();
    const bubble = assistantRow.querySelector(".bubble");
    const thinking = document.createElement("div");
    thinking.className = "thinking-block hidden";
    bubble.appendChild(thinking);

    setBusy(true);
    state.abortController = new AbortController();

    await runChat({
        sid, text, bubble, thinking,
        url: `/api/sessions/${sid}/chat`,
        body: { message: text },
        assistantRow,
    });
}


async function runChat({ sid, text, bubble, thinking, url, body, assistantRow }) {
    try {
        const resp = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
            signal: state.abortController.signal,
        });
        if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
        await consumeStream(resp.body, { bubble, thinking, assistantRow });
    } catch (err) {
        if (err.name === "AbortError") {
            // Tell the server to cancel the in-flight LLM stream too
            try { await fetch(`/api/sessions/${sid}/stop`, { method: "POST" }); } catch {}
            bubble.innerHTML = `<em style="color:var(--text-muted)">[interrompido]</em>`;
            attachAssistantActions(assistantRow, sid);
        } else {
            bubble.innerHTML = `<em style="color:var(--danger)">Erro: ${escapeHtml(err.message)}</em>`;
            attachAssistantActions(assistantRow, sid);
        }
    } finally {
        setBusy(false);
        state.abortController = null;
        await loadSessions();
        const s = state.sessions.find(x => x.id === sid);
        if (s) els.sessionTitle.textContent = s.title;
        renderSessions();
        if (state.workspaces.active_id) {
            setTimeout(() => loadTree(state.workspaces.active_id), 500);
        }
    }
}

async function consumeStream(body, ctx) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let pendingText = "";
    let pendingThinking = "";
    let lastRenderLen = 0;
    const toolCallNodes = new Map();

    // Reuse a single text node for streaming to avoid re-parsing
    // the entire accumulated markdown on every chunk.
    const streamNode = document.createElement("div");
    streamNode.style.whiteSpace = "pre-wrap";
    ctx.bubble.appendChild(streamNode);

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";
        for (const p of parts) {
            if (!p.startsWith("data: ")) continue;
            const payload = p.slice(6).trim();
            if (payload === "[DONE]") continue;
            let evt;
            try { evt = JSON.parse(payload); } catch { continue; }
            handleEvent(evt, {
                bubble: ctx.bubble, thinking: ctx.thinking,
                toolCallNodes, streamNode,
                getText: () => pendingText, setText: (v) => pendingText = v,
                getThinking: () => pendingThinking, setThinking: (v) => pendingThinking = v,
            });
        }
    }
}

function handleEvent(evt, ctx) {
    switch (evt.type) {
        case "thinking":
            ctx.thinking.classList.remove("hidden");
            ctx.thinking.textContent = evt.content;
            ctx.setThinking(evt.content);
            scrollToBottom();
            break;
        case "text": {
            // Append the new chunk to the streaming node, no re-parse.
            // We only parse to HTML when streaming ends (see `done`).
            ctx.setText(evt.content);
            if (ctx.streamNode) {
                ctx.streamNode.textContent = evt.content;
            } else {
                ctx.bubble.textContent = evt.content;
            }
            scrollToBottom();
            break;
        }
        case "tool_calls": {
            // Hide the streaming text node while tools run; the final
            // markdown parse will restore it.
            if (ctx.streamNode) ctx.streamNode.style.display = "none";
            const group = document.createElement("div");
            for (const call of evt.calls) {
                const node = buildToolCallBlock({ name: call.name, arguments: call.arguments }, null);
                group.appendChild(node);
                ctx.toolCallNodes.set(call.id, node);
            }
            ctx.bubble.appendChild(group);
            scrollToBottom();
            break;
        }
        case "tool_result": {
            if (evt.content === "(running...)") return;
            const node = ctx.toolCallNodes.get(evt.id);
            if (!node) return;
            node.classList.toggle("tool-error", !!evt.error);
            node.classList.remove("tool-pending");
            const body = node.querySelector(".tool-block-body");
            if (body) {
                body.textContent = "";
                let argsText = "";
                if (evt.arguments && evt.arguments !== "{}") {
                    try { argsText = JSON.stringify(JSON.parse(evt.arguments), null, 2); } catch { argsText = evt.arguments; }
                }
                if (argsText) {
                    const argsEl = document.createElement("pre");
                    const argsCode = document.createElement("code");
                    argsCode.textContent = argsText;
                    argsEl.appendChild(argsCode);
                    body.appendChild(argsEl);
                }
                const sep = document.createElement("div");
                sep.className = "tool-args";
                sep.textContent = "output:";
                body.appendChild(sep);
                const out = document.createElement("pre");
                const text = evt.content || "(empty)";
                const truncated = text.length > 4000 ? text.slice(0, 4000) + `\n... (truncated, ${text.length} chars total)` : text;
                // Use textContent (not innerHTML) — defense in depth against
                // XSS from tool output. The `out` <pre> renders preformatted text.
                const outCode = document.createElement("code");
                outCode.textContent = truncated;
                out.appendChild(outCode);
                body.appendChild(out);
            }
            const header = node.querySelector(".tool-block-header");
            if (header && header.firstElementChild) {
                header.firstElementChild.outerHTML = evt.error
                    ? '<span class="tool-error-icon">✕</span>'
                    : '<span style="color:var(--success)">✓</span>';
            }
            scrollToBottom();
            break;
        }
        case "done": {
            if (evt.usage) {
                const u = evt.usage;
                els.status.textContent = `tokens: in ${fmtTokens(u.prompt_tokens)} · out ${fmtTokens(u.completion_tokens)} · total ${fmtTokens(u.total_tokens)}`;
                els.status.classList.remove("hidden");
            }
            // Now safely parse the full accumulated text as markdown.
            const fullText = ctx.getText();
            if (fullText && ctx.bubble) {
                ctx.bubble.innerHTML = DOMPurify.sanitize(marked.parse(fullText));
            }
            // Re-highlight code blocks
            ctx.bubble.querySelectorAll("pre code").forEach(block => {
                try { hljs.highlightElement(block); } catch {}
            });
            // Attach action buttons (copy / regenerate)
            if (ctx.assistantRow) {
                attachAssistantActions(ctx.assistantRow);
            }
            scrollToBottom();
            break;
        }
        case "error":
            ctx.bubble.innerHTML = `<em style="color:var(--danger)">${escapeHtml(evt.content)}</em>`;
            break;
        case "session_meta":
            if (evt.title) els.sessionTitle.textContent = evt.title;
            break;
    }
}

function appendUserMessage(text, opts = {}) {
    const inner = ensureInner();
    const bubble = buildUserBubble(text);
    if (opts.editable) {
        attachUserActions(bubble, text);
    }
    inner.appendChild(bubble);
    scrollToBottom();
}

function appendAssistantPlaceholder() {
    const inner = ensureInner();
    const row = document.createElement("div");
    row.className = "message assistant";
    row.innerHTML = `<div class="avatar assistant">S</div><div class="bubble"></div>`;
    inner.appendChild(row);
    scrollToBottom();
    return row;
}

function attachAssistantActions(assistantRow, sid) {
    if (assistantRow.querySelector(".msg-actions")) return;
    const actions = document.createElement("div");
    actions.className = "msg-actions";
    actions.innerHTML = `
        <button class="msg-action" data-action="copy" title="Copiar">⎘</button>
        <button class="msg-action" data-action="regenerate" title="Regenerar resposta">↻</button>
    `;
    actions.addEventListener("click", (e) => {
        const btn = e.target.closest(".msg-action");
        if (!btn) return;
        const a = btn.dataset.action;
        if (a === "copy") {
            const text = assistantRow.querySelector(".bubble").innerText;
            navigator.clipboard.writeText(text).then(() => {
                btn.textContent = "✓";
                setTimeout(() => { btn.textContent = "⎘"; }, 1200);
            });
        } else if (a === "regenerate") {
            regenerateLast();
        }
    });
    assistantRow.appendChild(actions);
}

function attachUserActions(bubble, originalText) {
    if (bubble.querySelector(".msg-actions")) return;
    const actions = document.createElement("div");
    actions.className = "msg-actions user-actions";
    actions.innerHTML = `
        <button class="msg-action" data-action="edit" title="Editar">✎</button>
        <button class="msg-action" data-action="copy" title="Copiar">⎘</button>
    `;
    actions.addEventListener("click", (e) => {
        const btn = e.target.closest(".msg-action");
        if (!btn) return;
        const a = btn.dataset.action;
        if (a === "edit") {
            const newText = prompt("Editar mensagem:", originalText);
            if (newText && newText !== originalText) {
                bubble.querySelector(".body").textContent = newText;
                originalText = newText;
            }
        } else if (a === "copy") {
            navigator.clipboard.writeText(bubble.querySelector(".body").innerText).then(() => {
                btn.textContent = "✓";
                setTimeout(() => { btn.textContent = "⎘"; }, 1200);
            });
        }
    });
    bubble.appendChild(actions);
}

async function regenerateLast() {
    if (state.busy) return;
    if (!state.currentSessionId) return;
    const sid = state.currentSessionId;
    const assistantRow = appendAssistantPlaceholder();
    const bubble = assistantRow.querySelector(".bubble");
    const thinking = document.createElement("div");
    thinking.className = "thinking-block hidden";
    bubble.appendChild(thinking);

    setBusy(true);
    state.abortController = new AbortController();

    await runChat({
        sid, text: "", bubble, thinking,
        url: `/api/sessions/${sid}/regenerate`,
        body: {},
        assistantRow,
    });
}

function ensureInner() {
    let inner = els.messages.querySelector(".messages-inner");
    if (!inner) {
        els.messages.innerHTML = "";
        inner = document.createElement("div");
        inner.className = "messages-inner";
        els.messages.appendChild(inner);
    }
    return inner;
}

function setBusy(b) {
    state.busy = b;
    els.sendBtn.disabled = b;
    els.input.disabled = b;
    els.sendBtn.classList.toggle("hidden", b);
    els.stopBtn.classList.toggle("hidden", !b);
    if (!b) els.input.focus();
}

function autoSize() {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(els.input.scrollHeight, 200) + "px";
}

// ============================================================
// WORKSPACES
// ============================================================

async function loadWorkspaces() {
    const r = await fetch("/api/workspaces");
    state.workspaces = await r.json();
    renderWorkspaces();
    if (state.workspaces.active_id) loadTree(state.workspaces.active_id);
    else { state.tree = []; renderTree(); }
}

function renderWorkspaces() {
    els.workspaceList.innerHTML = "";
    for (const w of state.workspaces.workspaces) {
        const li = document.createElement("div");
        li.className = "workspace-item" + (w.id === state.workspaces.active_id ? " active" : "");
        const src = document.createElement("span");
        src.className = "ws-source";
        src.textContent = w.source === "git" ? "⎘" : w.source === "upload" ? "↑" : "·";
        src.title = w.source;
        const name = document.createElement("span");
        name.className = "ws-name";
        name.textContent = w.name;
        name.title = `${w.name} — ${w.file_count} arquivos (${fmtSize(w.size_bytes)})`;
        const del = document.createElement("button");
        del.className = "ws-del";
        del.textContent = "×";
        del.title = "Apagar workspace";
        del.addEventListener("click", (e) => { e.stopPropagation(); removeWorkspace(w.id); });
        li.appendChild(src);
        li.appendChild(name);
        li.appendChild(del);
        li.addEventListener("click", () => activateWorkspace(w.id));
        els.workspaceList.appendChild(li);
    }
}

async function activateWorkspace(id) {
    await fetch(`/api/workspaces/${id}/activate`, { method: "POST" });
    state.workspaces.active_id = id;
    renderWorkspaces();
    loadTree(id);
    renderWelcome();
}

async function removeWorkspace(id) {
    if (!confirm("Apagar este workspace? (os arquivos no disco são removidos)")) return;
    await fetch(`/api/workspaces/${id}`, { method: "DELETE" });
    await loadWorkspaces();
    renderWelcome();
}

async function createEmptyWorkspace() {
    const name = prompt("Nome do workspace:", "novo-workspace");
    if (!name) return;
    const r = await fetch("/api/workspaces", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
    });
    if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        alert(e.error || "erro");
        return;
    }
    await loadWorkspaces();
    renderWelcome();
}

// ---------- File tree ----------

async function loadTree(wsId) {
    const r = await fetch(`/api/workspaces/${wsId}/tree`);
    if (!r.ok) {
        state.tree = [];
        renderTree();
        return;
    }
    const data = await r.json();
    state.tree = data.tree || [];
    renderTree();
}

function renderTree() {
    els.treeContainer.innerHTML = "";
    for (const node of state.tree) {
        els.treeContainer.appendChild(buildTreeNode(node, 0));
    }
}

function buildTreeNode(node, depth) {
    if (node.type === "directory") {
        const wrap = document.createElement("div");
        const head = document.createElement("div");
        head.className = "tree-node";
        head.style.paddingLeft = `${depth * 4}px`;
        head.innerHTML = `<span class="icon">▾</span><span>📁 ${escapeHtml(node.name)}</span>`;
        const children = document.createElement("div");
        children.className = "tree-children";
        for (const child of (node.children || [])) {
            children.appendChild(buildTreeNode(child, depth + 1));
        }
        head.addEventListener("click", () => {
            head.querySelector(".icon").textContent = children.classList.toggle("collapsed") ? "▸" : "▾";
        });
        wrap.appendChild(head);
        wrap.appendChild(children);
        return wrap;
    } else {
        const leaf = document.createElement("div");
        leaf.className = "tree-node";
        leaf.style.paddingLeft = `${depth * 4 + 14}px`;
        leaf.innerHTML = `<span class="icon">·</span><span>📄 ${escapeHtml(node.name)}</span>`;
        leaf.title = `${node.path} (${fmtSize(node.size)})`;
        leaf.addEventListener("click", () => openFile(node.path, node.name));
        return leaf;
    }
}

async function openFile(path, name) {
    if (!state.workspaces.active_id) return;
    const r = await fetch(`/api/workspaces/${state.workspaces.active_id}/file?path=${encodeURIComponent(path)}`);
    if (!r.ok) { alert("Erro ao abrir arquivo"); return; }
    const data = await r.json();
    els.fileModalTitle.textContent = name;
    els.fileModalContent.textContent = data.content;
    els.fileModal.classList.remove("hidden");
}

// ============================================================
// UPLOAD (button + drag&drop)
// ============================================================

els.uploadBtn.addEventListener("click", () => els.fileInput.click());
els.newWsBtn.addEventListener("click", createEmptyWorkspace);
els.refreshWs.addEventListener("click", () => {
    if (state.workspaces.active_id) loadTree(state.workspaces.active_id);
    loadWorkspaces();
});

els.fileInput.addEventListener("change", async () => {
    if (!els.fileInput.files.length) return;
    await uploadFiles(els.fileInput.files);
    els.fileInput.value = "";
});

async function uploadFiles(files) {
    let wsId = state.workspaces.active_id;
    if (!wsId) {
        // Create a new workspace
        const r = await fetch("/api/workspaces", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: "uploaded" }),
        });
        const data = await r.json();
        wsId = data.workspace.id;
        await loadWorkspaces();
    }

    const fd = new FormData();
    for (const f of files) {
        // webkitRelativePath preserves folder structure when uploading folders
        const relPath = f.webkitRelativePath || f.name;
        fd.append("files", f, relPath);
    }

    const r = await fetch(`/api/workspaces/${wsId}/upload`, {
        method: "POST",
        body: fd,
    });
    if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        alert(e.error || "upload falhou");
        return;
    }
    await loadWorkspaces();
    renderWelcome();
}

// Drag & drop on whole document
window.addEventListener("dragenter", (e) => {
    e.preventDefault();
    state.dropCounter++;
    els.dropOverlay.classList.remove("hidden");
});
window.addEventListener("dragleave", (e) => {
    e.preventDefault();
    state.dropCounter--;
    if (state.dropCounter <= 0) {
        state.dropCounter = 0;
        els.dropOverlay.classList.add("hidden");
    }
});
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", async (e) => {
    e.preventDefault();
    state.dropCounter = 0;
    els.dropOverlay.classList.add("hidden");
    const items = e.dataTransfer.items;
    const files = e.dataTransfer.files;
    if (files && files.length) {
        await uploadFiles(files);
    }
});

// ============================================================
// GIT CLONE
// ============================================================

els.cloneBtn.addEventListener("click", () => {
    els.cloneUrl.value = "";
    els.cloneBranch.value = "";
    els.cloneName.value = "";
    els.cloneStatus.textContent = "";
    els.cloneStatus.className = "field-hint";
    els.cloneModal.classList.remove("hidden");
});
els.closeClone.addEventListener("click", () => els.cloneModal.classList.add("hidden"));
els.cancelClone.addEventListener("click", () => els.cloneModal.classList.add("hidden"));
els.confirmClone.addEventListener("click", async () => {
    const url = els.cloneUrl.value.trim();
    if (!url) {
        setCloneStatus("URL obrigatória", true);
        return;
    }
    els.confirmClone.disabled = true;
    setCloneStatus("Clonando... pode demorar alguns segundos", false);
    try {
        const r = await fetch("/api/workspaces", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: els.cloneName.value.trim() || "repo" }),
        });
        if (!r.ok) {
            setCloneStatus("Erro ao criar workspace", true);
            return;
        }
        const data = await r.json();
        const wsId = data.workspace.id;
        const r2 = await fetch(`/api/workspaces/${wsId}/clone`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                url,
                branch: els.cloneBranch.value.trim() || null,
            }),
        });
        if (!r2.ok) {
            const err = await r2.json().catch(() => ({}));
            setCloneStatus(err.error || "clone falhou", true);
            await fetch(`/api/workspaces/${wsId}`, { method: "DELETE" });
            return;
        }
        setCloneStatus("Clonado!", false);
        await loadWorkspaces();
        setTimeout(() => els.cloneModal.classList.add("hidden"), 800);
        renderWelcome();
    } finally {
        els.confirmClone.disabled = false;
    }
});

function setCloneStatus(text, isError) {
    els.cloneStatus.textContent = text;
    els.cloneStatus.className = "field-hint" + (isError ? " err" : " ok");
}

// ============================================================
// SETTINGS MODAL
// ============================================================

async function openSettings() {
    await loadConfig();
    els.providerSelect.value = state.config.provider;
    els.modelInput.value = state.config.custom_model || "";
    els.apiKeyInput.value = "";
    updateApiKeyVisibility();
    els.settingsModal.classList.remove("hidden");
}
function closeSettings() { els.settingsModal.classList.add("hidden"); }

function updateApiKeyVisibility() {
    const p = state.providers.find(x => x.id === els.providerSelect.value);
    if (p && p.needs_api_key) {
        els.apiKeyField.classList.remove("hidden");
        els.apiKeyInput.disabled = false;
        els.apiKeyInput.placeholder = p.id.includes("anthropic") ? "sk-ant-..." : "sk-...";
        els.apiKeyHint.textContent = p.api_key_env ? `(env: ${p.api_key_env})` : "";
        if (state.config.has_api_key && state.config.provider === p.id) {
            els.apiKeyStatus.textContent = "chave configurada";
            els.apiKeyStatus.className = "field-hint ok";
        } else {
            els.apiKeyStatus.textContent = "chave não definida";
            els.apiKeyStatus.className = "field-hint";
        }
    } else {
        els.apiKeyField.classList.add("hidden");
        els.apiKeyInput.disabled = true;
    }
}

async function saveSettings() {
    els.saveSettings.disabled = true;
    const body = {
        provider: els.providerSelect.value,
        custom_model: els.modelInput.value.trim(),
    };
    if (els.apiKeyInput.value.trim()) body.api_key = els.apiKeyInput.value.trim();
    try {
        const r = await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        if (!r.ok) {
            const e = await r.json().catch(() => ({}));
            alert(e.error || `HTTP ${r.status}`);
            return;
        }
        await loadConfig();
        updateBadges();
        closeSettings();
    } finally { els.saveSettings.disabled = false; }
}

async function loadConfig() {
    const [provR, cfgR] = await Promise.all([fetch("/api/providers"), fetch("/api/config")]);
    state.providers = await provR.json();
    state.config = await cfgR.json();
    els.providerSelect.innerHTML = "";
    for (const p of state.providers) {
        const opt = document.createElement("option");
        opt.value = p.id;
        opt.textContent = p.name;
        opt.title = p.description || "";
        els.providerSelect.appendChild(opt);
    }
}

function updateBadges() {
    els.providerBadge.textContent = state.config.provider;
    els.modelBadge.textContent = state.config.model;
}

// ============================================================
// WIRE UP
// ============================================================

els.newChatBtn.addEventListener("click", newSession);
els.openSettings.addEventListener("click", openSettings);
$("logout-btn").addEventListener("click", async () => {
    if (!confirm("Sair da conta?")) return;
    await logout();
});
els.closeSettings.addEventListener("click", closeSettings);
els.cancelSettings.addEventListener("click", closeSettings);
els.saveSettings.addEventListener("click", saveSettings);
els.providerSelect.addEventListener("change", updateApiKeyVisibility);
els.toggleKeyBtn.addEventListener("click", () => {
    if (els.apiKeyInput.type === "password") {
        els.apiKeyInput.type = "text";
        els.toggleKeyBtn.textContent = "ocultar";
    } else {
        els.apiKeyInput.type = "password";
        els.toggleKeyBtn.textContent = "mostrar";
    }
});

els.closeFile.addEventListener("click", () => els.fileModal.classList.add("hidden"));
els.fileModal.addEventListener("click", (e) => { if (e.target === els.fileModal) els.fileModal.classList.add("hidden"); });
els.cloneModal.addEventListener("click", (e) => { if (e.target === els.cloneModal) els.cloneModal.classList.add("hidden"); });
els.settingsModal.addEventListener("click", (e) => { if (e.target === els.settingsModal) closeSettings(); });

document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
        if (!els.settingsModal.classList.contains("hidden")) closeSettings();
        else if (!els.cloneModal.classList.contains("hidden")) els.cloneModal.classList.add("hidden");
        else if (!els.fileModal.classList.contains("hidden")) els.fileModal.classList.add("hidden");
    }
});

els.form.addEventListener("submit", (e) => { e.preventDefault(); sendMessage(); });
els.stopBtn.addEventListener("click", () => { if (state.abortController) state.abortController.abort(); });
els.input.addEventListener("input", autoSize);
els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

// ============================================================
// THEME
// ============================================================

function setTheme(theme) {
    if (theme === "light") {
        document.documentElement.setAttribute("data-theme", "light");
    } else {
        document.documentElement.removeAttribute("data-theme");
    }
    try { localStorage.setItem("stupidex-theme", theme); } catch (e) {}
}

els.themeToggle.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
    setTheme(current === "light" ? "dark" : "light");
});

// Apply saved theme on boot
(function() {
    try {
        const saved = localStorage.getItem("stupidex-theme");
        if (saved) setTheme(saved);
    } catch (e) {}
})();

// ============================================================
// SEARCH
// ============================================================

let searchDebounce = null;
$("search-input").addEventListener("input", (e) => {
    clearTimeout(searchDebounce);
    const q = e.target.value.trim();
    searchDebounce = setTimeout(async () => {
        if (!q) {
            await loadSessions();
            return;
        }
        const r = await fetch(`/api/sessions/search?q=${encodeURIComponent(q)}`);
        if (r.ok) {
            state.sessions = await r.json();
            renderSessions();
        }
    }, 200);
});

// ============================================================
// KEYBOARD SHORTCUTS
// ============================================================

document.addEventListener("keydown", (e) => {
    // Ctrl/Cmd+K → focus search
    if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        $("search-input").focus();
        $("search-input").select();
    }
    // Ctrl/Cmd+Shift+N → new chat
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === "N") {
        e.preventDefault();
        newSession();
    }
    // Esc on the search box clears it
    if (e.key === "Escape" && document.activeElement === $("search-input")) {
        $("search-input").value = "";
        loadSessions();
    }
});

// ============================================================
// AUTH
// ============================================================

const AUTH_KEY = "stupidex-token";
const elsAuth = {
    loginScreen: $("login-screen"),
    mainApp: $("main-app"),
    loginForm: $("login-form"),
    loginEmail: $("login-email"),
    loginPassword: $("login-password"),
    loginError: $("login-error"),
};

function getToken() { try { return localStorage.getItem(AUTH_KEY) || ""; } catch { return ""; } }
function setToken(t)   { try { localStorage.setItem(AUTH_KEY, t); } catch {} }
function clearToken()  { try { localStorage.removeItem(AUTH_KEY); } catch {} }

// Monkey-patch fetch to inject auth header on all API calls
const _origFetch = window.fetch;
window.fetch = function(url, opts = {}) {
    const token = getToken();
    const isApi = typeof url === "string" && (
        url.startsWith("/api/") || url.includes("/api/")
    );
    if (token && isApi) {
        opts.headers = opts.headers || {};
        if (opts.headers instanceof Headers) {
            opts.headers.set("Authorization", "Bearer " + token);
        } else {
            opts.headers["Authorization"] = "Bearer " + token;
        }
    }
    return _origFetch(url, opts);
};

async function checkAuth() {
    const token = getToken();
    if (!token) return false;
    try {
        const r = await _origFetch("/api/auth/me", {
            headers: { Authorization: "Bearer " + token },
        });
        return r.ok;
    } catch {
        return false;
    }
}

function showLogin() {
    elsAuth.loginScreen.classList.remove("hidden");
    elsAuth.mainApp.classList.add("hidden");
}

function showApp() {
    elsAuth.loginScreen.classList.add("hidden");
    elsAuth.mainApp.classList.remove("hidden");
}

async function logout() {
    try { await _origFetch("/api/auth/logout", { method: "POST", headers: { Authorization: "Bearer " + getToken() } }); } catch {}
    clearToken();
    if (state.abortController) try { state.abortController.abort(); } catch {}
    state = {
        providers: [],
        config: { provider: "deepseek-v4-flash", model: "deepseek-v4-flash", has_api_key: true },
        workspaces: { workspaces: [], active_id: null },
        sessions: [],
        currentSessionId: null,
        busy: false,
        abortController: null,
        tree: [],
        dropCounter: 0,
    };
    els.messages.innerHTML = "";
    els.sessionList.innerHTML = "";
    els.workspaceList.innerHTML = "";
    els.treeContainer.innerHTML = "";
    els.sessionTitle.textContent = "Stupidex";
    showLogin();
    elsAuth.loginEmail.value = "";
    elsAuth.loginPassword.value = "";
    elsAuth.loginError.classList.add("hidden");
}

function setLoginError(msg) {
    elsAuth.loginError.textContent = msg;
    elsAuth.loginError.classList.remove("hidden");
}

// Email/password login
elsAuth.loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = elsAuth.loginEmail.value.trim();
    const password = elsAuth.loginPassword.value.trim();
    if (!email || !password) { setLoginError("Preencha email e senha"); return; }
    try {
        const r = await _origFetch("/api/auth/register", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: email, password }),
        });
        return handleLoginResponse(r, email);
    } catch (err) { setLoginError("Erro de rede"); }
});

async function handleLoginResponse(r, email) {
    const data = await r.json().catch(() => ({}));
    if (r.ok && data.token) {
        setToken(data.token);
        showApp();
        await bootApp();
        return;
    }
    if (r.status === 409 || (data.error && data.error.includes("already taken"))) {
        return tryLogin(email, elsAuth.loginPassword.value);
    }
    setLoginError(data.error || "Erro ao autenticar");
}

async function tryLogin(email, password) {
    try {
        const r = await _origFetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: email, password }),
        });
        const data = await r.json().catch(() => ({}));
        if (r.ok && data.token) {
            setToken(data.token);
            showApp();
            await bootApp();
        } else {
            setLoginError(data.error || "Credenciais inválidas");
        }
    } catch { setLoginError("Erro de rede"); }
}

// ============================================================
// BOOT
// ============================================================

async function bootApp() {
    await loadConfig();
    updateBadges();
    await loadWorkspaces();
    await loadSessions();
    if (state.sessions.length) {
        openSession(state.sessions[0].id);
    } else {
        renderWelcome();
    }
    els.input.focus();
}

(async function init() {
    if (await checkAuth()) {
        showApp();
        await bootApp();
    } else {
        showLogin();
    }
})();
