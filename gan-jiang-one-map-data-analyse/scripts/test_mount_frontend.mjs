import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

class Element {
  children = [];
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
}
const node = (tag, props = {}, children = []) => {
  const element = Object.assign(new Element(), props);
  element.append(...children);
  return element;
};
const walk = element => element instanceof Element
  ? [element, ...element.children.flatMap(walk)] : [];
let evidence, pages = 1;
const urls = [], notices = [];
const opened = [];
const context = vm.createContext({node, api: async url => { urls.push(url); return {evidence, pages, page: 1, count: 21}; }, notify: message => notices.push(message), setTimeout: () => {}, openWindowsPath: (...args) => opened.push(args)});
const source = readFileSync(new URL('../app/static/mount.js', import.meta.url), 'utf8');
vm.runInContext(source.split('/* ==================== 事件绑定')[0] + '\nglobalThis.renderEvidence = mountEvidenceList; globalThis.renderRelation = mountRelation; globalThis.stateText = mountM1StateText; globalThis.methodLabel = mountMethodLabel; globalThis.validPage = validMountPage; globalThis.candidates = mountCandidates;', context);
assert.equal(context.validPage('3', 5), 3);
for (const value of ['', '0', '6', '1.5', '-1']) assert.equal(context.validPage(value, 5), null);
assert.equal(context.methodLabel(['M2']), '文件内容相同');
assert.equal(context.methodLabel(['M1']), '项目代码相同');
assert.equal(context.methodLabel(['M1', 'M2']), '项目代码相同 + 文件内容相同');
assert.equal(context.methodLabel([]), '未标明');
assert.equal(context.stateText('unresolved_ownership'), '项目代码归属不明');
assert.equal(context.stateText('missing_both'), '双方缺少项目代码证据');
const folders = Object.fromEntries(['a', 'b', 'x', 'y', 'z'].map(id => [id, {id, name: id}]));
const relation = (left, right, status = 'pending') => ({left: folders[left], right: folders[right], status});
const candidates = context.candidates({
  stages: {S1: [folders.a], S2: [folders.b]},
  links: [relation('a', 'b', 'confirmed'), relation('a', 'x'), relation('b', 'x', 'review'),
    relation('a', 'y', 'rejected'), relation('x', 'z')],
});
assert.equal(candidates.length, 1, '只显示当前项目直接相邻的未确认候选');
assert.equal(candidates[0].folder.id, 'x');
assert.equal(candidates[0].links.length, 2, '同一候选项目展示一次并保留直接关联证据');
const link = {id: 'relation', left: {stage: 'S1', name: '甲', folder: 'C:/甲'}, right: {stage: 'S2', name: '乙', folder: 'C:/乙'}};
const card = context.renderRelation({...link, methods: ['M2'], status: 'pending'});
assert.equal(card.children[0].children[0].textContent, 'S1 ↔ S2');
assert.equal(card.children[0].children[1].textContent, '文件内容相同');
assert.equal(card.children[1].className, 'mount-evidence-container');
assert(!walk(card).some(element => element.className === 'mount-relation-folders-inner'));
assert(!walk(card).some(element => element.textContent === '甲' || element.textContent === '乙'));
for (const method of ['M2', 'M1']) {
  const left = {physical_path: 'C:/materials/left.pdf', logical_path: 'archive.zip!left.pdf'};
  const right = {physical_path: 'C:/materials/right.pdf'};
  evidence = method === 'M2'
    ? [{sha256: 'a'.repeat(64), left_details: [left], right_details: [right]}]
    : [{method: 'M1', normalized_code: '2017-360199-27-03-014458', left_sources: [left], right_sources: [right]}];
  const box = new Element();
  await context.renderEvidence(link, box);
  const elements = walk(box);
  const stages = elements.filter(element => element.className === 'mount-side-title').map(element => element.children[0]);
  assert.equal(stages[0].textContent, 'S1');
  assert.equal(stages[0].title, 'C:/甲');
  assert.equal(stages[1].title, 'C:/乙');
  stages[1].onclick({preventDefault() {}, stopPropagation() {}});
  assert.deepEqual(opened.at(-1), ['C:/乙', 'folder']);
  const cards = elements.filter(element => element.className === 'mount-file-card');
  assert.equal(cards.length, 2, `${method} 必须展示双方原始证据`);
  assert.match(cards[0].title, /left\.pdf/);
  assert.match(cards[1].title, /right\.pdf/);
  if (method === 'M1') assert(elements.some(element => element.textContent?.includes(evidence[0].normalized_code)));
}
console.log('M1/M2 frontend evidence checks passed');
pages = 5;
const pagedBox = new Element();
await context.renderEvidence(link, pagedBox);
const pager = walk(pagedBox).find(element => element.className === 'mount-toolbar');
const pageInput = walk(pager).find(element => element.className === 'mount-page-input');
const go = walk(pager).find(element => element.textContent === '跳转');
pageInput.value = '4';
go.onclick();
assert.match(urls.at(-1), /page=4/);
pageInput.value = '6';
go.onclick();
assert.match(notices.at(-1), /1 到 5/);

const requests = [], messages = [];
let refreshes = 0;
const confirmation = vm.createContext({
  api: async (url, options) => {
    requests.push({url, ...JSON.parse(options.body)});
    if (requests.length === 2) throw new Error('证据已变化');
  },
  notify: message => messages.push(message),
  loadMount: async () => { refreshes++; },
});
const confirmSource = source.slice(source.indexOf('const confirmMountSelection ='), source.indexOf('const renderProjectMountTable ='));
vm.runInContext(confirmSource + '\nglobalThis.confirmSelected = confirmMountSelection;', confirmation);
await confirmation.confirmSelected([{id:'first', signature:'version-1'}, {id:'second', signature:'version-2'}, {id:'third', signature:'version-3'}], {});
assert.equal(requests.length, 2, '证据变更后停止，不能继续确认其余候选');
assert.equal(requests[0].status, 'confirmed');
assert.equal(requests[0].signature, 'version-1');
assert.match(messages[0], /已确认 1 条.*证据已变化/);
assert.equal(refreshes, 1);
console.log('Candidate selection and partial-confirmation checks passed');
