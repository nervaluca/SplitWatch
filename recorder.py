"""Registrazione IQ di FZR Skimmer.

Memoria continua: gli ultimi N minuti restano in RAM (16 bit a blocchi con la loro scala,
circa 23 MB al minuto a 96 kHz) e con «Salva ultimi minuti» finiscono in un WAV.
Registrazione manuale: scrive direttamente su disco finché non la fermi.

I WAV sono IQ stereo a 24 bit, leggibili da HDSDR, SDR Console, CW Skimmer e da FZR Skimmer.
Dentro c'è il blocco 'auxi' (frequenza centrale, come HDSDR) e un blocco 'fzrx' con il DX,
così riaprendoli in FZR Skimmer centro e DX tornano da soli.
"""
import collections
import json
import os
import struct
import threading
import time

import numpy as np

FULL24 = 8388607.0
MAX_WAV = 3_900_000_000           # sotto i 4 GB del formato WAV


def _systemtime(t):
    g = time.gmtime(t)
    ms = int((t % 1) * 1000)
    return struct.pack('<8H', g.tm_year, g.tm_mon, (g.tm_wday + 1) % 7, g.tm_mday, g.tm_hour, g.tm_min, g.tm_sec, ms)


def _auxi(t0, t1, center, fs):
    d = _systemtime(t0) + _systemtime(t1) + struct.pack('<IIIII', int(round(center)), int(fs), 0, 0, 0) + b'\0' * 16
    return b'auxi' + struct.pack('<I', len(d)) + d


def _fzrx(center, dx, fs, t0):
    d = json.dumps({'centro': center, 'dx': dx, 'fs': fs, 't0': t0}).encode()
    if len(d) & 1:
        d += b' '
    return b'fzrx' + struct.pack('<I', len(d)) + d


def _fmt(fs):
    return b'fmt ' + struct.pack('<IHHIIHH', 16, 1, 2, int(fs), int(fs) * 6, 6, 24)


def _to24(iq):
    """complex64 normalizzati (±1) -> byte PCM 24 bit I/Q intercalati."""
    v = np.empty(len(iq) * 2, np.float32)
    v[0::2] = iq.real
    v[1::2] = iq.imag
    i = np.clip(np.rint(v * FULL24), -FULL24 - 1, FULL24).astype('<i4')
    return i.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()


def file_name(folder, t0, center, dx, tag=''):
    ts = time.strftime('%Y%m%d_%H%M%SZ', time.gmtime(t0))
    name = f'FZR_{ts}_{center/1000:.3f}kHz'
    if dx:
        name += f'_DX{dx/1000:.2f}'
    if tag:
        name += '_' + tag
    return os.path.join(folder, name + '.wav')


class WavWriter:
    """WAV IQ 24 bit scritto a pezzi; l'intestazione si completa alla chiusura."""

    def __init__(self, path, fs, center, dx, t0=None):
        self.path, self.fs, self.center, self.dx = path, fs, center, dx
        self.t0 = t0 or time.time()
        self.f = open(path, 'wb')
        hdr = _fmt(fs) + _auxi(self.t0, self.t0, center, fs) + _fzrx(center, dx, fs, self.t0)
        self.f.write(b'RIFF' + struct.pack('<I', 0) + b'WAVE' + hdr + b'data' + struct.pack('<I', 0))
        self.data_pos = self.f.tell()
        self.nbytes = 0

    @property
    def seconds(self):
        return self.nbytes / 6 / self.fs

    def write(self, iq):
        b = _to24(iq)
        self.f.write(b)
        self.nbytes += len(b)

    def close(self):
        if self.f is None:
            return
        f, self.f = self.f, None
        t1 = self.t0 + self.seconds
        size = f.tell()
        # aggiorna dimensioni e ora di fine nel blocco auxi
        f.seek(4)
        f.write(struct.pack('<I', size - 8))
        f.seek(12 + 24 + 8 + 16)                       # dopo 'fmt '(24) e intestazione 'auxi'(8) + StartTime
        f.write(_systemtime(t1))
        f.seek(self.data_pos - 4)
        f.write(struct.pack('<I', self.nbytes))
        f.close()


class Recorder:
    def __init__(self, folder, minutes=5.0, on_status=None):
        self.folder = folder
        self.on_status = on_status or (lambda s: None)
        self.lock = threading.Lock()
        self.minutes = float(minutes)
        self.fs = None
        self.center = None
        self.dx = None
        self.segs = collections.deque()          # [fs, centro, t0, blocchi (int16 Nx2, scala), dx, t_ultimo]
        self.nsamp = 0                           # campioni in memoria (tutti i segmenti)
        self.writer = None
        self.rec_tag = ''
        self.saving = False

    # ------------------------------------------------------------ formato
    def set_format(self, fs, center):
        with self.lock:
            if fs == self.fs and center == self.center:
                return
            self.fs, self.center = float(fs), float(center)
            if self.writer:                      # cambio banda: chiudo il file e ne apro un altro
                self._roll_writer()

    def set_dx(self, dx):
        self.dx = dx

    def set_minutes(self, m):
        with self.lock:
            self.minutes = max(0.0, float(m))
            self._trim()

    def max_samples(self):
        if not self.fs:
            return 0
        return int(self.minutes * 60 * self.fs)

    # ------------------------------------------------------------ dati
    def push(self, iq, t_end=None):
        """Blocco IQ appena ricevuto (dal thread del calcolo)."""
        if self.fs is None or not len(iq):
            return
        t_end = t_end or time.time()
        with self.lock:
            if self.writer:
                try:
                    self.writer.write(iq)
                    if self.writer.nbytes > MAX_WAV:
                        self._roll_writer()
                except Exception as e:
                    self.on_status(f'Registrazione interrotta: {e}')
                    self._close_writer()
            if self.minutes <= 0:
                return
            sc = float(max(np.abs(iq.real).max(), np.abs(iq.imag).max(), 1e-9))
            q = np.empty((len(iq), 2), np.int16)
            k = 32767.0 / sc
            q[:, 0] = np.rint(iq.real * k)
            q[:, 1] = np.rint(iq.imag * k)
            last = self.segs[-1] if self.segs else None
            if (last is None or last[0] != self.fs or last[1] != self.center
                    or t_end - last[5] > 2.0 + len(iq) / self.fs):      # buco nei dati (riproduzione, riavvio): segmento nuovo
                self.segs.append([self.fs, self.center, t_end - len(iq) / self.fs, collections.deque(), self.dx, t_end])
            seg = self.segs[-1]
            seg[3].append((q, sc / 32767.0))
            seg[4] = self.dx
            seg[5] = t_end
            self.nsamp += len(iq)
            self._trim()

    def _trim(self):
        lim = self.max_samples()
        while self.segs and self.nsamp > lim:
            seg = self.segs[0]
            if not seg[3]:
                self.segs.popleft()
                continue
            q, _ = seg[3].popleft()
            self.nsamp -= len(q)
            seg[2] += len(q) / seg[0]
            if not seg[3] and len(self.segs) > 1:
                self.segs.popleft()

    def buffered_s(self):
        with self.lock:
            return sum(sum(len(q) for q, _ in s[3]) / s[0] for s in self.segs)

    def mem_mb(self):
        return self.nsamp * 4 / 1e6

    def clear(self):
        with self.lock:
            self.segs.clear()
            self.nsamp = 0

    # ------------------------------------------------------------ salva memoria
    def save_last(self, done=None):
        """Scrive gli ultimi minuti in memoria su disco (in un thread: il ricevitore non si ferma)."""
        if self.saving:
            self.on_status('Sto già salvando…')
            return
        with self.lock:
            snap = [(s[0], s[1], s[2], list(s[3]), s[4]) for s in self.segs if s[3]]
        if not snap:
            self.on_status('Memoria vuota: niente da salvare')
            return
        self.saving = True

        def run():
            paths = []
            try:
                os.makedirs(self.folder, exist_ok=True)
                for fs, center, t0, blocks, dx in snap:
                    p = file_name(self.folder, t0, center, dx, 'ultimi')
                    w = WavWriter(p, fs, center, dx, t0)
                    for q, sc in blocks:
                        iq = (q[:, 0].astype(np.float32) + 1j * q[:, 1].astype(np.float32)) * np.float32(sc)
                        w.write(iq.astype(np.complex64))
                    secs = w.seconds
                    w.close()
                    paths.append((p, secs))
                txt = ' + '.join(f'{os.path.basename(p)} ({s/60:.1f} min)' for p, s in paths)
                self.on_status(f'Salvato: {txt}')
            except Exception as e:
                self.on_status(f'Salvataggio fallito: {e}')
            finally:
                self.saving = False
                if done:
                    done([p for p, _ in paths])
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------ registrazione manuale
    def start_rec(self):
        with self.lock:
            if self.writer or self.fs is None:
                return self.writer is not None
            os.makedirs(self.folder, exist_ok=True)
            self._open_writer()
            return True

    def stop_rec(self):
        with self.lock:
            w = self.writer
            self._close_writer()
        if w:
            self.on_status(f'Registrazione salvata: {os.path.basename(w.path)} ({w.seconds/60:.1f} min)')
        return w.path if w else None

    def rec_info(self):
        w = self.writer
        if not w:
            return None
        return {'name': os.path.basename(w.path), 's': round(w.seconds, 1), 'mb': round(w.nbytes / 1e6, 1)}

    def _open_writer(self):
        self.writer = WavWriter(file_name(self.folder, time.time(), self.center, self.dx), self.fs, self.center, self.dx)

    def _close_writer(self):
        if self.writer:
            try:
                self.writer.close()
            except Exception:
                pass
            self.writer = None

    def _roll_writer(self):
        self._close_writer()
        self._open_writer()
