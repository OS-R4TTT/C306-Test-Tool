import asyncio
import json
import tkinter as tk
from view import View, State
from bleak_module import BleakModule
from log_module import LogModule
from csv_module import CsvModule
from typing import Literal
from enum import Enum

VERSION = "v1.1.1"

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

view: View = None
log: LogModule = None
bleak: BleakModule = None
csv: CsvModule | None = None

fingerprint_storage: dict[str, str] = {}

connect_lock: bool = False # ensures connect does not happen twice
disconnect_expected = False
test_abort_event: asyncio.Event | None = None
tests_in_progress = 0


class TestAbortedError(RuntimeError):
    """Error wrapper (Custom)"""
    pass

# ===============================================
#  Test wait, abort Logic (disconnect/reconnect)
#  - `_mark_test_start`, `_mark_test_end` are only used for tests that use notification.
# ===============================================

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


def on_reconnect() -> None:
    """
    This function will run in BleakModule after it reconnects.
    Handles view or main attributes that BleakModule cannot access.
    """
    view.set_state("idle-connected")
    view.clear_test_result()


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
        return
    if view.selected_device is None: # ignore double clicks on blank space
        return
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
        await asyncio.sleep(1.0)
        await bleak.write_gatt_char(UUID_OHSUNG_TESTMODE_NOTIFY, bytes([0xF3]))
        log.log(f"[INFO] Ohsung testmode command sent")
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


async def factory_reset_and_unpair() -> bool:
    """
    Factory resets the connected device, and unpair (removes from pair list).
    
    Note that this will also clear `fingerprint_storage`.
    """
    if not bleak.is_connected:
        log.log(f"[ERROR] Invalid Client")
        return False
    
    factory_reset_result: str | None = None
    try:
        _mark_test_start()
        bleak.disconnect_expected = True
        view.set_state("busy-factory-resetting")
        log.log(f"[INFO] Factory resetting...")

        event = asyncio.Event()

        def _cb(_sender, data):
            nonlocal factory_reset_result
            try:
                data_str = data.decode("utf-8")
                if data_str == "OK":
                    view.var_factory_reset.set("Pass")
                    view.set_lamp(view.lamp_factory_reset, "pass")
                    log.log(f"[INFO] Factory reset response OK")
                    factory_reset_result = "Pass"
                else:
                    view.var_factory_reset.set("Fail")
                    view.set_lamp(view.lamp_factory_reset, "fail")
                    log.log(f"[ERROR] Factory reset response NG: {data_str}")
                    factory_reset_result = "Fail"
            except UnicodeDecodeError:
                view.var_factory_reset.set("Fail")
                view.set_lamp(view.lamp_factory_reset, "fail")
                log.log(f"[ERROR] Received non-UTF-8 data: {data}")
                factory_reset_result = "Fail"
            except Exception as exc:
                view.var_factory_reset.set("Fail")
                view.set_lamp(view.lamp_factory_reset, "fail")
                log.log(f"[ERROR] Processing notification failed: {exc}")
                log.log_traceback(exc)
                factory_reset_result = "Fail"
            finally:
                event.set()
        
        # factory reset
        await bleak.start_notify(UUID_FACTORY_RESET, callback=_cb)
        await bleak.write_gatt_char(UUID_FACTORY_RESET, "1".encode("utf-8"))
        await wait_or_abort(event)
        if factory_reset_result is not None:
            csv.finish_test(factory_reset_result=factory_reset_result)

        # unpair
        view.set_state("busy-unpairing")
        log.log(f"[INFO] Unpairing...")
        await bleak.unpair()
        view.set_state("idle-disconnected")
        log.log(f"[INFO] Factory reset + Unpair complete")
        return True
    except Exception as exc:
        log.log(f"[ERROR] Factory reset + Unpair failed: {exc}")
        log.log_traceback(exc)
        return False
    finally:
        _mark_test_end()
        fingerprint_storage.clear()
        await bleak.stop_notify_silent(UUID_FACTORY_RESET)
        bleak.client = None
        view.set_state("idle-disconnected")
        log.log(f"[INFO] Test result will be erased in 5 seconds")
        await asyncio.sleep(5.0)
        view.clear_device_list()
        view.clear_device_info()
        view.clear_test_result()


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
        log.log(f"[WARN] User Name: {user_name} is invalid (1-8 len, only alphabets allowed)\n" \
                + f"Please enter a valid user name and try again."
        )
        return False

    result_literal: str | None = None
    try:
        _mark_test_start()
        view.set_state("busy-adding-fingerprint")
        view.set_lamp(view.lamp_fp_addition, 'testing')
        view.var_fp_addition.set("Testing...")
        log.log(f"[INFO] === Add Fingerprint start ===")

        event = asyncio.Event()
        cb_passed = False

        def _cb(_sender, data):
            nonlocal cb_passed
            try:
                data_str = data.decode("utf-8")
                if data_str == "CANCEL":
                    view.var_fp_addition.set("Add Fingerprint canceled by user")
                    view.set_lamp(view.lamp_fp_addition, 'fail')
                    log.log(f"[INFO] Add Fingerprint canceled by user")
                else:
                    fingerprint_storage[data_str] = user_name
                    cb_passed = True
                    view.update_fingerprint_list(fingerprint_storage)
                    view.set_lamp(view.lamp_fp_addition, 'pass')
                    log.log(f"[INFO] Registered fingerprint with {data_str} ({user_name})")
            except UnicodeDecodeError:
                view.var_fp_addition.set("Received non UTF-8 data")
                view.set_lamp(view.lamp_fp_addition, 'fail')
                log.log(f"[ERROR] Received non-UTF-8 data: {data}")
            except Exception as exc:
                view.var_fp_addition.set("Error in notification")
                view.set_lamp(view.lamp_fp_addition, 'fail')
                log.log(f"[ERROR] Processing notification failed: {exc}")
                log.log_traceback(exc)
            finally:
                event.set()

        await bleak.start_notify(UUID_FINGERPRINT_ADDITION, callback=_cb)
        await bleak.write_gatt_char(UUID_FINGERPRINT_ADDITION, user_name.encode("utf-8"))
        await wait_or_abort(event) # wait until user presses fp addition
        result_literal = "Pass" if cb_passed else "Fail"
        return cb_passed
    except TestAbortedError:
        view.var_fp_addition.set("Aborted")
        view.set_lamp(view.lamp_fp_addition, 'fail')
        result_literal = "Fail"
        return False
    except Exception as exc:
        view.var_fp_addition.set("FP add failed")
        view.set_lamp(view.lamp_fp_addition, 'fail')
        log.log(f"[ERROR] Fingerprint addition failed: {exc}")
        result_literal = "Fail"
        return False
    finally:
        _mark_test_end()
        await bleak.stop_notify_silent(UUID_FINGERPRINT_ADDITION)
        log.log(f"[INFO] === Add Fingerprint end ===")
        if result_literal is not None:
            csv.update_results(fp_addition=result_literal)
        if bleak.is_connected:
            view.set_state("idle-connected")
        else:
            view.set_state("idle-disconnected")


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
    
    result_literal: str | None = None
    try:
        _mark_test_start()
        view.set_state("busy-deleting-fingerprint")
        view.set_lamp(view.lamp_fp_deletion, 'testing')
        view.var_fp_deletion.set("Testing...")
        log.log(f"[INFO] === Delete Fingerprint start ===")

        event = asyncio.Event()
        target_uuid = ""
        all_pass = True

        def _cb(_sender, data):
            global all_pass
            try:
                data_str = data.decode("utf-8")
                if data_str == "OK":
                    del fingerprint_storage[target_uuid]
                    log.log(f"[INFO] Remove successful ({data_str})")
                else:
                    all_pass = False
                    log.log(f"[WARN] Remove failed ({data_str})")
            except UnicodeDecodeError:
                all_pass = False
                log.log(f"[ERROR] Received non-UTF-8 data: {data}")
            except Exception as exc:
                all_pass = False
                log.log(f"[ERROR] Processing notification failed: {exc}")
                log.log_traceback(exc)
            finally:
                event.set()
        
        await bleak.start_notify(UUID_FINGERPRINT_DELETION, callback=_cb)
        # Erase all fingerprints
        for uuid in list(fingerprint_storage.keys()):
            target_uuid = uuid
            await bleak.write_gatt_char(UUID_FINGERPRINT_DELETION, target_uuid.encode("utf-8"))
        await wait_or_abort(event)
        view.var_fp_deletion.set("Pass" if all_pass else "Fail")
        view.set_lamp(view.lamp_fp_deletion, 'pass' if all_pass else 'fail')
        result_literal = "Pass" if all_pass else "Fail"
        return all_pass
    except TestAbortedError:
        view.var_fp_deletion.set("Aborted")
        view.set_lamp(view.lamp_fp_deletion, 'fail')
        result_literal = "Fail"
        return False
    except Exception as exc:
        view.var_fp_deletion.set("Fail")
        view.set_lamp(view.lamp_fp_deletion, 'fail')
        log.log(f"[ERROR] Fingerprint deletion failed: {exc}")
        result_literal = "Fail"
        return False
    finally:
        _mark_test_end()
        await bleak.stop_notify_silent(UUID_FINGERPRINT_DELETION)
        log.log(f"[INFO] === Delete Fingerprint end ===")
        if result_literal is not None:
            csv.update_results(fp_deletion=result_literal)
        if bleak.is_connected:
            view.set_state("idle-connected")
        else:
            view.set_state("idle-disconnected")


async def start_event_management() -> bool:
    """
    Handles event sessions from start fo finish.
    The callback `_cb` will be called twice (start, finish).
    """

    if not bleak.is_connected:
        log.log(f"[ERROR] Invalid client")
        return False

    session_start_result: str | None = None
    session_stop_result: str | None = None
    try:
        _mark_test_start()
        view.set_state("busy-event-managing")
        view.set_lamp(view.lamp_event_session_start, "testing")
        view.var_event_session_start.set("Testing...")
        view.var_event_session_stop.set("")
        log.log(f"[INFO] === Event Session start ===")

        event = asyncio.Event()
        session_start_result = "NT"
        session_stop_result = "NT"

        async def _cb(sender, data):
            nonlocal session_start_result, session_stop_result
            try:
                data_str = data.decode("utf-8")
                json_data = json.loads(data_str)
                log.log(f"[INFO] Notification from {sender}: {json.dumps(json_data, indent=2)}")
                
                if json_data.get("action") == "start":
                    view.set_lamp(view.lamp_camera, "operating")
                    view.set_lamp(view.lamp_event_session_start, "pass")
                    view.set_lamp(view.lamp_event_session_stop, "testing")
                    view.var_event_session_start.set("Pass")
                    view.var_event_session_stop.set("Testing...")
                    session_start_result = "Pass"
                    csv.update_results(session_start=session_start_result)

                    #await bleak.write_gatt_char(UUID_EVENT_MANAGEMENT, "NOT-READY".encode("utf-8"))
                    #await asyncio.sleep(1.0)
                    await bleak.write_gatt_char(UUID_EVENT_MANAGEMENT, "IN-PROGRESS".encode("utf-8"))
                    await asyncio.sleep(3.0)
                    await bleak.write_gatt_char(UUID_EVENT_MANAGEMENT, "UNSET".encode("utf-8"))
                    
                elif json_data.get("action") == "stop":
                    view.set_lamp(view.lamp_camera, "not-operating")
                    view.set_lamp(view.lamp_event_session_stop, "pass")
                    view.var_event_session_stop.set("Pass")
                    session_stop_result = "Pass"
                    csv.update_results(session_stop=session_stop_result)

                    await bleak.write_gatt_char(UUID_EVENT_MANAGEMENT, "UNSET".encode("utf-8"))
                    event.set()
                else:
                    view.set_lamp(view.lamp_event_session_start, "fail")
                    view.set_lamp(view.lamp_event_session_stop, "fail")
                    view.var_event_session_start.set("Fail")
                    view.var_event_session_stop.set("Fail")

                    log.log(f"[WARNING] Unknown json data: {json_data.get("action")}")
                    session_start_result = "Fail"
                    session_stop_result = "Fail"
                    csv.update_results(
                        session_start=session_start_result,
                        session_stop=session_stop_result,
                    )
                    event.set()
            except json.JSONDecodeError:
                view.set_lamp(view.lamp_event_session_start, "fail")
                view.set_lamp(view.lamp_event_session_stop, "fail")
                view.var_event_session_start.set("Fail")
                view.var_event_session_stop.set("Fail")

                log.log(f"[ERROR] Received non-JSON data from {sender}: {data_str}")
                session_start_result = "Fail"
                session_stop_result = "Fail"
                csv.update_results(
                    session_start=session_start_result,
                    session_stop=session_stop_result,
                )
                event.set()
            except Exception as exc:
                view.set_lamp(view.lamp_event_session_start, "fail")
                view.set_lamp(view.lamp_event_session_stop, "fail")
                view.var_event_session_start.set("Fail")
                view.var_event_session_stop.set("Fail")

                log.log(f"[ERROR] Processing notification failed: {exc}")
                log.log_traceback(exc)
                session_start_result = "Fail"
                session_stop_result = "Fail"
                csv.update_results(
                    session_start=session_start_result,
                    session_stop=session_stop_result,
                )
                event.set()
            # start event should still wait for stop event, so sadly the code branches event.set()
        
        await bleak.start_notify(UUID_EVENT_MANAGEMENT, callback=_cb)
        await wait_or_abort(event)
        return True
    except TestAbortedError:
        view.set_lamp(view.lamp_event_session_start, "fail")
        view.set_lamp(view.lamp_event_session_stop, "fail")
        view.var_event_session_start.set("Aborted")
        view.var_event_session_stop.set("Aborted")
        session_start_result = "Fail"
        session_stop_result = "Fail"
        return False
    except Exception as exc:
        view.set_lamp(view.lamp_event_session_start, "fail")
        view.set_lamp(view.lamp_event_session_stop, "fail")
        view.var_event_session_start.set("Fail")
        view.var_event_session_stop.set("Fail")
        log.log(f"[ERROR] Event management failed: {exc}")
        session_start_result = "Fail"
        session_stop_result = "Fail"
        return False
    finally:
        _mark_test_end()
        view.set_lamp(view.lamp_camera, "not-operating") # kill camera on event finish
        await bleak.stop_notify_silent(UUID_EVENT_MANAGEMENT)
        log.log(f"[INFO] === Event Session end ===")
        if session_start_result is not None and session_stop_result is not None:
            csv.update_results(
                session_start=session_start_result,
                session_stop=session_stop_result,
            )
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

        ok = await start_event_management()
        return ok
    except: # logs are already handled in both functions, thus exception will never happen
        return False
    finally: # this part is also not necessary, but for aesthetics
        if bleak.is_connected:
            view.set_state("idle-connected")
        else:
            view.set_state("idle-disconnected")

#region unused
async def start_remote_diagnostic() -> None:
    """
    Unlike other functions, this only stops remote diagnostic when any json callback is detected.
    This makes the `view.set_state` branching inside the `except` rather than `finally` statement.
    """

    if not bleak.is_connected:
        log.log(f"[ERROR] Invalid Client")
        return
    
    try:
        view.set_state("busy-remote-diagnosting")

        def _cb(sender, data):
            try:
                data_str = data.decode("utf-8")
                json_data = json.loads(data_str)
                log.log(f"[INFO] Notification from {sender}: {json.dumps(json_data, indent=2)}")
            except json.JSONDecodeError:
                log.log(f"[ERROR] Received non-JSON data from {sender}: {data_str}")
            except Exception as exc:
                print(f"[Error] Processing notification failed: {exc}")
                log.log_traceback(exc)
            finally:
                asyncio.create_task(stop_remote_diagnostic())
    
        await bleak.start_notify(UUID_REMOTE_DIAGNOSTIC, callback=_cb)
        await bleak.write_gatt_char(UUID_REMOTE_DIAGNOSTIC, "1".encode("utf-8"))
    except Exception as exc:
        log.log(f"[ERROR] Remote diagnostic failed: {exc}")
        if bleak.is_connected:
            view.set_state("idle-connected")
        else:
            view.set_state("idle-disconnected")


async def stop_remote_diagnostic() -> None:
    """
    Remote diagnostic can be stopped manually by calling this function
    """
    await bleak.stop_notify_silent(UUID_REMOTE_DIAGNOSTIC)
    if bleak.is_connected:
        view.set_state("idle-connected")
    else:
        view.set_state("idle-disconnected")


async def ohsung_qc() -> None:
    if not bleak.is_connected:
        log.log(f"[ERROR] Invalid Client")
        return
    
    try:
        log.log(f"=== OHSUNG 출하 검사 시작 ===")

        # 1. Basic info
        data = await bleak.read_gatt_char(UUID_SW_VER)
        sw_ver = data.decode("utf-8")
        data = await bleak.read_gatt_char(UUID_SERIAL_NO)
        serial_no = data.decode("utf-8")
        log.log(f"1. Basic Info")
        log.log(f" - FW version: {sw_ver}")
        log.log(f" - Serial no : {serial_no}")

        # 2. Fingerprint addition
        log.log(f"2. Add fingerprint")
        await add_fingerprint()

        # 3. Event management
        log.log(f"3. Event management")
        await start_event_management()

        log.log(f"=== OHSUNG 출하 검사 종료 ===")
    except Exception as exc:
        log.log(f"[ERROR] Fingerprint deletion failed: {exc}")
    finally:
        if bleak.is_connected:
            view.set_state("idle-connected")
        else:
            view.set_state("idle-disconnected")
#endregion

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
