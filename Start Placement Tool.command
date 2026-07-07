#!/bin/zsh
# Whereabouts — starts the house placement tool and opens it in your browser.
#
#   HOW TO USE: double-click this file in Finder.
#   Keep the Terminal window that appears OPEN while you work.
#   Close the window when you're finished — that stops the tool.
#   Every press of Save in the browser writes your work to disk AND
#   commits it to git automatically, so nothing is ever lost.

cd "$(dirname "$0")/etl" || { echo "Could not find the etl folder next to this file."; read -r; exit 1; }
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo ""
  echo "  The 'uv' tool is missing (it may have been uninstalled)."
  echo "  To reinstall it, copy this whole line, paste it into this window,"
  echo "  and press Enter:"
  echo ""
  echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo ""
  echo "  Then double-click this file again."
  read -r -s -k '?Press any key to close...'
  exit 1
fi

URL="http://127.0.0.1:8000"

if curl -s -o /dev/null --max-time 2 "$URL"; then
  echo "The placement tool is already running — opening it in your browser."
  open "$URL"
  sleep 2
  exit 0
fi

(
  for i in {1..120}; do
    sleep 0.5
    if curl -s -o /dev/null --max-time 2 "$URL"; then
      open "$URL"
      exit 0
    fi
  done
) &

echo ""
echo "  Starting the Whereabouts placement tool…"
echo "  Your browser will open by itself in a few seconds."
echo ""
echo "  KEEP THIS WINDOW OPEN while you work."
echo "  Close this window when you're done — that switches the tool off."
echo ""
exec uv run python -m uvicorn etl.place_tool:app --host 127.0.0.1 --port 8000
