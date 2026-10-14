"""Split Watch - ricevitore per pile-up in split.

Uso:
  SplitWatch.exe                         Perseus dal vivo (ExtIO_perseus.dll nella stessa cartella)
  SplitWatch.exe registrazione.wav       riproduce una registrazione IQ
       --centro 18085.0                  frequenza centrale della registrazione in kHz (CW Skimmer non la salva)
       --dx 18085.0                      frequenza del DX in kHz
       --porta 8073                      porta dell'interfaccia web
       --cartella "C:\\HDSDR"            cartella con ExtIO_perseus.dll e perseususb.dll
       --no-browser                      non aprire il browser
L'interfaccia si apre su http://localhost:8073 (anche da tablet/telefono nella rete di casa).
"""
import argparse
import asyncio
import collections
import json
import os
import queue
import socket
import struct
import sys
import threading
import time
import webbrowser

import numpy as np
import websockets

from dsp import Engine
from sources import PerseusExtIO, WavFile

VERSION = '0.3'


def app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def res_path(name):
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


CFG_PATH = os.path.join(app_dir(), 'splitwatch.json')
LOG_PATH = os.path.join(app_dir(), 'splitwatch.log')


def log(msg):
    line = time.strftime('%H:%M:%S ') + str(msg)
    print(line, flush=True)
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def load_cfg():
    try:
        with open(CFG_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(cfg):
    try:
        with open(CFG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=1)
    except Exception:
        pass


class Hub:
    """Collega sorgente, motore DSP e browser."""

    def __init__(self, args):
        self.args = args
        self.cfg = load_cfg()
        self.loop = None
        self.clients = set()
        self.iq_q = queue.Queue(maxsize=400)
        self.cmd_q = queue.Queue()
        self.engine = None
        self.fs = None
        self.center = None
        self.dx_abs = None
        self.status = 'Avvio…'
        self.src = None
        self.src_kind = 'file' if args.file else 'perseus'
        self.rows_hist = collections.deque(maxlen=3000)    # ultimi 30 s di waterfall
        self.words_hist = collections.deque(maxlen=3000)
        self.listen_hist = collections.deque(maxlen=200)
        self.lock = threading.Lock()
        self.dropped = 0

    # -------------------------------------------------------------- sorgente
    def on_iq(self, iq):
        try:
            self.iq_q.put_nowait(iq)
        except queue.Full:
            self.dropped += 1

    def on_status(self, s):
        self.status = s
        log(s)
        self.post_json({'type': 'status', 'text': s})

    def on_rate(self, fs, center):
        self.cmd_q.put(('rate', float(fs), float(center)))

    def start_source(self):
        a = self.args
        if a.file:
            center = (a.centro * 1000) if a.centro else self.cfg.get('file_center', 0.0)
            self.src = WavFile(a.file, center, self.on_iq, self.on_status)
            if not a.centro and not self.src.center:
                self.on_status('Frequenza centrale sconosciuta: usa --centro (kHz). Mostro gli scostamenti dal centro.')
            self.on_rate(self.src.fs, self.src.center)
            self.src.start()
            self.on_status(f'Riproduzione: {os.path.basename(a.file)} · {self.src.fs/1000:.1f} kHz · {self.src.duration:.0f} s')
        else:
            folder = a.cartella or self.cfg.get('cartella') or app_dir()
            lo = self.cfg.get('lo', 21025000)
            self.src = PerseusExtIO(folder, lo, self.on_iq, self.on_status, self.on_rate)
            self.src.start()

    # -------------------------------------------------------------- DSP
    def dsp_thread(self):
        while True:
            try:
                self._dsp_loop()
            except Exception:
                import traceback
                log('ERRORE nel calcolo:\n' + traceback.format_exc())
                self.post_json({'type': 'status', 'text': 'Errore nel calcolo: vedi splitwatch.log'})
                time.sleep(1)

    def diag_thread(self):
        last = 0
        while True:
            time.sleep(3)
            n = getattr(self.src, 'nsamp', None)
            if n is None:
                continue
            rate = (n - last) / 3
            last = n
            q = self.iq_q.qsize()
            msg = f'dati: {rate/1000:.1f} k campioni/s · coda {q} · persi {self.dropped} · motore {"sì" if self.engine else "no"}'
            if rate == 0:
                msg = 'Nessun dato dal Perseus · ' + msg
            log(msg)
            self.post_json({'type': 'status', 'text': (self.status if rate else 'Nessun dato dal Perseus') + ' · ' + f'{rate/1000:.0f} k/s'})

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
                self.engine.feed(np.concatenate(chunks))
                r, au, w, l = self.engine.take()
                pend_rows += r
                pend_audio += au
                pend_words += w
                pend_listen += l
            now = time.perf_counter()
            if now - last_send >= 0.05 and self.engine is not None:
                last_send = now
                if pend_rows:
                    self._send_rows(pend_rows)
                    pend_rows = []
                if pend_audio:
                    a = np.concatenate(pend_audio)
                    self.post_bin(b'\x02' + struct.pack('<d', 0.0) + a.tobytes())
                    pend_audio = []
                if pend_words or pend_listen:
                    for w in pend_words:
                        w['fa'] = round(self.center + w['f'], 1)
                    for l in pend_listen:
                        l['fa'] = round(self.center + l['f'], 1)
                        l['t'] = float(l['t'])
                        l['f'] = float(l['f'])
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
            fs, center = c[1], c[2]
            log(f'campionamento {fs:.0f} Hz, centro {center:.0f} Hz')
            if fs <= 0:
                self.on_status('Campionamento sconosciuto: attendo i dati…')
                return
            if self.dx_abs is None:
                d = self.args.dx * 1000 if self.args.dx else self.cfg.get('dx')
                self.dx_abs = float(d) if d else center
            changed = (self.fs != fs)
            self.fs, self.center = fs, center
            if changed or self.engine is None:
                self.engine = Engine(fs, self.dx_abs - center)
                up = self.cfg.get('up', [0.15, 10.0])
                self.engine.up_lo, self.engine.up_hi = up[0] * 1000, up[1] * 1000
            else:
                self.engine.set_dx(self.dx_abs - center)
            with self.lock:
                self.rows_hist.clear()
            self.words_hist.clear()
            self.listen_hist.clear()
            if self.src_kind == 'perseus':
                self.cfg['lo'] = int(center)
                save_cfg(self.cfg)
            self.post_json(self.cfg_msg())
        elif c[0] == 'dx':
            self.dx_abs = float(c[1])
            if self.engine:
                self.engine.set_dx(self.dx_abs - self.center)
            self.cfg['dx'] = self.dx_abs
            save_cfg(self.cfg)
            self.post_json(self.cfg_msg())
        elif c[0] == 'up':
            lo, hi = sorted([float(c[1]), float(c[2])])
            if self.engine:
                self.engine.up_lo, self.engine.up_hi = lo * 1000, hi * 1000
            self.cfg['up'] = [lo, hi]
            save_cfg(self.cfg)
            self.post_json(self.cfg_msg())

    def cfg_msg(self):
        e = self.engine
        if e is None:
            return {'type': 'cfg', 'ready': False, 'status': self.status}
        return {'type': 'cfg', 'ready': True, 'fs': e.fs, 'center': self.center, 'dx': self.dx_abs,
                'nbins': e.nbins_out, 'bw': e.binhz * e.pool,
                'f0': (-e.nfft / 2 + (e.pool - 1) / 2) * e.binhz,
                'rowdt': 4.0 / 400.0, 'arate': e.arate, 'up': [e.up_lo / 1000, e.up_hi / 1000],
                'src': self.src_kind, 'status': self.status, 'version': VERSION}

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
                    self.cmd_q.put(('dx', float(c['hz'])))
                elif k == 'up':
                    self.cmd_q.put(('up', c['lo'], c['hi']))
                elif k == 'lo' and self.src:
                    self.src.set_lo(int(c['hz']))
                elif k == 'gui' and isinstance(self.src, PerseusExtIO):
                    self.src.show_gui()
        except Exception:
            pass
        finally:
            self.clients.discard(ws)

    async def http(self, path, headers):
        if path in ('/', '/index.html'):
            with open(res_path('index.html'), 'rb') as f:
                body = f.read()
            return (200, [('Content-Type', 'text/html; charset=utf-8'), ('Cache-Control', 'no-store')], body)
        if path == '/ws':
            return None
        if path == '/favicon.ico':
            return (204, [], b'')
        return (404, [('Content-Type', 'text/plain')], b'non trovato')

    async def main(self):
        self.loop = asyncio.get_running_loop()
        threading.Thread(target=self.dsp_thread, daemon=True).start()
        threading.Thread(target=self.diag_thread, daemon=True).start()
        log(f'Split Watch {VERSION} · Python {sys.version.split()[0]} {struct.calcsize("P")*8} bit · numpy {np.__version__}')
        try:
            self.start_source()
        except Exception as e:
            self.on_status(f'Errore: {e}')
        port = self.args.porta
        async with websockets.serve(self.handler, '0.0.0.0', port, process_request=self.http,
                                    max_size=2 ** 20, compression=None):
            url = f'http://localhost:{port}/'
            ip = local_ip()
            print(f'Split Watch {VERSION}: apri {url}' + (f'  (dal tablet: http://{ip}:{port}/)' if ip else ''), flush=True)
            if not self.args.no_browser:
                webbrowser.open(url)
            await asyncio.Future()


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
    p = argparse.ArgumentParser(description='Split Watch - pile-up in split')
    p.add_argument('file', nargs='?', help='registrazione IQ .wav da riprodurre')
    p.add_argument('--centro', type=float, help='frequenza centrale della registrazione (kHz)')
    p.add_argument('--dx', type=float, help='frequenza del DX (kHz)')
    p.add_argument('--porta', type=int, default=8073)
    p.add_argument('--cartella', help='cartella con ExtIO_perseus.dll e perseususb.dll')
    p.add_argument('--no-browser', action='store_true')
    args = p.parse_args()
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
