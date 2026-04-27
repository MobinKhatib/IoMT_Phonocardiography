const int ANALOG_PIN        = A0;
const int SAMPLE_RATE       = 500;                        // Hz
const int TIMER_INTERVAL_US = 1000000 / SAMPLE_RATE;     // 2000 µs
const int OVERSAMPLE_COUNT  = 8;                          // average 8 reads per sample

hw_timer_t* timer       = NULL;
volatile bool sampleReady = false;
volatile uint16_t adcValue = 0;

// ISR — fires every 2 ms (500 Hz)
void ARDUINO_ISR_ATTR onTimer() {
  uint32_t sum = 0;
  for (int i = 0; i < OVERSAMPLE_COUNT; i++) {
    sum += analogRead(ANALOG_PIN);
  }
  adcValue    = (uint16_t)(sum / OVERSAMPLE_COUNT);
  sampleReady = true;
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);   // 12-bit ADC (0–4095)

  // Timer: 1 MHz base clock → 1 µs resolution
  timer = timerBegin(1000000);
  timerAttachInterrupt(timer, &onTimer);
  timerAlarm(timer, TIMER_INTERVAL_US, true, 0);   // auto-reload
}

void loop() {
  if (sampleReady) {
    sampleReady = false;
    Serial.println(adcValue);
  }
}
