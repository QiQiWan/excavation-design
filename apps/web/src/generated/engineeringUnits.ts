// Generated from packages/units/engineering-units.json. Do not edit manually.
export const UNIT_SYSTEM = "SI-engineering" as const;
export const UNIT_RULES = {
  "tableHeaderPreferred": true,
  "inlineUnitForKeyMetrics": true,
  "rawUnitlessNumberForbiddenForEngineeringQuantity": true,
  "missingValueSymbol": "—"
} as const;
export const UNIT_REGISTRY = {
  "length": {
    "symbol": "m",
    "quantity": "长度",
    "decimals": 3
  },
  "elevation": {
    "symbol": "m",
    "quantity": "标高",
    "decimals": 3
  },
  "displacement": {
    "symbol": "mm",
    "quantity": "位移",
    "decimals": 2
  },
  "force": {
    "symbol": "kN",
    "quantity": "力/轴力",
    "decimals": 1
  },
  "line_force": {
    "symbol": "kN/m",
    "quantity": "线荷载/剪力",
    "decimals": 2
  },
  "moment": {
    "symbol": "kN·m",
    "quantity": "弯矩",
    "decimals": 2
  },
  "wall_moment": {
    "symbol": "kN·m/m",
    "quantity": "墙体单位宽度弯矩",
    "decimals": 2
  },
  "pressure": {
    "symbol": "kPa",
    "quantity": "压力",
    "decimals": 2
  },
  "stress": {
    "symbol": "MPa",
    "quantity": "应力/强度",
    "decimals": 3
  },
  "stiffness": {
    "symbol": "kN/m",
    "quantity": "平动刚度",
    "decimals": 1
  },
  "rotational_stiffness": {
    "symbol": "kN·m/rad",
    "quantity": "转动刚度",
    "decimals": 1
  },
  "rotation": {
    "symbol": "mrad",
    "quantity": "转角",
    "decimals": 3
  },
  "temperature": {
    "symbol": "°C",
    "quantity": "温度",
    "decimals": 1
  },
  "weight": {
    "symbol": "t",
    "quantity": "质量",
    "decimals": 2
  },
  "ground_pressure": {
    "symbol": "kPa",
    "quantity": "地基接地压力",
    "decimals": 1
  },
  "ratio": {
    "symbol": "—",
    "quantity": "比值",
    "decimals": 3
  },
  "count": {
    "symbol": "项",
    "quantity": "数量",
    "decimals": 0
  },
  "lineForce": {
    "symbol": "kN/m",
    "quantity": "线荷载/剪力",
    "decimals": 2
  },
  "wallMoment": {
    "symbol": "kN·m/m",
    "quantity": "墙体单位宽度弯矩",
    "decimals": 2
  },
  "rotationalStiffness": {
    "symbol": "kN·m/rad",
    "quantity": "转动刚度",
    "decimals": 1
  },
  "groundPressure": {
    "symbol": "kPa",
    "quantity": "地基接地压力",
    "decimals": 1
  }
} as const;
export const UNIT_FIELD_RULES = [
  {
    "pattern": "(elevation|level|标高)$",
    "unitKey": "elevation"
  },
  {
    "pattern": "(displacement|deflection|settlement|slip|位移|沉降)",
    "unitKey": "displacement"
  },
  {
    "pattern": "(axialForce|forceKn|force|轴力|反力)",
    "unitKey": "force"
  },
  {
    "pattern": "(wallMoment|momentPerM)",
    "unitKey": "wall_moment"
  },
  {
    "pattern": "(moment|弯矩)",
    "unitKey": "moment"
  },
  {
    "pattern": "(shearPerM|lineForce|线荷载|剪力)",
    "unitKey": "line_force"
  },
  {
    "pattern": "(stiffness|spring|刚度)",
    "unitKey": "stiffness"
  },
  {
    "pattern": "(pressureKpa|groundPressure|pressure|压力)",
    "unitKey": "pressure"
  },
  {
    "pattern": "(stressMpa|strengthMpa|stress|应力|强度)",
    "unitKey": "stress"
  },
  {
    "pattern": "(temperature|温度)",
    "unitKey": "temperature"
  },
  {
    "pattern": "(weightT|massT|重量|质量)",
    "unitKey": "weight"
  },
  {
    "pattern": "(length|width|height|depth|span|radius|clearance|offset|xM|yM|长度|宽度|高度|深度|跨度|半径|净距)",
    "unitKey": "length"
  },
  {
    "pattern": "(ratio|utilization|score|比值|利用率)",
    "unitKey": "ratio"
  },
  {
    "pattern": "(count|数量)$",
    "unitKey": "count"
  }
] as const;
export type GeneratedUnitKey = "length" | "elevation" | "displacement" | "force" | "line_force" | "moment" | "wall_moment" | "pressure" | "stress" | "stiffness" | "rotational_stiffness" | "rotation" | "temperature" | "weight" | "ground_pressure" | "ratio" | "count" | "lineForce" | "wallMoment" | "rotationalStiffness" | "groundPressure";
