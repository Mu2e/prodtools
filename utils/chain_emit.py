#!/usr/bin/env python3
"""Synthesis logic for ``latestDatasets --emit``: turn a discovered input
dataset + a per-campaign stage template into a ``json2jobdef`` config entry,
and derive the discovery defname for a stage from its template's input pattern.

POMS-free chain dts→digi→reco→ntuple, walked as per-tier hops. Templates live
in ``<templates_dir>/<campaign>/<stage>.json`` and carry the curated physics
(geom, DbService version, nearestMatch, fcl, dsconf, simjob_setup). The only
per-primary substitution done here is ``{desc}`` and ``{input}`` — everything
else is authored, not derived.
"""

import copy
import json
import os
import re

from utils.job_common import Mu2eName

_FAMILY_RE = re.compile(r"^(MDC\d{4}|Run\d+[A-Z]?)")

# stage -> the input data tier that stage consumes
STAGE_INPUT_TIER = {'digi': 'dts', 'reco': 'dig', 'ntuple': 'mcs'}
# inverse: input tier -> stage (for tier-inferred stage selection)
TIER_TO_STAGE = {tier: stage for stage, tier in STAGE_INPUT_TIER.items()}


def stage_for_tier(tier):
    """Infer the chain stage from an input dataset's tier (dts→digi, dig→reco, mcs→ntuple)."""
    try:
        return TIER_TO_STAGE[tier]
    except KeyError:
        raise ValueError(
            f"no chain stage consumes tier '{tier}' (known: {sorted(TIER_TO_STAGE)})")


def family_of(campaign):
    """Campaign family, release letters stripped: MDC2025ap→MDC2025,
    Run1Ban→Run1B. Returns the input unchanged if it doesn't match."""
    m = _FAMILY_RE.match(campaign or "")
    return m.group(1) if m else campaign


def template_path(campaign, stage, templates_dir):
    return os.path.join(templates_dir, campaign, f"{stage}.json")


def load_template(campaign, stage, templates_dir):
    """Load ``<templates_dir>/<family>/<stage>.json``, where family is the
    campaign with release letters stripped (MDC2025ap→MDC2025, Run1Ban→Run1B).

    Fail loud if absent: a new family must have its physics deliberately curated
    (geom/DbService/nearestMatch), never silently inherited.
    """
    family = family_of(campaign)
    path = template_path(family, stage, templates_dir)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"No template for family '{family}' stage '{stage}': {path}\n"
            f"Create it to set geom/DbService/nearestMatch for this family.")
    with open(path) as f:
        return json.load(f)


def _input_pattern(template):
    """The single input_data key pattern declared by a stage template."""
    indata = template.get('input_data')
    if not indata:
        raise ValueError("template has no 'input_data'")
    if isinstance(indata, list):
        if len(indata) != 1:
            raise ValueError("emit template input_data must declare exactly one pattern")
        indata = indata[0]
    keys = list(indata.keys())
    if len(keys) != 1:
        raise ValueError("emit template input_data must declare exactly one pattern")
    return keys[0]


def _input_merge(template):
    """The merge factor paired with the single input_data pattern."""
    indata = template['input_data']
    if isinstance(indata, list):
        indata = indata[0]
    return indata[_input_pattern(template)]


def _entries(template):
    """A template is one entry (dict) or a list of entries; normalize to a list."""
    return template if isinstance(template, list) else [template]


def _explicit_descs(entry):
    """Concrete descriptions an entry names (excludes the `{desc}` wildcard).
    `desc` may be a scalar or a list."""
    d = entry.get('desc')
    if isinstance(d, list):
        return [x for x in d if '{desc}' not in x]
    if isinstance(d, str) and '{desc}' not in d:
        return [d]
    return []


def has_wildcard(template):
    """True if any entry's `desc` is the `{desc}` wildcard (→ discover all descs)."""
    return any(isinstance(e.get('desc'), str) and '{desc}' in e['desc']
               for e in _entries(template))


def explicit_descriptions(template):
    """Union of concrete descriptions named across the template's entries.
    When non-empty and `has_wildcard` is False, --emit restricts to these."""
    out = []
    for e in _entries(template):
        out.extend(_explicit_descs(e))
    return out


def _default_entry(entries):
    """The entry that drives discovery: the `{desc}` wildcard if present
    (at most one), else the first entry (its input pattern shape is shared)."""
    wild = [e for e in entries if isinstance(e.get('desc'), str) and '{desc}' in e['desc']]
    if wild:
        if len(wild) != 1:
            raise ValueError("template must have at most one '{desc}' (wildcard) entry")
        return wild[0]
    return entries[0]


def match_entry(template, description):
    """Pick the entry for an input description: an entry naming it explicitly
    (scalar or in a list) wins, else the `{desc}` wildcard / first entry."""
    entries = _entries(template)
    for e in entries:
        if description in _explicit_descs(e):
            return e
    return _default_entry(entries)


def derive_input_defname(template, campaign):
    """Discovery defname for this stage's inputs: the template's input pattern
    with ``{desc}`` replaced by the SAM wildcard ``%`` and ``{campaign}`` filled
    from ``campaign`` (per-campaign templates usually bake the campaign already).
    """
    pat = _input_pattern(_default_entry(_entries(template)))
    pat = pat.replace('{campaign}', f"{campaign}%")
    pat = pat.replace('{desc}', '%')
    return pat


def _subst(obj, mapping):
    """Recursively substitute {key} placeholders in all strings of obj."""
    if isinstance(obj, str):
        for k, v in mapping.items():
            obj = obj.replace('{' + k + '}', v)
        return obj
    if isinstance(obj, list):
        return [_subst(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {_subst(k, mapping): _subst(v, mapping) for k, v in obj.items()}
    return obj


def synthesize_entry(template, input_dataset):
    """Return a ``json2jobdef`` config entry for one discovered input dataset.

    Substitutes the per-dataset fields: ``{desc}`` → its description,
    ``{campaign}`` → its release campaign (e.g. ``MDC2025ap``), ``{input}`` →
    the dataset name. input_data is pinned to the concrete dataset (keeping the
    template's merge factor); all other fields are the curated family template.
    """
    n = Mu2eName.parse(input_dataset)
    entry = copy.deepcopy(match_entry(template, n.description))
    merge = _input_merge(entry)
    entry['input_data'] = {input_dataset: merge}
    # Pin desc to the concrete description (the matched entry may carry a list
    # or the {desc} wildcard); {desc}/{campaign}/{input} substitute everywhere.
    entry['desc'] = n.description
    return _subst(entry, {'desc': n.description,
                          'campaign': n.campaign,
                          'input': input_dataset})


def emit_config(template, input_datasets):
    """Synthesize a json2jobdef config (list of entries) for the given inputs."""
    return [synthesize_entry(template, ds) for ds in input_datasets]


def output_datasets(entry, owner='mu2e'):
    """Expected output dataset name(s) of a synthesized entry: derived from each
    ``*.fileName`` override (a Mu2e file pattern with literal ``owner``/``version``
    fields plus a sequencer), resolving owner and version (=dsconf) and dropping
    the sequencer. Skips templates that resolve to a path (e.g. /dev/null)."""
    dsconf = entry.get('dsconf', '')
    out = []
    for key, val in entry.get('fcl_overrides', {}).items():
        if not key.endswith('fileName') or not isinstance(val, str) or '/' in val:
            continue
        parts = val.split('.')
        if len(parts) != 6:
            continue
        tier, _owner, desc, _version, _seq, ext = parts
        out.append(f"{tier}.{owner}.{desc}.{dsconf}.{ext}")
    return out


def dataset_complete(dataset_name, count_fn, njobs_fn):
    """True iff the dataset has exactly as many files as its producing cnf's
    njobs. ``count_fn(name)->int`` and ``njobs_fn(name)->int`` are injected so
    this stays unit-testable without SAM."""
    return count_fn(dataset_name) == njobs_fn(dataset_name)
