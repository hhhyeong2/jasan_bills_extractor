"""
config.py
---------------------------------
ConfigManager (spec.md §3): API 키/경로/임계값을 한 곳에서 관리한다.
값은 환경변수(.env)로 오버라이드할 수 있다.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

# extract.vision_client
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = os.environ.get("JASAN_MODEL", "claude-sonnet-5")

# 경로
DEFAULT_INPUT_DIR = Path(os.environ.get("JASAN_INPUT_DIR", PROJECT_ROOT.parent / "bills_png"))
SITE_MASTER_PATH = Path(os.environ.get("JASAN_SITE_MASTER", PROJECT_ROOT / "data" / "site_master.csv"))
HISTORY_DB_PATH = Path(os.environ.get("JASAN_HISTORY_DB", PROJECT_ROOT / "data" / "history.db"))

# validate.history — 전월/전년동월 대비 편차 임계값 (spec.md §5.3, 기본 ±30%)
HISTORY_ANOMALY_THRESHOLD = float(os.environ.get("JASAN_HISTORY_THRESHOLD", "0.30"))

# match.site_matcher — 퍼지 매칭 최소 신뢰도 (이 미만이면 예외 큐로)
SITE_MATCH_MIN_CONFIDENCE = float(os.environ.get("JASAN_SITE_MATCH_MIN_CONFIDENCE", "0.90"))
