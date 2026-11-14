"""«Che segnale è?»: riconosce il modo di un segnale dal waterfall, più il bandplan del QO-100.

Si guardano pochi secondi di IQ attorno al punto cliccato e si misurano cose semplici e robuste:
  - larghezza occupata e forma dello spettro medio (piatto con bordi netti = OFDM; a cupola = PSK a portante
    singola; righe = toni FSK; irregolare = voce);
  - comportamento nel tempo: acceso/spento (CW), sempre acceso (beacon, modem), a sillabe (SSB);
  - quanti toni diversi e a che passo (RTTY 170 Hz, FT8 6,25 Hz, FT4 20,8 Hz, MFSK/Olivia);
  - righe nell'inviluppo (PSK31 a 31,25 Hz).
Le frequenze note del QO-100 (beacon) hanno la precedenza. Solo numpy.
"""
import numpy as np

# ------------------------------------------------------------------ bandplan AMSAT-DL (transponder stretto)
# (da MHz, a MHz, chiave, nome it, nome en, larghezza massima Hz, modi ammessi)
BANDPLAN = [
    (10489.500, 10489.505, 'beacon_lo', 'Beacon CW basso (guardia)', 'Lower CW beacon (guard)', 0, ()),
    (10489.505, 10489.540, 'cw', 'Solo CW', 'CW only', 500, ('cw',)),
    (10489.540, 10489.580, 'nbdig', 'Digitale stretto (≤ 500 Hz)', 'Narrowband digital (≤ 500 Hz)', 500, ('digi', 'rtty')),
    (10489.580, 10489.650, 'dig', 'Digitale (≤ 2,7 kHz)', 'Digital (≤ 2.7 kHz)', 2700, ('digi', 'rtty')),
    (10489.650, 10489.745, 'ssb', 'Solo SSB', 'SSB only', 2700, ('ssb',)),
    (10489.745, 10489.755, 'beacon_mid', 'Beacon centrale PSK (guardia)', 'Middle PSK beacon (guard)', 0, ()),
    (10489.755, 10489.850, 'ssb', 'Solo SSB', 'SSB only', 2700, ('ssb',)),
    (10489.850, 10489.858, 'bcast', 'Broadcast (riservato)', 'Broadcast (reserved)', 0, ()),
    (10489.858, 10489.865, 'emerg', 'Emergenza (riservato)', 'Emergency (reserved)', 0, ()),
    (10489.865, 10489.990, 'mixed', 'Modi misti e contest', 'Mixed modes and contests', 2700, ('cw', 'ssb', 'digi', 'rtty')),
    (10489.990, 10489.997, 'beacon_mm', 'Beacon multimediale (guardia)', 'Multimedia beacon (guard)', 0, ()),
    (10489.997, 10490.000, 'beacon_hi', 'Beacon CW alto (guardia)', 'Upper CW beacon (guard)', 0, ()),
]
QO_LO, QO_HI = 10489.500e6, 10490.000e6
MODE_IT = {'cw': 'CW', 'ssb': 'SSB', 'digi': 'digitale', 'rtty': 'RTTY'}
MODE_EN = {'cw': 'CW', 'ssb': 'SSB', 'digi': 'digital', 'rtty': 'RTTY'}


def on_qo(hz):
    return 10489.0e6 <= hz <= 10490.5e6


def segment(hz):
    """Il tratto del bandplan per una frequenza di discesa (Hz), o None se fuori dal transponder."""
    mhz = hz / 1e6
    for a, b, k, it, en, bw, modes in BANDPLAN:
        if a <= mhz < b:
            return {'key': k, 'it': it, 'en': en, 'bw': bw, 'modes': modes, 'lo': a * 1e6, 'hi': b * 1e6}
    return None


def tx_check(lo_hz, hi_hz, mode):
    """Avviso di bandplan per una trasmissione che occupa la discesa da lo_hz a hi_hz, nel modo
    'cw' | 'ssb' | 'digi' | 'rtty'. Torna (it, en) oppure None se è tutto a posto. Solo avvisi: non blocca."""
    if hi_hz < QO_LO:
        return ('sotto il beacon basso (10489,500): fuori dal transponder',
                'below the lower beacon (10489.500): outside the transponder')
    if lo_hz > QO_HI:
        return ('sopra il beacon alto (10490,000): fuori dal transponder',
                'above the upper beacon (10490.000): outside the transponder')
    if lo_hz < QO_LO or hi_hz > QO_HI:
        return ('il segnale sborda oltre un beacon', 'the signal spills past a beacon')
    segs, seen = [], set()
    f = lo_hz
    while f <= hi_hz:                                        # i tratti toccati dal segnale
        s = segment(f)
        if s and s['lo'] not in seen:
            seen.add(s['lo'])
            segs.append(s)
        f += 25.0
    for s in segs:
        if s['bw'] == 0:
            return (f'dentro «{s["it"]}»: lascia libero', f'inside «{s["en"]}»: keep it clear')
    for s in segs:
        if mode not in s['modes']:
            return (f'{MODE_IT[mode]} nel tratto «{s["it"]}»', f'{MODE_EN[mode]} in the «{s["en"]}» segment')
    if len(segs) > 1:
        return ('il segnale sta a cavallo di due tratti del bandplan', 'the signal straddles two bandplan segments')
    return None


# ------------------------------------------------------------------ strumenti
def _band(iq, fs, df, half):
    """Porta df (Hz rispetto al centro dell'IQ) a 0 e tiene ±half Hz: FFT, taglio, FFT inversa.
    Torna (segnale complesso, nuova fs)."""
    n = len(iq)
    X = np.fft.fft(iq)
    k0 = int(round(df * n / fs))
    m = int(round(2 * half * n / fs))
    m += m % 2
    idx = (k0 + np.arange(-m // 2, m // 2)) % n
    Y = np.fft.ifftshift(X[idx])
    return np.fft.ifft(Y) * (m / n), m / (n / fs)


def _welch(x, fs, nper):
    nper = min(nper, len(x))
    hop = nper // 2
    w = np.hanning(nper)
    acc = np.zeros(nper)
    k = 0
    for i in range(0, len(x) - nper + 1, hop):
        acc += np.abs(np.fft.fft(x[i:i + nper] * w)) ** 2
        k += 1
    acc = np.fft.fftshift(acc / max(k, 1))
    f = np.fft.fftshift(np.fft.fftfreq(nper, 1 / fs))
    return f, acc


def _stft(x, nfft, hop):
    w = np.hanning(nfft)
    rows = [np.fft.fftshift(np.abs(np.fft.fft(x[i:i + nfft] * w)) ** 2) for i in range(0, len(x) - nfft + 1, hop)]
    return np.array(rows) if rows else np.zeros((0, nfft))


def _smooth(v, k):
    if k <= 1:
        return v
    return np.convolve(v, np.ones(k) / k, mode='same')


def _cluster(vals, gap):
    """Raggruppa valori ordinati che distano meno di gap: torna i centri dei gruppi (con almeno 2 elementi)."""
    if not len(vals):
        return []
    v = np.sort(vals)
    out, cur = [], [v[0]]
    for a in v[1:]:
        if a - cur[-1] <= gap:
            cur.append(a)
        else:
            out.append(cur)
            cur = [a]
    out.append(cur)
    tot = len(v)
    return [float(np.mean(c)) for c in out if len(c) >= max(2, 0.03 * tot)]


# ------------------------------------------------------------------ riconoscimento
KNOWN = [  # frequenze del QO-100 riconosciute subito (Hz di discesa, tolleranza)
    (10489.500e6, 900, 'beacon_cw_lo'), (10489.750e6, 1300, 'beacon_psk'),
    (10489.9935e6, 2200, 'beacon_mm'), (10490.000e6, 900, 'beacon_cw_hi'),
]

INFO = {
    # chiave: (nome it, nome en, come si ascolta/decodifica it, en)
    'beacon_cw_lo': ('Beacon CW basso del QO-100', 'QO-100 lower CW beacon',
                     'Riferimento per l’aggancio: CW a spostamento di 400 Hz.', 'Lock reference: CW with a 400 Hz shift.'),
    'beacon_cw_hi': ('Beacon CW alto del QO-100', 'QO-100 upper CW beacon',
                     'Riferimento per l’aggancio: CW a spostamento di 400 Hz.', 'Lock reference: CW with a 400 Hz shift.'),
    'beacon_psk': ('Beacon centrale: BPSK 400 bit/s', 'Middle beacon: BPSK 400 bit/s',
                   'Generato a terra con riferimento GPS: notizie e dati di servizio di AMSAT-DL.',
                   'Generated on the ground with a GPS reference: AMSAT-DL news and service data.'),
    'beacon_mm': ('Beacon multimediale: 8APSK 7200 bit/s', 'Multimedia beacon: 8APSK 7200 bit/s',
                  'Immagini e bollettini: si ricevono col programma QO-100 Modem di DJ0ABR / AMSAT-DL.',
                  'Pictures and bulletins: received with the QO-100 Modem program by DJ0ABR / AMSAT-DL.'),
    'carrier': ('Portante pura', 'Plain carrier', 'Qualcuno che regola il TX o l’amplificatore.',
                'Someone tuning their TX or amplifier.'),
    'cw': ('CW', 'CW', 'Ascoltalo in CW: il VFO A sulla traccia, oppure lo skimmer.', 'Listen in CW: VFO A on the trace, or the skimmer.'),
    'ft8': ('FT8 (o JS8Call)', 'FT8 (or JS8Call)', 'Si decodifica con WSJT-X (o JS8Call) col ponte 🔗 WSJT-X.',
            'Decode it with WSJT-X (or JS8Call) through the 🔗 WSJT-X bridge.'),
    'ft4': ('FT4', 'FT4', 'Si decodifica con WSJT-X col ponte 🔗 WSJT-X.', 'Decode it with WSJT-X through the 🔗 WSJT-X bridge.'),
    'rtty': ('RTTY', 'RTTY', 'Si decodifica in FZR SDR: modo RTTY.', 'FZR SDR decodes it: RTTY mode.'),
    'psk31': ('PSK31', 'PSK31', 'Serve fldigi, tramite il ponte audio.', 'Needs fldigi, through the audio bridge.'),
    'psk63': ('PSK63 / PSK125', 'PSK63 / PSK125', 'Serve fldigi, tramite il ponte audio.', 'Needs fldigi, through the audio bridge.'),
    'mfsk': ('MFSK (Olivia, Contestia, MFSK16, Thor…)', 'MFSK (Olivia, Contestia, MFSK16, Thor…)',
             'Serve fldigi, tramite il ponte audio.', 'Needs fldigi, through the audio bridge.'),
    'ssb': ('Fonia SSB', 'SSB voice', 'Ascoltala in SSB.', 'Listen to it in SSB.'),
    'freedv': ('Voce digitale FreeDV (OFDM)', 'FreeDV digital voice (OFDM)',
               'Serve il programma FreeDV, tramite il ponte audio.', 'Needs the FreeDV program, through the audio bridge.'),
    'drm': ('SSTV digitale DRM (EasyPal, KG-STV) o modem OFDM', 'Digital SSTV DRM (EasyPal, KG-STV) or OFDM modem',
            'Serve EasyPal o QSSTV, tramite il ponte audio.', 'Needs EasyPal or QSSTV, through the audio bridge.'),
    'ofdm': ('Modem OFDM', 'OFDM modem', 'Un modem dati multiportante: FreeDV, DRM, VARA o simili.',
             'A multicarrier data modem: FreeDV, DRM, VARA or similar.'),
    'hsmodem': ('Modem PSK ad alta velocità (QO-100 Modem, DJ0ABR)', 'High-speed PSK modem (QO-100 Modem, DJ0ABR)',
                'Foto e file: programma QO-100 Modem di DJ0ABR / AMSAT-DL.', 'Pictures and files: QO-100 Modem program by DJ0ABR / AMSAT-DL.'),
    'psk_wide': ('Dati PSK a portante singola', 'Single-carrier PSK data',
                 'Un modem PSK: forse QO-100 Modem (DJ0ABR) o un esperimento.', 'A PSK modem: maybe QO-100 Modem (DJ0ABR) or an experiment.'),
    'fm': ('FM (non ammessa sul transponder stretto)', 'FM (not allowed on the narrowband transponder)',
           'Il bandplan vieta FM e segnali oltre 2,7 kHz.', 'The bandplan forbids FM and signals wider than 2.7 kHz.'),
    'wide': ('Segnale largo oltre 2,7 kHz', 'Signal wider than 2.7 kHz', 'Fuori dal bandplan del transponder stretto.',
             'Outside the narrowband transponder bandplan.'),
    'unknown': ('Non riconosciuto con sicurezza', 'Not recognised with confidence', '', ''),
}


def identify(iq, fs, center, hz):
    """iq: campioni complessi recenti (qualche secondo) a fs, centrati su center (Hz assoluti).
    hz: frequenza cliccata (Hz assoluti). Torna un dizionario pronto per la finestrella."""
    out = {'hz': float(hz)}
    seg = segment(hz) if on_qo(hz) else None
    if seg:
        out['seg_it'], out['seg_en'] = seg['it'], seg['en']
    n = len(iq)
    if n < fs * 1.0:
        return dict(out, key='none', it='Pochi dati: riprova fra un attimo', en='Not enough data: try again in a moment')
    if abs(hz - center) > fs / 2 - 4000:
        return dict(out, key='none', it='Fuori dalla banda ricevuta', en='Outside the received band')
    for f0, tol, key in KNOWN:
        if on_qo(hz) and abs(hz - f0) <= tol:
            return _result(out, key, 0.95, {})

    # banda di ±8 kHz attorno al clic, a ~16 kS/s
    y, fs2 = _band(np.asarray(iq, np.complex64), fs, hz - center, 8000.0)
    f, P = _welch(y, fs2, int(fs2 / 4))                       # risoluzione 4 Hz
    df = f[1] - f[0]
    Pd = 10 * np.log10(P + 1e-20)
    floor = float(np.median(Pd))
    Ps = 10 * np.log10(_smooth(P, max(1, int(20 / df))) + 1e-20)   # liscio su ~20 Hz: le righe FSK si uniscono
    ipk = None
    for span in (200, 1500):                                  # prima vicino al clic (FT8 affollato), poi più largo
        near = np.abs(f) <= span
        j = int(np.argmax(np.where(near, Ps, -1e9)))
        if Ps[j] - floor >= 6:
            ipk = j
            break
    if ipk is None:
        ipk = j
    peak = float(Ps[ipk])
    snr = peak - floor
    if snr < 6:
        return dict(out, key='none', it='Nessun segnale chiaro qui in questo momento', en='No clear signal here right now',
                    note_it='Riprova mentre trasmette (in FT8 c’è una pausa ogni 15 s).',
                    note_en='Try again while it is transmitting (FT8 pauses every 15 s).')
    gap_ok = int(60 / df)                                     # salto buchi fino a 60 Hz (toni RTTY/MFSK)

    def grow(thr, Ps=Ps):
        lo = hi = ipk
        while lo > 0:
            k = lo - 1
            while k > 0 and Ps[k] < thr and lo - k <= gap_ok:
                k -= 1
            if Ps[k] >= thr and lo - k <= gap_ok:
                lo = k
            else:
                break
        while hi < len(Ps) - 1:
            k = hi + 1
            while k < len(Ps) - 1 and Ps[k] < thr and k - hi <= gap_ok:
                k += 1
            if Ps[k] >= thr and k - hi <= gap_ok:
                hi = k
            else:
                break
        return lo, hi
    lo, hi = grow(floor + max(6.0, snr - 20.0))              # larghezza «utile»: −20 dB dal picco
    Pw = 10 * np.log10(_smooth(P, max(1, int(200 / df))) + 1e-20)   # liscio su 200 Hz: si vede lo zoccolo debole
    lo6, hi6 = grow(float(np.median(Pw)) + 3.0, Pw)           # tutto lo zoccolo sopra il rumore (FM: portante + bande)
    bw6 = float(f[hi6] - f[lo6])
    flo, fhi = float(f[lo]), float(f[hi])
    bw = max(df, fhi - flo)
    fc = 0.5 * (flo + fhi)

    # forma dello spettro dentro la banda occupata
    inner = (f >= flo + 0.12 * bw) & (f <= fhi - 0.12 * bw)
    Pin = P[inner] if np.count_nonzero(inner) > 4 else P[lo:hi + 1]
    flat = float(np.exp(np.mean(np.log(Pin + 1e-20))) / (np.mean(Pin) + 1e-20))
    plateau = float(np.median(Pd[inner])) if np.count_nonzero(inner) > 4 else peak
    # inviluppo: 4º momento normalizzato del segnale nella sua banda, tolto il contributo del rumore.
    # OFDM ≈ 2 (gaussiano), PSK a portante singola 1,2–1,6, toni FSK 1, voce > 2,5 (a raffiche)
    yb, fsb = _band(y, fs2, fc, bw / 2 + 15)
    pw = np.abs(yb) ** 2
    Pt = float(np.mean(pw))
    Pn = float(np.median(P)) * (bw + 30) / df          # potenza di rumore nella banda (stessa scala di P)
    Psum = float(np.sum(P[(f >= fc - bw / 2 - 15) & (f <= fc + bw / 2 + 15)]))
    rn = min(0.95, Pn / max(Psum, 1e-30))                    # frazione di rumore nella banda
    E2 = Pt
    E4 = float(np.mean(pw ** 2))
    pn, ps = rn * E2, (1 - rn) * E2
    m4 = (E4 - 4 * ps * pn - 2 * pn * pn) / max(ps * ps, 1e-30)

    # nel tempo: energia nella banda occupata, fotogrammi di 16 ms
    sel = (f >= flo - 10) & (f <= fhi + 10)
    nf = 256
    S = _stft(y, nf, nf // 2)                                 # 62,5 Hz di risoluzione a 16 kS/s
    fs_ = np.fft.fftshift(np.fft.fftfreq(nf, 1 / fs2))
    bsel = (fs_ >= flo - 40) & (fs_ <= fhi + 40)
    if not np.any(bsel):
        bsel = np.abs(fs_ - fc) <= 40
    e = S[:, bsel].sum(1) if len(S) else np.zeros(1)
    nfl = np.median(S) * np.count_nonzero(bsel) if len(S) else 1.0
    edb = 10 * np.log10(e / (nfl + 1e-20) + 1e-12)
    on = np.zeros(len(edb), bool)                             # isteresi: si accende sopra 8 dB, si spegne sotto 4
    st_ = False
    for i, v in enumerate(edb):
        st_ = v > 8 if not st_ else v > 4
        on[i] = st_
    duty = float(on.mean()) if len(on) else 0.0
    secs = len(y) / fs2
    fr_s = (nf // 2) / fs2                                     # durata di un fotogramma
    runs, cur = [], 0
    for v in on:
        if v:
            cur += 1
        elif cur:
            runs.append(cur * fr_s)
            cur = 0
    if cur:
        runs.append(cur * fr_s)
    short = [r_ for r_ in runs if 0.02 <= r_ <= 0.6]          # punti e linee del CW
    rate = len(short) / max(secs, 1e-3)                        # elementi CW al secondo
    long_on = sum(r_ for r_ in runs if r_ > 1.0) / max(secs, 1e-3)
    # inviluppo nel tempo mentre è acceso (variazione relativa)
    if np.any(on):
        eo = e[on]
        evar = float(np.std(eo) / (np.mean(eo) + 1e-20))
    else:
        evar = 0.0

    feats = {'bw': round(bw), 'snr': round(snr), 'flat': round(flat, 2), 'm4': round(m4, 2),
             'duty': round(duty, 2), 'rate': round(rate, 1), 'evar': round(evar, 2)}

    # toni dominanti (per FSK): STFT fine attorno al segnale
    def tones(nfft, hop):
        T = _stft(y, nfft, hop)
        if not len(T):
            return [], 0.0
        ff = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / fs2))
        m = (ff >= flo - 15) & (ff <= fhi + 15)
        if np.count_nonzero(m) < 2:
            return [], 0.0
        Tm = T[:, m]
        fr = ff[m]
        tot = Tm.sum(1)
        good = tot > np.percentile(tot, 40)
        pk = fr[np.argmax(Tm[good], 1)] if np.any(good) else np.array([])
        conc = float(np.mean(Tm[good].max(1) / (tot[good] + 1e-20))) if np.any(good) else 0.0   # quanto un tono solo domina
        return pk, conc

    key, conf = 'unknown', 0.3

    if bw > 4500 or (bw6 > 4500 and bw6 > 3 * bw):
        wb = max(bw, bw6)
        key, conf = ('fm', 0.75) if wb < 20000 else ('wide', 0.7)
        feats['bw'] = round(wb)
    elif bw <= 30 and duty > 0.9 and evar < 0.25:
        key, conf = 'carrier', 0.8
    else:
        # FSK stretti: FT8, FT4, RTTY (inviluppo costante: niente tasto acceso/spento)
        steady = evar < 0.3 or m4 < 1.15
        pk, conc = tones(int(fs2 / 6.25), int(fs2 / 6.25) // 4)
        cl8 = _cluster(pk, 3.5) if len(pk) else []
        if 30 <= bw <= 80 and steady and len(cl8) >= 4:
            sp = np.diff(sorted(cl8))
            if len(sp) and np.median(np.abs(sp / 6.25 - np.round(sp / 6.25))) < 0.25:
                key, conf = 'ft8', 0.8
        if key == 'unknown' and 60 <= bw <= 130:
            pk4, _ = tones(int(fs2 / 20.833), int(fs2 / 20.833) // 4)
            cl4 = _cluster(pk4, 8) if len(pk4) else []
            if len(cl4) >= 3:
                sp = np.diff(sorted(cl4))
                if np.median(np.abs(sp / 20.833 - np.round(sp / 20.833))) < 0.25 and steady:
                    key, conf = 'ft4', 0.75
        if key == 'unknown' and 120 <= bw <= 1100 and steady:
            pkr, concr = tones(int(fs2 / 20), int(fs2 / 20) // 4)        # 20 Hz di risoluzione, 50 ms
            clr = _cluster(pkr, 25) if len(pkr) else []
            if len(clr) == 2:
                shift = abs(clr[1] - clr[0])
                if any(abs(shift - s) < 0.12 * s for s in (170, 85, 200, 425, 450, 850)):
                    key, conf = 'rtty', 0.8
                    feats['shift'] = round(shift)
            if key == 'unknown' and len(clr) >= 6 and concr > 0.45:
                key, conf = 'mfsk', 0.65
                feats['tones'] = len(clr)
        # CW: tanti elementi brevi accesi/spenti, nessun tratto lungo acceso
        if key == 'unknown' and bw <= 160 and 2 <= rate <= 30 and 0.15 < duty < 0.92 and long_on < 0.3 and snr >= 8:
            key, conf = 'cw', 0.8
        # PSK31/63: stretto, una sola frequenza, inviluppo che pulsa a 31,25 / 62,5 Hz
        if key == 'unknown' and 25 <= bw <= 260 and duty > 0.6:
            a = np.abs(_band(y, fs2, fc, 120)[0])
            fsa = 240.0
            a = a - a.mean()
            if len(a) > 64:
                A = np.abs(np.fft.rfft(a * np.hanning(len(a))))
                fa = np.fft.rfftfreq(len(a), 1 / fsa)
                def line(fx):
                    m = np.abs(fa - fx) < 2.5
                    bg = (np.abs(fa - fx) > 5) & (np.abs(fa - fx) < 20)
                    return float(A[m].max() / (np.median(A[bg]) + 1e-20)) if np.any(m) and np.any(bg) else 0.0
                l31, l62 = line(31.25), line(62.5)
                if bw <= 140 and l31 > 4:
                    key, conf = 'psk31', 0.75
                elif l62 > 4 or (bw > 100 and l31 > 4):
                    key, conf = 'psk63', 0.6
        # MFSK (Olivia, Contestia, MFSK16, Thor): un tono solo per volta, che salta fra tanti
        if key == 'unknown' and 150 <= bw <= 2200 and m4 < 1.5:
            nfm = int(fs2 * 0.03)                             # fotogrammi da 30 ms: un tono MFSK per volta
            Tm = _stft(y, nfm, nfm // 2)
            ffm = np.fft.fftshift(np.fft.fftfreq(nfm, 1 / fs2))
            mm = (ffm >= flo - 20) & (ffm <= fhi + 20)
            if len(Tm) and np.count_nonzero(mm) > 4:
                Tm = Tm[:, mm]
                tot = Tm.sum(1)
                g = tot > np.percentile(tot, 40)
                kk = np.argmax(Tm, 1)
                top = np.array([Tm[i, max(0, kk[i] - 1):kk[i] + 2].sum() for i in range(len(Tm))])
                concm = float(np.mean(top[g] / (tot[g] + 1e-20))) if np.any(g) else 0.0
                uniq = len(set(kk[g].tolist()))
                if concm > 0.6 and uniq >= 6:
                    key, conf = 'mfsk', 0.7
                    feats['tones'] = uniq
        # larghi: voce (a raffiche), OFDM (inviluppo gaussiano), PSK a portante singola
        if key == 'unknown' and bw >= 700:
            if duty < 0.85 or evar > 0.45 or m4 > 2.6:
                key, conf = ('ssb', 0.7) if bw >= 1000 or m4 > 3 else ('unknown', 0.3)
            elif m4 >= 1.72 and flat > 0.55:
                if 850 <= bw <= 2000:
                    key, conf = 'freedv', 0.6
                elif 2000 < bw <= 3000:
                    key, conf = 'drm', 0.6
                else:
                    key, conf = 'ofdm', 0.55
            elif flat > 0.5:
                key, conf = ('hsmodem', 0.6) if 1900 <= bw <= 3000 else ('psk_wide', 0.5)
        if key == 'unknown' and 450 <= bw < 700 and m4 > 3 and duty < 0.9:
            key, conf = 'ssb', 0.55                           # voce debole: si vede solo la parte più forte
        if key == 'unknown' and bw > 2900:
            key, conf = 'wide', 0.55

    r = _result(out, key, conf, feats)
    r['f_lo'], r['f_hi'] = float(hz + flo), float(hz + fhi)
    return r


def _result(out, key, conf, feats):
    it, en, hit, hen = INFO.get(key, INFO['unknown'])
    r = dict(out, key=key, conf=round(conf, 2), it=it, en=en, how_it=hit, how_en=hen, feats=feats)
    bw = feats.get('bw')
    if bw:
        fmt = (lambda x: f'{x / 1000:.2f} kHz'.replace('.', ',')) if bw >= 1000 else (lambda x: f'{x:.0f} Hz')
        fmt_en = (lambda x: f'{x / 1000:.2f} kHz') if bw >= 1000 else (lambda x: f'{x:.0f} Hz')
        d_it, d_en = [f'larghezza {fmt(bw)}'], [f'width {fmt_en(bw)}']
        if key in ('freedv', 'drm', 'ofdm'):
            d_it.append('spettro piatto, bordi netti'); d_en.append('flat spectrum, sharp edges')
        elif key in ('hsmodem', 'psk_wide'):
            d_it.append('spettro a cupola, sempre acceso'); d_en.append('dome-shaped spectrum, always on')
        elif key == 'ssb':
            d_it.append('spettro irregolare, a sillabe'); d_en.append('irregular spectrum, in syllables')
        elif key == 'cw':
            d_it.append(f'acceso/spento {feats.get("rate", 0):.0f} volte al secondo'); d_en.append(f'on/off {feats.get("rate", 0):.0f} times a second')
        if 'shift' in feats:
            d_it.append(f'shift {feats["shift"]} Hz'); d_en.append(f'shift {feats["shift"]} Hz')
        if 'tones' in feats:
            d_it.append(f'{feats["tones"]} toni'); d_en.append(f'{feats["tones"]} tones')
        d_it.append(f'{feats.get("snr", 0)} dB sul rumore'); d_en.append(f'{feats.get("snr", 0)} dB above noise')
        r['det_it'], r['det_en'] = ' · '.join(d_it), ' · '.join(d_en)
    return r
