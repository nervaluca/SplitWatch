"""Sorgenti IQ per Split Watch.

PerseusExtIO : Perseus dal vivo tramite ExtIO_perseus.dll (32 bit, formato Winrad/HDSDR)
WavFile      : riproduzione in tempo reale di una registrazione IQ (CW Skimmer, HDSDR, SDR Console)

Ogni sorgente chiama on_iq(numpy.complex64) da un suo thread e
on_status(testo) per messaggi da mostrare nell'interfaccia.
"""
import os
import queue
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
class WavFile:
    """Riproduce un WAV IQ a velocità reale (in loop)."""

    def __init__(self, path, center_hz, on_iq, on_status, loop=True, speed=1.0):
        self.path = path
        self.center = float(center_hz)
        self.on_iq = on_iq
        self.on_status = on_status
        self.loop = loop
        self.speed = speed
        self._stop = threading.Event()
        self.fs, self.fmt, self.data_off, self.data_len, self.bpf = self._parse()
        self.center = self._auxi_center() or self.center
        self.duration = self.data_len / self.bpf / self.fs

    def _parse(self):
        with open(self.path, 'rb') as f:
            hdr = f.read(12)
            if hdr[:4] != b'RIFF' or hdr[8:12] != b'WAVE':
                raise ValueError('Il file non è un WAV')
            fmt = None
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
                    return fs, fmt, f.tell(), sz, bpf
                else:
                    f.seek(sz + (sz & 1), 1)

    def _auxi_center(self):
        """Frequenza centrale dal blocco 'auxi' (HDSDR/SpectraVue), se c'è."""
        try:
            with open(self.path, 'rb') as f:
                f.seek(12)
                while True:
                    ch = f.read(8)
                    if len(ch) < 8:
                        return None
                    cid, sz = ch[:4], struct.unpack('<I', ch[4:])[0]
                    if cid == b'auxi':
                        d = f.read(sz)
                        if len(d) >= 40:
                            return float(struct.unpack('<I', d[36:40])[0]) or None
                        return None
                    f.seek(sz + (sz & 1), 1)
        except Exception:
            return None

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def set_lo(self, hz):
        self.on_status('In riproduzione la frequenza centrale non si cambia')

    def _run(self):
        block = 2048
        nbytes = block * self.bpf
        with open(self.path, 'rb') as f:
            while not self._stop.is_set():
                f.seek(self.data_off)
                left = self.data_len
                t0 = time.perf_counter()
                sent = 0
                while left > 0 and not self._stop.is_set():
                    raw = f.read(min(nbytes, left))
                    if not raw:
                        break
                    left -= len(raw)
                    iq = to_complex(raw, self.fmt)
                    self.on_iq(iq)
                    sent += len(iq)
                    ahead = sent / self.fs / self.speed - (time.perf_counter() - t0)
                    if ahead > 0:
                        time.sleep(ahead)
                if not self.loop:
                    self.on_status('Fine della registrazione')
                    return
                self.on_status('Registrazione: ricomincio da capo')


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
                raise RuntimeError('ExtIO_perseus.dll è a 32 bit: serve la versione a 32 bit di Split Watch')
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
