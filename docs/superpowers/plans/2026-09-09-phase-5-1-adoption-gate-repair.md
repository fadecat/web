# 第六轮 Review 与 Phase 5.1 接管门禁修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修正第五阶段验收证据、迁移版本校验和数据库副本接管链，使操作员只能通过一个可测试、失败即停的入口完成副本接管，并在进入共享状态设计前获得可信门禁。

**Architecture:** 保留 Alembic `0001` 和现有只读结构检查。恢复验证将业务数据与迁移版本分开比较，默认要求版本状态完全一致，只有显式的“未版本化源库接管到 0001”模式允许副本多出指定版本。新增接管编排入口，统一执行 backup、verify、compare、stamp、upgrade 和隔离应用冒烟；测试资源清理改为跨平台标准库实现。

**Tech Stack:** Python 3.11+、SQLite、SQLAlchemy 2、Alembic、pytest、FastAPI TestClient、Vue 3、Vitest、Node test。

---

## 1. Review 范围与裁决

审核范围：`b4c51b4b24e5a1f06a461ef697363f6a4177302a..8872ab1891ce065b5ab272826dbff279e7d08f40`，共 6 个提交、19 个文件，新增 1729 行、删除 462 行。开始审查时已跟踪工作区、暂存区均为空。

**裁决：不达标。** 前端 overview 异常、SQLite 只读打开、真实主键值比较、backup 覆盖保护均已有实现，后端和前端测试也能顺序执行通过。但业务内容摘要存在可复现漏检，第五阶段要求的“可信验收记录、迁移版本安全、副本接管编排、隔离应用冒烟”仍未达成；`git diff --check` 实际失败，两个漂移测试没有覆盖测试名声称的行为。当前提交不能通过第五阶段门禁，不能进入 Phase 6 共享配置或 operation 持久化。

### 1.1 具体缺陷

#### R6-01 / P1：迁移版本被无条件忽略，错误 revision 的副本仍被判为一致

`scripts/verify_db_restore.py:94`—`:107` 将两侧 `alembic_version` 都排除，后续 `:118`—`:142` 只比较业务表；`scripts/check_db_baseline.py:21`—`:22` 同样不检查版本值。临时 ORM 库反例中，源库版本为 `0001`、副本版本为 `9999`，业务结构和数据相同，`verify_restore(..., Base.metadata)` 返回 `[]`。

这会把“允许未 stamp 源库与已 stamp 0001 副本之间存在版本表差异”的特例扩大成“任何版本差异都合法”。默认恢复验证必须拒绝版本不一致，接管特例必须显式传入预期 revision。

#### R6-02 / P1：内容摘要会漏掉 NUL 字符后的文本篡改

`scripts/verify_db_restore.py:67`—`:76` 使用 SQLite `quote()` 把每个值转成文本再计算摘要。SQLite `quote()` 对含 NUL 的 TEXT 只保留 NUL 之前的内容；临时库反例中，源值 `a\0x`、副本值 `a\0y`，当前 `verify_restore()` 返回空差异。现有 `tests/test_backup_restore.py:125`—`:137` 只覆盖普通字符串 `tampered`，无法发现该漏检。

摘要必须直接编码 sqlite3 返回的原始值，包含类型标签、字节长度和完整 payload；TEXT、BLOB、NULL、整数与浮点数不能依赖拼接分隔符或 SQL 字面量表示。

#### R6-03 / P1：漂移库“不调用 stamp”的测试是自证，未覆盖任何编排入口

`tests/test_database_adoption.py:90`—`:97` 先得到 differences，然后在 mock 上下文中只执行 `assert differences`；生产代码和 Alembic stamp 都没有被调用。`:99`—`:109` 的多余表测试甚至没有创建 stamp mock。删除所有接管保护逻辑后，这两条测试仍会通过。

仓库目前也没有负责“compare 失败后禁止 stamp”的产品函数，安全性完全依赖操作员逐行复制 runbook。必须先建立可调用的接管编排入口，再对它注入 stamp/upgrade 依赖并断言失败分支调用次数为零。

#### R6-04 / P1：第五阶段要求的隔离应用冒烟没有实现

`tests/test_database_adoption.py:69`—`:76` 在 upgrade 后只用 sqlite3 查询一行业务数据和版本号，没有启动 FastAPI、没有覆盖请求级数据库连接。`docs/database-migration-runbook.md:54` 只写“隔离应用冒烟”注释，没有可执行命令、成功响应或失败停止条件。

因此现有测试只能证明 SQLite 文件可查询，不能证明应用能用该副本启动并访问数据库路由。

#### R6-05 / P1：验收记录宣称 diff check 通过，实际有 584 行错误

`tests/conftest.py:1`—`:292` 被整文件提交为 CRLF；本轮执行 `git diff --check b4c51b4..HEAD` 返回 1，并从第 1 行开始逐行报告 trailing whitespace，共 584 行输出。`docs/2026-09-09-phase-5-verification.md:22` 却记录为通过。

#### R6-06 / P2：唯一约束和索引列顺序反例没有测试对应目标

`tests/test_migration_baseline.py:189`—`:202` 的 `test_missing_unique_index_detected` 删除的是 `ix_cb_snapshot_bond`；`migrations/versions/0001_initial_schema.py:75` 明确该索引 `unique=False`，真正唯一约束是 `:72` 的 `uq_cb_snapshot_bond_date`。该测试没有证明唯一约束漂移可被发现。

`tests/test_migration_baseline.py:204`—`:214` 声称验证“列序反转”，实际把单列 `bond_id` 换成单列 `trade_date`。它只能证明索引列集合变化被发现，不能证明多列索引顺序被比较。

#### R6-07 / P2：测试清理实现绑定 Windows，并破坏非 Windows 的原异常

`tests/conftest.py:246`—`:249` 捕获 OSError 后离开 except，再在非 Windows 分支执行裸 `raise`；Python 会抛 `RuntimeError: No active exception to reraise`，丢失真实文件错误。`:250`—`:263` 将 WorkBuddy 沙箱和 `ctypes.windll` 写进仓库测试基础设施，使 CI 行为依赖当前开发机。

标准库删除可以带有限重试，但必须保存并重新抛出最后一个 OSError；测试代码不应绕过宿主删除策略。

#### R6-08 / P2：Runbook 与 backup CLI 仍有不可复制执行的路径和用法

`scripts/backup_db.py:87`—`:89` 实际生成 `{src.stem}.{timestamp}.db`，源文件 `web.db` 会得到 `web.<timestamp>.db`；`docs/database-migration-runbook.md:30`—`:52` 和 `README.md:89`—`:93` 后续命令一直使用 `web.db.<ts>.db`，按文档复制会找不到文件。

`scripts/backup_db.py:11` 仍写可以单独运行 `--list`，而解析器在 `:127` 要求 `--list DIRECTORY`。此外 `list_backups()` 在 `:111`—`:113` 对目录拼错返回成功码 0，不符合接管工具的失败即停语义。

#### R6-09 / P2：backup 失败可能留下看似可用的目标文件

`scripts/backup_db.py:58`—`:68` 先检查目标不存在，再由 `sqlite3.connect(dst)` 创建目标。检查与创建之间有竞争窗口；在临时反例中于两步之间创建 sentinel 数据库，SQLite backup 会静默覆盖其数据。backup 中途抛错时 finally 只关闭连接，不删除部分文件。`do_backup()` 在 `:102`—`:104` 后置校验失败时也返回 1 但保留该文件，`--list` 会把它与有效备份一起列出。

发布备份必须先原子占位，失败时删除临时/部分文件，完成所有校验后才将文件标记为可列出状态。

#### R6-10 / P2：验收数量和残留说明互相冲突

`docs/2026-09-09-phase-5-verification.md:18` 写 204 passed，本轮同一 Python 路径实跑为 203 passed。`:23` 写 case 目录 `before=104 → after=104`，`:81` 又称历史残留是 14 个；当前目录枚举得到 44 个 `case_*`，其中多个路径拒绝读取。验收记录没有提供采集命令和原始输出文件，无法解释三个数字的关系。

#### R6-11 / P2：迁移测试仍在 engine dispose 后使用 Inspector

`tests/test_migration_baseline.py:53`—`:58` 和 `:76`—`:81` 在 try 内只创建 Inspector，finally dispose 后才调用 `get_table_names()`。Inspector 会延迟重新连接，这正是上一轮 R5-01 要禁止的生命周期模式；测试依赖后续 `gc.collect()` 才释放该连接。

### 1.2 已执行验证

| 检查 | 本轮结果 |
|---|---|
| 后端 `pytest tests -q -p no:cacheprovider` | 203 passed，2141 warnings，退出 0。文档记录的 204 不可复现。 |
| frontend `pnpm test`（顺序执行） | Node 34 passed；Vitest 32 passed，无 Unhandled Errors，退出 0。 |
| frontend `pnpm build`（顺序执行） | 2256 modules transformed，退出 0。 |
| `git diff --check b4c51b4..HEAD` | 退出 1；`tests/conftest.py` 产生 584 行 trailing-whitespace 输出。 |
| 不同 Alembic revision 的恢复反例 | 源 `0001`、副本 `9999`，当前错误返回空差异。 |
| 含 NUL 文本的恢复反例 | 源 `a\0x`、副本 `a\0y`，当前错误返回空差异。 |
| `.test-artifacts` 枚举 | 当前 44 个 `case_*`；多个目录拒绝读取，不能证明历史残留为空目录。 |

前端测试与 build 同时并行时本机出现内存不足；改为顺序执行均通过，因此不作为产品缺陷，下一轮验收固定顺序执行资源密集命令。

### 1.3 已满足的第五阶段项

- `frontend/src/pages/DataStatus.vue:569`—`:575` 已使用 `v-if` 和安全日期格式化；缺失 overview 用例与全量前端测试通过。
- `scripts/sqlite_readonly.py:24`—`:49` 对不存在文件失败，并以 `mode=ro` 打开 SQLite；不存在路径反例通过。
- `scripts/verify_db_restore.py:50`—`:82` 已比较真实主键值集合和业务行摘要；主键替换、非键值篡改反例通过。
- `scripts/backup_db.py:49`—`:70` 已把拒绝覆盖放进生产 API；`test_backup_refuses_existing_destination` 直接调用该入口。
- `tests/test_database_adoption.py::test_unversioned_database_copy_can_be_adopted` 已证明现有 happy path 的 backup、stamp 和 upgrade 可在临时副本运行。

## 2. 下一阶段范围与门禁

阶段名称：**Phase 5.1 接管门禁修复**，预计 3～5 个开发工作日。

本阶段只关闭 R6-01～R6-11，不开始 factors/index universe 入库，不新增 operation/run 业务表，不接管日常 `data/web.db`。完成后重新裁决第五阶段；全部门禁通过才进入 Phase 6 架构设计。

| 路径 | 责任 |
|---|---|
| `scripts/verify_db_restore.py` | 默认严格比较 migration revision；显式接管模式只允许目标 revision。 |
| `scripts/backup_db.py` | 原子占位、失败清理、有效备份发布、CLI 失败码。 |
| `scripts/adopt_db_copy.py` | 单一副本接管编排入口和失败状态。 |
| `scripts/smoke_db_copy.py` | 使用显式副本启动隔离应用并访问真实数据库路由。 |
| `tests/conftest.py` | 跨平台、标准库、保留原始异常的清理。 |
| `tests/test_migration_baseline.py` | 正确的唯一约束、多列索引顺序和 Inspector 生命周期反例。 |
| `tests/test_backup_restore.py` | revision、部分备份、CLI 错误路径反例。 |
| `tests/test_database_adoption.py` | 调用真实编排入口覆盖成功链和所有失败分支。 |
| `docs/database-migration-runbook.md` | 只保留可复制执行且与 CLI 输出一致的命令。 |
| `docs/2026-09-09-phase-5-verification.md` | 追加更正，不覆盖原错误记录。 |

## 3. 实施任务

### Task 1：恢复跨平台清理语义并修正验收基础

**Files:**

- Modify: `tests/conftest.py:1`
- Modify: `tests/test_migration_baseline.py:49`
- Create: `tests/test_artifact_cleanup.py`
- Modify: `docs/2026-09-09-phase-5-verification.md:14`
- Test: `tests/test_artifact_cleanup.py`

- [ ] **Step 1: 先写标准库删除失败的反例**

新增单元测试，monkeypatch `os.remove` 连续抛一个带明确文本的 `PermissionError`，断言 `_delete_path_fail_closed` 最终抛出的异常以该错误为 cause；分别 monkeypatch `os.name` 为 `nt` 和 `posix`，两条分支都不得出现裸 `raise` 的 RuntimeError，也不得调用 ctypes。

- [ ] **Step 2: 用标准库实现有限重试并保留最后异常**

`_delete_path_fail_closed` 使用 `os.remove/os.rmdir`，最多重试 3 次，每次间隔不超过 100ms；成功立即返回，失败保存 `last_error`，最终 `raise OSError(...) from last_error`。删除 `ctypes.windll`、WorkBuddy 和 trash shim 相关代码及注释。

- [ ] **Step 3: 修正 Inspector 生命周期**

`test_empty_database_upgrades_to_current_schema` 和 `test_downgrade_to_base_removes_business_tables` 必须在 engine dispose 前完成 `inspector.get_table_names()`；或直接复用只读 helper。增加文件 rename 断言，证明测试自身不留句柄。

- [ ] **Step 4: 将 conftest 统一保存为 LF**

Run: `git ls-files --eol tests/conftest.py`

Expected: index/worktree 都为 LF。

Run: `git diff --check b4c51b4..HEAD`

Expected: 无输出、退出 0。

- [ ] **Step 5: 追加验收更正**

在第五阶段验收文档追加“第六轮复核更正”，记录原 `git diff --check` 和 204 passed 声明不可复现。残留统计必须同时记录采集命令、可枚举目录数、拒绝访问数和文件数，禁止用“运行前后相同”替代可清理性结论。

- [ ] **Step 6: 提交**

```bash
git add tests/conftest.py tests/test_migration_baseline.py tests/test_artifact_cleanup.py docs/2026-09-09-phase-5-verification.md
git commit -m "fix: make test cleanup portable and auditable"
```

### Task 2：让结构漂移测试真正覆盖唯一约束和索引顺序

**Files:**

- Modify: `tests/test_migration_baseline.py:189`
- Test: `tests/test_migration_baseline.py`

- [ ] **Step 1: 重建表以移除真实唯一约束**

从 Alembic 创建的 `cb_daily_snapshot` 读取原始建表 SQL，使用新表复制数据并替换旧表，但省略 `uq_cb_snapshot_bond_date(bond_id, trade_date)`；保留其余列、主键和普通索引。测试名改为 `test_missing_unique_constraint_detected`，只接受包含“唯一约束”的差异，不能用普通索引差异让断言通过。

- [ ] **Step 2: 反转真实多列索引**

选择 `ix_quote_idx_date(index_code, trade_date)`，删除后用同名索引按 `(trade_date, index_code)` 重建。`test_composite_index_column_order_detected` 必须断言输出同时包含索引名及两边列顺序。

- [ ] **Step 3: 运行 mutation check**

先临时把 `compare_schema` 中 index tuple 改为排序后的列集合，确认多列顺序测试失败；恢复列顺序比较后确认通过。先临时移除唯一约束比较，确认唯一约束测试失败。

- [ ] **Step 4: 运行并提交**

Run: `python -m pytest tests/test_migration_baseline.py -q -p no:cacheprovider`

Expected: 全部通过，无清理 warning，临时目录可删除。

```bash
git add tests/test_migration_baseline.py
git commit -m "test: prove unique and index-order drift detection"
```

### Task 3：修复内容摘要并增加严格的 migration revision 策略

**Files:**

- Modify: `scripts/verify_db_restore.py:91`
- Modify: `tests/test_backup_restore.py:111`
- Test: `tests/test_backup_restore.py`

- [ ] **Step 1: 写二进制安全的摘要反例**

至少覆盖：源 `a\0x` 与副本 `a\0y`；TEXT `"1"` 与 INTEGER `1`；BLOB `b"a\x00b"` 的末字节变化；包含当前分隔符 `\x1f/\x1e` 的多列字符串。每组保持表结构、主键和行数相同，`verify_restore` 必须返回“内容摘要不一致”。

- [ ] **Step 2: 用类型和长度分帧编码原始值**

删除 SQL `quote()`。通过 sqlite3 直接 SELECT 原始列值，对每个单元格写入固定类型标签、payload 字节长度和完整 payload：None、int、float、str、bytes 分开编码；str 使用 UTF-8，float 使用 `struct.pack`，长度使用固定宽度无符号整数。行与列都带长度边界，任何控制字符和 NUL 都不能改变分帧。

- [ ] **Step 3: 写三个 revision 状态反例**

固定覆盖：两侧均无版本表时通过；两侧都有且同为 `0001` 时通过；源 `0001`、副本 `9999` 时默认失败。再覆盖接管特例：源无版本表、副本 `0001` 只有在传入 `expected_backup_revision="0001"` 时通过，未传参数时失败。

- [ ] **Step 4: 分离业务表过滤与版本状态比较**

新增 `_migration_revision(path) -> str | None`。版本表不存在返回 None；存在但零行、多行、空字符串均返回明确错误，不吞掉 SQLite 异常。`verify_restore` 默认要求源和副本 revision 完全相同。

接管模式只允许以下状态：源 revision 为 None、副本 revision 等于调用方指定值。源已有 revision、目标 revision 不同仍失败。

- [ ] **Step 5: 修改 CLI**

为 `verify_db_restore.py` 增加 `--expected-backup-revision`，默认不设置。帮助文本明确该参数只用于“未版本化源库与已 stamp 副本”的第二次验证；普通备份恢复不得使用。

- [ ] **Step 6: 运行并提交**

Run: `python -m pytest tests/test_backup_restore.py -q -p no:cacheprovider`

Expected: NUL、BLOB、类型和分隔符反例全部被发现；错误 revision 返回差异并退出 1；显式 0001 接管模式通过。

```bash
git add scripts/verify_db_restore.py tests/test_backup_restore.py
git commit -m "fix: verify sqlite content and migration revision"
```

### Task 4：让备份文件只在完整成功后发布

**Files:**

- Modify: `scripts/backup_db.py:49`
- Modify: `tests/test_backup_restore.py:140`
- Modify: `docs/database-migration-runbook.md:28`
- Test: `tests/test_backup_restore.py`

- [ ] **Step 1: 写失败残留与目录错误反例**

monkeypatch SQLite backup 在目标创建后抛异常，断言最终目标和临时文件都不存在。并发调用同一 dst 时只能一个成功，另一个必须抛 FileExistsError。`list_backups(missing_dir)` 必须返回非零。

- [ ] **Step 2: 原子占位并清理部分文件**

对显式 dst 使用 `os.open(dst, os.O_CREAT | os.O_EXCL | os.O_RDWR)` 原子占位，立即关闭文件描述符后再交给 sqlite3。任何 backup、integrity 或内容校验异常都关闭连接并删除该占位文件；删除失败将原异常和清理异常一起暴露。

`do_backup` 只有在 integrity、表集合和每表行数全部一致后才打印“备份完成”。校验失败不得让文件进入 `list_backups`；可以删除或改为不匹配 `*.db` 的 `.failed` 后缀并返回 1。

- [ ] **Step 3: 统一 CLI 文档与真实文件名**

模块头将示例改为 `--list D:/path/to/backups`。runbook 使用真实格式 `web.<YYYYMMDD_HHMMSS_microseconds>.db`，并要求从 backup 命令输出复制绝对路径，后续命令不得手工拼接错误的 `web.db.<ts>.db`。

- [ ] **Step 4: 错误目录 fail closed**

`list_backups` 对不存在、非目录、不可读目录返回 2 并输出 stderr；只有存在且可读的空目录可以返回 0 和“暂无备份”。

- [ ] **Step 5: 运行并提交**

Run: `python -m pytest tests/test_backup_restore.py -q -p no:cacheprovider`

```bash
git add scripts/backup_db.py tests/test_backup_restore.py docs/database-migration-runbook.md
git commit -m "fix: publish only verified sqlite backups"
```

### Task 5：建立真实的副本接管编排与应用冒烟

**Files:**

- Create: `scripts/adopt_db_copy.py`
- Create: `scripts/smoke_db_copy.py`
- Modify: `tests/test_database_adoption.py:51`
- Modify: `docs/database-migration-runbook.md:18`
- Modify: `README.md`
- Test: `tests/test_database_adoption.py`

- [ ] **Step 1: 为编排入口写失败优先测试**

`adopt_database_copy(source, backup_copy, revision, dependencies)` 返回结构化阶段结果。注入 verify、compare、stamp、upgrade、smoke 五个依赖，固定断言：verify 失败时后四项调用 0 次；compare 失败时 stamp/upgrade/smoke 调用 0 次；stamp 失败时 upgrade/smoke 调用 0 次；upgrade 失败时 smoke 调用 0 次；任一步失败均返回非零且包含阶段名。

- [ ] **Step 2: 实现固定状态机**

状态仅允许：`backup_verified → schema_verified → stamped → revision_verified → upgraded → smoke_passed`。不接受跳步，不捕获后继续。所有输入都要求显式绝对路径；拒绝目标等于仓库 `data/web.db`，拒绝 source 与 backup_copy 指向同一文件。

stamp 后调用 Task 3 的显式 revision 策略再次验证；upgrade 后运行 `alembic current` 等价检查，必须等于 head。

- [ ] **Step 3: 实现隔离应用冒烟脚本**

`smoke_db_copy.py --database <absolute-path>` 在导入任何 `backend.*` 前设置 `DATABASE_URL` 和 `SCHEDULER_ENABLED=false`，然后启动 TestClient lifespan，依次请求 `/api/health` 和至少一个真实读库路由。测试副本预置可识别的 `app_setting`，断言 API 返回该值，证明请求确实使用副本而非内存库或日常库。

- [ ] **Step 4: 用真实入口替换自证测试**

删除 `assert differences` 后对空 mock 的断言。缺列和多余表用例直接调用 `adopt_database_copy`，并断言 stamp、upgrade、smoke 均未调用；happy path 调用真实 stamp、upgrade 和 smoke，最终状态必须为 `smoke_passed`。

- [ ] **Step 5: Runbook 只调用单一编排入口**

Runbook 保留人工 backup 和停写窗口说明，接管副本使用一条命令完成后续状态机。文档列出每个阶段的退出码与产物记录，禁止操作员跳过 compare 后直接 stamp。

- [ ] **Step 6: 运行并提交**

Run: `python -m pytest tests/test_database_adoption.py tests/test_migration_baseline.py tests/test_backup_restore.py -q -p no:cacheprovider`

Expected: 成功链访问真实读库路由；所有失败分支在 stamp 前或对应阶段停止；只操作临时副本。

```bash
git add scripts/adopt_db_copy.py scripts/smoke_db_copy.py tests/test_database_adoption.py docs/database-migration-runbook.md README.md
git commit -m "feat: orchestrate safe database copy adoption"
```

### Task 6：重新验收 Phase 5 并决定是否进入 Phase 6

**Files:**

- Create: `docs/2026-09-09-phase-5-1-verification.md`
- Modify: `docs/2026-09-09-phase-6-shared-state-design-input.md:37`

- [ ] **Step 1: 顺序执行固定门禁**

```powershell
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m pytest tests -q -p no:cacheprovider
Set-Location frontend
pnpm test
pnpm build
Set-Location ..
git diff --check b4c51b4..HEAD
```

Expected: 每条命令退出 0；不得并行运行 Vitest 与 Vite build；pytest 无资源清理 warning；Vitest 无 Unhandled Errors。

- [ ] **Step 2: 保存可复核原始证据**

验收文档记录命令、完整 SHA、开始/结束时间、退出码、测试数量。残留目录用固定 PowerShell 命令统计成功读取、拒绝访问、含文件三类数量；结果必须与正文数字一致。

- [ ] **Step 3: 逐项关闭 R6-01～R6-11**

每项附失败前反例、修复 commit、测试名和最终结果。不能用测试总数代替反例结果，不能覆盖第五阶段原错误记录。

- [ ] **Step 4: 更新 Phase 6 输入并提交**

只有本计划完成门禁全部通过，才把 Phase 6 输入中的“迁移与回滚门禁已建立”更新为已验证事实；否则标为阻塞项，不开始共享配置表或 operation/run 状态机实现。

```bash
git add docs/2026-09-09-phase-5-1-verification.md docs/2026-09-09-phase-6-shared-state-design-input.md
git commit -m "docs: reverify database adoption gates"
```

## 4. 完成门禁

- [ ] 普通恢复验证拒绝任意 Alembic revision 差异。
- [ ] 未版本化源库到 0001 的差异只在显式接管参数下允许。
- [ ] 内容摘要发现 NUL 后篡改、BLOB 篡改、类型变化和分隔符碰撞。
- [ ] 缺列、多余表、错误 revision 均通过真实编排入口阻止 stamp。
- [ ] upgrade 后隔离应用启动并通过真实读库路由读到副本数据。
- [ ] 唯一约束和多列索引顺序反例能杀死对应错误实现。
- [ ] backup 中断不留下 `*.db`，并发同目标只能一个成功。
- [ ] `--list` 的路径、文件名、错误码和 runbook 完全一致。
- [ ] 测试清理不含 ctypes 或宿主沙箱特例，Linux/Windows 都保留原始异常。
- [ ] `git diff --check b4c51b4..HEAD` 无输出、退出 0。
- [ ] 后端、前端测试和 build 顺序执行全部退出 0，验收数字与原始输出一致。
- [ ] 所有迁移、stamp、upgrade、冒烟只针对测试副本；未读取或修改日常 `data/web.db`。

全部门禁通过后，下一轮才能以 `docs/2026-09-09-phase-6-shared-state-design-input.md` 为输入，设计共享配置和持久化同步 operation。
