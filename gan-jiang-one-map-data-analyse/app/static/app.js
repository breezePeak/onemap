'use strict';

/**
 * 第一阶段公共前端工具层（仅服务于 1·目录关联 与 2·冲突管理，不含任何业务数据生成或 Excel 制表逻辑）。
 */
const $ = s => document.querySelector(s);

const node = (tag, props = {}, children = []) => {
  const el = Object.assign(document.createElement(tag), props);
  for (const child of children) {
    if (child !== null && child !== undefined && child !== false) {
      el.append(child);
    }
  }
  return el;
};

let toastTimer;
const notify = (text, error = false) => {
  const el = $('#toast');
  if (!el) return;
  el.textContent = text;
  el.classList.toggle('error', error);
  el.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 3800);
};

const api = async (url, options = {}) => {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({ error: '响应不是有效 JSON' }));
  if (!res.ok) throw new Error(data.error || `请求失败（${res.status}）`);
  return data;
};

const openWindowsPath = async (rawPath, action = 'folder') => {
  try {
    const res = await api('/api/folder-mount/open-path', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: rawPath, action }),
    });
    notify(`已在资源管理器打开：${res.opened}`);
  } catch (e) {
    notify(e.message, true);
  }
};

const copyDirectoryPath = async (path, button) => {
  const reset = button.textContent;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(path);
    } else {
      const input = node('input', { value: path });
      document.body.append(input);
      input.select();
      document.execCommand('copy');
      input.remove();
    }
    button.textContent = '已复制';
    notify('目录路径已复制到剪贴板');
    setTimeout(() => { button.textContent = reset; }, 1600);
  } catch {
    notify('复制失败，请手动复制该路径', true);
  }
};
