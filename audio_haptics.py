# TrueGear ME02 – Audio-to-Haptics Prototyp (Phase 3+4)
# Pipeline: Windows-Audio (WASAPI Loopback) -> Bandpass 20-120 Hz -> RMS/Envelope
#           -> Transient-Detection -> Haptik-Impulse -> TrueGear Player -> ME02
#
# Installation:  pip install pyaudiowpatch numpy scipy websocket-client
# Start:         python audio_haptics.py            (Profil "impact")
#                python audio_haptics.py cinematic
#                python audio_haptics.py balanced
# Beenden:       Strg + C
#
# Latenz-Änderungen 01.09.2026: Senden an den Player läuft in einem eigenen
# Thread, Konsolenausgabe nur noch alle 50 ms außerhalb des Audio-Callbacks.

import base64
import msvcrt
import json
import queue
import sys
import threading
import time
import uuid as uuidlib

import numpy as np
import pyaudiowpatch as pyaudio
import websocket
from scipy.signal import butter, sosfilt, sosfilt_zi

# ----------------------------------------------------------------- Profile
PROFILES = {
    # Trocken und knackig: kurze, harte Stöße, wenig Dauervibration.
    "impact": dict(
        low_hz=25, high_hz=110,
        gate=0.012, transient_ratio=2.2,
        attack_ms=2, release_ms=60,
        cooldown_ms=70, pulse_ms=60,
        gain=4.0, max_intensity=100,
        continuous=True, cont_intensity=35,
        min_intensity=20,
    ),
    # Wuchtige Explosionen + langes Grollen, Stärke folgt der Lautstärke.
    "cinematic": dict(
        low_hz=20, high_hz=100,
        gate=0.010, transient_ratio=1.8,
        attack_ms=5, release_ms=200,
        cooldown_ms=120, pulse_ms=120,
        gain=4.0, max_intensity=100,
        continuous=True, cont_intensity=80,
        min_intensity=15,
    ),
    # Allround. Werte von Basti im Test am 01.09.2026 als beste Einstellung bestätigt.
    "balanced": dict(
        low_hz=20, high_hz=120,
        gate=0.200, transient_ratio=2.0,
        attack_ms=4, release_ms=120,
        cooldown_ms=100, pulse_ms=80,
        gain=2.0, max_intensity=90,
        continuous=True, cont_intensity=55,
        min_intensity=15,
    ),
}
PROFILE_KEYS = {"1": "impact", "2": "cinematic", "3": "balanced"}

# --------------------------------------------------------- Motor-Layout ME02
# Bestätigt am 01.09.2026: vorne 0-19, hinten 100-119, 4 Spalten x 5 Zeilen,
# Nummer = Zeile*4 + Spalte. Spalte 0-1 = links (aus Trägersicht), 2-3 = rechts.
def _cols(base, cols):
    return [base + r * 4 + c for r in range(5) for c in cols]

LEFT = _cols(0, (0, 1)) + _cols(100, (0, 1))
RIGHT = _cols(0, (2, 3)) + _cols(100, (2, 3))
# Reduzierter Satz für Dauerbass (weniger Funkdaten): nur Zeilen 1-3, vorne + hinten
LEFT_CORE = [m for m in LEFT if (m % 100) // 4 < 3]
RIGHT_CORE = [m for m in RIGHT if (m % 100) // 4 < 3]

# ------------------------------------------------------- TrueGear-Anbindung
TRUEGEAR_WS = "ws://127.0.0.1:18233/v1/tact/"


class TrueGear:
    """Sendet Impulse aus einem eigenen Thread, damit der Audio-Callback
    nie auf Netzwerk/Player warten muss (sonst staut sich Latenz auf)."""

    def __init__(self):
        self.ws = None
        self.q = queue.Queue(maxsize=4)
        self.last_send_ms = 0.0      # Dauer des letzten Sendens (nur Diagnose)
        self._last_connect_try = 0.0
        self.connect()
        threading.Thread(target=self._worker, daemon=True).start()

    def connect(self):
        self._last_connect_try = time.time()
        try:
            self.ws = websocket.create_connection(TRUEGEAR_WS, timeout=2)
            self.ws.settimeout(0.5)
            print("TrueGear Player verbunden.")
        except Exception as e:
            self.ws = None
            print(f"TrueGear Player nicht erreichbar ({e}) – läuft er?")

    def pulse(self, left: int, right: int, duration_ms: int, core: bool = False):
        # Wird im Audio-Callback aufgerufen: nur einreihen, nicht senden.
        try:
            self.q.put_nowait((left, right, duration_ms, core))
        except queue.Full:
            pass  # lieber einen Impuls verwerfen als Verzögerung aufbauen

    def stop_all(self):
        # Leert die Warteschlange im Player (verhindert Nachvibrieren bei Stille).
        try:
            self.q.put_nowait(("stop", 0, 0, False))
        except queue.Full:
            pass

    def _worker(self):
        while True:
            left, right, dur, core = self.q.get()
            # Falls sich etwas gestaut hat: nur den neuesten Impuls senden.
            while True:
                try:
                    left, right, dur, core = self.q.get_nowait()
                except queue.Empty:
                    break
            if left == "stop":
                self._send_raw(json.dumps({"Method": "stop_all",
                                           "Body": base64.b64encode(b"{}").decode()}))
            else:
                self._send(left, right, dur, core)

    def _send(self, left: int, right: int, duration_ms: int, core: bool = False):
        tracks = []
        sides = ((LEFT_CORE, left), (RIGHT_CORE, right)) if core else ((LEFT, left), (RIGHT, right))
        for motors, inten in sides:
            if inten <= 0:
                continue
            tracks.append({
                "start_time": 0, "end_time": duration_ms, "stop_name": "",
                "start_intensity": int(inten), "end_intensity": int(inten),
                "intensity_mode": "Const", "action_type": "Shake",
                "once": "False", "interval": 0, "index": motors,
            })
        if not tracks:
            return
        effect = {"name": "AudioHaptics", "uuid": str(uuidlib.uuid4()),
                  "keep": "False", "priority": 0, "tracks": tracks}
        body = base64.b64encode(json.dumps(effect).encode()).decode()
        msg = json.dumps({"Method": "play_no_registered", "Body": body})
        self._send_raw(msg)

    def _send_raw(self, msg: str):
        t0 = time.perf_counter()
        try:
            if self.ws is None:
                if time.time() - self._last_connect_try < 2.0:
                    return  # nicht dauernd neu verbinden
                self.connect()
            if self.ws:
                self.ws.send(msg)
        except Exception:
            self.ws = None
        self.last_send_ms = (time.perf_counter() - t0) * 1000


# ------------------------------------------------------------------ DSP
# Sendetakt der Dauerpulse. Zu schnell -> Rückstau im Player/Funk (Nachvibrieren).
RATES = [0.10, 0.16, 0.25, 0.40]   # s Abstand; Taste "r" schaltet durch
RATE = {"i": 0}                    # Start: 0.10 s (im Test ohne Rückstau)
def cont_interval():
    return RATES[RATE["i"]]
def cont_pulse_ms():
    return int(cont_interval() * 1000) - 10   # immer kürzer als der Abstand
class BassEngine:
    def __init__(self, p: dict, rate: int, channels: int):
        self.p = p
        self.rate = rate
        self.channels = channels
        self.sos = butter(4, [p["low_hz"], p["high_hz"]], btype="band",
                          fs=rate, output="sos")
        self.zi = [sosfilt_zi(self.sos) * 0 for _ in range(channels)]
        self.env = 0.0        # schnelle Hüllkurve (Attack/Release)
        self.slow = 0.0       # langsame Referenz für Transient-Erkennung
        self.last_pulse = 0.0
        self.last_cont = 0.0
        self.active = False   # ob gerade Haptik läuft (für Stop bei Stille)
        self.last_level = 0.0 # Pegel beim letzten Dauerpuls (für Sofort-Auslösung)
        self.last_lr = (0, 0, False, 0.0)  # (links, rechts, nur Kern, Zeit) für die GUI-Anzeige

    def set_profile(self, p: dict):
        self.p = p
        self.sos = butter(4, [p["low_hz"], p["high_hz"]], btype="band",
                          fs=self.rate, output="sos")
        self.zi = [sosfilt_zi(self.sos) * 0 for _ in range(self.channels)]

    def _coef(self, ms, n):
        # Ein-Pol-Glättung, bezogen auf Blocklänge n
        return 1.0 - np.exp(-(n / self.rate) / max(ms / 1000.0, 1e-4))

    def process(self, block: np.ndarray, tg: TrueGear):
        """block: float32 [n, channels] in -1..1"""
        n = block.shape[0]
        p = self.p
        rms_ch = []
        for c in range(self.channels):
            y, self.zi[c] = sosfilt(self.sos, block[:, c], zi=self.zi[c])
            rms_ch.append(float(np.sqrt(np.mean(y * y))))
        rms = max(rms_ch)

        # Stereo-Balance (nur die ersten zwei Kanäle)
        l = rms_ch[0]
        r = rms_ch[1] if self.channels > 1 else rms_ch[0]
        tot = l + r + 1e-9
        bal_l, bal_r = l / tot, r / tot          # 0..1, Summe 1

        # Hüllkurven
        a = self._coef(p["attack_ms"], n) if rms > self.env else self._coef(p["release_ms"], n)
        self.env += a * (rms - self.env)
        self.slow += self._coef(400, n) * (rms - self.slow)

        now = time.time()
        level = 0.0
        if self.env > p["gate"]:
            level = min(1.0, (self.env - p["gate"]) * p["gain"])

        out_int = 0
        # Transient: schneller Sprung über langsame Referenz + Cooldown
        is_transient = (rms > p["gate"] and rms > self.slow * p["transient_ratio"]
                        and (now - self.last_pulse) * 1000 >= p["cooldown_ms"])
        if is_transient:
            # Stärke UND Dauer folgen der Lautstärke
            inten = int(p["min_intensity"] + level * (p["max_intensity"] - p["min_intensity"]))
            dur = int(p["pulse_ms"] * (0.6 + 0.8 * level))
            li = int(inten * min(1.0, bal_l * 2))
            ri = int(inten * min(1.0, bal_r * 2))
            tg.pulse(li, ri, dur)
            self.last_lr = (li, ri, False, now)
            self.last_pulse = now
            # nächster Dauerpuls erst, wenn dieser Impuls fertig ist (kein Rückstau)
            self.last_cont = now + dur / 1000.0 - cont_interval()
            self.active = True
            out_int = inten
        elif p["continuous"] and level > 0.05 and (
                (now - self.last_cont) >= cont_interval()
                # Sofort-Auslösung: deutlicher Anstieg wartet nicht auf den Takt
                or (level - self.last_level > 0.25 and (now - self.last_cont) >= 0.03)):
            # Dauerbass: Puls ist immer KÜRZER als der Abstand -> kein Rückstau im Player
            inten = int(level * p["cont_intensity"])
            if inten >= 5:
                li, ri = int(inten * min(1.0, bal_l * 2)), int(inten * min(1.0, bal_r * 2))
                tg.pulse(li, ri, cont_pulse_ms(), core=True)
                self.last_lr = (li, ri, True, now)
                self.active = True
            self.last_cont = now
            self.last_level = level
            out_int = inten
        elif level <= 0.0 and self.active:
            # Stille erreicht: Player-Warteschlange leeren
            tg.stop_all()
            self.active = False

        return level, out_int, is_transient


# ------------------------------------------------------------- Audio
def find_loopback(p: pyaudio.PyAudio):
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    speakers = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
    for lb in p.get_loopback_device_info_generator():
        if speakers["name"] in lb["name"]:
            return lb
    raise RuntimeError("Kein WASAPI-Loopback-Gerät gefunden.")


def main():
    name = sys.argv[1].lower() if len(sys.argv) > 1 else "balanced"
    if name not in PROFILES:
        print(f"Unbekanntes Profil. Verfügbar: {', '.join(PROFILES)}")
        return
    prof = dict(PROFILES[name])
    tg = TrueGear()

    pa = pyaudio.PyAudio()
    dev = find_loopback(pa)
    rate = int(dev["defaultSampleRate"])
    ch = max(1, int(dev["maxInputChannels"]))
    chunk = 256  # ~5 ms bei 48 kHz
    print(f"Profil: {name} | Gerät: {dev['name']} | {rate} Hz, {ch} Kanäle")
    engine = BassEngine(prof, rate, ch)

    bar_len = 30
    status = {"level": 0.0, "inten": 0, "hit": False, "hit_until": 0.0}

    def cb(in_data, frame_count, time_info, status_flags):
        # So wenig wie möglich hier drin tun: keine Konsolenausgabe, kein Netz.
        block = np.frombuffer(in_data, dtype=np.float32).reshape(-1, ch)
        level, inten, hit = engine.process(block, tg)
        status["level"], status["inten"] = level, inten
        if hit:
            status["hit_until"] = time.time() + 0.15
        return (None, pyaudio.paContinue)

    stream = pa.open(format=pyaudio.paFloat32, channels=ch, rate=rate, input=True,
                     input_device_index=dev["index"], frames_per_buffer=chunk,
                     stream_callback=cb)
    stream.start_stream()
    print("Läuft. Strg+C zum Beenden.")
    print("Tasten: +/- Gain (G) | m/n Max-Stärke (M) | ./, Schwelle (S) | r Sendetakt (T) | 1/2/3 Profil")
    try:
        while stream.is_active():
            time.sleep(0.05)   # Anzeige alle 50 ms, unabhängig vom Audio
            while msvcrt.kbhit():
                k = msvcrt.getwch()
                if k == "+":
                    prof["gain"] = round(min(20.0, prof["gain"] + 0.5), 1)
                elif k == "-":
                    prof["gain"] = round(max(0.5, prof["gain"] - 0.5), 1)
                elif k == "m":
                    prof["max_intensity"] = min(100, prof["max_intensity"] + 5)
                    prof["cont_intensity"] = min(100, prof["cont_intensity"] + 5)
                elif k == "n":
                    prof["max_intensity"] = max(10, prof["max_intensity"] - 5)
                    prof["cont_intensity"] = max(5, prof["cont_intensity"] - 5)
                elif k == ".":
                    step = 0.01 if prof["gate"] >= 0.1 else 0.002
                    prof["gate"] = round(min(1.0, prof["gate"] + step), 3)
                elif k == ",":
                    step = 0.01 if prof["gate"] > 0.1 else 0.002
                    prof["gate"] = round(max(0.0, prof["gate"] - step), 3)
                elif k == "r":
                    RATE["i"] = (RATE["i"] + 1) % len(RATES)
                elif k in PROFILE_KEYS:
                    name = PROFILE_KEYS[k]
                    prof.clear(); prof.update(PROFILES[name])
                    engine.set_profile(prof)
            b = int(status["level"] * bar_len)
            hit = time.time() < status["hit_until"]
            line = (f"\rBass [{'#' * b}{'.' * (bar_len - b)}] H {status['inten']:3d} "
                    f"{'IMPACT' if hit else '      '} {name[:4]} G{prof['gain']:.1f} "
                    f"M{prof['max_intensity']:3d} S{prof['gate']:.3f} T{int(cont_interval()*1000)} "
                    f"Tx{tg.last_send_ms:.1f}ms")
            sys.stdout.write(line[:100].ljust(100))
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
        print("\nBeendet.")


if __name__ == "__main__":
    main()
