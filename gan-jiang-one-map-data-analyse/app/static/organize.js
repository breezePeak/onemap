'use strict';

let organizePage=1,organizePages=1,organizeRequest=0,organizeTimer;
const ORGANIZE_STAT_COLS=[
  {key:'total',score:'',label:'项目总数'},
  {key:'score_100',score:'100',label:'100%'},
  {key:'score_60',score:'60',label:'60%'},
  {key:'score_40',score:'40',label:'40%'},
  {key:'score_0',score:'0',label:'0%'},
  {key:'unscored',score:'unscored',label:'未评分'}
];
const ORGANIZE_AUDIT_OPTS=[
  {score:100,label:'100% 清楚',cls:'tier-100'},
  {score:60,label:'60% 存疑',cls:'tier-60'},
  {score:40,label:'40% 混杂',cls:'tier-40'},
  {score:0,label:'0% 空目录',cls:'tier-0'}
];
const organizeLink=(path,label,action='folder')=>{
  const link=node('a',label,'manager-path clickable-path');link.href='#';link.title=path;
  link.onclick=e=>{e.preventDefault();openWindowsPath(path,action);};return link;
};
function applyOrganizeStatFilter(stageValue,scoreValue){
  $('organizeStage').value=stageValue;
  $('organizeScore').value=scoreValue;
  $('organizeKind').value='all';
  organizePage=1;
  loadOrganize();
}
function buildOrganizeStatRow(row,stageFilterValue,currentStage,currentScore){
  const tr=node('tr');
  tr.append(node('td',row.stage));
  for(const col of ORGANIZE_STAT_COLS){
    const val=Number(row[col.key]??0);
    const isActive=currentStage===stageFilterValue&&currentScore===col.score;
    const cls=['organize-stat-btn',val===0?'is-zero':'',isActive?'active':''].filter(Boolean).join(' ');
    const btn=node('button',{type:'button',className:cls,textContent:String(val)});
    btn.dataset.stage=stageFilterValue;
    btn.dataset.score=col.score;
    btn.title=`查看 ${row.stage} · ${col.label}（${val} 项）`;
    btn.onclick=()=>applyOrganizeStatFilter(stageFilterValue,col.score);
    const td=node('td');
    td.append(btn);
    tr.append(td);
  }
  return tr;
}
function renderOrganizeSummaryTable(summaryTable,currentStage,currentScore,totalProjects){
  const tbody=$('organizeStatsBody'),tfoot=$('organizeStatsFoot'),title=$('organizeStatsTitle');
  if(!tbody||!tfoot)return;
  tbody.replaceChildren();
  tfoot.replaceChildren();
  if(title)title.textContent=`全量复核结果总览（${totalProjects??0} 项）`;
  if(!summaryTable||!Array.isArray(summaryTable.rows))return;
  for(const row of summaryTable.rows){
    tbody.append(buildOrganizeStatRow(row,row.stage,currentStage,currentScore));
  }
  if(summaryTable.totals){
    tfoot.append(buildOrganizeStatRow(summaryTable.totals,'',currentStage,currentScore));
  }
}
async function submitOrganizeDecision(projectId,score,reason,btn){
  if(btn)btn.disabled=true;
  try{
    await api('/api/organize-review/decision',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:projectId,score,reason})
    });
    if(typeof toast==='function')toast('人工复核结果已保存');
    await loadOrganize();
  }catch(error){
    if(typeof toast==='function')toast(error.message,true);
  }finally{
    if(btn)btn.disabled=false;
  }
}
async function loadOrganize(){
  const request=++organizeRequest;
  const scoreEl=$('organizeScore');
  const currentScore=scoreEl?scoreEl.value:'';
  const params=new URLSearchParams({
    q:$('organizeQuery').value,
    stage:$('organizeStage').value,
    kind:$('organizeKind').value,
    score:currentScore,
    page:String(organizePage)
  });
  $('organizeList').setAttribute('aria-busy','true');
  try{
    const data=await api('/api/organize-review?'+params);
    if(request!==organizeRequest)return;
    const list=$('organizeList');list.replaceChildren();
    if(!data.available){
      organizePage=1;organizePages=1;
      $('organizePhase').textContent='尚无复核台账';$('organizeSummary').textContent='';
      renderOrganizeSummaryTable(null,'','',0);
      list.append(node('p','尚未生成整理目录复核台账。请先使用整理目录技能盘点并复核资料。','organize-empty'));
    }else{
      const selected=$('organizeStage').value,select=$('organizeStage');
      select.replaceChildren(node('option',{value:'',textContent:'全部阶段'}));
      for(const stage of data.stages)select.append(node('option',{value:stage,textContent:stage}));
      select.value=selected;organizePage=data.page;organizePages=data.pages;
      renderOrganizeSummaryTable(data.summary_table,select.value,currentScore,data.total_projects);
      $('organizePhase').textContent=`台账 ${data.created_at||'时间未记录'} · ${data.total_projects} 个项目`;
      $('organizeSummary').textContent=`发现问题 ${data.issue_projects} 个 · 当前筛选 ${data.count} 个`;
      for(const p of data.projects){
        const card=node('article',undefined,'organize-card');
        const scoreText=p.confidence_score==null?'未评':`${p.confidence_score}%`;
        const tierCls=p.confidence_score===100?'tier-100':p.confidence_score===60?'tier-60':p.confidence_score===40?'tier-40':'tier-0';
        card.append(node('div',{className:'organize-card-title'},[
          node('strong',`${p.stage} · ${p.name}`),
          node('div',{className:`organize-card-status organize-corner-triangle ${tierCls}`,title:`归属置信度：${p.confidence_score==null?'无法估计':p.confidence_score+'%'}`},[
            node('span',scoreText,'organize-confidence')
          ])
        ]));
        card.append(node('p',p.confidence_reason || '尚无足够的逐项目证据','organize-muted'));
        if(p.confidence_scope)card.append(node('p',`核查范围：${p.confidence_scope}`,'organize-muted'));
        if(p.issues.length){const issues=node('ul',undefined,'organize-issues');for(const issue of p.issues)issues.append(node('li',issue));card.append(issues);}
        else if(p.confidence_score!==100)card.append(node('p','尚未完成逐项目证据复核。','organize-muted'));
        if(p.observation)card.append(node('p',p.observation));
        const paths=node('div',undefined,'organize-paths');
        if(p.path)paths.append(node('span','整理目录：'),organizeLink(p.path,p.path));
        if(p.source)paths.append(node('span','原始来源：'),organizeLink(p.source,p.source));
        if(p.parent)paths.append(node('span','待核对上级：'),organizeLink(p.parent,p.parent));
        card.append(paths);
        if(p.evidence.length){const details=node('details');details.append(node('summary',`查看文书证据路径（${p.evidence.length}）`));for(const path of p.evidence)details.append(organizeLink(path,path,'select'));card.append(details);}
        const auditBar=node('div',undefined,'organize-audit-bar');
        const auditBtns=node('div',undefined,'organize-audit-actions');
        const statusTag=node('span',p.reviewed_by==='manual'?'已人工审核':'人工审核：',p.reviewed_by==='manual'?'organize-audit-tag is-manual':'organize-audit-tag');
        auditBtns.append(statusTag);
        const reasonInput=node('input',{type:'text',className:'organize-audit-input',value:p.confidence_reason||'',placeholder:'输入或修改审核意见…'});
        for(const opt of ORGANIZE_AUDIT_OPTS){
          const isCurrent=p.confidence_score===opt.score;
          const btn=node('button',{
            type:'button',
            className:`organize-audit-btn ${opt.cls}${isCurrent?' active':''}`,
            textContent:opt.label
          });
          btn.dataset.auditScore=String(opt.score);
          btn.onclick=()=>submitOrganizeDecision(p.id,opt.score,reasonInput.value,btn);
          auditBtns.append(btn);
        }
        const saveBtn=node('button',{type:'button',className:'secondary organize-audit-save',textContent:'保存意见'});
        saveBtn.onclick=()=>submitOrganizeDecision(p.id,p.confidence_score??100,reasonInput.value,saveBtn);
        const reasonRow=node('div',{className:'organize-audit-reason'},[reasonInput,saveBtn]);
        auditBar.append(auditBtns,reasonRow);
        card.append(auditBar);
        list.append(card);
      }
      if(!data.projects.length)list.append(node('p','没有匹配的项目。','organize-empty'));
    }
    $('organizePage').textContent=`${organizePage} / ${organizePages}`;
    $('organizePrev').disabled=!data.available||organizePage<=1;
    $('organizeNext').disabled=!data.available||organizePage>=organizePages;
  }catch(error){if(request===organizeRequest){$('organizePhase').textContent='读取失败';$('organizeSummary').textContent='';$('organizePrev').disabled=true;$('organizeNext').disabled=true;$('organizeList').replaceChildren(node('p',error.message,'organize-empty'));}}
  finally{if(request===organizeRequest)$('organizeList').setAttribute('aria-busy','false');}
}
function refreshOrganize(){organizePage=1;loadOrganize();}
$('organizeQuery').oninput=()=>{clearTimeout(organizeTimer);organizeTimer=setTimeout(refreshOrganize,300);};
$('organizeKind').onchange=refreshOrganize;
$('organizeStage').onchange=refreshOrganize;
if($('organizeScore'))$('organizeScore').onchange=refreshOrganize;
$('organizeRefresh').onclick=()=>loadOrganize();
$('organizePrev').onclick=()=>{organizePage=Math.max(1,organizePage-1);loadOrganize();};
$('organizeNext').onclick=()=>{organizePage=Math.max(1,Math.min(organizePages,organizePage+1));loadOrganize();};
loadOrganize();
