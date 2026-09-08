// ============================================================
// AUDIT FOLLOW-UP TRACKER - FRONTEND CONTROLLER
// ============================================================

let currentStep = 1;
let currentFindings = [];
let currentReports = [];

function el(id) { return document.getElementById(id); }
function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function showToast(message, type = "info") {
    const toast = el("toast");
    if (!toast) return;
    toast.textContent = message;
    toast.style.background = type === "error" ? "#B42318" : type === "success" ? "#2E7D32" : type === "warning" ? "#8A6200" : "#0F172A";
    toast.style.display = "block";
    setTimeout(() => { toast.style.display = "none"; }, 3500);
}

function goToStep(step) {
    const target = Math.max(1, Math.min(5, Number(step) || 1));
    currentStep = target;

    document.querySelectorAll(".page-step").forEach(section => {
        section.classList.toggle("active", section.id === `step${target}`);
    });
    document.querySelectorAll(".nav-item").forEach(button => {
        const number = Number(button.dataset.step);
        button.classList.toggle("active", number === target);
    });

    if (target === 1) loadReports();
    if (target === 2) loadProcessMatrix();
    if (target === 3) loadDashboardKPIs();
    if (target === 4) loadFindingsWithFilters();
    if (target === 5) loadCommitteeReport();

    window.scrollTo({ top: 0, behavior: "smooth" });
}

// ============================================================
// PASO 1: INGESTA DE INFORMES
// ============================================================

async function uploadAuditReport(file) {
    if (!file) return;
    showToast("Analizando e ingestado informe de auditoría...", "info");

    const form = new FormData();
    form.append("file", file);

    try {
        const response = await fetch("/upload-report", { method: "POST", body: form });
        let data = {};
        try { data = await response.json(); } catch (_) {}
        if (!response.ok) throw new Error(data.error || "No se pudo procesar el informe.");

        showToast(data.message || "Informe ingresado correctamente.", "success");
        loadReports();
        goToStep(2);
    } catch (err) {
        console.error(err);
        showToast(err.message || "Error al cargar el informe.", "error");
    }
}

async function loadReports() {
    try {
        const response = await fetch("/reports");
        if (!response.ok) return;
        const data = await response.json();
        currentReports = data.reports || [];
        renderReportsList(currentReports);
    } catch (err) {
        console.error("Error cargando informes:", err);
    }
}

function renderReportsList(reports) {
    const container = el("reportsList");
    if (!container) return;
    if (!reports.length) {
        container.innerHTML = `<div style="text-align: center; color: #64748b; padding: 24px;">No hay informes cargados aún. Subí el primer informe de auditoría arriba.</div>`;
        return;
    }
    container.innerHTML = reports.map(r => `
        <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 14px 18px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center;">
            <div>
                <strong style="font-size: 15px; color: #0F172A;">${escapeHtml(r.title)}</strong>
                <div style="font-size: 12px; color: #64748B; margin-top: 2px;">
                    Proceso: <strong>${escapeHtml(r.process)}</strong> · Área: <strong>${escapeHtml(r.area)}</strong> · Auditor: <strong>${escapeHtml(r.auditor)}</strong>
                </div>
            </div>
            <button class="btn btn-secondary" onclick="deleteReportItem('${r.id}')">Eliminar</button>
        </div>
    `).join("");
}

async function deleteReportItem(reportId) {
    if (!confirm("¿Eliminar este informe y todas sus recomendaciones asociadas?")) return;
    try {
        const response = await fetch(`/reports/${reportId}`, { method: "DELETE" });
        if (response.ok) {
            showToast("Informe eliminado.", "success");
            loadReports();
        }
    } catch (err) {
        console.error(err);
    }
}

// ============================================================
// PASO 2: MATRIZ DE PROCESOS
// ============================================================

async function loadProcessMatrix() {
    try {
        const response = await fetch("/findings");
        if (!response.ok) return;
        const data = await response.json();
        currentFindings = data.findings || [];
        renderProcessMatrix(currentFindings);
    } catch (err) {
        console.error("Error cargando matriz de procesos:", err);
    }
}

function renderProcessMatrix(findings) {
    const container = el("processMatrixContainer");
    if (!container) return;
    if (!findings.length) {
        container.innerHTML = `<div style="text-align: center; color: #64748b; padding: 30px;">Cargá un informe de auditoría en el Paso 1 para armar la Matriz de Procesos.</div>`;
        return;
    }

    // Agrupar hallazgos por Proceso
    const byProcess = {};
    findings.forEach(f => {
        const proc = f.report_process || f.process_step || "Proceso General";
        if (!byProcess[proc]) byProcess[proc] = [];
        byProcess[proc].push(f);
    });

    let html = "";
    Object.entries(byProcess).forEach(([procName, items]) => {
        html += `
            <div style="margin-bottom: 24px; border: 1px solid #E2E8F0; border-radius: 10px; overflow: hidden;">
                <div style="background: #17365D; color: white; padding: 12px 18px; font-weight: 700; font-size: 15px; display: flex; justify-content: space-between;">
                    <span>📁 PROCESO: ${escapeHtml(procName)}</span>
                    <span style="font-size: 12px; font-weight: 400; opacity: 0.8;">${items.length} mejora(s)</span>
                </div>
                <div style="padding: 16px;">
                    <table class="tracker-table">
                        <thead>
                            <tr>
                                <th>Código</th>
                                <th>Tipo</th>
                                <th>Mejora / Observación</th>
                                <th>Criticidad</th>
                                <th>Área Responsable</th>
                                <th>Estado</th>
                                <th>Acciones</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${items.map(item => `
                                <tr>
                                    <td><strong>${escapeHtml(item.code)}</strong></td>
                                    <td><span style="font-size: 11px; background: #F1F5F9; padding: 2px 6px; border-radius: 4px;">${escapeHtml(item.type)}</span></td>
                                    <td>
                                        <strong>${escapeHtml(item.title)}</strong>
                                        <div style="font-size: 11px; color: #64748B;">${escapeHtml(item.situation)}</div>
                                    </td>
                                    <td><span class="badge badge-${(item.severity||'media').toLowerCase()}">${escapeHtml(item.severity)}</span></td>
                                    <td>${escapeHtml(item.responsible_area)}</td>
                                    <td><span class="status-badge status-${(item.status||'pendiente').toLowerCase().replace(/\s+/g, '-').replace('/', '')}">${escapeHtml(item.status)}</span></td>
                                    <td><button class="ai-button" onclick="openFindingModal('${item.id}')">Gestionar</button></td>
                                </tr>
                            `).join("")}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    });
    container.innerHTML = html;
}

function filterMatrix(query) {
    const q = (query || "").toLowerCase().trim();
    if (!q) {
        renderProcessMatrix(currentFindings);
        return;
    }
    const filtered = currentFindings.filter(f =>
        (f.title || "").toLowerCase().includes(q) ||
        (f.situation || "").toLowerCase().includes(q) ||
        (f.report_process || "").toLowerCase().includes(q) ||
        (f.code || "").toLowerCase().includes(q)
    );
    renderProcessMatrix(filtered);
}

// ============================================================
// PASO 3: DASHBOARD EJECUTIVO
// ============================================================

async function loadDashboardKPIs() {
    try {
        const response = await fetch("/dashboard-stats");
        if (!response.ok) return;
        const stats = await response.json();

        if (el("kpiReports")) el("kpiReports").textContent = stats.total_reports || 0;
        if (el("kpiFindings")) el("kpiFindings").textContent = stats.total_findings || 0;
        if (el("kpiClosed")) el("kpiClosed").textContent = stats.closed_findings || 0;
        if (el("kpiInProgress")) el("kpiInProgress").textContent = stats.in_progress_findings || 0;
        if (el("kpiOverdue")) el("kpiOverdue").textContent = stats.overdue_findings || 0;
        if (el("kpiRate")) el("kpiRate").textContent = `${stats.resolution_rate || 0}%`;

        renderAreaBreakdown(stats.area_breakdown || {});
    } catch (err) {
        console.error("Error cargando dashboard:", err);
    }
}

function renderAreaBreakdown(areas) {
    const container = el("areaBreakdownContainer");
    if (!container) return;
    const entries = Object.entries(areas);
    if (!entries.length) {
        container.innerHTML = `<div style="text-align: center; color: #64748b; padding: 20px;">No hay datos de áreas para mostrar.</div>`;
        return;
    }

    container.innerHTML = entries.map(([area, data]) => {
        const pct = data.total > 0 ? Math.round((data.closed / data.total) * 100) : 0;
        return `
            <div style="margin-bottom: 16px;">
                <div style="display: flex; justify-content: space-between; font-size: 13px; font-weight: 600; margin-bottom: 4px;">
                    <span>${escapeHtml(area)}</span>
                    <span>${data.closed} de ${data.total} resueltos (${pct}%)</span>
                </div>
                <div style="background: #E2E8F0; border-radius: 6px; height: 10px; overflow: hidden;">
                    <div style="background: ${pct >= 80 ? '#2E7D32' : pct >= 50 ? '#1E40AF' : '#B42318'}; width: ${pct}%; height: 100%; transition: width 0.4s ease;"></div>
                </div>
            </div>
        `;
    }).join("");
}

// ============================================================
// PASO 4: SEGUIMIENTO & MODAL
// ============================================================

async function loadFindingsWithFilters() {
    const status = el("filterStatus")?.value || "";
    const severity = el("filterSeverity")?.value || "";
    const search = el("searchFinding")?.value || "";

    const params = new URLSearchParams();
    if (status) params.append("status", status);
    if (severity) params.append("severity", severity);
    if (search) params.append("search", search);

    try {
        const response = await fetch(`/findings?${params.toString()}`);
        if (!response.ok) return;
        const data = await response.json();
        renderFindingsTable(data.findings || []);
    } catch (err) {
        console.error("Error cargando hallazgos:", err);
    }
}

function renderFindingsTable(findings) {
    const container = el("findingsTableContainer");
    if (!container) return;
    if (!findings.length) {
        container.innerHTML = `<div style="text-align: center; color: #64748b; padding: 24px;">No se encontraron hallazgos con los filtros seleccionados.</div>`;
        return;
    }

    container.innerHTML = `
        <table class="tracker-table">
            <thead>
                <tr>
                    <th>Código</th>
                    <th>Informe</th>
                    <th>Título Mejora</th>
                    <th>Criticidad</th>
                    <th>Área / Responsable</th>
                    <th>Fecha Compromiso</th>
                    <th>Estado</th>
                    <th>Acciones</th>
                </tr>
            </thead>
            <tbody>
                ${findings.map(f => `
                    <tr>
                        <td><strong>${escapeHtml(f.code)}</strong></td>
                        <td style="font-size: 11px;">${escapeHtml(f.report_title)}</td>
                        <td>
                            <strong>${escapeHtml(f.title)}</strong>
                            <div style="font-size: 11px; color: #64748B;">${escapeHtml(f.situation)}</div>
                        </td>
                        <td><span class="badge badge-${(f.severity||'media').toLowerCase()}">${escapeHtml(f.severity)}</span></td>
                        <td><strong>${escapeHtml(f.responsible_area)}</strong><br><small>${escapeHtml(f.action_owner)}</small></td>
                        <td>${escapeHtml(f.target_date || 'Pendiente')}</td>
                        <td><span class="status-badge status-${(f.status||'pendiente').toLowerCase().replace(/\s+/g, '-').replace('/', '')}">${escapeHtml(f.status)}</span></td>
                        <td>
                            <button class="btn btn-primary" style="padding: 4px 8px; font-size: 11px;" onclick="openFindingModal('${f.id}')">Seguimiento</button>
                        </td>
                    </tr>
                `).join("")}
            </tbody>
        </table>
    `;
}

async function openFindingModal(findingId) {
    try {
        const response = await fetch(`/findings/${findingId}`);
        if (!response.ok) return;
        const f = await response.json();

        if (el("modalFindingId")) el("modalFindingId").value = f.id;
        if (el("modalFindingTitle")) el("modalFindingTitle").textContent = `${f.code} - ${f.title}`;
        if (el("modalStatus")) el("modalStatus").value = f.status || "Pendiente";
        if (el("modalTargetDate")) el("modalTargetDate").value = f.target_date || "";
        if (el("modalNotes")) el("modalNotes").value = f.follow_up_notes || "";
        if (el("modalEvidence")) el("modalEvidence").value = f.evidence_file || "";

        if (el("findingModal")) el("findingModal").style.display = "flex";
    } catch (err) {
        console.error("Error abriendo modal:", err);
    }
}

function closeFindingModal() {
    if (el("findingModal")) el("findingModal").style.display = "none";
}

async function saveFindingStatusUpdate() {
    const findingId = el("modalFindingId")?.value;
    const status = el("modalStatus")?.value;
    const target_date = el("modalTargetDate")?.value;
    const notes = el("modalNotes")?.value;
    const evidence_file = el("modalEvidence")?.value;

    if (!findingId || !status) return;

    try {
        const response = await fetch(`/findings/${findingId}/status`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status, notes, target_date, evidence_file })
        });
        if (response.ok) {
            showToast("Seguimiento actualizado.", "success");
            closeFindingModal();
            loadFindingsWithFilters();
            loadDashboardKPIs();
        } else {
            showToast("No se pudo actualizar el estado.", "error");
        }
    } catch (err) {
        console.error(err);
        showToast("Error al guardar actualización.", "error");
    }
}

// ============================================================
// PASO 5: REPORTE COMITÉ
// ============================================================

async function loadCommitteeReport() {
    try {
        const response = await fetch("/findings");
        if (!response.ok) return;
        const data = await response.json();
        renderCommitteeReport(data.findings || []);
    } catch (err) {
        console.error("Error cargando reporte comité:", err);
    }
}

function renderCommitteeReport(findings) {
    const container = el("committeeReportContainer");
    if (!container) return;
    if (!findings.length) {
        container.innerHTML = `<div style="text-align: center; color: #64748b; padding: 24px;">No hay hallazgos para mostrar en el informe consolidado.</div>`;
        return;
    }

    container.innerHTML = `
        <div style="margin-bottom: 16px; padding: 12px; background: #F8FAFC; border-radius: 8px; border: 1px solid #E2E8F0;">
            <strong>Informe Consolidado para la Gerencia y Comité de Auditoría</strong>
            <p style="font-size: 12px; color: #64748B;">Resumen estructurado de todas las observaciones, criticidades y grado de avance.</p>
        </div>
        <table class="tracker-table">
            <thead>
                <tr>
                    <th>Código</th>
                    <th>Informe de Origen</th>
                    <th>Título Mejora</th>
                    <th>Criticidad</th>
                    <th>Área</th>
                    <th>Estado</th>
                </tr>
            </thead>
            <tbody>
                ${findings.map(f => `
                    <tr>
                        <td><strong>${escapeHtml(f.code)}</strong></td>
                        <td>${escapeHtml(f.report_title)}</td>
                        <td><strong>${escapeHtml(f.title)}</strong></td>
                        <td><span class="badge badge-${(f.severity||'media').toLowerCase()}">${escapeHtml(f.severity)}</span></td>
                        <td>${escapeHtml(f.responsible_area)}</td>
                        <td><span class="status-badge status-${(f.status||'pendiente').toLowerCase().replace(/\s+/g, '-').replace('/', '')}">${escapeHtml(f.status)}</span></td>
                    </tr>
                `).join("")}
            </tbody>
        </table>
    `;
}

async function exportExcelReport() {
    showToast("Generando reporte Excel consolidado...", "info");
    try {
        const response = await fetch("/export-excel", { method: "POST" });
        if (!response.ok) throw new Error("No se pudo generar el archivo Excel.");
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `Seguimiento_Auditoria_${new Date().toISOString().slice(0,10)}.xlsx`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        showToast("Excel exportado correctamente.", "success");
    } catch (err) {
        console.error(err);
        showToast(err.message || "Error al exportar a Excel.", "error");
    }
}

// Inicialización
document.addEventListener("DOMContentLoaded", () => {
    goToStep(1);
});
