"""
build_site_master_draft.py
---------------------------------
Phase 0 산출물: data/site_master.csv 초안 생성 스크립트.

jasan_bills/bills_png 폴더의 팩스 수신 파일명 패턴(`<팩스번호>_<수신일시14자리>.tif`)에서
팩스번호를 추출해, 사업장번호/사업장명/납부자번호를 사람이 채워 넣을 수 있는
초안 CSV를 만든다.

사용법:
    python build_site_master_draft.py <폴더목록 JSON 경로> <경기북부 엑셀양식.xlsx 경로> <출력 CSV 경로>

폴더목록 JSON은 device_list_dir 같은 도구가 반환하는
{"entries": [{"name": "...", "size": ..., "mtimeMs": ...}, ...]} 형식을 그대로 사용한다.
"""

import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime

FAX_PATTERN = re.compile(r"^(\d{7,})_(\d{14})\.(tif|tiff)$", re.IGNORECASE)


def load_listing(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [e["name"] for e in data["entries"] if e.get("type", "file") == "file"]


def parse_timestamp(ts14):
    """'20260624102225' -> datetime, 형식이 안 맞으면 None."""
    try:
        return datetime.strptime(ts14, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def build_fax_groups(filenames):
    """팩스번호별로 파일 목록/수신횟수/최초·최근 수신일을 집계."""
    groups = defaultdict(list)
    unmatched = []

    for name in filenames:
        m = FAX_PATTERN.match(name)
        if m:
            fax_no, ts14, _ext = m.groups()
            dt = parse_timestamp(ts14)
            groups[fax_no].append((name, dt))
        else:
            unmatched.append(name)

    return groups, unmatched


def load_known_sites_from_excel(xlsx_path):
    """경기북부 엑셀양식.xlsx에서 이미 채워진 사업장번호/사업장명 조합을 참고용으로 읽는다."""
    try:
        import openpyxl
    except ImportError:
        print("[경고] openpyxl이 설치되어 있지 않아 엑셀 참고 데이터를 건너뜁니다.", file=sys.stderr)
        return []

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active  # 첫 시트 사용
    known = []
    for row in ws.iter_rows(min_row=2, max_col=5, values_only=True):
        # A=지급월 B=계좌명 C=유형 D=사업장명 E=사업장번호
        site_name, site_id = row[3], row[4]
        if site_name and site_id:
            known.append((str(site_id).strip(), str(site_name).strip()))
    # 중복 제거, 순서 유지
    seen = set()
    result = []
    for site_id, site_name in known:
        key = (site_id, site_name)
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def guess_name_from_filename(name):
    """숫자_타임스탬프 패턴이 아닌 파일명에서 건물명 후보를 뽑는다.
    예: '6월_흥화오피스텔.tif' -> '흥화오피스텔', '크리스탈빌딩.tif' -> '크리스탈빌딩'
    """
    stem = re.sub(r"\.(tif|tiff)$", "", name, flags=re.IGNORECASE)
    stem = re.sub(r"^\d+월_", "", stem)  # 앞의 '6월_' 같은 접두어 제거
    return stem


def write_draft_csv(out_path, fax_groups, unmatched_named, known_sites):
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "사업장번호", "사업장명", "납부자번호", "팩스번호",
            "수신건수", "최초수신일", "최근수신일", "예시파일명(최대3개)", "비고",
        ])

        # 1) 숫자 팩스번호 패턴으로 잡힌 그룹 -> 담당자가 사업장번호/사업장명/납부자번호를 채워야 함
        for fax_no in sorted(fax_groups.keys()):
            entries = fax_groups[fax_no]
            entries_sorted = sorted(
                entries, key=lambda x: (x[1] is None, x[1])
            )
            dates = [dt for _, dt in entries_sorted if dt is not None]
            first_seen = min(dates).strftime("%Y-%m-%d") if dates else ""
            last_seen = max(dates).strftime("%Y-%m-%d") if dates else ""
            examples = "; ".join(name for name, _ in entries_sorted[:3])
            writer.writerow([
                "", "", "", fax_no,
                len(entries), first_seen, last_seen, examples,
                "샘플 1건을 열어 사업장명을 확인 후 채워주세요",
            ])

        # 2) 이미 건물명이 파일명에 있는 경우 (팩스번호 패턴이 아닌 파일들)
        for name in sorted(unmatched_named):
            guess = guess_name_from_filename(name)
            writer.writerow([
                "", guess, "", "",
                "", "", "", name,
                "파일명에서 사업장명 추정됨 - 팩스번호/사업장번호 수동 확인 필요",
            ])

        # 3) 참고용: 엑셀 양식에 이미 기재돼 있던 사업장번호/사업장명 (있다면)
        for site_id, site_name in known_sites:
            writer.writerow([
                site_id, site_name, "", "",
                "", "", "", "",
                "경기북부 엑셀양식.xlsx에 이미 기재된 사업장 (참고용, 팩스번호와 매칭 필요)",
            ])


def main():
    if len(sys.argv) != 4:
        print(
            "사용법: python build_site_master_draft.py "
            "<폴더목록 JSON> <경기북부 엑셀양식.xlsx> <출력 CSV>",
            file=sys.stderr,
        )
        sys.exit(1)

    listing_json, xlsx_path, out_csv = sys.argv[1], sys.argv[2], sys.argv[3]

    filenames = load_listing(listing_json)
    fax_groups, unmatched = build_fax_groups(filenames)
    known_sites = load_known_sites_from_excel(xlsx_path)

    write_draft_csv(out_csv, fax_groups, unmatched, known_sites)

    print(f"[완료] 팩스번호 그룹 {len(fax_groups)}개, 미매칭 파일 {len(unmatched)}개, "
          f"엑셀 참고 사업장 {len(known_sites)}개 -> {out_csv}")


if __name__ == "__main__":
    main()
