"""Parse and order Mu2e dsconf strings.

Grammar (decision 4 of the 2026-09-03 grill):
    <family><letters><rev?>[_<purpose>_v<major>_<minor>][_<extra>][-<suffix>]
    family  : starts with an uppercase letter, e.g. MDC2025, Run1B, MDC2020
    letters : one or two lowercase campaign letters; ABSENT for the dead
              own-series ntuple names (MDC2025-003), which rank lowest
    rev     : optional single digit revision (Run1Bab2)
    extra   : legacy Offline-version tail (v06_06_00), ordered last, lexically

Ordering never uses a clock. `sort_key()` is the only comparison the
catalog makes between siblings.
"""
import re
from typing import NamedTuple, Optional

_RE = re.compile(
    r'^(?P<family>[A-Z][A-Za-z0-9]*?)'
    # rev only ever follows letters (Run1Bab2); grouping them together
    # keeps a bare trailing year digit (MDC2025-003) out of rev, which
    # would otherwise truncate family to MDC202.
    r'(?:(?P<letters>[a-z]{1,2})(?P<rev>\d)?)?'
    r'(?:_(?P<purpose>[a-z]+)_v(?P<major>\d+)_(?P<minor>\d+))?'
    r'(?:_(?P<extra>v\d+_\d+_\d+))?'
    r'(?:-(?P<suffix>\d+))?$'
)


class DsconfParseError(ValueError):
    """The dsconf does not follow the Mu2e grammar. Report it; never guess."""


class DsconfKey(NamedTuple):
    family: str
    letters: str          # '' for own-series names
    rev: int              # 0 when absent
    purpose: Optional[str]
    major: int            # -1 when absent
    minor: int            # -1 when absent
    suffix: int           # -1 when absent
    extra: str            # '' when absent

    def sort_key(self):
        """Newest sorts highest. Purpose is a group key, not an order key."""
        return ((len(self.letters), self.letters), self.rev,
                self.major, self.minor, self.suffix, self.extra)


def parse_dsconf(s: str) -> DsconfKey:
    m = _RE.match(s or '')
    if not m:
        raise DsconfParseError(f'dsconf does not parse: {s!r}')
    g = m.groupdict()
    # A family with no letters is only legal in the own-series form
    # (MDC2025-003); a bare 'MDC2025' would otherwise parse as a family.
    if g['letters'] is None and g['suffix'] is None:
        raise DsconfParseError(f'dsconf has neither letters nor suffix: {s!r}')
    return DsconfKey(
        family=g['family'],
        letters=g['letters'] or '',
        rev=int(g['rev']) if g['rev'] else 0,
        purpose=g['purpose'],
        major=int(g['major']) if g['major'] else -1,
        minor=int(g['minor']) if g['minor'] else -1,
        suffix=int(g['suffix']) if g['suffix'] else -1,
        extra=g['extra'] or '',
    )


def family_of(s: str) -> str:
    return parse_dsconf(s).family
