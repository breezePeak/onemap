async function matchingAction(action,payload){
  const result=await api(`/api/jobs/${jobId}/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  spatialCache.clear();if($('spatialDialog').open)$('spatialDialog').close();
  review=null;activeCell=null;$('exportBtn').disabled=true;$('rerunBtn').disabled=true;$('progressCard').classList.remove('hidden');
  $('tableWrap').replaceChildren(node('div','正在按人工证据重新匹配…','empty-state'));renderInspector();poll();return result;
}
function aliasEditor(project){
  const box=node('details',undefined,'matching-editor'),title=node('summary','历史名称与地址别名');box.append(title);
  const kind=node('select');kind.setAttribute('aria-label','别名类型');
  for(const [value,label] of [['name','项目历史名称'],['address','地址别名'],['road','道路曾用名']]){const option=node('option',label);option.value=value;kind.append(option);}
  const alias=node('input');alias.placeholder='填写已核实的历史名称或地址';alias.setAttribute('aria-label','别名');
  const canonical=node('input');canonical.placeholder='道路现名（道路曾用名必填）';canonical.setAttribute('aria-label','道路现名');
  const save=node('button','保存别名并重新匹配','secondary');save.onclick=async()=>{try{save.disabled=true;await matchingAction('alias',{project,kind:kind.value,alias:alias.value,canonical:canonical.value});}catch(e){save.disabled=false;notify(e.message,true);}};
  box.append(kind,alias,canonical,save);
  for(const item of (review.aliases||[]).filter(a=>a.project===project)){
    const row=node('div',`${item.alias}${item.canonical?' → '+item.canonical:''}`),remove=node('button','移除别名','text-button');
    remove.onclick=()=>matchingAction('alias',{...item,remove:true}).catch(e=>notify(e.message,true));row.append(remove);box.append(row);
  }
  return box;
}
function matchingSpatialTools(data,aside,hits){
  aside.append(node('h3','整体挂接与范围归属'));
  for(const group of data.groups||[])aside.append(node('p',`${group.key} · ${group.count}块 · 并集覆盖 ${(group.coverage*100).toFixed(2)}% · 重复区域 ${group.duplicated_m2.toFixed(2)} 平方米`));
  for(const conflict of data.resolution?.conflicts||[])aside.append(node('p',`${conflict.status==='unresolved'?'待消歧':'已确认'}：${conflict.projects.join('、')} · ${conflict.reason}`,'inspector-note'));
  if(data.resolution?.excluded)aside.append(node('p','此范围已排除，不参与字段候选。','inspector-note'));
  const reason=node('textarea');reason.placeholder='填写范围归属依据（用于跨项目整体消歧）';reason.setAttribute('aria-label','范围归属依据');
  aside.append(reason);
  for(const [mode,label] of [['shared','确认参考范围及所选图斑可共用'],['exclusive','确认参考范围及所选图斑仅属于本项目'],['reject','排除此范围'],['reset','撤销范围确认']]){
    const button=node('button',label,'secondary');button.onclick=()=>matchingAction('relationship',{project:data.project,anchor_signature:data.anchor_signature,hits,mode,note:reason.value}).catch(e=>notify(e.message,true));aside.append(button);
  }
  if(data.coordinate_inputs?.length){
    const section=node('details');section.append(node('summary','指定附件实际坐标系'));
    const source=node('select');source.setAttribute('aria-label','附件矢量来源');for(const item of data.coordinate_inputs){const option=node('option',item);option.value=item;source.append(option);}
    const crs=node('input');crs.placeholder='实际EPSG代码或WKT，例如 EPSG:4548';crs.setAttribute('aria-label','附件实际坐标系');
    const save=node('button','保存坐标系并重新匹配','secondary');save.onclick=()=>matchingAction('coordinate-system',{source:source.value,crs:crs.value}).catch(e=>notify(e.message,true));section.append(source,crs,save);aside.append(section);
  }
}
function drawRegisteredRasters(data,svg,origin){
  for(const registration of data.registrations||[]){
    const [a,b,c,d,e,f]=registration.matrix;
    const raster=spatialSvg('image',{href:`/api/jobs/${jobId}/source/${registration.source}`,width:registration.width,height:registration.height,opacity:.45,transform:`matrix(${a} ${-d} ${b} ${-e} ${c-origin[0]} ${origin[1]-f})`,'pointer-events':'none'});svg.append(raster);
    for(const boundary of registration.boundaries||[])svg.append(spatialSvg('path',{d:geometryPath(boundary.geometry,origin),fill:'none',stroke:'#cf2959','stroke-width':2,'vector-effect':'non-scaling-stroke','pointer-events':'none'}));
  }
}
function georeferenceEditor(data,svg,aside,origin){
  const sources=Object.values(review.sources).filter(s=>s.path&&['jpg','jpeg','png','bmp'].includes(s.kind));if(!sources.length||!data.reference_crs)return;
  const section=node('details');section.append(node('summary','图件控制点配准与叠加'),node('p','先点原图上的同名点，再点矢量图上的对应位置，至少4组，尽量分布在图幅四周。保存后检查叠加和误差。'));
  const source=node('select');source.setAttribute('aria-label','配准图片');for(const s of sources){const option=node('option',s.label+' '+(s.location||''));option.value=s.id;source.append(option);}
  const image=node('img');image.alt='点击选取配准控制点';image.style.width='100%';image.style.cursor='crosshair';
  let pixel=null,points=[];const summary=node('pre');const show=()=>{summary.textContent=points.map((p,i)=>`点${i+1}：图像 ${p[0].toFixed(1)},${p[1].toFixed(1)} → 地图 ${p[2].toFixed(3)},${p[3].toFixed(3)}`).join('\n')+(pixel?'\n已选原图点，请点地图对应位置':'');};
  const select=()=>{image.src=`/api/jobs/${jobId}/source/${source.value}`;points=[];pixel=null;show();};source.onchange=select;select();
  image.onclick=event=>{const rect=image.getBoundingClientRect();pixel=[(event.clientX-rect.left)/rect.width*image.naturalWidth,(event.clientY-rect.top)/rect.height*image.naturalHeight];show();};
  svg.addEventListener('click',event=>{if(!pixel||!section.open)return;event.stopImmediatePropagation();const point=svg.createSVGPoint();point.x=event.clientX;point.y=event.clientY;const local=point.matrixTransform(svg.getScreenCTM().inverse());points.push([...pixel,local.x+origin[0],origin[1]-local.y]);pixel=null;show();},true);
  const undo=node('button','撤销上一控制点','secondary');undo.onclick=()=>{points.pop();pixel=null;show();};
  const tolerance=node('input');tolerance.type='number';tolerance.value='2';tolerance.min='.01';tolerance.max='100';tolerance.setAttribute('aria-label','配准允许最大误差（米）');
  const save=node('button','计算配准并叠加','primary');save.onclick=async()=>{try{save.disabled=true;const result=await api(`/api/jobs/${jobId}/georeference`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project:data.project,source:source.value,points,crs:data.reference_crs,tolerance:Number(tolerance.value)})});data.registrations=(data.registrations||[]).filter(r=>r.id!==result.id).concat(result);spatialCache.clear();drawSpatial(data,[]);notify(`配准完成：最大误差 ${result.max_error_m.toFixed(3)} 米，红线闭合候选 ${result.boundary_candidates} 个。请核对叠加。`);}catch(e){save.disabled=false;notify(e.message,true);}};
  section.append(source,image,summary,undo,node('label','允许最大误差（米）'),tolerance,save);aside.append(section);
  for(const r of data.registrations||[])aside.append(node('p',`已配准：${r.source_label} · 最大误差${r.max_error_m.toFixed(3)}米 · 红线候选${r.boundary_candidates}个`));
}
