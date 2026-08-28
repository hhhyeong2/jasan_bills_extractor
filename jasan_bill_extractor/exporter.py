"""
exporter.py
---------------------------------
Phase 4 본체 (spec.md §6): pipeline.run_pipeline()이 만든 pipeline_results.csv를 읽어

  1) 검증을 통과하고 사업장이 매칭된 건 -> ExcelWriter로 새 엑셀 워크북에 기입
  2) 문제가 있는 건(매칭실패/산술불일치/구역불일치/이상탐지/저신뢰) -> ReviewQueueBuilder로
     예외 리포트(CSV) + 워크북 안의 '검토필요' 시트(하이라이트)에 모음
  3) 모든 건 -> AuditLogger로 원본값/합산근거 JSONL 로그
  4) bills_dir가 주어지면 원본 고지서 파일을 처리완료/검토필요/제외 폴더로 복사 분류

를 만든다. API를 다시 호출하지 않으므로 비용이 들지 않는다.

CLI(scripts/run_export.py)와 GUI(gui/app.py)가 공유하는 핵심 로직이다. pipeline.py와
같은 이유로 jasan_bill_extractor 패키지 안의 정식 모듈로 둔다 (PyInstaller 번들 문제,
pipeline.py 상단 설명 참고).
"""

import csv
import shutil
from datetime import date
from pathlib import Path

from jasan_bill_extractor import config
from jasan_bill_extractor.mapping.field_combiner import combine_power_fund
from jasan_bill_extractor.audit import logger as audit_logger
from jasan_bill_extractor.writer.excel_writer import (
    build_row, write_workbook, load_reference_data, add_business_days,
)
from jasan_bill_extractor.review.exception_queue import (
    build_exception_rows, write_exception_report, append_exception_sheet,
)


def _to_num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def load_pipeline_rows(csv_path: Path) -> list:
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    numeric_fields = [
        "supply_amount", "vat_amount", "power_fund_raw", "current_period_charge",
        "late_fee", "unpaid_amount", "unpaid_late_fee", "other_fee",
        "due_total_amount", "overdue_total_amount",
    ]
    out = []
    for r in rows:
        if not r.get("doc_type"):
            continue  # 오류/빈 프레임 행은 건너뜀
        doc = dict(r)
        for f in numeric_fields:
            doc[f] = _to_num(r.get(f))
        out.append(doc)
    return out


def sort_bills_into_folders(docs: list, exception_keys: set, bills_dir: Path, output_dir: Path, log=print) -> dict:
    """원본 고지서 파일을 처리 결과에 따라 폴더로 분류해 복사한다(원본은 그대로 두고 복사만 함).

    한 파일(TIFF 등 한 팩스 수신 단위) 안에 문서가 여러 건 있을 수 있으므로, 그 중
    하나라도 예외(재검토 필요)면 파일 전체를 '검토필요'로 분류한다(안전 우선 - 놓치는
    것보다 한 번 더 확인하는 게 낫다는 원칙, spec.md의 예외처리 철학과 동일).

        처리완료/  - 모든 문서가 검증 통과 + 사업장 매칭된 파일
        검토필요/  - 문서 중 하나라도 예외 큐에 들어간 파일
        제외/      - 고지서가 아니거나(오수신) 관할 사업장이 아닌 파일
    """
    bills_dir = Path(bills_dir)
    completed_dir = output_dir / "처리완료"
    review_dir = output_dir / "검토필요"
    excluded_dir = output_dir / "제외"
    for d in (completed_dir, review_dir, excluded_dir):
        d.mkdir(parents=True, exist_ok=True)

    excluded_methods = {"제외(비고지서)", "제외(관할아님/비고지서)"}
    file_status = {}  # source_file -> "review" | "excluded" | "completed"
    for doc in docs:
        fname = doc["source_file"]
        key = (fname, doc["frame_index"], doc["doc_index_in_frame"])
        if key in exception_keys:
            file_status[fname] = "review"
        elif doc.get("match_method") in excluded_methods:
            file_status.setdefault(fname, "excluded")
        else:
            file_status.setdefault(fname, "completed")

    counts = {"completed": 0, "review": 0, "excluded": 0, "not_found": 0}
    dest_map = {"completed": completed_dir, "review": review_dir, "excluded": excluded_dir}
    for fname, status in file_status.items():
        src = bills_dir / fname
        if not src.exists():
            counts["not_found"] += 1
            log(f"  [경고] 원본 파일을 찾을 수 없음: {src}")
            continue
        shutil.copy2(src, dest_map[status] / fname)
        counts[status] += 1

    log(f"[파일분류] 처리완료 {counts['completed']}건 / 검토필요 {counts['review']}건 / "
        f"제외 {counts['excluded']}건" + (f" / 원본없음 {counts['not_found']}건" if counts["not_found"] else ""))
    return counts


def run_export(
    pipeline_csv: Path,
    output_dir: Path,
    excel_template: Path = None,
    min_confidence: float = 0.7,
    payment_business_days: int = 3,
    bills_dir: Path = None,
    log=print,
) -> dict:
    """Phase 4 본체. GUI(gui.app)와 CLI(scripts/run_export.py)가 공유하는 핵심 로직.
    반환값: {"excel_path": Path, "exception_csv": Path, "n_good": int, "n_exception": int, "folder_counts": dict|None}
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    excel_template_path = Path(excel_template) if excel_template else (config.PROJECT_ROOT.parent / "경기북부 엑셀양식.xlsx")
    reference_data = load_reference_data(excel_template_path)
    log(f"[참고데이터] 엑셀양식에서 사업장 {len(reference_data)}건의 최근 이력 로드 ({excel_template_path.name})")

    payment_date = add_business_days(date.today(), payment_business_days)
    log(f"[지급일자] 오늘({date.today().isoformat()}) 기준 +{payment_business_days}영업일 -> {payment_date.isoformat()}")

    docs = load_pipeline_rows(Path(pipeline_csv))
    log(f"[로드] 문서 {len(docs)}건")

    excluded_methods = {"제외(비고지서)", "제외(관할아님/비고지서)"}
    exception_rows = build_exception_rows(docs, min_confidence=min_confidence)
    exception_keys = {(e.pipeline_row["source_file"], e.pipeline_row["frame_index"], e.pipeline_row["doc_index_in_frame"]) for e in exception_rows}

    good_rows = []
    audit_log_path = output_dir / "audit_log.jsonl"
    for doc in docs:
        key = (doc["source_file"], doc["frame_index"], doc["doc_index_in_frame"])
        if doc.get("match_method") in excluded_methods:
            continue

        combined = combine_power_fund(doc)
        entry = audit_logger.build_entry(
            source_file=doc["source_file"], frame_index=doc["frame_index"], doc_idx=doc["doc_index_in_frame"],
            doc=doc, combined_power_fund=combined,
            site_id=doc.get("site_id") or "", site_name=doc.get("site_name") or "",
            arithmetic_status=doc.get("arithmetic_due_status") or "",
            cross_field_status=doc.get("cross_field_status") or "",
            history_status=doc.get("history_status") or "",
        )
        audit_logger.append_entry(audit_log_path, entry)

        if key not in exception_keys and doc.get("site_id"):
            site_id = doc["site_id"]
            good_rows.append(build_row(
                doc, site_id, doc.get("site_name") or "",
                reference_data.get(site_id), payment_date,
            ))

    output_xlsx = output_dir / "경기북부_엑셀양식_자동입력.xlsx"
    n_written = write_workbook(excel_template_path, good_rows, output_xlsx)
    log(f"[엑셀기입] {n_written}건 -> {output_xlsx}")

    exception_csv = output_dir / "exception_report.csv"
    write_exception_report(exception_rows, exception_csv)
    append_exception_sheet(output_xlsx, exception_rows)
    log(f"[예외리포트] {len(exception_rows)}건 -> {exception_csv} (엑셀 '검토필요' 시트에도 반영)")
    log(f"[감사로그] -> {audit_log_path}")

    folder_counts = None
    if bills_dir:
        folder_counts = sort_bills_into_folders(docs, exception_keys, Path(bills_dir), output_dir, log=log)

    total = len(good_rows) + len(exception_rows)
    if total:
        log("\n=== Phase 4 요약 ===")
        log(f"자동기입 {len(good_rows)}건 ({len(good_rows)/total*100:.1f}%) / "
            f"예외검토 {len(exception_rows)}건 ({len(exception_rows)/total*100:.1f}%)")

    return {
        "excel_path": output_xlsx, "exception_csv": exception_csv,
        "n_good": len(good_rows), "n_exception": len(exception_rows),
        "folder_counts": folder_counts,
    }
