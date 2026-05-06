const API = ""; // relative API: same backend that serves this frontend
    let limit = 10;
    let offset = 0;
    let total = 0;

    const $ = (id) => document.getElementById(id);

    function statusClass(status) {
      const s = (status || "").toUpperCase();
      if (s === "OK") return "ok";
      if (s === "REVISAR") return "warn";
      return "risk";
    }

    function statusBadge(status) {
      const cls = statusClass(status);
      return `<span class="badge ${cls}"><span class="dot"></span>${status || "-"}</span>`;
    }

    function severityClass(severity) {
      const s = (severity || "").toLowerCase();
      if (s === "error") return "error";
      if (s === "warning") return "warning";
      return "info";
    }

    function scoreColor(score) {
      if (score >= 75) return "var(--ok)";
      if (score >= 55) return "var(--warn)";
      return "var(--risk)";
    }

    function clampScore(score) {
      return Math.max(0, Math.min(100, Number(score || 0)));
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function formatDate(value) {
      return (value || "").replace("T", " ").slice(0, 19);
    }

    async function checkHealth() {
      try {
        const res = await fetch(API + "/health");
        if (!res.ok) throw new Error("offline");
        $("apiDot").classList.add("online");
        $("apiStatus").textContent = "API online · " + window.location.host;
      } catch {
        $("apiDot").classList.remove("online");
        $("apiStatus").textContent = "API offline · " + window.location.host;
      }
    }

    function buildQuery() {
      const params = new URLSearchParams();
      params.set("limit", limit);
      params.set("offset", offset);

      const filename = $("f_filename").value.trim();
      const minScore = $("f_minscore").value.trim();
      const status = $("f_status").value;

      if (filename) params.set("filename", filename);
      if (minScore !== "") params.set("min_score", minScore);
      if (status) params.set("status", status);

      return params.toString();
    }

    function renderError(targetId, title, message, error = null) {
      $(targetId).innerHTML = `
        <div class="error-box">
          <strong>${escapeHtml(title)}</strong>
          <div>${escapeHtml(message)}</div>
          ${error ? `<pre>${escapeHtml(error.message || error)}</pre>` : ""}
        </div>
      `;
    }

    function renderAnalysis(data) {
      const score = clampScore(data.score);
      const color = scoreColor(score);
      const findings = data.findings || [];

      const findingsHtml = findings.length
        ? `<ul class="findings">
            ${findings.map((f) => `
              <li>
                <span class="severity ${severityClass(f.severity)}">[${escapeHtml((f.severity || "").toUpperCase())}]</span>
                ${escapeHtml(f.message || f.code || "")}
                ${f.hint ? `<div class="muted">${escapeHtml(f.hint)}</div>` : ""}
              </li>
            `).join("")}
          </ul>`
        : `<div class="empty-state"><strong>Sin hallazgos</strong>No se han detectado incidencias relevantes.</div>`;

      $("analysisCard").innerHTML = `
        <div class="result-card">
          <div class="result-top">
            <div>
              <div class="doc-title">${escapeHtml(data.filename || "Documento")}</div>
              <div class="doc-meta">
                <span class="kv">ID: ${escapeHtml(data.id ?? "-")}</span>
                <span class="kv">${escapeHtml(data.pages ?? "-")} pág.</span>
                <span class="kv">${escapeHtml(data.chars ?? "-")} chars</span>
                <span class="kv">${escapeHtml(data.document_type || "-")}</span>
                ${statusBadge(data.status)}
              </div>
            </div>
            <div class="score-ring" style="background: conic-gradient(${color} ${score * 3.6}deg, rgba(148, 163, 184, 0.16) 0deg)">
              <span>${score}</span>
            </div>
          </div>

          <div class="progress"><div style="width:${score}%; background:${color}"></div></div>
          <div class="summary"><strong>Resumen:</strong> ${escapeHtml(data.summary || "Sin resumen disponible.")}</div>

          <h3 style="margin:18px 0 0;">Validaciones detectadas</h3>
          ${findingsHtml}
        </div>
      `;
    }

    async function analyze() {
      const fileInput = $("file");
      if (!fileInput.files.length) {
        alert("Selecciona un PDF");
        return;
      }

      $("analysisCard").innerHTML = `
        <div class="empty-state loading">
          <strong>Analizando PDF...</strong>
          Extrayendo texto, aplicando reglas y guardando el informe en PostgreSQL.
        </div>
      `;

      try {
        const form = new FormData();
        form.append("file", fileInput.files[0]);

        const res = await fetch(API + "/analyze", { method: "POST", body: form });
        if (!res.ok) {
          const errorText = await res.text();
          throw new Error(`HTTP ${res.status}: ${errorText}`);
        }

        const data = await res.json();
        renderAnalysis(data);
        offset = 0;
        await loadAnalyses(false);
      } catch (error) {
        renderError("analysisCard", "Error al analizar el PDF", `Comprueba que el backend esté corriendo en ${window.location.origin}`, error);
      }
    }

    async function loadAnalyses(resetOffset = false) {
      if (resetOffset) offset = 0;

      try {
        const res = await fetch(API + "/analyses?" + buildQuery());
        if (!res.ok) {
          const errorText = await res.text();
          throw new Error(`HTTP ${res.status}: ${errorText}`);
        }

        const data = await res.json();
        total = Number(data.total || 0);
        const items = data.items || [];

        $("historyBody").innerHTML = items.length
          ? items.map((a) => `
              <tr data-id="${escapeHtml(a.id)}">
                <td>${escapeHtml(a.id)}</td>
                <td>${escapeHtml(formatDate(a.created_at))}</td>
                <td>${escapeHtml(a.filename)}</td>
                <td>${statusBadge(a.status)}</td>
                <td class="right"><strong>${escapeHtml(a.score ?? "")}</strong></td>
                <td class="right">
                  <a class="export-link" href="${API}/analyses/${encodeURIComponent(a.id)}/export" target="_blank" onclick="event.stopPropagation()">JSON</a>
                </td>
              </tr>
            `).join("")
          : `<tr><td colspan="6" class="muted">No hay resultados.</td></tr>`;

        document.querySelectorAll("#historyBody tr[data-id]").forEach((row) => {
          row.addEventListener("click", () => openDetail(row.dataset.id));
        });

        const from = total === 0 ? 0 : offset + 1;
        const to = Math.min(offset + limit, total);
        $("pageInfo").textContent = `Mostrando ${from}-${to} de ${total}`;
      } catch (error) {
        $("historyBody").innerHTML = `
          <tr>
            <td colspan="6">
              <div class="error-box">
                <strong>No se pudo cargar el historial</strong>
                <div>Comprueba que la API esté disponible en ${API}</div>
              </div>
            </td>
          </tr>
        `;
      }
    }

    async function openDetail(id) {
      try {
        const res = await fetch(API + "/analyses/" + encodeURIComponent(id));
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const score = clampScore(data.score);
        const color = scoreColor(score);
        const findings = data.findings || [];

        $("modalTitle").textContent = `Análisis #${data.id}`;
        $("modalSubtitle").textContent = data.filename || "";
        $("modalContent").innerHTML = `
          <div class="result-card">
            <div class="result-top">
              <div>
                <div class="doc-meta">
                  ${statusBadge(data.status)}
                  <span class="kv">Score: ${escapeHtml(score)}/100</span>
                  <span class="kv">Fecha: ${escapeHtml(formatDate(data.created_at))}</span>
                  <span class="kv">Tipo: ${escapeHtml(data.document_type || "-")}</span>
                </div>
              </div>
              <a class="export-link" href="${API}/analyses/${encodeURIComponent(data.id)}/export" target="_blank">Exportar JSON</a>
            </div>

            <div class="progress"><div style="width:${score}%; background:${color}"></div></div>
            <div class="summary"><strong>Resumen:</strong> ${escapeHtml(data.summary || "")}</div>

            <h3 style="margin:18px 0 0;">Findings</h3>
            ${findings.length ? `<ul class="findings">
              ${findings.map((f) => `
                <li>
                  <span class="severity ${severityClass(f.severity)}">[${escapeHtml((f.severity || "").toUpperCase())}]</span>
                  ${escapeHtml(f.message || f.code || "")}
                  ${f.hint ? `<div class="muted">${escapeHtml(f.hint)}</div>` : ""}
                </li>
              `).join("")}
            </ul>` : `<div class="empty-state"><strong>Sin hallazgos</strong>No se han detectado incidencias relevantes.</div>`}
          </div>
        `;
        $("modal").classList.add("open");
        $("modal").setAttribute("aria-hidden", "false");
      } catch (error) {
        alert("No se pudo abrir el análisis.");
      }
    }

    function closeModal() {
      $("modal").classList.remove("open");
      $("modal").setAttribute("aria-hidden", "true");
    }

    function resetFilters() {
      $("f_filename").value = "";
      $("f_minscore").value = "";
      $("f_status").value = "";
      offset = 0;
      loadAnalyses(false);
    }

    document.addEventListener("DOMContentLoaded", async () => {
      $("btnAnalyze").addEventListener("click", analyze);
      $("btnRefresh").addEventListener("click", () => loadAnalyses(false));
      $("btnApply").addEventListener("click", () => loadAnalyses(true));
      $("btnClear").addEventListener("click", resetFilters);
      $("btnPrev").addEventListener("click", () => {
        offset = Math.max(0, offset - limit);
        loadAnalyses(false);
      });
      $("btnNext").addEventListener("click", () => {
        if (offset + limit >= total) return;
        offset += limit;
        loadAnalyses(false);
      });
      $("modalClose").addEventListener("click", closeModal);
      $("modalBackdrop").addEventListener("click", closeModal);
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") closeModal();
      });

      $("file").addEventListener("change", () => {
        const file = $("file").files[0];
        $("selectedFile").textContent = file ? file.name : "Ningún archivo seleccionado";
      });

      const dropZone = $("dropZone");
      dropZone.addEventListener("dragover", (event) => {
        event.preventDefault();
        dropZone.style.borderColor = "rgba(125, 211, 252, 0.85)";
      });
      dropZone.addEventListener("dragleave", () => {
        dropZone.style.borderColor = "rgba(125, 211, 252, 0.34)";
      });
      dropZone.addEventListener("drop", (event) => {
        event.preventDefault();
        dropZone.style.borderColor = "rgba(125, 211, 252, 0.34)";
        const file = event.dataTransfer.files[0];
        if (!file) return;
        const dataTransfer = new DataTransfer();
        dataTransfer.items.add(file);
        $("file").files = dataTransfer.files;
        $("selectedFile").textContent = file.name;
      });

      await checkHealth();
      await loadAnalyses(true);
      setInterval(checkHealth, 10000);
    });
