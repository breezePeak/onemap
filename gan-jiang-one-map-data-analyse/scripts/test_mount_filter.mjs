import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {chromium} from 'playwright';

const source = readFileSync(new URL('../app/static/mount.js', import.meta.url), 'utf8');
let browser;
for (const options of [{}, {channel: 'msedge'}, {channel: 'chrome'}]) {
  try { browser = await chromium.launch(options); break; } catch { /* Try the next installed browser. */ }
}
if (!browser) throw new Error('No Chromium/Edge/Chrome browser available');
try {
  const page = await browser.newPage();
  await page.setContent(`<select id="mountCategoryFilters"><option value="all">全部项目</option><option value="review">需要审核</option><option value="unlinked">未关联</option><option value="review,unlinked">需要审核或未关联</option></select><input id="mountQuery"><div id="mountStageToggles"></div><div id="mountTable"></div><span id="mountPhase"></span><button id="mountSummary"></button><dialog id="mountStatsDialog"><button id="closeMountStats">关闭</button><div id="mountStatsChart"></div></dialog><span id="mountPage"></span><input id="mountPageInput"><button id="mountPageGo"></button><button id="mountPrev"></button><button id="mountNext"></button><button id="closeMountReview"></button>`);
  await page.addScriptTag({content: `
    const $ = id => document.querySelector(id);
    function node(tag, props = {}, children = []) { const el = Object.assign(document.createElement(tag), props); el.append(...children); return el; }
    function notify(message) { throw new Error(message); }
    async function api(url) {
      const category = new URL(url, 'http://localhost').searchParams.get('categories');
      window.lastStages = new URL(url, 'http://localhost').searchParams.get('stages');
      await new Promise(resolve => setTimeout(resolve, category === 'review' ? 100 : 5));
      return {phase: '完成', stage_config: [{stage_id: 'S1', name: '阶段一'}, {stage_id: 'S2', name: '阶段二'}], summary: {stage_projects: [{stage_id: 'S1', total: 3, linked: 2, unlinked: 1}]}, page: 1, pages: 1,
        rows: [{name: category, status: 'unlinked', stages: {}, links: []}]};
    }
  `});
  await page.addScriptTag({content: source});
  await page.waitForSelector('#mountTable table');
  assert.equal(await page.locator('#mountSummary').innerText(), 'S1: 3 / 1');
  await page.locator('#mountSummary').click();
  assert.equal(await page.locator('#mountStatsDialog').evaluate(el => el.open), true);
  assert.match(await page.locator('#mountStatsChart').innerText(), /总数 3 · 已关联 2 · 未关联 1/);
  await page.locator('#closeMountStats').click();
  await page.locator('#mountStageToggles input').nth(0).uncheck();
  await page.waitForFunction(() => window.lastStages === 'S2');
  await page.locator('#mountStageToggles input').nth(1).uncheck();
  await page.waitForFunction(() => window.lastStages === 'none');
  await page.locator('#mountStageToggles input').nth(0).check();
  await page.waitForFunction(() => window.lastStages === 'S1');
  assert.match(await page.locator('#mountTable').innerText(), /all/);
  await page.selectOption('#mountCategoryFilters', 'review');
  assert.equal(await page.locator('#mountTable').getAttribute('aria-busy'), 'true');
  assert.equal(await page.locator('#mountTable .spinner').count(), 1);
  assert.match(await page.locator('#mountTable').innerText(), /正在加载项目/);
  await page.selectOption('#mountCategoryFilters', 'unlinked');
  await page.waitForTimeout(200);
  assert.match(await page.locator('#mountTable').innerText(), /unlinked/);
  assert.doesNotMatch(await page.locator('#mountTable').innerText(), /review/);
  assert.equal(await page.locator('#mountTable').getAttribute('aria-busy'), 'false');
  assert.equal(await page.locator('#mountTable .spinner').count(), 0);
  await page.selectOption('#mountCategoryFilters', 'review,unlinked');
  await page.waitForFunction(() => document.querySelector('#mountTable').textContent.includes('review,unlinked'));
  console.log('Mount category selection and response ordering passed');
} finally {
  await browser.close();
}
