/*
  Manopola di sintonia con display per FZR SDR — ESP32-2432S028 ("CYD", schermo 2,8" touch) + encoder KY-040.

  Libreria da installare (Arduino IDE → Gestore librerie): "LovyanGFX" di lovyan03.
  Scheda: "ESP32 Dev Module". Il Wi-Fi non serve: va tutto sulla USB (porta COM).

  ENCODER KY-040 → CYD
    +   → 3,3 V          GND → GND
    CLK → IO27 (CN1)     DT  → IO22 (CN1 o P3)
    SW  → IO35 (P3) con una resistenza da 10 kΩ fra IO35 e 3,3 V (IO35 non ha pull-up interno)
          finché la resistenza non c'è lascia PULSANTE_ENCODER a 0: il passo si cambia col tasto sullo schermo.
  IO21 non va usato: accende la retroilluminazione.

  SENZA PC parte in "PROVA": la frequenza la muove la manopola qui dentro, così vedi subito se va tutto.
  CON FZR SDR (Impostazioni → Rete e radio → Manopola → porta COM) la frequenza la decide il programma
  e lo schermo mostra quella vera.

  Protocollo: come la manopola semplice (E+3, K1S, K1L ...) più, dal PC:
    S A=<Hz VFO A> B=<Hz VFO B, 0 = spento> P=<passo Hz> V=<A|B> L=<0|1> O=<modo>
*/

// ======== da regolare se serve ========
#define PANNELLO_ST7789   0     // 0 = ILI9341 (anche la tua CYD a due prese) · 1 = ST7789 (se lo schermo resta bianco)
#define INVERTI_COLORI    0     // sfondo bianco invece che nero? metti 1 (o 0 se era 1)
#define ORDINE_BGR        0     // rosso e blu scambiati (la frequenza è azzurra invece che arancio)? metti 1
#define ROTAZIONE         1     // schermo capovolto? prova 3
#define PULSANTE_ENCODER  0     // 1 dopo aver messo la resistenza da 10 kΩ su IO35
// tocco: valori grezzi agli angoli (in PROVA li vedi in basso a destra toccando lo schermo)
#define T_XMIN 200
#define T_XMAX 3700
#define T_YMIN 240
#define T_YMAX 3800
#define T_SCAMBIA_XY 0
#define T_INVERTI_X  0
#define T_INVERTI_Y  0
// ======================================

#include <soc/soc.h>
#include <soc/gpio_reg.h>
#define LGFX_USE_V1
#include <LovyanGFX.hpp>

class LGFX : public lgfx::LGFX_Device {
#if PANNELLO_ST7789
  lgfx::Panel_ST7789 _panel;
#else
  lgfx::Panel_ILI9341 _panel;
#endif
  lgfx::Bus_SPI _bus;
  lgfx::Touch_XPT2046 _touch;
public:
  LGFX() {
    { auto c = _bus.config();
      c.spi_host = SPI2_HOST; c.spi_mode = 0; c.freq_write = 40000000; c.freq_read = 16000000;
      c.spi_3wire = false; c.use_lock = true; c.dma_channel = SPI_DMA_CH_AUTO;
      c.pin_sclk = 14; c.pin_mosi = 13; c.pin_miso = 12; c.pin_dc = 2;
      _bus.config(c); _panel.setBus(&_bus); }
    { auto c = _panel.config();
      c.pin_cs = 15; c.pin_rst = -1; c.pin_busy = -1;
      c.panel_width = 240; c.panel_height = 320; c.offset_x = 0; c.offset_y = 0; c.offset_rotation = 0;
      c.readable = false; c.invert = INVERTI_COLORI; c.rgb_order = ORDINE_BGR; c.dlen_16bit = false; c.bus_shared = false;
      _panel.config(c); }
    { auto c = _touch.config();               // il tocco è su un suo SPI separato
      c.x_min = 0; c.x_max = 4095; c.y_min = 0; c.y_max = 4095;
      c.pin_int = 36; c.bus_shared = false; c.offset_rotation = 0;
      c.spi_host = SPI3_HOST; c.freq = 1000000;
      c.pin_sclk = 25; c.pin_mosi = 32; c.pin_miso = 39; c.pin_cs = 33;
      _touch.config(c); _panel.setTouch(&_touch); }
    setPanel(&_panel);
  }
};

LGFX lcd;
LGFX_Sprite spr(&lcd);                         // la frequenza si disegna qui e poi si copia: niente sfarfallio

const int PIN_A = 27, PIN_B = 22, PIN_SW = 35, PIN_BL = 21;
const int LED_R = 4, LED_G = 16, LED_B = 17;   // LED RGB della scheda (acceso = LOW)

// colori
const uint16_t C_FONDO = TFT_BLACK, C_FREQ = 0xFD20, C_TESTO = TFT_WHITE, C_GRIGIO = 0x7BEF,
               C_VERDE = 0x07E0, C_ROSSO = 0xF800, C_TASTO = 0x2124, C_BORDO = 0x4A69, C_AZZ = 0x5D1F;

// ---------- encoder (tabella a passo intero: i rimbalzi del KY-040 non contano) ----------
#define DIR_CW 0x10
#define DIR_CCW 0x20
const uint8_t TAB[7][4] = {
  {0x0, 0x2, 0x4, 0x0}, {0x3, 0x0, 0x1, 0x0 | DIR_CW}, {0x3, 0x2, 0x0, 0x0}, {0x3, 0x2, 0x1, 0x0},
  {0x6, 0x0, 0x4, 0x0}, {0x6, 0x5, 0x0, 0x0 | DIR_CCW}, {0x6, 0x5, 0x4, 0x0},
};
volatile uint8_t encStato = 0;
volatile int32_t conteggio = 0;
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;

void IRAM_ATTR suEncoder() {
  uint32_t r = REG_READ(GPIO_IN_REG);
  uint8_t ab = (((r >> PIN_A) & 1) << 1) | ((r >> PIN_B) & 1);
  uint8_t s = TAB[encStato & 0x0f][ab];
  encStato = s;
  portENTER_CRITICAL_ISR(&mux);
  if (s & DIR_CW) conteggio++;
  if (s & DIR_CCW) conteggio--;
  portEXIT_CRITICAL_ISR(&mux);
}

// ---------- stato mostrato ----------
struct Stato {
  uint64_t a = 14025000, b = 0;
  uint32_t p = 100;
  char v = 'A';
  bool lock = false;
  char modo[10] = "CW";
  bool vivo = false;                 // true = arrivano dati da FZR SDR
};
Stato st, vis;                        // st = da mostrare, vis = quello già disegnato
bool primo = true;
uint32_t tUltimoS = 0;
int tocX = -1, tocY = -1, tocRX = 0, tocRY = 0;
const uint32_t PASSI[] = {10, 50, 100, 500, 1000};

// ---------- tasti sullo schermo ----------
struct Tasto { int x, y, w, h; const char *nome; char k; };
Tasto tasti[] = {
  {4, 186, 101, 50, "PASSO", '1'},
  {110, 186, 100, 50, "A / B", '3'},
  {215, 186, 101, 50, "MODO", '4'},       // mostra il modo (CW, USB, LSB...) e lo cambia
};
int tastoGiu = -1;

void presentati() { Serial.println("FZRKNOB 1 CYD"); }

String fmtFreq(uint64_t hz) {                  // 14025300 → "14.025.300"
  char t[24], o[32];
  snprintf(t, sizeof t, "%llu", (unsigned long long)hz);
  int n = strlen(t), j = 0;
  for (int i = 0; i < n; i++) {
    o[j++] = t[i];
    int resto = n - 1 - i;
    if (resto > 0 && resto % 3 == 0) o[j++] = '.';
  }
  o[j] = 0;
  return String(o);
}

String fmtPasso(uint32_t p) { return p >= 1000 ? String(p / 1000.0, p % 1000 ? 1 : 0) + " kHz" : String(p) + " Hz"; }

void disegnaTasto(int i, bool premuto) {
  Tasto &t = tasti[i];
  bool modo = t.k == '4';
  uint16_t fondo = premuto ? C_BORDO : (modo ? 0x18E3 : C_TASTO);
  lcd.fillRoundRect(t.x, t.y, t.w, t.h, 8, fondo);
  lcd.drawRoundRect(t.x, t.y, t.w, t.h, 8, modo ? C_FREQ : C_BORDO);
  lcd.setTextDatum(middle_center);
  if (modo) {                                   // il modo in grande, "MODO" piccolo sopra
    lcd.setFont(&fonts::Font0);
    lcd.setTextColor(C_GRIGIO, fondo);
    lcd.drawString("MODO", t.x + t.w / 2, t.y + 9);
    lcd.setFont(strlen(st.modo) > 4 ? &fonts::FreeSansBold9pt7b : &fonts::FreeSansBold12pt7b);
    lcd.setTextColor(C_FREQ, fondo);
    lcd.drawString(st.modo, t.x + t.w / 2, t.y + t.h / 2 + 5);
  } else {
    lcd.setFont(&fonts::FreeSansBold9pt7b);
    lcd.setTextColor(C_TESTO, fondo);
    lcd.drawString(t.nome, t.x + t.w / 2, t.y + t.h / 2);
  }
}

const char *banda(uint64_t hz) {               // banda radioamatoriale della frequenza, per la barra in alto
  struct B { uint32_t da, a; const char *n; };
  static const B bb[] = {{1810, 2000, "160 m"}, {3500, 3800, "80 m"}, {5350, 5367, "60 m"}, {7000, 7200, "40 m"},
    {10100, 10150, "30 m"}, {14000, 14350, "20 m"}, {18068, 18168, "17 m"}, {21000, 21450, "15 m"},
    {24890, 24990, "12 m"}, {28000, 29700, "10 m"}, {50000, 52000, "6 m"}, {144000, 146000, "2 m"},
    {430000, 440000, "70 cm"}, {10489500, 10490000, "QO-100"}};
  uint64_t k = hz / 1000;
  for (auto &b : bb) if (k >= b.da && k < b.a) return b.n;
  return "";
}

void disegnaFrequenza() {
  bool spento = st.v == 'B' && !st.b;           // manopola su VFO B, ma in FZR SDR il VFO B è spento
  String s = spento ? String("B spento") : fmtFreq(st.v == 'B' ? st.b : st.a);
  spr.fillSprite(C_FONDO);
  spr.setTextDatum(middle_right);
  spr.setTextColor(spento ? C_GRIGIO : C_FREQ);
  const lgfx::IFont *f[] = {&fonts::FreeSansBold24pt7b, &fonts::FreeSansBold18pt7b, &fonts::FreeSansBold12pt7b};
  for (auto ff : f) {                          // il carattere più grande che ci sta (QO-100 ha 11 cifre)
    spr.setFont(ff);
    if (spr.textWidth(s) <= 300) break;
  }
  spr.drawString(s, 312, 32);
  lcd.startWrite();
  spr.pushSprite(0, 34);
  lcd.endWrite();
}

void disegna() {
  bool tutto = primo;
  if (tutto) {
    lcd.fillScreen(C_FONDO);
    lcd.drawFastHLine(0, 28, 320, C_BORDO);
    for (int i = 0; i < 3; i++) disegnaTasto(i, false);
  }
  // barra in alto: VFO mosso dalla manopola · modo · collegamento
  static const char *bandaVis = nullptr;
  const char *bnd = banda(st.a);
  if (tutto || st.v != vis.v || bnd != bandaVis || st.vivo != vis.vivo) {
    bandaVis = bnd;
    lcd.fillRect(0, 0, 320, 27, C_FONDO);
    lcd.fillRoundRect(4, 3, 70, 22, 6, st.v == 'A' ? C_VERDE : C_AZZ);
    lcd.setFont(&fonts::FreeSansBold9pt7b);
    lcd.setTextDatum(middle_center);
    lcd.setTextColor(TFT_BLACK);
    lcd.drawString(st.v == 'A' ? "VFO A" : "VFO B", 39, 14);
    lcd.setTextColor(C_TESTO, C_FONDO);
    lcd.drawString(bnd, 160, 14);
    lcd.setTextDatum(middle_right);
    lcd.setFont(&fonts::FreeSans9pt7b);
    lcd.setTextColor(st.vivo ? C_VERDE : 0xFD20, C_FONDO);
    lcd.drawString(st.vivo ? "FZR SDR" : "PROVA", 316, 14);
  }
  if (tutto || st.a != vis.a || st.b != vis.b || st.v != vis.v) disegnaFrequenza();
  // riga sotto: l'altro VFO
  if (tutto || st.a != vis.a || st.b != vis.b || st.v != vis.v) {
    lcd.fillRect(0, 100, 320, 30, C_FONDO);
    lcd.setFont(&fonts::FreeSans12pt7b);
    lcd.setTextDatum(middle_left);
    lcd.setTextColor(C_GRIGIO, C_FONDO);
    String r = st.v == 'A' ? (st.b ? String("B  ") + fmtFreq(st.b) : String("B  spento")) : String("A  ") + fmtFreq(st.a);
    lcd.drawString(r, 8, 115);
  }
  // passo e blocco
  if (tutto || st.p != vis.p || st.lock != vis.lock) {
    lcd.fillRect(0, 136, 320, 44, C_FONDO);
    lcd.setFont(&fonts::FreeSans12pt7b);
    lcd.setTextDatum(middle_left);
    lcd.setTextColor(C_TESTO, C_FONDO);
    lcd.drawString("Passo " + fmtPasso(st.p), 8, 158);
    if (st.lock) {
      lcd.fillRoundRect(206, 144, 106, 28, 6, C_ROSSO);
      lcd.setFont(&fonts::FreeSansBold9pt7b);
      lcd.setTextDatum(middle_center);
      lcd.setTextColor(TFT_WHITE);
      lcd.drawString("BLOCCATO", 259, 158);
    }
  }
  if (!tutto && strcmp(st.modo, vis.modo)) disegnaTasto(2, false);
  vis = st;
  primo = false;
}

// in PROVA i comandi agiscono qui; con FZR SDR vanno al PC, che rimanda lo stato giusto
void comando(char k, bool lungo) {
  Serial.printf("K%c%c\n", k, lungo ? 'L' : 'S');   // K4 = tasto MODO
  if (st.vivo) return;
  if (k == '1' && !lungo) {
    int i = 0;
    while (i < 5 && PASSI[i] != st.p) i++;
    st.p = PASSI[(i + 1) % 5];
  } else if ((k == '1' && lungo) || k == '3') {
    st.v = st.v == 'A' ? 'B' : 'A';
    if (st.v == 'B' && !st.b) st.b = st.a + 1000;
  } else if (k == '2') {
    st.lock = !st.lock;
  } else if (k == '4') {
    static const char *modi[] = {"CW", "USB", "LSB", "AM", "FM", "RTTY", "FT8"};
    int i = 0;
    while (i < 7 && strcmp(modi[i], st.modo)) i++;
    strcpy(st.modo, modi[(i + 1) % 7]);
  }
}

void giraProva(int32_t n) {
  if (st.vivo) return;
  uint64_t &f = st.v == 'B' ? st.b : st.a;
  if (st.v == 'A' && st.lock) return;
  int64_t g = (int64_t)(f / st.p) + n;         // sulla griglia del passo
  if (g > 0) f = (uint64_t)g * st.p;
}

void leggiRiga(char *r) {
  if (!strcmp(r, "?")) { presentati(); return; }
  if (r[0] != 'S' || r[1] != ' ') return;
  for (char *t = strtok(r + 2, " "); t; t = strtok(nullptr, " ")) {
    char *eq = strchr(t, '=');
    if (!eq) continue;
    *eq = 0;
    const char *v = eq + 1;
    if (!strcmp(t, "A")) st.a = strtoull(v, nullptr, 10);
    else if (!strcmp(t, "B")) st.b = strtoull(v, nullptr, 10);
    else if (!strcmp(t, "P")) st.p = max(1UL, strtoul(v, nullptr, 10));
    else if (!strcmp(t, "V")) st.v = v[0] == 'B' ? 'B' : 'A';
    else if (!strcmp(t, "L")) st.lock = v[0] == '1';
    else if (!strcmp(t, "O")) { strncpy(st.modo, v, sizeof st.modo - 1); st.modo[sizeof st.modo - 1] = 0; }
  }
  st.vivo = true;
  tUltimoS = millis();
}

void leggiSeriale() {
  static char buf[160];
  static int n = 0;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') { buf[n] = 0; leggiRiga(buf); n = 0; }
    else if (n < (int)sizeof buf - 1) buf[n++] = c;
    else n = 0;
  }
}

bool leggiTocco(int &x, int &y) {
  lgfx::touch_point_t tp;
  if (!lcd.getTouchRaw(&tp, 1)) return false;
  int rx = tp.x, ry = tp.y;
  tocRX = rx; tocRY = ry;
  if (T_SCAMBIA_XY) { int t = rx; rx = ry; ry = t; }
  x = map(rx, T_XMIN, T_XMAX, 0, 319);
  y = map(ry, T_YMIN, T_YMAX, 0, 239);
  if (T_INVERTI_X) x = 319 - x;
  if (T_INVERTI_Y) y = 239 - y;
  x = constrain(x, 0, 319);
  y = constrain(y, 0, 239);
  return true;
}

void gestisciTocco() {
  static uint32_t tPoll = 0, tVisto = 0;
  static bool dito = false;                     // dito appoggiato (con antirimbalzo)
  uint32_t ora = millis();
  if (ora - tPoll < 25) return;
  tPoll = ora;
  int x, y;
  bool giu = leggiTocco(x, y);
  if (giu) {
    tVisto = ora;
    if (dito) return;                           // stesso tocco di prima: un comando solo per pressione
    dito = true;
    for (int i = 0; i < 3; i++) {
      Tasto &t = tasti[i];
      if (x >= t.x && x < t.x + t.w && y >= t.y && y < t.y + t.h) {
        tastoGiu = i;
        disegnaTasto(i, true);
        comando(t.k, false);
      }
    }
    if (!st.vivo) {                             // in PROVA: dove tocchi e i valori grezzi, per la taratura
      lcd.fillRect(200, 100, 120, 30, C_FONDO);
      lcd.setFont(&fonts::Font0);
      lcd.setTextDatum(top_right);
      lcd.setTextColor(C_GRIGIO, C_FONDO);
      lcd.drawString(String("raw ") + tocRX + "," + tocRY, 316, 104);
      lcd.drawString(String("xy ") + x + "," + y, 316, 116);
      lcd.fillCircle(x, y, 3, C_AZZ);
    }
  } else if (dito && ora - tVisto > 150) {      // il touch "perde" il dito per qualche ms: è rilasciato solo dopo 150 ms
    dito = false;
    if (tastoGiu >= 0) {
      disegnaTasto(tastoGiu, false);
      tastoGiu = -1;
    }
  }
}

void gestisciPulsante() {
#if PULSANTE_ENCODER
  static bool giu = false, lungoFatto = false;
  static uint32_t tCambio = 0, tGiu = 0;
  bool g = digitalRead(PIN_SW) == LOW;
  uint32_t ora = millis();
  if (g != giu) {
    if (ora - tCambio < 25) return;
    tCambio = ora;
    giu = g;
    if (g) { tGiu = ora; lungoFatto = false; }
    else if (!lungoFatto) comando('1', false);
  } else if (g && !lungoFatto && ora - tGiu >= 600) {
    lungoFatto = true;
    comando('1', true);
  }
#endif
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_R, OUTPUT); pinMode(LED_G, OUTPUT); pinMode(LED_B, OUTPUT);
  digitalWrite(LED_R, HIGH); digitalWrite(LED_G, HIGH); digitalWrite(LED_B, HIGH);   // LED spento
  pinMode(PIN_BL, OUTPUT);
  digitalWrite(PIN_BL, HIGH);                  // retroilluminazione
  lcd.init();
  lcd.setRotation(ROTAZIONE);
  spr.setColorDepth(16);
  spr.createSprite(320, 64);
  pinMode(PIN_A, INPUT_PULLUP);
  pinMode(PIN_B, INPUT_PULLUP);
  pinMode(PIN_SW, INPUT);                      // IO35: pull-up esterno da 10 kΩ
  attachInterrupt(digitalPinToInterrupt(PIN_A), suEncoder, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_B), suEncoder, CHANGE);
  disegna();
  presentati();
}

void loop() {
  static uint32_t tInvio = 0;
  leggiSeriale();
  if (st.vivo && millis() - tUltimoS > 6000) st.vivo = false;   // FZR SDR chiuso: si torna in PROVA

  if (millis() - tInvio >= 10) {
    tInvio = millis();
    portENTER_CRITICAL(&mux);
    int32_t c = conteggio;
    conteggio = 0;
    portEXIT_CRITICAL(&mux);
    if (c) {
      Serial.printf("E%+ld\n", (long)c);
      giraProva(c);
    }
  }
  gestisciPulsante();
  gestisciTocco();

  static uint32_t tDis = 0;
  if (millis() - tDis >= 20) {                 // al massimo 50 ridisegni al secondo, e solo se qualcosa è cambiato
    tDis = millis();
    if (primo || memcmp(&st, &vis, sizeof st)) disegna();
  }
}
