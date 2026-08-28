"""
validate/history.py
---------------------------------
HistoryAnomalyDetector (spec.md §3, §5.3): history.db(SQLite)에서 동일 사업장의
직전월·전년 동월 청구요금계를 조회해 편차율을 계산한다. 임계값(기본 ±30%,
config.HISTORY_ANOMALY_THRESHOLD) 초과 시 예외 큐로 보낼 수 있도록 결과를 반환한다.

이 시스템은 아직 과거 데이터 백필이 되어 있지 않다(spec.md §6 Phase 3: "history.db 구축
(과거 데이터 백필 필요)"). 이력이 없는 사업장은 오류가 아니라 "이력없음"으로 정상 처리된다 -
첫 달 데이터가 쌓여야 비로소 이상탐지가 가능해진다.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS billing_history (
    site_id TEXT NOT NULL,
    billing_period TEXT NOT NULL,  -- 'YYYYMM'
    due_total_amount REAL NOT NULL,
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (site_id, billing_period)
);
"""


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(_SCHEMA)


def record_billing(db_path: Path, site_id: str, billing_period: str, due_total_amount: float) -> None:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO billing_history (site_id, billing_period, due_total_amount) VALUES (?, ?, ?)"
            " ON CONFLICT(site_id, billing_period) DO UPDATE SET due_total_amount=excluded.due_total_amount",
            (site_id, billing_period, due_total_amount),
        )


def _prev_month(period: str) -> str:
    y, m = int(period[:4]), int(period[4:6])
    if m == 1:
        return f"{y - 1}12"
    return f"{y}{m - 1:02d}"


def _prev_year(period: str) -> str:
    y, m = int(period[:4]), int(period[4:6])
    return f"{y - 1}{m:02d}"


@dataclass
class HistoryCheckResult:
    status: str  # "정상" | "이상탐지" | "이력없음"
    prev_month_amount: Optional[float] = None
    prev_month_deviation: Optional[float] = None
    prev_year_amount: Optional[float] = None
    prev_year_deviation: Optional[float] = None


def check_anomaly(
    db_path: Path,
    site_id: str,
    billing_period: str,
    due_total_amount: float,
    threshold: float = 0.30,
) -> HistoryCheckResult:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        row_prev = conn.execute(
            "SELECT due_total_amount FROM billing_history WHERE site_id=? AND billing_period=?",
            (site_id, _prev_month(billing_period)),
        ).fetchone()
        row_year = conn.execute(
            "SELECT due_total_amount FROM billing_history WHERE site_id=? AND billing_period=?",
            (site_id, _prev_year(billing_period)),
        ).fetchone()

    if row_prev is None and row_year is None:
        return HistoryCheckResult(status="이력없음")

    result = HistoryCheckResult(status="정상")
    is_anomaly = False

    if row_prev is not None and row_prev[0]:
        dev = (due_total_amount - row_prev[0]) / row_prev[0]
        result.prev_month_amount = row_prev[0]
        result.prev_month_deviation = round(dev, 4)
        if abs(dev) > threshold:
            is_anomaly = True

    if row_year is not None and row_year[0]:
        dev = (due_total_amount - row_year[0]) / row_year[0]
        result.prev_year_amount = row_year[0]
        result.prev_year_deviation = round(dev, 4)
        if abs(dev) > threshold:
            is_anomaly = True

    if is_anomaly:
        result.status = "이상탐지"
    return result
