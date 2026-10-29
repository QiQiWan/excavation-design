"""Frozen, unit-consistent stage actions for the bounded wall/soil model.

This is an explicit action combination, not a certification of a normative
design method. The source soil strengths must be effective-stress parameters.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

from app.calculation.earth_pressure import calculate_lateral_pressure_profile, earth_pressure_coefficients
from app.schemas.domain import DesignSettings, GeologicalLayer, PressurePoint, PressureProfile


CONTRACT_VERSION = "stage-load-effective-stress-v1"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StageLoadRecord:
    """Immutable serialized snapshots; exported profiles/evidence are fresh copies.

    Keeping JSON as the stored representation also prevents callers mutating a
    nested Pydantic point after the load ID has been assigned.
    """
    load_id: str
    input_hash: str
    evidence_json: str
    _profiles_json: str

    def _profile(self, name: str) -> PressureProfile:
        return PressureProfile.model_validate(json.loads(self._profiles_json)[name])

    @property
    def standard_profile(self) -> PressureProfile:
        return self._profile("standard")

    @property
    def design_profile(self) -> PressureProfile:
        return self._profile("design")

    @property
    def permanent_profile(self) -> PressureProfile:
        """Zero-surcharge net service load (not the design factoring operand)."""
        return self._profile("permanent")

    @property
    def permanent_action_profile(self) -> PressureProfile:
        """Outside zero-surcharge soil plus water, before inside resistance."""
        return self._profile("permanent_action")

    @property
    def resistance_profile(self) -> PressureProfile:
        """Inside passive soil plus inside water, positive resistance magnitude."""
        return self._profile("resistance")

    @property
    def surcharge_profile(self) -> PressureProfile:
        """Unclipped K*q upper bound on the surcharge increment, kPa."""
        return self._profile("surcharge")

    @property
    def evidence(self) -> dict[str, Any]:
        return json.loads(self.evidence_json)


def build_stage_loads(
    soil_profile: list[GeologicalLayer],
    excavation_depth: float,
    groundwater_level_outside: float | None,
    groundwater_level_inside: float | None,
    surcharge: float,
    top_elevation: float,
    calculation_depth: float,
    settings: DesignSettings,
    step: float = 0.5,
) -> StageLoadRecord:
    """Build standard/deformation and factored/design pressure snapshots.

    A_G = max(K*sigma'_G - 2*c*sqrt(K), 0) + u_out (at-rest omits c).
    R = passive_soil + u_in; Q = K*q. Design pressure is
    max(0, importance*(gamma_G*A_G + gamma_Q*psi*Q - R)).

    The favourable resistance factor is 1.0: gamma_G never amplifies R.
    Importance multiplies the whole resultant, including the resistance term.
    Q deliberately bypasses active-tension/net-pressure clipping: since
    max(x+K*q,0) <= max(x,0)+K*q for q>=0, it bounds the actual surcharge
    increment. Standard uses the direct full-q pressure; therefore G+Q is
    generally an envelope, not an exact decomposition near the cutoffs.
    """
    factor_names = ("load_gamma_g", "load_gamma_q", "load_psi", "importance_factor")
    factors = {name: float(getattr(settings, name)) for name in factor_names}
    for name, value in factors.items():
        if not math.isfinite(value) or (value < 0 if name == "load_psi" else value <= 0):
            raise ValueError(f"{name} must be finite and {'nonnegative' if name == 'load_psi' else 'positive'}")
    if factors["load_psi"] > 1:
        raise ValueError("load_psi must be in [0, 1]")
    if settings.pressure_method not in {"active", "at_rest"}:
        raise ValueError(f"Unsupported pressure_method: {settings.pressure_method}")
    arguments = dict(soil_profile=soil_profile, excavation_depth=excavation_depth,
        groundwater_level=groundwater_level_outside, groundwater_level_inside=groundwater_level_inside,
        top_elevation=top_elevation, calculation_depth=calculation_depth, step=step,
        mode=settings.pressure_method, water_soil_method=settings.water_soil_method)
    standard = calculate_lateral_pressure_profile(surcharge=surcharge, **arguments)
    permanent = calculate_lateral_pressure_profile(surcharge=0.0, **arguments)
    gamma_g = factors["load_gamma_g"]
    gamma_q_psi = factors["load_gamma_q"] * factors["load_psi"]
    importance = factors["importance_factor"]
    design_points: list[PressurePoint] = []
    action_points: list[PressurePoint] = []
    resistance_points: list[PressurePoint] = []
    surcharge_points: list[PressurePoint] = []
    component_evidence: list[dict[str, float]] = []
    for p in permanent.points:
        active_soil = p.active_earth_pressure or 0.0
        passive_soil = p.passive_earth_pressure or 0.0
        outside_water = p.outside_water_pressure or 0.0
        inside_water = p.inside_water_pressure or 0.0
        # Recompute K without the display rounding retained by PressurePoint.
        phi = p.friction_angle if p.friction_angle is not None else 30.0
        ka, _, _ = earth_pressure_coefficients(phi)
        source_layer = next((layer for layer in soil_profile if layer.top_elevation + 1e-9 >= p.elevation >= layer.bottom_elevation - 1e-9), soil_profile[-1] if soil_profile else None)
        explicit_k0 = source_layer.parameters.k0 if source_layer else None
        _, _, k0 = earth_pressure_coefficients(phi, explicit_k0)
        variable = (k0 if settings.pressure_method == "at_rest" else ka) * surcharge
        action = active_soil + outside_water
        resistance = passive_soil + inside_water
        design_soil = importance * (gamma_g*active_soil + gamma_q_psi*variable - passive_soil)
        design_water = importance * (gamma_g*outside_water - inside_water)
        design_points.append(p.model_copy(update=dict(
            earth_pressure=round(design_soil, 6), water_pressure=round(design_water, 6),
            total_pressure=round(max(0.0, design_soil+design_water), 6),
            active_earth_pressure=round(importance*(gamma_g*active_soil+gamma_q_psi*variable), 6),
            passive_earth_pressure=round(importance*passive_soil, 6),
            outside_water_pressure=round(importance*gamma_g*outside_water, 6),
            inside_water_pressure=round(importance*inside_water, 6),
            method="Factored effective-stress actions; unamplified inside resistance before importance")))
        action_points.append(p.model_copy(update=dict(earth_pressure=active_soil,
            water_pressure=outside_water, total_pressure=round(action, 6),
            passive_earth_pressure=0.0, inside_water_pressure=0.0, method="Permanent outside soil/water action")))
        resistance_points.append(p.model_copy(update=dict(earth_pressure=passive_soil,
            water_pressure=inside_water, total_pressure=round(resistance, 6),
            active_earth_pressure=0.0, outside_water_pressure=0.0, method="Inside passive soil/water resistance magnitude")))
        surcharge_points.append(p.model_copy(update=dict(earth_pressure=round(variable, 6),
            water_pressure=0.0, total_pressure=round(variable, 6), active_earth_pressure=round(variable, 6),
            passive_earth_pressure=0.0, outside_water_pressure=0.0, inside_water_pressure=0.0,
            vertical_stress_total=surcharge, vertical_stress_effective=surcharge,
            cohesion=0.0, method="Conservative unclipped lateral surcharge increment K*q")))
        component_evidence.append(dict(depth=p.depth, permanent_outside_action_kpa=action,
            inside_resistance_kpa=resistance, surcharge_action_kpa=variable,
            factored_permanent_outside_action_kpa=importance*gamma_g*action,
            factored_inside_resistance_kpa=importance*resistance,
            factored_surcharge_action_kpa=importance*gamma_q_psi*variable))
    limitation = "Surcharge K*q is an upper bound across active tension/passive net clipping; standard pressure is calculated directly."
    design = PressureProfile(points=design_points, method=CONTRACT_VERSION + " factored pressure combination",
        warnings=list(standard.warnings) + [limitation])
    profiles = {"standard":standard, "design":design, "permanent":permanent,
        "permanent_action":PressureProfile(points=action_points, method="Permanent outside soil/water action"),
        "resistance":PressureProfile(points=resistance_points, method="Inside passive soil/water resistance"),
        "surcharge":PressureProfile(points=surcharge_points, method="Unclipped K*q surcharge upper bound")}
    units = dict(pressure="kPa", depth="m", elevation="m", unit_weight="kN/m3", friction_angle="degree", factors="dimensionless")
    source = dict(soil_profile=[layer.model_dump(mode="json") for layer in soil_profile],
        excavation_depth=float(excavation_depth), groundwater_level_outside=groundwater_level_outside,
        groundwater_level_inside=groundwater_level_inside, surcharge=float(surcharge),
        top_elevation=float(top_elevation), calculation_depth=float(calculation_depth), step=float(step))
    methods = dict(pressure=settings.pressure_method, water_soil=settings.water_soil_method,
        supported="Rankine active or at-rest outside; effective-stress soil and hydrostatic water; Rankine passive inside",
        scope="bounded screening model; not normative certification")
    provenance = {"stage_fields":"excavation_depth, groundwater levels and surcharge supplied by caller",
        "soil_parameters":"caller geological profile; effective-stress interpretation required",
        "settings":"project DesignSettings selected pressure method and combination factors",
        "source_document_identity":"not represented in GeologicalLayer; no document verification inferred",
        "defaults":"missing c=0 kPa, phi=30 degrees, gamma=18 kN/m3; missing saturated gamma uses natural gamma; missing k0 uses Jaky"}
    identity = dict(contract_version=CONTRACT_VERSION, inputs=source, factors=factors,
                    methods=methods, units=units, provenance=provenance)
    input_hash = _hash(identity)
    serialized_profiles = {name:p.model_dump(mode="json") for name,p in profiles.items()}
    profile_ids = {name:"pressure-" + _hash(dict(input_hash=input_hash, kind=name, profile=p)) for name,p in serialized_profiles.items()}
    load_id = "stage-load-" + _hash(dict(input_hash=input_hash, profile_ids=profile_ids))
    evidence = dict(identity, input_hash=input_hash, load_id=load_id, profile_ids=profile_ids,
        decomposition=dict(formula="max(0, importance_factor*(gamma_g*A_G + gamma_q*psi*K*q - R))",
            permanent_action="A_G = zero-surcharge outside soil pressure + outside water",
            resistance="R = inside passive soil pressure + inside water; resistance factor=1.0 before importance",
            importance="multiplies the complete net action including resistance",
            standard_equals_component_sum=False, limitation=limitation,
            net_load_boundary="negative net pressure clipped to zero; reverse loading unsupported",
            stress_fields="design vertical stresses remain characteristic source geostatic stresses; lateral fields are factored actions"),
        components=component_evidence, warnings=design.warnings)
    return StageLoadRecord(load_id=load_id, input_hash=input_hash, evidence_json=_json(evidence),
                           _profiles_json=_json(serialized_profiles))
