# build/build_exe.spec
# ---------------------------------
# PyInstaller 빌드 설정 (spec.md §1.3, §6 Phase 5).
#
# 중요: PyInstaller는 크로스 컴파일을 지원하지 않는다 - 실행 대상(Windows 업무용 PC)과
# 같은 OS에서 빌드해야 한다. 즉 이 spec 파일은 반드시 Windows 머신에서 실행해야
# Windows용 .exe가 나온다 (Mac에서 실행하면 Mac용 바이너리가 나온다).
#
# 사용법 (Windows, jasan_bill_extractor 폴더에서):
#   pip install -r requirements.txt
#   pyinstaller build/build_exe.spec
#   -> dist/jasan_bill_extractor.exe (파일 하나) 생성
#
# onefile(단일 파일) 방식을 쓰는 이유: 예전 onedir(폴더) 방식은 .exe와 _internal 폴더가
# 분리돼 있어서, 사용자가 .exe만 다른 곳으로 복사/이동하면
# "python3XX.dll ... 지정된 모듈을 찾을 수 없습니다" 오류가 난다(가장 흔한 배포 실패
# 원인). onefile은 실행 시 임시폴더에 자동으로 풀어서 실행하므로 이 문제가 없다.
# 대신 최초 실행 속도가 조금 느리고, onedir보다 백신 오탐 가능성이 약간 더 높을 수 있다
# (spec.md §1.3) - 문제가 되면 코드서명 또는 onedir 방식(exclude_binaries=True + COLLECT)으로
# 되돌리고 "전체 dist 폴더를 통째로 복사하라"고 안내하는 방식을 검토.
#
# 배포 전 체크리스트 (spec.md §1.3):
#   - 사내 방화벽에서 api.anthropic.com 접속 허용 확인 (IT 협의 필요)
#   - 코드서명 인증서가 있으면 서명 (없으면 Defender/백신 오탐 가능성 → IT 예외 등록 요청)
#   - data/site_master.csv가 exe에 내장되므로 별도 배포 불필요 (아래 datas 참고)

import sys
from pathlib import Path

block_cipher = None
PROJECT_ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (PyInstaller가 주입하는 전역)

a = Analysis(  # noqa: F821
    [str(PROJECT_ROOT / "gui" / "app.py")],
    pathex=[str(PROJECT_ROOT), str(PROJECT_ROOT.parent)],
    binaries=[],
    datas=[
        (str(PROJECT_ROOT / "data" / "site_master.csv"), "data"),
        (str(PROJECT_ROOT / ".env.example"), "."),
    ],
    hiddenimports=["keyring.backends", "PIL._tkinter_finder"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="jasan_bill_extractor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI 앱이므로 콘솔 창 숨김
    icon=None,  # 아이콘 파일(.ico) 준비되면 경로 지정
)
