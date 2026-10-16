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
    print('OK' if ok else 'FALLITO')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
