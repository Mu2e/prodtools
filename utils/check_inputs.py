"""Pre-flight verification of a campaign's input files.

See docs/superpowers/specs/2026-07-21-input-preflight-check-design.md.
Read-only: reports problems, never remediates. Blocks (exit 2) when any
input is unreadable so a slice of jobs is not launched to die in bulk.
"""
import os
from dataclasses import dataclass

from utils.jobquery import Mu2eJobPars
from utils.job_common import Mu2eName


@dataclass(frozen=True)
class Problem:
    dataset: str
    filename: str
    kind: str      # 'truncated' | 'missing' | 'nearline' | 'query_error'
    detail: str


def _section_files(tbs, section):
    """Flatten the file lists of one tbs section (inputs/auxin).

    Each entry is `[merge_factor, [file, ...]]`; the file list is value[1].
    """
    files = []
    for value in tbs.get(section, {}).values():
        if isinstance(value, list) and len(value) >= 2 and isinstance(value[1], list):
            files.extend(value[1])
    return files


def _group_by_dataset(files):
    """Group filenames by their dataset (tier.owner.desc.dsconf.art),
    order-preserving and deduplicated."""
    out = {}
    for f in dict.fromkeys(files):          # dedup, preserve order
        ds = str(Mu2eName.parse(f).with_extension('art').dataset)
        out.setdefault(ds, []).append(f)
    return out


def split_inputs(tarball_path):
    """(primary_by_ds, auxin_by_ds): distinct input files grouped by
    dataset, from the tarball's tbs.inputs (primary) and tbs.auxin
    (pileup). Frozen in the tarball — no per-index reconstruction."""
    jp = Mu2eJobPars(tarball_path)
    tbs = jp.json_data.get('tbs', {})
    return (_group_by_dataset(_section_files(tbs, 'inputs')),
            _group_by_dataset(_section_files(tbs, 'auxin')))
