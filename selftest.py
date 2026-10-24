"""Prova automatica del motore: CW sintetico con DX e pile-up.
Deve decodificare il DX e trovare la frequenza di ascolto."""
import struct
import sys

import numpy as np

from dsp import Engine

MORSE = {'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.', 'F': '..-.', 'G': '--.', 'H': '....', 'I': '..',
         'J': '.---', 'K': '-.-', 'L': '.-..', 'M': '--', 'N': '-.', 'O': '---', 'P': '.--.', 'Q': '--.-', 'R': '.-.',
         'S': '...', 'T': '-', 'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-', 'Y': '-.--', 'Z': '--..',
         '0': '-----', '1': '.----', '2': '..---', '3': '...--', '4': '....-', '5': '.....', '6': '-....',
         '7': '--...', '8': '---..', '9': '----.'}
FS = 96000
DUR = 14.0
N = int(FS * DUR)


def key(text, wpm, t0):
    u = 1.2 / wpm
    env = np.zeros(N, np.float32)
    t = t0
    for wi, w in enumerate(text.split()):
        if wi:
            t += 4 * u
        for c in w:
            for e in MORSE[c]:
                d = u if e == '.' else 3 * u
                env[int(t * FS):int((t + d) * FS)] = 1
                t += d + u
            t += 2 * u
    return env, t


def main():
    rng = np.random.default_rng(1)
    x = (rng.normal(size=N) + 1j * rng.normal(size=N)).astype(np.complex64) * 0.01
    tt = np.arange(N) / FS

    def add(off, env, amp):
        nonlocal x
        x = x + (amp * env * np.exp(2j * np.pi * off * tt)).astype(np.complex64)

    env, t1 = key('DL1ABC 5NN', 28, 1.0)       # il DX passa il rapporto
    add(0, env, 0.2)
    env2, _ = key('R 5NN TU', 26, t1 + 0.35)    # risponde la stazione lavorata a +2,3 kHz
    add(2300, env2, 0.12)
    env3, _ = key('K1ZZ K1ZZ', 24, 0.5)         # un chiamante fuori tempo a +4 kHz
    add(4000, env3, 0.12)

    e = Engine(FS, 0.0)
    words, listen = [], []
    for i in range(0, N, 1024):
        e.feed(x[i:i + 1024])
        r, a, w, l = e.take()
        words += w
        listen += l
    txt = ' '.join(w['w'] for w in words)
    print('python', sys.version.split()[0], struct.calcsize('P') * 8, 'bit, numpy', np.__version__)
    print('parole:', txt)
    print('ascolti:', listen)
    ok = '5NN' in txt and 'DL1ABC' in txt and any(abs(l['f'] - 2300) < 120 for l in listen)
    ok = ok and test_wav(x[:FS])
    ok = ok and test_rtty()
    ok = ok and test_fm()
    ok = ok and test_banco()
    print('OK' if ok else 'FALLITO')
    return 0 if ok else 1


def test_wav(x):
    """Memoria degli ultimi minuti -> WAV -> rilettura: centro, DX e campioni devono tornare."""
    import tempfile
    import time
    from recorder import Recorder
    from sources import WavFile, to_complex
    d = tempfile.mkdtemp()
    r = Recorder(d, minutes=1)
    r.set_format(FS, 18085000)
    r.set_dx(18080000)
    t = time.time()
    for i in range(0, len(x), 4096):
        t += 4096 / FS
        r.push(x[i:i + 4096], t)
    got = []
    r.save_last(got.append)
    while not got:
        time.sleep(0.05)
    w = WavFile(got[0][0], 0, None, None)
    with open(got[0][0], 'rb') as f:
        f.seek(w.data_off)
        y = to_complex(f.read(w.data_len), w.fmt)
    err = float(np.abs(y - x[:len(y)]).max())
    print('wav:', len(y), 'campioni, centro', w.center, 'dx', w.dx, 'errore', err)
    return len(y) == len(x) and w.center == 18085000 and w.dx == 18080000 and err < 1e-3


def test_rtty():
    """FZRTTY: due segnali RTTY (45,45 baud, shift 170) col rumore, letti dallo skimmer."""
    import rtty
    fs = 48000
    rng = np.random.default_rng(2)

    def fsk(text, fm, amp, dur, t0):
        n = int(dur * fs)
        lvl = np.ones(n)
        t = t0
        for c in rtty.encode(text):
            for b in [0] + [(c >> i) & 1 for i in range(5)]:
                lvl[int(t * fs):int((t + rtty.T) * fs)] = b
                t += rtty.T
            t += 1.5 * rtty.T
        f = np.where(lvl > 0.5, fm, fm - rtty.SHIFT)
        return amp * np.exp(2j * np.pi * np.cumsum(f) / fs)

    dur = 9.0
    x = fsk('CQ TEST DL1ABC DL1ABC 599 14', 3000, 0.05, dur, 0.3) + fsk('CQ TEST K3LR K3LR', -6000, 0.03, dur, 0.5)
    x = (x + 0.01 * (rng.normal(size=len(x)) + 1j * rng.normal(size=len(x)))).astype(np.complex64)
    e = Engine(fs, 3000.0)
    e.set_rx(True, 'rtty', -1, 8000.0, 500)
    words = []
    for i in range(0, len(x), 2400):
        e.feed(x[i:i + 2400])
        words += e.take()[2]
    txt = ' '.join(w['w'] for w in words)
    print('rtty:', txt)
    return 'DL1ABC' in txt and '599' in txt and 'K3LR' in txt



def test_fm():
    """FM: tono 1 kHz con 3 kHz di deviazione, squelch chiuso senza portante e aperto con."""
    import dsp
    fs, adec = 192000.0, 24
    t = np.arange(int(fs * 2)) / fs
    rng = np.random.default_rng(3)
    ph = 2 * np.pi * np.cumsum(3000 * np.sin(2 * np.pi * 1000 * t)) / fs
    x = ((t > 1.0) * np.exp(1j * (2 * np.pi * 20000 * t + ph)) * 0.05
         + 0.01 * (rng.normal(size=len(t)) + 1j * rng.normal(size=len(t)))).astype(np.complex64)
    r = dsp.NarrowRx(fs, adec, 6000, 'fm')
    r.sql = 10
    a = np.concatenate([r.process(x[i:i + 9600], 20000.0) for i in range(0, len(x), 9600)])
    R = fs / adec
    quiet, seg = a[int(0.3 * R):int(0.9 * R)], a[int(1.3 * R):int(1.9 * R)]
    Z = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    pk = np.fft.rfftfreq(len(seg), 1 / R)[np.argmax(Z)]
    ok = abs(pk - 1000) < 20 and np.sqrt(np.mean(quiet ** 2)) < 0.01 < np.sqrt(np.mean(seg ** 2))
    print(f'fm: tono {pk:.0f} Hz, squelch {"ok" if ok else "NO"}')
    return ok


def test_banco():
    import banco
    cfg = {}
    n = len(banco.lista(cfg))
    m = banco.aggiungi(cfg, 'Prova', 145600000, 'fm', 'Mie')
    banco.modifica(cfg, m['id'], {'nome': 'R0', 'hz': 145600000})
    banco.cancella(cfg, m['id'])
    ok = n > 100 and len(banco.lista(cfg)) == n and banco.preset(cfg) == 0
    print(f'banco: {n} memorie predefinite', 'ok' if ok else 'NO')
    return ok


if __name__ == '__main__':
    sys.exit(main())
