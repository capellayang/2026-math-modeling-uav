# 山区洪涝灾害下无人机运输与通信协同优化

本仓库以原题 DOCX、装备与需求 Excel、30 m DEM 和官方结果模板为依据，研究山区救援物资的无人机运输、资源排程与通信中继保障。本文档描述**当前已实现的程序和已保存结果**；原题规则、项目建模假设和优化策略分别说明。输入字段及题面公式的审计证据见 [题目与数据审计](docs/题目与数据审计.md) 和 [输入字段与模板](docs/输入字段与模板.md)。

## 目录

- [项目简介](#intro)
- [当前完成状态](#status)
- [运行环境](#environment)
- [项目目录结构](#structure)
- [数据与统一建模约定](#data-model)
- [统一约束条件](#constraints)
- [规则来源与建模假设](#provenance)
- [Q1：单点往返组批](#q1)
- [Q2：多点运输调度](#q2)
- [Q3：通信与中继联合调度](#q3)
- [Q4：任务分区与资源配置](#q4)
- [测试与独立验证](#verification)
- [输出文件总览](#outputs)
- [模型局限性](#limitations)
- [完整复现流程](#reproduction)

<a id="intro"></a>
## 项目简介

题目为《山区洪涝灾害下无人机运输与通信协同优化》。标准场景包含 O01 调度中心、15 个服务区、80 个不可拆货箱、A/B/C 三种运输机型、8 架实体运输机、14 组共享运输电池、G01 通信网关、2 架中继机及 6 组中继能源组件。飞行高度依赖数字高程模型（DEM，Digital Elevation Model），通信链路也受地形遮挡影响。

```text
原题与附件审计 → 公共 DEM / 飞行 / 能耗 / SOC 模型
              → Q1 单点往返组批
              → Q2 多点运输与资源调度
              → Q3 通信约束下运输—中继联合调度
              → Q4 固定 Q3 任务下的分区及独立资源配置
```

<a id="status"></a>
## 当前完成状态

| 问题 | 状态 | 算法定位 | 已保存结果 |
|---|---|---|---|
| Q1 | 已完成 | 枚举可行箱组，bitmask 动态规划；在当前离散模型和字典序目标下精确 | 18 架次、59.130 kWh、累计作业 32776.016 s；validator PASS |
| Q2-v1 | 已完成，历史基准 | 多起点路线邻域搜索＋固定路线 CP-SAT 排程 | J1=25785.315，J2=10714.143 s，J3=90.689 kWh，J4=32；validator PASS |
| Q2-v2 | 已完成，正式 Q2 方案 | ALNS＋CP-SAT，J1/J2 Pareto 与 ε 约束 | J1=0，J2=7265.435 s，J3=78.480 kWh，J4=26；validator PASS、CP-SAT FEASIBLE |
| Q3 | 已完成，正式方案为 seed 20260927 | 有限中继点与运输路线搜索、联合 CP-SAT、独立通信验证 | J1=0，J2=6975.244 s，J3=80.090 kWh，J4=30；validator PASS、CP-SAT FEASIBLE |
| Q4 | 已实现，继承正式 Q3 | 固定 Q3 快照，原子组件集合分区完整枚举＋固定区间最大并发/最少着色 | strict K2：3 候选、规模 31、缺口 4；strict K3：1 候选、规模 35、缺口 8；validator PASS |

Q1 的“累计作业时间”是各架次时长之和，**不是** Q2/Q3 的并行调度最晚返航时间。Q2-v1、v2 和 Q3 也不是同一可行域，不宜把表中数值当作同一问题的全局最优排名。

<a id="environment"></a>
## 运行环境

项目使用已有的 Miniconda `dl` 环境，不创建 `.venv`。在仓库根目录运行：

```powershell
conda activate dl
python --version
python -m pip install -r requirements.txt
python -m pytest tests -q --basetemp outputs/test_tmp
```

`requirements.txt` 是 Python 依赖清单。`main.py` 从自身位置推导仓库根目录，命令不依赖本机固定盘符。`outputs/test_tmp/` 是 pytest 临时目录，不是模型结果。本文所有路径均相对于仓库根目录。

<a id="structure"></a>
## 项目目录结构

```text
docs/                          原题与输入字段审计
数据/                           原始 Excel、地理说明和 DEM 等附件
src/relief_uav/data/           原始表读取、单位转换和数据对象
src/relief_uav/geo/            GeoTIFF、距离、航段栅格分析与缓存
src/relief_uav/physics/        飞行、运输能耗、架次评估、SOC 与充电
src/relief_uav/communication/  三维轨迹、DEM LOS、双向链路和保障状态
src/relief_uav/q1/             Q1 组批、精确 DP 与报告
src/relief_uav/q2/             Q2 搜索、资源排程与报告
src/relief_uav/q3/             Q3 中继候选、联合排程与报告
src/relief_uav/q4/             Q3 继承、精确分区、资源着色、目标与报告
src/relief_uav/validation/     Q1–Q4 独立方案验证
tests/                         公式、边界和求解/验证行为测试
scripts/                       审计、基准比较、基础物理核验脚本
outputs/                       结果、图、日志与可重建缓存
main.py                        Q1–Q4 求解及已保存方案验证入口
```

<a id="data-model"></a>
## 数据与统一建模约定

数据加载器把原始 Excel 转为清晰的数据对象；后续算法不直接依赖单元格。当前场景有 16 个任务节点、53 条需求汇总、80 个货箱、3 种运输机型、8 架实体运输机、14 组电池、2 架中继机、6 组中继组件。逐字段含义见 `docs/输入字段与模板.md`。道路、水体、村镇与行政界用于解释或展示；题目未把它们规定为禁飞区。

| 原始输入（仓库相对位置） | 用途 |
|---|---|
| `数据/无人机应急物资运输基础数据/调度中心与服务区.xlsx`、`物资需求与配送时限.xlsx` | 节点、80 箱、优先系数、期望与首批截止 |
| 同目录的 `运输无人机数据.xlsx`、`中继无人机数据.xlsx`、`通信链路参数.xlsx` | 机型、实体和能源库存、飞行/作业参数及双向链路参数 |
| `数据/镇龙乡地理空间数据/` 下的 GeoTIFF、说明 PDF 和其他地理附件 | 航段 DEM、LOS；其他图层用于审计及展示 |
| 根目录 `结果提交模板.xlsx` | 六张官方结果 sheet 的字段与导出格式 |

| 量 | 内部单位或口径 |
|---|---|
| 水平/垂直距离、高程 | m；绝对高程 MSL 与悬停离地高度 AGL 分开 |
| 时间 | s，从任务计时零点起 |
| 质量、体积、能量 | kg、m³、kWh |
| 经纬度、通信频率 | °、MHz；FSPL 中三维距离换算为 km |
| SOC | 0–1；结果表标 `%` 时乘以 100 |

**地形和飞行几何。** 主要 DEM 计算源是 EPSG:4326 GeoTIFF；地理说明给出的 NoData 哨兵值为 −32767，程序也拒绝无效值。节点间航迹取经纬坐标中的两端点水平直线，栅格分析完整遍历线段触及的 DEM 像元，包括边界与角点，不用稀疏取样猜最高点。水平长度用 WGS84 椭球反算为米；经纬度直线与椭球测地线不是严格同一曲线，这是项目实现口径。16×16 个有向节点对缓存在 `outputs/cache/node_pair_geometry.json`，源指纹变化自动重建，`--force-recompute` 可强制重算。

普通运输航段 `i→j` 的原题高度规则为

$$
H_{ij}=\max_{c\cap\overline{ij}\ne\varnothing}\operatorname{DEM}(c)+50\ \mathrm{m},\qquad
h^+_{ij}=H_{ij}-z_i,\quad h^-_{ij}=H_{ij}-z_j.
$$

O01 作业海拔取附件 O01 高程；服务区作业海拔取附件该服务区高程再加 30 m，**不以节点所在 DEM 像元值替换附件海拔**。多点运输的每段独立计算 DEM、巡航海拔、爬升及下降；服务区交接后从其作业海拔重新起飞。时间为

$$
t_{gij}=\frac{h^+_{ij}}{v_g^\uparrow}
       +\frac{d_{ij}}{v_g^c}
       +\frac{h^-_{ij}}{v_g^\downarrow}.
$$

**航程、电池和作业时间。** 原题等效航程为

$$
L_g(q)=L_g^0-(L_g^0-L_g^F)\left(\frac{q}{Q_g}\right)^{3/2},
$$

其中 `q` 为当前航段剩余载荷；逐站卸货后必须更新。返航 SOC 按 `1-E_{\mathrm{task}}/E_{\mathrm{use}}` 核算，标准运输和中继下限均为 20%。返航 SOC 为 `s`，附件等效完全充电时间为 `T_{\mathrm{full}}` 时，两阶段充满时间为

$$
t_{\mathrm{chg}}(s)=
\begin{cases}
T_{\mathrm{full}}\!\left[0.65(0.90-s)/0.90+0.35\right],&0\le s<0.90,\\
T_{\mathrm{full}}\,0.35(1-s)/0.10,&0.90\le s\le1.
\end{cases}
$$

准备时间按“固定准备＋箱数×每箱装载时间”，交接时间按“基础交接＋本站箱数×每箱增量”；系数从附件读取。

<a id="constraints"></a>
## 统一约束条件

Q1 使用公共货箱与飞行约束；Q2 继承它们并增加时间资源，Q3 再继承 Q2 并增加中继和通信。

| 范围 | 硬约束 |
|---|---|
| Q1–Q3 货箱与飞行 | 货箱不可拆、每箱恰好交付一次、只在所属服务区交付；架次 O01 起飞并回 O01；载重、体积、逐段能耗与返航 SOC 均可行 |
| Q1 单点 | 一个架次只访问一个服务区，严格 `O01→Si→O01` |
| Q2–Q3 多点及时限 | 可串行访问多个服务区；医疗箱的期望时刻及首批标记箱的首批截止时刻是硬截止；其余期望时刻进入软及时性指标 |
| Q2–Q3 运输资源 | 实体机任务不重叠；同型号电池可跨实体共享、异型号不可混用；电池占用和充电互斥，复用前充至 100% |
| Q3 连续通信 | 运输机起飞至返航的爬升、巡航、下降及交接期间须有直连或有效单跳中继；中继须已建链且接入、回传均可用；最终检测 OUTAGE 时长为 0 |
| Q3 中继资源 | 悬停点在有效 DEM 范围，`0<AGL≤300 m`；满足能量和返航 SOC；实体任务互斥且返航后周转 300 s；组件占用、充电及复用时序合法 |

附件未给单个中继的并发用户上限，也未给充电桩数量；当前程序未自行添加这两类容量约束。一个中继服务区间可同时保障多架运输机。

<a id="provenance"></a>
## 规则来源与建模假设

| 类型 | 内容 |
|---|---|
| **原题明确规则** | 单点/多点路线、作业和巡航高度、等效航程、返航安全余量、两阶段充电、双向通信预算、DEM 遮挡附加损耗、中继悬停上限等 |
| **项目能耗假设** | 原题给出水平能耗与爬升附加能耗相加，但未给两项完整公式。下列具体关系经用户授权采用，**不是原题显式公式**；正式勘误若补充原式，Q1–Q3 应整体重算 |
| **项目时间/几何口径** | Q2/Q3“开始时刻”指准备开始，资源从此时占用；同站全部箱在整站交接结束时统一记为交付；运输机返航无需额外周转，电池可立即充电；Q3 中继高度和建链能耗另述 |
| **项目优化选择** | Q1 字典序；Q2-v2、Q3 的 J1/J2 主目标与 Pareto/ε、J3/J4 次级择优；ALNS 邻域及搜索预算。原题没有规定这些目标权重或优先级 |

当前运输能耗实现为

$$
E_{\mathrm{hor}}=E_{\mathrm{use}}\frac{d}{L_g(q)},\qquad
E_{\mathrm{up}}=
\frac{(m_{\mathrm{empty}}+q)\,g\,h^+}
{\eta_\uparrow\,3.6\times10^6},\qquad
E_{\mathrm{down}}=0,
$$

其中 `g=9.80665 m/s²`。下降仍计飞行时间；“下降附加能耗为 0”不表示瞬时下降。以上分项关系属于项目假设，不能在论文里写作题面已给公式。

<a id="q1"></a>
## Q1：单点往返组批

**任务要求与决策变量。** 对 15 个服务区分别安排不可拆货箱的批次，每批选 A/B/C 一种机型，每架次严格 `O01→Si→O01`。决策是“哪些箱同批”和“该批使用什么机型”。Q1 不决定实体机、电池编号、起飞时刻、充电、通信或配送时限排程。

**新增约束与优化目标。** 继承公共货箱、质量、体积、能耗和 SOC 约束，加上单区往返限制。项目按三元组**字典序**最小化：

$$
\min\bigl(N_{\mathrm{trip}},E_{\mathrm{transport}},T_{\mathrm{operation}}\bigr).
$$

先比架次数，持平再比运输能耗，再持平比各架次累计作业时间。这是项目目标选择；Q1 当前没有实现 `weighted` 或 `pareto` 模式。

**求解算法与流程。** 每个服务区枚举所有非空货箱子集及 A/B/C 机型；逐组调用完整架次评估器检查质量、体积、逐段能耗和 SOC；同一箱组只保留字典序最优机型。随后以尚未覆盖的首箱为锚点，做 bitmask 集合划分动态规划，再合并各区结果并由独立 validator 重算。在当前离散货箱、物理模型与目标下，这一 DP 是精确求解，不表示能耗假设是唯一正确的物理模型。

连续质量安全载荷矩阵通过二分求机型—服务区组合的结构/能量载荷上界，用于能力分析和预筛；真实组批**仍对每个箱组调用完整评估器**，体积单独校验，不能只凭“总质量≤安全载荷”判可行。

**运行方法与参数。**

```powershell
python main.py --question q1
python main.py --validate q1
```

Q1 只使用共用 `--force-recompute`（默认关闭），用来强制重建 DEM 航段缓存。主程序固定计算 10%、15%、20%、25%、30% 五档返航安全余量；20% 是 `q1_plan.xlsx` 与 `q1_summary.json` 的基准，档位目前**不是 CLI 参数**。基准结果为 18 架次、59.130 kWh、累计作业 32776.016 s，validator PASS。

**输出文件、作用和阅读方法。**

| 文件 | 内容、用途与输出原因 |
|---|---|
| `outputs/q1/q1_safe_payload_matrix.xlsx` | 45 个“机型×服务区”的最大连续安全载荷、结构上限、距离、DEM、能耗及返航 SOC；判断哪一组合受结构载荷或能量约束，**不是**最终箱组清单 |
| `outputs/q1/q1_plan.xlsx` | **正式可读组批清单**：每行一个往返架次，列出服务区、机型、货箱 ID、箱数、质量/体积、去返程能耗、准备/飞行/交接时间和返航 SOC；可直接核查哪些货箱同机送、成本和时长是多少 |
| `outputs/q1/q1_summary.json` | 可机器读取的 20% 基准快照、总指标、逐区统计、载荷矩阵及验证标记；供程序或论文表格读取，不依赖 Excel 单元格 |
| `outputs/q1/q1_sensitivity.xlsx` | `汇总`、`安全载荷`、`逐档组批` 三张表，分别记录五档余量的总架次/能耗/时间、载荷变化及箱组变化；核查提高安全余量是否触发载荷下降、重组批次或增加架次 |
| `outputs/figures/q1_safe_payload_sensitivity.png` | 横轴返航安全余量、纵轴最大安全载荷，分 A/B/C 机型画各服务区曲线；看哪些区域最先因能量约束失去载货能力，以及为何要做余量比较 |
| `outputs/figures/q1_objective_sensitivity.png` | 同一余量横轴下并列展示架次数、总能耗与累计作业时间；揭示安全余量与运输成本的权衡。曲线来自五档**重新求解**，不是简单改写已选方案 SOC |
| `outputs/validation/q1_validation.xlsx`、`q1_validation.txt` | 独立核验总体结论及违例明细：检查箱覆盖、跨区、机型、质量/体积、能量、SOC、时间和汇总一致性 |
| `outputs/q1/结果提交_Q1.xlsx` | 官方 `结果提交模板.xlsx` 的副本，只填 `Q1_单点组批`；安全载荷矩阵和敏感性分析不在官方 sheet 中，故另存上述文件 |

**验证方式与重点。** `python main.py --validate q1` 从保存的 JSON 和源箱重新构造批次并计算物理量。Q1 的“精确”仅指当前定义下的离散组批优化；五档敏感性文件用于判断结论对返航余量是否稳健。

<a id="q2"></a>
## Q2：多点运输调度

**任务要求与决策变量。** 允许一架次 `O01→Si→Sj→…→O01` 串行访问多个服务区，联合决定箱组、站点顺序、机型、实体无人机、同型共享电池和准备开始时刻。Q1 结果可作为种子，Q2 仍能重新组批，不固定 Q1 箱组。

**新增约束与优化指标。** 继承公共运输物理约束，新增实体机/电池的时间互斥、充满后复用和逐箱时限。医疗箱在期望时刻前、首批标记箱在首批截止时刻前交付；其他期望时刻是软目标。同站多箱统一在整站交接结束时计为交付。四项指标为

$$
J_1=\sum_b w_b\max(0,t_b^{\mathrm{delivery}}-d_b),\quad
J_2=\max_{r\in T}t_r^{\mathrm{return}},\quad
J_3=E_{\mathrm{transport}},\quad
J_4=N_{\mathrm{transport\ sorties}}.
$$

另报告无量纲归一化 J1：逐箱逾期除以其期望时刻，再以优先系数加权并除以系数之和；它只作展示，不替代 J1。Q2-v1 按 `(J1,J2,J3,J4)` 字典序；Q2-v2 将 J1/J2 作为双主目标，以 Pareto/ε 选择，J3/J4 次级择优，**不是**把四项不同量纲直接线性相加。这些优先结构为项目选择。

**求解算法与流程。** v1 是历史多起点路线邻域搜索＋CP-SAT 固定路线资源排程；v2 使用 ALNS（自适应大邻域搜索）对迟到箱、最晚返航机、充电链及邻近区域做破坏/修复搜索，并缓存重复架次评估。对较有希望的路线，CP-SAT 分配实体机、电池和开始时间，处理资源 NoOverlap、充电及硬截止；独立重建验证后形成 J1/J2 前沿与 ε 档结果。路线和组批空间很大，两版整体均不保证全局最优；固定候选的 CP-SAT 状态也不能推广成整个 Q2 的最优性证明。

CP-SAT 排程使用毫秒整数，阶段时长与交付偏移向上取整、截止时刻向下取整；最终输出和独立验证重建浮点秒时间轴。快速列表排程只是候选估值。Q2 可自行生成 Q1 种子，运行时不要求预先存在 `q1_summary.json`。

```text
原始箱与种子 → 组批、顺序、机型邻域搜索
→ 快速物理/排程估值 → 路线候选
→ CP-SAT 精排实体机、电池和时间 → 独立 validator
→ Pareto / ε 比较 → 选定 Q2-v2 方案
```

**运行方法与参数。** `--q2-algorithm` 默认 `v2`；只有显式指定 `v1` 才会运行历史算法并写 `outputs/q2/`。下列默认值来自 `main.py` 的 argparse 和分支处理：

```powershell
python main.py --question q2 --q2-algorithm v2
python main.py --validate q2 --q2-algorithm v2
```

历史 v1 如需重跑，使用 `python main.py --question q2 --q2-algorithm v1`；它会重写 `outputs/q2/`，正常 v2 运行只写 `outputs/q2_v2/`。

| 参数 | v2 默认值 | 作用 |
|---|---:|---|
| `--seed` | 20260923 | 随机种子 |
| `--q2-time-limit` | 300 s | 搜索与候选精排预算；验证/导出可增加总墙钟时间 |
| `--q2-iterations` | 3000 | 搜索迭代上限 |
| `--q2-restarts` | 8 | ALNS 重启次数 |
| `--q2-cp-candidates` | 20 | 进入 CP-SAT 精排的候选数上限 |
| `--q2-tardiness-slack` | 0.05 | 选解时 J1 相对容差；J1=0 且绝对容差=0 时不产生放宽 |
| `--q2-absolute-epsilon` | 0.0 | J1 绝对容差，补足零基准下相对 ε 无效的问题 |
| `--q2-selection` | `epsilon_makespan` | 允许 J1 范围内择较小 J2；另一选项 `ideal_distance` 使用归一化理想点距离 |
| `--q2-epsilon-levels` | `0,0.02,0.05,0.10` | 相对 ε 扫描档位 |
| `--q2-algorithm` | `v2` | `v1` 或 `v2`，决定算法和输出/验证目录 |
| `--force-recompute` | 关闭 | 强制重建公共 DEM 航段缓存 |

v1 的 `--q2-time-limit` 与 `--q2-iterations` 默认分别是 **60 s、400 次**；`--q2-objective` 默认 `lexicographic`，另支持 `weighted`，仅作用于 v1，后者是历史兼容模式而非 v2 正式目标。v2 不使用 `--q2-objective`。已保存 v2 方案 J1=0、归一化 J1=0、J2=7265.435 s、J3=78.480 kWh、J4=26，validator PASS，CP-SAT `FEASIBLE`。

**输出文件、作用和阅读方法。** 下表的 `q2_*` 基础文件分别位于历史 `outputs/q2/` 和正式 `outputs/q2_v2/`；应以 v2 目录的同名文件作为当前 Q2 结论。v1 的日志、图与验证在 `outputs/logs/`、`outputs/figures/`、`outputs/validation/`，v2 则集中在 `outputs/q2_v2/` 内。

| 文件（位于对应 Q2 结果目录） | 内容、用途与输出原因 |
|---|---|
| `q2_transport_sorties.xlsx` | 一行一架次的机型、实体机、电池、路线、逐站箱组、准备/起飞/返回、质量/体积、能耗及 SOC；复核调度与官方模板映射 |
| `q2_box_deliveries.xlsx` | 一行一箱的架次、服务区、交付完成、期望/首批截止和加权逾期；定位硬时限、软逾期和 J1 来源 |
| `q2_drone_timeline.xlsx` | `航段时间轴` 按段给载荷、DEM、高度、能耗和爬升/巡航/下降时刻；`交接时间轴` 给到站及交接时刻；追溯各箱交付时间和飞行物理 |
| `q2_battery_timeline.xlsx` | 内部电池 ID 的占用、返航 SOC、充电起止；人工核查同型共享、无重叠和复用前满电 |
| `q2_summary.json` | 完整运输路线、逐箱交付、资源时间轴与目标值的机器快照；v2 还存选解、Pareto、配置和搜索统计，供 Q3 或复核程序读取 |
| `q2_pareto.xlsx` | v1 含 CP 非支配候选及快速搜索档案；v2 含 `J1-J2 Pareto前沿`、`全部CP候选`、`ε扫描` 三表；检查双主目标权衡及最终选择，不等于穷尽全局路线 |
| `结果提交_Q2.xlsx` | 官方模板副本，仅填 `Q2_运输架次` 与 `Q2_逐箱交付`；“开始时刻”映射准备开始，其余 sheet 保持原样 |
| `validation/q2_validation.xlsx`、`q2_validation.txt` | 已保存方案的独立核验结论及违例，检查 80 箱、物理、时限、实体/电池、充电和汇总；v1 对应文件在 `outputs/validation/` |

v1 辅助文件：`outputs/logs/q2_search_history.csv` 记录邻域迭代和快速估值；`outputs/figures/q2_drone_gantt.png` 以横条展示实体机从准备到返航的占用；`q2_delivery_tardiness.png` 对比逐箱实际完成与期望时间；`q2_objective_history.png` 展示快速排程下搜索已知最好加权逾期，用于观察进展，**不是**最优性证明。

v2 额外文件和图：

| 文件（位于 `outputs/q2_v2/`） | 内容、用途与输出原因 |
|---|---|
| `baseline_v1/q2_summary.json` | 冻结的 v1 基准，避免本地重跑 v1 覆盖对比口径 |
| `q2_algorithm_comparison.xlsx` | v1、各 ε 档及 v2 最终方案的 J1–J4、运行时间、CP 状态及相对变化；量化方案改进与代价 |
| `q2_benchmark.json` | 从已保存 v1/v2 结果重新生成的机器可读对比，可由 `scripts/benchmark_q2_v2.py` 单独重建 |
| `logs/q2_search_history.csv` | 各重启/迭代的破坏、修复算子、接受原因、近似目标、温度及权重；复查 ALNS 行为 |
| `figures/q2_drone_gantt.png` | v2 实体机占用图；查看并行、空闲和完工瓶颈 |
| `figures/q2_delivery_tardiness.png` | v2 逐箱交付和期望时刻图；识别靠近或超过软目标的箱 |
| `figures/q2_timeliness_makespan_pareto.png` | 横轴归一化 J1、纵轴 J2，叠加 v1 与选定方案；展示“及时性—完工时间”权衡和选解位置 |
| `figures/q2_epsilon_sensitivity.png` | 相对 ε 档下的 J1、J2、能耗和架次数；检验容差变化能否换来更短完工时间。当前最佳 J1=0 且绝对 ε=0，曲线可能重合；这是相对 ε 在零基准下不起放宽作用的证据 |
| `figures/q2_operator_weights.png` | ALNS 破坏/修复算子的最终自适应权重；解释搜索偏向的邻域，不作因果效果证明 |
| `figures/q2_objective_history.png` | 快速排程的迭代最佳 J1；观察搜索是否停滞，不能代替最终独立核验 |

**验证方式与重点。** `python main.py --validate q2` 默认验证 v2 保存方案；验证历史 v1 需加 `--q2-algorithm v1`。validator 从源附件与保存决策重算，不直接相信求解器写出的能耗、时间和 SOC。`FEASIBLE` 仅表示相应 CP-SAT 候选排程找到可行解。

<a id="q3"></a>
## Q3：通信与中继联合调度

**任务要求与决策变量。** 在 Q2 全部运输约束上，要求运输机从起飞至返航的爬升、巡航、下降和交接全过程保持通信。优先走 `Transport↔G01` 直连；失败时可使用一架已完成建链的固定悬停中继，形成 `Transport↔Relay↔G01`，禁止中继—中继多跳。联合决策可包括运输箱组、路线、机型、实体机、电池、准备开始时刻，以及中继悬停经纬度、离地高度、服务区间、实体和能源组件。Q2-v2 保存解只是种子，**不是固定 Q3 运输方案**。

**新增约束、通信模型与目标。** 继承 Q2 运输约束，增加中继飞行/悬停/能量/资源、双向接入与回传，以及通信连续性硬约束。网关 G01 取 O01 经纬度，天线 MSL 海拔由附件 O01 高程＋附件网关离地高度计算。运输机端点随三维飞行阶段移动；中继服务期间固定悬停。DEM 视线（LOS，Line of Sight）分析检查通信线触及的像元。自由空间路径损耗（FSPL，Free-Space Path Loss）为

$$
L_{\mathrm{FSPL}}=32.45+20\log_{10}(f_{\mathrm{MHz}})
                       +20\log_{10}(D_{\mathrm{km}}).
$$

`D` 是三维距离。接收门限及双向允许损耗依附件发射功率、增益、系统损耗、灵敏度和衰落裕量计算：

$$
P_{\mathrm{th}}^b=P_{\mathrm{sens}}^b+M^b,\quad
L_{\max}^{a\to b}=P_t^a+G_t^a+G_r^b-L_{\mathrm{sys}}-P_{\mathrm{th}}^b,\quad
L_{\max}^{a\leftrightarrow b}=\min(L_{\max}^{a\to b},L_{\max}^{b\to a}).
$$

**地形遮挡不直接等于失联。** 遮挡时增加附件规定的 10 dB，再将 `L_{\mathrm{path}}=L_{\mathrm{FSPL}}+L_{\mathrm{obs}}b` 与双向允许损耗比较；链路裕量为 `L_{\max}-L_{\mathrm{path}}`。状态优先级依次为 `DIRECT`（直连可用）、`RELAY`（直连失败，但已建链中继接入与回传都可用）、`OUTAGE`（其余）。`T_{\mathrm{OUTAGE}}=0` 是最终方案**硬可行性条件**，不是新增的第五个优化目标。

Q3 沿用 Q2 的 J1，其他指标扩展为

$$
J_2=\max\!\left(\max_{r\in T}t_r^{\mathrm{return}},
                \max_{k\in R}t_k^{\mathrm{return}}\right),\qquad
J_3=E_T+E_R,\qquad J_4=N_T+N_R.
$$

J1/J2 主目标、J3/J4 次级择优及 Pareto/ε 选择是项目优化策略，不是题目规定的权重。

**中继物理与项目补充口径。** 悬停绝对海拔 `z_P=\operatorname{DEM}(P)+AGL`，要求 `0<AGL≤300 m`。中继往返巡航高度采用

$$
H_{\mathrm{relay}}=
\max\!\left(H_{\max,\mathrm{DEM}}+50,\ z_P,\ z_{O01}\right).
$$

这是处理高悬停点的**项目扩展几何约定**，并非原题显式公式。中继巡航能耗按附件巡航功率×巡航秒数/3600，服务能耗按“悬停功率＋通信附加功率”×服务秒数/3600；爬升附加能耗沿用 `mgh/\eta` 项目假设，下降附加能耗为 0。建链 30 s 默认按悬停＋通信功率计能（`hover_plus_comm`），也可选 `hover_only`；这是项目能耗口径。实体返航后周转 300 s 才能开始下一次准备；组件返航即可充电，能与实体周转并行。组件 `R-COMP-xx` 与电池 `A/B/C-BAT-xx` 都是按库存生成的**项目内部编号**，不是附件官方编号。当前联合 CP 为每个中继架次分配不同组件，属于搜索范围限制；独立验证器仍按组件占用、充电及复用规则检查。

**求解算法与流程。**

```text
读取已保存 Q2-v2 基准运输快照
→ 全部运输轨迹仅直连审计，提取失联区间
→ 从失联轨迹、服务区、中间位置及局部网格生成悬停候选
→ 筛查回传、接入、飞行/悬停能量及返航 SOC
→ 通信导向 ALNS 搜索箱组、路线和机型
→ 对有限候选用 CP-SAT 联合安排运输机/电池/开始时刻、
  中继实体/组件/位置与服务区间
→ 独立读取附件和 DEM，细粒度核验全部运输、中继与通信约束
```

ALNS 负责发现有希望的运输组织，关注失联较长、链路裕量低和中继共享困难的路线；CP-SAT 负责有限候选下的资源与时间排程。搜索估值可近似，最终 validator 不把优化器缓存当作链路真值。

**运行方法与参数。**

```powershell
python main.py --question q3 --seed 20260927
python main.py --validate q3
```

| 参数 | 默认值 | 作用 |
|---|---:|---|
| `--seed` | 20260923 | 搜索随机种子 |
| `--q3-time-limit` | 600 s | 搜索/候选精排预算；最终独立验证会使实际墙钟时间更长 |
| `--q3-iterations` | 2000 | ALNS 迭代上限 |
| `--q3-restarts` | 6 | ALNS 重启次数 |
| `--q3-search-step` | 1.0 s | 候选运输方案的直连审计实际采用 `max(2 s, 此值)`；同时决定中继覆盖区间保护量 `max(5 s, 2×此值)`。Q2 基准仅直连审计固定用 2 s |
| `--q3-validation-step` | 0.25 s | 最终独立通信核验最大时间步，也用于 `--validate q3` |
| `--q3-transition-tolerance` | 0.05 s | 状态切换二分定位精度 |
| `--q3-hover-grid-m` | 600 m | 悬停候选粗网格尺度 |
| `--q3-hover-altitudes` | `50,100,150,200,250,300` m | 接收逗号分隔高度列表；**当前粗筛仅使用列表最大 AGL**，优秀候选可再尝试降低 25 m 的局部细化，并未逐档穷举 |
| `--q3-hover-top-k` | 20 | 保留的候选悬停点数 |
| `--q3-cp-candidates` | 12 | 进入联合精排的运输路线候选上限 |
| `--q3-tardiness-slack` | 0.05 | J1 相对容差；零 J1 时单靠它不会放宽 |
| `--q3-absolute-epsilon` | 0.0 | J1 绝对容差 |
| `--q3-selection` | `epsilon_makespan` | 当前唯一可选的前沿选解方式 |
| `--q3-setup-energy-mode` | `hover_plus_comm` | 建链能耗口径；另可选 `hover_only` |
| `--force-recompute` | 关闭 | 强制重建公共 DEM 航段缓存 |

**最终结果与最优性边界。** 正式 Q3 已于 final freeze 更新为 seed `20260927`：交付 **80/80 箱**，J1=0（归一化 J1=0）、运输最晚返航 **6949.848 s**、联合最晚返航 J2=**6975.244 s**；运输 **76.741 kWh**、中继 **3.349 kWh**、联合 J3=**80.090 kWh**；运输 26 架次、中继 4 架次、联合 J4=**30**。通信需求总时长 **41076.426 s**，DIRECT **23544.649 s**，RELAY **17531.777 s**，0.25 s 数值核验的 OUTAGE **0 s**。使用 2 架中继实体、4 组不同组件；模型允许组件充满后复用，但该具体方案未发生同组件复用。保存的 CP-SAT 状态为 **FEASIBLE**，本次求解内已验证 Pareto 候选数为 1。Q3 重新决定了部分运输箱组/路线与资源时刻，路线集合与 Q2-v2 基准不完全相同。

独立 validator 为 **PASS**。在最大核验间隔 **0.25 s**、切换定位精度 **0.05 s** 下，未检测到通信中断；这不是对所有实数时刻的解析证明。ALNS、有限悬停点与有限候选 CP-SAT 都不支持宣称整个连续位置/路由空间全局最优。

**输出文件、作用和阅读方法。** Q3 文件集中在 `outputs/q3/`，不覆盖 Q1/Q2 结果。

| 文件（位于 `outputs/q3/`） | 内容、用途与输出原因 |
|---|---|
| `baseline_q2_v2_summary.json` | 已提交 Q2-v2 基准的副本，固定 Q3 种子与对比口径；Q3 不依赖重跑 Q2 后的本地工作表 |
| `baseline_q2_direct_audit.xlsx` / `.json` | 无中继时逐架次直连比例、分阶段失联起止、最低裕量及地形/距离原因；说明哪里、何时需要中继，JSON 也供候选点生成读取 |
| `q3_transport_sorties.xlsx` | Q3 最终运输架次、箱组、路线、实体机、电池、准备/起飞/返航及能耗；对比 Q2 的运输决策是否改变 |
| `q3_box_deliveries.xlsx` | 一箱一行的交付时间、期望/首批截止、优先系数与逾期；重算 J1 和硬截止 |
| `q3_relay_sorties.xlsx` | 中继架次的实体/组件、准备至返航各阶段、悬停经纬度、DEM/AGL/MSL、分项能耗及 SOC；检查选点、建链时序和能量 |
| `q3_relay_resource_timeline.xlsx` | 中继实体周转、组件占用、返航 SOC 及充电起止；核对实体和组件无冲突 |
| `q3_communication_coverage.xlsx` | `通信保障区间` 展示每段 DIRECT/RELAY、保障中继、最低裕量及遮挡；`链路预算明细` 在各区间中点列直连及有关接入/回传预算，便于人工抽查。明细**不是全部 0.25 s 验证采样** |
| `q3_pareto.xlsx` | 冻结方案的 J1–J4、直连/中继/中断秒数、CP 和 validator 状态；只保存 1 个已验证前沿点，`Q3-SEED-20260927` 是归档时的来源标签，不是原求解器内部候选编号，也不代表完整全局前沿 |
| `q3_algorithm_comparison.xlsx` | Q2-v2 基准、Q3 运输部分和 Q3 联合方案的时间、能量、架次及通信比例；解释通信硬约束新增的中继成本 |
| `q3_summary.json` | 完整运输、逐箱、中继、通信区间、目标、验证和项目口径快照；供 Q4 后续继承。含建链能耗替代口径的**同排程**敏感性核算，未重新优化 |
| `cache/relay_candidates.json` | 带来源指纹的候选悬停点缓存，加速重复搜索，不是最终可行性证据。局部 `.pkl` 临时缓存不属于正式提交结果 |
| `logs/q3_search_history.csv` | 五 seed 批处理未保存所选 seed 的逐迭代轨迹；冻结后的文件仅含表头，不能用于复查这次 ALNS 路径。最终方案记录了完成 2000 次迭代；详情见 `outputs/experiments/final_freeze/` |
| `validation/q3_validation.xlsx` / `.txt` | 从源附件及 DEM 独立重建后的约束统计、违例与验证精度；判断最终合法性应看这里 |
| `结果提交_Q3.xlsx` | 官方模板副本，只填 `Q3_中继架次` 和 `Q3_通信保障`；“悬停海拔”填 MSL、“开始时刻”填准备开始。官方没有 Q3 专用运输 sheet，完整运输方案在补充文件中 |

| 图（位于 `outputs/q3/figures/`） | 读图目的 |
|---|---|
| `q3_transport_relay_map.png` | DEM 背景上的 O01/G01、服务区、运输路线与中继悬停点；解释空间覆盖安排 |
| `q3_communication_timeline.png` | 每个运输架次的 DIRECT/RELAY 时间条；检查切换及共享保障时段 |
| `q3_relay_gantt.png` | 中继实体从准备到返航及其中正式服务的横条；观察任务重叠与周转压力 |
| `q3_link_margin.png` | 以区间中点为横坐标、该区间最低链路裕量为纵坐标；找出接近 0 dB 的薄弱保障段 |
| `q3_pareto.png` | 已验证候选的 J1–J2 散点；目前只有一点，主要核对选解，不能展示完整权衡曲线 |
| `q3_objective_history.png` | 标注五 seed 批处理未归档逐迭代轨迹，避免把旧 CURRENT 的曲线误认为冻结方案的搜索历史 |

**验证方式与重点。** `python main.py --validate q3` 读取 `q3_summary.json`，从附件/DEM 重算运输、电池、中继物理、SOC、资源时间、直连与两段中继链路；所有硬约束成立且数值核验 OUTAGE=0 才 PASS。

<a id="q4"></a>
## Q4：任务分区与资源配置

**固定来源与决策范围。** Q4 直接读取 `outputs/q3/q3_summary.json`，冻结快照 SHA256 为 `ec30af5a87d527d514081a35e7564bb41f74248331f3a2bc1d52472c540b3d3b`。先用 `--validate q3` 确认 80 箱、26 个运输架次、4 个中继架次和零失联。Q4 冻结箱组、路线及访问顺序、机型、全部运输和中继任务时间、悬停位置、能耗/SOC、DIRECT/RELAY 区间及其对应架次；不调用 Q3 求解器。只决定 15 个服务区如何分成 `K=2` 或 `K=3` 个非空组、固定任务属于哪组，以及等价资源如何在**组内**重新编号。每区恰属一组，组间不调拨资源。

**原子组件与中继歧义。** 从同一多点运输架次的服务区取传递闭包，冻结方案得到 6 个运输组件：`{S001}`、`{S002,S003,S004,S005,S007,S009,S015}`、`{S006}`、`{S008}`、`{S010,S012,S013}`、`{S011,S014}`。原题没有明确说明一个 Q3 中继架次同时保障未来不同组时如何分配，因此程序同时计算两种**项目解释**：

- `strict_no_duplication` 是正式主结果。一个 Q3 中继任务只能属于一组；由同一中继保障的运输任务进一步绑定。当前严格组件是 `{S001}`、`{S006}`、`{S002,S003,S004,S005,S007,S008,S009,S010,S011,S012,S013,S014,S015}`。若严格组件少于 `K`，明确报不可行，不拆任务。
- `replicate_relay` 仅作题意敏感性分析。先只按运输组件分区；当一个中继跨组时，各相关组复制它的**完整固定任务**，包括悬停位置、高度、全部时间、能耗、SOC 和原通信对应关系。复制任务赋组内 ID，不能缩短服务区间。它并非原题指定的唯一解释，官方 Q4 模板仅填严格模式。

**资源数为何重新着色。** Q3 旧实体 ID 若出现在不同组，会与组间不能调配冲突。Q4 允许对同型号、同类资源按固定时间轴在组内重新编号，并同时报告 `inherited_q3_id_count` 和 `minimum_recolored_requirement`。后一项是正式需求。四类占用区间均为半开区间 `[开始, 结束)`，同一时刻释放并开始可复用：

| 资源 | 固定占用区间 | 结束时刻依据 |
|---|---|---|
| A/B/C 运输无人机 | `[准备开始, 返回 O01)` | Q3 固定返航时间 |
| A/B/C 共享电池 | `[准备开始, 返航＋充至 100% 时间)` | 复用 `physics/battery.py` 两阶段充电模型 |
| 中继无人机 | `[准备开始, 周转结束)` | Q3 固定周转结束，含原题 300 s |
| 中继能源组件 | `[准备开始, 返航＋充至 100% 时间)` | Q3 组件充电结束，并与充电模型交叉核对 |

每组每类资源用 sweep-line 求**最大同时占用数**，再用 greedy interval coloring 产生可检查的内部 ID；固定区间图的最少颜色数等于最大重叠数。分区总需求是各组最少数之和，不能取组间最大值。充电和中继周转可能使电池/组件数高于机体数。

**评价指标均为项目定义，非原题给定公式。** `ResourceScale` 是 8 类资源需求数的简单总和，只表示资源**单元数量**，不表示无人机、电池和组件具有相同价格或价值；完整 8 维向量始终保留。`PartitionRedundancy[r] = 分区需求[r] − 固定 Q3 全局共享最少数[r]` 表示组间独立造成的额外配置，区别于 `Surplus[r] = max(库存[r] − 分区需求[r],0)` 的库存剩余。`Gap[r] = max(分区需求[r] − 库存[r],0)`。组工作量为该组运输与中继各任务的 `返航−准备开始` 时长之和；充电和周转只占资源，不计主工作量。均衡指标为组工作量的总体标准差/均值 `CV_W`，另报 `(最大−最小)/均值`。分别以 `(总缺口, ResourceScale, CV_W)` 求 Pareto 非支配集，代表方案按这三项及 canonical 分区签名字典序选择；没有主观线性权重。

**精确范围与本次结果。** 采用 canonical 集合分区完整枚举，自动去除组标签置换；当前严格模式 K2/K3 分别有 3/1 个候选，复制模式有 31/90 个。固定 Q3 时间表下的全局共享最少向量按 `A/B/C 机体；A/B/C 电池；中继机；组件` 顺序为 `(4,2,2; 6,4,4; 2,3)`。库存为 `(4,2,2; 6,4,4; 2,6)`。代表方案如下：

| 正式 strict 方案 | 分组 | 总需求向量（上述顺序） | 规模 | 分区冗余 | 库存缺口 | 工作量 CV |
|---|---|---|---:|---:|---:|---:|
| K2 | G1=除 S006 外 14 区；G2=`{S006}` | `(5,3,2; 7,5,4; 2,3)` | 31 | 4 | 4 | 0.882590 |
| K3 | G1=`{S001}`；G2=除 S001/S006 外 13 区；G3=`{S006}` | `(5,3,4; 7,5,6; 2,3)` | 35 | 8 | 8 | 1.183290 |

K2 的四个缺口是 A/B 型运输机和 A/B 型电池各 1；K3 的八个缺口是 A/B 型运输机各 1、C 型运输机 2，以及 A/B 型电池各 1、C 型电池 2。逐组峰值时刻、同时占用任务及充电/独立配置原因见 `q4_resource_gap.xlsx`。本快照下复制模式的字典序代表方案碰巧与严格模式相同，选中方案仍只有原始 4 个中继任务，因此选中方案的中继资源差值为 0；这不表示两种口径等价。复制模式的其他合法分区中，K2 最多生成 7 个中继任务、K3 最多 9 个，详见候选表和敏感性表。这里的“精确”只针对**固定 Q3 调度快照和上述 Q4 继承口径**的全部合法分区与最少同类区间资源数，不证明 Q1–Q4 联合全局最优。

```powershell
python main.py --validate q3
python main.py --question q4 --q4-relay-policy both --q4-selection pareto_lexicographic
python main.py --validate q4
```

`--q4-relay-policy strict` 只计算正式严格结果；`replicate` 和默认 `both` 同时计算严格结果及复制敏感性，以保证官方模板总有严格口径。每次 Q4 CLI 均先对保存的 Q3 源重新执行独立验证；保存的 Q4 validator 还比较 Q3 SHA256、冻结任务和通信字段、全部候选与组归属、四类资源区间/充电/周转、库存、Pareto/选解以及官方模板。Q4 不对每个分区重复执行完整的 Q3 链路搜索，因为其轨迹、时刻和保障关系未改变。

| `outputs/q4/` 文件 | 阅读目的 |
|---|---|
| `q4_summary.json` | 全候选机器快照、Q3 指纹及冻结字段、组内着色、选解和目标；是复核入口 |
| `q4_atomic_components.xlsx` | 运输组件、严格组件、中继与运输任务绑定；解释为什么有些区不能拆开 |
| `q4_partition_candidates.xlsx` | 四种 policy/K 的所有候选、资源向量、缺口、冗余、均衡、Pareto/选中标记 |
| `q4_selected_partitions.xlsx` | 各模式代表分区的服务区、运输/中继任务和组指标总览 |
| `q4_resource_requirements.xlsx` | 各代表方案每组 8 类独立配置数、货箱/质量和工作量；可直接比较组负担 |
| `q4_resource_coloring.xlsx` | 每项固定任务的组内机体/电池/中继/组件内部 ID 与占用区间；检查无重叠和无跨组调拨 |
| `q4_resource_gap.xlsx` | 库存、全局最少数、分区需求、冗余/剩余/缺口，以及峰值组、时段和关键任务；解释缺口来源 |
| `q4_k2_k3_comparison.xlsx` | 正式严格 K2 与 K3 的资源、缺口、冗余和逐组工作量并列；供论文比较，不预设孰优 |
| `q4_relay_policy_sensitivity.xlsx` | 严格与复制模式的代表方案和全部候选复制量范围；量化跨组中继歧义 |
| `结果提交_Q4.xlsx` | 原官方模板副本，仅填 `Q4_分区配置` 的严格 K2/K3 代表方案 |
| `validation/q4_validation.xlsx`、`.txt` | 独立重算的候选/组/资源分配检查数与 PASS/FAIL |

`figures/q4_partition_map_k2.png` 和 `q4_partition_map_k3.png` 在经纬度图上标出 O01、服务区及代表方案分组；`q4_resource_comparison.png` 并列 8 类 K2/K3 需求；`q4_workload_balance.png` 显示组工作量差异；`q4_resource_gap.png` 并列库存与两种分区需求，帮助定位超库存资源。这些图用于解释，validator 以数据与固定任务为准。

<a id="verification"></a>
## 测试与独立验证

两类检查回答不同问题：

| 检查 | 命令 | 回答的问题 |
|---|---|---|
| pytest | `python -m pytest tests -q --basetemp outputs/test_tmp` | 数据、DEM、公式、边界及 validator 等代码行为是否符合测试预期；**不会**重新求得全局最优 |
| 已保存方案独立验证 | 下列 `--validate` 命令 | 保存的决策在源数据与物理模型下是否满足货箱、能量、SOC、时限、资源及 Q3 通信硬约束；**不等于**最优性证明 |

```powershell
python main.py --validate q1
python main.py --validate q2 --q2-algorithm v2
python main.py --validate q3
python main.py --validate q4
```

历史基准可用 `python main.py --validate q2 --q2-algorithm v1` 检查。验证器从源数据重算关键数值，不直接相信保存的能耗或通信布尔值。Q3 结论应连同 0.25 s 最大核验步长及 0.05 s 切换定位精度一起阅读。Q4 验证器重查 Q3 源与所有保存的候选分区、资源时间轴和官方填表；只证明其相对固定 Q3 的继承与计算正确。

<a id="outputs"></a>
## 输出文件总览

| 问题 | 核心可读方案 | 完整机器快照 | 独立验证 | 官方模板副本 |
|---|---|---|---|---|
| Q1 | `outputs/q1/q1_plan.xlsx` | `outputs/q1/q1_summary.json` | `outputs/validation/q1_validation.*` | `outputs/q1/结果提交_Q1.xlsx` |
| Q2-v1（历史） | `outputs/q2/q2_transport_sorties.xlsx` | `outputs/q2/q2_summary.json` | `outputs/validation/q2_validation.*` | `outputs/q2/结果提交_Q2.xlsx` |
| Q2-v2（正式） | `outputs/q2_v2/q2_transport_sorties.xlsx` | `outputs/q2_v2/q2_summary.json` | `outputs/q2_v2/validation/q2_validation.*` | `outputs/q2_v2/结果提交_Q2.xlsx` |
| Q3 | `outputs/q3/q3_transport_sorties.xlsx`、`q3_relay_sorties.xlsx` | `outputs/q3/q3_summary.json` | `outputs/q3/validation/q3_validation.*` | `outputs/q3/结果提交_Q3.xlsx` |
| Q4 | `outputs/q4/q4_selected_partitions.xlsx`、`q4_resource_requirements.xlsx` | `outputs/q4/q4_summary.json` | `outputs/q4/validation/q4_validation.*` | `outputs/q4/结果提交_Q4.xlsx` |

其余公共和第一阶段输出：

| 文件/目录 | 作用 |
|---|---|
| `outputs/cache/node_pair_geometry.json` | 16×16 有向节点对的距离、最高 DEM、巡航/作业高度和爬升下降缓存；源指纹变化可重建 |
| `outputs/validation/base_physics_check.xlsx` | O01→S001 与多点示例的逐段距离、DEM、高度及各机型飞行时间，供人工核验公共计算；不是优化结果 |
| `outputs/data_audit/data_summary.xlsx`、`data_schema.json`、`data_validation.txt` | 初始输入表汇总、字段/规模字典和一致性结论；追溯原始数据如何被理解 |
| `outputs/data_audit/source_manifest.json`、`workbooks_raw.json`、`geodata_audit.json`、`validation_checks.json`、`audit_statistics.json`、`artifact_verification.json` | 原始文件哈希与路径、Excel 单元格、地理文件及交叉校验的机器证据；用于复查审计，不是求解方案 |
| `outputs/data_audit/problem_extracted.txt`、`document.xml`、`office_math.json`、`docx_structure.json`、`geodata_description.txt` | DOCX 正文/Office Math 与地理说明提取证据；普通文本不能代替原始公式 XML |
| `outputs/data_audit/` 下的 `image*`、`preview_*`、`geodata_description_page.png`、`data_summary.xlsx.inspect.ndjson`、`run_log.txt` | 附件媒体、审计预览、说明页、工作簿检查记录及审计运行日志；帮助人工定位结论来源 |

`.gitkeep` 只维持空目录；`outputs/test_tmp*` 和被忽略的临时缓存不是论文结果。Q1–Q4 每张表与图的阅读目的见各问“输出文件”表。

<a id="limitations"></a>
## 模型局限性

1. 原题缺少运输水平及爬升附加能耗的完整分项公式。Q1–Q3 使用用户授权的距离/等效航程与 `mgh/\eta` 项目假设；正式原式若补充，应整体重算。
2. Q1 的 DP 对当前离散箱组与目标精确，但不证明能耗假设是唯一物理解释；连续安全载荷矩阵不替代实际箱组体积和逐段物理检查。
3. Q2-v1/v2 的路线和组批依赖启发式搜索。固定路线候选的 CP-SAT `FEASIBLE` 或 `OPTIMAL` 状态都不能证明整个路线空间全局最优。
4. 当前正式 Q3 是 `20260927` 五 seed 实验的冻结方案，能源组件在模型中可充满后复用；该具体方案 4 个中继架次使用了 4 个不同组件。默认搜索仍有局部 XY、最高高度粗筛、有限 ALNS/CP 候选等范围限制。此前 CURRENT 和其他搜索空间实验保留在 `outputs/experiments/`；即使 CP-SAT 返回 `FEASIBLE`，也不证明连续空间全局最优。
5. Q3 通信连续性经阶段边界、细时间步及切换加密做数值核验。OUTAGE=0 仅表示指定精度下未检测到中断，不是解析意义上的所有时刻证明。
6. 中继高悬停点飞行高度、建链 30 s 能耗及部分资源时间口径属项目补充假设。道路、水体等图层未作禁飞约束，题目也没有提供相关限制。
7. Q4 的中继跨组复制、资源重新着色、工作量与简单资源规模均是清楚标注的项目解释。若题目后续给出不同口径，应据此重算 Q4。Q4 精确枚举不消除上游 Q2/Q3 启发式搜索、模型假设和 Q3 数值链路核验的限制。

<a id="reproduction"></a>
## 完整复现流程

在仓库根目录、原始附件齐全且已安装 Miniconda `dl` 的前提下，依次执行：

```powershell
conda activate dl
python -m pip install -r requirements.txt
python -m pytest tests -q --basetemp outputs/test_tmp

python main.py --question q1
python main.py --validate q1

python main.py --question q2 --q2-algorithm v2
python main.py --validate q2 --q2-algorithm v2

python main.py --question q3 --seed 20260927
python main.py --validate q3

python main.py --question q4 --q4-relay-policy both
python main.py --validate q4
```

这些命令会写对应 `outputs/` 目录；若只需核验已冻结结果，直接运行四个 `--validate` 命令即可。Q3 从 `outputs/q3/baseline_q2_v2_summary.json` 读取已提交 Q2-v2 基准副本，先重跑 Q2 不会自动替换此种子。DEM 或节点数据变更会令公共缓存源指纹失效，亦可加 `--force-recompute`。启发式受时间预算和运行环境影响，重新求解 Q2/Q3 即使使用 seed `20260927` 也可能得到不同快照；要复核当前正式 Q3/Q4，应验证仓库已保存的冻结文件。Q4 始终以当时保存并验证通过的 Q3 快照为固定输入，重新完整枚举。

## Q3→Q4 实验分支复现

历史 `experiment/q3-q4-improvement` 分支保留当时的正式快照；本 final-freeze 分支将正式 Q3/Q4 更新为 `20260927`。逐项原题审计见 `outputs/audit_v2/`；早期候选、参数对比、Q4 选择敏感性、工作量指标和图位于 `outputs/experiments/q3_q4_improvement/`。单独运行这些实验脚本时不会写正式四问目录。

```powershell
conda activate dl
python scripts/generate_audit_v2.py
python scripts/q3_q4_improvement.py
python scripts/route_archive_experiment.py
python scripts/refine_route_archive.py
python scripts/retry_altitude_search.py
python scripts/complete_group_local.py
python scripts/enrich_relay_policy.py
python scripts/extend_selection_sensitivity.py
python scripts/finalize_cross_q3.py
python scripts/finalize_pareto.py
python scripts/finalize_plots.py
python scripts/summarize_workload.py
python scripts/energy_assumption_sensitivity.py
python scripts/fine_validate_candidates.py
python -m pytest tests -q
```

主实验按 6/8/10/12 中继架次槽位、全高度、局部/扩大/全 DEM XY、多种 J1 ε 与能耗/架次目标比较。`q3_candidate_comparison.xlsx` 是候选总览；`q3_component_reuse_comparison.xlsx` 检查组件周转是否真正发生；`q3_hover_search_comparison.xlsx` 保留全高度/XY 搜索成功或失败的证据；`q3_multiobjective_pareto.xlsx` 展示 J1–J4 权衡；`q4_cross_q3_comparison.xlsx` 将每个通过验证的 Q3 送入 Q4 完整枚举。`q4_selection_sensitivity.xlsx` 改变 Gap/Scale 容差来查看分区平衡取舍，`q4_workload_sensitivity.xlsx` 和指标汇总比较不同工作量口径，`q4_relay_policy_comparison.xlsx` 并列 strict、完整复制和实验性 group-local 口径。`figures/` 中的 Pareto 与 K2/K3 取舍图帮助识别上游候选、资源缺口和均衡程度的关系。

工作簿的 `PASS` 必须结合验证步长解读。主实验先以 4 s 时间步独立筛选，关键候选再以 0.25 s 复核；任何检测出 OUTAGE 或资源冲突的候选均不得替换正式结果。`group_local_relay` 是题意歧义的敏感性分析，重新计算各组中继任务，不是唯一官方解释。能耗分项依然是项目假设，`energy_assumption_sensitivity.xlsx` 仅评估系数变化对已保存排程的影响。

实验脚本会在本地产生每个候选完整 Q4 JSON，供 `q4_selection_sensitivity.xlsx` 重算与逐候选验证；这些约 8 MB/份的中间快照不纳入 Git，运行上述脚本可重建。实验工作簿、报告、图、Q3 候选快照和脚本纳入分支提交。

## Q3 五随机种子复现实验

在 `experiment/q3-q4-improvement` 提交基础上建立独立 worktree 后，执行：

```powershell
conda activate dl
python scripts/q3_five_seed_batch.py
```

若当前 PowerShell 没有初始化 Conda 激活钩子，可用等价命令 `conda run -n dl --no-capture-output python -u scripts/q3_five_seed_batch.py`，并用 `python -c "import sys; print(sys.executable)"` 核实激活后的解释器。

脚本依次运行 20260923–20260927 五个 seed。除 seed 外，均使用该分支 `Q3AlgorithmConfig` 默认值：每次搜索预算 600 s、ALNS 2000 次迭代、6 次重启、悬停候选上限 20、CP 路线候选上限 12、中继架次搜索槽位 6；能源组件按准备至充电结束的区间分配，充满后可复用。各 seed 有独立实验缓存；Q2-v2 基准快照相同。模型与数据指纹和完整配置写入结果工作簿的 `Fixed configuration` 页。

每个 Q3 结果先保存为 `outputs/experiments/q3_five_seed/<seed>/q3.json`，再从磁盘读回，用独立 DEM/通信环境按 0.25 s 步长验证。只有验证通过且 OUTAGE=0 的方案才进入 strict Q4 完整枚举及独立 Q4 校验。`outputs/experiments/q3_five_seed/q3_five_seed_comparison.xlsx` 记录五个 seed 的 J1/J2/J3、运输与中继架次、strict 原子组件与最大组件大小、K2/K3 合法分区数和选中方案的 Gap/Scale/CV；失败 seed 保留原因为 FAIL，不会参与优选。`comparison.json` 是同一结果的机器可读版本。Q4 大型 JSON 保留本地但不提交 Git。

最终候选先满足 J1=0、J2 不超过五个合法方案中最优值的 102%、J3 不超过最优值的 101%；再按 strict 组件数、最大组件大小、K3/K2 分区数与资源/均衡指标排序。表中另标出五轴 `(J1,J2,J3,NT,NR)` Pareto 状态。这是针对这五次随机运行的经验比较，不是全局最优证明。

## Q3/Q4 final freeze

正式 `outputs/q3/` 和 `outputs/q4/` 现由 `20260927` 生成。冻结程序 `scripts/final_freeze_q3_q4.py` 对旧 CURRENT、`20260925` 对照和 `20260927` 逐个重跑 0.25 s Q3 独立验证，再对每个方案重新完整枚举 strict/replicate Q4；只有全部通过，才在隔离 staging 中生成官方模板与补充表并逐行核对，最后发布正式结果。`outputs/experiments/final_freeze/final_freeze_comparison.xlsx` 并列三方案的目标、通信、组件、K2/K3 候选数及 Gap/Scale/CV；`freeze_manifest.json` 保存源指纹、校验结果和正式 Q3/Q4 SHA256。`20260925` 的 Q3 JSON 与验证记录继续保存在五 seed 目录，作为“Q3 指标较接近、Q4 资源缺口更低”的对照方案，不替换正式文件。

原题没有给出水平运输能耗与爬升附加能耗的完整分项公式，冻结核对确认的是仓库已声明项目假设的**一致性和独立重算结果**，不能据此把这些分项称作原题官方公式。五 seed 批处理未归档所选 seed 的逐迭代 ALNS 轨迹；正式 `q3_search_history.csv` 仅有表头，`q3_objective_history.png` 明示该限制，没有借用旧 CURRENT 的搜索曲线。冻结脚本先在本地 `outputs/experiments/final_freeze/staging_root/` 生成完整导出，并把旧正式目录备份在 `previous_formal/`；两者是可重建/回退的本地文件，不纳入 Git。
