#!/usr/bin/env bash
set -euo pipefail
NEW="${1:?usage: set_version.sh X.Y.Z}"
echo "$NEW" > VERSION
git add VERSION
git commit -m "chore(release): v$NEW"
git tag "v$NEW"
