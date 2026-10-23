const spatialCache=new Map();let spatialRequest=0;
function spatialSource(source){
  const layer=/批文报批矢量1016\.dbf/i.test(source.label)?'approvals':/地表宗地\.dbf/i.test(source.label)?'parcels':null;
  const ident=source.location?.match(/OBJECTID=([^，\s]+)/)?.[1];return layer&&ident?`${layer}:${ident}`:null;
}
function geometryPoints(geometry){
  if(!geometry)return [];
  if(geometry.type==='GeometryCollection')return geometry.geometries.flatMap(geometryPoints);
  const points=[];function visit(value){if(typeof value[0]==='number'){if(Number.isFinite(value[0])&&Number.isFinite(value[1]))points.push(value);}else value.forEach(visit);}
  visit(geometry.coordinates||[]);return points;
}
function geometryPath(geometry,origin){
  if(!geometry)return '';
  if(geometry.type==='GeometryCollection')return geometry.geometries.map(g=>geometryPath(g,origin)).join(' ');
  const polygons=geometry.type==='Polygon'?[geometry.coordinates]:geometry.type==='MultiPolygon'?geometry.coordinates:[];
  return polygons.map(polygon=>polygon.map(ring=>ring.map((point,i)=>`${i?'L':'M'}${point[0]-origin[0]},${origin[1]-point[1]}`).join(' ')+' Z').join(' ')).join(' ');
}
function spatialSvg(tag,attributes={}){const element=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attributes))element.setAttribute(k,v);return element;}
async function showSpatial(project,focus=[],candidateTitle=null){
  const request=++spatialRequest,key=`${jobId}/${project}`,dialog=$('spatialDialog');
  $('spatialTitle').textContent='空间相交图';$('spatialSubtitle').textContent=review.rows.find(r=>r.project===project)?.name||project;
  $('spatialContent').replaceChildren(node('p','正在读取真实图斑…','spatial-loading'));if(!dialog.open)dialog.showModal();
  try{
    let data=spatialCache.get(key);if(!data){data=await api(`/api/jobs/${jobId}/spatial/${encodeURIComponent(project)}`);spatialCache.set(key,data);}
    if(request!==spatialRequest||!dialog.open)return;
    if(data.supply_pending){
      const box=$('spatialContent');box.replaceChildren(node('p','精确标识缺失或尚未确认。以下为供地、报批、宗地、附件的名称、位置、面积候选，请逐条查看；选择查看不会自动采用字段。','spatial-loading'));
      for(const candidate of data.supply_candidates){const b=node('button',`${candidate.name||'未命名图斑'} · 相似度 ${(candidate.score*100).toFixed(0)}%`,'secondary');b.onclick=()=>{drawSpatial({...candidate.view,registrations:data.registrations,coordinate_inputs:data.coordinate_inputs},[]);const back=node('button','返回图斑候选','secondary');back.onclick=()=>showSpatial(project);$('spatialContent').prepend(back);};box.append(b);}
      return;
    }
    const ids=focus.length?data.hits.filter(h=>focus.includes(h.source_id)&&(!candidateTitle||h.title===candidateTitle)).map(h=>h.id):[];
    drawSpatial(data,ids);
  }catch(error){if(request===spatialRequest)$('spatialContent').replaceChildren(node('p',error.message,'spatial-loading'));}
}
function drawSpatial(data,focus){
  const anchor=data.anchor_name||'供地范围';
  const layerNames={approvals:'报批图斑',parcels:'首登宗地',attachments:'附件矢量'};
  let layer=focus[0]?.split(':')[0]||'approvals',selected=new Set(focus),bounds,svg;
  const origin=geometryPoints(data.supply)[0]||[0,0],content=$('spatialContent');
  const toolbar=node('div',undefined,'spatial-toolbar'),tabs=node('div',undefined,'spatial-tabs');
  const layout=node('div',undefined,'spatial-layout'),map=node('div',undefined,'spatial-map'),aside=node('div',undefined,'spatial-list');
  const legend=node('div',undefined,'spatial-legend');
  for(const [cls,text] of [['supply',`${anchor}（蓝色实线）`],['target','候选图斑（橙色边线）'],['overlap','相交区域（绿色）']])legend.append(node('span',text,cls));
  const metrics=node('div',undefined,'spatial-metrics'),explanation=node('p',undefined,'spatial-explanation');
  toolbar.append(node('span',data.anchor_method||'精确供地标识'));
  const canvas=node('div',undefined,'spatial-canvas');map.append(legend,canvas,node('small','拖动平移 · 滚轮缩放 · 图形来自原始图层','spatial-map-help'));
  layout.append(map,aside);toolbar.append(tabs);
  const viewButton=(text,fn)=>{const b=node('button',text,'secondary');b.onclick=fn;toolbar.append(b);};
  viewButton(`聚焦${anchor}`,()=>fit([data.supply]));viewButton('聚焦相交区域',()=>fit(data.hits.filter(h=>h.layer===layer&&selected.has(h.id)).map(h=>h.intersection)));viewButton('查看图斑全貌',()=>fit([data.supply,...data.hits.filter(h=>h.layer===layer&&(!selected.size||selected.has(h.id))).map(h=>h.geometry)]));
  viewButton('放大 ＋',()=>zoom(.65));viewButton('缩小 −',()=>zoom(1.5));
  content.replaceChildren(toolbar,metrics,explanation,layout);
  function setBounds(value){bounds=value;svg?.setAttribute('viewBox',bounds.join(' '));}
  function fit(geometries){
    const points=geometries.flatMap(geometryPoints).map(p=>[p[0]-origin[0],origin[1]-p[1]]);if(!points.length)return;
    let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
    for(const p of points){minX=Math.min(minX,p[0]);maxX=Math.max(maxX,p[0]);minY=Math.min(minY,p[1]);maxY=Math.max(maxY,p[1]);}
    const width=Math.max(maxX-minX,1),height=Math.max(maxY-minY,1),padding=Math.max(width,height)*.12;setBounds([minX-padding,minY-padding,width+padding*2,height+padding*2]);
  }
  function zoom(factor){if(!bounds)return;const [x,y,w,h]=bounds;setBounds([x+w*(1-factor)/2,y+h*(1-factor)/2,w*factor,h*factor]);}
  function render(){
    tabs.replaceChildren();for(const [id,title] of [['approvals','报批图斑'],['parcels','首登宗地'],['attachments','附件SHP/CAD/红线']]){
      const b=node('button',`${title} · ${data.hits.filter(h=>h.layer===id).length}`,'secondary'+(layer===id?' active':''));b.onclick=()=>{layer=id;selected.clear();render();};tabs.append(b);
    }
    const hits=data.hits.filter(h=>h.layer===layer);if(!selected.size&&hits.length)selected.add(hits[0].id);
    const highlighted=hits.filter(h=>selected.has(h.id)),hit=highlighted[0];
    metrics.replaceChildren(node('div',`${anchor}总面积 ${(data.supply_area_m2/666.6666666667).toFixed(2)} 亩`));
    if(hit){
      metrics.append(node('div',`选中图斑覆盖${anchor} ${(hit.supply_coverage*100).toFixed(2)}%`),node('div',`相交 ${(hit.intersection_m2/666.6666666667).toFixed(2)} 亩`),node('div',`占该${layerNames[layer]} ${(hit.target_coverage*100).toFixed(2)}%`));
      explanation.textContent=highlighted.length>1?`当前高亮 ${highlighted.length} 个图斑，顶部数值对应列表中第一个高亮图斑。点击右侧条目逐个查看，覆盖率不会直接相加。`:
        hit.supply_coverage>=.98&&hit.target_coverage>=.98?'两块图斑的范围基本重合。图形可用于核对范围，批文及项目归属仍需结合材料确认。':`这块${layerNames[layer]}覆盖${anchor}的 ${(hit.supply_coverage*100).toFixed(2)}%。${hit.supply_coverage<.98?'只涉及部分参考范围，不能据此认定整项目适用这份材料。':'需核对材料适用范围，批次不代表项目独占范围。'}`;
    }else explanation.textContent=`本阶段未找到图斑。虚线仅展示${anchor}供参考，不代表本阶段矢量。`;
    svg=spatialSvg('svg',{role:'img','aria-label':`${anchor}与候选图斑的真实空间相交图`,preserveAspectRatio:'xMidYMid meet'});
    const title=spatialSvg('title');title.textContent=`蓝色为${anchor}，橙色为候选图斑，绿色为实际相交部分`;svg.append(title);drawRegisteredRasters(data,svg,origin);
    for(const h of hits){const active=selected.has(h.id),path=spatialSvg('path',{d:geometryPath(h.geometry,origin),class:'spatial-target'+(active?' selected':''),'fill-rule':'evenodd','vector-effect':'non-scaling-stroke'});const label=spatialSvg('title');label.textContent=`${h.title} · 图斑 ${h.object_id} · 覆盖${anchor} ${(h.supply_coverage*100).toFixed(2)}%`;path.append(label);path.onclick=()=>{selected=new Set([h.id]);render();};svg.append(path);}
    for(const h of highlighted)svg.append(spatialSvg('path',{d:geometryPath(h.intersection,origin),class:'spatial-overlap','fill-rule':'evenodd','vector-effect':'non-scaling-stroke','pointer-events':'none'}));
    svg.append(spatialSvg('path',{d:geometryPath(data.supply,origin),class:'spatial-supply','stroke-dasharray':hits.length?'none':'8 5','fill-rule':'evenodd','vector-effect':'non-scaling-stroke','pointer-events':'none'}));
    canvas.replaceChildren(svg);if(bounds)setBounds(bounds);else fit([data.supply]);
    let drag=null,moved=false;
    svg.onpointerdown=e=>{if(e.button!==0)return;const matrix=svg.getScreenCTM();drag={x:e.clientX,y:e.clientY,bounds:[...bounds],scale:matrix.a};moved=false;svg.setPointerCapture(e.pointerId);};
    svg.onpointermove=e=>{if(!drag)return;const dx=(e.clientX-drag.x)/drag.scale,dy=(e.clientY-drag.y)/drag.scale;moved=moved||Math.abs(e.clientX-drag.x)+Math.abs(e.clientY-drag.y)>4;setBounds([drag.bounds[0]-dx,drag.bounds[1]-dy,drag.bounds[2],drag.bounds[3]]);};
    svg.onpointerup=()=>{drag=null;};svg.onpointercancel=()=>{drag=null;};svg.addEventListener('click',e=>{if(moved){e.stopPropagation();moved=false;}},true);
    svg.addEventListener('wheel',e=>{e.preventDefault();zoom(e.deltaY>0?1.18:1/1.18);},{passive:false});
    aside.replaceChildren(node('h3',`相交候选 · ${hits.length} 个图斑`),node('p','点击条目，高亮对应位置。','spatial-list-help'));
    for(const h of hits){const b=node('button',undefined,'spatial-hit'+(selected.has(h.id)?' active':''));b.append(node('strong',h.title),node('span',h.name||`图斑编号 ${h.object_id}`),node('span',`覆盖${anchor} ${(h.supply_coverage*100).toFixed(2)}% · 相交 ${(h.intersection_m2/666.6666666667).toFixed(2)} 亩`),node('small',`${h.method||'空间相交'} · 图斑编号 ${h.object_id} · 占该图斑 ${(h.target_coverage*100).toFixed(2)}%`));b.onclick=()=>{selected=new Set([h.id]);render();};aside.append(b);}
    matchingSpatialTools(data,aside,[...selected]);georeferenceEditor(data,svg,aside,origin);
    const sources=Object.values(review.sources).filter(s=>s.path&&['jpg','jpeg','png','bmp','pdf'].includes(s.kind));
    if(sources.length){
      aside.append(node('h3','原件与图斑人工判读'));
      const sourceSelect=node('select');sourceSelect.setAttribute('aria-label','选择判读原件');
      for(const s of sources){const option=node('option',s.label+' '+(s.location||''));option.value=s.id;sourceSelect.append(option);}
      const imageBox=node('div'),show=()=>{const s=review.sources[sourceSelect.value],element=node(s.kind==='pdf'?'iframe':'img');element.src=`/api/jobs/${jobId}/source/${s.id}`;element.style.width='100%';if(s.kind==='pdf'){element.title='判读原件';element.style.height='400px';}else element.alt='判读原件';imageBox.replaceChildren(element);};
      sourceSelect.onchange=show;const verdict=node('select');verdict.setAttribute('aria-label','判读结论');for(const text of ['待核实','一致','不一致'])verdict.append(node('option',text));
      const note=node('textarea');note.placeholder='填写图形、位置、面积等判读依据';note.setAttribute('aria-label','判读依据');note.maxLength=2000;
      const save=node('button','保存人工判读','primary');save.onclick=async()=>{try{await matchingAction('judgement',{project:data.project,source:sourceSelect.value,anchor_layer:data.anchor_layer||'supply',supply_indices:data.supply_indices,hits:[...selected],verdict:verdict.value,note:note.value});notify('人工判读已保存，正在更新原件归属及图斑关系。');}catch(error){notify(error.message,true);}};
      aside.append(node('p','一致表示原件属于本项目并匹配所选图斑；不一致会排除这条关系。仅对当前原件和几何版本生效。'),sourceSelect,imageBox,verdict,note,save);show();
    }
  }
  render();
}
$('closeSpatial').onclick=()=>$('spatialDialog').close();
$('spatialDialog').addEventListener('close',()=>{spatialRequest++;});
$('spatialDialog').addEventListener('click',event=>{if(event.target===$('spatialDialog')){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();}});
