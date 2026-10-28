"""保存第二步整理目录新扫描的挂接关系；禁止从历史 JSON 回填。"""
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3


def save_directory_mounts(storage_dir, root, links, scan=None):
    folder = Path(storage_dir)
    folder.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(folder / 'project_catalog.sqlite', timeout=30)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS catalog_source (root TEXT NOT NULL)')
        bound = db.execute('SELECT root FROM catalog_source').fetchone()
        source = str(Path(root).resolve())
        if bound and bound[0] != source:
            raise ValueError('挂接结果属于其他数据源，请使用独立存储目录')
        if not bound:
            db.execute('INSERT INTO catalog_source VALUES (?)', (source,))
        db.execute('''CREATE TABLE IF NOT EXISTS directory_mounts (
            id TEXT PRIMARY KEY, left_id TEXT NOT NULL, right_id TEXT NOT NULL,
            left_stage TEXT NOT NULL, right_stage TEXT NOT NULL,
            left_path TEXT NOT NULL, right_path TEXT NOT NULL,
            methods TEXT NOT NULL, status TEXT NOT NULL, signature TEXT NOT NULL,
            evidence_count INTEGER NOT NULL, payload TEXT NOT NULL,
            confirmation TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        db.execute('CREATE TABLE IF NOT EXISTS directory_mount_state (id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)')
        previous_state = db.execute('SELECT payload FROM directory_mount_state WHERE id=1').fetchone()
        previous_state = json.loads(previous_state[0]) if previous_state else {}
        # 先完整校验待存数据，失败时事务回滚，不能使上一轮结果失效。
        rows = []
        for link in links:
            link = dict(link)
            old = db.execute('SELECT confirmation FROM directory_mounts WHERE id=?', (link['id'],)).fetchone()
            confirmation = json.loads(old[0]) if old else link.get('confirmation', {})
            if confirmation:
                same = (confirmation.get('signature') == link['signature']
                        and confirmation.get('review_context') == link.get('review_context')
                        and (not scan or confirmation.get('rule_version') == scan.get('rule_version'))
                        and (not scan or previous_state.get('m1_rule_version') == scan.get('m1_rule_version')))
                link['status'] = confirmation.get('status', 'pending') if same else 'review'
                link['confirmation'] = confirmation
            left, right = link['left'], link['right']
            rows.append((link['id'], left['id'], right['id'], left['stage'], right['stage'],
                         left['folder'], right['folder'],
                         json.dumps(link.get('methods', [link.get('method', 'M2')]), ensure_ascii=False),
                         link.get('status', 'pending'), link['signature'],
                         link.get('evidence_count', len(link.get('evidence', []))),
                         json.dumps(link, ensure_ascii=False, sort_keys=True),
                         json.dumps(link.get('confirmation', {}), ensure_ascii=False, sort_keys=True)))
        current = {row[0] for row in rows}
        db.executemany('UPDATE directory_mounts SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                       [(ident,) for ident, in db.execute('SELECT id FROM directory_mounts WHERE active=1') if ident not in current])
        db.executemany('''INSERT INTO directory_mounts
            (id,left_id,right_id,left_stage,right_stage,left_path,right_path,methods,status,
             signature,evidence_count,payload,confirmation) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET left_id=excluded.left_id,right_id=excluded.right_id,
            left_stage=excluded.left_stage,right_stage=excluded.right_stage,
            left_path=excluded.left_path,right_path=excluded.right_path,methods=excluded.methods,
            status=excluded.status,signature=excluded.signature,evidence_count=excluded.evidence_count,
            payload=excluded.payload,confirmation=excluded.confirmation,active=1,updated_at=CURRENT_TIMESTAMP
            WHERE directory_mounts.payload!=excluded.payload OR directory_mounts.active=0''', rows)
        if scan is not None:
            state = {k:v for k,v in scan.items() if k not in ('links', 'materials')}
            state['scanned_at'] = datetime.now().isoformat(timespec='seconds')
            db.execute('INSERT OR REPLACE INTO directory_mount_state VALUES (1,?)',
                       (json.dumps(state, ensure_ascii=False),))


def load_directory_mounts(storage_dir, root):
    path = Path(storage_dir) / 'project_catalog.sqlite'
    empty = dict(folders=[], links=[], errors=[])
    if not path.is_file():
        return empty
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)) as db, db:
        db.execute('BEGIN')
        bound = db.execute('SELECT root FROM catalog_source').fetchone()
        if not bound or bound[0] != str(Path(root).resolve()):
            raise ValueError('挂接结果属于其他数据源')
        if not db.execute("SELECT name FROM sqlite_master WHERE name='directory_mounts'").fetchone():
            return empty
        has_state = db.execute("SELECT name FROM sqlite_master WHERE name='directory_mount_state'").fetchone()
        state = db.execute('SELECT payload FROM directory_mount_state WHERE id=1').fetchone() if has_state else None
        result = json.loads(state[0]) if state else empty
        result['links'] = [json.loads(row[0]) for row in db.execute('SELECT payload FROM directory_mounts WHERE active=1 ORDER BY left_stage,left_path,right_stage,right_path,id')]
        return result


def review_directory_mount(storage_dir, root, ident, signature, status, rule_version):
    if status not in ('pending', 'confirmed', 'rejected', 'review'):
        raise ValueError('关联状态无效。')
    path = Path(storage_dir) / 'project_catalog.sqlite'
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, timeout=30)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        bound = db.execute('SELECT root FROM catalog_source').fetchone()
        if not bound or bound[0] != str(Path(root).resolve()):
            raise ValueError('挂接结果属于其他数据源')
        state = db.execute('SELECT payload FROM directory_mount_state WHERE id=1').fetchone()
        if not state or json.loads(state[0]).get('rule_version') != rule_version:
            raise ValueError('挂接规则已变化，请重新扫描后审核。')
        row = db.execute('SELECT payload FROM directory_mounts WHERE id=? AND active=1', (ident,)).fetchone()
        link = json.loads(row[0]) if row else None
        if not link or link['signature'] != signature:
            raise ValueError('关联证据已变化，请刷新后重新核对。')
        previous = link.get('confirmation', {})
        history = previous.get('history', [])
        if previous:
            history = history + [{k:v for k,v in previous.items() if k != 'history'}]
        confirmation = dict(status=status, signature=signature, review_context=link.get('review_context'),
                            method_signatures=link.get('method_signatures', {'M2': signature}),
                            rule_version=rule_version, updated_at=datetime.now().isoformat(timespec='seconds'),
                            actor='本机用户', history=history)
        link.update(status=status, confirmation=confirmation)
        db.execute('UPDATE directory_mounts SET status=?,confirmation=?,payload=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                   (status, json.dumps(confirmation, ensure_ascii=False, sort_keys=True),
                    json.dumps(link, ensure_ascii=False, sort_keys=True), ident))
