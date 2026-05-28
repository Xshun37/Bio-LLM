#!/bin/bash
# 用法: ./scripts/review_debug.sh [output_dir]
#   output_dir: 包含 analysis_results.json 的目录 (默认: outputs/ 下最新的 merged_* 或时间戳目录)
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# 自动定位最新输出目录
if [ -n "$1" ]; then
  DIR="$1"
else
  # 优先用 merged_* 目录，否则用最新的时间戳目录
  DIR=$(ls -dt "$ROOT"/outputs/merged_* "$ROOT"/outputs/20[0-9]*_* 2>/dev/null | head -1)
  if [ -z "$DIR" ]; then
    echo "错误: 未找到输出目录，请指定: $0 <output_dir>"
    exit 1
  fi
  echo "使用: $DIR"
fi

# 转为相对路径（snakemake 不需要绝对路径）
REL_DIR="${DIR#$ROOT/}"

PYTHONPATH="$ROOT/src" python -m bio_llm.reporting \
  --llm-json "$DIR/analysis_results.json" \
  --text-source fitz \
  --debug-json "$DIR/analysis_results_debug.json" \
  --gold-standard "$ROOT/data/raw/finalresult.tsv" \
  --output "$DIR/report.html"

echo "Report: $DIR/report.html"

if command -v explorer.exe >/dev/null 2>&1; then
  if command -v wslpath >/dev/null 2>&1; then
    explorer.exe "$(wslpath -w "$DIR/report.html")" >/dev/null 2>&1 &
  else
    explorer.exe "$DIR/report.html" >/dev/null 2>&1 &
  fi
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$DIR/report.html" >/dev/null 2>&1 &
fi
