"""
scripts/run_pipeline.py
---------------------------------
Phase 2~3 파이프라인 통합 스크립트 (spec.md §6): 전처리 -> 추출 -> 검증
(ArithmeticValidator + CrossFieldValidator) -> 사업장 매칭(SiteMatcher) -> 이력
이상탐지(HistoryAnomalyDetector)를 배치로 실행한다.

Phase 2 완료 기준: 산술검증 통과율 측정 (30~50건)
Phase 3 완료 기준: 사업장 매칭 실패율 측정

사용법:
    export ANTHROPIC_API_KEY=sk-ant-...
    python run_pipeline.py --input-dir ../bills_png --output-dir ./pipeline_out --limit 30
"""

import argparse
import csv
import json
import re
import sys
import time
import traceback
from pathlib import Path


def re_is_yyyymm(s: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(s)))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from jasan_bill_extractor import config  # noqa: E402
from jasan_bill_extractor.preprocess.image_prep import prep_file  # noqa: E402
from jasan_bill_extractor.extract.vision_client import (  # noqa: E402
    DEFAULT_MODEL,
    extract_from_image,
    VisionExtractionError,
)
from jasan_bill_extractor.validate.arithmetic import validate_due_total, validate_overdue_total  # noqa: E402
from jasan_bill_extractor.validate.cross_field import validate_regions  # noqa: E402
from jasan_bill_extractor.validate import history  # noqa: E402
from jasan_bill_extractor.match.site_master import load_site_master  # noqa: E402
from jasan_bill_extractor.match.site_matcher import SiteMatcher, MatchResult  # noqa: E402

_AMOUNT_FIELDS = [
    "supply_amount", "vat_amount", "power_fund_raw", "current_period_charge",
    "late_fee", "unpaid_amount", "unpaid_late_fee", "other_fee",
    "due_total_amount", "overdue_total_amount",
]


def _is_non_bill(doc: dict) -> bool:
    """금액 필드가 전부 null이면 애초에 청구서가 아닌 오수신 문서로 간주한다
    (예: 자동차등록원부, 광고성 팩스 등 - 모델이 notes에 '청구서가 아님'이라고 스스로 밝히는 경우가 많음)."""
    return all(doc.get(f) is None for f in _AMOUNT_FIELDS)

CSV_COLUMNS = [
    "source_file",
    "frame_index",
    "frame_count",
    "doc_index_in_frame",
    "doc_type",
    "site_hint_text",
    "billing_period",
    "usage_start_date",
    "usage_end_date",
    "supply_amount",
    "vat_amount",
    "power_fund_raw",
    "current_period_charge",
    "late_fee",
    "unpaid_amount",
    "unpaid_late_fee",
    "other_fee",
    "due_total_amount",
    "overdue_total_amount",
    "arithmetic_due_status",
    "arithmetic_overdue_status",
    "cross_field_status",
    "cross_field_detail",
    "site_id",
    "site_name",
    "match_method",
    "match_confidence",
    "history_status",
    "history_prev_month_deviation",
    "history_prev_year_deviation",
    "min_field_confidence",
    "notes",
    "error",
]


def process_document(
    doc: dict, source_file: str, frame_index: int, frame_count: int, doc_idx: int,
    matcher: SiteMatcher,
) -> dict:
    conf = doc.get("field_confidence") or {}
    # 값이 null인 필드의 confidence(보통 0.0, "해당사항 없음"의 의미)는 실제 추출 실패가
    # 아니므로 제외하고, 실제로 값을 채운 필드들의 confidence만으로 최솟값을 계산한다.
    relevant_conf = {k: v for k, v in conf.items() if doc.get(k) is not None}
    min_conf = min(relevant_conf.values()) if relevant_conf else None

    due_result = validate_due_total(doc)
    overdue_result = validate_overdue_total(doc)
    cross_result = validate_regions(doc)

    cross_detail = ""
    if cross_result.mismatches:
        parts = [
            f"{f}:{ra}={va} vs {rb}={vb}"
            for f, ra, va, rb, vb in cross_result.mismatches
        ]
        cross_detail = "; ".join(parts)

    if _is_non_bill(doc):
        # doc_type이 있어도 금액 필드가 전부 null이면(예: 자동차등록원부 등 오수신 문서)
        # 사업장 매칭 대상 자체가 아니므로 "매칭실패"로 세지 않는다.
        match_result = MatchResult(matched=False, method="제외(비고지서)")
    else:
        match_result = matcher.match(source_file, site_hint_text=doc.get("site_hint_text"))

    hist_status = ""
    hist_prev_month_dev = ""
    hist_prev_year_dev = ""
    billing_period = doc.get("billing_period")
    due_total = doc.get("due_total_amount")
    if match_result.matched and billing_period and re_is_yyyymm(billing_period) and due_total is not None:
        hist_result = history.check_anomaly(
            config.HISTORY_DB_PATH, match_result.site_id, billing_period, due_total,
            threshold=config.HISTORY_ANOMALY_THRESHOLD,
        )
        hist_status = hist_result.status
        hist_prev_month_dev = hist_result.prev_month_deviation
        hist_prev_year_dev = hist_result.prev_year_deviation
        # 이번 달 값을 이력에 기록해 다음 달부터 비교 가능하게 함
        history.record_billing(config.HISTORY_DB_PATH, match_result.site_id, billing_period, due_total)

    return {
        "source_file": source_file,
        "frame_index": frame_index,
        "frame_count": frame_count,
        "doc_index_in_frame": doc_idx,
        "doc_type": doc.get("doc_type"),
        "site_hint_text": doc.get("site_hint_text"),
        "billing_period": doc.get("billing_period"),
        "usage_start_date": doc.get("usage_start_date"),
        "usage_end_date": doc.get("usage_end_date"),
        "supply_amount": doc.get("supply_amount"),
        "vat_amount": doc.get("vat_amount"),
        "power_fund_raw": doc.get("power_fund_raw"),
        "current_period_charge": doc.get("current_period_charge"),
        "late_fee": doc.get("late_fee"),
        "unpaid_amount": doc.get("unpaid_amount"),
        "unpaid_late_fee": doc.get("unpaid_late_fee"),
        "other_fee": doc.get("other_fee"),
        "due_total_amount": doc.get("due_total_amount"),
        "overdue_total_amount": doc.get("overdue_total_amount"),
        "arithmetic_due_status": due_result.status_label,
        "arithmetic_overdue_status": overdue_result.status_label,
        "cross_field_status": cross_result.status,
        "cross_field_detail": cross_detail,
        "site_id": match_result.site_id or "",
        "site_name": match_result.site_name or "",
        "match_method": match_result.method,
        "match_confidence": match_result.confidence,
        "history_status": hist_status,
        "history_prev_month_deviation": hist_prev_month_dev,
        "history_prev_year_deviation": hist_prev_year_dev,
        "min_field_confidence": min_conf,
        "notes": doc.get("notes"),
        "error": "",
    }


def error_row(source_file: str, frame_index: int, frame_count: int, err: str) -> dict:
    row = {col: "" for col in CSV_COLUMNS}
    row.update(source_file=source_file, frame_index=frame_index, frame_count=frame_count, error=err)
    return row


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    limit: int = 30,
    model: str = None,
    denoise: bool = False,
    log=print,
    progress_cb=None,
) -> dict:
    """Phase 2~3 파이프라인 본체. GUI(gui.app)와 CLI(main())가 공유하는 핵심 로직.

    progress_cb(i, total, filename)는 파일 처리 시작 시마다 호출된다(진행률 표시용).
    반환값: {"csv_path": Path, "rows": list[dict], "n_ok": int, "n_err": int, "summary": dict}
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(exist_ok=True)

    input_dir = Path(input_dir)
    tif_files = sorted(input_dir.glob("*.tif")) + sorted(input_dir.glob("*.tiff"))
    tif_files = tif_files[:limit]
    if not tif_files:
        log(f"[경고] {input_dir} 에서 tif 파일을 찾지 못했습니다.")
        return {"csv_path": None, "rows": [], "n_ok": 0, "n_err": 0, "summary": {}}

    site_master = load_site_master(config.SITE_MASTER_PATH)
    matcher = SiteMatcher(site_master, min_confidence=config.SITE_MATCH_MIN_CONFIDENCE)
    log(f"[사업장마스터] 팩스매칭 {len(site_master.by_fax)}건, 전체후보 {len(site_master.all_known_sites)}건 로드")

    log(f"[시작] {len(tif_files)}개 파일 처리")
    rows = []
    n_ok, n_err = 0, 0

    for i, path in enumerate(tif_files, 1):
        if progress_cb:
            progress_cb(i, len(tif_files), path.name)
        t0 = time.time()
        try:
            prepped_frames = prep_file(str(path), denoise=denoise)
        except Exception as e:  # noqa: BLE001
            log(f"  [{i}/{len(tif_files)}] {path.name}: 전처리 실패 - {e}")
            rows.append(error_row(path.name, -1, -1, f"preprocess_error: {e}"))
            n_err += 1
            continue

        for frame in prepped_frames:
            try:
                result = extract_from_image(
                    frame.png_bytes,
                    filename_hint=path.name,
                    frame_index=frame.frame_index,
                    frame_count=frame.frame_count,
                    model=model or DEFAULT_MODEL,
                )
                raw_path = raw_dir / f"{path.stem}_f{frame.frame_index}.json"
                raw_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

                docs = result.get("documents", [])
                if not docs:
                    rows.append(error_row(path.name, frame.frame_index, frame.frame_count, "문서 없음(빈 배열)"))
                for doc_idx, doc in enumerate(docs):
                    rows.append(process_document(doc, path.name, frame.frame_index, frame.frame_count, doc_idx, matcher))
                n_ok += 1
            except VisionExtractionError as e:
                log(f"  [{i}/{len(tif_files)}] {path.name} (frame {frame.frame_index}): {e}")
                rows.append(error_row(path.name, frame.frame_index, frame.frame_count, str(e)))
                n_err += 1
            except Exception as e:  # noqa: BLE001
                log(f"  [{i}/{len(tif_files)}] {path.name}: 예기치 못한 오류 - {e}")
                traceback.print_exc()
                rows.append(error_row(path.name, frame.frame_index, frame.frame_count, f"unexpected: {e}"))
                n_err += 1

        elapsed = time.time() - t0
        log(f"  [{i}/{len(tif_files)}] {path.name} 처리 완료 ({elapsed:.1f}s)")

    out_csv = output_dir / "pipeline_results.csv"
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    summary = _summarize(rows, log)
    log(f"\n[완료] 파일 성공 {n_ok}건 / 오류 {n_err}건 -> {out_csv}")
    return {"csv_path": out_csv, "rows": rows, "n_ok": n_ok, "n_err": n_err, "summary": summary}


def _summarize(rows: list, log=print) -> dict:
    # Phase 2 완료 기준: 산술검증 통과율 집계
    doc_rows = [r for r in rows if r.get("doc_type")]
    n_docs = len(doc_rows)
    if not n_docs:
        return {}
    n_due_ok = sum(1 for r in doc_rows if str(r["arithmetic_due_status"]).startswith("OK"))
    n_due_na = sum(1 for r in doc_rows if str(r["arithmetic_due_status"]).startswith("N/A"))
    n_due_fail = n_docs - n_due_ok - n_due_na
    n_cross_checked = sum(1 for r in doc_rows if r["cross_field_status"] != "N/A")
    n_cross_ok = sum(1 for r in doc_rows if r["cross_field_status"] == "OK")

    log(f"\n=== Phase 2 검증 통과율 요약 (문서 {n_docs}건 기준) ===")
    log(f"ArithmeticValidator(납기내금액): OK {n_due_ok}건 ({n_due_ok/n_docs*100:.1f}%) / "
        f"불일치 {n_due_fail}건 / 비교불가 {n_due_na}건")
    if n_cross_checked:
        log(f"CrossFieldValidator: 비교대상 {n_cross_checked}건 중 OK {n_cross_ok}건 "
            f"({n_cross_ok/n_cross_checked*100:.1f}%)")
    else:
        log("CrossFieldValidator: region_readings 2개 이상인 문서 없음 (비교 대상 없음)")

    # Phase 3 완료 기준: 사업장 매칭 실패율 집계 (고지서가 아니거나 관할 아닌 문서는 분모에서 제외)
    EXCLUDE_METHODS = {"제외(비고지서)", "제외(관할아님/비고지서)"}
    n_excluded = sum(1 for r in doc_rows if r["match_method"] in EXCLUDE_METHODS)
    bill_rows = [r for r in doc_rows if r["match_method"] not in EXCLUDE_METHODS]
    n_bills = len(bill_rows)
    n_matched = sum(1 for r in bill_rows if r["site_id"])
    n_fail = n_bills - n_matched
    by_method = {}
    for r in bill_rows:
        if r["site_id"]:
            by_method[r["match_method"]] = by_method.get(r["match_method"], 0) + 1
    log(f"\n=== Phase 3 사업장 매칭 결과 (문서 {n_docs}건 중 실제 고지서 {n_bills}건 기준, 비고지서 {n_excluded}건 제외) ===")
    if n_bills:
        log(f"매칭 성공 {n_matched}건 ({n_matched/n_bills*100:.1f}%) / 매칭 실패 {n_fail}건 ({n_fail/n_bills*100:.1f}%)")
    if by_method:
        log("  매칭 방법별: " + ", ".join(f"{k}={v}" for k, v in by_method.items()))

    n_hist_checked = sum(1 for r in doc_rows if r["history_status"])
    n_hist_anomaly = sum(1 for r in doc_rows if r["history_status"] == "이상탐지")
    n_hist_none = sum(1 for r in doc_rows if r["history_status"] == "이력없음")
    log(f"HistoryAnomalyDetector: 이력있음/비교됨 {n_hist_checked - n_hist_none}건 "
        f"(이상탐지 {n_hist_anomaly}건) / 이력없음(첫 데이터) {n_hist_none}건")

    return {
        "n_docs": n_docs, "n_due_ok": n_due_ok, "n_due_na": n_due_na, "n_due_fail": n_due_fail,
        "n_bills": n_bills, "n_matched": n_matched, "n_fail": n_fail,
    }


def main():
    parser = argparse.ArgumentParser(description="jasan_bills Phase 2 파이프라인 실행기")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--model", default=None)
    parser.add_argument("--denoise", action="store_true")
    args = parser.parse_args()

    run_pipeline(
        Path(args.input_dir), Path(args.output_dir),
        limit=args.limit, model=args.model, denoise=args.denoise,
    )


if __name__ == "__main__":
    main()
