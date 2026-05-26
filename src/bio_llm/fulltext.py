"""PMC full-text fetching and XML parsing.

Retrieves full-text articles from Europe PMC (PMC Open Access subset)
given a list of PMIDs. Saves raw XML and extracted structured text locally.

Usage::

    # Single article test
    python -m bio_llm.fulltext --pmid 20052289

    # Batch from gold standard
    python -m bio_llm.fulltext --input data/raw/finalresult.tsv
"""

import argparse
import os
import time
import xml.etree.ElementTree as ET

import requests
from Bio import Entrez

from bio_llm.abstracts import (
    bypass_proxy_for_ncbi,
    clean_pmids,
    parse_bool_env,
    DEFAULT_NCBI_NO_PROXY_HOSTS,
)

# Sections to skip — not useful for TF-target extraction
SKIP_SECTIONS = {
    "references", "acknowledgments", "acknowledgements",
    "supplementary material", "supporting information",
    "author contributions", "funding", "data availability",
    "competings interests", "competing interests",
    "conflict of interest", "disclosure",
}

EUROPE_PMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
NCBI_EFETCH_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


# ---------------------------------------------------------------------------
# PMID → PMCID mapping
# ---------------------------------------------------------------------------

def pmid_to_pmcid(pmid_list, email, bypass_proxy=False,
                  no_proxy_hosts=DEFAULT_NCBI_NO_PROXY_HOSTS):
    """Map PMIDs to PMCIDs using Entrez.elink.

    Returns dict[str, str]: pmid → pmcid (with PMC prefix, e.g. "PMC2797294").
    PMIDs without PMC full-text are omitted from the result.
    """
    Entrez.email = email
    pmid_list = [str(p) for p in pmid_list]
    if not pmid_list:
        return {}

    print(f"查询 {len(pmid_list)} 个 PMID 的 PMC 全文可用性...")
    mapping = {}
    try:
        with bypass_proxy_for_ncbi(enabled=bypass_proxy, no_proxy_hosts=no_proxy_hosts):
            handle = Entrez.elink(
                dbfrom="pubmed", db="pmc",
                id=pmid_list, retmode="xml",
            )
            results = Entrez.read(handle)
            handle.close()

        for doc in results:
            pmid = doc["IdList"][0]
            if doc.get("LinkSetDb"):
                for linkdb in doc["LinkSetDb"]:
                    if linkdb["LinkName"] == "pubmed_pmc":
                        pmc_id_num = linkdb["Link"][0]["Id"]
                        mapping[pmid] = f"PMC{pmc_id_num}"
                        break
    except Exception as exc:
        print(f"Entrez.elink 查询失败: {exc}")

    found = len(mapping)
    missing = len(pmid_list) - found
    print(f"  PMC 全文可用: {found}/{len(pmid_list)}")
    if missing:
        print(f"  无全文: {', '.join(p for p in pmid_list if p not in mapping)}")
    return mapping


# ---------------------------------------------------------------------------
# PMC XML fetching
# ---------------------------------------------------------------------------

def fetch_pmc_xml(pmcid, session=None):
    """Fetch full-text XML from Europe PMC REST API.

    Args:
        pmcid: PMC ID string, e.g. "PMC2797294"
        session: optional requests.Session for connection reuse

    Returns:
        XML string, or None on failure.
    """
    # Try Europe PMC first (better structured XML)
    url = f"{EUROPE_PMC_BASE}/{pmcid}/fullTextXML"
    requester = session or requests
    try:
        resp = requester.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException:
        pass  # Fall through to NCBI

    # Fallback: NCBI efetch
    pmcid_num = pmcid.replace("PMC", "")
    ncbi_url = f"{NCBI_EFETCH_BASE}?db=pmc&id={pmcid_num}&retmode=xml"
    try:
        resp = requester.get(ncbi_url, timeout=30)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        print(f"  获取 {pmcid} XML 失败: {exc}")
        return None


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------

def _get_text(element):
    """Recursively extract all text from an XML element."""
    return "".join(element.itertext()).strip()


def _should_skip_section(title):
    """Check if a section title indicates we should skip it."""
    if not title:
        return False
    title_lower = title.lower().strip()
    return any(skip in title_lower for skip in SKIP_SECTIONS)


def parse_pmc_xml(xml_text):
    """Parse PMC XML into structured sections.

    Returns dict with keys:
        title: article title
        authors: list of author name strings
        journal: journal name
        sections: list of (title, text) tuples
        full_text: formatted string with [Section] headers
    """
    root = ET.fromstring(xml_text)

    # --- Metadata ---
    title_el = root.find(".//article-title")
    title = _get_text(title_el) if title_el is not None else ""

    authors = []
    for author in root.findall(".//contrib[@contrib-type='author']"):
        surname_el = author.find("name/surname")
        given_el = author.find("name/given-names")
        if surname_el is not None:
            name = _get_text(surname_el)
            if given_el is not None:
                name = f"{_get_text(given_el)} {name}"
            authors.append(name)

    journal_el = root.find(".//journal-title")
    journal = _get_text(journal_el) if journal_el is not None else ""

    # --- Body sections ---
    body = root.find(".//body")
    if body is None:
        # Fallback: try front matter abstract
        abstract_el = root.find(".//abstract")
        if abstract_el is not None:
            text = _get_text(abstract_el)
            return {
                "title": title, "authors": authors, "journal": journal,
                "sections": [("Abstract", text)],
                "full_text": f"[Abstract]\n{text}",
            }
        return {
            "title": title, "authors": authors, "journal": journal,
            "sections": [], "full_text": "",
        }

    sections = []
    _extract_sections(body, sections, prefix="")

    # Build formatted text
    parts = []
    for sec_title, sec_text in sections:
        parts.append(f"[{sec_title}]\n{sec_text}")
    full_text = "\n\n".join(parts)

    return {
        "title": title,
        "authors": authors,
        "journal": journal,
        "sections": sections,
        "full_text": full_text,
    }


def _extract_sections(parent, sections, prefix=""):
    """Recursively extract sections from XML element tree."""
    for sec in parent.findall("sec"):
        title_el = sec.find("title")
        title = _get_text(title_el) if title_el is not None else "Untitled"

        if _should_skip_section(title):
            continue

        full_title = f"{prefix}{title}" if not prefix else f"{prefix} > {title}"

        # Collect direct paragraph text (not from nested secs)
        paragraphs = []
        for p in sec.findall("p"):
            text = _get_text(p)
            if text:
                paragraphs.append(text)

        # Check for nested sections
        nested_secs = sec.findall("sec")
        if nested_secs:
            # If this section has its own text, add it first
            if paragraphs:
                sections.append((title, "\n\n".join(paragraphs)))
            # Then recurse into nested sections
            _extract_sections(sec, sections, prefix=title)
        elif paragraphs:
            sections.append((title, "\n\n".join(paragraphs)))


# ---------------------------------------------------------------------------
# Text formatting
# ---------------------------------------------------------------------------

def format_fulltext(parsed):
    """Format parsed article as human-readable text with metadata header."""
    lines = []
    if parsed.get("title"):
        lines.append(f"Title: {parsed['title']}")
    if parsed.get("authors"):
        lines.append(f"Authors: {', '.join(parsed['authors'])}")
    if parsed.get("journal"):
        lines.append(f"Journal: {parsed['journal']}")
    if lines:
        lines.append("")  # blank separator

    lines.append(parsed.get("full_text", ""))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Batch orchestration
# ---------------------------------------------------------------------------

def fetch_fulltexts(pmids, email, output_dir,
                    bypass_proxy=False,
                    no_proxy_hosts=DEFAULT_NCBI_NO_PROXY_HOSTS):
    """Fetch full texts for a list of PMIDs, caching to disk.

    For each PMID:
      1. Check if {output_dir}/{pmid}.txt already exists (cache hit)
      2. If not, resolve PMID → PMCID via Entrez
      3. Fetch XML from Europe PMC
      4. Parse XML → structured text
      5. Save {pmid}.xml (raw) and {pmid}.txt (extracted)

    Args:
        pmids: list of PMID strings
        email: NCBI Entrez email
        output_dir: directory to save XML and TXT files
        bypass_proxy: bypass proxy for NCBI requests
        no_proxy_hosts: direct-connect hosts

    Returns:
        dict[str, dict]: pmid → parsed result (same as parse_pmc_xml output)
    """
    os.makedirs(output_dir, exist_ok=True)
    pmids = [str(p) for p in pmids]
    results = {}

    # Check cache first
    uncached = []
    for pmid in pmids:
        txt_path = os.path.join(output_dir, f"{pmid}.txt")
        xml_path = os.path.join(output_dir, f"{pmid}.xml")
        if os.path.exists(txt_path) and os.path.exists(xml_path):
            with open(xml_path, "r", encoding="utf-8") as f:
                parsed = parse_pmc_xml(f.read())
            results[pmid] = parsed
        else:
            uncached.append(pmid)

    cached = len(results)
    if cached:
        print(f"全文缓存命中: {cached}/{len(pmids)}")

    if not uncached:
        return results

    # Resolve PMIDs → PMCIDs
    pmid_map = pmid_to_pmcid(
        uncached, email,
        bypass_proxy=bypass_proxy,
        no_proxy_hosts=no_proxy_hosts,
    )

    # Fetch and parse
    session = requests.Session()
    for i, pmid in enumerate(uncached):
        pmcid = pmid_map.get(pmid)
        if not pmcid:
            print(f"  [{i+1}/{len(uncached)}] PMID {pmid}: 无 PMC 全文，跳过")
            continue

        print(f"  [{i+1}/{len(uncached)}] PMID {pmid} → {pmcid} ...", end=" ")

        xml_text = fetch_pmc_xml(pmcid, session=session)
        if not xml_text:
            print("获取失败")
            continue

        # Save raw XML
        xml_path = os.path.join(output_dir, f"{pmid}.xml")
        with open(xml_path, "w", encoding="utf-8") as f:
            f.write(xml_text)

        # Parse and save text
        try:
            parsed = parse_pmc_xml(xml_text)
        except ET.ParseError as exc:
            print(f"XML 解析失败: {exc}")
            continue

        txt_path = os.path.join(output_dir, f"{pmid}.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(format_fulltext(parsed))

        n_sections = len(parsed["sections"])
        n_chars = len(parsed["full_text"])
        print(f"OK ({n_sections} sections, {n_chars} chars)")
        results[pmid] = parsed

        # Rate limit: be polite to Europe PMC
        if i < len(uncached) - 1:
            time.sleep(0.5)

    session.close()
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="从 PMC 获取论文全文并保存为结构化文本。"
    )
    parser.add_argument("--pmid", help="单个 PMID（测试用）")
    parser.add_argument("--input", default="data/raw/finalresult.tsv",
                        help="金标准 TSV 文件（批量模式）")
    parser.add_argument("--output-dir", default="data/interim/fulltext",
                        help="全文保存目录")
    parser.add_argument("--email", default="your_email@example.com",
                        help="NCBI Entrez 邮箱")
    parser.add_argument("--bypass-proxy", action="store_true",
                        default=parse_bool_env("BIO_LLM_BYPASS_PROXY_FOR_NCBI", False),
                        help="绕过代理访问 NCBI")
    return parser


def main():
    import pandas as pd

    args = build_parser().parse_args()

    if args.pmid:
        # Single PMID mode
        pmids = [args.pmid]
    else:
        # Batch from gold standard
        df = pd.read_csv(args.input, sep="\t", dtype={"PMID": str})
        df = df[df["PMID"].notna() & (df["PMID"].str.strip() != "")]
        pmids = clean_pmids(df["PMID"].tolist())

    results = fetch_fulltexts(
        pmids, args.email, args.output_dir,
        bypass_proxy=args.bypass_proxy,
    )
    print(f"\n完成: {len(results)}/{len(pmids)} 篇全文已获取")


if __name__ == "__main__":
    main()
