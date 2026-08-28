"""
gui/app.py
---------------------------------
DesktopApp (spec.md §3, §6 Phase 5): 비개발자용 tkinter 데스크톱 실행 UI.

흐름: API 키 입력(OS 자격증명 저장소에 암호화 저장) -> 고지서 폴더 선택 -> 경기북부
엑셀양식 파일 선택 -> 출력 폴더 선택 -> 실행 -> 진행률 표시 -> 결과 엑셀/예외 리포트 폴더 열기.

내부적으로 jasan_bill_extractor.pipeline.run_pipeline() (Phase 2~3: 전처리/추출/검증/매칭/이력)과
jasan_bill_extractor.exporter.run_export() (Phase 4: 엑셀 기입/예외 리포트)를 백그라운드
스레드에서 순서대로 호출한다. 실제 API 호출이 있으므로(spec.md §1.4 - 비용 발생) 실행 전
파일 수/예상 비용을 안내하고 확인을 받는다.

중요: pipeline.py/exporter.py는 반드시 jasan_bill_extractor 패키지 정식 모듈(패키지 최상위,
scripts/ 폴더 아님)에서 import해야 한다. scripts/의 느슨한 스크립트를 sys.path 조작 +
bare import로 불러오면 PyInstaller가 정적 분석으로 그 임포트를 추적하지 못해 번들에서
빠지고 "ModuleNotFoundError: No module named 'run_pipeline'"가 난다 (Phase 5 실배포 중
Windows exe에서 실제로 발생, 아래 방식으로 수정함).
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

GUI_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = GUI_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT.parent))

import jasan_bill_extractor.pipeline as run_pipeline  # noqa: E402
import jasan_bill_extractor.exporter as run_export  # noqa: E402

try:
    import keyring
    _KEYRING_OK = True
except ImportError:
    _KEYRING_OK = False

KEYRING_SERVICE = "jasan_bill_extractor"
KEYRING_USERNAME = "ANTHROPIC_API_KEY"

DEFAULT_INPUT_DIR = PROJECT_ROOT.parent / "bills_png"
DEFAULT_EXCEL_TEMPLATE = PROJECT_ROOT.parent / "경기북부 엑셀양식.xlsx"

# 페이지 1장당 대략적인 비용 추정치 (spec.md §1.4 참고, Sonnet 5 기준)
EST_COST_KRW_PER_FILE = 25


class DesktopApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("jasan_bills 전기료 고지서 자동 추출 도구")
        self.root.geometry("720x560")

        self.log_queue: queue.Queue = queue.Queue()
        self.worker_thread: threading.Thread = None
        self.last_output_dir: Path = None

        self._build_widgets()
        self._load_saved_api_key()
        self.root.after(100, self._poll_log_queue)

    # ---------------------------------------------------------------- UI 구성
    def _build_widgets(self):
        pad = {"padx": 8, "pady": 4}

        frm_key = ttk.LabelFrame(self.root, text="1. Anthropic API 키")
        frm_key.pack(fill="x", **pad)
        self.var_api_key = tk.StringVar()
        ttk.Entry(frm_key, textvariable=self.var_api_key, show="*", width=50).pack(
            side="left", padx=8, pady=6, fill="x", expand=True
        )
        self.var_save_key = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_key, text="이 컴퓨터에 안전하게 저장", variable=self.var_save_key).pack(
            side="left", padx=8
        )
        if not _KEYRING_OK:
            ttk.Label(frm_key, text="(keyring 미설치 - 이번 세션에만 사용됨)", foreground="orange").pack(side="left")

        frm_paths = ttk.LabelFrame(self.root, text="2. 파일/폴더 선택")
        frm_paths.pack(fill="x", **pad)

        self.var_input_dir = tk.StringVar(value=str(DEFAULT_INPUT_DIR) if DEFAULT_INPUT_DIR.exists() else "")
        self._path_row(frm_paths, "고지서 폴더", self.var_input_dir, self._browse_input_dir)

        self.var_excel_template = tk.StringVar(
            value=str(DEFAULT_EXCEL_TEMPLATE) if DEFAULT_EXCEL_TEMPLATE.exists() else ""
        )
        self._path_row(frm_paths, "경기북부 엑셀양식", self.var_excel_template, self._browse_excel_template)

        self.var_output_dir = tk.StringVar(value=str(PROJECT_ROOT / "output"))
        self._path_row(frm_paths, "결과 저장 폴더", self.var_output_dir, self._browse_output_dir)

        frm_opts = ttk.LabelFrame(self.root, text="3. 처리 옵션")
        frm_opts.pack(fill="x", **pad)
        ttk.Label(frm_opts, text="처리할 파일 수:").pack(side="left", padx=8)
        self.var_limit = tk.IntVar(value=30)
        ttk.Spinbox(frm_opts, from_=1, to=2000, textvariable=self.var_limit, width=8).pack(side="left")
        ttk.Label(frm_opts, text="지급일자 = 오늘 기준 +").pack(side="left", padx=(16, 0))
        self.var_pay_days = tk.IntVar(value=3)
        ttk.Spinbox(frm_opts, from_=0, to=30, textvariable=self.var_pay_days, width=5).pack(side="left")
        ttk.Label(frm_opts, text="영업일").pack(side="left")

        frm_run = ttk.Frame(self.root)
        frm_run.pack(fill="x", **pad)
        self.btn_run = ttk.Button(frm_run, text="실행", command=self._on_run_clicked)
        self.btn_run.pack(side="left")
        self.btn_open_output = ttk.Button(
            frm_run, text="결과 폴더 열기", command=self._on_open_output_clicked, state="disabled"
        )
        self.btn_open_output.pack(side="left", padx=8)

        self.progress = ttk.Progressbar(self.root, mode="determinate")
        self.progress.pack(fill="x", **pad)

        frm_log = ttk.LabelFrame(self.root, text="진행 로그")
        frm_log.pack(fill="both", expand=True, **pad)
        self.txt_log = tk.Text(frm_log, height=16, state="disabled", wrap="word")
        self.txt_log.pack(fill="both", expand=True, padx=4, pady=4)

    def _path_row(self, parent, label, var, browse_cmd):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=3)
        ttk.Label(row, text=label, width=16).pack(side="left")
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="찾아보기", command=browse_cmd).pack(side="left")

    # ---------------------------------------------------------------- 파일 선택
    def _browse_input_dir(self):
        d = filedialog.askdirectory(title="고지서(TIFF) 폴더 선택")
        if d:
            self.var_input_dir.set(d)

    def _browse_excel_template(self):
        f = filedialog.askopenfilename(title="경기북부 엑셀양식 파일 선택", filetypes=[("Excel", "*.xlsx")])
        if f:
            self.var_excel_template.set(f)

    def _browse_output_dir(self):
        d = filedialog.askdirectory(title="결과를 저장할 폴더 선택")
        if d:
            self.var_output_dir.set(d)

    # ---------------------------------------------------------------- API 키
    def _load_saved_api_key(self):
        if _KEYRING_OK:
            try:
                saved = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
                if saved:
                    self.var_api_key.set(saved)
                    return
            except Exception:  # noqa: BLE001 - 키체인 접근 실패해도 앱은 계속 동작해야 함
                pass
        env_key = os.environ.get("ANTHROPIC_API_KEY")
        if env_key:
            self.var_api_key.set(env_key)

    # ---------------------------------------------------------------- 실행
    def _on_run_clicked(self):
        api_key = self.var_api_key.get().strip()
        input_dir = Path(self.var_input_dir.get().strip())
        excel_template = Path(self.var_excel_template.get().strip())
        output_dir = Path(self.var_output_dir.get().strip())
        limit = self.var_limit.get()

        if not api_key:
            messagebox.showerror("오류", "Anthropic API 키를 입력하세요.")
            return
        if not input_dir.exists():
            messagebox.showerror("오류", f"고지서 폴더를 찾을 수 없습니다:\n{input_dir}")
            return
        if not excel_template.exists():
            messagebox.showerror("오류", f"엑셀양식 파일을 찾을 수 없습니다:\n{excel_template}")
            return

        n_files = len(list(input_dir.glob("*.tif"))) + len(list(input_dir.glob("*.tiff")))
        n_target = min(n_files, limit)
        est_cost = n_target * EST_COST_KRW_PER_FILE
        proceed = messagebox.askyesno(
            "실행 확인",
            f"{n_target}개 파일을 Claude Vision API로 처리합니다.\n"
            f"예상 비용: 약 {est_cost:,}원 (실측치 아님, spec.md §1.4 대략치 기준)\n\n"
            f"Anthropic API 요금이 실제로 청구됩니다. 계속할까요?",
        )
        if not proceed:
            return

        if self.var_save_key.get() and _KEYRING_OK:
            try:
                keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, api_key)
            except Exception:  # noqa: BLE001
                pass

        os.environ["ANTHROPIC_API_KEY"] = api_key

        self.btn_run.config(state="disabled")
        self.btn_open_output.config(state="disabled")
        self.progress["value"] = 0
        self.progress["maximum"] = n_target
        self._clear_log()

        self.worker_thread = threading.Thread(
            target=self._run_worker,
            args=(input_dir, excel_template, output_dir, limit, self.var_pay_days.get()),
            daemon=True,
        )
        self.worker_thread.start()

    def _run_worker(self, input_dir: Path, excel_template: Path, output_dir: Path, limit: int, pay_days: int):
        def log(msg=""):
            self.log_queue.put(("log", str(msg)))

        def progress_cb(i, total, filename):
            self.log_queue.put(("progress", (i, total, filename)))

        try:
            pipeline_out = output_dir / "pipeline_out"
            result = run_pipeline.run_pipeline(
                input_dir, pipeline_out, limit=limit, log=log, progress_cb=progress_cb,
            )
            if not result["rows"]:
                self.log_queue.put(("done", None))
                return

            export_out = output_dir / "export_out"
            run_export.run_export(
                result["csv_path"], export_out, excel_template=excel_template,
                payment_business_days=pay_days, bills_dir=input_dir, log=log,
            )
            self.log_queue.put(("done", export_out))
        except Exception as e:  # noqa: BLE001
            import traceback
            log(f"\n[오류] 처리 중 예외 발생: {e}")
            log(traceback.format_exc())
            self.log_queue.put(("done", None))

    # ---------------------------------------------------------------- 로그/진행률 폴링
    def _poll_log_queue(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "progress":
                    i, total, filename = payload
                    self.progress["maximum"] = total
                    self.progress["value"] = i
                    self._append_log(f"[{i}/{total}] {filename} 처리 중...")
                elif kind == "done":
                    self.btn_run.config(state="normal")
                    if payload is not None:
                        self.last_output_dir = payload
                        self.btn_open_output.config(state="normal")
                        messagebox.showinfo("완료", f"처리가 끝났습니다.\n결과: {payload}")
                    else:
                        messagebox.showwarning("완료", "처리는 끝났지만 결과가 없습니다. 로그를 확인하세요.")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_log_queue)

    def _append_log(self, msg: str):
        self.txt_log.config(state="normal")
        self.txt_log.insert("end", msg + "\n")
        self.txt_log.see("end")
        self.txt_log.config(state="disabled")

    def _clear_log(self):
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.config(state="disabled")

    # ---------------------------------------------------------------- 결과 폴더 열기
    def _on_open_output_clicked(self):
        if not self.last_output_dir:
            return
        path = str(self.last_output_dir)
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606 (Windows 전용)
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            else:
                subprocess.run(["xdg-open", path], check=False)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("오류", f"폴더를 열 수 없습니다: {e}")


def main():
    root = tk.Tk()
    DesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
