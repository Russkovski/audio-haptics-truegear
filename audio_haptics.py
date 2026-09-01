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
        ems_enabled=False, ems_intensity=10, ems_threshold=0.35, ems_cooldown_ms=1000,
        ems_cont_enabled=False, ems_cont_intensity=10,
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
        ems_enabled=False, ems_intensity=10, ems_threshold=0.35, ems_cooldown_ms=1000,
        ems_cont_enabled=False, ems_cont_intensity=10,
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
        ems_enabled=False, ems_intensity=10, ems_threshold=0.35, ems_cooldown_ms=1000,
        ems_cont_enabled=False, ems_cont_intensity=10,
    ),
}
PROFILE_KEYS = {"1": "impact", "2": "cinematic", "3": "balanced"}

# --------------------------------------------------------- Motor-Layout ME02
# Bestätigt am 01.09.2026: vorne 0-19, hinten 100-119, 4 Spalten x 5 Zeilen,
# Nummer = Zeile*4 + Spalte. Spalte 0-1 = links (aus Trägersicht), 2-3 = rechts.
def _cols(base, cols):
    return [base + r * 4 + c for r in range(5) for c in cols]

# Vier Quadranten: vorne links/rechts, hinten links/rechts
QUADS = {"fl": _cols(0, (0, 1)), "fr": _cols(0, (2, 3)), "bl": _cols(100, (0, 1)), "br": _cols(100, (2, 3))}
# Reduzierter Satz für Dauerbass (weniger Funkdaten): nur Zeilen 1-3
QUADS_CORE = {k: [m for m in v if (m % 100) // 4 < 3] for k, v in QUADS.items()}
LEFT = QUADS["fl"] + QUADS["bl"]
RIGHT = QUADS["fr"] + QUADS["br"]

# EMS-Armbänder: Index laut Doku 0 und 100. Annahme 0 = links, 100 = rechts (per Test prüfen!)
EMS_LEFT, EMS_RIGHT = 0, 100

# Kanal -> Quadranten-Gewichte (Windows-Standardreihenfolge: FL FR FC LFE BL BR SL SR).
# Stereo: links/rechts auf vorne UND hinten. LFE (Subwoofer) auf alle.
def channel_weights(n_ch):
    if n_ch <= 2:
        return {0: {"fl": 1, "bl": 1}, 1: {"fr": 1, "br": 1}} if n_ch == 2 else {0: {"fl": 1, "fr": 1, "bl": 1, "br": 1}}
    w = {0: {"fl": 1}, 1: {"fr": 1}, 2: {"fl": .5, "fr": .5}, 3: {"fl": 1, "fr": 1, "bl": 1, "br": 1},
         4: {"bl": 1}, 5: {"br": 1}, 6: {"fl": .5, "bl": .5}, 7: {"fr": .5, "br": .5}}
    if n_ch == 4:   # Quad: FL FR BL BR
        w = {0: {"fl": 1}, 1: {"fr": 1}, 2: {"bl": 1}, 3: {"br": 1}}
    return {c: w.get(c, {"fl": .25, "fr": .25, "bl": .25, "br": .25}) for c in range(n_ch)}

# ------------------------------------------------------- TrueGear-Anbindung
TRUEGEAR_WS = "ws://127.0.0.1:18233/v1/tact/"
EMS_MAX_INTENSITY = 100  # 100 % = die im TrueGear Player eingestellte EMS-Stärke (Player-Wert ist der Referenzwert)
EMS_HIT_MS = 150         # Dauer eines Einzelreizes (wie im Player-Editor üblich: End 150, Single)
EMS_CONT_MS = 400        # Blocklänge für Dauer-EMS; überlappende EMS-Befehle werden sonst verschluckt
EMS_MIN_GAP = 0.20       # s  Mindestabstand zwischen zwei EMS-Befehlen (global)


class TrueGear:
    """Sendet Impulse aus einem eigenen Thread, damit der Audio-Callback
    nie auf Netzwerk/Player warten muss (sonst staut sich Latenz auf)."""

    def __init__(self):
        self.ws = None
        self.q = queue.Queue(maxsize=8)
        self.last_send_ms = 0.0      # Dauer des letzten Sendens (nur Diagnose)
        self._last_connect_try = 0.0
        self._last_ems_sent = 0.0
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
        # Stereo-Kurzform: links/rechts auf vorne und hinten
        self.pulse4({"fl": left, "fr": right, "bl": left, "br": right}, duration_ms, core)

    def pulse4(self, q: dict, duration_ms: int, core: bool = False, ems=None):
        # Wird im Audio-Callback aufgerufen: nur einreihen, nicht senden.
        # ems: optional (links, rechts, once) -> wird als zusätzliche Spuren IM SELBEN Effekt gesendet,
        # weil der Player getrennte EMS-Effekte zwischen den Westen-Pulsen verschluckt.
        try:
            self.q.put_nowait((dict(q), ems, duration_ms, core))
        except queue.Full:
            pass  # lieber einen Impuls verwerfen als Verzögerung aufbauen

    def ems(self, intensity: int, duration_ms: int = 0, left: int = None, right: int = None):
        # EMS-Armbänder. duration_ms=0 -> Einzelreiz (once), >0 -> Dauerreiz für diese Zeit.
        # left/right: Stärke pro Band (None = intensity für beide).
        cap = lambda v: max(0, min(int(v), EMS_MAX_INTENSITY))
        l = cap(intensity if left is None else left)
        r = cap(intensity if right is None else right)
        if l <= 0 and r <= 0:
            return
        try:
            self.q.put_nowait(("ems", (l, r), duration_ms, False))
        except queue.Full:
            pass

    def stop_all(self):
        # Leert die Warteschlange im Player (verhindert Nachvibrieren bei Stille).
        try:
            self.q.put_nowait(("stop", 0, 0, False))
        except queue.Full:
            pass

    def _worker(self):
        while True:
            first = self.q.get()
            # Stau abbauen: pro Befehlstyp nur den neuesten behalten (Weste, EMS, Stop getrennt),
            # damit ein EMS-Befehl nie einen Westenbefehl verdrängt.
            latest = {}
            for item in [first] + self._drain():
                kind = item[0] if item[0] in ("stop", "ems") else "vest"
                latest[kind] = item
            if "stop" in latest:
                self._send_raw(json.dumps({"Method": "stop_all",
                                           "Body": base64.b64encode(b"{}").decode()}))
            if "vest" in latest:
                q, ems, dur, core = latest["vest"]
                self._send(q, dur, core, ems)
            if "ems" in latest:
                _, (l, r), dur, _ = latest["ems"]
                if time.time() - self._last_ems_sent >= EMS_MIN_GAP:
                    self._send_ems(l, r, dur)
                    self._last_ems_sent = time.time()

    def _drain(self):
        items = []
        while True:
            try:
                items.append(self.q.get_nowait())
            except queue.Empty:
                return items

    def _send(self, q: dict, duration_ms: int, core: bool = False, ems=None):
        # Ein Effekt mit bis zu vier Spuren (Quadranten); gleiche Stärke -> zusammengefasst.
        groups = QUADS_CORE if core else QUADS
        by_int = {}
        for k, motors in groups.items():
            inten = int(q.get(k, 0))
            if inten > 0:
                by_int.setdefault(inten, []).extend(motors)
        tracks = []
        for inten, motors in by_int.items():
            tracks.append({
                "start_time": 0, "end_time": duration_ms, "stop_name": "",
                "start_intensity": inten, "end_intensity": inten,
                "intensity_mode": "Const", "action_type": "Shake",
                "once": "False", "interval": 0, "index": motors,
            })
        if ems:
            tracks += self._ems_tracks(ems[0], ems[1], 0 if ems[2] else duration_ms)
        if not tracks:
            return
        effect = {"name": "AudioHaptics", "uuid": str(uuidlib.uuid4()),
                  "keep": "False", "priority": 0, "tracks": tracks}
        body = base64.b64encode(json.dumps(effect).encode()).decode()
        msg = json.dumps({"Method": "play_no_registered", "Body": body})
        self._send_raw(msg)

    def _ems_tracks(self, left: int, right: int, duration_ms: int = 0):
        # Format laut offizieller Doku: action_type "Electrical", index 0/100 = die beiden Bänder.
        # Einzelreiz: once True (Dauer EMS_HIT_MS). Dauerreiz: once False, interval = Reize innerhalb der Dauer
        # (im Player-Editor "Frequency (Total Times for Interval)").
        once = duration_ms <= 0
        if once:
            duration_ms = EMS_HIT_MS
        interval = 1 if once else max(1, int(duration_ms) // 15)
        cap = lambda v: max(0, min(int(v), EMS_MAX_INTENSITY))
        by_int = {}
        for idx, inten in ((EMS_LEFT, cap(left)), (EMS_RIGHT, cap(right))):
            if inten > 0:
                by_int.setdefault(inten, []).append(idx)
        return [{"start_time": 0, "end_time": int(duration_ms), "stop_name": "",
                 "start_intensity": inten, "end_intensity": inten,
                 "intensity_mode": "Const", "action_type": "Electrical",
                 "once": "True" if once else "False", "interval": interval, "index": idxs}
                for inten, idxs in by_int.items()]

    def _send_ems(self, left: int, right: int, duration_ms: int = 0):
        tracks = self._ems_tracks(left, right, duration_ms)
        if not tracks:
            return
        effect = {"name": "AudioHapticsEMS", "uuid": str(uuidlib.uuid4()), "keep": "False", "priority": 0,
                  "tracks": tracks}
        body = base64.b64encode(json.dumps(effect).encode()).decode()
        self._send_raw(json.dumps({"Method": "play_no_registered", "Body": body}))

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
        self.last_q = ({"fl": 0, "fr": 0, "bl": 0, "br": 0}, False, 0.0)  # (Quadranten, nur Kern, Zeit) für die GUI
        self.weights = channel_weights(channels)
        self.last_ems = 0.0
        self.ems_fired = 0.0   # Zeitpunkt des letzten EMS-Impulses (GUI-Anzeige)
        self.ems_lr = (0.0, 0.0)  # Seitenanteil des letzten EMS (GUI)
        self.last_ems_cont = 0.0
        self.peak = 0.0        # Spitzenwert des rohen Bass-Pegels (langsam abfallend) für die Anzeige

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
        self.peak = rms if rms > self.peak else self.peak - self._coef(1500, n) * self.peak

        # Räumliche Verteilung: Kanäle -> vier Quadranten (Stereo, 5.1, 7.1)
        qe = {"fl": 0.0, "fr": 0.0, "bl": 0.0, "br": 0.0}
        for c, val in enumerate(rms_ch):
            for k, wgt in self.weights.get(c, {}).items():
                qe[k] += val * wgt
        qmax = max(qe.values()) + 1e-9
        # dominanter Quadrant volle Stärke, andere proportional (leicht abgeflacht)
        scale = {k: (v / qmax) ** 0.7 for k, v in qe.items()}
        # Seiten für EMS-Bänder
        sl_, sr_ = qe["fl"] + qe["bl"], qe["fr"] + qe["br"]
        smax = max(sl_, sr_) + 1e-9
        ems_l, ems_r = (sl_ / smax) ** 0.7, (sr_ / smax) ** 0.7

        # Hüllkurven
        a = self._coef(p["attack_ms"], n) if rms > self.env else self._coef(p["release_ms"], n)
        self.env += a * (rms - self.env)
        self.slow += self._coef(400, n) * (rms - self.slow)

        now = time.time()
        level = 0.0
        level_u = 0.0   # ungedeckelt: 1.0 = Balken voll, 2.0 = doppelt so laut (für die EMS-Schwelle)
        if self.env > p["gate"]:
            level_u = (self.env - p["gate"]) * p["gain"]
            level = min(1.0, level_u)

        out_int = 0
        # Transient: schneller Sprung über langsame Referenz + Cooldown
        is_transient = (rms > p["gate"] and rms > self.slow * p["transient_ratio"]
                        and (now - self.last_pulse) * 1000 >= p["cooldown_ms"])
        if is_transient:
            # Stärke UND Dauer folgen der Lautstärke
            inten = int(p["min_intensity"] + level * (p["max_intensity"] - p["min_intensity"]))
            dur = int(p["pulse_ms"] * (0.6 + 0.8 * level))
            q = {k: int(inten * scale[k]) for k in qe}
            # EMS bei Schlägen: als Spur im selben Effekt, mit eigenem Mindestabstand
            ems = None
            if (p.get("ems_enabled") and rms >= p.get("ems_threshold", 0.35)
                    and (now - self.last_ems) * 1000 >= p.get("ems_cooldown_ms", 1000)):
                e = p.get("ems_intensity", 10)
                ems = (e * ems_l, e * ems_r, True)
                self.ems_lr = (ems_l, ems_r)
                self.last_ems = now
                self.ems_fired = now
            tg.pulse4(q, dur, ems=ems)
            self.last_q = (q, False, now)
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
                q = {k: int(inten * scale[k]) for k in qe}
                # Dauer-EMS: als Spur im selben Puls, folgt wie der Dauerbass der Lautstärke
                ems = None
                if p.get("ems_cont_enabled"):
                    e = int(level * p.get("ems_cont_intensity", 10))
                    if e >= 3:
                        ems = (e * ems_l, e * ems_r, False)
                        self.ems_lr = (ems_l, ems_r)
                        self.ems_fired = now
                tg.pulse4(q, cont_pulse_ms(), core=True, ems=ems)
                self.last_q = (q, True, now)
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
