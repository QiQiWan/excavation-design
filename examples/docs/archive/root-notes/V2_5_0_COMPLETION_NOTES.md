# PitGuard V2.5.0 完成度闭合说明

本版在 V2.4.0 基础上继续推进剩余五项工程化边界：

1. issue 定位闭环：问题清单 locator 增加多视图 highlightTargets，并新增 /api/projects/{project_id}/issues/locate/{issue_id}。
2. 企业 CAD 标准：CAD 模板新增签审流程、图号规则、字体规则、线型规则、打印样式，并提供 /cad-template/validation。
3. 钢筋逐根几何：individualBars 增补 cageSegmentId、spliceZoneId、constructionJointId、bendRadius、cover 和 finalShopStatus。
4. 钢筋施工详图：新增施工缝/钢筋笼分节、吊装计划、搭接区、弯折半径、保护层冲突检查和签审清单。
5. CAD 图纸集：新增 S-09~S-12 图纸和 cage/splice/cover/signoff CSV 表，完整 CAD 包升级为 12 张图纸。

仍然保持规范算法路线，不接入有限元。软件模块闭环完成，正式盖章仍由项目设计单位完成专业签审。
