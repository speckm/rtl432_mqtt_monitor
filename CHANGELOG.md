# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-04-07

### Added
- MQTT client connection to broker.
- Parsing of `rtl_433` JSON formatted messages.
- Textual TUI interface.
- Display discovered devices in a DataTable.
- Display event count and last seen time per device.
- Display list of recent global events or filtered events per device in RichLog.
- Syntax highlighting for JSON event data.
- Filtering of event log by selecting device in the table (via cursor movement).
- Persistence of discovered device data (excluding events) using pickle.
- Command-line arguments for broker, port, topic, auth, data file, clearing data.
- Basic status bar for connection status and errors.
- Keyboard navigation for device list and filtering.
- Debouncing for cursor movement to reduce CPU load during navigation.
- External CSS styling (`rtl432_mqtt_monitor.css`).
- Basic error handling for MQTT connection and JSON parsing.
- MIT License, README, CHANGELOG, .gitignore.
