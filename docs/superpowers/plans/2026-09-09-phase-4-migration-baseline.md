# 第四轮 Review 与数据库迁移基线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 关闭第三阶段遗留的旧配置回存、刷新停止窗口和测试会话隔离缺陷，并建立可重复验证的 Alembic 初始迁移与已有 SQLite 接管流程。

**Architecture:** 保留 Vue 3 + FastAPI + SQLAlchemy 的模块化单体。先让当前配置和测试合同闭环，再引入只管理结构版本的 Alembic；空库执行初始迁移，已有库必须先通过只读结构核对才允许显式 stamp，应用启动本阶段不自动迁移数据库。

**Tech Stack:** Python 3.11+、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、SQLite、pytest、Vue 3、Vitest、Node test。

---

## 1. Review 范围与裁决

审核范围为 `93e608be0ed331cdb580d7cd7d5b2ae84fc76d95..c23b24be29632f1ef44d822182068c94f2acfe45`，即本地 `main` 新增 5 个实现/验收提交。审查开始时 `main` 比本地记录的 `origin/main` 超前 17 个提交；没有执行 fetch，因此不据此判断服务器状态。已跟踪工作区和暂存区为空。

**裁决：部分达标。** R3-02、R3-03、R3-04 的主要路径已有实现，完整现有测试也通过；但旧配置经 GET 后不能原样保存、刷新控制器仍在立即停止后调用一次 load、请求会话关闭测试没有验证其标题声称的行为。上一阶段 T3 的 Factors 组件测试及 T5 数据库迁移基线也尚未交付。

### 1.1 具体缺陷

#### R4-01 / P1：旧配置迁移结果仍携带禁用字段，页面回存必然 422

`backend/services/cb_factors.py:153`—`:165` 计算新 `ratings` 后没有删除 `excluded_ratings`。`frontend/src/pages/Factors.vue:36` 将 GET 返回的模板直接放入页面状态，`:115` 又把模板整体提交。新模型在 `backend/api/schemas/cb_screen.py:113`—`:118` 拒绝任何 `excluded_ratings`，因此读取旧文件的用户即使只修改模板名称，首次保存也会失败。

本轮使用仓库真实 `_normalize_templates` 和 `FactorsConfigModel` 复现：旧输入被转成新 ratings，但输出仍含 `excluded_ratings`；把该输出再次校验得到 ValidationError。现有 `test_legacy_read_migration_idempotent` 只比较 ratings，没有执行“GET 旧配置 → POST 原响应”的用户链路，见 `tests/test_cb_factors_contract.py:161`—`:174`。

#### R4-02 / P2：start 后立即 stop/dispose 仍执行一次 load

`frontend/src/utils/dataManagementPolling.js:61` 在当前 tick 立即标记 inFlight，`:63`—`:65` 把 load 放入微任务；`stop()` 只增加版本并清理 timer，已经排队的微任务没有在调用 load 前复核版本。执行 `start(); stop(); await setImmediate()` 得到 `calls=1, running=false`。

这偏离上一阶段 `docs/2026-09-09-phase-3-review-and-execution-plan.md:170` 中“实际调用 load 前再次确认会话有效”的明确门禁。当前新增测试覆盖“load 已经开始后不续排”和“dispose 后再 start”，没有覆盖 start 与首个微任务之间的停止窗口，见 `frontend/src/utils/dataManagementPolling.test.mjs:199`—`:246`。

#### R4-03 / P2：请求会话持续到整个 client fixture 结束，关闭测试是空断言

`tests/conftest.py:145`—`:148` 的依赖函数直接返回 Session，不是带 finally 的生成器；会话只在 `contract_client` teardown 的 `:168`—`:172` 批量关闭。多个 HTTP 请求会累计打开的 Session，不能验证请求级资源释放。

`test_request_session_is_closed` 在 `tests/test_test_environment.py:33`—`:46` 只断言 override 存在且可调用，没有取得会话、没有断言 close，也没有在 fixture teardown 后执行自检。测试名称及第三阶段验收记录 `docs/2026-09-09-phase-3-verification.md:40` 因而高估了证据。

#### R4-04 / P2：测试文件仍写入运行时 data 目录，清理失败被静默忽略

`tests/conftest.py:183`、`tests/test_cb_factors_contract.py:50` 在 `DATA_DIR` 下创建临时目录，随后分别在 `tests/conftest.py:187`、`tests/test_cb_factors_contract.py:54` 使用 `ignore_errors=True`。这没有满足上一阶段 T1 的“独立测试目录、清理失败不可静默”清单，也解释了工作区持续出现 `.pytest_cache/`/测试目录权限警告的风险。该项不代表本轮观察到业务数据被覆盖；问题是隔离边界与验收证据不足。

#### R4-05 / P2：生产 lifespan 被空实现绕开，没有测试启动接线

`tests/conftest.py:155`—`:164` 为合同测试统一替换 `_noop_lifespan`；`tests/test_test_environment.py:25`—`:30` 随后断言启动函数零调用。这只能证明空生命周期没有副作用，无法发现 `backend/main.py:24`—`:31` 的真实 lifespan 是否仍按顺序初始化数据库、启动并停止 scheduler。上一阶段计划 `docs/2026-09-09-phase-3-review-and-execution-plan.md:132` 明确要求另建注入测试依赖的生命周期测试，本轮没有交付。

#### R4-06 / P2：T3/T4 的页面验收矩阵仍不完整

`frontend/tests/unit/Bonds.test.js:8`—`:15` mock 掉 API 模块，因此没有覆盖 `frontend/src/api/index.js:37`—`:38` 的 Axios 边界；BB+ 用例 `frontend/tests/unit/Bonds.test.js:118`—`:127` 只验证默认不限，没有实际选择并保存。评级目录失败仍在 `frontend/src/pages/Bonds.vue:64`—`:70` 被静默吞掉，没有上一阶段计划 `docs/2026-09-09-phase-3-review-and-execution-plan.md:159` 要求的提示/重试。`frontend/tests/unit/DataStatus.test.js:12`—`:18` mock 了 addIndex，却没有触发添加入口；`:59`—`:62` 也没有假时钟或 timer 残留断言。`frontend/tests/unit/Factors.test.js` 不存在。现有测试通过不构成这些路径的验收证据。

### 1.2 已验证项

- `C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe -m pytest tests -q -p no:cacheprovider`：171 passed，2140 warnings；warnings 主要是 `datetime.utcnow()` 和 TestClient 兼容层弃用提醒。
- frontend 下 `pnpm test`：Node 32 项、Vitest 18 项通过。
- frontend 下 `pnpm build`：通过，2256 modules transformed。
- `git diff --check 93e608b..c23b24b`：通过。
- `frontend/src/pages/Bonds.vue:51` 已把首次/重置评级改为 `[]`；相关 Bonds 7 项用例通过。
- `backend/api/routes/cb_screen.py:80`—`:84` 已保存 Pydantic 模型输出；缺省/null/重复/旧字段合同测试通过。
- `frontend/src/pages/DataStatus.vue:149`、`:156`、`:371`—`:375` 已阻止迟到 POST 在页面卸载后重启刷新；DataStatus 组件用例通过。

### 1.3 固定核对清单

- [ ] R4-01 的 GET→POST 旧配置链路返回 200，磁盘只保留当前结构。
- [ ] R4-02 的 start→stop 和 start→dispose 都不调用 load。
- [ ] 每个请求完成即关闭测试 Session，第二个请求不复用第一请求的 Session。
- [ ] 测试临时文件不进入 `data/`，清理失败使测试失败。
- [ ] 独立生命周期测试运行真实 lifespan 接线，但注入 DB/scheduler spy，不接触日常资源。
- [ ] Factors 页面关键配置路径和 API 层 query 参数有独立测试。
- [ ] 添加指数入口、目录失败提示、五分钟截止和 timer 清理有组件级证据。
- [ ] Alembic 空库升级、已有库匹配、漂移拒绝和备份恢复均只对临时库执行。

## 2. 文件结构

| 路径 | 责任 |
|---|---|
| `backend/services/cb_factors.py` | 将旧文件转换成当前、可回存的配置，移除迁移专用字段。 |
| `backend/api/schemas/cb_screen.py` | 当前 POST 合同；继续拒绝迁移字段。 |
| `frontend/src/utils/dataManagementPolling.js` | 刷新会话状态机，停止后不再发起尚未开始的 load。 |
| `tests/conftest.py` | 独立测试目录、请求级 DB 会话及外部 IO 隔离。 |
| `frontend/tests/unit/Factors.test.js` | 模板读取、编辑、保存、预览和计数的页面级合同。 |
| `frontend/tests/unit/api.test.js` | Axios 请求方法、URL 和 query/body 序列化。 |
| `alembic.ini`、`migrations/env.py` | Alembic 配置、metadata 绑定和显式数据库 URL。 |
| `migrations/versions/0001_initial_schema.py` | 当前 10 张 ORM 表的初始结构版本。 |
| `scripts/check_db_baseline.py` | 只读比较目标 SQLite 与 ORM 结构，输出确定差异和退出码。 |
| `scripts/backup_db.py` | 接受显式源库/目标目录，执行一致性 SQLite backup。 |
| `scripts/verify_db_restore.py` | 对恢复副本执行 integrity、结构和行数/主键核验。 |
| `tests/test_migration_baseline.py` | 空库升级、重复升级、匹配库接管、漂移拒绝。 |
| `tests/test_backup_restore.py` | WAL 快照与恢复副本验证。 |
| `docs/database-migration-runbook.md` | 日常库接管、失败停止和恢复步骤。 |

## 3. 实施任务

### Task 1：让旧配置迁移结果可直接回存

**Files:**

- Modify: `backend/services/cb_factors.py:131`
- Modify: `tests/test_cb_factors_contract.py:161`
- Test: `tests/test_cb_factors_contract.py`

- [ ] **Step 1: 写失败的完整往返测试**

在 `TestR3Contracts` 增加：

```python
def test_legacy_get_response_can_be_posted_as_current_config(
    self, contract_client, tmp_factors
):
    legacy = {
        "version": 1,
        "active_id": "t1",
        "templates": [_tmpl(ratings=None, excluded_ratings=["AA"])],
    }
    tmp_factors.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = contract_client.get(f"{BASE}/factors")
    assert loaded.status_code == 200
    template = loaded.json()["templates"][0]
    assert template["ratings"] == ["A", "A+", "A-", "AA+", "AA-", "AAA"]
    assert "excluded_ratings" not in template

    saved = contract_client.post(f"{BASE}/factors", json=loaded.json())
    assert saved.status_code == 200
    on_disk = json.loads(tmp_factors.read_text(encoding="utf-8"))
    assert "excluded_ratings" not in on_disk["templates"][0]
```

- [ ] **Step 2: 运行反例并确认失败**

Run:

```powershell
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m pytest tests/test_cb_factors_contract.py::TestR3Contracts::test_legacy_get_response_can_be_posted_as_current_config -q -p no:cacheprovider
```

Expected: FAIL，GET 响应仍包含 `excluded_ratings`，或 POST 返回 422。

- [ ] **Step 3: 在读取迁移中删除旧字段**

在 `_normalize_templates` 的评级分支结束后统一删除迁移字段：

```python
        raw_ratings = tmpl.get("ratings")
        if raw_ratings is None:
            legacy = tmpl.get("excluded_ratings") or []
            tmpl["ratings"] = sorted(
                DEFAULT_RATINGS - {str(x).strip().upper() for x in legacy}
            )
        else:
            tmpl["ratings"] = sorted({
                r
                for r in (str(x).strip().upper() for x in raw_ratings)
                if r
            })
        tmpl.pop("excluded_ratings", None)
```

`StrategyTemplateModel` 继续拒绝新 POST 主动携带旧字段；删除发生在读取旧文件的迁移输出中，不放宽新请求。

- [ ] **Step 4: 运行合同测试**

Run: `python -m pytest tests/test_cb_factors_contract.py -q -p no:cacheprovider`

Expected: 全部 PASS；新增测试证明 GET 响应可原样 POST。

- [ ] **Step 5: 提交**

```bash
git add backend/services/cb_factors.py tests/test_cb_factors_contract.py
git commit -m "fix: make legacy factor migration round-trip safe"
```

### Task 2：关闭刷新首个微任务的停止窗口

**Files:**

- Modify: `frontend/src/utils/dataManagementPolling.js:49`
- Modify: `frontend/src/utils/dataManagementPolling.test.mjs:199`
- Test: `frontend/src/utils/dataManagementPolling.test.mjs`

- [ ] **Step 1: 写两个失败反例**

```javascript
test('start 后同一轮 stop 不调用尚未开始的 load', async () => {
  let calls = 0;
  const refresh = createAutoRefresh({ load: async () => { calls += 1; return true; } });
  refresh.start();
  refresh.stop();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls, 0);
});

test('start 后同一轮 dispose 不调用尚未开始的 load', async () => {
  let calls = 0;
  const refresh = createAutoRefresh({ load: async () => { calls += 1; return true; } });
  refresh.start();
  refresh.dispose();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls, 0);
  assert.equal(refresh.isRunning(), false);
});
```

- [ ] **Step 2: 运行反例并确认失败**

Run: `cd frontend && node --test src/utils/dataManagementPolling.test.mjs`

Expected: 两个新增用例的 `calls` 实际为 1。

- [ ] **Step 3: 在真正调用 load 前验证会话**

把 tick 的 Promise 链改成带 skipped 状态的会话检查；不要让旧会话的 finally 清除新会话的 inFlight：

```javascript
    const flight = { version: versionAtStart };
    inFlight = flight;
    Promise.resolve()
      .then(() => {
        if (disposed || versionAtStart !== version || inFlight !== flight) {
          return { skipped: true, ok: false };
        }
        return Promise.resolve(load())
          .then((r) => ({ skipped: false, ok: r !== false }))
          .catch(() => ({ skipped: false, ok: false }));
      })
      .then(({ skipped, ok }) => {
        if (inFlight === flight) inFlight = null;
        if (skipped || disposed || versionAtStart !== version) return;
        if (!ok) {
          stop();
          if (typeof onStop === 'function') onStop('load_failed');
          return;
        }
        if (now() >= deadline) {
          stop();
          if (typeof onStop === 'function') onStop('deadline');
          return;
        }
        timerId = schedule(() => tick(versionAtStart, deadline), intervalMs);
      });
```

同时将声明改为 `let inFlight = null`，判断改为 `if (inFlight !== null)`。保留现有 start 覆盖、失败停止、deadline 和 dispose 用例。

- [ ] **Step 4: 运行控制器和页面测试**

Run: `cd frontend && pnpm test`

Expected: Node 34 项、Vitest 18 项全部 PASS；若实际总数因同时新增测试变化，以零失败和测试清单为准。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/utils/dataManagementPolling.js frontend/src/utils/dataManagementPolling.test.mjs
git commit -m "fix: cancel refresh before first load starts"
```

### Task 3：实现请求级测试 Session 与独立临时目录

**Files:**

- Modify: `.gitignore`
- Modify: `tests/conftest.py:23`
- Modify: `tests/test_test_environment.py:33`
- Modify: `tests/test_cb_factors_contract.py:37`
- Test: `tests/test_test_environment.py`

- [ ] **Step 1: 为测试资源定义状态对象和独立目录 fixture**

在 `tests/conftest.py` 增加：

```python
from dataclasses import dataclass, field
from pathlib import Path
from sqlalchemy.orm import Session

TEST_ARTIFACT_ROOT = Path(__file__).resolve().parents[1] / ".test-artifacts"


@dataclass
class ContractDbState:
    opened: list[Session] = field(default_factory=list)
    closed_ids: list[int] = field(default_factory=list)


@pytest.fixture()
def test_artifact_dir():
    TEST_ARTIFACT_ROOT.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="case_", dir=TEST_ARTIFACT_ROOT))
    try:
        yield directory
    finally:
        shutil.rmtree(directory)
        assert not directory.exists()
```

在 `.gitignore` 增加 `.test-artifacts/`。测试不得再在 `DATA_DIR` 创建 `.test_*`；已有运行遗留不在本任务中批量删除。

- [ ] **Step 2: 把 dependency override 改为请求级生成器**

让 `contract_client` 同时返回可观测状态 fixture：

```python
@pytest.fixture()
def contract_db_state():
    return ContractDbState()


@pytest.fixture()
def contract_client(thread_safe_engine, startup_spies, contract_db_state):
    # 保留现有 app/lifespan 隔离初始化
    TestSession = sessionmaker(bind=thread_safe_engine)

    def _get_test_db():
        session = TestSession()
        contract_db_state.opened.append(session)
        try:
            yield session
        finally:
            session.close()
            contract_db_state.closed_ids.append(id(session))

    app.dependency_overrides[get_db] = _get_test_db
    # 保留现有 TestClient try/finally，仅删除 teardown 批量 close 的 opened 循环
```

- [ ] **Step 3: 用真实断言替换空的关闭测试**

```python
def test_request_session_is_closed(contract_client, contract_db_state):
    first = contract_client.get("/api/cb-list/factors/ratings")
    assert first.status_code == 200
    assert len(contract_db_state.opened) == 1
    assert contract_db_state.closed_ids == [id(contract_db_state.opened[0])]

    second = contract_client.get("/api/cb-list/factors/ratings")
    assert second.status_code == 200
    assert len(contract_db_state.opened) == 2
    assert contract_db_state.opened[0] is not contract_db_state.opened[1]
    assert contract_db_state.closed_ids == [
        id(contract_db_state.opened[0]),
        id(contract_db_state.opened[1]),
    ]
```

- [ ] **Step 4: 将文件 fixture 移到独立目录**

`tmp_universe` 和 `tmp_factors` 接收 `test_artifact_dir`，直接使用其子文件：

```python
@pytest.fixture()
def tmp_factors(monkeypatch, test_artifact_dir):
    from backend.services import cb_factors

    path = test_artifact_dir / "factors.json"
    monkeypatch.setattr(cb_factors, "FACTORS_PATH", path)
    yield path
```

`tmp_universe` 同样使用 `test_artifact_dir / "index_universe.json"`。删除 `ignore_errors=True` 清理和不再使用的 DATA_DIR/pathlib 导入。

- [ ] **Step 5: 独立验证生产 lifespan 的接线顺序**

在 `tests/test_test_environment.py` 增加；该测试运行真实 lifespan 函数，但将三个外部动作替换为内存 spy：

```python
def test_production_lifespan_wires_database_and_scheduler(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main as main_mod

    events = []
    monkeypatch.setattr(main_mod, "init_db", lambda: events.append("init_db"))
    monkeypatch.setattr(main_mod, "start_scheduler", lambda: events.append("start"))
    monkeypatch.setattr(main_mod, "stop_scheduler", lambda: events.append("stop"))
    monkeypatch.setattr(main_mod.settings, "scheduler_enabled", True)

    isolated_app = main_mod.create_app()
    with TestClient(isolated_app) as client:
        assert client.get("/api/health").status_code == 200
        assert events == ["init_db", "start"]
    assert events == ["init_db", "start", "stop"]
```

这项测试不能使用 `contract_client`，否则又会绕过被测 lifespan；三个 spy 必须在 `create_app()` 和 TestClient 之前完成。

- [ ] **Step 6: 运行隔离测试和后端全量测试**

Run:

```powershell
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m pytest tests/test_test_environment.py tests/test_cb_factors_contract.py tests/test_cb_screen_contract.py -q -p no:cacheprovider
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m pytest tests -q -p no:cacheprovider
```

Expected: 全部 PASS；命令结束后 `.test-artifacts` 内没有 case 目录，`data/` 没有新增测试目录。

- [ ] **Step 7: 提交**

```bash
git add .gitignore tests/conftest.py tests/test_test_environment.py tests/test_cb_factors_contract.py
git commit -m "test: close request sessions and isolate artifacts"
```

### Task 4：补齐 Factors 页面和 API 序列化回归

**Files:**

- Modify: `frontend/src/pages/Bonds.vue`
- Modify: `frontend/tests/unit/Bonds.test.js`
- Modify: `frontend/tests/unit/DataStatus.test.js`
- Create: `frontend/tests/unit/Factors.test.js`
- Create: `frontend/tests/unit/api.test.js`
- Modify: `frontend/tests/setup.js`
- Test: `frontend/tests/unit/Factors.test.js`
- Test: `frontend/tests/unit/api.test.js`

- [ ] **Step 1: 建立 Factors 页面测试数据与 API mock**

测试 fixture 固定包含一个当前模板和一个从旧文件迁移后的模板。旧模板对象只能包含当前字段，保证 R4-01 的服务端清洗结果被前端接受：

```javascript
const template = {
  id: 't1', name: '三低', target_count: 10, hold_tolerance: 0,
  exclusion_rules: [], strategy_factors: [], excluded_redeem_icons: [],
  redeem_safe_days: 2, excluded_bond_codes: [], ratings: ['AA+'],
  min_listing_days: 0,
};

getFactorCatalog.mockResolvedValue([]);
getRatingCatalog.mockResolvedValue([{ value: 'AA+', label: 'AA+' }]);
getFactors.mockResolvedValue({ active_id: 't1', templates: [template] });
screenBonds.mockResolvedValue({
  total_all: 3, total_filtered: 2, top_n: 1, keep_n: 1,
  rows: [{ bond_id: '110001', score: null }], excluded_rows: [],
});
saveFactors.mockImplementation(async (payload) => ({ ok: true, data: payload }));
```

- [ ] **Step 2: 覆盖五条 Factors 页面行为**

新增五个独立用例，名称固定为“读取当前模板后保存不发送 excluded_ratings”“复制模板保留空评级和未知评级”“新建模板复制当前模板而非固定七档”“预览返回 null 数值时页面不抛异常”“实际入选与缓冲计数使用后端 top_n 和 keep_n”。每个用例挂载真实 Factors 页面，通过按钮文本触发保存、新建、复制或预览；保存类用例检查 `saveFactors.mock.calls[0][0]`，预览类用例检查 `screenBonds` 参数和渲染文本。不得直接调用从页面复制出来的辅助实现。

- [ ] **Step 3: 用 Axios mock adapter 检查 API 边界**

在 `api.test.js` 导入默认 api 实例并 mock adapter，覆盖：

```javascript
it('screenBondsIntraday 使用 GET 和 ratings 逗号参数', async () => {
  api.defaults.adapter = async (config) => {
    expect(config.method).toBe('get');
    expect(config.url).toBe('/cb-list/screen/intraday');
    expect(config.params).toEqual({ ratings: 'AA+,NONE', price_max: 120 });
    return { data: { rows: [] }, status: 200, statusText: 'OK', headers: {}, config };
  };
  await screenBondsIntraday({ ratings: 'AA+,NONE', price_max: 120 });
});
```

另加 `saveFactors` 使用 POST body、`syncIndex` 对代码 encodeURIComponent 的测试。每个 case 在 afterEach 恢复原 adapter，避免影响别的组件测试。

- [ ] **Step 4: 补 Bonds 失败状态和 DataStatus 第二入口**

目录加载失败时在 Bonds 页面设置可见错误文字并提供“重试评级目录”按钮；重试只调用 `getRatingCatalog`，不改变 `filters.ratings`。Bonds 测试先让目录 reject，再点击重试并 resolve 含 BB+ 的目录，随后通过 el-select 功能桩选择 BB+，查询断言参数为 `ratings: 'BB+'`。

DataStatus 测试使用 `vi.useFakeTimers()` 覆盖添加指数流程：探测成功后提交，断言 `addIndex` 恰好一次；成功后只调用 `getDataManagement`。再覆盖 load 失败停止、推进假时钟到五分钟后不增加 GET、unmount 后 `vi.getTimerCount() === 0`。测试结束先清 timer，再恢复 real timers。

- [ ] **Step 5: 运行组件及全前端测试**

Run: `cd frontend && pnpm test`

Expected: 新增 Factors/API 用例与既有 Bonds、DataStatus、Settings、工具测试全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/pages/Bonds.vue frontend/tests/unit/Bonds.test.js frontend/tests/unit/DataStatus.test.js frontend/tests/unit/Factors.test.js frontend/tests/unit/api.test.js frontend/tests/setup.js
git commit -m "test: cover factor workflows and api serialization"
```

### Task 5：建立 Alembic 初始结构版本

**Files:**

- Modify: `requirements.txt`
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/0001_initial_schema.py`
- Create: `tests/test_migration_baseline.py`

- [ ] **Step 1: 加入固定主版本依赖并初始化目录**

在 ORM 依赖段增加：

```text
alembic>=1.14.0,<2.0.0
```

Run: `python -m alembic init migrations`

Expected: 生成 `alembic.ini`、`migrations/env.py`、模板和 versions 目录。

- [ ] **Step 2: 配置显式数据库 URL 和完整 metadata**

`migrations/env.py` 的核心配置必须如下；CLI 用 `-x database_url=...`，程序化测试用 Config 的 `sqlalchemy.url`，两者都没有显式值时直接失败：

```python
from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.models.database import Base
from backend.models import app_setting, data_status, valuation  # noqa: F401

config = context.config
target_metadata = Base.metadata
x_args = context.get_x_argument(as_dictionary=True)
database_url = x_args.get("database_url") or config.get_main_option("sqlalchemy.url")
if not database_url or "__explicit_url_required__" in database_url:
    raise RuntimeError("必须显式提供 Alembic database_url")
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`alembic.ini` 不保存日常库路径，保留无效占位 `sqlalchemy.url = sqlite:///__explicit_url_required__.db`；日常接管命令必须传 `-x database_url=`。

- [ ] **Step 3: 生成并审查初始 revision**

对全新临时 SQLite 执行：

```powershell
$phase4Db = (Resolve-Path .).Path + '/.test-artifacts/alembic-generate.db'
python -m alembic -x "database_url=sqlite:///$($phase4Db.Replace('\','/'))" revision --autogenerate -m "initial schema" --rev-id 0001
```

Expected: `migrations/versions/0001_initial_schema.py` 的 upgrade 明确创建以下 10 张业务表和 `alembic_version`：

```python
EXPECTED_TABLES = {
    "app_setting", "task_run_log", "index_valuation_snapshot",
    "index_dividend_yield", "cn_bond_yield", "index_daily_quote",
    "cb_index_daily", "cb_daily_snapshot", "cb_redeem_daily", "cb_blacklist",
}
```

逐项核对 ORM 中全部列、主键、nullable、唯一约束和显式索引；revision 中不能导入运行时 engine、调用 `Base.metadata.create_all()` 或读写业务数据。`down_revision = None`；downgrade 只用于临时库测试，并按外键依赖逆序删除表。

- [ ] **Step 4: 写空库升级与重复升级测试**

```python
def _upgrade(db_path: Path) -> None:
    command.upgrade(_config(db_path), "head")


def test_empty_database_upgrades_to_current_schema(tmp_path):
    db_path = tmp_path / "empty.db"
    _upgrade(db_path)
    inspector = inspect(create_engine(f"sqlite:///{db_path.as_posix()}"))
    assert set(inspector.get_table_names()) == EXPECTED_TABLES | {"alembic_version"}


def test_upgrade_head_is_idempotent(tmp_path):
    db_path = tmp_path / "repeat.db"
    _upgrade(db_path)
    _upgrade(db_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select version_num from alembic_version").fetchone() == ("0001",)
```

`_config` 用 Alembic Config 设置 `script_location=migrations` 和显式 SQLite URL，不读取 `data/web.db`。

- [ ] **Step 5: 运行迁移测试和模型结构核对**

Run: `python -m pytest tests/test_migration_baseline.py -q -p no:cacheprovider`

Expected: 空库升级与重复升级 PASS；检查器返回完整 10 张业务表。

- [ ] **Step 6: 提交**

```bash
git add requirements.txt alembic.ini migrations tests/test_migration_baseline.py
git commit -m "feat: add initial database migration baseline"
```

### Task 6：实现已有库只读比对和安全 stamp

**Files:**

- Create: `scripts/check_db_baseline.py`
- Modify: `tests/test_migration_baseline.py`
- Create: `docs/database-migration-runbook.md`

- [ ] **Step 1: 实现结构快照比较**

`check_db_baseline.py` 提供 `compare_schema(url, metadata) -> list[str]`，比较：业务表集合、列名、SQLite affinity、nullable、主键列集合、唯一约束列集合、显式索引名及列顺序。稳定排序差异，空列表表示匹配。

CLI 必须显式接收 URL，并保持只读：

```python
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    if not args.database_url.startswith("sqlite:///"):
        parser.error("本阶段只允许显式 SQLite URL")
    differences = compare_schema(args.database_url, Base.metadata)
    for difference in differences:
        print(difference)
    return 1 if differences else 0
```

脚本不执行 DDL、不自动 stamp、不调用 `init_db()`。

- [ ] **Step 2: 写匹配和漂移拒绝测试**

```python
def test_current_unversioned_database_matches_baseline(tmp_path):
    db_path = tmp_path / "current.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    assert compare_schema(str(engine.url), Base.metadata) == []


def test_schema_drift_is_reported_without_modifying_database(tmp_path):
    db_path = tmp_path / "drift.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("create table unexpected_table (id integer primary key)")
    before = db_path.read_bytes()
    differences = compare_schema(f"sqlite:///{db_path.as_posix()}", Base.metadata)
    after = db_path.read_bytes()
    assert any("unexpected_table" in item for item in differences)
    assert after == before
```

再覆盖缺列、错误 nullable、缺唯一约束和索引列顺序变化。

- [ ] **Step 3: 文档化显式接管流程**

`docs/database-migration-runbook.md` 固定顺序：停止写入/调度 → 使用 Task 7 备份到新文件 → 对备份副本运行只读检查 → 在副本 stamp `0001` → 副本执行 `upgrade head` → 启动隔离应用冒烟。结构有任意差异立即停止并提交差异报告；禁止对不匹配库执行 stamp。

匹配副本的命令：

```powershell
python scripts/check_db_baseline.py --database-url sqlite:///D:/absolute/path/to/restore-copy.db
python -m alembic -x database_url=sqlite:///D:/absolute/path/to/restore-copy.db stamp 0001
python -m alembic -x database_url=sqlite:///D:/absolute/path/to/restore-copy.db current
```

本阶段不对 `data/web.db` 执行这些命令，也不把 stamp 放进应用启动。

- [ ] **Step 4: 运行检查器测试**

Run: `python -m pytest tests/test_migration_baseline.py -q -p no:cacheprovider`

Expected: 匹配库差异为空；每种漂移退出非零且数据库内容未变化。

- [ ] **Step 5: 提交**

```bash
git add scripts/check_db_baseline.py tests/test_migration_baseline.py docs/database-migration-runbook.md
git commit -m "feat: verify databases before migration stamping"
```

### Task 7：验证 SQLite 备份与恢复副本

**Files:**

- Modify: `scripts/backup_db.py`
- Create: `scripts/verify_db_restore.py`
- Create: `tests/test_backup_restore.py`
- Modify: `docs/database-migration-runbook.md`

- [ ] **Step 1: 先写 WAL 备份反例测试**

```python
def test_backup_includes_committed_wal_rows(tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "backup.db"
    with sqlite3.connect(source) as conn:
        conn.execute("pragma journal_mode=WAL")
        conn.execute("create table sample (id integer primary key, value text not null)")
        conn.execute("insert into sample(value) values ('committed-in-wal')")
        conn.commit()
        backup(source, destination)
    with sqlite3.connect(destination) as conn:
        assert conn.execute("pragma integrity_check").fetchone() == ("ok",)
        assert conn.execute("select value from sample").fetchone() == ("committed-in-wal",)
```

- [ ] **Step 2: 让备份 API 接受显式路径**

保留现有 `backup(src, dst)`，将 CLI 改为必需的 `--source` 和可选 `--destination-dir`；默认目标目录只能由显式 source 的父目录派生，不再静默固定 `data/web.db`。目标文件已存在时拒绝覆盖：

```python
if destination.exists():
    raise FileExistsError(f"拒绝覆盖已有备份: {destination}")
destination.parent.mkdir(parents=True, exist_ok=True)
backup(source, destination)
```

- [ ] **Step 3: 实现恢复副本验证**

`verify_db_restore.py` 接收 `--source`、`--backup`，执行两边 `PRAGMA integrity_check`、业务表集合、每表行数和主键集合比对，再调用 `compare_schema` 检查备份副本结构。任何差异打印 `表名 + 差异类型` 并返回 1；不修改任一文件。

- [ ] **Step 4: 覆盖失败路径**

增加四个独立用例：`test_backup_refuses_existing_destination` 预先创建目标文件并断言 FileExistsError；`test_restore_verifier_detects_missing_rows` 删除副本的一行并断言退出码 1；`test_restore_verifier_detects_schema_drift` 在副本增加一列并断言退出码 1；`test_restore_verifier_accepts_identical_snapshot` 对 SQLite backup API 生成的未修改副本断言退出码 0。

测试文件全部位于 pytest tmp_path；不读取或复制日常库。

- [ ] **Step 5: 运行备份与迁移测试**

Run: `python -m pytest tests/test_backup_restore.py tests/test_migration_baseline.py -q -p no:cacheprovider`

Expected: 全部 PASS；已有目标拒绝覆盖、篡改副本返回失败、一致副本通过。

- [ ] **Step 6: 提交**

```bash
git add scripts/backup_db.py scripts/verify_db_restore.py tests/test_backup_restore.py docs/database-migration-runbook.md
git commit -m "feat: verify sqlite backup restoration"
```

### Task 8：阶段验收与交接

**Files:**

- Modify: `README.md`
- Create: `docs/2026-09-09-phase-4-verification.md`
- Modify: `docs/database-migration-runbook.md`

- [ ] **Step 1: 执行全量自动化检查**

```powershell
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m pytest tests -q -p no:cacheprovider
Set-Location frontend
pnpm test
pnpm build
Set-Location ..
git diff --check 93e608b..HEAD
```

Expected: 四条命令退出码 0。验收记录填写实际用例数、warning 数、构建模块数和执行 SHA；数字必须来自本次输出。

- [ ] **Step 2: 在临时目录执行迁移演练**

演练包含：空库 upgrade head 两次、ORM 当前结构库只读检查、匹配库 stamp、缺列库拒绝、WAL 备份、篡改备份拒绝。保存命令和退出码；不使用 `data/web.db`。

- [ ] **Step 3: 更新 README**

增加三类命令：开发启动仍暂用 `init_db/create_all`；新空库用 Alembic upgrade；已有库只能按 runbook 检查、备份和显式 stamp。明确应用启动不会自动迁移。

- [ ] **Step 4: 写验收记录**

按 R4-01～R4-04、Factors/API 测试、迁移、备份恢复逐项列：实现文件行号、测试名、实际结果、未覆盖边界。未执行日常库接管应明确写“未执行”，不能写成等价覆盖。

- [ ] **Step 5: 提交**

```bash
git add README.md docs/2026-09-09-phase-4-verification.md docs/database-migration-runbook.md
git commit -m "docs: record phase 4 migration baseline verification"
```

## 4. 阶段门禁

只有以下清单全部完成，下一轮才能开始“共享配置进库与持久化同步操作”：

- [ ] R4-01～R4-04 的原始反例先失败、修复后通过。
- [ ] 旧配置 GET→POST 不丢评级、不携带迁移字段。
- [ ] 刷新 stop/dispose 后没有首个 GET、续排 timer 或 onStop 误报。
- [ ] 每个 HTTP 请求结束即关闭测试 Session，测试文件完全离开 `data/`。
- [ ] Factors 页面和 Axios 边界测试通过，既有 Bonds/DataStatus/Settings 不回归。
- [ ] 初始 revision 明确创建当前 10 张表，空库和重复升级通过。
- [ ] 已有库漂移检查只读且 fail closed；未通过检查不能 stamp。
- [ ] WAL 备份在恢复副本上通过 integrity、结构、行数和主键验证。
- [ ] `backend/models/database.py:47` 的 `create_all` 过渡状态及退出计划写入 runbook。
- [ ] 没有对日常 `data/web.db` 做迁移、stamp、删除或覆盖。

## 5. 下一轮预告

迁移基线门禁通过后，再单独设计共享配置表与同步 operation/run 模型。届时先核对现有 `TaskRunLog` 的复用边界，再决定新增表；不在本阶段提前把 `factors.json`、指数名单或运行中任务状态搬入数据库。
