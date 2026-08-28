"""
match/site_master.py
---------------------------------
사업장 마스터(data/site_master.csv) 로딩 (spec.md §3).

이 CSV는 두 부분으로 이뤄져 있다:
1. 실제 팩스 수신 기록 (팩스번호별 1행, 사업장번호/사업장명이 Phase 0에서 확정됨)
2. "--- 아래는 경기북부 엑셀양식.xlsx 참고용 전체 사업장 목록입니다 ---" 구분선 이후의
   참고용 전체 사업장 목록 (엑셀에 등록된 모든 사업장, 신규/미등록 팩스번호의 퍼지매칭 후보로 사용)
"""

import csv
from dataclasses import dataclass, field
from pathlib import Path

REFERENCE_MARKER = "참고용 전체 사업장 목록"
EXCLUSION_KEYWORDS = ["제외대상", "복구불가", "판독 불가", "관할 아님"]


@dataclass
class SiteRecord:
    site_id: str
    site_name: str
    payer_id: str = ""
    fax_number: str = ""
    alt_names: list = field(default_factory=list)  # 계좌명 등 별칭


@dataclass
class SiteMaster:
    by_fax: dict  # fax_number -> SiteRecord
    by_payer_id: dict  # payer_id -> SiteRecord
    all_known_sites: list  # SiteRecord (팩스기록 + 참고목록 전체, 퍼지매칭 후보 풀)
    excluded_fax: dict  # fax_number -> 제외 사유 (관할 아님/비고지서/복구불가 등, 매칭 대상에서 제외)


def load_site_master(csv_path: Path) -> SiteMaster:
    with open(csv_path, encoding="utf-8-sig") as f:
        lines = f.readlines()

    ref_start = next((i for i, l in enumerate(lines) if REFERENCE_MARKER in l), len(lines))
    header = lines[0]
    real_rows = list(csv.DictReader([header] + lines[1:ref_start]))
    ref_rows = list(csv.DictReader(lines[ref_start + 1:], fieldnames=[
        "사업장번호", "사업장명", "납부자번호", "팩스번호",
        "수신건수", "최초수신일", "최근수신일", "예시파일명(최대3개)", "매칭비고",
    ])) if ref_start < len(lines) else []

    by_fax = {}
    by_payer_id = {}
    all_known_sites = []
    excluded_fax = {}
    seen_ids = set()

    for r in real_rows:
        site_id = (r.get("사업장번호") or "").strip()
        site_name = (r.get("사업장명") or "").strip()
        fax = (r.get("팩스번호") or "").strip()
        payer_id = (r.get("납부자번호") or "").strip()
        note = (r.get("매칭비고") or "").strip()

        if not site_id:
            if fax and any(k in note for k in EXCLUSION_KEYWORDS):
                excluded_fax[fax] = note
            continue
        rec = SiteRecord(site_id=site_id, site_name=site_name, payer_id=payer_id, fax_number=fax)
        if fax:
            by_fax[fax] = rec
        if payer_id:
            by_payer_id[payer_id] = rec
        key = (site_id, site_name)
        if key not in seen_ids:
            seen_ids.add(key)
            all_known_sites.append(rec)

    for r in ref_rows:
        site_id = (r.get("사업장번호") or "").strip()
        site_name = (r.get("사업장명") or "").strip()
        acct_name = (r.get("납부자번호") or "").strip()  # 참고목록에서는 이 칸이 계좌명으로 채워짐
        if not site_id or not site_name:
            continue
        key = (site_id, site_name)
        if key in seen_ids:
            continue
        seen_ids.add(key)
        all_known_sites.append(SiteRecord(site_id=site_id, site_name=site_name, alt_names=[acct_name] if acct_name else []))

    return SiteMaster(by_fax=by_fax, by_payer_id=by_payer_id, all_known_sites=all_known_sites, excluded_fax=excluded_fax)
