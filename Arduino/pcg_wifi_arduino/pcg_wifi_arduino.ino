#include <WiFi.h>
#include <WiFiUdp.h>

// --- Wi-Fi Configuration ---
const char* WIFI_SSID     = "Aman";
const char* WIFI_PASSWORD = "09199571728";

// --- UDP Configuration ---
const uint16_t UDP_PORT = 4210;
WiFiUDP udp;
IPAddress broadcastIP;

// --- Sampling Configuration ---
const int ANALOG_PIN = A0;
const int SAMPLE_RATE = 500;
const int TIMER_INTERVAL_US = 1000000 / SAMPLE_RATE;
const int OVERSAMPLE_COUNT = 8;

// --- Batching ---
const int BATCH_SIZE = 20;
volatile uint16_t sampleBuffer[BATCH_SIZE];
volatile int sampleIndex = 0;
volatile bool batchReady = false;

// Packet: [magic:2][seq:4][count:2][samples:BATCH_SIZE*2]
// Total = 8 + 40 = 48 bytes
const uint16_t MAGIC = 0x5043;  // packet marker
uint8_t sendBuffer[8 + BATCH_SIZE * 2];
uint32_t sequence = 0;

hw_timer_t* timer = NULL;

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

void connectWiFi() {
  Serial.printf("Connecting to Wi-Fi: %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.printf("Connected. IP: %s\n", WiFi.localIP().toString().c_str());

  // Compute subnet broadcast address (e.g., 192.168.1.255)
  IPAddress ip      = WiFi.localIP();
  IPAddress subnet  = WiFi.subnetMask();
  for (int i = 0; i < 4; i++) {
    broadcastIP[i] = (ip[i] & subnet[i]) | (~subnet[i]);
  }
  Serial.printf("Broadcast IP: %s\n", broadcastIP.toString().c_str());
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);

  connectWiFi();
  udp.begin(UDP_PORT);

  // --- Timer Setup ---
  // --- Timer Setup (ESP32 core 2.x API) ---
  timer = timerBegin(0, 80, true);              // timer 0, prescaler 80 → 1 MHz tick
  timerAttachInterrupt(timer, &onTimer, true);  // edge-triggered
  timerAlarmWrite(timer, TIMER_INTERVAL_US, true);  // fire every TIMER_INTERVAL_US microseconds, auto-reload
  timerAlarmEnable(timer);

  Serial.printf("Streaming on UDP port %u (broadcast)\n", UDP_PORT);
}

void loop() {
  // Reconnect if Wi-Fi drops
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi lost, reconnecting...");
    connectWiFi();
  }

  if (batchReady) {
    // Build packet
    sendBuffer[0] = MAGIC & 0xFF;
    sendBuffer[1] = (MAGIC >> 8) & 0xFF;
    sendBuffer[2] = sequence & 0xFF;
    sendBuffer[3] = (sequence >> 8) & 0xFF;
    sendBuffer[4] = (sequence >> 16) & 0xFF;
    sendBuffer[5] = (sequence >> 24) & 0xFF;
    sendBuffer[6] = BATCH_SIZE & 0xFF;
    sendBuffer[7] = (BATCH_SIZE >> 8) & 0xFF;

    memcpy(sendBuffer + 8, (const void*)sampleBuffer, BATCH_SIZE * sizeof(uint16_t));
    batchReady = false;

    udp.beginPacket(broadcastIP, UDP_PORT);
    udp.write(sendBuffer, sizeof(sendBuffer));
    udp.endPacket();

    sequence++;
  }
}