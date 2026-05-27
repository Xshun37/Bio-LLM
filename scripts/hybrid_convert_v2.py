"""Hybrid PDF→text: Nougat OCR + fitz fallback with paragraph-level hallucination repair.

Strategy:
  - Nougat (Meta OCR) as primary: correct Greek letters, auto Markdown structure
  - fitz (PyMuPDF) as fallback: replace hallucinated Nougat segments with stable text
  - Nougat provides Markdown formatting; fitz only fills in content where Nougat fails

Two modes:
  --existing   Post-process saved Nougat text (no GPU, fast iteration)
  --full       Full pipeline: Nougat inference + fitz extraction + repair

Usage::
    python scripts/hybrid_convert_v2.py --existing --pmid 10453008
    python scripts/hybrid_convert_v2.py --full --pmids 15184388 15195143
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
FITZ_DIR = os.path.join(BASE, "data/raw/papers_txt/fitz")
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
    # Additional fitz-specific noise
    r"^\d+\s*$",                          # standalone page numbers
    r"^[A-Z]\.\s+\w+\s+et\s+al\.\s*/",   # author line "A. Perez et al. / ..."
    # Footnote / affiliation blocks (common in PDF page footers)
    r"^Received\s+\d+\s+\w+\s+\d{4}",
    r"^(?:Accepted|Published)\s+\d+\s+\w+",
    r"^(?:Current|Present)\s+address:",
    r"^(?:Corresponding\s+author|E-?mail|Address\s+correspondence)",
    r"^Department\s+of\s+",
    r"(?:This\s+work\s+was\s+supported|This\s+article\s+was\s+published)",
    r"(?:contributed\s+equally|To\s+whom\s+correspondence)",
    r"^(?:Abbreviations?|ABBR)[\s\w]*used",
    r"(?:These\s+authors\s+contributed)",
    r"(?:should\s+therefore\s+both|should\s+be\s+considered)",
    r"^The\s+costs?\s+of\s+publication",
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
    # Pattern 1: backreference — any token (2+ chars) repeated 10+ times
    m = re.search(r"(\w{2,})(?:\W*\1){9,}", text)
    if m:
        return True, {
            "token": m.group(1),
            "count": text.count(m.group(1)),
            "start": m.start(),
            "end": m.end(),
        }

    # Pattern 2: word/markdown repeated with separators
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
    """Detect cross-paragraph duplication: same text appearing in many paragraphs."""
    if len(paragraphs) < 10:
        return set()

    para_counter = Counter()
    para_groups = {}
    for i, p in enumerate(paragraphs):
        key = p.strip().lower()
        if len(key) < 5:
            continue
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
        (is_hallucinated, reason, boundary)
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
        severity: "none" | "moderate" | "severe"
        details:  dict with per-paragraph results
    """
    paras = split_paragraphs(nougat_text)
    if not paras:
        return "none", {}

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

    # Cross-paragraph duplication
    cross_dup_indices = _detect_cross_para_dup(paras)
    for idx in cross_dup_indices:
        if not results[idx]["is_hallucinated"]:
            results[idx]["is_hallucinated"] = True
            results[idx]["reason"] = "cross_para_dup"
            bad_indices.append(idx)
    bad_indices = sorted(set(bad_indices))

    n_bad = len(bad_indices)
    n_total = len(paras)

    has_line_dup = any(
        r["reason"] in ("line_dup", "cross_para_dup")
        for r in results if r["is_hallucinated"]
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
#  3. FITZ EXTRACTION & CLEANING
# ═══════════════════════════════════════════════════════════

def _is_footnote_block(text):
    """Check if a fitz text block is a page footnote / metadata (not main body)."""
    first_line = text.strip().split('\n')[0].strip()
    # Strip leading footnote number: "1 This work..." → "This work..."
    first_stripped = re.sub(r'^\d+\s+', '', first_line)
    for pat in META_PATTERNS:
        if re.search(pat, first_line, re.IGNORECASE):
            return True
        if re.search(pat, first_stripped, re.IGNORECASE):
            return True
    # Very short non-content blocks (affiliations, author names)
    if len(text.strip()) < 70 and not re.search(r'[.!?]\s*$', text.strip()):
        return True
    return False


def extract_fitz_pages(pdf_path):
    """Extract per-page text via fitz (PyMuPDF) blocks mode.

    Uses "blocks" mode so each text block (typically one paragraph) is
    separated by double-newlines.  Footnote/metadata blocks (page footers,
    affiliations, grant info) are filtered out so that paragraph indices
    align with Nougat's body-text paragraphs.

    Returns:
        list of strings, one per page (paragraphs separated by \\n\\n)
    """
    import fitz
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        blocks = page.get_text("blocks")
        # Sort by vertical position (top-to-bottom), then horizontal (left-to-right)
        blocks.sort(key=lambda b: (round(b[1] / 20) * 20, b[0]))
        page_paras = []
        for block in blocks:
            if block[6] == 0:  # text block (type=0), skip image blocks (type=1)
                text = block[4].strip()
                if text and not _is_footnote_block(text):
                    page_paras.append(text)
        pages.append("\n\n".join(page_paras))
    doc.close()
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


def clean_fitz_text(text):
    """Clean fitz output: remove noise, merge lines, truncate references."""
    lines = text.split("\n")
    cleaned = []
    in_refs = False

    for line in lines:
        stripped = line.strip()

        # Truncate at References section
        if re.match(
            r"^(References|REFERENCES|Bibliography)\s*$", stripped, re.IGNORECASE
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

        # Skip very short non-alpha lines (likely artifacts)
        if stripped and len(stripped) <= 5 and not stripped.isalpha():
            continue

        cleaned.append(line)

    text = "\n".join(cleaned)

    # Merge hyphenated line breaks: "tran-\n scription" → "transcription"
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
    text = re.sub(r"#{1,6}\s*", "", text)
    text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
    text = re.sub(r"\\?\(.*?\\?\)", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.lower().strip()


def _is_fitz_header(para):
    """Check if a fitz paragraph looks like a section header."""
    stripped = para.strip()
    if not stripped or len(stripped) > 120:
        return False
    # Common section headers in biology papers
    return bool(re.match(
        r"^(Introduction|INTRODUCTION|Background|BACKGROUND|"
        r"Materials?\s+and\s+Methods?|MATERIALS?\s+AND\s+METHODS?|"
        r"Experimental\s+Procedures?|EXPERIMENTAL\s+PROCEDURES?|"
        r"Results?|RESULTS?|Discussion|DISCUSSION|"
        r"Conclusions?|CONCLUSIONS?|Acknowledgm|ACKNOWLEDGM|"
        r"References|REFERENCES|Abbreviations|ABBR)",
        stripped, re.IGNORECASE,
    ))


def align_paragraphs(nougat_paras, fitz_paras, bad_indices=None):
    """Align Nougat paragraphs to fitz paragraphs via global content matching.

    Strategy (no header dependency — many old PDFs have unrecognizable fonts):
      1. Match good (non-hallucinated) Nougat paragraphs globally against fitz
      2. Use good matches as position anchors
      3. For bad paragraphs, find the gap between neighbouring anchors in fitz
         and assign unassigned fitz paragraphs from the gap (gap-filling)

    Args:
        nougat_paras: list of Nougat paragraphs (Markdown)
        fitz_paras: list of fitz paragraphs (plain text)
        bad_indices: set/list of hallucination paragraph indices

    Returns:
        mapping: dict {nougat_idx: fitz_idx}
    """
    mapping = {}
    if not nougat_paras or not fitz_paras:
        return mapping

    bad_set = set(bad_indices) if bad_indices else set()
    n_len = len(nougat_paras)
    f_len = len(fitz_paras)
    used_f = set()

    # ── Pass 1: match good paragraphs globally ──
    good_matches = []  # (nougat_idx, fitz_idx, score)
    for ni in range(n_len):
        if ni in bad_set:
            continue
        n_norm = _normalize_for_match(nougat_paras[ni][:500])
        if len(n_norm) < 15:
            continue

        best_score = 0
        best_fi = None
        for fi in range(f_len):
            f_norm = _normalize_for_match(fitz_paras[fi][:500])
            if len(f_norm) < 15:
                continue
            score = SequenceMatcher(None, n_norm, f_norm).ratio()
            if score > best_score:
                best_score = score
                best_fi = fi

        if best_score > 0.40 and best_fi is not None:
            good_matches.append((ni, best_fi, best_score))

    # Sort by score descending, greedily assign (best match wins)
    good_matches.sort(key=lambda x: -x[2])
    good_mapping = {}
    for ni, fi, score in good_matches:
        if ni in good_mapping or fi in used_f:
            continue
        good_mapping[ni] = fi
        used_f.add(fi)

    # ── Pass 2: build ordered anchor list from good matches ──
    anchors = sorted(good_mapping.items())  # [(n_idx, f_idx), ...]

    # ── Pass 3: gap-filling for bad paragraphs ──
    for ni in sorted(bad_set):
        # Find bounding anchors (re-sorted each time to include prior bad mappings)
        all_anchors = sorted(
            [(k, v) for k, v in mapping.items()] + anchors
        )
        prev_anchor = None
        next_anchor = None
        for a_ni, a_fi in all_anchors:
            if a_ni < ni:
                prev_anchor = (a_ni, a_fi)
            elif a_ni > ni and next_anchor is None:
                next_anchor = (a_ni, a_fi)

        # Determine the fitz gap
        gap_lo = prev_anchor[1] + 1 if prev_anchor else 0
        gap_hi = next_anchor[1] if next_anchor else f_len

        # Collect unassigned fitz paragraphs in the gap
        gap_fis = [fi for fi in range(gap_lo, gap_hi) if fi not in used_f]

        if gap_fis:
            # Pick from gap: if only one, take it; otherwise use content matching
            if len(gap_fis) == 1:
                mapping[ni] = gap_fis[0]
            else:
                # Try content match within gap
                n_norm = _normalize_for_match(nougat_paras[ni][:500])
                best_score = 0
                best_fi = None
                for fi in gap_fis:
                    f_norm = _normalize_for_match(fitz_paras[fi][:500])
                    if len(f_norm) < 10:
                        continue
                    score = SequenceMatcher(None, n_norm, f_norm).ratio()
                    if score > best_score:
                        best_score = score
                        best_fi = fi
                # Content match or position-based pick (first in gap)
                mapping[ni] = best_fi if best_fi is not None else gap_fis[0]
        else:
            # No gap — try wider search window (±5 around interpolation)
            if prev_anchor and next_anchor:
                pn, pf = prev_anchor
                nn, nf = next_anchor
                ratio = (ni - pn) / max(1, nn - pn)
                est_fi = pf + ratio * (nf - pf)
            elif prev_anchor:
                est_fi = prev_anchor[1] + (ni - prev_anchor[0])
            elif next_anchor:
                est_fi = next_anchor[1] - (next_anchor[0] - ni)
            else:
                est_fi = 0

            search_lo = max(0, int(est_fi) - 5)
            search_hi = min(f_len, int(est_fi) + 6)
            best_score = 0
            best_fi = None
            for fi in range(search_lo, search_hi):
                if fi in used_f:
                    continue
                f_norm = _normalize_for_match(fitz_paras[fi][:500])
                if len(f_norm) < 10:
                    continue
                n_norm = _normalize_for_match(nougat_paras[ni][:500])
                score = SequenceMatcher(None, n_norm, f_norm).ratio()
                if score > best_score:
                    best_score = score
                    best_fi = fi
            if best_fi is not None:
                mapping[ni] = best_fi

        # Mark assigned fitz paragraph as used so next bad para won't reuse it
        if ni in mapping:
            used_f.add(mapping[ni])

    # Merge good_mapping into result
    mapping.update(good_mapping)
    return mapping


# ═══════════════════════════════════════════════════════════
#  5. REPAIR STRATEGIES
# ═══════════════════════════════════════════════════════════

def _strip_repeated_tokens(text, boundary):
    """Remove repeated token cluster, keeping the first occurrence.

    The detector returns a boundary spanning the *entire* repeated cluster
    (first valid occurrence + all duplicates).  This collapses the cluster
    down to a single copy of the repeated token.
    """
    if not boundary or "start" not in boundary:
        return text

    start = boundary["start"]
    end = boundary["end"]
    matched = text[start:end]

    if len(matched) == 0:
        return text

    token = boundary.get("token")
    if not token:
        # char_rep: keep one copy of the char
        ch = boundary.get("char", matched[0])
        collapsed = ch
    else:
        # token_rep: collapse "CTGCTGCTG..." → "CTG"
        collapsed = token

    before = text[:start]
    after = text[end:]
    result = before + collapsed + after

    result = re.sub(r"  +", " ", result)
    return result.strip()


def repair_page(nougat_text, fitz_text, severity, details):
    """Apply repair strategy based on severity.

    Returns:
        repaired_text, repair_info
    """
    paras = split_paragraphs(nougat_text)
    fitz_paras = split_paragraphs(fitz_text)
    bad_indices = details.get("bad_indices", [])
    results = details.get("paragraphs", [])

    if severity == "none":
        return nougat_text, {"action": "keep", "n_replaced": 0}

    if severity == "severe":
        # Replace entire page with fitz text
        return fitz_text, {
            "action": "page_replace",
            "n_replaced": len(paras),
        }

    # moderate: replace only bad paragraphs
    alignment = align_paragraphs(paras, fitz_paras, bad_indices=bad_indices)
    repaired_paras = []
    n_replaced = 0
    n_inline = 0

    for i, para in enumerate(paras):
        if i not in bad_indices:
            repaired_paras.append(para)
            continue

        result = results[i]

        # Priority 1: replace with fitz paragraph (most reliable — fitz always
        # extracts the correct text from PDF; inline strip can preserve wrong
        # content when Nougat hallucinated entirely different text)
        fitz_idx = alignment.get(i)
        if fitz_idx is not None and fitz_idx < len(fitz_paras):
            replacement = fitz_paras[fitz_idx]
            if len(replacement.strip()) > 10:
                repaired_paras.append(replacement)
                n_replaced += 1
                continue

        # Priority 2: inline strip for token_rep / char_rep (fallback)
        if result["reason"] in ("token_rep", "char_rep") and result.get("boundary"):
            stripped = _strip_repeated_tokens(para, result["boundary"])
            # Use inline if result has meaningful content
            if len(stripped) > 50:
                repaired_paras.append(stripped)
                n_inline += 1
                continue

        # Priority 3: keep original
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
    text = re.split(
        r"\n(?:#+\s*)?\*{0,2}\s*(?:References|REFERENCES|Bibliography)\s*\*{0,2}\s*\n",
        text,
        maxsplit=1,
        flags=re.DOTALL | re.IGNORECASE,
    )[0]
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def process_paper_existing(nougat_text, fitz_text):
    """Process a paper in --existing mode (no GPU).

    Args:
        nougat_text: saved Nougat output (joined, no page boundaries)
        fitz_text: fitz output (joined, cleaned)

    Returns:
        (repaired_text, stats)
    """
    severity, details = assess_page(nougat_text)

    if severity == "none":
        cleaned = clean_nougat_page(nougat_text)
        return cleaned, {"severity": "none", "action": "keep"}

    repaired, repair_info = repair_page(nougat_text, fitz_text, severity, details)
    repaired = clean_nougat_page(repaired)

    return repaired, {
        "severity": severity,
        **repair_info,
        "n_total_paras": details.get("n_total", 0),
        "n_bad_paras": details.get("n_bad", 0),
    }


def process_paper_full(pdf_path, processor, model):
    """Process a paper in --full mode (Nougat + fitz, page by page).

    Returns:
        (final_markdown, stats)
    """
    pmid = os.path.basename(pdf_path).replace(".pdf", "")
    import fitz as _fitz
    with _fitz.open(pdf_path) as doc:
        n_pages = len(doc)

    # Extract fitz text per page (blocks mode for proper paragraph structure)
    fitz_pages = extract_fitz_pages(pdf_path)

    pages_out = []
    nougat_raw_pages = []
    stats = {"nougat": 0, "fitz_full": 0, "fitz_partial": 0, "fitz_inline": 0}

    for i in range(n_pages):
        # Nougat inference
        img = page_to_image(pdf_path, i)
        t0 = time.time()
        nougat_text = nougat_page(processor, model, img)
        elapsed = time.time() - t0
        nougat_raw_pages.append(nougat_text)

        # fitz text for this page
        fitz_text = clean_fitz_text(fitz_pages[i]) if i < len(fitz_pages) else ""

        # Assess
        severity, details = assess_page(nougat_text)

        if severity == "none":
            pages_out.append(clean_nougat_page(nougat_text))
            stats["nougat"] += 1
            tag = "Nougat OK"
        else:
            repaired, repair_info = repair_page(
                nougat_text, fitz_text, severity, details
            )
            pages_out.append(clean_nougat_page(repaired))
            action = repair_info["action"]
            if action == "page_replace":
                stats["fitz_full"] += 1
                tag = "SEVERE→fitz"
            elif action == "paragraph_replace":
                stats["fitz_partial"] += 1
                tag = f"MOD→replace {repair_info['n_replaced']}para"
            else:
                stats["fitz_inline"] += 1
                tag = "MINOR→inline"

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
    parser = argparse.ArgumentParser(description="Hybrid PDF→text: Nougat + fitz")
    parser.add_argument("--existing", action="store_true",
                        help="Post-process saved Nougat text (no GPU)")
    parser.add_argument("--full", action="store_true",
                        help="Full pipeline: Nougat + fitz")
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
            out_path = os.path.join(HYBRID_DIR, f"{pmid}.txt")

            if not os.path.exists(nougat_path):
                print(f"  [{pmid}] SKIP: no Nougat output")
                continue

            # Always extract fitz blocks from PDF for proper paragraph structure
            # (saved FITZ_DIR files use plain text mode without paragraph breaks)
            pdf_path = os.path.join(PDF_DIR, f"{pmid}.pdf")
            if os.path.exists(pdf_path):
                fitz_pages = extract_fitz_pages(pdf_path)
                fitz_text = "\n\n".join(fitz_pages)
            else:
                # Fallback: try saved fitz text (old format, less accurate)
                fitz_path = os.path.join(FITZ_DIR, f"{pmid}.txt")
                if os.path.exists(fitz_path):
                    with open(fitz_path, "r", encoding="utf-8") as f:
                        fitz_text = f.read()
                else:
                    print(f"  [{pmid}] SKIP: no PDF or fitz text")
                    continue

            print(f"\n  [{pmid}] Processing (--existing)...")

            with open(nougat_path, "r", encoding="utf-8") as f:
                nougat_text = f.read()

            fitz_cleaned = clean_fitz_text(fitz_text)
            repaired, stats = process_paper_existing(nougat_text, fitz_cleaned)

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
                f"Nougat={stats['nougat']} fitz_full={stats['fitz_full']} "
                f"fitz_partial={stats['fitz_partial']} fitz_inline={stats['fitz_inline']}"
            )

        print("\nDone.")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
