# 冻结剖面 3.87.17 独立只读审查

审查日期：2026-10-25。仓库：`/workspace/scratch/fd5d8a28c64e/work/foundation_section_validation`。比较基线：`6204f17`（3.87.16）。审查对象为本轮未提交的 `app/section`、剖面 API、前端入口、导出链和新增测试。未修改产品源码。

## 结论

发现 **2 项 P2 问题**：一个是已接受土弹簧边界输入的稳定性误判，另一个是分析接口与客户端的超时预算不一致。未发现 P0/P1 级的错误结果放行、调用方伪造结果导出、跨快照出图或失败阶段形成完整包络的问题。

本结论限于已声明的二维单桩、双侧 EPP 弹簧、零预力阶段支点及 N=0 圆截面纯弯复核范围；不把未覆盖的剪切、裂缝、稳定和构造验算作为新增缺陷，也不把本轮数值比较当作正式施工图放行。

## 发现 1 — P2：同时删除互补单向刚度，会把稳定的双侧界限初态误判为机构

### 位置

- `/workspace/scratch/fd5d8a28c64e/work/foundation_section_validation/services/api/app/section/solver.py`，第 295–306 行，尤其 296–300 行。
- 对照实现存在相同判定假设：`/workspace/scratch/fd5d8a28c64e/work/foundation_section_validation/services/api/app/section/independent.py`，第 188–190 行及 199–201 行。

### 影响与原因

收敛后检查把恰处于主动/被动界限的所有弹簧刚度同时删除，再判定物理切线是否奇异。然而双侧弹簧在同一节点可以互补：当两侧均有 `p0=lower`、`upper>lower`、`k>0` 时，正向微移令坑内弹簧进入弹性段，负向微移令坑外弹簧进入弹性段。两侧合成的恢复力在零点两边均有正刚度，不存在所报告的机构。

这是正常公开输入可达到的情况：模型允许无黏聚土并取 `K0=Ka`，预处理只要求 `pa <= p0 <= pp`。当前主求解器会在初始平衡阶段直接失败，后续阶段全部未计算，因而阻断有效模型的阶段计算与完整成果。此处是误拒绝，未观察到错误数值被放行。

### 最小可复现证据

用指定 Python，在 `services/api` 工作目录直接调用主求解器：

```python
from app.section.solver import solve_stages

springs = []
for node in (0, 1):
    for sign, side in ((1, 'inside'), (-1, 'outside')):
        springs.append(dict(
            id=f'{side}-{node}', node=node, sign=sign, side=side,
            depthM=float(node), p0Kn=10.0, kKnPerM=1000.0,
            lowerKn=10.0, upperKn=100.0,
        ))
model = dict(
    nodesM=[0.0, 1.0], eiKnM2=10000.0, toeBoundary='free',
    stages=[dict(
        id='S0', name='balanced', event='initial',
        externalForcesKn=[0.0, 0.0], soilSprings=springs,
        installSupports=[], removeSupports=[],
    )],
)
print(solve_stages(model)['stages'][0]['failureReason'])
```

实际输出：

```text
Singular or ill-conditioned physical tangent; no artificial restraint added
```

直接代入相同弹簧本构，单节点 `sum(sign * clip(p0 + k * sign * u, lower, upper))` 在 `u=-0.001, 0, +0.001 m` 时分别为 `-1, 0, +1 kN`。两处不同深度的这种恢复力约束了自由梁的平移与转动刚体模态，零位移初态应稳定。

进一步走完整公开输入链：在 `verification_example()` 中只将各土层 `cohesion_kpa=0`，`k0=tan(pi/4-radians(phi_deg)/2)**2`，保留其余参数；`freeze_section()` 和 `build_solver_model()` 均成功，初态 984 个弹簧全部满足 `p0Kn == lowerKn`。`analyze_section()` 实际返回：

```text
snapshotHash = 37e09839b4f8b07b1cf9526fe96eed69c06efb7940040f68d6ac397449afe6aa
S0 failed
S1, S2, S3, S4, S5 not_calculated
failureReason = Singular or ill-conditioned physical tangent; no artificial restraint added
```

### 建议

在屈服边界上依据实际位移方向和互补接触约束检查刚体模态的恢复力，保留双侧互补约束产生的稳定性；不要把所有边界分支同时当成无刚度。对照求解器的最终刚体约束检查也需独立采用正确判据。增加“两个不同深度各有一对处于相同主动界限的双侧弹簧”的有效稳定模型回归，同时保留真实塑性平台机构的失败回归。

## 发现 2 — P2：客户端 90 秒总超时短于服务端完整比较路径

### 位置

- `/workspace/scratch/fd5d8a28c64e/work/foundation_section_validation/apps/web/src/api/client.ts`，第 349–357 行：分析请求 `timeoutMs: 90000`。
- `/workspace/scratch/fd5d8a28c64e/work/foundation_section_validation/services/api/app/section/benchmarks.py`，第 154 行、第 166–170 行：单独给予独立子进程 `timeout_seconds=90`。
- 同文件第 177–191 行，比较按解析检查、细网格分析、独立求解顺序运行。
- `/workspace/scratch/fd5d8a28c64e/work/foundation_section_validation/services/api/app/routers/sections.py`，第 41–57 行：主求解、附加比较、服务端保存全部完成后才返回结果。

### 影响与可复现条件

若 OpenSees 子进程确实运行至自身 90 秒超时，HTTP 请求在此之前已执行主分析、解析检查和细网格分析；子进程超时后还需封存、校验、保存及传输结果。因此完整响应必然超过客户端 90 秒总预算。

服务端原本会把独立比较明确标成 `unavailable` 并保存主结果，但网页先中止请求，既拿不到该状态，也拿不到 `resultHash` 以下载证据。当前面板只支持通过新一轮分析取得身份，重试又启动一次相同计算。这一条件由现有超时常量和串行调用顺序直接确定，无需等待真实 90 秒探针才能判定。

### 建议

给整条分析路径定义明确时间预算，让前端总预算大于服务端的主分析、网格检查、独立比较及响应封存预算；或改为可恢复身份的后台任务。至少应保证独立求解器超时后的 `unavailable` 结果能被页面接收和导出，并加入使用虚拟计时器的超时边界回归。单纯提高独立子进程超时会扩大该问题。

## 已核查且未发现同级缺陷的链路

- 冻结输入采用不可变 Pydantic 字段、规范 JSON 和内容哈希；对外返回副本。记录恢复重新校验输入和快照/分析身份。
- `SectionStore` 限定 64 位小写十六进制身份，原子硬链接发布记录并拒绝覆盖不同字节。结果只能在源快照已存在且匹配时保存。
- API 导出只接收 `result_hash`，从服务端加载结果与快照，不接受调用方上传的计算结果正文。
- 结果校验不只检查摘要，还核对离散输入、阶段身份、网格、曲线、包络和配筋需求。求解器阶段截短被拒绝；失败/未计算阶段不保留有效曲线；完整包络和配筋需求关闭。
- 主土弹簧迭代使用上一提交阶段的塑性位移；阶段收敛和总平衡通过后才提交。桩距用于压力归集、有效土宽用于弹簧刚度，静水差作为单独外载一次计入。
- 支撑安装参考位移取上一阶段位移；刚性换撑为确切位移约束，拆撑独立成事件。结果按单桩水平反力表达。
- 配筋需求取完整特征包络乘冻结效应系数一次；给定钢筋数量/直径与需求面积分别保留。缺失钢筋不补成实配，超过方法范围不伪称通过，设计位移不计算。
- 导出器读取冻结输入和已封存结果，没有调用求解器、重新选筋或读取 Project。DOCX、CSV、剖面及包络共用同一结果身份；失败成果不生成包络和配筋需求表。
- 线程内渲染使用锁，最终目录使用独立原子保留名字，ZIP 写完后才替换可下载文件；未发现同一结果并发导出互相覆盖的路径。
- 字体随 `app.section/assets/*` 打包，字体来源与许可证随源码登记，运行时不下载字体。需按既定打包流程验证 wheel 内容，但当前 setuptools package-data 已包含资产。
- 前端编辑会立即清除快照/结果，异步返回带修订号检查，结果再次核对快照/分析/剖面身份；ZIP 使用 Blob 路径下载，未把 ZIP 解码为文本。
- 原 A3-3 资料维持 `incomplete`，没有将合成例通过转为原工程同条件通过。

## 本次实测范围与证据边界

本审查执行了上述稳定性最小探针及完整 `SectionInput` 探针，并对已有最终运行文件执行当前 `validate_result()`。未重复全套测试或长耗时 OpenSees 运行，也未独立完成所有文档页的视觉 QA。

已读取并校验的最终运行证据：

- `/workspace/scratch/fd5d8a28c64e/work/section_final_run/snapshot.json`
- `/workspace/scratch/fd5d8a28c64e/work/section_final_run/result.json`

校验结果：`status=complete`，六阶段全部 `success`，手算/独立求解器/网格比较均为 `passed`，原项目比较为 `incomplete`。快照身份为 `2223478185afda316bc8fc9585a4998cf4900f88108b94337ef064f9f8a989fd`，结果身份为 `016d7dbcfbba405d0e72b1576088d2cc7484bd12d683e20997cb96afe3ce4ad1`。给定纵筋 `16Φ18`、面积 `4071.5040790523717 mm²`，需求弯矩 `149.21076798406227 kN·m` 与结果中的控制阶段及效应系数一致。该记录只证明已检查样例链路的一致性，不抵消发现 1 的边界输入缺陷。

本报告反映审查读取时的工作区；其他代理可能继续修改文件，修复后应针对上述两项重验并更新最终交付证据。

## 修复复核追加 — 两项 P2 均已闭合

复核日期：2026-10-25。本次只复核上述两项 P2，没有扩大到其他功能或重跑完整套件，未修改产品源码。**最终结论：发现 1 闭合，发现 2 闭合；这两项不再阻断本轮已声明范围的交付。**

### 发现 1：方向锥与独立求解器重试 — 已闭合

已检查 `solver.py` 第 264–333 行的 `_check_directional_restraint` 与 `independent.py` 第 177–253 行的独立实现。

- 正 EI 梁的零应变能方向只有 `v=a+b·xi`。等式约束秩为 2 时没有非零方向；秩为 1 时只需检查其法线的两个垂直方向；无等式时，有限个齐次半平面的非零闭凸锥必有一条约束边界射线（没有半平面时则为整个平面）。当前代码分别检查了这些候选，二维候选集完备。
- 主动下界的零恢复力方向为 `sign·v<=0`，被动上界为 `-sign·v<=0`；恰闭合只受压支撑为 `v<=0`。双侧互补半平面能够共同排除非零刚体方向，未改成双倍或平均物理刚度。
- `upper==lower`、严格 `trial<lower` / `trial>upper` 的平台分支不提供约束。64 倍机器精度容差只把接近边界的内侧弹性分支保守地视为单侧约束，不会把严格越界平台提升成弹性约束。方向容差作用于已归一化、无量纲的行向量。
- 独立求解器在首次 `analyze(1)` 失败后重新进入 `_one_stage`，先 `ops.wipe()`，再从传入的上一提交 `plastic` 和支撑参考位移重建。`ModifiedNewton -initial` 只改变搜索矩阵；真实材料残差、方向锥与平衡验收仍然执行。`next_plastic` 只在成功输出后由外层接收；失败 trial 不会进入后续阶段。

### 发现 2：等待预算与按快照恢复 — 已闭合

分析请求等待预算已改为 180 秒，独立求解器保持 90 秒；导出等待预算为 120 秒。另新增只读 `GET /api/sections/snapshots/{hash}/latest-result`，通过 `snapshot_results` 下的不可变索引恢复已持久化结果。索引在结果文件保存后才发布；恢复时再次加载并验证结果和其来源快照，未用正在执行的计算冒充已完成结果。

前端分析超时后保留冻结身份，用户可显式恢复最近已保存结果，不会自动重算。恢复响应与分析响应共用 `snapshotHash`、`analysisHash`、`sectionId` 三项核对；修订号继续拒绝编辑后的迟到响应。恢复提示明确说明取得的是最近保存记录，下载使用恢复结果自己的 `resultHash`。

180 秒并非所有允许模型的硬运行上限，但超过页面等待预算时现在有可恢复、无需重复计算的结果路径，原先“客户端先超时而失去结果身份”的缺陷已消除。

### 本次定向验证

在 `services/api` 使用指定解释器运行：

```text
python -m pytest -q tests/test_section_solver.py tests/test_section_api.py \
  -k 'complementary_soil_limits or three_one_sided or exact_capacity_plateau or public_input_with_k0 or completed_result_is_recoverable'
```

实际输出：`11 passed, 22 deselected in 2.08s`。覆盖主动/被动界限的正向、零、负向加载，三处分散单侧约束闭合，真实平台仍失败，完整 `K0=Ka` 公共输入历史，以及按快照恢复最新已存结果。

另只读核对新增前端恢复测试，以及 `docs/releases/V3_87_17_evidence/frontend_http_recovery_verification.json` 中的 HTTPS Cookie 认证、恢复前 404、计算保存后恢复 200、同一结果 ZIP 下载 200 与清单身份一致证据。本次未重复 OpenSees 全套或浏览器全套运行；独立求解器部分采用源码、数学判据和已有针对性运行证据复核。

## 最终混合精度数值修复复核 — 无新增阻断

复核日期：2026-10-25。只读核对 `services/api/app/section/solver.py`，实际 SHA-256 与送审身份一致：`5cf9f3820862259adb68c4c00736c0a75377187671f6020745eb2307580e35e1`。**结论：未发现新的阻断问题；此前两项 P2 的闭合结论保持有效。**

### 状态、精度与验收

- `_scaled_solve` 保持 float64 矩阵分解，将校正右端转换为 float64；运行位移从 `longdouble` 零数组开始，Newton 校正累加和下一阶段 `previous=displacement.copy()` 保留扩展精度。本次没有改变土弹簧本构或验收容差。
- 局部梁端力、梁残差与梁应变能继续由同一组去除刚体运动的转角差计算。能量用于线搜索，最终接受仍要求真实节点残差、方向锥、矩阵条件及总力/总矩门禁；能量下降本身不会被当成阶段成功。
- 塑性位移与支撑安装参考位移仍按既有普通浮点状态合同保存。发布节点位移、转角、梁端力和残差向量时显式转换成普通浮点列表，反力和土状态也显式转换成 Python `float`，没有把 `np.longdouble` 直接送入 JSON。
- `previous`、活动支撑和 `committed_plastic` 的提交仍位于阶段收敛、稳定性及总平衡验收之后。失败分支先记录空响应并 `continue`，后续阶段标为 `not_calculated`，没有将失败 trial 提交为历史。
- 当前审查运行环境的有效尾数位为 float64 52 位、longdouble 63 位，扩展精度确实存在；本次结论针对已验证运行环境，不将该平台精度收益推定到所有体系结构。

### 定向实测

仅运行与本改动相关的细网格、历史提交及失败门禁回归：

```text
python -m pytest -q tests/test_section_solver.py \
  -k 'refined_full_section or soil_yield_unload or excavation_parameter_update or rigid_replacement or free_unrestrained or exact_capacity_plateau'
```

实际输出：`7 passed, 20 deselected in 0.38s`。真实无约束机构及完全塑性平台仍按失败处理，细网格拆撑和历史转移通过。

另用网格减半的 163 节点合成例进行一次原始求解输出核查：六阶段均 `success`；`json.dumps(..., allow_nan=False)` 与 `json.loads` 往返内容相等；递归检查未发现任何 NumPy 标量叶子。由 JSON 往返后的梁端力、外荷载、土力和支点/桩端反力独立重组节点平衡，最大力等价残差为 `8.176720134311921e-10 kN`，对应求解器最大记录残差为 `8.176731713591123e-10 kN`。输出转换未造成可见的反力或平衡验收失真。

已核对当前土求解器和网格比较证据中的源码哈希与送审文件一致；未重复统一 248 项后端回归或完整 OpenSees 对照，未修改任何产品源码。


## 发现 1 修复复核：方向锥与混合精度（2026-10-25）

原始审查发现保留为修复前记录；发现 1 已按真实双侧单向恢复力完成修复，源码已冻结。该补充仅关闭土弹簧稳定性问题；发现 2 的前端/API预算修复由其对应审查记录负责。

- 主核与独立核分别实现归一化二维刚体方向锥：内弹性/精确位移约束为等式，下限、上限及单侧支点为实际零刚度方向半平面，严格越界平台不新增约束。
- 先失败后修复的边界回归覆盖上下限互补、正负小荷载、非同节点三约束闭锥，以及公开 K0=Ka 完整阶段；真实自由端/铰端塑性平台继续失败。
- K0=Ka细网格额外暴露float64增量低于总位移ULP的残差卡滞；先红后用原float64矩阵求校正、longdouble累积位移和计算梁曲率。没有放宽力/矩、0.1%或2%门槛。
- 最终定向验证：54 passed in1.31s（主27+独立27）。默认与K0=Ka两例均为主/独立六阶段完成、逐值独立比较passed、.125→.0625m网格passed；最大峰值变化为0.9993404%和1.0059110%。
- 独立Fraction有理数可行性检验按最终源码刷新：1029配置，743闭锥、286非零方向、0差异；与生产perp枚举方法独立。
- 本环境np.finfo(float).nmant=52、np.finfo(np.longdouble).nmant=63；不含隐含首位。longdouble部分平台可能仍只有float64精度，不宣称跨平台同精度，不能收敛仍明确阻断。

详细闭环说明：`foundation_section_validation/docs/releases/V3_87_17_evidence/soil_solver_p2_closure.md`。当前计算证据：`soil_direction_cone_verification_current.json`；最终oracle证据：`soil_direction_cone_probe_result.json`。

最终solver.py SHA256：`5cf9f3820862259adb68c4c00736c0a75377187671f6020745eb2307580e35e1`；independent.py：`ae66d24601a1bac63cfd813572722032e24ab1f1b745f47c7fa743e257c2bb7b`。源码冻结后最终统一回归与出包由根代理执行。
