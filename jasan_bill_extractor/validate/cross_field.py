"""
validate/cross_field.py
---------------------------------
CrossFieldValidator (spec.md §5.1): 같은 페이지 안에서 동일 금액이 여러 구역
(관리비고지서 본문 / 납입통지서 / 수납의뢰서 / 납입영수증)에 반복 인쇄된 경우,
각 구역에서 개별적으로 읽은 값(extract.schema의 region_readings)이 서로 일치하는지
비교한다.

문서 하나에 region_readings가 2개 미만이면 비교할 대상이 없으므로 "N/A"로 처리한다.
"""

from dataclasses import dataclass, field

TOLERANCE_WON = 10

COMPARABLE_FIELDS = ["current_period_charge", "due_total_amount", "overdue_total_amount"]


@dataclass
class CrossFieldResult:
    status: str  # "OK" | "불일치" | "N/A"
    mismatches: list = field(default_factory=list)  # [(field, region_a, val_a, region_b, val_b), ...]
    regions_compared: int = 0


def validate_regions(doc: dict) -> CrossFieldResult:
    readings = doc.get("region_readings") or []
    readings = [r for r in readings if isinstance(r, dict)]
    if len(readings) < 2:
        return CrossFieldResult(status="N/A", regions_compared=len(readings))

    mismatches = []
    for f in COMPARABLE_FIELDS:
        values = [(r.get("region", "?"), r.get(f)) for r in readings if r.get(f) is not None]
        if len(values) < 2:
            continue
        base_region, base_val = values[0]
        for region, val in values[1:]:
            if abs(val - base_val) > TOLERANCE_WON:
                mismatches.append((f, base_region, base_val, region, val))

    status = "불일치" if mismatches else "OK"
    return CrossFieldResult(status=status, mismatches=mismatches, regions_compared=len(readings))
