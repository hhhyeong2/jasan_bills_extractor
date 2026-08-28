# jasan_bill_extractor — Phase 0~5 (spec.md 대응)

`spec.md`의 Phase 0~5(사업장 마스터, PoC, 검증 파이프라인, 사업장 매칭/이력 이상탐지,
엑셀 기입/예외 리포트, GUI)에 대응하는 실행 코드입니다.

## 폴더 구성

```
jasan_bill_extractor/
├── config.py                     # API 키/경로/임계값 설정
├── gui/app.py                    # Phase 5: tkinter 데스크톱 앱 (비개발자용 진입점)
├── build/build_exe.spec          # Phase 5: PyInstaller 빌드 설정 (Windows에서 실행해야 함)
├── scripts/
│   ├── build_site_master_draft.py   # Phase 0: site_master 초안 생성
│   ├── run_poc.py                    # Phase 1: 추출 PoC 실행기
│   ├── run_pipeline.py               # Phase 2~3: 전처리→추출→검증→매칭→이력 배치 실행기
│   └── run_export.py                 # Phase 4: 엑셀 기입 + 예외 리포트 생성기
├── preprocess/image_prep.py      # TIFF 프레임 분리 + 화질 보정
├── extract/                      # VisionExtractor (스키마/프롬프트/API 래퍼)
├── validate/                     # ArithmeticValidator, CrossFieldValidator, HistoryAnomalyDetector
├── match/                        # SiteMatcher, site_master 로더
├── mapping/field_combiner.py     # 전력기금+연체료 등 합산 (spec §1.1)
├── audit/logger.py               # 원본값/합산근거 JSONL 로그
├── writer/excel_writer.py        # 경기북부 엑셀양식에 자동 기입
├── review/exception_queue.py     # 예외 리포트 + 엑셀 하이라이트
├── data/site_master.csv          # Phase 0 산출물
├── requirements.txt
└── .env.example
```

## 0. 설치

```bash
cd jasan_bill_extractor
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # ANTHROPIC_API_KEY 값을 채워넣으세요
```

## 실행 방법 A — GUI (비개발자용, Phase 5)

```bash
python3 gui/app.py
```

(`python3 -m gui.app`도 되지만, 그러려면 `gui/__init__.py`가 정상적으로 존재해야 합니다.
`No module named 'gui'` 오류가 나면 압축/복사 과정에서 그 파일이 빠졌을 가능성이 높으니
위 방식(`python3 gui/app.py`)을 쓰세요 — 패키지 인식 여부와 무관하게 항상 동작합니다.)

1. Anthropic API 키 입력 (체크박스 선택 시 OS 자격증명 저장소에 암호화 저장, 다음 실행부터 자동 입력)
2. 고지서(TIFF) 폴더, 경기북부 엑셀양식 파일, 결과 저장 폴더 선택
3. 처리할 파일 수 확인 (실행 전 예상 API 비용 안내 — 실제 Anthropic 요금이 청구됨)
4. 실행 → 진행률/로그 확인 → 완료 후 "결과 폴더 열기"

결과 폴더 안에 다음이 생성됩니다:
- `pipeline_out/pipeline_results.csv` — 문서별 추출·검증 결과
- `export_out/경기북부_엑셀양식_자동입력.xlsx` — 자동기입 + "검토필요" 시트(하이라이트)
- `export_out/exception_report.csv`, `export_out/audit_log.jsonl`
- **`export_out/처리완료/`, `export_out/검토필요/`, `export_out/제외/`** — 원본 고지서 파일을
  결과에 따라 복사해 분류한 폴더(원본은 그대로 두고 복사만 함). 한 파일 안에 문서가
  여러 건이면 그중 하나라도 재검토 대상이면 파일 전체가 "검토필요"로 분류됩니다.

### exe로 배포하기 (Windows 전용)

PyInstaller는 크로스 컴파일이 안 되므로 **Windows 업무용 PC(또는 동일 OS 빌드 머신)에서**:

```bash
pip install -r requirements.txt
pyinstaller build/build_exe.spec
```

`dist/jasan_bill_extractor/jasan_bill_extractor.exe`가 생성됩니다. 배포 전 spec.md §1.3 체크리스트
(방화벽 예외, 백신 오탐 대비 코드서명, `data/site_master.csv` 동봉 여부)를 확인하세요.

## 실행 방법 B — CLI (개발/디버깅용)

```bash
# Phase 2~3: 추출 + 검증 + 사업장매칭 + 이력이상탐지
python3 scripts/run_pipeline.py --input-dir "../bills_png" --output-dir ./pipeline_out --limit 30

# Phase 4: 엑셀 기입 + 예외 리포트 (API 재호출 없음, 위 결과 CSV 재사용)
python3 scripts/run_export.py --pipeline-csv ./pipeline_out/pipeline_results.csv \
    --output-dir ./export_out --excel-template "../경기북부 엑셀양식.xlsx"
```

`run_pipeline.run_pipeline()` / `run_export.run_export()` 함수는 GUI에서도 그대로 재사용되는
공용 로직입니다 (진행률 콜백/로그 콜백 인자 지원).

## Phase 0 — 사업장 마스터

`data/site_master.csv`가 완성본입니다 (팩스번호/납부자번호 exact 매칭 + 상호명 fuzzy 매칭 결과,
매칭 확인이 필요한 행은 매칭비고 열에 후보와 이유가 남아있음). 갱신 방법은 코드 주석과
`scripts/build_site_master_draft.py` docstring 참고.

## Phase 1 — VisionExtractor PoC

```bash
python3 scripts/run_poc.py --input-dir "../bills_png" --output-dir ./poc_out --limit 5 --dry-run  # 비용 없음
python3 scripts/run_poc.py --input-dir "../bills_png" --output-dir ./poc_out --limit 30            # 실제 추출
```

## 주의사항 (spec.md §1.5 관련)

현재 파이프라인은 페이지 전체 이미지를 그대로 Claude Vision에 전송합니다 — 상호명/주소 등
헤더 정보도 함께 전송됩니다. **실제 운영 데이터(진짜 고객 주소가 담긴 고지서)를 처리하기 전에,
사내 법무팀과 §1.5의 크롭/마스킹 정책(`preprocess.pii_guard`, 아직 미구현) 적용 범위를 먼저
확정하세요.**
