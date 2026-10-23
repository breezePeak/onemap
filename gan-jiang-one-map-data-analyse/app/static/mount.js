let mountData=null,mountTimer=null,mountPage=1;
const mountLabels={pending:'待确认',confirmed:'已确认同项目',rejected:'已排除'};
document.querySelectorAll('[data-module]').forEach(button=>button.onclick=()=>{
  document.querySelectorAll('[data-module]').forEach(item=>{const active=item===button;item.classList.toggle('active',active);item.setAttribute('aria-pressed',String(active));});
  for(const name of ['mount','extract','output'])$(name+'Module').classList.toggle('hidden',button.dataset.module!==name);
});
function renderMount(){
  if(!mountData)return;
  const data=mountData,query=$('mountQuery').value.trim().toLowerCase(),filter=$('mountFilter').value;
  const running=data.status==='running';$('mountScan').disabled=running;
  $('mountScan').textContent=running?'正在扫描…':'扫描 / 更新关联';
  $('mountSource').textContent='数据源：'+data.source_root;
  $('mountPhase').textContent=data.phase+(data.scanned_at?' · 最近结果：'+data.scanned_at.replace('T',' '):'');
  const linked=new Set(data.links.flatMap(link=>[link.left.folder,link.right.folder]));
  const unlinked=data.folders.filter(folder=>!linked.has(folder.folder));
  const matches=filter==='unlinked'?unlinked.filter(folder=>JSON.stringify(folder).toLowerCase().includes(query)):data.links.filter(link=>(filter==='all'||link.status===filter)&&JSON.stringify(link).toLowerCase().includes(query));
  const pages=Math.max(1,Math.ceil(matches.length/50));mountPage=Math.min(mountPage,pages);
  const visible=matches.slice((mountPage-1)*50,mountPage*50);
  $('mountPage').textContent=`第 ${mountPage} / ${pages} 页 · 筛选 ${matches.length} 条 · 每页 50 条`;
  $('mountPrev').disabled=mountPage<=1;$('mountNext').disabled=mountPage>=pages;
  $('mountSummary').textContent=`${data.folders.length} 个材料目录 · ${data.links.length} 对关联 · 已确认 ${data.links.filter(link=>link.status==='confirmed').length} 对 · 待确认 ${data.links.filter(link=>link.status==='pending').length} 对 · 未关联 ${unlinked.length} 个`;
  $('mountErrors').classList.toggle('hidden',!data.errors.length);
  $('mountErrorText').textContent=data.errors.map(item=>`${item.path}：${item.error}`).join('\n');
  const table=node('table',undefined,'mount-table'),head=node('thead'),tr=node('tr'),body=node('tbody');
  for(const label of ['阶段 / 文件夹 A','阶段 / 文件夹 B','关联文件证据','关联状态','操作'])tr.append(node('th',label));head.append(tr);table.append(head,body);
  const folderCell=folder=>{const cell=node('td');cell.append(node('strong',folder.stage),node('div',folder.folder,'manager-path'),node('small',`${folder.files} 个文件`));return cell;};
  for(const link of filter==='unlinked'?[]:visible){
    const row=node('tr');row.append(folderCell(link.left),folderCell(link.right));
    const evidence=node('td'),details=node('details');details.append(node('summary',`${link.evidence.length} 份内容完全相同的文件 · 展开核对`));
    for(const item of link.evidence){const block=node('div',undefined,'mount-evidence');block.append(node('strong','SHA-256：'+item.sha256),node('p','A：'+item.left.join('\n')),node('p','B：'+item.right.join('\n')));details.append(block);}evidence.append(details);
    const actions=node('td');
    for(const [status,label] of [['confirmed','确认同项目'],['rejected','排除关联'],['pending','撤销确认']]){
      if(link.status===status)continue;
      const button=node('button',label,'secondary');button.disabled=running;
      button.onclick=async()=>{button.disabled=true;try{mountData=await api('/api/folder-mount/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:link.id,signature:link.signature,status})});renderMount();}catch(error){button.disabled=false;notify(error.message,true);}};actions.append(button);
    }
    row.append(evidence,node('td',mountLabels[link.status]),actions);body.append(row);
  }
  if(filter==='unlinked')for(const folder of visible){const row=node('tr');row.append(folderCell(folder),node('td','—'),node('td','未找到跨阶段相同文件，不能据此判断阶段不存在'),node('td','未关联'),node('td','—'));body.append(row);}
  $('mountTable').replaceChildren(table);
  if(!body.children.length)$('mountTable').append(node('p',running?'扫描中，完成后展示关联结果。':data.scanned_at?'没有符合筛选条件的记录。':'点击“扫描并关联文件夹”开始。','manager-empty'));
}
async function loadMount(){
  clearTimeout(mountTimer);
  try{mountData=await api('/api/folder-mount');renderMount();if(mountData.status==='running')mountTimer=setTimeout(loadMount,1500);}
  catch(error){$('mountPhase').textContent=error.message;$('mountScan').disabled=false;}
}
$('mountScan').onclick=async()=>{try{$('mountScan').disabled=true;await api('/api/folder-mount/scan',{method:'POST'});await loadMount();}catch(error){$('mountScan').disabled=false;notify(error.message,true);}};
$('mountQuery').oninput=()=>{mountPage=1;renderMount();};$('mountFilter').onchange=()=>{mountPage=1;renderMount();};
$('mountPrev').onclick=()=>{mountPage--;renderMount();};$('mountNext').onclick=()=>{mountPage++;renderMount();};loadMount();
