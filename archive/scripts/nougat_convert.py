"""Convert PDF papers to Markdown text using Nougat (Meta/Facebook Research).

Usage::

    conda run --no-capture-output -n bio_llm python scripts/nougat_convert.py
    conda run --no-capture-output -n bio_llm python scripts/nougat_convert.py --pmid 11706010
"""

import argparse
import os
import re
import time

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

import fitz  # PyMuPDF
import torch
from PIL import Image
from transformers import TrOCRProcessor, VisionEncoderDecoderModel

PDF_DIR = "/home/bioxs/Bioproduce/Bio-LLM/data/raw/papers"
TXT_DIR = "/home/bioxs/Bioproduce/Bio-LLM/data/raw/papers_txt"
DPI = 150


def load_model():
    """Load Nougat model and processor."""
    print("Loading Nougat model...")
    t0 = time.time()
    processor = TrOCRProcessor.from_pretrained("facebook/nougat-base")
    model = VisionEncoderDecoderModel.from_pretrained("facebook/nougat-base")
    model.to("cuda")
    model.eval()
    print(f"Model loaded in {time.time()-t0:.1f}s")
    return processor, model


def page_to_image(pdf_path, page_idx):
    """Render a PDF page to PIL Image."""
    doc = fitz.open(pdf_path)
    page = doc[page_idx]
    pix = page.get_pixmap(dpi=DPI)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()
    return img


def process_page(processor, model, img):
    """Run Nougat inference on a single page image."""
    pixel_values = processor(img, return_tensors="pt").pixel_values.to("cuda")
    with torch.no_grad():
        outputs = model.generate(pixel_values, min_length=1, max_length=3584)
    text = processor.batch_decode(outputs, skip_special_tokens=True)[0]
    return text.strip()


def postprocess(full_text):
    """Light cleaning: remove references section and excess blank lines."""
    # Remove references section (common patterns)
    ref_patterns = [
        r"\n#+\s*(References|REFERENCES|Bibliography)\s*\n.*",
        r"\n(References|REFERENCES|Bibliography)\s*\n.*",
    ]
    for pat in ref_patterns:
        full_text = re.split(pat, full_text, maxsplit=1, flags=re.DOTALL)[0]

    # Collapse 3+ blank lines into 2
    full_text = re.sub(r"\n{3,}", "\n\n", full_text)
    return full_text.strip()


def convert_pdf(pdf_path, processor, model):
    """Convert all pages of a PDF to Markdown text."""
    doc = fitz.open(pdf_path)
    n_pages = len(doc)
    doc.close()

    pages = []
    for i in range(n_pages):
        img = page_to_image(pdf_path, i)
        t0 = time.time()
        text = process_page(processor, model, img)
        elapsed = time.time() - t0
        pages.append(text)
        print(f"    page {i+1}/{n_pages}: {len(text)} chars, {elapsed:.1f}s")

    full_text = "\n\n".join(pages)
    return postprocess(full_text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pmid", type=str, default=None, help="Process single PMID")
    args = parser.parse_args()

    os.makedirs(TXT_DIR, exist_ok=True)
    processor, model = load_model()

    if args.pmid:
        pdf_files = [f"{args.pmid}.pdf"]
    else:
        pdf_files = sorted(f for f in os.listdir(PDF_DIR) if f.endswith(".pdf"))

    print(f"Processing {len(pdf_files)} PDFs...")
    t_total = time.time()
    success = 0

    for pdf_file in pdf_files:
        pmid = pdf_file.replace(".pdf", "")
        txt_path = os.path.join(TXT_DIR, f"{pmid}.txt")

        # Skip if already converted (unless single PMID mode)
        if not args.pmid and os.path.exists(txt_path):
            success += 1
            continue

        pdf_path = os.path.join(PDF_DIR, pdf_file)
        try:
            print(f"  {pmid}...")
            text = convert_pdf(pdf_path, processor, model)

            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text)

            print(f"  {pmid}: OK ({len(text)} chars)")
            success += 1
        except Exception as exc:
            print(f"  {pmid}: FAILED ({exc})")

    elapsed = time.time() - t_total
    print(f"\nDone: {success}/{len(pdf_files)} converted in {elapsed:.0f}s ({elapsed/60:.1f}min)")


if __name__ == "__main__":
    main()
