#!/usr/bin/env python3
"""重跑 screen_results.json 中失败（relevant=null）的条目。"""

import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed

from llm_client import init_client
from batch_screen import (
    load_config, load_prompt, screen_one, save_checkpoint,
)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="重跑失败的 LLM 筛选条目")
    parser.add_argument("--run-dir", required=True, help="运行目录路径")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    run_dir = args.run_dir
    screen_file = os.path.join(run_dir, "screen_results.json")
    abstracts_file = os.path.join(run_dir, "abstracts.json")

    with open(screen_file) as f:
        screen_results = json.load(f)
    with open(abstracts_file) as f:
        abstracts = json.load(f)

    # 找出失败条目
    failed = [r for r in screen_results if r.get("relevant") is None]
    print(f"失败条目: {len(failed)} 条")
    if not failed:
        print("无需重跑")
        return

    # 加载配置
    cfg = load_config()
    model = cfg.get("model", "qwen3.7-max")
    temperature = cfg.get("temperature", 0)
    seed = cfg.get("seed")
    max_tokens = cfg.get("max_tokens")
    api_key = cfg.get("api_key", "")
    prompt_cfg = load_prompt(os.path.join(PROJECT_ROOT, "prompts/screen_prompt.yaml"))

    print(f"模型: {model}, max_tokens: {max_tokens}")

    # 构建重跑任务
    init_client(api_key=api_key)
    tasks = []
    for r in failed:
        pmid = r["pmid"]
        tf = r["tf"]
        if pmid in abstracts:
            tasks.append((pmid, abstracts[pmid], tf))

    print(f"待重跑: {len(tasks)} 条\n")

    # 重跑
    rerun_results = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(screen_one, pmid, data, tf, prompt_cfg, model, temperature, seed, max_tokens): (pmid, tf)
            for pmid, data, tf in tasks
        }
        for future in as_completed(futures):
            result = future.result()
            rerun_results.append(result)
            status = "✓" if result.get("relevant") is not None else "✗"
            print(f"  [{status}] {result['pmid']} ({result['tf']}): relevant={result.get('relevant')}")

    # 替换失败条目
    failed_keys = {(r["pmid"], r["tf"]) for r in failed}
    screen_results = [r for r in screen_results if (r["pmid"], r["tf"]) not in failed_keys]
    screen_results.extend(rerun_results)

    save_checkpoint(run_dir, "screen_results.json", screen_results)

    # 汇总
    relevant = [r for r in screen_results if r.get("relevant") is True]
    irrelevant = [r for r in screen_results if r.get("relevant") is False]
    unknown = [r for r in screen_results if r.get("relevant") is None]

    print(f"\n重跑完成:")
    print(f"  相关: {len(relevant)}")
    print(f"  不相关: {len(irrelevant)}")
    print(f"  未知/失败: {len(unknown)}")

    # 更新 relevant_pmids.txt
    relevant_pmids = sorted(set(r["pmid"] for r in relevant))
    pmids_file = os.path.join(run_dir, "relevant_pmids.txt")
    with open(pmids_file, "w") as f:
        for pmid in relevant_pmids:
            f.write(f"{pmid}\n")
    print(f"\n已更新 {len(relevant_pmids)} 个相关 PMID → {pmids_file}")


if __name__ == "__main__":
    main()
