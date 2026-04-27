const int ANALOG_PIN        = A0;
const int SAMPLE_RATE       = 500;
const int TIMER_INTERVAL_US = 1000000 / SAMPLE_RATE;
const int OVERSAMPLE_COUNT  = 8;

hw_timer_t*      timer       = NULL;
volatile bool    sampleReady = false;
volatile uint16_t adcValue   = 0;

// ISR — identical workload to the real sketches
void ARDUINO_ISR_ATTR onTimer() {
  uint32_t sum = 0;
  for (int i = 0; i < OVERSAMPLE_COUNT; i++) {
    sum += analogRead(ANALOG_PIN);
  }
  adcValue    = (uint16_t)(sum / OVERSAMPLE_COUNT);
  sampleReady = true;
}

void setup() {
  // Brief serial only for confirming boot — stays silent after
  Serial.begin(115200);
  Serial.println("Baseline running — radios off, ADC sampling active.");
  Serial.flush();

  // Explicitly disable WiFi and Bluetooth radios
  WiFi.mode(WIFI_OFF);
  btStop();

  analogReadResolution(12);

  timer = timerBegin(1000000);
  timerAttachInterrupt(timer, &onTimer);
  timerAlarm(timer, TIMER_INTERVAL_US, true, 0);
}

void loop() {
  // Consume the flag so the ISR doesn't stall — but do NOT transmit anything
  if (sampleReady) {
    sampleReady = false;
    // adcValue is read and discarded — same CPU work, zero TX cost
    (void)adcValue;
  }
}
