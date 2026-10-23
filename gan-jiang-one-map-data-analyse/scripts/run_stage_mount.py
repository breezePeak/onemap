"""七阶段原地增量解压、大模型研判大目录与 M2 精确关联一键执行脚本。

可独立执行，也可由 onemap-stage-mount 技能或服务端调用。
"""
from datetime import datetime
import json
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'app'))

from source_config import SOURCE, STAGES, validate_source_stages
from folder_mount import scan_folders, PreparationError
from project_catalog import ProjectCatalog
from organize_independent_documents import organize_independent_documents


def run_pipeline(source=SOURCE, methods=None, verbose=True):
    validation = validate_source_stages(source)
    if not validation['is_valid']:
        raise FileNotFoundError(f"数据源验证失败，缺少子目录: {validation['missing']}")

    # 预处理：对汇编分类层下非同一项目的独立散落文书，自动按文件名新建独立项目大目录
    if verbose:
        print("\n=== 执行独立业务项目文书自动归档规整 ===")
    org_res = organize_independent_documents(root=source, verbose=verbose)

    db_path = BASE / '解压材料/解压记录.sqlite'
    mount_dir = BASE / 'work/folder_mount'
    mount_dir.mkdir(parents=True, exist_ok=True)
    hash_cache = {}
    if (mount_dir / 'hashes.json').is_file():
        try:
            hash_cache = json.loads((mount_dir / 'hashes.json').read_text(encoding='utf-8'))
        except Exception:
            pass

    def on_progress(p):
        if verbose:
            phase = p.get('phase', '')
            stage = p.get('current_stage', '')
            archive = p.get('current_archive', '').split('\\')[-1]
            proc = p.get('processed', 0)
            tot = p.get('total', 0)
            status_text = f"[{phase}] 阶段:{stage}"
            if archive:
                status_text += f" 包:{archive}"
            if tot:
                status_text += f" ({proc}/{tot})"
            print(status_text, flush=True)

    print(f"=== 开始执行七阶段数据盘点与关联治理流程 (源: {source}) ===")
    try:
        result, cache = scan_folders(
            root=source,
            database=db_path,
            cache=hash_cache,
            progress=on_progress,
            methods=['M2'] if methods is None else methods,
            storage_dir=mount_dir,
            tolerate_corrupted=True
        )
        (mount_dir / 'hashes.json').write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
        result_payload = dict(result, scanned_at=datetime.now().isoformat(timespec='seconds'))
        (mount_dir / 'result.json').write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding='utf-8')
        task_payload = dict(status='ready', phase='完成（含个别损坏/加密材料）' if result.get('errors') else '完成', errors=result.get('errors', []), stages=result.get('stages', []))
        (mount_dir / 'task.json').write_text(json.dumps(task_payload, ensure_ascii=False, indent=2), encoding='utf-8')

        catalog = ProjectCatalog(storage_dir=mount_dir, root=source)
        print("\n=== 执行成功 ===")
        print(f"有效材料文件数: {result['file_count']}")
        print(f"计算哈希材料数: {result['hashed_count']}")
        print(f"已研判存库大目录数: {len(catalog.projects)}")
        print(f"生成跨阶段 M2 关联数: {len(result['links'])}")
        return result
    except PreparationError as err:
        print(f"\n[阻断异常] {err}")
        return err.result


if __name__ == '__main__':
    run_pipeline()
