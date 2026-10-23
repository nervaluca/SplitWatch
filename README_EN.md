# FZR Skimmer 1.40

by IW1FZR

A CW receiver for **split pile-ups** and **contests** with the Perseus SDR (or any radio through
the PC sound card). The audio stays locked on the DX while the waterfall scrolls freely across the
whole received band; the decoded CW text appears next to each signal, with 5NN highlighted. After
every 5NN sent by the DX, the program marks in orange where the worked station answered — that is,
where the DX is listening.

**Language**: **EN / IT** button at the top right (English or Italian; it is remembered).

## Installation

**With the installer (recommended, from 1.34)**: from GitHub → Actions → latest build → Artifacts download
`FZRSkimmer_Setup`, unpack it and run `FZRSkimmer_Setup_1.40.exe`.
- No administrator rights needed: it installs in `%LOCALAPPDATA%\Programs\FZR Skimmer`
  (you can choose «all users», in Program Files: settings, log and licence then go to `%APPDATA%\FZRSkimmer`).
- It copies `perseususb.dll` and the `.sbs` files from CW Skimmer, the Perseus software or HDSDR by itself,
  plus the callsign list (N1MM MASTER.SCP or CW Skimmer MASTER.DTA). If you install the Perseus later:
  Start menu → FZR Skimmer → «Copy the Perseus files».
- Desktop and Start menu icons, uninstall from Settings → Apps.
- To update, just run the new setup: it closes FZR Skimmer and leaves settings, memories, licence and recordings alone.
- Coming from the zip: to keep settings and memories, copy `fzrskimmer.json` from the old folder into the new one
  (with the program closed).
- Windows may show the SmartScreen warning (unsigned program): «More info» → «Run anyway».

**With the zip** (as before):

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

## RTL-SDR dongle, VHF/UHF, FM and AM (1.40)

FZR Skimmer also works **without a Perseus**, with an **RTL-SDR** dongle (RTL-SDR Blog V3/V4, Nooelec…):
24 MHz – 1.7 GHz (10 m, 6 m, 2 m, 70 cm, 23 cm, airband, marine, PMR446, satellites); with V3/V4 direct sampling
shortwave too (with less dynamic range than the Perseus).
- **Driver**: WinUSB installed with **Zadig** (the same as SDR#). If SDR# or SDR++ see it, it is fine.
- **rtl_tcp**: the program talks to the dongle through `rtl_tcp.exe` (subfolder `rtl`, included in the setup and
  the zip; GPL v2, sources at github.com/rtlsdrblog/rtl-sdr-blog) and starts it by itself. A dongle on another PC
  or a Raspberry works too: `rtl_tcp -a 0.0.0.0` there, its address in the **rtl_tcp** box here (e.g. `192.168.1.50:1234`).
- **First start** with no Perseus but a dongle: it starts with the RTL-SDR, FM on 2 m. Otherwise at the bottom
  **Receiver → RTL-SDR (dongle)** (✓ when the PC sees it).
- At the bottom: **Gain** (Auto or manual), **HF** (direct sampling Q for V3/V4), **Bias-T**, **Chip AGC**, **rtl_tcp**.
  Crystal error: **Correction ppm** / **Calibrate…**.
- **Bands** 10, 6, 2, 70cm, 23cm (HF ones with direct sampling), each with its memory per mode.
- **FM** and **AM** modes (among the Receiver modes, with every receiver): RX1/RX2, SPLIT, NB/NR as in SSB;
  **Filter** 6–25 kHz, **SQL** squelch (0 = open, double click = 0) with the **SQ** lamp on RX1 and RX2;
  **Step** 5 / 6.25 / 8.33 / 12.5 / 25 kHz. Above 30 MHz the S-meter uses the VHF scale (S9 = −93 dBm).

## Memory bank (1.40)

**📒 Memories** (next to «Save memory», K key): frequencies with name, mode and group, for every receiver and
every mode (HF too).
- **Click** = go there, in the memory mode with its filter/squelch; the panel stays open.
- **＋ Save here**: saves the RX1 frequency with the current mode and settings (name and group).
- **✎** edit (name, group, frequency, mode), **🗑** delete (second click to confirm); **Search** and group filter.
- Ready-made: FM/SSB VHF/UHF calling, APRS, ISS, FT8 2 m and 6 m, repeaters R0–R7, PMR446 1–16,
  airband (121.5 / 123.45), marine (16, 6). **Preset** restores deleted ones.
- A memory out of reach of the receiver in use (e.g. 145.5 MHz with the Perseus) is not recalled: the status line says so.

## Radio panel (1.33)

Below the modes there is a radio-style panel:
- **SPLIT** (Receiver modes, S key): on = RX2 active; off = RX1 only, like a radio without split:
  the RX2 box disappears, wheel and clicks on the waterfall move RX1 and, with the radio connected, the radio split is turned off.
- **Only one receiver on** (split off, or RX1 or RX2 muted): you hear it in both ears.
- **RX1** (green edge): big frequency, mode and filter, S-meter, **🔊 RX1** (mute, M key) and RX1 **VOL**.
  **Wheel on the frequency** = moves the DX by the RX1 **Step** (10 Hz–1 kHz, one per Receiver mode: 100 Hz in SSB/DIGI and
  50 Hz in CW/RTTY by default; also used by Ctrl + wheel, arrows and the wheel with split off; Shift = 10 Hz; 🔒 DX blocks it).
  **Click on the frequency** = type it (kHz, e.g. `14195`, or MHz, e.g. `14.195`), Enter confirms, Esc cancels.
- **RX2** (blue edge, Receiver modes): frequency, UP/DOWN offset, S-meter, **🔊 RX2** (R key) and RX2 **VOL**,
  the wheel **Step** and **RX2 = DX** / **TX = RX2**. Wheel or click on the frequency as for RX1.
- **Audio**: **Volume** (audio on/off), **AF** (master volume), headphones (DX left / right / together), 🎧 Test,
  **💾 Save last**, **⏺ Record** and 📂.
- **Receiver**: AGC and RF, ATT and PRE, filter (CW or SSB) and sideband.

In the Skimmer modes the panel fits on one line, so the waterfall stays tall. In the Receiver modes
the **▴** button shrinks it to the two frequencies only (**▾** opens it again); M, R keys and the wheel still work.

The two **VOL** sliders replace the old DX ◀▶ RX2 knob: each receiver has its own volume next to its
mute (double click = normal).

## DSP: NB, NR, NOTCH (1.34) and display colours

In the **Receiver** box, **DSP** row:
- **NB** (noise blanker, B key): removes impulses (static crashes, motors, electric fences) before the filter.
  Click = level 1, 2, 3, then off; right click = off. Higher levels remove weaker impulses too.
- **NR** (noise reduction, N key): lowers the hiss between words and between CW elements. Level 1 light,
  3 strong (at 3 the voice gets a little «metallic»). Works on RX1 and RX2 and in the Skimmer modes too.
- **NOTCH** (SSB only): automatically kills steady carriers (tuners, heterodynes) lasting more than a second.
- NB, NR and NOTCH are remembered. NR adds a fixed 32 ms delay (even when off), the same for RX1 and RX2.

**Display** (next to Colours/Brightness/Contrast): radio panel colour, i.e. frequencies, S-meters and sliders:
night (green/blue), amber, green, blue, red. It is remembered.

## Radio controls: AGC, RF, AF, ATT, PRE, SSB filter

- **AGC** FAST (pile-ups, signals that come and go), MED, SLOW (relaxed listening, the default) or
  **OFF**: fixed gain, set with **RF** (lower it if strong signals distort).
- **AF**: volume (double click = normal).
- **ATT** 0/10/20/30 dB and **PRE** (preamplifier): Perseus only. The S-meter accounts for the attenuator
  and keeps reading the signal at the antenna.
- **SSB filter** 1.8–3.3 kHz (in SSB): the passband starts at 200 Hz.

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
- **While you transmit** the audio is muted and, as soon as you are back on receive, the receiver is ready
  at once (the AGC restarts): no more 1–2 seconds of deafness when working simplex. It knows from the
  radio PTT (CAT) and, even without CAT, from your own signal flooding the band.
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

## Mode: Skimmer (Normal, DXpedition, Contest) or Receiver (CW, SSB, RTTY, DIGI)

Top left, **Mode**:
- **Normal** – free listening, like CW Skimmer: the **Decoded text** panel opens on the right with the
  text of every signal visible in the waterfall, top to bottom, with callsigns, CQ and 599/5NN/TU
  highlighted. Click a row = RX on that signal. No listening search.
- **DXpedition** – split pile-up: RX locked on the DX, search for where it listens (orange lines,
  Listening, UP zone).
- **Contest** – S&P: the station list opens, the listening search is switched off and hidden.
- **SSB** – phone pile-ups. RX locked on the DX plus a second receiver, **RX2**, in stereo:
  DX in one ear and RX2 in the other (or both together), with each receiver's **VOL** to balance them.
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
  - **🔊 RX1 DX** (**M** key) and **🔊 RX2** (**R** key) work the same way: on (lit) = audible, off (🔇) =
    muted. RX2 off also disappears from the waterfall; spinning the wheel turns it back on.
  - **🔒 DX** (off by default): when on, Ctrl + wheel and arrows cannot move the DX by mistake;
    Ctrl + click, right click, typing and band buttons can.
  - **🎧 Test**: low note in the DX ear, high note in the RX2 ear. If they sound the same, the headphones,
    sound card or Windows (Settings → Accessibility → Mono audio) are mono.

- **Receiver: CW, RTTY, DIGI** – like SSB (RX1 on the DX, RX2 on the wheel, stereo, mute, split
  following RX2, S-meter, own band memories), but without the skimmer: a pure receiver.
  - **CW**: frequency = the signal frequency, heard at 600 Hz; width with **RX filter** (100–800 Hz).
    Uses the CW memories. Wheel steps of 10 and 50 Hz.
  - **RTTY** with **FZRTTY**, a multi-channel RTTY skimmer: it finds every visible RTTY signal by itself
    (45.45 baud, 170 Hz shift, Baudot) and decodes them all at once, with callsigns, CQ and 599
    highlighted next to the waterfall, the station list (the callsign each signal repeats most) and
    the DX text on the bottom line. Frequency = **mark** (like Icom); in the headphones mark at 2125 Hz
    and space at 2295 Hz (600 Hz filter), so the audio can also go to MMTTY. Memories: 7040, 14083,
    21083… (centre 15 kHz above: the whole RTTY segment in the waterfall).
  - **DIGI** (FT8, FT4, PSK…): USB-D, 100–3100 Hz passband, memories on the FT8 frequencies (7074,
    14074…). To decode with WSJT-X (or another digital program):
    in Windows (Settings → System → Sound → Volume mixer) send the Edge audio («FZR Skimmer») to a
    virtual audio cable (e.g. VB-Cable) and pick that cable as WSJT-X input; headphones «DX and RX2
    together» and RX2 off (🔇 RX2).
  - With the radio connected (CAT) the radio mode follows: CW, USB/LSB, RTTY, USB-D (DIGI).

## Use

- **Volume**: starts the audio (the browser requires a click).
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
  Home = back to DX, S = SPLIT (Receiver modes), B = NB, N = NR, K = memory bank, space = pause/resume in playback.

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

In the **Audio** box of the radio panel (compact in the Skimmer modes: 💾 m:ss and ● REC):
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
