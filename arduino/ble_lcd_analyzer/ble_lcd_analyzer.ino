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
