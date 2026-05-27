#!/bin/bash
# Usage: ./run.sh [sample_size] [pmid_seed]
#   sample_size: PMID 数量 (默认 48 = 全量)
#   pmid_seed:   PMID 随机抽取种子 (可选，不传则按顺序)
#   示例:
#     ./run.sh        # 全量 48 篇，按顺序
#     ./run.sh 5      # 随机 5 篇，按顺序
#     ./run.sh 5 42   # 随机 5 篇，pmid_seed=42

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUTDIR="outputs/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$ROOT/$OUTDIR"

SAMPLE_SIZE="${1:-48}"
PMID_SEED="${2:-}"

if [ -n "$PMID_SEED" ]; then
  conda run --no-capture-output -n bio_llm snakemake -s "$ROOT/snakefile" -d "$ROOT" -j16 \
    --config sample_size="$SAMPLE_SIZE" output_dir="$OUTDIR" pmid_seed="$PMID_SEED"
else
  conda run --no-capture-output -n bio_llm snakemake -s "$ROOT/snakefile" -d "$ROOT" -j16 \
    --config sample_size="$SAMPLE_SIZE" output_dir="$OUTDIR"
fi

echo "Report generated: $ROOT/$OUTDIR/report.html"
echo "LLM results: $ROOT/$OUTDIR/analysis_results.json"

# Open report in browser
if command -v explorer.exe >/dev/null 2>&1; then
  if command -v wslpath >/dev/null 2>&1; then
    explorer.exe "$(wslpath -w "$ROOT/$OUTDIR/report.html")" >/dev/null 2>&1 &
  else
    explorer.exe "$ROOT/$OUTDIR/report.html" >/dev/null 2>&1 &
  fi
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$ROOT/$OUTDIR/report.html" >/dev/null 2>&1 &
fi
