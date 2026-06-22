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


def derive_input_defname(template, campaign, family_wide=False):
    """Discovery defname for this stage's inputs: the template's input pattern
    with ``{desc}`` replaced by the SAM wildcard ``%`` and ``{campaign}`` filled.

    family_wide=False (digi/reco/ntuple): inputs come from the same campaign as
    the output, so ``{campaign}`` → ``<campaign>%``.

    family_wide=True (mix): inputs are primaries produced at any release of the
    family, independent of the output build, so ``{campaign}`` → ``<family>%``
    (e.g. dts.mu2e.%.MDC2025%.art). The caller then narrows to latest-per-desc.
    """
    pat = _input_pattern(_default_entry(_entries(template)))
    repl = f"{family_of(campaign)}%" if family_wide else f"{campaign}%"
    pat = pat.replace('{campaign}', repl)
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


def synthesize_entry(template, input_dataset, out_campaign=None, defer_desc=False):
    """Return a ``json2jobdef`` config entry for one discovered input dataset.

    Substitutes the per-dataset fields: ``{desc}`` → its description,
    ``{campaign}`` → its release campaign (e.g. ``MDC2025ap``), ``{input}`` →
    the dataset name, ``{out_campaign}`` → the target build campaign.

    For most stages input and output share a campaign, so ``out_campaign``
    defaults to the input's campaign. Mixing is the exception: it reads
    primaries from whatever campaign they were produced at but writes a
    separately-tagged build, so the caller passes the target build campaign and
    the template uses ``{out_campaign}`` for dsconf/simjob_setup.

    ``defer_desc`` (mixing): do NOT pin ``desc`` or substitute ``{desc}``.
    Mixing derives its output desc as ``input_desc + pbeam`` at generation time
    (config_utils.prepare_fields_for_job); pinning desc here would block that
    append, and pre-substituting ``{desc}`` in the output override would lock the
    name to the bare primary desc (missing the ``Mix1BB`` suffix). Leaving
    ``{desc}`` literal lets json2jobdef resolve it from the pbeam-augmented desc.
    """
    n = Mu2eName.parse(input_dataset)
    entry = copy.deepcopy(match_entry(template, n.description))
    merge = _input_merge(entry)
    # Pin the concrete input, preserving the template's container shape:
    # list-form [{name: merge}] (mixing/jsonexpander) vs dict {name: merge}
    # (digi/reco/ntuple). pileup_datasets and other fields are left untouched.
    if isinstance(entry.get('input_data'), list):
        entry['input_data'] = [{input_dataset: merge}]
    else:
        entry['input_data'] = {input_dataset: merge}
    mapping = {'campaign': n.campaign,
               'out_campaign': out_campaign or n.campaign,
               'parent_dsconf': n.dsconf,   # full input dsconf incl build suffix
               'input': input_dataset}
    if not defer_desc:
        # Pin desc to the concrete description and substitute {desc} everywhere.
        entry['desc'] = n.description
        mapping['desc'] = n.description
    else:
        # Mixing: drop desc entirely so json2jobdef's prepare_fields_for_job
        # derives desc = input_desc + pbeam (it skips derivation if desc is set).
        # Leave {desc} tokens (e.g. in the output fileName) unsubstituted for it
        # to resolve from the pbeam-augmented desc.
        entry.pop('desc', None)
    return _subst(entry, mapping)


def emit_config(template, input_datasets, out_campaign=None, defer_desc=False):
    """Synthesize a json2jobdef config (list of entries) for the given inputs."""
    return [synthesize_entry(template, ds, out_campaign=out_campaign,
                             defer_desc=defer_desc)
            for ds in input_datasets]


def output_datasets(entry, owner='mu2e'):
    """Expected output dataset name(s) of a synthesized entry: derived from each
    ``*.fileName`` override (a Mu2e file pattern with literal ``owner``/``version``
    fields plus a sequencer), resolving owner and version (=dsconf) and dropping
    the sequencer. Skips templates that resolve to a path (e.g. /dev/null).

    Handles both shapes: scalar fields (digi/reco/ntuple) and list-wrapped
    jsonexpander fields (mixing), unwrapping ``[x]`` -> ``x``."""
    unwrap = lambda v: v[0] if isinstance(v, list) and v else v
    dsconf = unwrap(entry.get('dsconf', '')) or ''
    out = []
    for key, val in (unwrap(entry.get('fcl_overrides', {})) or {}).items():
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
