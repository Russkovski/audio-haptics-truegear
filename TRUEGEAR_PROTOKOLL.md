# TrueGear-Protokoll – Analyseergebnis (Schritte 1–4)

Stand: 31.08.2026 · Quellen: `vr-commiter/Simhub` (MyTrueGear.cs + TrueGearSDK.dll, dekompiliert/Strings) und die offizielle Doku `vr-commiter/How-to-connect-the-TrueGear-suit`.

## 1. Wie externe Apps TrueGear ansprechen

TrueGear Player fungiert als lokaler **WebSocket-Server**:

```
ws://127.0.0.1:18233/v1/tact/
```

Kein Bluetooth-Reverse-Engineering nötig – der Player ist die Middleware. Genau wie erhofft.

## 2. Nachrichtenformat

JSON über WebSocket. Der eigentliche Effekt steckt **Base64-codiert** im Feld `Body`:

```json
{ "Method": "play_no_registered", "Body": "<base64(EffectJSON)>" }
```

Im SDK gefundene Methoden (Strings aus TrueGearSDK.dll):
- `register_app` (SimHub nutzt ID `-10003` + App-Name)
- `play_no_registered` ← **für uns ideal: Effekt direkt senden, ohne Registrierung**
- `play_effect_by_content`, `play_effect_by_uuid`, `seek_by_uuid`
- `stop`, `stop_name`, `stop_all`

## 3. Effect-JSON (decodierter Body)

```json
{
  "name": "MeinEffekt",
  "uuid": "eindeutige-id",
  "keep": "False",
  "priority": 0,
  "tracks": [{
    "start_time": 0,
    "end_time": 100,
    "stop_name": "",
    "start_intensity": 80,
    "end_intensity": 80,
    "intensity_mode": "Const",     // Const | Fade | FadeInAndOut
    "action_type": "Shake",        // Shake | Electrical (EMS)
    "once": "False",
    "interval": 0,                 // nur für Electrical
    "index": [0, 1, 4, 5]          // Motor-IDs
  }]
}
```

Zeiten in ms, Intensität ca. 0–100.

## 4. Motor-Adressierung → Spatial Mapping ist möglich! 🎉

Bestätigt am 01.09.2026 an der Weste: **vorne 0–19, hinten 100–119**, je 4 Spalten × 5 Zeilen, Nummer = Zeile·4 + Spalte (Spalte 0–1 = links aus Trägersicht). Einzelne Motoren sind direkt adressierbar → Links/Rechts- und Front/Rear-Mapping ist möglich.

Praxis-Erkenntnis: Der Player verarbeitet Effekte nacheinander. Werden Pulse schneller gesendet, als sie dauern, staut sich eine Warteschlange (Weste vibriert nach). Pulsdauer daher immer kürzer als der Sendeabstand halten.

## 4b. EMS-Armbänder (bestätigt 01.09.2026)

- `action_type: "Electrical"`, `index`: **0 = linkes Band, 100 = rechtes Band** (per Test bestätigt).
- Einzelreiz: `end_time: 0`, `once: "True"`. Dauerreiz: `end_time` = Dauer in ms, `once: "False"`, `interval` = Anzahl der Reize innerhalb der Dauer (im Player-Editor „Frequency (Total Times for Interval)“, Schalter „Single“ = once).
- Die Intensität (0–100) ist relativ zur im TrueGear Player eingestellten EMS-Stärke – der Player-Wert ist der Referenzwert.
- **Wichtig:** Werden EMS-Effekte als eigene Effekte zwischen schnellen Westen-Pulsen gesendet, verschluckt der Player einen Großteil davon. Zuverlässig ist nur, Shake- und Electrical-Spuren **im selben Effekt** (ein `play_no_registered`) zu senden.
- Einzelreiz funktioniert zuverlässig mit `end_time: 150` + `once: "True"` (wie im Player-Editor).

## 5. Latenz-Einschätzung

- Lokaler WebSocket, keine Registrierung nötig, Impuls-Effekte mit `end_time` ≈ Pulsdauer → sehr geringer Overhead.
- Für Audio-Haptics: pro Transient einen kurzen `play_no_registered`-Puls senden; für Dauer-Bass entweder wiederholte kurze Pulse oder längere Tracks mit `stop_name`/`stop_all` zum Abbrechen.

## 6. Lizenzsituation ⚠️

- Weder `Simhub` noch `How-to-connect-the-TrueGear-suit` enthalten eine LICENSE-Datei (Stand 31.08.2026) → Code daraus **nicht kopieren** (kein Nutzungsrecht ohne Lizenz).
- Unser Vorteil: Wir brauchen den Code nicht. Das Protokoll ist offiziell dokumentiert, wir implementieren es **von Grund auf selbst** (Clean-Room auf Basis der öffentlichen Doku). Die Doku als Quelle im README nennen.
- `TrueGearSDK.dll` ist proprietär → nicht ins Repo aufnehmen. Brauchen wir auch nicht.
- Namensvorschlag ohne Markenkonflikt: z. B. „AudioPulse for TrueGear (inoffiziell)".

## 7. Vorgeschlagene Architektur (unverändert sinnvoll)

```
AudioCapture (WASAPI Loopback, z. B. C# NAudio oder Python soundcard)
   → DSP (Biquad-Lowpass 20–120 Hz, RMS, Envelope, Transient/Onset-Detection,
          Gate, Attack/Release, Cooldown)
   → HapticEngine (Impuls vs. Continuous, Intensitäts-Mapping, Rate-Limit)
   → TrueGearInterface (WebSocket-Client, Reconnect, play_no_registered)
   → Profiles (Cinematic/Impact/Balanced/Custom als JSON)
   → UI (Start/Stop, Pegelanzeige, Test-Button)
```

Empfehlung Sprache: **C#/.NET (WPF + NAudio)** – natives WASAPI Loopback mit niedriger Latenz, einfache GUI, gleiche Welt wie die bestehenden TrueGear-Integrationen. Python nur für Prototyping.

## 8. Nächster Schritt (Phase 2)

`truegear_test.py` auf dem Windows-PC mit laufendem TrueGear Player ausführen → Enter → Weste muss 100 ms vibrieren. Wenn das klappt: AudioCapture + DSP.
