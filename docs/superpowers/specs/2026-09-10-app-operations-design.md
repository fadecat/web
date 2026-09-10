# 应用启停脚本设计

## 目标

把 Windows 日常开发和 ECS 正式运行的启停流程固化为同一套命令语义，支持整个应用、后端和前端的 `start`、`stop`、`restart`、`status`，并提供前端 `build`。脚本不得依赖 AI 临时拼装命令，也不得把密钥、IP或密码写入仓库。

## 命令入口

Windows 用户只需调用：

```powershell
.\scripts\app.ps1 <action> [target] [-Environment local|ecs]
```

- `action`：`start`、`stop`、`restart`、`status`、`build`。
- `target`：`all`、`backend`、`frontend`，省略为 `all`。
- `Environment`：省略为 `local`；`ecs` 通过 SSH 调用仓库内的 `scripts/app.sh`。
- ECS 默认 SSH 别名 `aliyun-ecs`、项目目录 `/opt/webapp`、systemd 服务 `webapp`，均允许参数或环境变量覆盖。

## Windows 本地行为

- 后端使用 Python 3.11+ 启动 `uvicorn backend.main:app --host 127.0.0.1 --port 8001 --app-dir <repo>`。
- 前端直接使用 Node 启动仓库已安装的 Vite，监听 `127.0.0.1:5173`。
- PID、启动时间、进程名、仓库归属和标准输出写入 `.runtime/`。停止前核对进程指纹，避免旧PID误杀其他Python或Node进程；该方式不要求管理员权限。
- `status` 同时报告PID状态与HTTP状态；后端检查 `/api/health`，前端检查根页面。任一目标未运行时返回非零。
- `restart all` 先停止前端和后端，再先后启动后端、前端。`build frontend` 运行 `pnpm build`，不重启开发服务器。
- Python按 `APP_PYTHON`、项目 `.venv`、PATH中的Python 3.11+、本机WorkBuddy Python顺序发现；找不到时给出明确配置提示。

## ECS 行为

- 后端启停统一调用 `systemctl <action> webapp`。
- 前端没有独立进程。`build frontend` 在临时目录构建，成功后替换 `frontend/dist`；失败保留原构建。
- `restart frontend` 执行依赖安装、原子化构建切换、重启后端、健康检查。
- `restart all` 与 `restart frontend` 一致，作为完整发布后重启入口。
- `start all` 在构建不存在时先构建，再启动后端；`stop all` 停止后端，即停止整个站点。
- ECS 上拒绝 `start frontend` 和 `stop frontend`，并解释静态前端没有独立进程。
- 健康检查失败、构建失败或systemd失败均返回非零，后续步骤停止。

## 安全与可诊断性

- `-DryRun` 和 `APP_OPS_DRY_RUN=1` 只打印将执行的动作，供检查和自动化测试使用。
- 本地日志、PID、临时构建目录加入 `.gitignore`。
- 脚本从自身位置解析仓库根目录，不依赖调用时的当前目录。
- ECS构建只删除固定的 `frontend/.dist-next` 与 `frontend/.dist-prev`；替换失败时恢复旧 `dist`。
- 不执行数据库迁移、数据同步、Git拉取或Git推送；这些动作继续由发布流程显式执行。

## 验收

- Windows dry-run覆盖本地完整重启、后端停止、前端构建和ECS转发。
- ECS dry-run覆盖完整重启、后端状态、前端构建及非法前端启停。
- 脚本参数错误和不支持组合返回非零。
- README提供可复制命令、环境覆盖方式、日志位置与前端在ECS没有独立进程的说明。
- 实际本地状态检查不得停止或覆盖用户已有进程；真实启停仅在明确执行对应命令时发生。
