# -*- coding: utf-8 -*-
"""_delete_path_fail_closed 单元测试(R6-07).

验证:
1. 标准库删除连续失败时, 最终抛出的异常以最后一个 OSError 为 cause(原始异常不丢失)。
2. os.name == "nt" 与 "posix" 行为一致: 都不会出现裸 raise 的 RuntimeError, 也不依赖 ctypes。
3. 目录经由 os.rmdir 路径删除。
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

from tests.conftest import _delete_path_fail_closed


@pytest.mark.parametrize("os_name", ["nt", "posix"])
def test_delete_path_chains_last_oserror(monkeypatch, os_name):
    """os.remove 持续失败时, 最终 OSError 以最后一个 PermissionError 为 __cause__。

    nt/posix 都必须如此, 且不得出现裸 RuntimeError、不得导入 ctypes。
    """
    monkeypatch.setattr(os, "name", os_name)

    # 记录调用期间是否有 ctypes 被导入
    imported_before = set(sys.modules)

    class DistinctPermissionError(PermissionError):
        pass

    def _failing_remove(_path):
        # 始终失败, 带可识别文本
        raise DistinctPermissionError("distinct-permission-denied-mark")

    monkeypatch.setattr(os, "remove", _failing_remove)
    monkeypatch.setattr(os, "rmdir", _failing_remove)

    with pytest.raises(OSError) as excinfo:
        # 不存在路径 → is_dir() 为 False → 走 os.remove 分支
        _delete_path_fail_closed(pathlib.Path("__never_existing_cleanup_test__"))

    raised = excinfo.value
    # 不得是裸 RuntimeError(原实现在 posix 分支裸 raise 会触发)
    assert not isinstance(raised, RuntimeError), f"不应触发裸 RuntimeError: {raised!r}"

    cause = raised.__cause__
    assert isinstance(cause, DistinctPermissionError), (
        f"cause 应链到最后一个 PermissionError, 实际: {cause!r}"
    )
    assert "distinct-permission-denied-mark" in str(cause)

    imported_after = set(sys.modules)
    newly = imported_after - imported_before
    assert not any(m == "ctypes" or m.startswith("ctypes.") for m in newly), (
        f"清理实现不应导入 ctypes, 新导入: {sorted(newly)}"
    )


def test_directory_deleted_via_os_rmdir(monkeypatch, test_artifact_dir):
    """目录经由 os.rmdir 删除; os.remove 不得被调用。"""
    target = test_artifact_dir / "empty_dir"
    target.mkdir()

    calls = {"rmdir": 0, "remove": 0}

    real_rmdir = os.rmdir
    real_remove = os.remove

    def _spy_rmdir(p):
        calls["rmdir"] += 1
        real_rmdir(p)

    def _spy_remove(p):
        calls["remove"] += 1
        real_remove(p)

    monkeypatch.setattr(os, "rmdir", _spy_rmdir)
    monkeypatch.setattr(os, "remove", _spy_remove)

    _delete_path_fail_closed(target)

    assert calls["rmdir"] == 1, f"os.rmdir 应被调用一次, 实际: {calls}"
    assert calls["remove"] == 0, f"os.remove 不应被调用, 实际: {calls}"
    assert not target.exists(), "目录应已被删除"
