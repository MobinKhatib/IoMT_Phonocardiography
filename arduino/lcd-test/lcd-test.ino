#include <U8g2lib.h>
#include <Wire.h>
#include <math.h>

U8G2_SSD1306_48X64_WINSTAR_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);

// In a real build, set this from the BLE write/characteristic the client sends.
const char* userName = "Sepehr";

// ---------- battery ----------
// Reads battery voltage from A0 through a 2:1 divider, returns 0-100%.
int batteryPercent() {
  // Average a few samples to reduce noise
  uint32_t mv = 0;
  for (int i = 0; i < 16; i++) mv += analogReadMilliVolts(A0);
  mv /= 16;

  float vbat = mv * 2.0 / 1000.0;   // x2 for the divider, mV -> V

  // Linear map: 3.3V = 0%, 4.2V = 100% (LiPo)
  int pct = (int)((vbat - 3.3) / (4.2 - 3.3) * 100.0);
  return constrain(pct, 0, 100);
}

// ---------- bluetooth glyph ----------
void drawBT(int cx, int ty) {
  int top = ty, bot = ty + 10, q1 = ty + 3, q3 = ty + 7;
  int r = cx + 3, l = cx - 3;
  u8g2.drawLine(cx, top, cx, bot);   // spine
  u8g2.drawLine(cx, top, r, q1);     // top -> right-upper
  u8g2.drawLine(r, q1, l, q3);       // cross down-left
  u8g2.drawLine(l, q1, r, q3);       // cross down-right
  u8g2.drawLine(r, q3, cx, bot);     // right-lower -> bottom
}

void drawCheck(int x, int y) {
  u8g2.drawLine(x, y + 2, x + 2, y + 4);
  u8g2.drawLine(x + 2, y + 4, x + 6, y - 1);
}

// Inverse "highlight" bar = the mono stand-in for a green status.
void textHighlight(int x, int y, int w, int h, const char* s, const uint8_t* font) {
  u8g2.drawBox(x, y, w, h);
  u8g2.setDrawColor(0);
  u8g2.setFont(font);
  u8g2.setCursor(x + 2, y + h - 2);
  u8g2.print(s);
  u8g2.setDrawColor(1);
}

void drawSpinner(int cx, int cy, int r) {
  u8g2.drawCircle(cx, cy, r);                  // track ring
  float a = millis() / 90.0;                   // rotation speed
  int sx = cx + (int)(cos(a) * r);
  int sy = cy + (int)(sin(a) * r);
  u8g2.drawDisc(sx, sy, 2);                     // orbiting dot
}

// ---------- top panel (always shown) ----------
void drawTopPanel() {
  u8g2.setFont(u8g2_font_5x7_tf);
  u8g2.setCursor(2, 10);
  u8g2.print("Bat:");
  u8g2.print(batteryPercent());
  u8g2.print("%");
  u8g2.drawHLine(0, 13, 48);
}

void setup() {
  u8g2.begin();
}

void loop() {
  unsigned long t = millis() % 11000;          // one full demo cycle

  u8g2.clearBuffer();
  drawTopPanel();

  if (t < 3000) {
    // ----- ADVERTISING -----
    if ((millis() / 400) % 2) drawBT(9, 20);    // blink = broadcasting
    u8g2.setFont(u8g2_font_5x7_tf);
    u8g2.setCursor(18, 30);
    u8g2.print("ADV");
    u8g2.setFont(u8g2_font_4x6_tf);
    u8g2.setCursor(2, 44);
    u8g2.print("advertising");

  } else if (t < 6000) {
    // ----- CONNECTED -----  (highlight bar = "green" on a color screen)
    drawBT(9, 20);
    drawCheck(34, 22);
    u8g2.setFont(u8g2_font_5x7_tf);
    u8g2.setCursor(18, 30);
    u8g2.print("CONN");
    textHighlight(2, 38, 46, 9, "connected", u8g2_font_4x6_tf);

  } else if (t < 7000) {
    // ----- REQUEST RECEIVED -----
    u8g2.setFont(u8g2_font_5x7_tf);
    u8g2.setCursor(6, 34);
    u8g2.print("REQUEST");
    u8g2.setFont(u8g2_font_4x6_tf);
    u8g2.setCursor(4, 46);
    u8g2.print("received");

  } else {
    // ----- ANALYZING -----
    u8g2.setFont(u8g2_font_4x6_tf);
    u8g2.setCursor(2, 22);
    u8g2.print("User:");
    u8g2.setFont(u8g2_font_5x7_tf);
    u8g2.setCursor(2, 33);
    u8g2.print(userName);
    u8g2.setFont(u8g2_font_4x6_tf);
    u8g2.setCursor(12, 45);          // "Analyzing" a bit more centered
    u8g2.print("Analyzing");
    drawSpinner(24, 55, 6);          // centered under the text
  }

  u8g2.sendBuffer();
  delay(30);                                    // ~30 fps, smooth spinner
}