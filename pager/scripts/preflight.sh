#!/bin/zsh
# preflight.sh — can this machine run pager? Run before `/pager on` enters away mode.
#
# Checks, and reports every problem found (not just the first):
#   - a `gws` is on PATH and it is the build the skill drives (`gws gmail list /
#     labels / label-info / thread / label`). Google's official Workspace CLI
#     (@googleworkspace/cli, `gws gmail +send/+read/users ...`) is also called
#     `gws` but has none of those subcommands (2026-10-29: the Studio has it at
#     ~/.local/bin/gws, and poll.sh failed only after ten retries, as "auth expired").
#   - `gws auth status` succeeds
#   - ~/.config/pager/channel.md exists and has no TODO left
#
#   exit 0  ready
#   exit 1  channel config missing or unfinished
#   exit 3  the right gws, but auth or the Gmail call is failing
#   exit 4  no gws, or the wrong gws build — pager cannot run on this machine as is
#
# Usage: preflight.sh [--channel <path>]   (default ~/.config/pager/channel.md)

CHANNEL="$HOME/.config/pager/channel.md"
[[ "$1" == "--channel" && -n "$2" ]] && CHANNEL="$2"

SUPPORTED="Machines with a working channel (references/infrastructure.md, Known channels): the CLT work laptop (channel 'clt') and the FUMC laptop (channel 'personal'). Anywhere else, stand a channel up first (same file, 'Standing up a channel on a new machine')."

rc=0
problems=()

gws_bin=$(command -v gws 2>/dev/null)
if [[ -z "$gws_bin" ]]; then
    problems+=("no 'gws' on PATH. $SUPPORTED")
    rc=4
else
    ver=$(gws --version 2>/dev/null | head -1)
    probe=$(gws gmail labels 2>&1)
    probe_rc=$?
    if [[ "$probe" == *"unrecognized subcommand"* ]]; then
        kind="a gws build without the 'gmail labels/list' subcommands pager uses"
        if gws gmail --help 2>/dev/null | grep -q '+send'; then
            kind="Google's official Workspace CLI (@googleworkspace/cli), not the gws build pager uses"
        fi
        problems+=("'$gws_bin' (${ver:-unknown version}) is $kind. poll.sh and the skill's commands (gws gmail list / labels / label-info / thread / label) will not work with it. $SUPPORTED")
        rc=4
    elif (( probe_rc != 0 )); then
        problems+=("'gws gmail labels' failed (exit $probe_rc): ${${probe[1,300]}//$'\n'/ }. Likely auth; re-run gws auth login (references/infrastructure.md has the --services list).")
        rc=3
    fi
    if (( rc != 4 )) && ! gws auth status >/dev/null 2>&1; then
        problems+=("'gws auth status' failed: not authenticated. Re-run gws auth login (references/infrastructure.md has the --services list).")
        (( rc == 0 )) && rc=3
    fi
fi

if [[ ! -f "$CHANNEL" ]]; then
    problems+=("no channel config at $CHANNEL: the channel is not built on this machine (references/infrastructure.md, 'Standing up a channel on a new machine').")
    (( rc == 0 )) && rc=1
elif grep -q 'TODO' "$CHANNEL"; then
    problems+=("$CHANNEL still has TODO values: finish it before entering away mode.")
    (( rc == 0 )) && rc=1
fi

if (( rc == 0 )); then
    echo "pager preflight OK: gws at $gws_bin (${ver:-unknown version}), authenticated; channel config $CHANNEL"
    exit 0
fi

echo "pager preflight FAILED (exit $rc) — do not enter away mode:"
for p in "${problems[@]}"; do
    echo "  - $p"
done
exit $rc
