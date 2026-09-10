# Application Operations Scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one Windows command surface that safely manages local development and delegates ECS operations to a matching repository script.

**Architecture:** `scripts/app.ps1` owns Windows PID-managed development processes and SSH delegation. `scripts/app.sh` owns ECS systemd and frontend build operations. Both expose the same action/target vocabulary and support dry-run verification.

**Tech Stack:** PowerShell 7, Bash, systemd, Uvicorn, Vite, pytest subprocess tests.

---

### Task 1: Command contract tests

**Files:**
- Create: `tests/test_app_operations_scripts.py`

- [x] Add subprocess tests for PowerShell local and ECS dry-run combinations and Bash ECS dry-run combinations.
- [x] Assert invalid action/target combinations exit nonzero and contain a concrete error.
- [x] Run `python -m pytest tests/test_app_operations_scripts.py -q -p no:cacheprovider` and verify failure because scripts do not exist.

### Task 2: Windows controller

**Files:**
- Create: `scripts/app.ps1`
- Modify: `.gitignore`

- [x] Implement validated positional action/target parameters and environment parameters.
- [x] Implement Python/Node/pnpm/SSH discovery with actionable errors.
- [x] Implement PID ownership checks, start/stop/restart/status, HTTP health checks and `.runtime` logs.
- [x] Implement ECS delegation with validated remote parameters.
- [x] Run the PowerShell subset until green.

### Task 3: ECS controller

**Files:**
- Create: `scripts/app.sh`

- [x] Implement systemd backend actions, status and HTTP health checks.
- [x] Implement frontend dependency install and temporary build/swap/rollback.
- [x] Implement whole-app semantics and reject unsupported standalone frontend start/stop.
- [x] Run all script contract tests until green.

### Task 4: Operator documentation and verification

**Files:**
- Modify: `README.md`

- [x] Add copyable Windows local and ECS command examples, configuration overrides, logs and troubleshooting.
- [x] Run script tests, full backend tests, frontend tests and frontend build sequentially.
- [x] Run `git diff --check` and verify only intended files are staged for this feature.
