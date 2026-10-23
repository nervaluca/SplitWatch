"""FZR Skimmer - motore DSP in streaming.

Riceve blocchi IQ complessi (numpy complex64) e produce:
  - righe di waterfall (uint8, una ogni 10 ms)
  - audio CW del DX (int16, ~8 kHz)
  - parole CW decodificate per ogni segnale attivo
  - eventi "ascolto": dove ha risposto la stazione dopo il 5NN del DX
Solo numpy: deve girare anche su Python 32 bit.
"""
import math
from collections import deque
import re
import numpy as np

FRAMES_PER_S = 200          # STFT del decoder: un frame ogni 5 ms
ROW_EVERY = 2               # 1 riga di waterfall ogni 2 frame = 100 righe/s
MAX_BINS = 4096             # righe di waterfall inviate al browser al massimo
THR_ON, THR_OFF = 0.6, 0.5   # soglie del canale: frazione (logaritmica) tra rumore e segnale
S_DECAY = 0.9              # discesa del livello del segnale ogni 0,1 s
CHAR_G, WORD_G = 2.0, 4.0   # spazi tra lettere e tra parole, in unità
TIMING_ROBUST = 0
SMOOTH = 1                  # frame della media mobile sull'inviluppo
REPLAY_S = 3.0               # secondi ridecodificati quando si apre un canale nuovo
ENV_LEN = 1                 # finestra dell'inviluppo dei canali, in multipli di nfft
OPEN = 4                    # righe (10 ms l'una) dell'apertura che toglie i click dal waterfall

HOT = re.compile(r'^(5NN|599|5NNTU|TU5NN|ENN|5N|NN|R5NN)$')
JUNK = set('EIST')
NOISE = set('EISHT5·')                      # lettere che il rumore genera facilmente
KEEP = {'5NN', '599', 'TU', 'R', 'K', 'NN', '5N', 'R5NN', 'TU5NN', '5NNTU', 'QRZ', 'UP', 'CQ', 'TEST',
        # parole e abbreviazioni dei QSO in CW: non vanno mai scartate
        'DE', 'ES', 'HI', 'ST', 'TNX', 'TKS', 'TX', 'RX', 'RST', 'UR', 'FB', 'OM', 'OP', 'QTH', 'QSL', 'QSO',
        'QRL', 'QRM', 'QRN', 'QRP', 'QRO', 'QRQ', 'QRS', 'QRT', 'QRV', 'QSB', 'QSY', 'QRU', 'NAME', 'AGN', 'PSE',
        'KN', 'BK', 'SK', 'AR', 'AS', 'RR', 'GM', 'GA', 'GE', 'GN', 'GL', 'WX', 'HR', 'HW', 'ANT', 'RIG', 'PWR',
        'FER', 'FOR', 'NR', 'NW', 'SRI', 'CFM', 'TIME', 'DR', 'TEMP', 'VY', 'GD', 'BEST', 'TEST', 'IS', 'HIS',
        'SIG', '73', '88', '599', 'SSB', 'CW', 'WAG', 'WPX', 'CQWW', 'IOTA', 'DX', 'POTA', 'SOTA', 'QRZ?', 'NR?', 'AGN?'}
CALL_RE = re.compile(r'^(?:[A-Z]{1,2}|\d[A-Z]|[A-Z]\d)\d{1,2}[A-Z]{1,4}(?:/[A-Z0-9]+)?$')


KNOWN_CALLS = set()                          # caricati da MASTER.SCP / MASTER.DTA / callsign.dat
_NEAR = {}                                    # schema con una lettera jolly -> nominativi


def build_index():
    """Indice per trovare i nominativi che differiscono di una sola lettera."""
    _NEAR.clear()
    for c in KNOWN_CALLS:
        for i in range(len(c)):
            _NEAR.setdefault(c[:i] + '?' + c[i + 1:], []).append(c)


def variants(w):
    """La parola e le sue versioni senza 1-2 lettere da rumore (E I S T H) all'inizio o alla fine."""
    out = [w]
    noise = 'EISTH'
    for a in range(0, 3):
        for b in range(0, 3):
            if a == 0 and b == 0:
                continue
            core = w[a:len(w) - b if b else None]
            if len(core) >= 3 and all(c in noise for c in w[:a]) and all(c in noise for c in w[len(w) - b:] if b):
                out.append(core)
    return out


def classify(w):
    """(parola da mostrare, 'v' se nominativo noto | 's' se correzione dedotta | None)."""
    vs = variants(w)
    for v in vs:
        if is_known(v):
            return v, 'v'
    for v in vs:
        sg = suggest(v)
        if sg:
            return sg, 's'
    return w, None


def suggest(w):
    """Nominativo noto che differisce di una sola lettera (solo se la corrispondenza è unica)."""
    if not _NEAR or len(w) < 4 or not CALL_RE.match(w) or is_known(w):
        return None
    found = set()
    for i in range(len(w)):
        found.update(_NEAR.get(w[:i] + '?' + w[i + 1:], ()))
        if len(found) > 1:
            return None
    return found.pop() if len(found) == 1 else None


def is_known(w):
    """Nominativo presente nell'elenco (anche con /P, /QRP, EA8/... ecc.)."""
    if not KNOWN_CALLS or len(w) < 3:
        return False
    if w in KNOWN_CALLS:
        return True
    if '/' in w:
        parts = [p for p in w.split('/') if len(p) >= 3]
        return any(p in KNOWN_CALLS for p in parts)
    return False


def is_junk(w):
    """Parole tipiche del rumore: quasi solo E I S H T 5."""
    if w in KEEP or is_known(w):
        return False
    if CALL_RE.match(w) and sum(c in NOISE for c in w) / len(w) < 0.75:
        return False
    if '·' in w and len(w) <= 3:
        return True
    return sum(c in NOISE for c in w) / len(w) >= 0.6


def _lev(a, b):
    d = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        p, d[0] = d[0], i
        for j, cb in enumerate(b, 1):
            p, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, p + (0 if ca == cb or ca == '·' else 1))
    return d[-1]


def _kmeans2(v):
    """Due gruppi di durate (punti, linee). None se i segni sono tutti simili o troppo pochi."""
    if len(v) < 4:
        return None
    a = sorted(v)
    lo, hi = a[0], a[-1]
    if hi < 1.8 * lo:
        return None
    for _ in range(6):
        m = math.sqrt(lo * hi)
        g1 = [x for x in a if x < m]
        g2 = [x for x in a if x >= m]
        if not g1 or not g2:
            return None
        lo, hi = sum(g1) / len(g1), sum(g2) / len(g2)
    if hi < 2.0 * lo or hi > 5.0 * lo or not 0.02 <= lo <= 0.15:
        return None
    return lo, hi


NOISE_WORDS = {'EE', 'ES', 'HI', 'IS', 'ST', 'II', 'SS', 'IE', 'EI', 'TE', 'ET'}
_EXCH = re.compile(r'^(?:\d{1,4}|[A-Z]\d{1,3}|\d{1,3}[A-Z])$')       # numeri e scambi da contest (001, E14, 15A)


def segment(w):
    """Divide una parola fatta di più parole attaccate. None se non si divide in pezzi tutti validi."""
    n = len(w)
    half = n // 2
    if n % 2 == 0 and w[:half] == w[half:] and CALL_RE.match(w[:half]):
        return [w[:half], w[half:]]
    best = [None] * (n + 1)                      # best[i] = (punteggio, pezzi) per w[:i]
    best[0] = (0.0, [])
    for i in range(n):
        if best[i] is None:
            continue
        sc, parts = best[i]
        prev = parts[-1] if parts else ''
        for j in range(i + 1, min(n, i + 10) + 1):
            tok = w[i:j]
            if tok in KEEP and tok not in NOISE_WORDS and (len(tok) >= 2 or tok in ('R', 'K')):
                v = 2.0 if len(tok) >= 2 else 0.3
            elif len(tok) >= 4 and CALL_RE.match(tok):
                v = 3.0
            elif _EXCH.match(tok) and prev in ('5NN', '599', 'NR', 'TU5NN', 'R5NN', 'ENN'):
                v = 1.0
            elif j - i <= 6:
                v = -2.0                                 # pezzo illeggibile: si scarta (vale solo se il resto è buono)
                tok = '\x00' + tok
            else:
                continue
            cand = (sc + v - 0.5, parts + [tok])
            if best[j] is None or cand[0] > best[j][0]:
                best[j] = cand
    if best[n] is None or len(best[n][1]) < 2 or best[n][0] <= 0:
        return None
    good = [p for p in best[n][1] if not p.startswith('\x00')]
    if not good or (len(good) == 1 and len(good[0]) < 4):
        return None
    return good


def _close(w, call):
    """w è una lettura storpiata di call? (una o due lettere sbagliate/in più, oppure l'inizio o la fine persi)"""
    if w == call:
        return True
    if len(w) >= 3 and (call.endswith(w) or call.startswith(w)) and len(call) - len(w) <= 3:
        return True
    lim = 1 if len(call) <= 4 else 2
    return abs(len(w) - len(call)) <= lim and _lev(w, call) <= lim


def _nextpow2(v):
    return 1 << int(math.ceil(math.log2(v)))


class Channel:
    """Decoder CW di un singolo segnale, a una frequenza precisa (non legata ai bin FFT)."""
    __slots__ = ('gh', 'id', 'f', 'on', 't_on', 't_off', 'pend', 'pend_start', 'dot', 'dash', 'ge', 'sym', 'word',
                 't0', 'last', 'is_dx', 'last_good', 'n', 's', 'seen', 'k', 'calls', 'mks', 'tail')

    def __init__(self, cid, f, t, is_dx=False):
        self.id = cid
        self.f = float(f)       # Hz dal centro
        self.k = 0              # bin più vicino (per il rumore e la deduplica)
        self.on = False
        self.t_on = t
        self.t_off = t
        self.pend = None        # ultimo segno, in attesa di sapere se è un buco nel segnale
        self.pend_start = t
        self.dot = 0.045        # durata del punto (s), si adatta
        self.dash = 0.135
        self.ge = 0.045         # spazio tra i segni
        self.sym = ''
        self.word = ''
        self.t0 = None
        self.last = t           # ultima attività
        self.seen = t           # ultima volta che il segnale è stato visto nello spettro
        self.is_dx = is_dx
        self.last_good = -99.0  # ultima parola "vera" (non fatta solo di punti)
        self.n = None           # rumore (ampiezza) sul canale
        self.s = 0.0            # livello del segnale (picco che scende piano)
        self.mks = []           # durate degli ultimi segni
        self.tail = None        # ultimi frame (media mobile)
        self.calls = []         # ultimi nominativi decodificati (per il consenso)
        self.gh = 0.0           # quanto i suoi segni coincidono con quelli di un vicino più forte (fantasma)

    def unit(self):
        return 0.5 * (self.dot + self.ge)

    def mmin(self):
        return max(0.010, 0.3 * self.dot)

    def gmin(self):
        return max(0.008, 0.3 * self.dot)


MORSE = {'.-': 'A', '-...': 'B', '-.-.': 'C', '-..': 'D', '.': 'E', '..-.': 'F', '--.': 'G', '....': 'H',
         '..': 'I', '.---': 'J', '-.-': 'K', '.-..': 'L', '--': 'M', '-.': 'N', '---': 'O', '.--.': 'P',
         '--.-': 'Q', '.-.': 'R', '...': 'S', '-': 'T', '..-': 'U', '...-': 'V', '.--': 'W', '-..-': 'X',
         '-.--': 'Y', '--..': 'Z', '-----': '0', '.----': '1', '..---': '2', '...--': '3', '....-': '4',
         '.....': '5', '-....': '6', '--...': '7', '---..': '8', '----.': '9', '..--..': '?', '-..-.': '/',
         '-...-': '=', '.-.-.': '+', '.-.-': '·'}


def blackman_harris(n):
    k = 2 * np.pi * np.arange(n) / (n - 1)
    return (0.35875 - 0.48829 * np.cos(k) + 0.14128 * np.cos(2 * k) - 0.01168 * np.cos(3 * k)).astype(np.float32)


def _hyst(E, on, off, prev):
    """Soglia con isteresi, vettoriale: 1 sopra 'on', 0 sotto 'off', altrimenti resta com'era."""
    m, n = E.shape
    st = np.full((m, n), -1, np.int8)
    st[E < off[None, :]] = 0
    st[E > on[None, :]] = 1
    idx = np.where(st >= 0, np.arange(m)[:, None], -1)
    idx = np.maximum.accumulate(idx, axis=0)
    cols = np.broadcast_to(np.arange(n)[None, :], (m, n))
    return np.where(idx >= 0, st[np.maximum(idx, 0), cols], prev[None, :]).astype(np.int8)


def agc_run(env, st, arate, slow_s):
    """AGC campione per campione: attacco 2 ms, discesa lenta (slow_s); se il segnale resta più di 20 dB
    sotto il livello dell'AGC per 0,35 s (fine della trasmissione, un segnale fortissimo sparito)
    la discesa diventa rapida (30 ms): il ricevitore torna a sentire subito.
    st = [livello, media breve, campioni in «hang»]. Restituisce il livello per ogni campione."""
    ag, fe, hang = st
    att = math.exp(-1.0 / (0.002 * arate))
    dec = math.exp(-1.0 / (slow_s * arate))
    fast = math.exp(-1.0 / (0.03 * arate))
    kf = 1.0 - math.exp(-1.0 / (0.03 * arate))
    hold = int(0.35 * arate)
    g = np.empty(len(env))
    for i, v in enumerate(env.tolist()):
        fe += (v - fe) * kf
        if v > ag:
            ag = v + (ag - v) * att
            hang = 0
        else:
            if ag > 1000.0 * fe:                    # 60 dB sotto: non è una pausa, è finito un segnale enorme
                hang += 1
            else:
                hang = 0
            ag *= fast if hang > hold else dec
        if ag < 1e-7:
            ag = 1e-7
        g[i] = ag
    st[0], st[1], st[2] = ag, fe, hang
    return g


class SsbRx:
    """Un ricevitore del modo «ricevitore puro» (SSB, CW, RTTY, FT8).

    Porta a 0 Hz il centro della banda passante, la filtra (±half) convertendo a ~8 kHz con un vero
    passa-basso, poi la rimette in banda audio centrata su `aud` Hz (sb = +1 dritta, -1 rovesciata,
    come LSB). Per i filtri stretti (CW, RTTY) c'è un secondo filtro a 8 kHz, leggero.
    Fase continua: si può spostare senza clic."""

    def __init__(self, fs, adec, half=1350.0):
        self.fs, self.d = float(fs), int(adec)
        self.arate = self.fs / self.d
        self.half = float(half)
        wide = max(self.half, 600.0)
        n = int(5.5 * self.fs / 250.0) | 1                      # Blackman: transizione 250 Hz
        t = np.arange(n) - (n - 1) / 2
        h = np.sinc(2 * (wide + 125) / self.fs * t) * np.blackman(n)
        self.h = (h / h.sum()).astype(np.float32)[::-1].copy()
        self.tail = np.zeros(n - 1, np.complex64)
        self.h2 = None
        if self.half < 600:                                      # secondo filtro stretto a ~8 kHz
            tr = max(40.0, self.half * 0.5)
            n2 = int(5.5 * self.arate / tr) | 1
            t2 = np.arange(n2) - (n2 - 1) / 2
            h2 = np.sinc(2 * (self.half + tr / 2) / self.arate * t2) * np.blackman(n2)
            self.h2 = (h2 / h2.sum()).astype(np.complex64)
            self.tail2 = np.zeros(n2 - 1, np.complex64)
        self.ph_in = 0.0
        self.ph_out = 0.0
        self.pos = 0                                            # primo campione da tenere nel prossimo blocco
        self.agc_st = [1e-6, 1e-6, 0]
        self.agc_slow = 0.4                                     # discesa dell'AGC (s)
        self.agc_fixed = None                                   # AGC spento: livello fisso
        self.pk = 0.0                                           # S-meter: potenza massima (blocchi da ~20 ms) dall'ultima lettura

    def process(self, iq, center_hz, aud_hz, sb, freeze=False):
        """iq al campionamento pieno -> audio reale a arate (float, AGC già applicato)."""
        n = len(iq)
        ph = self.ph_in + (-2 * np.pi * center_hz / self.fs) * np.arange(n)
        self.ph_in = float((ph[-1] - 2 * np.pi * center_hz / self.fs) % (2 * np.pi)) if n else self.ph_in
        y = (iq * np.exp(1j * ph).astype(np.complex64)).astype(np.complex64)
        z = np.concatenate([self.tail, y])
        L = len(self.h)
        idx = np.arange(self.pos, len(z) - L + 1, self.d)
        if len(idx):
            w = np.lib.stride_tricks.sliding_window_view(z, L)[idx]
            b = (w @ self.h).astype(np.complex64)
            self.pos = int(idx[-1] + self.d - (len(z) - (L - 1)))
        else:
            b = np.zeros(0, np.complex64)
            self.pos -= n
        self.tail = z[-(L - 1):].copy()
        if not len(b):
            return np.zeros(0, np.float64)
        if self.h2 is not None:
            zz = np.concatenate([self.tail2, b])
            b2 = np.convolve(zz, self.h2, mode='valid').astype(np.complex64)
            self.tail2 = zz[-(len(self.h2) - 1):]
            b = b2
        self.pk = max(self.pk, float(np.mean(np.abs(b) ** 2)))
        tt = self.ph_out + (2 * np.pi * sb * aud_hz / self.arate) * np.arange(len(b))
        self.ph_out = float((tt[-1] + 2 * np.pi * sb * aud_hz / self.arate) % (2 * np.pi))
        a = np.real(b * np.exp(1j * tt))
        # AGC: attacco 2 ms, discesa ~0,4 s (rapida dopo la propria trasmissione)
        env = np.abs(b).astype(np.float64)
        if freeze:                                        # in TX (PTT): AGC fermo al livello di prima, il proprio segnale muto
            out = a / (self.agc_st[0] * 2.2)
            out[env > 6 * self.agc_st[0]] = 0.0
            return out
        if self.agc_fixed is not None:                    # AGC spento: guadagno fisso (RF gain)
            g = np.full(len(env), self.agc_fixed)
        else:
            g = agc_run(env, self.agc_st, self.arate, self.agc_slow)
        return a / (g * 2.2)                              # margine: il limitatore finale non distorce


class NoiseBlanker:
    """NB: toglie gli impulsi (scariche, motori, recinti elettrici) sull'IQ a banda piena, prima del filtro,
    come il noise blanker delle radio. Un campione più alto di k volte il livello tipico della banda viene
    azzerato insieme a ~0,2 ms intorno."""
    K = {1: 10.0, 2: 6.5, 3: 4.5}

    def __init__(self, fs):
        self.fs = float(fs)
        self.lvl = None
        self.guard = max(3, int(self.fs * 0.0002))
        self.hits = 0

    def process(self, iq, level):
        if not level or not len(iq):
            return iq
        mag = np.abs(iq)
        med = float(np.median(mag)) + 1e-12
        self.lvl = med if self.lvl is None else (self.lvl * 0.9 + med * 0.1 if med > self.lvl else med)
        bad = mag > self.K.get(int(level), 6.5) * self.lvl
        if not bad.any():
            return iq
        bad = np.convolve(bad.astype(np.float32), np.ones(2 * self.guard + 1, np.float32), mode='same') > 0
        self.hits += int(bad.sum())
        out = iq.copy()
        out[bad] = 0
        return out


class AudioNR:
    """NR (riduzione del rumore) e NOTCH automatico sull'audio di un ricevitore (~8 kHz).

    Analisi a blocchi di 32 ms sovrapposti a metà (finestra radice di Hann: ricostruzione perfetta).
    NR: stima il rumore di fondo di ogni riga dello spettro (sale piano, scende subito) e attenua le righe
    che non stanno sopra il rumore (filtro di Wiener con un minimo, così la voce resta naturale).
    NOTCH: trova le portanti fisse (chi accorda, battimenti) che restano più di ~1 s e le spegne.
    Ritardo fisso di 32 ms, sempre uguale (anche spento), così RX1 e RX2 restano allineati."""
    LEV = {1: (1.6, 0.32), 2: (2.6, 0.18), 3: (4.0, 0.10)}      # (sottrazione, minimo del guadagno)

    def __init__(self, rate, n=256):
        self.rate, self.n, self.h = float(rate), int(n), int(n) // 2
        k = np.arange(self.n)
        self.w = np.sqrt(0.5 - 0.5 * np.cos(2 * np.pi * k / self.n))
        self.inb = np.zeros(0)
        self.ola = np.zeros(self.n - self.h)
        self.outq = np.zeros(self.n)                              # ritardo iniziale
        nb = self.h + 1
        self.psm = None
        self.noise = None
        self.g = np.ones(nb)
        self.long = np.zeros(nb)
        f = np.arange(nb) * self.rate / self.n
        self.band = (f > 120) & (f < 3400)

    def process(self, x, level=0, notch=False):
        x = np.asarray(x, np.float64)
        if not len(x):
            return x
        self.inb = np.concatenate([self.inb, x])
        outs = []
        while len(self.inb) >= self.n:
            fr = self.inb[:self.n]
            self.inb = self.inb[self.h:]
            X = np.fft.rfft(fr * self.w)
            P = X.real ** 2 + X.imag ** 2
            if self.psm is None:
                self.psm = P.copy()
                self.noise = P.copy() + 1e-12
            self.psm = 0.65 * self.psm + 0.35 * P
            lo = self.psm < self.noise
            self.noise = np.where(lo, 0.85 * self.noise + 0.15 * self.psm, self.noise * 1.004) + 1e-15
            G = np.ones_like(P)
            if level:
                a, fl = self.LEV.get(int(level), self.LEV[2])
                g = np.clip(1.0 - a * self.noise / (self.psm + 1e-15), fl, 1.0)
                g = np.maximum(g, np.convolve(g, [0.25, 0.5, 0.25], mode='same'))   # i picchi (voce, nota) restano pieni
                self.g = 0.5 * self.g + 0.5 * g                     # meno «rumore musicale»
                G = self.g
            self.long = 0.985 * self.long + 0.015 * P
            if notch:
                med = float(np.median(self.long[self.band])) + 1e-15
                side = 0.5 * (np.roll(self.long, 2) + np.roll(self.long, -2)) + 1e-15
                tone = self.band & (self.long > 12 * med) & (self.long > 6 * side)
                if tone.any():
                    t = np.convolve(tone.astype(float), [1, 1, 1], mode='same') > 0
                    G = np.where(t, 0.03, G)
            y = np.fft.irfft(X * G, self.n) * self.w
            y[:self.n - self.h] += self.ola
            outs.append(y[:self.h])
            self.ola = y[self.h:].copy()
        if outs:
            self.outq = np.concatenate([self.outq] + outs)
        out = self.outq[:len(x)]
        if len(out) < len(x):
            out = np.concatenate([out, np.zeros(len(x) - len(out))])
        self.outq = self.outq[len(x):]
        return out


class NarrowRx:
    """Ricevitore FM (banda stretta) e AM, per VHF/UHF e aeronautica. Stessa interfaccia di SsbRx.

    Canale portato a 0 Hz e filtrato a un campionamento intermedio (~32 kHz, abbastanza per 16 kHz di canale),
    demodulato (FM: discriminatore + deenfasi 750 µs; AM: inviluppo), poi audio 300–3400 Hz
    (via i toni CTCSS) ridotto a ~8 kHz come gli altri ricevitori. SQUELCH: apre quando il canale sta
    `sql` dB sopra il rumore di fondo (0 = sempre aperto)."""

    def __init__(self, fs, adec, half=6000.0, mode='fm'):
        self.fs, self.adec, self.mode = float(fs), int(adec), mode
        self.arate = self.fs / self.adec
        self.half = float(half)
        # fattore finale (audio): il più grande fra 4, 3, 2 che divide adec lasciando ≥ 2,2 volte il canale
        self.d2 = next((k for k in (4, 3, 2) if self.adec % k == 0 and self.fs / (self.adec // k) >= 2.2 * self.half), 1)
        self.d1 = self.adec // self.d2
        self.r1 = self.fs / self.d1
        tr = max(1500.0, 0.25 * self.half)
        n = int(5.5 * self.fs / tr) | 1
        t = np.arange(n) - (n - 1) / 2
        h = np.sinc(2 * (self.half + tr / 2) / self.fs * t) * np.blackman(n)
        self.h = (h / h.sum()).astype(np.float32)[::-1].copy()
        self.tail = np.zeros(n - 1, np.complex64)
        self.pos = 0
        # audio: passa-banda 300–3400 Hz al campionamento intermedio, poi ÷ d2
        na = int(4.0 * self.r1 / 300.0) | 1
        ta = np.arange(na) - (na - 1) / 2
        lp = lambda fc: np.sinc(2 * fc / self.r1 * ta)
        ha = (2 * 3400 / self.r1 * lp(3400) - 2 * 300 / self.r1 * lp(300)) * np.blackman(na)
        self.ha = ha.astype(np.float64)[::-1].copy()
        self.atail = np.zeros(na - 1)
        self.apos = 0
        self.prev = np.complex64(0)
        a = math.exp(-1.0 / (self.r1 * 750e-6))
        kd = int(math.log(1e-3) / math.log(a)) + 1
        self.h_de = (1 - a) * a ** np.arange(kd)
        self.de_tail = np.zeros(kd - 1)
        self.ph_in = 0.0
        self.agc_st = [1e-6, 1e-6, 0]
        self.agc_slow = 0.4
        self.agc_fixed = None
        self.pk = 0.0
        self.sql = 0.0
        self.floor = None
        self.open = True
        self.gate = 1.0
        self.am_lvl = None

    def process(self, iq, center_hz, aud_hz=0.0, sb=1, freeze=False):
        n = len(iq)
        ph = self.ph_in + (-2 * np.pi * center_hz / self.fs) * np.arange(n)
        self.ph_in = float((ph[-1] - 2 * np.pi * center_hz / self.fs) % (2 * np.pi)) if n else self.ph_in
        y = (iq * np.exp(1j * ph).astype(np.complex64)).astype(np.complex64)
        z = np.concatenate([self.tail, y])
        L = len(self.h)
        idx = np.arange(self.pos, len(z) - L + 1, self.d1)
        if len(idx):
            w = np.lib.stride_tricks.sliding_window_view(z, L)[idx]
            b = (w @ self.h).astype(np.complex64)
            self.pos = int(idx[-1] + self.d1 - (len(z) - (L - 1)))
        else:
            b = np.zeros(0, np.complex64)
            self.pos -= n
        self.tail = z[-(L - 1):].copy()
        if not len(b):
            return np.zeros(0, np.float64)
        p = float(np.mean(np.abs(b) ** 2)) + 1e-20
        self.pk = max(self.pk, p)
        # squelch: rumore di fondo = minimo lento della potenza del canale
        if self.floor is None or p < self.floor:
            self.floor = p if self.floor is None else 0.7 * self.floor + 0.3 * p
        else:
            self.floor *= 1.0 + 0.02 * len(b) / self.r1          # risale piano (~2 %/s)
        snr = 10 * math.log10(p / self.floor)
        if self.sql <= 0:
            self.open = True
        elif self.open:
            self.open = snr > self.sql - 3
        else:
            self.open = snr > self.sql
        if self.mode == 'am':
            env = np.abs(b).astype(np.float64)
            m = float(np.mean(env)) + 1e-12
            self.am_lvl = m if self.am_lvl is None else (self.am_lvl * 0.9 + m * 0.1)
            dem = env / self.am_lvl - 1.0
        else:
            bb = np.concatenate([[self.prev], b])
            self.prev = b[-1]
            dem = np.angle(bb[1:] * np.conj(bb[:-1])).astype(np.float64) * (self.r1 / (2 * np.pi * 3000.0))
            dd = np.concatenate([self.de_tail, dem])            # deenfasi 750 µs (6 dB/ottava), come FIR
            out = np.convolve(dd, self.h_de, mode='valid')
            self.de_tail = dd[-(len(self.h_de) - 1):]
            dem = out * 4.0
        zz = np.concatenate([self.atail, dem])
        La = len(self.ha)
        ia = np.arange(self.apos, len(zz) - La + 1, self.d2)
        if len(ia):
            wa = np.lib.stride_tricks.sliding_window_view(zz, La)[ia]
            a_out = wa @ self.ha
            self.apos = int(ia[-1] + self.d2 - (len(zz) - (La - 1)))
        else:
            a_out = np.zeros(0)
            self.apos -= len(dem)
        self.atail = zz[-(La - 1):].copy()
        # porta dello squelch con rampa di ~10 ms (niente clic)
        target = 1.0 if (self.open and not freeze) else 0.0
        if len(a_out):
            k = min(1.0, 100.0 / self.arate)
            g = target + (self.gate - target) * (1 - k) ** np.arange(1, len(a_out) + 1)
            self.gate = float(g[-1])
            a_out = a_out * g
        return a_out * 0.5


def rx_geometry(mode, sb, bw, ssb_bw=2700.0):
    """(scostamento del centro della banda passante dalla frequenza dell'RX, metà larghezza, centro audio, verso).

    SSB: frequenza = portante soppressa, 200–2900 Hz sopra (USB) o sotto (LSB).
    FT8: frequenza di sintonia USB, 100–3100 Hz sopra.
    RTTY: frequenza = mark (come gli Icom), space 170 Hz sotto, in LSB: mark a 2125 Hz e space a 2295 Hz.
    CW: frequenza = quella del segnale, sentito a 600 Hz, filtro = «Filtro RX»."""
    if mode in ('fm', 'am'):
        return 0.0, max(2500.0, float(ssb_bw) / 2), 0.0, 1          # FM/AM: canale centrato, larghezza intera
    if mode == 'ft8':
        return 1600.0, 1500.0, 1600.0, 1
    if mode == 'rtty':
        return -85.0, 300.0, 2210.0, -1
    if mode == 'rxcw':
        return 0.0, max(25.0, bw / 2), 600.0, 1
    w = max(1500.0, min(3600.0, float(ssb_bw)))           # SSB: banda passante da 200 Hz a 200 + larghezza
    return sb * (200.0 + w / 2), w / 2, 200.0 + w / 2, sb


class Engine:
    def __init__(self, fs, dx_off_hz=0.0, bfo=650.0):
        self.fs = float(fs)
        self.nfft = _nextpow2(self.fs / 47.0)             # ~47 Hz per bin (decoder: 21 ms a 96 kHz)
        self.nfft = max(1024, self.nfft)
        self.hop = int(round(self.fs / FRAMES_PER_S))
        self.binhz = self.fs / self.nfft
        self.win = np.hanning(self.nfft).astype(np.float32)
        # inviluppo dei canali: finestra di Blackman lunga ELEN campioni (più selettiva verso i vicini)
        self.elen = int(ENV_LEN * self.nfft)
        self.ewin = np.blackman(self.elen).astype(np.float32) if ENV_LEN != 1 else self.win
        # waterfall e ricerca dei segnali: finestra lunga il doppio, Blackman-Harris (niente "baffi" laterali)
        self.L = 2 * self.nfft
        self.wwin = blackman_harris(self.L)
        self.lbinhz = self.fs / self.L
        self.pool = max(1, self.L // MAX_BINS)
        self.nbins_out = self.L // self.pool
        self.wf_bw = self.lbinhz * self.pool
        self.wf_f0 = (-self.L / 2 + (self.pool - 1) / 2) * self.lbinhz
        self.buf = np.zeros(0, np.complex64)
        self.hist = np.zeros(0, np.complex64)              # campioni già consumati (per ridecodificare i canali nuovi)
        self.hist_keep = int(REPLAY_S * self.fs + 0.2 * self.fs) + self.L
        self.n_in = 0                                      # campioni consumati (tempo)
        self.frame_no = 0
        # stato per bin (finestra corta): serve alla ricerca dell'ascolto
        nb = self.nfft
        self.nf = None
        self.pk = np.zeros(nb, np.float32)
        self.duty = np.zeros(nb, np.float32)               # frazione di tempo attivo (30 s)
        self.duty_f = np.zeros(nb, np.float32)             # idem, veloce (5 s)
        # stato per bin (finestra lunga): ricerca dei segnali
        self.lnf = None
        self.lduty = np.zeros(self.L, np.float32)
        self.lavg = np.zeros(self.L, np.float32)
        self.row_floor = None
        self.wf_hist = []
        self.channels = {}
        self.next_id = 1
        self.recent = []                                   # (t, f, testo) per deduplica
        self.out_words = []
        self.out_rows = []
        self.out_listen = []
        self.out_audio = []
        # DX e ascolto
        self.bfo = bfo
        self.min_snr = 5.0                                 # soglia minima del segnale (x rumore)
        self.up_lo, self.up_hi = 150.0, 10000.0
        self.listen_on = True                              # ricerca dell'ascolto (spenta in modalità contest)             # zona UP dove cercare l'ascolto (Hz dal DX)
        self.set_dx(dx_off_hz)
        self.over = None                                   # [t_inizio, t_ultima_attività, parole]
        self.pending_listen = []
        self.ring = []                                     # ultimi ~2 s di frame 'strong' (tempo, matrice)
        # audio
        self.adec = max(1, int(round(self.fs / 8000.0)))
        self.arate = self.fs / self.adec
        self.nco_ph = 0.0
        self.box_tail = np.zeros(0, np.complex64)
        self.bw = 500.0
        self._design_filter()
        self.bfo_ph = 0.0
        self.agc_st = [1e-6, 1e-6, 0]
        self.tx_on = False                                 # propria trasmissione in corso (banda intera saturata)
        self.ptt = False
        self.agc_snap = deque()
        self.agc_mode, self.rf_gain = 'slow', 60.0
        self.agc_fixed, self.agc_slow = None, 1.0
        self.tx_ref = None
        # SSB: due ricevitori (DX e RX2) in stereo; la decodifica CW si spegne
        self.ssb = False
        self.sb = 1                                        # +1 USB, -1 LSB
        self.rx2_off = self.dx_off + 5000.0
        self.ssb_rx = None
        self.rx_geo = None
        self.decode_on = True
        self.nb_lvl, self.nr_lvl, self.notch = 0, 0, False
        self.nblank = NoiseBlanker(fs)
        self.anr = (AudioNR(self.arate), AudioNR(self.arate))
        self.sm_pk = 0.0                                   # S-meter del DX (CW): potenza nella banda passante

    # ------------------------------------------------------------------ comandi
    def _design_filter(self):
        """Filtro audio del DX: passa-basso complesso = banda passante larga bw (Hz)."""
        half = max(30.0, self.bw / 2)
        taps = int(5.5 * self.arate / max(40.0, 0.6 * half)) | 1
        taps = max(129, min(1201, taps))
        n = np.arange(taps) - (taps - 1) / 2
        h = np.sinc(2 * (half / self.arate) * n) * np.blackman(taps)
        self.alp = (h / h.sum()).astype(np.float32)
        self.afir_state = np.zeros(taps - 1, np.complex64)

    def set_bw(self, hz):
        self.bw = float(min(3000, max(50, hz)))
        self._design_filter()

    def set_dx(self, off_hz):
        self.dx_off = float(off_hz)
        if getattr(self, 'rtty', None) is not None:
            self.rtty.set_dx(self.dx_off)
        self.dx_k = int(round(self.dx_off / self.binhz)) % self.nfft
        for k in [k for k, c in self.channels.items() if c.is_dx]:
            del self.channels[k]
        self.over = None
        self.pending_listen = []

    @property
    def t(self):
        return self.n_in / self.fs

    # ------------------------------------------------------------------ ingresso
    def feed(self, iq):
        self.buf = np.concatenate([self.buf, iq.astype(np.complex64, copy=False)])
        self._audio(iq)
        nfr = (len(self.buf) - self.L) // self.hop + 1
        if nfr < FRAMES_PER_S // 10:          # elabora a blocchi di almeno 0,1 s
            return
        step = FRAMES_PER_S // 10
        done = 0
        ar = np.arange(self.nfft)
        while done < nfr:
            m = min(step, nfr - done)
            idx = ar[None, :] + self.hop * (done + np.arange(m))[:, None]
            Fw = self.buf[idx] * self.win
            A = np.abs(np.fft.fft(Fw, axis=1)).astype(np.float32)
            # righe del waterfall: una ogni ROW_EVERY frame, finestra lunga
            ri = [i for i in range(m) if (self.frame_no + i) % ROW_EVERY == 0]
            rows = None
            if ri:
                idx2 = np.arange(self.L)[None, :] + self.hop * (done + np.array(ri))[:, None]
                rows = (ri, (np.abs(np.fft.fft(self.buf[idx2] * self.wwin, axis=1)) ** 2).astype(np.float32))
            if self.elen == self.nfft:
                Fe = Fw
            else:
                Fe = self.buf[np.arange(self.elen)[None, :] + self.hop * (done + np.arange(m))[:, None]] * self.ewin
            self._frames(A, Fe, m, rows, done)
            done += m
        cut = nfr * self.hop
        self.hist = np.concatenate([self.hist, self.buf[:cut]])[-self.hist_keep:]
        self.buf = self.buf[cut:]
        self.n_in += cut

    # ------------------------------------------------------------------ audio DX
    def set_rx(self, on, mode='ssb', sb=1, rx2_off=None, bw=500.0, ssb_bw=2700.0):
        """Ricevitore puro (due RX in stereo) acceso/spento, con il modo di demodulazione."""
        self.ssb = bool(on)
        self.sb = 1 if sb >= 0 else -1
        if rx2_off is not None:
            self.rx2_off = float(rx2_off)
        self.decode_on = not self.ssb
        self.rx_mode = mode if self.ssb else None
        if self.ssb and mode == 'rtty':
            if getattr(self, 'rtty', None) is None:
                from rtty import RttySkimmer
                self.rtty = RttySkimmer(self.fs, self.nfft)
            self.rtty.set_dx(self.dx_off)
        else:
            self.rtty = None
        if self.ssb:
            geo = rx_geometry(mode, self.sb, bw, ssb_bw)
            kind = 'n' + mode if mode in ('fm', 'am') else 's'
            if self.ssb_rx is None or self.rx_geo is None or self.rx_geo[1] != geo[1] or getattr(self, 'rx_kind', None) != kind:
                self.rx_kind = kind
                if mode in ('fm', 'am'):
                    self.ssb_rx = tuple(NarrowRx(self.fs, self.adec, geo[1], mode) for _ in range(2))
                    for r in self.ssb_rx:
                        r.sql = getattr(self, 'sql_db', 0.0)
                else:
                    self.ssb_rx = (SsbRx(self.fs, self.adec, geo[1]), SsbRx(self.fs, self.adec, geo[1]))
            self.rx_geo = geo
            self._agc_apply()
            for k in [k for k, c in self.channels.items()]:
                del self.channels[k]
        else:
            self.ssb_rx = None
            self.rx_geo = None

    def set_ssb(self, on, sb=None, rx2_off=None):
        self.set_rx(on, 'ssb', self.sb if sb is None else sb, rx2_off)

    def _tx_guard(self, iq):
        """Riconosce la propria trasmissione: tutta la banda sale di colpo (il TX rientra nel Perseus).
        Durante il TX l'audio tace; appena finisce l'AGC riparte da zero, così il segnale torna subito."""
        if not len(iq):
            return self.tx_on
        db = 10 * math.log10(float(np.mean(np.abs(iq) ** 2)) + 1e-20)
        if self.tx_ref is None:
            self.tx_ref = db
        if not self.tx_on and db > self.tx_ref + 25:
            self.tx_on = True
        elif self.tx_on and db < self.tx_ref + 15:
            self.tx_on = False
            self.ptt = False                                  # il TX è finito adesso: non aspetto il PTT della radio
            self.agc_st = [1e-6, 1e-6, 0]
            if self.ssb_rx:
                for r in self.ssb_rx:
                    r.agc_st = [1e-6, 1e-6, 0]
        if not self.tx_on:
            k = min(1.0, len(iq) / self.fs / 3.0)             # riferimento: media (in dB) degli ultimi ~3 s
            self.tx_ref += (db - self.tx_ref) * k
        return self.tx_on

    AGC_TIMES = {'fast': (0.12, 0.15), 'med': (0.35, 0.4), 'slow': (1.0, 1.2)}   # discesa (s): CW, ricevitore

    def set_agc(self, mode='slow', rf=60.0):
        """AGC come su una radio: FAST, MED, SLOW oppure OFF con guadagno fisso (RF gain 0–100)."""
        self.agc_mode = mode if mode in ('fast', 'med', 'slow', 'off') else 'slow'
        self.rf_gain = max(0.0, min(100.0, float(rf)))
        self._agc_apply()

    def _agc_apply(self):
        mode = getattr(self, 'agc_mode', 'slow')
        # AGC spento: il livello fisso scende di 1 dB per ogni punto di RF gain (100 = massimo guadagno)
        fixed = 10 ** ((-20.0 - getattr(self, 'rf_gain', 60.0)) / 20.0) if mode == 'off' else None
        self.agc_fixed = fixed
        self.agc_slow = self.AGC_TIMES.get(mode, (1.0, 1.2))[0]
        if self.ssb_rx:
            for r in self.ssb_rx:
                r.agc_fixed = fixed
                r.agc_slow = self.AGC_TIMES.get(mode, (1.0, 1.2))[1]

    def set_sql(self, db):
        """Squelch FM/AM in dB sopra il rumore (0 = aperto)."""
        self.sql_db = max(0.0, min(40.0, float(db)))
        if self.ssb_rx:
            for r in self.ssb_rx:
                if isinstance(r, NarrowRx):
                    r.sql = self.sql_db

    def sq_state(self):
        if self.ssb_rx and isinstance(self.ssb_rx[0], NarrowRx):
            return [bool(r.open) for r in self.ssb_rx]
        return None

    def set_nrnb(self, nb=0, nr=0, notch=False):
        """NB (0–3), NR (0–3) e NOTCH automatico (solo SSB)."""
        self.nb_lvl = max(0, min(3, int(nb)))
        self.nr_lvl = max(0, min(3, int(nr)))
        self.notch = bool(notch)

    def set_ptt(self, on):
        """PTT letto dalla radio (CAT). La lettura arriva con un po' di ritardo, quindi in TX riporto l'AGC
        a com'era ~0,6 s prima e lo tengo fermo: il proprio segnale (sopra quel livello) è muto, e appena
        sparisce si sente subito la stazione, senza aspettare che la radio dica «fine TX»."""
        on = bool(on)
        if on and not getattr(self, 'ptt', False) and self.agc_snap:
            snap = self.agc_snap[0]
            self.agc_st = list(snap[0])
            if self.ssb_rx and snap[1]:
                for r, st in zip(self.ssb_rx, snap[1]):
                    r.agc_st = list(st)
        self.ptt = on

    def _audio(self, iq):
        if not getattr(self, 'ptt', False):
            # fotografie dell'AGC degli ultimi ~0,6 s (servono quando arriva il PTT)
            self.agc_snap.append((list(self.agc_st), [list(r.agc_st) for r in self.ssb_rx] if self.ssb_rx else None))
            while len(self.agc_snap) > max(4, int(0.6 * self.fs / max(1, len(iq)))):
                self.agc_snap.popleft()
        if self._tx_guard(iq):
            # in trasmissione: silenzio (stessa quantità di campioni, così l'audio non perde il passo)
            n = len(iq) + len(self.box_tail)
            m = n // self.adec
            self.box_tail = np.zeros(n - m * self.adec, np.complex64)
            if m:
                self.out_audio.append(np.zeros(2 * m if self.ssb and self.ssb_rx else m, '<i2'))
            return
        iq = self.nblank.process(iq, self.nb_lvl)                # NB: impulsi via prima del filtro
        if self.ssb and self.ssb_rx:
            c, _, aud, sb = self.rx_geo
            fz = getattr(self, 'ptt', False)
            a1 = self.ssb_rx[0].process(iq, self.dx_off + c, aud, sb, fz)
            a2 = self.ssb_rx[1].process(iq, self.rx2_off + c, aud, sb, fz)
            nt = self.notch and self.rx_mode == 'ssb'               # il notch spegne le portanti: solo in fonia
            a1 = self.anr[0].process(a1, self.nr_lvl, nt)
            a2 = self.anr[1].process(a2, self.nr_lvl, nt)
            m = min(len(a1), len(a2))
            if m:
                st = np.empty(2 * m, np.float64)
                st[0::2] = a1[:m]                         # canale 0 = DX, canale 1 = RX2 (il mix lo fa la pagina)
                st[1::2] = a2[:m]
                self.out_audio.append((np.tanh(st * 1.2) / 1.2 * 30000).astype('<i2'))
            return
        n = len(iq)
        ph = self.nco_ph + (-2 * np.pi * self.dx_off / self.fs) * np.arange(n)
        y = iq * np.exp(1j * ph).astype(np.complex64)
        self.nco_ph = float((ph[-1] - 2 * np.pi * self.dx_off / self.fs) % (2 * np.pi))
        y = np.concatenate([self.box_tail, y])
        m = len(y) // self.adec
        self.box_tail = y[m * self.adec:]
        if m == 0:
            return
        z = y[:m * self.adec].reshape(m, self.adec).mean(1)          # boxcar + decimazione
        zz = np.concatenate([self.afir_state, z])
        f = np.convolve(zz, self.alp, mode='valid')
        self.afir_state = zz[-(len(self.alp) - 1):]
        if len(f):
            self.sm_pk = max(self.sm_pk, float(np.mean(np.abs(f) ** 2)))
        # AGC campione per campione: attacco rapido (2 ms), discesa lenta (~1 s), nessun gradino tra i blocchi
        env = np.abs(f).astype(np.float64)
        if getattr(self, 'ptt', False):                  # in TX: AGC fermo, il proprio segnale muto
            g = np.full(len(env), self.agc_st[0])
        elif self.agc_fixed is not None:                 # AGC spento: guadagno fisso
            g = np.full(len(env), self.agc_fixed)
        else:
            g = agc_run(env, self.agc_st, self.arate, self.agc_slow)
        tt = self.bfo_ph + 2 * np.pi * self.bfo / self.arate * np.arange(len(f))
        self.bfo_ph = float((tt[-1] + 2 * np.pi * self.bfo / self.arate) % (2 * np.pi))
        a = np.real(f * np.exp(1j * tt)) / (g * 1.6)
        if getattr(self, 'ptt', False):
            a[env > 6 * self.agc_st[0]] = 0.0
        a = self.anr[0].process(a, self.nr_lvl, False)
        self.out_audio.append((np.tanh(a * 1.2) / 1.2 * 30000).astype('<i2'))

    # ------------------------------------------------------------------ frame STFT
    def _ftime(self, j):
        return j * self.hop / self.fs + self.nfft / 2 / self.fs

    def _frames(self, A, Fw, m, rows, done):
        fr_dt = 1.0 / FRAMES_PER_S
        tA = self._ftime(self.frame_no)
        if self.nf is None:
            self.nf = np.percentile(A, 30, axis=0).astype(np.float32) + 1e-9
        p25 = np.partition(A, m // 4, axis=0)[m // 4]
        self.nf = np.where(p25 < self.nf, self.nf * 0.8 + p25 * 0.2, self.nf * 0.985 + p25 * 0.015).astype(np.float32)
        cm = A.max(0)
        self.pk = np.maximum(self.pk * 0.85, cm)
        snr_ok = self.pk > self.min_snr * self.nf
        th = self.nf + 0.5 * (self.pk - self.nf)
        act = (A > th[None, :]) & snr_ok[None, :]
        strong = A > 6.0 * self.nf[None, :]
        sm_ = strong.mean(0)
        self.duty = self.duty * 0.997 + sm_ * 0.003
        self.duty_f = self.duty_f * 0.98 + sm_ * 0.02

        # waterfall e statistiche dei segnali (finestra lunga)
        if rows is not None:
            ri, P = rows
            # rumore = potenza media del rumore (il 25° percentile di una esponenziale è 0,29 x la media)
            if self.lnf is None:
                self.lnf = np.percentile(P, 30, axis=0).astype(np.float32) / 0.357 + 1e-20
            kq = len(P) // 4
            q25 = np.partition(P, kq, axis=0)[kq] / (0.2877 if kq else 0.1)
            self.lnf = np.where(q25 < self.lnf, self.lnf * 0.8 + q25 * 0.2, self.lnf * 0.985 + q25 * 0.015).astype(np.float32)
            R = P / self.lnf[None, :]
            self.lduty = self.lduty * 0.98 + (R > 8.0).mean(0) * 0.02
            self.lavg = self.lavg * 0.97 + R.mean(0) * 0.03
            for i, p in zip(ri, P):
                self._emit_row(tA + i * fr_dt, p)

        tend = tA + m * fr_dt
        if not self.decode_on:                          # ricevitore: niente CW (in RTTY lo skimmer RTTY)
            if getattr(self, 'rtty', None) is not None and self.nf is not None:
                self.out_words += self.rtty.process(A, self.nf, tA, fr_dt)
            self.frame_no += m
            return
        self.new_channels = []
        self._update_channels(tend)
        for ch in self.new_channels:
            self._replay(ch)

        # inviluppo di ogni canale alla sua frequenza esatta (DFT mirata sui frame già finestrati)
        chs = list(self.channels.values())
        if chs:
            fr = np.array([c.f for c in chs])
            W = np.exp(-2j * np.pi * np.arange(self.elen)[:, None] * fr[None, :] / self.fs).astype(np.complex64)
            E = np.abs(Fw @ W).astype(np.float32)
            self._decode_block(chs, E, tA, fr_dt)

        # DX: over e ascolto
        if self.listen_on:
            self._dx_track(strong[:, self.dx_k] & act[:, self.dx_k], tA, fr_dt)
            self._listen_accumulate(strong, act, tA, fr_dt, m)
        self.frame_no += m

    def _levels(self, chs, E, init=False):
        """Rumore e livello del segnale di ogni canale; restituisce le soglie (accensione, spegnimento)."""
        on = np.empty(len(chs), np.float32)
        off = np.empty(len(chs), np.float32)
        kq = len(E) // 4
        p25 = np.partition(E, kq, axis=0)[kq]
        mx = E.max(0)
        for j, c in enumerate(chs):
            if c.n is None or init:
                # rumore: quello già misurato su quel bin (il blocco può essere tutto segnale)
                nb = float(self.nf[c.k]) if self.nf is not None else float(np.percentile(E[:, j], 30))
                c.n = min(nb, float(np.percentile(E[:, j], 30))) + 1e-12 if init else nb + 1e-12
                c.s = float(np.percentile(E[:, j], 99)) if init else float(mx[j])
            else:
                p = float(p25[j])
                c.n = c.n * 0.8 + p * 0.2 if p < c.n else c.n * 0.985 + p * 0.015
                c.s = max(c.s * S_DECAY, float(mx[j]))
            r = c.s / c.n
            if r < self.min_snr:
                on[j] = off[j] = np.inf                      # niente segnale: non decodifico il rumore
            else:
                lr = math.log(r)
                on[j] = c.n * math.exp(THR_ON * lr)
                off[j] = c.n * math.exp(THR_OFF * lr)
        return on, off

    def _decode_block(self, chs, E, tA, dt, init=False):
        if SMOOTH > 1:
            # media mobile (in potenza) lungo il tempo, con la coda del blocco precedente: meno rumore sui segni
            P = E.astype(np.float64) ** 2
            tail = np.array([c.tail if c.tail is not None and len(c.tail) == SMOOTH - 1 else np.repeat(P[:1, j], SMOOTH - 1)
                             for j, c in enumerate(chs)]).T if not init else np.repeat(P[:1], SMOOTH - 1, axis=0)
            PP = np.vstack([tail, P])
            cs = np.cumsum(np.vstack([np.zeros((1, PP.shape[1])), PP]), axis=0)
            E = np.sqrt((cs[SMOOTH:] - cs[:-SMOOTH]) / SMOOTH).astype(np.float32)
            for j, c in enumerate(chs):
                c.tail = PP[-(SMOOTH - 1):, j].copy()
            tA = tA - (SMOOTH - 1) / 2 * dt
        on, off = self._levels(chs, E, init)
        prev = np.array([1 if c.on else 0 for c in chs], np.int8)
        S = _hyst(E, on, off, prev)
        if not init and len(chs) > 1:
            self._ghosts(chs, S)
        D = np.diff(np.vstack([prev[None, :], S]), axis=0)
        fi, ci = np.nonzero(D.T)
        starts = np.searchsorted(fi, np.arange(len(chs) + 1))
        tend = tA + len(E) * dt
        for j, ch in enumerate(chs):
            a, b = starts[j], starts[j + 1]
            for i, dv in zip(ci[a:b].tolist(), D[ci[a:b], j].tolist()):
                t = tA + i * dt
                if dv > 0:
                    self._rise(ch, t)
                else:
                    self._fall(ch, t)
            if not ch.on:
                self._flush(ch, tend)

    def _ghosts(self, chs, S):
        """Un canale i cui segni coincidono sempre con quelli di un vicino molto più forte (entro 220 Hz)
        è un'eco del vicino (bande laterali di manipolazione, click): le sue parole non si mostrano."""
        fr = np.array([c.f for c in chs])
        sv = np.array([c.s for c in chs])
        on = S.sum(0)
        order = np.argsort(fr)
        fs_ = fr[order]
        for j in range(len(chs)):
            if on[j] < 4:
                continue
            lo, hi = np.searchsorted(fs_, [fr[j] - 220, fr[j] + 220])
            best = 0.0
            for a in order[lo:hi].tolist():
                if a != j and sv[a] > 2.0 * sv[j]:
                    ov = float((S[:, j] & S[:, a]).sum()) / max(1.0, float((S[:, j] | S[:, a]).sum()))
                    best = max(best, ov)
            c = chs[j]
            c.gh = 0.8 * c.gh + 0.2 * best

    def _replay(self, ch):
        """Canale appena aperto: decodifica l'ultimo secondo e mezzo, così non si perde l'inizio."""
        R = int(REPLAY_S * FRAMES_PER_S)
        j0 = max(0, self.frame_no - R)
        nfr = self.frame_no - j0
        base = self.n_in - len(self.hist)
        s0 = j0 * self.hop - base
        if nfr < 20 or s0 < 0:
            return
        data = np.concatenate([self.hist, self.buf])
        idx = s0 + np.arange(self.elen)[None, :] + self.hop * np.arange(nfr)[:, None]
        if idx[-1, -1] >= len(data):
            return
        w = np.exp(-2j * np.pi * np.arange(self.elen) * ch.f / self.fs).astype(np.complex64)
        e = np.abs((data[idx] * self.ewin) @ w).astype(np.float32)[:, None]
        # durata del punto stimata dai segni già passati
        c_on, c_off = self._levels([ch], e, init=True)
        if np.isfinite(c_on[0]):
            s = _hyst(e, c_on, c_off, np.zeros(1, np.int8))[:, 0]
            d = np.diff(np.concatenate([[0], s, [0]]))
            st, en = np.nonzero(d > 0)[0], np.nonzero(d < 0)[0]
            mk = (en - st) / FRAMES_PER_S
            mk = mk[mk > 0.015]
            km = _kmeans2(mk.tolist())
            if km:
                ch.dot, ch.dash = km
                ch.ge = ch.dot
        t0 = self._ftime(j0)
        ch.on = False
        ch.t_on = ch.t_off = t0 - 1.0
        self._decode_block([ch], e, t0, 1.0 / FRAMES_PER_S, init=True)

    def _emit_row(self, t, p):
        # "apertura" nel tempo (minimo e poi massimo su OPEN righe): toglie i click e i baffi
        # verticali che durano meno di un punto, lascia intatti punti e linee
        self.wf_hist.append((t, p))
        if len(self.wf_hist) > 2 * OPEN - 1:
            self.wf_hist.pop(0)
        if len(self.wf_hist) < 2 * OPEN - 1:
            return
        H = np.array([q for _, q in self.wf_hist])
        n = len(H)
        ero = np.array([H[i:i + OPEN].min(0) for i in range(n - OPEN + 1)])
        p = ero.max(0)
        t = self.wf_hist[OPEN - 1][0]
        p = np.fft.fftshift(p)
        if self.pool > 1:
            p = p.reshape(-1, self.pool).max(1)
        db = 10 * np.log10(p + 1e-20)
        nb = len(db)
        self.row_n = getattr(self, 'row_n', 0) + 1
        if self.row_floor is None or self.row_n % 5 == 0:
            c = db[nb // 4: 3 * nb // 4]
            fl = float(np.partition(c, len(c) // 2)[len(c) // 2])   # rumore dalla metà centrale
            self.row_floor = fl if self.row_floor is None else self.row_floor * 0.8 + fl * 0.2
        q = np.clip((db - self.row_floor - 2.0) / 32.0, 0, 1) ** 0.8
        self.out_rows.append((t, (q * 255).astype(np.uint8)))

    def _sfreq(self, k):
        L = self.L
        return (k if k < L // 2 else k - L) * self.lbinhz

    def _update_channels(self, t):
        """Trova i segnali CW nello spettro lungo e mette un canale sulla loro frequenza esatta."""
        L = self.L
        a = self.lavg
        d = self.lduty
        # massimi locali (±2 bin = ±47 Hz) che battono (duty) e spiccano sul fondo vicino
        loc = np.ones(L, bool)
        for s in (1, 2):
            loc &= (a > np.roll(a, s)) & (a >= np.roll(a, -s))
        cand = np.nonzero(loc & (d > 0.03) & (a > 2.0))[0]
        if len(cand):
            # deve spiccare sui ±300 Hz attorno (scarta i "baffi" e i click dei segnali fortissimi)
            w = int(round(300 / self.lbinhz))
            nbm = a[(cand[:, None] + np.arange(-w, w + 1)[None, :]) % L]
            med = np.partition(nbm, w, axis=1)[:, w]
            cand = cand[a[cand] > 1.5 * med].tolist()
        if len(cand) > 150:
            cand = sorted(cand, key=lambda k: -a[k])[:150]
        found = []
        for k in cand:
            y0, y1, y2 = (math.log(a[(k - 1) % L] + 1e-9), math.log(a[k] + 1e-9), math.log(a[(k + 1) % L] + 1e-9))
            den = y0 - 2 * y1 + y2
            dl = 0.5 * (y0 - y2) / den if den < 0 else 0.0
            found.append((self._sfreq(k) + max(-0.5, min(0.5, dl)) * self.lbinhz, float(a[k])))
        # bande laterali di un segnale forte (entro 130 Hz e 13 dB sotto): non sono stazioni
        found.sort()
        keep = []
        for i, (f, sa) in enumerate(found):
            if any(abs(f2 - f) < 130 and s2 > 20 * sa for f2, s2 in found):
                continue
            keep.append((f, sa))
        found = keep
        chans = list(self.channels.values())
        dxc = next((c for c in chans if c.is_dx), None)
        # canale del DX: sulla frequenza impostata, agganciato al segnale vero se è entro 70 Hz
        near = [f for f, s in found if abs(f - self.dx_off) < 70]
        fdx = min(near, key=lambda f: abs(f - self.dx_off)) if near else self.dx_off
        if dxc is None:
            dxc = self._new_channel(fdx, t, True)
        else:
            dxc.f = 0.7 * dxc.f + 0.3 * fdx
            dxc.seen = t
        for f, s in found:
            if abs(f - dxc.f) < 45:
                continue
            best = None
            for c in chans:
                if not c.is_dx and abs(c.f - f) < 45 and (best is None or abs(c.f - f) < abs(best.f - f)):
                    best = c
            if best is not None:
                if abs(best.f - f) < 30:
                    best.f = 0.8 * best.f + 0.2 * f        # segue la deriva
                best.seen = t
            elif len(self.channels) < 160:
                chans.append(self._new_channel(f, t))
        # elimina canali spenti da 20 s o non più visti nello spettro da 30 s
        for k in [k for k, c in self.channels.items()
                  if not c.is_dx and not c.on and (t - c.last > 20 and t - c.seen > 6 or t - c.seen > 30)]:
            del self.channels[k]

    def _new_channel(self, f, t, is_dx=False):
        c = Channel(self.next_id, f, t, is_dx)
        c.k = int(round(f / self.binhz)) % self.nfft
        self.next_id += 1
        self.channels[c.id] = c
        self.new_channels.append(c)
        return c

    # ------------------------------------------------------------------ decodifica dei tempi
    def _rise(self, ch, t):
        gap = t - ch.t_off
        if ch.pend is not None and gap < ch.gmin():
            ch.t_on = ch.pend_start                      # buco nel segno (QSB, QRM): il segno continua
            ch.pend = None
            ch.on = True
            return
        if ch.pend is not None:
            self._commit(ch, ch.pend)
            ch.pend = None
        if ch.sym and gap < 2.0 * ch.unit():
            ch.ge = min(2.0 * ch.dot, max(0.5 * ch.dot, 0.85 * ch.ge + 0.15 * gap))
        self._gap(ch, gap)
        ch.on = True
        ch.t_on = t
        if ch.t0 is None:
            ch.t0 = t

    def _fall(self, ch, t):
        mk = t - ch.t_on
        ch.on = False
        first = not ch.sym and not ch.word and ch.pend is None
        if mk < (max(ch.mmin(), 0.6 * ch.dot) if first else ch.mmin()):   # impulso di rumore: lo ignoro
            if not ch.sym and not ch.word and ch.pend is None:
                ch.t0 = None
            return
        ch.pend = mk
        ch.pend_start = ch.t_on
        ch.t_off = t
        ch.last = t

    def _flush(self, ch, now):
        gap = now - ch.t_off
        if ch.pend is not None:
            if gap < ch.gmin():
                return
            self._commit(ch, ch.pend)
            ch.pend = None
        self._gap(ch, gap)

    def _commit(self, ch, mk):
        ch.mks.append(mk)
        if len(ch.mks) > 24:
            del ch.mks[0]
        if len(ch.mks) >= 6 and len(ch.mks) % 3 == 0:
            km = _kmeans2(ch.mks)                       # punti e linee dagli ultimi segni: corregge stime sbagliate
            if km:
                ch.dot = 0.5 * ch.dot + 0.5 * km[0]
                ch.dash = 0.5 * ch.dash + 0.5 * km[1]
        b = math.sqrt(ch.dot * ch.dash)
        if mk < b:
            ch.sym += '.'
            if TIMING_ROBUST == 0 or 0.5 * ch.dot < mk < 1.7 * ch.dot:        # solo punti plausibili: i disturbi non accorciano la velocità
                ch.dot = 0.8 * ch.dot + 0.2 * mk
        else:
            ch.sym += '-'
            if mk < 5.0 * ch.dot:
                ch.dash = 0.85 * ch.dash + 0.15 * mk
        ch.dot = min(0.15, max(0.02, ch.dot))
        ch.dash = min(4.5 * ch.dot, max(2.0 * ch.dot, ch.dash))
        if len(ch.sym) > 7:
            ch.sym = '-------'                           # diventa '·'

    def _gap(self, ch, gap):
        u = ch.unit()
        if ch.sym and gap > CHAR_G * u:
            ch.word += MORSE.get(ch.sym, '·')
            ch.sym = ''
        if ch.word and gap > WORD_G * u:
            self._emit_word(ch, ch.word)
            ch.word = ''
            ch.t0 = None

    def _emit_word(self, ch, w, split=True):
        t = ch.t0 if ch.t0 is not None else self.t
        if not CALL_RE.match(w) and w not in KEEP:
            # E / I / S / T in testa: quasi sempre un disturbo prima del primo segno
            for k in (1, 2):
                core = w[k:]
                if all(c in 'EIST' for c in w[:k]) and (len(core) >= 3 and CALL_RE.match(core)
                                                        or len(core) >= 2 and core in KEEP and core not in NOISE_WORDS):
                    w = core
                    break
        if split and len(w) >= 5 and not CALL_RE.match(w) and w not in KEEP:
            parts = segment(w)
            if parts:
                # parole attaccate (spazio troppo corto): CQCQDE, DL1ABCTEST, TU5NN203, IZ8XXEIZ8XXE
                for p in parts:
                    self._emit_word(ch, p, split=False)
                return
        if len(w) < 3 and w not in KEEP:
            return                                       # 1-2 lettere fuori dal vocabolario CW: quasi sempre rumore
        if len(w) == 1 and t - ch.last_good > 4.0:
            return                                       # R / K da soli: solo dopo una parola buona
        if w.count('·') > 1 or is_junk(w) or (ch.gh > 0.6 and not ch.is_dx):
            return
        if set(w) <= NOISE:
            # EE, ES, HI, ST...: valide solo dentro una trasmissione già riconosciuta
            if t - ch.last_good > 2.5:
                return
        else:
            ch.last_good = ch.last
        self.recent = [r for r in self.recent if t - r[0] < 1.5]
        for (tr, fr, wr) in self.recent:
            if wr == w and abs(fr - ch.f) < 150:
                return
        self.recent.append((t, ch.f, w))
        ev = {'t': round(t, 2), 'f': round(ch.f, 1), 'w': w, 'dx': ch.is_dx, 'ch': ch.id,
              'wpm': int(round(1.2 / ch.dot)), 'db': int(round(20 * math.log10(max(ch.s, 1e-12) / max(ch.n or 1e-12, 1e-12))))}
        cons = self._consensus(ch, w, t)
        if cons:
            ev['cc'] = cons[0]                          # nominativo confermato su questo canale
            if cons[1] == 's':
                ev['s'] = cons[0]
        self.out_words.append(ev)
        if ch.is_dx and self.over is not None:
            self.over[2].append(w)

    def _consensus(self, ch, w, t):
        """Chi chiama ripete il nominativo: due decodifiche uguali sullo stesso canale lo confermano.
        Una decodifica storpiata (una lettera diversa, l'inizio mancante) viene ricondotta a quello."""
        h = [x for x in ch.calls if t - x[0] < 90]
        if CALL_RE.match(w) or '·' in w:
            h.append((t, w))
        ch.calls = h[-12:]
        cnt = {}
        for _, x in ch.calls:
            if '·' not in x and CALL_RE.match(x):
                cnt[x] = cnt.get(x, 0) + 1
        best = max(cnt.items(), key=lambda kv: (kv[1], len(kv[0])))[0] if cnt else None
        if best is None or cnt[best] < 2:
            return None
        if w == best:
            return best, 'v'
        if len(w) >= 3 and _close(w, best):
            return best, 's'
        return None

    def _off(self, k):
        k = k if k < self.nfft // 2 else k - self.nfft
        return k * self.binhz


    # ------------------------------------------------------------------ DX / ascolto
    def _dx_track(self, col, tA, dt):
        on = np.nonzero(col)[0]
        tend = tA + len(col) * dt
        if len(on):
            t_first = tA + on[0] * dt
            t_last = tA + on[-1] * dt
            if self.over is None:
                self.over = [t_first, t_last, []]
            else:
                self.over[1] = t_last
        if self.over is not None and tend - self.over[1] > 0.8:
            st, en, words = self.over
            self.over = None
            if en - st > 1.2 and any(HOT.match(w) for w in words):
                # attività nel secondo e mezzo prima della fine del DX
                bef = np.zeros(self.nfft, np.float32); nb_ = 0
                for (tr, Sr) in self.ring:
                    tt = tr + np.arange(len(Sr)) / FRAMES_PER_S
                    sel = (tt >= en - 1.5) & (tt < en)
                    if sel.any():
                        bef += Sr[sel].sum(0); nb_ += int(sel.sum())
                before = bef / max(1, nb_)
                self.pending_listen.append([en, np.zeros(self.nfft, np.float32), 0, before, np.zeros(self.nfft, np.float32), None])

    def _listen_accumulate(self, strong, act, tA, dt, m):
        self.ring.append((tA, strong))
        while self.ring and self.ring[0][0] < tA - 2.5:
            self.ring.pop(0)
        if not self.pending_listen:
            return
        times = tA + np.arange(m) * dt
        keep = []
        for p in self.pending_listen:
            en = p[0]
            sel = (times >= en + 0.15) & (times < en + 2.5)
            if sel.any():
                S8 = strong[sel].astype(np.int8)
                p[1] += S8.sum(0)
                p[2] += int(sel.sum())
                prev = p[5] if p[5] is not None else S8[0]
                p[4] += (np.diff(np.vstack([prev[None, :], S8]), axis=0) > 0).sum(0)
                p[5] = S8[-1]
            if times[-1] >= en + 2.5:
                self._listen_finish(p)
            else:
                keep.append(p)
        self.pending_listen = keep

    def _listen_finish(self, p):
        en, acc, n, before, edges, _ = p
        if n == 0:
            return
        frac = acc / n
        # chi parte dopo il rapporto e taceva prima: è la stazione lavorata
        score = frac - np.maximum(before, self.duty) * 1.5
        keyed = np.convolve(edges, [1, 1, 1], mode='same') >= 4     # CW: tanti inizi di segno
        score[(frac > 0.85) | ~keyed] = -1
        score[self.duty > 0.22] = -1
        # solo sopra il DX: da +150 Hz a +30 kHz
        offs = np.array([self._off(k) for k in range(self.nfft)])
        rel = offs - self.dx_off
        score[(rel < self.up_lo) | (rel > self.up_hi)] = -1
        sm = np.convolve(score, [0.25, 0.5, 0.25], mode='same')
        k = int(np.argmax(sm))
        if sm[k] > 0.025:
            self.out_listen.append({'t': round(en + 0.4, 2), 'f': round(offs[k], 1)})

    # ------------------------------------------------------------------ uscita
    def take_smeter(self):
        """Livelli in dBFS (tono a fondo scala = 0) dall'ultima lettura: (DX, RX2 o None)."""
        db = lambda p: round(10 * math.log10(p + 1e-20), 1) if p > 0 else None
        if self.ssb and self.ssb_rx:
            a, b = self.ssb_rx[0].pk, self.ssb_rx[1].pk
            self.ssb_rx[0].pk = self.ssb_rx[1].pk = 0.0
            return db(a), db(b)
        a, self.sm_pk = self.sm_pk, 0.0
        return db(a), None

    def take(self):
        r = (self.out_rows, self.out_audio, self.out_words, self.out_listen)
        self.out_rows, self.out_audio, self.out_words, self.out_listen = [], [], [], []
        return r
