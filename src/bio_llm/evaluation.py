"""Bio-LLM evaluation criteria and metrics.

Centralized module for:
- Direction normalization
- Fuzzy gene name matching (isoform-aware)
- LLM-vs-GT classification
- Summary metrics computation
- Gene name normalization logging
"""

import re

# ---------------------------------------------------------------------------
# Direction normalization
# ---------------------------------------------------------------------------


def normalize_direction(raw_dir):
    """Normalize direction to canonical 'Activation' or 'Repression'.

    Handles variants: 'inhibition' → 'Repression',
    'Synergistic Activation' → 'Activation', etc.
    """
    if not raw_dir:
        return ""
    d = str(raw_dir).strip().lower()
    if "activation" in d:
        return "Activation"
    if "repression" in d or "inhibition" in d:
        return "Repression"
    return str(raw_dir).strip()


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
# LLM entry classification
# ---------------------------------------------------------------------------

# Status labels used across evaluation and reporting
STATUS_CONSISTENT = "Consistent"
STATUS_CONFLICT = "Conflict"
STATUS_NEW_FOUND = "New Found"
STATUS_NEW = "New"
STATUS_MISSED = "Missed"


def classify_llm_entry(llm_tf, llm_target, llm_dir, gt_entries_norm):
    """Classify a single LLM prediction against TRRUST ground truth.

    Args:
        llm_tf: normalized TF symbol from LLM
        llm_target: normalized Target symbol from LLM
        llm_dir: raw direction string from LLM
        gt_entries_norm: list of (tf, target, direction) tuples (all normalized)

    Returns:
        (status, gt_direction, gt_index)
        status: 'Consistent' | 'Conflict' | 'New Found' | 'New'
        gt_direction: matched GT direction string, or None
        gt_index: index of matched GT entry, or -1
    """
    for idx, (gt_tf, gt_target, gt_dir) in enumerate(gt_entries_norm):
        if fuzzy_gene_match(llm_tf, gt_tf) and fuzzy_gene_match(llm_target, gt_target):
            if normalize_direction(llm_dir) == normalize_direction(gt_dir):
                return STATUS_CONSISTENT, gt_dir, idx
            return STATUS_CONFLICT, gt_dir, idx

    if gt_entries_norm:
        return STATUS_NEW_FOUND, None, -1
    return STATUS_NEW, None, -1


def classify_missed_gt(gt_entries_norm, matched_gt_indices):
    """Return list of unmatched GT entries: [(tf, target, direction), ...]."""
    return [
        entry for i, entry in enumerate(gt_entries_norm)
        if i not in matched_gt_indices
    ]


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------


def compute_metrics(llm_data, gt_data, abstracts, normalize_tf_fn, normalize_target_fn):
    """Compute all summary metrics from LLM results and ground truth.

    Args:
        llm_data: dict of {pmid: llm_results}
        gt_data: dict of {pmid: [(tf, target, direction), ...]} from trrust_by_pmid
        abstracts: dict of {pmid: {abstract, trrust_entries}} from parse_abstracts_file
        normalize_tf_fn: callable to normalize TF gene names
        normalize_target_fn: callable to normalize target gene names

    Returns:
        dict with keys: total_pmids, total_gt, total_matched_gt, total_llm,
        total_consistent, total_conflict, total_new_found, total_new,
        total_missed, recall, overall_precision, evaluable_precision,
        direction_accuracy
    """
    total_gt = 0
    total_matched_gt = 0
    total_llm = 0
    total_consistent = 0
    total_conflict = 0
    total_new_found = 0
    total_new = 0

    for pmid, llm_results in llm_data.items():
        info = abstracts.get(str(pmid), {})
        gt_raw = gt_data.get(str(pmid)) or info.get("trrust_entries", [])
        gt_norm = [(normalize_tf_fn(tf), normalize_target_fn(target), dr)
                   for tf, target, dr in gt_raw]
        llm_list = llm_results if isinstance(llm_results, list) else []
        if isinstance(llm_results, dict) and llm_results.get("error"):
            continue

        total_gt += len(gt_norm)
        total_llm += len(llm_list)

        matched_gt = set()
        for item in llm_list:
            if not isinstance(item, dict):
                continue
            llm_tf = _get_field(item, "tf", "TF")
            llm_target = _get_field(item, "target", "Target")
            llm_dir = _get_field(item, "direction", "Direction")
            _, gt_dir, gt_idx = classify_llm_entry(
                normalize_tf_fn(llm_tf), normalize_target_fn(llm_target),
                llm_dir, gt_norm,
            )
            if gt_idx >= 0:
                matched_gt.add(gt_idx)
                if normalize_direction(llm_dir) == normalize_direction(gt_dir):
                    total_consistent += 1
                else:
                    total_conflict += 1
            elif gt_norm:
                total_new_found += 1
            else:
                total_new += 1

        total_matched_gt += len(matched_gt)

    total_missed = total_gt - total_matched_gt
    recall = (total_matched_gt / total_gt * 100) if total_gt > 0 else 0
    precision = ((total_consistent + total_conflict) / total_llm * 100) if total_llm > 0 else 0
    evaluable_llm = total_llm - total_new_found - total_new
    evaluable_precision = ((total_consistent + total_conflict) / evaluable_llm * 100) if evaluable_llm > 0 else 0
    denom_dir = total_consistent + total_conflict
    direction_accuracy = (total_consistent / denom_dir * 100) if denom_dir > 0 else 0

    return {
        "total_pmids": len(llm_data),
        "total_gt": total_gt,
        "total_matched_gt": total_matched_gt,
        "total_llm": total_llm,
        "total_consistent": total_consistent,
        "total_conflict": total_conflict,
        "total_new_found": total_new_found,
        "total_new": total_new,
        "total_missed": total_missed,
        "recall": recall,
        "overall_precision": precision,
        "evaluable_precision": evaluable_precision,
        "direction_accuracy": direction_accuracy,
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


def log_normalization(original, normalized, gene_type="", alias_map=None):
    """Record a gene name normalization event.

    Returns a dict recording the before/after state, or None if unchanged.
    The gene_type is 'TF' or 'Target' for context.
    """
    if not original:
        return None
    orig_clean = str(original).strip()
    if orig_clean == normalized:
        return None
    return {
        "original": orig_clean,
        "normalized": normalized,
        "type": gene_type,
    }


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
    normalized = norm_fn(raw_name)
    entry = log_normalization(raw_name, normalized, gene_type=gene_type)
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
