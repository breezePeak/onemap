"""独立业务项目文书归档规整模块。

针对 S2 规划条件盖章版（如 2024年用地规划条件、2025年用地规划条件等）中散落的独立项目规划条件文件：
这些文件各自代表不同的业务项目，不能混在同一分类层中；
本模块自动为每个散落文书新建与文件名同名的项目文件夹，并将文件归入该文件夹中，
使每个独立业务项目均拥有其专属的项目大目录。
"""
from pathlib import Path
import os
import shutil

from source_config import SOURCE, STAGES
from extract_archives import disk_path
from project_catalog import is_pure_classification, normalize_dir_name

DOCUMENT_EXTENSIONS = {'.pdf', '.docx', '.doc', '.wps'}


def should_organize_container(dir_path):
    """精确判断目录是否属于需要进行'单文件建独立项目文件夹'的规划条件汇编层。"""
    # 必须在规划条件盖章版目录下，或者是明显的规划条件汇编目录
    parts = [p.name for p in dir_path.parents] + [dir_path.name]
    if '规划条件盖章版' in parts and is_pure_classification(normalize_dir_name(dir_path.name)):
        return True
    return False


def organize_independent_documents(root=SOURCE, dry_run=False, verbose=True):
    """扫描规划条件盖章版等汇编层，对散落的独立项目文件，新建与文件名同名的文件夹并归入其中。"""
    root_path = Path(root).resolve()
    moved_count = 0
    created_folders = []

    s2_folders = [f for sid, f in STAGES if sid == 'S2']
    if not s2_folders:
        return dict(moved=0, folders=[])

    stage_dir = root_path / s2_folders[0]
    if not stage_dir.is_dir():
        return dict(moved=0, folders=[])

    for base, dirs, files in os.walk(disk_path(stage_dir)):
        current_dir = stage_dir / Path(base).relative_to(disk_path(stage_dir))
        dirs[:] = [d for d in dirs if not d.endswith('.解压') and not d.startswith('.')]
        if current_dir.name.endswith('.解压') or current_dir.name.startswith('.'):
            continue

        if not should_organize_container(current_dir):
            continue

        for filename in sorted(files):
            file_path = current_dir / filename
            suffix = file_path.suffix.lower()
            if suffix not in DOCUMENT_EXTENSIONS:
                continue
            if filename.startswith('~$') or filename.startswith('.'):
                continue

            stem_name = file_path.stem.strip()
            if not stem_name:
                continue

            # 目标项目文件夹（与文件名同名）
            target_folder = current_dir / stem_name
            target_file = target_folder / filename

            # 如果文件已经在同名文件夹里，则跳过
            if file_path.parent.name == stem_name:
                continue

            if verbose:
                print(f"[S2] 新建项目文件夹并归档: {file_path.name} -> {target_folder.name}/")

            if not dry_run:
                disk_path(target_folder).mkdir(parents=True, exist_ok=True)
                # 使用 disk_path 支持 Windows 长路径
                src_dp = disk_path(file_path)
                dst_dp = disk_path(target_file)
                if not dst_dp.exists():
                    shutil.move(str(src_dp), str(dst_dp))
                    moved_count += 1
                    created_folders.append(str(target_folder))
                elif dst_dp.resolve() != src_dp.resolve():
                    bak_target = disk_path(target_folder / f"{file_path.stem}_bak{file_path.suffix}")
                    shutil.move(str(src_dp), str(bak_target))
                    moved_count += 1

    if verbose:
        print(f"\n规整完成: 新建项目文件夹并归档文件数 = {moved_count}")
    return dict(moved=moved_count, folders=created_folders)


if __name__ == '__main__':
    organize_independent_documents()
