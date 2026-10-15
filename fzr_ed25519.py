# -*- coding: utf-8 -*-
"""
ed25519_puro.py — Ed25519 in Python puro (nessuna dipendenza esterna).

Serve alla registrazione software di ADIF FZR: l'app verifica le firme con la
CHIAVE PUBBLICA qui inclusa; solo il generatore (keygen, in mano a Luca) possiede
la chiave privata e puo' creare firme valide.

Basato sull'implementazione di riferimento di D. J. Bernstein (dominio pubblico),
con pow()/inv ottimizzati in modo che una verifica sia veloce (<50 ms).
API principali:
    publickey_da_seed(seed32) -> bytes(32)
    firma(msg, seed32)        -> bytes(64)
    verifica(firma64, msg, pub32) -> bool
"""

import hashlib

_b = 256
_q = 2 ** 255 - 19
_l = 2 ** 252 + 27742317777372353535851937790883648493


def _H(m):
    return hashlib.sha512(m).digest()


def _inv(x):
    return pow(x, _q - 2, _q)


_d = (-121665 * _inv(121666)) % _q
_I = pow(2, (_q - 1) // 4, _q)


def _xrecover(y):
    xx = (y * y - 1) * _inv(_d * y * y + 1)
    x = pow(xx, (_q + 3) // 8, _q)
    if (x * x - xx) % _q != 0:
        x = (x * _I) % _q
    if x % 2 != 0:
        x = _q - x
    return x


_By = (4 * _inv(5)) % _q
_Bx = _xrecover(_By)
_B = (_Bx % _q, _By % _q)


def _edwards_add(P, Q):
    x1, y1 = P
    x2, y2 = Q
    dd = _d * x1 * x2 * y1 * y2
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + dd)
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - dd)
    return (x3 % _q, y3 % _q)


def _scalarmult(P, e):
    """Double-and-add iterativo (niente ricorsione profonda)."""
    Q = (0, 1)  # elemento neutro
    while e > 0:
        if e & 1:
            Q = _edwards_add(Q, P)
        P = _edwards_add(P, P)
        e >>= 1
    return Q


def _bit(h, i):
    return (h[i // 8] >> (i % 8)) & 1


def _encodeint(y):
    return y.to_bytes(_b // 8, "little")


def _encodepoint(P):
    x, y = P
    bits = [(y >> i) & 1 for i in range(_b - 1)] + [x & 1]
    return bytes(sum(bits[i * 8 + j] << j for j in range(8)) for i in range(_b // 8))


def _Hint(m):
    h = _H(m)
    return sum(2 ** i * _bit(h, i) for i in range(2 * _b))


def _clamp(h):
    a = 2 ** (_b - 2) + sum(2 ** i * _bit(h, i) for i in range(3, _b - 2))
    return a


def publickey_da_seed(seed):
    h = _H(seed)
    a = _clamp(h)
    A = _scalarmult(_B, a)
    return _encodepoint(A)


def firma(m, seed, pub=None):
    if pub is None:
        pub = publickey_da_seed(seed)
    h = _H(seed)
    a = _clamp(h)
    r = _Hint(bytes(h[_b // 8:_b // 4]) + m)
    R = _scalarmult(_B, r)
    encR = _encodepoint(R)
    S = (r + _Hint(encR + pub + m) * a) % _l
    return encR + _encodeint(S)


def _isoncurve(P):
    x, y = P
    return (-x * x + y * y - 1 - _d * x * x * y * y) % _q == 0


def _decodeint(s):
    return int.from_bytes(s, "little")


def _decodepoint(s):
    y = sum(2 ** i * _bit(s, i) for i in range(_b - 1))
    x = _xrecover(y)
    if x & 1 != _bit(s, _b - 1):
        x = _q - x
    P = (x, y)
    if not _isoncurve(P):
        raise ValueError("punto non sulla curva")
    return P


def verifica(firma64, m, pub):
    """True se la firma e' valida per (msg, chiave pubblica). Non solleva."""
    try:
        s = firma64
        if len(s) != 64 or len(pub) != 32:
            return False
        R = _decodepoint(s[:32])
        A = _decodepoint(pub)
        S = _decodeint(s[32:])
        h = _Hint(s[:32] + pub + m)
        return _scalarmult(_B, S) == _edwards_add(R, _scalarmult(A, h))
    except Exception:
        return False


if __name__ == "__main__":
    import os
    seed = os.urandom(32)
    pub = publickey_da_seed(seed)
    msg = b"IU1PJO|DONOR"
    sig = firma(msg, seed, pub)
    print("pub :", pub.hex())
    print("seed:", seed.hex())
    print("ok  :", verifica(sig, msg, pub))
    print("ko  :", verifica(sig, b"IU1PJO|REG", pub))
