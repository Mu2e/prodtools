#!/usr/bin/env python3
"""Synthesis logic for ``latestDatasets --emit``: turn a discovered input
dataset + a per-campaign stage template into a ``json2jobdef`` config entry,
and derive the discovery defname for a stage from its template's input pattern.

Chain dts->digi->reco->ntuple, walked as per-tier hops. Templates live in
``<templates_dir>/<campaign>/<stage>.json`` and carry the curated physics
(geom, DbService version, nearestMatch, fcl, dsconf, simjob_setup). The only
per-primary substitution done here is ``{desc}`` and ``{input}`` — everything
else is authored, not derived.
"""

import copy
import json
import os
import re

from utils.config_utils import _get_first_if_list, mixing_desc
from utils.job_common import Mu2eName

_FAMILY_RE = re.compile(r"^(MDC\d{4}|Run\d+[A-Z]?)")

# stage -> the input data tier that stage consumes
STAGE_INPUT_TIER = {'digi': 'dts', 'reco': 'dig', 'ntuple': 'mcs'}
# stage -> the output data tier(s) it produces (ntuple writes nts or ntd)
STAGE_OUTPUT_TIERS = {'digi': ('dig',), 'reco': ('mcs',), 'ntuple': ('nts', 'ntd')}
# inverse: output tier -> stage
_TIER_TO_OUTPUT_STAGE = {t: s for s, tiers in STAGE_OUTPUT_TIERS.items() for t in tiers}


def input_tier_for_output(out_tier):
    """Map a stage's output tier back to the input tier it consumes
    (mcs→dig, dig→dts, nts/ntd→mcs)."""
    try:
        return STAGE_INPUT_TIER[_TIER_TO_OUTPUT_STAGE[out_tier]]
    except KeyError:
        raise ValueError(
            f"no chain stage produces tier '{out_tier}' (known: {sorted(_TIER_TO_OUTPUT_STAGE)})")


def family_of(campaign):
    """Campaign family, release letters stripped: MDC2025ap→MDC2025,
    Run1Ban→Run1B. Returns the input unchanged if it doesn't match."""
    m = _FAMILY_RE.match(campaign or "")
    return m.group(1) if m else campaign


def template_path(campaign, stage, templates_dir):
    return os.path.join(templates_dir, campaign, f"{stage}.json")


def load_template(campaign, stage, templates_dir):
    """Load ``<templates_dir>/<family>/<stage>.json`` (family = campaign with
    release letters stripped, e.g. MDC2025ap->MDC2025, Run1Ban->Run1B).

    Fails loud if absent: a new family must have its physics deliberately
    curated (geom/DbService/nearestMatch), never silently inherited.
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
    """The single input_data key pattern declared by a stage template.

    `input_data` is either a bare pattern string (the merge factor then
    lives per-description in `desc`) or the legacy `{pattern: merge}`
    mapping.
    """
    indata = template.get('input_data')
    if not indata:
        raise ValueError("template has no 'input_data'")
    if isinstance(indata, list):
        if len(indata) != 1:
            raise ValueError("emit template input_data must declare exactly one pattern")
        indata = indata[0]
    if isinstance(indata, str):
        return indata
    keys = list(indata.keys())
    if len(keys) != 1:
        raise ValueError("emit template input_data must declare exactly one pattern")
    return keys[0]


def _desc_name(item):
    """The description named by one `desc` list item.

    An item is either a plain string, or the legacy per-description dict
    ``{"desc": "<name>", "merge": <n>}``. Prefer the `desc` mapping form
    (``{"<name>": <merge>}``) for new templates — see `_desc_map`.
    """
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        name = item.get('desc')
        if not isinstance(name, str):
            raise ValueError(
                f"template desc entry {item!r} has no 'desc' name — a malformed "
                f"item would silently drop that description from the roster")
        return name
    raise ValueError(f"template desc entry must be a string or dict, got {item!r}")


def _desc_map(entry):
    """The entry's `desc` as an ordered {name: settings} mapping, or None
    if it does not use the mapping form.

    Mapping form pairs each description with its own settings, mirroring
    `input_data`'s scalar-or-dict grammar: a bare int is the merge factor,
    a dict carries `merge` plus anything else (e.g. `fcl_overrides`)::

        "desc": {"CeMLeadingLog": 4,
                 "NoPrimary": {"merge": 5, "fcl_overrides": {...}}}

    Preferred shape: keeps the merge factor in one place, no repeated key.
    """
    d = entry.get('desc')
    if not isinstance(d, dict):
        return None
    out = {}
    for name, spec in d.items():
        if isinstance(spec, int) and not isinstance(spec, bool):
            out[name] = {'merge': spec}
        elif isinstance(spec, dict):
            out[name] = spec
        else:
            raise ValueError(
                f"desc mapping for {name!r}: value must be an int merge factor "
                f"or a settings dict, got {spec!r}")
    return out


def _desc_settings(entry, description):
    """The per-description settings dict for `description`, or {}."""
    mapping = _desc_map(entry)
    if mapping is not None:
        return mapping.get(description, {})
    d = entry.get('desc')
    for item in (d if isinstance(d, list) else [d]):
        if isinstance(item, dict) and _desc_name(item) == description:
            return item
    return {}


def _input_merge(template, description=None):
    """The merge factor for `description` under this entry.

    Precedence: the description's own `merge` (from the `desc` mapping or a
    legacy per-desc dict) wins; otherwise the factor paired with the
    input_data pattern (legacy `{pattern: merge}` form).

    Fails loud when neither supplies one — a silently-defaulted merge would
    produce an undersized round with nothing to flag it.
    """
    indata = template['input_data']
    if isinstance(indata, list):
        indata = indata[0]
    default = None if isinstance(indata, str) else indata[_input_pattern(template)]

    if description is not None:
        merge = _desc_settings(template, description).get('merge', default)
    else:
        merge = default

    if merge is None:
        where = f" for description {description!r}" if description else ""
        raise ValueError(
            f"no merge factor{where}: give it one in the template's `desc` "
            f"mapping, or pair a factor with the input_data pattern")
    return merge


def _apply_desc_overrides(entry, description):
    """Patch the entry's `fcl_overrides` with this description's own, in
    place, preserving the template's container shape (list vs dict).

    A PATCH, not a replacement: one description needing a single extra
    override (e.g. NoPrimary.fcl's trigger include) would otherwise force a
    duplicate of the entry's whole pileup/dsconf/fcl block. Per-desc keys win.
    `entry` is already a deep copy, so the shared template is never mutated.
    """
    extra = _desc_settings(entry, description).get('fcl_overrides')
    if not extra:
        return
    base = entry.get('fcl_overrides')
    if isinstance(base, list):
        merged = dict(base[0]) if base else {}
        merged.update(extra)
        entry['fcl_overrides'] = [merged]
    else:
        merged = dict(base or {})
        merged.update(extra)
        entry['fcl_overrides'] = merged


def _entries(template):
    """A template is one entry (dict) or a list of entries; normalize to a list."""
    return template if isinstance(template, list) else [template]


def _explicit_descs(entry):
    """Concrete descriptions an entry names (excludes the `{desc}` wildcard).
    `desc` may be a scalar, a list of strings / per-desc dicts, or the
    preferred {name: settings} mapping."""
    mapping = _desc_map(entry)
    if mapping is not None:
        return [x for x in mapping if '{desc}' not in x]
    d = entry.get('desc')
    if isinstance(d, list):
        names = [_desc_name(x) for x in d]
        return [x for x in names if '{desc}' not in x]
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

    family_wide=False (digi/reco/ntuple): inputs share the output's campaign,
    so ``{campaign}`` -> ``<campaign>%``.

    family_wide=True (mix): inputs are primaries from any release of the
    family, independent of the output build, so ``{campaign}`` -> ``<family>%``
    (e.g. dts.mu2e.%.MDC2025%.art). Caller narrows to latest-per-desc.
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


def synthesize_entry(template, input_dataset, out_campaign=None, defer_desc=False,
                     dsconf=None):
    """Return a ``json2jobdef`` config entry for one discovered input dataset.

    Substitutes the per-dataset fields: ``{desc}`` -> its description,
    ``{campaign}`` -> its release campaign (e.g. ``MDC2025ap``), ``{input}`` ->
    the dataset name, ``{out_campaign}`` -> the target build campaign.

    ``out_campaign`` defaults to the input's campaign (most stages share one).
    Mixing is the exception: primaries come from whatever campaign they were
    produced at, but the build is separately tagged, so the caller passes the
    target build campaign for the template's ``{out_campaign}``.

    ``dsconf``, if given, overrides the template's dsconf outright (after
    substitution, preserving scalar-vs-list shape) — e.g. pin
    ``MDC2025ar_best_v1_3`` so both the emitted config and the skip-produced
    check target that build regardless of the template's own dsconf. ``None``
    leaves the template's dsconf as-is.

    ``defer_desc`` (mixing): do NOT pin ``desc`` or substitute ``{desc}``,
    since json2jobdef derives output desc as ``input_desc + pbeam`` at
    generation time (config_utils.prepare_fields_for_job) and skips that if
    desc is already set; leaving ``{desc}`` literal lets it resolve later
    instead of locking to the bare primary desc (missing the ``Mix1BB`` suffix).
    """
    n = Mu2eName.parse(input_dataset)
    entry = copy.deepcopy(match_entry(template, n.description))
    merge = _input_merge(entry, n.description)
    _apply_desc_overrides(entry, n.description)
    # pin the concrete input, preserving container shape: list-form (mixing)
    # vs dict (digi/reco/ntuple); other fields (e.g. pileup_datasets) untouched
    if isinstance(entry.get('input_data'), list):
        entry['input_data'] = [{input_dataset: merge}]
    else:
        entry['input_data'] = {input_dataset: merge}
    mapping = {'campaign': n.campaign,
               'out_campaign': out_campaign or n.campaign,
               'parent_dsconf': n.dsconf,   # full input dsconf incl build suffix
               'input': input_dataset}
    if not defer_desc:
        entry['desc'] = n.description
        mapping['desc'] = n.description
    else:
        # mixing: drop desc so prepare_fields_for_job derives input_desc+pbeam
        # (it skips derivation if desc is set); leave {desc} tokens literal
        # for it to resolve from the pbeam-augmented desc.
        entry.pop('desc', None)
    entry = _subst(entry, mapping)
    if dsconf is not None and 'dsconf' in entry:
        entry['dsconf'] = [dsconf] if isinstance(entry['dsconf'], list) else dsconf
    return entry


def emit_config(template, input_datasets, out_campaign=None, defer_desc=False,
                dsconf=None):
    """Synthesize a json2jobdef config (list of entries) for the given inputs."""
    return [synthesize_entry(template, ds, out_campaign=out_campaign,
                             defer_desc=defer_desc, dsconf=dsconf)
            for ds in input_datasets]


def _deferred_descs(entry):
    """Reconstruct the concrete output desc(s) for a ``defer_desc`` (mixing)
    entry, whose desc is left as the literal ``{desc}`` since json2jobdef
    derives it as ``input_desc + pbeam`` at generation time: parse the pinned
    input's description and append each ``pbeam`` value (e.g. CeMLeadingLog +
    Mix1BB). Returns [] when the entry isn't deferred / has no pbeam."""
    indata = entry.get('input_data')
    indata = indata[0] if isinstance(indata, list) and indata else indata
    if not isinstance(indata, dict) or not indata:
        return []
    try:
        input_desc = Mu2eName.parse(next(iter(indata))).description
    except ValueError:
        return []
    pbeam = entry.get('pbeam')
    pbeams = pbeam if isinstance(pbeam, list) else ([pbeam] if isinstance(pbeam, str) else [])
    return [mixing_desc(input_desc, pb) for pb in pbeams]


def output_datasets(entry, owner='mu2e'):
    """Expected output dataset name(s) of a synthesized entry: derived from each
    ``*.fileName`` override (a Mu2e file pattern with literal ``owner``/``version``
    fields plus a sequencer), resolving owner and version (=dsconf) and dropping
    the sequencer. Skips templates that resolve to a path (e.g. /dev/null).
    Handles both scalar fields (digi/reco/ntuple) and list-wrapped mixing fields.

    Mixing leaves ``{desc}`` literal in the output fileName (see ``defer_desc``);
    when it survives, expand it to the concrete ``input_desc + pbeam`` name(s)
    so the produced-output check matches real SAM datasets instead of a literal
    ``dig.mu2e.{desc}...`` that can never exist."""
    dsconf = _get_first_if_list(entry.get('dsconf', '')) or ''
    out = []
    for key, val in (_get_first_if_list(entry.get('fcl_overrides', {})) or {}).items():
        if not key.endswith('fileName') or not isinstance(val, str) or '/' in val:
            continue
        # only the 6-field file form counts; Mu2eName parses it structurally
        try:
            n = Mu2eName.parse(val)
        except ValueError:
            continue
        if n.is_dataset:
            continue
        descs = ([n.description.replace('{desc}', rd) for rd in _deferred_descs(entry)]
                 if '{desc}' in n.description else [n.description])
        for d in descs:
            out.append(str(Mu2eName.build(tier=n.tier, owner=owner, description=d,
                                          dsconf=dsconf, extension=n.extension)))
    return out

