"""Whether this process serves one caller or many.

The difference is a property of the transport, decided once in main():

- stdio is one client in the caller's own account, so getpass.getuser()
  IS the caller and `mine` means what it says.
- streamable-http is an endpoint several people reach. The process user
  is then the HOST, not the reader, and `mine` would hand every reader
  the host's ledger and the host's queue. An empty answer from the
  wrong ledger is indistinguishable from "no campaigns", which is the
  silent failure this flag exists to prevent.

Kept as process state rather than a tool parameter because no client
could correctly set it: the caller cannot know how the server it is
talking to was started.
"""

_SHARED = False


def set_shared(value):
    """Called once by main(), before any tool can run."""
    global _SHARED
    _SHARED = bool(value)


def is_shared():
    return _SHARED
