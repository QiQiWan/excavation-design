# PitGuard V3.87.5 安装与验收

## 安装

建议先停止并备份旧工程：

```bash
cd ~/Desktop/designer
./stop-dev.sh 2>/dev/null || true
cd ..
mv designer designer_v3.87.4_backup
unzip PitGuard_V3.87.5_relative_intervention_optimization_search_complete.zip
mv PitGuard_V3.87.5_relative_intervention_optimization_search_complete designer
cd designer
chmod +x start-dev.sh start-linux-dev.sh start-macos-dev.sh stop-dev.sh scripts/*.sh
./start-dev.sh
```

访问：

- 前端：`http://127.0.0.1:5173`
- 后端健康检查：`http://127.0.0.1:8002/health`
- API 文档：`http://127.0.0.1:8002/docs`

## 验收一：连续相对加固

1. 打开存在墙体安全储备缺口的计算结果；
2. 记录目标墙段当前墙厚和安全系数；
3. 点击“按当前值递增并复算”；
4. 检查“最近一次有效修改”显示前值、后值；
5. 再次点击同一墙段；
6. 第二次后值应继续增大，不能回写第一次的绝对建议值；
7. 检查计算合同、输入快照哈希、结果哈希和配筋更新时间均已刷新。

## 验收二：一键优化并复算

1. 在计算阻断解决中心点击“一键优化并复算”；
2. 任务进度应逐个显示候选完成状态；
3. 结果应显示已评估候选数、可闭合候选数、采用策略和材料代理增量；
4. 候选列表应显示排名、硬失败、定量缺口和最大位移；
5. 正式工程应只采用排名第一的候选，并生成新的计算结果；
6. 配筋方案应按最终计算包络刷新；配筋引起截面变化时应发生附加复算。

## 验收三：工程边界

确认自动搜索没有修改以下数据：

- 土层参数；
- 地下水位；
- 外部荷载；
- 测量坐标；
- 已锁定施工阶段；
- 已锁定墙趾。

达到墙厚、梁截面或支撑深化自动上限时，系统应停止继续放大并给出专项设计提示。
