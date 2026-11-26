/* ==========================================================================
   TARRANT ADVISORS — CLIENT INTAKE & PROFILE HUB JS
   ========================================================================== */

let customerCatalog = {};
let currentMode = "lead"; // Modes: "lead", "heal", "deactivate", "demographics"

// Demographics directory state
let demoSortField = "display_name";
let demoSortDir = "asc";
let expandedDrawerIds = new Set();
let demoCurrentPage = 1;
let demoPageSize = 50; // Numeric page size or "all"

const ENTITY_LABELS = {
    individual: "Individual / Sole Prop",
    sm_llc: "Single-Member LLC",
    s_corp: "S-Corporation",
    partnership: "Partnership / MM-LLC",
    c_corp: "C-Corporation",
    non_profit: "Non-Profit",
    trust: "Trust / Estate",
    organization: "Organization"
};

// Environment detection
const urlParams = new URLSearchParams(window.location.search);
const isSandbox = urlParams.get("sandbox") === "true";
const CGI_SUFFIX = isSandbox ? "?sandbox=true" : "";

const LOOKUP_CGI_URL = `/cgi/intake_lookup.cgi${CGI_SUFFIX}`;
const PROVISION_CGI_URL = `/cgi/intake_provision.cgi${CGI_SUFFIX}`;

document.addEventListener("DOMContentLoaded", () => {
    if (isSandbox) {
        const sandboxEl = document.getElementById("sandbox");
        if (sandboxEl) {
            sandboxEl.innerHTML = ' <span class="sandbox-badge">***SANDBOX***</span>';
        }

        const engagementLink = document.getElementById("engagement-pipeline");
        if (engagementLink) {
            engagementLink.href = "/cgi/engagement_sandbox.cgi";
        }

        const consentLink = document.getElementById("consent-pipeline");
        if (consentLink) {
            consentLink.href = "/cgi/consent_sandbox.cgi";
        }
    }

    // Initialize mode setup (also sets today's default contact_date)
    setMode("lead");

    // Fetch existing QBO customers on load
    loadCustomerCatalog();
});

/**
 * Escape HTML special characters for safe DOM rendering
 */
function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

/**
 * Fetch QBO customer catalog from intake_lookup.cgi
 */
async function loadCustomerCatalog() {
    try {
        const response = await fetch(LOOKUP_CGI_URL, { method: "GET" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data = await response.json();
        if (data.status === "success" && data.customers) {
            customerCatalog = data.customers;
            populateDatalist();
            renderDemographicsTable();
        } else {
            console.warn("Could not load customer catalog:", data.message);
        }
    } catch (err) {
        console.error("Error fetching QBO catalog:", err);
        const tbody = document.getElementById("demographics-tbody");
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="7" class="demo-empty-state">Error loading QBO customer roster. Check server logs.</td></tr>`;
        }
    }
}

/**
 * Populate datalist options for client lookup
 */
function populateDatalist() {
    const datalist = document.getElementById("client-select-options");
    if (!datalist) return;

    datalist.innerHTML = "";
    Object.keys(customerCatalog).sort().forEach(catalogKey => {
        const option = document.createElement("option");
        option.value = catalogKey;
        datalist.appendChild(option);
    });
}

/**
 * Toggle form fields read-only / disabled state for Deactivate mode
 */
function setFieldsReadOnly(isReadOnly) {
    const inputIds = [
        "friendly_name", "contact_date", "primary_email", "phone_number",
        "co_signer_name", "co_signer_email", "responder", "street", "city",
        "state", "zip", "notes"
    ];

    inputIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.readOnly = isReadOnly;
        }
    });

    ["entity_type", "delivery_format"].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.disabled = isReadOnly;
        }
    });
}

/**
 * Switch mode between "lead", "heal", "deactivate", and "demographics"
 * @param {string} mode - Target mode ("lead", "heal", "deactivate", "demographics")
 * @param {boolean} preserveData - If true, keeps existing form values intact (used when cloning a client)
 */
function setMode(mode, preserveData = false) {
    currentMode = mode;

    const wrapperEl = document.getElementById("main-wrapper");
    const btnLead = document.getElementById("btn-mode-lead");
    const btnHeal = document.getElementById("btn-mode-heal");
    const btnDeactivate = document.getElementById("btn-mode-deactivate");
    const btnDemographics = document.getElementById("btn-mode-demographics");
    const submitBtn = document.getElementById("btn-submit");
    const clientInput = document.getElementById("client-select-input");
    const notesSection = document.getElementById("section-notes");
    const formSections = document.querySelectorAll("#intake-form .form-section");
    const badge = document.getElementById("client-lead-badge");
    const formEl = document.getElementById("intake-form");
    const demoView = document.getElementById("demographics-view");

    // Reset tab button active states
    [btnLead, btnHeal, btnDeactivate, btnDemographics].forEach(btn => btn && btn.classList.remove("active"));

    // Handle Demographics directory tab separately from the intake form
    if (mode === "demographics") {
        if (btnDemographics) btnDemographics.classList.add("active");
        if (formEl) formEl.classList.add("hidden");
        if (demoView) demoView.classList.remove("hidden");
        if (wrapperEl) wrapperEl.classList.add("demographics-wide");
        renderDemographicsTable();
        return;
    }

    // Restore intake form visibility and standard width for form modes
    if (formEl) formEl.classList.remove("hidden");
    if (demoView) demoView.classList.add("hidden");
    if (wrapperEl) wrapperEl.classList.remove("demographics-wide");

    // 1. Reset form fields unless preserving cloned data
    if (!preserveData) {
        if (formEl) formEl.reset();
        clearClientFields();

        const contactDateInput = document.getElementById("contact_date");
        if (contactDateInput) {
            contactDateInput.value = new Date().toISOString().split("T")[0];
        }
    }

    formSections.forEach(section => section.classList.remove("heal-mode", "deactivate-mode"));

    if (submitBtn) submitBtn.disabled = false;

    // 2. Apply mode-specific UI & input rules
    if (mode === "deactivate") {
        if (btnDeactivate) btnDeactivate.classList.add("active");
        if (notesSection) notesSection.classList.add("hidden");

        formSections.forEach(section => section.classList.add("deactivate-mode"));
        setFieldsReadOnly(true);

        if (badge) {
            badge.textContent = "Deactivate Client";
            badge.className = "badge-status deactivate";
        }
        if (submitBtn) {
            submitBtn.className = "btn-submit deactivate-mode";
            submitBtn.innerHTML = "🚫 Deactivate Client & Archive Folders";
            submitBtn.disabled = true; // Require valid catalog selection before deactivating
        }
        if (clientInput) {
            clientInput.setAttribute("list", "client-select-options");
            clientInput.placeholder = "Select client to deactivate...";
        }

    } else if (mode === "heal") {
        if (btnHeal) btnHeal.classList.add("active");
        if (notesSection) notesSection.classList.add("hidden");

        formSections.forEach(section => section.classList.add("heal-mode"));
        setFieldsReadOnly(false);

        if (badge) {
            badge.textContent = "Existing Client";
            badge.className = "badge-status heal";
        }
        if (submitBtn) {
            submitBtn.className = "btn-submit heal-mode";
            submitBtn.innerHTML = "💾 Update QBO Record";
        }
        if (clientInput) {
            clientInput.setAttribute("list", "client-select-options");
            clientInput.placeholder = "Select or search QBO client...";
        }

    } else { // "lead"
        if (btnLead) btnLead.classList.add("active");
        if (notesSection) notesSection.classList.remove("hidden");

        setFieldsReadOnly(false);

        if (badge) {
            badge.textContent = "New Client";
            badge.className = "badge-status lead";
        }
        if (submitBtn) {
            submitBtn.className = "btn-submit";
            submitBtn.innerHTML = "⚡ Provision New Client & Folders";
        }
        if (clientInput) {
            clientInput.removeAttribute("list");
            clientInput.placeholder = "Enter new lead name...";
        }
    }
}

/* ==========================================================================
   CLIENT DEMOGRAPHICS DIRECTORY LOGIC
   ========================================================================== */

/**
 * Filter and sort customerCatalog entries based on active toolbar controls
 */
function getFilteredDemographics() {
    const searchVal = (document.getElementById("demo-search")?.value || "").trim().toLowerCase();
    const entityFilter = document.getElementById("demo-filter-entity")?.value || "";
    const formatFilter = document.getElementById("demo-filter-format")?.value || "";

    const rows = Object.entries(customerCatalog).map(([catalogKey, client]) => {
        const meta = client.metadata || {};
        const addr = client.address || {};
        return {
            catalogKey,
            id: String(client.id || ""),
            display_name: client.display_name || "",
            friendly_name: meta.friendly_name || "",
            email: client.email || "",
            phone: client.phone || "",
            co_signer_name: meta.co_signer_name || "",
            co_signer_email: meta.co_signer_email || "",
            entity_type: meta.entity_type || "individual",
            delivery_format: meta.delivery_format || "electronic",
            street: addr.street || "",
            city: addr.city || "",
            state: addr.state || "",
            zip: addr.zip || ""
        };
    });

    const filtered = rows.filter(row => {
        if (entityFilter && row.entity_type !== entityFilter) return false;
        if (formatFilter && row.delivery_format !== formatFilter) return false;

        if (searchVal) {
            const haystack = [
                row.id,
                row.display_name,
                row.friendly_name,
                row.email,
                row.phone,
                row.co_signer_name,
                row.co_signer_email,
                row.city,
                row.state
            ].join(" ").toLowerCase();

            if (!haystack.includes(searchVal)) return false;
        }
        return true;
    });

    const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
    filtered.sort((a, b) => {
        const valA = String(a[demoSortField] || "");
        const valB = String(b[demoSortField] || "");
        const cmp = collator.compare(valA, valB);
        return demoSortDir === "asc" ? cmp : -cmp;
    });

    return filtered;
}

/**
 * Reset to page 1 when search or filter dropdowns change
 */
function onDemographicsFilterChange() {
    demoCurrentPage = 1;
    renderDemographicsTable();
}

/**
 * Step forward or backward by delta pages
 */
function changeDemographicsPage(delta) {
    demoCurrentPage = Math.max(1, demoCurrentPage + delta);
    renderDemographicsTable();
}

/**
 * Update rows-per-page setting and reset to page 1
 */
function changeDemographicsPageSize(sizeVal) {
    demoPageSize = sizeVal === "all" ? "all" : (parseInt(sizeVal, 10) || 50);
    demoCurrentPage = 1;
    renderDemographicsTable();
}

/**
 * Toggle sort field and direction for Demographics table
 */
function sortDemographics(field) {
    if (demoSortField === field) {
        demoSortDir = demoSortDir === "asc" ? "desc" : "asc";
    } else {
        demoSortField = field;
        demoSortDir = "asc";
    }
    demoCurrentPage = 1;

    // Update header sort indicators
    document.querySelectorAll("#demographics-table thead th.sortable").forEach(th => {
        const indicator = th.querySelector(".sort-indicator");
        if (!indicator) return;
        if (th.dataset.sort === demoSortField) {
            indicator.textContent = demoSortDir === "asc" ? "▲" : "▼";
        } else {
            indicator.textContent = "";
        }
    });

    renderDemographicsTable();
}

/**
 * Render master rows and expandable client detail drawers
 */
function renderDemographicsTable() {
    const tbody = document.getElementById("demographics-tbody");
    const countBadge = document.getElementById("demo-count");
    const exportBtn = document.getElementById("btn-export-csv");
    const paginationBar = document.getElementById("demo-pagination");
    const pageInfo = document.getElementById("demo-page-info");
    const pageIndicator = document.getElementById("demo-page-indicator");
    const prevBtn = document.getElementById("btn-page-prev");
    const nextBtn = document.getElementById("btn-page-next");
    if (!tbody) return;

    const rows = getFilteredDemographics();
    const totalCount = Object.keys(customerCatalog).length;

    if (countBadge) {
        countBadge.textContent = rows.length === totalCount
            ? `${totalCount} Clients`
            : `${rows.length} of ${totalCount} Clients`;
    }

    if (exportBtn) {
        exportBtn.textContent = `⬇️ Export CSV (${rows.length})`;
    }

    if (rows.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="demo-empty-state">No active clients match the current filters.</td></tr>`;
        if (paginationBar) paginationBar.classList.add("hidden");
        return;
    }

    if (paginationBar) paginationBar.classList.remove("hidden");

    const isShowAll = (demoPageSize === "all");
    const totalPages = isShowAll ? 1 : Math.max(1, Math.ceil(rows.length / demoPageSize));
    if (demoCurrentPage > totalPages) {
        demoCurrentPage = totalPages;
    }

    const startIdx = isShowAll ? 0 : (demoCurrentPage - 1) * demoPageSize;
    const endIdx = isShowAll ? rows.length : Math.min(startIdx + demoPageSize, rows.length);
    const pageRows = rows.slice(startIdx, endIdx);

    if (pageInfo) {
        pageInfo.textContent = `Showing ${startIdx + 1}–${endIdx} of ${rows.length}`;
    }
    if (pageIndicator) {
        pageIndicator.textContent = `Page ${demoCurrentPage} of ${totalPages}`;
    }
    if (prevBtn) {
        prevBtn.disabled = isShowAll || demoCurrentPage <= 1;
    }
    if (nextBtn) {
        nextBtn.disabled = isShowAll || demoCurrentPage >= totalPages;
    }

    const htmlChunks = [];

    pageRows.forEach(row => {
        const isExpanded = expandedDrawerIds.has(row.id);
        const entityLabel = ENTITY_LABELS[row.entity_type] || row.entity_type;
        const isPaper = row.delivery_format === "paper";
        const formatLabel = isPaper ? "Paper" : "Electronic";
        const formatClass = isPaper ? "format-paper" : "format-electronic";

        const emailHtml = row.email
            ? `<a href="mailto:${escapeHtml(row.email)}" class="demo-email-link" onclick="event.stopPropagation()">${escapeHtml(row.email)}</a>`
            : `<span class="demo-muted">—</span>`;

        const phoneHtml = row.phone
            ? escapeHtml(row.phone)
            : `<span class="demo-muted">—</span>`;

        const contactHtml = row.friendly_name
            ? escapeHtml(row.friendly_name)
            : `<span class="demo-muted">—</span>`;

        // Master Row
        htmlChunks.push(`
            <tr class="demo-row ${isExpanded ? "expanded" : ""}" data-client-id="${escapeHtml(row.id)}" onclick="toggleDemographicsDrawer('${escapeHtml(row.id)}')">
                <td>
                    <span class="demo-client-name">${escapeHtml(row.display_name)}</span>
                    <span class="demo-id-pill">#${escapeHtml(row.id)}</span>
                </td>
                <td>${contactHtml}</td>
                <td><span class="demo-pill entity">${escapeHtml(entityLabel)}</span></td>
                <td><span class="demo-pill ${formatClass}">${formatLabel}</span></td>
                <td>${emailHtml}</td>
                <td>${phoneHtml}</td>
                <td class="col-actions">
                    <div class="demo-actions-cell">
                        <button type="button" class="btn-row-edit" data-catalog-key="${escapeHtml(row.catalogKey)}" onclick="editClientFromDemographics(event, this)">
                            Edit
                        </button>
                        <span class="demo-chevron">${isExpanded ? "▼" : "▶"}</span>
                    </div>
                </td>
            </tr>
        `);

        // Expandable Detail Drawer Row
        if (isExpanded) {
            const coSignerParts = [];
            if (row.co_signer_name) coSignerParts.push(escapeHtml(row.co_signer_name));
            if (row.co_signer_email) {
                coSignerParts.push(`<a href="mailto:${escapeHtml(row.co_signer_email)}" class="demo-email-link">${escapeHtml(row.co_signer_email)}</a>`);
            }
            const coSignerDisplay = coSignerParts.length > 0
                ? coSignerParts.join(" &bull; ")
                : `<span class="demo-muted">None on file</span>`;

            const cityStateZip = [row.city, row.state].filter(Boolean).join(", ") + (row.zip ? ` ${row.zip}` : "");
            const addressParts = [row.street, cityStateZip.trim()].filter(Boolean).map(escapeHtml);
            const addressDisplay = addressParts.length > 0
                ? addressParts.join("<br>")
                : `<span class="demo-muted">No billing address on file</span>`;

            htmlChunks.push(`
                <tr class="demo-drawer-row">
                    <td colspan="7">
                        <div class="demo-drawer-card">
                            <div class="demo-drawer-grid">
                                <div class="demo-detail-item">
                                    <span class="demo-detail-label">Additional Signer (Spouse / Partner)</span>
                                    <span class="demo-detail-value">${coSignerDisplay}</span>
                                </div>
                                <div class="demo-detail-item">
                                    <span class="demo-detail-label">Billing Address</span>
                                    <span class="demo-detail-value">${addressDisplay}</span>
                                </div>
                                <div class="demo-detail-item">
                                    <span class="demo-detail-label">QuickBooks Online Record</span>
                                    <span class="demo-detail-value">Customer ID: <strong>${escapeHtml(row.id)}</strong></span>
                                </div>
                            </div>
                        </div>
                    </td>
                </tr>
            `);
        }
    });

    tbody.innerHTML = htmlChunks.join("");
}

/**
 * Toggle inline detail drawer for a given QBO client ID
 */
function toggleDemographicsDrawer(clientId) {
    if (!clientId) return;
    if (expandedDrawerIds.has(clientId)) {
        expandedDrawerIds.delete(clientId);
    } else {
        expandedDrawerIds.add(clientId);
    }
    renderDemographicsTable();
}

/**
 * Jump directly to "Update Existing QBO Client" tab pre-populated with selected client
 */
function editClientFromDemographics(event, btnEl) {
    event.stopPropagation();
    const catalogKey = btnEl.getAttribute("data-catalog-key");
    if (!catalogKey || !customerCatalog[catalogKey]) return;

    setMode("heal");
    fillClientFields(customerCatalog[catalogKey], catalogKey);

    const wrapperEl = document.getElementById("main-wrapper");
    if (wrapperEl) {
        wrapperEl.scrollIntoView({ behavior: "smooth", block: "start" });
    }
}

/**
 * Export currently filtered demographics rows as a colon-delimited CSV file
 * Matches column order & sanitization of export_qbo_demographics.sh
 */
function exportDemographicsCSV() {
    const rows = getFilteredDemographics();

    const cleanField = (val) => {
        if (val === null || val === undefined) return "";
        return String(val)
            .replace(/[\r\n]+/g, " ")
            .replace(/:/g, ";")
            .trim();
    };

    const header = [
        "qbo_id",
        "client_name",
        "friendly_name",
        "primary_email",
        "phone_number",
        "co_signer_name",
        "co_signer_email",
        "entity_type",
        "delivery_format",
        "street",
        "city",
        "state",
        "zip"
    ].join(":");

    const lines = rows.map(r => [
        r.id,
        r.display_name,
        r.friendly_name,
        r.email,
        r.phone,
        r.co_signer_name,
        r.co_signer_email,
        r.entity_type,
        r.delivery_format,
        r.street,
        r.city,
        r.state,
        r.zip
    ].map(cleanField).join(":"));

    const csvContent = [header, ...lines].join("\n") + "\n";
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);

    const dateStr = new Date().toISOString().split("T")[0];
    const envSuffix = isSandbox ? "_sandbox" : "";
    const filename = `qbo_demographics${envSuffix}_${dateStr}.csv`;

    const link = document.createElement("a");
    link.href = url;
    link.setAttribute("download", filename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

/* ==========================================================================
   INTAKE / UPDATE / DEACTIVATE FORM LOGIC
   ========================================================================== */

/**
 * Handle typing/selecting in the Client Name input
 */
function onClientSearchInput() {
    const inputEl = document.getElementById("client-select-input");
    if (!inputEl) return;

    const inputVal = inputEl.value.trim();
    const qboIdInput = document.getElementById("qbo_id");
    const submitBtn = document.getElementById("btn-submit");

    // 1. Exact match selected from datalist (e.g., "Sper, Karyn (ID: 55)")
    if (customerCatalog[inputVal]) {
        if (currentMode === "lead") {
            setMode("heal");
        }
        fillClientFields(customerCatalog[inputVal], inputVal);
        if (currentMode === "deactivate" && submitBtn) {
            submitBtn.disabled = false;
        }
        return;
    }

    // 2. Deactivate mode requires an exact catalog match
    if (currentMode === "deactivate") {
        if (inputVal === "") {
            clearClientFields();
        } else if (qboIdInput) {
            qboIdInput.value = "";
        }
        if (submitBtn) submitBtn.disabled = true;
        return;
    }

    // 3. Cloning trigger in "heal" mode:
    // If (ID: XX) was removed or the input was cleared (e.g. Ctrl+A -> Backspace),
    // detach qbo_id and switch to "lead" mode while preserving cloned fields.
    if (currentMode === "heal" && qboIdInput && qboIdInput.value) {
        const idPattern = new RegExp(`\\(ID:\\s*${qboIdInput.value}\\)`, "i");
        if (!idPattern.test(inputVal)) {
            qboIdInput.value = "";
            setMode("lead", true);
        }
        return;
    }

    // 4. Input completely cleared outside of a clone action
    if (inputVal === "") {
        clearClientFields();
    }
}

/**
 * Auto-populate form fields from selected QBO customer metadata and preserve QBO ID
 */
function fillClientFields(clientData, catalogKey = "") {
    if (!clientData) return;

    const meta = clientData.metadata || {};
    const addr = clientData.address || {};

    const formEl = document.getElementById("intake-form");
    let qboIdInput = document.getElementById("qbo_id");
    if (!qboIdInput) {
        qboIdInput = document.createElement("input");
        qboIdInput.type = "hidden";
        qboIdInput.id = "qbo_id";
        qboIdInput.name = "qbo_id";
        if (formEl) formEl.appendChild(qboIdInput);
    }

    qboIdInput.value = clientData.id || "";

    const clientInput = document.getElementById("client-select-input");
    if (clientInput) {
        clientInput.value = catalogKey || `${clientData.display_name} (ID: ${clientData.id})`;
    }

    const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (el && val !== undefined && val !== null) el.value = val;
    };

    setVal("friendly_name", meta.friendly_name || clientData.display_name || "");
    setVal("primary_email", clientData.email || "");
    setVal("phone_number", clientData.phone || "");
    setVal("entity_type", meta.entity_type || "");
    setVal("delivery_format", meta.delivery_format || meta.format || "electronic");
    setVal("co_signer_name", meta.co_signer_name || "");
    setVal("co_signer_email", meta.co_signer_email || "");

    // Address fields
    setVal("street", addr.street || "");
    setVal("city", addr.city || "");
    setVal("state", addr.state || "");
    setVal("zip", addr.zip || "");
}

/**
 * Clear auto-filled fields and purge hidden metadata
 */
function clearClientFields() {
    const qboIdInput = document.getElementById("qbo_id");
    if (qboIdInput) {
        qboIdInput.value = "";
    }

    const setVal = (id, val = "") => {
        const el = document.getElementById(id);
        if (el) el.value = val;
    };

    setVal("friendly_name");
    setVal("primary_email");
    setVal("phone_number");
    setVal("responder");
    setVal("entity_type");
    setVal("delivery_format", "electronic");
    setVal("co_signer_name");
    setVal("co_signer_email");
    setVal("street");
    setVal("city");
    setVal("state");
    setVal("zip");
    setVal("notes");
}

/**
 * Close status overlay and optionally reset form
 */
function closeStatusOverlay(shouldReset = false) {
    const overlay = document.getElementById("status-overlay");
    if (overlay) overlay.style.display = "none";

    if (shouldReset) {
        setMode("lead");
        loadCustomerCatalog(); // Refresh catalog in background
    }
}

/**
 * Deactivation Confirmation Modal Handlers
 */
function cancelDeactivationPrompt() {
    const modal = document.getElementById("confirm-modal-overlay");
    if (modal) modal.classList.add("hidden");
}

function executeDeactivationSubmit() {
    const modal = document.getElementById("confirm-modal-overlay");
    if (modal) modal.classList.add("hidden");
    sendIntakeFormRequest();
}

/**
 * Handle form submission entry point
 */
function handleFormSubmit(event) {
    event.preventDefault();

    if (currentMode === "deactivate") {
        const clientInput = document.getElementById("client-select-input");
        const clientName = clientInput ? clientInput.value : "this client";
        const confirmTextEl = document.getElementById("confirm-modal-text");

        if (confirmTextEl) {
            confirmTextEl.innerHTML = `Are you sure you want to set <strong>${escapeHtml(clientName)}</strong> to inactive in QBO and move their folders to <strong>!Inactive Clients</strong>?`;
        }

        const modal = document.getElementById("confirm-modal-overlay");
        if (modal) modal.classList.remove("hidden");
        return;
    }

    sendIntakeFormRequest();
}

/**
 * Send POST request payload to CGI script
 */
async function sendIntakeFormRequest() {
    const overlay = document.getElementById("status-overlay");
    const statusTitle = document.getElementById("status-title");
    const statusText = document.getElementById("status-text");
    const statusSpinner = document.getElementById("status-spinner");
    const btnClose = document.getElementById("btn-close-overlay");

    if (overlay) overlay.style.display = "flex";
    if (statusSpinner) statusSpinner.style.display = "block";
    if (btnClose) btnClose.style.display = "none";

    const isDeactivate = (currentMode === "deactivate");
    const isNewLead = (currentMode === "lead");

    if (statusTitle) {
        statusTitle.className = "";
        if (isDeactivate) {
            statusTitle.textContent = "Deactivating Client & Archiving Folders...";
        } else if (isNewLead) {
            statusTitle.textContent = "Provisioning New Client...";
        } else {
            statusTitle.textContent = "Updating QBO Profile...";
        }
    }

    if (statusText) {
        if (isDeactivate) {
            statusText.textContent = "Setting QBO status to inactive, moving SharePoint folders to !Inactive Clients, revoking folder access, and notifying team...";
        } else if (isNewLead) {
            statusText.textContent = "Creating QBO Customer record, generating SharePoint folder trees, copying log files, and sending team notifications...";
        } else {
            statusText.textContent = "Syncing updated metadata to QuickBooks Online and sending email notification...";
        }
    }

    const formEl = document.getElementById("intake-form");
    const formData = new FormData(formEl);
    const payload = {};

    for (const [key, value] of formData.entries()) {
        payload[key] = (key === "client_name")
            ? value.replace(/\s*\(\s*ID:\s*\d+\s*\)/gi, "").trim()
            : value;
    }

    // Capture disabled <select> values omitted by FormData in deactivate mode
    const getVal = (id) => document.getElementById(id)?.value || "";
    payload["entity_type"] = payload["entity_type"] || getVal("entity_type");
    payload["delivery_format"] = payload["delivery_format"] || getVal("delivery_format");

    // Explicit action parameters
    payload["action"] = currentMode; // "lead", "heal", or "deactivate"
    payload["is_new_lead"] = isNewLead ? "true" : "false";

    try {
        const response = await fetch(PROVISION_CGI_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        if (!response.ok) throw new Error(`HTTP ${response.status} - Internal Server Error`);

        const result = await response.json();

        if (result.status === "success") {
            if (statusSpinner) statusSpinner.style.display = "none";
            if (statusTitle) {
                if (isDeactivate) {
                    statusTitle.className = "success-deactivate";
                    statusTitle.textContent = "🚫 Client Deactivated & Archived Successfully!";
                } else if (isNewLead) {
                    statusTitle.className = "success-lead";
                    statusTitle.textContent = "✨ New Client Provisioned!";
                } else {
                    statusTitle.className = "success-heal";
                    statusTitle.textContent = "🟢 Profile Updated Successfully!";
                }
            }
            if (statusText) {
                statusText.textContent = result.message || "Operation completed successfully.";
            }

            if (btnClose) {
                btnClose.textContent = "Done & Reset Form";
                btnClose.className = isDeactivate ? "btn-submit deactivate-mode" : "btn-submit";
                btnClose.onclick = () => closeStatusOverlay(true);
                btnClose.style.display = "inline-block";
            }
        } else {
            throw new Error(result.message || "An error occurred during processing.");
        }
    } catch (err) {
        if (statusSpinner) statusSpinner.style.display = "none";
        if (statusTitle) {
            statusTitle.className = "error";
            statusTitle.textContent = "❌ Process Failed";
        }
        if (statusText) {
            statusText.textContent = err.message || "Could not complete the intake request. Please check the server logs.";
        }

        if (btnClose) {
            btnClose.textContent = "Close & Keep Editing";
            btnClose.className = "btn-submit heal-mode";
            btnClose.onclick = () => closeStatusOverlay(false);
            btnClose.style.display = "inline-block";
        }
    }
}