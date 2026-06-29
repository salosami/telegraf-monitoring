# Influx Analyzer Deep v2

Syväanalyysityökalu InfluxDB v2 -datalle:

- kausiperusteinen baseline (hour-of-week)
- counter unwrap + rate-analyysi
- changepoint-havainto
- stale-datan tunnistus
- incident-scoring + severity-luokittelu

Skripti: `tools/influx_analyzer_deep.py`

## Pikaesimerkki

```bash
python3 tools/influx_analyzer_deep.py \
  --url http://172.30.22.13:8086 \
  --org my-org \
  --token my-influxdb-token \
  --bucket snmp \
  --lookback 14d \
  --output-json report4.json
```

## Ympäristömuuttujatila

```bash
export INFLUX_URL=http://YOUR-INFLUX:8086
export INFLUX_ORG=YOUR_ORG
export INFLUX_TOKEN=YOUR_TOKEN
export INFLUX_BUCKET=snmp

python3 tools/influx_analyzer_deep.py --lookback 14d
```

## Rajatut analyysit

Vain valitut measurementit:

```bash
python3 tools/influx_analyzer_deep.py \
  --url http://YOUR-INFLUX:8086 \
  --org YOUR_ORG \
  --token YOUR_TOKEN \
  --bucket snmp \
  --measurements teleste_amp,telesteLevelDetector,ifXTable,docsis_cm,vdm_xp_state
```

Kynnysarvojen säätö:

```bash
python3 tools/influx_analyzer_deep.py \
  --url http://YOUR-INFLUX:8086 \
  --org YOUR_ORG \
  --token YOUR_TOKEN \
  --bucket snmp \
  --seasonal-z-threshold 2.8 \
  --changepoint-sigma 2.5 \
  --stale-multiplier 4
```

Pakota counter-kentät:

```bash
python3 tools/influx_analyzer_deep.py \
  --url http://YOUR-INFLUX:8086 \
  --org YOUR_ORG \
  --token YOUR_TOKEN \
  --bucket snmp \
  --counter-fields ifHCInOctets,ifHCOutOctets,ifHCInUcastPkts,ifHCOutUcastPkts,repliedPackets,unrepliedPackets
```

## Tärkeimmät parametrit

- `--min-points` (oletus 36)
- `--seasonal-z-threshold` (oletus 3.0)
- `--changepoint-sigma` (oletus 3.0)
- `--stale-multiplier` (oletus 3.0)
- `--top` (oletus 30)

## Tulosteet

Konsoliyhteenveto sisältää:

- luettujen rivien määrä
- analysoitujen sarjojen määrä
- incidentit severityittäin (`critical`, `high`, `medium`, `low`)
- top-lista syykoodien kanssa

JSON-raportti (`--output-json`) sisältää täydet incident-rivit analyysimetrikoineen.

## Tulkinta

Yleisiä syykoodeja:

- `hard_drop_to_zero`
- `outside_iqr_bounds`
- `seasonal_deviation`
- `counter_reset_detected`
- `counter_wrap_detected`
- `stale_data`

## Huomiot

- Device tunniste valitaan oletuksena tageista järjestyksessä:
  `agent_host,host,device,source,instance`
- Järjestystä voi muuttaa parametrilla `--device-tags`.
- Kausianalyysi toimii parhaiten vähintään 7 päivän datalla (suositus 14 päivää).
