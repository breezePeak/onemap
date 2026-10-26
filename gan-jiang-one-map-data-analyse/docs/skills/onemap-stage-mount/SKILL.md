---
name: onemap-stage-mount
description: 执行赣江一张图七阶段目录扫描、压缩包原地增量解压、Agent 业务语义智能研判项目大目录并存库、以及 M2 相同附件匹配与 M1 完整项目代码补漏及交叉校验的全流程技能。
---

# 赣江一张图 · 七阶段项目大目录智能识别 + M1 / M2 联合关联技能 (onemap-stage-mount)

## 一、技能定位与架构分工

本技能是供执行任务的 **Agent 自身**完成“OneMap 第一阶段（项目大目录智能识别 + M2 相同附件关联 + M1 完整项目代码补漏与校验）”的完整业务技能，**不依赖也不调用任何外部独立大模型服务或 API**。

核心协同范式：
**程序扫描并提供证据 → Agent 理解目录结构 → Agent 判断项目边界 → 程序校验及存库 → M2 相同附件匹配 → M1 代码补漏与交叉校验 → 统一关联结果与人工复核。**

### 1. Agent 职责（业务语义推理与决策）
- 理解七阶段目录树及完整的上下级父子关系。
- 区分独立项目目录（`project`）、多项目汇总/批次目录（`collection`）、年份/业务类别分类目录（`classification`）、项目内部材料目录（`material`）以及证据不足目录（`unresolved`）。
- 发现同一批次、同一交地文号或同一压缩包下的多个独立项目/宗地，不将整批材料归为一个项目，也不合并多个宗地。
- 在目录存在歧义时，自主决定按需调用工具读取代表性文件摘要；无足够证据时绝不猜测，标记为 `unresolved` 或提交人工复核。
- 分析 M1/M2 反向触发的同目录多代码冲突、同阶段多项目共用附件等边界疑点。

### 2. 程序职责（扫描、取证、校验与持久化）
- 扫描七阶段文件系统，执行压缩包原地增量解压（`<压缩包>.解压/`）与完整性校验，**严禁自动移动、删除或重组原始文件**。
- 提取目录树、父子层级、文件数量/类型、代表性文件名、材料内容摘要及项目代码线索。
- 严格校验 Agent 输出的目录路径与证据路径是否真实存在于磁盘，拒绝任何虚构路径或无依据判断。
- 维护 SQLite（`work/folder_mount/project_catalog.sqlite`）与 JSON（`work/folder_mount/project_catalog.json`）项目目录库，生成稳定目录 ID，记录 `structure_version` 与 `boundary_history`，无条件保护人工确认边界（`status == 'manual'`）。
- 计算有效材料完整 SHA-256（M2），提取并标准化完整项目代码（M1），执行跨阶段精确匹配与交叉校验。

### 3. 第一阶段职责边界与前端分工（重要）
- **第一阶段范围**：仅负责（1）Agent 智能识别七阶段项目大目录、（2）M2 相同附件关联、（3）M1 项目代码关联、补漏与交叉校验、（4）前端展示关联结果（`1 · 目录关联`）、（5）人工审核、处理和管理冲突（`2 · 冲突管理`）。
- **前端定位**：前端仅用于读取已由本 Skill 生成的关联结果并进行人工审核（确认关联、排除关联、标记复核、撤销决定、查看审核历史与预览原始证据），**不是数据生产平台，也不是 Agent 执行控制台**。
- **严禁行为**：禁止在前端触发后台扫描、自动生成项目业务数据、自动补全字段、自动填表、导出 Excel 成果、自动确认关联或自动合并宗地。

---

## 二、数据源与七阶段定义

默认数据根目录：`F:\项目数据\赣江一张图`（可通过 `--source` 或环境变量 `ONEMAP_SOURCE` 指定）。
仅扫描以下 S1~S7 真实业务阶段，自动忽略非业务目录（`数据图层`、`收储材料`、`计划供应`）：

| 阶段代码 | 阶段目录名 | 业务含义 | 常见目录形态 |
|---|---|---|---|
| S1 | `1.用地预审与选址` | 用地预审与选址 | 独立工程项目目录，或解压包内的多项目选址论证汇总 |
| S2 | `2.条件出具` | 规划条件和用地红线出具 | 直接项目目录，或 `规划条件盖章版/<年份>/` 下的地块项目 |
| S3 | `3.批次报批阶段` | 用地报批 | 批次项目、单独选址项目，或 `一码管地报批相关材料汇总提供` 等多项目汇编包 |
| S4 | `4.土地供应阶段` | 土地供应 | `出让/划拨` → 年度/整理层 → 单宗地项目，或网交地批复下的多个 `DX...` 独立宗地 |
| S5 | `5.土地首次登记` | 土地首次登记 | `土地首次登记附件` 汇总层下的 `360122...` 独立地籍宗地单元 |
| S6 | `6.用地规划` | 用地规划 | 以统一项目代码或工程名称命名的项目大目录，内含申请表/合同/四址等材料夹 |
| S7 | `7.工程规划` | 工程规划 | 以统一项目代码或工程名称命名的项目大目录，内含设计方案/申请表/证明文件等材料夹 |

---

## 三、Agent 语义判断标准（5 类节点定义）

Agent 必须结合上下级目录关系、文件名称、材料内容摘要与代码线索，将每个待研判目录归入以下 5 类之一：

1. **`project`（独立项目大目录）**
   - 对应单一建设项目、单一用地报批单元或单一供地/登记宗地。
   - 注意：项目目录名不一定包含“项目”二字（如道路工程名 `G105...改建工程`、地块名、交易编号 `DXJJ2018002`、地籍号 `360122...`、统一代码 `2017-360199-...`）。
   - 不能仅因为目录在第一级或最深一级就认定为项目，也不能仅因为不在排除规则中就认定为项目。
2. **`collection`（多项目汇总 / 批次 / 归档容器）**
   - 包含多个独立子项目或多个独立宗地的父级容器（例如：`一码管地报批相关材料汇总提供`、含多个 `DX...` 宗地子目录的 `赣国土资网交地【2018】XJ002号`、`土地首次登记附件`）。
   - 必须继续向下展开并识别每个独立子项目（`project`），严禁将汇总容器本身缓存为项目而吞并子项目。
3. **`classification`（业务分类 / 年份分类目录）**
   - 按业务类别、年份、档案整理状态划分的目录（例如：`出让`、`划拨`、`04出让档案整理`、`2020年度`、`土地变更协议`、`延期开工`、`没找到的`）。
4. **`material`（项目内部材料分类子目录）**
   - 隶属于某个具体项目内部的材料文件夹（例如：`PDF`、`CAD`、`矢量`、`报批材料`、`1项目红线与权属`、`4用地批复`、`01用地预审与选址意见书阶段`、`建设用地规划许可证申请表`、`宗地图`、`回复函`）。
   - 必须归属到其上级 `project` 大目录，绝不能单独注册为项目。
5. **`unresolved`（证据不足，暂无法确定）**
   - 目录名宽泛（如仅写 `绿地`、`8 延期开工 赣江集团`）且内部为空或缺乏能够指明具体项目/宗地的材料证据。
   - **铁律**：证据不足时不得猜测，必须标记为 `unresolved`（`needs_review: true`），保留为未归属状态供人工复核。

---

## 四、完整 10 步标准执行流程

Agent 执行本技能时，严格按照以下顺序推进（支持增量复用与断点续跑）：

### 步骤 1：发现及检查七阶段资料与已存库概览
运行 `summary` 查看当前项目目录库状态、已识别项目数、待复核项目与 `unresolved` 节点：
```powershell
$env:PYTHONPATH = "gan-jiang-one-map-data-analyse;gan-jiang-one-map-data-analyse/scripts;gan-jiang-one-map-data-analyse/work/python_packages"
$py = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $py gan-jiang-one-map-data-analyse/scripts/project_catalog.py summary
```

### 步骤 2：分层获取七阶段目录树结构（上下文控制）
先通过 `inspect-tree` 查看各阶段顶层及浅层目录结构（不要一次性把所有文件内容塞入上下文）：
```powershell
# 查看全部阶段浅层目录树（或加 --only-pending 仅看新目录、变更目录和存疑目录）
& $py gan-jiang-one-map-data-analyse/scripts/project_catalog.py inspect-tree --max-depth 2 --only-pending

# 对存在多项目汇总或深层结构的阶段/路径按需展开
& $py gan-jiang-one-map-data-analyse/scripts/project_catalog.py inspect-tree --stage S3 --path "一码管地报批相关材料汇总提供" --max-depth 2
& $py gan-jiang-one-map-data-analyse/scripts/project_catalog.py inspect-tree --stage S4 --path "出让" --max-depth 3
```

### 步骤 3：智能研判项目大目录与上下级关系
Agent 根据 `inspect-tree` 返回的 `child_dir_names`、`sample_files`、`detected_codes`、`db_decision`、`db_project` 及 `needs_agent_review` 标记，分析父子目录语义关系：
- 对已判断且 `structure_version` 未变化、`needs_agent_review == false` 的目录直接复用缓存；
- 对新目录、结构变化目录、父目录疑似多项目容器、或被标记为 `boundary_review` 的目录进行重点研判。

### 步骤 4：按需读取代表性材料摘要（深入取证）
当仅凭目录名和文件名无法确定边界（如编号目录、缩写目录、疑似多项目汇编或空壳目录）时，调用 `inspect-node` 并开启 `--read-files` 读取代表性文件前部摘要（PDF/DOCX/TXT 等）：
```powershell
& $py gan-jiang-one-map-data-analyse/scripts/project_catalog.py inspect-node --stage S4 --path "出让/04出让档案整理/土地变更协议/绿地" --read-files --max-files 5
```

### 步骤 5：校验并将项目边界及其证据持久化存库
Agent 将语义判断整理为结构化 JSON 文件（如 `gan-jiang-one-map-data-analyse/work/folder_mount/agent_decisions.json`），调用 `apply-decisions` 经程序严格校验后存入 SQLite 与 JSON 目录库：
```powershell
& $py gan-jiang-one-map-data-analyse/scripts/project_catalog.py apply-decisions --file gan-jiang-one-map-data-analyse/work/folder_mount/agent_decisions.json
```

#### Agent 决策输入 JSON 格式规范：
```json
{
  "decisions": [
    {
      "stage_id": "S3",
      "rel_path": "一码管地报批相关材料汇总提供",
      "node_type": "collection",
      "reason": "多项目报批材料汇总包，内含01至10共10个独立报批项目",
      "evidence_paths": [
        "一码管地报批相关材料汇总提供/01赣江新区2025年度第01批次集镇建设用地"
      ],
      "confidence": "high",
      "needs_review": false,
      "children": [
        {
          "stage_id": "S3",
          "rel_path": "一码管地报批相关材料汇总提供/01赣江新区2025年度第01批次集镇建设用地",
          "node_type": "project",
          "project_name": "01赣江新区2025年度第01批次集镇建设用地",
          "reason": "汇总包内第01号独立批次报批项目，含红线、公告、组卷、批复4类材料子目录",
          "evidence_paths": [
            "一码管地报批相关材料汇总提供/01赣江新区2025年度第01批次集镇建设用地/1项目红线与权属"
          ],
          "project_codes": [],
          "confidence": "high",
          "needs_review": false,
          "children": [
            {
              "stage_id": "S3",
              "rel_path": "一码管地报批相关材料汇总提供/01赣江新区2025年度第01批次集镇建设用地/1项目红线与权属",
              "node_type": "material",
              "parent_project_root": "一码管地报批相关材料汇总提供/01赣江新区2025年度第01批次集镇建设用地",
              "reason": "项目内部的红线与权属材料子目录",
              "evidence_paths": [
                "一码管地报批相关材料汇总提供/01赣江新区2025年度第01批次集镇建设用地/1项目红线与权属"
              ]
            }
          ]
        }
      ]
    }
  ]
}
```
- **校验保护规则**：
  - `stage_id`、`rel_path`、`node_type`、`reason` 必填；
  - `rel_path` 与 `evidence_paths` 中的每一个路径必须在磁盘（或对应 `.解压` 目录）真实存在，否则程序抛出 `ValueError` 拒绝入库；
  - 若某目录已处于人工确认状态（`status == 'manual'`），程序自动保护并跳过覆盖；
  - 若父目录被判定为 `collection` 或其下新增了 `project` 子项目，程序自动解除旧数据库中对该父目录的错误单项目锁定，并记录到 `boundary_history`。
  - 如需清理磁盘上已不存在的非人工确认历史遗留记录，可执行：`& $py gan-jiang-one-map-data-analyse/scripts/project_catalog.py prune-stale`。

### 步骤 6 & 步骤 7 & 步骤 8：按“M2 相同附件匹配 → M1 代码补漏及交叉校验 → 统一目录关系”顺序执行关联
项目大目录边界确定后，执行 `run_stage_mount.py`：
```powershell
# 推荐：M2 + M1 联合执行（内部严格按：增量解压与目录归属 → M2 SHA-256 精确匹配 → M1 项目代码提取、补漏与交叉校验 → 统一合并关系）
& $py gan-jiang-one-map-data-analyse/scripts/run_stage_mount.py --methods M1 M2 --source "F:\项目数据\赣江一张图"

# 也可单独运行 M2 或单独运行 M1（单独运行 M1 不新增计算 M2 附件哈希）
& $py gan-jiang-one-map-data-analyse/scripts/run_stage_mount.py --methods M2
& $py gan-jiang-one-map-data-analyse/scripts/run_stage_mount.py --methods M1
```
- **M2 执行规则**：
  - 支持七阶段任意两阶段（全部 21 组阶段对）跨阶段比较；
  - 以有效文件完整 SHA-256 完全一致作为证据；
  - 关联关系挂接在双方项目大目录（`project`），证据明细保留实际命中的内部材料小目录（`left_small_dirs` / `right_small_dirs`）与文件。
- **M1 执行规则**：
  - 检查全部项目大目录，从目录名、子目录名、文件名及原生文档正文（PDF/DOCX/XLSX/CSV/JSON，及可选按需 OCR）分级（A/B/C）提取完整标准化项目代码；
  - **补漏**：补充 M2 未发现的跨阶段项目大目录关系；
  - **交叉校验**：对已由 M2 关联的目录追加 `m1_validation`（`corroborated` 双方代码一致印证、`conflict` 双方可靠代码无交集冲突、`missing_left/right/both` 缺码、`unresolved_ownership` 多代码归属未明）；
  - **多宗地保护**：同一项目代码对应同阶段多个宗地目录时，保留多条独立候选关系并标记 `ambiguous`，绝不自动合并宗地。
- **统一目录关系**：
  - 同一对项目大目录被 M1 和 M2 同时命中时，以稳定目录对 ID 归并为**唯一一条关系**（`evidence_type: "M1+M2"`，`methods: ["M1", "M2"]`），并在 `method_evidence` 与 `method_signatures` 中完整保留 M1、M2 各自原始证据与签名。

### 步骤 9：反向边界复核与冲突/不确定项目提交人工复核
- 当 M1 发现单一项目目录内含多个不同项目代码且无法确定局部归属，或同阶段多个目录共用同一项目代码，或 M2 发现同阶段多个项目共用相同附件时，程序自动调用 `catalog.flag_boundary_review()` 将相关目录标记为 `boundary_review = True`（`status = 'review'`）。
- 当双方代码冲突（`conflict`）或目录边界/审核上下文发生变化时，关系自动进入 `status = 'review'`（需人工复核），绝不自动确认。
- Agent 可再次运行 `project_catalog.py summary` 查看新增的 `review_projects` 和 `unresolved_directories`，必要时回溯步骤 4~5 进一步细分目录边界，或保留供人工在工作台复核。

### 步骤 10：保存结果与断点续跑
所有中间与最终产物自动持久化落盘，支持随时中断后继续执行：
- **项目大目录与 Agent 决策库**：`work/folder_mount/project_catalog.sqlite`（含 `projects`、`directory_decisions`、`boundary_history`、`project_codes`、`relation_snapshots` 表）及镜像 `work/folder_mount/project_catalog.json`
- **Agent 语义判断文件**：`work/folder_mount/agent_decisions.json`
- **原地解压索引**：`解压材料/解压记录.sqlite`
- **M2 附件哈希缓存**：`work/folder_mount/hashes.json`
- **统一关联结果与各方法快照**：`work/folder_mount/result.json`、`result-M1.json`、`result-M2.json`
- **人工审核记录**：`work/folder_mount/decisions.json`

---

## 五、全量验收与回归测试命令

1. **运行单元测试回归**：
```powershell
$env:PYTHONPATH = "gan-jiang-one-map-data-analyse;gan-jiang-one-map-data-analyse/scripts;gan-jiang-one-map-data-analyse/work/python_packages"
$py = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $py gan-jiang-one-map-data-analyse/scripts/test_folder_mount.py -v
& $py gan-jiang-one-map-data-analyse/scripts/test_joint_mount.py -v
& $py gan-jiang-one-map-data-analyse/scripts/test_project_codes.py -v
& $py gan-jiang-one-map-data-analyse/scripts/test_m1_recognition.py -v
```

2. **运行七阶段副本全量验收**：
```powershell
& $py gan-jiang-one-map-data-analyse/scripts/verify_joint_mount.py --source "完整副本目录" --output "工作区验收输出目录"
```
验收脚本在确保不移动、不修改任何原始文件的前提下，顺序校验 M2、M1、M1+M2 联合结果及全部 21 组阶段对统计，并输出 `acceptance.json`。
