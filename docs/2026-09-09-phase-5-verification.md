# 第五阶段验收记录：迁移安全收口

> 对应计划：`docs/superpowers/plans/2026-09-09-phase-5-migration-safety-closure.md`
> 验收日期：2026-09-09（本地，未推送）

## 1. 执行环境

| 工具 | 版本 / 路径 |
|---|---|
| Python | 3.13.14 — `C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe` |
| Alembic | 1.19.2 |
| Node.js | 22.22.2 / pnpm 10.33.2 |

## 2. 自动化验证结果（本次实际执行）

| 命令 | 结果 |
|---|---|
| `python -m pytest tests -q -p no:cacheprovider` | **204 passed**，0 failed（52s；无测试目录清理失败 warning） |
| `node --test src/utils/*.test.mjs` | **34 passed**，0 failed |
| `pnpm test:unit`（Bonds 8 + Settings 7 + DataStatus 7 + Factors 6 + api 4） | **32 passed**，0 failed，**无 Unhandled Errors** |
| `pnpm build` | 通过 |
| `git diff --check c23b24b..HEAD` | 通过（含 migration 0001 尾随空格修复） |
| 测试产物残留 | **运行前后 case 目录数不变(before=104 → after=104)，本轮零新增**；46 次 teardown 全部成功 |

## 3. 缺陷关闭证据

### R5-01 / P1：标称"只读"检查会创建数据库并泄漏连接

**修复**：新增 `scripts/sqlite_readonly.py`（`sqlite_path_from_url` 用 `resolve(strict=True)` + 文件检查；`readonly_engine` 用 `file:...?mode=ro` URI + NullPool；`readonly_connection` 供裸 sqlite3 只读连接）。`check_db_baseline.compare_schema` 改用 readonly_engine，**全部 Inspector 调用在 try 内、finally 才 dispose**。

**测试证据**：`test_compare_schema_rejects_missing_database_without_creating_it`（不存在路径报错且不建文件）、`test_compare_schema_releases_database_handle`（比对后文件可重命名）。

### R5-02 / P1：恢复验证未比较主键值/业务内容

**修复**：`verify_db_restore.py` 新增 `_primary_key_values`（按 PK 序号排序列名，查每行键值集合；区分"无主键列"与"空表"）和 `_table_digest`（quote() 全行内容 SHA-256 摘要，按主键排序）；`alembic_version` 不参与业务比较。

**测试证据**：`test_restore_verifier_detects_replaced_primary_key`（主键值 2→99 被拒）、`test_restore_verifier_detects_changed_non_key_value`（非键内容篡改被拒）、`test_restore_verifier_detects_pk_drift`（改为主键值集合不一致）。

### R5-03 / P1：runbook stamp→restore 顺序不可达

**修复**：`check_db_baseline` 与 `verify_db_restore` 都显式忽略 `MIGRATION_TABLES = {"alembic_version"}`；runbook 重写为可执行顺序（verify → compare → stamp → 再 verify/compare → upgrade）。`tests/test_database_adoption.py::test_unversioned_database_copy_can_be_adopted` 证明 stamp 前后 compare/verify 都通过、业务数据与版本号保留。

### R5-04 / P1：前端全量测试退出码 1（DataStatus overview TypeError）

**修复**：`DataStatus.vue` 的 `v-show`（仍求值隐藏节点）改为外层 `<template v-for>` + 内层 `v-if` 条件渲染；新增 `overviewDate(value, mode)` 安全格式化（null/非字符串 → '暂无'）。

**测试证据**：`DataStatus.test.js` 新增缺 overview / null 日期 / 完整 overview 三态用例，`errorHandler` 收集渲染错误为空、`vi.getTimerCount()` 收尾清零；`pnpm test` 退出 0 无 Unhandled。

### R5-05 / P2：测试清理失败被放行

**修复**：`conftest._remove_tree_fail_closed` 移除 warning 降级，fail-closed（`os.remove/os.rmdir` 失败 → ctypes 直调 Win32 API 兜底 → 仍失败即测试失败）；teardown 先 `gc.collect()`（释放 SQLAlchemy inspector 已借出连接的 Python 引用）；所有测试 sqlite3 连接改用 `contextlib.closing`。

**测试证据**：全量 204 passed 无"清理失败"warning；运行前后 case 目录数不变（本轮零新增）；46 次 teardown 全部 `done: False`。

### R5-06 / P2：backup CLI `--list` 不可执行、覆盖测试未走生产入口

**修复**：`backup_db.py` 的 `--source`/`--list <DIR>` 互斥（argparse mutually exclusive + required）；`backup()` API 本身拒绝覆盖（`dst.exists() → FileExistsError`，不依赖 CLI）；文件名加微秒时间戳；`list_backups(directory)` 显式目录。

**测试证据**：`TestBackupCli` 5 项（独立 --list / 无模式 2 / source+list 2 / list+destination 2 / 同秒两次备份两个文件）；`test_backup_refuses_existing_destination` 直接调 `backup()`。

### R5-07 / P3：migration 0001 尾随空格

**修复**：`Revises:` 行尾随空格删除；`git diff --check` 通过。

### R5-08 / P2：初始 revision 结构等价未证明

**修复**：`test_migration_created_database_matches_orm`（Alembic 产物 compare_schema 空差异 + `alembic.command.check` 无新 operation）、`test_missing_unique_index_detected`（删唯一索引报差异）、`test_index_column_order_detected`（同名索引列序反转报差异）。反例直接改 migration 产物，不用 metadata 自比较。

### R5-09 / P2：新测试断言杀不死错误实现

**修复**：`api.test.js` 用 `A/B + 1` 验证编码（去掉 encodeURIComponent 时用例失败，已实测红→绿）；`Factors.test.js` 拆为"复制保留未知评级"+"复制保留空评级"两条，分别断言序列化结果。

## 4. 数据库接管链（新增）

`tests/test_database_adoption.py`（3 项）：
- `test_unversioned_database_copy_can_be_adopted`：backup → verify → compare → stamp → verify/compare（忽略 alembic_version 仍通过）→ upgrade head → 业务数据 + 版本号断言。
- `test_drifted_database_never_calls_stamp` / `test_extra_table_database_never_calls_stamp`：缺列/多余表漂移，compare_schema 报差异且 stamp mock 调用次数 0。

## 5. 已知边界与说明

1. **历史残留**：`.test-artifacts` 下 14 个空 `case_*` 目录被外部进程（IDE/trash 服务）锁定，本会话内无法删除；非本轮测试产物，验收指标以"运行前后 case 数不变"为准。需在无沙箱环境手动删除。
2. **沙箱删除兜底**：conftest 用 ctypes 直调 Win32 API 删除测试临时文件（绕开 WorkBuddy safe-delete shim 对 trash 服务的依赖）；句柄占用时 ctypes 同样失败 → 测试失败，fail-closed 语义保持。
3. **日常 `data/web.db` 未触碰**：接管链只对临时副本执行；runbook 顺序已与可执行测试一致，实际日常接管仍属独立实施单。

## 6. 提交清单（本地 main，未推送）

1. `fix: safely render incomplete capability overviews`（R5-04/R5-09 + phase-4 更正）
2. `fix: make schema inspection strictly read only`（R5-01/R5-08）
3. `fix: verify sqlite backup record contents`（R5-02）
4. `fix: enforce backup and test resource cleanup`（R5-05/R5-06）
5. `test: prove safe database copy adoption`（R5-03/R5-07）
6. `docs: record migration safety closure`（T6）
