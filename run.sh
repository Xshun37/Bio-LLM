#!/bin/bash
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUTDIR="outputs/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$ROOT/$OUTDIR"
conda run --no-capture-output -n bio_llm snakemake -s "$ROOT/snakefile" -d "$ROOT" -j16 \
  --config sample_size="${1:-48}" output_dir="$OUTDIR"
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
