#!/usr/bin/env python3
"""
Master Checklist Processing Pipeline:
1. Reads ProConnect cURL headers from stdin.
2. Queries QBO API to map QBO_Id <-> ProConnect ClientEntityId and resolve customer emails (merging PrimaryEmailAddr and Notes.signers).
3. Iterates over qbosp.csv (master customer list).
4. Fetches raw JSON checklists from ProConnect API (logging [INFO] and continuing without staging JSON/.xlsx if no checklist exists yet).
5. When checklist data is returned, stages raw checklist JSON payloads locally in checklists/{qbo_id}.json (translating QBO ID if --xlate is provided).
6. Ensures /{TAX_YEAR} subfolder exists under client folder on SharePoint.
7. Resolves folder sharing link (reusing cached folder_url, reusing existing SharePoint 'edit' link covering all
   QBO emails with folder_link_id=null, or creating a new link via 'm365 spo folder sharinglink add').
8. If checklist data is present (and not already uploaded unless --force-upload is set), builds formatted .xlsx
   workbook with openpyxl, uploads via m365 CLI, and generates direct Excel Online edit link (Doc.aspx?action=edit).
9. Maintains sp_links.json as a read-write state manifest keyed by Production QBO Customer Id.
10. Includes --cleanup mode to revoke script-created sharing links (where folder_link_id is non-null), delete uploaded
    checklist files, and reset folder_url and folder_link_id to null in sp_links.json.
"""

import os
import re
import sys
import json
import time
import shlex
import tempfile
import subprocess
import urllib.parse
import urllib.request
import urllib.error
import argparse
from typing import Dict, Any, Optional, Tuple, List, Set

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.formatting.rule import CellIsRule
except ImportError:
    print("[ERROR] 'openpyxl' is required. Install via: pip install openpyxl", file=sys.stderr)
    sys.exit(1)


# ==========================================
# CONSTANTS & ENVIRONMENT SETUP
# ==========================================
TAX_URL = "https://protaxdata.api.intuit.com"
DOC_URL = "https://protaxonlinecommon.api.intuit.com"

QBO_APIBASE = os.environ.get("QBO_APIBASE", "").rstrip("/")
QBO_REALMID = os.environ.get("QBO_REALMID", "")
QBO_ACCESS_TOKEN = os.environ.get("QBO_ACCESS_TOKEN", "")

SITE_URL = os.environ.get("SITE_URL", "")
DOC_LIBRARY = os.environ.get("DOC_LIBRARY", "Shared Documents")
TAX_YEAR = os.environ.get("TAX_YEAR", "2026")


# ==========================================
# ID MAPPING & STATE MANIFEST HELPERS
# ==========================================
def load_id_mapping(xlate_path: str) -> Dict[str, Any]:
    """Loads production-to-sandbox ID mapping file."""
    if xlate_path and os.path.exists(xlate_path):
        try:
            with open(xlate_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARNING] Failed reading ID mapping file {xlate_path}: {e}", file=sys.stderr)
    elif xlate_path:
        print(f"[WARNING] ID mapping file not found: {xlate_path}", file=sys.stderr)
    return {"customers": {}}


def load_links_manifest(json_path: str) -> Dict[str, Dict[str, Any]]:
    """Loads existing sp_links.json state manifest if present."""
    if json_path and os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            print(f"[WARNING] Failed reading existing links manifest {json_path}: {e}", file=sys.stderr)
    return {}


def save_links_manifest(json_path: str, data: Dict[str, Dict[str, Any]]):
    """Atomically persists sp_links.json state manifest to disk."""
    parent_dir = os.path.dirname(os.path.abspath(json_path))
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    tmp_path = f"{json_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, json_path)


# ==========================================
# SHARING LINK BACKOUT & CLEANUP HELPERS
# ==========================================
def remove_specific_sharing_link(site_url: str, folder_relative_path: str, link_id: str) -> bool:
    """
    Revokes a specific script-created sharing link by ID on a target folder using M365 CLI.
    Executed exclusively during --cleanup mode when folder_link_id is non-null.
    """
    clean_site_url = site_url.rstrip("/")
    clean_folder_path = "/" + urllib.parse.unquote(folder_relative_path).lstrip("/")

    remove_cmd = [
        "m365", "spo", "folder", "sharinglink", "remove",
        "--webUrl", clean_site_url,
        "--folderUrl", clean_folder_path,
        "--id", link_id,
        "--force"
    ]

    try:
        subprocess.run(remove_cmd, capture_output=True, text=True, check=True)
        print(f"  ├─ Removed script-created Sharing Link ID '{link_id}' from {clean_folder_path}", file=sys.stderr)
        return True
    except subprocess.CalledProcessError as e:
        err_msg = (e.stderr or e.stdout or "").strip()
        print(f"  ├─ [ERROR] Failed to remove link '{link_id}': {err_msg}", file=sys.stderr)
        return False


def run_cleanup_mode(json_file_path: str, site_url: str):
    """
    Reads sp_links.json, revokes script-created sharing links (only where folder_link_id is non-null),
    deletes the created checklist .xlsx files from SharePoint, and resets folder_url and folder_link_id
    to null in sp_links.json.
    """
    if not os.path.exists(json_file_path):
        print(f"[ERROR] JSON file not found for cleanup: {json_file_path}", file=sys.stderr)
        sys.exit(1)

    if not site_url:
        print("[ERROR] SharePoint site URL is required. Pass --site-url or set SITE_URL.", file=sys.stderr)
        sys.exit(1)

    clean_site_url = site_url.rstrip("/")
    parsed_site = urllib.parse.urlparse(clean_site_url)
    site_path = parsed_site.path.rstrip("/")

    try:
        with open(json_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to read {json_file_path}: {e}", file=sys.stderr)
        sys.exit(1)

    if not data or not isinstance(data, dict):
        print(f"[INFO] {json_file_path} is empty. Nothing to clean up.", file=sys.stderr)
        return

    print(f"\n[CLEANUP] Starting backout for {len(data)} entry/entries from {json_file_path}...\n", file=sys.stderr)

    for qbo_id, raw_entry in data.items():
        entry = raw_entry if isinstance(raw_entry, dict) else {}
        folder_path = str(entry.get("folder_path") or "").strip()
        raw_link_id = entry.get("folder_link_id")
        folder_link_id = str(raw_link_id).strip() if raw_link_id is not None else ""
        checklist_url = str(entry.get("checklist_url") or "").strip()

        print(f"[*] Processing Cleanup for QBO ID: {qbo_id}", file=sys.stderr)

        # 1. Revoke script-created sharing link only if folder_link_id is non-null
        if folder_link_id:
            if folder_path:
                remove_specific_sharing_link(clean_site_url, folder_path, folder_link_id)
            else:
                print(f"  ├─ [CLEANUP WARNING] Missing folder_path for QBO ID {qbo_id}; cannot remove link ID '{folder_link_id}'.", file=sys.stderr)
        else:
            print(f"  ├─ Pre-existing or unset sharing link (folder_link_id is null) for QBO ID {qbo_id}; leaving SharePoint link untouched.", file=sys.stderr)

        # 2. Extract site-relative path and delete target .xlsx file via --url
        if checklist_url and ("sourcedoc=" in checklist_url or "id=" in checklist_url):
            parsed_checklist = urllib.parse.urlparse(checklist_url)
            qs = urllib.parse.parse_qs(parsed_checklist.query)
            raw_file_id = (qs.get("sourcedoc") or qs.get("id") or [""])[0]
            file_server_relative = urllib.parse.unquote(raw_file_id)

            if site_path and file_server_relative.startswith(site_path):
                file_site_relative = file_server_relative[len(site_path):]
            else:
                file_site_relative = file_server_relative

            if file_site_relative:
                delete_cmd = [
                    "m365", "spo", "file", "remove",
                    "--webUrl", clean_site_url,
                    "--url", file_site_relative,
                    "--force"
                ]
                try:
                    subprocess.run(delete_cmd, capture_output=True, text=True, check=True)
                    print(f"  ├─ Successfully deleted checklist file: {file_site_relative}", file=sys.stderr)
                except subprocess.CalledProcessError as e:
                    err_msg = (e.stderr or e.stdout or "").strip()
                    print(f"  ├─ [ERROR] Failed deleting file {file_site_relative}: {err_msg}", file=sys.stderr)

        # 3. Reset folder_url and folder_link_id to null (and clear checklist_url) in sp_links.json
        data[qbo_id] = {
            "folder_path": folder_path,
            "folder_url": None,
            "folder_link_id": None,
            "checklist_url": ""
        }
        save_links_manifest(json_file_path, data)
        print(f"  └─ Reset folder_url and folder_link_id to null in {json_file_path}", file=sys.stderr)

    print(f"\n[CLEANUP COMPLETE] Finished backing out links and files. Updated {json_file_path}.", file=sys.stderr)


# ==========================================
# CURL HEADER PARSER (ProConnect Auth)
# ==========================================
def parse_curl_headers() -> Dict[str, str]:
    """Parses cURL command input from stdin to extract authorization headers."""
    if sys.stdin.isatty():
        print("[WARNING] Stdin is a terminal. Paste cURL headers and press Ctrl+D:", file=sys.stderr)
    curl_input = sys.stdin.read()

    headers = {}
    try:
        tokens = shlex.split(curl_input)
    except ValueError:
        return headers

    for i, token in enumerate(tokens):
        if token in ('-H', '--header') and i + 1 < len(tokens):
            header_string = tokens[i + 1]
            if ':' in header_string:
                key, value = header_string.split(':', 1)
                headers[key.strip().lower()] = value.strip()
        elif token in ('-b', '--cookie') and i + 1 < len(tokens):
            if 'cookie' not in headers:
                headers['cookie'] = tokens[i + 1].strip()

    headers.pop('accept-encoding', None)
    headers.pop('host', None)
    return headers


def fetch_intuit_json(url: str, headers: Dict[str, str], allow_404: bool = False) -> Optional[Any]:
    """Executes HTTP GET against Intuit ProConnect endpoints."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if allow_404 and e.code == 404:
            return None
        print(f"[FETCH ERROR] Failed request to {url}: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[FETCH ERROR] Failed request to {url}: {e}", file=sys.stderr)
        return None


# ==========================================
# QUICKBOOKS ONLINE API HELPERS
# ==========================================
def qbo_api_query(query_str: str) -> Dict[str, Any]:
    """Executes authenticated SQL query against QuickBooks Online API."""
    if not QBO_APIBASE or not QBO_REALMID or not QBO_ACCESS_TOKEN:
        print("[ERROR] Missing QBO environment variables (QBO_APIBASE, QBO_REALMID, QBO_ACCESS_TOKEN).", file=sys.stderr)
        sys.exit(1)

    encoded_query = urllib.parse.quote(query_str)
    url = f"{QBO_APIBASE}/company/{QBO_REALMID}/query?query={encoded_query}"
    
    headers = {
        "Authorization": f"Bearer {QBO_ACCESS_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"[QBO ERROR] HTTP {e.code}: {error_body}", file=sys.stderr)
        raise Exception(f"QBO Query Failed: {error_body}")


def get_qbo_customer_metadata() -> Dict[str, Dict[str, str]]:
    """
    Queries active QBO customers and builds metadata lookup:
    QBO_Id -> { "client_entity_id": ..., "email": ... }
    Merges PrimaryEmailAddr and all signer emails from Notes JSON.
    """
    print("[INFO] Querying QBO active customer records...", file=sys.stderr)
    res = qbo_api_query("select * from Customer WHERE Active = true MAXRESULTS 1000")
    customers = res.get("QueryResponse", {}).get("Customer", [])

    qbo_meta = {}
    for c in customers:
        qbo_id = str(c["Id"])
        client_entity_id = c.get("ClientEntityId") or ""
        
        raw_c_email = c.get("PrimaryEmailAddr", {}).get("Address", "")
        primary_emails = [
            addr.strip() for addr in raw_c_email.split(",") if addr.strip()
        ] if raw_c_email else []
        
        notes_emails = []
        if c.get("Notes"):
            try:
                notes_obj = json.loads(c["Notes"])
                if isinstance(notes_obj, str):
                    notes_obj = json.loads(notes_obj)
                if isinstance(notes_obj, dict):
                    if not client_entity_id:
                        client_entity_id = str(notes_obj.get("client_id") or notes_obj.get("ClientEntityId") or "")
                    
                    signers = notes_obj.get("signers", [])
                    if isinstance(signers, list):
                        for signer in signers:
                            if isinstance(signer, dict):
                                s_email = str(signer.get("email", "")).strip()
                                if s_email:
                                    notes_emails.append(s_email)
            except Exception:
                pass

        raw_emails = primary_emails + notes_emails
        seen = set()
        unique_emails = []
        for email in raw_emails:
            lower_email = email.lower()
            if lower_email not in seen:
                seen.add(lower_email)
                unique_emails.append(email)

        effective_email = ",".join(unique_emails)

        qbo_meta[qbo_id] = {
            "client_entity_id": str(client_entity_id),
            "email": effective_email
        }

    print(f"[INFO] Fetched metadata for {len(qbo_meta)} QBO customer(s).", file=sys.stderr)
    return qbo_meta


# ==========================================
# EXCEL GENERATOR (openpyxl with Dropdown DataValidation & Color Rules)
# ==========================================
def clean_item_text(text: str) -> str:
    """Strips boilerplate instruction prefixes from document requests."""
    if not text:
        return ""
    pattern = r'^(?:Please\s+)?(?:upload|provide|submit|share)(?:\s+all|\s+your|\s+us)?\s+'
    return re.sub(pattern, '', str(text), flags=re.IGNORECASE).strip()


def build_excel_workbook(data: Dict[str, Any], output_path: str):
    """Generates an Excel checklist workbook matching the styled layout with conditional dropdown formatting."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Document Checklist"
    ws.views.sheetView[0].showGridLines = True

    client_name = str(data.get("client_name") or "Unknown Client")

    font_title = Font(name="Segoe UI", size=16, bold=True, color="1E3A8A")
    font_label_bold = Font(name="Segoe UI", size=10, bold=True, color="0F172A")
    font_label_regular = Font(name="Segoe UI", size=10, color="0F172A")
    font_inst = Font(name="Segoe UI", size=10, color="1E3A8A")
    font_cat = Font(name="Segoe UI", size=11, bold=True, color="334155")
    font_tbl_header = Font(name="Segoe UI", size=10, bold=True, color="0F172A")
    font_data = Font(name="Calibri", size=11, color="000000")

    fill_label = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    fill_inst = PatternFill(start_color="EFF6FF", end_color="EFF6FF", fill_type="solid")
    fill_cat = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")
    fill_tbl_header = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

    border_side = Side(border_style="thin", color="CBD5E1")
    border_all = Border(left=border_side, right=border_side, top=border_side, bottom=border_side)

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")

    dv = DataValidation(type="list", formula1='"Pending, Completed"', allow_blank=True)
    ws.add_data_validation(dv)

    ws["A1"] = "Client Tax Document Checklist"
    ws["A1"].font = font_title

    ws.merge_cells("B3:D3")
    ws["A3"] = "Client Name"
    ws["A3"].font = font_label_bold
    ws["A3"].fill = fill_label
    ws["A3"].border = border_all

    ws["B3"] = client_name
    ws["B3"].font = font_label_regular
    for col in range(2, 5):
        ws.cell(row=3, column=col).border = border_all

    ws.merge_cells("A5:D5")
    inst_cell = ws["A5"]
    inst_cell.value = "Instructions: Please gather the requested tax documents listed below. Update status to Completed as you collect each item and submit supporting files to your tax preparer."
    inst_cell.font = font_inst
    inst_cell.fill = fill_inst
    inst_cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[5].height = 28

    checklist_obj = data.get("checklist") if "checklist" in data else data
    source_docs = checklist_obj.get("sourceDocuments") if isinstance(checklist_obj, dict) else []
    source_docs = source_docs or []

    categories = {}
    for doc in source_docs:
        if isinstance(doc, dict):
            doc_type = str(doc.get("sourceDocumentType") or "Other").upper()
            categories.setdefault(doc_type, []).append(doc)

    item_counter = 1
    r = 7
    status_cells = []

    for category, docs in categories.items():
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        cat_cell = ws.cell(row=r, column=1, value=f"Category: {category}")
        cat_cell.font = font_cat
        cat_cell.fill = fill_cat
        cat_cell.alignment = align_center
        ws.row_dimensions[r].height = 24
        r += 1

        headers_def = [
            ("Status", align_center),
            ("#", align_center),
            ("Document Requested", align_left),
            ("Notes / Comments", align_left)
        ]

        for col_idx, (h_text, h_align) in enumerate(headers_def, 1):
            cell = ws.cell(row=r, column=col_idx, value=h_text)
            cell.font = font_tbl_header
            cell.fill = fill_tbl_header
            cell.border = border_all
            cell.alignment = h_align
        ws.row_dimensions[r].height = 20
        r += 1

        for doc in docs:
            raw_text = doc.get("text", "")
            display_text = clean_item_text(raw_text)

            c1 = ws.cell(row=r, column=1, value="Pending")
            c1.font = font_data
            c1.alignment = align_center
            c1.border = border_all
            dv.add(c1)
            status_cells.append(f"A{r}")

            c2 = ws.cell(row=r, column=2, value=item_counter)
            c2.font = font_data
            c2.alignment = align_center
            c2.border = border_all

            c3 = ws.cell(row=r, column=3, value=display_text)
            c3.font = font_data
            c3.alignment = align_left
            c3.border = border_all

            c4 = ws.cell(row=r, column=4, value=None)
            c4.font = font_data
            c4.alignment = align_left
            c4.border = border_all

            ws.row_dimensions[r].height = 20
            item_counter += 1
            r += 1

        r += 1

    # Apply Conditional Color Rules to Status Column
    if status_cells:
        cf_range = f"A8:A{r}"
        
        fill_pending = PatternFill(start_color="FFEDD5", end_color="FFEDD5", fill_type="solid")
        font_pending = Font(name="Calibri", size=11, bold=True, color="9A3412")
        rule_pending = CellIsRule(operator='equal', formula=['"Pending"'], fill=fill_pending, font=font_pending)
        
        fill_completed = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
        font_completed = Font(name="Calibri", size=11, bold=True, color="166534")
        rule_completed = CellIsRule(operator='equal', formula=['"Completed"'], fill=fill_completed, font=font_completed)

        ws.conditional_formatting.add(cf_range, rule_pending)
        ws.conditional_formatting.add(cf_range, rule_completed)

    ws.column_dimensions['A'].width = 16.0
    ws.column_dimensions['B'].width = 8.0
    ws.column_dimensions['C'].width = 65.0
    ws.column_dimensions['D'].width = 30.0

    wb.save(output_path)


# ==========================================
# SHAREPOINT INTEGRATION (m365 CLI via Sharing Links)
# ==========================================
def ensure_sharepoint_folder(site_url: str, folder_path: str):
    """Ensures a subfolder (e.g. Shared Documents/Client_Folder/2026) exists on SharePoint."""
    cmd = [
        "m365", "spo", "folder", "add",
        "--webUrl", site_url,
        "--folderUrl", folder_path
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError:
        pass


def _extract_recipient_emails(link_item: Dict[str, Any]) -> Set[str]:
    """Extracts all lowercased recipient email addresses from a Graph/SPO sharing link object."""
    emails: Set[str] = set()

    def _collect_from_identity(identity_obj: Any):
        if not isinstance(identity_obj, dict):
            return
        user_obj = identity_obj.get("user")
        if isinstance(user_obj, dict):
            for key in ("email", "userPrincipalName", "loginName"):
                val = str(user_obj.get(key) or "").strip().lower()
                if "@" in val:
                    # Strip SharePoint claims prefix (e.g., i:0#.f|membership|user@domain.com)
                    if "|" in val:
                        val = val.split("|")[-1]
                    emails.add(val)
        for key in ("email", "userPrincipalName"):
            val = str(identity_obj.get(key) or "").strip().lower()
            if "@" in val:
                emails.add(val)

    for list_key in ("grantedToIdentitiesV2", "grantedToIdentities"):
        identities = link_item.get(list_key)
        if isinstance(identities, list):
            for ident in identities:
                _collect_from_identity(ident)

    single_ident = link_item.get("grantedToV2") or link_item.get("grantedTo")
    if single_ident:
        _collect_from_identity(single_ident)

    return emails


def find_existing_edit_sharing_link(
    site_url: str,
    folder_relative_path: str,
    required_emails: List[str]
) -> Optional[str]:
    """
    Queries existing sharing links on folder_relative_path via 'm365 spo folder sharinglink list'.
    Returns the webUrl of an existing link ONLY IF:
      1. It grants 'edit' / 'write' permissions (view-only links are ignored).
      2. Every email in required_emails is present on that link.
    Returns None if no qualifying link exists.
    """
    target_emails = {e.strip().lower() for e in required_emails if e.strip()}
    if not target_emails:
        return None

    list_cmd = [
        "m365", "spo", "folder", "sharinglink", "list",
        "--webUrl", site_url,
        "--folderUrl", folder_relative_path,
        "-o", "json"
    ]

    try:
        res = subprocess.run(list_cmd, capture_output=True, text=True, check=True)
        links = json.loads(res.stdout or "[]")
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        err_msg = (getattr(e, 'stderr', None) or getattr(e, 'stdout', None) or str(e)).strip()
        print(f"  ├─ [WARNING] Failed listing existing sharing links for {folder_relative_path}: {err_msg}", file=sys.stderr)
        return None

    if not isinstance(links, list):
        return None

    for item in links:
        if not isinstance(item, dict):
            continue

        link_obj = item.get("link") if isinstance(item.get("link"), dict) else {}
        link_type = str(link_obj.get("type") or item.get("type") or "").strip().lower()
        roles = [str(r).strip().lower() for r in (item.get("roles") or [])]
        web_url = str(link_obj.get("webUrl") or item.get("webUrl") or item.get("Url") or "").strip()

        is_edit = (link_type == "edit") or ("write" in roles)
        if not is_edit or not web_url:
            continue

        link_emails = _extract_recipient_emails(item)
        if target_emails.issubset(link_emails):
            return web_url

    return None


def resolve_folder_sharing_link(
    site_url: str,
    folder_relative_path: str,
    sp_folder: str,
    client_email: str,
    existing_link_entry: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[str], Optional[str]]:
    """
    Resolves folder sharing link in priority order:
      a) Reuse cached folder_url (and folder_link_id) from sp_links.json if present.
      b) Inspect existing SharePoint folder sharing links via 'm365 spo folder sharinglink list':
         if an 'edit' link exists that includes ALL QBO emails, reuse its URL and set folder_link_id = None.
      c) Otherwise create a new 'edit' sharing link via 'm365 spo folder sharinglink add' and store both
         folder_url and folder_link_id.
    Returns: (folder_url, folder_link_id)
    """
    clean_site_url = site_url.rstrip("/")
    existing = existing_link_entry or {}
    raw_cached_url = existing.get("folder_url")
    raw_cached_id = existing.get("folder_link_id")

    folder_url: Optional[str] = str(raw_cached_url).strip() if raw_cached_url else None
    folder_link_id: Optional[str] = str(raw_cached_id).strip() if raw_cached_id else None

    if folder_url:
        print(f"  ├─ Reusing cached sharing link from manifest for folder {sp_folder} (Link ID: {folder_link_id or 'null'})", file=sys.stderr)
        return folder_url, folder_link_id

    recipient_list = [e.strip() for e in (client_email or "").split(",") if e.strip()]
    if not recipient_list:
        print(f"  ├─ [WARNING] No client email found in QBO for folder '{sp_folder}'. Skipping sharing link resolution.", file=sys.stderr)
        return None, None

    existing_sp_url = find_existing_edit_sharing_link(clean_site_url, folder_relative_path, recipient_list)
    if existing_sp_url:
        print(f"  ├─ Found existing SharePoint edit link covering all QBO email(s) ({', '.join(recipient_list)}); storing folder_link_id=null", file=sys.stderr)
        return existing_sp_url, None

    recipients_arg = ",".join(recipient_list)
    add_link_cmd = [
        "m365", "spo", "folder", "sharinglink", "add",
        "--webUrl", clean_site_url,
        "--folderUrl", folder_relative_path,
        "--type", "edit",
        "--scope", "users",
        "--recipients", recipients_arg,
        "-o", "json"
    ]
    try:
        res = subprocess.run(add_link_cmd, capture_output=True, text=True, check=True)
        link_data = json.loads(res.stdout or "{}")
        if isinstance(link_data, list) and len(link_data) > 0:
            link_data = link_data[0]
        if isinstance(link_data, dict):
            extracted_id = str(link_data.get("id") or "").strip()
            folder_link_id = extracted_id if extracted_id else None
            link_obj = link_data.get("link")
            if isinstance(link_obj, dict):
                extracted_url = str(link_obj.get("webUrl") or "").strip()
            else:
                extracted_url = str(link_obj or link_data.get("Url") or link_data.get("webUrl") or "").strip()
            folder_url = extracted_url if extracted_url else None

        if folder_url:
            print(f"  ├─ Created new sharing link for folder {sp_folder} (ID: {folder_link_id or 'null'}, Recipients: {recipients_arg})", file=sys.stderr)
        else:
            folder_link_id = None
            print(f"  ├─ [M365 SHARING LINK ERROR] Command succeeded but returned no sharing URL for {sp_folder}.", file=sys.stderr)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        err_msg = (getattr(e, 'stderr', None) or getattr(e, 'stdout', None) or str(e)).strip()
        print(f"  ├─ [M365 SHARING LINK ERROR] Failed creating folder sharing link for {sp_folder}: {err_msg}", file=sys.stderr)
        folder_url = None
        folder_link_id = None

    return folder_url, folder_link_id


def process_sharepoint_folder_and_checklist(
    site_url: str,
    doc_lib: str,
    sp_folder: str,
    tax_year: str,
    client_email: str,
    local_file_path: Optional[str],
    remote_filename: str,
    existing_link_entry: Optional[Dict[str, Any]] = None,
    force_upload: bool = False
) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
    """
    1. Ensures /{TAX_YEAR} subfolder exists under client folder.
    2. Resolves folder sharing link (manifest cache -> existing SPO edit link -> create new link).
    3. If local_file_path is provided (checklist exists), uploads .xlsx unless checklist_url is already
       cached (and force_upload is False), and returns the direct Excel Online edit URL (Doc.aspx).
       If local_file_path is None (no checklist in ProConnect), preserves any existing checklist_url or returns "".
    Returns: (folder_relative_path, folder_url, folder_link_id, checklist_url)
    """
    clean_site_url = site_url.rstrip("/")
    base_folder_path = f"{doc_lib}/{sp_folder}".strip("/")
    year_folder_path = f"{base_folder_path}/{tax_year}"
    folder_relative_path = f"/{doc_lib}/{sp_folder}".replace("//", "/")

    ensure_sharepoint_folder(clean_site_url, year_folder_path)

    folder_url, folder_link_id = resolve_folder_sharing_link(
        clean_site_url, folder_relative_path, sp_folder, client_email, existing_link_entry
    )

    existing = existing_link_entry or {}
    existing_checklist_url = str(existing.get("checklist_url") or "").strip()

    # No ProConnect checklist available for this client: skip Excel upload, keep folder sharing intact
    if not local_file_path:
        return folder_relative_path, folder_url, folder_link_id, existing_checklist_url

    # Skip rebuilding/uploading .xlsx if already deployed in sp_links.json (avoids version bumps & preserves client edits)
    if existing_checklist_url and not force_upload:
        print(f"  ├─ Skipping .xlsx upload: checklist_url already present in manifest.", file=sys.stderr)
        return folder_relative_path, folder_url, folder_link_id, existing_checklist_url

    # Upload .xlsx via local named copy
    stage_dir = os.path.dirname(local_file_path)
    staged_file_path = os.path.join(stage_dir, remote_filename)

    if os.path.exists(staged_file_path):
        os.remove(staged_file_path)
    os.rename(local_file_path, staged_file_path)

    upload_cmd = [
        "m365", "spo", "file", "add",
        "--webUrl", clean_site_url,
        "--folder", year_folder_path,
        "--path", staged_file_path,
        "--overwrite"
    ]

    try:
        subprocess.run(upload_cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        err_msg = (e.stderr or e.stdout or "").strip()
        print(f"  ├─ [M365 UPLOAD ERROR] Failed uploading {remote_filename}: {err_msg}", file=sys.stderr)
        return folder_relative_path, folder_url, folder_link_id, existing_checklist_url
    finally:
        if os.path.exists(staged_file_path):
            os.remove(staged_file_path)

    # Construct Direct Excel Online Edit Link for the Checklist
    parsed_site = urllib.parse.urlparse(clean_site_url)
    site_path = parsed_site.path.rstrip('/')

    relative_file_path = f"{site_path}/{year_folder_path}/{remote_filename}".replace("//", "/")
    encoded_sourcedoc = urllib.parse.quote(relative_file_path, safe="")
    encoded_filename = urllib.parse.quote(remote_filename, safe="")

    checklist_url = (
        f"{clean_site_url}/_layouts/15/Doc.aspx"
        f"?sourcedoc={encoded_sourcedoc}&file={encoded_filename}&action=edit&mobileredirect=true"
    )

    return folder_relative_path, folder_url, folder_link_id, checklist_url


# ==========================================
# MAIN EXECUTION PIPELINE
# ==========================================
def parse_qbosp_csv(qbosp_path: str) -> Dict[str, Dict[str, str]]:
    """Parses colon-separated qbosp.csv (QBO_Id:Name:SP_Folder) into customer target lookup."""
    customers = {}
    if not os.path.exists(qbosp_path):
        print(f"[ERROR] qbosp.csv not found at: {qbosp_path}", file=sys.stderr)
        sys.exit(1)

    with open(qbosp_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line_str = line.strip()
            if not line_str or line_str.lower().startswith("qbo_id") or line_str.startswith("#"):
                continue
            parts = line_str.split(":", 4)
            if len(parts) >= 3 and parts[0].strip():
                qbo_id = parts[0].strip()
                c_name = parts[1].strip()
                sp_folder = parts[2].strip()
                customers[qbo_id] = {
                    "name": c_name,
                    "sp_folder": sp_folder
                }
    return customers


def main():
    parser = argparse.ArgumentParser(description="Process ProConnect checklists, build Excel files with dropdowns, upload to SharePoint via m365 CLI.")
    parser.add_argument("-q", "--qbosp", default="qbosp.csv", help="Path to qbosp.csv mapping file")
    parser.add_argument("-o", "--output-json", default="sp_links.json", help="Path for read-write JSON link state manifest")
    parser.add_argument("-c", "--checklists", default="checklists", help="Directory to stage raw checklist JSON files keyed by QBO ID")
    parser.add_argument("-x", "--xlate", default="", metavar="FILE", help="Path to id_mapping.json to translate production QBO IDs to sandbox QBO IDs when naming checklist JSON files.")
    parser.add_argument("--site-url", default=SITE_URL, help="SharePoint Site Web URL")
    parser.add_argument("--doc-lib", default=DOC_LIBRARY, help="SharePoint Document Library folder root (default: Shared Documents)")
    parser.add_argument("--tax-year", default=TAX_YEAR, help="Tax year parameter for API requests (default: 2026)")
    parser.add_argument("--force-upload", action="store_true", help="Rebuild and overwrite .xlsx on SharePoint even if checklist_url is already present in sp_links.json.")
    parser.add_argument("--cleanup", action="store_true", help="Run in cleanup mode: revoke script-created sharing links (where folder_link_id is non-null), delete uploaded checklist files, and reset links to null.")

    args = parser.parse_args()

    site_url = args.site_url
    doc_lib = args.doc_lib
    qbosp_path = args.qbosp
    out_json_path = args.output_json
    checklists = args.checklists
    xlate_path = args.xlate
    tax_year = args.tax_year
    force_upload = args.force_upload

    # If --cleanup is passed, execute the backout process and exit
    if args.cleanup:
        run_cleanup_mode(out_json_path, site_url)
        sys.exit(0)

    if not site_url:
        print("[ERROR] SharePoint site URL is required. Set SITE_URL env var or pass --site-url.", file=sys.stderr)
        sys.exit(1)

    headers = parse_curl_headers()
    if 'authorization' not in headers:
        print("[ERROR] Missing 'authorization' header in cURL input from stdin.", file=sys.stderr)
        sys.exit(1)

    id_mapping = load_id_mapping(xlate_path)
    cust_map = id_mapping.get("customers", {})
    if xlate_path:
        print(f"[INFO] Loaded ID mapping from {xlate_path} ({len(cust_map)} customer(s) mapped).", file=sys.stderr)

    # Load existing sp_links.json as a read-write state manifest
    sharepoint_links = load_links_manifest(out_json_path)
    if sharepoint_links:
        print(f"[INFO] Loaded {len(sharepoint_links)} existing link entry/entries from {out_json_path}.", file=sys.stderr)

    qbo_meta = get_qbo_customer_metadata()
    qbosp_customers = parse_qbosp_csv(qbosp_path)
    print(f"[INFO] Loaded {len(qbosp_customers)} master customer(s) from {qbosp_path}.", file=sys.stderr)

    print(f"[INFO] Fetching {tax_year} tax returns filter from ProConnect...", file=sys.stderr)
    prior_year = str(int(tax_year) - 1)
    returns_list = fetch_intuit_json(f"{TAX_URL}/v1/returns/filter/{prior_year}?use-oii-client-id=true", headers)
    if not isinstance(returns_list, list):
        print("[ERROR] Failed to retrieve valid returns list from ProConnect.", file=sys.stderr)
        sys.exit(1)

    returns_by_client_id = {}
    for ret in returns_list:
        if isinstance(ret, dict) and ret.get('id_client'):
            returns_by_client_id[str(ret['id_client'])] = ret

    os.makedirs(checklists, exist_ok=True)
    processed_count = 0

    print("\n[INFO] Starting checklist extraction, local JSON staging, Excel creation, and SharePoint upload loop...\n", file=sys.stderr)

    for prod_qbo_id, cust_info in qbosp_customers.items():
        c_name = cust_info["name"]
        sp_folder = cust_info["sp_folder"]

        # Resolve target QBO ID for local checklist JSON staging using --xlate if available
        target_qbo_id = str(cust_map.get(prod_qbo_id, prod_qbo_id))

        meta = qbo_meta.get(prod_qbo_id, {})
        proconnect_id = meta.get("client_entity_id", "")
        client_email = meta.get("email", "")

        ret_data = returns_by_client_id.get(proconnect_id) if proconnect_id else None
        return_name = ret_data.get('name') if ret_data else f'{tax_year} Tax Return'

        # Fetch ProConnect Checklist safely (404 means no checklist exists yet for this client/year)
        checklist_data = None
        if proconnect_id:
            checklist_url_api = f"{DOC_URL}/v1/doc-checklist/{proconnect_id}?currentYear={tax_year}"
            checklist_data = fetch_intuit_json(checklist_url_api, headers, allow_404=True)
            if not checklist_data:
                print(f"[INFO] No ProConnect checklist found for {c_name} (QBO ID: {prod_qbo_id}, ProConnect ID: {proconnect_id}) for {tax_year}; continuing without checklist...", file=sys.stderr)
        else:
            print(f"[INFO] No ProConnect ClientEntityId found in QBO for {c_name} (QBO ID: {prod_qbo_id}); continuing without checklist...", file=sys.stderr)

        has_checklist = bool(checklist_data)
        payload = None
        staged_json_path: Optional[str] = None

        if has_checklist:
            payload = {
                "client_id": proconnect_id,
                "client_name": c_name,
                "return_name": return_name,
                "checklist": checklist_data
            }

            # Stage raw checklist JSON locally only when ProConnect returned checklist data
            staged_json_path = os.path.join(checklists, f"{target_qbo_id}.json")
            try:
                with open(staged_json_path, "w", encoding="utf-8") as jf:
                    json.dump(payload, jf, indent=2)
            except Exception as je:
                print(f"[-] Warning: Failed staging checklist JSON to {staged_json_path}: {je}", file=sys.stderr)
                staged_json_path = None

        if not sp_folder:
            id_label = f"Prod ID: {prod_qbo_id} -> Sandbox ID: {target_qbo_id}" if target_qbo_id != prod_qbo_id else f"QBO ID: {prod_qbo_id}"
            staged_note = f"Staged JSON to {staged_json_path}." if staged_json_path else "No checklist staged."
            print(f"[INFO] No sp_folder for [{c_name}] ({id_label}). {staged_note} Continue...", file=sys.stderr)
            continue

        existing_entry = sharepoint_links.get(prod_qbo_id) or {}
        existing_checklist_url = str(existing_entry.get("checklist_url") or "").strip()

        processed_count += 1
        id_label = f"Prod ID: {prod_qbo_id} -> Sandbox ID: {target_qbo_id}" if target_qbo_id != prod_qbo_id else f"QBO ID: {prod_qbo_id}"
        print(f"[{processed_count}] Processing [{c_name}] ({id_label}) -> Folder: '{sp_folder}/{tax_year}'...", file=sys.stderr)

        tmp_xlsx_path: Optional[str] = None
        if has_checklist and payload and (not existing_checklist_url or force_upload):
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp_file:
                tmp_xlsx_path = tmp_file.name
            build_excel_workbook(payload, tmp_xlsx_path)

        try:
            target_filename = "Tax Checklist.xlsx"
            folder_path, folder_url, folder_link_id, checklist_url = process_sharepoint_folder_and_checklist(
                site_url,
                doc_lib,
                sp_folder,
                tax_year,
                client_email,
                tmp_xlsx_path,
                target_filename,
                existing_entry,
                force_upload=force_upload
            )

            sharepoint_links[prod_qbo_id] = {
                "folder_path": folder_path or "",
                "folder_url": folder_url,
                "folder_link_id": folder_link_id,
                "checklist_url": checklist_url
            }
            save_links_manifest(out_json_path, sharepoint_links)
            print(f"  ├─ Staged JSON   : {staged_json_path or '[None - no checklist]'}", file=sys.stderr)
            print(f"  ├─ Folder Path   : {folder_path}", file=sys.stderr)
            print(f"  ├─ Folder URL    : {folder_url or '[null / Not generated]'}", file=sys.stderr)
            print(f"  ├─ Folder Link ID: {folder_link_id or 'null'}", file=sys.stderr)
            print(f"  └─ Checklist URL : {checklist_url or '[None - no checklist]'}", file=sys.stderr)

        finally:
            if tmp_xlsx_path and os.path.exists(tmp_xlsx_path):
                os.remove(tmp_xlsx_path)

        time.sleep(0.1)

    print(f"\n[SUCCESS] Execution complete! Successfully processed {processed_count} client(s).", file=sys.stderr)
    print(f"  - Staged checklist JSONs in : {checklists}", file=sys.stderr)
    print(f"  - Keyed output JSON saved to: {out_json_path}", file=sys.stderr)


if __name__ == "__main__":
    main()