#!/usr/bin/env bash
# Fails if any OpenRouter key material or secret file is tracked or staged. Never prints the key.
# Installed as .git/hooks/pre-push; also run manually before every push.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
fail=0

if git ls-files --cached | grep -i -E '(^|/)openrouterkey\.txt$|(^|/)\.env$|(^|/)secrets/'; then
  echo "ERROR: a secret file is tracked by git"; fail=1
fi

if git grep --cached -l -I -E 'sk-or-v1-[0-9a-fA-F]{16,}' -- . >/dev/null 2>&1; then
  echo "ERROR: OpenRouter key material found in tracked content:"
  git grep --cached -l -I -E 'sk-or-v1-[0-9a-fA-F]{16,}' -- .
  fail=1
fi

if [ -f Openrouterkey.txt ]; then
  k=$(tr -d '\r\n ' < Openrouterkey.txt)
  if [ -n "$k" ] && git grep --cached -q -F -e "$k" -- . 2>/dev/null; then
    echo "ERROR: the exact key value appears in tracked content"; fail=1
  fi
  # Also scan commit history about to be pushed.
  if [ -n "$k" ] && git log --all -p 2>/dev/null | grep -q -F -e "$k"; then
    echo "ERROR: the exact key value appears in git history"; fail=1
  fi
fi

[ "$fail" -eq 0 ] && echo "secret check: OK"
exit "$fail"
