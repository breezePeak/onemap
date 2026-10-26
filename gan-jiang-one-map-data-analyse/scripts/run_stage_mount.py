"""七阶段原地增量解压、规则识别大目录与 M1/M2 候选关联脚本。

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
from folder_mount import scan_folders, PreparationError, validate_methods
from project_catalog import ProjectCatalog
from organize_independent_documents import organize_independent_documents


def run_pipeline(source=SOURCE, methods=None, verbose=True):
    methods = ['M2'] if methods is None else methods
    validate_methods(methods)
    validation = validate_source_stages(source)
    if not validation['is_valid']:
        raise FileNotFoundError(f"数据源验证失败，缺少子目录: {validation['missing']}")

    # 预处理：对汇编分类层下非同一项目的独立散落文书，自动按文件名新建独立项目大目录
    if verbose:
        print("\n=== 执行独立业务项目文书自动归档规整 ===")
    organize_independent_documents(root=source, verbose=verbose)

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
            methods=methods,
            storage_dir=mount_dir
        )
        (mount_dir / 'hashes.json').write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
        result_payload = dict(result, scanned_at=datetime.now().isoformat(timespec='seconds'))
        for method in methods:
            (mount_dir / f'result-{method}.json').write_text(json.dumps(result_payload, ensure_ascii=False), encoding='utf-8')
        snapshot = mount_dir / 'result.json.tmp'
        snapshot.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding='utf-8')
        snapshot.replace(mount_dir / 'result.json')
        task_payload = dict(status='ready', phase='完成（含未读取材料，请核对异常）' if result.get('errors') else '完成', errors=result.get('errors', []), stages=result.get('stages', []))
        (mount_dir / 'task.json').write_text(json.dumps(task_payload, ensure_ascii=False, indent=2), encoding='utf-8')

        catalog = ProjectCatalog(storage_dir=mount_dir, root=source)
        print("\n=== 执行成功 ===")
        print(f"材料条目数: {result['file_count']}；有效材料数: {len(result['materials'])}")
        print(f"计算哈希材料数: {result['hashed_count']}")
        print(f"已研判存库大目录数: {len(catalog.projects)}")
        print(f"生成跨阶段 {'+'.join(methods)} 关联数: {len(result['links'])}")
        if result.get('m1_statistics'): print(json.dumps(result['m1_statistics'], ensure_ascii=False))
        catalog.close()
        return result
    except PreparationError as err:
        (mount_dir / 'hashes.json').write_text(json.dumps(err.cache, ensure_ascii=False), encoding='utf-8')
        (mount_dir / 'inventory.json').write_text(json.dumps({k:err.result[k] for k in ('folders','stages','errors','rule_version','methods')}, ensure_ascii=False), encoding='utf-8')
        (mount_dir / 'task.json').write_text(json.dumps(dict(status='failed',phase=str(err),errors=err.result['errors'],stages=err.result['stages']), ensure_ascii=False), encoding='utf-8')
        print(f"\n[阻断异常] {err}")
        raise
    except Exception as err:
        (mount_dir / 'task.json').write_text(json.dumps(dict(status='failed',phase='任务失败',errors=[dict(path=str(source),error=str(err))]), ensure_ascii=False), encoding='utf-8')
        raise


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='七阶段 M1/M2 目录关联')
    parser.add_argument('--methods', nargs='+', choices=['M1', 'M2'], default=['M2'])
    parser.add_argument('--source', type=Path, default=SOURCE)
    args = parser.parse_args()
    run_pipeline(args.source, args.methods)
