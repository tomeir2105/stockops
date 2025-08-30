#!/usr/bin/env bash
set -euo pipefail
VERSION="${1:-$(cat VERSION)}"
docker build -t meir25/lse-fetcher:"$VERSION" fetcher
docker build -t meir25/lse-news:"$VERSION" news
