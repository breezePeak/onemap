"""工作台共用的数据源及七阶段范围定义。"""
import os
from pathlib import Path

_DEFAULT_SOURCE = r'F:\项目数据\赣江一张图'
SOURCE = Path(os.environ.get('ONEMAP_SOURCE', _DEFAULT_SOURCE)).resolve()

# 七个业务阶段定义：阶段代码、目录名
STAGES = (
    ('S1', '1.用地预审与选址'),
    ('S2', '2.条件出具'),
    ('S3', '3.批次报批阶段'),
    ('S4', '4.土地供应阶段'),
    ('S5', '5.土地首次登记'),
    ('S6', '6.用地规划'),
    ('S7', '7.工程规划'),
)

# 各阶段业务中文含义
STAGE_TITLES = {
    'S1': '用地预审与选址',
    'S2': '规划条件和用地红线出具',
    'S3': '用地报批',
    'S4': '土地供应',
    'S5': '土地首次登记',
    'S6': '用地规划',
    'S7': '工程规划',
}

# 阶段映射字典，便于快速查找
STAGE_DICT = {sid: dict(stage_id=sid, folder_name=folder, title=STAGE_TITLES[sid]) for sid, folder in STAGES}
STAGE_FOLDER_TO_ID = {folder: sid for sid, folder in STAGES}

# 非业务阶段目录，扫描时应严格跳过
EXCLUDED_DIRECTORIES = {'收储材料', '数据图层', '计划供应'}

def validate_source_stages(root=None):
    """验证数据源根目录及 7 个阶段子目录的存在性。"""
    root_path = Path(root or SOURCE)
    if not root_path.is_dir():
        raise FileNotFoundError(f'数据源根目录不存在或不可访问: {root_path}')
    missing = []
    for sid, folder in STAGES:
        stage_dir = root_path / folder
        if not stage_dir.is_dir():
            missing.append(f'{sid}({STAGE_TITLES[sid]}): {folder}')
    return dict(root=str(root_path), missing=missing, is_valid=len(missing) == 0)


