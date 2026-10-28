'use strict';
let s2Page=1,s2Project=null,s2Timer=null;
const s2Names={original:'原始值',empty:'缺少证据',candidate:'候选待确认',conflict:'冲突保留原值',filled:'可靠证据补齐',selected:'人工已采用',keep:'确认保留原值',review:'需复核',stale:'来源变化，重新审核'};
const s2Post=(action,payload={})=>api('/api/stage2/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
const s2Button=(label,handler)=>{const b=node('button',label,'secondary');b.type='button';b.onclick=()=>Promise.resolve(handler()).catch(e=>notify(e.message,true));return b;};
function s2SpatialMap(evidence){
  const points=[...geometryPoints(evidence.anchor_geometry),...geometryPoints(evidence.geometry)],origin=points[0]||[0,0];
  let x=Infinity,y=Infinity,right=-Infinity,bottom=-Infinity;
  for(const p of points){const a=p[0]-origin[0],b=origin[1]-p[1];x=Math.min(x,a);y=Math.min(y,b);right=Math.max(right,a);bottom=Math.max(bottom,b);}
  const pad=Math.max(right-x,bottom-y,1)*.05,svg=spatialSvg('svg',{viewBox:`${x-pad} ${y-pad} ${right-x+pad*2} ${bottom-y+pad*2}`,role:'img','aria-label':'蓝色参考范围、橙色候选图斑、绿色真实交集',width:'100%',height:'300'});
  for(const [geometry,color] of [[evidence.geometry,'#c56b18'],[evidence.anchor_geometry,'#175aa0'],[evidence.intersection,'#32934b']])svg.append(spatialSvg('path',{d:geometryPath(geometry,origin),fill:color,'fill-opacity':'.2',stroke:color,'stroke-width':'2','vector-effect':'non-scaling-stroke','fill-rule':'evenodd'}));
  return svg;
}
async function loadStage2(){
  const data=await api(`/api/stage2?q=${encodeURIComponent($('s2Query').value)}&page=${s2Page}`);s2Page=data.page;
  const phase={partial:'抽样或含待核对材料',ready:'检索完成',failed:'检索失败',interrupted:'上次任务中断'}[data.task?.status]||'等待检索';
  $('s2Phase').textContent=`${data.count} 个独立业务项目 · ${data.running?'检索中':phase}${data.task?` · ${data.task.progress}/${data.task.total}`:''}`;
  $('s2Errors').textContent=data.task?JSON.stringify({...data.task,errors:JSON.parse(data.task.errors)},null,2):'尚未执行';
  $('s2Page').textContent=`${data.page}/${data.pages}`;$('s2Prev').disabled=s2Page<=1;$('s2Next').disabled=s2Page>=data.pages;
  for(const id of ['s2Init','s2Scan','s2Import'])$(id).disabled=data.running;
  const list=$('s2Projects');list.replaceChildren();
  for(const p of data.projects){const b=s2Button(p.name,()=>openS2Project(p.id));b.classList.add('s2-project');b.append(node('small',`${p.stage||'导入项目'} · 档案 ${p.archive||'待识别'} · 完成 ${p.completed_fields??p.original_fields}/46 · 必填 ${p.completed_required??0}/${p.required_fields??4} · 审核 ${p.reviewed}`),node('small',`关联阶段候选：${p.related_stages?.join('、')||'暂无'} · 字段候选 ${p.candidates}`),node('small',p.path));list.append(b);}
  clearTimeout(s2Timer);if(data.running)s2Timer=setTimeout(()=>loadStage2().catch(e=>notify(e.message,true)),2500);
}
async function openS2Project(id){s2Project=id;renderS2Project(await api('/api/stage2/projects/'+encodeURIComponent(id)));}
function renderS2Project(project){
  const box=$('s2Detail');box.replaceChildren(node('h2',project.name),node('p',project.path));
  if(project.directory_unit_id)box.append(node('p',`目录不动产单元号：${project.directory_unit_id}（目录命名线索）`));
  const spatial=node('div');box.append(s2Button('计算 M3 空间关联候选',async()=>{
    const data=await api('/api/stage2/spatial/'+encodeURIComponent(project.id));spatial.replaceChildren(node('p',`${data.evidence.length} 条空间相交候选；局部相交不能确认全部权属。`),node('pre',JSON.stringify(data.errors,null,2)));
    for(const evidence of data.evidence){const detail=node('details');detail.append(node('summary',`${evidence.source} · 相交 ${evidence.intersection_m2.toFixed(2)} 平方米`),s2SpatialMap(evidence),node('pre',JSON.stringify(evidence,null,2)));const note=node('textarea');note.placeholder='核对范围、坐标系和图斑后填写关联依据';note.setAttribute('aria-label','空间关联依据');detail.append(note,s2Button('确认记录关联，字段仍逐项审核',async()=>{if(!note.value.trim())throw Error('请填写空间核对依据');renderS2Project(await s2Post('attach',{project:project.id,record:evidence.record,method:'M3',spatial:evidence,note:note.value,confirm_identity:true}));}));spatial.append(detail);}
  }),spatial);
  for(const group of [...new Set(project.fields.map(f=>f.group))]){
    const section=node('details');section.open=true;section.append(node('summary',group));
    const table=node('table',undefined,'s2-fields'),head=node('tr');for(const text of ['字段','原始值','当前预览 / 状态','候选与来源'])head.append(node('th',text));table.append(head);
    for(const field of project.fields.filter(f=>f.group===group)){
      const row=node('tr');row.append(node('td',`${field.code} · ${field.name}`),node('td',String(field.original??'')));
      const state=node('td');state.append(node('strong',String(field.value??'')),node('p',s2Names[field.status]||field.status));
      for(const [label,action] of [['保留原值','keep'],['需复核','review'],['撤销审核','undo']])state.append(s2Button(label,async()=>renderS2Project(await s2Post('review',{project:project.id,field:field.code,action}))));
      row.append(state);const options=node('td');
      for(const c of field.candidates){
        const detail=node('details');detail.append(node('summary',`${c.value} · ${c.excluded?'已排除':c.valid?'待核对':'来源已失效'}`));
        const source=node('a','查看真实来源');source.href='/api/stage2/source/'+c.record+(c.location.startsWith('page=')?'#'+c.location.split(';')[0]:'');source.target='_blank';source.rel='noopener';
        detail.append(node('p',`${c.stage} 存放 → ${c.business_stage} 字段 · ${c.method}`),source,node('p',`${c.path} · ${c.location}`),node('pre',JSON.stringify({原文:c.raw,关联依据:c.basis,可靠性:c.reliability,指纹:c.fingerprint},null,2)));
        const confirm=node('input');confirm.type='checkbox';const label=node('label');label.append(confirm,' 已核对原文，确认属于当前项目及宗地');detail.append(label);
        detail.append(s2Button('采用此值',async()=>{if(!confirm.checked)throw Error('请先查看来源并确认项目及宗地归属');renderS2Project(await s2Post('review',{project:project.id,field:field.code,action:'adopt',candidate:c.id,confirm_identity:true}));}),s2Button('排除此候选',async()=>renderS2Project(await s2Post('review',{project:project.id,field:field.code,action:'exclude',candidate:c.id}))));options.append(detail);
      }
      row.append(options);table.append(row);
    }
    section.append(table);box.append(section);
  }
  const history=node('details');history.append(node('summary',`审核历史（${project.history.length}）`),node('pre',JSON.stringify(project.history,null,2)));box.append(history);
}
$('s2Init').onclick=async()=>{try{await s2Post('init');await loadStage2();notify('模板及第一阶段项目已读取，示例行未导入');}catch(e){notify(e.message,true);}};
$('s2Scan').onclick=async()=>{try{await s2Post('scan');await loadStage2();}catch(e){notify(e.message,true);}};
$('s2Import').onchange=async e=>{const file=e.target.files[0];if(!file)return;try{await api('/api/stage2/import',{method:'POST',body:file,headers:{'Content-Type':'application/octet-stream'}});await loadStage2();}catch(error){notify(error.message,true);}};
$('s2Query').oninput=()=>{s2Page=1;clearTimeout(s2Timer);s2Timer=setTimeout(()=>loadStage2().catch(e=>notify(e.message,true)),300);};
$('s2Prev').onclick=()=>{s2Page--;loadStage2().catch(e=>notify(e.message,true));};
$('s2Next').onclick=()=>{s2Page++;loadStage2().catch(e=>notify(e.message,true));};
$('s2FindRecords').onclick=async()=>{try{
  const rows=await api('/api/stage2/records?q='+encodeURIComponent($('s2RecordQuery').value)),box=$('s2Records');box.replaceChildren(node('p','最多显示50条，请缩小搜索范围；先选择目标项目再关联业务记录。'));
  for(const r of rows){const detail=node('details');detail.append(node('summary',`${r.stage} · ${r.business_stage} · ${r.path} · ${r.location}`),node('pre',r.text));const source=node('a','查看原件');source.href='/api/stage2/source/'+r.id;source.target='_blank';detail.append(source);
    const note=node('textarea');note.placeholder='填写编号核对、空间叠加或人工判读依据';note.setAttribute('aria-label','记录归属依据');detail.append(note);
    detail.append(s2Button('将记录关联到当前项目（字段逐项审核）',async()=>{if(!s2Project)throw Error('请先选择目标项目');if(!note.value.trim())throw Error('请填写已核实的归属依据');renderS2Project(await s2Post('attach',{project:s2Project,record:r.id,note:note.value,method:'M5',confirm_identity:true}));}));box.append(detail);}
}catch(e){notify(e.message,true);}};
document.querySelector('[data-module="extract"]').addEventListener('click',()=>loadStage2().catch(e=>notify(e.message,true)));
