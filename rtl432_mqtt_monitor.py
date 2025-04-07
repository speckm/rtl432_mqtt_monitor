#!/usr/bin/env python3

import argparse
import json
import os
import pickle
import queue
import select  # Keep for input thread
import sys
import threading
import time
import traceback
from collections import defaultdict, deque
from datetime import datetime

import paho.mqtt.client as mqtt
from rich.syntax import Syntax
from rich.text import Text

# --- Textual Imports ---
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.reactive import reactive
from textual.timer import Timer

# Import DataTable Message class
from textual.widgets import DataTable, Footer, Header, RichLog, Static

# --- Configuration (Identical) ---
MAX_EVENTS_PER_DEVICE = 2000
MAX_GLOBAL_EVENTS = 100000
DEFAULT_BROKER = "localhost"
DEFAULT_PORT = 1883
DEFAULT_TOPIC = "rtl_433/+/events"
DEFAULT_DATA_FILE = "rtl432_mqtt_monitor.pkl"
CURSOR_DEBOUNCE_DELAY = 0.1

# --- Global State (MQTT Client related) ---
mqtt_client = None
mqtt_shutdown_flag = threading.Event()
mqtt_thread = None

# --- Input Handling (Identical - Does not seem to be the source of high CPU) ---
input_queue = queue.Queue()
KEY_ESC = "\x1b"
KEY_UP_SEQ = "[A"
KEY_DOWN_SEQ = "[B"
KEY_ENTER = "\r"
KEY_LF = "\n"
KEY_CTRL_C = "\x03"
IS_POSIX = os.name == "posix"


def keyboard_input_thread(stop_event):
    try:
        if not sys.stdin.isatty():
            return
        fd = sys.stdin.fileno()
        while not stop_event.is_set():
            try:
                rlist, _, _ = select.select([sys.stdin], [], [], 0.1)
                if rlist:
                    char_bytes = os.read(fd, 1)
                    if not char_bytes:
                        break
                    char = char_bytes.decode("utf-8", errors="ignore")
                    # Input queue is not actively polled in the Textual app,
                    # Textual's bindings handle keys directly.
                    # We keep this thread mainly for Ctrl+C detection if needed,
                    # though Textual handles that via bindings too.
                    # Could potentially be removed if relying purely on Textual bindings.
                    if char == KEY_CTRL_C:
                        # Optionally signal shutdown via queue if bindings fail
                        # input_queue.put("CTRL_C") # Currently unused by App
                        break
            except (OSError, ValueError):
                break
            except Exception:
                pass
    finally:
        pass


# --- End Input Handling ---


# --- Data Persistence (Keep debug prints for event issue) ---
def save_data(filepath, device_data_dict):
    # (Identical to previous version with debug prints)
    data_to_save = {"devices": device_data_dict}
    print(f"\n[DEBUG save_data] Saving {len(data_to_save['devices'])} devices.")  # DEBUG
    for key, dev_info in data_to_save["devices"].items():
        events_deque = dev_info.get("events")
        if events_deque is not None:
            first_event = events_deque[0] if len(events_deque) > 0 else None
            print(
                f"[DEBUG save_data] Device {key}: Saving 'events' (type: {type(events_deque)}, "
                + f"len: {len(events_deque)}, first: {str(first_event)[:80]}...)"
            )  # DEBUG
        else:
            print(f"[DEBUG save_data] Device {key}: No 'events' deque found.")  # DEBUG
    print(f"Attempting to save data to {filepath}...")
    try:
        with open(filepath, "wb") as f:
            pickle.dump(data_to_save, f, pickle.HIGHEST_PROTOCOL)
        print("Data saved successfully.")
    except Exception as e:
        print(f"Error saving: {e}")
        traceback.print_exc()


def load_data(filepath, max_events_per_device):
    # (Identical to previous version with debug prints)
    if not os.path.exists(filepath):
        print(f"[dim]Data file {filepath} not found.")
        return {}
    print(f"[dim]Attempting to load data from {filepath}...[/dim]")
    try:
        with open(filepath, "rb") as f:
            loaded_state = pickle.load(f)
        loaded_devices = loaded_state.get("devices", {})
        print(f"[DEBUG load_data] Found {len(loaded_devices)} device entries.")  # DEBUG
        processed_devices = {}
        successful_event_loads = 0
        for key, data in loaded_devices.items():
            loaded_events_raw = data.get("events")
            if loaded_events_raw is not None:
                first_event_raw = (
                    loaded_events_raw[0]
                    if hasattr(loaded_events_raw, "__len__") and len(loaded_events_raw) > 0
                    else None
                )
                print(
                    f"[DEBUG load_data] Device {key}: Found 'events' (type: {type(loaded_events_raw)}, "
                    + f"len: {len(loaded_events_raw) if hasattr(loaded_events_raw, '__len__') else 'N/A'}, "
                    + f"first_raw: {str(first_event_raw)[:80]}...)"
                )  # DEBUG
            else:
                print(f"[DEBUG load_data] Device {key}: No 'events' found.")  # DEBUG
            try:
                if loaded_events_raw is not None and hasattr(loaded_events_raw, "__iter__"):
                    events_deque = deque(loaded_events_raw, maxlen=max_events_per_device)
                    data["events"] = events_deque
                    successful_event_loads += 1
                else:
                    data["events"] = deque(maxlen=max_events_per_device)
            except Exception as e_deque:
                print(f"[bold red][DEBUG load_data] Error reconstructing deque for {key}: {e_deque}[/]")
                traceback.print_exc()
                data["events"] = deque(maxlen=max_events_per_device)
            processed_devices[key] = data
        print(
            f"[green]Data loaded. Processed {len(processed_devices)}. Events OK for {successful_event_loads}.[/green]"
        )  # DEBUG summary
        return processed_devices
    except Exception as e:
        print(f"[bold red]Error loading data: {e}[/]")
        traceback.print_exc()
        return {}


# --- Textual App Definition ---
class MQTTMonitorApp(App):
    CSS_PATH = "rtl432_mqtt_monitor.css"
    # FIX 3: Update Bindings - Remove Enter binding for selection
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True, priority=True),
        # Arrow keys are handled implicitly by DataTable focusing/cursor movement
        # Binding("up", "cursor_up", "Cursor Up", show=False), # Not needed explicitly
        # Binding("down", "cursor_down", "Cursor Down", show=False), # Not needed explicitly
        # Binding("enter", "select_device", "Select Device/Filter", show=False), # REMOVED
        Binding("c", "clear_filter", "Clear Filter", show=True),
    ]

    connection_status = reactive("[yellow]Initializing...[/]")
    last_error = reactive("")
    selected_device_key = reactive(None)  # Stays None if no device selected/filtered
    device_count = reactive(0)

    # FIX 3: Update help text
    HELP_TEXT = " Controls: [bold]↑↓[/] Navigate/Filter | [bold]c[/] Clear Filter | [bold]Ctrl+C[/] Exit"

    # Add state for cursor debouncing
    _cursor_debounce_timer: Timer | None = None
    _pending_selection_key: tuple | None = None

    # (App __init__ remains the same)
    def __init__(self, cli_args):
        super().__init__()
        self.cli_args = cli_args
        self.max_global_events = cli_args.max_events if cli_args.max_events is not None else MAX_GLOBAL_EVENTS
        self.max_events_per_device = (
            cli_args.max_dev_events if cli_args.max_dev_events is not None else MAX_EVENTS_PER_DEVICE
        )
        self.data_file_path = cli_args.data_file
        self.device_data_factory = lambda: {
            "model": "Unknown",
            "id": "Unknown",
            "count": 0,
            "last_seen": datetime.min,
            "events": deque(maxlen=self.max_events_per_device),
            "last_raw_event": None,
        }
        self.device_data = defaultdict(self.device_data_factory)
        self.global_event_log = deque(maxlen=self.max_global_events)
        self.sorted_device_keys = []

    # (compose method remains the same)
    def compose(self) -> ComposeResult:
        yield Header(name="rtl_433 MQTT Monitor")
        with Container(id="app-grid"):
            with Container(id="devices-panel"):
                yield DataTable(id="devices-table", cursor_type="row", zebra_stripes=True)
            with Container(id="events-panel"):
                with VerticalScroll():
                    yield RichLog(id="events-log", wrap=False, highlight=True, markup=True)
        yield Static(id="status-bar", markup=True, expand=True)
        yield Footer()

    # (Widget Access properties remain the same)
    @property
    def device_table(self) -> DataTable:
        return self.query_one("#devices-table", DataTable)

    @property
    def event_log(self) -> RichLog:
        return self.query_one("#events-log", RichLog)

    @property
    def status_bar(self) -> Static:
        return self.query_one("#status-bar", Static)

    @property
    def devices_panel(self) -> Container:
        return self.query_one("#devices-panel", Container)

    @property
    def events_panel(self) -> Container:
        return self.query_one("#events-panel", Container)

    # --- Lifecycle Methods ---
    def on_mount(self) -> None:
        # (Logic identical to previous version - includes FIX 1 logic)
        loaded_device_dict = {}
        if self.cli_args.clear_data:
            if os.path.exists(self.data_file_path):
                try:
                    os.remove(self.data_file_path)
                    self.log.info(f"Cleared data file: {self.data_file_path}")
                except OSError as e:
                    self.log.error(f"Could not delete data file: {e}")
            else:
                self.log.info("Data file not found.")
            self.device_data.clear()
        else:
            loaded_device_dict = load_data(self.data_file_path, self.max_events_per_device)

        self.device_data = defaultdict(self.device_data_factory, loaded_device_dict)
        self.sorted_device_keys = sorted(
            self.device_data.keys(),
            key=lambda k: self.device_data[k]["last_seen"],
            reverse=True,
        )

        table = self.device_table
        table.add_column("Model", key="model", width=20)
        table.add_column("ID", key="id", width=15)
        table.add_column("Count", key="count", width=8)
        table.add_column("Last Seen", key="last_seen", width=10)

        self.rebuild_device_table()  # Populate table AFTER columns are added

        self.run_worker(self.mqtt_worker, thread=True)  # Start MQTT
        self.update_status_bar()  # Initial status

    def on_unmount(self) -> None:
        # (Identical)
        mqtt_shutdown_flag.set()
        save_data(self.data_file_path, dict(self.device_data))

    # --- Reactive Watchers ---
    def watch_connection_status(self, status: str) -> None:
        self.update_status_bar()

    def watch_last_error(self, error: str) -> None:
        self.update_status_bar()

    def watch_selected_device_key(self, old_key, new_key) -> None:
        # This watcher now triggers redraws when the key changes due to cursor move
        self.log.debug(f"Selected device key changed from {old_key} to {new_key}")  # Debug selection change
        self.rebuild_event_log()  # Rebuild log based on new key
        self.rebuild_device_table()  # Rebuild table to update prefix

    # --- Action Handlers ---
    def action_quit(self) -> None:
        self.exit()

    # REMOVED action_cursor_up/down - DataTable handles this
    # REMOVED action_select_device
    def action_clear_filter(self) -> None:
        # Only clear if something is selected
        if self.selected_device_key is not None:
            self.selected_device_key = None
            # Optionally move cursor back to top?
            self.device_table.move_cursor(row=0, animate=False)

    # --- Custom Methods ---
    def update_status_bar(self) -> None:
        # (Identical)
        status_content = [f"MQTT: {self.connection_status}"]
        if self.last_error:
            status_content.append(f" | Last Error: [bold red]{self.last_error}[/]")
        status_content.append(f"\n{self.HELP_TEXT}")
        self.status_bar.update(" ".join(status_content))

    def rebuild_device_table(self) -> None:
        # FIX 1: Use row index `idx` as the key for add_row
        table = self.device_table
        key_at_cursor = None  # Store the actual (model, id) tuple key
        current_cursor_row = table.cursor_row
        if table.is_valid_row_index(current_cursor_row) and 0 <= current_cursor_row < len(self.sorted_device_keys):
            try:
                key_at_cursor = self.sorted_device_keys[current_cursor_row]
            except IndexError:
                key_at_cursor = None

        self.sorted_device_keys = sorted(
            self.device_data.keys(),
            key=lambda k: self.device_data[k]["last_seen"],
            reverse=True,
        )
        table.clear()
        new_cursor_pos = 0

        for idx, key in enumerate(self.sorted_device_keys):  # key is the (model, id_str) tuple
            dev_info = self.device_data.get(key)
            if not dev_info:
                continue
            last_seen_str = (
                dev_info["last_seen"].strftime("%H:%M:%S") if dev_info["last_seen"] != datetime.min else "Never"
            )
            id_display = str(dev_info.get("id", "N/A"))
            id_display = id_display[:27] + "..." if len(id_display) > 30 else id_display
            prefix = "➡️ " if key == self.selected_device_key else "  "
            model_str = prefix + dev_info.get("model", "N/A")
            count_str = str(dev_info.get("count", 0))
            row_values = (model_str, id_display, count_str, last_seen_str)
            try:
                # Use the index `idx` as the unique key for the DataTable row itself
                # We still use our internal `key` (the tuple) for selection state
                table.add_row(*row_values, key=idx)  # Using index as key
            except Exception as e:  # Catch potential errors more broadly here just in case
                # Log potentially different error types
                self.log.error(f"Error adding row {idx} for device key {key}: {e}")
                self.log.error(f"  Values passed: {row_values}")
                self.log.error(f"  Table columns ({len(table.columns)}): {[c.label for c in table.columns.values()]}")
                traceback.print_exc()  # Print full traceback for unexpected errors
                continue
            if key == key_at_cursor:
                new_cursor_pos = idx

        if table.row_count > 0:
            if table.is_valid_row_index(new_cursor_pos):
                table.move_cursor(row=new_cursor_pos, animate=False)
            else:
                table.move_cursor(row=0, animate=False)

        self.device_count = len(self.sorted_device_keys)
        self.devices_panel.border_title = f"Devices ({self.device_count})"

    def rebuild_event_log(self) -> None:
        # FIX 2: Add logging to trace filter logic
        log = self.event_log
        log.clear()

        # *** DEBUG: Log state before deciding source ***
        self.log.debug(f"Rebuilding event log. Selected Key: {self.selected_device_key}")
        # *** END DEBUG ***

        source_deque = self.global_event_log
        event_count = len(source_deque)
        max_count = self.max_global_events
        title_prefix = "Recent Events"

        if self.selected_device_key and self.selected_device_key in self.device_data:
            # *** DEBUG: Log when filtering is applied ***
            self.log.debug(f"Applying filter for key: {self.selected_device_key}")
            # *** END DEBUG ***
            dev_info = self.device_data[self.selected_device_key]
            model = dev_info["model"]
            dev_id = str(dev_info["id"])
            dev_id = dev_id[:22] + "..." if len(dev_id) > 25 else dev_id
            source_deque = dev_info["events"]  # Switch source
            event_count = len(source_deque)
            max_count = self.max_events_per_device
            title_prefix = f"Events for {model} / {dev_id}"
            # *** DEBUG: Log the source deque after switching ***
            self.log.debug(f"  Switched source_deque. Type: {type(source_deque)}, Len: {len(source_deque)}")
            # *** END DEBUG ***
        else:
            # *** DEBUG: Log when using global log ***
            self.log.debug(f"No valid filter key, using global log. Len: {len(source_deque)}")
            # *** END DEBUG ***
            pass  # Keep global source_deque

        self.events_panel.border_title = f"{title_prefix} ({event_count}/{max_count})"

        if not source_deque:
            log.write(Text.from_markup("[dim]No events to display.[/dim]"))
            return

        # (Rest of the event rendering logic is identical)
        writes = []
        for event_item in reversed(source_deque):
            ts_str, topic_str, event_data = "N/A", "N/A", {}
            if isinstance(event_item, tuple):
                timestamp, topic, event_data = event_item
                ts_str = timestamp.strftime("%H:%M:%S.%f")[:-3]
                topic_str = topic
            elif isinstance(event_item, dict):
                event_data = event_item
                ts_iso = event_data.get("_received_timestamp")
                if ts_iso:
                    try:
                        ts_dt = datetime.fromisoformat(ts_iso)
                        ts_str = ts_dt.strftime("%H:%M:%S.%f")[:-3]
                    except (ValueError, TypeError):
                        ts_str = ts_iso
                topic_str = event_data.get("_mqtt_topic", "N/A")
            else:
                event_data = {"error": "Unknown event format", "raw": str(event_item)}
            header_parts = []
            header_parts.append(ts_str) if ts_str != "N/A" else None
            header_parts.append(f"T: {topic_str}") if topic_str != "N/A" else None
            header_markup = f"[dim]{' | '.join(header_parts)}[/dim]"
            writes.append(Text.from_markup(header_markup))
            try:
                json_str = json.dumps(event_data, indent=2, ensure_ascii=False, default=str)
                syntax = Syntax(
                    json_str,
                    "json",
                    theme="default",
                    line_numbers=False,
                    word_wrap=True,
                )
                writes.append(syntax)
            except Exception as e:
                writes.append(Text(f"Error rendering event: {e}\nRaw: {str(event_data)}"))
            writes.append("")
        for item in writes:
            log.write(item)

    # --- MQTT Handling Methods (Identical logic) ---
    def handle_mqtt_message(self, topic: str, payload_str: str) -> None:
        # (Identical logic, triggers rebuilds)
        timestamp = datetime.now()
        data = None
        try:
            data = json.loads(payload_str)
            data["_received_timestamp"] = timestamp.isoformat()
            data["_mqtt_topic"] = topic
        except json.JSONDecodeError as e:
            self.last_error = f"Invalid JSON: {e}"
            data = {
                "error": "Invalid JSON",
                "raw": payload_str,
                "_rcv_ts": timestamp.isoformat(),
                "_topic": topic,
            }
            self.global_event_log.append((timestamp, topic, data))
            self.rebuild_event_log()
            return
        self.global_event_log.append((timestamp, topic, data))
        model = data.get("model", "?")
        dev_id = data.get("id", data.get("channel", data.get("sid", None)))
        if dev_id is None:
            topic_parts = topic.split("/")
            dev_id = topic_parts[-2] if len(topic_parts) >= 5 and topic_parts[-1] == "events" else f"Unknown_{topic}"
        dev_id_str = json.dumps(dev_id, sort_keys=True) if isinstance(dev_id, (list, dict)) else str(dev_id)
        device_key = (str(model), dev_id_str)
        device = self.device_data[device_key]
        device["model"] = str(model)
        device["id"] = dev_id
        device["count"] += 1
        device["last_seen"] = timestamp
        device["events"].append(data)
        device["last_raw_event"] = data
        self.rebuild_device_table()
        if self.selected_device_key is None or self.selected_device_key == device_key:
            self.rebuild_event_log()

    def handle_mqtt_connect(self, client, userdata, flags, rc, properties):
        # (Identical)
        if rc == 0:
            broker = client._host
            port = client._port
            self.connection_status = f"[green]Connected[/] to {broker}:{port}"
            self.last_error = ""
            client.subscribe(userdata["topic"])
        else:
            self.connection_status = f"[red]Failed[/] (Code: {rc})"
            self.last_error = f"Conn fail: {mqtt.connack_string(rc)}"

    def handle_mqtt_disconnect(self, client, userdata, rc, properties):
        # (Identical)
        self.connection_status = "[yellow]Disconnected[/]"
        if rc != 0:
            self.last_error = f"Disconnect (Code: {rc}). Reconnecting..."

    # --- Background Worker (Identical) ---
    async def mqtt_worker(self) -> None:
        # (Identical)
        global mqtt_client

        def worker_on_connect(c, ud, f, rc, p=None):
            self.call_from_thread(self.handle_mqtt_connect, c, ud, f, rc, p)

        def worker_on_disconnect(c, ud, rc, p=None):
            self.call_from_thread(self.handle_mqtt_disconnect, c, ud, rc, p)

        def worker_on_message(c, ud, msg):
            try:
                payload_str = msg.payload.decode("utf-8")
                self.call_from_thread(self.handle_mqtt_message, msg.topic, payload_str)
            except Exception as e:
                err_str = f"OnMsg Err: {e}"
                self.call_from_thread(setattr, self, "last_error", err_str)

        cid = f"rtlmon-{os.getpid()}"
        mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, cid)
        ud = {"topic": self.cli_args.topic}
        mqtt_client.user_data_set(ud)
        mqtt_client.on_connect = worker_on_connect
        mqtt_client.on_disconnect = worker_on_disconnect
        mqtt_client.on_message = worker_on_message
        if self.cli_args.username:
            mqtt_client.username_pw_set(self.cli_args.username, self.cli_args.password)
        mqtt_client._host = self.cli_args.broker
        mqtt_client._port = self.cli_args.port
        while not mqtt_shutdown_flag.is_set():
            try:
                if not mqtt_client.is_connected():
                    self.call_from_thread(setattr, self, "connection_status", "[yellow]Connecting[/]")
                    mqtt_client.connect(self.cli_args.broker, self.cli_args.port, 60)
                    mqtt_client.loop_forever(retry_first_connection=True)
            except Exception as e:
                self.call_from_thread(setattr, self, "connection_status", "[red]MQTT Error[/]")
                self.call_from_thread(setattr, self, "last_error", f"Worker err: {e}")
            try:
                if mqtt_client and mqtt_client.is_connected():
                    mqtt_client.disconnect()
            except:
                pass
            time.sleep(5)
        try:
            if mqtt_client and mqtt_client.is_connected():
                mqtt_client.disconnect()
        except:
            pass

    # --- FIX 3: Add Event Handler for Cursor Movement ---
    # --- DEBOUNCED Event Handler for Cursor Movement ---
    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Called when the DataTable cursor row changes. Debounces the update."""
        cursor_row = event.cursor_row
        new_key = None

        if 0 <= cursor_row < len(self.sorted_device_keys):
            try:
                new_key = self.sorted_device_keys[cursor_row]
            except IndexError:
                new_key = None  # Should not happen with check, but safeguard

        # Store the potentially new key
        self._pending_selection_key = new_key

        # Stop any existing debounce timer
        if self._cursor_debounce_timer is not None:
            self._cursor_debounce_timer.stop()
            # self.log.debug(f"Stopped existing cursor debounce timer.") # Optional debug

        # Start a new debounce timer
        self._cursor_debounce_timer = self.set_timer(CURSOR_DEBOUNCE_DELAY, self._apply_debounced_selection)
        # self.log.debug(f"Started cursor debounce timer for key: {new_key}") # Optional debug

    def _apply_debounced_selection(self) -> None:
        """Applies the pending selection after the debounce delay."""
        # self.log.debug(f"Debounce timer fired. Pending: {self._pending_selection_key},
        # Current: {self.selected_device_key}") # Optional debug
        pending_key = self._pending_selection_key  # Capture value before resetting timer ref

        # Reset timer reference *before* potentially triggering watcher
        self._cursor_debounce_timer = None
        self._pending_selection_key = None

        # Check if the pending key is different from the actual selected key
        if pending_key != self.selected_device_key:
            # Update the reactive variable, triggering the watcher and UI updates
            self.selected_device_key = pending_key


# --- Main Execution (Identical) ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitor rtl_433 MQTT messages (Textual UI).")
    # (Args parsing identical)
    parser.add_argument("-b", "--broker", default=DEFAULT_BROKER, help="MQTT broker address.")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_PORT, help="MQTT broker port.")
    parser.add_argument("-t", "--topic", default=DEFAULT_TOPIC, help="MQTT topic.")
    parser.add_argument("-u", "--username", default=None, help="MQTT username.")
    parser.add_argument("-pw", "--password", default=None, help="MQTT password.")
    parser.add_argument(
        "--max-events",
        type=int,
        help=f"Max global events (default: {MAX_GLOBAL_EVENTS}).",
    )
    parser.add_argument(
        "--max-dev-events",
        type=int,
        help=f"Max events per device (default: {MAX_EVENTS_PER_DEVICE}).",
    )
    parser.add_argument(
        "--data-file",
        default=DEFAULT_DATA_FILE,
        help=f"Path to data storage file (default: {DEFAULT_DATA_FILE}).",
    )
    parser.add_argument("--clear-data", action="store_true", help="Clear stored data file on start.")
    args = parser.parse_args()
    app = MQTTMonitorApp(cli_args=args)
    app.run()
