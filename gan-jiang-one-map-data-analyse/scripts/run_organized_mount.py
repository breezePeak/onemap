"""从已整理的七阶段项目文件夹生成独立 M1/M2 挂接结果。"""
import argparse
from datetime import datetime
import json
from pathlib import Path

from folder_mount import scan_folders
from source_config import validate_source_stages

BASE = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = Path(r'F:\项目数据\赣江一张图_整理')
OUTPUT = BASE / 'work/folder_mount_organized'


def run(source=DEFAULT_SOURCE, methods=None, output=OUTPUT):
    source = Path(source).resolve()
    output = Path(output)
    validation = validate_source_stages(source)
    if not validation['is_valid']:
        raise FileNotFoundError(f"整理目录缺少阶段: {validation['missing']}")
    output.mkdir(parents=True, exist_ok=True)
    cache_path = output / 'hashes.json'
    cache = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.is_file() else {}
    def progress(value):
        if value.get('phase') != '建索引' or value.get('processed', 0) % 1000 == 0:
            print(value.get('phase'), value.get('current_stage'), value.get('processed', 0), flush=True)
    result, cache = scan_folders(source, output / 'archive/解压记录.sqlite', cache, progress,
                                 methods or ['M2'], output, organized=True)
    payload = dict(result, scanned_at=datetime.now().isoformat(timespec='seconds'))
    temp = output / 'result.json.tmp'
    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    temp.replace(output / 'result.json')
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
    print(f"项目目录 {len(result['folders'])}，关联候选 {len(result['links'])}", flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='从整理目录挂接项目文件夹')
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--methods', nargs='+', choices=['M1', 'M2'], default=['M2'])
    args = parser.parse_args()
    run(args.source, args.methods)
