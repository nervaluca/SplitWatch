"""Sorgenti IQ per FZR Skimmer.

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

    def __init__(self, folder, lo_hz, rate, on_iq, on_status, on_rate, preamp=True, swap_iq=False):
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
            u.SetAttenuator(0)
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
                    stop_stream()
                    u.Close()
                    break
                elif c[0] == 'lo':
                    fid = tune(c[1])
                    self.on_rate(self.fs, self.center)
                    self.on_status(f'Centro {self.center/1000:.3f} kHz · preselettore {fid}')
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
