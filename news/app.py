import os, sys, time, traceback
from typing import List, Dict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, quote_plus

import feedparser
import yaml
from influxdb_client import InfluxDBClient, Point, WriteOptions
from email.utils import parsedate_to_datetime

VERSION = "news-reload-v2"
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
    env.update(os.environ)  # container env wins
    return env

def cfg_from_env(e: Dict[str, str]) -> Dict[str, object]:
    tickers = [t.strip() for t in e.get("TICKERS", "VOD.L,HSBA.L,BP.L").split(",") if t.strip()]
    return {
        "tickers": tickers,
        "backfill_on_start": e.get("NEWS_BACKFILL_ON_START", "true").lower() in ("1","true","yes","y"),
        "backfill_days": int(e.get("NEWS_BACKFILL_DAYS", "30")),
        "poll_seconds": int(e.get("NEWS_POLL_SECONDS", "900")),
        "lookback_hours": int(e.get("NEWS_LOOKBACK_HOURS", "24")),
        "require_ticker": e.get("NEWS_FILTER_REQUIRE_TICKER", "true").lower() == "true",
        "extra_keywords": [kw.strip().lower() for kw in e.get("NEWS_KEYWORDS", "").split(",") if kw.strip()],
    }

# ---- Settings / guards ----
FUTURE_CLAMP = timedelta(minutes=5)  # clamp accidental future timestamps
FEEDS_PATH = "/app/feeds.yaml"

def domain_from_url(u: str) -> str:
    try:
        return urlparse(u).netloc or ""
    except Exception:
        return ""

def load_feeds_config(tickers: List[str]) -> Dict[str, List[str]]:
    cfg: Dict[str, List[str]] = {}
    if os.path.exists(FEEDS_PATH):
        try:
            with open(FEEDS_PATH, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
                if isinstance(loaded, dict):
                    for k, v in loaded.items():
                        if isinstance(v, list):
                            cfg[str(k)] = [str(x) for x in v]
        except Exception as e:
            print(f"[news] failed to read feeds.yaml: {e}", file=sys.stderr)

    for t in tickers:
        if t not in cfg or not cfg[t]:
            cfg[t] = [f"https://news.google.com/rss/search?q={quote_plus(t)}&hl=en-GB&gl=GB&ceid=GB:en"]
    return cfg

def parse_time(entry) -> datetime:
    for key in ("published", "updated", "dc:date", "date"):
        s = entry.get(key)
        if s:
            try:
                dt = parsedate_to_datetime(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts = dt.astimezone(timezone.utc)
                break
            except Exception:
                pass
    else:
        if getattr(entry, "published_parsed", None):
            ts = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        elif getattr(entry, "updated_parsed", None):
            ts = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
        else:
            ts = datetime.now(timezone.utc)

    now_utc = datetime.now(timezone.utc)
    if ts > now_utc + FUTURE_CLAMP:
        ts = now_utc
    return ts

def fetch_news_for_ticker(ticker: str, feeds: List[str], cutoff: datetime, require_ticker: bool, extra_keywords) -> list:
    items = []
    t_lc = ticker.lower()
    for url in feeds:
        try:
            d = feedparser.parse(url)
        except Exception as e:
            print(f"[news] feed error {url}: {e}", file=sys.stderr)
            continue

        for e in d.get("entries", []):
            title = (e.get("title") or "").strip()
            summary = (e.get("summary") or e.get("description") or "").strip()
            link = e.get("link") or ""
            if not title or not link:
                continue

            ts = parse_time(e)
            if ts < cutoff:
                continue

            text_lc = (title + " " + summary).lower()
            want = True
            if require_ticker:
                want = t_lc in text_lc
            if extra_keywords:
                want = want or any(kw in text_lc for kw in extra_keywords)
            if not want:
                continue

            source = ""
            try:
                src = e.get("source")
                if isinstance(src, dict):
                    source = (src.get("title") or "").strip()
            except Exception:
                pass
            if not source:
                source = domain_from_url(link)

            items.append({
                "ticker": ticker,
                "title": title,
                "summary": summary[:800],
                "url": link,
                "source": source,
                "time": ts
            })
    print(f"[news] matched {len(items)} items for {ticker} since {cutoff.isoformat()}")
    return items

def write_news(items, client: InfluxDBClient, bucket: str, org: str):
    if not items:
        print("[influx] news: nothing new")
        return
    write_api = client.write_api(write_options=WriteOptions(batch_size=500, flush_interval=5_000, jitter_interval=1_000))
    seen = set()
    points = []
    for it in items:
        key = (it["ticker"], it["url"])
        if key in seen:
            continue
        seen.add(key)
        p = (Point("lse_news")
             .tag("ticker", it["ticker"])
             .tag("source", it["source"] or "")
             .field("title", it["title"])
             .field("summary", it["summary"])
             .field("url", it["url"])
             .time(it["time"]))
        points.append(p)
    try:
        write_api.write(bucket=bucket, org=org, record=points)
        print(f"[influx] wrote {len(points)} news points")
    except Exception as e:
        print(f"[influx] write news error: {e}", file=sys.stderr)

def backfill_once(client: InfluxDBClient, tickers: List[str], feeds_cfg: Dict[str, List[str]], backfill_days: int, require_ticker: bool, extra_keywords):
    cutoff = datetime.now(timezone.utc) - timedelta(days=backfill_days)
    print(f"[backfill] start days={backfill_days} (cutoff={cutoff.isoformat()})")
    all_items = []
    for t in tickers:
        feeds = feeds_cfg.get(t, [])
        all_items.extend(fetch_news_for_ticker(t, feeds, cutoff, require_ticker, extra_keywords))
    write_news(all_items, client, os.getenv("INFLUX_BUCKET", "lse"), os.getenv("INFLUX_ORG", "stocks"))
    print("[backfill] done")

def main():
    INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
    INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
    INFLUX_ORG = os.getenv("INFLUX_ORG", "stocks")
    INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "lse")

    e0 = merged_env()
    cfg0 = cfg_from_env(e0)
    print(f"{VERSION} | InfluxDB: {INFLUX_URL}, org={INFLUX_ORG}, bucket={INFLUX_BUCKET}, tickers={cfg0['tickers']} (.env hot-reload at {ENV_PATH})")

    feeds_cfg = load_feeds_config(cfg0["tickers"])
    with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as client:
        if cfg0["backfill_on_start"]:
            backfill_once(client, cfg0["tickers"], feeds_cfg, cfg0["backfill_days"], cfg0["require_ticker"], cfg0["extra_keywords"])

        last_cfg = cfg0
        while True:
            try:
                e = merged_env()
                cfg = cfg_from_env(e)
                if cfg != last_cfg:
                    print(f"[config] reloaded: {cfg}")
                    if cfg["tickers"] != last_cfg["tickers"]:
                        feeds_cfg = load_feeds_config(cfg["tickers"])
                    last_cfg = cfg

                cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg["lookback_hours"])
                all_items = []
                for t in cfg["tickers"]:
                    feeds = feeds_cfg.get(t, [])
                    all_items.extend(fetch_news_for_ticker(t, feeds, cutoff, cfg["require_ticker"], cfg["extra_keywords"]))
                write_news(all_items, client, INFLUX_BUCKET, INFLUX_ORG)
            except Exception as e:
                print(f"[loop] news error: {e}", file=sys.stderr)
                traceback.print_exc()
            time.sleep(cfg["poll_seconds"])

if __name__ == "__main__":
    main()
