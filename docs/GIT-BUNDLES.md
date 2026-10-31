# Offline Git repository updates

These helpers use [general file transport](TRANSPORT.md) without changing its
staging or import behavior. The workstation downloads repositories; the live OS
reads only local bundles already in the encrypted inbox.

## Prepare bundles on the workstation

```bash
make add-transport-git-bundle REPO=git@github.com:example/homelab.git
make add-transport-git-bundle REPO=git@github.com:example/homelab-hsm.git
make stage-transport PAIR=1
```

`REPO` is required. HTTPS, SSH (including `git@host:owner/repo.git`) and `file://`
URLs are supported. Use your normal Git credential helper or SSH agent; do not
embed HTTPS credentials or query tokens in the URL. No root or connected USB pair
is required to prepare bundles.

The repository basename becomes `transport/git-bundles/<name>.bundle`, with a
`<name>.json` sidecar recording its source identity, default branch, branch/tag
revisions and bundle SHA-256. Names must begin with an ASCII letter or digit and
contain only letters, digits, dots, underscores or hyphens. Different owners or
hosts with the same basename are refused as collisions rather than overwriting
one another. HTTPS and SSH spellings of the same host/path share an identity.

Each preparation uses a fresh full bare clone and creates a self-contained bundle
of every fetched branch and tag and all history reachable from them. Empty or
shallow repositories are refused. No previous delivery is needed on the receiver.
Repeating the command refreshes only that repository's pair of files. Cloning,
creation and verification finish before publication, so a failed preparation
preserves the previous completed bundle. Preparation commands serialize through a
checkout lock. Do not run staging concurrently with preparation.

A process or power failure between the two final file renames can leave a
mismatched pair; the receiver refuses its checksum. Rerun preparation to repair
it before staging. A leftover `.prepare-*` directory can be removed after
inspection. All files beneath `transport/git-bundles/` remain Git-ignored.

## Restore or update during the offline session

```bash
start 1
sudo workspace-transport-import 1
workspace-transport-import-git-bundles \
  /mnt/workspace/inbox/<delivery>/git-bundles
```

Run the Git command **as simlun, without sudo**, inside the working session. It
requires the active writable encrypted workspace and a source directory within
its inbox. It takes the workspace lock to serialize with export and other helper
operations. No pool import, unlock or replica activation happens automatically.

For each bundle, the destination is an ordinary working checkout:

```text
/mnt/workspace/git/homelab/
/mnt/workspace/git/homelab-hsm/
```

New repositories are checked out on the recorded default branch in a temporary
sibling directory before publication. Existing destinations must have been
created by this helper and retain the same source identity and default branch.
Unrelated checkouts, linked worktrees, symlinks and shallow/partial clones are
refused. Repositories are created as simlun, so files use UID/GID 1000:1000.

The importer preflights the complete batch: file hashes, metadata, Git bundle
self-containment, object connectivity, branch/tag refs and destination state.
Failure in preflight updates no managed repository. It then applies repositories
independently, checking destination state again before each update. Later
failures do not roll back completed repositories; the command reports them and
returns nonzero. Rerunning retries the remaining work. Incoming bundles and
metadata stay in the inbox as recovery artifacts.

## Update policy

| Situation | Result |
| --- | --- |
| New repository | Clone all branches/tags and check out the default branch |
| Clean default checkout with newer upstream commits | Fast-forward the checkout |
| Reimport the same bundle | Succeed without changing the checkout |
| Tracked changes, untracked files or ignored files | Refuse and preserve local files |
| Local commits, divergent history or an older default-branch revision | Refuse without reset or rollback |
| Another checked-out branch or changed upstream default branch | Refuse for deliberate inspection |
| Upstream branch added/updated/deleted | Refresh/prune `origin/*` tracking refs; preserve local branches |
| New upstream tag | Import it |
| Existing tag changed or absent upstream | Preserve it and print a discrepancy |

This updates a working copy; it is not a destructive mirror of upstream. Keep
local changes elsewhere or commit/push them through your normal workflow before
retrying. No automatic stash, reset, clean, branch switching or force-push repair
is provided. Interrupted fetches can be retried; inspect and resolve any ordinary
Git lock/index recovery errors after abrupt termination rather than deleting
locks automatically.

The source identity and expected default branch are stored as local
`level1vault.transport-*` Git configuration. `origin` points at the last inbox
bundle, never the Internet URL. The importer always explicitly fetches from its
verified local copy, even if remote settings were edited. Deleting an old inbox
delivery does not remove committed data from the restored repository.

## Scope and security

Only committed Git objects reachable from fetched branches/tags are included.
Uncommitted source files, hosting-provider metadata, notes and other custom refs,
submodule repositories and Git LFS object storage are outside this first version.
Submodule pointers and LFS pointer files remain in Git, but their external content
is not downloaded. Prepare submodule repositories separately when required.

Offline Git invocations allow only local file transport, disable recursive
submodule fetching, hooks, filesystem-monitor hooks and automatic maintenance,
and ignore system/global Git configuration. New repositories use an empty Git
template. Existing repositories with configured content filters or partial-clone
settings are refused. Import never builds or runs repository code or installs
the software it carries.

Bundles contain full history, including any secrets ever committed in reachable
history. They are plaintext while in local `transport/` and the USB transport
dataset. Hashes detect corruption and mismatched metadata, not malicious content
from the workstation. Importing into encrypted storage does not erase old
plaintext transport blocks. The general transport security boundary still applies.
