(function () {
    "use strict";

    const BOOT = window.TAX_QANDA_BOOT || {
        formKey: "2026",
        formMeta: {
            key: "2026",
            label: "2026 Tax Return",
            tax_year: "2026",
            prior_year: "2025",
            next_year: "2027",
            as_of_date: "12/31/26",
            has_2025_fields: false,
            has_ev_charger: true
        },
        availableForms: [],
        respondents: [],
        initialIdx: null,
        notFoundKey: null,
        embedMode: false,
        generatedDate: ""
    };

    const FORM_META = BOOT.formMeta;
    const RESPONDENTS = Array.isArray(BOOT.respondents) ? BOOT.respondents : [];
    const AVAILABLE_FORMS = Array.isArray(BOOT.availableForms) ? BOOT.availableForms : [];

    let acctRevealed = false;
    let currentIdx = null;

    function escHtml(str) {
        if (str === null || str === undefined) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function getVal(rec, key) {
        const v = rec && rec[key];
        return (v && String(v).trim() !== "") ? String(v).trim() : "—";
    }

    function hasValue(val) {
        return Boolean(val && val !== "—");
    }

    function isYes(val) {
        return typeof val === "string" && val.trim().toLowerCase() === "yes";
    }

    function isNoOrEmpty(val) {
        if (!val || val === "—") return true;
        return val.trim().toLowerCase() === "no";
    }

    function formatBadge(val, warnOnYes) {
        if (!hasValue(val)) {
            return `<span class="value value-muted">—</span>`;
        }
        const lower = val.toLowerCase();
        if (lower === "yes") {
            const cls = warnOnYes ? "badge badge-warn" : "badge badge-yes";
            return `<span class="${cls}">Yes</span>`;
        }
        if (lower === "no") {
            return `<span class="badge badge-no">No</span>`;
        }
        return `<span class="value">${escHtml(val)}</span>`;
    }

    function renderRow(label, value, opts) {
        const { warnOnYes = false, isSub = false, hideIfEmpty = false } = opts || {};
        if (hideIfEmpty && !hasValue(value)) return "";
        const mutedCls = isNoOrEmpty(value) ? " muted-row" : "";
        const subCls = isSub ? " sub-row" : "";
        return `
            <div class="item-row${mutedCls}${subCls}">
                <span class="label">${escHtml(label)}</span>
                ${formatBadge(value, warnOnYes)}
            </div>
        `;
    }

    function renderStacked(label, value, opts) {
        const { hideIfEmpty = true, neutral = false } = opts || {};
        if (hideIfEmpty && !hasValue(value)) return "";
        const lblCls = neutral ? "stacked-label neutral-label" : "stacked-label";
        const txtCls = neutral ? "stacked-text neutral-note" : "stacked-text";
        return `
            <div class="stacked-block">
                <div class="${lblCls}">${escHtml(label)}</div>
                <div class="${txtCls}">${escHtml(value)}</div>
            </div>
        `;
    }

    function renderPair(flagLabel, flagVal, detailLabel, detailVal, opts) {
        const { warnOnYes = true } = opts || {};
        if (hasValue(detailVal)) {
            return `
                <div class="callout-group">
                    ${renderRow(flagLabel, flagVal, { warnOnYes })}
                    ${renderStacked(detailLabel, detailVal, { hideIfEmpty: false })}
                </div>
            `;
        }
        return renderRow(flagLabel, flagVal, { warnOnYes });
    }

    function renderAccountRow(acctNum) {
        if (!hasValue(acctNum)) {
            return renderRow("Account Number", "—");
        }
        const clean = acctNum.replace(/\s+/g, "");
        const masked = clean.length > 4 ? "••••" + clean.slice(-4) : "•".repeat(clean.length);
        const screenVal = acctRevealed ? clean : masked;
        const toggleBtn = `<button type="button" class="toggle-acct no-print" data-action="toggle-acct">${acctRevealed ? "Hide" : "Show"}</button>`;
        return `
            <div class="item-row">
                <span class="label">Account Number</span>
                <span class="value">
                    <span class="no-print">${escHtml(screenVal)}${toggleBtn}</span>
                    <span class="print-only">${escHtml(clean)}</span>
                </span>
            </div>
        `;
    }

    function renderStatusChips(rec) {
        const strip = document.getElementById("status-strip");
        if (!strip) return;

        const chips = [];

        const signed = getVal(rec, "signed_agreement");
        if (isYes(signed)) {
            chips.push(`<span class="status-chip chip-ok">✓ Agreement Signed</span>`);
        } else if (signed.toLowerCase() === "no") {
            chips.push(`<span class="status-chip chip-danger">✗ Agreement Unsigned</span>`);
        }

        const irsAuth = getVal(rec, "irs_auth");
        if (isYes(irsAuth)) {
            chips.push(`<span class="status-chip chip-ok">✓ IRS Auth</span>`);
        } else if (irsAuth.toLowerCase() === "no") {
            chips.push(`<span class="status-chip chip-danger">✗ No IRS Auth</span>`);
        }

        const pkg = getVal(rec, "final_package_pref");
        if (hasValue(pkg)) {
            const shortPkg = pkg.toLowerCase().includes("electronic")
                ? "Delivery: Electronic PDF"
                : pkg;
            chips.push(`<span class="status-chip chip-info" title="${escHtml(pkg)}">${escHtml(shortPkg)}</span>`);
        }

        strip.innerHTML = chips.join("");
        strip.style.display = chips.length ? "flex" : "none";
    }

    function renderHighlights(rec) {
        const banner = document.getElementById("highlights-banner");
        if (!banner) return;

        const yr = FORM_META.tax_year;
        const priorYr = FORM_META.prior_year;
        const nextYr = FORM_META.next_year;

        const flagChecks = [
            ["first_year_client", "First-Year Client"],
            ["bank_changed", "Bank Info Changed"],
            ["dep_changes", "Dependent Changes"],
            ["kiddie_tax", "Kiddie Tax Applies"],
            ["high_deductible", "High-Deductible Health Plan"],
            ["form_1095a", "Form 1095-A"],
            ["sit_diff_flag", `${priorYr} vs ${yr} Situation Diff`],
            ["new_car_flag", `New Financed Car (${yr})`],
            ["home_buy_sell_refi", "Home Buy / Sell / Refi / HELOC"],
            ["energy_improvements", FORM_META.has_2025_fields ? "Energy Improvements" : "SC Solar Improvement"],
            ["ev_charger_2026", "EV Charger Installed (H1 2026)"],
            ["rental_business_home", "Home Rented / Business Use"],
            ["non_employer_ira", "Non-Employer IRA/SEP"],
            ["retirement_rollover", "Retirement Rollover"],
            ["roth_conversion", `${yr} Roth Conversion`],
            ["sec_529_flag", "Sec. 529 Contribution"],
            ["qcd_charity_ira", "Direct IRA Charity (QCD)"],
            ["est_tax_flag", `${yr} Est. Taxes Paid`],
            ["income_diff_next_flag", `${nextYr} Income Diff Expected`],
            ["estate_wealth_talk", "Requests Estate/Wealth Talk"],
            ["future_charity_intent", `${nextYr}+ Charitable Intent`],
            ["employer_benefit_talk", "Requests Benefit Talk"],
            ["overtime_tips", "Overtime / Tips Income"],
            ["household_employees", "Household Employee >$2.6k"],
            ["foreign_accounts", "Foreign Accounts / Assets"],
            ["gifts_over_19k", "Gifts >$19k or Trust"]
        ];

        const pills = flagChecks
            .filter(([key]) => isYes(rec[key]))
            .map(([, label]) => `<span class="flag-pill">! ${escHtml(label)}</span>`);

        if (rec.signed_agreement && rec.signed_agreement.toLowerCase() === "no") {
            pills.unshift(`<span class="flag-pill flag-pill-danger">! Agreement Not Signed</span>`);
        }

        if (pills.length === 0) {
            banner.style.display = "none";
            banner.innerHTML = "";
            return;
        }

        banner.style.display = "flex";
        banner.innerHTML = `<span class="highlights-label">Preparer Flags:</span>` + pills.join("");
    }

    function renderEmptyState() {
        currentIdx = null;
        document.getElementById("header-title").textContent = `${FORM_META.tax_year} Tax Q&A Summary: Select Taxpayer`;
        document.getElementById("header-meta").style.display = "none";
        document.getElementById("status-strip").style.display = "none";

        const banner = document.getElementById("highlights-banner");
        if (BOOT.notFoundKey) {
            banner.style.display = "flex";
            banner.innerHTML = `<span class="flag-pill flag-pill-danger">No ${escHtml(FORM_META.tax_year)} form submission found for key: ${escHtml(BOOT.notFoundKey)}</span>`;
        } else {
            banner.style.display = "none";
            banner.innerHTML = "";
        }

        const msg = BOOT.notFoundKey
            ? `No ${escHtml(FORM_META.tax_year)} response matched customer key "${escHtml(BOOT.notFoundKey)}". Select a taxpayer from the dropdown above.`
            : `Select a taxpayer above to view their ${escHtml(FORM_META.tax_year)} intake summary.`;

        const placeholder = `<div class="empty-placeholder">${msg}</div>`;
        document.getElementById("group-engagement").innerHTML = placeholder;
        document.getElementById("group-family").innerHTML = placeholder;
        document.getElementById("group-tax-activity").innerHTML = placeholder;
        document.getElementById("group-planning").innerHTML = placeholder;
        document.getElementById("group-banking-compliance").innerHTML = placeholder;
    }

    function updateDashboard(idx, syncUrl) {
        if (idx === "" || idx === null || idx === undefined || !RESPONDENTS[idx]) {
            renderEmptyState();
            return;
        }

        const numericIdx = Number(idx);
        if (currentIdx !== numericIdx) {
            acctRevealed = false;
            currentIdx = numericIdx;
        }

        const rec = RESPONDENTS[numericIdx];
        const select = document.getElementById("master-select");
        if (select) select.value = String(numericIdx);

        const custKey = getVal(rec, "customer_key");

        // Keep browser URL clean & shareable using ?form=YYYY&key=CUSTOMER_KEY (or fallback ?idx=N)
        if (syncUrl && window.history && window.history.replaceState) {
            const url = new URL(window.location.href);
            url.searchParams.delete("year");
            url.searchParams.set("form", BOOT.formKey);
            if (hasValue(custKey)) {
                url.searchParams.set("key", custKey);
                url.searchParams.delete("idx");
            } else {
                url.searchParams.set("idx", String(numericIdx));
                url.searchParams.delete("key");
            }
            window.history.replaceState({}, "", url.toString());
        }

        const yr = FORM_META.tax_year;
        const priorYr = FORM_META.prior_year;
        const nextYr = FORM_META.next_year;

        document.getElementById("header-title").textContent = `${yr} Tax Q&A: ${rec.client_name}`;

        const metaEl = document.getElementById("header-meta");
        metaEl.textContent = hasValue(custKey)
            ? `Key: ${custKey}  |  Printed: ${BOOT.generatedDate}`
            : `Printed: ${BOOT.generatedDate}`;
        metaEl.style.display = "inline-block";

        renderStatusChips(rec);
        renderHighlights(rec);

        // --- COLUMN 1: Client & Household ---
        let engagementHtml = "";
        if (FORM_META.has_2025_fields) {
            engagementHtml += renderRow("Email Address", getVal(rec, "email"));
        }
        engagementHtml +=
            renderRow("First Year with Firm?", getVal(rec, "first_year_client"), { warnOnYes: true }) +
            renderRow("Taxpayer Accepted Terms?", getVal(rec, "taxpayer_terms")) +
            renderRow("Spouse Accepted Terms?", getVal(rec, "spouse_terms")) +
            renderPair(
                "Contact Info Same as Last Yr?",
                getVal(rec, "contact_same"),
                "Updated Contact Details",
                getVal(rec, "updated_contact_info"),
                { warnOnYes: false }
            ) +
            renderRow("Final Package Delivery", getVal(rec, "final_package_pref"));
        document.getElementById("group-engagement").innerHTML = engagementHtml;

        let familyHtml = renderRow(`Married as of ${FORM_META.as_of_date}?`, getVal(rec, "married"));
        if (FORM_META.has_2025_fields) {
            familyHtml += renderRow("Spouse's Name", getVal(rec, "spouse_name"), { hideIfEmpty: true });
        }
        familyHtml +=
            renderPair(
                "Dependent Changes?",
                getVal(rec, "dep_changes"),
                "Dependent Change Details",
                getVal(rec, "updated_dep_info"),
                { warnOnYes: true }
            ) +
            renderRow("Kiddie Tax Qualifier?", getVal(rec, "kiddie_tax"), { warnOnYes: true }) +
            renderStacked("Taxpayer & Dependent Birthdates", getVal(rec, "birthdates"), {
                hideIfEmpty: false,
                neutral: true
            });
        document.getElementById("group-family").innerHTML = familyHtml;

        // --- COLUMN 2: Tax Year Activity & Deductions ---
        let taxActivityHtml =
            renderPair(
                `${priorYr} vs ${yr} Situation Diff?`,
                getVal(rec, "sit_diff_flag"),
                "Situation Difference Details",
                getVal(rec, "updated_sit_diff_info"),
                { warnOnYes: true }
            );

        if (FORM_META.has_2025_fields) {
            taxActivityHtml += renderRow("Form 1095-A (Marketplace)?", getVal(rec, "form_1095a"), { warnOnYes: true });
        }

        taxActivityHtml +=
            renderRow("High-Deductible Medical Plan?", getVal(rec, "high_deductible"), { warnOnYes: true }) +
            renderRow("Overtime or Tips Income?", getVal(rec, "overtime_tips"), { warnOnYes: true }) +
            renderRow(`New Car Financed in ${yr}?`, getVal(rec, "new_car_flag"), { warnOnYes: true }) +
            renderRow("Vehicle VIN", getVal(rec, "vin_number"), { isSub: true, hideIfEmpty: true }) +
            renderRow("Home Buy / Sell / Refi / HELOC?", getVal(rec, "home_buy_sell_refi"), { warnOnYes: true }) +
            renderRow(
                FORM_META.has_2025_fields ? "Residential Energy Improvements?" : "SC Residential Solar?",
                getVal(rec, "energy_improvements"),
                { warnOnYes: true }
            );

        if (FORM_META.has_ev_charger) {
            taxActivityHtml += renderRow("EV Charger (Jan–Jun 2026)?", getVal(rec, "ev_charger_2026"), { warnOnYes: true });
        }

        if (FORM_META.has_2025_fields) {
            taxActivityHtml += renderRow("Home Rented / Business Use?", getVal(rec, "rental_business_home"), { warnOnYes: true });
        }

        taxActivityHtml +=
            renderRow("Non-Employer IRA / SEP?", getVal(rec, "non_employer_ira"), { warnOnYes: true }) +
            renderRow("Retirement Plan Rollover?", getVal(rec, "retirement_rollover"), { warnOnYes: true }) +
            renderRow(`${yr} Roth IRA Conversion?`, getVal(rec, "roth_conversion"), { warnOnYes: true }) +
            renderRow("Sec. 529 Plan Contribution?", getVal(rec, "sec_529_flag"), { warnOnYes: true }) +
            renderRow("529 State & Amount", getVal(rec, "sec_529_details"), { isSub: true, hideIfEmpty: true }) +
            renderRow("Direct IRA Charity (QCD)?", getVal(rec, "qcd_charity_ira"), { warnOnYes: true });

        document.getElementById("group-tax-activity").innerHTML = taxActivityHtml;

        // --- COLUMN 3: Planning, Banking & Compliance ---
        document.getElementById("group-planning").innerHTML =
            renderPair(
                `Paid ${yr} Estimated Taxes?`,
                getVal(rec, "est_tax_flag"),
                `${yr} Est. Tax Payments`,
                getVal(rec, "est_tax_details"),
                { warnOnYes: true }
            ) +
            renderPair(
                `${nextYr} Income Diff Expected?`,
                getVal(rec, "income_diff_next_flag"),
                `${nextYr} Income Outlook`,
                getVal(rec, "updated_next_income_info"),
                { warnOnYes: true }
            ) +
            renderRow("Estate / Wealth Discussion?", getVal(rec, "estate_wealth_talk"), { warnOnYes: true }) +
            renderRow(`${nextYr}+ Charitable Intent?`, getVal(rec, "future_charity_intent"), { warnOnYes: true }) +
            renderRow("Employer Benefit Discussion?", getVal(rec, "employer_benefit_talk"), { warnOnYes: true });

        document.getElementById("group-banking-compliance").innerHTML =
            renderRow("Use Bank for Refund / Due?", getVal(rec, "use_bank_dd")) +
            renderRow("Bank Info Changed / New?", getVal(rec, "bank_changed"), { warnOnYes: true }) +
            renderRow("Bank Name", getVal(rec, "bank_name")) +
            renderRow("Account Type", getVal(rec, "account_type")) +
            renderRow("Routing Number", getVal(rec, "routing_num")) +
            renderAccountRow(getVal(rec, "account_num")) +
            renderRow("Foreign Accounts / Assets?", getVal(rec, "foreign_accounts"), { warnOnYes: true }) +
            renderRow("Household Employee >$2.6k?", getVal(rec, "household_employees"), { warnOnYes: true }) +
            renderRow("Gifts >$19k or to Trust?", getVal(rec, "gifts_over_19k"), { warnOnYes: true }) +
            renderRow("Taxpayer Pres. Fund ($3)?", getVal(rec, "pres_fund_taxpayer")) +
            renderRow("Spouse Pres. Fund ($3)?", getVal(rec, "pres_fund_spouse")) +
            renderStacked("Additional Client Notes", getVal(rec, "additional_comments"), { hideIfEmpty: true });
    }

    function printSummary() {
        if (currentIdx === null || !RESPONDENTS[currentIdx]) {
            alert("Please select a taxpayer before printing or saving as PDF.");
            return;
        }
        const rec = RESPONDENTS[currentIdx];
        const safeName = (rec.client_name || "Taxpayer")
            .replace(/[^a-zA-Z0-9 _-]/g, "")
            .trim()
            .replace(/\s+/g, "_") || "Taxpayer";

        const originalTitle = document.title;
        document.title = `${FORM_META.tax_year}_Tax_Summary_${safeName}`;
        window.print();
        setTimeout(() => {
            document.title = originalTitle;
        }, 500);
    }

    function init() {
        const controlsEl = document.getElementById("controls-container");
        if (controlsEl && !BOOT.embedMode) {
            const formOptionsHtml = AVAILABLE_FORMS.map(f => {
                const selected = (f.key === BOOT.formKey) ? " selected" : "";
                return `<option value="${escHtml(f.key)}"${selected}>${escHtml(f.label)}</option>`;
            }).join("");

            const sortedClients = RESPONDENTS.map((r, i) => ({
                name: r.client_name || `Respondent #${i + 1}`,
                idx: i
            })).sort((a, b) => a.name.localeCompare(b.name));

            const clientOptionsHtml = sortedClients.map(c =>
                `<option value="${c.idx}">${escHtml(c.name)}</option>`
            ).join("");

            controlsEl.innerHTML = `
                <select id="form-select" aria-label="Select Tax Form Year">
                    ${formOptionsHtml}
                </select>
                <select id="master-select" aria-label="Select Taxpayer">
                    <option value="" disabled selected>Select Taxpayer (${sortedClients.length})...</option>
                    ${clientOptionsHtml}
                </select>
                <button type="button" class="btn-print" id="btn-print-summary">Print / Save PDF</button>
            `;

            document.getElementById("form-select").addEventListener("change", function () {
                const url = new URL(window.location.href);
                url.searchParams.set("form", this.value);
                url.searchParams.delete("year");
                url.searchParams.delete("idx");
                if (currentIdx !== null && RESPONDENTS[currentIdx]) {
                    const activeKey = getVal(RESPONDENTS[currentIdx], "customer_key");
                    if (hasValue(activeKey)) {
                        url.searchParams.set("key", activeKey);
                    } else {
                        url.searchParams.delete("key");
                    }
                }
                window.location.href = url.toString();
            });

            document.getElementById("master-select").addEventListener("change", function () {
                updateDashboard(this.value, true);
            });

            document.getElementById("btn-print-summary").addEventListener("click", printSummary);
        }

        // Delegated listener for bank account Show/Hide toggle (works across all renders & embed mode)
        const bankingCard = document.getElementById("group-banking-compliance");
        if (bankingCard) {
            bankingCard.addEventListener("click", function (e) {
                if (e.target && e.target.closest("[data-action='toggle-acct']")) {
                    acctRevealed = !acctRevealed;
                    if (currentIdx !== null) {
                        updateDashboard(currentIdx, false);
                    }
                }
            });
        }

        if (BOOT.initialIdx !== null && RESPONDENTS[BOOT.initialIdx]) {
            updateDashboard(BOOT.initialIdx, false);
        } else {
            renderEmptyState();
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
