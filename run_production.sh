#!/bin/bash
# 生产规模 TF-Target 提取
#
# Usage: ./run_production.sh [limit] [seed]
#   limit: 只处理前 N 篇 (不传则处理全部)
#   seed:  LLM 输出确定性种子 (可选)
#
# 示例:
#   ./run_production.sh          # 全部处理
#   ./run_production.sh 5        # 调试前 5 篇
#   ./run_production.sh 0 42     # 全部处理，seed=42

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUTDIR="outputs/production_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$ROOT/$OUTDIR"

LIMIT="${1:-0}"
SEED="${2:-}"

LIMIT_FLAG=""
if [ "$LIMIT" -gt 0 ] 2>/dev/null; then
  LIMIT_FLAG="--limit $LIMIT"
fi

SEED_FLAG=""
if [ -n "$SEED" ]; then
  SEED_FLAG="--seed $SEED"
fi

conda run --no-capture-output -n bio_llm python scripts/run_production.py \
  --input data/raw/paper_for_produce \
  --output "$OUTDIR/production_results.tsv" \
  --json-output "$OUTDIR/production_results.json" \
  $LIMIT_FLAG \
  $SEED_FLAG \
  --skip-existing

# 生成 HTML 报告
conda run --no-capture-output -n bio_llm python scripts/production_report.py "$OUTDIR"

echo ""
echo "Results: $ROOT/$OUTDIR/production_results.tsv"
echo "JSON:    $ROOT/$OUTDIR/production_results.json"
echo "Report:  $ROOT/$OUTDIR/production_report.html"
