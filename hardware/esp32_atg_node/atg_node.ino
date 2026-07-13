/* ============================================================================
 * ATG-P8  |  Americas TechGuard - Periodo 8
 * No sensor de nivel de rio: ESP32 (T-Beam V1.1 / Heltec V3) + HC-SR04
 *
 * O que este firmware faz:
 *   1. le o nivel do rio com o sensor ultrassonico (mesmo sensor de
 *      Zakaria et al. 2023, HC-SR04, faixa 2-400 cm);
 *   2. calcula a taxa de variacao (m/h);
 *   3. classifica o risco pela escada OFICIAL da Defesa Civil de Blumenau;
 *   4. serializa no formato de fio ATG-C1-BIN (23 bytes) - identico, byte a
 *      byte, ao que o codec Python produz (src/atg_mesh/codec.py);
 *   5. emite pela serial em Base64, no formato que o no Meshtastic vizinho
 *      consome (Serial Module) ou que a placa envia como TEXT_MESSAGE_APP;
 *   6. ajusta o intervalo de reporte conforme o risco (economia de bateria e
 *      de airtime: 60 min em calmaria, 60 s em alerta maximo).
 *
 * Integracao com o Meshtastic (trilha A, presencial em Joinville):
 *   - opcao 1: Serial Module do Meshtastic
 *       meshtastic --set serial.enabled true --set serial.mode TEXTMSG \
 *                  --set serial.rxd 16 --set serial.txd 17 --set serial.baud BAUD_38400
 *     A string Base64 impressa aqui vira um TEXT_MESSAGE_APP na malha.
 *   - opcao 2: firmware Meshtastic customizado enviando os 23 bytes crus em
 *     PortNum PRIVATE_APP (256).
 *
 * NAO contem credenciais, tokens ou chaves.
 * ==========================================================================*/

#include <Arduino.h>
#include <base64.h>
#include <time.h>

// ---------------------------------------------------------------- pinos
static const uint8_t PIN_TRIG = 12;
static const uint8_t PIN_ECHO = 13;
static const uint8_t PIN_VBAT = 35;   // T-Beam: divisor de bateria

// ---------------------------------------------------------------- no
static const uint32_t NODE_NUM   = 0xa76c0006UL;   // !a76c0006
static const int32_t  LAT_I      = -26918700;      // graus * 1e6 (Prainha)
static const int32_t  LON_I      = -49066500;
static const float    SENSOR_ALT = 12.0f;          // altura do sensor sobre o zero da regua

// ---------------------------------------------------------------- ATG-C1
static const uint8_t  ATG_VERSION      = 1;
static const uint8_t  SENSOR_RIVER     = 1;        // config.SENSOR_CODE
static const float    SCALE_RIVER      = 100.0f;   // m -> cm

// escada oficial AlertaBlu (m): Normalidade<3 | Observacao 3-4 | Atencao 4-6
//                               | Alerta 6-8  | Alerta Maximo >8
static const float STAGE[4]        = {3.0f, 4.0f, 6.0f, 8.0f};
static const uint8_t STAGE_RISK[5] = {0, 1, 1, 2, 3};   // -> safe/attention/alert/critical

static const float RATE_ESCALATE_1 = 0.25f;   // m/h
static const float RATE_ESCALATE_2 = 0.40f;   // m/h

// intervalo de reporte por risco (ms)
static const uint32_t INTERVAL_MS[4] = {3600000UL, 900000UL, 300000UL, 60000UL};

// ---------------------------------------------------------------- estado
static float    last_level = NAN;
static uint32_t last_ms    = 0;
static uint8_t  risk       = 0;

#pragma pack(push, 1)
typedef struct {
  uint8_t  ver_type;   // versao<<4 | codigo do sensor
  uint32_t node_num;
  uint32_t ts;
  int32_t  lat_i;
  int32_t  lon_i;
  int16_t  val;        // valor * SCALE
  int16_t  rate;       // taxa por hora * 1000
  uint8_t  risk;
  uint8_t  batt;
} atg_c1_t;             // 23 bytes, little-endian (ESP32 e LE)
#pragma pack(pop)

// ---------------------------------------------------------------- sensor
// D(t) = t * Cs / 2  ;  Nivel = Dmax - D(t)      (Zakaria et al., Eq. 1 e 2)
static float readLevelMeters() {
  digitalWrite(PIN_TRIG, LOW);  delayMicroseconds(3);
  digitalWrite(PIN_TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);

  uint32_t us = pulseIn(PIN_ECHO, HIGH, 30000UL);   // timeout 30 ms (~5 m)
  if (us == 0) return NAN;                          // sem eco -> leitura invalida

  float dist_m = (us * 344.0f) / 2.0f / 1e6f;       // Cs = 344 m/s
  float level  = SENSOR_ALT - dist_m;
  if (level < -0.5f || level > 20.0f) return NAN;   // guarda de plausibilidade
  return level;
}

static uint8_t batteryPct() {
  float v = analogRead(PIN_VBAT) / 4095.0f * 2.0f * 3.3f * 1.1f;   // divisor 1:2
  int pct = (int)((v - 3.30f) / (4.20f - 3.30f) * 100.0f);
  return (uint8_t)constrain(pct, 0, 100);
}

// ---------------------------------------------------------------- risco
static uint8_t classify(float level_m, float rate_m_h) {
  uint8_t band = 0;
  for (uint8_t i = 0; i < 4; i++) if (level_m >= STAGE[i]) band = i + 1;
  int r = STAGE_RISK[band];

  if (rate_m_h >= RATE_ESCALATE_2)      r += 2;
  else if (rate_m_h >= RATE_ESCALATE_1) r += 1;

  return (uint8_t)constrain(r, 0, 3);
}

// ---------------------------------------------------------------- setup/loop
void setup() {
  Serial.begin(115200);
  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
  Serial.println(F("# ATG-P8 node | ATG-C1-BIN 23B | 915 MHz ANZ | LongFast"));
}

void loop() {
  float level = readLevelMeters();
  uint32_t now = millis();

  if (isnan(level)) {                       // erro de leitura: nao transmite
    Serial.println(F("# ERRO: leitura ultrassonica invalida (sem eco)"));
    delay(5000);
    return;
  }

  float rate = 0.0f;
  if (!isnan(last_level) && last_ms > 0) {
    float dt_h = (now - last_ms) / 3600000.0f;
    if (dt_h > 0.0001f) rate = (level - last_level) / dt_h;
  }
  risk = classify(level, rate);

  atg_c1_t p;
  p.ver_type = (uint8_t)((ATG_VERSION << 4) | SENSOR_RIVER);
  p.node_num = NODE_NUM;
  p.ts       = (uint32_t)time(nullptr);     // GPS do T-Beam ou NTP via gateway
  p.lat_i    = LAT_I;
  p.lon_i    = LON_I;
  p.val      = (int16_t)lroundf(level * SCALE_RIVER);
  p.rate     = (int16_t)constrain(lroundf(rate * 1000.0f), -32768, 32767);
  p.risk     = risk;
  p.batt     = batteryPct();

  String b64 = base64::encode((uint8_t *)&p, sizeof(p));   // 23 B -> 32 chars

  // Linha consumida pelo Serial Module do Meshtastic (mode TEXTMSG)
  Serial.println(b64);
  Serial.printf("# nivel=%.2f m  taxa=%+.3f m/h  risco=%u  bytes=%u\n",
                level, rate, risk, (unsigned)sizeof(p));

  last_level = level;
  last_ms    = now;

  delay(INTERVAL_MS[risk]);   // reporte adaptativo ao risco
}
