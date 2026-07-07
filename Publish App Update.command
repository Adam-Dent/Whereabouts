#!/bin/zsh
# Whereabouts — publishes your latest placement work to the live app.
#
#   HOW TO USE: double-click this file in Finder after a placement session.
#   It rebuilds the app's data and sends it to whereabouts-app.pages.dev.
#   Phones pick the update up automatically the next time they open the app.
#
#   Safe to run any time: if nothing has changed, it publishes nothing new.

cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

finish() {
  echo ""
  if [[ -t 0 ]]; then
    read -r -s -k '?Press any key to close this window...'
  fi
  exit "$1"
}

if ! command -v uv >/dev/null 2>&1; then
  echo "The 'uv' tool is missing. Reinstall it by pasting this line here:"
  echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
  finish 1
fi
if ! command -v npx >/dev/null 2>&1; then
  echo "Node.js is missing (needed to talk to the hosting service)."
  echo "Reinstall it from https://nodejs.org or with: brew install node"
  finish 1
fi

echo ""
echo "  Step 1 of 2: building the app data from your placements..."
echo ""
if ! (cd etl && uv run whereabouts-build-pwa); then
  echo ""
  echo "  The build FAILED, so nothing was published. The live app is untouched."
  echo "  The messages above say what went wrong."
  finish 1
fi

echo ""
echo "  Step 2 of 2: publishing to whereabouts-app.pages.dev..."
echo ""
if ! npx wrangler pages deploy docs/ --project-name whereabouts-app --branch main; then
  echo ""
  echo "  Publishing FAILED. The live app is still running the previous version."
  echo "  If it mentions being logged out, run this line here and try again:"
  echo "    npx wrangler login"
  finish 1
fi

echo ""
echo "  ✓ Done. Your latest work is live at https://whereabouts-app.pages.dev"
echo "  Phones will pick it up the next time the app opens with signal."
finish 0
