from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ConcreteDesignValues:
    grade: str
    fc: float  # MPa = N/mm2
    ft: float  # MPa = N/mm2
    ec: float  # MPa = N/mm2
    alpha1: float = 1.0


@dataclass(frozen=True)
class SteelDesignValues:
    grade: str
    fy: float  # MPa = N/mm2
    fyv: float  # MPa = N/mm2
    elastic_modulus: float = 200000.0  # MPa


# Common GB 50010 design strengths for ordinary concrete grades used by this prototype.
# The rules engine keeps these values centralized so that a licensed engineering team can
# audit or replace them against the project-specific standard edition.
_CONCRETE: dict[str, ConcreteDesignValues] = {
    "C25": ConcreteDesignValues("C25", fc=11.9, ft=1.27, ec=28000),
    "C30": ConcreteDesignValues("C30", fc=14.3, ft=1.43, ec=30000),
    "C35": ConcreteDesignValues("C35", fc=16.7, ft=1.57, ec=31500),
    "C40": ConcreteDesignValues("C40", fc=19.1, ft=1.71, ec=32500),
    "C45": ConcreteDesignValues("C45", fc=21.1, ft=1.80, ec=33500),
    "C50": ConcreteDesignValues("C50", fc=23.1, ft=1.89, ec=34500),
    "C55": ConcreteDesignValues("C55", fc=25.3, ft=1.96, ec=35500),
    "C60": ConcreteDesignValues("C60", fc=27.5, ft=2.04, ec=36000),
}

_STEEL: dict[str, SteelDesignValues] = {
    "HPB300": SteelDesignValues("HPB300", fy=270, fyv=270),
    "HRB335": SteelDesignValues("HRB335", fy=300, fyv=300),
    "HRB400": SteelDesignValues("HRB400", fy=360, fyv=360),
    "HRBF400": SteelDesignValues("HRBF400", fy=360, fyv=360),
    "HRB500": SteelDesignValues("HRB500", fy=435, fyv=435),
    "HRBF500": SteelDesignValues("HRBF500", fy=435, fyv=435),
}


def normalize_grade(value: str | None, default: str) -> str:
    if not value:
        return default
    value = value.strip().upper().replace(" ", "")
    match = re.search(r"C\d+", value)
    if match:
        return match.group(0)
    return value


def concrete_values(grade: str | None = "C35") -> ConcreteDesignValues:
    key = normalize_grade(grade, "C35")
    return _CONCRETE.get(key, _CONCRETE["C35"])


def steel_values(grade: str | None = "HRB400") -> SteelDesignValues:
    key = (grade or "HRB400").strip().upper().replace(" ", "")
    return _STEEL.get(key, _STEEL["HRB400"])


def bar_area(diameter_mm: float) -> float:
    return 3.141592653589793 * diameter_mm * diameter_mm / 4.0


def bars_area_per_m(diameter_mm: float, spacing_mm: float) -> float:
    if spacing_mm <= 0:
        raise ValueError("spacing_mm must be positive")
    return bar_area(diameter_mm) * 1000.0 / spacing_mm
