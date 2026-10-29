import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {chromium} from 'playwright';

const html=readFileSync(new URL('../app/static/index.html',import.meta.url),'utf8');
const tabs=[...html.matchAll(/data-module="([^"]+)"/g)].map(x=>x[1]);
assert.deepEqual(tabs,['organize','mount','extract','output']);
assert.match(html, /<select id="organizeKind"[^>]*>\s*<option value="all">/);
assert.match(html, /id="organizeStatsTable"/);
assert.match(html, /id="organizeScore"/);
assert.match(html, /id="organizeCriteria"/);
const source=readFileSync(new URL('../app/static/organize.js',import.meta.url),'utf8');
let browser;
for(const options of [{},{channel:'msedge'},{channel:'chrome'}]){
  try{browser=await chromium.launch(options);break;}catch{}
}
if(!browser)throw Error('No Chromium/Edge/Chrome browser available');
try{
  const page=await browser.newPage();
  await page.setViewportSize({width:1600,height:900});
  await page.setContent(`
    <span id="organizePhase"></span>
    <input id="organizeQuery">
    <select id="organizeKind"><option value="issues">问题</option><option value="all">全部</option></select>
    <select id="organizeStage"><option value="">全部</option></select>
    <select id="organizeScore"><option value="">全部评分</option><option value="100">100%</option><option value="60">60%</option><option value="40">40%</option><option value="0">0%</option><option value="unscored">未评分</option></select>
    <button id="organizeRefresh"></button>
    <div class="organize-split">
      <aside id="organizeStatsPanel" class="organize-stats-panel">
        <div class="organize-stats-header"><h2 id="organizeStatsTitle">全量复核结果总览</h2></div>
        <div class="organize-stats-wrap">
          <table id="organizeStatsTable" class="organize-stats-table">
            <thead><tr><th>阶段目录</th><th>项目总数</th><th>100%</th><th>60%</th><th>40%</th><th>0%</th><th>未评分</th></tr></thead>
            <tbody id="organizeStatsBody"></tbody>
            <tfoot id="organizeStatsFoot"></tfoot>
          </table>
        </div>
        <div class="organize-criteria" id="organizeCriteria">
          <div class="organize-stats-header"><h2>置信度评分标准</h2></div>
          <ul class="organize-criteria-list">
            <li><span class="criteria-tag tier-100">100%</span><div><strong>目录结构清楚</strong><p>说明</p></div></li>
          </ul>
        </div>
      </aside>
      <div class="organize-main-panel">
        <div id="organizeList" class="organize-list"></div>
      </div>
    </div>
    <span id="organizeSummary"></span><span id="organizePage"></span><button id="organizePrev"></button><button id="organizeNext"></button>
  `);
  await page.addScriptTag({content:`
    const $=id=>document.getElementById(id);
    function node(tag,text,cls){const e=document.createElement(tag);if(text&&typeof text==='object'){Object.assign(e,text);for(const child of cls||[])e.append(child);return e;}if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
    let opened=[],testScore=60,testReason='材料未核完',testReviewedBy='';
    function openWindowsPath(...args){opened.push(args);}
    async function api(url,opts){
      if(url==='/api/organize-review/decision'&&opts?.method==='POST'){
        const body=JSON.parse(opts.body);
        testScore=body.score;
        if(body.reason)testReason=body.reason;
        testReviewedBy='manual';
        return {updated_id:body.id,score:testScore,reason:testReason,reviewed_by:'manual'};
      }
      const q=new URL(url,'http://localhost').searchParams;
      const allProjects=[
        {id:'p1',name:'甲',stage:'4.土地供应阶段',path:'F:/整理/甲',source:'F:/原始/甲',issues:['编号冲突'],parent:'F:/原始',evidence:['F:/原始/证据.pdf'],observation:'代码不一致',confidence_score:null},
        {id:'p2',name:'乙',stage:'4.土地供应阶段',path:'F:/整理/乙',source:'',issues:[],evidence:[],confidence_score:testScore,confidence_reason:testReason,reviewed_by:testReviewedBy}
      ];
      const scoreFilter=q.get('score')||'';
      const stageFilter=q.get('stage')||'';
      const kindFilter=q.get('kind')||'all';
      const filtered=allProjects.filter(p=>{
        if(kindFilter==='issues'&&!p.issues.length)return false;
        if(stageFilter&&p.stage!==stageFilter)return false;
        if(scoreFilter==='unscored')return p.confidence_score==null;
        if(scoreFilter)return String(p.confidence_score)===scoreFilter;
        return true;
      });
      return {
        available:true,created_at:'test',total_projects:2,issue_projects:1,
        count:filtered.length,page:1,pages:1,stages:['4.土地供应阶段'],
        summary_table:{
          rows:[{stage:'4.土地供应阶段',total:2,score_100:testScore===100?1:0,score_60:testScore===60?1:0,score_40:0,score_0:0,unscored:1}],
          totals:{stage:'合计',total:2,score_100:testScore===100?1:0,score_60:testScore===60?1:0,score_40:0,score_0:0,unscored:1}
        },
        projects:filtered
      };
    }
  `});
  await page.addStyleTag({content:readFileSync(new URL('../app/static/style.css',import.meta.url),'utf8')});
  await page.addScriptTag({content:source});
  await page.waitForFunction(()=>document.querySelector('#organizeList').textContent.includes('编号冲突'));
  assert.equal(await page.locator('.organize-card').first().locator('.organize-confidence').innerText(),'未评');
  assert.ok(await page.locator('.organize-card').first().locator('.organize-issues').isVisible());
  assert.ok(await page.locator('#organizeCriteria').isVisible());
  assert.match(await page.locator('#organizeSummary').innerText(),/发现问题 1 个/);
  assert.equal(await page.locator('.organize-card').count(),1);
  // 检查左侧表格约占屏幕宽 1/4
  const panelRect=await page.locator('#organizeStatsPanel').boundingBox();
  assert.ok(panelRect.width>=300 && panelRect.width<=460, `Expected left panel ~1/4 width, got ${panelRect.width}`);
  // 点击左侧表格中 60% 对应的数字按钮，右侧应过滤显示 60% 的项目“乙”
  await page.locator('#organizeStatsBody button[data-score="60"]').click();
  await page.waitForFunction(()=>document.querySelectorAll('.organize-card').length===1 && document.querySelector('#organizeList').textContent.includes('乙'));
  assert.equal(await page.locator('#organizeScore').inputValue(),'60');
  // 点击左侧表格“项目总数”数字按钮，右侧显示全部 2 个项目
  await page.locator('#organizeStatsBody button[data-score=""]').click();
  await page.waitForFunction(()=>document.querySelectorAll('.organize-card').length===2);
  await page.locator('.organize-paths a').first().click();
  assert.deepEqual(await page.evaluate(()=>opened[0]),['F:/整理/甲','folder']);
  assert.equal(await page.locator('.organize-card').nth(1).locator('.organize-card-status').innerText(),'60%');
  assert.match(await page.locator('.organize-card').nth(1).innerText(),/材料未核完/);
  // 测试人工审核功能：在项目“乙”卡片中输入审核意见并点击“100% 清楚”按钮
  await page.locator('.organize-card').nth(1).locator('.organize-audit-input').fill('人工确认目录材料完整');
  await page.locator('.organize-card').nth(1).locator('button[data-audit-score="100"]').click();
  await page.waitForFunction(()=>document.querySelectorAll('.organize-card')[1].querySelector('.organize-confidence').textContent==='100%');
  assert.ok(await page.locator('.organize-card').nth(1).locator('.organize-corner-triangle.tier-100').isVisible());
  assert.match(await page.locator('.organize-card').nth(1).innerText(),/已人工审核/);
  assert.match(await page.locator('.organize-card').nth(1).innerText(),/人工确认目录材料完整/);
  assert.doesNotMatch(await page.locator('.organize-card').nth(1).innerText(),/尚未完成逐项目证据复核/);
  let boxes=await page.locator('.organize-card').evaluateAll(cards=>cards.map(card=>card.getBoundingClientRect().toJSON()));
  assert.equal(boxes[0].y,boxes[1].y);
  assert.ok(boxes[1].x>boxes[0].x);
  await page.setViewportSize({width:400,height:900});
  boxes=await page.locator('.organize-card').evaluateAll(cards=>cards.map(card=>card.getBoundingClientRect().toJSON()));
  assert.ok(boxes[1].y>=boxes[0].bottom);
  assert.ok(boxes[0].right<=400);
  console.log('整理目录复核界面检查通过');
}finally{await browser.close();}
