#!/usr/bin/env python3
"""Fermilab EAF (Elastic Analysis Facility) as a place to run a
`json2jobdef --once --local` run: `--local --site eaf`.

EAF is JupyterHub on Kubernetes. Each user gets servers (pods) they start
through the hub's REST API with a personal API token; a CPU server
guarantees 8 cores (bursting to 32 when its node is idle) and caps memory
at 64 GiB. There is no batch queue and no ssh. A run here is runlocal
started detached in a terminal of the user's named server `prodtools`.

Why this works without copying anything: EAF mounts /exp read-write (the
same CephFS the gpvms see) and /cvmfs. The run directory
(/exp/mu2e/data/users/<you>/prodtools/runs/<name>/) is built on the gpvm
as for any local run, and runlocal on EAF reads the cnf from it and
writes the jobs and summary.json into it. The gpvm reads them back
directly.

Files the launch script writes into the run directory, all on /exp:

    eaf_launch.sh   the script itself (written by json2jobdef)
    eaf.pid         "<runlocal pid> <pod hostname>", once runlocal started
    eaf.alive       epoch seconds, rewritten every HEARTBEAT_S
    eaf.done        "exit=<rc>", written last, when runlocal has exited
    runlocal.log    runlocal's own output (and the setup's)

What EAF cannot do, refused before a run name is spent:
- read /pnfs: no grid token there, so `inloc` must be `none` or
  `dir:/exp/...`;
- see /nashome or a gpvm's /tmp: the prodtools checkout and any code
  tarball must live under /exp.

Standard library only: the read-only MCP server imports this module for
its file names and staleness rule, and must not pull in anything else.
"""

import argparse
import base64
import datetime
import json
import os
import re
import shlex
import socket
import ssl
import stat
import struct
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import account  # noqa: E402

HUB = 'https://analytics-hub.fnal.gov'
SERVER = 'prodtools'
# The hub's spawner options for a CPU server with the Mu2e AL9 image, as
# the hub records them for a server started by hand from the FIFE box
# ("CPU Interactives"). Read 2026-11-26 from servers[...].user_options.
PROFILE = {'profile': 'fife-sl7-unpriv-interactive-htcondor',
           'imagecpu': 'mu2e-AL9-pixi'}
START_TIMEOUT_S = 420
POLL_S = 10
# 8 cores are guaranteed; the quota allows 32 on an idle node, and memory
# (64 GiB at runlocal's ~2.5 GB a job) runs out near 25.
DEFAULT_PARALLEL = 8
MAX_PARALLEL = 16
HEARTBEAT_S = 60
STALE_S = 300
# How long json2jobdef waits for eaf.pid after typing the launch line.
LAUNCH_CONFIRM_S = 120

LAUNCH = 'eaf_launch.sh'
PID = 'eaf.pid'
ALIVE = 'eaf.alive'
DONE = 'eaf.done'
LOG = 'runlocal.log'

# Paths a job on EAF can see.
VISIBLE_PREFIXES = ('/exp/', '/cvmfs/')

# The grid worker's chain (bin/runjob.sh), minus getToken: EAF has no
# Kerberos ticket to get one with. OfflineOps before `muse setup ops`, as
# there, so runmu2e's `import samweb_client` finds the spack view's copy;
# and, as there, a failed OfflineOps is a warning, not the end.
ENV_SETUP = """\
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1 \\
  || { echo 'eaf: setupmu2e-art.sh failed' >&2; exit 81; }
setup OfflineOps > /dev/null 2>&1 \\
  || echo 'eaf: WARNING: setup OfflineOps failed (continuing)' >&2
muse setup ops > /dev/null 2>&1 \\
  || { echo 'eaf: muse setup ops failed' >&2; exit 83; }
"""


class EafError(Exception):
    """Anything that stops an EAF run from being started or reached. The
    text says what to do."""


# -- refusals made from the entry alone -------------------------------------

def check_inloc(inloc):
    """Refuse an input location a job on EAF cannot read."""
    inloc = inloc or 'none'
    if inloc == 'none':
        return
    if inloc.startswith('dir:'):
        path = inloc[len('dir:'):]
        if path.startswith('/exp/'):
            return
        raise EafError(
            f"--site eaf reads inputs only from /exp: inloc {inloc!r} names "
            f"a directory EAF does not mount.")
    raise EafError(
        f"--site eaf cannot read inloc {inloc!r}: inputs on /pnfs need a "
        f"grid token, and EAF has none. Use inloc \"none\" (no input "
        f"files) or \"dir:/exp/...\", or run on a gpvm (--local without "
        f"--site) or the grid (--once).")


def check_visible(path, what):
    """Refuse a path that a job on EAF would not see."""
    real = os.path.realpath(path)
    if not real.startswith(VISIBLE_PREFIXES):
        raise EafError(
            f"--site eaf: {what} {path} resolves to {real}, which EAF does "
            f"not mount (only /exp and /cvmfs). Move it under "
            f"/exp/mu2e/app or /exp/mu2e/data.")


# -- token ------------------------------------------------------------------

def token_file(user=None):
    """$PRODTOOLS_EAF_TOKEN_FILE, else /exp/mu2e/app/users/<you>/.eaf_token
    (under /exp so the launch script on EAF can read it too)."""
    return os.environ.get('PRODTOOLS_EAF_TOKEN_FILE') or \
        f'/exp/mu2e/app/users/{user or account.current_account()}/.eaf_token'


def read_token(path):
    """The hub API token in `path`. Refuses a missing or empty file, and
    one that group or others can read."""
    hint = (f"Make one at {HUB}/hub/token (Fermilab login, VPN or proxy "
            f"off-site) and save it with mode 600 to {path}.")
    try:
        mode = os.stat(path).st_mode
    except FileNotFoundError:
        raise EafError(f"no EAF API token at {path}. {hint}") from None
    if stat.S_IMODE(mode) & 0o077:
        raise EafError(
            f"EAF token {path} has mode {stat.S_IMODE(mode):o}; it is a "
            f"credential, make it 600 (chmod 600 {path}).")
    with open(path) as fh:
        token = fh.read().strip()
    if not token:
        raise EafError(f"EAF token file {path} is empty. {hint}")
    return token


# -- hub REST ---------------------------------------------------------------

class Hub:
    """The JupyterHub REST API as the token's user. `opener` is
    urllib.request.urlopen, injectable for the tests."""

    def __init__(self, token, hub=HUB, opener=None, clock=time.time,
                 sleep=time.sleep):
        self.token, self.hub = token, hub.rstrip('/')
        self.opener = opener or urllib.request.urlopen
        self.clock, self.sleep = clock, sleep
        self._name = None

    def call(self, method, path, body=None, ok=(200, 201, 202, 204)):
        url = path if path.startswith('http') else self.hub + path
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={'Authorization': 'token ' + self.token,
                     'Content-Type': 'application/json'})
        try:
            with self.opener(req, timeout=30) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        except (urllib.error.URLError, OSError) as exc:
            raise EafError(f"EAF hub {method} {path}: {exc}") from exc
        if status in (401, 403):
            raise EafError(
                f"EAF hub refused the token (HTTP {status}) on {method} "
                f"{path}: it expired or lacks a scope. Make a new one at "
                f"{HUB}/hub/token.")
        if status not in ok:
            text = raw.decode(errors='replace')[:300]
            raise EafError(f"EAF hub {method} {path}: HTTP {status}: {text}")
        return status, (json.loads(raw) if raw else None)

    def user(self):
        _, data = self.call('GET', '/hub/api/user')
        self._name = data['name']
        return data

    @property
    def name(self):
        return self._name or self.user()['name']

    def server(self, name=SERVER):
        """This user's server `name` as the hub reports it, or None."""
        return (self.user().get('servers') or {}).get(name)

    def start(self, name=SERVER, options=None):
        self.call('POST', f'/hub/api/users/{self.name}/servers/{name}',
                  dict(PROFILE if options is None else options))

    def stop(self, name=SERVER):
        self.call('DELETE', f'/hub/api/users/{self.name}/servers/{name}',
                  ok=(202, 204, 404))

    def ensure_server(self, name=SERVER, timeout=START_TIMEOUT_S,
                      poll=POLL_S):
        """The ready server `name`, started with PROFILE when it is not
        running. Fails loudly when it is not ready within `timeout`; a
        start that never became ready is cancelled, not left pending."""
        s = self.server(name)
        if s and s.get('ready'):
            return s
        started = False
        if not (s and s.get('pending')):
            self.start(name)
            started = True
        # A spawn that was seen pending and then is neither pending nor
        # ready has failed: stop waiting. Before it is first seen pending,
        # an absent server is only the hub catching up with the start.
        seen_pending = bool(s and s.get('pending'))
        t0 = self.clock()
        while self.clock() - t0 < timeout:
            self.sleep(poll)
            s = self.server(name)
            if s and s.get('ready'):
                return s
            if s and s.get('pending'):
                seen_pending = True
            elif seen_pending:
                break
        if started:
            try:
                self.stop(name)
            except EafError:
                pass
        state = 'gone' if s is None else ('still ' + str(s.get('pending'))
                                          if s.get('pending') else 'not ready')
        raise EafError(
            f"EAF server {name!r} did not become ready within {timeout:.0f} s "
            f"({state}): EAF may have no free capacity. Try again later.")

    def run_in_terminal(self, server, command, wait_s=60):
        """Type `command` into a new terminal of `server` and return what
        it printed. The command must return promptly (background anything
        long with setsid nohup). The terminal is closed afterwards."""
        _, term = self.call('POST', server['url'] + 'api/terminals', {})
        tname = term['name']
        host = re.sub(r'^https?://', '', self.hub)
        # Wall time, not self.clock: it bounds real socket waits. The hub
        # pings an idle websocket, so only a deadline inside recv ends a
        # wait for a reply that never comes.
        deadline = time.time() + wait_s
        try:
            ws = _WebSocket(host, server['url'] +
                            f'terminals/websocket/{tname}', self.token)
            try:
                ws.send(json.dumps(['set_size', 50, 200, 800, 1600]))
                ws.send(json.dumps(['stdin', typed(command)]))
                out = ''
                while '__EAF_DONE__' not in out:
                    msg = ws.recv(deadline=deadline,
                                  what=f'a reply to {command[:120]!r}')
                    if msg is None:
                        raise EafError("EAF terminal closed before the "
                                       "command finished")
                    kind, *rest = json.loads(msg)
                    if kind == 'stdout':
                        out += rest[0]
                ws.send(json.dumps(['stdin', 'exit\r']))
            finally:
                ws.close()
        finally:
            try:
                self.call('DELETE', server['url'] + f'api/terminals/{tname}',
                          ok=(204, 404))
            except EafError:
                pass
        text = _ANSI.sub('', out)
        return text.split('__EAF_DONE__', 1)[0]

    def post_activity(self, name=SERVER):
        now = datetime.datetime.now(datetime.timezone.utc).strftime(
            '%Y-%m-%dT%H:%M:%SZ')
        self.call('POST', f'/hub/api/users/{self.name}/activity',
                  {'last_activity': now,
                   'servers': {name: {'last_activity': now}}})


_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\r')


def typed(command):
    """What is typed into the terminal for `command`: the command, then a
    marker echo, as two lines, so a command ending in `&` stays valid
    bash (`cmd &; echo` is a syntax error). The marker is split by "" so
    the terminal's echo of the typed line never contains it; only the
    shell's output does."""
    return f'{command}\recho __EAF""_DONE__\r'


class _WebSocket:
    """Just enough RFC 6455 client for a Jupyter terminal: TLS, one text
    message at a time, ping answered, no extensions. Standard library
    only, so nothing has to be installed for it."""

    def __init__(self, host, path, token, sock=None):
        if sock is None:
            raw = socket.create_connection((host, 443), timeout=30)
            sock = ssl.create_default_context().wrap_socket(
                raw, server_hostname=host)
        self.s, self.buf = sock, b''
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((
            f'GET {path} HTTP/1.1\r\nHost: {host}\r\n'
            f'Upgrade: websocket\r\nConnection: Upgrade\r\n'
            f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n'
            f'Authorization: token {token}\r\n\r\n').encode())
        head = b''
        while b'\r\n\r\n' not in head:
            chunk = self.s.recv(4096)
            if not chunk:
                raise EafError(f"EAF terminal handshake: connection closed "
                               f"({head[:200]!r})")
            head += chunk
        head, _, self.buf = head.partition(b'\r\n\r\n')
        status = head.split(b'\r\n', 1)[0].split()
        if len(status) < 2 or status[1] != b'101':
            raise EafError("EAF terminal handshake refused: " +
                           head[:300].decode(errors='replace'))

    def _read(self, n, deadline=None, what='data'):
        while len(self.buf) < n:
            if deadline is not None:
                left = deadline - time.time()
                if left <= 0:
                    raise EafError(f"EAF terminal: no {what} before the "
                                   f"deadline")
                self.s.settimeout(min(left, 30))
            try:
                chunk = self.s.recv(65536)
            except socket.timeout:
                if deadline is not None and time.time() < deadline:
                    continue
                raise EafError(f"EAF terminal: no {what} in time") from None
            if not chunk:
                raise EafError("EAF terminal: connection closed by server")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text, opcode=1):
        payload = text.encode() if isinstance(text, str) else bytes(text)
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head = struct.pack('!BB', 0x80 | opcode, 0x80 | n)
        elif n < 1 << 16:
            head = struct.pack('!BBH', 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack('!BBQ', 0x80 | opcode, 0x80 | 127, n)
        self.s.sendall(head + mask +
                       bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self, deadline=None, what='data'):
        """The next whole text message, or None once the server closes.
        With `deadline` (time.time() seconds), EafError once it passes,
        pings or not."""
        parts = []
        while True:
            b0, b1 = self._read(2, deadline, what)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack('!H', self._read(2, deadline, what))[0]
            elif n == 127:
                n = struct.unpack('!Q', self._read(8, deadline, what))[0]
            mask = self._read(4, deadline, what) if b1 & 0x80 else None
            data = self._read(n, deadline, what)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            opcode = b0 & 0x0F
            if opcode == 8:
                return None
            if opcode == 9:
                self.send(data, opcode=10)
                continue
            if opcode == 10:
                continue
            parts.append(data)
            if b0 & 0x80:
                return b''.join(parts).decode(errors='replace')

    def close(self):
        try:
            self.send(b'', opcode=8)
        except OSError:
            pass
        finally:
            self.s.close()


# -- the launch script ------------------------------------------------------

def launch_script(run_dir, runlocal_args, checkout, token_path, user,
                  server=SERVER, hub=HUB, env_setup=ENV_SETUP,
                  heartbeat_s=HEARTBEAT_S):
    """The bash script EAF runs for one run (written to run_dir/LAUNCH).

    runlocal runs in the background so the script can record its pid and
    `wait` for it; a `kill -TERM <pid in eaf.pid>` reaches runlocal, whose
    handler stops the jobs. The EXIT trap writes eaf.done LAST of the run's
    files, then stops this server: one run at a time per server, so the
    server's work is over and the 8 cores go back to EAF.
    """
    q = shlex.quote
    rd = q(run_dir)
    auth = (f"printf 'Authorization: token %s\\n' "
            f"\"$(tr -d '[:space:]' < {q(token_path)})\"")
    api = f"{hub}/hub/api/users/{user}"
    runlocal = ' '.join(q(a) for a in runlocal_args)
    return f"""#!/bin/bash
# Written by json2jobdef --once --local --site eaf; runs on EAF server
# {server!r}. Reads and writes only {run_dir}.
cd {rd} || exit 90
child=
hb=
finish() {{
    rc=$?
    trap - EXIT
    [ -n "$hb" ] && kill "$hb" 2>/dev/null
    echo "exit=$rc" > {rd}/{DONE}
    {auth} | curl -sS -m 30 -H @- -X DELETE {q(api + '/servers/' + server)} \\
        > /dev/null 2>&1
    exit "$rc"
}}
trap finish EXIT
heartbeat() {{
    while :; do
        date -u +%s > {rd}/{ALIVE}
        now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        {auth} | curl -sS -m 30 -H @- -H 'Content-Type: application/json' \\
            -X POST -d "{{\\"last_activity\\": \\"$now\\", \\"servers\\": \
{{\\"{server}\\": {{\\"last_activity\\": \\"$now\\"}}}}}}" \\
            {q(api + '/activity')} > /dev/null 2>&1
        sleep {int(heartbeat_s)}
    done
}}
heartbeat &
hb=$!
{env_setup}unset MUSE_WORK_DIR
source {q(os.path.join(checkout, 'bin', 'setup.sh'))} || exit 84
python3 -u {q(os.path.join(checkout, 'utils', 'runlocal.py'))} {runlocal} &
child=$!
echo "$child $(hostname)" > {rd}/{PID}
wait "$child"
"""


def launch_line(run_dir):
    """What json2jobdef types into the EAF terminal."""
    rd = shlex.quote(run_dir)
    return (f"setsid nohup bash {rd}/{LAUNCH} > {rd}/{LOG} 2>&1 "
            f"< /dev/null &")


# -- reading a run's files (run_status uses these) --------------------------

def read_pid(run_dir):
    """(pid, host) from eaf.pid, or None while there is none."""
    try:
        with open(os.path.join(run_dir, PID)) as fh:
            fields = fh.read().split()
    except FileNotFoundError:
        return None
    if len(fields) != 2 or not fields[0].isdigit():
        raise ValueError(f"{os.path.join(run_dir, PID)} is not "
                         f"'<pid> <host>': {fields!r}")
    return int(fields[0]), fields[1]


def read_done(run_dir):
    """runlocal's exit code from eaf.done, or None while there is none."""
    try:
        with open(os.path.join(run_dir, DONE)) as fh:
            text = fh.read().strip()
    except FileNotFoundError:
        return None
    m = re.fullmatch(r'exit=(\d+)', text)
    if not m:
        raise ValueError(f"{os.path.join(run_dir, DONE)} is not "
                         f"'exit=<rc>': {text!r}")
    return int(m.group(1))


def heartbeat_age(run_dir, now=None):
    """Seconds since the launch script last wrote eaf.alive, or None
    while it never has."""
    try:
        with open(os.path.join(run_dir, ALIVE)) as fh:
            stamp = int(fh.read().strip())
    except FileNotFoundError:
        return None
    except ValueError:
        return None
    return (time.time() if now is None else now) - stamp


def busy_runs(root, now=None):
    """Names of EAF runs under `root` that may still be going: receipt
    `running`, no eaf.done, and a heartbeat that is fresh or not yet
    written. One run at a time per server."""
    from utils import run_receipt
    busy = []
    try:
        names = sorted(os.listdir(root))
    except FileNotFoundError:
        return busy
    for name in names:
        run_dir = os.path.join(root, name)
        try:
            receipt = run_receipt.read(root, name)
        except (run_receipt.RunNotFound, ValueError, OSError):
            continue
        if receipt.get('executor') != 'eaf' or \
                receipt.get('state') not in ('starting', 'running'):
            continue
        try:
            if read_done(run_dir) is not None:
                continue
        except ValueError:
            continue
        age = heartbeat_age(run_dir, now)
        if age is None or age < STALE_S:
            busy.append(name)
    return busy


# -- command line -----------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(
        prog='python -m utils.eaf',
        description='Look at or stop the EAF server and runs that '
                    '`json2jobdef --once --local --site eaf` uses.')
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status', help='the server as the hub reports it')
    s = sub.add_parser('stop', help='stop one run: SIGTERM to its '
                       'runlocal, which ends its jobs')
    s.add_argument('name', help='run name, cnf.<you>.<desc>.<dsconf>.N')
    sub.add_parser('stop-server', help='stop the server now, whatever '
                   'runs on it')
    args = p.parse_args(argv)
    hub = Hub(read_token(token_file()))
    try:
        if args.cmd == 'status':
            s = hub.server()
            print(json.dumps(s, indent=2) if s else
                  f'no server {SERVER!r} running')
        elif args.cmd == 'stop-server':
            hub.stop()
            print(f'server {SERVER!r} stopping')
        else:
            from utils import run_receipt
            run_dir = os.path.join(run_receipt.runs_root(), args.name)
            got = read_pid(run_dir)
            if got is None:
                sys.exit(f"eaf: {run_dir}/{PID} does not exist: the run "
                         f"never started runlocal")
            if read_done(run_dir) is not None:
                sys.exit(f"eaf: {args.name} already finished "
                         f"({run_dir}/{DONE})")
            pid, host = got
            s = hub.server()
            if not (s and s.get('ready')):
                sys.exit(f"eaf: server {SERVER!r} is not running, so "
                         f"neither is {args.name}")
            # SIG""SENT: the echoed command line must not match.
            out = hub.run_in_terminal(
                s, f'[ "$(hostname)" = {shlex.quote(host)} ] && '
                   f'kill -TERM {pid} && echo SIG""SENT')
            if 'SIGSENT' not in out:
                sys.exit(f"eaf: could not signal pid {pid} on {host}: "
                         f"{out.strip()[-300:]}")
            print(f'SIGTERM sent to runlocal pid {pid} on {host}; '
                  f'{run_dir}/{DONE} appears when it has stopped')
    except EafError as exc:
        sys.exit(f'eaf: {exc}')


if __name__ == '__main__':
    main()
