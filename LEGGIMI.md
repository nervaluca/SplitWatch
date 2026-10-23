# FZR Skimmer 1.33

IW1FZR

Ricevitore per pile-up in split con il Perseus: l'audio resta fermo sul DX,
il waterfall scorre libero su tutta la banda ricevuta, il testo CW decodificato
appare accanto a ogni segnale e i 5NN sono evidenziati. Dopo ogni 5NN del DX
il programma segna in arancio dove ha risposto la stazione lavorata, cioè dove
il DX sta ascoltando.

**Lingua**: pulsante **EN / IT** in alto a destra (italiano o inglese; resta memorizzato).
English instructions: `README_EN.md`.

## Installazione

1. Scarica da GitHub → **Actions** → ultima compilazione → **Artifacts** lo zip `FZRSkimmer`
   e scompattalo dove vuoi (es. `C:\FZRSkimmer`).
2. Avvia una volta **prepara_cartella.bat**: copia `perseususb.dll` e i file `perseus*.sbs`
   dalla cartella di CW Skimmer (o del software Perseus). Da quel momento la cartella è completa.
   Se non lo fai, FZR Skimmer cerca comunque quei file nella cartella di CW Skimmer.
3. **Chiudi CW Skimmer, HDSDR e il software Perseus**: il Perseus lo può usare
   un solo programma per volta.
4. Avvia `FZRSkimmer.exe`: si apre la finestra di FZR Skimmer (usa Edge in modalità app,
   senza barra degli indirizzi). **Chiudendo la finestra, o con il pulsante Esci, si ferma
   anche il Perseus** e il programma esce.
   Dal tablet in stazione: apri nel browser l'indirizzo scritto in `fzrskimmer.log`
   (tipo `http://192.168.x.x:8073`). Dal tablet chiudere il browser non spegne nulla:
   per spegnere usa **Esci**.
   Se il ricevitore non parte: **Riavvia ricevitore** (in basso). Il programma riprova già da solo 3 volte.
   Se ancora niente, controlla in Gestione attività che non ci sia un altro `FZRSkimmer.exe` acceso.
   Opzioni: `--browser` apre nel browser normale; `--no-browser` non apre finestre.

I file del Perseus (`perseususb.dll`, `.sbs`) sono di Microtelecom: non vanno caricati
nel repository pubblico, si copiano solo sul PC della stazione.

Il campionamento (48 / 96 / 192 kHz) si sceglie in basso nella pagina.
Se le stazioni appaiono specchiate rispetto al centro, spunta **Inverti I/Q**.

## Registrazione

FZR Skimmer usa **la stessa registrazione di ADIF FZR** (stessa chiave, stesso nominativo).
- Senza registrazione funziona **5 minuti per ogni avvio**, poi il ricevitore si ferma
  e compare la finestra di registrazione. La riproduzione delle registrazioni IQ è libera.
- **Registra** (in alto): nominativo + chiave incollata, oppure **Verifica online** se il
  nominativo è già abilitato sul sito. **Richiedi una chiave** apre la pagina di iw1fzr.it.
- Se ADIF FZR è già registrato sullo stesso PC, FZR Skimmer trova da solo la sua licenza.
- La licenza viene salvata in `licenza.json` accanto all'exe.

## Nominativi riconosciuti

Se nella cartella c'è un elenco di nominativi, i call decodificati che compaiono nell'elenco
sono evidenziati (bianco su fondo blu, sottolineati). Se un call differisce di **una sola lettera**
da uno dell'elenco (e l'elenco ne ha uno solo così), viene mostrato corretto con un **?**
(es. `DL9UO?`), sottolineato a tratti: è una deduzione, non una lettura sicura.

File letti all'avvio (il primo che trova, anche più di uno):
`MASTER.SCP` (Super Check Partial, da www.supercheckpartial.com o dalla cartella di N1MM Logger+),
`MASTER.DTA` (CW Skimmer), oppure `callsign.dat` / `nominativi.txt` con un nominativo per riga.
`prepara_cartella.bat` copia MASTER.SCP da N1MM, se c'è.

## Ricevitori

Si sceglie in basso nella pagina, alla voce **Ricevitore**, senza riavviare:

- **Perseus** – banda larga (48/96/192 kHz), il modo migliore per i pile-up.
- **Audio della radio** – qualsiasi radio: l'uscita audio (USB o CW) entra nella scheda
  audio del PC (es. USB Audio CODEC dell'IC-7600). Si vede e si decodifica solo la banda
  passante della radio (2–3 kHz): basta per gli split stretti.
  - **Frequenza radio**: scrivi la frequenza che mostra la radio (non c'è ancora il CAT).
  - **Offset**: la nota CW (es. 600) se la radio mostra la frequenza del segnale, come gli
    Icom in CW; 0 se mostra la portante USB.
  - L'audio lo senti già dalla radio: in questo modo il pulsante Volume serve poco.
- **IQ da scheda audio** – ricevitori che mandano I e Q sui canali sinistro/destro
  (SoftRock e simili), a 48/96/192 kHz. **LO** = frequenza centrale del ricevitore.
- **ExtIO (SDRplay, Airspy HF+…)** – *sperimentale, da collaudare*. Qualsiasi ricevitore che
  abbia un plugin ExtIO per HDSDR: SDRplay RSP1A/RSPdx/RSPduo, Airspy HF+ / Discovery,
  FunCube e altri.
  - Serve il plugin **a 32 bit** per HDSDR del ricevitore (`ExtIO_SDRplay….dll`,
    `ExtIO_AirspyHF.dll`…) con il suo driver installato: se funziona in HDSDR funziona anche qui.
  - FZR Skimmer lo cerca da solo nelle cartelle di HDSDR, SDRplay e Airspy e in quella del
    programma; altrimenti **Plugin → Sfoglia…** e scegli la DLL.
  - **Campionamento**: l'elenco viene dal plugin; scegli 192 kHz o meno (Airspy HF+: 192).
    Se il ricevitore dà solo velocità alte (SDRplay a 2 MHz) il programma le riduce da solo
    sotto i 200 kHz (÷ n accanto al valore).
  - **Impostazioni ricevitore** apre la finestra del plugin (guadagno, antenna, AGC…).
  - Chiudi HDSDR o gli altri programmi che usano il ricevitore.

**Ingresso**: la scheda audio da cui leggere.

## Pannello radio (1.33)

Sotto i modi c'è il pannello «da radio»:
- **SPLIT** (nei modi Ricevitore, tasto S): acceso = RX2 attivo; spento = solo RX1, come una radio senza split:
  il riquadro RX2 sparisce, rotellina e clic sul waterfall spostano RX1 e, con la radio collegata, lo split della radio si spegne.
- **Un solo ricevitore acceso** (split spento, oppure RX1 o RX2 muto): lo senti in tutte e due le orecchie.
- **RX1** (bordo verde): frequenza grande, modo e filtro, S-meter, **🔊 RX1** (mute, tasto M) e **VOL** di RX1.
  **Rotellina sulla frequenza** = sposta il DX del **Passo** di RX1 (10 Hz–1 kHz, uno per ogni modo Ricevitore: di base 100 Hz in SSB/DIGI,
  50 Hz in CW/RTTY; vale anche per Ctrl + rotellina, frecce e rotellina con lo split spento; Maiusc = 10 Hz; il 🔒 DX la blocca).
  **Clic sulla frequenza** = la scrivi (in kHz, es. `14195`, oppure in MHz, es. `14.195`), Invio conferma, Esc annulla.
- **RX2** (bordo azzurro, nei modi Ricevitore): frequenza, scostamento UP/DOWN, S-meter, **🔊 RX2** (tasto R)
  e **VOL** di RX2, il **Passo** della rotellina e **RX2 = DX** / **TX = RX2**. Rotellina o clic sulla frequenza come per RX1.
- **Audio**: **Volume** (audio acceso/spento), **AF** (volume generale), cuffia (DX a sinistra / a destra / insieme), 🎧 Prova,
  **💾 Salva ultimi**, **⏺ Registra** e 📂.
- **Ricevitore**: AGC e RF, ATT e PRE, filtro (CW o SSB) e banda laterale.

Nei modi Skimmer il pannello sta su una riga, così il waterfall resta alto. Nei modi Ricevitore
il pulsante **▴** lo riduce alle sole due frequenze (**▾** lo riapre); i tasti M, R e la rotellina funzionano lo stesso.

I due **VOL** sostituiscono il vecchio potenziometro DX ◀▶ RX2: ogni ricevitore ha il suo volume
accanto al suo mute (doppio clic = normale).

## Comandi da radio: AGC, RF, AF, ATT, PRE, filtro SSB

- **AGC** FAST (pile-up, segnali che vanno e vengono), MED, SLOW (ascolto riposante, il predefinito) oppure
  **OFF**: guadagno fisso, regolato con **RF** (abbassalo se i segnali forti distorcono).
- **AF**: volume (doppio clic = normale).
- **ATT** 0/10/20/30 dB e **PRE** (preamplificatore): solo con il Perseus. Lo S-meter tiene conto
  dell'attenuatore e continua a leggere il segnale in antenna.
- **Filtro SSB** 1,8–3,3 kHz (in SSB): la banda passante parte da 200 Hz.

## S-meter

In alto c'è lo S-meter del DX (in SSB anche quello di RX2): misura il segnale nella banda passante
dell'RX, come una radio (S9 = −73 dBm, 6 dB per unità S, poi S9+10…+60). Barra che sale subito e
scende piano, trattino bianco = picco degli ultimi 2 s.

Finché non è tarato i dBm hanno il segno ≈ (valore indicativo). Per tararlo clic sullo S-meter:
- **Imposta**: metti il DX su un segnale stabile e scegli quanto vale (es. S9);
- **Con la radio** (CAT, stessa frequenza del DX, segnale stabile: una portante, un broadcast) in due
  passi, perché col relè d'antenna Perseus e radio non ricevono insieme:
  **1 · Misura qui** col relè sul Perseus, poi spegni il relè (antenna alla radio) e
  **2 · Leggi la radio**: legge lo S-meter della radio e si allinea. Con antenne separate o uno
  splitter i due passi si fanno uno dopo l'altro senza toccare niente.
La taratura si ricorda per ogni ricevitore (Perseus, ExtIO, scheda audio).

## Radio (CAT) con Hamlib

FZR Skimmer comanda la radio (IC-7600 e tutte quelle di Hamlib) in parallelo al Perseus, tramite
**rigctld** di Hamlib. Installa Hamlib per Windows (https://github.com/Hamlib/Hamlib/releases,
file `hamlib-w32-….exe` o `-w64`, va bene l'uno o l'altro): FZR Skimmer trova `rigctld.exe` da solo
(cartelle di Hamlib, WSJT-X, JTDX), altrimenti **Sfoglia…**.

In basso **Radio…**: modello (IC-7600 = 3063, scrivi «7600» per cercarlo), porta COM e velocità
(sull'IC-7600: CI-V USB, 19200), poi **Collega la radio**. In alternativa ci si collega a un rigctld
già aperto da un altro programma (computer e porta, di solito 4532). La spunta **Radio (CAT)** la
accende e la spegne.

- **La radio segue FZR Skimmer**: DX, pulsanti di banda, modo (CW, oppure USB/LSB in SSB).
- **FZR Skimmer segue la radio**: giri la manopola e il DX si sposta; se esci dalla banda ricevuta il
  Perseus si risintonizza. Con «Audio della radio» la frequenza si legge da sola.
- **Split**: in SSB il TX segue RX2 (rotellina = TX già sulla stazione che hai trovato); in DXpedition
  **TX → ascolto** o tasto **T** mette il TX sull'ultimo ascolto rilevato; tasto destro sul waterfall →
  **TX qui (split)**. La linea rossa tratteggiata è il TX della radio.
- Cambiando banda o passando CW ↔ SSB lo split si spegne.
- **Quando trasmetti** l'audio tace e, appena torni in ricezione, il ricevitore è subito pronto
  (l'AGC riparte da zero): niente più 1–2 secondi di sordità in isofrequenza. Lo capisce dal PTT
  della radio (CAT) e, anche senza CAT, da come il proprio segnale satura la banda.
- All'avvio: se la radio è già su una banda HF la segue FZR Skimmer, altrimenti la radio va sul DX.

## Calibrazione della frequenza

Se il ricevitore legge un po' spostato (il Perseus a volte di qualche decina di Hz), si corregge
con **Correzione ppm** in basso. L'errore viene dal quarzo, è proporzionale alla frequenza:
per questo si scrive in ppm e vale da solo per tutte le bande (1 ppm = 14 Hz a 14 MHz, 7 Hz a 7 MHz).

Il modo comodo è **Calibra…**: scegli una stazione campione che senti bene (RWM 4996/9996/14996 kHz,
WWV e BPM 5/10/15 MHz, CHU 3330/7850/14670 kHz) e premi **Misura**. Il programma si sintonizza,
misura la portante per 6 secondi al centesimo di Hz, torna dov'eri e mostra errore attuale,
forza del segnale e stabilità. La correzione la applichi tu con **Applica la correzione**;
**Azzera** la toglie. Una misura su una seconda stazione deve dare «già preciso».

Vale per Perseus, ExtIO e IQ da scheda audio (ognuno ha la sua). Con la scheda IQ, che non si
sintonizza da sola, metti prima la stazione campione dentro la banda ricevuta.

## Uso

- **Volume**: avvia l'audio (il browser lo richiede con un clic).
- **▲ / ▼ 5 kHz**, rotellina o trascinamento: scorri il waterfall. L'audio resta sul DX.
- **Ctrl + clic** sul waterfall: il DX va esattamente lì (passo 10 Hz). La vista resta ferma.
- **Tasto destro** (o pressione lunga sul tablet) sul waterfall:
  *Imposta il DX qui*, *Segnaposto qui*, *Centra la vista*.
  Per togliere un segnaposto: tasto destro sopra la sua linea → *Togli questo segnaposto*;
  *Togli tutti i segnaposti* li cancella tutti.
- **Zona UP**: dove cercare l'ascolto, in kHz sopra il DX (es. 1–5 se il DX dice "UP 1-5").
  Serve anche a escludere FT8 e altri segnali digitali.
- **Centro Perseus**: frequenza centrale del ricevitore in kHz.
- **Ctrl + rotellina** sul waterfall: sposta la frequenza del DX di 50 Hz (con Ctrl + Maiusc di 10 Hz). La vista resta ferma, l'audio segue.
- **Banda** (160…10) richiama centro e DX memorizzati; **Salva memoria** li memorizza per la banda attuale.
- **Filtro RX** (100–800 Hz): larghezza dell'audio sul DX, per togliere chi chiama troppo vicino.
  La fascia verde sul waterfall mostra la banda passante.
- **Sensibilità**: Alta = più nominativi deboli ma più errori; Media (iniziale); Bassa = solo segnali puliti.
- **Colori / Luce / Contrasto**: tavolozza del waterfall (Skimmer, Classica, Fosfori, Grigio, Notte),
  luminosità e contrasto. Doppio clic su un cursore = valore iniziale. Restano memorizzati.
- Tastiera: PagSu/PagGiù ±5 kHz, frecce su/giù scorrono, frecce sinistra/destra spostano il DX (±50 Hz, con Maiusc ±10 Hz), Home = torna al DX, S = SPLIT (modi Ricevitore).

## Registrare

Nel riquadro **Audio** del pannello radio (nei modi Skimmer, compatti: 💾 m:ss e ● REC):

- **💾 Salva ultimi m:ss** – FZR Skimmer tiene sempre in memoria gli ultimi minuti ricevuti
  (5 min di base, si cambia in basso alla voce **Memoria**). Il pulsante li scrive su disco
  senza fermare il ricevitore: utile per controllare con calma se il DX ti ha messo a log.
  Occupa RAM: a 96 kHz circa 23 MB al minuto (5 min ≈ 115 MB), a 192 kHz il doppio.
  **Memoria: spenta** se non ti serve.
- **⏺ Registra** – registra su disco da adesso finché non lo premi di nuovo
  (a 96 kHz circa 35 MB al minuto). Cambiando banda il file si chiude e ne parte un altro.
- **📂** apre la cartella `registrazioni` (accanto all'exe).

I file sono WAV IQ a 24 bit con nome tipo `FZR_20261017_121500Z_18085.000kHz_DX18080.00.wav`
e dentro hanno centro e DX: si aprono anche con HDSDR, SDR Console e CW Skimmer.

## Riprodurre una registrazione

**Ricevitore → Registrazione (WAV)**: il Perseus si ferma (si libera) e compare la barra
di riproduzione con ▶/⏸ (anche barra spaziatrice), **−15 s**, cursore della posizione,
elenco delle registrazioni e **Sfoglia…** per aprire un WAV qualsiasi (CW Skimmer, HDSDR,
SDR Console; la finestra si apre sul PC). **Torna al ricevitore** riaccende il Perseus.

Il waterfall, il testo decodificato e la ricerca dell'ascolto funzionano come dal vivo.
Centro e DX si leggono dal file (FZR Skimmer, HDSDR, nome del file); CW Skimmer non salva
la frequenza: scrivila in **Centro kHz**, viene ricordata per quel file.
La riproduzione è libera anche senza registrazione del programma.

Da riga di comando:

    FZRSkimmer.exe registrazione.wav --centro 18085 --dx 18085.04

**Riavvia ricevitore**: chiude e riapre il Perseus (o la scheda audio) senza chiudere il
programma: un paio di secondi di buco, si ricarica il firmware, le impostazioni restano,
il waterfall e il testo ripartono da vuoti. La memoria degli ultimi minuti resta.

## Modo: Skimmer (Normale, DXpedition, Contest) o Ricevitore (CW, SSB, RTTY, DIGI)

In alto a sinistra, **Modo**:
- **Normale** – ascolto libero, come CW Skimmer: a destra si apre il pannello **Testo decodificato**
  con il testo di tutti i segnali visibili nel waterfall, dal più alto al più basso, con nominativi,
  CQ e 599/5NN/TU evidenziati. Clic su una riga = RX su quel segnale. Niente ricerca dell'ascolto.
- **DXpedition** – pile-up in split: RX fermo sul DX, ricerca di dove ascolta (linee arancio,
  Ascolti, Zona UP).
- **Contest** – S&P: si accende l'elenco stazioni, la ricerca dell'ascolto si spegne e sparisce
  dalla schermata. Il modo resta memorizzato.
- **SSB** – pile-up in fonia. RX fermo sul DX e un secondo ricevitore, **RX2**, in stereo:
  DX in un orecchio e RX2 nell'altro (o insieme), con il **VOL** di ciascun ricevitore per dosarli.
  - **Rotellina** sul waterfall: RX2 su e giù del **Passo** scelto (100, 500, 1000 Hz).
    Maiusc + rotellina scorre la vista, Ctrl + rotellina sposta il DX.
  - **Clic** su una voce: RX2 va lì (banda passante centrata dove clicchi). **RX2 = DX** lo riporta sul DX.
  - Banda laterale **Auto** (LSB sotto i 10 MHz, USB sopra) oppure USB/LSB fissa; filtro 2,7 kHz.
  - Sul waterfall le bande passanti: DX verde, RX2 azzurro con lo scostamento (UP +5.30).
    La decodifica CW è spenta.
  - **Memorie di banda separate** per la fonia: passando in SSB la radio va sul segmento fonia della
    stessa banda (es. 40 m: centro 7150, DX 7140; 20 m: 14220 / 14195), tornando in CW alle memorie CW.
    «Salva memoria» in SSB salva quella fonia. Il 30 m in SSB non c'è.
  - **Ctrl + rotellina** (e Ctrl + clic) sposta **solo il DX**: RX2 resta dov'è, cambia lo scostamento.
    Cambiando banda invece RX2 tiene lo scostamento (es. UP 5).
  - **🔊 RX1 DX** (tasto **M**) e **🔊 RX2** (tasto **R**) funzionano uguale: acceso (illuminato) = si sente,
    spento (🔇) = muto. RX2 spento sparisce anche dal waterfall; girando la rotellina si riaccende.
  - **🔒 DX** (spento di base): se acceso, Ctrl + rotellina e frecce non spostano il DX per sbaglio;
    Ctrl + clic, tasto destro, frequenza scritta e pulsanti di banda sì.
  - **🎧 Prova**: nota bassa nell'orecchio del DX, nota alta in quello di RX2. Se le senti uguali la
    cuffia, la scheda audio o Windows (Impostazioni → Accessibilità → Audio mono) sono in mono.

- **Ricevitore: CW, RTTY, DIGI** – come l'SSB (RX1 sul DX, RX2 con la rotellina, stereo, mute,
  split che segue RX2, S-meter, memorie di banda proprie), ma senza skimmer: un ricevitore puro.
  - **CW**: frequenza = quella del segnale, sentito a 600 Hz; larghezza con **Filtro RX** (100–800 Hz).
    Usa le memorie CW. Passi della rotellina 10 e 50 Hz.
  - **RTTY** con **FZRTTY**, skimmer RTTY multicanale: trova da solo tutti i segnali RTTY visibili
    (45,45 baud, shift 170 Hz, Baudot) e li decodifica tutti insieme, con nominativi, CQ e 599
    evidenziati accanto al waterfall, l'elenco stazioni (nominativo che ripete di più su ogni segnale)
    e il testo del DX nella riga in basso. Frequenza = **mark** (come gli Icom); in cuffia mark a
    2125 Hz e space a 2295 Hz (filtro 600 Hz), così l'audio va anche a MMTTY. Memorie: 7040, 14083,
    21083… (centro 15 kHz sopra: tutto il segmento RTTY nel waterfall).
  - **DIGI** (FT8, FT4, PSK…): USB-D, banda 100–3100 Hz, memorie sulle frequenze FT8 (7074, 14074…).
    Per decodificare con WSJT-X (o un altro programma digitale): in Windows (Impostazioni → Sistema → Audio → Mixer volume) manda l'audio di Edge
    («FZR Skimmer») su un cavo audio virtuale (es. VB-Cable) e in WSJT-X scegli quel cavo come ingresso;
    cuffia «DX e RX2 insieme» e RX2 spento (🔇 RX2).
  - Con la radio collegata (CAT) il modo della radio segue: CW, USB/LSB, RTTY, USB-D (DIGI).

## Elenco stazioni (contest, S&P)

Pulsante **☰ Elenco**: a destra del testo compare l'elenco delle stazioni, ordinate per frequenza:
nominativo, **CQ** se stanno chiamando CQ/TEST, frequenza, dB, velocità e da quanto sono state sentite.
Ci finiscono i nominativi confermati (decodificati uguali almeno due volte sullo stesso segnale,
oppure subito dopo CQ/TEST) e gli spot RBN della banda ricevuta (in viola).
**Clic su una riga**: l'audio va su quella stazione e il waterfall si centra lì.

Le parole attaccate (spazio troppo corto tra una parola e l'altra: `CQCQDE`, `DL1EKOTEST`, `TU5NN203`)
vengono separate quando i pezzi sono parole CW, nominativi o numeri di scambio.

## Nominativi: conferma per ripetizione

Chi chiama ripete il proprio nominativo. Quando su un segnale lo stesso call viene decodificato
due volte, diventa **confermato**; le letture storpiate dello stesso segnale (una lettera diversa,
l'inizio perso) vengono mostrate corrette con il **?**, anche quelle arrivate prima.

## Reverse Beacon Network (RBN)

Casella **RBN** in basso (accesa di base): FZR Skimmer si collega a telnet.reversebeacon.net
(solo CW) con il tuo nominativo (quello della registrazione; se non sei registrato compare
il campo **Nominativo**). Serve Internet.

- **RBN** (in alto, accanto a Elenco) accende/spegne il collegamento; **Spot** mostra o nasconde
  le etichette sul waterfall.
- Sul waterfall, sul bordo sinistro (la parte vecchia, così non coprono i segnali appena arrivati), compaiono in viola i nominativi spottati nella banda ricevuta,
  sulla loro frequenza, con il segnale in dB (più sbiaditi se lo spot è vecchio, fino a 15 minuti).
- Un nominativo decodificato che coincide con uno spottato lì vicino (entro 600 Hz) viene
  mostrato come **sicuro**; se differisce di una sola lettera da uno spottato entro 300 Hz,
  viene corretto con il **?**.
- Attenzione: l'RBN spotta chi chiama CQ (il DX, chi fa contest), quasi mai i chiamanti del
  pile-up. Conferma il DX e le stazioni attorno, non chi risponde.

## Novità 1.12

- **Decodifica nuova**: ogni segnale CW viene agganciato alla sua frequenza esatta (non più al
  "gradino" FFT più vicino), con soglie a isteresi, filtro dei disturbi brevi e stima della
  velocità dai segni già passati. Le eco dei segnali forti (bande laterali, click) non
  aprono più canali fasulli. Sul banco di prova con pile-up simulato: errori dal 45% al 17%.
- **Waterfall pulito**: finestra Blackman-Harris lunga il doppio (risoluzione 23 Hz invece di 47)
  e filtro che toglie i "baffi" verticali dei click: punti e linee restano netti, come in CW Skimmer.
- **Audio senza clic**: buffer continuo nel browser e AGC campione per campione.
  Dal tablet (indirizzo http della rete) il browser non permette il buffer continuo:
  lì resta il metodo vecchio, migliorato.

## Limiti attuali

- Il decoder nel pile-up sovrapposto è ancora sporco; sul DX funziona bene.
- Il rilevamento dell'ascolto scatta solo quando il DX passa un 5NN decodificato.


Tutte le impostazioni (banda, DX, memorie, filtro, sensibilità, ricevitore, larghezza, scorrimento,
colori) stanno in `fzrskimmer.json` accanto all'exe; se quella cartella non è scrivibile, in
`%APPDATA%\FZRSkimmer`. Il percorso esatto è scritto all'inizio di `fzrskimmer.log`
(al primo avvio vengono riprese da `splitwatch.json`, se c'è). Il registro è `fzrskimmer.log`.
