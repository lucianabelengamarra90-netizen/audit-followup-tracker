// ============================================================
// AUDITTRACK - FRONTEND CONTROLLER (ESTRUCTURA RELACIONAL INTEGRADA)
// ============================================================

// Fetch interceptor for 401 Unauthorized handling
const originalFetch = window.fetch;
window.fetch = async function(...args) {
    const response = await originalFetch(...args);
    if (response.status === 401) {
        const urlStr = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url ? args[0].url : '');
        if (!urlStr.includes('/login') && !urlStr.includes('/api/user')) {
            showLoginModal();
        }
    }
    return response;
};

function showLoginModal() {
    const modal = el("loginModal");
    if (modal) modal.style.display = "flex";
}

function closeLoginModal() {
    const modal = el("loginModal");
    if (modal) modal.style.display = "none";
}

async function submitLoginModal() {
    const user = el("loginUsernameInput")?.value || "";
    const pwd = el("loginPasswordInput")?.value || "";
    const errBox = el("loginErrorMessage");
    if (errBox) errBox.style.display = "none";

    try {
        const res = await originalFetch("/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: user, password: pwd })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            closeLoginModal();
            showToast(`Sesión iniciada como ${data.user.name}`, "success");
            await initUserSession();
            await loadAllData();
        } else {
            if (errBox) {
                errBox.textContent = data.error || "Credenciales inválidas";
                errBox.style.display = "block";
            }
        }
    } catch (e) {
        if (errBox) {
            errBox.textContent = "Error de conexión al iniciar sesión";
            errBox.style.display = "block";
        }
    }
}

let currentFindings = [];
let currentProposals = [];
let currentActionPlans = [];
let currentReports = [];
let currentDashboardStats = null;
let currentKpiIndicators = [];

let activeFilters = {
    report: "",
    area: "",
    status: "",
    risk: "",
    search: "",
    overdue: false,
    no_plan: false
};

let chartInstances = {};
let selectedReportDetail = null;

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

function isDateOverdue(dateStr) {
    if (!dateStr) return false;
    let d = null;
    const str = dateStr.toString().trim();
    if (!str) return false;

    // ISO format YYYY-MM-DD
    if (/^\d{4}-\d{2}-\d{2}/.test(str)) {
        const parts = str.slice(0, 10).split("-");
        d = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
    } 
    // Latam format DD/MM/YYYY
    else if (/^\d{1,2}\/\d{1,2}\/\d{4}/.test(str)) {
        const parts = str.slice(0, 10).split("/");
        d = new Date(parseInt(parts[2], 10), parseInt(parts[1], 10) - 1, parseInt(parts[0], 10));
    }

    if (!d || isNaN(d.getTime())) return false;

    const today = new Date();
    today.setHours(0, 0, 0, 0);

    return d < today;
}

function getRowEffectiveStatus(item, prop) {
    let raw = (prop && prop.status) ? prop.status : (item && item.status ? item.status : "En proceso");
    if (!raw) raw = "En proceso";
    const clean = raw.toString().trim().toLowerCase();

    // 1. Si estado = Finalizado -> estado efectivo = Finalizado
    if (["finalizado", "finalizada", "completada", "completado", "cerrado", "cerrada", "implementado", "implementada", "archivada"].includes(clean)) {
        return "Finalizado";
    }

    // 2. Si estado = En suspensión -> estado efectivo = En suspensión
    if (clean === "en suspensión" || clean === "en suspension" || clean === "stand-by") {
        return "En suspensión";
    }

    // 3. Si estado = Pendiente de validación -> estado efectivo = Pendiente de validación
    if (clean.includes("pendiente de validación") || clean.includes("pendiente de validacion") || clean === "pendiente validacion") {
        return "Pendiente de validación";
    }

    // 4, 5. Si estado = En proceso y hay fecha compromiso
    const targetDateStr = (prop && prop.target_date) || (item && item.target_date) || "";
    if (targetDateStr && isDateOverdue(targetDateStr)) {
        return "Vencido";
    }

    return "En proceso";
}

function isStatusEqual(s1, s2) {
    if (!s1 || !s2) return false;
    const clean1 = s1.toString().trim().toLowerCase();
    const clean2 = s2.toString().trim().toLowerCase();
    if (clean1 === clean2) return true;

    const isFinished1 = ["finalizado", "finalizada", "completada", "completado", "cerrado", "cerrada", "implementado", "implementada", "archivada"].includes(clean1);
    const isFinished2 = ["finalizado", "finalizada", "completada", "completado", "cerrado", "cerrada", "implementado", "implementada", "archivada"].includes(clean2);
    if (isFinished1 && isFinished2) return true;

    const isSuspended1 = ["en suspensión", "en suspension", "stand-by"].includes(clean1);
    const isSuspended2 = ["en suspensión", "en suspension", "stand-by"].includes(clean2);
    if (isSuspended1 && isSuspended2) return true;

    const isOverdue1 = ["vencido", "vencida", "overdue"].includes(clean1);
    const isOverdue2 = ["vencido", "vencida", "overdue"].includes(clean2);
    if (isOverdue1 && isOverdue2) return true;

    return false;
}

function toggleExcelMenu(event) {
    if (event) event.stopPropagation();
    const dropdown = el("excelMenuDropdown");
    if (!dropdown) return;
    const isVisible = dropdown.style.display === "block";
    dropdown.style.display = isVisible ? "none" : "block";
}

document.addEventListener("click", function(event) {
    const dropdown = el("excelMenuDropdown");
    if (dropdown && dropdown.style.display === "block") {
        if (!dropdown.contains(event.target) && !event.target.closest('button[onclick*="toggleExcelMenu"]')) {
            dropdown.style.display = "none";
        }
    }
});

async function downloadExcelTemplate() {
    try {
        const dropdown = el("excelMenuDropdown");
        if (dropdown) dropdown.style.display = "none";

        showToast("Descargando plantilla modelo...", "info");
        const response = await fetch("/download-template");
        if (!response.ok) {
            if (response.status === 401) {
                showLoginModal();
                showToast("Sesión requerida para descargar la plantilla.", "warning");
                return;
            }
            const contentType = response.headers.get("content-type") || "";
            if (contentType.includes("application/json")) {
                const errData = await response.json();
                showToast(errData.error || "Error al descargar plantilla modelo", "error");
            } else {
                showToast(`Error servidor (${response.status}) al descargar plantilla`, "error");
            }
            return;
        }
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.style.display = "none";
        a.href = url;
        a.download = "Plantilla_Importacion_AuditTrack.xlsx";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => window.URL.revokeObjectURL(url), 1000);
        showToast("Plantilla modelo descargada", "success");
    } catch (e) {
        console.error("Error al descargar plantilla:", e);
        showToast("Error de conexión al descargar plantilla", "error");
    }
}

async function importExcelFile(file, selectedMode = null, targetReportId = null) {
    if (!file) return;
    const dropdown = el("excelMenuDropdown");
    if (dropdown) dropdown.style.display = "none";

    try {
        showToast("Procesando e importando planilla...", "info");
        const formData = new FormData();
        formData.append("file", file);
        if (selectedMode) formData.append("mode", selectedMode);
        if (targetReportId) formData.append("target_report_id", targetReportId);

        const response = await fetch("/import-excel", {
            method: "POST",
            body: formData
        });

        const data = await response.json();
        const input = el("importExcelInput");

        if (response.status === 409 && data.requires_decision) {
            const cand = (data.analysis && data.analysis.candidates && data.analysis.candidates[0]) || {};
            const title = cand.title || "Informe existente";
            const code = cand.code || "";
            const choice = prompt(
                `El archivo contiene datos que ya existen en AuditTrack (${code} · ${title}).\n\n` +
                `Escribí el número de la opción deseada:\n` +
                `1. Actualizar conservando ediciones manuales (Recomendado)\n` +
                `2. Sobrescribir todos los datos\n` +
                `3. Crear un nuevo informe separado`,
                "1"
            );
            if (input) input.value = "";
            if (!choice) return;
            const modeMap = { "1": "merge", "2": "overwrite", "3": "new" };
            const mode = modeMap[choice.trim()] || "merge";
            return importExcelFile(file, mode, cand.report_id);
        }

        if (input) input.value = "";

        if (!response.ok || data.error) {
            showToast(data.error || "Error al importar el archivo", "error");
            return;
        }

        if (data.errors && data.errors.length > 0) {
            alert("Atención: Ocurrieron errores o incompatibilidades en filas de la planilla:\n\n" + data.errors.join("\n"));
        }

        const msg = data.message || "Planilla importada exitosamente";
        showToast(msg, "success");
        await loadAllData();
        await loadExecutiveDashboard();
    } catch (e) {
        console.error("Error al importar planilla:", e);
        showToast("Error de conexión al importar planilla", "error");
        const input = el("importExcelInput");
        if (input) input.value = "";
    }
}


async function exportExcelReport() {
    try {
        const dropdown = el("excelMenuDropdown");
        if (dropdown) dropdown.style.display = "none";

        showToast("Generando reporte Excel...", "info");
        const response = await fetch("/export-excel", {
            method: "POST"
        });
        if (!response.ok) {
            if (response.status === 401) {
                showLoginModal();
                showToast("Sesión requerida para exportar el reporte.", "warning");
                return;
            }
            const contentType = response.headers.get("content-type") || "";
            if (contentType.includes("application/json")) {
                const errData = await response.json();
                showToast(errData.error || "Error al exportar reporte Excel", "error");
            } else {
                showToast(`Error servidor (${response.status}) al exportar reporte Excel`, "error");
            }
            return;
        }
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.style.display = "none";
        a.href = url;
        a.download = `Reporte_AuditTrack_${new Date().toISOString().slice(0,10)}.xlsx`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => window.URL.revokeObjectURL(url), 1000);
        showToast("Excel exportado exitosamente", "success");
    } catch (e) {
        console.error("Error al exportar Excel:", e);
        showToast("Error de conexión al exportar Excel", "error");
    }
}

// ============================================================
// TAB NAVIGATION
// ============================================================

let isSyncing = false;

async function syncDataWithServer(silent = true) {
    if (isSyncing) return;
    isSyncing = true;
    const badge = el("syncStatusBadge");
    if (badge && !silent) {
        badge.innerHTML = '🟡 Sincronizando...';
        badge.style.color = '#F59E0B';
    }

    try {
        await loadAllData(silent);
        if (badge) {
            const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            badge.innerHTML = `🟢 Sincronizado (${timeStr})`;
            badge.style.color = '#10B981';
        }
        if (!silent) {
            showToast("Datos sincronizados en vivo con Supabase", "success");
        }
    } catch (e) {
        console.error("Error en sincronización:", e);
        if (badge) {
            badge.innerHTML = '🔴 Error de sync';
            badge.style.color = '#EF4444';
        }
    } finally {
        isSyncing = false;
    }
}

function switchTab(tabName) {
    if (tabName === "tableros" || tabName === "indicadores") {
        tabName = "tablero-ejecutivo";
    }

    document.querySelectorAll(".topnav-tab").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.tab === tabName);
    });

    document.querySelectorAll(".tab-pane").forEach(pane => {
        pane.classList.toggle("active", pane.id === `tab-${tabName}`);
    });

    if (tabName === "informes") loadReports();
    if (tabName === "hallazgos") filterAndRenderAll();
    if (tabName === "propuestas") renderProposalsTab();
    if (tabName === "planes") renderActionPlansTab();
    if (tabName === "tablero-ejecutivo") loadExecutiveDashboard();

    // Trigger silent sync when switching tabs
    syncDataWithServer(true);
}

// ============================================================
// DATA LOADING
// ============================================================

let globalDataSeq = 0;
let execDashboardSeq = 0;

async function loadAllData(silent = false) {
    const seq = ++globalDataSeq;
    try {
        const [resF, resP, resPA, resR] = await Promise.all([
            fetch("/findings"),
            fetch("/proposals"),
            fetch("/action-plans"),
            fetch("/reports")
        ]);

        if (seq !== globalDataSeq) return;

        if (!resF.ok || !resP.ok || !resPA.ok || !resR.ok) {
            throw new Error(`Error al consultar servidor: F:${resF.status} P:${resP.status} PA:${resPA.status} R:${resR.status}`);
        }

        const dataF = await resF.json();
        const dataP = await resP.json();
        const dataPA = await resPA.json();
        const dataR = await resR.json();

        if (seq !== globalDataSeq) return;

        // Never erase an already displayed dataset because of malformed or transient responses.
        if (![dataF.findings, dataP.proposals, dataPA.action_plans, dataR.reports].every(Array.isArray)) {
            throw new Error("La sincronización devolvió datos incompletos; se conserva la última vista válida.");
        }
        if (currentFindings.length > 0 && dataF.findings.length === 0) {
            throw new Error("El servidor devolvió inesperadamente cero hallazgos; se conserva la última vista. Recargá para confirmar una eliminación intencional.");
        }
        currentFindings = dataF.findings;
        currentProposals = dataP.proposals;
        currentActionPlans = dataPA.action_plans;
        currentReports = dataR.reports;

        populateFilterDropdowns();
        populateExecutiveFilterDropdowns();

        const activeDrawerOpen = el("findingDrawer") && el("findingDrawer").classList.contains("open");

        if (!activeDrawerOpen) {
            filterAndRenderAll();
        }
        renderProposalsTab();
        renderActionPlansTab();
        updateSidebarMetrics();
        loadNotifications();

        // Always refresh executive dashboard KPIs & stats
        await loadExecutiveDashboard();

        // If drilldown modal is open, refresh drilldown records
        const modal = el("execDrilldownModal");
        if (modal && modal.style.display === "flex" && currentExecutiveDrilldownMetric) {
            openExecutiveDrilldown(currentExecutiveDrilldownMetric, currentExecutiveDrilldownTitle);
        }
    } catch (err) {
        console.error("Error cargando estructura relacional de AuditTrack:", err);
        throw err;
    }
}

async function loadNotifications() {
    try {
        const res = await fetch("/api/notifications");
        if (!res.ok) return;
        const alertsData = await res.json();

        const badgeEl = el("bellBadgeCount");
        if (badgeEl) badgeEl.textContent = alertsData.total_count || 0;

        renderNotificationDropdownBody(alertsData);
    } catch (err) {
        console.error("Error cargando notificaciones:", err);
    }
}

function toggleNotificationDropdown() {
    const dropdown = el("notificationDropdown");
    if (dropdown) {
        dropdown.style.display = dropdown.style.display === "none" ? "block" : "none";
    }
}

function renderNotificationDropdownBody(data) {
    const body = el("notificationDropdownBody");
    if (!body) return;

    if (!data.total_count) {
        body.innerHTML = `<div style="text-align: center; color: #16A34A; padding: 20px; font-size: 12px;"><strong>¡Sin alertas pendientes!</strong><p style="color:#64748B; margin-top:2px;">Todos los registros se encuentran al día.</p></div>`;
        return;
    }

    let html = "";

    const renderAuditorBadge = (auditorName) => `
        <span style="font-size:11px; background:#EFF6FF; color:#0055D4; padding:2px 6px; border-radius:4px; font-weight:600; float:right;">
            👤 ${escapeHtml(auditorName || 'Auditoría Interna')}
        </span>
    `;

    if (data.overdue && data.overdue.length > 0) {
        html += `<div class="notification-category">🔴 Vencidas (${data.overdue.length})</div>`;
        data.overdue.forEach(item => {
            html += `
                <div class="notification-item" onclick="onNotificationClick('${item.finding_id}')">
                    ${renderAuditorBadge(item.auditor)}
                    <strong>${escapeHtml(item.code)}</strong>
                    <p style="color:#DC2626; font-weight:600; margin-top:2px;">${escapeHtml(item.message)}</p>
                    <p>${escapeHtml(item.title)}</p>
                </div>
            `;
        });
    }

    if (data.due_today && data.due_today.length > 0) {
        html += `<div class="notification-category">🟠 Vence hoy (${data.due_today.length})</div>`;
        data.due_today.forEach(item => {
            html += `
                <div class="notification-item" onclick="onNotificationClick('${item.finding_id}')">
                    ${renderAuditorBadge(item.auditor)}
                    <strong>${escapeHtml(item.code)}</strong>
                    <p style="color:#D97706; font-weight:600; margin-top:2px;">${escapeHtml(item.message)}</p>
                    <p>${escapeHtml(item.title)}</p>
                </div>
            `;
        });
    }

    if (data.due_soon && data.due_soon.length > 0) {
        html += `<div class="notification-category">🟡 Próximas a vencer (${data.due_soon.length})</div>`;
        data.due_soon.forEach(item => {
            html += `
                <div class="notification-item" onclick="onNotificationClick('${item.finding_id}')">
                    ${renderAuditorBadge(item.auditor)}
                    <strong>${escapeHtml(item.code)}</strong>
                    <p style="color:#B45309; font-weight:600; margin-top:2px;">${escapeHtml(item.message)}</p>
                    <p>${escapeHtml(item.title)}</p>
                </div>
            `;
        });
    }

    if (data.attention && data.attention.length > 0) {
        html += `<div class="notification-category">⚠ Requieren atención (${data.attention.length})</div>`;
        data.attention.forEach(item => {
            html += `
                <div class="notification-item" onclick="onNotificationClick('${item.finding_id}')">
                    ${renderAuditorBadge(item.auditor)}
                    <strong>${escapeHtml(item.code)}</strong>
                    <p style="color:#0055D4; font-weight:600; margin-top:2px;">${escapeHtml(item.message)}</p>
                    <p>${escapeHtml(item.title)}</p>
                </div>
            `;
        });
    }

    body.innerHTML = html;
}


function onNotificationClick(findingId) {
    toggleNotificationDropdown();
    if (findingId) openFindingDrawer(findingId);
}

function markAllNotificationsRead() {
    const badgeEl = el("bellBadgeCount");
    if (badgeEl) badgeEl.textContent = 0;
    showToast("Notificaciones marcadas como leídas.", "info");
    toggleNotificationDropdown();
}

function populateFilterDropdowns() {
    const reportSelect = el("filterReportSelect");
    const areaSelect = el("filterAreaSelect");

    if (reportSelect) {
        const reports = Array.from(new Set(currentFindings.map(i => i.report_title).filter(Boolean))).sort();
        const cur = reportSelect.value;
        reportSelect.innerHTML = `<option value="">Todos los informes</option>` +
            reports.map(r => `<option value="${escapeHtml(r)}"${r === cur ? " selected" : ""}>${escapeHtml(r)}</option>`).join("");
    }

    if (areaSelect) {
        const areas = Array.from(new Set(currentFindings.map(i => i.responsible_area).filter(Boolean))).sort();
        const cur = areaSelect.value;
        areaSelect.innerHTML = `<option value="">Todas las áreas</option>` +
            areas.map(a => `<option value="${escapeHtml(a)}"${a === cur ? " selected" : ""}>${escapeHtml(a)}</option>`).join("");
    }
}

function applyFilters() {
    activeFilters.report = el("filterReportSelect")?.value || "";
    activeFilters.area = el("filterAreaSelect")?.value || "";
    activeFilters.status = el("filterStatusSelect")?.value || "";
    activeFilters.risk = el("filterRiskSelect")?.value || "";
    filterAndRenderAll();
}

function onGlobalSearch(query) {
    activeFilters.search = (query || "").trim();
    filterAndRenderAll();
}

function clearFilters() {
    if (el("filterReportSelect")) el("filterReportSelect").value = "";
    if (el("filterAreaSelect")) el("filterAreaSelect").value = "";
    if (el("filterStatusSelect")) el("filterStatusSelect").value = "";
    if (el("filterRiskSelect")) el("filterRiskSelect").value = "";
    if (el("globalSearchInput")) el("globalSearchInput").value = "";

    activeFilters = { report: "", area: "", status: "", risk: "", search: "", overdue: false, no_plan: false };
    filterAndRenderAll();
}

function toggleFilterBar() {
    const bar = el("filterBarContainer");
    if (bar) bar.style.display = bar.style.display === "none" ? "flex" : "none";
}

function filterAndRenderAll() {
    let filteredF = [...currentFindings];

    if (activeFilters.report) {
        filteredF = filteredF.filter(i => (i.report_title || "").toLowerCase() === activeFilters.report.toLowerCase());
    }
    if (activeFilters.area) {
        filteredF = filteredF.filter(i => (i.responsible_area || "").toLowerCase() === activeFilters.area.toLowerCase());
    }

    if (activeFilters.risk) {
        filteredF = filteredF.filter(i => (i.severity || "").toLowerCase() === activeFilters.risk.toLowerCase());
    }
    if (activeFilters.search) {
        const q = activeFilters.search.toLowerCase();
        filteredF = filteredF.filter(i =>
            (i.code || "").toLowerCase().includes(q) ||
            (i.title || "").toLowerCase().includes(q) ||
            (i.situation || "").toLowerCase().includes(q) ||
            (i.responsible_area || "").toLowerCase().includes(q) ||
            (i.report_title || "").toLowerCase().includes(q) ||
            (i.action_owner || "").toLowerCase().includes(q)
        );
    }
    if (activeFilters.no_plan) {
        filteredF = filteredF.filter(i => (i.action_plans_count || 0) === 0 && !isFinalized(i));
    }

    renderAuditTrackTable(filteredF);
    renderProposalsTab();
    renderActionPlansTab();
}

// ============================================================
// 1. MAIN TABLE (ÁREA PRIMERO + TRAZABILIDAD DESPLEGABLE CON FLECHA)
// ============================================================

function renderAuditTrackTable(items) {
    const tbody = el("auditTrackTableBody");
    const countSpan = el("showingRecordsCount");
    if (!tbody) return;

    let html = "";
    let renderedRowCount = 0;

    items.forEach((item) => {
        const filename = item.source_filename || "Informe.xlsx";
        const proposalsToRender = (item.proposals && item.proposals.length > 0) ? item.proposals : [null];

        proposalsToRender.forEach((prop) => {
            const status = getRowEffectiveStatus(item, prop);

            // Row-level status filter: Skip rendering if row effective status does not match activeFilters.status
            if (activeFilters.status && !isStatusEqual(activeFilters.status, status)) {
                return;
            }

            renderedRowCount++;

            const propCodeCell = prop
                ? `<a href="#" class="id-cell" style="color: #16A34A; font-weight:700;" onclick="openFindingDrawer('${item.id}'); return false;">${escapeHtml(prop.code)}</a>`
                : `<span style="color:#94A3B8; font-size:11px;">Sin propuesta</span>`;

            const propText = prop ? (prop.proposal_text || prop.title) : "";
            let firstAction = (prop && prop.action_plans && prop.action_plans.length > 0) ? prop.action_plans[0] : null;
            let owner = (prop && prop.action_owner) ? prop.action_owner : (firstAction ? firstAction.action_owner : (item.action_owner || "Sin asignar"));
            let targetDate = (prop && prop.target_date) ? prop.target_date : (firstAction ? firstAction.target_date : "");
            let pct = firstAction ? (firstAction.progress_pct || 0) : 0;
            let observations = item.observations || "";

            const currentRisk = item.severity || "Medio";
            const badgeClass = currentRisk === "Alto" ? "risk-badge-alto" : (currentRisk === "Bajo" ? "risk-badge-bajo" : "risk-badge-medio");

            const riskSelectHtml = `<select class="inline-select inline-select-risk ${badgeClass}" data-finding-id="${item.id}" data-field="severity" onchange="inlineUpdateFindingRisk(this)">
                <option value="Alto" ${currentRisk === 'Alto' ? 'selected' : ''}>Alto</option>
                <option value="Medio" ${currentRisk === 'Medio' ? 'selected' : ''}>Medio</option>
                <option value="Bajo" ${currentRisk === 'Bajo' ? 'selected' : ''}>Bajo</option>
            </select>`;

            const statusOptions = ["En proceso", "En suspensión", "Finalizado"];
            const statusSelectHtml = `<select class="inline-select inline-select-status" data-finding-id="${item.id}" data-proposal-id="${prop ? prop.id : ''}" data-field="status" onchange="inlineUpdateStatus(this)">
                ${statusOptions.map(s => `<option value="${s}" ${isStatusEqual(s, status) ? 'selected' : ''}>${s}</option>`).join('')}
            </select>`;

            const propTextCell = prop
                ? `<div class="truncate-2-lines" style="color:#16A34A; font-weight:500;" title="${escapeHtml(propText)}">💡 ${escapeHtml(propText)}</div>`
                : `<span style="color:#94A3B8; font-size:11px;">Sin propuesta</span>`;

            html += `
                <tr data-row-id="${item.id}">
                    <td style="font-weight: 700; color: #1E293B;">${escapeHtml(item.responsible_area || 'Pendiente de definir')}</td>
                    <td>
                        <a href="#" class="id-cell" style="color: #0055D4; font-weight:700;" onclick="openFindingDrawer('${item.id}'); return false;">${escapeHtml(item.code)}</a>
                    </td>
                    <td>
                        <strong style="color: #0F172A; cursor:pointer;" onclick="openFindingDrawer('${item.id}')">${escapeHtml(item.title)}</strong>
                        <div class="truncate-2-lines" style="font-size: 11px; color: #64748B; margin-top: 3px;" title="${escapeHtml(item.situation)}">${escapeHtml(item.situation)}</div>
                    </td>
                    <td>${propCodeCell}</td>
                    <td>${propTextCell}</td>
                    <td>${riskSelectHtml}</td>
                    <td>
                        <input type="text" class="inline-input" value="${escapeHtml(owner)}"
                            data-finding-id="${item.id}" data-proposal-id="${prop ? prop.id : ''}" data-field="action_owner"
                            onchange="inlineUpdateOwner(this)" placeholder="Responsable" />
                    </td>
                    <td>
                        <input type="date" class="inline-input" value="${escapeHtml(targetDate)}"
                            data-finding-id="${item.id}" data-proposal-id="${prop ? prop.id : ''}" data-field="target_date"
                            onchange="inlineUpdateDate(this)" />
                    </td>
                    <td>${statusSelectHtml}</td>
                    <td>
                        <div style="display:flex; align-items:center; gap:4px;">
                            <div style="background:#CBD5E1; border-radius:4px; height:6px; flex:1; overflow:hidden;">
                                <div style="background:#16A34A; width:${pct}%; height:100%;"></div>
                            </div>
                            <span style="font-size:10px;">${pct}%</span>
                        </div>
                    </td>
                    <td>
                        <button class="btn btn-outlined" style="padding: 3px 8px; font-size: 11px;" onclick="openFindingDrawer('${item.id}')" title="Ver / Editar detalle">✏️ Editar</button>
                    </td>
                    <td>
                        <input type="text" class="inline-input inline-input-obs" value="${escapeHtml(observations)}"
                            data-finding-id="${item.id}" data-field="observations"
                            onchange="inlineUpdateFinding(this)" placeholder="Agregar observación..." />
                    </td>
                </tr>
            `;
        });
    });

    if (countSpan) {
        const filtersActive = Object.values(activeFilters).some(Boolean);
        countSpan.textContent = `Mostrando ${renderedRowCount} filas de ${currentFindings.length} hallazgos guardados` + (filtersActive ? " (vista filtrada)" : "");
    }

    if (!renderedRowCount) {
        const filtersActive = Object.values(activeFilters).some(Boolean);
        tbody.innerHTML = filtersActive && currentFindings.length
            ? `<tr><td colspan="13" style="text-align:center; padding:36px;">Hay ${currentFindings.length} hallazgos guardados, pero ninguno coincide con los filtros activos. <button type="button" class="btn btn-outlined" onclick="clearFilters()">Mostrar todos los hallazgos</button></td></tr>`
            : `<tr><td colspan="13" style="text-align:center; color:#64748b; padding:36px;">No hay hallazgos disponibles.</td></tr>`;
        return;
    }

    tbody.innerHTML = html;
}


// ============================================================
// INLINE EDITING FUNCTIONS
// ============================================================

async function inlineUpdateFindingRisk(el) {
    const findingId = el.dataset.findingId;
    const value = el.value;
    const badgeClass = value === "Alto" ? "risk-badge-alto" : (value === "Bajo" ? "risk-badge-bajo" : "risk-badge-medio");

    document.querySelectorAll(`select[data-finding-id="${findingId}"][data-field="severity"]`).forEach(s => {
        s.value = value;
        s.className = `inline-select inline-select-risk ${badgeClass}`;
    });

    try {
        const resp = await fetch(`/findings/${findingId}/update`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ severity: value })
        });
        if (resp.ok) {
            showToast(`Riesgo actualizado a "${value}"`, "success");
            await loadAllData();
        } else {
            showToast("Error al actualizar riesgo en el servidor", "error");
            await loadAllData();
        }
    } catch (e) {
        showToast("Error de conexión al actualizar riesgo", "error");
        await loadAllData();
    }
}

async function inlineUpdateFinding(el) {
    const findingId = el.dataset.findingId;
    const field = el.dataset.field;
    const value = el.value;
    try {
        const resp = await fetch(`/findings/${findingId}/update`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ [field]: value })
        });
        if (resp.ok) {
            showToast("Registro actualizado", "success");
            await loadAllData();
        } else {
            showToast("Error al actualizar registro en el servidor", "error");
            await loadAllData();
        }
    } catch (e) {
        showToast("Error de conexión al actualizar", "error");
        await loadAllData();
    }
}

async function inlineUpdateStatus(el) {
    const findingId = el.dataset.findingId;
    const proposalId = el.dataset.proposalId;
    const value = el.value;

    const dbValue = (value === "Vencido") ? "En proceso" : value;

    try {
        const res1 = await fetch(`/findings/${findingId}/update`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: dbValue })
        });
        let res2Ok = true;
        if (proposalId) {
            const res2 = await fetch(`/proposals/${proposalId}/update`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ status: dbValue })
            });
            res2Ok = res2.ok;
        }

        if (res1.ok && res2Ok) {
            showToast(`Estado actualizado a "${dbValue}"`, "success");
        } else {
            showToast("Error al actualizar estado en el servidor", "error");
        }
        await loadAllData();
    } catch (e) {
        showToast("Error de conexión al actualizar estado", "error");
        await loadAllData();
    }
}

async function inlineUpdateOwner(el) {
    const findingId = el.dataset.findingId;
    const proposalId = el.dataset.proposalId;
    const value = el.value;

    try {
        const res1 = await fetch(`/findings/${findingId}/update`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action_owner: value })
        });
        let res2Ok = true;
        if (proposalId) {
            const res2 = await fetch(`/proposals/${proposalId}/update`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action_owner: value })
            });
            res2Ok = res2.ok;
        }

        if (res1.ok && res2Ok) {
            showToast("Responsable actualizado", "success");
        } else {
            showToast("Error al actualizar responsable en el servidor", "error");
        }
        await loadAllData();
    } catch (e) {
        showToast("Error al actualizar responsable", "error");
        await loadAllData();
    }
}

async function inlineUpdateDate(el) {
    const proposalId = el.dataset.proposalId;
    const planId = el.dataset.planId;
    const value = el.value;

    if (!proposalId && !planId) return;

    if (proposalId) {
        const prop = (currentProposals || []).find(p => p.id === proposalId);
        if (prop) prop.target_date = value;
    }
    if (planId) {
        const plan = (currentActionPlans || []).find(pa => pa.id === planId);
        if (plan) plan.target_date = value;
    }

    try {
        let resp = null;
        if (planId) {
            resp = await fetch(`/action-plans/${planId}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ target_date: value })
            });
        } else if (proposalId) {
            resp = await fetch(`/proposals/${proposalId}/update`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ target_date: value })
            });
        }

        if (resp && resp.ok) {
            showToast("Fecha actualizada", "success");
            await loadAllData();
        } else {
            showToast("Error al actualizar fecha en el servidor", "error");
            await loadAllData();
        }
    } catch (e) {
        showToast("Error al actualizar fecha", "error");
        await loadAllData();
    }
}



function toggleTraceRow(rowId, findingId) {
    const btn = document.querySelector(`#${rowId} .expand-btn`);
    const nestedTr = el(`${rowId}-nested`);
    const contentBox = el(`${rowId}-nested-content`);

    if (!nestedTr || !contentBox) return;

    if (nestedTr.style.display === "none") {
        nestedTr.style.display = "table-row";
        if (btn) btn.textContent = "▼";

        const item = currentFindings.find(f => f.id === findingId);
        if (!item) return;

        let propsHtml = "";
        if (item.proposals && item.proposals.length > 0) {
            propsHtml = item.proposals.map(p => {
                let plansHtml = "";
                if (p.action_plans && p.action_plans.length > 0) {
                    plansHtml = p.action_plans.map(pa => `
                        <div style="background:#FFFFFF; border:1px solid #CBD5E1; border-radius:6px; padding:8px 12px; margin-top:6px; font-size:12px;">
                            <strong>📋 Plan ${escapeHtml(pa.code)}:</strong> ${escapeHtml(pa.action_text)}
                            <div style="font-size:11px; color:#64748B; margin-top:2px;">
                                Responsable: <strong>${escapeHtml(pa.action_owner)}</strong> · Fecha: <strong>${escapeHtml(pa.target_date)}</strong> · Avance: <strong>${pa.progress_pct}%</strong> · Estado: <span class="pill pill-${(pa.status||'en-proceso').toLowerCase()}">${escapeHtml(pa.status)}</span>
                            </div>
                        </div>
                    `).join("");
                } else {
                    plansHtml = `<div style="font-size:11px; color:#94A3B8; margin-top:4px;">Sin planes de acción creados aún.</div>`;
                }

                return `
                    <div style="background:#EFF6FF; border:1px solid #BFDBFE; border-radius:8px; padding:10px 14px; margin-bottom:8px;">
                        <strong style="color:#0055D4;">💡 Propuesta ${escapeHtml(p.code)}:</strong> ${escapeHtml(p.proposal_text || p.title)}
                        <div style="margin-top:6px;">${plansHtml}</div>
                    </div>
                `;
            }).join("");
        } else {
            propsHtml = `<div style="font-size:12px; color:#64748B;">No hay propuestas de mejora registradas para este hallazgo.</div>`;
        }

        contentBox.innerHTML = `
            <div style="font-size:12px;">
                <strong style="color:#0F172A; text-transform:uppercase;">Cadena de Trazabilidad e Historial (${escapeHtml(item.code)})</strong>
                <div style="margin-top:8px;">${propsHtml}</div>
            </div>
        `;
    } else {
        nestedTr.style.display = "none";
        if (btn) btn.textContent = "►";
    }
}

// ============================================================
// 2. PROPUESTAS DE MEJORA TAB
// ============================================================

// ============================================================
// 2. PROPUESTAS DE MEJORA TAB & REPOSITORIO PLAN 2026
// ============================================================

let currentProposalViewMode = "all"; // "all" or "repo"
let proposalKpiFilter = "all"; // all, in_process, completed, no_plan
let proposalFilters = {
    report_id: "",
    area: "",
    status: "",
    risk: ""
};

function switchProposalView(mode) {
    currentProposalViewMode = mode;
    proposalKpiFilter = mode === "repo" ? "completed" : "all";
    const btnAll = el("subtabAllProp");
    const btnRepo = el("subtabRepoProp");

    if (mode === "repo") {
        if (btnAll) btnAll.classList.remove("active-subtab");
        if (btnRepo) btnRepo.classList.add("active-subtab");
    } else {
        if (btnRepo) btnRepo.classList.remove("active-subtab");
        if (btnAll) btnAll.classList.add("active-subtab");
    }
    renderProposalsTab();
}

function toggleProposalRepoView(openRepo = true) {
    switchTab('propuestas');
    switchProposalView(openRepo ? 'repo' : 'all');
}

function populateProposalFilterDropdowns() {
    const reportSelect = el("filterPropReportSelect");
    const areaSelect = el("filterPropAreaSelect");

    if (reportSelect && currentReports.length) {
        const currentVal = reportSelect.value;
        reportSelect.innerHTML = `<option value="">Todos los informes</option>` +
            currentReports.map(r => `<option value="${r.id}">${escapeHtml(r.title)} (${r.code})</option>`).join("");
        reportSelect.value = currentVal;
    }

    if (areaSelect && currentProposals.length) {
        const currentVal = areaSelect.value;
        const areas = Array.from(new Set(currentProposals.map(p => p.responsible_area).filter(Boolean)));
        areaSelect.innerHTML = `<option value="">Todas las áreas</option>` +
            areas.map(a => `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`).join("");
        areaSelect.value = currentVal;
    }
}

function selectProposalKpiFilter(kind) {
    // Clicking the same KPI again returns to the complete list.
    proposalKpiFilter = proposalKpiFilter === kind ? "all" : kind;
    switchProposalView("all");
    proposalKpiFilter = kind;
    renderProposalsTab();
}

function applyProposalFilters() {
    proposalFilters.report_id = el("filterPropReportSelect")?.value || "";
    proposalFilters.area = el("filterPropAreaSelect")?.value || "";
    proposalFilters.status = el("filterPropStatusSelect")?.value || "";
    proposalFilters.risk = el("filterPropRiskSelect")?.value || "";
    renderProposalsTab();
}

function clearProposalFilters() {
    if (el("filterPropReportSelect")) el("filterPropReportSelect").value = "";
    if (el("filterPropAreaSelect")) el("filterPropAreaSelect").value = "";
    if (el("filterPropStatusSelect")) el("filterPropStatusSelect").value = "";
    if (el("filterPropRiskSelect")) el("filterPropRiskSelect").value = "";
    proposalFilters = { report_id: "", area: "", status: "", risk: "" };
    proposalKpiFilter = "all";
    switchProposalView("all");
}

async function archiveProposal(proposalId) {
    try {
        const res = await fetch(`/proposals/${proposalId}/update`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: "Finalizado" })
        });
        if (res.ok) {
            showToast("Propuesta archivada en Repositorio Plan 2026", "success");
            loadAllData();
        } else {
            showToast("Error al archivar propuesta", "error");
        }
    } catch (e) {
        showToast("Error de conexión", "error");
    }
}

async function inlineUpdateProposalStatus(el) {
    const proposalId = el.dataset.proposalId;
    const value = el.value;

    const dbValue = (value === "Vencido") ? "En proceso" : value;

    try {
        const resp = await fetch(`/proposals/${proposalId}/update`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: dbValue })
        });
        if (resp.ok) {
            showToast(`Estado de propuesta actualizado a "${dbValue}"`, "success");
            await loadAllData();
        } else {
            showToast("Error al actualizar propuesta", "error");
            await loadAllData();
        }
    } catch (e) {
        showToast("Error de conexión", "error");
        await loadAllData();
    }
}

function isFinalized(item) {
    return getRowEffectiveStatus(item, item) === "Finalizado";
}

function isOpenProposalWithoutPlan(proposal) {
    return !isFinalized(proposal) && Number(proposal.action_plans_count ?? proposal.action_plans?.length ?? 0) === 0;
}

function showProposalsWithoutPlans() {
    selectProposalKpiFilter("no_plan");
}

function renderProposalsTab() {
    const tbody = el("proposalsTableBody");
    if (!tbody) return;

    populateProposalFilterDropdowns();

    // KPI counts are calculated from the common report/area/status/risk scope,
    // before the selected KPI detail filter is applied.
    const baseFiltered = currentProposals.filter(p => {
        const effStatus = p.effective_status || getRowEffectiveStatus(p, p);
        if (proposalFilters.report_id && p.report_id !== proposalFilters.report_id) return false;
        if (proposalFilters.area && p.responsible_area !== proposalFilters.area) return false;
        if (proposalFilters.status && !isStatusEqual(effStatus, proposalFilters.status)) return false;
        if (proposalFilters.risk && (p.finding_severity || p.severity) !== proposalFilters.risk) return false;
        return true;
    });
    const inProcess = p => ["En proceso", "Vencido"].includes(p.effective_status || getRowEffectiveStatus(p, p));
    const completed = p => isFinalized(p);
    if (el("propKpiTotal")) el("propKpiTotal").textContent = baseFiltered.length;
    if (el("propKpiNoPlan")) el("propKpiNoPlan").textContent = baseFiltered.filter(isOpenProposalWithoutPlan).length;
    if (el("propKpiInProcess")) el("propKpiInProcess").textContent = baseFiltered.filter(inProcess).length;
    if (el("propKpiCompleted")) el("propKpiCompleted").textContent = baseFiltered.filter(completed).length;
    const activeKpi = currentProposalViewMode === "repo" ? "completed" : proposalKpiFilter;
    const matchesKpi = p => activeKpi === "all" ||
        (activeKpi === "no_plan" && isOpenProposalWithoutPlan(p)) ||
        (activeKpi === "in_process" && inProcess(p)) ||
        (activeKpi === "completed" && completed(p));
    const filtered = baseFiltered.filter(matchesKpi);
    const labels = { all: "Total Propuestas", no_plan: "Abiertas sin plan", in_process: "En Implementación", completed: "Finalizadas / Archivadas" };
    if (el("propNoPlanLabel")) el("propNoPlanLabel").textContent = "Abiertas sin plan · Ver detalle";
    ["all", "no_plan", "in_process", "completed"].forEach(k => {
        const card = el("propKpiCard_" + k);
        if (card) {
            card.setAttribute("aria-pressed", String(activeKpi === k));
            card.style.outline = activeKpi === k ? "2px solid #0055D4" : "none";
            card.style.outlineOffset = "-2px";
        }
    });
    const detailTitle = el("proposalKpiDetailTitle");
    if (detailTitle) detailTitle.textContent = labels[activeKpi] + " · " + filtered.length + " propuestas";

    if (!filtered.length) {
        const msg = currentProposalViewMode === "repo"
            ? "No hay propuestas archivadas en el <strong>Repositorio Plan 2026</strong>."
            : "No hay propuestas registradas con los filtros aplicados.";
        tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: #64748b; padding: 36px;">${msg}</td></tr>`;
        return;
    }

    const statusOptions = ["En proceso", "En suspensión", "Finalizado"];

    tbody.innerHTML = filtered.map(p => {
        const effStatus = p.effective_status || getRowEffectiveStatus(p, p);
        const rawStatus = p.status || "En proceso";
        const isArchived = ["finalizado", "completada", "implementada", "archivada"].includes(effStatus.toLowerCase());
        const statusClass = rawStatus.toLowerCase().replace(/\s+/g, '-').replace('ó', 'o').replace('sión', 'sion');

        const statusSelectHtml = `<select class="inline-select inline-select-status pill-${statusClass}" data-proposal-id="${p.id}" data-finding-id="${p.finding_id}" onchange="inlineUpdateProposalStatus(this)">
            ${statusOptions.map(s => `<option value="${s}" ${isStatusEqual(s, rawStatus) ? 'selected' : ''}>${s}</option>`).join('')}
        </select>`;

        const vencidoBadgeHtml = (effStatus.toLowerCase() === "vencido")
            ? `<span class="pill-vencido" style="display:inline-block; padding:2px 8px; font-size:11px; font-weight:700; border-radius:12px; margin-left:4px;">🚨 Vencido</span>`
            : ``;

        const archiveActionCell = isArchived
            ? `<span class="repo-badge">🗃️ Plan 2026</span>`
            : `<button class="btn btn-outlined" style="padding:2px 8px; font-size:11px;" onclick="archiveProposal('${p.id}')">🗃️ Archivar</button>`;

        return `
            <tr>
                <td class="id-cell">${escapeHtml(p.code)}</td>
                <td style="min-width: 480px; max-width: 650px; white-space: normal; word-break: break-word;"><strong style="color:#16A34A; line-height: 1.5; display: inline-block;">💡 ${escapeHtml(p.proposal_text || p.title)}</strong></td>
                <td>
                    <a href="#" style="color:#0055D4; font-weight:700;" onclick="openFindingDrawer('${p.finding_id}'); return false;">${escapeHtml(p.finding_code)}</a>
                </td>
                <td><strong>${escapeHtml(p.responsible_area || 'Operaciones')}</strong></td>
                <td><div style="display:flex; align-items:center; flex-wrap:wrap; gap:4px;">${statusSelectHtml}${vencidoBadgeHtml}</div></td>
                <td>${escapeHtml(p.action_owner || 'Auditoría')}</td>
                <td>
                    <button class="btn btn-outlined" style="padding:2px 8px; font-size:11px;" onclick="switchTab('planes')">
                        ${p.action_plans_count || 0} plan(es)
                    </button>
                </td>
                <td>${escapeHtml(p.target_date || '31/10/2026')}</td>
                <td>${archiveActionCell}</td>
                <td style="font-size: 11px; color: #64748B;">${escapeHtml(p.report_title)}</td>
            </tr>
        `;
    }).join("");
}

// ============================================================
// 3. PLANES DE ACCIÓN TAB & CASCADING MODAL
// ============================================================

function renderActionPlansTab() {
    const tbody = el("actionPlansTableBody");
    if (!tbody) return;

    if (!currentActionPlans.length) {
        tbody.innerHTML = `<tr><td colspan="11" style="text-align: center; color: #64748b; padding: 36px;">No hay planes de acción registrados. Hacé clic en "+ Crear Plan de Acción" arriba.</td></tr>`;
        return;
    }

    const statusOptions = ["En proceso", "En suspensión", "Finalizado"];

    tbody.innerHTML = currentActionPlans.map(pa => {
        const effStatus = pa.effective_status || getRowEffectiveStatus(pa, pa);
        const rawStatus = pa.status || "En proceso";
        const statusClass = rawStatus.toLowerCase().replace(/\s+/g, '-').replace('ó', 'o').replace('sión', 'sion');

        const statusSelectHtml = `<select class="inline-select inline-select-status pill-${statusClass}" data-plan-id="${pa.id}" onchange="inlineUpdateActionPlanStatus(this)">
            ${statusOptions.map(s => `<option value="${s}" ${isStatusEqual(s, rawStatus) ? 'selected' : ''}>${s}</option>`).join('')}
        </select>`;

        const vencidoBadgeHtml = (effStatus.toLowerCase() === "vencido")
            ? `<span class="pill-vencido" style="display:inline-block; padding:2px 8px; font-size:11px; font-weight:700; border-radius:12px; margin-left:4px;">🚨 Vencido</span>`
            : ``;

        return `
            <tr>
                <td class="id-cell">${escapeHtml(pa.code)}</td>
                <td><strong>${escapeHtml(pa.action_text || pa.title)}</strong></td>
                <td><span style="color:#16A34A; font-weight:600;">💡 ${escapeHtml(pa.proposal_code)}</span></td>
                <td>
                    <a href="#" style="color:#0055D4; font-weight:700;" onclick="openFindingDrawer('${pa.finding_code}'); return false;">${escapeHtml(pa.finding_code)}</a>
                </td>
                <td>${escapeHtml(pa.report_title)}</td>
                <td><strong>${escapeHtml(pa.action_owner)}</strong></td>
                <td>${escapeHtml(pa.target_date || '30/09/2026')}</td>
                <td>
                    <div style="display:flex; align-items:center; gap:6px;">
                        <div style="background:#E2E8F0; border-radius:4px; height:8px; flex:1; overflow:hidden;">
                            <div style="background:var(--primary-blue); width:${pa.progress_pct || 0}%; height:100%;"></div>
                        </div>
                        <span>${pa.progress_pct || 0}%</span>
                    </div>
                </td>
                <td><div style="display:flex; align-items:center; flex-wrap:wrap; gap:4px;">${statusSelectHtml}${vencidoBadgeHtml}</div></td>
                <td>${pa.evidence_file ? `📎 <small>${escapeHtml(pa.evidence_file)}</small>` : `<span style="color:#94A3B8;">Sin evidencia</span>`}</td>
                <td>
                    <button class="btn btn-outlined" style="padding: 3px 8px; font-size: 11px;" onclick="openUpdatePlanModal('${pa.id}')">⚙️ Actualizar</button>
                </td>
            </tr>
        `;
    }).join("");
}

function openNewActionPlanModal(preselectedFindingId = null) {
    const reportSelect = el("modalPlanReport");
    const findingSelect = el("modalPlanFinding");
    const proposalSelect = el("modalPlanProposal");

    if (!reportSelect || !findingSelect || !proposalSelect) return;

    reportSelect.innerHTML = `<option value="">-- Seleccionar Informe --</option>` +
        currentReports.map(r => `<option value="${r.id}">${escapeHtml(r.title)} (${r.code})</option>`).join("");

    findingSelect.innerHTML = `<option value="">Seleccioná un informe primero...</option>`;
    findingSelect.disabled = true;

    proposalSelect.innerHTML = `<option value="">Seleccioná un hallazgo primero...</option>`;
    proposalSelect.disabled = true;

    if (el("modalPlanActionText")) el("modalPlanActionText").value = "";
    if (el("modalPlanOwner")) el("modalPlanOwner").value = "Auditoría Interna";
    if (el("modalPlanTargetDate")) el("modalPlanTargetDate").value = new Date().toISOString().slice(0, 10);
    if (el("modalPlanProgress")) el("modalPlanProgress").value = 0;
    if (el("modalPlanNotes")) el("modalPlanNotes").value = "";

    if (preselectedFindingId) {
        const f = currentFindings.find(item => item.id === preselectedFindingId || item.code === preselectedFindingId);
        if (f) {
            reportSelect.value = f.report_id;
            onModalReportChange(f.report_id);
            findingSelect.value = f.id;
            onModalFindingChange(f.id);
        }
    }

    if (el("actionPlanModal")) el("actionPlanModal").style.display = "flex";
}

function closeActionPlanModal() {
    if (el("actionPlanModal")) el("actionPlanModal").style.display = "none";
}

function onModalReportChange(reportId) {
    const findingSelect = el("modalPlanFinding");
    const proposalSelect = el("modalPlanProposal");

    if (!findingSelect || !proposalSelect) return;

    if (!reportId) {
        findingSelect.innerHTML = `<option value="">Seleccioná un informe primero...</option>`;
        findingSelect.disabled = true;
        proposalSelect.innerHTML = `<option value="">Seleccioná un hallazgo primero...</option>`;
        proposalSelect.disabled = true;
        return;
    }

    const filtered = currentFindings.filter(f => f.report_id === reportId);
    findingSelect.innerHTML = `<option value="">-- Seleccionar Hallazgo --</option>` +
        filtered.map(f => `<option value="${f.id}">${escapeHtml(f.code)} - ${escapeHtml(f.title)}</option>`).join("");
    findingSelect.disabled = false;

    proposalSelect.innerHTML = `<option value="">Seleccioná un hallazgo primero...</option>`;
    proposalSelect.disabled = true;
}

function onModalFindingChange(findingId) {
    const proposalSelect = el("modalPlanProposal");
    if (!proposalSelect) return;

    if (!findingId) {
        proposalSelect.innerHTML = `<option value="">Seleccioná un hallazgo primero...</option>`;
        proposalSelect.disabled = true;
        return;
    }

    const finding = currentFindings.find(f => f.id === findingId || f.code === findingId);
    const proposals = finding ? (finding.proposals || []) : [];

    if (!proposals.length) {
        proposalSelect.innerHTML = `<option value="">✨ (Se creará una propuesta automáticamente al guardar)</option>`;
        proposalSelect.disabled = false;
        return;
    }

    proposalSelect.innerHTML = `<option value="">-- Seleccionar Propuesta (Opcional) --</option>` +
        proposals.map(p => {
            const pVal = p.id || p.code || (p.number ? `P-${p.number}` : "");
            const pCode = p.code || (p.number ? `PM-2026-${String(p.number).padStart(3, '0')}` : "Propuesta");
            return `<option value="${pVal}">${escapeHtml(pCode)} - ${escapeHtml(p.proposal_text || p.title || '')}</option>`;
        }).join("");
    proposalSelect.disabled = false;
}

async function saveActionPlanFromModal() {
    let finding_id = el("modalPlanFinding")?.value || null;
    let proposal_id = el("modalPlanProposal")?.value || null;
    if (proposal_id === "undefined" || proposal_id === "null") proposal_id = null;
    if (finding_id === "undefined" || finding_id === "null") finding_id = null;

    const action_text = el("modalPlanActionText")?.value;
    const action_owner = el("modalPlanOwner")?.value;
    const target_date = el("modalPlanTargetDate")?.value;
    const status = el("modalPlanStatus")?.value;
    const progress_pct = el("modalPlanProgress")?.value;
    const notes = el("modalPlanNotes")?.value;

    if (!action_text) {
        showToast("Por favor completá la descripción de la Acción Comprometida.", "warning");
        return;
    }

    if (!finding_id && !proposal_id) {
        showToast("Por favor seleccioná un informe y hallazgo origen.", "warning");
        return;
    }

    try {
        const response = await fetch("/action-plans", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ finding_id, proposal_id, action_text, action_owner, target_date, status, progress_pct, notes })
        });

        if (response.ok) {
            showToast("Plan de Acción creado e integrado correctamente.", "success");
            closeActionPlanModal();
            await loadAllData();
            switchTab("planes");
        } else {
            const data = await response.json().catch(() => ({}));
            showToast(data.error || "Error al crear el Plan de Acción.", "error");
        }
    } catch (err) {
        console.error(err);
        showToast("Error de conexión.", "error");
    }
}

function openNewPlanFromCurrentDrawer() {
    if (!currentDrawerFindingId) return;
    const fId = currentDrawerFindingId;
    closeFindingDrawer();
    openNewActionPlanModal(fId);
}

async function inlineUpdateActionPlanStatus(el) {
    const planId = el.dataset.planId;
    const value = el.value;
    const plan = currentActionPlans.find(p => p.id === planId);
    if (!plan) return;

    try {
        const res = await fetch(`/action-plans/${plan.id}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: value, confirm_finalize: (value === "Finalizado") })
        });
        if (res.ok) {
            showToast(`Estado de plan ${plan.code} actualizado a "${value}"`, "success");
            await loadAllData();
        } else {
            showToast("Error al actualizar estado del plan", "error");
            await loadAllData();
        }
    } catch (e) {
        showToast("Error de conexión", "error");
        await loadAllData();
    }
}

function openUpdatePlanModal(planId) {
    const plan = currentActionPlans.find(p => p.id === planId || p.code === planId);
    if (!plan) return;

    if (el("editPlanId")) el("editPlanId").value = plan.id;
    if (el("editPlanCode")) el("editPlanCode").textContent = plan.code;
    if (el("editPlanTitle")) el("editPlanTitle").textContent = plan.action_text || plan.title || "";
    if (el("editPlanProgress")) el("editPlanProgress").value = plan.progress_pct || 0;
    if (el("editPlanStatus")) el("editPlanStatus").value = plan.status || "En proceso";
    if (el("editPlanOwner")) el("editPlanOwner").value = plan.action_owner || "";
    if (el("editPlanTargetDate")) el("editPlanTargetDate").value = (plan.target_date || "").slice(0, 10);
    if (el("editPlanNotes")) el("editPlanNotes").value = plan.notes || "";
    if (el("editPlanEvidence")) el("editPlanEvidence").value = plan.evidence_file || "";

    const alertBox = el("finalizeConfirmAlert");
    if (alertBox) {
        alertBox.style.display = (plan.progress_pct >= 100 || plan.status === "Finalizado") ? "block" : "none";
    }

    const modal = el("updatePlanModal");
    if (modal) modal.style.display = "flex";
}

function closeUpdatePlanModal() {
    const modal = el("updatePlanModal");
    if (modal) modal.style.display = "none";
}

function onEditPlanProgressInput(val) {
    const num = Math.max(0, Math.min(100, parseInt(val) || 0));
    const statusSelect = el("editPlanStatus");
    const alertBox = el("finalizeConfirmAlert");

    if (num === 100) {
        // No seleccionar Finalizado automáticamente.
        // Si no se selecciona Finalizado explícitamente, se mantiene En proceso (100% con pendiente de validación).
        if (alertBox) alertBox.style.display = "block";
    } else {
        if (statusSelect && statusSelect.value === "Finalizado") {
            statusSelect.value = "En proceso";
        }
        if (alertBox) alertBox.style.display = "none";
    }
}

function onEditPlanStatusChange(val) {
    const progressInput = el("editPlanProgress");
    const alertBox = el("finalizeConfirmAlert");

    if (val === "Finalizado") {
        if (progressInput) progressInput.value = 100;
        if (alertBox) alertBox.style.display = "block";
    } else {
        if (val === "En proceso" && progressInput && Number(progressInput.value) === 100) {
            progressInput.value = 0;
        }
        if (alertBox) alertBox.style.display = "none";
    }
}

async function submitUpdatePlanModal() {
    const planId = el("editPlanId")?.value;
    if (!planId) return;

    const progress_pct = Math.max(0, Math.min(100, parseInt(el("editPlanProgress")?.value) || 0));
    const status = el("editPlanStatus")?.value || "En proceso";
    const action_owner = el("editPlanOwner")?.value;
    const target_date = el("editPlanTargetDate")?.value;
    const notes = el("editPlanNotes")?.value;
    const evidence_file = el("editPlanEvidence")?.value;

    try {
        const res = await fetch(`/action-plans/${planId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                progress_pct,
                status,
                action_owner,
                target_date,
                notes,
                evidence_file,
                confirm_finalize: (status === "Finalizado")
            })
        });

        if (res.ok) {
            showToast("Plan de Acción actualizado exitosamente.", "success");
            closeUpdatePlanModal();
            await loadAllData();
        } else {
            showToast("Error al actualizar Plan de Acción.", "error");
        }
    } catch (e) {
        showToast("Error de conexión al actualizar plan", "error");
    }
}

// ============================================================
// TABLERO EJECUTIVO UNIFICADO (v1.4.0)
// ============================================================

let execFilters = {
    report_id: "",
    area: "",
    period: ""
};
let execData = null;

async function loadExecutiveDashboard() {
    const seq = ++execDashboardSeq;
    try {
        populateExecutiveFilterDropdowns();

        const params = new URLSearchParams();
        if (execFilters.report_id) params.append("report_id", execFilters.report_id);
        if (execFilters.area) params.append("area", execFilters.area);
        if (execFilters.period) params.append("period", execFilters.period);

        const url = `/api/kpi-executive${params.toString() ? "?" + params.toString() : ""}`;
        const res = await fetch(url);
        if (seq !== execDashboardSeq) return;
        if (!res.ok) {
            showToast("Error cargando Tablero Ejecutivo", "error");
            throw new Error(`HTTP ${res.status} al cargar Tablero Ejecutivo`);
        }
        const data = await res.json();
        if (seq !== execDashboardSeq) return;
        execData = data;

        // Set cut date in header
        if (el("execCutDateStr")) el("execCutDateStr").textContent = execData.cut_date || "--";

        renderExecutiveScopeBar(execData.scope);
        renderExecutiveKpis(execData.kpis);
        renderExecutiveAreaChart(execData.charts ? execData.charts.by_area : []);
        renderExecutiveAgingChart(execData.charts ? execData.charts.aging : {});
        renderExecutiveEffectivenessChart(execData.charts?.proposal_states || {});
        renderExecutiveAgendaTable(execData.agenda || []);
    } catch (err) {
        console.error("Error cargando Tablero Ejecutivo:", err);
        showToast("Error de conexión al cargar Tablero Ejecutivo", "error");
        throw err;
    }
}

function populateExecutiveFilterDropdowns() {
    const reportSel = el("execFilterReport");
    const areaSel = el("execFilterArea");
    const periodSel = el("execFilterPeriod");

    if (reportSel) {
        const curVal = execFilters.report_id || reportSel.value;
        let html = '<option value="">Todos los Informes</option>';
        (currentReports || []).forEach(r => {
            html += `<option value="${r.id}" ${String(r.id) === String(curVal) ? 'selected' : ''}>${escapeHtml(r.code || '')} - ${escapeHtml(r.title || '')}</option>`;
        });
        reportSel.innerHTML = html;
        if (!Array.from(reportSel.options).some(o => o.value === String(curVal))) {
            execFilters.report_id = "";
            reportSel.value = "";
        }
    }

    if (areaSel) {
        const curVal = execFilters.area || areaSel.value;
        const areas = new Set();
        (currentFindings || []).forEach(f => { if (f.responsible_area || f.area) areas.add(f.responsible_area || f.area); });
        (currentProposals || []).forEach(p => { if (p.responsible_area) areas.add(p.responsible_area); });

        let html = '<option value="">Todas las Áreas Responsables</option>';
        Array.from(areas).sort().forEach(a => {
            html += `<option value="${escapeHtml(a)}" ${a === curVal ? 'selected' : ''}>${escapeHtml(a)}</option>`;
        });
        areaSel.innerHTML = html;
        if (!Array.from(areaSel.options).some(o => o.value === curVal)) {
            execFilters.area = "";
            areaSel.value = "";
        }
    }

    if (periodSel) {
        const curVal = execFilters.period || periodSel.value;
        const periods = new Set();
        (currentReports || []).forEach(r => { if (r.period) periods.add(r.period); });

        let html = '<option value="">Todos los Períodos</option>';
        Array.from(periods).sort().forEach(p => {
            html += `<option value="${escapeHtml(p)}" ${p === curVal ? 'selected' : ''}>${escapeHtml(p)}</option>`;
        });
        periodSel.innerHTML = html;
        if (!Array.from(periodSel.options).some(o => o.value === curVal)) {
            execFilters.period = "";
            periodSel.value = "";
        }
    }
}

function onExecutiveFilterChange() {
    execFilters.report_id = el("execFilterReport")?.value || "";
    execFilters.area = el("execFilterArea")?.value || "";
    execFilters.period = el("execFilterPeriod")?.value || "";
    loadExecutiveDashboard();
}

function resetExecutiveFilters() {
    execFilters = { report_id: "", area: "", period: "" };
    if (el("execFilterReport")) el("execFilterReport").value = "";
    if (el("execFilterArea")) el("execFilterArea").value = "";
    if (el("execFilterPeriod")) el("execFilterPeriod").value = "";
    loadExecutiveDashboard();
}

function renderExecutiveScopeBar(scope) {
    if (!scope) return;
    if (el("execScopeReports")) el("execScopeReports").textContent = scope.reports || 0;
    if (el("execScopeFindings")) el("execScopeFindings").textContent = scope.findings || 0;
    if (el("execScopeProposals")) el("execScopeProposals").textContent = scope.proposals || 0;
    if (el("execScopePlans")) el("execScopePlans").textContent = scope.plans || 0;
}

function renderExecutiveKpis(kpis) {
    if (!kpis) return;

    // Card A: Riesgo Alto Abierto
    if (el("execValHighRisk")) el("execValHighRisk").textContent = kpis.high_risk_open ? kpis.high_risk_open.count : 0;
    if (el("execSubHighRisk")) el("execSubHighRisk").textContent = kpis.high_risk_open ? kpis.high_risk_open.subtitle : "";

    // Card A2: Riesgo Medio Abierto
    if (el("execValMediumRisk")) el("execValMediumRisk").textContent = kpis.medium_risk_open ? kpis.medium_risk_open.count : 0;
    if (el("execSubMediumRisk")) el("execSubMediumRisk").textContent = kpis.medium_risk_open ? kpis.medium_risk_open.subtitle : "";

    // Card A3: Riesgo Bajo Abierto
    if (el("execValLowRisk")) el("execValLowRisk").textContent = kpis.low_risk_open ? kpis.low_risk_open.count : 0;
    if (el("execSubLowRisk")) el("execSubLowRisk").textContent = kpis.low_risk_open ? kpis.low_risk_open.subtitle : "";

    // Card B: Compromisos Vencidos
    if (el("execValOverdue")) el("execValOverdue").textContent = kpis.overdue_commitments ? kpis.overdue_commitments.count : 0;
    if (el("execSubOverdue")) el("execSubOverdue").textContent = kpis.overdue_commitments ? kpis.overdue_commitments.subtitle : "";

    // Card C: Pendientes de Validación
    if (el("execValPendingValidation")) el("execValPendingValidation").textContent = kpis.pending_validation ? kpis.pending_validation.count : 0;
    if (el("execSubPendingValidation")) el("execSubPendingValidation").textContent = kpis.pending_validation ? kpis.pending_validation.subtitle : "";

    // Card D: Implementación Validada
    if (el("execValValidatedRate")) {
        const rate = kpis.validated_implementation ? kpis.validated_implementation.rate : null;
        el("execValValidatedRate").textContent = rate !== null ? `${rate}%` : "Sin datos suficientes";
    }
    if (el("execSubValidatedRate")) el("execSubValidatedRate").textContent = kpis.validated_implementation ? kpis.validated_implementation.subtitle : "";

    // Card E: Cierre en Plazo
    if (el("execValOnTimeRate")) {
        const rate = kpis.on_time_closing ? kpis.on_time_closing.rate : null;
        el("execValOnTimeRate").textContent = rate !== null ? `${rate}%` : "Sin datos suficientes";
    }
    if (el("execSubOnTimeRate")) el("execSubOnTimeRate").textContent = kpis.on_time_closing ? kpis.on_time_closing.subtitle : "";
}

let execChartActiveSeries = { high: true, medium: true, low: true, overdue: true };
let latestExecutiveAreaData = [];

function toggleExecutiveChartSeries(seriesKey) {
    if (execChartActiveSeries[seriesKey] !== undefined) {
        execChartActiveSeries[seriesKey] = !execChartActiveSeries[seriesKey];
    }

    const btnMap = {
        high: { id: "btnToggleRiskHigh", cls: "active-high" },
        medium: { id: "btnToggleRiskMed", cls: "active-med" },
        low: { id: "btnToggleRiskLow", cls: "active-low" },
        overdue: { id: "btnToggleRiskOv", cls: "active-ov" }
    };

    const target = btnMap[seriesKey];
    if (target && el(target.id)) {
        const isActive = execChartActiveSeries[seriesKey];
        el(target.id).className = `exec-risk-btn ${isActive ? target.cls : "inactive"}`;
    }

    renderExecutiveAreaChart(latestExecutiveAreaData);
}

function renderExecutiveAreaChart(areaData) {
    if (areaData) latestExecutiveAreaData = areaData;
    const canvas = el("execChartArea");
    const ctx = canvas?.getContext("2d");
    if (!ctx) return;
    if (chartInstances.execArea) chartInstances.execArea.destroy();

    const dataList = latestExecutiveAreaData || [];
    const labels = dataList.map(item => item.area);

    const datasets = [];

    if (execChartActiveSeries.high) {
        datasets.push({
            label: "Riesgo Alto Abierto",
            data: dataList.map(item => item.high_risk_open || 0),
            backgroundColor: "#DC2626",
            borderRadius: 6,
            borderSkipped: false,
            barPercentage: 0.7,
            categoryPercentage: 0.8
        });
    }

    if (execChartActiveSeries.medium) {
        datasets.push({
            label: "Riesgo Medio Abierto",
            data: dataList.map(item => item.medium_risk_open || 0),
            backgroundColor: "#F59E0B",
            borderRadius: 6,
            borderSkipped: false,
            barPercentage: 0.7,
            categoryPercentage: 0.8
        });
    }

    if (execChartActiveSeries.low) {
        datasets.push({
            label: "Riesgo Bajo Abierto",
            data: dataList.map(item => item.low_risk_open || 0),
            backgroundColor: "#10B981",
            borderRadius: 6,
            borderSkipped: false,
            barPercentage: 0.7,
            categoryPercentage: 0.8
        });
    }

    if (execChartActiveSeries.overdue) {
        datasets.push({
            label: "Compromisos Vencidos",
            data: dataList.map(item => item.overdue || 0),
            backgroundColor: "#EA580C",
            borderRadius: 6,
            borderSkipped: false,
            barPercentage: 0.7,
            categoryPercentage: 0.8
        });
    }

    chartInstances.execArea = new Chart(ctx, {
        type: "bar",
        data: {
            labels: labels.length ? labels : ["Sin áreas registradas"],
            datasets: datasets.length ? datasets : [{ label: "Sin métrica activa", data: [0], backgroundColor: "#CBD5E1" }]
        },
        options: {
            indexAxis: "y",
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: "#0F172A",
                    titleFont: { family: "Inter, sans-serif", size: 12, weight: "700" },
                    bodyFont: { family: "Inter, sans-serif", size: 11 },
                    padding: 10,
                    cornerRadius: 8
                }
            },
            scales: {
                x: {
                    beginAtZero: true,
                    grid: { color: "#F1F5F9" },
                    ticks: { precision: 0, font: { family: "Inter, sans-serif", size: 11 }, color: "#64748B" }
                },
                y: {
                    grid: { display: false },
                    ticks: { font: { family: "Inter, sans-serif", size: 11, weight: "600" }, color: "#334155" }
                }
            }
        }
    });
}

function renderExecutiveAgingChart(agingData) {
    const canvas = el("execChartAging");
    const ctx = canvas?.getContext("2d");
    if (!ctx) return;
    if (chartInstances.execAging) chartInstances.execAging.destroy();

    const dataVals = [
        agingData ? agingData["1_30"] || 0 : 0,
        agingData ? agingData["31_60"] || 0 : 0,
        agingData ? agingData[">60"] || 0 : 0
    ];

    chartInstances.execAging = new Chart(ctx, {
        type: "bar",
        data: {
            labels: ["1-30 días", "31-60 días", "> 60 días"],
            datasets: [
                {
                    label: "Planes Vencidos",
                    data: dataVals,
                    backgroundColor: ["#F59E0B", "#F97316", "#DC2626"],
                    borderRadius: 6,
                    borderSkipped: false,
                    barPercentage: 0.6
                }
            ]
        },
        options: {
            indexAxis: "y",
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: "#0F172A",
                    titleFont: { family: "Inter, sans-serif", size: 12, weight: "700" },
                    bodyFont: { family: "Inter, sans-serif", size: 11 },
                    padding: 10,
                    cornerRadius: 8
                }
            },
            scales: {
                x: {
                    beginAtZero: true,
                    grid: { color: "#F1F5F9" },
                    ticks: { precision: 0, font: { family: "Inter, sans-serif", size: 11 }, color: "#64748B" }
                },
                y: {
                    grid: { display: false },
                    ticks: { font: { family: "Inter, sans-serif", size: 11, weight: "600" }, color: "#334155" }
                }
            }
        }
    });
}

function renderExecutiveEffectivenessChart(states) {
    const canvas = el("execChartEffectiveness");
    const ctx = canvas?.getContext("2d");
    if (!ctx) return;
    if (chartInstances.execEffectiveness) chartInstances.execEffectiveness.destroy();

    const labels = ["Finalizado", "Pendiente de validación", "En proceso", "En suspensión"];

    chartInstances.execEffectiveness = new Chart(ctx, {
        type: "bar",
        data: {
            labels,
            datasets: [
                {
                    label: "Propuestas",
                    data: labels.map(label => states[label] || 0),
                    backgroundColor: ["#10B981", "#2563EB", "#94A3B8", "#D97706"],
                    borderRadius: 6,
                    borderSkipped: false,
                    barPercentage: 0.6
                }
            ]
        },
        options: {
            indexAxis: "y",
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: "#0F172A",
                    titleFont: { family: "Inter, sans-serif", size: 12, weight: "700" },
                    bodyFont: { family: "Inter, sans-serif", size: 11 },
                    padding: 10,
                    cornerRadius: 8
                }
            },
            scales: {
                x: {
                    beginAtZero: true,
                    grid: { color: "#F1F5F9" },
                    ticks: { precision: 0, font: { family: "Inter, sans-serif", size: 11 }, color: "#64748B" }
                },
                y: {
                    grid: { display: false },
                    ticks: { font: { family: "Inter, sans-serif", size: 11, weight: "600" }, color: "#334155" }
                }
            }
        }
    });
}

function renderExecutiveAgendaTable(agendaItems) {
    const tbody = el("execAgendaTableBody");
    if (!tbody) return;

    if (!agendaItems || agendaItems.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" class="text-center py-4 text-slate-500">
                    <i class="fas fa-check-circle text-green-500 mr-2"></i>No hay compromisos vencidos para los filtros seleccionados.
                </td>
            </tr>
        `;
        return;
    }

    let html = "";
    agendaItems.forEach(item => {
        const severityClass = item.risk_level === 'Alto' 
            ? 'bg-red-100 text-red-800 font-bold' 
            : item.risk_level === 'Medio' 
                ? 'bg-amber-100 text-amber-800' 
                : 'bg-green-100 text-green-800';

        const statusClass = item.status === 'Pendiente de validación'
            ? 'bg-purple-100 text-purple-800'
            : item.overdue_days > 0
                ? 'bg-red-100 text-red-800 font-semibold'
                : 'bg-blue-100 text-blue-800';

        html += `
            <tr class="hover:bg-slate-50 border-b border-slate-100">
                <td class="px-3 py-2 text-xs font-mono font-bold text-slate-800">${escapeHtml(item.plan_code || item.proposal_code || '-')}</td>
                <td class="px-3 py-2 text-xs font-medium text-slate-900 truncate max-w-xs" title="${escapeHtml(item.description)}">${escapeHtml(item.description)}</td>
                <td class="px-3 py-2 text-xs text-slate-700">${escapeHtml(item.responsible_area || '-')}</td>
                <td class="px-3 py-2 text-xs text-center"><span class="px-2 py-0.5 rounded text-xs ${severityClass}">${escapeHtml(item.risk_level)}</span></td>
                <td class="px-3 py-2 text-xs text-center font-mono ${item.overdue_days > 0 ? 'text-red-600 font-bold' : 'text-slate-600'}">${item.overdue_days > 0 ? '+' + item.overdue_days + 'd' : '0d'}</td>
                <td class="px-3 py-2 text-xs text-center"><span class="px-2 py-0.5 rounded text-xs ${statusClass}">${escapeHtml(item.status)}</span></td>
                <td class="px-3 py-2 text-xs text-slate-800 font-medium bg-amber-50/50">${escapeHtml(item.next_action)}</td>
            </tr>
        `;
    });
    tbody.innerHTML = html;
}

let currentExecutiveDrilldownMetric = null;
let currentExecutiveDrilldownTitle = null;

async function openExecutiveDrilldown(metricKey, title) {
    const modal = el("execDrilldownModal");
    if (!modal) return;

    currentExecutiveDrilldownMetric = metricKey;
    currentExecutiveDrilldownTitle = title;

    if (el("execDrilldownTitle")) el("execDrilldownTitle").textContent = title || "Detalle Auditoría";
    if (el("execDrilldownMetricTitle")) el("execDrilldownMetricTitle").textContent = title || "";
    if (el("execDrilldownTableHead")) {
        el("execDrilldownTableHead").innerHTML = `
            <tr>
                <th class="px-3 py-2 text-xs font-semibold text-slate-700">Código</th>
                <th class="px-3 py-2 text-xs font-semibold text-slate-700">Título / Compromiso</th>
                <th class="px-3 py-2 text-xs font-semibold text-slate-700">Informe / Área</th>
                <th class="px-3 py-2 text-xs font-semibold text-slate-700 text-center">Criticidad</th>
                <th class="px-3 py-2 text-xs font-semibold text-slate-700 text-center">Estado</th>
                <th class="px-3 py-2 text-xs font-semibold text-slate-700 text-center">Fecha Target</th>
                <th class="px-3 py-2 text-xs font-semibold text-slate-700 text-center">Acción</th>
            </tr>
        `;
    }
    if (el("execDrilldownTableBody")) {
        el("execDrilldownTableBody").innerHTML = '<tr><td colspan="7" class="text-center py-4"><i class="fas fa-spinner fa-spin mr-2"></i>Cargando detalle...</td></tr>';
    }

    modal.style.display = "flex";

    try {
        const params = new URLSearchParams();
        params.append("metric", metricKey);
        if (execFilters.report_id) params.append("report_id", execFilters.report_id);
        if (execFilters.area) params.append("area", execFilters.area);
        if (execFilters.period) params.append("period", execFilters.period);

        const res = await fetch(`/api/kpi-executive/drilldown?${params.toString()}`);
        if (!res.ok) {
            if (el("execDrilldownTableBody")) {
                el("execDrilldownTableBody").innerHTML = '<tr><td colspan="7" class="text-center py-4 text-red-500">Error al obtener el detalle del servidor.</td></tr>';
            }
            return;
        }

        const data = await res.json();
        renderExecutiveDrilldownTable(data.records);
    } catch (err) {
        console.error("Error abriendo drilldown:", err);
        if (el("execDrilldownTableBody")) {
            el("execDrilldownTableBody").innerHTML = '<tr><td colspan="7" class="text-center py-4 text-red-500">Error de conexión al cargar detalle.</td></tr>';
        }
    }
}

function closeExecutiveDrilldown() {
    const modal = el("execDrilldownModal");
    if (modal) modal.style.display = "none";
}

function renderExecutiveDrilldownTable(records) {
    const tbody = el("execDrilldownTableBody");
    if (!tbody) return;

    if (!records || records.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center py-4 text-slate-500">No se encontraron registros para esta métrica con los filtros aplicados.</td></tr>';
        return;
    }

    let html = "";
    records.forEach(r => {
        const targetId = r.finding_id || r.id;
        html += `
            <tr class="hover:bg-slate-50 border-b border-slate-100">
                <td class="px-3 py-2 text-xs font-mono font-bold text-slate-800">${escapeHtml(r.code || '-')}</td>
                <td class="px-3 py-2 text-xs text-slate-900">${escapeHtml(r.title || r.description || '-')}</td>
                <td class="px-3 py-2 text-xs text-slate-700">${escapeHtml(r.report_title || r.area || '-')}</td>
                <td class="px-3 py-2 text-xs text-center"><span class="px-2 py-0.5 rounded ${r.risk_level === 'Alto' ? 'bg-red-100 text-red-800 font-bold' : (r.risk_level === 'Bajo' ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800')}">${escapeHtml(r.risk_level || '-')}</span></td>
                <td class="px-3 py-2 text-xs text-center">${escapeHtml(r.status || '-')}</td>
                <td class="px-3 py-2 text-xs text-center font-mono">${escapeHtml(r.target_date || r.closed_date || '-')}</td>
                <td class="px-3 py-2 text-xs text-center" style="white-space:nowrap;">
                    <button class="btn btn-outlined" style="padding: 2px 6px; font-size: 11px;" onclick="openFindingDrawer('${targetId}')">✏️ Ver</button>
                    <button class="btn btn-outlined" style="padding: 2px 6px; font-size: 11px; color:#DC2626; border-color:#FCA5A5;" onclick="deleteFindingItem('${targetId}')">🗑️ Eliminar</button>
                </td>
            </tr>
        `;
    });
    tbody.innerHTML = html;
}

// ============================================================
// 4. TABLEROS (GERENCIAL DASHBOARD CON CHART.JS Y FILTROS INTERACTIVOS)
// ============================================================

async function loadDashboardTab() {
    try {
        const res = await fetch("/dashboard-stats");
        if (!res.ok) return;
        currentDashboardStats = await res.json();

        if (el("dashOpen")) el("dashOpen").textContent = currentDashboardStats.open_findings || 0;
        if (el("dashHigh")) el("dashHigh").textContent = currentDashboardStats.high_risk_findings || 0;
        if (el("dashProp")) el("dashProp").textContent = currentDashboardStats.total_proposals || 0;
        if (el("dashOverdue")) el("dashOverdue").textContent = currentDashboardStats.overdue_plans || 0;
        if (el("dashRate")) el("dashRate").textContent = `${currentDashboardStats.impl_rate || 0}%`;

        renderDashboardCharts(currentDashboardStats);
        renderCriticalPendingTable(currentDashboardStats.critical_pending || []);
    } catch (err) {
        console.error("Error cargando dashboard:", err);
    }
}

function renderDashboardCharts(stats) {
    // 1. Chart Risk
    const ctxRisk = el("chartRisk")?.getContext("2d");
    if (ctxRisk) {
        if (chartInstances.risk) chartInstances.risk.destroy();
        chartInstances.risk = new Chart(ctxRisk, {
            type: "doughnut",
            data: {
                labels: ["Alto", "Medio", "Bajo"],
                datasets: [{
                    data: [stats.risk_breakdown["Alto"] || 0, stats.risk_breakdown["Medio"] || 0, stats.risk_breakdown["Bajo"] || 0],
                    backgroundColor: ["#DC2626", "#D97706", "#16A34A"]
                }]
            },
            options: {
                responsive: true,
                plugins: { legend: { position: "bottom" } },
                onClick: (e, items) => {
                    if (items.length > 0) {
                        const label = ["Alto", "Medio", "Bajo"][items[0].index];
                        activeFilters.risk = label;
                        switchTab("hallazgos");
                        applyFilters();
                    }
                }
            }
        });
    }

    // 2. Chart Status
    const ctxStatus = el("chartStatus")?.getContext("2d");
    if (ctxStatus) {
        if (chartInstances.status) chartInstances.status.destroy();
        const labels = Object.keys(stats.status_breakdown || {});
        const dataVals = Object.values(stats.status_breakdown || {});
        chartInstances.status = new Chart(ctxStatus, {
            type: "bar",
            data: {
                labels: labels.length ? labels : ["Pendiente", "En proceso", "Completada"],
                datasets: [{
                    label: "Hallazgos",
                    data: dataVals.length ? dataVals : [0, 0, 0],
                    backgroundColor: "#0055D4"
                }]
            },
            options: { responsive: true, plugins: { legend: { display: false } } }
        });
    }

    // 3. Chart Plans
    const ctxPlans = el("chartPlans")?.getContext("2d");
    if (ctxPlans) {
        if (chartInstances.plans) chartInstances.plans.destroy();
        chartInstances.plans = new Chart(ctxPlans, {
            type: "pie",
            data: {
                labels: ["En término", "Planes Vencidos"],
                datasets: [{
                    data: [(currentActionPlans.length - (stats.overdue_plans || 0)), stats.overdue_plans || 0],
                    backgroundColor: ["#16A34A", "#B42318"]
                }]
            },
            options: { responsive: true, plugins: { legend: { position: "bottom" } } }
        });
    }

    // 4. Chart Aging
    const ctxAging = el("chartAging")?.getContext("2d");
    if (ctxAging) {
        if (chartInstances.aging) chartInstances.aging.destroy();
        const agingData = stats.aging_breakdown || {};
        chartInstances.aging = new Chart(ctxAging, {
            type: "bar",
            data: {
                labels: Object.keys(agingData),
                datasets: [{
                    label: "Hallazgos por Antigüedad",
                    data: Object.values(agingData),
                    backgroundColor: ["#16A34A", "#0055D4", "#D97706", "#DC2626"]
                }]
            },
            options: { responsive: true, plugins: { legend: { display: false } } }
        });
    }
}

function renderCriticalPendingTable(criticalItems) {
    const tbody = el("criticalPendingTableBody");
    if (!tbody) return;

    if (!criticalItems.length) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: #16A34A; padding: 24px;">¡Excelente! No hay pendientes críticos ni planes vencidos.</td></tr>`;
        return;
    }

    tbody.innerHTML = criticalItems.map(item => `
        <tr>
            <td>
                <a href="#" style="color:#0055D4; font-weight:700;" onclick="openFindingDrawer('${item.code}'); return false;">${escapeHtml(item.code)}</a> -
                <strong>${escapeHtml(item.title)}</strong>
            </td>
            <td><span class="pill pill-${(item.severity||'alto').toLowerCase()}">${escapeHtml(item.severity)}</span></td>
            <td>${escapeHtml(item.area)}</td>
            <td><strong>${escapeHtml(item.owner)}</strong></td>
            <td>${escapeHtml(item.target_date)}</td>
            <td>
                <strong style="color: ${item.days_overdue > 0 ? '#DC2626' : '#D97706'};">
                    ${item.days_overdue > 0 ? `⚠️ ${item.days_overdue} días` : 'Al día'}
                </strong>
            </td>
        </tr>
    `).join("");
}

// ============================================================
// 5. INDICADORES TAB (DASHBOARD EJECUTIVO KPIS & TABLA DECISIONES)
// ============================================================

let currentExecutiveKPIs = null;
let currentDecisionFilter = null;

async function loadKpiIndicatorsTab() {
    try {
        const res = await fetch("/api/kpi-executive");
        if (!res.ok) return;
        const data = await res.json();
        currentExecutiveKPIs = data.kpis || data.indicators || {};
        renderExecutiveKpiCards(currentExecutiveKPIs);
        renderAreaBreakdownList();
        renderDecisionTable(currentDecisionFilter);
    } catch (err) {
        console.error("Error cargando indicadores ejecutivos:", err);
    }
}

function renderExecutiveKpiCards(kpis) {
    if (!kpis) return;

    // 1. Riesgo Alto Abierto
    const hr = kpis.high_risk_open || {};
    if (el("execValHighRisk")) el("execValHighRisk").textContent = hr.count ?? 0;
    if (el("execContextHighRisk")) el("execContextHighRisk").textContent = hr.context || "0 hallazgos críticos";

    // 2. Compromisos Vencidos
    const ov = kpis.overdue_commitments || {};
    if (el("execValOverdue")) el("execValOverdue").textContent = ov.count ?? 0;
    if (el("execContextOverdue")) el("execContextOverdue").textContent = ov.context || "0 planes vencidos";

    // 3. Implementación Validada
    const vi = kpis.validated_implementation || {};
    if (el("execValValidated")) el("execValValidated").textContent = vi.percentage || "0%";
    if (el("execContextValidated")) el("execContextValidated").textContent = vi.context || "0 propuestas validadas";

    // 4. Cierre en Plazo
    const ot = kpis.on_time_closure || {};
    if (el("execValOnTime")) el("execValOnTime").textContent = ot.percentage || "Sin datos";
    if (el("execContextOnTime")) el("execContextOnTime").textContent = ot.context || "0 cierres evaluables";

    // 5. Pendiente de Validación
    const pv = kpis.pending_validation || {};
    if (el("execValPendingVal")) el("execValPendingVal").textContent = pv.count ?? 0;
    if (el("execContextPendingVal")) el("execContextPendingVal").textContent = pv.context || "0 compromisos al 100%";
}

function renderAreaBreakdownList() {
    const container = el("areaBreakdownList");
    if (!container) return;

    const areaMap = {};
    (currentFindings || []).forEach(f => {
        const area = f.responsible_area || "Operaciones";
        if (!areaMap[area]) {
            areaMap[area] = { total: 0, high: 0, open: 0, closed: 0 };
        }
        areaMap[area].total += 1;
        if ((f.severity || "").toLowerCase() === "alto") areaMap[area].high += 1;
        if ((f.status || "").toLowerCase() === "finalizado") {
            areaMap[area].closed += 1;
        } else {
            areaMap[area].open += 1;
        }
    });

    const areas = Object.keys(areaMap).sort((a, b) => areaMap[b].total - areaMap[a].total);

    if (areas.length === 0) {
        container.innerHTML = `<div style="padding: 16px; color: #64748B; text-align: center;">No hay hallazgos registrados por área.</div>`;
        return;
    }

    container.innerHTML = areas.map(area => {
        const info = areaMap[area];
        const pct = info.total > 0 ? Math.round((info.closed / info.total) * 100) : 0;
        return `
            <div style="padding:10px 12px; border-bottom:1px solid #E2E8F0;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <strong style="color:#0F172A; font-size:13px;">${escapeHtml(area)}</strong>
                    <span style="font-size:11px; font-weight:700; color:#0055D4;">${pct}% cerrado (${info.closed}/${info.total})</span>
                </div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-top:2px;">
                    <div style="font-size:11px; color:#64748B;">
                        ${info.open} abiertos | <span style="color:#DC2626; font-weight:600;">${info.high} Riesgo Alto</span>
                    </div>
                </div>
                <div style="background:#E2E8F0; border-radius:4px; height:6px; margin-top:6px; overflow:hidden;">
                    <div style="background:#0055D4; width:${pct}%; height:100%;"></div>
                </div>
            </div>
        `;
    }).join("");
}

function getDaysOverdue(dateStr) {
    if (!dateStr) return 0;
    let d = null;
    const str = dateStr.toString().trim();
    if (/^\d{4}-\d{2}-\d{2}/.test(str)) {
        const parts = str.slice(0, 10).split("-");
        d = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
    } else if (/^\d{1,2}\/\d{1,2}\/\d{4}/.test(str)) {
        const parts = str.slice(0, 10).split("/");
        d = new Date(parseInt(parts[2], 10), parseInt(parts[1], 10) - 1, parseInt(parts[0], 10));
    }
    if (!d || isNaN(d.getTime())) return 0;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    if (d >= today) return 0;
    const diffMs = today.getTime() - d.getTime();
    return Math.floor(diffMs / (1000 * 60 * 60 * 24));
}

function renderDecisionTable(filterKey = null) {
    const tbody = el("decisionTableBody");
    const titleEl = el("decisionTableTitle");
    if (!tbody) return;

    // Expand findings, proposals, and action plans into flat decision items
    let rows = [];
    (currentFindings || []).forEach(f => {
        const props = (f.proposals && f.proposals.length > 0) ? f.proposals : [null];
        props.forEach(p => {
            const plans = (p && p.action_plans && p.action_plans.length > 0) ? p.action_plans : [null];
            plans.forEach(pa => {
                const owner = (pa && pa.action_owner) || (p && p.action_owner) || f.action_owner || "Sin asignar";
                const targetDate = (pa && pa.target_date) || (p && p.target_date) || f.target_date || "";
                const status = (pa && pa.status) || (p && p.status) || f.status || "En proceso";
                const progressPct = pa ? pa.progress_pct : (p ? (p.progress_pct || 0) : 0);
                const daysOverdue = getDaysOverdue(targetDate);
                const isPendingVal = status === "Pendiente de validación" || (progressPct === 100 && status !== "Finalizado");

                rows.push({
                    finding_id: f.id,
                    finding_code: f.code,
                    finding_title: f.title || f.situation,
                    report_title: f.report_title || f.source_filename || "Informe",
                    proposal_id: p ? p.id : null,
                    proposal_code: p ? p.code : "-",
                    proposal_text: p ? (p.proposal_text || p.title) : "Sin propuesta",
                    plan_id: pa ? pa.id : null,
                    plan_code: pa ? pa.code : null,
                    plan_text: pa ? (pa.action_text || pa.title) : null,
                    area: f.responsible_area || "Operaciones",
                    severity: f.severity || "Medio",
                    owner: owner,
                    target_date: targetDate,
                    status: status,
                    days_overdue: daysOverdue,
                    is_pending_val: isPendingVal
                });
            });
        });
    });

    if (filterKey === "high_risk") {
        rows = rows.filter(r => (r.severity || "").toLowerCase() === "alto" && (r.status || "").toLowerCase() !== "finalizado");
        if (titleEl) titleEl.textContent = "Tabla de Decisiones: Hallazgos de Riesgo Alto Abiertos";
    } else if (filterKey === "overdue") {
        rows = rows.filter(r => r.days_overdue > 0 && (r.status || "").toLowerCase() !== "finalizado");
        if (titleEl) titleEl.textContent = "Tabla de Decisiones: Compromisos Vencidos";
    } else if (filterKey === "validated") {
        rows = rows.filter(r => ["finalizado", "completado", "validado"].includes((r.status || "").toLowerCase()));
        if (titleEl) titleEl.textContent = "Tabla de Decisiones: Implementaciones Validadas";
    } else if (filterKey === "pending_val") {
        rows = rows.filter(r => r.is_pending_val);
        if (titleEl) titleEl.textContent = "Tabla de Decisiones: Pendientes de Validación";
    } else if (filterKey === "on_time") {
        rows = rows.filter(r => ["finalizado", "completado", "validado"].includes((r.status || "").toLowerCase()) && r.days_overdue === 0);
        if (titleEl) titleEl.textContent = "Tabla de Decisiones: Cierres en Plazo";
    } else {
        if (titleEl) titleEl.textContent = "Tabla de Decisiones Ejecutivas (Todos)";
    }

    if (rows.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:20px; color:#64748B;">No se encontraron registros para este filtro.</td></tr>`;
        return;
    }

    const canValidate = ["Validador", "Admin"].includes(window.currentUserRole);

    tbody.innerHTML = rows.map(r => {
        let actionCol = `<span class="pill pill-${(r.status || 'en proceso').toLowerCase().replace(/\s+/g, '-')}">${escapeHtml(r.status || "En proceso")}</span>`;

        if (r.is_pending_val && canValidate && r.proposal_id) {
            actionCol += `<div style="margin-top:4px;"><button class="btn btn-primary" style="padding:2px 8px; font-size:10px;" onclick="validateProposal('${r.proposal_id}')">✓ Validar</button></div>`;
        }

        return `
            <tr>
                <td>
                    <span style="font-size:10px; color:#64748B; display:block;">${escapeHtml(r.report_title)}</span>
                    <a href="#" style="color:#0055D4; font-weight:700;" onclick="openFindingDrawer('${r.finding_id}'); return false;">${escapeHtml(r.finding_code)}</a>
                    <div style="font-size:11px; color:#475569; max-width:200px; text-overflow:ellipsis; overflow:hidden; white-space:nowrap;" title="${escapeHtml(r.finding_title)}">${escapeHtml(r.finding_title)}</div>
                </td>
                <td>
                    <strong style="color:#16A34A; font-size:12px;">${escapeHtml(r.proposal_code)}</strong>
                    <div style="font-size:11px; color:#334155; max-width:240px; text-overflow:ellipsis; overflow:hidden; white-space:nowrap;" title="${escapeHtml(r.proposal_text)}">${escapeHtml(r.proposal_text)}</div>
                </td>
                <td>
                    <strong style="font-size:12px; color:#0F172A;">${escapeHtml(r.area)}</strong>
                    <div style="font-size:11px; color:#64748B;">👤 ${escapeHtml(r.owner)}</div>
                </td>
                <td>${escapeHtml(r.target_date || '-')}</td>
                <td>
                    <strong style="color: ${r.days_overdue > 0 ? '#DC2626' : '#16A34A'};">
                        ${r.days_overdue > 0 ? `⚠️ ${r.days_overdue} días` : 'Al día'}
                    </strong>
                </td>
                <td>${actionCol}</td>
            </tr>
        `;
    }).join("");
}

async function validateProposal(proposalId) {
    try {
        const res = await fetch(`/api/proposals/${proposalId}/validate`, {
            method: "POST"
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(data.message || "Propuesta validada exitosamente", "success");
            await loadAllData();
            if (el("tab-tablero-ejecutivo")?.classList.contains("active")) {
                await loadExecutiveDashboard();
            }
        } else {
            showToast(data.error || "Error al validar propuesta", "error");
        }
    } catch (e) {
        console.error("Error al validar propuesta:", e);
        showToast("Error de conexión al validar propuesta", "error");
    }
}

async function validateActionPlan(planId) {
    try {
        const res = await fetch(`/api/action-plans/${planId}/validate`, {
            method: "POST"
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(data.message || "Plan de acción validado exitosamente", "success");
            await loadAllData();
            if (el("tab-tablero-ejecutivo")?.classList.contains("active")) {
                await loadExecutiveDashboard();
            }
        } else {
            showToast(data.error || "Error al validar plan de acción", "error");
        }
    } catch (e) {
        console.error("Error al validar plan de acción:", e);
        showToast("Error de conexión al validar plan de acción", "error");
    }
}

function filterDecisionTable(filterKey) {
    currentDecisionFilter = filterKey;
    renderDecisionTable(filterKey);
}

function resetDecisionTableFilter() {
    currentDecisionFilter = null;
    renderDecisionTable(null);
}

function applyKpiFilter(key, val) {
    if (key === "severity") activeFilters.risk = val;
    if (key === "status") activeFilters.status = val;
    if (key === "no_plan") activeFilters.no_plan = true;

    switchTab("hallazgos");
    filterAndRenderAll();
}

// ============================================================
// 6. INFORMES TAB & SUB-TABS (REGISTRO PADRE)
// ============================================================

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
        container.innerHTML = `<div style="text-align: center; color: #64748b; padding: 24px;">No hay informes activos. Subí tu primer informe arriba.</div>`;
        return;
    }

    container.innerHTML = reports.map(r => `
        <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; padding: 14px 18px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center;">
            <div>
                <strong style="font-size: 15px; color: #0055D4; cursor: pointer;" onclick="openReportDetail('${r.id}')">${escapeHtml(r.title)} (${r.code})</strong>
                <div style="font-size: 12px; color: #64748B; margin-top: 4px;">
                    Proceso: <strong>${escapeHtml(r.process)}</strong> · Área: <strong>${escapeHtml(r.area)}</strong> · Archivo: <strong>${escapeHtml(r.source_filename)}</strong>
                </div>
                <div style="margin-top: 8px; display: flex; gap: 12px; font-size: 11px;">
                    <span style="background: #EFF6FF; color: #0055D4; padding: 2px 8px; border-radius: 6px;"><strong>${r.findings_count || 0}</strong> hallazgos</span>
                    <span style="background: #DCFCE7; color: #16A34A; padding: 2px 8px; border-radius: 6px;"><strong>${r.proposals_count || 0}</strong> propuestas</span>
                    <span style="background: #FEF3C7; color: #D97706; padding: 2px 8px; border-radius: 6px;"><strong>${r.action_plans_count || 0}</strong> planes</span>
                </div>
            </div>
            <div style="display: flex; gap: 8px;">
                <button class="btn btn-primary" style="padding: 6px 12px; font-size: 12px;" onclick="openReportDetail('${r.id}')">Ver Detalle</button>
                <button class="btn btn-outlined" style="padding: 6px 10px; font-size: 12px;" onclick="deleteReportItem('${r.id}')">Eliminar</button>
            </div>
        </div>
    `).join("");
}

async function openReportDetail(reportId) {
    try {
        const res = await fetch(`/reports/${reportId}`);
        if (!res.ok) return;
        selectedReportDetail = await res.json();

        if (el("reportDetailTitle")) el("reportDetailTitle").textContent = `${selectedReportDetail.title} (${selectedReportDetail.code})`;
        if (el("reportDetailMeta")) el("reportDetailMeta").textContent = `Proceso: ${selectedReportDetail.process} · Auditor: ${selectedReportDetail.auditor} · Archivo: ${selectedReportDetail.source_filename}`;

        if (el("reportDetailCard")) el("reportDetailCard").style.display = "block";
        switchReportSubTab("resumen");
    } catch (err) {
        console.error("Error abriendo detalle informe:", err);
    }
}

function closeReportDetail() {
    if (el("reportDetailCard")) el("reportDetailCard").style.display = "none";
    selectedReportDetail = null;
}

function switchReportSubTab(subTabName) {
    document.querySelectorAll(".inner-tab-btn").forEach(btn => {
        btn.classList.toggle("active", btn.textContent.toLowerCase().includes(subTabName));
    });

    const box = el("reportSubTabContent");
    if (!box || !selectedReportDetail) return;

    if (subTabName === "resumen") {
        box.innerHTML = `
            <div style="font-size: 13px; line-height: 1.6;">
                <p><strong>Resumen Ejecutivo:</strong> ${escapeHtml(selectedReportDetail.summary || 'Informe procesado con éxito en AuditTrack.')}</p>
                <div style="margin-top: 12px; display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px;">
                    <div style="background:#F8FAFC; padding:12px; border-radius:8px; border:1px solid #E2E8F0;">
                        <strong>Total Hallazgos:</strong> ${(selectedReportDetail.findings || []).length}
                    </div>
                    <div style="background:#F8FAFC; padding:12px; border-radius:8px; border:1px solid #E2E8F0;">
                        <strong>Auditor a Cargo:</strong> ${escapeHtml(selectedReportDetail.auditor)}
                    </div>
                    <div style="background:#F8FAFC; padding:12px; border-radius:8px; border:1px solid #E2E8F0;">
                        <strong>Período Auditado:</strong> ${escapeHtml(selectedReportDetail.period)}
                    </div>
                </div>
            </div>
        `;
    } else if (subTabName === "hallazgos") {
        box.innerHTML = (selectedReportDetail.findings || []).map(f => `
            <div style="border-bottom:1px solid #E2E8F0; padding:10px 0;">
                <strong style="color:#0055D4;">${escapeHtml(f.code)} - ${escapeHtml(f.title)}</strong>
                <div style="font-size:12px; color:#64748B;">${escapeHtml(f.situation)}</div>
            </div>
        `).join("") || "No hay hallazgos.";
    } else {
        box.innerHTML = `<div style="font-size:12px; color:#64748B;">Visualizando ${subTabName} para ${escapeHtml(selectedReportDetail.code)}.</div>`;
    }
}

let currentPreviewData = null;

async function uploadAuditReport(file) {
    if (!file) return;
    const inputMain = el("reportInputMainTab");
    const inputHist = el("reportInput");

    showToast(`Analizando e interpretando '${file.name}' con IA Analista y Revisora...`, "info");

    const form = new FormData();
    form.append("file", file);

    try {
        const response = await fetch("/parse-preview", { method: "POST", body: form });
        let data = {};
        const contentType = response.headers.get("content-type");
        if (contentType && contentType.includes("application/json")) {
            data = await response.json();
        } else {
            const rawText = await response.text();
            data = { error: `Error del servidor (${response.status}): ${rawText.slice(0, 200)}` };
        }

        if (!response.ok) {
            showToast(data.error || "No se pudo procesar el informe.", "error");
            return;
        }

        currentPreviewData = data;
        openPreviewValidationModal(data);
    } catch (err) {
        console.error(err);
        showToast(err.message || "Error de conexión al analizar el informe.", "error");
    } finally {
        if (inputMain) inputMain.value = "";
        if (inputHist) inputHist.value = "";
    }
}

function promptUserImportDecision(analysis) {
    const cand = (analysis && analysis.candidates && analysis.candidates[0]) || {};
    const title = cand.title || "Informe existente";
    const code = cand.code || "";
    const choice = prompt(
        `El archivo contiene datos que coinciden con un informe existente en AuditTrack (${code} · ${title}).\n\n` +
        `Seleccioná la acción a realizar:\n` +
        `1. Actualizar conservando ediciones manuales en UI (Recomendado)\n` +
        `2. Sobrescribir por completo todos los registros con el archivo\n` +
        `3. Crear un nuevo informe separado`,
        "1"
    );
    if (!choice) return null;
    const modeMap = { "1": "merge", "2": "overwrite", "3": "new" };
    return {
        mode: modeMap[choice.trim()] || "merge",
        target_report_id: cand.report_id
    };
}

function openPreviewValidationModal(data) {
    const modal = el("previewValidationModal");
    const infoBox = el("previewReportInfoBox");
    const alertsBox = el("previewAlertsBox");
    const btnSave = el("btnConfirmSavePreview");
    const tbody = el("previewTableBody");
    const subtitle = el("previewModalSubtitle");
    if (!modal || !tbody) return;

    const rep = data.report || {};
    const findings = data.findings || [];
    const errors = data.errors || [];
    const warnings = data.warnings || [];
    let propCount = 0;
    findings.forEach(f => { propCount += (f.proposals || []).length; });

    if (subtitle) {
        subtitle.textContent = `Se identificaron ${findings.length} Hallazgos y ${propCount} Propuestas de Mejora. Revisá y aprobá antes de ingestar.`;
    }

    if (infoBox) {
        infoBox.innerHTML = `
            <strong>Informe:</strong> ${escapeHtml(rep.title || 'Informe sin título')} | 
            <strong>Proceso:</strong> ${escapeHtml(rep.process || 'Control Interno')} | 
            <strong>Área:</strong> <span style="background:#EFF6FF; color:#0055D4; padding:2px 6px; border-radius:4px; font-weight:600;">${escapeHtml(rep.area || 'Pendiente de definir')}</span> | 
            <strong>Auditor:</strong> ${escapeHtml(rep.auditor || 'Auditoría Interna')}
        `;
    }

    if (alertsBox) {
        let alertHtml = "";
        if (errors.length > 0) {
            alertHtml += `
                <div style="background:#FEF2F2; border:1px solid #FCA5A5; color:#991B1B; border-radius:8px; padding:10px 14px; margin-bottom:12px; font-size:12px;">
                    <strong>⚠️ Se detectaron ${errors.length} error(es) de relación o estructura en la planilla. Deben resolverse antes de poder ingestar:</strong>
                    <ul style="margin:4px 0 0 16px; padding:0;">
                        ${errors.map(e => `<li>${escapeHtml(e)}</li>`).join('')}
                    </ul>
                </div>
            `;
        }
        if (warnings.length > 0) {
            alertHtml += `
                <div style="background:#FFFBEB; border:1px solid #FCD34D; color:#92400E; border-radius:8px; padding:10px 14px; margin-bottom:12px; font-size:12px;">
                    <strong>ℹ️ Advertencias detectadas:</strong>
                    <ul style="margin:4px 0 0 16px; padding:0;">
                        ${warnings.map(w => `<li>${escapeHtml(w)}</li>`).join('')}
                    </ul>
                </div>
            `;
        }
        alertsBox.innerHTML = alertHtml;
    }

    if (btnSave) {
        if (errors.length > 0) {
            btnSave.disabled = true;
            btnSave.style.opacity = "0.5";
            btnSave.style.cursor = "not-allowed";
            btnSave.title = "Resolvé los errores indicados antes de ingestar";
        } else {
            btnSave.disabled = false;
            btnSave.style.opacity = "1";
            btnSave.style.cursor = "pointer";
            btnSave.title = "";
        }
    }

    let html = "";
    findings.forEach((f, fIdx) => {
        html += `
            <tr>
                <td><span class="pill pill-hallazgo">Hallazgo</span></td>
                <td>
                    <textarea class="preview-edit-text" style="width:100%; font-size:12px; border:1px solid #CBD5E1; border-radius:4px; padding:4px;" rows="2" onchange="updatePreviewFindingText(${fIdx}, this.value)">${escapeHtml(f.situation || f.title)}</textarea>
                </td>
                <td>
                    <input type="text" style="width:100%; font-size:12px; border:1px solid #CBD5E1; border-radius:4px; padding:4px;" value="${escapeHtml(f.responsible_area || rep.area || 'Pendiente de definir')}" onchange="updatePreviewFindingArea(${fIdx}, this.value)">
                </td>
                <td>
                    <select style="font-size:12px; border:1px solid #CBD5E1; border-radius:4px; padding:4px;" onchange="updatePreviewFindingSeverity(${fIdx}, this.value)">
                        <option value="Alto"${f.severity === 'Alto' ? ' selected' : ''}>Alto</option>
                        <option value="Medio"${f.severity === 'Medio' ? ' selected' : ''}>Medio</option>
                        <option value="Bajo"${f.severity === 'Bajo' ? ' selected' : ''}>Bajo</option>
                    </select>
                </td>
                <td><small style="color:#64748B;">Hallazgo Principal</small></td>
                <td>
                    <button type="button" class="btn btn-outlined" style="padding:2px 6px; font-size:11px; color:#DC2626;" onclick="deletePreviewFinding(${fIdx})">Eliminar</button>
                </td>
            </tr>
        `;

        (f.proposals || []).forEach((p, pIdx) => {
            html += `
                <tr style="background:#F8FAFC;">
                    <td style="padding-left:18px;"><span class="pill pill-propuesta">Propuesta</span></td>
                    <td>
                        <textarea class="preview-edit-text" style="width:100%; font-size:12px; border:1px solid #CBD5E1; border-radius:4px; padding:4px;" rows="2" onchange="updatePreviewProposalText(${fIdx}, ${pIdx}, this.value)">${escapeHtml(p.proposal_text || p.title)}</textarea>
                    </td>
                    <td><small style="color:#64748B;">(Idem Hallazgo)</small></td>
                    <td><small style="color:#64748B;">-</small></td>
                    <td><strong style="color:#0055D4;">${escapeHtml(f.code || `H-2026-${fIdx+1}`)}</strong></td>
                    <td>
                        <button type="button" class="btn btn-outlined" style="padding:2px 6px; font-size:11px; color:#DC2626;" onclick="deletePreviewProposal(${fIdx}, ${pIdx})">Eliminar</button>
                    </td>
                </tr>
            `;
        });
    });

    tbody.innerHTML = html || `<tr><td colspan="6" style="text-align:center; color:#94A3B8; padding:20px;">No hay registros cargados.</td></tr>`;
    modal.style.display = "flex";
}

function closePreviewValidationModal() {
    const modal = el("previewValidationModal");
    if (modal) modal.style.display = "none";
    currentPreviewData = null;
}

function updatePreviewFindingText(fIdx, val) {
    if (currentPreviewData && currentPreviewData.findings[fIdx]) {
        currentPreviewData.findings[fIdx].situation = val;
    }
}

function updatePreviewFindingArea(fIdx, val) {
    if (currentPreviewData && currentPreviewData.findings[fIdx]) {
        currentPreviewData.findings[fIdx].responsible_area = val;
    }
}

function updatePreviewFindingSeverity(fIdx, val) {
    if (currentPreviewData && currentPreviewData.findings[fIdx]) {
        currentPreviewData.findings[fIdx].severity = val;
    }
}

function updatePreviewProposalText(fIdx, pIdx, val) {
    if (currentPreviewData && currentPreviewData.findings[fIdx] && currentPreviewData.findings[fIdx].proposals[pIdx]) {
        currentPreviewData.findings[fIdx].proposals[pIdx].proposal_text = val;
    }
}

function deletePreviewFinding(fIdx) {
    if (currentPreviewData) {
        currentPreviewData.findings.splice(fIdx, 1);
        openPreviewValidationModal(currentPreviewData);
    }
}

function deletePreviewProposal(fIdx, pIdx) {
    if (currentPreviewData && currentPreviewData.findings[fIdx]) {
        currentPreviewData.findings[fIdx].proposals.splice(pIdx, 1);
        openPreviewValidationModal(currentPreviewData);
    }
}

async function confirmSaveValidatedReport(overrideMode = null, overrideTargetId = null) {
    if (!currentPreviewData) return;

    if (currentPreviewData.errors && currentPreviewData.errors.length > 0) {
        showToast("Error: No se puede guardar. Resolvé los errores de relación antes de continuar.", "error");
        return;
    }

    const payload = {
        ...currentPreviewData,
        mode: overrideMode || currentPreviewData.mode || null,
        target_report_id: overrideTargetId || currentPreviewData.target_report_id || null
    };

    try {
        showToast("Ingestando informe validado en AuditTrack...", "info");
        const res = await fetch("/save-validated-report", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        const data = await res.json();

        if (res.status === 409 && data.requires_decision) {
            const decision = promptUserImportDecision(data.analysis);
            if (!decision) return;
            return confirmSaveValidatedReport(decision.mode, decision.target_report_id);
        }

        if (!res.ok) throw new Error(data.error || "Error al guardar el informe.");

        showToast(data.message || "Informe ingresado exitosamente en AuditTrack.", "success");
        closePreviewValidationModal();
        await loadAllData();
        switchTab("hallazgos");
    } catch (err) {
        console.error(err);
        showToast(err.message || "Error al ingresar el informe.", "error");
    }
}


async function deleteReportItem(reportId) {
    if (!confirm("¿Eliminar este informe y todas sus propuestas y planes asociados?")) return;
    try {
        const response = await fetch(`/reports/${reportId}`, { method: "DELETE" });
        if (response.ok) {
            showToast("Informe eliminado.", "success");
            loadAllData();
        }
    } catch (err) {
        console.error(err);
    }
}

async function deleteFindingItem(findingId) {
    if (!findingId) return;
    if (!confirm("¿Eliminar este hallazgo y sus propuestas y planes asociados?")) return;
    try {
        const response = await fetch(`/findings/${findingId}`, { method: "DELETE" });
        if (response.ok) {
            showToast("Hallazgo eliminado.", "success");
            closeFindingDrawer();
            await loadAllData();
        } else {
            showToast("Error al eliminar hallazgo.", "error");
        }
    } catch (err) {
        console.error("Error eliminando hallazgo:", err);
        showToast("Error de conexión al eliminar hallazgo.", "error");
    }
}

function deleteCurrentDrawerFinding() {
    if (currentDrawerFindingId) {
        deleteFindingItem(currentDrawerFindingId);
    }
}

// ============================================================
// 7. DETALLE DEL HALLAZGO (DRAWER SLIDE-OVER CON EDICIÓN INTERACTIVA)
// ============================================================

let currentDrawerFindingId = null;

async function openFindingDrawer(findingId) {
    try {
        const res = await fetch(`/findings/${findingId}`);
        if (!res.ok) return;
        const f = await res.json();
        currentDrawerFindingId = f.id;

        if (el("drawerCodeTitle")) el("drawerCodeTitle").textContent = f.code;
        if (el("drawerInputTitle")) el("drawerInputTitle").value = f.title || "";
        if (el("drawerSelectRisk")) el("drawerSelectRisk").value = f.severity || "Medio";
        if (el("drawerSelectStatus")) el("drawerSelectStatus").value = f.status || "En proceso";
        if (el("drawerInputArea")) el("drawerInputArea").value = f.responsible_area || "";
        if (el("drawerInputOwner")) el("drawerInputOwner").value = f.action_owner || "";
        if (el("drawerTextSituation")) el("drawerTextSituation").value = f.situation || f.title || "";
        if (el("drawerTextObservations")) el("drawerTextObservations").value = f.observations || "";

        if (el("drawerRiskPill")) {
            el("drawerRiskPill").textContent = f.severity || "Medio";
            el("drawerRiskPill").className = `pill pill-${(f.severity||'medio').toLowerCase()}`;
        }
        if (el("drawerStatusPill")) {
            const st = f.status || "En proceso";
            el("drawerStatusPill").textContent = st;
            el("drawerStatusPill").className = `pill pill-${st.toLowerCase().replace(/\s+/g, '-')}`;
        }

        if (el("drawerReportName")) el("drawerReportName").textContent = `${f.report_title || ''} (${f.report_code || ''})`;
        if (el("drawerFileName")) el("drawerFileName").textContent = f.source_filename || "Informe.xlsx";

        // Propuestas vinculadas (Editables desde el panel de edición)
        const propBox = el("drawerProposalsList");
        if (propBox) {
            const props = f.proposals || [];
            let propHtml = "";
            if (props.length > 0) {
                props.forEach((p) => {
                    propHtml += `
                        <div style="background:#EFF6FF; border:1px solid #BFDBFE; border-radius:8px; padding:10px; margin-bottom:8px;">
                            <label style="font-size:11px; font-weight:600; color:#0055D4; display:block; margin-bottom:4px;">💡 ${escapeHtml(p.code)} - Propuesta de Mejora ✏️</label>
                            <textarea class="drawer-prop-input" data-prop-id="${p.id}" rows="2" style="width:100%; padding:6px; border-radius:4px; border:1px solid #CBD5E1; font-size:12px;">${escapeHtml(p.proposal_text || p.title)}</textarea>
                        </div>
                    `;
                });
            }
            propHtml += `
                <div style="background:#F8FAFC; border:1px dashed #CBD5E1; border-radius:8px; padding:10px; margin-top:8px;">
                    <label style="font-size:11px; font-weight:600; color:#475569; display:block; margin-bottom:4px;">➕ Agregar Nueva Propuesta de Mejora ✏️</label>
                    <textarea id="drawerNewProposalText" rows="2" style="width:100%; padding:6px; border-radius:4px; border:1px solid #CBD5E1; font-size:12px;" placeholder="Escribir nueva recomendación o propuesta de mejora..."></textarea>
                </div>
            `;
            propBox.innerHTML = propHtml;
        }

        // Planes de acción vinculados
        const plansBox = el("drawerActionPlansList");
        if (plansBox) {
            let allPlans = [];
            (f.proposals || []).forEach(p => {
                allPlans = allPlans.concat(p.action_plans || []);
            });

            if (!allPlans.length) {
                plansBox.innerHTML = `<div style="font-size:12px; color:#94A3B8;">Sin planes de acción asignados.</div>`;
            } else {
                plansBox.innerHTML = allPlans.map(pa => `
                    <div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px; padding:10px; margin-bottom:8px; font-size:12px;">
                        <strong>📋 ${pa.code}:</strong> ${escapeHtml(pa.action_text || pa.title)}
                        <div style="font-size:11px; color:#64748B; margin-top:4px;">
                            Responsable: <strong>${escapeHtml(pa.action_owner)}</strong> · Fecha: <strong>${pa.target_date}</strong> · Avance: <strong>${pa.progress_pct}%</strong>
                        </div>
                    </div>
                `).join("");
            }
        }

        // Evidencias
        const evBox = el("drawerEvidenceList");
        if (evBox) {
            const evs = f.evidence_files || [];
            if (!evs.length) {
                evBox.innerHTML = `<div style="font-size:12px; color:#94A3B8;">No se han adjuntado evidencias de cierre aún.</div>`;
            } else {
                evBox.innerHTML = evs.map(e => `
                    <div style="font-size:12px; color:#0F172A;">📎 <strong>${escapeHtml(e.filename)}</strong> (${e.plan_code})</div>
                `).join("");
            }
        }

        // Historial
        const histBox = el("drawerHistoryList");
        if (histBox) {
            const logs = f.history || [];
            if (!logs.length) {
                histBox.innerHTML = `<div style="font-size:12px; color:#94A3B8;">Sin registros de historial.</div>`;
            } else {
                histBox.innerHTML = logs.map(l => `
                    <div class="history-item">
                        <div><strong>${escapeHtml(l.description)}</strong></div>
                        <div class="history-date">${escapeHtml(l.change_date)} · por <strong>${escapeHtml(l.user_name)}</strong></div>
                    </div>
                `).join("");
            }
        }

        if (el("drawerFindingDetail")) el("drawerFindingDetail").style.display = "flex";
    } catch (err) {
        console.error("Error cargando detalle hallazgo drawer:", err);
    }
}

function closeFindingDrawer() {
    if (el("drawerFindingDetail")) el("drawerFindingDetail").style.display = "none";
    currentDrawerFindingId = null;
}

function closeDrawerAndGoToProposals() {
    closeFindingDrawer();
    closeActionPlanModal();
    switchTab('propuestas');
}

async function saveFindingFromDrawer() {
    if (!currentDrawerFindingId) return;

    const title = el("drawerInputTitle")?.value;
    const severity = el("drawerSelectRisk")?.value;
    const status = el("drawerSelectStatus")?.value;
    const responsible_area = el("drawerInputArea")?.value;
    const action_owner = el("drawerInputOwner")?.value;
    const situation = el("drawerTextSituation")?.value;
    const observations = el("drawerTextObservations")?.value;

    try {
        let allOk = true;
        const res = await fetch(`/findings/${currentDrawerFindingId}/update`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                title,
                severity,
                status,
                responsible_area,
                action_owner,
                situation,
                observations
            })
        });
        if (!res.ok) allOk = false;

        // Guardar cambios en las propuestas existentes
        const propInputs = document.querySelectorAll(".drawer-prop-input");
        for (const input of propInputs) {
            const propId = input.dataset.propId;
            const newText = input.value.trim();
            if (propId && newText) {
                const pres = await fetch(`/proposals/${propId}/update`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ proposal_text: newText, title: newText })
                });
                if (!pres.ok) allOk = false;
            }
        }

        // Agregar nueva propuesta si fue ingresada
        const newPropInput = el("drawerNewProposalText");
        if (newPropInput && newPropInput.value.trim()) {
            const nres = await fetch(`/findings/${currentDrawerFindingId}/add-proposal`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ proposal_text: newPropInput.value.trim() })
            });
            if (!nres.ok) allOk = false;
        }

        if (allOk) {
            showToast("Cambios guardados correctamente en AuditTrack.", "success");
            closeFindingDrawer();
            await loadAllData();
            await loadExecutiveDashboard();
        } else {
            showToast("Error al guardar cambios del hallazgo en el servidor.", "error");
            await loadAllData();
        }
    } catch (e) {
        console.error(e);
        showToast("Error de conexión al guardar cambios.", "error");
        await loadAllData();
    }
}

function updateSidebarMetrics() {
    const openCountEl = el("sidebarOpenCount");
    const progressBarEl = el("sidebarProgressBar");
    const pctEl = el("sidebarPct");

    const totalF = currentFindings.length;
    if (totalF === 0) {
        if (openCountEl) openCountEl.textContent = 0;
        if (progressBarEl) progressBarEl.style.width = `0%`;
        if (pctEl) pctEl.textContent = `0%`;
        return;
    }

    const openCount = currentFindings.filter(i => !isFinalized(i)).length;

    let totalPlans = currentActionPlans.length;
    let completedPlans = currentActionPlans.filter(isFinalized).length;
    let pct = totalPlans > 0 ? Math.round((completedPlans / totalPlans) * 100) : 0;

    if (openCountEl) openCountEl.textContent = openCount;
    if (progressBarEl) progressBarEl.style.width = `${pct}%`;
    if (pctEl) pctEl.textContent = `${pct}%`;
}

// ============================================================
// AUTHENTICATION & ROLE-BASED ACCESS CONTROL (RBAC)
// ============================================================

window.currentUser = null;
window.currentUserRole = "Validador";

async function initUserSession() {
    try {
        const res = await fetch("/api/user");
        if (res.ok) {
            const data = await res.json();
            if (data.success && data.user) {
                window.currentUser = data.user;
                window.currentUserRole = data.user.role || "Validador";
                updateUserHeaderUI(data.user);
            }
        }
    } catch (e) {
        console.error("Error cargando usuario:", e);
    }
    applyRolePermissions();
}

function updateUserHeaderUI(user) {
    if (!user) return;
    const nameEl = el("userName");
    const roleBadge = el("userRoleBadge");
    const avatarEl = el("userAvatar");
    const selectEl = el("roleSwitcherSelect");

    if (nameEl) nameEl.textContent = user.name || user.username;
    if (roleBadge) {
        roleBadge.textContent = user.role;
        roleBadge.className = user.role === "Validador" ? "pill pill-alto" : user.role === "Editor" ? "pill pill-en-proceso" : "pill pill-bajo";
    }
    if (avatarEl) {
        const initials = (user.name || user.username || "U").split(" ").map(w => w[0]).join("").toUpperCase().slice(0, 2);
        avatarEl.textContent = initials || "LG";
    }
    if (selectEl) {
        selectEl.value = user.username || "admin";
    }
}

async function switchUserRole(usernameKey) {
    const passwords = {
        "admin": "audit2026admin",
        "editor": "audit2026editor",
        "lector": "audit2026reader",
        "luciana": "audit2026admin"
    };
    const pwd = passwords[usernameKey] || "audit2026admin";
    try {
        const res = await fetch("/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: usernameKey, password: pwd })
        });
        if (res.ok) {
            const data = await res.json();
            if (data.success && data.user) {
                window.currentUser = data.user;
                window.currentUserRole = data.user.role;
                updateUserHeaderUI(data.user);
                applyRolePermissions();
                showToast(`Rol cambiado a: ${data.user.role} (${data.user.name})`, "success");
            }
        } else {
            showToast("Error al cambiar de rol.", "error");
        }
    } catch (e) {
        console.error("Error al cambiar rol:", e);
    }
}

async function logoutCurrentSession() {
    await fetch("/logout", { method: "POST" });
    window.location.reload();
}

function applyRolePermissions() {
    const role = window.currentUserRole || "Validador";
    const isReader = role === "Consulta";
    const isValidator = role === "Validador";

    // Botones de edición / carga
    const uploadBtn = document.querySelector('button[onclick*="reportInputMainTab"]');
    if (uploadBtn) uploadBtn.style.display = isReader ? "none" : "inline-flex";

    const createPlanBtn = document.querySelector('button[onclick*="openNewActionPlanModal"]');
    if (createPlanBtn) createPlanBtn.style.display = isReader ? "none" : "inline-flex";

    const drawerSaveBtn = document.querySelector('button[onclick*="saveFindingFromDrawer"]');
    if (drawerSaveBtn) drawerSaveBtn.style.display = isReader ? "none" : "block";

    // Visibilidad de acciones de eliminación (requieren Validador)
    document.querySelectorAll(".btn-delete-report, .btn-delete-finding").forEach(btn => {
        btn.style.display = isValidator ? "inline-block" : "none";
    });
}

// Initialize on page load
document.addEventListener("DOMContentLoaded", () => {
    // This is a findings keyword filter, NOT a username field.
    // Never apply browser-restored credentials as a hidden filter on page load.
    const keywordFilter = el("globalSearchInput");
    if (keywordFilter) keywordFilter.value = "";
    activeFilters.search = "";
    switchTab("hallazgos");
    initUserSession();
    syncDataWithServer(true);

    // Auto-synchronize with Supabase every 10 seconds when tab is active
    setInterval(() => {
        if (document.visibilityState === "visible") {
            syncDataWithServer(true);
        }
    }, 10000);

    // Auto-synchronize whenever user returns to or focuses the window/tab
    window.addEventListener("focus", () => {
        syncDataWithServer(true);
    });
});
