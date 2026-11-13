const $ = id => document.getElementById(id);
let active = null, result = null, timer = null;
const statuses = {queued:'等待执行',running:'正在探索',completed:'探索完成',failed:'查询失败'};
async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}
function el(tag, text, cls) { const node = document.createElement(tag); if(text !== undefined) node.textContent = text; if(cls) node.className=cls; return node; }
async function history() {
  const jobs = await api('/api/jobs'); $('history').replaceChildren();
  if(!jobs.length) $('history').textContent = '还没有查询记录';
  jobs.forEach(job => { const button=el('button',job.query.paper);button.append(el('small',`${statuses[job.status]} · ${new Date(job.created).toLocaleString()}`));button.onclick=()=>select(job.id);$('history').append(button); });
}
async function select(id) {
  clearTimeout(timer); active=id; result=null; $('papers').replaceChildren(); $('stats').replaceChildren(); $('warnings').textContent=''; $('provenance').hidden=true; $('downloads').replaceChildren();
  $('empty').hidden=true;$('activity').hidden=false;$('filter').value=''; await poll();
}
async function poll() {
  const id=active;
  try {
    const job=await api(`/api/jobs/${id}`); if(id!==active)return;
    $('status').textContent=statuses[job.status];$('result-title').textContent=job.query.paper;
    $('logs').textContent=job.logs.join('\n');$('logs-box').open=job.status==='running'||job.status==='failed';
    if(job.status==='completed') {
      result=await api(`/api/jobs/${id}/result`);if(id!==active)return;render();await history();
    } else if(job.status==='failed') {$('warnings').textContent=job.error;await history();}
    else timer=setTimeout(poll,1500);
  }catch(error){$('warnings').textContent=error.message;timer=setTimeout(poll,4000);}
}
function render() {
  const papers=result.nodes.filter(n=>!result.seed_ids.includes(n.id));
  $('stats').replaceChildren();
  [[papers.length,'发现的论文'],[result.edges.length,'已确认引用关系'],[papers.filter(n=>n.abstract_status==='complete').length,'已获取完整摘要']].forEach(([n,label])=>{const s=el('div',undefined,'stat');s.append(el('b',n),el('span',label));$('stats').append(s);});
  $('downloads').replaceChildren();[['excel','下载 Excel ↗'],['json','下载 JSON ↗']].forEach(([type,label])=>{const a=el('a',label);a.href=`/api/jobs/${active}/${type}`;$('downloads').append(a);});
  $('warnings').textContent=result.truncated?'部分引用分支未能核验或已达到查询上限，结果不代表完整引用集合。':'';
  $('provenance').hidden=!(result.warnings||[]).length; $('provenance-logs').textContent=(result.warnings||[]).join('\n');
  renderPapers();
}
function renderPapers(){
  if(!result)return;
  $('papers').replaceChildren();const filter=$('filter').value.toLowerCase();
  const analysis=new Map((result.analysis||[]).map(a=>[a.paper_id,a]));
  const papers=result.nodes.filter(n=>!result.seed_ids.includes(n.id)&&`${n.title} ${n.abstract||''}`.toLowerCase().includes(filter)).sort((a,b)=>a.hop-b.hop);
  if(!papers.length)$('papers').append(el('p','没有符合条件的论文。'));
  papers.forEach(p=>{
    const ai=analysis.get(p.id)||{},card=el('article',undefined,'paper');
    card.append(el('span',`Hop ${p.hop} · ${p.abstract_status==='complete'?'完整摘要':'摘要待补全'}`,`tag ${p.abstract_status==='complete'?'':'incomplete'}`));
    card.append(el('h3',p.title),el('div',`${!p.venue||/arxiv/i.test(p.venue)?'arxiv':p.venue.includes('@')?p.venue.split(',')[0]:p.venue} · ${p.year||'年份待补全'} · ${(p.authors||[]).join(', ')||'作者待补全'}`,'meta'));
    if(ai.summary)card.append(el('p',ai.summary,'summary'));
    if(ai.score!==undefined&&ai.score!==null)card.append(el('div',`AI 参考评分 ${ai.score}/100 · ${ai.reason||''}`,'meta'));
    const details=el('details');details.append(el('summary','查看摘要与中文翻译'),el('p',p.abstract||'尚未获取摘要'));
    if(ai.translation)details.append(el('p',ai.translation));card.append(details);
    const url=p.pdf_url||p.landing_url;
    if(url&&/^https?:\/\//i.test(url)){const a=el('a','阅读原文 ↗');a.href=url;a.target='_blank';a.rel='noopener noreferrer';card.append(a);}
    $('papers').append(card);
  });
}
$('filter').oninput=renderPapers;
$('search-form').onsubmit=async event=>{
  event.preventDefault();$('error').textContent='';$('submit').disabled=true;
  try{const job=await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paper:$('paper').value,keywords:$('keywords').value,depth:Number($('depth').value),max_papers:Number($('max-papers').value),max_neighbors:Number($('max-neighbors').value),gemini:$('gemini').checked})});await history();await select(job.id);}
  catch(error){$('error').textContent=error.message;}finally{$('submit').disabled=false;}
};
(async()=>{try{const config=await api('/api/config');$('config').textContent=`SerpApi ${config.serpapi?'已配置 ✓':'未配置'} · Gemini ${config.gemini?'已配置 ✓':'未配置'}`;if(!config.gemini)$('gemini').checked=false;await history();}catch(error){$('error').textContent=error.message;}})();
