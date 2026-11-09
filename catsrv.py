"""Server CAT per WSJT-X/JTDX: parla il protocollo di rigctld (Hamlib «NET rigctl»).

In WSJT-X: Radio = «Hamlib NET rigctl», Network Server = 127.0.0.1:4533, PTT = «CAT».
Così WSJT-X legge la frequenza vera di FZR SDR (anche 10489,540 MHz su QO-100), la può cambiare
e comanda il PTT. L'audio passa sempre dai cavi virtuali del ponte."""
import socket
import threading

# risposta a \\dump_state come rigctld 4.5 (rig «dummy»), con la gamma estesa a 11 GHz per QO-100
DUMP_STATE = """1
1
0
10000.000000 11000000000.000000 0x1ff -1 -1 0x77e00007 0xf
0 0 0 0 0 0 0
10000.000000 11000000000.000000 0x1ff 5000 100000 0x77e00007 0xf
0 0 0 0 0 0 0
0x1ff 1
0x1ff 0
0 0
0xc 2400
0xc 1800
0xc 3000
0xc 0
0x2 500
0x2 2400
0x2 50
0x2 0
0x10 300
0x10 2400
0x10 50
0x10 0
0x1 8000
0x1 2400
0x1 10000
0x20 15000
0x20 8000
0x40 230000
0 0
9990
9990
10000
0
10 
10 20 30 
0xffffffffffffffff
0xffffffffffffffff
0xfffffffff7ffffff
0xffffff7083ffffff
0xffffffffffffffff
0xffffffffffffffbf
vfo_ops=0x0
ptt_type=0x1
targetable_vfo=0x0
has_set_vfo=0
has_get_vfo=1
has_set_freq=1
has_get_freq=1
has_set_conf=0
has_get_conf=0
has_power2mW=0
has_mW2power=0
timeout=0
rig_model=2
rigctld_version=FZR SDR
done
"""

LONG = {'get_freq': 'f', 'set_freq': 'F', 'get_mode': 'm', 'set_mode': 'M', 'get_vfo': 'v', 'set_vfo': 'V',
        'get_ptt': 't', 'set_ptt': 'T', 'get_split_vfo': 's', 'set_split_vfo': 'S', 'get_split_freq': 'i',
        'set_split_freq': 'I', 'get_split_mode': 'x', 'set_split_mode': 'X', 'get_rit': 'j', 'set_rit': 'J',
        'get_xit': 'z', 'set_xit': 'Z', 'get_level': 'l', 'set_level': 'L', 'get_func': 'u', 'set_func': 'U'}


class RigctlServer:
    """get_freq() → Hz; set_freq(hz); get_ptt() → bool; set_ptt(bool); get_mode() → 'USB'."""

    def __init__(self, port, get_freq, set_freq, get_ptt, set_ptt, get_mode=lambda: 'USB', log=print):
        self.port = int(port)
        self.get_freq, self.set_freq = get_freq, set_freq
        self.get_ptt, self.set_ptt = get_ptt, set_ptt
        self.get_mode = get_mode
        self.log = log
        self.alive = False
        self.srv = None
        self.clients = 0

    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('127.0.0.1', self.port))
        s.listen(4)
        self.srv = s
        self.alive = True
        threading.Thread(target=self._accept, daemon=True).start()

    def stop(self):
        self.alive = False
        try:
            self.srv.close()
        except Exception:
            pass

    def _accept(self):
        while self.alive:
            try:
                c, a = self.srv.accept()
            except OSError:
                break
            threading.Thread(target=self._client, args=(c,), daemon=True).start()

    def _client(self, c):
        self.clients += 1
        self.log('CAT: WSJT-X collegato')
        f = c.makefile('rb')
        try:
            for raw in f:
                if not self.alive:
                    break
                line = raw.decode('ascii', 'replace').strip()
                if not line:
                    continue
                out = self.handle(line)
                if out is None:
                    break
                c.sendall(out.encode('ascii'))
        except Exception:
            pass
        finally:
            self.clients -= 1
            try:
                c.close()
            except Exception:
                pass
            self.log('CAT: WSJT-X scollegato')

    def handle(self, line):
        ext = ''
        if line[0] in '+;|,':                       # risposta estesa: non serve a WSJT-X, la tratto normale
            line = line[1:]
        parts = line.split()
        cmd, args = parts[0], parts[1:]
        if cmd.startswith('\\'):
            name = cmd[1:]
            cmd = LONG.get(name, cmd)
        ok = 'RPRT 0\n'
        try:
            if cmd in ('q', 'Q', '\\quit'):
                return None
            if cmd == '\\dump_state':
                return DUMP_STATE
            if cmd == '\\chk_vfo':
                return '0\n'
            if cmd == '\\get_powerstat':
                return '1\n'
            if cmd == '\\get_lock_mode':
                return '0\n'
            if cmd in ('\\get_vfo_info',):
                return f'{int(round(self.get_freq()))}\n{self.get_mode()}\n3000\n0\n0\n'
            if cmd == 'f':
                return f'{int(round(self.get_freq()))}\n'
            if cmd == 'F':
                v = args[-1] if args else '0'
                self.set_freq(float(v))
                return ok
            if cmd == 'm':
                return f'{self.get_mode()}\n3000\n'
            if cmd == 'v':
                return 'VFOA\n'
            if cmd == 't':
                return f'{1 if self.get_ptt() else 0}\n'
            if cmd == 'T':
                v = args[-1] if args else '0'
                self.set_ptt(v not in ('0',))
                return ok
            if cmd == 's':
                return '0\nVFOA\n'
            if cmd == 'i':
                return f'{int(round(self.get_freq()))}\n'
            if cmd == 'x':
                return f'{self.get_mode()}\n3000\n'
            if cmd in ('j', 'z'):
                return '0\n'
            if cmd in ('M', 'V', 'S', 'I', 'X', 'J', 'Z', 'L', 'U', '\\set_powerstat', '\\set_lock_mode',
                       '\\send_morse', '\\stop_morse', '\\set_trn'):
                return ok
            if cmd in ('l', 'u'):
                return '0\n'
            return 'RPRT -11\n'                     # non implementato: Hamlib lo accetta
        except Exception:
            return 'RPRT -1\n'
