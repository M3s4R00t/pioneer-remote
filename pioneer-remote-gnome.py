#!/usr/bin/env python3
# Pioneer Remote Control for GNOME
# Copyright (C) 2026 Bnargin https://github.com/M3s4R00t
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib
import socket
import threading
import traceback
import time
import webbrowser

RECEIVER_PORT = 8102

INPUTS = {
    "BD": "25",
    "DVD": "04",
    "TV/SAT": "05",
    "DVR/BDR": "15",
    "HDMI1": "19",
    "HDMI2": "20",
    "HDMI3": "21",
    "HDMI4": "22",
    "HDMI5": "23",
    "HDMI6": "24",
    "Network": "26",
    "Internet Radio": "38",
    "USB": "17",
    "Tuner": "02",
    "CD": "01",
    "Phono": "00",
}

def raw_to_db(raw: int) -> float:
    return (raw / 2.0) - 80.0

def db_to_raw(db: float) -> int:
    return int(round((db + 80.0) * 2))

def decode_fl(hexstr: str) -> str:
    """Decode Pioneer FL display message (hex ASCII)."""
    try:
        # Strip the leading length/type nibble if present and decode
        raw = bytes.fromhex(hexstr)
        return raw.decode("ascii", errors="ignore").strip()
    except Exception:
        return hexstr

class PioneerWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        print("Window created")
        self.set_title("Pioneer Remote")
        self.set_default_size(460, 720)
        self.sock = None
        self.running = True
        self.updating = False
        self.receiver_ip = "42.0.0.65"
        self.volume_timer = None

        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        menu = Gio.Menu()
        menu.append("Preferences", "app.preferences")
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)

        # Scrolled content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        scrolled.set_child(box)

        toolbar_view.set_content(scrolled)

        # Status
        status_group = Adw.PreferencesGroup(title="Status")
        box.append(status_group)

        self.power_row = Adw.ActionRow(title="Power", subtitle="Connecting…")
        status_group.add(self.power_row)

        self.volume_row = Adw.ActionRow(title="Volume", subtitle="—")
        status_group.add(self.volume_row)

        self.input_row = Adw.ActionRow(title="Input", subtitle="—")
        status_group.add(self.input_row)

        self.mute_row = Adw.ActionRow(title="Mute", subtitle="—")
        status_group.add(self.mute_row)

        self.display_row = Adw.ActionRow(title="Display", subtitle="—")
        status_group.add(self.display_row)

        self.mode_row = Adw.ActionRow(title="Listening Mode", subtitle="—")
        status_group.add(self.mode_row)

        # Controls
        controls_group = Adw.PreferencesGroup(title="Controls")
        box.append(controls_group)

        power_row = Adw.ActionRow(title="Power")
        self.power_switch = Gtk.Switch()
        self.power_switch.set_valign(Gtk.Align.CENTER)
        self.power_switch.connect("notify::active", self.on_power_toggled)
        power_row.add_suffix(self.power_switch)
        controls_group.add(power_row)

        mute_row = Adw.ActionRow(title="Mute")
        self.mute_switch = Gtk.Switch()
        self.mute_switch.set_valign(Gtk.Align.CENTER)
        self.mute_switch.connect("notify::active", self.on_mute_toggled)
        mute_row.add_suffix(self.mute_switch)
        controls_group.add(mute_row)

        vol_row = Adw.ActionRow(title="Volume")
        self.volume_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -80.0, 12.0, 0.5)
        self.volume_scale.set_hexpand(True)
        self.volume_scale.set_digits(1)
        self.volume_scale.connect("value-changed", self.on_volume_changed)
        vol_row.add_suffix(self.volume_scale)
        controls_group.add(vol_row)

        input_row = Adw.ActionRow(title="Input")
        self.input_dropdown = Gtk.DropDown.new_from_strings(list(INPUTS.keys()))
        self.input_dropdown.connect("notify::selected", self.on_input_selected)
        input_row.add_suffix(self.input_dropdown)
        controls_group.add(input_row)

        # Listening mode buttons
        mode_group = Adw.PreferencesGroup(title="Listening Mode")
        box.append(mode_group)

        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mode_box.set_halign(Gtk.Align.CENTER)
        mode_group.add(mode_box)

        modes = [
            ("Auto/Direct", "0005SR"),
            ("Stereo", "0009SR"),
            ("ALC/Standard", "0010SR"),
            ("ADV SURR", "0100SR"),
        ]
        for label, cmd in modes:
            btn = Gtk.Button(label=label)
            btn.connect("clicked", lambda _, c=cmd: self.send(c))
            mode_box.append(btn)

        # Network transport
        transport_group = Adw.PreferencesGroup(title="Network / Internet Radio")
        box.append(transport_group)

        transport_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        transport_box.set_halign(Gtk.Align.CENTER)
        transport_group.add(transport_box)

        for label, cmd in [("⏮", "12NW"), ("▶/⏸", "11NW"), ("⏹", "20NW"), ("⏭", "13NW")]:
            btn = Gtk.Button(label=label)
            btn.connect("clicked", lambda _, c=cmd: self.send(c))
            transport_box.append(btn)

        print("Starting connection thread")
        threading.Thread(target=self.connect_loop, daemon=True).start()

    def connect_loop(self):
        while self.running:
            try:
                print("Connecting to", self.receiver_ip)
                self.sock = socket.create_connection((self.receiver_ip, RECEIVER_PORT), timeout=5)
                self.sock.settimeout(1.0)
                GLib.idle_add(self.power_row.set_subtitle, "Connected")
                self.send("?P")
                self.send("?V")
                self.send("?F")
                self.send("?M")
                self.send("?S")
                self.read_loop()
            except Exception as e:
                print("Connection error:", e)
                traceback.print_exc()
                GLib.idle_add(self.power_row.set_subtitle, f"Disconnected: {e}")
                self.sock = None
                if self.running:
                    time.sleep(3)

    def read_loop(self):
        buffer = b""
        while self.running and self.sock:
            try:
                data = self.sock.recv(1024)
                if not data:
                    break
                buffer += data
                while b"\r\n" in buffer:
                    line, buffer = buffer.split(b"\r\n", 1)
                    msg = line.decode(errors="ignore").strip()
                    if msg:
                        self.handle_message(msg)
            except socket.timeout:
                continue
            except Exception as e:
                print("Read error:", e)
                traceback.print_exc()
                break
        print("Reader stopped")

    def handle_message(self, msg):
        print("RX:", msg)
        try:
            if msg.startswith("PWR"):
                on = msg.endswith("0")
                self.updating = True
                GLib.idle_add(self.power_switch.set_active, on)
                GLib.idle_add(self.power_row.set_subtitle, "On" if on else "Standby")
                self.updating = False
            elif msg.startswith("VOL"):
                raw = int(msg[3:])
                db = raw_to_db(raw)
                self.updating = True
                GLib.idle_add(self.volume_scale.set_value, db)
                GLib.idle_add(self.volume_row.set_subtitle, f"{db:.1f} dB")
                self.updating = False
            elif msg.startswith("FN"):
                code = msg[2:]
                name = next((k for k, v in INPUTS.items() if v == code), code)
                GLib.idle_add(self.input_row.set_subtitle, name)
                try:
                    idx = list(INPUTS.keys()).index(name)
                    self.updating = True
                    GLib.idle_add(self.input_dropdown.set_selected, idx)
                    self.updating = False
                except ValueError:
                    pass
            elif msg.startswith("MUT"):
                muted = msg.endswith("0")
                self.updating = True
                GLib.idle_add(self.mute_switch.set_active, muted)
                GLib.idle_add(self.mute_row.set_subtitle, "Muted" if muted else "Unmuted")
                self.updating = False
            elif msg.startswith("FL"):
                text = decode_fl(msg[2:])
                if text:
                    GLib.idle_add(self.display_row.set_subtitle, text)
            elif msg.startswith("SR"):
                code = msg[2:] if len(msg) > 2 else msg
                friendly = {
                    "0005": "Auto/Direct",
                    "0006": "Auto Surround",
                    "0007": "Direct",
                    "0008": "Pure Direct",
                    "0009": "Stereo",
                    "0001": "Stereo",
                    "0010": "Standard",
                    "0151": "ALC",
                    "0100": "ADV SURR",
                }.get(code, code)
                GLib.idle_add(self.mode_row.set_subtitle, friendly)
        except Exception as e:
            print("Handle message error:", e)
            traceback.print_exc()

    def send(self, cmd):
        if not self.sock:
            return
        try:
            self.sock.sendall(f"{cmd}\r\n".encode())
        except Exception as e:
            print("Send error:", e)

    def on_power_toggled(self, switch, _):
        if self.updating or not self.sock:
            return
        cmd = "PO" if switch.get_active() else "PF"
        threading.Thread(target=lambda: self.send(cmd), daemon=True).start()

    def on_mute_toggled(self, switch, _):
        if self.updating or not self.sock:
            return
        cmd = "MO" if switch.get_active() else "MF"
        threading.Thread(target=lambda: self.send(cmd), daemon=True).start()

    def on_volume_changed(self, scale):
        if self.updating or not self.sock:
            return
        if self.volume_timer:
            GLib.source_remove(self.volume_timer)
        self.volume_timer = GLib.timeout_add(180, self.send_volume)

    def send_volume(self):
        if not self.sock:
            return False
        db = self.volume_scale.get_value()
        raw = db_to_raw(db)
        self.send(f"{raw:03d}VL")
        self.volume_timer = None
        return False

    def on_input_selected(self, dropdown, _):
        if self.updating or not self.sock:
            return
        name = list(INPUTS.keys())[dropdown.get_selected()]
        code = INPUTS[name]
        cmd = f"{code}FN"
        threading.Thread(target=lambda: self.send(cmd), daemon=True).start()

    def show_preferences(self):
        dialog = Adw.PreferencesDialog()
        dialog.set_title("Preferences")

        page = Adw.PreferencesPage()
        dialog.add(page)

        group = Adw.PreferencesGroup(title="Connection")
        page.add(group)

        ip_row = Adw.EntryRow(title="Receiver IP")
        ip_row.set_text(self.receiver_ip)
        group.add(ip_row)

        web_row = Adw.ActionRow(title="Web Control")
        web_btn = Gtk.Button(label="Open")
        web_btn.set_valign(Gtk.Align.CENTER)
        web_btn.connect("clicked", lambda *_: webbrowser.open(f"http://{ip_row.get_text().strip()}/"))
        web_row.add_suffix(web_btn)
        group.add(web_row)

        apply_row = Adw.ActionRow(title="Apply Changes")
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.set_css_classes(["suggested-action"])
        apply_btn.set_valign(Gtk.Align.CENTER)
        apply_row.add_suffix(apply_btn)
        group.add(apply_row)

        def on_apply(*_):
            new_ip = ip_row.get_text().strip()
            if new_ip and new_ip != self.receiver_ip:
                self.receiver_ip = new_ip
                if self.sock:
                    try:
                        self.sock.close()
                    except Exception:
                        pass
                    self.sock = None
            dialog.close()

        apply_btn.connect("clicked", on_apply)
        dialog.present(self)

class PioneerApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.example.PioneerRemote",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )
        self.connect("activate", self.on_activate)
        self.create_action("preferences", self.on_preferences)
        print("App initialized")

    def create_action(self, name, callback):#!/usr/bin/env python3
# Pioneer Remote Control for GNOME
# Copyright (C) 2026 Bnargin https://github.com/M3s4R00t
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib
import socket
import threading
import traceback
import time
import webbrowser

RECEIVER_PORT = 8102

INPUTS = {
    "BD": "25",
    "DVD": "04",
    "TV/SAT": "05",
    "DVR/BDR": "15",
    "HDMI1": "19",
    "HDMI2": "20",
    "HDMI3": "21",
    "HDMI4": "22",
    "HDMI5": "23",
    "HDMI6": "24",
    "Network": "26",
    "Internet Radio": "38",
    "USB": "17",
    "Tuner": "02",
    "CD": "01",
    "Phono": "00",
}

def raw_to_db(raw: int) -> float:
    return (raw / 2.0) - 80.0

def db_to_raw(db: float) -> int:
    return int(round((db + 80.0) * 2))

def decode_fl(hexstr: str) -> str:
    """Decode Pioneer FL display message (hex ASCII)."""
    try:
        # Strip the leading length/type nibble if present and decode
        raw = bytes.fromhex(hexstr)
        return raw.decode("ascii", errors="ignore").strip()
    except Exception:
        return hexstr

class PioneerWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        print("Window created")
        self.set_title("Pioneer Remote")
        self.set_default_size(460, 720)
        self.sock = None
        self.running = True
        self.updating = False
        self.receiver_ip = "42.0.0.65"
        self.volume_timer = None

        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        menu = Gio.Menu()
        menu.append("Preferences", "app.preferences")
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)

        # Scrolled content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        scrolled.set_child(box)

        toolbar_view.set_content(scrolled)

        # Status
        status_group = Adw.PreferencesGroup(title="Status")
        box.append(status_group)

        self.power_row = Adw.ActionRow(title="Power", subtitle="Connecting…")
        status_group.add(self.power_row)

        self.volume_row = Adw.ActionRow(title="Volume", subtitle="—")
        status_group.add(self.volume_row)

        self.input_row = Adw.ActionRow(title="Input", subtitle="—")
        status_group.add(self.input_row)

        self.mute_row = Adw.ActionRow(title="Mute", subtitle="—")
        status_group.add(self.mute_row)

        self.display_row = Adw.ActionRow(title="Display", subtitle="—")
        status_group.add(self.display_row)

        self.mode_row = Adw.ActionRow(title="Listening Mode", subtitle="—")
        status_group.add(self.mode_row)

        # Controls
        controls_group = Adw.PreferencesGroup(title="Controls")
        box.append(controls_group)

        power_row = Adw.ActionRow(title="Power")
        self.power_switch = Gtk.Switch()
        self.power_switch.set_valign(Gtk.Align.CENTER)
        self.power_switch.connect("notify::active", self.on_power_toggled)
        power_row.add_suffix(self.power_switch)
        controls_group.add(power_row)

        mute_row = Adw.ActionRow(title="Mute")
        self.mute_switch = Gtk.Switch()
        self.mute_switch.set_valign(Gtk.Align.CENTER)
        self.mute_switch.connect("notify::active", self.on_mute_toggled)
        mute_row.add_suffix(self.mute_switch)
        controls_group.add(mute_row)

        vol_row = Adw.ActionRow(title="Volume")
        self.volume_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -80.0, 12.0, 0.5)
        self.volume_scale.set_hexpand(True)
        self.volume_scale.set_digits(1)
        self.volume_scale.connect("value-changed", self.on_volume_changed)
        vol_row.add_suffix(self.volume_scale)
        controls_group.add(vol_row)

        input_row = Adw.ActionRow(title="Input")
        self.input_dropdown = Gtk.DropDown.new_from_strings(list(INPUTS.keys()))
        self.input_dropdown.connect("notify::selected", self.on_input_selected)
        input_row.add_suffix(self.input_dropdown)
        controls_group.add(input_row)

        # Listening mode buttons
        mode_group = Adw.PreferencesGroup(title="Listening Mode")
        box.append(mode_group)

        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mode_box.set_halign(Gtk.Align.CENTER)
        mode_group.add(mode_box)

        modes = [
            ("Auto/Direct", "0005SR"),
            ("Stereo", "0009SR"),
            ("ALC/Standard", "0010SR"),
            ("ADV SURR", "0100SR"),
        ]
        for label, cmd in modes:
            btn = Gtk.Button(label=label)
            btn.connect("clicked", lambda _, c=cmd: self.send(c))
            mode_box.append(btn)

        # Network transport
        transport_group = Adw.PreferencesGroup(title="Network / Internet Radio")
        box.append(transport_group)

        transport_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        transport_box.set_halign(Gtk.Align.CENTER)
        transport_group.add(transport_box)

        for label, cmd in [("⏮", "12NW"), ("▶/⏸", "11NW"), ("⏹", "20NW"), ("⏭", "13NW")]:
            btn = Gtk.Button(label=label)
            btn.connect("clicked", lambda _, c=cmd: self.send(c))
            transport_box.append(btn)

        print("Starting connection thread")
        threading.Thread(target=self.connect_loop, daemon=True).start()

    def connect_loop(self):
        while self.running:
            try:
                print("Connecting to", self.receiver_ip)
                self.sock = socket.create_connection((self.receiver_ip, RECEIVER_PORT), timeout=5)
                self.sock.settimeout(1.0)
                GLib.idle_add(self.power_row.set_subtitle, "Connected")
                self.send("?P")
                self.send("?V")
                self.send("?F")
                self.send("?M")
                self.send("?S")
                self.read_loop()
            except Exception as e:
                print("Connection error:", e)
                traceback.print_exc()
                GLib.idle_add(self.power_row.set_subtitle, f"Disconnected: {e}")
                self.sock = None
                if self.running:
                    time.sleep(3)

    def read_loop(self):
        buffer = b""
        while self.running and self.sock:
            try:
                data = self.sock.recv(1024)
                if not data:
                    break
                buffer += data
                while b"\r\n" in buffer:
                    line, buffer = buffer.split(b"\r\n", 1)
                    msg = line.decode(errors="ignore").strip()
                    if msg:
                        self.handle_message(msg)
            except socket.timeout:
                continue
            except Exception as e:
                print("Read error:", e)
                traceback.print_exc()
                break
        print("Reader stopped")

    def handle_message(self, msg):
        print("RX:", msg)
        try:
            if msg.startswith("PWR"):
                on = msg.endswith("0")
                self.updating = True
                GLib.idle_add(self.power_switch.set_active, on)
                GLib.idle_add(self.power_row.set_subtitle, "On" if on else "Standby")
                self.updating = False
            elif msg.startswith("VOL"):
                raw = int(msg[3:])
                db = raw_to_db(raw)
                self.updating = True
                GLib.idle_add(self.volume_scale.set_value, db)
                GLib.idle_add(self.volume_row.set_subtitle, f"{db:.1f} dB")
                self.updating = False
            elif msg.startswith("FN"):
                code = msg[2:]
                name = next((k for k, v in INPUTS.items() if v == code), code)
                GLib.idle_add(self.input_row.set_subtitle, name)
                try:
                    idx = list(INPUTS.keys()).index(name)
                    self.updating = True
                    GLib.idle_add(self.input_dropdown.set_selected, idx)
                    self.updating = False
                except ValueError:
                    pass
            elif msg.startswith("MUT"):
                muted = msg.endswith("0")
                self.updating = True
                GLib.idle_add(self.mute_switch.set_active, muted)
                GLib.idle_add(self.mute_row.set_subtitle, "Muted" if muted else "Unmuted")
                self.updating = False
            elif msg.startswith("FL"):
                text = decode_fl(msg[2:])
                if text:
                    GLib.idle_add(self.display_row.set_subtitle, text)
            elif msg.startswith("SR"):
                code = msg[2:] if len(msg) > 2 else msg
                friendly = {
                    "0005": "Auto/Direct",
                    "0006": "Auto Surround",
                    "0007": "Direct",
                    "0008": "Pure Direct",
                    "0009": "Stereo",
                    "0001": "Stereo",
                    "0010": "Standard",
                    "0151": "ALC",
                    "0100": "ADV SURR",
                }.get(code, code)
                GLib.idle_add(self.mode_row.set_subtitle, friendly)
        except Exception as e:
            print("Handle message error:", e)
            traceback.print_exc()

    def send(self, cmd):
        if not self.sock:
            return
        try:
            self.sock.sendall(f"{cmd}\r\n".encode())
        except Exception as e:
            print("Send error:", e)

    def on_power_toggled(self, switch, _):
        if self.updating or not self.sock:
            return
        cmd = "PO" if switch.get_active() else "PF"
        threading.Thread(target=lambda: self.send(cmd), daemon=True).start()

    def on_mute_toggled(self, switch, _):
        if self.updating or not self.sock:
            return
        cmd = "MO" if switch.get_active() else "MF"
        threading.Thread(target=lambda: self.send(cmd), daemon=True).start()

    def on_volume_changed(self, scale):
        if self.updating or not self.sock:
            return
        if self.volume_timer:
            GLib.source_remove(self.volume_timer)
        self.volume_timer = GLib.timeout_add(180, self.send_volume)

    def send_volume(self):
        if not self.sock:
            return False
        db = self.volume_scale.get_value()
        raw = db_to_raw(db)
        self.send(f"{raw:03d}VL")
        self.volume_timer = None
        return False

    def on_input_selected(self, dropdown, _):
        if self.updating or not self.sock:
            return
        name = list(INPUTS.keys())[dropdown.get_selected()]
        code = INPUTS[name]
        cmd = f"{code}FN"
        threading.Thread(target=lambda: self.send(cmd), daemon=True).start()

    def show_preferences(self):
        dialog = Adw.PreferencesDialog()
        dialog.set_title("Preferences")

        page = Adw.PreferencesPage()
        dialog.add(page)

        group = Adw.PreferencesGroup(title="Connection")
        page.add(group)

        ip_row = Adw.EntryRow(title="Receiver IP")
        ip_row.set_text(self.receiver_ip)
        group.add(ip_row)

        web_row = Adw.ActionRow(title="Web Control")
        web_btn = Gtk.Button(label="Open")
        web_btn.set_valign(Gtk.Align.CENTER)
        web_btn.connect("clicked", lambda *_: webbrowser.open(f"http://{ip_row.get_text().strip()}/"))
        web_row.add_suffix(web_btn)
        group.add(web_row)

        apply_row = Adw.ActionRow(title="Apply Changes")
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.set_css_classes(["suggested-action"])
        apply_btn.set_valign(Gtk.Align.CENTER)
        apply_row.add_suffix(apply_btn)
        group.add(apply_row)

        def on_apply(*_):
            new_ip = ip_row.get_text().strip()
            if new_ip and new_ip != self.receiver_ip:
                self.receiver_ip = new_ip
                if self.sock:
                    try:
                        self.sock.close()
                    except Exception:
                        pass
                    self.sock = None
            dialog.close()

        apply_btn.connect("clicked", on_apply)
        dialog.present(self)

class PioneerApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.github.M3s4R00t.PioneerRemote",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )
        self.connect("activate", self.on_activate)
        self.create_action("preferences", self.on_preferences)
        print("App initialized")

    def create_action(self, name, callback):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)

    def on_activate(self, app):
        print("Activating window")
        self.win = PioneerWindow(application=app)
        self.win.present()
        print("Window presented")

    def on_preferences(self, *_):
        if hasattr(self, "win"):
            self.win.show_preferences()

if __name__ == "__main__":
    try:
        print("Starting application")
        app = PioneerApp()
        app.run(None)
    except Exception:
        traceback.print_exc()
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)

    def on_activate(self, app):
        print("Activating window")
        self.win = PioneerWindow(application=app)
        self.win.present()
        print("Window presented")

    def on_preferences(self, *_):
        if hasattr(self, "win"):
            self.win.show_preferences()

if __name__ == "__main__":
    try:
        print("Starting application")
        app = PioneerApp()
        app.run(None)
    except Exception:
        traceback.print_exc()
