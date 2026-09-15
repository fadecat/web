# 集思录账号池 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可由前端管理、对业务调用方透明、带调用统计和登录保护的集思录账号池。

**Architecture:** 账号配置与运行状态落在 SQLAlchemy 表中，`JisiluAccountPool` 管理会话、选择、冷却和预算，`JisiluGateway` 统一发起 HTTP 请求并分类失败。现有抓取器逐步改用网关，设置页通过脱敏 CRUD API 管理账号。

**Tech Stack:** FastAPI、SQLAlchemy、SQLite、httpx、Vue 3、Element Plus、pytest、Vitest。

---

### Task 1: 账号模型、仓储与调度核心

**Files:**
- Create: `backend/models/jisilu_account.py`
- Create: `backend/services/jisilu_account_pool.py`
- Modify: `backend/models/database.py`
- Test: `tests/test_jisilu_account_pool.py`

- [x] 先写失败测试，覆盖脱敏序列化、最少调用量选择、禁用/冷却跳过、按自然日重置计数、每日登录预算、Cookie 复用和密码留空不覆盖。
- [x] 运行 `pytest -q tests/test_jisilu_account_pool.py`，确认因模型和服务缺失失败。
- [x] 实现模型、仓储、线程锁保护的账号选择和状态转换；所有时间统一使用 Asia/Shanghai 业务日和 UTC/无时区数据库时间的一致转换。
- [x] 再运行测试，确认全部通过。

### Task 2: 统一网关与兼容认证

**Files:**
- Create: `backend/services/jisilu_gateway.py`
- Modify: `backend/services/jisilu.py`
- Modify: `backend/services/fetchers/cb_list.py`
- Modify: `backend/services/fetchers/cb_redeem.py`
- Modify: `backend/services/fetchers/cb_index.py`
- Modify: `backend/services/fetchers/stock_dividend.py`
- Modify: `backend/services/cb_discussion.py`
- Test: `tests/test_jisilu_gateway.py`
- Test: `tests/test_jisilu.py`

- [x] 先写失败测试，覆盖有效 Cookie 不登录、未登录只重登一次、登录预算耗尽、429/刷新过快冷却、网络异常不禁用账号、最多切换一个账号和调用计数。
- [x] 运行定向测试确认失败原因是网关行为缺失。
- [x] 实现 `request()` 与批次租约；保留池为空时的环境变量单账号兼容路径。
- [x] 将抓取器的 Cookie 拼接和 HTTP 调用迁移到网关，不改变解析输出契约。
- [x] 运行相关抓取器、认证和选债契约测试。

### Task 3: 管理 API

**Files:**
- Create: `backend/api/routes/jisilu_accounts.py`
- Modify: `backend/main.py`
- Test: `tests/test_jisilu_accounts_api.py`

- [x] 先写失败契约测试，覆盖列表脱敏、新增、修改、密码留空、删除、启停、人工检测和状态重置。
- [x] 运行测试确认路由不存在而失败。
- [x] 实现 Pydantic 请求模型、错误码和脱敏响应；删除和检测操作返回确定状态。
- [x] 运行 API 契约测试和全量后端测试。

### Task 4: 前端账号池管理

**Files:**
- Modify: `frontend/src/api/index.js`
- Modify: `frontend/src/pages/Settings.vue`
- Modify: `frontend/tests/unit/Settings.test.js`

- [x] 先写失败组件测试，覆盖汇总、脱敏列表、新增/编辑、删除确认、启停、检测、重置、加载失败和操作中禁用。
- [x] 运行 `pnpm test:unit --run tests/unit/Settings.test.js`，确认账号池 UI 缺失而失败。
- [x] 实现账号池卡片、编辑弹窗和状态展示；密码永不回填，留空表示保持原值。
- [x] 运行组件测试和 `pnpm build`。

### Task 5: 集成验收

**Files:**
- Modify: `docs/superpowers/plans/2026-09-15-jisilu-account-pool.md`

- [x] 运行 `pytest -q`。
- [x] 运行前端全量 Vitest 和 `pnpm build`。
- [x] 检查 API 响应、异常日志和前端状态中不存在密码或 Cookie。
- [x] 检查池为空时旧环境变量账号仍可工作，池存在时调用方无需传账号。
- [x] 记录实际通过的命令和剩余限制。

## 验收记录（2026-09-15）

- 后端：`python -m pytest -q --basetemp .test-artifacts/pytest-backend-final-20260915-a`，644 passed。
- 前端：`NODE_OPTIONS=--max-old-space-size=4096 pnpm exec vitest run --pool=forks --maxWorkers=1`，12 个测试文件、117 个测试通过。
- 构建：`NODE_OPTIONS=--max-old-space-size=4096 pnpm build`，Vite 生产构建通过。
- 网关、管理 API、前端分别经过独立红队复审，裁决均为达标。

## 上线限制

- 第一版按单台 ECS、单应用进程内多线程设计。若 Uvicorn/Gunicorn 启用多个 worker，账号选择、最小请求间隔和登录 single-flight 需要迁移到数据库行锁或 Redis 分布式锁后再扩容。
- 账号密码和 Cookie 当前保存在后端数据库明文字段中，API 和日志不会回传明文。正式环境应限制数据库文件与备份权限；引入多用户或异地备份前，应增加由 ECS 环境变量或密钥服务提供主密钥的字段加密。
- 账号池数据库查询失败采用 fail-closed，不会偷偷回退到旧环境变量账号。只有数据库可用且账号表为空时才启用旧单账号兼容路径。
