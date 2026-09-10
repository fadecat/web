# Phase 5.1 接管门禁修复 验收记录

> 依据计划: `docs/superpowers/plans/2026-09-09-phase-5-1-adoption-gate-repair.md`（第六轮 review 裁决「不达标」后的修复轮）。
> 本文档只记录本次验收的原始证据，不修改第五阶段原文。

## 1. 门禁命令（顺序执行）

- **开始时间**: 2026-09-09 21:08 (GMT+8)；**结束时间**: 2026-09-09 21:33 (GMT+8)
- **仓库完整 SHA**: `79bcfd5161a16d60a63aa30952ea5d047a4a62ce`（分支 main）

| # | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 1 | `python -m pytest tests -q -p no:cacheprovider` | 0 | **224 passed**, 2146 warnings（均为 sqlalchemy/starlette DeprecationWarning，无资源清理 warning） |
| 2 | frontend `pnpm test`（node --test 先、Vitest 后，顺序执行） | 0 | Node 断言 **34 ok**；Vitest **32 passed (5 files)**，无 Unhandled Errors |
| 3 | frontend `pnpm build`（与测试顺序执行，未并行） | 0 | built in 17.98s |
| 4 | `git diff --check b4c51b4..HEAD` | 0 | 无输出 |

执行环境说明: 本机运行 pytest 前以空值中和 `CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR` /
`CODEBUDDY_TOOL_CALL_ID` 两个环境变量，规避宿主 safe-delete shim 在会话内累积删除计数
后对 `.test-artifacts` teardown 抛错的环境噪声（见 Task 3/4 报告；非被测代码行为）。

## 2. R6-01～R6-11 逐项关闭

| 编号 | 反例（修复前行为） | 修复 commit | 测试名 | 修复后结果 |
|---|---|---|---|---|
| R6-01 源 0001/副本 9999 返回空差异 | 旧 verify_restore 忽略 alembic_version，错误返回 [] | e10f66e | `TestMigrationRevision`（同 revision/无版本表通过；0001 vs 9999 失败；接管特例仅显式参数通过） | 报「migration revision 不一致」 |
| R6-02 NUL 后文本漏检 | quote() 截断 NUL 后内容，源 `a\0x` vs 副本 `a\0y` 返回 [] | e10f66e | `TestContentDigest`（NUL/TEXT-INTEGER/BLOB 尾字节/分隔符碰撞 4 例） | 均报「内容摘要不一致」 |
| R6-03 自证 stamp 测试 | 删除编排保护后两条测试仍通过 | 79bcfd5 | `TestAdoptionOrchestration`（5 个失败分支: verify/compare/stamp/upgrade 失败→下游 0 次调用）+ `test_drifted_database_never_calls_stamp` / `test_extra_table_database_never_calls_stamp`（真实编排入口, stamp/upgrade/smoke=0） | 编排入口 fail closed |
| R6-04 隔离应用冒烟缺失 | 只 sqlite3 查一行, 无 FastAPI/路由 | 79bcfd5 | `test_unversioned_database_copy_can_be_adopted`（真实 TestClient lifespan → `/api/health` + `/api/settings` 命中副本 `app_setting[smtp_host]`） | 最终状态 smoke_passed |
| R6-05 diff check 失败 584 行 | conftest CRLF 被判 trailing whitespace | 373fa0d | `git ls-files --eol tests/conftest.py` = `i/lf w/lf` | diff check 退出 0 |
| R6-06 唯一约束/列序弱测试 | 删的是非唯一索引; 单列换单列 | e17c416 | `test_missing_unique_constraint_detected`（重建表省略 uq_cb_snapshot_bond_date, 只认「唯一约束」差异）、`test_composite_index_column_order_detected`（ix_quote_idx_date 列序反转, 断言两侧列顺序） | mutation check: 忽略列序/跳过唯一约束两种错误实现均被杀死 |
| R6-07 清理绑 Windows/裸 raise | 非 nt 分支 `RuntimeError: No active exception`；ctypes 绕过宿主删除 | 373fa0d | `tests/test_artifact_cleanup.py`（nt/posix 参数化: `__cause__` 保留最后 PermissionError, 不 import ctypes；os.rmdir 目录路径） | 标准库 3 次重试, `raise OSError(...) from last_error` |
| R6-08 CLI 文档/文件名/错误码 | runbook 写 `web.db.<ts>.db` 实际生成 `web.<ts>.db`；`--list` 文档可单独运行但需参数；目录拼错返回 0 | f2395f7 | `TestBackupAtomicPublish::test_list_missing_directory_fails` 等 | runbook/README/模块头统一真实格式; list_backups 错误路径返回 2 |
| R6-09 失败残留伪可用备份 | 检查与创建间竞争; 中途失败留部分文件 | f2395f7 | `test_backup_failure_leaves_no_target`（注入失败→目标/临时文件均不存在）、`test_concurrent_same_destination`（并发同 dst 恰一个成功, 另一个 FileExistsError） | os.open(O_CREAT\|O_EXCL) 原子占位, 失败删占位文件; 后置校验失败改名 .failed 不进 --list |
| R6-10 验收数字冲突 | 204 passed 不可复现; 残留数字无采集方法 | 373fa0d | docs/2026-09-09-phase-5-verification.md §7「第六轮复核更正」 | 原错误记录保留, 更正注明; 本次残留统计见 §3 |
| R6-11 dispose 后用 Inspector | finally dispose 后才 get_table_names | 373fa0d | tests/test_migration_baseline.py 两测试改为 dispose 前取表名 | 全量测试无资源清理 warning |

## 3. 残留目录统计（可复现采集命令）

采集命令（Python 标准库，逐目录 try listdir）:

```python
import os
root = ".test-artifacts"
readable = denied = with_files = empty = 0
for name in sorted(os.listdir(root)):
    try:
        entries = os.listdir(os.path.join(root, name))
        readable += 1
        with_files += 1 if entries else 0
        empty += 0 if entries else 1
    except OSError:
        denied += 1
```

**结果: total=94, readable=80, denied=14, 含文件=78, 空目录=2**（其中 91 个 `case_*`
目录为历轮残留；本计划新增测试的 teardown 已能正常清理，denied 目录为宿主删除
保护导致的宿主侧残留，非测试句柄泄漏——正常单文件级删除均成功）。

## 4. 裁决

- 普通恢复验证拒绝任意 revision 差异；未版本化源库→0001 仅显式 `--expected-backup-revision` 允许。✅
- 内容摘要覆盖 NUL/BLOB/类型/分隔符碰撞。✅
- 缺列、多余表、错误 revision 均经真实编排入口 `adopt_db_copy.py` 阻止 stamp。✅
- upgrade 后隔离应用启动并经真实读库路由读到副本数据。✅
- 唯一约束与多列索引顺序反例可杀死错误实现（mutation check 已做）。✅
- backup 中断不残留 `*.db`；并发同目标仅一个成功。✅
- `--list` 路径/文件名/错误码与 runbook 一致。✅
- 测试清理无 ctypes/宿主特例，nt/posix 均保留原始异常。✅
- `git diff --check b4c51b4..HEAD` 退出 0。✅
- 后端/前端测试与 build 顺序执行全部退出 0，本文档数字与原始输出一致。✅
- 所有迁移/stamp/upgrade/冒烟只针对测试副本，未读取或修改日常 `data/web.db`。✅

**结论: Phase 5.1 门禁全部通过，第五阶段重新裁决为达标，可进入 Phase 6 共享状态设计。**

---

## 5. 第七轮复核更正（2026-09-10, Phase 5.2 起点）

第七轮 review（`docs/superpowers/plans/2026-09-10-phase-5-2-cli-isolation-and-publish-safety.md`）
判定 5.1 阶段不达标, 主要问题:

- **R7-01 / R7-04**: 父进程 `_smoke()` 直接调用 `run_smoke()`, 进入全局 `backend.main`
  lifespan, 可能向 `DATABASE_URL` 指向的非副本库写入（实测反例: 父进程 unrelated
  库被创建 10 张业务表）。happy path 之前的测试以手工 `_smoke` 覆盖绕过生产 smoke。
- **R7-02**: `python scripts/*.py` 文档命令从仓库根目录执行触发
  `ModuleNotFoundError: scripts/backend`。
- **R7-03**: 写操作成功后 `✅/❌` 状态字符在 Windows GBK 控制台抛
  `UnicodeEncodeError`, 副本已写但运维看到"失败"。
- **R7-05 / R7-06 / R7-07**: 备份占位即写最终 `.db`, 校验前可被 `--list` 列出;
  source connect 失败留 0 字节 `.db`; 后置校验只比较 source 表, 副本多出
  `attacker_extra` 仍判通过。
- **R7-08**: runbook §2 仍声称"忽略 alembic_version 后默认 verify 通过", 与
  严格 revision 策略冲突, 人工按文档执行 stamp 后会得到 "revision 不一致" 误报。
- **R7-09**: 状态机接受相对路径、dependency 缺键时 KeyError 绕过结构化结果、
  非 list 返回值被当成功; revision/smoke 失败分支无测试覆盖;
  `_current_revision()` engine 未 dispose。
- **R7-10**: 验证文档使用 PATH 中的裸 `python`（指向缺 pytest 的 2.7）,
  验收依据（计划文档）未纳入跟踪, 干净 checkout 无法阅读。

**裁决: Phase 5.1 已被 Phase 5.2 替代, 旧 5.1 验收记录仅作历史。**
Phase 6 入口（`docs/2026-09-09-phase-6-shared-state-design-input.md` § 1 "迁移
与回滚门禁"）在 Phase 5.2 验收通过前继续标记为阻塞项, 不开始共享配置表或
operation/run 状态机实现。
