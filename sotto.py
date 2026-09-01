"""Sotto: hold right Option anywhere, speak, release — locally
transcribed text is pasted into the focused app. See DESIGN.md."""

import os
import queue
import subprocess
import threading
import time

import mlx_whisper
import numpy as np
import Quartz
import rumps
import sounddevice as sd

# kVK_RightOption / kVK_ANSI_V from Carbon's Events.h. Raw keycodes, not
# characters: pynput was dropped because its character mapping calls TIS
# (Text Input Source) APIs off the main thread, which macOS 15 kills with
# EXC_BREAKPOINT (dispatch_assert_queue).
HOTKEY_KEYCODE = 61
V_KEYCODE = 9

MODEL = "mlx-community/whisper-large-v3-turbo"
SAMPLE_RATE = 16_000
MIN_SECONDS = 0.3
LOG_PATH = os.path.expanduser("~/Library/Logs/Sotto.log")
TITLES = {"loading": "…", "ready": "🎙", "recording": "🔴"}

jobs = queue.Queue()
frames = []
stream = None
tap = None
state = "loading"  # loading | ready | recording
input_device = None
input_name = "system default"


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


# Bluetooth mics (AirPods etc.) switch to the low-quality HFP codec when
# recording starts, losing ~1s of audio during the switch — so prefer the
# built-in mic over the system default.
def pick_input_device():
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and ("MacBook" in d["name"] or "Built-in" in d["name"]):
            return i, d["name"]
    return None, sd.query_devices(kind="input")["name"] + " (system default)"


def start_recording():
    global stream, state
    if state != "ready":
        return
    state = "recording"
    frames.clear()
    try:
        stream = sd.InputStream(
            device=input_device,
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=lambda data, *_: frames.append(data.copy()),
        )
        stream.start()
    except sd.PortAudioError as e:
        state = "ready"
        log(f"mic open failed: {e}\n(System Settings > Privacy & Security > Microphone)")


def stop_recording():
    global stream, state
    if state != "recording":
        return
    state = "ready"
    stream.stop()
    stream.close()
    stream = None
    if not frames:
        log("dropped: no audio captured")
        return
    audio = np.concatenate(frames)[:, 0]
    secs = len(audio) / SAMPLE_RATE
    if secs < MIN_SECONDS:
        log(f"dropped: {secs:.2f}s is under the {MIN_SECONDS}s minimum")
        return
    peak = float(np.abs(audio).max())
    if peak < 1e-6:
        log(
            f"dropped: {secs:.1f}s of pure silence — macOS delivered no mic signal "
            "(check System Settings > Privacy & Security > Microphone)"
        )
        return
    log(f"recorded {secs:.1f}s on '{input_name}' (peak {peak:.3f}), transcribing...")
    jobs.put(audio)


def tap_callback(_proxy, type_, event, _refcon):
    if type_ in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
        Quartz.CGEventTapEnable(tap, True)
        return event
    if type_ == Quartz.kCGEventFlagsChanged:
        keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        if keycode == HOTKEY_KEYCODE:
            if Quartz.CGEventGetFlags(event) & Quartz.kCGEventFlagMaskAlternate:
                start_recording()
            else:
                stop_recording()
    return event


def install_hotkey_tap():
    global tap
    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionListenOnly,
        Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged),
        tap_callback,
        None,
    )
    if tap is None:
        log(
            "WARNING: could not install the hotkey listener — grant Sotto in System "
            "Settings > Privacy & Security > Accessibility and Input Monitoring, "
            "then relaunch."
        )
        return False
    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetMain(), source, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    Quartz.CFRunLoopWakeUp(Quartz.CFRunLoopGetMain())
    return True


def transcribe(audio):
    return mlx_whisper.transcribe(audio, path_or_hf_repo=MODEL)["text"].strip()


def paste(text):
    subprocess.run("pbcopy", input=text.encode(), check=True)
    for key_down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, V_KEYCODE, key_down)
        Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


# All slow work happens here: the tap callback runs on the main run loop, and
# blocking it stalls keyboard events system-wide until macOS disables the tap.
def worker():
    while True:
        audio = jobs.get()
        t0 = time.monotonic()
        text = transcribe(audio)
        if text:
            paste(text)
        log(f"[{time.monotonic() - t0:.2f}s] {text or '(empty transcription, nothing pasted)'}")


def backend():
    global state, input_device, input_name
    input_device, input_name = pick_input_device()
    log(f"mic: {input_name}")
    log(f"loading {MODEL} (first run downloads ~1.6 GB)...")
    t0 = time.monotonic()
    # Warmup on silence: pays model load + Metal kernel compilation now instead
    # of on the first real dictation
    transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))
    if not install_hotkey_tap():
        return
    log(f"model ready in {time.monotonic() - t0:.1f}s — hold right Option to dictate")
    state = "ready"
    threading.Thread(target=worker, daemon=True).start()


class SottoApp(rumps.App):
    def __init__(self):
        super().__init__("Sotto", title=TITLES[state], quit_button="Quit Sotto")
        self.menu = [
            rumps.MenuItem("Open Log", callback=lambda _: subprocess.run(["open", LOG_PATH], check=False))
        ]
        # Poll state instead of pushing: AppKit UI must only be touched from the main thread
        rumps.Timer(self._refresh, 0.3).start()

    def _refresh(self, _):
        if self.title != TITLES[state]:
            self.title = TITLES[state]


def main():
    threading.Thread(target=backend, daemon=True).start()
    SottoApp().run()


if __name__ == "__main__":
    main()
