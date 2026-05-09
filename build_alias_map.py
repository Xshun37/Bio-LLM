#!/usr/bin/env python3
"""Build a comprehensive gene alias -> HGNC symbol map from TRRUST data + mygene."""
import csv
import json
import mygene


TRRUST_PATH = "data/raw/trrust_rawdata.human.tsv"
OUTPUT_PATH = "data/curated/gene_alias_map.json"


def main():
    # 1. Collect all unique gene names from TRRUST
    names = set()
    with open(TRRUST_PATH, "r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            if len(row) < 2:
                continue
            names.add(row[0].strip())   # TF
            names.add(row[1].strip())   # Target

    print(f"Unique gene names in TRRUST: {len(names)}")

    # 2. For each TRRUST gene, look up its official symbol + all aliases
    mg = mygene.MyGeneInfo()
    alias_map = {}

    # Batch query: for each name, get the official record with aliases
    results = mg.querymany(
        list(names),
        scopes="symbol,alias,retired",
        fields="symbol,alias,retired",
        species="human",
        returnall=False,
        verbose=False,
    )

    for entry in results:
        if "notfound" in entry or "symbol" not in entry:
            continue
        official = entry["symbol"]
        # Map all known aliases for this gene → official symbol
        for alias in entry.get("alias", []):
            alias_map[alias] = official
        retired = entry.get("retired", [])
        if isinstance(retired, list):
            for alias in retired:
                if isinstance(alias, dict):
                    alias_map[alias.get("symbol", "")] = official
        # Also map the query itself if it differs from official
        query = entry.get("query", "")
        if query and query.upper() != official.upper():
            alias_map[query] = official

    print(f"Alias mappings built: {len(alias_map)}")

    # 4. Merge with existing curated map
    existing = {}
    try:
        with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except FileNotFoundError:
        pass

    merged = {k.upper(): v for k, v in existing.items()}
    for k, v in alias_map.items():
        merged[k.upper()] = v
    merged = dict(sorted(merged.items()))

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"Total mappings (curated + auto): {len(merged)}")
    print(f"Saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
