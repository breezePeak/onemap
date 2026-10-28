---
name: onemap-stage2-extract
description: 执行 OneMap 第三阶段业务字段识别、七阶段证据检索、跨阶段关联和人工审核。使用用户 test.xlsx 的69列模板，只填项目信息及 S1～S4；不负责第四阶段正式成果制表。
---

# OneMap 第三阶段：字段识别与关联

在仓库根目录执行，先读 `agent.md` 和 `gan-jiang-one-map-data-analyse/agent.md`。阶段一目录库是检索起点，不把目录或关联候选视为文书中所有值的归属证明。

## 不变约束

- `templates/test.xlsx` 是本次用户模板的原样副本。Sheet1 第2行 A～BQ 完整69列；第1行分组保持不变。第3行是示例，初始化时不导入。
- A～W 保存原始标识；目标范围 X～AC 项目信息、AD～AL 预审选址、AM～AT 条件红线、AU～BC 报批、BD～BQ 供应。不创建 S5～S7 业务字段。
- 检索 S1～S7，按原件文书类型决定目标分组；存放阶段不决定字段分组。
- 相同代码、同名、相同附件、相交范围不能单独确认同一宗地。保留一对多、多对一和多对多引用，不批量将共享文件里的值填给所有项目。
- 规则提取与 Agent 语义识别均附真实原文位置。语义判断由当前 Agent 完成，不要求外部大模型服务。
- 有效原值默认保留；空值自动补齐还要求已知不动产单元号唯一精确匹配、无冲突且原生文字规则经过验证。合同号或监管号可能跨宗地，单独命中仍保留候选。无证据保持空白。面积原值、标准化值、单位分开保存；编号保留字符串；日期用实际日期。
- 材料改变后旧审核留在历史中，当前值进入重新审核。不能把旧指纹下的决定套给新材料。

## D1：模板与项目清单

后端 `scripts/stage2.py`；独立数据库 `work/stage2/business.sqlite`。工作台“3 · 提取项目数据”点击“读取 test.xlsx 与第一阶段项目”，复用现有 `mount_result()` 校验后的目录关系。CLI 可初始化目录清单；目录关系同步由工作台执行。

```powershell
$py = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$env:PYTHONPATH = 'gan-jiang-one-map-data-analyse;gan-jiang-one-map-data-analyse/scripts;gan-jiang-one-map-data-analyse/work/python_packages'
& $py gan-jiang-one-map-data-analyse/scripts/stage2.py init --template gan-jiang-one-map-data-analyse/templates/test.xlsx
```

真实已有数据表须显式导入，不能将模板示例当真实项目：`stage2.py import --workbook <真实表.xlsx>`。保留全部有效原值与来源表行号，候选不会无确认覆盖原值。跨阶段目录各有稳定项目 ID，关系另存，不直接合并。

## D2：七阶段材料取证

提取数据时，S5“土地首次登记附件”项目目录名开头的完整28位编码是不动产单元号，例如 `360122009006GB00002W00000000-2009` 提取 `360122009006GB00002W00000000`。年份、横线、下划线及“无对应供地矢量”等后缀不属于编码；无后缀也可识别。该编号作为目录来源的关联检索线索，保留原目录路径，不直接覆盖原表 O 列，也不据此把目录内所有文书自动确认给同一宗地。已有有效单元号优先，冲突交由复核。

`stage2.py scan` 或工作台“检索七阶段材料”。复用已由第一阶段原地解压的七阶段资料；先完成第一阶段压缩包展开与完整性检查。程序遍历七阶段有效目录，读取 PDF、图像 OCR、DOCX、Office、Excel/伪 CSV 台账、文本和 DBF，保存 SHA-256、实际路径、页码/表行/对象、业务类型和解析版本。

扫描件复用 Windows OCR、现有 PDF 渲染器；解析失败和缺失转换器明确记入任务错误，不生成伪值。局部 `--limit` 是开发抽样，不能作为全量完成。不要清理或重组源文件。CAD/SHP 复用 `gis_geometry.cad_entities` 读取对象和几何，SHP 指纹包括 SHX/DBF/PRJ/CPG；坐标系不明不猜测。

## D3：按业务文书识别字段

先用现有分类和字段提取器。对于跨页条款、复杂表格、标签与值分列及 OCR 排序异常，Agent 必须查看原件，按项目/宗地记录判断。不要只从文件名推断合同正文，也不要把文件名中的“出让合同”覆盖原件实际“划拨决定书”。

`stage2.py records --query <名称或编号>` 查看真实业务记录；`stage2.py project --project <ID>` 查看候选。通过工作台来源链接核对原件。程序保留行、页、分段位置，单份多项目文书不得整份共享赋值。

Agent 可写 JSON 到 `work/`，通过 `stage2.py propose --payload <json路径>` 提交语义候选：

```json
{"project":"真实项目ID","record":"真实记录ID","field":"AQ","raw":"真实原文片段","value":"原文编号","business_stage":"规划条件与红线","reason":"说明该编号是什么、项目或宗地归属的依据及尚存歧义"}
```

程序检查真实记录和当前原件指纹、字段范围、原文片段是否出现、基本类型与业务分组。该校验只能证明片段存在，不能证明语义判断正确；候选仍需审核。不要用未查看的片段构造候选，不填“无”、0或猜测。`propose` 可显式标明跨页文书业务阶段，但必须说明上下文证据。

## D4：关系与复核

M1 完整项目代码、M2 相同附件/业务编号沿用第二阶段与现有识别模块；M4 名称/位置相似仅扩大候选。新工作台“计算 M3 空间关联候选”复用 `projected_crs/reproject` 计算真实交集，返回原件版本、图斑对象、双方覆盖率和坐标系。M5 使用原件与保留的原有空间核对工具进行人工判读，填写依据后关联记录。不能把相交面积当整宗地面积，也不能把相交升级为全部权属一致。

确需关联未归属记录时，用工作台“全部材料业务记录与未归属证据”，选择目标项目，查看原件并填归属依据。`attach` 只建立可审核关系，字段仍逐项采用。允许同记录引用到多个项目，每一次都要独立核对值的适用范围。

## D5：审核与预览

字段支持采用、排除候选、保留原值、标记复核、撤销；所有动作保留历史。采用前必须明确确认项目与宗地归属。CLI `review --payload` 与 HTTP `/api/stage2/review` 使用同一校验。示例动作字段：`project,field,action,candidate,confirm_identity,note`，`action` 为 `adopt/exclude/keep/review/undo`。

`stage2.py preview` 或工作台“下载69列预览”。预览不包含审计附加列；来源保存 `preview-audit.json`。这是第三阶段检查产物，第四阶段再处理正式成果表格式、批量输出及验收。

## 开发与验收

- 修改后同步 `docs/第二阶段开发记录_2026-10-26.md`、相关设计、真实验证报告和本技能。
- `python -m unittest discover -s gan-jiang-one-map-data-analyse/scripts -p 'test_*.py' -v`；设置上面的 PYTHONPATH。另运行 Node 前端检查。
- `scripts/validate_stage2_real.py` 进行真实七阶段抽样，输出 `docs/stage2-real-validation.json`；覆盖度、失败材料、原生文字/OCR质量和待人工确认项必须披露。该脚本不等于全量验收。
- 必查：S5合同→S4、S6条件→S2、S7代码不并宗地、S4选址→S1、多项目文书、多供地宗地、原值保护、真实来源、无额外列、重复/重启/更新、解析/归属/编号异常、第二阶段关联回归。
- 合成测试通过不能替代真实材料成功；没有完成的真实场景明确标为待验，不宣布整个第三阶段已验收。
