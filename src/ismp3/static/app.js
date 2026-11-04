/* ismp3 front-end — vanilla JS, no build step. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  const STATUS_LABELS = {
    queued: "في الانتظار",
    running: "جارٍ التنفيذ",
    done: "تم",
    error: "خطأ",
    cancelled: "أُلغي",
    interrupted: "متوقف",
    uploading: "جارٍ الرفع",
  };
  const KIND_LABELS = {
    url: "رابط",
    video: "فيديو",
    playlist: "قائمة",
    file: "ملف",
    folder: "مجلد",
    archive: "أرشيف",
    upload: "رفع",
  };
  const PRESETS = {
    tiny: { bitrate: "16", sampleRate: "16000", channels: "1", mode: "cbr" },
    voice: { bitrate: "24", sampleRate: "24000", channels: "1", mode: "cbr" },
    music: { bitrate: "64", sampleRate: "44100", channels: "2", mode: "cbr" },
    hq: { bitrate: "128", sampleRate: "44100", channels: "2", mode: "cbr" },
  };

  const state = {
    jobs: new Map(),
    filter: "",
    search: "",
    settings: null,
    browsePath: "",
    browseMode: "sources",
    events: null,
    pollTimer: null,
    audio: null,
    playingBtn: null,
    renderQueued: false,
    libraryTimer: null,
  };

  // ── URLs ─────────────────────────────────────────────────────────────
  function apiUrl(path, extra) {
    const url = new URL(path, location.origin);
    if (extra) {
      for (const [key, value] of Object.entries(extra)) {
        if (value !== undefined && value !== null && value !== "") {
          url.searchParams.set(key, value);
        }
      }
    }
    return url.pathname + url.search;
  }

  async function api(path, options = {}) {
    const opts = { ...options };
    const paramsIn = opts.params;
    delete opts.params;
    const headers = { ...(opts.headers || {}) };
    if (opts.body && typeof opts.body === "string" && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    let response;
    try {
      response = await fetch(apiUrl(path, paramsIn), { ...opts, headers });
    } catch (err) {
      setOnline(false);
      throw new Error("تعذّر الاتصال بالخادم");
    }
    const type = response.headers.get("Content-Type") || "";
    let data;
    if (type.includes("application/json")) {
      data = await response.json().catch(() => ({}));
    } else {
      data = await response.text();
    }
    if (!response.ok) {
      const message = (data && (data.error || data.detail)) || response.statusText || "خطأ";
      throw new Error(message);
    }
    return data;
  }

  // ── helpers ──────────────────────────────────────────────────────────
  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[char]));

  function toast(message, kind = "") {
    const box = document.createElement("div");
    box.className = `toast ${kind}`.trim();
    box.textContent = message;
    $("#toasts").append(box);
    setTimeout(() => {
      box.classList.add("out");
      setTimeout(() => box.remove(), 300);
    }, kind === "err" ? 6500 : 4200);
  }

  function fmtDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    try {
      return date.toLocaleString("ar", { dateStyle: "short", timeStyle: "short" });
    } catch {
      return date.toLocaleString();
    }
  }

  function baseName(path) {
    if (!path) return "";
    const parts = String(path).split(/[\\/]/).filter(Boolean);
    return parts[parts.length - 1] || String(path);
  }

  function sizeForHour(bitrate) {
    const mb = (Number(bitrate) * 3600) / 8 / 1024;
    return `${mb.toFixed(1)} MB لكل ساعة`;
  }

  function setOnline(online) {
    const dot = $("#healthDot");
    dot.classList.toggle("online", online);
    dot.classList.toggle("offline", !online);
    $("#healthText").textContent = online ? "متصل" : "غير متصل";
  }

  function setHealthText(text, online = true) {
    $("#healthText").textContent = text;
    setOnline(online);
  }

  // ── dashboard ────────────────────────────────────────────────────────
  async function refreshHealth() {
    try {
      const data = await api("/api/health");
      setOnline(true);
      if (data.version) $("#versionText").textContent = `v${data.version}`;
    } catch {
      setOnline(false);
    }
  }

  async function refreshStats() {
    try {
      const [statsData, disk] = await Promise.all([api("/api/stats"), api("/api/disk")]);
      const stats = statsData.stats || {};
      $("#statTotal").textContent = stats.total ?? 0;
      $("#statDone").textContent = stats.done ?? 0;
      $("#statActive").textContent = stats.active ?? 0;
      $("#statSize").textContent = stats.size_text || "0 B";
      $("#statSeen").textContent = stats.seen ?? 0;
      $("#statFree").textContent = disk.free_text || "—";
    } catch {
      setOnline(false);
    }
  }

  async function loadJobs() {
    try {
      const data = await api("/api/jobs", { params: { limit: 500 } });
      state.jobs.clear();
      for (const job of data.jobs || []) state.jobs.set(job.id, job);
      scheduleRender();
    } catch (err) {
      toast(err.message, "err");
    }
  }

  function upsertJob(job) {
    if (!job || !job.id) return;
    state.jobs.set(job.id, job);
    scheduleRender();
    if (job.status === "done") {
      clearTimeout(state.libraryTimer);
      state.libraryTimer = setTimeout(loadLibrary, 1600);
    }
  }

  function scheduleRender() {
    if (state.renderQueued) return;
    state.renderQueued = true;
    requestAnimationFrame(() => {
      state.renderQueued = false;
      renderJobs();
    });
  }

  function jobMatches(job) {
    if (state.filter === "active") {
      if (!["queued", "running"].includes(job.status)) return false;
    } else if (state.filter && job.status !== state.filter) {
      return false;
    }
    if (!state.search) return true;
    const haystack = [
      job.title, job.error, job.label,
      ...(job.sources || []), ...(job.outputs || []),
    ].join(" ").toLowerCase();
    return haystack.includes(state.search);
  }

  function actionButtons(job) {
    const active = ["queued", "running"].includes(job.status);
    const done = job.status === "done";
    const hasOutputs = (job.outputs || []).length > 0;
    const buttons = [];
    if (active) {
      buttons.push(`<button class="btn ghost danger" data-action="cancel">⏹ إلغاء</button>`);
    }
    if (!active && job.status !== "done") {
      buttons.push(`<button class="btn" data-action="retry">🔁 إعادة المحاولة</button>`);
    }
    if (done && hasOutputs) {
      if ((job.outputs || []).length > 1) {
        buttons.push(`<button class="btn" data-action="zip">🗜 ZIP</button>`);
      }
      buttons.push(`<button class="btn ghost" data-action="ia">🏛 رفع للأرشيف</button>`);
    }
    if (done && (job.outputs || []).length === 1) {
      buttons.push(`<button class="btn ghost" data-action="rename">✏️ إعادة تسمية</button>`);
    }
    buttons.push(
      `<button class="btn ghost danger" data-action="delete">🗑 حذف</button>`
    );
    return buttons.join("");
  }

  function outputsHTML(job) {
    const outputs = job.outputs || [];
    if (!outputs.length) return "";
    const shown = outputs.slice(0, 8);
    const rows = shown.map((path, index) => {
      const url = apiUrl(`/api/jobs/${encodeURIComponent(job.id)}/download`, { index });
      const isZip = String(path).toLowerCase().endsWith(".zip");
      return `<div class="output">
        <span>${isZip ? "🗜" : "🎵"}</span>
        <span class="mono">${esc(baseName(path))}</span>
        <a href="${esc(url)}" download>⬇ تنزيل</a>
      </div>`;
    });
    if (outputs.length > shown.length) {
      rows.push(`<div class="output"><span class="muted">… و${outputs.length - shown.length} ملفات أخرى</span></div>`);
    }
    return `<div class="outputs">${rows.join("")}</div>`;
  }

  function renderJobs() {
    const container = $("#jobs");
    const list = [...state.jobs.values()]
      .filter(jobMatches)
      .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));

    $("#jobsEmpty").classList.toggle("hidden", list.length > 0);
    if (!list.length) {
      container.innerHTML = "";
      return;
    }

    container.innerHTML = list.map((job) => {
      const status = job.status || "queued";
      const active = ["queued", "running"].includes(status);
      const label = job.label || STATUS_LABELS[status] || status;
      const progress = Math.round((Number(job.progress) || 0) * 100);
      const kind = KIND_LABELS[job.kind] || job.kind || "";
      const source = (job.sources || [])[0] || "";
      const ia = (job.extra || {}).ia_url || "";

      const progressBar = active
        ? `<div class="progress ${status === "queued" ? "indeterminate" : ""}">
             <div class="progress-bar" style="width:${progress}%"></div>
           </div>`
        : "";

      const meta = [];
      meta.push(`<span>${esc(label)}${status === "running" ? ` · ${progress}%` : ""}</span>`);
      if (job.speed) meta.push(`<span>⚡ ${esc(job.speed)}</span>`);
      if (job.size) meta.push(`<span>💾 ${esc(job.size_text || "")}</span>`);
      if (job.count) meta.push(`<span>📄 ${job.done_count || 0}/${job.count}</span>`);
      if (job.bitrate) meta.push(`<span>🎚 ${esc(job.bitrate)}k</span>`);
      if (job.created_at) meta.push(`<span>🕒 ${esc(fmtDate(job.created_at))}</span>`);
      if (ia) meta.push(`<a href="${esc(ia)}" target="_blank" rel="noopener">🏛 Archive</a>`);

      return `<article class="job" data-status="${esc(status)}" data-id="${esc(job.id)}">
        <div class="job-head">
          <div style="min-width:0">
            <div class="job-title">${esc(job.title || "بدون عنوان")}</div>
            <div class="job-sub">${esc(kind)}${source ? ` · <span class="mono">${esc(source)}</span>` : ""}</div>
          </div>
          <span class="badge ${esc(status)}">${status === "running" ? '<span class="spinner"></span> ' : ""}${esc(STATUS_LABELS[status] || status)}</span>
        </div>
        ${progressBar}
        <div class="job-meta">${meta.join("")}</div>
        ${job.error ? `<div class="error-box">${esc(job.error)}</div>` : ""}
        <div class="job-actions">${actionButtons(job)}</div>
        ${outputsHTML(job)}
      </article>`;
    }).join("");
  }

  // ── adding ───────────────────────────────────────────────────────────
  function switchTab(name) {
    $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
    $$(".tab-panel").forEach((panel) => panel.classList.toggle("hidden", panel.dataset.panel !== name));
  }

  function currentSettings() {
    return {
      bitrate: Number($("#bitrate").value) || 24,
      mode: $("#mode").value,
      sample_rate: Number($("#sampleRate").value) || 0,
      channels: Number($("#channels").value) || 1,
      layout: $("#layout").value,
      speed: Number($("#speed").value) || 1,
      playlist: $("#playlist").checked,
      cover: $("#cover").checked,
      client: $("#client").value,
    };
  }

  function updateHint() {
    const bitrate = Number($("#bitrate").value) || 24;
    const channels = Number($("#channels").value) === 2 ? "ستيريو" : "مونو";
    const rate = Number($("#sampleRate").value) || 0;
    $("#addHint").textContent =
      `${bitrate} kbps · ${channels}${rate ? ` · ${rate} Hz` : " · تردد تلقائي"} — ${sizeForHour(bitrate)}.`;
  }

  function applyPreset(name) {
    const preset = PRESETS[name];
    if (!preset) return;
    $("#bitrate").value = preset.bitrate;
    $("#sampleRate").value = preset.sampleRate;
    $("#channels").value = preset.channels;
    $("#mode").value = preset.mode;
    $$(".preset").forEach((btn) => btn.classList.toggle("active", btn.dataset.preset === name));
    updateHint();
  }

  async function addJobs() {
    const raw = $("#sources").value;
    const sources = raw.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    if (!sources.length) {
      toast("الصق رابطاً أو مسار ملف أولاً", "err");
      switchTab("links");
      $("#sources").focus();
      return;
    }
    const button = $("#addBtn");
    button.disabled = true;
    button.textContent = "… جارٍ الإضافة";
    try {
      const data = await api("/api/jobs", {
        method: "POST",
        body: JSON.stringify({
          sources,
          settings: currentSettings(),
          pick: $("#pick").value.trim() || null,
          bundle: $("#bundle").checked,
          force: $("#force").checked,
        }),
      });
      for (const job of data.jobs || []) upsertJob(job);
      $("#sources").value = "";
      const count = (data.jobs || []).length;
      toast(`أُضيفت ${count} مهمة — تابع التقدم أدناه`, "ok");
      refreshStats();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      button.disabled = false;
      button.textContent = "🎧 حوّل الآن";
    }
  }

  // ── job actions ──────────────────────────────────────────────────────
  async function jobAction(id, action) {
    const job = state.jobs.get(id);
    if (!job) return;
    if (action === "cancel") {
      await api(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
      toast("أُرسل طلب الإلغاء");
      return;
    }
    if (action === "retry") {
      const created = await api(`/api/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" });
      upsertJob(created);
      toast("أُعيدت المحاولة", "ok");
      return;
    }
    if (action === "rename") {
      const title = prompt("العنوان الجديد:", job.title || "");
      if (!title || !title.trim()) return;
      const data = await api(`/api/jobs/${encodeURIComponent(id)}/rename`, {
        method: "POST",
        body: JSON.stringify({ title: title.trim() }),
      });
      if (data.job) upsertJob(data.job);
      toast("تمت إعادة التسمية", "ok");
      return;
    }
    if (action === "delete") {
      if (!confirm("حذف المهمة وملفاتها نهائياً؟")) return;
      await api(`/api/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
      state.jobs.delete(id);
      scheduleRender();
      refreshStats();
      loadLibrary();
      toast("حُذفت المهمة");
      return;
    }
    if (action === "zip") {
      window.open(apiUrl(`/api/jobs/${encodeURIComponent(id)}/zip`), "_blank");
      return;
    }
    if (action === "ia") {
      const identifier = prompt(
        "معرّف Archive.org (اتركه فارغاً لتوليده تلقائياً):",
        ""
      );
      if (identifier === null) return;
      const created = await api(`/api/jobs/${encodeURIComponent(id)}/upload-ia`, {
        method: "POST",
        body: JSON.stringify({ identifier: identifier.trim() }),
      });
      upsertJob(created);
      toast("بدأ الرفع إلى Archive.org", "ok");
      return;
    }
  }

  // ── uploads ──────────────────────────────────────────────────────────
  async function uploadFiles(fileList) {
    const files = [...fileList];
    if (!files.length) return;
    const status = $("#uploadStatus");
    const added = [];
    for (let i = 0; i < files.length; i += 1) {
      const file = files[i];
      status.textContent = `⏫ رفع ${i + 1}/${files.length}: ${file.name}`;
      try {
        const response = await fetch(apiUrl("/api/upload", { name: file.name }), {
          method: "POST",
          body: file,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || "فشل الرفع");
        added.push(data.path);
      } catch (err) {
        toast(`${file.name}: ${err.message}`, "err");
      }
    }
    if (added.length) {
      const box = $("#sources");
      box.value = [box.value.trim(), ...added].filter(Boolean).join("\n");
      status.textContent = `✅ أُضيف ${added.length} ملف إلى قائمة الروابط.`;
      toast(`رُفع ${added.length} ملف — اضغط «حوّل الآن»`, "ok");
      switchTab("links");
    } else {
      status.textContent = "";
    }
  }

  // ── library ──────────────────────────────────────────────────────────
  async function loadLibrary() {
    try {
      const data = await api("/api/files");
      $("#libraryDir").textContent = data.dir ? `— ${data.dir}` : "";
      const files = data.files || [];
      $("#libraryEmpty").classList.toggle("hidden", files.length > 0);
      $("#library").innerHTML = files.map((file) => {
        const url = apiUrl("/api/files/download", { path: file.path });
        return `<div class="file-row">
          <span>🎵</span>
          <span class="name">${esc(file.name)}</span>
          <span class="size">${esc(file.size_text || "")}</span>
          <button type="button" class="btn ghost" data-play="${esc(url)}" title="استماع">▶</button>
          <a class="btn ghost" href="${esc(url)}" download title="تنزيل">⬇</a>
        </div>`;
      }).join("");
    } catch (err) {
      $("#libraryEmpty").classList.remove("hidden");
      $("#libraryEmpty").textContent = err.message;
    }
  }

  function playFile(url, button) {
    if (!state.audio) {
      state.audio = new Audio();
      state.audio.addEventListener("ended", () => {
        if (state.playingBtn) state.playingBtn.textContent = "▶";
        state.playingBtn = null;
      });
      state.audio.addEventListener("pause", () => {
        if (state.playingBtn && !state.audio.ended) state.playingBtn.textContent = "▶";
      });
      state.audio.addEventListener("play", () => {
        if (state.playingBtn) state.playingBtn.textContent = "⏸";
      });
    }
    const same = state.audio.src && state.audio.src.endsWith(url);
    if (same && !state.audio.paused) {
      state.audio.pause();
      return;
    }
    if (state.playingBtn && state.playingBtn !== button) state.playingBtn.textContent = "▶";
    state.playingBtn = button;
    state.audio.src = url;
    state.audio.play().catch((err) => toast(err.message, "err"));
  }

  // ── settings ─────────────────────────────────────────────────────────
  async function loadCookiesStatus() {
    try {
      const data = await api("/api/cookies");
      $("#cookiesStatus").textContent = data.configured
        ? `✅ ${data.count} كوكي — ${data.path}`
        : "لا يوجد ملف كوكيز.";
    } catch (err) {
      $("#cookiesStatus").textContent = err.message;
    }
  }

  async function loadIaStatus() {
    try {
      const data = await api("/api/ia");
      $("#iaStatus").textContent = data.configured
        ? `✅ مضبوط (${data.source === "env" ? "بيئة" : "ملف"})`
        : "غير مضبوط.";
    } catch (err) {
      $("#iaStatus").textContent = err.message;
    }
  }

  async function openSettings() {
    const dialog = $("#settingsDialog");
    try {
      const s = await api("/api/settings");
      state.settings = s;
      $("#setOutput").value = s.output_dir || "";
      $("#setBitrate").value = String(s.bitrate || 24);
      $("#setMode").value = s.mode || "cbr";
      $("#setChannels").value = String(s.channels || 1);
      $("#setLayout").value = s.layout || "flat";
      $("#setJobs").value = String(s.jobs || 2);
      $("#setSample").value = String(s.sample_rate || 0);
      $("#setCover").checked = !!s.cover;
      $("#setPlaylist").checked = !!s.playlist;
      $("#updateSection").classList.toggle("hidden", !s.can_update);
      $("#settingsInfo").textContent =
        `الإصدار ${$("#versionText").textContent || ""} · ملف الإعدادات: ${s.config_file || ""}`;
      await Promise.all([loadCookiesStatus(), loadIaStatus()]);
      if (!dialog.open) dialog.showModal();
    } catch (err) {
      toast(err.message, "err");
    }
  }

  async function saveSettings() {
    const payload = {
      output_dir: $("#setOutput").value.trim(),
      bitrate: Number($("#setBitrate").value) || 24,
      mode: $("#setMode").value,
      channels: Number($("#setChannels").value) || 1,
      layout: $("#setLayout").value,
      jobs: Number($("#setJobs").value) || 2,
      sample_rate: Number($("#setSample").value) || 0,
      cover: $("#setCover").checked,
      playlist: $("#setPlaylist").checked,
    };
    const button = $("#settingsSave");
    button.disabled = true;
    try {
      const saved = await api("/api/settings", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.settings = saved;
      toast("حُفظت الإعدادات", "ok");
      syncAddForm(saved);
      refreshStats();
      loadLibrary();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      button.disabled = false;
    }
  }

  function syncAddForm(settings) {
    if (!settings) return;
    if (settings.bitrate) $("#bitrate").value = String(settings.bitrate);
    if (settings.mode) $("#mode").value = settings.mode;
    if (settings.channels) $("#channels").value = String(settings.channels);
    if (settings.layout) $("#layout").value = settings.layout;
    if (settings.sample_rate !== undefined) $("#sampleRate").value = String(settings.sample_rate);
    if (settings.playlist !== undefined) $("#playlist").checked = !!settings.playlist;
    if (settings.cover !== undefined) $("#cover").checked = !!settings.cover;
    updateHint();
  }

  // ── browse ───────────────────────────────────────────────────────────
  async function openBrowse(mode, startPath) {
    state.browseMode = mode;
    const dialog = $("#browseDialog");
    if (!dialog.open) dialog.showModal();
    await loadBrowse(startPath || state.settings?.output_dir || "");
  }

  async function loadBrowse(path) {
    const list = $("#browseList");
    list.innerHTML = `<p class="muted small">جارٍ التحميل…</p>`;
    try {
      const data = await api("/api/browse", { params: path ? { path } : {} });
      state.browsePath = data.path;
      $("#browsePath").value = data.path;
      $("#browseUp").disabled = !data.parent;

      const roots = (data.roots || []).map((root) =>
        `<button type="button" class="chip" data-dir="${esc(root)}">📌 ${esc(root)}</button>`
      ).join("");
      const dirs = (data.dirs || []).map((dir) =>
        `<button type="button" class="browse-item" data-dir="${esc(dir.path)}">
           <span>📁</span><span class="grow">${esc(dir.name)}</span><span>›</span>
         </button>`
      ).join("");
      const files = (data.files || []).map((file) =>
        `<button type="button" class="browse-item" data-file="${esc(file.path)}">
           <span>🎵</span><span class="grow">${esc(file.name)}</span><small>${esc(file.size_text)}</small><span>＋</span>
         </button>`
      ).join("");

      list.innerHTML = `
        ${roots ? `<div class="presets" style="margin-bottom:6px">${roots}</div>` : ""}
        ${dirs || files ? "" : `<p class="muted small">المجلد فارغ (أو لا يحتوي وسائط).</p>`}
        ${dirs}${files}`;
    } catch (err) {
      list.innerHTML = `<p class="error-box">${esc(err.message)}</p>`;
    }
  }

  function addPathToSources(path) {
    const box = $("#sources");
    const existing = box.value.split(/\r?\n/).map((line) => line.trim());
    if (!existing.includes(path)) {
      box.value = [box.value.trim(), path].filter(Boolean).join("\n");
    }
    switchTab("links");
  }

  async function useBrowseDirectory() {
    if (!state.browsePath) return;
    if (state.browseMode === "output") {
      $("#setOutput").value = state.browsePath;
    } else {
      addPathToSources(state.browsePath);
      toast("أُضيف المجلد إلى القائمة", "ok");
    }
    $("#browseDialog").close();
  }

  // ── SSE + polling ────────────────────────────────────────────────────
  function connectEvents() {
    if (state.events) {
      state.events.close();
      state.events = null;
    }
    let source;
    try {
      source = new EventSource(apiUrl("/api/events"));
    } catch {
      setOnline(false);
      return;
    }
    state.events = source;
    source.onopen = () => setOnline(true);
    source.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      if (data.type === "job") {
        upsertJob(data.job);
        if (data.job && data.job.status === "done") refreshStats();
      } else if (data.type === "hello") {
        setOnline(true);
        if (data.stats) {
          $("#statTotal").textContent = data.stats.total ?? 0;
          $("#statDone").textContent = data.stats.done ?? 0;
          $("#statActive").textContent = data.stats.active ?? 0;
          $("#statSize").textContent = data.stats.size_text || "0 B";
          $("#statSeen").textContent = data.stats.seen ?? 0;
        }
      }
    };
    source.onerror = () => {
      setOnline(false);
      source.close();
      if (state.events === source) state.events = null;
      setTimeout(() => {
        if (!state.events) connectEvents();
      }, 6000);
    };
  }

  // ── wiring ───────────────────────────────────────────────────────────
  function bind() {
    // theme
    const stored = localStorage.getItem("ismp3_theme");
    if (stored === "light") document.documentElement.classList.add("light");
    const paintTheme = () => {
      const light = document.documentElement.classList.contains("light");
      $("#themeBtn").textContent = light ? "☀️" : "🌙";
    };
    paintTheme();
    $("#themeBtn").addEventListener("click", () => {
      const light = document.documentElement.classList.toggle("light");
      localStorage.setItem("ismp3_theme", light ? "light" : "dark");
      paintTheme();
    });

    // tabs
    $$(".tab").forEach((tab) => tab.addEventListener("click", () => switchTab(tab.dataset.tab)));

    // add
    $("#addBtn").addEventListener("click", addJobs);
    $("#bitrate").addEventListener("change", () => {
      $$(".preset").forEach((btn) => btn.classList.remove("active"));
      updateHint();
    });
    $("#channels").addEventListener("change", updateHint);
    $("#sampleRate").addEventListener("change", updateHint);
    $$(".preset").forEach((btn) =>
      btn.addEventListener("click", () => applyPreset(btn.dataset.preset))
    );

    // uploads
    $("#pickFilesBtn").addEventListener("click", () => $("#fileInput").click());
    $("#fileInput").addEventListener("change", (event) => {
      uploadFiles(event.target.files);
      event.target.value = "";
    });
    const dropzone = $("#dropzone");
    ["dragenter", "dragover"].forEach((name) =>
      dropzone.addEventListener(name, (event) => {
        event.preventDefault();
        dropzone.classList.add("dragover");
      })
    );
    ["dragleave", "drop"].forEach((name) =>
      dropzone.addEventListener(name, (event) => {
        event.preventDefault();
        dropzone.classList.remove("dragover");
      })
    );
    dropzone.addEventListener("drop", (event) => {
      if (event.dataTransfer?.files?.length) uploadFiles(event.dataTransfer.files);
    });

    // jobs
    $("#refreshBtn").addEventListener("click", () => {
      refreshStats();
      loadJobs();
      loadLibrary();
      refreshHealth();
    });
    $("#search").addEventListener("input", (event) => {
      state.search = event.target.value.trim().toLowerCase();
      scheduleRender();
    });
    $$(".filters .chip").forEach((chip) =>
      chip.addEventListener("click", () => {
        state.filter = chip.dataset.filter || "";
        $$(".filters .chip").forEach((other) => other.classList.toggle("active", other === chip));
        scheduleRender();
      })
    );
    $("#clearBtn").addEventListener("click", async () => {
      if (!confirm("حذف جميع المهام المنتهية وملفاتها؟")) return;
      try {
        const data = await api("/api/jobs/clear", { method: "POST" });
        toast(`حُذفت ${data.deleted || 0} مهمة`, "ok");
        await loadJobs();
        refreshStats();
        loadLibrary();
      } catch (err) {
        toast(err.message, "err");
      }
    });
    $("#jobs").addEventListener("click", async (event) => {
      const button = event.target.closest("[data-action]");
      if (!button) return;
      const card = button.closest(".job");
      if (!card) return;
      button.disabled = true;
      try {
        await jobAction(card.dataset.id, button.dataset.action);
      } catch (err) {
        toast(err.message, "err");
      } finally {
        button.disabled = false;
      }
    });

    // library
    $("#libraryRefresh").addEventListener("click", loadLibrary);
    $("#library").addEventListener("click", (event) => {
      const button = event.target.closest("[data-play]");
      if (!button) return;
      playFile(button.dataset.play, button);
    });

    // settings
    $("#settingsBtn").addEventListener("click", openSettings);
    $("#settingsClose").addEventListener("click", () => $("#settingsDialog").close());
    $("#settingsSave").addEventListener("click", saveSettings);
    $("#setOutputBrowse").addEventListener("click", () =>
      openBrowse("output", $("#setOutput").value.trim())
    );
    $("#cookiesSave").addEventListener("click", async () => {
      const text = $("#setCookiesText").value;
      if (!text.trim()) {
        toast("الصق محتوى cookies.txt", "err");
        return;
      }
      try {
        await api("/api/cookies", { method: "POST", body: JSON.stringify({ text }) });
        $("#setCookiesText").value = "";
        await loadCookiesStatus();
        toast("حُفظت الكوكيز", "ok");
      } catch (err) {
        toast(err.message, "err");
      }
    });
    $("#cookiesDelete").addEventListener("click", async () => {
      try {
        await api("/api/cookies", { method: "DELETE" });
        await loadCookiesStatus();
        toast("حُذفت الكوكيز");
      } catch (err) {
        toast(err.message, "err");
      }
    });
    $("#iaSave").addEventListener("click", async () => {
      const access = $("#setIaAccess").value.trim();
      const secret = $("#setIaSecret").value.trim();
      if (!access || !secret) {
        toast("أدخل access و secret", "err");
        return;
      }
      try {
        await api("/api/ia", { method: "POST", body: JSON.stringify({ access, secret }) });
        $("#setIaSecret").value = "";
        await loadIaStatus();
        toast("حُفظت بيانات Archive.org", "ok");
      } catch (err) {
        toast(err.message, "err");
      }
    });
    $("#iaDelete").addEventListener("click", async () => {
      try {
        await api("/api/ia", { method: "DELETE" });
        await loadIaStatus();
        toast("حُذفت بيانات Archive.org");
      } catch (err) {
        toast(err.message, "err");
      }
    });

    // browse
    $("#browseBtn").addEventListener("click", () => openBrowse("sources", $("#sources").value.trim().split(/\r?\n/).pop() || ""));
    $("#browseClose").addEventListener("click", () => $("#browseDialog").close());
    $("#browseUseDir").addEventListener("click", useBrowseDirectory);
    $("#browseUp").addEventListener("click", async () => {
      const current = $("#browsePath").value;
      if (!current) return;
      const parent = current.replace(/[\\/][^\\/]+[\\/]?$/, "") || "/";
      await loadBrowse(parent);
    });
    $("#browseGo").addEventListener("click", () => loadBrowse($("#browsePath").value.trim()));
    $("#browsePath").addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        loadBrowse($("#browsePath").value.trim());
      }
    });
    $("#browseList").addEventListener("click", async (event) => {
      const target = event.target.closest("[data-dir],[data-file]");
      if (!target) return;
      if (target.dataset.file) {
        if (state.browseMode === "output") {
          $("#setOutput").value = target.dataset.file;
          $("#browseDialog").close();
          return;
        }
        addPathToSources(target.dataset.file);
        toast("أُضيف الملف إلى القائمة", "ok");
        return;
      }
      if (target.dataset.dir) await loadBrowse(target.dataset.dir);
    });

    // update (Replit)
    $("#updateApp").addEventListener("click", async () => {
      if (!confirm("سيتم جلب آخر نسخة من GitHub واستبدال ملفات التطبيق. هل تريد المتابعة؟")) return;
      const button = $("#updateApp");
      const status = $("#updateStatus");
      button.disabled = true;
      status.textContent = "جارٍ التحديث…";
      try {
        const data = await api("/api/update", { method: "POST" });
        status.textContent = data.message;
        toast(data.message, data.updated ? "ok" : "");
      } catch (err) {
        status.textContent = err.message;
        toast(err.message, "err");
      } finally {
        button.disabled = false;
      }
    });

    // avoid the browser opening dropped files outside the dropzone
    window.addEventListener("dragover", (event) => event.preventDefault());
    window.addEventListener("drop", (event) => event.preventDefault());
  }

  function refreshAll() {
    refreshHealth();
    refreshStats();
    loadJobs();
    loadLibrary();
  }

  function init() {
    bind();
    updateHint();
    refreshAll();
    api("/api/settings")
      .then((settings) => {
        state.settings = settings;
        syncAddForm(settings);
      })
      .catch(() => {});
    connectEvents();
    state.pollTimer = setInterval(() => {
      refreshHealth();
      refreshStats();
      if (!state.events) connectEvents();
    }, 20000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
