# C306 Test Tool

## Project installation guide

This project requires Python 3.12+ in Windows environment

1. Create virtual environment

```
python -m venv venv
.\venv\Scripts\activate
```

2. Install required packages

```
pip install -r requirements.txt
```

3. Run program

```
python main.py
```

## Code structure

- `main.py`: App entrypoint. Defines BLE UUIDs/constants, owns global instances (`View`, `BleakModule`, `LogModule`, `CsvModule`), registers UI handlers, and orchestrates scan/connect/test flows and abort handling.
- `view.py`: Tkinter/ttkbootstrap UI. Builds widgets, manages UI state and lamp indicators, exposes user input and selected device, and dispatches actions to handlers.
- `bleak_module.py`: BLE adapter around `bleak`. Handles scan/connect/reconnect, GATT read/write/notify, and Windows unpair logic for `DekodaRemote-*` devices.
- `csv_module.py`: Test result persistence. Maintains one CSV row per device MAC, enforces result literals (`Pass`/`Fail`/`NT`), and writes `test_results.csv`.
- `log_module.py`: Timestamped logging to both UI and rotating log files in `log/`, with traceback support.

**Runtime flow**
1. `main.py` creates `View`, `LogModule`, `BleakModule`, and `CsvModule`, then registers button/table handlers.
2. Scan: `scan_and_update_device_list()` calls `BleakModule.scan()`, filters devices by name prefix `DekodaRemote-`, and updates the table.
3. Connect: `connect_and_read_device_info()` connects, enables test mode, reads device info GATT characteristics, and writes initial CSV fields.
4. Tests: `fp_add_and_event_session()`, `delete_fingerprint()`, and `factory_reset_and_unpair()` use GATT notifications and writes, update UI lamps/status, and update CSV results.
5. Main loop: `View.update()` is called in an async loop with a 60 FPS tick (`FRAMERATE`) until the window closes.
