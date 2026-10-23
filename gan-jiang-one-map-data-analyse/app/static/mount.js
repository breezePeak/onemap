let mountData=null,mountTimer=null,mountSearchTimer=null,mountPage=1,mountRequest=0;
let activeReviewProjectId=null,activeReviewFolderIds=[],activePreviewPath=null;
const mountLabels={pending:'候选待确认',confirmed:'已确认',rejected:'已排除',review:'需复核',unlinked:'未找到对应',unscanned:'待匹配'};

document.querySelectorAll('[data-module]').forEach(button=>button.onclick=()=>{
  document.querySelectorAll('[data-module]').forEach(item=>{const active=item===button;item.classList.toggle('active',active);item.setAttribute('aria-pressed',String(active));});
  for(const name of ['mount','extract','output'])$(name+'Module').classList.toggle('hidden',button.dataset.module!==name);
});

if($('closeMountReview'))$('closeMountReview').onclick=()=>{$('mountReviewDialog').close();activeReviewProjectId=null;activeReviewFolderIds=[];};

function mountParams(){return new URLSearchParams({view:'projects',q:$('mountQuery').value.trim(),status:$('mountFilter').value,pair:$('mountPair').value,page:String(mountPage)});}

function mountPathNode(path,action='folder',prefix='📂 '){
  const label=node('div',prefix+path,'manager-path clickable-path');
  label.title=(action==='select'?'点击在 Windows 资源管理器中定位此文件：\n':'点击在 Windows 资源管理器中打开此文件夹：\n')+path;
  label.onclick=e=>{e.stopPropagation();openWindowsPath(path,action);};
  return label;
}

function mountProjectCell(folders){
  const cell=node('td');
  if(!folders.length){cell.append(node('span',mountData?.inventory_only?'待匹配':'—','mount-missing'));return cell;}
  const content=node('div');
  for(const folder of folders){
    const block=node('div',undefined,'mount-project-folder'),title=node('strong',folder.name);
    if(folder.candidate)block.append(node('small','待确认候选','mount-warning'));
    block.append(title);
    for(const path of folder.paths||[folder.folder]){
      block.append(mountPathNode(path,'folder'));
    }
    content.append(block);
  }
  if(folders.length>1){const details=node('details');details.append(node('summary',`${folders.length} 个文件夹候选 · 展开`),content);cell.append(details);}else cell.append(content);
  return cell;
}

function renderMountPreview(fileInfo){
  const box=$('mountReviewRight');
  if(!box)return;
  if(!fileInfo||!fileInfo.physical_path){
    box.replaceChildren(node('div','点击左侧任意证据文件即可在此直接预览文件内容，或打开所在文件夹。','mount-preview-empty'));
    return;
  }
  activePreviewPath=fileInfo.physical_path;
  document.querySelectorAll('.mount-file-card').forEach(el=>{
    el.classList.toggle('active',el.dataset.path===activePreviewPath);
  });
  const fileName=fileInfo.physical_path.split(/[\\/]/).pop()||fileInfo.physical_path;
  const ext=(fileName.includes('.')?fileName.split('.').pop():'').toLowerCase();
  const fileUrl='/api/folder-mount/file?'+new URLSearchParams({path:fileInfo.physical_path});
  const downloadUrl=fileUrl+'&download=1';

  const head=node('div',undefined,'mount-preview-head');
  const titleRow=node('div',undefined,'mount-preview-title');
  if(fileInfo.stageLabel)titleRow.append(node('span',fileInfo.stageLabel,'mount-stage-badge'));
  titleRow.append(node('strong',fileName));
  head.append(titleRow);
  head.append(mountPathNode(fileInfo.physical_path,'select','📂 '));

  const actions=node('div',undefined,'mount-preview-actions');
  const openDirBtn=node('button','📂 打开文件目录','secondary');
  openDirBtn.onclick=()=>openWindowsPath(fileInfo.physical_path,'select');
  const openAppBtn=node('button','🖥 本机默认程序打开','secondary');
  openAppBtn.onclick=()=>openWindowsPath(fileInfo.physical_path,'open');
  const newTabLink=node('a','🔗 新窗口打开','secondary mount-action-link');
  newTabLink.href=fileUrl;newTabLink.target='_blank';newTabLink.rel='noopener';
  const downloadLink=node('a','⬇ 下载文件','secondary mount-action-link');
  downloadLink.href=downloadUrl;downloadLink.target='_blank';downloadLink.rel='noopener';
  actions.append(openDirBtn,openAppBtn,newTabLink,downloadLink);
  head.append(actions);

  const body=node('div',undefined,'mount-preview-body');
  if(ext==='pdf'){
    const frame=node('iframe');
    frame.src=fileUrl;
    frame.className='mount-preview-frame';
    frame.title=fileName;
    body.append(frame);
  }else if(['jpg','jpeg','png','bmp','gif','webp','svg'].includes(ext)){
    const wrap=node('div',undefined,'mount-preview-img-wrap');
    const img=node('img');
    img.src=fileUrl;
    img.alt=fileName;
    img.className='mount-preview-image';
    wrap.append(img);
    body.append(wrap);
  }else if(['txt','csv','json','md','xml','prj','cpg','log'].includes(ext)){
    const pre=node('pre','正在读取文件内容…','mount-preview-text');
    body.append(pre);
    fetch(fileUrl).then(r=>r.ok?r.text():Promise.reject(Error(`读取失败 (HTTP ${r.status})`))).then(text=>{
      pre.textContent=text||'（文件内容为空）';
    }).catch(err=>{
      pre.textContent='无法直接读取文本：'+err.message;
    });
  }else{
    const unsupported=node('div',undefined,'mount-preview-unsupported');
    unsupported.append(
      node('h3',`${fileName}（.${ext?ext.toUpperCase():'未知'} 格式）`),
      node('p','浏览器不支持在网页内直接渲染此格式（如 CAD 图纸、Office 文档或矢量数据），您可以通过以下按钮直接调用本机软件查看或打开所在文件夹：')
    );
    const btnGroup=node('div',undefined,'mount-preview-Actions-large');
    const bigOpen=node('button','🖥 用本机默认程序打开此文件','primary');
    bigOpen.onclick=()=>openWindowsPath(fileInfo.physical_path,'open');
    const bigFolder=node('button','📂 打开所在 Windows 文件夹','secondary');
    bigFolder.onclick=()=>openWindowsPath(fileInfo.physical_path,'select');
    btnGroup.append(bigOpen,bigFolder);
    unsupported.append(btnGroup);
    body.append(unsupported);
  }
  box.replaceChildren(head,body);
}

function mountEvidence(link,autoPreviewFirst=false){
  const container=node('div',undefined,'mount-evidence-container');
  const topBar=node('div',undefined,'mount-evidence-topbar');
  topBar.append(node('strong',`共 ${link.evidence_count} 份相同附件证据`));
  const search=node('input');search.placeholder='搜索文件名或哈希';search.setAttribute('aria-label','搜索材料证据');
  topBar.append(search);
  const records=node('div',undefined,'mount-evidence-list'),toolbar=node('div',undefined,'mount-toolbar'),prev=node('button','上一页','secondary'),next=node('button','下一页','secondary'),label=node('span');
  let page=1,request=0,timer;
  const load=async()=>{
    const current=++request;
    try{
      const data=await api('/api/folder-mount/links/'+encodeURIComponent(link.id)+'?'+new URLSearchParams({page:String(page),q:search.value.trim()}));
      if(current!==request)return;
      records.replaceChildren();
      let firstCandidate=null;
      for(const item of data.evidence){
        const block=node('div',undefined,'mount-evidence');
        const shaHeader=node('div',undefined,'mount-sha-header');
        const shaShort=item.sha256?item.sha256.slice(0,12):'';
        const matchTag=node('span','🔒 内容完全一致（附件比对）','mount-sha-tag');
        const shaText=node('span',`SHA-256: ${shaShort}…`,'mount-sha-hash');
        shaText.title=`完整 SHA-256 哈希值：\n${item.sha256}\n（两端附件字节完全相同）`;
        shaHeader.append(matchTag,shaText);
        block.append(shaHeader);

        const grid=node('div',undefined,'mount-evidence-pair');
        for(const [side,stageLabel,logicalList] of [
          ['left_details',`${link.left.stage} · ${link.left.name}`,item.left||[]],
          ['right_details',`${link.right.stage} · ${link.right.name}`,item.right||[]]
        ]){
          const col=node('div',undefined,'mount-evidence-side');
          col.append(node('div',stageLabel,'mount-side-title'));
          const detailsList=item[side]||[];
          if(detailsList.length){
            for(const material of detailsList){
              const fileInfo={physical_path:material.physical_path,stageLabel};
              if(!firstCandidate&&material.physical_path)firstCandidate=fileInfo;
              const fileCard=node('div',undefined,'mount-file-card'+(activePreviewPath===material.physical_path?' active':''));
              fileCard.dataset.path=material.physical_path||'';
              fileCard.title=material.physical_path?`【点击在右侧预览】\n物理位置: ${material.physical_path}`:'点击在右侧预览';
              fileCard.onclick=()=>renderMountPreview(fileInfo);

              const rawName=(material.physical_path||'').split(/[\\/]/).pop()||material.logical_path.split(/[\\/]/).pop()||'未知文件';
              const ext=(rawName.includes('.')?rawName.split('.').pop():'').toLowerCase();
              let icon='📄 ';
              if(['jpg','jpeg','png','bmp','gif','webp'].includes(ext))icon='🖼 ';
              else if(['dwg','dxf'].includes(ext))icon='📐 ';
              else if(['doc','docx','wps'].includes(ext))icon='📝 ';
              else if(['xls','xlsx','csv'].includes(ext))icon='📊 ';
              else if(['zip','rar','7z'].includes(ext))icon='📦 ';

              const fTitleRow=node('div',undefined,'mount-file-main');
              const fIcon=node('span',icon,'mount-file-icon');
              const fTitle=node('span',rawName,'mount-file-name');
              fTitleRow.append(fIcon,fTitle);
              fileCard.append(fTitleRow);

              if(material.archive_chain?.length||material.logical_path?.includes('!')){
                const zipBadge=node('span','📦 提取自压缩包','mount-archive-tag');
                zipBadge.title='该附件来源于压缩包解压内容';
                fileCard.append(zipBadge);
              }

              if(material.physical_path){
                const btnRow=node('div',undefined,'mount-file-btns');
                const previewBtn=node('button','👁 预览文件','primary');
                previewBtn.onclick=e=>{e.stopPropagation();renderMountPreview(fileInfo);};
                const dirBtn=node('button','📂 打开目录','secondary');
                dirBtn.onclick=e=>{e.stopPropagation();openWindowsPath(material.physical_path,'select');};
                const appBtn=node('button','🖥 默认程序打开','secondary');
                appBtn.onclick=e=>{e.stopPropagation();openWindowsPath(material.physical_path,'open');};
                btnRow.append(previewBtn,dirBtn,appBtn);
                fileCard.append(btnRow);
              }
              col.append(fileCard);
            }
          }else{
            for(const lpath of logicalList){
              const fName=lpath.split(/[\\/!]/).pop()||lpath;
              const card=node('div',undefined,'mount-file-card');
              card.append(node('div',`📄 ${fName}`,'mount-file-name'));
              card.title=lpath;
              col.append(card);
            }
          }
          grid.append(col);
        }
        block.append(grid);
        records.append(block);
      }
      if(!data.evidence.length)records.append(node('p','没有符合条件的证据。'));
      if(data.confirmation?.updated_at)records.append(node('small','最近确认操作：'+(mountLabels[data.confirmation.status]||data.confirmation.status)+' · '+data.confirmation.updated_at+' · '+data.confirmation.actor));
      label.textContent=`第 ${data.page}/${data.pages} 页 · 共 ${data.count} 份相同附件`;
      prev.disabled=page<=1;next.disabled=page>=data.pages;
      if(autoPreviewFirst&&firstCandidate&&!activePreviewPath){
        renderMountPreview(firstCandidate);
      }
    }catch(error){records.replaceChildren(node('p',error.message));}
  };
  prev.onclick=()=>{page--;load();};next.onclick=()=>{page++;load();};
  search.oninput=()=>{page=1;clearTimeout(timer);timer=setTimeout(load,250);};
  toolbar.append(prev,label,next);
  container.append(topBar,records,toolbar);
  load();
  return container;
}

function mountRelation(link,blocked,autoPreviewFirst=false){
  const block=node('div',undefined,'mount-relation-card');
  const header=node('div',undefined,'mount-relation-header');
  
  const titleArea=node('div',undefined,'mount-relation-title-area');
  const title=node('div',undefined,'mount-relation-title');
  const sLeft=node('span',link.left.stage,`mount-stage-pill stage-${link.left.stage.toLowerCase()}`);
  const nameLeft=node('strong',link.left.name,'mount-relation-nodename');
  const arrow=node('span',' ➔ ','mount-relation-arrow');
  const sRight=node('span',link.right.stage,`mount-stage-pill stage-${link.right.stage.toLowerCase()}`);
  const nameRight=node('strong',link.right.name,'mount-relation-nodename');
  title.append(sLeft,nameLeft,arrow,sRight,nameRight);
  const badge=node('span',mountLabels[link.status],`mount-status-badge status-${link.status}`);
  titleArea.append(title,badge);

  const actionRow=node('div',undefined,'mount-relation-header-actions');
  for(const [status,label,cls] of [
    ['confirmed','✓ 确认对应','primary mount-btn-confirm'],
    ['rejected','✕ 排除','secondary mount-btn-reject'],
    ['pending','↺ 撤销待确认','secondary'],
    ['review','⚑ 需复核','secondary mount-btn-review']
  ]){
    if(link.status===status)continue;
    const button=node('button',label,cls);
    button.disabled=blocked;
    button.onclick=async e=>{
      e.stopPropagation();
      button.disabled=true;
      try{
        await api('/api/folder-mount/decision',{
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({id:link.id,signature:link.signature,status})
        });
        notify(`已更新关联状态为：${mountLabels[status]}`);
        await loadMount();
      }catch(error){
        button.disabled=false;
        notify(error.message,true);
      }
    };
    actionRow.append(button);
  }
  header.append(titleArea,actionRow);
  block.append(header);

  if(link.left.folder||link.right.folder){
    const foldersDetails=node('details',undefined,'mount-relation-folders-collapse');
    foldersDetails.append(node('summary','📂 查看两端目录实际路径 · 折叠'));
    const fBox=node('div',undefined,'mount-relation-folders-inner');
    if(link.left.folder)fBox.append(mountPathNode(link.left.folder,'folder',`源阶段 (${link.left.stage}) 目录：`));
    if(link.right.folder)fBox.append(mountPathNode(link.right.folder,'folder',`目标阶段 (${link.right.stage}) 目录：`));
    foldersDetails.append(fBox);
    block.append(foldersDetails);
  }

  block.append(mountEvidence(link,autoPreviewFirst));
  return block;
}

function findActiveReviewProject(){
  if(!mountData?.rows?.length)return null;
  if(activeReviewProjectId){
    const exact=mountData.rows.find(r=>r.id===activeReviewProjectId);
    if(exact)return exact;
  }
  if(activeReviewFolderIds.length){
    return mountData.rows.find(r=>Object.values(r.stages).flat().some(f=>activeReviewFolderIds.includes(f.id)))||null;
  }
  return null;
}

function renderMountReviewDialog(project){
  const dialog=$('mountReviewDialog');
  if(!dialog||!project)return;
  activeReviewProjectId=project.id;
  activeReviewFolderIds=Object.values(project.stages).flat().map(f=>f.id);
  const running=mountData?.status==='running',blocked=running||mountData?.historical;

  $('mountReviewTitle').textContent=`核对项目：${project.name}`;
  $('mountReviewSubtitle').textContent=`状态：${mountLabels[project.status]} · 共 ${project.links.length} 条跨阶段对应关系 · ${project.evidence_count} 份相同附件证据`;

  const left=$('mountReviewLeft');
  left.replaceChildren();

  const stageSummary=node('details',undefined,'mount-dialog-stages-collapse');
  const stageSummaryTitle=node('summary',`📁 查看当前项目在各阶段的原始文件夹清单（共涉及 ${Object.keys(project.stages).filter(k=>(project.stages[k]||[]).length).length} 个阶段）· 点击展开`);
  stageSummary.append(stageSummaryTitle);
  const stageSummaryBody=node('div',undefined,'mount-dialog-stages-body');
  for(const stage of ['S1','S2','S3','S4']){
    const folders=project.stages[stage]||[];
    if(!folders.length)continue;
    const pathItems=[];
    for(const f of folders){
      for(const p of f.paths||[f.folder]){
        pathItems.push({f,p});
      }
    }
    const stageRow=node('div',undefined,'mount-dialog-stage-item');
    stageRow.append(node('strong',`${stage}（${pathItems.length} 个目录）：`));
    for(const {f,p} of pathItems){
      stageRow.append(mountPathNode(p,'folder',`📂 ${f.name}${f.candidate?' [候选]':''}：`));
    }
    stageSummaryBody.append(stageRow);
  }
  stageSummary.append(stageSummaryBody);
  left.append(stageSummary);

  if(project.links.length){
    project.links.forEach((link,idx)=>{
      left.append(mountRelation(link,blocked,idx===0));
    });
  }else{
    left.append(node('p','当前项目暂无跨阶段关联证据。','manager-empty'));
  }

  if(!activePreviewPath){
    renderMountPreview(null);
  }
  if(!dialog.open)dialog.showModal();
}

function openMountReview(project){
  activePreviewPath=null;
  renderMountReviewDialog(project);
}

function renderMount(){
  if(!mountData)return;
  const data=mountData,running=data.status==='running',blocked=running||data.historical;
  $('mountScan').disabled=running||!document.querySelector('#mountMethods [value="M2"]').checked;
  $('mountScan').textContent=running?'正在扫描…':'扫描 / 更新目录关联';
  $('mountPhase').textContent=(data.historical?'历史结果 · ':'')+data.phase+(running&&data.current_stage?' · '+data.current_stage:'')+(data.total?` · ${data.processed}/${data.total}`:'')+(running&&data.current_archive?' · '+data.current_archive.split(/[\\/]/).pop():'');
  $('mountPhase').title=[data.source_root,data.current_archive,data.current_member,data.scanned_at?'最近成功快照：'+data.scanned_at:''].filter(Boolean).join('\n');
  $('mountPhase').classList.toggle('mount-warning',Boolean(data.historical)||data.status==='failed'||data.status==='interrupted');
  const stats=node('table',undefined,'mount-table'),statsHead=node('tr'),statsBody=node('tbody');
  for(const text of ['阶段','业务目录','压缩包','复用成员','新解压','失败','有效材料'])statsHead.append(node('th',text));
  const thead=node('thead');thead.append(statsHead);stats.append(thead,statsBody);
  for(const stage of data.stage_config){const value=data.stages.find(s=>s.stage_id===stage.stage_id)||{};const row=node('tr');for(const text of [stage.stage_id+' · '+stage.name,...['business_directories','archives','reused','extracted','failed','valid_materials'].map(k=>value[k]??'—')])row.append(node('td',String(text)));statsBody.append(row);}
  $('mountStages').replaceChildren(stats);
  $('mountPage').textContent=`第 ${data.page}/${data.pages} 页 · 筛选 ${data.count} 条 · 每页50条`;mountPage=data.page;
  $('mountPrev').disabled=mountPage<=1;$('mountNext').disabled=mountPage>=data.pages;
  const s=data.summary;$('mountSummary').textContent=`${s.projects??0} 个项目${data.inventory_only?'目录':''} · 待匹配 ${s.project_unscanned??0} · 已确认 ${s.project_confirmed??0} · 候选 ${s.project_pending??0} · 需复核 ${s.project_review??0} · 未找到对应 ${s.project_unlinked??0}`;
  if(data.inventory_only)$('mountPhase').textContent=(running?'正在检查材料':'材料检查有异常')+' · 当前显示真实项目目录，尚未生成对应关系';
  $('mountErrors').classList.toggle('hidden',!data.errors.length);
  const errBox=$('mountErrorText');
  errBox.replaceChildren();
  for(const item of data.errors){
    const line=mountPathNode(item.path,'select',`⚠ ${item.error} · `);
    errBox.append(line);
  }
  const table=node('table',undefined,'mount-table mount-projects'),head=node('thead'),tr=node('tr'),body=node('tbody');
  for(const label of ['项目','S1 · 用地预审与选址','S2 · 规划条件与用地红线','S3 · 用地报批','S4 · 土地供应','状态 / 核对'])tr.append(node('th',label));head.append(tr);table.append(head,body);
  for(const project of data.rows||[]){
    const row=node('tr'),name=node('td');name.append(node('strong',project.name));row.append(name);
    for(const stage of ['S1','S2','S3','S4'])row.append(mountProjectCell(project.stages[stage]));
    const actions=node('td');actions.append(node('span',mountLabels[project.status],`mount-status-badge status-${project.status}`));
    if(project.ambiguous)actions.append(node('small','多个候选，需核对','mount-warning'));
    if(project.links.length){
      const reviewBtn=node('button',`🔍 核对 ${project.links.length} 条对应 · ${project.evidence_count} 份证据`,'primary mount-review-trigger');
      reviewBtn.onclick=()=>openMountReview(project);
      actions.append(reviewBtn);
    }else{
      actions.append(node('small',data.inventory_only?'材料准备完成后匹配':'当前方法未匹配到，不代表项目不存在'));
    }
    row.append(actions);body.append(row);
  }
  $('mountTable').replaceChildren(table);
  if(!body.children.length)$('mountTable').append(node('p',running?'正在检查真实数据源材料，完成后更新四阶段项目表。':'暂无符合条件的项目。','manager-empty'));
  $('mountChains').replaceChildren(...data.chains.map(chain=>node('p',chain.map(f=>f.stage+' · '+f.name).join(' → '))));
  if(!data.chains.length)$('mountChains').append(node('p','尚无连续两条以上的人工确认关系。相邻候选不会自动生成确定链路。'));

  if($('mountReviewDialog')?.open){
    const updatedProject=findActiveReviewProject();
    if(updatedProject)renderMountReviewDialog(updatedProject);
  }
}

async function loadMount(){
  clearTimeout(mountTimer);const current=++mountRequest;
  try{const data=await api('/api/folder-mount?'+mountParams());if(current!==mountRequest)return;mountData=data;renderMount();if(data.status==='running')mountTimer=setTimeout(loadMount,1500);}
  catch(error){if(current!==mountRequest)return;$('mountPhase').textContent=error.message;$('mountScan').disabled=false;}
}
$('mountScan').onclick=async()=>{try{$('mountScan').disabled=true;const methods=Array.from(document.querySelectorAll('#mountMethods input:checked:not(:disabled)'),input=>input.value);await api('/api/folder-mount/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({methods})});mountPage=1;await loadMount();}catch(error){renderMount();notify(error.message,true);}};
$('mountMethods').onchange=renderMount;
$('mountQuery').oninput=()=>{mountPage=1;clearTimeout(mountSearchTimer);mountSearchTimer=setTimeout(loadMount,250);};
$('mountFilter').onchange=$('mountPair').onchange=()=>{mountPage=1;loadMount();};
$('mountPrev').onclick=()=>{mountPage--;loadMount();};$('mountNext').onclick=()=>{mountPage++;loadMount();};loadMount();
