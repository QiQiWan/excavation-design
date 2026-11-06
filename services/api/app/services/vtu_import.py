from __future__ import annotations

import base64
import importlib.util
import struct
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

FIELD_ALIASES = {
    "mat_id": "stratum_id",
    "material": "material_id",
    "materialid": "material_id",
    "material_id": "material_id",
    "stratum": "stratum_id",
    "stratumid": "stratum_id",
    "stratum_id": "stratum_id",
    "layer": "layer_id",
    "layerid": "layer_id",
    "gamma": "unit_weight",
    "unitweight": "unit_weight",
    "unit_weight": "unit_weight",
    "c": "cohesion",
    "cohesion": "cohesion",
    "phi": "friction_angle",
    "friction": "friction_angle",
    "friction_angle": "friction_angle",
    "e": "elastic_modulus",
    "young": "elastic_modulus",
    "youngsmodulus": "elastic_modulus",
    "elastic_modulus": "elastic_modulus",
    "poisson": "poisson_ratio",
    "poisson_ratio": "poisson_ratio",
    "k": "permeability",
    "permeability": "permeability",
}

VTK_CELL_TYPES = {
    1: "vertex",
    3: "line",
    5: "triangle",
    9: "quad",
    10: "tetra",
    12: "hexahedron",
    13: "wedge",
    14: "pyramid",
}


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _dtype_struct_code(dtype: str) -> tuple[str, int, str]:
    dtype = dtype.lower()
    if dtype in {"float32", "float"}:
        return "f", 4, "float"
    if dtype in {"float64", "double"}:
        return "d", 8, "float"
    if dtype in {"int8"}:
        return "b", 1, "int"
    if dtype in {"uint8", "uchar", "unsigned_char"}:
        return "B", 1, "int"
    if dtype in {"int16"}:
        return "h", 2, "int"
    if dtype in {"uint16"}:
        return "H", 2, "int"
    if dtype in {"int32", "int"}:
        return "i", 4, "int"
    if dtype in {"uint32"}:
        return "I", 4, "int"
    if dtype in {"int64"}:
        return "q", 8, "int"
    if dtype in {"uint64"}:
        return "Q", 8, "int"
    return "d", 8, "float"


def _parse_binary_data_array(element: ET.Element, byte_order: str, header_type: str) -> list[float | int]:
    text = (element.text or "").strip().replace("\n", "").replace(" ", "")
    if not text:
        return []
    raw = base64.b64decode(text)
    endian = ">" if byte_order.lower().startswith("big") else "<"
    header_fmt = "Q" if header_type.lower() == "uint64" else "I"
    header_size = struct.calcsize(endian + header_fmt)
    if len(raw) < header_size:
        raise ValueError("VTU binary DataArray header is incomplete.")
    payload_size = struct.unpack(endian + header_fmt, raw[:header_size])[0]
    payload = raw[header_size : header_size + payload_size]
    dtype = (element.attrib.get("type") or "Float64").lower()
    code, size, kind = _dtype_struct_code(dtype)
    if len(payload) % size != 0:
        raise ValueError(f"VTU binary DataArray payload length does not align with dtype={dtype}.")
    count = len(payload) // size
    values = struct.unpack(endian + code * count, payload) if count else ()
    if kind == "int":
        return [int(v) for v in values]
    return [float(v) for v in values]


def _parse_data_array(element: ET.Element, byte_order: str = "LittleEndian", header_type: str = "UInt32") -> list[float | int | str]:
    text = (element.text or "").strip()
    if not text:
        return []
    fmt = (element.attrib.get("format") or "ascii").lower()
    dtype = (element.attrib.get("type") or "Float64").lower()
    if fmt == "binary":
        return _parse_binary_data_array(element, byte_order, header_type)
    if fmt not in {"ascii", ""}:
        raise ValueError(f"当前轻量 VTU 解析器不支持 DataArray format={fmt}；请安装 meshio 后解析 appended/compressed VTU。")
    values: list[float | int | str] = []
    for part in text.split():
        if "int" in dtype or "uint" in dtype:
            values.append(int(part))
        elif "float" in dtype or "double" in dtype:
            values.append(float(part))
        else:
            values.append(part)
    return values


def _suggest_mapping(fields: list[str]) -> dict[str, str]:
    suggested_mapping: dict[str, str] = {}
    for field in fields:
        key = field.lower().replace(" ", "_").replace("-", "_")
        compact = key.replace("_", "")
        if key in FIELD_ALIASES:
            suggested_mapping[field] = FIELD_ALIASES[key]
        elif compact in FIELD_ALIASES:
            suggested_mapping[field] = FIELD_ALIASES[compact]
    return suggested_mapping


def _build_cell_blocks(cells: dict[str, list[Any]], cell_data: dict[str, list[Any]]) -> list[dict[str, Any]]:
    connectivity = [int(v) for v in cells.get("connectivity", [])]
    offsets = [int(v) for v in cells.get("offsets", [])]
    types = [int(v) for v in cells.get("types", [])]
    blocks: list[dict[str, Any]] = []
    start = 0
    for idx, offset in enumerate(offsets):
        nodes = connectivity[start:offset]
        start = offset
        vtk_type = types[idx] if idx < len(types) else None
        attrs = {name: values[idx] for name, values in cell_data.items() if idx < len(values)}
        blocks.append({
            "index": idx,
            "vtkType": vtk_type,
            "cellType": VTK_CELL_TYPES.get(vtk_type, f"vtk_{vtk_type}"),
            "nodes": nodes,
            "attributes": attrs,
        })
    return blocks


def _bounds(points: list[list[float]]) -> dict[str, list[float]] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] if len(p) > 2 else 0.0 for p in points]
    return {"min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]}


def _parse_vtu_ascii_xml(content: bytes) -> dict[str, Any]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"VTU XML 解析失败：{exc}") from exc

    if any((_strip_namespace(node.tag) == "AppendedData") for node in root.iter()):
        raise ValueError("检测到 VTU AppendedData。当前轻量解析器不能解析 appended/压缩 VTU；安装 meshio 后会自动处理。")

    byte_order = root.attrib.get("byte_order", "LittleEndian")
    header_type = root.attrib.get("header_type", "UInt32")
    saw_binary = False

    piece = None
    for node in root.iter():
        if _strip_namespace(node.tag) == "Piece":
            piece = node
            break
    if piece is None:
        raise ValueError("VTU 文件缺少 UnstructuredGrid/Piece 节点。")

    points: list[list[float]] = []
    cells: dict[str, list[Any]] = {"connectivity": [], "offsets": [], "types": []}
    point_data: dict[str, list[Any]] = {}
    cell_data: dict[str, list[Any]] = {}

    for parent in piece:
        tag = _strip_namespace(parent.tag)
        if tag == "Points":
            for data_array in parent:
                if _strip_namespace(data_array.tag) == "DataArray":
                    saw_binary = saw_binary or (data_array.attrib.get("format", "ascii").lower() == "binary")
                    values = [float(v) for v in _parse_data_array(data_array, byte_order, header_type)]
                    components = int(data_array.attrib.get("NumberOfComponents", "3"))
                    points = [values[i : i + components] for i in range(0, len(values), components)]
        elif tag == "Cells":
            for data_array in parent:
                name = data_array.attrib.get("Name", "")
                if name in cells:
                    saw_binary = saw_binary or (data_array.attrib.get("format", "ascii").lower() == "binary")
                    cells[name] = _parse_data_array(data_array, byte_order, header_type)
        elif tag == "PointData":
            for data_array in parent:
                if _strip_namespace(data_array.tag) == "DataArray":
                    name = data_array.attrib.get("Name") or f"field_{len(point_data) + 1}"
                    saw_binary = saw_binary or (data_array.attrib.get("format", "ascii").lower() == "binary")
                    point_data[name] = _parse_data_array(data_array, byte_order, header_type)
        elif tag == "CellData":
            for data_array in parent:
                if _strip_namespace(data_array.tag) == "DataArray":
                    name = data_array.attrib.get("Name") or f"field_{len(cell_data) + 1}"
                    saw_binary = saw_binary or (data_array.attrib.get("format", "ascii").lower() == "binary")
                    cell_data[name] = _parse_data_array(data_array, byte_order, header_type)

    detected_fields = list(point_data.keys()) + list(cell_data.keys())
    blocks = _build_cell_blocks(cells, cell_data)
    return {
        "points": points,
        "cells": cells,
        "cellBlocks": blocks,
        "pointData": point_data,
        "cellData": cell_data,
        "detectedFields": detected_fields,
        "suggestedMapping": _suggest_mapping(detected_fields),
        "bounds": _bounds(points),
        "summary": {"pointCount": len(points), "cellCount": len(blocks), "cellTypes": sorted({b["cellType"] for b in blocks})},
        "warnings": ["VTU 已按轻量 XML 解析（支持 ASCII 与 inline base64 binary DataArray；appended/压缩 VTU 建议安装 meshio）。" if saw_binary else "VTU 已按 ASCII XML 解析；二进制/压缩 VTU 可在安装 meshio 后自动增强解析。"],
    }


def _parse_with_meshio(content: bytes) -> dict[str, Any]:
    import meshio  # type: ignore

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "input.vtu"
        path.write_bytes(content)
        mesh = meshio.read(path)
    points = mesh.points.tolist()
    cells = {"connectivity": [], "offsets": [], "types": []}
    cell_blocks: list[dict[str, Any]] = []
    offset = 0
    cell_index = 0
    vtk_type_reverse = {v: k for k, v in VTK_CELL_TYPES.items()}
    for block in mesh.cells:
        vtk_type = vtk_type_reverse.get(block.type, None)
        for nodes in block.data.tolist():
            cells["connectivity"].extend(nodes)
            offset += len(nodes)
            cells["offsets"].append(offset)
            cells["types"].append(vtk_type or -1)
            attrs: dict[str, Any] = {}
            for name, values_list in mesh.cell_data.items():
                # meshio stores data by cell block; flattening keeps the information available enough for MVP interaction.
                flat = []
                for arr in values_list:
                    flat.extend(arr.tolist())
                if cell_index < len(flat):
                    attrs[name] = flat[cell_index]
            cell_blocks.append({"index": cell_index, "vtkType": vtk_type, "cellType": block.type, "nodes": nodes, "attributes": attrs})
            cell_index += 1
    point_data = {name: values.tolist() for name, values in mesh.point_data.items()}
    cell_data_flat: dict[str, list[Any]] = {}
    for name, values_list in mesh.cell_data.items():
        cell_data_flat[name] = []
        for arr in values_list:
            cell_data_flat[name].extend(arr.tolist())
    fields = list(point_data.keys()) + list(cell_data_flat.keys())
    return {
        "points": points,
        "cells": cells,
        "cellBlocks": cell_blocks,
        "pointData": point_data,
        "cellData": cell_data_flat,
        "detectedFields": fields,
        "suggestedMapping": _suggest_mapping(fields),
        "bounds": _bounds(points),
        "summary": {"pointCount": len(points), "cellCount": len(cell_blocks), "cellTypes": sorted({b["cellType"] for b in cell_blocks})},
        "warnings": ["VTU 已通过 meshio 解析，支持 ASCII/binary/appended 多种 VTU 编码。"],
    }


def parse_vtu(content: bytes) -> dict[str, Any]:
    if importlib.util.find_spec("meshio") is not None:
        try:
            return _parse_with_meshio(content)
        except Exception as exc:  # fall back to XML path for clearer validation errors
            fallback = _parse_vtu_ascii_xml(content)
            fallback["warnings"].append(f"meshio 解析失败，已回退到 ASCII XML 解析：{exc}")
            return fallback
    return _parse_vtu_ascii_xml(content)
