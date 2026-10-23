---
name: onemap-stage-mount
description: 执行赣江一张图全部 7 个阶段的数据目录扫描、压缩包原地增量解压、大模型研判业务项目大目录并存库、以及 M2 相同附件材料精确关联挂接至双方项目大目录的全流程技能。
---

# 赣江一张图 · 七阶段项目大目录识别与 M2 关联技能 (onemap-stage-mount)

## 技能定位
本技能固化并自动化执行“OneMap 第一阶段”核心前 4 项任务：
1. **七阶段业务目录扫描**：严格纳入 S1~S7 真实业务阶段，排除非业务阶段（数据图层、收储材料、计划供应）；
2. **汇编层散落独立文书规整**：自动识别规划条件盖章版等汇编目录下非同一项目的独立散落文书，就地新建与文件名同名的项目文件夹并将文件归档，确保独立业务项目均拥有专属项目大目录；
3. **原地增量解压与校验**：压缩包在原目录下以 `<文件名>.解压/` 隔离解压并校验，复用未损坏成员，不污染工作区；
4. **大模型研判业务项目大目录并持久化存库**：依据阶段业务形态判断项目根节点层级，存库至 `work/folder_mount/project_catalog.json`；
5. **M2 相同附件精确关联挂接至项目大目录**：支持任意两个不同阶段跨阶段比较（21组），关系归属项目大目录，证据展示命中小目录。

## 数据源与阶段定义
默认数据根目录：`F:\项目数据\赣江一张图`（可通过环境变量 `ONEMAP_SOURCE` 自定义）。

| 阶段代码 | 阶段目录名 | 业务含义 |
|---|---|---|
| S1 | `1.用地预审与选址` | 用地预审与选址 |
| S2 | `2.条件出具` | 规划条件和用地红线出具 |
| S3 | `3.批次报批阶段` | 用地报批 |
| S4 | `4.土地供应阶段` | 土地供应 |
| S5 | `5.土地首次登记` | 土地首次登记 |
| S6 | `6.用地规划` | 用地规划 |
| S7 | `7.工程规划` | 工程规划 |

## 触发与执行方式

### 方式 1：Python 脚本一键执行
在项目根目录下运行：
```powershell
python gan-jiang-one-map-data-analyse/scripts/run_stage_mount.py
```

### 方式 2：服务端 API 触发
启动治理工作台服务后，前端调用：
`POST /api/folder-mount/scan` （参数：`{"methods": ["M2"]}`）

## 核心判定规则与产物
- **原地解压记录**：`gan-jiang-one-map-data-analyse/解压材料/解压记录.sqlite`
- **项目大目录仓储**：`gan-jiang-one-map-data-analyse/work/folder_mount/project_catalog.json`
- **关联与扫描结果**：`gan-jiang-one-map-data-analyse/work/folder_mount/result.json`
- **附件哈希缓存**：`gan-jiang-one-map-data-analyse/work/folder_mount/hashes.json`
