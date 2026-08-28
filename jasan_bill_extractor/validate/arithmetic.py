"""
validate/arithmetic.py
---------------------------------
ArithmeticValidator (spec.md §5.2): 추출된 금액들이 서로 산술적으로 맞는지 검증한다.

사람 검수 결과(poc_out/poc_results_review.csv) 확인된 사실: 청구서 계열에 따라
검증 산식이 다르다.
  - 관리비고지서 계열: current_period_charge(당월부과액) + unpaid_amount + unpaid_late_fee
    = due_total_amount ,  current_period_charge + late_fee = overdue_total_amount
  - 세금계산서 계열: supply_amount + vat_amount + power_fund_raw + unpaid_amount
    + unpaid_late_fee + other_fee = due_total_amount

합산 전 원본 값 기준으로 검증한다(전력기금 등 W열 합산은 mapping.field_combiner의 몫).
"""

from dataclasses import dataclass, field
from typing import Optional

TOLERANCE_WON = 10  # 원단위 절사 등 허용오차


def _sum_known(parts) -> float:
    return sum(p for p in parts if isinstance(p, (int, float)))


@dataclass
class ArithmeticResult:
    passed: bool
    formula_used: Optional[str]  # "current_period_charge" | "supply_amount" | None
    diff: Optional[float] = None
    detail: str = ""
    checks_tried: list = field(default_factory=list)  # [(label, diff), ...]

    @property
    def status_label(self) -> str:
        if self.formula_used is None and self.diff is None:
            return "N/A" if not self.checks_tried else "N/A(비교불가)"
        if self.passed:
            return f"OK({self.formula_used})"
        return f"불일치({self.formula_used},차이 {self.diff:+.0f}원)"


def validate_due_total(doc: dict) -> ArithmeticResult:
    """납기내금액(due_total_amount) 기준 산술 검증. 두 산식을 모두 시도해 하나라도
    맞으면 통과 처리한다."""
    total = doc.get("due_total_amount")
    if total is None:
        return ArithmeticResult(passed=False, formula_used=None, detail="총액없음")

    checks = []

    cpc = doc.get("current_period_charge")
    if cpc is not None:
        parts = [cpc, doc.get("unpaid_amount"), doc.get("unpaid_late_fee")]
        diff = round(total - _sum_known(parts), 2)
        checks.append(("당월부과액기준", diff))

    parts_b = [
        doc.get("supply_amount"),
        doc.get("vat_amount"),
        doc.get("power_fund_raw"),
        doc.get("unpaid_amount"),
        doc.get("unpaid_late_fee"),
        doc.get("other_fee"),
    ]
    if any(p is not None for p in parts_b):
        diff_b = round(total - _sum_known(parts_b), 2)
        checks.append(("공급가액기준", diff_b))

    if not checks:
        return ArithmeticResult(passed=False, formula_used=None, checks_tried=checks, detail="비교 가능한 필드 없음")

    for label, diff in checks:
        if abs(diff) <= TOLERANCE_WON:
            return ArithmeticResult(passed=True, formula_used=label, diff=diff, checks_tried=checks)

    label, diff = min(checks, key=lambda x: abs(x[1]))
    return ArithmeticResult(passed=False, formula_used=label, diff=diff, checks_tried=checks)


def validate_overdue_total(doc: dict) -> ArithmeticResult:
    """납기후금액(overdue_total_amount) 기준 검증 (참고용, 있는 경우만).
    current_period_charge + late_fee = overdue_total_amount
    """
    total = doc.get("overdue_total_amount")
    if total is None:
        return ArithmeticResult(passed=False, formula_used=None, detail="납기후금액 없음(참고용, 필수 아님)")

    cpc = doc.get("current_period_charge")
    if cpc is None:
        # 세금계산서 계열은 late_fee만으로 due_total_amount에서 파생
        due = doc.get("due_total_amount")
        late_fee = doc.get("late_fee")
        if due is None or late_fee is None:
            return ArithmeticResult(passed=False, formula_used=None, detail="비교 가능한 필드 없음")
        diff = round(total - (due + late_fee), 2)
        passed = abs(diff) <= TOLERANCE_WON
        return ArithmeticResult(passed=passed, formula_used="납기내금액+연체료기준", diff=diff)

    late_fee = doc.get("late_fee") or 0
    diff = round(total - (cpc + late_fee), 2)
    passed = abs(diff) <= TOLERANCE_WON
    return ArithmeticResult(passed=passed, formula_used="당월부과액기준", diff=diff)
