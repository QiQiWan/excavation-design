# PitGuard V3.87.27 工程验收执行记录

## 1. 本版结论与边界

**数值单项验收：在明确假设及适用条件内已获得证据。真实工程和正式签发：尚未通过。**

P1：对于给定的二维稳态 Darcy 边界值问题，已有独立稀疏装配、双网格、边界敏感性、不利水头、物理不变量核验。仍缺勘察来源、承压突涌/管涌与内蚀的场地特定破坏准则以及独立专业审核；不得将水力计算“收敛”升级为工程“通过”。

P2：本版把 **GB/T 50010-2010（2024 年局部修订）第 6.3.15 条**的圆形截面等效法用于**第 6.3.12 条规定范围内的偏心受压抗剪**，同时检查第 6.3.1 条截面限制。必须有设计轴压力、剪力、明确均布/集中荷载类型及剪跨比来源、箍筋有效面积和规范参数，不适用受拉与其他受力情形。正截面轴力-弯矩及配筋几何沿用现有受限核验。适用于目标圆桩的裂缝宽度、二阶效应和实际构造仍未全部核验；方法单项通过不代表构件通过。

P3：增加研究求解器双侧压力积分点 `soilPoints` 向全通道比较器 `pressureStations` 的保力转换；进行了自由、铰支、固定桩端和两种冠梁、支撑刚度各半/倍的七组对照。此研究保留模型中人工假设；不得根据某一峰值相近推断原工程真实约束。已核对原计算书 PDF 字节但 PDF 不是原生五阶段数值导出。桩端、冠梁分担规则、对侧墙阶段响应、分层坡台压力方法和原始 S1–S5 节点值仍缺，真实同条件 RMSE **未计算**。

P4：继续采用 V3.87.26 不可变冻结快照、单一结果身份、工程师 P1–P4 分阶段复核、受限 DOCX/PDF/DXF/CSV 工作版与 SHA256 核验。最终 `formalConstructionIssue=false`，正式设计院校审应另行完成。

## 2. 本轮规范参数与公式

圆截面半径 r(mm)，规范等效值：`b=1.76r`、`h0=1.6r`；如符合偏心受压条文及荷载条件，计算：

```
V_R = [1.75/(lambda+1)] ft b h0 + fyv Asv h0 / s + 0.07 N
N_used = min(N, 0.3 fc Ac)
V_section = 0.25 beta_c fc b h0
V_allow = min(V_R, V_section)
```

内部强度/尺寸采用 MPa 和 mm，输出 kN；轴压力正，轴拉阻断。集中荷载需说明实际 `a/h0`，依适用条文限幅；均布工况采用条文指定的剪跨比。计算工具仅执行受限公式，现场来源证据不由工具自行认证。若不具备条文适用条件，则输出 `outside_method_scope`。

P1 使用三个与线性 Darcy 方程相符的变形检验：统一增加所有边界水头时，梯度/流量不变；全域渗透系数乘正数时，梯度不变、流量按比例变化；水头差放大时，梯度/流量同步变化。上述检验独立于主/备稀疏线性方程求解的逐值比较。

## 3. 模块与接口

新增或修改：

```
services/api/app/engineering/single_braced_pile/
  normative/circular_axial_shear.py
  normative/hydraulic_invariants.py
  normative/reinforcement.py
  real_project/curve_adapter.py
  real_project/boundary_sensitivity.py
  assurance/hydraulic.py
  assurance/luoyu.py
  assurance/router.py
  assurance/quality_protocol.py
  assurance/assets/method_catalog.json
```

只读研究接口：`GET /api/engineering/one-strut/assurance/luoyu-3-3/boundary-sensitivity?mesh_max_m=0.25`（具体全局 API 前缀以部署配置为准）。响应明确列出 `sameConditionsAsOriginal=false`。

## 4. 复现实测（验证环境）

```bash
cd <unzipped_project_root>
# 安装相应 Python 依赖后，使用独立验证包解压到项目同层 verification 目录
PITGUARD_SOURCE_ROOT="$PWD" PYTHONPATH=services/api pytest -q verification/tests
PITGUARD_SOURCE_ROOT="$PWD" \
 LUOYU_REFERENCE_PDF=/absolute/path/to/original_report.pdf \
 PYTHONPATH=services/api python verification/run_38727_validation.py
python scripts/verify-release-consistency.py
```

正式源码包不包括原始勘察报告、项目数据、测试、生成物。验证包提供复现代码、真实源 PDF 读取摘要（不附源 PDF）、P1/P2 数值结果、P3 七组敏感性 CSV/JSON 和 P4 受限工作版示例。

## 5. 退出研发状态需补齐的实际工程证据

1. P1：抽水/水位/渗透系数试验、现场边界及有效期、适用土层破坏准则、最不利工况与独立计算报告。
2. P2：所有 ULS/SLS 组合的逐阶段同截面 N-M-V、钢筋配置与材料标准、圆桩裂缝宽度适用方法及校核、节点构造和二阶效应评估。
3. P3：原工程桩端、冠梁分担规则、对侧围护墙刚度/位移、坡台分层侧压力方法、原生五阶段全部压力/u/M/V/反力数组及文件身份。
4. P4：工程师设计与校审签认、全部强制性验算闭合、完整构件/节点详图以及现场适用性审查。发行许可不由本版软件自动决定。
