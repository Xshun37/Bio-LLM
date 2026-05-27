"""Hybrid post-process: detect hallucination in existing Nougat output, replace with fitz.

Usage::
    python scripts/hybrid_merge.py
"""

import os
import re

NOUGAT_DIR = "data/raw/papers_txt"       # Nougat 输出（根目录）
FITZ_DIR = "data/raw/papers_txt/fitz"    # fitz 原始输出
OUT_DIR = "data/raw/papers_txt/hybrid"   # 合并输出

HALLUCINATION_THRESHOLD = 0.20


def duplicate_ratio(text):
    """Ratio of duplicate lines among non-blank lines."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) < 10:
        return 0.0
    return 1 - len(set(lines)) / len(lines)


def clean_nougat(text):
    """Remove references and collapse blank lines."""
    text = re.split(r"\n#+\s*(References|REFERENCES|Bibliography)\s*\n", text, maxsplit=1, flags=re.DOTALL)[0]
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_fitz(text):
    """Basic fitz cleanup: remove references, merge hyphenated breaks."""
    # Truncate at References
    for pat in [r"\n(References|REFERENCES|Bibliography)\s*\n"]:
        text = re.split(pat, text, maxsplit=1, flags=re.IGNORECASE)[0]

    # Merge hyphenated line breaks
    text = re.sub(r"(\w)-\n\s*(\w)", r"\1\2", text)

    # Collapse blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    pmids = ["10082553", "10453008", "10978529", "11013233",
             "11706010", "11854297", "14642566", "15178343"]

    results = []
    for pmid in pmids:
        nougat_path = os.path.join(NOUGAT_DIR, f"{pmid}.txt")
        fitz_path = os.path.join(FITZ_DIR, f"{pmid}.txt")
        out_path = os.path.join(OUT_DIR, f"{pmid}.txt")

        with open(nougat_path, "r", encoding="utf-8") as f:
            nougat_text = f.read()
        with open(fitz_path, "r", encoding="utf-8") as f:
            fitz_text = f.read()

        dup = duplicate_ratio(nougat_text)

        if dup > HALLUCINATION_THRESHOLD:
            # Hallucination detected → use fitz
            output = clean_fitz(fitz_text)
            source = "fitz"
        else:
            # Nougat OK → keep Nougat
            output = clean_nougat(nougat_text)
            source = "Nougat"

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output)

        results.append((pmid, source, dup, len(output)))
        print(f"  {pmid}: {source:>6s} (dup={dup:.0%}, {len(output)} chars)")

    print(f"\nDone: {sum(1 for _,s,_,_ in results if s=='Nougat')} Nougat, "
          f"{sum(1 for _,s,_,_ in results if s=='fitz')} fitz fallback")


if __name__ == "__main__":
    main()
