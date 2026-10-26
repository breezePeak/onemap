'use strict';

let mountPage = 1;
let mountStageConfig = [];
let conflictPage = 1;

const mountStatusText = status => ({
  confirmed: '✓ 已人工确认',
  pending: '待确认候选',
  review: '待复核候选',
  rejected: '✕ 已人工排除',
  unlinked: '未关联目录',
  unscanned: '待挂接（当前仅识别目录）',
}[status] || status);

const mountMethodLabel = methods => {
  const list = Array.isArray(methods) && methods.length ? methods : ['M2'];
  if (list.includes('M1') && list.includes('M2')) return 'M1+M2';
  return list.join('+');
};

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

const syncStageSelectOptions = (selectEl, stages) => {
  if (!selectEl || selectEl.options.length > 1) return;
  const stageIds = (stages?.length ? stages : mountStageConfig).map(s => s.stage_id);
  const ids = stageIds.length ? stageIds : ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7'];
  ids.forEach(s => selectEl.append(node('option', { value: s, textContent: `${s} 单阶段筛选` })));
  ids.forEach((a, i) => ids.slice(i + 1).forEach(b => selectEl.append(node('option', { value: `${a}-${b}`, textContent: `${a}-${b}` }))));
};

const mountPairs = stages => {
  syncStageSelectOptions($('#mountPair'), stages);
  syncStageSelectOptions($('#conflictStageFilter'), stages);
};

const mountFolderNode = (folder, compact = false) => {
  const realPath = folder.folder || '';
  const pathEl = node('div', {
    className: 'manager-path clickable-path',
    title: `点击在资源管理器打开：${realPath}`,
    textContent: realPath,
  });
  pathEl.onclick = e => { e.stopPropagation(); openWindowsPath(realPath, 'folder'); };

  const copyBtn = node('button', { className: 'secondary manager-copy', type: 'button', textContent: '复制路径' });
  copyBtn.onclick = e => { e.stopPropagation(); copyDirectoryPath(realPath, copyBtn); };

  const openBtn = node('button', { className: 'secondary manager-copy', type: 'button', textContent: '打开目录' });
  openBtn.onclick = e => { e.stopPropagation(); openWindowsPath(realPath, 'folder'); };

  const badges = [];
  if (folder.decision_source === 'agent') {
    badges.push(node('span', { className: 'mount-sha-tag', style: 'margin-left:6px;background:#dcfce7;color:#166534;', textContent: 'Agent研判' }));
  } else if (folder.decision_source === 'manual') {
    badges.push(node('span', { className: 'mount-sha-tag', style: 'margin-left:6px;background:#dbeafe;color:#1e40af;', textContent: '人工锁定' }));
  }
  if (folder.boundary_review) {
    badges.push(node('span', { className: 'conflict-type-badge type-boundary_review', style: 'margin-left:6px;', textContent: '目录边界待复核' }));
  }

  return node('div', { className: compact ? 'mount-project-folder' : '' }, [
    node('div', { style: 'display:flex;align-items:center;flex-wrap:wrap;gap:4px;' }, [
      node('strong', { textContent: `${folder.stage ? folder.stage + ' · ' : ''}${folder.name || ''}` }),
      ...badges,
    ]),
    pathEl,
    node('small', { textContent: `${folder.kind === 'archive' ? '压缩包解压项目目录' : '项目文件夹'} · 有效材料 ${folder.files ?? 0} 份` }),
    node('div', { style: 'display:flex;gap:6px;margin-top:4px;' }, [openBtn, copyBtn]),
  ]);
};

const getFileExt = p => {
  const m = String(p || '').toLowerCase().match(/(\.[a-z0-9]+)$/);
  return m ? m[1] : '';
};

const renderFilePreview = (container, fileInfo, stageLabel) => {
  container.replaceChildren();
  const realPath = fileInfo.physical_path || fileInfo.logical_path || '';
  const ext = getFileExt(realPath);
  const pageSuffix = fileInfo.page ? `#page=${fileInfo.page}` : '';
  const previewUrl = `/api/folder-mount/file?path=${encodeURIComponent(realPath)}${pageSuffix}`;
  const downloadUrl = `/api/folder-mount/file?path=${encodeURIComponent(realPath)}&download=1`;

  const openFolderBtn = node('button', { className: 'secondary', type: 'button', textContent: '📂 定位文件所在目录' });
  openFolderBtn.onclick = () => openWindowsPath(realPath, 'select');

  const openNativeBtn = node('button', { className: 'secondary', type: 'button', textContent: '🖥️ 用本机默认程序打开' });
  openNativeBtn.onclick = () => openWindowsPath(realPath, 'open');

  const copyBtn = node('button', { className: 'secondary', type: 'button', textContent: '📋 复制完整路径' });
  copyBtn.onclick = () => copyDirectoryPath(realPath, copyBtn);

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
      fileInfo.page ? node('span', { className: 'mount-sha-tag', textContent: `第 ${fileInfo.page} 页` }) : '',
    ]),
    node('div', { className: 'manager-path clickable-path', textContent: realPath, title: '点击在资源管理器中定位该文件' }),
    node('div', { className: 'mount-preview-actions' }, [openFolderBtn, openNativeBtn, copyBtn, downloadLink]),
  ]);
  head.querySelector('.manager-path').onclick = () => openWindowsPath(realPath, 'select');

  const body = node('div', { className: 'mount-preview-body' });

  if (ext === '.pdf') {
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
        node('button', { className: 'secondary', type: 'button', textContent: '📂 在资源管理器中显示', onclick: () => openWindowsPath(realPath, 'select') }),
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
  const archiveChain = (fileObj.archive_chain || []).map(a => a.split(/[\\/]/).pop()).join(' → ');

  const card = node('div', { className: 'mount-file-card', title: `完整路径：${realPath}` });

  const previewBtn = node('button', { className: 'primary', type: 'button', textContent: '👁️ 右侧预览' });
  previewBtn.onclick = e => {
    e.stopPropagation();
    document.querySelectorAll('.mount-file-card.active').forEach(el => el.classList.remove('active'));
    card.classList.add('active');
    if (previewPane) renderFilePreview(previewPane, fileObj, stageLabel);
  };

  const openDirBtn = node('button', { className: 'secondary', type: 'button', textContent: '📂 所在目录' });
  openDirBtn.onclick = e => { e.stopPropagation(); openWindowsPath(realPath, 'select'); };

  const openAppBtn = node('button', { className: 'secondary', type: 'button', textContent: '🖥️ 本机打开' });
  openAppBtn.onclick = e => { e.stopPropagation(); openWindowsPath(realPath, 'open'); };

  card.append(
    node('div', { className: 'mount-file-main' }, [
      node('span', { className: 'mount-file-icon', textContent: getFileTypeIcon(fileName) }),
      node('span', { className: 'mount-file-name', textContent: fileName }),
    ]),
    fileObj.small_dir ? node('div', { className: 'mount-small-dir-tag', textContent: `📁 所属子路径：${fileObj.small_dir}` }) : '',
    archiveChain ? node('span', { className: 'mount-archive-tag', textContent: `📦 来源压缩包：${archiveChain}` }) : '',
    node('div', { className: 'mount-file-btns' }, [previewBtn, openDirBtn, openAppBtn])
  );

  card.onclick = () => previewBtn.click();
  return card;
};

const mountEvidenceList = (link, box, page = 1, q = '', previewPane = null, method = 'all') => api(`/api/folder-mount/links/${link.id}?page=${page}&q=${encodeURIComponent(q)}&method=${encodeURIComponent(method)}`).then(data => {
  box.replaceChildren(...data.evidence.map((e, idx) => {
    if (e.method === 'M1' || e.canonical_code) {
      const leftCard = renderEvidenceFileCard({ physical_path: e.left_source_path, logical_path: e.left_source_path, page: e.left_page }, `${link.left.stage} 代码证据`, previewPane);
      const rightCard = renderEvidenceFileCard({ physical_path: e.right_source_path, logical_path: e.right_source_path, page: e.right_page }, `${link.right.stage} 代码证据`, previewPane);
      if (idx === 0 && page === 1 && previewPane && !previewPane.dataset.initialized) {
        previewPane.dataset.initialized = '1';
        setTimeout(() => leftCard.click(), 30);
      }
      return node('div', { className: 'mount-evidence' }, [
        node('div', { className: 'mount-sha-header' }, [
          node('span', { className: 'mount-sha-tag', textContent: `M1 项目代码 #${(page - 1) * 20 + idx + 1} · ${e.canonical_code}` }),
          node('code', { className: 'mount-sha-hash', textContent: `${e.code_type || ''} · 权重 ${e.weight || ''}` }),
        ]),
        node('p', { textContent: `左阶段位置：${e.left_field_name || e.left_location || '-'}（页码：${e.left_page ?? '无'}）｜右阶段位置：${e.right_field_name || e.right_location || '-'}（页码：${e.right_page ?? '无'}）` }),
        node('p', { textContent: `命中片段：${e.left_snippet || ''} ⇄ ${e.right_snippet || ''}` }),
        node('div', { className: 'mount-evidence-pair' }, [
          node('div', { className: 'mount-evidence-side' }, [node('div', { className: 'mount-side-title', textContent: `◀ ${link.left.stage} · ${link.left.name}` }), leftCard]),
          node('div', { className: 'mount-evidence-side' }, [node('div', { className: 'mount-side-title', textContent: `▶ ${link.right.stage} · ${link.right.name}` }), rightCard]),
        ]),
      ]);
    }
    const leftCards = (e.left_files || []).map(f => renderEvidenceFileCard(f, `${link.left.stage} · ${link.left.name}`, previewPane));
    const rightCards = (e.right_files || []).map(f => renderEvidenceFileCard(f, `${link.right.stage} · ${link.right.name}`, previewPane));
    if (idx === 0 && page === 1 && leftCards[0] && previewPane && !previewPane.dataset.initialized) {
      previewPane.dataset.initialized = '1';
      setTimeout(() => leftCards[0].click(), 30);
    }
    const shortSha = (e.sha256 || '').slice(0, 12);
    return node('div', { className: 'mount-evidence' }, [
      node('div', { className: 'mount-sha-header' }, [
        node('span', { className: 'mount-sha-tag', textContent: `M2 相同附件 #${(page - 1) * 20 + idx + 1}` }),
        node('code', { className: 'mount-sha-hash', title: `完整 SHA-256 指纹：${e.sha256}`, textContent: `SHA-256: ${shortSha}...` }),
      ]),
      node('div', { className: 'mount-evidence-pair' }, [
        node('div', { className: 'mount-evidence-side' }, [
          node('div', { className: 'mount-side-title', textContent: `◀ ${link.left.stage} · ${link.left.name}` }),
          ...leftCards,
        ]),
        node('div', { className: 'mount-evidence-side' }, [
          node('div', { className: 'mount-side-title', textContent: `▶ ${link.right.stage} · ${link.right.name}` }),
          ...rightCards,
        ]),
      ]),
    ]);
  }));
  if (data.pages > 1) {
    const prev = node('button', { className: 'secondary', type: 'button', textContent: '上一页证据', disabled: data.page <= 1 });
    const next = node('button', { className: 'secondary', type: 'button', textContent: '下一页证据', disabled: data.page >= data.pages });
    prev.onclick = () => mountEvidenceList(link, box, data.page - 1, q, previewPane, method);
    next.onclick = () => mountEvidenceList(link, box, data.page + 1, q, previewPane, method);
    box.append(node('div', { className: 'mount-toolbar' }, [prev, node('span', { textContent: `第 ${data.page} / ${data.pages} 页（共 ${data.count} 组证据）` }), next]));
  }
});

const mountEvidenceLoader = (link, previewPane = null, autoOpen = false) => {
  const box = node('div');
  const methodSelect = node('select', {}, [
    node('option', { value: 'all', textContent: '全部方法证据' }),
    node('option', { value: 'M1', textContent: '仅看 M1 项目代码证据' }),
    node('option', { value: 'M2', textContent: '仅看 M2 相同附件证据' }),
  ]);
  const input = node('input', { type: 'search', placeholder: '筛选本关系的证据文件名、项目代码或 SHA-256...' });
  input.oninput = () => mountEvidenceList(link, box, 1, input.value, previewPane, methodSelect.value);
  methodSelect.onchange = () => mountEvidenceList(link, box, 1, input.value, previewPane, methodSelect.value);

  if (autoOpen) {
    setTimeout(() => mountEvidenceList(link, box, 1, '', previewPane, 'all').catch(e => notify(e.message, true)), 10);
    return node('div', { className: 'mount-evidence-container' }, [
      node('div', { className: 'mount-evidence-topbar' }, [
        node('strong', { textContent: `🔍 关联证据文件明细（共 ${link.evidence_count} 组）` }),
        methodSelect,
        input,
      ]),
      box,
    ]);
  }

  const wrap = node('details', {}, [node('summary', { textContent: `查看关联证据（${link.evidence_count} 组）` }), methodSelect, input, box]);
  wrap.ontoggle = () => { if (wrap.open && !box.childElementCount) mountEvidenceList(link, box, 1, input.value, previewPane, methodSelect.value).catch(e => notify(e.message, true)); };
  return wrap;
};

let currentReviewContext = null;

const refreshAfterDecision = async data => {
  renderMount(data);
  if ($('#mountView').value === 'projects') {
    await loadMount();
  }
  if (!$('#conflictsModule').classList.contains('hidden')) {
    await loadConflicts();
  }
  const dialog = $('#mountReviewDialog');
  if (dialog?.open && currentReviewContext) {
    if (currentReviewContext.type === 'link') {
      const updated = (data.links || []).find(l => l.id === currentReviewContext.id);
      if (updated) openMountReviewDialog({ type: 'link', link: updated });
      else {
        const fresh = await api(`/api/folder-mount?view=links&q=${encodeURIComponent(currentReviewContext.id)}`);
        const found = (fresh.links || []).find(l => l.id === currentReviewContext.id);
        if (found) openMountReviewDialog({ type: 'link', link: found });
      }
    } else if (currentReviewContext.type === 'project') {
      const res = await api(`/api/folder-mount?view=projects&q=${encodeURIComponent(currentReviewContext.title || '')}`);
      const updatedRow = (res.rows || []).find(r => r.id === currentReviewContext.id);
      if (updatedRow) openMountReviewDialog({ type: 'project', row: updatedRow });
    }
  }
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
  const stageClass = s => `mount-stage-pill stage-${String(s || '').toLowerCase()}`;
  const methodBadge = mountMethodLabel(link.methods);
  const m1State = link.m1_validation ? ` · M1校验:${link.m1_validation.state}` : '';
  const hasHistory = Boolean(link.confirmation?.status || link.confirmation?.history?.length);

  return node('div', { className: 'mount-relation-card' }, [
    node('div', { className: 'mount-relation-header' }, [
      node('div', { className: 'mount-relation-title-area' }, [
        node('div', { className: 'mount-relation-title' }, [
          node('span', { className: stageClass(link.left.stage), textContent: link.left.stage }),
          node('strong', { className: 'mount-relation-nodename', textContent: link.left.name }),
          node('span', { className: 'mount-relation-arrow', textContent: '⇄' }),
          node('span', { className: stageClass(link.right.stage), textContent: link.right.stage }),
          node('strong', { className: 'mount-relation-nodename', textContent: link.right.name }),
        ]),
        node('span', { className: 'mount-sha-tag', textContent: methodBadge }),
        node('span', { className: `mount-status-badge status-${link.status}`, textContent: `${mountStatusText(link.status)}${m1State}` }),
        link.ambiguous ? node('span', { className: 'conflict-type-badge type-multi_parcel_same_code', textContent: '一对多/多对多候选' }) : '',
        link.conflict ? node('span', { className: 'conflict-type-badge', textContent: 'M1/M2证据冲突' }) : '',
      ]),
      node('div', { className: 'mount-relation-header-actions' }, mountActionButtons(link)),
    ]),
    link.m1_validation?.reason ? node('p', { className: 'conflict-reasons', style: 'margin:8px 12px 0;', textContent: `校验与复核说明：${link.m1_validation.reason}` }) : '',
    node('details', { className: 'mount-relation-folders-collapse', open: true }, [
      node('summary', { textContent: '📁 关联双方项目大目录真实路径（点击路径可在资源管理器打开）' }),
      node('div', { className: 'mount-relation-folders-inner' }, [
        mountFolderNode(link.left, true),
        mountFolderNode(link.right, true),
      ]),
    ]),
    hasHistory ? node('div', { style: 'padding:0 12px;' }, [renderAuditHistory(link.confirmation)]) : '',
    mountEvidenceLoader(link, previewPane, autoOpen),
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
    currentReviewContext = { type: 'project', id: r.id, title: r.title };
    $('#mountReviewTitle').textContent = `项目关联核对：${r.title}`;
    $('#mountReviewSubtitle').textContent = `当前状态：${mountStatusText(r.status)} · 跨阶段候选关系 ${r.links.length} 条 · 相同附件指纹 ${r.evidence_count} 个`;

    const stageSummary = node('details', { className: 'mount-dialog-stages-collapse' }, [
      node('summary', { textContent: '📂 查看该项目已归属的各阶段业务项目大目录路径' }),
      node('div', { className: 'mount-dialog-stages-body' },
        Object.entries(r.stages || {}).filter(([, v]) => v?.length).map(([sid, list]) =>
          node('div', { className: 'mount-dialog-stage-item' }, [
            node('strong', { textContent: `${mountStageName(sid)}：` }),
            ...list.map(f => mountFolderNode(f, true)),
          ])
        )
      ),
    ]);

    leftPane.append(
      stageSummary,
      ...r.links.map((link, idx) => mountRelation(link, rightPane, idx === 0))
    );
  } else if (context.type === 'link') {
    const link = context.link;
    currentReviewContext = { type: 'link', id: link.id };
    $('#mountReviewTitle').textContent = `${link.left.stage} ⇄ ${link.right.stage} 跨阶段目录关系与证据核对`;
    $('#mountReviewSubtitle').textContent = `${link.left.name} ⇄ ${link.right.name} · 来源方法：${mountMethodLabel(link.methods)} · 状态：${mountStatusText(link.status)} · 证据 ${link.evidence_count} 组`;
    leftPane.append(mountRelation(link, rightPane, true));
  }

  if (!dialog.open) dialog.showModal();
};

const showCodeEvidenceModal = (directory = '', titleText = 'M1 项目代码提取与归属证据') => {
  api(`/api/folder-mount/code-evidence?directory=${encodeURIComponent(directory)}`).then(data => {
    const dialog = $('#mountReviewDialog');
    const left = $('#mountReviewLeft');
    const right = $('#mountReviewRight');
    $('#mountReviewTitle').textContent = `${titleText}（共 ${data.count} 条）`;
    $('#mountReviewSubtitle').textContent = '展示文件相对路径、页码/段落/表格位置、原始代码、规范化代码及归属状态（点击材料可在右侧预览并定位 PDF 页码）';
    right.replaceChildren(node('div', { className: 'mount-preview-empty', textContent: '点击左侧代码证据材料在右侧预览原件' }));
    left.replaceChildren(...(data.evidence.length ? data.evidence.map(e => {
      const card = renderEvidenceFileCard({ physical_path: e.source_path, logical_path: e.rel_path, page: e.page }, `${e.stage_id} ${e.canonical_code}`, right);
      return node('div', { className: 'mount-evidence' }, [
        node('div', { className: 'mount-sha-header' }, [
          node('span', { className: 'mount-sha-tag', textContent: `${e.stage_id} · ${e.canonical_code}` }),
          node('code', { className: 'mount-sha-hash', textContent: `${e.ownership_status} · 权重 ${e.weight}` }),
        ]),
        node('p', { textContent: `原始值：${e.raw_code}｜类型：${e.code_type}｜位置：${e.field_name || e.location}｜页码：${e.page ?? '无'}` }),
        node('p', { textContent: `文本片段：${e.snippet || ''}` }),
        e.reason ? node('p', { className: 'mount-warning', textContent: `复核原因：${e.reason}` }) : '',
        card,
      ]);
    }) : [node('div', { className: 'empty-state' }, [node('h3', { textContent: '暂无项目代码证据记录' })])]));
    if (!dialog.open) dialog.showModal();
  }).catch(e => notify(e.message, true));
};

const renderProjectMountTable = data => {
  const stageConfig = (data.stage_config?.length ? data.stage_config : [
    { stage_id: 'S1', title: '用地预审与选址' }, { stage_id: 'S2', title: '条件出具' },
    { stage_id: 'S3', title: '批次报批阶段' }, { stage_id: 'S4', title: '土地供应阶段' },
    { stage_id: 'S5', title: '土地首次登记' }, { stage_id: 'S6', title: '用地规划' },
    { stage_id: 'S7', title: '工程规划' },
  ]);
  const rows = data.rows || [];
  if (!rows.length) {
    $('#mountTable').replaceChildren(node('div', { className: 'empty-state' }, [
      node('h3', { textContent: '当前筛选条件下没有项目目录或关联记录' }),
    ]));
    return;
  }
  const cell = (r, stageId) => {
    const list = r.stages?.[stageId] || [];
    return node('td', {}, list.length ? list.map(f => mountFolderNode(f, true)) : [node('span', { className: 'mount-missing', textContent: '未关联' })]);
  };
  $('#mountTable').replaceChildren(node('table', { className: 'mount-table mount-projects' }, [
    node('thead', {}, [node('tr', {}, [
      node('th', { textContent: '项目 / 锚点大目录' }),
      ...stageConfig.map(s => node('th', { textContent: `${s.stage_id} ${s.title || s.name}` })),
      node('th', { textContent: '状态与人工核对' }),
    ])]),
    node('tbody', {}, rows.map(r => {
      const reviewBtn = r.links.length ? node('button', {
        className: 'primary mount-review-trigger',
        type: 'button',
        textContent: `🔍 核对关系与证据 (${r.links.length})`,
      }) : '';
      if (reviewBtn) {
        reviewBtn.onclick = () => openMountReviewDialog({ type: 'project', row: r });
      }
      const codeBtn = (r.project_codes?.length || r.unresolved_codes?.length) ? node('button', {
        className: 'secondary mount-review-trigger',
        type: 'button',
        textContent: `查看 M1 代码证据 (${(r.project_codes || []).length}/${(r.unresolved_codes || []).length})`,
      }) : '';
      if (codeBtn) {
        const firstDir = Object.values(r.stages || {}).flat()[0]?.id || '';
        codeBtn.onclick = () => showCodeEvidenceModal(firstDir, `项目「${r.title}」M1 代码证据`);
      }
      return node('tr', {}, [
        node('td', {}, [
          node('strong', { textContent: r.title }),
          node('small', { textContent: `${r.anchor_stage} 锚点 · 关联阶段 ${Object.values(r.stages).filter(v => v.length).length}/${stageConfig.length} 个` }),
          r.project_codes?.length ? node('small', { textContent: `项目代码：${r.project_codes.join('、')}` }) : '',
          r.unresolved_codes?.length ? node('small', { className: 'mount-warning', textContent: `待核对代码：${r.unresolved_codes.join('、')}` }) : '',
          r.ambiguous ? node('small', { className: 'mount-warning', textContent: '含一对多/多对多候选，需人工复核' }) : '',
          r.conflict ? node('small', { className: 'mount-warning', textContent: '含 M1/M2 证据冲突，请核对' }) : '',
        ]),
        ...stageConfig.map(s => cell(r, s.stage_id)),
        node('td', {}, [
          node('span', { className: `mount-status-badge status-${r.status}`, textContent: mountStatusText(r.status) }),
          node('small', { textContent: `关系 ${r.links.length} 条 · 证据 ${r.evidence_count} 组 · 方法 ${(r.methods || ['M2']).join('+')}` }),
          reviewBtn,
          codeBtn,
        ]),
      ]);
    })),
  ]));
};

const renderMount = data => {
  if (data.stage_config?.length) mountStageConfig = data.stage_config;
  mountPairs(data.stages);
  const s = data.summary || {};
  $('#mountPhase').textContent = `${data.phase || '已就绪'}${data.scanned_at ? ` · Skill 结果生成于 ${data.scanned_at}` : ''}`;
  $('#mountSummary').textContent = data.rows
    ? `项目 ${s.projects || 0}｜已人工确认 ${s.project_confirmed || 0}｜待确认候选 ${s.project_pending || 0}｜待复核/冲突 ${s.project_review || 0}｜未关联 ${s.project_unlinked || 0}`
    : `项目大目录 ${s.folders || 0}｜跨阶段候选关联 ${s.links || 0}｜已人工确认 ${s.confirmed || 0}｜待确认候选 ${s.pending || 0}｜待复核/冲突 ${s.review || 0}｜未关联 ${s.unlinked || 0}`;
  $('#mountPage').textContent = `${data.page || 1} / ${data.pages || 1}`;
  $('#mountPrev').disabled = (data.page || 1) <= 1;
  $('#mountNext').disabled = (data.page || 1) >= (data.pages || 1);

  if (data.projects && $('#mountProjectList')) {
    const byStage = {};
    data.projects.forEach(p => { (byStage[p.stage_id] = byStage[p.stage_id] || []).push(p); });
    const stageDivs = Object.keys(byStage).sort().map(sid => {
      const projs = byStage[sid];
      return node('details', { style: 'margin: 6px 0; padding-left: 10px; border-left: 2px solid #cbd5e1;' }, [
        node('summary', { style: 'cursor:pointer; font-weight:bold;' }, [`${mountStageName(sid)}（共识别 ${projs.length} 个项目大目录）`]),
        node('ul', { style: 'margin: 4px 0 8px 20px; font-size: 13px;' }, projs.map(p =>
          node('li', { style: 'margin-bottom:4px;' }, [
            node('strong', {}, [p.project_name]),
            node('span', { style: 'color:#64748b; margin-left:8px;' }, [`[相对路径: ${p.project_root}]`]),
            node('span', { style: 'color:#0f766e; margin-left:8px; font-size:12px;' }, [`(${p.decision_source === 'agent' ? 'Agent研判' : p.decision_source === 'manual' ? '人工锁定' : '规则筛选'}：${p.reason})`]),
            p.boundary_review ? node('span', { className: 'conflict-type-badge type-boundary_review', style: 'margin-left:6px;', textContent: `需复核边界: ${p.boundary_review_reason || p.reason}` }) : '',
          ])
        )),
      ]);
    });
    $('#mountProjectList').replaceChildren(...stageDivs);
  }

  $('#mountStages').replaceChildren(node('table', { className: 'mount-table' }, [
    node('thead', {}, [node('tr', {}, ['阶段', '项目大目录', '压缩包', '复用成员', '新解压成员', '失败', '有效材料', '跳过文件'].map(t => node('th', { textContent: t })))]),
    node('tbody', {}, (data.stages || []).map(st => node('tr', {}, [
      `${st.stage_id} ${st.name}`, st.business_directories, st.archives, st.reused, st.extracted, st.failed, st.valid_materials,
      Object.entries(st.skipped || {}).map(([k, v]) => `${k}:${v}`).join(' ') || '0',
    ].map(v => node('td', { textContent: String(v ?? 0) }))))),
  ]));

  if (data.m1_statistics) {
    const m1 = data.m1_statistics;
    $('#mountStages').append(node('p', {
      textContent: `M1 统计：含代码目录 ${m1.projects_with_codes || 0}｜唯一项目代码 ${m1.distinct_codes || 0}｜M1 跨阶段关联 ${m1.cross_stage_candidates || 0}｜M1/M2 冲突 ${m1.conflicts || 0}｜代码归属不明 ${m1.unresolved_ownership || 0}｜过滤无效代码 ${m1.invalid_evidence || 0}`,
    }));
  }

  $('#mountChains div').replaceChildren(
    node('p', { textContent: data.chains_truncated ? '已人工确认关联链超过 200 条，仅显示前 200 条。' : `共 ${(data.chains || []).length} 条已人工确认的多阶段关联链。` }),
    ...(data.chains || []).map(c => node('div', { className: 'mount-evidence', textContent: c.map(x => `${x.stage}:${x.name}`).join(' → ') }))
  );

  $('#mountErrors summary').textContent = `未读取或异常材料清单（${(data.errors || []).length}）`;
  $('#mountErrors pre').textContent = (data.errors || []).map(e => `${e.stage || ''} ${e.path}: ${e.error}`).join('\n') || '无';

  if (data.rows) {
    renderProjectMountTable(data);
    return;
  }

  if ($('#mountFilter').value === 'unlinked') {
    $('#mountTable').replaceChildren(node('table', { className: 'mount-table' }, [
      node('thead', {}, [node('tr', {}, ['阶段', '未关联项目大目录', '材料数'].map(t => node('th', { textContent: t })))]),
      node('tbody', {}, (data.unlinked || []).map(f => node('tr', {}, [
        node('td', { textContent: f.stage }),
        node('td', {}, [mountFolderNode(f)]),
        node('td', { textContent: String(f.files) }),
      ]))),
    ]));
    return;
  }

  const rows = data.links || [];
  if (!rows.length) {
    $('#mountTable').replaceChildren(node('div', { className: 'empty-state' }, [
      node('h3', { textContent: '当前筛选条件下没有目录关联记录' }),
    ]));
    return;
  }

  $('#mountTable').replaceChildren(node('table', { className: 'mount-table' }, [
    node('thead', {}, [node('tr', {}, ['阶段A项目大目录', '阶段B项目大目录', '关联来源与证据概要', '当前状态', '人工核对操作'].map(t => node('th', { textContent: t })))]),
    node('tbody', {}, rows.map(link => {
      const openDialogBtn = node('button', {
        className: 'primary mount-review-trigger',
        type: 'button',
        textContent: `🔍 查看证据与预览 (${link.evidence_count})`,
      });
      openDialogBtn.onclick = () => openMountReviewDialog({ type: 'link', link });
      const methodBadge = mountMethodLabel(link.methods);

      return node('tr', {}, [
        node('td', {}, [mountFolderNode(link.left)]),
        node('td', {}, [mountFolderNode(link.right)]),
        node('td', {}, [
          node('div', { style: 'display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:4px;' }, [
            node('span', { className: 'mount-sha-tag', textContent: `来源: ${methodBadge}` }),
            node('strong', { textContent: `共 ${link.evidence_count} 组证据` }),
            link.ambiguous ? node('span', { className: 'conflict-type-badge type-multi_parcel_same_code', textContent: '一对多/多对多候选' }) : '',
            link.conflict ? node('span', { className: 'conflict-type-badge', textContent: 'M1/M2证据矛盾' }) : '',
          ]),
          link.m1_validation ? node('p', { className: link.conflict ? 'mount-warning' : '', textContent: `M1 校验：${link.m1_validation.state}（${link.m1_validation.reason || '一致'}）` }) : '',
          openDialogBtn,
        ]),
        node('td', {}, [
          node('span', { className: `mount-status-badge status-${link.status}`, textContent: mountStatusText(link.status) }),
          link.confirmation?.updated_at ? node('small', { style: 'display:block;margin-top:4px;color:#64748b;', textContent: `审核于 ${link.confirmation.updated_at}` }) : '',
        ]),
        node('td', {}, mountActionButtons(link)),
      ]);
    })),
  ]));
};

const loadMount = () => {
  const q = encodeURIComponent($('#mountQuery').value.trim());
  const status = encodeURIComponent($('#mountFilter').value);
  const pair = encodeURIComponent($('#mountPair').value);
  const view = encodeURIComponent($('#mountView').value);
  const method = encodeURIComponent($('#mountMethodFilter')?.value || 'all');
  return api(`/api/folder-mount?page=${mountPage}&q=${q}&status=${status}&pair=${pair}&view=${view}&method=${method}`)
    .then(renderMount)
    .catch(e => notify(e.message, true));
};

/* ==================== 2 · 冲突管理视图 ==================== */

const renderConflictCard = item => {
  const badges = (item.conflict_types || []).map((t, i) =>
    node('span', { className: `conflict-type-badge type-${t}`, textContent: item.conflict_labels?.[i] || t })
  );

  if (item.item_type === 'link') {
    const link = item.link;
    const methodBadge = mountMethodLabel(item.methods);
    const viewEvidenceBtn = node('button', {
      className: 'primary',
      type: 'button',
      textContent: `🔍 查看全部证据与原始材料 (${item.evidence_count} 组)`,
    });
    viewEvidenceBtn.onclick = () => openMountReviewDialog({ type: 'link', link });

    return node('div', { className: 'conflict-card' }, [
      node('div', { className: 'conflict-card-head' }, [
        node('div', { className: 'conflict-badges' }, [
          node('span', { className: 'mount-stage-pill', textContent: item.pair }),
          node('span', { className: 'mount-sha-tag', textContent: `来源: ${methodBadge}` }),
          ...badges,
        ]),
        node('span', { className: `mount-status-badge status-${item.status}`, textContent: mountStatusText(item.status) }),
      ]),
      item.conflict_reasons?.length
        ? node('div', { className: 'conflict-reasons', textContent: item.conflict_reasons.join(' ｜ ') })
        : '',
      node('div', { className: 'conflict-sides' }, [
        node('div', { className: 'conflict-side-box' }, [
          node('small', { style: 'color:#64748b;font-weight:600;', textContent: '冲突甲方项目目录' }),
          mountFolderNode(item.left, true),
        ]),
        node('div', { className: 'conflict-side-box' }, [
          node('small', { style: 'color:#64748b;font-weight:600;', textContent: '冲突乙方项目目录' }),
          mountFolderNode(item.right, true),
        ]),
      ]),
      node('div', { className: 'conflict-actions' }, [
        viewEvidenceBtn,
        ...mountActionButtons(link),
      ]),
      renderAuditHistory(item.confirmation),
    ]);
  }

  if (item.item_type === 'code_ownership') {
    const dir = item.directory || {};
    const viewCodeBtn = node('button', {
      className: 'primary',
      type: 'button',
      textContent: `🔍 查看该目录全部代码证据与原始材料 (${item.evidence_count} 条)`,
    });
    viewCodeBtn.onclick = () => showCodeEvidenceModal(dir.id, `${dir.stage}「${dir.name}」项目代码归属证据`);

    return node('div', { className: 'conflict-card' }, [
      node('div', { className: 'conflict-card-head' }, [
        node('div', { className: 'conflict-badges' }, [
          node('span', { className: 'mount-stage-pill', textContent: dir.stage || item.pair }),
          node('span', { className: 'mount-sha-tag', textContent: 'M1 代码归属核查' }),
          ...badges,
        ]),
        node('span', { className: 'mount-status-badge status-review', textContent: '待人工核实归属' }),
      ]),
      node('div', { className: 'conflict-reasons', textContent: (item.conflict_reasons || []).join(' ｜ ') }),
      node('div', { className: 'conflict-side-box' }, [
        mountFolderNode(dir, true),
        node('p', { style: 'margin:4px 0 0;font-size:11px;color:#1e293b;', textContent: `涉及项目代码：${(item.codes || []).join('、')}` }),
      ]),
      node('div', { className: 'conflict-actions' }, [viewCodeBtn]),
    ]);
  }

  // item_type === 'directory' (项目文件夹边界存在疑问 / unresolved)
  const dir = item.directory || {};
  return node('div', { className: 'conflict-card' }, [
    node('div', { className: 'conflict-card-head' }, [
      node('div', { className: 'conflict-badges' }, [
        node('span', { className: 'mount-stage-pill', textContent: dir.stage || item.pair }),
        node('span', { className: 'mount-sha-tag', textContent: dir.node_type === 'unresolved' ? 'Agent 判定: 待定(unresolved)' : '目录边界复核' }),
        ...badges,
      ]),
      node('span', { className: 'mount-status-badge status-review', textContent: '边界待复核' }),
    ]),
    node('div', { className: 'conflict-reasons', textContent: (item.conflict_reasons || []).join(' ｜ ') }),
    node('div', { className: 'conflict-side-box' }, [
      mountFolderNode(dir, true),
      dir.evidence_paths?.length
        ? node('small', { style: 'color:#475569;margin-top:4px;', textContent: `关联证据路径：${dir.evidence_paths.join('、')}` })
        : '',
    ]),
  ]);
};

const renderConflicts = data => {
  const counts = data.counts || {};
  const activeType = $('#conflictTypeFilter').value;
  const chips = [
    { key: 'all', label: '全部冲突与审核项', count: data.total_items || 0 },
    { key: 'm1_m2_conflict', label: 'M1和M2证据矛盾', count: counts.m1_m2_conflict || 0 },
    { key: 'unresolved_ownership', label: '项目代码归属不明', count: counts.unresolved_ownership || 0 },
    { key: 'multi_parcel_same_code', label: '相同代码涉及多个宗地', count: counts.multi_parcel_same_code || 0 },
    { key: 'boundary_review', label: '项目文件夹边界存疑', count: counts.boundary_review || 0 },
    { key: 'shared_attachment', label: '多个目录共用相同附件', count: counts.shared_attachment || 0 },
    { key: 'stale_confirmation', label: '新证据变化需复核', count: counts.stale_confirmation || 0 },
    { key: 'audited', label: '已有人工作出决定', count: counts.audited || 0 },
  ];

  $('#conflictSummaryBar').replaceChildren(...chips.map(c => {
    const btn = node('button', {
      type: 'button',
      className: `conflict-chip ${activeType === c.key ? 'active' : ''}`,
    }, [
      node('span', { textContent: c.label }),
      node('strong', { textContent: String(c.count) }),
    ]);
    btn.onclick = () => {
      $('#conflictTypeFilter').value = c.key;
      conflictPage = 1;
      loadConflicts();
    };
    return btn;
  }));

  $('#conflictCountInfo').textContent = `当前筛选共 ${data.count || 0} 项`;
  $('#conflictPage').textContent = `${data.page || 1} / ${data.pages || 1}`;
  $('#conflictPrev').disabled = (data.page || 1) <= 1;
  $('#conflictNext').disabled = (data.page || 1) >= (data.pages || 1);

  const list = data.items || [];
  if (!list.length) {
    $('#conflictList').replaceChildren(node('div', { className: 'empty-state' }, [
      node('h3', { textContent: '当前筛选条件下没有冲突或待复核记录' }),
    ]));
    return;
  }
  $('#conflictList').replaceChildren(...list.map(renderConflictCard));
};

const loadConflicts = () => {
  const ctype = encodeURIComponent($('#conflictTypeFilter').value);
  const stage = encodeURIComponent($('#conflictStageFilter').value);
  const method = encodeURIComponent($('#conflictMethodFilter').value);
  const status = encodeURIComponent($('#conflictStatusFilter').value);
  const q = encodeURIComponent($('#conflictQuery').value.trim());
  return api(`/api/folder-mount/conflicts?page=${conflictPage}&conflict_type=${ctype}&stage=${stage}&method=${method}&status=${status}&q=${q}`)
    .then(renderConflicts)
    .catch(e => notify(e.message, true));
};

/* ==================== 事件绑定 ==================== */

document.querySelectorAll('.module-tabs button').forEach(btn => {
  btn.onclick = () => {
    const mod = btn.dataset.module;
    document.querySelectorAll('.module-tabs button').forEach(b => {
      const active = b === btn;
      b.classList.toggle('active', active);
      b.setAttribute('aria-pressed', String(active));
    });
    $('#mountModule').classList.toggle('hidden', mod !== 'mount');
    $('#conflictsModule').classList.toggle('hidden', mod !== 'conflicts');
    if (mod === 'conflicts') loadConflicts();
    else loadMount();
  };
});

$('#mountQuery').oninput = () => { mountPage = 1; loadMount(); };
$('#mountView').onchange = () => { mountPage = 1; loadMount(); };
$('#mountMethodFilter').onchange = () => { mountPage = 1; loadMount(); };
$('#mountPair').onchange = () => { mountPage = 1; loadMount(); };
$('#mountFilter').onchange = () => { mountPage = 1; loadMount(); };
$('#mountPrev').onclick = () => { mountPage = Math.max(1, mountPage - 1); loadMount(); };
$('#mountNext').onclick = () => { mountPage += 1; loadMount(); };

$('#conflictTypeFilter').onchange = () => { conflictPage = 1; loadConflicts(); };
$('#conflictStageFilter').onchange = () => { conflictPage = 1; loadConflicts(); };
$('#conflictMethodFilter').onchange = () => { conflictPage = 1; loadConflicts(); };
$('#conflictStatusFilter').onchange = () => { conflictPage = 1; loadConflicts(); };
$('#conflictQuery').oninput = () => { conflictPage = 1; loadConflicts(); };
$('#conflictPrev').onclick = () => { conflictPage = Math.max(1, conflictPage - 1); loadConflicts(); };
$('#conflictNext').onclick = () => { conflictPage += 1; loadConflicts(); };
$('#openCodeEvidenceBtn').onclick = () => showCodeEvidenceModal('', '全库 M1 项目代码提取与归属证据');

$('#closeMountReview')?.addEventListener('click', () => {
  $('#mountReviewDialog')?.close();
});

loadMount();
