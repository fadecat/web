# 第三轮 Review 与下一阶段实施计划

日期：2026-09-09。服务场景：内部团队日常使用，优先稳定性和操作效率。

本文件交付给后续开发人员执行。本轮仅审查提交并制定计划，没有修改业务代码、提交 Git 或执行数据库迁移。

## 1. 审核边界与裁决

**裁决：部分达标。** 上一阶段要求接口合同一致、同步生命周期可控、测试不接触真实数据。当前仍存在新请求被旧迁移逻辑改写、快捷筛选默认范围偏窄、卸载后重启刷新、测试启动真实应用等偏离；不能将第二阶段整体标记为完成。

审查基线：`02c2cce..93e608b`，本地 `main` 上新增 6 个提交，23 个文件，新增 2283 行、删除 96 行。起始已跟踪工作区及暂存区无改动；Git 对 `.pytest_cache/` 报权限警告。没有 fetch，`main` 超前本地 `origin/main` 12 个提交不代表已核对服务器状态。

| 提交 | 审核对象 |
|---|---|
| `8dcc0af` | 测试入口、依赖、README |
| `5a3c315` | 服务端筛选模型和接口测试 |
| `1115ef8` | 评级目录、策略校验及页面接入 |
| `b73efe0` | 同步刷新控制器与页面调用 |
| `0d1bec9` | 设置保存与重读结果 |
| `93e608b` | 第二阶段验收记录 |

裁决目标来自 `docs/2026-09-09-phase-2-contract-quality-plan.md:59`、`:214`、`:222`。以下结论基于 diff 和调用链；提交说明及作者验收记录不作为独立通过证据。旧代码仅用于解释本轮改动的实际结果。

## 2. 本轮具体缺陷

### R3-01 / P1：接口测试的数据库覆盖没有隔离应用启动

**证据：** `tests/test_cb_screen_contract.py:42`、`tests/test_cb_factors_contract.py:43`、`backend/main.py:24`、`backend/models/database.py:47`。

两个新增 fixture 只覆盖 `get_db`，随后进入带 lifespan 的 `TestClient(app)`。lifespan 仍调用使用模块级 engine 的 `init_db()`，并依据日常配置决定启动 scheduler。因此，在配置了日常数据库的环境执行测试，内存请求库之外仍可能创建真实库表；开启调度时还会启动调度器。是否实际写入、执行任务取决于环境，本轮没有触发这些副作用。

这直接偏离计划第 65 行“不访问生产数据库”和验收记录第 26 行的断言。必须把启动依赖一并隔离，不能靠操作者记得关闭调度。

- [ ] 路由测试使用独立 app/隔离 lifespan，或在进入 TestClient 前替换启动依赖。
- [ ] 请求会话以 yield/finally 关闭；退出 fixture 恢复 override 并 dispose 测试 engine。
- [ ] 添加启动哨兵测试：真实 `init_db`、scheduler 启动入口被调用即失败。
- [ ] 在导入业务配置之前配置独立测试环境；文件写入限制到测试临时目录，外部 HTTP/SMTP 意外调用即失败。

### R3-02 / P1：校验模型被丢弃，新策略仍进入旧评级迁移

**证据：** `backend/api/routes/cb_screen.py:80`、`:83`；`backend/api/schemas/cb_screen.py:85`、`:90`；`backend/services/cb_factors.py:153`、`:182`。

`model_validate(body)` 的返回值没有用于保存。模型把缺省评级视作 `[]`，也把 null 转为 `[]`，但 `write_config(body)` 接收原始请求；原有规范化函数把缺省/null 识别成旧配置，补成七档。新 POST 还能携带 `excluded_ratings` 进入旧反转逻辑，与“兼容仅发生在读取旧文件”相冲突。

本轮用标准库 AST 提取仓库原函数进行无文件写入检查：缺省和 null 都得到 `[A,A+,A-,AA,AA+,AA-,AAA]`；显式 `[]` 得到 `[]`。这是规范化函数反例，不能冒称完整 HTTP 测试。

- [ ] 保存模型输出，使用 `model_dump()` 保留默认字段；不能使用会丢掉默认评级的 `exclude_unset=True`。
- [ ] 明确合同：新 POST 缺省评级为 `[]`；null 按“只接受数组”的合同返回 422；显式 `[]` 保持不限。
- [ ] 新 POST 携带旧 `excluded_ratings` 返回 422；旧文件读取迁移继续保留原选择范围。
- [ ] 保存成功后的返回值、文件内容、再次 GET 的评级完全一致。

### R3-03 / P2：快捷筛选默认仍枚举固定评级，未知评级被静默排除

**证据：** `frontend/src/pages/Bonds.vue:33`、`:49`、`:54`；目标为上一阶段计划 `:214`；错误关闭声明位于 `docs/2026-09-09-phase-2-verification.md:37`。

`defaultFilters()` 仍复制 14 项兜底目录作为选中值。目录请求只更新选项，不改变默认筛选条件。无本地保存条件时，若后端目录发现 `BB+`，页面虽可选择它，但默认查询及重置仍排除它；固定“全选”并不等于不限评级。

- [ ] 首次进入及重置使用 `ratings=[]`，明确展示“不限评级”。
- [ ] 保留已有 localStorage 中用户明确保存的评级集合，不通过升级版本号整体抹除偏好。
- [ ] 目录加载失败也保持“不限”语义；兜底目录只提供可选项，不充当筛选默认值。
- [ ] 对目录新增 `BB+`、旧选择恢复、重置、目录失败分别做页面测试。

### R3-04 / P2：同步 POST 在卸载后完成，会重启自动刷新

**证据：** `frontend/src/pages/DataStatus.vue:151`、`:152`、`:345`、`:361`；`frontend/src/utils/dataManagementPolling.js:88`。

`syncOne` 等待 POST 后无条件调用 `startSyncPoll()`，卸载钩子只停止当时存在的刷新会话。用户点击同步后马上离开页面，POST 随后成功，会在卸载后的页面重新启动 GET 和定时器。添加指数也有跨 await 后启动的相同入口。

本轮提取原 `syncOne` 函数体并使用真实刷新控制器、可控 Promise 和假调度器复现：先触发 POST、执行 stop、再 resolve POST，结果为 `loads=1, scheduled=1, running=true`。这是函数级生命周期反例，尚未执行实际挂载/卸载组件测试。

- [ ] 页面建立永久 disposed 标记，卸载后所有 await 续体均不得启动刷新或新请求。
- [ ] 区分暂时 stop 和永久 dispose；必要时用 AbortController 取消只读请求，但不能将取消浏览器请求解释为取消服务器任务。
- [ ] 控制器在实际调用 load 前再次确认会话有效，覆盖 start 后同一轮立即 stop 的微任务窗口。
- [ ] 使用真实 DataStatus 组件，延迟同步/添加响应、先卸载再 resolve，断言无新增 GET、无活动定时器。

### R3-05 / P2：重复评级被接受，与约定及验收记录相反

**证据：** `backend/api/schemas/cb_screen.py:105`、`:106`；上一阶段计划 `:222`；`docs/2026-09-09-phase-2-verification.md:32`、`:45`；现有 `tests/test_cb_factors_contract.py:93` 起的用例未覆盖重复值。

校验器遇到重复项执行 continue，而不是报错。`["AAA", " aaa "]` 会被接受并去重，验收记录却两次声称重复评级返回 422。此项是合同偏离，不等同于重复值必然造成数据损坏。

- [ ] 按已定合同，在 trim/uppercase 后检查重复并返回 422。
- [ ] 同时测试完全重复和大小写/空白归一后重复，断言文件没有变化。
- [ ] 修改验收记录，仅以实际存在且执行过的测试关闭此项。

## 3. 上轮目标核对与本轮验证

| 上轮项 | 本轮判断 | 当前证据与剩余边界 |
|---|---|---|
| P2-R01 服务端数值约束 | 实现已补，执行验收待补 | `IntradayFilterQuery._check` 与 `test_inverted_price_range_rejected` 等已存在；本轮未重跑后端测试。 |
| P2-R02 策略输入 | 部分达标 | 字符串/对象拒绝逻辑已增加；R3-02、R3-05 未闭环。 |
| P2-R03 同步刷新 | 部分达标 | `DataStatus.vue:152` 两入口开始复用控制器；R3-04 证明卸载边界仍有反例。 |
| P2-R04 评级目录 | 部分达标 | `get_ratings_catalog` 接入发现值；R3-03 中默认条件仍固定枚举。 |
| P2-R05 回归保护 | 部分达标 | `frontend/tests/unit/Settings.test.js` 7 项通过；验收记录 `:53` 明确缺少另三页测试，另有 R3-01。 |
| P2-R06 设置结果 | 本轮覆盖范围达标 | `Settings.test.js` 的“保存成功但重读失败”及取消恢复测试已运行通过。 |
| P2-R07 默认规则 | 部分达标 | `Factors.vue:78` 保持复制模板；快捷筛选默认空评级未实现，见 R3-03。 |

本轮实际执行结果：

| 检查 | 结果 |
|---|---|
| frontend 下 `pnpm test` | Node 工具测试 30 项、Settings 组件测试 7 项通过。 |
| frontend 下 `pnpm build` | 通过，2256 个模块。 |
| `git diff --check 02c2cce..93e608b` | 通过。 |
| 原评级规范化函数隔离执行 | 缺省/null 变七档的反例成立。 |
| 原 syncOne + 真实控制器隔离执行 | stop 后迟到 POST 重新启动刷新的反例成立。 |
| 后端 pytest | 本轮未执行。PATH 的 python 为 2.7，python3 缺 FastAPI，py 启动器没有注册环境；没有安装依赖或借用日常配置启动测试。 |

作者记录的“160 passed”保留为作者报告，本轮未独立复现。纯函数测试、路由测试不能替代页面点击、Axios 参数与卸载生命周期验收。构建通过也不能关闭上述行为缺陷。

## 4. 下一阶段目标与边界

**阶段名称：合同收口 + 数据库迁移基线。** 按 1 名前端、1 名后端、QA 兼职估算 5～7 个工作日；为排期估计，不是完成承诺。

交付目标：

1. R3-01～R3-05 各有修复、对应反例测试及执行记录。
2. Bonds、Factors、DataStatus 的关键用户路径进入组件测试，保持现有 Settings 回归。
3. 建立 Alembic 初始版本及离线数据库接管演练，让后续共享配置进库和任务持久化具备可验证的升级入口。

保留 Vue + FastAPI 模块化单体。本阶段不新增队列服务，不改业务表字段，不搬迁 JSON 配置，不重做首页；现有 `TaskRunLog` 已在 `backend/models/data_status.py:17` 定义，下一轮设计 operation/run 时须复用或明确迁移，不能假设项目完全没有任务记录表。

前端修复和后端隔离可同时推进；数据库结构清单、迁移设计可提前准备。只有隔离测试通过后才执行临时数据库迁移演练。日常数据库的实际接管不属于本阶段交付动作。

## 5. 可分派任务

### T1：建立隔离测试入口（后端，约 1 天，最高优先）

修改 `tests/conftest.py`、两个 `test_cb_*_contract.py`、`README.md`；按需新增 `tests/test_test_environment.py`、`scripts/test-backend.ps1`。

- [ ] 将测试环境设置放在导入 backend 配置之前；统一请求库、启动行为、配置文件路径和网络替身。
- [ ] 路由测试关闭生产 lifespan；独立生命周期测试注入测试 engine 和 scheduler spy，不能靠彻底跳过启动测试掩盖启动缺陷。
- [ ] 默认禁止外部网络/SMTP，意外调用直接失败；临时文件放独立测试目录并验证清理，禁止忽略清理异常后继续宣称无残留。
- [ ] 提供明确 Python 3.11+ 解释器路径的命令。不要假设 Windows 的 `python` 就是 Python 3。
- [ ] 增加 `test_contract_client_never_calls_production_startup`、`test_request_session_is_closed`、`test_external_io_is_blocked`（拟新增测试名）。

验收：从含日常 `.env` 的工作目录运行，仍完全使用隔离资源；哨兵若被故意触发，测试必须失败。不以实际触碰日常库来证明隔离。

### T2：统一新请求与旧文件的评级边界（后端，约 1 天，依赖 T1）

修改 `backend/api/schemas/cb_screen.py`、`backend/api/routes/cb_screen.py`、`backend/services/cb_factors.py`、`tests/test_cb_factors_contract.py`。

- [ ] 先加入 R3-02/R3-05 反例，再保存已校验模型输出。
- [ ] 拆出明确的旧文件迁移函数；持久化入口只接收当前规范结构。
- [ ] 新请求：缺省/空数组为不限，null/错误类型/空项/归一后重复/旧字段返回 422。
- [ ] 旧文件：七档选择和排除式旧配置读取结果保持；迁移连续读取两次结果一致。
- [ ] 使用临时文件验证“POST 响应 → 文件 → GET”一致，422 不改文件。
- [ ] 新增 `test_missing_ratings_round_trip_is_unrestricted`、`test_null_ratings_rejected`、`test_new_post_rejects_legacy_exclusions`、`test_normalized_duplicate_ratings_rejected`。

验收：上述输入矩阵全部通过；不要通过扩大所有旧策略评级范围来修复新请求。

### T3：快捷筛选默认与页面合同（前端，约 1 天，可与 T1 同时）

修改 `frontend/src/pages/Bonds.vue`；新增 `frontend/tests/unit/Bonds.test.js`、`frontend/tests/unit/Factors.test.js`；只在需要时修改 API 测试替身。

- [ ] 修复 R3-03，首次加载和重置均显示“不限”；已有选择保持。
- [ ] 点击查询检查请求次数、数值转换、loading 恢复；非法文本不调用筛选接口。
- [ ] API 层验证逗号评级编码，包含 `AA+` 和 `NONE`；测试不能仅 mock 页面导入函数而跳过序列化。
- [ ] 目录新增 BB+ 可以选择和保存；目录失败提供重试/提示，不改变选择范围。
- [ ] Factors 覆盖复制当前模板、空评级、未知评级保存、预览 null 数据、实际入选/缓冲计数渲染。

验收：页面行为与路由合同分别有测试，并明确各自覆盖边界；旧用户条件恢复不被首次默认值覆盖。

### T4：封闭同步生命周期（前端，约 1 天，可与 T2 同时）

修改 `frontend/src/pages/DataStatus.vue`、`frontend/src/utils/dataManagementPolling.js` 及其测试；新增 `frontend/tests/unit/DataStatus.test.js`。

- [ ] 在页面卸载时永久失效当前实例；检查 syncOne 和 addIndex 成功路径的每个 await 续体。
- [ ] 为控制器增加 dispose 或等价机制，保留正常 stop 后重新启动能力。
- [ ] 添加延迟 POST、延迟列表 GET、卸载、同 tick start/stop 的反例测试。
- [ ] 两条入口分别验证一次触发请求、随后只读刷新；失败停止、5 分钟截止不再发新请求。
- [ ] 使用 Vitest 假时钟验证，无需真实等待五分钟；测试结束检查无定时器残留。

验收：POST 已被服务器接受后离开页面，后台任务可以继续，但页面不得继续建立 GET 或定时器；不把数据新鲜度显示为本次任务成功。

### T5：数据库迁移基线与接管演练（后端，约 2 天，执行依赖 T1）

新增 `alembic.ini`、`migrations/env.py`、`migrations/versions/0001_initial_schema.py`、`scripts/check_db_baseline.py`、`tests/test_migration_baseline.py`、`docs/database-migration-runbook.md`；修改 `requirements.txt`。目录名以本任务为约定。

- [ ] 从 `backend/models/app_setting.py`、`data_status.py`、`valuation.py` 登记完整 metadata。先产出表/列/主键/索引/唯一约束清单；本轮未检查日常 SQLite 实际结构，不能直接认定一致。
- [ ] 初始 revision 在空临时库创建当前全部业务表；手工审查生成结果，不只保留 autogenerate 输出。
- [ ] `check_db_baseline.py` 必须显式接收目标库，默认只读；缺表、多表、列类型/可空性、索引或约束差异均报告，不能静默忽略未知结构。
- [ ] 已有无版本库：仅在结构匹配后允许显式 stamp 初始 revision；有漂移时停止，不盲目 stamp head，不直接对已有表运行重复建表迁移。
- [ ] 用测试生成的旧库及其快照演练，包含设置、黑名单、估值快照和任务日志样本。检查接管前后行数、主键集合和关键值一致。
- [ ] 活跃 SQLite 的备份说明采用 SQLite backup API 或停写后的一致性备份；禁止只复制正在 WAL 写入的主 db 文件作为有效备份。
- [ ] 测试空库 upgrade、重复 upgrade、已有库检查+stamp、结构不匹配拒绝、备份恢复。
- [ ] 初始 downgrade 可能删表，只在临时库测试；日常回退方案采用已验证备份恢复，文档注明停写及数据损失窗口。

验收：提交可运行迁移脚本和临时库演练证据。本阶段不让日常启动自动迁移，也不立即移除 `init_db/create_all`；下一轮必须单独决定启动校验及部署接管，明确当前过渡状态。

### T6：汇总验收与交接（QA + 开发，约 0.5～1 天）

- [ ] 固定提交 SHA、解释器绝对路径、依赖安装方式及每条命令退出码；不记录凭证或真实配置内容。
- [ ] 运行隔离后端全量测试、前端全量测试、生产构建及 `git diff --check`。
- [ ] 在无外部数据源环境走通筛选、策略、同步离页、设置保存重读失败四条页面路径；未实际手工执行就写未执行。
- [ ] 新增下一轮验收记录，并对 `2026-09-09-phase-2-verification.md` 的重复评级、快捷默认和隔离声明添加更正，保留历史上下文。
- [ ] README 明确前后端开发端口一致；后端测试命令指向 T1 的安全入口。

## 6. 阶段门禁与提交顺序

建议按 T1、T2、T3、T4、T5、T6 分别提交。每个修复提交包含对应反例测试；迁移基线单独提交，便于独立 review。

**门禁 A：合同收口。** R3-01～R3-05 全部有测试通过证据，三页组件测试补齐，全量后端在隔离环境执行，前端测试和构建通过。完成后可以判定本轮合同收口达标。

**门禁 B：迁移基线。** 空库升级、旧库接管、漂移拒绝、备份恢复均在临时库通过。完成后可以进入“共享配置进库与持久化同步操作”设计；实际日常数据迁移仍需独立实施单。

提交验收清单：

- [ ] 每条 R3 意见都有修复 commit、文件位置、测试名、执行结果。
- [ ] 验收记录没有将作者自述、代码阅读或未执行路径写成测试通过。
- [ ] 测试资源与日常运行资源完全分离。
- [ ] 首次与重置不限评级，已有用户条件保留。
- [ ] 页面卸载后无新刷新，设置既有 7 项回归保持通过。
- [ ] 初始 migration 与现有模型一致，接管失败不会修改目标库。
- [ ] 无凭证、数据库、构建产物或临时测试数据误入提交。

## 7. 后续固定推进流程

后续每轮以本次已审核 HEAD `93e608b` 为下一轮比较起点；新文档记录实际范围，不将全部历史提交反复作为新成果。

1. 检查分支、工作区和 commit log，区分已提交内容及未提交改动。
2. 对照上轮目标逐项审查 diff，先列带文件行号/测试名的具体缺陷，再列已验证项。
3. 明确“达标 / 部分达标 / 不达标”，缺少执行证据写待验收；不为满足数量编造缺陷。
4. 输出下一阶段目标、任务、文件、依赖、反例测试及完成门禁；未关闭项明确结转。
5. 开发者按任务提交实现与证据，下轮 reviewer 独立复核。未通过门禁不得仅修改验收文案宣布完成。
