# PitGuard V3.87.4 安装与验收

## 安装

本包为完整源码包。建议先停止并备份旧目录，再整体替换，不要把此前的启动补丁继续叠加到新目录。

```bash
./stop-dev.sh 2>/dev/null || true
cd ..
mv designer designer_v3.87.3_backup
unzip PitGuard_V3.87.4_resilient_design_closure_complete.zip
mv PitGuard_V3.87.4_resilient_design_closure_complete designer
cd designer
chmod +x start-dev.sh start-linux-dev.sh start-macos-dev.sh stop-dev.sh scripts/*.sh
./start-dev.sh
```

默认地址：前端 `http://127.0.0.1:5173`，后端 `http://127.0.0.1:8002`。

## 验收 1：设计基准保存

1. 从项目列表进入工程，URL 应为 `/projects/<project-id>`。
2. 在“设计基准”页滚动至任意位置并点击“确认并应用设计基准”。
3. 保存后仍位于当前工程、当前“设计基准”阶段和原滚动位置，不返回项目列表。

## 验收 2：钻孔导入

1. 打开“工程输入 → 地勘数据”。
2. 上传 CSV/XLSX/XLSM。
3. 页面显示后台任务进度，并可取消；主工作台保持响应。
4. 导入成功后刷新钻孔和地层，旧地质模型及旧计算证据被标记失效。

默认保护参数：50 MB、100000 行、128 列、单次解析 600 秒、暂存文件 24 小时清理。均可通过环境变量调整。

## 验收 3：计算自修复

1. 点击“计算当前方案”或“自动诊断、修复并复算”。
2. 系统先修复可确定的几何、地质覆盖和拓扑问题，再运行验算—优化—再验算。
3. 第一阶段后仍有可安全处理的结构超限时，自动执行有限数量的墙趾加深、局部截面增强或支撑深化，并进入第二阶段复算。
4. 测量坐标、土层参数、水位、锁定施工阶段和专项结构决策只生成明确的人工处理项，不静默修改。
