"""
scripts/run_export.py
---------------------------------
Phase 4 CLI 진입점. 실제 로직은 jasan_bill_extractor/exporter.py에 있다
(GUI와 공유하기 위함 + PyInstaller 번들 문제 회피, pipeline.py 상단 설명 참고).

사용법:
    python run_export.py --pipeline-csv ./pipeline_out/pipeline_results.csv --output-dir ./export_out
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT.parent))

from jasan_bill_extractor.exporter import run_export  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="jasan_bills Phase 4 엑셀 기입 + 예외 리포트 생성기")
    parser.add_argument("--pipeline-csv", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--excel-template", default=None,
                         help="기존 경기북부 엑셀양식 경로 (사업장명/사용기간 이력 참고용 겸 헤더 템플릿). "
                              "생략하면 프로젝트 상위 폴더의 '경기북부 엑셀양식.xlsx'를 사용")
    parser.add_argument("--min-confidence", type=float, default=0.7)
    parser.add_argument("--payment-business-days", type=int, default=3,
                         help="지급일자 = 오늘 기준 +N 영업일 (기본 3, 공휴일 캘린더는 미반영)")
    parser.add_argument("--bills-dir", default=None,
                         help="원본 고지서 폴더. 지정하면 처리 결과에 따라 output-dir 아래 "
                              "처리완료/검토필요/제외 폴더로 파일을 복사해 분류한다.")
    args = parser.parse_args()

    run_export(
        Path(args.pipeline_csv), Path(args.output_dir),
        excel_template=Path(args.excel_template) if args.excel_template else None,
        min_confidence=args.min_confidence,
        payment_business_days=args.payment_business_days,
        bills_dir=Path(args.bills_dir) if args.bills_dir else None,
    )


if __name__ == "__main__":
    main()
