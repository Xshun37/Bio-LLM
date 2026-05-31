#!/usr/bin/env python3
"""PubMed TF-Target 批量搜索 + LLM 筛选脚本。

流程：
1. 加载 TF 列表 + HGNC 别名
2. PubMed esearch（别名扩展 + 关键词限定 + retmax）
3. efetch 批量拉摘要
4. LLM 并行筛选是否涉及 TF-Target 调控
5. 保存结果到时间戳目录（支持断点续传）
"""

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import yaml
from Bio import Entrez

from llm_client import init_client, _call_llm, clean_json_text

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# 搜索关键词限定
KEYWORDS = [
    '"transcriptional"[tiab] OR "gene regulat*"[tiab]'
]


def load_config():
    cfg_path = os.path.join(PROJECT_ROOT, "config.yaml")
    with open(cfg_path) as f:
        return yaml.safe_load(f)


def setup_ncbi_proxy(cfg):
    """根据配置设置 NCBI 代理旁路。"""
    if cfg.get("ncbi_bypass_proxy", False):
        no_proxy = os.environ.get("no_proxy", "")
        extra = cfg.get("ncbi_no_proxy_hosts", "")
        if extra:
            no_proxy = f"{no_proxy},{extra}" if no_proxy else extra
        os.environ["no_proxy"] = no_proxy


def load_tfs(tf_file):
    tfs = []
    with open(tf_file) as f:
        for line in f:
            tf = line.strip()
            if tf:
                tfs.append(tf)
    return tfs


def load_aliases(alias_file):
    with open(alias_file) as f:
        return json.load(f)


def load_prompt(prompt_file):
    with open(prompt_file) as f:
        return yaml.safe_load(f)


def build_search_query(tf, aliases, keywords):
    """构建 PubMed 搜索查询：(TF OR 别名) AND (关键词1 OR 关键词2 ...)"""
    # 过滤噪声别名（纯数字、LOC* 等）
    valid_aliases = [a for a in aliases if a and not a[0].isdigit() and "LOC" not in a]
    if not valid_aliases:
        valid_aliases = [tf]

    tf_part = " OR ".join([f'"{a}"[Title/Abstract]' for a in valid_aliases])
    kw_part = " OR ".join(keywords)
    return f"({tf_part}) AND ({kw_part})"


def search_pubmed(query, retmax, email):
    """搜索 PubMed，返回 PMID 列表。"""
    Entrez.email = email
    try:
        handle = Entrez.esearch(db="pubmed", term=query, retmax=retmax)
        record = Entrez.read(handle)
        handle.close()
        return record["IdList"], int(record["Count"])
    except Exception as e:
        print(f"  搜索失败: {e}")
        return [], 0


def fetch_abstracts(pmids, email, batch_size=200):
    """批量获取摘要和 DOI，返回 dict[pmid] -> {title, abstract, doi}。"""
    Entrez.email = email
    result = {}
    for i in range(0, len(pmids), batch_size):
        batch = pmids[i:i + batch_size]
        print(f"  efetch: {len(batch)} 篇 ({i+1}-{i+len(batch)}/{len(pmids)})...")

        # 带重试的 efetch
        records = None
        for attempt in range(3):
            try:
                with Entrez.efetch(db="pubmed", id=",".join(batch), retmode="xml") as handle:
                    records = Entrez.read(handle)
                break
            except Exception as e:
                if attempt == 2:
                    print(f"    批次失败 ({attempt+1} 次重试): {e}")
                    records = {"PubmedArticle": []}
                else:
                    delay = 2 ** attempt
                    print(f"    传输中断，{delay}s 后重试 ({attempt+1}/3)...")
                    time.sleep(delay)

        for article in records.get("PubmedArticle", []):
            pmid = str(article["MedlineCitation"]["PMID"])
            art = article["MedlineCitation"]["Article"]
            title = str(art.get("ArticleTitle", ""))

            abstract_parts = []
            if "Abstract" in art:
                for part in art["Abstract"].get("AbstractText", []):
                    abstract_parts.append(str(part))
            abstract = " ".join(abstract_parts)

            # 提取 DOI
            doi = None
            pubmed_data = article.get("PubmedData", {})
            for article_id in pubmed_data.get("ArticleIdList", []):
                if getattr(article_id, "attributes", {}).get("IdType") == "doi":
                    doi = str(article_id).strip()
                    break

            result[pmid] = {"title": title, "abstract": abstract, "doi": doi}

        if i + batch_size < len(pmids):
            time.sleep(0.5)
    return result


def screen_one(pmid, data, tf_name, prompt_cfg, model, temperature, seed, max_tokens=None):
    """用 LLM 判断单篇是否涉及 TF-Target 调控。"""
    system_prompt = prompt_cfg["system"]
    user_template = prompt_cfg["user"]
    user_prompt = user_template.format(
        tf_name=tf_name,
        title=data["title"],
        abstract=data["abstract"] or "(无摘要)",
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    resp = _call_llm(model, temperature, messages, seed=seed, max_tokens=max_tokens)
    if resp is None:
        return {"pmid": pmid, "tf": tf_name, "relevant": None, "reason": "LLM调用失败"}

    raw = resp.choices[0].message.content
    try:
        cleaned = clean_json_text(raw)
        result = json.loads(cleaned, strict=False)
        return {
            "pmid": pmid,
            "tf": tf_name,
            "title": data["title"],
            "relevant": result.get("relevant", None),
            "reason": result.get("reason", ""),
        }
    except (json.JSONDecodeError, KeyError):
        return {
            "pmid": pmid,
            "tf": tf_name,
            "title": data["title"],
            "relevant": None,
            "reason": f"JSON解析失败: {raw[:100]}",
        }


def save_checkpoint(output_dir, filename, data):
    """保存中间结果。"""
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="PubMed TF-Target 批量搜索 + LLM 筛选")
    parser.add_argument("--tf-file", default="data/TF.txt")
    parser.add_argument("--alias-file", default="data/TF_aliases.json")
    parser.add_argument("--prompt-file", default="prompts/screen_prompt.yaml")
    parser.add_argument("--retmax", type=int, default=150)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", default="outputs/batch_screen")
    parser.add_argument("--resume", action="store_true", help="继续最近一次运行（断点续传）")
    parser.add_argument("--skip-llm", action="store_true", help="跳过 LLM 筛选，只搜索")
    parser.add_argument("--limit-tfs", type=int, default=0, help="只处理前 N 个 TF（调试用）")
    args = parser.parse_args()

    cfg = load_config()
    email = cfg.get("email", "sanae0307@stu.pku.edu.cn")
    api_key = cfg.get("api_key", "")
    model = cfg.get("model", "qwen3.7-max")
    temperature = cfg.get("temperature", 0)
    seed = cfg.get("seed")
    max_tokens = cfg.get("max_tokens")

    # 设置 NCBI
    setup_ncbi_proxy(cfg)
    Entrez.email = email
    if api_key:
        Entrez.api_key = api_key

    # 输出目录：每次运行创建时间戳子目录，或 --resume 续传最近一次
    base_output = os.path.join(PROJECT_ROOT, args.output)
    os.makedirs(base_output, exist_ok=True)

    if args.resume:
        # 查找最近的 run_* 目录
        runs = sorted([d for d in os.listdir(base_output) if d.startswith("run_")])
        if not runs:
            print("未找到可续传的运行目录")
            return
        run_dir = os.path.join(base_output, runs[-1])
        print(f"续传运行: {runs[-1]}")
    else:
        # 创建新运行目录
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = os.path.join(base_output, f"run_{timestamp}")
        os.makedirs(run_dir)
        print(f"新建运行: run_{timestamp}")

    output_dir = run_dir

    # 加载数据
    tfs = load_tfs(os.path.join(PROJECT_ROOT, args.tf_file))
    aliases = load_aliases(os.path.join(PROJECT_ROOT, args.alias_file))
    prompt_cfg = load_prompt(os.path.join(PROJECT_ROOT, args.prompt_file))

    if args.limit_tfs > 0:
        tfs = tfs[:args.limit_tfs]

    print(f"TF 数量: {len(tfs)}")
    print(f"retmax: {args.retmax}")
    print(f"关键词: {', '.join(KEYWORDS)}")
    print(f"模型: {model}")
    print(f"输出: {output_dir}\n")

    # ── Step 1: 搜索 PubMed ──
    search_file = os.path.join(output_dir, "search_results.json")
    if os.path.exists(search_file):
        with open(search_file) as f:
            search_results = json.load(f)
        print(f"Step 1: 从缓存恢复，已搜索 {len(search_results)} 个 TF")
    else:
        search_results = {}

    all_pmids = set()
    tf_pmid_map = {}  # tf -> [pmids]

    for i, tf in enumerate(tfs):
        if tf in search_results:
            tf_pmid_map[tf] = search_results[tf]["pmids"]
            all_pmids.update(search_results[tf]["pmids"])
            continue

        tf_aliases = aliases.get(tf, [tf])
        query = build_search_query(tf, tf_aliases, KEYWORDS)

        pmids, total_count = search_pubmed(query, args.retmax, email)
        search_results[tf] = {"pmids": pmids, "total_count": total_count, "query": query}
        tf_pmid_map[tf] = pmids
        all_pmids.update(pmids)

        fetched = min(len(pmids), args.retmax)
        print(f"  [{i+1}/{len(tfs)}] {tf}: {fetched}/{total_count} 篇")

        # 每 10 个 TF 保存一次
        if (i + 1) % 10 == 0:
            save_checkpoint(output_dir, "search_results.json", search_results)

        time.sleep(0.35)

    save_checkpoint(output_dir, "search_results.json", search_results)
    print(f"\n搜索完成: {len(all_pmids)} 个唯一 PMID")

    if args.skip_llm:
        print("跳过 LLM 筛选（--skip-llm）")
        return

    # ── Step 2: 批量获取摘要 ──
    print(f"\nStep 2: 获取 {len(all_pmids)} 篇摘要...")
    abstracts_file = os.path.join(output_dir, "abstracts.json")
    if os.path.exists(abstracts_file):
        with open(abstracts_file) as f:
            abstracts = json.load(f)
        print(f"  从缓存加载 {len(abstracts)} 篇")
    else:
        abstracts = fetch_abstracts(sorted(all_pmids), email)
        save_checkpoint(output_dir, "abstracts.json", abstracts)
        print(f"  获取到 {len(abstracts)} 篇")

    # ── Step 3: LLM 筛选 ──
    print(f"\nStep 3: LLM 筛选 (workers={args.workers})...")
    screen_file = os.path.join(output_dir, "screen_results.json")
    if os.path.exists(screen_file):
        with open(screen_file) as f:
            screen_results = json.load(f)
        print(f"  从断点恢复 {len(screen_results)} 条")
    else:
        screen_results = []

    screened_pmids = {r["pmid"] for r in screen_results}

    # 构建待筛选任务
    tasks = []
    for tf, info in tf_pmid_map.items():
        for pmid in info:
            if pmid not in screened_pmids and pmid in abstracts:
                tasks.append((pmid, abstracts[pmid], tf))

    print(f"  待筛选: {len(tasks)} 篇")

    if tasks:
        init_client(api_key=api_key)
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(screen_one, pmid, data, tf, prompt_cfg, model, temperature, seed, max_tokens): pmid
                for pmid, data, tf in tasks
            }
            done_count = 0
            for future in as_completed(futures):
                result = future.result()
                screen_results.append(result)
                done_count += 1
                if done_count % 50 == 0:
                    save_checkpoint(output_dir, "screen_results.json", screen_results)
                    print(f"  进度: {done_count}/{len(tasks)}")

        save_checkpoint(output_dir, "screen_results.json", screen_results)

    # ── Step 4: 汇总输出 ──
    relevant = [r for r in screen_results if r.get("relevant") is True]
    irrelevant = [r for r in screen_results if r.get("relevant") is False]
    unknown = [r for r in screen_results if r.get("relevant") is None]

    print(f"\n{'='*60}")
    print(f"筛选结果汇总:")
    print(f"  相关: {len(relevant)}")
    print(f"  不相关: {len(irrelevant)}")
    print(f"  未知/失败: {len(unknown)}")

    # 保存 relevant PMIDs
    relevant_pmids = sorted(set(r["pmid"] for r in relevant))
    pmids_file = os.path.join(output_dir, "relevant_pmids.txt")
    with open(pmids_file, "w") as f:
        for pmid in relevant_pmids:
            f.write(f"{pmid}\n")
    print(f"\n已保存 {len(relevant_pmids)} 个相关 PMID 到: {pmids_file}")


if __name__ == "__main__":
    main()
