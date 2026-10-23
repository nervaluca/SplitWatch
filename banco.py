"""Banco di memorie di FZR Skimmer: frequenze con nome, modo e gruppo, per tutti i ricevitori e tutti i modi.

Ogni memoria: {'id', 'nome', 'hz', 'modo', 'gruppo', 'x'} dove 'x' tiene le impostazioni che la memoria
si porta dietro (filtro, banda laterale, squelch…). Si salva in fzrskimmer.json, chiave 'banco'.
Al primo uso il banco parte con un gruppo di frequenze utili (si possono cancellare o rimettere).
"""

MODI = {'dx': 'CW DXpedition', 'contest': 'CW Contest', 'normale': 'CW Skimmer', 'rxcw': 'CW', 'ssb': 'SSB',
        'rtty': 'RTTY', 'ft8': 'DIGI', 'fm': 'FM', 'am': 'AM'}

MAX = 1000


def _ch(nome, mhz, modo, gruppo, **x):
    return {'nome': nome, 'hz': round(mhz * 1e6), 'modo': modo, 'gruppo': gruppo, 'x': x}


# frequenze standard (IARU Regione 1, piani di canale ufficiali): niente ripetitori o aeroporti locali
PRESET = (
    [_ch('Chiamata FM 2 m', 145.500, 'fm', 'Radioamatori VHF/UHF'),
     _ch('Chiamata FM 70 cm', 433.500, 'fm', 'Radioamatori VHF/UHF'),
     _ch('APRS', 144.800, 'fm', 'Radioamatori VHF/UHF'),
     _ch('ISS (voce, APRS)', 145.800, 'fm', 'Radioamatori VHF/UHF'),
     _ch('Chiamata SSB 2 m', 144.300, 'ssb', 'Radioamatori VHF/UHF'),
     _ch('Chiamata SSB 70 cm', 432.200, 'ssb', 'Radioamatori VHF/UHF'),
     _ch('FT8 2 m', 144.174, 'ft8', 'Radioamatori VHF/UHF'),
     _ch('FT8 6 m', 50.313, 'ft8', 'Radioamatori VHF/UHF'),
     _ch('Chiamata FM 6 m', 51.510, 'fm', 'Radioamatori VHF/UHF'),
     _ch('Chiamata FM 10 m', 29.600, 'fm', 'Radioamatori VHF/UHF')]
    + [_ch(f'R{i} (uscita)', 145.600 + 0.025 * i, 'fm', 'Ripetitori 2 m') for i in range(8)]
    + [_ch(f'PMR446 canale {i + 1}', 446.00625 + 0.0125 * i, 'fm', 'PMR446', fm_bw=10000) for i in range(16)]
    + [_ch('Emergenza aeronautica', 121.500, 'am', 'Aeronautica (AM)', am_bw=8000),
       _ch('Aria-aria', 123.450, 'am', 'Aeronautica (AM)', am_bw=8000)]
    + [_ch('Canale 16 (soccorso e chiamata)', 156.800, 'fm', 'Nautica (FM)'),
       _ch('Canale 6 (nave-nave)', 156.300, 'fm', 'Nautica (FM)')]
)


def _all(cfg):
    if 'banco' not in cfg:                         # primo uso: si parte con i preset
        cfg['banco'] = []
        preset(cfg)
    return cfg['banco']


def _new_id(cfg):
    n = int(cfg.get('banco_n', 0)) + 1
    cfg['banco_n'] = n
    return str(n)


def lista(cfg):
    return _all(cfg)


def versione(cfg):
    """Cambia a ogni modifica: la pagina chiede l'elenco solo quando serve."""
    b = _all(cfg)
    return f"{cfg.get('banco_n', 0)}.{len(b)}.{cfg.get('banco_mod', 0)}"


def toccato(cfg):
    cfg['banco_mod'] = int(cfg.get('banco_mod', 0)) + 1


def gruppi(cfg):
    out = []
    for m in _all(cfg):
        g = m.get('gruppo') or ''
        if g not in out:
            out.append(g)
    return out


def trova(cfg, mid):
    return next((m for m in _all(cfg) if m['id'] == str(mid)), None)


def aggiungi(cfg, nome, hz, modo, gruppo='', x=None):
    b = _all(cfg)
    nome = (nome or '').strip()[:40] or f'{hz / 1e6:.4f} {MODI.get(modo, modo)}'
    m = {'id': _new_id(cfg), 'nome': nome, 'hz': round(float(hz)), 'modo': modo,
         'gruppo': (gruppo or '').strip()[:30] or 'Mie memorie', 'x': dict(x or {})}
    b.append(m)
    del b[:-MAX]
    return m


def modifica(cfg, mid, v):
    m = trova(cfg, mid)
    if not m:
        return None
    if 'nome' in v and str(v['nome']).strip():
        m['nome'] = str(v['nome']).strip()[:40]
    if 'gruppo' in v:
        m['gruppo'] = str(v['gruppo']).strip()[:30] or 'Mie memorie'
    if 'hz' in v:
        try:
            hz = float(v['hz'])
            if 9e3 <= hz <= 2e9:
                m['hz'] = round(hz)
        except (TypeError, ValueError):
            pass
    if v.get('modo') in MODI:
        m['modo'] = v['modo']
    return m


def cancella(cfg, mid):
    b = _all(cfg)
    b[:] = [m for m in b if m['id'] != str(mid)]


def preset(cfg):
    """Aggiunge le memorie predefinite che mancano (stessa frequenza e modo = già presente)."""
    b = cfg.setdefault('banco', [])
    have = {(m['hz'], m['modo']) for m in b}
    n = 0
    for p in PRESET:
        if (p['hz'], p['modo']) not in have:
            b.append({'id': _new_id(cfg), **p, 'x': dict(p['x'])})
            n += 1
    return n
