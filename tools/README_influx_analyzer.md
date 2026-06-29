# Influx Analyzer (quick)

Työkalu analysoi InfluxDB v2 -aikasarjoja ja raportoi:

- poikkeamat uusimmassa arvossa (IQR + robust z-score)
- counter-kenttien resetit
- counter-kenttien epäilyttävät increment-spiket

Skripti: `tools/influx_analyzer.py`

## Suoritus suoraan parametreilla

```bash
python3 tools/influx_analyzer.py \
  --url http://172.30.22.13:8086 \
  --org my-org \
  --token my-influxdb-token \
  --bucket snmp \
  --lookback 96h \
  --output-json report.json
```

## Suoritus ympäristömuuttujilla

```bash
export INFLUX_URL=http://YOUR-INFLUX:8086
export INFLUX_ORG=YOUR_ORG
export INFLUX_TOKEN=YOUR_TOKEN
export INFLUX_BUCKET=snmp

python3 tools/influx_analyzer.py --lookback 24h
```

## Hyödyllisiä rajauksia

Vain tietyt measurementit:

```bash
python3 tools/influx_analyzer.py \
  --url http://YOUR-INFLUX:8086 \
  --org YOUR_ORG \
  --token YOUR_TOKEN \
  --bucket snmp \
  --measurements teleste_amp,docsis_cm,ifXTable
```

Vain tietyt kentät:

```bash
python3 tools/influx_analyzer.py \
  --url http://YOUR-INFLUX:8086 \
  --org YOUR_ORG \
  --token YOUR_TOKEN \
  --bucket snmp \
  --fields modem_rx_level,modem_tx_level,ifHCInOctets
```

Pakota counter-kentät:

```bash
python3 tools/influx_analyzer.py \
  --url http://YOUR-INFLUX:8086 \
  --org YOUR_ORG \
  --token YOUR_TOKEN \
  --bucket snmp \
  --counter-fields ifHCInOctets,ifHCOutOctets,ifHCInUcastPkts,ifHCOutUcastPkts
```

## Tärkeimmät asetukset

- `--min-points` (oletus 12)
- `--robust-z-threshold` (oletus 3.5)
- `--spike-sigma` (oletus 4.0)
- `--max-series` (0 = ei rajoitusta)

## Tulosteet

Konsoliyhteenveto näyttää mm.:

- rivien määrä
- analysoitujen sarjojen määrä
- poikkeamien määrä
- kriittisiksi luokitellut löydökset

Valinnainen JSON-raportti (`--output-json`) sisältää kaikki analysoidut sarjat ja syykoodit.

## Huomiot

- Device tunnistetaan oletuksena tageista järjestyksessä:
  `agent_host,host,device,source,instance`
- Järjestyksen voi muuttaa parametrilla `--device-tags`.
- Jos saat paljon `too_few_points`, kasvata `--lookback`.
