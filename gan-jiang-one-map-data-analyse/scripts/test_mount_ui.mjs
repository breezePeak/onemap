import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(import.meta.dirname, '..');
const html = fs.readFileSync(path.join(root, 'app/static/index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'app/static/style.css'), 'utf8');
const appJs = fs.readFileSync(path.join(root, 'app/static/app.js'), 'utf8');
const mountJs = fs.readFileSync(path.join(root, 'app/static/mount.js'), 'utf8');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'onemap-stage1-ui-'));
const sourceRoot = path.join(temp, 'source');
const stage1 = path.join(sourceRoot, '1.用地预审与选址');
const stage2 = path.join(sourceRoot, '2.条件出具');
const stage3 = path.join(sourceRoot, '3.批次报批阶段');
const stage4 = path.join(sourceRoot, '4.土地供应阶段');
const stage5 = path.join(sourceRoot, '5.土地首次登记');
const stage6 = path.join(sourceRoot, '6.用地规划');
const stage7 = path.join(sourceRoot, '7.工程规划');
const s1Folder = path.join(stage1, '项目甲-S1');
const s2Folder = path.join(stage2, '项目甲-S2');
const s4Folder = path.join(stage4, '项目甲-S4');
const s6Folder = path.join(stage6, '项目甲-S6');
const s7Folder = path.join(stage7, '项目甲-S7');
for (const dir of [s1Folder, s2Folder, stage3, s4Folder, stage5, s6Folder, s7Folder]) {
  fs.mkdirSync(dir, { recursive: true });
}
const pdfPath = path.join(s6Folder, '批复与代码.pdf');
fs.writeFileSync(pdfPath, '%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF');

const linkId = 'link-m1-m2';
let linkState = {
  id: linkId,
  signature: 'sig-joint-v1',
  method_signatures: { M2: 'sig-m2-v1', M1: 'sig-m1-v1' },
  method_statuses: { M2: 'pending', M1: 'pending' },
  status: 'review',
  methods: ['M1', 'M2'],
  evidence_count: 2,
  ambiguous: true,
  conflict: true,
  m1_validation: {
    state: 'conflict',
    reason: 'M1 项目代码证据指向另一宗地，与 M2 相同附件候选不一致',
    codes: ['2109-360090-04-01-312194'],
  },
  left: { id: 'dir-s6', stage: 'S6', name: '项目甲-S6', folder: s6Folder, kind: 'folder', files: 1, decision_source: 'agent', paths: [s6Folder] },
  right: { id: 'dir-s7', stage: 'S7', name: '项目甲-S7', folder: s7Folder, kind: 'folder', files: 1, decision_source: 'agent', boundary_review: true, boundary_review_reason: '含多项目共用附件', paths: [s7Folder] },
  confirmation: {},
};

const makeMountData = (urlObj) => {
  const view = urlObj.searchParams.get('view') || 'links';
  const method = urlObj.searchParams.get('method') || 'all';
  const status = urlObj.searchParams.get('status') || 'all';
  const q = (urlObj.searchParams.get('q') || '').toLowerCase();

  let links = [linkState];
  if (method === 'M1+M2') {
    links = links.filter(l => l.methods.includes('M1') && l.methods.includes('M2'));
  } else if (method !== 'all') {
    links = links.filter(l => l.methods.includes(method));
  }
  if (status !== 'all' && status !== 'unlinked') {
    links = links.filter(l => l.status === status);
  }
  if (q) {
    links = links.filter(l => JSON.stringify(l).toLowerCase().includes(q));
  }

  const base = {
    status: 'ready',
    phase: '完成',
    scanned_at: '2026-10-26T15:30:00',
    source_root: sourceRoot,
    methods: ['M2', 'M1'],
    stage_config: [
      { stage_id: 'S1', name: '1.用地预审与选址', title: '用地预审与选址' },
      { stage_id: 'S2', name: '2.条件出具', title: '条件出具' },
      { stage_id: 'S3', name: '3.批次报批阶段', title: '批次报批阶段' },
      { stage_id: 'S4', name: '4.土地供应阶段', title: '土地供应阶段' },
      { stage_id: 'S5', name: '5.土地首次登记', title: '土地首次登记' },
      { stage_id: 'S6', name: '6.用地规划', title: '用地规划' },
      { stage_id: 'S7', name: '7.工程规划', title: '工程规划' },
    ],
    stages: [
      { stage_id: 'S6', name: '6.用地规划', business_directories: 1, archives: 0, reused: 0, extracted: 0, failed: 0, valid_materials: 1, skipped: {} },
      { stage_id: 'S7', name: '7.工程规划', business_directories: 1, archives: 0, reused: 0, extracted: 0, failed: 0, valid_materials: 1, skipped: {} },
    ],
    projects: [
      { id: 'dir-s6', stage_id: 'S6', project_name: '项目甲-S6', project_root: '项目甲-S6', physical_path: s6Folder, decision_source: 'agent', reason: 'Agent语义判断独立项目', boundary_review: false },
      { id: 'dir-s7', stage_id: 'S7', project_name: '项目甲-S7', project_root: '项目甲-S7', physical_path: s7Folder, decision_source: 'agent', reason: 'Agent语义判断独立项目', boundary_review: true, boundary_review_reason: '含多项目共用附件' },
    ],
    m1_statistics: { projects_with_codes: 2, distinct_codes: 1, cross_stage_candidates: 1, conflicts: 1, unresolved_ownership: 1, invalid_evidence: 0 },
    links,
    unlinked: [],
    errors: [],
    chains: [],
    chains_truncated: false,
    summary: {
      folders: 2,
      links: links.length,
      confirmed: links.filter(l => l.status === 'confirmed').length,
      pending: links.filter(l => l.status === 'pending').length,
      review: links.filter(l => l.status === 'review').length,
      unlinked: 0,
      projects: 1,
      project_confirmed: links.every(l => l.status === 'confirmed') ? 1 : 0,
      project_pending: links.some(l => l.status === 'pending') ? 1 : 0,
      project_review: links.some(l => l.status === 'review') ? 1 : 0,
      project_unlinked: 0,
    },
    page: 1,
    pages: 1,
    count: links.length,
  };

  if (view === 'projects') {
    base.rows = links.length ? [{
      id: 'project-1',
      title: '项目甲-S6',
      anchor_stage: 'S6',
      status: linkState.status,
      unlinked: false,
      ambiguous: true,
      conflict: true,
      methods: ['M1', 'M2'],
      project_codes: ['2109-360090-04-01-312194'],
      unresolved_codes: ['2201-360100-04-01-999999'],
      evidence_count: 2,
      stages: { S1: [], S2: [], S3: [], S4: [], S5: [], S6: [linkState.left], S7: [linkState.right] },
      links: [linkState],
    }] : [];
  }
  return base;
};

const launchBrowser = async () => {
  for (const opts of [{}, { channel: 'msedge' }, { channel: 'chrome' }]) {
    try {
      return await chromium.launch(opts);
    } catch {
      // try next
    }
  }
  throw new Error('No Chromium/Edge/Chrome browser available for Playwright');
};

const browser = await launchBrowser();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.route('**/*', async route => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;

    // 验证第一阶段已禁用的接口返回 403
    if (
      p === '/api/folder-mount/scan' ||
      p.startsWith('/api/jobs') ||
      p === '/api/current' ||
      p.startsWith('/api/datafiles') ||
      p.startsWith('/api/storage')
    ) {
      return route.fulfill({
        status: 403,
        contentType: 'application/json',
        body: JSON.stringify({ error: '第一阶段仅开放目录关联与冲突管理功能，业务数据自动生产、Excel制表与前端触发扫描接口已禁用。' }),
      });
    }

    if (p === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
    if (p === '/style.css') return route.fulfill({ contentType: 'text/css; charset=utf-8', body: css });
    if (p === '/app.js') return route.fulfill({ contentType: 'application/javascript; charset=utf-8', body: appJs });
    if (p === '/mount.js') return route.fulfill({ contentType: 'application/javascript; charset=utf-8', body: mountJs });

    if (p === '/api/folder-mount') {
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(makeMountData(url)) });
    }
    if (p === '/api/folder-mount/conflicts') {
      const ctype = url.searchParams.get('conflict_type') || 'all';
      const item = {
        item_type: 'link',
        id: linkState.id,
        conflict_types: ['m1_m2_conflict', 'multi_parcel_same_code', 'shared_attachment', 'boundary_review'],
        conflict_labels: ['M1和M2证据矛盾', '相同代码涉及多个宗地', '多个目录共用相同附件', '项目文件夹边界存在疑问'],
        conflict_reasons: [linkState.m1_validation.reason, '关联双方存在项目文件夹边界疑问'],
        status: linkState.status,
        methods: linkState.methods,
        pair: 'S6-S7',
        stages: ['S6', 'S7'],
        left: linkState.left,
        right: linkState.right,
        signature: linkState.signature,
        evidence_count: 2,
        m1_validation: linkState.m1_validation,
        confirmation: linkState.confirmation,
        link: linkState,
      };
      const items = (ctype === 'all' || item.conflict_types.includes(ctype) || (ctype === 'audited' && linkState.confirmation?.status)) ? [item] : [];
      return route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          items,
          count: items.length,
          total_items: 1,
          page: 1,
          pages: 1,
          counts: {
            m1_m2_conflict: 1,
            unresolved_ownership: 1,
            multi_parcel_same_code: 1,
            boundary_review: 1,
            shared_attachment: 1,
            stale_confirmation: 0,
            audited: linkState.confirmation?.status ? 1 : 0,
          },
        }),
      });
    }
    if (p === `/api/folder-mount/links/${linkId}`) {
      return route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          id: linkId,
          signature: linkState.signature,
          confirmation: linkState.confirmation,
          page: 1,
          pages: 1,
          count: 2,
          evidence: [
            {
              method: 'M1',
              canonical_code: '2109-360090-04-01-312194',
              code_type: '投资项目统一代码',
              weight: 100,
              left_source_path: pdfPath,
              right_source_path: pdfPath,
              left_page: 2,
              right_page: 2,
              left_field_name: '项目代码',
              right_field_name: '项目代码',
              left_snippet: '项目代码：2109-360090-04-01-312194',
              right_snippet: '项目代码：2109-360090-04-01-312194',
            },
            {
              method: 'M2',
              sha256: 'abc1234567890def',
              left_files: [{ physical_path: pdfPath, logical_path: pdfPath, archive_chain: [] }],
              right_files: [{ physical_path: pdfPath, logical_path: pdfPath, archive_chain: [] }],
            },
          ],
        }),
      });
    }
    if (p === '/api/folder-mount/code-evidence') {
      return route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          page: 1,
          pages: 1,
          count: 1,
          evidence: [{
            stage_id: 'S6',
            directory_id: 'dir-s6',
            source_path: pdfPath,
            rel_path: '项目甲-S6/批复与代码.pdf',
            page: 2,
            location: 'pdf_page:2',
            field_name: '项目代码',
            raw_code: '2109-360090-04-01-312194',
            canonical_code: '2109-360090-04-01-312194',
            code_type: '投资项目统一代码',
            weight: 100,
            ownership_status: 'unresolved_ownership',
            needs_review: true,
            reason: '同一目录发现多个项目代码，归属待核实',
            snippet: '项目代码：2109-360090-04-01-312194',
          }],
        }),
      });
    }
    if (p === '/api/folder-mount/decision') {
      const body = JSON.parse(req.postData() || '{}');
      const prev = linkState.confirmation?.status ? [{ ...linkState.confirmation, history: undefined }] : [];
      const history = [...(linkState.confirmation?.history || []), ...prev];
      linkState = {
        ...linkState,
        status: body.status,
        confirmation: {
          status: body.status,
          signature: body.signature,
          updated_at: '2026-10-26T15:40:00',
          actor: '本机用户',
          history,
        },
      };
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(makeMountData(url)) });
    }
    if (p === '/api/folder-mount/file') {
      return route.fulfill({ contentType: 'application/pdf', body: fs.readFileSync(pdfPath) });
    }
    return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'not found' }) });
  });

  await page.goto('http://127.0.0.1:8766/');

  // 1. 验证前端只保留两个核心视图，且已移除所有无关模块与扫描按钮
  const tabTexts = await page.locator('.module-tabs button').allTextContents();
  assert.deepEqual(tabTexts, ['1 · 目录关联', '2 · 冲突管理']);
  assert.equal(await page.locator('#mountScan').count(), 0, '前端不应存在扫描按钮');
  assert.equal(await page.locator('#mountMethods').count(), 0, '前端不应存在扫描方法勾选框');
  assert.equal(await page.locator('#extractModule').count(), 0, '前端不应存在提取项目数据模块');
  assert.equal(await page.locator('#outputModule').count(), 0, '前端不应存在数据制表模块');
  assert.equal(await page.locator('#managerDialog').count(), 0, '前端不应存在任务与数据管理弹窗');
  assert.equal(await page.locator('#spatialDialog').count(), 0, '前端不应存在空间叠加弹窗');

  // 2. 验证后台禁用接口返回 403
  const forbiddenStatuses = await page.evaluate(async () => {
    const urls = [
      ['/api/folder-mount/scan', { method: 'POST', body: '{}' }],
      ['/api/jobs', { method: 'POST', body: '{}' }],
      ['/api/current', { method: 'POST', body: '{}' }],
      ['/api/jobs', { method: 'GET' }],
      ['/api/datafiles', { method: 'GET' }],
      ['/api/storage', { method: 'GET' }],
    ];
    return Promise.all(urls.map(([u, opt]) => fetch(u, opt).then(r => r.status)));
  });
  assert.deepEqual(forbiddenStatuses, [403, 403, 403, 403, 403, 403]);

  // 3. 验证 1·目录关联 视图：候选关系不被展示成已确认关系，支持 M1+M2 筛选，支持 PDF 页码预览
  await page.waitForSelector('#mountTable table');
  const tableText = await page.locator('#mountTable').innerText();
  assert.match(tableText, /项目甲-S6/);
  assert.match(tableText, /待复核候选/);
  assert.doesNotMatch(tableText, /✓ 已人工确认/);

  await page.selectOption('#mountMethodFilter', 'M1+M2');
  await page.waitForTimeout(50);
  assert.match(await page.locator('#mountTable').innerText(), /项目甲-S6/);

  await page.click('button:has-text("核对关系与证据")');
  await page.waitForSelector('#mountReviewDialog[open]');
  await page.waitForSelector('#mountReviewLeft .mount-evidence');
  assert.match(await page.locator('#mountReviewLeft').innerText(), /2109-360090-04-01-312194/);
  await page.waitForSelector('#mountReviewRight iframe');
  assert.match(await page.locator('#mountReviewRight iframe').getAttribute('src'), /#page=2$/);
  await page.click('#closeMountReview');

  // 4. 验证 2·冲突管理 视图：展示冲突原因、双方目录，支持确认、排除、标记复核、撤销决定与查看审核历史
  await page.click('.module-tabs button[data-module="conflicts"]');
  await page.waitForSelector('#conflictList .conflict-card');
  const conflictText = await page.locator('#conflictList').innerText();
  assert.match(conflictText, /M1和M2证据矛盾/);
  assert.match(conflictText, /相同代码涉及多个宗地/);
  assert.match(conflictText, /多个目录共用相同附件/);
  assert.match(conflictText, /项目文件夹边界存在疑问/);

  // 确认关联 -> 状态变为 ✓ 已人工确认，且生成审核历史
  await page.click('#conflictList button:has-text("✓ 确认关联")');
  await page.waitForTimeout(80);
  assert.match(await page.locator('#conflictList').innerText(), /✓ 已人工确认/);
  assert.match(await page.locator('#conflictList').innerText(), /查看审核历史/);

  // 撤销之前的人工决定 -> 状态恢复为 待确认候选，且历史记录保留
  await page.click('#conflictList button:has-text("↺ 撤销人工决定")');
  await page.waitForTimeout(80);
  assert.match(await page.locator('#conflictList').innerText(), /待确认候选/);
  assert.match(await page.locator('#conflictList').innerText(), /查看审核历史（共 2 条记录/);

  console.log('UI_STAGE1_CONVERGENCE_TEST_OK');
} finally {
  await browser.close();
  fs.rmSync(temp, { recursive: true, force: true });
}
