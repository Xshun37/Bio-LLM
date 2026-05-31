// GS Review — sidebar, search tool, mode switching
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
  try { localStorage.setItem("gs_review_v4", JSON.stringify(state)); } catch(e) {}
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
    var raw = localStorage.getItem("gs_review_v4");
    if (raw) {
      var parsed = JSON.parse(raw);
      if (parsed.d) state = parsed;
    }
  } catch(e) {}

  // also try legacy v3 key
  if (Object.keys(state.d).length === 0) {
    try {
      var oldRaw = localStorage.getItem("gs_review_v3");
      if (oldRaw) {
        var old = JSON.parse(oldRaw);
        if (old.d) {
          // migrate old field names to simplified model
          for (var pmid in old.d) {
            var oe = old.d[pmid];
            if (!oe || !oe.p) continue;
            state.d[pmid] = { p: [], n: oe.n || "", r: !!oe.r };
            for (var i = 0; i < oe.p.length; i++) {
              var op = oe.p[i];
              state.d[pmid].p.push({
                tf: op.tf_input || op.f || "",
                gene: op.gene_input || op.t || "",
                direction: op.direction || op.d || "",
                cellline: op.cellline || op.c || "",
                assay: op.assay || [],
                complex: op.complex || ""
              });
            }
          }
        }
      }
    } catch(e) {}
  }

  fetch('/api/gs_review/load').then(function(r) { return r.json(); }).then(function(data) {
    if (!data.states) return;
    var merged = false;
    for (var pmid in data.states) {
      if (!state.d[pmid]) { state.d[pmid] = data.states[pmid]; merged = true; }
      else {
        // Server data takes priority (fixes stale localStorage blocking server data)
        state.d[pmid] = data.states[pmid];
        merged = true;
      }
    }
    if (merged) { saveLocal(); renderSidebar(); updateProgress(); renderCurrent(); }
  });
}

// ====== Sidebar ======
function renderSidebar() {
  var list = document.getElementById('sidebarList');
  var html = '';
  var currentPmid = DataStore.getCurrentPmid();
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
  var allItems = document.querySelectorAll('.sidebar-item');
  allItems.forEach(function(el) { el.classList.remove('active'); });
  if (pmid === DataStore.getCurrentPmid()) item.classList.add('active');
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
        (rp.tf_aliases && rp.tf_aliases.length ? ' <span class="aliases">aka: ' + rp.tf_aliases.map(function(a){return '<span>'+escapeHTML(a)+'</span>'}).join(' ') + '</span>' : '') +
        ' <span class="ref-arrow">&rarr;</span> ' +
        '<span class="ref-dir ' + dirClass + '">' + dirClass + '</span>' +
        ' <span class="ref-arrow">&rarr;</span> ' +
        escapeHTML(rp.target) +
        (rp.target_aliases && rp.target_aliases.length ? ' <span class="aliases">aka: ' + rp.target_aliases.map(function(a){return '<span>'+escapeHTML(a)+'</span>'}).join(' ') + '</span>' : '') +
        '</div>';
    }
    refHTML += '</div>';
  }

  var absHTML = "";
  if (abstract) {
    absHTML = '<div class="abstract-box" id="absBox">' + escapeHTML(abstract) + '</div>';
  }

  var pairsHTML = "";
  for (var j = 0; j < e.p.length; j++) {
    var p = e.p[j];
    var assayArr = p.assay || [];
    if (typeof assayArr === 'string') assayArr = assayArr ? assayArr.split(';') : [];
    var hasRef = assayArr.indexOf('Literature') >= 0;
    var isCofactor = !!p.cofactor;
    pairsHTML += '<div class="pair-block' + (isCofactor ? ' cofactor' : '') + '">' +
      '<div class="pair-top">' +
        '<div class="pair-field"><label>TF</label><input type="text" id="tf_' + j + '" value="' + escapeHTML(p.tf||'') + '" onchange="updatePairField(' + j + ',\'tf\',this.value)"></div>' +
        '<div class="pair-field"><label>Gene</label><input type="text" id="gene_' + j + '" value="' + escapeHTML(p.gene||'') + '" onchange="updatePairField(' + j + ',\'gene\',this.value)"></div>' +
        '<div class="pair-field pair-field-sm"><label>Direction</label>' +
          '<select onchange="updatePairField(' + j + ',\'direction\',this.value)">' +
            '<option value="" ' + ((p.direction||'')===""?"selected":"") + '>--</option>' +
            '<option value="Activation" ' + ((p.direction||'')==="Activation"?"selected":"") + '>Activation</option>' +
            '<option value="Repression" ' + ((p.direction||'')==="Repression"?"selected":"") + '>Repression</option>' +
            '<option value="Unknown" ' + ((p.direction||'')==="Unknown"?"selected":"") + '>Unknown</option>' +
          '</select></div>' +
        '<div class="pair-field"><label>Cell Line</label><input type="text" value="' + escapeHTML(p.cellline||'') + '" onchange="updatePairField(' + j + ',\'cellline\',this.value)"></div>' +
        '<div class="pair-field"><label>Complex</label><input type="text" value="' + escapeHTML(p.complex||'') + '" onchange="updatePairField(' + j + ',\'complex\',this.value)" placeholder="复合体"></div>' +
        '<label class="ref-check"><input type="checkbox" id="cofactor_' + j + '" ' + (isCofactor ? 'checked' : '') + ' onchange="toggleCofactor(' + j + ',this.checked)" title="标记为协同因子，导出时并入Notes"> cofactor</label>' +
        '<label class="ref-check"><input type="checkbox" id="ref_' + j + '" ' + (hasRef ? 'checked' : '') + ' onchange="toggleReference(' + j + ',this.checked)"> citation</label>' +
        '<button class="btn-del" onclick="removePair(' + j + ')">&times;</button>' +
      '</div>' +
      '<div class="pair-assay">' +
        '<label>Assay</label>' +
        '<div id="assay_chips_' + j + '"></div>' +
      '</div>' +
    '</div>';
  }

  var html =
    '<div class="card ' + (e.r ? 'completed' : 'pending') + '" id="pmidCard">' +
      '<div class="card-header">' +
        '<span class="pmid">PMID: ' + pmid + '</span>' +
        '<a href="https://pubmed.ncbi.nlm.nih.gov/' + pmid + '/" target="_blank" rel="noopener">PubMed &#8599;</a>' +
        '<span class="toggle-link" onclick="document.getElementById(\'refBox\').classList.toggle(\'open\')">TRRUST Ref (' + ref.length + ')</span>' +
        (abstract ? '<span class="toggle-link" onclick="document.getElementById(\'absBox\').classList.toggle(\'open\')">Abstract</span>' : '') +
        '<label class="reviewed-label">' +
          '<input type="checkbox" ' + (e.r ? "checked" : "") + ' onchange="toggleReviewed(this.checked)"> Done' +
        '</label>' +
      '</div>' +
      refHTML + absHTML +
      (e.p.length === 0
        ? '<p class="empty-hint">点击 "添加 Pair" 开始标注</p>'
        : pairsHTML) +
      '<button class="btn-add" onclick="addPair()">+ 添加 Pair</button>' +
      '<div class="notes-row">' +
        '<label>Notes</label>' +
        '<textarea id="notesArea" oninput="updateNotes(this.value)">' + escapeHTML(e.n) + '</textarea>' +
      '</div>' +
    '</div>';

  document.getElementById('pmidCardContainer').innerHTML = html;

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

function toggleCofactor(pairIdx, checked) {
  var pmid = DataStore.getCurrentPmid();
  getEntry(pmid).p[pairIdx].cofactor = checked;
  saveState(pmid);
  var block = document.querySelectorAll('.pair-block')[pairIdx];
  if (block) block.classList.toggle('cofactor', checked);
}
window.toggleCofactor = toggleCofactor;

function toggleReference(pairIdx, checked) {
  var pmid = DataStore.getCurrentPmid();
  var assays = getEntry(pmid).p[pairIdx].assay || [];
  if (typeof assays === 'string') assays = assays ? assays.split(';') : [];
  var idx = assays.indexOf('Literature');
  if (checked && idx < 0) assays.push('Literature');
  if (!checked && idx >= 0) assays.splice(idx, 1);
  getEntry(pmid).p[pairIdx].assay = assays;
  saveState(pmid);
  // Sync Literature chip
  syncLiteratureChip(pairIdx, checked);
}
window.toggleReference = toggleReference;

function syncLiteratureChip(pairIdx, checked) {
  var chips = document.querySelectorAll('#assay_chips_' + pairIdx + ' .assay-chip[data-tag="Literature"]');
  chips.forEach(function(ch) {
    var inp = ch.querySelector('input[type="checkbox"]');
    if (inp && inp.checked !== checked) {
      inp.checked = checked;
      ch.classList.toggle('checked', checked);
    }
  });
}

function collectAssaySelections(pairIdx) {
  var pmid = DataStore.getCurrentPmid();
  if (!pmid) return;
  var chips = document.querySelectorAll('.assay-chip[data-pair="' + pairIdx + '"] input[type="checkbox"]:checked');
  var selected = [];
  chips.forEach(function(cb) { selected.push(cb.value); });
  getEntry(pmid).p[pairIdx].assay = selected;
  saveState(pmid);
  // Sync Ref checkbox
  var refCb = document.getElementById('ref_' + pairIdx);
  if (refCb) refCb.checked = selected.indexOf('Literature') >= 0;
}

// ====== Pair management ======
function addPair() {
  var pmid = DataStore.getCurrentPmid();
  var e = getEntry(pmid);
  e.p.push({ tf: "", gene: "", direction: "", cellline: "", assay: [], complex: "", cofactor: false });
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

// ====== Inline Search Panel ======
var _searchOpen = false;

function openSearchTool() {
  _searchOpen = !_searchOpen;
  var panel = document.getElementById('searchPanel');
  if (!panel) return;
  if (_searchOpen) {
    panel.style.display = 'block';
    panel.innerHTML = '<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px">' +
      '<input id="searchToolInput" style="flex:1;padding:7px 10px;border:1px solid #ddd;border-radius:5px;font-size:0.9em" placeholder="输入 TF 或 Gene 名称搜索..." onkeydown="if(event.key===\'Enter\')doSearchTool()">' +
      '<button onclick="doSearchTool()" style="padding:7px 16px;background:#1a73e8;color:#fff;border:none;border-radius:5px;cursor:pointer;font-size:0.9em">Search</button>' +
      '<button onclick="openSearchTool()" style="padding:7px 12px;background:none;border:1px solid #ddd;border-radius:5px;cursor:pointer;font-size:0.9em">&times;</button>' +
    '</div>' +
    '<div id="searchToolResults" style="max-height:300px;overflow-y:auto"></div>';
  } else {
    panel.style.display = 'none';
  }
  document.getElementById('searchBtn').textContent = _searchOpen ? 'Close Search' : 'Search Tool';
}

function doSearchTool() {
  var input = document.getElementById('searchToolInput');
  if (!input) return;
  var q = input.value.trim();
  if (!q) return;
  var resultsDiv = document.getElementById('searchToolResults');
  if (!resultsDiv) return;
  resultsDiv.innerHTML = '<div style="color:#999;text-align:center;padding:16px">搜索中...</div>';

  Promise.all([
    fetch('/api/search_protein?q=' + encodeURIComponent(q)).then(function(r) { return r.json(); }),
    fetch('/api/search_gene?q=' + encodeURIComponent(q)).then(function(r) { return r.json(); })
  ]).then(function(results) {
    var proteins = results[0] || [];
    var genes = results[1] || [];
    var html = '';

    if (proteins.length > 0) {
      html += '<h4 style="margin-bottom:4px;color:#555;font-size:0.85em">UniProt Proteins (' + proteins.length + ')</h4>';
      proteins.forEach(function(d) {
        var name = d.name || d.id || '';
        var gs = (d.genes || []).join(', ');
        html += '<div class="search-candidate" style="padding:8px 10px;margin-bottom:4px">' +
          '<div class="cand-main">' +
            '<div class="cand-name" style="font-size:0.88em">' + escapeHTML(name) + '</div>' +
            '<div class="cand-meta" style="font-size:0.78em"><span>ID: ' + escapeHTML(d.id||'') + '</span><span>Genes: ' + escapeHTML(gs) + '</span></div>' +
          '</div>' +
        '</div>';
      });
    }

    if (genes.length > 0) {
      html += '<h4 style="margin:8px 0 4px;color:#555;font-size:0.85em">MyGene Genes (' + genes.length + ')</h4>';
      genes.forEach(function(d) {
        html += '<div class="search-candidate" style="padding:8px 10px;margin-bottom:4px">' +
          '<div class="cand-main">' +
            '<div class="cand-name" style="font-size:0.88em">' + escapeHTML(d.query_symbol||'') + '</div>' +
            '<div class="cand-meta" style="font-size:0.78em"><span>' + escapeHTML(d.name||'') + '</span><span>ENSG: ' + escapeHTML(d.ensg||'') + '</span></div>' +
          '</div>' +
        '</div>';
      });
    }

    if (!html) html = '<div style="color:#999;text-align:center;padding:16px">无结果</div>';
    resultsDiv.innerHTML = html;
  }).catch(function() {
    resultsDiv.innerHTML = '<div style="color:#c62828;text-align:center;padding:16px">搜索失败</div>';
  });
}

window.openSearchTool = openSearchTool;
window.doSearchTool = doSearchTool;

function closeSearchModal() {
  _searchOpen = false;
  var panel = document.getElementById('searchPanel');
  if (panel) panel.style.display = 'none';
  var btn = document.getElementById('searchBtn');
  if (btn) btn.textContent = 'Search Tool';
}
window.closeSearchModal = closeSearchModal;

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
    renderCurrent();
    updateSidebarItem(oldPmid);
    document.getElementById('mainArea').scrollTo({ top: 0, behavior: 'smooth' });
  }
}
window.jumpTo = jumpTo;

function saveCurrentFromDom() {
  var pmid = DataStore.getCurrentPmid();
  var e = getEntry(pmid);
  for (var i = 0; i < e.p.length; i++) {
    var tfEl = document.getElementById('tf_' + i);
    if (tfEl) e.p[i].tf = tfEl.value;
    var geneEl = document.getElementById('gene_' + i);
    if (geneEl) e.p[i].gene = geneEl.value;
  }
  var notesEl = document.getElementById('notesArea');
  if (notesEl) e.n = notesEl.value;
}

function updateUrl() {
  try { history.replaceState({ pmid: DataStore.getCurrentPmid() }, '', '/gs_review?pmid=' + DataStore.getCurrentPmid()); } catch(e) {}
}

// ====== Export ======
function exportTSV() {
  saveCurrentFromDom(); saveState(DataStore.getCurrentPmid());
  setTimeout(function() { window.open('/api/gs_review/export/tsv', '_blank'); }, 300);
}
window.exportTSV = exportTSV;

// ====== Keyboard ======
document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'Escape') { closeSearchModal(); return; }
  if (e.key === 'j' || e.key === 'n') { e.preventDefault(); navigate(1); }
  else if (e.key === 'k' || e.key === 'p') { e.preventDefault(); navigate(-1); }
  else if (e.key === 'g') { e.preventDefault(); navigateToPmid(DataStore.pmids[0]); }
  else if (e.key === 'Enter') { e.preventDefault(); window.open('https://pubmed.ncbi.nlm.nih.gov/' + DataStore.getCurrentPmid() + '/', '_blank'); }
});

// ====== Popstate ======
window.addEventListener('popstate', function(e) {
  if (e.state && e.state.pmid) { DataStore.setPmid(e.state.pmid); renderCurrent(); }
});

// ====== Init ======
loadState();
var urlParams = new URLSearchParams(window.location.search);
var initPmid = urlParams.get('pmid');
if (initPmid) DataStore.setPmid(initPmid);
renderSidebar();
renderCurrent();

})();
