async function runAudit(){
  const btn = document.getElementById('runBtn');
  const status = document.getElementById('status');
  btn.disabled = true; status.textContent = ' 审核中...';
  try{
    const r = await fetch('/api/ai/audit', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({limit:200})});
    const j = await r.json();
    if(j.ok){ status.textContent = ` 完成：处理 ${j.count} 条`; loadResults(); }
    else status.textContent = ' 审核失败';
  }catch(e){ status.textContent = ' 请求出错: '+e; }
  btn.disabled = false;
}

async function loadResults(){
  const r = await fetch('/api/ai/results');
  const j = await r.json();
  const wrap = document.getElementById('results'); wrap.innerHTML='';
  j.forEach(it=>{
    const d = document.createElement('div'); d.className='row';
    const flags = Array.isArray(it.ai_flags)? it.ai_flags.join(',') : it.ai_flags;
    d.innerHTML = `<div><strong>${it.pubmed_id}</strong> TF:${it.tf_input}/${it.tf_standard} Gene:${it.gene_input}/${it.gene_ensg}</div><div class="flags">Flags: ${flags}</div><div>Notes: ${it.ai_notes||''}</div>`;
    wrap.appendChild(d);
  });
}

window.onload = ()=>{ loadResults(); };
