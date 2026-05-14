#!/usr/bin/env python3
"""Build auditable gene alias data from the HGNC complete set TSV."""

from collections import defaultdict
import csv
import json
import os
import re


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
HGNC_PATH = os.path.join(PROJECT_ROOT, "data/raw/hgnc_complete_set.txt")
CURATED_DIR = os.path.join(PROJECT_ROOT, "data/curated")
OVERRIDES_PATH = os.path.join(CURATED_DIR, "gene_alias_overrides.json")
INDEX_PATH = os.path.join(CURATED_DIR, "gene_alias_index.json")
CONFLICTS_PATH = os.path.join(CURATED_DIR, "gene_alias_conflicts.json")
LEGACY_MAP_PATH = os.path.join(CURATED_DIR, "gene_alias_map.json")


def clean_key(name):
    name = str(name or "").strip().upper()
    name = re.sub(r"\(.*?\)", "", name).strip()
    return name


def compact_key(name):
    return re.sub(r"[\s/\-]+", "", clean_key(name))


def key_variants(name):
    keys = []
    for key in (clean_key(name), compact_key(name)):
        if key and key not in keys:
            keys.append(key)
    return keys


def is_valid_alias(name):
    """Filter out aliases that are too weak to safely normalize."""
    name = clean_key(name)
    if not name or len(name) < 3:
        return False
    if re.match(r"^[\d\.\-]+$", name):
        return False
    if re.match(r"^[\s\W]+$", name):
        return False
    return True


def add_candidate(alias_index, alias, symbol, source):
    if not is_valid_alias(alias):
        return
    symbol = clean_key(symbol)
    if not symbol:
        return
    for key in key_variants(alias):
        alias_index[key][symbol].add(source)


def load_overrides(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            overrides = json.load(handle)
    except FileNotFoundError:
        return []
    if isinstance(overrides, dict):
        return [
            {"alias": key, "symbol": value, "roles": ["tf", "target"], "reason": "legacy"}
            for key, value in overrides.items()
        ]
    return overrides if isinstance(overrides, list) else []


def flatten_alias_index(alias_index):
    result = {}
    for alias in sorted(alias_index):
        result[alias] = [
            {"symbol": symbol, "sources": sorted(sources)}
            for symbol, sources in sorted(alias_index[alias].items())
        ]
    return result


def build_legacy_unique_map(alias_entries, overrides):
    role_specific_keys = set()
    global_override_symbols = {}
    for override in overrides:
        roles = {str(role).strip().lower() for role in override.get("roles", [])}
        keys = key_variants(override.get("alias", ""))
        if not roles or roles == {"all"} or roles == {"tf", "target"}:
            symbol = clean_key(override.get("symbol", ""))
            for key in keys:
                if symbol:
                    global_override_symbols[key] = symbol
            continue
        role_specific_keys.update(keys)

    legacy = {}
    for alias, candidates in alias_entries.items():
        if alias not in role_specific_keys and len(candidates) == 1:
            legacy[alias] = candidates[0]["symbol"]

    legacy.update(global_override_symbols)
    return dict(sorted(legacy.items()))


def main():
    alias_index = defaultdict(lambda: defaultdict(set))
    official_symbols = set()
    genes_processed = 0

    with open(HGNC_PATH, "r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            symbol = clean_key(row.get("symbol", ""))
            if not symbol:
                continue
            genes_processed += 1
            official_symbols.add(symbol)

            for source_column, source_name in (
                ("alias_symbol", "hgnc_alias_symbol"),
                ("prev_symbol", "hgnc_prev_symbol"),
            ):
                raw = row.get(source_column, "").strip()
                if not raw:
                    continue
                for alias in raw.split("|"):
                    alias = alias.strip().strip('"')
                    if clean_key(alias) != symbol:
                        add_candidate(alias_index, alias, symbol, source_name)

    alias_entries = flatten_alias_index(alias_index)
    conflicts = {
        alias: candidates
        for alias, candidates in alias_entries.items()
        if len({item["symbol"] for item in candidates}) > 1
    }
    overrides = load_overrides(OVERRIDES_PATH)
    index = {
        "metadata": {
            "source": "HGNC complete set",
            "hgnc_path": os.path.relpath(HGNC_PATH, PROJECT_ROOT),
            "genes_processed": genes_processed,
            "alias_count": len(alias_entries),
            "conflict_count": len(conflicts),
            "override_count": len(overrides),
        },
        "official_symbols": sorted(official_symbols),
        "aliases": alias_entries,
    }

    os.makedirs(CURATED_DIR, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, indent=2)
    with open(CONFLICTS_PATH, "w", encoding="utf-8") as handle:
        json.dump(conflicts, handle, ensure_ascii=False, indent=2)
    with open(LEGACY_MAP_PATH, "w", encoding="utf-8") as handle:
        json.dump(build_legacy_unique_map(alias_entries, overrides), handle,
                  ensure_ascii=False, indent=2)

    print(f"HGNC genes processed: {genes_processed}")
    print(f"Alias keys:           {len(alias_entries)}")
    print(f"Conflicting aliases:  {len(conflicts)}")
    print(f"Curated overrides:    {len(overrides)}")
    print(f"Saved index:          {INDEX_PATH}")
    print(f"Saved conflicts:      {CONFLICTS_PATH}")
    print(f"Saved legacy map:     {LEGACY_MAP_PATH}")


if __name__ == "__main__":
    main()
