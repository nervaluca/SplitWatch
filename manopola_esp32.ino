/*
  Manopola di sintonia per FZR SDR — ESP32 + encoder rotativo, collegata al PC con la USB (porta COM).

  Scheda: ESP32 Dev Module (DevKit con CP2102 o CH340). Nessuna libreria da installare.
  Arduino IDE: Strumenti → Scheda → esp32 → "ESP32 Dev Module", poi Carica.

  ENCODER (scegli sotto con TIPO_ENCODER):
    KY040   modulo per Arduino, 20 scatti/giro, con pulsante.
            + → 3V3 (NON 5V)   GND → GND   CLK → GPIO 18   DT → GPIO 19   SW → GPIO 21
    OTTICO  encoder ottico 600 impulsi/giro (es. LPD3806-600BM), open collector.
            rosso → VIN (5 V della USB)   nero → GND   verde (A) → GPIO 18   bianco (B) → GPIO 19
            pull-up da 2,2 kΩ fra GPIO 18 e 3V3 e fra GPIO 19 e 3V3 (mai verso i 5 V)
            il pulsante "passo" va messo a parte su GPIO 21 (verso GND)

  TASTI IN PIÙ (facoltativi, pulsante normalmente aperto verso GND, pull-up interno):
    GPIO 22 → K2 = blocca / sblocca il DX (🔒)
    GPIO 23 → K3 = la manopola passa da VFO A a VFO B e viceversa

  Protocollo verso il PC (115200 baud, una riga per messaggio):
    FZRKNOB 1 <tipo>   presentazione, all'avvio e quando il PC manda "?"
    E+3 / E-1          scatti girati dall'ultimo invio (al massimo uno ogni 10 ms)
    K1S / K1L          tasto 1 premuto breve / tenuto premuto (K2, K3 allo stesso modo)
  Verso, passo e accelerazione li decide FZR SDR: qui si contano solo gli scatti.
*/

#include <soc/soc.h>
#include <soc/gpio_reg.h>

#define KY040  1
#define OTTICO 2

// ======== scegli qui ========
#define TIPO_ENCODER  KY040
#define DIVISORE_OTTICO 24      // solo OTTICO: 2400 conteggi/giro ÷ 24 = 100 scatti/giro
// ============================

const int PIN_A  = 18;
const int PIN_B  = 19;
const int PIN_K1 = 21;          // pulsante dell'encoder: passo (breve), VFO A/B (lungo)
const int PIN_K2 = 22;          // facoltativo: blocco DX
const int PIN_K3 = 23;          // facoltativo: VFO A/B

const uint32_t INVIO_MS   = 10;   // ogni quanto mandare gli scatti accumulati
const uint32_t ANTIRIMB_MS = 25;  // antirimbalzo dei tasti
const uint32_t LUNGO_MS   = 600;  // pressione lunga

volatile int32_t conteggio = 0;
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;

static inline uint8_t leggiAB() {
  uint32_t r = REG_READ(GPIO_IN_REG);                 // GPIO 0–31 in un colpo solo, anche dentro l'interrupt
  return (((r >> PIN_A) & 1) << 1) | ((r >> PIN_B) & 1);
}

#if TIPO_ENCODER == KY040
// Tabella "a passo intero" (Ben Buxton): conta uno scatto solo quando il ciclo A/B è completo e valido.
// I rimbalzi dei contatti fanno avanti e indietro fra stati vicini e non contano nulla.
#define DIR_CW  0x10
#define DIR_CCW 0x20
#define R_START     0x0
#define R_CW_FINAL  0x1
#define R_CW_BEGIN  0x2
#define R_CW_NEXT   0x3
#define R_CCW_BEGIN 0x4
#define R_CCW_FINAL 0x5
#define R_CCW_NEXT  0x6
const uint8_t TAB[7][4] = {
  {R_START,    R_CW_BEGIN,  R_CCW_BEGIN, R_START},
  {R_CW_NEXT,  R_START,     R_CW_FINAL,  R_START | DIR_CW},
  {R_CW_NEXT,  R_CW_BEGIN,  R_START,     R_START},
  {R_CW_NEXT,  R_CW_BEGIN,  R_CW_FINAL,  R_START},
  {R_CCW_NEXT, R_START,     R_CCW_BEGIN, R_START},
  {R_CCW_NEXT, R_CCW_FINAL, R_START,     R_START | DIR_CCW},
  {R_CCW_NEXT, R_CCW_FINAL, R_CCW_BEGIN, R_START},
};
volatile uint8_t stato = R_START;

void IRAM_ATTR suEncoder() {
  uint8_t s = TAB[stato & 0x0f][leggiAB()];
  stato = s;
  portENTER_CRITICAL_ISR(&mux);
  if (s & DIR_CW)  conteggio++;
  if (s & DIR_CCW) conteggio--;
  portEXIT_CRITICAL_ISR(&mux);
}
#else
// Quadratura ×4: ogni fronte di A o di B vale un conteggio (2400 per giro con 600 impulsi).
const int8_t Q4[16] = {0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0};
volatile uint8_t vecchio = 0;

void IRAM_ATTR suEncoder() {
  uint8_t n = leggiAB();
  int8_t d = Q4[(vecchio << 2) | n];
  vecchio = n;
  portENTER_CRITICAL_ISR(&mux);
  conteggio += d;
  portEXIT_CRITICAL_ISR(&mux);
}
#endif

struct Tasto {
  int pin;
  char nome;
  bool giu;             // premuto (stabile)
  bool lungoFatto;
  uint32_t tCambio, tGiu;
};
Tasto tasti[] = {{PIN_K1, '1'}, {PIN_K2, '2'}, {PIN_K3, '3'}};

void presentati() {
  Serial.println(TIPO_ENCODER == KY040 ? "FZRKNOB 1 KY040" : "FZRKNOB 1 OTTICO");
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_A, INPUT_PULLUP);
  pinMode(PIN_B, INPUT_PULLUP);
  for (auto &t : tasti) pinMode(t.pin, INPUT_PULLUP);
#if TIPO_ENCODER == OTTICO
  vecchio = leggiAB();
#endif
  attachInterrupt(digitalPinToInterrupt(PIN_A), suEncoder, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_B), suEncoder, CHANGE);
  delay(50);
  presentati();
}

void leggiTasti(uint32_t ora) {
  for (auto &t : tasti) {
    bool g = digitalRead(t.pin) == LOW;
    if (g != t.giu) {
      if (ora - t.tCambio < ANTIRIMB_MS) continue;
      t.tCambio = ora;
      t.giu = g;
      if (g) {
        t.tGiu = ora;
        t.lungoFatto = false;
      } else if (!t.lungoFatto) {
        Serial.printf("K%cS\n", t.nome);              // rilasciato prima del tempo lungo: breve
      }
    } else if (g && !t.lungoFatto && ora - t.tGiu >= LUNGO_MS) {
      t.lungoFatto = true;                             // lungo: si manda subito, senza aspettare il rilascio
      Serial.printf("K%cL\n", t.nome);
    }
  }
}

void loop() {
  static uint32_t tInvio = 0;
  static int32_t resto = 0;
  uint32_t ora = millis();

  while (Serial.available()) {
    if (Serial.read() == '?') presentati();
  }

  if (ora - tInvio >= INVIO_MS) {
    tInvio = ora;
    portENTER_CRITICAL(&mux);
    int32_t c = conteggio;
    conteggio = 0;
    portEXIT_CRITICAL(&mux);
#if TIPO_ENCODER == OTTICO
    resto += c;
    c = resto / DIVISORE_OTTICO;                       // scatti interi; il resto aspetta il giro dopo
    resto -= c * DIVISORE_OTTICO;
#endif
    if (c) Serial.printf("E%+ld\n", (long)c);
  }

  leggiTasti(ora);
  delay(1);
}
