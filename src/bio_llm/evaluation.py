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
        entry = (
            str(row.get("TF", "")).strip(),
            str(row.get("Target", "")).strip(),
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

# Status labels
STATUS_CONSISTENT = "Consistent"
STATUS_NEW_FOUND = "New Found"
STATUS_NEW = "New"
STATUS_MISSED = "Missed"


def classify_llm_entry(llm_tf, llm_target, gt_entries_norm):
    """Classify a single LLM prediction against gold standard.

    Args:
        llm_tf: normalized TF symbol from LLM
        llm_target: normalized Target symbol from LLM
        gt_entries_norm: list of (tf, target, ...) tuples (TF/Target normalized)

    Returns:
        (status, gt_index)
        status: 'Consistent' | 'New Found' | 'New'
        gt_index: index of matched GT entry, or -1
    """
    for idx, gt_entry in enumerate(gt_entries_norm):
        gt_tf, gt_target = gt_entry[0], gt_entry[1]
        if fuzzy_gene_match(llm_tf, gt_tf) and fuzzy_gene_match(llm_target, gt_target):
            return STATUS_CONSISTENT, idx

    if gt_entries_norm:
        return STATUS_NEW_FOUND, -1
    return STATUS_NEW, -1


def classify_missed_gt(gt_entries_norm, matched_gt_indices):
    """Return list of unmatched GT entries."""
    return [
        entry for i, entry in enumerate(gt_entries_norm)
        if i not in matched_gt_indices
    ]


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------


def compute_metrics(llm_data, gt_data, abstracts, normalize_tf_fn, normalize_target_fn):
    """Compute all summary metrics from LLM results and gold standard.

    Args:
        llm_data: dict of {pmid: llm_results}
        gt_data: dict of {pmid: [(tf, target, assay, cellline, ensg), ...]}
        abstracts: dict of {pmid: {abstract, gold_standard}} from parse_test_file
        normalize_tf_fn: callable to normalize TF gene names
        normalize_target_fn: callable to normalize target gene names

    Returns:
        dict with all metrics
    """
    total_gt = 0
    total_matched_gt = 0
    total_llm = 0
    total_consistent = 0
    total_new_found = 0
    total_new = 0

    # Assay / CellLine accuracy (among matched pairs)
    assay_matched = 0
    assay_total = 0
    cellline_matched = 0
    cellline_total = 0

    # Experimental-only subset metrics
    exp_gt = 0
    exp_matched_gt = 0

    for pmid, llm_results in llm_data.items():
        info = abstracts.get(str(pmid), {})
        gt_raw = gt_data.get(str(pmid)) or []
        # Normalize TF/Target in GT entries
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

        matched_gt = set()
        for item in llm_list:
            if not isinstance(item, dict):
                continue
            llm_tf = _get_field(item, "tf", "TF")
            llm_target = _get_field(item, "target", "Target")
            llm_assay = _get_field(item, "assay", "Assay")
            llm_cellline = _get_field(item, "cellLine", "CellLine", "cellline")

            status, gt_idx = classify_llm_entry(
                normalize_tf_fn(llm_tf), normalize_target_fn(llm_target),
                gt_norm,
            )
            if gt_idx >= 0:
                matched_gt.add(gt_idx)
                total_consistent += 1

                # Check if this matched GT entry is experimental
                gt_assay = gt_norm[gt_idx][2]
                gt_cellline = gt_norm[gt_idx][3]
                if gt_assay.strip().lower() and gt_assay.strip().lower() != "literature":
                    exp_matched_gt += 1

                # Assay and CellLine accuracy (for matched pairs with non-empty GT)
                if gt_assay.strip():
                    assay_total += 1
                    if match_assays(llm_assay, gt_assay):
                        assay_matched += 1
                if gt_cellline.strip() and gt_cellline.strip() not in ("-", "nan"):
                    cellline_total += 1
                    if match_cellline(llm_cellline, gt_cellline):
                        cellline_matched += 1
            elif gt_norm:
                total_new_found += 1
            else:
                total_new += 1

        total_matched_gt += len(matched_gt)

    total_missed = total_gt - total_matched_gt
    recall = (total_matched_gt / total_gt * 100) if total_gt > 0 else 0
    evaluable_llm = total_llm - total_new_found - total_new
    evaluable_precision = (total_consistent / evaluable_llm * 100) if evaluable_llm > 0 else 0
    assay_accuracy = (assay_matched / assay_total * 100) if assay_total > 0 else 0
    cellline_accuracy = (cellline_matched / cellline_total * 100) if cellline_total > 0 else 0
    exp_recall = (exp_matched_gt / exp_gt * 100) if exp_gt > 0 else 0

    return {
        "total_pmids": len(llm_data),
        "total_gt": total_gt,
        "total_matched_gt": total_matched_gt,
        "total_llm": total_llm,
        "total_consistent": total_consistent,
        "total_new_found": total_new_found,
        "total_new": total_new,
        "total_missed": total_missed,
        "recall": recall,
        "evaluable_precision": evaluable_precision,
        "assay_matched": assay_matched,
        "assay_total": assay_total,
        "assay_accuracy": assay_accuracy,
        "cellline_matched": cellline_matched,
        "cellline_total": cellline_total,
        "cellline_accuracy": cellline_accuracy,
        "exp_gt": exp_gt,
        "exp_matched_gt": exp_matched_gt,
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
