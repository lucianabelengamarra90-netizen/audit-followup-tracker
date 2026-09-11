// ============================================================
// AUDITTRACK - FRONTEND CONTROLLER (ESTRUCTURA RELACIONAL INTEGRADA)
// ============================================================

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

// ============================================================
// TAB NAVIGATION
// ============================================================

function switchTab(tabName) {
    document.querySelectorAll(".topnav-tab").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.tab === tabName);
    });

    document.querySelectorAll(".tab-pane").forEach(pane => {
        pane.classList.toggle("active", pane.id === `tab-${tabName}`);
    });

    if (tabName === "informes") loadReports();
    if (tabName === "hallazgos") renderAuditTrackTable(currentFindings);
    if (tabName === "propuestas") renderProposalsTab();
    if (tabName === "planes") renderActionPlansTab();
    if (tabName === "tableros") loadDashboardTab();
    if (tabName === "indicadores") loadKpiIndicatorsTab();
}

// ============================================================
// DATA LOADING
// ============================================================

async function loadAllData() {
    try {
        const [resF, resP, resPA, resR] = await Promise.all([
            fetch("/findings"),
            fetch("/proposals"),
            fetch("/action-plans"),
            fetch("/reports")
        ]);

        if (resF.ok) currentFindings = (await resF.json()).findings || [];
        if (resP.ok) currentProposals = (await resP.json()).proposals || [];
        if (resPA.ok) currentActionPlans = (await resPA.json()).action_plans || [];
        if (resR.ok) currentReports = (await resR.json()).reports || [];

        populateFilterDropdowns();
        renderAuditTrackTable(currentFindings);
        renderProposalsTab();
        renderActionPlansTab();
        updateSidebarMetrics();
        loadNotifications();
    } catch (err) {
        console.error("Error cargando estructura relacional de AuditTrack:", err);
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
    if (activeFilters.status) {
        filteredF = filteredF.filter(i => (i.status || "").toLowerCase() === activeFilters.status.toLowerCase());
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
        filteredF = filteredF.filter(i => (i.action_plans_count || 0) === 0);
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

    if (countSpan) {
        countSpan.textContent = `Mostrando ${items.length} de ${currentFindings.length} hallazgos`;
    }

    if (!items.length) {
        tbody.innerHTML = `<tr><td colspan="13" style="text-align: center; color: #64748b; padding: 36px;">No hay hallazgos con los filtros aplicados. Cargar informe arriba.</td></tr>`;
        return;
    }

    let html = "";
    items.forEach((item) => {
        const filename = item.source_filename || "Informe.xlsx";
        const proposalsToRender = (item.proposals && item.proposals.length > 0) ? item.proposals : [null];

        proposalsToRender.forEach((prop) => {
            const propCodeCell = prop
                ? `<a href="#" class="id-cell" style="color: #16A34A; font-weight:700;" onclick="openFindingDrawer('${item.id}'); return false;">${escapeHtml(prop.code)}</a>`
                : `<span style="color:#94A3B8; font-size:11px;">Sin propuesta</span>`;

            const propText = prop ? (prop.proposal_text || prop.title) : "";
            let firstAction = (prop && prop.action_plans && prop.action_plans.length > 0) ? prop.action_plans[0] : null;
            let owner = (prop && prop.action_owner) ? prop.action_owner : (firstAction ? firstAction.action_owner : (item.action_owner || "Sin asignar"));
            let targetDate = (prop && prop.target_date) ? prop.target_date : (firstAction ? firstAction.target_date : "");
            let status = (prop && prop.status) ? prop.status : (item.status || "Pendiente");
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
                ${statusOptions.map(s => `<option value="${s}" ${s === status ? 'selected' : ''}>${s}</option>`).join('')}
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
        } else {
            showToast("Error al actualizar riesgo", "error");
        }
    } catch (e) {
        showToast("Error de conexión", "error");
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
        } else {
            showToast("Error al actualizar", "error");
        }
    } catch (e) {
        showToast("Error de conexión", "error");
    }
}

async function inlineUpdateStatus(el) {
    const findingId = el.dataset.findingId;
    const proposalId = el.dataset.proposalId;
    const value = el.value;

    try {
        // Update finding status
        await fetch(`/findings/${findingId}/update`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: value })
        });
        // Also update linked proposal status
        if (proposalId) {
            await fetch(`/proposals/${proposalId}/update`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ status: value })
            });
        }
        showToast(`Estado actualizado a "${value}"`, "success");
    } catch (e) {
        showToast("Error al actualizar estado", "error");
    }
}

async function inlineUpdateOwner(el) {
    const findingId = el.dataset.findingId;
    const proposalId = el.dataset.proposalId;
    const value = el.value;

    try {
        await fetch(`/findings/${findingId}/update`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action_owner: value })
        });
        if (proposalId) {
            await fetch(`/proposals/${proposalId}/update`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action_owner: value })
            });
        }
        showToast("Responsable actualizado", "success");
    } catch (e) {
        showToast("Error al actualizar responsable", "error");
    }
}

async function inlineUpdateDate(el) {
    const findingId = el.dataset.findingId;
    const proposalId = el.dataset.proposalId;
    const value = el.value;

    try {
        if (proposalId) {
            await fetch(`/proposals/${proposalId}/update`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ target_date: value })
            });
        }
        showToast("Fecha actualizada", "success");
    } catch (e) {
        showToast("Error al actualizar fecha", "error");
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
let proposalFilters = {
    report_id: "",
    area: "",
    status: "",
    risk: ""
};

function switchProposalView(mode) {
    currentProposalViewMode = mode;
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
    renderProposalsTab();
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

function renderProposalsTab() {
    const tbody = el("proposalsTableBody");
    if (!tbody) return;

    populateProposalFilterDropdowns();

    const total = currentProposals.length;
    const noPlan = currentProposals.filter(p => (p.action_plans_count || 0) === 0).length;
    const inProcess = currentProposals.filter(p => (p.status || "").toLowerCase() === "en proceso").length;
    const completed = currentProposals.filter(p => ["finalizado", "completada", "implementada", "archivada"].includes((p.status || "").toLowerCase())).length;

    if (el("propKpiTotal")) el("propKpiTotal").textContent = total;
    if (el("propKpiNoPlan")) el("propKpiNoPlan").textContent = noPlan;
    if (el("propKpiInProcess")) el("propKpiInProcess").textContent = inProcess;
    if (el("propKpiCompleted")) el("propKpiCompleted").textContent = completed;

    // Filter proposals based on active filters & mode
    let filtered = currentProposals.filter(p => {
        if (currentProposalViewMode === "repo") {
            const isFinished = ["finalizado", "completada", "implementada", "archivada"].includes((p.status || "").toLowerCase());
            if (!isFinished) return false;
        }
        if (proposalFilters.report_id && p.report_id !== proposalFilters.report_id) return false;
        if (proposalFilters.area && p.responsible_area !== proposalFilters.area) return false;
        if (proposalFilters.status && (p.status || "").toLowerCase() !== proposalFilters.status.toLowerCase()) return false;
        return true;
    });

    if (!filtered.length) {
        const msg = currentProposalViewMode === "repo"
            ? "No hay propuestas archivadas en el <strong>Repositorio Plan 2026</strong>."
            : "No hay propuestas registradas con los filtros aplicados.";
        tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: #64748b; padding: 36px;">${msg}</td></tr>`;
        return;
    }

    tbody.innerHTML = filtered.map(p => {
        const isArchived = ["finalizado", "completada", "implementada", "archivada"].includes((p.status || "").toLowerCase());
        const statusClass = (p.status || "en-proceso").toLowerCase().replace(/\s+/g, '-');

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
                <td><span class="pill pill-${statusClass}">${escapeHtml(p.status || 'En proceso')}</span></td>
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

    tbody.innerHTML = currentActionPlans.map(pa => `
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
            <td><span class="pill pill-${(pa.status||'en-proceso').toLowerCase()}">${escapeHtml(pa.status)}</span></td>
            <td>${pa.evidence_file ? `📎 <small>${escapeHtml(pa.evidence_file)}</small>` : `<span style="color:#94A3B8;">Sin evidencia</span>`}</td>
            <td>
                <button class="btn btn-outlined" style="padding: 3px 8px; font-size: 11px;" onclick="openUpdatePlanModal('${pa.id}')">⚙️ Actualizar</button>
            </td>
        </tr>
    `).join("");
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

function openUpdatePlanModal(planId) {
    const plan = currentActionPlans.find(p => p.id === planId);
    if (!plan) return;

    const newPct = prompt(`Actualizar porcentaje de avance para ${plan.code} (0-100):`, plan.progress_pct || 0);
    if (newPct === null) return;

    const newStatus = prompt(`Actualizar estado (En proceso / Pendiente / Completada):`, plan.status || "En proceso");
    if (!newStatus) return;

    fetch(`/action-plans/${plan.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ progress_pct: parseInt(newPct) || 0, status: newStatus })
    }).then(res => {
        if (res.ok) {
            showToast("Plan actualizado.", "success");
            loadAllData();
        }
    });
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
// 5. INDICADORES TAB (KPIs DE AUDITORÍA CON SEMÁFOROS Y FILTROS)
// ============================================================

async function loadKpiIndicatorsTab() {
    try {
        const res = await fetch("/kpi-indicators");
        if (!res.ok) return;
        currentKpiIndicators = (await res.json()).indicators || [];
        renderKpiIndicatorsTable(currentKpiIndicators);
    } catch (err) {
        console.error("Error cargando indicadores:", err);
    }
}

function renderKpiIndicatorsTable(indicators) {
    const tbody = el("kpiTableBody");
    if (!tbody) return;

    tbody.innerHTML = indicators.map(kpi => {
        const colorClass = kpi.status === "Verde" ? "semaforo-verde" : kpi.status === "Amarillo" ? "semaforo-amarillo" : "semaforo-rojo";
        return `
            <tr>
                <td><strong>${escapeHtml(kpi.name)}</strong></td>
                <td style="font-size: 16px; font-weight: 700; color: #0F172A;">${escapeHtml(kpi.value)}</td>
                <td><span style="font-size: 12px; color: #64748B;">${escapeHtml(kpi.target)}</span></td>
                <td><span class="semaforo-badge ${colorClass}">● ${escapeHtml(kpi.status)}</span></td>
                <td>
                    <button class="btn btn-outlined" style="padding: 4px 10px; font-size: 11px;" onclick="applyKpiFilter('${kpi.filter_key}', '${kpi.filter_val}')">👁️ Ver Registros</button>
                </td>
            </tr>
        `;
    }).join("");
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

function openPreviewValidationModal(data) {
    const modal = el("previewValidationModal");
    const infoBox = el("previewReportInfoBox");
    const tbody = el("previewTableBody");
    const subtitle = el("previewModalSubtitle");
    if (!modal || !tbody) return;

    const rep = data.report || {};
    const findings = data.findings || [];
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

async function confirmSaveValidatedReport() {
    if (!currentPreviewData) return;

    try {
        showToast("Ingestando informe validado en AuditTrack...", "info");
        const res = await fetch("/save-validated-report", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(currentPreviewData)
        });

        const data = await res.json();
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

        // Guardar cambios en las propuestas existentes
        const propInputs = document.querySelectorAll(".drawer-prop-input");
        for (const input of propInputs) {
            const propId = input.dataset.propId;
            const newText = input.value.trim();
            if (propId && newText) {
                await fetch(`/proposals/${propId}/update`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ proposal_text: newText, title: newText })
                });
            }
        }

        // Agregar nueva propuesta si fue ingresada
        const newPropInput = el("drawerNewProposalText");
        if (newPropInput && newPropInput.value.trim()) {
            await fetch(`/findings/${currentDrawerFindingId}/add-proposal`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ proposal_text: newPropInput.value.trim() })
            });
        }

        if (res.ok) {
            showToast("Cambios guardados correctamente en AuditTrack.", "success");
            closeFindingDrawer();
            await loadAllData();
        } else {
            showToast("Error al guardar cambios del hallazgo.", "error");
        }
    } catch (e) {
        console.error(e);
        showToast("Error de conexión al guardar cambios.", "error");
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

    const openCount = currentFindings.filter(i => (i.status || "").toLowerCase() !== "completada").length;

    let totalPlans = currentActionPlans.length;
    let completedPlans = currentActionPlans.filter(pa => ["completada", "completado"].includes((pa.status||"").toLowerCase())).length;
    let pct = totalPlans > 0 ? Math.round((completedPlans / totalPlans) * 100) : 0;

    if (openCountEl) openCountEl.textContent = openCount;
    if (progressBarEl) progressBarEl.style.width = `${pct}%`;
    if (pctEl) pctEl.textContent = `${pct}%`;
}

// Initialize on page load
document.addEventListener("DOMContentLoaded", () => {
    switchTab("informes");
    loadAllData();
});
