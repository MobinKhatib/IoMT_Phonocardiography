#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <math.h>

// --- Configuration ---
const int ANALOG_PIN = A0;
const int SAMPLE_RATE = 500;
const int TIMER_INTERVAL_US = 1000000 / SAMPLE_RATE;

const int OVERSAMPLE_COUNT = 8;
const int BATCH_SIZE = 6;

// BLE UUIDs
#define SERVICE_UUID        "12345678-1234-1234-1234-123456789abc"
#define CHARACTERISTIC_UUID "abcd1234-ab12-cd34-ef56-123456789abc"

volatile uint16_t sampleBuffer[BATCH_SIZE];
volatile int sampleIndex = 0;
volatile bool batchReady = false;

// 12-byte payload to match your original UI
uint16_t sendBuffer[BATCH_SIZE]; 

BLEServer* pServer = NULL;
BLECharacteristic* pCharacteristic = NULL;
bool deviceConnected = false;
hw_timer_t* timer = NULL;

// --- Standard Biquad Filter Class ---
class Biquad {
  private:
    float b0, b1, b2, a1, a2;
    float z1, z2;
  public:
    Biquad(float _b0, float _b1, float _b2, float _a1, float _a2) {
      b0 = _b0; b1 = _b1; b2 = _b2; a1 = _a1; a2 = _a2;
      z1 = 0; z2 = 0;
    }
    float process(float in) {
      float out = in * b0 + z1;
      z1 = in * b1 + z2 - a1 * out;
      z2 = in * b2 - a2 * out;
      return out;
    }
};

// --- PRE-CALCULATED FILTER COEFFICIENTS (Sample Rate: 500Hz) ---

// 1. Highpass at 25Hz (Removes baseline wander & DC offset)
Biquad highpass25Hz(0.8006, -1.6011, 0.8006, -1.5609, 0.6414);

// 2. Lowpass at 200Hz (Together with the Highpass, this creates your 25-200Hz Bandpass)
Biquad lowpass200Hz(0.6389, 1.2778, 0.6389, 1.1429, 0.4127);

// 3. Notch at 50Hz (Removes powerline hum)
Biquad notch50Hz(0.991673, -1.604556, 0.991673, -1.604556, 0.983346);

// 4. Lowpass at 8Hz (Smooths out the Shannon Envelope peaks)
Biquad smoother8Hz(0.00238, 0.00476, 0.00238, -1.8581, 0.8676);


// --- BLE Callbacks ---
class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer* pServer) {
    deviceConnected = true;
    Serial.println("Client connected");
  }
  void onDisconnect(BLEServer* pServer) {
    deviceConnected = false;
    Serial.println("Client disconnected");
    BLEDevice::startAdvertising();
  }
};

// --- Interrupt Service Routine ---
void ARDUINO_ISR_ATTR onTimer() {
  if (batchReady) return;

  uint32_t sum = 0;
  for (int i = 0; i < OVERSAMPLE_COUNT; i++) {
    sum += analogRead(ANALOG_PIN);
  }
  sampleBuffer[sampleIndex] = sum / OVERSAMPLE_COUNT;
  sampleIndex++;

  if (sampleIndex >= BATCH_SIZE) {
    sampleIndex = 0;
    batchReady = true;
  }
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);

  BLEDevice::init("PCG_Monitor");
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new ServerCallbacks());

  BLEService* pService = pServer->createService(SERVICE_UUID);
  pCharacteristic = pService->createCharacteristic(
    CHARACTERISTIC_UUID,
    BLECharacteristic::PROPERTY_NOTIFY
  );
  pCharacteristic->addDescriptor(new BLE2902());
  pService->start();

  BLEAdvertising* pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(true);
  pAdvertising->setMinPreferred(0x06);
  BLEDevice::startAdvertising();

  Serial.println("BLE advertising started...");

  timer = timerBegin(0, 80, true);              
  timerAttachInterrupt(timer, &onTimer, true);  
  timerAlarmWrite(timer, TIMER_INTERVAL_US, true);
  timerAlarmEnable(timer);
}

void loop() {
  if (batchReady && deviceConnected) {
    
    uint16_t localRaw[BATCH_SIZE];
    noInterrupts();
    memcpy(localRaw, (const void*)sampleBuffer, BATCH_SIZE * sizeof(uint16_t));
    batchReady = false;
    interrupts();

    // --- THE FULL DSP PIPELINE ---
    for (int i = 0; i < BATCH_SIZE; i++) {
      float val = (float)localRaw[i];

      // 1. Bandpass Filter (25Hz - 200Hz)
      val = highpass25Hz.process(val);  // Cuts everything below 25Hz (and acts as DC blocker)
      val = lowpass200Hz.process(val);  // Cuts everything above 200Hz
      
      // 2. 50Hz Notch Filter
      val = notch50Hz.process(val);

      // 3. Shannon Energy Envelope
      float norm = val / 2048.0; 
      float sq = norm * norm;
      float shannon = -sq * log(sq + 1e-10); 
      if (shannon < 0) shannon = 0;

      // 4. Smooth the Envelope (8Hz Lowpass)
      float smoothed_shannon = smoother8Hz.process(shannon);
      if (smoothed_shannon < 0) smoothed_shannon = 0;
      
      // Load into BLE buffer
      sendBuffer[i] = (uint16_t)(smoothed_shannon * 10000.0); 
    }

    // Send exactly 12 bytes
    pCharacteristic->setValue((uint8_t*)sendBuffer, BATCH_SIZE * sizeof(uint16_t));
    pCharacteristic->notify();
    
  } else if (batchReady && !deviceConnected) {
    batchReady = false;
  }
}