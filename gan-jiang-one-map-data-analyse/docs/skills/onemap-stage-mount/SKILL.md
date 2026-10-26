---
name: onemap-stage-mount
description: 执行赣江一张图七阶段目录扫描、压缩包原地增量解压、规则判定项目大目录及 M1 项目代码、M2 相同附件独立或联合目录候选关联。
---

# 赣江一张图 · 七阶段 M1 / M2 目录关联技能 (onemap-stage-mount)

## 技能定位
本技能固化并自动化执行“OneMap 第一阶段”核心前 4 项任务：
1. **七阶段业务目录扫描**：严格纳入 S1~S7 真实业务阶段，排除非业务阶段（数据图层、收储材料、计划供应）；
2. **汇编层散落独立文书规整**：自动识别规划条件盖章版等汇编目录下非同一项目的独立散落文书，就地新建与文件名同名的项目文件夹并将文件归档，确保独立业务项目均拥有专属项目大目录；
3. **原地增量解压与校验**：压缩包在原目录下以 `<文件名>.解压/` 隔离解压并校验，复用未损坏成员，不污染工作区；
4. **规则判定业务项目大目录并持久化存库**：依据阶段业务形态判断项目根节点层级，存库至 `work/folder_mount/project_catalog.json` 和 SQLite；当前执行过程不调用大模型；
5. **M2 相同附件精确关联挂接至项目大目录**：支持任意两个不同阶段跨阶段比较（21组），关系归属项目大目录，证据展示命中小目录。
6. **M1 项目代码候选关联**：复用统一代码正则，从项目目录、子目录、文件名、明确正文标签及结构化字段取证；完整代码相等才命中，保留冲突和一对多目录，不自动合并宗地。M1 与 M2 同一目录对合并展示并分别保留证据及签名。

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
# 只执行 M1，不新增 M2 材料哈希计算
python gan-jiang-one-map-data-analyse/scripts/run_stage_mount.py --methods M1
# 独立 M2（默认）
python gan-jiang-one-map-data-analyse/scripts/run_stage_mount.py --methods M2
# 联合执行，可用 --source 指定已具备七阶段子目录的数据源
python gan-jiang-one-map-data-analyse/scripts/run_stage_mount.py --methods M1 M2 --source "F:\项目数据\赣江一张图"
```

### 方式 2：服务端 API 触发
启动治理工作台服务后，前端调用：
`POST /api/folder-mount/scan` （参数：`{"methods": ["M2"]}`）

也支持 `{"methods":["M1"]}`、`{"methods":["M1","M2"]}`。前端方法选择与 API 参数一致。
扫描会按已有流程规整散落文书并原地解压，真实数据验证应使用工作区副本；只读核验源材料时不要直接启动该写入流程。

## 审核、缓存与异常

- 同一目录对用稳定目录 ID 关联，显示一次；M1/M2 证据可分别筛选查看，七阶段及全部21组阶段组合均可筛选。
- 所有自动结果均为候选或需复核；项目代码不是宗地主键，多项目代码不得按来源优先级静默覆盖。
- 保存人工决定及历史。新增方法、原有证据签名或目录边界/歧义上下文变化时，派生状态为需复核；保留原决定及各方法原签名，不自动套用确认。
- M1 材料缓存按逻辑来源、大小、mtime、ctime、解析版本复用；新增、修改和删除会更新索引。只复用版本完整匹配的原生 PDF 文字缓存，旧版缺少 ctime 的解析结果重新读取。未绑定源版本的旧 OCR/字段结果不作为精确证据，不启动强制 OCR。
- 可读正文：原生文字 PDF（逐文件45秒超时）、DOCX、XLSX/XLSM、UTF-8/GB18030 文本及 JSON 明确字段。扫描图片、旧 DOC/XLS、未知格式记录跳过，不推测代码。文本/DOCX XML 超20MB记录限制原因。
- Web 与独立脚本使用同一异常分类：已知损坏/加密成员记录并跳过，其他异常默认阻断，包括越界、链接、文件并发变化、磁盘不足、未验证材料冲突、工具缺失。旧 `tolerate_corrupted` 参数不能绕过安全阻断。
- `file_count` 为材料条目数，`materials` 为过滤空文件/旁文件/临时文件后的有效材料，`hashed_count` 为已取得哈希的材料数；`skip_statistics` 和阶段 `skipped` 明确记录差异。M1 单独执行可复用已有哈希，但不新增计算。
- 本轮界面使用最近一次成功运行的所选方法结果；各方法最近运行快照独立留存，不自动把未重扫的历史证据合入当前结果。

## 核心判定规则与产物
- **原地解压记录**：`gan-jiang-one-map-data-analyse/解压材料/解压记录.sqlite`
- **项目大目录仓储**：`gan-jiang-one-map-data-analyse/work/folder_mount/project_catalog.json`
- **关联与扫描结果**：`gan-jiang-one-map-data-analyse/work/folder_mount/result.json`
- **附件哈希缓存**：`gan-jiang-one-map-data-analyse/work/folder_mount/hashes.json`
- **项目代码及版本缓存**：`work/folder_mount/project_catalog.sqlite` 中 `project_codes`、`project_code_cache` 表；完整代码证据亦在本次 `result.json.project_code_index` 中。
- **各方法最近运行快照**：`work/folder_mount/result-M1.json`、`result-M2.json`（联合执行的快照包含双方方法证据，按 `method_evidence` 分开）。
- **人工审核历史**：`work/folder_mount/decisions.json`，保留规则、证据签名、方法签名、上下文和历史。
- **M1 统计**：`result.json.m1_statistics` 包含有代码目录数、不同代码、跨阶段候选、歧义、冲突、未识别、未关联、读取失败和正文跳过原因。
