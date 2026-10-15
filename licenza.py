# -*- coding: utf-8 -*-
"""
licenza.py — Registrazione FZR Skimmer (stesse chiavi di ADIF FZR) (firma crittografica Ed25519).

MODELLO DI SICUREZZA (robusto):
  * Ogni licenza e' una FIRMA Ed25519 del messaggio  "NOMINATIVO|LIVELLO".
  * L'app contiene SOLO la CHIAVE PUBBLICA (_PUB_HEX): puo' verificare, non firmare.
  * La CHIAVE PRIVATA sta unicamente nel generatore  keygen_adiffzr.py  (di Luca).
    => Anche decompilando l'exe NON si possono creare chiavi valide.
  * Verifica 100% OFFLINE: nessun server, nessun database.

Livelli:
  REG   = registrato        -> "Registered to CALL"
  DONOR = sostenitore        -> "Registered to CALL  ♥ Donor"  (sblocca funzioni extra)

La chiave consegnata all'utente e' la firma (64 byte) in Base32, a gruppi.
E' lunga (~104 caratteri): si incolla, non si digita a mano.
"""

import os
import re
import json
import base64

import fzr_ed25519 as _ed

# ---------------------------------------------------------------------------
# CHIAVE PUBBLICA dell'app (l'unica cosa che viaggia nell'exe).
# La coppia e' stata generata una volta sola; la privata sta nel keygen.
# ---------------------------------------------------------------------------
_PUB_HEX = "07c36f323723406305b854633c588c2e05578f600d4e34fd3a60df5503d87e7d"
_PUB = bytes.fromhex(_PUB_HEX)

LIVELLI = {
    "REG":   "Registered to {call}",
    "DONOR": "Registered to {call}  ♥ Donor",
}
_ORDINE_LIVELLI = ["DONOR", "REG"]

_LEN_GRUPPO = 8   # solo estetica quando si formatta la chiave


# ---------------------------------------------------------------------------
# Nominativo
# ---------------------------------------------------------------------------
def normalizza_call(call):
    """MAIUSCOLO, senza spazi, tiene lettere/cifre/'/'.  'iu1pjo/p' -> 'IU1PJO/P'."""
    if not call:
        return ""
    c = str(call).strip().upper()
    return re.sub(r"[^A-Z0-9/]", "", c)


def _messaggio(call_norm, livello):
    return ("%s|%s" % (call_norm, livello)).encode("utf-8")


# ---------------------------------------------------------------------------
# Codifica/decodifica chiave (Base32 della firma a 64 byte)
# ---------------------------------------------------------------------------
def _pulisci(chiave):
    """Toglie tutto tranne l'alfabeto Base32 (A-Z 2-7), in MAIUSCOLO."""
    if not chiave:
        return ""
    return re.sub(r"[^A-Z2-7]", "", str(chiave).upper())


def codifica_firma(sig):
    """64 byte -> stringa Base32 senza '=', a gruppi separati da '-'."""
    b32 = base64.b32encode(sig).decode("ascii").replace("=", "")
    return "-".join(b32[i:i + _LEN_GRUPPO] for i in range(0, len(b32), _LEN_GRUPPO))


def decodifica_chiave(chiave):
    """Stringa chiave -> 64 byte di firma (o None se malformata)."""
    d = _pulisci(chiave)
    if not d:
        return None
    # ripristina il padding Base32 a multipli di 8
    pad = (-len(d)) % 8
    try:
        raw = base64.b32decode(d + ("=" * pad))
    except Exception:
        return None
    return raw if len(raw) == 64 else None


# ---------------------------------------------------------------------------
# Verifica
# ---------------------------------------------------------------------------
def verifica(call, chiave):
    """Ritorna (ok, livello|None, call_norm). Prova i livelli noti."""
    cn = normalizza_call(call)
    if not cn:
        return (False, None, "")
    sig = decodifica_chiave(chiave)
    if sig is None:
        return (False, None, cn)
    for liv in _ORDINE_LIVELLI:
        if _ed.verifica(sig, _messaggio(cn, liv), _PUB):
            return (True, liv, cn)
    return (False, None, cn)


def testo_barra(call, livello):
    modello = LIVELLI.get((livello or "REG").upper(), LIVELLI["REG"])
    return modello.format(call=normalizza_call(call))


def is_donor(livello):
    return (livello or "").upper() == "DONOR"


# ---------------------------------------------------------------------------
# Persistenza
# ---------------------------------------------------------------------------
def _percorso(cartella):
    return os.path.join(cartella, "licenza.json")


def carica(cartella):
    """Carica e RIVERIFICA la licenza. dict{call,livello,chiave,testo,donor} o None."""
    try:
        p = _percorso(cartella)
        if not os.path.exists(p):
            return None
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        ok, liv, cn = verifica(d.get("call", ""), d.get("chiave", ""))
        if not ok:
            return None
        return {"call": cn, "livello": liv, "chiave": d.get("chiave", ""),
                "testo": testo_barra(cn, liv), "donor": is_donor(liv)}
    except Exception:
        return None


def salva(cartella, call, chiave):
    """Valida e salva. Ritorna (ok, info_dict|messaggio_errore)."""
    ok, liv, cn = verifica(call, chiave)
    if not ok:
        return (False, "Chiave non valida per il nominativo indicato.")
    try:
        os.makedirs(cartella, exist_ok=True)
        chiave_fmt = codifica_firma(decodifica_chiave(chiave))
        with open(_percorso(cartella), "w", encoding="utf-8") as f:
            json.dump({"call": cn, "livello": liv, "chiave": chiave_fmt},
                      f, ensure_ascii=False, indent=2)
    except Exception as e:
        return (False, "Impossibile salvare la licenza: %s" % e)
    return (True, {"call": cn, "livello": liv, "chiave": chiave_fmt,
                   "testo": testo_barra(cn, liv), "donor": is_donor(liv)})


def rimuovi(cartella):
    try:
        p = _percorso(cartella)
        if os.path.exists(p):
            os.remove(p)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    # Prova solo la VERIFICA con una chiave passata a mano:
    #   python licenza.py IU1PJO  <chiave>
    import sys
    if len(sys.argv) >= 3:
        print(verifica(sys.argv[1], sys.argv[2]))
    else:
        print("Uso: python licenza.py CALL CHIAVE")
        print("(la generazione delle chiavi e' in keygen_adiffzr.py)")
