from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConcreteDesignStrength:
    grade: str
    fc: float  # MPa = N/mm2
    ft: float  # MPa = N/mm2
    elastic_modulus: float  # MPa


@dataclass(frozen=True)
class RebarDesignStrength:
    grade: str
    fy: float  # MPa
    fy_compression: float  # MPa
    elastic_modulus: float  # MPa


# Common design values used in GB 50010-2010/2015 practice and the 2024 GB/T reference.
CONCRETE: dict[str, ConcreteDesignStrength] = {
    "C25": ConcreteDesignStrength("C25", fc=11.9, ft=1.27, elastic_modulus=28000.0),
    "C30": ConcreteDesignStrength("C30", fc=14.3, ft=1.43, elastic_modulus=30000.0),
    "C35": ConcreteDesignStrength("C35", fc=16.7, ft=1.57, elastic_modulus=31500.0),
    "C40": ConcreteDesignStrength("C40", fc=19.1, ft=1.71, elastic_modulus=32500.0),
    "C45": ConcreteDesignStrength("C45", fc=21.1, ft=1.80, elastic_modulus=33500.0),
    "C50": ConcreteDesignStrength("C50", fc=23.1, ft=1.89, elastic_modulus=34500.0),
}

REBAR: dict[str, RebarDesignStrength] = {
    "HPB300": RebarDesignStrength("HPB300", fy=270.0, fy_compression=270.0, elastic_modulus=210000.0),
    "HRB335": RebarDesignStrength("HRB335", fy=300.0, fy_compression=300.0, elastic_modulus=200000.0),
    "HRB400": RebarDesignStrength("HRB400", fy=360.0, fy_compression=360.0, elastic_modulus=200000.0),
    "HRBF400": RebarDesignStrength("HRBF400", fy=360.0, fy_compression=360.0, elastic_modulus=200000.0),
    "HRB500": RebarDesignStrength("HRB500", fy=435.0, fy_compression=410.0, elastic_modulus=200000.0),
    "HRBF500": RebarDesignStrength("HRBF500", fy=435.0, fy_compression=410.0, elastic_modulus=200000.0),
}


def _clean(value: str | None, fallback: str) -> str:
    return (value or fallback).upper().replace(" ", "")


def concrete_strength(grade: str | None) -> ConcreteDesignStrength:
    return CONCRETE.get(_clean(grade, "C35"), CONCRETE["C35"])


def rebar_strength(grade: str | None) -> RebarDesignStrength:
    return REBAR.get(_clean(grade, "HRB400"), REBAR["HRB400"])


def concrete_elastic_modulus_mpa(grade: str | None) -> float:
    return concrete_strength(grade).elastic_modulus


def concrete_unit_weight(grade: str = "C35") -> float:
    return 25.0

# Backward-compatible aliases for earlier modules.
ConcreteStrength = ConcreteDesignStrength
RebarStrength = RebarDesignStrength
CONCRETE_STRENGTHS = CONCRETE
REBAR_STRENGTHS = REBAR
