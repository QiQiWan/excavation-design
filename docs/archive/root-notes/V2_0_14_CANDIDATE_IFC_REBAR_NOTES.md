# V2.0.14 候选方案多样性、施工图 IFC 可视化与钢筋状态说明

## 1. 支撑候选方案近似度问题

V2.0.13 仍会出现多个候选方案视觉上接近、A/B/C 完整计算结果完全一致的问题。根因在 `support_layout.py` 的 `_main_support_count`：`target_spacing` 使用了 Python 函数默认参数 `TARGET_MAIN_SUPPORT_SPACING_M`。默认参数在函数定义时绑定，优化器后续临时修改全局 `TARGET_MAIN_SUPPORT_SPACING_M` 不会生效，导致 3.5、4.0、5.0、6.0m 等候选分仓实际仍按同一个 5.0m 规则生成。

V2.0.14 已修复为运行时读取当前目标分仓，并调整候选排序策略：优先保留支撑数量、立柱数量、最大分仓、最大跨长存在结构差异的候选，再补充线位微调候选。前端同步增加“基准 / 明显差异 / 中等差异 / 高度相似”标识，避免把微小线位扰动当作正式方案比选。

## 2. 施工图级 IFC 可视化问题

上传的 design_detailed IFC 文件基础 STEP 结构可用：未发现未定义引用、raw unicode、零尺寸实体或空间归属缺失；文件中包含 IFCWALL、IFCBEAM、IFCCOLUMN、IFCPLATE、IFCREINFORCINGBAR 和 IFCBUILDINGELEMENTPROXY。问题更可能出现在下游 Viewer 对 IFC4 详细钢筋、承压板、代理构件和大量属性集的支持差异。部分轻量 Web Viewer 或 Revit/Navisworks 导入链会跳过 IfcReinforcingBar，或在详细钢筋/代理构件混合模型上只显示部分构件。

V2.0.14 新增 `construction_visual` 导出模式和 `/api/projects/{id}/export/ifc-construction-visual` 接口。该模式保留墙、梁、支撑、柱、承压板、节点、预埋件和钢筋参数属性，但将代表性钢筋几何输出为 viewer-safe 的 IfcBuildingElementProxy，以优先保证可视化；正式 BIM 语义审查仍使用 `design_detailed`。

## 3. 钢筋状态

当前系统已经植入钢筋信息，但属于“参数化配筋 + 代表性钢筋组”阶段，不是最终施工详图级逐根钢筋建模。地连墙和支撑可导出代表性钢筋实体或可视化代理；围檩/冠梁、节点附加筋、承压板和预埋件主要以属性集和局部代理构件表达。后续施工图级深化仍需增加梁柱箍筋/主筋逐根布置、钢筋锚固长度、搭接、弯钩、保护层、节点锚筋、桩基钢筋笼和钢筋编号出图。

## 4. 验证

- 后端 `compileall` 通过。
- 前端 `npm run build` 通过。
- 前端 `vitest` 3 项通过。
- 候选优化烟囱测试显示：修复后可生成支撑数量、立柱数量和分仓间距不同的候选。
- `construction_visual` IFC 导出接口返回 200，IFC 自检通过，`IFCREINFORCINGBAR=0`，钢筋以可视化代理表达。
