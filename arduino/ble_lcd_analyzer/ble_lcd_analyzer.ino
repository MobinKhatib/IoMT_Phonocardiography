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
U8G2_SSD1306_48X64_WINSTAR_F_SW_I2C u8g2(U8G2_R0, 13, 12, U8X8_PIN_NONE);

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
void ARDUINO_ISR_ATTR onTimer() {
  if (!isAnalyzing || batchReady) return;

  uint32_t sum = 0;
  for (int i = 0; i < oversampleCount; i++) {
    sum += analogRead(SIGNAL_PIN);  // Read from signal input, not battery
  }
  sampleBuffer[sampleIndex] = sum / oversampleCount;
  sampleIndex++;

  if (sampleIndex >= batchSize) {
    sampleIndex = 0;
    batchReady = true;
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
  pCharacteristic->setCallbacks(new CharacteristicCallbacks());
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
