import argparse

import pandas as pd
from Bio import Entrez

# PubMed 结构化摘要中与实验证据相关的标签
EVIDENCE_LABELS = {
    "BACKGROUND", "OBJECTIVE", "PURPOSE", "AIM",
    "METHODS", "METHOD", "MATERIALS AND METHODS",
    "EXPERIMENTAL PROCEDURES", "EXPERIMENTAL DESIGN",
    "RESULTS", "FINDINGS", "OUTCOMES",
    "CONCLUSIONS", "CONCLUSION", "DISCUSSION",
}

SECTION_SEP = "\n---\n"


def _clean_pmids(pmid_list):
    expanded = []
    for p in pmid_list:
        if pd.notna(p):
            parts = str(p).replace(".0", "").split(";")
            expanded.extend([s.strip() for s in parts if s.strip().isdigit()])
    return sorted(set(expanded))


def fetch_abstracts(pmid_list, email):
    """从 PubMed 获取结构化摘要，保留各段标签。"""
    Entrez.email = email
    unique_pmids = _clean_pmids(pmid_list)
    if not unique_pmids:
        return {}

    print(f"正在向 PubMed 请求 {len(unique_pmids)} 条摘要...")
    try:
        pmid_string = ",".join(unique_pmids)
        with Entrez.efetch(db="pubmed", id=pmid_string, retmode="xml") as handle:
            results = Entrez.read(handle)

        abstract_dict = {}
        for article in results.get("PubmedArticle", []):
            pmid = str(article["MedlineCitation"]["PMID"])
            article_data = article["MedlineCitation"]["Article"]

            if "Abstract" in article_data:
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
            else:
                abstract_dict[pmid] = ""

        return abstract_dict
    except Exception as e:
        print(f"PubMed 获取失败: {e}")
        return {}


def generate_test_file(input_file, output_file, sample_size=5, seed=None,
                       email="your_email@example.com"):
    df = pd.read_csv(input_file, sep="\t", header=None,
                     names=["tf", "target", "direction", "pmid"], dtype={"pmid": str})

    df["clean_pmid"] = df["pmid"].apply(
        lambda x: x.split(";")[0].strip() if pd.notna(x) else ""
    )
    df = df[df["clean_pmid"] != ""]
    # 只取有明确调控方向的关系（排除 Unknown）
    df = df[df["direction"].str.lower() != "unknown"]

    sample_df = df.sample(min(sample_size, len(df)), random_state=seed)
    sample_pmids = sample_df["clean_pmid"].tolist()

    abstracts = fetch_abstracts(sample_pmids, email=email)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("=== TF-Target Analysis Test Data ===\n\n")
        for _, row in sample_df.iterrows():
            curr_pmid = row["clean_pmid"]
            f.write(f"{'=' * 50}\n")
            f.write(f"PMID: {curr_pmid}\n")
            f.write(f"TRRUST Standard: {row['tf']} -> {row['target']} ({row['direction']})\n")

            f.write(f"\nAbstract:{SECTION_SEP}")
            content = abstracts.get(curr_pmid, "Abstract not found in PubMed")

            if isinstance(content, dict):
                for label, text in content.items():
                    f.write(f"[{label}]\n{text}\n{SECTION_SEP}")
            else:
                f.write(f"{content}\n{SECTION_SEP}")
            f.write("\n")


def main():
    parser = argparse.ArgumentParser(
        description="从 TRRUST 数据随机选取 PMID 并下载结构化摘要。")
    parser.add_argument("--input", default="trrust_rawdata.human.tsv")
    parser.add_argument("--output", default="abstracts_for_test.txt")
    parser.add_argument("--sample-size", type=int, default=5)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--email", default="your_email@example.com")
    args = parser.parse_args()

    generate_test_file(
        input_file=args.input,
        output_file=args.output,
        sample_size=args.sample_size,
        seed=args.seed,
        email=args.email,
    )


if __name__ == "__main__":
    main()
