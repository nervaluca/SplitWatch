"""Import di elenchi di ponti / canali nel banco di memorie.

- Elenco ponti di IK2ANE (Walter De Vercelli), file pontixls.xls da https://www.ik2ane.it/ham.htm
  («Si autorizzano tutti i radioamatori ad utilizzare gratuitamente per uso privato il presente elenco
  citandone gli autori»): il file lo scarica l'utente, qui si legge soltanto.
- File CSV di CHIRP (i canali programmati nei portatili).

Ogni riga diventa un dizionario semplice; la scelta (province, distanza, bande) la fa la pagina.
"""
import csv
import math
import os
import re

FONTE_IK2ANE = 'ik2ane'
FONTE_CHIRP = 'chirp'

BANDE = (('10', 28.0e6, 29.7e6), ('6', 50.0e6, 52.0e6), ('2', 144.0e6, 146.0e6), ('70', 430.0e6, 440.0e6),
         ('23', 1240.0e6, 1300.0e6))


def banda(hz):
    for b, lo, hi in BANDE:
        if lo <= hz <= hi:
            return b
    return 'x'                                   # fuori dalle bande radioamatoriali


def loc2ll(loc):
    """Locatore Maidenhead (4, 6 o 8 caratteri) -> (lat, lon) del centro del quadrato."""
    loc = (loc or '').strip().upper()
    if not re.fullmatch(r'[A-R]{2}\d{2}([A-X]{2}(\d{2})?)?', loc):
        return None
    lon = (ord(loc[0]) - 65) * 20 - 180 + int(loc[2]) * 2
    lat = (ord(loc[1]) - 65) * 10 - 90 + int(loc[3])
    dlon, dlat = 2.0, 1.0
    if len(loc) >= 6:
        dlon, dlat = 2 / 24, 1 / 24
        lon += (ord(loc[4]) - 65) * dlon
        lat += (ord(loc[5]) - 65) * dlat
    if len(loc) >= 8:
        dlon, dlat = dlon / 10, dlat / 10
        lon += int(loc[6]) * dlon
        lat += int(loc[7]) * dlat
    return lat + dlat / 2, lon + dlon / 2


def km(a, b):
    """Distanza in km fra due locatori (None se uno manca)."""
    pa, pb = loc2ll(a), loc2ll(b)
    if not pa or not pb:
        return None
    la1, lo1, la2, lo2 = map(math.radians, (pa[0], pa[1], pb[0], pb[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, h)))


def _s(v):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def analogico(grp):
    """Si ascolta in FM? Vuoto, EchoLink, SVXLink e i sistemi «+A» (analogico e digitale) sì;
    DMR, C4FM, D-STAR, NXDN, 2D, 3D da soli no."""
    g = (grp or '').strip().upper()
    return g in ('', 'ALLMODE') or g.startswith('EL') or g.endswith('+A')


def leggi_ik2ane(path):
    import xlrd
    wb = xlrd.open_workbook(path)
    try:
        sh = wb.sheet_by_name('ponti')
    except xlrd.biffh.XLRDError:
        raise ValueError('nel file non c\'è il foglio «ponti»: è l\'elenco di IK2ANE?')
    head = [_s(sh.cell_value(0, c)).lower() for c in range(sh.ncols)]

    def col(*keys, default=None):
        for i, h in enumerate(head):
            if any(k in h for k in keys):
                return i
        return default

    cN, cF, cS, cT = col('(n)ome', default=1), col('(f)req', default=2), col('shift', default=3), col('tono', default=4)
    cR, cP, cL = col('regione', default=5), col('(p)rov', default=6), col('localit', default=7)
    cG, cI, cRete, cQ = col('(g)rp', default=8), col('id', default=9), col('(r)ete', default=11), col('qra', default=12)
    cGest = col('gestore', 'g(e)', default=17)
    out = []
    for r in range(1, sh.nrows):
        def v(c):
            return _s(sh.cell_value(r, c)) if c is not None and c < sh.ncols else ''
        try:
            khz = float(sh.cell_value(r, cF))
        except (TypeError, ValueError):
            continue
        if khz <= 0:
            continue
        hz = round(khz * 1000)
        reg = re.sub(r'^[\d/]+([a-z](?=\s))?\s*', '', v(cR).lower()).strip()      # «9t sicilia», «1x v.aosta», «7/8 basilicata»
        reg = {'v.aosta': "valle d'aosta", 'pugla': 'puglia', 'emilia r.': 'emilia romagna',
               'friuli v.g.': 'friuli venezia giulia', 'trentino a.a.': 'trentino alto adige'}.get(reg, reg)
        if reg and len(reg) <= 2:
            reg = ''
        out.append({'nome': v(cN), 'hz': hz, 'shift': v(cS), 'tono': v(cT), 'regione': reg.title().replace("D'Aosta", "d'Aosta") if reg else 'Altro',
                    'prov': v(cP).upper(), 'loc': re.sub(r'\s+', ' ', v(cL)).strip(), 'grp': v(cG), 'id': v(cI).upper(),
                    'rete': v(cRete), 'qra': v(cQ).upper(), 'gestore': v(cGest), 'banda': banda(hz),
                    'analog': analogico(v(cG))})
    return out


_CHIRP_MODI = {'FM': 'fm', 'NFM': 'fm', 'WFM': 'fm', 'AM': 'am', 'NAM': 'am', 'USB': 'ssb', 'LSB': 'ssb',
               'CW': 'rxcw', 'CWR': 'rxcw', 'RTTY': 'rtty', 'DIG': 'ft8'}


def leggi_chirp(path):
    with open(path, newline='', encoding='utf-8-sig', errors='replace') as f:
        rd = csv.DictReader(f)
        if not rd.fieldnames or 'Frequency' not in rd.fieldnames:
            raise ValueError('non è un file CSV di CHIRP (manca la colonna Frequency)')
        out = []
        for row in rd:
            try:
                hz = round(float(row.get('Frequency', '0')) * 1e6)
            except ValueError:
                continue
            if hz <= 0:
                continue
            mode = (row.get('Mode') or 'FM').strip().upper()
            dup, off = (row.get('Duplex') or '').strip(), (row.get('Offset') or '').strip()
            try:
                o = float(off or 0)
            except ValueError:
                o = 0.0
            o_txt = f'{o * 1000:g} kHz' if o < 1 else f'{o:g} MHz'
            shift = '0' if not dup or dup == 'off' else ('split' if dup == 'split' else f'{dup}{o_txt}')
            tone = (row.get('Tone') or '').strip()
            tono = row.get('rToneFreq', '') if tone in ('Tone', 'TSQL') else ''
            out.append({'nome': (row.get('Name') or '').strip(), 'hz': hz, 'shift': shift, 'tono': tono,
                        'regione': '', 'prov': '', 'loc': (row.get('Comment') or '').strip(), 'grp': mode,
                        'id': '', 'rete': '', 'qra': '', 'gestore': '', 'banda': banda(hz),
                        'analog': mode in _CHIRP_MODI, 'modo': _CHIRP_MODI.get(mode, 'fm')})
        return out


def leggi(path):
    """(fonte, righe) dal file scelto: .xls = IK2ANE, .csv = CHIRP."""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.xls':
        return FONTE_IK2ANE, leggi_ik2ane(path)
    if ext == '.csv':
        return FONTE_CHIRP, leggi_chirp(path)
    raise ValueError('formato non riconosciuto: serve pontixls.xls di IK2ANE oppure un CSV di CHIRP')


def memoria(r, fonte, gruppo_chirp='CHIRP'):
    """Una riga dell'elenco -> memoria del banco."""
    if fonte == FONTE_IK2ANE:
        parti = [r['nome'] if r['nome'].lower() not in ('', '-') else 'Ponte']
        if r['id']:
            parti.append(r['id'])
        loc = re.sub(r'\s*\([A-Z]{2,3}\)\s*$', '', r['loc'])
        if loc:
            parti.append(loc)
        nome = ' '.join(parti)
        if not r['analog']:
            nome += f' [{r["grp"]}]'
        gruppo = f'Ponti {r["prov"]}' if r['prov'] else 'Ponti'
        modo = 'fm'
    else:
        nome = r['nome'] or f'{r["hz"] / 1e6:.4f}'
        gruppo = gruppo_chirp
        modo = r.get('modo', 'fm')
    nota = ' · '.join(x for x in (
        f'shift {r["shift"]}' if r['shift'] and r['shift'] != '0' else 'simplex' if r['shift'] == '0' else '',
        f'tono {r["tono"]}' if r['tono'] else '', r['grp'] if r['grp'] and fonte == FONTE_IK2ANE else '',
        r['rete'], r['qra'], r['gestore'] and f'gestore {r["gestore"]}',
        'elenco IK2ANE' if fonte == FONTE_IK2ANE else '') if x)
    x = {'fm_bw': 10000 if r['hz'] % 25000 else 12000} if modo == 'fm' else {}
    return {'nome': nome[:48], 'hz': int(r['hz']), 'modo': modo, 'gruppo': gruppo[:30], 'x': x, 'nota': nota[:160],
            'fonte': fonte}
