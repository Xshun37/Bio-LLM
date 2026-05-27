"""Bio-LLM evaluation criteria and metrics.

Centralized module for:
- Gold standard data loading
- Fuzzy gene name matching (isoform-aware)
- Assay and cell line matching
- LLM-vs-GT classification
- Summary metrics computation
- Gene name normalization logging
"""

import os
import re

import pandas as pd


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_GOLD_STANDARD = os.path.join(PROJECT_ROOT, "data", "raw", "finalresult.tsv")


# ---------------------------------------------------------------------------
# Gold standard data loading
# ---------------------------------------------------------------------------


def load_gold_standard(tsv_path=None):
    """Load gold standard data from finalresult.tsv.

    Returns dict: pmid -> [(tf, target, assay, cellline, ensg), ...]
    """
    tsv_path = tsv_path or DEFAULT_GOLD_STANDARD
    if not os.path.exists(tsv_path):
        return {}

    df = pd.read_csv(tsv_path, sep="\t", dtype={"PMID": str})
    result = {}
    for _, row in df.iterrows():
        pmid = str(row.get("PMID", "")).strip()
        if not pmid:
            continue
        tf = str(row.get("TF", "")).strip()
        target = str(row.get("Target", "")).strip()
        # Skip rows with empty TF/Target (nan or blank — test cases for "no relationship")
        if tf in ("", "nan") or target in ("", "nan"):
            result.setdefault(pmid, [])
            continue
        entry = (
            tf,
            target,
            str(row.get("Assay", "")).strip(),
            str(row.get("CellLine", "")).strip(),
            str(row.get("ENSG", "")).strip(),
        )
        result.setdefault(pmid, []).append(entry)
    return result


# ---------------------------------------------------------------------------
# Fuzzy gene name matching
# ---------------------------------------------------------------------------


def fuzzy_gene_match(a, b):
    """Return True if gene names match, allowing isoform suffix differences.

    Handles cases like RASSF1 vs RASSF1A by stripping a trailing single
    uppercase letter that follows a digit (a known isoform suffix pattern).
    """
    if a == b:
        return True

    def strip_isoform(name):
        m = re.search(r"^(.+\d)[A-Z]$", name)
        return m.group(1) if m else name

    a_stripped = strip_isoform(a)
    b_stripped = strip_isoform(b)
    if a_stripped == b_stripped:
        return True
    if a_stripped == b or a == b_stripped:
        return True
    return False


# ---------------------------------------------------------------------------
# Assay and cell line matching
# ---------------------------------------------------------------------------


def match_assays(llm_assay, gt_assay):
    """Check if GT assay set is a subset of LLM assay set.

    'Literature' entries in GT are treated as always matching (no experimental
    assay expected). Empty GT assay also matches anything.
    """
    if not gt_assay:
        return True
    gt_set = set()
    for a in gt_assay.split(";"):
        a_clean = a.strip().lower()
        if a_clean and a_clean != "literature":
            gt_set.add(a_clean)
    if not gt_set:
        return True  # Only "Literature" in GT — always match
    if not llm_assay:
        return False
    llm_set = {a.strip().lower() for a in llm_assay.split(";") if a.strip()}
    return gt_set.issubset(llm_set)


def _normalize_cellline(name):
    """Normalize cell line name for comparison."""
    if not name:
        return set()
    # Split on common separators
    parts = re.split(r"[,;/]+", name)
    result = set()
    for part in parts:
        cleaned = re.sub(r"\s+", "", part.strip().lower())
        # Remove common prefixes/suffixes
        cleaned = re.sub(r"^cell(line)?[:\s]*", "", cleaned)
        if cleaned and cleaned != "-" and cleaned != "nan":
            result.add(cleaned)
    return result


def match_cellline(llm_cellline, gt_cellline):
    """Fuzzy cell line matching — case-insensitive, separator-agnostic.

    Returns True if any GT cell line matches any LLM cell line.
    Empty GT cell line always matches.
    """
    if not gt_cellline or gt_cellline.strip() in ("-", "nan", ""):
        return True
    gt_set = _normalize_cellline(gt_cellline)
    if not gt_set:
        return True
    llm_set = _normalize_cellline(llm_cellline)
    if not llm_set:
        return False
    # Check if any GT cell line appears in LLM set
    return bool(gt_set.intersection(llm_set))


# ---------------------------------------------------------------------------
# LLM entry classification
# ---------------------------------------------------------------------------

# Two-level status labels
# rel_status: relationship-level (TF+Target only)
STATUS_REL_MATCH = "rel_match"
STATUS_REL_NEW_FOUND = "new_found"
STATUS_REL_NEW = "new"

# full_status: full 4D matching (TF+Target+Assay+CellLine)
STATUS_FULL_MATCH = "full_match"
STATUS_FULL_PARTIAL = "partial_match"
STATUS_FULL_NEW_FOUND = "new_found"
STATUS_FULL_NEW = "new"


def classify_llm_entry(llm_tf, llm_target, llm_assay, llm_cellline,
                       gt_entries_norm, claimed_gt=None):
    """Classify a single LLM prediction against gold standard (two-level).

    Args:
        llm_tf: normalized TF symbol from LLM
        llm_target: normalized Target symbol from LLM
        llm_assay: assay string from LLM
        llm_cellline: cell line string from LLM
        gt_entries_norm: list of (tf, target, assay, cellline, ensg) tuples
        claimed_gt: set of GT indices already claimed (for 1-to-1 matching)

    Returns:
        (rel_status, full_status, gt_index)
        rel_status: 'rel_match' | 'new_found' | 'new'
        full_status: 'full_match' | 'partial_match' | 'new_found' | 'new'
        gt_index: index of matched GT entry, or -1
    """
    claimed = claimed_gt or set()

    # Try full match first (prefer fully matching entries)
    for idx, gt in enumerate(gt_entries_norm):
        if idx in claimed:
            continue
        if fuzzy_gene_match(llm_tf, gt[0]) and fuzzy_gene_match(llm_target, gt[1]):
            assay_ok = match_assays(llm_assay, gt[2])
            cl_ok = match_cellline(llm_cellline, gt[3])
            if assay_ok and cl_ok:
                return STATUS_REL_MATCH, STATUS_FULL_MATCH, idx

    # Try partial match (TF+Target match but Assay/CellLine don't)
    for idx, gt in enumerate(gt_entries_norm):
        if idx in claimed:
            continue
        if fuzzy_gene_match(llm_tf, gt[0]) and fuzzy_gene_match(llm_target, gt[1]):
            return STATUS_REL_MATCH, STATUS_FULL_PARTIAL, idx

    # No relationship match found
    if gt_entries_norm:
        return STATUS_REL_NEW_FOUND, STATUS_FULL_NEW_FOUND, -1
    return STATUS_REL_NEW, STATUS_FULL_NEW, -1


def classify_missed_gt(gt_entries_norm, matched_gt_indices):
    """Return list of unmatched GT entries."""
    return [
        entry for i, entry in enumerate(gt_entries_norm)
        if i not in matched_gt_indices
    ]


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------


def compute_metrics(llm_data, gt_data, normalize_tf_fn, normalize_target_fn):
    """Compute two-level summary metrics from LLM results and gold standard.

    Two levels:
      - rel (relationship): matching on (TF, Target) only
      - full: matching on (TF, Target) + Assay ⊆ + CellLine ∩

    Args:
        llm_data: dict of {pmid: llm_results}
        gt_data: dict of {pmid: [(tf, target, assay, cellline, ensg), ...]}
        normalize_tf_fn: callable to normalize TF gene names
        normalize_target_fn: callable to normalize target gene names

    Returns:
        dict with all metrics
    """
    total_gt = 0
    total_llm = 0

    # Relationship-level (TF+Target)
    tp_rel = 0
    fp_rel = 0        # new_found (no TF+Target match in GT)
    total_new = 0     # PMID has no GT entries

    # Full-level (TF+Target+Assay+CellLine)
    tp_full = 0
    fp_partial = 0    # TF+Target matched but Assay/CellLine wrong
    # FP_full = fp_partial + fp_rel (all non-TP predictions)

    # Experimental-only subset (GT assay ≠ Literature)
    exp_gt = 0
    exp_tp_full = 0

    for pmid, llm_results in llm_data.items():
        gt_raw = gt_data.get(str(pmid)) or []
        gt_norm = [
            (normalize_tf_fn(tf), normalize_target_fn(target), assay, cellline, ensg)
            for tf, target, assay, cellline, ensg in gt_raw
        ]

        llm_list = llm_results if isinstance(llm_results, list) else []
        if isinstance(llm_results, dict) and llm_results.get("error"):
            continue

        total_gt += len(gt_norm)
        total_llm += len(llm_list)

        # Count experimental GT entries
        for entry in gt_raw:
            assay_val = entry[2].strip().lower() if entry[2] else ""
            if assay_val and assay_val != "literature":
                exp_gt += 1

        # Greedy 1-to-1 matching per PMID
        claimed_gt = set()
        for item in llm_list:
            if not isinstance(item, dict):
                continue
            llm_tf = _get_field(item, "tf", "TF")
            llm_target = _get_field(item, "target", "Target")
            llm_assay = _get_field(item, "assay", "Assay")
            llm_cellline = _get_field(item, "cellLine", "CellLine", "cellline")

            rel_status, full_status, gt_idx = classify_llm_entry(
                normalize_tf_fn(llm_tf), normalize_target_fn(llm_target),
                llm_assay, llm_cellline,
                gt_norm, claimed_gt,
            )

            if full_status == STATUS_FULL_MATCH:
                tp_rel += 1
                tp_full += 1
                claimed_gt.add(gt_idx)

                # Check if this matched GT entry is experimental
                gt_assay = gt_norm[gt_idx][2]
                if gt_assay.strip().lower() and gt_assay.strip().lower() != "literature":
                    exp_tp_full += 1

            elif full_status == STATUS_FULL_PARTIAL:
                tp_rel += 1
                fp_partial += 1
                claimed_gt.add(gt_idx)

            elif full_status == STATUS_FULL_NEW_FOUND:
                fp_rel += 1

            else:  # STATUS_FULL_NEW
                total_new += 1

    # Relationship-level metrics
    total_missed_rel = total_gt - tp_rel
    recall_rel = (tp_rel / total_gt * 100) if total_gt > 0 else 0
    precision_rel = (tp_rel / (tp_rel + fp_rel) * 100) if (tp_rel + fp_rel) > 0 else 0
    p_r = precision_rel / 100
    r_r = recall_rel / 100
    f1_rel = (2 * p_r * r_r / (p_r + r_r) * 100) if (p_r + r_r) > 0 else 0

    # Full-level metrics
    fp_full_total = fp_partial + fp_rel
    total_missed_full = total_gt - tp_full
    recall_full = (tp_full / total_gt * 100) if total_gt > 0 else 0
    precision_full = (tp_full / (tp_full + fp_full_total) * 100) if (tp_full + fp_full_total) > 0 else 0
    p_f = precision_full / 100
    r_f = recall_full / 100
    f1_full = (2 * p_f * r_f / (p_f + r_f) * 100) if (p_f + r_f) > 0 else 0

    # Experimental recall
    exp_recall = (exp_tp_full / exp_gt * 100) if exp_gt > 0 else 0

    return {
        "total_pmids": len(llm_data),
        "total_gt": total_gt,
        "total_llm": total_llm,
        # Relationship-level
        "tp_rel": tp_rel,
        "fp_rel": fp_rel,
        "total_missed_rel": total_missed_rel,
        "precision_rel": precision_rel,
        "recall_rel": recall_rel,
        "f1_rel": f1_rel,
        # Full-level
        "tp_full": tp_full,
        "fp_partial": fp_partial,
        "fp_rel_count": fp_rel,
        "total_missed_full": total_missed_full,
        "precision_full": precision_full,
        "recall_full": recall_full,
        "f1_full": f1_full,
        # Counts
        "total_new": total_new,
        # Experimental
        "exp_gt": exp_gt,
        "exp_tp_full": exp_tp_full,
        "exp_recall": exp_recall,
    }


# ---------------------------------------------------------------------------
# Gene name validation
# ---------------------------------------------------------------------------


def is_suspicious_gene_name(name):
    """Check if a gene name looks suspicious.

    Returns (bool, reason).
    """
    if not name:
        return True, "empty"
    s = str(name).strip()
    if len(s) < 2:
        return True, "too_short"
    if re.match(r"^\d+$", s):
        return True, "numeric"
    if re.match(r"^[^a-zA-Z0-9]+$", s):
        return True, "non_alphanumeric"
    return False, ""


# ---------------------------------------------------------------------------
# Normalization logging
# ---------------------------------------------------------------------------


def log_normalization(original, normalized, gene_type="", alias_map=None, meta=None):
    """Record a gene name normalization event.

    Returns a dict recording the before/after state, or None if unchanged.
    The gene_type is 'TF' or 'Target' for context.
    """
    if not original:
        return None
    orig_clean = str(original).strip()
    meta_dict = meta.to_dict() if hasattr(meta, "to_dict") else (meta or {})
    status = meta_dict.get("status", "")
    if orig_clean == normalized and status in ("", "identity"):
        return None
    entry = {
        "original": orig_clean,
        "normalized": normalized,
        "type": gene_type,
    }
    if meta_dict:
        entry.update({
            "status": status,
            "source": meta_dict.get("source", ""),
            "candidates": meta_dict.get("candidates", []),
            "matched_key": meta_dict.get("matched_key", ""),
        })
    return entry


def normalize_and_log(raw_name, norm_fn, gene_type, log_list):
    """Normalize a gene name and log the change if any.

    Args:
        raw_name: raw gene name before normalization
        norm_fn: normalization function (normalize_tf or normalize_target)
        gene_type: 'TF' or 'Target'
        log_list: list to append the log entry to

    Returns:
        normalized gene name string
    """
    meta_getter = getattr(norm_fn, "with_meta", None)
    meta = meta_getter(raw_name) if meta_getter else None
    normalized = meta.normalized if meta is not None else norm_fn(raw_name)
    entry = log_normalization(raw_name, normalized, gene_type=gene_type, meta=meta)
    if entry:
        log_list.append(entry)
    return normalized


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def _get_field(obj, *keys, default=""):
    """Case-insensitive field access for dict with mixed-case keys."""
    for key in keys:
        if key in obj:
            return str(obj[key])
        if key.lower() in obj:
            return str(obj[key.lower()])
        if key.upper() in obj:
            return str(obj[key.upper()])
    return default
