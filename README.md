# 📊 Conosle Monitor for rtl_433 (MQTT)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

A cli application built with [Textual](https://textual.textualize.io/) to monitor MQTT messages, specifically designed for JSON payloads generated by the excellent [rtl_433](https://github.com/merbanan/rtl_433) tool.

---

## ✨ Features

*   **Live MQTT Monitoring:** Connects to an MQTT broker and subscribes to a specified topic (defaults suitable for `rtl_433`).
*   **rtl_433 Optimized:** Parses incoming JSON payloads, expecting fields commonly generated by `rtl_433` (`model`, `id`, `channel`, etc.).
*   **Device Discovery:** Automatically detects unique devices based on `model` and `id`/`channel`/`sid`.
*   **Device Overview:** Displays a filterable list of discovered devices showing:
    *   Model Name
    *   Device ID
    *   Event Count
    *   Last Seen Timestamp
*   **Event Log:** Shows a scrollable log of recent events:
    *   Displays all messages by default.
    *   Filters events to the currently selected device in the list (updates on cursor movement).
    *   Includes full JSON payload with syntax highlighting.
    *   Shows message timestamp and originating MQTT topic.
*   **Persistence:** Remembers discovered devices (model, ID, count, last seen) between runs using a pickle file (configurable path). *Note: Does not save event history.*
*   **Responsive TUI:** Built with Textual for a modern terminal experience.
*   **Efficient Updates:** Uses debouncing for cursor navigation to prevent high CPU load during rapid scrolling.
*   **Configurable:** Set broker, port, topic, authentication, data file via command-line arguments.
*   **Styling:** Uses external CSS (`mqtt_monitor.css`) for layout and appearance.

## 📦 Installation

1.  **Prerequisites:**
    *   Python 3.8 or newer.
    *   An MQTT broker (like Mosquitto) running.
    *   `rtl_433` running and configured to publish events to your MQTT broker (e.g., `rtl_433 -F mqtt`).

2.  **Clone the repository:**
    ```bash
    git clone https://github.com/speckm/rtl432_mqtt_monitor.git
    cd rtl432_mqtt_monitor
    ```

3.  **(Recommended) Create a virtual environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows use `venv\Scripts\activate`
    ```

4.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## 🚀 Usage

Run the monitor from your terminal:

```bash
python mqtt_monitor.py [OPTIONS]

Command-line Options:

usage: mqtt_monitor.py [-h] [-b BROKER] [-p PORT] [-t TOPIC] [-u USERNAME] [-P PASSWORD] [--max-events MAX_EVENTS] [--max-dev-events MAX_DEV_EVENTS] [--data-file DATA_FILE] [--clear-data]

Monitor rtl_433 MQTT messages (Textual UI).

options:
  -h, --help            show this help message and exit
  -b BROKER, --broker BROKER
                        MQTT broker address.
  -p PORT, --port PORT  MQTT broker port.
  -t TOPIC, --topic TOPIC
                        MQTT topic.
  -u USERNAME, --username USERNAME
                        MQTT username.
  -pw PASSWORD, --password PASSWORD
                        MQTT password.
  --max-events MAX_EVENTS
                        Max global events (default: 100000).
  --max-dev-events MAX_DEV_EVENTS
                        Max events per device (default: 2000).
  --data-file DATA_FILE
                        Path to data storage file (default: rtl432_mqtt_monitor.pkl).
  --clear-data          Clear stored data file on start.

  python mqtt_monitor.py -b 192.168.1.100 -t "rtl_433/+/events"
```

## ⌨️ Controls

↑ / ↓: Navigate the device list. Filtering updates automatically.

c: Clear the device filter (show all recent events).

Ctrl+C: Save device list and exit the application.

## 🔧 Dependencies

*   [Textual](https://github.com/Textualize/textual): TUI framework.
*   [Paho-MQTT](https://github.com/eclipse/paho.mqtt.python): MQTT client library.

## 🎨 Customization

The layout and colors can be modified by editing the `mqtt_monitor.css` file.

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgements

*   The developers of [`rtl_433`](https://github.com/merbanan/rtl_433).
*   The developers of [Textual](https://github.com/Textualize/textual) and [Rich](https://github.com/Textualize/rich).
*   The [Eclipse Paho project](https://www.eclipse.org/paho/) (specifically the [Python MQTT Client](https://github.com/eclipse/paho.mqtt.python)).