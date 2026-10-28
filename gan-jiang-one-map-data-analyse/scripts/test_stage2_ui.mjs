import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {chromium} from 'playwright';

const root=path.resolve(import.meta.dirname,'..');
let browser;
for(const options of [{},{channel:'msedge'},{channel:'chrome'}]){try{browser=await chromium.launch(options);break;}catch{}}
if(!browser)throw Error('No browser available');
const candidate={id:'candidate',value:100,stage:'S5',business_stage:'土地供应',method:'PDF原生文字',path:'C:/source/contract.pdf',location:'page=2;block-1',record:'record',raw:'宗地面积：100平方米',basis:{directory_hint:true},reliability:'需复核',fingerprint:'sha',valid:true,excluded:false};
const field={code:'BI',name:'供地面积',group:'土地供应',original:88,value:88,status:'conflict',candidates:[candidate]};
const project={id:'project',name:'真实项目工作台交互测试',path:'C:/source/S5/project',fields:[field],history:[]};
const requests=[],errors=[];
try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url()),p=url.pathname;
    const json=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
    if(p==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'app/static/index.html'),'utf8')});
    if(['/app.js','/mount.js','/stage2.js','/organize.js','/spatial.js','/matching.js','/style.css'].includes(p))return route.fulfill({contentType:p.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(path.join(root,'app/static',p.slice(1)),'utf8')});
    if(p==='/api/jobs')return json([]);
    if(p==='/api/organize-review')return json({available:false,projects:[],count:0,page:1,pages:1,stages:[]});
    if(p==='/api/folder-mount')return json({stages:[],rows:[],summary:{projects:0,links:0},page:1,pages:1});
    if(p==='/api/stage2')return json({count:1,page:1,pages:1,running:false,projects:[{...project,original_fields:1,candidates:1,reviewed:0}]});
    if(p==='/api/stage2/projects/project')return json(project);
    if(p==='/api/stage2/spatial/project')return json({errors:[],evidence:[{record:'record',source:'C:/source/plot.shp',intersection_m2:50,geometry:{type:'Polygon',coordinates:[[[5,0],[15,0],[15,10],[5,10],[5,0]]]},anchor_geometry:{type:'Polygon',coordinates:[[[0,0],[10,0],[10,10],[0,10],[0,0]]]},intersection:{type:'Polygon',coordinates:[[[5,0],[10,0],[10,10],[5,10],[5,0]]]}}]});
    if(p==='/api/stage2/review'){
      const payload=route.request().postDataJSON();requests.push(payload);
      if(payload.action==='adopt'){field.value=100;field.status='selected';}
      else if(payload.action==='undo'){field.value=88;field.status='conflict';}
      else field.status=payload.action;
      project.history.push(payload);return json(project);
    }
    return route.fulfill({status:404,contentType:'application/json',body:'{"error":"not found"}'});
  });
  await page.goto('http://127.0.0.1:8766/');await page.getByRole('button',{name:'3 · 提取项目数据'}).click();
  await page.locator('#s2Projects button').click();
  await page.getByRole('button',{name:'计算 M3 空间关联候选'}).click();
  await page.waitForFunction(()=>document.querySelectorAll('#s2Detail svg path').length===3);
  await page.locator('#s2Detail summary').filter({hasText:'100 · 待核对'}).click();
  await page.getByRole('button',{name:'采用此值'}).click();
  assert.equal(requests.length,0,'不确认归属时不得发送采用');
  await page.locator('#s2Detail input[type=checkbox]').check();await page.getByRole('button',{name:'采用此值'}).click();
  await page.waitForFunction(()=>document.querySelector('#s2Detail').textContent.includes('人工已采用'));
  assert.equal(requests[0].confirm_identity,true);
  await page.getByRole('button',{name:'撤销审核'}).click();
  await page.waitForFunction(()=>document.querySelector('#s2Detail').textContent.includes('冲突保留原值'));
  assert.equal(requests.at(-1).action,'undo');assert.deepEqual(errors,[]);
  await page.screenshot({path:path.join(root,'work/stage2-workbench.png'),fullPage:true});
  console.log('Stage2 UI: source, identity gate, adoption, undo, history and module navigation passed');
}finally{await browser.close();}
