# BLE LCD Command-Response System Design
**Date:** 2026-06-09  
**Project:** IoMT Phonocardiography  
**Scope:** Arduino LCD display + command-driven BLE data collection + Python OOP receiver

---

## Overview

This design adds an LCD UI to the Arduino that displays system state, and implements a command-response protocol where:
- **Arduino** waits for Python to send collection parameters, then collects data for a fixed duration
- **Python** sends requests, receives batches in real-time, validates sample count, and returns the full signal

The Python class is designed for backend reuse—it hands off the signal to a separate analyzer class.

---

## Arduino Behavior

### States (with LCD Display)

**1. INITIALIZING**
- Shows spinner + "Starting..." text
- Duration: <1 second (until BLE is ready)
- Battery % always visible at top

**2. IDLE**
- Shows battery % at top
- Shows blinking BT icon + "ADV" text (advertising)
- Shows "advertising" label
- Waits for BLE client to connect

**3. CONNECTED**
- Shows battery % at top
- Shows BT icon + checkmark + "CONN" text
- Shows "connected" label (highlighted/inverted)
- Waits for Python to send analysis request

**4. ANALYZING**
- Shows battery % at top
- Shows patient name (12-15 chars, truncated, no scrolling)
- Shows spinner + "Analyzing" text
- Actively collecting data

**Display Update:** Redrawn every ~30ms in main loop. Sampling timer interrupt runs independently—display updates do NOT block data collection.

### BLE Communication Protocol

Binary packet format (Arduino receives from Python):

```
Byte 0:        Command type (0x01 = START, 0x02 = STOP)
Bytes 1-4:     SAMPLE_RATE (uint32_t, little-endian, e.g., 500 Hz)
Bytes 5-6:     OVERSAMPLE_COUNT (uint16_t, little-endian, e.g., 8)
Bytes 7-8:     BATCH_SIZE (uint16_t, little-endian, e.g., 6)
Bytes 9-12:    ANALYSIS_TIME_SECONDS (uint32_t, little-endian, e.g., 60)
Bytes 13-28:   Patient name (null-terminated C string, max 16 bytes)
```

**Arduino → Python:** Raw sample batches via BLE NOTIFY characteristic (same as current format: array of uint16_t values).

### Sampling & Timing

- Timer interrupt fires at `1000000 / SAMPLE_RATE` microseconds
- Each interrupt reads oversampled ADC value, increments buffer index
- When buffer reaches BATCH_SIZE, sets flag and clears index
- Main loop sends batch via BLE NOTIFY and resets flag
- **Critical:** Timer interrupt is never blocked by display or BLE operations

### Flow

1. Arduino starts in INITIALIZING (spinner) → enters IDLE (advertising) when BLE ready
2. Python connects → Arduino enters CONNECTED state
3. Python sends START packet with parameters → Arduino transitions to ANALYZING
4. Arduino sets up timer with SAMPLE_RATE, initializes a duration timer for ANALYSIS_TIME_SECONDS
5. Arduino collects samples and sends batches continuously
6. After ANALYSIS_TIME_SECONDS expires, Arduino stops sampling and returns to IDLE
7. Arduino is ready for next analysis request

---

## Python Class Design

### Class: `PCGClient`

**Purpose:** Handle all BLE communication, command sending, batch reception, and signal accumulation.

**Constructor:**
```python
def __init__(self, device_name="PCG_Monitor_Raw"):
    """
    device_name: BLE device name to connect to
    """
```

**Methods:**

```python
def connect():
    """
    Establish BLE connection to Arduino.
    Raises BLEConnectionError if device not found or connection fails.
    """

def disconnect():
    """
    Close BLE connection gracefully.
    """

def is_connected() -> bool:
    """
    Return True if BLE connection is active and device is responsive.
    """

def analyze(self, sample_rate, oversample_count, batch_size, patient_name, analysis_time_seconds) -> Generator:
    """
    Send START command to Arduino with analysis parameters.
    Yields batches of samples as they arrive from BLE (generator).
    
    Args:
        sample_rate (int): Sampling frequency in Hz (e.g., 500)
        oversample_count (int): Number of ADC reads per sample (e.g., 8)
        batch_size (int): Samples per BLE packet (e.g., 6)
        patient_name (str): Patient identifier, truncated to 15 chars
        analysis_time_seconds (int): Duration to collect data (e.g., 60)
    
    Yields:
        np.ndarray of shape (batch_size,), dtype uint16
    
    Generator exits naturally when Arduino stops (after analysis_time_seconds).
    Raises BLEConnectionError if connection drops.
    """

def get_full_signal() -> np.ndarray:
    """
    Return all accumulated samples from last analyze() call.
    Expected: sample_rate * analysis_time_seconds samples.
    
    Behavior:
    - If fewer samples than expected: wait up to 2 seconds for missing batches, then warn and return what we have
    - If more samples than expected: trim to exact expected count
    - Returns: np.ndarray of shape (expected_samples,), dtype uint16
    
    Raises BLEConnectionError if validation times out.
    """

def _send_command(self, cmd_type, sample_rate, oversample_count, batch_size, analysis_time_seconds, patient_name):
    """Internal: encode binary packet and send via BLE write characteristic."""

def _on_batch_received(self, sender, data):
    """Internal: BLE notification callback. Parse incoming batch and queue it."""
```

### Usage Pattern

```python
client = PCGClient(device_name="PCG_Monitor_Raw")
client.connect()

if not client.is_connected():
    raise RuntimeError("Failed to connect to device")

# Collect 60 seconds at 500 Hz
for batch in client.analyze(sample_rate=500, oversample_count=8, batch_size=6, 
                           patient_name="John Doe", analysis_time_seconds=60):
    print(f"Received batch: {batch}")  # batch is numpy array of 6 samples

# After loop: get full signal (should be ~30,000 samples)
full_signal = client.get_full_signal()

# Pass to analyzer
analyzer = PCGAnalyzer()
result = analyzer.process(full_signal)

client.disconnect()
```

### Implementation Notes

- Uses **bleak** library (cross-platform BLE)
- Internal state tracks: `_accumulated_data` (list/deque of batches), `_sample_rate`, `_analysis_time_seconds`
- Accumulation happens in the BLE callback; main thread yields from a queue
- BLE write/notify characteristics must match Arduino UUIDs (from current code):
  - Service UUID: `12345678-1234-1234-1234-123456789abc`
  - Characteristic UUID: `abcd1234-ab12-cd34-ef56-123456789abc`

---

## File Structure

```
arduino/
  └── ble_lcd_analyzer/
      └── ble_lcd_analyzer.ino       ← new code for LCD + command-response BLE

backend/
  └── pcg_ble_client.py             ← PCGClient class
  └── requirements.txt              ← dependencies (bleak, numpy)
```

---

## Data Integrity

**Sample Count Validation:**

Expected samples = `sample_rate * analysis_time_seconds`

Example: 500 Hz × 60 seconds = 30,000 samples

- Arduino collects for exactly the specified duration
- Python accumulates batches
- `get_full_signal()` validates and trims to expected count
- If data loss occurred (fewer samples), user is warned but code doesn't fail

---

## Error Handling

- **BLE connection drop during analyze():** Generator raises `BLEConnectionError`
- **Timeout waiting for full signal:** `get_full_signal()` warns and returns partial data
- **Device not found:** `connect()` raises `BLEConnectionError`
- **Invalid packet:** Silently dropped (logged), collection continues

---

## Testing Checklist

**Arduino:**
- [ ] INITIALIZING state shows spinner, transitions to IDLE within 1s
- [ ] IDLE state shows battery % and blinking BT icon
- [ ] CONNECTED state shows connected indicator
- [ ] ANALYZING state shows patient name (truncated) and spinner
- [ ] Samples collected for exact duration (no premature stop)
- [ ] Sample count = sample_rate × analysis_time_seconds ± 1 batch
- [ ] Display updates don't jitter sampling (check with oscilloscope if available)

**Python:**
- [ ] `connect()` finds device and establishes BLE
- [ ] `is_connected()` returns correct status
- [ ] `analyze()` sends binary packet correctly
- [ ] Generator yields batches in real-time
- [ ] `get_full_signal()` returns expected sample count
- [ ] Connection drop raises exception
- [ ] Patient name truncated correctly on Arduino LCD
