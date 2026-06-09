#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <U8g2lib.h>
#include <Wire.h>
#include <math.h>

// --- Configuration ---
const int BATTERY_PIN = A0;      // Battery voltage monitor (through divider)
const int SIGNAL_PIN = A1;       // PCG sensor analog input
const int TIMER_ID = 0;
const int TIMER_PRESCALER = 80;
const int TIMER_COUNT_UP = true;

// BLE UUIDs (from existing code)
#define SERVICE_UUID        "12345678-1234-1234-1234-123456789abc"
#define CHARACTERISTIC_UUID "abcd1234-ab12-cd34-ef56-123456789abc"

// Display (Arduino Nano ESP32: SCL=A5(pin13), SDA=A4(pin12))
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
#define MAX_BATCH_SIZE 6

// One completed batch of samples, passed from the sampling task to loop().
struct SampleBatch {
  uint16_t samples[MAX_BATCH_SIZE];
  uint8_t count;
};

hw_timer_t* timer = NULL;
TaskHandle_t samplingTaskHandle = NULL;
QueueHandle_t batchQueue = NULL;       // sampling task -> loop(); buffers batches so none are dropped
volatile bool resetSampling = false;   // signals the task to start a fresh run

int sampleRate = 500;
int oversampleCount = 8;
int batchSize = 6;
int analysisTimeSeconds = 60;
char patientName[16] = {0};

// --- Analysis Timer ---
unsigned long analysisStartTime = 0;
bool isAnalyzing = false;
bool shouldStartTimer = false;  // Flag to defer timer setup to main loop

// --- Battery Helper ---
int batteryPercent() {
  uint32_t mv = 0;
  for (int i = 0; i < 16; i++) mv += analogReadMilliVolts(BATTERY_PIN);
  mv /= 16;
  float vbat = mv * 2.0 / 1000.0;   // x2 for divider, mV -> V
  int pct = (int)((vbat - 3.3) / (4.2 - 3.3) * 100.0);
  return constrain(pct, 0, 100);
}

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

// --- Sampling Timer ISR ---
// IMPORTANT: On ESP32 you must NOT call analogRead() (or most driver/RTOS
// functions) from a hardware-timer ISR. analogRead() is not IRAM-safe and
// acquires locks, which aborts -> the board reboots the instant the timer
// fires. So the ISR only notifies the sampling task; the task does the reads.
void ARDUINO_ISR_ATTR onTimer() {
  BaseType_t higherPriorityTaskWoken = pdFALSE;
  vTaskNotifyGiveFromISR(samplingTaskHandle, &higherPriorityTaskWoken);
  portYIELD_FROM_ISR(higherPriorityTaskWoken);
}

// --- Sampling Task ---
// Runs in normal (non-ISR) context, so analogRead() is safe here. Wakes up
// once per timer tick, takes an oversampled reading, and fills the batch.
void samplingTask(void* param) {
  static SampleBatch batch;
  static int idx = 0;

  for (;;) {
    // Block until the timer ISR signals that it's time to take a sample.
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

    // Start of a new analysis run: clear any partial batch and stale queue.
    if (resetSampling) {
      idx = 0;
      xQueueReset(batchQueue);
      resetSampling = false;
    }

    if (!isAnalyzing) {
      idx = 0;
      continue;
    }

    uint32_t sum = 0;
    for (int i = 0; i < oversampleCount; i++) {
      sum += analogRead(SIGNAL_PIN);  // Read from signal input, not battery
    }
    batch.samples[idx] = sum / oversampleCount;
    idx++;

    if (idx >= batchSize) {
      batch.count = batchSize;
      // Non-blocking hand-off to loop(). The queue buffers many batches so
      // samples are never dropped while loop() is busy with display or BLE.
      xQueueSend(batchQueue, &batch, 0);
      idx = 0;
    }
  }
}

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
  sampleRate = constrain(sampleRate, 100, 5000);  // Limit to safe range

  // Bytes 5-6: OVERSAMPLE_COUNT (little-endian)
  oversampleCount = (uint16_t)data[5] | ((uint16_t)data[6] << 8);
  oversampleCount = constrain(oversampleCount, 1, 32);  // Limit to safe range

  // Bytes 7-8: BATCH_SIZE (little-endian)
  batchSize = (uint16_t)data[7] | ((uint16_t)data[8] << 8);
  batchSize = constrain(batchSize, 1, MAX_BATCH_SIZE);  // Limit to batch buffer size

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
  resetSampling = true;
  currentState = STATE_ANALYZING;
  shouldStartTimer = true;  // Defer timer setup to main loop
  Serial.println("Analysis started");
}

// --- BLE Callbacks ---
class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer* pServer) {
    deviceConnected = true;
    currentState = STATE_CONNECTED;
    Serial.println("BLE: Client connected");
  }
  void onDisconnect(BLEServer* pServer) {
    Serial.println("BLE: Client disconnected (onDisconnect callback triggered)");
    deviceConnected = false;
    isAnalyzing = false;
    currentState = STATE_IDLE;
    BLEDevice::startAdvertising();
  }
};

class CharacteristicCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* pCharacteristic) {
    std::string value = pCharacteristic->getValue();
    if (value.length() > 0) {
      Serial.print("BLE: Received ");
      Serial.print(value.length());
      Serial.println(" bytes");
      parseStartCommand((uint8_t*)value.data(), value.length());
      Serial.println("BLE: Write processed successfully");
    }
  }
};

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

void setup() {
  Serial.begin(115200);
  delay(500);
  analogReadResolution(12);

  // Queue carries completed sample batches from the sampling task to loop().
  // Sized to buffer plenty of batches so none are lost during display/BLE work.
  batchQueue = xQueueCreate(64, sizeof(SampleBatch));

  // Create the sampling task before any timer can notify it. The timer ISR
  // notifies this task; the task performs the actual analogRead() sampling.
  xTaskCreatePinnedToCore(samplingTask, "sampling", 4096, NULL, 5, &samplingTaskHandle, 1);

  u8g2.begin();
  currentState = STATE_INITIALIZING;

  // Initialize BLE
  BLEDevice::init("PCG_Monitor_Raw");
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new ServerCallbacks());

  BLEService* pService = pServer->createService(SERVICE_UUID);
  pCharacteristic = pService->createCharacteristic(
    CHARACTERISTIC_UUID,
    BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_WRITE_NR | BLECharacteristic::PROPERTY_NOTIFY
  );
  pCharacteristic->setAccessPermissions(ESP_GATT_PERM_READ | ESP_GATT_PERM_WRITE);
  pCharacteristic->setCallbacks(new CharacteristicCallbacks());

  // Add CCCD descriptor for notifications
  BLE2902* pBLE2902 = new BLE2902();
  pBLE2902->setNotifications(true);
  pCharacteristic->addDescriptor(pBLE2902);

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

void loop() {
  // Set up timer if needed (deferred from BLE callback to avoid watchdog issues)
  if (shouldStartTimer) {
    shouldStartTimer = false;
    if (timer != NULL) {
      timerEnd(timer);
    }
    int timerIntervalUs = 1000000 / sampleRate;
    timer = timerBegin(TIMER_ID, TIMER_PRESCALER, TIMER_COUNT_UP);
    timerAttachInterrupt(timer, &onTimer, true);
    timerAlarmWrite(timer, timerIntervalUs, true);
    timerAlarmEnable(timer);
    Serial.println("Timer started in main loop");
  }

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

  // Drain ALL completed batches from the sampling task and send them over BLE.
  // Emptying the queue every loop keeps up even when a single iteration is slow
  // (e.g. an OLED redraw), so no samples back up or get dropped.
  SampleBatch outBatch;
  while (xQueueReceive(batchQueue, &outBatch, 0) == pdTRUE) {
    if (deviceConnected) {
      pCharacteristic->setValue((uint8_t*)outBatch.samples, outBatch.count * sizeof(uint16_t));
      pCharacteristic->notify();
    }
  }

  // Throttle the OLED: its slow I2C writes would otherwise bottleneck BLE.
  static unsigned long lastDisplay = 0;
  if (millis() - lastDisplay >= 150) {
    lastDisplay = millis();
    updateDisplay();
  }

  delay(2);
}
