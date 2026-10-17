"""Traduzione in inglese dei messaggi di stato del programma (la pagina mostra quella giusta)."""
import re

# (espressione regolare sul testo italiano, sostituzione inglese) — applicate in ordine sul messaggio
_EN = [
    (r'Avvio…', 'Starting…'),
    (r'Nessuna registrazione: usa «Sfoglia…» o salva prima gli ultimi minuti',
     'No recordings: use «Browse…» or save the last minutes first'),
    (r'Riproduzione: (.+?) · ([\d.]+) kHz · ([\d.]+) min', r'Playing: \1 · \2 kHz · \3 min'),
    (r'centro sconosciuto: scrivilo in «Centro kHz»', 'centre frequency unknown: type it in «Centre kHz»'),
    (r'Ingresso audio "(.*?)" non trovato: uso quello predefinito', r'Audio input "\1" not found: using the default one'),
    (r'premi «Riavvia ricevitore» per riprovare', 'press «Restart receiver» to try again'),
    (r'Prova di 5 minuti terminata: registra FZR Skimmer per continuare', '5-minute trial over: register FZR Skimmer to continue'),
    (r'Prova terminata: registra FZR Skimmer per riattivare il ricevitore', 'Trial over: register FZR Skimmer to re-enable the receiver'),
    (r"Metti i file \.wav in (.+?) e sceglili dall'elenco", r'Put the .wav files in \1 and pick them from the list'),
    (r'Impossibile aprire la finestra dei file', 'Cannot open the file dialog'),
    (r'Errore nel calcolo: vedi fzrskimmer\.log', 'Processing error: see fzrskimmer.log'),
    (r'Nessun dato dal Perseus', 'No data from the Perseus'),
    (r'Campionamento sconosciuto: attendo i dati…', 'Unknown sample rate: waiting for data…'),
    (r'Sintonizza la radio su ([\d.]+) kHz', r'Tune the radio to \1 kHz'),
    (r'In riproduzione la frequenza centrale non si cambia: imposto solo il DX',
     'The centre frequency cannot change during playback: setting the DX only'),
    (r'In riproduzione la frequenza centrale non si cambia', 'The centre frequency cannot change during playback'),
    (r'Memoria (\d+) m salvata: centro ([\d.]+) · DX ([\d.]+) kHz', r'\1 m memory saved: centre \2 · DX \3 kHz'),
    (r'Frequenza fuori dalle bande radioamatoriali: memoria non salvata', 'Frequency outside the amateur bands: memory not saved'),
    (r'Per registrare serve il ricevitore acceso \(non la riproduzione\)', 'Recording needs the live receiver (not playback)'),
    (r'Registrazione in corso in (.+)', r'Recording in \1'),
    (r'Le registrazioni sono in (.+)', r'Recordings are in \1'),
    (r'Fine della registrazione', 'End of recording'),
    (r'Registrazione: ricomincio da capo', 'Recording: starting over'),
    (r'Il Perseus non risponde', 'The Perseus does not respond'),
    (r'perseususb\.dll è a 32 bit: serve la versione a 32 bit di FZR Skimmer', 'perseususb.dll is 32-bit: the 32-bit FZR Skimmer is needed'),
    (r'ExtIO_perseus\.dll è a 32 bit: serve la versione a 32 bit di FZR Skimmer', 'ExtIO_perseus.dll is 32-bit: the 32-bit FZR Skimmer is needed'),
    (r'Non trovo perseususb\.dll in (.+?) \(.*?\)', r'perseususb.dll not found in \1 (it is in the CW Skimmer or Perseus software folder)'),
    (r'Non trovo ExtIO_perseus\.dll in (.+)', r'ExtIO_perseus.dll not found in \1'),
    (r'Primo blocco dal Perseus: (\d+) campioni', r'First block from the Perseus: \1 samples'),
    (r'Messaggio dal driver: stato', 'Driver message: status'),
    (r'Errore dati', 'Data error'),
    (r'Perseus non trovato \((\w+)\)\. È acceso e collegato\?.*', r'Perseus not found (\1). Is it on and connected? Is another program (CW Skimmer…) using it?'),
    (r'Apertura del Perseus fallita', 'Opening the Perseus failed'),
    (r'Avvio del Perseus fallito', 'Starting the Perseus failed'),
    (r'Il Perseus non manda dati \(0 campioni/s\)\..*', 'The Perseus sends no data (0 samples/s). Check that no other program is using it.'),
    (r'Nessun dato dopo 4 s: riavvio lo streaming', 'No data after 4 s: restarting the stream'),
    (r'Ancora nessun dato dal Perseus dopo 4 s', 'Still no data from the Perseus after 4 s'),
    (r"Con il driver diretto non c'è la finestra del Perseus: usa i comandi della pagina",
     'With the direct driver there is no Perseus window: use the controls on the page'),
    (r'Manca il file (.+?) nella cartella (.+)', r'File \1 missing in folder \2'),
    (r'Configurazione FPGA fallita \((.+?), codice (.+?)\)', r'FPGA configuration failed (\1, code \2)'),
    (r'Avvio dello streaming fallito', 'Starting the stream failed'),
    (r'Perseus attivo', 'Perseus running'),
    (r'preselettore', 'preselector'),
    (r'Campionamento non dichiarato dal driver: misurati', 'Sample rate not reported by the driver: measured'),
    (r'Campionamento:? ', 'Sample rate '),
    (r'campionamento dichiarato', 'declared sample rate'),
    (r'formato', 'format'),
    (r'campioni per blocco', 'samples per block'),
    (r'campioni/s', 'samples/s'),
    (r'\bcentro\b', 'centre'),
    (r'\bCentro\b', 'Centre'),
    (r'Errore audio', 'Audio error'),
    (r'Scheda audio:', 'Sound card:'),
    (r'Registrazione interrotta', 'Recording stopped'),
    (r'Sto già salvando…', 'Already saving…'),
    (r'Memoria vuota: niente da salvare', 'Memory empty: nothing to save'),
    (r'Salvataggio fallito', 'Saving failed'),
    (r'Registrazione salvata:', 'Recording saved:'),
    (r'Salvato:', 'Saved:'),
    (r'Errore:', 'Error:'),
    (r'RBN collegato come', 'RBN connected as'),
]
_EN = [(re.compile(a), b) for a, b in _EN]


def en(text):
    """Il messaggio in inglese (le parti non riconosciute restano come sono)."""
    if not text:
        return text
    out = str(text)
    for rx, rep in _EN:
        out = rx.sub(rep, out)
    return out
