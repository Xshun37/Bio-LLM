import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI, RateLimitError, APIStatusError

try:
    from tqdm import tqdm as _tqdm
except ImportError:
    _tqdm = None
from bio_llm import normalize_tf as _norm_tf, normalize_target as _norm_target
from bio_llm.evaluation import normalize_and_log

DEFAULT_INPUT = "data/interim/abstracts_for_test.txt"
DEFAULT_OUTPUT = "outputs/analysis_results.json"
DEFAULT_MODEL = "qwen3.7-max-2026-05-20"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_PROMPT_DIR = os.path.join(PROJECT_ROOT, "config", "prompts")

_client = None


def init_client(api_key=None):
    global _client
    key = api_key or os.getenv("DASHSCOPE_API_KEY")
    if not key:
        raise ValueError("缺少阿里云百炼 API Key，请设置环境变量 DASHSCOPE_API_KEY 或使用 --api-key 参数。")
    _client = OpenAI(api_key=key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")


def _get_client():
    if _client is None:
        init_client()
    return _client


def load_prompt(filename, prompt_dir=None):
    """Load a prompt template from config/prompts/."""
    prompt_dir = prompt_dir or DEFAULT_PROMPT_DIR
    path = os.path.join(prompt_dir, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Prompt file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def parse_test_file(file_path):
    """Parse PMID blocks, structured abstracts, and gold standard entries."""
    if not os.path.exists(file_path):
        print(f"错误: 找不到输入文件 {file_path}")
        return []

    with open(file_path, "r", encoding="utf-8") as handle:
        content = handle.read()

    blocks = re.split(r"={10,}", content)
    tasks = []
    for block in blocks:
        pmid_match = re.search(r"PMID:\s*(\d+)", block)
        if not pmid_match:
            continue

        # Match either "Full Text:" or "Abstract:" marker
        abstract_match = re.search(
            r"(?:Full Text|Abstract):\s*-{3,}\s*(.*?)(?:\n(?=={10,})|\Z)",
            block,
            re.DOTALL,
        )
        if not abstract_match:
            continue

        pmid = pmid_match.group(1).strip()
        raw_abstract = abstract_match.group(1).strip()

        # Parse gold standard entries
        gs_entries = []
        for gs_match in re.finditer(
            r"Gold Standard:\s*(\S+)\s*->\s*(\S+)"
            r"(?:\s*\[Assay:\s*([^\]]*)\])?"
            r"(?:\s*\[CellLine:\s*([^\]]*)\])?",
            block,
        ):
            gs_entries.append({
                "tf": gs_match.group(1).strip(),
                "target": gs_match.group(2).strip(),
                "assay": (gs_match.group(3) or "").strip(),
                "cellLine": (gs_match.group(4) or "").strip(),
            })

        sections = {}
        if raw_abstract.startswith("["):
            segments = re.split(r"\n---+\n?", raw_abstract)
            for segment in segments:
                label_match = re.match(r"\[\[?([^\]\[]+)\]\]?\s*\n(.*)", segment, re.DOTALL)
                if label_match:
                    sections[label_match.group(1).strip()] = label_match.group(2).strip()
            abstract_text = (
                "\n\n".join(f"[{label}]\n{text}" for label, text in sections.items())
                if sections
                else raw_abstract
            )
        else:
            abstract_text = raw_abstract

        tasks.append({
            "pmid": pmid,
            "abstract": abstract_text,
            "sections": sections,
            "gold_standard": gs_entries,
        })

    return tasks


def clean_json_text(text):
    """Extract valid JSON text from a model response."""
    if not text:
        return text

    text = text.strip()
    code_block = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if code_block:
        text = code_block.group(1).strip()

    bracket_match = re.search(r"(\[.*\])", text, re.DOTALL)
    if bracket_match:
        text = bracket_match.group(1)

    text = re.sub(r"//.*?$|#.*?$", "", text, flags=re.MULTILINE)
    text = re.sub(r",\s*([\]}])", r"\1", text)
    text = text.strip()

    if text.startswith("[") and text.endswith("]"):
        return text

    first = text.find("[")
    last = text.rfind("]")
    if first != -1 and last != -1 and first < last:
        return text[first:last + 1]

    return text


def extract_model_content(response):
    """Extract message content from OpenAI-compatible response."""
    try:
        return response.choices[0].message.content
    except Exception:
        return str(response)


def extract_reasoning_content(response):
    """Extract reasoning content from thinking mode (if available)."""
    try:
        msg = response.choices[0].message
        return getattr(msg, "reasoning_content", "") or ""
    except Exception:
        return ""


def _extract_usage(resp):
    """Safely extract token usage and request_id from a chat completion."""
    usage = getattr(resp, "usage", None)
    return {
        "request_id": getattr(resp, "id", ""),
        "input_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
        "output_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
    }


def _call_llm(model, temperature, prompt=None, messages=None, max_retries=3):
    """Call Qwen API via DashScope with exponential backoff on 429 rate-limit."""
    client = _get_client()
    for attempt in range(max_retries):
        kwargs = {"model": model, "temperature": temperature}
        if messages is not None:
            kwargs["messages"] = messages
        else:
            kwargs["messages"] = [{"role": "user", "content": prompt}]

        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError:
            delay = 2 ** attempt
            print(f"  API 限流 (429)，{delay}s 后重试 (attempt {attempt + 1}/{max_retries})...")
            time.sleep(delay)
        except APIStatusError as e:
            if e.status_code == 429:
                delay = 2 ** attempt
                print(f"  API 限流 (429)，{delay}s 后重试 (attempt {attempt + 1}/{max_retries})...")
                time.sleep(delay)
                continue
            raise
    return None


def analyze_tf_interaction(
    abstract_text,
    model_name=DEFAULT_MODEL,
    temperature=0,
    debug=False,
    round1_prompt=None,
    round2_prompt=None,
):
    # Load prompts from files or use overrides
    if round1_prompt is None:
        round1_template = load_prompt("round1.txt")
    else:
        round1_template = round1_prompt
    if round2_prompt is None:
        round2_template = load_prompt("round2.txt")
    else:
        round2_template = round2_prompt

    round1_user = round1_template.replace("{abstract_text}", abstract_text)
    round2_user = round2_template  # Round 2 has no abstract placeholder

    resp1 = _call_llm(model_name, temperature, prompt=round1_user)
    if resp1 is None:
        err_msg = "Round1_API_Error: rate_limit_exhausted"
        if debug:
            return {"error": err_msg}
        return {"error": err_msg}

    analysis = extract_model_content(resp1)

    resp2 = _call_llm(model_name, temperature, messages=[
        {"role": "user", "content": round1_user},
        {"role": "assistant", "content": analysis},
        {"role": "user", "content": round2_user},
    ])
    if resp2 is None:
        err_msg = "Round2_API_Error: rate_limit_exhausted"
        if debug:
            return {
                "error": err_msg,
                "round1_analysis": analysis,
                "round1_reasoning": extract_reasoning_content(resp1),
                "round1_usage": _extract_usage(resp1),
                "round2_usage": _extract_usage(resp2),
            }
        return {"error": err_msg, "analysis": analysis}

    content = extract_model_content(resp2)
    clean = clean_json_text(content)
    try:
        parsed = json.loads(clean)
        # Post-process: normalize gene names through synonym maps
        norm_log = []
        if isinstance(parsed, list):
            for entry in parsed:
                if isinstance(entry, dict):
                    if "TF" in entry:
                        entry["TF"] = normalize_and_log(
                            entry["TF"], _norm_tf, "TF", norm_log)
                    if "Target" in entry:
                        entry["Target"] = normalize_and_log(
                            entry["Target"], _norm_target, "Target", norm_log)
    except json.JSONDecodeError as exc:
        print(f"JSON 解析失败。错误: {exc}")
        if debug:
            return {
                "error": "parse_fail",
                "round1_analysis": analysis,
                "round1_reasoning": extract_reasoning_content(resp1),
                "round2_raw": content,
                "round2_clean": clean,
                "round2_reasoning": extract_reasoning_content(resp2),
                "round1_usage": _extract_usage(resp1),
                "round2_usage": _extract_usage(resp2),
            }
        return {"error": "parse_fail", "content": content, "analysis": analysis}

    if debug:
        return {
            "result": parsed,
            "round1_analysis": analysis,
            "round1_reasoning": extract_reasoning_content(resp1),
            "round1_usage": _extract_usage(resp1),
            "round2_raw": content,
            "round2_clean": clean,
            "round2_reasoning": extract_reasoning_content(resp2),
            "round2_usage": _extract_usage(resp2),
            "normalization_log": norm_log,
        }
    if norm_log:
        return {"result": parsed, "normalization_log": norm_log}
    return parsed


def run_analysis(input_path, output_path, model_name, temperature=0, workers=1, debug=False):
    tasks = parse_test_file(input_path)
    if not tasks:
        print("未发现待处理任务。")
        return

    results = {}
    debug_info = {}
    worker_count = max(1, min(workers, len(tasks)))
    print(f"开始分析 {len(tasks)} 条摘要 (并行 workers={worker_count})...")

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(
                analyze_tf_interaction,
                task["abstract"],
                model_name=model_name,
                temperature=temperature,
                debug=debug,
            ): task["pmid"]
            for task in tasks
        }
        pbar = _tqdm(total=len(tasks), desc="LLM 分析", unit="PMID",
                      disable=_tqdm is None) if True else None
        for future in as_completed(future_map):
            pmid = future_map[future]
            try:
                raw_result = future.result()
                if isinstance(raw_result, dict) and "result" in raw_result:
                    if "round1_analysis" in raw_result:
                        debug_info[pmid] = raw_result
                    elif "normalization_log" in raw_result:
                        debug_info.setdefault(pmid, {}).update(raw_result)
                    results[pmid] = raw_result["result"]
                elif debug and isinstance(raw_result, dict) and "round1_analysis" in raw_result:
                    debug_info[pmid] = raw_result
                    results[pmid] = raw_result.get("result", raw_result)
                else:
                    results[pmid] = raw_result
                count = len(results[pmid]) if isinstance(results[pmid], list) else 0
                if pbar:
                    pbar.set_postfix_str(f"PMID {pmid} → {count}条", refresh=True)
                else:
                    print(f"PMID {pmid}: {count} relationships")
            except Exception as exc:
                if pbar:
                    pbar.set_postfix_str(f"PMID {pmid} ✗ {exc}", refresh=True)
                else:
                    print(f"PMID {pmid}: ERROR - {exc}")
                results[pmid] = {"error": str(exc)}
            if pbar:
                pbar.update(1)
        if pbar:
            pbar.close()

    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=4)

    if debug and debug_info:
        debug_path = output_path.replace(".json", "_debug.json")
        with open(debug_path, "w", encoding="utf-8") as handle:
            json.dump(debug_info, handle, ensure_ascii=False, indent=4)
        print(f"Debug info saved to: {debug_path}")

    print(f"分析完成！结果已存至: {output_path}")


def test_single(abstract_text, model_name=DEFAULT_MODEL, temperature=0,
                round1_prompt=None, round2_prompt=None):
    """Run analyze_tf_interaction in debug mode and pretty-print all outputs.

    Useful for iterating on prompt design with a single abstract.
    """
    result = analyze_tf_interaction(
        abstract_text, model_name=model_name, temperature=temperature, debug=True,
        round1_prompt=round1_prompt, round2_prompt=round2_prompt,
    )

    print("=" * 60)
    print("ROUND 1 — Free-text Analysis")
    print("=" * 60)
    print(result.get("round1_analysis", "(not available)"))
    if "round1_usage" in result:
        u = result["round1_usage"]
        print(f"\n[Round 1 tokens: {u['input_tokens']} in, {u['output_tokens']} out"
              f" | request: {u['request_id']}]")

    print("\n" + "=" * 60)
    print("ROUND 2 — Raw Output (before cleaning)")
    print("=" * 60)
    print(result.get("round2_raw", "(not available)"))
    if "round2_usage" in result:
        u = result["round2_usage"]
        print(f"\n[Round 2 tokens: {u['input_tokens']} in, {u['output_tokens']} out"
              f" | request: {u['request_id']}]")

    print("\n" + "=" * 60)
    print("ROUND 2 — Cleaned JSON")
    print("=" * 60)
    print(result.get("round2_clean", "(not available)"))

    if "error" in result:
        print("\n" + "=" * 60)
        print(f"ERROR: {result['error']}")
    elif "result" in result:
        print("\n" + "=" * 60)
        print("FINAL PARSED RESULT:")
        print(json.dumps(result["result"], indent=2, ensure_ascii=False))

    return result


def build_parser():
    parser = argparse.ArgumentParser(description="从 PubMed 摘要提取 TF-Target 关系并保存 JSON 结果。")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="输入摘要文件路径")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="输出 JSON 文件路径")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="阿里云百炼 Qwen 模型名称")
    parser.add_argument("--api-key", default=None, help="阿里云百炼 API Key")
    parser.add_argument("--temperature", type=float, default=0, help="LLM temperature")
    parser.add_argument("--workers", type=int, default=1, help="并行 worker 数量")
    parser.add_argument("--debug", action="store_true", default=False,
                        help="Save intermediate LLM outputs and token usage to *_debug.json")
    parser.add_argument("--test-abstract", default=None,
                        help="Test a single abstract interactively (for prompt iteration)")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        init_client(args.api_key)
    except ValueError as exc:
        print(exc)
        sys.exit(1)

    if args.test_abstract:
        test_single(args.test_abstract, model_name=args.model, temperature=args.temperature)
        return

    run_analysis(
        input_path=args.input,
        output_path=args.output,
        model_name=args.model,
        temperature=args.temperature,
        workers=args.workers,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()

