# V2.0.13 计算反馈、候选去重与内力包络可视化

## 修复的问题

1. 一键计算校核点击后缺少即时反馈。  
   前端现在为一键计算校核显示分阶段任务面板：生成施工工况、运行结构与稳定计算、A/B/C 候选完整比选。每一步都有状态和进度条。

2. Step 6 顶部“已生成施工工况 / 已运行计算 / 已输出校核结果”误导用户。  
   这些标签原来只是静态 required 文案。V2.0.13 已改成真实状态：未运行计算时显示空心状态，运行完成后才显示勾选。

3. 候选支撑方案看起来过于相似。  
   后端候选优化器现在计算支撑几何指纹和几何差异分数，自动隐藏几何重复候选。新增 global shift、center gap、alternating escape 等支撑线变量策略，避免只给出评分接近但图形几乎一致的方案。

4. 关键部件内力结果缺少可视化。  
   计算结果页新增墙体弯矩/剪力/位移曲线、围檩弯矩/剪力/挠度包络曲线、支撑轴力包络条形图。

5. IFC / DOCX / JSON 下载缺少生成过程提示。  
   导出卡片已从直接链接改为前端受控下载，显示“提交导出请求、后端生成、浏览器准备下载、已生成文件”的状态和进度条。

## 主要改动文件

- `apps/web/src/pages/ProjectWorkspace.tsx`
- `apps/web/src/viewers/ResultViewer.tsx`
- `apps/web/src/app/styles.css`
- `services/api/app/services/support_layout_optimizer.py`
- `services/api/app/main.py`
- `services/api/pyproject.toml`
- `apps/web/package.json`

## 仍建议后续推进

- 将计算、IFC、DOCX 生成从同步 HTTP 请求升级为后端任务队列，并提供 `/api/tasks/{id}` 轮询接口。
- 将内力包络图升级为可切换构件、可查看控制工况、可导出 PNG/SVG 的图表模块。
- 将候选方案的支撑线差异从缩略图升级为主平面图叠加层，支持按支撑层、支撑角色、出土通道过滤。
