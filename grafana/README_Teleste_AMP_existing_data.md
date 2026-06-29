# Teleste AMP thermal-cycle dashboard (existing data)

Dashboard käyttää InfluxDB:ssä jo olevaa dataa eikä vaadi erillistä keruumuutosta, kunhan mittarit ovat saatavilla bucketissa.

## Importointi

1. Avaa Grafana.
2. Siirry kohtaan Dashboards -> New -> Import.
3. Tuo tiedosto `grafana/teleste_amp_existing_data_dashboard.json`.
4. Valitse InfluxDB (Flux) -datasource.
5. Tallenna ja avaa dashboard.

## Oletukset

- datasource tukee Flux-kyselyitä
- bucket löytyy (oletus `snmp`)
- datassa on laitetagi, jota dashboard käyttää suodatukseen

## Muuttujat

- `bucket`: Influx-bucket
- `device`: laitetagi (oletus `agent_host`)
- `metric`: ranking-paneelin metriikka

## Näkymät

1. RX/TX-tasot valituille laitteille
2. Top-laitteet valitun metriikan mukaan (24h)
3. Hälytys/status-trendi tunti-ikkunoissa
4. Päiväsyklin vertailu (nykyinen vs edellinen päivä)
5. Viimeisimmät avainarvot laitekohtaisesti

## Sovitus omaan dataan

- Jos datassa ei ole `agent_host`-tagia, vaihda `device`-muuttujan lähde oikeaan tagiin (esim. `device`, `host` tai `source`).
- Päivitä paneelien filterit vastaamaan käytössä olevia measurementeja/fieldejä.
- Jos data tulee harvalla intervallilla (esim. 15m), kasvata panelin aikajännettä tarvittaessa.
