#!/usr/bin/env python3
"""Generate the manual acceptance steps from the automated QEMU checks."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def render():
    # Explain how to run the shared checks in a manually operated guest.
    parts = ['''# Stage 4: test a pair

Input: an initialized active pair, pair ID, passphrase, and OVMF firmware. Output: private logs and `result.json` under the build run’s `validation/` directory, falling back to `~/level1vault-qemu/<test-run>/` if permissions prevent that. Run `make test-qemu` as your desktop user on the workstation for pair 1; no extra arguments are required (see [QEMU setup](../../docs/QEMU.md)). This optional stage tests disposable copies; originals remain unchanged. On failure inspect the report and logs, then rerun with a new results directory. Tests: `make test` checks the runner and this generated manual; actual boots are workstation-only.

## Shared manual acceptance checks

[QEMU setup and automated runner](../../docs/QEMU.md) · [Documentation index](../../docs/README.md)

This page is generated from `stages/04-test-pair/guest-checks.sh` and `stages/04-test-pair/scenarios.json`.
Run `python3 stages/04-test-pair/generate-manual.py` after editing either source; `make test` rejects drift.

For manual testing, use the QEMU launch command in [QEMU.md](../../docs/QEMU.md).
Keep the same writable image pair throughout the sequence. Stop QEMU before
changing the attached drives, and start each scenario with a fresh copy of OVMF
variables. Run every scenario in order; each consumes the preceding writes.
Use a disposable initialized **active** pair, not a replica or physical disks.

At each login (including after reboot), save the following script as
`/tmp/level1vault-qemu-check.sh` in the guest. The live root is ephemeral, so the
script and shell variables must be recreated after every boot. Set `PAIR` to
your pair ID and reuse the **same** 16-character hexadecimal `RUN` in every boot:

```bash
PAIR=1
RUN=0123456789abcdef
cat > /tmp/level1vault-qemu-check.sh <<'CHECKS'
'''.rstrip()]
    parts.append((ROOT/'stages/04-test-pair/guest-checks.sh').read_text().rstrip())
    parts.append("CHECKS\n```\n\nEnter the workspace passphrase when `mount` prompts. Each check must print `PASS`; stop on any failure.\n")

    # Render every scenario from the exact sequence consumed by the runner.
    for scenario in json.loads((ROOT/'stages/04-test-pair/scenarios.json').read_text()):
        drives = ' and '.join(side.upper() for side in scenario['drives'])
        parts.append(f"## {scenario['name']}\n\nAttach **{drives}**. {scenario['purpose']}\n\n```bash")
        for phase, *value in scenario['steps']:
            if phase == 'reboot':
                parts.append('sudo reboot\n# After login, recreate the script and PAIR/RUN variables above.')
            else:
                suffix = ' ' + value[0] if value else ''
                parts.append(f'bash /tmp/level1vault-qemu-check.sh {phase} "$PAIR" "$RUN"{suffix}')
        parts.append('sudo poweroff\n```\n')
    parts.append('Physical USB transport, firmware compatibility, Secure Boot settings, and unexpected power loss remain separate hardware checks.\n')
    return '\n'.join(parts)


def main():
    # Update the manual or report that its generated checks have drifted.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    path = ROOT/'stages/04-test-pair/README.md'
    expected = render()
    if args.check:
        if not path.exists() or path.read_text() != expected:
            sys.exit('QEMU manual checks are stale; run python3 stages/04-test-pair/generate-manual.py')
    else:
        path.write_text(expected)


if __name__ == '__main__':
    main()
