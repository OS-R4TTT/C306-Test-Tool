import asyncio
import json
import tkinter as tk
from view import View, State
from bleak_module import BleakModule
from log_module import LogModule
from csv_module import CsvModule
from typing import Literal
from enum import Enum

VERSION = "v1.2.0"

UUID_MODEL_INFO = "00002a24-0000-1000-8000-00805f9b34fb"
UUID_SERIAL_NO = "00002a25-0000-1000-8000-00805f9b34fb"
UUID_SW_VER = "00002a28-0000-1000-8000-00805f9b34fb"
UUID_MANUFACTURER = "00002a29-0000-1000-8000-00805f9b34fb"
UUID_PNP_ID = "00002a50-0000-1000-8000-00805f9b34fb"

UUID_FINGERPRINT_ADDITION = "1994de3b-ae1f-41ec-aeb6-6d22e1a31024"
UUID_FINGERPRINT_DELETION = "b7e22e5e-9eb8-4a2f-8198-62faeec1df82"
UUID_FACTORY_RESET = "f7611e0d-dc51-4a4e-8c53-917aa82dfc43"
UUID_EVENT_MANAGEMENT = "ac6c355a-2d32-4dba-a9a9-261581e3dc71"
UUID_REMOTE_DIAGNOSTIC = "d06cbe80-6177-43c0-af7c-06e7c62031be"

UUID_OHSUNG_TESTMODE_NOTIFY = "0000ffa1-0000-1000-8000-00805f9b34fb"
UUID_OHSUNG_TESTMODE = "0000ffa2-0000-1000-8000-00805f9b34fb"

MAX_CONNECT_RETRY = 3
FRAMERATE = 1 / 60


TestResult = Literal['Pass', 'Fail', 'NT']

# ===============================================
#  Modules
# ===============================================

view: View = None
log: LogModule = None
bleak: BleakModule = None
csv: CsvModule | None = None

# ===============================================
#  Globals
# ===============================================

connect_lock: bool = False # ensures connect does not happen twice
disconnect_expected = False

fingerprint_storage: dict[str, str] = {}

event_fp_add: asyncio.Event | None = asyncio.Event()
event_fp_del: asyncio.Event | None = asyncio.Event()
event_ev: asyncio.Event | None = asyncio.Event()
event_fr: asyncio.Event | None = asyncio.Event()

res_fp_add: TestResult = "NT"
res_fp_del: TestResult = "NT"
res_ev_start: TestResult = "NT"
res_ev_stop: TestResult = "NT"
res_fr: TestResult = "NT"

fp_del_target_uuid = "" # caches uuid that should be erased


# ===============================================
#  Test wait, abort Logic (disconnect/reconnect)
#  - `_mark_test_start`, `_mark_test_end` are only used for tests that use notification.
# ===============================================

class TestAbortedError(RuntimeError):
    """Error wrapper (Custom)"""
    pass

test_abort_event: asyncio.Event | None = None
tests_in_progress = 0


def _mark_test_start() -> None:
    """
    Should be called at start of test
    """
    global tests_in_progress
    tests_in_progress += 1
    if tests_in_progress == 1 and test_abort_event is not None:
        test_abort_event.clear()


def _mark_test_end() -> None:
    """
    Should be called at end of test, usually on `finally` statements
    """
    global tests_in_progress
    tests_in_progress = max(0, tests_in_progress - 1)


def on_disconnect() -> None:
    """
    This function will run in BleakModule after it disconnects.
    Handles view or main attributes that BleakModule cannot access.
    """
    view.set_state('idle-disconnected')
    if tests_in_progress > 0 and test_abort_event is not None:
        if not test_abort_event.is_set():
            if log is not None:
                log.log("Disconnect happened while testing. Aborting test")
            test_abort_event.set()


async def on_reconnect() -> None:
    """
    This function will run in BleakModule after it reconnects.
    """
    # We don't need to start notifications on reconnect

    if len(fingerprint_storage) == 0:
        view.set_state("idle-connected")
    else:
        await handle_event_management()


async def wait_or_abort(event: asyncio.Event) -> None:
    """
    Tests will await until the device is disconnected (goes to sleep) or the `event` is set.
    """
    if test_abort_event is None:
        await event.wait()
        return
    if test_abort_event.is_set():
        raise TestAbortedError()
    if event.is_set():
        return

    done, pending = await asyncio.wait(
        [asyncio.create_task(event.wait()), asyncio.create_task(test_abort_event.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()

    if test_abort_event.is_set() and not event.is_set():
        raise TestAbortedError()

# ===============================================
#  Main Logic
#  - All functions returns a boolean that indicates test success/failure
#  - tests are wrapped inside `try-except` statements.
# ===============================================

async def scan_and_update_device_list() -> bool:
    try:
        view.set_state("busy-scanning")
        view.clear_device_list()
        log.log(f"[INFO] Scanning...")
        await bleak.scan()
        filtered_devices = {
            key: (device, adv_data)
            for key, (device, adv_data) in bleak.devices.items()
            if device.name is not None and "DekodaRemote-" in device.name
        }
        log.log(f"[INFO] Scan complete. Found {len(bleak.devices)} device(s)\n" \
                + f" - Found {len(filtered_devices)} DekodaRemote(s)"
        )
        view.update_device_list(filtered_devices)
        view.set_state("idle-disconnected")
        return True
    except Exception as exc:
        log.log(f"[ERROR] Scan failed: {exc}")
        log.log_traceback(exc)
        view.set_state("idle-disconnected")
        return False


async def connect_and_read_device_info() -> bool:
    global connect_lock
    if connect_lock:
        return False
    if view.selected_device is None: # ignore double clicks on blank space
        return False
    connect_lock = True

    try:
        view.set_state("busy-connecting")
        name, address = view.selected_device
        recent_exc = None
        for idx in range(MAX_CONNECT_RETRY):
            try:
                log.log(f"[INFO] Connecting... {idx+1}")
                await bleak.connect(address)
                break
            except Exception as exc:
                recent_exc = exc
        if not bleak.is_connected:
            raise recent_exc
        log.log(f"[INFO] Connect complete with {name} ({address})")
        csv.start_test(device_name=name, mac_address=address)
        view.clear_device_list()
        view.clear_test_result()
        #await asyncio.sleep(1.0)
        #await bleak.write_gatt_char(UUID_OHSUNG_TESTMODE_NOTIFY, bytes([0xF3]))
        #log.log(f"[INFO] Ohsung testmode command sent")
        await start_notifications()
        await read_device_info()
        return True
    except Exception as exc:
        log.log(f"[ERROR] Connect failed: {exc}")
        log.log_traceback(exc)
        view.set_state("idle-disconnected")
        return False
    finally:
        connect_lock = False
        if bleak.is_connected:
            view.set_state("idle-connected")
        else:
            view.set_state("idle-disconnected")


async def start_notifications() -> bool:
    if not bleak.is_connected:
        log.log(f"[ERROR] Invalid Client")
        return False
    
    try:
        log.log(
            f"[INFO] Enabling notifications for UUID:\n" \
            + f"UUID_FINGERPRINT_ADDITION ({UUID_FINGERPRINT_ADDITION})\n" \
            + f"UUID_FINGERPRINT_DELETION ({UUID_FINGERPRINT_DELETION})\n" \
            + f"UUID_EVENT_MANAGEMENT     ({UUID_EVENT_MANAGEMENT})\n" \
            + f"UUID_FACTORY_RESET        ({UUID_FACTORY_RESET})\n"
        )
        await bleak.start_notify(UUID_FINGERPRINT_ADDITION, _cb_add_fingerprint)
        await bleak.start_notify(UUID_FINGERPRINT_DELETION, _cb_delete_fingerprint)
        await bleak.start_notify(UUID_EVENT_MANAGEMENT, _cb_event_management)
        await bleak.start_notify(UUID_FACTORY_RESET, _cb_factory_reset)
        log.log("[INFO] Enable notifications complete")
        return True
    except Exception as exc:
        log.log(f"[ERROR] Enable notifications failed: {exc}")
        log.log_traceback(exc)
    finally:
        if bleak.is_connected:
            view.set_state("idle-connected")
        else:
            view.set_state("idle-disconnected")


async def read_device_info() -> bool:
    """
    Reads the following attributes. If one fails, aborts the whole read process.
    - Manufacturer name
    - Serial number
    - Model number
    - FW version

    It also reads 'Device name' and 'MAC address', but these do not require reading GATT characteristic.
    """
    if not bleak.is_connected:
        log.log(f"[ERROR] Invalid Client")
        return False
    
    try:
        view.set_state("busy-reading-gatt")
        device_name = bleak.client_name
        data = await bleak.read_gatt_char(UUID_MANUFACTURER)
        manufacturer_name = data.decode("utf-8")
        data = await bleak.read_gatt_char(UUID_SERIAL_NO)
        serial_no = data.decode("utf-8")
        data = await bleak.read_gatt_char(UUID_MODEL_INFO)
        model_no = data.decode("utf-8")
        mac_address = bleak.client_address
        data = await bleak.read_gatt_char(UUID_SW_VER)
        fw_ver = data.decode("utf-8")

        view.update_device_info(device_name, manufacturer_name, serial_no, model_no, mac_address, fw_ver)
        csv.update_basic_info(
            device_name=device_name,
            manufacturer_name=manufacturer_name,
            serial_number=serial_no,
            model_number=model_no,
            mac_address=mac_address,
            firmware_ver=fw_ver,
        )

        text = ""
        text += f"=== {device_name} Information ===\n" \
                + f" - Device Name     : {device_name}\n" \
                + f" - Manufactuer Name: {manufacturer_name}\n" \
                + f" - Serial Number   : {serial_no}\n" \
                + f" - Model Number    : {model_no}\n" \
                + f" - MAC Address     : {mac_address}\n" \
                + f" - Firmware Ver    : {fw_ver}\n" \
                + f"==========================================="

        log.log(f"[INFO]\n{text}")
        view.set_state("idle-connected")
        return True
    except Exception as exc:
        log.log(f"[ERROR] Read device information failed: {exc}")
        log.log_traceback(exc)
        return False
    finally:
        if bleak.is_connected:
            view.set_state("idle-connected")
        else:
            view.set_state("idle-disconnected")


async def unpair_all_dekoda_remotes() -> bool:
    """
    Triggers when user presses "Dongle Reset".
    You don't need to press this when factory reset was successfully performed.

    Note that this will also clear `fingerprint_storage`.
    """
    try:
        view.set_state("busy-unpairing")
        log.log("[INFO] Unpairing all...")
        await bleak.unpair_all_dekoda_remotes()
        log.log(f"[INFO] Unpair all complete")
        return True
    except Exception as exc:
        log.log(f"[ERROR] Processing all unpair failed: {exc}")
        log.log_traceback(exc)
        return False
    finally:
        fingerprint_storage.clear()
        view.set_state("idle-disconnected")
        view.clear_device_info()
        view.clear_test_result()


def _cb_add_fingerprint(_sender, data):
    """
    This reads `user_name` directly from view, rather than caching it somewhere.

    A malicious user can change `user_name` while fp add process; but we can ignore that.
    """
    global res_fp_add
    try:
        user_name = view.user_name
        data_str = data.decode("utf-8")
        if data_str == "CANCEL":
            res_fp_add = "Fail"
            view.var_fp_addition.set("Add Fingerprint canceled by user")
            view.set_lamp(view.lamp_fp_addition, 'fail')
            log.log(f"[INFO] Add Fingerprint canceled by user")
        else:
            fingerprint_storage[data_str] = user_name
            res_fp_add = "Pass"
            view.update_fingerprint_list(fingerprint_storage)
            view.set_lamp(view.lamp_fp_addition, 'pass')
            log.log(f"[INFO] Registered fingerprint with {data_str} ({user_name})")
    except UnicodeDecodeError:
        res_fp_add = "Fail"
        view.var_fp_addition.set("Received non UTF-8 data")
        view.set_lamp(view.lamp_fp_addition, 'fail')
        log.log(f"[ERROR] Received non-UTF-8 data: {data}")
    except Exception as exc:
        res_fp_add = "Fail"
        view.var_fp_addition.set("Error in notification")
        view.set_lamp(view.lamp_fp_addition, 'fail')
        log.log(f"[ERROR] Processing notification failed: {exc}")
        log.log_traceback(exc)
    finally:
        event_fp_add.set()


async def add_fingerprint() -> bool:
    """
    Adds fingerprint to device and in `fingerprint_storage`.
    """
    if not bleak.is_connected:
        log.log(f"[ERROR] Invalid client")
        return False
    
    # sanitize user_name
    user_name = view.user_name
    if not (bool(user_name) and user_name.isalpha() and len(user_name) <= 8):
        log.log(
            f"[WARN] User Name: {user_name} is invalid (1-8 len, only alphabets allowed)\n" \
            + f"Please enter a valid user name and try again."
        )
        return False

    try:
        global event_fp_add, res_fp_add
        _mark_test_start()
        view.set_state("busy-adding-fingerprint")
        view.set_lamp(view.lamp_fp_addition, 'testing')
        view.var_fp_addition.set("Testing...")
        log.log(f"[INFO] === Add Fingerprint start ===")

        event_fp_add = asyncio.Event()
        res_fp_add = "NT"

        await bleak.write_gatt_char(UUID_FINGERPRINT_ADDITION, user_name.encode("utf-8"))
        await wait_or_abort(event_fp_add) # wait until user presses fp addition
        return res_fp_add == "Pass"
    except TestAbortedError:
        res_fp_add = "Fail"
        view.var_fp_addition.set("Aborted")
        view.set_lamp(view.lamp_fp_addition, 'fail')
        return False
    except Exception as exc:
        res_fp_add = "Fail"
        view.var_fp_addition.set("FP add failed")
        view.set_lamp(view.lamp_fp_addition, 'fail')
        log.log(f"[ERROR] Fingerprint addition failed: {exc}")
        return False
    finally:
        _mark_test_end()
        log.log(f"[INFO] === Add Fingerprint end ===")
        csv.update_results(fp_addition=res_fp_add)
        if bleak.is_connected:
            view.set_state("idle-connected")
        else:
            view.set_state("idle-disconnected")


def _cb_delete_fingerprint(_sender, data):
    global res_fp_del
    try:
        data_str = data.decode("utf-8")
        if data_str == "OK":
            del fingerprint_storage[fp_del_target_uuid]
            # To check all passes for each uuid, set to "Pass" when not "Fail"
            if res_fp_del != "Fail":
                res_fp_del = "Pass"
            log.log(f"[INFO] Remove successful ({data_str})")
        else:
            res_fp_del = "Fail"
            log.log(f"[WARN] Remove failed ({data_str})")
    except UnicodeDecodeError:
        res_fp_del = "Fail"
        log.log(f"[ERROR] Received non-UTF-8 data: {data}")
    except Exception as exc:
        res_fp_del = "Fail"
        log.log(f"[ERROR] Processing notification failed: {exc}")
        log.log_traceback(exc)
    finally:
        event_fp_del.set()


async def delete_fingerprint() -> bool:
    """
    Deletes fingerprint from device and in `fingerprint_stroage`.

    Note that it tries to remove all uuids in `fingerprint_storage`.
    In some cases this can ask the device to erase fingerprints that are not actually registered.
    This will result in remove failed, which will mark the test as 'Failed'.
    """
    if not bleak.is_connected:
        log.log(f"[ERROR] Invalid client")
        return False
    if len(fingerprint_storage) == 0:
        log.log(f"[WARN] No fingerprint added. Ignoring request")
        return False
    
    try:
        global event_fp_del, res_fp_del, fp_del_target_uuid
        _mark_test_start()
        view.set_state("busy-deleting-fingerprint")
        view.set_lamp(view.lamp_fp_deletion, 'testing')
        view.var_fp_deletion.set("Testing...")
        log.log(f"[INFO] === Delete Fingerprint start ===")

        event_fp_del = asyncio.Event()
        res_fp_del = "NT"
        
        # Erase all fingerprints
        for uuid in list(fingerprint_storage.keys()):
            fp_del_target_uuid = uuid
            await bleak.write_gatt_char(UUID_FINGERPRINT_DELETION, fp_del_target_uuid.encode("utf-8"))
        await wait_or_abort(event_fp_del)
        view.var_fp_deletion.set(res_fp_del)
        view.set_lamp(view.lamp_fp_deletion, 'pass' if res_fp_del == "Pass" else 'fail')
        return res_fp_del == "Pass"
    except TestAbortedError:
        res_fp_del = "Fail"
        view.var_fp_deletion.set("Aborted")
        view.set_lamp(view.lamp_fp_deletion, 'fail')
        return False
    except Exception as exc:
        res_fp_del = "Fail"
        view.var_fp_deletion.set("Fail")
        view.set_lamp(view.lamp_fp_deletion, 'fail')
        log.log(f"[ERROR] Fingerprint deletion failed: {exc}")
        return False
    finally:
        _mark_test_end()
        log.log(f"[INFO] === Delete Fingerprint end ===")
        csv.update_results(fp_deletion=res_fp_del)
        if bleak.is_connected:
            view.set_state("idle-connected")
        else:
            view.set_state("idle-disconnected")


async def _cb_event_management(sender, data):
    """
    This callback should be called twice
    """
    global res_ev_start, res_ev_stop
    try:
        data_str = data.decode("utf-8")
        json_data = json.loads(data_str)
        log.log(f"[INFO] Notification from {sender}: {json.dumps(json_data, indent=2)}")

        action = json_data.get("action")
        
        if action == "start":
            res_ev_start = "Pass"
            view.set_lamp(view.lamp_camera, "operating")
            view.set_lamp(view.lamp_event_session_start, "pass")
            view.set_lamp(view.lamp_event_session_stop, "testing")
            view.var_event_session_start.set("Pass")
            view.var_event_session_stop.set("Testing...")
            csv.update_results(session_start=res_ev_start)
            await bleak.write_gatt_char(UUID_EVENT_MANAGEMENT, "IN-PROGRESS".encode("utf-8"))
            await asyncio.sleep(3.0)
            await bleak.write_gatt_char(UUID_EVENT_MANAGEMENT, "UNSET".encode("utf-8"))
            # we should still wait for stop event, so it does not set event
        elif action == "stop":
            res_ev_stop = "Pass"
            view.set_lamp(view.lamp_camera, "not-operating")
            view.set_lamp(view.lamp_event_session_stop, "pass")
            view.var_event_session_stop.set("Pass")
            csv.update_results(session_stop=res_ev_stop)
            await bleak.write_gatt_char(UUID_EVENT_MANAGEMENT, "UNSET".encode("utf-8"))
            event_ev.set()
        elif action == "cancel":
            res_ev_start = "Fail"
            res_ev_stop = "Fail"
            view.set_lamp(view.lamp_camera, "not-operating")
            view.set_lamp(view.lamp_event_session_start, "fail")
            view.set_lamp(view.lamp_event_session_stop, "fail")
            view.var_event_session_start.set("Canceled by user")
            view.var_event_session_stop.set("Canceled by user")
            event_ev.set()
        else:
            res_ev_start = "Fail"
            res_ev_stop = "Fail"
            view.set_lamp(view.lamp_event_session_start, "fail")
            view.set_lamp(view.lamp_event_session_stop, "fail")
            view.var_event_session_start.set("Fail")
            view.var_event_session_stop.set("Fail")
            log.log(f"[WARNING] Unknown json data: {json_data.get("action")}")
            csv.update_results(session_start=res_ev_start, session_stop=res_ev_stop)
            event_ev.set()
    except json.JSONDecodeError:
        res_ev_start = "Fail"
        res_ev_stop = "Fail"
        view.set_lamp(view.lamp_event_session_start, "fail")
        view.set_lamp(view.lamp_event_session_stop, "fail")
        view.var_event_session_start.set("Fail")
        view.var_event_session_stop.set("Fail")
        log.log(f"[ERROR] Received non-JSON data from {sender}: {data_str}")
        csv.update_results(session_start=res_ev_start, session_stop=res_ev_stop)
        event_ev.set()
    except Exception as exc:
        res_ev_start = "Fail"
        res_ev_stop = "Fail"
        view.set_lamp(view.lamp_event_session_start, "fail")
        view.set_lamp(view.lamp_event_session_stop, "fail")
        view.var_event_session_start.set("Fail")
        view.var_event_session_stop.set("Fail")
        log.log(f"[ERROR] Processing notification failed: {exc}")
        log.log_traceback(exc)
        csv.update_results(session_start=res_ev_start, session_stop=res_ev_stop)
        event_ev.set()


async def handle_event_management() -> bool:
    if not bleak.is_connected:
        log.log(f"[ERROR] Invalid client")
        return False

    try:
        global event_ev, res_ev_start, res_ev_stop
        _mark_test_start()
        view.set_state("busy-event-managing")
        view.set_lamp(view.lamp_event_session_start, "testing")
        view.var_event_session_start.set("Testing...")
        view.var_event_session_stop.set("")
        log.log(f"[INFO] === Event Session start ===")

        event_ev = asyncio.Event()
        res_ev_start = "NT"
        res_ev_stop = "NT"

        # The remote triggers the event, so there is no write_gatt_char here
        await wait_or_abort(event_ev)
        return res_ev_start == "Pass" and res_ev_stop == "Pass"
    except TestAbortedError:
        res_ev_start = "Fail"
        res_ev_stop = "Fail"
        view.set_lamp(view.lamp_event_session_start, "fail")
        view.set_lamp(view.lamp_event_session_stop, "fail")
        view.var_event_session_start.set("Aborted")
        view.var_event_session_stop.set("Aborted")
        return False
    except Exception as exc:
        res_ev_start = "Fail"
        res_ev_stop = "Fail"
        view.set_lamp(view.lamp_event_session_start, "fail")
        view.set_lamp(view.lamp_event_session_stop, "fail")
        view.var_event_session_start.set("Fail")
        view.var_event_session_stop.set("Fail")
        log.log(f"[ERROR] Event management failed: {exc}")
        return False
    finally:
        _mark_test_end()
        view.set_lamp(view.lamp_camera, "not-operating") # kill camera on event finish
        log.log(f"[INFO] === Event Session end ===")
        csv.update_results(session_start=res_ev_start, session_stop=res_ev_stop)
        if bleak.is_connected:
            view.set_state("idle-connected")
        else:
            view.set_state("idle-disconnected")


async def fp_add_and_event_session() -> bool:
    """
    Function that runs "fp add" to "event session stop" in one go.
    If the FP addition is failed, it will skip event session.
    """
    if not bleak.is_connected:
        log.log(f"[ERROR] Invalid Client")
        return False
    
    try:
        ok = await add_fingerprint()
        if not ok:
            return False

        ok = await handle_event_management()
        return ok
    except: # logs are already handled in both functions, thus exception will never happen
        return False


def _cb_factory_reset(_sender, data):
    global res_fr
    try:
        data_str = data.decode("utf-8")
        if data_str == "OK":
            res_fr = "Pass"
            view.var_factory_reset.set("Pass")
            view.set_lamp(view.lamp_factory_reset, "pass")
            log.log(f"[INFO] Factory reset response OK")
        else:
            res_fr = "Fail"
            view.var_factory_reset.set("Fail")
            view.set_lamp(view.lamp_factory_reset, "fail")
            log.log(f"[ERROR] Factory reset response NG: {data_str}")
    except UnicodeDecodeError:
        res_fr = "Fail"
        view.var_factory_reset.set("Fail")
        view.set_lamp(view.lamp_factory_reset, "fail")
        log.log(f"[ERROR] Received non-UTF-8 data: {data}")
    except Exception as exc:
        res_fr = "Fail"
        view.var_factory_reset.set("Fail")
        view.set_lamp(view.lamp_factory_reset, "fail")
        log.log(f"[ERROR] Processing notification failed: {exc}")
        log.log_traceback(exc)
    finally:
        event_fr.set()


async def factory_reset() -> bool:
    """
    Factory resets the connected device
    Note that this will also clear `fingerprint_storage`.
    """
    if not bleak.is_connected:
        log.log(f"[ERROR] Invalid Client")
        return False
    
    try:
        global event_fr, res_fr
        _mark_test_start()
        view.set_state("busy-factory-resetting")
        log.log(f"[INFO] === Factory Reset start ===")

        event_fr = asyncio.Event()

        # factory reset
        bleak.disconnect_expected = True
        await bleak.write_gatt_char(UUID_FACTORY_RESET, "1".encode("utf-8"))
        await wait_or_abort(event_fr)
        fingerprint_storage.clear()
        return True
    except Exception as exc:
        res_fr = "Fail"
        view.var_factory_reset.set("Factory Reset failed")
        view.set_lamp(view.lamp_factory_reset, 'fail')
        log.log(f"[ERROR] Factory reset + Unpair failed: {exc}")
        log.log_traceback(exc)
        return False
    finally:
        _mark_test_end()
        log.log(f"[INFO] === Factory Reset end ===")
        csv.finish_test(factory_reset_result=res_fr)
        if bleak.is_connected:
            view.set_state("idle-connected")
        else:
            view.set_state("idle-disconnected")


async def unpair_and_clear_test_result() -> bool:
    # we will not check bleak connection, if fails, silently exits

    try:
        view.set_state("busy-unpairing")
        log.log(f"[INFO] === Unpair start ===")
        # https://bleak.readthedocs.io/en/latest/api/client.html#bleak.BleakClient.stop_notify
        # Notifications are stopped automatically on disconnect, so this method does not need
        # to be called unless notifications need to be stopped some time before the device disconnects.
        """
        log.log(
            f"[INFO] Disabling notifications for UUID:\n" \
            + f"UUID_FINGERPRINT_ADDITION ({UUID_FINGERPRINT_ADDITION})\n" \
            + f"UUID_FINGERPRINT_DELETION ({UUID_FINGERPRINT_DELETION})\n" \
            + f"UUID_EVENT_MANAGEMENT     ({UUID_EVENT_MANAGEMENT})\n" \
            + f"UUID_FACTORY_RESET        ({UUID_FACTORY_RESET})\n"
        )
        await bleak.stop_notify_silent(UUID_FINGERPRINT_ADDITION)
        await bleak.stop_notify_silent(UUID_FINGERPRINT_DELETION)
        await bleak.stop_notify_silent(UUID_EVENT_MANAGEMENT)
        await bleak.stop_notify_silent(UUID_FACTORY_RESET)
        log.log("[INFO] Disable notifications complete")
        """
        await bleak.unpair()
        return True
    except Exception as exc:
        log.log(f"[ERROR] Unpair failed: {exc}")
        log.log_traceback(exc)
        return False
    finally:
        log.log(f"[INFO] === Unpair end ===")
        view.set_state("idle-disconnected")

        bleak.client = None

        log.log(f"[INFO] Test result will be erased in 5 seconds")
        await asyncio.sleep(5.0)
        view.clear_device_list()
        view.clear_device_info()
        view.clear_test_result()


async def factory_reset_and_unpair() -> bool:
    if not bleak.is_connected:
        log.log(f"[ERROR] Invalid Client")
        return False
    
    try:
        ok = await factory_reset()
        if not ok:
            return False
        
        ok = await unpair_and_clear_test_result()
        return ok
    except: # logs are already handled in both functions, thus exception will never happen
        return False


async def main() -> None:
    global view, log, bleak, test_abort_event, csv
    view = View(VERSION)
    log = LogModule(view=view)
    test_abort_event = asyncio.Event()
    bleak = BleakModule(log=log, on_disconnect=on_disconnect, on_reconnect=on_reconnect)
    csv = CsvModule(path="test_results.csv", log=log)

    # === register callbacks to app ===
    view.set_handler("scan", lambda: asyncio.create_task(scan_and_update_device_list()))
    view.set_handler("connect", lambda: asyncio.create_task(connect_and_read_device_info()))
    view.set_handler("fp_add", lambda: asyncio.create_task(fp_add_and_event_session()))
    view.set_handler("fp_delete", lambda: asyncio.create_task(delete_fingerprint()))
    view.set_handler("factory_reset", lambda: asyncio.create_task(factory_reset_and_unpair()))
    view.set_handler("unpair_all", lambda: asyncio.create_task(unpair_all_dekoda_remotes()))
    # =================================

    log.log(f"*** C306-KV-BLE Quality Assurance Tool {VERSION} ***")

    while view.is_open:
        try:
            view.update()
        except tk.TclError:
            break
        await asyncio.sleep(FRAMERATE)
    
    await factory_reset_and_unpair()


if __name__ == "__main__":
    asyncio.run(main())
