#!/usr/bin/env python3
"""Build a clean gene alias -> HGNC symbol map from the HGNC complete set TSV."""
import csv
import json
import os
import re


HGNC_PATH = os.path.join(os.path.dirname(__file__), "..", "data/raw/hgnc_complete_set.txt")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data/curated/gene_alias_map.json")


def is_valid_alias(name):
    """Filter out garbage aliases."""
    if not name or len(name) < 3:
        return False
    if re.match(r'^[\d\.\-]+$', name):
        return False
    if re.match(r'^[\s\W]+$', name):
        return False
    return True


def main():
    alias_map = {}
    missing = 0

    with open(HGNC_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            symbol = row.get("symbol", "").strip()
            if not symbol:
                continue

            # Parse pipe-delimited alias_symbol column
            alias_str = row.get("alias_symbol", "").strip()
            if alias_str:
                for alias in alias_str.split("|"):
                    alias = alias.strip()
                    if alias and is_valid_alias(alias) and alias.upper() != symbol.upper():
                        alias_map[alias.upper()] = symbol

            # Also parse prev_symbol
            prev_str = row.get("prev_symbol", "").strip()
            if prev_str:
                for prev in prev_str.split("|"):
                    prev = prev.strip()
                    if prev and is_valid_alias(prev) and prev.upper() != symbol.upper():
                        alias_map[prev.upper()] = symbol

            # Also handle genes with no alias but with standard symbol (identity map not needed)

    # Merge with curated overrides (highest priority)
    curated_path = os.path.join(os.path.dirname(__file__), "..", "data/curated/gene_alias_curated.json")
    try:
        with open(curated_path, "r", encoding="utf-8") as f:
            curated = json.load(f)
        for k, v in curated.items():
            alias_map[k.upper()] = v
        print(f"Curated overrides merged: {len(curated)}")
    except FileNotFoundError:
        print("No curated overrides found, skipping.")

    # Sort and deduplicate
    alias_map = dict(sorted(alias_map.items()))

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(alias_map, f, ensure_ascii=False, indent=2)

    print(f"HGNC genes processed: {sum(1 for _ in open(HGNC_PATH)) - 1}")
    print(f"Valid alias mappings:  {len(alias_map)}")
    print(f"Saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
