# Phase 5.3 跨平台发布及运维实证记录

## 1. 变更范围

- 基线：`cf3f6d0`
- 已验证实现提交：`673c5b14b49877e55a18b033bd55ca3e67e13c1c`
- 目标：修复 no-clobber 发布、发布后失败状态、绝对路径门禁和 dependency 返回值契约。
- 数据边界：本轮测试仅使用 `.test-artifacts` 临时库，未访问 `data/web.db`。

## 2. 本机验证

| 命令 | 结果 |
|---|---|
| 受管 Python `-m pytest tests -q -p no:cacheprovider` | 267 passed，退出 0 |
| `frontend/pnpm test` | Node 34 passed；Vitest 32 passed，退出 0 |
| `frontend/pnpm build` | 2256 modules transformed，退出 0 |
| `git diff --check` | 通过 |
| 解释器 `sys.version` | 3.13.14 |

## 3. 新增反例门禁

- 既有目标字节在 `_publish()` 冲突后保持不变，并验证发布文件完整等于 candidate partial。
- 公共 `backup()` 的 source connect 失败不留下显式目标文件。
- 发布后不再执行表统计或 `stat()`；统计异常只能发生在提交点之前。
- `sqlite:///relative.db` 对 source 与 backup-copy 均返回路径护栏错误。
- dependency 返回 `[1]` 时返回结构化阶段失败，不抛 `TypeError`。

## 4. 完整 ORM 三库运维演练

- 临时目录：`D:/gitub_codes/web/.test-artifacts/phase53_acceptance_20260910_124758_096`
- 真实命令：`python -m scripts.backup_db` 后接 `python -m scripts.adopt_db_copy`
- backup 退出码：0；source 与副本均为 10 张业务表。
- adopt 退出码：0；`backup_verified`、`schema_verified`、`stamped`、
  `revision_verified`、`upgraded`、`smoke_passed` 六阶段全部通过。
- source SHA-256：`B62BFBDB93E9CD9C1B89D96DBC42089D5814BB5510F4D9BE1B0A4F0FF3B78A89`；
  大小 131072；mtime ticks `639246124789658345`，前后完全一致。
- unrelated SHA-256：`63B6DE089DC22C41DF85E926B5076CAD0D324A1C711A4269FC3AFAF52A9F038A`；
  大小 8192；mtime ticks `639246124791241815`，前后完全一致。
- copy 最终 SHA-256：`30C8B58EF527C9C1A71752EB739A166645A31FEBC02EFB79D9691906901D4540`；
  Alembic revision 为 `0001`，目录中 `.partial` 数量为 0。

## 5. 跨平台门禁与裁决

- GitHub Actions run：`34440497864`
- 地址：`https://github.com/fadecat/web/actions/runs/34440497864`
- `database-tools (windows-latest)`：成功。
- `database-tools (ubuntu-latest)`：成功。
- 总状态：Success；两个 job 完成，耗时 1m36s。

**裁决：Phase 5.3 达标。** 本地全量、真实三库演练、Windows/Linux 专项矩阵和
独立红队复核均已通过，Phase 5 数据库安全收口完成，可以进入 Phase 6。
