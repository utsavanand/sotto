"""Sotto: hold right Option anywhere, speak, release — locally
transcribed text is pasted into the focused app. See DESIGN.md."""

import collections
import json
import os
import queue
import subprocess
import threading
import time

import AppKit
import huggingface_hub
import mlx_whisper
import numpy as np
import Quartz
import sounddevice as sd
from PyObjCTools import AppHelper

# kVK_RightOption / kVK_ANSI_V from Carbon's Events.h. Raw keycodes, not
# characters: pynput was dropped because its character mapping calls TIS
# (Text Input Source) APIs off the main thread, which macOS 15 kills with
# EXC_BREAKPOINT (dispatch_assert_queue).
HOTKEY_KEYCODE = 61
V_KEYCODE = 9
# NX_DEVICERALTKEYMASK: right-Option's device-specific bit. The aggregate
# NSEventModifierFlagOption stays set while LEFT Option is held, which made a
# right-Option release look like a press and left recording stuck on.
RIGHT_OPTION_MASK = 0x0040

MODEL_REPO = "mlx-community/whisper-large-v3-turbo"
# Pinned HF revision: the repo name is a mutable reference, the commit is not.
# Update deliberately (huggingface.co/api/models/<repo> -> "sha") after
# checking the diff, since the model runs inside an app holding mic and
# Accessibility permissions.
MODEL_REVISION = "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb"
SAMPLE_RATE = 16_000
MIN_SECONDS = 0.3
TAP_MAX_SECONDS = 0.35  # a press shorter than this counts as a tap
DOUBLE_TAP_SECONDS = 0.5  # two taps within this window lock hands-free mode
HISTORY_SIZE = 10
LOG_PATH = os.path.expanduser("~/Library/Logs/Sotto.log")
SUPPORT_DIR = os.path.expanduser("~/Library/Application Support/Sotto")
HISTORY_PATH = os.path.join(SUPPORT_DIR, "history.jsonl")
TITLES = {"loading": "…", "ready": "🎙", "recording": "🔴", "error": "⚠️"}
SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"

jobs = queue.Queue()
audio_ops = queue.Queue()  # serialized PortAudio operations, see audio_control()
audio_op_started = None  # monotonic start of the op in flight, None when idle
record_buf = None  # per-recording frame list; identity marks the active recording
stream = None
monitors = []
state = "loading"  # loading | ready | recording
input_device = None
input_name = "system default"
history = collections.deque(maxlen=HISTORY_SIZE)  # (time_str, text), newest first
history_version = 0
overlay = None
history_win = None
locked = False
press_time = 0.0
last_tap = 0.0


# Transcripts are sensitive: create log/history files 0600 instead of the
# umask default, in case the parent directory permissions ever loosen
def _private_opener(path, flags):
    return os.open(path, flags, 0o600)


def log(msg):
    # Best-effort by design: log() runs inside the exception handlers that
    # keep the workers alive, so it must never raise (full disk, broken pipe)
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    try:
        print(line, flush=True)
    except OSError:
        pass
    try:
        with open(LOG_PATH, "a", opener=_private_opener) as f:
            f.write(line + "\n")
    except OSError:
        pass


def append_history(text):
    global history_version
    history.appendleft((time.strftime("%H:%M"), text))
    history_version += 1
    try:
        with open(HISTORY_PATH, "a", opener=_private_opener) as f:
            f.write(json.dumps({"t": time.time(), "text": text}) + "\n")
    except OSError as e:
        log(f"could not persist history entry: {e}")


def read_history_file():
    """Returns [(epoch, text)], oldest first, skipping damaged lines — a
    truncated final write must never take the whole app down."""
    try:
        with open(HISTORY_PATH) as f:
            lines = f.readlines()
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            e = json.loads(line)
            entries.append((float(e["t"]), str(e["text"])))
        except (ValueError, KeyError, TypeError):
            log("skipping a malformed history line")
    return entries


def load_history():
    global history_version
    for epoch, text in read_history_file()[-HISTORY_SIZE:]:
        history.appendleft((time.strftime("%H:%M", time.localtime(epoch)), text))
    history_version += 1


# Bluetooth mics (AirPods etc.) switch to the low-quality HFP codec when
# recording starts, losing ~1s of audio during the switch — so prefer the
# built-in mic over the system default.
def pick_input_device():
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and ("MacBook" in d["name"] or "Built-in" in d["name"]):
            return i, d["name"]
    return None, sd.query_devices(kind="input")["name"] + " (system default)"


# CoreAudio open/stop can block indefinitely on a HAL mutex held by another
# audio client (observed as a full main-thread deadlock with Wispr Flow
# running), so every PortAudio call runs on one dedicated audio thread — the
# hotkey and UI stay alive no matter what the audio stack does, and
# serializing the ops means a wedged device pins at most that one thread
# instead of leaking a new one per recording.
def audio_control():
    global audio_op_started
    while True:
        op = audio_ops.get()
        audio_op_started = time.monotonic()
        try:
            op()
        except Exception as e:  # noqa: BLE001
            log(f"audio operation failed: {e!r}")
        audio_op_started = None


def audio_wedged():
    started = audio_op_started
    return started is not None and time.monotonic() - started > 5


def start_recording():
    global state, record_buf
    if state != "ready":
        return
    if audio_wedged():
        log(
            "audio device is not responding — recording skipped. Quit other "
            "audio apps (e.g. another dictation tool) or relaunch Sotto."
        )
        return
    state = "recording"
    buf = []
    record_buf = buf
    overlay.show()
    audio_ops.put(lambda: _open_stream(buf))


def _open_stream(buf):
    global stream, state, locked
    try:
        s = sd.InputStream(
            device=input_device,
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=lambda data, *_: buf.append(data.copy()),
        )
        s.start()
    except sd.PortAudioError as e:
        log(f"mic open failed: {e}\n(System Settings > Privacy & Security > Microphone)")
        if buf is record_buf and state == "recording":
            state = "ready"
            locked = False
            AppHelper.callAfter(overlay.hide)
        return
    if buf is record_buf and state == "recording":
        stream = s
    else:
        # Released before the stream finished opening — discard
        _shutdown_stream(s)


def _shutdown_stream(s):
    # close() must run even when stop() raises, or the abandoned stream can
    # keep the microphone device busy and wedge every later open
    try:
        s.stop()
    except sd.PortAudioError as e:
        log(f"mic stop failed: {e}")
    finally:
        try:
            s.close()
        except sd.PortAudioError:
            pass


def stop_recording():
    global state, stream, record_buf
    if state != "recording":
        return
    state = "ready"
    overlay.hide()
    s, buf = stream, record_buf
    stream = None
    record_buf = None
    audio_ops.put(lambda: _finish_recording(s, buf))


def _finish_recording(s, buf):
    if s is not None:
        t0 = time.monotonic()
        _shutdown_stream(s)
        if time.monotonic() - t0 > 3:
            log("audio device was slow to release — another audio app may be fighting for the mic")
    if not buf:
        log("dropped: no audio captured")
        return
    audio = np.concatenate(buf)[:, 0]
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


def handle_flags_changed(event):
    global locked, press_time, last_tap
    if event.keyCode() != HOTKEY_KEYCODE:
        return
    now = time.monotonic()
    if event.modifierFlags() & RIGHT_OPTION_MASK:  # key down
        if locked:
            locked = False
            stop_recording()
        else:
            press_time = now
            start_recording()
    else:  # key up
        if locked or state != "recording":
            return
        if now - press_time < TAP_MAX_SECONDS:
            # Double-tap: keep recording hands-free until the next tap
            if now - last_tap < DOUBLE_TAP_SECONDS:
                locked = True
                log("hands-free recording — tap right Option to stop")
                return
            last_tap = now
        stop_recording()


# NSEvent monitors instead of a CGEventTap: same job for a single modifier
# key, but gated on Accessibility only — a tap would additionally require
# the Input Monitoring permission (this is how Wispr Flow gets away with
# fewer grants). With Accessibility missing the global monitor silently
# never fires, hence the startup permission check.
def install_hotkey_monitors():
    global monitors
    monitors = [
        AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskFlagsChanged, handle_flags_changed
        ),
        AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskFlagsChanged, lambda e: (handle_flags_changed(e), e)[1]
        ),
    ]


model_path = None  # local snapshot dir of the pinned revision, set by backend


def transcribe(audio):
    return mlx_whisper.transcribe(audio, path_or_hf_repo=model_path)["text"].strip()


def set_clipboard(text):
    subprocess.run("pbcopy", input=text.encode(), check=True)


def paste(text):
    set_clipboard(text)
    for key_down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, V_KEYCODE, key_down)
        Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


# All slow work happens here: the hotkey handler runs on the main run loop,
# and blocking it would freeze the menu bar and delay key handling.
def worker():
    while True:
        audio = jobs.get()
        t0 = time.monotonic()
        # The sole worker must outlive any single bad job, or dictation dies
        # silently while the UI still shows ready
        try:
            text = transcribe(audio)
            if text:
                paste(text)
                append_history(text)
            log(f"[{time.monotonic() - t0:.2f}s] {text or '(empty transcription, nothing pasted)'}")
        except Exception as e:  # noqa: BLE001
            log(f"transcription failed: {e!r} — dictation continues")


def backend():
    global state, input_device, input_name
    # Without this boundary a failed download/device/model init leaves the
    # menu bar stuck on "…" forever with no explanation
    global model_path
    try:
        input_device, input_name = pick_input_device()
        log(f"mic: {input_name}")
        log(f"loading {MODEL_REPO}@{MODEL_REVISION[:8]} (first run downloads ~1.6 GB)...")
        t0 = time.monotonic()
        model_path = huggingface_hub.snapshot_download(MODEL_REPO, revision=MODEL_REVISION)
        # Warmup on silence: pays model load + Metal kernel compilation now
        # instead of on the first real dictation
        transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))
        log(f"model ready in {time.monotonic() - t0:.1f}s — hold right Option to dictate")
        state = "ready"
        threading.Thread(target=worker, daemon=True).start()
    except Exception as e:  # noqa: BLE001
        state = "error"
        log(f"startup failed: {e!r}")
        AppHelper.callAfter(startup_failed_alert, e)


def startup_failed_alert(error):
    choice = run_alert(
        "Sotto failed to start",
        f"{error}\n\nIf this was the first run, check your internet connection "
        "(the model downloads once from Hugging Face) and relaunch. Details are "
        "in the log.",
        ["Open Log", "Quit"],
    )
    if choice == 0:
        subprocess.run(["open", LOG_PATH], check=False)
    else:
        AppKit.NSApp.terminate_(None)


def run_alert(title, text, buttons):
    AppKit.NSApp.activateIgnoringOtherApps_(True)
    alert = AppKit.NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(text)
    for b in buttons:
        alert.addButtonWithTitle_(b)
    # Join all Spaces and float over fullscreen apps — otherwise the alert
    # opens on another desktop and the user never sees it
    alert.window().setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
    )
    return alert.runModal() - AppKit.NSAlertFirstButtonReturn


def prompt_missing_permissions():
    """Trigger the native macOS permission prompt, then explain the relaunch."""
    if Quartz.CGPreflightPostEventAccess():
        return
    Quartz.CGRequestPostEventAccess()
    log("missing permission: Accessibility")
    choice = run_alert(
        "Sotto needs the Accessibility permission",
        "Accessibility lets Sotto see the hotkey and paste the transcribed "
        "text.\n\nEnable Sotto in System Settings > Privacy & Security > "
        "Accessibility (it may be listed as \"Python\"), then quit Sotto from "
        "the 🎙 menu and open it again — grants only apply on a fresh launch.",
        ["Open System Settings", "Later"],
    )
    if choice == 0:
        AppKit.NSWorkspace.sharedWorkspace().openURL_(AppKit.NSURL.URLWithString_(SETTINGS_URL))


def status_item_onscreen():
    pid = os.getpid()
    wins = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID)
    for w in wins:
        if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer") == 25:
            return bool(w.get("kCGWindowIsOnscreen", False))
    return None


OVERLAY_SIZE = (176, 36)
BAR_COUNT = 24


class LevelView(AppKit.NSView):
    def drawRect_(self, _rect):
        bounds = self.bounds()
        mid = bounds.size.height / 2
        # Record dot, gently pulsing
        pulse = 0.55 + 0.45 * abs(np.sin(getattr(self, "ticks", 0) * 0.18))
        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.27, 0.23, pulse).setFill()
        AppKit.NSBezierPath.bezierPathWithOvalInRect_(((14, mid - 4), (8, 8))).fill()
        # Waveform: flat dotted line at rest, bars rise only on speech
        levels = getattr(self, "levels", [])
        AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.9).setFill()
        for i in range(BAR_COUNT):
            lvl = levels[i] if i < len(levels) else 0.0
            h = 2.5 + lvl * 20
            bar = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                ((32 + i * 5.5, mid - h / 2), (3, h)), 1.5, 1.5
            )
            bar.fill()


class Overlay(AppKit.NSObject):
    """Floating bottom-center pill with a live mic level animation."""

    def build(self):
        size = OVERLAY_SIZE
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), size),
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setLevel_(AppKit.NSScreenSaverWindowLevel)
        panel.setIgnoresMouseEvents_(True)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        # Frosted-glass HUD background instead of a flat fill
        effect = AppKit.NSVisualEffectView.alloc().initWithFrame_(((0, 0), size))
        effect.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow)
        effect.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(AppKit.NSVisualEffectStateActive)
        effect.setWantsLayer_(True)
        effect.layer().setCornerRadius_(size[1] / 2)
        effect.layer().setMasksToBounds_(True)
        panel.setContentView_(effect)
        view = LevelView.alloc().initWithFrame_(((0, 0), size))
        effect.addSubview_(view)
        self.panel, self.view, self.timer = panel, view, None

    def show(self):
        screen = AppKit.NSScreen.mainScreen().frame()
        w, h = OVERLAY_SIZE
        x = screen.origin.x + (screen.size.width - w) / 2
        self.panel.setFrame_display_(((x, screen.origin.y + 110), (w, h)), True)
        self.view.levels = []
        self.rms_history = collections.deque(maxlen=30)
        self.displayed = 0.0
        self.panel.orderFrontRegardless()
        self.timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.07, self, "tick:", None, True
        )

    def hide(self):
        if self.timer:
            self.timer.invalidate()
            self.timer = None
        self.panel.orderOut_(None)

    def tick_(self, _timer):
        rms = 0.0
        buf = record_buf
        if buf:
            chunk = buf[-1]
            rms = float(np.sqrt((chunk**2).mean()))
        self.rms_history.append(rms)
        # Noise gate with an absolute margin: the floor is the quietest recent
        # level, and nothing moves until rms clears floor*2 + 0.004. Ambient
        # room noise therefore draws a flat dotted line; only speech animates.
        # (Pure min/max normalization amplified silence-level jitter.)
        floor = sorted(self.rms_history)[max(0, len(self.rms_history) // 5)]
        gate = floor * 2.0 + 0.004
        if rms <= gate:
            target = 0.0
        else:
            ceiling = max(max(self.rms_history), gate + 0.03)
            target = min(1.0, (rms - gate) / (ceiling - gate))
        # Fast attack, slow decay reads as speech rather than jitter
        if target > self.displayed:
            self.displayed = 0.5 * self.displayed + 0.5 * target
        else:
            self.displayed = 0.75 * self.displayed + 0.25 * target
        if self.displayed < 0.04:
            self.displayed = 0.0
        self.view.ticks = getattr(self.view, "ticks", 0) + 1
        self.view.levels = (getattr(self.view, "levels", []) + [self.displayed])[-BAR_COUNT:]
        self.view.setNeedsDisplay_(True)


class HistoryWindow(AppKit.NSObject):
    """Scrollable read-only window with every transcription ever made."""

    def show(self):
        if not getattr(self, "window", None):
            self.buildWindow()
        self.text_view.setString_(self.renderText())
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def buildWindow(self):
        mask = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskResizable
            | AppKit.NSWindowStyleMaskMiniaturizable
        )
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (480, 560)), mask, AppKit.NSBackingStoreBuffered, False
        )
        window.setTitle_("Sotto History")
        window.setReleasedWhenClosed_(False)
        window.center()
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(window.contentView().bounds())
        scroll.setHasVerticalScroller_(True)
        scroll.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        tv = AppKit.NSTextView.alloc().initWithFrame_(scroll.bounds())
        tv.setEditable_(False)
        tv.setFont_(AppKit.NSFont.systemFontOfSize_(13))
        tv.setTextContainerInset_((14, 14))
        tv.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        tv.setVerticallyResizable_(True)
        tv.textContainer().setWidthTracksTextView_(True)
        scroll.setDocumentView_(tv)
        window.setContentView_(scroll)
        self.window, self.text_view = window, tv

    def renderText(self):
        entries = read_history_file()
        if not entries:
            return "No transcriptions yet.\n\nHold right Option, speak, release."
        blocks = []
        for epoch, text in reversed(entries):
            stamp = time.strftime("%b %d, %H:%M", time.localtime(epoch))
            blocks.append(f"{stamp}\n{text}")
        return "\n\n".join(blocks)


class StatusItem(AppKit.NSObject):
    def refresh_(self, _timer):
        button = self.item.button()
        if button.title() != TITLES[state]:
            button.setTitle_(TITLES[state])
        if self.menu_version != history_version:
            self.menu_version = history_version
            self.rebuildMenu()
        self.ticks += 1
        if self.ticks == 10 and status_item_onscreen() is False:
            log("WARNING: menu bar icon is hidden behind the notch — the menu bar is full")
            run_alert(
                "Sotto's icon is hidden behind the notch",
                "Your menu bar is full, so macOS placed Sotto's icon in the notch "
                "area where it can't be seen. Sotto still works — hold right "
                "Option to dictate.\n\nTo see the icon, free up space: hold ⌘ and "
                "drag unused menu bar icons off the bar, or quit other menu bar "
                "apps, then relaunch Sotto.",
                ["OK"],
            )

    def rebuildMenu(self):
        menu = AppKit.NSMenu.alloc().init()
        if history:
            for stamp, text in history:
                label = text if len(text) <= 60 else text[:57] + "…"
                entry = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    f"{stamp}  {label}", "copyTranscript:", ""
                )
                entry.setTarget_(self)
                entry.setRepresentedObject_(text)
                entry.setToolTip_("Click to copy")
                menu.addItem_(entry)
        else:
            placeholder = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "No transcriptions yet", None, ""
            )
            placeholder.setEnabled_(False)
            menu.addItem_(placeholder)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        actions = (
            ("History…", "showHistory:", "h"),
            ("Open Log", "openLog:", ""),
            ("Quit Sotto", "quit:", "q"),
        )
        for title, action, key in actions:
            entry = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            entry.setTarget_(self)
            menu.addItem_(entry)
        self.item.setMenu_(menu)

    def copyTranscript_(self, sender):
        set_clipboard(sender.representedObject())

    def showHistory_(self, _sender):
        history_win.show()

    def openLog_(self, _sender):
        subprocess.run(["open", LOG_PATH], check=False)

    def quit_(self, _sender):
        AppKit.NSApp.terminate_(None)


class AppDelegate(AppKit.NSObject):
    # Launching Sotto again while it runs (Launchpad, Finder, `open`) lands
    # here — show the history window, since the menu bar icon can be hidden
    # behind the notch on a crowded menu bar
    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _has_windows):
        history_win.show()
        return False


def install_status_item():
    delegate = StatusItem.alloc().init()
    item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
        AppKit.NSVariableStatusItemLength
    )
    item.button().setTitle_(TITLES[state])
    delegate.item = item
    delegate.menu_version = -1  # forces the first rebuildMenu from refresh_
    delegate.ticks = 0
    timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.3, delegate, "refresh:", None, True
    )
    log(f"status item installed (visible: {not item.button().isHidden()})")
    return delegate, item, timer


def main():
    global overlay, history_win
    os.makedirs(SUPPORT_DIR, exist_ok=True)
    # Migrate transcript files created by older versions to private mode;
    # _private_opener only covers newly created files
    for path in (LOG_PATH, HISTORY_PATH):
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    app = AppKit.NSApplication.sharedApplication()
    # Accessory: menu-bar only. Without this the process inherits Python.app's
    # bundle identity and takes over the app menu as "Python".
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    load_history()
    refs = install_status_item()  # noqa: F841 — keep AppKit objects alive
    overlay = Overlay.alloc().init()
    overlay.build()
    history_win = HistoryWindow.alloc().init()
    threading.Thread(target=audio_control, daemon=True).start()
    install_hotkey_monitors()
    prompt_missing_permissions()
    threading.Thread(target=backend, daemon=True).start()
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
