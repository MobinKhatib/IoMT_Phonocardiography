// --- Configuration ---
const int ANALOG_PIN = A0;      // Connect to the 'Audio' output pin of SEN-14262
const int SAMPLE_RATE = 500;   // Hz (1000 Hz gives a 500 Hz Nyquist limit)
const int TIMER_INTERVAL_US = 1000000 / SAMPLE_RATE; 

hw_timer_t * timer = NULL;
volatile bool sampleReady = false;
volatile uint16_t adcValue = 0;

// Interrupt Service Routine (ISR) - Triggers exactly every 1 millisecond
void ARDUINO_ISR_ATTR onTimer() {
  // Read the analog value from the sensor
  adcValue = analogRead(ANALOG_PIN);
  sampleReady = true;
}

void setup() {
  Serial.begin(115200);
  
  // The ESP32 ADC is 12-bit by default (values from 0 to 4095)
  analogReadResolution(12); 

  // --- Timer Setup (ESP32 Core 3.x syntax) ---
  
  // 1. Initialize timer with 1 MHz frequency (1 microsecond resolution)
  timer = timerBegin(1000000); 
  
  // 2. Attach the ISR function to the timer
  timerAttachInterrupt(timer, &onTimer);
  
  // 3. Set alarm to trigger every TIMER_INTERVAL_US (1000 us), auto-reload = true
  timerAlarm(timer, TIMER_INTERVAL_US, true, 0); 
}

void loop() {
  // Keep the main loop lean to prevent serial bottlenecking
  // Only print when the ISR flags that a new sample has been taken
  if (sampleReady) {
    sampleReady = false;
    Serial.println(adcValue);
  }
}