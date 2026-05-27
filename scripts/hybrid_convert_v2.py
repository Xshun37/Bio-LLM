"""Hybrid PDF→text v2: Nougat + pymupdf4llm with paragraph-level hallucination repair.

Two modes:
  --existing   Post-process saved Nougat text (no GPU, fast iteration)
  --full       Full pipeline: Nougat inference + pymupdf4llm + repair

Usage::
    # Quick test with existing Nougat output
    python scripts/hybrid_convert_v2.py --existing --pmid 10453008

    # Full pipeline for new papers
    python scripts/hybrid_convert_v2.py --full --pmid 12345678

    # Detection report only (no repair)
    python scripts/hybrid_convert_v2.py --detect
"""

import argparse
import os
import re
import sys
import time
from collections import Counter
from difflib import SequenceMatcher

# ── Paths ──
BASE = "/home/bioxs/Bioproduce/Bio-LLM"
PDF_DIR = os.path.join(BASE, "data/raw/papers")
NOUGAT_DIR = os.path.join(BASE, "data/raw/papers_txt/Nougat")
HYBRID_DIR = os.path.join(BASE, "data/raw/papers_txt/hybrid")
REPORT_DIR = os.path.join(BASE, "data/raw/papers_txt/hybrid_v2")

DPI = 150
LINE_DUP_THRESHOLD = 0.20
TOKEN_REPEAT_THRESHOLD = 10
CHAR_REPEAT_THRESHOLD = 20

COMMON_WORDS = frozenset(
    "the and for with that this from are was but not can has have been were "
    "which their other into than".split()
)

# ── Metadata patterns (from clean_pdf_txt.py) ──
META_PATTERNS = [
    r"^THE JOURNAL OF BIOLOGICAL CHEMISTRY",
    r"^Vol\.\s*\d+", r"^VoL\.\s*\d+",
    r"^Printed in", r"^Copyright",
    r"^\d{4} by ", r"^Received for publication",
    r"^Published,\s+\w+", r"^DOI\s+\d+",
    r"^ISSN:", r"^To cite this article:",
    r"^To link to this article:", r"^Published online:",
    r"^Submit your article", r"^Article views:",
    r"^View related articles", r"^Citing articles:",
    r"^Full Terms & Conditions", r"^https://doi\.org/",
    r"^https://www\.tandfonline",
    r"^Molecular and Cellular Biology$",
    r"^\d{4}\s+Published by Elsevier",
    r"^Edited by ", r"^Available online \d+",
    r"^\(Received", r"^Accepted \d+",
]


# ═══════════════════════════════════════════════════════════
#  1. HALLUCINATION DETECTORS
# ═══════════════════════════════════════════════════════════

def _detect_line_dup(text):
    """Detect line-level duplication. Returns (bool, boundary_info|None)."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) < 5:
        return False, None
    dup_ratio = 1 - len(set(lines)) / len(lines)
    if dup_ratio > LINE_DUP_THRESHOLD:
        return True, {"dup_ratio": round(dup_ratio, 3)}
    return False, None


def _detect_token_rep(text):
    """Detect token-level repetition. Returns (bool, boundary_info|None)."""
    # Pattern 1 (new): backreference — any token (2+ chars) repeated 10+ times
    # Catches: "_Hind_III_III_III", "_Nh_U_M_M_M_M", "ClClClCl", etc.
    m = re.search(r"(\w{2,})(?:\W*\1){9,}", text)
    if m:
        return True, {
            "token": m.group(1),
            "count": text.count(m.group(1)),
            "start": m.start(),
            "end": m.end(),
        }

    # Pattern 2: word/markdown repeated with separators
    # e.g. "_i.e._, a _i.e._, a"
    for m in re.finditer(
        r"((?:_?\w[\w.]*_?)(?:[,.\s]+_?\w[\w.]*_?){2,})", text
    ):
        span = m.group(1)
        tokens = re.findall(r"_?\w[\w.]*_?", span)
        if not tokens:
            continue
        first = tokens[0]
        count = tokens.count(first)
        if count >= TOKEN_REPEAT_THRESHOLD:
            return True, {
                "token": first,
                "count": count,
                "start": m.start(),
                "end": m.end(),
            }

    # Pattern 3: single word appearing many times in a single line
    for line in text.split("\n"):
        words = re.findall(r"[A-Za-z][A-Za-z0-9_.]{2,}", line)
        if len(words) < TOKEN_REPEAT_THRESHOLD:
            continue
        counter = Counter(words)
        for word, count in counter.most_common(3):
            if word.lower() in COMMON_WORDS:
                continue
            if count >= TOKEN_REPEAT_THRESHOLD and count / len(words) > 0.15:
                first_pos = text.find(word)
                last_pos = text.rfind(word) + len(word)
                return True, {
                    "token": word,
                    "count": count,
                    "start": first_pos,
                    "end": last_pos,
                }

    return False, None


def _detect_sentence_rep(text):
    """Detect sentence-level repetition via 8-gram. Returns (bool, boundary_info|None)."""
    normalized = re.sub(r"\(see[^)]*\)", "", text)
    normalized = re.sub(r"\[\d+(?:[-–,]\d+)*\]", "", normalized)
    normalized = re.sub(r"\(\d+(?:[-–,]\d+)*\)", "", normalized)
    words = normalized.split()
    if len(words) <= 50:
        return False, None
    ngram_size = 8
    ngrams = [
        tuple(words[i : i + ngram_size])
        for i in range(len(words) - ngram_size + 1)
    ]
    ngram_counts = Counter(ngrams)
    for ngram, count in ngram_counts.most_common(5):
        if count >= 4:
            ngram_str = " ".join(ngram)
            pos = text.find(ngram[0])
            return True, {
                "ngram": ngram_str,
                "count": count,
                "start": max(0, pos),
            }
    return False, None


def _detect_char_rep(text):
    """Detect single-character repetition (e.g. 'MMMMMMM...'). Returns (bool, boundary_info|None)."""
    m = re.search(r"(\w)\1{19,}", text)
    if m:
        return True, {
            "char": m.group(1),
            "length": m.end() - m.start(),
            "start": m.start(),
            "end": m.end(),
        }
    return False, None


def _detect_cross_para_dup(paragraphs):
    """Detect cross-paragraph duplication: same text appearing in many paragraphs.

    This catches cases like PMID 10082553 where the author name is repeated
    as a separate paragraph hundreds of times.

    Args:
        paragraphs: list of paragraph strings

    Returns:
        set of indices that are duplicated, or empty set
    """
    if len(paragraphs) < 10:
        return set()

    # Count paragraph occurrences (normalize for comparison)
    para_counter = Counter()
    para_groups = {}  # normalized_text -> list of indices
    for i, p in enumerate(paragraphs):
        key = p.strip().lower()
        if len(key) < 5:
            continue  # skip very short paragraphs
        para_counter[key] += 1
        para_groups.setdefault(key, []).append(i)

    bad_indices = set()
    for text, count in para_counter.most_common(5):
        if count >= 5 and count / len(paragraphs) > 0.3:
            for idx in para_groups[text]:
                bad_indices.add(idx)

    return bad_indices


def detect_paragraph_hallucination(paragraph):
    """Run all 4 detectors on a paragraph.

    Returns:
        (is_hallucinated: bool,
         reason: str|None,      # "line_dup" / "token_rep" / "sentence_rep" / "char_rep"
         boundary: dict|None)   # position info for repair
    """
    for name, detector in [
        ("line_dup", _detect_line_dup),
        ("token_rep", _detect_token_rep),
        ("sentence_rep", _detect_sentence_rep),
        ("char_rep", _detect_char_rep),
    ]:
        hit, boundary = detector(paragraph)
        if hit:
            return True, name, boundary
    return False, None, None


# ═══════════════════════════════════════════════════════════
#  2. PAGE / TEXT ASSESSMENT
# ═══════════════════════════════════════════════════════════

def split_paragraphs(text):
    """Split text into paragraphs by double-newline, filtering empties."""
    return [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]


def assess_page(nougat_text):
    """Assess hallucination severity for a page of Nougat text.

    Returns:
        severity: "none" | "minor" | "moderate" | "severe"
        details:  dict with per-paragraph results
    """
    paras = split_paragraphs(nougat_text)
    if not paras:
        return "none", {}

    # Phase 1: per-paragraph detection
    bad_indices = []
    results = []
    for i, para in enumerate(paras):
        is_hall, reason, boundary = detect_paragraph_hallucination(para)
        results.append({
            "index": i,
            "is_hallucinated": is_hall,
            "reason": reason,
            "boundary": boundary,
            "preview": para[:80] + ("..." if len(para) > 80 else ""),
        })
        if is_hall:
            bad_indices.append(i)

    # Phase 2: cross-paragraph duplication
    cross_dup_indices = _detect_cross_para_dup(paras)
    for idx in cross_dup_indices:
        if not results[idx]["is_hallucinated"]:
            results[idx]["is_hallucinated"] = True
            results[idx]["reason"] = "cross_para_dup"
            bad_indices.append(idx)
    bad_indices = sorted(set(bad_indices))

    n_bad = len(bad_indices)
    n_total = len(paras)

    # Severe: line_dup or cross_para_dup detected, or >50% paragraphs bad
    has_line_dup = any(
        r["reason"] in ("line_dup", "cross_para_dup")
        for r in results
        if r["is_hallucinated"]
    )
    if has_line_dup or n_bad > n_total * 0.5:
        severity = "severe"
    elif n_bad == 0:
        severity = "none"
    elif n_bad <= 2 and n_total >= 3:
        severity = "moderate"
    else:
        severity = "severe"

    return severity, {
        "paragraphs": results,
        "bad_indices": bad_indices,
        "n_total": n_total,
        "n_bad": n_bad,
    }


# ═══════════════════════════════════════════════════════════
#  3. PYMUPDF4LLM EXTRACTION & CLEANING
# ═══════════════════════════════════════════════════════════

def extract_pymupdf4llm(pdf_path):
    """Extract per-page Markdown via pymupdf4llm."""
    import pymupdf4llm
    pages = pymupdf4llm.to_markdown(pdf_path, page_chunks=True)
    return pages


def _is_meta_line(line):
    """Check if a line is journal metadata."""
    stripped = line.strip()
    if not stripped:
        return False
    for pat in META_PATTERNS:
        if re.search(pat, stripped, re.IGNORECASE):
            return True
    return False


def clean_p4_text(text):
    """Clean pymupdf4llm output: remove noise, merge lines, truncate references."""
    lines = text.split("\n")
    cleaned = []
    skip_until_blank = False
    in_refs = False

    for line in lines:
        stripped = line.strip()

        # Truncate at References section (allow **bold**, optional # headers)
        if re.match(
            r"^(#+\s*)?\*{0,2}\s*(References|REFERENCES|Bibliography)\s*\*{0,2}\s*$",
            stripped,
            re.IGNORECASE,
        ):
            in_refs = True
            continue
        if in_refs:
            continue

        # Skip metadata lines
        if _is_meta_line(stripped):
            continue

        # Skip standalone page numbers (short line, all digits)
        if stripped and len(stripped) <= 6 and stripped.isdigit():
            continue

        # Skip image placeholders: ![...](...)
        if re.match(r"^!\[.*?\]\(.*?\)\s*$", stripped):
            continue

        # Skip very short non-alpha lines (likely artifacts)
        if stripped and len(stripped) <= 5 and not stripped.isalpha():
            continue

        cleaned.append(line)

    text = "\n".join(cleaned)

    # Merge hyphenated line breaks
    text = re.sub(r"(\w)-\n\s*(\w)", r"\1\2", text)

    # Collapse 3+ blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ═══════════════════════════════════════════════════════════
#  4. PARAGRAPH ALIGNMENT
# ═══════════════════════════════════════════════════════════

def _is_header(para):
    """Check if a paragraph is a Markdown header."""
    return bool(re.match(r"^#{1,6}\s+", para))


def _normalize_for_match(text):
    """Normalize text for fuzzy matching (strip markdown, LaTeX, lowercase)."""
    text = re.sub(r"#{1,6}\s*", "", text)          # strip headers
    text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)  # strip bold/italic
    text = re.sub(r"\\?\(.*?\\?\)", "", text)       # strip LaTeX math
    text = re.sub(r"\s+", " ", text)                # normalize whitespace
    return text.lower().strip()


def align_paragraphs(nougat_paras, p4_paras, bad_indices=None):
    """Align Nougat paragraphs to pymupdf4llm paragraphs.

    Strategy:
      1. Use section headers as anchor points
      2. Within each section, align by position + fuzzy match
      3. Hallucination paragraphs: skip fuzzy match, use position fallback
      4. Fallback: positional mapping

    Args:
        nougat_paras: list of Nougat paragraphs
        p4_paras: list of pymupdf4llm paragraphs
        bad_indices: set/list of hallucination paragraph indices (skip fuzzy match)

    Returns:
        mapping: dict {nougat_idx: p4_idx}
    """
    mapping = {}
    if not nougat_paras or not p4_paras:
        return mapping

    bad_set = set(bad_indices) if bad_indices else set()

    # Extract header anchors from both
    n_headers = [
        (i, _normalize_for_match(p))
        for i, p in enumerate(nougat_paras)
        if _is_header(p)
    ]
    p_headers = [
        (i, _normalize_for_match(p))
        for i, p in enumerate(p4_paras)
        if _is_header(p)
    ]

    # Match headers between nougat and p4
    header_pairs = []
    used_p = set()
    for n_idx, n_text in n_headers:
        best_score = 0
        best_p_idx = None
        for p_idx, p_text in p_headers:
            if p_idx in used_p:
                continue
            score = SequenceMatcher(None, n_text, p_text).ratio()
            if score > best_score:
                best_score = score
                best_p_idx = p_idx
        if best_score > 0.5 and best_p_idx is not None:
            header_pairs.append((n_idx, best_p_idx))
            mapping[n_idx] = best_p_idx
            used_p.add(best_p_idx)

    # Between each pair of matched headers, align paragraphs by position
    # Add sentinel anchors
    n_anchors = [(-1, -1)] + header_pairs + [
        (len(nougat_paras), len(p4_paras))
    ]
    n_anchors.sort()

    for k in range(len(n_anchors) - 1):
        n_start, p_start = n_anchors[k]
        n_end, p_end = n_anchors[k + 1]

        # Paragraphs between these anchors (exclusive of anchors)
        n_range = list(range(n_start + 1, n_end))
        p_range = list(range(p_start + 1, p_end))

        if not n_range or not p_range:
            continue

        # Try fuzzy matching for each nougat paragraph
        for ni in n_range:
            if ni in mapping:
                continue

            # Hallucination paragraphs: skip fuzzy match, use position fallback
            # (the hallucinated text doesn't exist in p4, so fuzzy match
            # will match the wrong p4 paragraph)
            if ni in bad_set:
                pos_idx = ni - n_start - 1
                if pos_idx < len(p_range):
                    mapping[ni] = p_range[pos_idx]
                continue

            n_norm = _normalize_for_match(nougat_paras[ni][:200])
            if len(n_norm) < 10:
                # Too short for fuzzy match, use positional
                pos_idx = ni - n_start - 1
                if pos_idx < len(p_range):
                    mapping[ni] = p_range[pos_idx]
                continue

            best_score = 0
            best_pi = None
            for pi in p_range:
                if pi in mapping.values():
                    continue
                p_norm = _normalize_for_match(p4_paras[pi][:200])
                score = SequenceMatcher(None, n_norm, p_norm).ratio()
                if score > best_score:
                    best_score = score
                    best_pi = pi

            if best_score > 0.4 and best_pi is not None:
                mapping[ni] = best_pi
            else:
                # Positional fallback
                pos_idx = ni - n_start - 1
                if pos_idx < len(p_range):
                    mapping[ni] = p_range[pos_idx]

    return mapping


# ═══════════════════════════════════════════════════════════
#  5. REPAIR STRATEGIES
# ═══════════════════════════════════════════════════════════

def _strip_repeated_tokens(text, boundary):
    """Remove repeated token cluster, keeping the first occurrence.

    The detector returns a boundary spanning the *entire* repeated cluster
    (first valid occurrence + all duplicates).  The old implementation deleted
    the whole span, destroying table content.  This version collapses the
    cluster down to a single copy of the repeated token so that surrounding
    text (e.g. LaTeX table structure) is preserved.
    """
    if not boundary or "start" not in boundary:
        return text

    start = boundary["start"]
    end = boundary["end"]
    matched = text[start:end]

    if len(matched) == 0:
        return text

    # ── char_rep: boundary covers e.g. "MMMMMMMM..." (20+ identical chars) ──
    token = boundary.get("token")
    if not token:
        # char_rep boundary has "char" key; keep one copy of the char
        ch = boundary.get("char", matched[0])
        collapsed = ch
    else:
        # ── token_rep: collapse "CTGCTGCTG..." → "CTG" ──
        # Replace the entire matched region with a single copy of the token
        collapsed = token

    before = text[:start]
    after = text[end:]
    result = before + collapsed + after

    # Clean up potential double spaces at join points
    result = re.sub(r"  +", " ", result)
    return result.strip()


def _is_p4_quality_ok(p4_text, nougat_text):
    """Check if p4 replacement text is usable (not full of  or █ blocks)."""
    if not p4_text or len(p4_text.strip()) < 10:
        return False
    # Reject if >5% replacement characters (U+FFFD) or block chars
    bad_chars = p4_text.count("�") + p4_text.count("█")
    if bad_chars / max(len(p4_text), 1) > 0.05:
        return False
    # Reject if p4 is much shorter than Nougat (< 20% of Nougat length)
    if len(p4_text) < len(nougat_text) * 0.2:
        return False
    return True


def repair_page(nougat_text, p4_text, severity, details):
    """Apply repair strategy based on severity.

    Returns:
        repaired_text: str
        repair_info: dict with stats
    """
    paras = split_paragraphs(nougat_text)
    p4_paras = split_paragraphs(p4_text)
    bad_indices = details.get("bad_indices", [])
    results = details.get("paragraphs", [])

    if severity == "none":
        return nougat_text, {"action": "keep", "n_replaced": 0}

    if severity == "severe":
        # Check if p4 is usable before replacing entire page
        if _is_p4_quality_ok(p4_text, nougat_text):
            return p4_text, {
                "action": "page_replace",
                "n_replaced": len(paras),
            }
        # p4 is bad quality — fall through to paragraph-level repair
        severity = "moderate"

    # moderate: replace only bad paragraphs
    alignment = align_paragraphs(paras, p4_paras, bad_indices=bad_indices)
    repaired_paras = []
    n_replaced = 0
    n_inline = 0

    for i, para in enumerate(paras):
        if i not in bad_indices:
            repaired_paras.append(para)
            continue

        result = results[i]

        # ── Priority 1: inline strip for token_rep / char_rep ──
        if result["reason"] in ("token_rep", "char_rep") and result.get("boundary"):
            stripped = _strip_repeated_tokens(para, result["boundary"])
            # Use inline if result has meaningful content (> 50 chars)
            if len(stripped) > 50:
                repaired_paras.append(stripped)
                n_inline += 1
                continue

        # ── Priority 2: replace with p4 paragraph ──
        p4_idx = alignment.get(i)
        if p4_idx is not None and p4_idx < len(p4_paras):
            replacement = p4_paras[p4_idx]
            if _is_p4_quality_ok(replacement, para):
                repaired_paras.append(replacement)
                n_replaced += 1
                continue

        # ── Priority 3: keep original (even if hallucinated) ──
        repaired_paras.append(para)

    return "\n\n".join(repaired_paras), {
        "action": "paragraph_replace",
        "n_replaced": n_replaced,
        "n_inline": n_inline,
    }


# ═══════════════════════════════════════════════════════════
#  6. NOUGAT MODEL (for --full mode)
# ═══════════════════════════════════════════════════════════

def load_nougat():
    """Load Nougat model from HuggingFace."""
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    import torch
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel

    print("Loading Nougat model...")
    t0 = time.time()
    processor = TrOCRProcessor.from_pretrained("facebook/nougat-base")
    model = VisionEncoderDecoderModel.from_pretrained("facebook/nougat-base")
    model.to("cuda")
    model.eval()
    print(f"Model loaded in {time.time() - t0:.1f}s")
    return processor, model


def nougat_page(processor, model, img):
    """Run Nougat inference on a single page image."""
    import torch

    pixel_values = processor(img, return_tensors="pt").pixel_values.to("cuda")
    with torch.no_grad():
        outputs = model.generate(pixel_values, min_length=1, max_length=3584)
    return processor.batch_decode(outputs, skip_special_tokens=True)[0].strip()


def page_to_image(pdf_path, page_idx):
    """Render a PDF page to PIL Image."""
    import fitz
    from PIL import Image

    doc = fitz.open(pdf_path)
    page = doc[page_idx]
    pix = page.get_pixmap(dpi=DPI)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()
    return img


# ═══════════════════════════════════════════════════════════
#  7. MAIN PROCESSING
# ═══════════════════════════════════════════════════════════

def clean_nougat_page(text):
    """Remove references section and collapse blank lines."""
    # Match References/REFERENCES/Bibliography with optional # headers and **bold**
    text = re.split(
        r"\n(?:#+\s*)?\*{0,2}\s*(?:References|REFERENCES|Bibliography)\s*\*{0,2}\s*\n",
        text,
        maxsplit=1,
        flags=re.DOTALL | re.IGNORECASE,
    )[0]
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def process_paper_existing(nougat_text, p4_text):
    """Process a paper in --existing mode (no GPU).

    Args:
        nougat_text: saved Nougat output (joined, no page boundaries)
        p4_text: pymupdf4llm output (cleaned)

    Returns:
        (repaired_text, stats)
    """
    severity, details = assess_page(nougat_text)

    if severity == "none":
        cleaned = clean_nougat_page(nougat_text)
        return cleaned, {"severity": "none", "action": "keep"}

    cleaned_p4 = clean_p4_text(p4_text)
    repaired, repair_info = repair_page(nougat_text, cleaned_p4, severity, details)
    repaired = clean_nougat_page(repaired)

    return repaired, {
        "severity": severity,
        **repair_info,
        "n_total_paras": details.get("n_total", 0),
        "n_bad_paras": details.get("n_bad", 0),
    }


def process_paper_full(pdf_path, processor, model):
    """Process a paper in --full mode (Nougat + pymupdf4llm, page by page).

    Returns:
        (final_markdown, stats)
    """
    import fitz

    pmid = os.path.basename(pdf_path).replace(".pdf", "")
    doc = fitz.open(pdf_path)
    n_pages = len(doc)
    doc.close()

    # Extract pymupdf4llm output
    p4_pages = extract_pymupdf4llm(pdf_path)

    pages_out = []
    nougat_raw_pages = []  # collect raw Nougat output for archival
    stats = {"nougat": 0, "p4_full": 0, "p4_partial": 0, "p4_inline": 0}

    for i in range(n_pages):
        # Nougat inference
        img = page_to_image(pdf_path, i)
        t0 = time.time()
        nougat_text = nougat_page(processor, model, img)
        elapsed = time.time() - t0
        nougat_raw_pages.append(nougat_text)

        # p4 text for this page
        p4_text = ""
        if i < len(p4_pages):
            p4_text = clean_p4_text(p4_pages[i].get("text", ""))

        # Assess
        severity, details = assess_page(nougat_text)

        if severity == "none":
            pages_out.append(clean_nougat_page(nougat_text))
            stats["nougat"] += 1
            tag = "Nougat OK"
        else:
            repaired, repair_info = repair_page(
                nougat_text, p4_text, severity, details
            )
            pages_out.append(clean_nougat_page(repaired))
            action = repair_info["action"]
            if action == "page_replace":
                stats["p4_full"] += 1
                tag = f"SEVERE→p4"
            elif action == "paragraph_replace":
                stats["p4_partial"] += 1
                tag = f"MOD→replace {repair_info['n_replaced']}para"
            else:
                stats["p4_inline"] += 1
                tag = f"MINOR→inline"

        print(
            f"    p{i + 1}/{n_pages}: {tag} "
            f"({len(nougat_text)} chars) [{elapsed:.1f}s]"
        )

    # Save Nougat raw output for human review
    os.makedirs(NOUGAT_DIR, exist_ok=True)
    nougat_out = os.path.join(NOUGAT_DIR, f"{pmid}.txt")
    with open(nougat_out, "w", encoding="utf-8") as f:
        f.write("\n\n".join(nougat_raw_pages))

    final = "\n\n".join(pages_out)
    return final, stats


def generate_detection_report(pmids):
    """Generate a detection report for existing Nougat texts."""
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, "detection_report.txt")

    lines = [
        "=" * 70,
        "HALLUCINATION DETECTION REPORT",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"PMIDs: {', '.join(pmids)}",
        "=" * 70,
        "",
    ]

    summary = []

    for pmid in pmids:
        nougat_path = os.path.join(NOUGAT_DIR, f"{pmid}.txt")
        if not os.path.exists(nougat_path):
            lines.append(f"[{pmid}] SKIP: no Nougat output\n")
            continue

        with open(nougat_path, "r", encoding="utf-8") as f:
            text = f.read()

        severity, details = assess_page(text)
        paras = details.get("paragraphs", [])
        n_total = details.get("n_total", 0)
        n_bad = details.get("n_bad", 0)

        lines.append(f"{'─' * 70}")
        lines.append(f"PMID: {pmid}  |  Severity: {severity}  |  "
                      f"Bad paras: {n_bad}/{n_total}")
        lines.append(f"File size: {len(text)} chars")
        lines.append("")

        for r in paras:
            marker = "✗" if r["is_hallucinated"] else "✓"
            reason_str = f" [{r['reason']}]" if r["reason"] else ""
            lines.append(f"  {marker} para[{r['index']}]{reason_str}: "
                          f"{r['preview']}")

        lines.append("")
        summary.append(f"  {pmid}: {severity} ({n_bad}/{n_total} bad)")

    lines.insert(5, "SUMMARY:")
    for i, s in enumerate(summary):
        lines.insert(6 + i, s)
    lines.insert(6 + len(summary), "")

    report_text = "\n".join(lines)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(report_text)
    print(f"\nReport saved to: {report_path}")
    return report_text


# ═══════════════════════════════════════════════════════════
#  8. CLI
# ═══════════════════════════════════════════════════════════

EXISTING_PMIDS = [
    "10082553", "10453008", "10978529", "11013233",
    "11706010", "11854297", "14642566", "15178343",
]


def main():
    parser = argparse.ArgumentParser(description="Hybrid PDF→text v2")
    parser.add_argument("--existing", action="store_true",
                        help="Post-process saved Nougat text (no GPU)")
    parser.add_argument("--full", action="store_true",
                        help="Full pipeline: Nougat + pymupdf4llm")
    parser.add_argument("--detect", action="store_true",
                        help="Detection report only (no repair)")
    parser.add_argument("--pmid", type=str, help="Single PMID to process")
    parser.add_argument("--pmids", nargs="+", help="Multiple PMIDs")
    args = parser.parse_args()

    if args.detect:
        pmids = args.pmids or EXISTING_PMIDS
        generate_detection_report(pmids)
        return

    if args.existing:
        pmids = args.pmids or ([args.pmid] if args.pmid else EXISTING_PMIDS)
        os.makedirs(HYBRID_DIR, exist_ok=True)

        for pmid in pmids:
            nougat_path = os.path.join(NOUGAT_DIR, f"{pmid}.txt")
            pdf_path = os.path.join(PDF_DIR, f"{pmid}.pdf")
            out_path = os.path.join(HYBRID_DIR, f"{pmid}.txt")

            if not os.path.exists(nougat_path):
                print(f"  [{pmid}] SKIP: no Nougat output")
                continue
            if not os.path.exists(pdf_path):
                print(f"  [{pmid}] SKIP: no PDF")
                continue

            print(f"\n  [{pmid}] Processing (--existing)...")

            with open(nougat_path, "r", encoding="utf-8") as f:
                nougat_text = f.read()

            # Extract pymupdf4llm
            p4_pages = extract_pymupdf4llm(pdf_path)
            p4_full = "\n\n".join(
                p.get("text", "") for p in p4_pages
            )

            repaired, stats = process_paper_existing(nougat_text, p4_full)

            with open(out_path, "w", encoding="utf-8") as f:
                f.write(repaired)

            print(
                f"  [{pmid}] severity={stats['severity']} "
                f"action={stats.get('action', 'keep')} "
                f"→ {len(repaired)} chars saved"
            )

        print("\nDone.")
        return

    if args.full:
        pmids = args.pmids or ([args.pmid] if args.pmid else EXISTING_PMIDS)
        os.makedirs(HYBRID_DIR, exist_ok=True)

        processor, model = load_nougat()

        for pmid in pmids:
            pdf_path = os.path.join(PDF_DIR, f"{pmid}.pdf")
            out_path = os.path.join(HYBRID_DIR, f"{pmid}.txt")

            if not os.path.exists(pdf_path):
                print(f"  [{pmid}] SKIP: no PDF")
                continue

            print(f"\n  [{pmid}] Processing (--full)...")
            text, stats = process_paper_full(pdf_path, processor, model)

            with open(out_path, "w", encoding="utf-8") as f:
                f.write(text)

            print(
                f"  [{pmid}] saved ({len(text)} chars) | "
                f"Nougat={stats['nougat']} p4_full={stats['p4_full']} "
                f"p4_partial={stats['p4_partial']} p4_inline={stats['p4_inline']}"
            )

        print("\nDone.")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
