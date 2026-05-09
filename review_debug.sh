#!/bin/bash
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

PYTHONPATH="$ROOT/src" python -m bio_llm.reporting \
  --llm-json "$ROOT/outputs/analysis_results.json" \
  --abstracts "$ROOT/data/interim/abstracts_for_test.txt" \
  --debug-json "$ROOT/outputs/analysis_results_debug.json" \
  --output "$ROOT/outputs/report.html"

echo "Report: $ROOT/outputs/report.html"

if command -v explorer.exe >/dev/null 2>&1; then
  if command -v wslpath >/dev/null 2>&1; then
    explorer.exe "$(wslpath -w "$ROOT/outputs/report.html")" >/dev/null 2>&1 &
  else
    explorer.exe "$ROOT/outputs/report.html" >/dev/null 2>&1 &
  fi
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$ROOT/outputs/report.html" >/dev/null 2>&1 &
fi
