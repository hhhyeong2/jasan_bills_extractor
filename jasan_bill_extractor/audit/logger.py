"""
audit/logger.py
---------------------------------
AuditLogger (spec.md §1.1, §2): W열 합산값이 이상해 보일 때 "전력기금 자체가 이상한 건지,
연체료가 이상한 건지"를 즉시 확인할 수 있도록, 원본 추출값 + 합산 근거 + 검증 결과를
문서 1건당 1줄의 JSONL로 남긴다.
"""

import json
from datetime import datetime, timezone
from pathlib import Path


def append_entry(log_path: Path, entry: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"logged_at": datetime.now(timezone.utc).isoformat(), **entry}
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def build_entry(
    source_file: str,
    frame_index: int,
    doc_idx: int,
    doc: dict,
    combined_power_fund,
    site_id: str,
    site_name: str,
    arithmetic_status: str,
    cross_field_status: str,
    history_status: str,
) -> dict:
    return {
        "source_file": source_file,
        "frame_index": frame_index,
        "doc_index_in_frame": doc_idx,
        "site_id": site_id,
        "site_name": site_name,
        "billing_period": doc.get("billing_period"),
        "due_total_amount": doc.get("due_total_amount"),
        "power_fund_original_values": {
            "power_fund_raw": combined_power_fund.power_fund_raw,
            "late_fee": combined_power_fund.late_fee,
            "unpaid_amount": combined_power_fund.unpaid_amount,
            "unpaid_late_fee": combined_power_fund.unpaid_late_fee,
        },
        "power_fund_combined": combined_power_fund.combined_value,
        "arithmetic_status": arithmetic_status,
        "cross_field_status": cross_field_status,
        "history_status": history_status,
        "field_confidence": doc.get("field_confidence"),
    }
