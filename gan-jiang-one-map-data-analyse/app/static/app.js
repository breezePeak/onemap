const $=id=>document.getElementById(id);
const stages=[['项目信息',25,29],['预审与选址',30,38],['规划条件与红线',39,46],['用地报批',47,55],['土地供应',56,69],['全部字段',24,69]];
const statusNames={original:'原有值',empty:'暂无证据',filled:'自动补全',candidate:'候选待确认',conflict:'存在冲突',selected:'人工已采用',kept:'已确认保留'};
let file=null,jobId=null,review=null,stage=5,activeCell=null,pendingChoice=null,pollTimer=null;
function node(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function notify(message,error=false){$('toast').textContent=message;$('toast').className='toast'+(error?' error':'');clearTimeout(notify.timer);notify.timer=setTimeout(()=>$('toast').classList.add('hidden'),6500);}
async function api(url,options={}){
  let response;
  try{response=await fetch(url,options);}
  catch(e){throw Error('无法连接工作台服务，请确认服务器已启动（可运行 启动工作台.bat）。');}
  let data=null;
  try{data=await response.json();}catch(e){}
  if(!response.ok)throw Error(data?.error||`操作失败（HTTP ${response.status}），若刚更新过程序请重启服务器并按 Ctrl+F5 刷新页面。`);
  return data;
}
function selectFile(selected){if(!selected)return;if(!selected.name.toLowerCase().endsWith('.xlsx'))return notify('请选择 .xlsx 文件',true);file=selected;$('uploadTitle').textContent=selected.name;$('uploadHelp').textContent=`${(selected.size/1024).toFixed(1)} KB · 已准备好，可开始检索`;$('searchBtn').disabled=false;}
$('chooseFile').onclick=()=>$('fileInput').click();$('fileInput').onchange=e=>selectFile(e.target.files[0]);
for(const name of ['dragenter','dragover'])$('dropZone').addEventListener(name,e=>{e.preventDefault();$('dropZone').classList.add('dragging');});
$('dropZone').addEventListener('dragleave',()=>$('dropZone').classList.remove('dragging'));
$('dropZone').addEventListener('drop',e=>{e.preventDefault();$('dropZone').classList.remove('dragging');selectFile(e.dataTransfer.files[0]);});
async function begin(current=false){
  try{
    $('searchBtn').disabled=true;$('currentBtn').disabled=true;
    const result=await api(current?'/api/current':'/api/jobs',current?{method:'POST'}:{method:'POST',body:file,headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(file.name)}});
    jobId=result.id;review=null;activeCell=null;$('exportBtn').disabled=true;$('progressCard').classList.remove('hidden');$('governanceDownloads').classList.add('hidden');
    $('tableWrap').replaceChildren(node('div','正在检索本机材料，请稍候…','empty-state'));
    renderInspector();await listJobs();poll();
  }catch(e){notify(e.message,true);}finally{$('currentBtn').disabled=false;$('searchBtn').disabled=!file;}
}
$('searchBtn').onclick=()=>begin(false);$('currentBtn').onclick=()=>begin(true);$('emptyCurrent').onclick=()=>begin(true);
async function poll(){
  clearTimeout(pollTimer);const id=jobId;
  try{
    const state=await api(`/api/jobs/${id}`);if(id!==jobId)return;
    $('rerunBtn').disabled=!['ready','failed'].includes(state.status);$('rerunBtn').textContent=state.status==='failed'?'重试当前任务':'按已确认字段重新检索';
    $('phase').textContent=state.phase;$('progressNumber').textContent=`${state.progress||0}%`;$('progressBar').value=state.progress||0;
    $('runLog').textContent=state.logs||'正在建立检索任务…';$('completedLog').textContent=state.logs||'尚无日志';
    $('fullLog').href=`/api/jobs/${id}/log`;$('taskQueue').href=`/api/jobs/${id}/queue`;$('logDownloads').classList.toggle('hidden',state.status!=='ready');
    for(const name of ['unassociated','recognition']){$(name).href=`/api/jobs/${id}/${name}`;$(name).classList.toggle('hidden',!state.cross_stage);}
    for(const name of ['bundle','selection','new-projects','vectors','missing-shp','mapping','materials','resolution','cad','cad-status','coordinate-pending','source-errors']){const link=document.getElementById(name);link.href=`/api/jobs/${id}/${name}`;}
    $('governanceDownloads').classList.toggle('hidden',!state.artifacts||state.status!=='ready');
    if(state.status==='ready'){
      review=await api(`/api/jobs/${id}/review`);if(id!==jobId)return;
      $('progressCard').classList.add('hidden');$('exportBtn').disabled=false;
      $('uploadTitle').textContent=state.filename;$('uploadHelp').textContent='检索完成 · 点击彩色单元格核对候选与来源'+(review.warnings?.length?' · '+review.warnings.join('；'):'');
      render();listJobs();notify(`检索完成：${review.rows.length} 个项目，可开始核对。`);
    }else if(state.status==='failed'){
      $('phase').textContent=state.error||'处理失败';$('progressCard').classList.remove('hidden');notify(state.error||'处理失败，请查看日志',true);
    }else pollTimer=setTimeout(poll,1200);
  }catch(e){notify(e.message,true);}
}
async function listJobs(){
  try{
    const jobs=await api('/api/jobs');$('recentJobs').replaceChildren();
    for(const j of jobs){
      const b=node('button',j.filename,'recent-job'+(j.id===jobId?' active':''));
      b.append(node('small',`${j.created_at?.replace('T',' ')} · ${j.status==='ready'?'可核对':j.status==='failed'?'处理失败':'处理中'}`));
      b.onclick=()=>{document.querySelector('.job-menu').open=false;jobId=j.id;review=null;activeCell=null;$('progressCard').classList.remove('hidden');$('exportBtn').disabled=true;renderInspector();poll();};
      $('recentJobs').append(b);
    }
  }catch(e){notify(e.message,true);}
}
$('refreshJobs').onclick=listJobs;
function formatSize(bytes){
  if(typeof bytes!=='number'||isNaN(bytes))return '—';
  if(bytes<1024)return `${bytes} B`;
  const units=['KB','MB','GB','TB'];let value=bytes/1024,unit=0;
  while(value>=1024&&unit<units.length-1){value/=1024;unit++;}
  return `${value>=100?value.toFixed(0):value.toFixed(1)} ${units[unit]}`;
}
const managerStatusNames={ready:'可核对',failed:'处理失败',running:'处理中',queued:'等待检索'};
let managerTab='datafiles';
let storageKind='archive',storageQuery='',storagePage=1;
async function loadStorageTab(box){
  const data=await api(`/api/storage?${new URLSearchParams({kind:storageKind,q:storageQuery,page:storagePage})}`);
  if(managerTab!=='storage')return;
  const explanation=node('div',undefined,'storage-explanation');
  explanation.append(node('h3','当前存储方式：磁盘文件 + 共享解压索引 + JSON 处理结果'),node('p','没有为每个原始文件建立独立数据库，也没有把所有文件内容存入业务数据库。SQLite 只登记压缩包和成员的解压信息；登记成功不代表 OCR、字段提取或业务入库完成。'));
  for(const [label,path] of [['原始材料目录',data.source_root],['共享解压数据库',data.database],['文件盘点快照',data.inventory],['识别缓存与提取结果（JSON、图片等）',data.work],['各任务的候选、人工选择与来源（review.json 等）',data.jobs],['治理成果（Excel、JSON、ZIP 等）',data.outputs]]){
    const pNode=node('span',path,'manager-path clickable-path');pNode.title='点击在 Windows 资源管理器中打开：'+path;pNode.onclick=()=>openWindowsPath(path,'folder');const line=node('div');line.append(node('strong',label+'：'),pNode);explanation.append(line);
  }
  explanation.append(node('p',`共享数据库：${data.database_exists?'已存在':'尚未生成'} · 成功记录 ${data.counts.done||0} · 失败记录 ${data.counts.failed||0} · 文件盘点 ${data.inventory_count} 条。以下“磁盘状态”在本次查询时检查文件是否存在，未重新校验内容或识别完整性。`));
  const controls=node('form',undefined,'storage-controls'),kind=node('select'),search=node('input'),submit=node('button','搜索','secondary');
  kind.setAttribute('aria-label','选择文件记录类型');
  for(const [value,label] of [['archive','压缩包成员 · 数据库记录'],['inventory','文件盘点 · JSON 快照']]){const option=node('option',label);option.value=value;kind.append(option);}kind.value=storageKind;
  search.value=storageQuery;search.placeholder='搜索文件名、源路径或保存路径';search.setAttribute('aria-label','搜索原始文件');submit.type='submit';
  controls.onsubmit=e=>{e.preventDefault();storageKind=kind.value;storageQuery=search.value.trim();storagePage=1;loadJobManager();};
  kind.onchange=()=>{storageKind=kind.value;storagePage=1;loadJobManager();};controls.append(kind,search,submit);
  const pages=Math.max(1,Math.ceil(data.total/data.page_size)),pager=node('div',undefined,'storage-controls');
  for(const [label,delta] of [['上一页',-1],['下一页',1]]){const button=node('button',label,'secondary');button.disabled=delta<0?storagePage<=1:storagePage>=pages;button.onclick=()=>{storagePage+=delta;loadJobManager();};pager.append(button);}
  pager.append(node('span',`第 ${data.page} / ${pages} 页 · 匹配 ${data.total} 条 · 每页 ${data.page_size} 条`));
  const table=node('table',undefined,'manager-table storage-table'),thead=node('thead'),head=node('tr'),body=node('tbody');
  for(const label of ['原始文件 / 压缩包成员','实际保存路径','登记位置','登记状态 / 磁盘状态'])head.append(node('th',label));thead.append(head);table.append(thead);
  for(const entry of data.entries){
    const tr=node('tr'),srcTd=node('td',entry.source,'manager-path clickable-path');srcTd.title='点击在 Windows 资源管理器中打开：'+entry.source;srcTd.onclick=()=>openWindowsPath(entry.source,'select');tr.append(srcTd);
    const destination=node('td'),destDiv=node('div',entry.dest||'无保存路径',entry.dest?'manager-path clickable-path':'manager-path');if(entry.dest){destDiv.title='点击在 Windows 资源管理器中打开：'+entry.dest;destDiv.onclick=()=>openWindowsPath(entry.dest,'select');}destination.append(destDiv,node('small',`${entry.location} · ${formatSize(entry.size)}`));
    if(entry.dest){const copy=node('button','复制路径','text-button manager-copy');copy.onclick=()=>copyDirectoryPath(entry.dest);destination.append(copy);}tr.append(destination,node('td',entry.record));
    const status=node('td');status.append(node('div',({done:'解压登记成功',failed:'解压失败',snapshot:'已盘点（快照）'})[entry.status]||entry.status),node('div',entry.exists?'磁盘文件存在':'磁盘文件不存在或不可访问',entry.exists?'status-ready':'status-failed'));
    if(entry.error)status.append(node('div',entry.error,'status-failed'));tr.append(status);body.append(tr);
  }
  table.append(body);box.replaceChildren(explanation,controls,pager,table);
  if(!data.entries.length)box.append(node('p','没有匹配的文件记录。尚未生成记录时，请先执行材料解压或数据源盘点。','manager-empty'));
}
async function loadJobManager(){
  const box=$('managerContent');box.replaceChildren(node('div','正在统计磁盘占用，文件较多时约需数秒…','manager-loading'));
  try{
    if(managerTab==='storage')await loadStorageTab(box);
    else if(managerTab==='datafiles')await loadDataFilesTab(box);
    else await loadJobsTab(box);
  }catch(e){box.replaceChildren(node('div',e.message,'manager-empty'));}
}
async function loadDataFilesTab(box){
  const data=await api('/api/datafiles');
  const entries=data.entries||[];
  const summary=node('div',undefined,'manager-summary');
  summary.append(node('span',`共 ${entries.length} 项处理数据 · 合计占用 ${formatSize(data.total_bytes)}`),node('span','按占用从大到小排列；删除缓存类数据后，下次检索会重新生成','manager-summary-note'));
  if(!entries.length){box.replaceChildren(node('div','还没有处理数据。运行检索管线后，处理结果会保存在本机 work 与 outputs 目录。','manager-empty'));return;}
  const table=node('table',undefined,'manager-table'),thead=node('thead'),head=node('tr');
  for(const label of ['数据项','类型','存储目录','占用空间','文件数','修改时间','操作'])head.append(node('th',label));
  thead.append(head);table.append(thead);const body=node('tbody');
  for(const entry of entries){
    const tr=node('tr');
    const nameCell=node('td');nameCell.append(node('div',entry.name,'manager-filename'),node('div',entry.note||'','manager-note'));tr.append(nameCell);
    tr.append(node('td',entry.is_dir?'目录':'文件'));
    const dirCell=node('td'),path=node('div',entry.path,'manager-path clickable-path');path.title='点击在 Windows 资源管理器中打开：'+entry.path;path.onclick=()=>openWindowsPath(entry.path,'folder');
    const copy=node('button','复制路径','text-button manager-copy');copy.onclick=()=>copyDirectoryPath(entry.path);
    dirCell.append(path,copy);tr.append(dirCell);
    tr.append(node('td',formatSize(entry.size_bytes)));
    tr.append(node('td',entry.file_count??'—'));
    tr.append(node('td',(entry.modified||'—').replace('T',' ')));
    const actionCell=node('td');
    if(entry.protected){
      if(entry.name==='jobs'){
        const go=node('button','在“处理任务”页签删除','text-button manager-goto');go.onclick=()=>switchManagerTab('jobs');actionCell.append(go);
      }else actionCell.append(node('span','受保护','manager-protected'));
    }else{
      const remove=node('button','删除','manager-delete');
      remove.onclick=()=>deleteDataFile(entry.key,entry.name,entry.note,entry.path,formatSize(entry.size_bytes));
      actionCell.append(remove);
    }
    tr.append(actionCell);body.append(tr);
  }
  table.append(body);box.replaceChildren(summary,table);
}
async function loadJobsTab(box){
  const jobs=await api('/api/jobs/stats');
  if(!jobs.length){box.replaceChildren(node('div','还没有处理任务。上传 Excel 开始检索后，每个任务的数据会保存在本机 work/jobs/{编号} 目录。','manager-empty'));return;}
  const summary=node('div',undefined,'manager-summary');
  summary.append(node('span',`共 ${jobs.length} 个任务 · 合计占用 ${formatSize(jobs.reduce((s,j)=>s+(j.size_bytes||0),0))}`),node('span','删除任务将移除其上传文件、中间产物与导出结果','manager-summary-note'));
  const table=node('table',undefined,'manager-table'),thead=node('thead'),head=node('tr');
  for(const label of ['任务文件','状态','创建时间','存储目录','占用空间','文件数','操作'])head.append(node('th',label));
  thead.append(head);table.append(thead);const body=node('tbody');
  for(const j of jobs){
    const tr=node('tr');
    const fileCell=node('td');fileCell.append(node('div',j.filename||'未命名文件','manager-filename'),node('div',j.id,'manager-job-id'));tr.append(fileCell);
    tr.append(node('td',managerStatusNames[j.status]||j.status||'—',`manager-status status-${j.status}`));
    tr.append(node('td',(j.created_at||'—').replace('T',' ')));
    const dirCell=node('td'),path=node('div',j.directory||'—',j.directory?'manager-path clickable-path':'manager-path');path.title=j.directory?'点击在 Windows 资源管理器中打开：'+j.directory:'';if(j.directory)path.onclick=()=>openWindowsPath(j.directory,'folder');
    const copy=node('button','复制路径','text-button manager-copy');copy.onclick=()=>copyDirectoryPath(j.directory);dirCell.append(path,copy);tr.append(dirCell);
    tr.append(node('td',formatSize(j.size_bytes)));
    tr.append(node('td',j.file_count??'—'));
    const actionCell=node('td'),remove=node('button','删除','manager-delete');
    remove.onclick=()=>deleteJob(j.id,j.filename,j.directory,formatSize(j.size_bytes));
    actionCell.append(remove);tr.append(actionCell);
    body.append(tr);
  }
  table.append(body);box.replaceChildren(summary,table);
}
function switchManagerTab(tab){
  managerTab=tab;
  $('tabStorage').classList.toggle('active',tab==='storage');
  $('tabDataFiles').classList.toggle('active',tab==='datafiles');
  $('tabJobs').classList.toggle('active',tab==='jobs');
  loadJobManager();
}
async function openWindowsPath(path,action='folder'){
  if(!path||path==='—'||path==='无保存路径')return;
  try{
    const res=await api('/api/folder-mount/open-path',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,action})});
    notify(action==='open'?`已调用本机程序打开：${res.opened}`:`已在 Windows 资源管理器打开：${res.opened}`);
  }catch(e){notify(e.message,true);}
}
function copyDirectoryPath(path){
  if(!path)return;
  navigator.clipboard.writeText(path).then(()=>notify('路径已复制，可在资源管理器地址栏粘贴打开。'),()=>notify('复制失败，请手动选中路径文本复制。',true));
}
function resetWorkspace(){
  $('governanceDownloads').classList.add('hidden');
  clearTimeout(pollTimer);jobId=null;review=null;activeCell=null;pendingChoice=null;
  $('progressCard').classList.add('hidden');$('exportBtn').disabled=true;$('saveStatus').textContent='点击任意单元格，查看候选和来源';
  $('uploadTitle').textContent='上传需要补全的 Excel';$('uploadHelp').textContent='使用当前 69 列治理模板 · .xlsx · 最大 25 MB';
  const empty=node('div',undefined,'empty-state');
  empty.append(node('div','▦','empty-grid'),node('h3','把分散的材料，连接到同一张表'),node('p','上传目标 Excel 后，这里会展示原值、补全结果和待选择的冲突。'));
  const current=node('button','检索当前 11 个项目 →','secondary');current.onclick=()=>begin(true);empty.append(current);
  $('tableWrap').replaceChildren(empty);renderInspector();
}
async function deleteDataFile(key,name,note,path,sizeText){
  const hint=note==='程序运行依赖（不可删除）'||note==='处理任务数据（在“处理任务”页签中逐个删除）'?'':`（${note}）`;
  if(!confirm(`确定删除数据项「${name}」${hint}吗？\n\n存储位置：${path}\n数据量：约 ${sizeText}\n\n删除后将从本机移除，无法恢复；缓存类数据下次检索时会重新生成。`))return;
  try{
    await api(`/api/datafiles/${encodeURIComponent(key)}`,{method:'DELETE'});
    notify(`已删除「${name}」及其全部内容。`);
    await loadJobManager();await listJobs();
  }catch(e){notify(e.message,true);}
}
async function deleteJob(id,filename,directory,sizeText){
  if(!confirm(`确定删除任务「${filename}」吗？\n\n存储目录：${directory}\n数据量：约 ${sizeText}\n\n删除后该目录内全部数据将从本机移除，无法恢复。`))return;
  try{
    await api(`/api/jobs/${id}`,{method:'DELETE'});
    if(jobId===id)resetWorkspace();
    notify('已删除任务及其数据目录。');
    await loadJobManager();await listJobs();
  }catch(e){notify(e.message,true);}
}
$('manageJobs').onclick=()=>{document.querySelector('.job-menu').open=false;$('managerDialog').showModal();loadJobManager();};
$('closeManager').onclick=()=>$('managerDialog').close();
$('tabDataFiles').onclick=()=>switchManagerTab('datafiles');
$('tabJobs').onclick=()=>switchManagerTab('jobs');
$('tabStorage').onclick=()=>switchManagerTab('storage');
$('storageBtn').onclick=()=>{$('managerDialog').showModal();switchManagerTab('storage');};
$('refreshManager').onclick=()=>loadJobManager();
function renderStages(){
  $('stageTabs').replaceChildren();stages.forEach((s,i)=>{const b=node('button',s[0],'stage-tab'+(stage===i?' active':''));b.onclick=()=>{stage=i;renderStages();renderTable();};$('stageTabs').append(b);});
}
function display(value){return value===null||value===undefined||value===''?'—':String(value);}
function detailText(value){return Array.isArray(value)?value.map(detailText).join('；'):value&&typeof value==='object'?Object.entries(value).map(([k,v])=>`${k}：${detailText(v)}`).join('；'):value===null||value===undefined?'—':String(value);}
function detailLines(detail){return detail?Object.entries(detail).map(([k,v])=>`${k}：${detailText(v)}`):[];}
function passes(status){const filter=$('statusFilter').value;return filter==='all'||(filter==='review'?['conflict','candidate'].includes(status):filter==='selected'?['selected','kept'].includes(status):status===filter);}
function render(){
  const c=review.counts;$('filledCount').textContent=c.filled;$('conflictCount').textContent=c.conflict+c.candidate;$('selectedCount').textContent=c.selected+c.kept;$('emptyCount').textContent=c.empty;
  $('saveStatus').textContent='选择自动保存到当前任务';renderTable();renderInspector();
}
function renderTable(){
  if(!review)return;const[,from,to]=stages[stage],query=$('searchInput').value.trim().toLowerCase();
  const {scrollLeft,scrollTop}=$('tableWrap');
  const rows=review.rows.filter(r=>(r.name+' '+(r.archive_id??r.project)).toLowerCase().includes(query)&&r.cells.some(c=>{const e=review.cells[c];return e.column>=from&&e.column<=to&&passes(e.status);}));
  const columns=Array.from({length:to-from+1},(_,i)=>from+i).filter(col=>$('statusFilter').value==='all'||rows.some(r=>r.cells.some(c=>review.cells[c].column===col&&passes(review.cells[c].status))));
  $('rowCount').textContent=`${rows.length} / ${review.rows.length} 个项目`;
  const table=node('table');table.style.width=`${170+columns.length*104}px`;const thead=node('thead'),head=node('tr');head.append(node('th','项目 / 档案编号'));
  for(const col of columns)head.append(node('th',review.headers[col-1]?.replaceAll('\n',' ')||`第 ${col} 列`));
  thead.append(head);table.append(thead);const body=node('tbody');
  for(const row of rows){
    const tr=node('tr'),id=node('td');id.append(node('div',row.name,'project-name'),node('div',`${row.archive_id??row.project} · 第 ${row.row} 行`,'project-id'));id.title=row.name;tr.append(id);
    for(const col of columns){
      const e=review.cells[row.cells.find(c=>review.cells[c].column===col)],td=node('td');
      if(!e){tr.append(td);continue;}
      const b=node('button',undefined,`cell-button cell-${e.status}${activeCell===e.cell?' active':''}`);
      b.dataset.cell=e.cell;b.setAttribute('aria-label',`${e.cell} ${e.field} ${statusNames[e.status]}`);b.title=`${display(e.value)}\n${e.cell} · ${statusNames[e.status]} · 点击查看来源`;
      b.append(node('span',display(e.value),'cell-value'));
      const sourceCount=new Set(e.candidates.flatMap(c=>c.sources)).size;
      const meta=e.status==='conflict'?`${e.candidates.length} 个候选 · 待选择`:e.status==='candidate'?'候选待确认':e.status==='empty'?'暂无证据':`${statusNames[e.status]}${sourceCount?` · ${sourceCount} 份来源`:''}`;
      b.append(node('span',meta,'cell-meta'));b.onclick=()=>{activeCell=e.cell;pendingChoice=null;renderTable();renderInspector();};td.append(b);tr.append(td);
    }
    body.append(tr);
  }
  table.append(body);$('tableWrap').replaceChildren(table);
  if(!rows.length)$('tableWrap').replaceChildren(node('div','当前筛选下没有项目，请切换阶段或状态。','empty-state'));
  $('tableWrap').scrollLeft=scrollLeft;$('tableWrap').scrollTop=scrollTop;
}
function renderInspector(){
  $('inspector').classList.toggle('hidden',!review||!activeCell);
  if(!review||!activeCell){
    $('inspector').replaceChildren();return;
  }
  const e=review.cells[activeCell],header=node('div',undefined,'inspector-head');header.append(node('small',`CELL ${e.cell} · ${statusNames[e.status]}`),node('h3',e.field?.replaceAll('\n',' ')),node('p',review.rows.find(r=>r.project===e.project)?.name));
  const close=node('button','关闭','secondary');close.setAttribute('aria-label','关闭字段详情');close.onclick=()=>{activeCell=null;pendingChoice=null;renderTable();renderInspector();};header.prepend(close);
  const body=node('div',undefined,'inspector-body');body.append(aliasEditor(e.project));body.append(node('span','原有值','section-label'),node('div',display(e.original),'original-box'));
  if(review.spatial_projects?.includes(e.project)){
    const map=node('button','查看本项目全部空间相交图','secondary spatial-entry');map.onclick=()=>showSpatial(e.project);body.append(map);
  }
  if(['filled','selected'].includes(e.status))body.append(node('span','当前采用值','section-label'),node('div',display(e.value),'original-box cell-'+e.status));
  if(['conflict','candidate'].includes(e.status))body.append(node('p','候选尚未确认。导出时将保留原值，缺失字段保持空白。','inspector-note'));
  body.append(node('span',e.candidates.length?`可选值 · ${e.candidates.length} 项`:'未检索到字段候选','section-label'));
  for(const c of e.candidates){
    const isCurrent=review.decisions[e.cell]===c.id||(e.status==='filled'&&e.candidates.length===1);
    const card=node('div',undefined,'candidate-card'+(pendingChoice===c.id||isCurrent?' chosen':'')),top=node('label',undefined,'candidate-top');
    const radio=node('input');radio.type='radio';radio.name='candidate';radio.value=c.id;radio.checked=pendingChoice===c.id;radio.setAttribute('aria-label',`采用 ${display(c.value)}`);
    radio.onchange=()=>{pendingChoice=c.id;renderInspector();};top.append(radio,node('span',display(c.value)));card.append(top,node('span',(isCurrent?'当前已采用 · ':'')+c.methods.join('；'),'method-tag'));
    if(c.project_pending){
      card.append(node('p','本页有目录、正文名称或位置等候选线索，可能对应多个项目。请打开原件核对数值所属项目。','inspector-note'));
      if(pendingChoice===c.id){
        const label=node('label'),check=node('input');check.type='checkbox';check.id='confirmProject';
        check.onchange=()=>{$('adoptBtn').disabled=!check.checked;};
        label.append(check,node('span',`我已核对该页数值属于「${review.rows.find(r=>r.project===e.project)?.name}」`));card.append(label);
      }
    }
    const spatialIds=c.sources.map(sid=>spatialSource(review.sources[sid])).filter(Boolean);
    if(spatialIds.length){const map=node('button','查看这项候选的相交图','secondary spatial-entry');map.onclick=()=>showSpatial(e.project,spatialIds,e.column===52?display(c.value):null);card.append(map);}
    for(const sid of c.sources){const s=review.sources[sid],source=node('div',undefined,'source-item'),button=node('button',spatialSource(s)?`查看图斑 ${s.location} 的相交范围`:`↗ ${s.label.split(/[\\/]/).slice(-2).join(' / ')}`);button.title=s.label;button.onclick=()=>showSource(sid,e.column===52?display(c.value):null);source.append(button);if(s.location)source.append(node('small',s.location));for(const line of detailLines(s.detail))source.append(node('small',line,'detail-line'));card.append(source);}
    body.append(card);
  }
  const detail=node('div');detail.id='sourcePreview';body.append(detail);
  const actions=node('div',undefined,'inspector-actions');
  if(e.candidates.length){const adopt=node('button','采用所选值','primary');adopt.id='adoptBtn';adopt.disabled=!pendingChoice||!!e.candidates.find(c=>c.id===pendingChoice)?.project_pending;adopt.onclick=()=>choose(pendingChoice,$('confirmProject')?.checked===true);actions.append(adopt);}
  const keep=node('button',e.original===null?'确认保留空白':'确认保留原值','secondary');keep.id='keepOriginal';keep.onclick=()=>choose('original');
  const reset=node('button','撤销人工选择','secondary');reset.disabled=!review.decisions[e.cell];reset.onclick=()=>choose(null);actions.append(keep,reset);body.append(actions);$('inspector').replaceChildren(header,body);
}
function showSource(sid,candidateTitle=null){
  const spatialId=spatialSource(review.sources[sid]);if(spatialId){showSpatial(review.cells[activeCell].project,[spatialId],candidateTitle);return;}
  const s=review.sources[sid],box=node('div',undefined,'source-detail');box.append(node('strong','来源详情'),node('pre',s.label+(s.location?'\n'+s.location:'')));
  if(s.detail)box.append(node('pre',detailLines(s.detail).join('\n')));
  if(s.path){
    const url=`/api/jobs/${jobId}/source/${sid}`;
    if(['jpg','jpeg','png','bmp'].includes(s.kind)){const im=node('img');im.src=url;im.alt='原始材料页面';box.append(im);}
    else if(s.kind==='pdf'){const frame=node('iframe');frame.src=url;frame.title='原始 PDF 材料';box.append(frame);}
    const link=node('a','打开 / 下载原始来源文件');link.href=url;link.target='_blank';link.rel='noopener';box.append(link);
  }else box.append(node('p','按上述材料名或档案位置在本机查阅原件。'));
  $('sourcePreview').replaceChildren(box);box.scrollIntoView({block:'nearest'});
}
async function choose(id,confirmProject=false){
  try{const spatialProjects=review.spatial_projects;review=await api(`/api/jobs/${jobId}/choice`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cell:activeCell,candidate_id:id,confirm_project:confirmProject})});review.spatial_projects=spatialProjects;pendingChoice=null;render();notify(id===null?'已撤销选择':id==='original'?'已保存：保留原值':'已保存：采用所选值');}
  catch(e){notify(e.message,true);}
}
$('statusFilter').onchange=renderTable;$('searchInput').oninput=renderTable;
for(const b of document.querySelectorAll('[data-filter]'))b.onclick=()=>{$('statusFilter').value=b.dataset.filter;stage=5;renderStages();renderTable();};
$('rerunBtn').onclick=async()=>{
  if(!jobId)return;
  try{
    $('rerunBtn').disabled=true;
    await api(`/api/jobs/${jobId}/rerun`,{method:'POST'});
    spatialCache.clear();review=null;activeCell=null;$('exportBtn').disabled=true;
    $('progressCard').classList.remove('hidden');$('governanceDownloads').classList.add('hidden');
    $('tableWrap').replaceChildren(node('div','正在按已确认字段重新检索…','empty-state'));renderInspector();poll();
  }catch(error){$('rerunBtn').disabled=false;notify(error.message,true);}
};
$('exportBtn').onclick=async()=>{
  const b=$('exportBtn');b.disabled=true;b.textContent='正在生成带颜色的 Excel…';
  try{const result=await api(`/api/jobs/${jobId}/export`,{method:'POST'});const a=node('a');a.href=result.download;a.download='治理结果.xlsx';document.body.append(a);a.click();a.remove();notify('导出完成，已包含标色；未确认项保留原值。');const audit=node('a','下载选择记录与多来源清单');audit.href=result.audit;audit.target='_blank';$('saveStatus').replaceChildren(audit);}
  catch(e){notify(e.message,true);}finally{b.disabled=false;b.textContent='↓ 生成并下载 Excel';}
};
renderStages();listJobs();
