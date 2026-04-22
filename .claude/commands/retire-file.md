---
description: Produce the command to retire a SAM file and remove it from dCache (as mu2epro)
argument-hint: <sam-filename>
---

# Retire + remove a SAM-registered file

Given a Mu2e SAM filename, produce a copy-pasteable command that
retires the SAM record and removes the on-disk file, running as
`mu2epro` via `ksu`. **Does not execute** — the user runs it
themselves after reviewing.

## Instructions

You are given `$ARGUMENTS`. Treat the first whitespace-separated
token as the SAM filename (e.g.
`cnf.mu2e.PBINormal_33344.MDC2025ai.0.tar`). If empty or obviously
not a Mu2e filename, ask for clarification instead of producing a
command.

Emit exactly this, with `<FILE>` substituted:

> **WARNING:** this command retires the SAM record and deletes the
> file from `/pnfs`. Not reversible. Verify the filename before
> running. Must run as `mu2epro` — use your own prompt, don't ask
> Claude to execute.

Then output the command in a fenced `bash` block:

```bash
ksu mu2epro -e /bin/bash -c '
  source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
  muse setup ops
  set -e
  FILE=<FILE>
  PNFS=$(samweb locate-file $FILE | grep -oE "/pnfs/[^ ]+" | head -1)
  echo "retiring in SAM..."
  samweb retire-file $FILE
  echo "removing $PNFS/$FILE"
  rm -v "$PNFS/$FILE"
  echo "verify:"
  samweb locate-file $FILE 2>&1 || echo "(gone)"
'
```

### Ordering rationale

`samweb retire-file` first, then `rm`. If retire fails (e.g. expired
token), the physical file is still present and the SAM record still
points to it — consistent state, retry safe. The reverse order risks
leaving SAM with a live pointer to a deleted file.

### When not to suggest running it for the user

By default: do not offer to execute. If the user explicitly asks you
to run it, use the same `ksu mu2epro -e /bin/bash -c '...'` wrapper
via the Bash tool. Never acquire tokens for mu2epro — if the
authenticated `oksuzian@FNAL.GOV` principal doesn't have the
production bearer token cached, stop and report (see the
`feedback_never_get_mu2epro_token` memory).

## Notes

- dCache's FUSE mount supports `unlink` on ordinary files; plain `rm`
  suffices. `mdh rm -s disk` is the official Mu2e pattern and
  provides niceties (coordinated multi-location removal, verbose
  logging) but isn't required for single-location files like
  `cnf.*.tar` on persistent disk.
- File ownership matters: files pushed as mu2epro can only be removed
  by mu2epro. Hence the `ksu` wrapper.
