#!/bin/bash
# 用法: ./scripts/rerun_pmids.sh <PMID1,PMID2,...> [output_dir]
# 示例:
#   ./scripts/rerun_pmids.sh 18776923,22479354
#   ./scripts/rerun_pmids.sh 18776923,22479354 my_debug_run

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ -z "$1" ]; then
  echo "用法: $0 <PMID1,PMID2,...> [output_dir]"
  exit 1
fi

PMIDS="$1"
OUTDIR="${2:-outputs/debug_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "$ROOT/$OUTDIR"

echo "重新运行 PMID: $PMIDS"
echo "输出目录: $OUTDIR"

# 读取 config.yaml 中的 model/temperature/workers（可选）
CONFIG="$ROOT/config/config.yaml"
MODEL=$(grep -E '^model:' "$CONFIG" 2>/dev/null | awk '{print $2}' | tr -d '"' || echo "qwen3.7-max-2026-05-20")
TEMP=$(grep -E '^temperature:' "$CONFIG" 2>/dev/null | awk '{print $2}' || echo "0")
WORKERS=$(grep -E '^workers:' "$CONFIG" 2>/dev/null | awk '{print $2}' || echo "4")
SEED=$(grep -E '^seed:' "$CONFIG" 2>/dev/null | awk '{print $2}' || echo "")

SEED_FLAG=""
if [ -n "$SEED" ]; then
  SEED_FLAG="--seed $SEED"
fi

PYTHONPATH="$ROOT/src" conda run --no-capture-output -n bio_llm python -m bio_llm.analysis \
  --gold-standard "$ROOT/data/raw/finalresult.tsv" \
  --text-source fitz \
  --output "$ROOT/$OUTDIR/analysis_results.json" \
  --model "$MODEL" \
  --temperature "$TEMP" \
  --workers "$WORKERS" \
  --pmids "$PMIDS" \
  $SEED_FLAG \
  --debug

PYTHONPATH="$ROOT/src" conda run --no-capture-output -n bio_llm python -m bio_llm.reporting \
  --llm-json "$ROOT/$OUTDIR/analysis_results.json" \
  --debug-json "$ROOT/$OUTDIR/analysis_results_debug.json" \
  --gold-standard "$ROOT/data/raw/finalresult.tsv" \
  --text-source fitz \
  --output "$ROOT/$OUTDIR/report.html"

echo "完成: $ROOT/$OUTDIR/report.html"
