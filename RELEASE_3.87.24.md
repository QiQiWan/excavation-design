# PitGuard V3.87.24 - 新增工程研究接口与核验范围

## 新增/修改目录

- `services/api/app/engineering/single_braced_pile/normative/hydraulic_assurance.py`：非均质渗流的双网格、渗流出逸和水量守恒证据；不执行正式异质地层合格认定。
- `.../normative/crack_scope.py`：圆截面在已确认开裂等级与SLS组合下的未开裂拉应力核算。III类宽度保留未计算。
- `.../normative/groundwater.py`：复用既有附录C均质核验；非均质另返回 assuranceStatus；既有status合同不变。
- `.../normative/reinforcement.py`：带有轴压的零轴力保守抗剪下界参考，保留范围外标记。
- `.../real_project/terrain_pressure.py`：坡台折线填土竖向附加应力高斯积分，远侧无限平台解析积分。不是规范坡面主动楔体方法。
- `.../real_project/bracing_compatibility.py`：对侧墙弹簧/支撑EA-L的独立双端相容性核算，未建完整三维支撑体系。
- `.../real_project/boundary_evidence.py`：原项目证据与完整曲线的严格协议、非有限和重复节点拒绝；未取得原生数值数据时不计算RMSE。
- `.../real_project/luoyu_three_three.py`：保留显式桩端、冠梁映射、分层饱和重度假定；五阶段条件化研究模型。
- `apps/web/src/features/single-strut/OneStrutWorkbench.tsx`：裂缝等级与SLS轴力动作编辑、原项目边界记录入口。

## 本版审查门禁

- 非均质稳态渗流对比与保守筛查可计算；正式规范合格状态 **禁止** 由筛查升级。
- 圆形桩I/II类裂缝仅在规范允许的组合及适用前提下进行未开裂截面拉应力校核；III类宽度不自动填值。
- 带轴压的抗剪仅能给出 N=0 保守下界筛查，不能证明圆桩轴剪承载力满足规范。
- 螺屿村坡台侧压力采用弹性竖向附加应力投影，严格分层强度口径；尚需规范可比的土压力计算模型。
- 冠梁由 22.7 MN/m² 映射单桩刚度需确认；桩底约束和两端墙位移/刚度不明，不得宣称同条件复现。
- 原项目 PDF 含图像与峰值，未给原生节点数组，完全阶段曲线比较保留阻断。
- 运行包只包含前后端源码、配置、工具和必要静态资产，研究验证数据独立打包。

## 接口边界

- GET `/api/engineering/one-strut/research/luoyu-3-3/boundary-register`
- POST `/api/engineering/one-strut/research/luoyu-3-3/compare-curves`
- 前者返回原报告证据、待确认参数、来源页码；后者要求逐点数字数组的单位、桩宽口径、符号和坐标基准一致，无法一致时拒绝RMSE。

## 运行说明

```bash
cd services/api
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

运行环境必须安装 `services/api/pyproject.toml` 指定的依赖；本次不包含数据缓存、用户项目数据库、测试资料及字体文件。
