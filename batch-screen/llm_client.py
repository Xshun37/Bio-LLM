"""独立的 LLM 客户端，供 batch-screen 使用。

从 bio_llm.analysis 提取，不再依赖 bio_llm 包。
"""

import os
import re
import time

from openai import OpenAI, RateLimitError, APIStatusError

_client = None


def init_client(api_key=None):
    """初始化 OpenAI 客户端（阿里云百炼 DashScope）。"""
    global _client
    key = api_key or os.getenv("DASHSCOPE_API_KEY")
    if not key:
        raise ValueError("缺少阿里云百炼 API Key，请设置环境变量 DASHSCOPE_API_KEY 或在 config.yaml 中配置 api_key。")
    _client = OpenAI(api_key=key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")


def _get_client():
    if _client is None:
        init_client()
    return _client


def _call_llm(model, temperature, messages, max_retries=3, seed=None, max_tokens=None):
    """调用阿里云百炼 API，传入 messages 列表，自动处理 429 限流重试。"""
    client = _get_client()
    kwargs = {"model": model, "temperature": temperature, "messages": messages}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    extra = {"enable_thinking": False}
    if seed is not None:
        extra["seed"] = seed
    kwargs["extra_body"] = extra
    print(f"  [_call_llm] temperature={temperature}, seed={seed}")
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(**kwargs)
        except (RateLimitError, APIStatusError) as e:
            if isinstance(e, APIStatusError) and e.status_code != 429:
                raise
            delay = 2 ** attempt
            print(f"  API 限流 (429)，{delay}s 后重试 ({attempt + 1}/{max_retries})...")
            time.sleep(delay)
    return None


def clean_json_text(text):
    """从模型响应中提取有效 JSON 文本。"""
    if not text:
        return text
    text = text.strip()
    code_block = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if code_block:
        text = code_block.group(1).strip()
    text = re.sub(r"//.*?$|#.*?$", "", text, flags=re.MULTILINE)
    text = re.sub(r",\s*([\]}])", r"\1", text)
    # 修复无效 JSON 转义（如 $\epsilon$ 中的 \e）
    text = re.sub(r"\\(?!['\"\\/bfnrtu])", r"\\\\", text)
    text = text.strip()
    # 数组 JSON: 提取最外层 [...]
    if text.startswith("["):
        last = text.rfind("]")
        if last != -1:
            return text[:last + 1]
    # 对象 JSON: 提取最外层 {...}
    if text.startswith("{"):
        last = text.rfind("}")
        if last != -1:
            return text[:last + 1]
    return text
