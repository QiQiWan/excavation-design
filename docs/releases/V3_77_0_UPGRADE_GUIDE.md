# PitGuard V3.77.0 升级与部署说明

## 升级影响

V3.77 对旧项目保持字段兼容，新字段均有默认值。升级后正式发行门禁更严格，历史项目可能从“可交付”变为“需补充证据”，这是预期行为。

默认新增设置：

- `formalIssueStrictMode=true`；
- `requiredFormalAnalysisLevel=L2`；
- `geotechnicalAnalysisLevel=nonlinear_spring`；
- `enableSixDofVerification=true`；
- `requireExternalBenchmarkForIssue=true`；
- `hazardousWorkClassification=unclassified`。

## 推荐升级步骤

1. 备份数据库、对象存储和运行时资格目录；
2. 替换后端与前端源码；
3. 安装 Python 和前端依赖；
4. 运行 V3.77 专项测试；
5. 在完整产品模式运行验证矩阵；
6. 重新构建前端，不沿用旧 `dist`；
7. 打开历史项目，确认分析等级、危大工程分类和法定证据缺项；
8. 重新计算并生成新的结果哈希；
9. 完成真实地质、水位、施工期资料和专业审签后再发行正式成果。

## 验证命令

```bash
PYTHONPATH=services/api pytest -q services/api/tests/test_v3_77_0_accuracy_compliance_workflow.py
PYTHONPATH=services/api python scripts/evaluate-v377-accuracy-compliance-workflow.py
PITGUARD_PRODUCT_MODE=full PYTHONPATH=services/api uvicorn app.main:app --host 0.0.0.0 --port 8002
npm --prefix apps/web ci
npm --prefix apps/web run test
npm --prefix apps/web run build
```

## 环境边界

- OpenSeesPy 不可用时，外部基准状态为 `unavailable`，正式发行保持阻断；
- npm 依赖安装失败时，应停止部署，不能继续使用旧版前端构建目录；
- 资格目录、签名哈希和法定证据必须由受控部署环境维护；
- 软件筛查不能替代责任主体对危大工程类别和专家论证范围的确认。
