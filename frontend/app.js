/* ============ 个人知识管理工作台 · 前端逻辑 v2 ============ */
"use strict";

/* ---------- 分类颜色 ---------- */
const CAT_COLORS = {
  "技术开发": "#0a84ff",
  "人工智能": "#bf5af2",
  "金融投资": "#ff9f0a",
  "营销运营": "#ff375f",
  "教育学习": "#30d158",
  "健康养生": "#64d2ff",
  "职场管理": "#ffd60a",
  "生活随笔": "#ff9f0a",
  "未分类": "#8e8e93",
};
const EXT_META = {
  pdf: ["PDF", "#ff453a"], docx: ["DOCX", "#0a84ff"], md: ["MD", "#30d158"],
  markdown: ["MD", "#30d158"], txt: ["TXT", "#8e8e93"], html: ["HTML", "#bf5af2"],
  htm: ["HTML", "#bf5af2"],
  xlsx: ["XLSX", "#30d158"], xls: ["XLS", "#30d158"], pptx: ["PPTX", "#ff9f0a"],
  png: ["IMG", "#bf5af2"], jpg: ["IMG", "#bf5af2"], jpeg: ["IMG", "#bf5af2"],
  bmp: ["IMG", "#bf5af2"], webp: ["IMG", "#bf5af2"], tiff: ["IMG", "#bf5af2"],
  tif: ["IMG", "#bf5af2"], gif: ["IMG", "#bf5af2"],
};
const DEFAULT_EXT = ["FILE", "#636366"];

/* ---------- 状态 ---------- */
const state = {
  category: "全部",
  q: "",
  tag: "",
  documents: [],
  categories: {},
  topTags: [],
  currentDoc: null,
  deletingId: null,
  drawerOpen: false,
  modalOpen: false,
  lastFocused: null,     // 打开抽屉/弹窗前获得焦点的元素（关闭后恢复）
  uploadBusy: false,
  searchTimer: null,
  previewUrl: null,   // 源文件预览 blob URL（关闭抽屉时回收）
};

let pendingLogin = null;  // 登录成功等待"记住我"选择（必须在 boot 之前声明，避免 TDZ）

/* ---------- DOM ---------- */
const $ = (id) => document.getElementById(id);

/* ---------- 登录态存取（记住我 → localStorage 持久；仅本次 → sessionStorage） ---------- */
function saveToken(token, remember) {
  if (remember) {
    localStorage.setItem("kb_token", token);
    sessionStorage.removeItem("kb_token");
  } else {
    sessionStorage.setItem("kb_token", token);
    localStorage.removeItem("kb_token");
  }
}
function getToken() {
  return sessionStorage.getItem("kb_token") || localStorage.getItem("kb_token");
}
function clearToken() {
  sessionStorage.removeItem("kb_token");
  localStorage.removeItem("kb_token");
}

/* ---------- API ---------- */
async function api(url, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  const token = getToken();
  if (token) headers["Authorization"] = "Bearer " + token;
  const res = await fetch(url, { ...opts, headers });
  if (res.status === 401 && !url.startsWith("/api/auth/login")) {
    // 会话失效（未登录/过期/被踢出）→ 清 token 回登录页
    clearToken();
    showAuthGate("login");
  }
  if (!res.ok) {
    let detail = `请求失败 ${res.status}`;
    try { const d = await res.json(); if (d.detail) detail = d.detail; } catch (e) { /* ignore */ }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

/* ---------- 工具 ---------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
/* 高亮搜索词（先按原文切分再逐段转义，避免实体错位） */
function hl(text, q) {
  if (!q) return esc(text || "");
  const parts = String(text || "").split(q);
  return parts
    .map((p, i) => (i ? "<mark>" + esc(q) + "</mark>" : "") + esc(p))
    .join("");
}
function fmtSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1048576).toFixed(1) + " MB";
}
function fmtWords(n) {
  if (n >= 10000) return (n / 10000).toFixed(1) + "w";
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return String(n);
}
function fmtDate(s) {
  if (!s) return "";
  return s.slice(5, 10).replace("-", "/");
}
function catColor(cat) {
  return CAT_COLORS[cat] || "#8e8e93";
}
function isTyping(el) {
  return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
}

let toastTimer = null;
function toast(msg, ms = 2200) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  requestAnimationFrame(() => t.classList.add("show"));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    t.classList.remove("show");
    setTimeout(() => (t.hidden = true), 350);
  }, ms);
}

/* ---------- 加载数据 ---------- */
async function loadAll() {
  await Promise.all([loadStats(), loadCategories(), loadDocuments()]);
}

async function loadStats() {
  try {
    const d = await api("/api/stats");
    $("statTotal").textContent = d.total;
    $("statWords").textContent = fmtWords(d.total_words);
    $("statSize").textContent = fmtSize(d.total_size);
    state.topTags = d.top_tags || [];
    renderTagCloud();
  } catch (e) { /* ignore */ }
}

async function loadCategories() {
  try {
    const d = await api("/api/categories");
    state.categories = d.categories || {};
    renderCatNav(d.total);
  } catch (e) { /* ignore */ }
}

async function loadDocuments() {
  // 显示搜索进度条（有筛选/搜索词时）
  const busy = state.q || state.category !== "全部" || state.tag;
  $("searchProgress").hidden = !busy;
  const params = new URLSearchParams();
  if (state.category !== "全部") params.set("category", state.category);
  if (state.q) params.set("q", state.q);
  if (state.tag) params.set("tag", state.tag);
  const d = await api("/api/documents?" + params.toString());
  state.documents = d.items || [];
  $("searchProgress").hidden = true;
  renderDocuments();
}

/* ---------- 渲染：分类导航（键盘可达） ---------- */
function renderCatNav(total) {
  const nav = $("catNav");
  const counts = { "全部": total, ...state.categories };
  const order = ["全部", "技术开发", "人工智能", "金融投资", "营销运营", "教育学习", "健康养生", "职场管理", "生活随笔", "未分类"];
  const cats = order.filter((c) => counts[c] !== undefined);
  nav.innerHTML = cats
    .map(
      (c) => `
      <div class="cat-item ${state.category === c ? "active" : ""}" data-cat="${esc(c)}" role="button" tabindex="0" aria-pressed="${state.category === c}">
        <span class="dot" style="background:${catColor(c)}"></span>
        <span class="name">${esc(c)}</span>
        <span class="cnt">${counts[c] ?? 0}</span>
      </div>`
    )
    .join("");
  nav.querySelectorAll(".cat-item").forEach((el) => {
    const activate = () => {
      state.category = el.dataset.cat;
      state.tag = "";
      renderCatNav(total);
      loadDocuments();
    };
    el.addEventListener("click", activate);
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); }
    });
  });
}

/* ---------- 渲染：标签云（键盘可达） ---------- */
function renderTagCloud() {
  const cloud = $("tagCloud");
  const tags = state.topTags.slice(0, 24);
  cloud.innerHTML = tags
    .map(
      (t) =>
        `<span class="tag-chip ${state.tag === t.name ? "active" : ""}" data-tag="${esc(t.name)}" role="button" tabindex="0" aria-pressed="${state.tag === t.name}">#${esc(t.name)}</span>`
    )
    .join("");
  cloud.querySelectorAll(".tag-chip").forEach((el) => {
    const toggle = () => {
      state.tag = state.tag === el.dataset.tag ? "" : el.dataset.tag;
      state.category = "全部";
      renderCatNav(state.categories.total ?? 0);
      renderTagCloud();
      loadDocuments();
    };
    el.addEventListener("click", toggle);
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    });
  });
}

/* ---------- 渲染：文档列表（键盘可达卡片） ---------- */
function renderDocuments() {
  const grid = $("docGrid");
  const empty = $("emptyState");
  const meta = $("docMeta");

  const n = state.documents.length;
  const hasFilter = state.q || state.category !== "全部" || state.tag;
  let metaText = `共 <b>${n}</b> 篇文档`;
  if (state.category !== "全部") metaText += ` · 分类 <b>${esc(state.category)}</b>`;
  if (state.tag) metaText += ` · 标签 <b>#${esc(state.tag)}</b>`;
  if (state.q) metaText += ` · 搜索 "<b>${esc(state.q)}</b>"`;
  meta.innerHTML = metaText;

  if (n === 0) {
    grid.innerHTML = "";
    empty.hidden = false;
    $("resetFilter").hidden = !hasFilter;
    if (hasFilter) {
      $("emptyTitle").textContent = "没有匹配的文档";
      $("emptySub").textContent = "试试文件名、文件夹路径或正文里的任意词";
    } else {
      $("emptyTitle").textContent = "还没有文档";
      $("emptySub").textContent = "把收藏的报告、笔记拖进来，自动总结并分类";
    }
    return;
  }
  empty.hidden = true;

  grid.innerHTML = state.documents
    .map((d, i) => {
      const ext = (d.ext || "").replace(".", "").toLowerCase();
      const [label, color] = EXT_META[ext] || DEFAULT_EXT;
      const cat = d.category || "未分类";
      const kw = (d.keywords || []).slice(0, 3);
      const title = hl(d.title || d.filename, state.q);
      // 命中来源徽标：文件名 / 路径 / 分类 / 标签 / 内容…
      const hits = (d.matched || [])
        .slice(0, 4)
        .map((m) => `<span class="hit-badge">${esc(m)}</span>`)
        .join("");
      // 优先展示内容命中片段，否则展示摘要（均高亮）
      const body = d.snippet ? hl(d.snippet, state.q) : hl(d.summary, state.q);
      const nameLine = d.path
        ? `<span class="doc-path" title="${esc(d.path)}">${hl(d.path, state.q)}</span>`
        : esc(d.filename);
      return `
      <div class="doc-card" data-id="${d.id}" tabindex="0" role="button" aria-label="查看 ${esc(d.title || d.filename)}" style="animation-delay:${Math.min(i * 30, 300)}ms">
        <div class="doc-top">
          <div class="doc-file-icon" style="background:${color}">${label}</div>
          <div style="min-width:0;flex:1">
            <div class="doc-title">${title}</div>
            <div class="doc-filename">${nameLine} · ${fmtSize(d.file_size)}</div>
          </div>
        </div>
        <div class="doc-summary">${body || "（暂无摘要）"}</div>
        <div class="doc-bottom">
          <span class="cat-badge" style="background:${catColor(cat)}">${esc(cat)}</span>
          ${hits}
          ${kw.map((k) => `<span class="keyword-tag">${esc(k)}</span>`).join("")}
          <span class="doc-date">${fmtDate(d.created_at)}</span>
        </div>
      </div>`;
    })
    .join("");

  grid.querySelectorAll(".doc-card").forEach((el) => {
    const open = () => openDrawer(Number(el.dataset.id));
    el.addEventListener("click", open);
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    });
  });
}

/* ---------- 上传（阶段状态机 + 失败重试） ---------- */
function queueItem(file) {
  const el = document.createElement("div");
  el.className = "upload-item running";
  el.innerHTML = `
    <div class="u-row">
      <div class="u-name">${esc(file.name)}</div>
      <div class="u-state">上传中</div>
    </div>
    <div class="u-bar"><i style="width:30%"></i></div>
    <div class="u-status"></div>`;
  $("uploadQueue").appendChild(el);
  return { file, el, bar: el.querySelector("i"), stateEl: el.querySelector(".u-state"), status: el.querySelector(".u-status") };
}

function setUploadState(item, cls, stateText, width) {
  item.el.classList.remove("running", "analyzing", "done", "fail");
  item.el.classList.add(cls);
  item.stateEl.textContent = stateText;
  item.bar.style.width = width;
}

async function processOne(item) {
  setUploadState(item, "running", "上传中", 30);
  item.status.textContent = "正在上传…";
  try {
    const fd = new FormData();
    fd.append("files", item.file);
    // 保留原始文件夹路径（拖入文件夹时 webkitRelativePath 形如 笔记/2024/总结.md）
    fd.append("paths", item.file.webkitRelativePath || item.file.name || "");
    const res = await api("/api/upload", { method: "POST", body: fd });
    const r = res.results[0];
    if (r.ok) {
      // 进入分析阶段（后端已同步完成，短暂展示分析状态增加反馈层次）
      setUploadState(item, "analyzing", "分析中", 85);
      await new Promise((resolve) => setTimeout(resolve, 700));
      setUploadState(item, "done", "完成", 100);
      item.status.textContent = r.low_text
        ? "已入库 · 未能提取有效文本"
        : `已入库 · ${r.category} · ${(r.keywords || []).slice(0, 3).join(" / ") || "无关键词"}`;
      return true;
    }
    throw new Error(r.error || "处理失败");
  } catch (e) {
    setUploadState(item, "fail", "失败", 100);
    const retry = document.createElement("button");
    retry.className = "retry-btn";
    retry.textContent = "重试";
    retry.addEventListener("click", () => processOne(item));
    item.status.textContent = e.message + " ";
    item.status.appendChild(retry);
    return false;
  }
}

async function uploadFiles(fileList) {
  const files = Array.from(fileList).filter(
    (f) => /\.(pdf|docx|md|markdown|txt|html|htm|xlsx|xls|pptx|png|jpg|jpeg|bmp|webp|tiff|tif|gif)$/i.test(f.name)
  );
  if (files.length !== fileList.length) toast("已忽略不支持的文件类型");
  if (files.length === 0) return;

  state.uploadBusy = true;
  $("uploadBtn").classList.add("loading");
  const items = files.map(queueItem);
  let success = 0;
  for (const item of items) {
    if (await processOne(item)) success++;
  }
  state.uploadBusy = false;
  $("uploadBtn").classList.remove("loading");

  await loadAll();
  toast(`成功导入 ${success} 篇文档`);
  setTimeout(() => {
    items.forEach((item) => {
      if (!item.el.classList.contains("fail")) item.el.classList.add("leaving");
    });
    setTimeout(() => items.forEach((i) => i.el.remove()), 450);
  }, 7000);
}

/* ---------- 详情抽屉（焦点管理 + 键盘闭环） ---------- */
const PREVIEW_IMAGE = new Set(["png", "jpg", "jpeg", "bmp", "webp", "tiff", "tif", "gif"]);
const PREVIEW_NO = new Set(["xlsx", "xls", "pptx"]);

/* 源文件预览：fetch 带鉴权头取 blob → objectURL（img/iframe 无法带自定义 header） */
async function loadFileBlob(stored_name) {
  const res = await fetch("/api/files/" + encodeURIComponent(stored_name), {
    headers: { Authorization: "Bearer " + getToken() },
  });
  if (!res.ok) throw new Error("加载失败 " + res.status);
  const blob = await res.blob();
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = URL.createObjectURL(blob);
  return state.previewUrl;
}

async function openDrawer(id) {
  state.lastFocused = document.activeElement;
  const d = await api(`/api/documents/${id}`);
  state.currentDoc = d.item;
  renderDrawer(d.item);
  $("drawerMask").hidden = false;
  state.drawerOpen = true;
  requestAnimationFrame(() => {
    $("drawerMask").classList.add("show");
    $("drawer").classList.add("open");
    $("drawer").setAttribute("aria-hidden", "false");
    $("drawerClose").focus(); // 焦点移入抽屉
  });
}

function renderDrawer(d) {
  const body = $("drawerBody");
  const cat = d.category || "未分类";
  const kw = d.keywords || [];
  const tags = d.tags || [];
  const ext = (d.ext || "").replace(".", "").toLowerCase();
  const [label, color] = EXT_META[ext] || DEFAULT_EXT;
  $("downloadLink").href = `/api/files/${encodeURIComponent(d.stored_name)}`;

  const content = d.content || "";
  const truncated = content.length > 4000;
  const shown = truncated ? content.slice(0, 4000) + "…" : content;

  // 预览区按类型渲染：图片直显 / PDF 内嵌 / 表格提示下载 / 文本显示原文
  let previewHtml;
  if (PREVIEW_IMAGE.has(ext) || ext === "pdf") {
    previewHtml = '<div class="d-preview" id="dPreview"><div class="d-preview-loading">正在加载源文件预览…</div></div>';
  } else if (PREVIEW_NO.has(ext)) {
    previewHtml = '<div class="d-no-preview">该格式暂不支持在线预览，请下载原文件查看</div>';
  } else {
    previewHtml = `
      <div class="d-content" id="dContent">${hl(shown, state.q) || '<span class="d-empty-content">（无文本内容）</span>'}</div>
      ${truncated ? '<button class="expand-btn" id="expandBtn">展开全部（' + (content.length / 1000).toFixed(1) + 'k 字符）</button>' : ""}`;
  }

  body.innerHTML = `
    <div class="d-title">${esc(d.title || d.filename)}</div>
    <div class="d-filename">${esc(d.filename)}${d.path ? ' · <span class="doc-path">' + esc(d.path) + "</span>" : ""} · ${fmtSize(d.file_size)} · ${d.word_count || 0} 词 · 入库于 ${esc(d.created_at)}</div>

    <div class="d-chip-row">
      <span class="d-chip" style="color:${catColor(cat)};border-color:${catColor(cat)}55;background:${catColor(cat)}18;font-weight:600">${esc(cat)}</span>
      ${tags.slice(0, 10).map((t) => `<span class="d-chip">#${esc(t)}</span>`).join("")}
    </div>

    <div class="d-section">
      <div class="d-section-title">AI 摘要</div>
      <div class="d-summary">${hl(d.summary || "（未生成摘要）", state.q)}</div>
    </div>

    <div class="d-section">
      <div class="d-section-title">关键词（点击搜索）</div>
      <div class="d-keywords">
        ${kw.map((k) => `<button class="kw-chip" data-kw="${esc(k)}">${esc(k)}</button>`).join("") || '<span style="color:var(--label-2);font-size:13px">无</span>'}
      </div>
    </div>

    <div class="d-section">
      <div class="d-section-title">${PREVIEW_IMAGE.has(ext) || ext === "pdf" ? "源文件预览" : "原文预览"}</div>
      <div class="d-content-wrap">${previewHtml}</div>
    </div>
  `;

  // 关键词点击 → 全局搜索
  body.querySelectorAll(".kw-chip").forEach((el) =>
    el.addEventListener("click", () => {
      state.q = el.dataset.kw;
      $("searchInput").value = state.q;
      $("clearSearch").classList.add("show");
      closeDrawer();
      loadDocuments();
    })
  );

  // 图片 / PDF：异步加载源文件 blob 渲染
  if (PREVIEW_IMAGE.has(ext) || ext === "pdf") {
    loadFileBlob(d.stored_name)
      .then((url) => {
        const box = $("dPreview");
        if (!box) return;
        if (PREVIEW_IMAGE.has(ext)) {
          box.innerHTML = `<img class="d-img" src="${url}" alt="${esc(d.filename)}">`;
        } else {
          box.innerHTML = `<iframe class="d-pdf" src="${url}" title="${esc(d.filename)}"></iframe>`;
        }
      })
      .catch(() => {
        const box = $("dPreview");
        if (box) box.innerHTML = '<div class="d-no-preview">预览加载失败，请下载查看</div>';
      });
  }

  // 原文展开/收起
  const expandBtn = $("expandBtn");
  if (expandBtn) {
    let expanded = false;
    expandBtn.addEventListener("click", () => {
      const box = $("dContent");
      if (!expanded) {
        box.innerHTML = hl(content, state.q);
        expandBtn.textContent = "收起";
        expanded = true;
      } else {
        box.innerHTML = hl(shown, state.q);
        expandBtn.textContent = "展开全部";
        expanded = false;
      }
    });
  }
  body.scrollTop = 0;
}

function closeDrawer() {
  $("drawer").classList.remove("open");
  $("drawerMask").classList.remove("show");
  $("drawer").setAttribute("aria-hidden", "true");
  state.drawerOpen = false;
  if (state.previewUrl) { URL.revokeObjectURL(state.previewUrl); state.previewUrl = null; }  // 回收预览 blob
  setTimeout(() => {
    $("drawerMask").hidden = true;
    // 焦点恢复到触发元素
    if (state.lastFocused && document.contains(state.lastFocused)) {
      state.lastFocused.focus();
    }
    state.lastFocused = null;
    state.currentDoc = null;
  }, 450);
}

/* ---------- 删除 ---------- */
async function confirmDelete() {
  if (!state.deletingId) return;
  const id = state.deletingId;
  state.deletingId = null;
  $("modalMask").hidden = true;
  state.modalOpen = false;
  try {
    await api(`/api/documents/${id}`, { method: "DELETE" });
    toast("已删除，文件副本已移除");
    closeDrawer();
    await loadAll();
  } catch (e) {
    toast("删除失败: " + e.message);
  }
}

/* ---------- 侧边栏（移动端） ---------- */
function openSidebar() {
  $("sidebar").classList.add("open");
  $("sidebarMask").hidden = false;
  requestAnimationFrame(() => $("sidebarMask").classList.add("show"));
  state.lastFocused = document.activeElement;
  $("sidebarClose").focus();
}
function closeSidebar() {
  $("sidebar").classList.remove("open");
  $("sidebarMask").classList.remove("show");
  setTimeout(() => {
    $("sidebarMask").hidden = true;
    if (state.lastFocused && document.contains(state.lastFocused)) state.lastFocused.focus();
    state.lastFocused = null;
  }, 350);
}

/* ---------- 拖拽上传 ---------- */
let dragDepth = 0;
const overlay = $("dropOverlay");

window.addEventListener("dragenter", (e) => {
  e.preventDefault();
  if (e.dataTransfer?.types?.includes("Files")) {
    dragDepth++;
    overlay.classList.add("show");
  }
});
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("dragleave", (e) => {
  e.preventDefault();
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) overlay.classList.remove("show");
});
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  overlay.classList.remove("show");
  if (e.dataTransfer?.files?.length && !state.uploadBusy) uploadFiles(e.dataTransfer.files);
});

/* ---------- 事件绑定 ---------- */
$("uploadBtn").addEventListener("click", () => $("fileInput").click());
$("fileInput").addEventListener("change", (e) => {
  uploadFiles(e.target.files);
  e.target.value = "";
});
$("drawerClose").addEventListener("click", closeDrawer);
$("drawerMask").addEventListener("click", closeDrawer);
$("deleteBtn").addEventListener("click", () => {
  if (!state.currentDoc) return;
  state.deletingId = state.currentDoc.id;
  state.lastFocused = document.activeElement;
  $("modalSub").textContent = `「${state.currentDoc.title || state.currentDoc.filename}」删除后原文件副本也会一并移除，不可恢复。`;
  $("modalMask").hidden = false;
  state.modalOpen = true;
  requestAnimationFrame(() => $("modalCancel").focus()); // 安全默认：取消
});
$("modalCancel").addEventListener("click", () => {
  state.deletingId = null;
  $("modalMask").hidden = true;
  state.modalOpen = false;
  if (state.lastFocused && document.contains(state.lastFocused)) state.lastFocused.focus();
  state.lastFocused = null;
});
$("modalConfirm").addEventListener("click", confirmDelete);

/* 搜索（防抖 + 进度反馈） */
$("searchInput").addEventListener("input", (e) => {
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(() => {
    state.q = e.target.value.trim();
    $("clearSearch").classList.toggle("show", !!state.q);
    loadDocuments();
  }, 300);
});
$("clearSearch").addEventListener("click", () => {
  $("searchInput").value = "";
  state.q = "";
  $("clearSearch").classList.remove("show");
  loadDocuments();
  $("searchInput").focus();
});

/* 清除全部筛选（空状态按钮） */
$("resetFilter").addEventListener("click", () => {
  state.q = "";
  state.category = "全部";
  state.tag = "";
  $("searchInput").value = "";
  $("clearSearch").classList.remove("show");
  renderCatNav(state.categories.total ?? 0);
  renderTagCloud();
  loadDocuments();
});

/* 移动端侧边栏 */
$("menuBtn").addEventListener("click", openSidebar);
$("sidebarClose").addEventListener("click", closeSidebar);
$("sidebarMask").addEventListener("click", closeSidebar);

/* ---------- 全局键盘 ---------- */
document.addEventListener("keydown", (e) => {
  // Esc 优先关弹窗 → 抽屉 → 侧边栏
  if (e.key === "Escape") {
    if (state.modalOpen) {
      $("modalCancel").click();
    } else if (state.drawerOpen) {
      closeDrawer();
    } else if ($("sidebar").classList.contains("open")) {
      closeSidebar();
    }
    return;
  }
  // "/" 快速聚焦搜索（非输入态）
  if (e.key === "/" && !isTyping(e.target)) {
    e.preventDefault();
    $("searchInput").focus();
    $("searchInput").select();
    return;
  }
  // 抽屉焦点陷阱
  if (e.key === "Tab") {
    if (state.drawerOpen) {
      const focusables = $("drawer").querySelectorAll('button, a[href], [tabindex]:not([tabindex="-1"])');
      if (focusables.length) {
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    }
  }
  // 删除弹窗焦点陷阱
  if (e.key === "Tab" && state.modalOpen) {
    const btns = $("modalMask").querySelectorAll("button");
    if (btns.length && document.activeElement === btns[btns.length - 1] && !e.shiftKey) {
      e.preventDefault();
      btns[0].focus();
    }
  }
});

/* ---------- 启动（鉴权守卫：先校验会话再初始化） ---------- */
(async function boot() {
  try {
    const token = getToken();
    if (!token) {
      showAuthGate("login");
      return;
    }
    const d = await api("/api/auth/me");
    if (d.must_change_password) {
      showAuthGate("force");
      return;
    }
    enterApp({ username: d.username, role: d.role });
  } catch (e) {
    clearToken();
    showAuthGate("login");
  }
})();

/* ============================================================
   认证模块：登录 / 注册 / 找回 / 强制改密 / 用户菜单 / 管理后台
   ============================================================ */
const auth = {
  user: null,          // {username, role}
  pendingUser: null,   // 登录成功但需强制改密时暂存
  regStep: 1,
  qaCount: 0,
  fgStep: 1,
  fgQuestions: [],     // 找回：问题列表
  resetTarget: null,   // 管理员重置目标用户名
};

const QA_OPTIONS = [
  "你的第一只宠物叫什么名字？",
  "你的家乡是哪里？",
  "你的小学名称是什么？",
  "你最喜欢的食物是什么？",
  "你崇拜的偶像或人物是谁？",
  "自定义问题…",
];

function showAuthGate(view) {
  document.querySelectorAll(".auth-view").forEach((v) => (v.hidden = true));
  $("rememberBar").hidden = true;
  pendingLogin = null;
  $("authGate").hidden = false;
  const map = { login: "authViewLogin", register: "authViewRegister", forgot: "authViewForgot", force: "authViewForce" };
  $(map[view] || "authViewLogin").hidden = false;
  setTimeout(() => {
    const f = view === "login" ? $("loginUsername") : view === "force" ? $("fcOld") : null;
    if (f) f.focus();
  }, 120);
}

function enterApp(user) {
  auth.user = user;
  $("authGate").hidden = true;
  renderUserMenu();
  $("menuAdmin").hidden = user.role !== "admin";
  loadAll().catch((e) => toast("加载失败: " + e.message));
}

/* ---------- 用户菜单 ---------- */
function renderUserMenu() {
  const u = auth.user;
  $("userAvatar").textContent = (u?.username || "U").charAt(0).toUpperCase();
  $("userMenuName").textContent = u?.username || "—";
  $("userMenuRole").textContent = u?.role === "admin" ? "管理员" : "普通用户";
}

function closeUserMenu() {
  $("userMenu").hidden = true;
  $("userBtn").setAttribute("aria-expanded", "false");
}

/* ---------- 密码强度 ---------- */
function pwStrength(pw) {
  let score = 0;
  if (pw.length >= 8) score++;
  if (/[a-zA-Z]/.test(pw)) score++;
  if (/\d/.test(pw)) score++;
  if (pw.length >= 12) score++;
  return Math.min(score, 3);
}
function pwMeta(barEl, hintEl, pw) {
  const s = pwStrength(pw);
  const pct = [0, 33, 66, 100][s];
  const colors = ["", "var(--orange)", "var(--orange)", "var(--green)"];
  barEl.style.width = pct + "%";
  barEl.style.background = colors[s];
  if (hintEl) {
    if (pw && pw.length < 8) { hintEl.textContent = "太短，至少 8 位"; hintEl.className = "pw-hint warn"; }
    else if (pw && !/[a-zA-Z]/.test(pw)) { hintEl.textContent = "还差一个字母"; hintEl.className = "pw-hint warn"; }
    else if (pw && !/\d/.test(pw)) { hintEl.textContent = "还差一个数字"; hintEl.className = "pw-hint warn"; }
    else if (pw) { hintEl.textContent = s >= 3 ? "密码强度：强" : s === 2 ? "密码强度：中" : "密码强度：弱"; hintEl.className = s >= 3 ? "pw-hint ok" : "pw-hint warn"; }
    else { hintEl.textContent = "至少 8 位，同时包含字母和数字"; hintEl.className = "pw-hint"; }
  }
}
function pwOk(pw) {
  return pw.length >= 8 && /[a-zA-Z]/.test(pw) && /\d/.test(pw);
}
function showErr(id, msg) {
  const el = $(id);
  el.textContent = msg;
  el.hidden = false;
}
function hideErr(id) {
  $(id).hidden = true;
}

/* ---------- 登录（成功后可询问是否记住） ---------- */

async function doLogin(username, password) {
  hideErr("loginErr");
  const btn = $("loginBtn");
  btn.classList.add("loading");
  try {
    const d = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (d.must_change_password) {
      // 强制改密：直接走改密流程（改密后视为本次登录）
      saveToken(d.token, false);
      auth.pendingUser = { username: d.username, role: d.role };
      showAuthGate("force");
      return;
    }
    // 登录成功 → 询问保存登录状态（除非已固化为不再询问）
    const pref = localStorage.getItem("kb_remember_pref") || "ask";
    if (pref === "remember") {
      saveToken(d.token, true);
      enterApp({ username: d.username, role: d.role });
    } else if (pref === "no") {
      saveToken(d.token, false);
      enterApp({ username: d.username, role: d.role });
    } else {
      pendingLogin = d;
      $("rememberBar").hidden = false;
      $("rememberAskNoMore").checked = false;
      $("rememberYes").focus();
    }
  } catch (e) {
    showErr("loginErr", e.message || "登录失败");
  } finally {
    btn.classList.remove("loading");
  }
}

function resolveRemember(remember) {
  if (!pendingLogin) return;
  const d = pendingLogin;
  pendingLogin = null;
  $("rememberBar").hidden = true;
  if ($("rememberAskNoMore").checked) {
    localStorage.setItem("kb_remember_pref", remember ? "remember" : "no");
  }
  saveToken(d.token, remember);
  enterApp({ username: d.username, role: d.role });
}

$("rememberYes").addEventListener("click", () => resolveRemember(true));
$("rememberNo").addEventListener("click", () => resolveRemember(false));

$("loginBtn").addEventListener("click", () => {
  doLogin($("loginUsername").value.trim(), $("loginPassword").value);
});
["loginUsername", "loginPassword"].forEach((id) =>
  $(id).addEventListener("keydown", (e) => {
    if (e.key === "Enter") doLogin($("loginUsername").value.trim(), $("loginPassword").value);
  })
);

/* ---------- 密码可见切换 ---------- */
document.querySelectorAll(".pw-toggle").forEach((btn) => {
  btn.addEventListener("click", () => {
    const input = $(btn.dataset.target);
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.textContent = show ? "🙈" : "👁";
    btn.setAttribute("aria-pressed", String(show));
  });
});

/* ---------- 注册（3 步） ---------- */
function resetRegister() {
  auth.regStep = 1;
  $("regStep1").hidden = false;
  $("regStep2").hidden = true;
  $("regStep3").hidden = true;
  $("regProgress").querySelector("i").style.width = "33%";
  $("regTitle").textContent = "创建账号";
  $("regSub").textContent = "步骤 1 / 3 · 账号与密码";
  $("qaList").innerHTML = "";
  auth.qaCount = 0;
  addQaCard();
  $("addQa").textContent = "+ 添加第 2 个问题";
}

function addQaCard() {
  auth.qaCount++;
  const idx = auth.qaCount;
  const card = document.createElement("div");
  card.className = "qa-card";
  card.innerHTML = `
    <div class="qa-num">问题 ${idx}</div>
    <select class="qa-select">
      ${QA_OPTIONS.map((q, i) => `<option value="${esc(q)}" ${i === 0 ? "selected" : ""}>${esc(q)}</option>`).join("")}
    </select>
    <div class="qa-custom-wrap" hidden>
      <input type="text" class="qa-custom" placeholder="自定义问题（≤100 字）" maxlength="100" style="width:100%;padding:12px 12px;margin:0 0 10px;background:var(--card);border:1px solid var(--separator);border-radius:10px;color:var(--label);font-size:14px;">
    </div>
    <input type="text" class="qa-answer" placeholder="答案（仅自己知道，至少 4 位）" maxlength="100" style="width:100%;padding:12px 14px;background:var(--card);border:1px solid var(--separator);border-radius:10px;color:var(--label);font-size:14px;">
  `;
  card.querySelector(".qa-select").addEventListener("change", (e) => {
    card.querySelector(".qa-custom-wrap").hidden = e.target.value !== "自定义问题…";
  });
  $("qaList").appendChild(card);
  return card;
}

$("regPassword").addEventListener("input", (e) => pwMeta($("regMeterBar"), $("regPwHint"), e.target.value));
$("regNext1").addEventListener("click", () => {
  const u = $("regUsername").value.trim();
  const p1 = $("regPassword").value;
  const p2 = $("regPw2").value;
  hideErr("regErr1");
  if (!/^[a-zA-Z0-9_-]{3,32}$/.test(u)) return showErr("regErr1", "用户名需 3-32 位，仅限字母/数字/下划线/中划线");
  if (!pwOk(p1)) return showErr("regErr1", "密码至少 8 位，且同时包含字母和数字");
  if (p1 !== p2) return showErr("regErr1", "两次输入的密码不一致");
  auth.regStep = 2;
  $("regStep1").hidden = true;
  $("regStep2").hidden = false;
  $("regProgress").querySelector("i").style.width = "66%";
  $("regTitle").textContent = "设置安全问题";
  $("regSub").textContent = "步骤 2 / 3 · 忘记密码时用于自助找回";
});

$("addQa").addEventListener("click", () => {
  if (auth.qaCount >= 3) return;
  addQaCard();
  $("addQa").textContent = auth.qaCount >= 3 ? "最多 3 个问题" : `+ 添加第 ${auth.qaCount + 1} 个问题`;
});

$("regNext2").addEventListener("click", async () => {
  hideErr("regErr2");
  const questions = [];
  let ok = true;
  $("qaList").querySelectorAll(".qa-card").forEach((card) => {
    const sel = card.querySelector(".qa-select").value;
    const custom = card.querySelector(".qa-custom").value.trim();
    const q = sel === "自定义问题…" ? custom : sel;
    const a = card.querySelector(".qa-answer").value.trim();
    if (!q || a.length < 4) { ok = false; return; }
    questions.push({ question: q, answer: a });
  });
  if (!ok || questions.length === 0) return showErr("regErr2", "请完整填写问题与答案（答案至少 4 位）");

  const btn = $("regNext2");
  btn.classList.add("loading");
  try {
    await api("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: $("regUsername").value.trim(),
        password: $("regPassword").value,
        questions,
      }),
    });
    // 注册成功 → 自动登录
    auth.regStep = 3;
    $("regStep2").hidden = true;
    $("regStep3").hidden = false;
    $("regProgress").querySelector("i").style.width = "100%";
    $("regDoneSub").textContent = "注册成功，正在安全登录…";
    const d = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: $("regUsername").value.trim(), password: $("regPassword").value }),
    });
    saveToken(d.token, false);
    if (d.must_change_password) { auth.pendingUser = { username: d.username, role: d.role }; showAuthGate("force"); }
    else enterApp({ username: d.username, role: d.role });
  } catch (e) {
    auth.regStep = 2;
    $("regStep3").hidden = true;
    $("regStep2").hidden = false;
    showErr("regErr2", e.message || "注册失败");
  } finally {
    btn.classList.remove("loading");
  }
});

$("regBack").addEventListener("click", () => {
  if (auth.regStep === 1) { resetAuthForm(); showAuthGate("login"); }
  else {
    auth.regStep = 1;
    $("regStep2").hidden = true;
    $("regStep1").hidden = false;
    $("regProgress").querySelector("i").style.width = "33%";
    $("regTitle").textContent = "创建账号";
    $("regSub").textContent = "步骤 1 / 3 · 账号与密码";
  }
});

/* ---------- 找回密码（3 步） ---------- */
function resetForgot() {
  auth.fgStep = 1;
  $("fgStep1").hidden = false;
  $("fgStep2").hidden = true;
  $("fgStep3").hidden = true;
  $("fgProgress").querySelector("i").style.width = "33%";
  $("fgTitle").textContent = "找回密码";
  $("fgSub").textContent = "步骤 1 / 3 · 输入用户名";
  $("qaVerify").innerHTML = "";
  auth.fgQuestions = [];
}

$("fgNext1").addEventListener("click", async () => {
  const u = $("fgUsername").value.trim();
  hideErr("fgErr1");
  if (!u) return showErr("fgErr1", "请输入用户名");
  const btn = $("fgNext1");
  btn.classList.add("loading");
  try {
    const d = await api("/api/auth/questions?username=" + encodeURIComponent(u));
    if (!d.questions || d.questions.length === 0) {
      return showErr("fgErr1", "该账号未设置安全问题，请联系管理员重置");
    }
    auth.fgQuestions = d.questions;
    auth.fgUsername = u;
    auth.fgStep = 2;
    $("fgStep1").hidden = true;
    $("fgStep2").hidden = false;
    $("fgProgress").querySelector("i").style.width = "66%";
    $("fgSub").textContent = "步骤 2 / 3 · 回答安全问题";
    $("qaVerify").innerHTML = d.questions
      .map(
        (q, i) => `
        <div class="qa-verify-card">
          <div class="qa-q">${esc(q)}</div>
          <div class="qa-a"><input type="text" class="fg-answer" data-i="${i}" placeholder="输入答案" maxlength="100" autocomplete="off"></div>
        </div>`
      )
      .join("");
    setTimeout(() => $("qaVerify").querySelector("input")?.focus(), 100);
  } catch (e) {
    showErr("fgErr1", e.message || "获取失败");
  } finally {
    btn.classList.remove("loading");
  }
});

$("fgNext2").addEventListener("click", () => {
  hideErr("fgErr2");
  const answers = Array.from($("qaVerify").querySelectorAll(".fg-answer")).map((i) => i.value.trim());
  if (answers.some((a) => !a)) return showErr("fgErr2", "请完整回答所有问题");
  auth.fgAnswers = answers;
  auth.fgStep = 3;
  $("fgStep2").hidden = true;
  $("fgStep3").hidden = false;
  $("fgProgress").querySelector("i").style.width = "100%";
  $("fgTitle").textContent = "设置新密码";
  $("fgSub").textContent = "步骤 3 / 3 · 全部答对，可以重置";
});

$("fgPassword").addEventListener("input", (e) => pwMeta($("fgMeterBar"), $("fgPwHint"), e.target.value));

$("fgNext3").addEventListener("click", async () => {
  const n1 = $("fgPassword").value;
  const n2 = $("fgPw2").value;
  hideErr("fgErr3");
  if (!pwOk(n1)) return showErr("fgErr3", "密码至少 8 位，且同时包含字母和数字");
  if (n1 !== n2) return showErr("fgErr3", "两次输入的新密码不一致");
  const btn = $("fgNext3");
  btn.classList.add("loading");
  try {
    await api("/api/auth/reset-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: auth.fgUsername, answers: auth.fgAnswers, new_password: n1 }),
    });
    toast("密码已重置，请用新密码登录");
    resetAuthForm();
    showAuthGate("login");
    $("loginUsername").value = auth.fgUsername;
    $("loginPassword").focus();
  } catch (e) {
    showErr("fgErr3", e.message || "重置失败");
  } finally {
    btn.classList.remove("loading");
  }
});

$("fgBack").addEventListener("click", () => {
  if (auth.fgStep === 1) { resetAuthForm(); showAuthGate("login"); }
  else {
    auth.fgStep = 1;
    $("fgStep2").hidden = true;
    $("fgStep3").hidden = true;
    $("fgStep1").hidden = false;
    $("fgProgress").querySelector("i").style.width = "33%";
    $("fgTitle").textContent = "找回密码";
    $("fgSub").textContent = "步骤 1 / 3 · 输入用户名";
  }
});

/* ---------- 视图跳转 ---------- */
function resetAuthForm() {
  ["loginUsername", "loginPassword", "regUsername", "regPassword", "regPw2",
   "fgUsername", "fgPassword", "fgPw2", "fcOld", "fcNew", "fcNew2"].forEach((id) => {
    const el = $(id);
    if (el) el.value = "";
  });
  ["loginErr", "regErr1", "regErr2", "fgErr1", "fgErr2", "fgErr3", "fcErr"].forEach(hideErr);
  ["regMeterBar", "fgMeterBar", "fcMeterBar"].forEach((id) => {
    const el = $(id);
    if (el) { el.style.width = "0%"; el.style.background = "var(--orange)"; }
  });
  resetRegister();
  resetForgot();
}

$("gotoRegister").addEventListener("click", () => { resetAuthForm(); showAuthGate("register"); });
$("gotoForgot").addEventListener("click", () => { resetAuthForm(); showAuthGate("forgot"); });

/* ---------- 强制改密 ---------- */
$("fcNew").addEventListener("input", (e) => pwMeta($("fcMeterBar"), $("fcPwHint"), e.target.value));
$("fcBtn").addEventListener("click", async () => {
  const old = $("fcOld").value;
  const n1 = $("fcNew").value;
  const n2 = $("fcNew2").value;
  hideErr("fcErr");
  if (!old) return showErr("fcErr", "请输入当前密码");
  if (!pwOk(n1)) return showErr("fcErr", "密码至少 8 位，且同时包含字母和数字");
  if (n1 !== n2) return showErr("fcErr", "两次输入的新密码不一致");
  const btn = $("fcBtn");
  btn.classList.add("loading");
  try {
    await api("/api/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old_password: old, new_password: n1 }),
    });
    clearToken();
    const u = auth.pendingUser?.username || "";
    const d = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: u, password: n1 }),
    });
    saveToken(d.token, false);
    enterApp({ username: d.username, role: d.role });
    toast("密码已更新");
  } catch (e) {
    showErr("fcErr", e.message || "修改失败");
  } finally {
    btn.classList.remove("loading");
  }
});

/* ---------- 用户菜单 ---------- */
$("userBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  const menu = $("userMenu");
  menu.hidden = !menu.hidden;
  $("userBtn").setAttribute("aria-expanded", String(!menu.hidden));
});
document.addEventListener("click", (e) => {
  if (!$("userMenu").hidden && !$("userMenu").contains(e.target) && e.target !== $("userBtn")) {
    closeUserMenu();
  }
});

/* 修改密码弹窗 */
$("menuChangePw").addEventListener("click", () => {
  closeUserMenu();
  $("pwOld").value = "";
  $("pwNew").value = "";
  $("pwNew2").value = "";
  hideErr("pwErr");
  $("pwMeterBar").style.width = "0%";
  $("pwModalMask").hidden = false;
  setTimeout(() => $("pwOld").focus(), 100);
});
$("pwNew").addEventListener("input", (e) => pwMeta($("pwMeterBar"), null, e.target.value));
$("pwCancel").addEventListener("click", () => { $("pwModalMask").hidden = true; });
$("pwModalMask").addEventListener("click", (e) => { if (e.target === $("pwModalMask")) $("pwModalMask").hidden = true; });
$("pwConfirm").addEventListener("click", async () => {
  const old = $("pwOld").value;
  const n1 = $("pwNew").value;
  const n2 = $("pwNew2").value;
  hideErr("pwErr");
  if (!old) return showErr("pwErr", "请输入当前密码");
  if (!pwOk(n1)) return showErr("pwErr", "密码至少 8 位，且同时包含字母和数字");
  if (n1 !== n2) return showErr("pwErr", "两次输入的新密码不一致");
  const btn = $("pwConfirm");
  btn.disabled = true;
  try {
    await api("/api/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old_password: old, new_password: n1 }),
    });
    $("pwModalMask").hidden = true;
    clearToken();
    toast("密码已修改，请重新登录");
    resetAuthForm();
    showAuthGate("login");
  } catch (e) {
    showErr("pwErr", e.message || "修改失败");
  } finally {
    btn.disabled = false;
  }
});

/* 退出登录（二次确认防误触） */
let logoutArmed = false;
$("menuLogout").addEventListener("click", async () => {
  if (!logoutArmed) {
    logoutArmed = true;
    const btn = $("menuLogout");
    btn.textContent = "再次点击确认退出";
    setTimeout(() => { logoutArmed = false; btn.textContent = "退出登录"; }, 3000);
    return;
  }
  logoutArmed = false;
  try { await api("/api/auth/logout", { method: "POST" }); } catch (e) { /* ignore */ }
  clearToken();
  closeUserMenu();
  resetAuthForm();
  showAuthGate("login");
});

/* ---------- 管理后台 ---------- */
let adminTab = "users";
$("menuAdmin").addEventListener("click", () => {
  closeUserMenu();
  openAdmin();
});

function openAdmin() {
  $("docSection").hidden = true;
  $("adminView").hidden = false;
  switchAdminTab("users");
  loadAdminUsers();
}

function closeAdmin() {
  $("adminView").hidden = true;
  $("docSection").hidden = false;
  loadAll().catch(() => {});
}

function switchAdminTab(tab) {
  adminTab = tab;
  $("tabUsers").classList.toggle("active", tab === "users");
  $("tabAudit").classList.toggle("active", tab === "audit");
  $("tabUsers").setAttribute("aria-selected", String(tab === "users"));
  $("tabAudit").setAttribute("aria-selected", String(tab === "audit"));
  $("adminUsers").hidden = tab !== "users";
  $("adminAudit").hidden = tab !== "audit";
  if (tab === "audit") loadAdminAudit();
}

async function loadAdminUsers() {
  const box = $("adminUsers");
  box.innerHTML = '<div class="admin-empty">加载中…</div>';
  try {
    const d = await api("/api/admin/users");
    const items = d.items || [];
    box.innerHTML = items.length
      ? items
          .map((u) => {
            const roleChip =
              u.role === "admin"
                ? '<span class="admin-chip admin">管理员</span>'
                : '<span class="admin-chip ok">用户</span>';
            const pendingChip = u.must_change_password ? '<span class="admin-chip pending">待改密</span>' : "";
            const canReset = u.role !== "admin";
            return `
            <div class="admin-user-card">
              <div class="admin-user-avatar">${esc((u.username || "?").charAt(0).toUpperCase())}</div>
              <div class="admin-user-info">
                <div class="admin-user-line1">
                  <span class="admin-user-name">${esc(u.username)}</span>
                  ${roleChip}${pendingChip}
                </div>
                <div class="admin-user-meta">创建 ${esc(u.created_at || "-")} · 最后登录 ${esc(u.last_login || "-")} · 文档 ${u.doc_count ?? 0} · 安全问题 ${u.question_count ?? 0}</div>
              </div>
              ${canReset ? `<button class="admin-reset-btn" data-user="${esc(u.username)}">重置密码</button>` : ""}
            </div>`;
          })
          .join("")
      : '<div class="admin-empty">暂无用户</div>';
    box.querySelectorAll(".admin-reset-btn").forEach((btn) =>
      btn.addEventListener("click", () => {
        auth.resetTarget = btn.dataset.user;
        $("resetPwTitle").textContent = `重置用户「${btn.dataset.user}」密码`;
        $("resetPwInput").value = "";
        hideErr("resetPwErr");
        $("resetPwMask").hidden = false;
        setTimeout(() => $("resetPwInput").focus(), 100);
      })
    );
  } catch (e) {
    box.innerHTML = `<div class="admin-empty">加载失败: ${esc(e.message)}</div>`;
  }
}

async function loadAdminAudit() {
  const box = $("adminAudit");
  box.innerHTML = '<div class="admin-empty">加载中…</div>';
  try {
    const d = await api("/api/admin/audit?limit=100");
    const items = d.items || [];
    const colors = { login: "#0a84ff", change_password: "#ff9f0a", reset_password: "#ff453a", admin: "#bf5af2", system: "#30d158", register: "#64d2ff" };
    box.innerHTML = items.length
      ? `<div class="admin-audit-list">${items
          .map(
            (i) => `
          <div class="admin-audit-item">
            <span class="admin-audit-dot" style="background:${colors[i.action] || "#8e8e93"}"></span>
            <span class="admin-audit-time">${esc((i.created_at || "").slice(5, 16))}</span>
            <span class="admin-audit-text">${esc(i.actor)} · ${esc(i.action)}${i.target ? " → " + esc(i.target) : ""}</span>
          </div>`
          )
          .join("")}</div>`
      : '<div class="admin-empty">暂无审计记录</div>';
  } catch (e) {
    box.innerHTML = `<div class="admin-empty">加载失败: ${esc(e.message)}</div>`;
  }
}

$("adminBack").addEventListener("click", closeAdmin);
$("tabUsers").addEventListener("click", () => switchAdminTab("users"));
$("tabAudit").addEventListener("click", () => switchAdminTab("audit"));

/* 管理员重置密码 */
$("resetPwCancel").addEventListener("click", () => { $("resetPwMask").hidden = true; auth.resetTarget = null; });
$("resetPwMask").addEventListener("click", (e) => { if (e.target === $("resetPwMask")) { $("resetPwMask").hidden = true; auth.resetTarget = null; } });
$("resetPwConfirm").addEventListener("click", async () => {
  const u = auth.resetTarget;
  const pw = $("resetPwInput").value;
  hideErr("resetPwErr");
  if (!u) return;
  if (!pwOk(pw)) return showErr("resetPwErr", "密码至少 8 位，且同时包含字母和数字");
  const btn = $("resetPwConfirm");
  btn.disabled = true;
  try {
    await api(`/api/admin/users/${encodeURIComponent(u)}/reset-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_password: pw }),
    });
    $("resetPwMask").hidden = true;
    auth.resetTarget = null;
    toast(`已重置「${u}」密码，该用户下次登录需改密`);
    loadAdminUsers();
  } catch (e) {
    showErr("resetPwErr", e.message || "重置失败");
  } finally {
    btn.disabled = false;
  }
});

/* Esc：关闭用户菜单 / 改密弹窗 / 重置弹窗 */
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!$("userMenu").hidden) { closeUserMenu(); return; }
    if (!$("pwModalMask").hidden) { $("pwModalMask").hidden = true; return; }
    if (!$("resetPwMask").hidden) { $("resetPwMask").hidden = true; auth.resetTarget = null; return; }
  }
});

/* ============================================================
   主题模块：浅色 / 深色 / 跟随系统（localStorage 持久化）
   ============================================================ */
const THEME_MQ = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

function currentTheme() {
  const t = localStorage.getItem("kb_theme") || "system";
  if (t === "system") return THEME_MQ && THEME_MQ.matches ? "dark" : "light";
  return t;
}
function applyTheme() {
  document.documentElement.setAttribute("data-theme", currentTheme());
  renderThemeIcons();
}
function renderThemeIcons() {
  const dark = currentTheme() === "dark";
  const icon = dark
    ? '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'
    : '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4.5"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8"/></svg>';
  ["themeBtn", "authThemeBtn"].forEach((id) => {
    const el = $(id);
    if (el) el.innerHTML = icon;
  });
}
function toggleTheme() {
  localStorage.setItem("kb_theme", currentTheme() === "dark" ? "light" : "dark");
  applyTheme();
}
if (THEME_MQ) THEME_MQ.addEventListener("change", () => {
  if ((localStorage.getItem("kb_theme") || "system") === "system") applyTheme();
});
$("themeBtn").addEventListener("click", toggleTheme);
$("authThemeBtn").addEventListener("click", toggleTheme);
$("menuThemeSystem").addEventListener("click", () => {
  localStorage.setItem("kb_theme", "system");
  applyTheme();
  closeUserMenu();
  toast("已跟随系统主题");
});
$("menuRememberReset").addEventListener("click", () => {
  localStorage.removeItem("kb_remember_pref");
  closeUserMenu();
  toast("已恢复登录询问，下次登录会再次询问");
});
applyTheme();
