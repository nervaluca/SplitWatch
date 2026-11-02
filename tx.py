"""Trasmissione con l'ADALM-Pluto verso QO-100 (salita 2400,0–2400,5 MHz).

Il CW si fa con il generatore di toni (DDS) del Pluto: un tono fisso a +500 kHz dall'oscillatore TX, così la
portante residua dell'oscillatore e l'immagine cadono fuori dal transponder (sotto 2400,0 MHz). La
manipolazione si fa con l'attenuatore TX del chip (−89,75 dB = spento), con una rampa di pochi passi per
non fare «clic». Niente flusso di campioni verso il Pluto: la USB resta tutta per la ricezione.

Si parla con iiod (porta 30431) su una connessione tutta sua, separata da quella della ricezione.
"""
import re
import socket
import sys
import threading
import time
import queue

QO_SHIFT = 8089.5e6                       # discesa − salita del transponder a banda stretta
QO_UP = (2400.0e6, 2400.5e6)
QO_BEACONS_UP = (2400.000e6, 2400.250e6, 2400.500e6)
BEACON_GUARD = 4000.0                     # niente TX a meno di 4 kHz da un beacon
DDS_F = 500000                            # tono del DDS sopra l'oscillatore TX
OFF_DB = -89.75                           # attenuazione massima = chiave alzata
RAMP = (-36.0, -20.0, -10.0, -4.0)        # passi della rampa (dB sotto il livello pieno)
MAX_KEY_S = 20.0                          # chiave giù di fila al massimo (sicurezza)

MORSE = {
    'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.', 'F': '..-.', 'G': '--.', 'H': '....', 'I': '..',
    'J': '.---', 'K': '-.-', 'L': '.-..', 'M': '--', 'N': '-.', 'O': '---', 'P': '.--.', 'Q': '--.-', 'R': '.-.',
    'S': '...', 'T': '-', 'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-', 'Y': '-.--', 'Z': '--..',
    '0': '-----', '1': '.----', '2': '..---', '3': '...--', '4': '....-', '5': '.....', '6': '-....',
    '7': '--...', '8': '---..', '9': '----.', '/': '-..-.', '?': '..--..', '.': '.-.-.-', ',': '--..--',
    '=': '-...-', '+': '.-.-.', '-': '-....-', '!': '-.-.--', '@': '.--.-.',
    '<AR>': '.-.-.', '<SK>': '...-.-', '<BK>': '-...-.-', '<KN>': '-.--.', '<BT>': '-...-',
}


def up_ok(f):
    """Salita permessa: dentro il transponder a banda stretta e lontano dai beacon."""
    if not (QO_UP[0] + BEACON_GUARD <= f <= QO_UP[1] - BEACON_GUARD):
        return False
    return all(abs(f - b) >= BEACON_GUARD for b in QO_BEACONS_UP)


def tokens(text):
    """Testo → simboli Morse: lettere, numeri, segni e prosegni scritti come <SK>."""
    out = []
    for m in re.finditer(r'<[A-Z]{2}>|\S|\s+', text.upper()):
        t = m.group(0)
        if t.isspace():
            out.append(' ')
        elif t in MORSE:
            out.append(t)
    return out


def _hires_timer():
    """Su Windows il timer di sistema va a 15,6 ms: lo porto a 1 ms mentre si trasmette."""
    if sys.platform.startswith('win'):
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass


def _wait_until(t):
    """Attende fino all'istante t (perf_counter): dorme, poi gli ultimi 2 ms in attesa attiva."""
    while True:
        d = t - time.perf_counter()
        if d <= 0:
            return
        if d > 0.003:
            time.sleep(d - 0.002)


class PlutoTx:
    PORT = 30431

    def __init__(self, host, on_status):
        self.host = (host or '192.168.2.1').strip()
        self.on_status = on_status
        self.sock = self.f = None
        self.lock = threading.Lock()
        self.phy = self.dds = None
        self.dch = {}                 # nome del tono del DDS (TX1_I_F1…) → id del canale (altvoltage0…)
        self.fq = float(DDS_F)        # frequenza vera del DDS (risoluzione ~35 Hz): letta dal Pluto
        self.lo = None
        self.level = -20.0            # dB: 0 = massimo del Pluto (~+5 dBm)
        self.inv = False              # I/Q invertiti: il segnale uscirebbe sotto l'oscillatore
        self.down = False
        self.enabled = False

    # ---- protocollo iiod (come la ricezione)
    def _int(self):
        while True:
            b = self.f.readline()
            if not b:
                raise OSError('il Pluto ha chiuso la connessione TX')
            t = b.decode('ascii', 'replace').strip()
            if t:
                return int(t)

    def _write(self, dev, ch, attr, value):
        v = str(value).encode('ascii')
        with self.lock:
            if self.sock is None:
                return -1
            self.sock.sendall(f'WRITE {dev} OUTPUT {ch} {attr} {len(v)}\r\n'.encode('ascii') + v)
            return self._int()

    def _read(self, dev, ch, attr):
        with self.lock:
            self.sock.sendall(f'READ {dev} OUTPUT {ch} {attr}\r\n'.encode('ascii'))
            n = self._int()
            if n < 0:
                return None
            return self.f.read(n + 1)[:n].decode('ascii', 'replace').strip('\x00\n ')

    def connect(self):
        import xml.etree.ElementTree as ET
        try:
            self.sock = socket.create_connection((self.host, self.PORT), timeout=3)
        except OSError as e:
            raise RuntimeError(f'TX: il Pluto non risponde su {self.host} ({e})')
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(5)
        self.f = self.sock.makefile('rb', buffering=1 << 16)
        with self.lock:
            self.sock.sendall(b'PRINT\r\n')
            n = self._int()
            xml = self.f.read(n + 1)[:n]
        root = ET.fromstring(xml.split(b'?>', 1)[-1] if xml.startswith(b'<?xml') else xml)
        for d in root.iter('device'):
            nm, did = d.get('name'), d.get('id')
            if nm == 'ad9361-phy':
                self.phy = did
            elif nm == 'cf-ad9361-dds-core-lpc':
                self.dds = did
                for c in d.iter('channel'):
                    if c.get('type') == 'output' and c.get('name'):
                        self.dch[c.get('name')] = c.get('id')
        if not self.phy or not self.dds or 'TX1_I_F1' not in self.dch:
            raise RuntimeError('TX: il Pluto non ha il generatore di toni (DDS) dove me lo aspetto')
        p, dch = self.phy, self.dch
        self._write(p, 'voltage0', 'hardwaregain', OFF_DB)
        self._write(p, 'voltage0', 'rf_bandwidth', 1500000)          # il tono a +500 kHz deve passare
        self._write(p, 'voltage0', 'rf_port_select', 'A')
        for name, ch in dch.items():                                 # tutti i toni spenti…
            if name.startswith('TX'):
                self._write(self.dds, ch, 'scale', 0)
        self._dds()                                                  # …tranne quello che serve
        self._write(self.dds, dch['TX1_I_F1'], 'raw', 1)
        self.down = False
        self.enabled = False
        self.lo_on(False)

    def _dds(self):
        """Tono complesso a +DDS_F: I con fase 90°, Q con fase 0 (−DDS_F se invertito)."""
        d, c = self.dds, self.dch
        for nm in ('TX1_I_F1', 'TX1_Q_F1'):
            self._write(d, c[nm], 'frequency', DDS_F)
            self._write(d, c[nm], 'scale', 0.7)
        self._write(d, c['TX1_I_F1'], 'phase', 0 if self.inv else 90000)
        self._write(d, c['TX1_Q_F1'], 'phase', 90000 if self.inv else 0)
        try:
            self.fq = float(self._read(d, c['TX1_I_F1'], 'frequency') or DDS_F)
        except (OSError, ValueError):
            self.fq = float(DDS_F)

    def set_inv(self, inv):
        self.inv = bool(inv)
        if self.sock is not None:
            self._dds()
            self.lo = None

    def power(self, on):
        """TX abilitato o no (l'oscillatore TX si accende solo mentre si trasmette)."""
        self.enabled = bool(on)
        if self.sock is None:
            return
        if not on:
            self.key(False)
            self.lo_on(False)

    def lo_on(self, on):
        """Oscillatore TX acceso solo durante la trasmissione: in ricezione niente perdite sulla salita."""
        if self.sock is None:
            return
        self._write(self.phy, 'altvoltage1', 'powerdown', 0 if on else 1)
        if on:
            time.sleep(0.005)                 # aggancio del PLL

    def set_freq(self, f_up):
        """Frequenza di salita (Hz). L'oscillatore va DDS_F sotto (o sopra, se invertito)."""
        lo = int(round(f_up - (-self.fq if self.inv else self.fq)))
        if lo != self.lo:
            r = self._write(self.phy, 'altvoltage1', 'frequency', lo)
            if r is not None and r < 0:
                raise RuntimeError(f'TX: il Pluto non accetta {lo/1e6:.6f} MHz')
            self.lo = lo

    def key(self, down):
        """Chiave giù / su, con la rampa (ogni passo è una scrittura: ~1 ms)."""
        if down == self.down or self.sock is None:
            return
        steps = list(RAMP) + [0.0] if down else [0.0] + list(reversed(RAMP))
        for s in (steps[1:] if not down else steps):
            self._write(self.phy, 'voltage0', 'hardwaregain', round(max(OFF_DB, self.level + s) * 4) / 4)
        if not down:
            self._write(self.phy, 'voltage0', 'hardwaregain', OFF_DB)
        self.down = down

    def close(self):
        try:
            if self.sock is not None:
                self.key(False)
                self._write(self.phy, 'altvoltage1', 'powerdown', 1)
        except OSError:
            pass
        try:
            if self.sock is not None:
                self.sock.close()
        except OSError:
            pass
        self.sock = None


class CwKeyer:
    """Manda testo in CW con il PlutoTx: coda di messaggi, velocità in WPM, STOP immediato.

    on_ptt(True/False): inizio e fine della trasmissione (per il semiduplex: audio muto);
    on_progress(testo_mandato, testo_che_resta) a ogni carattere."""

    def __init__(self, tx, on_ptt, on_progress, on_status):
        self.tx = tx
        self.on_ptt, self.on_progress, self.on_status = on_ptt, on_progress, on_status
        self.wpm = 24
        self.q = queue.Queue()
        self.abort = threading.Event()
        self.busy = False
        self.freq_fn = None           # → frequenza di salita al momento di partire (None = non permessa)
        self.r_off = 0.004            # durata della rampa di discesa (misurata)
        threading.Thread(target=self._run, daemon=True).start()

    def send(self, text):
        t = ' '.join(str(text).split())
        if t:
            self.abort.clear()
            self.q.put(('cw', t))

    def tune(self, secs):
        self.abort.clear()
        self.q.put(('tune', float(secs)))

    def stop(self):
        self.abort.set()
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass
        try:
            self.tx.key(False)
        except OSError:
            pass

    def _run(self):
        _hires_timer()
        while True:
            kind, arg = self.q.get()
            if self.abort.is_set():
                continue
            try:
                f = self.freq_fn() if self.freq_fn else None
                if f is None:
                    self.on_status('TX: frequenza di salita fuori dal transponder o vicino a un beacon: non trasmetto')
                    continue
                if not self.tx.enabled:
                    self.on_status('TX spento: accendilo prima')
                    continue
                self.tx.set_freq(f)
                if not self.busy:
                    self.tx.lo_on(True)
                self.busy = True
                self.on_ptt(True)
                if kind == 'tune':
                    self._tune(arg)
                else:
                    self._cw(arg)
            except Exception as e:
                self.on_status(f'TX: {e}')
            finally:
                try:
                    self.tx.key(False)
                except Exception:
                    pass
                if self.q.empty() or self.abort.is_set():
                    try:
                        self.tx.lo_on(False)
                    except Exception:
                        pass
                    self.busy = False
                    self.on_ptt(False)
                    self.on_progress('', '')

    def _tune(self, secs):
        secs = min(secs, MAX_KEY_S)
        self.on_progress('', '▬ portante')
        self.tx.key(True)
        end = time.perf_counter() + secs
        while time.perf_counter() < end and not self.abort.is_set():
            time.sleep(0.02)

    def _cw(self, text):
        u = 1.2 / max(5, min(60, self.wpm))
        toks = tokens(text)
        t = time.perf_counter() + 0.02
        sent = ''
        for i, tk in enumerate(toks):
            if self.abort.is_set():
                return
            rest = ''.join(toks[i:])
            self.on_progress(sent, rest)
            if tk == ' ':
                t += 4 * u                  # 7 unità fra le parole (3 già messe dopo il carattere)
                sent += ' '
                continue
            for j, el in enumerate(MORSE[tk]):
                _wait_until(t)
                if self.abort.is_set():
                    return
                self.tx.key(True)
                t += u if el == '.' else 3 * u
                _wait_until(t - self.r_off)            # la rampa di discesa finisce sul tempo giusto
                t1 = time.perf_counter()
                self.tx.key(False)
                self.r_off = 0.8 * self.r_off + 0.2 * min(0.02, time.perf_counter() - t1)
                t += u
            t += 2 * u                      # 3 unità fra i caratteri
            sent += tk
        _wait_until(t)
        self.on_progress(sent, '')
