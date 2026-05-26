import argparse
import os
from contextlib import contextmanager

import pandas as pd
from Bio import Entrez

EVIDENCE_LABELS = {
    "BACKGROUND", "OBJECTIVE", "PURPOSE", "AIM",
    "METHODS", "METHOD", "MATERIALS AND METHODS",
    "EXPERIMENTAL PROCEDURES", "EXPERIMENTAL DESIGN",
    "RESULTS", "FINDINGS", "OUTCOMES",
    "CONCLUSIONS", "CONCLUSION", "DISCUSSION",
}

SECTION_SEPARATOR = "\n---\n"
DEFAULT_NCBI_NO_PROXY_HOSTS = ",".join([
    "eutils.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov",
    "pubmed.ncbi.nlm.nih.gov",
])
PROXY_ENV_KEYS = [
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "all_proxy",
    "ALL_PROXY",
]


def parse_bool_env(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@contextmanager
def bypass_proxy_for_ncbi(enabled=False, no_proxy_hosts=DEFAULT_NCBI_NO_PROXY_HOSTS):
    """Bypass proxy env vars for NCBI requests.

    This only helps when the VPN/proxy is configured through environment variables
    or proxy tools that honor NO_PROXY. Full-tunnel/TUN VPNs still need split tunneling
    on the VPN client side.
    """
    if not enabled:
        yield
        return

    saved_env = {key: os.environ.get(key) for key in PROXY_ENV_KEYS + ["NO_PROXY", "no_proxy"]}
    try:
        for key in PROXY_ENV_KEYS:
            os.environ.pop(key, None)

        existing_no_proxy = ",".join(
            filter(None, [saved_env.get("NO_PROXY"), saved_env.get("no_proxy")])
        )
        merged_hosts = ",".join(filter(None, [existing_no_proxy, no_proxy_hosts]))
        os.environ["NO_PROXY"] = merged_hosts
        os.environ["no_proxy"] = merged_hosts
        print(f"NCBI 请求已启用代理旁路: {no_proxy_hosts}")
        yield
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def clean_pmids(pmid_list):
    expanded = []
    for pmid in pmid_list:
        if pd.notna(pmid):
            parts = str(pmid).replace(".0", "").split(";")
            expanded.extend([value.strip() for value in parts if value.strip().isdigit()])
    return sorted(set(expanded))


def fetch_abstracts(
    pmid_list,
    email,
    bypass_proxy=False,
    no_proxy_hosts=DEFAULT_NCBI_NO_PROXY_HOSTS,
):
    """Fetch structured PubMed abstracts and preserve section labels.

    Legacy function — kept as fallback for PMIDs without PMC full-text.
    """
    Entrez.email = email
    unique_pmids = clean_pmids(pmid_list)
    if not unique_pmids:
        return {}

    print(f"正在向 PubMed 请求 {len(unique_pmids)} 条摘要...")
    try:
        with bypass_proxy_for_ncbi(enabled=bypass_proxy, no_proxy_hosts=no_proxy_hosts):
            with Entrez.efetch(
                db="pubmed",
                id=",".join(unique_pmids),
                retmode="xml",
            ) as handle:
                results = Entrez.read(handle)

        abstract_dict = {}
        for article in results.get("PubmedArticle", []):
            pmid = str(article["MedlineCitation"]["PMID"])
            article_data = article["MedlineCitation"]["Article"]

            if "Abstract" not in article_data:
                abstract_dict[pmid] = ""
                continue

            abstract_list = article_data["Abstract"]["AbstractText"]
            if not isinstance(abstract_list, list):
                abstract_list = [abstract_list]

            sections = {}
            unstructured = []
            for part in abstract_list:
                label = part.attributes.get("Label", "") if hasattr(part, "attributes") else ""
                text = str(part).strip()
                if label:
                    label_upper = label.upper()
                    if label_upper in EVIDENCE_LABELS:
                        sections[label_upper] = text
                    else:
                        sections[f"[{label}]"] = text
                else:
                    unstructured.append(text)

            if sections:
                abstract_dict[pmid] = sections
            elif unstructured:
                abstract_dict[pmid] = " ".join(unstructured)
            else:
                abstract_dict[pmid] = ""

        return abstract_dict
    except Exception as exc:
        print(f"PubMed 获取失败: {exc}")
        return {}


def generate_test_file(
    input_file,
    output_file,
    sample_size=None,
    seed=None,
    email="your_email@example.com",
    bypass_proxy=False,
    no_proxy_hosts=DEFAULT_NCBI_NO_PROXY_HOSTS,
    fulltext_dir="data/interim/fulltext",
):
    """Generate test file with full-text articles from PMC.

    Fetches full-text from PMC for each PMID in the gold standard,
    falling back to abstracts for PMIDs without PMC availability.
    """
    from bio_llm.fulltext import fetch_fulltexts  # lazy import to avoid circular

    df = pd.read_csv(input_file, sep="\t", dtype={"PMID": str})

    # Drop rows with missing PMID
    df = df[df["PMID"].notna() & (df["PMID"].str.strip() != "")]

    pmid_groups = df.groupby("PMID")
    unique_pmids = list(pmid_groups.groups.keys())

    if sample_size is not None and sample_size < len(unique_pmids):
        sampled_pmids = pd.Series(unique_pmids).sample(
            sample_size, random_state=seed
        ).tolist()
    else:
        sampled_pmids = unique_pmids

    print(f"Gold standard: {len(unique_pmids)} PMIDs, using {len(sampled_pmids)}")

    # Fetch full texts from PMC
    fulltexts = fetch_fulltexts(
        sampled_pmids,
        email=email,
        output_dir=fulltext_dir,
        bypass_proxy=bypass_proxy,
        no_proxy_hosts=no_proxy_hosts,
    )

    # Fallback: fetch abstracts for PMIDs without PMC full-text
    missing_pmids = [p for p in sampled_pmids if p not in fulltexts]
    abstracts = {}
    if missing_pmids:
        print(f"  {len(missing_pmids)} 篇无 PMC 全文，降级获取摘要...")
        abstracts = fetch_abstracts(
            missing_pmids,
            email=email,
            bypass_proxy=bypass_proxy,
            no_proxy_hosts=no_proxy_hosts,
        )

    with open(output_file, "w", encoding="utf-8") as handle:
        handle.write("=== TF-Target Analysis Test Data ===\n\n")
        for curr_pmid in sampled_pmids:
            handle.write(f"{'=' * 50}\n")
            handle.write(f"PMID: {curr_pmid}\n")

            pmid_rows = pmid_groups.get_group(curr_pmid)
            for _, row in pmid_rows.iterrows():
                tf = str(row.get("TF", "")).strip()
                target = str(row.get("Target", "")).strip()
                assay = str(row.get("Assay", "")).strip()
                cellline = str(row.get("CellLine", "")).strip()

                parts = [f"Gold Standard: {tf} -> {target}"]
                if assay and assay != "nan":
                    parts.append(f"[Assay: {assay}]")
                if cellline and cellline != "nan" and cellline != "-":
                    parts.append(f"[CellLine: {cellline}]")
                handle.write(" ".join(parts) + "\n")

            # Full text or abstract
            if curr_pmid in fulltexts:
                parsed = fulltexts[curr_pmid]
                handle.write(f"\nFull Text:{SECTION_SEPARATOR}")
                for sec_title, sec_text in parsed.get("sections", []):
                    handle.write(f"[{sec_title}]\n{sec_text}\n{SECTION_SEPARATOR}")
            elif curr_pmid in abstracts:
                content = abstracts[curr_pmid]
                handle.write(f"\nAbstract:{SECTION_SEPARATOR}")
                if isinstance(content, dict):
                    for label, text in content.items():
                        handle.write(f"[{label}]\n{text}\n{SECTION_SEPARATOR}")
                else:
                    handle.write(f"{content}\n{SECTION_SEPARATOR}")
            else:
                handle.write(f"\nFull Text:{SECTION_SEPARATOR}")
                handle.write(f"Full text not available{SECTION_SEPARATOR}")
            handle.write("\n")


def build_parser():
    parser = argparse.ArgumentParser(
        description="从金标准数据集选取 PMID 并下载 PMC 全文。"
    )
    parser.add_argument("--input", default="data/raw/finalresult.tsv")
    parser.add_argument("--output", default="data/interim/abstracts_for_test.txt")
    parser.add_argument("--sample-size", type=int, default=None,
                        help="选取 PMID 数量 (默认全部)")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--email", default="your_email@example.com")
    parser.add_argument("--fulltext-dir", default="data/interim/fulltext",
                        help="全文缓存目录")
    parser.add_argument(
        "--bypass-proxy",
        action="store_true",
        default=parse_bool_env("BIO_LLM_BYPASS_PROXY_FOR_NCBI", False),
        help="绕过环境代理访问 NCBI。仅对基于代理/NO_PROXY 的梯子有效。",
    )
    parser.add_argument(
        "--ncbi-no-proxy-hosts",
        default=os.getenv("BIO_LLM_NCBI_NO_PROXY_HOSTS", DEFAULT_NCBI_NO_PROXY_HOSTS),
        help="NCBI 直连域名列表，逗号分隔。",
    )
    return parser


def main():
    args = build_parser().parse_args()
    generate_test_file(
        input_file=args.input,
        output_file=args.output,
        sample_size=args.sample_size,
        seed=args.seed,
        email=args.email,
        bypass_proxy=args.bypass_proxy,
        no_proxy_hosts=args.ncbi_no_proxy_hosts,
        fulltext_dir=args.fulltext_dir,
    )


if __name__ == "__main__":
    main()
