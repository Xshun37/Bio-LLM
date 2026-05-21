#!/usr/bin/env python3
"""Post-process GS review TSV export: normalize gene names + add ENSG.

Usage:
    python tools/normalize_export.py <exported.tsv>

Outputs to stdout a TSV with columns:
    PMID  TF  ENSG(Target)  Direction  CellLine  Assay  complex

Normalization priority (4-step):
    1. Manual overrides  (gene_alias_overrides.json — e.g. CBF2→CEBPZ, CBF→no-op)
    2. Official HGNC symbol (already in gene_ensg_map.json — skip alias lookup)
    3. Unambiguous HGNC alias (gene_alias_index.json — only if exactly 1 candidate)
    4. Keep original input
"""

import json
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# review_server/tools/ -> review_server/ -> Bio-LLM/
PROJECT_ROOT = os.path.dirname(os.path.dirname(THIS_DIR))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

ENSG_MAP_PATH = os.path.join(PROJECT_ROOT, "data", "curated", "gene_ensg_map.json")
OVERRIDES_PATH = os.path.join(PROJECT_ROOT, "data", "curated", "gene_alias_overrides.json")
ALIAS_INDEX_PATH = os.path.join(PROJECT_ROOT, "data", "curated", "gene_alias_index.json")


def load_maps():
    with open(ENSG_MAP_PATH, encoding="utf-8") as f:
        ensg_map = json.load(f)

    with open(OVERRIDES_PATH, encoding="utf-8") as f:
        raw_overrides = json.load(f)
    if isinstance(raw_overrides, dict):
        overrides = raw_overrides.get("rules", [])
    else:
        overrides = raw_overrides

    with open(ALIAS_INDEX_PATH, encoding="utf-8") as f:
        alias_idx = json.load(f)

    # Build override map: alias -> symbol (map action only)
    override_map = {}
    for rule in overrides:
        q = (rule.get("alias") or "").strip().upper()
        action = rule.get("action", "map")
        target = (rule.get("symbol") or "").strip().upper()
        if action == "map" and q and target:
            override_map[q] = target

    # Build alias map: alias -> symbol (unambiguous only — exactly 1 candidate)
    alias_map = {}
    for alias, candidates in alias_idx.get("aliases", {}).items():
        if len(candidates) == 1:
            alias_map[alias.upper()] = candidates[0]["symbol"].strip().upper()

    return ensg_map, override_map, alias_map


def normalize(symbol, override_map, alias_map, ensg_map):
    """Normalize a gene symbol using the 4-step hierarchy."""
    sym = symbol.strip().upper()
    if not sym:
        return ""
    # 1. Override (highest priority — manually curated)
    if sym in override_map:
        return override_map[sym]
    # 2. Already an official symbol (skip alias to avoid bad HGNC aliases)
    if sym in ensg_map:
        return sym
    # 3. Unambiguous HGNC alias (exactly 1 candidate)
    if sym in alias_map:
        return alias_map[sym]
    # 4. Fallback: return cleaned original
    return symbol.strip()


def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/normalize_export.py <exported.tsv>", file=sys.stderr)
        sys.exit(1)

    input_path = sys.argv[1]
    ensg_map, override_map, alias_map = load_maps()

    # Reverse ENSG -> symbol for old-format detection
    ensg_to_symbol = {}
    for sym, ensg in ensg_map.items():
        if ensg not in ensg_to_symbol:
            ensg_to_symbol[ensg] = sym

    header = ["PMID", "TF", "ENSG", "CellLine", "Assay", "complex"]
    print("\t".join(header))

    with open(input_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 5:
                continue

            pmid = parts[0].strip()
            tf_raw = parts[1].strip()
            col2 = parts[2].strip()
            cellline = parts[3].strip() if len(parts) >= 4 else ""
            assay = parts[4].strip() if len(parts) >= 5 else ""
            complex_note = parts[5].strip() if len(parts) >= 6 else ""

            # Detect format: old (ENSG in col2) or new (target name in col2)
            if col2.startswith("ENSG"):
                target_raw = ensg_to_symbol.get(col2, "")
                target_ensg = col2
            else:
                target_raw = col2

            tf_norm = normalize(tf_raw, override_map, alias_map, ensg_map)
            target_norm = normalize(target_raw, override_map, alias_map, ensg_map)

            if target_norm and (not col2.startswith("ENSG")):
                target_ensg = ensg_map.get(target_norm.upper(), "NOT_FOUND")
            elif not target_norm:
                target_ensg = "NOT_FOUND"

            print("\t".join([pmid, tf_norm, target_ensg,
                             cellline, assay, complex_note]))


if __name__ == "__main__":
    main()
