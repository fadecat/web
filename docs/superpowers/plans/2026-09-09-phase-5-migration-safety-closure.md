# 第五轮 Review 与迁移安全收口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复第四阶段迁移、恢复验证和前端测试中可复现的错误，使数据库接管工具真正只读、备份验证能发现记录替换、测试零失败且零资源残留。

**Architecture:** 保留现有 Alembic 初始版本，先修正检查器和恢复验证的安全语义。数据库读取统一使用 SQLite 只读连接，迁移元数据表与业务表分开处理；测试资源使用显式关闭的连接和失败即停的清理门禁。完成后只在数据库副本上执行完整接管演练，不触碰日常 `data/web.db`。

**Tech Stack:** Python 3.11+、SQLite、SQLAlchemy 2、Alembic、pytest、Vue 3、Vitest、Node test。

---

## 1. Review 范围与裁决

审核范围：`c23b24be29632f1ef44d822182068c94f2acfe45..b4c51b4b24e5a1f06a461ef697363f6a4177302a`，共 8 个提交、28 个文件，新增 2798 行、删除 135 行。本地 `main` 相对本地记录的 `origin/main` 超前 25 个提交；本轮没有 fetch，不能据此判断服务器状态。开始审查时已跟踪工作区和暂存区为空。

**裁决：不达标。** 旧配置回存、刷新首个微任务、请求级 Session 和 Alembic 初始表结构已有对应实现，但第四阶段目标是建立可执行的迁移安全基线；数据库接管链仍会修改误填路径、无法验证真实记录、stamp 后验证必失败，前端全量测试当前退出码为 1，测试产物清理门禁也没有生效。当前提交不能通过第四阶段门禁，不得进入日常库接管或共享配置入库。

### 1.1 具体缺陷

#### R5-01 / P1：标称“只读”的结构检查会创建不存在的数据库并泄漏连接

`scripts/check_db_baseline.py:46`—`:51` 对路径直接 `create_engine`，在 Inspector 真正查询之前就 dispose engine；随后 `inspector.get_table_names()` 位于 `:53`，会重新连接并在路径不存在时创建空 SQLite 文件，新连接池又没有在函数末尾 dispose。

本轮临时目录反例：调用前 `missing.db` 不存在，`compare_schema()` 返回空差异且调用后文件存在。传入空 metadata 时甚至会把新建空库判定为“结构匹配”。这同时造成迁移测试结束时数据库文件仍被占用，是 `.test-artifacts` 清理失败的来源之一。

#### R5-02 / P1：恢复验证没有比较记录主键值或业务内容

`scripts/verify_db_restore.py:47`—`:55` 的 `_pk_sets()` 读取 `PRAGMA table_info`，得到的是“哪些列构成主键”，不是每行的主键值。`:91`—`:101` 只比较行数和主键列定义。两个库各有两行、主键分别为 `{1,2}` 和 `{1,99}` 时，`verify_restore()` 返回空差异。

现有 `test_restore_verifier_detects_pk_drift` 在 `tests/test_backup_restore.py:94`—`:108` 改变的是表的主键定义，没有覆盖同结构、同行数、不同记录，也没有覆盖主键相同但非主键数据被篡改。

#### R5-03 / P1：runbook 的 stamp→restore 顺序按文档执行必然失败

`docs/database-migration-runbook.md:32`—`:43` 先在备份副本写入 `alembic_version`，再将副本与未 stamp 的源库比较。`scripts/verify_db_restore.py:80`—`:88` 把该表视为业务表差异，`scripts/check_db_baseline.py:53`—`:59` 也把它视为 ORM 未定义的多余表。

本轮按文档在临时 ORM 库执行：stamp 前结构检查为空；stamp 后恢复验证同时报告“备份副本多余表 alembic_version”和“ORM 未定义”。因此当前 runbook 的成功出口不可达。

#### R5-04 / P1：前端全量测试退出码为 1，验收记录写成通过

`frontend/src/pages/DataStatus.vue:569` 使用 `v-show="c.overview && ..."`，但 Vue 仍会渲染并求值隐藏节点；`:570` 直接访问 `c.overview.first_date.slice()`。新增测试在 `frontend/tests/unit/DataStatus.test.js:137` 和 `:174` 提供没有 overview 的合法 capability，触发 4 个未处理 TypeError。

本轮执行 `pnpm test`：Node 34 项通过、Vitest 30 个断言用例显示通过，但 Vitest 捕获 4 个 unhandled rejection，最终进程退出 1。`docs/2026-09-09-phase-4-verification.md:20`—`:22` 将其记录为 30 passed、0 failed，证据与当前 checkout 不一致。

#### R5-05 / P2：测试清理失败仍被放行，仓库已残留 28 个 case 目录

`tests/conftest.py:217` 注释声称“清理失败即测试失败”，但 `_remove_tree_fail_closed` 在 `:246`—`:247` 捕获 OSError 后仅 warnings.warn。第四阶段计划要求失败即停，验收记录仍在 `docs/2026-09-09-phase-4-verification.md:104` 勾选“测试文件完全离开”。

本轮后端测试虽为 188 passed，却产生 14 条测试目录清理失败 warning；检查时 `.test-artifacts` 下有 28 个 `case_*` 目录。多个测试使用 `with sqlite3.connect(...)`，例如 `tests/test_backup_restore.py:19`—`:24`；sqlite3 Connection 上下文只管理事务，不负责 close，文件句柄会留到垃圾回收。

#### R5-06 / P2：备份 CLI 的 `--list` 文档用法不可执行，覆盖测试没有调用生产入口

`scripts/backup_db.py:11` 声称可单独运行 `--list`，但 `:116` 将 `--source` 设为 required，实测 `python scripts/backup_db.py --list` 由 argparse 返回 2。`tests/test_backup_restore.py:41`—`:50` 在测试函数内手工抛 FileExistsError，没有调用 `do_backup()` 的 `:80`—`:83` 覆盖保护，测试名无法证明生产入口拒绝覆盖。

#### R5-07 / P3：迁移文件存在格式错误，验收记录称 diff check 通过

`migrations/versions/0001_initial_schema.py:4` 的 `Revises:` 行含尾随空格。`git diff --check c23b24b..b4c51b4` 明确报告该行，而 `docs/2026-09-09-phase-4-verification.md:23` 记录为通过。

#### R5-08 / P2：初始 revision 的结构等价没有被测试证明

`tests/test_migration_baseline.py:49`—`:57` 对 Alembic 产物只检查表名；`:99`—`:107` 则用 `Base.metadata.create_all()` 建库后再和同一份 metadata 比较，属于自比较。`:122`—`:143` 只覆盖缺列和 nullable 漂移，没有上一阶段要求的缺唯一约束、索引列顺序反例。初始 revision 的列、约束或索引写错时，现有门禁仍可能通过。

#### R5-09 / P2：新增前端测试中有断言无法证明测试名所述行为

`frontend/tests/unit/api.test.js:48`—`:53` 用纯数字 `930955` 验证 `encodeURIComponent`；删除编码实现后预期 URL 不变，该用例没有覆盖编码。`frontend/tests/unit/Factors.test.js:102`—`:119` 的名称声称覆盖“空评级和未知评级”，复制路径只验证未知评级。两条用例都需要加入能杀死错误实现的输入和断言。

### 1.2 已执行验证

| 检查 | 本轮结果 |
|---|---|
| 后端 `pytest tests -q -p no:cacheprovider` | 188 passed，2154 warnings；其中 14 条为测试目录清理失败。 |
| frontend `pnpm test` | Node 34 passed；Vitest 30 个用例完成但有 4 个未处理异常，整体退出码 1。 |
| frontend `pnpm build` | 通过，2256 modules transformed。 |
| `git diff --check c23b24b..b4c51b4` | 失败：初始 migration 第 4 行尾随空格。 |
| 不存在路径的 checker 反例 | 调用后创建空文件；空 metadata 时错误返回无差异。 |
| 同行数不同主键值的 restore 反例 | 错误返回无差异。 |
| runbook 临时库 stamp→verify | 必然报告 `alembic_version` 差异。 |

### 1.3 已满足的上一阶段项

- `test_legacy_get_response_can_be_posted_as_current_config` 通过；`backend/services/cb_factors.py:168` 已移除迁移字段。
- polling 的 start→stop/dispose 两个同轮反例通过；`frontend/src/utils/dataManagementPolling.js:66`—`:69` 在 load 前复核会话。
- `test_request_session_is_closed` 通过；`tests/conftest.py:179`—`:185` 使用请求级生成器关闭 Session。
- 真实 lifespan 的启用/禁用 scheduler 两条接线测试通过，见 `tests/test_test_environment.py:50`—`:88`。
- 初始 revision 能在临时空库创建 10 张业务表，重复 upgrade 和临时 downgrade 用例通过。

## 2. 下一阶段范围与门禁

阶段名称：**迁移安全收口与副本接管演练**，预计 4～6 个开发工作日。

本阶段只做三件事：关闭 R5-01～R5-09；让 migration 创建结果与 ORM 完整比对；对测试数据库副本完成备份、验证、stamp、upgrade 和应用冒烟。本阶段不迁移 `data/web.db`，不将 factors/index universe 搬入数据库，不新增 operation/run 表。

文件责任：

| 路径 | 责任 |
|---|---|
| `frontend/src/pages/DataStatus.vue` | 对缺失/不完整 capability overview 安全渲染。 |
| `frontend/tests/unit/api.test.js` | 用必须编码的路径参数证明 URL 编码。 |
| `frontend/tests/unit/Factors.test.js` | 分别覆盖空评级和未知评级的复制结果。 |
| `scripts/sqlite_readonly.py` | 统一路径解析、存在性检查和 SQLite 只读连接。 |
| `scripts/check_db_baseline.py` | 只读结构比对；忽略明确的 Alembic 元数据表。 |
| `scripts/verify_db_restore.py` | 比较业务表、主键值及完整行内容摘要。 |
| `scripts/backup_db.py` | 明确的 backup/list CLI 和拒绝覆盖入口。 |
| `tests/conftest.py` | 测试目录严格清理；发现句柄泄漏即失败。 |
| `tests/test_migration_baseline.py` | migration 产物与 ORM 的结构等价和只读反例。 |
| `tests/test_backup_restore.py` | 生产备份入口及内容篡改反例。 |
| `tests/test_database_adoption.py` | 完整副本接管链及隔离应用冒烟。 |
| `docs/database-migration-runbook.md` | 与可执行接管顺序保持一致。 |

## 3. 实施任务

### Task 1：修复 DataStatus capability 渲染和测试收尾

**Files:**

- Modify: `frontend/src/pages/DataStatus.vue:569`
- Modify: `frontend/tests/unit/DataStatus.test.js:135`
- Modify: `frontend/tests/unit/api.test.js:48`
- Modify: `frontend/tests/unit/Factors.test.js:102`
- Modify: `docs/2026-09-09-phase-4-verification.md:15`
- Test: `frontend/tests/unit/DataStatus.test.js`

- [ ] **Step 1: 增加不完整 overview 的失败用例**

测试探测返回三种 capability：无 overview、overview 日期为 null、完整 overview。挂载后等待所有 Promise，断言页面存在且只展示完整日期；使用 Vue `errorHandler` 收集错误并断言为空：

```javascript
const renderErrors = [];
const wrapper = mount(DataStatus, {
  global: {
    config: { errorHandler: (error) => renderErrors.push(error) },
    stubs: EP_STUBS,
  },
});

probeIndex.mockResolvedValue({
  code: '399296', name: '创成长', probe_token: 'tok-1',
  capabilities: [
    { key: 'quote', status: 'available', label: '收盘价' },
    { key: 'valuation', status: 'available', label: '估值', overview: { first_date: null, latest_date: null, count: 0 } },
    { key: 'dividend', status: 'available', label: '股息率', overview: { first_date: '2020-01-02', latest_date: '2026-09-09', count: 100 } },
  ],
});
```

- [ ] **Step 2: 运行单文件并确认当前失败**

Run: `cd frontend && pnpm test:unit -- --run tests/unit/DataStatus.test.js`

Expected: 当前实现出现 `Cannot read properties of undefined (reading 'first_date')` 并退出 1。

- [ ] **Step 3: 将显示条件改为真正的条件渲染并安全格式化日期**

在 script setup 增加：

```javascript
function overviewDate(value, mode) {
  if (typeof value !== 'string' || !value) return '暂无';
  return mode === 'year' ? value.slice(0, 4) : value.slice(5);
}
```

模板改为外层迭代、内层条件渲染：

```vue
<template v-for="c in form.caps" :key="c.key + '-ov'">
  <div v-if="c.overview && c.status === 'available'" class="cap-ov">
    {{ capLabel(c) }}：{{ overviewDate(c.overview.first_date, 'year') }} 年起 ·
    最新 {{ overviewDate(c.overview.latest_date, 'month-day') }} ·
    共 {{ c.overview.count ?? 0 }} 条
  </div>
</template>
```

- [ ] **Step 4: 让每个组件测试显式 unmount**

在 DataStatus 测试维护 `wrappers` 集合，mountPage 后登记，并在 afterEach 中先逐个 unmount，再清空 timers、等待一次 flushPromises，最后恢复 real timers。`vi.getTimerCount()` 必须在 fake timers 生效时调用。

- [ ] **Step 5: 补齐两条不能杀死错误实现的测试**

`api.test.js` 使用 `code = "A/B + 1"`，断言请求路径包含 `A%2FB%20%2B%201`；先临时去掉生产代码中的 `encodeURIComponent`，确认用例失败，再恢复实现。`Factors.test.js` 分别提供 `rating: ""` 和未登记的评级值，执行真实复制入口并断言序列化结果；不得只断言下拉选项存在。

- [ ] **Step 6: 重跑前端测试**

Run: `cd frontend && pnpm test`

Expected: Node 与 Vitest 均退出 0，Vitest 输出没有 Unhandled Errors。

- [ ] **Step 7: 更正第四阶段验收记录并提交**

在历史记录追加“本轮复核更正”，记录原 30 个断言完成但存在 4 个未处理异常，不能算整体通过；保留原文，不覆盖审计历史。

```bash
git add frontend/src/pages/DataStatus.vue frontend/tests/unit/DataStatus.test.js frontend/tests/unit/api.test.js frontend/tests/unit/Factors.test.js docs/2026-09-09-phase-4-verification.md
git commit -m "fix: safely render incomplete capability overviews"
```

### Task 2：建立真正只读且会释放连接的 SQLite 入口

**Files:**

- Create: `scripts/sqlite_readonly.py`
- Modify: `scripts/check_db_baseline.py:19`
- Modify: `tests/test_migration_baseline.py:98`
- Test: `tests/test_migration_baseline.py`

- [ ] **Step 1: 写不存在路径和连接释放反例**

```python
def test_compare_schema_rejects_missing_database_without_creating_it(test_artifact_dir):
    path = test_artifact_dir / "missing.db"
    with pytest.raises(FileNotFoundError):
        compare_schema(f"sqlite:///{path.as_posix()}", Base.metadata)
    assert not path.exists()


def test_compare_schema_releases_database_handle(test_artifact_dir):
    path = test_artifact_dir / "closed.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()
    assert compare_schema(f"sqlite:///{path.as_posix()}", Base.metadata) == []
    path.rename(test_artifact_dir / "renamed.db")
```

- [ ] **Step 2: 实现 URL 解析和只读 engine**

`scripts/sqlite_readonly.py`：

```python
from __future__ import annotations

import sqlite3
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool


def sqlite_path_from_url(url: str) -> Path:
    if not url.startswith("sqlite:///"):
        raise ValueError(f"仅支持 SQLite 文件 URL: {url}")
    path = Path(url[len("sqlite:///"):]).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"SQLite 目标不是普通文件: {path}")
    return path


def readonly_engine(url: str):
    path = sqlite_path_from_url(url)

    def connect():
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)

    return create_engine("sqlite://", creator=connect, poolclass=NullPool)
```

- [ ] **Step 3: 在 engine 生命周期内完成全部 Inspector 调用**

`compare_schema` 用 `engine = readonly_engine(url)`，所有 `get_table_names/get_columns/get_pk_constraint/get_unique_constraints/get_indexes` 都放在 try 内，最后 finally `engine.dispose()`。禁止在 dispose 后继续使用 Inspector。

明确系统表：

```python
MIGRATION_TABLES = {"alembic_version"}
db_tables = set(inspector.get_table_names()) - MIGRATION_TABLES
model_tables = set(metadata.tables.keys()) - MIGRATION_TABLES
```

- [ ] **Step 4: 比较 Alembic 创建的真实结构**

扩展空库 upgrade 测试：upgrade 后除了检查表名，还必须执行：

```python
assert compare_schema(f"sqlite:///{db_path.as_posix()}", Base.metadata) == []
```

再用 `alembic.command.check(_config(db_path))` 断言没有新的 upgrade operation；这会捕获初始 revision 与 ORM 的列、索引和约束漂移。

新增两条反例：从 Alembic 创建的库删除一个唯一约束的对应唯一索引时 `compare_schema` 必须报差异；用同名索引但反转列顺序时也必须报差异。反例直接修改 migration 产物，不能用 `Base.metadata.create_all()` 生成被测库。

- [ ] **Step 5: 运行迁移测试**

Run: `python -m pytest tests/test_migration_baseline.py -q -p no:cacheprovider`

Expected: 缺失路径抛 FileNotFoundError 且不创建文件；migration 库与 ORM 比较为空；测试结束无清理 warning。

- [ ] **Step 6: 提交**

```bash
git add scripts/sqlite_readonly.py scripts/check_db_baseline.py tests/test_migration_baseline.py
git commit -m "fix: make schema inspection strictly read only"
```

### Task 3：让恢复验证覆盖主键值和全部业务内容

**Files:**

- Modify: `scripts/verify_db_restore.py:23`
- Modify: `tests/test_backup_restore.py:53`
- Test: `tests/test_backup_restore.py`

- [ ] **Step 1: 写同结构、同行数篡改反例**

```python
def test_restore_verifier_detects_replaced_primary_key(test_artifact_dir):
    source, backup_copy = make_pair(test_artifact_dir, rows=2)
    with closing(sqlite3.connect(backup_copy)) as conn:
        conn.execute("update sample set id = 99 where id = 2")
        conn.commit()
    differences = verify_restore(source, backup_copy)
    assert any("主键值集合" in item for item in differences)


def test_restore_verifier_detects_changed_non_key_value(test_artifact_dir):
    source, backup_copy = make_pair(test_artifact_dir, rows=2)
    with closing(sqlite3.connect(backup_copy)) as conn:
        conn.execute("update sample set value = 'tampered' where id = 2")
        conn.commit()
    differences = verify_restore(source, backup_copy)
    assert any("内容摘要" in item for item in differences)
```

- [ ] **Step 2: 统一只读 sqlite3 连接**

在 `sqlite_readonly.py` 增加：

```python
def readonly_connection(path: Path) -> sqlite3.Connection:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"SQLite 目标不是普通文件: {resolved}")
    return sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
```

`verify_db_restore.py` 的 integrity、表、行数、主键和摘要查询都使用 `contextlib.closing(readonly_connection(path))`。

- [ ] **Step 3: 比较真实主键值集合**

按 `PRAGMA table_info` 的 pk 序号排序列名，然后查询每行键值：

```python
def _primary_key_values(db_path: Path, tables: set[str]) -> dict[str, set[tuple]]:
    result = {}
    with closing(readonly_connection(db_path)) as conn:
        for table in sorted(tables):
            info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            columns = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5] > 0]
            select = ", ".join(f'"{column}"' for column in columns)
            result[table] = set(conn.execute(f'SELECT {select} FROM "{table}"').fetchall())
    return result
```

业务表必须有主键；发现空 columns 直接报告“无主键，无法证明记录集合一致”。

- [ ] **Step 4: 增加确定性的全行内容摘要**

```python
def _table_digest(conn: sqlite3.Connection, table: str) -> str:
    columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
    quoted = ", ".join(f'quote("{column}")' for column in columns)
    pk_info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    pk_columns = [row[1] for row in sorted(pk_info, key=lambda row: row[5]) if row[5] > 0]
    order = ", ".join(f'"{column}"' for column in (pk_columns or columns))
    digest = hashlib.sha256()
    for row in conn.execute(f'SELECT {quoted} FROM "{table}" ORDER BY {order}'):
        digest.update("\x1f".join("NULL" if value is None else str(value) for value in row).encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()
```

源库与副本对每张业务表比较 digest；`alembic_version` 不进入业务表、行数、主键值或 digest 比较。

- [ ] **Step 5: 运行备份恢复测试**

Run: `python -m pytest tests/test_backup_restore.py -q -p no:cacheprovider`

Expected: 替换主键值、修改非键值、缺行、结构漂移全部被拒；一致副本通过。

- [ ] **Step 6: 提交**

```bash
git add scripts/sqlite_readonly.py scripts/verify_db_restore.py tests/test_backup_restore.py
git commit -m "fix: verify sqlite backup record contents"
```

### Task 4：修复 backup CLI 并启用严格测试清理

**Files:**

- Modify: `scripts/backup_db.py:49`
- Modify: `tests/conftest.py:213`
- Modify: `tests/test_backup_restore.py:18`
- Modify: `tests/test_migration_baseline.py:48`
- Test: `tests/test_backup_restore.py`

- [ ] **Step 1: 使用 closing 关闭所有测试 sqlite3 连接**

所有测试中的：

```python
with sqlite3.connect(path) as conn:
```

改为：

```python
with closing(sqlite3.connect(path)) as conn:
    with conn:
        conn.execute("select 1")
```

第一层负责 close，第二层负责事务 commit/rollback。SQLAlchemy engine 保持 try/finally dispose。

- [ ] **Step 2: 让测试目录清理失败直接失败**

删除 `_remove_tree_fail_closed` 的 try/except warning 降级：

```python
def _remove_tree_fail_closed(directory: pathlib.Path) -> None:
    for child in sorted(directory.rglob("*"), key=lambda path: len(path.parts), reverse=True):
        if child.is_dir() and not child.is_symlink():
            os.rmdir(child)
        else:
            os.remove(child)
    os.rmdir(directory)
    assert not directory.exists(), f"测试临时目录清理失败: {directory}"
```

若仍有 WinError 32，测试应失败并根据路径定位未关闭连接，不能改回 warning。

- [ ] **Step 3: 让 backup API 本身拒绝覆盖**

```python
def backup(src: Path, dst: Path) -> None:
    src = src.resolve(strict=True)
    if not src.is_file():
        raise ValueError(f"源库不是普通文件: {src}")
    if dst.exists():
        raise FileExistsError(f"拒绝覆盖已有备份: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_con = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    try:
        dst_con = sqlite3.connect(dst)
        try:
            src_con.backup(dst_con)
        finally:
            dst_con.close()
    finally:
        src_con.close()
```

`test_backup_refuses_existing_destination` 必须直接调用 `backup(source, destination)`，删除测试内手工 if/raise。

- [ ] **Step 4: 将 source 与 list 设为互斥命令**

```python
parser = argparse.ArgumentParser(description="SQLite 一致性备份(含 WAL)")
mode = parser.add_mutually_exclusive_group(required=True)
mode.add_argument("--source", help="源库(sqlite:///... URL 或文件路径)")
mode.add_argument("--list", metavar="DIRECTORY", help="列出指定目录的备份")
parser.add_argument("--destination-dir", help="备份目录(默认: 源库所在目录)")
args = parser.parse_args()
if args.list:
    if args.destination_dir:
        parser.error("--list 不能与 --destination-dir 同时使用")
    return list_backups(Path(args.list))
return do_backup(args.source, Path(args.destination_dir) if args.destination_dir else None)
```

备份文件名使用微秒或 UUID，避免同一秒两次备份冲突。list_backups 接受显式目录，不再读取模块级 BACKUP_DIR。

- [ ] **Step 5: 增加 CLI 解析测试**

使用 monkeypatch sys.argv 覆盖：`--list <dir>` 返回 0；无模式返回 2；source+list 返回 2；list+destination 返回 2；两个同秒备份生成不同文件。

- [ ] **Step 6: 运行全后端测试并检查零残留**

Run: `python -m pytest tests -q -p no:cacheprovider`

Expected: 退出 0；没有“测试临时目录清理失败”warning；本次新建的 `case_*` 全部删除。历史残留目录只在确认无进程占用后单独清理，不在测试代码中掩盖。

- [ ] **Step 7: 提交**

```bash
git add scripts/backup_db.py tests/conftest.py tests/test_backup_restore.py tests/test_migration_baseline.py
git commit -m "fix: enforce backup and test resource cleanup"
```

### Task 5：打通可执行的副本接管链

**Files:**

- Modify: `docs/database-migration-runbook.md:18`
- Create: `tests/test_database_adoption.py`
- Modify: `migrations/versions/0001_initial_schema.py:4`
- Test: `tests/test_database_adoption.py`

- [ ] **Step 1: 写完整链路测试**

```python
def test_unversioned_database_copy_can_be_adopted(test_artifact_dir):
    source = test_artifact_dir / "source.db"
    backup_copy = test_artifact_dir / "backup.db"
    engine = create_engine(f"sqlite:///{source.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            AppSetting.__table__.insert(),
            {"key": "smtp_host", "value": "example.invalid"},
        )
    engine.dispose()

    backup(source, backup_copy)
    assert verify_restore(source, backup_copy, Base.metadata) == []
    assert compare_schema(f"sqlite:///{backup_copy.as_posix()}", Base.metadata) == []
    command.stamp(_config(backup_copy), "0001")
    assert verify_restore(source, backup_copy, Base.metadata) == []
    assert compare_schema(f"sqlite:///{backup_copy.as_posix()}", Base.metadata) == []
    command.upgrade(_config(backup_copy), "head")

    with closing(sqlite3.connect(backup_copy)) as conn:
        assert conn.execute("select value from app_setting where key='smtp_host'").fetchone() == ("example.invalid",)
        assert conn.execute("select version_num from alembic_version").fetchone() == ("0001",)
```

- [ ] **Step 2: 增加漂移库禁止接管测试**

构造缺列和多余表两种库；`compare_schema` 必须返回差异，测试不得调用 stamp。用 Alembic command mock 断言调用次数为 0，避免只靠测试作者的流程约定。

- [ ] **Step 3: 按真实成功顺序改 runbook**

固定流程：停止写入 → backup → verify_restore → compare_schema → 在副本 stamp 0001 → 再次 compare/verify（忽略 migration metadata）→ upgrade head → 使用副本启动隔离应用 → 记录结果。任何一步非零立即停止。

runbook 的 `--list` 示例改为：

```powershell
python scripts/backup_db.py --list D:/path/to/backups
```

- [ ] **Step 4: 修复迁移格式并执行 diff check**

删除 `migrations/versions/0001_initial_schema.py:4` 尾随空格。

Run: `git diff --check c23b24b..HEAD`

Expected: 无输出、退出 0。

- [ ] **Step 5: 运行接管链测试**

Run: `python -m pytest tests/test_database_adoption.py tests/test_migration_baseline.py tests/test_backup_restore.py -q -p no:cacheprovider`

Expected: 完整链成功；漂移库 fail closed；全部临时目录删除。

- [ ] **Step 6: 提交**

```bash
git add docs/database-migration-runbook.md tests/test_database_adoption.py migrations/versions/0001_initial_schema.py
git commit -m "test: prove safe database copy adoption"
```

### Task 6：阶段验收与下一架构入口

**Files:**

- Modify: `README.md`
- Create: `docs/2026-09-09-phase-5-verification.md`
- Create: `docs/2026-09-09-phase-6-shared-state-design-input.md`

- [ ] **Step 1: 执行固定验收命令**

```powershell
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m pytest tests -q -p no:cacheprovider
Set-Location frontend
pnpm test
pnpm build
Set-Location ..
git diff --check c23b24b..HEAD
```

Expected: 所有命令退出 0；pytest 无测试目录清理 warning；Vitest 无 Unhandled Errors。

- [ ] **Step 2: 记录反例和修复证据**

验收记录逐项列出 R5-01～R5-09 的失败前输出、修复 commit、测试名、最终退出码。把 `.test-artifacts` 剩余 case 数作为明确指标；本轮新测试结束后必须为 0。历史验收错误以追加更正保留，不覆盖旧记录。

- [ ] **Step 3: 输出 Phase 6 设计输入**

`phase-6-shared-state-design-input.md` 只列已验证事实和待设计问题：现有 `TaskRunLog` 字段及保留策略、factors.json/index universe 的并发写入模型、operation/run 状态机、权限主体、SQLite 单写者限制、迁移和回滚门禁。不得在该文件预先宣称选定新表或队列。

- [ ] **Step 4: 更新 README 并提交**

README 的 checker、backup list 和副本接管命令必须与 runbook 一致；明确 `data/web.db` 尚未 stamp，应用仍不自动迁移。

```bash
git add README.md docs/2026-09-09-phase-5-verification.md docs/2026-09-09-phase-6-shared-state-design-input.md
git commit -m "docs: record migration safety closure"
```

## 4. 完成门禁

- [ ] `pnpm test` 退出 0，DataStatus 缺 overview 不抛异常。
- [ ] URL 编码用例含 `/`、空格和 `+`，Factors 复制分别验证空评级和未知评级。
- [ ] checker 对不存在路径不创建文件，对只读路径不保留句柄。
- [ ] migration 创建库与 ORM 的列、类型、nullable、主键、唯一约束和索引完全一致。
- [ ] 恢复验证能发现主键值替换和非主键内容篡改。
- [ ] stamp 前后只允许 `alembic_version` 这一迁移元数据差异，业务数据摘要一致。
- [ ] backup API 和 CLI 都拒绝覆盖；`--list <dir>` 可独立执行。
- [ ] 后端测试无清理 warning，本轮测试产物零残留。
- [ ] 完整接管链只在临时副本执行，漂移数据库不会调用 stamp。
- [ ] `git diff --check` 退出 0，验收记录与实际退出码一致。
- [ ] 未读取、stamp、迁移、覆盖或删除日常 `data/web.db`。

完成这些门禁后，下一轮才能设计共享配置和持久化同步 operation；不直接从当前状态进入业务表迁移。
