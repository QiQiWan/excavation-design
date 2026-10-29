# PitGuard 受撑剖面土抗力方法研究与首版边界

日期：2026-10-25。用途：本轮实现与独立验证的内部方法记录；不构成工程设计校审、商业软件标定或规范全覆盖声明。

## 1. 本轮方法决定

本轮新增独立的 `two_sided_elastoplastic_winkler_v1` 剖面路径，不替换已有全基坑引擎。采用小位移 Euler-Bernoulli 梁、两侧有初始静止压力的独立理想弹塑性弹簧、主动/被动压力上下限、已提交塑性位移历史，以及明确安装和拆除事件。首版只支持零预力安装；任何非零预加力硬拒绝。永久刚性换撑用安装位置的位移约束，不用任意巨大刚度。

研究起初评估了两条路线：

1. 固定主动作用加唯一线性抗力，极限被动仅用于可用域检查。这可做受限结构求解，但必须交代初始参考压力。直接施加坑外主动压力、坑内弹簧初力为零，不能在未开挖、两侧对称土层下得到正确的零响应。两侧主动参考压力相减虽能数学平衡，却不能声称重现真实 K0 初始状态；坑内剩余抗力上限也应为 `pp-pa`，不能再用完整 `pp`。因此没有选择它作为本轮历史分析路径。
2. 两侧初始 K0 平衡，压力随局部位移变化并受各自上下限约束。这一方法能明确承载方向、零开挖平衡和塑性卸载历史，且可以用 OpenSees 独立材料与梁单元对照，故选为本轮受限方法。

“有初始压力”只是独立弹簧的本构偏置；不代表模拟了三维地应力生成、连续土体卸荷、土拱、墙土摩擦或孔压消散。

## 2. 一手依据及实际核对位置

|来源|准确位置|本研究采用的事实及边界|
|---|---|---|
|USACE, *Design of Sheet Pile Walls*, EM 1110-2-2504, 31 March 1994|§7-4、§7-5，印刷页 7-1 至 7-3；图7-1/7-2；式(7-1)至(7-6)|一维梁与分布非线性弹簧；零侧移对应 `p0=K0 pv`，反力在主动、被动界限之间。其砂土刚度还含相互作用距离，不可把本项目m值冒称该手册原式。|
|同上|§7-7，印刷页7-4|原手册明确该实现是 gravity turn-on，不累计施工历史。本轮塑性位移carry是另行定义并待数值验证的扩展，不归因于该历史手册已经验证。|
|香港 GEO, *Deep Excavation Design and Construction*, Publication No.1/2023|§6.6.3.1，印刷页73；§6.6.3.2，页74|讨论弹塑性独立弹簧梁法、弹簧参数选择难度和局限；梁弹簧不直接给出地表沉降。连续体初始应力等能力不可据此推导。|
|同上|§8.3.4、§8.3.5，印刷页106|预加支撑和拆撑顺序会改变墙体响应，拆撑回弹必须考虑。|
|OpenSees 官方文档|§3.1.10.13 `elasticBeamColumn`；§3.1.10.1 `zeroLength`|2D弹性梁单元和单轴材料零长度弹簧的官方接口。|
|OpenSeesPy 文档|§4.14.3.2 `ElasticPP`；§4.14.5.21 `InitStrainMaterial`|不对称屈服位移、初应变材料，用于独立验证，而非土体参数推荐。|
|OpenSees 官方源码|`ZeroLength.cpp::setDomain` 与 `ZeroLength.cpp::update`|创建时保存两端非零相对位移d0，更新时减d0；新安装弹簧不能再误扣一次安装位移。|

来源URL与检索ref（正文全部为转述，不提供长段复制）：

- USACE官方入口：https://www.publications.usace.army.mil/Portals/76/Publications/EngineerManuals/EM_1110-2-2504.pdf （检索 `turn24search0`；直取403）。公开原文镜像：https://civiltech.com/wp-content/uploads/2021/01/sheetpile.pdf （已读取 `turn29view0`、`turn30view0`、`turn30view1`，原文封面和章节核对为1994手册）。此为历史方法证据，不宣称现行当地规范适用性。
- GEO官方PDF：https://www.cedd.gov.hk/filemanager/eng/content_1024/ep1_2023.pdf （`turn18view4`、`turn22view0`、`turn22view1`；PDF索引72和105分别对应印刷73和106）。
- https://opensees.github.io/OpenSeesDocumentation/user/manual/model/elements/elasticBeamColumn.html （`turn11search3`）。
- https://opensees.github.io/OpenSeesDocumentation/user/manual/model/elements/zeroLength.html （`turn11search13`）。
- https://openseespydoc.readthedocs.io/en/latest/src/ElasticPP.html （`turn22view2`）。
- https://openseespydoc.readthedocs.io/en/latest/src/InitStrainMaterial.html （`turn21view1`）。
- https://opensees.github.io/OpenSeesDocumentation/user/manual/misc/remove.html （`turn21view0`）。
- https://openseespydoc.readthedocs.io/en/latest/src/loadConst.html （`turn25view2`）。
- https://github.com/OpenSees/OpenSees/blob/master/SRC/element/zeroLength/ZeroLength.cpp （`turn25view0`、`turn28view0`、`turn28view1`）。

## 3. 现有路径确实重复计入抗力

已读 `docs/releases/V3_87_16_CALCULATION_RELIABILITY.md`，以及 `services/api/app/calculation/stage_loads.py`、`stage_solver.py`、`wall_internal_force.py`。

- `stage_loads.py` 的标准荷载来自净土水压力，设计荷载明确为 `max(0,importance*(gammaG*A_G+gammaQ*psi*K*q-R))`，其中 `R=passive_soil+inside_water`。
- `stage_solver.py` 将这一profile原样传给全局和墙体弹簧求解。
- `wall_internal_force.py` 同时在坑底以下加入 `ks=m*(z-H)`，解 `EI*y''''+ks*y+support=q_net`。它又有自由头、自由趾边界，并以当前总位移恢复支反力，尚无安装参考位移或塑性历史。

因此新剖面不可复用旧净荷载作为驱动力。新求解器中的土弹簧已经产生完整土压力，`externalForcesKn`只允许水压力差和其他显式外力；不得再加入 `K0净土压力` 或 `主动减被动净土压力`。

旧 `_m_value` 还能从弹性模量、压缩模量经验换算或默认8000取得值。这些回退不进入新路径。新路径要求明确m系数及来源/单位，或直接给定节点刚度；缺项不视为通过。

## 4. 统一符号、单位和两侧压力

深度 `z` 从墙顶向下；墙横向位移 `y>0` 指向坑内；转角 `theta=dy/dz`。坑内侧符号 `s=+1`，坑外侧 `s=-1`，局部压入位移 `x=s*y`。每侧土压力 `p>=0` 为对墙压应力，土对墙的全局力方向为 `-s*p`。

按本阶段实际土面分别积分竖向应力，孔压另外计算：

`sigma'_v(z)=integral(gamma_total dz)+q-u(z)`。

在均质、水平地面、竖直光滑墙、有效应力条件下：

`Ka=tan²(45°-phi'/2)`，`Kp=tan²(45°+phi'/2)`；

`pa=max(0,Ka*sigma'_v-2*c'*sqrt(Ka))`；

`pp=Kp*sigma'_v+2*c'*sqrt(Kp)`；`p0=K0*sigma'_v`。

必须检查 `0<=pa<=p0<=pp`；不符合时拒绝数据/模型，不自动夹正p0。K0应有明确来源；若明确采用Jaky，仅是对应假设，不替代OCR、结构性等数据。首版宜限制 `c'=0`，否则需单独定义张裂缝、进水和地表黏聚项适用性。

节点离散将应力乘实际分担宽度和竖向积分权重，得到 `p0Kn/lowerKn/upperKn`（kN）及 `kKnPerM`（kN/m）。土与水分开：水差 `u_out-u_in` 直接积分到external；水无土塑性位移状态，也不随墙侧移卸载成主动压力。

### m系数与宽度

`kh=m*d`，其中 `m` 单位为 `kN/m^4`，`kh` 为 `kN/m^3=kPa/m`，`d` 为该侧地面以下深度。`kh*y` 才是kPa。弹簧节点刚度为 `K=kh*b_eff*tributary_height`，单位kN/m。

每侧地面不同：坑外 `d_out=z`，坑内 `d_in=max(z-H,0)`。坑内已挖土弹簧移除，不能保留上层土的埋深。应在分担区内积分刚度与压力，避免把地表点的零刚度赋给整个首格；分层界面、坑底、支撑、EI突变处应有节点。

排桩单桩：`EI_pile=E*pi*D^4/64`（乘明确折减因子）。线荷载通常由桩距 `s_pile` 分担，而土抗力宽度 `b_eff` 另需方法依据。不能把桩径、桩距、计算宽度相互替代。

等效每米墙：`EI_1m=EI_pile/s_pile`，土刚度和极限反力也必须除同一桩距。连续墙才可直接取 `I=t^3/12` 每米。首个可确认算例应冻结 `b_eff=s_pile` 的等效连续墙近似并标明适用边界；间隔桩三维绕流/土拱不因此已被证明。

## 5. 初始平衡、弹塑性历史和开挖

每个土弹簧保存已提交的局部塑性位移 `xpl_n`。一个事件内参数冻结，所有Newton试算均使用同一个 `xpl_n`：

`p_trial=p0_s+k_s*(s*y-xpl_n)`；

`p=clip(p_trial,pa_s,pp_s)`。

弹性域切线 `dp/dy=s*k_s`；位于任一屈服平台时切线为零。土弹簧对整体残差贡献 `s*p`，其弹性切线贡献为 `k_s`，两侧均为非负。

平衡成立后才提交：若发生塑性，`xpl_(n+1)=s*y-(p-p0_s)/k_s`；弹性时保留历史。失败阶段不得提交状态，不得把试迭代产生的塑性当成施工历史。

梁离散后的总平衡：

`K_beam*d + sum(Bj^T*s_j*p_j) + sum(Ba^T*R_a) - F_external = 0`。

初始对称两侧 `p0_out=p0_in`、同水位、同荷载时 `y=0`、弯矩/剪力/支反力=0自然满足平衡。若初始水位或外侧超载不对称，初始响应本来就不应强制为零。

下一开挖事件：移除已经开挖的坑内弹簧，幸存ID保留其材料坐标和 `xpl`，按现阶段覆盖土重更新 `p0/kh/pa/pp`。阶段参数改变在同一y下产生的压力改变属于显式定义的卸土参数路径。本模型没有推导连续土体卸荷K0演化；这一限制要随结果输出。

不能把新阶段每个弹簧xpl重置为零；不能在尚未收敛的内迭代中累计xpl；不能改变幸存ID的深度/侧别来“匹配”新网格。已消失ID再次出现需要明确定义回填/重激活，本版拒绝。

两侧线性可用域是此模型的特例 `pa<p_trial<pp`。若只做线性解而越界，结果必须标为域外。非线性实现则可进入平台，但仍须检查体系是否成为机构以及总体平衡能否满足。不能以微小正刚度、伪固定墙趾或荷载截断掩盖失稳。

## 6. 支撑和边界

弹性支撑安装时记录安装前已收敛位移 `y_ref`。零预力支撑：`R=K*(y-y_ref)`，R>0为对内移的抗力。压缩单向支撑：`R=max(0,K*(y-y_ref))`，卸载可脱开，再压合，不能用塑性零平台代替可重合间隙关系。

安装当步没有新增荷载且结构不变时，新支撑反力必须严格为零、原位移和原有内力不变。旧做法 `R=K*y` 会把安装前位移错误转成安装内力。

刚性换撑：`y=y_ref`，以约束反力恢复R；不可指定超大K。首版只允许双向刚性约束，单向刚性接触另需互补条件；同节点多个完全刚性支撑会导致各自反力不唯一，应拒绝或在更高层明确定义共享。

拆除事件删除该支撑的刚度或位移约束并重新平衡；它的已存在反力不能留作荷载。若采用总平衡重解，不再额外施加一次反向释放荷载。换撑必须先装新构件、求平衡，再拆旧构件；同步重置参考位置会抹掉荷载转移。

边界口径：自由墙顶、自由墙趾为M=V=0；嵌固土弹簧提供约束。明确固定趾为y=theta=0；铰趾为y=0且M=0。边界属于输入，不按计算是否收敛自动改变。

预加力本版拒绝。后续如支持实际锁定力P0，应先以千斤顶P0求位移y_jack，再在此位置安装带P0初力的弹簧并移除千斤顶力；直接写 `P0+K*(y-y_before)` 一般不能保持安装后锁定力正好为P0。

## 7. 独立OpenSees最小实现与已完成探针

项目锁定 `openseespy=3.8.0.0`、`openseespylinux=3.8.0.0`。现有Python为3.12.14；未在现有venv中安装，已将上述锁定版本独立安装至 `/tmp/pitguard-soil-opensees`。引擎实际返回 `3.8.0`。由于其MPI导入要创建本机socket，普通沙箱运行会直接终止，放行后的隔离运行成功。没有修改项目依赖文件。

使用2D `elasticBeamColumn`（3DOF，额外轴向自由度约束一致）、土的 `zeroLength`、`ElasticPP`、`InitStrainMaterial`，独立组装和独立历史记录。主程序不可读取主求解器的位移、矩阵、塑性状态作为OpenSees参考答案。

正压力上下限 `[pa,pp]` 的OpenSees实现：以 `f=p-pa` 为材料应力，取 `ElasticPP(K,(pp-pa)/K,0,eps0)`，其中从零节点位移重建时原生 `eps0=xpl_n-(p0-pa)/K`；pa作为同侧明确基准力进入外载。若zeroLength是在非零位移的节点上创建，还需按其自动保存的相对位移修正材料参考。基准力与增量弹簧是同一侧总压力的分解，不能额外再扣被动。

独立实现代理补充实测：土弹簧历史重建不能普遍用 `InitStrainMaterial` 代替原生 `ElasticPP eps0`。例p0=50、pa=20、pp=150、K=1000、xpl=.05、并联梁刚度1000、外力200，原生eps0=.02得到y=.10、p=100；InitStrain包装在构造时可能先触发限值并提交，得到y=.09、p=110。这一反例已改变独立适配器实现。初始无塑性的API探针仍成立；不要把它扩展为任意历史初始化已经正确。

`/tmp/pitguard_soil_api_probe.py` 已实跑：

|探针|手算/实测结果|
|---|---|
|原弹簧K=1000 kN/m，F=10 kN|y=0.01 m|
|此位移处新增K=500零预力zeroLength|y仍0.01 m，新支撑R=0|
|再增荷10 kN|y=0.0166666666667 m，新增支撑R=3.33333333333 kN|
|拆除新增支撑|y=0.020 m|
|千斤顶2 kN后锁定探针（不进入首版产品）|y_jack=y_lock=0.008 m，锁定R=2 kN|
|土侧pa=20,p0=50,pp=150,k=1000|局部位移依次[0,.05,.15,.10,-.10,-.05]m，对应压力[50,100,150,100,20,70]，屈服后的卸载/反向重载保留塑性历史|

材料探针在完全塑性时另有一个已知物理弹簧维持力控制稳定；没有对失稳体系作虚假正则。探针只证明API和本构映射，不证明工程输入或整墙结果。

## 8. 验收例与能力阻断

预先冻结容差与量纲，再观察结果：

1. 同土同水无差荷零开挖：完整y/theta/M/V、各成对土反力与支撑反力应相容；不能通过输出归零实现。
2. 解析固定趾悬臂顶力：`y=P L^3/(3EI)`，`|theta|=P L^2/(2EI)`，趾反力P、趾弯矩PL；校验DOF与端力符号。
3. 上表单弹簧循环：压力值和塑性位移由手算固定，校验正/负限值及卸载。
4. 零预力安装、后续增荷、拆除：安装不生虚假力，拆除不残留刚度或重复释放力。
5. 刚性换撑：安装参考位移完全不变，拆旧撑后新增刚性反力承接；无超大K。
6. m量纲与宽度：单桩/每米墙转换必须给出同一位移；EI/荷载/刚度/极限同倍缩放时位移不变、力同倍缩放。
7. 正压力和支撑反力累计后的总水平力、总力矩平衡；不只检查求解矩阵残差。
8. 开挖参数变化与同ID塑性历史；基准、主梁核心、OpenSees分别保存本支路历史并比较。
9. 土全部平台化、失去必要约束的机构/无解反例必须报告失败，不用pinv、对角正则、隐含toe fix生成“通过”。
10. 非零预力、未定义回填、缺有效土参数、非有限输入、桩距/有效宽度不明确等必须拒绝或保留工程适用性未确认。

首版不自动判通过：整体稳定、坑底隆起、管涌/承压水突涌、地表沉降及建筑影响、渗流/固结、三维转角和土拱、桩间土稳定、墙土摩擦、支撑屈曲和节点连接、裂缝和钢筋构造。数值求解、独立求解器一致和工程可发行性应保持不同状态。
