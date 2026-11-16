const $ = id => document.getElementById(id);
let active = null, result = null, timer = null;
const statuses = {queued:'等待执行',running:'正在探索',completed:'探索完成',failed:'查询失败',interrupted:'已中断',cancelled:'已取消',cancelling:'正在停止'};
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
  $('graph-panel').hidden=true; $('job-actions').replaceChildren(); graphState.collapsed.clear(); graphState.selected=null;
  $('empty').hidden=true;$('activity').hidden=false;$('filter').value=''; await poll();
}
async function poll() {
  const id=active;
  try {
    const job=await api(`/api/jobs/${id}`); if(id!==active)return;
    $('status').textContent=statuses[job.status];$('result-title').textContent=job.query.paper;
    renderActions(job);
    $('logs').textContent=(job.logs||[]).join('\n');$('logs-box').open=job.status==='running'||job.status==='failed';
    if(job.status==='completed') {
      result=await api(`/api/jobs/${id}/result`);if(id!==active)return;render();await history();
    } else if(['failed','interrupted','cancelled'].includes(job.status)) {$('warnings').textContent=job.error||'任务已停止，可继续查询。';await history();}
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
  drawGraph(true);
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
    const publication = {published:'已发表',accepted:'已接收',preprint:'预印本',unknown:'发表状态未核实'};
    card.append(el('div',publication[p.publication_status||'unknown'],'meta'));
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
  try{const job=await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paper:$('paper').value,keywords:$('keywords').value,depth:Number($('depth').value),max_papers:Number($('max-papers').value),max_neighbors:Number($('max-neighbors').value),gemini:$('gemini').checked,refresh:$('refresh').checked})});await history();await select(job.id);}
  catch(error){$('error').textContent=error.message;}finally{$('submit').disabled=false;}
};
(async()=>{try{const config=await api('/api/config');$('config').textContent=`SerpApi ${config.serpapi?'已配置 ✓':'未配置'} · Gemini ${config.gemini?'已配置 ✓':'未配置'}`;if(!config.gemini)$('gemini').checked=false;await history();}catch(error){$('error').textContent=error.message;}})();

function renderActions(job) {
  $('job-actions').replaceChildren();
  const action = ['queued','running'].includes(job.status) ? 'cancel' : ['failed','interrupted','cancelled'].includes(job.status) ? 'resume' : null;
  if (!action) return;
  const button = el('button', action==='cancel'?'停止任务':'继续查询');
  button.type='button';
  button.onclick=async()=>{
    button.disabled=true;
    try {
      await api(`/api/jobs/${job.id}/${action}`, {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      await history();
      if(active===job.id) {clearTimeout(timer);await poll();}
    }catch(error){$('warnings').textContent=error.message;button.disabled=false;}
  };
  $('job-actions').append(button);
  if(action==='resume')$('job-actions').append(el('span','保留已完成的查询和分析，从保存的进度继续。','hint'));
}

const svgNS='http://www.w3.org/2000/svg';
const graphState={collapsed:new Set(),selected:null,positions:new Map(),children:new Map(),width:0,height:0,scale:1,x:20,y:20,drag:null};
function svgEl(tag, attrs={}, text) {
  const node=document.createElementNS(svgNS,tag);
  for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));
  if(text!==undefined)node.textContent=text;
  return node;
}
function graphTransform(){
  const group=$('graph-scene');
  if(group)group.setAttribute('transform',`translate(${graphState.x} ${graphState.y}) scale(${graphState.scale})`);
}
function fitGraph(){
  const width=$('citation-graph').clientWidth||700;
  graphState.scale=Math.min(1.2,width/(graphState.width+40),420/(graphState.height+40));
  graphState.x=(width-graphState.width*graphState.scale)/2;
  graphState.y=(420-graphState.height*graphState.scale)/2;
  graphTransform();
}
function zoomGraph(factor, px, py){
  const svg=$('citation-graph');
  px=px??svg.clientWidth/2;py=py??210;
  const next=Math.max(.002,Math.min(3,graphState.scale*factor));
  const ratio=next/graphState.scale;
  graphState.x=px-(px-graphState.x)*ratio;graphState.y=py-(py-graphState.y)*ratio;
  graphState.scale=next;graphTransform();
}
function titleLines(title){
  const chars=Array.from(title);const lines=[];let text='',width=0;
  for(const ch of chars){
    const w=/[^\u0000-\u00ff]/.test(ch)?2:1;
    if(width+w>32){lines.push(text);text='';width=0;if(lines.length===2)break;}
    text+=ch;width+=w;
  }
  if(lines.length<2&&text)lines.push(text);
  if(chars.length>lines.join('').length)lines[lines.length-1]=lines[lines.length-1].slice(0,-1)+'…';
  return lines;
}
function visibleGraphNodes(){
  const known=new Set(result.nodes.map(n=>n.id));
  const visible=new Set(result.seed_ids.filter(id=>known.has(id))),queue=[...visible];
  while(queue.length){
    const id=queue.shift();
    if(graphState.collapsed.has(id))continue;
    for(const child of graphState.children.get(id)||[]){if(!visible.has(child)){visible.add(child);queue.push(child);}}
  }
  return visible;
}
function drawGraph(initial=false){
  if(!result)return;
  $('graph-panel').hidden=false;
  const svg=$('citation-graph');svg.replaceChildren();graphState.positions.clear();graphState.children.clear();
  const nodes=new Map(result.nodes.map(n=>[n.id,n]));
  for(const node of result.nodes){
    for(const parent of node.parent_ids||[]){
      if(nodes.has(parent)&&nodes.get(parent).hop<node.hop){
        if(!graphState.children.has(parent))graphState.children.set(parent,new Set());
        graphState.children.get(parent).add(node.id);
      }
    }
  }
  // Legacy files may have only paths, so retain the same discovery semantics there.
  for(const path of result.paths||[]){
    for(let i=1;i<path.path.length;i++){
      const parent=path.path[i-1],child=path.path[i];
      if(nodes.has(parent)&&nodes.has(child)&&nodes.get(parent).hop<nodes.get(child).hop){
        if(!graphState.children.has(parent))graphState.children.set(parent,new Set());
        graphState.children.get(parent).add(child);
      }
    }
  }
  const visible=visibleGraphNodes();
  if(!visible.has(graphState.selected))graphState.selected=null;
  const layers=new Map();
  for(const node of result.nodes){if(visible.has(node.id)){if(!layers.has(node.hop))layers.set(node.hop,[]);layers.get(node.hop).push(node);}}
  const defs=svgEl('defs'),marker=svgEl('marker',{id:'citation-arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:7,markerHeight:7,orient:'auto-start-reverse'});
  marker.append(svgEl('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#7c9a89'}));defs.append(marker);svg.append(defs);
  const scene=svgEl('g',{id:'graph-scene'});svg.append(scene);
  const maxRows=Math.max(1,...[...layers.values()].map(items=>items.length));
  graphState.width=(Math.max(0,...layers.keys())+1)*290;
  graphState.height=maxRows*100+40;
  for(const [hop,items] of layers){
    scene.append(svgEl('text',{x:hop*290+120,y:18,'text-anchor':'middle',class:'graph-layer'},hop===0?'种子论文':`Hop ${hop}`));
    items.sort((a,b)=>a.title.localeCompare(b.title));
    items.forEach((node,index)=>graphState.positions.set(node.id,{x:hop*290+10,y:40+index*100}));
  }
  const edgeLayer=svgEl('g',{class:'graph-edges'});scene.append(edgeLayer);
  const edges=result.edges.filter(e=>visible.has(e.source_id)&&visible.has(e.target_id));
  for(const edge of edges){
    const a=graphState.positions.get(edge.source_id),b=graphState.positions.get(edge.target_id);
    const right=a.x<b.x,ax=a.x+(right?230:0),bx=b.x+(right?0:230),ay=a.y+36,by=b.y+36;
    const curve=(ax+bx)/2;
    const edgePath=a.x===b.x?`M ${a.x} ${ay} C ${a.x-48} ${ay}, ${b.x-48} ${by}, ${b.x} ${by}`:`M ${ax} ${ay} C ${curve} ${ay}, ${curve} ${by}, ${bx} ${by}`;
    const selected=graphState.selected&&[edge.source_id,edge.target_id].includes(graphState.selected);
    const path=svgEl('path',{d:edgePath,class:`graph-edge${selected?' selected':''}`,'marker-end':'url(#citation-arrow)'});
    path.append(svgEl('title',{},`${nodes.get(edge.source_id).title} 引用 ${nodes.get(edge.target_id).title}`));edgeLayer.append(path);
  }
  for(const [hop,items] of layers){for(const node of items){
    const pos=graphState.positions.get(node.id),collapsed=graphState.collapsed.has(node.id);
    const group=svgEl('g',{transform:`translate(${pos.x} ${pos.y})`,class:`graph-node${hop===0?' seed':''}${graphState.selected===node.id?' selected':''}`,'data-node-id':node.id,role:'button',tabindex:'0','aria-label':`${node.title}，Hop ${hop}${collapsed?'，后续分支已折叠':''}`});
    group.append(svgEl('title',{},node.title),svgEl('rect',{width:230,height:74,rx:9}));
    titleLines(node.title).forEach((line,i)=>group.append(svgEl('text',{x:12,y:24+i*16,class:'graph-node-title'},line)));
    group.append(svgEl('text',{x:12,y:63,class:'graph-node-meta'},`${node.year||'年份待补全'}${collapsed?' · 分支已折叠':''}`));
    const activate=()=>{graphState.selected=node.id;drawGraph();};
    group.addEventListener('click',activate);group.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();activate();}});scene.append(group);
  }}
  $('graph-count').textContent=`显示 ${visible.size} / ${result.nodes.length} 篇论文、${edges.length} 条引用边。折叠只隐藏该分支独有的后续论文，共享节点仍保留。`;
  graphTransform();
  if(initial)fitGraph();
  showGraphDetail();
}
function showGraphDetail(){
  const container=$('graph-detail');container.replaceChildren();
  const paper=result.nodes.find(n=>n.id===graphState.selected);
  if(!paper){container.textContent='点击图中的论文查看详情，或折叠其后续发现分支。';return;}
  container.append(el('h4',paper.title),el('p',`Hop ${paper.hop} · ${paper.year||'年份待补全'} · ${paper.venue||'发表信息待补全'}`,'meta'));
  const related=result.edges.filter(e=>e.source_id===paper.id||e.target_id===paper.id);
  const nodes=new Map(result.nodes.map(n=>[n.id,n]));
  const list=el('ul');related.forEach(edge=>list.append(el('li',`${nodes.get(edge.source_id)?.title||edge.source_id} → ${nodes.get(edge.target_id)?.title||edge.target_id}`)));
  if(related.length){container.append(el('p','与该论文相连的已确认引用（引用方 → 被引用方）：'),list);}
  if(graphState.children.get(paper.id)?.size){
    const collapse=el('button',graphState.collapsed.has(paper.id)?'展开后续分支':'折叠后续分支');collapse.type='button';
    collapse.onclick=()=>{if(graphState.collapsed.has(paper.id))graphState.collapsed.delete(paper.id);else graphState.collapsed.add(paper.id);drawGraph();};container.append(collapse);
  }
  const details=el('details');details.append(el('summary','查看论文摘要'),el('p',paper.abstract||'尚未取得摘要'));container.append(details);
}
$('graph-zoom-in').onclick=()=>zoomGraph(1.25);
$('graph-zoom-out').onclick=()=>zoomGraph(.8);
$('graph-fit').onclick=fitGraph;
$('graph-reset').onclick=()=>{graphState.collapsed.clear();drawGraph(true);};
$('citation-graph').addEventListener('wheel',event=>{
  event.preventDefault();const box=event.currentTarget.getBoundingClientRect();zoomGraph(event.deltaY<0?1.1:.9,event.clientX-box.left,event.clientY-box.top);
},{passive:false});
$('citation-graph').addEventListener('pointerdown',event=>{
  if(event.target.closest('.graph-node'))return;
  graphState.drag={x:event.clientX,y:event.clientY,tx:graphState.x,ty:graphState.y};event.currentTarget.setPointerCapture(event.pointerId);
});
$('citation-graph').addEventListener('pointermove',event=>{
  if(!graphState.drag)return;
  graphState.x=graphState.drag.tx+event.clientX-graphState.drag.x;graphState.y=graphState.drag.ty+event.clientY-graphState.drag.y;graphTransform();
});
for(const event of ['pointerup','pointercancel'])$('citation-graph').addEventListener(event,()=>{graphState.drag=null;});
