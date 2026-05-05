const API = window.location.origin;

let page = 0;
const limit = 10;

const el = (id) => document.getElementById(id);

function statusBadge(status) {
  const s = (status || "").toUpperCase();
  let cls = "warn";
  if (s === "OK") cls = "ok";
  if (s === "RIESGO" || s === "RISK") cls = "risk";

  return `<span class="badge ${cls}">
    <span class="dot"></span>${status}
  </span>`;
}

function severityClass(sev) {
  const s = (sev || "").toLowerCase();
  if (s === "error") return "sev-error";
  if (s === "warning") return "sev-warning";
  return "sev-info";
}

function progressColor(score) {
  if (score >= 75) return "var(--ok)";
  if (score >= 55) return "var(--warn)";
  return "var(--risk)";
}

/* ---------- RESULT RENDER ---------- */
function renderResult(data) {
  const findings = data.findings || [];
  const score = data.score ?? 0;

  const findingsHtml = findings.length
    ? `<ul class="findings">
        ${findings.map(f => `
          <li>
            <span class="${severityClass(f.severity)}">[${(f.severity || "").toUpperCase()}]</span>
            ${f.message || ""}
            ${f.hint ? `<span class="muted"> — ${f.hint}</span>` : ""}
          </li>
        `).join("")}
      </ul>`
    : `<div class="muted">No hay findings.</div>`;

  el("resultArea").innerHTML = `
    <div class="result-grid">
      <div class="result-card">
        <div class="result-title">${data.filename || "Documento"}</div>
        <div class="result-meta">
          ${statusBadge(data.status)}
          <div class="kv"><b>Score:</b> ${score}/100</div>
          <div class="kv"><b>Páginas:</b> ${data.pages ?? "-"}</div>
          <div class="kv"><b>Chars:</b> ${data.chars ?? "-"}</div>
        </div>

        <div class="progress"><div id="pbar"></div></div>
        <div class="muted">${data.summary || ""}</div>
      </div>

      <div class="result-card">
        <div class="result-title">Detalles</div>
        ${findingsHtml}
      </div>
    </div>
  `;

  const bar = document.getElementById("pbar");
  bar.style.width = `${Math.max(0, Math.min(100, score))}%`;
  bar.style.background = progressColor(score);
}

/* ---------- ANALYZE ---------- */
async function analyze() {
  const f = el("file");
  if (!f.files.length) return alert("Selecciona un PDF");

  const form = new FormData();
  form.append("file", f.files[0]);

  el("resultArea").innerHTML = `<div class="empty-state">
    <div class="empty-title">Analizando...</div>
    <div class="muted">Procesando PDF y validando reglas</div>
  </div>`;

  try {
    const res = await fetch(API + "/analyze", { method: "POST", body: form });

    if (!res.ok) {
      throw new Error(`Error ${res.status}`);
    }

    const data = await res.json();

    renderResult(data);
    await loadAnalyses();
  } catch (error) {
    el("resultArea").innerHTML = `
      <div class="empty-state">
        <div class="empty-title">Error al analizar el PDF</div>
        <div class="muted">Comprueba que el backend esté corriendo en ${API}</div>
      </div>
    `;
  }
}

/* ---------- LIST / FILTERS ---------- */
function readFilters() {
  return {
    filename: el("fFilename").value.trim(),
    min_score: Number(el("fMinScore").value || 0),
    status: el("fStatus").value.trim(),
  };
}

async function loadAnalyses() {
  const { filename, min_score, status } = readFilters();

  const qs = new URLSearchParams();
  qs.set("limit", limit);
  qs.set("offset", page * limit);

  // Estos parámetros deben existir en tu backend.
  // Si todavía no los has metido, te los meto yo en el main + repository.
  if (filename) qs.set("filename", filename);
  if (min_score && min_score > 0) qs.set("min_score", String(min_score));
  if (status) qs.set("status", status);

  const res = await fetch(`${API}/analyses?${qs.toString()}`);

if (!res.ok) {
  el("historyBody").innerHTML = `
    <tr>
      <td colspan="5">
        <div class="muted">No se pudo cargar el historial.</div>
      </td>
    </tr>
  `;
  return;
}

const data = await res.json();

  const items = data.items || [];
  const tbody = items.length ? items.map(row => `
      <tr data-id="${row.id}">
        <td>${row.id}</td>
        <td>${row.filename}</td>
        <td>${statusBadge(row.status)}</td>
        <td>${row.score}</td>
        <td class="muted">${(row.created_at || "").replace("T"," ").slice(0,19)}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="5"><div class="muted">No hay resultados</div></td></tr>`;

  el("historyBody").innerHTML = tbody;
  el("pageInfo").textContent = `Página ${page + 1}`;

  // click row -> modal detail
  document.querySelectorAll("#historyBody tr[data-id]").forEach(tr => {
    tr.addEventListener("click", () => openDetail(tr.dataset.id));
  });
}

/* ---------- DETAIL MODAL ---------- */
async function openDetail(id) {
  const res = await fetch(API + "/analyses/" + id);
  const data = await res.json();

  el("modalTitle").textContent = `Análisis #${data.id}`;
  el("modalSubtitle").textContent = data.filename || "";

  el("modalContent").innerHTML = `
    <div class="result-meta" style="margin:0 0 10px;">
      ${statusBadge(data.status)}
      <div class="kv"><b>Score:</b> ${data.score}/100</div>
      <div class="kv"><b>Fecha:</b> ${(data.created_at || "").replace("T"," ").slice(0,19)}</div>
      <div class="kv"><b>Tipo:</b> ${data.document_type || "-"}</div>
      <a class="export-link" href="${API}/analyses/${id}/export" target="_blank">
  Exportar JSON
</a>
    </div>

    <div class="progress"><div style="width:${data.score}%; background:${progressColor(data.score)}"></div></div>

    <div class="muted" style="margin:10px 0 14px;">${data.summary || ""}</div>

    <h3 style="margin:0 0 8px;">Findings</h3>
    ${
      (data.findings || []).length
      ? `<ul class="findings">${
          data.findings.map(f => `
            <li>
              <span class="${severityClass(f.severity)}">[${(f.severity || "").toUpperCase()}]</span>
              ${f.message || f.code || ""}
              ${f.hint ? `<span class="muted"> — ${f.hint}</span>` : ""}
            </li>
          `).join("")
        }</ul>`
      : `<div class="muted">No hay findings.</div>`
    }
  `;

  el("modal").classList.add("open");
}

function closeModal() {
  el("modal").classList.remove("open");
}

/* ---------- EVENTS ---------- */
document.addEventListener("DOMContentLoaded", async () => {
  el("btnAnalyze").addEventListener("click", analyze);
  el("btnRefresh").addEventListener("click", loadAnalyses);

  el("btnApply").addEventListener("click", () => { page = 0; loadAnalyses(); });
  el("btnClear").addEventListener("click", () => {
    el("fFilename").value = "";
    el("fMinScore").value = 0;
    el("fStatus").value = "";
    page = 0;
    loadAnalyses();
  });

  el("btnPrev").addEventListener("click", () => { if (page > 0) page--; loadAnalyses(); });
  el("btnNext").addEventListener("click", () => { page++; loadAnalyses(); });

  el("modalClose").addEventListener("click", closeModal);
  el("modalBackdrop").addEventListener("click", closeModal);

  await loadAnalyses();
});
