# PitGuard V3.87.28：P4 工作版成果完整性与离线可信验签

## 版本及工程适用范围

本功能作为 V3.87.28 完整运行源码的一部分，针对成果包原有的两个字节清单，补充独立 Ed25519 签名和公钥验签，并保留 P1—P4 的工程状态。能够证明的结论是：`manifest.json`、`original_export_manifest.json`、验收阶段登记和全部成果文件的字节没有发生未经察觉的改变；验签后还可证明签名来自**事先通过可信渠道绑定并保存的公钥**所对应的私钥。

签名**不能确认**场地水位的真实性、设计方法是否适用、力学计算是否正确、校审人员是否具备设计资质或正式施工图是否可发行。该版本的 `formalConstructionIssue` 始终为 `false`。签名记录的 UTC 时间是签名者声明时间，未连接可信时间戳服务。

## 运行前提

- 一份 V3.87.28 的完整运行源码，包含 `services/api/app/engineering/single_braced_pile/assurance/dossier_verify.py`。
- Python 3.11 或更高版本。
- 离线安装或预装 Python `cryptography>=44,<47`；若仅执行 ZIP 字节审计，可以不安装 cryptography。
- P4 接口实际导出的 `P1_P4_..._review.zip`，包含两级清单与 `P1_P4_gate_index.json`。该工具与手工填写的数值参考资料无关。

## 集成安装

本模块已经包含在 `PitGuard_3.87.28_Integrated_Source.zip` 中，不需要额外应用独立补丁安装器。建议将完整源码解压到**新目录**，并根据实际部署迁移数据库与业务数据；不要把测试源码直接覆盖正在运行的服务。

## 运行与验签

在 PitGuard 运行源码根目录：

```powershell
# 1. 独立复核 ZIP 的两层清单、全部条目哈希及冻结输入/封存结果/阶段校审的规范化身份。
.\RUN_P4_DOSSIER.bat audit "D:\review\P1_P4_review.zip"

# 2. 管理员在离线签署机器安装依赖，使用强口令加密私钥。
python -m pip install "cryptography>=44,<47"
$env:PITGUARD_SIGNING_PASSWORD = '<在受控终端中输入的强口令>'
.\RUN_P4_DOSSIER.bat keygen --private-key "D:\keys\reviewer-private.pem" --public-key "D:\keys\reviewer-public.pem"

# 3. 私钥保留在离线签署机器，不上传至 Web 服务器。
.\RUN_P4_DOSSIER.bat sign "D:\review\P1_P4_review.zip" --private-key "D:\keys\reviewer-private.pem"

# 4. 收件人持有经过独立可信渠道确认的 reviewer-public.pem，在另一台计算机上验签。
.\RUN_P4_DOSSIER.bat verify "D:\review\P1_P4_review.zip" --trusted-public-key "D:\trusted\reviewer-public.pem"
Remove-Item Env:PITGUARD_SIGNING_PASSWORD
```

`sign` 命令只写一个独立的 `P1_P4_review.zip.sig.json`，绝不修改 ZIP 内容。签名封套采用 Ed25519、SHA-256、UTF-8 JSON 确定性序列化，并把压缩包名称、完整字节摘要、大小、冻结快照、封存结果、校审记录、签名时间及公钥标识一起签署。只接受单独提供的可信公钥，绝不从签名文件自动导入公钥。

Linux/macOS 可直接用 `python services/api/app/engineering/single_braced_pile/assurance/dossier_signature.py <子命令>`，不需要 BAT。

## 返回状态与阻断

`audit` 可以得到 `archive_byte_integrity_verified_source_authenticity_unverified`；这只证明归档自洽，没有验证谁制作了归档。

`verify` 成功后状态为 `archive_and_trusted_signer_verified_working_copy_only`，并包含 `signatureVerified=true`、`trustedKeyId`、归档 SHA-256、快照/结果/校审身份以及 P1—P4 阶段缺项。以下三个字段始终为 `false`：

- `formalConstructionIssue`
- `engineeringComplianceVerified`
- `sourceFilesIndependentlyAuthenticated`

任意失败都返回非零退出码，包括归档截断、路径穿越、重复文件、两层清单与实际文件不一致、SHA-256 不一致、阶段身份矛盾、正式签发标志异常、错误签名、非可信公钥和签名内容篡改。

## 测试及后续工作

```bash
pytest -q verification/tests/test_dossier_signature.py
```

本版经独立合成负例、真实冻结剖面同源成果及 FastAPI 接口回归；具体验证项目数见 `RELEASE_3.87.28.md` 和交付清单。前端 npm 生产构建仍须在具备锁定依赖的环境中执行。本功能没有读取原工程五阶段数据，没有改变水力或圆桩力学求解内核，也不宣称通过 P1、P2、P3 工程验收。

建议生产发布时在独立机器上保存经过组织确认的公钥指纹、建立密钥轮换和吊销登记、采用可信时间戳服务（如有监管要求）、由不同校审角色分别审查工程结论和最终签发；现场原始资料本身仍须做来源认证。
