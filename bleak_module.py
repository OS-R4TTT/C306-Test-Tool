import asyncio
import inspect
from collections.abc import Callable
from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from log_module import LogModule
from winrt.windows.devices.bluetooth import BluetoothLEDevice, BluetoothConnectionStatus
from winrt.windows.devices.enumeration import (
    DeviceInformation,
    DeviceUnpairingResultStatus,
)


class BleakModuleError(RuntimeError):
    pass


class BleakModule:
    def __init__(self, log: LogModule = None, on_disconnect=None, on_reconnect=None) -> None:
        self.devices: dict[str, tuple[BLEDevice, AdvertisementData]] = {}

        self.client: BleakClient = None

        self.reconnect_task = None
        self.reconnect_lock = asyncio.Lock()
        self.disconnect_expected: bool = False
        self.force_cancel_reconnect: bool = False

        self.log = log
        self.on_disconnect = on_disconnect
        self.on_reconnect = on_reconnect

    
    async def scan(self):
        scanner = BleakScanner()
        try:
            await scanner.start()
            await asyncio.sleep(5.0)
            await scanner.stop()
            self.devices = scanner.discovered_devices_and_advertisement_data
        except Exception as exc:
            raise BleakModuleError("Scan failed") from exc

    
    async def connect(self, device: BLEDevice | str, pair: bool = True):
        client = BleakClient(device, self._disconnected_callback, pair=pair)
        self.disconnect_expected = True # prevent reconnection when failed atm
        try:
            await client.connect()
            if not client.is_connected:
                raise BleakModuleError("Connect failed: client not connected")
            self.client = client
            self.disconnect_expected = False
        except Exception as exc:
            raise BleakModuleError("Connect failed") from exc
    
    
    async def reconnect(self) -> None:
        async with self.reconnect_lock:
            while True:
                if self.force_cancel_reconnect:
                    self._log("* Reconn force canceled")
                    self.force_cancel_reconnect = False
                    return

                try:
                    await self.client.connect()
                    if self.client.is_connected:
                        self._log("* Reconn success")
                        await self._call_hook_async(self.on_reconnect, "on_reconnect")
                        return
                except Exception as exc:
                    self._log("* Reconn failed, trying again...")
                await asyncio.sleep(0.5)


    def _disconnected_callback(self, _client):
        self._log("* Device disconnected", force_file_open=False)
        if not self.disconnect_expected:
            self._call_hook_sync(self.on_disconnect, "on_disconnect")
            if self.reconnect_task is None or self.reconnect_task.done():
                self._log("Unexpected disconn, try reconnection")
                self.reconnect_task = asyncio.create_task(self.reconnect())
        else:
            self._log("* Expected disconnect, does not trigger reconn")


    async def unpair(self) -> None:
        try:
            await self.client.unpair()
        except Exception as exc:
            raise BleakModuleError("Unpair failed") from exc


    async def write_gatt_char(self, uuid: str, data: bytes | bytearray) -> None:
        if self.client is None or not self.client.is_connected:
            raise BleakModuleError("Client not connected")
        try:
            log_msg = f"[BLE] Write GATT\n" \
                        + f"- uuid: {uuid}\n" \
                        + f"- data: {data}"
            self._log(log_msg)
            await self.client.write_gatt_char(uuid, data)
        except Exception as exc:
            raise BleakModuleError(f"Write failed for {uuid}") from exc

    
    async def read_gatt_char(self, uuid: str) -> bytes | bytearray:
        if self.client is None or not self.client.is_connected:
            raise BleakModuleError("Client not connected")
        try:
            data = await self.client.read_gatt_char(uuid)
            log_msg = f"[BLE] Read GATT\n" \
                        + f"- uuid: {uuid}\n" \
                        + f"- data: {data}"
            self._log(log_msg)
            return data
        except Exception as exc:
            raise BleakModuleError(f"Read failed for {uuid}") from exc
    

    async def start_notify(self, uuid: str, callback: Callable) -> None:
        if self.client is None or not self.client.is_connected:
            raise BleakModuleError("Client not connected")
        try:
            await self.client.start_notify(uuid, callback)
        except Exception as exc:
            raise BleakModuleError(f"Notification start failed for {uuid}") from exc
    

    async def stop_notify(self, uuid: str) -> None:
        if self.client is None or not self.client.is_connected:
            raise BleakModuleError("Client not connected")
        try:
            await self.client.stop_notify(uuid)
        except Exception as exc:
            raise BleakModuleError(f"Notification stop failed for {uuid}") from exc
    

    async def stop_notify_silent(self, uuid: str) -> None:
        try:
            await self.client.stop_notify(uuid)
        except:
            pass


    async def unpair_all_dekoda_remotes(self) -> None:
        """
        Unpair all *paired* BLE devices whose DeviceInformation.name starts with `DekodaRemote-`,
        regardless of whether they are currently connected.
        """
        PREFIX = "DekodaRemote-"

        # AQS for paired BLE devices
        selector = BluetoothLEDevice.get_device_selector_from_pairing_state(True)
        if hasattr(DeviceInformation, "find_all_async_aqs_filter"):
            devices = await DeviceInformation.find_all_async_aqs_filter(selector)
        elif hasattr(DeviceInformation, "find_all_async_aqs_filter_and_additional_properties"):
            devices = await DeviceInformation.find_all_async_aqs_filter_and_additional_properties(selector, [])
        else:
            devices = await DeviceInformation.find_all_async(selector, [])
        filtered_devices = [device for device in devices if (device.name or "").startswith(PREFIX)]

        self._log(f"[INFO] Found {len(devices)} device(s)\n" \
                + f" - Found {len(filtered_devices)} DekodaRemote(s)"
        )

        if self.reconnect_lock.locked():
            self.force_cancel_reconnect = True

        for device in filtered_devices:
            result = await device.pairing.unpair_async()
            status = result.status

            if status in (DeviceUnpairingResultStatus.UNPAIRED,
                        DeviceUnpairingResultStatus.ALREADY_UNPAIRED):
                self._log(f"Unpaired: {device.name}")
            else:
                self._log(f"Failed to unpair {device.name}: {status.name}")


    def _log(self, msg: str, force_file_open: bool = True) -> None:
        if self.log is not None:
            self.log.log(msg, force_file_open)


    async def _call_hook_async(self, hook: Callable | None, name: str) -> None:
        if hook is None:
            return
        try:
            result = hook()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            self._log(f"[ERROR] {name} hook failed: {exc}")


    def _call_hook_sync(self, hook: Callable | None, name: str) -> None:
        if hook is None:
            return
        try:
            result = hook()
            if inspect.isawaitable(result):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(result)
                else:
                    loop.create_task(result)
        except Exception as exc:
            self._log(f"[ERROR] {name} hook failed: {exc}")


    @property
    def is_connected(self) -> bool:
        if self.client is None:
            return False
        return self.client.is_connected
    

    @property
    def client_address(self) -> str | None:
        if self.is_connected:
            return self.client.address
        return None
    

    @property
    def client_name(self) -> str | None:
        if self.is_connected:
            return self.client.name
        return None
