#!/usr/bin/env bash
# fleet-connect.sh — the one entry point. Reads the host's fleet-connect config, works out
# which keep-alive mechanism this host uses, and dispatches. It contains no knowledge of any
# particular machine, agent, session or label: everything comes from the config.
#
#   ./fleet-connect.sh                 re-arm the channel on every agent of this host
#   ./fleet-connect.sh --dry-run       show the targets, send no keystrokes
#   ./fleet-connect.sh --restart       escalate: restart the runtime processes, then re-arm
#   ./fleet-connect.sh NAME...         limit to these agents (config name or session name)
#   ./fleet-connect.sh --where         print the config path, host kind and target list
#
# Config: $FLEET_CONNECT_CONFIG, else ${XDG_CONFIG_HOME:-~/.config}/fleet-connect/fleet.json
# (create it with scripts/install.sh). Schema: reference/config.md.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"
fc_require_config || exit 1

MODE=connect; DRY=0; PASS=()
for a in "$@"; do
  case "$a" in
    --restart) MODE=restart ;;
    --where) MODE=where ;;
    --dry-run) DRY=1; PASS+=("$a") ;;
    *) PASS+=("$a") ;;
  esac
done

KIND="$(fc_host_kind)"
ROOT="$(fc_fleet_root)"

if [ "$MODE" = where ]; then
  echo "config:    $FC_CONFIG"
  [ "$FC_CONFIG_REAL" != "$FC_CONFIG" ] && echo "resolves:  $FC_CONFIG_REAL"
  echo "host kind: $KIND"
  [ "$KIND" = systemd ] && echo "fleet root: $ROOT"
  echo "agents:"
  fc_agents | awk -F'\t' '{printf "  %-24s session=%-26s label=%s\n", $1, $2, $3}'
  exit 0
fi

case "$KIND" in
  systemd)
    # A fleet host owns its own generic machinery — this skill only calls it.
    [ -x "$ROOT/infra/channel-up.sh" ] || { echo "fleet-connect: no infra/channel-up.sh under $ROOT" >&2; exit 1; }
    names=()
    if [ "${#PASS[@]}" -gt 0 ]; then
      for p in "${PASS[@]}"; do [ -n "$p" ] && [ "${p#-}" = "$p" ] && names+=("$p"); done
    fi
    if [ "${#names[@]}" -eq 0 ]; then
      while IFS=$'\t' read -r n _rest; do [ -n "$n" ] && names+=("$n"); done < <(fc_agents)
    fi
    if [ "$DRY" -eq 1 ]; then
      printf 'would run: %s %s\n' "$ROOT/infra/channel-up.sh" "${names[*]}"
      [ "$MODE" = restart ] && printf 'preceded by: %s <name> for each\n' "$ROOT/infra/agent-restart.sh"
      exit 0
    fi
    rc=0
    if [ "$MODE" = restart ]; then
      for n in "${names[@]}"; do "$ROOT/infra/agent-restart.sh" "$n" || rc=1; done
    fi
    "$ROOT/infra/channel-up.sh" "${names[@]}" || rc=1
    exit $rc
    ;;
  launchd)
    script="$HERE/launchd-connect.sh"
    [ "$MODE" = restart ] && script="$HERE/launchd-restart.sh"
    if [ "${#PASS[@]}" -gt 0 ]; then exec "$script" "${PASS[@]}"; else exec "$script"; fi
    ;;
  *)
    echo "fleet-connect: unknown host.keepalive '$KIND' in $FC_CONFIG (expected launchd or systemd)." >&2
    exit 1
    ;;
esac
