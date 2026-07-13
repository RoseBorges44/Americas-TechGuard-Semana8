# Integração com hardware (Trilha A) — o que falta e como fazer

Esta entrega foi feita pela **Trilha B (software-only / simulação)**: estou em
Florianópolis e o hardware está sob guarda do prof. Lucas, em Joinville. Ainda
assim, todo o software foi escrito **para ser conectado ao hardware sem
reescrita**. Esta pasta documenta exatamente o que ligar onde.

## 1. O que já é compatível byte a byte

`atg_node.ino` monta a struct `atg_c1_t` (23 bytes, little-endian) com o
**mesmo layout** do `struct.pack("<BIIiihhBB", ...)` de `src/atg_mesh/codec.py`.
Ou seja: o payload produzido pelo ESP32 é decodificado pelo `from_c1_bin()`
Python sem nenhuma adaptação. Isso pode ser conferido sem placa nenhuma:

```bash
python -c "
import sys; sys.path.insert(0,'src')
from atg_mesh.codec import from_c1_bin
print(from_c1_bin(bytes.fromhex('11060073a7e09d536ad44065fefc4d13fd820218010257')))"
```

## 2. Ligação do sensor (nó do rio)

| HC-SR04 | ESP32 (T-Beam V1.1 / Heltec V3) |
|---|---|
| VCC   | 5 V |
| GND   | GND |
| TRIG  | GPIO 12 |
| ECHO  | GPIO 13 (divisor 5 V → 3,3 V: 1 kΩ / 2 kΩ) |

Alimentação: LiPo 3,7 V (2000–3000 mAh) ou 18650 (3000–3400 mAh), como na lista
do enunciado. Antena 5 dBi SMA 915 MHz nos nós de campo; 8 dBi no repetidor de
ponto elevado (Morro do Aipim) e no gateway.

## 3. Rádio

```bash
./meshtastic_cli_config.sh /dev/ttyUSB0 ATG-BLU-06 06
```

Pontos que valem atenção:

- **Região `ANZ`, não `BR_902`.** A tabela oficial *LoRa Region by Country* do
  Meshtastic lista o Brasil como `ANZ | BR_902`. `BR_902` opera em
  **902–907,5 MHz**; a atividade exige **915 MHz obrigatório**, faixa coberta
  por `ANZ` (915–928 MHz). Nós com regiões diferentes não se falam.
- **Preset `LONG_FAST`** = BW 250 kHz, SF 11, CR 4/5 (documentação oficial).
  Todos os nós precisam do **mesmo preset**.
- **`mqtt.json_enabled true`** não funciona em plataformas nRF52 — nas três
  placas da atividade (todas ESP32) funciona.
- O **downlink** JSON só entra na malha se existir um canal chamado
  literalmente **`mqtt`**, com *downlink* habilitado.

## 4. Como o payload chega à malha

Duas opções, ambas previstas no firmware:

**(a) Serial Module** — o ESP32 sensor imprime o ATG-C1 em Base64 (32 chars) na
UART; o nó Meshtastic vizinho, com `serial.mode TEXTMSG`, publica isso como
`TEXT_MESSAGE_APP`. Zero modificação de firmware Meshtastic.

**(b) PortNum privado** — enviar os 23 bytes crus em `PRIVATE_APP` (256) usando
a API Python/C++ do Meshtastic. Mais limpo, exige firmware customizado.

## 5. Testes que eu faria em Joinville (roteiro pronto)

1. **Bancada:** dois nós na mesa, `--info`, confirmar região/preset/canal;
   enviar um ATG-C1-B64 e conferir no app Meshtastic do celular.
2. **Range test:** módulo *Range Test* do Meshtastic, caminhada de 100 em 100 m,
   registrando RSSI/SNR/PDR — exatamente o protocolo de Zakaria et al. (2023),
   que varreu 100–600 m com SF7 e SF12.
3. **Multi-hop:** afastar o nó de origem até perder o enlace direto e verificar
   se o repetidor mantém a entrega (é a hipótese que a simulação quantifica:
   PDR de 54,2% → 100% no nó mais distante).
4. **Duty cycle real:** medir o *airtime* reportado pelo firmware e comparar com
   `lora.time_on_air_s()` (previsão: 0,559 s por pacote de 23 B em LongFast).
5. **Bateria:** logar `battery_level` da telemetria por 72 h com o reporte
   adaptativo ligado.
