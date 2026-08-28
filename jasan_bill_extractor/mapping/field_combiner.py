"""
mapping/field_combiner.py
---------------------------------
FieldCombiner (spec.md §1.1): 연체료·미납액·미납연체료를 전력기금 원본값과 합산해
엑셀 W열(전력기금) 기입값을 만든다.

    W(전력기금, 엑셀 기입값) = 전력기금_원본 + 연체료 + 미납액 + 미납연체료

원본 4개 값은 그대로 보존해서 audit.logger가 별도 로그로 남기고 (§1.1), 이 함수는
합산값 하나만 반환한다. null은 0으로 취급한다.
"""

from dataclasses import dataclass


@dataclass
class CombinedPowerFund:
    combined_value: float
    power_fund_raw: float
    late_fee: float
    unpaid_amount: float
    unpaid_late_fee: float


def combine_power_fund(doc: dict) -> CombinedPowerFund:
    power_fund_raw = doc.get("power_fund_raw") or 0
    late_fee = doc.get("late_fee") or 0
    unpaid_amount = doc.get("unpaid_amount") or 0
    unpaid_late_fee = doc.get("unpaid_late_fee") or 0
    combined = power_fund_raw + late_fee + unpaid_amount + unpaid_late_fee
    return CombinedPowerFund(
        combined_value=combined,
        power_fund_raw=power_fund_raw,
        late_fee=late_fee,
        unpaid_amount=unpaid_amount,
        unpaid_late_fee=unpaid_late_fee,
    )
