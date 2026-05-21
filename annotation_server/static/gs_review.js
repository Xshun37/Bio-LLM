// GS Review — client-side SPA, one PMID per page
(function() {

// ====== DataStore ======
const DataStore = {
  pmids: GS_DATA.pmids,
  refPairs: GS_DATA.ref_pairs,
  abstracts: GS_DATA.abstracts,
  currentIdx: 0,

  getCurrentPmid: function() { return this.pmids[this.currentIdx]; },
  getTotal: function() { return this.pmids.length; },

  setPmid: function(pmid) {
    var idx = this.pmids.indexOf(pmid);
    if (idx >= 0) this.currentIdx = idx;
  }
};

// ====== StateManager ======
var state = { d: {} };

function getEntry(pmid) {
  if (!state.d[pmid]) state.d[pmid] = { p: [], n: "", r: false };
  return state.d[pmid];
}

function saveLocal() {
  try { localStorage.setItem("gs_review_v3", JSON.stringify(state)); } catch(e) {}
}

var _saveTimer = null;
function scheduleServerSave(pmid) {
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(function() {
    var s = state.d[pmid];
    if (!s) return;
    fetch('/api/gs_review/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pubmed_id: pmid, state: s })
    });
  }, 800);
}

function saveState(pmid) {
  saveLocal();
  scheduleServerSave(pmid);
}

function loadState() {
  // 1. try localStorage
  try {
    var raw = localStorage.getItem("gs_review_v3");
    if (raw) {
      var parsed = JSON.parse(raw);
      if (parsed.d) state = parsed;
    }
  } catch(e) {}

  // 2. fetch server state
  fetch('/api/gs_review/load').then(function(r) { return r.json(); }).then(function(data) {
    if (!data.states) return;
    var merged = false;
    for (var pmid in data.states) {
      if (!state.d[pmid]) {
        state.d[pmid] = data.states[pmid];
        merged = true;
      }
    }
    if (merged) {
      saveLocal();
      renderCurrent();
      updateProgress();
    }
  });
}

// ====== Renderer ======
function escapeHTML(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function renderAssayChips(containerId, selectedAssays, pairIdx) {
  var html = '<div class="assay-chips-wrap">';
  for (var i = 0; i < ASSAY_CATEGORIES.length; i++) {
    var cat = ASSAY_CATEGORIES[i];
    var opts = GROUPED_ASSAYS[cat];
    html += '<div class="assay-cat">' + escapeHTML(cat) + '</div>';
    for (var j = 0; j < opts.length; j++) {
      var o = opts[j];
      var checked = selectedAssays.indexOf(o.tag) >= 0;
      html += '<label class="assay-chip' + (checked ? ' checked' : '') + '" data-pair="' + pairIdx + '" data-tag="' + escapeHTML(o.tag) + '" title="' + escapeHTML(o.en) + '">';
      html += '<input type="checkbox" value="' + escapeHTML(o.tag) + '" ' + (checked ? 'checked' : '') + ' onchange="void(0)">';
      html += escapeHTML(o.cn) + ' (' + escapeHTML(o.tag) + ')</label>';
    }
  }
  html += '</div>';
  document.getElementById(containerId).innerHTML = html;
}

function renderCurrent() {
  var pmid = DataStore.getCurrentPmid();
  var e = getEntry(pmid);
  var ref = DataStore.refPairs[pmid] || [];
  var abstract = DataStore.abstracts[pmid] || "";

  // TRRUST reference HTML
  var refHTML = "";
  if (ref.length) {
    refHTML = '<div class="ref-box" id="refBox">' +
      '<div class="ref-header">TRRUST Reference (' + ref.length + ' pair' + (ref.length>1?'s':'') + ')</div>';
    for (var i = 0; i < ref.length; i++) {
      var rp = ref[i];
      var dirClass = rp.direction || "Unknown";
      refHTML += '<div class="ref-pair">' +
        '<span class="ref-tf">' + escapeHTML(rp.tf) + '</span>' +
        (rp.tf_aliases && rp.tf_aliases.length ? '<div class="aliases">aka: ' + rp.tf_aliases.map(function(a){return '<span>'+escapeHTML(a)+'</span>'}).join(' ') + '</div>' : '') +
        ' <span class="ref-arrow">&rarr;</span> ' +
        '<span class="ref-dir ' + dirClass + '">' + dirClass + '</span>' +
        ' <span class="ref-arrow">&rarr;</span> ' +
        escapeHTML(rp.target) +
        (rp.target_aliases && rp.target_aliases.length ? '<div class="aliases">aka: ' + rp.target_aliases.map(function(a){return '<span>'+escapeHTML(a)+'</span>'}).join(' ') + '</div>' : '') +
        '</div>';
    }
    refHTML += '</div>';
  }

  // Abstract
  var absHTML = "";
  if (abstract) {
    absHTML = '<div class="abstract-box" id="absBox">' + escapeHTML(abstract) + '</div>';
  }

  // Pairs table
  var rowsHTML = "";
  for (var j = 0; j < e.p.length; j++) {
    var p = e.p[j];
    var assayArr = p.assay || [];
    if (typeof assayArr === 'string') assayArr = assayArr ? assayArr.split(';') : [];
    var assayDisplay = Array.isArray(assayArr) ? assayArr.join(', ') : '';
    rowsHTML += '<tr>' +
      '<td class="col-tf">' +
        '<div class="search-row">' +
          '<input type="text" id="tf_input_' + j + '" value="' + escapeHTML(p.tf_input||'') + '" onchange="updatePairField(' + j + ',\'tf_input\',this.value)">' +
          '<button type="button" onclick="searchTF(' + j + ')">搜索</button>' +
        '</div>' +
        '<div class="candidates" id="tf_candidates_' + j + '"></div>' +
        '<input type="hidden" id="tf_standard_' + j + '" value="' + escapeHTML(p.tf_standard||'') + '">' +
        '<input type="hidden" id="tf_uniprot_' + j + '" value="' + escapeHTML(p.tf_uniprot||'') + '">' +
        (p.tf_standard ? '<div style="font-size:0.75em;color:#2b8cff">' + escapeHTML(p.tf_standard) + (p.tf_uniprot ? ' / ' + escapeHTML(p.tf_uniprot) : '') + '</div>' : '') +
      '</td>' +
      '<td class="col-gene">' +
        '<div class="search-row">' +
          '<input type="text" id="gene_input_' + j + '" value="' + escapeHTML(p.gene_input||'') + '" onchange="updatePairField(' + j + ',\'gene_input\',this.value)">' +
          '<button type="button" onclick="searchGene(' + j + ')">搜索</button>' +
        '</div>' +
        '<div class="candidates" id="gene_candidates_' + j + '"></div>' +
        '<input type="hidden" id="gene_ensg_' + j + '" value="' + escapeHTML(p.gene_ensg||'') + '">' +
        (p.gene_ensg ? '<div style="font-size:0.75em;color:#2b8cff">' + escapeHTML(p.gene_ensg) + '</div>' : '') +
      '</td>' +
      '<td class="col-dir">' +
        '<select onchange="updatePairField(' + j + ',\'direction\',this.value)">' +
          '<option value="" ' + ((p.direction||'')===""?"selected":"") + '>--</option>' +
          '<option value="Activation" ' + ((p.direction||'')==="Activation"?"selected":"") + '>Activation</option>' +
          '<option value="Repression" ' + ((p.direction||'')==="Repression"?"selected":"") + '>Repression</option>' +
          '<option value="Unknown" ' + ((p.direction||'')==="Unknown"?"selected":"") + '>Unknown</option>' +
        '</select>' +
      '</td>' +
      '<td class="col-cellline">' +
        '<input type="text" value="' + escapeHTML(p.cellline||'') + '" onchange="updatePairField(' + j + ',\'cellline\',this.value)">' +
      '</td>' +
      '<td class="col-assay">' +
        '<div id="assay_chips_' + j + '"></div>' +
        '<div style="font-size:0.75em;color:#888">已选: ' + (assayDisplay || '无') + '</div>' +
      '</td>' +
      '<td class="col-complex">' +
        '<input type="text" value="' + escapeHTML(p.complex||'') + '" onchange="updatePairField(' + j + ',\'complex\',this.value)" placeholder="复合体">' +
      '</td>' +
      '<td class="col-del"><button class="btn-del" onclick="removePair(' + j + ')">&times;</button></td>' +
      '</tr>';
  }

  var mainHTML =
    '<div class="card ' + (e.r ? 'completed' : 'pending') + '" id="pmidCard">' +
      '<div class="card-header">' +
        '<span class="pmid">PMID: ' + pmid + '</span>' +
        '<a href="https://pubmed.ncbi.nlm.nih.gov/' + pmid + '/" target="_blank" rel="noopener">PubMed &#8599;</a>' +
        '<span class="toggle-link" onclick="document.getElementById(\'refBox\').classList.toggle(\'open\')">TRRUST Ref (' + ref.length + ')</span>' +
        (abstract ? '<span class="toggle-link" onclick="document.getElementById(\'absBox\').classList.toggle(\'open\')">Abstract</span>' : '') +
        '<span class="toggle-link" onclick="toggleAllSearch()">展开搜索</span>' +
        '<label class="reviewed-label">' +
          '<input type="checkbox" ' + (e.r ? "checked" : "") + ' onchange="toggleReviewed(this.checked)"> Done' +
        '</label>' +
      '</div>' +
      refHTML + absHTML +
      (e.p.length === 0
        ? '<p class="empty-hint">点击 "添加 Pair" 开始标注</p>'
        : '<table class="pairs-table"><thead><tr>' +
            '<th class="col-tf">TF</th>' +
            '<th class="col-gene">Gene</th>' +
            '<th class="col-dir">Direction</th>' +
            '<th class="col-cellline">Cell Line</th>' +
            '<th class="col-assay">Assay</th>' +
            '<th class="col-complex">Complex</th>' +
            '<th class="col-del"></th>' +
          '</tr></thead><tbody>' + rowsHTML + '</tbody></table>') +
      '<button class="btn-add" onclick="addPair()">+ 添加 Pair</button>' +
      '<div class="notes-row">' +
        '<label>Notes</label>' +
        '<textarea id="notesArea" oninput="updateNotes(this.value)">' + escapeHTML(e.n) + '</textarea>' +
      '</div>' +
    '</div>';

  document.getElementById('mainContent').innerHTML = mainHTML;

  // Render assay chips for each pair
  for (var k = 0; k < e.p.length; k++) {
    var aArr = e.p[k].assay || [];
    if (typeof aArr === 'string') aArr = aArr ? aArr.split(';') : [];
    renderAssayChips('assay_chips_' + k, aArr, k);
  }

  // Attach assay chip click handlers
  attachAssayHandlers();

  updateProgress();
  updateUrl();
}

// ====== Assay chip interaction ======
function attachAssayHandlers() {
  var chips = document.querySelectorAll('.assay-chip');
  chips.forEach(function(ch) {
    var inp = ch.querySelector('input[type="checkbox"]');
    if (!inp) return;
    ch.addEventListener('click', function(ev) {
      if (ev.target.tagName.toLowerCase() === 'input') return;
      inp.checked = !inp.checked;
      inp.dispatchEvent(new Event('change'));
    });
    inp.addEventListener('change', function() {
      if (inp.checked) ch.classList.add('checked'); else ch.classList.remove('checked');
      var pairIdx = parseInt(ch.dataset.pair);
      collectAssaySelections(pairIdx);
    });
  });
}

function collectAssaySelections(pairIdx) {
  var pmid = DataStore.getCurrentPmid();
  var chips = document.querySelectorAll('.assay-chip[data-pair="' + pairIdx + '"] input[type="checkbox"]:checked');
  var selected = [];
  chips.forEach(function(cb) { selected.push(cb.value); });
  var e = getEntry(pmid);
  e.p[pairIdx].assay = selected;
  saveState(pmid);
  // update display text
  var td = document.querySelectorAll('.col-assay')[pairIdx];
  if (td) {
    var div = td.querySelector('div:last-child');
    if (div) div.textContent = '已选: ' + (selected.length ? selected.join(', ') : '无');
  }
}

// ====== Pair management ======
function addPair() {
  var pmid = DataStore.getCurrentPmid();
  var e = getEntry(pmid);
  e.p.push({ tf_input: "", tf_standard: "", tf_uniprot: "", gene_input: "", gene_ensg: "",
             direction: "", cellline: "", assay: [], complex: "" });
  saveState(pmid);
  renderCurrent();
}

window.addPair = addPair;

function removePair(idx) {
  var pmid = DataStore.getCurrentPmid();
  var e = getEntry(pmid);
  e.p.splice(idx, 1);
  saveState(pmid);
  renderCurrent();
}

window.removePair = removePair;

function updatePairField(idx, field, value) {
  var pmid = DataStore.getCurrentPmid();
  var e = getEntry(pmid);
  e.p[idx][field] = value;
  saveState(pmid);
}

window.updatePairField = updatePairField;

// ====== Search ======
var _allSearchOpen = false;

function toggleAllSearch() {
  _allSearchOpen = !_allSearchOpen;
  var pmid = DataStore.getCurrentPmid();
  var e = getEntry(pmid);
  for (var i = 0; i < e.p.length; i++) {
    var tfInput = document.getElementById('tf_input_' + i);
    var geneInput = document.getElementById('gene_input_' + i);
    if (_allSearchOpen) {
      if (tfInput && tfInput.value.trim()) searchTF(i);
      if (geneInput && geneInput.value.trim()) searchGene(i);
    }
  }
}

window.toggleAllSearch = toggleAllSearch;

async function searchTF(pairIdx) {
  var q = document.getElementById('tf_input_' + pairIdx).value.trim();
  if (!q) return;
  var wrap = document.getElementById('tf_candidates_' + pairIdx);
  wrap.innerHTML = '搜索中...';
  try {
    var res = await fetch('/api/search_protein?q=' + encodeURIComponent(q));
    var data = await res.json();
    wrap.innerHTML = '';
    if (!data || data.length === 0) { wrap.innerHTML = '无结果'; return; }
    data.forEach(function(d) {
      var el = document.createElement('div'); el.className = 'candidate';
      var name = d.name || d.id || '';
      var genes = (d.genes || []).join(', ');
      el.innerHTML = '<div><strong>' + escapeHTML(name) + '</strong><div class="meta">ID: ' + escapeHTML(d.id||'') + ' | Genes: ' + escapeHTML(genes) + '</div></div>';
      var btn = document.createElement('button');
      btn.textContent = '选择';
      btn.onclick = function() {
        document.getElementById('tf_input_' + pairIdx).value = name;
        document.getElementById('tf_standard_' + pairIdx).value = name;
        document.getElementById('tf_uniprot_' + pairIdx).value = d.id || '';
        updatePairField(pairIdx, 'tf_input', name);
        updatePairField(pairIdx, 'tf_standard', name);
        updatePairField(pairIdx, 'tf_uniprot', d.id || '');
        // highlight selected
        Array.from(wrap.children).forEach(function(c) {
          if (c === el) { c.classList.add('selected'); } else { c.classList.add('hidden'); }
        });
        // re-render to show the standardized name below input
        renderCurrent();
      };
      el.appendChild(btn);
      wrap.appendChild(el);
    });
  } catch(e) { wrap.innerHTML = '搜索失败'; }
}

window.searchTF = searchTF;

async function searchGene(pairIdx) {
  var q = document.getElementById('gene_input_' + pairIdx).value.trim();
  if (!q) return;
  var wrap = document.getElementById('gene_candidates_' + pairIdx);
  wrap.innerHTML = '搜索中...';
  try {
    var res = await fetch('/api/search_gene?q=' + encodeURIComponent(q));
    var data = await res.json();
    wrap.innerHTML = '';
    if (!data || data.length === 0) { wrap.innerHTML = '无结果'; return; }
    data.forEach(function(d) {
      var el = document.createElement('div'); el.className = 'candidate';
      el.innerHTML = '<div><strong>' + escapeHTML(d.query_symbol||'') + '</strong> ' + escapeHTML(d.name||'') + '<div class="meta">ENSG: ' + escapeHTML(d.ensg||'') + '</div></div>';
      var btn = document.createElement('button');
      btn.textContent = '选择';
      btn.onclick = function() {
        document.getElementById('gene_input_' + pairIdx).value = d.query_symbol || d.name || '';
        document.getElementById('gene_ensg_' + pairIdx).value = d.ensg || '';
        updatePairField(pairIdx, 'gene_input', d.query_symbol || d.name || '');
        updatePairField(pairIdx, 'gene_ensg', d.ensg || '');
        Array.from(wrap.children).forEach(function(c) {
          if (c === el) { c.classList.add('selected'); } else { c.classList.add('hidden'); }
        });
        renderCurrent();
      };
      el.appendChild(btn);
      wrap.appendChild(el);
    });
  } catch(e) { wrap.innerHTML = '搜索失败'; }
}

window.searchGene = searchGene;

// ====== Notes & Reviewed ======
function updateNotes(value) {
  var pmid = DataStore.getCurrentPmid();
  getEntry(pmid).n = value;
  saveState(pmid);
}

window.updateNotes = updateNotes;

function toggleReviewed(checked) {
  var pmid = DataStore.getCurrentPmid();
  getEntry(pmid).r = checked;
  saveState(pmid);
  var card = document.getElementById('pmidCard');
  if (card) {
    card.classList.toggle('completed', checked);
    card.classList.toggle('pending', !checked);
  }
  updateProgress();
}

window.toggleReviewed = toggleReviewed;

// ====== Progress ======
function updateProgress() {
  var done = 0;
  for (var i = 0; i < DataStore.getTotal(); i++) {
    if (getEntry(DataStore.pmids[i]).r) done++;
  }
  document.getElementById('progress').textContent = done + '/' + DataStore.getTotal();
}

// ====== Pagination ======
function navigate(delta) {
  var newIdx = DataStore.currentIdx + delta;
  if (newIdx < 0) newIdx = 0;
  if (newIdx >= DataStore.getTotal()) newIdx = DataStore.getTotal() - 1;
  if (newIdx !== DataStore.currentIdx) {
    // save current pmid state from DOM before navigating
    saveCurrentFromDom();
    DataStore.currentIdx = newIdx;
    renderCurrent();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
}

window.navigate = navigate;

function jumpTo() {
  var val = document.getElementById('jumpPmid').value.trim();
  if (!val) return;
  var idx = DataStore.pmids.indexOf(val);
  if (idx < 0) {
    // try partial match
    for (var i = 0; i < DataStore.pmids.length; i++) {
      if (DataStore.pmids[i].indexOf(val) >= 0) { idx = i; break; }
    }
  }
  if (idx >= 0) {
    saveCurrentFromDom();
    DataStore.currentIdx = idx;
    renderCurrent();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } else {
    alert('PMID 未找到: ' + val);
  }
}

window.jumpTo = jumpTo;

function saveCurrentFromDom() {
  // Sync any in-progress text edits back to state before navigating
  var pmid = DataStore.getCurrentPmid();
  var e = getEntry(pmid);
  for (var i = 0; i < e.p.length; i++) {
    var tfEl = document.getElementById('tf_input_' + i);
    if (tfEl) e.p[i].tf_input = tfEl.value;
    var geneEl = document.getElementById('gene_input_' + i);
    if (geneEl) e.p[i].gene_input = geneEl.value;
  }
  var notesEl = document.getElementById('notesArea');
  if (notesEl) e.n = notesEl.value;
}

function updateUrl() {
  var pmid = DataStore.getCurrentPmid();
  try {
    history.replaceState({ pmid: pmid }, '', '/gs_review?pmid=' + pmid);
  } catch(e) {}
}

// ====== Export ======
function exportTSV() {
  saveCurrentFromDom();
  var pmid = DataStore.getCurrentPmid();
  saveState(pmid);
  // Force server save then download
  setTimeout(function() {
    window.open('/api/gs_review/export/tsv', '_blank');
  }, 300);
}

window.exportTSV = exportTSV;

// ====== Keyboard ======
document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'j' || e.key === 'n') {
    e.preventDefault(); navigate(1);
  } else if (e.key === 'k' || e.key === 'p') {
    e.preventDefault(); navigate(-1);
  } else if (e.key === 'g') {
    e.preventDefault();
    saveCurrentFromDom();
    DataStore.currentIdx = 0;
    renderCurrent();
    window.scrollTo({ top: 0 });
  } else if (e.key === 'Enter') {
    e.preventDefault();
    window.open('https://pubmed.ncbi.nlm.nih.gov/' + DataStore.getCurrentPmid() + '/', '_blank');
  }
});

// ====== Popstate for back/forward ======
window.addEventListener('popstate', function(e) {
  if (e.state && e.state.pmid) {
    DataStore.setPmid(e.state.pmid);
    renderCurrent();
  }
});

// ====== Init ======
loadState();

// Parse ?pmid= from URL
var urlParams = new URLSearchParams(window.location.search);
var initPmid = urlParams.get('pmid');
if (initPmid) DataStore.setPmid(initPmid);

renderCurrent();

})();
