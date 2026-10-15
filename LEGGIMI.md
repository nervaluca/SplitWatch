# FZR Skimmer 1.6

IW1FZR

Ricevitore per pile-up in split con il Perseus: l'audio resta fermo sul DX,
il waterfall scorre libero su tutta la banda ricevuta, il testo CW decodificato
appare accanto a ogni segnale e i 5NN sono evidenziati. Dopo ogni 5NN del DX
il programma segna in arancio dove ha risposto la stazione lavorata, cioè dove
il DX sta ascoltando.

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
   Opzioni: `--browser` apre nel browser normale; `--no-browser` non apre finestre.

I file del Perseus (`perseususb.dll`, `.sbs`) sono di Microtelecom: non vanno caricati
nel repository pubblico, si copiano solo sul PC della stazione.

Il campionamento (48 / 96 / 192 kHz) si sceglie in basso nella pagina.
Se le stazioni appaiono specchiate rispetto al centro, spunta **Inverti I/Q**.

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
  - L'audio lo senti già dalla radio: in questo modo il pulsante Audio DX serve poco.
- **IQ da scheda audio** – ricevitori che mandano I e Q sui canali sinistro/destro
  (SoftRock e simili), a 48/96/192 kHz. **LO** = frequenza centrale del ricevitore.

**Ingresso**: la scheda audio da cui leggere.

## Uso

- **Audio DX**: avvia l'audio (il browser lo richiede con un clic).
- **▲ / ▼ 5 kHz**, rotellina o trascinamento: scorri il waterfall. L'audio resta sul DX.
- **Tasto destro** (o pressione lunga sul tablet) sul waterfall:
  *Imposta il DX qui*, *Segnaposto*, *Centra la vista*.
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
- Tastiera: PagSu/PagGiù ±5 kHz, frecce su/giù scorrono, frecce sinistra/destra spostano il DX (±50 Hz, con Maiusc ±10 Hz), Home = torna al DX.

## Riprodurre una registrazione

Trascina un WAV IQ (CW Skimmer, HDSDR, SDR Console) sopra `FZRSkimmer.exe`.
CW Skimmer non salva la frequenza: il programma te la chiede.
Da riga di comando:

    FZRSkimmer.exe registrazione.wav --centro 18085 --dx 18085.04

## Limiti attuali

- Il decoder nel pile-up sovrapposto è ancora sporco; sul DX funziona bene.
- Il rilevamento dell'ascolto scatta solo quando il DX passa un 5NN decodificato.


Le impostazioni e le memorie di banda stanno in `fzrskimmer.json` accanto all'exe
(al primo avvio vengono riprese da `splitwatch.json`, se c'è). Il registro è `fzrskimmer.log`.
