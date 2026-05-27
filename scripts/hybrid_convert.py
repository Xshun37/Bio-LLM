"""Hybrid PDF→text: Nougat per page, fallback to fitz on hallucination.

Three hallucination detectors:
  1. Line duplication: >20% of non-blank lines are exact duplicates
  2. Token repetition: any substring (len>=3) repeated >10 times in the page
  3. Sentence repetition: same 8-word n-gram appears 4+ times (after removing ref numbers)

Usage::
    conda run --no-capture-output -n bio_llm python scripts/hybrid_convert.py
"""

import os
import re
import time
from collections import Counter

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

import fitz
import torch
from PIL import Image
from transformers import TrOCRProcessor, VisionEncoderDecoderModel

PDF_DIR = "/home/bioxs/Bioproduce/Bio-LLM/data/raw/papers"
OUT_DIR = "/home/bioxs/Bioproduce/Bio-LLM/data/raw/papers_txt/hybrid"
DPI = 150

LINE_DUP_THRESHOLD = 0.20   # >20% duplicate lines → hallucination
TOKEN_REPEAT_THRESHOLD = 10  # any token repeated >10 times → hallucination


def load_model():
    print("Loading Nougat model...")
    t0 = time.time()
    processor = TrOCRProcessor.from_pretrained("facebook/nougat-base")
    model = VisionEncoderDecoderModel.from_pretrained("facebook/nougat-base")
    model.to("cuda")
    model.eval()
    print(f"Model loaded in {time.time()-t0:.1f}s")
    return processor, model


def nougat_page(processor, model, img):
    pixel_values = processor(img, return_tensors="pt").pixel_values.to("cuda")
    with torch.no_grad():
        outputs = model.generate(pixel_values, min_length=1, max_length=3584)
    return processor.batch_decode(outputs, skip_special_tokens=True)[0].strip()


def fitz_page(pdf_path, page_idx):
    doc = fitz.open(pdf_path)
    raw = doc[page_idx].get_text("text").strip()
    doc.close()

    # Merge hyphenated line breaks: "differen-\ntiate" → "differentiate"
    raw = re.sub(r"(\w)-\n\s*(\w)", r"\1\2", raw)

    # Merge PDF column-width line breaks into paragraphs
    lines = raw.split("\n")
    merged = []
    for line in lines:
        stripped = line.strip()
        if not merged:
            merged.append(stripped)
            continue
        prev = merged[-1]
        if not stripped:
            merged.append("")
            continue
        if not prev:
            merged.append(stripped)
            continue
        # Merge if prev doesn't end with sentence-ending punct and current starts lowercase
        if (
            not prev.endswith((".", "!", "?", ":", ";"))
            and not prev.endswith("]")
            and stripped[0].islower()
        ):
            merged[-1] = prev + " " + stripped
        else:
            merged.append(stripped)

    # Collapse 3+ blank lines to 2
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(merged))
    return text.strip()


def page_to_image(pdf_path, page_idx):
    doc = fitz.open(pdf_path)
    page = doc[page_idx]
    pix = page.get_pixmap(dpi=DPI)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()
    return img


def _find_repeated_tokens(text, min_len=3):
    """Find substrings that repeat excessively within the text.

    Returns list of (token, count) for tokens exceeding threshold.
    """
    # Extract repeating patterns: look for `_X_X_X` or `X X X X` or `X,X,X`
    hits = []

    # Pattern 1: word/markdown repeated with separators (space, comma, underscore)
    # e.g. "_i.e._, a _i.e._, a" or "_Hind_III_III_III"
    for m in re.finditer(r'((?:_?\w[\w.]*_?)(?:[,.\s]+_?\w[\w.]*_?){2,})', text):
        span = m.group(1)
        # Count how many times the first "token" appears in the span
        tokens = re.findall(r'_?\w[\w.]*_?', span)
        if not tokens:
            continue
        first = tokens[0]
        count = tokens.count(first)
        if count >= TOKEN_REPEAT_THRESHOLD:
            hits.append((first, count))

    # Pattern 2: any word (>=3 chars) appearing many times in a single line
    for line in text.split("\n"):
        words = re.findall(r'[A-Za-z][A-Za-z0-9_.]{2,}', line)
        if len(words) < TOKEN_REPEAT_THRESHOLD:
            continue
        counter = Counter(words)
        for word, count in counter.most_common(3):
            # Ignore common words
            if word.lower() in {"the", "and", "for", "with", "that", "this", "from", "are", "was", "but", "not", "can", "has", "have", "been", "were", "which", "their", "other", "into", "than"}:
                continue
            if count >= TOKEN_REPEAT_THRESHOLD and count / len(words) > 0.15:
                hits.append((word, count))

    return hits


def is_hallucination(text):
    """Detect hallucination via line duplication, token repetition, or sentence repetition."""
    # Check 1: line-level duplication
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) >= 5:
        dup_ratio = 1 - len(set(lines)) / len(lines)
        if dup_ratio > LINE_DUP_THRESHOLD:
            return True, "line_dup"

    # Check 2: token-level repetition
    repeats = _find_repeated_tokens(text)
    if repeats:
        return True, "token_rep"

    # Check 3: sentence-level repetition (e.g. "sentence (see, e.g., [1]). sentence (see, e.g., [2])")
    # Normalize: remove reference numbers like [1], [2], (1-3), (see, e.g., [1])
    normalized = re.sub(r'\(see[^)]*\)', '', text)
    normalized = re.sub(r'\[\d+(?:[-–,]\d+)*\]', '', normalized)
    normalized = re.sub(r'\(\d+(?:[-–,]\d+)*\)', '', normalized)
    # Split into sentences, check for repeated 8-grams
    words = normalized.split()
    if len(words) > 50:
        ngram_size = 8
        ngrams = [tuple(words[i:i+ngram_size]) for i in range(len(words) - ngram_size + 1)]
        ngram_counts = Counter(ngrams)
        for ngram, count in ngram_counts.most_common(5):
            if count >= 4:  # same 8-word sequence appears 4+ times
                return True, "sentence_rep"

    return False, None


def clean_nougat_page(text):
    text = re.split(r"\n#+\s*(References|REFERENCES|Bibliography)\s*\n", text, maxsplit=1, flags=re.DOTALL)[0]
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def process_paper(pdf_path, processor, model):
    pmid = os.path.basename(pdf_path).replace(".pdf", "")
    doc = fitz.open(pdf_path)
    n_pages = len(doc)
    doc.close()

    pages = []
    stats = {"nougat": 0, "fitz": 0}

    for i in range(n_pages):
        img = page_to_image(pdf_path, i)
        t0 = time.time()
        nougat_text = nougat_page(processor, model, img)
        elapsed = time.time() - t0

        hallucinated, reason = is_hallucination(nougat_text)

        if hallucinated:
            fitz_text = fitz_page(pdf_path, i)
            pages.append(fitz_text)
            stats["fitz"] += 1
            print(f"    page {i+1}/{n_pages}: HALLUCINATION ({reason}) → fitz ({len(fitz_text)} chars) [{elapsed:.1f}s]")
        else:
            pages.append(clean_nougat_page(nougat_text))
            stats["nougat"] += 1
            print(f"    page {i+1}/{n_pages}: Nougat OK ({len(nougat_text)} chars) [{elapsed:.1f}s]")

    return "\n\n".join(pages), stats


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    processor, model = load_model()

    pmids = ["10082553", "10453008", "10978529", "11013233",
             "11706010", "11854297", "14642566", "15178343"]

    total = {"nougat": 0, "fitz": 0}
    t_total = time.time()

    for pmid in pmids:
        pdf_path = os.path.join(PDF_DIR, f"{pmid}.pdf")
        out_path = os.path.join(OUT_DIR, f"{pmid}.txt")

        print(f"\n  {pmid}...")
        text, stats = process_paper(pdf_path, processor, model)

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)

        total["nougat"] += stats["nougat"]
        total["fitz"] += stats["fitz"]
        print(f"  {pmid}: saved ({len(text)} chars) | Nougat={stats['nougat']} fitz={stats['fitz']}")

    elapsed = time.time() - t_total
    print(f"\nDone: 8 papers in {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"Total: Nougat={total['nougat']} fitz_fallback={total['fitz']}")


if __name__ == "__main__":
    main()
