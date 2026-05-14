"""Centralized gene alias normalization.

The runtime rule is intentionally conservative: curated overrides may resolve
role-specific aliases, HGNC identity symbols are always accepted, and HGNC
aliases are accepted only when they resolve to one unique symbol.
"""

from dataclasses import asdict, dataclass
import json
import os
import re


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CURATED_DIR = os.path.join(PROJECT_ROOT, "data", "curated")
ALIAS_INDEX_PATH = os.path.join(CURATED_DIR, "gene_alias_index.json")
LEGACY_ALIAS_MAP_PATH = os.path.join(CURATED_DIR, "gene_alias_map.json")
OVERRIDES_PATH = os.path.join(CURATED_DIR, "gene_alias_overrides.json")

STATUS_EMPTY = "empty"
STATUS_OVERRIDE = "override"
STATUS_IDENTITY = "identity"
STATUS_HGNC_ALIAS = "hgnc_alias"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_UNMAPPED = "unmapped"
STATUS_BLOCKED = "blocked"


@dataclass(frozen=True)
class NormalizationResult:
    original: str
    normalized: str
    role: str
    status: str
    source: str
    candidates: list
    matched_key: str

    def to_dict(self):
        return asdict(self)


_ALIAS_INDEX = None
_OVERRIDES = None


def project_path(*parts):
    return os.path.join(PROJECT_ROOT, *parts)


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _load_alias_index():
    global _ALIAS_INDEX
    if _ALIAS_INDEX is None:
        index = _load_json(ALIAS_INDEX_PATH, None)
        if index is None:
            legacy = _load_json(LEGACY_ALIAS_MAP_PATH, {})
            index = {
                "aliases": {
                    key: [{"symbol": value, "sources": ["legacy_alias_map"]}]
                    for key, value in legacy.items()
                },
                "official_symbols": sorted(set(legacy.values())),
            }
        _ALIAS_INDEX = index
    return _ALIAS_INDEX


def _load_overrides():
    global _OVERRIDES
    if _OVERRIDES is None:
        raw = _load_json(OVERRIDES_PATH, [])
        # Backward compatibility for the old one-level curated map if the new
        # schema has not been generated yet.
        if isinstance(raw, dict):
            raw = [
                {"alias": key, "symbol": value, "roles": ["tf", "target"], "reason": "legacy"}
                for key, value in raw.items()
            ]
        _OVERRIDES = raw if isinstance(raw, list) else []
    return _OVERRIDES


def reset_gene_alias_cache():
    """Clear cached alias data. Intended for tests and rebuild workflows."""
    global _ALIAS_INDEX, _OVERRIDES
    _ALIAS_INDEX = None
    _OVERRIDES = None


def _clean_name(raw_name):
    if raw_name is None:
        return "", ""
    name = str(raw_name).strip().upper()
    name = re.sub(r"\(.*?\)", "", name).strip()
    compact = re.sub(r"[\s/\-]+", "", name)
    return name, compact


def _candidate_keys(raw_name):
    canonical, compact = _clean_name(raw_name)
    keys = []
    for key in (canonical, compact):
        if key and key not in keys:
            keys.append(key)
    return canonical, keys


def _role_matches(override_roles, role):
    if not override_roles:
        return True
    normalized_roles = {str(item).strip().lower() for item in override_roles}
    return "all" in normalized_roles or str(role).strip().lower() in normalized_roles


def _lookup_override(keys, role):
    for override in _load_overrides():
        alias = override.get("alias", "")
        _, override_keys = _candidate_keys(alias)
        if not set(keys).intersection(override_keys):
            continue
        if not _role_matches(override.get("roles", []), role):
            continue
        action = str(override.get("action", "map")).strip().lower()
        symbol = str(override.get("symbol", "")).strip().upper()
        if action == "block":
            return "", override
        if symbol:
            return symbol, override
    return "", None


def _lookup_hgnc(keys):
    index = _load_alias_index()
    official_symbols = set(index.get("official_symbols", []))
    aliases = index.get("aliases", {})

    for key in keys:
        if key in official_symbols:
            return STATUS_IDENTITY, key, [{"symbol": key, "sources": ["official_symbol"]}]

    for key in keys:
        candidates = aliases.get(key, [])
        symbols = sorted({
            str(item.get("symbol", "")).strip().upper()
            for item in candidates
            if item.get("symbol")
        })
        if len(symbols) == 1:
            return STATUS_HGNC_ALIAS, key, candidates
        if len(symbols) > 1:
            return STATUS_AMBIGUOUS, key, candidates

    return STATUS_UNMAPPED, "", []


def normalize_gene_name_with_meta(raw_name, role=None):
    """Normalize a gene-like name and return metadata about the decision."""
    role = (role or "gene").lower()
    original = "" if raw_name is None else str(raw_name).strip()
    canonical, keys = _candidate_keys(raw_name)
    fallback = canonical or ""
    if not fallback:
        return NormalizationResult(original, "", role, STATUS_EMPTY, "none", [], "")

    override_symbol, override = _lookup_override(keys, role)
    if override and override.get("action") == "block":
        return NormalizationResult(
            original,
            fallback,
            role,
            STATUS_BLOCKED,
            "curated_override",
            [{"symbol": item.get("symbol", ""), "reason": override.get("reason", "")}
             for item in override.get("candidates", [])],
            override.get("alias", ""),
        )
    if override_symbol:
        return NormalizationResult(
            original,
            override_symbol,
            role,
            STATUS_OVERRIDE,
            "curated_override",
            [{"symbol": override_symbol, "reason": override.get("reason", "")}],
            override.get("alias", ""),
        )

    status, matched_key, candidates = _lookup_hgnc(keys)
    if status == STATUS_IDENTITY:
        return NormalizationResult(
            original, matched_key, role, status, "hgnc_official_symbol", candidates, matched_key
        )
    if status == STATUS_HGNC_ALIAS:
        symbol = str(candidates[0]["symbol"]).strip().upper()
        return NormalizationResult(
            original, symbol, role, status, "hgnc_alias_index", candidates, matched_key
        )
    if status == STATUS_AMBIGUOUS:
        return NormalizationResult(
            original, fallback, role, status, "hgnc_alias_index", candidates, matched_key
        )

    return NormalizationResult(original, fallback, role, STATUS_UNMAPPED, "none", [], "")


def normalize_gene_name(raw_name, role=None):
    return normalize_gene_name_with_meta(raw_name, role=role).normalized


def normalize_tf(name):
    return normalize_gene_name(name, role="tf")


def normalize_target(name):
    return normalize_gene_name(name, role="target")


def _normalize_tf_with_meta(name):
    return normalize_gene_name_with_meta(name, role="tf")


def _normalize_target_with_meta(name):
    return normalize_gene_name_with_meta(name, role="target")


normalize_tf.with_meta = _normalize_tf_with_meta
normalize_target.with_meta = _normalize_target_with_meta
