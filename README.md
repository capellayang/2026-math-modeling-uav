# 山区洪涝灾害下无人机运输与通信协同优化

当前完成第一阶段数据审计与第二阶段公共数据、DEM几何和飞行时间基础模块。Q1–Q4优化器尚未实现。原始题目与附件不改写；项目路径由代码位置推导，没有固定盘符。

## 运行环境

**统一使用现有Miniconda `dl` 环境（Python 3.10.20）**。在已经初始化conda的终端：

```powershell
conda activate dl
python --version
python -m pip install -r requirements.txt
```

本机PowerShell未运行 `conda init` 时，先加载Miniconda的hook：

```powershell
. 'C:/Users/YYF/miniconda3/shell/condabin/conda-hook.ps1'
conda activate dl
python --version
```

这一步只用于让当前PowerShell识别 `conda activate`；项目源码和数据路径仍全部相对项目根目录。**不要创建 `.venv`**。`requirements.txt`包含本阶段业务模块、第一阶段审计脚本及测试所需依赖。已有审计JS文件用于生成历史审计工作簿，新的正式业务模块全为Python。

## 当前模块

| 路径 | 已实现内容 |
|---|---|
| `src/relief_uav/data/` | 严格表头与分块读取；节点、53条需求、80箱、三型运输机、8架实体机、14组电池、2架中继及6组组件、通信参数的不可变数据对象；外键与需求对账；百分数转为0–1 SOC |
| `src/relief_uav/geo/dem.py` | 以GeoTIFF读取EPSG:4326 DEM；按附件识别−32767 NoData（即使TIF元数据为None）；像元中心、边界、点位与航段覆盖；WGS84椭球水平距离 |
| `src/relief_uav/geo/segments.py` | O01及S001–S015全部16×16有向航段的DEM最高值、距离、作业/巡航海拔与爬升下降；带源指纹的JSON缓存 |
| `src/relief_uav/physics/flight.py` | 原题等效航程、爬升/巡航/下降飞行时间；固定准备+逐箱装载、基础交接+逐箱交接时间 |
| `src/relief_uav/physics/battery.py` | SOC核算与0–90% / 90–100%两阶段充满时间 |
| `src/relief_uav/physics/energy.py` | 水平及爬升能耗接口和明确的缺式异常；未提供计算公式 |

内部使用m、s、kg、m³、kWh；经纬度保留°，通信频率MHz，通信距离换算km时由后续通信模块处理。SOC用0–1比例，原表的20%读取为0.2。电池和组件原附件只有各型号库存数，**没有逐块编号**，数据模型保持库存对象而不伪造官方ID。

### 航段几何口径

题目规定两节点间水平直线。当前以原始EPSG:4326经纬坐标中的两端点连线定义航迹；仿射变换后求该线与栅格线的全部交点，并纳入各区间与交点触及的像元，包括恰好沿栅格边和经过角点的情况，不按稀疏点采样。水平长度由WGS84椭球反算，单位米；这项口径选择已写入缓存元数据。地理范围较小，但经纬坐标与椭球测地线并非严格同一曲线，后续论文应明确此方法口径；如对“直线”有更具体投影要求，应统一调整航迹和距离定义并重建缓存。

巡航海拔=相交有效像元最高高程+50 m。O01起终作业海拔是**附件节点表**中的127.7 m；服务区作业海拔是附件海拔+30 m，不用所在DEM像元覆盖附件值。每一有向段单独计算。矩阵含16个自身到自身记录以保持16×16结构；这些对角记录不是实际运输航段。

缓存路径：`outputs/cache/node_pair_geometry.json`。内容带DEM文件哈希、节点数据和算法版本指纹，源变化自动重算，写入使用临时文件替换。强制重算参数见下方命令。缓存里的距离/DEM最高值在反向航段相同，爬升与下降依起终节点互换。

## 运行和核验

在项目根目录且激活 `dl` 后：

```powershell
python -m pytest tests -q --basetemp outputs/test_tmp
python scripts/build_base_physics_check.py --force-recompute
python scripts/build_base_physics_check.py
```

第一次生成256条有向航段与Excel核验表，第二次复用缓存。结果在 `outputs/validation/base_physics_check.xlsx`，包含O01→S001→O01和O01→S001→S005→O01，逐航段列出距离、DEM最高值、巡航及作业海拔、爬升/下降、相交像元数和A/B/C机型的三阶段时间。不会填造能耗或返航SOC。

测试覆盖真实场景对象数量及对账、起终方向、多点段、真实缓存强制重建与复用、人工构造DEM的尖峰/NoData/边界、像元覆盖与独立矩形裁剪结果比对、等效航程端点、飞行三阶段、准备/交接时间、SOC=0/0.5/0.9/1充电边界和连续性。`--basetemp outputs/test_tmp`是为了避开本机系统临时目录对pytest的访问权限限制；仅测试运行时使用。

## 第一阶段资料与后续边界

见 [题目与数据审计](docs/题目与数据审计.md) 和 [字段与模板字典](docs/输入字段与模板.md)。审计证据和汇总在 `outputs/data_audit/`，原始提交模板没有修改。

原题给出 `E=E_hor+E_up`，但未给出两个分项的具体关系式。`energy.py`故意抛出 `MissingOfficialFormulaError`，由此无法计算安全载荷、任务能耗、返航SOC或Q1–Q4优化结果。SOC/充电函数本身可以在给定实际能耗时工作。中继建链能耗归属与Q4共享中继跨组关系也仍需澄清。**不以经验公式填补空缺**。下一阶段应先得到可追溯的原题补充依据，再实现公共能耗与独立验证；目前未编写Q1优化器。

## 目录

```text
docs/                          题意和字段审计
src/relief_uav/data/           数据对象与读取
src/relief_uav/geo/            DEM、距离、航段矩阵和缓存
src/relief_uav/physics/        飞行、地面作业、SOC与能耗接口
src/relief_uav/q1...q4/       后续求解目录
src/relief_uav/validation/     后续独立验证目录
tests/                         已运行的基础单元/集成测试
scripts/                       审计脚本及基础物理核验表生成
outputs/cache/                 可重建几何缓存
outputs/validation/            人工核验表
```
