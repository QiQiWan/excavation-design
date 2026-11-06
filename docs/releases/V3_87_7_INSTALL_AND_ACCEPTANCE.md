# V3.87.7 安装与验收

## 安装

```bash
cd ~/Desktop/designer
./stop-dev.sh 2>/dev/null || true
cd ..
mv designer designer_v3.87.6_backup
unzip PitGuard_V3.87.7_transfer_path_auto_recovery_complete.zip
mv PitGuard_V3.87.7_transfer_path_auto_recovery_complete designer
cd designer
chmod +x start-dev.sh start-linux-dev.sh start-macos-dev.sh stop-dev.sh scripts/*.sh
./start-dev.sh
```

## 旧项目验收

1. 打开原丰收湖项目。
2. 进入“计算验算”。
3. 点击“一键计算、优化并闭合”。
4. 检查“自动闭合搜索结果”：
   - 已评估候选应大于 0；
   - 不应直接因旧支撑 ID 返回空结果；
   - “自动恢复阶段”应显示实际数量；
   - 标准软件生成路径应显示“换撑序列重建：是”或完成语义映射；
   - 结果应为 `closed`、`calculated_pending_transfer_review` 或 `cannot_close`。
5. 若状态为 `calculated_pending_transfer_review`，点击“定位并确认换撑工况”，确认退出支撑层、永久结构生效条件和顺序后保存，再执行一次闭合。

## 日志验收

```bash
grep -E "恢复.*施工阶段|标准换撑序列|优化搜索完成|当前拓扑筛查" runtime/logs/*.log pitguard_*.log 2>/dev/null
```

诊断事件应分别记录实际施工阶段数和阶段—墙段结果数，不再把 140 条结果误记成 140 个阶段。
