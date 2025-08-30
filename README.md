# LSE & NASDAQ Prices + News (InfluxDB + Grafana + Docker)

A batteries‑included stack that pulls **stock prices** (LSE + US), ingests **news headlines**, stores everything in **InfluxDB 2.x**, and visualizes it in **Grafana** with **clickable news dots** on the price chart and an **articles table**—all in **UTC**.

> LSE symbols must end with **`.L`** (e.g., `VOD.L`, `HSBA.L`, `BP.L`). US tickers are plain (e.g., `AAPL`, `MSFT`, `GOOGL`).

---

## Architecture

```
+---------------------------+            +-------------------+
|        fetcher (py)       |  prices    |                   |
|  yfinance -> lse_prices   +----------->+    InfluxDB 2.x   |
|  UTC, tags: ticker,...    |            |   bucket: lse     |
+---------------------------+            |  org: stocks      |
                                         |  auth: token      |
+---------------------------+            +---------+---------+
|         news (py)         |   news              |
|  RSS -> lse_news          +---------------------+
|  UTC, title/url/summary   |                     v
+---------------------------+               +-------------+
                                            |   Grafana   |
                                            | dashboards  |
                                            |  - Prices   |
                                            |  - Articles |
                                            +-------------+
```

- **fetcher**: polls yfinance for OHLCV; one‑time backfill on start; hot‑reloads config.
- **news**: parses RSS (Google News per ticker by default), stores `title`, `summary`, `url`; hot‑reloads config.
- **Grafana**: provisioned dashboards with a **ticker dropdown**, **resolution dropdown**, **news annotations** (dots) and an **Articles** table (with clickable link column).

---

## Repo layout

```
project/
├─ docker-compose.yml
├─ app.env                         # single source of runtime config (contains secrets)  ← keep out of git
├─ feeds.yaml                      # optional: custom feeds per ticker
├─ grafana/
│  └─ provisioning/
│     ├─ dashboards/
│     │  ├─ dashboards.yml         # provider (allowUiUpdates: true)
│     │  ├─ lse_prices.json        # price chart + news dots
│     │  └─ lse_articles.json      # articles table
│     └─ datasources/
│        └─ datasource.yml         # InfluxDB (Flux) datasource, uid: influxdb-flux
├─ fetcher/
│  ├─ Dockerfile
│  ├─ requirements.txt
│  └─ app.py                       # fetcher-reload-v6 (comment-safe, validated config)
└─ news/
   ├─ Dockerfile
   ├─ requirements.txt
   └─ app.py                       # backfill + polling, UTC clamp
```

---

## Configuration (single file: `app.env`)

`app.env` is used **two ways**:
1) `env_file: app.env` loads vars into containers at start.
2) It’s also **bind‑mounted** into `fetcher` and `news` at `/app/.env`, which those apps **re-read every loop** (hot‑reload).

> Fetcher v6 strips **inline comments** and validates values, so `YF_PERIOD=5d # note` works. For Influx/Grafana init vars, keep clean values.

**Example `app.env`** (adjust to your needs):
```env
# ------------ InfluxDB / Grafana ------------
INFLUXDB_ORG=stocks
INFLUXDB_BUCKET=lse
INFLUXDB_ADMIN_USER=admin
INFLUXDB_ADMIN_PASSWORD=CHANGE_ME
INFLUXDB_ADMIN_TOKEN=CHANGE_ME
DOCKER_INFLUXDB_INIT_MODE=setup
DOCKER_INFLUXDB_INIT_USERNAME=${INFLUXDB_ADMIN_USER}
DOCKER_INFLUXDB_INIT_PASSWORD=${INFLUXDB_ADMIN_PASSWORD}
DOCKER_INFLUXDB_INIT_ORG=${INFLUXDB_ORG}
DOCKER_INFLUXDB_INIT_BUCKET=${INFLUXDB_BUCKET}
DOCKER_INFLUXDB_INIT_ADMIN_TOKEN=${INFLUXDB_ADMIN_TOKEN}
GF_SECURITY_ADMIN_USER=admin
GF_SECURITY_ADMIN_PASSWORD=CHANGE_ME

# ------------ Fetcher (prices) --------------
TICKERS=VOD.L,HSBA.L,BP.L,AAPL,MSFT,GOOGL
FETCH_INTERVAL_SECONDS=60
YF_INTERVAL=1m             # allowed: 1m,2m,5m,15m,30m,60m,90m,1h,1d,5d,1wk,1mo,3mo
YF_PERIOD=5d               # allowed: 1d,5d,1mo,3mo,6mo,1y,2y,5y,10y,ytd,max
BACKFILL_ON_START=true
BACKFILL_PERIOD=7d         # optional override (same allowed set as YF_PERIOD)

# --------------- News (RSS) -----------------
NEWS_BACKFILL_ON_START=true
NEWS_BACKFILL_DAYS=30
NEWS_POLL_SECONDS=900
NEWS_LOOKBACK_HOURS=24
NEWS_FILTER_REQUIRE_TICKER=false
NEWS_KEYWORDS=HSBC,Vodafone,BP,Apple,Microsoft,Alphabet
```

> For **1m** bars, Yahoo typically limits history to ~7 days, so `YF_PERIOD=5d`–`7d` is practical.

---

## Docker Compose

Bring everything up:
```bash
docker compose up -d --build
```

Stop:
```bash
docker compose down
```

Recreate just the apps (optional nudge; both already hot‑reload `/app/.env`):
```bash
docker compose up -d --force-recreate fetcher news
```

Verify `app.env` is mounted:
```bash
docker exec -it lse-fetcher head -n 12 /app/.env
docker exec -it lse-news    head -n 12 /app/.env
```

---

## Grafana dashboards

### Prices (`grafana/provisioning/dashboards/lse_prices.json`)
- Variables:
  - `ticker` (multi) — from Influx tag values
  - `every` (custom) — `3s,5s,10s,1m,5m` aggregate window
- Close query (Flux, simplified):
  ```flux
  from(bucket: v.bucket)
    |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
    |> filter(fn: (r) => r._measurement == "lse_prices")
    |> filter(fn: (r) => r._field == "close")
    |> filter(fn: (r) => r.ticker =~ /^${ticker:regex}$/)
    |> aggregateWindow(every: duration(v: "${every}"), fn: mean, createEmpty: false)
    |> yield(name: "mean")
  ```
- **News annotations (clickable)**:
  ```flux
  from(bucket: v.bucket)
    |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
    |> filter(fn: (r) => r._measurement == "lse_news")
    |> filter(fn: (r) => r.ticker =~ /^${ticker:regex}$/)
    |> pivot(rowKey: ["_time","ticker","source"], columnKey: ["_field"], valueColumn: "_value")
    |> map(fn:(r) => ({ time: r._time, text: "[" + string(v: r.title) + "](" + string(v: r.url) + ")" }))
    |> keep(columns: ["time","text"])
  ```

### Articles (`grafana/provisioning/dashboards/lse_articles.json`)
- Variables: `ticker` (multi), `source` (multi, All), `q` (textbox search).
- Table query:
  ```flux
  import "strings"
  q = "${q}"
  from(bucket: v.bucket)
    |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
    |> filter(fn: (r) => r._measurement == "lse_news")
    |> filter(fn: (r) => r.ticker =~ /^${ticker:regex}$/)
    |> filter(fn: (r) => r.source =~ /^${source:regex}$/)
    |> pivot(rowKey: ["_time","ticker","source"], columnKey: ["_field"], valueColumn: "_value")
    |> map(fn: (r) => ({ r with _title_lc: strings.toLower(v: string(v: r.title)), _summary_lc: strings.toLower(v: string(v: r.summary)) }))
    |> filter(fn: (r) => q == "" or strings.containsStr(v: r._title_lc, substr: strings.toLower(v: q)) or strings.containsStr(v: r._summary_lc, substr: strings.toLower(v: q)))
    |> keep(columns: ["_time","title","url"])
    |> sort(columns: ["_time"], desc: true)
  ```
- **URL column** is clickable via panel field override: map column name `url` to a Link (`targetBlank: true`).

---

## Small tweaks we made (so you don’t forget)

- **Fetcher v6**: hot‑reloads `/app/.env`; **strips inline comments**; validates `YF_PERIOD`/`YF_INTERVAL`; adds `exchange` tag; ensures **UTC** timestamps.
- **News**: backfill on start; **clamps future timestamps** (now+5m max); matches on **title + summary**; optional extra keyword OR‑match.
- **Grafana**:
  - **Annotations** use Markdown link text (`[title](url)`), so clicking a dot opens the article.
  - Added **Articles** dashboard with **Link** column.
  - Fixed legacy templating issue by using Flux and the Influx datasource (`uid: influxdb-flux`).
  - Enabled saving via `allowUiUpdates: true` and ensured files writable by `uid 472`.
- **Compose**: `app.env` used for all services; additionally bind‑mounted to `/app/.env` for fetcher/news hot‑reload.
- **Influx delete**: run two delete commands (no `OR` in predicate).

---

## Common operations

### Add tickers (incl. NASDAQ)
1) Edit `app.env` → `TICKERS=...`
2) Save (hot‑reload), or nudge: `docker compose up -d --force-recreate fetcher news`
3) Watch logs for backfill messages.

### Manual test article
```bash
set -a; source app.env; set +a
TS=$(date -u +%s)
LP=$(printf 'lse_news,ticker=%s,source=%s title="%s",summary="%s",url="%s" %s\n' \
  "VOD.L" "Manual" "Manual test: VOD spike" "Smoke test" "https://example.com/vod" "$TS")
curl -i -XPOST "http://localhost:8086/api/v2/write?org=$INFLUXDB_ORG&bucket=$INFLUXDB_BUCKET&precision=s" \
  -H "Authorization: Token $INFLUXDB_ADMIN_TOKEN" --data-binary "$LP"
```

### Health checks
```bash
curl -s http://localhost:8086/health | jq .      # Influx
curl -s http://localhost:3000/api/health | jq .  # Grafana
```

### Influx quick queries
```bash
# Last close per ticker (7d)
set -a; source app.env; set +a
curl -sS -H "Authorization: Token $INFLUXDB_ADMIN_TOKEN" \
  -H "Accept: application/csv" -H "Content-Type: application/vnd.flux" \
  -X POST "http://localhost:8086/api/v2/query?org=$INFLUXDB_ORG" \
  --data-binary $'from(bucket: "lse")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "lse_prices" and r._field == "close")
  |> group(columns: ["ticker"]) |> last()
  |> keep(columns: ["_time","ticker","_value"])'
```

### Delete measurement data
```bash
docker exec -it influxdb influx delete \
  --org "$DOCKER_INFLUXDB_INIT_ORG" \
  --token "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" \
  --bucket lse \
  --start 1970-01-01T00:00:00Z \
  --stop 2100-01-01T00:00:00Z \
  --predicate '_measurement="lse_prices"'

docker exec -it influxdb influx delete \
  --org "$DOCKER_INFLUXDB_INIT_ORG" \
  --token "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" \
  --bucket lse \
  --start 1970-01-01T00:00:00Z \
  --stop 2100-01-01T00:00:00Z \
  --predicate '_measurement="lse_news"'
```

---

## Git ignore (recommended)

```
app.env
influx-data/
influx-config/
grafana-data/
__pycache__/
*.pyc
*.log
```

If you ever committed secrets, **rotate** tokens/passwords immediately.

---

## License

Private/internal project. Add an explicit license if sharing publicly.
