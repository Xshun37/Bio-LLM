// GS Review — sidebar, search modal, mode switching, annotation form
(function() {

// ====== DataStore ======
var DataStore = {
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

function getStatusClass(pmid) {
  var e = state.d[pmid];
  if (!e) return 'untouched';
  if (e.r) return 'done';
  if (e.p && e.p.length > 0) return 'in-progress';
  return 'untouched';
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
  try {
    var raw = localStorage.getItem("gs_review_v3");
    if (raw) {
      var parsed = JSON.parse(raw);
      if (parsed.d) state = parsed;
    }
  } catch(e) {}

  fetch('/api/gs_review/load').then(function(r) { return r.json(); }).then(function(data) {
    if (!data.states) return;
    var merged = false;
    for (var pmid in data.states) {
      if (!state.d[pmid]) { state.d[pmid] = data.states[pmid]; merged = true; }
    }
    if (merged) { saveLocal(); renderSidebar(); updateProgress(); }
  });
}

// ====== Sidebar ======
function renderSidebar() {
  var list = document.getElementById('sidebarList');
  var html = '';
  var currentPmid = currentMode === 'review' ? DataStore.getCurrentPmid() : null;
  for (var i = 0; i < DataStore.getTotal(); i++) {
    var pmid = DataStore.pmids[i];
    var cls = getStatusClass(pmid);
    var summary = PMID_SUMMARIES[pmid] || {};
    var tfs = (summary.tfs || []).join(', ');
    var targets = (summary.targets || []).join(', ');
    var preview = tfs + ' → ' + targets;
    html += '<div class="sidebar-item' + (pmid === currentPmid ? ' active' : '') + '" data-pmid="' + pmid + '" data-tfs="' + escapeHTML(tfs) + '" data-targets="' + escapeHTML(targets) + '" onclick="navigateToPmid(\'' + pmid + '\')">' +
      '<span class="status-dot ' + cls + '"></span>' +
      '<span class="item-text">' +
        '<span class="item-pmid">' + pmid + '</span>' +
        '<span class="item-preview">' + escapeHTML(preview) + '</span>' +
      '</span>' +
    '</div>';
  }
  list.innerHTML = html;
}

function updateSidebarItem(pmid) {
  var item = document.querySelector('.sidebar-item[data-pmid="' + pmid + '"]');
  if (!item) return;
  var cls = getStatusClass(pmid);
  var dot = item.querySelector('.status-dot');
  if (dot) { dot.className = 'status-dot ' + cls; }
  // update active
  if (currentMode === 'review') {
    var allItems = document.querySelectorAll('.sidebar-item');
    allItems.forEach(function(el) { el.classList.remove('active'); });
    if (pmid === DataStore.getCurrentPmid()) item.classList.add('active');
  }
}

function filterSidebar() {
  var q = document.getElementById('sidebarFilter').value.trim().toLowerCase();
  var items = document.querySelectorAll('.sidebar-item');
  var count = 0;
  items.forEach(function(item) {
    var pmid = item.dataset.pmid || '';
    var tfs = (item.dataset.tfs || '').toLowerCase();
    var targets = (item.dataset.targets || '').toLowerCase();
    var visible = !q || pmid.indexOf(q) >= 0 || tfs.indexOf(q) >= 0 || targets.indexOf(q) >= 0;
    item.style.display = visible ? '' : 'none';
    if (visible) count++;
  });
  document.getElementById('sidebarCount').textContent = count;
}

function escapeHTML(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

window.filterSidebar = filterSidebar;

// ====== Mode Switching ======
var currentMode = 'review';

function switchMode(mode) {
  saveCurrentFromDom();
  closeSearchModal();
  currentMode = mode;
  // update tab buttons
  document.querySelectorAll('.mode-tab').forEach(function(t) {
    t.classList.toggle('active', t.dataset.mode === mode);
  });
  // show/hide bottom bar
  var bottomBar = document.getElementById('bottomBar');
  if (bottomBar) bottomBar.style.display = (mode === 'review') ? '' : 'none';
  // render
  if (mode === 'review') {
    renderCurrent();
  } else {
    renderAnnoForm();
  }
  renderSidebar();
}

window.switchMode = switchMode;

// ====== PMID Card Renderer ======
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
      html += '<input type="checkbox" value="' + escapeHTML(o.tag) + '" ' + (checked ? 'checked' : '') + '> ' + escapeHTML(o.cn);
      html += '</label>';
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

  var absHTML = "";
  if (abstract) {
    absHTML = '<div class="abstract-box" id="absBox">' + escapeHTML(abstract) + '</div>';
  }

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
          '<button type="button" onclick="openSearchModal(\'tf\',' + j + ',\'tf_input_' + j + '\')">搜索</button>' +
        '</div>' +
        '<input type="hidden" id="tf_standard_' + j + '" value="' + escapeHTML(p.tf_standard||'') + '">' +
        '<input type="hidden" id="tf_uniprot_' + j + '" value="' + escapeHTML(p.tf_uniprot||'') + '">' +
        (p.tf_standard ? '<div style="font-size:0.75em;color:#2b8cff">' + escapeHTML(p.tf_standard) + (p.tf_uniprot ? ' / ' + escapeHTML(p.tf_uniprot) : '') + '</div>' : '') +
      '</td>' +
      '<td class="col-gene">' +
        '<div class="search-row">' +
          '<input type="text" id="gene_input_' + j + '" value="' + escapeHTML(p.gene_input||'') + '" onchange="updatePairField(' + j + ',\'gene_input\',this.value)">' +
          '<button type="button" onclick="openSearchModal(\'gene\',' + j + ',\'gene_input_' + j + '\')">搜索</button>' +
        '</div>' +
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

  var html =
    '<div class="card ' + (e.r ? 'completed' : 'pending') + '" id="pmidCard">' +
      '<div class="card-header">' +
        '<span class="pmid">PMID: ' + pmid + '</span>' +
        '<a href="https://pubmed.ncbi.nlm.nih.gov/' + pmid + '/" target="_blank" rel="noopener">PubMed &#8599;</a>' +
        '<span class="toggle-link" onclick="document.getElementById(\'refBox\').classList.toggle(\'open\')">TRRUST Ref (' + ref.length + ')</span>' +
        (abstract ? '<span class="toggle-link" onclick="document.getElementById(\'absBox\').classList.toggle(\'open\')">Abstract</span>' : '') +
        '<span class="toggle-link" onclick="searchAllCurrent()">一键搜索全部</span>' +
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

  document.getElementById('mainContent').innerHTML = html;

  for (var k = 0; k < e.p.length; k++) {
    var aArr = e.p[k].assay || [];
    if (typeof aArr === 'string') aArr = aArr ? aArr.split(';') : [];
    renderAssayChips('assay_chips_' + k, aArr, k);
  }
  attachAssayHandlers();
  updateSidebarItem(pmid);
  updateProgress();
  updateUrl();
}

// ====== Assay chips ======
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
      ch.classList.toggle('checked', inp.checked);
      var pairIdx = parseInt(ch.dataset.pair);
      collectAssaySelections(pairIdx);
    });
  });
}

function collectAssaySelections(pairIdx) {
  var pmid = currentMode === 'review' ? DataStore.getCurrentPmid() : null;
  if (!pmid) return;
  var chips = document.querySelectorAll('.assay-chip[data-pair="' + pairIdx + '"] input[type="checkbox"]:checked');
  var selected = [];
  chips.forEach(function(cb) { selected.push(cb.value); });
  getEntry(pmid).p[pairIdx].assay = selected;
  saveState(pmid);
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
  getEntry(pmid).p[idx][field] = value;
  saveState(pmid);
}
window.updatePairField = updatePairField;

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
  if (card) { card.classList.toggle('completed', checked); card.classList.toggle('pending', !checked); }
  updateSidebarItem(pmid);
  updateProgress();
}
window.toggleReviewed = toggleReviewed;

// ====== Search Modal ======
var _modalState = { type: null, pairIdx: -1, inputId: null };

function openSearchModal(type, pairIdx, inputId) {
  _modalState = { type: type, pairIdx: pairIdx, inputId: inputId };
  var overlay = document.getElementById('searchOverlay');
  var title = document.getElementById('searchModalTitle');
  var body = document.getElementById('searchBody');
  var q = document.getElementById(inputId).value.trim();

  title.textContent = (type === 'tf' ? 'TF Protein Search (UniProt)' : 'Gene ENSG Search (MyGene)');
  body.innerHTML = q ? '<div class="search-loading">Searching...</div>' : '<div class="search-empty">Please enter a search term first.</div>';
  overlay.classList.add('open');

  if (!q) return;

  var url = type === 'tf' ? '/api/search_protein?q=' : '/api/search_gene?q=';
  fetch(url + encodeURIComponent(q))
    .then(function(r) { return r.json(); })
    .then(function(data) { renderSearchResults(data); })
    .catch(function() { body.innerHTML = '<div class="search-empty">Search failed.</div>'; });
}
window.openSearchModal = openSearchModal;

function closeSearchModal() {
  document.getElementById('searchOverlay').classList.remove('open');
  _modalState = { type: null, pairIdx: -1, inputId: null };
}
window.closeSearchModal = closeSearchModal;

function renderSearchResults(data) {
  var body = document.getElementById('searchBody');
  if (!data || data.length === 0) { body.innerHTML = '<div class="search-empty">No results found.</div>'; return; }

  var isTF = _modalState.type === 'tf';
  var html = '';
  data.forEach(function(d, i) {
    if (isTF) {
      var name = d.name || d.id || '';
      var genes = (d.genes || []).join(', ');
      html += '<div class="search-candidate">' +
        '<div class="cand-main">' +
          '<div class="cand-name">' + escapeHTML(name) + '</div>' +
          '<div class="cand-meta">' +
            '<span>UniProt: ' + escapeHTML(d.id||'') + '</span>' +
            '<span>Genes: ' + escapeHTML(genes) + '</span>' +
          '</div>' +
        '</div>' +
        '<button onclick="selectCandidate(' + i + ')">Select</button>' +
      '</div>';
    } else {
      html += '<div class="search-candidate">' +
        '<div class="cand-main">' +
          '<div class="cand-name">' + escapeHTML(d.query_symbol||'') + '</div>' +
          '<div class="cand-meta">' +
            '<span>' + escapeHTML(d.name||'') + '</span>' +
            '<span>ENSG: ' + escapeHTML(d.ensg||'') + '</span>' +
          '</div>' +
        '</div>' +
        '<button onclick="selectCandidate(' + i + ')">Select</button>' +
      '</div>';
    }
  });
  body.innerHTML = html;
  // store results for selectCandidate
  body._results = data;
}

function selectCandidate(idx) {
  var data = document.getElementById('searchBody')._results;
  if (!data || !data[idx]) return;
  var d = data[idx];
  var info = _modalState;
  var inputEl = document.getElementById(info.inputId);
  if (!inputEl) { closeSearchModal(); return; }

  var isTF = info.type === 'tf';
  if (isTF) {
    var name = d.name || d.id || '';
    inputEl.value = name;
    var stdEl = document.getElementById('tf_standard_' + info.pairIdx);
    var uniEl = document.getElementById('tf_uniprot_' + info.pairIdx);
    if (stdEl) stdEl.value = name;
    if (uniEl) uniEl.value = d.id || '';
    if (currentMode === 'review') {
      updatePairField(info.pairIdx, 'tf_input', name);
      updatePairField(info.pairIdx, 'tf_standard', name);
      updatePairField(info.pairIdx, 'tf_uniprot', d.id || '');
    }
  } else {
    var sym = d.query_symbol || d.name || '';
    inputEl.value = sym;
    var ensgEl = document.getElementById('gene_ensg_' + info.pairIdx);
    if (ensgEl) ensgEl.value = d.ensg || '';
    if (currentMode === 'review') {
      updatePairField(info.pairIdx, 'gene_input', sym);
      updatePairField(info.pairIdx, 'gene_ensg', d.ensg || '');
    }
  }
  closeSearchModal();
  if (currentMode === 'review') renderCurrent();
}
window.selectCandidate = selectCandidate;

function searchAllCurrent() {
  if (currentMode !== 'review') return;
  var pmid = DataStore.getCurrentPmid();
  var e = getEntry(pmid);
  for (var i = 0; i < e.p.length; i++) {
    var tfEl = document.getElementById('tf_input_' + i);
    var geneEl = document.getElementById('gene_input_' + i);
    if (tfEl && tfEl.value.trim()) openSearchModal('tf', i, 'tf_input_' + i);
    if (geneEl && geneEl.value.trim()) openSearchModal('gene', i, 'gene_input_' + i);
  }
}
window.searchAllCurrent = searchAllCurrent;

// ====== Annotation Form ======
function renderAnnoForm() {
  var html = '<div class="card anno-form" style="max-width:680px">' +
    '<h3 style="margin-bottom:4px">New Annotation</h3>' +
    '<label><span>PubMed ID</span><input type="text" id="anno_pmid"></label>' +
    '<label><span>TF (Transcription Factor)</span>' +
      '<div class="search-row"><input type="text" id="anno_tf"><button type="button" onclick="openSearchModal(\'tf\',-1,\'anno_tf\')">Search</button></div>' +
    '</label>' +
    '<label><span>Gene (Target)</span>' +
      '<div class="search-row"><input type="text" id="anno_gene"><button type="button" onclick="openSearchModal(\'gene\',-1,\'anno_gene\')">Search</button></div>' +
    '</label>' +
    '<label><span>Cell Line</span><input type="text" id="anno_cellline"></label>' +
    '<label><span>Assay</span><div id="anno_assay_chips"></div></label>' +
    '<label><span>Complex</span><input type="text" id="anno_complex"></label>' +
    '<button class="anno-save-btn" onclick="saveAnnotation()">Save</button>' +
    '<div id="anno_save_msg" style="margin-top:8px;font-size:0.85em"></div>' +
    '<div class="recent-list" id="recentList"></div>' +
  '</div>';

  document.getElementById('mainContent').innerHTML = html;

  // render assay chips for annotation form
  renderAnnoAssayChips([]);
  attachAnnoAssayHandlers();
  loadRecentAnnotations();
}

var _annoAssaySelected = [];

function renderAnnoAssayChips(selected) {
  var html = '<div class="assay-chips-wrap">';
  for (var i = 0; i < ASSAY_CATEGORIES.length; i++) {
    var cat = ASSAY_CATEGORIES[i];
    var opts = GROUPED_ASSAYS[cat];
    html += '<div class="assay-cat">' + escapeHTML(cat) + '</div>';
    for (var j = 0; j < opts.length; j++) {
      var o = opts[j];
      var checked = selected.indexOf(o.tag) >= 0;
      html += '<label class="assay-chip' + (checked ? ' checked' : '') + '" data-tag="' + escapeHTML(o.tag) + '" title="' + escapeHTML(o.en) + '">';
      html += '<input type="checkbox" value="' + escapeHTML(o.tag) + '" ' + (checked ? 'checked' : '') + '> ' + escapeHTML(o.cn);
      html += '</label>';
    }
  }
  html += '</div>';
  var container = document.getElementById('anno_assay_chips');
  if (container) container.innerHTML = html;
}

function attachAnnoAssayHandlers() {
  var chips = document.querySelectorAll('#anno_assay_chips .assay-chip');
  chips.forEach(function(ch) {
    var inp = ch.querySelector('input[type="checkbox"]');
    if (!inp) return;
    ch.addEventListener('click', function(ev) {
      if (ev.target.tagName.toLowerCase() === 'input') return;
      inp.checked = !inp.checked;
      inp.dispatchEvent(new Event('change'));
    });
    inp.addEventListener('change', function() {
      ch.classList.toggle('checked', inp.checked);
      _annoAssaySelected = [];
      var checked = document.querySelectorAll('#anno_assay_chips input[type="checkbox"]:checked');
      checked.forEach(function(cb) { _annoAssaySelected.push(cb.value); });
    });
  });
}

async function saveAnnotation() {
  var tfInput = document.getElementById('anno_tf').value.trim();
  var geneInput = document.getElementById('anno_gene').value.trim();
  var tfStd = document.getElementById('tf_standard_-1');
  var tfUni = document.getElementById('tf_uniprot_-1');
  var geneEns = document.getElementById('gene_ensg_-1');

  var payload = {
    pubmed_id: document.getElementById('anno_pmid').value.trim(),
    tf_input: tfInput,
    tf_standard: tfStd ? tfStd.value : tfInput,
    tf_uniprot: tfUni ? tfUni.value : '',
    gene_input: geneInput,
    gene_ensg: geneEns ? geneEns.value : '',
    cellline: document.getElementById('anno_cellline').value.trim(),
    assay: _annoAssaySelected,
    complex: document.getElementById('anno_complex').value.trim()
  };
  try {
    var r = await fetch('/api/save_annotation', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    var j = await r.json();
    var msg = document.getElementById('anno_save_msg');
    if (j.ok) {
      msg.innerHTML = '<span style="color:#34a853">Saved (id=' + j.id + ')</span>';
      loadRecentAnnotations();
    } else {
      msg.innerHTML = '<span style="color:#c62828">Error: ' + escapeHTML(j.error||'') + '</span>';
    }
  } catch(e) {
    document.getElementById('anno_save_msg').innerHTML = '<span style="color:#c62828">Save failed.</span>';
  }
}

window.saveAnnotation = saveAnnotation;

async function loadRecentAnnotations() {
  try {
    var r = await fetch('/api/annotations');
    var data = await r.json();
    var list = document.getElementById('recentList');
    if (!list) return;
    var html = '<h4>Recent Annotations (' + (Array.isArray(data) ? data.length : 0) + ')</h4>';
    if (Array.isArray(data)) {
      data.slice(0, 20).forEach(function(item) {
        var assayText = Array.isArray(item.assay) ? item.assay.join(', ') : item.assay;
        html += '<div class="recent-item"><strong>' + escapeHTML(item.pubmed_id||'') + '</strong> TF:' + escapeHTML(item.tf_input||'') + '/' + escapeHTML(item.tf_standard||'') + ' ENSG:' + escapeHTML(item.gene_ensg||'') + ' assay:' + escapeHTML(assayText||'') + '</div>';
      });
    }
    list.innerHTML = html;
  } catch(e) {}
}

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
  closeSearchModal();
  if (currentMode !== 'review') return;
  var newIdx = DataStore.currentIdx + delta;
  if (newIdx < 0) newIdx = 0;
  if (newIdx >= DataStore.getTotal()) newIdx = DataStore.getTotal() - 1;
  if (newIdx !== DataStore.currentIdx) {
    saveCurrentFromDom();
    var oldPmid = DataStore.getCurrentPmid();
    DataStore.currentIdx = newIdx;
    renderCurrent();
    updateSidebarItem(oldPmid);
    document.getElementById('mainArea').scrollTo({ top: 0, behavior: 'smooth' });
  }
}
window.navigate = navigate;

function navigateToPmid(pmid) {
  closeSearchModal();
  if (currentMode !== 'review') switchMode('review');
  saveCurrentFromDom();
  var oldPmid = DataStore.getCurrentPmid();
  DataStore.setPmid(pmid);
  if (DataStore.getCurrentPmid() !== oldPmid) {
    renderCurrent();
    updateSidebarItem(oldPmid);
    document.getElementById('mainArea').scrollTo({ top: 0, behavior: 'smooth' });
  }
}
window.navigateToPmid = navigateToPmid;

function jumpTo() {
  var val = document.getElementById('jumpPmid').value.trim();
  if (!val) return;
  var idx = DataStore.pmids.indexOf(val);
  if (idx < 0) {
    for (var i = 0; i < DataStore.pmids.length; i++) {
      if (DataStore.pmids[i].indexOf(val) >= 0) { idx = i; break; }
    }
  }
  if (idx >= 0) {
    closeSearchModal();
    saveCurrentFromDom();
    var oldPmid = DataStore.getCurrentPmid();
    DataStore.currentIdx = idx;
    if (currentMode !== 'review') switchMode('review');
    renderCurrent();
    updateSidebarItem(oldPmid);
    document.getElementById('mainArea').scrollTo({ top: 0, behavior: 'smooth' });
  }
}
window.jumpTo = jumpTo;

function saveCurrentFromDom() {
  if (currentMode !== 'review') return;
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
  try { history.replaceState({ pmid: DataStore.getCurrentPmid() }, '', '/gs_review?pmid=' + DataStore.getCurrentPmid()); } catch(e) {}
}

// ====== Export ======
function exportTSV() {
  if (currentMode === 'review') { saveCurrentFromDom(); saveState(DataStore.getCurrentPmid()); }
  setTimeout(function() { window.open('/api/gs_review/export/tsv', '_blank'); }, 300);
}
window.exportTSV = exportTSV;

// ====== Keyboard ======
document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'Escape') { closeSearchModal(); return; }
  if (currentMode !== 'review') return;
  if (e.key === 'j' || e.key === 'n') { e.preventDefault(); navigate(1); }
  else if (e.key === 'k' || e.key === 'p') { e.preventDefault(); navigate(-1); }
  else if (e.key === 'g') { e.preventDefault(); navigateToPmid(DataStore.pmids[0]); }
  else if (e.key === 'Enter') { e.preventDefault(); window.open('https://pubmed.ncbi.nlm.nih.gov/' + DataStore.getCurrentPmid() + '/', '_blank'); }
});

// ====== Popstate ======
window.addEventListener('popstate', function(e) {
  if (e.state && e.state.pmid) { DataStore.setPmid(e.state.pmid); if (currentMode !== 'review') switchMode('review'); renderCurrent(); }
});

// ====== Init ======
loadState();
var urlParams = new URLSearchParams(window.location.search);
var initPmid = urlParams.get('pmid');
if (initPmid) DataStore.setPmid(initPmid);
renderSidebar();
renderCurrent();

})();
