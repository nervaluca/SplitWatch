"""Split Watch - motore DSP in streaming.

Riceve blocchi IQ complessi (numpy complex64) e produce:
  - righe di waterfall (uint8, una ogni 10 ms)
  - audio CW del DX (int16, ~8 kHz)
  - parole CW decodificate per ogni segnale attivo
  - eventi "ascolto": dove ha risposto la stazione dopo il 5NN del DX
Solo numpy: deve girare anche su Python 32 bit.
"""
import math
import re
import numpy as np

FRAMES_PER_S = 400          # STFT del decoder: 2,5 ms
ROW_EVERY = 4               # 1 riga di waterfall ogni 4 frame = 100 righe/s
MAX_BINS = 4096             # righe di waterfall inviate al browser al massimo

HOT = re.compile(r'^(5NN|599|5NNTU|TU5NN|ENN|5N|NN|R5NN)$')
JUNK = set('EIST')


def _nextpow2(v):
    return 1 << int(math.ceil(math.log2(v)))


class Channel:
    """Decoder CW di un singolo canale (bin FFT)."""
    __slots__ = ('k', 'on', 'edge', 'u', 'sym', 'word', 't0', 'last', 'is_dx')

    def __init__(self, k, t, is_dx=False):
        self.k = k
        self.on = False
        self.edge = t
        self.u = 0.045          # durata del punto (s), si adatta
        self.sym = ''
        self.word = ''
        self.t0 = None
        self.last = t           # ultima attività
        self.is_dx = is_dx


MORSE = {'.-': 'A', '-...': 'B', '-.-.': 'C', '-..': 'D', '.': 'E', '..-.': 'F', '--.': 'G', '....': 'H',
         '..': 'I', '.---': 'J', '-.-': 'K', '.-..': 'L', '--': 'M', '-.': 'N', '---': 'O', '.--.': 'P',
         '--.-': 'Q', '.-.': 'R', '...': 'S', '-': 'T', '..-': 'U', '...-': 'V', '.--': 'W', '-..-': 'X',
         '-.--': 'Y', '--..': 'Z', '-----': '0', '.----': '1', '..---': '2', '...--': '3', '....-': '4',
         '.....': '5', '-....': '6', '--...': '7', '---..': '8', '----.': '9', '..--..': '?', '-..-.': '/',
         '-...-': '=', '.-.-.': '+', '.-.-': '·'}


class Engine:
    def __init__(self, fs, dx_off_hz=0.0, bfo=650.0):
        self.fs = float(fs)
        self.nfft = _nextpow2(self.fs / 47.0)             # ~47 Hz per bin
        self.nfft = max(1024, self.nfft)
        self.hop = int(round(self.fs / FRAMES_PER_S))
        self.binhz = self.fs / self.nfft
        self.win = np.hanning(self.nfft).astype(np.float32)
        self.buf = np.zeros(0, np.complex64)
        self.n_in = 0                                      # campioni consumati (tempo)
        self.frame_no = 0
        self.pool = max(1, self.nfft // MAX_BINS)          # riduzione righe per fs alte
        self.nbins_out = self.nfft // self.pool
        # stato per bin
        nb = self.nfft
        self.nf = None                                     # rumore per bin (ampiezza)
        self.pk = np.zeros(nb, np.float32)
        self.avg = np.zeros(nb, np.float32)                # media lenta (scelta canali)
        self.duty = np.zeros(nb, np.float32)               # frazione di tempo attivo (30 s)
        self.duty_f = np.zeros(nb, np.float32)             # idem, veloce (5 s)
        self.row_acc = np.zeros(nb, np.float32)
        self.row_n = 0
        self.row_floor = None
        self.channels = {}
        self.recent = []                                   # (t, k, testo) per deduplica
        self.out_words = []
        self.out_rows = []
        self.out_listen = []
        self.out_audio = []
        # DX e ascolto
        self.bfo = bfo
        self.up_lo, self.up_hi = 150.0, 10000.0             # zona UP dove cercare l'ascolto (Hz dal DX)
        self.set_dx(dx_off_hz)
        self.over = None                                   # [t_inizio, t_ultima_attività, parole]
        self.pending_listen = []                           # (t_fine_over, accumulatore attività)
        self.ring = []                                     # ultimi ~2 s di frame 'strong' (tempo, matrice)
        # audio
        self.adec = max(1, int(round(self.fs / 8000.0)))
        self.arate = self.fs / self.adec
        self.nco_ph = 0.0
        self.box_tail = np.zeros(0, np.complex64)
        taps = 129
        fc = 260.0 / self.arate
        n = np.arange(taps) - (taps - 1) / 2
        h = np.sinc(2 * fc * n) * np.hamming(taps)
        self.alp = (h / h.sum()).astype(np.float32)
        self.afir_state = np.zeros(taps - 1, np.complex64)
        self.bfo_ph = 0.0
        self.agc = 1e-6

    # ------------------------------------------------------------------ comandi
    def set_dx(self, off_hz):
        self.dx_off = float(off_hz)
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
        nfr = (len(self.buf) - self.nfft) // self.hop + 1
        if nfr < FRAMES_PER_S // 10:          # elabora a blocchi di almeno 0,1 s
            return
        # massimo 0,1 s di frame per volta, così le soglie si aggiornano spesso
        step = FRAMES_PER_S // 10
        done = 0
        while done < nfr:
            m = min(step, nfr - done)
            idx = (np.arange(self.nfft)[None, :] + self.hop * (done + np.arange(m))[:, None])
            X = np.fft.fft(self.buf[idx] * self.win, axis=1)
            self._frames(np.abs(X).astype(np.float32), m)
            done += m
        cut = nfr * self.hop
        self.buf = self.buf[cut:]
        self.n_in += cut

    # ------------------------------------------------------------------ audio DX
    def _audio(self, iq):
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
        f = np.convolve(zz, self.alp, mode='valid')                     # filtro CW ~±260 Hz
        self.afir_state = zz[-(len(self.alp) - 1):]
        env = np.abs(f)
        pk = env.max() if len(env) else 0
        self.agc = max(pk, self.agc * 0.97, 1e-7)
        tt = self.bfo_ph + 2 * np.pi * self.bfo / self.arate * np.arange(len(f))
        self.bfo_ph = float((tt[-1] + 2 * np.pi * self.bfo / self.arate) % (2 * np.pi))
        a = np.real(f * np.exp(1j * tt)) / (self.agc * 1.6)
        self.out_audio.append((np.clip(a, -1, 1) * 30000).astype('<i2'))

    # ------------------------------------------------------------------ frame STFT
    def _frames(self, A, m):
        fr_dt = 1.0 / FRAMES_PER_S
        # tempo del primo frame del blocco
        tA = self.frame_no * self.hop / self.fs + self.nfft / 2 / self.fs
        if self.nf is None:
            self.nf = np.percentile(A, 30, axis=0).astype(np.float32) + 1e-9
        # rumore per bin: inseguimento lento del 25° percentile
        p25 = np.percentile(A, 25, axis=0)
        self.nf = np.where(p25 < self.nf, self.nf * 0.8 + p25 * 0.2, self.nf * 0.985 + p25 * 0.015).astype(np.float32)
        # picco con decadimento (~0,4 s di emivita)
        cm = A.max(0)
        self.pk = np.maximum(self.pk * 0.85, cm)
        snr_ok = self.pk > 4.0 * self.nf
        th = self.nf + 0.5 * (self.pk - self.nf)
        act = (A > th[None, :]) & snr_ok[None, :]
        strong = A > 6.0 * self.nf[None, :]
        sm_ = strong.mean(0)
        self.duty = self.duty * 0.997 + sm_ * 0.003
        self.duty_f = self.duty_f * 0.98 + sm_ * 0.02
        self.avg = self.avg * 0.97 + (A.mean(0) / self.nf) * 0.03

        # waterfall
        P = A * A
        for i in range(m):
            self.row_acc += P[i]
            self.row_n += 1
            if self.row_n == ROW_EVERY:
                self._emit_row(tA + i * fr_dt)
                self.row_acc[:] = 0
                self.row_n = 0

        # canali attivi
        self._update_channels(tA + m * fr_dt)
        chs = list(self.channels.values())
        ks = np.array([c.k for c in chs], dtype=np.int64)
        prev = np.array([c.on for c in chs], dtype=np.int8)
        sub = act[:, ks].astype(np.int8)
        D = np.diff(np.vstack([prev[None, :], sub]), axis=0)
        fi, ci = np.nonzero(D.T)                 # ordinati per canale, poi per frame
        starts = np.searchsorted(fi, np.arange(len(chs) + 1))
        tend = tA + m * fr_dt
        for j, ch in enumerate(chs):
            a, b = starts[j], starts[j + 1]
            if b > a:
                self._decode_edges(ch, ci[a:b], D[ci[a:b], j], tA, fr_dt)
            if not ch.on and (ch.sym or ch.word):
                self._gap(ch, tend - ch.edge, ch.edge)

        # DX: over e ascolto
        self._dx_track(strong[:, self.dx_k] & act[:, self.dx_k], tA, fr_dt)
        self._listen_accumulate(strong, act, tA, fr_dt, m)
        self.frame_no += m

    def _emit_row(self, t):
        p = np.fft.fftshift(self.row_acc / ROW_EVERY)
        if self.pool > 1:
            p = p.reshape(-1, self.pool).max(1)
        db = 10 * np.log10(p + 1e-20)
        fl = float(np.median(db))
        self.row_floor = fl if self.row_floor is None else self.row_floor * 0.95 + fl * 0.05
        q = np.clip((db - self.row_floor - 1.0) / 30.0, 0, 1) ** 0.8
        self.out_rows.append((t, (q * 255).astype(np.uint8)))

    def _update_channels(self, t):
        a = self.duty_f + self.avg * 1e-3
        nb = self.nfft
        # massimi locali dell'attività recente (segnali che "battono" sopra 6x il rumore)
        loc = (a > np.roll(a, 1)) & (a >= np.roll(a, -1)) & (a > np.roll(a, 2)) & (a >= np.roll(a, -2))
        cand = np.where(loc & (self.duty_f > 0.04))[0]
        if len(cand) > 120:
            cand = cand[np.argsort(a[cand])[::-1][:120]]
        for k in cand:
            k = int(k)
            if k in self.channels or (k - 1) % nb in self.channels or (k + 1) % nb in self.channels:
                continue
            if True:
                self.channels[k] = Channel(k, t)
        if self.dx_k not in self.channels:
            self.channels[self.dx_k] = Channel(self.dx_k, t, is_dx=True)
        else:
            self.channels[self.dx_k].is_dx = True
        # elimina canali inattivi da 20 s
        for k in [k for k, c in self.channels.items() if not c.is_dx and t - c.last > 20 and not c.on]:
            del self.channels[k]

    def _decode_edges(self, ch, idx, dirs, tA, dt):
        for i, dv in zip(idx.tolist(), dirs.tolist()):
            t = tA + i * dt
            if dv > 0:                         # inizio segno
                gap = t - ch.edge
                self._gap(ch, gap, ch.edge)
                ch.on = True
                ch.edge = t
                if ch.t0 is None:
                    ch.t0 = t
            else:                                # fine segno
                mk = t - ch.edge
                ch.on = False
                ch.edge = t
                if mk < 0.012:
                    continue
                u = ch.u
                if mk < 2 * u:
                    ch.sym += '.'
                    ch.u = 0.8 * u + 0.2 * mk
                else:
                    ch.sym += '-'
                    ch.u = 0.85 * u + 0.15 * (mk / 3)
                ch.u = min(0.12, max(0.025, ch.u))
                ch.last = t
                if len(ch.sym) > 7:
                    ch.sym = ''

    def _gap(self, ch, gap, tedge, final=False):
        u = ch.u
        if ch.sym and gap > 2.0 * u:
            ch.word += MORSE.get(ch.sym, '·')
            ch.sym = ''
        if ch.word and gap > 5.0 * u:
            self._emit_word(ch, ch.word)
            ch.word = ''
            ch.t0 = None

    def _emit_word(self, ch, w):
        t = ch.t0 if ch.t0 is not None else self.t
        if len(w) < 2 and w not in ('R', 'K'):
            return
        if set(w) <= JUNK or w.count('·') > 1:
            return
        if not ch.is_dx and set(w) <= set('EISH5·') and len(w) < 7:
            return
        nb = self.nfft
        self.recent = [r for r in self.recent if t - r[0] < 1.5]
        for (tr, kr, wr) in self.recent:
            if wr == w and min((kr - ch.k) % nb, (ch.k - kr) % nb) <= 3:
                return
        self.recent.append((t, ch.k, w))
        off = self._off(ch.k)
        self.out_words.append({'t': round(t, 2), 'f': round(off, 1), 'w': w, 'dx': ch.is_dx})
        if ch.is_dx and self.over is not None:
            self.over[2].append(w)

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
    def take(self):
        r = (self.out_rows, self.out_audio, self.out_words, self.out_listen)
        self.out_rows, self.out_audio, self.out_words, self.out_listen = [], [], [], []
        return r
