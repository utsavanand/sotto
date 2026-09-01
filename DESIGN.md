# sotto — design

A local clone of Wispr Flow's core loop: hold a key anywhere on macOS, speak,
release, and the transcribed text is inserted into whatever app has focus.
Transcription runs entirely on-device.

Revised after design review. Material changes from v1: transcription moved off
the pynput listener callback onto a worker thread (blocking the callback stalls
the macOS event tap, delays keystrokes system-wide, and can silently kill the
listener); startup warmup inference added; clipboard save/restore dropped;
`no_speech_prob` filter cut; secure-input and TCC failure modes documented.

## Goals

- Hold-to-talk dictation that works in any app (editor, browser, terminal, Slack)
- Fully local: audio never leaves the machine, works offline
- Fast enough to feel like typing: < 1.5 s **end-to-end** (key-release to text
  visible) in steady state on this machine (M4 Max). Whisper pads every input
  to a 30 s window, so short and long utterances cost nearly the same; the
  first inference after startup is much slower (Metal warmup), which is why
  startup runs a throwaway transcribe on a zero buffer.

## Non-goals (v1)

- No UI, menu bar icon, or settings screen — constants at the top of one file
- No streaming/live transcription (transcribe once on key release)
- No Wispr-style tone rewriting, dictionary, or per-app formatting
- No clipboard preservation: dictation overwrites the clipboard. The
  save/restore alternative has a timing race (restore too early and slow apps
  paste the *old* clipboard), pollutes clipboard-manager history with two
  writes per dictation, and `pbpaste` round-trips destroy non-text content.
  Overwriting is the honest, deterministic v1 behavior.
- No auto-start at login (documented as a manual `launchd` step, not built)
- No Windows/Linux

## Stack

- Python 3.13, single process, one file (`sotto.py`) + `run.sh`
- **Model**: `mlx-community/whisper-large-v3-turbo` via `mlx-whisper`.
  MLX runs on the M4 Max GPU; steady-state inference for one utterance lands
  well under a second there (weights ~1.6 GB; resident footprint is higher
  under load once activations and KV cache are counted — irrelevant at 36 GB).
  Model downloads from Hugging Face on first run, then cached in
  `~/.cache/huggingface`. Inference itself is offline.
- **Audio capture**: `sounddevice` (bundles PortAudio), 16 kHz mono float32 —
  Whisper's native input format, no resampling or ffmpeg needed
- **Hotkey**: `pynput` **raw `Listener`** — deliberately not `GlobalHotKeys`,
  whose alt/ctrl combination matching is broken on macOS (pynput #297). The
  raw listener does report `Key.alt_r` on both edges. Do not "clean this up"
  into the hotkey API.
  Default key: hold **right Option**. Caveat: right Option is a dead-key
  modifier (composes ø, ∆, …), so it's only conflict-free when held *alone* —
  and Cmd+V must not be synthesized while it's still physically down, or apps
  receive Cmd+Opt+V ("Paste and Match Style" or nothing). The worker-thread
  structure guarantees the paste happens after release.
- **Text insertion**: set clipboard via `pbcopy`, simulate Cmd+V with pynput.
  Pasting is instant regardless of length; per-character synthetic typing is
  10-100× slower and drops characters in some apps.

## Flow

```
right-Option down ──▶ ignore if a recording is already active (one boolean)
                      else start mic stream, append frames to a list
right-Option up   ──▶ stop stream
                      < 0.3 s of audio? drop it (accidental tap)
                      else put audio ndarray on a Queue and return immediately
worker thread     ──▶ loops on Queue.get(): transcribe ▸ pbcopy ▸ Cmd+V
                      prints one line per event (text, timing, or why dropped)
```

- The listener callbacks only flip state and enqueue — they return in
  microseconds. All slow work (transcription, paste) lives on one
  `threading.Thread(daemon=True)` with a `queue.Queue`. Nothing larger: no
  pool, no executor, no framework.
- Model is loaded once at startup and stays resident; startup then runs a
  warmup `transcribe()` on 1 s of zeros so the first real dictation doesn't
  pay Metal kernel compilation
- The mic stream is opened per-hold, not kept open, so the mic indicator dot
  only shows while the key is held. Cost: stream open takes ~100-200 ms, so
  speech in the first instant after keydown can clip — hold, breathe, speak.
- A second hold during a long transcription queues behind it on the Queue

## macOS permissions (manual, one-time)

The terminal app that runs this (Terminal/iTerm) needs all three:

1. **Microphone** — prompted automatically on first recording
2. **Accessibility** — required to observe global keys and send Cmd+V
3. **Input Monitoring** — required alongside Accessibility for global key
   observation on current macOS

The TCC grant is bound to the specific binary and launching terminal:
recreating the venv, upgrading Homebrew Python, or switching Terminal→iTerm
silently revokes it, and the symptom is "runs but sees no keys." Startup
therefore checks `AXIsProcessTrusted()` and prints a pointer to System
Settings instead of sitting mute.

## Failure modes

- **Secure input**: password fields, `sudo` prompts, and Keychain dialogs
  enable secure event input, which blocks event taps process-wide — both the
  hotkey and the synthetic paste stop working, by OS design. If the hotkey
  ever stops responding globally, a stuck secure-input session (usually a
  terminal) is the first suspect.
- Hugging Face unreachable on first run → mlx-whisper raises; retry when
  online (one-time download)
- No microphone permission or device held exclusively by another app →
  stream open raises per-hold; caught and printed with the reason, process
  keeps running
- Whisper hallucinating on silence (the classic "thank you for watching") →
  mitigated only by the 0.3 s minimum. A `no_speech_prob` threshold was cut
  from v1: tuning a magic number before observing a false positive risks
  silently eating real quiet speech, which is worse.
- Every event prints one console line, so "nothing happened" is always
  distinguishable from "hotkey not firing"

## Execution plan

1. Scaffold `~/ws-my-projects/local-apps/sotto/`: `sotto.py`,
   `run.sh`, `requirements.txt`, `README.md`
2. `python3 -m venv .venv` and install `mlx-whisper sounddevice pynput`
3. Verify the model end-to-end without a mic: generate a spoken wav with
   macOS `say`, load it, run it through the same `transcribe()` call the app
   uses, and check the text matches the input phrase
4. Run the app, confirm it starts, loads the model, warms up, and registers
   the listener (mic + paste need the Accessibility grant, so hold-to-talk is
   a manual user test)
5. Manual test must include: dictate once, then immediately dictate again —
   verifies the hotkey survives a completed transcription (catches the
   event-tap-death regression if the threading structure is ever undone)

## Post-v1 revisions

### Mic device selection (bug fix)

First real-world failure: with AirPods connected they are the default input,
and Bluetooth mics switch A2DP→HFP when recording starts — the switch takes
~1 s (start of speech lost) and HFP audio is narrowband, so transcriptions
came out wrong or empty. Fix: prefer the built-in microphone (device name
containing "MacBook" or "Built-in") over the system default, and log each
dictation's device, duration, and peak level so audio-path failures are
visible in the log instead of manifesting as mystery transcripts. A peak of
exactly ~0 additionally means macOS delivered no signal (mic permission), and
is reported as such instead of being transcribed into a hallucination.

### Packaging as Sotto.app

Users install by cloning the repo and running `install.sh`, which builds the
.app locally: venv in `~/Library/Application Support/Sotto`, a hand-rolled
bundle (Info.plist + zsh launcher that execs the venv python) in
`/Applications`, ad-hoc codesigned. Chosen over the alternatives because:

- Building locally means no quarantine attribute → no Gatekeeper block → no
  $99/yr notarization needed
- TCC prompts attribute to "Sotto" (the bundle), not the user's terminal —
  which also removes the v1 gotcha of grants dying with the terminal binding
- py2app/PyInstaller bundling of MLX + model was rejected: multi-GB artifact,
  fragile, and still unsigned
- A native Swift rewrite (WhisperKit) is the "real product" path but 10× the
  code for the same v1 behavior

`LSUIElement` makes it a menu-bar-only app (no Dock icon). rumps provides the
status item: state glyph (… / 🎙 / 🔴), Open Log, Quit. A 0.3 s rumps.Timer
polls the state variable because AppKit UI must only be touched from the main
thread. All logs go to `~/Library/Logs/Sotto.log` as well as stdout, since a
double-clicked app has no terminal.
