import tkinter as tk
import tkinter.font as tkfont
import time
import ttkbootstrap as ttk
from tkinter.scrolledtext import ScrolledText
from ttkbootstrap.widgets.tableview import Tableview
from collections.abc import Callable
from typing import Literal


LONG_PRESS_TRIGGER_MS = 600
LONG_PRESS_TICK_MS = 20


State = Literal[
    'idle-disconnected',
    'idle-connected',
    'busy-scanning',
    'busy-connecting',
    'busy-unpairing',
    'busy-reading-gatt',
    'busy-adding-fingerprint',
    'busy-deleting-fingerprint',
    'busy-remote-diagnosting',
    'busy-event-managing',
    'busy-factory-resetting',
    'debug',
]


class View(ttk.Window):
    def __init__(self, version_str) -> None:
        super().__init__(themename="litera")

        self.title(f"C306-KV-BLE QA PC Tool ({version_str})")
        self.geometry("1480x720")
        self.font_default = tkfont.Font(family="Segoe UI", size=9)
        self.option_add("*Font", self.font_default)
        self.style.configure(".", font=self.font_default)
        self.font_small = (self.font_default.actual("family"), 9)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.is_open: bool = True

        self._handlers: dict[str, Callable[[], None]] = {}

        # For long-press button (factory reset) handling
        self._factory_reset_after_id: str | None = None
        self._factory_reset_press_start: float | None = None
        self._factory_reset_ignore = False

        # === variables ===
        self.var_state = ttk.StringVar(value="idle-disconnected")

        self.var_user_name = ttk.StringVar(value="")

        self.var_device_name = ttk.StringVar(value="")
        self.var_manufacturer_name = ttk.StringVar(value = "")
        self.var_serial_no = ttk.StringVar(value="")
        self.var_model_no = ttk.StringVar(value="")
        self.var_mac_address = ttk.StringVar(value="")
        self.var_fw_ver = ttk.StringVar(value="")

        self.var_fp_addition = ttk.StringVar(value="")
        self.var_event_session_start = ttk.StringVar(value="")
        self.var_event_session_stop = ttk.StringVar(value="")
        self.var_fp_deletion = ttk.StringVar(value="")
        self.var_factory_reset = ttk.StringVar(value="")

        # ========================
        # UI Components
        # ========================
        
        # === ui components - 1 ===
        self.btn_scan = ttk.Button(
            self,
            text="Scan",
            command=lambda: self._dispatch("scan"),
            bootstyle="success",
        )
        self.btn_dongle_reset = ttk.Button(
            self,
            text="Dongle Reset",
            command=lambda: self._dispatch("unpair_all"),
            bootstyle="success",
        )

        coldata = [
            {"text": "Device Name", "width": 210, "stretch": False, "anchor": "w"},
            {"text": "Mac Address",  "width": 180, "stretch": True,  "anchor": "w"},
        ]
        self.table_scan_result = Tableview(
            self,
            coldata=coldata,
            rowdata=[],
            yscrollbar=True,
            autoalign=False,
            disable_right_click=True,
            height=8,
        )
        self.table_scan_result.view.unbind("<Button-1>") # disable sort
        self.table_scan_result.view.bind("<Double-1>", lambda _event: self._dispatch("connect"))

        # === ui components - 2 ===
        self.entry_user_name = ttk.Entry(self, textvariable=self.var_user_name, bootstyle="primary")
        self.lbl_device_name = ttk.Entry(self, textvariable=self.var_device_name, bootstyle="secondary")
        self.lbl_manufacturer_name = ttk.Entry(self, textvariable=self.var_manufacturer_name, bootstyle="secondary")
        self.lbl_serial_no = ttk.Entry(self, textvariable=self.var_serial_no, bootstyle="secondary")
        self.lbl_model_no = ttk.Entry(self, textvariable=self.var_model_no, bootstyle="secondary")
        self.lbl_mac_address = ttk.Entry(self, textvariable=self.var_mac_address, bootstyle="secondary")
        self.lbl_fw_ver = ttk.Entry(self, textvariable=self.var_fw_ver, bootstyle="secondary")

        self.lbl_device_name.configure(state="readonly")
        self.lbl_manufacturer_name.configure(state="readonly")
        self.lbl_serial_no.configure(state="readonly")
        self.lbl_model_no.configure(state="readonly")
        self.lbl_mac_address.configure(state="readonly")
        self.lbl_fw_ver.configure(state="readonly")

        # === ui components - 3 ===
        self.btn_fp_add = ttk.Button(
            self,
            text="FP Add",
            command=lambda: self._dispatch("fp_add"),
            bootstyle="success",
        )
        self.btn_fp_delete = ttk.Button(
            self,
            text="FP Delete",
            command=lambda: self._dispatch("fp_delete"),
            bootstyle="success",
        )
        # Legacy button (not used)
        """
        self.btn_factory_reset = ttk.Button(
            self,
            text="Factory Reset",
            command=lambda: self._dispatch("factory_reset"),
            bootstyle="success",
        )
        """
        self.btn_factory_reset = ttk.Floodgauge(
            self,
            font=self.font_small, # for some reason this widget needs to set font even if it's already set to default
            text="Factory Reset",
            bootstyle="success",
            maximum=100,
            value=0,
        )

        self.btn_factory_reset.bind("<ButtonPress-1>", self._on_factory_reset_press)
        self.btn_factory_reset.bind("<ButtonRelease-1>", self._on_factory_reset_release)

        self.lbl_fp_addition = ttk.Entry(self, textvariable=self.var_fp_addition, bootstyle="secondary")
        self.lbl_event_session_start = ttk.Entry(self, textvariable=self.var_event_session_start, bootstyle="secondary")
        self.lbl_event_session_stop = ttk.Entry(self, textvariable=self.var_event_session_stop, bootstyle="secondary")
        self.lbl_fp_deletion = ttk.Entry(self, textvariable=self.var_fp_deletion, bootstyle="secondary")
        self.lbl_factory_reset = ttk.Entry(self, textvariable=self.var_factory_reset, bootstyle="secondary")

        self.lbl_fp_addition.configure(state="readonly")
        self.lbl_event_session_start.configure(state="readonly")
        self.lbl_event_session_stop.configure(state="readonly")
        self.lbl_fp_deletion.configure(state="readonly")
        self.lbl_factory_reset.configure(state="readonly")

        self.lamp_fp_addition = ttk.Label(self)
        self.lamp_event_session_start = ttk.Label(self)
        self.lamp_event_session_stop = ttk.Label(self)
        self.lamp_fp_deletion = ttk.Label(self)
        self.lamp_factory_reset = ttk.Label(self)

        self.set_lamp(self.lamp_fp_addition, 'none')
        self.set_lamp(self.lamp_event_session_start, 'none')
        self.set_lamp(self.lamp_event_session_stop, 'none')
        self.set_lamp(self.lamp_fp_deletion, 'none')
        self.set_lamp(self.lamp_factory_reset, 'none')

        # === ui components - 4 ===
        self.lbl_state = ttk.Label(self, textvariable=self.var_state, bootstyle="inverse-secondary", anchor="center")
        self.scrtxt_log = ScrolledText(self, wrap="word", height=10)

        # === ui components - 5 ===
        self.lamp_ble = ttk.Label(self)
        self.lamp_camera = ttk.Label(self)

        self.set_lamp(self.lamp_ble, 'disconnected')
        self.set_lamp(self.lamp_camera, 'not-operating')

        # ========================
        # UI Placements
        # ========================

        # === ui placement - 1 ===
        self.btn_scan.place(x=20, y=20, width=130, height=40)
        self.btn_dongle_reset.place(x=170, y=20, width=130, height=40)
        self.table_scan_result.place(x=20, y=80, width=400, height=200)

        # === ui placement - 2 ===
        label = ttk.Label(self, text="User Name", bootstyle="inverse-info", anchor="center")
        label.place(x=440, y=20, width=120, height=40)
        self.entry_user_name.place(x=560, y=20, width=220, height=40)

        label1 = ttk.Label(self, text="Device Name", bootstyle="inverse-secondary", anchor="center")
        label2 = ttk.Label(self, text="Manf. Name", bootstyle="inverse-secondary", anchor="center", font=self.font_small)
        label3 = ttk.Label(self, text="Serial Number", bootstyle="inverse-secondary", anchor="center")
        label4 = ttk.Label(self, text="Model Number", bootstyle="inverse-secondary", anchor="center")
        label5 = ttk.Label(self, text="MAC Address", bootstyle="inverse-secondary", anchor="center")
        label6 = ttk.Label(self, text="Frimware Ver", bootstyle="inverse-secondary", anchor="center")
        label1.place(x=440, y=80, width=120, height=40)
        label2.place(x=440, y=130, width=120, height=40)
        label3.place(x=440, y=180, width=120, height=40)
        label4.place(x=440, y=230, width=120, height=40)
        label5.place(x=440, y=280, width=120, height=40)
        label6.place(x=440, y=330, width=120, height=40)

        self.lbl_device_name.place(x=560, y=80, width=220, height=40)
        self.lbl_manufacturer_name.place(x=560, y=130, width=220, height=40)
        self.lbl_serial_no.place(x=560, y=180, width=220, height=40)
        self.lbl_model_no.place(x=560, y=230, width=220, height=40)
        self.lbl_mac_address.place(x=560, y=280, width=220, height=40)
        self.lbl_fw_ver.place(x=560, y=330, width=220, height=40)

        # === ui placement - 3 ===
        self.btn_fp_add.place(x=800, y=20, width=120, height=40)
        self.btn_fp_delete.place(x=940, y=20, width=120, height=40)
        self.btn_factory_reset.place(x=1080, y=20, width=130, height=40)

        label1 = ttk.Label(self, text="FP Addition", bootstyle="inverse-secondary", anchor="center")
        label2 = ttk.Label(self, text="Session Start", bootstyle="inverse-secondary", anchor="center", font=self.font_small)
        label3 = ttk.Label(self, text="Session Stop", bootstyle="inverse-secondary", anchor="center", font=self.font_small)
        label4 = ttk.Label(self, text="FP Deletion", bootstyle="inverse-secondary", anchor="center")
        label5 = ttk.Label(self, text="Factory Reset", bootstyle="inverse-secondary", anchor="center")
        label1.place(x=800, y=80, width=120, height=40)
        label2.place(x=800, y=130, width=120, height=40)
        label3.place(x=800, y=180, width=120, height=40)
        label4.place(x=800, y=230, width=120, height=40)
        label5.place(x=800, y=280, width=120, height=40)

        self.lbl_fp_addition.place(x=920, y=80, width=300, height=40)
        self.lbl_event_session_start.place(x=920, y=130, width=300, height=40)
        self.lbl_event_session_stop.place(x=920, y=180, width=300, height=40)
        self.lbl_fp_deletion.place(x=920, y=230, width=300, height=40)
        self.lbl_factory_reset.place(x=920, y=280, width=300, height=40)

        self.lamp_fp_addition.place(x=1220, y=80, width=40, height=40)
        self.lamp_event_session_start.place(x=1220, y=130, width=40, height=40)
        self.lamp_event_session_stop.place(x=1220, y=180, width=40, height=40)
        self.lamp_fp_deletion.place(x=1220, y=230, width=40, height=40)
        self.lamp_factory_reset.place(x=1220, y=280, width=40, height=40)

        # === ui placement - 4 ===
        #self.lbl_state.place(x=20, y=380, width=180, height=40)
        label = ttk.Label(self, text="Log", bootstyle="inverse-info", anchor="center")
        label.place(x=20, y=340, width=120, height=40)
        self.scrtxt_log.place(x=20, y=400, width=1440, height=300)

        # === ui placement - 5 ===
        label7 = ttk.Label(self, text="BLE", bootstyle="inverse-info", anchor="center")
        label8 = ttk.Label(self, text="Camera", bootstyle="inverse-info", anchor="center")
        label7.place(x=1280, y=40, width=80, height=40)
        label8.place(x=1380, y=40, width=80, height=40)

        self.lamp_ble.place(x=1280, y=80, width=80, height=80)
        self.lamp_camera.place(x=1380, y=80, width=80, height=80)

        # init state
        self.set_state('idle-disconnected')
    

    def set_handler(self, action: str, handler: Callable[[], None]) -> None:
        self._handlers[action] = handler


    def set_state(self, state: State) -> None:
        self.var_state.set(state)

        match state:
            case 'debug':
                self.btn_scan.configure(state="normal")
                self.btn_dongle_reset.configure(state="normal")
                self.btn_fp_add.configure(state="normal")
                self.btn_fp_delete.configure(state="normal")
                self.btn_factory_reset.configure(state="normal")

            case 'idle-disconnected':
                # only scan is allowed
                self.btn_scan.configure(state="normal")
                self.btn_dongle_reset.configure(state="normal")
                self.btn_fp_add.configure(state="disabled")
                self.btn_fp_delete.configure(state="disabled")
                self.btn_factory_reset.configure(state="disabled")
            
            case 'idle-connected':
                self.btn_scan.configure(state="disabled")
                self.btn_dongle_reset.configure(state="disabled")
                self.btn_fp_add.configure(state="normal")
                self.btn_fp_delete.configure(state="normal")
                self.btn_factory_reset.configure(state="normal")
            
            case _: # busy
                self.btn_scan.configure(state="disabled")
                self.btn_dongle_reset.configure(state="disabled")
                self.btn_fp_add.configure(state="disabled")
                self.btn_fp_delete.configure(state="disabled")
                self.btn_factory_reset.configure(state="disabled")

        match state:
            case 'idle-disconnected' | 'busy-scanning':
                self.set_lamp(self.lamp_ble, 'disconnected')
            
            case 'busy-connecting':
                self.set_lamp(self.lamp_ble, 'connecting')
            
            case _:
                self.set_lamp(self.lamp_ble, 'connected')


    def clear_device_list(self) -> None:
        self.table_scan_result.delete_rows()


    def update_device_list(self, device_list) -> None:
        self.clear_device_list()
        for key, (device, _adv_data) in device_list.items():
            self.table_scan_result.insert_row("end", [device.name, device.address])


    def clear_device_info(self) -> None:
        self.var_device_name.set("")
        self.var_manufacturer_name.set("")
        self.var_serial_no.set("")
        self.var_model_no.set("")
        self.var_mac_address.set("")
        self.var_fw_ver.set("")


    def update_device_info(
            self,
            device_name: str,
            manufacturer_name: str,
            serial_no: str,
            model_no: str,
            mac_address: str,
            fw_ver: str,
    ) -> None:
        self.var_device_name.set(device_name)
        self.var_manufacturer_name.set(manufacturer_name)
        self.var_serial_no.set(serial_no)
        self.var_model_no.set(model_no)
        self.var_mac_address.set(mac_address)
        self.var_fw_ver.set(fw_ver)


    def update_fingerprint_list(self, fingerprint_storage: dict[str, str]) -> None:
        """
        Currently only supports 1 fingerprint
        """
        fp_uuid, _fp_name = next(iter(fingerprint_storage.items()))
        self.var_fp_addition.set(f"{fp_uuid}")


    def clear_test_result(self) -> None:
        self.var_fp_addition.set("")
        self.var_event_session_start.set("")
        self.var_event_session_stop.set("")
        self.var_fp_deletion.set("")
        self.var_factory_reset.set("")

        self.set_lamp(self.lamp_fp_addition, 'none')
        self.set_lamp(self.lamp_event_session_start, 'none')
        self.set_lamp(self.lamp_event_session_stop, 'none')
        self.set_lamp(self.lamp_fp_deletion, 'none')
        self.set_lamp(self.lamp_factory_reset, 'none')


    def set_lamp(self, lamp: ttk.Label, state: Literal['pass', 'fail', 'testing', 'none', 'connected', 'connecting', 'disconnected', 'operating', 'not-operating']) -> None:
        match state:
            case 'pass' | 'connected' | 'operating':
                lamp.configure(bootstyle="inverse-success")
            
            case 'fail' | 'disconnected':
                lamp.configure(bootstyle="inverse-danger")
            
            case 'testing' | 'connecting':
                lamp.configure(bootstyle='inverse-warning')
            
            case 'not-operating':
                lamp.configure(bootstyle="secondary-inverse")

            case 'none':
                lamp.configure(bootstyle="secondary-inverse")


    def log(self, msg: str) -> None:
        try:
            self.scrtxt_log.insert("end", msg)
            self.scrtxt_log.see("end")
        except:
            pass


    def _on_close(self) -> None:
        self.is_open = False
        self.quit()
        self.destroy()


    def _dispatch(self, action: str) -> None:
        handler = self._handlers.get(action)
        if handler:
            handler()
        else:
            print(f"!! There is no action named: {action}")


    def _on_factory_reset_press(self, _event: tk.Event) -> None:
        if self._factory_reset_ignore:
            return
        if str(self.btn_factory_reset.cget("state")) == "disabled":
            return

        self._factory_reset_press_start = time.perf_counter()
        self.btn_factory_reset.configure(value=0)
        if self._factory_reset_after_id is not None:
            self.after_cancel(self._factory_reset_after_id)
            self._factory_reset_after_id = None
        self._factory_reset_after_id = self.after(
            LONG_PRESS_TICK_MS,
            self._update_factory_reset_fill,
        )


    def _on_factory_reset_release(self, _event: tk.Event) -> None:
        if self._factory_reset_after_id is not None:
            self.after_cancel(self._factory_reset_after_id)
            self._factory_reset_after_id = None

        self._factory_reset_press_start = None
        self.btn_factory_reset.configure(value=0)

        if self._factory_reset_ignore:
            self._factory_reset_ignore = False


    def _update_factory_reset_fill(self) -> None:
        if self._factory_reset_press_start is None or self._factory_reset_ignore:
            return

        elapsed_ms = (time.perf_counter() - self._factory_reset_press_start) * 1000.0
        t = min(elapsed_ms / LONG_PRESS_TRIGGER_MS, 1.0)
        eased = 1.0 - (1.0 - t) ** 2
        max_value = float(self.btn_factory_reset.cget("maximum"))
        self.btn_factory_reset.configure(value=max_value * eased)

        if elapsed_ms >= LONG_PRESS_TRIGGER_MS:
            self.btn_factory_reset.configure(value=0)
            self._factory_reset_ignore = True
            self._factory_reset_press_start = None
            self._factory_reset_after_id = None
            self._dispatch("factory_reset")
            return

        self._factory_reset_after_id = self.after(
            LONG_PRESS_TICK_MS,
            self._update_factory_reset_fill,
        )
    

    @property
    def state(self) -> str:
        return self.var_state.get()

    @property
    def pair_on_connect(self) -> bool:
        return True

    @property
    def selected_device(self) -> tuple | None:
        selected = self.table_scan_result.view.selection()
        if not selected:
            return None

        iid = selected[0]
        item = self.table_scan_result.view.item(iid)
        values = item.get("values", ())

        if not values:
            return None
        
        return tuple(values)

    @property
    def user_name(self) -> str:
        return self.var_user_name.get()


if __name__ == "__main__":
    view = View("v0.0.0")

    def debug_seed():
        view.table_scan_result.insert_row("end", ["DekodaRemote-25403Z005P", "68:96:6A:FF:0E:3D"])
        view.var_user_name.set("Ohsung")
        view.update_device_info(
            device_name="DekodaRemote-25403Z005P",
            manufacturer_name="Kohler Ventures",
            serial_no="25403Z005P",
            model_no="DekodaRemote-1",
            mac_address="68:96:6A:FF:0E:3D",
            fw_ver="2.71.3",
        )
        view.var_fp_addition.set("128-bit uuid")
        view.var_event_session_start.set("Lorem ipsum dolor sit amet, consectetur adipiscing elit")
        view.var_event_session_stop.set("Aenean vitae blandit nisi")
        view.var_fp_deletion.set("Vivamus dictum tincidunt orci vitae lacinia")
        view.var_factory_reset.set("This is a test text")
        view.log("[DEBUG] This is a test mode (view)")

        view.set_state('debug')

    view.after(0, debug_seed)
    view.mainloop()
