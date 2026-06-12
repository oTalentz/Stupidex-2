/* Stupidex client v2 — Workspaces, drag&drop, file tree, git clone */

// Safely configure external libraries (they may fail to load from CDN).
try {
  hljs && hljs.configure({ ignoreUnescapedHTML: true });
} catch (e) {}
try {
  marked &&
    marked.setOptions({
      breaks: true,
      gfm: true,
      highlight: function (code, lang) {
        try {
          return lang && hljs && hljs.getLanguage(lang)
            ? hljs.highlight(code, { language: lang }).value
            : hljs && hljs.highlightAuto(code).value;
        } catch (e) {
          return code;
        }
      },
    });
} catch (e) {}

// Avatar HTML for the assistant. Uses the Stupidex logo with a graceful
// fallback to the letter "S" if the image fails to load.
const ASSISTANT_AVATAR_HTML = `<div class="avatar assistant"><img src="/static/logo.webp" alt="S" onerror="this.replaceWith(Object.assign(document.createElement('span'),{textContent:'S',style:'color:#FAFAFA;font-weight:700;font-size:14px'}))"/></div>`;
const USER_AVATAR_HTML = `<div class="avatar user">U</div>`;

const $ = (id) => document.getElementById(id);

const els = {
  sidebar: $("sidebar"),
  sessionList: $("session-list"),
  newChatBtn: $("new-chat-btn"),
  openSettings: $("open-settings"),
  logoutBtn: $("logout-btn"),
  railWorkspace: $("rail-workspace"),

  workspacePanel: $("workspace-panel"),
  workspaceList: $("workspace-list"),
  treeContainer: $("tree-container"),
  refreshWs: $("ws-refresh"),
  uploadBtn: $("upload-btn"),
  cloneBtn: $("clone-btn"),
  newWsBtn: $("new-ws-btn"),
  fileInput: $("file-input"),
  wsFilesCount: $("ws-files-count"),
  wsActiveBadge: $("ws-active-badge"),

  sessionTitle: $("session-title"),
  sessionTime: $("session-time"),
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

  // Profile modal
  userProfileBanner: $("user-profile-banner"),
  userProfileAvatar: $("user-profile-avatar"),
  profileModal: $("profile-modal"),
  closeProfile: $("close-profile"),
  closeProfileBtn: $("close-profile-btn"),
  profileModalAvatar: $("profile-modal-avatar"),
  profileUsername: $("profile-username"),
  profileEmail: $("profile-email"),
  profileProvider: $("profile-provider"),
  profileOauthBadge: $("profile-oauth-badge"),
  profileMemberSince: $("profile-member-since"),
  profileWsCount: $("profile-ws-count"),

  // Shell widget
  shellOutput: $("shell-output"),
  shellInput: $("shell-input"),
  shellRun: $("shell-run"),
  shellClear: $("shell-clear"),

  cloneModal: $("clone-modal"),
  closeClone: $("close-clone"),
  cancelClone: $("cancel-clone"),
  confirmClone: $("confirm-clone"),
  cloneUrl: $("clone-url"),
  cloneBranch: $("clone-branch"),
  cloneName: $("clone-name"),
  cloneStatus: $("clone-status"),
  cloneGithubCard: $("clone-github-card"),
  cloneGithubTitle: $("clone-github-title"),
  cloneGithubDescription: $("clone-github-description"),
  cloneGithubAction: $("clone-github-action"),

  settingsGithubCard: $("settings-github-card"),
  settingsGithubAvatar: $("settings-github-avatar"),
  settingsGithubTitle: $("settings-github-title"),
  settingsGithubDescription: $("settings-github-description"),
  settingsGithubAction: $("settings-github-action"),

  fileModal: $("file-modal"),
  closeFile: $("close-file"),
  fileModalTitle: $("file-modal-title"),
  fileModalContent: $("file-modal-content"),

  dropOverlay: $("drop-overlay"),

  // v3 premium UI elements
  researchPanel: $("research-panel"),
  researchToggle: $("research-toggle"),
  closeResearch: $("close-research"),
  researchTabs: document.querySelectorAll(".research-tab"),
  researchTabPanes: {
    sources: $("research-tab-sources"),
    notes: $("research-tab-notes"),
    graph: $("research-tab-graph"),
  },
  composerAttach: $("composer-attach"),
  composerSearch: $("composer-search"),
  composerImageInput: $("composer-image-input"),
  composerImagePreview: $("composer-image-preview"),
  composerInputWrap: $("composer-input-wrap"),
  visionIndicator: $("vision-indicator"),
  webSearchIndicator: $("web-search-indicator"),
  composerViewProject: $("composer-view-project"),
  modelSwitcher: $("model-switcher"),

  // Trash & confirm modal
  railTrash: $("rail-trash"),
  sessionsSectionTitle: $("sessions-section-title"),
  trashCount: $("trash-count"),
  confirmModal: $("confirm-modal"),
  closeConfirm: $("close-confirm"),
  cancelConfirm: $("cancel-confirm"),
  confirmDelete: $("confirm-delete"),
  confirmTitle: $("confirm-title"),
  confirmMessage: $("confirm-message"),
  confirmDetail: $("confirm-detail"),
  confirmIcon: $("confirm-icon"),
};

let state = {
  providers: [],
  config: {
    provider: "deepseek-v4-flash",
    model: "deepseek-v4-flash",
    has_api_key: false,
  },
  workspaces: { workspaces: [], active_id: null },
  sessions: [],
  currentSessionId: null,
  busy: false,
  abortController: null,
  tree: [],
  dropCounter: 0,
  pendingImages: [],
  confirmCallback: null,
  trashMode: false,
  webSearchEnabled: false,
  github: {
    configured: false,
    connected: false,
    login: "",
    avatar_url: "",
  },
  user: {
    username: "",
    email: "",
    avatar_url: "",
    oauth_provider: "",
  },
};

const MAX_CHAT_IMAGES = 4;
const MAX_CHAT_IMAGE_BYTES = 5 * 1024 * 1024;
const CHAT_IMAGE_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
]);
const CHAT_IMAGE_MAX_DISPLAY_WIDTH = 300;
const CHAT_IMAGE_MAX_DISPLAY_HEIGHT = 300;

// Confirm modal helper
function showConfirm(title, message, onConfirm, options = {}) {
  els.confirmTitle.textContent = title;
  els.confirmMessage.textContent = message;
  els.confirmDetail.textContent = options.detail || "";
  els.confirmDetail.classList.toggle("hidden", !options.detail);
  els.confirmDelete.textContent = options.confirmLabel || "Confirmar";
  els.confirmDelete.classList.toggle("danger-btn", options.danger !== false);
  els.confirmDelete.classList.toggle("primary-btn", options.danger === false);
  els.confirmIcon.className = `confirm-icon ${options.danger === false ? "confirm-icon-neutral" : "confirm-icon-danger"}`;
  els.confirmIcon.innerHTML = `<i class="ph ${options.icon || "ph-trash"}"></i>`;
  els.confirmModal.classList.remove("hidden");
  state.confirmCallback = onConfirm;
  requestAnimationFrame(() => els.confirmDelete.focus());
}

function hideConfirm() {
  els.confirmModal.classList.add("hidden");
  state.confirmCallback = null;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function fmtSize(n) {
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / 1024 / 1024).toFixed(1) + " MB";
}

function currentProvider() {
  return state.providers.find((p) => p.id === state.config.provider) || null;
}

function currentModelSupportsVision() {
  return Boolean(currentProvider()?.supports_vision);
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () =>
      reject(new Error(`Não foi possível ler ${file.name || "a imagem"}`));
    reader.readAsDataURL(file);
  });
}

async function addChatImages(fileList) {
  const files = Array.from(fileList || []).filter((file) =>
    file.type.startsWith("image/"),
  );
  if (!files.length) return;
  if (!currentModelSupportsVision()) {
    alert(
      "O modelo selecionado não aceita imagens. Escolha um modelo com visão.",
    );
    return;
  }
  const available = MAX_CHAT_IMAGES - state.pendingImages.length;
  if (available <= 0) {
    alert(
      `Você pode anexar no máximo ${MAX_CHAT_IMAGES} imagens por mensagem.`,
    );
    return;
  }
  for (const file of files.slice(0, available)) {
    if (!CHAT_IMAGE_TYPES.has(file.type)) {
      alert(`Formato não suportado: ${file.type || file.name}`);
      continue;
    }
    if (file.size > MAX_CHAT_IMAGE_BYTES) {
      alert(`${file.name || "Imagem"} excede o limite de 5 MB.`);
      continue;
    }
    try {
      state.pendingImages.push({
        id:
          globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`,
        name: file.name || "imagem-colada.png",
        type: file.type,
        size: file.size,
        dataUrl: await fileToDataUrl(file),
      });
    } catch (error) {
      alert(error.message);
    }
  }
  renderChatImagePreviews();
}

function renderChatImagePreviews() {
  els.composerImagePreview.innerHTML = "";
  els.composerImagePreview.classList.toggle(
    "hidden",
    state.pendingImages.length === 0,
  );
  for (const image of state.pendingImages) {
    const item = document.createElement("div");
    item.className = "composer-image-item";
    const thumbnail = document.createElement("img");
    thumbnail.src = image.dataUrl;
    thumbnail.alt = image.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "composer-image-remove";
    remove.title = "Remover imagem";
    remove.setAttribute("aria-label", `Remover ${image.name}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      state.pendingImages = state.pendingImages.filter(
        (candidate) => candidate.id !== image.id,
      );
      renderChatImagePreviews();
    });
    item.append(thumbnail, remove);
    els.composerImagePreview.appendChild(item);
  }
}

function clearChatImages() {
  state.pendingImages = [];
  if (els.composerImageInput) els.composerImageInput.value = "";
  renderChatImagePreviews();
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  if (d.toDateString() === now.toDateString())
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString();
}

function updateConversationHeader(session = null) {
  if (!session) {
    els.sessionTitle.textContent = "Stupidex";
    els.sessionTime.textContent = "Pronto para começar";
    return;
  }
  const count = Number(session.message_count || 0);
  const messageLabel = `${count} ${count === 1 ? "mensagem" : "mensagens"}`;
  const updated = fmtTime(session.updated_at);
  els.sessionTitle.textContent = session.title || "Nova conversa";
  els.sessionTime.textContent = count
    ? `${messageLabel}${updated ? ` · Atualizada ${updated}` : ""}`
    : "Sem mensagens ainda";
}

function fmtTokens(n) {
  if (n < 1000) return n;
  return (n / 1000).toFixed(1) + "k";
}

// ============================================================
// SESSIONS
// ============================================================

async function loadSessions() {
  const r = await fetch("/api/sessions?include_trashed=1");
  if (!r.ok) return;
  state.sessions = await r.json();
  renderSessions();
  if (state.currentSessionId) {
    updateConversationHeader(
      state.sessions.find((session) => session.id === state.currentSessionId),
    );
  }
}

function renderSessions() {
  els.sessionList.innerHTML = "";
  const trashedCount = state.sessions.filter(
    (session) => session.trashed,
  ).length;
  els.sessionsSectionTitle.textContent = state.trashMode
    ? "Lixeira"
    : "Recent Chats";
  els.trashCount.textContent = trashedCount;
  els.trashCount.classList.toggle(
    "hidden",
    !state.trashMode || trashedCount === 0,
  );
  const sessionsToShow = state.trashMode
    ? state.sessions.filter((s) => s.trashed)
    : state.sessions.filter((s) => !s.trashed);

  if (sessionsToShow.length === 0) {
    const emptyMsg = document.createElement("div");
    emptyMsg.className = "sessions-empty";
    emptyMsg.textContent = state.trashMode
      ? "A lixeira está vazia"
      : "Nenhuma conversa ainda. Clique em + para começar.";
    els.sessionList.appendChild(emptyMsg);
    return;
  }

  for (const s of sessionsToShow) {
    const li = document.createElement("div");
    li.className =
      "session-item" + (s.id === state.currentSessionId ? " active" : "");
    if (s.pinned) li.classList.add("pinned");
    if (s.trashed) li.classList.add("trashed");

    if (s.pinned && !s.trashed) {
      const pin = document.createElement("span");
      pin.className = "pin-icon";
      pin.innerHTML = '<i class="ph-fill ph-push-pin"></i>';
      pin.title = "Fixada";
      li.appendChild(pin);
    }

    const title = document.createElement("div");
    title.className = "title";
    title.textContent = s.title || "Nova conversa";
    title.title = `${s.title}\n${s.message_count} mensagens · ${fmtTime(s.updated_at)}`;
    li.appendChild(title);

    if (s.trashed) {
      const actions = document.createElement("div");
      actions.className = "trash-session-actions";
      const restore = document.createElement("button");
      restore.type = "button";
      restore.className = "session-inline-action";
      restore.title = "Restaurar conversa";
      restore.setAttribute("aria-label", `Restaurar ${s.title}`);
      restore.innerHTML = '<i class="ph ph-arrow-counter-clockwise"></i>';
      restore.addEventListener("click", async (event) => {
        event.stopPropagation();
        await restoreSession(s.id);
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "session-inline-action session-inline-danger";
      remove.title = "Excluir permanentemente";
      remove.setAttribute("aria-label", `Excluir permanentemente ${s.title}`);
      remove.innerHTML = '<i class="ph ph-trash"></i>';
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        showConfirm(
          "Excluir permanentemente?",
          `A conversa “${s.title || "Sem título"}” será removida.`,
          () => deleteSession(s.id),
          {
            detail:
              "Todas as mensagens serão apagadas e esta ação não poderá ser desfeita.",
            confirmLabel: "Excluir conversa",
            icon: "ph-trash",
          },
        );
      });
      actions.append(restore, remove);
      li.appendChild(actions);
    } else {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "session-more-btn";
      more.innerHTML = '<i class="ph-bold ph-dots-three"></i>';
      more.title = "Mais ações";
      more.setAttribute("aria-label", `Ações de ${s.title}`);
      more.addEventListener("click", (event) => {
        event.stopPropagation();
        showSessionMenu(s, more);
      });
      li.appendChild(more);
      li.addEventListener("click", () => openSession(s.id));
    }
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
  menu.setAttribute("role", "menu");
  menu.innerHTML = `
      <button data-act="rename" class="menu-item" role="menuitem"><i class="ph ph-pencil-simple menu-icon"></i><span>Renomear</span></button>
      <button data-act="pin" class="menu-item" role="menuitem"><i class="ph ph-push-pin menu-icon"></i><span>${session.pinned ? "Desafixar" : "Fixar"}</span></button>
      <div class="menu-separator"></div>
      <button data-act="export-md" class="menu-item" role="menuitem"><i class="ph ph-file-md menu-icon"></i><span>Exportar Markdown</span></button>
      <button data-act="export-json" class="menu-item" role="menuitem"><i class="ph ph-brackets-curly menu-icon"></i><span>Exportar JSON</span></button>
      <div class="menu-separator"></div>
      <button data-act="clear" class="menu-item" role="menuitem"><i class="ph ph-eraser menu-icon"></i><span>Limpar mensagens</span></button>
      <button data-act="archive" class="menu-item" role="menuitem"><i class="ph ph-archive menu-icon"></i><span>${session.archived ? "Reabrir" : "Arquivar"}</span></button>
      <button data-act="trash" class="menu-item menu-danger" role="menuitem"><i class="ph ph-trash menu-icon"></i><span>Mover para lixeira</span></button>
  `;
  const rect = anchorEl.getBoundingClientRect();
  menu.style.position = "fixed";
  menu.style.left = `${Math.max(8, Math.min(rect.right - 224, window.innerWidth - 232))}px`;
  menu.style.top = `${Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - 330))}px`;
  document.body.appendChild(menu);

  menu.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const act = btn.dataset.act;
    menu.remove();
    if (act === "rename") {
      const t = prompt("Novo título:", session.title);
      if (t) {
        await fetch(`/api/sessions/${session.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: t }),
        });
        await loadSessions();
      }
    } else if (act === "pin") {
      await fetch(`/api/sessions/${session.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pinned: !session.pinned }),
      });
      await loadSessions();
    } else if (act === "export-md" || act === "export-json") {
      const fmt = act === "export-md" ? "md" : "json";
      window.open(`/api/sessions/${session.id}/export?format=${fmt}`);
    } else if (act === "clear") {
      showConfirm(
        "Limpar mensagens?",
        `O histórico de “${session.title || "Sem título"}” será apagado.`,
        async () => {
          await fetch(`/api/sessions/${session.id}/clear`, { method: "POST" });
          if (state.currentSessionId === session.id) {
            els.messages.innerHTML = "";
            renderWelcome();
          }
          await loadSessions();
        },
        {
          detail:
            "A conversa será mantida, mas as mensagens não poderão ser recuperadas.",
          confirmLabel: "Limpar mensagens",
          icon: "ph-eraser",
        },
      );
    } else if (act === "archive") {
      await fetch(`/api/sessions/${session.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived: !session.archived }),
      });
      if (state.currentSessionId === session.id && session.archived === false) {
        state.currentSessionId = null;
        els.messages.innerHTML = "";
        renderWelcome();
        updateConversationHeader();
      }
      await loadSessions();
    } else if (act === "trash") {
      showConfirm(
        "Mover para lixeira",
        `Tem certeza que deseja mover "${session.title || "Esta conversa"}" para a lixeira?`,
        async () => {
          await moveSessionToTrash(session.id);
        },
        {
          detail:
            "Você poderá restaurar ou excluir definitivamente pela lixeira lateral.",
          confirmLabel: "Mover para lixeira",
          icon: "ph-trash",
        },
      );
    }
  });

  const closeOnOutside = (e) => {
    if (!menu.contains(e.target)) {
      menu.remove();
      document.removeEventListener("click", closeOnOutside);
    }
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
  updateConversationHeader(s);
  els.input.focus();
}

async function openSession(id) {
  if (state.busy) return;
  const target = state.sessions.find((session) => session.id === id);
  if (!target || target.trashed) return;
  state.currentSessionId = id;
  renderSessions();
  await loadMessages(id);
  updateConversationHeader(target);
  els.workspacePanel.classList.remove("mobile-open");
}

async function moveSessionToTrash(id) {
  const response = await fetch(`/api/sessions/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ trashed: true }),
  });
  if (!response.ok)
    throw new Error("Não foi possível mover a conversa para a lixeira.");
  if (state.currentSessionId === id) {
    state.currentSessionId = null;
    els.messages.innerHTML = "";
    renderWelcome();
    updateConversationHeader();
  }
  await loadSessions();
}

async function restoreSession(id) {
  const response = await fetch(`/api/sessions/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ trashed: false }),
  });
  if (!response.ok) throw new Error("Não foi possível restaurar a conversa.");
  await loadSessions();
}

async function deleteSession(id) {
  const response = await fetch(`/api/sessions/${id}`, { method: "DELETE" });
  if (!response.ok) throw new Error("Não foi possível excluir a conversa.");
  state.sessions = state.sessions.filter((s) => s.id !== id);
  if (state.currentSessionId === id) {
    state.currentSessionId = null;
    renderWelcome();
    updateConversationHeader();
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
  const loading = document.querySelector(".messages-loading");
  if (loading) loading.remove();
  els.messages.innerHTML = "";
  const wrap = document.createElement("div");
  wrap.className = "welcome";
  const hasWs = state.workspaces.active_id;
  wrap.innerHTML = hasWs
    ? `
        <h2>Como posso ajudar hoje?</h2>
        <p>Estou com o workspace <strong>${escapeHtml(state.workspaces.workspaces.find((w) => w.id === hasWs)?.name || "ativo")}</strong> carregado. Peça uma alteração, descreva um bug, ou me peça para explorar o código.</p>
    `
    : `
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
    wrap.querySelectorAll(".suggestion").forEach((s) => {
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
  const loading = document.querySelector(".messages-loading");
  if (loading) loading.remove();
  els.messages.innerHTML = "";
  if (!msgs.length) {
    renderWelcome();
    return;
  }
  const inner = document.createElement("div");
  inner.className = "messages-inner";
  els.messages.appendChild(inner);

  let i = 0;
  while (i < msgs.length) {
    const m = msgs[i];
    if (m.role === "user") {
      const userBubble = buildUserBubble(m.content, m.metadata?.images || []);
      attachUserActions(userBubble, m.content);
      inner.appendChild(userBubble);
      i++;
    } else if (m.role === "assistant" && m.type === "text") {
      const aBubble = buildAssistantBubble(m);
      // Convert to row so we can attach actions; we re-attach the bubble inside
      const row = document.createElement("div");
      row.className = "message assistant";
      row.innerHTML = `${ASSISTANT_AVATAR_HTML}<div class="bubble"></div>`;
      row.querySelector(".bubble").innerHTML =
        aBubble.querySelector(".bubble").innerHTML;
      attachAssistantActions(row);
      inner.appendChild(row);
      // Re-highlight code blocks
      row.querySelectorAll("pre code").forEach((block) => {
        try {
          hljs.highlightElement(block);
        } catch {}
      });
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

function buildUserBubble(text, images = []) {
  const row = document.createElement("div");
  row.className = "message user";
  row.innerHTML = `${USER_AVATAR_HTML}<div class="bubble">${DOMPurify.sanitize(marked.parse(text || ""))}</div>`;
  const bubble = row.querySelector(".bubble");
  if (images.length) {
    const gallery = document.createElement("div");
    gallery.className = "message-image-gallery";
    for (const image of images) {
      if (image.dataUrl || image.data_url) {
        const img = document.createElement("img");
        img.src = image.dataUrl || image.data_url;
        img.alt = image.name || "Imagem anexada";
        img.loading = "lazy";
        gallery.appendChild(img);
      } else {
        const attachment = document.createElement("span");
        attachment.className = "message-image-attachment";
        attachment.textContent = `Imagem: ${image.name || "anexo"}`;
        gallery.appendChild(attachment);
      }
    }
    bubble.prepend(gallery);
  }
  return row;
}

function buildAssistantBubble(m) {
  const row = document.createElement("div");
  row.className = "message assistant";
  row.innerHTML = `${ASSISTANT_AVATAR_HTML}<div class="bubble">${DOMPurify.sanitize(marked.parse(m.content || ""))}</div>`;
  return row;
}

function buildAssistantGroup(m, all, start) {
  const row = document.createElement("div");
  row.className = "message assistant";
  row.innerHTML = `${ASSISTANT_AVATAR_HTML}<div class="bubble"></div>`;
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
    for (const c of calls)
      bubble.appendChild(buildToolCallBlock(c, toolMap[c.id]));
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
      const first = Object.entries(parsed).find(
        ([k]) => k !== "working_dir" && k !== "cwd",
      );
      if (first)
        headerLabel = `${call.name} ${first[0]}=${JSON.stringify(first[1])}`;
    } catch {}
  }
  if (result && result.metadata && result.metadata.error)
    block.classList.add("tool-error");
  if (!result) block.classList.add("tool-pending");

  const header = document.createElement("div");
  header.className = "tool-block-header";
  header.innerHTML = `
        ${
          result && result.metadata && result.metadata.error
            ? '<span class="tool-error-icon">✕</span>'
            : result
              ? '<span style="color:var(--success)">✓</span>'
              : '<span class="tool-spinner"></span>'
        }
        <span>${escapeHtml(headerLabel)}</span>
        <span class="arrow">▾</span>
    `;
  header.addEventListener("click", () => block.classList.toggle("collapsed"));
  block.appendChild(header);

  const body = document.createElement("div");
  body.className = "tool-block-body";
  let argsText = "";
  if (call.arguments && call.arguments !== "{}") {
    try {
      argsText = JSON.stringify(JSON.parse(call.arguments), null, 2);
    } catch {
      argsText = call.arguments;
    }
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
    const truncated =
      text.length > 4000
        ? text.slice(0, 4000) + `\n... (truncated, ${text.length} chars total)`
        : text;
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

function scrollToBottom() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

function renderWebSources(content) {
  const pane = els.researchTabPanes.sources;
  if (!pane) return;
  const sources = [];
  const pattern = /URL:\s+(https?:\/\/\S+)/g;
  for (const match of content.matchAll(pattern)) {
    try {
      const url = new URL(match[1]);
      if (!["http:", "https:"].includes(url.protocol)) continue;
      const previousLines = content
        .slice(0, match.index)
        .trim()
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
      const title = [...previousLines]
        .reverse()
        .find((line) => !/^\d+\.$/.test(line) && !line.startsWith("Found "));
      sources.push({ title: title || url.hostname, url: url.href });
    } catch {}
  }
  pane.innerHTML = "";
  for (const source of sources) {
    const card = document.createElement("a");
    card.className = "research-source-card";
    card.href = source.url;
    card.target = "_blank";
    card.rel = "noopener noreferrer";
    const icon = document.createElement("i");
    icon.className = "ph ph-globe-hemisphere-west";
    const copy = document.createElement("span");
    copy.className = "research-source-copy";
    const title = document.createElement("strong");
    title.textContent = source.title;
    const host = document.createElement("small");
    host.textContent = new URL(source.url).hostname;
    copy.append(title, host);
    card.append(icon, copy);
    pane.appendChild(card);
  }
  if (!sources.length) {
    const empty = document.createElement("p");
    empty.className = "research-empty";
    empty.textContent = "A pesquisa não retornou fontes identificáveis.";
    pane.appendChild(empty);
  }
  const count = document.querySelector(
    '[data-tab="sources"] .research-tab-count',
  );
  if (count) count.textContent = String(sources.length);
  if (sources.length) setResearchVisible(true);
}

// ============================================================
// STREAMING
// ============================================================

async function sendMessage() {
  if (state.busy) return;
  const text = els.input.value.trim();
  const images = [...state.pendingImages];
  if (!text && !images.length) return;
  if (images.length && !currentModelSupportsVision()) {
    alert("O modelo selecionado não aceita imagens.");
    return;
  }
  if (!state.currentSessionId) await newSession();
  const sid = state.currentSessionId;
  els.input.value = "";
  autoSize();
  clearChatImages();

  appendUserMessage(text, { editable: Boolean(text), images });
  const assistantRow = appendAssistantPlaceholder();
  const bubble = assistantRow.querySelector(".bubble");
  const thinking = document.createElement("div");
  thinking.className = "thinking-block hidden";
  bubble.appendChild(thinking);

  setBusy(true);
  state.abortController = new AbortController();

  await runChat({
    sid,
    text,
    bubble,
    thinking,
    url: `/api/sessions/${sid}/chat`,
    body: {
      message: text,
      provider: state.config.provider,
      model: state.config.model,
      web_search: state.webSearchEnabled,
      images: images.map((image) => ({
        name: image.name,
        data_url: image.dataUrl,
      })),
    },
    assistantRow,
  });
}

async function runChat({
  sid,
  text,
  bubble,
  thinking,
  url,
  body,
  assistantRow,
}) {
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: state.abortController.signal,
    });
    if (!resp.ok || !resp.body) {
      const payload = await resp.json().catch(() => ({}));
      throw new Error(payload.error || `HTTP ${resp.status}`);
    }
    await consumeStream(resp.body, { bubble, thinking, assistantRow });
  } catch (err) {
    if (err.name === "AbortError") {
      // Tell the server to cancel the in-flight LLM stream too
      try {
        await fetch(`/api/sessions/${sid}/stop`, { method: "POST" });
      } catch {}
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
    const s = state.sessions.find((x) => x.id === sid);
    if (s) updateConversationHeader(s);
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
      try {
        evt = JSON.parse(payload);
      } catch {
        continue;
      }
      handleEvent(evt, {
        bubble: ctx.bubble,
        thinking: ctx.thinking,
        toolCallNodes,
        streamNode,
        getText: () => pendingText,
        setText: (v) => (pendingText = v),
        getThinking: () => pendingThinking,
        setThinking: (v) => (pendingThinking = v),
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
        const node = buildToolCallBlock(
          { name: call.name, arguments: call.arguments },
          null,
        );
        group.appendChild(node);
        ctx.toolCallNodes.set(call.id, node);
      }
      ctx.bubble.appendChild(group);
      scrollToBottom();
      break;
    }
    case "tool_result": {
      if (evt.content === "(running...)") return;
      if (evt.name === "web_search" && !evt.error) {
        renderWebSources(evt.content || "");
      }
      const node = ctx.toolCallNodes.get(evt.id);
      if (!node) return;
      node.classList.toggle("tool-error", !!evt.error);
      node.classList.remove("tool-pending");
      const body = node.querySelector(".tool-block-body");
      if (body) {
        body.textContent = "";
        let argsText = "";
        if (evt.arguments && evt.arguments !== "{}") {
          try {
            argsText = JSON.stringify(JSON.parse(evt.arguments), null, 2);
          } catch {
            argsText = evt.arguments;
          }
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
        const truncated =
          text.length > 4000
            ? text.slice(0, 4000) +
              `\n... (truncated, ${text.length} chars total)`
            : text;
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
      ctx.bubble.querySelectorAll("pre code").forEach((block) => {
        try {
          hljs.highlightElement(block);
        } catch {}
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
      if (evt.title) {
        const session = state.sessions.find(
          (item) => item.id === state.currentSessionId,
        );
        if (session) session.title = evt.title;
        updateConversationHeader(session || { title: evt.title });
      }
      break;
  }
}

function appendUserMessage(text, opts = {}) {
  const inner = ensureInner();
  const bubble = buildUserBubble(text, opts.images || []);
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
  row.innerHTML = `${ASSISTANT_AVATAR_HTML}<div class="bubble"></div>`;
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
        setTimeout(() => {
          btn.textContent = "⎘";
        }, 1200);
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
      navigator.clipboard
        .writeText(bubble.querySelector(".body").innerText)
        .then(() => {
          btn.textContent = "✓";
          setTimeout(() => {
            btn.textContent = "⎘";
          }, 1200);
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
    sid,
    text: "",
    bubble,
    thinking,
    url: `/api/sessions/${sid}/regenerate`,
    body: { provider: state.config.provider, model: state.config.model },
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
  else {
    state.tree = [];
    renderTree();
  }
}

function renderWorkspaces() {
  els.workspaceList.innerHTML = "";
  for (const w of state.workspaces.workspaces) {
    const li = document.createElement("div");
    li.className =
      "workspace-item" + (w.id === state.workspaces.active_id ? " active" : "");
    const src = document.createElement("span");
    src.className = "ws-source-icon";
    src.textContent =
      w.source === "git" ? "⎘" : w.source === "upload" ? "↑" : "·";
    src.title = w.source;
    const name = document.createElement("span");
    name.className = "ws-name";
    name.textContent = w.name;
    name.title = `${w.name} — ${w.file_count} arquivos (${fmtSize(w.size_bytes)})`;
    const actions = document.createElement("span");
    actions.className = "ws-actions";
    if (w.source === "git") {
      const sync = document.createElement("button");
      sync.className = "ws-sync-btn";
      sync.innerHTML = '<i class="ph ph-arrows-clockwise"></i>';
      sync.title = "Sincronizar repositório (git pull)";
      sync.addEventListener("click", async (e) => {
        e.stopPropagation();
        sync.classList.add("syncing");
        try {
          const r = await fetch(`/api/workspaces/${w.id}/pull`, { method: "POST" });
          const data = await r.json();
          if (data.ok) {
            await loadWorkspaces();
          } else {
            alert(data.output || "Falha ao sincronizar");
          }
        } catch (err) {
          alert("Erro de rede ao sincronizar");
        } finally {
          sync.classList.remove("syncing");
        }
      });
      actions.appendChild(sync);
    }
    const del = document.createElement("button");
    del.className = "ws-del";
    del.textContent = "×";
    del.title = "Apagar workspace";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      removeWorkspace(w.id);
    });
    li.appendChild(src);
    li.appendChild(name);
    li.appendChild(actions);
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
  if (!confirm("Apagar este workspace? (os arquivos no disco são removidos)"))
    return;
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
    for (const child of node.children || []) {
      children.appendChild(buildTreeNode(child, depth + 1));
    }
    head.addEventListener("click", () => {
      head.querySelector(".icon").textContent = children.classList.toggle(
        "collapsed",
      )
        ? "▸"
        : "▾";
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
  const r = await fetch(
    `/api/workspaces/${state.workspaces.active_id}/file?path=${encodeURIComponent(path)}`,
  );
  if (!r.ok) {
    alert("Erro ao abrir arquivo");
    return;
  }
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
  const items = Array.from(e.dataTransfer?.items || []).filter(
    (item) => item.kind === "file",
  );
  const imageDrop =
    items.length > 0 && items.every((item) => item.type.startsWith("image/"));
  const dropText = els.dropOverlay.querySelector(".drop-text");
  if (dropText) {
    dropText.textContent =
      imageDrop && currentModelSupportsVision()
        ? "Solte para anexar ao chat"
        : "Solte arquivos ou pastas aqui";
  }
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
    const imageFiles = Array.from(files).filter((file) =>
      file.type.startsWith("image/"),
    );
    if (imageFiles.length === files.length && currentModelSupportsVision()) {
      await addChatImages(imageFiles);
    } else {
      await uploadFiles(files);
    }
  }
});

// ============================================================
// GIT CLONE
// ============================================================

function safeGithubAvatar(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" &&
      parsed.hostname.endsWith("githubusercontent.com")
      ? parsed.href
      : "";
  } catch {
    return "";
  }
}

function renderGithubIntegrationCard(card, title, description, action) {
  if (!card) return;
  const connected = state.github.connected;
  const configured = state.github.configured;
  card.classList.toggle("is-connected", connected);
  card.classList.toggle("is-unavailable", !configured);
  title.textContent = connected
    ? `GitHub conectado · @${state.github.login}`
    : configured
      ? "GitHub não conectado"
      : "Integração GitHub indisponível";
  description.textContent = connected
    ? "Acesso habilitado para repositórios públicos e privados."
    : configured
      ? "Conecte sua conta para clonar repositórios privados."
      : "Configure o OAuth do GitHub no servidor para habilitar projetos privados.";
  action.textContent = connected
    ? "Desconectar"
    : configured
      ? "Conectar"
      : "Indisponível";
  action.disabled = !configured;

  const icon = card.querySelector(".github-integration-icon");
  const avatar = connected ? safeGithubAvatar(state.github.avatar_url) : "";
  icon.replaceChildren();
  if (avatar) {
    const image = document.createElement("img");
    image.src = avatar;
    image.alt = "";
    icon.appendChild(image);
  } else {
    const logo = document.createElement("i");
    logo.className = "ph ph-github-logo";
    icon.appendChild(logo);
  }
}

function renderGithubIntegration() {
  renderGithubIntegrationCard(
    els.cloneGithubCard,
    els.cloneGithubTitle,
    els.cloneGithubDescription,
    els.cloneGithubAction,
  );
  renderGithubIntegrationCard(
    els.settingsGithubCard,
    els.settingsGithubTitle,
    els.settingsGithubDescription,
    els.settingsGithubAction,
  );
}

async function loadGithubIntegration() {
  try {
    const response = await fetch("/api/integrations/github");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.github = await response.json();
  } catch {
    state.github = {
      configured: false,
      connected: false,
      login: "",
      avatar_url: "",
    };
  }
  renderGithubIntegration();
}

function handleGithubAction() {
  if (!state.github.configured) return;
  if (!state.github.connected) {
    window.location.assign("/api/integrations/github/connect");
    return;
  }
  showConfirm(
    "Desconectar GitHub?",
    `A conta @${state.github.login} deixará de ser usada para acessar repositórios privados.`,
    async () => {
      const response = await fetch("/api/integrations/github", {
        method: "DELETE",
      });
      if (!response.ok)
        throw new Error("Não foi possível desconectar o GitHub.");
      await loadGithubIntegration();
    },
    {
      detail:
        "Workspaces já clonados permanecem disponíveis, mas novos pulls privados exigirão uma nova conexão.",
      confirmLabel: "Desconectar",
      danger: false,
      icon: "ph-link-break",
    },
  );
}

async function openCloneModal() {
  els.cloneUrl.value = "";
  els.cloneBranch.value = "";
  els.cloneName.value = "";
  els.cloneStatus.textContent = "";
  els.cloneStatus.className = "field-hint";
  await loadGithubIntegration();
  els.cloneModal.classList.remove("hidden");
}

els.cloneBtn.addEventListener("click", openCloneModal);
els.cloneGithubAction.addEventListener("click", handleGithubAction);
els.settingsGithubAction.addEventListener("click", handleGithubAction);
els.closeClone.addEventListener("click", () =>
  els.cloneModal.classList.add("hidden"),
);
els.cancelClone.addEventListener("click", () =>
  els.cloneModal.classList.add("hidden"),
);
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
  await Promise.all([loadConfig(), loadGithubIntegration()]);
  els.providerSelect.value = state.config.provider;
  els.modelInput.value = state.config.custom_model || "";
  els.apiKeyInput.value = "";
  updateApiKeyVisibility();
  els.settingsModal.classList.remove("hidden");
}
function closeSettings() {
  els.settingsModal.classList.add("hidden");
}

function updateApiKeyVisibility() {
  const p = state.providers.find((x) => x.id === els.providerSelect.value);
  if (p && p.needs_api_key) {
    els.apiKeyField.classList.remove("hidden");
    els.apiKeyInput.disabled = false;
    els.apiKeyInput.placeholder = p.id.includes("anthropic")
      ? "sk-ant-..."
      : "sk-...";
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
  } finally {
    els.saveSettings.disabled = false;
  }
}

async function loadConfig() {
  const [provR, cfgR] = await Promise.all([
    fetch("/api/providers"),
    fetch("/api/config"),
  ]);
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
  if (els.providerBadge) els.providerBadge.textContent = state.config.provider;
  if (els.modelBadge) els.modelBadge.textContent = state.config.model;
  // v3: reflect the active model on the model chip switcher
  if (els.modelSwitcher) {
    const model = state.config.model || "deepseek-v4-flash";
    els.modelSwitcher.querySelectorAll(".model-chip").forEach((c) => {
      c.classList.toggle("model-chip-active", c.dataset.model === model);
    });
  }
  const hasVision = currentModelSupportsVision();
  els.visionIndicator?.classList.toggle("hidden", !hasVision);
  if (els.composerAttach) {
    els.composerAttach.disabled = !hasVision;
    els.composerAttach.title = hasVision
      ? "Anexar imagem ao chat"
      : "O modelo atual não aceita imagens";
  }
  if (!hasVision && state.pendingImages.length) clearChatImages();
}

// ============================================================
// WIRE UP
// ============================================================

els.newChatBtn.addEventListener("click", newSession);
els.railWorkspace.addEventListener("click", () => {
  els.workspacePanel.classList.toggle("mobile-open");
});
els.openSettings.addEventListener("click", openSettings);
els.userProfileBanner.addEventListener("click", openProfile);
els.closeProfile.addEventListener("click", closeProfile);
els.closeProfileBtn.addEventListener("click", closeProfile);
els.profileModal.addEventListener("click", (e) => {
  if (e.target === els.profileModal) closeProfile();
});
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

els.closeFile.addEventListener("click", () =>
  els.fileModal.classList.add("hidden"),
);
els.fileModal.addEventListener("click", (e) => {
  if (e.target === els.fileModal) els.fileModal.classList.add("hidden");
});
els.cloneModal.addEventListener("click", (e) => {
  if (e.target === els.cloneModal) els.cloneModal.classList.add("hidden");
});
els.settingsModal.addEventListener("click", (e) => {
  if (e.target === els.settingsModal) closeSettings();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!els.settingsModal.classList.contains("hidden")) closeSettings();
    else if (!els.cloneModal.classList.contains("hidden"))
      els.cloneModal.classList.add("hidden");
    else if (!els.fileModal.classList.contains("hidden"))
      els.fileModal.classList.add("hidden");
    else if (!els.confirmModal.classList.contains("hidden")) hideConfirm();
  }
});

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage();
});
els.stopBtn.addEventListener("click", () => {
  if (state.abortController) state.abortController.abort();
});

// Confirm modal event listeners
if (els.closeConfirm) {
  els.closeConfirm.addEventListener("click", hideConfirm);
}
if (els.cancelConfirm) {
  els.cancelConfirm.addEventListener("click", hideConfirm);
}
if (els.confirmDelete) {
  els.confirmDelete.addEventListener("click", async () => {
    if (!state.confirmCallback) return;
    const callback = state.confirmCallback;
    els.confirmDelete.disabled = true;
    try {
      await callback();
      hideConfirm();
    } catch (error) {
      els.confirmDetail.textContent =
        error.message || "Não foi possível concluir a ação.";
      els.confirmDetail.classList.remove("hidden");
    } finally {
      els.confirmDelete.disabled = false;
    }
  });
}
if (els.confirmModal) {
  els.confirmModal.addEventListener("click", (e) => {
    if (e.target === els.confirmModal) hideConfirm();
  });
}

// Trash button
if (els.railTrash) {
  els.railTrash.addEventListener("click", async () => {
    state.trashMode = !state.trashMode;
    els.railTrash.classList.toggle("active", state.trashMode);
    els.railTrash.setAttribute("aria-pressed", String(state.trashMode));
    await loadSessions();
  });
}
els.input.addEventListener("input", autoSize);
els.input.addEventListener("paste", async (e) => {
  const imageFiles = Array.from(e.clipboardData?.items || [])
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter(Boolean);
  if (imageFiles.length) {
    e.preventDefault();
    await addChatImages(imageFiles);
  }
});
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
  try {
    localStorage.setItem("stupidex-theme", theme);
  } catch (e) {}
}

els.themeToggle.addEventListener("click", () => {
  const current =
    document.documentElement.getAttribute("data-theme") === "light"
      ? "light"
      : "dark";
  setTheme(current === "light" ? "dark" : "light");
});

// Apply saved theme on boot
(function () {
  try {
    const saved = localStorage.getItem("stupidex-theme");
    if (saved) setTheme(saved);
  } catch (e) {}
})();

// ============================================================
// RESEARCH PANEL (v3 premium UI)
// ============================================================

function setResearchVisible(visible) {
  if (!els.researchPanel) return;
  if (visible) {
    els.researchPanel.classList.remove("research-hidden");
    els.researchToggle && els.researchToggle.classList.add("is-active");
  } else {
    els.researchPanel.classList.add("research-hidden");
    els.researchToggle && els.researchToggle.classList.remove("is-active");
  }
  try {
    localStorage.setItem("stupidex-research-open", visible ? "1" : "0");
  } catch (e) {}
}

if (els.researchToggle) {
  els.researchToggle.addEventListener("click", () => {
    const isHidden = els.researchPanel.classList.contains("research-hidden");
    setResearchVisible(isHidden);
  });
}
if (els.closeResearch) {
  els.closeResearch.addEventListener("click", () => setResearchVisible(false));
}

// Research tabs
els.researchTabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    els.researchTabs.forEach((t) => t.classList.remove("research-tab-active"));
    tab.classList.add("research-tab-active");
    const target = tab.dataset.tab;
    Object.entries(els.researchTabPanes).forEach(([k, pane]) => {
      if (!pane) return;
      pane.classList.toggle("hidden", k !== target);
    });
  });
});

// Ephemeral image attachments for the current chat message.
if (els.composerAttach) {
  els.composerAttach.addEventListener("click", () =>
    els.composerImageInput?.click(),
  );
}
if (els.composerSearch) {
  els.composerSearch.setAttribute("aria-pressed", "false");
  els.composerSearch.addEventListener("click", () => {
    state.webSearchEnabled = !state.webSearchEnabled;
    els.composerSearch.classList.toggle("is-active", state.webSearchEnabled);
    els.composerSearch.setAttribute(
      "aria-pressed",
      String(state.webSearchEnabled),
    );
    els.composerSearch.title = state.webSearchEnabled
      ? "Pesquisa web ativa"
      : "Ativar pesquisa web";
    els.webSearchIndicator?.classList.toggle("hidden", !state.webSearchEnabled);
  });
}
els.composerImageInput?.addEventListener("change", async () => {
  await addChatImages(els.composerImageInput.files);
  els.composerImageInput.value = "";
});
for (const eventName of ["dragenter", "dragover"]) {
  els.composerInputWrap?.addEventListener(eventName, (e) => {
    const hasImages = Array.from(e.dataTransfer?.items || []).some(
      (item) => item.kind === "file" && item.type.startsWith("image/"),
    );
    if (!hasImages) return;
    e.preventDefault();
    e.stopPropagation();
    els.composerInputWrap.classList.add("image-drag-active");
  });
}
els.composerInputWrap?.addEventListener("dragleave", (e) => {
  if (!els.composerInputWrap.contains(e.relatedTarget)) {
    els.composerInputWrap.classList.remove("image-drag-active");
  }
});
els.composerInputWrap?.addEventListener("drop", async (e) => {
  const images = Array.from(e.dataTransfer?.files || []).filter((file) =>
    file.type.startsWith("image/"),
  );
  if (!images.length) return;
  e.preventDefault();
  e.stopPropagation();
  state.dropCounter = 0;
  els.dropOverlay.classList.add("hidden");
  els.composerInputWrap.classList.remove("image-drag-active");
  await addChatImages(images);
});
if (els.composerViewProject) {
  els.composerViewProject.addEventListener("click", () => {
    // Visual placeholder — just open settings for now
    openSettings();
  });
}

// Model chip switcher
if (els.modelSwitcher) {
  els.modelSwitcher.addEventListener("click", async (e) => {
    const chip = e.target.closest(".model-chip");
    if (!chip) return;
    const model = chip.dataset.model;
    const previous = state.config;
    state.config = {
      ...state.config,
      provider: model,
      model,
      custom_model: "",
    };
    updateBadges();
    try {
      const response = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: model, custom_model: "" }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      state.config = await response.json();
      updateBadges();
    } catch {
      state.config = previous;
      updateBadges();
    }
  });
}

// Shell widget wire-up
els.shellRun.addEventListener("click", () => runShellCommand(els.shellInput.value));
els.shellInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    runShellCommand(els.shellInput.value);
  }
});
els.shellClear.addEventListener("click", clearShell);

// Periodic file tree refresh (every 45s)
setInterval(() => {
  const id = state.workspaces?.active_id;
  if (id) loadTree(id);
}, 45000);

// Restore research panel state
(function () {
  try {
    if (localStorage.getItem("stupidex-research-open") === "1") {
      setResearchVisible(true);
    }
  } catch (e) {}
})();

// ============================================================
// SEARCH
// ============================================================

let searchDebounce = null;
function bindSearchInput(input) {
  if (!input) return;
  input.addEventListener("input", (e) => {
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
}
// Existing legacy input (kept in DOM for backwards-compat)
bindSearchInput($("search-input"));
// New: rail button focuses an inline search field in the workspace panel
const railSearch = $("rail-search");
if (railSearch) {
  railSearch.addEventListener("click", () => {
    // The workspace panel already lists all sessions; we just focus the
    // session list as a soft "search" affordance. Future: show an input.
    const firstSession = document.querySelector(".session-item");
    if (firstSession)
      firstSession.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
}

// ============================================================
// KEYBOARD SHORTCUTS
// ============================================================

document.addEventListener("keydown", (e) => {
  // Ctrl/Cmd+K → focus composer input
  if ((e.ctrlKey || e.metaKey) && e.key === "k") {
    e.preventDefault();
    if (els.input) {
      els.input.focus();
      els.input.select();
    }
  }
  // Ctrl/Cmd+Shift+N → new chat
  if (
    (e.ctrlKey || e.metaKey) &&
    e.shiftKey &&
    (e.key === "N" || e.key === "n")
  ) {
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

function getToken() {
  try {
    return localStorage.getItem(AUTH_KEY) || "";
  } catch {
    return "";
  }
}
function setToken(t) {
  try {
    localStorage.setItem(AUTH_KEY, t);
  } catch {}
}
function clearToken() {
  try {
    localStorage.removeItem(AUTH_KEY);
  } catch {}
}

// Monkey-patch fetch to inject auth header on all API calls
const _origFetch = window.fetch;
window.fetch = function (url, opts = {}) {
  const token = getToken();
  const isApi =
    typeof url === "string" &&
    (url.startsWith("/api/") || url.includes("/api/"));
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
  try {
    const token = getToken();
    const r = await _origFetch("/api/auth/me", {
      headers: token ? { Authorization: "Bearer " + token } : {},
    });
    if (r.ok && token) clearToken();
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
  const token = getToken();
  try {
    await _origFetch("/api/auth/logout", {
      method: "POST",
      headers: token ? { Authorization: "Bearer " + token } : {},
    });
  } catch {}
  clearToken();
  if (state.abortController)
    try {
      state.abortController.abort();
    } catch {}
  state = {
    providers: [],
    config: {
      provider: "deepseek-v4-flash",
      model: "deepseek-v4-flash",
      has_api_key: false,
    },
    workspaces: { workspaces: [], active_id: null },
    sessions: [],
    currentSessionId: null,
    busy: false,
    abortController: null,
    tree: [],
    dropCounter: 0,
    pendingImages: [],
    confirmCallback: null,
    trashMode: false,
    webSearchEnabled: false,
    github: {
      configured: false,
      connected: false,
      login: "",
      avatar_url: "",
    },
    user: {
      username: "",
      email: "",
      avatar_url: "",
      oauth_provider: "",
    },
  };
  els.messages.innerHTML = "";
  els.sessionList.innerHTML = "";
  els.workspaceList.innerHTML = "";
  els.treeContainer.innerHTML = "";
  updateConversationHeader();
  renderChatImagePreviews();
  els.composerSearch?.classList.remove("is-active");
  els.webSearchIndicator?.classList.add("hidden");
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
  if (!email || !password) {
    setLoginError("Preencha email e senha");
    return;
  }
  try {
    const r = await _origFetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: email, password }),
    });
    return handleLoginResponse(r, email);
  } catch (err) {
    setLoginError("Erro de rede");
  }
});

async function handleLoginResponse(r, email) {
  const data = await r.json().catch(() => ({}));
  if (r.ok) {
    clearToken();
    showApp();
    await bootApp();
    return;
  }
  if (
    r.status === 409 ||
    (data.error && data.error.includes("already taken"))
  ) {
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
    if (r.ok) {
      clearToken();
      showApp();
      await bootApp();
    } else {
      setLoginError(data.error || "Credenciais inválidas");
    }
  } catch {
    setLoginError("Erro de rede");
  }
}

// ============================================================
// SHELL
// ============================================================

async function runShellCommand(cmd) {
  if (!cmd.trim()) return;
  const wsId = state.workspaces.active_id;
  if (!wsId) {
    appendShellLine("error", "Nenhum workspace ativo.");
    return;
  }
  appendShellLine("prompt", `$ ${cmd}`);
  try {
    const r = await fetch(`/api/workspaces/${wsId}/shell`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: cmd }),
    });
    const data = await r.json();
    if (data.output) {
      const lines = data.output.split("\n");
      for (const line of lines) {
        if (line.startsWith("stdout:") || line.startsWith("stderr:")) continue;
        if (line.startsWith("[exit")) { appendShellLine("exit", line); continue; }
        if (line.includes("SECURITY") || line.includes("ERROR:")) { appendShellLine("error", line); continue; }
        if (line.trim()) appendShellLine("stdout", line);
      }
    }
    if (data.error) appendShellLine("error", data.error);
    if (data.tree_changed) loadTree(wsId);
  } catch (e) {
    appendShellLine("error", `Erro: ${e.message}`);
  }
  els.shellInput.value = "";
  els.shellOutput.scrollTop = els.shellOutput.scrollHeight;
}

function appendShellLine(type, text) {
  const line = document.createElement("span");
  line.className = `shell-line ${type}`;
  line.textContent = text;
  els.shellOutput.appendChild(line);
}

function clearShell() {
  els.shellOutput.innerHTML = "";
}

async function loadUserProfile() {
  try {
    const response = await fetch("/api/auth/me");
    if (response.ok) {
      const data = await response.json();
      state.user = data.user || state.user;
      updateUserProfileBanner();
    }
  } catch (e) {
    console.error("Failed to load user profile:", e);
  }
}

function updateUserProfileBanner() {
  if (!els.userProfileBanner) return;

  const { avatar_url } = state.user;

  if (avatar_url) {
    els.userProfileAvatar.src = avatar_url;
    els.userProfileAvatar.style.display = "block";
    els.userProfileBanner.classList.remove("hidden");
  } else {
    els.userProfileAvatar.src = "";
    els.userProfileAvatar.style.display = "none";
    els.userProfileBanner.classList.add("hidden");
  }
}

function openProfile() {
  const u = state.user || {};
  els.profileModalAvatar.src = u.avatar_url || "";
  els.profileUsername.textContent = u.username || u.email || "—";
  els.profileEmail.textContent = u.email || "—";
  const provider = u.oauth_provider || "email";
  els.profileProvider.textContent = provider === "google" ? "Google" : provider === "github" ? "GitHub" : "Email e senha";
  els.profileOauthBadge.textContent = provider === "google" ? "⋮ Gmail" : provider === "github" ? "⊞ GitHub" : "⊡ Local";
  const created = u.created_at ? new Date(u.created_at * 1000) : null;
  els.profileMemberSince.textContent = created ? created.toLocaleDateString("pt-BR", { year: "numeric", month: "long", day: "numeric" }) : "—";
  const count = (state.workspaces?.workspaces || []).length;
  els.profileWsCount.textContent = count;
  els.profileModal.classList.remove("hidden");
}

function closeProfile() {
  els.profileModal.classList.add("hidden");
}

async function bootApp() {
  await Promise.all([loadConfig(), loadGithubIntegration(), loadUserProfile()]);
  updateBadges();
  await loadWorkspaces();
  await loadSessions();
  const firstActiveSession = state.sessions.find((session) => !session.trashed);
  if (firstActiveSession) {
    await openSession(firstActiveSession.id);
  } else {
    renderWelcome();
    updateConversationHeader();
  }
  showGithubCallbackResult();
  els.input.focus();
}

function showGithubCallbackResult() {
  const params = new URLSearchParams(window.location.search);
  const result = params.get("github");
  if (!result) return;
  const messages = {
    connected: "GitHub conectado. Repositórios privados estão disponíveis.",
    denied: "A conexão com o GitHub foi cancelada.",
    error: "Não foi possível conectar o GitHub. Tente novamente.",
  };
  els.status.textContent = messages[result] || "";
  els.status.classList.toggle("hidden", !messages[result]);
  params.delete("github");
  const query = params.toString();
  const cleanUrl = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  window.history.replaceState({}, "", cleanUrl);
}

(async function init() {
  if (await checkAuth()) {
    showApp();
    const timeout = setTimeout(() => {
      renderWelcome();
    }, 7000);
    try {
      await bootApp();
      clearTimeout(timeout);
    } catch (e) {
      clearTimeout(timeout);
      renderWelcome();
    }
  } else {
    showLogin();
  }
})();
