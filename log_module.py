from __future__ import annotations

import atexit
import traceback
from datetime import datetime
from pathlib import Path
from threading import Lock
import sys
from view import View


class LogModule:
    def __init__(self, view: View = None):
        self._lock = Lock()
        self._log_file = None
        self._log_path = None
        
        self.view = view

    def log(self, msg: str, force_file_open: bool = True) -> None:
        if force_file_open and self._log_file is None:
            self._open_log_file()
            atexit.register(self._close_log_file)
        with self._lock:
            timestamp = datetime.now().strftime("%H:%M:%S")
            if self._log_file is not None:
                self._log_file.write(f"{timestamp}: {msg}\n")
            if self.view is not None:
                self.view.log(f"{timestamp}: {msg}\n")


    def log_traceback(self, exc: BaseException | None = None, force_file_open: bool = True) -> None:
        if exc is None:
            trace_text = traceback.format_exc()
        else:
            trace_text = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
        self.log(f"[TRACEBACK]\n{trace_text.rstrip()}", force_file_open)
    

    def _open_log_file(self) -> None:
        if self._log_file is not None:
            return
        timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        filename = f"log_{timestamp}.txt"
        if getattr(sys, "frozen", False):
            base_dir = Path(sys.executable).resolve().parent
        else:
            base_dir = Path(__file__).resolve().parent
        log_dir = base_dir / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = log_dir / filename
        self._log_file = self._log_path.open("a", encoding="utf-8", buffering=1)


    def _close_log_file(self) -> None:
        if self._log_file is None:
            return
        try:
            self._log_file.flush()
        finally:
            self._log_file.close()
            self._log_file = None


if __name__ == "__main__":
    log_module = LogModule()
    log_module.log("[DEBUG] python log.py")

