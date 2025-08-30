# set env from your .env (edit the TOKEN if you changed it)
export ORG="stocks"
export BUCKET="lse"
export TOKEN="devtoken-please-change-me"

# choose a ticker that’s on your dashboard
export TICKER="VOD.L"
export SOURCE="Manual"
export TITLE='Manual test: VOD spike'
export SUMMARY='Smoke test for Grafana annotations.'
export URL='https://example.com/test'

# write with current timestamp (precision = seconds)
TS=$(date -u +%s)
printf 'lse_news,ticker=%s,source=%s title="%s",summary="%s",url="%s" %s\n' \
  "$TICKER" "$SOURCE" "$TITLE" "$SUMMARY" "$URL" "$TS" > /tmp/news.lp

curl -s -i -XPOST "http://localhost:8086/api/v2/write?org=$ORG&bucket=$BUCKET&precision=s" \
  -H "Authorization: Token $TOKEN" \
  --data-binary @/tmp/news.lp

