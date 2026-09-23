# 山区洪涝无人机运输与通信协同优化

当前仅完成第一阶段“题目与数据审计”，未实现Q1–Q4优化求解。

先读 [题目与数据审计](docs/题目与数据审计.md)；逐字段、单位、规模与官方提交字段见 [输入字段与模板](docs/输入字段与模板.md)。审计汇总位于 `outputs/data_audit/data_summary.xlsx`，机器可读字典为 `data_schema.json`，检查记录为 `data_validation.txt`。原始题目和附件保持原样。

## 当前关键结论

80箱、758 kg、2.011 m³；15服务区；运输8架和电池14组；中继2架和组件6组。原题中的Office Math已检查，航程指数、求和和两阶段充电公式已恢复。**现有题目未给出水平运输能耗、爬升附加能耗的具体关系式，禁止自行补式。** 正式公共能耗模块需等待原题补充或明确依据。时间定义、中继建链能耗和Q4共享中继的继承关系也须先明确。

## 结构

```text
docs/                 审计报告、字段字典
scripts/              仅审计、对账、审计工作簿生成脚本
configs/              待确认模型配置（未设任意权重）
src/relief_uav/
  data/ geo/ physics/ scheduling/ communication/
  q1/ q2/ q3/ q4/ validation/ export/  待开发目录
tests/                后续人工可核对案例
outputs/
  data_audit/          本阶段证据与汇总
  cache/ q1/ q2/ q3/ q4/ figures/ validation/ logs/
数据/                 原始附件，不改写
```

## 复现审计

Python 3.12，建议单独虚拟环境：

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python scripts/audit_tables_geo.py
.venv/Scripts/python scripts/finalize_audit.py
```

`audit_tables_geo.py`会先运行原文/工作簿提取，再扫描DEM、CSV和MAT并对账；当前仅更新审计输出，不调用求解器。脚本从自身路径确定根目录，无固定盘符。首次审计的额外地理库安装在本工作区 `.audit_deps`，脚本兼容该目录；普通复现用虚拟环境即可。

Excel审计汇总由 `scripts/build_audit_workbook.mjs` 使用Codex捆绑的 `@oai/artifact-tool`生成。该JS依赖不是本项目Python依赖；在Codex中由依赖加载工具定位Node与node_modules，并在scripts建立链接后运行。已有汇总工作簿可直接查看，重新生成Python JSON并不会自动刷新xlsx。首次环境验证过导出与预览；原模板未修改。

DOCX分页渲染因环境缺少捆绑LibreOffice未完成，改用完整OOXML与内嵌图像检查公式；不声称视觉核对了全部原题页面。PDF单页已渲染核查。地理MAT分传统DEM格式与v7.3 table对象格式，需scipy及h5py分别读取；标准表格只读使用openpyxl。

当前没有 `main.py`，也不提供尚未实现的Q1/Q2/Q3/Q4运行命令。建议开发顺序：缺式及口径确认→加载/地理→公共物理→Q1→Q2→通信→Q3→固定Q3的Q4→独立验证与导出。详细变量、硬约束和目标见审计报告。

## 已知风险与排查

TIF无NoData元数据而MAT/PDF指定−32767；节点附件海拔与像元高程不应互相覆盖。Excel一个sheet可能多表块、B:C合并表头、格式化空白范围不等于数据量。跳过Office `~$`锁文件。所有数值内算精度与最终显示精度分离。原题未明确的工位容量、目标权重、通信并发上限不得静默增加。

优化器、独立validator、航段缓存、通信连续性检查、结果导出和论文图均为后续工作；本次审计检查通过不代表任何优化方案已可行。
