"""计算书上传先校验：非 PDF / 空文件不得排进任务队列。"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "services" / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "services" / "api"))

from app.agents.router import calculation_book_autopilot  # noqa: E402


def _request() -> SimpleNamespace:
    identity = SimpleNamespace(role="designer", actor="tester")
    return SimpleNamespace(state=SimpleNamespace(pitguard_identity=identity))


def _upload(name: str, content: bytes) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content))


def test_rejects_non_pdf_before_queueing() -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(calculation_book_autopilot(_request(), _upload("fake.txt", b"not a pdf")))
    assert exc.value.status_code == 400
    assert "%PDF" in str(exc.value.detail)


def test_rejects_empty_file() -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(calculation_book_autopilot(_request(), _upload("empty.pdf", b"")))
    assert exc.value.status_code == 400
    assert "空" in str(exc.value.detail)


def test_rejects_oversized_file() -> None:
    big = b"%PDF-1.4" + b"0" * (80 * 1024 * 1024 + 10)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(calculation_book_autopilot(_request(), _upload("big.pdf", big)))
    assert exc.value.status_code == 413
