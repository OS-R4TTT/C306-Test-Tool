from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable

from log_module import LogModule

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

COLUMN_ORDER = [
    "test_start",
    "test_finish",
    "device_name",
    "manufacturer_name",
    "serial_number",
    "model_number",
    "mac_address",
    "firmware_ver",
    "fp_addition",
    "session_start",
    "session_stop",
    "fp_deletion",
    "factory_reset",
]

RESULT_FIELDS = {
    "fp_addition",
    "session_start",
    "session_stop",
    "fp_deletion",
    "factory_reset",
}
RESULT_LITERALS = {"Pass", "Fail", "NT"}


def _now_ts() -> str:
    return datetime.now().strftime(TIMESTAMP_FORMAT)


def _empty_row() -> dict[str, str]:
    row = {key: "" for key in COLUMN_ORDER}
    for key in RESULT_FIELDS:
        row[key] = "NT"
    return row


class CsvModule:
    def __init__(self, path: str | Path = "test_results.csv", log: LogModule | None = None) -> None:
        self.path = Path(path)
        self.log = log
        self.current_mac: str | None = None
        self.current_row: dict[str, str] | None = None
        self._ensure_file()

    def start_test(self, device_name: str, mac_address: str) -> None:
        if not mac_address:
            self._log("[CSV] start_test called without mac_address")
            return
        row = _empty_row()
        row["test_start"] = _now_ts()
        row["device_name"] = device_name or ""
        row["mac_address"] = mac_address
        self.current_mac = mac_address
        self.current_row = row
        self._upsert_row(row)

    def update_basic_info(
        self,
        device_name: str,
        manufacturer_name: str,
        serial_number: str,
        model_number: str,
        mac_address: str,
        firmware_ver: str,
    ) -> None:
        row = self._ensure_current_row(mac_address)
        if row is None:
            return
        row["device_name"] = device_name or row["device_name"]
        row["manufacturer_name"] = manufacturer_name or row["manufacturer_name"]
        row["serial_number"] = serial_number or row["serial_number"]
        row["model_number"] = model_number or row["model_number"]
        row["mac_address"] = mac_address or row["mac_address"]
        row["firmware_ver"] = firmware_ver or row["firmware_ver"]
        self._upsert_row(row)

    def update_results(self, **results: str | None) -> None:
        if not results:
            return
        row = self._ensure_current_row(self.current_mac)
        if row is None:
            return
        for key, value in results.items():
            if key not in RESULT_FIELDS:
                continue
            literal = self._ensure_literal(value, key)
            if literal is None:
                continue
            row[key] = literal
        self._upsert_row(row)

    def finish_test(self, factory_reset_result: str | None) -> None:
        row = self._ensure_current_row(self.current_mac)
        if row is None:
            return
        row["test_finish"] = _now_ts()
        literal = self._ensure_literal(factory_reset_result, "factory_reset")
        if literal is not None:
            row["factory_reset"] = literal
        self._upsert_row(row)

    def read_all(self) -> list[dict[str, str]]:
        return self._read_rows()

    def _ensure_current_row(self, mac_address: str | None) -> dict[str, str] | None:
        if mac_address is None:
            self._log("[CSV] No active test row")
            return None
        if self.current_row is None or self.current_mac != mac_address:
            self.current_mac = mac_address
            rows = self._read_rows()
            for row in rows:
                if row.get("mac_address") == mac_address:
                    self.current_row = row
                    break
            if self.current_row is None:
                self.current_row = _empty_row()
                self.current_row["mac_address"] = mac_address
        return self.current_row

    def _ensure_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() or self.path.stat().st_size == 0:
            self._write_rows([])

    def _read_rows(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        with self.path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return []
            rows = []
            for row in reader:
                rows.append({key: row.get(key, "") for key in COLUMN_ORDER})
            return rows

    def _write_rows(self, rows: Iterable[dict[str, str]]) -> None:
        with self.path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMN_ORDER)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in COLUMN_ORDER})

    def _upsert_row(self, row: dict[str, str]) -> None:
        mac = row.get("mac_address")
        if not mac:
            self._log("[CSV] Attempted to write row without mac_address")
            return
        rows = self._read_rows()
        replaced = False
        for idx, existing in enumerate(rows):
            if existing.get("mac_address") == mac:
                rows[idx] = row
                replaced = True
                break
        if not replaced:
            rows.append(row)
        self._write_rows(rows)

    def _log(self, msg: str) -> None:
        if self.log is not None:
            self.log.log(msg)

    def _ensure_literal(self, value: str | None, field: str) -> str | None:
        if value is None:
            return "NT"
        if value not in RESULT_LITERALS:
            self._log(f"[CSV] Invalid result literal for {field}: {value}")
            return None
        return value
