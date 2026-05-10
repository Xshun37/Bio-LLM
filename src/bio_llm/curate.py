#!/usr/bin/env python3
"""Bio-LLM anomaly curation helper.

Interactive tool for adding TRRUST data issue records to
data/curated/trrust_anomalies.jsonl with step-by-step system prompts.
"""

import argparse
import json
import os
import re
import sys
from datetime import date

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_ANOMALIES = os.path.join(PROJECT_ROOT, "data", "curated", "trrust_anomalies.jsonl")

ANOMALY_TYPES = {
    "1": ("phantom_gene", "基因名不存在于该论文中（如核苷酸被误读为基因名）"),
    "2": ("indirect_chain", "TRRUST 记录为直接调控，实际为间接级联"),
    "3": ("wrong_direction", "调控方向记录错误"),
    "4": ("other", "其他类型问题（请在 issue 中详述）"),
}

WELCOME = """============================================================
  Bio-LLM 异常标注工具
  用于向 data/curated/trrust_anomalies.jsonl 添加 TRRUST 数据问题记录
============================================================

支持的异常类型:
  phantom_gene    — 基因名不存在于该论文中（如核苷酸被误读为基因名）
  indirect_chain  — TRRUST 记录为直接调控，实际为间接级联
  wrong_direction  — 调控方向记录错误
  other           — 其他类型问题（请在 issue 中详述）
"""


def load_anomalies(path):
    result = {}
    entries = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    pmid = entry.get("pmid", "")
                    result.setdefault(str(pmid), []).append(entry)
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
    return result, entries


def save_anomalies(entries, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def validate_pmid(pmid):
    if not pmid.strip().isdigit():
        return False, "PMID 必须为纯数字"
    return True, ""


def validate_trrust_entry(entry):
    if not re.match(r"\S+->\S+\s*\([^)]+\)", entry.strip()):
        return False, "格式错误，应为: TF->Target (Direction)，如 HNF4G->AFP (Activation)"
    return True, ""


def step_prompt(step, total, title, hint):
    print(f"\n{'='*60}")
    print(f"Step {step}/{total} — {title}")
    print(f"{'='*60}")
    if hint:
        print(hint)
    print()


def confirm_prompt(summary):
    print(f"\n{'='*60}")
    print("确认提交")
    print(f"{'='*60}")
    for key, val in summary.items():
        print(f"  {key}: {val}")
    print()
    while True:
        ans = input("确认添加? [y/N]: ").strip().lower()
        if ans in ("y", "n", ""):
            return ans == "y"
        print("请输入 y 或 n")


def cmd_add(entries, path):
    print(WELCOME)

    # Step 1: PMID
    step_prompt(1, 5, "PMID",
                "请输入有问题的 PubMed ID:\n"
                "  例如: 9792724\n"
                "  提示: PMID 可以在 TRRUST 原始 TSV 第四列找到")
    while True:
        pmid = input("> ").strip()
        ok, msg = validate_pmid(pmid)
        if ok:
            break
        print(f"  ✗ {msg}，请重新输入")

    # Step 2: anomaly type
    step_prompt(2, 5, "异常类型",
                "请选择异常类型:\n"
                "  1. phantom_gene     — 基因名不存在于该论文中\n"
                "  2. indirect_chain   — 实际为间接调控，非直接关系\n"
                "  3. wrong_direction  — 调控方向记录错误\n"
                "  4. other            — 其他类型")
    while True:
        choice = input("请输入序号 (1-4): ").strip()
        if choice in ANOMALY_TYPES:
            anomaly_type, _ = ANOMALY_TYPES[choice]
            break
        print("  ✗ 请输入 1-4 之间的数字")

    # Step 3: TRRUST entry
    step_prompt(3, 5, "原始 TRRUST 条目",
                "请输入有问题的原始 TRRUST 条目:\n"
                "  格式: TF->Target (Direction)\n"
                "  例如: HNF4G->AFP (Activation)")
    while True:
        trrust_entry = input("> ").strip()
        ok, msg = validate_trrust_entry(trrust_entry)
        if ok:
            break
        print(f"  ✗ {msg}，请重新输入")

    # Step 4: issue description
    type_hints = {
        "phantom_gene": '  提示: 为什么这个基因名不存在？实际上它是什么？',
        "indirect_chain": "  提示: 真正的直接调控关系是什么？中间因子是什么？",
        "wrong_direction": "  提示: 正确的方向是什么？依据是什么？",
        "other": "  提示: 请详细描述遇到的问题",
    }
    step_prompt(4, 5, "问题说明",
                "请详细描述问题原因:\n"
                "  可以用中英文混合描述\n"
                f"{type_hints.get(anomaly_type, '')}")
    while True:
        issue = input("> ").strip()
        if issue:
            break
        print("  ✗ 问题说明不能为空，请输入")

    # Step 5: corrected entry (optional)
    step_prompt(5, 5, "修正条目 (可选)",
                "如果知道正确的调控关系，请输入修正后的条目:\n"
                "  格式: TF->Target (Direction)\n"
                "  例如: HNF1A->AFP (Activation)\n"
                "  按 Enter 跳过（表示暂无修正方案）")
    corrected = input("> ").strip() or None

    # Confirm
    entry = {
        "pmid": pmid,
        "anomaly_type": anomaly_type,
        "trrust_entry": trrust_entry,
        "issue": issue,
        "corrected": corrected,
        "curated_date": str(date.today()),
    }
    summary = {
        "PMID": pmid,
        "异常类型": anomaly_type,
        "TRRUST 条目": trrust_entry,
        "问题说明": issue,
        "修正条目": corrected or "(无)",
        "记录日期": entry["curated_date"],
    }
    if not confirm_prompt(summary):
        print("\n已取消。")
        return

    entries.append(entry)
    save_anomalies(entries, path)
    print(f"\n✓ 已添加至 {path} (当前共 {len(entries)} 条记录)")


def cmd_list(entries, path):
    if not entries:
        print("当前没有异常记录。")
        return

    print(f"\n当前共 {len(entries)} 条异常记录:\n")
    for i, entry in enumerate(entries):
        print(f"[{i+1}] PMID {entry['pmid']}  {entry['anomaly_type']}")
        print(f"    {entry['trrust_entry']}")
        print(f"    {entry['issue'][:80]}{'...' if len(entry['issue']) > 80 else ''}")
        print()


def cmd_remove(entries, path):
    if not entries:
        print("当前没有异常记录。")
        return

    cmd_list(entries, path)
    while True:
        choice = input(f"请输入要删除的编号 (1-{len(entries)})，或按 Enter 取消: ").strip()
        if not choice:
            print("已取消。")
            return
        if choice.isdigit() and 1 <= int(choice) <= len(entries):
            idx = int(choice) - 1
            removed = entries.pop(idx)
            save_anomalies(entries, path)
            print(f"✓ 已删除: PMID {removed['pmid']} {removed['trrust_entry']}")
            return
        print(f"  ✗ 请输入 1-{len(entries)} 之间的数字")


def cmd_export(entries, path):
    if not entries:
        print("当前没有异常记录。")
        return

    print("\n按 PMID 分组导出:")
    grouped = {}
    for entry in entries:
        grouped.setdefault(entry["pmid"], []).append(entry)

    for pmid in sorted(grouped.keys(), key=int):
        print(f"\n--- PMID {pmid} ---")
        for e in grouped[pmid]:
            print(f"  类型: {e['anomaly_type']}")
            print(f"  条目: {e['trrust_entry']}")
            print(f"  问题: {e['issue']}")
            if e.get("corrected"):
                print(f"  修正: {e['corrected']}")
            print(f"  日期: {e['curated_date']}")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Bio-LLM 异常标注工具 — 管理 trrust_anomalies.jsonl",
    )
    sub = parser.add_subparsers(dest="command", help="操作命令")

    sub.add_parser("add", help="交互式添加一条异常记录")
    sub.add_parser("list", help="列出所有已记录的异常")
    sub.add_parser("remove", help="按索引删除一条异常记录")
    sub.add_parser("export", help="按 PMID 分组导出异常记录")

    parser.add_argument("--anomalies", default=DEFAULT_ANOMALIES,
                        help="异常记录文件路径")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    path = args.anomalies
    grouped, entries = load_anomalies(path)

    if args.command == "add":
        cmd_add(entries, path)
    elif args.command == "list":
        cmd_list(entries, path)
    elif args.command == "remove":
        cmd_remove(entries, path)
    elif args.command == "export":
        cmd_export(entries, path)


if __name__ == "__main__":
    main()
