"""FZR Skimmer - ricevitore per pile-up in split con il Perseus.

Uso:
  FZRSkimmer.exe                         Perseus dal vivo (ExtIO_perseus.dll nella stessa cartella)
  FZRSkimmer.exe registrazione.wav       riproduce una registrazione IQ
       --centro 18085.0                  frequenza centrale della registrazione in kHz (CW Skimmer non la salva)
       --dx 18085.0                      frequenza del DX in kHz
       --porta 8073                      porta dell'interfaccia web
       --cartella "C:\\HDSDR"            cartella con ExtIO_perseus.dll e perseususb.dll
       --no-browser                      non aprire il browser
L'interfaccia si apre su http://localhost:8073 (anche da tablet/telefono nella rete di casa).
"""
import os as _os
import sys as _sys


def _errore_avvio(tipo, val, tb):
    """Errore prima che il programma parta (es. un file mancante nella compilazione): lo scrive in
    fzrskimmer_errore.txt accanto all'exe ed esce, invece di restare appeso a una finestra di Windows."""
    import traceback
    txt = ''.join(traceback.format_exception(tipo, val, tb))
    try:
        if _sys.__stderr__ is not None:                 # con la console (prove, GitHub) si vede subito
            _sys.__stderr__.write(txt)
            _sys.__stderr__.flush()
    except Exception:
        pass
    try:
        base = _os.path.dirname(_sys.executable) if getattr(_sys, 'frozen', False) else _os.path.dirname(_os.path.abspath(__file__))
        with open(_os.path.join(base, 'fzrskimmer_errore.txt'), 'a', encoding='utf-8') as f:
            f.write(txt + '\n')
    except Exception:
        pass
    _os._exit(3)


_sys.excepthook = _errore_avvio
import argparse
import asyncio
import collections
import atexit
import json
import math
import os
import queue
import re
import socket
import struct
import sys
import threading
import time
import webbrowser

import numpy as np
import websockets

import dsp
import calib
import i18n
import licenza
import rig as riglib
from dsp import Engine
from rbn import RBN
from recorder import Recorder
from sources import (AudioCard, ExtIOSource, PerseusDirect, PerseusExtIO, WavFile, find_extio_dlls,
                     list_audio_inputs, wav_info, RtlTcpSource, find_rtl_dongles, find_rtl_tcp, RTL_GAINS,
                     PlutoSource, PLUTO_RANGE)
import banco
import ponti

VERSION = '1.67'
APP = 'FZR Skimmer'
TRIAL_S = 5 * 60                                   # prova senza registrazione: 5 minuti per avvio
URL_RICHIESTA_CHIAVE = 'https://www.iw1fzr.it/?p=38#registrazione'


def find_license():
    """Licenza di FZR Skimmer: solo quella registrata qui incollando la chiave (nessuna attivazione automatica,
    nemmeno prendendo quella di ADIF FZR: la chiave è la stessa, ma va incollata anche qui)."""
    for d in (DATA_DIR, app_dir()):
        info = licenza.carica(d)
        if info:
            info['origine'] = d
            return info
    return None


def app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def res_path(name):
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def _writable(d):
    try:
        os.makedirs(d, exist_ok=True)
        t = os.path.join(d, '.prova_scrittura')
        with open(t, 'w') as f:
            f.write('x')
        os.remove(t)
        return True
    except Exception:
        return False


def data_dir():
    """Cartella per impostazioni, registro e licenza: quella dell'exe se scrivibile,
    altrimenti %APPDATA%\\FZRSkimmer (come ADIF FZR)."""
    if _writable(app_dir()):
        return app_dir()
    fb = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'FZRSkimmer')
    _writable(fb)
    return fb


DATA_DIR = data_dir()
CFG_PATH = os.path.join(DATA_DIR, 'fzrskimmer.json')
OLD_CFG_PATH = os.path.join(app_dir(), 'splitwatch.json')     # impostazioni della vecchia versione Split Watch
LOG_PATH = os.path.join(DATA_DIR, 'fzrskimmer.log')


if sys.stdout is None:                      # eseguibile senza finestra nera
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')


def log(msg):
    line = time.strftime('%H:%M:%S ') + str(msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


BANDS = {  # banda: (limite basso, limite alto, centro predefinito, DX predefinito) in Hz
    '160': (1810000, 2000000, 1830000, 1824000),
    '80':  (3500000, 3800000, 3525000, 3505000),
    '60':  (5351500, 5366500, 5360000, 5352500),      # banda WRC-15: CW in basso, fonia in USB
    '40':  (7000000, 7200000, 7025000, 7005000),
    '30':  (10100000, 10150000, 10125000, 10105000),
    '20':  (14000000, 14350000, 14035000, 14025000),
    '17':  (18068000, 18168000, 18085000, 18075000),
    '15':  (21000000, 21450000, 21035000, 21025000),
    '12':  (24890000, 24990000, 24905000, 24895000),
    '10':  (28000000, 29700000, 28035000, 28025000),
    '6':   (50000000, 52000000, 50100000, 50090000),      # VHF/UHF: con la chiavetta RTL-SDR (o ExtIO a larga banda)
    '2':   (144000000, 146000000, 144100000, 144050000),
    '70':  (430000000, 440000000, 432100000, 432050000),
    '23':  (1240000000, 1300000000, 1296100000, 1296050000),
    # QO-100 (Es'hail-2), transponder a banda stretta: si riceve con un LNB (convertitore 9750 MHz)
    'QO':  (10489500000, 10490000000, 10489550000, 10489525000),   # skimmer nella fetta CW (10489,505–540)
}
CONV_QO100 = 9750000000          # oscillatore dell'LNB standard: 10489,5 MHz → 739,5 MHz
QO100_BEACONS = (10489500000, 10489750000, 10490000000)   # CW basso, PSK 400 al centro, CW alto
HF_BANDS = ('160', '80', '60', '40', '30', '20', '17', '15', '12', '10')
VHF_BANDS = ('6', '2', '70', '23')


# memorie SSB: segmenti fonia (IARU Regione 1), DX con spazio UP sopra; 30 m niente fonia
BANDS_SSB = {
    '160': (1860000, 1850000),
    '80':  (3780000, 3790000),
    '60':  (5360000, 5363500),
    'QO':  (10489800000, 10489700000),                 # QO-100: fonia 10489,650–990 (a metà il beacon PSK)
    '40':  (7150000, 7140000),
    '20':  (14220000, 14195000),
    '17':  (18140000, 18130000),
    '15':  (21300000, 21295000),
    '12':  (24950000, 24945000),
    '10':  (28500000, 28495000),
    '6':   (50160000, 50150000),
    '2':   (144310000, 144300000),
    '70':  (432210000, 432200000),
    '23':  (1296210000, 1296200000),
}
# FM e AM: frequenze di chiamata (IARU Regione 1)
BANDS_FM = {'10': 29600000, '6': 51510000, '2': 145500000, '70': 433500000, '23': 1297500000}


# Ricevitore "puro" (due RX in stereo, niente skimmer): modi e memorie di banda per ciascuno.
RX_MODES = ('rxcw', 'ssb', 'rtty', 'ft8', 'fm', 'am')
# RTTY: frequenza = mark (come gli Icom), shift 170 Hz. DIGI (modo interno 'ft8'): frequenze FT8 standard (USB-D).
BANDS_RTTY = {'160': 1838000, '80': 3590000, '40': 7040000, '30': 10142000, '20': 14083000,
              '17': 18101000, '15': 21083000, '12': 24921000, '10': 28083000}
BANDS_FT8 = {'QO': 10489540000, '160': 1840000, '80': 3573000, '60': 5357000, '40': 7074000, '30': 10136000, '20': 14074000,
             '17': 18100000, '15': 21074000, '12': 24915000, '10': 28074000, '6': 50313000, '2': 144174000, '70': 432174000}

SM_CAL_DEFAULT = -10.0      # taratura indicativa (dBm = dBFS - 10) finché non la allinei


def band_of(hz):
    for b, (lo, hi, _, _) in BANDS.items():
        if lo - 50000 <= hz <= hi + 50000:
            return b
    return None


def find_perseus_folder(cfg, explicit=None):
    """Cerca la cartella con perseususb.dll e i file .sbs: indicata, salvata, quella dell'exe,
    poi le installazioni tipiche di CW Skimmer e del software Perseus."""
    cands = [explicit, cfg.get('cartella'), app_dir()]
    for pf in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), r'C:\Program Files (x86)', r'C:\Program Files'):
        if pf:
            cands += [os.path.join(pf, 'Afreet', 'CwSkimmer'), os.path.join(pf, 'Afreet', 'SkimSrv'),
                      os.path.join(pf, 'Microtelecom', 'Perseus'), os.path.join(pf, 'Perseus'), os.path.join(pf, 'HDSDR')]
    for c in cands:
        if c and os.path.exists(os.path.join(c, 'perseususb.dll')) and os.path.exists(os.path.join(c, 'perseus96k24v31.sbs')):
            return c
    return explicit or cfg.get('cartella') or app_dir()


CALL_FILES = ('MASTER.SCP', 'master.scp', 'MASTER.DTA', 'master.dta', 'callsign.dat', 'Callsign.dat',
              'calls.txt', 'nominativi.txt')


def load_calls(extra_dirs=()):
    """Legge l'elenco dei nominativi noti. Formati: testo (un nominativo per riga, commenti #),
    MASTER.SCP (Super Check Partial) o MASTER.DTA (CW Skimmer/CT): si estraggono le parole
    che hanno la forma di un nominativo."""
    import re
    rx = re.compile(rb'(?<![A-Z0-9/])[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}[A-Z](?:/[A-Z0-9]{1,4})?(?![A-Z0-9])')
    calls, used = set(), []
    for d in [app_dir(), *[x for x in extra_dirs if x]]:
        for name in CALL_FILES:
            p = os.path.join(d, name)
            if os.path.exists(p) and p not in used:
                try:
                    with open(p, 'rb') as f:
                        data = f.read().upper()
                    data = re.sub(rb'#[^\n]*', b' ', data)
                    n0 = len(calls)
                    calls.update(m.decode() for m in rx.findall(data))
                    used.append(p)
                    log(f'Nominativi da {p}: {len(calls) - n0}')
                except Exception as e:
                    log(f'Errore leggendo {p}: {e}')
    return calls, used


def load_cfg():
    for path in (CFG_PATH, OLD_CFG_PATH):          # recupera memorie e impostazioni di FZR Skimmer
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            continue
    return {}


_save_err = [False]
_save_pend = [None]
_save_ev = threading.Event()
_save_lock = threading.Lock()


def _write_cfg(cfg):
    for _ in range(4):                           # un altro thread può cambiare le impostazioni mentre le scrivo
        try:
            txt = json.dumps(cfg, indent=1)
            break
        except RuntimeError:
            time.sleep(0.01)
    else:
        return
    try:
        tmp = CFG_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(txt)
        os.replace(tmp, CFG_PATH)                 # scrittura sicura: niente file a metà
        _save_err[0] = False
    except Exception as e:
        if not _save_err[0]:
            _save_err[0] = True
            log(f'Impossibile salvare le impostazioni in {CFG_PATH}: {e}')


def _saver():
    while True:
        _save_ev.wait()
        time.sleep(0.8)                          # raccolgo i cambi ravvicinati (rotella, trascinamento)
        _save_ev.clear()
        flush_cfg()


def save_cfg(cfg):
    """Le impostazioni si scrivono su disco poco dopo, in un thread a parte: con il banco di memorie pieno
    il file è grande, e scriverlo a ogni scatto della rotella fermava il calcolo (audio a scatti)."""
    _save_pend[0] = cfg
    if not getattr(save_cfg, 'th', None):
        save_cfg.th = threading.Thread(target=_saver, daemon=True)
        save_cfg.th.start()
    _save_ev.set()


def flush_cfg():
    """Scrive subito quello che è in attesa (all'uscita)."""
    with _save_lock:
        cfg, _save_pend[0] = _save_pend[0], None
        if cfg is not None:
            _write_cfg(cfg)


atexit.register(flush_cfg)


class Hub:
    """Collega sorgente, motore DSP e browser."""

    def __init__(self, args):
        self.args = args
        self.cfg = load_cfg()
        self.loop = None
        self.clients = set()
        self.iq_q = queue.Queue(maxsize=400)
        self.busy = 0.0                              # secondi passati nel calcolo (carico, nel log ogni 3 s)
        self.late = 0                                # blocchi buttati perché il calcolo era in ritardo
        self.cmd_q = queue.Queue()
        self.engine = None
        self.fs = None
        self.center = None
        self.hw_center = None                  # centro come lo dice il ricevitore (senza correzione)
        self.cal = None                        # misura di calibrazione in corso
        self.lk_buf, self.lk_n, self.lk_ok, self.lk_last = [], 0, False, None   # aggancio al beacon
        self.cal_busy = False
        self.sm_last = 0.0
        self.sm_hist = collections.deque(maxlen=12)      # ultimi livelli del DX (dBFS) per la taratura
        self.starting = False
        self.auto_rs = 0                       # riavvii automatici del ricevitore senza dati
        self.nodata_n = 0
        self.src_t0 = time.time()
        self.rig = None                        # radio comandata con Hamlib (rigctld)
        self.rig_state = {'ok': False}
        self.rig_last_f = None
        self.rig_models = None
        self.rig_models_exe = None
        self.dx_abs = None
        self.status = 'Avvio…'
        self.src = None
        self.src_kind = 'file' if args.file else (args.sorgente or self.cfg.get('sorgente', 'perseus'))
        self.rows_hist = collections.deque(maxlen=3000)    # ultimi 30 s di waterfall
        self.words_hist = collections.deque(maxlen=3000)
        self.listen_hist = collections.deque(maxlen=200)
        self.lock = threading.Lock()
        self.dropped = 0
        self.audio_devs = list_audio_inputs()
        # --prova: si comporta da non registrato (per provare la versione di prova); può solo accorciarla
        self.trial_s = TRIAL_S
        if getattr(args, 'prova', None) is not None:
            self.lic = None
            if args.prova > 0:
                self.trial_s = min(TRIAL_S, args.prova)
            log(f'Modo prova: licenza ignorata, ricevitore per {self.trial_s:.0f} s')
        else:
            self.lic = find_license()
        self.t_start = time.time()
        self.locked = False                              # prova scaduta: ricevitore fermo
        up = os.environ.get('USERPROFILE', '')
        calls, files = load_calls([self.cfg.get('cartella'),
                                   os.path.join(up, 'Documents', 'N1MM Logger+', 'SupportFiles'),
                                   os.path.join(up, 'OneDrive', 'Documents', 'N1MM Logger+', 'SupportFiles')])
        if not calls:
            log('Nessun elenco di nominativi (MASTER.SCP / MASTER.DTA / callsign.dat): i call non verranno verificati')
        dsp.KNOWN_CALLS.clear()
        dsp.KNOWN_CALLS.update(calls)
        dsp.build_index()
        self.calls_files = files
        self.rec_dir = os.path.join(DATA_DIR, 'registrazioni')
        self.rec = Recorder(self.rec_dir, float(self.cfg.get('memoria_min', 5)), self.on_status)
        if args.file:
            self.cfg['file_path'] = os.path.abspath(args.file)
        self.rbn = None
        self.rbn_band = []
        self.stations = {}

    # -------------------------------------------------------------- sorgente
    def on_iq(self, iq):
        try:
            self.iq_q.put_nowait(iq)
        except queue.Full:
            self.dropped += 1

    def on_status(self, s):
        self.status = s
        log(s)
        self.post_json({'type': 'status', 'text': s, 'en': i18n.en(s)})

    def on_rate(self, fs, center):
        self.cmd_q.put(('rate', float(fs), float(center)))

    def start_source(self):
        self.starting = True
        try:
            return self._start_source()
        finally:
            self.starting = False
            self.src_t0 = time.time()                # il controllo dei dati parte da qui

    def _start_source(self):
        a = self.args
        kind = 'file' if (a.file or self.src_kind == 'file') else (a.sorgente or self.cfg.get('sorgente', 'perseus'))
        if kind == 'file':
            self.src_kind = 'file'
            path = self.cfg.get('file_path')
            if not path or not os.path.isfile(path):
                files = self.list_files()
                path = files[0]['path'] if files else None
            if not path:
                raise RuntimeError('Nessuna registrazione: usa «Sfoglia…» o salva prima gli ultimi minuti')
            known = self.cfg.get('file_centri', {}).get(path)
            center = (a.centro * 1000) if a.centro else (known or 0.0)
            src = WavFile(path, center, self.on_iq, self.on_status, loop=False, on_rate=self.on_rate, on_pos=self.on_pos)
            if a.centro:
                src.center = a.centro * 1000
            self.src = src
            self.cfg['file_path'] = path
            rec = [path] + [x for x in self.cfg.get('file_recenti', []) if x != path]
            self.cfg['file_recenti'] = rec[:12]
            save_cfg(self.cfg)
            self.on_rate(src.fs, src.center)
            dx = (a.dx * 1000 if a.dx else None) or src.dx or self.cfg.get('file_dx', {}).get(path)
            if not dx or abs(dx - src.center) > src.fs / 2:
                dx = src.center
            self.cmd_q.put(('dx', float(dx)))
            src.start()
            msg = f'Riproduzione: {os.path.basename(path)} · {src.fs/1000:.1f} kHz · {src.duration/60:.1f} min'
            if not src.center:
                msg += ' · centro sconosciuto: scrivilo in «Centro kHz»'
            self.on_status(msg)
            return
        self.src_kind = kind
        self.src_t0 = time.time()
        if self.locked:
            return
        if kind == 'extio':
            path = self.cfg.get('extio_path')
            if not path or not os.path.isfile(path):
                found = self.extio_list()
                path = found[0] if found else None
            if not path:
                raise RuntimeError('Nessun plugin ExtIO trovato: installa quello del ricevitore per HDSDR '
                                   'oppure scegli il file con «Sfoglia…»')
            self.cfg['extio_path'] = path
            save_cfg(self.cfg)
            src = ExtIOSource(path, self.cfg.get('lo', 14025000), int(self.cfg.get('extio_rate', 192000)),
                              self.on_iq, self.on_status, self.on_rate)
            src.start()
            self.src = src
            return
        if kind == 'pluto':
            pc = self.cfg.get('pluto', {})
            lo = float(self.cfg.get('pluto_lo', 433540000))
            if not PLUTO_RANGE[0] <= lo <= PLUTO_RANGE[1]:
                lo = 433540000.0
            self.src = PlutoSource(pc.get('host', '192.168.2.1'), lo, self.on_iq, self.on_status, self.on_rate,
                                   gain=pc.get('gain'))
            try:
                self.src.start()
            except Exception:
                try:
                    self.src.stop()
                except Exception:
                    pass
                self.src = None
                raise
            return
        if kind == 'rtl':
            r = self.cfg.get('rtl', {})
            lo = float(self.cfg.get('rtl_lo', 145540000))
            direct = int(r.get('direct', 0))
            if not direct and lo < 24e6:
                lo = 145540000.0                          # la chiavetta non scende sotto i 24 MHz
            self.src = RtlTcpSource(r.get('host', '127.0.0.1'), int(r.get('port', 1234)), lo, self.on_iq, self.on_status,
                                    self.on_rate, gain=r.get('gain'), direct=direct, bias=bool(r.get('bias', False)),
                                    rtl_agc=bool(r.get('agc', False)), dev=int(r.get('dev', 0)), exe=r.get('exe'))
            try:
                self.src.start()
            except Exception:
                try:
                    self.src.stop()
                except Exception:
                    pass
                self.src = None
                raise
            return
        if kind in ('audio', 'iq'):
            devs = list_audio_inputs()
            want = self.cfg.get('audio_dev')
            dev = next((i for i, n, c in devs if n == want), None)
            if want and dev is None:
                self.on_status(f'Ingresso audio "{want}" non trovato: uso quello predefinito')
            rate = 48000 if kind == 'audio' else int(self.cfg.get('audio_rate', 96000))
            self.src = AudioCard(dev, kind, float(self.cfg.get('dial', 21025000)), float(self.cfg.get('audio_offset', 600)),
                                 rate, self.on_iq, self.on_status, self.on_rate,
                                 swap_iq=bool(self.cfg.get('inverti_iq', False)))
            self.src.start()
            return
        folder = find_perseus_folder(self.cfg, a.cartella)
        log(f'Cartella del Perseus: {folder}')
        if folder != app_dir():
            self.cfg['cartella'] = folder
            save_cfg(self.cfg)
        lo = self.cfg.get('lo', 21025000)
        last_err = None
        for tent in range(3):                     # la USB a volte si libera con un attimo di ritardo
            if a.extio:
                src = PerseusExtIO(folder, lo, self.on_iq, self.on_status, self.on_rate)
            else:
                src = PerseusDirect(folder, lo, int(self.cfg.get('campionamento', 96000)),
                                    self.on_iq, self.on_status, self.on_rate,
                                    preamp=bool(self.cfg.get('preamp', True)), att_db=int(self.cfg.get('att', 0)),
                                    swap_iq=bool(self.cfg.get('inverti_iq', False)))
            try:
                src.start()
                self.src = src
                return
            except Exception as e:
                last_err = e
                log(f'Avvio del Perseus, tentativo {tent + 1}/3 fallito: {e}')
                try:
                    src.stop()
                except Exception:
                    pass
                time.sleep(2)
        self.src = None
        raise RuntimeError(f'{last_err} — premi «Riavvia ricevitore» per riprovare')

    PPM_KINDS = ('perseus', 'extio', 'iq', 'rtl', 'pluto')

    def ppm(self):
        """Correzione di frequenza (ppm) del ricevitore attuale."""
        if self.src_kind not in self.PPM_KINDS:
            return 0.0
        return float(self.cfg.get('ppm', {}).get(self.src_kind, 0.0))

    def conv(self):
        """Convertitore davanti al ricevitore (LNB, transverter): frequenza vera = ricevitore + conv (Hz)."""
        if self.src_kind == 'file':
            return 0.0
        return float(self.cfg.get('conv', {}).get(self.src_kind, 0.0))

    def set_conv(self, hz):
        """Cambia il convertitore: il ricevitore resta dov'è, cambiano le frequenze mostrate."""
        old = self.conv()
        hz = float(hz or 0)
        if abs(hz) > 100e9:
            return
        self.cfg.setdefault('conv', {})[self.src_kind] = round(hz)
        save_cfg(self.cfg)
        if self.dx_abs is not None:
            self.dx_abs += hz - old
        if self.fs and self.hw_center is not None:
            self.cmd_q.put(('rate', self.fs, self.hw_center, 'cal'))
        self.on_status(f'Convertitore: {hz/1e6:.6f} MHz' if hz else 'Convertitore spento')

    def lock_step(self, blk):
        """Aggancio al beacon (QO-100): l'oscillatore dell'LNB scivola con la temperatura; ogni mezzo secondo
        misuro dove cade il beacon e correggo il convertitore, così le frequenze restano quelle vere.
        Il segnale si eleva al quadrato: la riga a 2×Δf c'è sia col beacon CW sia col PSK (che non ha portante)."""
        cv = self.conv()
        if not cv or not self.fs or self.center is None:
            return
        if time.time() < getattr(self, 'lk_hold', 0):          # dopo una correzione: i dati vecchi in coda non contano
            return
        self.lk_buf.append(blk)
        self.lk_n += len(blk)
        if self.lk_n < 0.5 * self.fs:
            return
        x = np.concatenate(self.lk_buf)[-int(0.5 * self.fs):]
        self.lk_buf, self.lk_n = [], 0
        near = [b for b in QO100_BEACONS if abs(b - self.center) < 0.36 * self.fs]
        if not near:
            self._lock_msg(False, None, 'nessun beacon nella banda ricevuta')
            return
        b = min(near, key=lambda v: abs(v - self.center))
        off = b - self.center
        # agganciato: cerco vicino; all'inizio fino a ±40 kHz (un LNB comune sbaglia anche di 30 kHz)
        win = 1000.0 if self.lk_ok else 40000.0
        dec = 2 if win <= 20000 else 1                          # al quadrato la riga va a 2×: serve banda
        n = len(x) // 2 * 2
        t = np.arange(n) / self.fs
        y = (x[:n] * np.exp(-2j * np.pi * off * t).astype(np.complex64)).reshape(-1, dec).mean(1)
        fs2 = self.fs / dec
        z = y * y
        nf = 1 << int(np.ceil(np.log2(len(z) * 2)))
        sp = np.abs(np.fft.fft(z * np.hanning(len(z)), nf)) ** 2
        f = np.fft.fftfreq(nf, 1 / fs2)
        idx = np.nonzero(np.abs(f) <= 2 * win)[0]
        k = idx[np.argmax(sp[idx])]
        snr = 10 * math.log10(sp[k] / (float(np.median(sp[idx])) + 1e-30))
        if snr < (18 if self.lk_ok else 22):                    # il rumore da solo arriva a ~10 dB
            self.lk_ok = False
            self._lock_msg(False, None, 'beacon non trovato')
            return
        a, bb, c = (math.log(sp[(k - 1) % nf] + 1e-30), math.log(sp[k] + 1e-30), math.log(sp[(k + 1) % nf] + 1e-30))
        den = a - 2 * bb + c
        d = 0.5 * (a - c) / den if den != 0 else 0.0
        delta = (f[k] + d * fs2 / nf) / 2                       # il beacon appare spostato di delta
        self.lk_ok = True
        corr = delta
        if abs(corr) > 2.0 and self.hw_center is not None and self.tunable():
            # si risintonizza il ricevitore per compensare l'LNB (e si aggiorna la sua stima): il centro mostrato,
            # il DX, i filtri e la decodifica non si toccano, quindi niente buchi nell'audio
            k = 1 + self.ppm() * 1e-6
            hw = self.hw_center + corr / k
            self.cfg.setdefault('conv', {})[self.src_kind] = round(cv - corr)
            save_cfg(self.cfg)
            self.hw_center = hw
            try:
                self.src.set_lo(hw, notify=False)
            except TypeError:
                pass
            # il ricevitore ha in coda fino a ~0,5 s di campioni presi prima della correzione: aspetto
            self.lk_hold = time.time() + 1.2
            self.lk_buf, self.lk_n = [], 0
        self._lock_msg(True, delta, b)

    def _lock_msg(self, ok, delta, b):
        m = {'type': 'lock', 'ok': ok, 'hz': None if delta is None else round(delta, 1),
             'beacon': b if isinstance(b, int) else None, 'why': b if isinstance(b, str) else '',
             'conv': self.conv()}
        now = time.time()
        if m != self.lk_last or now - getattr(self, 'lk_sent', 0) > 3:   # ogni 3 s anche se uguale (pagine nuove)
            self.lk_last, self.lk_sent = m, now
            self.post_json(m)

    def tune(self, center):
        """Porta il centro (frequenza vera) a center: il ricevitore riceve il valore corretto."""
        if self.src:
            self.src.set_lo(int(round((float(center) - self.conv()) / (1 + self.ppm() * 1e-6))))

    def set_ppm(self, v):
        v = max(-200.0, min(200.0, float(v)))
        self.cfg.setdefault('ppm', {})[self.src_kind] = round(v, 3)
        save_cfg(self.cfg)
        if self.fs and self.hw_center is not None:
            self.cmd_q.put(('rate', self.fs, self.hw_center, 'cal'))
        self.on_status(f'Correzione frequenza: {v:+.3f} ppm ({v * 14:+.1f} Hz a 14 MHz)')

    def calibrate(self, khz):
        """Misura una portante campione: sintonizza, ascolta 6 s, torna dove eri, propone la correzione."""
        if self.cal_busy:
            return
        if self.src_kind not in self.PPM_KINDS or not self.src or not self.fs:
            self.post_json({'type': 'cal', 'ok': False, 'why': 'serve il ricevitore acceso (Perseus, ExtIO o IQ)'})
            return
        true_hz = float(khz) * 1000
        self.cal_busy = True

        def run():
            prev_hw, prev_dx = self.hw_center, self.dx_abs
            tunable = self.tunable()
            k = 1 + self.ppm() * 1e-6
            cv = self.conv()
            true_rx = true_hz - cv                          # col convertitore: la frequenza al ricevitore
            try:
                if tunable:
                    # chiavetta: la portante può cadere ±20 kHz più in là, lontano dal picco al centro
                    off = 20000 if cv else 40000 if self.src_kind in ('rtl', 'pluto') else calib.OFFSET
                    target = int(round(true_rx / k - off))
                    self.src.set_lo(target)
                    t0 = time.time()
                    while abs((self.hw_center or 0) - target) > 2 and time.time() - t0 < 6:
                        time.sleep(0.05)
                    if abs((self.hw_center or 0) - target) > 2:
                        raise RuntimeError('il ricevitore non si è sintonizzato')
                elif abs(true_rx / k - self.hw_center) > self.fs * 0.45:
                    raise RuntimeError(f'sintonizza il ricevitore in modo che {khz:g} kHz sia nella banda ricevuta')
                self.cmd_q.put(('dx', true_hz, 'cal'))     # la portante finisce nell'audio: si sente il test (la radio no)
                time.sleep(0.8)                            # lascia assestare il ricevitore
                self.post_json({'type': 'cal', 'busy': True, 'khz': khz})
                # LNB: l'errore va da pochi Hz (GPS) a decine di kHz; i beacon di QO-100 sono manipolati
                m = calib.Measure(self.fs, self.hw_center, true_rx, self.ppm(),
                                  span_ppm=150.0 if self.src_kind in ('rtl', 'pluto') else 10.0,
                                  span_hz=60000.0 if cv else None, min_steady=0.3 if cv else 0.85)
                self.cal = m
                t0 = time.time()
                while not m.done and time.time() - t0 < calib.SECS + 6:
                    time.sleep(0.1)
                self.cal = None
                if not m.done:
                    raise RuntimeError('dati insufficienti dal ricevitore')
                r = m.result()
                r['type'] = 'cal'
                if cv:
                    # col convertitore l'errore è quasi tutto suo (quarzo dell'LNB): si corregge lì, non i ppm
                    r['conv'] = cv
                    r['khz'] = khz
                log(f'Calibrazione {khz:g} kHz: {r}')
                self.post_json(r)
            except Exception as e:
                self.post_json({'type': 'cal', 'ok': False, 'why': str(e)})
            finally:
                self.cal = None
                if tunable and prev_hw:
                    self.src.set_lo(int(prev_hw))
                    t0 = time.time()
                    while abs((self.hw_center or 0) - prev_hw) > 2 and time.time() - t0 < 6:
                        time.sleep(0.05)
                if prev_dx:
                    self.cmd_q.put(('dx', float(prev_dx), 'cal'))
                # la decodifica ha visto 6 s di un'altra banda: riparte pulita, come dopo un riavvio
                if self.fs and self.hw_center is not None:
                    self.cmd_q.put(('rate', self.fs, self.hw_center, 'reset'))
                self.cal_busy = False
        threading.Thread(target=run, daemon=True).start()

    def extio_list(self):
        extra = [app_dir(), DATA_DIR, os.path.dirname(self.cfg.get('extio_path') or '') or None]
        return find_extio_dlls([d for d in extra if d])

    def pick_extio(self):
        """Finestra di Windows per scegliere il plugin ExtIO_*.dll."""
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
            p = filedialog.askopenfilename(parent=root, title='Plugin ExtIO del ricevitore (32 bit, quello per HDSDR)',
                                           filetypes=[('Plugin ExtIO', 'ExtIO*.dll'), ('DLL', '*.dll')])
            root.destroy()
            return os.path.abspath(p) if p else None
        except Exception as e:
            self.on_status(f'Impossibile aprire la finestra dei file: {e}')
            return None

    def trial_left(self):
        if self.lic or self.src_kind == 'file':
            return None
        return max(0, self.trial_s - (time.time() - self.t_start))

    def trial_thread(self):
        while True:
            time.sleep(1)
            left = self.trial_left()
            if left is None or self.locked:
                continue
            if left <= 0:
                self.locked = True
                self.rec.stop_rec()
                src, self.src = self.src, None
                if src:
                    try:
                        src.stop()
                    except Exception:
                        pass
                log('Prova terminata: ricevitore fermato')
                self.on_status('Prova di 5 minuti terminata: registra FZR Skimmer per continuare')
                self.post_json(self.cfg_msg())

    def register(self, call, key):
        ok, info = licenza.salva(DATA_DIR, call, key)
        if not ok:
            return False, info
        info['origine'] = DATA_DIR
        self.lic = info
        try:
            self.rbn_apply()
        except Exception:
            pass
        log(f"Registrazione: {info['testo']}")
        if self.locked:
            self.locked = False
            self.switch_source(self.src_kind if self.src_kind in ('perseus', 'audio', 'iq', 'extio', 'rtl', 'pluto') else 'perseus')
        self.post_json(self.cfg_msg())
        return True, info['testo']

    def switch_source(self, kind, dev=None):
        """Cambia ricevitore senza riavviare il programma."""
        if self.locked and kind != 'file':
            self.on_status('Prova terminata: registra FZR Skimmer per riattivare il ricevitore')
            return

        def run():
            if kind == 'file':
                self.rec.stop_rec()
            elif self.src_kind == 'file':
                d = self.cfg.get('dx')                    # torno al ricevitore: riprendo il DX di prima
                if d:
                    self.dx_abs = float(d)
            old, self.src = self.src, None
            if old:
                try:
                    old.stop()
                except Exception:
                    pass
                time.sleep(0.8)
            if kind != 'file':
                self.cfg['sorgente'] = kind
                self.args.sorgente = kind
            self.args.file = None
            self.src_kind = kind
            if dev is not None:
                self.cfg['audio_dev'] = dev
            save_cfg(self.cfg)
            with self.iq_q.mutex:
                self.iq_q.queue.clear()
            try:
                self.start_source()
            except Exception as e:
                self.on_status(f'Errore: {e}')
            self.post_json(self.cfg_msg())
        threading.Thread(target=run, daemon=True).start()

    # -------------------------------------------------------------- elenco stazioni (contest)
    def update_stations(self, words):
        now = time.time()
        own = self.rbn_call()
        for w in words:
            ch = w.get('ch')
            if ch is None:
                continue
            st = self.stations.get(ch)
            word = w.get('s') or w['w']
            if word in ('CQ', 'TEST') or (st is None and word == 'CQ'):
                st = self.stations.setdefault(ch, {'call': None, 'n': 0})
                st['cq_t'] = now
            elif dsp.CALL_RE.match(word) and word != own and (
                    w.get('v') or w.get('cc') or w.get('rbn') or w.get('s') or w.get('m') == 'rtty'
                    or (st is not None and now - st.get('cq_t', -1e9) < 6)):
                # il nominativo della stazione è quello che ripete di più sul suo canale (non il mio,
                # che compare quando mi risponde, né quello di chi la chiama)
                st = self.stations.setdefault(ch, {'call': None, 'n': 0})
                cnt = st.setdefault('cnt', {})
                cnt[word] = cnt.get(word, 0) + 1
                best = max(cnt, key=cnt.get)
                st['call'], st['n'] = best, cnt[best]
            if st is not None:
                st.update({'fa': w['fa'], 't': now, 'wpm': w.get('wpm'), 'db': w.get('db'), 'dx': w.get('dx', False),
                           'ok': bool(w.get('v') or w.get('rbn'))})
        for k in [k for k, v in self.stations.items() if now - v.get('t', now) > 180]:
            del self.stations[k]

    def stations_msg(self):
        now = time.time()
        out = []
        for k, v in self.stations.items():
            if not v.get('call'):
                continue
            out.append({'call': v['call'], 'fa': v['fa'], 'age': round(now - v['t']), 'wpm': v.get('wpm'), 'db': v.get('db'),
                        'cq': now - v.get('cq_t', -1e9) < 120, 'n': v['n'], 'ok': v.get('ok', False), 'src': 'rx'})
        have = {o['call'] for o in out}
        for sp in self.rbn_band:
            if sp['call'] not in have:
                out.append({'call': sp['call'], 'fa': round(sp['f'], 1), 'age': sp['age'], 'wpm': sp['wpm'], 'db': sp['snr'],
                            'cq': sp.get('tipo') == 'CQ', 'n': sp['n'], 'ok': True, 'src': 'rbn'})
            else:
                for o in out:
                    if o['call'] == sp['call']:
                        o['ok'] = True
                        o['rbn'] = True
        out.sort(key=lambda o: o['fa'])
        return {'type': 'stations', 'list': out}

    def stations_thread(self):
        while True:
            time.sleep(1)
            try:
                if self.clients:
                    self.post_json(self.stations_msg())
            except Exception:
                pass

    # -------------------------------------------------------------- Reverse Beacon Network
    def rbn_match(self, word, fa, band):
        r = self.rbn.match(word, fa, band) if self.rbn else None
        return r

    def rbn_call(self):
        return ((self.lic or {}).get('call') or self.cfg.get('nominativo') or '').strip().upper()

    def rbn_apply(self):
        """Accende o spegne il collegamento all'RBN secondo le impostazioni."""
        want = bool(self.cfg.get('rbn', True)) and bool(self.rbn_call())
        if self.rbn and (not want or self.rbn.call != self.rbn_call()):
            self.rbn.stop()
            self.rbn = None
        if want and not self.rbn:
            self.rbn = RBN(self.rbn_call(), log)
            self.rbn.start()

    def rbn_state(self):
        if not self.cfg.get('rbn', True):
            return 'spento'
        if not self.rbn_call():
            return 'serve il nominativo'
        return self.rbn.state if self.rbn else 'spento'

    def rbn_thread(self):
        while True:
            time.sleep(3)
            try:
                if not self.rbn or not self.center or not self.fs:
                    self.rbn_band = []
                    continue
                band = self.rbn.in_band(self.center - self.fs / 2, self.center + self.fs / 2)
                self.rbn_band = band
                self.post_json({'type': 'spots', 'state': self.rbn_state(),
                                'spots': [{'call': x['call'], 'fa': round(x['f'], 1), 'snr': x['snr'], 'wpm': x['wpm'],
                                           'n': x['n'], 'age': x['age'], 'tipo': x['tipo']} for x in band]})
            except Exception:
                pass

    # -------------------------------------------------------------- registrazioni
    def on_pos(self, src):
        self.post_json({'type': 'play', **self.file_state(src)})

    def file_state(self, src=None):
        src = src or self.src
        if not isinstance(src, WavFile):
            return None
        return {'name': os.path.basename(src.path), 'path': src.path, 'pos': round(src.pos, 2),
                'dur': round(src.duration, 2), 'paused': src.paused, 'centro_ok': bool(src.center)}

    def post_rec(self):
        self.post_json({'type': 'rec', 'buf': round(self.rec.buffered_s(), 1), 'mem_min': self.rec.minutes,
                        'mb': round(self.rec.mem_mb()), 'rec': self.rec.rec_info(), 'saving': self.rec.saving})

    def rec_thread(self):
        while True:
            time.sleep(1)
            try:
                self.post_rec()
            except Exception:
                pass

    def list_files(self):
        paths = []
        try:
            for n in os.listdir(self.rec_dir):
                if n.lower().endswith('.wav'):
                    paths.append(os.path.join(self.rec_dir, n))
        except Exception:
            pass
        for x in self.cfg.get('file_recenti', []):
            if x not in paths and os.path.isfile(x):
                paths.append(x)
        out = []
        for x in sorted(set(paths), key=lambda q: os.path.getmtime(q) if os.path.exists(q) else 0, reverse=True)[:40]:
            if self.rec.writer and os.path.abspath(x) == os.path.abspath(self.rec.writer.path):
                continue                               # quella che sto ancora scrivendo
            try:
                i = wav_info(x)
                i['centro'] = i['centro'] or self.cfg.get('file_centri', {}).get(x)
                i['mtime'] = os.path.getmtime(x)
                out.append(i)
            except Exception:
                pass
        return out

    def pick_file(self):
        """Finestra di Windows per scegliere un WAV (si apre sul PC, non sul tablet)."""
        try:
            import tkinter as tk
            from tkinter import filedialog
        except Exception:
            self.on_status(f'Metti i file .wav in {self.rec_dir} e sceglili dall\'elenco')
            return None
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            start = os.path.dirname(self.cfg.get('file_path') or '') or self.rec_dir
            p = filedialog.askopenfilename(parent=root, title='Apri una registrazione IQ', initialdir=start,
                                           filetypes=[('Registrazioni IQ', '*.wav'), ('Tutti i file', '*.*')])
            root.destroy()
            return os.path.abspath(p) if p else None
        except Exception as e:
            self.on_status(f'Impossibile aprire la finestra dei file: {e}')
            return None

    def pick_list(self):
        """Finestra di Windows per scegliere l'elenco dei ponti (IK2ANE .xls) o un CSV di CHIRP."""
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            start = os.path.dirname(self.cfg.get('ponti_path') or '') or os.path.join(os.path.expanduser('~'), 'Downloads')
            p = filedialog.askopenfilename(parent=root, title='Elenco ponti (pontixls.xls di IK2ANE) o CSV di CHIRP',
                                           initialdir=start, filetypes=[('Elenco ponti o CHIRP', '*.xls *.csv'),
                                                                        ('Tutti i file', '*.*')])
            root.destroy()
            if p:
                self.cfg['ponti_path'] = os.path.abspath(p)
                save_cfg(self.cfg)
            return os.path.abspath(p) if p else None
        except Exception as e:
            self.on_status(f'Impossibile aprire la finestra dei file: {e}')
            return None

    # -------------------------------------------------------------- DSP
    def dsp_thread(self):
        while True:
            try:
                self._dsp_loop()
            except Exception:
                import traceback
                log('ERRORE nel calcolo:\n' + traceback.format_exc())
                self.post_json({'type': 'status', 'text': 'Errore nel calcolo: vedi fzrskimmer.log',
                                'en': 'Processing error: see fzrskimmer.log'})
                time.sleep(1)

    def diag_thread(self):
        last = 0
        while True:
            time.sleep(3)
            n = getattr(self.src, 'nsamp', None)
            if n is None:
                if self.src is None:
                    self._watchdog(0)                # avvio fallito: riprovo anche qui
                continue
            if n < last:                     # sorgente nuova: il contatore riparte da zero
                last = 0
            rate = (n - last) / 3
            last = n
            q = self.iq_q.qsize()
            busy, self.busy = self.busy, 0.0
            msg = (f'dati: {rate/1000:.1f} k campioni/s · coda {q} · persi {self.dropped} · calcolo {busy / 3 * 100:.0f}%'
                   f' · motore {"sì" if self.engine else "no"}')
            if self.src_kind in ('rtl', 'pluto'):
                msg += f' · in ritardo {self.late}'
                if getattr(self.src, 'lost', 0):
                    msg += f' · blocchi persi dal Pluto {self.src.lost}'
                if self.engine is not None and getattr(self.engine, 'tx_on', False):
                    msg += ' · TX rilevato (audio muto)'
                ll = getattr(self.src, 'll_max', 0)
                if ll:
                    msg += f' · rtl_tcp in coda {ll}'
                    self.src.ll_max = 0
            nodata = 'Nessun dato dal Perseus' if self.src_kind == 'perseus' else 'Nessun dato dal ricevitore'
            if rate == 0:
                msg = nodata + ' · ' + msg
            log(msg)
            txt = (self.status if rate else nodata) + ' · ' + f'{rate/1000:.0f} k/s'
            self.post_json({'type': 'status', 'text': txt, 'en': i18n.en(txt)})
            self._watchdog(rate)

    def _watchdog(self, rate):
        """Il Perseus (o il ricevitore ExtIO) non manda niente: lo chiudo e riapro da solo, come
        «Riavvia ricevitore». Capita al primo avvio dopo un aggiornamento o se la USB è rimasta a metà."""
        if rate > 0:
            self.nodata_n = 0
            self.auto_rs = 0
            return
        if (self.src_kind not in ('perseus', 'extio', 'rtl', 'pluto') or self.locked or self.cal_busy or self.starting
                or time.time() - self.src_t0 < 8 or self.auto_rs >= 3):
            return
        self.nodata_n += 1
        if self.nodata_n < 2:                       # 6 s di fila senza dati
            return
        self.nodata_n = 0
        self.auto_rs += 1
        log(f'Nessun dato: riavvio automatico del ricevitore ({self.auto_rs}/3)')
        self.on_status(f'Il ricevitore non manda dati: lo riavvio da solo ({self.auto_rs}/3)…')
        self.switch_source(self.src_kind)

    def _dsp_loop(self):
        last_send = time.perf_counter()
        pend_rows, pend_audio, pend_words, pend_listen = [], [], [], []
        while True:
            # comandi
            try:
                while True:
                    c = self.cmd_q.get_nowait()
                    self._apply(c)
            except queue.Empty:
                pass
            try:
                iq = self.iq_q.get(timeout=0.05)
            except queue.Empty:
                iq = None
            if iq is not None and self.engine is not None:
                chunks = [iq]
                try:
                    while len(chunks) < 64:
                        chunks.append(self.iq_q.get_nowait())
                except queue.Empty:
                    pass
                if self.src_kind in ('rtl', 'pluto') and self.fs:
                    # in ritardo di più di 1,5 s (PC occupato): butto il vecchio, così l'ascolto resta in tempo reale
                    back = sum(len(c) for c in chunks) + self.iq_q.qsize() * len(iq)
                    if back > 1.5 * self.fs:
                        with self.iq_q.mutex:
                            nq = len(self.iq_q.queue)
                            self.iq_q.queue.clear()
                        self.late += nq + len(chunks) - 1
                        chunks = chunks[-1:]
                t0 = time.perf_counter()
                blk = np.concatenate(chunks)
                if self.src_kind != 'file':
                    self.rec.push(blk)
                m = self.cal
                if m is not None:
                    m.feed(blk)
                elif self.cfg.get('beacon_lock') and self.src_kind != 'file':
                    self.lock_step(blk)
                # il riconoscimento del proprio TX (tutta la banda sale di colpo) vale per i ricevitori HF;
                # con chiavetta e Pluto un cambio di guadagno lo farebbe scattare e zittirebbe l'audio
                self.engine.tx_guard_on = self.src_kind not in ('rtl', 'pluto')
                self.engine.feed(blk)
                self.busy += time.perf_counter() - t0
                r, au, w, l = self.engine.take()
                pend_rows += r
                pend_audio += au
                pend_words += w
                pend_listen += l
            now = time.perf_counter()
            if now - self.sm_last >= 0.12 and self.engine is not None:
                self.sm_last = now
                self.send_smeter()
            if now - last_send >= 0.05 and self.engine is not None:
                last_send = now
                if pend_rows:
                    self._send_rows(pend_rows)
                    pend_rows = []
                if pend_audio:
                    a = np.concatenate(pend_audio)
                    st = self.engine is not None and self.engine.ssb
                    self.post_bin((b'\x03' if st else b'\x02') + struct.pack('<d', 0.0) + a.tobytes())
                    pend_audio = []
                if pend_words or pend_listen:
                    band = self.rbn_band
                    for w in pend_words:
                        w['fa'] = round(self.center + w['f'], 1)
                        if w.get('m') == 'rtty':
                            # RTTY: niente RBN (solo CW) né correzioni pensate per gli errori del CW
                            if dsp.is_known(w['w']):
                                w['v'] = 1
                            continue
                        hit = None
                        if band:
                            for v in dsp.variants(w['w']):
                                hit = self.rbn_match(v, w['fa'], band)
                                if hit:
                                    break
                        if hit:
                            w['rbn'] = 1
                            if hit[1] == 'v':
                                if hit[0] != w['w']:
                                    w['raw'] = w['w']
                                    w['w'] = hit[0]
                                w['v'] = 1
                            else:
                                w['s'] = hit[0]
                            continue
                        if w.get('s') or ('cc' in w and w['w'] == w['cc']):
                            if dsp.is_known(w['w']):
                                w['v'] = 1
                            continue                    # già deciso dal consenso sul canale
                        word, kind = dsp.classify(w['w'])
                        if kind == 'v':
                            if word != w['w']:
                                w['raw'] = w['w']
                                w['w'] = word
                            w['v'] = 1
                        elif kind == 's':
                            w['s'] = word
                    for l in pend_listen:
                        l['fa'] = round(self.center + l['f'], 1)
                        l['t'] = float(l['t'])
                        l['f'] = float(l['f'])
                    self.update_stations(pend_words)
                    self.words_hist.extend(pend_words)
                    self.listen_hist.extend(pend_listen)
                    self.post_json({'type': 'ev', 'words': pend_words, 'listen': pend_listen})
                    pend_words, pend_listen = [], []

    def _send_rows(self, rows):
        t0 = rows[0][0]
        nb = len(rows[0][1])
        body = b''.join(r[1].tobytes() for r in rows)
        msg = b'\x01' + struct.pack('<dII', float(t0), len(rows), nb) + body
        with self.lock:
            for r in rows:
                self.rows_hist.append(r)
        self.post_bin(msg)

    def _apply(self, c):
        if c[0] == 'rate':
            fs, hw = c[1], c[2]
            self.hw_center = hw
            center = hw * (1 + self.ppm() * 1e-6) + self.conv()    # quarzo del ricevitore e convertitore (LNB)
            if not (len(c) > 3 and c[3] == 'lock'):          # le correzioni del beacon non riempiono il registro
                log(f'campionamento {fs:.0f} Hz, centro {center:.0f} Hz' + (f' (ricevitore {hw:.0f}, {self.ppm():+.3f} ppm)' if self.ppm() else ''))
            if fs <= 0:
                self.on_status('Campionamento sconosciuto: attendo i dati…')
                return
            if len(c) > 3 and c[3] == 'reset':
                self.engine = None                          # decodifica da zero (dopo la calibrazione)
            elif len(c) > 3 and self.engine is not None and fs == self.fs:     # cambiata solo la correzione
                self.center = center
                self.engine.set_dx(self.dx_abs - center)
                self.ssb_apply()
                if self.src_kind != 'file':
                    self.rec.set_format(fs, center)
                self.post_json(self.cfg_msg())
                return
            if self.dx_abs is None:
                d = self.args.dx * 1000 if self.args.dx else self.cfg.get('dx')
                self.dx_abs = float(d) if d else center
            self.sm_floor = None                             # S-meter della chiavetta: nuovo fondo
            if self.src_kind != 'file' and abs(self.dx_abs - center) > fs / 2:
                self.dx_abs = round(center - fs * 0.2, -2)       # altro ricevitore / altra gamma: DX dentro la vista
            changed = (self.fs != fs)
            self.fs, self.center = fs, center
            if self.src_kind != 'file':
                self.rec.set_format(fs, center)
            elif isinstance(self.src, WavFile) and center:
                self.cfg.setdefault('file_centri', {})[self.src.path] = center
            if changed or self.engine is None:
                self.engine = Engine(fs, self.dx_abs - center)
                up = self.cfg.get('up', [0.15, 10.0])
                self.engine.up_lo, self.engine.up_hi = up[0] * 1000, up[1] * 1000
                self.engine.set_bw(float(self.cfg.get('filtro', 500)))
                self.engine.min_snr = float(self.cfg.get('sensibilita', 5.0))
                self.engine.listen_on = self.cfg.get('modo', 'dx') == 'dx'
                self.engine.set_agc(self.cfg.get('agc', 'slow'), float(self.cfg.get('rfgain', 60)))
                self.nrnb_apply()
            else:
                self.engine.set_dx(self.dx_abs - center)
            self.ssb_apply()
            with self.lock:
                self.rows_hist.clear()
            self.words_hist.clear()
            self.stations.clear()
            self.listen_hist.clear()
            if self.src_kind in ('perseus', 'extio'):
                self.cfg['lo'] = int(round(hw))
                save_cfg(self.cfg)
            elif self.src_kind == 'rtl':
                self.cfg['rtl_lo'] = int(round(hw))
                save_cfg(self.cfg)
            elif self.src_kind == 'pluto':
                self.cfg['pluto_lo'] = int(round(hw))
                save_cfg(self.cfg)
            elif self.src_kind in ('audio', 'iq') and isinstance(self.src, AudioCard):
                self.cfg['dial'] = self.src.dial
                save_cfg(self.cfg)
            self.post_json(self.cfg_msg())
        elif c[0] == 'dx':
            old = self.dx_abs
            self.dx_abs = float(c[1])
            why = c[2] if len(c) > 2 else None
            if (self.is_ssb() and old and why in (None, 'radio') and abs(self.dx_abs - old) < 30000):
                # SSB: si sposta solo il DX, RX2 resta dov'è (lo scostamento cambia)
                self.cfg['rx2_off'] = round(float(self.cfg.get('rx2_off', 5000.0)) - (self.dx_abs - old))
            if why in (None, 'band'):                   # deciso in FZR Skimmer (non dalla manopola, non dalla calibrazione)
                self.rig_follow_dx()
            if self.engine:
                self.engine.set_dx(self.dx_abs - self.center)
                self.ssb_apply()
            if self.src_kind == 'file':
                if isinstance(self.src, WavFile):
                    fd = self.cfg.setdefault('file_dx', {})
                    fd[self.src.path] = self.dx_abs
                    if len(fd) > 40:
                        fd.pop(next(iter(fd)))
            else:
                self.cfg['dx'] = self.dx_abs
                self.rec.set_dx(self.dx_abs)
            save_cfg(self.cfg)
            self.post_json(self.cfg_msg())
        elif c[0] == 'rx2':
            self.cfg['rx2_off'] = max(-200000.0, min(200000.0, round(float(c[1]))))
            save_cfg(self.cfg)
            self.ssb_apply()
            if self.is_ssb() and self.cfg.get('split', True) and self.cat().get('tx_rx2', True):
                self.rig_tx(self.dx_abs + self.cfg['rx2_off'])     # SSB: il TX della radio segue RX2
            self.post_json(self.cfg_msg())
        elif c[0] == 'agc':
            if self.engine:
                self.engine.set_agc(self.cfg.get('agc', 'slow'), float(self.cfg.get('rfgain', 60)))
                self.nrnb_apply()
        elif c[0] == 'ptt':
            if self.engine:
                self.engine.set_ptt(c[1])
        elif c[0] == 'ssbcfg':
            self.ssb_apply()
            self.post_json(self.cfg_msg())
        elif c[0] == 'bw':
            self.cfg['filtro'] = float(c[1])
            save_cfg(self.cfg)
            if self.engine:
                self.engine.set_bw(float(c[1]))
                self.ssb_apply()                        # RX CW: il filtro vale anche per i due ricevitori
            self.post_json(self.cfg_msg())
        elif c[0] == 'snr':
            self.cfg['sensibilita'] = float(c[1])
            save_cfg(self.cfg)
            if self.engine:
                self.engine.min_snr = float(c[1])
            self.post_json(self.cfg_msg())
        elif c[0] == 'up':
            lo, hi = sorted([float(c[1]), float(c[2])])
            if self.engine:
                self.engine.up_lo, self.engine.up_hi = lo * 1000, hi * 1000
            self.cfg['up'] = [lo, hi]
            save_cfg(self.cfg)
            self.post_json(self.cfg_msg())

    # -------------------------------------------------------------- S-meter
    def sm_att(self):
        """L'attenuatore del Perseus abbassa il segnale: lo S-meter lo rimette (legge il segnale in antenna)."""
        return int(self.cfg.get('att', 0)) if self.src_kind == 'perseus' else 0

    def sm_cal(self):
        """dBm = dBFS + taratura; None = non tarato (si usa un valore indicativo)."""
        return self.cfg.get('sm_cal', {}).get(self.src_kind)

    def send_smeter(self):
        dx, rx2 = self.engine.take_smeter()
        if dx is not None:
            self.sm_hist.append(dx)
        cal = self.sm_cal()
        c = (SM_CAL_DEFAULT if cal is None else cal) + self.sm_att()
        if cal is None and self.src_kind in ('rtl', 'pluto'):
            a = self.sm_auto(dx, rx2)                  # chiavetta non tarata: riferimento al rumore di fondo
            if a is not None:
                c = a
        self.post_json({'type': 'sm', 'dx': None if dx is None else round(dx + c, 1),
                        'rx2': None if rx2 is None else round(rx2 + c, 1), 'tarato': cal is not None,
                        'sq': self.engine.sq_state()})

    def sm_bw(self):
        """Larghezza di banda del ricevitore in uso (Hz), per il rumore di fondo atteso."""
        m = self.modo()
        if m == 'fm':
            return float(self.cfg.get('fm_bw', 12000))
        if m == 'am':
            return float(self.cfg.get('am_bw', 8000))
        if m in ('ssb', 'ft8'):
            return float(self.cfg.get('ssb_bw', 2700)) if m == 'ssb' else 3000.0
        if m == 'rtty':
            return 600.0
        return float(self.cfg.get('filtro', 500))

    def sm_auto(self, dx, rx2):
        """Taratura indicativa per la chiavetta (il livello dei suoi campioni dipende dal guadagno): il rumore
        di fondo del canale vale quello di un buon ricevitore, -174 dBm/Hz + larghezza + 6 dB di cifra di rumore.
        Il fondo si segue da solo: scende subito, risale piano (un segnale non lo sposta)."""
        v = [x for x in (dx, rx2) if x is not None]
        if not v:
            return None
        lo = min(v)
        f = getattr(self, 'sm_floor', None)
        if f is None or lo < f:
            f = lo if f is None else f + (lo - f) * 0.3
        else:
            f += 0.02                                    # ~0,15 dB al secondo
        self.sm_floor = f
        return -174.0 + 10 * math.log10(max(100.0, self.sm_bw())) + 6.0 - f

    def sm_set(self, dbm):
        """Il segnale del DX adesso vale dbm: ricavo la taratura dagli ultimi livelli misurati."""
        if self.src_kind == 'file' or len(self.sm_hist) < 4:
            self.on_status('S-meter: serve il ricevitore acceso con un segnale stabile sul DX')
            return
        v = sorted(self.sm_hist)[len(self.sm_hist) // 2]
        self.cfg.setdefault('sm_cal', {})[self.src_kind] = round(dbm - v - self.sm_att(), 1)
        save_cfg(self.cfg)
        self.on_status(f'S-meter tarato: {dbm:.0f} dBm')
        self.post_json(self.cfg_msg())

    def sm_mark(self):
        """Passo 1 della taratura con la radio: misuro il segnale del DX col ricevitore (relè sul Perseus)."""
        if self.src_kind == 'file' or len(self.sm_hist) < 6:
            self.on_status('S-meter: serve il ricevitore acceso con un segnale stabile sul DX')
            return
        h = sorted(self.sm_hist)
        self.sm_marked = (h[len(h) // 2], float(self.dx_abs or 0), time.time())
        self.on_status(f'S-meter: misura fatta sul ricevitore ({h[len(h) // 2]:.1f} dBFS). Ora passa l\'antenna '
                       f'alla radio e premi «2 · Leggi la radio»')
        self.post_json({'type': 'sm_step', 'step': 1})

    def sm_radio(self):
        """Passo 2: leggo lo S-meter della radio (Hamlib) sullo stesso segnale. Se c'è la misura del passo 1
        uso quella (relè: Perseus e radio non ricevono insieme), altrimenti misuro adesso (antenne separate)."""
        r = self.rig
        if not (r and self.rig_state.get('ok')):
            self.on_status('S-meter: per allinearlo serve la radio collegata (CAT)')
            return
        mk = getattr(self, 'sm_marked', None)
        if not mk or time.time() - mk[2] > 600:
            # senza la misura del passo 1 non leggo il Perseus adesso: col relè sulla radio sentirebbe solo rumore
            self.sm_marked = None
            self.on_status('S-meter: fai prima «1 · Misura qui» (vale 10 minuti)')
            return
        fref = mk[1] if mk else (self.dx_abs or 0)
        f = self.rig_state.get('f') or 0
        if abs(f - fref) > 300:
            self.on_status('S-meter: radio e DX devono essere sulla stessa frequenza')
            return
        vals, mine = [], []
        for _ in range(10):
            v = r.strength()
            if v is not None:
                vals.append(v)
            if not mk and self.sm_hist:
                mine.append(self.sm_hist[-1])
            time.sleep(0.25)
        if len(vals) < 4 or (not mk and len(mine) < 4):
            self.on_status('S-meter: la radio non dà il livello del segnale (STRENGTH)')
            return
        dbm = -73.0 + sorted(vals)[len(vals) // 2]
        me = mk[0] if mk else sorted(mine)[len(mine) // 2]
        self.sm_marked = None
        self.post_json({'type': 'sm_step', 'step': 2})
        self.cfg.setdefault('sm_cal', {})[self.src_kind] = round(dbm - me - self.sm_att(), 1)
        save_cfg(self.cfg)
        self.on_status(f'S-meter allineato alla radio: {dbm:.0f} dBm')
        self.post_json(self.cfg_msg())

    # -------------------------------------------------------------- radio (Hamlib / rigctld)
    def cat(self):
        return self.cfg.setdefault('cat', {})

    def rig_apply(self):
        """Accende, spegne o ricollega la radio con le impostazioni attuali."""
        old, self.rig = getattr(self, 'rig', None), None
        if old:
            threading.Thread(target=old.stop, daemon=True).start()
        self.rig_state = {'ok': False}
        self.rig_last_f = None
        c = self.cat()
        if c.get('on'):
            if old:
                time.sleep(0.6)                     # la porta COM si libera
            self.rig = riglib.Rig(c, on_state=self.on_rig, on_status=self.on_status)
            self.rig.start()
        self.post_json(self.rig_msg())

    def rig_msg(self):
        st = getattr(self, 'rig_state', {'ok': False})
        return {'type': 'rig', 'on': bool(self.cat().get('on')), **st}

    def rig_ctl(self):
        """La radio è da comandare? (accesa, collegata, opzione attiva, non in riproduzione)"""
        r = getattr(self, 'rig', None)
        return r if (r and self.rig_state.get('ok') and self.cat().get('comanda', True)
                     and self.src_kind != 'file') else None

    def rig_mode_for(self):
        m = self.modo()
        if m == 'ssb':
            return 'USB' if self.sb_eff() > 0 else 'LSB'
        if m == 'rtty':
            return 'RTTY'
        if m == 'ft8':
            return 'PKTUSB'                          # USB-D sugli Icom
        if m in ('fm', 'am'):
            return m.upper()
        return 'CW'

    def rig_follow_dx(self):
        r = self.rig_ctl()
        if r and self.dx_abs:
            if self.src_kind == 'audio':
                return                                # l'audio viene dalla radio: è lei che comanda
            r.set_freq(self.dx_abs)

    def nrnb_apply(self):
        if self.engine:
            self.engine.set_nrnb(int(self.cfg.get('nb', 0)), int(self.cfg.get('nr', 0)), bool(self.cfg.get('notch', False)))

    def rig_tx(self, hz):
        r = self.rig_ctl()
        if r and hz:
            r.set_split_tx(hz)

    def on_rig(self, st):
        """Nuovo stato dalla radio (thread della radio)."""
        changed = st != self.rig_state
        was_ptt = bool(self.rig_state.get('ptt'))
        self.rig_state = st
        if bool(st.get('ptt')) != was_ptt:
            self.cmd_q.put(('ptt', bool(st.get('ptt'))))   # TX dalla radio: audio muto, poi AGC da zero
        if changed:
            self.post_json(self.rig_msg())
        r = self.rig
        f = st.get('f')
        if not st.get('ok') or not f or r is None:
            return
        first = self.rig_last_f is None
        # La manopola si considera girata solo se la nuova frequenza resta uguale per due letture di fila
        # (con lo split alcune radio Icom, mentre Hamlib scrive il VFO B, per un attimo rispondono con la
        # frequenza di TX) e se non è la frequenza di TX appena messa da noi (RX2 / TX qui).
        prev, self.rig_prev_poll = getattr(self, 'rig_prev_poll', None), f
        moved = False
        if first:
            self.rig_last_f = f
        elif f != self.rig_last_f and f == prev:
            ftx = st.get('ftx')
            is_tx = (r.ours('ftx', f) or (st.get('split') and ftx and abs(f - ftx) <= 5))
            if not r.ours('f', f) and not is_tx:
                moved = True
            if not is_tx:
                self.rig_last_f = f
        if not self.cat().get('segui', True) or self.src_kind == 'file':
            return
        if isinstance(self.src, AudioCard) and self.src_kind == 'audio':
            if moved or first:
                self.src.set_lo(float(f))              # «Frequenza radio» letta dalla radio: niente più a mano
            return
        if first:
            # appena collegata: se la radio è in una banda HF la segue FZR Skimmer, altrimenti la porto io sul DX
            if band_of(f):
                moved = True
            else:
                if self.cat().get('comanda', True) and self.dx_abs:
                    r.set_freq(self.dx_abs)
                    r.set_mode(self.rig_mode_for())
                return
        if not moved:
            return
        # manopola della radio girata: il DX (o l'RX) di FZR Skimmer va lì
        self.cmd_q.put(('dx', float(f), 'radio'))
        if self.fs and self.center and abs(f - self.center) > self.fs * 0.42 and \
                self.tunable():
            self.tune(f + (self.fs * 0.2 if self.modo() in ('dx',) + RX_MODES else 0))   # spazio UP sopra

    def modo(self):
        return self.cfg.get('modo', 'dx')

    def is_ssb(self):
        """Ricevitore puro (CW, SSB, RTTY, FT8): due RX in stereo, niente decodifica CW."""
        return self.modo() in RX_MODES

    def mem_key(self, modo=None):
        """Memorie di banda: CW (skimmer e RX CW), fonia (SSB), RTTY, FT8."""
        return {'ssb': 'memorie_ssb', 'rtty': 'memorie_rtty', 'ft8': 'memorie_ft8', 'fm': 'memorie_fm',
                'am': 'memorie_am'}.get(modo or self.modo(), 'memorie')

    def band_mem(self, b, modo=None):
        k = self.mem_key(modo)
        m = self.cfg.get(k, {}).get(b)
        if m:
            return m
        if k == 'memorie_ssb':
            return {'centro': BANDS_SSB[b][0], 'dx': BANDS_SSB[b][1], 'pred': True} if b in BANDS_SSB else None
        if k in ('memorie_fm', 'memorie_am'):
            return {'centro': BANDS_FM[b] + 40000, 'dx': BANDS_FM[b], 'pred': True} if b in BANDS_FM else None
        if k in ('memorie_rtty', 'memorie_ft8'):
            t = BANDS_RTTY if k == 'memorie_rtty' else BANDS_FT8
            return {'centro': t[b] + (15000 if k == 'memorie_rtty' else 10000), 'dx': t[b], 'pred': True} if b in t else None
        return {'centro': BANDS[b][2], 'dx': BANDS[b][3], 'pred': True}

    def pluto_msg(self):
        pc = self.cfg.get('pluto', {})
        return {'host': pc.get('host', '192.168.2.1'), 'gain': pc.get('gain')}

    def pluto_set(self, v):
        """Impostazioni del Pluto: guadagno (None = AGC del chip) e indirizzo."""
        pc = self.cfg.setdefault('pluto', {})
        self.sm_floor = None
        if 'gain' in v:
            pc['gain'] = None if v['gain'] in (None, '', 'auto') else max(0, min(71, int(v['gain'])))
            if isinstance(self.src, PlutoSource):
                self.src.set_gain(pc['gain'])
        restart = False
        if v.get('host'):
            pc['host'] = str(v['host']).strip()[:80]
            restart = True
        save_cfg(self.cfg)
        if restart and self.src_kind == 'pluto':
            self.switch_source('pluto')
        else:
            self.post_json(self.cfg_msg())

    def rtl_msg(self):
        r = self.cfg.get('rtl', {})
        src = self.src if isinstance(self.src, RtlTcpSource) else None
        return {'present': getattr(self, 'rtl_found', []), 'exe': r.get('exe') or getattr(self, 'rtl_exe', None),
                'host': r.get('host', '127.0.0.1'), 'port': int(r.get('port', 1234)), 'gain': r.get('gain'),
                'gains': src.gains() if src and src.tuner else RTL_GAINS, 'bias': bool(r.get('bias', False)), 'direct': int(r.get('direct', 0)),
                'agc': bool(r.get('agc', False)), 'tuner': src.tuner if src else None}

    def rtl_set(self, v):
        """Impostazioni della chiavetta: guadagno, bias-T, direct sampling, AGC del chip, indirizzo di rtl_tcp."""
        r = self.cfg.setdefault('rtl', {})
        restart = False
        self.sm_floor = None
        if 'gain' in v:
            r['gain'] = None if v['gain'] in (None, '', 'auto') else int(v['gain'])
            if isinstance(self.src, RtlTcpSource):
                self.src.set_gain(r['gain'])
        if 'bias' in v:
            r['bias'] = bool(v['bias'])
            if isinstance(self.src, RtlTcpSource):
                self.src.set_bias(r['bias'])
        if 'agc' in v:
            r['agc'] = bool(v['agc'])
            if isinstance(self.src, RtlTcpSource):
                self.src.set_rtl_agc(r['agc'])
        if 'direct' in v:
            r['direct'] = int(v['direct'])
            restart = True
        for key in ('host', 'port', 'dev'):
            if key in v and v[key] not in (None, ''):
                r[key] = int(v[key]) if key in ('port', 'dev') else str(v[key]).strip()
                restart = True
        save_cfg(self.cfg)
        if restart and self.src_kind == 'rtl':
            if 'direct' in v:                              # cambiano le bande possibili: vado su una che si riceve
                self.cfg['rtl_lo'] = 14035000 if r['direct'] else 145540000
                self.dx_abs = 14025000.0 if r['direct'] else 145500000.0
                if not r['direct'] and self.modo() not in RX_MODES:
                    self.cfg['modo'] = 'fm'
            self.switch_source('rtl')
        else:
            self.post_json(self.cfg_msg())

    def first_run_source(self):
        """Primo avvio senza Perseus ma con una chiavetta RTL-SDR: parto con la chiavetta in FM sui 2 m."""
        if self.args.file or self.args.sorgente or 'sorgente' in self.cfg or 'dx' in self.cfg:
            return                                      # non è il primo avvio: resta il ricevitore di sempre
        if os.path.exists(os.path.join(find_perseus_folder(self.cfg, self.args.cartella), 'perseususb.dll')):
            return
        d = find_rtl_dongles(0)
        if not d:
            return
        log(f'Primo avvio: niente Perseus, chiavetta {d[0]}: parto con la RTL-SDR in FM sui 2 m')
        self.cfg.update(sorgente='rtl', modo='fm', rtl_lo=145540000, dx=145500000.0)
        save_cfg(self.cfg)
        self.src_kind = 'rtl'

    def rtl_scan(self):
        """In sottofondo: c'è una chiavetta RTL-SDR collegata? (e dove sta rtl_tcp)"""
        def run():
            try:
                self.rtl_found = find_rtl_dongles(0)
                self.rtl_exe = find_rtl_tcp()
                if self.rtl_found:
                    log(f'Chiavetta RTL-SDR: {", ".join(self.rtl_found)} · rtl_tcp: {self.rtl_exe or "non trovato"}')
                self.post_json(self.cfg_msg())
            except Exception as e:
                log(f'Ricerca RTL-SDR: {e}')
        threading.Thread(target=run, daemon=True).start()

    def banco_cmd(self, k, c):
        """Banco di memorie: salva, richiama, modifica, cancella, ripristina i preset."""
        if k == 'banco_add':
            if not self.dx_abs:
                return
            m = banco.aggiungi(self.cfg, c.get('nome') or '', self.dx_abs, self.modo(), c.get('gruppo') or '',
                               self.extra_mem())
            self.on_status(f'Memoria salvata: {m["nome"]} · {m["hz"]/1e6:.5f} MHz {banco.MODI.get(m["modo"], m["modo"])}')
        elif k == 'banco_edit':
            banco.modifica(self.cfg, c.get('id'), c)
        elif k == 'banco_del':
            banco.cancella(self.cfg, c.get('id'))
        elif k == 'banco_preset':
            n = banco.preset(self.cfg)
            self.on_status(f'Memorie predefinite: {n} aggiunte')
        elif k == 'banco_go':
            m = banco.trova(self.cfg, c.get('id'))
            if m:
                self.banco_go(m)
            return
        elif k == 'banco_import':
            fonte, rows, path = getattr(self, 'ponti_file', (None, [], ''))
            idx = [int(i) for i in c.get('idx', []) if 0 <= int(i) < len(rows)]
            if not idx:
                return
            if c.get('locatore'):
                self.cfg['locatore'] = str(c['locatore']).strip().upper()[:8]
            gch = (c.get('gruppo') or '').strip()[:30] or 'CHIRP'
            mems = [ponti.memoria(rows[i], fonte, gch) for i in idx]
            grp = sorted({m['gruppo'] for m in mems})
            n = banco.importa(self.cfg, mems, fonte, grp)
            self.on_status(f'Importati {n} canali in {len(grp)} gruppi: {", ".join(grp[:6])}{"…" if len(grp) > 6 else ""}')
        elif k == 'banco_delgroup':
            n = banco.cancella_gruppo(self.cfg, c.get('gruppo', ''))
            self.on_status(f'Gruppo «{c.get("gruppo", "")}» cancellato: {n} memorie')
        elif k == 'banco_list':
            self.post_json({'type': 'banco', 'v': banco.versione(self.cfg), 'list': banco.lista(self.cfg),
                            'gruppi': banco.gruppi(self.cfg), 'modi': banco.MODI})
            return
        banco.toccato(self.cfg)
        save_cfg(self.cfg)
        self.post_json({'type': 'banco', 'v': banco.versione(self.cfg), 'list': banco.lista(self.cfg),
                        'gruppi': banco.gruppi(self.cfg), 'modi': banco.MODI})
        self.post_json(self.cfg_msg())

    def extra_mem(self):
        """Impostazioni che una memoria si porta dietro (filtro, banda laterale, squelch)."""
        m = self.modo()
        x = {}
        if m == 'ssb':
            x = {'sb': self.cfg.get('sb', 'auto'), 'ssb_bw': int(self.cfg.get('ssb_bw', 2700))}
        elif m in ('fm', 'am'):
            x = {m + '_bw': int(self.cfg.get(m + '_bw', 12000 if m == 'fm' else 8000)), 'sql': float(self.cfg.get('sql', 0))}
        elif m in ('rxcw', 'dx', 'contest', 'normale'):
            x = {'filtro': float(self.cfg.get('filtro', 500))}
        return x

    def banco_go(self, m):
        hz, modo = float(m['hz']), m.get('modo', 'fm')
        if not self.band_reach(hz):
            self.on_status(f'{m["nome"]}: {hz/1e6:.4f} MHz non si riceve con questo ricevitore')
            return
        was_mode = self.modo()
        if modo in ('dx', 'contest', 'normale') + RX_MODES:
            self.cfg['modo'] = modo
        for key in ('sb', 'ssb_bw', 'fm_bw', 'am_bw', 'sql', 'filtro'):
            if key in m.get('x', {}):
                self.cfg[key] = m['x'][key]
        save_cfg(self.cfg)
        if self.engine:
            self.engine.listen_on = self.modo() == 'dx'
            if 'filtro' in m.get('x', {}):
                self.cmd_q.put(('bw', float(m['x']['filtro'])))
        self.cmd_q.put(('dx', hz, 'band'))
        # memoria richiamata: il centro si sposta sempre, con la memoria nello stesso punto della banda ricevuta
        # (un quinto sotto il centro: lontano dal picco al centro e con spazio sopra per lo split)
        target = hz + (self.fs or 192000) * 0.2
        if self.tunable() and (not self.center or abs(target - self.center) > 500):
            self.tune(target)
        elif isinstance(self.src, AudioCard):
            self.src.set_lo(hz)
        self.cmd_q.put(('ssbcfg',))
        r = self.rig_ctl()
        if r:
            if was_mode != self.modo():
                r.set_mode(self.rig_mode_for())
        self.on_status(f'{m["nome"]} · {hz/1e6:.5f} MHz {banco.MODI.get(self.modo(), self.modo())}')
        self.post_json(self.cfg_msg())

    def band_reach(self, hz):
        hz = hz - self.conv()                       # frequenza che arriva al ricevitore
        k = self.src_kind
        if k == 'perseus':
            return hz < 40e6
        if k == 'pluto':
            return PLUTO_RANGE[0] <= hz <= PLUTO_RANGE[1]
        if k == 'rtl':
            return hz < 30e6 if int(self.cfg.get('rtl', {}).get('direct', 0)) else 24e6 <= hz <= 1766e6
        return True

    def band_ok(self, b):
        """Bande che il ricevitore in uso può ricevere."""
        if self.conv():                              # con il convertitore: le bande che ci cadono dentro
            return self.src_kind != 'file' and self.band_reach(BANDS[b][2])
        if b == 'QO':
            return False
        k = self.src_kind
        if k == 'perseus':
            return b in HF_BANDS                        # Perseus: fino a 40 MHz
        if k == 'pluto':
            return b in ('6', '2', '70', '23')           # Pluto sbloccato: da 46,9 MHz (6 m e 2 m), standard da 325 MHz
        if k == 'rtl':
            if int(self.cfg.get('rtl', {}).get('direct', 0)):
                return b in HF_BANDS                    # direct sampling: solo onde corte
            return b in ('10',) + VHF_BANDS             # chiavetta: da 24 MHz in su
        if k == 'audio':
            return b in HF_BANDS + ('6', '2', '70')     # la radio
        return True

    def tunable(self):
        return isinstance(self.src, (PerseusDirect, PerseusExtIO, ExtIOSource, RtlTcpSource, PlutoSource))

    def go_band(self, b):
        m = self.band_mem(b)
        if not m:
            return False
        self.cmd_q.put(('dx', float(m['dx']), 'band'))
        if self.tunable():
            self.tune(m['centro'])
        elif isinstance(self.src, AudioCard):
            self.src.set_lo(float(m['dx']))      # senza CAT: sintonizza la radio sul DX
            self.on_status(f'Sintonizza la radio su {m["dx"]/1000:.2f} kHz')
        else:
            self.on_status('In riproduzione la frequenza centrale non si cambia: imposto solo il DX')
        return True

    def ws_cmd_info(self):
        exe = self.cat().get('exe')
        self.rig_models, self.rig_models_exe = riglib.list_models(exe), exe
        self.post_json({'type': 'cat_info', 'exe': exe, 'models': self.rig_models,
                        'coms': riglib.list_com_ports(), 'cfg': self.cat()})

    def sb_eff(self):
        """Banda laterale in uso: +1 USB, -1 LSB (automatica: LSB sotto i 10 MHz)."""
        v = self.cfg.get('sb', 'auto')
        if v == 'usb':
            return 1
        if v == 'lsb':
            return -1
        f = self.dx_abs or 0
        if band_of(f) == '60':
            return 1                                  # 60 m: per convenzione USB, anche sotto i 10 MHz
        return -1 if f < 10e6 else 1

    def ssb_apply(self):
        """Accende/spegne i due ricevitori del modo «ricevitore» (si chiama dal thread del calcolo)."""
        e = self.engine
        if e is None or self.center is None:
            return
        on = self.is_ssb()
        if on != e.ssb:
            e.out_audio = []
        m = self.modo()
        w = float(self.cfg.get('fm_bw', 12000) if m == 'fm' else self.cfg.get('am_bw', 8000) if m == 'am'
                  else self.cfg.get('ssb_bw', 2700))
        e.set_rx(on, m, self.sb_eff(), self.dx_abs - self.center + float(self.cfg.get('rx2_off', 5000.0)),
                 float(self.cfg.get('filtro', 500)), w)
        e.set_sql(float(self.cfg.get('sql', 0)))

    def cfg_msg(self):
        e = self.engine
        lic = {'lic': (self.lic or {}).get('testo'), 'donor': bool((self.lic or {}).get('donor')),
               'trial': self.trial_left(), 'locked': self.locked, 'url_key': URL_RICHIESTA_CHIAVE}
        if e is None:
            # ricevitore non partito: la pagina mostra lo stesso le sue impostazioni (indirizzo del Pluto, di rtl_tcp)
            return {'type': 'cfg', 'ready': False, 'status': self.status, 'status_en': i18n.en(self.status),
                    'src': self.src_kind, 'pluto': self.pluto_msg(), 'rtl': self.rtl_msg(), **lic}
        return {'type': 'cfg', 'ready': True, 'fs': e.fs, 'center': self.center, 'dx': self.dx_abs,
                'nbins': e.nbins_out, 'bw': e.wf_bw, 'f0': e.wf_f0,
                'rowdt': dsp.ROW_EVERY / dsp.FRAMES_PER_S, 'arate': e.arate, 'up': [e.up_lo / 1000, e.up_hi / 1000],
                'src': self.src_kind, 'status': self.status, 'status_en': i18n.en(self.status), 'version': VERSION,
                'rate_sel': int(self.cfg.get('audio_rate', 96000) if self.src_kind == 'iq' else
                                self.cfg.get('extio_rate', 192000) if self.src_kind == 'extio' else self.cfg.get('campionamento', 96000)),
                'ext_rates': [r for r in getattr(self.src, 'rates', [])] if isinstance(self.src, ExtIOSource) else [],
                'extio_path': self.cfg.get('extio_path'), 'extio_list': self.extio_list(),
                'ppm': self.ppm(), 'ppm_on': self.src_kind in self.PPM_KINDS, 'cal_list': calib.STATIONS,
                'rx2_off': float(self.cfg.get('rx2_off', 5000.0)), 'sb': self.cfg.get('sb', 'auto'), 'sb_eff': self.sb_eff(),
                'passo': int(self.cfg.get('passo', 500)), 'split': bool(self.cfg.get('split', True)),
                'agc': self.cfg.get('agc', 'slow'), 'rfgain': float(self.cfg.get('rfgain', 60)),
                'nb': int(self.cfg.get('nb', 0)), 'nr': int(self.cfg.get('nr', 0)), 'notch': bool(self.cfg.get('notch', False)),
                'ssb_bw': int(self.cfg.get('ssb_bw', 2700)), 'att': int(self.cfg.get('att', 0)),
                'pre': bool(self.cfg.get('preamp', True)), 'attpre': isinstance(self.src, PerseusDirect), 'swap': bool(self.cfg.get('inverti_iq', False)),
                'bands': {b: self.band_mem(b) for b in BANDS if self.band_ok(b) and self.band_mem(b)},
                'band_order': [b for b in HF_BANDS + VHF_BANDS + ('QO',) if self.band_ok(b)],
                'conv': self.conv(), 'beacon_lock': bool(self.cfg.get('beacon_lock')),
                'fm_bw': int(self.cfg.get('fm_bw', 12000)), 'am_bw': int(self.cfg.get('am_bw', 8000)),
                'sql': float(self.cfg.get('sql', 0)), 'rtl': self.rtl_msg(), 'pluto': self.pluto_msg(), 'banco_v': banco.versione(self.cfg),
                'band': band_of(self.dx_abs or 0) or band_of(self.center or 0),
                'bw_rx': float(self.cfg.get('filtro', 500)), 'snr': float(self.cfg.get('sensibilita', 5.0)),
                'audio_devs': [n for i, n, ch in self.audio_devs], 'audio_dev': self.cfg.get('audio_dev'),
                'offset': float(self.cfg.get('audio_offset', 600)),
                'dial': getattr(self.src, 'dial', None), 'calls_n': len(dsp.KNOWN_CALLS), 'ui': self.cfg.get('ui', {}), 'datadir': DATA_DIR,
                'rbn_on': bool(self.cfg.get('rbn', True)), 'rbn_state': self.rbn_state(), 'rbn_call': self.rbn_call(),
                'rbn_need_call': not (self.lic or {}).get('call'),
                'modo': self.cfg.get('modo', 'dx'), 'modo_mem': bool(self.cfg.get('modo_mem', False)),
                'mem_min': self.rec.minutes, 'live_src': self.cfg.get('sorgente', 'perseus'), 'rec_dir': self.rec_dir, 'file': self.file_state(), **lic}

    # -------------------------------------------------------------- web
    def post_json(self, obj):
        if self.loop:
            self.loop.call_soon_threadsafe(self._broadcast, json.dumps(obj))

    def post_bin(self, data):
        if self.loop:
            self.loop.call_soon_threadsafe(self._broadcast, data)

    def _broadcast(self, msg):
        for ws in list(self.clients):
            if ws.transport is not None and ws.transport.get_write_buffer_size() > 4_000_000:
                continue                                   # client lento: salta
            asyncio.ensure_future(self._safe_send(ws, msg))

    async def _safe_send(self, ws, msg):
        try:
            await ws.send(msg)
        except Exception:
            self.clients.discard(ws)

    async def handler(self, ws, path=None):
        self.clients.add(ws)
        try:
            await ws.send(json.dumps(self.cfg_msg()))
            await ws.send(json.dumps(self.rig_msg()))
            with self.lock:
                rows = list(self.rows_hist)
            if rows:
                for i in range(0, len(rows), 200):
                    part = rows[i:i + 200]
                    body = b''.join(r[1].tobytes() for r in part)
                    await ws.send(b'\x01' + struct.pack('<dII', float(part[0][0]), len(part), len(part[0][1])) + body)
            await ws.send(json.dumps({'type': 'ev', 'words': list(self.words_hist), 'listen': list(self.listen_hist)}))
            async for m in ws:
                try:
                    c = json.loads(m)
                except Exception:
                    continue
                k = c.get('cmd')
                if k == 'dx':
                    hz = float(c['hz'])
                    if c.get('jump'):                       # frequenza scritta: se è fuori dalla vista ci vado
                        if not self.band_reach(hz):
                            self.on_status(f'{hz/1e6:.4f} MHz non si riceve con questo ricevitore')
                            continue
                        self.cmd_q.put(('dx', hz, 'band'))
                        # frequenza scritta: centro sempre spostato (come le memorie); cifra per cifra solo se esce
                        target = hz + (self.fs or 192000) * 0.2
                        if self.tunable() and self.fs and self.center and (
                                abs(hz - self.center) > self.fs * 0.42
                                or c.get('centro') and abs(target - self.center) > 500):
                            self.tune(target)
                        elif isinstance(self.src, AudioCard) and self.src_kind == 'audio':
                            self.src.set_lo(hz)
                    else:
                        self.cmd_q.put(('dx', hz))
                elif k == 'up':
                    self.cmd_q.put(('up', c['lo'], c['hi']))
                elif k == 'bw':
                    self.cmd_q.put(('bw', float(c['hz'])))
                elif k == 'snr':
                    self.cmd_q.put(('snr', float(c['v'])))
                elif k == 'lo' and self.src:
                    self.tune(c['hz'])
                elif k == 'band':
                    b = str(c['band'])
                    if b in BANDS:
                        if self.go_band(b):
                            r = self.rig_ctl()
                            if r:
                                r.split_off()
                                r.set_mode(self.rig_mode_for())
                elif k == 'save':
                    b = band_of(self.dx_abs or 0) or band_of(self.center or 0)
                    if b:
                        self.cfg.setdefault(self.mem_key(), {})[b] = {'centro': int(self.center), 'dx': float(self.dx_abs)}
                        save_cfg(self.cfg)
                        self.on_status(f'Memoria {b} m{" " + {"rxcw": "CW", "ssb": "SSB", "rtty": "RTTY", "ft8": "DIGI", "fm": "FM", "am": "AM"}[self.modo()] if self.is_ssb() else ""} salvata: centro {self.center/1000:.1f} · DX {self.dx_abs/1000:.2f} kHz')
                        self.post_json(self.cfg_msg())
                    else:
                        self.on_status('Frequenza fuori dalle bande radioamatoriali: memoria non salvata')
                elif k == 'register':
                    ok, msg = self.register(c.get('call', ''), c.get('key', ''))
                    await ws.send(json.dumps({'type': 'reg', 'ok': ok, 'msg': msg}))
                elif k == 'restart':
                    self.switch_source(self.src_kind if self.src_kind in ('perseus', 'audio', 'iq', 'file', 'extio', 'rtl', 'pluto') else 'perseus')
                elif k == 'ui' and isinstance(c.get('v'), dict):
                    ui = self.cfg.setdefault('ui', {})
                    for key in ('span', 'win', 'pal', 'lum', 'con', 'low_rel', 'list', 'lang'):
                        if key in c['v']:
                            ui[key] = c['v'][key]
                    save_cfg(self.cfg)
                elif k == 'quit':
                    self.shutdown()
                elif k == 'save_last':
                    self.rec.save_last(lambda paths: self.post_json({'type': 'files', 'files': self.list_files()}))
                elif k == 'rec':
                    if c.get('on'):
                        if self.src_kind == 'file' or not self.src:
                            self.on_status('Per registrare serve il ricevitore acceso (non la riproduzione)')
                        elif self.rec.start_rec():
                            self.on_status(f'Registrazione in corso in {self.rec_dir}')
                    else:
                        self.rec.stop_rec()
                        self.post_json({'type': 'files', 'files': self.list_files()})
                    self.post_rec()
                elif k == 'rbn':
                    if 'on' in c:
                        self.cfg['rbn'] = bool(c['on'])
                    if 'call' in c:
                        self.cfg['nominativo'] = str(c['call']).strip().upper()[:12]
                    save_cfg(self.cfg)
                    self.rbn_apply()
                    self.post_json(self.cfg_msg())
                elif k == 'rx2':
                    self.cmd_q.put(('rx2', float(c['off'])))
                elif k == 'sb' and c.get('v') in ('auto', 'usb', 'lsb'):
                    self.cfg['sb'] = c['v']
                    save_cfg(self.cfg)
                    self.cmd_q.put(('ssbcfg',))
                    r = self.rig_ctl()
                    if r and self.modo() == 'ssb':
                        r.set_mode(self.rig_mode_for())
                elif k == 'sm_set' and c.get('dbm') is not None:
                    self.sm_set(float(c['dbm']))
                elif k == 'sm_mark':
                    self.sm_mark()
                elif k == 'sm_radio':
                    threading.Thread(target=self.sm_radio, daemon=True).start()
                elif k == 'sm_reset':
                    self.cfg.get('sm_cal', {}).pop(self.src_kind, None)
                    save_cfg(self.cfg)
                    self.post_json(self.cfg_msg())
                elif k == 'agc' and c.get('v') in ('fast', 'med', 'slow', 'off'):
                    self.cfg['agc'] = c['v']
                    save_cfg(self.cfg)
                    self.cmd_q.put(('agc',))
                    self.post_json(self.cfg_msg())
                elif k == 'nrnb':
                    # NB e NR 0–3, NOTCH acceso/spento (vale in SSB)
                    for key in ('nb', 'nr'):
                        if key in c:
                            self.cfg[key] = max(0, min(3, int(c[key])))
                    if 'notch' in c:
                        self.cfg['notch'] = bool(c['notch'])
                    save_cfg(self.cfg)
                    self.cmd_q.put(('agc',))
                    self.post_json(self.cfg_msg())
                elif k == 'rfgain':
                    self.cfg['rfgain'] = max(0.0, min(100.0, float(c['v'])))
                    save_cfg(self.cfg)
                    self.cmd_q.put(('agc',))
                elif k == 'ssb_bw' and int(c.get('v', 0)) in (1800, 2100, 2400, 2700, 3000, 3300):
                    self.cfg['ssb_bw'] = int(c['v'])
                    save_cfg(self.cfg)
                    self.cmd_q.put(('ssbcfg',))
                elif k == 'att' and int(c.get('v', -1)) in (0, 10, 20, 30):
                    self.cfg['att'] = int(c['v'])
                    save_cfg(self.cfg)
                    if isinstance(self.src, PerseusDirect):
                        self.src.set_att(self.cfg['att'])
                    self.post_json(self.cfg_msg())
                elif k == 'pre':
                    self.cfg['preamp'] = bool(c.get('v'))
                    save_cfg(self.cfg)
                    if isinstance(self.src, PerseusDirect):
                        self.src.set_preamp(self.cfg['preamp'])
                    self.post_json(self.cfg_msg())
                elif k == 'tx' and c.get('hz'):
                    if self.rig_ctl():
                        self.rig_tx(float(c['hz']))
                    else:
                        self.on_status('Radio non collegata: attiva «Radio (CAT)» nelle impostazioni')
                elif k == 'cat':
                    v = c.get('v') or {}
                    keep = ('on', 'modo', 'exe', 'model', 'nome', 'com', 'baud', 'host', 'tcp', 'segui', 'comanda', 'tx_rx2')
                    self.cat().update({k2: v[k2] for k2 in keep if k2 in v})
                    save_cfg(self.cfg)
                    if c.get('apply', True):
                        threading.Thread(target=self.rig_apply, daemon=True).start()
                    else:
                        self.post_json(self.rig_msg())
                elif k == 'cat_info':
                    def info(ws=ws):
                        exe = self.cat().get('exe') or riglib.find_rigctld([app_dir(), DATA_DIR])
                        if exe and not self.cat().get('exe'):
                            self.cat()['exe'] = exe
                            save_cfg(self.cfg)
                        if not getattr(self, 'rig_models', None) or self.rig_models_exe != exe:
                            self.rig_models, self.rig_models_exe = riglib.list_models(exe), exe
                        self.post_json({'type': 'cat_info', 'exe': exe, 'models': self.rig_models,
                                        'coms': riglib.list_com_ports(), 'cfg': self.cat()})
                    threading.Thread(target=info, daemon=True).start()
                elif k == 'browse_rigctld':
                    def br():
                        try:
                            import tkinter as tk
                            from tkinter import filedialog
                            root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
                            pth = filedialog.askopenfilename(parent=root, title='rigctld.exe (Hamlib)',
                                                             filetypes=[('rigctld', 'rigctld*.exe'), ('Programmi', '*.exe')])
                            root.destroy()
                        except Exception as e:
                            self.on_status(f'Impossibile aprire la finestra dei file: {e}')
                            return
                        if pth:
                            self.cat()['exe'] = os.path.abspath(pth)
                            self.rig_models = None
                            save_cfg(self.cfg)
                            self.ws_cmd_info()
                    threading.Thread(target=br, daemon=True).start()
                elif k == 'split':
                    # modi Ricevitore: SPLIT acceso = RX2 attivo (e il TX della radio può seguirlo); spento = solo RX1
                    on = bool(c.get('v'))
                    self.cfg['split'] = on
                    save_cfg(self.cfg)
                    r = self.rig_ctl()
                    if r and self.is_ssb():
                        if not on:
                            r.split_off()
                        elif self.cat().get('tx_rx2', True) and self.dx_abs:
                            self.rig_tx(self.dx_abs + float(self.cfg.get('rx2_off', 5000.0)))
                    self.post_json(self.cfg_msg())
                elif k == 'passo' and int(c.get('v', 0)) in (10, 50, 100, 500, 1000):
                    self.cfg['passo'] = int(c['v'])
                    save_cfg(self.cfg)
                    self.post_json(self.cfg_msg())
                elif k == 'mode' and c.get('v') in ('dx', 'contest', 'normale') + RX_MODES:
                    was_key, was_mode = self.mem_key(), self.modo()
                    self.cfg['modo'] = c['v']
                    if was_key != self.mem_key() and self.cfg.get('modo_mem', False):
                        # cambia la serie di memorie (CW, fonia, RTTY, FT8): stessa banda, memoria dell'altra serie
                        # (solo se l'hai scelto: di base cambiando modo si resta sulla frequenza)
                        b = band_of(self.dx_abs or 0) or band_of(self.center or 0)
                        if self.src_kind == 'file':
                            pass                                  # in riproduzione il centro non si sposta
                        elif b and not self.go_band(b):
                            self.on_status(f'{b} m: niente fonia, resto qui')
                    save_cfg(self.cfg)
                    if self.engine:
                        self.engine.listen_on = c['v'] == 'dx'
                        self.engine.pending_listen = []
                    if c['v'] != 'dx':
                        self.listen_hist.clear()
                    self.cmd_q.put(('ssbcfg',))
                    r = self.rig_ctl()
                    if r and was_mode != self.modo():
                        if was_key != self.mem_key():
                            r.split_off()                         # lo split di un altro modo non vale qui
                        r.set_mode(self.rig_mode_for())
                elif k == 'modo_mem':
                    self.cfg['modo_mem'] = bool(c.get('v'))
                    save_cfg(self.cfg)
                    self.post_json(self.cfg_msg())
                elif k == 'memory':
                    m = max(0.0, min(10.0, float(c.get('min', 5))))
                    self.cfg['memoria_min'] = m
                    save_cfg(self.cfg)
                    self.rec.set_minutes(m)
                    self.post_json(self.cfg_msg())
                elif k == 'files':
                    await ws.send(json.dumps({'type': 'files', 'files': self.list_files()}))
                elif k == 'play':
                    path = c.get('path')
                    if path and os.path.isfile(path):
                        self.cfg['file_path'] = path
                    self.switch_source('file')
                elif k == 'banco_pick':
                    path = c.get('path') if (c.get('path') and os.path.isfile(c['path'])
                                             and c['path'].lower().endswith(('.xls', '.csv'))) else None
                    if not path:
                        path = await asyncio.get_running_loop().run_in_executor(None, self.pick_list)
                    if path:
                        try:
                            fonte, rows = ponti.leggi(path)
                        except Exception as e:
                            self.on_status(f'Elenco non letto: {e}')
                            continue
                        self.ponti_file = (fonte, rows, path)
                        keys = ('nome', 'hz', 'shift', 'tono', 'regione', 'prov', 'loc', 'grp', 'id', 'qra', 'banda', 'analog')
                        await ws.send(json.dumps({'type': 'ponti', 'fonte': fonte, 'file': os.path.basename(path),
                                                  'locatore': self.cfg.get('locatore', ''),
                                                  'rows': [[r[k] for k in keys] for r in rows], 'keys': keys}))
                elif k == 'browse':
                    path = await asyncio.get_running_loop().run_in_executor(None, self.pick_file)
                    if path:
                        self.cfg['file_path'] = path
                        self.switch_source('file')
                elif k == 'pause' and isinstance(self.src, WavFile):
                    self.src.set_pause(bool(c.get('on')))
                    self.on_pos(self.src)
                elif k == 'seek' and isinstance(self.src, WavFile):
                    self.src.seek(float(c.get('s', 0)))
                    self.on_pos(self.src)
                elif k == 'opendir':
                    try:
                        os.makedirs(self.rec_dir, exist_ok=True)
                        os.startfile(self.rec_dir)
                    except Exception:
                        self.on_status(f'Le registrazioni sono in {self.rec_dir}')
                elif k == 'source' and c.get('kind') == 'file':
                    self.switch_source('file')
                elif k == 'extio' and c.get('path'):
                    if os.path.isfile(c['path']):
                        self.cfg['extio_path'] = c['path']
                        self.switch_source('extio')
                elif k == 'ppm':
                    if self.src_kind in self.PPM_KINDS:
                        self.set_ppm(c['v'])
                elif k == 'calib':
                    self.calibrate(float(c['khz']))
                elif k == 'browse_extio':
                    path = await asyncio.get_running_loop().run_in_executor(None, self.pick_extio)
                    if path:
                        self.cfg['extio_path'] = path
                        self.switch_source('extio')
                elif k == 'rate' and isinstance(self.src, ExtIOSource):
                    self.cfg['extio_rate'] = int(c['hz'])
                    save_cfg(self.cfg)
                    self.src.set_rate(int(c['hz']))
                elif k == 'beacon_lock':
                    self.cfg['beacon_lock'] = bool(c.get('v'))
                    self.lk_ok, self.lk_buf, self.lk_n, self.lk_last = False, [], 0, None
                    save_cfg(self.cfg)
                    self.post_json(self.cfg_msg())
                elif k == 'conv':
                    try:
                        self.set_conv(float(c.get('hz') or 0))
                    except (TypeError, ValueError):
                        pass
                elif k == 'pluto' and isinstance(c.get('v'), dict):
                    self.pluto_set(c['v'])
                elif k == 'source' and c.get('kind') in ('perseus', 'audio', 'iq', 'extio', 'rtl', 'pluto'):
                    self.switch_source(c['kind'], c.get('dev'))
                elif k == 'rtl' and isinstance(c.get('v'), dict):
                    self.rtl_set(c['v'])
                elif k == 'sql':
                    self.cfg['sql'] = max(0.0, min(40.0, float(c.get('v', 0))))
                    save_cfg(self.cfg)
                    if self.engine:
                        self.engine.set_sql(self.cfg['sql'])
                elif k in ('fm_bw', 'am_bw') and int(c.get('v', 0)) in (6000, 8000, 10000, 12000, 16000, 25000):
                    self.cfg[k] = int(c['v'])
                    save_cfg(self.cfg)
                    self.cmd_q.put(('ssbcfg',))
                    self.post_json(self.cfg_msg())
                elif k.startswith('banco_'):
                    self.banco_cmd(k, c)
                elif k == 'audiodev':
                    self.switch_source(self.src_kind if self.src_kind in ('audio', 'iq') else 'audio', c.get('dev'))
                elif k == 'offset' and isinstance(self.src, AudioCard):
                    self.cfg['audio_offset'] = float(c['hz'])
                    save_cfg(self.cfg)
                    self.src.set_offset(float(c['hz']))
                elif k == 'rate' and isinstance(self.src, AudioCard) and self.src_kind == 'iq':
                    self.cfg['audio_rate'] = int(c['hz'])
                    self.switch_source('iq')
                elif k == 'rate' and isinstance(self.src, PerseusDirect):
                    self.cfg['campionamento'] = int(c['hz'])
                    save_cfg(self.cfg)
                    self.src.set_rate(int(c['hz']))
                elif k == 'swap' and isinstance(self.src, (PerseusDirect, AudioCard)):
                    self.cfg['inverti_iq'] = bool(c['on'])
                    save_cfg(self.cfg)
                    self.src.set_swap(bool(c['on']))
                    self.post_json(self.cfg_msg())
                elif k == 'gui' and isinstance(self.src, (PerseusExtIO, ExtIOSource)):
                    self.src.show_gui()
        except Exception:
            pass
        finally:
            self.clients.discard(ws)
            if self._is_local(ws) and getattr(self, 'app_proc', None) is not None:
                asyncio.ensure_future(self._maybe_quit())

    @staticmethod
    def _is_local(ws):
        try:
            h = ws.remote_address[0]
            return h in ('127.0.0.1', '::1', 'localhost') or str(h).startswith('::ffff:127.')
        except Exception:
            return False

    def idle_thread(self):
        """Se nessuna pagina sul PC è collegata da 20 s (finestra chiusa senza avvisare), esco."""
        seen = time.time() + 25                  # all'avvio la finestra può metterci un po' ad aprirsi
        while True:
            time.sleep(2)
            if getattr(self, 'app_proc', None) is None:
                continue
            if any(self._is_local(c) for c in list(self.clients)):
                seen = time.time()
            elif time.time() - seen > 20:
                log('Nessuna finestra collegata da 20 s: esco')
                self.shutdown()
                return

    async def _maybe_quit(self):
        """Finestra del programma chiusa: se entro 6 s non si riapre (non era un ricaricamento), esco.
        Serve perché Edge a volte resta acceso in sottofondo e il processo della finestra non termina."""
        await asyncio.sleep(6)
        if not any(self._is_local(c) for c in self.clients):
            log('Finestra chiusa (nessuna pagina locale collegata): esco')
            self.shutdown()

    async def http(self, path, headers):
        path = path.split('?', 1)[0]
        if path in ('/', '/index.html'):
            with open(res_path('index.html'), 'rb') as f:
                body = f.read()
            # il logo con la versione nell'indirizzo: a ogni versione nuova il browser lo ricarica
            body = body.replace(b'"/logo.png"', ('"/logo.png?v=' + VERSION + '"').encode())
            body = body.replace(b'__VERSIONE__', VERSION.encode())          # versione in alto, accanto al nome
            return (200, [('Content-Type', 'text/html; charset=utf-8'), ('Cache-Control', 'no-store')], body)
        if path == '/ws':
            return None
        if path == '/versione':                       # un FZR Skimmer appena avviato chiede chi è già acceso
            return (200, [('Content-Type', 'text/plain'), ('Cache-Control', 'no-store')], VERSION.encode())
        if path in ('/logo.png', '/favicon.ico'):
            try:
                with open(res_path('logo.png'), 'rb') as f:
                    return (200, [('Content-Type', 'image/png'), ('Cache-Control', 'no-cache')], f.read())
            except Exception:
                return (204, [], b'')
        return (404, [('Content-Type', 'text/plain')], b'non trovato')

    async def main(self):
        self.loop = asyncio.get_running_loop()
        threading.Thread(target=self.dsp_thread, daemon=True).start()
        threading.Thread(target=self.diag_thread, daemon=True).start()
        threading.Thread(target=self.trial_thread, daemon=True).start()
        threading.Thread(target=self.rec_thread, daemon=True).start()
        threading.Thread(target=self.rbn_thread, daemon=True).start()
        threading.Thread(target=self.stations_thread, daemon=True).start()
        threading.Thread(target=self.idle_thread, daemon=True).start()
        self.rbn_apply()
        self.rig_apply()
        log(f'Impostazioni in {CFG_PATH}')
        log('Licenza: ' + (f"{self.lic['testo']} ({self.lic['origine']})" if self.lic else 'non registrato, prova di 5 minuti'))
        log(f'{APP} {VERSION} · Python {sys.version.split()[0]} {struct.calcsize("P")*8} bit · numpy {np.__version__}')
        self.first_run_source()
        self.rtl_scan()
        try:
            self.start_source()
        except Exception as e:
            self.on_status(f'Errore: {e}')
        port = self.args.porta
        async with websockets.serve(self.handler, '0.0.0.0', port, process_request=self.http,
                                    max_size=2 ** 20, compression=None):
            url = f'http://localhost:{port}/'
            ip = local_ip()
            log(f'{APP} {VERSION}: interfaccia su {url}' + (f'  (dal tablet: http://{ip}:{port}/)' if ip else ''))
            if self.args.browser:
                webbrowser.open(url)
            elif not self.args.no_browser:
                proc = open_app_window(url)
                self.app_proc = proc
                if proc is not None:
                    def watch():
                        t0 = time.time()
                        proc.wait()
                        if time.time() - t0 < 15:
                            # Edge era già acceso con il nostro profilo (rimasto in sottofondo): ha passato la
                            # finestra a quello e il processo lanciato è uscito subito. La finestra c'è:
                            # da qui in poi capisco la chiusura dalla pagina collegata, non dal processo.
                            self.app_handoff = True
                            log('La finestra è stata aperta da un Edge già acceso: sorveglio la pagina')
                            return
                        log('Finestra chiusa: esco')
                        self.shutdown()
                    threading.Thread(target=watch, daemon=True).start()
            await asyncio.Future()

    def shutdown(self):
        """Ferma il ricevitore (libera il Perseus) ed esce."""
        if getattr(self, '_closing', False):
            return
        self._closing = True
        log('Chiusura in corso…')
        # rete di sicurezza: qualunque cosa si blocchi (driver, browser), fra 8 s il processo esce comunque
        t = threading.Timer(8.0, lambda: (log('Chiusura forzata'), hard_exit()))
        t.daemon = True
        t.start()

        def run():
            try:
                self.rec.stop_rec()                     # chiude bene il WAV in corso
            except Exception:
                pass
            try:
                if self.rig:
                    self.rig.stop()                     # chiude anche rigctld se l'abbiamo aperto noi
            except Exception:
                pass
            src, self.src = self.src, None
            if src:
                try:
                    src.stop()
                    ev = getattr(src, 'stopped', None)
                    if ev is not None:
                        ev.wait(3)
                    else:
                        time.sleep(0.5)
                except Exception:
                    pass
            # chiude la finestra dell'applicazione (Edge non lascia che la pagina si chiuda da sola)
            # chiude anche gli Edge rimasti in sottofondo con il profilo della finestra: altrimenti al prossimo
            # avvio Edge passa la finestra a loro e sembra che il programma si chiuda da solo
            close_app_windows()
            proc = getattr(self, 'app_proc', None)
            if proc is not None and proc.poll() is None:
                try:
                    import subprocess
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                                       creationflags=0x08000000, capture_output=True, timeout=5)
                    else:
                        proc.terminate()
                except Exception:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
            log('Uscita')
            hard_exit()
        threading.Thread(target=run, daemon=True).start()


def hard_exit(code=0):
    """Termina subito il processo.
    Su Windows os._exit passa dalla chiusura delle DLL caricate: perseususb.dll in quel momento
    aspetta il suo thread USB e il processo resta appeso (si vede in Gestione attività).
    TerminateProcess invece chiude e basta, come «Termina attività»."""
    try:
        flush_cfg()                              # impostazioni ancora da scrivere
    except Exception:
        pass
    if os.name == 'nt':
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.TerminateProcess(k.GetCurrentProcess(), code)
        except Exception:
            pass
    os._exit(code)


def find_app_browser():
    """Edge o Chrome per aprire l'interfaccia in una finestra propria (modalità app)."""
    cands = []
    for pf in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), os.environ.get('LOCALAPPDATA')):
        if pf:
            cands += [os.path.join(pf, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
                      os.path.join(pf, 'Google', 'Chrome', 'Application', 'chrome.exe')]
    return next((c for c in cands if os.path.exists(c)), None)


def open_app_window(url):
    """Apre la finestra e restituisce il processo da sorvegliare (None = browser normale)."""
    import subprocess
    exe = find_app_browser()
    if not exe:
        webbrowser.open(url)
        return None
    profile = os.path.join(app_dir(), 'finestra')      # profilo separato: il processo resta legato alla finestra
    os.makedirs(profile, exist_ok=True)
    return subprocess.Popen([exe, f'--app={url}', f'--user-data-dir={profile}', '--window-size=1400,920',
                             '--no-first-run', '--no-default-browser-check', '--disable-features=Translate',
                             '--autoplay-policy=no-user-gesture-required'])     # audio acceso già all'avvio


def close_app_windows():
    """Chiude i processi Edge/Chrome che usano il profilo della finestra di FZR Skimmer."""
    if os.name != 'nt':
        return
    import subprocess
    prof = os.path.join(app_dir(), 'finestra').replace("'", "''")
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\" | "
          f"Where-Object {{ $_.CommandLine -like '*{prof}*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}")
    try:
        subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                       creationflags=0x08000000, capture_output=True, timeout=6)
    except Exception:
        pass


def port_in_use(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        return s.connect_ex(('127.0.0.1', port)) == 0
    finally:
        s.close()


def running_version(port):
    """Versione del FZR Skimmer già acceso sulla porta (None se è uno vecchio, che non lo dice)."""
    import urllib.request
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # niente proxy per questo PC
        with op.open(f'http://127.0.0.1:{port}/versione', timeout=3) as r:
            v = r.read(32).decode('ascii', 'replace').strip()
            return v if re.fullmatch(r'[\d.]+', v) else None
    except Exception:
        return None


def close_old_instance(port):
    """Chiude il FZR Skimmer di un'altra versione rimasto acceso (aggiornando, il vecchio teneva la porta
    e la finestra nuova si collegava a lui). Solo un processo FZRSkimmer.exe, mai altri programmi."""
    if os.name != 'nt':
        return False
    import subprocess
    try:
        out = subprocess.run(['netstat', '-ano', '-p', 'TCP'], capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000).stdout
    except Exception:
        return False
    pids = {int(m.group(1)) for m in re.finditer(rf'[:.]{port}\s+\S+\s+LISTENING\s+(\d+)', out)}
    pids.discard(os.getpid())
    done = False
    for pid in pids:
        try:
            info = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'], capture_output=True,
                                  text=True, timeout=10, creationflags=0x08000000).stdout.lower()
        except Exception:
            continue
        if 'fzrskimmer' not in info:
            continue
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)], capture_output=True, timeout=10,
                       creationflags=0x08000000)
        done = True
    if done:
        subprocess.run(['taskkill', '/F', '/IM', 'rtl_tcp.exe'], capture_output=True, timeout=10,
                       creationflags=0x08000000)
        for _ in range(40):                              # la porta si libera
            if not port_in_use(port):
                break
            time.sleep(0.25)
        time.sleep(1.0)                                  # e la USB del ricevitore
    return done


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('192.168.1.1', 9))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser(description='FZR Skimmer - pile-up in split con il Perseus')
    p.add_argument('file', nargs='?', help='registrazione IQ .wav da riprodurre')
    p.add_argument('--centro', type=float, help='frequenza centrale della registrazione (kHz)')
    p.add_argument('--dx', type=float, help='frequenza del DX (kHz)')
    p.add_argument('--porta', type=int, default=8073)
    p.add_argument('--cartella', help='cartella con perseususb.dll e i file .sbs (es. quella di CW Skimmer)')
    p.add_argument('--sorgente', choices=['perseus', 'audio', 'iq', 'extio', 'rtl', 'pluto'], help='ricevitore: perseus, audio (uscita audio della radio), iq (IQ dalla scheda audio)')
    p.add_argument('--extio', action='store_true', help='usa ExtIO_perseus.dll invece del driver diretto')
    p.add_argument('--no-browser', action='store_true', help='non aprire nessuna finestra')
    p.add_argument('--browser', action='store_true', help='usa il browser normale invece della finestra propria')
    p.add_argument('--prova', type=float, nargs='?', const=0, default=None, metavar='SECONDI',
                   help='si comporta da non registrato (ignora la licenza) per provare la versione di prova; '
                        'con un numero la prova dura quei secondi (al massimo 5 minuti)')
    args = p.parse_args()
    if port_in_use(args.porta) and running_version(args.porta) != VERSION and close_old_instance(args.porta):
        log(f'Chiuso il FZR Skimmer di un\'altra versione rimasto acceso: parte la {VERSION}')
    if port_in_use(args.porta):
        # già acceso (la stessa versione): riapro solo la finestra
        if not args.no_browser:
            url = f'http://localhost:{args.porta}/'
            (webbrowser.open(url) if args.browser else open_app_window(url))
        return
    if args.file and not args.centro and sys.stdin and sys.stdin.isatty():
        # file trascinato sull'eseguibile: CW Skimmer non salva la frequenza, la chiedo
        try:
            v = input('Frequenza centrale della registrazione in kHz (es. 18085): ').strip().replace(',', '.')
            args.centro = float(v) if v else None
            v = input('Frequenza del DX in kHz (invio = uguale al centro): ').strip().replace(',', '.')
            args.dx = float(v) if v else args.centro
        except Exception:
            pass
    hub = Hub(args)
    try:
        asyncio.run(hub.main())
    except KeyboardInterrupt:
        pass
    finally:
        if hub.src:
            hub.src.stop()


if __name__ == '__main__':
    main()
