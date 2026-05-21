#!/usr/bin/env python3
"""Build gene symbol -> ENSG ID mapping from HGNC complete set."""

import csv
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

HGNC_PATH = os.path.join(PROJECT_ROOT, "data", "raw", "hgnc_complete_set.txt")
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "curated")
OUT_PATH = os.path.join(OUT_DIR, "gene_ensg_map.json")


def build():
    ensg_map = {}
    with open(HGNC_PATH, encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            symbol = (row.get("symbol") or "").strip().upper()
            ensg = (row.get("ensembl_gene_id") or "").strip()
            if symbol and ensg:
                ensg_map[symbol] = ensg

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(ensg_map, handle, indent=2, sort_keys=True)

    print(f"Built {len(ensg_map):,} symbol->ENSG mappings -> {OUT_PATH}")
    return ensg_map


if __name__ == "__main__":
    build()
