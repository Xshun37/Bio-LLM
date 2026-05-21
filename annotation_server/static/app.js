async function searchTF(){
  const q = document.getElementById('tf_q').value.trim();
  const wrap = document.getElementById('tf_candidates');
  wrap.innerHTML = '搜索中...';
  const res = await fetch(`/api/search_protein?q=${encodeURIComponent(q)}`);
  const data = await res.json();
  wrap.innerHTML = '';
  if(!data || data.length===0){ wrap.innerHTML='未找到候选项'; return; }
  data.forEach(d=>{
    const el = document.createElement('div'); el.className='candidate';
    const name = d.name || d.query_symbol || d.id || '';
    const meta = document.createElement('div'); meta.className='meta'; meta.innerText = `id: ${d.id||''} genes: ${(d.genes||[]).join(',')}`;
    const left = document.createElement('div'); left.innerHTML = `<strong>${name}</strong>`;
    const right = document.createElement('div');
    const btn = document.createElement('button'); btn.textContent='选择为标准TF';
    btn.onclick = ()=>{
      // set values
      document.getElementById('tf_q').value = name;
      document.getElementById('tf_q').dataset.standard = name;
      document.getElementById('tf_q').dataset.uniprot = d.id||'';
      // highlight this candidate and hide others
      Array.from(wrap.children).forEach(c=>{ if(c===el){ c.classList.add('selected'); c.classList.remove('hidden'); } else { c.classList.add('hidden'); c.classList.remove('selected'); } });
    };
    right.appendChild(btn);
    el.appendChild(left);
    el.appendChild(meta);
    el.appendChild(right);
    wrap.appendChild(el);
  });
}

async function searchGene(){
  const q = document.getElementById('gene_q').value.trim();
  const wrap = document.getElementById('gene_candidates');
  wrap.innerHTML = '搜索中...';
  const res = await fetch(`/api/search_gene?q=${encodeURIComponent(q)}`);
  const data = await res.json();
  wrap.innerHTML = '';
  if(!data || data.length===0){ wrap.innerHTML='未找到ENSG'; return; }
  data.forEach(d=>{
    const el = document.createElement('div'); el.className='candidate';
    const left = document.createElement('div'); left.innerHTML = `<strong>${d.query_symbol||''}</strong> ${d.name||''}`;
    const meta = document.createElement('div'); meta.className='meta'; meta.innerText = `ENSG: ${d.ensg||''}`;
    const right = document.createElement('div');
    const btn = document.createElement('button'); btn.textContent='选择ENSG';
    btn.onclick = ()=>{
      document.getElementById('gene_q').value = d.query_symbol||d.name||'';
      document.getElementById('gene_q').dataset.ensg = d.ensg||'';
      // highlight this candidate and hide others
      Array.from(wrap.children).forEach(c=>{ if(c===el){ c.classList.add('selected'); c.classList.remove('hidden'); } else { c.classList.add('hidden'); c.classList.remove('selected'); } });
    };
    right.appendChild(btn);
    el.appendChild(left);
    el.appendChild(meta);
    el.appendChild(right);
    wrap.appendChild(el);
  });
}

async function save(){
  // collect checked assays as array
  const assayEls = document.querySelectorAll('input[name="assay"]:checked');
  const assays = Array.from(assayEls).map(e=>e.value);

  const payload = {
    pubmed_id: document.getElementById('pubmed_id').value.trim(),
    tf_input: document.getElementById('tf_q').value.trim(),
    tf_standard: document.getElementById('tf_q').dataset.standard||'',
    tf_uniprot: document.getElementById('tf_q').dataset.uniprot||'',
    gene_input: document.getElementById('gene_q').value.trim(),
    gene_ensg: document.getElementById('gene_q').dataset.ensg||'',
    cellline: document.getElementById('cellline').value.trim(),
    assay: assays,
    complex: document.getElementById('complex').value.trim()
  };
  const r = await fetch('/api/save_annotation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const j = await r.json();
  if(j.ok){ alert('保存成功 id='+j.id); loadList(); }
  else alert('保存失败');
}

async function loadList(){
  const r = await fetch('/api/annotations');
  const j = await r.json();
  const wrap = document.getElementById('list'); wrap.innerHTML='';
  j.forEach(item=>{
    const d = document.createElement('div');
    const assayText = Array.isArray(item.assay) ? item.assay.join(',') : item.assay;
    d.innerHTML = `<div><strong>${item.pubmed_id}</strong> TF:${item.tf_input}/${item.tf_standard} ENSG:${item.gene_ensg} assay:${assayText} cell:${item.cellline} <button onclick="deleteAnno(${item.id},this)" style="color:#c62828;border:1px solid #ef9a9a;background:#fff;cursor:pointer;font-size:0.78em;padding:2px 8px;border-radius:3px;margin-left:8px">Del</button></div>`;
    wrap.appendChild(d);
  });
}

async function deleteAnno(id, btn){
  if(!confirm('Delete annotation #'+id+'?')) return;
  const r = await fetch('/api/delete_annotation/'+id, {method:'DELETE'});
  const j = await r.json();
  if(j.ok) btn.closest('div').style.display='none';
}

window.onload = ()=>{ loadList(); };

function initAssayChips(){
  const chips = document.querySelectorAll('.assay-chip');
  chips.forEach(ch=>{
    const inp = ch.querySelector('input[name="assay"]');
    if(!inp) return;
    // set initial state
    if(inp.checked) ch.classList.add('checked'); else ch.classList.remove('checked');
    inp.addEventListener('change', ()=>{
      if(inp.checked) ch.classList.add('checked'); else ch.classList.remove('checked');
    });
    // allow clicking the label to toggle
    ch.addEventListener('click', (e)=>{
      if(e.target.tagName.toLowerCase() === 'input') return; // let native handle
      inp.checked = !inp.checked;
      inp.dispatchEvent(new Event('change'));
    });
  });
}

// enhance window.onload
window.addEventListener('load', ()=>{ loadList(); initAssayChips(); });
