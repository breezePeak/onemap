"""七阶段项目大目录智能识别、证据收集与持久化仓储。

核心机制（程序扫描并提供证据 → Agent 理解目录结构 → Agent 判断项目边界 → 程序校验及存库）：
1. 证据提供：按需收集七阶段目录树、父子层级关系、文件数量/类型/代表性文件名、材料摘要及项目代码线索；
2. Agent 语义判断：支持將目录判定为 project / collection / classification / material / unresolved；
3. 树结构先行与防提前锁定：先检查必要的子目录结构再确定边界，父目录为汇总/分类或未确定时不锁定子项目；
4. 严格校验与存库：校验真实路径与证据，保护 manual 人工确认边界，增量复用未变更目录，变更或冲突时触发复核。
"""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import zipfile
from xml.etree import ElementTree

from extract_archives import disk_path
from material_evidence import PATTERNS, canonical_code, valid_project_code
from source_config import SOURCE, STAGES, STAGE_DICT, STAGE_FOLDER_TO_ID, EXCLUDED_DIRECTORIES

RULE_VERSION = 'project-catalog-7stages-v6-agent-semantic'
VALID_NODE_TYPES = {'project', 'collection', 'classification', 'material', 'unresolved'}
ARCHIVES = {'.zip', '.rar', '.7z'}

# 常见确定性纯业务分类/归档层名称（仅用于快速筛选明显分类目录，不替代 Agent 对复杂目录的判断）
CLASSIFICATION_DIR_NAMES = {
    '出让', '划拨', '01划拨档案整理', '04出让档案整理', '04划拨用地',
    '土地首次登记附件', '规划条件盖章版',
    '用地预审及选址论证',
    '数据图层', '收储材料', '计划供应',
    '土地变更协议', '地块并宗调整', '土地租赁事宜', '延期开工', '变更协议',
    '开工提醒函', '划拨协议书', '划拨决定书', '批复', '未完成',
    '一码管地报批相关材料汇总提供',
}

# 常见项目内部材料分类小目录（属于项目内部的 material，绝不能作为项目大目录）
MATERIAL_SUBDIR_NAMES = {
    '附件', '扫描件', '报件', '报批材料', '材料', '矢量', '红线', '图件',
    '06 测量', '测量', '初审意见', '成果表',
    '省厅', '耕占', '规费', '听证', '听证社保', 'PDF0906', 'PDF新',
    '回复函', '宗地图', '成果资料', '测量报告', '界址点成果表', '示意图',
    '交地确认书', '流程附件', '供地批复', '用地批复', '组卷材料', '上会材料',
    '其他材料', '其他文件', '呈批件',
    '1项目红线与权属', '2公告文件和稳评报告', '3组卷材料', '4用地批复',
    '项目四址范围（附图）、用地权属及拆迁情况',
    '建设项目批准、核准、备案文件',
    '建设项目用地预审与选址意见书（涉及划拨用地、新增建设用地的项目）',
    '使用土地的证明文件 (不占用土地的管线类项目除外；属原有房屋改建、扩建的建设工程，提交房屋不动产权证或房产证)。',
    '建设用地规划许可证申请表',
    '建设工程规划许可证申请表',
    '建设工程设计方案、修建性详细规划、总平面图（需要进行日照分析、交通影响评价的，同时提交有关报告）',
    '国有土地使用权出让合同（涉及出让用地的建设项目）',
    '项目审批、核准备案文件',
    '没找到', '没找到的',
}


def catalog_identifier(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]


def normalize_dir_name(name):
    name = re.sub(r'\.解压$', '', str(name))
    return re.sub(r'\.(?:zip|rar|7z)$', '', name, flags=re.I)


def has_explicit_project_code_or_number(name):
    """检查名称是否包含明确的统一项目代码、地块编号、档案编号等项目标识。"""
    cleaned = normalize_dir_name(name).strip()
    if re.search(PATTERNS['project_code'], cleaned):
        return True
    if re.match(r'^(?:20\d{2}-\d{3,6}|DX[A-Z0-9]+|DDB[A-Z0-9]+|DA[A-Z0-9]+|360\d{9,})', cleaned, re.I):
        return True
    return False


def is_pure_classification(name):
    cleaned = re.sub(r'[(（]\d+[)）]$', '', normalize_dir_name(name)).strip()
    if cleaned in CLASSIFICATION_DIR_NAMES:
        return True
    if re.fullmatch(r'\d{4}(?:年度|年)?', cleaned):
        return True
    if re.fullmatch(r'\d{4}(?:年度|年)?(?:用地)?规划条件(?:汇编|材料)?', cleaned):
        return True
    if re.fullmatch(r'\d{4}[-—至]+\d{4}.*报批.*', cleaned):
        return True
    if re.fullmatch(r'赣江新区\d{4}年报批材料', cleaned):
        return True
    if re.match(r'^情况整理一览表', cleaned):
        return True
    # 若带有明确项目编号（如 2024-000199...宗地面积测量合集），不能简单当作纯分类目录吞掉项目
    if any(word in cleaned for word in ('汇编', '汇总', '合集', '合订本')) and not has_explicit_project_code_or_number(cleaned):
        return True
    return False


def is_material_leaf_dir(name):
    cleaned = re.sub(r'[(（]\d+[)）]$', '', normalize_dir_name(name)).strip()
    if cleaned.casefold() in {'pdf', 'cad', 'shp', 'word', 'excel', 'jpg', 'jpeg', 'png', 'tif', 'tiff', 'dwg', 'dxf'}:
        return True
    if cleaned in MATERIAL_SUBDIR_NAMES:
        return True
    if has_explicit_project_code_or_number(cleaned):
        return False
    if any(k in cleaned for k in ('申请表', '情况说明', '设计方案', '红线图', '四址范围', '证明文件', '宗地面积测量', '线面文件')):
        return True
    if re.fullmatch(r'\d{1,2}\s*测量.*', cleaned):
        return True
    if re.fullmatch(r'[0-9]{1,2}[\.\s、_-]*(?:项目红线.*|公告文件.*|组卷材料.*|用地批复|批复|呈批件|测量.*|上会材料.*|其他材料.*|其他文件.*|信息发布|成交确认|合同签订|交易卷宗|土地交易任务书|出让方案及批复|照片及视频|规划条件|评估备案号|去函|回函|划拨决定书|矢量|一书.*)', cleaned):
        return True
    if re.fullmatch(r'0?[1-9].*阶段.*|0?[1-9].*首次登记.*', cleaned):
        return True
    if re.fullmatch(r'(?:听证PDF.*|国有建设用地交地确认书|国有建设用地划拨决定书|不动产.*)', cleaned):
        return True
    return False


def _extract_codes_from_text(text):
    hits = []
    for m in re.finditer(PATTERNS['project_code'], str(text or '')):
        raw = m.group(0)
        if valid_project_code(raw):
            hits.append(canonical_code(raw))
    return sorted(set(hits))


def _read_text_snippet(path, max_chars=500):
    """按需读取单个代表性文件的简短内容摘要，不把全量内容塞入上下文。"""
    p = disk_path(Path(path))
    if not p.is_file():
        return ''
    suffix = p.suffix.lower()
    try:
        if suffix in ('.txt', '.md', '.csv', '.json', '.xml'):
            if p.stat().st_size > 5 * 1024 * 1024:
                return ''
            raw = p.read_bytes()[:8192]
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('gb18030', errors='replace')
            return re.sub(r'\s+', ' ', text).strip()[:max_chars]
        if suffix == '.docx':
            with zipfile.ZipFile(p) as zf:
                if 'word/document.xml' not in zf.namelist():
                    return ''
                tree = ElementTree.fromstring(zf.read('word/document.xml'))
                text = ' '.join(''.join(el.itertext()).strip() for el in tree.iter() if el.text)
                return re.sub(r'\s+', ' ', text).strip()[:max_chars]
        if suffix == '.pdf':
            cache_file = Path(__file__).resolve().parent.parent / 'work/pdf_text_cache.json'
            if cache_file.is_file():
                try:
                    cache = json.loads(cache_file.read_text(encoding='utf-8'))
                    item = cache.get(str(Path(path)))
                    if item and item.get('pages'):
                        return re.sub(r'\s+', ' ', ' '.join(item['pages'][:2])).strip()[:max_chars]
                except Exception:
                    pass
            from extract_pdfs import read_pdf_text
            pages = read_pdf_text(p)
            return re.sub(r'\s+', ' ', ' '.join(pages[:2])).strip()[:max_chars]
    except Exception:
        return ''
    return ''


class ProjectCatalog:
    """管理七个阶段的项目大目录智能识别、Agent 决策持久化与归属检索。"""

    def __init__(self, storage_dir=None, root=None):
        self.root = Path(root or SOURCE).resolve()
        if storage_dir is None:
            if self.root != Path(SOURCE).resolve():
                self.storage_dir = self.root / '.catalog'
            else:
                self.storage_dir = Path(__file__).resolve().parent.parent / 'work/folder_mount'
        else:
            self.storage_dir = Path(storage_dir)

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.storage_dir / 'project_catalog.sqlite'
        self.storage_file = self.storage_dir / 'project_catalog.json'

        self.db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.init_db()
        self.db.execute('CREATE TABLE IF NOT EXISTS catalog_source (root TEXT NOT NULL)')
        self.db.execute('BEGIN IMMEDIATE')
        bound = self.db.execute('SELECT root FROM catalog_source').fetchone()
        old_paths = [row[0] for row in self.db.execute('SELECT physical_path FROM projects')]
        if not old_paths and self.storage_file.is_file():
            try:
                old_paths = [p['physical_path'] for p in json.loads(self.storage_file.read_text(encoding='utf-8')).get('projects', {}).values()]
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass
        if bound and bound[0] != str(self.root) or not bound and any(not Path(p).resolve().is_relative_to(self.root) for p in old_paths):
            self.db.close()
            raise ValueError('项目仓储属于其他数据源，请为新数据源使用独立 storage_dir；不能继承旧缓存和确认')
        if not bound:
            self.db.execute('INSERT INTO catalog_source VALUES (?)', (str(self.root),))
        self.db.commit()
        self.projects = {}  # {id: project_dict}
        self.decisions = {}  # {id: decision_dict}
        self.stage_prefixes = {}  # {stage_id: [(prefix_parts, proj_dict), ...]}
        self.load()

    def init_db(self):
        self.db.execute('CREATE TABLE IF NOT EXISTS boundary_history (project_id TEXT, changed_at TEXT, before_json TEXT, after_json TEXT)')
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                stage_id TEXT,
                stage_name TEXT,
                project_root TEXT,
                project_name TEXT,
                physical_path TEXT,
                depth INTEGER,
                reason TEXT,
                confidence TEXT,
                status TEXT,
                files INTEGER DEFAULT 0,
                updated_at TEXT,
                node_type TEXT DEFAULT 'project',
                decision_source TEXT DEFAULT 'rule',
                evidence_paths TEXT DEFAULT '[]',
                structure_version TEXT DEFAULT '',
                boundary_review INTEGER DEFAULT 0,
                boundary_review_reason TEXT DEFAULT ''
            )
        """)
        existing_cols = {row[1] for row in self.db.execute("PRAGMA table_info(projects)").fetchall()}
        for col, col_def in (
            ('node_type', "TEXT DEFAULT 'project'"),
            ('decision_source', "TEXT DEFAULT 'rule'"),
            ('evidence_paths', "TEXT DEFAULT '[]'"),
            ('structure_version', "TEXT DEFAULT ''"),
            ('boundary_review', "INTEGER DEFAULT 0"),
            ('boundary_review_reason', "TEXT DEFAULT ''"),
        ):
            if col not in existing_cols:
                self.db.execute(f"ALTER TABLE projects ADD COLUMN {col} {col_def}")

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS directory_decisions (
                id TEXT PRIMARY KEY,
                stage_id TEXT,
                rel_path TEXT,
                physical_path TEXT,
                node_type TEXT,
                project_name TEXT,
                parent_project_root TEXT DEFAULT '',
                reason TEXT,
                evidence_paths TEXT DEFAULT '[]',
                project_codes TEXT DEFAULT '[]',
                confidence TEXT,
                status TEXT,
                decision_source TEXT,
                structure_version TEXT,
                updated_at TEXT
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_stage_root ON projects (stage_id, project_root)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_decisions_stage_path ON directory_decisions (stage_id, rel_path)")
        self.db.commit()

    def resolve_physical_dir(self, stage_id, rel_path):
        """将阶段内相对路径（含压缩包或 .解压 路径）解析为磁盘真实目录路径。
        支持原地目录与同名 .zip.解压 目录并存时的穿透解析。
        """
        if stage_id not in STAGE_DICT:
            return None
        stage_root = self.root / STAGE_DICT[stage_id]['folder_name']
        parts = [p for p in re.split(r'[/\\!]', str(rel_path or '').strip('/\\')) if p]
        if not parts:
            return stage_root

        def step_candidates(base_dir, part):
            results = []
            if not disk_path(base_dir).is_dir():
                return results
            norm_part = normalize_dir_name(part)
            # 直接匹配、.解压 匹配、压缩包内同名包裹层匹配
            for cand in (
                base_dir / part,
                base_dir / (part + '.解压'),
                base_dir / (norm_part + '.zip.解压'),
                base_dir / (norm_part + '.rar.解压'),
                base_dir / (norm_part + '.7z.解压'),
                base_dir / (norm_part + '.zip.解压') / norm_part,
                base_dir / (norm_part + '.rar.解压') / norm_part,
                base_dir / (norm_part + '.7z.解压') / norm_part,
            ):
                if disk_path(cand).exists() and cand not in results:
                    results.append(cand)
            try:
                with os.scandir(disk_path(base_dir)) as it:
                    for entry in it:
                        if normalize_dir_name(entry.name) == norm_part:
                            p = base_dir / entry.name
                            if p not in results:
                                results.append(p)
                            if entry.is_dir(follow_symlinks=False) and entry.name.endswith('.解压'):
                                wrapped = p / norm_part
                                if disk_path(wrapped).exists() and wrapped not in results:
                                    results.append(wrapped)
            except OSError:
                pass
            return results

        frontier = [stage_root]
        for part in parts:
            next_frontier = []
            for base in frontier:
                # 若当前 base 是 .解压 目录且内部有一层同名目录，也允许从该同名目录继续向下查找
                bases_to_try = [base]
                if base.name.endswith('.解压'):
                    wrapped = base / normalize_dir_name(base.name)
                    if disk_path(wrapped).is_dir():
                        bases_to_try.append(wrapped)
                for b in bases_to_try:
                    for c in step_candidates(b, part):
                        if c not in next_frontier:
                            next_frontier.append(c)
            if not next_frontier:
                # 检查是否位于阶段根目录下尚未展开的同名 .zip 压缩包内部
                top_zip = stage_root / (normalize_dir_name(parts[0]) + '.zip')
                if disk_path(top_zip).is_file():
                    if not hasattr(self, '_zip_prefix_cache'):
                        self._zip_prefix_cache = {}
                    zkey = str(top_zip)
                    if zkey not in self._zip_prefix_cache:
                        prefixes = set()
                        try:
                            with zipfile.ZipFile(disk_path(top_zip)) as zf:
                                for name in zf.namelist():
                                    segs = [normalize_dir_name(x) for x in name.replace('\\', '/').split('/') if x]
                                    for k in range(1, len(segs) + 1):
                                        prefixes.add('/'.join(segs[:k]))
                        except Exception:
                            pass
                        self._zip_prefix_cache[zkey] = prefixes
                    target_norm = '/'.join(normalize_dir_name(x) for x in parts if x)
                    if target_norm in self._zip_prefix_cache[zkey]:
                        return top_zip
                return frontier[0] / part
            frontier = next_frontier

        chosen = frontier[0]
        if disk_path(chosen).is_file() and chosen.suffix.lower() in ARCHIVES:
            ext_dir = chosen.with_name(chosen.name + '.解压')
            if disk_path(ext_dir).is_dir():
                return ext_dir
        return chosen

    def compute_structure_version(self, phys_dir):
        """计算目录的直接子目录与直接文件列表指纹，用于判断目录结构是否变化。"""
        p = Path(phys_dir) if phys_dir else None
        if not p:
            return ''
        if disk_path(p).is_file() and p.suffix.lower() in ARCHIVES:
            ext = p.with_name(p.name + '.解压')
            if disk_path(ext).is_dir():
                p = ext
        if not disk_path(p).is_dir():
            return ''
        dirs, files = [], []
        try:
            with os.scandir(disk_path(p)) as it:
                for entry in it:
                    if entry.name in EXCLUDED_DIRECTORIES or entry.name.startswith('~$'):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append(normalize_dir_name(entry.name))
                    elif entry.is_file(follow_symlinks=False):
                        files.append(entry.name)
        except OSError:
            return ''
        payload = json.dumps([sorted(set(dirs)), sorted(files)], ensure_ascii=False)
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]

    def _inspect_child_dirs(self, phys_dir):
        """查看物理目录（穿透同名单层解压目录）下的直接子目录与直接业务文件。"""
        p = Path(phys_dir) if phys_dir else None
        if not p:
            return [], []
        if disk_path(p).is_file() and p.suffix.lower() in ARCHIVES:
            ext = p.with_name(p.name + '.解压')
            if disk_path(ext).is_dir():
                p = ext
        elif not disk_path(p).is_dir():
            ext = p.with_name(p.name + '.解压')
            if disk_path(ext).is_dir():
                p = ext
        if not disk_path(p).is_dir():
            return [], []

        def list_entries(target):
            sub_dirs, sub_files = [], []
            try:
                with os.scandir(disk_path(target)) as it:
                    for entry in it:
                        if entry.name in EXCLUDED_DIRECTORIES or entry.name.startswith('~$') or entry.name.startswith('.onemap-'):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            sub_dirs.append(target / entry.name)
                        elif entry.is_file(follow_symlinks=False):
                            if entry.name.lower().endswith(('.zip', '.rar', '.7z')):
                                ext_sub = target / (entry.name + '.解压')
                                if ext_sub.is_dir():
                                    sub_dirs.append(ext_sub)
                                else:
                                    sub_files.append(target / entry.name)
                            else:
                                sub_files.append(target / entry.name)
            except OSError:
                pass
            # 去重同名原目录与 .解压 目录
            by_norm = {}
            for d in sorted(sub_dirs, key=lambda x: (x.name.endswith('.解压'), x.name)):
                by_norm[normalize_dir_name(d.name)] = d
            return list(by_norm.values()), sub_files

        dirs, files = list_entries(p)
        # 若解压包下只有唯一一个与包同名的包裹目录，继续向下看一层
        if len(dirs) == 1 and not files and normalize_dir_name(dirs[0].name) == normalize_dir_name(p.name):
            dirs, files = list_entries(dirs[0])
        return dirs, files

    def _is_multi_project_container_on_disk(self, stage_id, rel_parts):
        """检查某个目录节点在磁盘上是否包含多个独立子项目（例如批次包含多个项目、交地号包含多个宗地）。"""
        if not rel_parts:
            return False
        rel_path = '/'.join(rel_parts)
        dec_id = catalog_identifier(f"{stage_id}:{ '/'.join(normalize_dir_name(p) for p in rel_parts) }")
        dec = self.decisions.get(dec_id)
        if dec:
            if dec['node_type'] in ('collection', 'classification'):
                return True
            if dec['node_type'] == 'project':
                return False

        phys_dir = self.resolve_physical_dir(stage_id, rel_path)
        child_dirs, child_files = self._inspect_child_dirs(phys_dir)
        if not child_dirs:
            return False

        non_material_children = []
        parcel_or_coded_children = 0
        for cd in child_dirs:
            cname = normalize_dir_name(cd.name)
            if is_pure_classification(cname) or is_material_leaf_dir(cname):
                continue
            non_material_children.append(cname)
            if has_explicit_project_code_or_number(cname) or re.match(r'^\d{2}\s*[^\s\d]', cname):
                parcel_or_coded_children += 1

        curr_name = normalize_dir_name(rel_parts[-1])
        # 1. 包含多个宗地/编号子项目（如 赣国土资网交地 下含多个 DXJJ 宗地，或汇总下含 01..10 项目）
        if parcel_or_coded_children >= 2:
            return True
        # 2. S4 交地确认书文号目录，其下只要存在独立 DX/DDB 宗地子目录，父目录即为交地批次容器而非单宗地
        if stage_id == 'S4' and '交地' in curr_name and parcel_or_coded_children >= 1:
            return True
        # 3. 含“汇总/汇编/合集/批次”且其下有多个非材料业务子目录
        if any(w in curr_name for w in ('汇总', '汇编', '合集', '批次')) and len(non_material_children) >= 2:
            return True
        return False

    def load(self):
        self.projects = {}
        self.decisions = {}

        cursor = self.db.execute("""
            SELECT id, stage_id, rel_path, physical_path, node_type, project_name,
                   parent_project_root, reason, evidence_paths, project_codes,
                   confidence, status, decision_source, structure_version, updated_at
            FROM directory_decisions
        """)
        for row in cursor.fetchall():
            dec = dict(
                id=row[0], stage_id=row[1], rel_path=row[2], physical_path=row[3],
                node_type=row[4], project_name=row[5], parent_project_root=row[6] or '',
                reason=row[7],
                evidence_paths=json.loads(row[8] or '[]'),
                project_codes=json.loads(row[9] or '[]'),
                confidence=row[10], status=row[11], decision_source=row[12],
                structure_version=row[13] or '', updated_at=row[14]
            )
            self.decisions[dec['id']] = dec

        cursor = self.db.execute("""
            SELECT id, stage_id, stage_name, project_root, project_name, physical_path,
                   depth, reason, confidence, status, files, updated_at,
                   node_type, decision_source, evidence_paths, structure_version,
                   boundary_review, boundary_review_reason
            FROM projects
        """)
        for row in cursor.fetchall():
            proj = dict(
                id=row[0], stage_id=row[1], stage_name=row[2], project_root=row[3],
                project_name=row[4], physical_path=row[5], depth=row[6],
                reason=row[7], confidence=row[8], status=row[9], files=row[10], updated_at=row[11],
                node_type=row[12] or 'project',
                decision_source=row[13] or ('manual' if row[9] == 'manual' else 'rule'),
                evidence_paths=json.loads(row[14] or '[]'),
                structure_version=row[15] or '',
                boundary_review=bool(row[16]),
                boundary_review_reason=row[17] or '',
            )
            self.projects[proj['id']] = proj

        if not self.projects and self.storage_file.is_file():
            try:
                data = json.loads(self.storage_file.read_text(encoding='utf-8'))
                for p in data.get('projects', {}).values():
                    p.setdefault('node_type', 'project')
                    p.setdefault('decision_source', 'manual' if p.get('status') == 'manual' else 'rule')
                    p.setdefault('evidence_paths', [])
                    p.setdefault('structure_version', '')
                    p.setdefault('boundary_review', False)
                    p.setdefault('boundary_review_reason', '')
                    self.upsert_db(p)
                    self.projects[p['id']] = p
                for d in data.get('decisions', {}).values():
                    self.upsert_decision_db(d)
                    self.decisions[d['id']] = d
                self.db.commit()
            except Exception:
                pass

        self._evict_invalid_cached_projects()
        self.rebuild_prefixes()

    def _evict_invalid_cached_projects(self):
        """清理旧数据库中明显错误的非人工记录（如材料子目录、纯分类目录、被判定为非项目的节点、或吞并了子项目的父容器）。"""
        filtered = {}
        for key, p in self.projects.items():
            if p.get('status') == 'manual':
                filtered[key] = p
                continue
            leaf_name = normalize_dir_name(p['project_root'].split('/')[-1])
            norm_parts = [normalize_dir_name(x) for x in p['project_root'].split('/') if x]
            dec_id = catalog_identifier(f"{p['stage_id']}:{'/'.join(norm_parts)}")
            dec = self.decisions.get(dec_id)
            if dec and dec['node_type'] != 'project':
                continue
            if not dec and (is_pure_classification(leaf_name) or is_material_leaf_dir(leaf_name)):
                continue
            if p.get('decision_source') not in ('agent', 'manual') and self._is_multi_project_container_on_disk(p['stage_id'], norm_parts):
                continue
            filtered[key] = p

        # 若某个非 manual 且非 agent 确认的旧项目存在更深层的独立子项目，移除父级错误锁死记录
        by_stage = {}
        for p in filtered.values():
            norm_parts = tuple(normalize_dir_name(x) for x in p['project_root'].split('/') if x)
            by_stage.setdefault(p['stage_id'], []).append((norm_parts, p))

        to_remove = set()
        for sid, items in by_stage.items():
            for parts_a, proj_a in items:
                if proj_a.get('status') == 'manual' or proj_a.get('decision_source') == 'agent':
                    continue
                for parts_b, proj_b in items:
                    if len(parts_b) > len(parts_a) and parts_b[:len(parts_a)] == parts_a:
                        to_remove.add(proj_a['id'])
                        break

        for rid in to_remove:
            filtered.pop(rid, None)
        evicted_ids = set(self.projects.keys()) - set(filtered.keys())
        if evicted_ids:
            for eid in evicted_ids:
                self.db.execute('DELETE FROM projects WHERE id=?', (eid,))
            self.db.commit()
        self.projects = filtered

    def prune_missing_disk_projects(self):
        """清理在真实磁盘数据源上已不存在的非 manual 历史遗留项目记录。"""
        removed = []
        for pid, p in list(self.projects.items()):
            if p.get('status') == 'manual':
                continue
            phys = self.resolve_physical_dir(p['stage_id'], p['project_root'])
            if phys is None or not disk_path(phys).exists():
                removed.append({'id': pid, 'stage_id': p['stage_id'], 'project_root': p['project_root'], 'project_name': p['project_name']})
                self.projects.pop(pid, None)
                self.db.execute('DELETE FROM projects WHERE id=?', (pid,))
        if removed:
            self.rebuild_prefixes()
            self.save()
        return removed

    def rebuild_prefixes(self):
        """为已存库的项目构建按阶段的前缀索引（深度大的在前，优先匹配最精准的子项目）。"""
        self.stage_prefixes = {sid: [] for sid, _ in STAGES}
        for proj in self.projects.values():
            if proj['status'] != 'manual':
                leaf_name = normalize_dir_name(proj['project_root'].split('/')[-1])
                norm_parts = [normalize_dir_name(p) for p in proj['project_root'].split('/') if p]
                dec_id = catalog_identifier(f"{proj['stage_id']}:{'/'.join(norm_parts)}")
                dec = self.decisions.get(dec_id)
                if dec and dec['node_type'] != 'project':
                    continue
                if not dec and (is_pure_classification(leaf_name) or is_material_leaf_dir(leaf_name)):
                    continue
            sid = proj['stage_id']
            if sid not in self.stage_prefixes:
                self.stage_prefixes[sid] = []
            parts = [normalize_dir_name(p) for p in proj['project_root'].split('/') if p]
            self.stage_prefixes[sid].append((parts, proj))

        for sid in self.stage_prefixes:
            self.stage_prefixes[sid].sort(key=lambda item: len(item[0]), reverse=True)

    def upsert_db(self, proj):
        self.db.execute("""
            INSERT INTO projects (
                id, stage_id, stage_name, project_root, project_name, physical_path,
                depth, reason, confidence, status, files, updated_at,
                node_type, decision_source, evidence_paths, structure_version,
                boundary_review, boundary_review_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                project_root=excluded.project_root,
                project_name=excluded.project_name,
                physical_path=excluded.physical_path,
                depth=excluded.depth,
                reason=excluded.reason,
                confidence=excluded.confidence,
                status=excluded.status,
                updated_at=excluded.updated_at,
                node_type=excluded.node_type,
                decision_source=excluded.decision_source,
                evidence_paths=excluded.evidence_paths,
                structure_version=excluded.structure_version,
                boundary_review=excluded.boundary_review,
                boundary_review_reason=excluded.boundary_review_reason
        """, (
            proj['id'], proj['stage_id'], proj['stage_name'], proj['project_root'],
            proj['project_name'], proj['physical_path'], proj['depth'],
            proj['reason'], proj['confidence'], proj['status'], proj.get('files', 0), proj['updated_at'],
            proj.get('node_type', 'project'),
            proj.get('decision_source', 'rule'),
            json.dumps(proj.get('evidence_paths', []), ensure_ascii=False),
            proj.get('structure_version', ''),
            1 if proj.get('boundary_review') else 0,
            proj.get('boundary_review_reason', '')
        ))

    def upsert_decision_db(self, dec):
        self.db.execute("""
            INSERT INTO directory_decisions (
                id, stage_id, rel_path, physical_path, node_type, project_name,
                parent_project_root, reason, evidence_paths, project_codes,
                confidence, status, decision_source, structure_version, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                rel_path=excluded.rel_path,
                physical_path=excluded.physical_path,
                node_type=excluded.node_type,
                project_name=excluded.project_name,
                parent_project_root=excluded.parent_project_root,
                reason=excluded.reason,
                evidence_paths=excluded.evidence_paths,
                project_codes=excluded.project_codes,
                confidence=excluded.confidence,
                status=excluded.status,
                decision_source=excluded.decision_source,
                structure_version=excluded.structure_version,
                updated_at=excluded.updated_at
        """, (
            dec['id'], dec['stage_id'], dec['rel_path'], dec['physical_path'],
            dec['node_type'], dec.get('project_name', ''), dec.get('parent_project_root', ''),
            dec['reason'],
            json.dumps(dec.get('evidence_paths', []), ensure_ascii=False),
            json.dumps(dec.get('project_codes', []), ensure_ascii=False),
            dec.get('confidence', 'high'), dec.get('status', 'confirmed'),
            dec.get('decision_source', 'agent'), dec.get('structure_version', ''),
            dec['updated_at']
        ))

    def save(self):
        """将当前仓储同步写入 SQLite 与 JSON 镜像。"""
        self.db.commit()
        payload = {
            'rule_version': RULE_VERSION,
            'updated_at': datetime.now().isoformat(timespec='seconds'),
            'count': len(self.projects),
            'projects': self.projects,
            'decisions_count': len(self.decisions),
            'decisions': self.decisions,
        }
        temp_file = self.storage_file.with_suffix('.tmp')
        temp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        temp_file.replace(self.storage_file)

    def _decision_for_parts(self, stage_id, normalized_parts):
        dec_id = catalog_identifier(f"{stage_id}:{'/'.join(normalized_parts)}")
        dec = self.decisions.get(dec_id)
        if not dec:
            return None
        # 若目录结构发生变化且非 manual，缓存自动失效需重判
        if dec.get('status') != 'manual' and dec.get('structure_version'):
            phys = self.resolve_physical_dir(stage_id, '/'.join(normalized_parts))
            current_ver = self.compute_structure_version(phys)
            if current_ver and current_ver != dec['structure_version']:
                return None
        return dec

    def find_in_database(self, stage_id, rel_path_parts):
        """【第一优先级】：查询数据库已有项目边界。
        同时保证：
        1. 若当前路径或其祖先被 Agent 明确判定为 unresolved 且无更深层子项目，不命中假项目；
        2. 数据库中浅层规则缓存不能阻止发现更深层的独立子项目。
        """
        if stage_id not in self.stage_prefixes or not rel_path_parts:
            return None

        normalized_target = [normalize_dir_name(p) for p in rel_path_parts if p]
        if not normalized_target:
            return None

        # 检查是否命中已存库项目前缀（已按深度降序排列，最深子项目优先）
        for prefix_parts, proj in self.stage_prefixes[stage_id]:
            if len(normalized_target) >= len(prefix_parts) and normalized_target[:len(prefix_parts)] == prefix_parts:
                # 人工确认边界无条件受保护
                if proj.get('status') == 'manual':
                    return proj
                # 若命中前缀比目标路径浅，且不是 Agent 明确锁定的单项目，检查是否阻止了子项目发现
                if len(normalized_target) > len(prefix_parts):
                    if self._is_multi_project_container_on_disk(stage_id, prefix_parts):
                        # 父目录实际上是多项目汇总容器，剔除该错误缓存，允许继续发现子项目
                        self.projects.pop(proj['id'], None)
                        self.rebuild_prefixes()
                        continue
                    if proj.get('decision_source') != 'agent':
                        # 检查更深层路径中是否存在已经被 Agent 标记为 project 的子项目，或含独立项目编号的子目录
                        for depth in range(len(normalized_target), len(prefix_parts), -1):
                            sub_parts = normalized_target[:depth]
                            sub_dec = self._decision_for_parts(stage_id, sub_parts)
                            if sub_dec and sub_dec['node_type'] == 'project':
                                return None
                            sub_name = sub_parts[-1]
                            if not is_material_leaf_dir(sub_name) and not is_pure_classification(sub_name):
                                if has_explicit_project_code_or_number(sub_name):
                                    return None
                return proj
        return None

    def determine_project_root(self, stage_id, rel_path_parts):
        """【第二优先级】：结合 Agent 存库决策、子目录树结构与筛选规则判定项目大目录。"""
        if not rel_path_parts:
            return None, 'UNRESOLVED', '空路径', 'low'

        normalized_parts = [normalize_dir_name(p) for p in rel_path_parts]

        # 1. 优先检查路径上是否有 Agent 决策记录（从深到浅查找 project，或识别 collection/classification/unresolved）
        for idx in range(len(normalized_parts) - 1, -1, -1):
            sub_norm = normalized_parts[:idx + 1]
            dec = self._decision_for_parts(stage_id, sub_norm)
            if dec:
                if dec['node_type'] == 'project':
                    return '/'.join(rel_path_parts[:idx + 1]), dec.get('project_name') or sub_norm[-1], dec['reason'], dec.get('confidence', 'high')
                if dec['node_type'] == 'unresolved':
                    return None, 'UNRESOLVED', dec['reason'], 'low'
                if dec['node_type'] in ('collection', 'classification') and idx == len(normalized_parts) - 1:
                    # 当前路径本身就是汇总或分类容器，不能作为项目返回
                    return None, 'UNRESOLVED', dec['reason'], 'low'

        # 2. 若当前最后一级本身是多项目汇总容器，或属于纯分类/材料目录且无上级项目，不能把当前容器提前锁定为项目
        if self._is_multi_project_container_on_disk(stage_id, normalized_parts):
            return None, 'UNRESOLVED', '多项目汇总/批次容器，需展开子目录识别独立项目', 'low'

        # helper: 判断第 i 级是否可作为独立项目候选（非纯分类、非材料目录、且在磁盘上不是多项目容器）
        def is_valid_project_candidate(i):
            name = normalized_parts[i]
            if not name or is_pure_classification(name) or is_material_leaf_dir(name):
                return False
            sub_dec = self._decision_for_parts(stage_id, normalized_parts[:i + 1])
            if sub_dec and sub_dec['node_type'] in ('collection', 'classification', 'material', 'unresolved'):
                return False
            if self._is_multi_project_container_on_disk(stage_id, normalized_parts[:i + 1]):
                return False
            return True

        # S1: 用地预审与选址
        if stage_id == 'S1':
            for i, p in enumerate(normalized_parts):
                if p == '用地预审及选址论证' and i + 1 < len(normalized_parts):
                    for index in range(i + 1, len(normalized_parts)):
                        if is_valid_project_candidate(index):
                            return '/'.join(rel_path_parts[:index + 1]), normalized_parts[index], 'S1解压包内独立选址工程项目', 'high'
            if is_valid_project_candidate(0):
                return rel_path_parts[0], normalized_parts[0], 'S1用地预审独立业务项目目录', 'high'

        # S2: 条件出具
        elif stage_id == 'S2':
            first = normalized_parts[0]
            if first == '规划条件盖章版':
                for i in range(1, len(normalized_parts)):
                    if is_valid_project_candidate(i):
                        return '/'.join(rel_path_parts[:i + 1]), normalized_parts[i], 'S2规划条件出具项目', 'high'
            elif is_valid_project_candidate(0):
                return rel_path_parts[0], first, 'S2条件出具业务项目目录', 'high'

        # S3: 批次报批阶段
        elif stage_id == 'S3':
            candidates = [i for i in range(len(normalized_parts)) if is_valid_project_candidate(i)]
            if candidates:
                idx = candidates[-1]
                return '/'.join(rel_path_parts[:idx + 1]), normalized_parts[idx], 'S3用地报批批次/项目大目录', 'high'

        # S4: 土地供应阶段
        elif stage_id == 'S4':
            candidates = [i for i in range(len(normalized_parts)) if is_valid_project_candidate(i)]
            if candidates:
                # 若存在更深层的明确宗地/编号子项目，优先选择子项目而非外层容器
                coded = [i for i in candidates if has_explicit_project_code_or_number(normalized_parts[i])]
                idx = coded[-1] if coded else candidates[0]
                return '/'.join(rel_path_parts[:idx + 1]), normalized_parts[idx], 'S4土地供应宗地/项目大目录', 'high'

        # S5: 土地首次登记
        elif stage_id == 'S5':
            for i in range(len(normalized_parts)):
                if is_valid_project_candidate(i):
                    return '/'.join(rel_path_parts[:i + 1]), normalized_parts[i], 'S5土地首次登记地籍宗地单元', 'high'

        # S6: 用地规划
        elif stage_id == 'S6':
            if is_valid_project_candidate(0):
                return rel_path_parts[0], normalized_parts[0], 'S6用地规划统一审批代码项目目录', 'high'

        # S7: 工程规划
        elif stage_id == 'S7':
            if is_valid_project_candidate(0):
                return rel_path_parts[0], normalized_parts[0], 'S7工程规划统一审批代码项目目录', 'high'

        # 通用 fallback
        for i in range(len(normalized_parts)):
            if is_valid_project_candidate(i):
                return '/'.join(rel_path_parts[:i + 1]), normalized_parts[i], f'{stage_id}候选项目目录', 'medium'

        return None, 'UNRESOLVED', '未识别到项目大目录，需人工指认', 'low'

    def register_or_find(self, stage_id, rel_path, is_dir=True):
        """核心查询接口：优先查数据库已有大目录，未命中才研判并存库。"""
        parts = [p for p in re.split(r'[/\\!]', str(rel_path or '').strip('/\\')) if p]
        if not parts:
            return None
        dir_parts = parts if is_dir else parts[:-1]
        if not dir_parts:
            return None

        # 1. 优先查库
        existing = self.find_in_database(stage_id, dir_parts)
        if existing:
            return existing

        # 2. 数据库未命中时，调用研判逻辑
        root_rel, proj_name, reason, confidence = self.determine_project_root(stage_id, dir_parts)
        if not root_rel:
            return None

        norm_root_parts = [normalize_dir_name(p) for p in root_rel.split('/') if p]
        # 若 normalized 前缀已经在库中存在，直接复用避免同一物理项目因 .zip/.解压 产生重复 ID
        existing_by_norm = self.find_in_database(stage_id, norm_root_parts)
        if existing_by_norm and len([normalize_dir_name(p) for p in existing_by_norm['project_root'].split('/') if p]) == len(norm_root_parts):
            return existing_by_norm

        ident = catalog_identifier(f'{stage_id}:{root_rel}')
        if ident not in self.projects:
            stage_folder = STAGE_DICT[stage_id]['folder_name']
            phys_dir = self.resolve_physical_dir(stage_id, root_rel)
            phys_path = str(phys_dir) if phys_dir else str(self.root / stage_folder / root_rel.replace('/', os.sep))
            dec = self._decision_for_parts(stage_id, norm_root_parts)
            decision_source = dec['decision_source'] if dec else 'rule'
            evidence_paths = dec.get('evidence_paths', []) if dec else []
            struct_ver = self.compute_structure_version(phys_path)
            new_proj = {
                'id': ident,
                'stage_id': stage_id,
                'stage_name': stage_folder,
                'project_root': root_rel,
                'project_name': proj_name,
                'physical_path': phys_path,
                'depth': len(root_rel.split('/')),
                'reason': reason,
                'confidence': confidence,
                'status': 'confirmed' if confidence == 'high' else 'review',
                'files': 0,
                'updated_at': datetime.now().isoformat(timespec='seconds'),
                'node_type': 'project',
                'decision_source': decision_source,
                'evidence_paths': evidence_paths,
                'structure_version': struct_ver,
                'boundary_review': False,
                'boundary_review_reason': '',
            }
            self.projects[ident] = new_proj
            self.upsert_db(new_proj)
            self.db.commit()
            self.rebuild_prefixes()

        return self.projects[ident]

    def apply_agent_decisions(self, decisions):
        """校验并持久化 Agent 的目录语义判断结果。
        支持列表输入，每项包含：
        - stage_id: S1..S7
        - rel_path: 阶段内相对路径
        - node_type: 'project' | 'collection' | 'classification' | 'material' | 'unresolved'
        - project_name: 项目名称（node_type == 'project' 时使用）
        - parent_project_root: 当 node_type == 'material' 时所属项目路径（可选）
        - reason: 判断依据（必填，非空字符串）
        - evidence_paths: 证据文件或子目录路径列表（可选，若提供必须真实存在）
        - project_codes: 提取到的项目代码列表（可选）
        - confidence: 'high' | 'medium' | 'low'
        - needs_review: bool
        - children: 子目录决策列表（可选）
        """
        if not isinstance(decisions, list) or not decisions:
            raise ValueError('decisions 必须是非空列表')

        flat_decisions = []

        def flatten(items):
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError('每条决策必须是字典对象')
                flat_decisions.append(item)
                if item.get('children'):
                    flatten(item['children'])

        flatten(decisions)
        applied = []
        now = datetime.now().isoformat(timespec='seconds')

        for item in flat_decisions:
            stage_id = item.get('stage_id')
            if stage_id not in STAGE_DICT:
                raise ValueError(f'无效的 stage_id: {stage_id}')
            raw_rel = str(item.get('rel_path') or '').strip().strip('/\\')
            if not raw_rel or Path(raw_rel).is_absolute() or '..' in re.split(r'[/\\]', raw_rel):
                raise ValueError(f'目录路径必须为阶段内安全相对路径: {raw_rel}')
            node_type = item.get('node_type')
            if node_type not in VALID_NODE_TYPES:
                raise ValueError(f'无效的 node_type: {node_type}，必须属于 {sorted(VALID_NODE_TYPES)}')
            reason = str(item.get('reason') or '').strip()
            if not reason:
                raise ValueError(f'Agent 判断结果必须提供明确依据 (reason): {stage_id}:{raw_rel}')

            stage_root = (self.root / STAGE_DICT[stage_id]['folder_name']).resolve()
            phys_dir = self.resolve_physical_dir(stage_id, raw_rel)
            if phys_dir is None or not disk_path(phys_dir).exists():
                raise ValueError(f'Agent 判断的目录在磁盘上不存在，拒绝存库: {stage_id}:{raw_rel}')
            if not phys_dir.resolve().is_relative_to(stage_root):
                raise ValueError(f'目录超出阶段根目录范围: {stage_id}:{raw_rel}')

            evidence_paths = item.get('evidence_paths') or []
            if not isinstance(evidence_paths, list):
                raise ValueError('evidence_paths 必须为列表')
            verified_evidence = []
            for ev in evidence_paths:
                ev_str = str(ev or '').strip().strip('/\\')
                if not ev_str or '..' in re.split(r'[/\\]', ev_str):
                    raise ValueError(f'无效的证据路径: {ev}')
                ev_phys = self.resolve_physical_dir(stage_id, ev_str)
                if ev_phys is None or not disk_path(ev_phys).exists():
                    # 尝试作为相对于当前目录的子路径解析
                    ev_phys = self.resolve_physical_dir(stage_id, f'{raw_rel}/{ev_str}')
                if ev_phys is None or not disk_path(ev_phys).exists():
                    raise ValueError(f'Agent 提供的证据路径不存在，拒绝存库: {ev}')
                verified_evidence.append(ev_str)

            norm_parts = [normalize_dir_name(p) for p in re.split(r'[/\\!]', raw_rel) if p]
            norm_rel = '/'.join(norm_parts)
            dec_id = catalog_identifier(f'{stage_id}:{norm_rel}')
            proj_id = catalog_identifier(f'{stage_id}:{raw_rel}')
            norm_proj_id = catalog_identifier(f'{stage_id}:{norm_rel}')

            # 保护人工确认记录（status == 'manual'）
            for cid in (proj_id, norm_proj_id):
                if cid in self.projects and self.projects[cid].get('status') == 'manual':
                    applied.append({'id': cid, 'stage_id': stage_id, 'rel_path': raw_rel, 'skipped': 'manual_protected'})
                    break
            else:
                struct_ver = self.compute_structure_version(phys_dir)
                confidence = item.get('confidence') if item.get('confidence') in ('high', 'medium', 'low') else ('low' if node_type == 'unresolved' else 'high')
                needs_review = bool(item.get('needs_review')) or node_type == 'unresolved' or confidence != 'high'
                status = 'unresolved' if node_type == 'unresolved' else ('review' if needs_review else 'confirmed')
                project_name = str(item.get('project_name') or norm_parts[-1]).strip()

                dec_record = {
                    'id': dec_id,
                    'stage_id': stage_id,
                    'rel_path': norm_rel,
                    'physical_path': str(phys_dir),
                    'node_type': node_type,
                    'project_name': project_name if node_type == 'project' else '',
                    'parent_project_root': str(item.get('parent_project_root') or ''),
                    'reason': reason,
                    'evidence_paths': verified_evidence,
                    'project_codes': [canonical_code(c) for c in (item.get('project_codes') or []) if valid_project_code(str(c))],
                    'confidence': confidence,
                    'status': status,
                    'decision_source': 'agent',
                    'structure_version': struct_ver,
                    'updated_at': now,
                }
                self.decisions[dec_id] = dec_record
                self.upsert_decision_db(dec_record)

                if node_type == 'project':
                    # 若旧库中存在同一规范化路径但不同后缀名的非 manual 项目记录，合并更新它
                    target_id = norm_proj_id
                    for old_id, old_p in list(self.projects.items()):
                        if old_p['stage_id'] == stage_id and [normalize_dir_name(x) for x in old_p['project_root'].split('/') if x] == norm_parts:
                            if old_p.get('status') == 'manual':
                                target_id = None
                                break
                            if old_id != target_id:
                                before_json = json.dumps(old_p, ensure_ascii=False, sort_keys=True)
                                self.projects.pop(old_id, None)
                                self.db.execute('DELETE FROM projects WHERE id=?', (old_id,))
                                self.db.execute('INSERT INTO boundary_history VALUES (?,?,?,?)', (old_id, now, before_json, json.dumps({'evicted_by_agent': target_id}, ensure_ascii=False)))

                    if target_id:
                        before_json = json.dumps(self.projects.get(target_id), ensure_ascii=False, sort_keys=True) if target_id in self.projects else ''
                        proj_record = {
                            'id': target_id,
                            'stage_id': stage_id,
                            'stage_name': STAGE_DICT[stage_id]['folder_name'],
                            'project_root': norm_rel,
                            'project_name': project_name,
                            'physical_path': str(phys_dir),
                            'depth': len(norm_parts),
                            'reason': reason,
                            'confidence': confidence,
                            'status': 'review' if needs_review else 'confirmed',
                            'files': self.projects.get(target_id, {}).get('files', 0),
                            'updated_at': now,
                            'node_type': 'project',
                            'decision_source': 'agent',
                            'evidence_paths': verified_evidence,
                            'structure_version': struct_ver,
                            'boundary_review': bool(item.get('needs_review')),
                            'boundary_review_reason': reason if item.get('needs_review') else '',
                        }
                        self.projects[target_id] = proj_record
                        self.upsert_db(proj_record)
                        if before_json:
                            self.db.execute('INSERT INTO boundary_history VALUES (?,?,?,?)', (target_id, now, before_json, json.dumps(proj_record, ensure_ascii=False, sort_keys=True)))

                        # 如果该独立项目的父目录曾被错误存为非 manual 的项目，自动解除父目录锁定
                        for p_id, p_obj in list(self.projects.items()):
                            if p_obj['stage_id'] == stage_id and p_obj.get('status') != 'manual' and p_obj.get('decision_source') != 'agent':
                                p_parts = [normalize_dir_name(x) for x in p_obj['project_root'].split('/') if x]
                                if len(p_parts) < len(norm_parts) and norm_parts[:len(p_parts)] == p_parts:
                                    old_parent = json.dumps(p_obj, ensure_ascii=False, sort_keys=True)
                                    self.projects.pop(p_id, None)
                                    self.db.execute('DELETE FROM projects WHERE id=?', (p_id,))
                                    self.db.execute('INSERT INTO boundary_history VALUES (?,?,?,?)', (p_id, now, old_parent, json.dumps({'evicted_parent_of_subproject': target_id}, ensure_ascii=False)))
                else:
                    # node_type 是 collection / classification / material / unresolved：若旧库中错误地将其存为了非 manual 项目，予以清理
                    for old_id, old_p in list(self.projects.items()):
                        if old_p['stage_id'] == stage_id and old_p.get('status') != 'manual':
                            old_parts = [normalize_dir_name(x) for x in old_p['project_root'].split('/') if x]
                            if old_parts == norm_parts or (node_type == 'unresolved' and len(old_parts) >= len(norm_parts) and old_parts[:len(norm_parts)] == norm_parts and old_p.get('decision_source') != 'agent'):
                                before_json = json.dumps(old_p, ensure_ascii=False, sort_keys=True)
                                self.projects.pop(old_id, None)
                                self.db.execute('DELETE FROM projects WHERE id=?', (old_id,))
                                self.db.execute('INSERT INTO boundary_history VALUES (?,?,?,?)', (old_id, now, before_json, json.dumps({'reclassified_as': node_type, 'reason': reason}, ensure_ascii=False)))

                applied.append(dec_record)

        self._evict_invalid_cached_projects()
        self.rebuild_prefixes()
        self.save()
        return applied

    def flag_boundary_review(self, directory_id, reason, evidence=None):
        """由 M1/M2 发现的代码冲突、共用附件或多项目材料反向触发项目边界复核（不自动合并宗地）。"""
        proj = self.projects.get(directory_id)
        if not proj:
            return None
        proj['boundary_review'] = True
        existing_reasons = [r for r in (proj.get('boundary_review_reason') or '').split('；') if r]
        if reason and reason not in existing_reasons:
            existing_reasons.append(reason)
        proj['boundary_review_reason'] = '；'.join(existing_reasons)
        if proj.get('status') != 'manual':
            proj['status'] = 'review'
        proj['updated_at'] = datetime.now().isoformat(timespec='seconds')
        self.upsert_db(proj)
        self.db.commit()
        return proj

    def inspect_stage_tree(self, stage_id=None, rel_path='', max_depth=2, only_pending=False):
        """为 Agent 收集目录树、父子层级、文件统计及当前边界状态，供 Agent 理解目录结构。"""
        target_stages = [(stage_id, STAGE_DICT[stage_id]['folder_name'])] if stage_id else STAGES
        result = []

        for sid, sname in target_stages:
            stage_root = self.root / sname
            if not stage_root.is_dir():
                continue
            start_dir = self.resolve_physical_dir(sid, rel_path) if rel_path else stage_root
            if not start_dir or not start_dir.is_dir():
                continue

            def build_node(phys_dir, rel_parts, current_depth):
                norm_rel = '/'.join(rel_parts)
                dec = self._decision_for_parts(sid, rel_parts) if rel_parts else None
                proj = self.find_in_database(sid, rel_parts) if rel_parts else None
                child_dirs, child_files = self._inspect_child_dirs(phys_dir)

                ext_counts = Counter(f.suffix.lower() or 'no_ext' for f in child_files)
                sample_files = [f.name for f in sorted(child_files, key=lambda x: x.name)[:8]]
                dir_codes = _extract_codes_from_text(norm_rel + ' ' + ' '.join(sample_files))
                struct_ver = self.compute_structure_version(phys_dir) if rel_parts else ''

                is_unchanged = bool(
                    (dec and dec.get('structure_version') == struct_ver and dec.get('status') in ('confirmed', 'manual'))
                    or (proj and proj.get('status') == 'manual')
                )
                needs_attention = bool(
                    not is_unchanged and (
                        self._is_multi_project_container_on_disk(sid, rel_parts)
                        or (proj and proj.get('boundary_review'))
                        or (dec and dec.get('status') in ('review', 'unresolved'))
                        or (rel_parts and not proj and not is_pure_classification(rel_parts[-1]) and not is_material_leaf_dir(rel_parts[-1]))
                    )
                )

                node = {
                    'stage_id': sid,
                    'rel_path': norm_rel,
                    'name': rel_parts[-1] if rel_parts else sname,
                    'depth': len(rel_parts),
                    'direct_file_count': len(child_files),
                    'file_types': dict(ext_counts),
                    'sample_files': sample_files,
                    'child_dir_count': len(child_dirs),
                    'child_dir_names': [normalize_dir_name(d.name) for d in child_dirs[:25]],
                    'detected_codes': dir_codes,
                    'structure_version': struct_ver,
                    'db_decision': {
                        'node_type': dec['node_type'],
                        'status': dec['status'],
                        'decision_source': dec['decision_source'],
                        'reason': dec['reason'],
                    } if dec else None,
                    'db_project': {
                        'id': proj['id'],
                        'project_root': proj['project_root'],
                        'project_name': proj['project_name'],
                        'status': proj['status'],
                        'decision_source': proj.get('decision_source', 'rule'),
                        'boundary_review': proj.get('boundary_review', False),
                        'boundary_review_reason': proj.get('boundary_review_reason', ''),
                    } if proj else None,
                    'needs_agent_review': needs_attention,
                }
                if current_depth < max_depth and child_dirs:
                    children = []
                    for cd in child_dirs:
                        c_node = build_node(cd, rel_parts + [normalize_dir_name(cd.name)], current_depth + 1)
                        if not only_pending or c_node['needs_agent_review'] or c_node.get('children'):
                            children.append(c_node)
                    node['children'] = children
                return node

            init_parts = [normalize_dir_name(p) for p in re.split(r'[/\\!]', rel_path.strip('/\\')) if p] if rel_path else []
            result.append(build_node(start_dir, init_parts, 0))
        return result

    def inspect_directory_evidence(self, stage_id, rel_path, read_files=False, max_files=5):
        """按需深入查看某个具体目录的结构、代表性材料摘要、项目代码线索及已有数据库记录。"""
        if stage_id not in STAGE_DICT:
            raise ValueError(f'无效的 stage_id: {stage_id}')
        phys_dir = self.resolve_physical_dir(stage_id, rel_path)
        if not phys_dir or not disk_path(phys_dir).exists():
            raise FileNotFoundError(f'目录不存在: {stage_id}:{rel_path}')

        norm_parts = [normalize_dir_name(p) for p in re.split(r'[/\\!]', str(rel_path).strip('/\\')) if p]
        child_dirs, child_files = self._inspect_child_dirs(phys_dir)

        sub_summaries = []
        for cd in child_dirs[:30]:
            g_dirs, g_files = self._inspect_child_dirs(cd)
            sub_summaries.append({
                'name': normalize_dir_name(cd.name),
                'rel_path': '/'.join(norm_parts + [normalize_dir_name(cd.name)]),
                'child_dirs': [normalize_dir_name(x.name) for x in g_dirs[:15]],
                'file_count': len(g_files),
                'sample_files': [x.name for x in g_files[:6]],
                'codes_in_names': _extract_codes_from_text(cd.name + ' ' + ' '.join(x.name for x in g_files[:10])),
            })

        snippets = []
        discovered_codes = set(_extract_codes_from_text(rel_path))
        candidate_files = list(child_files)
        if len(candidate_files) < max_files:
            for cd in child_dirs[:5]:
                _, g_files = self._inspect_child_dirs(cd)
                candidate_files.extend(g_files[:2])

        readable_Open = [f for f in candidate_files if f.suffix.lower() in ('.txt', '.docx', '.pdf', '.csv', '.json', '.xml')][:max_files]
        for f in readable_Open:
            discovered_codes.update(_extract_codes_from_text(f.name))
            if read_files:
                snippet = _read_text_snippet(f, max_chars=400)
                if snippet:
                    discovered_codes.update(_extract_codes_from_text(snippet))
                    stage_root = self.root / STAGE_DICT[stage_id]['folder_name']
                    rel_f = f.relative_to(stage_root).as_posix() if f.is_relative_to(stage_root) else f.name
                    snippets.append({'file': rel_f, 'snippet': snippet})

        dec = self._decision_for_parts(stage_id, norm_parts)
        proj = self.find_in_database(stage_id, norm_parts)
        return {
            'stage_id': stage_id,
            'stage_name': STAGE_DICT[stage_id]['folder_name'],
            'rel_path': '/'.join(norm_parts),
            'physical_path': str(phys_dir),
            'parent_chain': ['/'.join(norm_parts[:i]) for i in range(1, len(norm_parts))],
            'structure_version': self.compute_structure_version(phys_dir),
            'direct_files': [f.name for f in child_files[:30]],
            'subdirectories': sub_summaries,
            'material_snippets': snippets,
            'discovered_project_codes': sorted(discovered_codes),
            'existing_decision': dec,
            'existing_project': proj,
        }

    def update_project_boundary(self, project_id, new_root_rel, new_name=None):
        """人工手动调整或修正项目大目录边界，锁定保存到数据库。"""
        if project_id not in self.projects:
            raise KeyError(f'项目大目录不存在: {project_id}')
        proj = self.projects[project_id]
        stage_id = proj['stage_id']
        stage_folder = STAGE_DICT[stage_id]['folder_name']
        stage_root = self.root / stage_folder
        if not isinstance(new_root_rel, str) or not new_root_rel.strip() or Path(new_root_rel).is_absolute() or '..' in re.split(r'[/\\]', new_root_rel):
            raise ValueError('项目边界必须为阶段内相对路径')
        if not (stage_root / new_root_rel).resolve().is_relative_to(stage_root.resolve()):
            raise ValueError('项目边界超出阶段目录')
        phys_dir = self.resolve_physical_dir(stage_id, new_root_rel)
        new_phys = str(phys_dir) if phys_dir and phys_dir.exists() else str(self.root / stage_folder / new_root_rel.replace('/', os.sep))

        before = json.dumps(proj, ensure_ascii=False, sort_keys=True)
        proj['project_root'] = new_root_rel
        if new_name:
            proj['project_name'] = new_name
        proj['physical_path'] = new_phys
        proj['depth'] = len(new_root_rel.split('/'))
        proj['status'] = 'manual'
        proj['decision_source'] = 'manual'
        proj['reason'] = '人工手动指定项目大目录边界'
        proj['boundary_review'] = False
        proj['boundary_review_reason'] = ''
        proj['updated_at'] = datetime.now().isoformat(timespec='seconds')

        norm_rel = '/'.join(normalize_dir_name(p) for p in re.split(r'[/\\!]', new_root_rel.strip('/\\')) if p)
        dec_id = catalog_identifier(f'{stage_id}:{norm_rel}')
        dec = {
            'id': dec_id,
            'stage_id': stage_id,
            'rel_path': norm_rel,
            'physical_path': new_phys,
            'node_type': 'project',
            'project_name': proj['project_name'],
            'parent_project_root': '',
            'reason': proj['reason'],
            'evidence_paths': proj.get('evidence_paths', []),
            'project_codes': [],
            'confidence': 'high',
            'status': 'manual',
            'decision_source': 'manual',
            'structure_version': self.compute_structure_version(new_phys),
            'updated_at': proj['updated_at'],
        }
        self.decisions[dec_id] = dec
        self.upsert_decision_db(dec)

        self.upsert_db(proj)
        self.db.execute('INSERT INTO boundary_history VALUES (?,?,?,?)',
                        (project_id, proj['updated_at'], before, json.dumps(proj, ensure_ascii=False, sort_keys=True)))
        self.save()
        self.rebuild_prefixes()
        return proj

    def close(self):
        try:
            if hasattr(self, 'db') and self.db:
                self.db.close()
                self.db = None
        except Exception:
            pass

    def __del__(self):
        self.close()


def main():
    parser = argparse.ArgumentParser(description='OneMap 七阶段项目大目录证据收集与 Agent 决策存库工具')
    parser.add_argument('--source', type=Path, default=SOURCE, help='七阶段数据源根目录')
    parser.add_argument('--storage-dir', type=Path, default=None, help='目录库存储目录')
    sub = parser.add_subparsers(dest='command', required=True)

    p_tree = sub.add_parser('inspect-tree', help='查看阶段目录树结构与待研判节点')
    p_tree.add_argument('--stage', choices=[s for s, _ in STAGES], default=None)
    p_tree.add_argument('--path', default='')
    p_tree.add_argument('--max-depth', type=int, default=2)
    p_tree.add_argument('--only-pending', action='store_true')

    p_node = sub.add_parser('inspect-node', help='深入查看指定目录的层级、文件名、代码线索与代表性材料摘要')
    p_node.add_argument('--stage', choices=[s for s, _ in STAGES], required=True)
    p_node.add_argument('--path', required=True)
    p_node.add_argument('--read-files', action='store_true')
    p_node.add_argument('--max-files', type=int, default=5)

    p_apply = sub.add_parser('apply-decisions', help='校验并持久化 Agent 的项目边界判断 JSON')
    p_apply.add_argument('--file', type=Path, required=True, help='Agent 判断结果 JSON 文件路径')

    p_prune = sub.add_parser('prune-stale', help='清理磁盘上已不存在的非人工确认历史遗留项目记录')

    p_summary = sub.add_parser('summary', help='输出当前已存库项目与待复核边界统计')

    args = parser.parse_args()
    catalog = ProjectCatalog(storage_dir=args.storage_dir, root=args.source)
    try:
        if args.command == 'inspect-tree':
            data = catalog.inspect_stage_tree(stage_id=args.stage, rel_path=args.path, max_depth=args.max_depth, only_pending=args.only_pending)
            print(json.dumps(data, ensure_ascii=False, indent=2))
        elif args.command == 'inspect-node':
            data = catalog.inspect_directory_evidence(stage_id=args.stage, rel_path=args.path, read_files=args.read_files, max_files=args.max_files)
            print(json.dumps(data, ensure_ascii=False, indent=2))
        elif args.command == 'apply-decisions':
            payload = json.loads(args.file.read_text(encoding='utf-8'))
            items = payload.get('decisions', payload) if isinstance(payload, dict) else payload
            applied = catalog.apply_agent_decisions(items)
            print(json.dumps({'applied_count': len(applied), 'total_projects': len(catalog.projects), 'applied': applied}, ensure_ascii=False, indent=2))
        elif args.command == 'prune-stale':
            removed = catalog.prune_missing_disk_projects()
            print(json.dumps({'removed_count': len(removed), 'remaining_projects': len(catalog.projects), 'removed': removed[:50]}, ensure_ascii=False, indent=2))
        elif args.command == 'summary':
            by_stage = Counter(p['stage_id'] for p in catalog.projects.values())
            by_source = Counter(p.get('decision_source', 'rule') for p in catalog.projects.values())
            reviews = [p for p in catalog.projects.values() if p.get('status') == 'review' or p.get('boundary_review')]
            unresolved = [d for d in catalog.decisions.values() if d.get('node_type') == 'unresolved']
            print(json.dumps({
                'total_projects': len(catalog.projects),
                'by_stage': dict(by_stage),
                'by_decision_source': dict(by_source),
                'review_projects_count': len(reviews),
                'unresolved_directories_count': len(unresolved),
                'review_projects': [{'stage_id': p['stage_id'], 'project_root': p['project_root'], 'project_name': p['project_name'], 'reason': p.get('boundary_review_reason') or p['reason']} for p in reviews[:50]],
                'unresolved_directories': [{'stage_id': d['stage_id'], 'rel_path': d['rel_path'], 'reason': d['reason']} for d in unresolved[:50]],
            }, ensure_ascii=False, indent=2))
    finally:
        catalog.close()


if __name__ == '__main__':
    main()
