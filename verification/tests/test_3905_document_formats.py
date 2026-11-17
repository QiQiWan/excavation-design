"""多格式资料读取：XLSX / DOCX / CSV 都能抽到同一批字段。"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "services" / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "services" / "api"))

from app.agents import document_agent  # noqa: E402

ROWS = [
    ("参数", "值"),
    ("地基承载力特征值", "180 kPa"),
    ("施工荷载", "30 kPa"),
    ("基坑安全等级", "三级"),
    ("混凝土等级", "C30"),
]


def _xlsx() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    for row in ROWS:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _docx() -> bytes:
    import docx

    document = docx.Document()
    for key, value in ROWS:
        document.add_paragraph(f"{key}：{value}")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _csv() -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(ROWS)
    return buffer.getvalue().encode("utf-8-sig")


def _values(result: dict) -> dict:
    return {field["key"]: field["value"] for field in result["fields"]}


def test_reads_xlsx() -> None:
    result = document_agent.extract("参数表.xlsx", _xlsx())
    assert result["document"]["fileType"] == "xlsx"
    values = _values(result)
    assert values["surchargeGeneral"] == 30.0
    assert values["safetyLevel"] == "三级"
    assert values["bearingCapacityKpa"] == 180.0


def test_reads_docx() -> None:
    result = document_agent.extract("参数表.docx", _docx())
    assert result["document"]["fileType"] == "docx"
    values = _values(result)
    assert values["surchargeGeneral"] == 30.0
    assert values["concreteGrade"] == "C30"
    assert values["bearingCapacityKpa"] == 180.0


def test_reads_csv() -> None:
    result = document_agent.extract("参数表.csv", _csv())
    assert result["document"]["fileType"] == "text"
    assert _values(result)["bearingCapacityKpa"] == 180.0


def test_rejects_unknown_format() -> None:
    try:
        document_agent.extract("data.bin", b"\x00\x01\x02")
    except ValueError as exc:
        assert "暂不支持" in str(exc)
    else:
        raise AssertionError("expected ValueError")
