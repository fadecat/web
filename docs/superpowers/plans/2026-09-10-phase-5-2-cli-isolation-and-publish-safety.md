# 第七轮 Review 与 Phase 5.2 CLI 隔离及备份发布安全 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让数据库接管的真实 CLI 与测试使用同一条安全路径，确保冒烟只连接指定副本、备份通过全部校验后才可见，并使 runbook 中每条命令可在 Windows 环境复制执行。

**Architecture:** 运维脚本统一通过 `python -m scripts.模块名` 执行。接管编排进程不在自身进程启动 FastAPI，而是以显式环境变量启动隔离 smoke 子进程；子进程在导入 backend 前绑定副本数据库并关闭 scheduler。备份先写同目录 `.partial` 文件，完成完整内容校验后再以不覆盖语义发布最终 `.db`。所有阶段返回结构化结果和 ASCII 状态标记。

**Tech Stack:** Python 3.13、SQLite、SQLAlchemy 2、Alembic、FastAPI TestClient、pytest、PowerShell、Vue 3、Vitest。

---

## 1. Review 范围与裁决

审核范围：`8872ab1891ce065b5ab272826dbff279e7d08f40..cf3f6d0`，共 6 个提交、14 个已跟踪文件，新增 1488 行、删除 387 行。工作区另有未跟踪的上一轮计划 `docs/superpowers/plans/2026-09-09-phase-5-1-adoption-gate-repair.md`。

**裁决：不达标。** revision、NUL/BLOB 摘要、唯一约束、复合索引和跨平台清理的单元测试已通过；但真实接管 smoke 会进入全局应用 lifespan，可能修改指定副本之外的数据库。runbook 的核心脚本命令无法直接执行，备份在校验前已经以最终 `.db` 暴露。Phase 5.1 的“只操作副本”和“校验后发布”两个安全目标没有达成，不能进入 Phase 6。

### 1.1 具体缺陷

#### R7-01 / P1：生产接管 smoke 会对全局 DATABASE_URL 执行 init_db

`scripts/adopt_db_copy.py:137`—`:142` 在父进程导入全局 backend 数据库模块，生产 `_smoke()` 在 `:187`—`:189` 直接调用 `run_smoke()`。`scripts/smoke_db_copy.py:40`、`:60`—`:64` 创建应用并进入真实 lifespan；`backend/main.py:23`—`:31` 随即调用全局 `init_db()` 和可能的 scheduler。路由 dependency override 只改变 `/api/settings` 的 Session，不能隔离 lifespan。

临时反例将全局 `DATABASE_URL` 指向 `unrelated.db`，接管 source/copy 使用另外两个文件；接管后 `unrelated.db` 被创建10张业务表。另一个无文件写入的哨兵反例确认 `run_smoke(copy)` 必然调用 `backend.main.init_db`。这直接否定 `docs/2026-09-09-phase-5-1-verification.md:72` 的“只针对测试副本”。

#### R7-02 / P1：Runbook 的核心 CLI 命令无法复制执行

`docs/database-migration-runbook.md:35`、`:51`、`:70`、`:90` 使用 `python scripts/*.py`。从仓库根目录执行时，`scripts/verify_db_restore.py:23` 报 `ModuleNotFoundError: No module named 'scripts'`；`scripts/adopt_db_copy.py:137` 和 `scripts/smoke_db_copy.py:40` 报 `No module named 'backend'`。`scripts/check_db_baseline.py:19` 同样失败。

pytest 使用包导入，不能证明 direct-file CLI 可用。运维入口必须统一成模块执行，并用 subprocess 测试文档中的原始命令。

#### R7-03 / P1：Windows 默认控制台编码可能在接管完成后制造失败结果

`scripts/adopt_db_copy.py:217`—`:225` 在所有 stamp、upgrade、smoke 阶段完成后输出 `✅/❌`。Windows GBK 控制台反例中，模块命令在这里抛 UnicodeEncodeError 并返回 1，而副本已经完成写操作；操作员收到“失败”后可能重复执行或错误处置。

CLI 状态前缀必须为 ASCII `[PASS]/[FAIL]`，并通过 `PYTHONIOENCODING=gbk` 的 subprocess 测试证明成功和失败路径都能输出并保持正确退出码。

#### R7-04 / P1：生产 smoke 没有证明读到可识别副本数据

`scripts/adopt_db_copy.py:187`—`:189` 只调用 `run_smoke(backup_copy)`，没有传 expect key/value；`scripts/smoke_db_copy.py:74`—`:84` 只有传入 expect_key 才检查数据。`tests/test_database_adoption.py:49`—`:54` 手工替换了生产 smoke dependency，因此 happy path 没有测试 `build_dependencies()` 实际返回的 smoke。

生产 smoke 应在全新子进程中断言全局 engine 解析出的数据库绝对路径等于指定副本，并让真实 `get_db/SessionLocal` 服务数据库路由；测试不得替换 production smoke。

#### R7-05 / P1：所谓原子发布在校验前已经暴露最终 `.db`

`scripts/backup_db.py:63`—`:76` 直接原子占位并写最终 dst，`do_backup()` 到 `:120`—`:138` 才执行后置校验；`list_backups()` 在 `:160`—`:169` 同期会列出该文件。线程反例在 backup 暂停时观察到 `published.db` 已存在且为0字节。

备份必须写不匹配 `*.db` 的 partial 文件，完成 integrity、表集合、行数和内容摘要后再一次性发布最终名称。

#### R7-06 / P1：源连接或后置校验失败会留下最终扩展名文件

`scripts/backup_db.py:69` 的 source connect 位于 `try` 外；注入连接失败后，目标0字节 `.db` 仍存在。后置 `_table_counts/_integrity` 抛异常时也没有包在清理逻辑内，本轮临时反例留下最终扩展名的 `src.时间戳.db`。

`:128`—`:131` 对 `.failed` rename 失败直接 pass，原 `.db` 会继续进入 `--list`，但 stderr 仍声称已隔离。所有创建、连接、backup、校验、发布异常都必须进入同一个清理状态机。

#### R7-07 / P1：backup 后置校验没有比较目标的额外表

`scripts/backup_db.py:120`—`:123` 的 mismatch 只遍历 source 表。临时反例在 backup 后向 dst 加入 `attacker_extra`，`do_backup()` 仍返回0、保留最终 `.db` 并打印“备份可恢复校验通过”。表集合必须先完全相等，再比较每表数据摘要。

#### R7-08 / P1：Runbook 的 stamp 后验证与严格 revision 策略冲突

`docs/database-migration-runbook.md:20`—`:22`、`:49`—`:51` 仍声称忽略 `alembic_version` 后默认 verify 会通过；实际 `scripts/verify_db_restore.py:229`—`:235` 默认拒绝源 None、副本 0001。临时副本按文档执行会得到 migration revision 不一致，必须显式传 `--expected-backup-revision 0001`，或只保留接管编排这一条权威流程。

#### R7-09 / P2：绝对路径门禁和状态机契约仍不完整

`scripts/adopt_db_copy.py:76`—`:77` 对相对路径直接 resolve，没有拒绝；偏离上一计划的显式绝对路径要求。`:93`—`:105` 在 try 外读取 dependency key，缺键会绕过结构化 AdoptionResult；非 list 的错误返回值会被当作成功。

`tests/test_database_adoption.py:175`—`:224` 只覆盖 verify、compare、stamp、upgrade 失败，没有 revision 和 smoke 失败分支。`_current_revision()` 在 `scripts/adopt_db_copy.py:171`—`:175` 创建 engine 后没有 dispose。

#### R7-10 / P2：验收命令和计划文档无法在干净 checkout 复现

`docs/2026-09-09-phase-5-1-verification.md:13` 只写 `python -m pytest`，但当前 PATH 中 `python` 指向缺 pytest 的 Python 2.7；上一计划明确要求受管 Python 3.13 绝对路径。验收文件 `:3` 引用的 Phase 5.1 计划仍是未跟踪文件，其他程序员 checkout `cf3f6d0` 后无法读取评审依据。

### 1.2 已执行验证

| 检查 | 本轮结果 |
|---|---|
| 受管 Python 3.13 全量 pytest | 224 passed，2146 warnings，退出0。 |
| frontend `pnpm test` | Node 34 passed；Vitest 32 passed，无 Unhandled Errors，退出0。 |
| frontend `pnpm build` | 2256 modules transformed，退出0。 |
| `git diff --check 8872ab1..HEAD` | 无输出，退出0；`tests/conftest.py` 为 LF。 |
| direct-file CLI | checker、verify、adopt、smoke 均可复现 ModuleNotFoundError。 |
| runbook stamp 后默认 verify | 返回 migration revision 不一致。 |
| smoke lifespan 哨兵 | `run_smoke(copy)` 调用了全局 `init_db`。 |
| backup 后置校验异常 | 异常传播，最终 `*.db` 文件仍被保留。 |

### 1.3 本轮已满足项

- `tests/test_backup_restore.py::TestContentDigest` 覆盖 NUL、BLOB、类型和分隔符反例。
- `tests/test_backup_restore.py::TestMigrationRevision` 覆盖普通严格 revision 和显式未版本化接管模式。
- `tests/test_migration_baseline.py::test_missing_unique_constraint_detected` 重建真实表并移除唯一约束。
- `tests/test_migration_baseline.py::test_composite_index_column_order_detected` 使用真实双列索引反转顺序。
- `tests/test_artifact_cleanup.py` 覆盖 nt/posix 错误 cause，生产清理代码不再使用 ctypes。

## 2. 下一阶段范围与门禁

阶段名称：**Phase 5.2 CLI 隔离及备份发布安全**，预计 3～4 个开发工作日。

本阶段关闭 R7-01～R7-10。不得实现 Phase 6 的共享配置表、operation/run 状态机或鉴权，不得对日常 `data/web.db` 执行 stamp、upgrade、init_db 或 smoke。测试只使用临时 source、copy 和 unrelated 哨兵库。

| 路径 | 责任 |
|---|---|
| `scripts/__init__.py` | 明确 scripts 包入口。 |
| `scripts/adopt_db_copy.py` | 绝对路径、依赖契约、子进程 smoke、ASCII 输出和 engine 生命周期。 |
| `scripts/smoke_db_copy.py` | 全新进程绑定副本后再导入 backend，使用真实 SessionLocal。 |
| `scripts/backup_db.py` | partial 写入、完整校验、最终发布和异常清理。 |
| `tests/test_cli_entrypoints.py` | 按 runbook 原命令执行模块 CLI，覆盖 GBK 与路径错误。 |
| `tests/test_database_adoption.py` | 真实 production dependencies 及 unrelated DB 哨兵。 |
| `tests/test_backup_restore.py` | 发布可见性、连接/校验异常、额外表反例。 |
| `docs/database-migration-runbook.md` | 唯一权威接管流程和正确 revision 语义。 |
| `docs/2026-09-09-phase-5-1-verification.md` | 追加第七轮更正。 |

## 3. 实施任务

### Task 1：固定可执行的模块 CLI 与 Windows 输出契约

**Files:**

- Create: `scripts/__init__.py`
- Create: `tests/test_cli_entrypoints.py`
- Modify: `scripts/adopt_db_copy.py:201`
- Modify: `scripts/smoke_db_copy.py:91`
- Modify: `docs/database-migration-runbook.md:24`
- Modify: `README.md:78`

- [ ] **Step 1: 写真实 subprocess 失败用例**

从仓库根目录运行文档入口，覆盖 checker/verify/adopt/smoke 的 `--help` 和一个临时库 happy path。测试命令必须使用参数数组 `[sys.executable, "-m", "scripts.adopt_db_copy"]` 等真实模块名，不得通过 Python 函数调用替代 CLI。

- [ ] **Step 2: 统一模块执行**

所有 README/runbook 示例改为：

```powershell
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m scripts.backup_db --source D:/migration-drill/source.db --destination-dir D:/migration-drill/backups
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m scripts.verify_db_restore --source D:/migration-drill/source.db --backup D:/migration-drill/backups/source.20260910_120000_000000.db
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m scripts.adopt_db_copy --source D:/migration-drill/source.db --backup-copy D:/migration-drill/backups/source.20260910_120000_000000.db --revision 0001
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m scripts.smoke_db_copy --database D:/migration-drill/backups/source.20260910_120000_000000.db
```

文档说明生产环境可替换解释器绝对路径，但验收必须记录实际解释器和 `sys.version`。

- [ ] **Step 3: CLI 状态标记使用 ASCII**

将所有状态前缀改为 `[PASS]`、`[FAIL]`、`[WARN]`。subprocess 设置 `PYTHONIOENCODING=gbk` 分别运行成功和失败链，断言无 UnicodeEncodeError、退出码与 AdoptionResult 一致。

- [ ] **Step 4: 先提交评审依据**

把未跟踪的 Phase 5.1 计划和本计划一并纳入 Git，确保验收引用在干净 checkout 存在。

```bash
git add scripts/__init__.py tests/test_cli_entrypoints.py scripts/adopt_db_copy.py scripts/smoke_db_copy.py README.md docs/database-migration-runbook.md docs/superpowers/plans/2026-09-09-phase-5-1-adoption-gate-repair.md docs/superpowers/plans/2026-09-10-phase-5-2-cli-isolation-and-publish-safety.md
git commit -m "fix: make database operations runnable from documented cli"
```

### Task 2：将应用冒烟放进绑定副本的全新进程

**Files:**

- Modify: `scripts/adopt_db_copy.py:121`
- Modify: `scripts/smoke_db_copy.py:25`
- Modify: `tests/test_database_adoption.py:35`
- Test: `tests/test_cli_entrypoints.py`

- [ ] **Step 1: 写 unrelated 数据库哨兵**

创建 source、copy、unrelated 三个临时文件。父进程 `DATABASE_URL` 指向 unrelated；运行真实 module adopt CLI 后，断言 unrelated 的字节、表集合和 mtime 均未改变，copy 完成 0001，输出包含 smoke_passed。

- [ ] **Step 2: production smoke 使用 subprocess**

`build_dependencies()._smoke` 使用参数数组启动：

```python
[sys.executable, "-X", "utf8", "-m", "scripts.smoke_db_copy", "--database", str(backup_copy)]
```

显式传入 `cwd=repo_root`、`check=False`、`capture_output=True`、`text=True`、`timeout=60`，子环境强制 `DATABASE_URL=sqlite:///copy`、`SCHEDULER_ENABLED=false`、`PYTHONUTF8=1`。不得使用 shell 字符串。

- [ ] **Step 3: smoke 子进程使用真实全局数据库依赖**

`smoke_db_copy` 必须在设置环境变量之后才导入 `backend.main` 和 `backend.models.database`。删除 `app.dependency_overrides[get_db]`；启动前断言全局 engine URL 解析后的文件等于 copy。TestClient 进入真实 lifespan，再请求 `/api/health` 和 `/api/settings`，从而同时验证 init_db、SessionLocal 和路由均绑定副本。

- [ ] **Step 4: 不再替换 happy-path smoke**

删除 `tests/test_database_adoption.py:49`—`:54` 的手工 `_smoke` 覆盖。真实 `build_dependencies()` 必须完成子进程 smoke；测试同时断言 unrelated 哨兵未变化。

- [ ] **Step 5: 运行并提交**

Run: `python -m pytest tests/test_database_adoption.py tests/test_cli_entrypoints.py -q -p no:cacheprovider`

```bash
git add scripts/adopt_db_copy.py scripts/smoke_db_copy.py tests/test_database_adoption.py tests/test_cli_entrypoints.py
git commit -m "fix: isolate database copy smoke in subprocess"
```

### Task 3：备份写 partial，完整校验后发布

**Files:**

- Modify: `scripts/backup_db.py:50`
- Modify: `tests/test_backup_restore.py`
- Test: `tests/test_backup_restore.py`

- [ ] **Step 1: 写发布时序反例**

注入会阻塞的 backup callback；阻塞期间 `list_backups(directory)` 必须返回空，目录中只能出现 `.partial`。释放后校验完成，最终 `.db` 才出现且 `.partial` 消失。

- [ ] **Step 2: 覆盖全部失败点**

参数化注入 source connect、destination connect、SQLite backup、表统计、integrity、内容摘要、最终发布失败。每个用例断言：非零/异常包含阶段，最终 `*.db` 不存在，partial 与 sidecar 文件清理；清理失败不能被吞掉。

- [ ] **Step 3: 完整比较副本**

后置校验至少比较 integrity、完整表集合、每表行数和 Task 3 已实现的二进制安全摘要。额外表、缺表、同行数内容篡改均禁止发布。

- [ ] **Step 4: 原子且不覆盖地发布**

partial 与 dst 放在同一目录。校验后用不覆盖的原子发布 helper 将完整文件变为最终 `.db`；Windows/Linux 都必须由并发测试证明同一 dst 恰好一个成功。任何平台不支持该发布原语时失败即停，不回退到可能覆盖目标的 rename。

- [ ] **Step 5: 修正返回与日志顺序**

只有发布成功后打印“备份完成”。失败隔离如果无法改名必须返回清理错误，不能声称已隔离。`--list` 永远不展示 partial/failed/sidecar。

- [ ] **Step 6: 运行并提交**

Run: `python -m pytest tests/test_backup_restore.py -q -p no:cacheprovider`

```bash
git add scripts/backup_db.py tests/test_backup_restore.py
git commit -m "fix: publish sqlite backups after full verification"
```

### Task 4：收紧接管状态机输入和资源生命周期

**Files:**

- Modify: `scripts/adopt_db_copy.py:59`
- Modify: `tests/test_database_adoption.py:144`

- [ ] **Step 1: 拒绝相对路径和无效 revision**

CLI 原始参数不是绝对文件路径时返回2，不得先 resolve 后接受。source 和 copy 必须存在、是普通文件、不是同一文件；copy 不得是日常 `data/web.db`。revision 必须由 ScriptDirectory 解析为当前迁移图中的 revision。

- [ ] **Step 2: 校验 dependency 契约**

执行前验证六个 dependency key 全部存在且 callable。阶段返回只接受 `None` 或 `list[str]`；其他类型形成结构化 failed result。dependency 查找也放进异常转译边界。

- [ ] **Step 3: 补齐所有失败分支**

新增 revision failure 和 smoke failure；每条都断言所有下游调用为0、failed_stage 精确、code 非0。再覆盖缺 dependency、错误返回类型、相对路径、source/copy 不存在及 copy 指向日常库。

- [ ] **Step 4: 显式释放 current revision engine**

`_current_revision()` 使用 try/finally `engine.dispose()`。测试完成后立即 rename copy，不能依赖 fixture `gc.collect()`。

- [ ] **Step 5: 运行并提交**

Run: `python -m pytest tests/test_database_adoption.py -q -p no:cacheprovider`

```bash
git add scripts/adopt_db_copy.py tests/test_database_adoption.py
git commit -m "fix: enforce database adoption state contracts"
```

### Task 5：删除冲突流程并建立唯一 Runbook

**Files:**

- Modify: `docs/database-migration-runbook.md:18`
- Modify: `README.md:78`
- Modify: `docs/2026-09-09-phase-5-1-verification.md:60`

- [ ] **Step 1: 移除过时的“忽略版本表”说明**

明确普通 verify 严格比较 revision；只有编排入口 stamp 后的内部验证使用 expected revision。删除任何“stamp 前后默认 verify 都通过”的文本。

- [ ] **Step 2: 只保留一条接管流程**

Runbook 固定为：停止写入 → module backup CLI → 从输出复制最终绝对路径 → module adopt CLI。人工诊断命令放在故障排查附录，其中 stamp 后 verify 必须带 `--expected-backup-revision 0001`。

- [ ] **Step 3: 明确写入边界**

source 只读；copy 允许 backup、stamp、upgrade 和 init_db；unrelated/global/daily DB 不得被 smoke 打开。发生 stamped 之后的失败时，copy 标记为不可交付，必须从已验证 backup 重新开始，不能继续补跑后续命令。

- [ ] **Step 4: 追加历史验收更正**

在 Phase 5.1 验收文档追加第七轮复核结果，保留原文：CLI 入口不可执行、全局 lifespan 写入风险、备份提前发布、runbook revision 冲突。Phase 6 输入继续标记为阻塞。

```bash
git add docs/database-migration-runbook.md README.md docs/2026-09-09-phase-5-1-verification.md docs/2026-09-09-phase-6-shared-state-design-input.md
git commit -m "docs: correct database adoption operating procedure"
```

### Task 6：Phase 5.2 验收与 Phase 6 入口裁决

**Files:**

- Create: `docs/2026-09-10-phase-5-2-verification.md`
- Modify: `docs/2026-09-09-phase-6-shared-state-design-input.md:37`

- [ ] **Step 1: 使用绝对解释器顺序执行**

```powershell
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m pytest tests -q -p no:cacheprovider
Set-Location frontend
pnpm test
pnpm build
Set-Location ..
git diff --check 8872ab1..HEAD
```

- [ ] **Step 2: 执行运维级临时副本演练**

只能使用测试目录中的 source、copy、unrelated。按 runbook 原样运行 backup 和 adopt module CLI，保存 stdout/stderr、退出码和三库前后哈希。必须证明 source/unrelated 不变、copy 达到 head、smoke 子进程命中 copy。

- [ ] **Step 3: 执行 Windows 编码与发布并发门禁**

GBK stdout 下成功/失败 CLI 均返回预期 code；backup 阻塞期间 list 看不到最终 `.db`；两个进程发布同一 dst 恰好一个成功。

- [ ] **Step 4: 逐项关闭 R7-01～R7-10**

每项记录失败前反例、修复 commit、测试名、最终退出码。验收文档写受管 Python 绝对路径、`sys.version`、完整 commit SHA，不使用 PATH 中的裸 `python` 作为证据。

- [ ] **Step 5: 提交**

```bash
git add docs/2026-09-10-phase-5-2-verification.md docs/2026-09-09-phase-6-shared-state-design-input.md
git commit -m "docs: verify isolated database adoption cli"
```

## 4. 完成门禁

- [ ] 干净 checkout 中，runbook 的 module CLI 命令可原样执行。
- [ ] adopt smoke 全新进程的全局 engine、lifespan、SessionLocal 都绑定 copy。
- [ ] source、unrelated 和日常库在接管演练前后字节与 mtime 不变。
- [ ] production happy-path 测试不替换 smoke dependency。
- [ ] GBK/UTF-8 控制台均不会在写操作后因状态字符编码失败。
- [ ] backup 完成校验前 `--list` 看不到最终 `.db`。
- [ ] source connect、backup、后置校验、发布任一失败均不留下最终 `.db`。
- [ ] 额外表、缺表、主键或内容变化均禁止备份发布。
- [ ] 普通 verify 严格比较 revision；runbook 不再包含冲突的手工成功路径。
- [ ] 相对路径、无效 revision、dependency 缺失和错误返回类型全部失败即停。
- [ ] 所有 SQLAlchemy engine 显式 dispose，不依赖 gc 释放句柄。
- [ ] 上一轮与本轮计划均已跟踪，干净 checkout 可读取验收依据。
- [ ] 后端、前端、build、diff check 和临时副本运维演练全部退出0。

这些门禁全部通过后，才重新裁决 Phase 5.1，并决定是否开始 Phase 6 共享状态架构设计。
