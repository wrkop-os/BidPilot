#!/usr/bin/env bash
# SessionStart provisioning for Claude Code web/remote sessions.
# Everything is conditional so warm sessions cost ~nothing; cold sessions
# get the deps this repo's tests need (the ones that had to be hand-fixed
# before this hook existed: python extras, cffi, libreoffice-writer).
set -u
cd "$(dirname "$0")/.." || exit 0

python -c "import bidpilot, fastapi, pdfplumber, sklearn" 2>/dev/null || \
    pip install -q -e ".[dev,tables,server,ml]" 2>&1 | tail -1

# pypdf's crypto path needs a working cffi backend (broken system pairing
# observed in fresh containers).
python -c "import _cffi_backend" 2>/dev/null || pip install -q cffi 2>&1 | tail -1

# Exact page counts need the LibreOffice writer filter; skip silently where
# apt is unavailable or we lack rights (tests degrade gracefully without it).
if ! python -c "from bidpilot.rendering import soffice_conversion_works as w; import sys; sys.exit(0 if w() else 1)" 2>/dev/null; then
    if command -v apt-get >/dev/null 2>&1 && [ "$(id -u)" = "0" ]; then
        (apt-get update -qq && apt-get install -y -qq libreoffice-writer) >/dev/null 2>&1 || true
    fi
fi

echo "bidpilot session setup: $(python -m pytest --collect-only -q 2>/dev/null | tail -1 || echo 'collection failed')"
