"""七阶段项目大目录判定引擎与持久化仓储（带数据库优先查验缓存）。

核心机制：
1. 优先查库：每次检查材料时，优先查询已有的项目大目录数据库与索引；
   若当前文件所属路径已有大目录研判记录，直接复用数据库结果，不再重复分析；
2. 增量存库：仅对数据库中未记录的新路径执行大模型/业务研判，研判后即时持久化存库；
3. 人工保护：人工已核准或调整的边界记录锁定保护，不被自动研判覆盖。
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from source_config import SOURCE, STAGES, STAGE_DICT, STAGE_FOLDER_TO_ID, EXCLUDED_DIRECTORIES

RULE_VERSION = 'project-catalog-7stages-v5-all-stage-containers'

# 常见纯业务分类/归档层名称（非项目）
CLASSIFICATION_DIR_NAMES = {
    '出让', '划拨', '01划拨档案整理', '04出让档案整理',
    '土地首次登记附件', '规划条件盖章版',
    '用地预审及选址论证',
    '数据图层', '收储材料', '计划供应',
}

# 常见材料分类小目录（属于项目内部的材料子目录，绝不能作为项目大目录）
MATERIAL_SUBDIR_NAMES = {
    '附件', '扫描件', '报件', '报批材料', '材料', '矢量', '红线', '图件',
    '06 测量', '测量', '初审意见', '成果表',
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


def is_pure_classification(name):
    cleaned = re.sub(r'[(（]\d+[)）]$', '', name).strip()
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
    return False


def is_material_leaf_dir(name):
    cleaned = re.sub(r'[(（]\d+[)）]$', '', name).strip()
    if cleaned.casefold() in {'pdf', 'cad', 'shp', 'word', 'excel', 'jpg', 'jpeg', 'png', 'tif', 'tiff', 'dwg', 'dxf'}:
        return True
    if cleaned in MATERIAL_SUBDIR_NAMES:
        return True
    if any(k in cleaned for k in ('申请表', '情况说明', '设计方案', '红线图', '四址范围', '证明文件', '宗地面积测量')):
        return True
    if re.fullmatch(r'\d{1,2}\s*测量.*', cleaned):
        return True
    return False


def normalize_dir_name(name):
    name = re.sub(r'\.解压$', '', name)
    return re.sub(r'\.(?:zip|rar|7z)$', '', name, flags=re.I)


class ProjectCatalog:
    """管理七个阶段的项目大目录持久化存储、数据库优先查验与归属检索。"""

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
        self.projects = {}  # {id: project_dict}
        self.stage_prefixes = {}  # {stage_id: [(prefix_parts, proj_dict), ...]} 按路径深度降序排列，用于极速前缀匹配
        self.load()


    def init_db(self):
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
                updated_at TEXT
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_stage_root ON projects (stage_id, project_root)")
        self.db.commit()

    def load(self):
        # 优先从 SQLite 数据库加载已分析存库的项目大目录
        cursor = self.db.execute("SELECT id, stage_id, stage_name, project_root, project_name, physical_path, depth, reason, confidence, status, files, updated_at FROM projects")
        for row in cursor.fetchall():
            proj = dict(
                id=row[0], stage_id=row[1], stage_name=row[2], project_root=row[3],
                project_name=row[4], physical_path=row[5], depth=row[6],
                reason=row[7], confidence=row[8], status=row[9], files=row[10], updated_at=row[11]
            )
            self.projects[proj['id']] = proj

        # 若数据库为空但 JSON 缓存文件存在，从 JSON 恢复导入到数据库
        if not self.projects and self.storage_file.is_file():
            try:
                data = json.loads(self.storage_file.read_text(encoding='utf-8'))
                for p in data.get('projects', {}).values():
                    self.upsert_db(p)
                    self.projects[p['id']] = p
                self.db.commit()
            except Exception:
                pass

        # 旧版分类节点保留在 SQLite 中供追溯，但不再进入可用项目和前缀缓存。
        self.projects = {key: p for key, p in self.projects.items() if p['status'] == 'manual' or not (is_pure_classification(normalize_dir_name(p['project_root'].split('/')[-1])) or is_material_leaf_dir(normalize_dir_name(p['project_root'].split('/')[-1])))}
        self.rebuild_prefixes()

    def rebuild_prefixes(self):
        """为已存库的项目构建按阶段的前缀索引（深度大的在前，优先匹配最精准的子项目）。"""
        self.stage_prefixes = {sid: [] for sid, _ in STAGES}
        for proj in self.projects.values():
            if proj['status'] != 'manual' and (is_pure_classification(normalize_dir_name(proj['project_root'].split('/')[-1])) or is_material_leaf_dir(normalize_dir_name(proj['project_root'].split('/')[-1]))):
                continue
            sid = proj['stage_id']
            if sid not in self.stage_prefixes:
                self.stage_prefixes[sid] = []
            parts = [normalize_dir_name(p) for p in proj['project_root'].split('/') if p]
            self.stage_prefixes[sid].append((parts, proj))

        for sid in self.stage_prefixes:
            # 按深度从大到小排序，确保先命中更精确的大项目目录
            self.stage_prefixes[sid].sort(key=lambda item: len(item[0]), reverse=True)

    def upsert_db(self, proj):
        self.db.execute("""
            INSERT INTO projects (id, stage_id, stage_name, project_root, project_name, physical_path, depth, reason, confidence, status, files, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                project_root=excluded.project_root,
                project_name=excluded.project_name,
                physical_path=excluded.physical_path,
                depth=excluded.depth,
                reason=excluded.reason,
                confidence=excluded.confidence,
                status=excluded.status,
                updated_at=excluded.updated_at
        """, (
            proj['id'], proj['stage_id'], proj['stage_name'], proj['project_root'],
            proj['project_name'], proj['physical_path'], proj['depth'],
            proj['reason'], proj['confidence'], proj['status'], proj.get('files', 0), proj['updated_at']
        ))

    def save(self):
        """将当前仓储同步写入 SQLite 与 JSON 镜像。"""
        self.db.commit()
        payload = {
            'rule_version': RULE_VERSION,
            'updated_at': datetime.now().isoformat(timespec='seconds'),
            'count': len(self.projects),
            'projects': self.projects,
        }
        temp_file = self.storage_file.with_suffix('.tmp')
        temp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        temp_file.replace(self.storage_file)

    def find_in_database(self, stage_id, rel_path_parts):
        """【第一优先级】：直接查询数据库，检查是否已在已分析存库的大项目目录内。
        若命中，直接返回已有记录，0毫秒开销，绝不重复分析！
        """
        if stage_id not in self.stage_prefixes or not rel_path_parts:
            return None

        normalized_target = [normalize_dir_name(p) for p in rel_path_parts]
        for prefix_parts, proj in self.stage_prefixes[stage_id]:
            # 检查 normalized_target 是否以 prefix_parts 开头
            if len(normalized_target) >= len(prefix_parts):
                if normalized_target[:len(prefix_parts)] == prefix_parts:
                    return proj
        return None

    def determine_project_root(self, stage_id, rel_path_parts):
        """【第二优先级】：数据库未命中时，才执行大模型/业务研判规则提取项目大目录。"""
        if not rel_path_parts:
            return None, 'UNRESOLVED', '空路径', 'low'

        normalized_parts = [normalize_dir_name(p) for p in rel_path_parts]

        # S1: 用地预审与选址
        if stage_id == 'S1':
            for i, p in enumerate(normalized_parts):
                if p == '用地预审及选址论证' and i + 1 < len(normalized_parts):
                    for index in range(i + 1, len(normalized_parts)):
                        proj_name = normalized_parts[index]
                        if not is_pure_classification(proj_name) and not is_material_leaf_dir(proj_name):
                            return '/'.join(rel_path_parts[:index + 1]), proj_name, 'S1解压包内独立选址工程项目', 'high'
            proj_name = normalized_parts[0]
            if not is_pure_classification(proj_name) and not is_material_leaf_dir(proj_name):
                return rel_path_parts[0], proj_name, 'S1用地预审独立业务项目目录', 'high'

        # S2: 条件出具
        elif stage_id == 'S2':
            first = normalized_parts[0]
            if first == '规划条件盖章版':
                for i, part in enumerate(normalized_parts[1:], 1):
                    if not is_pure_classification(part) and not is_material_leaf_dir(part):
                        return '/'.join(rel_path_parts[:i + 1]), part, 'S2规划条件出具项目', 'high'
            elif not is_pure_classification(first) and not is_material_leaf_dir(first):
                return rel_path_parts[0], first, 'S2条件出具业务项目目录', 'high'

        # S3: 批次报批阶段
        elif stage_id == 'S3':
            candidates = []
            for i, p in enumerate(normalized_parts):
                if p and not is_pure_classification(p) and not is_material_leaf_dir(p):
                    candidates.append((i, p))
            if candidates:
                idx, proj_name = candidates[-1]
                return '/'.join(rel_path_parts[:idx + 1]), proj_name, 'S3用地报批批次/项目大目录', 'high'

        # S4: 土地供应阶段
        elif stage_id == 'S4':
            candidates = []
            for i, p in enumerate(normalized_parts):
                if p and not is_pure_classification(p) and not is_material_leaf_dir(p):
                    candidates.append((i, p))
            if candidates:
                idx, proj_name = candidates[0]
                return '/'.join(rel_path_parts[:idx + 1]), proj_name, 'S4土地供应宗地/项目大目录', 'high'

        # S5: 土地首次登记
        elif stage_id == 'S5':
            for i, p in enumerate(normalized_parts):
                if not is_pure_classification(p) and not is_material_leaf_dir(p):
                    return '/'.join(rel_path_parts[:i + 1]), p, 'S5土地首次登记地籍宗地单元', 'high'

        # S6: 用地规划
        elif stage_id == 'S6':
            first = normalized_parts[0]
            if first and not is_pure_classification(first) and not is_material_leaf_dir(first):
                return rel_path_parts[0], first, 'S6用地规划统一审批代码项目目录', 'high'

        # S7: 工程规划
        elif stage_id == 'S7':
            first = normalized_parts[0]
            if first and not is_pure_classification(first) and not is_material_leaf_dir(first):
                return rel_path_parts[0], first, 'S7工程规划统一审批代码项目目录', 'high'

        # 通用 fallback
        for i, p in enumerate(normalized_parts):
            if p and not is_pure_classification(p) and not is_material_leaf_dir(p):
                return '/'.join(rel_path_parts[:i + 1]), p, f'{stage_id}候选项目目录', 'medium'

        return None, 'UNRESOLVED', '未识别到项目大目录，需人工指认', 'low'

    def register_or_find(self, stage_id, rel_path, is_dir=True):
        """核心查询接口：优先查数据库已有大目录，未命中才研判并存库。"""
        parts = [p for p in re.split(r'[/\\!]', rel_path.strip('/\\')) if p]
        if not parts:
            return None
        dir_parts = parts if is_dir else parts[:-1]
        if not dir_parts:
            # 文件直接散落在阶段根目录下，无项目大目录
            return None

        # 1. 优先查库：如果已有已存库的大目录覆盖此路径，直接复用返回！
        existing = self.find_in_database(stage_id, dir_parts)
        if existing:
            return existing

        # 2. 数据库未命中时，才调用研判逻辑进行识别
        root_rel, proj_name, reason, confidence = self.determine_project_root(stage_id, dir_parts)
        if not root_rel:
            return None

        ident = catalog_identifier(f'{stage_id}:{root_rel}')
        if ident not in self.projects:
            stage_folder = STAGE_DICT[stage_id]['folder_name']
            phys_path = str(self.root / stage_folder / root_rel.replace('/', os.sep))
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
            }
            self.projects[ident] = new_proj
            self.upsert_db(new_proj)
            self.db.commit()
            self.rebuild_prefixes()

        return self.projects[ident]

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
        new_phys = str(self.root / stage_folder / new_root_rel.replace('/', os.sep))

        proj['project_root'] = new_root_rel
        if new_name:
            proj['project_name'] = new_name
        proj['physical_path'] = new_phys
        proj['depth'] = len(new_root_rel.split('/'))
        proj['status'] = 'manual'
        proj['reason'] = '人工手动指定项目大目录边界'
        proj['updated_at'] = datetime.now().isoformat(timespec='seconds')

        self.upsert_db(proj)
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


