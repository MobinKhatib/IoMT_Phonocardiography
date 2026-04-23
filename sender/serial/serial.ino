// --- Configuration ---
const int ANALOG_PIN = A0;
const int SAMPLE_RATE = 3000;                  // change per test: 500, 1000, 2000, 4000, 8000
const int TIMER_INTERVAL_US = 1000000 / SAMPLE_RATE;

const int OVERSAMPLE_COUNT = 8;
const int BATCH_SIZE = 6;

const uint8_t HEADER_1 = 0xAA;
const uint8_t HEADER_2 = 0x55;

// Use same baud for all Serial benchmark tests

// IMPORTANT:
// false = normal benchmark mode, binary packets only
// true  = debug text mode, do NOT run Python receiver at the same time
const bool DEBUG_TEXT_MODE = true;

// --- Sampling / batching ---
hw_timer_t* timer = NULL;

volatile uint16_t sampleBuffer[BATCH_SIZE];
volatile int sampleIndex = 0;
volatile bool batchReady = false;

uint16_t sendBuffer[BATCH_SIZE];
uint32_t seq = 0;

volatile uint32_t droppedBatches = 0;
volatile uint32_t generatedBatches = 0;

unsigned long lastStatsPrint = 0;

// Packet format:
// header1, header2, seq, count, samples[]
struct __attribute__((packed)) Packet {
  uint8_t header1;
  uint8_t header2;
  uint32_t seq;
  uint16_t count;
  uint16_t samples[BATCH_SIZE];
};

void ARDUINO_ISR_ATTR onTimer() {
  // If previous batch is still waiting to be sent, count a drop
  if (batchReady) {
    droppedBatches++;
    return;
  }

  uint32_t sum = 0;
  for (int i = 0; i < OVERSAMPLE_COUNT; i++) {
    sum += analogRead(ANALOG_PIN);
  }

  sampleBuffer[sampleIndex] = sum / OVERSAMPLE_COUNT;
  sampleIndex++;

  if (sampleIndex >= BATCH_SIZE) {
    sampleIndex = 0;
    batchReady = true;
    generatedBatches++;
  }
}

void setup() {
  Serial.begin(460800);
  analogReadResolution(12);

  // ESP32 Arduino core 2.x timer API
  timer = timerBegin(0, 80, true);
  timerAttachInterrupt(timer, &onTimer, true);
  timerAlarmWrite(timer, TIMER_INTERVAL_US, true);
  timerAlarmEnable(timer);
}

void loop() {
  if (batchReady) {
    noInterrupts();
    memcpy(sendBuffer, (const void*)sampleBuffer, BATCH_SIZE * sizeof(uint16_t));
    batchReady = false;
    interrupts();

    Packet pkt;
    pkt.header1 = HEADER_1;
    pkt.header2 = HEADER_2;
    pkt.seq = seq++;
    pkt.count = BATCH_SIZE;
    memcpy(pkt.samples, sendBuffer, BATCH_SIZE * sizeof(uint16_t));

    // In benchmark mode: send binary packets
    if (!DEBUG_TEXT_MODE) {
      Serial.write((uint8_t*)&pkt, sizeof(pkt));
    }
  }

  // In debug mode: print only text stats
  if (DEBUG_TEXT_MODE && millis() - lastStatsPrint >= 2000) {
    lastStatsPrint = millis();

    noInterrupts();
    uint32_t gen = generatedBatches;
    uint32_t drop = droppedBatches;
    uint32_t s = seq;
    interrupts();

    Serial.print("SER stats | seq=");
    Serial.print(s);
    Serial.print(" generated=");
    Serial.print(gen);
    Serial.print(" dropped=");
    Serial.println(drop);
  }
}