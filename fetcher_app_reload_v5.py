import os, sys, time, traceback
from typing import List, Dict
import pandas as pd
import yfinance as yf
from influxdb_client import InfluxDBClient, Point, WriteOptions

VERSION = "fetcher-reload-v5"
ENV_PATH = "/app/.env"

# ------------- tiny .env reader (no extra deps) -------------
def read_env_file(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "=" in s:
                    k, v = s.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    out[k] = v
    except FileNotFoundError:
        pass
    return out

def merged_env() -> Dict[str, str]:
    env = read_env_file(ENV_PATH)
    # container env wins over file
    env.update(os.environ)
    return env

def get_cfg() -> Dict[str, object]:
    e = merged_env()
    # Influx connection comes from container env; do not hot-reload URL/token/org/bucket here.
    tickers = [t.strip() for t in e.get("TICKERS", "VOD.L,HSBA.L,BP.L").split(",") if t.strip()]
    cfg = {
        "tickers": tickers,
        "fetch_interval": int(e.get("FETCH_INTERVAL_SECONDS", "300")),
        "yf_interval": e.get("YF_INTERVAL", "1m"),
        "yf_period": e.get("YF_PERIOD", "1d"),
        "backfill_on_start": e.get("BACKFILL_ON_START", "true").lower() in ("1","true","yes","y"),
        "backfill_period": e.get("BACKFILL_PERIOD", "").strip(),  # optional override
    }
    return cfg

# -------------------- helpers --------------------
def ensure_utc(ts):
    return pd.to_datetime(ts, utc=True).to_pydatetime()

def normalize_datetime(df: pd.DataFrame) -> pd.DataFrame:
    df = df.reset_index()
    if 'Date' in df.columns:
        df = df.rename(columns={'Date': 'datetime'})
    elif 'Datetime' in df.columns:
        df = df.rename(columns={'Datetime': 'datetime'})
    elif 'index' in df.columns:
        df = df.rename(columns={'index': 'datetime'})
    df['datetime'] = pd.to_datetime(df['datetime'], utc=True)
    return df

def default_backfill_period(interval: str) -> str:
    i = interval.lower()
    if i == "1m":
        return "7d"
    if i in ("2m","5m","15m","30m"):
        return "60d"
    if i in ("60m","90m","1h"):
        return "2y"
    return "max"

def fetch(tickers: List[str], period: str, interval: str) -> pd.DataFrame:
    data = yf.download(
        tickers=tickers,
        period=period,
        interval=interval,
        group_by='ticker',
        auto_adjust=False,
        progress=False,
        threads=True,
    )
    frames = []
    for t in tickers:
        if isinstance(data.columns, pd.MultiIndex):
            if t in data.columns.get_level_values(0):
                df_t = data[t].copy()
            else:
                print(f"[fetch] No data for {t}", file=sys.stderr)
                continue
        else:
            df_t = data.copy()

        if df_t is None or df_t.empty:
            print(f"[fetch] Empty df for {t}", file=sys.stderr)
            continue

        df_t = normalize_datetime(df_t).rename(columns={
            'Open': 'open', 'High': 'high', 'Low': 'low',
            'Close': 'close', 'Adj Close': 'adj_close', 'Volume': 'volume',
        })
        df_t['ticker'] = t

        currency = ''
        try:
            info = yf.Ticker(t).fast_info
            currency = getattr(info, 'currency', None) or (info.get('currency') if isinstance(info, dict) else None) or ''
        except Exception:
            pass
        df_t['currency'] = currency
        df_t = df_t[['ticker','datetime','open','high','low','close','adj_close','volume','currency']]
        frames.append(df_t)

    if not frames:
        return pd.DataFrame(columns=['ticker','datetime','open','high','low','close','adj_close','volume','currency'])
    return pd.concat(frames, ignore_index=True).sort_values(['ticker','datetime'])

def write_to_influx(df: pd.DataFrame, client: InfluxDBClient, bucket: str, org: str):
    if df.empty:
        print("[influx] nothing to write")
        return
    write_api = client.write_api(write_options=WriteOptions(batch_size=500, flush_interval=5_000, jitter_interval=1_000))
    points = []
    for _, row in df.iterrows():
        ts = ensure_utc(row["datetime"])
        ticker = row["ticker"]
        exchange = "LSE" if ticker.endswith(".L") else "US"
        p = Point("lse_prices").tag("ticker", ticker).tag("exchange", exchange).tag("currency", row.get("currency") or "")
        if pd.notna(row["open"]):      p = p.field("open", float(row["open"]))
        if pd.notna(row["high"]):      p = p.field("high", float(row["high"]))
        if pd.notna(row["low"]):       p = p.field("low",  float(row["low"]))
        if pd.notna(row["close"]):     p = p.field("close", float(row["close"]))
        if pd.notna(row["adj_close"]): p = p.field("adj_close", float(row["adj_close"]))
        if pd.notna(row["volume"]):    p = p.field("volume", int(row["volume"]))
        p = p.time(ts)
        points.append(p)
    try:
        write_api.write(bucket=bucket, org=org, record=points)
        print(f"[influx] wrote {len(points)} points")
    except Exception as e:
        print(f"[influx] write error: {e}", file=sys.stderr)

def backfill_once(client: InfluxDBClient, tickers: List[str], yf_interval: str, backfill_period: str):
    try:
        period = backfill_period or default_backfill_period(yf_interval)
        print(f"[backfill] start period={period} interval={yf_interval} tickers={tickers}")
        df = fetch(tickers, period, yf_interval)
        write_to_influx(df, client, os.getenv("INFLUX_BUCKET", "lse"), os.getenv("INFLUX_ORG", "stocks"))
        print("[backfill] done")
    except Exception as e:
        print(f"[backfill] error: {e}", file=sys.stderr)
        traceback.print_exc()

def main():
    INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
    INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
    INFLUX_ORG = os.getenv("INFLUX_ORG", "stocks")
    INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "lse")

    cfg0 = get_cfg()
    print(f"{VERSION} | InfluxDB: {INFLUX_URL}, org={INFLUX_ORG}, bucket={INFLUX_BUCKET}, tickers={cfg0['tickers']} (hot-reload .env mounted at {ENV_PATH})")

    with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as client:
        # one-time backfill
        if cfg0["backfill_on_start"]:
            backfill_once(client, cfg0["tickers"], cfg0["yf_interval"], cfg0["backfill_period"])

        last_cfg = cfg0
        while True:
            try:
                cfg = get_cfg()
                if cfg != last_cfg:
                    print(f"[config] reloaded: {cfg}")
                    last_cfg = cfg
                df = fetch(cfg["tickers"], cfg["yf_period"], cfg["yf_interval"])
                if not df.empty:
                    cutoff = pd.Timestamp.now(tz='UTC') - pd.Timedelta(minutes=30)
                    df = df[df["datetime"] >= cutoff]
                write_to_influx(df, client, INFLUX_BUCKET, INFLUX_ORG)
            except Exception as e:
                print(f"[loop] error: {e}", file=sys.stderr)
                traceback.print_exc()
            time.sleep(cfg["fetch_interval"])

if __name__ == "__main__":
    main()
