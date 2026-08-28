"""
scripts/run_poc.py
---------------------------------
Phase 1 PoC 실행 스크립트: 샘플 폴더의 TIFF 청구서를 30~50건 정도 돌려서
VisionExtractor의 필드별 추출 결과를 CSV로 뽑아 정확도를 사람이 검수할 수 있게 한다.

사용법:
    export ANTHROPIC_API_KEY=sk-ant-...
    python run_poc.py --input-dir jasan_bills/bills_png --output-dir ./poc_out --limit 30

    # API 키 없이 전처리 파이프라인만 확인하고 싶을 때:
    python run_poc.py --input-dir jasan_bills/bills_png --output-dir ./poc_out --limit 5 --dry-run
"""

import argparse
import csv
import json
import sys
import time
import traceback
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (jasan_bill_extractor/를 패키지처럼 임포트하기 위함)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from jasan_bill_extractor.preprocess.image_prep import prep_file  # noqa: E402
from jasan_bill_extractor.extract.vision_client import (  # noqa: E402
    DEFAULT_MODEL,
    extract_from_image,
    VisionExtractionError,
)

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
    "sanity_check",
    "min_field_confidence",
    "notes",
    "error",
]


def _sum_known(parts) -> float:
    return sum(p for p in parts if isinstance(p, (int, float)))


def sanity_check_row(doc: dict) -> str:
    """산술 검증. 관리비고지서 계열(당월부과액 기반)과 세금계산서 계열(공급가액/부가세
    분해 기반) 두 가지 산식을 모두 시도해 하나라도 맞으면 OK로 표시한다.
    (사람 검수 결과, 두 계열이 서로 다른 산식을 쓴다는 걸 확인함 - poc_results_review.csv 참고)
    (정식 ArithmeticValidator는 Phase 2에서 구현. 여기서는 눈으로 훑어보기 위한 보조 표시.)
    """
    total = doc.get("due_total_amount")
    if total is None:
        return "N/A(총액없음)"

    checks = []

    # 산식 A: 관리비고지서 계열 - 당월부과액 + 미납액 + 미납연체료 = 납기내금액
    cpc = doc.get("current_period_charge")
    if cpc is not None:
        parts_a = [cpc, doc.get("unpaid_amount"), doc.get("unpaid_late_fee")]
        diff_a = round(total - _sum_known(parts_a), 2)
        checks.append(("당월부과액기준", diff_a))

    # 산식 B: 세금계산서 계열 - 공급가액+부가세+전력기금+미납액+미납연체료+기타 = 납기내금액
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
        return "N/A"

    for label, diff in checks:
        if abs(diff) <= 10:  # 원단위 절사 등 허용오차
            return f"OK({label})"

    # 둘 다 실패하면 더 작은 차이를 보여준다
    label, diff = min(checks, key=lambda x: abs(x[1]))
    return f"불일치({label},차이 {diff:+.0f}원)"


def process_document(doc: dict, source_file: str, frame_index: int, frame_count: int, doc_idx: int) -> dict:
    conf = doc.get("field_confidence") or {}
    min_conf = min(conf.values()) if conf else None
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
        "sanity_check": sanity_check_row(doc),
        "min_field_confidence": min_conf,
        "notes": doc.get("notes"),
        "error": "",
    }


def error_row(source_file: str, frame_index: int, frame_count: int, err: str) -> dict:
    row = {col: "" for col in CSV_COLUMNS}
    row.update(
        source_file=source_file,
        frame_index=frame_index,
        frame_count=frame_count,
        error=err,
    )
    return row


def main():
    parser = argparse.ArgumentParser(description="jasan_bills PoC 추출 실행기")
    parser.add_argument("--input-dir", required=True, help="TIFF 청구서가 있는 폴더")
    parser.add_argument("--output-dir", required=True, help="결과 CSV/원본 응답을 저장할 폴더")
    parser.add_argument("--limit", type=int, default=30, help="처리할 최대 파일 수 (기본 30)")
    parser.add_argument("--model", default=None, help="사용할 모델 (기본: 환경변수 JASAN_MODEL 또는 claude-sonnet-5)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="API를 호출하지 않고 전처리 파이프라인만 검증 (API 키 불필요)",
    )
    parser.add_argument(
        "--denoise",
        action="store_true",
        help="미디언 필터 노이즈 제거 사용 (기본 끔 — 실측상 글자 획이 뭉개지는 경우가 있어 비교 테스트 권장)",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(exist_ok=True)

    tif_files = sorted(input_dir.glob("*.tif")) + sorted(input_dir.glob("*.tiff"))
    tif_files = tif_files[: args.limit]

    if not tif_files:
        print(f"[경고] {input_dir} 에서 tif 파일을 찾지 못했습니다.")
        return

    print(f"[시작] {len(tif_files)}개 파일 처리 (dry-run={args.dry_run})")

    rows = []
    n_ok, n_err = 0, 0

    for i, path in enumerate(tif_files, 1):
        t0 = time.time()
        try:
            prepped_frames = prep_file(str(path), denoise=args.denoise)
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{len(tif_files)}] {path.name}: 전처리 실패 - {e}")
            rows.append(error_row(path.name, -1, -1, f"preprocess_error: {e}"))
            n_err += 1
            continue

        for frame in prepped_frames:
            if args.dry_run:
                rows.append(
                    error_row(
                        path.name,
                        frame.frame_index,
                        frame.frame_count,
                        "DRY-RUN: 전처리만 수행됨 (실제 추출 없음)",
                    )
                )
                continue

            try:
                result = extract_from_image(
                    frame.png_bytes,
                    filename_hint=path.name,
                    frame_index=frame.frame_index,
                    frame_count=frame.frame_count,
                    model=args.model or DEFAULT_MODEL,
                )
                # 원본 응답 저장 (감사/디버깅용, spec.md의 AuditLogger 역할 축소판)
                raw_path = raw_dir / f"{path.stem}_f{frame.frame_index}.json"
                raw_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                )

                docs = result.get("documents", [])
                if not docs:
                    rows.append(
                        error_row(
                            path.name, frame.frame_index, frame.frame_count, "문서 없음(빈 배열)"
                        )
                    )
                for doc_idx, doc in enumerate(docs):
                    rows.append(
                        process_document(
                            doc, path.name, frame.frame_index, frame.frame_count, doc_idx
                        )
                    )
                n_ok += 1
            except VisionExtractionError as e:
                print(f"  [{i}/{len(tif_files)}] {path.name} (frame {frame.frame_index}): {e}")
                rows.append(error_row(path.name, frame.frame_index, frame.frame_count, str(e)))
                n_err += 1
            except Exception as e:  # noqa: BLE001
                print(f"  [{i}/{len(tif_files)}] {path.name}: 예기치 못한 오류 - {e}")
                traceback.print_exc()
                rows.append(error_row(path.name, frame.frame_index, frame.frame_count, f"unexpected: {e}"))
                n_err += 1

        elapsed = time.time() - t0
        print(f"  [{i}/{len(tif_files)}] {path.name} 처리 완료 ({elapsed:.1f}s)")

    out_csv = output_dir / "poc_results.csv"
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"\n[완료] 성공 파일 {n_ok}건 / 오류 {n_err}건 -> {out_csv}")
    if args.dry_run:
        print("(dry-run 모드였으므로 실제 추출 결과는 없습니다. ANTHROPIC_API_KEY 설정 후 재실행하세요.)")


if __name__ == "__main__":
    main()
