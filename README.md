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

### `main.py`
App entrypoint. Defines BLE UUIDs/constants, owns global instances (`View`, `BleakModule`, `LogModule`, `CsvModule`), registers UI handlers, and orchestrates scan/connect/test flows and abort handling.
All complex protocols are handled here. If you need to fix something, it will usually be here.

### `view.py`
Tkinter/ttkbootstrap UI. Builds widgets, manages UI state and lamp indicators, exposes user input and selected device, and dispatches actions to handlers.
`python view.py` will show you the example UI.

### `bleak_module.py`
BLE adapter around `bleak`. Handles scan/connect/reconnect, GATT read/write/notify, and Windows unpair logic for `DekodaRemote-*` devices.

### `csv_module.py`
Test result persistence. Maintains one CSV row per device MAC, enforces result literals (`Pass`/`Fail`/`NT`), and writes `test_results.csv`.

### `log_module.py`
Timestamped logging to both UI and rotating log files in `log/`, with traceback support.

**Runtime flow**
1. `main.py` creates `View`, `LogModule`, `BleakModule`, and `CsvModule`, then registers button/table handlers.
2. Scan: `scan_and_update_device_list()` calls `BleakModule.scan()`, filters devices by name prefix `DekodaRemote-`, and updates the table.
3. Connect: `connect_and_read_device_info()` connects, enables test mode, reads device info GATT characteristics, and writes initial CSV fields.
4. Tests: `fp_add_and_event_session()`, `delete_fingerprint()`, and `factory_reset_and_unpair()` use GATT notifications and writes, update UI lamps/status, and update CSV results.
5. Main loop: `View.update()` is called in an async loop with a 60 FPS tick (`FRAMERATE`) until the window closes.

**How to add a test**

Add UI
- Create a button to trigger the test in `view.py`
- Wire the button with `lambda _event: self._dispatch("some_custom_action_string")`. This design separates the UI with logic.
- To prevent the button to be called twice, define the button's desired state in `set_state`.


Define test. The test flow should contain, with this specific order:

- Sanitize
  If the requirements are not met, make the function return early. For example, `add_fingerprint` function checks for bleak connection and user name validity.

    *The following is highly recommended to be wrapped in `try-except` statement.*
- `_mark_test_start()`

- view(ui) changes, such as `view.set_state`.

- define `event` that will be awaited to mark test finish. It is especially useful when you should wait for some callback.

- define `_cb`, a callback function for GATT notifications. Your test logic will mainly be here.

- Trigger the test with `await bleak.start_notify` and `await bleak.write_gatt_char`

- Await the test by `await wait_or_abort(event)`. This will stall the function until the test is finished by `event.set` or BLE disconnection.

    In `except` and `finally` statements, fill out your ui logic, logs and csv updates.

    In `finally`, you **should** state `_mark_test_end` and `await bleak.stop_notify_silent` to make sure the test ends without any loose ends.

## Test workflow in a nutshell

```python
# globals that will manage test workflow
res_testname: TestResult = "NT"
event_testname: asyncio.Event | None = asyncio.Event()


# this callback is triggered when the device sends data
def _cb_testname(sender, data):
    global res_testname
    try:
        # parse the data and set `res_testname` to "Pass" or "Fail".
    
    except UnicodeDecodeError:
        # set `res_testname` to "Fail" and set view(ui). Optionally log data.
        res_testname = "Fail"
        view.var_testname.set("Received non UTF-8 data")
        view.set_lamp(view.lamp_testname, 'fail')
        log.log(f"[ERROR] Received non-UTF-8 data: {data}")
    
    except Exception as exc:
        # same as above.
        res_testname = "Fail"
        view.var_fp_addition.set("Error in notification")
        view.set_lamp(view.lamp_fp_addition, 'fail')
        log.log(f"[ERROR] Processing notification failed: {exc}")
        log.log_traceback(exc)
    
    finally:
        # set event (this is important!)
        # the main flow will await for this event. That is, it will await until this callback process is fully finished
        event_testname.set()


async def testname() -> bool:
    # Exit early if the device is not connected. You may add more early-exit codes if you like
    if not bleak.is_connected:
        log.log(f"[ERROR] Invalid client")
        return False

    try:
        global event_testname, res_testname

        # `_mark_test_start()` is crucial for test that should abort when the device disconnects unexpectedly.
        _mark_test_start()

        # set view(ui) to test mode, and optionally log it
        view.set_state("busy-testing-testname")
        view.set_lamp(view.lamp_testname, 'testing')
        view.var_testname.set("Testing...")
        log.log(f"[INFO] === [[testname]] start ===")

        # initalize event and result string
        event_testname = asyncio.Event()
        res_testname = "NT"

        # if needed, use `write_gatt_char` or `read_gatt_char` to trigger the desired test
        await bleak.write_gatt_char(SOME_UUID, some_data)

        # this will wait until the test is complete (by callback), or until the device disconnects
        # if disconnects, it will raise `TestAbortedError` hence the return statement right below will not trigger
        await wait_or_abort(event_testname)

        return res_fp_add == "Pass"
    
    except TestAbortedError:
        # when disconnected. set view and log if needed
        # normally you would not need to log because `wait_or_abort` will already log it for you
        res_testname = "Fail"
        view.var_testname.set("Aborted")
        view.set_lamp(view.lamp_testname, 'fail')
        return False
    
    except Exception as exc:
        # set view and log if needed
        res_testname = "Fail"
        view.var_testname.set("FP add failed")
        view.set_lamp(view.lamp_testname, 'fail')
        log.log(f"[ERROR] [[testname]] failed: {exc}")
        return False
    
    finally:
        # if you called `_mark_test_start()` above, you should call this!!
        _mark_test_end()

        # update csv and optionally log
        log.log(f"[INFO] === Add Fingerprint end ===")
        csv.update_results(fp_addition=res_fp_add)

        # set ui state to idle
        if bleak.is_connected:
            view.set_state("idle-connected")
        else:
            view.set_state("idle-disconnected")
```
