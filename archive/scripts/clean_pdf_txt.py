"""Post-process fitz PDF text output: remove noise, keep prose.

Usage::

    python scripts/clean_pdf_txt.py
"""

import os
import re

FITZ_DIR = "data/raw/papers_txt/fitz"
OUT_DIR = "data/raw/papers_txt/clean"

# --- Metadata / journal header patterns (remove these lines) ---
META_PATTERNS = [
    r"^THE JOURNAL OF BIOLOGICAL CHEMISTRY",
    r"^Vol\.\s*\d+",
    r"^VoL\.\s*\d+",
    r"^Printed in",
    r"^Copyright",
    r"^\d{4} by ",
    r"^Received for publication",
    r"^Published,\s+\w+",
    r"^DOI\s+\d+",
    r"^ISSN:",
    r"^To cite this article:",
    r"^To link to this article:",
    r"^Published online:",
    r"^Submit your article",
    r"^Article views:",
    r"^View related articles",
    r"^Citing articles:",
    r"^Full Terms & Conditions",
    r"^https://doi\.org/",
    r"^https://www\.tandfonline",
    r"^Molecular and Cellular Biology$",
    r"^\d{4}\s+Published by Elsevier",
    r"^Edited by ",
    r"^Available online \d+",
    r"^\(Received",
    r"^Accepted \d+",
]

# --- Section header detection (keep as markers) ---
SECTION_HEADERS = [
    r"^(Introduction|INTRODUCTION)\s*$",
    r"^(Background|BACKGROUND)\s*$",
    r"^(Materials?\s+and\s+Methods?|MATERIALS?\s+AND\s+METHODS?|Experimental\s+Procedures?)\s*$",
    r"^(Results?|RESULTS?)\s*$",
    r"^(Discussion|DISCUSSION)\s*$",
    r"^(Conclusions?|CONCLUSIONS?)\s*$",
    r"^Acknowledgm",
]

# --- Section to truncate at (remove everything after) ---
TRUNCATE_SECTIONS = [
    r"^(References|REFERENCES|REFERENCES\s*$)",
    r"^(Supplementary|Supplementary\s+Material)",
]


def is_meta_line(line):
    """Check if a line is journal metadata."""
    stripped = line.strip()
    if not stripped:
        return False
    for pat in META_PATTERNS:
        if re.search(pat, stripped, re.IGNORECASE):
            return True
    return False


def is_section_header(line):
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return False
    for pat in SECTION_HEADERS:
        if re.match(pat, stripped, re.IGNORECASE):
            return True
    return False


def is_truncate_section(line):
    stripped = line.strip()
    for pat in TRUNCATE_SECTIONS:
        if re.match(pat, stripped, re.IGNORECASE):
            return True
    return False


def is_figure_caption(line):
    """Detect figure/table caption start."""
    stripped = line.strip()
    return bool(re.match(r"^(FIG\.?|FIGURE|Fig\.?)\s*\d", stripped, re.IGNORECASE))


def is_table_header(line):
    stripped = line.strip()
    return bool(re.match(r"^(TABLE|Table)\s+[IVX\d]", stripped))


def clean_paper(text):
    """Clean a single paper's text."""
    lines = text.split("\n")
    cleaned = []
    skip_until_blank = False  # skip multi-line figure/table caption
    in_truncated_section = False

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Truncate at References
        if is_truncate_section(stripped):
            in_truncated_section = True

        if in_truncated_section:
            i += 1
            continue

        # Skip metadata lines
        if is_meta_line(stripped):
            i += 1
            continue

        # Skip figure captions (may span multiple lines)
        if is_figure_caption(stripped):
            skip_until_blank = True
            i += 1
            continue

        # Skip table headers (may span multiple lines until blank)
        if is_table_header(stripped):
            skip_until_blank = True
            i += 1
            continue

        # End skip block on blank line
        if skip_until_blank:
            if not stripped:
                skip_until_blank = False
                cleaned.append("")
            i += 1
            continue

        # Section headers: keep as markers
        if is_section_header(stripped):
            cleaned.append(f"\n[{stripped}]\n")
            i += 1
            continue

        # Skip very short lines that are likely page numbers or artifacts
        # (but keep if they look like continuation text)
        if stripped and len(stripped) <= 5 and not stripped.isalpha():
            i += 1
            continue

        cleaned.append(line)
        i += 1

    # Join and fix line breaks
    text = "\n".join(cleaned)

    # Merge hyphenated line breaks: "tran-\n scription" → "transcription"
    text = re.sub(r"(\w)-\n\s*(\w)", r"\1\2", text)

    # Merge broken lines: short line ending without period followed by lowercase
    lines = text.split("\n")
    merged = []
    for line in lines:
        stripped = line.strip()
        if not merged:
            merged.append(stripped)
            continue
        prev = merged[-1]
        # Merge if previous line doesn't end with sentence-ending punct
        # and current line starts with lowercase (continuation)
        if (
            prev
            and stripped
            and not prev.endswith((".", "!", "?", ":", ";"))
            and not prev.endswith("]")
            and stripped[0].islower()
            and len(prev) > 30
        ):
            merged[-1] = prev + " " + stripped
        else:
            merged.append(stripped)

    text = "\n".join(merged)

    # Collapse 3+ blank lines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Remove leading/trailing whitespace
    text = text.strip()

    # Remove author affiliation blocks at the very beginning
    # (lines before first substantial paragraph)
    paragraphs = text.split("\n\n")
    result = []
    found_content = False
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if not found_content:
            # Skip short initial blocks (title, authors, affiliation)
            if len(para) < 200 and not any(
                kw in para.lower()
                for kw in ["abstract", "transcription", "promoter", "gene", "protein", "cell"]
            ):
                continue
            found_content = True
        result.append(para)

    return "\n\n".join(result)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    files = sorted(f for f in os.listdir(FITZ_DIR) if f.endswith(".txt"))
    print(f"Cleaning {len(files)} files...")

    for fname in files:
        pmid = fname.replace(".txt", "")
        with open(os.path.join(FITZ_DIR, fname), "r", encoding="utf-8") as f:
            raw = f.read()

        cleaned = clean_paper(raw)

        out_path = os.path.join(OUT_DIR, fname)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(cleaned)

        raw_chars = len(raw)
        clean_chars = len(cleaned)
        reduction = (1 - clean_chars / raw_chars) * 100 if raw_chars else 0
        print(f"  {pmid}: {raw_chars} → {clean_chars} chars ({reduction:.0f}% removed)")

    print("Done")


if __name__ == "__main__":
    main()
