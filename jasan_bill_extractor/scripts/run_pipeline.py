"""
scripts/run_pipeline.py
---------------------------------
Phase 2~3 파이프라인 CLI 진입점. 실제 로직은 jasan_bill_extractor/pipeline.py에 있다
(GUI와 공유하기 위함 + PyInstaller 번들 문제 회피, pipeline.py 상단 설명 참고).

사용법:
    export ANTHROPIC_API_KEY=sk-ant-...
    python run_pipeline.py --input-dir ../bills_png --output-dir ./pipeline_out --limit 30
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from jasan_bill_extractor.pipeline import run_pipeline  # noqa: E402


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
