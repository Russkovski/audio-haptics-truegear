# Audio Haptics for TrueGear ME02

Turns the bass of **any** PC game, video or music into haptic feedback on the TrueGear ME02 vest – no per-game mod needed.

> Unofficial community project. Not made by, affiliated with or supported by TrueGear.

**Deutsch:** Macht den Bass aus jedem Spiel, Video oder Lied auf der TrueGear ME02 spürbar – ohne Spiel-Mod. Voraussetzung ist nur der laufende TrueGear Player. Installation: Setup aus den [Releases](../../releases) laden, starten, fertig.

## How it works

```
Windows audio (WASAPI loopback) → band-pass 20–120 Hz → envelope + transient detection
→ haptic mapping (left/right) → TrueGear Player (local WebSocket) → ME02
```

The app listens to whatever Windows is playing, extracts the bass, and sends short vibration commands to the TrueGear Player, which forwards them to the vest. Your audio is not modified.

## Install (Windows)

1. Install and start the **TrueGear Player**, connect the vest.
2. Download `AudioHaptics-Setup.exe` from the [Releases](../../releases) page and run it.
3. Start Audio Haptics, pick your playback device, press **Start**.

Windows SmartScreen may warn about an unknown publisher (the build is not code-signed). Choose "More info → Run anyway".

## Using it

| Control | What it does |
|---|---|
| Threshold | Bass level below which nothing triggers. Raise it if the vest reacts to everything. |
| Gain | Boost above the threshold. Raise it if hits feel too weak. |
| Impulse strength | Max intensity for bass hits (explosions, shots). |
| Continuous strength | Max intensity for sustained bass / rumble. |
| Attack / Release | How fast detection reacts / how fast vibration fades. |
| Cooldown | Minimum gap between two impulses. |
| Bass from / to | Frequency band that is analysed. |
| Send rate | How often commands go to the vest. If the vest keeps vibrating after you stop audio, choose a slower rate. |
| EMS arm cuffs (optional) | Off by default. "On hits": strong single hits trigger the cuffs (threshold, min. gap). "Continuous": cuffs follow the bass level. Left/right cuff follow the audio. Own strength sliders (100 % = the EMS strength set in the TrueGear Player). Read the warning in the app before enabling. |
| Surround | With a 5.1/7.1 playback device, front channels drive the front of the vest, rear/side channels the back, LFE everything. Stereo: left/right on front and back. |

Profiles **impact**, **cinematic** and **balanced** are included; your own profiles are stored in `%LOCALAPPDATA%\AudioHaptics\profiles.json`. The app and its built-in **Help** are available in English and German (switch top right).

Some delay between sound and vibration is normal. It comes from the TrueGear Player and the wireless link to the vest, not from this app.

## Run from source

```
pip install customtkinter pyaudiowpatch numpy scipy websocket-client
python audio_haptics_gui.py
```

`audio_haptics.py` is the engine (also runs standalone in a console), `audio_haptics_gui.py` the GUI.
`build.bat` creates the single-file EXE with PyInstaller and, if Inno Setup is installed, the installer.

## TrueGear protocol notes

See [TRUEGEAR_PROTOKOLL.md](TRUEGEAR_PROTOKOLL.md). Short version: the TrueGear Player runs a local WebSocket server at `ws://127.0.0.1:18233/v1/tact/`; effects are sent as base64-encoded JSON with method `play_no_registered`. Motor indices: front 0–19, back 100–119 (4 columns × 5 rows, index = row·4 + column). EMS cuffs: `action_type` "Electrical", index 0 = left cuff, 100 = right cuff.

This implementation was written from scratch based on TrueGear's public documentation
([vr-commiter/How-to-connect-the-TrueGear-suit](https://github.com/vr-commiter/How-to-connect-the-TrueGear-suit)). No TrueGear code or SDK files are included.

## License

MIT – see [LICENSE](LICENSE). Third-party libraries: CustomTkinter (MIT), PyAudioWPatch (MIT), NumPy/SciPy (BSD), websocket-client (Apache 2.0), PyInstaller (GPL with bootloader exception – not distributed in source form).
