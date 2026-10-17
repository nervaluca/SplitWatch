"""Reverse Beacon Network (solo CW) per FZR Skimmer.

Si collega a telnet.reversebeacon.net:7000 con il nominativo dell'operatore, legge gli spot
e li tiene in memoria (ultimi 15 minuti). Servono a:
  - mostrare sul waterfall chi è stato spottato e dove;
  - confermare i nominativi decodificati (stesso call entro poche centinaia di Hz).
"""
import re
import socket
import threading
import time

HOST, PORT = 'telnet.reversebeacon.net', 7000
KEEP_S = 15 * 60
SPOT_RE = re.compile(r'^DX de\s+([A-Z0-9/#-]+):\s+(\d+(?:\.\d+)?)\s+([A-Z0-9/]+)\s+CW\s+(-?\d+)\s*dB\s+(\d+)\s*WPM\s+(\w+)?',
                     re.I)


class RBN:
    def __init__(self, call, on_status=None):
        self.call = (call or '').strip().upper()
        self.on_status = on_status or (lambda s: None)
        self.spots = {}                 # call -> {'call','f','t','snr','wpm','n','tipo'}
        self.lock = threading.Lock()
        self.state = 'spento'
        self._stop = threading.Event()
        self.sock = None
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()
        s = self.sock
        if s:
            try:
                s.close()
            except Exception:
                pass
        self.state = 'spento'

    def _run(self):
        wait = 5
        while not self._stop.is_set():
            try:
                self.state = 'connessione…'
                s = socket.create_connection((HOST, PORT), timeout=20)
                self.sock = s
                s.settimeout(90)
                buf = b''
                logged = False
                t_login = time.time()
                while not self._stop.is_set():
                    try:
                        d = s.recv(4096)
                    except socket.timeout:
                        s.sendall(b'\r\n')                    # tiene viva la connessione
                        continue
                    if not d:
                        raise ConnectionError('chiusa dal server')
                    buf += d
                    if not logged and (b'call' in buf.lower() or time.time() - t_login > 5):
                        s.sendall(self.call.encode() + b'\r\n')
                        logged = True
                        self.state = 'collegato'
                        self.on_status(f'RBN collegato come {self.call}')
                        wait = 5
                    while b'\n' in buf:
                        line, buf = buf.split(b'\n', 1)
                        self._line(line.decode('ascii', 'replace').strip())
            except Exception as e:
                if self._stop.is_set():
                    break
                self.state = f'non raggiungibile, riprovo tra {wait} s'
                if wait == 5:
                    self.on_status(f'RBN: {e}')
            finally:
                try:
                    if self.sock:
                        self.sock.close()
                except Exception:
                    pass
                self.sock = None
            self._stop.wait(wait)
            wait = min(120, wait * 2)

    def _line(self, line):
        m = SPOT_RE.match(line)
        if not m:
            return
        spotter, fk, call, snr, wpm, tipo = m.groups()
        call = call.upper()
        f = float(fk) * 1000.0
        now = time.time()
        with self.lock:
            sp = self.spots.get(call)
            if sp and abs(sp['f'] - f) < 500 and now - sp['t'] < 600:
                sp['n'] += 1
                sp['t'] = now
                sp['snr'] = max(sp['snr'], int(snr))
                sp['f'] = (sp['f'] * 3 + f) / 4
            else:
                self.spots[call] = {'call': call, 'f': f, 't': now, 'snr': int(snr), 'wpm': int(wpm), 'n': 1,
                                    'tipo': (tipo or '').upper()}
            if len(self.spots) > 3000:
                self._prune(now)

    def _prune(self, now):
        for c in [c for c, s in self.spots.items() if now - s['t'] > KEEP_S]:
            del self.spots[c]

    def in_band(self, lo, hi):
        now = time.time()
        with self.lock:
            self._prune(now)
            return [dict(s, age=round(now - s['t'])) for s in self.spots.values() if lo <= s['f'] <= hi]

    def match(self, word, fa, band):
        """Conferma un nominativo decodificato: (call, 'v') se spottato lì vicino,
        (call, 's') se differisce di una lettera da uno spottato entro 300 Hz, altrimenti None."""
        if len(word) < 3:
            return None
        near = [s for s in band if abs(s['f'] - fa) < 600]
        for s in near:
            if s['call'] == word:
                return word, 'v'
        if len(word) >= 4:
            hit = [s['call'] for s in near if abs(s['f'] - fa) < 300 and len(s['call']) == len(word)
                   and sum(a != b for a, b in zip(s['call'], word)) == 1]
            if len(hit) == 1:
                return hit[0], 's'
        return None
