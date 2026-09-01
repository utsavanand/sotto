# Sotto

Hold **right Option** anywhere on macOS, speak, release — the transcription is
pasted into whatever app has focus. Fully on-device dictation (Whisper
large-v3-turbo on the GPU via Apple MLX): audio never leaves the machine, and
the only network access is the one-time ~1.6 GB model download.

A minimal local alternative to Wispr Flow's core loop. Menu bar shows state:
`…` loading, `🎙` ready, `🔴` recording.

## Requirements

- Apple Silicon Mac (MLX)
- Python 3.10+ on the PATH (`brew install python`)

## Install

```sh
git clone https://github.com/utsavanand/sotto && cd sotto
./install.sh
open /Applications/Sotto.app
```

`install.sh` creates a Python environment in `~/Library/Application Support/Sotto`,
builds `Sotto.app` from it, and ad-hoc signs the bundle. Because the app is
built on your machine, there's no Gatekeeper/notarization friction.

### Permissions (one-time)

Grant **Sotto** in System Settings → Privacy & Security, then relaunch it:

1. **Microphone** — macOS prompts on first dictation
2. **Accessibility** — needed to see the hotkey and send the paste
3. **Input Monitoring** — needed alongside Accessibility for global keys

The first launch downloads the model (watch progress via menu bar → Open Log);
after that, startup is a few seconds. To start Sotto at login: System Settings
→ General → Login Items → add Sotto.app.

## Usage

Hold right Option, wait a beat (the mic stream takes a moment to open), speak,
release. Text appears at your cursor. Every event is logged to
`~/Library/Logs/Sotto.log` with timing, the transcript, the mic used and its
signal level — or the reason a dictation was dropped.

Sotto records from the **built-in microphone** even when Bluetooth headphones
are connected: AirPods-class mics switch to a low-quality codec when recording
starts and lose about a second of audio during the switch, which garbles the
start of every dictation.

## Limitations

- **Dictation overwrites your clipboard** (deliberate — see DESIGN.md). If you
  run a clipboard manager, every dictation lands in its history.
- Taps under 0.3 s are dropped as accidental
- Doesn't work in password fields, `sudo` prompts, or Keychain dialogs: macOS
  secure input blocks global key observation and synthetic paste by design.
  If the hotkey stops responding everywhere, a stuck secure-input session
  (usually a terminal) is the first suspect.
- Re-running `install.sh` rebuilds the app bundle, which can reset the
  permission grants — re-grant and relaunch if the hotkey goes quiet
- Hotkey and model are constants at the top of `sotto.py`

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh   # runs from the repo with logs in the terminal
```

## Uninstall

```sh
rm -rf /Applications/Sotto.app "~/Library/Application Support/Sotto" ~/Library/Logs/Sotto.log
```

The cached model lives in `~/.cache/huggingface` if you want that gone too.

Design decisions and their reasoning: [DESIGN.md](DESIGN.md)
