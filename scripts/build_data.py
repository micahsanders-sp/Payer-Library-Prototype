#!/usr/bin/env python3
"""Embed data/source.csv into index.html for the Payer Library prototype.

Re-run with `python3 scripts/build_data.py` whenever data/source.csv changes.
Requires data/source.csv to already have SizeRank/Credentialing/Patient Cost
Estimates columns - run scripts/rank_payers.py --write first if it doesn't.
"""
import csv
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_CSV = REPO_ROOT / "data" / "source.csv"
INDEX_HTML = REPO_ROOT / "index.html"

DATA_START_MARKER = "<!-- PAYER_DATA_START -->"
DATA_END_MARKER = "<!-- PAYER_DATA_END -->"

# Matches the design's STATE_NAMES key order (50 states + DC).
ALL_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
]

PROGRAM_LABELS = {
    "COMMERCIAL": "Commercial",
    "MEDICARE": "Medicare",
    "MEDICAID": "Medicaid",
    "WORKERS_COMPENSATION": "Workers Comp",
    "TRICARE": "Tricare",
    "AUTOMOBILE_MEDICAL": "Automobile Medical",
    "VETERANS_AFFAIRS": "Veterans Affairs",
    "MEDICARE_ADVANTAGE": "Medicare Advantage",
}

ERA_TYPE_LABELS = {
    "ONE_CLICK": "One-click",
    "MULTI_STEP": "Multi-step",
}

ERA_TIMELINE_LABELS = {
    "INSTANT": "Instant",
    "HOURS": "Within hours",
    "DAYS": "Within days",
    "WEEKS": "Within weeks",
    "OVER_4_WEEKS": "Over 4 weeks",
}


def split_pipe(value):
    return [v for v in value.split("|") if v] if value else []


def to_bool(value):
    return value.strip().upper() == "TRUE"


def build_operating_states(raw):
    if not raw:
        return []
    tokens = [s.strip().upper() for s in raw.split("|") if s.strip()]
    if "NATIONAL" in tokens:
        result = list(ALL_STATES)
        for t in tokens:
            if t != "NATIONAL" and t not in result:
                result.append(t)
        return result
    return tokens


def build_programs(raw):
    return [PROGRAM_LABELS.get(p, p) for p in split_pipe(raw)]


def build_coverage_types(raw):
    return [c.strip().title() for c in split_pipe(raw)]


def main():
    with SOURCE_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        records = []
        for index, row in enumerate(reader):
            records.append({
                "id": f"r{index}",
                "sizeRank": int(row["SizeRank"]),
                "displayName": row["DisplayName"],
                "primaryPayerId": row["PrimaryPayerId"],
                "otherNames": split_pipe(row["Other Names"]),
                "aliasIds": split_pipe(row["Aliases IDs"]),
                "operatingStates": build_operating_states(row["OperatingStates"]),
                "programs": build_programs(row["Programs"]),
                "coverageTypes": build_coverage_types(row["CoverageTypes"]),
                "websiteUrl": row["WebsiteUrl"] or "",
                "credentialing": to_bool(row["Credentialing"]),
                "eligibilityChecks": to_bool(row["Eligibility Checks"]),
                "clientObligationEstimates": to_bool(row["Patient Cost Estimates"]),
                "submitClaims": to_bool(row["Submit Claims"]),
                "receiveERAs": to_bool(row["Receive ERAs"]),
                "enrollToReceiveERAs": to_bool(row["Enroll to receive ERAs"]),
                "eraEnrollmentType": ERA_TYPE_LABELS.get(row["ERA enrollment type"].strip().upper()) if row["ERA enrollment type"].strip() else None,
                "eraEnrollmentTimeline": ERA_TIMELINE_LABELS.get(row["ERA enrollment timeline"].strip().upper()) if row["ERA enrollment timeline"].strip() else None,
            })

    payload = "window.PAYER_LIBRARY_DATA = " + json.dumps(records, indent=None, separators=(",", ":")) + ";"
    new_block = f"{DATA_START_MARKER}\n<script>{payload}</script>\n{DATA_END_MARKER}"

    html = INDEX_HTML.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(DATA_START_MARKER) + r".*?" + re.escape(DATA_END_MARKER),
        re.DOTALL,
    )
    if not pattern.search(html):
        raise SystemExit(f"Could not find {DATA_START_MARKER}/{DATA_END_MARKER} markers in {INDEX_HTML}")
    html = pattern.sub(lambda _match: new_block, html, count=1)
    INDEX_HTML.write_text(html, encoding="utf-8")

    print(f"Embedded {len(records)} records into {INDEX_HTML}")


if __name__ == "__main__":
    main()
