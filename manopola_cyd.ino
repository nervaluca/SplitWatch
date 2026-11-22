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
    D <ora> <snr> <Hz> <messaggio>   decodifica FT8 (D* = c'è il mio nominativo)
  e dal display: PG 0 / PG 1 = pagina mostrata (manopola / decodifiche), così il PC non manda righe inutili
      X=<0 split spento, 1 acceso, 2 modi skimmer> M=<S-meter 0…100, -1 ignoto> N=<S-meter scritto>
      T=<0 TX non disponibile, 1 pronto, 2 in trasmissione> W=<1 avviso bandplan>
*/

// ======== da regolare se serve ========
#define PANNELLO_ST7789   0     // 0 = ILI9341 (anche la tua CYD a due prese) · 1 = ST7789 (se lo schermo resta bianco)
#define INVERTI_COLORI    0     // sfondo bianco invece che nero? metti 1 (o 0 se era 1)
#define ORDINE_BGR        0     // rosso e blu scambiati (la frequenza è azzurra invece che arancio)? metti 1
#define ROTAZIONE         1     // schermo capovolto? prova 3
#define PULSANTE_ENCODER  0     // 1 dopo aver messo la resistenza da 10 kΩ su IO35
// tocco: valori grezzi di partenza. NON serve più toccarli: tieni il dito fermo 2 s sullo schermo
// e parte la taratura guidata (tre croci), che si salva nella memoria dell'ESP32.
// Misurati su una CYD ILI9341 a due prese: gli assi del tocco sono scambiati e rovesciati tutti e due
// (il raw x segue l'alto-basso, il raw y il destra-sinistra). Il primo numero è il bordo a schermo 0.
#define T_XMIN 3721            // raw (dopo lo scambio) al bordo sinistro
#define T_XMAX 180             // e al bordo destro
#define T_YMIN 3786            // raw in cima
#define T_YMAX 317             // e in fondo
#define T_SCAMBIA_XY 1
#define T_INVERTI_X  0         // il verso è già nei numeri qui sopra: da cambiare solo se li rimetti crescenti
#define T_INVERTI_Y  0
// ======================================

#include <Preferences.h>
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
  uint8_t split = 2;                 // 0 spento · 1 acceso · 2 modi skimmer (non c'entra)
  int16_t m = -1;                    // S-meter 0…100 sulla scala di FZR SDR (-1 = non si sa)
  char sm[8] = "";                   // S-meter scritto (S7, S9+20)
  uint8_t tx = 0;                    // 0 non disponibile · 1 pronto · 2 in trasmissione
  bool warn = false;                 // avviso bandplan
};
Stato st, vis;                        // st = da mostrare, vis = quello già disegnato
bool primo = true;
uint32_t tUltimoS = 0;
int tocX = -1, tocY = -1, tocRX = 0, tocRY = 0;
// taratura del tocco: valori grezzi che corrispondono ai bordi dello schermo (possono essere al contrario)
Preferences prefs;
int TAR[4] = {T_XMIN, T_XMAX, T_YMIN, T_YMAX};
bool tarSwap = T_SCAMBIA_XY, tarato = false;   // tarato = presa dalla memoria, non dai #define
const uint32_t PASSI[] = {10, 50, 100, 500, 1000};

// ---------- tasti sullo schermo ----------
// Ognuno mostra il valore che cambia: passo, VFO mosso dalla manopola, blocco del DX, modo.
struct Tasto { int x, y, w, h; const char *nome; char k; };
Tasto tasti[] = {
  {4, 186, 74, 50, "PASSO", '1'},
  {83, 186, 74, 50, "VFO", '3'},
  {162, 186, 74, 50, "FT8", '5'},           // apre la pagina delle decodifiche (comando solo qui, non va al PC)
  {241, 186, 75, 50, "MODO", '4'},
};
const int NTASTI = sizeof tasti / sizeof tasti[0];
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

String valoreTasto(int i) {                     // il valore scritto in grande dentro il tasto
  switch (tasti[i].k) {
    case '1': return fmtPasso(st.p);
    case '3': return String(st.v);
    case '5': return "apri";
    default:  return String(st.modo);
  }
}

void disegnaTasto(int i, bool premuto) {
  Tasto &t = tasti[i];
  bool bloccato = tasti[i].k == '2' && st.lock;      // (tasto del blocco: non c'è più, resta se lo rimetti)
  String val = valoreTasto(i);
  uint16_t fondo = premuto ? C_BORDO : bloccato ? 0x5800 : C_TASTO;
  uint16_t cval = bloccato ? C_TESTO : C_FREQ;
  lcd.fillRoundRect(t.x, t.y, t.w, t.h, 8, fondo);
  lcd.drawRoundRect(t.x, t.y, t.w, t.h, 8, bloccato ? C_ROSSO : C_BORDO);
  lcd.setTextDatum(middle_center);
  lcd.setFont(&fonts::Font0);                   // nome piccolo sopra, valore grande sotto
  lcd.setTextColor(C_GRIGIO, fondo);
  lcd.drawString(t.nome, t.x + t.w / 2, t.y + 10);
  lcd.setFont(val.length() > 4 ? &fonts::FreeSansBold9pt7b : &fonts::FreeSansBold12pt7b);
  lcd.setTextColor(cval, fondo);
  lcd.drawString(val, t.x + t.w / 2, t.y + t.h / 2 + 6);
}

// ---------- S-meter: la barra arriva già pronta da FZR SDR (0…100 sulla sua scala, S9 al 60%) ----------
void disegnaSmeter() {
  const int x0 = 8, y0 = 142, w = 304, h = 26;
  if (!st.vivo) {                                // in PROVA al posto dell'S-meter c'è il promemoria
    lcd.fillRect(0, 136, 320, 44, C_FONDO);
    lcd.setFont(&fonts::FreeSans9pt7b);
    lcd.setTextDatum(middle_center);
    lcd.setTextColor(C_GRIGIO, C_FONDO);
    lcd.drawString("dito fermo 2 s sullo schermo: taratura del tocco", 160, 158);
    return;
  }
  lcd.fillRoundRect(x0, y0, w, h, 6, C_TASTO);
  lcd.drawRoundRect(x0, y0, w, h, 6, C_BORDO);
  const int dentro = w - 4, s9 = dentro * 60 / 100;
  if (st.m > 0) {
    int piena = dentro * (st.m > 100 ? 100 : st.m) / 100;
    int verde = piena < s9 ? piena : s9;
    if (verde > 2) lcd.fillRoundRect(x0 + 2, y0 + 2, verde, h - 4, 4, C_VERDE);
    if (piena > verde) lcd.fillRect(x0 + 2 + verde, y0 + 2, piena - verde, h - 4, C_ROSSO);
  }
  lcd.drawFastVLine(x0 + 2 + s9, y0 + 4, h - 8, C_GRIGIO);        // dove sta S9
  lcd.setFont(&fonts::FreeSansBold9pt7b);
  lcd.setTextDatum(middle_right);
  lcd.setTextColor(C_TESTO);
  lcd.drawString(st.sm, x0 + w - 8, y0 + h / 2);
}

// in trasmissione tutto lo schermo prende un bordo rosso (arancio se c'è l'avviso bandplan)
void disegnaBordo() {
  uint16_t c = st.tx == 2 ? (st.warn ? C_FREQ : C_ROSSO) : C_FONDO;
  lcd.drawRect(0, 0, 320, 240, c);
  lcd.drawRect(1, 1, 318, 238, c);
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

// ---------- pagina delle decodifiche FT8 ----------
const int NDEC = 9;
String dec[NDEC];
bool decMio[NDEC];
int ndec = 0;
bool decNuovo = false;
uint8_t pagina = 0;                            // 0 = manopola · 1 = decodifiche

void aggiungiDec(const char *t, bool mio) {    // la più nuova in cima
  for (int i = NDEC - 1; i > 0; i--) { dec[i] = dec[i - 1]; decMio[i] = decMio[i - 1]; }
  dec[0] = t;
  decMio[0] = mio;
  if (ndec < NDEC) ndec++;
  decNuovo = true;
}

void disegnaDecodifiche() {
  lcd.fillRect(0, 0, 320, 240, C_FONDO);
  lcd.fillRect(0, 0, 320, 26, 0x18E3);
  lcd.setFont(&fonts::FreeSansBold9pt7b);
  lcd.setTextDatum(middle_left);
  lcd.setTextColor(C_FREQ, 0x18E3);
  lcd.drawString("FT8 · decodifiche", 8, 13);
  lcd.setFont(&fonts::FreeSans9pt7b);
  lcd.setTextDatum(middle_right);
  lcd.setTextColor(C_GRIGIO, 0x18E3);
  lcd.drawString("tocca: torna", 312, 13);
  if (!ndec) {
    lcd.setFont(&fonts::FreeSans12pt7b);
    lcd.setTextDatum(middle_center);
    lcd.setTextColor(C_GRIGIO, C_FONDO);
    lcd.drawString(st.vivo ? "aspetto le decodifiche..." : "FZR SDR non collegato", 160, 120);
  }
  for (int i = 0; i < ndec; i++) {
    int y = 42 + i * 22;
    String r = dec[i];
    int p = -1;
    for (int k = 0; k < 3; k++) { p = r.indexOf(' ', p + 1); if (p < 0) break; }
    String capo = p > 0 ? r.substring(0, p) : r, msg = p > 0 ? r.substring(p + 1) : "";
    lcd.setFont(&fonts::Font0);
    lcd.setTextDatum(middle_left);
    lcd.setTextColor(C_GRIGIO, C_FONDO);
    lcd.drawString(capo, 6, y);
    lcd.setFont(&fonts::FreeSans9pt7b);
    lcd.setTextColor(decMio[i] ? C_FREQ : msg.startsWith("CQ") ? C_VERDE : C_TESTO, C_FONDO);
    lcd.drawString(msg, 96, y);
  }
  decNuovo = false;
}

void vaiAPagina(uint8_t p) {
  pagina = p;
  Serial.printf("PG %u\n", p);                // il PC manda le decodifiche solo quando le guardo
  if (p == 1) { decNuovo = true; disegnaDecodifiche(); }
  else primo = true;                           // alla manopola ci ripensa il ciclo
}

void disegna() {
  bool tutto = primo;
  if (tutto) {
    lcd.fillScreen(C_FONDO);
    lcd.drawFastHLine(0, 28, 320, C_BORDO);
    for (int i = 0; i < NTASTI; i++) disegnaTasto(i, false);
  }
  // barra in alto: VFO mosso dalla manopola · modo · collegamento
  static const char *bandaVis = nullptr;
  const char *bnd = banda(st.a);
  if (tutto || st.v != vis.v || bnd != bandaVis || st.vivo != vis.vivo || st.tx != vis.tx || st.warn != vis.warn) {
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
    if (st.tx == 2) {                           // in trasmissione: al posto della scritta, TX ben visibile
      uint16_t c = st.warn ? C_FREQ : C_ROSSO;
      lcd.fillRoundRect(st.warn ? 232 : 262, 3, st.warn ? 84 : 54, 22, 6, c);
      lcd.setFont(&fonts::FreeSansBold9pt7b);
      lcd.setTextDatum(middle_center);
      lcd.setTextColor(st.warn ? TFT_BLACK : TFT_WHITE);
      lcd.drawString(st.warn ? "TX  BANDA!" : "TX", st.warn ? 274 : 289, 14);
    } else {
      lcd.setFont(&fonts::FreeSans9pt7b);
      lcd.setTextColor(st.vivo ? C_VERDE : 0xFD20, C_FONDO);
      lcd.drawString(st.vivo ? "FZR SDR" : "PROVA", 316, 14);
    }
  }
  if (tutto || st.a != vis.a || st.b != vis.b || st.v != vis.v) disegnaFrequenza();
  // riga sotto: l'altro VFO
  if (tutto || st.a != vis.a || st.b != vis.b || st.v != vis.v || st.split != vis.split || st.lock != vis.lock) {
    lcd.fillRect(0, 100, 320, 30, C_FONDO);
    lcd.setFont(&fonts::FreeSans12pt7b);
    lcd.setTextDatum(middle_left);
    lcd.setTextColor(C_GRIGIO, C_FONDO);
    String r = st.v == 'A' ? (st.b ? String("B  ") + fmtFreq(st.b) : String("B  spento")) : String("A  ") + fmtFreq(st.a);
    lcd.drawString(r, 8, 115);
    if (st.lock) {                              // DX bloccato in FZR SDR: la manopola non lo sposta
      lcd.fillRoundRect(150, 102, 88, 24, 6, C_ROSSO);
      lcd.setFont(&fonts::FreeSansBold9pt7b);
      lcd.setTextDatum(middle_center);
      lcd.setTextColor(TFT_WHITE);
      lcd.drawString("DX fermo", 194, 114);
    }
    if (st.split == 1) {                        // split acceso: il TX sta sul VFO B
      lcd.fillRoundRect(244, 102, 68, 24, 6, C_AZZ);
      lcd.setFont(&fonts::FreeSansBold9pt7b);
      lcd.setTextDatum(middle_center);
      lcd.setTextColor(TFT_BLACK);
      lcd.drawString("SPLIT", 278, 114);
    }
  }
  if (tutto || st.m != vis.m || strcmp(st.sm, vis.sm) || st.vivo != vis.vivo) disegnaSmeter();
  if (!tutto && st.p != vis.p) disegnaTasto(0, false);
  if (!tutto && strcmp(st.modo, vis.modo)) disegnaTasto(3, false);
  if (!tutto && st.v != vis.v) disegnaTasto(1, false);
  disegnaBordo();                               // due righe sottili: si rifà sempre, costa niente
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
  if (r[0] == 'D' && r[1] == ' ') { aggiungiDec(r + 2, false); return; }
  if (r[0] == 'D' && r[1] == '*' && r[2] == ' ') { aggiungiDec(r + 3, true); return; }
  if (r[0] != 'S' || r[1] != ' ') return;
  st.sm[0] = 0;                                 // «N=» vuoto non arriva come campo: lo azzero prima
  st.m = -1;
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
    else if (!strcmp(t, "X")) st.split = (uint8_t)atoi(v);
    else if (!strcmp(t, "M")) st.m = (int16_t)atoi(v);
    else if (!strcmp(t, "N")) { strncpy(st.sm, v, sizeof st.sm - 1); st.sm[sizeof st.sm - 1] = 0; }
    else if (!strcmp(t, "T")) st.tx = (uint8_t)atoi(v);
    else if (!strcmp(t, "W")) st.warn = v[0] == '1';
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
  if (tarSwap) { int t = rx; rx = ry; ry = t; }
  x = map(rx, TAR[0], TAR[1], 0, 319);
  y = map(ry, TAR[2], TAR[3], 0, 239);
  if (!tarato) {                               // senza taratura salvata valgono i #define
    if (T_INVERTI_X) x = 319 - x;
    if (T_INVERTI_Y) y = 239 - y;
  }
  x = constrain(x, 0, 319);
  y = constrain(y, 0, 239);
  return true;
}

// ---------- taratura del tocco: tre croci, poi si salva nella memoria dell'ESP32 ----------
void caricaTaratura() {
  prefs.begin("manopola", true);
  int x0 = prefs.getInt("x0", -99999);
  if (x0 != -99999) {
    TAR[0] = x0;
    TAR[1] = prefs.getInt("x1", T_XMAX);
    TAR[2] = prefs.getInt("y0", T_YMIN);
    TAR[3] = prefs.getInt("y1", T_YMAX);
    tarSwap = prefs.getBool("sw", false);
    tarato = true;
  }
  prefs.end();
}

void scordaTaratura() {                        // torna ai valori scritti nel firmware
  prefs.begin("manopola", false);
  prefs.clear();
  prefs.end();
  TAR[0] = T_XMIN; TAR[1] = T_XMAX; TAR[2] = T_YMIN; TAR[3] = T_YMAX;
  tarSwap = T_SCAMBIA_XY;
  tarato = false;
}

void salvaTaratura() {
  prefs.begin("manopola", false);
  prefs.putInt("x0", TAR[0]); prefs.putInt("x1", TAR[1]);
  prefs.putInt("y0", TAR[2]); prefs.putInt("y1", TAR[3]);
  prefs.putBool("sw", tarSwap);
  prefs.end();
  tarato = true;
}

void scrivoAlCentro(const char *t, int y, const lgfx::IFont *f, uint16_t c) {
  lcd.setFont(f);
  lcd.setTextDatum(middle_center);
  lcd.setTextColor(c, C_FONDO);
  lcd.drawString(t, 160, y);
}

// aspetta che il dito sia staccato per un quarto di secondo (true = staccato, false = tempo scaduto)
bool aspettaDitoSu(uint32_t fine) {
  uint32_t libero = millis();
  while (millis() < fine) {
    leggiSeriale();
    lgfx::touch_point_t tp;
    if (lcd.getTouchRaw(&tp, 1)) libero = millis();
    else if (millis() - libero > 250) return true;
    delay(10);
  }
  return false;
}

// aspetta che il dito tocchi la croce e la lasci; restituisce i valori grezzi (false = tempo scaduto)
bool prendiPunto(int px, int py, int &rx, int &ry) {
  lcd.fillRect(0, 60, 320, 120, C_FONDO);
  scrivoAlCentro("Tocca il centro della croce", 110, &fonts::FreeSans12pt7b, C_TESTO);
  lcd.drawFastHLine(px - 14, py, 29, C_FREQ);
  lcd.drawFastVLine(px, py - 14, 29, C_FREQ);
  lcd.drawCircle(px, py, 7, C_FREQ);
  uint32_t fine = millis() + 20000;
  if (!aspettaDitoSu(fine)) return false;      // il dito della pressione lunga è ancora giù: aspetto
  long sx = 0, sy = 0;
  int n = 0;
  while (millis() < fine) {
    leggiSeriale();                            // intanto non lascio riempire la seriale
    lgfx::touch_point_t tp;
    if (lcd.getTouchRaw(&tp, 1)) {
      sx += tp.x; sy += tp.y; n++;
      if (n >= 12) {                           // dodici letture: la media toglie il tremolio
        rx = sx / n; ry = sy / n;
        lcd.fillCircle(px, py, 7, C_VERDE);
        while (millis() < fine && lcd.getTouchRaw(&tp, 1)) delay(10);   // aspetto che stacchi il dito
        delay(150);
        return true;
      }
    } else { sx = sy = 0; n = 0; }
    delay(12);
  }
  return false;
}

void taratura() {
  lcd.fillScreen(C_FONDO);
  scrivoAlCentro("Taratura del tocco", 26, &fonts::FreeSansBold12pt7b, C_FREQ);
  scrivoAlCentro("tre croci, una alla volta  ·  non toccare = annulla", 200, &fonts::FreeSans9pt7b, C_GRIGIO);
  int ax, ay, bx, by, cx, cy;
  bool ok = prendiPunto(24, 24, ax, ay) && prendiPunto(296, 24, bx, by) && prendiPunto(24, 216, cx, cy);
  if (ok) {
    tarSwap = abs(bx - ax) < abs(by - ay);     // muovendomi in orizzontale cambia di più la y grezza: assi scambiati
    long x1 = tarSwap ? ay : ax, x2 = tarSwap ? by : bx;
    long y1 = tarSwap ? ax : ay, y2 = tarSwap ? cx : cy;
    if (abs(x2 - x1) > 300 && abs(y2 - y1) > 300) {      // letture sensate
      TAR[0] = x1 - (x2 - x1) * 24 / 272;                // valore grezzo al bordo sinistro (schermo 0)
      TAR[1] = x1 + (x2 - x1) * 295 / 272;               // e al bordo destro (schermo 319)
      TAR[2] = y1 - (y2 - y1) * 24 / 192;
      TAR[3] = y1 + (y2 - y1) * 215 / 192;
      salvaTaratura();
      lcd.fillRect(0, 60, 320, 120, C_FONDO);
      scrivoAlCentro("Fatto, taratura salvata", 110, &fonts::FreeSans12pt7b, C_VERDE);
    } else {
      ok = false;
    }
  }
  if (!ok) {                                   // annullata: si torna ai valori scritti nel firmware
    scordaTaratura();
    lcd.fillRect(0, 60, 320, 120, C_FONDO);
    scrivoAlCentro("Annullata: valori del firmware", 110, &fonts::FreeSans12pt7b, C_ROSSO);
  }
  delay(1500);
  portENTER_CRITICAL(&mux);
  conteggio = 0;                               // scatti presi mentre taravo: li butto, il VFO non deve saltare
  portEXIT_CRITICAL(&mux);
  primo = true;
  disegna();
}

void gestisciTocco() {
  static uint32_t tPoll = 0, tVisto = 0, tGiu = 0;
  static bool dito = false, fuoriTasti = false; // dito appoggiato (con antirimbalzo)
  uint32_t ora = millis();
  if (ora - tPoll < 25) return;
  tPoll = ora;
  int x, y;
  bool giu = leggiTocco(x, y);
  if (giu) {
    tVisto = ora;
    if (dito) {                                 // stesso tocco di prima: un comando solo per pressione
      if (fuoriTasti && ora - tGiu > 2000) {    // tenuto premuto due secondi: taratura del tocco
        fuoriTasti = false;
        dito = false;
        taratura();
      }
      return;
    }
    dito = true;
    tGiu = ora;
    if (pagina == 1) { vaiAPagina(0); return; } // sulle decodifiche un tocco qualsiasi torna alla manopola
    fuoriTasti = true;                          // da qualunque punto: col tocco storto i tasti non si trovano
    for (int i = 0; i < NTASTI; i++) {
      Tasto &t = tasti[i];
      if (x >= t.x && x < t.x + t.w && y >= t.y && y < t.y + t.h) {
        if (t.k == '5') { vaiAPagina(1); return; }
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
  caricaTaratura();
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

  static uint32_t tDis = 0, tDec = 0;
  if (pagina == 1) {                           // decodifiche: al massimo quattro ridisegni al secondo
    if (decNuovo && millis() - tDec >= 250) { tDec = millis(); disegnaDecodifiche(); }
  } else if (millis() - tDis >= 20) {          // al massimo 50 ridisegni al secondo, e solo se qualcosa è cambiato
    tDis = millis();
    if (primo || memcmp(&st, &vis, sizeof st)) disegna();
  }
}
