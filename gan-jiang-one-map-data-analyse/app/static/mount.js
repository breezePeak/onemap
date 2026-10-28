'use strict';

let mountPage = 1;
let mountStageConfig = [];
let mountData = null;
let mountRequest = 0;
const hiddenMountStages = new Set();
const validMountPage = (value, pages) => /^\d+$/.test(String(value)) && Number(value) >= 1 && Number(value) <= pages ? Number(value) : null;

const mountStatusText = status => ({
  confirmed: '✓ 已人工确认',
  pending: '待确认候选',
  review: '待复核候选',
  rejected: '✕ 已人工排除',
  unlinked: '未关联目录',
  unscanned: '待挂接（当前仅识别目录）',
}[status] || status);

const mountMethodLabel = methods => {
  const list = Array.isArray(methods) && methods.length ? methods : ['未标明'];
  return list.map(method => ({ M1: '项目代码相同', M2: '文件内容相同' }[method] || method)).join(' + ');
};

const mountM1StateText = state => ({
  corroborated: '项目代码一致印证', conflict: '项目代码冲突',
  missing_left: '左侧缺少项目代码证据', missing_right: '右侧缺少项目代码证据',
  missing_both: '双方缺少项目代码证据', unresolved_ownership: '项目代码归属不明',
}[state] || state);

const renderAuditHistory = confirmation => {
  const conf = confirmation || {};
  const history = Array.isArray(conf.history) ? conf.history : [];
  const entries = [];
  if (conf.status && conf.updated_at) {
    entries.push({ ...conf, is_current: true });
  }
  for (let i = history.length - 1; i >= 0; i--) {
    entries.push({ ...history[i], is_current: false });
  }
  if (!entries.length) {
    return node('div', { style: 'font-size:11px;color:#6b7280;margin-top:4px;' }, ['暂无人工审核历史记录']);
  }
  return node('details', { className: 'audit-history-box' }, [
    node('summary', {}, [`查看审核历史（共 ${entries.length} 条记录，绑定证据版本）`]),
    node('ul', { className: 'audit-history-list' }, entries.map(item =>
      node('li', {}, [
        node('strong', {}, [item.is_current ? '[当前决定] ' : '[历史记录] ']),
        `${mountStatusText(item.status)} · 操作人：${item.actor || '本机用户'} · 时间：${item.updated_at || '-'} · 证据版本签名：`,
        node('code', {}, [String(item.signature || '').slice(0, 12) || '-']),
      ])
    )),
  ]);
};

const mountStageName = stageId => {
  const found = mountStageConfig.find(s => s.stage_id === stageId);
  return found ? `${stageId} ${found.title || found.name}` : stageId;
};

const mountPath = (folder, label = folder.folder) => node('a', {
  href: '#', className: 'manager-path clickable-path', textContent: label,
  title: folder.folder,
  onclick: event => { event.preventDefault(); event.stopPropagation(); openWindowsPath(folder.folder, 'folder'); },
});
const mountFolderNode = folder => node('div', {}, [
  node('strong', { textContent: `${folder.stage} · ${folder.name}` }), mountPath(folder),
]);

const getFileExt = p => {
  const m = String(p || '').toLowerCase().match(/(\.[a-z0-9]+)$/);
  return m ? m[1] : '';
};

const renderFilePreview = (container, fileInfo, stageLabel) => {
  container.replaceChildren();
  const realPath = fileInfo.physical_path || fileInfo.logical_path || '';
  const ext = getFileExt(realPath);
  const page = fileInfo.page || fileInfo.source_location?.match(/page=(\d+)/)?.[1];
  const isDirectory = ['project_directory', 'subdirectory'].includes(fileInfo.source_type);
  const pageSuffix = page ? `#page=${page}` : '';
  const previewUrl = `/api/folder-mount/file?path=${encodeURIComponent(realPath)}${pageSuffix}`;
  const downloadUrl = `/api/folder-mount/file?path=${encodeURIComponent(realPath)}&download=1`;

  const downloadLink = node('a', {
    className: 'secondary mount-action-link',
    href: downloadUrl,
    target: '_blank',
    rel: 'noopener',
    textContent: '⬇️ 下载原文件',
  });

  const head = node('div', { className: 'mount-preview-head' }, [
    node('div', { className: 'mount-preview-title' }, [
      node('span', { className: 'mount-stage-badge', textContent: stageLabel || '证据文件' }),
      node('strong', { textContent: realPath.split(/[\\/!]/).pop() || realPath }),
      page ? node('span', { className: 'mount-sha-tag', textContent: `第 ${page} 页` }) : '',
    ]),
    node('div', { className: 'manager-path clickable-path', textContent: realPath, title: '点击在资源管理器中定位该文件' }),
    node('div', { className: 'mount-preview-actions' }, isDirectory ? [] : [downloadLink]),
  ]);
  head.querySelector('.manager-path').onclick = () => openWindowsPath(realPath, 'select');

  const body = node('div', { className: 'mount-preview-body' });

  if (isDirectory) {
    body.append(node('p', { textContent: '此代码证据来自目录名称，可打开目录核对所属项目。' }));
  } else if (ext === '.pdf') {
    body.append(node('iframe', { className: 'mount-preview-frame', src: previewUrl, title: 'PDF 证据预览' }));
  } else if (['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg'].includes(ext)) {
    body.append(node('div', { className: 'mount-preview-img-wrap' }, [
      node('img', { className: 'mount-preview-image', src: previewUrl, alt: realPath }),
    ]));
  } else if (['.txt', '.csv', '.json', '.md', '.xml', '.prj', '.cpg', '.log'].includes(ext)) {
    const pre = node('pre', { className: 'mount-preview-text', textContent: '正在加载文件内容...' });
    body.append(pre);
    fetch(previewUrl).then(r => r.ok ? r.text() : Promise.reject(new Error('读取失败'))).then(txt => {
      pre.textContent = txt.slice(0, 100000) || '（文件内容为空）';
    }).catch(err => {
      pre.textContent = `无法直接读取文本：${err.message}`;
    });
  } else {
    body.append(node('div', { className: 'mount-preview-unsupported' }, [
      node('h3', { textContent: `${realPath.split(/[\\/!]/).pop()} (${ext || '未知格式'})` }),
      node('p', { textContent: '该格式（如 Word / Excel / CAD / 矢量图层）不支持浏览器内直接渲染，请使用本机程序打开或定位到目录查看。' }),
      node('div', { className: 'mount-preview-Actions-large' }, [
        node('button', { className: 'primary', type: 'button', textContent: '🖥️ 直接用本机程序打开', onclick: () => openWindowsPath(realPath, 'open') }),
      ]),
    ]));
  }

  container.append(head, body);
};

const getFileTypeIcon = filename => {
  const ext = getFileExt(filename);
  if (ext === '.pdf') return '📕';
  if (['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'].includes(ext)) return '🖼️';
  if (['.doc', '.docx'].includes(ext)) return '📘';
  if (['.xls', '.xlsx', '.csv'].includes(ext)) return '📗';
  if (['.dwg', '.dxf', '.shp'].includes(ext)) return '📐';
  return '📄';
};

const renderEvidenceFileCard = (fileObj, stageLabel, previewPane) => {
  const realPath = fileObj.physical_path || fileObj.logical_path || '';
  const fileName = realPath.split(/[\\/!]/).pop() || realPath;
  const card = node('button', { className: 'mount-file-card', type: 'button', title: `完整路径：${realPath}` });
  card.onclick = () => {
    document.querySelectorAll('.mount-file-card.active').forEach(el => el.classList.remove('active'));
    card.classList.add('active');
    if (previewPane) renderFilePreview(previewPane, fileObj, stageLabel);
  };

  card.append(
    node('div', { className: 'mount-file-main' }, [
      node('span', { className: 'mount-file-icon', textContent: getFileTypeIcon(fileName) }),
      node('span', { className: 'mount-file-name', textContent: fileName }),
    ]),
    fileObj.source_location ? node('small', { textContent: `${fileObj.source_location} ${fileObj.evidence_text || ''}` }) : ''
  );
  return card;
};

const mountEvidenceList = (link, box, page = 1, q = '', previewPane = null, method = 'all', autoPreview = true) => api(`/api/folder-mount/links/${link.id}?page=${page}&q=${encodeURIComponent(q)}&method=${encodeURIComponent(method)}`).then(data => {
  box.replaceChildren(...data.evidence.map((e, idx) => {
    if (e.method === 'M1' || e.canonical_code) {
      const leftCards = (e.left_sources || []).map(source => renderEvidenceFileCard(source, `${link.left.stage} 代码证据`, previewPane));
      const rightCards = (e.right_sources || []).map(source => renderEvidenceFileCard(source, `${link.right.stage} 代码证据`, previewPane));
      if (autoPreview && idx === 0 && page === 1 && leftCards[0] && previewPane && !previewPane.dataset.initialized) {
        previewPane.dataset.initialized = '1';
        setTimeout(() => leftCards[0].click(), 30);
      }
      return node('div', { className: 'mount-evidence' }, [
        node('div', { className: 'mount-evidence-label', textContent: `项目代码：${e.normalized_code || e.canonical_code}` }),
        node('div', { className: 'mount-evidence-pair' }, [
          node('div', { className: 'mount-evidence-side' }, [node('div', { className: 'mount-side-title' }, [mountPath(link.left, link.left.stage)]), ...leftCards]),
          node('div', { className: 'mount-evidence-side' }, [node('div', { className: 'mount-side-title' }, [mountPath(link.right, link.right.stage)]), ...rightCards]),
        ]),
      ]);
    }
    const leftCards = (e.left_details || []).map(f => renderEvidenceFileCard(f, link.left.stage, previewPane));
    const rightCards = (e.right_details || []).map(f => renderEvidenceFileCard(f, link.right.stage, previewPane));
    if (autoPreview && idx === 0 && page === 1 && leftCards[0] && previewPane && !previewPane.dataset.initialized) {
      previewPane.dataset.initialized = '1';
      setTimeout(() => leftCards[0].click(), 30);
    }
    return node('div', { className: 'mount-evidence' }, [
      link.methods?.includes('M1') ? node('div', { className: 'mount-evidence-label', textContent: '文件内容相同' }) : '',
      node('div', { className: 'mount-evidence-pair' }, [
        node('div', { className: 'mount-evidence-side' }, [
          node('div', { className: 'mount-side-title' }, [mountPath(link.left, link.left.stage)]),
          ...leftCards,
        ]),
        node('div', { className: 'mount-evidence-side' }, [
          node('div', { className: 'mount-side-title' }, [mountPath(link.right, link.right.stage)]),
          ...rightCards,
        ]),
      ]),
    ]);
  }));
  if (data.pages > 1) {
    const prev = node('button', { className: 'secondary', type: 'button', textContent: '上一页证据', disabled: data.page <= 1 });
    const next = node('button', { className: 'secondary', type: 'button', textContent: '下一页证据', disabled: data.page >= data.pages });
    const input = node('input', { className: 'mount-page-input', type: 'number', min: '1', max: String(data.pages), step: '1', value: String(data.page), ariaLabel: '证据页码' });
    const go = node('button', { className: 'secondary', type: 'button', textContent: '跳转' });
    go.onclick = () => {
      const page = validMountPage(input.value, data.pages);
      if (page === null) return notify(`请输入 1 到 ${data.pages} 的整数页码`, true);
      mountEvidenceList(link, box, page, q, previewPane, method, false).catch(e => notify(e.message, true));
    };
    input.onkeydown = event => { if (event.key === 'Enter') go.click(); };
    prev.onclick = () => mountEvidenceList(link, box, data.page - 1, q, previewPane, method, false).catch(e => notify(e.message, true));
    next.onclick = () => mountEvidenceList(link, box, data.page + 1, q, previewPane, method, false).catch(e => notify(e.message, true));
    box.append(node('div', { className: 'mount-toolbar' }, [prev, node('span', { textContent: `第 ${data.page} / ${data.pages} 页（共 ${data.count} 组证据）` }), next, node('label', {}, ['跳至 ', input, ' 页']), go]));
  }
});

const mountEvidenceLoader = (link, previewPane = null, autoPreview = false) => {
  const box = node('div', { className: 'mount-evidence-container' });
  setTimeout(() => mountEvidenceList(link, box, 1, '', previewPane, 'all', autoPreview).catch(e => {
    box.textContent = `无法加载文件：${e.message}`;
    notify(e.message, true);
  }), 10);
  return box;
};

let currentReviewContext = null;

const refreshAfterDecision = async data => {
  await loadMount();
  const dialog = $('#mountReviewDialog');
  if (dialog?.open && currentReviewContext?.type === 'link') {
    const updated = (data.links || []).find(link => link.id === currentReviewContext.id);
    if (updated) openMountReviewDialog({ type: 'link', link: updated });
    else dialog.close();
  } else if (dialog?.open) dialog.close();
};

const decideMount = (link, status) => api('/api/folder-mount/decision', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ id: link.id, signature: link.signature, status }),
}).then(async data => {
  await refreshAfterDecision(data);
  const label = {
    confirmed: '已人工确认该关联（证据版本已保存）',
    rejected: '已人工排除该关联',
    review: '已标记该关联为待复核',
    pending: '已撤销之前的人工决定，恢复为待确认候选',
  }[status] || '状态已更新';
  notify(label);
}).catch(e => notify(e.message, true));

const mountActionButtons = link => {
  const confirm = node('button', { className: 'mount-btn-confirm', type: 'button', textContent: '✓ 确认关联' });
  const reject = node('button', { className: 'mount-btn-reject', type: 'button', textContent: '✕ 排除关联' });
  const review = node('button', { className: 'mount-btn-review', type: 'button', textContent: '⚑ 标记需复核' });
  const reset = node('button', { className: 'secondary', type: 'button', textContent: '↺ 撤销人工决定' });
  confirm.onclick = () => decideMount(link, 'confirmed');
  reject.onclick = () => decideMount(link, 'rejected');
  review.onclick = () => decideMount(link, 'review');
  reset.onclick = () => decideMount(link, 'pending');
  return [confirm, reject, review, reset];
};

const mountRelation = (link, previewPane = null, autoOpen = false) => {
  const methodBadge = mountMethodLabel(link.methods);
  const m1State = link.m1_validation ? mountM1StateText(link.m1_validation.state) : '';
  const hasHistory = Boolean(link.confirmation?.status || link.confirmation?.history?.length);

  return node('div', { className: 'mount-relation-card' }, [
    node('div', { className: 'mount-relation-header' }, [
      node('strong', { className: 'mount-relation-title', textContent: `${mountStageName(link.left.stage)} ↔ ${mountStageName(link.right.stage)}` }),
      node('span', { className: 'mount-relation-method', textContent: methodBadge }),
    ]),
    mountEvidenceLoader(link, previewPane, autoOpen),
    node('div', { className: 'mount-relation-facts' }, [
      node('span', { className: `mount-status-badge status-${link.status}`, textContent: mountStatusText(link.status) }),
      m1State ? node('span', { className: 'mount-relation-check', textContent: `代码核对：${m1State}` }) : '',
      link.ambiguous ? node('span', { className: 'conflict-type-badge type-multi_parcel_same_code', textContent: '一对多/多对多候选' }) : '',
      link.conflict ? node('span', { className: 'conflict-type-badge', textContent: '代码与文件证据冲突' }) : '',
    ]),
    node('div', { className: 'mount-relation-header-actions' }, mountActionButtons(link)),
    link.m1_validation?.reason ? node('details', { className: 'mount-relation-folders-collapse' }, [node('summary', { textContent: '查看核对详情' }), node('p', { className: 'conflict-reasons', textContent: link.m1_validation.reason })]) : '',
    hasHistory ? node('div', { style: 'padding:0 12px;' }, [renderAuditHistory(link.confirmation)]) : '',
  ]);
};

const openMountReviewDialog = context => {
  const dialog = $('#mountReviewDialog');
  const leftPane = $('#mountReviewLeft');
  const rightPane = $('#mountReviewRight');
  delete rightPane.dataset.initialized;
  leftPane.replaceChildren();
  rightPane.replaceChildren(node('div', { className: 'mount-preview-empty', textContent: '请点击左侧的任意证据材料文件，在此处直接预览原件（PDF 自动定位命中页码）' }));

  if (context.type === 'project') {
    const r = context.row;
    currentReviewContext = { type: 'project', id: r.id, title: r.name };
    $('#mountReviewTitle').textContent = `项目关联核对：${r.name}`;
    $('#mountReviewSubtitle').textContent = `${mountStatusText(r.status)} · ${r.links.length} 条关联`;

    leftPane.append(
      ...r.links.map((link, idx) => mountRelation(link, rightPane, idx === 0))
    );
  } else if (context.type === 'link') {
    const link = context.link;
    currentReviewContext = { type: 'link', id: link.id };
    $('#mountReviewTitle').textContent = `${link.left.stage} ⇄ ${link.right.stage} 跨阶段目录关系与证据核对`;
    $('#mountReviewSubtitle').textContent = `${mountStatusText(link.status)} · ${link.evidence_count} 组证据`;
    leftPane.append(mountRelation(link, rightPane, true));
  }

  if (!dialog.open) dialog.showModal();
};

const mountCandidates = row => {
  const members = new Set(Object.values(row.stages).flat().map(folder => folder.id));
  const candidates = new Map();
  for (const link of row.links) {
    if (link.status === 'confirmed' || link.status === 'rejected') continue;
    const folder = members.has(link.left.id) ? link.right : members.has(link.right.id) ? link.left : null;
    if (!folder || members.has(folder.id)) continue;
    if (!candidates.has(folder.id)) candidates.set(folder.id, {folder, links: []});
    candidates.get(folder.id).links.push(link);
  }
  return [...candidates.values()];
};

const confirmMountSelection = async (links, button) => {
  button.disabled = true;
  let completed = 0;
  try {
    for (const link of links) {
      button.textContent = `正在确认 ${completed + 1}/${links.length}`;
      await api('/api/folder-mount/decision', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: link.id, signature: link.signature, status: 'confirmed'}),
      });
      completed++;
    }
    notify(`已确认 ${completed} 条关联`);
  } catch (error) {
    notify(`已确认 ${completed} 条；${error.message}`, true);
  } finally {
    await loadMount();
  }
};

const renderProjectMountTable = data => {
  const stages = mountStageConfig.filter(stage => !hiddenMountStages.has(stage.stage_id));
  const rows = data.rows || [];
  if (!rows.length) {
    $('#mountTable').replaceChildren(node('div', {className: 'empty-state', textContent: data.scanned_at ? '没有匹配的项目' : '暂无新挂接结果，请先执行第二阶段目录挂接技能'}));
    return;
  }
  const body = rows.map(row => {
    const candidates = mountCandidates(row);
    const selected = new Set();
    const confirm = node('button', {type: 'button', className: 'primary', textContent: '确认关联', disabled: true});
    confirm.onclick = () => {
      const links = [...new Map(candidates.filter(item => selected.has(item.folder.id))
        .flatMap(item => item.links).map(link => [link.id, link])).values()];
      if (links.length) confirmMountSelection(links, confirm);
    };
    const review = row.links.length ? node('button', {
      type: 'button', className: 'text-button', textContent: '审核',
      onclick: () => openMountReviewDialog({type: 'project', row}),
    }) : '';
    return node('tr', {}, [
      node('td', {}, [node('strong', {textContent: row.name})]),
      ...stages.map(stage => {
        const current = row.stages[stage.stage_id] || [];
        const options = candidates.filter(item => item.folder.stage === stage.stage_id);
        return node('td', {}, [node('div', {className: 'mount-stage-items'}, [
          ...current.map(folder => node('div', {className: 'mount-stage-project'}, [
            node('small', {textContent: row.status === 'confirmed' ? '已关联' : '当前项目'}), mountPath(folder),
          ])),
          ...options.map(item => {
            const checkbox = node('input', {type: 'checkbox', ariaLabel: `关联 ${item.folder.name}`});
            checkbox.onchange = () => {
              if (checkbox.checked) selected.add(item.folder.id); else selected.delete(item.folder.id);
              confirm.disabled = !selected.size;
              confirm.textContent = selected.size ? `确认关联（${selected.size}）` : '确认关联';
            };
            return node('div', {className: 'mount-stage-project mount-candidate'}, [
              node('label', {className: 'mount-candidate-label'}, [checkbox, item.folder.name]),
              mountPath(item.folder),
              item.links.some(link => link.status === 'review') ? node('small', {className: 'mount-warning', textContent: '需复核'}) : '',
            ]);
          }),
          ...(!current.length && !options.length ? [node('span', {className: 'mount-missing', textContent: '—'})] : []),
        ])]);
      }),
      node('td', {className: 'mount-audit-cell'}, [
        node('small', {textContent: mountStatusText(row.status)}),
        ...(candidates.some(item => !hiddenMountStages.has(item.folder.stage)) ? [confirm] : []), review,
      ]),
    ]);
  });
  $('#mountTable').replaceChildren(node('table', {className: 'mount-table mount-projects'}, [
    node('thead', {}, [node('tr', {}, [
      node('th', {textContent: '项目名称'}),
      ...stages.map(stage => node('th', {textContent: stage.title || stage.name})),
      node('th', {textContent: '审核'}),
    ])]), node('tbody', {}, body),
  ]));
};

const renderMount = data => {
  mountData = data;
  mountPage = data.page || 1;
  if (data.stage_config?.length) mountStageConfig = data.stage_config;
  if (!$('#mountStageToggles').children.length) {
    $('#mountStageToggles').append(node('span', {textContent: '显示阶段：'}), ...mountStageConfig.map(stage => {
      const checkbox = node('input', {type: 'checkbox', checked: true});
      checkbox.onchange = () => {
        if (checkbox.checked) hiddenMountStages.delete(stage.stage_id); else hiddenMountStages.add(stage.stage_id);
        if ($('#mountTable').ariaBusy !== 'true') renderProjectMountTable(mountData);
      };
      return node('label', {}, [checkbox, stage.title || stage.name]);
    }));
  }
  $('#mountPhase').textContent = `${data.phase || '已就绪'}${data.scanned_at ? ` · ${data.scanned_at.replace('T', ' ')}` : ''}`;
  $('#mountSummary').textContent = `总项目 ${data.summary.projects} · 需要审核 ${data.summary.project_needs_review} · 未关联 ${data.summary.project_unlinked}`;
  $('#mountPage').textContent = `${data.page || 1} / ${data.pages || 1}`;
  $('#mountPageInput').value = data.page || 1;
  $('#mountPageInput').max = data.pages || 1;
  $('#mountPrev').disabled = (data.page || 1) <= 1;
  $('#mountNext').disabled = (data.page || 1) >= (data.pages || 1);
  renderProjectMountTable(data);
};

const loadMount = () => {
  const request = ++mountRequest;
  $('#mountPhase').textContent = '正在读取关联结果…';
  $('#mountTable').ariaBusy = 'true';
  $('#mountTable').replaceChildren(node('div', {className: 'mount-loading', role: 'status'}, [node('span', {className: 'spinner', ariaHidden: 'true'}), '正在加载项目…']));
  const categories = $('#mountCategoryFilters').value;
  return api(`/api/folder-mount?view=projects&page=${mountPage}&categories=${encodeURIComponent(categories)}&q=${encodeURIComponent($('#mountQuery').value.trim())}`)
    .then(data => { if (request === mountRequest) renderMount(data); }).catch(error => {
      if (request !== mountRequest) return;
      $('#mountPhase').textContent = `关联结果读取失败：${error.message}`;
      $('#mountTable').replaceChildren(node('div', {className: 'empty-state', textContent: '项目加载失败，请重试'}));
      notify(error.message, true);
    }).finally(() => { if (request === mountRequest) $('#mountTable').ariaBusy = 'false'; });
};

/* ==================== 事件绑定 ==================== */
let mountQueryTimer;
$('#mountCategoryFilters').onchange = () => {
  mountPage = 1;
  loadMount();
};
$('#mountQuery').oninput = () => {
  clearTimeout(mountQueryTimer);
  mountQueryTimer = setTimeout(() => { mountPage = 1; loadMount(); }, 300);
};
$('#mountPrev').onclick = () => { mountPage = Math.max(1, mountPage - 1); loadMount(); };
$('#mountNext').onclick = () => { mountPage += 1; loadMount(); };
$('#mountPageGo').onclick = () => {
  const pages = mountData?.pages || 1;
  const page = validMountPage($('#mountPageInput').value, pages);
  if (page === null) return notify(`请输入 1 到 ${pages} 的整数页码`, true);
  mountPage = page;
  loadMount();
};
$('#mountPageInput').onkeydown = event => { if (event.key === 'Enter') $('#mountPageGo').click(); };
$('#closeMountReview').addEventListener('click', () => $('#mountReviewDialog').close());
loadMount();
