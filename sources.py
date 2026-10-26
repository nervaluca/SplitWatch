"""Sorgenti IQ per FZR Skimmer.

PerseusExtIO : Perseus dal vivo tramite ExtIO_perseus.dll (32 bit, formato Winrad/HDSDR)
WavFile      : riproduzione in tempo reale di una registrazione IQ (CW Skimmer, HDSDR, SDR Console)

Ogni sorgente chiama on_iq(numpy.complex64) da un suo thread e
on_status(testo) per messaggi da mostrare nell'interfaccia.
"""
import json
import os
import queue
import re
import struct
import sys
import threading
import time

import numpy as np


def unpack24(raw):
    """Byte little-endian a 24 bit con segno -> float32 normalizzati."""
    b = np.frombuffer(raw, dtype=np.uint8)
    b = b[: len(b) // 3 * 3].reshape(-1, 3).astype(np.int32)
    v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
    v = np.where(v >= 1 << 23, v - (1 << 24), v)
    return v.astype(np.float32) / float(1 << 23)


def to_complex(raw, fmt):
    if fmt == 's24':
        v = unpack24(raw)
    elif fmt == 's16':
        v = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
    elif fmt == 's32':
        v = np.frombuffer(raw, dtype='<i4').astype(np.float32) / 2147483648.0
    elif fmt == 'f32':
        v = np.frombuffer(raw, dtype='<f4').astype(np.float32)
    else:
        raise ValueError(fmt)
    v = v[: len(v) // 2 * 2].reshape(-1, 2)
    return (v[:, 0] + 1j * v[:, 1]).astype(np.complex64)


# ---------------------------------------------------------------------------- file
def wav_info(path):
    """Durata, campionamento, centro e DX di un WAV IQ (solo intestazione)."""
    w = WavFile(path, 0, None, None)
    return {'path': path, 'name': os.path.basename(path), 'dur': w.duration, 'fs': w.fs,
            'centro': w.center or None, 'dx': w.dx}


class WavFile:
    """Riproduce un WAV IQ a velocità reale, con pausa e salto avanti/indietro."""

    def __init__(self, path, center_hz, on_iq, on_status, loop=True, speed=1.0, on_rate=None, on_pos=None):
        self.path = path
        self.on_iq = on_iq
        self.on_status = on_status
        self.on_rate = on_rate
        self.on_pos = on_pos
        self.loop = loop
        self.speed = speed
        self.paused = False
        self._seek = None
        self.pos = 0.0
        self._stop = threading.Event()
        self.stopped = threading.Event()
        self.dx = None
        self.fs, self.fmt, self.data_off, self.data_len, self.bpf = self._parse()
        self.center = self._fzrx() or self._auxi_center() or self._name_center() or float(center_hz or 0)
        self.duration = self.data_len / self.bpf / self.fs
        self.nsamp = 0

    def _chunks(self):
        with open(self.path, 'rb') as f:
            f.seek(12)
            while True:
                ch = f.read(8)
                if len(ch) < 8:
                    return
                cid, sz = ch[:4], struct.unpack('<I', ch[4:])[0]
                if cid == b'data':
                    return
                yield cid, f.read(min(sz, 4096))
                f.seek(f.tell() - min(sz, 4096) + sz + (sz & 1))

    def _parse(self):
        with open(self.path, 'rb') as f:
            hdr = f.read(12)
            if hdr[:4] != b'RIFF' or hdr[8:12] != b'WAVE':
                raise ValueError('Il file non è un WAV')
            fmt = None
            bpf = 0
            fs = 0
            while True:
                ch = f.read(8)
                if len(ch) < 8:
                    raise ValueError('WAV senza dati')
                cid, sz = ch[:4], struct.unpack('<I', ch[4:])[0]
                if cid == b'fmt ':
                    d = f.read(sz)
                    tag, nch, fs, _, align, bits = struct.unpack('<HHIIHH', d[:16])
                    if tag == 0xFFFE and len(d) >= 40:
                        tag = struct.unpack('<H', d[24:26])[0]
                    if nch != 2:
                        raise ValueError('Serve un WAV IQ stereo (I e Q)')
                    fmt = {(1, 16): 's16', (1, 24): 's24', (1, 32): 's32', (3, 32): 'f32'}.get((tag, bits))
                    if fmt is None:
                        raise ValueError(f'Formato WAV non gestito ({tag}, {bits} bit)')
                    bpf = align
                    if sz & 1:
                        f.read(1)
                elif cid == b'data':
                    if fmt is None:
                        raise ValueError('WAV senza formato')
                    size = os.path.getsize(self.path) - f.tell()
                    if sz == 0 or sz > size:            # registrazione interrotta: uso quello che c'è
                        sz = size
                    return fs, fmt, f.tell(), sz - sz % bpf, bpf
                else:
                    f.seek(sz + (sz & 1), 1)

    def _fzrx(self):
        """Blocco scritto da FZR Skimmer: centro e DX."""
        try:
            for cid, d in self._chunks():
                if cid == b'fzrx':
                    j = json.loads(d.decode('utf-8', 'replace'))
                    self.dx = float(j['dx']) if j.get('dx') else None
                    return float(j.get('centro') or 0) or None
        except Exception:
            pass
        return None

    def _auxi_center(self):
        """Frequenza centrale dal blocco 'auxi' (HDSDR/SpectraVue: dopo le due SYSTEMTIME)."""
        try:
            for cid, d in self._chunks():
                if cid == b'auxi' and len(d) >= 36:
                    v = float(struct.unpack('<I', d[32:36])[0])
                    return v if v > 10000 else None
        except Exception:
            pass
        return None

    def _name_center(self):
        """Frequenza dal nome del file (HDSDR: ..._18085kHz_RF.wav, SDR Console: ..._18.085MHz...)."""
        n = os.path.basename(self.path)
        m = re.search(r'(\d+(?:[.,]\d+)?)\s*kHz', n, re.I)
        if m:
            return float(m.group(1).replace(',', '.')) * 1000
        m = re.search(r'(\d+(?:[.,]\d+)?)\s*MHz', n, re.I)
        if m:
            return float(m.group(1).replace(',', '.')) * 1e6
        m = re.search(r'_(\d{6,9})Hz', n, re.I)
        if m:
            return float(m.group(1))
        return None

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def set_lo(self, hz):
        """Centro della registrazione scritto a mano (CW Skimmer non lo salva)."""
        self.center = float(hz)
        if self.on_rate:
            self.on_rate(self.fs, self.center)

    def set_pause(self, on):
        self.paused = bool(on)

    def seek(self, s):
        self._seek = max(0.0, min(float(s), self.duration))

    def _run(self):
        block = 2048
        nbytes = block * self.bpf
        last_pos = 0
        try:
            with open(self.path, 'rb') as f:
                start = 0
                while not self._stop.is_set():
                    f.seek(self.data_off + start)
                    left = self.data_len - start
                    t0 = time.perf_counter()
                    sent = 0
                    while left > 0 and not self._stop.is_set():
                        if self._seek is not None:
                            break
                        if self.paused:
                            time.sleep(0.05)
                            t0 = time.perf_counter() - sent / self.fs / self.speed
                            now = time.perf_counter()
                            if self.on_pos and now - last_pos > 0.3:
                                last_pos = now
                                self.on_pos(self)
                            continue
                        raw = f.read(min(nbytes, left))
                        if not raw:
                            break
                        left -= len(raw)
                        iq = to_complex(raw, self.fmt)
                        self.on_iq(iq)
                        sent += len(iq)
                        self.nsamp += len(iq)
                        self.pos = (self.data_len - left) / self.bpf / self.fs
                        now = time.perf_counter()
                        if self.on_pos and now - last_pos > 0.3:
                            last_pos = now
                            self.on_pos(self)
                        ahead = sent / self.fs / self.speed - (now - t0)
                        if ahead > 0:
                            time.sleep(ahead)
                    if self._seek is not None:
                        start = int(self._seek * self.fs) * self.bpf
                        self.pos = self._seek
                        self._seek = None
                        continue
                    start = 0
                    if self._stop.is_set():
                        break
                    if not self.loop:
                        self.paused = True
                        self.pos = self.duration
                        if self.on_pos:
                            self.on_pos(self)
                        self.on_status('Fine della registrazione')
                        while self.paused and self._seek is None and not self._stop.is_set():
                            time.sleep(0.05)
                        if self._seek is None:
                            self._seek = 0.0
                        start = int(self._seek * self.fs) * self.bpf
                        self.pos = self._seek
                        self._seek = None
                        continue
                    self.on_status('Registrazione: ricomincio da capo')
        finally:
            self.stopped.set()


# ---------------------------------------------------------------------------- Perseus
class PerseusExtIO:
    """Perseus tramite ExtIO_perseus.dll (stesso driver di HDSDR).

    La DLL è a 32 bit: il programma deve girare con Python a 32 bit.
    Tutte le chiamate alla DLL avvengono in un unico thread con la coda dei
    messaggi di Windows, così la finestra di impostazioni del Perseus funziona.
    """

    TYPES = {3: ('s16', 4), 5: ('s24', 6), 6: ('s32', 8), 7: ('f32', 8)}

    def __init__(self, folder, lo_hz, on_iq, on_status, on_rate):
        self.folder = folder
        self.lo = int(lo_hz)
        self.on_iq = on_iq
        self.on_status = on_status
        self.on_rate = on_rate
        self.cmd = queue.Queue()
        self.fs = None
        self.center = float(lo_hz)
        self.ready = threading.Event()
        self.error = None
        self._cb = None
        self.nsamp = 0                         # campioni ricevuti (diagnostica)
        self.nblocks = 0

    # API pubblica (thread-safe)
    def start(self):
        threading.Thread(target=self._thread, daemon=True).start()
        self.ready.wait(20)
        if self.error:
            raise RuntimeError(self.error)
        if not self.ready.is_set():
            raise RuntimeError('Il Perseus non risponde')

    def stop(self):
        self.cmd.put(('stop',))

    def set_lo(self, hz):
        self.cmd.put(('lo', int(hz)))

    def show_gui(self):
        self.cmd.put(('gui',))

    # thread della DLL
    def _thread(self):
        import ctypes
        from ctypes import wintypes
        try:
            if struct.calcsize('P') != 4:
                raise RuntimeError('ExtIO_perseus.dll è a 32 bit: serve la versione a 32 bit di FZR Skimmer')
            dll_path = os.path.join(self.folder, 'ExtIO_perseus.dll')
            if not os.path.exists(dll_path):
                raise RuntimeError(f'Non trovo ExtIO_perseus.dll in {self.folder}')
            if not os.path.exists(os.path.join(self.folder, 'perseususb.dll')):
                raise RuntimeError(f'Non trovo perseususb.dll in {self.folder} (copiala dalla cartella del software Perseus)')
            k32 = ctypes.windll.kernel32
            k32.SetDllDirectoryW(self.folder)
            os.chdir(self.folder)                      # i file .sbs dell'FPGA si caricano da qui
            dll = ctypes.WinDLL(dll_path)
            u32 = ctypes.windll.user32

            # callback cdecl: void cb(int cnt, int status, float IQoffs, void* IQdata)
            CB = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_int, ctypes.c_float, ctypes.c_void_p)
            self._CB = CB
            name = ctypes.create_string_buffer(64)
            model = ctypes.create_string_buffer(64)
            typ = ctypes.c_int(0)
            dll.InitHW.restype = ctypes.c_bool
            dll.InitHW.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
            fmt, bpf = 's24', 6                     # aggiornati dopo InitHW
            fmt_box = {'fmt': fmt, 'bpf': bpf}

            def cb_early(cnt, status, iqoffs, data):
                try:
                    if cnt > 0 and data:
                        raw = ctypes.string_at(data, cnt * fmt_box['bpf'])
                        self.nsamp += cnt
                        self.nblocks += 1
                        if self.nblocks == 1:
                            self.on_status(f'Primo blocco dal Perseus: {cnt} campioni')
                        self.on_iq(to_complex(raw, fmt_box['fmt']))
                    elif cnt < 0:
                        self.on_status(f'Messaggio dal driver: stato {status}')
                        if status == 100:
                            self.cmd.put(('rate',))
                        elif status == 101:
                            self.cmd.put(('getlo',))
                except Exception as e:
                    self.on_status(f'Errore dati: {e}')

            self._cb = CB(cb_early)                 # tenere il riferimento!
            dll.SetCallback.argtypes = [CB]
            dll.SetCallback.restype = None
            dll.SetCallback(self._cb)
            if not dll.InitHW(name, model, ctypes.byref(typ)):
                raise RuntimeError('Perseus non trovato (InitHW). È acceso e collegato? Un altro programma lo sta usando?')
            if typ.value not in self.TYPES:
                raise RuntimeError(f'Formato campioni ExtIO non gestito: {typ.value}')
            fmt, bpf = self.TYPES[typ.value]
            fmt_box['fmt'], fmt_box['bpf'] = fmt, bpf

            dll.OpenHW.restype = ctypes.c_bool
            if not dll.OpenHW():
                raise RuntimeError('Apertura del Perseus fallita (OpenHW)')
            dll.GetHWSR.restype = ctypes.c_long
            dll.GetHWLO.restype = ctypes.c_long
            dll.SetHWLO.argtypes = [ctypes.c_long]
            dll.StartHW.argtypes = [ctypes.c_long]
            dll.StartHW.restype = ctypes.c_int
            sr0 = float(dll.GetHWSR())
            self.on_status(f'Perseus: {name.value.decode(errors="ignore")} {model.value.decode(errors="ignore")}, formato {fmt}, campionamento dichiarato {sr0:.0f} Hz')
            nb = dll.StartHW(self.lo)
            self.on_status(f'StartHW({self.lo}) -> {nb} campioni per blocco')
            if nb <= 0:
                raise RuntimeError('Avvio del Perseus fallito (StartHW)')
            self.center = float(dll.GetHWLO() or self.lo)
            self.fs = float(dll.GetHWSR()) or sr0
            if self.fs <= 0:
                # il driver non lo dice: lo misuro dai dati in arrivo
                t0 = time.time(); n0 = self.nsamp
                while time.time() - t0 < 1.5:
                    msg0 = wintypes.MSG()
                    while u32.PeekMessageW(ctypes.byref(msg0), None, 0, 0, 1):
                        u32.TranslateMessage(ctypes.byref(msg0))
                        u32.DispatchMessageW(ctypes.byref(msg0))
                    k32.SleepEx(10, True)
                meas = (self.nsamp - n0) / (time.time() - t0)
                known = [48077, 95238, 96154, 125000, 192308, 250000, 500000, 1000000, 2000000]
                self.fs = float(min(known, key=lambda k: abs(k - meas))) if meas > 1000 else 0.0
                self.on_status(f'Campionamento non dichiarato dal driver: misurati {meas:.0f} campioni/s -> {self.fs:.0f} Hz')
                if self.fs <= 0:
                    raise RuntimeError('Il Perseus non manda dati (0 campioni/s). Controlla che nessun altro programma lo stia usando.')
            self.on_rate(self.fs, self.center)
            self.on_status(f'Perseus attivo: {model.value.decode(errors="ignore") or "Perseus"} · {self.fs/1000:.1f} kHz · centro {self.center/1000:.3f}')
            self.ready.set()

            msg = wintypes.MSG()
            running = True
            QS_ALLINPUT, MWMO_ALERTABLE, MWMO_INPUTAVAILABLE = 0x04FF, 0x0002, 0x0004
            u32.MsgWaitForMultipleObjectsEx.argtypes = [wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
            t_start = time.time()
            retried = False
            while running:
                # attesa che lascia lavorare la USB (APC) e la finestra del driver
                u32.MsgWaitForMultipleObjectsEx(0, None, 20, QS_ALLINPUT, MWMO_ALERTABLE | MWMO_INPUTAVAILABLE)
                while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    u32.TranslateMessage(ctypes.byref(msg))
                    u32.DispatchMessageW(ctypes.byref(msg))
                if not retried and self.nsamp == 0 and time.time() - t_start > 4:
                    retried = True
                    self.on_status('Nessun dato dopo 4 s: riavvio lo streaming (StopHW/StartHW)')
                    dll.StopHW()
                    k32.SleepEx(300, True)
                    r = dll.StartHW(self.lo)
                    self.on_status(f'StartHW di nuovo -> {r}')
                try:
                    c = self.cmd.get_nowait()
                except queue.Empty:
                    continue
                if c[0] == 'stop':
                    dll.StopHW()
                    dll.CloseHW()
                    running = False
                elif c[0] == 'lo':
                    dll.SetHWLO(c[1])
                    self.center = float(dll.GetHWLO() or c[1])
                    self.lo = int(self.center)
                    self.on_rate(self.fs, self.center)
                elif c[0] == 'getlo':
                    self.center = float(dll.GetHWLO())
                    self.on_rate(self.fs, self.center)
                elif c[0] == 'rate':
                    self.fs = float(dll.GetHWSR()) or self.fs
                    self.on_rate(self.fs, self.center)
                    self.on_status(f'Campionamento: {self.fs/1000:.1f} kHz')
                elif c[0] == 'gui':
                    dll.ShowGUI()
        except Exception as e:
            self.error = str(e)
            self.on_status(str(e))
            self.ready.set()


# ---------------------------------------------------------------------------- ExtIO generico
def find_extio_dlls(extra=()):
    """ExtIO_*.dll a 32 bit nelle cartelle solite (HDSDR, programma, scelte dall'utente)."""
    dirs = list(extra)
    for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), os.environ.get('LOCALAPPDATA')):
        if base:
            dirs += [os.path.join(base, 'HDSDR'), os.path.join(base, 'Programs', 'HDSDR'),
                     os.path.join(base, 'SDRplay', 'ExtIO'), os.path.join(base, 'Airspy')]
    out, seen = [], set()
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except Exception:
            continue
        for n in sorted(names):
            if n.lower().startswith('extio') and n.lower().endswith('.dll') and 'perseus' not in n.lower():
                p = os.path.abspath(os.path.join(d, n))
                if p.lower() not in seen:
                    seen.add(p.lower())
                    out.append(p)
    return out


class Decimator:
    """Riduce il campionamento di un fattore intero (FIR passa-basso, calcolato solo sui campioni tenuti).
    Serve per i ricevitori che danno 2 MHz o più (SDRplay): il programma lavora bene fino a ~200 kHz."""
    TARGET = 200000

    def __init__(self, fs):
        self.fs_in = float(fs)
        self.d = max(1, int(np.ceil(self.fs_in / self.TARGET)))
        self.fs = self.fs_in / self.d
        if self.d > 1:
            n = 10 * self.d + 1
            t = np.arange(n) - (n - 1) / 2
            h = np.sinc(0.8 * t / self.d) * np.blackman(n)
            self.h = (h / h.sum()).astype(np.float32)[::-1].copy()
            self.tail = np.zeros(n - 1, np.complex64)
            self.ph = 0                              # da dove parte il prossimo campione tenuto

    def __call__(self, x):
        if self.d == 1:
            return x
        n = len(self.h)
        z = np.concatenate([self.tail, x.astype(np.complex64)])
        idx = np.arange(self.ph, len(z) - n + 1, self.d)
        if len(idx):
            w = np.lib.stride_tricks.sliding_window_view(z, n)[idx]
            y = (w @ self.h).astype(np.complex64)
            self.ph = idx[-1] + self.d - (len(z) - (n - 1))
        else:
            y = np.zeros(0, np.complex64)
            self.ph -= len(x)
        self.tail = z[-(n - 1):].copy()
        return y


class ExtIOSource:
    """Qualsiasi ricevitore con un plugin ExtIO per HDSDR/Winrad (SDRplay RSP, Airspy HF+, FunCube, …).

    Il plugin deve essere a 32 bit (quelli per HDSDR lo sono). Tutte le chiamate alla DLL
    avvengono in un solo thread con la coda dei messaggi di Windows, così la finestra
    di impostazioni del plugin (guadagno, antenna, AGC…) funziona.
    """

    TYPES = {3: ('s16', 4), 5: ('s24', 6), 6: ('s32', 8), 7: ('f32', 8)}

    def __init__(self, dll_path, lo_hz, rate, on_iq, on_status, on_rate):
        self.dll_path = dll_path
        self.name = os.path.splitext(os.path.basename(dll_path))[0]
        self.lo = int(lo_hz)
        self.want_rate = int(rate or 0)
        self.on_iq = on_iq
        self.on_status = on_status
        self.on_rate = on_rate
        self.cmd = queue.Queue()
        self.fs = None
        self.center = float(lo_hz)
        self.rates = []                         # campionamenti offerti dal plugin
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.error = None
        self._cb = None
        self.dec = None
        self.fs_hw = None
        self.nsamp = 0
        self.nblocks = 0

    def _new_rate(self, fs_hw):
        """Campionamento del ricevitore -> quello che arriva al programma (ridotto se serve)."""
        self.fs_hw = float(fs_hw)
        if not self.dec or self.dec.fs_in != self.fs_hw:
            self.dec = Decimator(self.fs_hw)
        self.fs = self.dec.fs
        self.on_rate(self.fs, self.center)

    # API pubblica (thread-safe)
    def start(self):
        threading.Thread(target=self._thread, daemon=True).start()
        self.ready.wait(25)
        if self.error:
            raise RuntimeError(self.error)
        if not self.ready.is_set():
            raise RuntimeError(f'{self.name} non risponde')

    def stop(self):
        self.cmd.put(('stop',))
        if self.ready.is_set():
            self.stopped.wait(4)               # il plugin va chiuso prima di riaprirlo

    def set_lo(self, hz):
        self.cmd.put(('lo', int(hz)))

    def set_rate(self, hz):
        self.cmd.put(('setrate', int(hz)))

    def show_gui(self):
        self.cmd.put(('gui',))

    def _thread(self):
        import ctypes
        from ctypes import wintypes
        dll = None
        try:
            if struct.calcsize('P') != 4:
                raise RuntimeError('I plugin ExtIO per HDSDR sono a 32 bit: serve FZR Skimmer a 32 bit')
            if not os.path.exists(self.dll_path):
                raise RuntimeError(f'Non trovo {self.dll_path}')
            folder = os.path.dirname(self.dll_path)
            k32 = ctypes.windll.kernel32
            k32.SetDllDirectoryW(folder)              # le DLL che il plugin si porta dietro
            try:
                dll = ctypes.WinDLL(self.dll_path)
            except OSError as e:
                raise RuntimeError(f'{self.name}: impossibile caricare il plugin ({e}). '
                                   'Serve la versione a 32 bit e il driver del ricevitore installato.')
            u32 = ctypes.windll.user32
            CB = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_int, ctypes.c_float, ctypes.c_void_p)
            box = {'fmt': 's16', 'bpf': 4}

            def cb(cnt, status, iqoffs, data):
                try:
                    if cnt > 0 and data:
                        raw = ctypes.string_at(data, cnt * box['bpf'])
                        self.nsamp += cnt
                        self.nblocks += 1
                        if self.nblocks == 1:
                            self.on_status(f'{self.name}: primo blocco, {cnt} campioni')
                        y = self.dec(to_complex(raw, box['fmt'])) if self.dec else to_complex(raw, box['fmt'])
                        if len(y):
                            self.on_iq(y)
                    elif cnt < 0:
                        if status == 100:            # campionamento cambiato (dalla finestra del plugin)
                            self.cmd.put(('rate',))
                        elif status in (101, 102):   # LO cambiato
                            self.cmd.put(('getlo',))
                except Exception as e:
                    self.on_status(f'Errore dati: {e}')

            self._cb = CB(cb)
            dll.SetCallback.argtypes = [CB]
            dll.SetCallback.restype = None
            dll.SetCallback(self._cb)              # alcuni plugin la vogliono prima di InitHW, altri dopo
            name = ctypes.create_string_buffer(256)
            model = ctypes.create_string_buffer(256)
            typ = ctypes.c_int(0)
            dll.InitHW.restype = ctypes.c_bool
            dll.InitHW.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
            if not dll.InitHW(name, model, ctypes.byref(typ)):
                raise RuntimeError(f'{self.name}: ricevitore non trovato (InitHW). È collegato? Un altro programma lo sta usando?')
            if typ.value not in self.TYPES:
                raise RuntimeError(f'{self.name}: formato dei campioni non gestito ({typ.value})')
            box['fmt'], box['bpf'] = self.TYPES[typ.value]
            dll.SetCallback(self._cb)              # dopo InitHW, come fa HDSDR
            dll.OpenHW.restype = ctypes.c_bool
            if not dll.OpenHW():
                raise RuntimeError(f'{self.name}: apertura del ricevitore fallita (OpenHW)')
            dll.GetHWSR.restype = ctypes.c_long
            dll.GetHWLO.restype = ctypes.c_long
            dll.SetHWLO.argtypes = [ctypes.c_long]
            dll.SetHWLO.restype = ctypes.c_int
            dll.StartHW.argtypes = [ctypes.c_long]
            dll.StartHW.restype = ctypes.c_int

            # campionamenti disponibili (funzioni facoltative dei plugin moderni)
            get_sr = getattr(dll, 'ExtIoGetSrates', None)
            set_sr = getattr(dll, 'ExtIoSetSrate', None)
            if get_sr is not None:
                get_sr.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_double)]
                get_sr.restype = ctypes.c_int
                v = ctypes.c_double()
                for i in range(64):
                    if get_sr(i, ctypes.byref(v)) != 0:
                        break
                    self.rates.append(float(v.value))
            if set_sr is not None:
                set_sr.argtypes = [ctypes.c_int]
                set_sr.restype = ctypes.c_int

            def choose_rate(want):
                if not self.rates or set_sr is None:
                    return
                # il più vicino a quello voluto (di base 192 kHz: oltre il PC lavora molto di più)
                want = want or 192000
                i = min(range(len(self.rates)), key=lambda k: abs(self.rates[k] - want))
                set_sr(i)

            choose_rate(self.want_rate)
            nb = dll.StartHW(self.lo)
            if nb <= 0:
                raise RuntimeError(f'{self.name}: avvio fallito (StartHW)')
            self.center = float(dll.GetHWLO() or self.lo)
            fs_hw = float(dll.GetHWSR())
            if fs_hw <= 0:
                raise RuntimeError(f'{self.name}: il plugin non dichiara il campionamento')
            self._new_rate(fs_hw)
            red = f' (ricevitore {fs_hw/1000:.0f} kHz ÷ {self.dec.d})' if self.dec.d > 1 else ''
            self.on_status(f'{name.value.decode(errors="ignore") or self.name} {model.value.decode(errors="ignore")} attivo · '
                           f'{self.fs/1000:.1f} kHz{red} · centro {self.center/1000:.3f}')
            self.ready.set()

            msg = wintypes.MSG()
            QS_ALLINPUT, MWMO_ALERTABLE, MWMO_INPUTAVAILABLE = 0x04FF, 0x0002, 0x0004
            u32.MsgWaitForMultipleObjectsEx.argtypes = [wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
            t0 = time.time()
            warned = False
            running = True
            while running:
                u32.MsgWaitForMultipleObjectsEx(0, None, 20, QS_ALLINPUT, MWMO_ALERTABLE | MWMO_INPUTAVAILABLE)
                while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    u32.TranslateMessage(ctypes.byref(msg))
                    u32.DispatchMessageW(ctypes.byref(msg))
                if not warned and self.nsamp == 0 and time.time() - t0 > 5:
                    warned = True
                    self.on_status(f'{self.name}: nessun dato dopo 5 s')
                try:
                    c = self.cmd.get_nowait()
                except queue.Empty:
                    continue
                if c[0] == 'stop':
                    try:
                        dll.StopHW()
                        dll.CloseHW()
                    finally:
                        running = False
                elif c[0] == 'lo':
                    dll.SetHWLO(c[1])
                    self.center = float(dll.GetHWLO() or c[1])
                    self.lo = int(self.center)
                    self.on_rate(self.fs, self.center)
                elif c[0] == 'getlo':
                    self.center = float(dll.GetHWLO() or self.center)
                    self.on_rate(self.fs, self.center)
                elif c[0] == 'setrate':
                    dll.StopHW()
                    choose_rate(c[1])
                    dll.StartHW(self.lo)
                    self._new_rate(float(dll.GetHWSR()) or self.fs_hw)
                    self.on_status(f'Campionamento {self.fs/1000:.1f} kHz'
                                   + (f' (ricevitore {self.fs_hw/1000:.0f} kHz ÷ {self.dec.d})' if self.dec.d > 1 else ''))
                elif c[0] == 'rate':
                    self._new_rate(float(dll.GetHWSR()) or self.fs_hw)
                    self.on_status(f'Campionamento {self.fs/1000:.1f} kHz'
                                   + (f' (ricevitore {self.fs_hw/1000:.0f} kHz ÷ {self.dec.d})' if self.dec.d > 1 else ''))
                elif c[0] == 'gui':
                    sg = getattr(dll, 'ShowGUI', None)
                    if sg is not None:
                        sg()
                    else:
                        self.on_status(f'{self.name} non ha una finestra di impostazioni')
        except Exception as e:
            self.error = str(e)
            self.on_status(str(e))
            self.ready.set()
        finally:
            self.stopped.set()


# ---------------------------------------------------------------------------- Perseus diretto
class PerseusDirect:
    """Perseus comandato direttamente tramite perseususb.dll (driver WinUSB),
    come fa CW Skimmer. Non serve ExtIO_perseus.dll.

    Sequenza ricavata dalla ExtIO di Microtelecom e da libperseus-sdr:
      Open -> FirmwareDownload(NULL) -> FpgaConfig(sbs, chiave) -> preselettore/attenuatore
      -> Sio(controllo, frequenza) -> StartAsyncInput(n*6144, callback) -> Sio(+FIFO)
    """

    ADC_CLK = 80e6
    # campionamento: (file .sbs, chiave di controllo a 8 byte, campioni/s reali)
    RATES = {
        48000:   ('perseus48k24v31',  'e437737b9a6a0000', 80e6 / 1664),
        96000:   ('perseus96k24v31',  '391879f5908c0000', 80e6 / 832),
        125000:  ('perseus125k24v21', '457cd43a0ed40000', 80e6 / 640),
        192000:  ('perseus192k24v31', '7b3926759f7b0000', 80e6 / 416),
        250000:  ('perseus250k24v21', 'cf4b9f02b21e0000', 80e6 / 320),
    }
    # preselettore: limite superiore (Hz) -> filtro; oltre 32 MHz = banda larga (10)
    PRESEL = [(1.7e6, 0), (2.1e6, 1), (3.0e6, 2), (4.2e6, 3), (6.0e6, 4), (8.4e6, 5),
              (12e6, 6), (17e6, 7), (24e6, 8), (32e6, 9)]
    FIFOEN, DITHER, GAINHIGH = 0x01, 0x02, 0x04

    def __init__(self, folder, lo_hz, rate, on_iq, on_status, on_rate, preamp=True, swap_iq=False, att_db=0):
        self.att_db = int(att_db)
        self.folder = folder
        self.lo = int(lo_hz)
        self.rate_key = rate if rate in self.RATES else 96000
        self.on_iq = on_iq
        self.on_status = on_status
        self.on_rate = on_rate
        self.ctl = self.DITHER | (self.GAINHIGH if preamp else 0)
        self.swap_iq = swap_iq
        self.cmd = queue.Queue()
        self.ready = threading.Event()
        self.error = None
        self.nsamp = 0
        self.nblocks = 0
        self.fs = None
        self.center = float(lo_hz)
        self._cb = None
        self.stopped = threading.Event()

    # API pubblica
    def start(self):
        threading.Thread(target=self._thread, daemon=True).start()
        self.ready.wait(20)
        if self.error:
            raise RuntimeError(self.error)
        if not self.ready.is_set():
            raise RuntimeError('Il Perseus non risponde')

    def stop(self):
        self.cmd.put(('stop',))

    def set_lo(self, hz):
        self.cmd.put(('lo', int(hz)))

    def set_rate(self, rate):
        self.cmd.put(('rate', int(rate)))

    def show_gui(self):
        self.on_status('Con il driver diretto non c\'è la finestra del Perseus: usa i comandi della pagina')

    def set_swap(self, on):
        self.swap_iq = bool(on)

    def set_att(self, db):
        """Attenuatore del Perseus: 0, 10, 20 o 30 dB."""
        self.cmd.put(('att', max(0, min(30, int(db) // 10 * 10))))

    def set_preamp(self, on):
        self.cmd.put(('pre', bool(on)))

    # thread
    def _presel(self, hz):
        for lim, fid in self.PRESEL:
            if hz < lim:
                return fid
        return 10

    def _thread(self):
        import ctypes
        try:
            if struct.calcsize('P') != 4:
                raise RuntimeError('perseususb.dll è a 32 bit: serve la versione a 32 bit di FZR Skimmer')
            path = os.path.join(self.folder, 'perseususb.dll')
            if not os.path.exists(path):
                raise RuntimeError(f'Non trovo perseususb.dll in {self.folder} (è nella cartella di CW Skimmer o del software Perseus)')
            k32 = ctypes.windll.kernel32
            k32.SetDllDirectoryW(self.folder)
            os.chdir(self.folder)                      # i file .sbs si caricano da qui
            u = ctypes.WinDLL(path)                    # funzioni __stdcall
            u.Open.restype = ctypes.c_int
            u.Close.restype = ctypes.c_int
            u.FirmwareDownload.argtypes = [ctypes.c_char_p]
            u.FirmwareDownload.restype = ctypes.c_int
            u.FpgaConfig.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
            u.FpgaConfig.restype = ctypes.c_int
            u.Sio.argtypes = [ctypes.c_void_p, ctypes.c_int]
            u.Sio.restype = ctypes.c_int
            u.SetAttenuator.argtypes = [ctypes.c_int]
            u.SetPreselector.argtypes = [ctypes.c_int]
            CB = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)   # void __stdcall cb(buf, extra)
            u.StartAsyncInput.argtypes = [ctypes.c_int, CB, ctypes.c_void_p]
            u.StartAsyncInput.restype = ctypes.c_int
            u.StopAsyncInput.restype = ctypes.c_int
            try:
                u.GetDLLVersion.restype = ctypes.c_int
                ver = u.GetDLLVersion()
            except Exception:
                ver = '?'

            sio = (ctypes.c_ubyte * 5)()

            def send_sio(freq):
                freg = int(round(freq / self.ADC_CLK * 4294967296.0)) & 0xFFFFFFFF
                sio[0] = self.ctl & 0xFF
                struct.pack_into('<I', sio, 1, freg)
                return u.Sio(ctypes.byref(sio), 5)

            state = {'bufsize': 0}

            def cb(buf, extra):
                try:
                    n = state['bufsize']
                    if buf and n:
                        raw = ctypes.string_at(buf, n)
                        self.nsamp += n // 6
                        self.nblocks += 1
                        if self.nblocks == 1:
                            self.on_status(f'Primo blocco dal Perseus: {n // 6} campioni')
                        iq = to_complex(raw, 's24')
                        if self.swap_iq:
                            iq = np.conj(iq) * 1j
                        self.on_iq(iq)
                except Exception as e:
                    self.on_status(f'Errore dati: {e}')

            self._cb = CB(cb)                           # tenere il riferimento!

            def configure(rate_key):
                name, key_hex, fs = self.RATES[rate_key]
                if not os.path.exists(os.path.join(self.folder, name + '.sbs')):
                    raise RuntimeError(f'Manca il file {name}.sbs nella cartella {self.folder}')
                key = ctypes.create_string_buffer(bytes.fromhex(key_hex), 8)
                r = u.FpgaConfig(name.encode(), key)
                self.on_status(f'FPGA {name}.sbs -> {r} (0 = ok)')
                if r != 0:
                    raise RuntimeError(f'Configurazione FPGA fallita ({name}.sbs, codice {r})')
                return fs

            def start_stream():
                # blocchi multipli di 6144 byte (1024 campioni), ~30 ms
                k = max(1, int(round(self.fs * 0.03 / 1024)))
                state['bufsize'] = 6144 * k
                self.ctl &= ~self.FIFOEN
                send_sio(self.lo)
                ok = u.StartAsyncInput(state['bufsize'], self._cb, None)
                self.on_status(f'StartAsyncInput({state["bufsize"]}) -> {ok} (1 = ok)')
                if not ok:
                    raise RuntimeError('Avvio dello streaming fallito (StartAsyncInput)')
                self.ctl |= self.FIFOEN
                send_sio(self.lo)

            def stop_stream():
                self.ctl &= ~self.FIFOEN
                send_sio(self.lo)
                u.StopAsyncInput()

            def tune(hz):
                self.lo = int(hz)
                fid = self._presel(self.lo)
                u.SetPreselector(fid)
                send_sio(self.lo)
                self.center = float(self.lo)
                return fid

            if not u.Open():
                raise RuntimeError('Perseus non trovato (Open). È acceso e collegato? CW Skimmer o un altro programma lo stanno usando?')
            r = u.FirmwareDownload(None)
            self.on_status(f'perseususb.dll versione {ver} · firmware -> {r} (0 = ok)')
            self.fs = configure(self.rate_key)
            u.SetAttenuator(self.att_db // 10)
            fid = tune(self.lo)
            self.on_rate(self.fs, self.center)
            start_stream()
            self.on_status(f'Perseus attivo · {self.fs/1000:.1f} kHz · centro {self.center/1000:.3f} · preselettore {fid}')
            self.ready.set()

            t0 = time.time()
            while True:
                try:
                    c = self.cmd.get(timeout=0.25)
                except queue.Empty:
                    if self.nsamp == 0 and time.time() - t0 > 4:
                        self.on_status('Ancora nessun dato dal Perseus dopo 4 s')
                        t0 = time.time() + 1e9
                    continue
                if c[0] == 'stop':
                    try:
                        stop_stream()
                        u.Close()
                    finally:
                        self.stopped.set()
                    break
                elif c[0] == 'lo':
                    fid = tune(c[1])
                    self.on_rate(self.fs, self.center)
                    self.on_status(f'Centro {self.center/1000:.3f} kHz · preselettore {fid}')
                elif c[0] == 'att':
                    self.att_db = c[1]
                    u.SetAttenuator(self.att_db // 10)
                    self.on_status(f'Attenuatore {self.att_db} dB')
                elif c[0] == 'pre':
                    self.ctl = (self.ctl | self.GAINHIGH) if c[1] else (self.ctl & ~self.GAINHIGH)
                    tune(self.lo)                                 # il bit del preamplificatore viaggia con la frequenza
                    self.on_status('Preamplificatore ' + ('acceso' if c[1] else 'spento'))
                elif c[0] == 'rate':
                    if c[1] in self.RATES and c[1] != self.rate_key:
                        stop_stream()
                        self.rate_key = c[1]
                        self.fs = configure(self.rate_key)
                        tune(self.lo)
                        self.on_rate(self.fs, self.center)
                        start_stream()
                        self.on_status(f'Campionamento {self.fs/1000:.1f} kHz')
        except Exception as e:
            self.error = str(e)
            self.on_status(str(e))
            self.ready.set()


# ---------------------------------------------------------------------------- scheda audio
def list_audio_inputs():
    """Ingressi audio disponibili: [(indice, nome, canali)]."""
    try:
        import sounddevice as sd
        out = []
        hostapis = sd.query_hostapis()
        for i, d in enumerate(sd.query_devices()):
            if d.get('max_input_channels', 0) > 0:
                api = hostapis[d['hostapi']]['name'] if d.get('hostapi') is not None else ''
                if 'WDM-KS' in api:            # spesso instabile, lo salto
                    continue
                out.append((i, f"{d['name']} ({api})", int(d['max_input_channels'])))
        return out
    except Exception:
        return []


class AudioCard:
    """Ricezione dalla scheda audio del PC.

    mode 'audio': uscita audio della radio (mono, USB o CW). Il segnale reale viene reso
                  complesso (spostamento di fs/4 + filtro), così non ci sono immagini.
                  Frequenza RF = frequenza radio + (frequenza audio - offset).
                  Offset = nota CW se la radio mostra la frequenza del segnale (Icom in CW),
                  0 se mostra la portante USB.
    mode 'iq':    I e Q sui canali sinistro/destro (SoftRock e simili), centro = LO.
    """

    def __init__(self, device, mode, dial_hz, offset_hz, rate, on_iq, on_status, on_rate, swap_iq=False):
        self.device = device
        self.mode = mode
        self.dial = float(dial_hz)
        self.offset = float(offset_hz)
        self.fs = float(rate)
        self.on_iq = on_iq
        self.on_status = on_status
        self.on_rate = on_rate
        self.swap_iq = swap_iq
        self.nsamp = 0
        self.stream = None
        self.n4 = 0
        taps = 127
        n = np.arange(taps) - (taps - 1) / 2
        h = np.sinc(2 * 0.235 * n) * np.blackman(taps)          # passa-basso a ~fs/4
        self.h = (h / h.sum()).astype(np.float32)
        self.state = np.zeros(taps - 1, np.complex64)

    @property
    def center(self):
        if self.mode == 'audio':
            return self.dial - self.offset + self.fs / 4
        return self.dial

    def _real_to_iq(self, x):
        n = len(x)
        rot = np.array([1, -1j, -1, 1j], np.complex64)[(self.n4 + np.arange(n)) % 4]   # e^{-j pi n/2}
        self.n4 = (self.n4 + n) % 4
        y = np.concatenate([self.state, x.astype(np.float32) * rot])
        z = np.convolve(y, self.h, mode='valid').astype(np.complex64)
        self.state = y[-(len(self.h) - 1):]
        return z

    def _cb(self, indata, frames, t, status):
        try:
            if self.mode == 'iq' and indata.shape[1] >= 2:
                a, b = indata[:, 0], indata[:, 1]
                if self.swap_iq:
                    a, b = b, a
                iq = (a + 1j * b).astype(np.complex64)
            else:
                iq = self._real_to_iq(indata[:, 0])
            self.nsamp += frames
            self.on_iq(iq)
        except Exception as e:
            self.on_status(f'Errore audio: {e}')

    def start(self):
        import sounddevice as sd
        ch = 2 if self.mode == 'iq' else 1
        self.stream = sd.InputStream(device=self.device, channels=ch, samplerate=self.fs, dtype='float32',
                                     blocksize=int(self.fs * 0.02), callback=self._cb, latency='low')
        self.stream.start()
        name = sd.query_devices(self.device)['name'] if self.device is not None else 'predefinito'
        self.on_rate(self.fs, self.center)
        what = 'IQ stereo' if self.mode == 'iq' else f'audio radio (offset {self.offset:.0f} Hz)'
        self.on_status(f'Scheda audio: {name} · {what} · {self.fs/1000:.0f} kHz')

    def stop(self):
        try:
            if self.stream:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass

    def set_lo(self, hz):
        """Senza CAT: è la frequenza mostrata dalla radio, la imposti tu."""
        self.dial = float(hz)
        self.on_rate(self.fs, self.center)

    def set_offset(self, hz):
        self.offset = float(hz)
        self.on_rate(self.fs, self.center)

    def set_swap(self, on):
        self.swap_iq = bool(on)


# ---------------------------------------------------------------------------------------------- RTL-SDR
# 1,92 MS/s ÷ 10 = 192 kHz di banda al programma. rtl_tcp manda i dati a pacchetti fissi da 256 kB:
# a 1,92 MS/s ne arriva uno ogni 68 ms (a 960 kS/s erano 136 ms, waterfall e audio andavano a scatti)
RTL_RATE = 1920000
RTL_GAINS = [0, 9, 14, 27, 37, 77, 87, 125, 144, 157, 166, 197, 207, 229, 254, 280, 297, 328, 338, 364, 372,
             386, 402, 421, 434, 439, 445, 480, 496]          # R820T/R828D, decimi di dB
# guadagni veri di ogni tuner (librtlsdr), decimi di dB
RTL_GAINS_TUNER = {'E4000': [-10, 15, 40, 65, 90, 115, 140, 165, 190, 215, 240, 290, 340, 420],
                   'FC0012': [-99, -40, 71, 179, 192],
                   'FC0013': [-99, -73, -65, -63, -60, -58, -54, 58, 61, 63, 65, 67, 68, 70, 71, 179, 181, 182,
                              184, 186, 188, 191, 197],
                   'R820T': RTL_GAINS, 'R828D': RTL_GAINS}
RTL_TUNERS = {1: 'E4000', 2: 'FC0012', 3: 'FC0013', 4: 'FC2580', 5: 'R820T', 6: 'R828D'}
RTL_IDS = ('VID_0BDA&PID_2838', 'VID_0BDA&PID_2832')


def find_rtl_tcp(extra=()):
    """rtl_tcp.exe: nella cartella del programma (anche sottocartella rtl), in quelle note o nel PATH."""
    import shutil
    exe = 'rtl_tcp.exe' if os.name == 'nt' else 'rtl_tcp'
    base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    pf = [os.environ.get(k) for k in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA')]
    cands = list(extra) + [base, os.path.join(base, 'rtl')]
    for p in pf:
        if p:
            cands += [os.path.join(p, d) for d in ('rtl-sdr', 'rtl-sdr-blog', 'RTL-SDR Blog', 'rtlsdr', 'SDRSharp', 'airspy-sdrsharp')]
    for d in cands:
        if d and os.path.isfile(os.path.join(d, exe)):
            return os.path.join(d, exe)
        if d and os.path.isdir(d):                       # zip delle release: x64/, x86/, Release/
            for sub in ('x64', 'x86', 'Release', 'release', 'bin'):
                q = os.path.join(d, sub, exe)
                if os.path.isfile(q):
                    return q
    return shutil.which(exe)


_rtl_seen = {'t': 0.0, 'v': []}


def find_rtl_dongles(max_age=30.0):
    """Chiavette RTL-SDR collegate (nome), cercate fra le periferiche USB. Non serve aprirle."""
    if time.time() - _rtl_seen['t'] < max_age:
        return list(_rtl_seen['v'])
    found = []
    try:
        if os.name == 'nt':
            import subprocess
            ps = ("Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_0BDA&PID_283[28]' } | "
                  "ForEach-Object { $_.FriendlyName }")
            r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps], capture_output=True,
                               text=True, timeout=12, creationflags=0x08000000)
            found = [x.strip() for x in r.stdout.splitlines() if x.strip()]
        else:
            import glob
            for d in glob.glob('/sys/bus/usb/devices/*'):
                try:
                    v = open(os.path.join(d, 'idVendor')).read().strip().lower()
                    p = open(os.path.join(d, 'idProduct')).read().strip().lower()
                except OSError:
                    continue
                if v == '0bda' and p in ('2838', '2832'):
                    try:
                        found.append(open(os.path.join(d, 'product')).read().strip())
                    except OSError:
                        found.append('RTL2838')
    except Exception:
        found = []
    # con il driver di Zadig la chiavetta si chiama spesso «Bulk-In, Interface (Interface 0)»: la rinomino
    out = []
    for n in found:
        n = 'RTL-SDR' if n.lower().startswith('bulk') else n
        if 'composit' in n.lower() or n in out:          # «Dispositivo USB composito»: è la stessa chiavetta
            continue
        out.append(n)
    if found and not out:
        out = ['RTL-SDR']
    _rtl_seen.update(t=time.time(), v=out)
    return list(out)


class RtlTcpSource:
    """Chiavetta RTL-SDR (24 MHz – 1,7 GHz; con il «direct sampling» anche le onde corte) tramite rtl_tcp.

    Se il server è su questo PC e non è già acceso, FZR Skimmer avvia rtl_tcp.exe da solo (serve il driver
    WinUSB della chiavetta, quello che si installa con Zadig per SDR#). Si può usare anche una chiavetta
    su un altro computer (Raspberry, ecc.) con rtl_tcp acceso là: basta l'indirizzo.
    I campioni (8 bit) arrivano a 1,92 MS/s e si riducono a 192 kHz."""

    def __init__(self, host, port, lo_hz, on_iq, on_status, on_rate, gain=None, direct=0, bias=False,
                 rtl_agc=False, dev=0, exe=None, ppm_hw=0):
        self.host, self.port = host or '127.0.0.1', int(port or 1234)
        self.center = float(lo_hz)
        self.on_iq, self.on_status, self.on_rate = on_iq, on_status, on_rate
        self.gain, self.direct, self.bias, self.rtl_agc = gain, int(direct or 0), bool(bias), bool(rtl_agc)
        self.dev, self.exe, self.ppm_hw = int(dev or 0), exe, int(ppm_hw or 0)
        self.sock = None
        self.proc = None
        self.dec = Decimator(RTL_RATE)
        self.fs = self.dec.fs
        self.tuner = None
        self.ngains = 0
        self.alive = False
        self.lock = threading.Lock()
        self.name = 'RTL-SDR'
        self.nsamp = 0                       # campioni consegnati (il controllo dei dati ogni 3 s)
        self.out_lines = []

    def gains(self):
        """Guadagni possibili per il tuner di questa chiavetta."""
        return RTL_GAINS_TUNER.get(self.tuner or '', RTL_GAINS)

    def _local(self):
        return self.host in ('127.0.0.1', 'localhost', '::1', '')

    def _port_open(self):
        import socket
        try:
            with socket.create_connection((self.host, self.port), timeout=0.6):
                return True
        except OSError:
            return False

    def _spawn(self):
        import subprocess
        exe = self.exe or find_rtl_tcp()
        if not exe:
            raise RuntimeError('Non trovo rtl_tcp.exe: mettilo nella cartella di FZR Skimmer (o nella sottocartella rtl), '
                               'oppure avvialo a mano e scrivi l\'indirizzo')
        args = [exe, '-a', '127.0.0.1', '-p', str(self.port), '-s', str(RTL_RATE), '-d', str(self.dev)]
        self.proc = subprocess.Popen(args, cwd=os.path.dirname(exe), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     creationflags=0x08000000 if os.name == 'nt' else 0)
        self.out_lines = []

        def pump():
            for raw in iter(self.proc.stdout.readline, b''):
                t = raw.decode('utf-8', 'replace').strip()
                if t.startswith('ll+'):             # «ll+, now N»: rtl_tcp accumula, il programma non tiene il passo
                    self.ll_max = max(getattr(self, 'll_max', 0), int(re.sub(r'\D', '', t) or 0))
                    continue
                self.out_lines.append(t)
                del self.out_lines[:-30]
        threading.Thread(target=pump, daemon=True).start()
        for _ in range(40):                                     # fino a 8 s: la chiavetta si apre e rtl_tcp ascolta
            time.sleep(0.2)
            if self.proc.poll() is not None:
                break
            if any('listening' in l.lower() for l in self.out_lines):
                time.sleep(0.2)
                return
        txt = ' '.join(self.out_lines).lower()
        if 'no supported devices' in txt or 'no matching devices' in txt:
            raise RuntimeError('rtl_tcp non trova la chiavetta RTL-SDR: è collegata?')
        if 'usb_open error' in txt or 'usb_claim_interface' in txt or 'access' in txt and 'denied' in txt:
            if self.proc.poll() is None:
                self.proc.kill()
            raise RuntimeError('La chiavetta c\'è ma Windows non la lascia aprire: installa il driver WinUSB con Zadig '
                               '(come per SDR#) e chiudi gli altri programmi che la usano')
        if self.proc.poll() is not None:
            raise RuntimeError('rtl_tcp si è chiuso: ' + (self.out_lines[-1] if self.out_lines else 'nessun messaggio'))

    def _cmd(self, code, val):
        s = self.sock
        if s is None:
            return
        try:
            with self.lock:
                s.sendall(struct.pack('>BI', code, int(val) & 0xFFFFFFFF))
        except OSError:
            pass

    def _kill_stale(self):
        """Un rtl_tcp rimasto acceso (bloccato o di un avvio precedente) tiene occupata la chiavetta."""
        if os.name == 'nt':
            import subprocess
            subprocess.run(['taskkill', '/F', '/IM', 'rtl_tcp.exe'], capture_output=True, creationflags=0x08000000)
        time.sleep(1.5)                                          # la USB si libera con un attimo di ritardo

    def _connect(self):
        import socket
        self.sock = socket.create_connection((self.host, self.port), timeout=5)
        self.sock.settimeout(5)
        try:                                    # riserva grande: se il programma si ferma un attimo, i dati aspettano qui
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
        except OSError:
            pass
        hdr = b''
        while len(hdr) < 12:
            d = self.sock.recv(12 - len(hdr))
            if not d:
                raise OSError('rtl_tcp ha chiuso la connessione')
            hdr += d
        return hdr

    def start(self):
        hdr = None
        for tent in range(3):
            try:
                if self._local() and not self._port_open():
                    self._spawn()
                hdr = self._connect()
                break
            except (OSError, RuntimeError) as e:
                err = e
                try:
                    if self.sock:
                        self.sock.close()
                except OSError:
                    pass
                self.sock = None
                if not self._local():
                    raise RuntimeError(f'rtl_tcp non risponde su {self.host}:{self.port} ({e})')
                if self.proc and self.proc.poll() is None:
                    self.proc.kill()
                self.proc = None
                self._kill_stale()                              # riprovo con un rtl_tcp nuovo
        if hdr is None:
            if isinstance(err, RuntimeError):
                raise err
            raise RuntimeError(f'rtl_tcp non risponde su {self.host}:{self.port} ({err})')
        if hdr[:4] == b'RTL0':
            t, self.ngains = struct.unpack('>II', hdr[4:12])
            self.tuner = RTL_TUNERS.get(t, f'tuner {t}')
            self.name = f'RTL-SDR {self.tuner}'
        self._cmd(0x02, RTL_RATE)
        self._cmd(0x09, self.direct)
        self._cmd(0x01, int(self.center))
        if self.ppm_hw:
            self._cmd(0x05, self.ppm_hw)
        self.set_gain(self.gain)
        self._cmd(0x08, 1 if self.rtl_agc else 0)
        self._cmd(0x0e, 1 if self.bias else 0)
        self.alive = True
        self.on_rate(self.fs, self.center)
        threading.Thread(target=self._reader, daemon=True).start()
        self.on_status(f'{self.name} · {RTL_RATE/1e6:.2f} MS/s → {self.fs/1000:.0f} kHz'
                       + (' · direct sampling' if self.direct else '') + (f' · {self.host}:{self.port}' if not self._local() else ''))

    def _reader(self):
        blk = 2 * 32768                                         # ~17 ms a 1,92 MS/s
        buf = bytearray()
        s = self.sock
        while self.alive:
            try:
                d = s.recv(524288)
            except OSError:
                d = b''
            if not d:
                if self.alive:
                    self.on_status('RTL-SDR: connessione persa')
                break
            buf += d
            if len(buf) >= blk:
                n = len(buf) - (len(buf) % 2)
                u = np.frombuffer(bytes(buf[:n]), np.uint8).astype(np.float32)
                del buf[:n]
                u = (u - 127.4) * (1.0 / 128.0)
                iq = u.view(np.complex64)
                y = self.dec(iq)
                if len(y):
                    self.nsamp += len(y)
                    self.on_iq(y)
        self.alive = False

    def stop(self):
        self.alive = False
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass
        self.sock = None
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None

    def set_lo(self, hz):
        self.center = float(hz)
        self._cmd(0x01, int(hz))
        self.on_rate(self.fs, self.center)

    def set_gain(self, tenths):
        """None = automatico (AGC del tuner), altrimenti decimi di dB (il più vicino fra quelli possibili)."""
        self.gain = tenths
        if tenths is None:
            self._cmd(0x03, 0)
        else:
            self._cmd(0x03, 1)
            self._cmd(0x04, min(self.gains(), key=lambda g: abs(g - int(tenths))))

    def set_bias(self, on):
        self.bias = bool(on)
        self._cmd(0x0e, 1 if on else 0)

    def set_direct(self, mode):
        self.direct = int(mode)
        self._cmd(0x09, self.direct)

    def set_rtl_agc(self, on):
        self.rtl_agc = bool(on)
        self._cmd(0x08, 1 if on else 0)


# ---------------------------------------------------------------------------------------------- ADALM-Pluto
PLUTO_RATE = 2304000              # 2,304 MS/s ÷ 12 = 192 kHz (sotto 2,08 MS/s il chip vuole i filtri FIR)
PLUTO_RANGE = (46.875e6, 6000e6)  # sbloccato (AD9364): il PLL scende a 46,875 MHz; standard (AD9363) 325–3800 MHz


class PlutoSource:
    """ADALM-Pluto attraverso iiod, il server di libiio che gira dentro il Pluto (porta 30431).

    Funziona uguale via USB e via rete: collegato con la USB il Pluto è anche una scheda di rete
    (indirizzo 192.168.2.1, driver «PlutoSDR» di Analog Devices); in rete basta il suo indirizzo IP
    o pluto.local. Si usa il protocollo testuale di iiod: niente libiio da installare sul PC.
    Campioni a 12 bit (in 16), I e Q del canale RX, ridotti a 192 kHz."""

    PORT = 30431
    BUF = 65536                     # campioni per lettura (~28 ms)
    NBUF = 16                       # buffer nel Pluto: ~0,45 s di riserva se il PC o la rete rallentano

    def __init__(self, host, lo_hz, on_iq, on_status, on_rate, gain=None):
        self.host = (host or '192.168.2.1').strip()
        self.center = float(lo_hz)
        self.on_iq, self.on_status, self.on_rate = on_iq, on_status, on_rate
        self.gain = gain                     # None = AGC del chip (slow_attack), altrimenti dB
        self.sock = None
        self.f = None
        self.lock = threading.Lock()
        self.alive = False
        self.dec = Decimator(PLUTO_RATE)
        self.fs = self.dec.fs
        self.nsamp = 0
        self.phy = self.rx = None
        self.nch = 0
        self.name = 'ADALM-Pluto'
        self.lo_err = None

    # ---- protocollo
    def _line(self):
        b = self.f.readline()
        if not b:
            raise OSError('il Pluto ha chiuso la connessione')
        return b.decode('ascii', 'replace').strip()

    def _int(self):
        while True:
            t = self._line()
            if t:
                return int(t)

    def _cmd(self, txt, data=None):
        with self.lock:
            self.sock.sendall(txt.encode('ascii') + b'\r\n')
            if data is not None:
                self.sock.sendall(data)
            return self._int()

    def _read_attr(self, dev, ch, attr, out=False):
        with self.lock:
            self.sock.sendall(f'READ {dev} {"OUTPUT" if out else "INPUT"} {ch} {attr}\r\n'.encode('ascii'))
            n = self._int()
            if n < 0:
                return None
            return self.f.read(n + 1)[:n].decode('ascii', 'replace').strip('\x00\n ')

    def _write_attr(self, dev, ch, attr, value, out=False):
        v = str(value).encode('ascii')
        if self.sock is None:
            return -1
        try:
            return self._cmd(f'WRITE {dev} {"OUTPUT" if out else "INPUT"} {ch} {attr} {len(v)}', v)
        except OSError:
            return -1

    def _context(self):
        """Dal descrittore XML del Pluto: id del chip (ad9361-phy) e del flusso RX (cf-ad9361-lpc)."""
        import xml.etree.ElementTree as ET
        with self.lock:
            self.sock.sendall(b'PRINT\r\n')
            n = self._int()
            xml = self.f.read(n + 1)[:n]
        root = ET.fromstring(xml.split(b'?>', 1)[-1] if xml.startswith(b'<?xml') else xml)
        for d in root.iter('device'):
            nm, did = d.get('name'), d.get('id')
            if nm == 'ad9361-phy':
                self.phy = did
            elif nm == 'cf-ad9361-lpc':
                self.rx = did
                chans = [c for c in d.iter('channel') if c.get('type') == 'input']
                idx = {}
                for c in chans:
                    se = c.find('scan-element')
                    if se is not None:
                        idx[c.get('id')] = int(se.get('index'))
                self.nch = max(idx.values()) + 1 if idx else 2
                self.mask_bits = (1 << idx.get('voltage0', 0)) | (1 << idx.get('voltage1', 1))
        if not self.phy or not self.rx:
            raise RuntimeError('Collegato, ma non sembra un ADALM-Pluto (manca il chip AD9361)')

    # ---- avvio
    def start(self):
        import socket
        try:
            self.sock = socket.create_connection((self.host, self.PORT), timeout=3)
        except OSError as e:
            raise RuntimeError(f'Il Pluto non risponde su {self.host}: con la USB serve il driver «PlutoSDR» di Analog '
                               f'Devices (indirizzo 192.168.2.1), in rete scrivi il suo indirizzo ({e})')
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
        except OSError:
            pass
        self.sock.settimeout(8)
        self.f = self.sock.makefile('rb', buffering=1 << 20)
        self._context()
        p = self.phy
        self._write_attr(p, 'voltage0', 'sampling_frequency', PLUTO_RATE)
        self._write_attr(p, 'voltage0', 'rf_bandwidth', 250000)        # il filtro analogico più stretto possibile
        self._write_attr(p, 'voltage0', 'rf_port_select', 'A_BALANCED')
        self.set_lo(self.center, notify=False)
        self.set_gain(self.gain)
        words = (self.nch + 31) // 32
        mask = ''.join(f'{(self.mask_bits >> (32 * w)) & 0xFFFFFFFF:08x}' for w in reversed(range(words)))
        self.mask_words = words
        try:
            self._cmd(f'SET {self.rx} BUFFERS_COUNT {self.NBUF}')
        except OSError:
            pass
        r = self._cmd(f'OPEN {self.rx} {self.BUF} {mask}')
        if r < 0:
            raise RuntimeError(f'Il Pluto non apre il flusso dei campioni (errore {r})')
        self.alive = True
        self.on_rate(self.fs, self.center)
        self.rawq = queue.Queue(maxsize=64)
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._worker, daemon=True).start()
        self.on_status(f'{self.name} · {self.host} · {PLUTO_RATE/1e6:.3f} MS/s → {self.fs/1000:.0f} kHz')

    def _reader(self):
        nb = self.BUF * 4                                  # I e Q a 16 bit
        while self.alive:
            try:
                with self.lock:
                    self.sock.sendall(f'READBUF {self.rx} {nb}\r\n'.encode('ascii'))
                    left, mask_read, chunks = nb, False, []
                    while left > 0:
                        n = self._int()
                        if n < 0:
                            raise OSError(f'errore {n} leggendo i campioni')
                        if n == 0:
                            break
                        if not mask_read:
                            self.f.read(self.mask_words * 8 + 1)
                            mask_read = True
                        d = self.f.read(n)
                        if len(d) < n:
                            raise OSError('dati interrotti')
                        chunks.append(d)
                        left -= n
                raw = b''.join(chunks)
            except (OSError, ValueError) as e:
                if self.alive:
                    self.on_status(f'ADALM-Pluto: connessione persa ({e})')
                break
            if len(raw) < 4:
                continue
            try:
                self.rawq.put_nowait(raw)                  # il calcolo in un altro thread: subito la richiesta dopo
            except queue.Full:
                self.lost = getattr(self, 'lost', 0) + 1
        self.alive = False
        self.rawq.put(None)

    def _worker(self):
        while True:
            raw = self.rawq.get()
            if raw is None:
                return
            v = np.frombuffer(raw[:len(raw) // 4 * 4], '<i2').astype(np.float32) * (1.0 / 2048.0)
            y = self.dec(v.view(np.complex64))
            if len(y):
                self.nsamp += len(y)
                self.on_iq(y)

    def stop(self):
        self.alive = False
        s = self.sock
        if s is not None:
            try:
                with self.lock:
                    s.sendall(f'CLOSE {self.rx}\r\n'.encode('ascii'))
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass
        self.sock = None

    def set_lo(self, hz, notify=True):
        hz = float(min(PLUTO_RANGE[1], max(PLUTO_RANGE[0], hz)))
        r = self._write_attr(self.phy, 'altvoltage0', 'frequency', int(round(hz)), out=True)
        if r is not None and r < 0 and self.lo_err != int(hz):
            self.lo_err = int(hz)
            self.on_status(f'ADALM-Pluto: {hz/1e6:.3f} MHz non accettati (con il firmware standard 325–3800 MHz)')
            return
        self.center = hz
        if notify:
            self.on_rate(self.fs, self.center)

    def set_gain(self, db):
        """None = AGC del chip (slow_attack), altrimenti guadagno fisso 0–71 dB."""
        self.gain = db
        if db is None:
            self._write_attr(self.phy, 'voltage0', 'gain_control_mode', 'slow_attack')
        else:
            self._write_attr(self.phy, 'voltage0', 'gain_control_mode', 'manual')
            self._write_attr(self.phy, 'voltage0', 'hardwaregain', int(max(0, min(71, int(db)))))
