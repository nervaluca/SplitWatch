"""Comando della radio con Hamlib (rigctld) per FZR Skimmer.

FZR Skimmer non carica le librerie di Hamlib: parla con rigctld via rete (porta 4532), come
fanno WSJT-X e molti logger. Due modi:
  - "avvia": FZR Skimmer apre rigctld.exe con modello, porta COM e velocità scelti, e lo chiude all'uscita;
  - "collega": rigctld è già aperto (da un altro programma o a mano), FZR Skimmer si collega soltanto.
rigctld è un programma a sé: va bene sia a 32 sia a 64 bit.
"""
import os
import re
import socket
import subprocess
import sys
import threading
import time

DEF_PORT = 4532
POLL_S = 0.3


def find_rigctld(extra=()):
    """rigctld.exe nelle cartelle solite (Hamlib, WSJT-X, JTDX, programma)."""
    if sys.platform != 'win32':
        for d in os.environ.get('PATH', '').split(os.pathsep):
            p = os.path.join(d, 'rigctld')
            if os.path.isfile(p):
                return p
        return None
    names = ('rigctld.exe',)
    dirs = [d for d in extra if d]
    for base in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'), os.environ.get('LOCALAPPDATA')):
        if not base or not os.path.isdir(base):
            continue
        try:
            for n in os.listdir(base):
                nl = n.lower()
                if nl.startswith('hamlib'):
                    dirs.append(os.path.join(base, n, 'bin'))
                elif nl in ('wsjt-x', 'wsjtx', 'jtdx', 'js8call'):
                    dirs.append(os.path.join(base, n, 'bin'))
        except Exception:
            pass
    for d in dirs:
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return os.path.abspath(p)
    return None


def list_models(exe):
    """[(numero, 'Marca Modello')] da 'rigctld -l'."""
    if not exe:
        return []
    try:
        flags = 0x08000000 if sys.platform == 'win32' else 0       # CREATE_NO_WINDOW
        out = subprocess.run([exe, '-l'], capture_output=True, text=True, timeout=15, creationflags=flags).stdout
    except Exception:
        return []
    res = []
    for line in out.splitlines():
        m = re.match(r'^\s*(\d+)\s+(.+?)\s{2,}(.+?)\s{2,}', line)
        if m:
            res.append((int(m.group(1)), f'{m.group(2).strip()} {m.group(3).strip()}'))
    return res


def list_com_ports():
    if sys.platform != 'win32':
        return [p for p in ('/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyACM0') if os.path.exists(p)]
    out = []
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DEVICEMAP\SERIALCOMM')
        i = 0
        while True:
            try:
                out.append(str(winreg.EnumValue(k, i)[1]))
                i += 1
            except OSError:
                break
    except Exception:
        pass
    return sorted(set(out), key=lambda s: (len(s), s))


class Rig:
    """Client di rigctld con lettura continua (frequenza, modo, split) e comandi in coda."""

    def __init__(self, cfg, on_state=None, on_status=None):
        self.cfg = dict(cfg)
        self.on_state = on_state or (lambda st: None)
        self.on_status = on_status or (lambda s: None)
        self.proc = None
        self.sock = None
        self.f = None
        self.lock = threading.Lock()
        self.stop_ev = threading.Event()
        self.thread = None
        self.state = {'ok': False, 'f': None, 'mode': None, 'split': None, 'ftx': None, 'err': None}
        self.pending = {}                  # cosa abbiamo impostato noi: non è "la manopola della radio"

    # ------------------------------------------------------------ avvio
    def start(self):
        self.stop_ev.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_ev.set()
        self._close()
        p, self.proc = self.proc, None
        if p:
            try:
                p.terminate()
                p.wait(2)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def _port_busy(self):
        try:
            s = socket.create_connection(('127.0.0.1', int(self.cfg.get('tcp', DEF_PORT))), timeout=0.5)
            s.close()
            return True
        except Exception:
            return False

    def _spawn(self):
        if self._port_busy():
            # c'è già un rigctld sulla porta (rimasto da prima o di un altro programma): uso quello
            self.on_status('Radio: rigctld è già aperto, mi collego a quello')
            return
        exe = self.cfg.get('exe') or find_rigctld()
        if not exe or not os.path.isfile(exe):
            raise RuntimeError('rigctld.exe non trovato: installa Hamlib (32 o 64 bit) o indicane il percorso')
        model = int(self.cfg.get('model') or 0)
        port = self.cfg.get('com') or ''
        if not model:
            raise RuntimeError('scegli il modello della radio')
        args = [exe, '-m', str(model), '-t', str(int(self.cfg.get('tcp', DEF_PORT)))]
        if model > 2:                       # 1 = finta, 2 = rete: niente porta seriale
            if not port:
                raise RuntimeError('scegli la porta COM della radio')
            args += ['-r', port]
            if self.cfg.get('baud'):
                args += ['-s', str(int(self.cfg['baud']))]
        flags = 0x08000000 if sys.platform == 'win32' else 0
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     creationflags=flags, cwd=os.path.dirname(exe) or None)
        time.sleep(0.8)
        if self.proc.poll() is not None:
            err = (self.proc.stderr.read() or b'').decode(errors='ignore').strip().splitlines()
            raise RuntimeError('rigctld si è chiuso subito' + (f': {err[-1]}' if err else ''))

    def _connect(self):
        host = self.cfg.get('host') or '127.0.0.1'
        port = int(self.cfg.get('tcp', DEF_PORT))
        last = None
        for _ in range(20):
            try:
                s = socket.create_connection((host, port), timeout=3)
                s.settimeout(5)
                self.sock = s
                self.f = s.makefile('rw', newline='\n', encoding='ascii', errors='ignore')
                return
            except Exception as e:
                last = e
                if self.stop_ev.wait(0.3):
                    return
        raise RuntimeError(f'non riesco a collegarmi a rigctld su {host}:{port} ({last})')

    def _close(self):
        for o in (self.f, self.sock):
            try:
                if o:
                    o.close()
            except Exception:
                pass
        self.f = self.sock = None

    # ------------------------------------------------------------ protocollo
    def cmd(self, line):
        """Comando con risposta estesa: restituisce (codice RPRT, {chiave: valore})."""
        with self.lock:
            if not self.f:
                raise ConnectionError('radio non collegata')
            self.f.write('+\\' + line + '\n')
            self.f.flush()
            vals = {}
            while True:
                l = self.f.readline()
                if not l:
                    raise ConnectionError('rigctld ha chiuso il collegamento')
                l = l.strip()
                if l.startswith('RPRT'):
                    return int(l.split()[1]), vals
                if ':' in l:
                    k, v = l.split(':', 1)
                    if v.strip():
                        vals[k.strip()] = v.strip()

    def _set(self, key, line, value):
        self.pending[key] = (value, time.time())
        try:
            rc, _ = self.cmd(line)
            if rc != 0:
                self.on_status(f'Radio: comando «{line}» rifiutato (errore {rc})')
        except Exception as e:
            self.on_status(f'Radio: {e}')

    def set_freq(self, hz):
        threading.Thread(target=self._set, args=('f', f'set_freq {int(round(hz))}', int(round(hz))), daemon=True).start()

    def set_mode(self, mode):
        threading.Thread(target=self._set, args=('mode', f'set_mode {mode} 0', mode), daemon=True).start()

    def set_split_tx(self, hz):
        """Split acceso e TX (VFO B) sulla frequenza data."""
        def run():
            try:
                if self.state.get('split') != 1:
                    self.cmd('set_split_vfo 1 VFOB')
            except Exception as e:
                self.on_status(f'Radio: {e}')
            self._set('ftx', f'set_split_freq {int(round(hz))}', int(round(hz)))
        threading.Thread(target=run, daemon=True).start()

    def split_off(self):
        def run():
            try:
                if self.state.get('split'):
                    self.cmd('set_split_vfo 0 VFOA')
            except Exception as e:
                self.on_status(f'Radio: {e}')
        threading.Thread(target=run, daemon=True).start()

    def ours(self, key, value, tol=5):
        """True se il valore letto è quello che abbiamo appena impostato noi (entro 2 s)."""
        p = self.pending.get(key)
        if not p or time.time() - p[1] > 2.0:
            return False
        if isinstance(value, (int, float)) and isinstance(p[0], (int, float)):
            return abs(value - p[0]) <= tol
        return value == p[0]

    # ------------------------------------------------------------ ciclo
    def _run(self):
        wait = 2
        while not self.stop_ev.is_set():
            try:
                if self.cfg.get('modo', 'avvia') == 'avvia' and (self.proc is None or self.proc.poll() is not None):
                    self._spawn()
                    self.cfg['host'] = '127.0.0.1'
                self._connect()
                if self.stop_ev.is_set():
                    break
                rc, v = self.cmd('get_freq')
                if rc != 0:
                    raise RuntimeError(f'la radio non risponde (errore {rc}): porta, velocità o modello sbagliati?')
                self.on_status('Radio collegata' + (f' ({self.cfg.get("nome")})' if self.cfg.get('nome') else ''))
                wait = 2
                n = 0
                while not self.stop_ev.is_set():
                    st = dict(self.state)
                    rc, v = self.cmd('get_freq')
                    if rc == 0 and 'Frequency' in v:
                        st['f'] = int(float(v['Frequency']))
                    if n % 3 == 0:
                        rc, v = self.cmd('get_mode')
                        if rc == 0:
                            st['mode'] = v.get('Mode')
                        rc, v = self.cmd('get_split_vfo')
                        if rc == 0:
                            st['split'] = int(v.get('Split', 0) or 0)
                        if st['split']:
                            rc, v = self.cmd('get_split_freq')
                            if rc == 0 and 'TX Frequency' in v:
                                st['ftx'] = int(float(v['TX Frequency']))
                        else:
                            st['ftx'] = None
                    st['ok'], st['err'] = True, None
                    if st != self.state:
                        self.state = st
                        self.on_state(dict(st))
                    n += 1
                    self.stop_ev.wait(POLL_S)
            except Exception as e:
                if self.stop_ev.is_set():
                    break
                self.state = {'ok': False, 'f': None, 'mode': None, 'split': None, 'ftx': None, 'err': str(e)}
                self.on_state(dict(self.state))
                if wait == 2:
                    self.on_status(f'Radio: {e}')
                self._close()
                if self.proc and self.proc.poll() is None and 'non risponde' in str(e):
                    pass                                     # rigctld vivo ma radio muta: riprovo senza riaprirlo
                self.stop_ev.wait(wait)
                wait = min(30, wait * 2)
        self._close()
