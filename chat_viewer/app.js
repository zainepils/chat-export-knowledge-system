const state = {
  query: "",
  offset: 0,
  limit: 80,
  total: 0,
  titleOnly: false,
  semantic: false,
  imageGallery: [],
  activeImageIndex: -1,
  accounts: [],
  activeAccount: "",
  items: [],
  activeChatId: null,
  activeConversation: null,
  permanentDelete: false,
  importPath: "",
};

const els = {
  chatCount: document.getElementById("chatCount"),
  accountSelect: document.getElementById("accountSelect"),
  searchInput: document.getElementById("searchInput"),
  titleOnlySearch: document.getElementById("titleOnlySearch"),
  semanticSearch: document.getElementById("semanticSearch"),
  conversationList: document.getElementById("conversationList"),
  loadMoreButton: document.getElementById("loadMoreButton"),
  chatTitle: document.getElementById("chatTitle"),
  chatMeta: document.getElementById("chatMeta"),
  messages: document.getElementById("messages"),
  detailsPanel: document.getElementById("detailsPanel"),
  toggleMetaButton: document.getElementById("toggleMetaButton"),
  copyPathButton: document.getElementById("copyPathButton"),
  scrollTopButton: document.getElementById("scrollTopButton"),
  scrollBottomButton: document.getElementById("scrollBottomButton"),
  binChatButton: document.getElementById("binChatButton"),
  deleteChatButton: document.getElementById("deleteChatButton"),
  imageModal: document.getElementById("imageModal"),
  closeImageModal: document.getElementById("closeImageModal"),
  modalImage: document.getElementById("modalImage"),
  modalCaption: document.getElementById("modalCaption"),
  importExportButton: document.getElementById("importExportButton"),
  importDialog: document.getElementById("importDialog"),
  importForm: document.getElementById("importForm"),
  chooseExportButton: document.getElementById("chooseExportButton"),
  chosenExportName: document.getElementById("chosenExportName"),
  importEmail: document.getElementById("importEmail"),
  importLabel: document.getElementById("importLabel"),
  replaceAccount: document.getElementById("replaceAccount"),
  skipSemantic: document.getElementById("skipSemantic"),
  importStatus: document.getElementById("importStatus"),
  runImportButton: document.getElementById("runImportButton"),
  cancelImportButton: document.getElementById("cancelImportButton"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function shortDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(date);
}

function debounce(fn, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  let size = bytes;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(size >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

async function loadConversations({ reset = false } = {}) {
  if (reset) {
    state.offset = 0;
    state.items = [];
  }
  const params = new URLSearchParams({
    q: state.query,
    limit: String(state.limit),
    offset: String(state.offset),
    titles_only: state.titleOnly ? "1" : "0",
    semantic: state.semantic ? "1" : "0",
  });
  const data = await fetchJson(`/api/conversations?${params}`);
  state.total = data.total;
  state.items = reset ? data.items : [...state.items, ...data.items];
  state.offset = state.items.length;
  renderConversationList();
}

async function loadAccounts() {
  const data = await fetchJson("/api/accounts");
  state.accounts = data.accounts || [];
  state.activeAccount = data.active || "";
  state.permanentDelete = Boolean(data.capabilities?.permanent_delete);
  renderAccounts();
}

function renderAccounts() {
  els.deleteChatButton.hidden = !state.permanentDelete;
  els.accountSelect.innerHTML = state.accounts
    .map((account) => {
      const label = account.ready ? account.label : `${account.label} (not imported yet)`;
      return `<option value="${escapeHtml(account.slug)}" ${account.slug === state.activeAccount ? "selected" : ""}>${escapeHtml(label)}</option>`;
    })
    .join("");
}

async function switchAccount(slug) {
  if (!slug || slug === state.activeAccount) return;
  const data = await postJson("/api/account", { slug });
  state.accounts = data.accounts || [];
  state.permanentDelete = Boolean(data.capabilities?.permanent_delete);
  state.activeAccount = data.active || slug;
  state.offset = 0;
  state.total = 0;
  state.items = [];
  state.activeChatId = null;
  state.activeConversation = null;
  state.imageGallery = [];
  state.activeImageIndex = -1;
  renderAccounts();
  renderConversationList();
  els.chatTitle.textContent = "Select a conversation";
  els.chatMeta.textContent = "Search or pick a chat from the left.";
  els.detailsPanel.classList.add("hidden");
  els.messages.classList.add("empty-state");
  els.messages.innerHTML = `<div class="empty-card"><h3>${escapeHtml(activeAccountLabel())}</h3><p>${activeAccountReady() ? "Account switched." : "This account is ready for import when the export finishes downloading."}</p></div>`;
  await loadConversations({ reset: true });
}

function activeAccountLabel() {
  const account = state.accounts.find((item) => item.slug === state.activeAccount);
  return account ? account.label : "Account";
}

function activeAccountReady() {
  const account = state.accounts.find((item) => item.slug === state.activeAccount);
  return Boolean(account && account.ready);
}

function renderConversationList() {
  els.chatCount.textContent = state.semantic && state.query
    ? `${state.total.toLocaleString()} conversations ranked`
    : `${state.total.toLocaleString()} conversations`;
  els.loadMoreButton.hidden = state.items.length >= state.total;
  els.conversationList.innerHTML = state.items
    .map((item) => {
      const active = item.chat_id === state.activeChatId ? " active" : "";
      const snippet = item.first_user_message || item.last_user_message || "";
      return `
        <button class="chat-row${active}" type="button" data-chat-id="${escapeHtml(item.chat_id)}">
          <span class="chat-row-title">${escapeHtml(item.title || "Untitled")}</span>
          <span class="chat-row-snippet">${escapeHtml(snippet)}</span>
          <span class="chat-row-meta">
            <span>${escapeHtml(shortDate(item.updated_at))}</span>
            <span>${Number(item.message_count || 0).toLocaleString()} msgs</span>
            ${item.asset_count ? `<span>${item.asset_count} assets</span>` : ""}
            ${item.semantic_score !== undefined ? `<span>${Math.round(item.semantic_score * 100)}% match</span>` : ""}
          </span>
        </button>
      `;
    })
    .join("");
}

async function openConversation(chatId) {
  state.activeChatId = chatId;
  renderConversationList();
  els.messages.classList.remove("empty-state");
  els.messages.innerHTML = `<div class="message"><div class="empty-card"><h3>Loading chat...</h3><p>${escapeHtml(chatId)}</p></div></div>`;
  const conversation = await fetchJson(`/api/conversation/${encodeURIComponent(chatId)}`);
  state.activeConversation = conversation;
  renderConversation(conversation);
}

function renderConversation(conversation) {
  els.chatTitle.textContent = conversation.title || "Untitled";
  els.chatMeta.textContent = `${formatDate(conversation.updated_at)} · ${conversation.message_count.toLocaleString()} rendered messages · ${conversation.chat_id}`;
  renderDetails(conversation);
  state.imageGallery = collectImageGallery(conversation);
  state.activeImageIndex = -1;
  els.messages.classList.remove("empty-state");
  els.messages.innerHTML = `${renderConversationFiles(conversation.assets || [])}${conversation.messages.map(renderMessage).join("")}`;
  jumpToBottom();
}

function collectImageGallery(conversation) {
  const seen = new Set();
  const images = [];
  const add = (attachment) => {
    if (!attachment || !attachment.is_image || !attachment.preview_url || seen.has(attachment.preview_url)) return;
    seen.add(attachment.preview_url);
    images.push({
      url: attachment.preview_url,
      caption: attachment.filename || attachment.clean_filename || attachment.asset_id || "Image",
      path: attachment.path || attachment.source_path || "",
    });
  };
  (conversation.assets || []).forEach(add);
  (conversation.messages || []).forEach((message) => (message.attachments || []).forEach(add));
  return images;
}

function renderDetails(conversation) {
  const details = [
    `Chat ID: ${conversation.chat_id}`,
    `Created: ${conversation.created_at}`,
    `Updated: ${conversation.updated_at}`,
    `Archived: ${conversation.is_archived}`,
    `Starred: ${conversation.is_starred}`,
    `JSON: ${conversation.source_json}`,
    `Markdown: ${conversation.source_markdown}`,
    `Assets: ${(conversation.asset_ids || []).join(", ") || "none detected"}`,
    `Attachment names: ${(conversation.attachment_names || []).join(", ") || "none detected"}`,
  ].join("\n");
  els.detailsPanel.textContent = details;
}

function renderConversationFiles(assets) {
  if (!assets.length) return "";
  return `
    <section class="message file-summary">
      <div class="message-inner">
        <div class="avatar">Files</div>
        <div class="bubble">
          <div class="message-bar">
            <span class="role-name">Files</span>
            <span>${assets.length.toLocaleString()} attached</span>
          </div>
          <div class="attachments">${assets.map(renderAttachment).join("")}</div>
        </div>
      </div>
    </section>
  `;
}

function renderMessage(message) {
  const role = normalizeRole(message.role);
  const avatar = role === "assistant" ? "AI" : role === "user" ? "You" : role.slice(0, 3).toUpperCase();
  const model = message.model ? ` · ${escapeHtml(message.model)}` : "";
  const created = message.created_at ? `${escapeHtml(formatDate(message.created_at))}${model}` : model.replace(/^ · /, "");
  const attachments = (message.attachments || []).length
    ? `<div class="attachments">${message.attachments.map(renderAttachment).join("")}</div>`
    : "";
  return `
    <article class="message ${escapeHtml(role)}">
      <div class="message-inner">
        <div class="avatar">${escapeHtml(avatar)}</div>
        <div class="bubble">
          <div class="message-bar">
            <span class="role-name">${escapeHtml(role)}</span>
            <span>${created}</span>
          </div>
          <div class="content">${renderMarkdown(message.text || "")}</div>
          ${attachments}
        </div>
      </div>
    </article>
  `;
}

function normalizeRole(role) {
  if (["user", "assistant", "system", "tool"].includes(role)) return role;
  return "unknown";
}

function attachmentLabel(attachment) {
  const raw = attachment.filename || attachment.clean_filename || attachment.asset_pointer || attachment.content_type || "attachment";
  const label = String(raw).replace(/^sediment:\/\//, "");
  if (label.length <= 28) return label;
  return `${label.slice(0, 18)}...${label.slice(-6)}`;
}

function renderAttachment(attachment) {
  const label = attachmentLabel(attachment);
  const path = attachment.path || "";
  const sourcePath = attachment.source_path || "";
  const size = formatBytes(attachment.size_bytes);
  const meta = [attachment.is_pdf ? "PDF" : attachment.extension || attachment.content_type, size].filter(Boolean).join(" · ");
  if (attachment.is_image && attachment.preview_url) {
    return `
      <span class="attachment-card image-attachment">
        <button class="image-preview-button" type="button" data-image-url="${escapeHtml(attachment.preview_url)}" data-caption="${escapeHtml(label)}">
          <img src="${escapeHtml(attachment.preview_url)}" alt="${escapeHtml(label)}" loading="lazy" />
        </button>
        <button class="image-file-name" type="button" data-reveal-path="${escapeHtml(path || sourcePath)}">${escapeHtml(label)}</button>
      </span>
    `;
  }
  if (!path && !sourcePath) {
    return `
      <span class="attachment-card unresolved-attachment">
        <span class="file-icon">REF</span>
        <span class="file-copy">
          <span class="file-name">${escapeHtml(label)}</span>
          <span class="file-meta">${escapeHtml(attachment.asset_pointer ? "Unresolved export reference" : "Attachment metadata only")}</span>
        </span>
      </span>
    `;
  }
  return `
    <button class="attachment-card file-attachment" type="button" data-reveal-path="${escapeHtml(path || sourcePath)}">
      <span class="file-icon">${attachment.is_pdf ? "PDF" : "FILE"}</span>
      <span class="file-copy">
        <span class="file-name">${escapeHtml(label)}</span>
        <span class="file-meta">${escapeHtml(meta || "Attachment")}</span>
      </span>
    </button>
  `;
}

function jumpToTop() {
  els.messages.scrollTop = 0;
}

function jumpToBottom() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

function openImageModal(url, caption) {
  const index = state.imageGallery.findIndex((image) => image.url === url);
  state.activeImageIndex = index >= 0 ? index : -1;
  els.modalImage.src = url;
  els.modalImage.alt = caption;
  els.modalCaption.textContent = caption;
  els.imageModal.classList.remove("hidden");
}

function closeImageModal() {
  els.imageModal.classList.add("hidden");
  els.modalImage.removeAttribute("src");
  els.modalCaption.textContent = "";
  state.activeImageIndex = -1;
}

function showGalleryImage(index) {
  if (!state.imageGallery.length) return;
  const nextIndex = (index + state.imageGallery.length) % state.imageGallery.length;
  const image = state.imageGallery[nextIndex];
  state.activeImageIndex = nextIndex;
  els.modalImage.src = image.url;
  els.modalImage.alt = image.caption;
  els.modalCaption.textContent = image.caption;
}

function stepGallery(direction) {
  if (els.imageModal.classList.contains("hidden") || state.activeImageIndex < 0) return;
  showGalleryImage(state.activeImageIndex + direction);
}

async function revealFile(path) {
  if (!path) return;
  await postJson("/api/reveal", { path });
}

async function removeActiveChat(mode) {
  if (!state.activeConversation) return;
  const title = state.activeConversation.title || "Untitled";
  const chatId = state.activeConversation.chat_id;
  if (mode === "bin") {
    const ok = window.confirm(`Move "${title}" to the viewer bin?\n\nThis moves the chat JSON/Markdown and exclusive attached asset files out of the clean archive.`);
    if (!ok) return;
  } else {
    const typed = window.prompt(`Permanently delete "${title}"?\n\nThis deletes the chat JSON/Markdown and exclusive attached asset files. Shared assets are skipped.\n\nType DELETE to continue.`);
    if (typed !== "DELETE") return;
  }
  const result = await postJson(`/api/conversation/${encodeURIComponent(chatId)}/delete`, {
    mode,
    include_raw_asset_sources: false,
  });
  state.items = state.items.filter((item) => item.chat_id !== chatId);
  state.total = Math.max(0, state.total - 1);
  state.activeChatId = null;
  state.activeConversation = null;
  renderConversationList();
  els.chatTitle.textContent = "Select a conversation";
  els.chatMeta.textContent = `${result.changed.length} files changed · ${result.skipped.length} skipped`;
  els.detailsPanel.classList.add("hidden");
  els.messages.classList.add("empty-state");
  els.messages.innerHTML = `<div class="empty-card"><h3>Chat ${mode === "bin" ? "moved to bin" : "deleted"}</h3><p>${escapeHtml(title)}</p></div>`;
}

function renderMarkdown(markdown) {
  if (!markdown.trim()) return "";
  const blocks = [];
  let cursor = 0;
  const fence = /```([\w.+-]*)\n?([\s\S]*?)```/g;
  let match;
  while ((match = fence.exec(markdown))) {
    if (match.index > cursor) {
      blocks.push({ type: "markdown", value: markdown.slice(cursor, match.index) });
    }
    blocks.push({ type: "code", lang: match[1], value: match[2] });
    cursor = fence.lastIndex;
  }
  if (cursor < markdown.length) {
    blocks.push({ type: "markdown", value: markdown.slice(cursor) });
  }
  return blocks
    .map((block) => {
      if (block.type === "code") {
        return `<pre><code>${escapeHtml(block.value.replace(/\n$/, ""))}</code></pre>`;
      }
      return renderMarkdownBlocks(block.value);
    })
    .join("");
}

function renderMarkdownBlocks(text) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let paragraph = [];
  let list = null;
  let quote = [];
  let table = [];

  function flushParagraph() {
    if (!paragraph.length) return;
    html.push(`<p>${inlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  }

  function flushList() {
    if (!list) return;
    html.push(`<${list.type}>${list.items.map((item) => `<li>${inlineMarkdown(item)}</li>`).join("")}</${list.type}>`);
    list = null;
  }

  function flushQuote() {
    if (!quote.length) return;
    html.push(`<blockquote>${quote.map((item) => `<p>${inlineMarkdown(item)}</p>`).join("")}</blockquote>`);
    quote = [];
  }

  function flushTable() {
    if (table.length < 2) {
      table.forEach((row) => paragraph.push(row));
      table = [];
      return;
    }
    const rows = table.map((row) => row.split("|").slice(1, -1).map((cell) => cell.trim()));
    const [head, divider, ...body] = rows;
    const isDivider = divider.every((cell) => /^:?-{3,}:?$/.test(cell));
    if (!isDivider) {
      table.forEach((row) => paragraph.push(row));
      table = [];
      return;
    }
    html.push(`
      <table>
        <thead><tr>${head.map((cell) => `<th>${inlineMarkdown(cell)}</th>`).join("")}</tr></thead>
        <tbody>${body.map((row) => `<tr>${row.map((cell) => `<td>${inlineMarkdown(cell)}</td>`).join("")}</tr>`).join("")}</tbody>
      </table>
    `);
    table = [];
  }

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (!line.trim()) {
      flushTable();
      flushParagraph();
      flushList();
      flushQuote();
      continue;
    }
    if (/^\|.*\|$/.test(line)) {
      flushParagraph();
      flushList();
      flushQuote();
      table.push(line);
      continue;
    }
    flushTable();
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      flushParagraph();
      flushList();
      flushQuote();
      const level = heading[1].length;
      html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }
    const quoteMatch = /^>\s?(.*)$/.exec(line);
    if (quoteMatch) {
      flushParagraph();
      flushList();
      quote.push(quoteMatch[1]);
      continue;
    }
    const bullet = /^[-*]\s+(.+)$/.exec(line);
    const numbered = /^\d+[.)]\s+(.+)$/.exec(line);
    if (bullet || numbered) {
      flushParagraph();
      flushQuote();
      const type = bullet ? "ul" : "ol";
      if (!list || list.type !== type) {
        flushList();
        list = { type, items: [] };
      }
      list.items.push((bullet || numbered)[1]);
      continue;
    }
    flushList();
    flushQuote();
    paragraph.push(line);
  }
  flushTable();
  flushParagraph();
  flushList();
  flushQuote();
  return html.join("");
}

function inlineMarkdown(value) {
  let html = escapeHtml(value);
  const codeSpans = [];
  html = html.replace(/`([^`]+)`/g, (_, code) => {
    const token = `@@CODE${codeSpans.length}@@`;
    codeSpans.push(`<code>${code}</code>`);
    return token;
  });
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  codeSpans.forEach((code, index) => {
    html = html.replace(`@@CODE${index}@@`, code);
  });
  return html;
}

els.conversationList.addEventListener("click", (event) => {
  const row = event.target.closest(".chat-row");
  if (!row) return;
  openConversation(row.dataset.chatId).catch((error) => {
    els.messages.innerHTML = `<div class="message"><div class="empty-card"><h3>Could not open chat</h3><p>${escapeHtml(error.message)}</p></div></div>`;
  });
});

els.messages.addEventListener("click", (event) => {
  const imageButton = event.target.closest(".image-preview-button");
  if (imageButton) {
    openImageModal(imageButton.dataset.imageUrl, imageButton.dataset.caption || "Image");
    return;
  }
  const fileButton = event.target.closest(".file-attachment, .image-file-name");
  if (fileButton) {
    revealFile(fileButton.dataset.revealPath).catch((error) => {
      window.alert(`Could not reveal file: ${error.message}`);
    });
  }
});

els.loadMoreButton.addEventListener("click", () => {
  loadConversations().catch(console.error);
});

els.accountSelect.addEventListener("change", () => {
  switchAccount(els.accountSelect.value).catch((error) => {
    window.alert(`Could not switch account: ${error.message}`);
    renderAccounts();
  });
});

els.searchInput.addEventListener(
  "input",
  debounce(() => {
    state.query = els.searchInput.value.trim();
    loadConversations({ reset: true }).catch(console.error);
  }, 220)
);

els.titleOnlySearch.addEventListener("change", () => {
  state.titleOnly = els.titleOnlySearch.checked;
  loadConversations({ reset: true }).catch(console.error);
});

els.semanticSearch.addEventListener("change", () => {
  state.semantic = els.semanticSearch.checked;
  els.titleOnlySearch.disabled = state.semantic;
  if (state.semantic) {
    state.titleOnly = false;
    els.titleOnlySearch.checked = false;
  }
  loadConversations({ reset: true }).catch(console.error);
});

els.toggleMetaButton.addEventListener("click", () => {
  els.detailsPanel.classList.toggle("hidden");
});

els.scrollTopButton.addEventListener("click", jumpToTop);
els.scrollBottomButton.addEventListener("click", jumpToBottom);

els.binChatButton.addEventListener("click", () => {
  removeActiveChat("bin").catch((error) => window.alert(`Could not move chat to bin: ${error.message}`));
});

els.deleteChatButton.addEventListener("click", () => {
  removeActiveChat("delete").catch((error) => window.alert(`Could not delete chat: ${error.message}`));
});

els.closeImageModal.addEventListener("click", closeImageModal);
els.imageModal.addEventListener("click", (event) => {
  if (event.target === els.imageModal) closeImageModal();
});
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeImageModal();
  if (event.key === "ArrowLeft") stepGallery(-1);
  if (event.key === "ArrowRight") stepGallery(1);
});

els.copyPathButton.addEventListener("click", async () => {
  if (!state.activeConversation) return;
  const text = [
    state.activeConversation.source_json,
    state.activeConversation.source_markdown,
  ].join("\n");
  await navigator.clipboard.writeText(text);
  els.copyPathButton.textContent = "Copied";
  setTimeout(() => {
    els.copyPathButton.textContent = "Copy paths";
  }, 900);
});

function enableNativeImport() {
  if (window.pywebview?.api?.pick_export) {
    els.importExportButton.hidden = false;
  }
}

window.addEventListener("pywebviewready", enableNativeImport);
enableNativeImport();

els.importExportButton.addEventListener("click", () => {
  els.importStatus.textContent = "";
  els.importDialog.showModal();
});

els.cancelImportButton.addEventListener("click", () => els.importDialog.close());

els.chooseExportButton.addEventListener("click", async () => {
  try {
    const path = await window.pywebview.api.pick_export();
    if (!path) return;
    state.importPath = path;
    els.chosenExportName.textContent = path.split(/[\\/]/).pop();
  } catch (error) {
    els.importStatus.textContent = error.message;
  }
});

els.importForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.importPath) {
    els.importStatus.textContent = "Choose an export ZIP first.";
    return;
  }
  const email = els.importEmail.value.trim();
  const label = els.importLabel.value.trim() || email;
  els.runImportButton.disabled = true;
  els.cancelImportButton.disabled = true;
  els.importStatus.textContent = "Importing export. This may take several minutes.";
  try {
    const result = await window.pywebview.api.import_export(
      state.importPath, email, label, els.replaceAccount.checked, els.skipSemantic.checked
    );
    await postJson("/api/account", { slug: result.slug });
    await loadAccounts();
    state.activeChatId = null;
    state.activeConversation = null;
    els.chatTitle.textContent = "Select a conversation";
    els.chatMeta.textContent = "Search or pick a chat from the left.";
    els.messages.innerHTML = "";
    await loadConversations({ reset: true });
    els.importDialog.close();
  } catch (error) {
    els.importStatus.textContent = error.message || String(error);
  } finally {
    els.runImportButton.disabled = false;
    els.cancelImportButton.disabled = false;
  }
});

loadAccounts()
  .then(async () => {
    const params = new URLSearchParams(window.location.search);
    const query = params.get("q") || "";
    state.query = query;
    els.searchInput.value = query;
    if (params.get("semantic") === "1") {
      state.semantic = true;
      els.semanticSearch.checked = true;
      els.titleOnlySearch.disabled = true;
    }
    await loadConversations({ reset: true });
    const chatId = params.get("chat");
    if (chatId) await openConversation(chatId);
  })
  .catch((error) => {
    els.chatCount.textContent = "Could not load archive";
    els.conversationList.innerHTML = `<div class="empty-card"><p>${escapeHtml(error.message)}</p></div>`;
  });
