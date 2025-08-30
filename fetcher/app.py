import os, sys, time, traceback
from typing import List, Dict
import pandas as pd
import yfinance as yf
from influxdb_client import InfluxDBClient, Point, WriteOptions

VERSION = "fetcher-reload-v6"
ENV_PATH = "/app/.env"

# Allowed values to keep yfinance happy
ALLOWED_PERIODS = {
    "1d","5d","1mo","3mo","6mo","1y","2y","5y","10y","ytd","max"
}
ALLOWED_INTERVALS = {
    "1m","2m","5m","15m","30m","60m","90m","1h","1d","5d","1wk","1mo","3mo"
}

# ---------- tiny .env reader (with inline-comment stripping) ----------
def _clean_value(v: str) -> str:
    # strip inline comments, quotes, and surrounding spaces
    return v.split("#", 1)[0].strip().strip('"').strip("'")

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
                    out[k.strip()] = _clean_value(v)
    except FileNotFoundError:
        pass
    return out

def merged_env() -> Dict[str, str]:
    # container env (from compose env_file) + mounted file; sanitize both
    env: Dict[str, str] = {}
    # Start with container env
    for k, v in os.environ.items():
        env[k] = _clean_value(v) if isinstance(v, str) else v
    # Overlay with file (lets you hot-edit /app/.env without recreating container)
    for k, v in read_env_file(ENV_PATH).items():
        env[k] = _clean_value(v)
    return env

def default_backfill_period(interval: str) -> str:
    i = interval.lower()
    if i == "1m":
        return "7d"
    if i in ("2m","5m","15m","30m"):
        return "60d"
    if i in ("60m","90m","1h"):
        return "2y"
    return "max"

def get_cfg() -> Dict[str, object]:
    e = merged_env()
    try:
        tickers = [t.strip() for t in e.get("TICKERS", "VOD.L,HSBA.L,BP.L").split(",") if t.strip()]
    except Exception:
        tickers = ["VOD.L","HSBA.L","BP.L"]

    yf_interval = e.get("YF_INTERVAL", "1m")
    if yf_interval not in ALLOWED_INTERVALS:
        print(f"[config] invalid YF_INTERVAL='{yf_interval}', falling back to '1m'", file=sys.stderr)
        yf_interval = "1m"

    yf_period = e.get("YF_PERIOD", "1d")
    if yf_period not in ALLOWED_PERIODS:
        # Try a sensible default for the chosen interval
        fallback = default_backfill_period(yf_interval)
        # But fallback must be a valid period literal for yfinance (ensure membership)
        yf_period = fallback if fallback in ALLOWED_PERIODS else "5d"
        print(f"[config] invalid YF_PERIOD, using '{yf_period}'", file=sys.stderr)

    backfill_on_start = str(e.get("BACKFILL_ON_START", "true")).lower() in ("1","true","yes","y")
    backfill_period = e.get("BACKFILL_PERIOD", "").strip()
    if backfill_period and backfill_period not in ALLOWED_PERIODS:
        print(f"[config] invalid BACKFILL_PERIOD='{backfill_period}', ignoring", file=sys.stderr)
        backfill_period = ""

    try:
        fetch_interval = int(str(e.get("FETCH_INTERVAL_SECONDS", "300")).strip() or "300")
    except Exception:
        fetch_interval = 300

    return {
        "tickers": tickers,
        "yf_interval": yf_interval,
        "yf_period": yf_period,
        "backfill_on_start": backfill_on_start,
        "backfill_period": backfill_period,  # optional override
        "fetch_interval": fetch_interval,
    }

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

def backfill_once(client: InfluxDBClient, tickers: List[str], yf_interval: str, backfill_period: str, org: str, bucket: str):
    try:
        period = backfill_period or default_backfill_period(yf_interval)
        if period not in ALLOWED_PERIODS:
            period = "5d"
        print(f"[backfill] start period={period} interval={yf_interval} tickers={tickers}")
        df = fetch(tickers, period, yf_interval)
        write_to_influx(df, client, bucket, org)
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
    print(f"{VERSION} | InfluxDB: {INFLUX_URL}, org={INFLUX_ORG}, bucket={INFLUX_BUCKET}, tickers={cfg0['tickers']} (hot-reload .env at {ENV_PATH})")

    with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as client:
        if cfg0["backfill_on_start"]:
            backfill_once(client, cfg0["tickers"], cfg0["yf_interval"], cfg0["backfill_period"], INFLUX_ORG, INFLUX_BUCKET)

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

