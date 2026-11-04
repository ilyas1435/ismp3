from __future__ import annotations

import os
from pathlib import Path

import pytest

from ismp3 import probe


def test_tool_issue_explains_glibc_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "ffprobe"
    fake.write_text(
        "#!/bin/sh\n"
        "echo \"libc.so.6: version 'GLIBC_2.38' not found\" >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    issue = probe.tool_issue("ffprobe")
    assert "GLIBC" in issue
    assert "Replit" in issue
    assert "stable-24_05" in issue


def test_probe_reports_glibc_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "ffprobe"
    fake.write_text(
        "#!/bin/sh\n"
        "echo \"libc.so.6: version 'GLIBC_2.38' not found\" >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"\xff\xfb")
    monkeypatch.setenv("PATH", os.pathsep.join([str(tmp_path), os.defpath]))

    with pytest.raises(ValueError, match="تعارض مكتبات"):
        probe.probe(sample)
