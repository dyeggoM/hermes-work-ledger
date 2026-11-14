#!/usr/bin/env bash
# Install the work ledger on this host: the CLI wrapper, the hook script, the ledgers directory and
# the skill itself.
#
#   ./scripts/install.sh                  # ~/.hermes/skills/personal/work-ledger (symlinked to this checkout)
#   ./scripts/install.sh --copy           # copy the skill instead of symlinking it
#   HERMES_HOME_DIR=/srv/hermes ./scripts/install.sh
#
# Layout it produces, all under <hermes-home> (default ~/.hermes):
#   scripts/ledger                    the wrapper every agent calls
#   scripts/ledger_guard_hook.py      the pre_tool_call guard (see references/enforcement.md)
#   ledgers/<scope>[.<agent>].db      one SQLite per scope and agent
#   skills/personal/work-ledger       this skill
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
home="${HERMES_HOME_DIR:-${HERMES_HOME:-$HOME/.hermes}}"
# inside a bot, HERMES_HOME points at the profile: the shared home is two levels up
if [[ "$home" == */profiles/* ]]; then home="$(cd "$home/../.." && pwd)"; fi

mode="symlink"
[[ "${1:-}" == "--copy" ]] && mode="copy"

mkdir -p "$home/scripts" "$home/ledgers" "$home/skills/personal"

install -m 755 "$here/ledger.py" "$home/scripts/ledger_impl.py"
cat > "$home/scripts/ledger" <<EOF
#!/usr/bin/env bash
# Work-ledger wrapper (installed by scripts/install.sh). Call it by absolute path or put it on PATH.
set -euo pipefail
scope="\${LEDGER_SCOPE:-}"
if [[ -z "\$scope" ]]; then
  root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
  scope="\$(git -C "\$root" config --get remote.origin.url 2>/dev/null | sed -E 's#.*/##; s#\\.git\$##' || true)"
  [[ -n "\$scope" ]] || scope="\$(basename "\$root")"
fi
exec python3 "$home/scripts/ledger_impl.py" --dir "\${LEDGER_DIR:-$home/ledgers}" --scope "\$scope" "\$@"
EOF
chmod 755 "$home/scripts/ledger"
install -m 755 "$here/ledger_guard_hook.py" "$home/scripts/ledger_guard_hook.py"

target="$home/skills/personal/work-ledger"
rm -rf "$target"
if [[ "$mode" == "symlink" ]]; then ln -s "$root" "$target"; else cp -r "$root" "$target"; rm -rf "$target/.git"; fi

echo "installed:"
echo "  CLI      $home/scripts/ledger"
echo "  hook     $home/scripts/ledger_guard_hook.py"
echo "  ledgers  $home/ledgers"
echo "  skill    $target ($mode)"
echo
echo "next: ledger init --items 80, then wire the hook into a profile with scripts/install-enforcement.sh <profile>"
