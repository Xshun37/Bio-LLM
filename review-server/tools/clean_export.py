#!/usr/bin/env python3
"""Clean the GS review TSV export.

Operations:
1. Deduplicate Notes — keep Notes only on the first row of each PMID group
2. Normalize TF/Target aliases via local gene alias index
3. Map Target -> ENSG ID; move original Target after Notes column
4. Delete Direction column
"""

import json
import os
import re
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REVIEW_ROOT = os.path.dirname(THIS_DIR)
DATA_DIR = os.path.join(REVIEW_ROOT, "data")

ENSG_MAP_PATH = os.path.join(DATA_DIR, "gene_ensg_map.json")
ALIAS_INDEX_PATH = os.path.join(DATA_DIR, "gene_alias_index.json")

# Manual overrides for ambiguous/unmapped genes that the alias system can't resolve.
# Order: normalizes name before ENSG lookup.
MANUAL_NORMALIZE = {
    "CREB": "CREB1",
    "E2F-3A": "E2F3",
    "E2F-3a": "E2F3",
    "ANT1": "SLC25A4",
    "CBP": "CREBBP",
}

# TFs that represent multi-gene complexes / families — keep as-is.
KEEP_AS_IS = {"AP1", "CBF", "CEBP/SPI1", "LEF/TCF", "SMAD", "CEBP"}


def _candidate_keys(name):
    """Generate canonical and variant keys for a gene name."""
    if not name:
        return "", []
    original = str(name).strip()
    canonical = re.sub(r'[^A-Za-z0-9]', '', original).upper()
    if not canonical:
        return "", []
    keys = [canonical]
    # Add compact forms with common separators
    for sep in ['-', '/', ' ']:
        variant = original.replace(sep, '').upper()
        if variant and variant not in keys:
            keys.append(variant)
    return canonical, keys


def _load_alias_index():
    """Load the gene alias index JSON."""
    if not os.path.exists(ALIAS_INDEX_PATH):
        return {}
    with open(ALIAS_INDEX_PATH, encoding="utf-8") as f:
        return json.load(f)


def _lookup_hgnc(keys, index):
    """Look up gene name in HGNC alias index."""
    official_symbols = set(index.get("official_symbols", []))
    aliases = index.get("aliases", {})

    # Step 1: canonical key as official symbol
    if keys and keys[0] in official_symbols:
        return "identity", keys[0]

    # Step 2: alias lookup
    for key in keys:
        candidates = aliases.get(key, [])
        symbols = sorted({
            str(item.get("symbol", "")).strip().upper()
            for item in candidates
            if item.get("symbol")
        })
        if len(symbols) == 1:
            return "hgnc_alias", symbols[0]
        if len(symbols) > 1:
            return "ambiguous", ""

    # Step 3: compact keys as official symbols
    for key in keys[1:]:
        if key in official_symbols:
            return "identity", key

    return "unmapped", ""


def normalize_gene_name(name):
    """Simple gene name normalization using local alias index."""
    if not name:
        return name, None
    canonical, keys = _candidate_keys(name)
    if not canonical:
        return name, None

    index = _load_alias_index()
    status, symbol = _lookup_hgnc(keys, index)
    if status in ("identity", "hgnc_alias"):
        return symbol, status
    return name, None


def load_ensg_map():
    with open(ENSG_MAP_PATH, encoding="utf-8") as f:
        return json.load(f)


def normalize_name(name, role, ensg_map):
    """Normalize a gene name using local alias index with manual fallbacks."""
    if not name:
        return name, None

    # 1. Manual normalization
    manual = MANUAL_NORMALIZE.get(name)
    if manual:
        return manual, "manual"

    # 2. Keep-as-is (complexes)
    if name.upper() in {k.upper() for k in KEEP_AS_IS}:
        return name, "complex"

    # 3. Local alias system
    normalized, status = normalize_gene_name(name)
    if status in ("identity", "hgnc_alias"):
        return normalized, status

    # 4. Fallback: try compact form in ENSG map
    compact = name.upper().replace("-", "").replace(" ", "").replace("/", "")
    if compact in ensg_map:
        return compact, "compact_ensg"

    return name, None


def main():
    input_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        THIS_DIR, "..", "data", "gs_review_export.tsv"
    )
    ensg_map = load_ensg_map()

    # Read all rows
    rows = []
    with open(input_path, encoding="utf-8") as f:
        first = True
        for line in f:
            line = line.strip()
            if not line:
                continue
            if first:
                first = False
                continue  # skip header
            parts = line.split("\t")
            if len(parts) < 9:
                continue
            rows.append(parts)

    print(f"Read {len(rows)} rows", file=sys.stderr)

    # ---- Pass 1: deduplicate Notes per PMID ----
    seen_pmid = set()
    for row in rows:
        pmid = row[0].strip()
        if pmid in seen_pmid:
            row[7] = ""  # clear Notes (col index 7)
        else:
            seen_pmid.add(pmid)

    # ---- Pass 2: normalize TF/Target + ENSG + restructure columns ----
    output_rows = []
    tf_stats = {}
    target_stats = {}

    for row in rows:
        pmid = row[0].strip()
        tf_raw = row[1].strip()
        target_raw = row[2].strip()
        # col 3 = Direction (to delete)
        cellline = row[4].strip()
        assay = row[5].strip()
        complex_val = row[6].strip()
        notes = row[7].strip() if len(row) > 7 else ""
        cofactor = row[8].strip() if len(row) > 8 else "0"

        # Normalize TF
        tf_norm, tf_source = normalize_name(tf_raw, "tf", ensg_map)
        tf_stats[tf_source] = tf_stats.get(tf_source, 0) + 1

        # Normalize Target
        target_norm, target_source = normalize_name(target_raw, "target", ensg_map)
        target_stats[target_source] = target_stats.get(target_source, 0) + 1

        # Map Target to ENSG
        ensg = ensg_map.get(target_norm.upper(), "NOT_FOUND")

        # New row: PMID | TF | ENSG | CellLine | Assay | Complex | Notes | Original_Target | Cofactor
        new_row = [
            pmid,
            tf_norm,
            ensg,
            cellline,
            assay,
            complex_val,
            notes,
            target_raw,  # original target moved after Notes
            cofactor,
        ]
        output_rows.append(new_row)

    # ---- Report ----
    print(f"TF normalization stats: {tf_stats}", file=sys.stderr)
    print(f"Target normalization stats: {target_stats}", file=sys.stderr)

    # Count NOT_FOUND
    not_found = [(row[1], row[7], row[0]) for row in output_rows if row[2] == "NOT_FOUND"]
    if not_found:
        print(f"\nNOT_FOUND ENSG ({len(not_found)}):", file=sys.stderr)
        for tf, target, pmid in not_found:
            print(f"  PMID={pmid}  TF={tf}  Target={target}", file=sys.stderr)

    # ---- Write output ----
    output_header = ["PMID", "TF", "ENSG", "CellLine", "Assay", "Complex", "Notes", "Target", "Cofactor"]
    print("\t".join(output_header))
    for row in output_rows:
        print("\t".join(row))


if __name__ == "__main__":
    main()
