#!/usr/bin/env bash
set -euo pipefail
VERSION="${1:-$(cat VERSION)}"
docker push meir25/lse-fetcher:"$VERSION"
docker push meir25/lse-news:"$VERSION"
