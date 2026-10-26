"""M1 按页调用已有渲染/OCR；语义适配器只接收候选所在片段，不能确认关联。"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

SCRIPTS = Path(__file__).resolve().parent
IMAGES = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.gif', '.webp'}
SEMANTIC_SCHEMA = dict(type='object', additionalProperties=False,
    required=['normalized_code', 'source_location', 'ownership', 'confidence', 'evidence_text', 'needs_review'],
    properties=dict(normalized_code=dict(type='string'), source_location=dict(type='string'),
        ownership=dict(enum=['current_project', 'other_project', 'uncertain']),
        confidence=dict(enum=['review', 'uncertain']), evidence_text=dict(type='string'), needs_review=dict(const=True)))


class Recognition:
    def __init__(self, catalog):
        self.db = catalog.db
        self.root = str(catalog.root)
        self.storage_dir = catalog.storage_dir
        self.options = json.loads(os.environ.get('ONEMAP_M1_OPTIONS', '{}'))
        if not isinstance(self.options, dict): raise ValueError('ONEMAP_M1_OPTIONS 必须是 JSON 对象')
        defaults = dict(ocr=False, max_pages=3, max_files=30, max_bytes=40*1024*1024,
                        timeout=45, retries=1, max_seconds=300, max_calls=10,
                        semantic_command=[], semantic_version='', cost_per_call=0, max_cost=0)
        if set(self.options) - set(defaults): raise ValueError('未知 M1 识别配置')
        defaults.update(self.options); self.options = defaults
        for key in ('max_pages', 'max_files', 'max_bytes', 'timeout', 'max_seconds', 'max_calls'):
            if type(defaults[key]) is not int or defaults[key] <= 0: raise ValueError(f'{key} 必须为正整数')
        if type(defaults['retries']) is not int or not 0 <= defaults['retries'] <= 3: raise ValueError('retries 必须在0至3之间')
        for key in ('cost_per_call', 'max_cost'):
            if type(defaults[key]) not in (int, float) or not 0 <= defaults[key] < float('inf'): raise ValueError(f'{key} 必须为有限非负数')
        command = defaults['semantic_command']
        if not isinstance(command, list) or any(not isinstance(v, str) or not v for v in command): raise ValueError('semantic_command 必须是非空字符串数组')
        if command and (not isinstance(defaults['semantic_version'],str) or not defaults['semantic_version'] or defaults['cost_per_call'] <= 0 or defaults['max_cost'] <= 0):
            raise ValueError('语义适配器必须配置版本、每次费用上界及总费用上限')
        if type(defaults['ocr']) is not bool: raise ValueError('ocr 必须为布尔值')
        self.elapsed = 0; self.files = 0; self.calls = 0; self.cost = 0
        self.engine = None
        self.db.execute('CREATE TABLE IF NOT EXISTS code_recognition_cache (id TEXT PRIMARY KEY, payload TEXT)')

    def run(self, command, **kwargs):
        remaining = self.options['max_seconds'] - self.elapsed
        if remaining <= 0: raise TimeoutError('本轮识别总时限已到')
        started = time.monotonic()
        try:
            return subprocess.run(command, capture_output=True, encoding='utf-8', errors='replace', check=True,
                timeout=min(self.options['timeout'], remaining), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), **kwargs)
        finally: self.elapsed += time.monotonic()-started

    def key(self, *values):
        return hashlib.sha256(json.dumps([self.root, *values], ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def cached(self, key):
        row = self.db.execute('SELECT payload FROM code_recognition_cache WHERE id=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO code_recognition_cache VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))
        self.db.commit()  # 每页落库，中断后复用已完成页；失败不缓存。
        return value

    def ocr_page(self, path, page, version, allow_new=True):
        if self.engine is None and not allow_new: return None
        if self.engine is None:
            description = json.loads(self.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPTS/'ocr_images.ps1'), '-Describe']).stdout.lstrip('\ufeff'))
            scripts = [(SCRIPTS/name).read_bytes() for name in ('ocr_images.ps1', 'render_pdfs.mjs', 'image_materials.py', 'code_recognition.py')]
            from PIL import __version__ as pillow_version
            scripts.append(pillow_version.encode())
            for package in ('pdfjs-dist','@napi-rs/canvas'):
                metadata = SCRIPTS.parent/'node_modules'/package/'package.json'
                scripts.append(metadata.read_bytes() if metadata.is_file() else b'unavailable')
            self.engine = dict(description, adapter_version=hashlib.sha256(b''.join(scripts)).hexdigest())
        key = self.key('ocr', str(path.resolve()), version, page, self.engine)
        saved = self.cached(key)
        if saved is not None: return dict(saved, cache_hit=True)
        if not allow_new: return None
        with tempfile.TemporaryDirectory(dir=self.storage_dir) as directory:
            directory = Path(directory); image = directory/'page.png'; text = directory/'page.txt'
            if path.suffix.lower() == '.pdf':
                self.run(['node', str(SCRIPTS/'render_pdfs.mjs'), '--page', str(path), str(page), str(image)])
            else:
                # Pillow 的多帧 TIFF/GIF 同样逐帧处理；转换在受超时限制的子进程内执行。
                code = 'import sys;from PIL import Image;from image_materials import prepare_image;im=Image.open(sys.argv[1]);im.seek(int(sys.argv[2])-1);im.convert("RGB").save(sys.argv[3]);im.close();prepare_image(sys.argv[3],sys.argv[3])'
                import sys
                self.run([sys.executable, '-c', code, str(path), str(page), str(image)], cwd=SCRIPTS)
            manifest = directory/'manifest.json'
            manifest.write_text(json.dumps([dict(path=str(image), text=str(text), source=str(path))]), encoding='utf-8')
            self.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPTS/'ocr_images.ps1'), '-Manifest', str(manifest)])
            error_file = Path(str(text)+'.error')
            if error_file.exists(): raise RuntimeError('OCR 失败：'+error_file.read_text(encoding='utf-8-sig'))
            if not text.is_file(): raise RuntimeError('OCR 未生成文字结果')
            return self.save(key, dict(text=text.read_text(encoding='utf-8-sig'),
                layout=json.loads(Path(str(text)+'.layout.json').read_text(encoding='utf-8-sig')), engine=self.engine))

    def ocr(self, path, version, texts, candidate_pages=None):
        if not self.options['ocr']: return [], [dict(state='unprocessed', reason='ocr_disabled')]
        if path.stat().st_size > self.options['max_bytes']: return [], [dict(state='skipped', reason='ocr_size_limit')]
        if self.elapsed >= self.options['max_seconds']: return [], [dict(state='unprocessed', reason='ocr_time_limit')]
        try:
            if path.suffix.lower() == '.pdf':
                # Native extraction already supplies page count; failed extraction gets a bounded metadata subprocess.
                count = len(texts)
                if not count:
                    import sys
                    count = int(self.run([sys.executable, '-c', 'import sys;from pypdf import PdfReader;print(len(PdfReader(sys.argv[1]).pages))', str(path)]).stdout)
            else:
                import sys
                count = int(self.run([sys.executable, '-c', 'import sys;from PIL import Image;im=Image.open(sys.argv[1]);print(getattr(im,"n_frames",1));im.close()', str(path)]).stdout)
            pages = list(candidate_pages) if candidate_pages is not None else list(range(1, count+1))
            clues = {int(location.split('=')[1]) for location, text, _ in texts if location.startswith('page=') and any(v in text for v in ('项目', '建设', '代码'))}
            pages.sort(key=lambda p: (p != 1, p not in clues, p))
            results = []; states = []; new_pages = 0; charged = False; pending = 0
            for page in pages:
                if self.elapsed >= self.options['max_seconds']:
                    pending += 1; continue
                allow_new = new_pages < self.options['max_pages'] and (charged or self.files < self.options['max_files'])
                for attempt in range(self.options['retries']+1):
                    try:
                        value = self.ocr_page(path, page, version, allow_new=allow_new)
                        if value is None:
                            pending += 1; break
                        if not value.get('cache_hit'):
                            new_pages += 1
                            if not charged: self.files += 1; charged = True
                        results.append((f'page={page}' if path.suffix.lower()=='.pdf' else f'image={page}', value))
                        states.append(dict(state='recognized' if value['text'].strip() else 'unrecognized', page=page))
                        break
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                        if attempt == self.options['retries']:
                            new_pages += 1
                            if not charged: self.files += 1; charged = True
                            states.append(dict(state='failed', page=page, reason=str(error)))
            if pending: states.append(dict(state='unprocessed', reason='ocr_resource_limit', remaining=pending, total_pages=count))
            return results, states
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            return [], [dict(state='failed', reason=str(error))]

    def semantic(self, hit, location, version, source):
        command = self.options['semantic_command']
        if not command: return dict(status='unprocessed', reason='semantic_not_configured')
        request = dict(normalized_code=hit['normalized_code'], source_location=location,
                       evidence_text=hit.get('context_text', hit['text_evidence'])[:1200], schema=SEMANTIC_SCHEMA)
        key = self.key('semantic', source, version, request, command, self.options['semantic_version'])
        saved = self.cached(key)
        if saved is not None: return saved
        if self.elapsed >= self.options['max_seconds']: return dict(status='unprocessed', reason='semantic_time_limit')
        if self.calls >= self.options['max_calls'] or self.cost+self.options['cost_per_call'] > self.options['max_cost']:
            return dict(status='unprocessed', reason='semantic_budget_limit')
        self.calls += 1; self.cost += self.options['cost_per_call']
        try:
            value = json.loads(self.run(command, input=json.dumps(request, ensure_ascii=False)).stdout)
            if not isinstance(value, dict) or set(value) != set(SEMANTIC_SCHEMA['required']): raise ValueError('语义输出不符合 Schema')
            if value['normalized_code'] != hit['normalized_code'] or value['source_location'] != location: raise ValueError('语义输出与来源不符')
            if value['ownership'] not in ('current_project','other_project','uncertain') or value['confidence'] not in ('review','uncertain') or value['needs_review'] is not True: raise ValueError('语义输出枚举或审核状态无效')
            if not isinstance(value['evidence_text'], str) or not value['evidence_text'] or value['evidence_text'] not in request['evidence_text']: raise ValueError('语义输出缺少原文证据')
            from project_codes import extract_codes
            if not any(v['valid'] and v['normalized_code']==hit['normalized_code'] for v in extract_codes(value['evidence_text'])): raise ValueError('语义输出代码未在原文中验证')
            return self.save(key, dict(status='review', conclusion=value, model_version=self.options['semantic_version']))
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            return dict(status='failed', reason=str(error))
