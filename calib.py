"""Calibrazione della frequenza con le stazioni campione (RWM, WWV, BPM, CHU…).

L'errore di un ricevitore SDR viene quasi tutto dal suo quarzo: è proporzionale alla frequenza,
quindi si misura in ppm (parti per milione) e vale per tutte le bande. Si sintonizza una portante
campione, se ne misura la frequenza al centesimo di Hz e si ricava la correzione.
"""
import numpy as np

# (kHz, stazioni) — dall'Italia di solito RWM e BPM/WWV a 10 o 15 MHz si sentono bene
STATIONS = [
    (4996, 'RWM'), (9996, 'RWM'), (14996, 'RWM'),
    (2500, 'WWV · BPM'), (5000, 'WWV · BPM'), (10000, 'WWV · BPM'), (15000, 'WWV · BPM'), (20000, 'WWV'),
    (3330, 'CHU'), (7850, 'CHU'), (14670, 'CHU'),
]
OFFSET = 12000          # la portante si mette 12 kHz sopra il centro (lontano da DC e dai suoi disturbi)
SECS = 6.0              # durata della misura
SEG = 3                 # pezzi indipendenti: dicono se la misura è stabile
MIN_SNR = 15.0          # dB sopra il rumore


class Measure:
    """Raccoglie IQ per SECS secondi e trova la portante vicino a dove dovrebbe essere."""

    def __init__(self, fs, hw_center, true_hz, ppm_now=0.0, secs=SECS, span_ppm=10.0, span_hz=None, min_steady=0.85):
        self.fs, self.hw_center, self.true_hz = float(fs), float(hw_center), float(true_hz)
        self.ppm_now = float(ppm_now)
        self.n = int(secs * self.fs)
        self.buf = []
        self.got = 0
        # dove deve cadere la portante, rispetto al centro del ricevitore, con la correzione attuale
        self.expect = self.true_hz / (1 + self.ppm_now * 1e-6) - self.hw_center
        # cerca entro ±10 ppm (100 Hz a 10 MHz); le chiavette RTL possono sbagliare di 100 ppm e più
        self.span_ppm = float(span_ppm)
        self.win = span_hz or max(60.0, self.span_ppm * 1e-6 * self.true_hz)
        self.min_steady = float(min_steady)

    @property
    def done(self):
        return self.got >= self.n

    @property
    def progress(self):
        return min(1.0, self.got / self.n)

    def feed(self, iq):
        if self.done:
            return
        self.buf.append(np.asarray(iq, np.complex64).copy())
        self.got += len(iq)

    def result(self):
        x = np.concatenate(self.buf)[:self.n]
        if abs(self.expect) + self.win > self.fs / 2 * 0.95:
            return {'ok': False, 'why': 'fuori banda'}
        L = len(x) // SEG
        freqs, snrs = [], []
        t = np.arange(L) / self.fs
        for i in range(SEG):
            seg = x[i * L:(i + 1) * L] * np.exp(-2j * np.pi * self.expect * t).astype(np.complex64)
            # porto la zona d'interesse a 0 Hz e decimo: FFT piccola e fine
            dec = max(1, int(self.fs // (4 * self.win)))
            if dec > 1:
                m = len(seg) // dec
                seg = seg[:m * dec].reshape(m, dec).mean(1)
            fs2 = self.fs / dec
            nfft = 1 << int(np.ceil(np.log2(len(seg) * 4)))
            sp = np.abs(np.fft.fft(seg * np.hanning(len(seg)), nfft)) ** 2
            f = np.fft.fftfreq(nfft, 1 / fs2)
            sel = (np.abs(f) <= self.win) & (np.abs(f + self.expect) > 1500)    # niente picco DC al centro
            idx = np.nonzero(sel)[0]
            k = idx[np.argmax(sp[idx])]
            a, b, c = np.log(sp[(k - 1) % nfft] + 1e-30), np.log(sp[k] + 1e-30), np.log(sp[(k + 1) % nfft] + 1e-30)
            den = a - 2 * b + c
            d = 0.5 * (a - c) / den if den != 0 else 0.0
            freqs.append(f[k] + d * fs2 / nfft)
            snrs.append(10 * np.log10(sp[k] / (np.median(sp[idx]) + 1e-30)))
        # la portante campione è continua: un segnale CW manipolato (o RWM nei minuti a impulsi) no
        rel0 = self.expect + float(np.median(freqs))
        q = int(self.fs * 0.02)                     # pezzi da 20 ms: più corti di un punto CW
        nq = len(x) // q
        tt = np.arange(q) / self.fs
        lo = np.exp(2j * np.pi * rel0 * tt).astype(np.complex64)      # vdot coniuga lo: riporta la portante a 0 Hz
        amp = np.array([abs(np.vdot(lo, x[i * q:(i + 1) * q])) for i in range(nq)])
        steady = float(np.mean(amp > 0.15 * np.percentile(amp, 90))) if nq else 0.0   # il QSB lento va bene
        freqs = np.array(freqs)
        snr = float(np.median(snrs))
        rel = self.expect + float(np.median(freqs))
        spread = float(freqs.max() - freqs.min())
        ppm = (self.true_hz / (self.hw_center + rel) - 1) * 1e6
        err_hz = (self.hw_center + rel) * (1 + self.ppm_now * 1e-6) - self.true_hz   # di quanto sbaglia ora
        tol = 0.5e-6 if self.span_ppm <= 10 else 1.5e-6        # in VHF la portante (ponte, beacon) è meno ferma
        ok = snr >= MIN_SNR and spread < max(1.0, tol * self.true_hz) and steady >= self.min_steady
        why = ('' if ok else 'segnale troppo debole' if snr < MIN_SNR else
               'portante non continua' if steady < self.min_steady else 'misura instabile')
        return {'ok': ok, 'why': why, 'ppm': round(ppm, 3), 'err_hz': round(err_hz, 2), 'snr': round(snr, 1),
                'spread': round(spread, 2), 'steady': round(steady, 2), 'khz': self.true_hz / 1000, 'old': round(self.ppm_now, 3),
                'hz14': round(ppm * 14.0, 1)}
