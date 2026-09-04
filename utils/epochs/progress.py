r"""Build progress for `bin/epochs`, on stderr.

A full catalog build walks every dig of every family and issues well over
a thousand serial SAM queries. Until this module it printed nothing at
all until it finished, so the only signal a user had was a terminal
sitting still for minutes — reported four times as "it hangs". It does
not hang; it just never said so.

Three rules keep the cure from becoming a second disease:

- **stderr only.** stdout is the catalog rows (and `publish`'s summary
  line); `epochs members ... > file` and any piping of stdout must be
  byte-for-byte what they were before.
- **Off unless stderr is a TTY**, and off under `--quiet` whatever
  stderr is. A redirected log gets nothing at all.
- **One line per family**, rewritten in place with `\r`, plus one
  summary line at the end. One line per SAM call would be thousands of
  lines; one line per dig would be ~400.

`enabled=False` turns every method into a no-op, so callers never have
to branch on whether progress is wanted. `clock` and `interval` are
injectable so a test can drive the redraw throttle deterministically
instead of sleeping.
"""
import sys
import time

_WIDTH = 78


class Progress:
    def __init__(self, stream=None, enabled=None, interval: float = 0.25,
                 clock=time.monotonic):
        """`stream=None` resolves `sys.stderr` NOW, at construction, so a
        caller running under `contextlib.redirect_stderr` (the test
        harness, and any embedding of `cli.main`) reports into the
        redirected stream rather than the process's real stderr.

        `enabled=None` means "decide from the stream": a TTY gets
        progress, a redirected file or pipe does not."""
        self._stream = sys.stderr if stream is None else stream
        if enabled is None:
            enabled = bool(getattr(self._stream, 'isatty', lambda: False)())
        self.enabled = enabled
        self._interval = interval
        self._clock = clock
        self._t0 = clock()
        self._last_draw = None
        self._family = ''
        self._family_digs = 0
        self._family_done = 0
        self._families = 0
        self._digs = 0
        self._members = 0

    # -- events ----------------------------------------------------------
    def note(self, text: str) -> None:
        """A standing line — a caveat about what the build covers, not a
        progress tick. Terminated with a newline so the in-place family
        line never overwrites it."""
        self._write(f'epochs: {text}\n')

    def family(self, name: str, ndigs: int) -> None:
        """Start of one family's dig loop. The previous family's line is
        completed and terminated first, so a finished family stays on
        screen instead of being overwritten."""
        if self._family:
            self._draw(force=True)
            self._write('\n')
        self._family, self._family_digs, self._family_done = name, ndigs, 0
        self._families += 1
        self._draw(force=True)

    def dig(self, nmembers: int) -> None:
        """One dig of the current family finished being walked."""
        self._family_done += 1
        self._digs += 1
        self._members = nmembers
        self._draw()

    def finish(self, nmembers: int) -> None:
        self._members = nmembers
        if self._family:
            self._draw(force=True)
            self._write('\n')
        plural = 'y' if self._families == 1 else 'ies'
        self._write(f'epochs: built {self._families} famil{plural}, {self._digs} digs, '
                    f'{nmembers} members in {self._clock() - self._t0:.1f}s\n')

    # -- plumbing --------------------------------------------------------
    def _write(self, text: str) -> None:
        if not self.enabled:
            return
        self._stream.write(text)
        self._stream.flush()

    def _draw(self, force: bool = False) -> None:
        """Rewrite the current family's line in place. Throttled to one
        redraw per `interval` unless forced: ~400 digs at a few per
        second is a redraw rate a terminal does not need."""
        if not self.enabled:
            return
        now = self._clock()
        if not force and self._last_draw is not None and now - self._last_draw < self._interval:
            return
        self._last_draw = now
        line = (f'epochs: {self._family} {self._family_done}/{self._family_digs} digs, '
                f'{self._members} members, {now - self._t0:.0f}s')
        self._write('\r' + line[:_WIDTH].ljust(_WIDTH))
