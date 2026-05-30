#!/usr/bin/env python3
"""生产规模 TF-Target 提取 pipeline。

读取 data/raw/paper_for_produce/ 中的 PDF 或 TXT 文件，
调用 LLM 提取 TF-Target 调控关系，输出 TSV。

用法:
    python scripts/run_production.py
    python scripts/run_production.py --input data/raw/paper_for_produce --workers 4
    python scripts/run_production.py --limit 5 --debug
"""

import argparse
import csv
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import fitz  # PyMuPDF

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

import yaml

from bio_llm.analysis import (
    init_client,
    analyze_tf_interaction,
    DEFAULT_MODEL,
)


def _load_config():
    cfg_path = os.path.join(PROJECT_ROOT, "config", "config.yaml")
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            return yaml.safe_load(f) or {}
    return {}

DEFAULT_INPUT = "data/raw/paper_for_produce"
DEFAULT_OUTPUT = "outputs/production_results.tsv"
DEFAULT_JSON = "outputs/production_results.json"

# ── PDF → 文本 ──

SECTION_PATTERNS = [
    r"^(Introduction|Background)\s*$",
    r"^(Materials?\s+and\s+Methods?|Experimental\s+Procedures?|Methods?)\s*$",
    r"^(Results?)\s*$",
    r"^(Discussion)\s*$",
    r"^(Conclusions?)\s*$",
    r"^(Supplementary|References|Acknowledgm|Funding|Author\s+Contributions|Data\s+Availability|Conflict\s+of\s+Interest)",
]

META_PATTERNS = [
    r"^\d+\s*$",  # standalone page numbers
    r"^doi:",
    r"published online",
    r"received\s|accepted\s|revised\s",
    r"©.*\d{4}",
    r"correspondence.*to",
]


def _is_section_header(line):
    stripped = line.strip()
    if not stripped or len(stripped) > 100:
        return False
    for pat in SECTION_PATTERNS:
        if re.match(pat, stripped, re.IGNORECASE):
            return True
    return False


def _is_meta_line(line):
    stripped = line.strip()
    if not stripped:
        return False
    for pat in META_PATTERNS:
        if re.search(pat, stripped, re.IGNORECASE):
            return True
    return False


def pdf_to_text(pdf_path):
    """将 PDF 转为纯文本，按 section 组织。"""
    doc = fitz.open(pdf_path)
    sections = []
    current_section = "Untitled"
    current_text = []
    in_refs = False

    for page in doc:
        blocks = page.get_text("blocks")
        blocks.sort(key=lambda b: (round(b[1] / 20) * 20, b[0]))

        for block in blocks:
            if block[6] != 0:
                continue
            text = block[4].strip()
            if not text:
                continue

            # 跳过 References 之后所有内容
            if re.match(r"^(References|REFERENCES|Bibliography)\s*$", text, re.IGNORECASE):
                in_refs = True
                continue
            if in_refs:
                continue

            # 跳过元数据行
            if _is_meta_line(text):
                continue

            if _is_section_header(text):
                if current_text:
                    sections.append((current_section, "\n".join(current_text).strip()))
                current_section = text.strip()
                current_text = []
            else:
                current_text.append(text)

    if current_text:
        sections.append((current_section, "\n".join(current_text).strip()))

    doc.close()

    if not sections:
        return ""

    parts = []
    for title, text in sections:
        if text:
            parts.append(f"# {title}\n\n{text}")
    return "\n\n".join(parts)


def load_papers(input_dir):
    """加载 input_dir 下所有 PDF/TXT 文件，返回 {file_id: text}。"""
    papers = {}
    files = sorted(os.listdir(input_dir))

    for fname in files:
        path = os.path.join(input_dir, fname)
        if not os.path.isfile(path):
            continue

        base, ext = os.path.splitext(fname)
        file_id = base

        if ext.lower() == ".pdf":
            try:
                text = pdf_to_text(path)
                if text:
                    papers[file_id] = text
                    print(f"  PDF: {fname} → {len(text)} chars")
                else:
                    print(f"  PDF: {fname} → 空文本，跳过")
            except Exception as e:
                print(f"  PDF: {fname} → 转换失败: {e}")
        elif ext.lower() == ".txt":
            with open(path, "r", encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                papers[file_id] = text
                print(f"  TXT: {fname} → {len(text)} chars")
        else:
            continue

    return papers


# ── TSV 输出 ──

def results_to_tsv(results, output_path):
    """将 LLM 结果写为 TSV。

    results: dict  file_id → list of {TF, Target, assay, cellLine}
    """
    rows = []
    for file_id, entries in results.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            rows.append({
                "PaperID": file_id,
                "TF": entry.get("TF", ""),
                "Target": entry.get("Target", ""),
                "Assay": entry.get("assay", ""),
                "CellLine": entry.get("cellLine", ""),
            })

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["PaperID", "TF", "Target", "Assay", "CellLine"],
                                delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    return len(rows)


# ── 主流程 ──

def main():
    parser = argparse.ArgumentParser(description="生产规模 TF-Target 提取")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="论文目录 (PDF/TXT)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="输出 TSV 路径")
    parser.add_argument("--json-output", default=DEFAULT_JSON, help="输出 JSON 路径 (中间结果)")
    parser.add_argument("--workers", type=int, default=2, help="并行 worker 数")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 篇 (调试)")
    parser.add_argument("--debug", action="store_true", help="保存 LLM 中间输出")
    parser.add_argument("--skip-existing", action="store_true", help="跳过已处理的文件")
    parser.add_argument("--seed", type=int, default=None, help="LLM 输出确定性种子")
    args = parser.parse_args()

    cfg = _load_config()
    seed = args.seed if args.seed is not None else cfg.get("seed")
    model_name = cfg.get("model", DEFAULT_MODEL)
    temperature = cfg.get("temperature", 0)

    input_dir = os.path.join(PROJECT_ROOT, args.input)
    output_path = os.path.join(PROJECT_ROOT, args.output)
    json_path = os.path.join(PROJECT_ROOT, args.json_output)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Step 1: 加载论文
    print(f"Step 1: 加载论文 ({input_dir})")
    papers = load_papers(input_dir)
    if not papers:
        print("未找到任何论文，退出。")
        return
    print(f"  共 {len(papers)} 篇\n")

    # 限制数量
    if args.limit > 0:
        paper_ids = sorted(papers.keys())[:args.limit]
        papers = {k: papers[k] for k in paper_ids}
        print(f"  限制为前 {args.limit} 篇\n")

    # 跳过已处理
    existing_results = {}
    if args.skip_existing and os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            existing_results = json.load(f)
        print(f"  已有 {len(existing_results)} 篇结果，跳过\n")

    todo = {k: v for k, v in papers.items() if k not in existing_results}
    if not todo:
        print("所有论文已处理，退出。")
        return

    # Step 2: LLM 分析
    print(f"Step 2: LLM 分析 ({len(todo)} 篇, workers={args.workers}, seed={seed})")
    init_client()

    results = dict(existing_results)  # 保留已有结果
    debug_info = {}

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_map = {
            executor.submit(
                analyze_tf_interaction, text,
                model_name=model_name, temperature=temperature,
                debug=True, seed=seed
            ): file_id
            for file_id, text in todo.items()
        }

        done_count = 0
        for future in as_completed(future_map):
            file_id = future_map[future]
            done_count += 1
            try:
                raw = future.result()
                if isinstance(raw, dict):
                    if "result" in raw:
                        results[file_id] = raw["result"]
                        if "round1_analysis" in raw:
                            debug_info[file_id] = raw
                    elif "error" in raw:
                        results[file_id] = raw
                        print(f"  [{done_count}/{len(todo)}] {file_id}: ERROR {raw['error']}")
                        continue
                    else:
                        results[file_id] = raw
                else:
                    results[file_id] = raw

                count = len(results[file_id]) if isinstance(results[file_id], list) else 0
                print(f"  [{done_count}/{len(todo)}] {file_id}: {count} 条关系")
            except Exception as e:
                results[file_id] = {"error": str(e)}
                print(f"  [{done_count}/{len(todo)}] {file_id}: EXCEPTION {e}")

    # Step 3: 保存结果
    print(f"\nStep 3: 保存结果")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"  JSON: {json_path}")

    if debug_info:
        debug_path = json_path.replace(".json", "_debug.json")
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump(debug_info, f, ensure_ascii=False, indent=2)
        print(f"  Debug: {debug_path}")

    n_rows = results_to_tsv(results, output_path)
    print(f"  TSV: {output_path} ({n_rows} 条)")

    # 汇总
    total = len(results)
    success = sum(1 for v in results.values() if isinstance(v, list))
    errors = total - success
    total_relations = sum(len(v) for v in results.values() if isinstance(v, list))
    print(f"\n完成: {success}/{total} 篇成功, {errors} 篇失败, 共 {total_relations} 条关系")


if __name__ == "__main__":
    main()
