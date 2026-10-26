// 真实浏览器检查现有工作台；API 固定夹具，避免扫描或修改用户数据。
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile, mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const base=fileURLToPath(new URL('../',import.meta.url));
const modules=process.env.ONEMAP_NODE_MODULES||path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=createRequire(path.join(modules,'package.json'))('playwright');
const browser=await chromium.launch({headless:true,...(process.env.ONEMAP_BROWSER_EXECUTABLE?{executablePath:process.env.ONEMAP_BROWSER_EXECUTABLE}:{channel:process.env.ONEMAP_BROWSER_CHANNEL||'msedge'})});
try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],requests=[];
  page.on('pageerror',error=>errors.push(error.message));
  const stages=Array.from({length:7},(_,i)=>({stage_id:`S${i+1}`,name:`阶段${i+1}`}));
  const folders=stages.map(s=>({id:s.stage_id,name:s.name,stage:s.stage_id,folder:`F:/sample/${s.stage_id}`,paths:[`F:/sample/${s.stage_id}`]}));
  const link={id:'link',left:folders[0],right:folders[6],status:'pending',signature:'fixture',methods:['M1','M2'],evidence_count:2};
  const project={id:'row',name:'联合候选',status:'pending',stages:Object.fromEntries(folders.map(f=>[f.stage,[f]])),links:[link],evidence_count:2};
  const source={normalized_code:'2017-360199-27-03-014458',raw_code:'２０１７–３６０１９９–２７–０３–０１４４５８',stage:'S1',project_name:'阶段1',source_type:'body_field',source_path:'F:/sample/S1/材料.txt',location:'page=2',text_evidence:'项目代码：２０１７–３６０１９９–２７–０３–０１４４５８'};
  const evidence=[{method:'M1',normalized_code:source.normalized_code,left_sources:[source],right_sources:[{...source,stage:'S7'}],explanation:'完整项目代码一致，待审核'},
    {sha256:'a'.repeat(64),left:['F:/sample/S1/a.pdf'],right:['F:/sample/S7/a.pdf']}];
  await page.route('http://127.0.0.1:18767/**',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.pathname.startsWith('/api/')){
      requests.push({path:url.pathname,query:url.search,body:request.postDataJSON()});
      let data=[];
      if(url.pathname==='/api/folder-mount')data={status:'ready',phase:'完成',source_root:'F:/sample',stage_config:stages,stages:[],rows:[project],summary:{projects:51,project_pending:51},page:Number(url.searchParams.get('page')||1),pages:2,count:51,chains:[],projects:[],errors:[]};
      if(url.pathname==='/api/folder-mount/links/link'){
        const items=evidence.filter(e=>!url.searchParams.get('method')||url.searchParams.get('method')==='all'||(e.method||'M2')===url.searchParams.get('method'));
        data={evidence:items,page:1,pages:1,count:items.length};
      }
      if(url.pathname==='/api/folder-mount/decision')link.status=request.postDataJSON().status;
      await route.fulfill({json:data});return;
    }
    const filename=url.pathname==='/'?'index.html':url.pathname.slice(1);
    if(!['index.html','app.js','mount.js','spatial.js','style.css'].includes(filename)){await route.fulfill({status:404,body:''});return;}
    await route.fulfill({body:await readFile(path.join(base,'app/static',filename)),contentType:filename.endsWith('.js')?'application/javascript':filename.endsWith('.css')?'text/css':'text/html'});
  });
  await page.goto('http://127.0.0.1:18767/');
  await page.locator('.mount-review-trigger').waitFor();
  assert.equal(await page.locator('#mountTable th').count(),9);
  assert.equal(await page.locator('#mountPair option[value*="-"]').count(),21);
  await page.locator('#mountMethods [value="M2"]').uncheck();
  assert.equal(await page.locator('#mountScan').isDisabled(),true);
  await page.locator('#mountMethods [value="M1"]').check();
  assert.equal(await page.locator('#mountScan').isEnabled(),true);
  await page.locator('#mountScan').click();
  await page.waitForFunction(()=>!document.querySelector('#mountScan').disabled);
  assert.deepEqual(requests.find(r=>r.path.endsWith('/scan')).body.methods,['M1']);
  await page.locator('#mountMethods [value="M1"]').uncheck();
  await page.locator('#mountMethods [value="M2"]').check();
  await page.locator('#mountScan').click();
  await page.waitForFunction(()=>!document.querySelector('#mountScan').disabled);
  await page.locator('#mountMethods [value="M1"]').check();
  await page.locator('#mountScan').click();
  await page.waitForFunction(()=>!document.querySelector('#mountScan').disabled);
  assert.deepEqual(requests.filter(r=>r.path.endsWith('/scan')).map(r=>r.body.methods),[['M1'],['M2'],['M1','M2']]);
  await page.locator('#mountNext').click();
  await page.waitForFunction(()=>document.querySelector('#mountPage').innerText.includes('第 2/2 页'));
  assert.equal(await page.locator('#mountNext').isDisabled(),true);
  await page.locator('#mountPrev').click();
  await page.waitForFunction(()=>document.querySelector('#mountPage').innerText.includes('第 1/2 页'));
  await page.locator('.mount-review-trigger').click();
  await page.locator('.mount-dialog-stages-collapse').evaluate(el=>el.open=true);
  assert.equal(await page.locator('.mount-dialog-stage-item').count(),7);
  await page.locator('.mount-evidence').first().waitFor();
  assert.equal(await page.locator('.mount-relation-card').count(),1);
  assert.match(await page.locator('.mount-relation-header').innerText(),/M1 \+ M2/);
  assert.match(await page.locator('.mount-evidence').first().innerText(),/２０１７/);
  assert.match(await page.locator('.mount-evidence').first().innerText(),/page=2/);
  await page.locator('.mount-evidence').first().getByRole('button',{name:'查看来源'}).first().click();
  // 真正的页码只用于 PDF；文本材料仍显示原始位置，不伪造 PDF 页面。
  await page.locator('.mount-preview-text').waitFor();
  const evidenceMethod=page.locator('select[aria-label="证据方法"]');
  await evidenceMethod.selectOption('M2');
  await page.waitForFunction(()=>document.querySelectorAll('.mount-evidence').length===1&&document.querySelector('.mount-evidence-list').innerText.includes('SHA-256'));
  assert.equal(await page.locator('.mount-evidence').count(),1);
  await evidenceMethod.selectOption('M1');
  await page.waitForFunction(()=>document.querySelector('.mount-evidence-list').innerText.includes('M1 ·'));
  await page.getByRole('button',{name:'✓ 确认对应'}).click();
  await page.waitForFunction(()=>document.querySelector('.mount-relation-header').innerText.includes('已确认'));
  assert.equal(requests.find(r=>r.path.endsWith('/decision')).body.status,'confirmed');
  await mkdir(path.join(base,'work/m1-ui'),{recursive:true});
  await page.screenshot({path:path.join(base,'work/m1-ui/review.png'),fullPage:true});
  await page.locator('#closeMountReview').click();
  await page.locator('#mountMethodFilter').selectOption('M1');
  const filtered=page.waitForResponse(response=>response.url().includes('pair=S1-S7'));
  await page.locator('#mountPair').selectOption('S1-S7');
  await page.waitForFunction(()=>document.querySelector('#mountScan').textContent.includes('扫描'));
  await filtered;
  assert(requests.some(r=>r.query.includes('method=M1')&&r.query.includes('pair=S1-S7')));
  assert.deepEqual(errors,[]);
  console.log('PASS: 七阶段、21组筛选、三种方法参数、联合单行、分方法证据、原文位置及预览、人工审核、分页及筛选请求；无页面异常。');
}finally{await browser.close();}
