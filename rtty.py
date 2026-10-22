"""FZRTTY: skimmer RTTY multicanale per FZR Skimmer.

RTTY dei radioamatori: 45,45 baud, shift 170 Hz, Baudot (ITA2), 1 bit di start, 5 di dati, 1,5 di stop.
Il mark è la frequenza più alta (come gli Icom: la frequenza del segnale è il mark).

Lavora sullo stesso spettro (STFT, un frame ogni 5 ms, finestra di ~21 ms, cioè circa un bit) che usa
il decoder CW: per ogni segnale RTTY trovato prende l'ampiezza sul mark e sullo space, ne fa la
differenza normalizzata (+1 = mark, -1 = space) e la legge come una telescrivente: aspetta il fronte
dello start, campiona a metà di ogni bit, controlla lo stop.

Trova i segnali da solo: coppie di righe a 170 Hz l'una dall'altra che si alternano nel tempo
(una portante doppia o due segnali CW non si alternano e vengono scartati).
"""
import math
from collections import deque

import numpy as np

BAUD = 45.45
T = 1.0 / BAUD
SHIFT = 170.0

LTRS, FIGS = 0x1F, 0x1B
_L = {0x00: '', 0x01: 'E', 0x02: '\n', 0x03: 'A', 0x04: ' ', 0x05: 'S', 0x06: 'I', 0x07: 'U', 0x08: '\r',
      0x09: 'D', 0x0A: 'R', 0x0B: 'J', 0x0C: 'N', 0x0D: 'F', 0x0E: 'C', 0x0F: 'K', 0x10: 'T', 0x11: 'Z',
      0x12: 'L', 0x13: 'W', 0x14: 'H', 0x15: 'Y', 0x16: 'P', 0x17: 'Q', 0x18: 'O', 0x19: 'B', 0x1A: 'G',
      0x1C: 'M', 0x1D: 'X', 0x1E: 'V'}
# cifre: tabella US-TTY, quella dei programmi per radioamatori (MMTTY, N1MM)
_F = {0x00: '', 0x01: '3', 0x02: '\n', 0x03: '-', 0x04: ' ', 0x05: "'", 0x06: '8', 0x07: '7', 0x08: '\r',
      0x09: '$', 0x0A: '4', 0x0B: "'", 0x0C: ',', 0x0D: '!', 0x0E: ':', 0x0F: '(', 0x10: '5', 0x11: '"',
      0x12: ')', 0x13: '2', 0x14: '#', 0x15: '6', 0x16: '0', 0x17: '1', 0x18: '9', 0x19: '?', 0x1A: '&',
      0x1C: '.', 0x1D: '/', 0x1E: ';'}
_INV = {v: k for k, v in _L.items() if len(v) == 1 and v.isalpha()}
_INVF = {v: k for k, v in _F.items() if len(v) == 1 and v not in ' \r\n'}


def encode(text):
    """Testo -> codici Baudot (con LTRS/FIGS). Serve alle prove."""
    out, shift = [LTRS], 'L'
    for ch in text.upper():
        if ch == ' ':
            out.append(0x04)
            shift = 'L'                       # USOS: dopo lo spazio si torna alle lettere
            continue
        if ch in _INV:
            if shift != 'L':
                out.append(LTRS)
                shift = 'L'
            out.append(_INV[ch])
        elif ch in _INVF:
            if shift != 'F':
                out.append(FIGS)
                shift = 'F'
            out.append(_INVF[ch])
    return out


class Chan:
    __slots__ = ('id', 'f', 'dx', 'buf', 't_search', 'shift', 'word', 'word_t', 'last_char', 'seen', 'lvl',
                 'good', 'bad', 'snr', 'fresh', 'ok', 'pend')

    def __init__(self, cid, f, t, dx=False):
        self.id, self.f, self.dx = cid, float(f), dx
        self.buf = deque()                # (t, d, livello)
        self.t_search = t
        self.shift = 'L'
        self.word = ''
        self.word_t = None
        self.last_char = t
        self.seen = t
        self.lvl = 0.0
        self.good = 0
        self.bad = 0
        self.snr = 0.0
        self.fresh = False
        self.ok = dx                      # confermato come RTTY vero: prima le parole restano in attesa
        self.pend = []


class RttySkimmer:
    def __init__(self, fs, nfft):
        self.fs, self.nfft = float(fs), int(nfft)
        self.binhz = self.fs / self.nfft
        self.avg = None
        self.ring = deque(maxlen=600)     # ~3 s di frame (ampiezze): controllo e ridecodifica dei canali nuovi
        self.chans = {}
        self.next_id = 10000              # numeri diversi dai canali CW
        self.t_detect = 0.0
        self.dx_off = None
        self.min_snr = 2.0                # mark o space almeno 2x il rumore (6 dB)

    # ------------------------------------------------------------ utilità
    def _col(self, A, f):
        """Ampiezza alla frequenza f (Hz dal centro): la più alta dei due bin vicini (perdita < 1,5 dB)."""
        kf = (f / self.binhz) % self.nfft
        k0 = int(math.floor(kf)) % self.nfft
        k1 = (k0 + 1) % self.nfft
        return np.maximum(A[:, k0], A[:, k1])

    def _nf(self, nf, f):
        kf = (f / self.binhz) % self.nfft
        k0 = int(math.floor(kf)) % self.nfft
        return float(min(nf[k0], nf[(k0 + 1) % self.nfft]))

    def _soft(self, c, em, es):
        """Valore morbido: (mark - space) / ampiezza del segnale del canale (+1 mark, -1 space).
        Con i segnali deboli non si normalizza frame per frame (il rumore lo sporcherebbe): si usa
        l'ampiezza media del canale."""
        mx = np.maximum(em, es)
        out = np.empty(len(em))
        lv = c.lvl if c.lvl > 0 else float(np.median(mx))
        a = 0.995
        for i, v in enumerate(mx.tolist()):
            lv = lv * a + v * (1 - a)
            out[i] = lv
        c.lvl = lv
        return (em - es) / (out + 1e-20)

    def set_dx(self, off):
        """Canale fisso sulla frequenza del DX (mark), anche se debole."""
        self.dx_off = float(off)
        for c in self.chans.values():
            c.dx = False
        hit = [c for c in self.chans.values() if abs(c.f - off) < 30]
        if hit:
            hit[0].dx = True
            hit[0].f = float(off)
        else:
            cid = self.next_id
            self.next_id += 1
            self.chans[cid] = Chan(cid, off, 0.0, dx=True)

    # ------------------------------------------------------------ ricerca dei segnali
    def _detect(self, nf, t):
        a = self.avg
        r = a / (nf + 1e-12)
        L = self.nfft
        loc = (r > 2.0) & (a >= np.roll(a, 1)) & (a > np.roll(a, -1))
        peaks = []
        for k in np.nonzero(loc)[0]:
            y0, y1, y2 = math.log(a[(k - 1) % L] + 1e-12), math.log(a[k] + 1e-12), math.log(a[(k + 1) % L] + 1e-12)
            den = y0 - 2 * y1 + y2
            d = 0.5 * (y0 - y2) / den if den < 0 else 0.0
            f = ((k + max(-0.5, min(0.5, d))) if k < L // 2 else (k - L + max(-0.5, min(0.5, d)))) * self.binhz
            peaks.append((f, k))
        if not peaks or len(self.ring) < 60:
            return
        peaks.sort()
        R = np.array(self.ring)                       # frame x bin
        fs_ = [p[0] for p in peaks]
        for i, (fm, km) in enumerate(peaks):
            # lo space 170 Hz sotto il mark
            j = min(range(len(peaks)), key=lambda q: abs(fs_[q] - (fm - SHIFT)))
            if abs(fs_[j] - (fm - SHIFT)) > 25:
                continue
            if any(abs(c.f - fm) < 40 for c in self.chans.values()):
                continue
            Rr = R[-200:]
            m_ = self._col(Rr, fm)
            s_ = self._col(Rr, fm - SHIFT)
            if m_.std() < 1e-12 or s_.std() < 1e-12:
                continue
            corr = float(np.corrcoef(m_, s_)[0, 1])
            if corr > -0.2:                           # mark e space devono alternarsi
                continue
            # in un segnale RTTY c'è SEMPRE uno dei due toni: quasi tutti i frame sopra il rumore
            n0 = max(self._nf(nf, fm), self._nf(nf, fm - SHIFT), 1e-12)
            pres = float(np.mean(np.maximum(m_, s_) > 2.5 * n0))
            if pres < 0.75:
                continue
            cid = self.next_id
            self.next_id += 1
            c = Chan(cid, fm, t)
            self.chans[cid] = c
            self._replay(c, R, t)
            if len(self.chans) > 80:
                break

    def _replay(self, c, R, t):
        """Canale appena trovato: ridecodifico gli ultimi ~3 s, così non perdo l'inizio (il nominativo)."""
        n = len(R)
        em = self._col(R, c.f)
        es = self._col(R, c.f - SHIFT)
        c.lvl = float(np.percentile(np.maximum(em, es), 60))
        d = self._soft(c, em, es)
        n0 = max(self._nf(self.nf_last, c.f), self._nf(self.nf_last, c.f - SHIFT), 1e-12)
        lv = np.maximum(em, es) / n0
        t0 = t - n * self.dt
        for i in range(n):
            c.buf.append((t0 + i * self.dt, float(d[i]), float(lv[i])))
        c.t_search = t0
        c.snr = float(np.median(lv))
        c.seen = t
        c.fresh = True

    # ------------------------------------------------------------ ingresso
    def process(self, A, nf, tA, dt):
        """A: frame x bin (ampiezze dello STFT), nf: rumore per bin, tA: tempo del primo frame (centro).
        Restituisce le parole decodificate (dizionari come quelli del CW)."""
        m = A.shape[0]
        self.dt, self.nf_last = dt, nf
        mean = A.mean(0)
        if self.avg is None:
            self.avg = mean.astype(np.float32)
        else:
            k = math.exp(-m * dt / 1.5)
            self.avg = (self.avg * k + mean * (1 - k)).astype(np.float32)
        tend = tA + m * dt
        for row in A:
            self.ring.append(row)
        if tend - self.t_detect > 0.5:
            self.t_detect = tend
            self._detect(nf, tend)
        out = []
        ts = tA + np.arange(m) * dt
        for c in list(self.chans.values()):
            out += self._chan(c, A, nf, ts, tend)
        return out

    def _gate(self, c, words):
        """Le parole di un canale nuovo escono solo quando è chiaro che è RTTY (caratteri buoni): il rumore
        che per caso sembra una coppia mark/space produce quasi solo caratteri rotti e viene scartato."""
        if c.ok:
            return words
        c.pend += words
        if c.good >= 6 and c.good >= 2 * c.bad:
            c.ok = True
            words, c.pend = c.pend, []
            return words
        return []

    def _chan(self, c, A, nf, ts, tend):
        out = []
        if True:
            if getattr(c, 'fresh', False):
                c.fresh = False                               # il blocco attuale è già nel ring ridecodificato
                return self._gate(c, self._run(c, tend))
            em = self._col(A, c.f)
            es = self._col(A, c.f - SHIFT)
            d = self._soft(c, em, es)
            n0 = max(self._nf(nf, c.f), self._nf(nf, c.f - SHIFT), 1e-12)
            lv = np.maximum(em, es) / n0
            c.snr = 0.9 * c.snr + 0.1 * float(np.median(lv))
            for t, dv, l in zip(ts.tolist(), d.tolist(), lv.tolist()):
                c.buf.append((t, dv, l))
            if c.snr > self.min_snr:
                c.seen = tend
            out += self._run(c, tend)
            if c.word and tend - c.last_char > 1.5:
                out += self._flush(c)
            if not c.dx and (tend - c.seen > 15 or (c.good + c.bad >= 12 and c.bad > c.good)):
                del self.chans[c.id]                          # sparito, o non è RTTY (troppi caratteri rotti)
                return []
        return self._gate(c, out)

    # ------------------------------------------------------------ telescrivente
    def _at(self, buf, t):
        """Valore di d al tempo t (interpolazione lineare)."""
        n = len(buf)
        lo, hi = 0, n - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if buf[mid][0] <= t:
                lo = mid
            else:
                hi = mid
        t0, d0, l0 = buf[lo]
        t1, d1, l1 = buf[hi]
        if t1 <= t0:
            return d0, l0
        a = (t - t0) / (t1 - t0)
        return d0 + (d1 - d0) * a, l0 + (l1 - l0) * a

    def _run(self, c, tend):
        out = []
        buf = c.buf
        while len(buf) > 2:
            # cerco il fronte mark -> space dopo t_search
            idx = None
            prev = None
            for i, (t, dv, l) in enumerate(buf):
                if t < c.t_search:
                    prev = dv
                    continue
                if prev is not None and prev >= 0 > dv and l > self.min_snr:
                    idx = i
                    break
                prev = dv
            if idx is None:
                while len(buf) > 3 and buf[1][0] < max(c.t_search, buf[-1][0] - 0.05):
                    buf.popleft()
                break
            t1, d1, _ = buf[idx]
            t0, d0, _ = buf[idx - 1]
            te = t0 + (t1 - t0) * (d0 / (d0 - d1)) if d0 != d1 else t1
            if buf[-1][0] < te + 6.6 * T:
                # non ho ancora tutto il carattere: aspetto altri dati
                while len(buf) > 3 and buf[1][0] < te - 0.02:
                    buf.popleft()
                break
            # ogni bit: media di tre punti nella sua metà centrale (meno rumore)
            vals = [(sum(self._at(buf, te + (k + 0.5 + o) * T)[0] for o in (-0.22, 0.0, 0.22)) / 3, 0)
                    for k in range(7)]
            start, stop = vals[0][0], vals[6][0]
            if start > -0.15:
                c.t_search = te + 0.5 * T                       # falso start
                continue
            bits = [1 if v > 0 else 0 for v, _ in vals[1:6]]
            conf = sum(abs(v) for v, _ in vals) / 7
            if stop <= 0.05 or conf < 0.3:
                c.bad += 1
                c.t_search = te + 1.0 * T                       # carattere rotto: mi risincronizzo
                continue
            c.good += 1
            c.t_search = te + 7.0 * T
            code = sum(b << i for i, b in enumerate(bits))
            out += self._char(c, code, te)
        return out

    def _char(self, c, code, t):
        c.last_char = t
        if code == LTRS:
            c.shift = 'L'
            return []
        if code == FIGS:
            c.shift = 'F'
            return []
        ch = (_F if c.shift == 'F' else _L).get(code, '')
        if ch in (' ', '\r', '\n'):
            if ch == ' ':
                c.shift = 'L'                                   # USOS
            return self._flush(c)
        if ch:
            if not c.word:
                c.word_t = t
            c.word += ch
            if len(c.word) >= 14:
                return self._flush(c)
        return []

    def _flush(self, c):
        w, c.word = c.word, ''
        if not w:
            return []
        if w in ('TOO', 'TOOTOO'):                              # 599 perso il cambio cifre
            w = '599'
        return [{'w': w, 'f': round(c.f, 1), 't': float(c.word_t or c.last_char), 'ch': c.id, 'dx': c.dx,
                 'db': round(20 * math.log10(max(c.snr, 1e-3)), 0), 'm': 'rtty'}]
