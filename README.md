# IoMT Phonocardiography

An Internet of Medical Things lab project for collecting and streaming phonocardiography (PCG) sensor data from a microcontroller to a computer over `Serial` or `BLE`.

The repository currently contains Arduino sender sketches, Python receiver scripts, and an older desktop plotting prototype.

## Repo Layout

- `sender/serial/serial.ino`: ESP32 serial sender with binary packet framing and sequence numbers.
- `sender/BLE/BLE.ino`: ESP32 BLE sender for the newer `PCG_Monitor` BLE path.
- `reciver/serialReceiver.py`: Python serial receiver for framed binary packets.
- `reciver/bleReceiver.py`: Python BLE receiver for framed binary packets.
- `iomt_simple_data_sender.ino`: older standalone sender sketch.
- `iomt_simple_plot.py`: older BLE desktop plotting app using PyQt and PyQtGraph.
- `aman/IoMT_Phonocardiography/`: copied snapshot of an earlier version of the project.

## How It Works

The main data path is:

1. A microcontroller samples an analog PCG signal from a sensor.
2. Samples are grouped into small batches.
3. The batch is sent either over USB serial or Bluetooth Low Energy.
4. A Python receiver on the computer reads incoming packets and reports packet health.

The serial sender in [sender/serial/serial.ino](/Users/mosi_dev/Developer/university/imot/sender/serial/serial.ino) is the most complete transmission path in this repo because it includes:

- fixed packet headers
- a sequence number
- a sample count
- batch-based transmission
- dropped-batch counters for benchmarking

## Current Status

The repository is partially in-progress.

- The receiver folder is intentionally minimal right now and contains only `bleReceiver.py` and `serialReceiver.py`.
- Both receiver scripts still import `receiver_common`, but that file is not currently present in `reciver/`.
- Because of that, the Python receivers will not run successfully until the shared helper module is restored or the logic is moved into each file.
- The BLE sender in `sender/BLE/BLE.ino` sends raw sample batches, while `reciver/bleReceiver.py` expects framed packets with headers and sequence numbers. Those two files are not currently protocol-compatible.
- The older plotting app in `iomt_simple_plot.py` is built around a different BLE device name and characteristic UUID than the newer receiver path.

## Serial Path

### Sender

The serial sender sketch:

- samples from `A0`
- oversamples each point
- groups samples into batches of `6`
- prepends packet metadata
- writes binary packets over serial at `460800` baud

Packet shape used by the serial receiver:

```text
header1 | header2 | seq(uint32) | count(uint16) | 6 samples(uint16 each)
```

Expected header bytes:

```text
0xAA 0x55
```

### Receiver

[reciver/serialReceiver.py](/Users/mosi_dev/Developer/university/imot/reciver/serialReceiver.py) is intended to:

- open a serial port
- search for the packet header
- unpack binary packets
- validate the packet
- track missing sequence numbers
- print packet statistics

Example intended usage:

```bash
python reciver/serialReceiver.py --port /dev/cu.usbmodemXXXX --baud 460800
```

This currently requires restoring `receiver_common.py` or refactoring the shared constants and helpers back into the script.

## BLE Path

### Newer BLE Sender

[sender/BLE/BLE.ino](/Users/mosi_dev/Developer/university/imot/sender/BLE/BLE.ino) advertises:

- device name: `PCG_Monitor`
- characteristic UUID: `abcd1234-ab12-cd34-ef56-123456789abc`

It batches `6` samples and sends the raw sample bytes via BLE notifications.

### Receiver Expectation

[reciver/bleReceiver.py](/Users/mosi_dev/Developer/university/imot/reciver/bleReceiver.py) is intended to:

- scan for the `PCG_Monitor` device
- connect with `bleak`
- subscribe to notifications
- unpack framed binary packets
- detect missing packets
- print periodic statistics

Example intended usage:

```bash
python reciver/bleReceiver.py --device-name PCG_Monitor
```

At the moment, the receiver expects a framed packet format with headers and sequence numbers, but the sender sketch sends only raw sample arrays. The sender and receiver need to be aligned before this path will work.

## Older Prototype Files

The files at the repository root represent an older prototype:

- [iomt_simple_data_sender.ino](/Users/mosi_dev/Developer/university/imot/iomt_simple_data_sender.ino)
- [iomt_simple_plot.py](/Users/mosi_dev/Developer/university/imot/iomt_simple_plot.py)

That prototype uses:

- BLE device name: `Nano_ESP32_Heart`
- characteristic UUID: `12345678-1234-5678-1234-56789abcdef1`
- string-based BLE payloads instead of the newer batch packet format

Use those two files together if you want to explore the older demo path.

## Dependencies

Python dependencies depend on which receiver path you want to run.

For the minimal receivers:

```bash
pip install pyserial bleak
```

For the older plotting app:

```bash
pip install numpy pyqtgraph PyQt6 scipy bleak qasync
```

## Recommended Next Steps

To make the main receiver path usable again:

1. Restore or recreate `reciver/receiver_common.py`.
2. Decide on one BLE protocol format and make both sender and receiver match it.
3. Add a small `requirements.txt` or `pyproject.toml` for the Python tools.
4. Rename `reciver/` to `receiver/` if you want cleaner long-term structure.

## License

This repository includes a [LICENSE](/Users/mosi_dev/Developer/university/imot/LICENSE) file.
