/**
 * Tarrant Advisors - Section 7216 Consent Portal Front-End Orchestrator
 * Controls customer selection changes, unified profile metadata rendering
 * (matching the Engagement Portal UX without QBO write-back or disk persistence),
 * and form submission validation.
 */

/**
 * Utility helper to sanitize dynamic text strings before DOM insertion.
 */
function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/**
 * Utility helper to decode HTML entities (e.g., &amp; -> &)
 */
function unescapeHtml(str) {
    if (!str) return '';
    const txt = document.createElement('textarea');
    txt.innerHTML = str;
    return txt.value;
}

/**
 * Handles the event when a staff member selects a different QBO Customer.
 */
function onClientChange() {
    const clientSelect = document.getElementById('client-select');
    if (!clientSelect) return;

    const selectedClient = clientSelect.value;
    const profileContainer = document.getElementById('profile-container');
    const submitBtn = document.getElementById('btn-submit-main');

    // Reset workspace DOM state
    if (profileContainer) profileContainer.innerHTML = '';

    if (!selectedClient || !window.clientData || !window.clientData[selectedClient]) {
        if (profileContainer) profileContainer.style.display = 'none';
        if (submitBtn) submitBtn.style.display = 'none';
        return;
    }

    const clientRecord = window.clientData[selectedClient];
    const address = clientRecord.address || {};
    const metadata = clientRecord.metadata || {}; // Sourced from QBO Notes JSON via Python backend
    
    // Support both <input list="..."> and legacy <select> elements for name extraction
    const rawText = clientSelect.value || (clientSelect.options && clientSelect.selectedIndex >= 0 ? clientSelect.options[clientSelect.selectedIndex].text : '');
    const rawOptionText = unescapeHtml(rawText);
    const rawCustomerName = rawOptionText.split(/\s*\(Customer/)[0].trim();

    const healData = window.preservedHealData || {};

    // Prioritize preserved workspace edits, then QBO Notes metadata, then QBO primary fields
    const profileValues = {
        friendlyName: healData.friendly_name || metadata.friendly_name || rawCustomerName,
        legalName: healData.local_legal_name || rawCustomerName,
        targetEmail: healData.target_email || metadata.primary_signer_email || clientRecord.email || '',
        phone: healData.client_phone_num || clientRecord.phone || '',
        street: healData.local_street !== undefined ? healData.local_street : (address.street || ''),
        city: healData.local_city !== undefined ? healData.local_city : (address.city || ''),
        state: healData.local_state !== undefined ? healData.local_state : (address.state || ''),
        zip: healData.local_zip !== undefined ? healData.local_zip : (address.zip || ''),
        coSignerName: healData.meta_co_signer_name !== undefined ? healData.meta_co_signer_name : (metadata.co_signer_name || ''),
        coSignerEmail: healData.meta_additional_signer !== undefined ? healData.meta_additional_signer : (metadata.co_signer_email || '')
    };

    const isProfileIncomplete = !profileValues.street || !profileValues.city || !profileValues.state || !profileValues.zip || !profileValues.targetEmail;

    if (profileContainer) {
        profileContainer.style.display = 'block';
        renderProfileMetadataPanel(profileContainer, profileValues, isProfileIncomplete);
    }

    if (submitBtn) {
        submitBtn.style.display = 'block';
    }

    // Hydrate top-level form inputs if preserved from preview revert
    if (healData.tax_years_covered) {
        const taxYearsSelect = document.getElementById('tax-years-covered');
        if (taxYearsSelect) taxYearsSelect.value = healData.tax_years_covered;
    }
    if (healData.client_advisor) {
        const clientAdvisorInput = document.getElementById('client-advisor');
        if (clientAdvisorInput) clientAdvisorInput.value = healData.client_advisor;
    }

    // Clear preserved memory after initial hydration pass to prevent leaks across client switches
    window.preservedHealData = {};
}

/**
 * Renders the unified Client Account & Signer Profile Metadata card matching the Engagement Portal UI.
 * Note: All fields are strictly local overrides for this consent document and are NEVER saved to disk or written back to QBO.
 */
function renderProfileMetadataPanel(container, values, isIncomplete) {
    const cardClass = isIncomplete ? 'profile-card profile-card-fallback' : 'profile-card profile-card-complete';
    const titleColor = isIncomplete ? '#b76200' : '#107c41';
    const titleBadge = isIncomplete
        ? '⚠️ Client Account & Signer Profile Metadata (Incomplete QBO Record)'
        : 'Client Account & Signer Profile Metadata';

    const statusBanner = isIncomplete
        ? `<p style="margin: 0 0 14px 0; font-size: 13px; color: #7c4200;">
               QuickBooks Online is missing a primary email or complete billing address for this customer. Complete the fields below to generate the consent letter.
               <em>(Local override only — values are not saved to disk or written back to QBO.)</em>
           </p>`
        : `<p style="margin: 0 0 14px 0; font-size: 12px; color: #555;">
               Pre-populated from QuickBooks Online. Any edits below apply strictly to this consent letter and are <strong>not</strong> saved to disk or written back to QBO.
           </p>`;

    container.innerHTML = `
        <div class="${cardClass}">
            <div class="profile-card-title" style="color: ${titleColor};">${titleBadge}</div>
            ${statusBanner}

            <!-- Row 1: Primary Signer Friendly Name, Legal Name, Primary Signer Email, Primary Phone Number -->
            <div style="display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 12px;">
                <div class="form-field-group">
                    <label class="field-label" style="font-weight: 600; font-size: 13px;">Primary Signer Friendly Name:</label>
                    <input type="text" name="friendly_name" value="${escapeHtml(unescapeHtml(values.friendlyName))}" required placeholder="e.g., Danny Corning">
                </div>
                <div class="form-field-group">
                    <label class="field-label" style="font-weight: 600; font-size: 13px;">Legal Name / Entity Title:</label>
                    <input type="text" name="local_legal_name" value="${escapeHtml(unescapeHtml(values.legalName))}" required placeholder="e.g., Corning, Daniel & Catherine">
                </div>
                <div class="form-field-group">
                    <label class="field-label" style="font-weight: 600; font-size: 13px;">Primary Signer Email:</label>
                    <input type="email" name="target_email" value="${escapeHtml(unescapeHtml(values.targetEmail))}" required placeholder="client@example.com">
                </div>
                <div class="form-field-group">
                    <label class="field-label" style="font-weight: 600; font-size: 13px;">Primary Phone Number:</label>
                    <input type="text" name="client_phone_num" value="${escapeHtml(unescapeHtml(values.phone))}" placeholder="(555) 555-5555">
                </div>
            </div>

            <!-- Row 2: Street Address, City, State, Zip Code -->
            <div style="display: grid; grid-template-columns: 1.5fr 1fr 0.6fr 0.8fr; gap: 12px; margin-bottom: 12px;">
                <div class="form-field-group">
                    <label class="field-label" style="font-weight: 600; font-size: 13px;">Street Address:</label>
                    <input type="text" name="local_street" value="${escapeHtml(unescapeHtml(values.street))}" required placeholder="e.g., 1525 Melton Rd">
                </div>
                <div class="form-field-group">
                    <label class="field-label" style="font-weight: 600; font-size: 13px;">City:</label>
                    <input type="text" name="local_city" value="${escapeHtml(unescapeHtml(values.city))}" required placeholder="e.g., Lutherville Timonium">
                </div>
                <div class="form-field-group">
                    <label class="field-label" style="font-weight: 600; font-size: 13px;">State:</label>
                    <input type="text" name="local_state" value="${escapeHtml(unescapeHtml(values.state))}" required placeholder="MD" maxlength="2">
                </div>
                <div class="form-field-group">
                    <label class="field-label" style="font-weight: 600; font-size: 13px;">Zip Code:</label>
                    <input type="text" name="local_zip" value="${escapeHtml(unescapeHtml(values.zip))}" required placeholder="21093">
                </div>
            </div>

            <!-- Row 3: Co-Signer Full Name, Co-Signer Email Address -->
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
                <div class="form-field-group">
                    <label class="field-label" style="font-weight: 600; font-size: 13px;">Co-Signer Full Name (Joint Return):</label>
                    <input type="text" name="meta_co_signer_name" value="${escapeHtml(unescapeHtml(values.coSignerName))}" placeholder="e.g., Katie Corning">
                </div>
                <div class="form-field-group">
                    <label class="field-label" style="font-weight: 600; font-size: 13px;">Co-Signer Email Address:</label>
                    <input type="email" name="meta_additional_signer" value="${escapeHtml(unescapeHtml(values.coSignerEmail))}" placeholder="spouse@example.com">
                </div>
            </div>
        </div>
    `;
}

// Global Form Submit Safeguard
document.addEventListener('DOMContentLoaded', () => {
    const form = document.querySelector('form');
    if (!form) return;

    form.addEventListener('submit', (e) => {
        const action = e.submitter ? e.submitter.value : '';
        if (action === 'revert_to_workspace') return true;

        const clientSelect = document.getElementById('client-select');
        if (!clientSelect || !clientSelect.value) return true;

        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

        const targetEmailInput = document.querySelector('input[name="target_email"]');
        if (targetEmailInput) {
            const targetEmailVal = targetEmailInput.value.trim();
            if (!targetEmailVal || !emailRegex.test(targetEmailVal)) {
                alert("Please enter a valid email address for Primary Signer Email.");
                targetEmailInput.focus();
                e.preventDefault();
                return false;
            }
        }

        const addSignerInput = document.querySelector('input[name="meta_additional_signer"]');
        const coSignerNameInput = document.querySelector('input[name="meta_co_signer_name"]');

        const hasEmail = addSignerInput && addSignerInput.value.trim() !== "";
        const hasName = coSignerNameInput && coSignerNameInput.value.trim() !== "";

        if (hasEmail || hasName) {
            if (hasEmail && !emailRegex.test(addSignerInput.value.trim())) {
                alert("Please enter a valid email address for Co-Signer Email Address.");
                addSignerInput.focus();
                e.preventDefault();
                return false;
            }
            if (hasEmail && !hasName) {
                alert("Please enter the Co-Signer Full Name along with their email address.");
                coSignerNameInput.focus();
                e.preventDefault();
                return false;
            }
            if (hasName && !hasEmail) {
                alert("Please enter the Co-Signer Email Address along with their full name.");
                addSignerInput.focus();
                e.preventDefault();
                return false;
            }
        }
    });
});