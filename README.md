# Telegraf DOCSIS Cable Modem Monitoring

A Docker-based monitoring stack for DOCSIS cable modem health metrics using **Telegraf** and **InfluxDB v2**.

## Overview

This project collects real-time RF signal quality, channel information, and traffic metrics from DOCSIS 3.0/3.1 cable modems via SNMP, storing them in InfluxDB v2 for visualization and analysis.

## Features

- **InfluxDB v2** — Time-series database with built-in UI
- **Telegraf** — Data collection agent with SNMP input
- **Docker Compose** — One-command setup
- **DOCSIS Metrics**:
  - IF-MIB interface traffic (octets, packets)
  - DOCSIS 3.0 downstream RF power & SNR/MER
  - DOCSIS 3.0 upstream channel tx power
  - DOCSIS 3.1 OFDM/OFDMA channels with MER/power conversion

## Prerequisites

- Docker & Docker Compose
- Network access to cable modem (SNMP v2c on port 161)
- Environment variables set in `.env`

## Quick Start

1. **Configure environment variables:**
   ```bash
   # Edit .env with your credentials
   cp .env.example .env  # (if provided, else edit .env directly)
   ```

2. **Start the stack:**
   ```bash
   docker compose up -d
   ```

3. **Access InfluxDB UI:**
   - URL: http://localhost:8086
   - Login: username/password from `.env`
   - Organization: `docsis` (default)
   - Bucket: `docsis` (default)

4. **Verify data collection:**
   ```bash
   docker logs -f telegraf
   ```

## Configuration

### InfluxDB v2 Setup
Configured automatically via Docker Compose using `DOCKER_INFLUXDB_INIT_*` environment variables:
- Initial admin user and password
- Organization name
- Bucket for time-series data
- API token for authentication

Edit `.env` to change values.

### Telegraf Configuration
- **Main config:** `telegraf.conf` — Agent settings and InfluxDB output
- **Input plugins:** `docsis_cm.conf` — SNMP configuration
   - Agent IP: `192.0.2.11:161` (example; replace with your modem IP)
  - Community: `public`
  - Polling interval: 30 seconds

#### SNMP OIDs Tracked

| Metric | OID | Unit |
|--------|-----|------|
| Interface traffic (octets/packets) | IF-MIB (1.3.6.1.2.1.31.*) | bytes/packets |
| DS RF power | 1.3.6.1.2.1.10.127.1.1.1.6 | dBmV |
| DS SNR/MER | 1.3.6.1.2.1.10.127.1.1.4.1.5 | dB |
| US tx power | 1.3.6.1.2.1.10.127.1.2.2.1.3 | dBmV |
| OFDM downstream MER | 1.3.6.1.2.1.10.166.3.1.1.4 | dB (÷10 on conversion) |
| OFDMA upstream tx power | 1.3.6.1.2.1.10.166.3.2.1.3 | dBmV (÷10 on conversion) |

### Starlark Processors
Two processors convert scaled OID values:
- `ds_ofdm_mer_x10` → `ds_ofdm_mer_db` (divide by 10)
- `us_ofdma_txpower_x10` → `us_ofdma_txpower_dbmv` (divide by 10)

## Usage

### View Metrics in InfluxDB UI

1. Go to http://localhost:8086
2. **Explore** tab → Select bucket `docsis`
3. Select measurement (e.g., `docsIfDownChannel`)
4. Select fields and apply filters

### Manage Containers

```bash
# View running containers
docker compose ps

# Stop everything
docker compose down

# Remove all data (volumes)
docker compose down -v

# Restart Telegraf
docker compose restart telegraf

# View Telegraf logs
docker logs telegraf

# View InfluxDB logs
docker logs influxdb
```

### Query Data with CLI

```bash
# List buckets
docker exec influxdb influx bucket list --token $INFLUXDB_TOKEN --org docsis

# Query data (example)
docker exec influxdb influx query \
  'from(bucket:"docsis") |> range(start: -1h) |> filter(fn: (r) => r._measurement == "docsIfDownChannel")'
```

## Troubleshooting

### Telegraf exits with code 1

**"error parsing data TOML syntax"**
- Check `docsis_cm.conf` for syntax errors
- Ensure all `[[inputs.snmp.table]]` sections are valid

**"Cannot find module / Unknown Object Identifier"**
- SNMP MIBs are missing in container
- Solution: Remove `oid = "..."` from `[[inputs.snmp.table]]` blocks (already done in this config)
- Individual field `oid` values are still used

**"Connection refused"**
- Cable modem IP/port unreachable
- Check: `ping <MODEM_IP>` and `snmpwalk -v 2c -c <COMMUNITY> <MODEM_IP> .1`

### InfluxDB exits with code 2

**Initialization failure**
- `.env` variables contain invalid characters
- Check volume permissions: `docker volume ls`
- Reset: `docker compose down -v && docker compose up`

### No data in InfluxDB

1. Verify Telegraf is running: `docker logs telegraf`
2. Check InfluxDB token matches in `.env`
3. Verify network connectivity to modem

## Environment Variables

```bash
# InfluxDB Admin (for initial setup)
INFLUXDB_ADMIN_USER=admin
INFLUXDB_ADMIN_PASSWORD=change-me-strong-password

# InfluxDB Organization & Bucket
INFLUXDB_ORG=docsis
INFLUXDB_BUCKET=docsis

# API Token (used by Telegraf)
INFLUXDB_TOKEN=replace-with-long-random-token
```

**⚠️ Change these in production!** Use strong passwords and secure token generation.

## File Structure

```
.
├── docker-compose.yml      # Service definitions
├── telegraf.conf           # Telegraf agent config & InfluxDB output
├── docsis_cm.conf          # SNMP input plugin & processors
├── .env                    # Environment variables (gitignored)
├── .gitignore              # Git exclude patterns
└── README.md               # This file
```

## License

[Your License Here]

## Support

For issues:
1. Check logs: `docker logs <service>`
2. Verify `.env` configuration
3. Test SNMP connectivity: `snmpwalk -v 2c -c public <ip> .1.3.6.1.2.1.1`
