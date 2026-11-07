#!/bin/sh
# Enable the repository's Git hooks for this clone only (no global Git settings are touched).
set -eu
cd "$(git rev-parse --show-toplevel)"
chmod +x .githooks/pre-commit .githooks/pre-push
git config core.hooksPath .githooks
echo "hooks enabled: $(git config --get core.hooksPath)"
echo "release policy: $(git config --get release.policyfile || echo '<not set - release checks will fail closed>')"
