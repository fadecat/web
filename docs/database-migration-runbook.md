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

```powershell
# 1) 停止写入/调度(服务窗口, 避免期间写入)
#    生产: systemctl stop webapp

# 2) 一致性备份(含 WAL, 拒绝覆盖已有目标)
python scripts/backup_db.py --source D:/path/to/web.db --destination-dir D:/path/to/backups
#    → 生成 D:/path/to/backups/web.db.<ts>.db

# 3) 对备份副本做只读结构核对(任何差异立即停止, 禁止 stamp)
python scripts/check_db_baseline.py --database-url sqlite:///D:/path/to/backups/web.db.<ts>.db
#    → 输出"结构匹配"才可继续; 有差异则提交差异报告, 先对齐结构

# 4) 在副本上显式 stamp 0001(不升级, 只写版本号)
python -m alembic -x database_url=sqlite:///D:/path/to/backups/web.db.<ts>.db stamp 0001

# 5) 确认版本
python -m alembic -x database_url=sqlite:///D:/path/to/backups/web.db.<ts>.db current
#    → 0001

# 6) 副本执行 upgrade head(空操作幂等, 验证迁移链完整)
python -m alembic -x database_url=sqlite:///D:/path/to/backups/web.db.<ts>.db upgrade head

# 7) 恢复副本验证(integrity/表集合/行数/主键/结构)
python scripts/verify_db_restore.py --source D:/path/to/web.db --backup D:/path/to/backups/web.db.<ts>.db
#    → 输出"✅ 恢复副本验证通过"

# 8) 隔离应用冒烟(用副本临时启动, 或部署验证)
```

**硬性规则**:
- 结构有任意差异**立即停止**, 不 stamp、不 upgrade、不对已有表重复建表。
- 禁止对不匹配库执行 `stamp head`。
- 本阶段**不对 `data/web.db` 执行**这些命令。

## 3. 新空库初始化

```powershell
python -m alembic -x database_url=sqlite:///D:/absolute/path/to/new.db upgrade head
# → 创建 10 张业务表 + alembic_version
```

## 4. 备份与恢复

- 备份必须用 SQLite backup API(`scripts/backup_db.py`), **禁止只复制主 .db 文件**
  (WAL 模式下主文件不含未 checkpoint 写入)。
- 目标文件已存在时脚本**拒绝覆盖**(fail closed)。
- 恢复演练: 把副本当作源启动应用, 走冒烟。

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
