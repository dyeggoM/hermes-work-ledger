#!/usr/bin/env bash
# Install the work-ledger guard as a Hermes shell hook for one or more profiles.
#
#   ./scripts/install-enforcement.sh bot-a bot-b
#   HERMES_HOME_DIR=$HOME/.hermes ./scripts/install-enforcement.sh --all
#
# Copies ledger_guard_hook.py into ~/.hermes/scripts, then adds the hooks block to each profile's
# config.yaml and turns on hooks_auto_accept (a headless profile never sees the TTY prompt that would
# otherwise approve the script, and an unapproved hook simply does not fire).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
home="${HERMES_HOME_DIR:-$HOME/.hermes}"
scripts="$home/scripts"
hook="$scripts/ledger_guard_hook.py"
matcher='write_file|patch|edit_file|apply_patch|multi_edit|terminal'

mkdir -p "$scripts"
install -m 755 "$root/scripts/ledger_guard_hook.py" "$hook"

profiles=("$@")
if [[ "${1:-}" == "--all" ]]; then
  profiles=()
  for d in "$home"/profiles/*/; do
    [[ -f "$d/config.yaml" ]] && profiles+=("$(basename "$d")")
  done
fi
[[ ${#profiles[@]} -gt 0 ]] || { echo "usage: $0 <profile>... | --all" >&2; exit 2; }

for profile in "${profiles[@]}"; do
  cfg="$home/profiles/$profile/config.yaml"
  if [[ ! -f "$cfg" ]]; then echo "skip $profile: no config.yaml"; continue; fi
  python3 - "$cfg" "$hook" "$matcher" <<'PY'
import re, sys
cfg, hook, matcher = sys.argv[1:4]
text = open(cfg).read()

block = f"""hooks_auto_accept: true
hooks:
  pre_tool_call:
    - command: {hook}
      matcher: '{matcher}'
      timeout: 30
"""
if f"command: {hook}" in text:
    print(f"  {cfg}: already wired")
    raise SystemExit
if re.search(r"^hooks_auto_accept:", text, flags=re.M):
    text = re.sub(r"^hooks_auto_accept:.*$", "hooks_auto_accept: true", text, count=1, flags=re.M)
else:
    text = text.rstrip("\n") + "\n" + block
    open(cfg, "w").write(text)
    print(f"  {cfg}: hooks block added")
    raise SystemExit

if re.search(r"^hooks:\s*$", text, flags=re.M):
    # append the entry under the existing pre_tool_call list, or create the list
    if re.search(r"^hooks:\s*\n(?:\s+.*\n)*?\s+pre_tool_call:\s*$", text, flags=re.M):
        text = re.sub(r"(^hooks:\s*\n(?:\s+.*\n)*?\s+pre_tool_call:\s*$)",
                      lambda m: m.group(1) + f"\n    - command: {hook}\n      matcher: '{matcher}'\n      timeout: 30",
                      text, count=1, flags=re.M)
    else:
        text = re.sub(r"^hooks:\s*$", f"hooks:\n  pre_tool_call:\n    - command: {hook}\n      matcher: '{matcher}'\n      timeout: 30",
                      text, count=1, flags=re.M)
else:
    text = text.rstrip("\n") + "\n" + block
open(cfg, "w").write(text)
print(f"  {cfg}: wired")
PY
  echo "  check it with: HERMES_HOME=$home/profiles/$profile hermes hooks doctor"
done
