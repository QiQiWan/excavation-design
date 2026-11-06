# 冻结剖面前端入口验证

验证日期：2026-10-25 UTC。此记录属于 V3.87.17 实施期间的前端与真实 HTTP 验证，供最终集成复核。

## 实现范围

- 计算页的“剖面结果验证”按钮展开独立面板，初始为空；加载合成验证例、导入完整输入或冻结记录均需显式操作。面板不把当前 Project 或 A3-3 的未知参数转换成输入。
- 核心几何、材料刚度、抗力方法、土层与配筋输入、完整阶段表可复核；原始 JSON 编辑与身份信息放在可展开区域。
- 输入编辑立即清除快照、结果及下载身份。请求期间编辑后，迟到的旧响应不会恢复结果。导入的快照记录仅提取输入，计算前由服务端重新冻结。
- 阶段结果显示位移、单桩特征弯矩、剪力、支点水平反力及控制阶段。缺失/null 显示“未计算”；四类比较保留真实状态；工程状态显示 review_required。
- 运行请求仅发送服务端返回的 snapshotHash；成果包仅按服务端返回的 resultHash 下载。客户端检查结果与冻结记录的 snapshotHash、analysisHash 和 sectionId。
- 新请求复用现有认证客户端。最终审查后，分析总预算为 180 秒，ZIP 导出为 120 秒，其余剖面 JSON 请求保持 90 秒。Blob 分支保留 ZIP 原始字节；错误分支继续按 JSON/文本解析并触发既有未登录事件。输入校验错误展示字段路径及原因。

## 先失败、再实现

新面板和 API 方法未实现时，新增测试因缺少组件及方法失败；工作台入口测试随后因找不到“剖面结果验证”按钮失败。另补的真实 422 字段校验测试先复现原客户端只显示“422”的问题，修复后显示具体字段和错误原因。

最初一轮面板、客户端和工作台共 19 项测试通过。补齐 422 错误、当时的 90 秒超时、JSON 缓存回归，并纳入既有全局请求进度测试后，该阶段聚焦结果为 **4 个测试文件、24 项全部通过**。最终审查后的超时修复记录见下节。

工作目录：`apps/web`。

```bash
./node_modules/.bin/vitest run src/components/FrozenSectionPanel.test.tsx src/api/client.section.test.ts src/pages/CoreProjectWorkspace.test.tsx src/app/GlobalRequestProgress.test.tsx
./node_modules/.bin/tsc --noEmit
git diff --check
```

最终输出：Vitest 4 files / 24 tests passed，约 2.50 秒；TypeScript 与 diff 检查退出码均为 0。覆盖输入失效、迟到结果、缺失值、返回身份、快照导入、下载字节、401、422、超时和既有 JSON 缓存的读取/失效。生产构建及完整项目回归由集成步骤另行记录。

## 最终审查 P2：分析超时与结果恢复

独立子进程本身可使用 90 秒，前端原 90 秒总预算不足以覆盖整个分析链。修复将分析总预算改为 180 秒，并在超时提示“计算可能仍在执行，可恢复该冻结输入最近已保存结果”。冻结身份保留；用户可通过“恢复已保存结果”读取 `GET /api/sections/snapshots/{snapshotHash}/latest-result`，不会自动再次计算。

恢复路径核对 snapshotHash、analysisHash 和 sectionId，沿用编辑失效与迟到响应保护。恢复成功明确提示最近已保存结果，下载使用该恢复结果返回的 resultHash。404 或身份错误保持可见，不自动轮询或重算。

先增加行为测试，实测旧实现有 9 项失败；修复后执行受影响测试与类型检查：

```bash
./node_modules/.bin/vitest run src/components/FrozenSectionPanel.test.tsx src/api/client.section.test.ts
./node_modules/.bin/tsc --noEmit
```

结果为 **2 个文件、21 项全部通过**，约 2.18 秒；类型检查退出码 0。包含 180 秒分析边界、120 秒导出与普通读取 90 秒边界、超时保留冻结身份、恢复结果身份用于下载、三类身份不符拒绝、编辑后迟到恢复丢弃及恢复失败不自动重算。

随后对新增恢复接口补充完整应用 HTTPS Cookie 实测，独立保存为 `frontend_http_recovery_verification.json`，原 `frontend_http_verification.json` 保持原样。冻结后、结果保存前访问 latest-result 返回 404；保存后返回 200，完整结果与按 resultHash 读取的结果一致。随后 ZIP 下载及 manifest 身份核对通过，全链约 6.94 秒。脚本为 `/tmp/pitguard_section_frontend_http_recovery.py`，运行方式与下述原脚本相同。

本次恢复验证的 snapshotHash 和 analysisHash 与原记录相同；当前源码与比较记录下的 resultHash 为 `f0ae5992a341be1385b4a5156e4cef9f394134ad74481c06bc52dab2ff99df81`，ZIP 为 1,296,386 字节，SHA256 为 `23b3229c0737a32265ddcfefb59288482929350c40fde039c1896178ba62a796`。该记录是一次新的验证和打包，不能与下述原记录视为同一包。新增证据也不包含账户密码、Cookie 或登录响应。

## 真实 HTTP 验证

同目录 `frontend_http_verification.json` 为完整应用 TestClient 的实测记录，使用隔离临时账户、HTTPS Secure Cookie 和临时不可变存储，未绕过认证中间件。验证了未登录 401、登录、获取验证例、无效输入 422、冻结、计算、读取持久结果和下载 ZIP。证据不包含账户密码、会话 Cookie 或登录响应。

复现命令（本次临时脚本位于 `/tmp/pitguard_section_frontend_http.py`）：

```bash
cd services/api
PYTHONPATH=.:/tmp/pitguard-soil-opensees /workspace/scratch/fd5d8a28c64e/work/.venv/bin/python /tmp/pitguard_section_frontend_http.py
```

此命令在允许本机 socket 的执行环境运行，以支持异步线程唤醒与 OpenSees/MPI 初始化；未修改 API 的异步实现。首次使用 HTTP 测试基地址时，默认 Secure Cookie 被正常限制；换为 HTTPS 测试基地址后完整链通过，约 8.19 秒。

实测 6 个阶段均 success；hand、independent、mesh 为 passed，original 为 incomplete；engineeringStatus 为 review_required，formalDesignReady 为 false。返回 ZIP 为 1,294,259 字节，含 17 个文件，ZIP CRC 检查通过，manifest 的三类身份均与 HTTP 返回记录一致。

| 身份 | 本次验证值 |
| --- | --- |
| snapshotHash | `2223478185afda316bc8fc9585a4998cf4900f88108b94337ef064f9f8a989fd` |
| analysisHash | `bc4ef3904cb3669e7f72b94080fe00614d343332af6849e1c9744a8035bb052a` |
| resultHash | `016d7dbcfbba405d0e72b1576088d2cc7484bd12d683e20997cb96afe3ce4ad1` |
| 本次 ZIP SHA256 | `d5163d7883875dd1e845b122a5cc04b35f38a06f7a445107abf2acac9f0d38d6` |

这些身份指向本次验证时的源码和比较记录。最终发布前若实现哈希或比较记录更新，重新封装结果会产生不同 resultHash，ZIP 哈希也可能改变；本记录不证明不同身份的最终包与本次临时包是同一个文件。最终成果包需使用其自身 manifest 和校验和。

本次以 DOM 行为测试、真实 HTTP/ZIP 链及类型检查验证入口；未声称完成浏览器截图视觉验收。
