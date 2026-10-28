import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {chromium} from 'playwright';

const html=readFileSync(new URL('../app/static/index.html',import.meta.url),'utf8');
const tabs=[...html.matchAll(/data-module="([^"]+)"/g)].map(x=>x[1]);
assert.deepEqual(tabs,['organize','mount','extract','output']);
assert.match(html, /<select id="organizeKind"[^>]*>\s*<option value="all">/);
const source=readFileSync(new URL('../app/static/organize.js',import.meta.url),'utf8');
let browser;
for(const options of [{},{channel:'msedge'},{channel:'chrome'}]){
  try{browser=await chromium.launch(options);break;}catch{}
}
if(!browser)throw Error('No Chromium/Edge/Chrome browser available');
try{
  const page=await browser.newPage();
  await page.setContent(`<span id="organizePhase"></span><input id="organizeQuery"><select id="organizeKind"><option value="issues">问题</option><option value="all">全部</option></select><select id="organizeStage"><option value="">全部</option></select><button id="organizeRefresh"></button><div id="organizeList"></div><span id="organizeSummary"></span><span id="organizePage"></span><button id="organizePrev"></button><button id="organizeNext"></button>`);
  await page.addScriptTag({content:`
    const $=id=>document.getElementById(id);
    function node(tag,text,cls){const e=document.createElement(tag);if(text&&typeof text==='object'){Object.assign(e,text);for(const child of cls||[])e.append(child);return e;}if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
    let opened=[];function openWindowsPath(...args){opened.push(args);}
    async function api(url){const q=new URL(url,'http://localhost').searchParams;return {available:true,created_at:'test',total_projects:2,issue_projects:1,count:q.get('kind')==='issues'?1:2,page:1,pages:1,stages:['4.土地供应阶段'],projects:[{name:'甲',stage:'4.土地供应阶段',path:'F:/整理/甲',source:'F:/原始/甲',issues:['编号冲突'],parent:'F:/原始',evidence:['F:/原始/证据.pdf'],observation:'代码不一致'},...(q.get('kind')==='all'?[{name:'乙',stage:'4.土地供应阶段',path:'F:/整理/乙',source:'',issues:[],evidence:[]}]:[])]};}
  `});
  await page.addScriptTag({content:source});
  await page.waitForFunction(()=>document.querySelector('#organizeList').textContent.includes('编号冲突'));
  assert.match(await page.locator('#organizeSummary').innerText(),/发现问题 1 个/);
  assert.equal(await page.locator('.organize-card').count(),1);
  await page.locator('.organize-paths a').first().click();
  assert.deepEqual(await page.evaluate(()=>opened[0]),['F:/整理/甲','folder']);
  await page.selectOption('#organizeKind','all');
  await page.waitForFunction(()=>document.querySelectorAll('.organize-card').length===2);
  assert.match(await page.locator('#organizeList').innerText(),/尚未完成核验/);
  console.log('整理目录复核界面检查通过');
}finally{await browser.close();}
