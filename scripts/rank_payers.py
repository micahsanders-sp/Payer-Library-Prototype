#!/usr/bin/env python3
"""Compute SizeRank, Credentialing, and Patient Cost Estimates for data/source.csv.

Combines several signals, strongest first:
  1. Major-brand match: curated list of ~30 real national/regional payer
     parent companies with public membership figures (millions), matched via
     explicit parent-company strings already present in a row's own Other
     Names/Aliases IDs, known subsidiary brand names, a BCBS state-routing
     table, or Medicare/Medicaid/TRICARE structural regexes.
  2. Priority to SP: SimplePractice's own human-curated business-priority
     tier for payers (~/Downloads/SP __ Medallion Payer Mapping - Sheet1.csv),
     joined by clearinghouse ID first, normalized name as fallback.
  3. Usage volume: real SimplePractice claim counts from two internal
     exports, name-matched.
  4. Heuristic fallback: derived from source.csv's own fields (Programs/
     OperatingStates/CoverageTypes/alias breadth), used only when nothing
     above matched - this is the only "signal" for most of the long tail,
     and is documented as a proxy, not fact.

Credentialing / Patient Cost Estimates are seeded using the SAME major-brand
match computed above (no second matching system): major-brand + the two
genuinely-national structural matches (CMS Medicare, TRICARE/DoD) get
TRUE/TRUE; state-Medicaid-FFS structural matches and everything else get
independently randomized per column, at the live-observed TRUE-rate of the
3 existing boolean columns, via one seeded RNG walked in fixed row order.

Usage:
    python3 scripts/rank_payers.py            # dry run, prints a report only
    python3 scripts/rank_payers.py --write     # recompute and rewrite source.csv
"""
import csv
import math
import os
import random
import re
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_CSV = REPO_ROOT / "data" / "source.csv"
DOWNLOADS = Path.home() / "Downloads"

MEDALLION_CSV = DOWNLOADS / "SP __ Medallion Payer Mapping - Sheet1.csv"
CLAIMS_BY_PRACTICE_CSV = DOWNLOADS / "Claims by practice_2026-05-15-1159.csv"
CLAIMS_SUBMITTED_CSV = DOWNLOADS / "Insurance Claims Submitted (last 365 days)_data (5).csv"
STEDI_CSV = DOWNLOADS / "stedi-payers-2026-07-13.csv"

SEED = 20260715

PRIORITY_SCORE = {
    "High Priority": 4,
    "Mid Priority": 3,
    "Low Priority": 2,
    "Very Low Priority": 1,
}

# ---------------------------------------------------------------------------
# Major-brand family table (Part 1 of the ranking methodology).
# `membership_millions` is a SORT-ONLY approximation from public market-share
# data - never displayed to end users as fact. `match` is one of:
#   "brand"     - real, specific, well-known company/brand (forced green)
#   "national"  - structural but genuinely national (Medicare, TRICARE - forced green)
#   "state"     - structural but state-scoped (Medicaid FFS - NOT forced green,
#                 since these are state programs, not "prominent national payers")
# ---------------------------------------------------------------------------
FAMILIES = [
    {"key": "unitedhealth", "membership_millions": 45, "match": "brand",
     "parent_signals": ["UnitedHealthcare Shared Services", "UnitedHealth Group", "Optum"],
     "brand_names": ["UnitedHealthcare", "United HealthCare", "UMR", "Optum", "Oxford Health",
                      "AARP UnitedHealthCare", "All Savers", "Surest", "Golden Rule"]},
    {"key": "elevance", "membership_millions": 45.6, "match": "brand",
     "parent_signals": ["Elevance Health"],
     "brand_names": ["Anthem", "Empire Blue Cross Blue Shield", "Healthy Blue", "Carelon"]},
    {"key": "hcsc", "membership_millions": 17, "match": "brand",
     "parent_signals": ["Health Care Service Corporation", "HCSC"],
     "brand_names": ["Luminare Health"],
     "bcbs_states": {"IL", "TX", "OK", "NM", "MT"}},
    {"key": "aetna_cvs", "membership_millions": 36, "match": "brand",
     "parent_signals": ["Aetna"],
     "brand_names": ["Aetna"]},
    {"key": "centene", "membership_millions": 28, "match": "brand",
     "parent_signals": ["Centene Corporation", "Centene"],
     "brand_names": ["Ambetter", "WellCare", "Fidelis Care", "Buckeye Health Plan",
                      "Sunshine Health", "Superior HealthPlan", "Peach State Health Plan",
                      "Absolute Total Care", "Health Net", "Magnolia Health",
                      "Home State Health", "Trillium", "PA Health & Wellness",
                      "Arizona Complete Health", "Nebraska Total Care",
                      "Louisiana Healthcare Connections", "MHS Indiana"]},
    {"key": "molina", "membership_millions": 5.8, "match": "brand",
     "brand_names": ["Molina Healthcare", "Molina HealthCare"]},
    {"key": "humana", "membership_millions": 17, "match": "brand",
     "parent_signals": ["Humana Military"],
     "brand_names": ["Humana"]},
    {"key": "cigna", "membership_millions": 19.5, "match": "brand",
     "brand_names": ["Cigna", "CIGNA"]},
    {"key": "kaiser", "membership_millions": 12.5, "match": "brand",
     "brand_names": ["Kaiser Foundation Health Plan", "Kaiser Permanente"]},
    {"key": "highmark", "membership_millions": 7, "match": "brand",
     "parent_signals": ["Highmark"], "brand_names": ["Highmark"],
     "bcbs_states": {"PA", "WV", "DE"}},
    {"key": "carefirst", "membership_millions": 3.5, "match": "brand",
     "brand_names": ["CareFirst"], "bcbs_states": {"MD", "DC"}},
    {"key": "independence_bx", "membership_millions": 2.5, "match": "brand",
     "brand_names": ["Independence Blue Cross", "Keystone Health Plan East"]},
    {"key": "upmc", "membership_millions": 4, "match": "brand",
     "brand_names": ["UPMC Health Plan"]},
    {"key": "emblemhealth", "membership_millions": 3, "match": "brand",
     "brand_names": ["EmblemHealth"]},
    {"key": "oscar", "membership_millions": 1.75, "match": "brand",
     "brand_names": ["Oscar Health"]},
    {"key": "horizon_nj", "membership_millions": 3.8, "match": "brand",
     "bcbs_states": {"NJ"}},
    {"key": "bcbs_mi", "membership_millions": 5, "match": "brand", "bcbs_states": {"MI"}},
    {"key": "florida_blue", "membership_millions": 6, "match": "brand", "bcbs_states": {"FL"}},
    {"key": "bcbs_nc", "membership_millions": 4.3, "match": "brand", "bcbs_states": {"NC"}},
    {"key": "bcbs_tn", "membership_millions": 3.4, "match": "brand", "bcbs_states": {"TN"}},
    {"key": "bcbs_ma", "membership_millions": 3, "match": "brand", "bcbs_states": {"MA"}},
    {"key": "blueshield_ca", "membership_millions": 6, "match": "brand",
     "brand_names": ["Blue Shield of California"]},
    # Structural families - no brand name at all.
    {"key": "cms_traditional_medicare", "membership_millions": 66, "match": "national",
     "regex": re.compile(r"Medicare\b.*\bPart\s?[AB]\b", re.IGNORECASE)},
    {"key": "tricare_dod", "membership_millions": 9.5, "match": "national",
     "brand_names": ["TriWest", "TRICARE", "Tricare"]},
    {"key": "state_medicaid_ffs", "membership_millions": None, "match": "state",
     "regex": re.compile(r"^Medicaid\s+[A-Za-z .'\-]+$")},
]

# BCBS-branded rows are only routed to a family if they carry NO other
# brand qualifier (i.e. plain "Blue Cross"/"Blue Shield of <state>" naming) -
# states with no confirmed owner+number (e.g. Mississippi) simply have no
# entry below and correctly fall through to the heuristic tail.
BCBS_STATE_TO_FAMILY = {}
for fam in FAMILIES:
    for st in fam.get("bcbs_states", ()):
        BCBS_STATE_TO_FAMILY[st] = fam["key"]

FAMILY_BY_KEY = {f["key"]: f for f in FAMILIES}

# New York is split three ways and can't be a single state->owner entry.
NY_BRAND_OVERRIDE = [
    ("Empire", "elevance"),
    ("Excellus", None),  # independent, no confirmed number -> heuristic tail
    ("Highmark", "highmark"),
]

# Deliberately absent from FAMILIES (verified independent / non-existent as
# a standalone row, or a confirmed false-positive risk against a broad rule
# above e.g. "Capital Blue Cross" is an independent central-PA plan, not
# Highmark, despite PA being in Highmark's bcbs_states) - kept as an
# explicit deny-list so a broad rule never accidentally sweeps these in.
KNOWN_FALSE_POSITIVE_EXCLUSIONS = ["CareSource", "Capital Blue Cross"]

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
# Rough population-based bucket for state Medicaid FFS rows (a proxy, not a
# claim about real per-state Medicaid enrollment).
MEDICAID_STATE_BUCKET = {
    "California": 3, "Texas": 3, "New York": 3, "Florida": 3,
    "Pennsylvania": 2, "Illinois": 2, "Ohio": 2, "Georgia": 2, "North Carolina": 2,
    "Michigan": 2, "New Jersey": 2, "Virginia": 2, "Washington": 2, "Arizona": 2,
    "Massachusetts": 2, "Tennessee": 2, "Indiana": 2, "Missouri": 2, "Maryland": 2,
    "Wisconsin": 2,
}


def strip_pipes(value):
    return [v.strip() for v in value.split("|") if v.strip()] if value else []


def normalize_name(name):
    name = name.lower().strip()
    name = re.sub(r"[.,'\"]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name


def match_family(row):
    """Return (family_key, membership_millions, match_type) or (None, None, None)."""
    display_name = row["DisplayName"]
    if any(excl.lower() in display_name.lower() for excl in KNOWN_FALSE_POSITIVE_EXCLUSIONS):
        return None, None, None

    other_names = strip_pipes(row["Other Names"])
    haystack_text = " | ".join([display_name] + other_names)

    # Tier 1: explicit parent-company signal already in the row's own data.
    for fam in FAMILIES:
        for signal in fam.get("parent_signals", []):
            if signal.lower() in haystack_text.lower():
                return fam["key"], fam["membership_millions"], fam["match"]

    # Tier 2: known subsidiary/standalone brand name (DisplayName or an Other
    # Names token), and Tier "national" brand-name families (TRICARE etc).
    for fam in FAMILIES:
        for brand in fam.get("brand_names", []):
            b = brand.lower()
            if b in display_name.lower() or any(b in n.lower() for n in other_names):
                return fam["key"], fam["membership_millions"], fam["match"]

    # Tier 3: BCBS state routing, only for a plain "Blue Cross/Shield of <state>"
    # name with no other brand qualifier already caught above (Tiers 1-2
    # already catch Anthem/Highmark/CareFirst/Independence-branded rows).
    if re.search(r"\bblue (cross|shield)\b", display_name, re.IGNORECASE):
        # New York override first (three different real owners share the state).
        if re.search(r"new york", display_name, re.IGNORECASE):
            for token, family_key in NY_BRAND_OVERRIDE:
                if token.lower() in display_name.lower():
                    if family_key is None:
                        return None, None, None
                    fam = FAMILY_BY_KEY[family_key]
                    return fam["key"], fam["membership_millions"], fam["match"]
            return None, None, None
        # Route by full state name appearing in the display name - only
        # states with a confirmed owner+number are in BCBS_STATE_TO_FAMILY;
        # everything else (e.g. Mississippi) has no entry and correctly
        # falls through to the heuristic tail.
        for state_abbr, family_key in BCBS_STATE_TO_FAMILY.items():
            state_name = STATE_NAMES[state_abbr]
            if re.search(r"\b" + re.escape(state_name) + r"\b", display_name, re.IGNORECASE):
                fam = FAMILY_BY_KEY[family_key]
                return fam["key"], fam["membership_millions"], fam["match"]

    # Tier 4: structural regex families (Medicare Part A/B MAC rows, state
    # Medicaid FFS programs).
    for fam in FAMILIES:
        regex = fam.get("regex")
        if regex and regex.search(display_name):
            if fam["key"] == "state_medicaid_ffs":
                state_part = display_name.split(None, 1)[1].strip() if " " in display_name else ""
                bucket = MEDICAID_STATE_BUCKET.get(state_part, 1)
                # membership_millions is None for this family; use the bucket
                # (1-3) directly as a small tiebreak-only value.
                return fam["key"], bucket, fam["match"]
            return fam["key"], fam["membership_millions"], fam["match"]

    return None, None, None


def load_medallion_priority():
    id_to_priority = {}
    name_to_priority = {}
    if not MEDALLION_CSV.exists():
        print(f"WARNING: {MEDALLION_CSV} not found - skipping Priority to SP signal", file=sys.stderr)
        return id_to_priority, name_to_priority
    with MEDALLION_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            score = PRIORITY_SCORE.get(row["Priority to SP"].strip(), 0)
            for col in (
                "Clearinghouse 1 payer ID",
                "Clearinghouse 2 payer ID",
                "Clearinghouse 3 primary payer ID",
                "Clearinghouse 3 payer ID",
            ):
                val = row.get(col, "").strip()
                if val:
                    id_to_priority[val.upper()] = max(score, id_to_priority.get(val.upper(), 0))
            name = row["SimplePractice Payer Name"].strip()
            if name:
                name_to_priority[normalize_name(name)] = max(
                    score, name_to_priority.get(normalize_name(name), 0)
                )
    return id_to_priority, name_to_priority


def load_claims_by_practice():
    counts = defaultdict(int)
    if not CLAIMS_BY_PRACTICE_CSV.exists():
        print(f"WARNING: {CLAIMS_BY_PRACTICE_CSV} not found - skipping", file=sys.stderr)
        return counts
    with CLAIMS_BY_PRACTICE_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = normalize_name(row["PAYER_NAME"])
            try:
                counts[name] += int(row["COUNT(DISTINCT I.ID)"])
            except (ValueError, KeyError):
                pass
    return counts


def load_claims_submitted():
    counts = defaultdict(int)
    if not CLAIMS_SUBMITTED_CSV.exists():
        print(f"WARNING: {CLAIMS_SUBMITTED_CSV} not found - skipping", file=sys.stderr)
        return counts
    with CLAIMS_SUBMITTED_CSV.open(newline="", encoding="utf-16") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            name = normalize_name(row["INSURANCE_PAYER_NAME"])
            try:
                counts[name] += int(row["CLAIMS_COUNT"])
            except (ValueError, KeyError):
                pass
    return counts


def load_stedi_group_sizes():
    """Map PrimaryPayerId -> number of OTHER source rows sharing its ParentPayerGroupId."""
    if not STEDI_CSV.exists():
        print(f"WARNING: {STEDI_CSV} not found - skipping ParentPayerGroupId signal", file=sys.stderr)
        return {}
    payer_id_to_group = {}
    with STEDI_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            gid = row.get("ParentPayerGroupId", "").strip()
            if gid:
                payer_id_to_group[row["PrimaryPayerId"]] = gid
    group_counts = Counter(payer_id_to_group.values())
    return {pid: group_counts[gid] for pid, gid in payer_id_to_group.items()}


def heuristic_score(row):
    programs = strip_pipes(row["Programs"])
    states = strip_pipes(row["OperatingStates"])
    coverage = strip_pipes(row["CoverageTypes"])
    other_names = strip_pipes(row["Other Names"])
    alias_ids = strip_pipes(row["Aliases IDs"])

    programs_pts = min(len(programs), 4) * 3

    if not states:
        states_bucket = 0
    elif "NATIONAL" in [s.upper() for s in states] or len(states) >= 45:
        states_bucket = 6
    elif len(states) >= 31:
        states_bucket = 5
    elif len(states) >= 15:
        states_bucket = 4
    elif len(states) >= 6:
        states_bucket = 3
    elif len(states) >= 2:
        states_bucket = 2
    else:
        states_bucket = 1
    states_pts = states_bucket * 2

    alias_pts = min(len(other_names) + len(alias_ids), 20) / 2

    coverage_pts = min(len(coverage), 3) * 2

    bools = sum(
        1
        for col in ("Eligibility Checks", "Submit Claims", "Receive ERAs")
        if row[col].strip().upper() == "TRUE"
    )
    bool_pts = bools

    return programs_pts + states_pts + alias_pts + coverage_pts + bool_pts


def main():
    write = "--write" in sys.argv

    with SOURCE_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    n = len(rows)

    id_to_priority, name_to_priority = load_medallion_priority()
    claims_by_practice = load_claims_by_practice()
    claims_submitted = load_claims_submitted()
    stedi_group_sizes = load_stedi_group_sizes()

    total_true = 0
    total_bool_cols = 0
    for row in rows:
        for col in ("Eligibility Checks", "Submit Claims", "Receive ERAs"):
            total_bool_cols += 1
            if row[col].strip().upper() == "TRUE":
                total_true += 1
    p = total_true / total_bool_cols

    computed = []
    for idx, row in enumerate(rows):
        family_key, membership_millions, match_type = match_family(row)
        is_major = family_key is not None

        alias_tokens = {row["PrimaryPayerId"].strip().upper()} | {
            a.upper() for a in strip_pipes(row["Aliases IDs"])
        }
        priority = 0
        for tok in alias_tokens:
            if tok in id_to_priority:
                priority = max(priority, id_to_priority[tok])
        if priority == 0:
            priority = name_to_priority.get(normalize_name(row["DisplayName"]), 0)

        name_key = normalize_name(row["DisplayName"])
        usage = claims_by_practice.get(name_key, 0) + claims_submitted.get(name_key, 0)
        usage_log = math.log1p(usage)

        group_bonus = stedi_group_sizes.get(row["PrimaryPayerId"], 1)

        h_score = heuristic_score(row)

        computed.append({
            "idx": idx,
            "is_major": is_major,
            "family_key": family_key,
            "membership_millions": membership_millions or 0,
            "match_type": match_type,
            "priority": priority,
            "usage_log": usage_log,
            "group_bonus": group_bonus,
            "heuristic": h_score,
            "display_name": row["DisplayName"],
            "primary_payer_id": row["PrimaryPayerId"],
        })

    # Commercial brands always outrank government programs, regardless of
    # the government program's real aggregate size: Medicare's ~66M total
    # is real, but this dataset represents it as ~110 separate regional
    # claims-contractor rows, and letting each one carry the full 66M
    # let the whole cluster outrank UnitedHealthcare/Aetna. "brand" is
    # always tier 0; "national" (Medicare/TRICARE) and "state" (Medicaid
    # FFS) still rank above the unmatched heuristic tail, just never
    # above a named commercial payer.
    TIER_ORDER = {"brand": 0, "national": 1, "state": 2}

    def sort_key(c):
        tier = TIER_ORDER[c["match_type"]] if c["is_major"] else 3
        membership = -c["membership_millions"] if c["is_major"] else 0
        return (
            tier,
            membership,
            -c["priority"],
            -c["usage_log"],
            -c["group_bonus"],
            -c["heuristic"],
            c["display_name"].lower(),
            c["primary_payer_id"],
            c["idx"],
        )

    ranked = sorted(computed, key=sort_key)
    for rank, c in enumerate(ranked, start=1):
        c["size_rank"] = rank

    rng = random.Random(SEED)
    for c in computed:
        force_green = c["is_major"] and c["match_type"] in ("brand", "national")
        if force_green:
            c["credentialing"] = True
            c["patient_cost_estimates"] = True
        else:
            c["credentialing"] = rng.random() < p
            c["patient_cost_estimates"] = rng.random() < p

    # ---- Report ----
    n_major = sum(1 for c in computed if c["is_major"])
    n_priority = sum(1 for c in computed if c["priority"] > 0)
    n_usage = sum(1 for c in computed if c["usage_log"] > 0)
    family_counts = Counter(c["family_key"] for c in computed if c["is_major"])

    print(f"Total rows: {n}")
    print(f"Observed TRUE-rate p (Eligibility/Claims/ERAs) = {p:.3f}")
    print(f"Major-brand matches: {n_major} ({n_major/n:.1%})")
    for key, cnt in family_counts.most_common():
        print(f"  {key}: {cnt}")
    print(f"Priority-to-SP matches: {n_priority} ({n_priority/n:.1%})")
    print(f"Claims-volume matches: {n_usage} ({n_usage/n:.1%})")
    print()
    print("Top 20 by SizeRank:")
    for c in ranked[:20]:
        print(f"  {c['size_rank']:>5}  {c['display_name']} "
              f"[{c['family_key'] or 'unmatched'}] cred={c['credentialing']} cost={c['patient_cost_estimates']}")
    print()
    print("Bottom 20 by SizeRank:")
    for c in ranked[-20:]:
        print(f"  {c['size_rank']:>5}  {c['display_name']} "
              f"[{c['family_key'] or 'unmatched'}] cred={c['credentialing']} cost={c['patient_cost_estimates']}")

    excluded_check = [c for c in computed if "caresource" in c["display_name"].lower()]
    print()
    print(f"CareSource rows (should all be unmatched): "
          f"{sum(1 for c in excluded_check if c['is_major'])} matched / {len(excluded_check)} total")

    if not write:
        print()
        print("Dry run only - rerun with --write to update data/source.csv")
        return

    by_idx = {c["idx"]: c for c in computed}
    insert_at = fieldnames.index("Eligibility Checks")
    new_fieldnames = (
        ["SizeRank"]
        + fieldnames[:insert_at]
        + ["Credentialing", "Eligibility Checks", "Patient Cost Estimates"]
        + fieldnames[insert_at + 1:]
    )

    for idx, row in enumerate(rows):
        c = by_idx[idx]
        row["SizeRank"] = str(c["size_rank"])
        row["Credentialing"] = "TRUE" if c["credentialing"] else "FALSE"
        row["Patient Cost Estimates"] = "TRUE" if c["patient_cost_estimates"] else "FALSE"

    fd, tmp_path = tempfile.mkstemp(dir=str(SOURCE_CSV.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=new_fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp_path, SOURCE_CSV)
    except Exception:
        os.unlink(tmp_path)
        raise

    print()
    print(f"Wrote {n} rows to {SOURCE_CSV} with SizeRank/Credentialing/Patient Cost Estimates.")


if __name__ == "__main__":
    main()
