#!/usr/bin/env python3
"""合并 outputs/ 中所有 debug JSON，同一 PMID 保留最新运行的结果。

用法：
    python scripts/merge_debug.py [-o outputs/merged] [--clean]

扫描 outputs/**/analysis_results_debug.json，按目录时间戳取最新，
输出合并后的 analysis_results.json + analysis_results_debug.json。

--clean  删除空目录和被合并覆盖的旧目录（保留 merged 和最新源目录）
"""

import argparse
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def parse_dir_timestamp(dirpath: Path) -> datetime:
    """从目录名中提取时间戳，支持多种格式。

    支持：
        debug_20260528_140815
        20260528_140815
        debug for HDAC/debug_20260528_113431  (取子目录名)
    回退：目录 mtime
    """
    name = dirpath.name
    # 匹配 YYYYMMDD_HHMMSS
    m = re.search(r"(\d{8}_\d{6})", name)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
        except ValueError:
            pass
    # 回退到 mtime
    return datetime.fromtimestamp(dirpath.stat().st_mtime)


def collect_debug_files(outputs_dir: Path) -> list[tuple[Path, datetime]]:
    """收集所有 analysis_results_debug.json 及其时间戳。"""
    results = []
    for p in outputs_dir.rglob("analysis_results_debug.json"):
        dirpath = p.parent
        ts = parse_dir_timestamp(dirpath)
        results.append((p, ts))
    results.sort(key=lambda x: x[1])  # 时间从旧到新
    return results


def merge_debug_entries(debug_files: list[tuple[Path, datetime]]) -> tuple[dict, dict]:
    """合并所有 debug JSON，同一 PMID 保留最新条目。

    返回 (merged_debug, source_map):
        merged_debug: {pmid: entry_dict}
        source_map:   {pmid: source_dirpath}  用于日志
    """
    merged = {}
    source_map = {}

    for filepath, ts in debug_files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  跳过 {filepath}: {e}", file=sys.stderr)
            continue

        rel_dir = filepath.parent
        for pmid, entry in data.items():
            merged[pmid] = entry
            source_map[pmid] = rel_dir

    return merged, source_map


def build_analysis_results(merged_debug: dict) -> dict:
    """从合并的 debug 数据中提取 analysis_results.json 格式。"""
    result = {}
    for pmid, entry in merged_debug.items():
        result[pmid] = entry.get("result", [])
    return result


def main():
    parser = argparse.ArgumentParser(description="合并 outputs 中的 debug JSON")
    parser.add_argument(
        "--outputs-dir",
        default="outputs",
        help="outputs 目录路径 (默认: outputs)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="输出目录 (默认: outputs/merged_YYYYMMDD_HHMMSS)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="删除空目录",
    )
    args = parser.parse_args()

    outputs_dir = Path(args.outputs_dir)
    if not outputs_dir.exists():
        print(f"错误: {outputs_dir} 不存在", file=sys.stderr)
        sys.exit(1)

    # 收集所有 debug 文件
    debug_files = collect_debug_files(outputs_dir)
    if not debug_files:
        print("未找到 analysis_results_debug.json 文件")
        sys.exit(0)

    print(f"找到 {len(debug_files)} 个 debug 文件:")
    for filepath, ts in debug_files:
        rel = filepath.parent.relative_to(outputs_dir)
        try:
            with open(filepath) as f:
                n = len(json.load(f))
        except Exception:
            n = "?"
        print(f"  {rel}  ({ts:%Y-%m-%d %H:%M:%S})  {n} PMIDs")

    # 合并
    merged_debug, source_map = merge_debug_entries(debug_files)
    analysis_results = build_analysis_results(merged_debug)

    # 统计
    pmid_counts = defaultdict(int)
    for _, source in source_map.items():
        pmid_counts[source] += 1

    print(f"\n合并结果: {len(merged_debug)} 个 PMID")

    # 输出目录
    if args.output:
        out_dir = Path(args.output)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = outputs_dir / f"merged_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 写入
    debug_out = out_dir / "analysis_results_debug.json"
    results_out = out_dir / "analysis_results.json"

    with open(debug_out, "w", encoding="utf-8") as f:
        json.dump(merged_debug, f, ensure_ascii=False, indent=2)
    print(f"  debug:   {debug_out}")

    with open(results_out, "w", encoding="utf-8") as f:
        json.dump(analysis_results, f, ensure_ascii=False, indent=2)
    print(f"  results: {results_out}")

    # 清理空目录
    if args.clean:
        removed = 0
        for p in sorted(outputs_dir.rglob("*"), reverse=True):
            if p.is_dir() and not any(p.iterdir()):
                p.rmdir()
                removed += 1
                print(f"  删除空目录: {p.relative_to(outputs_dir)}")
        if removed:
            print(f"  共删除 {removed} 个空目录")


if __name__ == "__main__":
    main()
