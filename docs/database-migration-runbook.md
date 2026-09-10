# 数据库迁移接管 Runbook

> 第四阶段(T5/T6/T7)。本 runbook 只覆盖**已有 SQLite 库的显式接管演练**；
> 日常 `data/web.db` 的实际接管是独立实施单, 本阶段**不执行**。

## 0. 现状与过渡状态

- 应用启动仍用 `backend/models/database.py` 的 `init_db()`(`create_all`), **不自动迁移**。
- Alembic 已建立初始版本 `0001`(10 张业务表), 仅对**显式传入 URL** 的临时库执行。
- 过渡退出计划: 后续阶段单独决定启动校验与部署接管(见 phase-4 计划 §门禁)。

## 1. 前置要求

- Python 3.11+(项目当前用受管 3.13)。
- 依赖含 `alembic>=1.14.0,<2.0.0`。
- 所有命令**必须显式传 `-x database_url=...`**(占位符/缺失即报错, 防误触日常库)。

## 2. 固定顺序(已有库接管)

> **唯一权威接管流程**: 步骤 1-9 已固化为 `scripts/adopt_db_copy.py` 单一编排入口
> (本节 §2.1), 操作员不得手工拆分执行。§2.2 故障排查附录仅在编排入口失败后
> 用于定位,**stamp 后人工 verify 必须带 `--expected-backup-revision 0001`**。
>
> **R5-03 校正: 已撤销**。原"verify_restore/compare_schema 忽略 alembic_version"
> 的早期实现已被 `scripts/verify_db_restore.py` 的严格 revision 校验替代
> (Phase 5.1 / 5.2): 默认 verify 拒绝源 None / 副本 stamped 0001 的差异,
> 接管特例必须显式传 `--expected-backup-revision 0001`。人工诊断沿用旧文
> 极易在 stamp 后得到 "migration revision 不一致" 误报, 请改用编排入口。

**写入边界(Phase 5.2 起, 不可违反)**:
- `source`: 只读(备份 API 也以 `mode=ro` 打开, 禁止写入)。
- `backup_copy`: 允许 backup、stamp、upgrade、smoke 的 init_db; 不得作为其他用途。
- 任何 `unrelated` / `global` / 日常 `data/web.db`: 禁止被 adopt 或 smoke 打开。
- 一旦 stamped 之后失败, copy 标记为"不可交付", 必须从已验证 backup 重新
  开始完整接管链, 不得"补跑"后续命令(stamp 前的二进制安全摘要已记录在源).

## 2.1 接管副本: 单一编排入口(推荐)

> Task 5(R6-03/R6-04): 步骤 3-9 的接管链已固化为一个失败即停的编排入口,
> 操作员**禁止跳过 compare 直接 stamp**, 也无需手工逐条执行状态机命令。

```powershell
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m scripts.adopt_db_copy --source D:/path/to/web.db --backup-copy D:/path/to/backups/web.<ts>.db --revision 0001
```

固定状态机(不可跳步, 任一阶段失败立即停止并返回非 0):

| 阶段 | 含义 | 失败退出码 |
|---|---|---|
| backup_verified | 恢复验证(integrity/表集合/行数/主键/内容摘要) | 1 |
| schema_verified | 结构核对(任何差异禁止 stamp) | 1 |
| stamped | 在副本显式 stamp 指定 revision | 1 |
| revision_verified | stamp 后用显式接管策略再次验证 | 1 |
| upgraded | upgrade head 且 current 等于 head | 1 |
| smoke_passed | 隔离应用冒烟(真实读库路由命中副本) | 1 |
| path_guard / revision_arg | 输入护栏(同文件/日常库/空 revision) | 2 |

产物记录: 每次运行把命令、各阶段 [PASS]/[FAIL] 输出与最终退出码记入操作日志。
护栏: 拒绝 backup_copy 等于日常 `data/web.db`, 拒绝 source 与 backup_copy 同文件。
冒烟可单独执行:

```powershell
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m scripts.smoke_db_copy --database D:/path/to/backups/web.<ts>.db --expect-key smtp_host --expect-value <明文值>
```

## 3. 新空库初始化

```powershell
python -m alembic -x database_url=sqlite:///D:/absolute/path/to/new.db upgrade head
# → 创建 10 张业务表 + alembic_version
```

## 4. 备份与恢复

- 备份必须用 SQLite backup API(`scripts/backup_db.py`), **禁止只复制主 .db 文件**
  (WAL 模式下主文件不含未 checkpoint 写入)。
- 目标文件已存在时**备份 API 与 CLI 都拒绝覆盖**(fail closed); 文件名带微秒
  时间戳, 同秒两次备份不冲突。
- 命令模式互斥: `--source <库>` 或 `--list <目录>` 二选一, 两者都缺/都传报错。
- 恢复演练: 把副本当作源启动应用, 走冒烟。

```powershell
# 列出备份
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m scripts.backup_db --list D:/path/to/backups
```

## 5. 失败停止与恢复

- 结构不匹配 → 停止, 保留差异报告, 不碰目标库。
- 日常回退: 采用已验证备份恢复, **停写窗口 + 数据损失窗口**在实施单中声明;
  `downgrade` 会删表, 只允许在临时库测试, 不作为日常回退手段。

## 6. 测试清单(全部在临时库, 不触日常库)

`tests/test_migration_baseline.py`(8 项): 空库 upgrade / 重复 upgrade /
downgrade 删业务表 / stamp+upgrade 幂等 / 结构匹配 / 漂移报告不改库 /
缺列报告 / 可空性漂移。
`tests/test_backup_restore.py`(6 项): WAL 行捕获 / 拒绝覆盖 / 一致副本通过 /
缺行拒绝 / 结构漂移拒绝 / 主键漂移拒绝。
