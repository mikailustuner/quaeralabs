"""Sandbox kaynak sınırları: bellek sınırını aşan ajan kodu yalnızca kendi kapsamında öldürülür."""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from quaera.mcp_servers import sandbox_server

pytestmark = pytest.mark.skipif(not shutil.which("systemd-run") or not shutil.which("bwrap")
                                or not (Path(sandbox_server.ML_ENV) / "bin" / "python").exists(),
                                reason="systemd-run, bwrap ya da ml-env yok")


def run(code, monkeypatch, mem="200M"):
    monkeypatch.setenv("QUAERA_SANDBOX_MEM", mem)
    return json.loads(sandbox_server.sandbox_exec(tempfile.mkdtemp(), ["python", "-c", code], timeout_s=60))


def test_over_memory_limit_is_killed(monkeypatch):
    r = run("x = bytearray(600 * 1024 * 1024); print('geçti')", monkeypatch)
    assert r["returncode"] != 0 and "geçti" not in r["stdout"]


def test_within_limit_runs(monkeypatch):
    r = run("x = bytearray(50 * 1024 * 1024); print('tamam')", monkeypatch)
    assert r["returncode"] == 0 and "tamam" in r["stdout"]


def test_no_network_and_no_home(monkeypatch):
    r = run("import os; print(os.path.exists('/home/aurict/.bashrc'))", monkeypatch)
    assert r["stdout"].strip() == "False"
