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

// --- Battery Helper ---
int batteryPercent() {
  uint32_t mv = 0;
  for (int i = 0; i < 16; i++) mv += analogReadMilliVolts(A0);
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
  analogReadResolution(12);
  u8g2.begin();

  // BLE setup will follow in later tasks

  currentState = STATE_IDLE;  // Move out of INITIALIZING after setup
}

void loop() {
  updateDisplay();
  delay(30);  // ~30 fps
}
