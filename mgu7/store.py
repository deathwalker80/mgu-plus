"""Install the mgu-store app store (github.com/robertnisipeanu/mgu-store) on a
rooted MGU head unit, from the root SSH access this tool has just set up.

It runs the project's own installer, unchanged: the standalone `mgu-install`
executable of the `installer` release, checked against that release's
SHA256SUMS before it is run (the same checks deploy/install.ps1 makes). The
installer is started as

    mgu-install first-install --identity <key> --host <MGU IP> --no-restart-prompt

so it reaches the unit this tool already found over ENET. Its output is
streamed into this tool's log. The head unit's restart, when the installer
asks for one, is a separate step (restart()), so the GUI can ask first.

mgu-store is GPL-3.0-or-later and is downloaded from its own release; nothing
of it is bundled here.
"""

import datetime
import gzip
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request

from . import sshcli

if getattr(sys, 'frozen', False):
    HERE = os.path.dirname(os.path.abspath(sys.executable))
else:
    HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INSTALLER_BASE = 'https://github.com/robertnisipeanu/mgu-store/releases/download/installer'
CATALOG_URL = 'https://github.com/robertnisipeanu/mgu-store/releases/download/catalog/catalog.json'
STORE_DIR = os.path.join(HERE, 'data', 'mgu-store')
STATE_FILE = os.path.join(STORE_DIR, 'state.json')
USER_AGENT = 'MGU-Plus'

# The car's own Wi-Fi hotspot: the head unit answers SSH here on every car.
HOTSPOT_IP = '172.16.222.97'

# The deploy tool mgu-store installs on the unit, and how mgu-install itself
# reads its state: the last line of `status --json` (the status steps write
# their own lines to stderr), or "absent" when the Store was never installed.
DEPLOY = '/var/opt/mgu-deploy/mgu-deploy.sh'
STATUS_CMD = ('if [ -e %s ]; then sh %s status --json 2>/dev/null | tail -n 1; '
              'else echo absent; fi' % (DEPLOY, DEPLOY))

# Row states of compare().
ROW_CURRENT = 'current'
ROW_UPDATE = 'update'
ROW_MISSING = 'missing'
ROW_NEWER = 'newer'
ROW_OTHER = 'other'      # installed, not in mgu-store's catalog (another source)

# Exit status of install(), also used as the tool's own exit status.
EXIT_DONE = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2          # the installer refused (head unit coding missing, ...)
EXIT_RESTART_NEEDED = 10  # installed; a head-unit restart finishes the setup

# Lines the installer prints (deploy/mgu-install.js) that decide the outcome.
LINE_RESTART = 'Restart the head unit to finish setup.'
LINE_DONE = 'Setup is done'
LINE_CODING = "The head unit's coding is missing"

# The head unit's restart, exactly as mgu-install and the Store make it: the
# Node State Manager's normal restart, refused (exit 3) while a deploy tool run
# of this boot holds its lock. No double quote or backslash, so it passes
# through Windows' command-line quoting unchanged.
RESTART = ("L=/var/opt/mgu-deploy/lock; if [ -e $L ] && [ $(cut -d ' ' -f 2 $L) = "
           "$(cat /proc/sys/kernel/random/boot_id) ] && kill -0 $(cut -d ' ' -f 1 $L) "
           "2>/dev/null; then exit 3; fi; /usr/bin/nsm_control --requestRestart 7 --normal 2>&1")


class StoreError(Exception):
    """The installer could not be fetched, verified or started."""


def asset_name(system=None, machine=None):
    """The installer release asset for this computer, e.g.
    mgu-install-windows-x64.exe.gz. Raises StoreError when there is none."""
    system = (system or platform.system()).lower()
    machine = (machine or os.environ.get('PROCESSOR_ARCHITEW6432')
               or platform.machine()).lower()
    cpu = {'amd64': 'x64', 'x86_64': 'x64', 'x64': 'x64',
           'arm64': 'arm64', 'aarch64': 'arm64'}.get(machine)
    osname = {'windows': 'windows', 'linux': 'linux', 'darwin': 'macos'}.get(system)
    if not cpu or not osname:
        raise StoreError('no mgu-store installer for %s %s' % (system, machine))
    return 'mgu-install-%s-%s%s.gz' % (osname, cpu, '.exe' if osname == 'windows' else '')


def parse_sums(text):
    """SHA256SUMS text -> {file name: lower-case sha256}."""
    sums = {}
    for line in text.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and len(fields[0]) == 64:
            sums[fields[1].strip().lstrip('*')] = fields[0].lower()
    return sums


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _download(url, dest, timeout=60):
    """Download *url* to *dest* (through a .part file). Raises StoreError."""
    part = dest + '.part'
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, open(part, 'wb') as out:
            shutil.copyfileobj(response, out)
        os.replace(part, dest)
    except (urllib.error.URLError, OSError, ValueError) as e:
        try:
            os.remove(part)
        except OSError:
            pass
        raise StoreError('could not download %s: %s' % (url, getattr(e, 'reason', e)))


def ensure_installer(log, base=INSTALLER_BASE, folder=STORE_DIR, name=None):
    """Download and verify the installer, and return the executable's path.

    The release's SHA256SUMS and the compressed executable are kept in
    *folder*, so a computer without internet at the car runs the copy it
    downloaded last time (checked against the SHA256SUMS kept with it)."""
    name = name or asset_name()
    os.makedirs(folder, exist_ok=True)
    sums_path = os.path.join(folder, 'SHA256SUMS')
    gz_path = os.path.join(folder, name)
    exe_path = os.path.join(folder, name[:-3])
    online = True
    log('Downloading the mgu-store installer from its release...')
    try:
        _download(base + '/SHA256SUMS', sums_path + '.new')
    except StoreError as e:
        online = False
        log('Could not reach the installer release: %s' % e)
        if not (os.path.isfile(sums_path) and os.path.isfile(gz_path)):
            raise StoreError('no internet and no copy of the installer from an earlier run; '
                             'connect this computer to the internet (Wi-Fi) and try again')
        log('Using the copy of the installer downloaded earlier.')
    if online:
        os.replace(sums_path + '.new', sums_path)
    with open(sums_path, 'r', encoding='utf-8', errors='replace') as f:
        want = parse_sums(f.read()).get(name)
    if not want:
        raise StoreError('SHA256SUMS lists no %s' % name)
    if not (os.path.isfile(gz_path) and sha256_file(gz_path) == want):
        if not online:
            raise StoreError('the installer kept from an earlier run does not match its SHA256SUMS; '
                             'connect this computer to the internet and try again')
        _download(base + '/' + name, gz_path)
    got = sha256_file(gz_path)
    if got != want:
        try:
            os.remove(gz_path)
        except OSError:
            pass
        raise StoreError('%s does not match SHA256SUMS (sha256 %s, expected %s); nothing was run. '
                         'Try again in a minute' % (name, got, want))
    log('Installer verified (sha256 %s).' % got)
    try:
        with gzip.open(gz_path, 'rb') as src, open(exe_path + '.part', 'wb') as out:
            shutil.copyfileobj(src, out)
        os.replace(exe_path + '.part', exe_path)
        if os.name != 'nt':
            os.chmod(exe_path, 0o755)
    except OSError as e:
        raise StoreError('could not unpack the installer: %s' % e)
    return exe_path


def classify(rc, lines):
    """The outcome of an installer run from its exit status and output."""
    text = '\n'.join(lines)
    if rc == 2:
        return EXIT_REFUSED
    if rc != 0:
        return EXIT_FAILED
    if LINE_RESTART in text:
        return EXIT_RESTART_NEEDED
    return EXIT_DONE


def ssh_banner(host, port=22, timeout=3.0):
    """The SSH banner *host* serves, or None: a cheap 'is the head unit there'."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            line = s.recv(255).split(b'\n', 1)[0].decode('latin-1', 'replace').strip()
            return line if line.startswith('SSH-') else None
    except OSError:
        return None


def run_installer(exe, host, key_path, log, extra=(), command='first-install'):
    """Run the installer's *command* (first-install, uninstall) against *host*
    with *key_path*; stream its output to *log*. Returns (exit status, lines)."""
    lines = []
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    # The key goes through a temporary copy locked to this user (Windows'
    # OpenSSH refuses a key other accounts can read), and the host keys
    # through an empty temporary file: the installer's own
    # UserKnownHostsFile=/dev/null is not a usable path on Windows (issue #2
    # of this tool), and ssh takes the first value given for an option.
    with sshcli.prepared_key(key_path) as key, sshcli.empty_known_hosts() as known:
        argv = [exe, command, '--identity', key, '--host', host, '--no-restart-prompt',
                '--ssh-opt', 'UserKnownHostsFile=' + known,
                '--ssh-opt', 'GlobalKnownHostsFile=' + known] + list(extra)
        env = dict(os.environ)
        env.pop('CI', None)
        try:
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, env=env, creationflags=flags)
        except OSError as e:
            raise StoreError('could not start the installer: %s' % e)
        with p.stdout:
            for raw in p.stdout:
                line = raw.decode('utf-8', 'replace').rstrip('\r\n')
                lines.append(line)
                if line.strip():
                    log(line)
        rc = p.wait()
    return rc, lines


def install(host, key_path, log, extra=()):
    """Fetch, verify and run the installer. Returns one of the EXIT_* values."""
    if not sshcli.available():
        log('OpenSSH client (ssh) not found on PATH; cannot verify the login. '
            'Install the Windows OpenSSH client or add ssh to PATH.')
        return EXIT_FAILED
    try:
        exe = ensure_installer(log)
        log('Installing the Store on the head unit (keep the car awake and on stable power)...')
        rc, lines = run_installer(exe, host, key_path, log, extra)
    except StoreError as e:
        log('Store install failed: %s' % e)
        return EXIT_FAILED
    result = classify(rc, lines)
    if result == EXIT_DONE:
        log('DONE: the Store is installed. Its tile is among the car\'s apps.')
    elif result == EXIT_RESTART_NEEDED:
        log('DONE: the Store is installed. Restart the head unit to finish.')
    elif result == EXIT_REFUSED and any(LINE_CODING in line for line in lines):
        log('STOPPED: the head unit\'s coding is missing (normal after a flash). Restore it with '
            'BimmerCode or E-Sys from your backup, restart the head unit, then press Install Store again.')
    elif result == EXIT_REFUSED:
        log('STOPPED: the installer refused to install (exit 2); see its message above.')
    else:
        log('Store install failed (installer exit %d); see its messages above.' % rc)
    return result


def fetch_catalog(log, url=CATALOG_URL, folder=STORE_DIR):
    """The published catalog (release `catalog` of mgu-store): the newest
    version of every component. Kept in *folder*, so without internet the
    copy from the last check is shown. Returns (catalog, online)."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, 'catalog.json')
    online = True
    try:
        _download(url, path + '.new', timeout=30)
        with open(path + '.new', 'r', encoding='utf-8') as f:
            json.load(f)
        os.replace(path + '.new', path)
    except (StoreError, ValueError) as e:
        online = False
        try:
            os.remove(path + '.new')
        except OSError:
            pass
        log('Could not read the latest Store versions: %s' % e)
        if not os.path.isfile(path):
            raise StoreError('no internet and no list of versions from an earlier check')
        log('Showing the versions from the last check.')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            catalog = json.load(f)
    except (OSError, ValueError) as e:
        raise StoreError('the list of versions is not readable: %s' % e)
    if not isinstance(catalog.get('components'), list):
        raise StoreError('the list of versions has no components')
    return catalog, online


def catalog_rows(catalog):
    """The catalog's components as plain rows: id, kind, latest version,
    optional, the head units they are for, and their titles by language."""
    rows = []
    for c in catalog.get('components', []):
        if not isinstance(c, dict) or not c.get('id') or not c.get('version'):
            continue
        titles = {'en': c.get('title') or c['id']}
        for lang, text in (c.get('texts') or {}).items():
            if isinstance(text, dict) and text.get('title'):
                titles[lang] = text['title']
        rows.append({'id': c['id'], 'kind': c.get('kind') or 'tool', 'latest': c['version'],
                     'optional': bool(c.get('optional')), 'hmi': c.get('mguHmi') or None,
                     'titles': titles})
    return rows


def parse_version(text):
    """'0.9.33' -> (0, 9, 33); anything after the numbers is ignored."""
    parts = []
    for piece in str(text or '').split('-', 1)[0].split('+', 1)[0].split('.'):
        digits = ''.join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def read_installed(host, key_path):
    """What the deploy tool on the unit reports: None when the Store was never
    installed, else {'hmi', 'firmware', 'deploy', 'components' {id: version},
    'pending_reboot' [ids]}. Raises StoreError when it cannot be read."""
    rc, out, err = sshcli.run(host, key_path, STATUS_CMD, timeout=90)
    if rc != 0:
        raise StoreError('could not read the installed versions: %s' % (err.strip() or 'exit %d' % rc))
    last = out.strip().splitlines()[-1].strip() if out.strip() else ''
    if last == 'absent':
        return None
    try:
        data = json.loads(last)
    except ValueError:
        raise StoreError('the head unit returned no readable status')
    unit = data.get('unit') or {}
    comps = {}
    for c in data.get('components') or []:
        if isinstance(c, dict) and c.get('id'):
            comps[c['id']] = c.get('version') or '?'
    pending = sorted({p.get('id') for p in data.get('pending_reboot') or [] if isinstance(p, dict) and p.get('id')})
    return {'hmi': unit.get('hmi'), 'firmware': unit.get('firmware'), 'deploy': data.get('version'),
            'components': comps, 'pending_reboot': pending}


def compare(rows, installed):
    """Rows of catalog_rows() with the installed version and a state; only
    the components meant for this head unit (its hmi) are kept."""
    comps = (installed or {}).get('components') or {}
    hmi = (installed or {}).get('hmi')
    out = []
    for r in rows:
        if hmi and r['hmi'] and hmi not in r['hmi']:
            continue
        have = comps.get(r['id'])
        if have is None:
            state = ROW_MISSING
        elif parse_version(have) < parse_version(r['latest']):
            state = ROW_UPDATE
        elif parse_version(have) > parse_version(r['latest']):
            state = ROW_NEWER
        else:
            state = ROW_CURRENT
        row = dict(r)
        row.update({'installed': have, 'state': state})
        out.append(row)
    known = {r['id'] for r in rows}
    for cid in sorted(comps):
        if cid not in known:
            out.append({'id': cid, 'kind': None, 'latest': None, 'optional': False, 'hmi': None,
                        'titles': {'en': cid}, 'installed': comps[cid], 'state': ROW_OTHER})
    return out


def summarize(rows, installed):
    """{'installed': bool, 'store': installed Store version or None,
    'updates': n, 'missing': n} for the status line and the logs."""
    store_row = next((r for r in rows if r['id'] == 'robertn_store'), None)
    return {'installed': installed is not None,
            'store': store_row['installed'] if store_row else None,
            'updates': sum(1 for r in rows if r['state'] == ROW_UPDATE),
            'missing': sum(1 for r in rows if r['state'] == ROW_MISSING)}


def write_state(state, path=STATE_FILE):
    """Save the result of a check for the GUI (written whole, then renamed)."""
    state = dict(state)
    state['checked'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.part', 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(path + '.part', path)
    return state


def read_state(path=STATE_FILE):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def uninstall(host, key_path, log):
    """Remove everything the Store setup put on the unit, with the installer's
    own `uninstall --yes` (the deploy tool's remove-all). Returns EXIT_DONE,
    EXIT_RESTART_NEEDED (removed; a restart finishes it) or EXIT_FAILED."""
    if not sshcli.available():
        log('OpenSSH client (ssh) not found on PATH; cannot verify the login. '
            'Install the Windows OpenSSH client or add ssh to PATH.')
        return EXIT_FAILED
    try:
        exe = ensure_installer(log)
        log('Removing the Store from the head unit...')
        rc, lines = run_installer(exe, host, key_path, log, extra=('--yes',), command='uninstall')
    except StoreError as e:
        log('Store removal failed: %s' % e)
        return EXIT_FAILED
    text = '\n'.join(lines)
    if rc != 0:
        log('Store removal not finished (installer exit %d); see its messages above.' % rc)
        return EXIT_FAILED
    if 'Restart the head unit to finish' in text:
        log('DONE: the Store is removed. Restart the head unit to finish.')
        return EXIT_RESTART_NEEDED
    log('DONE: the Store is removed from the head unit.')
    return EXIT_DONE


def restart(host, key_path, log):
    """Restart the head unit the way the Store does. Returns 0 on success."""
    rc, out, err = sshcli.run(host, key_path, RESTART, timeout=45)
    refused = None
    marker = out.find('[Error.')
    if marker >= 0:
        refused = out[marker + len('[Error.'):].split(']', 1)[0].strip()
    if rc == 3:
        log('An install is running on the head unit: restart it once it is done.')
        return 1
    if rc != 0 or refused is not None:
        log('The head unit did not take the restart: %s' % (refused or err.strip() or 'exit %d' % rc))
        return 1
    log('Restarting the head unit. The Store tile appears among the car\'s apps when it is back.')
    return 0
