#!/usr/bin/env python3
"""Generate GS 50 gold standard review HTML.

Usage:  python gs_review/build_review_html.py  [--fetch-abstracts]

Reads:
  - data/raw/GS_50.tsv
  - data/raw/targets_human_track.txt
  - data/curated/gene_ensg_map.json  (run build_ensg_map.py first)
  - config/config.yaml (email, for optional abstract fetch)

Writes:
  - gs_review/review.html
"""

import json
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.join(PROJECT_ROOT, "gs_review")
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

GS_PATH = os.path.join(PROJECT_ROOT, "data", "raw", "GS_50.tsv")
TRRUST_PATH = os.path.join(PROJECT_ROOT, "data", "raw", "trrust_rawdata.human.tsv")
TRACK_PATH = os.path.join(PROJECT_ROOT, "data", "raw", "targets_human_track.txt")
ENSG_MAP_PATH = os.path.join(PROJECT_ROOT, "data", "curated", "gene_ensg_map.json")
ALIAS_INDEX_PATH = os.path.join(PROJECT_ROOT, "data", "curated", "gene_alias_index.json")
OUT_PATH = os.path.join(SCRIPT_DIR, "review.html")


def load_gs_pairs(path):
    """Parse GS_50.tsv -> {pmid: [(tf, target_symbol), ...]}."""
    entries = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            pmid = parts[0].strip()
            tf = parts[1].strip().upper()
            target = parts[2].strip().upper()
            entries.setdefault(pmid, []).append((tf, target))
    return entries


def load_human_track_tfs(path):
    """Extract unique TF names from targets_human_track.txt last column CHIP:TF:cellline."""
    tfs = set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            last_col = line.split("\t")[-1].strip()
            parts = last_col.split(":")
            if len(parts) >= 2 and parts[0] == "CHIP":
                tfs.add(parts[1].strip().upper())
    return tfs


def load_ensg_map(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def resolve_ensg(symbol, ensg_map):
    """Look up ENSG ID for a gene symbol. Returns (ensg_id, status)."""
    sym = symbol.upper()
    if sym in ensg_map:
        return ensg_map[sym], "ok"
    # fallback: normalize and retry
    try:
        from bio_llm.gene_aliases import normalize_gene_name
        norm = normalize_gene_name(sym).upper()
        if norm and norm in ensg_map:
            return ensg_map[norm], "normalized"
    except Exception:
        pass
    return "NOT_FOUND", "missing"


def cross_ref_tf(tf_symbol, human_track_tfs):
    """Check TF against human_track. Returns (tf_name, in_track)."""
    sym = tf_symbol.upper()
    if sym in human_track_tfs:
        return sym, True
    return sym, False


def load_trrust(path):
    """Load TRRUST raw data -> {pmid: {(tf, target): direction, ...}}.

    TRRUST PMID column is semicolon-delimited (e.g. '10211993;11556732').
    """
    trrust = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            parts = line.strip().split("\t")
            if len(parts) < 4:
                continue
            tf = parts[0].strip().upper()
            target = parts[1].strip().upper()
            direction = parts[2].strip()
            for pmid in parts[3].split(";"):
                pmid = pmid.strip()
                if pmid:
                    trrust.setdefault(pmid, {})[(tf, target)] = direction
    return trrust


def load_alias_reverse(path):
    """Build reverse alias index: {official_symbol: [alias1, alias2, ...]}."""
    index = load_ensg_map(path)  # reuse JSON loader
    reverse = {}
    for alias_key, candidates in index.get("aliases", {}).items():
        for c in candidates:
            sym = c["symbol"].strip().upper()
            reverse.setdefault(sym, set()).add(alias_key.upper())
    # Remove self-references
    for sym in reverse:
        reverse[sym].discard(sym)
    return {k: sorted(v) for k, v in reverse.items()}


def fetch_abstracts_if_needed(pmids, should_fetch):
    """Fetch PubMed abstracts. Returns {pmid: text} or empty dict."""
    if not should_fetch:
        return {}
    try:
        from bio_llm.abstracts import fetch_abstracts
        # read email from config
        config_path = os.path.join(PROJECT_ROOT, "config", "config.yaml")
        email = "user@example.com"
        try:
            with open(config_path) as f:
                for line in f:
                    m = re.search(r'email:\s*"([^"]+)"', line)
                    if m:
                        email = m.group(1)
                        break
        except Exception:
            pass
        print(f"Fetching abstracts for {len(pmids)} PMIDs (email={email})...")
        return fetch_abstracts(list(pmids), email)
    except ImportError:
        print("BioPython not available, skipping abstract fetch")
        return {}
    except Exception as exc:
        print(f"Abstract fetch failed: {exc}")
        return {}


def build(pmids, entries, abstracts):
    """Assemble the data structure for embedding in HTML."""
    data = {"pmids": pmids, "entries": {}}
    for pmid in pmids:
        pair_dicts = entries.get(pmid, [])
        abstract = abstracts.get(pmid, "")
        pair_list = []
        for p in pair_dicts:
            pair_list.append({
                "tf": p["tf"],
                "gene": p["gene"],
                "ensg": p["ensg"],
                "in_track": p["in_track"],
                "tf_aliases": p.get("tf_aliases", []),
                "gene_aliases": p.get("gene_aliases", []),
                "trrust_direction": p.get("trrust_direction", ""),
            })
        data["entries"][pmid] = {
            "abstract": abstract,
            "pairs": pair_list,
        }
    return data


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GS 50 Gold Standard Review</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, -apple-system, sans-serif; background: #f5f5f5; color: #333; }
  .toolbar {
    position: sticky; top: 0; z-index: 100; background: #fff; border-bottom: 1px solid #ddd;
    padding: 12px 24px; display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
  }
  .toolbar .title { font-size: 1.1em; font-weight: 600; margin-right: auto; }
  .toolbar button, .toolbar select {
    padding: 6px 14px; border: 1px solid #ccc; border-radius: 6px; background: #fff;
    cursor: pointer; font-size: 0.9em;
  }
  .toolbar button:hover { background: #e9e9e9; }
  .toolbar button.export-btn { background: #1a73e8; color: #fff; border-color: #1a73e8; }
  .toolbar button.export-btn:hover { background: #1557b0; }
  .progress { font-size: 0.9em; color: #666; }
  .container { max-width: 1000px; margin: 0 auto; padding: 20px 24px; }
  .card {
    background: #fff; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    margin-bottom: 20px; padding: 20px 24px; transition: box-shadow 0.2s;
  }
  .card.hidden { display: none; }
  .card.completed { border-left: 4px solid #34a853; }
  .card.pending { border-left: 4px solid #ea4335; }
  .card-header { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
  .card-header .pmid { font-weight: 700; font-size: 1.05em; }
  .card-header a { color: #1a73e8; text-decoration: none; font-size: 0.9em; }
  .card-header a:hover { text-decoration: underline; }
  .card-header .reviewed-label { margin-left: auto; display: flex; align-items: center; gap: 6px;
    font-size: 0.85em; color: #666; cursor: pointer; user-select: none; }
  .pairs-section { margin-bottom: 12px; }
  .pairs-section .header { font-size: 0.85em; color: #888; margin-bottom: 4px; }
  .pair-row {
    display: flex; gap: 16px; flex-wrap: wrap; align-items: center;
    padding: 8px 12px; background: #f8f9fa; border-radius: 6px; margin-bottom: 6px;
  }
  .pair-row .tf-name { font-weight: 600; color: #1a73e8; min-width: 80px; }
  .pair-row .arrow { color: #999; }
  .pair-row .target-name { min-width: 80px; }
  .pair-row .ensg { font-family: monospace; font-size: 0.85em; color: #666; }
  .pair-row .ensg.missing { color: #ea4335; }
  .pair-row .tf-warn { font-size: 0.8em; color: #f9ab00; }
  .aliases { font-size: 0.78em; color: #999; margin-top: 2px; }
  .aliases span { font-family: monospace; background: #eee; padding: 1px 5px; border-radius: 3px; margin-right: 3px; }
  .trrust-dir { font-size: 0.8em; padding: 2px 8px; border-radius: 4px; font-weight: 600; }
  .trrust-dir.Activation { background: #e8f5e9; color: #2e7d32; }
  .trrust-dir.Repression { background: #fce4ec; color: #c62828; }
  .trrust-dir.Unknown { background: #fff3e0; color: #e65100; }
  .fields { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
  .fields.full-width { grid-template-columns: 1fr; }
  .field label { display: block; font-size: 0.85em; color: #666; margin-bottom: 4px; }
  .field input, .field select {
    width: 100%; padding: 8px 10px; border: 1px solid #ddd; border-radius: 6px;
    font-size: 0.95em;
  }
  .field input:focus, .field select:focus { outline: none; border-color: #1a73e8; }
  .abstract-box {
    margin-top: 12px; max-height: 200px; overflow-y: auto; padding: 12px;
    background: #fafafa; border: 1px solid #e0e0e0; border-radius: 6px;
    font-size: 0.85em; line-height: 1.5; white-space: pre-wrap; display: none;
  }
  .abstract-box.open { display: block; }
  .abstract-toggle { font-size: 0.85em; color: #1a73e8; cursor: pointer; margin-top: 8px; }
  .kbd-hint {
    position: fixed; bottom: 16px; right: 24px; font-size: 0.8em; color: #aaa;
    background: #fff; padding: 6px 12px; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  }
</style>
</head>
<body>
<div class="toolbar">
  <span class="title">GS 50 Gold Standard Review</span>
  <span class="progress" id="progress">0/50</span>
  <select id="filter" onchange="applyFilter()">
    <option value="all">All</option>
    <option value="pending">Pending</option>
    <option value="done">Done</option>
  </select>
  <button onclick="importTSV()">Import progress from TSV</button>
  <button class="export-btn" onclick="exportTSV()">Export TSV</button>
</div>
<div class="container" id="container"></div>
<div class="kbd-hint">j/k navigate &middot; Enter open PubMed &middot; g top</div>

<script>
const REVIEW_DATA = __REVIEW_DATA__;

const LS_KEY = "gs50_review_state";
const FIELDS = ["direction", "cellline", "assay", "notes"];

function loadState() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function saveState(state) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(state)); } catch {}
}

let state = loadState();

function getReviewed(pmid) {
  const s = state[pmid] || {};
  return {
    direction: s.direction || "",
    cellline: s.cellline || "",
    assay: s.assay || "",
    notes: s.notes || "",
    reviewed: !!s.reviewed,
    in_track: s.in_track,
  };
}

function saveField(pmid, field, value) {
  if (!state[pmid]) state[pmid] = {};
  state[pmid][field] = value;
  // auto-mark reviewed only when filling in data fields (not when toggling checkbox directly)
  if (field !== "reviewed") {
    const s = state[pmid];
    s.reviewed = !!(s.direction && s.cellline && s.assay);
  }
  saveState(state);
  updateProgress();
  updateCardStatus(pmid);
}

function updateCardStatus(pmid) {
  const s = getReviewed(pmid);
  const card = document.getElementById("card-" + pmid);
  if (!card) return;
  card.classList.toggle("completed", s.reviewed);
  card.classList.toggle("pending", !s.reviewed);
}

function updateProgress() {
  let done = 0;
  for (const pmid of REVIEW_DATA.pmids) {
    const s = state[pmid] || {};
    if (s.reviewed) done++;
  }
  document.getElementById("progress").textContent = done + "/" + REVIEW_DATA.pmids.length;
}

function applyFilter() {
  const filter = document.getElementById("filter").value;
  for (const pmid of REVIEW_DATA.pmids) {
    const card = document.getElementById("card-" + pmid);
    if (!card) continue;
    const s = getReviewed(pmid);
    if (filter === "done") card.classList.toggle("hidden", !s.reviewed);
    else if (filter === "pending") card.classList.toggle("hidden", s.reviewed);
    else card.classList.remove("hidden");
  }
}

function exportTSV() {
  const lines = [];
  for (const pmid of REVIEW_DATA.pmids) {
    const entry = REVIEW_DATA.entries[pmid];
    const s = state[pmid] || {};
    const cellline = s.cellline || "";
    const assay = s.assay || "";
    const direction = s.direction || "";
    for (const pair of entry.pairs) {
      lines.push([pmid, pair.tf, pair.ensg, direction, cellline, assay].join("\t"));
    }
  }
  const blob = new Blob([lines.join("\n") + "\n"], {type: "text/tab-separated-values"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "gs50_review_export.tsv";
  a.click();
  URL.revokeObjectURL(url);
}

function importTSV() {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".tsv,.txt";
  input.onchange = function() { if (this.files[0]) importFromTSV(this.files[0]); };
  input.click();
}

function importFromTSV(file) {
  const reader = new FileReader();
  reader.onload = function(e) {
    const lines = e.target.result.split("\n").filter(l => l.trim());
    const newState = {};
    for (const line of lines) {
      const parts = line.split("\t");
      if (parts.length < 6) continue;
      const pmid = parts[0].trim();
      // const tf = parts[1];  // not needed for import
      // const ensg = parts[2];  // not needed for import
      const direction = parts[3].trim();
      const cellline = parts[4].trim();
      const assay = parts[5].trim();
      if (!newState[pmid] || !newState[pmid].direction) {
        newState[pmid] = {direction, cellline, assay, notes: "", reviewed: true};
      }
    }
    state = newState;
    saveState(state);
    renderCards();  // re-render to sync inputs
    updateProgress();
    alert("Imported " + Object.keys(newState).length + " PMIDs");
  };
  reader.readAsText(file);
}

function renderCards() {
  const container = document.getElementById("container");
  container.innerHTML = "";

  for (const pmid of REVIEW_DATA.pmids) {
    const entry = REVIEW_DATA.entries[pmid];
    const s = getReviewed(pmid);
    const card = document.createElement("div");
    card.className = "card " + (s.reviewed ? "completed" : "pending");
    card.id = "card-" + pmid;

    let pairsHTML = "";
    for (const pair of entry.pairs) {
      const ensg = pair.ensg;
      const ensgClass = ensg === "NOT_FOUND" ? "missing" : "";
      const tfWarn = pair.in_track ? "" :
        '<span class="tf-warn" title="TF not found in human_track">&#9888; not in track</span>';
      const tfAliases = pair.tf_aliases && pair.tf_aliases.length
        ? '<div class="aliases">aka: ' + pair.tf_aliases.map(a => '<span>' + a + '</span>').join(' ') + '</div>' : '';
      const geneAliases = pair.gene_aliases && pair.gene_aliases.length
        ? '<div class="aliases">aka: ' + pair.gene_aliases.map(a => '<span>' + a + '</span>').join(' ') + '</div>' : '';
      const trrustDir = pair.trrust_direction
        ? '<span class="trrust-dir ' + pair.trrust_direction + '">' + pair.trrust_direction + '</span>' : '';
      pairsHTML +=
        '<div class="pair-row">' +
          '<div style="min-width:80px"><span class="tf-name">' + pair.tf + '</span>' + tfWarn + tfAliases + '</div>' +
          '<span class="arrow">&rarr;</span>' + trrustDir + '<span class="arrow">&rarr;</span>' +
          '<div style="min-width:80px"><span class="target-name">' + pair.gene + '</span>' +
          '<span class="ensg ' + ensgClass + '">' + ensg + '</span>' + geneAliases + '</div>' +
        '</div>';
    }

    let abstractHTML = "";
    if (entry.abstract) {
      const absText = typeof entry.abstract === "string"
        ? entry.abstract
        : Object.entries(entry.abstract).map(([k,v]) => "[" + k + "] " + v).join("\n\n");
      abstractHTML =
        '<span class="abstract-toggle" onclick="this.nextElementSibling.classList.toggle(\'open\')">Show/Hide Abstract</span>' +
        '<div class="abstract-box">' + absText.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;") + '</div>';
    }

    card.innerHTML =
      '<div class="card-header">' +
        '<span class="pmid">PMID: ' + pmid + '</span>' +
        '<a href="https://pubmed.ncbi.nlm.nih.gov/' + pmid + '/" target="_blank" rel="noopener">Open PubMed &#8599;</a>' +
        '<label class="reviewed-label">' +
          '<input type="checkbox" ' + (s.reviewed ? "checked" : "") +
          ' onchange="saveField(\'' + pmid + '\', \'reviewed\', this.checked)"> Done' +
        '</label>' +
      '</div>' +
      '<div class="pairs-section">' +
        '<div class="header">TRRUST reference (' + entry.pairs.length + ' pair' + (entry.pairs.length > 1 ? "s" : "") + ')</div>' +
        pairsHTML +
      '</div>' +
      '<div class="fields">' +
        '<div class="field">' +
          '<label>Direction</label>' +
          '<select value="' + s.direction + '" onchange="saveField(\'' + pmid + '\', \'direction\', this.value)">' +
            '<option value="" ' + (s.direction === "" ? "selected" : "") + '>-- select --</option>' +
            '<option value="Activation" ' + (s.direction === "Activation" ? "selected" : "") + '>Activation</option>' +
            '<option value="Repression" ' + (s.direction === "Repression" ? "selected" : "") + '>Repression</option>' +
            '<option value="Unknown" ' + (s.direction === "Unknown" ? "selected" : "") + '>Unknown</option>' +
          '</select>' +
        '</div>' +
        '<div class="field">' +
          '<label>Cell Line</label>' +
          '<input type="text" value="' + s.cellline.replace(/"/g, "&quot;") + '"' +
          ' oninput="saveField(\'' + pmid + '\', \'cellline\', this.value)" placeholder="e.g. HeLa, MCF-7, K562">' +
        '</div>' +
        '<div class="field">' +
          '<label>Assay</label>' +
          '<input type="text" value="' + s.assay.replace(/"/g, "&quot;") + '"' +
          ' oninput="saveField(\'' + pmid + '\', \'assay\', this.value)" placeholder="e.g. ChIP-seq, luciferase, EMSA">' +
        '</div>' +
        '<div class="field">' +
          '<label>Notes</label>' +
          '<input type="text" value="' + s.notes.replace(/"/g, "&quot;") + '"' +
          ' oninput="saveField(\'' + pmid + '\', \'notes\', this.value)" placeholder="Optional notes">' +
        '</div>' +
      '</div>' +
      abstractHTML;

    container.appendChild(card);
  }
  updateProgress();
}

// Keyboard navigation
let currentIdx = 0;
document.addEventListener("keydown", function(e) {
  // don't intercept when typing in inputs
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || e.target.tagName === "TEXTAREA") return;

  if (e.key === "j" || e.key === "n") {
    e.preventDefault();
    currentIdx = Math.min(currentIdx + 1, REVIEW_DATA.pmids.length - 1);
    document.getElementById("card-" + REVIEW_DATA.pmids[currentIdx])?.scrollIntoView({behavior: "smooth", block: "start"});
  } else if (e.key === "k" || e.key === "p") {
    e.preventDefault();
    currentIdx = Math.max(currentIdx - 1, 0);
    document.getElementById("card-" + REVIEW_DATA.pmids[currentIdx])?.scrollIntoView({behavior: "smooth", block: "start"});
  } else if (e.key === "g") {
    e.preventDefault();
    currentIdx = 0;
    window.scrollTo({top: 0, behavior: "smooth"});
  } else if (e.key === "Enter") {
    e.preventDefault();
    window.open("https://pubmed.ncbi.nlm.nih.gov/" + REVIEW_DATA.pmids[currentIdx] + "/", "_blank");
  }
});

renderCards();
</script>
</body>
</html>"""


def generate_html(data):
    json_data = json.dumps(data, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__REVIEW_DATA__", json_data)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate GS 50 review HTML")
    parser.add_argument("--fetch-abstracts", action="store_true",
                        help="Fetch PubMed abstracts and include in the HTML")
    args = parser.parse_args()

    # Load data
    print("Loading GS_50 pairs...")
    entries = load_gs_pairs(GS_PATH)
    pmids = sorted(entries.keys())
    print(f"  {len(pmids)} PMIDs, {sum(len(v) for v in entries.values())} pairs")

    print("Loading human_track TFs...")
    track_tfs = load_human_track_tfs(TRACK_PATH)
    print(f"  {len(track_tfs)} unique TFs from ENCODE ChIP-seq tracks")

    print("Loading ENSG map...")
    ensg_map = load_ensg_map(ENSG_MAP_PATH)
    print(f"  {len(ensg_map)} mappings loaded")

    print("Loading alias reverse index...")
    alias_reverse = load_alias_reverse(ALIAS_INDEX_PATH)
    print(f"  {len(alias_reverse)} symbols with aliases")

    print("Loading TRRUST reference directions...")
    trrust = load_trrust(TRRUST_PATH)
    print(f"  {len(trrust)} PMIDs with TRRUST records")

    # Process each pair
    tf_matched = 0
    tf_missing = 0
    ensg_ok = 0
    ensg_missing = 0

    for pmid in pmids:
        for i, (tf, target) in enumerate(entries[pmid]):
            # TF cross-reference
            resolved_tf, in_track = cross_ref_tf(tf, track_tfs)
            if in_track:
                tf_matched += 1
            else:
                tf_missing += 1

            # ENSG resolution
            ensg_id, ensg_status = resolve_ensg(target, ensg_map)
            if ensg_status != "missing":
                ensg_ok += 1
            else:
                ensg_missing += 1

            # Aliases
            tf_aliases = alias_reverse.get(resolved_tf, [])
            gene_aliases = alias_reverse.get(target, [])

            # TRRUST direction
            trrust_direction = trrust.get(pmid, {}).get((resolved_tf, target), "")

            entries[pmid][i] = {
                "tf": resolved_tf,
                "gene": target,
                "ensg": ensg_id,
                "in_track": in_track,
                "ensg_status": ensg_status,
                "tf_aliases": tf_aliases,
                "gene_aliases": gene_aliases,
                "trrust_direction": trrust_direction,
            }

    print(f"\nTF cross-ref: {tf_matched} matched in human_track, {tf_missing} not found")
    print(f"ENSG: {ensg_ok} resolved, {ensg_missing} NOT_FOUND")

    # Optional abstract fetch
    abstracts = fetch_abstracts_if_needed(pmids, args.fetch_abstracts)
    if abstracts:
        print(f"  Fetched {len(abstracts)} abstracts")

    # Build data and generate HTML
    data = build(pmids, entries, abstracts)
    html = generate_html(data)

    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        handle.write(html)
    print(f"\nReview page written to: {OUT_PATH}")
    print("Open it in your browser:  gs_review/review.html")


if __name__ == "__main__":
    main()
