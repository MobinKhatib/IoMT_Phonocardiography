#include <WiFi.h>
#include <WiFiUdp.h>

// --- Wi-Fi Configuration ---
const char* WIFI_SSID     = "Aman";
const char* WIFI_PASSWORD = "09199571728";

// --- TCP Configuration ---
const uint16_t TCP_PORT = 4210;
WiFiServer server(TCP_PORT);
WiFiClient client;

// --- Discovery (UDP broadcast beacon) ---
const uint16_t DISCOVERY_PORT = 4211;
WiFiUDP discoveryUdp;
IPAddress broadcastIP;
unsigned long lastBeaconMs = 0;
const unsigned long BEACON_INTERVAL_MS = 1000;

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

// Packet: [magic:2][seq:4][count:2][samples:BATCH_SIZE*2] = 48 bytes
const uint16_t MAGIC = 0x5043;  // 'PC'
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
  IPAddress ip   = WiFi.localIP();
  IPAddress mask = WiFi.subnetMask();
  for (int i = 0; i < 4; i++) {
    broadcastIP[i] = (ip[i] & mask[i]) | (~mask[i]);
  }
  Serial.printf("Broadcast IP: %s\n", broadcastIP.toString().c_str());
}

void sendBeacon() {
  // Beacon payload: "PCG_Monitor:<TCP_PORT>"
  char payload[32];
  int len = snprintf(payload, sizeof(payload), "PCG_Monitor:%u", TCP_PORT);

  discoveryUdp.beginPacket(broadcastIP, DISCOVERY_PORT);
  discoveryUdp.write((const uint8_t*)payload, len);
  discoveryUdp.endPacket();
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);

  connectWiFi();

  server.begin();
  server.setNoDelay(true);
  Serial.printf("TCP server listening on port %u\n", TCP_PORT);

  discoveryUdp.begin(DISCOVERY_PORT);
  Serial.printf("Discovery beacon on UDP port %u\n", DISCOVERY_PORT);

  // --- Timer Setup (ESP32 core 2.x API) ---
  timer = timerBegin(0, 80, true);
  timerAttachInterrupt(timer, &onTimer, true);
  timerAlarmWrite(timer, TIMER_INTERVAL_US, true);
  timerAlarmEnable(timer);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi lost, reconnecting...");
    connectWiFi();
    server.begin();
  }

  // Send beacon every second so Python can find us
  unsigned long now = millis();
  if (now - lastBeaconMs >= BEACON_INTERVAL_MS) {
    lastBeaconMs = now;
    sendBeacon();
  }

  // Accept new TCP client if none connected
  if (!client || !client.connected()) {
    if (client) client.stop();
    client = server.available();
    if (client) {
      client.setNoDelay(true);
      sequence = 0;
      Serial.printf("Client connected: %s\n", client.remoteIP().toString().c_str());
    }
  }

  // Send a batch if ready and someone is listening
  if (batchReady) {
    if (client && client.connected()) {
      sendBuffer[0] = MAGIC & 0xFF;
      sendBuffer[1] = (MAGIC >> 8) & 0xFF;
      sendBuffer[2] = sequence & 0xFF;
      sendBuffer[3] = (sequence >> 8) & 0xFF;
      sendBuffer[4] = (sequence >> 16) & 0xFF;
      sendBuffer[5] = (sequence >> 24) & 0xFF;
      sendBuffer[6] = BATCH_SIZE & 0xFF;
      sendBuffer[7] = (BATCH_SIZE >> 8) & 0xFF;

      memcpy(sendBuffer + 8, (const void*)sampleBuffer, BATCH_SIZE * sizeof(uint16_t));

      size_t written = client.write(sendBuffer, sizeof(sendBuffer));
      if (written != sizeof(sendBuffer)) {
        Serial.println("Short write — client disconnected");
        client.stop();
      }
      sequence++;
    }
    batchReady = false;
  }
}