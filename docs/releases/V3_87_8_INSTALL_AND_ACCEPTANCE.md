# V3.87.8 安装与验收

## 安装

```bash
cd ~/Desktop/designer
./stop-dev.sh 2>/dev/null || true
cd ..
mv designer designer_v3.87.7_backup
unzip PitGuard_V3.87.8_rebar_worker_wall_visibility_complete.zip
mv PitGuard_V3.87.8_rebar_worker_wall_visibility_complete designer
cd designer
chmod +x start-dev.sh start-linux-dev.sh start-macos-dev.sh stop-dev.sh scripts/*.sh
./start-dev.sh
```

## 验收 1：配筋 worker

1. 完成计算闭合。
2. 点击“一键修复并闭合配筋”。
3. 任务正常结束后，worker supervisor 会启动新进程。
4. 已完成任务不得变为 `External calculation worker restarted before task completion`。
5. 若进程真实异常退出，界面自动重试一次；第二次失败后保留真实错误和任务日志。

## 验收 2：地下连续墙完整性

1. 打开钢筋三维模型。
2. 查看“地下连续墙钢筋覆盖”。
3. `应显示` 与 `已显示` 数量应一致。
4. 若修复过退化轴线，应显示自动修复数量；仍缺失时显示墙编号。

## 验收 3：水平支撑钢筋

1. 进入“支撑配筋”。
2. 切换到“全部”。
3. 每根 RC 支撑应显示纵筋、端部/跨中箍筋、侧面构造筋、拉结筋和附加筋。
4. 在三维模型使用“仅看该支撑箍筋”，应能分别看到 A 端、跨中和 B 端。

## 验收 4：列表长度

各长列表默认显示 12 项；点击“展开全部”显示完整数据，点击“收起”恢复前 12 项。

## 已验证

- 受影响的后端回归测试：40 项通过。
- Worker 恢复和支撑箍筋专项测试：17 项通过。
- 修改后的 Python 模块编译通过。
- 修改后的 TSX 文件 TypeScript 语法转译通过。
- 由于打包环境缺少 `ezdxf`，依赖 CAD/DXF 的两组测试未能收集；目标环境依赖清单仍包含 `ezdxf`。
