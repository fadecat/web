# 集思录账号池设计

## 目标

将单账号认证改为对调用方透明的账号池。管理员可在前端增删改查账号、启停账号并查看状态和当日调用量；业务抓取器只调用统一网关，不接触账号、密码或 Cookie。

## 边界

- 第一版面向单台 ECS、单数据库，可支持 FastAPI 与 APScheduler 并发线程。
- 不通过增加账号扩大瞬时总请求量；同时实施全局限速和账号级限速。
- 不高频探活。有效 Cookie 长期复用，只有明确返回未登录、登录页或人工触发检测时才探活或登录。
- 密码和 Cookie 只在后端保存，API 永不回传明文，日志只记录账号 ID 和脱敏用户名。

## 数据模型

`JisiluAccount` 保存配置与运行状态：`id`、`name`、`username`、`password`、`enabled`、`status`、`cookie`、`cookie_saved_at`、`last_request_at`、`last_success_at`、`last_failure_at`、`cooldown_until`、`daily_request_date`、`daily_request_count`、`daily_login_date`、`daily_login_count`、`consecutive_failures`、`last_error`、`created_at`、`updated_at`。

状态为 `ready`、`unknown`、`cooldown`、`invalid`、`disabled`。密码与 Cookie 是敏感字段，列表和详情接口只返回 `password_configured`、`cookie_configured`。

## 调度与失败语义

账号池在可用账号中选择当前请求数最少、最后请求时间最早的账号。同一业务批次可获取租约并复用同一账号。每个账号有最小请求间隔、每日登录预算和登录失败冷却；全局请求也有最小间隔。

网络错误不惩罚账号；明确未登录时清除该账号 Cookie，并在登录预算允许时重新登录一次；429、刷新过快或验证码使账号进入冷却；解析错误直接上抛，不轮换账号。单次业务请求最多换一个账号，防止源端异常耗尽整个池。

## 管理 API

- `GET /api/jisilu/accounts`：脱敏列表与池汇总。
- `POST /api/jisilu/accounts`：新增账号。
- `PUT /api/jisilu/accounts/{id}`：修改名称、用户名、密码和启用状态；密码留空保持原值。
- `DELETE /api/jisilu/accounts/{id}`：删除账号。
- `POST /api/jisilu/accounts/{id}/check`：人工探活；仅在 Cookie 无效时且预算允许才登录。
- `POST /api/jisilu/accounts/{id}/reset-status`：清除人工可恢复的错误和冷却，不主动登录。

## 前端

系统设置页新增“集思录账号池”卡片，显示池内总数、可用数、冷却数、今日请求总量。账号表显示名称、脱敏用户名、启用状态、会话状态、今日调用、今日登录、最近成功、冷却截止和最近错误。支持新增、编辑、删除、启停、检测和重置状态。

## 兼容迁移

保留 `get_cookie()` 作为兼容入口：若数据库账号池为空，继续读取原 `JISILU_USERNAME/JISILU_PASSWORD` 和旧会话文件；账号池存在时由统一池选择账号。随后逐个把抓取器迁移到统一 `request()`，完成后旧入口只用于兼容测试。

## 验收

- 密码、Cookie 不出现在 API 响应或日志。
- 同账号在 Cookie 有效时不会重复登录。
- 单账号每日登录达到预算后不会再次访问登录接口。
- 冷却账号不会被选择，全部不可用时返回可诊断错误。
- CRUD、启停、检测、状态重置和调用量展示可用。
- 集思录转债列表、强赎、指数、高股息和讨论抓取均通过统一网关。
