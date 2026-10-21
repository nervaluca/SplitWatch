# FZR Skimmer 1.25

by IW1FZR

A CW receiver for **split pile-ups** and **contests** with the Perseus SDR (or any radio through
the PC sound card). The audio stays locked on the DX while the waterfall scrolls freely across the
whole received band; the decoded CW text appears next to each signal, with 5NN highlighted. After
every 5NN sent by the DX, the program marks in orange where the worked station answered — that is,
where the DX is listening.

**Language**: **EN / IT** button at the top right (English or Italian; it is remembered).

## Installation

1. Download the `FZRSkimmer` zip and unpack it anywhere (e.g. `C:\FZRSkimmer`).
2. Run **prepara_cartella.bat** once: it copies `perseususb.dll` and the `perseus*.sbs` files from
   the CW Skimmer (or Perseus software) folder. If you skip it, FZR Skimmer looks for them in the
   CW Skimmer folder anyway.
   No CW Skimmer or Perseus software? The files are in the official Microtelecom package
   **ExtIO_perseus2v4a.zip** (www.microtelecom.it); the Perseus USB driver is in
   **PerseusDriversUpdate2026a1.zip**. Copy `perseususb.dll` and all `perseus*.sbs` next to
   `FZRSkimmer.exe`.
3. **Close CW Skimmer, HDSDR and the Perseus software**: only one program at a time can use the Perseus.
4. Start `FZRSkimmer.exe`: the FZR Skimmer window opens (Edge in app mode). **Closing the window,
   or the Quit button, also stops the Perseus** and exits the program.
   From a tablet in the shack: open in the browser the address written in `fzrskimmer.log`
   (like `http://192.168.x.x:8073`). Closing the tablet browser does not stop anything: use **Quit**.
   If the receiver does not start: **Restart receiver** (bottom). The program already retries 3 times.
   Options: `--browser` opens the normal browser; `--no-browser` opens no window.

The Perseus files (`perseususb.dll`, `.sbs`) belong to Microtelecom and are not distributed with
FZR Skimmer. The sample rate (48 / 96 / 192 kHz) is chosen at the bottom of the page; if stations
appear mirrored around the centre, tick **Swap I/Q**.

## Registration

FZR Skimmer uses **the same registration as ADIF FZR** (same key, same callsign).
- Unregistered, the receiver runs **5 minutes per start**, then stops and the registration window
  appears. Playback of IQ recordings is always free.
- **Register** (top): callsign + pasted key, or **Check online** if your callsign is already enabled
  on the site. **Request a key** opens the iw1fzr.it page. Registration is free.
- If ADIF FZR is already registered on the same PC, FZR Skimmer finds its licence by itself.

## Receivers

Chosen at the bottom of the page under **Receiver**, without restarting:

- **Perseus** – wide band (48/96/192 kHz), the best way for pile-ups.
- **Radio audio** – any radio: its audio output goes into the PC sound card (e.g. the IC-7600
  USB Audio CODEC). Only the radio passband (2–3 kHz) is seen and decoded.
  - **Radio frequency**: type the frequency shown by the radio (no CAT yet).
  - **Offset**: the CW pitch (e.g. 600) if the radio shows the signal frequency, like Icom in CW;
    0 if it shows the USB carrier.
- **IQ from sound card** – receivers sending I and Q on the left/right channels (SoftRock and
  similar) at 48/96/192 kHz. **LO** = receiver centre frequency.
- **ExtIO (SDRplay, Airspy HF+…)** – *experimental, needs testing*. Any receiver with an HDSDR
  ExtIO plugin: SDRplay RSP1A/RSPdx/RSPduo, Airspy HF+ / Discovery, FunCube and others.
  - You need the receiver's **32-bit** HDSDR plugin (`ExtIO_SDRplay….dll`, `ExtIO_AirspyHF.dll`…)
    with its driver installed: if it works in HDSDR it works here.
  - FZR Skimmer looks for it in the HDSDR, SDRplay and Airspy folders and in its own folder;
    otherwise use **Plugin → Browse…** and pick the DLL.
  - **Sample rate**: the list comes from the plugin; choose 192 kHz or less (Airspy HF+: 192).
    If the receiver only offers high rates (SDRplay at 2 MHz) the program reduces them below
    200 kHz by itself (÷ n next to the value).
  - **Receiver settings** opens the plugin window (gain, antenna, AGC…).
  - Close HDSDR or any other program using the receiver.
- **Recording (WAV)** – playback, see below.

**Input**: the sound card to read from.

## S-meter

At the top there is the DX S-meter (in SSB also the RX2 one): it measures the signal in the RX
passband, like a radio (S9 = −73 dBm, 6 dB per S unit, then S9+10…+60). The bar rises at once and
falls slowly; the white tick is the peak of the last 2 s.

Until it is calibrated, dBm values carry a ≈ (approximate). To calibrate, click the S-meter:
- **Set**: put the DX on a steady signal and pick its strength (e.g. S9);
- **With the radio** (CAT, same frequency as the DX, steady signal: a carrier, a broadcast) in two
  steps, because with an antenna relay the Perseus and the radio do not receive together:
  **1 · Measure here** with the relay on the Perseus, then switch the relay off (antenna to the radio)
  and **2 · Read the radio**: it reads the radio S-meter and aligns. With separate antennas or a
  splitter just press the two buttons one after the other.
The calibration is remembered per receiver (Perseus, ExtIO, sound card).

## Radio (CAT) with Hamlib

FZR Skimmer controls the radio (IC-7600 and every rig Hamlib supports) alongside the Perseus, through
Hamlib **rigctld**. Install Hamlib for Windows (https://github.com/Hamlib/Hamlib/releases, the
`hamlib-w32-….exe` or `-w64` file, either works): FZR Skimmer finds `rigctld.exe` by itself (Hamlib,
WSJT-X, JTDX folders), otherwise **Browse…**.

At the bottom, **Radio…**: model (IC-7600 = 3063, type «7600» to find it), COM port and speed
(IC-7600: CI-V USB, 19200), then **Connect the radio**. Or connect to an rigctld already started by
another program (computer and port, usually 4532). The **Radio (CAT)** tick turns it on and off.

- **The radio follows FZR Skimmer**: DX, band buttons, mode (CW, or USB/LSB in SSB).
- **FZR Skimmer follows the radio**: turn the dial and the DX moves; if you leave the received band the
  Perseus retunes. With «Radio audio» the frequency is read automatically.
- **Split**: in SSB the TX follows RX2 (wheel = TX already on the station you found); in DXpedition
  **TX → listening** or the **T** key puts the TX on the last listening spot; right click on the
  waterfall → **TX here (split)**. The dashed red line is the radio TX.
- Changing band or switching CW ↔ SSB turns split off.
- At start-up: if the radio is already on an HF band FZR Skimmer follows it, otherwise the radio goes to the DX.

## Frequency calibration

If the receiver reads slightly off (the Perseus sometimes by a few tens of Hz), correct it with
**Correction ppm** at the bottom. The error comes from the crystal and is proportional to frequency,
so it is set in ppm and applies by itself to all bands (1 ppm = 14 Hz at 14 MHz, 7 Hz at 7 MHz).

The easy way is **Calibrate…**: pick a standard-frequency station you hear well (RWM 4996/9996/14996 kHz,
WWV and BPM 5/10/15 MHz, CHU 3330/7850/14670 kHz) and press **Measure**. The program tunes to it,
measures the carrier for 6 seconds to a hundredth of a Hz, goes back where you were and shows the
current error, signal strength and stability. You apply it with **Apply the correction**; **Reset**
removes it. A measurement on a second station should then say "already accurate".

Works with Perseus, ExtIO and IQ sound card (each has its own value). With the IQ sound card, which
cannot tune by itself, first put the standard station inside the received band.

## Mode: Normal, DXpedition, Contest or SSB

Top left, **Mode**:
- **Normal** – free listening, like CW Skimmer: the **Decoded text** panel opens on the right with the
  text of every signal visible in the waterfall, top to bottom, with callsigns, CQ and 599/5NN/TU
  highlighted. Click a row = RX on that signal. No listening search.
- **DXpedition** – split pile-up: RX locked on the DX, search for where it listens (orange lines,
  Listening, UP zone).
- **Contest** – S&P: the station list opens, the listening search is switched off and hidden.
- **SSB** – phone pile-ups. RX locked on the DX plus a second receiver, **RX2**, in stereo:
  DX in one ear and RX2 in the other (or both together), with the **DX ◀▶ RX2** knob to balance them.
  - **Mouse wheel** on the waterfall: RX2 up and down by the chosen **Step** (100, 500, 1000 Hz).
    Shift + wheel scrolls the view, Ctrl + wheel moves the DX.
  - **Click** on a voice: RX2 goes there (passband centred where you click). **RX2 = DX** brings it back.
  - Sideband **Auto** (LSB below 10 MHz, USB above) or fixed USB/LSB; 2.7 kHz filter.
  - The passbands are drawn on the waterfall: DX green, RX2 light blue with its offset (UP +5.30).
    CW decoding is off.
  - **Separate band memories** for phone: switching to SSB moves to the phone segment of the same band
    (e.g. 40 m: centre 7150, DX 7140; 20 m: 14220 / 14195); back to CW, the CW memories return.
    «Save memory» in SSB saves the phone one. No 30 m in SSB.
  - **Ctrl + wheel** (and Ctrl + click) moves **only the DX**: RX2 stays where it is, the offset changes.
    Changing band, RX2 keeps its offset instead (e.g. UP 5).
  - **🔊 DX / 🔇 DX** (**M** key) mutes the DX; **RX2 on / off** (**R** key) turns the second receiver off
    (spinning the wheel turns it back on).
  - **🔒 DX** (off by default): when on, Ctrl + wheel and arrows cannot move the DX by mistake;
    Ctrl + click, right click, typing and band buttons can.
  - **🎧 Test**: low note in the DX ear, high note in the RX2 ear. If they sound the same, the headphones,
    sound card or Windows (Settings → Accessibility → Mono audio) are mono.

## Use

- **DX audio**: starts the audio (the browser requires a click).
- **▲ / ▼ 5 kHz**, mouse wheel or drag: scroll the waterfall. The audio stays on the DX.
- **Ctrl + click** on the waterfall: DX exactly there (10 Hz step). The view stays still.
- **Ctrl + wheel**: moves the DX by 50 Hz (Ctrl + Shift: 10 Hz).
- **Right click** (or long press on a tablet): *Set DX here*, *Marker here* / *Remove this marker*,
  *Remove all markers*, *Centre view here*.
- **UP zone**: where to look for the listening frequency, in kHz above the DX (e.g. 1–5 if the DX
  says "UP 1-5"). It also keeps FT8 and other digital signals out.
- **Band** (160…10) recalls the stored centre and DX; **Save memory** stores them for the current band.
- **RX filter** (100–800 Hz): DX audio bandwidth. The green stripe on the waterfall shows the passband.
- **Sensitivity**: High = more weak calls but more errors; Medium (default); Low = clean signals only.
- **Colours / Brightness / Contrast**: waterfall palette and levels. Double click on a slider = default.
- Keyboard: PgUp/PgDn ±5 kHz, up/down arrows scroll, left/right arrows move the DX (±50 Hz, Shift ±10 Hz),
  Home = back to DX, space = pause/resume in playback.

## Station list (contest, S&P)

**☰ List** button: the stations, sorted by frequency: callsign, **CQ** if calling CQ/TEST, frequency,
dB, speed and how long ago they were heard. It contains confirmed calls (decoded the same at least
twice on the same signal, or right after CQ/TEST) and the RBN spots in the received band (purple).
**Click a row**: the audio goes to that station and the waterfall centres on it.

## Callsigns

- **Repetition**: when the same call is decoded twice on a signal it becomes **confirmed**; garbled
  readings of that signal (one wrong letter, lost beginning) are shown corrected with **?**, even
  the earlier ones.
- **Callsign file**: if the folder contains `MASTER.SCP` (Super Check Partial), `MASTER.DTA`
  (CW Skimmer), `callsign.dat` or `nominativi.txt` (one call per line), known calls are highlighted
  and one-letter differences are suggested with **?**.
- Glued words (`CQCQDE`, `DL1ABCTEST`, `TU5NN203`) are split automatically.

## Reverse Beacon Network (RBN)

**RBN** button (or the RBN box at the bottom) connects to telnet.reversebeacon.net (CW only) with
your callsign (from the registration; otherwise a **Callsign** field appears). Internet required.
- **Spot** shows/hides the labels: spotted calls appear in purple on the **left edge** of the
  waterfall at their frequency, with dB; they fade with age and disappear after 15 minutes.
- A decoded call matching a nearby spot (within 600 Hz) is shown as **sure**; one letter off a spot
  within 300 Hz is corrected with **?**.
- The RBN spots stations calling CQ (the DX, contesters), hardly ever the pile-up callers.

## Recording

Top right:
- **💾 Save last m:ss** – FZR Skimmer always keeps the last minutes in memory (5 by default, see
  **Memory** at the bottom); the button writes them to disk without stopping the receiver — handy
  to check calmly whether the DX logged you. RAM: about 23 MB per minute at 96 kHz.
- **⏺ Record** – records to disk from now until pressed again (about 35 MB per minute at 96 kHz).
- **📂** opens the `registrazioni` (recordings) folder.

Files are 24-bit IQ WAVs named like `FZR_20261017_121500Z_18085.000kHz_DX18080.00.wav`, with centre
and DX inside; HDSDR, SDR Console and CW Skimmer open them too.

**Playback**: **Receiver → Recording (WAV)**. The Perseus is released and the playback bar appears:
▶/⏸, **−15 s**, position slider, recordings list and **Browse…** for any IQ WAV. Waterfall, decoding
and listening search work as live. CW Skimmer recordings do not store the frequency: type it in
**Centre kHz** (remembered for that file). **Back to receiver** restarts the Perseus.

## Files

All settings are saved in `fzrskimmer.json` next to the exe (or in `%APPDATA%\FZRSkimmer` if that
folder is not writable). The log is `fzrskimmer.log`: the first file to look at if something goes wrong.
