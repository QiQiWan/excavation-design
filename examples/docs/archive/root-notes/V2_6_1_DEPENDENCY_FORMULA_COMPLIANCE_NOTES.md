# V2.6.1 Dependency, Formula and Compliance Display Fix

## 修复范围

1. 移除 `ProjectWorkspace.tsx` 对 `katex/dist/katex.min.css` 的直接导入，避免用户本地 `node_modules` 未同步时 Vite 启动失败。
2. 公式显示采用内置数学表达组件，将 `gamma_0 * gamma_F * envelope(M_stage)` 等程序公式显示为 `γ₀ · γF · env(Mstage)` 一类数学表达。
3. Windows/Linux 一键启动脚本增加前端依赖完整性检查。若 `node_modules` 存在但缺少 vite、react、three、typescript、zustand 或 @vitejs/plugin-react，会自动执行 `npm install`。
4. 计算追溯链从普通表格升级为条文对比表，集中显示判定、对象/工况、校验项、需求—限值、利用率、公式和规范条文。

## 工程边界

当前仍采用规范算法，不接入有限元。合规性状态来自规范筛查链，正式出图仍需工程师复核项目参数、工况假定和条文适用性。
