"""QSO da WSJT-X → ADIF buono per LoTW via satellite (QO-100) → file .adi e, se configurato, Cloudlog.

WSJT-X manda su UDP (protocollo suo, porta 2237 di default) i messaggi di stato e, a ogni QSO registrato,
il record ADIF («Logged ADIF», tipo 12). Qui si ascolta quella porta, si completa il record con i campi che
LoTW richiede per contare un QSO via satellite (PROP_MODE SAT, SAT_NAME, banda di salita e di discesa) e
lo si scrive in un file ADIF; volendo lo si spedisce anche a Cloudlog con la sua API.
Solo libreria standard: niente dipendenze in più nell'eseguibile.
"""
import json
import os
import re
import socket
import struct
import threading
import time
import urllib.request
import urllib.error

MAGIC = 0xADBCCBDA
SCHEMA = 2

# transponder a banda stretta del QO-100: discesa 10489,5 MHz ↔ salita 2400,0 MHz
QO_DOWN = (10489.0, 10490.5)        # MHz: se la frequenza del QSO sta qui, è QO-100
QO_SHIFT = 8089.5                   # MHz: discesa − salita


def _utf8(b, i):
    """Stringa «utf8» del protocollo WSJT-X: lunghezza a 32 bit (0xffffffff = nulla) + byte."""
    n, = struct.unpack_from('>I', b, i)
    i += 4
    if n == 0xFFFFFFFF:
        return '', i
    return b[i:i + n].decode('utf-8', 'replace'), i + n


def _pack_utf8(s):
    d = s.encode('utf-8')
    return struct.pack('>I', len(d)) + d


def parse_adif(text):
    """Record ADIF → (lista di (campo, valore) in ordine). Ignora l'intestazione fino a <eoh>."""
    t = text
    m = re.search(r'<eoh>', t, re.I)
    if m:
        t = t[m.end():]
    out = []
    for m in re.finditer(r'<([A-Za-z0-9_]+):(\d+)(?::[A-Za-z])?>', t):
        name, n = m.group(1).upper(), int(m.group(2))
        val = t[m.end():m.end() + n]
        out.append((name, val))
    return out


def fmt_adif(fields):
    return ''.join(f'<{k}:{len(str(v))}>{v} ' for k, v in fields if str(v) != '') + '<eor>'


def fix_sat(fields, sat='QO-100', shift=QO_SHIFT):
    """Completa il record per LoTW se il QSO è sul QO-100 (si riconosce dalla frequenza di discesa).
    Torna (campi, True se era QO-100)."""
    d = dict(fields)
    try:
        f = float(d.get('FREQ', '') or 0)
    except ValueError:
        f = 0.0
    if not (QO_DOWN[0] <= f < QO_DOWN[1]):
        return fields, False
    up = f - shift
    keep = [(k, v) for k, v in fields if k not in ('FREQ', 'FREQ_RX', 'BAND', 'BAND_RX', 'PROP_MODE', 'SAT_NAME', 'SAT_MODE')]
    keep += [('FREQ', f'{up:.6f}'), ('FREQ_RX', f'{f:.6f}'), ('BAND', '13cm'), ('BAND_RX', '3cm'),
             ('PROP_MODE', 'SAT'), ('SAT_NAME', sat), ('SAT_MODE', 'S/X')]
    return keep, True


ADIF_BANDS = [(0.1357, 0.1378, '2190m'), (0.472, 0.479, '630m'), (1.8, 2.0, '160m'), (3.5, 4.0, '80m'), (5.06, 5.45, '60m'), (7.0, 7.3, '40m'),
              (10.1, 10.15, '30m'), (14.0, 14.35, '20m'), (18.068, 18.168, '17m'), (21.0, 21.45, '15m'), (24.89, 24.99, '12m'), (28.0, 29.7, '10m'),
              (50.0, 54.0, '6m'), (70.0, 71.0, '4m'), (144.0, 148.0, '2m'), (222.0, 225.0, '1.25m'), (420.0, 450.0, '70cm'), (902.0, 928.0, '33cm'),
              (1240.0, 1300.0, '23cm'), (2300.0, 2450.0, '13cm'), (3300.0, 3500.0, '9cm'), (5650.0, 5925.0, '6cm'), (10000.0, 10500.0, '3cm'), (24000.0, 24250.0, '1.25cm')]


def adif_band(mhz):
    for lo, hi, b in ADIF_BANDS:
        if lo - 0.05 <= mhz <= hi + 0.05:
            return b
    return ''


ADIF_HEADER = 'FZR SDR — QSO da WSJT-X, completati per LoTW (satellite)\n<adif_ver:5>3.1.4 <programid:7>FZR SDR <eoh>\n'


def append_adif(path, record):
    new = not os.path.isfile(path) or os.path.getsize(path) == 0
    with open(path, 'a', encoding='utf-8') as f:
        if new:
            f.write(ADIF_HEADER)
        f.write(record + '\n')


def _parse_reply(body):
    """Cloudlog risponde in JSON o in XML (<auth><status>Valid</status>…): tiro fuori i campi in un dizionario."""
    b = body.strip()
    try:
        r = json.loads(b)
        return r if isinstance(r, dict) else {'raw': b[:200]}
    except ValueError:
        pass
    if b.startswith('<'):
        d = {k.lower(): v.strip() for k, v in re.findall(r'<(status|rights|reason|message)>(.*?)</\1>', b, re.S | re.I)}
        if d:
            return d
    return {'raw': b[:200]}


class Cloudlog:
    """API di Cloudlog / Wavelog: POST /api/qso con un record ADIF; GET /api/auth/<chiave> per la prova."""

    def __init__(self, url, key, station_id):
        self.url = (url or '').strip().rstrip('/')
        self.key = (key or '').strip()
        self.sid = str(station_id or '').strip()

    @property
    def ok(self):
        return bool(self.url and self.key)

    def _call(self, path, data=None):
        last = None
        for base in (self.url + '/index.php', self.url):      # con o senza la riscrittura degli URL
            try:
                if data is None:
                    req = urllib.request.Request(base + path, headers={'Accept': 'application/json'})
                else:
                    req = urllib.request.Request(base + path, data=json.dumps(data).encode('utf-8'),
                                                 headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
                with urllib.request.urlopen(req, timeout=12) as r:
                    body = r.read().decode('utf-8', 'replace')
                return _parse_reply(body)
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (404, 405):
                    continue
                try:
                    body = e.read().decode('utf-8', 'replace')[:200]
                except Exception:
                    body = ''
                raise RuntimeError(f'HTTP {e.code} {body}'.strip())
            except Exception as e:
                last = e
                break
        raise RuntimeError(str(last) if last else 'indirizzo non raggiungibile')

    def test(self):
        r = self._call(f'/api/auth/{self.key}')
        st = str(r.get('status', r.get('raw', '?')))
        if st.lower() != 'valid':
            raise RuntimeError(f'chiave non valida ({st})')
        return r.get('rights', '')

    def qso(self, record):
        d = {'key': self.key, 'type': 'adif', 'string': record}
        if self.sid:
            d['station_profile_id'] = self.sid
        r = self._call('/api/qso', d)
        st = str(r.get('status', r.get('raw', '?')))
        if st.lower() not in ('created', 'ok', 'success'):
            raise RuntimeError(st + (': ' + str(r.get('reason')) if r.get('reason') else ''))
        return st


class WsjtxListener(threading.Thread):
    """Ascolta i messaggi UDP di WSJT-X; a ogni «Logged ADIF» chiama on_adif(testo, id_istanza)."""

    def __init__(self, port, on_adif, log=print, host='127.0.0.1'):
        super().__init__(daemon=True, name='wsjtx-udp')
        self.port, self.host, self.on_adif, self.log = int(port), host, on_adif, log
        self.alive = True
        self.sock = None
        self.seen = {}                  # ultimo heartbeat per istanza
        self.err = None

    def run(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.settimeout(1.0)
            self.sock = s
        except OSError as e:
            self.err = str(e)
            self.log(f'WSJT-X UDP: porta {self.port} non disponibile ({e})')
            return
        while self.alive:
            try:
                b, addr = s.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._msg(b, addr)
            except Exception as e:
                self.log(f'WSJT-X UDP: messaggio non capito ({e})')
        try:
            s.close()
        except OSError:
            pass

    def _msg(self, b, addr):
        if len(b) < 16:
            return
        magic, schema, typ = struct.unpack_from('>III', b, 0)
        if magic != MAGIC:
            return
        ident, i = _utf8(b, 12)
        if typ == 0:                                        # Heartbeat: rispondo, così WSJT-X mi vede
            self.seen[ident] = time.time()
            rep = struct.pack('>III', MAGIC, SCHEMA, 0) + _pack_utf8('FZR SDR') + struct.pack('>I', 3) + _pack_utf8('') + _pack_utf8('')
            try:
                self.sock.sendto(rep, addr)
            except OSError:
                pass
        elif typ == 12:                                     # Logged ADIF
            text, _ = _utf8(b, i)
            self.on_adif(text, ident)

    def close(self):
        self.alive = False


def ini_path(rig_name):
    """Il file delle impostazioni dell'istanza dedicata di WSJT-X (QSettings, formato ini) su Windows."""
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    d = os.path.join(base, f'WSJT-X - {rig_name}')
    return os.path.join(d, f'WSJT-X - {rig_name}.ini')


def ini_set_udp(path, port, host='127.0.0.1'):
    """Nell'ini di WSJT-X mette il server UDP su host:porta (sezione [Configuration]). Crea il file se manca."""
    want = {'UDPServer': host, 'UDPServerPort': str(int(port))}
    lines = []
    if os.path.isfile(path):
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    out, sec, done = [], None, set()
    for ln in lines:
        m = re.match(r'\s*\[(.+)\]\s*$', ln)
        if m:
            if sec == 'Configuration':
                for k, v in want.items():
                    if k not in done:
                        out.append(f'{k}={v}')
                        done.add(k)
            sec = m.group(1)
            out.append(ln)
            continue
        if sec == 'Configuration':
            k = ln.split('=', 1)[0].strip()
            if k in want:
                if k in done:
                    continue
                out.append(f'{k}={want[k]}')
                done.add(k)
                continue
        out.append(ln)
    if sec == 'Configuration':
        for k, v in want.items():
            if k not in done:
                out.append(f'{k}={v}')
                done.add(k)
    if 'Configuration' not in [re.match(r'\s*\[(.+)\]', l).group(1) for l in out if re.match(r'\s*\[(.+)\]', l)]:
        out += ['[Configuration]'] + [f'{k}={v}' for k, v in want.items()]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out) + '\n')
