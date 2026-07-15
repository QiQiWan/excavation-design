from __future__ import annotations

from app.rules.enterprise.preliminary_design_rules import SUPPORT_LEVEL_RULE, WALL_THICKNESS_RULE
from app.rules.gb50007.foundation_rules import GB50007_BEARING_RULE
from app.rules.gb50009.load_combinations import GB50009_BASIC_COMBINATION_RULE
from app.rules.gb50010.rc_section_rules import GB50010_AXIAL_RULE, GB50010_FLEXURE_RULE, GB50010_SHEAR_RULE
from app.rules.gb50010.reinforcement_rules import MIN_REINFORCEMENT_RULE
from app.rules.gb50017.steel_support_rules import GB50017_STEEL_COMPRESSION_RULE
from app.rules.gb55003_2021.foundation_general_rules import FOUNDATION_GENERAL_SAFETY_RULE
from app.rules.gb55008_2021.concrete_general_rules import CONCRETE_GENERAL_RULE
from app.rules.jgj120_2012.retaining_wall_rules import JGJ120_DIAPHRAGM_CONSTRUCTION_RULE, JGJ120_EMBEDMENT_RULE
from app.rules.jgj120_2012.stability_rules import JGJ120_DEFORMATION_RULE, JGJ120_HEAVE_RULE, JGJ120_WATER_RULE


def list_rules() -> list[dict]:
    """Return the rule catalogue exposed by /api/rules.

    The catalogue intentionally labels partial implementations as screening subsets.  It
    avoids suggesting that the prototype performs full formal code review.
    """
    rules = [
        WALL_THICKNESS_RULE,
        SUPPORT_LEVEL_RULE,
        JGJ120_EMBEDMENT_RULE,
        JGJ120_DEFORMATION_RULE,
        JGJ120_WATER_RULE,
        JGJ120_HEAVE_RULE,
        JGJ120_DIAPHRAGM_CONSTRUCTION_RULE,
        GB50010_FLEXURE_RULE,
        GB50010_SHEAR_RULE,
        GB50010_AXIAL_RULE,
        MIN_REINFORCEMENT_RULE,
        GB50009_BASIC_COMBINATION_RULE,
        GB50007_BEARING_RULE,
        GB50017_STEEL_COMPRESSION_RULE,
        FOUNDATION_GENERAL_SAFETY_RULE,
        CONCRETE_GENERAL_RULE,
    ]
    return [rule.model_dump(mode="json", by_alias=True) for rule in rules]
