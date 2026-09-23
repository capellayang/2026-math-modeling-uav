# 山区洪涝灾害下无人机运输与通信协同优化

当前完成第一阶段数据审计、第二阶段公共基础模块及第三阶段Q1单点往返优化。Q2–Q4尚未实现。原始题目与附件不改写；项目路径由代码位置推导，没有固定盘符。

## 运行环境

**统一使用现有Miniconda `dl` 环境**：

```powershell
conda activate dl
python --version
python -m pip install -r requirements.txt
```

PowerShell无法识别 `conda activate` 时，先初始化PowerShell，再重新打开终端：

```powershell
conda init powershell
# 重新打开PowerShell后：
conda activate dl
python --version
```

**不要创建 `.venv`**。`requirements.txt`包含业务模块、审计脚本、图表及测试所需依赖。已有审计JS文件只用于第一阶段历史审计工作簿，正式业务模块全为Python。

## 当前模块

| 路径 | 已实现内容 |
|---|---|
| `src/relief_uav/data/` | 严格表头与分块读取；节点、53条需求、80箱、三型运输机、8架实体机、14组电池、2架中继及6组组件、通信参数的不可变数据对象；外键与需求对账；百分数转为0–1 SOC |
| `src/relief_uav/geo/dem.py` | 以GeoTIFF读取EPSG:4326 DEM；按附件识别−32767 NoData（即使TIF元数据为None）；像元中心、边界、点位与航段覆盖；WGS84椭球水平距离 |
| `src/relief_uav/geo/segments.py` | O01及S001–S015全部16×16有向航段的DEM最高值、距离、作业/巡航海拔与爬升下降；带源指纹的JSON缓存 |
| `src/relief_uav/physics/flight.py` | 原题等效航程、爬升/巡航/下降飞行时间；固定准备+逐箱装载、基础交接+逐箱交接时间 |
| `src/relief_uav/physics/battery.py` | SOC核算与0–90% / 90–100%两阶段充满时间 |
| `src/relief_uav/physics/energy.py` | 可替换的项目假设能耗模型；水平与爬升分项、单段总能耗 |
| `src/relief_uav/physics/sortie.py` | 多点架次逐段载荷、能耗、SOC和作业时间统一评估 |
| `src/relief_uav/q1/` | 45格最大连续安全载荷、真实货箱子集枚举、精确bitmask DP及报告 |
| `src/relief_uav/validation/q1.py` | 独立重算货箱覆盖、容量、能耗、SOC、时间与总指标 |

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

测试覆盖真实场景对象数量及对账、起终方向、多点段、真实缓存强制重建与复用、人工构造DEM的尖峰/NoData/边界、像元覆盖与独立矩形裁剪结果比对、等效航程端点、飞行三阶段、准备/交接时间、SOC=0/0.5/0.9/1充电边界和连续性。`--basetemp outputs/test_tmp`是为了避开本机系统临时目录对pytest的访问权限限制；仅测试运行时使用。该目录已加入`.gitignore`。

## Q1数学模型与规则来源

**① 原题明确规则：** 单点架次严格为 `O01→Si→O01`，每箱不可拆、恰好交付一次且不能跨服务区；各架次可选不同机型，质量、体积、返航电量必须满足附件限制。等效航程 `L(q)=L0−(L0−LF)(q/Q)^(3/2)`；单段能耗为水平与爬升附加能耗之和、下降不单列能耗；架次总能耗不超过 `(1−ρ)Euse`。O01作业海拔取节点附件值，服务区取附件海拔+30m，巡航海拔取沿段DEM最高值+50m。每段按爬升、巡航、下降分别算时。

**② 由附件字段直接整理的时间：** 准备秒数=固定准备+每箱装载×箱数；交接秒数=基础交接+每箱增加交接×本站箱数。Q1累计作业时间为所有架次的准备、去程飞行、交接、回程飞行时间之和，不是并行调度的makespan。不计Q1未涉及的充电、等待或实体无人机周转。输出官方模板中的“往返时间（s）”映射为**去程飞行+回程飞行**，完整作业时间见`q1_plan.xlsx`。这是本项目对模板列名的映射口径。

**③ 经用户授权采用的项目建模假设，不是原题给出的分项公式：**

```text
E_hor = E_use × d / L(q)
E_up  = (m_empty + q) × 9.80665 × h_up / (eta_up × 3,600,000)
E_down = 0
```

`E_hor`、`E_up`的单位是kWh；`m_empty`取附件“含电池空载总质量”，q是该航段未投送货物的质量。公式只封装在`physics/energy.py`的`RangeGravityEnergyModel`中，公共架次评估器允许注入其他模型。若得到正式勘误，可替换该策略并重新计算全部结果。当前Q1数值以这组项目假设为条件。

## Q1求解和多目标

对每个服务区单独枚举非空货箱子集，按A/B/C机型检查结构重量、体积，并对实际组合调用完整架次评估器检查返航SOC。卸货前去程载荷为该组全部货箱质量，回程载荷为0。对同一个货箱子集只保留能耗和时间字典序最优的可行机型；其余机型没有实体资源约束，不能改进该子集的Q1字典序目标。然后使用锚定未覆盖货箱的bitmask动态规划，精确找出该服务区的最优不重叠分区。15区彼此独立，其最优分区合并即全局Q1最优分区。

本项目选择的默认目标为 `min(总架次数, 总运输能耗kWh, 累计作业时间s)`，按字典序依次比较。**原题要求说明权衡，但没有指定目标权重或优先级**，此优先顺序是项目选择。接口保留`objective_mode`，当前只实现`lexicographic`；`weighted`和`pareto`尚未实现，指定时明确报错，不会暗中退化为默认模式。最大连续安全载荷在0到结构载荷间二分，能量受限时返回可行下界，区间宽度小于0.0001kg；实际组批仍按真实箱组重新计算能耗。

## Q1运行、输出与验证

在项目根目录并激活`dl`后运行：

```powershell
python -m pytest tests -q --basetemp outputs/test_tmp
python main.py --question q1 --force-recompute
python main.py --validate q1
```

省略`--force-recompute`可复用`outputs/cache/node_pair_geometry.json`。主命令同时重新计算返航安全余量10%、15%、20%、25%、30%的45格安全载荷和组批；20%作为基准输出。无资源时间轴，因此Q1不处理实体机、共享电池竞争、充电、开始时刻、通信或配送时限。

| 文件 | 内容 |
|---|---|
| `outputs/q1/q1_safe_payload_matrix.xlsx` | 20%时45个机型×服务区连续安全载荷、几何、能耗、SOC和限制类型 |
| `outputs/q1/q1_plan.xlsx` | 基准货箱组批与逐架次去回能耗/飞行/地面作业明细 |
| `outputs/q1/q1_summary.json` | 基准总指标、逐区机型及完整计划，供CLI重新加载验证 |
| `outputs/q1/q1_sensitivity.xlsx` | 5档目标、225格安全载荷和每档实际组批 |
| `outputs/figures/q1_safe_payload_sensitivity.png` | 各机型各区安全载荷曲线 |
| `outputs/figures/q1_objective_sensitivity.png` | 三目标随安全余量变化 |
| `outputs/validation/q1_validation.xlsx/.txt` | 独立重算与违例报告 |
| `outputs/q1/结果提交_Q1.xlsx` | 复制官方模板，仅填`Q1_单点组批`，其他sheet保持原样 |

独立validator从原始箱ID重建每架次，不接受求解器保存的能耗/时间作为真实值；验证重复、遗漏、跨区、机型、载重、体积、返航SOC、时间和汇总指标。测试还故意注入这些故障，检查validator能识别。其覆盖箱数从加载的数据取得，没有硬编码80。

## 第一阶段资料与后续边界

见 [题目与数据审计](docs/题目与数据审计.md) 和 [字段与模板字典](docs/输入字段与模板.md)。审计证据和汇总在 `outputs/data_audit/`，原始提交模板没有修改。

原题未给出能耗分项公式，所以Q1结果以用户授权的项目建模假设为条件；与正式勘误不一致时须整体重算。中继建链能耗归属与Q4共享中继跨组关系仍需澄清。Q2–Q4优化器及相应通信/能源调度验证尚未实现。

## 目录

```text
docs/                          题意和字段审计
src/relief_uav/data/           数据对象与读取
src/relief_uav/geo/            DEM、距离、航段矩阵和缓存
src/relief_uav/physics/        飞行、能耗、统一架次评估、SOC
src/relief_uav/q1/             Q1精确求解与报告
src/relief_uav/q2...q4/       后续求解目录
src/relief_uav/validation/     Q1独立验证，后续扩展
tests/                         基础和Q1单元/集成测试
scripts/                       审计脚本及基础物理核验表生成
outputs/cache/                 可重建几何缓存
outputs/validation/            人工核验表
```
