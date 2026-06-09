#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

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

// 12-byte payload for raw signal
uint16_t sendBuffer[BATCH_SIZE];

BLEServer* pServer = NULL;
BLECharacteristic* pCharacteristic = NULL;
bool deviceConnected = false;
hw_timer_t* timer = NULL;

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

  BLEDevice::init("PCG_Monitor_Raw");
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

  Serial.println("BLE advertising started (Raw Signal Mode)...");

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

    // Send raw signal directly without any filtering
    for (int i = 0; i < BATCH_SIZE; i++) {
      sendBuffer[i] = localRaw[i];
    }

    // Send exactly 12 bytes
    pCharacteristic->setValue((uint8_t*)sendBuffer, BATCH_SIZE * sizeof(uint16_t));
    pCharacteristic->notify();

  } else if (batchReady && !deviceConnected) {
    batchReady = false;
  }
}
