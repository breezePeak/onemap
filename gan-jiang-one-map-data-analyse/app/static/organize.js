'use strict';

let organizePage=1,organizePages=1,organizeRequest=0,organizeTimer;
const organizeLink=(path,label,action='folder')=>{
  const link=node('a',label,'manager-path clickable-path');link.href='#';link.title=path;
  link.onclick=e=>{e.preventDefault();openWindowsPath(path,action);};return link;
};
async function loadOrganize(){
  const request=++organizeRequest;
  const params=new URLSearchParams({q:$('organizeQuery').value,stage:$('organizeStage').value,kind:$('organizeKind').value,page:String(organizePage)});
  $('organizeList').setAttribute('aria-busy','true');
  try{
    const data=await api('/api/organize-review?'+params);
    if(request!==organizeRequest)return;
    const list=$('organizeList');list.replaceChildren();
    if(!data.available){
      organizePage=1;organizePages=1;
      $('organizePhase').textContent='尚无复核台账';$('organizeSummary').textContent='';
      list.append(node('p','尚未生成整理目录复核台账。请先使用整理目录技能盘点并复核资料。','organize-empty'));
    }else{
      const selected=$('organizeStage').value,select=$('organizeStage');
      select.replaceChildren(node('option',{value:'',textContent:'全部阶段'}));
      for(const stage of data.stages)select.append(node('option',{value:stage,textContent:stage}));
      select.value=selected;organizePage=data.page;organizePages=data.pages;
      $('organizePhase').textContent=`台账 ${data.created_at||'时间未记录'} · ${data.total_projects} 个项目`;
      $('organizeSummary').textContent=`发现问题 ${data.issue_projects} 个 · 当前筛选 ${data.count} 个`;
      for(const p of data.projects){
        const card=node('article',undefined,'organize-card');
        card.append(node('div',{className:'organize-card-title'},[node('strong',`${p.stage} · ${p.name}`),node('span',p.issues.length?'需要人工复核':'尚未完成核验','organize-badge')]));
        if(p.issues.length){const issues=node('ul',undefined,'organize-issues');for(const issue of p.issues)issues.append(node('li',issue));card.append(issues);}
        else card.append(node('p','尚未完成逐项目证据复核。','organize-muted'));
        if(p.observation)card.append(node('p',p.observation));
        const paths=node('div',undefined,'organize-paths');
        if(p.path)paths.append(node('span','整理目录：'),organizeLink(p.path,p.path));
        if(p.source)paths.append(node('span','原始来源：'),organizeLink(p.source,p.source));
        if(p.parent)paths.append(node('span','待核对上级：'),organizeLink(p.parent,p.parent));
        card.append(paths);
        if(p.evidence.length){const details=node('details');details.append(node('summary',`查看文书证据路径（${p.evidence.length}）`));for(const path of p.evidence)details.append(organizeLink(path,path,'select'));card.append(details);}
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
$('organizeRefresh').onclick=()=>loadOrganize();
$('organizePrev').onclick=()=>{organizePage=Math.max(1,organizePage-1);loadOrganize();};
$('organizeNext').onclick=()=>{organizePage=Math.min(organizePages,organizePage+1);loadOrganize();};
loadOrganize();
