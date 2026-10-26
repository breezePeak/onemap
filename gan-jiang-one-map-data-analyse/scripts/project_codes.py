"""M1 目录代码证据：只读材料、版本缓存、按完整代码索引；不判定宗地相同。"""
from collections import defaultdict
import hashlib
from itertools import combinations, product
import json
from pathlib import Path
import re
import unicodedata
import zipfile
from xml.etree import ElementTree

from extract_archives import disk_path, file_version
from material_evidence import PATTERNS, canonical_code
from project_catalog import normalize_dir_name

RULE_VERSION = 'M1-project-code-v2'
PRIORITY = {'body_field': 100, 'structured_field': 100, 'filename': 80, 'project_directory': 70, 'subdirectory': 60}


def extract_codes(text, labelled=False):
    # NFKC 的偏移映射保留原始全角字形、空格和真实出处。
    normalized = ''; offsets = []
    for index, char in enumerate(text):
        value = unicodedata.normalize('NFKC', char).upper()
        normalized += value
        offsets.extend([index] * len(value))
    result = []
    for match in re.finditer(PATTERNS['project_code'], normalized):
        prefix = normalized[max(0, match.start() - 16):match.start()]
        if re.search(r'(?:电子监管号|监管号|合同编号|不动产单元号)\s*[:：=]?\s*$', prefix):
            continue
        if labelled and not re.search(r'项目代码\s*[:：=]?\s*["\']?\s*$', prefix):
            continue
        raw = text[offsets[match.start()]:offsets[match.end() - 1] + 1]
        code = canonical_code(raw)
        valid = code.isascii() and 1900 <= int(code[:4]) <= 2099 and code.split('-')[1] != '000000'
        result.append(dict(raw_code=raw, normalized_code=code, valid=valid,
                           location=f'字符 {offsets[match.start()]}', text_evidence=text[max(0, offsets[match.start()] - 16):offsets[match.end() - 1] + 1]))
    return result


def read_code_material(path, native_cache=None):
    suffix = path.suffix.lower()
    if suffix in ('.txt', '.md', '.csv', '.xml', '.json'):
        if disk_path(path).stat().st_size > 20 * 1024 * 1024:
            return [], 'text_size_limit'
        raw = disk_path(path).read_bytes()
        try: text = raw.decode('utf-8-sig')
        except UnicodeDecodeError: text = raw.decode('gb18030')
        if suffix == '.json':
            values = []
            def visit(value, location='$'):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in ('project_code', '项目代码') and isinstance(item, str):
                            values.append((location + '.' + key, item, 'structured_field'))
                        elif isinstance(item, (dict, list)):
                            visit(item, location + '.' + key)
                elif isinstance(value, list):
                    for index, item in enumerate(value): visit(item, f'{location}[{index}]')
            visit(json.loads(text))
            return values or [('正文', text, 'body_field')], None
        return [('正文', text, 'body_field')], None
    if suffix == '.pdf':
        saved = (native_cache or {}).get(str(path))
        stat = disk_path(path).stat()
        if saved and saved.get('size') == stat.st_size and saved.get('mtime_ns') == stat.st_mtime_ns and saved.get('ctime_ns') == stat.st_ctime_ns and not saved.get('error'):
            return [(f'page={number}', text, 'body_field') for number, text in enumerate(saved['pages'], 1)], None
        from extract_pdfs import read_pdf_text
        return [(f'page={number}', text, 'body_field') for number, text in enumerate(read_pdf_text(disk_path(path)), 1)], None
    if suffix == '.docx':
        with zipfile.ZipFile(disk_path(path)) as archive:
            info = archive.getinfo('word/document.xml')
            if info.file_size > 20 * 1024 * 1024: return [], 'text_size_limit'
            tree = ElementTree.fromstring(archive.read(info))
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        values = [(f'段落 {i}', ''.join(p.itertext()), 'body_field') for i, p in enumerate(tree.findall('.//w:p', ns), 1)]
        for table_number, table in enumerate(tree.findall('.//w:tbl', ns), 1):
            for row_number, row in enumerate(table.findall('w:tr', ns), 1):
                cells = [''.join(cell.itertext()).strip() for cell in row.findall('w:tc', ns)]
                for index, text in enumerate(cells[:-1]):
                    if text.rstrip(':：').strip() in ('项目代码','project_code'):
                        values.append((f'表{table_number}行{row_number}列{index + 2}', cells[index + 1], 'structured_field'))
        return values, None
    if suffix in ('.xlsx', '.xlsm'):
        import openpyxl
        book = openpyxl.load_workbook(disk_path(path), read_only=True, data_only=True)
        values = []
        try:
            for sheet in book:
                code_columns = set()
                for row in sheet:
                    for index, cell in enumerate(row):
                        if cell.value in ('项目代码', 'project_code') and index + 1 < len(row):
                            code_columns.add(index)
                            target = row[index + 1]
                            if target.value is not None and re.match(r'[0-9０-９]',str(target.value)):
                                values.append((f'{sheet.title}!{target.coordinate}', str(target.value), 'structured_field'))
                        elif cell.value in ('项目代码', 'project_code'):
                            code_columns.add(index)
                        elif index in code_columns and cell.value is not None and re.match(r'[0-9０-９]',str(cell.value)):
                            values.append((f'{sheet.title}!{cell.coordinate}', str(cell.value), 'structured_field'))
                        elif isinstance(cell.value, str) and '项目代码' in cell.value:
                            values.append((f'{sheet.title}!{cell.coordinate}', cell.value, 'body_field'))
        finally: book.close()
        return values, None
    return [], 'unsupported_body_format'


def build_code_index(folders, materials, catalog, report):
    db = catalog.db
    db.execute('CREATE TABLE IF NOT EXISTS project_code_cache (source TEXT PRIMARY KEY, version TEXT, parser TEXT, payload TEXT)')
    cached = {row[0]: row[1:] for row in db.execute('SELECT source,version,parser,payload FROM project_code_cache')}
    rows = []; seen = set(); issues = []; skipped = defaultdict(int); active = set()
    native_path = Path(__file__).resolve().parent.parent / 'work/pdf_text_cache.json'
    try: native_cache = json.loads(native_path.read_text(encoding='utf-8')) if native_path.is_file() else {}
    except (OSError, ValueError): native_cache = {}

    def add(folder, text, source_type, source, location='', labelled=False, hits=None, source_version=None):
        for hit in extract_codes(text, labelled) if hits is None else hits:
            key = (folder['id'], source, location, hit['raw_code'], source_type, hit['location'])
            if key in seen: continue
            seen.add(key)
            rows.append(dict(hit, directory_id=folder['id'], stage=folder['stage'], project_name=folder['name'],
                             code_type='project_code', source_type=source_type, source_path=source,
                             location=location or hit['location'], priority=PRIORITY[source_type],
                             confidence='high' if hit['valid'] else 'review', source_version=source_version, parser_version=RULE_VERSION))

    by_id = {f['id']: f for f in folders if f['kind'] != 'ungrouped'}
    for folder in by_id.values():
        add(folder, normalize_dir_name(Path(folder['folder']).name), 'project_directory', folder['folder'])
    for index, material in enumerate(materials, 1):
        folder = by_id.get(material['business_directory_id'])
        if folder is None:
            skipped['ungrouped_material'] += 1
            continue
        source = material['logical_path']; path = Path(material['physical_path'])
        if not path.resolve().is_relative_to(catalog.root):
            raise ValueError('M1 材料路径超出数据根目录')
        add(folder, path.name, 'filename', source, source_version=material['version'])
        parts = re.split(r'[/\\!]', source)
        # 从项目根之后逐级取证，分类父目录的代码不应污染子项目。
        root_name = normalize_dir_name(Path(folder['folder']).name)
        start = next((i for i, part in enumerate(parts) if canonical_code(normalize_dir_name(part)) == canonical_code(root_name)), None)
        if start is not None:
            for i in range(start + 1, len(parts) - 1):
                add(folder, parts[i], 'subdirectory', '/'.join(parts[:i + 1]), source_version=material['version'])
        active.add(source)
        # shortcut: 元数据版本不能识别刻意保留大小及全部时间戳的改写，此类数据需清缓存或升级内容签名。
        version = json.dumps(material['version'])
        previous = cached.get(source)
        if previous and previous[:2] == (version, RULE_VERSION):
            payload = json.loads(previous[2])
        else:
            try:
                texts, reason = read_code_material(path, native_cache)
                matches = []; malformed = []
                for location, text, source_type in texts:
                    hits = extract_codes(text, labelled=source_type!='structured_field')
                    if hits: matches.append((location, source_type, hits))
                    elif '项目代码' in text or source_type=='structured_field': malformed.append(location)
                payload = dict(matches=matches, skipped=reason, malformed=malformed)
            except Exception as error:
                payload = dict(matches=[], error=str(error))
            # 失败不缓存，依赖或权限恢复后可重试。
            if 'error' not in payload:
                db.execute('INSERT OR REPLACE INTO project_code_cache VALUES (?,?,?,?)', (source, version, RULE_VERSION, json.dumps(payload, ensure_ascii=False)))
        if file_version(path) != material['version']:
            raise OSError('M1 读取时文件版本发生变化，请重试')
        if payload.get('error'): issues.append(dict(path=source, directory_id=folder['id'], error=payload['error'], category='m1_read_error', blocking=False))
        if payload.get('skipped'): skipped[payload['skipped']] += 1
        if payload.get('malformed'): issues.append(dict(path=source, directory_id=folder['id'], error='项目代码字段无法可靠识别', locations=payload['malformed'], category='m1_unrecognized_code', blocking=False))
        for location, source_type, hits in payload['matches']:
            add(folder, '', source_type, source, location, hits=hits, source_version=material['version'])
        if index % 100 == 0: report('M1 项目代码索引', done=index, total=len(materials), stage=material['stage'])
    for source in set(cached) - active:
        db.execute('DELETE FROM project_code_cache WHERE source=?', (source,))

    codes = defaultdict(set)
    for row in rows: codes[row['directory_id']].add(row['normalized_code'])
    for row in rows: row['conflict'] = len(codes[row['directory_id']]) > 1 or not row['valid']
    review_ids = {row['directory_id'] for row in rows if row['conflict']} | {i['directory_id'] for i in issues}
    for folder in by_id.values(): folder['code_review'] = folder['id'] in review_ids
    index = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row['valid']: index[row['normalized_code']][row['directory_id']].append(row)
    # 同一代码出现在同一阶段多个目录时，一对多关系全部保留并标记歧义。
    links = {}
    for code, owners in index.items():
        stages = defaultdict(list)
        for ident in owners: stages[by_id[ident]['stage']].append(ident)
        for left_stage, right_stage in combinations(sorted(stages), 2):
            for left, right in product(sorted(stages[left_stage]), sorted(stages[right_stage])):
                key = (left, right)
                link = links.setdefault(key, dict(left=by_id[left], right=by_id[right], evidence=[], ambiguous=False, conflict=False))
                link['ambiguous'] |= any(len(values) > 1 for values in stages.values())
                conflict = any(ident in review_ids for ident in key)
                link['conflict'] |= conflict
                link['evidence'].append(dict(method='M1', normalized_code=code, left_sources=owners[left], right_sources=owners[right],
                                             explanation='完整标准化项目代码一致；目录及宗地归属需人工确认', conflict=conflict, rule_version=RULE_VERSION))
    db.execute('CREATE TABLE IF NOT EXISTS project_codes (id TEXT PRIMARY KEY, directory_id TEXT, normalized_code TEXT, payload TEXT)')
    db.execute('DELETE FROM project_codes')
    for row in rows:
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
        db.execute('INSERT INTO project_codes VALUES (?,?,?,?)', (hashlib.sha256(payload.encode()).hexdigest(), row['directory_id'], row['normalized_code'], payload))
    db.commit()
    stats = dict(projects_with_codes=len(indexed := {ident for owners in index.values() for ident in owners}), distinct_codes=len(index),
                 cross_stage_candidates=len(links), ambiguous=sum(l['ambiguous'] for l in links.values()),
                 conflicts=sum(len(values) > 1 for values in codes.values()), invalid_evidence=sum(not r['valid'] for r in rows),
                 unrecognized_projects=len(by_id) - len(indexed), unlinked_projects=len(by_id) - len({ident for pair in links for ident in pair}),
                 read_errors=sum(i['category']=='m1_read_error' for i in issues), unrecognized_fields=sum(i['category']=='m1_unrecognized_code' for i in issues), skipped_body=dict(skipped))
    return rows, list(links.values()), stats, issues
