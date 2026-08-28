"""
extract/schema.py
---------------------------------
Claude Vision 구조화 출력(tool use)에 사용할 JSON 스키마 정의.

한 이미지(페이지) 안에 청구서가 여러 건 있을 수 있으므로(예: 세금계산서 2건이
위아래로 붙어있는 경우), 최상위는 항상 documents 배열이다.

필드 이름은 spec.md §4의 ExtractedBill / §1.5 설계와 맞춰져 있다.
- supply_amount        -> 엑셀 T열(전기요금/공급가액)
- vat_amount            -> 엑셀 U열(부가가치세)
- power_fund_raw        -> 전력기금 원본값 (엑셀 W열에 합산되기 전 값)
- late_fee / unpaid_amount / unpaid_late_fee
    -> spec.md §1.1에 따라 최종적으로 power_fund_raw와 합산되어 W열에 기입됨
       (합산은 mapping.field_combiner에서 수행, 여기서는 원본값만 추출)
- due_total_amount      -> '납기내금액' (담당자 확정: 검증 기준 총액)
- overdue_total_amount  -> '납기후금액' (참고용, 있으면 기록)
"""

EXTRACT_TOOL_NAME = "record_extracted_bills"

EXTRACT_TOOL_SCHEMA = {
    "name": EXTRACT_TOOL_NAME,
    "description": (
        "이미지에서 인식한 청구서(세금계산서/계산서/관리비고지서/수기영수증) 정보를 "
        "documents 배열로 기록한다. 한 이미지에 청구서가 여러 건 있으면 배열 원소도 여러 개다. "
        "값을 읽을 수 없으면 null로 남긴다(추측해서 채우지 않는다)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "documents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "doc_type": {
                            "type": "string",
                            "enum": [
                                "세금계산서",
                                "계산서",
                                "관리비고지서",
                                "수기영수증",
                                "기타",
                            ],
                            "description": "이 문서의 유형",
                        },
                        "site_hint_text": {
                            "type": ["string", "null"],
                            "description": "사업장 매칭 보조용 상호명/건물명 원문 (주소는 절대 포함하지 말 것)",
                        },
                        "billing_period": {
                            "type": ["string", "null"],
                            "description": "청구년월 (예: 202606)",
                        },
                        "supply_amount": {
                            "type": ["number", "null"],
                            "description": "공급가액 (세금계산서: '공급가액' 칸 / 관리비고지서: 전기 사용요금)",
                        },
                        "vat_amount": {
                            "type": ["number", "null"],
                            "description": "부가가치세 (세금계산서: '세액' 칸 / 관리비고지서: 부가세)",
                        },
                        "power_fund_raw": {
                            "type": ["number", "null"],
                            "description": "전력기금 (고지서에 '전력기금'으로 별도 표기된 값만, 없으면 null)",
                        },
                        "current_period_charge": {
                            "type": ["number", "null"],
                            "description": (
                                "당월부과액(당월부과액계/당월관리비) - 관리비고지서에서 여러 관리비 "
                                "세부항목(공동전기료/전기기본요금/공동수도료 등)을 다 더한 당월 합계 "
                                "금액. 세부항목을 일일이 찾으려 하지 말고 고지서에 인쇄된 이 합계 "
                                "라벨의 값을 그대로 읽으세요. 이 값 + 미납액 + 미납연체료 = 납기내금액, "
                                "이 값 + 납기후연체료 = 납기후금액 이 되는 것이 정상입니다. "
                                "세금계산서처럼 공급가액/세액으로만 구성된 문서는 null."
                            ),
                        },
                        "usage_start_date": {
                            "type": ["string", "null"],
                            "description": "전기 사용기간 시작일 (예: '2026-05-23'), 고지서에 사용기간이 인쇄된 경우만",
                        },
                        "usage_end_date": {
                            "type": ["string", "null"],
                            "description": "전기 사용기간 종료일 (예: '2026-06-22'), 고지서에 사용기간이 인쇄된 경우만",
                        },
                        "late_fee": {
                            "type": ["number", "null"],
                            "description": "연체료 (고지서에 '연체료'로 표기된 값)",
                        },
                        "unpaid_amount": {
                            "type": ["number", "null"],
                            "description": "미납액",
                        },
                        "unpaid_late_fee": {
                            "type": ["number", "null"],
                            "description": "미납연체료",
                        },
                        "other_fee": {
                            "type": ["number", "null"],
                            "description": "기타요금 (청소용역비 등)",
                        },
                        "due_total_amount": {
                            "type": ["number", "null"],
                            "description": "납기내금액 (없으면 세금계산서의 '합계금액')",
                        },
                        "overdue_total_amount": {
                            "type": ["number", "null"],
                            "description": "납기후금액 (있는 경우만, 참고용)",
                        },
                        "field_confidence": {
                            "type": "object",
                            "description": "필드별 추출 확신도 0~1 (예: {'supply_amount': 0.9})",
                            "additionalProperties": {"type": "number"},
                        },
                        "region_readings": {
                            "type": "array",
                            "description": (
                                "관리비고지서에서 같은 금액이 여러 구역(고지서 본문/납입통지서/"
                                "수납의뢰서/납입영수증 등)에 반복 인쇄된 경우, 이미 서로 대조해서 "
                                "하나의 값으로 합의하기 전에 각 구역에서 개별적으로 읽은 값을 여기에 "
                                "그대로 남기세요(구역이 2개 미만이거나 반복 인쇄가 없으면 빈 배열)."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "region": {
                                        "type": "string",
                                        "description": "구역 이름 (예: '고지서 본문', '관리비 납입통지서', '관리비 수납의뢰서', '관리비 납입영수증')",
                                    },
                                    "current_period_charge": {"type": ["number", "null"]},
                                    "due_total_amount": {"type": ["number", "null"]},
                                    "overdue_total_amount": {"type": ["number", "null"]},
                                },
                                "required": ["region"],
                            },
                        },
                        "notes": {
                            "type": ["string", "null"],
                            "description": "판독이 애매했던 부분에 대한 메모 (예: 화질 불량으로 세액 흐릿함)",
                        },
                    },
                    "required": [
                        "doc_type",
                        "supply_amount",
                        "vat_amount",
                        "due_total_amount",
                        "field_confidence",
                    ],
                },
            }
        },
        "required": ["documents"],
    },
}
