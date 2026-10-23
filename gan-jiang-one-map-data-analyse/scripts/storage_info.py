"""只读展示共享解压索引与文件盘点快照，不创建业务数据库。"""
from contextlib import closing
from pathlib import Path
import json
import sqlite3


def storage_info(base, source, kind='archive', query='', page=1):
    if kind not in ('archive', 'inventory') or page < 1:
        raise ValueError('文件视图或页码无效。')
    database=base/'解压材料/解压记录.sqlite'
    inventory_path=base/'work/files.json'
    inventory=json.loads(inventory_path.read_text(encoding='utf-8')) if inventory_path.is_file() else []
    counts={};entries=[];total=0;limit=50
    if database.is_file():
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            counts={row['status']:row['n'] for row in db.execute('SELECT status,COUNT(*) AS n FROM files GROUP BY status')}
            if kind=='archive':
                where='WHERE instr(lower(archive || char(10) || raw || char(10) || coalesce(dest,\'\')), lower(?)) > 0'
                total=db.execute('SELECT COUNT(*) FROM files '+where,(query,)).fetchone()[0]
                entries=[dict(row) for row in db.execute('SELECT archive,raw,dest,size,status,error FROM files '+where+' ORDER BY archive,raw LIMIT ? OFFSET ?',(query,limit,(page-1)*limit))]
                for entry in entries:
                    entry['source']=entry['archive']+'!'+entry['raw']
                    entry['record']='files 表（共享数据库中的一条记录）'
    if kind=='inventory':
        matches=[row for row in inventory if query.casefold() in (row.get('source','')+' '+row['path']).casefold()]
        total=len(matches)
        entries=[dict(source=row.get('source') or row['path'],dest=row['path'],size=row.get('size',0),status='snapshot',record='files.json 盘点记录；不代表已入数据库') for row in matches[(page-1)*limit:page*limit]]
    for entry in entries:
        path=Path(entry['dest']) if entry.get('dest') else None
        entry['exists']=path.is_file() if path else False
        if entry['status']=='done' and entry['exists']:
            entry['location']='复用原目录文件' if path.resolve().is_relative_to(source.resolve()) else '解压到本地文件'
        else:entry['location']='文件盘点快照' if entry['status']=='snapshot' else '解压记录'
    return dict(database=str(database),database_exists=database.is_file(),counts=counts,
                inventory=str(inventory_path),inventory_count=len(inventory),source_root=str(source),
                work=str(base/'work'),jobs=str(base/'work/jobs'),outputs=str(base/'outputs'),
                kind=kind,page=page,page_size=limit,total=total,entries=entries)
