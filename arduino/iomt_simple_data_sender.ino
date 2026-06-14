#include <Arduino.h>

// --- Hardware Pins ---
const int audioPin = A0; // Connected to the 'Audio' pin on SEN-14262

// --- Timing Configuration ---
const uint32_t samplingFreq = 250; // 250 Hz sampling rate
hw_timer_t * timer = NULL;
volatile bool readDataFlag = false;

// Interrupt Service Routine (ISR)
void IRAM_ATTR onTimer() { 
  readDataFlag = true; 
}

void setup() {
  // High baud rate to prevent serial bottlenecking
  Serial.begin(115200);

  // ESP32 ADC resolution is 12-bit (values from 0 to 4095)
  analogReadResolution(12);

  // --- Hardware Timer Setup (ESP32 Core 2.x) ---
  // timerBegin(timer_id, divider, countUp)
  // 80MHz base clock / 80 = 1MHz (1 microsecond per tick)
  timer = timerBegin(0, 80, true);
  
  // timerAttachInterrupt(timer, function, edge)
  timerAttachInterrupt(timer, &onTimer, true);
  
  // timerAlarmWrite(timer, alarm_value_in_microseconds, autoreload)
  timerAlarmWrite(timer, 1000000 / samplingFreq, true); 
  
  // Enable the timer
  timerAlarmEnable(timer);
}

void loop() {
  // Wait for the precise interrupt flag
  if (readDataFlag) {
    readDataFlag = false; // Reset the gate
    
    // 1. Read the real hardware sensor
    int rawSignal = analogRead(audioPin);
    
    // 2. Send the data to the Python GUI
    Serial.println(rawSignal);
  }
}