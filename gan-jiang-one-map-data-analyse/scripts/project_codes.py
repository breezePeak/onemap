"""M1 目录代码证据：只读材料、版本缓存、按完整代码索引；不判定宗地相同。"""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import csv
import io
from itertools import combinations, product
import json
from pathlib import Path
import re
import unicodedata
import zipfile
from xml.etree import ElementTree

from extract_archives import disk_path, file_version
from material_evidence import PATTERNS, canonical_code, valid_project_code
from project_catalog import normalize_dir_name
from code_recognition import Recognition, IMAGES

RULE_VERSION = 'M1-project-code-v3-ownership'
PRIORITY = {'ocr': 90, 'body_field': 100, 'structured_field': 100, 'filename': 80, 'project_directory': 70, 'subdirectory': 60}
LABEL = r'(?:建设项目代码|项目代码|项目编号|PROJECT_CODE)'
NAME = r'(?:建设项目名称|项目名称)\s*[:：=]?\s*([^\n\t，,。；;:：]{1,100}?)(?=\s*(?:项目代码|项目编号|建设单位|建设地点)|[\n\t，,。；;:：]|$)'


def record_location(location):
    location = re.sub(r'列\d+$', '', location)
    location = re.sub(r'![A-Z]+(\d+)$', r'!row=\1', location)
    location = location.split(',column=')[0]
    return location.rsplit('.',1)[0] if location.startswith('$') else location


def extract_codes(text, labelled=False):
    # NFKC 的偏移映射保留原始全角字形、空格和真实出处。
    normalized = ''; offsets = []
    for index, char in enumerate(text):
        value = unicodedata.normalize('NFKC', char).upper()
        normalized += value
        offsets.extend([index] * len(value))
    result = []
    for match in re.finditer(PATTERNS['project_code'], normalized):
        prefix = normalized[max(0, match.start() - 80):match.start()]
        if re.search(r'(?:电子监管号|监管号|合同编号|不动产单元号|证书编号|证书文号|业务编号)\s*[:：=]?\s*$', prefix):
            continue
        field = bool(re.search(LABEL + r'\s*[:：=]?\s*["\']?\s*$', prefix))
        if labelled and not field:
            continue
        raw = text[offsets[match.start()]:offsets[match.end() - 1] + 1]
        code = canonical_code(raw)
        valid = valid_project_code(raw)
        result.append(dict(raw_code=raw, normalized_code=code, valid=valid, grade='A' if field else 'C',
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
        if suffix == '.csv':
            records = []
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                number = reader.line_num
                name = row.get('项目名称') or row.get('建设项目名称') or row.get('project_name')
                for key, value in row.items():
                    if value and isinstance(value,str):
                        structured = key in ('项目代码','项目编号','建设项目代码','project_code')
                        records.append((f'row={number},column={key}', value+('\n项目名称：'+name if name else ''), 'structured_field' if structured else 'body_field'))
            return records, None
        if suffix == '.json':
            values = []
            def visit(value, location='$'):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in ('project_code', '项目代码', '项目编号', '建设项目代码') and isinstance(item, str):
                            context = '\n'.join(str(value.get(k) or '') for k in ('项目名称','建设项目名称','project_name'))
                            values.append((location + '.' + key, item + ('\n项目名称：'+context.strip() if context.strip() else ''), 'structured_field'))
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
        values = [(f'段落 {i}', ''.join(p.itertext()), 'body_field') for i, p in enumerate(tree.findall('./w:body/w:p', ns), 1)]
        # 相邻行标签和值保留为同一局部片段；原文位置仍指向段落。
        values = [(location, text + ('\n'+values[i+1][1] if re.search(LABEL+r'\s*[:：]?\s*$', text) and i+1<len(values) else ''), kind) for i,(location,text,kind) in enumerate(values)]
        for table_number, table in enumerate(tree.findall('.//w:tbl', ns), 1):
            code_columns = set(); name_columns = set()
            for row_number, row in enumerate(table.findall('w:tr', ns), 1):
                cells = [''.join(cell.itertext()).strip() for cell in row.findall('w:tc', ns)]
                code_columns.update(i for i,c in enumerate(cells) if re.fullmatch(LABEL,c,re.I))
                name_columns.update(i for i,c in enumerate(cells) if c in ('项目名称','建设项目名称','project_name'))
                for i in code_columns:
                    if i<len(cells) and extract_codes(cells[i]):
                        name = next((cells[n] for n in name_columns if n<len(cells) and cells[n] not in ('项目名称','建设项目名称','project_name')), '')
                        records = cells[i]+('\n项目名称：'+name if name else '')
                        values.append((f'表{table_number}行{row_number}列{i+1}',records,'structured_field'))
                for index, text in enumerate(cells[:-1]):
                    if re.fullmatch(LABEL, text.rstrip(':：').strip(), re.I):
                        target = next((i for i in range(index+1,len(cells)) if cells[i]), index+1)
                        names = re.findall(NAME,'\n'.join(cells))
                        context = '\n'.join('项目名称：'+name for name in names)
                        values.append((f'表{table_number}行{row_number}列{target + 1}', cells[target]+ ('\n'+context if context else ''), 'structured_field'))
                if not any(re.fullmatch(LABEL, cell.rstrip(':：').strip(), re.I) for cell in cells) and not any(i<len(cells) and extract_codes(cells[i]) for i in code_columns):
                    values.append((f'表{table_number}行{row_number}', '\n'.join(cells), 'body_field'))
        return values, None
    if suffix in ('.xlsx', '.xlsm'):
        import openpyxl
        book = openpyxl.load_workbook(disk_path(path), read_only=True, data_only=True)
        values = []
        try:
            for sheet in book:
                code_columns = set(); name_columns = set()
                for row in sheet:
                    name_columns.update(i for i,c in enumerate(row) if c.value in ('项目名称','建设项目名称','project_name'))
                    record_name = next((str(row[i].value) for i in name_columns if i<len(row) and row[i].value and row[i].value not in ('项目名称','建设项目名称','project_name')), '')
                    context = '\n项目名称：'+record_name if record_name else ''
                    for index, cell in enumerate(row):
                        if cell.value in ('项目代码', 'project_code', '项目编号','建设项目代码') and index + 1 < len(row):
                            code_columns.add(index)
                            target = row[index + 1]
                            if target.value is not None and re.match(r'[0-9０-９]',str(target.value)):
                                values.append((f'{sheet.title}!{target.coordinate}', str(target.value)+context, 'structured_field'))
                        elif cell.value in ('项目代码', 'project_code', '项目编号','建设项目代码'):
                            code_columns.add(index)
                        elif index in code_columns and cell.value is not None and re.match(r'[0-9０-９]',str(cell.value)):
                            values.append((f'{sheet.title}!{cell.coordinate}', str(cell.value)+context, 'structured_field'))
                        elif isinstance(cell.value, str):
                            values.append((f'{sheet.title}!{cell.coordinate}', cell.value+context, 'body_field'))
        finally: book.close()
        return values, None
    return ([], None) if suffix in IMAGES else ([], 'unsupported_body_format')


def build_code_index(folders, materials, catalog, report):
    db = catalog.db
    recognition = Recognition(catalog)
    db.execute('CREATE TABLE IF NOT EXISTS project_code_cache (source TEXT PRIMARY KEY, version TEXT, parser TEXT, payload TEXT)')
    cached = {row[0]: row[1:] for row in db.execute('SELECT source,version,parser,payload FROM project_code_cache')}
    content_cache = {}
    rows = []; seen = set(); issues = []; skipped = defaultdict(int); active = set()
    native_path = Path(__file__).resolve().parent.parent / 'work/pdf_text_cache.json'
    try: native_cache = json.loads(native_path.read_text(encoding='utf-8')) if native_path.is_file() else {}
    except (OSError, ValueError): native_cache = {}

    def add(folder, text, source_type, source, location='', labelled=False, hits=None, source_version=None, physical_path=None):
        for hit in extract_codes(text, labelled) if hits is None else hits:
            key = (folder['id'], source, location, hit['raw_code'], source_type, hit['location'])
            if key in seen: continue
            seen.add(key)
            hit = dict(hit)
            if source_type in ('filename','project_directory','subdirectory','structured_field'): hit['grade'] = 'A'
            hit.setdefault('ownership_status', 'current_project' if hit['grade']=='A' else 'unresolved_ownership')
            hit.setdefault('ownership_unique', hit['ownership_status']=='current_project')
            hit.setdefault('ownership_basis', '完整代码位于目录或文件名称')
            hit.setdefault('recognition_status', 'recognized')
            rows.append(dict(hit, directory_id=folder['id'], stage=folder['stage'], project_name=folder['name'],
                             code_type='project_code', source_type=source_type, source_path=source,
                             physical_path=physical_path or source, source_location=location or hit['location'],
                             source_record=record_location(location or hit['location']),
                             evidence_text=hit['text_evidence'], verification_status='valid' if hit['valid'] else 'invalid',
                             needs_review=source_type=='ocr' or hit['grade']!='A' or not hit['valid'],
                             location=location or hit['location'], priority=PRIORITY[source_type],
                             confidence='high' if hit['valid'] else 'review', source_version=source_version, parser_version=RULE_VERSION))

    by_id = {f['id']: f for f in folders if f['kind'] != 'ungrouped'}
    for folder in by_id.values():
        add(folder, normalize_dir_name(Path(folder['folder']).name), 'project_directory', folder['folder'])
    pool = ThreadPoolExecutor(max_workers=4)
    pdf_reads = {}
    try:
        for material in materials:
            path = Path(material['physical_path'])
            if path.suffix.lower()!='.pdf' or material['business_directory_id'] not in by_id: continue
            source = material['logical_path']
            version = json.dumps([str(catalog.root), material['version']])
            previous = cached.get(source)
            if previous and previous[:2] == (version, RULE_VERSION): continue
            if not path.resolve().is_relative_to(catalog.root): raise ValueError('M1 PDF path outside source')
            key = material.get('sha256') or source
            if key not in pdf_reads:
                pdf_reads[key] = pool.submit(read_code_material, path, native_cache)
        for index, material in enumerate(materials, 1):
            folder = by_id.get(material['business_directory_id'])
            if folder is None:
                skipped['ungrouped_material'] += 1
                continue
            source = material['logical_path']; path = Path(material['physical_path'])
            cheap_texts = [folder['name'], path.name]
            if not path.resolve().is_relative_to(catalog.root):
                raise ValueError('M1 材料路径超出数据根目录')
            add(folder, path.name, 'filename', source, source_version=material['version'], physical_path=str(path))
            parts = re.split(r'[/\\!]', source)
            # 从项目根之后逐级取证，分类父目录的代码不应污染子项目。
            root_name = normalize_dir_name(Path(folder['folder']).name)
            start = next((i for i, part in enumerate(parts) if canonical_code(normalize_dir_name(part)) == canonical_code(root_name)), None)
            if start is not None:
                for i in range(start + 1, len(parts) - 1):
                    cheap_texts.append(parts[i])
                    physical = next((parent for parent in path.parents if normalize_dir_name(parent.name) == normalize_dir_name(parts[i])), path.parent)
                    add(folder, parts[i], 'subdirectory', '/'.join(parts[:i + 1]), source_version=material['version'], physical_path=str(physical))
            active.add(source)
            # shortcut: 元数据版本不能识别刻意保留大小及全部时间戳的改写，此类数据需清缓存或升级内容签名。
            version = json.dumps([str(catalog.root), material['version']])
            previous = cached.get(source)
            content_key = (material.get('sha256'), path.suffix.lower()) if material.get('sha256') else None
            if not previous and content_key in content_cache:
                previous = (version, RULE_VERSION, content_cache[content_key])
            if previous and previous[:2] == (version, RULE_VERSION):
                payload = json.loads(previous[2])
            else:
                try:
                    reader = pdf_reads.get(material.get('sha256') or source) if path.suffix.lower()=='.pdf' else None
                    texts, reason = reader.result() if reader else read_code_material(path, native_cache)
                    matches = []; malformed = []
                    for location, text, source_type in texts:
                        hits = extract_codes(text)
                        names = re.findall(NAME, text)
                        for hit in hits:
                            if source_type=='structured_field': hit['grade']='A'
                            hit['record_project_names'] = names
                            offset = int(hit['location'].split()[-1])
                            hit['context_text'] = text[max(0,offset-500):offset+700]
                            if hit['grade']=='C' and names: hit['grade']='B'
                            hit['referenced'] = bool(re.search(r'(?:引用|参照|参考|另一个项目|其他项目)[^\n。；]{0,30}$', text[:int(hit['location'].split()[-1])]))
                        if hits: matches.append((location, source_type, hits))
                        elif re.search(LABEL, text, re.I) or source_type=='structured_field': malformed.append(location)
                    payload = dict(matches=matches, skipped=reason, malformed=malformed, texts=texts if path.suffix.lower()=='.pdf' else [])
                except Exception as error:
                    payload = dict(matches=[], error=str(error))
                # 失败不缓存，依赖或权限恢复后可重试。
                if 'error' not in payload:
                    db.execute('INSERT OR REPLACE INTO project_code_cache VALUES (?,?,?,?)', (source, version, RULE_VERSION, json.dumps(payload, ensure_ascii=False)))
            if file_version(path) != material['version']:
                raise OSError('M1 读取时文件版本发生变化，请重试')
            if content_key and not payload.get('error'):
                content_cache[content_key] = json.dumps(payload, ensure_ascii=False)
                db.execute('INSERT OR REPLACE INTO project_code_cache VALUES (?,?,?,?)', (source, version, RULE_VERSION, content_cache[content_key]))
            if payload.get('error'): issues.append(dict(path=source, directory_id=folder['id'], error=payload['error'], category='m1_read_error', blocking=False))
            if payload.get('skipped'): skipped[payload['skipped']] += 1
            if payload.get('malformed'): issues.append(dict(path=source, directory_id=folder['id'], error='项目代码字段无法可靠识别', locations=payload['malformed'], category='m1_unrecognized_code', blocking=False))
            matches = list(payload['matches'])
            # 原生文字已可靠识别的页不再 OCR；空白、缺失字段及没有可靠字段代码的页按需进入候选。
            if path.suffix.lower() == '.pdf' or path.suffix.lower() in IMAGES:
                texts = payload.get('texts', [])
                reliable = {location for location, _, hits in matches if any(h['valid'] and h['grade']=='A' for h in hits)}
                pending_pages = [int(location.split('=')[1]) for location, _, _ in texts if location not in reliable] if texts else None
                cheap_codes = {hit['normalized_code'] for text in cheap_texts for hit in extract_codes(text) if hit['valid']}
                if pending_pages != [] and (len(cheap_codes)!=1 or payload.get('malformed')):
                    report('M1 按需 OCR', member=source, done=index, total=len(materials), stage=material['stage'])
                    ocr_results, states = recognition.ocr(path, material['version'], texts, pending_pages)
                    for location, value in ocr_results:
                        hits = extract_codes(value['text'])
                        for hit in hits:
                            hit.update(record_project_names=re.findall(NAME,value['text']),
                                       ocr_engine=value['engine'], ocr_layout=value['layout'], recognition_status='recognized')
                            offset = int(hit['location'].split()[-1])
                            hit['context_text'] = value['text'][max(0,offset-500):offset+700]
                            hit['referenced'] = bool(re.search(r'(?:引用|参照|参考|另一个项目|其他项目)[^\n。；]{0,30}$', value['text'][:offset]))
                            if hit['grade']=='C' and hit['record_project_names']: hit['grade']='B'
                        if not any(h['valid'] for h in hits):
                            issues.append(dict(path=source, physical_path=str(path), directory_id=folder['id'], category='m1_ocr', blocking=False,
                                               error='OCR 已读取文字但没有可验证的完整项目代码，疑似字符须人工核对', state='unrecognized',
                                               source_location=location, evidence_text=value['text'][:1200], source_version=material['version'], ocr_engine=value['engine']))
                        matches.append((location, 'ocr', hits))
                    for state in states:
                        if state['state'] != 'recognized':
                            issues.append(dict(path=source, physical_path=str(path), directory_id=folder['id'], category='m1_ocr', blocking=False,
                                               source_version=material['version'], parser_version=RULE_VERSION,
                                               error='OCR '+state['state']+'：'+state.get('reason','未提取文字'), **state))
            material_codes = {h['normalized_code'] for _,_,hits in matches for h in hits if h['valid']}
            record_codes = defaultdict(set)
            for location, _, hits in matches:
                record_codes[record_location(location)].update(h['normalized_code'] for h in hits if h['valid'])
            for location, source_type, hits in matches:
                for hit in hits:
                    names = hit.get('record_project_names', [])
                    # 名称只用于验证代码的局部归属，跨阶段命中仍必须是完整代码相等。
                    local_codes = record_codes[record_location(location)]
                    owned = len(local_codes)==1 and len(set(names))==1 and canonical_code(names[0])==canonical_code(folder['name'])
                    hit['ownership_unique'] = owned or (len(material_codes)==1 and not names and hit['grade']=='A')
                    hit['ownership_status'] = 'current_project' if hit['ownership_unique'] else 'unresolved_ownership'
                    if names and not owned or hit.get('referenced'):
                        hit['ownership_status']='unresolved_ownership'; hit['ownership_unique']=False
                    hit['ownership_basis'] = ('局部项目名称与目录名称完全一致' if owned else '单代码明确字段，待人工确认目录关系') if hit['ownership_unique'] else '局部名称不符、多代码、引用或缺少可靠归属上下文'
                    if hit['grade']!='A' or hit['ownership_status']!='current_project':
                        hit['semantic'] = recognition.semantic(hit, location, material['version'], source)
                        if hit['semantic'].get('conclusion',{}).get('ownership') in ('other_project','uncertain'):
                            hit['ownership_status']='unresolved_ownership'; hit['ownership_unique']=False
                        # 模型只能提出复核意见，不能把非确定性证据提升为可自动索引的目录代码。
                    add(folder, '', source_type, source, location, hits=[hit], source_version=material['version'], physical_path=str(path))
            if index % 100 == 0:
                db.commit()
                report('M1 项目代码索引', done=index, total=len(materials), stage=material['stage'])
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    for source in set(cached) - active:
        db.execute('DELETE FROM project_code_cache WHERE source=?', (source,))

    codes = defaultdict(set)
    for row in rows: codes[row['directory_id']].add(row['normalized_code'])
    for row in rows:
        row['conflict'] = len(codes[row['directory_id']]) > 1 or not row['valid']
        if row['conflict'] and not row.get('record_project_names'):
            row['ownership_status']='unresolved_ownership'; row['ownership_unique']=False
        row['needs_review'] |= row['ownership_status']!='current_project' or row['conflict']
        row['confidence'] = 'review' if row['needs_review'] else 'high'
    review_ids = {row['directory_id'] for row in rows if row['needs_review']} | {i['directory_id'] for i in issues}
    context_by_dir = defaultdict(list); pending_by_dir = defaultdict(list)
    for row in rows: context_by_dir[row['directory_id']].append(row)
    for issue in issues: pending_by_dir[issue['directory_id']].append(issue)
    for folder in by_id.values():
        folder['code_review'] = folder['id'] in review_ids
        if len(codes[folder['id']]) > 1 and hasattr(catalog, 'flag_boundary_review'):
            catalog.flag_boundary_review(folder['id'], f"M1发现同一目录内存在多个不同项目代码({','.join(sorted(codes[folder['id']]))})，可能包含多项目材料，需复核目录边界")
            folder['boundary_review'] = True
        context = context_by_dir[folder['id']]
        pending = pending_by_dir[folder['id']]
        folder['code_evidence_version'] = hashlib.sha256(json.dumps([context,pending],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    index = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row['valid'] and row['grade'] in ('A','B') and row['ownership_status']=='current_project': index[row['normalized_code']][row['directory_id']].append(row)
    # 同一代码出现在同一阶段多个目录时，一对多关系全部保留并标记歧义（绝不自动合并宗地）。
    links = {}
    for code, owners in index.items():
        stages = defaultdict(list)
        for ident in owners: stages[by_id[ident]['stage']].append(ident)
        for sid, stage_idents in stages.items():
            if len(stage_idents) > 1 and hasattr(catalog, 'flag_boundary_review'):
                for ident in stage_idents:
                    catalog.flag_boundary_review(ident, f"M1发现同阶段({sid})多个目录共享项目代码({code})，保留多宗地独立候选，禁止自动合并")
                    by_id[ident]['boundary_review'] = True
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
    stats.update(unresolved_ownership=sum(r['ownership_status']=='unresolved_ownership' for r in rows),
                 discovered_codes=len({r['normalized_code'] for r in rows if r['valid']}),
                 ocr_states={state:sum(i.get('state')==state for i in issues) for state in ('failed','skipped','unprocessed','unrecognized')},
                 semantic_calls=recognition.calls, semantic_cost_upper_bound=recognition.cost)
    return rows, list(links.values()), stats, issues
