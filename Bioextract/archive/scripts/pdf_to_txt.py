"""Convert PDF papers to plain text.

Usage::

    python scripts/pdf_to_txt.py
"""

import os
import re

import fitz  # PyMuPDF

PDF_DIR = "data/raw/papers"
TXT_DIR = "data/raw/papers_txt"

# Simple section title patterns for heading detection
SECTION_PATTERNS = [
    r"^(Introduction|Background)\s*$",
    r"^(Materials?\s+and\s+Methods?|Experimental\s+Procedures?|Methods?)\s*$",
    r"^(Results?)\s*$",
    r"^(Discussion)\s*$",
    r"^(Conclusions?)\s*$",
    r"^(Supplementary|References|Acknowledgm|Funding|Author\s+Contributions|Data\s+Availability|Conflict\s+of\s+Interest)",
]


def is_section_header(line):
    """Check if a line looks like a section heading."""
    stripped = line.strip()
    if not stripped or len(stripped) > 100:
        return False
    for pat in SECTION_PATTERNS:
        if re.match(pat, stripped, re.IGNORECASE):
            return True
    return False


def pdf_to_text(pdf_path):
    """Extract text from PDF with basic section detection."""
    doc = fitz.open(pdf_path)
    sections = []
    current_section = "Untitled"
    current_text = []

    for page in doc:
        text = page.get_text("text")
        for line in text.split("\n"):
            stripped = line.strip()

            # Skip short noise lines (page numbers, headers)
            if not stripped:
                if current_text:
                    current_text.append("")
                continue

            if is_section_header(line):
                # Save previous section
                if current_text:
                    sections.append((current_section, "\n".join(current_text).strip()))
                current_section = stripped
                current_text = []
            else:
                current_text.append(stripped)

    # Save last section
    if current_text:
        sections.append((current_section, "\n".join(current_text).strip()))

    doc.close()
    return sections


def format_text(sections):
    """Format sections into readable text."""
    parts = []
    for title, text in sections:
        if text:
            parts.append(f"[{title}]\n{text}")
    return "\n\n".join(parts)


def main():
    os.makedirs(TXT_DIR, exist_ok=True)

    pdf_files = sorted(f for f in os.listdir(PDF_DIR) if f.endswith(".pdf"))
    print(f"Found {len(pdf_files)} PDFs")

    success = 0
    for pdf_file in pdf_files:
        pmid = pdf_file.replace(".pdf", "")
        txt_path = os.path.join(TXT_DIR, f"{pmid}.txt")

        # Skip if already converted
        if os.path.exists(txt_path):
            success += 1
            continue

        pdf_path = os.path.join(PDF_DIR, pdf_file)
        try:
            sections = pdf_to_text(pdf_path)
            text = format_text(sections)

            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text)

            n_sections = len(sections)
            n_chars = len(text)
            print(f"  {pmid}: OK ({n_sections} sections, {n_chars} chars)")
            success += 1
        except Exception as exc:
            print(f"  {pmid}: FAILED ({exc})")

    print(f"\nDone: {success}/{len(pdf_files)} converted")


if __name__ == "__main__":
    main()
