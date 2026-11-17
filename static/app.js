const urlInput = document.getElementById("url-input");
const pasteBtn = document.getElementById("paste-btn");
const clearBtn = document.getElementById("clear-btn");
const formatSelect = document.getElementById("format-select");
const previewBtn = document.getElementById("preview-btn");
const downloadBtn = document.getElementById("download-btn");
const statusMsg = document.getElementById("status-msg");

const previewCard = document.getElementById("preview-card");
const previewThumb = document.getElementById("preview-thumb");
const previewPlatform = document.getElementById("preview-platform");
const previewDuration = document.getElementById("preview-duration");
const previewTitle = document.getElementById("preview-title");
const previewUploader = document.getElementById("preview-uploader");

const progressCard = document.getElementById("progress-card");
const progressLabel = document.getElementById("progress-label");
const progressPercent = document.getElementById("progress-percent");
const progressBar = document.getElementById("progress-bar");
const progressDetails = document.getElementById("progress-details");
const saveFileLink = document.getElementById("save-file-link");

const historyList = document.getElementById("history-list");
const refreshHistoryBtn = document.getElementById("refresh-history-btn");

const badgeTiktok = document.getElementById("badge-tiktok");
const badgeFacebook = document.getElementById("badge-facebook");
const badgeYoutube = document.getElementById("badge-youtube");

let pollTimer = null;

function extractFirstUrl(text) {
  const match = text.match(/https?:\/\/[^\s<>"']+/i);
  return match ? match[0].replace(/[.,;)]+$/, "") : "";
}

function updatePlatformBadges(text) {
  const lower = text.toLowerCase();
  badgeTiktok.classList.toggle("active-tiktok", lower.includes("tiktok.com"));
  badgeFacebook.classList.toggle(
    "active-facebook",
    lower.includes("facebook.com") || lower.includes("fb.watch") || lower.includes("fb.com")
  );
  badgeYoutube.classList.toggle(
    "active-youtube",
    lower.includes("youtube.com") || lower.includes("youtu.be")
  );
  clearBtn.classList.toggle("hidden", text.trim().length === 0);
}

function showStatus(message, type = "info") {
  statusMsg.textContent = message;
  statusMsg.className = `status-msg ${type}`;
  statusMsg.classList.remove("hidden");
}

function hideStatus() {
  statusMsg.classList.add("hidden");
}

function setBusy(isBusy) {
  previewBtn.disabled = isBusy;
  downloadBtn.disabled = isBusy;
}

async function fetchPreview() {
  const raw = urlInput.value.trim();
  if (!raw) {
    showStatus("Pega primero un enlace de TikTok, Facebook o YouTube.", "error");
    return;
  }

  hideStatus();
  setBusy(true);
  previewBtn.textContent = "Cargando...";

  try {
    const res = await fetch("/api/info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: raw }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "No se pudo leer el enlace.");
    }

    previewThumb.src = data.thumbnail || "/static/icon.svg";
    previewPlatform.textContent = data.platform;
    previewDuration.textContent = data.duration;
    previewTitle.textContent = data.title;
    previewUploader.textContent = data.uploader;
    previewCard.classList.remove("hidden");
  } catch (err) {
    showStatus(err.message, "error");
    previewCard.classList.add("hidden");
  } finally {
    setBusy(false);
    previewBtn.textContent = "Ver vista previa";
  }
}

async function startDownload() {
  const raw = urlInput.value.trim();
  if (!raw) {
    showStatus("Pega primero un enlace de TikTok, Facebook o YouTube.", "error");
    return;
  }

  hideStatus();
  setBusy(true);
  saveFileLink.classList.add("hidden");
  progressCard.classList.remove("hidden");
  progressLabel.textContent = "Iniciando descarga...";
  progressPercent.textContent = "0%";
  progressBar.style.width = "0%";
  progressDetails.textContent = "";

  try {
    const res = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: raw,
        format_choice: formatSelect.value,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Error al iniciar la descarga.");
    }

    pollProgress(data.job_id);
  } catch (err) {
    showStatus(err.message, "error");
    progressCard.classList.add("hidden");
    setBusy(false);
  }
}

function pollProgress(jobId) {
  if (pollTimer) clearInterval(pollTimer);

  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/progress/${jobId}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Error al consultar el progreso.");

      const pct = Math.round(data.progress || 0);
      progressPercent.textContent = `${pct}%`;
      progressBar.style.width = `${pct}%`;

      if (data.status === "queued") {
        progressLabel.textContent = "En cola...";
      } else if (data.status === "downloading") {
        progressLabel.textContent = "Descargando video...";
        const parts = [];
        if (data.speed) parts.push(`Velocidad: ${data.speed}`);
        if (data.eta) parts.push(`Restante: ${data.eta}`);
        progressDetails.textContent = parts.join(" · ");
      } else if (data.status === "processing") {
        progressLabel.textContent = "Procesando audio/video (FFmpeg)...";
        progressDetails.textContent = "Casi listo...";
      } else if (data.status === "completed") {
        clearInterval(pollTimer);
        pollTimer = null;
        setBusy(false);
        progressLabel.textContent = "¡Descarga completada!";
        progressPercent.textContent = "100%";
        progressBar.style.width = "100%";
        progressDetails.textContent = data.filename || "";

        const fileUrl = `/api/file/${jobId}`;
        saveFileLink.href = fileUrl;
        saveFileLink.download = data.filename || "video.mp4";
        saveFileLink.classList.remove("hidden");

        // Trigger automatic browser download
        const autoLink = document.createElement("a");
        autoLink.href = fileUrl;
        autoLink.download = data.filename || "video.mp4";
        document.body.appendChild(autoLink);
        autoLink.click();
        autoLink.remove();

        loadHistory();
      } else if (data.status === "error") {
        clearInterval(pollTimer);
        pollTimer = null;
        setBusy(false);
        progressLabel.textContent = "Error en la descarga";
        showStatus(data.error || "No se pudo descargar el video.", "error");
      }
    } catch (err) {
      clearInterval(pollTimer);
      pollTimer = null;
      setBusy(false);
      showStatus(err.message, "error");
    }
  }, 700);
}

async function loadHistory() {
  try {
    const res = await fetch("/api/history");
    const data = await res.json();
    const files = data.files || [];

    if (files.length === 0) {
      historyList.innerHTML = `<li class="history-empty">Aún no hay videos descargados.</li>`;
      return;
    }

    historyList.innerHTML = "";
    for (const file of files) {
      const li = document.createElement("li");
      li.className = "history-item";

      const nameSpan = document.createElement("span");
      nameSpan.className = "history-name";
      nameSpan.textContent = file.filename;
      nameSpan.title = file.filename;

      const sizeSpan = document.createElement("span");
      sizeSpan.className = "history-size";
      sizeSpan.textContent = `${file.size_mb} MB`;

      const actionsDiv = document.createElement("div");
      actionsDiv.className = "history-actions";

      const dlLink = document.createElement("a");
      dlLink.className = "btn btn-secondary btn-sm";
      dlLink.href = `/api/history/${encodeURIComponent(file.filename)}`;
      dlLink.download = file.filename;
      dlLink.textContent = "Guardar";

      const delBtn = document.createElement("button");
      delBtn.className = "btn btn-ghost btn-sm";
      delBtn.type = "button";
      delBtn.textContent = "✕";
      delBtn.title = "Eliminar";
      delBtn.addEventListener("click", async () => {
        await fetch(`/api/history/${encodeURIComponent(file.filename)}`, { method: "DELETE" });
        loadHistory();
      });

      actionsDiv.appendChild(dlLink);
      actionsDiv.appendChild(delBtn);

      li.appendChild(nameSpan);
      li.appendChild(sizeSpan);
      li.appendChild(actionsDiv);
      historyList.appendChild(li);
    }
  } catch {
    // Ignore history load errors silently
  }
}

urlInput.addEventListener("input", () => {
  updatePlatformBadges(urlInput.value);
});

pasteBtn.addEventListener("click", async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (text) {
      const clean = extractFirstUrl(text) || text.trim();
      urlInput.value = clean;
      updatePlatformBadges(clean);
      hideStatus();
    }
  } catch {
    showStatus("Permite el acceso al portapapeles o pega el enlace con Ctrl+V.", "info");
  }
});

clearBtn.addEventListener("click", () => {
  urlInput.value = "";
  updatePlatformBadges("");
  previewCard.classList.add("hidden");
  hideStatus();
});

previewBtn.addEventListener("click", fetchPreview);
downloadBtn.addEventListener("click", startDownload);
refreshHistoryBtn.addEventListener("click", loadHistory);

urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    fetchPreview();
  }
});

// Handle PWA Web Share Target (?url=... or ?text=...)
window.addEventListener("DOMContentLoaded", () => {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  const params = new URLSearchParams(window.location.search);
  const shared = params.get("url") || params.get("text") || "";
  const extracted = extractFirstUrl(shared);
  if (extracted) {
    urlInput.value = extracted;
    updatePlatformBadges(extracted);
    fetchPreview();
  }

  loadHistory();
});
