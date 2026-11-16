"""Diagnostica completa in SOLA LETTURA della head unit MGU.

Legge da sola, senza scegliere niente: gateway ENET, VIN, IP della MGU, tutte le
versioni software (SVK: HWEL/BTLD/SWFL/SWFK/CAFD), il CAFD, il numero di serie,
gli identificativi 0xF100-0xF1FF (part number ecc.), gli errori (DTC) e la
variante riconosciuta (ID7 / ID8). Scrive un report leggibile + un JSON e apre
la cartella.

Ogni lettura è UDS 0x22 nella sessione di default o 0x19 (lettura errori): non
apre sessioni di programmazione, non fa SecurityAccess, non scrive mai nulla.
Non può cambiare la vettura.

Uso headless (lo chiama MGU Plus con --diagnostica); torna 0 se ha scritto il
report, 1 se non è riuscito nemmeno a collegarsi.
"""
import datetime
import json
import os
import subprocess
import sys

if getattr(sys, "frozen", False):
    HERE = os.path.dirname(os.path.abspath(sys.executable))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))

from mgu7 import discovery, hsfz, diag, preflight


def _read_did(client, ident):
    try:
        return preflight._read_did(client, hsfz.MGU, ident)
    except (hsfz.HsfzError, hsfz.UdsReject, diag.DiagError, OSError):
        return None


def _text(body):
    if not body:
        return None
    try:
        t = body.decode("ascii", "replace").strip("\x00 ")
    except Exception:
        t = ""
    return t or body.hex().upper()


def collect(gateway="auto", mgu_ip=None, log=print):
    """Legge tutto dalla vettura (sola lettura). -> dict. Solleva solo se non
    riesce a collegarsi al gateway/alla MGU."""
    out = {"strumento": "MGU Plus - diagnostica", "quando": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S") + " UTC",
           "gateway": None, "vin": None, "mgu_ip": None, "svk": [], "svk_riassunto": None,
           "cafd": [], "hardware": None, "seriale": None, "ident": {}, "dtc": [], "variante": None, "batteria": None}

    gw = gateway
    found_vin = None
    if gateway == "auto" or not gateway:
        log("Cerco il gateway ENET (broadcast UDP 6811)...")
        gws = discovery.discover()
        if not gws:
            raise RuntimeError("Nessun gateway ENET ha risposto. Controlla il cavo ENET, "
                               "che sia nella presa OBD e che il quadro sia acceso in diagnosi.")
        gw = gws[0].ip
        found_vin = gws[0].vin
        log("Gateway trovato: %s%s" % (gw, " | VIN " + found_vin if found_vin else ""))
    out["gateway"] = gw

    client = hsfz.EnetClient(gw)
    client.connect()
    try:
        log("Leggo le versioni software (SVK)...")
        svk = diag.read_svk(client)
        out["svk"] = [str(r) for r in svk.records]
        out["svk_riassunto"] = svk.summary()
        out["cafd"] = [str(r) for r in svk.all_named("CAFD")]
        out["hardware"] = ", ".join(str(r) for r in svk.all_named("HWEL")) or None
        log("  %d record software, %d CAFD." % (len(svk.records), len(svk.all_named("CAFD"))))

        try:
            out["variante"] = diag.detect_variant(svk)
            log("  Variante: %s" % out["variante"]["it"])
        except Exception as e:
            log("  (riconoscimento variante saltato: %s)" % e)

        try:
            volts = diag.read_battery(client)
            out["batteria"] = volts
            log("  Batteria: %s" % ("%.1f V" % volts if volts is not None else "non leggibile"))
        except (hsfz.HsfzError, hsfz.UdsReject, diag.DiagError, OSError) as e:
            log("  (batteria non letta: %s)" % e)

        vin = _text(_read_did(client, 0xF190))
        out["vin"] = vin or found_vin
        out["seriale"] = _text(_read_did(client, 0xF18C))
        log("  VIN %s | seriale %s" % (out["vin"] or "?", out["seriale"] or "?"))

        try:
            out["mgu_ip"] = list(diag.read_mgu_ip(client))
            log("  IP MGU: %s" % ", ".join(out["mgu_ip"]))
        except (hsfz.HsfzError, hsfz.UdsReject, diag.DiagError) as e:
            log("  (IP MGU non letto: %s)" % e)

        log("Leggo gli identificativi 0xF100-0xF1FF (part number ecc.)...")
        ident = {}
        for d in range(0xF100, 0xF200):
            body = _read_did(client, d)
            if body:
                ident["%04X" % d] = {"hex": body.hex(), "testo": _text(body)}
        out["ident"] = ident
        log("  %d identificativi presenti." % len(ident))

        try:
            log("Leggo la memoria errori (DTC)...")
            out["dtc"] = diag.read_dtcs(client)
            log("  %d codici di errore." % len(out["dtc"]))
        except (hsfz.HsfzError, hsfz.UdsReject, diag.DiagError) as e:
            log("  (DTC non letti: %s)" % e)
    finally:
        try:
            client.close()
        except OSError:
            pass
    return out


def _report_text(d):
    v = d.get("variante") or {}
    lines = [
        "MGU Plus - diagnostica completa della vettura (sola lettura)",
        "============================================================",
        "Quando:   %s" % d["quando"],
        "Gateway:  %s" % (d.get("gateway") or "?"),
        "VIN:      %s" % (d.get("vin") or "?"),
        "IP MGU:   %s" % (", ".join(d["mgu_ip"]) if d.get("mgu_ip") else "?"),
        "Seriale:  %s" % (d.get("seriale") or "?"),
        "Hardware: %s" % (d.get("hardware") or "?"),
        "Batteria: %s" % (("%.1f V" % d["batteria"]) if d.get("batteria") is not None else "non leggibile"),
        "",
        "VARIANTE RICONOSCIUTA",
        "  %s" % (v.get("it") or "non disponibile"),
        "  (certezza: %s)" % (v.get("confidence") or "?"),
        "",
        "CAFD (codifica):",
    ]
    lines += ["  " + c for c in (d.get("cafd") or ["(nessuno)"])]
    lines += ["", "TUTTO IL SOFTWARE (SVK):"]
    lines += ["  " + r for r in (d.get("svk") or ["(non letto)"])]
    lines += ["", "IDENTIFICATIVI 0xF100-0xF1FF:"]
    if d.get("ident"):
        for k in sorted(d["ident"]):
            t = d["ident"][k]["testo"] or ""
            h = d["ident"][k]["hex"]
            printable = len(t) <= 40 and any(c.isalnum() for c in t) and all(32 <= ord(c) < 127 for c in t)
            lines.append("  %s: %s" % (k, t if printable else h))
    else:
        lines.append("  (nessuno)")
    lines += ["", "ERRORI (DTC) al momento della lettura (%d):" % len(d.get("dtc") or [])]
    lines.append("  " + (", ".join(d.get("dtc") or []) or "nessuno"))
    lines += ["", "Nota: tutte letture 0x22/0x19. Nessuna scrittura, nessuna sessione di",
              "programmazione, nessun SecurityAccess. La vettura non è stata modificata."]
    return "\n".join(lines) + "\n"


def run(gateway="auto", mgu_ip=None, open_folder=True):
    folder = os.path.join(HERE, "data", "diagnostica")
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    log_lines = []

    def log(m):
        log_lines.append(str(m))

    try:
        data = collect(gateway=gateway, mgu_ip=mgu_ip, log=log)
    except Exception as e:
        log("ERRORE: %s" % e)
        path = os.path.join(folder, "diagnostica-NON-RIUSCITA-%s.txt" % stamp)
        with open(path, "w", encoding="utf-8") as f:
            f.write("MGU Plus - diagnostica NON riuscita\n===================================\n\n")
            f.write("\n".join(log_lines) + "\n\n")
            f.write("Controlla: cavo ENET nella presa OBD, quadro in diagnosi (3 pressioni\n"
                    "rapide di Start/Stop), e riprova.\n")
        if open_folder:
            _open(folder)
        return 1, path

    vin = "".join(c for c in (data.get("vin") or "SENZA-VIN") if c.isalnum()) or "SENZA-VIN"
    base = os.path.join(folder, "diagnostica-%s-%s" % (vin, stamp))
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write(_report_text(data))
        f.write("\n--- registro della lettura ---\n")
        f.write("\n".join(log_lines) + "\n")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    if open_folder:
        _open(folder)
    return 0, base + ".txt"


def _open(folder):
    try:
        if sys.platform.startswith("win"):
            os.startfile(folder)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except Exception:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Diagnostica MGU in sola lettura")
    ap.add_argument("--gateway", default="auto")
    ap.add_argument("--mgu-ip", default=None)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    code, path = run(gateway=a.gateway, mgu_ip=a.mgu_ip, open_folder=not a.no_open)
    print(path)
    sys.exit(code)
