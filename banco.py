"""Banco di memorie di FZR Skimmer: frequenze con nome, modo e gruppo, per tutti i ricevitori e tutti i modi.

Ogni memoria: {'id', 'nome', 'hz', 'modo', 'gruppo', 'x'} dove 'x' tiene le impostazioni che la memoria
si porta dietro (filtro, banda laterale, squelch…). Si salva in fzrskimmer.json, chiave 'banco'.
Al primo uso il banco parte con un gruppo di frequenze utili (si possono cancellare o rimettere).
"""

MODI = {'dx': 'CW DXpedition', 'contest': 'CW Contest', 'normale': 'CW Skimmer', 'rxcw': 'CW', 'ssb': 'SSB',
        'rtty': 'RTTY', 'ft8': 'DIGI', 'fm': 'FM', 'am': 'AM'}

MAX = 3000


def _ch(nome, mhz, modo, gruppo, **x):
    return {'nome': nome, 'hz': round(mhz * 1e6), 'modo': modo, 'gruppo': gruppo, 'x': x}


# frequenze standard (IARU Regione 1, piani di canale ufficiali): niente ripetitori o aeroporti locali
_R2, _R6, _R70, _R23, _R10 = 'Radioamatori 2 m', 'Radioamatori 6 m', 'Radioamatori 70 cm', 'Radioamatori 23 cm', 'Radioamatori 10 m'
# canali CB (CEPT, 40 canali): il 23 sta fra il 25 e il 26
_CB = [26.965, 26.975, 26.985, 27.005, 27.015, 27.025, 27.035, 27.055, 27.065, 27.075, 27.085, 27.105, 27.115,
       27.125, 27.135, 27.155, 27.165, 27.175, 27.185, 27.205, 27.215, 27.225, 27.255, 27.235, 27.245, 27.265,
       27.275, 27.285, 27.295, 27.305, 27.315, 27.325, 27.335, 27.345, 27.355, 27.365, 27.375, 27.385, 27.395, 27.405]
PRESET = (
    [_ch('Chiamata SSB 6 m', 50.150, 'ssb', _R6),
     _ch('FT8 6 m', 50.313, 'ft8', _R6),
     _ch('Chiamata FM 6 m', 51.510, 'fm', _R6),
     _ch('Chiamata CW 2 m', 144.050, 'rxcw', _R2),
     _ch('FT8 2 m', 144.174, 'ft8', _R2),
     _ch('Chiamata SSB 2 m', 144.300, 'ssb', _R2),
     _ch('APRS', 144.800, 'fm', _R2),
     _ch('Chiamata FM 2 m', 145.500, 'fm', _R2),
     _ch('ISS voce (downlink)', 145.800, 'fm', _R2),
     _ch('ISS APRS (digipeater)', 145.825, 'fm', _R2),
     _ch('Chiamata CW 70 cm', 432.050, 'rxcw', _R70),
     _ch('FT8 70 cm', 432.174, 'ft8', _R70),
     _ch('Chiamata SSB 70 cm', 432.200, 'ssb', _R70),
     _ch('Chiamata FM 70 cm', 433.500, 'fm', _R70),
     _ch('ISS ripetitore (downlink)', 437.800, 'fm', _R70),
     _ch('Chiamata CW 23 cm', 1296.050, 'rxcw', _R23),
     _ch('Chiamata SSB 23 cm', 1296.200, 'ssb', _R23),
     _ch('Chiamata FM 23 cm', 1297.500, 'fm', _R23),
     _ch('Chiamata FM 10 m', 29.600, 'fm', _R10)]
    + [_ch(f'R{i} (uscita)', 145.600 + 0.025 * i, 'fm', 'Ripetitori 2 m') for i in range(8)]
    + [_ch(f'R{i}x (uscita, 12,5 kHz)', 145.6125 + 0.025 * i, 'fm', 'Ripetitori 2 m', fm_bw=10000) for i in range(8)]
    + [_ch(f'CB canale {i + 1}', f, 'am', 'CB 27 MHz', am_bw=8000) for i, f in enumerate(_CB)]
    + [_ch(f'PMR446 canale {i + 1}', 446.00625 + 0.0125 * i, 'fm', 'PMR446', fm_bw=10000) for i in range(16)]
    + [_ch(f'LPD canale {i + 1}', 433.075 + 0.025 * i, 'fm', 'LPD 433', fm_bw=12000) for i in range(69)]
    + [_ch('Emergenza aeronautica', 121.500, 'am', 'Aeronautica (AM)', am_bw=8000),
       _ch('Aria-aria', 123.450, 'am', 'Aeronautica (AM)', am_bw=8000),
       _ch('Emergenza militare (UHF)', 243.000, 'am', 'Aeronautica (AM)', am_bw=8000)]
    + [_ch('Canale 16 (soccorso e chiamata)', 156.800, 'fm', 'Nautica (FM)'),
       _ch('Canale 6 (nave-nave)', 156.300, 'fm', 'Nautica (FM)'),
       _ch('Canale 9 (chiamata, porti turistici)', 156.450, 'fm', 'Nautica (FM)'),
       _ch('Canale 13 (sicurezza della navigazione)', 156.650, 'fm', 'Nautica (FM)'),
       _ch('Canale 70 (DSC, dati)', 156.525, 'fm', 'Nautica (FM)'),
       _ch('AIS 1 (dati delle navi)', 161.975, 'fm', 'Nautica (FM)'),
       _ch('AIS 2 (dati delle navi)', 162.025, 'fm', 'Nautica (FM)')]
)


def _mhz(f):
    t = f'{f:.4f}'.rstrip('0')
    return (t + '0' * (3 - len(t.split('.')[1]))).replace('.', ',')


# arrivati con la versione 3 del banco (FZR Skimmer 1.50): canali dei ripetitori UHF e aeroporti del Nord-Ovest
_AER = 'Aeroporti Nord-Ovest (AM)'
_V3 = (
    # ponti 70 cm: le tre griglie usate in Italia (uscita; l'ingresso è l'uscita + lo shift)
    [_ch(f'{_mhz(f)} (+1,6)', f, 'fm', 'Ripetitori 70 cm (shift +1,6 MHz)', fm_bw=10000)
     for f in [430.0125 + 0.0125 * i for i in range(32)] + [431.2125 + 0.0125 * i for i in range(32)]]
    + [_ch(f'{_mhz(f)} (+5)', f, 'fm', 'Ripetitori 70 cm (shift +5 MHz)', fm_bw=10000)
       for f in [430.500 + 0.0125 * i for i in range(49)]]
    + [_ch(f'{_mhz(f)} (−7,6)', f, 'fm', 'Ripetitori 70 cm (shift −7,6 MHz, IARU)')
       for f in [438.650 + 0.025 * i for i in range(32)]]
    # aeroporti vicini (AIP Italia / ENAV): le frequenze ogni tanto cambiano
    + [_ch(n, f, 'am', _AER, am_bw=8000) for n, f in (
        ('Malpensa ATIS arrivi', 120.025), ('Malpensa ATIS partenze', 121.625), ('Malpensa TWR', 119.000),
        ('Malpensa TWR 2', 128.350), ('Malpensa GND', 121.900), ('Linate ATIS', 136.375), ('Linate TWR', 118.100),
        ('Linate GND', 121.800), ('Milano Radar APP', 126.750), ('Milano Radar APP 2', 132.700),
        ('Milano Radar APP 3', 134.175), ('Milano Informazioni (FIS)', 124.925), ('Caselle ATIS', 120.475),
        ('Caselle TWR', 118.500), ('Caselle GND', 121.700), ('Torino APP', 129.275), ('Torino APP 2', 125.275),
        ('Aosta AFIS', 119.950), ('Ricerca e soccorso (SAR)', 123.100))]
)
PRESET = PRESET + _V3
_V3_KEYS = {(p['hz'], p['modo']) for p in _V3}
# preset della prima versione (1.40): servono per l'aggiornamento del banco
_OLD = {(145500000, 'fm'), (433500000, 'fm'), (144800000, 'fm'), (145800000, 'fm'), (144300000, 'ssb'),
        (432200000, 'ssb'), (144174000, 'ft8'), (50313000, 'ft8'), (51510000, 'fm'), (29600000, 'fm'),
        (121500000, 'am'), (123450000, 'am'), (156800000, 'fm'), (156300000, 'fm')} | \
       {(round((145.600 + 0.025 * i) * 1e6), 'fm') for i in range(8)} | \
       {(round((446.00625 + 0.0125 * i) * 1e6), 'fm') for i in range(16)}
VERSIONE = 3


def _aggiorna(cfg):
    """Banco di una versione precedente: arrivano i preset nuovi.
    Le memorie tue (o quelle che hai rinominato) non si toccano; i preset che avevi cancellato non tornano."""
    b = cfg['banco']
    if int(cfg.get('banco_ver', 1)) >= 2:          # dalla versione 2: solo i preset della 3
        have = {(m['hz'], m['modo']) for m in b}
        for p in _V3:
            if (p['hz'], p['modo']) not in have:
                b.append({'id': _new_id(cfg), **p, 'x': dict(p['x'])})
        cfg['banco_ver'] = VERSIONE
        return
    # banco della 1.40: i preset vecchi passano nei gruppi nuovi per banda
    per = {(p['hz'], p['modo']): p for p in PRESET}
    vecchi = {'Chiamata FM 2 m', 'Chiamata FM 70 cm', 'APRS', 'ISS (voce, APRS)', 'Chiamata SSB 2 m',
              'Chiamata SSB 70 cm', 'FT8 2 m', 'FT8 6 m', 'Chiamata FM 6 m', 'Chiamata FM 10 m'}
    for m in b:
        p = per.get((m['hz'], m['modo']))
        if p and m.get('gruppo') == 'Radioamatori VHF/UHF' and m.get('nome') in vecchi:
            m['gruppo'], m['nome'] = p['gruppo'], p['nome']
    have = {(m['hz'], m['modo']) for m in b}
    for p in PRESET:
        k = (p['hz'], p['modo'])
        if k not in _OLD and k not in have:
            b.append({'id': _new_id(cfg), **p, 'x': dict(p['x'])})
    cfg['banco_ver'] = VERSIONE


def _all(cfg):
    if 'banco' not in cfg:                         # primo uso: si parte con i preset
        cfg['banco'] = []
        preset(cfg)
        cfg['banco_ver'] = VERSIONE
    elif int(cfg.get('banco_ver', 1)) < VERSIONE:  # banco di una versione precedente
        _aggiorna(cfg)
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
    """Prima i gruppi tuoi (nell'ordine in cui li hai creati), poi quelli predefiniti per frequenza."""
    pres = []
    for p in sorted(PRESET, key=lambda p: p['hz']):
        if p['gruppo'] not in pres:
            pres.append(p['gruppo'])
    out = []
    for m in _all(cfg):
        g = m.get('gruppo') or ''
        if g not in out and g not in pres:
            out.append(g)
    have = {m.get('gruppo') for m in _all(cfg)}
    return out + [g for g in pres if g in have]


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


def importa(cfg, nuove, fonte, gruppi_nuovi):
    """Memorie da un elenco (ponti IK2ANE, CHIRP): quelle della stessa fonte nei gruppi importati vengono
    sostituite (elenco aggiornato), le tue non si toccano."""
    b = _all(cfg)
    gset = set(gruppi_nuovi)
    b[:] = [m for m in b if not (m.get('fonte') == fonte and m.get('gruppo') in gset)]
    for m in nuove:
        b.append({'id': _new_id(cfg), **m})
    del b[:-MAX]
    return len(nuove)


def cancella_gruppo(cfg, gruppo):
    b = _all(cfg)
    n = len(b)
    b[:] = [m for m in b if m.get('gruppo') != gruppo]
    return n - len(b)


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
