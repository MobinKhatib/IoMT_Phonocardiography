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

## Pinout Reference (Arduino Nano ESP32)

```
┌──────────────────────────────────────────┐
│      ARDUINO NANO ESP32 (Top View)       │
├──────────────────────────────────────────┤
│  D0/RX   GND   RESET  AREF   A0         │
│  D1/TX   GND    VIN    3V3   A1         │
│                                          │
│  D2      D3     D4     D5    D6          │
│  D7      D8     D9    D10   D11          │
│                                          │
│  D12    D13    GND    5V    RST          │
│  A2     A3    A4/SDA  A5/SCL            │
│         A6     A7     GND               │
│                                          │
│ I2C Default: SDA=A4(pin12)  SCL=A5(pin13)
│ Signal Input: A0 (pin19)                │
│ Battery Input: A1 (pin20)               │
└──────────────────────────────────────────┘
```

---

## Connection Diagram

### 1. OLED Display (SSD1306 I2C)

**Display Pins (Arduino Nano ESP32):**
- **GND** → Arduino Nano ESP32 GND
- **VCC** → Arduino Nano ESP32 3V3
- **SCL** → Arduino Nano ESP32 **A5** (pin 13)
- **SDA** → Arduino Nano ESP32 **A4** (pin 12)

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
- **SEPARATE from signal input** (A0 dedicated to battery only)

```
        LiPo Battery (4.2V max)
        │
        │ (+ terminal)
        ├──────────────────→ (to system power, ~5V input)
        │
        ├──[2.2kΩ resistor]──┬──→ A0 (ADC, Battery Monitor)
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

### 3. Phonocardiography Sensor Input (A1)

**Analog Signal Path:**
- **Sensor Output** → 10µF capacitor → **A1** (separate from battery A0)
- Optional: 2.2kΩ pull-up to 3V3 for impedance matching

```
    Phonocardiography Sensor
    (analog output, 0-3.3V)
           │
           ├──[10µF cap]──┬──→ A1 (ADC, Signal Input)
           │              │
           └──────────────┴──→ GND
                          
    (Optional pull-up for high-impedance sensors)
           ├──[2.2kΩ]──→ 3V3
```

**Why separate pins?**
- A0 dedicated to battery monitoring (always active)
- A1 dedicated to signal sampling (during analysis)
- Prevents interference between battery and signal readings

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
                    ┌────────┴────────────────┐
                    │                         │
              [Voltage Divider]          [Power]
              (2.2k/2.2k)                 │
                    │                     │
                    ↓                     ↓
                  A0 (Battery)        +5V (VBAT)
                    │                     │
            ┌───────┴─────┬───────────────┴────────┐
            │             │                        │
        ┌─────────────────────────────────┐   ┌─────────────┐
        │     ESP32-S3 NANO               │   │   100µF     │
        │                                 │   │   Capacitor │
        │  D4 ──────────┬─────────────────┼───┤ (decouple)  │
        │  D5 ──────────┤                 │   └─────┬───────┘
        │               │ (I2C for OLED)  │         │
        │  A0 ←─ Battery Divider          │      GND
        │  A1 ←─ PCG Sensor               │
        │  3V3 ──────────┘                │
        │  GND ──────────────────┬────────┘
        │                        │
        └────────────┬───────────┘
                     │
        ┌────────────┴────────────┐
        │                         │
    ┌─────────────────┐   ┌──────────────────┐
    │  SSD1306 OLED   │   │  PCG Sensor      │
    │  (48x64 display)│   │  (Analog Out)    │
    │                 │   │                  │
    │  SCL ← D4       │   │  OUT ──[10µF]──┐ │
    │  SDA ← D5       │   │                 │ │
    │  VCC ← 3V3      │   │  GND ───────────┼─┘
    │  GND ─────────┐ │   │                 │
    │              │ │   │              GND
    └──────────────┼─┘   └────────┬────────┘
                  │                │
                  └────────┬───────┘
                           │
                  GND (common return)

Key: A0=Battery  A1=Signal  D4=SCL  D5=SDA
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

## Pin Usage Summary (Arduino Nano ESP32)

| Pin | Function | Used | Notes |
|-----|----------|------|-------|
| A0 (pin 19) | ADC Input | ✅ | Battery voltage divider (2:1) |
| A1 (pin 20) | ADC Input | ✅ | PCG sensor analog signal (10µF cap) |
| A4 (pin 12) | I2C SDA | ✅ | SSD1306 OLED display |
| A5 (pin 13) | I2C SCL | ✅ | SSD1306 OLED display |
| GND | Ground | ✅ | 2+ pins (battery, display, sensor) |
| 3V3 | Power 3.3V | ✅ | Display + logic |
| VIN/5V | Battery | ✅ | System power input |

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

