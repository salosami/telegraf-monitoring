# Telegraf Monitoring Stack

Docker-pohjainen monitorointiprojekti, joka kerää mittareita Telegrafilla ja tallentaa ne InfluxDB v2 -tietokantaan.

Projektissa on kolme pääkäyttötapausta:

- DOCSIS-kaapelimodeemien SNMP-mittaus
- Teleste-laitteiden SNMP-mittaus
- VDM REST API -mittaus HTTP-inputilla

## Mitä tämä projekti sisältää

- InfluxDB v2 (aikasarjatietokanta)
- Telegraf (keruuagentti)
- Docker Compose -käynnistys
- Valmiit analyysiskriptit poikkeamien etsintään
- Grafana-dashboardin import-esimerkki

## Esivaatimukset

- Docker
- Docker Compose
- Verkkoyhteys mitattaviin laitteisiin (SNMP/HTTP)

## Pika-aloitus

1. Luo ympäristömuuttujatiedosto:

```bash
cp .env.example .env
```

2. Täytä vähintään nämä arvot tiedostoon `.env`:

- `INFLUXDB_ADMIN_USER`
- `INFLUXDB_ADMIN_PASSWORD`
- `INFLUXDB_ORG`
- `INFLUXDB_BUCKET`
- `INFLUXDB_TOKEN`

3. Käynnistä palvelut:

```bash
docker compose up -d
```

4. Avaa InfluxDB UI:

- URL: http://localhost:8086

## Konfiguraatiorakenne

- `telegraf.conf`
   - agentin yleiset asetukset
   - `outputs.influxdb_v2` (lukee arvot env-muuttujista)

- `telegraf.d/`
   - aktiiviset input-konfiguraatiot, jotka Telegraf lataa automaattisesti
   - tällä hetkellä mukana `vdm_restapi.conf`

- juurihakemiston mallit:
   - `docsis_cm.conf`
   - `telegraf_cm_six_modems.conf`
   - `teleste_amp.conf`

Huomio: juurihakemiston SNMP-konffeja ei lueta automaattisesti ennen kuin ne kopioidaan hakemistoon `telegraf.d/`.

## SNMP-konffin käyttöönotto

Esimerkki DOCSIS-konffin aktivoinnista:

```bash
cp docsis_cm.conf telegraf.d/docsis_cm.conf
docker compose restart telegraf
```

Vaihtoehtoisesti voit aktivoida useamman laitteen mallin:

```bash
cp telegraf_cm_six_modems.conf telegraf.d/docsis_multi.conf
docker compose restart telegraf
```

Teleste SNMP -malli:

```bash
cp teleste_amp.conf telegraf.d/teleste_amp.conf
docker compose restart telegraf
```

## VDM REST API -keruu

Tiedosto `telegraf.d/vdm_restapi.conf` sisältää kaksi `inputs.http`-inputia:

- `vdm_xp_state`
- `vdm_manager_state`

Tarkista ennen tuotantokäyttöä:

- URL-osoitteet
- Bearer-token
- TLS-asetus (`insecure_skip_verify`)

## Yleisimmät komennot

```bash
# palveluiden tila
docker compose ps

# lokit
docker logs -f telegraf
docker logs -f influxdb

# uudelleenkäynnistys
docker compose restart telegraf

# pysäytys
docker compose down

# pysäytys + datavolyymien poisto
docker compose down -v
```

## Analyysityökalut

Hakemistossa `tools/` on kaksi skriptiä:

- `influx_analyzer.py`
   - kevyempi poikkeama- ja counter-reset-analyysi
- `influx_analyzer_deep.py`
   - syvempi analyysi: kausivaihtelu, changepoint, stale-data, incident-severity

Tarkemmat käyttöohjeet:

- `tools/README_influx_analyzer.md`
- `tools/README_influx_analyzer_deep.md`

## Grafana

Hakemistossa `grafana/` on importoitava dashboard:

- `grafana/teleste_amp_existing_data_dashboard.json`

Ohjeet löytyvät tiedostosta:

- `grafana/README_Teleste_AMP_existing_data.md`

## Tietoturvahuomiot

- Älä tallenna oikeita salasanoja tai tokeneita versionhallintaan.
- Vaihda oletusarvot (`public`, esimerkkisalasanat, placeholder-tokenit) aina ennen tuotantoa.
- Suosi SNMPv3:a, jos laitteet tukevat sitä.
- `insecure_skip_verify = true` kannattaa poistaa, jos käytössä on luotettu TLS-sertifikaatti.

## Nykyinen hakemistorakenne (pääosiot)

```text
.
├── docker-compose.yml
├── telegraf.conf
├── telegraf.d/
├── docsis_cm.conf
├── telegraf_cm_six_modems.conf
├── teleste_amp.conf
├── tools/
├── grafana/
├── Teleste/
├── .env.example
└── README.md
```

## Vianhaku

- Telegraf ei käynnisty:
   - tarkista TOML-syntaksi konffeista
   - tarkista että tiedosto on hakemistossa `telegraf.d/`

- Ei dataa InfluxDB:ssä:
   - varmista että `INFLUXDB_TOKEN`, `INFLUXDB_ORG` ja `INFLUXDB_BUCKET` täsmäävät
   - varmista laitteiden verkko- ja SNMP/HTTP-yhteys
   - tarkista Telegrafin loki

- InfluxDB setup epäonnistuu:
   - tarkista `.env`-arvot
   - tarvittaessa resetoi volyymit `docker compose down -v`
