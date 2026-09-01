# Changelog
## 1.3.5 — 2026-09-01

- install.sh recreates the venv when it was built by a pre-3.13 Python, so an
  upgrade can't pair old wheels with the 3.13 hash lock
- Existing log/history files are chmodded 0600 at startup (the private opener
  only covered newly created files)
- CI runs static checks on Python 3.13 (matching production) and adds an
  arm64 macOS job that dry-run resolves the hashed lock

## 1.3.4 — 2026-09-01

Security and hardening release.

- Supply chain: dependencies install from requirements.lock — every package
  pinned to an exact version with a sha256 hash (--require-hashes); the
  Whisper model is pinned to an immutable Hugging Face revision instead of a
  mutable repo reference. Requires Python 3.13 (the lock pins 3.13 wheels).
- log() is best-effort and can no longer throw from inside the exception
  handlers that keep the workers alive (full disk, broken pipe)
- Log and history files are created 0600 — transcripts stay private even if
  parent directory permissions loosen
- CI actions pinned by commit SHA, ruff pinned to an exact version

## 1.3.3 — 2026-09-01

- Audio operations are serialized through one dedicated thread: a wedged
  CoreAudio device now pins at most one thread instead of leaking one per
  recording, and new recordings are refused with a clear log line while the
  device is unresponsive (>5 s)
- A failed stream stop() no longer skips close(), which could keep the
  microphone busy and break every later recording

## 1.3.2 — 2026-09-01

Reliability release: fixes a main-thread deadlock and addresses a code review.

- Fixed: CoreAudio's stop call could block forever on a HAL mutex held by
  another audio client (observed with Wispr Flow running), freezing the
  hotkey, menu bar, and overlay. All PortAudio open/stop calls now run on
  background threads; the main thread can no longer be taken hostage.
- Fixed: holding left Option masked a right-Option release (aggregate
  modifier flag), leaving recording stuck on — now uses the device-specific
  right-Option bit
- A transcription error no longer kills the worker thread silently
- A failed startup (network, device, model cache) now shows an error alert
  and ⚠️ in the menu bar instead of hanging at "…" forever
- A damaged history line no longer prevents launch; bad lines are skipped
- install.sh stages the new bundle before replacing the old one, so a failed
  build can't destroy a working install
- Dependencies pinned to tested version ranges

## 1.3.1 — 2026-09-01

- Recording pill redesign: frosted-glass HUD background, finer 24-bar
  waveform, pulsing record dot
- Real noise gate: the waveform is a flat dotted line until the mic level
  clears an absolute margin above the rolling noise floor — ambient noise no
  longer animates the bars (min/max normalization was amplifying
  silence-level jitter)

## 1.3.0 — 2026-09-01

- Hands-free mode: double-tap right Option to lock recording on, tap once to
  stop and paste
- Recording pill is smaller and calmer: levels are normalized against a
  rolling ambient-noise floor with fast-attack/slow-decay smoothing, so the
  bars sit flat in a quiet room and move on speech
- Launching Sotto while it's already running opens the History window —
  reachable even when the menu bar icon is hidden behind the notch
- README: release badge and an architecture diagram

## 1.2.0 — 2026-09-01

- On-screen recording indicator: a floating pill at the bottom of the screen
  with a live mic level animation while the hotkey is held — visible over
  fullscreen apps, so recording state no longer depends on the menu bar icon
- History window: transcripts persist to
  ~/Library/Application Support/Sotto/history.jsonl and 🎙 > History… opens a
  scrollable window with every transcription; the menu still shows the last
  10 with click-to-copy, now surviving restarts

## 1.1.1 — 2026-09-01

- Input Monitoring is no longer required: the hotkey is observed with NSEvent
  global monitors (Accessibility only) instead of a CGEventTap. Sotto now
  needs the same two grants as Wispr Flow: Microphone and Accessibility.

## 1.1.0 — 2026-09-01

- Permission popups on launch: missing Input Monitoring / Accessibility now
  trigger the native macOS prompts plus an alert with an Open System Settings
  button, instead of failing silently into the log
- Transcription history in the menu bar: the last 10 transcripts are listed in
  the dropdown, click one to copy it back to the clipboard
- Replaced rumps with direct AppKit (status item was invisible when launched
  from the app bundle); the app no longer shows as "Python" in the menu bar

## 1.0.1 — 2026-09-01

- Fix crash on macOS Sequoia: replaced pynput with a Quartz CGEventTap on the
  main run loop and CGEventPost for the paste. pynput's key handling calls
  Text Input Source APIs from a background thread, which macOS 15 terminates
  with EXC_BREAKPOINT (dispatch_assert_queue) on the first key event.
- One dependency fewer; failed tap creation now logs a permissions pointer
  at startup instead of silently seeing no keys.

## 1.0.0 — 2026-09-01

Initial release.

- Hold-to-talk dictation: hold right Option, speak, release — transcript is
  pasted into the focused app
- On-device transcription with Whisper large-v3-turbo via MLX (Apple Silicon
  GPU); ~0.5 s per utterance on an M4 Max
- Menu bar app (`…` loading / `🎙` ready / `🔴` recording) with Open Log and
  Quit; built locally by `install.sh`, no notarization needed
- Records from the built-in microphone even when Bluetooth headphones are
  connected — Bluetooth mics lose ~1 s of audio to a codec switch when
  recording starts, which garbled transcripts
- Per-dictation log line with mic, duration, peak level, latency, and
  transcript in `~/Library/Logs/Sotto.log`
