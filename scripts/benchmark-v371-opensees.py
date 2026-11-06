#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "services" / "api"))
from app.calculation.opensees_benchmark import write_benchmark_certificate

result = write_benchmark_certificate(root / "packages" / "benchmarks" / "v371_opensees_certificate.json")
print(result["status"], result["maximumRelativeDisplacementError"])
raise SystemExit(0 if result["status"] == "pass" else 1)
