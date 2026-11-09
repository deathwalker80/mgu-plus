"""Controllo aggiornamenti upstream di MGU Plus (eseguito da GitHub Actions).

Confronta upstream.json con quello che pubblicano oggi:
  - idrive7-8-ssh-root (root SSH): ultima release e commit del ramo main;
  - mgu-store: commit che toccano l'installer (deploy/mgu-install.js) e
    l'SHA256SUMS della release "installer" che MGU Plus scarica.
Per ogni novità apre una issue nel repository (una sola per versione), con
l'elenco dei commit, i file cambiati e le righe dei parametri della riga di
comando aggiunte o tolte: sono quelle da riportare in MGU Plus.
Solo libreria standard di Python.
"""

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
TOKEN = os.environ.get("GH_TOKEN", "")
THIS_REPO = os.environ.get("GITHUB_REPOSITORY", "deathwalker80/mgu-plus")
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def request(url, data=None, raw=False):
    headers = {"User-Agent": "mgu-plus-upstream-check", "Accept": "application/vnd.github+json"}
    if TOKEN and url.startswith(API):
        headers["Authorization"] = "Bearer " + TOKEN
    body = json.dumps(data).encode() if data is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = r.read()
    return payload if raw else json.loads(payload.decode("utf-8"))


def param_lines(files, wanted):
    """Righe aggiunte/tolte che definiscono parametri della riga di comando."""
    out = []
    for f in files:
        if f.get("filename") not in wanted:
            continue
        for line in (f.get("patch") or "").splitlines():
            if not line[:1] in "+-" or line.startswith(("+++", "---")):
                continue
            text = line[1:].strip()
            if ("add_argument" in text or "choices=" in text or "a === '--" in text
                    or "a === \"--" in text or text.startswith(("node mgu-install.js", "| `--"))):
                out.append("- %s `%s`" % ("aggiunto:" if line[0] == "+" else "tolto:", text[:220]))
    return out


def compare(repo, base, head):
    data = request("%s/repos/%s/compare/%s...%s" % (API, repo, base, head))
    commits = ["- [`%s`](%s) %s" % (c["sha"][:7], c["html_url"], c["commit"]["message"].splitlines()[0])
               for c in data.get("commits", [])]
    files = data.get("files", [])
    return data, commits, files


def open_titles():
    titles = set()
    page = 1
    while True:
        issues = request("%s/repos/%s/issues?state=open&per_page=100&page=%d" % (API, THIS_REPO, page))
        titles.update(i["title"] for i in issues if "pull_request" not in i)
        if len(issues) < 100:
            return titles
        page += 1


def open_issue(title, body, existing):
    if title in existing:
        print("già aperta:", title)
        return
    if DRY_RUN:
        print("=== %s ===\n%s\n" % (title, body))
        return
    issue = request("%s/repos/%s/issues" % (API, THIS_REPO), {"title": title, "body": body})
    print("aperta:", issue.get("html_url"))


def check_root_tool(cfg, existing):
    repo = cfg["repo"]
    head = request("%s/repos/%s/commits/%s" % (API, repo, cfg["branch"]))["sha"]
    try:
        release = request("%s/repos/%s/releases/latest" % (API, repo))
        tag, notes, url = release["tag_name"], release.get("body") or "", release["html_url"]
    except urllib.error.HTTPError:
        tag, notes, url = cfg["release"], "", ""
    if head == cfg["commit"] and tag == cfg["release"]:
        print("idrive7-8-ssh-root: nessuna novità (%s, %s)" % (tag, head[:7]))
        return
    data, commits, files = compare(repo, cfg["commit"], head)
    params = param_lines(files, {"ssh_enable.py", "gui.py"})
    title = "Upstream idrive7-8-ssh-root: %s (%s) da integrare" % (tag, head[:7])
    body = ["Novità nel tool root SSH su cui si basa MGU Plus, rispetto a `%s` (%s)." % (cfg["release"], cfg["commit"][:7]),
            "", "Confronto: %s" % data.get("html_url", ""), ""]
    if tag != cfg["release"]:
        body += ["### Nuova release: %s" % tag, url, "", notes[:4000], ""]
    body += ["### Parametri della riga di comando cambiati"] + (params or ["- nessuno"]) + [""]
    body += ["### Commit (%d)" % len(commits)] + commits[:60] + [""]
    body += ["### File cambiati"] + ["- `%s` (+%d / -%d)" % (f["filename"], f.get("additions", 0), f.get("deletions", 0))
                                     for f in files[:80]]
    body += ["", "Dopo l'integrazione aggiornare `root_tool` in `upstream.json`."]
    open_issue(title, "\n".join(body), existing)


def check_mgu_store(cfg, existing):
    repo = cfg["repo"]
    commits = request("%s/repos/%s/commits?sha=%s&path=%s&per_page=1" % (API, repo, cfg["branch"], cfg["installer_path"]))
    head = commits[0]["sha"] if commits else cfg["installer_commit"]
    sums = request("https://github.com/%s/releases/download/installer/SHA256SUMS" % repo, raw=True)
    sums_sha = hashlib.sha256(sums).hexdigest()
    if head == cfg["installer_commit"] and sums_sha == cfg["installer_sha256sums"]:
        print("mgu-store installer: nessuna novità (%s)" % head[:7])
        return
    title = "mgu-store: installer cambiato (%s) da verificare" % head[:7]
    body = ["L'installer di mgu-store usato da MGU Plus (`%s` e la release `installer`) è cambiato." % cfg["installer_path"], ""]
    if sums_sha != cfg["installer_sha256sums"]:
        body += ["- Nuovi eseguibili nella release `installer` (SHA256SUMS cambiato): MGU Plus li scarica già da solo, "
                 "verificare solo che i parametri usati funzionino ancora.", ""]
    if head != cfg["installer_commit"]:
        data, lines, files = compare(repo, cfg["installer_commit"], head)
        wanted = {cfg["installer_path"], "deploy/README.md"}
        params = param_lines(files, wanted)
        mine = [f for f in files if f["filename"] in wanted]
        body += ["Confronto: %s" % data.get("html_url", ""), "",
                 "### Parametri / opzioni cambiati in mgu-install"] + (params or ["- nessuno"]) + [""]
        body += ["### File dell'installer cambiati"] + ["- `%s` (+%d / -%d)" % (f["filename"], f.get("additions", 0),
                                                                                f.get("deletions", 0)) for f in mine]
    body += ["", "MGU Plus usa: `first-install` / `uninstall` con `--identity`, `--host`, `--no-restart-prompt`, "
             "`--ssh-opt`, `--yes`; legge `status --json` del deploy tool e le righe di esito dell'installer "
             "(mgu7/store.py).", "", "Dopo la verifica aggiornare `mgu_store` in `upstream.json`."]
    open_issue(title, "\n".join(body), existing)


def main():
    with open("upstream.json", encoding="utf-8") as f:
        cfg = json.load(f)
    existing = set() if DRY_RUN else open_titles()
    failed = False
    for name, fn in (("root_tool", check_root_tool), ("mgu_store", check_mgu_store)):
        try:
            fn(cfg[name], existing)
        except (urllib.error.URLError, KeyError, ValueError) as e:
            failed = True
            print("%s: controllo non riuscito: %s" % (name, e), file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
