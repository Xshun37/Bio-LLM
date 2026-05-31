#!/usr/bin/env python3
"""过滤调控关系：去重 + 去掉纯 Literature，输出过滤后的 JSON + TSV。

用法:
    python scripts/filter_relations.py <output_dir>
    python scripts/filter_relations.py outputs/production_20260531_xxx
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

from bio_llm.result_utils import dump_json, load_json, normalize_results_map, relation_list


def load_results(json_path):
    return normalize_results_map(load_json(json_path))


def normalize_assay(assay_str):
    """标准化 assay 字符串为集合。"""
    if not assay_str:
        return set()
    return set(a.strip() for a in assay_str.replace("|", ";").split(";") if a.strip())


def is_pure_literature(assays):
    """判断是否纯 Literature（无实验验证）。"""
    return assays == {"Literature"} or len(assays) == 0


def filter_and_dedup(data):
    """过滤 + 去重，返回 (filtered_results, stats)。"""
    stats = {
        "total_papers": 0,
        "total_relations": 0,
        "removed_pure_literature": 0,
        "removed_duplicate": 0,
        "kept": 0,
        "papers_with_relations": 0,
    }

    # 全局去重：(TF, Target) → 保留实验验证最多的
    global_pairs = {}  # (tf_upper, target_upper) → (paper_id, entry)

    for paper_id, relations in data.items():
        stats["total_papers"] += 1
        for entry in relation_list(relations):
            stats["total_relations"] += 1

            tf = entry.get("TF", "").strip()
            target = entry.get("Target", "").strip()
            assays = normalize_assay(entry.get("assay", ""))

            # 过滤纯 Literature
            if is_pure_literature(assays):
                stats["removed_pure_literature"] += 1
                continue

            if not tf or not target:
                stats["removed_pure_literature"] += 1
                continue

            # 去重：同 (TF, Target) 保留实验方法最多的
            key = (tf.upper(), target.upper())
            if key in global_pairs:
                existing = global_pairs[key]
                existing_assays = normalize_assay(existing[1].get("assay", ""))
                # 保留实验方法更多的（不含 Literature）
                existing_exp = existing_assays - {"Literature"}
                current_exp = assays - {"Literature"}
                if len(current_exp) > len(existing_exp):
                    stats["removed_duplicate"] += 1
                    global_pairs[key] = (paper_id, entry)
                else:
                    stats["removed_duplicate"] += 1
            else:
                global_pairs[key] = (paper_id, entry)

    # 构建结果
    filtered = defaultdict(list)
    for (tf, target), (paper_id, entry) in global_pairs.items():
        filtered[paper_id].append(entry)
        stats["kept"] += 1

    stats["papers_with_relations"] = len(filtered)

    return dict(filtered), stats


def write_tsv(filtered, output_path):
    rows = []
    for paper_id, entries in sorted(filtered.items()):
        for entry in entries:
            rows.append({
                "PaperID": paper_id,
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


def main():
    parser = argparse.ArgumentParser(description="过滤调控关系：去重 + 去掉纯 Literature")
    parser.add_argument("output_dir", help="输出目录 (如 outputs/production_20260531_xxx)")
    parser.add_argument("--input-json", default="analysis_results.json",
                        help="输入 JSON 文件名 (默认 analysis_results.json)")
    parser.add_argument("--output-json", default="filtered_results.json",
                        help="输出 JSON 文件名 (默认 filtered_results.json)")
    parser.add_argument("--output-tsv", default="filtered_results.tsv",
                        help="输出 TSV 文件名 (默认 filtered_results.tsv)")
    args = parser.parse_args()

    # 处理路径
    if not os.path.isabs(args.output_dir):
        out_dir = os.path.join(PROJECT_ROOT, args.output_dir)
    else:
        out_dir = args.output_dir

    json_path = os.path.join(out_dir, args.input_json)
    if not os.path.exists(json_path):
        print(f"错误: 未找到 {json_path}")
        sys.exit(1)

    # 加载 + 过滤
    data = load_results(json_path)
    filtered, stats = filter_and_dedup(data)

    # 保存
    out_json = os.path.join(out_dir, args.output_json)
    dump_json(filtered, out_json)

    out_tsv = os.path.join(out_dir, args.output_tsv)
    n_rows = write_tsv(filtered, out_tsv)

    # 终端输出统计
    print("=" * 50)
    print("调控关系过滤结果")
    print("=" * 50)
    print(f"  论文总数:           {stats['total_papers']}")
    print(f"  原始关系数:         {stats['total_relations']}")
    print(f"  去掉纯 Literature:  {stats['removed_pure_literature']}")
    print(f"  去掉重复:           {stats['removed_duplicate']}")
    print(f"  ─────────────────────────")
    print(f"  保留关系数:         {stats['kept']}")
    print(f"  有关系论文数:       {stats['papers_with_relations']}")
    print("=" * 50)
    print(f"\n输出文件:")
    print(f"  JSON: {out_json}")
    print(f"  TSV:  {out_tsv} ({n_rows} 条)")


if __name__ == "__main__":
    main()
