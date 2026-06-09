# Arduino Nano ESP32 Wiring Guide

## Components Required

| Component | Part Number | Notes |
|-----------|-------------|-------|
| Arduino Nano ESP32 | AE-ESP32-S3 | Main microcontroller |
| OLED Display | SSD1306 48x64 | I2C interface |
| LiPo Battery | 3.7V or 4.2V | Recommended 2000-5000 mAh |
| Phonocardiography Sensor | Analog output | ADC input |
| Resistor 2.2kΩ | (2x) | Battery voltage divider |
| Resistor 2.2kΩ | 1x | Optional pull-up for sensor |
| Capacitor 100µF | 1x | Power supply decoupling |
| Capacitor 10µF | 1x | Filter for battery line |

---

## Pinout Reference (ESP32-S3 Nano)

```
┌─────────────────────────────────────────┐
│         ESP32-S3 NANO (Top View)        │
├─────────────────────────────────────────┤
│  D1/TX    GND    5V    3V3    A0        │
│   TX     GND    VBUS   3V3   A0/ADC    │
│                                         │
│  D0/RX    GND    GND    D2    D3        │
│   RX     GND    GND    GPIO2 GPIO3     │
│                                         │
│  D8       D9    D10    D11   D12        │
│ GPIO8   GPIO9  GPIO10 GPIO11 GPIO12    │
│                                         │
│  D13      GND   GND    D15   D16        │
│ GPIO13   GND    GND  GPIO15 GPIO16     │
│                                         │
│  D4/SCL  D5/SDA  D6    D7    D18       │
│ GPIO4    GPIO5  GPIO6  GPIO7 GPIO18    │
│ (SCL)    (SDA)                         │
│                                         │
│  D19      GND   GND    D21   3V3        │
│ GPIO19   GND    GND  GPIO21  3V3       │
└─────────────────────────────────────────┘
```

---

## Connection Diagram

### 1. OLED Display (SSD1306 I2C)

**Display Pins:**
- **GND** → ESP32 GND
- **VCC** → ESP32 3V3
- **SCL** → ESP32 D4 (GPIO4)
- **SDA** → ESP32 D5 (GPIO5)

```
     ┌──────────────────────┐
     │   SSD1306 OLED       │
     │   (48x64 pixels)     │
     └──────────────────────┘
            │    │    │    │
           GND  VCC  SCL  SDA
            │    │    │    │
            ↓    ↓    ↓    ↓
        GND  3V3 D4   D5
        ESP32 NANO
```

**Schematic Detail:**
```
3V3 ──────┬────────→ VCC (OLED)
          │
        100µF cap (ground other end)
          │
        GND

D4 (GPIO4/SCL) ──→ SCL (OLED)
D5 (GPIO5/SDA) ──→ SDA (OLED)

GND ──→ GND (OLED)
```

---

### 2. Battery Voltage Monitor (A0)

**Setup: 2:1 Voltage Divider**
- Monitor battery voltage to calculate percentage
- Supports 3.3V to 4.2V (LiPo) range

```
        LiPo Battery (4.2V max)
        │
        │ (+ terminal)
        ├──────────────────→ (to system power, ~5V input)
        │
        ├──[2.2kΩ resistor]──┬──→ A0 (ADC, ESP32)
        │                     │
        └────────────────────┴──[2.2kΩ resistor]──→ GND
                             │
                         (midpoint = A0 input)
```

**Voltage Divider Math:**
- Vin = 4.2V (fully charged LiPo)
- Vout = Vin × (R2 / (R1 + R2)) = 4.2 × (2.2k / 4.4k) = 2.1V
- ADC reads 2.1V at 12-bit resolution: (2.1 / 3.3) × 4095 ≈ 2613 counts
- Code maps: 3.3V (0%) → 4.2V (100%)

**Capacity Calculation (in code):**
```cpp
uint32_t mv = analogReadMilliVolts(A0);
mv /= 16;  // Average 16 samples
float vbat = mv * 2.0 / 1000.0;  // × 2 for divider
int pct = (int)((vbat - 3.3) / (4.2 - 3.3) * 100.0);
```

---

### 3. Phonocardiography Sensor Input

**Analog Signal Path:**
- **Sensor Output** → 10µF capacitor → **A0** (alternate: GPIO A1)
- Optional: 2.2kΩ pull-up to 3V3 for impedance matching

```
    Phonocardiography Sensor
    (analog output, 0-3.3V)
           │
           ├──[10µF cap]──┬──→ A0 (ADC)
           │              │
           └──────────────┴──→ GND
                          
    (Optional pull-up for high-impedance sensors)
           ├──[2.2kΩ]──→ 3V3
```

**ADC Configuration (in code):**
```cpp
analogReadResolution(12);  // 12-bit resolution (0-4095 counts)
```

---

## Full System Schematic

```
                    ┌─────────────────┐
                    │  LiPo Battery   │
                    │   3.7-4.2V      │
                    │ (2000-5000 mAh) │
                    └────────┬────────┘
                             │
                    ┌────────┴────────┐
                    │                 │
              [Voltage Divider]  [Power]
              (2.2k/2.2k)        │
                    │            │
                    ↓            ↓
                  A0 (ADC)    +5V (VBAT)
                    │            │
            ┌───────┴─────┬──────┴────────┐
            │             │               │
        ┌─────────────────┐         ┌─────┴──────┐
        │  ESP32-S3 NANO  │         │   100µF    │
        │                 │         │   Capacitor│
        │  D4──────────┬──┼─────────┤ (decouple) │
        │  D5──────────┤  │         └─────┬──────┘
        │              │  │               │
        │  A0──────────┤  │            GND
        │              │  │
        │  3V3─────────┤  │
        │  GND─────────┤  │
        │              │  │
        └──────┬───────┘  │
               │          │
        ┌──────┴──────┐   │
        │ SSD1306     │   │
        │ OLED        │   │
        │ SCL ←───────┘   │
        │ SDA ←────────┐  │
        │ VCC ←────────┼──┴─→ 3V3
        │ GND ←────────┴─────→ GND
        │ (48x64 display)  │
        └─────────────────┘
        
        ┌──────────────────┐
        │  PCG Sensor      │
        │  (Analog Out)    │
        │  OUT ←──[10µF]───→ A0
        │  GND ←────────────→ GND
        └──────────────────┘
```

---

## Assembly Steps

1. **Battery Connection**
   - Connect LiPo + terminal → voltage divider upper end
   - Connect voltage divider center → A0
   - Connect voltage divider lower end → GND
   - Connect LiPo ground → ESP32 GND

2. **Power Decoupling**
   - 100µF capacitor across +5V and GND (near ESP32)
   - 10µF capacitor on sensor output (near A0)

3. **I2C Display (SSD1306)**
   - Connect SCL → GPIO4 (D4)
   - Connect SDA → GPIO5 (D5)
   - Connect 3V3 and GND

4. **Analog Sensor**
   - Connect sensor analog output through 10µF capacitor to A0
   - Ensure sensor is grounded to same GND

5. **Testing**
   - Flash `ble_lcd_analyzer.ino`
   - Check Serial Monitor (115200 baud)
   - Verify LCD shows battery % and "ADV" state
   - Confirm BLE device appears as "PCG_Monitor_Raw"

---

## Pin Usage Summary

| GPIO | Function | Used | Notes |
|------|----------|------|-------|
| A0 | ADC Input | ✅ | Battery voltage divider + sensor |
| D4 (GPIO4) | I2C SCL | ✅ | SSD1306 OLED |
| D5 (GPIO5) | I2C SDA | ✅ | SSD1306 OLED |
| GND | Ground | ✅ | 2 pins used |
| 3V3 | Power 3.3V | ✅ | Display + logic |
| 5V/VBAT | Battery | ✅ | Optional alt power input |

---

## Power Budget

| Component | Current | Voltage | Power |
|-----------|---------|---------|-------|
| ESP32-S3 | ~80 mA | 3.3V | 0.26W |
| OLED SSD1306 | ~10 mA | 3.3V | 0.03W |
| Sensor | ~5 mA | 3.3V | 0.02W |
| **Total** | **~95 mA** | **3.3V** | **~0.31W** |

**Runtime:** 2000 mAh battery ÷ 95 mA ≈ **21 hours** (nominal)

---

## Troubleshooting

**No Display:**
- Check I2C wiring (SDA/SCL swapped?)
- Verify 3V3 power to OLED
- Check Serial Monitor for I2C error messages

**No Sensor Data:**
- Verify A0 is not shorted
- Check sensor voltage is in 0-3.3V range
- Verify 10µF capacitor orientation (- to GND)

**Battery Reading Wrong:**
- Measure actual voltage at voltage divider midpoint
- Should be ~2.1V when battery is 4.2V
- Adjust resistor values if needed

**BLE Not Advertising:**
- Check Serial output: "BLE: Advertising started"
- Restart ESP32 if needed
- Ensure antenna connection (if external antenna model)

