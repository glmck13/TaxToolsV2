#!/usr/bin/env python3

import sys
import os
import json
import html as html_lib
import requests
import subprocess
import urllib.parse
from datetime import datetime

AZ_DIR = os.environ.get("AZ_DIR", "")
os.environ["AZURE_CONFIG_DIR"] = AZ_DIR
os.environ["HOME"] = AZ_DIR

ASSET_VERSION = "2026.2"
DEFAULT_FORM_KEY = "2026"

# Form Registry: Add future tax years here without touching JS/CSS
FORMS = {
    "2026": {
        "key": "2026",
        "form_id": "3ldY-25hvUW4QYupAGZf1U5xmw6h3vpJrX1t2OAg0wFUOFg3Wk9MSVFINzhaQ1EzNVY5OE1PTkNOSi4u",
        "label": "2026 Tax Return",
        "tax_year": "2026",
        "prior_year": "2025",
        "next_year": "2027",
        "as_of_date": "12/31/26",
        "has_2025_fields": False,
        "has_ev_charger": True,
    },
    "2025": {
        "key": "2025",
        "form_id": "3ldY-25hvUW4QYupAGZf1U5xmw6h3vpJrX1t2OAg0wFUOU5PNEtERTRKQUlWQ1pBMjBBT0YySVo3Sy4u",
        "label": "2025 Tax Return",
        "tax_year": "2025",
        "prior_year": "2024",
        "next_year": "2026",
        "as_of_date": "12/31/25",
        "has_2025_fields": True,
        "has_ev_charger": False,
    },
}

# Unified Question ID -> Canonical Key Mapping across 2025 and 2026 Forms
QUESTION_ID_MAP = {
    # Identity & Engagement
    "rddfc13984b6b482fb9fa313f904d1580": "client_name",
    "r5632cb4474d54645b24bbb16a5dd73f2": "email",  # 2025 form
    "r04c447fc0835485dae516a72f209dc48": "married",
    "rabe9bc0b74f7451ba2a4de88c849ddd7": "spouse_name",  # 2025 form
    # Branched Agreement Questions (Single vs. Married paths)
    "rf09f192772094799bca6780cde3a983f": "signed_agreement",
    "r55c3206d22d44e468d1a44ede301b9b8": "signed_agreement",
    "rad9fc99e98b64fa09be04245093c7ca4": "taxpayer_terms",
    "rc8d77f0c3c0646ed9ffb4c90134da067": "taxpayer_terms",
    "r30a8a5ec2f964d98a74436ba11fda85e": "spouse_terms",
    # Banking & Direct Deposit
    "r76e60e1195344d3083aaa0f530a7cf75": "use_bank_dd",
    "r59b1bc0e12c445c9bedc615e69de59b4": "bank_changed",
    "r5130abc8b55c4597b7bfb888d02302e2": "bank_name",
    "rf5c3c1005f4548768e830b67f4965cf6": "account_type",
    "rc96869b1ad1448bbbcbffa858e307f89": "routing_num",
    "r5c6236889714497eaf24a86ab30b57d7": "account_num",
    # Contact, Profile & Dependents
    "rbef376c9536e47398431453c6ef435ad": "contact_same",
    "r47441d423219405bb9541c46a446bcf0": "updated_contact_info",
    "rc8756d6eef3b43f49c468f6946769b9f": "first_year_client",
    "r95a813691ad14e489598cac8659770b1": "birthdates",
    "r13fe7d5700c2447cb2b9254858ea96ce": "dep_changes",
    "r9cda4d39c47843c380f0725c9cae9899": "updated_dep_info",
    "r01eece54e02e477d9a0d448541ca8ba4": "kiddie_tax",
    # Tax Year Activity, Deductions & Real Estate
    "r2dca12a392294063961022b27dec4839": "form_1095a",  # 2025 form
    "rbf3d7159b890406487121d4b006691ad": "high_deductible",
    "r43ae78ed26664cc687d177cef3623374": "sit_diff_flag",
    "r6deec1dd55814665a154692ab769ecee": "updated_sit_diff_info",
    "rcd1283bc3375488ab47e6fea7f84e1e9": "new_car_flag",
    "r4c8ed305b64a4dbd8ddf38724bbed42d": "vin_number",
    "r14f33bcdd6734cd0be7fd918102f14f2": "home_buy_sell_refi",
    "rfd799499353745029d29bead49cf5f4b": "energy_improvements",
    "r41f40c25b65b4fe68ff39893437c1e8d": "ev_charger_2026",  # 2026 form
    "r375a4e164d8843a7ba00b91a39ebf078": "rental_business_home",  # 2025 form
    "r0a718356baa840898f933b40c4afeeaa": "non_employer_ira",
    "r01f2bb1c27234dfdb6e828d5b41ac925": "retirement_rollover",
    "r621c6ee68a414e5193786d758dfae24f": "roth_conversion",
    "r569295a2cc8d453188f92e50d23c713b": "sec_529_flag",
    "r77a4c5838e7d4457b2a83ae90ff7e3a8": "sec_529_details",
    "r217d73eb0ae646b1977dedbf09def722": "qcd_charity_ira",
    # Estimated Taxes & Forward Planning
    "r8f1c9fcdd21143fa948e889e672e614e": "est_tax_flag",
    "re7f5703ac2c241c5bc9b2128838b6902": "est_tax_details",
    "r897ab462ebe340d78d68f567213f585b": "income_diff_next_flag",
    "r14ccf072c2cd477d81f88754e759f2d5": "updated_next_income_info",
    "r84dfdaee7c6f428d82c8f2620478a12c": "estate_wealth_talk",
    "r7d89b919cf85421aa9934ea7aa9ef487": "future_charity_intent",
    "r92da72a1acca42f19b9958e710704fa1": "employer_benefit_talk",
    # Compliance & Administration
    "r4d3f5aa0ea5146af962c2fb4e0a73db4": "overtime_tips",
    "r63cd88dc4ae64a69b5e9c95df003089f": "household_employees",
    "r285ecb738f4e4b82856375eda81cd322": "foreign_accounts",
    "r529dc66ce6ce48a0b0dbc010354ece79": "gifts_over_19k",
    "r2126f1864ac84b3da2d34b3126bd4e75": "irs_auth",
    "r865aa21795b746feaedb005a41aafb7f": "pres_fund_taxpayer",
    "rb83a5e37ebee46dd9e36c49229f1bc79": "pres_fund_spouse",
    "rb084a34f42314238bd47a0ac0d83c7d6": "final_package_pref",
    "rf7be74b15dec4daa9f93bb7b3b8190fc": "additional_comments",
    "r8317f2e8061f4b9e97fef60dc3ce2903": "customer_key",
}


def render_error(title, detail=""):
    sys.stdout.write("Content-Type: text/html; charset=utf-8\n\n")
    sys.stdout.write(f"<h3>{html_lib.escape(title)}</h3>")
    if detail:
        sys.stdout.write(f"<p>{html_lib.escape(detail)}</p>")
    sys.exit(1)


def safe_json_for_script(obj):
    return json.dumps(obj).replace("</", "<\\/")


# Parse incoming CGI query parameters (supports ?form=2026 or ?year=2026, plus ?key=... or ?idx=...)
query_params = urllib.parse.parse_qs(os.environ.get("QUERY_STRING", ""))
requested_form = query_params.get("form", query_params.get("year", [DEFAULT_FORM_KEY]))[0]
form_key = requested_form if requested_form in FORMS else DEFAULT_FORM_KEY
active_form = FORMS[form_key]

requested_customer_key = (query_params.get("key", [None])[0] or "").strip()
embed_mode = query_params.get("embed", ["0"])[0] in ("1", "true", "yes")

raw_idx = query_params.get("idx", [None])[0]
client_index = None
if raw_idx is not None:
    try:
        client_index = int(raw_idx)
    except ValueError:
        client_index = None

# Acquire Azure CLI credentials
TENANT = subprocess.run(
    ["az", "account", "show", "--query", "tenantId", "-o", "tsv"],
    capture_output=True, text=True, check=False, timeout=5
).stdout.strip()

TOKEN = subprocess.run(
    ["az", "account", "get-access-token", "--resource", "https://forms.office.com", "--query", "accessToken", "--output", "tsv"],
    capture_output=True, text=True, check=False, timeout=5
).stdout.strip()

USERID = subprocess.run(
    ["az", "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"],
    capture_output=True, text=True, check=False, timeout=5
).stdout.strip()

if not (TENANT and TOKEN and USERID and active_form.get("form_id")):
    render_error("Authentication or Configuration Error")

headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Fetch all pages of responses (@odata.nextLink pagination)
raw_responses = []
form_id = active_form["form_id"]
next_url = f"https://forms.office.com/formapi/api/{TENANT}/users/{USERID}/light/forms('{form_id}')/responses?$expand=answers"

try:
    while next_url:
        rsp = requests.get(next_url, headers=headers, timeout=15)
        rsp.raise_for_status()
        rsp_json = rsp.json()
        raw_responses.extend(rsp_json.get("value", []))
        next_url = rsp_json.get("@odata.nextLink")
except (requests.RequestException, ValueError) as exc:
    render_error(f"Error fetching {active_form['label']} responses", str(exc))

# Normalize per-respondent records
respondents = []
for idx, resp in enumerate(raw_responses):
    record = {"_id": idx}
    try:
        ans_list = json.loads(resp.get("answers", "[]"))
    except (ValueError, TypeError):
        ans_list = []

    for ans in ans_list:
        qid = ans.get("questionId", "")
        val = (ans.get("answer1") or "").strip()
        key = QUESTION_ID_MAP.get(qid)
        if key and val:
            if key not in record or not record[key]:
                record[key] = val

    if not record.get("client_name"):
        record["client_name"] = f"Respondent #{idx + 1}"
    respondents.append(record)

# Resolve ?key=... (customer_key) to matching respondent index (preferring latest submission if duplicated)
total_answers = len(respondents)
not_found_key = None

if requested_customer_key:
    target_lower = requested_customer_key.lower()
    matched_idx = None
    for idx, rec in enumerate(respondents):
        rec_key = (rec.get("customer_key") or "").strip().lower()
        if rec_key == target_lower:
            matched_idx = idx
    if matched_idx is not None:
        client_index = matched_idx
    else:
        client_index = None
        not_found_key = requested_customer_key
elif client_index is not None:
    if total_answers == 0:
        client_index = None
    elif client_index < 0 or client_index >= total_answers:
        client_index = 0

# Build client bootstrap payload
public_form_meta = {k: v for k, v in active_form.items() if k != "form_id"}
available_forms = [{"key": k, "label": v["label"]} for k, v in FORMS.items()]

boot_payload = {
    "formKey": form_key,
    "formMeta": public_form_meta,
    "availableForms": available_forms,
    "respondents": respondents,
    "initialIdx": client_index,
    "notFoundKey": not_found_key,
    "embedMode": embed_mode,
    "generatedDate": datetime.now().strftime("%m/%d/%Y"),
}

controls_style = ' style="display:none;"' if embed_mode else ""
body_class = ' class="embed-mode"' if embed_mode else ""

html_output = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html_lib.escape(active_form['tax_year'])} Tax Q&amp;A One-Page Summary</title>
    <link rel="stylesheet" href="/css/taxqanda.css?v={ASSET_VERSION}">
</head>
<body{body_class}>

    <header>
        <div class="header-left">
            <h2 id="header-title">{html_lib.escape(active_form['tax_year'])} Tax Q&amp;A Summary</h2>
            <span id="header-meta" class="meta-tag" style="display:none;"></span>
            <div id="status-strip" class="status-strip" style="display:none;"></div>
        </div>
        <div id="controls-container" class="controls no-print"{controls_style}></div>
    </header>

    <div id="highlights-banner" class="highlights-banner"></div>

    <div class="bento-grid">
        <!-- Column 1: Client & Household -->
        <div class="bento-col">
            <div class="card">
                <h3>1. Engagement &amp; Identity</h3>
                <div id="group-engagement"></div>
            </div>
            <div class="card card-grow">
                <h3>2. Family &amp; Dependents</h3>
                <div id="group-family"></div>
            </div>
        </div>

        <!-- Column 2: Tax Year Activity & Deductions -->
        <div class="bento-col">
            <div class="card card-grow">
                <h3>3. Income, Deductions &amp; Real Estate</h3>
                <div id="group-tax-activity"></div>
            </div>
        </div>

        <!-- Column 3: Planning, Banking & Compliance -->
        <div class="bento-col">
            <div class="card">
                <h3>4. Est. Taxes &amp; Forward Planning</h3>
                <div id="group-planning"></div>
            </div>
            <div class="card card-grow">
                <h3>5. Banking &amp; Compliance</h3>
                <div id="group-banking-compliance"></div>
            </div>
        </div>
    </div>

    <script>
        window.TAX_QANDA_BOOT = {safe_json_for_script(boot_payload)};
    </script>
    <script src="/js/taxqanda.js?v={ASSET_VERSION}"></script>
</body>
</html>
"""

sys.stdout.write("Content-Type: text/html; charset=utf-8\n\n")
sys.stdout.write(html_output)
