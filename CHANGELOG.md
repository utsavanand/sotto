# Changelog

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
