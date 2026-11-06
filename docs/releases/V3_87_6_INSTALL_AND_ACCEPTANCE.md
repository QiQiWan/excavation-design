# V3.87.6 安装与验收

## 安装

```bash
cd ~/Desktop/designer
./stop-dev.sh 2>/dev/null || true
cd ..
mv designer designer_v3.87.5_backup
unzip PitGuard_V3.87.6_unified_auto_closure_complete.zip
mv PitGuard_V3.87.6_unified_auto_closure_complete designer
cd designer
chmod +x start-dev.sh start-linux-dev.sh start-macos-dev.sh stop-dev.sh scripts/*.sh
./start-dev.sh
```

## 验收 1：按钮合并

进入“计算验算”，确认只显示“**一键计算、优化并闭合**”一个主计算按钮。

## 验收 2：进度稳定

点击按钮后确认：

- 按钮立即进入“计算处理中…”；
- 不能重复提交；
- 进度不会倒退；
- 当前候选名称和阶段持续更新。

## 验收 3：旧支撑引用修复

旧项目第一次运行时，任务日志应出现：

`已按当前支撑标高修复 N 个常规设计控制工况的失效支撑引用`

不应再出现 52/104/156 根旧支撑 ID 阻断。

## 验收 4：体系搜索

优化结果应最多显示七个候选，其中包括：

- 平面支撑加密；
- 增设控制支撑层；
- 支撑层与截面联合增强。

## 验收 5：明确结论

计算通过时显示“计算已闭合”。

计算仍不通过时显示“在当前自动优化边界内无法计算闭合”，并显示控制项代码。

换撑或拆撑工况无法自动映射时显示“需要人工确认”。
