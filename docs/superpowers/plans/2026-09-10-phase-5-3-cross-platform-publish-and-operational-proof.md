# 第八轮 Review 与 Phase 5.3 跨平台发布及运维实证 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development while implementing each behavior change, then use superpowers:verification-before-completion before committing. Track every step with the checkboxes below.

**Goal:** 关闭数据库副本发布、接管路径和验收证据中的剩余安全缺口，使备份在 Windows 与 Linux 上都不会覆盖既有文件，并用一条真实、可复制、全程退出 0 的运维链证明 source 与 unrelated 库未被修改。

**Architecture:** 备份仍采用“同目录 partial → 完整校验 → 最终名称”的两阶段流程，但最终发布改用跨平台的原子 no-clobber 原语。发布成功是不可逆的提交点；所有统计、格式化和验证在提交点前完成，提交点后的清理或输出不得把已成功发布的备份报告成失败。接管入口统一用一个严格路径解析器处理文件路径与 `sqlite:///` URL，并把所有 dependency 契约错误转成结构化阶段结果。Runbook 只保留一条完整的 backup → adopt 操作链。

**Tech Stack:** Python 3.13、SQLite、SQLAlchemy 2、Alembic、FastAPI TestClient、pytest、PowerShell、GitHub Actions、Vue 3、Vitest。

---

## 1. Review 范围与裁决

审核范围：`cf3f6d0..3c0d7927a46a92b60623337381d3aca252c459c3`，共 7 个提交、16 个文件，新增 1833 行、删除 155 行。审核目标是上一轮 Phase 5.2 计划中“跨平台不覆盖发布、真实 CLI 隔离、完整运维演练和可复现验收”四项门禁。

**裁决：不达标。** 真实 module CLI、隔离 smoke、GBK 输出和多数失败即停测试已经落地，但跨平台发布的核心假设错误；验收记录中的运维演练实际退出 1，却被勾选为全部退出 0。绝对路径门禁仍可被 SQLite URL 形式绕过。Phase 5 尚不能重新裁决为达标，Phase 6 继续阻塞。

### 1.1 具体缺陷

#### R8-01 / P1：POSIX 上的最终发布会覆盖已有目标

`scripts/backup_db.py:106`—`:120` 使用 `os.rename(partial, dst)`，并在 `:109`—`:111` 声称目标存在时 POSIX 与 Windows 都会失败。POSIX `rename()` 对普通文件会原子替换目标，因此 Linux 上并发发布或同名冲突可能静默覆盖既有备份。

`tests/test_backup_restore.py::TestAtomicPublish::test_publish_refuses_overwrite` 和 `tests/test_backup_restore.py::TestAtomicPublish::test_concurrent_publish_same_dst_only_one_succeeds` 只证明当前 Windows 运行时行为，不能支持代码中的跨平台断言。

#### R8-02 / P1：发布后异常会形成“文件成功、CLI 失败”的矛盾状态

`scripts/backup_db.py:193`—`:211` 已把 partial 发布为最终 `.db`，随后 `:214`—`:218` 再次调用 `_table_counts(src)`、`_table_counts(dst)`、`dst.stat()` 和输出。这些操作任何一个抛异常，调用方都会收到失败或未捕获异常，但最终备份已经对 `--list` 可见。

`tests/test_backup_restore.py::TestPartialPublish::test_failure_point_leaves_no_final_db` 的 `table_stats` 分支只在发布前替换 `verify_restore`，没有覆盖发布后的真实统计失败点。

#### R8-03 / P1：备份公共函数与测试钩子仍有清理边界缺口

`scripts/backup_db.py:70`—`:74` 已创建显式目标占位文件，但 source `sqlite3.connect()` 位于 `try` 外；直接调用公共 `backup()` 时，源连接异常会留下目标文件。`do_backup()` 会二次清理自己的 partial，但不能满足 `backup()` 在 `:56`—`:61` 声明的独立安全契约。

`scripts/backup_db.py:161`—`:163` 与 `:189`—`:191` 的两个注入钩子也位于对应清理边界外。钩子异常会留下 partial，并绕过结构化退出码。

#### R8-04 / P1：验收记录把失败的运维演练写成了通过

`docs/2026-09-10-phase-5-2-verification.md:43`—`:57` 明确记录 adopt 在 `schema_verified` 阶段失败、退出码为 1，原因是演练 source 只有 1 张不完整的 `app_setting` 表。该记录没有证明完整 ORM source 的真实命令链可以通过，也没有记录 source 前后哈希、copy 的最终 head revision。

同一文档 `:92` 却勾选“临时副本运维演练全部退出 0”，`:94` 据此宣布 Phase 5.2 达标。记录内部自相矛盾，不能作为放行证据。

#### R8-05 / P1：权威 Runbook 无法从 source 完成完整接管

`docs/database-migration-runbook.md:20`—`:22` 宣称 §2.1 是唯一权威流程，但 `:37`—`:64` 直接要求一个已经存在的 `web.<ts>.db`，没有停止写入和创建备份的命令；`:73`—`:85` 也只有 `--list`，没有 `backup_db --source ... --destination-dir ...`。

`docs/database-migration-runbook.md:16` 还要求所有命令传 `-x database_url=...`，而 backup、adopt、smoke 的 module CLI 没有这个参数。操作员无法照文档原样完成“停写 → backup → adopt”。

#### R8-06 / P1：SQLite URL 可以绕过绝对路径门禁

`scripts/adopt_db_copy.py:82`—`:89` 对任何 `sqlite:///` 前缀直接返回真，`sqlite:///relative.db` 因此通过检查；`:186`—`:190` 再把它解析并 resolve 到当前工作目录。这仍是依赖 cwd 的相对路径，与 `:83`—`:86` 声明的显式绝对路径要求冲突。

`tests/test_database_adoption.py::TestAdoptionCliInput::test_relative_source_rejected` 和 `tests/test_database_adoption.py::TestAdoptionCliInput::test_relative_backup_copy_rejected` 只覆盖普通相对路径，没有覆盖 URL 形式。

#### R8-07 / P2：`list[str]` dependency 契约只校验了外层类型

`scripts/adopt_db_copy.py:163`—`:170` 看到 list 就直接执行 `"; ".join(report)`。dependency 返回 `[1]`、`[ValueError()]` 等非字符串成员时会抛出未捕获 `TypeError`，无法返回文档承诺的结构化 `AdoptionResult`。

`tests/test_database_adoption.py::TestAdoptionStateContracts::test_wrong_return_type_fails_stage` 只覆盖整个返回值为字符串，没有覆盖 list 成员类型错误。

#### R8-08 / P2：验收元数据与实际运行环境不一致

`docs/2026-09-10-phase-5-2-verification.md:6`—`:10` 把受管解释器的 `sys.version` 写成 3.13.12，并把验收 SHA 写成 `a1adcb3`。本轮直接运行该绝对路径得到 Python `3.13.14`；当前验收提交为 `3c0d7927a46a92b60623337381d3aca252c459c3`。

`scripts/smoke_db_copy.py:7`—`:10` 仍称 production smoke 使用 dependency override，但当前实现已经改成导入前绑定环境并使用真实全局数据库依赖，说明代码注释也未随实现更新。

### 1.2 已执行验证

| 检查 | 结果 |
|---|---|
| 受管 Python 全量测试 | `256 passed, 2159 warnings`，退出 0；解释器实际版本 3.13.14。 |
| 数据库专项测试 | `test_backup_restore.py`、`test_cli_entrypoints.py`、`test_database_adoption.py` 共 63 项通过。 |
| 前端测试 | Node test 34 项通过；Vitest 32 项通过，退出 0。 |
| 前端构建 | 2256 modules transformed，退出 0。 |
| `git diff --check cf3f6d0..HEAD` | 无错误，退出 0。 |
| 临时反例 | `sqlite:///relative.db` 被接受；`list[int]` 触发未捕获 TypeError；发布后统计异常时最终 `.db` 仍存在。 |
| 数据边界 | 未读取或修改 `data/web.db`；测试与反例只使用临时库。 |

### 1.3 本轮已满足项

- `tests/test_cli_entrypoints.py::test_adopt_chain_gbk_success` 用真实 subprocess 跑通 backup → adopt，并验证 GBK 输出不会制造 UnicodeEncodeError。
- `tests/test_database_adoption.py` 的 production dependency happy path 已使用隔离 smoke 子进程，父进程的 unrelated 数据库保持不变。
- `tests/test_backup_restore.py::TestPartialPublish` 已覆盖 partial 发布窗口、校验前不可见、额外表和内容篡改。
- `scripts/adopt_db_copy.py:242`—`:249` 已显式 dispose 用于读取 Alembic revision 的 engine。
- 上一轮两份实施计划已经纳入 Git，干净 checkout 可以读取评审依据。

---

## 2. 下一阶段范围与门禁

阶段名称：**Phase 5.3 跨平台发布及运维实证**，预计 2～3 个开发工作日。

本阶段只关闭 R8-01～R8-08，不实现 Phase 6 的共享配置、operation/run 状态机、权限或界面功能。所有写操作和演练只能针对测试临时目录中的 source、copy 与 unrelated 哨兵库；禁止对 `data/web.db` 执行 backup、stamp、upgrade、init_db 或 smoke。

| 文件 | 本阶段责任 |
|---|---|
| `scripts/backup_db.py` | 跨平台 no-clobber 发布、提交点语义、完整失败清理。 |
| `scripts/adopt_db_copy.py` | 统一绝对路径解析与完整 dependency 返回值契约。 |
| `scripts/smoke_db_copy.py` | 修正与真实隔离机制不一致的说明。 |
| `tests/test_backup_restore.py` | 覆盖 POSIX 覆盖语义、发布后异常和所有清理边界。 |
| `tests/test_database_adoption.py` | 覆盖 SQLite URL 相对路径和 list 成员类型反例。 |
| `tests/test_cli_entrypoints.py` | 固化完整 backup → adopt CLI happy path及三库隔离。 |
| `.github/workflows/database-safety.yml` | 在 Windows 与 Linux 实跑发布、备份和接管专项测试。 |
| `docs/database-migration-runbook.md` | 唯一、完整、可复制的运维链。 |
| `docs/2026-09-10-phase-5-2-verification.md` | 追加第八轮复核更正，撤回错误放行。 |
| `docs/2026-09-10-phase-5-3-verification.md` | 记录本阶段原始命令、版本、SHA、退出码与三库证据。 |

---

## 3. 实施任务

### Task 1：替换为真正跨平台的原子 no-clobber 发布

**Files:**

- Modify: `scripts/backup_db.py:106`
- Modify: `tests/test_backup_restore.py:594`
- Create: `.github/workflows/database-safety.yml`

- [ ] **Step 1：先写目标已存在的内容保护反例**

扩展 `TestAtomicPublish::test_publish_refuses_overwrite`：除断言 `FileExistsError` 外，必须断言目标原始字节未变化、partial 仍可由调用方清理。测试命名需直接表达 `existing_destination_bytes_are_preserved`。

- [ ] **Step 2：写两个竞争发布的内容归属测试**

让两个 partial 包含不同 marker。并发发布同一个 dst 后，断言恰一个提交成功、另一个得到结构化冲突，最终内容完整等于某一个 partial，不能出现混合或空文件。

- [ ] **Step 3：实现同目录 hard-link 发布原语**

用 `os.link(partial, dst)` 创建最终目录项；该操作在目标已存在时以 `EEXIST/FileExistsError` 失败，不依赖 POSIX `rename` 的覆盖语义。link 成功即为提交点，再 unlink partial。partial 与 dst 已由 `do_backup()` 保证位于同一目录；遇到不支持 hard link、权限或文件系统错误时必须 fail closed，禁止回退到 `os.rename`、`os.replace` 或“先 exists 再 rename”。

- [ ] **Step 4：定义 link 成功后的清理语义**

link 成功后，最终文件已经提交。若删除 partial 失败，返回“已发布 + 清理告警”的结构化结果，CLI 仍以成功退出，禁止把已存在的最终 `.db` 报告成备份失败。告警必须包含 partial 绝对路径，便于人工清理；`--list` 仍只展示 `.db`。

- [ ] **Step 5：增加 Windows/Linux 双平台门禁**

新增 `.github/workflows/database-safety.yml`，矩阵至少包含 `windows-latest` 与 `ubuntu-latest`，使用项目约束安装依赖并运行：

```text
pytest -q tests/test_backup_restore.py tests/test_database_adoption.py tests/test_cli_entrypoints.py
```

两个系统都必须证明既有目标字节不变、并发恰一个成功。未取得两平台退出 0 的记录前，不得写“跨平台达标”。

- [ ] **Step 6：运行 Task 1 测试**

```powershell
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m pytest -q tests/test_backup_restore.py -p no:cacheprovider
```

### Task 2：把所有可能失败的工作移到发布提交点之前

**Files:**

- Modify: `scripts/backup_db.py:56`
- Modify: `scripts/backup_db.py:130`
- Modify: `tests/test_backup_restore.py:385`

- [ ] **Step 1：写公共 `backup()` 源连接失败测试**

直接调用 `backup(source, explicit_dst)`，注入只读 source connect 失败；断言抛出原始异常、explicit dst 不存在、无 sidecar。不得只通过 `do_backup()` 间接覆盖。

- [ ] **Step 2：写钩子异常清理测试**

分别让 `_POST_WRITE_HOOK` 和 `_PRE_PUBLISH_HOOK` 抛异常，断言 `do_backup()` 返回非 0，目录中既无最终 `.db`，也无 `.partial` 和 SQLite sidecar。

- [ ] **Step 3：写发布后统计异常反例**

注入 `_table_counts` 或 `Path.stat` 的失败时机，证明统计只在 publish 前发生。测试必须断言：若 publish 尚未发生，失败不留下最终文件；一旦 publish 已提交，后续代码不再调用这些可失败操作。

- [ ] **Step 4：重排 `backup()` 的异常边界**

把 source connect、destination connect、SQLite backup、连接关闭和目标清理纳入一个明确的资源/清理边界。任何提交前异常都删除占位文件与 sidecar；清理失败继续保留原异常与清理异常的因果链。

- [ ] **Step 5：发布前冻结完成信息**

在 partial 上完成 `verify_restore`、表统计、文件大小读取和完成消息所需的格式化，保存不可变结果。调用 publish 后只消费已经冻结的数据；不得重新打开 source/dst、不得重新 stat。输出异常需要由 CLI 边界捕获，不能改变“已提交”的成功状态。

- [ ] **Step 6：统一钩子与 production 清理路径**

两个测试钩子必须放在提交前 cleanup 边界内。删除散落的重复清理分支，使用一个 helper 统一 partial/sidecar 清理与退出码映射，避免新增失败点再次绕过清理。

### Task 3：关闭路径解析和 dependency 契约绕过

**Files:**

- Modify: `scripts/adopt_db_copy.py:82`
- Modify: `scripts/adopt_db_copy.py:107`
- Modify: `scripts/adopt_db_copy.py:151`
- Modify: `tests/test_database_adoption.py`
- Modify: `tests/test_cli_entrypoints.py`

- [ ] **Step 1：写 SQLite URL 相对路径反例**

对 source 与 backup-copy 分别覆盖 `sqlite:///relative.db`、`sqlite:///./relative.db`，真实 module CLI 必须退出 2、输出 `[FAIL]`，并且 cwd 下不得创建或修改同名文件。

- [ ] **Step 2：写跨平台绝对 URL 正例**

Windows 覆盖 `sqlite:///D:/absolute/path.db`，POSIX 覆盖 `sqlite:////tmp/absolute/path.db`。解析结果必须是绝对路径；测试按运行平台选择合法样例，不在另一平台伪造通过。

- [ ] **Step 3：统一解析与校验入口**

提取一个函数，先剥离受支持的 `sqlite:///` 前缀，再对剥离后的 path 执行平台原生 `Path.is_absolute()`，最后才允许 resolve。CLI 和 `adopt_database_copy()` 都必须调用这一入口，避免程序化调用绕过 CLI 护栏。

- [ ] **Step 4：写 list 成员类型反例**

dependency 返回 `[1]`、`[Exception("boom")]`、`["difference", 1]` 时，状态机都必须返回 `failed_stage`、非 0 code 和可序列化 detail；不得抛 TypeError，也不得继续执行下一阶段。

- [ ] **Step 5：完整校验 `list[str]`**

只有 `None`、空 list 和元素全部为 `str` 的 list 合法。非空 `list[str]` 表示该阶段有差异；其他任何返回值形成 `dependency_contract` 或当前阶段的结构化失败。不要用隐式 `str()` 把程序错误伪装成业务差异。

- [ ] **Step 6：运行路径与状态机测试**

```powershell
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m pytest -q tests/test_database_adoption.py tests/test_cli_entrypoints.py -p no:cacheprovider
```

### Task 4：把 Runbook 收敛成一条完整可复制流程

**Files:**

- Modify: `docs/database-migration-runbook.md:12`
- Modify: `README.md`
- Modify: `scripts/smoke_db_copy.py:2`
- Modify: `tests/test_cli_entrypoints.py`

- [ ] **Step 1：删除错误的全局 `-x` 规则**

明确 `-x database_url=...` 只用于 Alembic 命令；backup、adopt、smoke 分别使用自己的 argparse 参数。文档中的解释器路径可以作为本机示例，但必须同时提供环境无关的 `python -m` 形式。

- [ ] **Step 2：在 §2.1 给出唯一完整命令链**

按固定顺序写明：进入停写窗口 → 记录 source 哈希/mtime → 执行 `python -m scripts.backup_db --source ... --destination-dir ...` → 从 `[PASS]` 输出取得唯一最终路径 → 执行 `python -m scripts.adopt_db_copy --source ... --backup-copy ... --revision 0001` → 复核 revision/head → 记录三库证据 → 结束停写窗口。

- [ ] **Step 3：删除重复或半套流程**

§4 只解释 backup/list/恢复语义并链接回 §2.1；不得再提供另一套可被误认为接管流程的命令。故障排查命令必须标明只读或写副本，并说明失败后从新备份重新开始。

- [ ] **Step 4：更新 smoke 说明**

修正 `scripts/smoke_db_copy.py:7`—`:10`，准确说明生产 smoke 在子进程导入 backend 前设置 `DATABASE_URL`，随后验证全局 engine、lifespan 和真实 SessionLocal；删除 dependency override 的旧描述。

- [ ] **Step 5：用测试逐字固化命令形状**

`tests/test_cli_entrypoints.py` 的完整链必须使用 Runbook 中相同的 module 名和参数，不得通过内部函数替代。至少断言 backup 输出能唯一解析出 final path，adopt 六阶段均 `[PASS]`，最终退出 0。

### Task 5：执行完整 ORM 三库运维演练并纠正历史裁决

**Files:**

- Modify: `docs/2026-09-10-phase-5-2-verification.md:78`
- Create: `docs/2026-09-10-phase-5-3-verification.md`

- [ ] **Step 1：先撤回 Phase 5.2 错误放行**

在 Phase 5.2 验收文档末尾追加“第八轮复核更正”，明确 `:92` 与 `:94` 作废，记录失败阶段、退出码 1、解释器实际版本 3.13.14 和完整审核 SHA。保留旧记录作为审计轨迹，不静默改写原始输出。

- [ ] **Step 2：构造完整临时 source 与 unrelated 哨兵**

在系统临时目录创建 source、backup 目录和 unrelated。source 必须由当前 `Base.metadata.create_all()` 建出全部业务表并插入可识别 setting；source 保持未 stamp。unrelated 写入独立 marker。演练前记录 source/unrelated 的 SHA-256、大小与 mtime_ns。

- [ ] **Step 3：只运行 Runbook 的真实命令**

顺序执行 module backup 与 module adopt，不得 monkeypatch dependency，不得调用内部函数，不得补跑单独阶段。记录每条完整命令、stdout/stderr 和退出码；两条命令都必须退出 0，adopt 六阶段必须全部 `[PASS]`。

- [ ] **Step 4：记录三库终态证据**

演练后必须证明：source SHA-256、大小、mtime_ns 不变；unrelated SHA-256、大小、mtime_ns 不变；copy 内容与 source 业务数据一致；copy Alembic current 等于唯一 head；copy smoke 读到预置 setting；目录无 partial/sidecar 残留。

- [ ] **Step 5：记录可复现元数据**

Phase 5.3 验收文档必须包含 `sys.executable`、`sys.version` 原始输出、`git rev-parse HEAD` 完整 SHA、工作区状态、命令退出码和 CI 两平台 run 链接或不可变 run id。目录名中的 `3.13.12` 不能代替 `sys.version`。

### Task 6：全量回归与放行

**Files:**

- Modify: `docs/2026-09-10-phase-5-3-verification.md`

- [ ] **Step 1：运行后端全量测试**

```powershell
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m pytest tests -q -p no:cacheprovider
```

- [ ] **Step 2：运行前端测试与构建**

```powershell
Set-Location frontend
pnpm test
pnpm build
```

- [ ] **Step 3：检查提交差异与工作区**

```powershell
git diff --check <phase-5.3-base>..HEAD
git status --short
git log --oneline <phase-5.3-base>..HEAD
```

- [ ] **Step 4：取得 Windows/Linux 专项测试结果**

GitHub Actions 的 `windows-latest` 与 `ubuntu-latest` 都必须退出 0。若没有远端 CI 权限，则由两台对应系统运行同一专项命令并保存完整环境与退出码；单个平台通过不得替代此门禁。

- [ ] **Step 5：提交验收证据**

```powershell
git add scripts tests docs README.md .github/workflows/database-safety.yml
git commit -m "fix: close cross-platform database publish gates"
```

---

## 4. Phase 5.3 完成门禁

- [ ] Windows 与 Linux 上，既有 destination 的字节在冲突发布后完全不变。
- [ ] Windows 与 Linux 上，并发发布同一 destination 恰一个成功，最终文件内容完整。
- [ ] 代码中不再用 `os.rename` 或 `os.replace` 实现 no-clobber 发布。
- [ ] 发布前任一失败不留下最终 `.db`；发布后不再执行数据库读取或文件 stat。
- [ ] `backup()` 的 source connect 失败不会留下显式目标文件。
- [ ] 两个注入钩子抛异常时 partial 与 sidecar 都被清理。
- [ ] `sqlite:///relative.db` 与普通相对路径都被 source、backup-copy 两个参数拒绝。
- [ ] dependency 返回含非字符串成员的 list 时得到结构化失败，并立即停止。
- [ ] Runbook 从停写、backup、adopt 到终态复核只有一条权威流程，所有参数可原样执行。
- [ ] 真实完整 ORM 三库演练中 backup 与 adopt 均退出 0，六阶段全通过。
- [ ] source 与 unrelated 的 SHA-256、大小、mtime_ns 在演练前后完全一致。
- [ ] copy 的 current revision 等于唯一 head，且真实 smoke 读到预置业务数据。
- [ ] Phase 5.2 错误放行已追加更正；Phase 5.3 验收记录使用实际 Python 版本与完整 HEAD SHA。
- [ ] 后端全量测试、前端测试、前端 build、diff check 全部退出 0。
- [ ] Windows/Linux 专项 CI 均退出 0，工作区只包含预期文件。

只有以上清单全部有可复现证据后，Phase 5 才能重新裁决为达标，并开始 Phase 6 共享状态架构实施。
