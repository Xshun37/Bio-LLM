#!/usr/bin/env python3
"""从金标准文献提取 Abstract+Keyword，喂给 LLM 提取 TF-Target 调控相关搜索关键词。

PMID 列表默认从 outputs/PMID_20.tsv 读取（每行一个 PMID，无 header）。
"""

import os
import sys
import time

import yaml
from Bio import Entrez

from llm_client import init_client, _call_llm

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
pmid_max_20 = os.path.join(PROJECT_ROOT, "outputs", "PMID_20.tsv")


def load_config():
    cfg_path = os.path.join(PROJECT_ROOT, "config.yaml")
    with open(cfg_path) as f:
        return yaml.safe_load(f)


def load_pmids(tsv_path):
    """从 TSV/TXT 提取唯一 PMID 列表（自动检测有无 header）。"""
    pmids = set()
    with open(tsv_path) as f:
        first = f.readline().strip()
        if first.split("\t")[0].isdigit():
            pmids.add(first.split("\t")[0])   # 无 header，第一行也是 PMID
        for line in f:
            parts = line.strip().split("\t")
            if parts and parts[0].isdigit():
                pmids.add(parts[0])
    return sorted(pmids)


def fetch_abstracts_and_keywords(pmids, email):
    """批量获取 PubMed Abstract + Keyword，返回 dict[pmid] -> {title, abstract, keywords}。"""
    Entrez.email = email
    result = {}

    batch_size = 200
    for i in range(0, len(pmids), batch_size):
        batch = pmids[i:i + batch_size]
        print(f"  请求 PubMed: {len(batch)} 篇 ({i+1}-{i+len(batch)}/{len(pmids)})...")
        with Entrez.efetch(db="pubmed", id=",".join(batch), retmode="xml") as handle:
            records = Entrez.read(handle)

        for article in records.get("PubmedArticle", []):
            pmid = str(article["MedlineCitation"]["PMID"])
            art = article["MedlineCitation"]["Article"]

            title = str(art.get("ArticleTitle", ""))

            abstract_parts = []
            if "Abstract" in art:
                for part in art["Abstract"].get("AbstractText", []):
                    abstract_parts.append(str(part))
            abstract = " ".join(abstract_parts)

            kw_parts = []
            if "KeywordList" in article["MedlineCitation"]:
                for kw_list in article["MedlineCitation"]["KeywordList"]:
                    for kw in kw_list:
                        kw_parts.append(str(kw))
            keywords = "; ".join(kw_parts)

            result[pmid] = {
                "title": title,
                "abstract": abstract,
                "keywords": keywords,
            }

        if i + batch_size < len(pmids):
            time.sleep(0.5)

    return result


def build_abstract_all(papers):
    """将所有论文的 Abstract + Keyword 拼接成大文本。"""
    parts = []
    no_abstract = []
    for pmid, data in sorted(papers.items()):
        entry = f"[PMID:{pmid}]\n"
        entry += f"Title: {data['title']}\n"
        if data["abstract"]:
            entry += f"Abstract: {data['abstract']}\n"
        else:
            no_abstract.append(pmid)
        if data["keywords"]:
            entry += f"Keywords: {data['keywords']}\n"
        parts.append(entry)

    if no_abstract:
        print(f"  {len(no_abstract)} 篇无摘要: {no_abstract}")
    return "\n---\n".join(parts)


def main():
    cfg = load_config()
    email = cfg.get("email", "sanae0307@stu.pku.edu.cn")
    model = cfg.get("model", "qwen3.7-max")
    temperature = cfg.get("temperature", 0)
    seed = cfg.get("seed")
    api_key = cfg.get("api_key", "")

    tsv_path = pmid_max_20
    if not os.path.exists(tsv_path):
        print(f"金标准文件不存在: {tsv_path}")
        sys.exit(1)

    # Step 1: 获取 PMID 列表
    pmids = load_pmids(tsv_path)
    print(f"Step 1: 从金标准提取 {len(pmids)} 个唯一 PMID")

    # Step 2: 批量获取 Abstract + Keyword
    print("Step 2: 批量获取 PubMed Abstract + Keyword...")
    papers = fetch_abstracts_and_keywords(pmids, email)
    print(f"  获取到 {len(papers)} 篇")

    # Step 3: 拼接
    abstract_all = build_abstract_all(papers)
    print(f"Step 3: 拼接完成，共 {len(abstract_all)} 字符")

    # Step 4: 调用 LLM
    print(f"Step 4: 调用 LLM ({model})...")
    init_client(api_key=api_key)

    system_prompt = "你是一名优秀的生物学专家和数据分析专家，善于从海量内容中提取关键词组，并且明白如何组合才能搜索到尽可能准确的内容。"
    ROUND1_prompt = """
    #背景#
    {abstract_all}
    上面提供了{papers_count}篇文献的Abstract以及Keyword，他们都是生物学领域关于TF-Target调控关系的文献。

    #目的#
    我们需要知道，有关TF-Target调控关系的文献在Abstract和Keyword里往往使用哪些词组进行描述，以便于初步**搜索和筛选大量用于研究调控关系的文献**

    #实现步骤#
    1. 从以上摘要中统计出现频率前20的短语/词组
    2. 给出你的选取理由，并提出可能搜索到的其他非调控关系的内容
    ====
    输出结构：
    [{"Key": "词组/短语", "非调控关系的内容": "some example"}]
    ====
    """.replace("{abstract_all}", abstract_all).replace("{papers_count}", str(len(papers)))

    ROUND2_prompt = """根据上面的内容，组合你认为最适合用于PUBMED搜索的提示词组合方法，使用 OR,AND,NOT连接，输出结果，以\\n分割"""


    # Round 1: 提取高频词组
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": ROUND1_prompt},
    ]

    resp1 = _call_llm(model, temperature, messages, seed=seed)
    if resp1 is None:
        print("LLM Round 1 调用失败（重试耗尽）")
        sys.exit(1)

    reply1 = resp1.choices[0].message.content
    print("\n" + "=" * 60)
    print("Round 1 输出 (高频词组):")
    print("=" * 60)
    print(reply1)

    # Round 2: 组合搜索策略
    messages.append({"role": "assistant", "content": reply1})
    messages.append({"role": "user", "content": ROUND2_prompt})

    resp2 = _call_llm(model, temperature, messages, seed=seed)
    if resp2 is None:
        print("LLM Round 2 调用失败（重试耗尽）")
        sys.exit(1)

    reply2 = resp2.choices[0].message.content
    print("\n" + "=" * 60)
    print("Round 2 输出 (搜索组合):")
    print("=" * 60)
    print(reply2)

    # Step 5: 保存中间文件
    output_dir = os.path.join(PROJECT_ROOT, "outputs", "keyword_extraction")
    os.makedirs(output_dir, exist_ok=True)

    abstract_file = os.path.join(output_dir, "abstracts_all.txt")
    with open(abstract_file, "w", encoding="utf-8") as f:
        f.write(abstract_all)
    print(f"\n已保存摘要拼接文本: {abstract_file}")

    llm_file1 = os.path.join(output_dir, "round1_keywords.txt")
    with open(llm_file1, "w", encoding="utf-8") as f:
        f.write(reply1)
    print(f"已保存 Round 1 输出: {llm_file1}")

    llm_file2 = os.path.join(output_dir, "round2_search.txt")
    with open(llm_file2, "w", encoding="utf-8") as f:
        f.write(reply2)
    print(f"已保存 Round 2 输出: {llm_file2}")


if __name__ == "__main__":
    main()
