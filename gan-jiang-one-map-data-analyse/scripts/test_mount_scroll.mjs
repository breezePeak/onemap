import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {chromium} from 'playwright';

const css = readFileSync(new URL('../app/static/style.css', import.meta.url), 'utf8');
let browser;
for (const options of [{}, {channel: 'msedge'}, {channel: 'chrome'}]) {
  try { browser = await chromium.launch(options); break; } catch { /* Try the next installed browser. */ }
}
if (!browser) throw new Error('No Chromium/Edge/Chrome browser available');
try {
  for (const width of [1440, 800]) {
    const page = await browser.newPage({viewport: {width, height: 800}});
    await page.setContent(`<style>${css}</style><dialog id="mountReviewDialog" open><header>项目关联核对</header><div class="mount-review-layout"><div class="mount-review-left">${Array.from({length: 6}, (_, i) => `<div class="mount-relation-card"><div class="mount-relation-header">S1 ↔ S${i + 2}</div><div class="mount-evidence-container"><div class="mount-evidence-pair"><button class="mount-file-card">左侧文件</button><button class="mount-file-card">右侧文件</button></div></div><div class="mount-relation-header-actions"><button>确认关联</button></div></div>`).join('')}</div><div class="mount-review-right"></div></div></dialog>`);
    const result = await page.locator('.mount-review-left').evaluate(el => {
      const cards = [...el.querySelectorAll('.mount-relation-card')];
      const unclipped = cards.every(card => card.scrollHeight <= card.clientHeight + 1);
      el.scrollTop = el.scrollHeight;
      return {unclipped, scrollTop: el.scrollTop, lastVisible: cards.at(-1).getBoundingClientRect().bottom <= el.getBoundingClientRect().bottom + 1};
    });
    assert(result.unclipped, `${width}px: 关联卡片内容不应被裁切`);
    assert(result.scrollTop > 0 && result.lastVisible, `${width}px: 应能滚动到最后一条关联`);
    await page.close();
  }
  console.log('Mount review scrolling passed');
} finally {
  await browser.close();
}
