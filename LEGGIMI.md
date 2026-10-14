# Split Watch 0.1

Ricevitore per pile-up in split con il Perseus: l'audio resta fermo sul DX,
il waterfall scorre libero su tutta la banda ricevuta, il testo CW decodificato
appare accanto a ogni segnale e i 5NN sono evidenziati. Dopo ogni 5NN del DX
il programma segna in arancio dove ha risposto la stazione lavorata, cioè dove
il DX sta ascoltando.

## Installazione

1. Scarica `SplitWatch.exe` (da GitHub → Actions → ultima compilazione → Artifacts).
2. Copialo nella cartella dove ci sono **ExtIO_perseus.dll** e **perseususb.dll**
   (di solito la cartella di HDSDR). Se perseususb.dll non c'è, copiala dalla
   cartella del software Perseus insieme ai file `.sbs`.
3. **Chiudi CW Skimmer, HDSDR e il software Perseus**: il Perseus lo può usare
   un solo programma per volta.
4. Avvia `SplitWatch.exe`: si apre il browser su http://localhost:8073
   (dal tablet in stazione: l'indirizzo che compare nella finestra nera).

Il campionamento è quello impostato l'ultima volta nel driver ExtIO
(da HDSDR). Il pulsante **Impostazioni Perseus** apre la finestra del driver.

## Uso

- **Audio DX**: avvia l'audio (il browser lo richiede con un clic).
- **▲ / ▼ 5 kHz**, rotellina o trascinamento: scorri il waterfall. L'audio resta sul DX.
- **Tasto destro** (o pressione lunga sul tablet) sul waterfall:
  *Imposta il DX qui*, *Segnaposto*, *Centra la vista*.
- **Zona UP**: dove cercare l'ascolto, in kHz sopra il DX (es. 1–5 se il DX dice "UP 1-5").
  Serve anche a escludere FT8 e altri segnali digitali.
- **Centro Perseus**: frequenza centrale del ricevitore in kHz.
- Tastiera: PagSu/PagGiù ±5 kHz, frecce su/giù, Home = torna al DX.

## Riprodurre una registrazione

Trascina un WAV IQ (CW Skimmer, HDSDR, SDR Console) sopra `SplitWatch.exe`.
CW Skimmer non salva la frequenza: il programma te la chiede.
Da riga di comando:

    SplitWatch.exe registrazione.wav --centro 18085 --dx 18085.04

## Limiti della 0.1

- Il decoder nel pile-up sovrapposto è ancora sporco; sul DX funziona bene.
- Il rilevamento dell'ascolto scatta solo quando il DX passa un 5NN decodificato.
