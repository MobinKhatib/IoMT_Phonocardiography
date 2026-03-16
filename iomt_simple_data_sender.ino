#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h> // Required for enabling Notifications

// --- Hardware Pins ---
const int audioPin = A0; // Connected to the 'Audio' pin on SEN-14262

// --- Timing Configuration ---
const uint32_t samplingFreq = 250; // 250 Hz sampling rate
hw_timer_t * timer = NULL;
volatile bool readDataFlag = false;

// --- BLE Configuration ---
BLEServer* pServer = NULL;
BLECharacteristic* pCharacteristic = NULL;
bool deviceConnected = false;
bool oldDeviceConnected = false;

// UUIDs must match the Python code exactly!
#define SERVICE_UUID        "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define CHARACTERISTIC_UUID "12345678-1234-5678-1234-56789abcdef1" 

// Callback class to track when your PC connects and disconnects
class MyServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
      deviceConnected = true;
    };

    void onDisconnect(BLEServer* pServer) {
      deviceConnected = false;
    }
};

// Interrupt Service Routine (ISR)
void IRAM_ATTR onTimer() { 
  readDataFlag = true; 
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);

  // --- 1. BLE Setup ---
  Serial.println("Starting BLE work!");
  
  // The device name MUST match BLE_DEVICE_NAME in your Python script
  BLEDevice::init("Nano_ESP32_Heart"); 

  // Create the BLE Server and attach the connection callbacks
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());

  // Create the BLE Service
  BLEService *pService = pServer->createService(SERVICE_UUID);

  // Create a BLE Characteristic with Notify and Read properties
  pCharacteristic = pService->createCharacteristic(
                      CHARACTERISTIC_UUID,
                      BLECharacteristic::PROPERTY_READ   |
                      BLECharacteristic::PROPERTY_NOTIFY
                    );

  // Add a descriptor (Standard requirement for notifications to work)
  pCharacteristic->addDescriptor(new BLE2902());

  // Start the service
  pService->start();

  // Start advertising so the PC can find it
  BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(false);
  pAdvertising->setMinPreferred(0x0);  // Prevent issues with iPhone connections
  BLEDevice::startAdvertising();
  Serial.println("Waiting for a client connection to notify...");

  // --- 2. Hardware Timer Setup ---
  timer = timerBegin(0, 80, true);
  timerAttachInterrupt(timer, &onTimer, true);
  timerAlarmWrite(timer, 1000000 / samplingFreq, true); 
  timerAlarmEnable(timer);
}

void loop() {
  // Wait for the precise interrupt flag
  if (readDataFlag) {
    readDataFlag = false; // Reset the gate
    
    // Read the real hardware sensor
    int rawSignal = analogRead(audioPin);
    
    // Only send data if the Python script is actively connected
    if (deviceConnected) {
      // Convert the integer into a string (like Serial.println does automatically)
      String dataString = String(rawSignal);
      
      // Set the value and push the notification out via BLE
      pCharacteristic->setValue(dataString.c_str());
      pCharacteristic->notify();
    }
  }

  // --- Handle Bluetooth Disconnections ---
  if (!deviceConnected && oldDeviceConnected) {
      delay(500); // Give the Bluetooth stack a moment to get things ready
      pServer->startAdvertising(); // Restart advertising
      Serial.println("Client disconnected. Restarting advertising...");
      oldDeviceConnected = deviceConnected;
  }
  
  // --- Handle Bluetooth Connections ---
  if (deviceConnected && !oldDeviceConnected) {
      Serial.println("Client connected!");
      oldDeviceConnected = deviceConnected;
  }
}
