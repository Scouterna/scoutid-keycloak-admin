#!/usr/bin/env python3
"""Map legacy ScoutID SAML service providers onto Keycloak SAML clients.

Reads the `saml20_sp_remote` table of the old SimpleSAMLphp ScoutID database and
emits the Keycloak client representations that would be created. **Dry run by
default: nothing is written anywhere.** Pass --out to save the JSON.

Input is a TSV of `entity_id<TAB>entity_data` (entity_data being SimpleSAMLphp's
JSON blob), exported from the `saml20_sp_remote` table with tabs and newlines
stripped from the data column. Taking a file rather than a connection string
keeps database credentials out of this script entirely.

Usage:
    migrate-saml-sps.py <tsv> [--out clients.json] [--report] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any

# The attribute set the legacy IdP released, near-identical across the estate.
# Reported for information only: this script does NOT create a client scope, and
# no such scope exists yet. How attribute release should actually be configured
# is decided in scoutid-keycloak-provider (docs/client_config_guide.md).
SCOUTID_SAML_ATTRIBUTES = [
    "uid",
    "email",
    "firstname",
    "lastname",
    "firstlast",
    "displayName",
    "dob",
    "group_name",
    "group_no",
    "group_id",
    "above_15",
    "roles",
]



class SkipReason(Exception):
    """An SP that cannot be mapped without a human decision."""


def first_url(value: Any) -> str | None:
    """SimpleSAMLphp stores endpoints as a string, a list, or a list of dicts
    with a Location key. Normalise to a single URL."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for item in value:
            url = first_url(item)
            if url:
                return url
        return None
    if isinstance(value, dict):
        return first_url(value.get("Location"))
    return None


def map_sp(entity_id: str, data: dict) -> dict:
    """Translate one SimpleSAMLphp SP into a Keycloak SAML client."""
    acs = first_url(data.get("AssertionConsumerService"))
    if not acs:
        raise SkipReason("no AssertionConsumerService")
    if not acs.startswith(("http://", "https://")):
        # e.g. the wildcard triggerfish entry, whose ACS is a bare hostname.
        raise SkipReason(f"ACS is not an absolute URL: {acs!r}")

    slo = first_url(data.get("SingleLogoutService"))

    # SimpleSAMLphp's nameidattribute names the attribute carrying the subject;
    # every observed row uses uid. Keycloak expresses the *format* separately.
    name_id_format = "email"
    if "nameid-format:persistent" in (data.get("NameIDFormat") or ""):
        name_id_format = "persistent"
    elif "nameid-format:transient" in (data.get("NameIDFormat") or ""):
        name_id_format = "transient"

    attributes = {
        "saml_name_id_format": name_id_format,
        "saml_assertion_consumer_url_post": acs,
        "saml_assertion_consumer_url_redirect": acs,
        # The legacy IdP signs assertions but does not require the SP to sign
        # its requests — the WordPress sites have no signing keys configured.
        "saml.assertion.signature": "true",
        "saml.server.signature": "true",
        "saml.client.signature": "false",
        "saml.authnstatement": "true",
        "saml_force_name_id_format": "true",
    }
    if slo:
        attributes["saml_single_logout_service_url_post"] = slo
        attributes["saml_single_logout_service_url_redirect"] = slo

    return {
        "clientId": entity_id,
        "name": data.get("name") or entity_id,
        "protocol": "saml",
        "enabled": True,
        "frontchannelLogout": bool(slo),
        "redirectUris": [acs],
        "adminUrl": acs,
        "attributes": attributes,
    }


def load(path: str) -> list[tuple[str, str]]:
    rows = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if "\t" not in line:
                continue
            entity_id, data = line.split("\t", 1)
            rows.append((entity_id, data))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tsv", help="TSV of entity_id<TAB>entity_data")
    parser.add_argument("--out", help="write the client JSON here (default: dry run)")
    parser.add_argument("--report", action="store_true", help="print a summary")
    parser.add_argument("--limit", type=int, help="only process the first N rows")
    args = parser.parse_args()

    rows = load(args.tsv)
    if args.limit:
        rows = rows[: args.limit]

    clients: list[dict] = []
    skipped: list[tuple[str, str]] = []
    acs_paths: Counter[str] = Counter()

    for entity_id, raw in rows:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            skipped.append((entity_id, f"unparsable entity_data: {error}"))
            continue
        try:
            client = map_sp(entity_id, data)
        except SkipReason as reason:
            skipped.append((entity_id, str(reason)))
            continue
        clients.append(client)
        acs = client["attributes"]["saml_assertion_consumer_url_post"]
        acs_paths["/" + acs.split("/", 3)[-1] if acs.count("/") > 2 else "/"] += 1

    duplicates = [
        client_id
        for client_id, count in Counter(c["clientId"] for c in clients).items()
        if count > 1
    ]

    if args.report:
        print(f"input rows:      {len(rows)}")
        print(f"mapped clients:  {len(clients)}")
        print(f"skipped:         {len(skipped)}")
        print(f"duplicate ids:   {len(duplicates)}")
        print("\ntop ACS paths:")
        for path, count in acs_paths.most_common(8):
            print(f"  {count:5d}  {path}")
        if skipped:
            print("\nskipped entities:")
            for entity_id, reason in skipped[:20]:
                print(f"  {entity_id}: {reason}")
            if len(skipped) > 20:
                print(f"  … and {len(skipped) - 20} more")
        if duplicates:
            print("\nduplicate clientIds (would collide in Keycloak):")
            for client_id in duplicates[:10]:
                print(f"  {client_id}")

    if args.out:
        payload = {
            "legacyAttributesForReference": SCOUTID_SAML_ATTRIBUTES,
            "clients": clients,
        }
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        print(f"\nwrote {len(clients)} clients to {args.out}", file=sys.stderr)
    else:
        print(
            f"\nDry run — nothing written. Pass --out to save. ({len(clients)} clients)",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
