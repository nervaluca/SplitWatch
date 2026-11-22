"""Manopola di sintonia per FZR SDR: ESP32 con encoder, collegato con la USB (porta COM).

Il firmware è in manopola_esp32/manopola_esp32.ino. Protocollo (righe di testo, 115200 baud):
  ESP32 → PC   'FZRKNOB 1 <tipo>'   presentazione (all'avvio e in risposta a '?')
               'E+3' / 'E-1'         scatti girati dall'ultimo invio
               'K1S' / 'K1L'         tasto 1 breve / lungo (K2, K3 tasti in più)
  PC → ESP32   '?'                   chi sei?
               'S A=… B=… P=… V=A L=0 O=CW'   stato da mostrare (manopola con display, es. CYD)

Qui si contano solo scatti e velocità: cosa muovere (VFO A o B), il passo e il blocco del DX li decide la
pagina, che ha già le stesse regole della rotellina del mouse.
"""
import collections
import threading
import time

BAUD = 115200
FLUSH_S = 0.025          # gli scatti arrivati si mandano alla pagina al massimo 40 volte al secondo
SPEED_WIN = 0.15         # finestra per la velocità (scatti al secondo) che decide l'accelerazione
RETRY_S = 3.0            # porta assente o cavo staccato: riprovo ogni 3 s


class Manopola:
    def __init__(self, port, on_turn, on_key, on_state, inv=False):
        """on_turn(n, v): n scatti (+ orario), v velocità in scatti/s · on_key(k, lungo) · on_state()."""
        self.port = port
        self.inv = bool(inv)
        self.on_turn, self.on_key, self.on_state = on_turn, on_key, on_state
        self.alive = True
        self.ok = False                 # porta aperta
        self.err = ''
        self.hello = ''                 # tipo di encoder dichiarato dal firmware ('' = non ha ancora risposto)
        self.pagina = 0                 # pagina mostrata dal display: 0 manopola · 1 decodifiche FT8
        self.s = None
        self.wlock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def close(self):
        self.alive = False
        s = self.s
        if s is not None:
            try:
                s.close()
            except Exception:
                pass

    def write(self, line):
        """Manda una riga all'ESP32 (stato per il display). Senza porta aperta non fa nulla."""
        s = self.s
        if s is None or not self.hello:
            return
        try:
            with self.wlock:
                s.write(line.encode('ascii', 'ignore') + b'\n')
        except Exception:
            pass

    def _set(self, ok, err=''):
        if (ok, err) != (self.ok, self.err):
            self.ok, self.err = ok, err
            try:
                self.on_state()
            except Exception:
                pass

    def _open(self):
        import serial
        s = serial.Serial()
        s.port = self.port
        s.baudrate = BAUD
        s.timeout = 0.01
        s.write_timeout = 0.05          # se la porta si inchioda lo stato per il display non blocca il programma
        s.dtr = False                   # DTR e RTS spenti: aprendo la porta l'ESP32 non si resetta
        s.rts = False
        s.open()
        return s

    def _sleep(self, t):
        end = time.time() + t
        while self.alive and time.time() < end:
            time.sleep(0.1)

    def _run(self):
        while self.alive:
            try:
                s = self._open()
            except Exception as e:
                self._set(False, str(e))
                self._sleep(RETRY_S)
                continue
            self.s = s
            self.hello = ''
            self.pagina = 0
            self._set(True)
            try:
                with self.wlock:
                    s.write(b'?\n')
                self._loop(s)
            except Exception as e:
                if self.alive:
                    self._set(False, f'scollegata ({e})')
            finally:
                try:
                    s.close()
                except Exception:
                    pass
                self.s = None
            self._sleep(RETRY_S)

    def _loop(self, s):
        buf = b''
        pend = 0
        hist = collections.deque()      # (istante, scatti) per la velocità
        t_flush = time.time()
        t_open = t_flush
        asked = False
        while self.alive:
            data = s.read(256)
            now = time.time()
            if data:
                buf += data
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    t = line.decode('ascii', 'ignore').strip()
                    if t.startswith('E') and len(t) > 1:
                        try:
                            n = int(t[1:])
                        except ValueError:
                            continue
                        n = -n if self.inv else n
                        pend += n
                        hist.append((now, abs(n)))
                    elif len(t) == 3 and t[0] == 'K' and t[2] in 'SL':
                        self.on_key(t[1], t[2] == 'L')
                    elif t.startswith('PG'):                 # pagina mostrata dal display
                        try:
                            self.pagina = int(t.split()[1])
                        except (IndexError, ValueError):
                            self.pagina = 0
                        self.on_state()                  # aprendo le decodifiche gli mando quelle già prese
                    elif t.startswith('FZRKNOB'):
                        parts = t.split()
                        self.hello = parts[2] if len(parts) > 2 else '?'
                        self.on_state()
                if len(buf) > 4096:
                    buf = b''                # spazzatura (velocità sbagliata): ricomincio
            if not self.hello and not asked and now - t_open > 2.0:
                asked = True                 # forse l'ESP32 stava già girando: richiedo la presentazione
                with self.wlock:
                    s.write(b'?\n')
            if pend and now - t_flush >= FLUSH_S:     # dopo una pausa il primo scatto parte subito
                while hist and now - hist[0][0] > SPEED_WIN:
                    hist.popleft()
                v = sum(n for _, n in hist) / SPEED_WIN
                self.on_turn(pend, round(v, 1))
                pend = 0
                t_flush = now
