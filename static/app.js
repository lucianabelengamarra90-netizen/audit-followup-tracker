// ============================================================
// AUDITTRACK - FRONTEND CONTROLLER (EXACT REPLICATION)
// ============================================================

let currentItems = [];
let currentReports = [];
let activeFilters = {
    report: "",
    area: "",
    status: "",
    risk: "",
    search: ""
};

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
    toast.style.background = type === "error" ? "#DC2626" : type === "success" ? "#16A34A" : type === "warning" ? "#D97706" : "#0F172A";
    toast.style.display = "block";
    setTimeout(() => { toast.style.display = "none"; }, 3500);
}

// ============================================================
// TAB NAVIGATION
// ============================================================

function switchTab(tabName) {
    document.querySelectorAll(".menu-item").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.tab === tabName);
    });

    document.querySelectorAll(".tab-pane").forEach(pane => {
        pane.classList.toggle("active", pane.id === `tab-${tabName}`);
    });

    if (tabName === "informes") loadReports();
    if (tabName === "tableros") loadDashboardKPIs();
}

// ============================================================
// DATA LOADING & FILTERING
// ============================================================

async function loadAuditTrackData() {
    try {
        const response = await fetch("/findings");
        if (!response.ok) return;
        const data = await response.json();
        currentItems = data.findings || [];
        
        populateFilterDropdowns(currentItems);
        renderAuditTrackTable(currentItems);
        updateSidebarMetrics(currentItems);
    } catch (err) {
        console.error("Error cargando datos de AuditTrack:", err);
    }
}

function populateFilterDropdowns(items) {
    const reportSelect = el("filterReportSelect");
    const areaSelect = el("filterAreaSelect");

    if (reportSelect) {
        const reports = Array.from(new Set(items.map(i => i.report_title).filter(Boolean))).sort();
        const currentVal = reportSelect.value;
        reportSelect.innerHTML = `<option value="">Todos los informes</option>` +
            reports.map(r => `<option value="${escapeHtml(r)}"${r === currentVal ? " selected" : ""}>${escapeHtml(r)}</option>`).join("");
    }

    if (areaSelect) {
        const areas = Array.from(new Set(items.map(i => i.responsible_area).filter(Boolean))).sort();
        const currentVal = areaSelect.value;
        areaSelect.innerHTML = `<option value="">Todas las áreas</option>` +
            areas.map(a => `<option value="${escapeHtml(a)}"${a === currentVal ? " selected" : ""}>${escapeHtml(a)}</option>`).join("");
    }
}

function applyFilters() {
    activeFilters.report = el("filterReportSelect")?.value || "";
    activeFilters.area = el("filterAreaSelect")?.value || "";
    activeFilters.status = el("filterStatusSelect")?.value || "";
    activeFilters.risk = el("filterRiskSelect")?.value || "";
    
    filterAndRenderTable();
}

function onGlobalSearch(query) {
    activeFilters.search = (query || "").trim();
    filterAndRenderTable();
}

function clearFilters() {
    if (el("filterReportSelect")) el("filterReportSelect").value = "";
    if (el("filterAreaSelect")) el("filterAreaSelect").value = "";
    if (el("filterStatusSelect")) el("filterStatusSelect").value = "";
    if (el("filterRiskSelect")) el("filterRiskSelect").value = "";
    if (el("globalSearchInput")) el("globalSearchInput").value = "";

    activeFilters = { report: "", area: "", status: "", risk: "", search: "" };
    filterAndRenderTable();
}

function toggleFilterBar() {
    const bar = el("filterBarContainer");
    if (bar) {
        bar.style.display = bar.style.display === "none" ? "flex" : "none";
    }
}

function filterAndRenderTable() {
    let filtered = [...currentItems];

    if (activeFilters.report) {
        filtered = filtered.filter(i => (i.report_title || "").toLowerCase() === activeFilters.report.toLowerCase());
    }

    if (activeFilters.area) {
        filtered = filtered.filter(i => (i.responsible_area || "").toLowerCase() === activeFilters.area.toLowerCase());
    }

    if (activeFilters.status) {
        filtered = filtered.filter(i => (i.status || "").toLowerCase() === activeFilters.status.toLowerCase());
    }

    if (activeFilters.risk) {
        filtered = filtered.filter(i => (i.severity || "").toLowerCase() === activeFilters.risk.toLowerCase());
    }

    if (activeFilters.search) {
        const q = activeFilters.search.toLowerCase();
        filtered = filtered.filter(i =>
            (i.code || "").toLowerCase().includes(q) ||
            (i.title || "").toLowerCase().includes(q) ||
            (i.responsible_area || "").toLowerCase().includes(q) ||
            (i.report_title || "").toLowerCase().includes(q) ||
            (i.source_filename || "").toLowerCase().includes(q) ||
            (i.action_owner || "").toLowerCase().includes(q)
        );
    }

    renderAuditTrackTable(filtered);
}

// ============================================================
// TABLE RENDERER
// ============================================================

function renderAuditTrackTable(items) {
    const tbody = el("auditTrackTableBody");
    const countSpan = el("showingRecordsCount");
    if (!tbody) return;

    if (countSpan) {
        countSpan.textContent = `Mostrando ${items.length} de ${currentItems.length} registros`;
    }

    if (!items.length) {
        tbody.innerHTML = `<tr><td colspan="11" style="text-align: center; color: #64748b; padding: 32px;">No se encontraron hallazgos ni propuestas con los filtros aplicados.</td></tr>`;
        return;
    }

    tbody.innerHTML = items.map(item => {
        const typePill = item.type === "Hallazgo"
            ? `<span class="pill pill-hallazgo">Hallazgo</span>`
            : `<span class="pill pill-propuesta">Propuesta</span>`;

        const riskPill = item.severity === "Alto"
            ? `<span class="pill pill-alto">Alto</span>`
            : item.severity === "Medio"
            ? `<span class="pill pill-medio">Medio</span>`
            : `<span class="pill pill-bajo">Bajo</span>`;

        const statusSlug = (item.status || "Pendiente").toLowerCase().replace(/\s+/g, "-");
        const statusPill = `<span class="pill pill-${statusSlug}">${escapeHtml(item.status)}</span>`;

        // File icon check
        const filename = item.source_filename || "Documento.xlsx";
        const fileExt = filename.includes(".") ? filename.split(".").pop().toLowerCase() : "xlsx";
        let fileIcon = "📊"; // excel default
        if (fileExt === "pdf") fileIcon = "📄";
        if (fileExt === "docx") fileIcon = "📝";

        return `
            <tr>
                <td class="id-cell">${escapeHtml(item.code)}</td>
                <td>${typePill}</td>
                <td><strong>${escapeHtml(item.title)}</strong></td>
                <td>${escapeHtml(item.responsible_area)}</td>
                <td>${escapeHtml(item.report_title)}</td>
                <td>
                    <div class="file-cell">
                        <span class="file-icon">${fileIcon}</span>
                        <span>${escapeHtml(filename)}</span>
                    </div>
                </td>
                <td>${riskPill}</td>
                <td>${statusPill}</td>
                <td>${escapeHtml(item.action_owner)}</td>
                <td>${escapeHtml(item.target_date || "30/09/2026")}</td>
                <td style="text-align: center;">
                    <button type="button" class="btn-link" style="font-size: 16px; text-decoration: none;" onclick="openEditModal('${item.id}')">•••</button>
                </td>
            </tr>
        `;
    }).join("");
}

function updateSidebarMetrics(items) {
    const openCountEl = el("sidebarOpenCount");
    const progressBarEl = el("sidebarProgressBar");
    const pctEl = el("sidebarPct");

    const total = items.length;
    if (total === 0) return;

    const openCount = items.filter(i => (i.status || "").toLowerCase() !== "completada").length;
    const inProcessCount = items.filter(i => ["en proceso", "planificada", "completada"].includes((i.status || "").toLowerCase())).length;
    const pct = Math.round((inProcessCount / total) * 100);

    if (openCountEl) openCountEl.textContent = openCount;
    if (progressBarEl) progressBarEl.style.width = `${pct}%`;
    if (pctEl) pctEl.textContent = `${pct}%`;
}

// ============================================================
// MODALS FOR EDITING / CREATING
// ============================================================

function openNewFindingModal(defaultType = "Hallazgo") {
    if (el("modalTitle")) el("modalTitle").textContent = `Nuevo ${defaultType}`;
    if (el("modalItemId")) el("modalItemId").value = "";
    if (el("modalType")) el("modalType").value = defaultType;
    if (el("modalRisk")) el("modalRisk").value = "Alto";
    if (el("modalItemTitle")) el("modalItemTitle").value = "";
    if (el("modalArea")) el("modalArea").value = "Contabilidad";
    if (el("modalReportTitle")) el("modalReportTitle").value = "Informe Auditoría 2026";
    if (el("modalStatus")) el("modalStatus").value = "Pendiente";
    if (el("modalOwner")) el("modalOwner").value = "Luciana Gamarra";
    if (el("modalTargetDate")) el("modalTargetDate").value = new Date().toISOString().slice(0, 10);
    if (el("modalSituation")) el("modalSituation").value = "";
    if (el("modalProposal")) el("modalProposal").value = "";

    if (el("itemModal")) el("itemModal").style.display = "flex";
}

function openEditModal(itemId) {
    const item = currentItems.find(i => i.id === itemId);
    if (!item) return;

    if (el("modalTitle")) el("modalTitle").textContent = `Editar ${item.code}`;
    if (el("modalItemId")) el("modalItemId").value = item.id;
    if (el("modalType")) el("modalType").value = item.type || "Hallazgo";
    if (el("modalRisk")) el("modalRisk").value = item.severity || "Alto";
    if (el("modalItemTitle")) el("modalItemTitle").value = item.title || "";
    if (el("modalArea")) el("modalArea").value = item.responsible_area || "";
    if (el("modalReportTitle")) el("modalReportTitle").value = item.report_title || "";
    if (el("modalStatus")) el("modalStatus").value = item.status || "Pendiente";
    if (el("modalOwner")) el("modalOwner").value = item.action_owner || "";
    if (el("modalTargetDate")) el("modalTargetDate").value = item.target_date || "";
    if (el("modalSituation")) el("modalSituation").value = item.situation || "";
    if (el("modalProposal")) el("modalProposal").value = item.proposal || "";

    if (el("itemModal")) el("itemModal").style.display = "flex";
}

function closeItemModal() {
    if (el("itemModal")) el("itemModal").style.display = "none";
}

async function saveModalItem() {
    const itemId = el("modalItemId")?.value;
    const type = el("modalType")?.value;
    const severity = el("modalRisk")?.value;
    const title = el("modalItemTitle")?.value;
    const area = el("modalArea")?.value;
    const report_title = el("modalReportTitle")?.value;
    const status = el("modalStatus")?.value;
    const owner = el("modalOwner")?.value;
    const target_date = el("modalTargetDate")?.value;
    const situation = el("modalSituation")?.value;
    const proposal = el("modalProposal")?.value;

    if (!title) {
        showToast("Por favor ingresá un título ejecutivo.", "warning");
        return;
    }

    if (itemId) {
        // Actualizar estado existente
        try {
            const res = await fetch(`/findings/${itemId}/status`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ status, notes: situation, target_date })
            });
            if (res.ok) {
                showToast("Registro actualizado correctamente.", "success");
                closeItemModal();
                loadAuditTrackData();
            } else {
                showToast("Error al guardar cambios.", "error");
            }
        } catch (err) {
            console.error(err);
            showToast("Error de conexión.", "error");
        }
    } else {
        showToast("Nuevo registro guardado.", "success");
        closeItemModal();
        loadAuditTrackData();
    }
}

// ============================================================
// REPORT UPLOAD & INFORMES TAB
// ============================================================

async function uploadAuditReport(file) {
    if (!file) return;
    showToast(`Analizando e ingestado '${file.name}'...`, "info");

    const form = new FormData();
    form.append("file", file);

    try {
        const response = await fetch("/upload-report", { method: "POST", body: form });
        let data = {};
        try { data = await response.json(); } catch (_) {}

        if (!response.ok) throw new Error(data.error || "No se pudo procesar el informe.");

        showToast(data.message || "Informe ingresado correctamente.", "success");
        loadAuditTrackData();
        switchTab("hallazgos");
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
    const container = el("reportsListContainer");
    if (!container) return;
    if (!reports.length) {
        container.innerHTML = `<div style="text-align: center; color: #64748b; padding: 24px;">No hay informes activos cargados.</div>`;
        return;
    }

    container.innerHTML = reports.map(r => `
        <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 12px 16px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center;">
            <div>
                <strong style="font-size: 14px; color: #0F172A;">${escapeHtml(r.title)}</strong>
                <div style="font-size: 12px; color: #64748B; margin-top: 2px;">
                    Proceso: <strong>${escapeHtml(r.process)}</strong> · Área: <strong>${escapeHtml(r.area)}</strong> · Archivo: <strong>${escapeHtml(r.source_filename)}</strong>
                </div>
            </div>
            <button class="btn btn-outlined" style="padding: 4px 10px; font-size: 11px;" onclick="deleteReportItem('${r.id}')">Eliminar</button>
        </div>
    `).join("");
}

async function deleteReportItem(reportId) {
    if (!confirm("¿Eliminar este informe y todas sus recomendaciones asociadas?")) return;
    try {
        const response = await fetch(`/reports/${reportId}`, { method: "DELETE" });
        if (response.ok) {
            showToast("Informe eliminado.", "success");
            loadAuditTrackData();
            loadReports();
        }
    } catch (err) {
        console.error(err);
    }
}

// ============================================================
// DASHBOARD EXECUTIVE KPIS
// ============================================================

async function loadDashboardKPIs() {
    try {
        const response = await fetch("/dashboard-stats");
        if (!response.ok) return;
        const stats = await response.json();

        if (el("kpiTotal")) el("kpiTotal").textContent = stats.total_findings || 0;
        if (el("kpiOpen")) el("kpiOpen").textContent = stats.pending_findings || 0;
        if (el("kpiProcess")) el("kpiProcess").textContent = stats.in_progress_findings || 0;
        if (el("kpiCompleted")) el("kpiCompleted").textContent = stats.closed_findings || 0;

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
        container.innerHTML = `<div style="text-align: center; color: #64748b; padding: 20px;">No hay datos de áreas registrados.</div>`;
        return;
    }

    container.innerHTML = entries.map(([area, data]) => {
        const pct = data.total > 0 ? Math.round((data.closed / data.total) * 100) : 0;
        return `
            <div style="margin-bottom: 14px;">
                <div style="display: flex; justify-content: space-between; font-size: 12px; font-weight: 600; margin-bottom: 4px;">
                    <span>${escapeHtml(area)}</span>
                    <span>${data.closed} de ${data.total} resueltos (${pct}%)</span>
                </div>
                <div style="background: #E2E8F0; border-radius: 6px; height: 8px; overflow: hidden;">
                    <div style="background: var(--primary-blue); width: ${pct}%; height: 100%;"></div>
                </div>
            </div>
        `;
    }).join("");
}

// ============================================================
// EXCEL EXPORT
// ============================================================

async function exportExcelReport() {
    showToast("Generando reporte Excel...", "info");
    try {
        const response = await fetch("/export-excel", { method: "POST" });
        if (!response.ok) throw new Error("No se pudo generar el archivo Excel.");
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `Seguimiento_Auditoria_${new Date().toISOString().slice(0, 10)}.xlsx`;
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

// Initialize on page load
document.addEventListener("DOMContentLoaded", () => {
    loadAuditTrackData();
});
