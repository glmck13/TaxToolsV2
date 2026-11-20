#!/usr/bin/env python3
"""
TARRANT ADVISORS — BATCH QBO DEMOGRAPHICS IMPORTER (HARDENED)
Reads a colon-delimited CSV (matching export_qbo_demographics.sh) and applies
sparse updates to QuickBooks Online ONLY for records with actual changes.

Usage:
    source /path/to/qboTokens.conf
    ./import_qbo_demographics.py <path/to/demographics.csv> [--apply] [--overwrite-text-notes]
"""

import argparse
import copy
import csv
import html
import json
import os
import re
import sys
from typing import Any

import requests

EXPECTED_HEADERS = [
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
    "zip",
]

VALID_ENTITIES = {
    "individual",
    "sm_llc",
    "s_corp",
    "partnership",
    "c_corp",
    "non_profit",
    "trust",
    "organization",
}


class QBOClient:
    def __init__(
        self,
        api_base: str,
        realm_id: str,
        access_token: str,
        fmt: str = "json",
        minor_version: str = "75",
    ):
        self.api_base = api_base.rstrip("/")
        self.realm_id = realm_id
        self.base_url = f"{self.api_base}/company/{realm_id}"
        self.minor_version = minor_version
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Accept": f"application/{fmt}",
                "Content-Type": f"application/{fmt}",
            }
        )

    def _handle_response(self, resp: requests.Response) -> dict[str, Any]:
        if resp.status_code == 401:
            raise RuntimeError(
                "QBO API returned 401 Unauthorized — QBO_ACCESS_TOKEN is expired or invalid."
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid JSON from QBO (HTTP {resp.status_code}): {resp.text[:300]}"
            ) from exc

        if isinstance(data, dict) and data.get("Fault"):
            err = data["Fault"].get("Error", [{}])[0]
            raise RuntimeError(
                f"QBO Fault (HTTP {resp.status_code}): "
                f"{err.get('Message', 'Unknown')} - {err.get('Detail', '')}"
            )

        if not resp.ok:
            raise RuntimeError(f"QBO HTTP {resp.status_code}: {resp.text[:300]}")

        return data

    def query(self, sql: str) -> dict[str, Any]:
        resp = self.session.get(
            f"{self.base_url}/query",
            params={"query": sql, "minorversion": self.minor_version},
            timeout=30,
        )
        return self._handle_response(resp)

    def update_customer(self, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self.session.post(
            f"{self.base_url}/customer",
            params={"minorversion": self.minor_version},
            json=payload,
            timeout=30,
        )
        return self._handle_response(resp)

    def fetch_all_customers(self) -> dict[str, dict[str, Any]]:
        """Fetches all active QBO customers in pages of 1000 keyed by string Id."""
        customers: dict[str, dict[str, Any]] = {}
        start_pos = 1
        page_size = 1000

        while True:
            sql = (
                f"select * from Customer where Active = true "
                f"startposition {start_pos} maxresults {page_size}"
            )
            resp = self.query(sql)
            batch = resp.get("QueryResponse", {}).get("Customer", [])
            if not batch:
                break
            for c in batch:
                cid = clean_str(c.get("Id", ""))
                if cid:
                    customers[cid] = c
            if len(batch) < page_size:
                break
            start_pos += page_size

        return customers


def clean_str(val: Any) -> str:
    if val is None:
        return ""
    s = html.unescape(str(val)).strip()
    if s.lower() == "null":
        return ""
    return re.sub(r"[\r\n]+", " ", s).strip()


def normalize_zip(z: str) -> str:
    z = clean_str(z)
    # Restore leading zero stripped by Excel on 5-digit US ZIPs (e.g., 2138 -> 02138)
    if re.fullmatch(r"\d{4}", z):
        return "0" + z
    return z


def normalize_entity(e: str) -> str:
    e = clean_str(e).lower()
    return e if e in VALID_ENTITIES else "individual"


def normalize_format(fmt: str) -> str:
    return "paper" if clean_str(fmt).lower() == "paper" else "electronic"


def parse_qbo_notes(notes_str: str) -> dict[str, Any]:
    """Mirrors parse_acct_num() in intake_lookup.py and parse_notes in export_qbo_demographics.sh."""
    raw = clean_str(notes_str)
    if not raw:
        return {"_valid_json": False, "_raw": "", "_obj": {}}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, str):
            parsed = json.loads(parsed.strip())
        if not isinstance(parsed, dict):
            return {"_valid_json": False, "_raw": raw, "_obj": {}}
    except Exception:
        return {"_valid_json": False, "_raw": raw, "_obj": {}}

    signers = parsed.get("signers") if isinstance(parsed.get("signers"), list) else []
    p_signer = signers[0] if len(signers) > 0 and isinstance(signers[0], dict) else {}
    c_signer = signers[1] if len(signers) > 1 and isinstance(signers[1], dict) else {}

    return {
        "_valid_json": True,
        "_raw": raw,
        "_obj": parsed,
        "entity": normalize_entity(parsed.get("entity", "")),
        "delivery_format": normalize_format(parsed.get("delivery_format", "")),
        "p_name": clean_str(p_signer.get("name", "")),
        "p_email": clean_str(p_signer.get("email", "")),
        "c_name": clean_str(c_signer.get("name", "")),
        "c_email": clean_str(c_signer.get("email", "")),
    }


def load_qbo_client_from_env() -> QBOClient:
    sandbox = os.environ.get("QBO_SANDBOX", "")
    default_base = (
        "https://sandbox-quickbooks.api.intuit.com/v3"
        if sandbox
        else "https://quickbooks.api.intuit.com/v3"
    )
    api_base = os.environ.get("QBO_APIBASE", default_base).strip()
    realm_id = os.environ.get("QBO_REALMID", "").strip()
    access_token = os.environ.get("QBO_ACCESS_TOKEN", "").strip()
    fmt = os.environ.get("QBO_FORMAT", "json").strip() or "json"

    missing = [
        name
        for name, val in [
            ("QBO_REALMID", realm_id),
            ("QBO_ACCESS_TOKEN", access_token),
        ]
        if not val
    ]
    if missing:
        sys.exit(
            f"Error: Missing required environment variable(s): {', '.join(missing)}.\n"
            "Did you source qboTokens.conf before running this script?"
        )

    return QBOClient(api_base=api_base, realm_id=realm_id, access_token=access_token, fmt=fmt)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-update QBO customer demographics from a colon-delimited CSV."
    )
    parser.add_argument("csv_file", help="Path to colon-delimited demographics CSV")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Write changes to QuickBooks Online (defaults to dry-run)",
    )
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying QuickBooks Online (default)",
    )
    parser.add_argument(
        "--overwrite-text-notes",
        action="store_true",
        help="Allow replacing non-empty, non-JSON plaintext Notes with structured JSON",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not os.path.isfile(args.csv_file):
        sys.exit(f"Error: CSV file not found: {args.csv_file}")

    client = load_qbo_client_from_env()
    apply_changes = args.apply
    env_label = "SANDBOX" if "sandbox-" in client.api_base else "PRODUCTION"

    print(f"Environment: {env_label} ({client.api_base}) | Realm ID: {client.realm_id}")
    print(f"Mode:        {'APPLY (live QBO updates)' if apply_changes else 'DRY-RUN (no changes will be written)'}")
    print(f"Reading {args.csv_file}...")

    with open(args.csv_file, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=":")
        if not reader.fieldnames or reader.fieldnames[:13] != EXPECTED_HEADERS:
            sys.exit(
                f"Error: Unexpected CSV headers.\n"
                f"  Expected: {':'.join(EXPECTED_HEADERS)}\n"
                f"  Found:    {':'.join(reader.fieldnames or [])}"
            )
        expected_col_count = len(reader.fieldnames)
        csv_rows = list(reader)

    # Pre-flight duplicate qbo_id check
    seen_ids: dict[str, int] = {}
    duplicates: list[str] = []
    for line_num, row in enumerate(csv_rows, start=2):
        qid = clean_str(row.get("qbo_id", ""))
        if qid:
            if qid in seen_ids:
                duplicates.append(f"ID {qid} (lines {seen_ids[qid]} and {line_num})")
            else:
                seen_ids[qid] = line_num
    if duplicates:
        sys.exit(
            "Error: Duplicate qbo_id values found in CSV. Resolve duplicates before running:\n  - "
            + "\n  - ".join(duplicates)
        )

    print(f"Loaded {len(csv_rows)} CSV rows. Fetching live QBO customer records...")
    qbo_by_id = client.fetch_all_customers()
    print(f"Fetched {len(qbo_by_id)} active QBO customers.\n")

    stats = {"unchanged": 0, "updated": 0, "skipped_missing": 0, "errors": 0}

    for line_num, row in enumerate(csv_rows, start=2):
        # Guard against extra colons ':' introduced during manual Excel edits
        if None in row or any(row.get(k) is None for k in EXPECTED_HEADERS):
            print(
                f"[Line {line_num}] ERROR: Column count mismatch (expected {expected_col_count} columns). "
                f"Check for stray ':' characters.",
                file=sys.stderr,
            )
            stats["errors"] += 1
            continue

        qbo_id = clean_str(row.get("qbo_id", ""))
        if not qbo_id:
            print(f"[Line {line_num}] SKIP: Missing qbo_id.")
            stats["skipped_missing"] += 1
            continue

        live = qbo_by_id.get(qbo_id)
        if not live:
            print(f"[Line {line_num}] SKIP (ID {qbo_id}): Not found among active QBO customers.")
            stats["skipped_missing"] += 1
            continue

        # Desired normalized values from CSV
        desired_display_name = clean_str(row.get("client_name", ""))
        if not desired_display_name:
            print(f"[Line {line_num}] SKIP (ID {qbo_id}): Blank client_name.")
            stats["skipped_missing"] += 1
            continue

        desired_entity = normalize_entity(row.get("entity_type", ""))
        desired_company_name = "" if desired_entity == "individual" else desired_display_name
        desired_friendly = clean_str(row.get("friendly_name", "")) or desired_display_name
        desired_email = clean_str(row.get("primary_email", ""))
        desired_phone = clean_str(row.get("phone_number", ""))
        desired_co_name = clean_str(row.get("co_signer_name", ""))
        desired_co_email = clean_str(row.get("co_signer_email", ""))
        desired_format = normalize_format(row.get("delivery_format", ""))
        desired_street = clean_str(row.get("street", ""))
        desired_city = clean_str(row.get("city", ""))
        desired_state = clean_str(row.get("state", ""))
        desired_zip = normalize_zip(row.get("zip", ""))

        # Current normalized values from live QBO object
        live_display_name = clean_str(live.get("DisplayName", ""))
        live_company_name = clean_str(live.get("CompanyName", ""))
        live_raw_email = clean_str((live.get("PrimaryEmailAddr") or {}).get("Address", ""))
        email_parts = [p.strip() for p in live_raw_email.split(",") if p.strip()] if live_raw_email else []
        live_email = email_parts[0] if email_parts else ""

        # Preserve secondary emails without duplicating if desired_email already includes them
        desired_email_set = {p.strip().lower() for p in desired_email.split(",") if p.strip()}
        extra_live_emails = [
            p for p in email_parts[1:] if p.lower() not in desired_email_set
        ]

        live_phone = clean_str((live.get("PrimaryPhone") or {}).get("FreeFormNumber", ""))
        live_bill = live.get("BillAddr") or {}
        live_street = clean_str(live_bill.get("Line1", ""))
        live_city = clean_str(live_bill.get("City", ""))
        live_state = clean_str(live_bill.get("CountrySubDivisionCode", ""))
        live_zip = clean_str(live_bill.get("PostalCode", ""))
        live_notes = parse_qbo_notes(live.get("Notes", ""))

        # Guard against silently destroying human-written free-text Notes
        has_plaintext_notes = (not live_notes["_valid_json"]) and bool(live_notes["_raw"])
        if has_plaintext_notes and not args.overwrite_text_notes:
            preview = (
                (live_notes["_raw"][:60] + "...")
                if len(live_notes["_raw"]) > 60
                else live_notes["_raw"]
            )
            print(
                f"[Line {line_num}] SKIP (ID {qbo_id}) {desired_display_name}: "
                f"Contains non-JSON plaintext Notes ({preview!r}). "
                f"Pass --overwrite-text-notes to replace.",
                file=sys.stderr,
            )
            stats["errors"] += 1
            continue

        # Build field-by-field diff
        diffs = []
        if desired_display_name != live_display_name:
            diffs.append(f"DisplayName: {live_display_name!r} -> {desired_display_name!r}")
        if desired_company_name != live_company_name:
            diffs.append(f"CompanyName: {live_company_name!r} -> {desired_company_name!r}")
        if desired_email != live_email:
            diffs.append(f"PrimaryEmail: {live_email!r} -> {desired_email!r}")
        if desired_phone != live_phone:
            diffs.append(f"PrimaryPhone: {live_phone!r} -> {desired_phone!r}")
        if desired_street != live_street:
            diffs.append(f"BillAddr.Line1: {live_street!r} -> {desired_street!r}")
        if desired_city != live_city:
            diffs.append(f"BillAddr.City: {live_city!r} -> {desired_city!r}")
        if desired_state != live_state:
            diffs.append(f"BillAddr.State: {live_state!r} -> {desired_state!r}")
        if desired_zip != live_zip:
            diffs.append(f"BillAddr.PostalCode: {live_zip!r} -> {desired_zip!r}")

        # Notes JSON metadata comparison
        if not live_notes["_valid_json"]:
            preview = (
                (live_notes["_raw"][:60] + "...")
                if len(live_notes["_raw"]) > 60
                else live_notes["_raw"]
            )
            diffs.append(f"Notes: ({preview or 'empty'}) -> structured JSON metadata")
        else:
            if desired_entity != live_notes["entity"]:
                diffs.append(f"Notes.entity: {live_notes['entity']!r} -> {desired_entity!r}")
            if desired_format != live_notes["delivery_format"]:
                diffs.append(
                    f"Notes.delivery_format: {live_notes['delivery_format']!r} -> {desired_format!r}"
                )
            if desired_friendly != live_notes["p_name"]:
                diffs.append(f"Notes.signers[0].name: {live_notes['p_name']!r} -> {desired_friendly!r}")
            if desired_email != live_notes["p_email"]:
                diffs.append(f"Notes.signers[0].email: {live_notes['p_email']!r} -> {desired_email!r}")
            if desired_co_name != live_notes["c_name"]:
                diffs.append(f"Notes.signers[1].name: {live_notes['c_name']!r} -> {desired_co_name!r}")
            if desired_co_email != live_notes["c_email"]:
                diffs.append(f"Notes.signers[1].email: {live_notes['c_email']!r} -> {desired_co_email!r}")

        if not diffs:
            stats["unchanged"] += 1
            continue

        print(f"[ID {qbo_id}] {desired_display_name}:")
        for d in diffs:
            print(f"    - {d}")

        if not apply_changes:
            stats["updated"] += 1
            continue

        # Construct Notes JSON while preserving any extra top-level keys on existing JSON
        notes_obj = copy.deepcopy(live_notes["_obj"]) if live_notes["_valid_json"] else {}
        existing_signers = notes_obj.get("signers") if isinstance(notes_obj.get("signers"), list) else []

        p_signer_obj = copy.deepcopy(existing_signers[0]) if len(existing_signers) > 0 and isinstance(existing_signers[0], dict) else {}
        p_signer_obj["name"] = desired_friendly
        p_signer_obj["email"] = desired_email
        signers_payload = [p_signer_obj]

        if desired_co_name or desired_co_email:
            c_signer_obj = copy.deepcopy(existing_signers[1]) if len(existing_signers) > 1 and isinstance(existing_signers[1], dict) else {}
            c_signer_obj["name"] = desired_co_name
            c_signer_obj["email"] = desired_co_email
            signers_payload.append(c_signer_obj)

        notes_obj["entity"] = desired_entity
        notes_obj["delivery_format"] = desired_format
        notes_obj["signers"] = signers_payload

        notes_json = json.dumps(notes_obj, separators=(",", ":"))

        patch_payload: dict[str, Any] = {
            "sparse": True,
            "Id": qbo_id,
            "SyncToken": str(live.get("SyncToken", "0")),
            "DisplayName": desired_display_name,
            "CompanyName": desired_company_name,
            "Notes": notes_json,
        }

        # Handle PrimaryEmailAddr safely
        if desired_email:
            combined_email = ", ".join([desired_email] + extra_live_emails)
            patch_payload["PrimaryEmailAddr"] = {"Address": combined_email}
        elif live_raw_email:
            patch_payload["PrimaryEmailAddr"] = {"Address": ""}

        # Handle PrimaryPhone safely
        if desired_phone:
            patch_payload["PrimaryPhone"] = {"FreeFormNumber": desired_phone}
        elif live_phone:
            patch_payload["PrimaryPhone"] = {"FreeFormNumber": ""}

        # Handle BillAddr safely (preserve Line2/Line3/Country/Id from existing BillAddr)
        has_desired_addr = any([desired_street, desired_city, desired_state, desired_zip])
        has_live_addr = bool(live.get("BillAddr"))
        if has_desired_addr:
            addr_obj = copy.deepcopy(live["BillAddr"]) if isinstance(live.get("BillAddr"), dict) else {}
            addr_obj["Line1"] = desired_street
            addr_obj["City"] = desired_city
            addr_obj["CountrySubDivisionCode"] = desired_state
            addr_obj["PostalCode"] = desired_zip
            patch_payload["BillAddr"] = addr_obj
        elif has_live_addr:
            patch_payload["BillAddr"] = None

        try:
            resp = client.update_customer(patch_payload)
            if isinstance(resp, dict) and isinstance(resp.get("Customer"), dict):
                qbo_by_id[qbo_id] = resp["Customer"]
            print("    -> SAVED to QBO.")
            stats["updated"] += 1
        except Exception as exc:
            print(f"    -> ERROR: {exc}", file=sys.stderr)
            stats["errors"] += 1

    print("\n=== Summary ===")
    print(f"Unchanged (skipped): {stats['unchanged']}")
    print(f"{'Updated' if apply_changes else 'Would update'}:   {stats['updated']}")
    print(f"Missing/Skipped:     {stats['skipped_missing']}")
    if apply_changes:
        print(f"Errors:              {stats['errors']}")


if __name__ == "__main__":
    main()