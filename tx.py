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
from collections import deque
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
        self.key_log = []             # (ora, giù/su) di ogni manipolazione: serve a riconoscere la mia eco

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
                idx = {}
                for c in d.iter('channel'):
                    if c.get('type') == 'output' and c.get('name'):
                        self.dch[c.get('name')] = c.get('id')
                    se = c.find('scan-element')
                    if c.get('type') == 'output' and se is not None:
                        idx[c.get('id')] = int(se.get('index'))
                self.tx_nch = max(idx.values()) + 1 if idx else 2
                self.tx_mask = (1 << idx.get('voltage0', 0)) | (1 << idx.get('voltage1', 1))
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
            self._write(d, c[nm], 'scale', 0.95)
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

    def set_freq_off(self, f_up, off):
        """Per il flusso di campioni (SSB): il segnale sta a +off dall'oscillatore, esatto."""
        lo = int(round(f_up - (-off if self.inv else off)))
        if lo != self.lo:
            r = self._write(self.phy, 'altvoltage1', 'frequency', lo)
            if r is not None and r < 0:
                raise RuntimeError(f'TX: il Pluto non accetta {lo/1e6:.6f} MHz')
            self.lo = lo

    def key(self, down):
        """Chiave giù / su, con la rampa (ogni passo è una scrittura: ~1 ms)."""
        if down == self.down or self.sock is None:
            return
        self.key_log.append((time.perf_counter(), bool(down)))   # per riconoscere la mia eco
        if len(self.key_log) > 4000:
            del self.key_log[:2000]
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


class TxProbe:
    """Trova la propria portante sulla discesa per differenza: spettro a chiave alzata e a chiave giù.

    Cerca entro ±span Hz da dove deve arrivare (l'LNB può sbagliare di decine di kHz): vince la riga che sale
    di più quando si trasmette, quindi i beacon e le altre stazioni non ingannano. Poi la frequenza fine."""

    def __init__(self, fs, expect_off, span=40000.0):
        self.fs, self.e, self.span = float(fs), float(expect_off), float(span)
        self.phase = None             # 'off' | 'on' | None
        self.buf = {'off': [], 'on': []}
        self.n = {'off': 0, 'on': 0}

    def feed(self, iq):
        if self.phase in self.buf:
            self.buf[self.phase].append(iq.copy())
            self.n[self.phase] += len(iq)

    @property
    def done(self):                   # compatibile con il giro della calibrazione (non usato)
        return False

    def _base(self, x):
        import numpy as np
        t = np.arange(len(x)) / self.fs
        y = x * np.exp(-2j * np.pi * self.e * t).astype(np.complex64)
        d = max(1, int(self.fs // (2.4 * self.span)))
        m = len(y) // d
        return y[:m * d].reshape(m, d).mean(1), self.fs / d

    def _psd(self, y, nfft):
        import numpy as np
        w = np.hanning(nfft).astype(np.float32)
        segs = [y[i:i + nfft] for i in range(0, len(y) - nfft + 1, nfft // 2)]
        return np.mean([np.abs(np.fft.fft(s * w)) ** 2 for s in segs], axis=0) + 1e-20

    def result(self):
        import numpy as np
        if self.n['off'] < self.fs * 0.5 or self.n['on'] < self.fs * 0.8:
            return {'ok': False, 'why': 'dati insufficienti dal ricevitore'}
        yo, fs2 = self._base(np.concatenate(self.buf['off']))
        yi, _ = self._base(np.concatenate(self.buf['on']))
        nfft = 1 << int(np.log2(fs2 / 12.0))          # ~12 Hz per riga
        po, pi = self._psd(yo, nfft), self._psd(yi, nfft)
        f = np.fft.fftfreq(nfft, 1 / fs2)
        sel = (np.abs(f) <= self.span) & (np.abs(f + self.e) > 1500)   # niente picco DC del ricevitore
        r = np.where(sel, 10 * np.log10(pi / po), -99)
        k = int(np.argmax(r))
        rise = float(r[k])
        snr = float(10 * np.log10(pi[k] / np.median(pi[sel])))
        if rise < 10 or snr < 12:
            return {'ok': False, 'why': f'non vedo la mia portante sulla discesa (sale di {rise:.0f} dB): '
                                        f'alza PWR o controlla l\'amplificatore', 'snr': round(snr, 1)}
        # frequenza fine: porto la riga a 0 Hz, stretto, FFT lunga con interpolazione
        f0 = float(f[k])
        t = np.arange(len(yi)) / fs2
        z = yi * np.exp(-2j * np.pi * f0 * t)
        d = max(1, int(fs2 // 400))
        m = len(z) // d
        z = z[:m * d].reshape(m, d).mean(1)
        fs3 = fs2 / d
        N = 1 << int(np.ceil(np.log2(len(z) * 8)))
        sp = np.abs(np.fft.fft(z * np.hanning(len(z)), N)) ** 2
        ff = np.fft.fftfreq(N, 1 / fs3)
        win = np.abs(ff) < 30
        j = np.nonzero(win)[0][np.argmax(sp[win])]
        a, b, c = np.log(sp[j - 1] + 1e-30), np.log(sp[j] + 1e-30), np.log(sp[(j + 1) % N] + 1e-30)
        den = a - 2 * b + c
        dd = 0.5 * (a - c) / den if den != 0 else 0.0
        err = f0 + ff[j] + dd * fs3 / N
        return {'ok': True, 'err_hz': round(float(err), 1), 'snr': round(snr, 1), 'rise': round(rise, 1)}


def stream_test(tx, f_up, secs=10.0, on_status=None):
    """Prova di banda per la SSB: manda al Pluto un flusso continuo di campioni (due toni a +750 e +1875 Hz
    sulla salita) per secs secondi, su una connessione a parte, mentre la ricezione continua.
    Restituisce la velocità ottenuta: se sta sotto PLUTO_RATE la USB (o il PC) non ce la fa."""
    import numpy as np
    from sources import PLUTO_RATE
    fs = float(PLUTO_RATE)
    N = 36864                                   # 16 ms: fs/N = 62,5 Hz, i toni chiudono il giro esatti
    n = np.arange(N)
    off = (-tx.fq if tx.inv else tx.fq)
    z = 0.35 * (np.exp(2j * np.pi * (off + 750.0) * n / fs) + np.exp(2j * np.pi * (off + 1875.0) * n / fs))
    a = np.empty(2 * N, '<i2')
    a[0::2] = np.round(z.real * 16384)
    a[1::2] = np.round(z.imag * 16384)
    data = a.tobytes()
    words = (getattr(tx, 'tx_nch', 2) + 31) // 32
    mask = ''.join(f'{(getattr(tx, "tx_mask", 3) >> (32 * w)) & 0xFFFFFFFF:08x}' for w in reversed(range(words)))
    s = socket.create_connection((tx.host, tx.PORT), timeout=5)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
    except OSError:
        pass
    f = s.makefile('rb')

    def rint():
        while True:
            b = f.readline()
            if not b:
                raise OSError('il Pluto ha chiuso la connessione del flusso TX')
            t = b.decode('ascii', 'replace').strip()
            if t:
                return int(t)
    res = {'ok': False}
    try:
        tx.set_freq(f_up)
        tx.lo_on(True)
        s.sendall(f'SET {tx.dds} BUFFERS_COUNT 8\r\n'.encode('ascii'))
        rint()
        s.sendall(f'OPEN {tx.dds} {N} {mask}\r\n'.encode('ascii'))
        r = rint()
        if r < 0:
            raise RuntimeError(f'il Pluto non apre il flusso TX (errore {r})')
        tx.key(True)
        t0 = time.perf_counter()
        sent, wmax, nw = 0, 0.0, 0
        while time.perf_counter() - t0 < secs:
            w0 = time.perf_counter()
            s.sendall(f'WRITEBUF {tx.dds} {len(data)}\r\n'.encode('ascii') + data)
            r = rint()
            if r < 0:
                raise RuntimeError(f'errore {r} scrivendo i campioni')
            wmax = max(wmax, time.perf_counter() - w0)
            sent += N
            nw += 1
        el = time.perf_counter() - t0
        rate = sent / el
        res = {'ok': rate >= 0.98 * fs, 'rate': round(rate / 1e6, 3), 'need': round(fs / 1e6, 3),
               'secs': round(el, 1), 'wmax_ms': round(wmax * 1000, 1), 'writes': nw}
    except Exception as e:
        res = {'ok': False, 'why': str(e)}
    finally:
        try:
            tx.key(False)
        except Exception:
            pass
        try:
            s.sendall(f'CLOSE {tx.dds}\r\n'.encode('ascii'))
            rint()
        except Exception:
            pass
        try:
            s.close()
        except OSError:
            pass
        try:
            tx.lo_on(False)
            tx._dds()                                   # dopo il flusso il DAC torna al generatore di toni (CW)
            tx._write(tx.dds, tx.dch['TX1_I_F1'], 'raw', 1)
        except Exception:
            pass
    return res


# ---------------------------------------------------------------------------------------------- SSB
SSB_PEAK = 29000          # cresta dei campioni verso il DAC (fondo scala 32767): la portante del DDS sta a 0,95
TX_RATE = 2304000         # come la ricezione del Pluto
SSB_OFF = 500000          # come il CW: oscillatore 500 kHz sotto, portante residua e immagine fuori dal transponder
SSB_N = 36864             # campioni per buffer (16 ms); 128 campioni audio a 8 kHz
SSB_MAX_S = 300.0         # PTT acceso al massimo 5 minuti (sicurezza)


def _kaiser_lp(ntaps, fc, fs, beta=5.65):
    import numpy as np
    n = np.arange(ntaps) - (ntaps - 1) / 2
    h = np.sinc(2 * fc / fs * n) * np.kaiser(ntaps, beta)
    return h / h.sum()


class _Interp:
    """Interpolatore polifase complesso ×L con stato (blocchi consecutivi senza giunture).
    Una sola moltiplicazione di matrici per blocco: finestre dell'ingresso × fasi del filtro."""

    def __init__(self, L, h):
        import numpy as np
        self.L = int(L)
        n = (len(h) + L - 1) // L * L
        hh = np.zeros(n, np.complex128)
        hh[:len(h)] = np.asarray(h) * L
        self.k = n // L
        # colonna p = fase p del filtro, rovesciata (convoluzione → prodotto scalare sulle finestre)
        self.H = np.stack([hh[p::L][::-1] for p in range(L)], axis=1).astype(np.complex64)
        self.tail = np.zeros(self.k - 1, np.complex64)

    def __call__(self, x):
        import numpy as np
        z = np.concatenate([self.tail, x.astype(np.complex64)])
        self.tail = z[len(z) - (self.k - 1):].copy() if self.k > 1 else np.zeros(0, np.complex64)
        W = np.lib.stride_tricks.sliding_window_view(z, self.k)      # len(x) finestre
        return (W @ self.H).reshape(-1)


EQ_F = (200.0, 400.0, 800.0, 1600.0, 2600.0)     # bande dell'equalizzatore TX (Hz)
EQ_TAPS = 129                                    # 16 ms a 8 kHz: risoluzione ~60 Hz, ritardo fisso


def eq_fir(gains, fs=8000.0):
    """Equalizzatore a 5 bande → filtro FIR a fase lineare (piatto = impulso: stesso ritardo sempre)."""
    import numpy as np
    g = [max(-12.0, min(12.0, float(x))) for x in (list(gains or []) + [0.0] * 5)[:5]]
    h = np.zeros(EQ_TAPS, np.float32)
    if not any(g):
        h[EQ_TAPS // 2] = 1.0
        return h
    nf = 2048
    f = np.linspace(0, fs / 2, nf // 2 + 1)
    db = np.interp(np.log(np.maximum(f, 60.0)), np.log(EQ_F), g)   # in mezzo interpolato, fuori piatto
    hz = np.fft.irfft(10 ** (db / 20), nf)
    h = np.roll(hz, EQ_TAPS // 2)[:EQ_TAPS] * np.kaiser(EQ_TAPS, 6.0)
    return h.astype(np.float32)


def _ssb_bp():
    """Passa-banda complesso 300–2700 Hz a 8 kHz: fa da solo la banda laterale superiore (guadagno ½)."""
    import numpy as np
    n = np.arange(201) - 100
    return (_kaiser_lp(201, 1200.0, 8000.0) * np.exp(2j * np.pi * 1500.0 * n / 8000.0)).astype(np.complex64)


class EnvClip:
    """Tosatore sull'inviluppo del segnale SSB (8 kHz complesso), poi di nuovo il filtro 300–2700 Hz.

    Come il processore a RF delle radio: taglia le creste dell'ampiezza senza toccare la fase, quindi
    distorce poco e abbassa davvero il rapporto cresta/media (più potenza media a parità di cresta).
    Riceve metà dei dB del processore. Il filtro c'è sempre (ritardo fisso, niente salti)."""

    def __init__(self):
        import numpy as np
        self.hc = _ssb_bp()
        self.tails = [np.zeros(200, np.complex64), np.zeros(200, np.complex64)]

    def __call__(self, y, proc_db):
        import numpy as np
        if proc_db > 0:
            y = y * (10 ** (proc_db / 40))
        for i in range(2):                                     # due passaggi: il filtro rifà meno creste
            if proc_db > 0:
                m = np.abs(y)
                y = np.where(m > 0.45, y * (0.45 / np.maximum(m, 1e-9)), y)
            z = np.concatenate([self.tails[i], y.astype(np.complex64)])
            self.tails[i] = z[-200:]
            y = np.convolve(z, self.hc, mode='valid')          # su un segnale complesso il filtro rende 1
        return y


class VoiceProc:
    """La voce a 8 kHz prima del modulatore: guadagno del microfono, equalizzatore, processore.

    Processore (0–20 dB): livellatore lento che alza le parti deboli fino a quei dB; la tosatura vera la fa
    EnvClip sul segnale SSB già formato. 0 = spento: solo le creste oltre 0,7 vengono arrotondate.
    I valori si cambiano anche mentre si trasmette."""

    def __init__(self, gain_db=0.0, proc_db=10.0, eq=None):
        import numpy as np
        self.te = np.zeros(EQ_TAPS - 1, np.float32)
        self.g = 1.0
        self.level = -90.0
        self.comp_db = 0.0
        self.set(gain_db, proc_db, eq if eq is not None else [0] * 5)

    def set(self, gain_db=None, proc_db=None, eq=None):
        if gain_db is not None:
            self.gain = 10 ** (float(gain_db) / 20)
        if proc_db is not None:
            self.proc = max(0.0, min(20.0, float(proc_db)))
        if eq is not None:
            self.h = eq_fir(eq)

    def __call__(self, a):
        import numpy as np
        a = np.asarray(a, np.float32) * self.gain
        h = self.h
        z = np.concatenate([self.te, a])
        a = np.convolve(z, h, mode='valid').astype(np.float32)
        self.te = z[len(z) - (EQ_TAPS - 1):]
        p = self.proc
        if p > 0:                                              # livellatore: livello medio verso −12 dB
            rms = float(np.sqrt(np.mean(a * a)) + 1e-6)
            want = min(10 ** (p / 20), 0.25 / rms)
            k = 0.5 if want < self.g else 0.03                 # giù subito, su piano
            g1 = self.g + (want - self.g) * k
            a = a * np.linspace(self.g, g1, len(a), dtype=np.float32)
            self.g = g1
        else:
            self.g = 1.0
        self.comp_db = 20 * np.log10(max(1e-3, self.g))
        pk = float(np.max(np.abs(a))) if len(a) else 0.0
        self.level = 20 * np.log10(pk + 1e-6)
        return np.where(np.abs(a) > 0.7, np.sign(a) * (0.7 + 0.3 * np.tanh((np.abs(a) - 0.7) / 0.3)), a).astype(np.float32)


class SsbMod:
    """Audio del microfono (8 kHz) → USB a +SSB_OFF, campioni a 2,304 MS/s pronti per il Pluto.

    VoiceProc (guadagno, equalizzatore, processore), poi il filtro 300–2700 Hz complesso (fa da solo la banda
    laterale superiore e toglie le armoniche della tosatura), poi due interpolazioni (×12 e ×24) con reiezione
    delle immagini > 60 dB."""

    def __init__(self, gain_db=0.0, proc_db=10.0, eq=None, inv=False):
        import numpy as np
        self.inv = bool(inv)
        self.vp = VoiceProc(gain_db, proc_db, eq)
        self.ec = EnvClip()
        self.hc = _ssb_bp()
        self.tc = np.zeros(200, np.complex64)
        self.i1 = _Interp(12, _kaiser_lp(181, 3600.0, 96000.0))
        # spostamento a +500 kHz senza moltiplicare a 2,3 MHz: a 96 kHz si sposta di 20 kHz (500000 mod 96000),
        # poi l'ultimo filtro è un passa-banda su 501,5 kHz che prende la 5ª immagine (20 kHz + 5 × 96 kHz)
        k = np.arange(24)
        self.rot = np.exp(2j * np.pi * (SSB_OFF % 96000) * k / 96000.0).astype(np.complex64)
        self.ri = 0
        h2 = _kaiser_lp(192, 4000.0, float(TX_RATE), beta=8.0)
        n2 = np.arange(len(h2)) - (len(h2) - 1) / 2
        self.i2 = _Interp(24, h2 * np.exp(2j * np.pi * (SSB_OFF + 1500.0) * n2 / TX_RATE))

    def __call__(self, a):
        import numpy as np
        a = self.vp(a)
        z = np.concatenate([self.tc, a.astype(np.complex64)])
        y = np.convolve(z, self.hc, mode='valid')
        self.tc = z[-200:]
        y = self.ec(y, self.vp.proc)
        return self.up(y)

    def up(self, y):
        """Banda base complessa a 8 kHz (frequenze audio della USB) → campioni per il Pluto."""
        import numpy as np
        y = self.i1(y)
        idx = (self.ri + np.arange(len(y))) % 24
        self.ri = (self.ri + len(y)) % 24
        y = self.i2(y * self.rot[idx]) * 2.0                   # il filtro complesso dimezza (solo metà spettro)
        n = len(y)
        if self.inv:
            y = np.conj(y)                                     # I/Q scambiati nel Pluto: torna USB a +off
        m = np.max(np.abs(y)) if n else 0.0
        if m > 1.0:
            y = y / m                                          # mai oltre il fondo scala
        return (y.astype(np.complex64).view(np.float32) * SSB_PEAK).astype('<i2').tobytes()   # I, Q, I, Q…


def open_mic(dev):
    """Microfono a 48 kHz (o a quello che regge), ridotto a 8 kHz in una coda. → (stream, coda)"""
    import numpy as np
    import sounddevice as sd
    q = queue.Queue(maxsize=100)
    st = {'rate': None}
    dec = {'h': None, 'tail': None, 'ph': 0}

    def cb(indata, frames, t, status):
        x = indata[:, 0].astype(np.float32)
        r = st['rate']
        if r == 8000:
            y = x
        else:
            z = np.concatenate([dec['tail'], x])
            f = np.convolve(z, dec['h'], mode='valid')
            dec['tail'] = z[-(len(dec['h']) - 1):]
            step = r / 8000.0
            pos = dec['ph'] + np.arange(0, len(f) - dec['ph'], step)
            dec['ph'] = float(pos[-1] + step - len(f)) if len(pos) else dec['ph'] - len(f)
            y = np.interp(pos, np.arange(len(f)), f).astype(np.float32)
        try:
            q.put_nowait(y)
        except queue.Full:
            pass
    dv = None if dev in (None, '') else dev
    last = None
    for rate in (48000, 44100, 16000, 8000):
        try:
            if rate != 8000:
                dec['h'] = _kaiser_lp(63, 3300.0, float(rate)).astype(np.float32)
                dec['tail'] = np.zeros(62, np.float32)
            st['rate'] = rate
            s = sd.InputStream(device=dv, channels=1, samplerate=rate, blocksize=int(rate * 0.01),
                               dtype='float32', callback=cb)
            s.start()
            return s, q
        except Exception as e:
            last = e
    raise RuntimeError(f'microfono non disponibile ({last})')


def mic_record(dev, secs=5.0):
    """Registra secs secondi dal microfono (8 kHz, senza guadagno): per la prova voce."""
    import numpy as np
    s, q = open_mic(dev)
    out, n, end = [], 0, time.time() + secs + 2.0
    try:
        while n < secs * 8000 and time.time() < end:
            try:
                y = q.get(timeout=0.2)
            except queue.Empty:
                continue
            out.append(y)
            n += len(y)
    finally:
        try:
            s.stop()
            s.close()
        except Exception:
            pass
    return np.concatenate(out)[:int(secs * 8000)] if out else np.zeros(0, np.float32)


def voice_preview(raw, gain_db, proc_db, eq):
    """La voce come la sente chi ti ascolta: VoiceProc a blocchi come in trasmissione, poi la banda 300–2700."""
    import numpy as np
    vp, ec, hc = VoiceProc(gain_db, proc_db, eq), EnvClip(), _ssb_bp()
    tc = np.zeros(200, np.complex64)
    out = []
    for i in range(0, len(raw), 128):
        z = np.concatenate([tc, vp(raw[i:i + 128]).astype(np.complex64)])
        tc = z[-200:]
        out.append(ec(np.convolve(z, hc, mode='valid'), vp.proc))
    y = np.concatenate(out) if out else np.zeros(0, np.complex64)
    return (y.real * 2.0).astype(np.float32)


def wav_b64(x, rate=8000):
    """Campioni float (±1) → WAV 16 bit in base64, per farli suonare alla pagina."""
    import base64
    import io
    import wave
    import numpy as np
    b = io.BytesIO()
    with wave.open(b, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32000).astype('<i2').tobytes())
    return base64.b64encode(b.getvalue()).decode('ascii')


class SsbTx:
    """PTT in SSB: microfono → SsbMod → flusso continuo verso il Pluto (connessione a parte)."""

    def __init__(self, tx, on_status, on_level=None, on_end=None):
        self.tx, self.on_status, self.on_level, self.on_end = tx, on_status, on_level, on_end
        self.on_stats = None
        self.alive = False
        self.th = None

    audio_off = 0.0          # Hz fra la portante (soppressa) e la frequenza sulla scala: 0 in SSB
    gen = None

    def start(self, f_up, mic=None, gain_db=0.0, proc_db=10.0, eq=None, gen=None):
        """gen: generatore di audio al posto del microfono (RTTY). f_up = frequenza sulla scala: con gen la
        portante va a f_up − gen.mark, così il mark cade esattamente lì."""
        if self.alive:
            return
        self.alive = True
        self.mod = None
        self.gen = gen
        self.audio_off = float(gen.mark) if gen is not None else 0.0
        self.th = threading.Thread(target=self._run, args=(f_up, mic, gain_db, proc_db, eq), daemon=True)
        self.th.start()

    def stop(self):
        self.alive = False

    def set(self, gain_db=None, proc_db=None, eq=None):
        """Regolazioni della voce mentre si trasmette."""
        m = getattr(self, 'mod', None)
        if m is not None:
            m.vp.set(gain_db, proc_db, eq)

    def _run(self, f_up, mic, gain_db, proc_db, eq):
        import numpy as np
        tx = self.tx
        mod = SsbMod(gain_db, proc_db, eq, tx.inv)
        self.mod = mod
        s = ms = None
        f = None
        gen = self.gen
        what = 'RTTY' if gen is not None else 'SSB'
        try:
            if gen is None:
                ms, mq = open_mic(mic)
            tx.set_freq_off(f_up - self.audio_off, SSB_OFF)
            tx.lo_on(True)
            s = socket.create_connection((tx.host, tx.PORT), timeout=5)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            f = s.makefile('rb')

            def rint():
                while True:
                    b = f.readline()
                    if not b:
                        raise OSError('il Pluto ha chiuso il flusso TX')
                    t = b.decode('ascii', 'replace').strip()
                    if t:
                        return int(t)
            words = (getattr(tx, 'tx_nch', 2) + 31) // 32
            mask = ''.join(f'{(getattr(tx, "tx_mask", 3) >> (32 * w)) & 0xFFFFFFFF:08x}' for w in reversed(range(words)))
            s.sendall(f'SET {tx.dds} BUFFERS_COUNT 8\r\n'.encode('ascii'))   # 128 ms di riserva nel Pluto
            rint()
            s.sendall(f'OPEN {tx.dds} {SSB_N} {mask}\r\n'.encode('ascii'))
            if rint() < 0:
                raise RuntimeError('il Pluto non apre il flusso TX')
            buf = np.zeros(0, np.float32)
            need = SSB_N // 288
            t0 = time.perf_counter()
            keyed = False
            lastlev = 0.0
            st = {'zeri': 0, 'tagli': 0, 'wmax': 0.0, 'mod': 0.0, 'nb': 0, 't': time.perf_counter()}
            while self.alive and time.perf_counter() - t0 < SSB_MAX_S:
                if gen is not None:
                    if gen.done:
                        break
                    data = mod.up(gen.read(need))
                    s.sendall(f'WRITEBUF {tx.dds} {len(data)}\r\n'.encode('ascii') + data)
                    if rint() < 0:
                        raise RuntimeError('errore scrivendo i campioni')
                    if not keyed:
                        tx.key(True)
                        keyed = True
                    continue
                while len(buf) < need:
                    try:
                        buf = np.concatenate([buf, mq.get(timeout=0.05)])
                    except queue.Empty:
                        buf = np.concatenate([buf, np.zeros(need - len(buf), np.float32)])   # microfono in ritardo
                        st['zeri'] += 1
                if len(buf) > 8000 * 0.25:                     # troppo indietro (orologi diversi): taglio
                    buf = buf[-need:]
                    st['tagli'] += 1
                a, buf = buf[:need], buf[need:]
                m0 = time.perf_counter()
                data = mod(a)
                w0 = time.perf_counter()
                st['mod'] += w0 - m0
                s.sendall(f'WRITEBUF {tx.dds} {len(data)}\r\n'.encode('ascii') + data)
                if rint() < 0:
                    raise RuntimeError('errore scrivendo i campioni')
                st['wmax'] = max(st['wmax'], time.perf_counter() - w0)
                st['nb'] += 1
                if self.on_stats and time.perf_counter() - st['t'] >= 3.0:
                    el = time.perf_counter() - st['t']
                    self.on_stats(f"TX SSB: {st['nb'] * SSB_N / el / 1e6:.3f} MS/s · modulatore {st['mod'] / el * 100:.0f}% · "
                                  f"scrittura max {st['wmax'] * 1000:.0f} ms · microfono vuoto {st['zeri']} · tagli {st['tagli']}")
                    st.update(zeri=0, tagli=0, wmax=0.0, mod=0.0, nb=0, t=time.perf_counter())
                if not keyed:
                    tx.key(True)                               # il livello sale dopo il primo buffer: niente botto
                    keyed = True
                now = time.perf_counter()
                if self.on_level and now - lastlev > 0.2:
                    lastlev = now
                    self.on_level(mod.vp.level, mod.vp.comp_db)
            if time.perf_counter() - t0 >= SSB_MAX_S:
                self.on_status(f'TX {what}: 5 minuti di PTT, spento per sicurezza')
        except Exception as e:
            self.on_status(f'TX {what}: {e}')
        finally:
            self.alive = False
            if gen is not None and gen.done:
                time.sleep(0.15)                               # il Pluto ha ancora 8 buffer in coda: falli uscire
            try:
                tx.key(False)
            except Exception:
                pass
            if s is not None:
                try:
                    s.sendall(f'CLOSE {tx.dds}\r\n'.encode('ascii'))
                except Exception:
                    pass
                try:
                    s.close()
                except OSError:
                    pass
            if ms is not None:
                try:
                    ms.stop()
                    ms.close()
                except Exception:
                    pass
            try:
                tx.lo_on(False)
                tx._dds()                                      # di nuovo il generatore di toni per il CW
                tx._write(tx.dds, tx.dch['TX1_I_F1'], 'raw', 1)
            except Exception:
                pass
            if self.on_end:
                self.on_end()



RTTY_BAUD = 8000.0 / 176          # 45,45 baud: 176 campioni audio per bit
RTTY_SHIFT = 170.0
RTTY_MARK = 1500.0                # tono del mark nella USB (lo space 170 Hz sotto): la portante va 1500 Hz sotto
_BD_L = {'E': 1, 'A': 3, ' ': 4, 'S': 5, 'I': 6, 'U': 7, 'D': 9, 'R': 10, 'J': 11, 'N': 12, 'F': 13, 'C': 14,
         'K': 15, 'T': 16, 'Z': 17, 'L': 18, 'W': 19, 'H': 20, 'Y': 21, 'P': 22, 'Q': 23, 'O': 24, 'B': 25,
         'G': 26, 'M': 28, 'X': 29, 'V': 30}
# cifre: tabella US-TTY (MMTTY, N1MM, il nostro skimmer)
_BD_F = {'3': 1, '-': 3, '8': 6, '7': 7, '$': 9, '4': 10, "'": 11, ',': 12, '!': 13, ':': 14, '(': 15,
         '5': 16, '"': 17, ')': 18, '2': 19, '#': 20, '6': 21, '0': 22, '1': 23, '9': 24, '?': 25, '&': 26,
         '.': 28, '/': 29, ';': 30, '=': 30}
BD_LTRS, BD_FIGS, BD_CR, BD_LF = 31, 27, 8, 2


def baudot(text):
    """Testo → [(codice, carattere mostrato)]. LTRS/FIGS messi da soli; dopo lo spazio si torna alle lettere
    (USOS, come fanno quasi tutti i decodificatori). Caratteri che il Baudot non ha: saltati."""
    out, sh = [], 'L'
    for ch in text.upper():
        if ch == '\n':
            out += [(BD_CR, ''), (BD_LF, '\n')]
            continue
        if ch == ' ':
            out.append((4, ' '))
            sh = 'L'
            continue
        if ch in _BD_L:
            if sh != 'L':
                out.append((BD_LTRS, ''))
                sh = 'L'
            out.append((_BD_L[ch], ch))
        elif ch in _BD_F:
            if sh != 'F':
                out.append((BD_FIGS, ''))
                sh = 'F'
            out.append((_BD_F[ch], ch))
    return out


class RttyGen:
    """Audio RTTY generato qui (non dal microfono): 45,45 baud, shift 170 Hz, 1 start, 5 dati, 1,5 stop,
    fase continua e passaggi mark/space ammorbiditi (spettro stretto, niente click).

    send() mette il testo in coda (anche mentre trasmette); all'inizio e alla fine qualche LTRS ("diddle") per
    far agganciare chi riceve. Quando la coda è vuota e la coda finale è uscita, done = True e il PTT si spegne."""

    def __init__(self, mark=RTTY_MARK, shift=RTTY_SHIFT, fs=8000.0, on_prog=None, pre=8, post=2):
        import numpy as np
        self.mark, self.shift, self.fs = float(mark), float(shift), float(fs)
        self.on_prog = on_prog
        self.post = post
        self.q = deque()                         # (codice, carattere mostrato)
        self.lock = threading.Lock()
        self.sbuf = np.zeros(0, np.float32)      # frequenze istantanee (Hz) già pronte
        self.fsm = np.full(23, self.mark, np.float32)   # coda del filtro che ammorbidisce i passaggi
        self.w = np.hanning(25)[1:-1].astype(np.float32)
        self.w /= self.w.sum()
        self.ph = 0.0
        self.done = False
        self.ending = False
        self.sent, self.idle = '', 0
        self.tail = 0
        for _ in range(pre):
            self.q.append((BD_LTRS, ''))

    def send(self, text):
        with self.lock:
            self.q.extend(baudot(str(text)))
            self.ending = False
            self.tail = 0
            self._prog()

    def rest(self):
        return ''.join(c for _, c in self.q)

    def _prog(self):
        k = (self.sent[-40:], self.rest())
        if self.on_prog and k != getattr(self, '_last', None):
            self._last = k
            try:
                self.on_prog(*k)
            except Exception:
                pass

    def _char(self, code):
        """Un carattere: start (space), 5 bit (dal meno significativo, 1 = mark), 1,5 stop (mark)."""
        import numpy as np
        n = int(round(self.fs / RTTY_BAUD))
        bits = [0] + [(code >> i) & 1 for i in range(5)]
        f = np.concatenate([np.full(n, self.mark if b else self.mark - self.shift, np.float32) for b in bits] +
                           [np.full(n + n // 2, self.mark, np.float32)])
        return f

    def _more(self):
        import numpy as np
        with self.lock:
            if self.q:
                code, ch = self.q.popleft()
                self.sent += ch
                self._prog()
            elif self.tail < self.post:
                code = BD_LTRS                   # coda: un paio di LTRS, poi fine (se non arriva altro)
                self.tail += 1
            else:
                self.ending = True
                return np.full(int(self.fs * 0.02), self.mark, np.float32)
        return self._char(code)

    def read(self, n):
        import numpy as np
        while len(self.sbuf) < n and not self.ending:
            self.sbuf = np.concatenate([self.sbuf, self._more()])
        if len(self.sbuf) < n:
            self.sbuf = np.concatenate([self.sbuf, np.full(n - len(self.sbuf), self.mark, np.float32)])
            self.done = True                     # questo è l'ultimo pezzo
        f, self.sbuf = self.sbuf[:n], self.sbuf[n:]
        z = np.concatenate([self.fsm, f])
        fs = np.convolve(z, self.w, mode='valid')[:n]
        self.fsm = z[-len(self.fsm):]
        ph = self.ph + np.cumsum(2 * np.pi * fs / self.fs)
        self.ph = float(ph[-1] % (2 * np.pi))
        return (0.95 * np.exp(1j * ph)).astype(np.complex64)


class EchoTrack:
    """Segue la propria eco: mentre si trasmette in CW raccoglie la discesa attorno a dove deve arrivare il
    segnale e, a fine messaggio, misura di quanti Hz è spostato (deriva del quarzo del Pluto che si scalda…).

    La propria eco si riconosce dalla manipolazione: il Pluto annota quando il tasto va giù e su (key_log), e
    qui si cerca il segnale che si accende e si spegne con quel ritmo, con un ritardo fra 0,1 e 1,2 s (satellite
    e tempi di elaborazione). Prima prendeva il più forte entro ±400 Hz e in una fetta affollata agganciava
    un'altra stazione, spostando il TX di 150 Hz a ogni messaggio. Se nessun segnale segue la manipolazione
    non corregge niente."""

    def __init__(self, fs, expect_off, key_log=None, clock=None, span=420.0):
        self.fs, self.e = float(fs), float(expect_off)
        self.span = float(span)                       # ±420 Hz per l'eco; ±40 kHz per Calibra TX
        self.d = max(1, int(self.fs // max(2000.0, 2.4 * self.span)))
        self.fs2 = self.fs / self.d
        self.ph = 0.0
        self.buf = []
        self.blk = []                                 # (ora di arrivo, primo campione, quanti)
        self.n = 0
        self.on = False
        self.key_log = key_log if key_log is not None else []
        self.clock = clock or time.perf_counter

    def feed(self, iq):
        import numpy as np
        if not self.on:
            return
        t = self.clock()
        n = len(iq)
        ph = self.ph + (-2 * np.pi * self.e / self.fs) * np.arange(n)
        self.ph = float((ph[-1] - 2 * np.pi * self.e / self.fs) % (2 * np.pi)) if n else self.ph
        y = iq * np.exp(1j * ph).astype(np.complex64)
        m = n // self.d
        if m:
            self.buf.append(y[:m * self.d].reshape(m, self.d).mean(1))
            self.blk.append((t, self.n, m))
            self.n += m

    def _times(self, s):
        """Ora (dell'orologio del programma) del campione decimato s: il blocco arriva subito dopo il suo ultimo."""
        import numpy as np
        b = np.array(self.blk, float)
        i = np.clip(np.searchsorted(b[:, 1], s, side='right') - 1, 0, len(b) - 1)
        return b[i, 0] - (b[i, 1] + b[i, 2] - s) / self.fs2

    def _keyed(self, ev_t, ev_d, t):
        import numpy as np
        i = np.searchsorted(ev_t, t, side='right') - 1
        return np.where(i >= 0, ev_d[np.clip(i, 0, len(ev_d) - 1)], 0.0)

    def result(self):
        import numpy as np
        if self.n < self.fs2 * 1.0 or not self.blk:
            return None
        x = np.concatenate(self.buf)
        ev = sorted(self.key_log)
        if len(ev) < 4:
            return None
        ev_t = np.array([e[0] for e in ev])
        ev_d = np.array([1.0 if e[1] else 0.0 for e in ev])
        # spettrogramma: 10 ms per riga; ~16 Hz per colonna per l'eco, ~50 Hz per Calibra TX (±40 kHz)
        hop = max(1, int(round(self.fs2 * 0.01)))
        nf = 1 << max(7, int(np.ceil(np.log2(self.fs2 / (16.0 if self.span < 2000 else 50.0)))))
        nfr = (len(x) - nf) // hop
        if nfr < 80:
            return None
        idx = np.arange(nf)[None, :] + hop * np.arange(nfr)[:, None]
        S = np.abs(np.fft.fft(x[idx] * np.hanning(nf).astype(np.float32), axis=1)) ** 2
        f = np.fft.fftfreq(nf, 1 / self.fs2)
        sel = np.abs(f) <= self.span
        P = 10 * np.log10(S[:, sel] + 1e-20)
        tf = self._times(hop * np.arange(nfr) + nf / 2)
        # quale colonna si accende col mio tasto, e con che ritardo
        lags = np.arange(-0.05, 1.50, 0.01)                # satellite ~0,27 s + elaborazione (il banco di prova: quasi 0)
        K = np.array([self._keyed(ev_t, ev_d, tf - L) for L in lags])
        ks = K.std(axis=1)
        ok = ks > 0.15
        if not ok.any():
            return None
        Kz = (K[ok] - K[ok].mean(1, keepdims=True)) / ks[ok, None]
        Pz = (P - P.mean(0)) / (P.std(0) + 1e-9)
        C = Kz @ Pz / nfr
        li, bi = np.unravel_index(int(np.argmax(C)), C.shape)
        corr = float(C[li, bi])
        if corr < 0.45:
            return None                                   # nessun segnale segue la mia manipolazione
        lag = float(lags[ok][li])
        # frequenza fine: solo i campioni in cui la mia eco c'è, FFT lunga attorno alla colonna trovata
        keyed = self._keyed(ev_t, ev_d, self._times(np.arange(len(x))) - lag) > 0.5
        xo = (x * keyed).astype(np.complex64)
        N = 1 << int(np.ceil(np.log2(len(xo) * 4)))
        sp = np.abs(np.fft.fft(xo, N)) ** 2
        ff = np.fft.fftfreq(N, 1 / self.fs2)
        f0 = float(f[sel][bi])
        win = np.abs(ff - f0) <= 1.6 * self.fs2 / nf
        j = np.nonzero(win)[0][np.argmax(sp[win])]
        a, b, c = np.log(sp[j - 1] + 1e-30), np.log(sp[j] + 1e-30), np.log(sp[(j + 1) % N] + 1e-30)
        den = a - 2 * b + c
        dd = 0.5 * (a - c) / den if den != 0 else 0.0
        on = S[:, sel][K[ok][li] > 0.5].mean(0) if (K[ok][li] > 0.5).any() else S[:, sel].mean(0)
        snr = 10 * np.log10(on[bi] / (np.median(on) + 1e-30))
        if snr < 10:
            return None
        self.lag, self.corr = lag, corr
        return float(ff[j] + dd * self.fs2 / N), float(snr)


class ToneSnap:
    """Clic su una traccia CW: trova la portante della stazione entro ±80 Hz dal clic, al Hz.

    Ascolta fino a 2,5 s (la stazione può essere a tasto alzato) e tiene il massimo di ogni riga dello spettro:
    così conta quando il tasto è giù. Con il TX calibrato si trasmette esattamente sopra la stazione."""

    def __init__(self, fs, expect_off, span=80.0):
        self.fs, self.e, self.span = float(fs), float(expect_off), float(span)
        self.d = max(1, int(self.fs // 800))
        self.fs2 = self.fs / self.d
        self.ph = 0.0
        self.buf = []
        self.n = 0
        self.tried = 0

    def feed(self, iq):
        import numpy as np
        n = len(iq)
        ph = self.ph + (-2 * np.pi * self.e / self.fs) * np.arange(n)
        self.ph = float((ph[-1] - 2 * np.pi * self.e / self.fs) % (2 * np.pi)) if n else self.ph
        y = iq * np.exp(1j * ph).astype(np.complex64)
        m = n // self.d
        if m:
            self.buf.append(y[:m * self.d].reshape(m, self.d).mean(1))
            self.n += m

    def result(self):
        """(scarto in Hz dal clic, dB sul rumore) o None se ancora non c'è niente di chiaro."""
        import numpy as np
        if self.n < self.fs2 * 0.6:
            return None
        x = np.concatenate(self.buf)
        seg = int(self.fs2 * 0.25)
        N = 4096
        w = np.hanning(seg).astype(np.float32)
        mh = None
        for i in range(0, len(x) - seg + 1, seg // 2):
            p = np.abs(np.fft.fft(x[i:i + seg] * w, N)) ** 2
            mh = p if mh is None else np.maximum(mh, p)
        f = np.fft.fftfreq(N, 1 / self.fs2)
        win = np.abs(f) <= self.span
        idx = np.nonzero(win)[0]
        k = idx[np.argmax(mh[idx])]
        noise = float(np.median(mh[np.abs(f) <= self.fs2 * 0.45]))
        snr = 10 * np.log10(mh[k] / (noise + 1e-30))
        if snr < 12:
            return None
        a, b, c = np.log(mh[k - 1] + 1e-30), np.log(mh[k] + 1e-30), np.log(mh[(k + 1) % N] + 1e-30)
        den = a - 2 * b + c
        dd = 0.5 * (a - c) / den if den != 0 else 0.0
        return float(f[k] + dd * self.fs2 / N), float(snr)


# ------------------------------------------------------------------ paddle su porta COM
# Punto su CTS, linea su DSR (o DCD, come l'HaliKey Serial). DTR e RTS accesi: con il cavo RS-232 (DB9)
# il comune del paddle va su DTR; con le schedine TTL il comune va a massa (gli ingressi hanno il pull-up).

DECODE = {}
for _k, _v in MORSE.items():
    DECODE.setdefault(_v, _k)
DECODE.update({'.-.-.': '<AR>', '...-.-': '<SK>', '-.--.': '<KN>', '-...-.-': '<BK>'})

PAD_HANG = 0.8          # s: dopo l'ultimo segno l'oscillatore TX resta acceso (fra lettere e parole)
PAD_DEBOUNCE = 0.003    # s: tasto verticale, contatto stabile per almeno 3 ms


def com_ports():
    """Porte COM presenti: [(nome, descrizione)]."""
    try:
        from serial.tools import list_ports
        return [(p.device, p.description or p.device) for p in sorted(list_ports.comports(), key=lambda p: p.device)]
    except Exception:
        return []


class PaddleIn:
    """Legge le due leve dalla porta COM."""

    def __init__(self, port):
        import serial
        s = serial.Serial()
        s.port = port
        s.baudrate = 9600
        s.dtr = True                  # alimenta il comune (cavo RS-232, schema A)
        s.rts = True
        s.open()
        self.s = s

    def read(self):
        s = self.s
        return bool(s.cts), bool(s.dsr or s.cd)

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


class PaddleKeyer:
    """Keyer per il paddle: iambico A o B, oppure tasto verticale.

    tx_fn() → (PlutoTx, CwKeyer) se si può trasmettere, altrimenti None: allora suona solo il sidetone (esercizio).
    on_side(on): sidetone nella pagina; on_char(c): segno decodificato; on_ptt(on): inizio e fine della trasmissione."""
    POLL = 0.001

    def __init__(self, inp, on_side, on_char, on_ptt, on_status):
        self.inp = inp
        self.on_side, self.on_char, self.on_ptt, self.on_status = on_side, on_char, on_ptt, on_status
        self.mode = 'b'               # 'b' iambico B, 'a' iambico A, 's' tasto verticale
        self.swap = False
        self.wpm = 24
        self.tx_fn = None
        self.freq_fn = None
        self.alive = True
        self.busy = False             # leve in uso (anche solo sidetone)
        self.tx = None                # PlutoTx mentre si trasmette davvero
        self.r_off = 0.004
        self.err = ''
        self.mem_dit = self.mem_dah = False
        threading.Thread(target=self._run, daemon=True).start()

    def close(self):
        self.alive = False

    # ---- leve
    def _paddles(self):
        a, b = self.inp.read()
        return (b, a) if self.swap else (a, b)

    def _u(self):
        return 1.2 / max(5, min(60, self.wpm))

    # ---- trasmissione
    def _ptt_on(self):
        if self.busy:
            return
        self.busy = True
        self.tx = None
        r = self.tx_fn() if self.tx_fn else None
        if r is not None:
            tx, keyer = r
            if keyer is not None and keyer.busy:        # il paddle ha la precedenza sulle macro
                keyer.stop()
                end = time.perf_counter() + 0.3
                while keyer.busy and time.perf_counter() < end:
                    time.sleep(0.002)
            f = self.freq_fn() if self.freq_fn else None
            if not tx.enabled:
                pass
            elif f is None:
                self.on_status('Paddle: VFO A fuori dal transponder o vicino a un beacon, solo sidetone')
            else:
                try:
                    tx.set_freq(f)
                    tx.lo_on(True)
                    self.tx = tx
                except Exception as e:
                    self.on_status(f'Paddle: TX non riuscito ({e})')
        self.on_ptt(True, self.tx is not None)

    def _ptt_off(self):
        if not self.busy:
            return
        tx, self.tx = self.tx, None
        if tx is not None:
            try:
                tx.key(False)
                tx.lo_on(False)
            except Exception:
                pass
        self.busy = False
        self.on_ptt(False, tx is not None)

    def _key(self, down):
        self.on_side(down)
        tx = self.tx
        if tx is None:
            return
        try:
            tx.key(down)
        except Exception as e:
            self.tx = None
            self.on_status(f'Paddle: TX interrotto ({e})')

    # ---- iambico
    def _watch(self, until, dah):
        """Fino a until guarda la leva opposta (memoria del segno): serve all'iambico B e allo squeeze."""
        while True:
            d = until - time.perf_counter()
            if d <= 0:
                return
            dit_p, dah_p = self._paddles()
            if dah and dit_p:
                self.mem_dit = True
            if not dah and dah_p:
                self.mem_dah = True
            time.sleep(min(self.POLL, max(0.0, d - 0.0005)) if d > 0.0015 else 0)

    def _element(self, dah):
        u = self._u()
        t0 = time.perf_counter()
        end_on = t0 + (3 * u if dah else u)
        self._key(True)
        self._watch(end_on - (self.r_off if self.tx else 0.0), dah)
        t1 = time.perf_counter()
        self._key(False)
        if self.tx is not None:
            self.r_off = 0.8 * self.r_off + 0.2 * min(0.02, time.perf_counter() - t1)
        self._watch(end_on + u, dah)              # spazio di un'unità dopo il segno

    def _next(self, last):
        dit, dah = self._paddles()
        if self.mode == 'b':
            dit, dah = dit or self.mem_dit, dah or self.mem_dah
        self.mem_dit = self.mem_dah = False
        if last == '.':
            return '-' if dah else '.' if dit else None
        return '.' if dit else '-' if dah else None

    def _run(self):
        _hires_timer()
        last, elems, t_end, word = None, '', time.perf_counter(), False
        st_down, st_cand, st_since, t_down = False, False, 0.0, 0.0
        try:
            while self.alive:
                now = time.perf_counter()
                u = self._u()
                if self.mode == 's':
                    dit, dah = self._paddles()
                    v = dit or dah
                    if v != st_cand:
                        st_cand, st_since = v, now
                    if st_cand != st_down and now - st_since >= PAD_DEBOUNCE:
                        st_down = st_cand
                        if st_down:
                            self._ptt_on()
                            self._key(True)
                            t_down = now
                        else:
                            self._key(False)
                            elems += '-' if now - t_down > 2 * u else '.'
                            t_end, word = now, True
                    if st_down and now - t_down > MAX_KEY_S:
                        self._key(False)
                        self.on_status('Paddle: chiave giù da troppo tempo, alzata')
                        st_down = False
                        t_down = now
                    nxt = None
                    if st_down:
                        time.sleep(self.POLL)
                        continue
                else:
                    nxt = self._next(last)
                if nxt is not None:
                    self._ptt_on()
                    self._element(nxt == '-')
                    elems += nxt
                    last = nxt
                    t_end, word = time.perf_counter(), True
                    continue
                last = None
                gap = now - t_end
                if elems and gap > 1.5 * u:               # fine lettera (3 unità, 1 già passata)
                    self.on_char(DECODE.get(elems, '?'))
                    elems = ''
                if word and not elems and gap > 4.5 * u:   # fine parola (7 unità)
                    self.on_char(' ')
                    word = False
                if self.busy and gap > max(PAD_HANG, 8 * u) and not st_down:
                    self._ptt_off()
                time.sleep(self.POLL)
        except Exception as e:
            self.err = str(e)
            self.on_status(f'Paddle: porta COM persa ({e})')
        finally:
            try:
                self.on_side(False)
            except Exception:
                pass
            self._ptt_off()
            self.alive = False
            self.inp.close()
