# BLE LCD Command-Response System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a command-driven BLE data collection system where Arduino displays state on LCD and collects samples for a fixed duration, and Python class streams batches in real-time with full-signal validation.

**Architecture:** 
- Arduino combines the LCD test patterns with timer-driven sampling, adding a BLE command parser to accept analysis parameters and a duration timer to auto-stop collection
- Python uses bleak (cross-platform BLE) with a generator-based API for streaming batches and post-analysis validation
- Binary packet format minimizes parsing overhead on the Arduino

**Tech Stack:** Arduino (U8G2 display, BLE2902), Python 3.8+ (bleak, numpy)

---

## File Structure

```
arduino/
  ble_lcd_analyzer/
    ble_lcd_analyzer.ino          ← Arduino code (LCD display + BLE command parsing + timer sampling)

backend/
  pcg_ble_client.py               ← PCGClient class (BLE communication, batch accumulation)
  requirements.txt                ← Python dependencies (bleak, numpy)
  tests/
    test_pcg_ble_client.py        ← Unit + integration tests
```

---

## Task Breakdown

### Task 1: Arduino Project Setup & BLE Foundation

**Files:**
- Create: `arduino/ble_lcd_analyzer/ble_lcd_analyzer.ino`

- [ ] **Step 1: Create skeleton with headers and BLE UUIDs**

Create `arduino/ble_lcd_analyzer/ble_lcd_analyzer.ino`:

```cpp
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <U8g2lib.h>
#include <Wire.h>
#include <math.h>

// --- Configuration ---
const int ANALOG_PIN = A0;
const int TIMER_ID = 0;
const int TIMER_PRESCALER = 80;
const int TIMER_COUNT_UP = true;

// BLE UUIDs (from existing code)
#define SERVICE_UUID        "12345678-1234-1234-1234-123456789abc"
#define CHARACTERISTIC_UUID "abcd1234-ab12-cd34-ef56-123456789abc"

// Display
U8G2_SSD1306_48X64_WINSTAR_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);

// --- State Machine ---
enum State {
  STATE_INITIALIZING,
  STATE_IDLE,
  STATE_CONNECTED,
  STATE_ANALYZING
};

State currentState = STATE_INITIALIZING;

// --- BLE ---
BLEServer* pServer = NULL;
BLECharacteristic* pCharacteristic = NULL;
bool deviceConnected = false;

// --- Sampling ---
volatile uint16_t sampleBuffer[6];  // MAX BATCH_SIZE for now
volatile int sampleIndex = 0;
volatile bool batchReady = false;
hw_timer_t* timer = NULL;

int sampleRate = 500;
int oversampleCount = 8;
int batchSize = 6;
int analysisTimeSeconds = 60;
char patientName[16] = {0};

// --- Analysis Timer ---
unsigned long analysisStartTime = 0;
bool isAnalyzing = false;

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  u8g2.begin();
  
  // BLE setup will follow in later tasks
  
  currentState = STATE_IDLE;  // Move out of INITIALIZING after setup
}

void loop() {
  // Display update will follow
  // BLE polling will follow
  
  delay(30);
}
```

- [ ] **Step 2: Verify it compiles**

In Arduino IDE, select ESP32 Nano board, compile. Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add arduino/ble_lcd_analyzer/ble_lcd_analyzer.ino
git commit -m "arduino: scaffold BLE LCD analyzer sketch"
```

---

### Task 2: Implement LCD Display & State Rendering

**Files:**
- Modify: `arduino/ble_lcd_analyzer/ble_lcd_analyzer.ino`

- [ ] **Step 1: Add battery reading function**

Add after the `#include` section and before `setup()`:

```cpp
// --- Battery Helper ---
int batteryPercent() {
  uint32_t mv = 0;
  for (int i = 0; i < 16; i++) mv += analogReadMilliVolts(A0);
  mv /= 16;
  float vbat = mv * 2.0 / 1000.0;   // x2 for divider, mV -> V
  int pct = (int)((vbat - 3.3) / (4.2 - 3.3) * 100.0);
  return constrain(pct, 0, 100);
}
```

- [ ] **Step 2: Add display drawing helpers**

Add before `setup()`:

```cpp
// --- Display Helpers ---
void drawBT(int cx, int ty) {
  int top = ty, bot = ty + 10, q1 = ty + 3, q3 = ty + 7;
  int r = cx + 3, l = cx - 3;
  u8g2.drawLine(cx, top, cx, bot);
  u8g2.drawLine(cx, top, r, q1);
  u8g2.drawLine(r, q1, l, q3);
  u8g2.drawLine(l, q1, r, q3);
  u8g2.drawLine(r, q3, cx, bot);
}

void drawCheck(int x, int y) {
  u8g2.drawLine(x, y + 2, x + 2, y + 4);
  u8g2.drawLine(x + 2, y + 4, x + 6, y - 1);
}

void textHighlight(int x, int y, int w, int h, const char* s, const uint8_t* font) {
  u8g2.drawBox(x, y, w, h);
  u8g2.setDrawColor(0);
  u8g2.setFont(font);
  u8g2.setCursor(x + 2, y + h - 2);
  u8g2.print(s);
  u8g2.setDrawColor(1);
}

void drawSpinner(int cx, int cy, int r) {
  u8g2.drawCircle(cx, cy, r);
  float a = millis() / 90.0;
  int sx = cx + (int)(cos(a) * r);
  int sy = cy + (int)(sin(a) * r);
  u8g2.drawDisc(sx, sy, 2);
}

void drawTopPanel() {
  u8g2.setFont(u8g2_font_5x7_tf);
  u8g2.setCursor(2, 10);
  u8g2.print("Bat:");
  u8g2.print(batteryPercent());
  u8g2.print("%");
  u8g2.drawHLine(0, 13, 48);
}
```

- [ ] **Step 3: Add display state rendering function**

Add before `setup()`:

```cpp
void updateDisplay() {
  u8g2.clearBuffer();
  drawTopPanel();

  switch (currentState) {
    case STATE_INITIALIZING: {
      u8g2.setFont(u8g2_font_5x7_tf);
      u8g2.setCursor(6, 34);
      u8g2.print("Starting");
      drawSpinner(24, 55, 6);
      break;
    }
    
    case STATE_IDLE: {
      if ((millis() / 400) % 2) drawBT(9, 20);
      u8g2.setFont(u8g2_font_5x7_tf);
      u8g2.setCursor(18, 30);
      u8g2.print("ADV");
      u8g2.setFont(u8g2_font_4x6_tf);
      u8g2.setCursor(2, 44);
      u8g2.print("advertising");
      break;
    }
    
    case STATE_CONNECTED: {
      drawBT(9, 20);
      drawCheck(34, 22);
      u8g2.setFont(u8g2_font_5x7_tf);
      u8g2.setCursor(18, 30);
      u8g2.print("CONN");
      textHighlight(2, 38, 46, 9, "connected", u8g2_font_4x6_tf);
      break;
    }
    
    case STATE_ANALYZING: {
      u8g2.setFont(u8g2_font_4x6_tf);
      u8g2.setCursor(2, 22);
      u8g2.print("User:");
      u8g2.setFont(u8g2_font_5x7_tf);
      u8g2.setCursor(2, 33);
      // Truncate patient name to 12-15 chars
      char displayName[16];
      strncpy(displayName, patientName, 15);
      displayName[15] = '\0';
      u8g2.print(displayName);
      u8g2.setFont(u8g2_font_4x6_tf);
      u8g2.setCursor(12, 45);
      u8g2.print("Analyzing");
      drawSpinner(24, 55, 6);
      break;
    }
  }

  u8g2.sendBuffer();
}
```

- [ ] **Step 4: Update loop to call updateDisplay**

Replace the `loop()` function:

```cpp
void loop() {
  updateDisplay();
  delay(30);  // ~30 fps
}
```

- [ ] **Step 5: Verify it compiles**

Compile in Arduino IDE. Expected: No errors.

- [ ] **Step 6: Commit**

```bash
git add arduino/ble_lcd_analyzer/ble_lcd_analyzer.ino
git commit -m "arduino: add LCD display with state rendering"
```

---

### Task 3: Implement BLE Server & Connection Callbacks

**Files:**
- Modify: `arduino/ble_lcd_analyzer/ble_lcd_analyzer.ino`

- [ ] **Step 1: Add BLE callback class**

Add before `setup()`:

```cpp
// --- BLE Callbacks ---
class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer* pServer) {
    deviceConnected = true;
    currentState = STATE_CONNECTED;
    Serial.println("BLE: Client connected");
  }
  void onDisconnect(BLEServer* pServer) {
    deviceConnected = false;
    isAnalyzing = false;
    currentState = STATE_IDLE;
    Serial.println("BLE: Client disconnected");
    BLEDevice::startAdvertising();
  }
};
```

- [ ] **Step 2: Initialize BLE in setup()**

Replace the `setup()` function entirely:

```cpp
void setup() {
  Serial.begin(115200);
  delay(500);
  analogReadResolution(12);
  
  u8g2.begin();
  currentState = STATE_INITIALIZING;
  
  // Initialize BLE
  BLEDevice::init("PCG_Monitor_Raw");
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new ServerCallbacks());

  BLEService* pService = pServer->createService(SERVICE_UUID);
  pCharacteristic = pService->createCharacteristic(
    CHARACTERISTIC_UUID,
    BLECharacteristic::PROPERTY_NOTIFY | BLECharacteristic::PROPERTY_WRITE
  );
  pCharacteristic->addDescriptor(new BLE2902());
  pService->start();

  BLEAdvertising* pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(true);
  pAdvertising->setMinPreferred(0x06);
  BLEDevice::startAdvertising();

  Serial.println("BLE: Advertising started");
  
  // Brief initialization animation, then go idle
  currentState = STATE_IDLE;
}
```

- [ ] **Step 3: Verify it compiles and runs**

Flash to device. Expected: Serial output shows "BLE: Advertising started", LCD shows blinking BT icon with "ADV".

- [ ] **Step 4: Commit**

```bash
git add arduino/ble_lcd_analyzer/ble_lcd_analyzer.ino
git commit -m "arduino: implement BLE server with state callbacks"
```

---

### Task 4: Implement BLE Command Parser (START Packet)

**Files:**
- Modify: `arduino/ble_lcd_analyzer/ble_lcd_analyzer.ino`

- [ ] **Step 1: Add packet parsing function**

Add before `setup()`:

```cpp
// --- BLE Command Parsing ---
void parseStartCommand(uint8_t* data, size_t length) {
  if (length < 29) {
    Serial.println("BLE: Invalid packet length");
    return;
  }
  
  // Byte 0: command type (should be 0x01 for START)
  uint8_t cmdType = data[0];
  if (cmdType != 0x01) {
    Serial.println("BLE: Invalid command type");
    return;
  }
  
  // Bytes 1-4: SAMPLE_RATE (little-endian)
  sampleRate = (uint32_t)data[1] | ((uint32_t)data[2] << 8) | ((uint32_t)data[3] << 16) | ((uint32_t)data[4] << 24);
  
  // Bytes 5-6: OVERSAMPLE_COUNT (little-endian)
  oversampleCount = (uint16_t)data[5] | ((uint16_t)data[6] << 8);
  
  // Bytes 7-8: BATCH_SIZE (little-endian)
  batchSize = (uint16_t)data[7] | ((uint16_t)data[8] << 8);
  
  // Bytes 9-12: ANALYSIS_TIME_SECONDS (little-endian)
  analysisTimeSeconds = (uint32_t)data[9] | ((uint32_t)data[10] << 8) | ((uint32_t)data[11] << 16) | ((uint32_t)data[12] << 24);
  
  // Bytes 13-28: Patient name (null-terminated)
  memset(patientName, 0, sizeof(patientName));
  strncpy(patientName, (const char*)&data[13], 15);
  patientName[15] = '\0';
  
  Serial.print("BLE: START command - SR=");
  Serial.print(sampleRate);
  Serial.print(" OS=");
  Serial.print(oversampleCount);
  Serial.print(" BS=");
  Serial.print(batchSize);
  Serial.print(" Time=");
  Serial.print(analysisTimeSeconds);
  Serial.print(" Patient=");
  Serial.println(patientName);
  
  // Start analysis
  startAnalysis();
}

void startAnalysis() {
  isAnalyzing = true;
  analysisStartTime = millis();
  sampleIndex = 0;
  batchReady = false;
  currentState = STATE_ANALYZING;
  
  // Set up timer with computed sample rate
  if (timer != NULL) {
    timerEnd(timer);
  }
  
  int timerIntervalUs = 1000000 / sampleRate;
  timer = timerBegin(TIMER_ID, TIMER_PRESCALER, TIMER_COUNT_UP);
  timerAttachInterrupt(timer, &onTimer, true);
  timerAlarmWrite(timer, timerIntervalUs, true);
  timerAlarmEnable(timer);
  
  Serial.println("Analysis started");
}
```

- [ ] **Step 2: Add timer interrupt handler**

Add before `setup()`:

```cpp
// --- Sampling Timer ISR ---
void ARDUINO_ISR_ATTR onTimer() {
  if (!isAnalyzing || batchReady) return;

  uint32_t sum = 0;
  for (int i = 0; i < oversampleCount; i++) {
    sum += analogRead(ANALOG_PIN);
  }
  sampleBuffer[sampleIndex] = sum / oversampleCount;
  sampleIndex++;

  if (sampleIndex >= batchSize) {
    sampleIndex = 0;
    batchReady = true;
  }
}
```

- [ ] **Step 3: Add characteristic write callback**

Add the callback class before `setup()`:

```cpp
class CharacteristicCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* pCharacteristic) {
    std::string value = pCharacteristic->getValue();
    if (value.length() > 0) {
      Serial.print("BLE: Received ");
      Serial.print(value.length());
      Serial.println(" bytes");
      parseStartCommand((uint8_t*)value.data(), value.length());
    }
  }
};
```

- [ ] **Step 4: Register callback in setup()**

Find the line `pCharacteristic->addDescriptor(new BLE2902());` in `setup()` and add after it:

```cpp
pCharacteristic->setCallbacks(new CharacteristicCallbacks());
```

- [ ] **Step 5: Update loop to send batches**

Replace the `loop()` function:

```cpp
void loop() {
  // Check if analysis time has expired
  if (isAnalyzing && (millis() - analysisStartTime) > (analysisTimeSeconds * 1000UL)) {
    isAnalyzing = false;
    if (timer != NULL) {
      timerEnd(timer);
      timer = NULL;
    }
    currentState = STATE_CONNECTED;
    Serial.println("Analysis finished");
  }

  // Send batch if ready
  if (batchReady && deviceConnected && isAnalyzing) {
    noInterrupts();
    uint16_t localBatch[batchSize];
    memcpy(localBatch, (const void*)sampleBuffer, batchSize * sizeof(uint16_t));
    batchReady = false;
    interrupts();

    pCharacteristic->setValue((uint8_t*)localBatch, batchSize * sizeof(uint16_t));
    pCharacteristic->notify();
  } else if (batchReady) {
    batchReady = false;
  }

  updateDisplay();
  delay(30);
}
```

- [ ] **Step 6: Verify it compiles**

Compile in Arduino IDE. Expected: No errors.

- [ ] **Step 7: Commit**

```bash
git add arduino/ble_lcd_analyzer/ble_lcd_analyzer.ino
git commit -m "arduino: implement BLE command parser and timer-based sampling"
```

---

### Task 5: Python Project Setup & Requirements

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/pcg_ble_client.py` (skeleton)

- [ ] **Step 1: Create requirements.txt**

Create `backend/requirements.txt`:

```
bleak==0.20.2
numpy>=1.21.0
```

- [ ] **Step 2: Create Python package skeleton**

Create `backend/pcg_ble_client.py`:

```python
import asyncio
import struct
import numpy as np
from bleak import BleakClient, BleakScanner
from typing import Generator

class BLEConnectionError(Exception):
    """Raised when BLE connection fails or drops."""
    pass

class PCGClient:
    """
    Client for controlling Arduino PCG data collection via BLE.
    Sends analysis requests and receives phonocardiogram signal batches.
    """
    
    SERVICE_UUID = "12345678-1234-1234-1234-123456789abc"
    CHARACTERISTIC_UUID = "abcd1234-ab12-cd34-ef56-123456789abc"
    
    def __init__(self, device_name="PCG_Monitor_Raw"):
        self.device_name = device_name
        self.client = None
        self._sample_rate = 0
        self._analysis_time_seconds = 0
        self._accumulated_data = []
        self._batch_queue = asyncio.Queue()
    
    async def connect(self):
        """Establish BLE connection to Arduino."""
        pass
    
    async def disconnect(self):
        """Close BLE connection."""
        pass
    
    def is_connected(self) -> bool:
        """Return True if BLE connection is active."""
        pass
    
    async def analyze(self, sample_rate: int, oversample_count: int, batch_size: int, 
                     patient_name: str, analysis_time_seconds: int) -> Generator:
        """
        Send analysis request and yield batches as they arrive.
        Generator exits when Arduino finishes collection.
        """
        pass
    
    def get_full_signal(self) -> np.ndarray:
        """Return all accumulated samples, validated to expected count."""
        pass
    
    def _encode_start_packet(self, sample_rate: int, oversample_count: int, batch_size: int,
                            analysis_time_seconds: int, patient_name: str) -> bytes:
        """Encode binary START packet."""
        pass
    
    async def _notification_handler(self, sender, data: bytearray):
        """BLE notification callback: parse batch and queue it."""
        pass
```

- [ ] **Step 3: Verify Python environment**

Run:
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Expected: No errors, all packages installed.

- [ ] **Step 4: Commit**

```bash
git add backend/requirements.txt backend/pcg_ble_client.py
git commit -m "backend: scaffold Python BLE client project"
```

---

### Task 6: Implement PCGClient Binary Packet Encoding

**Files:**
- Modify: `backend/pcg_ble_client.py`

- [ ] **Step 1: Implement packet encoder**

Find the `_encode_start_packet` method and replace it:

```python
def _encode_start_packet(self, sample_rate: int, oversample_count: int, batch_size: int,
                        analysis_time_seconds: int, patient_name: str) -> bytes:
    """
    Encode binary START packet:
    Byte 0:        Command type (0x01)
    Bytes 1-4:     SAMPLE_RATE (uint32_t, little-endian)
    Bytes 5-6:     OVERSAMPLE_COUNT (uint16_t, little-endian)
    Bytes 7-8:     BATCH_SIZE (uint16_t, little-endian)
    Bytes 9-12:    ANALYSIS_TIME_SECONDS (uint32_t, little-endian)
    Bytes 13-28:   Patient name (null-terminated, max 16 bytes)
    """
    # Truncate patient name to 15 chars (16 bytes with null terminator)
    truncated_name = patient_name[:15].encode('utf-8')
    
    # Build packet
    packet = bytearray(29)  # Fixed size: 1 + 4 + 2 + 2 + 4 + 16
    
    packet[0] = 0x01  # START command
    struct.pack_into('<I', packet, 1, sample_rate)
    struct.pack_into('<H', packet, 5, oversample_count)
    struct.pack_into('<H', packet, 7, batch_size)
    struct.pack_into('<I', packet, 9, analysis_time_seconds)
    
    # Copy patient name (null-padded)
    packet[13:13+len(truncated_name)] = truncated_name
    # Rest is zeros (null padding)
    
    return bytes(packet)
```

- [ ] **Step 2: Write unit test for encoding**

Create `backend/tests/__init__.py`:
```python
```

Create `backend/tests/test_pcg_ble_client.py`:

```python
import pytest
import struct
from pcg_ble_client import PCGClient

def test_encode_start_packet():
    """Test binary packet encoding."""
    client = PCGClient()
    
    packet = client._encode_start_packet(
        sample_rate=500,
        oversample_count=8,
        batch_size=6,
        analysis_time_seconds=60,
        patient_name="Alice"
    )
    
    assert len(packet) == 29
    assert packet[0] == 0x01  # Command type
    
    # Verify sample rate (little-endian)
    sr = struct.unpack('<I', packet[1:5])[0]
    assert sr == 500
    
    # Verify oversample count
    os = struct.unpack('<H', packet[5:7])[0]
    assert os == 8
    
    # Verify batch size
    bs = struct.unpack('<H', packet[7:9])[0]
    assert bs == 6
    
    # Verify time
    t = struct.unpack('<I', packet[9:13])[0]
    assert t == 60
    
    # Verify patient name
    name = packet[13:13+5].decode('utf-8')
    assert name == "Alice"

def test_encode_truncates_long_name():
    """Test that patient name is truncated to 15 chars."""
    client = PCGClient()
    
    long_name = "A" * 20
    packet = client._encode_start_packet(500, 8, 6, 60, long_name)
    
    # Extract name part
    name_section = packet[13:29]
    name_str = name_section.split(b'\x00')[0].decode('utf-8')
    assert len(name_str) == 15
    assert name_str == "A" * 15
```

- [ ] **Step 3: Run tests**

```bash
cd backend
python3 -m pytest tests/test_pcg_ble_client.py -v
```

Expected: Both tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/pcg_ble_client.py backend/tests/test_pcg_ble_client.py backend/tests/__init__.py
git commit -m "backend: implement binary packet encoding with tests"
```

---

### Task 7: Implement PCGClient BLE Connection Methods

**Files:**
- Modify: `backend/pcg_ble_client.py`

- [ ] **Step 1: Implement connect()**

Replace the `connect` method:

```python
async def connect(self):
    """Establish BLE connection to Arduino."""
    print(f"Searching for device: {self.device_name}")
    
    scanner = BleakScanner()
    devices = await scanner.discover()
    
    target_device = None
    for device in devices:
        if device.name == self.device_name:
            target_device = device
            break
    
    if target_device is None:
        raise BLEConnectionError(f"Device '{self.device_name}' not found")
    
    print(f"Found device: {target_device.address}")
    
    try:
        self.client = BleakClient(target_device.address)
        await self.client.connect()
        print("Connected to device")
    except Exception as e:
        raise BLEConnectionError(f"Failed to connect: {e}")
```

- [ ] **Step 2: Implement disconnect()**

Replace the `disconnect` method:

```python
async def disconnect(self):
    """Close BLE connection."""
    if self.client and self.client.is_connected:
        await self.client.disconnect()
        print("Disconnected from device")
```

- [ ] **Step 3: Implement is_connected()**

Replace the `is_connected` method:

```python
def is_connected(self) -> bool:
    """Return True if BLE connection is active."""
    return self.client is not None and self.client.is_connected
```

- [ ] **Step 4: Verify it compiles**

```bash
cd backend
python3 -c "from pcg_ble_client import PCGClient; c = PCGClient(); print('OK')"
```

Expected: No import errors.

- [ ] **Step 5: Commit**

```bash
git add backend/pcg_ble_client.py
git commit -m "backend: implement BLE connection methods"
```

---

### Task 8: Implement Batch Notification Callback

**Files:**
- Modify: `backend/pcg_ble_client.py`

- [ ] **Step 1: Implement notification handler**

Replace the `_notification_handler` method:

```python
async def _notification_handler(self, sender, data: bytearray):
    """
    BLE notification callback: parse batch and queue it.
    Expects data to be uint16_t values (2 bytes per sample).
    """
    # Convert bytearray to uint16 samples
    num_samples = len(data) // 2
    samples = np.frombuffer(data, dtype=np.uint16)[:num_samples]
    
    self._accumulated_data.extend(samples.tolist())
    
    # Queue for generator
    await self._batch_queue.put(samples)
```

- [ ] **Step 2: Write unit test**

Add to `backend/tests/test_pcg_ble_client.py`:

```python
def test_notification_handler_parsing():
    """Test that notification handler correctly parses batches."""
    import asyncio
    import numpy as np
    
    async def test_coro():
        client = PCGClient()
        
        # Simulate 6-sample batch as bytearray
        samples = np.array([100, 200, 300, 400, 500, 600], dtype=np.uint16)
        data = samples.tobytes()
        
        await client._notification_handler(None, bytearray(data))
        
        assert len(client._accumulated_data) == 6
        assert client._accumulated_data == [100, 200, 300, 400, 500, 600]
    
    asyncio.run(test_coro())
```

- [ ] **Step 3: Run tests**

```bash
cd backend
python3 -m pytest tests/test_pcg_ble_client.py::test_notification_handler_parsing -v
```

Expected: Test passes.

- [ ] **Step 4: Commit**

```bash
git add backend/pcg_ble_client.py backend/tests/test_pcg_ble_client.py
git commit -m "backend: implement batch notification callback"
```

---

### Task 9: Implement analyze() Generator Method

**Files:**
- Modify: `backend/pcg_ble_client.py`

- [ ] **Step 1: Add time import at top of file**

At the very top of `backend/pcg_ble_client.py`, add:

```python
import time
```

- [ ] **Step 2: Implement analyze()**

Replace the `analyze` method:

```python
async def analyze(self, sample_rate: int, oversample_count: int, batch_size: int, 
                 patient_name: str, analysis_time_seconds: int):
    """
    Send analysis request to Arduino and yield batches as they arrive.
    
    Args:
        sample_rate: Samples per second (e.g., 500)
        oversample_count: ADC reads per sample (e.g., 8)
        batch_size: Samples per BLE packet (e.g., 6)
        patient_name: Patient identifier
        analysis_time_seconds: Duration to collect (e.g., 60)
    
    Yields:
        np.ndarray of samples (length batch_size), dtype uint16
    
    Raises:
        BLEConnectionError: If connection drops
    """
    if not self.is_connected():
        raise BLEConnectionError("Not connected to device")
    
    # Store for later validation
    self._sample_rate = sample_rate
    self._analysis_time_seconds = analysis_time_seconds
    
    # Reset accumulation
    self._accumulated_data = []
    self._batch_queue = asyncio.Queue()
    
    # Encode and send START packet
    packet = self._encode_start_packet(sample_rate, oversample_count, batch_size, 
                                       analysis_time_seconds, patient_name)
    
    try:
        await self.client.write_gatt_char(
            self.CHARACTERISTIC_UUID,
            packet,
            response=False
        )
        print(f"Sent START command for {analysis_time_seconds}s analysis")
    except Exception as e:
        raise BLEConnectionError(f"Failed to send command: {e}")
    
    # Register notification handler
    try:
        await self.client.start_notify(
            self.CHARACTERISTIC_UUID,
            self._notification_handler
        )
    except Exception as e:
        raise BLEConnectionError(f"Failed to start notifications: {e}")
    
    # Yield batches until analysis time expires
    expected_total_samples = sample_rate * analysis_time_seconds
    expected_num_batches = (expected_total_samples + batch_size - 1) // batch_size
    batches_yielded = 0
    
    try:
        timeout_seconds = analysis_time_seconds + 5  # Grace period
        while batches_yielded < expected_num_batches:
            try:
                batch = await asyncio.wait_for(
                    self._batch_queue.get(),
                    timeout=timeout_seconds
                )
                yield batch
                batches_yielded += 1
            except asyncio.TimeoutError:
                print("Warning: Timeout waiting for batches")
                break
    finally:
        # Stop notifications
        try:
            await self.client.stop_notify(self.CHARACTERISTIC_UUID)
        except:
            pass
```

- [ ] **Step 3: Verify it's syntactically correct**

```bash
cd backend
python3 -m py_compile pcg_ble_client.py
```

Expected: No syntax errors.

- [ ] **Step 4: Commit**

```bash
git add backend/pcg_ble_client.py
git commit -m "backend: implement analyze() generator method"
```

---

### Task 10: Implement get_full_signal() with Validation

**Files:**
- Modify: `backend/pcg_ble_client.py`

- [ ] **Step 1: Implement get_full_signal()**

Replace the `get_full_signal` method:

```python
def get_full_signal(self) -> np.ndarray:
    """
    Return all accumulated samples from analyze().
    Validates sample count and trims/waits as needed.
    
    Expected: sample_rate * analysis_time_seconds samples
    
    Returns:
        np.ndarray of shape (expected_samples,), dtype uint16
    
    Raises:
        BLEConnectionError: If validation times out
    """
    expected_samples = self._sample_rate * self._analysis_time_seconds
    
    print(f"Waiting for {expected_samples} samples...")
    
    # Wait up to 2 seconds for any lagging batches
    timeout = time.time() + 2.0
    while len(self._accumulated_data) < expected_samples and time.time() < timeout:
        time.sleep(0.01)
    
    actual_samples = len(self._accumulated_data)
    
    if actual_samples < expected_samples:
        print(f"Warning: Expected {expected_samples} samples, got {actual_samples}")
    
    if actual_samples > expected_samples:
        print(f"Trimming from {actual_samples} to {expected_samples} samples")
    
    # Return exactly expected_samples
    result = np.array(self._accumulated_data[:expected_samples], dtype=np.uint16)
    
    return result
```

- [ ] **Step 2: Write integration test**

Add to `backend/tests/test_pcg_ble_client.py`:

```python
def test_get_full_signal_validation():
    """Test that get_full_signal returns correct sample count."""
    client = PCGClient()
    
    # Simulate collected data
    client._sample_rate = 500
    client._analysis_time_seconds = 10
    client._accumulated_data = list(range(5000))  # Exactly 500*10 samples
    
    signal = client.get_full_signal()
    
    assert len(signal) == 5000
    assert signal.dtype == np.uint16
    assert signal[0] == 0
    assert signal[4999] == 4999

def test_get_full_signal_trims_excess():
    """Test trimming when more samples than expected."""
    client = PCGClient()
    
    client._sample_rate = 500
    client._analysis_time_seconds = 10
    client._accumulated_data = list(range(5100))  # 100 extra
    
    signal = client.get_full_signal()
    
    assert len(signal) == 5000  # Trimmed
```

- [ ] **Step 3: Run tests**

```bash
cd backend
python3 -m pytest tests/test_pcg_ble_client.py::test_get_full_signal_validation tests/test_pcg_ble_client.py::test_get_full_signal_trims_excess -v
```

Expected: Both tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/pcg_ble_client.py backend/tests/test_pcg_ble_client.py
git commit -m "backend: implement get_full_signal() with validation and tests"
```

---

### Task 11: Create Usage Example & Documentation

**Files:**
- Create: `backend/example_usage.py`
- Create: `backend/README.md`

- [ ] **Step 1: Write example usage script**

Create `backend/example_usage.py`:

```python
"""
Example: Collect phonocardiogram data using PCGClient.
"""
import asyncio
import numpy as np
from pcg_ble_client import PCGClient

async def main():
    # Initialize client
    client = PCGClient(device_name="PCG_Monitor_Raw")
    
    try:
        # Connect to Arduino
        print("Connecting to device...")
        await client.connect()
        
        if not client.is_connected():
            print("Failed to connect")
            return
        
        print("Connected! Starting analysis...")
        
        # Collect 60 seconds of data at 500 Hz
        batch_count = 0
        async for batch in client.analyze(
            sample_rate=500,
            oversample_count=8,
            batch_size=6,
            patient_name="Test Patient",
            analysis_time_seconds=60
        ):
            batch_count += 1
            print(f"Batch {batch_count}: {len(batch)} samples")
        
        print("Analysis complete. Getting full signal...")
        
        # Retrieve accumulated signal
        full_signal = client.get_full_signal()
        print(f"Received {len(full_signal)} total samples")
        
        # Pass to analyzer (placeholder)
        print(f"Signal shape: {full_signal.shape}, dtype: {full_signal.dtype}")
        print(f"Min: {full_signal.min()}, Max: {full_signal.max()}")
        
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Write README**

Create `backend/README.md`:

```markdown
# PCG BLE Client

Python client for collecting phonocardiogram (PCG) data from Arduino via BLE.

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

```python
import asyncio
from pcg_ble_client import PCGClient

async def main():
    client = PCGClient(device_name="PCG_Monitor_Raw")
    await client.connect()
    
    # Collect 60 seconds of data
    for batch in client.analyze(sample_rate=500, oversample_count=8, batch_size=6, 
                               patient_name="John", analysis_time_seconds=60):
        print(f"Got batch: {batch}")
    
    # Get full accumulated signal
    signal = client.get_full_signal()
    
    await client.disconnect()

asyncio.run(main())
```

## API

### `PCGClient`

#### `connect()`
Establish BLE connection to Arduino.

#### `disconnect()`
Close BLE connection.

#### `is_connected() -> bool`
Check connection status.

#### `analyze(...) -> Generator[np.ndarray]`
Send analysis request and yield batches.

Parameters:
- `sample_rate` (int): Hz
- `oversample_count` (int): ADC reads per sample
- `batch_size` (int): Samples per BLE packet
- `patient_name` (str): Patient ID (truncated to 15 chars)
- `analysis_time_seconds` (int): Collection duration

Yields: `np.ndarray` of samples (uint16)

#### `get_full_signal() -> np.ndarray`
Return all accumulated samples (validated).

## Running Tests

```bash
python3 -m pytest tests/ -v
```
```

- [ ] **Step 3: Commit**

```bash
git add backend/example_usage.py backend/README.md
git commit -m "backend: add usage example and documentation"
```

---

### Task 12: Arduino Integration Testing

**Files:**
- No new files

- [ ] **Step 1: Manual test - verify states with serial monitor**

1. Flash `ble_lcd_analyzer.ino` to ESP32
2. Open Serial Monitor (115200 baud)
3. Observe:
   - "BLE: Advertising started" appears
   - LCD shows battery % and blinking BT icon
4. Expected: Device advertises, ready for connection

- [ ] **Step 2: Manual test - connect and receive START**

Using a BLE app (e.g., nRF Connect):
1. Scan and connect to "PCG_Monitor_Raw"
2. Write to characteristic `abcd1234-ab12-cd34-ef56-123456789abc` a valid START packet
3. Observe serial output shows packet parsed correctly
4. LCD should show patient name and "Analyzing" with spinner
5. Expected: Batches sent to characteristic every ~12ms (at 500 Hz, 6 samples = 12ms)

- [ ] **Step 3: Verify sample count**

Write a Python script to connect and collect for 10 seconds:
```python
# Manual Python test (not automated)
# Collect 10 seconds at 500 Hz = 5000 samples expected
# Observe how many batches arrive (833 batches of 6, plus 1 batch of 2)
```

Expected: Approximately correct sample count.

- [ ] **Step 4: Verify timer stops collection**

After `analysis_time_seconds` expires, Arduino should stop sending batches and return to CONNECTED state.

Expected: Serial output shows "Analysis finished", LCD returns to "CONN" state.

---

### Task 13: Python Integration Testing (with real Arduino)

**Files:**
- Create: `backend/tests/test_integration.py`

- [ ] **Step 1: Write integration test (requires hardware)**

Create `backend/tests/test_integration.py`:

```python
"""
Integration tests with real Arduino.
Requires Arduino flashed with ble_lcd_analyzer.ino and powered on.
"""
import pytest
import asyncio
import numpy as np
from pcg_ble_client import PCGClient, BLEConnectionError

class TestPCGClientIntegration:
    
    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self):
        """Test basic connect/disconnect."""
        client = PCGClient(device_name="PCG_Monitor_Raw")
        
        try:
            await client.connect()
            assert client.is_connected()
            
            await client.disconnect()
            assert not client.is_connected()
        except BLEConnectionError as e:
            pytest.skip(f"Arduino not available: {e}")
    
    @pytest.mark.asyncio
    async def test_analyze_collect_data(self):
        """Test data collection for 5 seconds."""
        client = PCGClient(device_name="PCG_Monitor_Raw")
        
        try:
            await client.connect()
            
            batch_count = 0
            async for batch in client.analyze(
                sample_rate=500,
                oversample_count=8,
                batch_size=6,
                patient_name="TestUser",
                analysis_time_seconds=5
            ):
                batch_count += 1
                assert len(batch) == 6
                assert batch.dtype == np.uint16
            
            signal = client.get_full_signal()
            
            # Expect ~2500 samples (500 Hz * 5 sec)
            assert 2400 <= len(signal) <= 2600, f"Got {len(signal)} samples"
            
            await client.disconnect()
        except BLEConnectionError as e:
            pytest.skip(f"Arduino not available: {e}")
```

- [ ] **Step 2: Add pytest-asyncio to requirements**

Update `backend/requirements.txt`:

```
bleak==0.20.2
numpy>=1.21.0
pytest>=7.0.0
pytest-asyncio>=0.18.0
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_integration.py backend/requirements.txt
git commit -m "backend: add integration tests and pytest-asyncio dependency"
```
