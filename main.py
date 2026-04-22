import atexit
import fcntl
import os
import platform
import subprocess
import threading
import time
import traceback
import sys
from pathlib import Path
from typing import Callable

IS_MAC = platform.system() == "Darwin"
PROJECT_DIR = Path(__file__).resolve().parent
LOCAL_VENV_PYTHON = PROJECT_DIR / ".venv" / "bin" / "python"
LOCK_FILE = PROJECT_DIR / ".autotyper.lock"


def _relaunch_in_local_venv(exc: ModuleNotFoundError) -> None:
    current_python = Path(sys.executable)
    current_prefix = Path(sys.prefix).resolve()
    local_venv_prefix = (PROJECT_DIR / ".venv").resolve()

    if LOCAL_VENV_PYTHON.exists() and current_prefix != local_venv_prefix:
        print(
            "Required automation packages are not available in "
            f"{current_python}. Re-launching with {LOCAL_VENV_PYTHON}..."
        )
        os.execv(
            str(LOCAL_VENV_PYTHON),
            [str(LOCAL_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
        )

    missing_module = exc.name or "a required dependency"
    raise ModuleNotFoundError(
        f"Missing dependency: {missing_module}. Run this script with "
        f"{LOCAL_VENV_PYTHON} or reinstall the project dependencies."
    ) from exc


try:
    import pyautogui
    import pyperclip
except ModuleNotFoundError as exc:
    _relaunch_in_local_venv(exc)

# `pynput` uses `alt` for the Mac Option key.
TRIGGER_HOTKEYS = ["<ctrl>+<alt>+v"]
TYPE_HOTKEYS = ["<ctrl>+<alt>+t"]
DELAYED_TYPE_HOTKEYS = ["<ctrl>+<alt>+g"]
QUIT_HOTKEYS = ["<ctrl>+<alt>+b"]

if IS_MAC:
    TRIGGER_HOTKEYS.append("<cmd>+<alt>+v")
    TYPE_HOTKEYS.append("<cmd>+<alt>+t")
    DELAYED_TYPE_HOTKEYS.append("<cmd>+<alt>+g")
    QUIT_HOTKEYS.append("<cmd>+<alt>+b")

TRIGGER_HOTKEY_LABEL = "Ctrl+Option+V or Cmd+Option+V" if IS_MAC else "Ctrl+Alt+V"
TYPE_HOTKEY_LABEL = "Ctrl+Option+T or Cmd+Option+T" if IS_MAC else "Ctrl+Alt+T"
DELAYED_TYPE_HOTKEY_LABEL = (
    "Ctrl+Option+G or Cmd+Option+G" if IS_MAC else "Ctrl+Alt+G"
)
QUIT_HOTKEY_LABEL = "Ctrl+Option+B or Cmd+Option+B" if IS_MAC else "Ctrl+Alt+B"

SOURCE_FILE = Path(__file__).with_name("code.txt")
HOTKEY_RELEASE_DELAY = 0.2
CLIPBOARD_SETTLE_DELAY = 0.1
PASTE_RETRY_DELAY = 0.15
TYPE_INTERVAL = 0.001
DELAYED_TYPE_WAIT = 2.5
DELAYED_TYPE_INTERVAL = 0.02
DELAYED_LINE_PAUSE = 0.2
OSASCRIPT_TIMEOUT = 2.0
LISTENER_RESTART_DELAY = 0.5

_typing_lock = threading.Lock()
_keyboard_module = None
_instance_lock_handle = None


def _ensure_mac_input_permissions() -> bool:
    if not IS_MAC:
        return True

    try:
        from Quartz import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
    except Exception as exc:
        print(
            "Could not verify macOS Accessibility permissions automatically: "
            f"{exc}"
        )
        return True

    trusted = bool(
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    )
    if trusted:
        return True

    print("macOS is blocking global hotkeys or simulated typing for this script.")
    print(
        "Enable access in System Settings > Privacy & Security > Accessibility "
        "for the app that launches Python, then try again."
    )
    print(
        "If the shortcut still does not fire, also enable the same app under "
        "System Settings > Privacy & Security > Input Monitoring."
    )
    print(f"Current Python: {Path(sys.executable).resolve()}")
    return False


def _release_instance_lock() -> None:
    global _instance_lock_handle

    if _instance_lock_handle is None:
        return

    try:
        fcntl.flock(_instance_lock_handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass

    _instance_lock_handle.close()
    _instance_lock_handle = None


def _acquire_instance_lock() -> bool:
    global _instance_lock_handle

    if _instance_lock_handle is not None:
        return True

    lock_handle = LOCK_FILE.open("w", encoding="utf-8")

    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_handle.close()
        print("Autotyper is already running in another window.")
        print("Stop the older copy with the quit hotkey or close that terminal first.")
        return False

    lock_handle.write(str(os.getpid()))
    lock_handle.flush()
    _instance_lock_handle = lock_handle
    atexit.register(_release_instance_lock)
    return True


def _load_keyboard_module():
    global _keyboard_module

    if _keyboard_module is None:
        try:
            from pynput import keyboard as pynput_keyboard
        except ModuleNotFoundError as exc:
            _relaunch_in_local_venv(exc)
            raise

        _keyboard_module = pynput_keyboard

    return _keyboard_module


def _register_hotkeys(
    hotkeys: dict[str, Callable[[], None]],
    combinations: list[str],
    handler: Callable[[], None],
) -> None:
    for combination in combinations:
        hotkeys[combination] = handler


def _paste_shortcut() -> tuple[object, str]:
    keyboard = _load_keyboard_module()

    if IS_MAC:
        return (keyboard.Key.cmd, "v")
    return (keyboard.Key.ctrl, "v")


def _send_paste_shortcut() -> None:
    modifier, key = _paste_shortcut()

    if IS_MAC:
        try:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to keystroke "v" using command down',
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=OSASCRIPT_TIMEOUT,
            )
            return
        except Exception as exc:
            print(f"Native paste failed ({exc}). Falling back to simulated paste.")

    try:
        keyboard = _load_keyboard_module()
        keyboard_controller = keyboard.Controller()
        keyboard_controller.press(modifier)
        keyboard_controller.press(key)
        keyboard_controller.release(key)
        keyboard_controller.release(modifier)
        return
    except Exception:
        # Fall back to pyautogui if the native controller is rejected.
        pass

    if IS_MAC:
        pyautogui.hotkey("command", key)
        return

    pyautogui.hotkey("ctrl", key)


def _run_background_job(
    label: str,
    job: Callable[[], None],
    on_finish: Callable[[], None] | None = None,
) -> None:
    try:
        job()
    except Exception as exc:
        print(f"{label} failed: {exc.__class__.__name__}: {exc}")
        traceback.print_exc()
    finally:
        if on_finish is not None:
            on_finish()


def _start_background_job(
    label: str,
    job: Callable[[], None],
    on_finish: Callable[[], None] | None = None,
) -> None:
    threading.Thread(
        target=_run_background_job,
        args=(label, job, on_finish),
        daemon=True,
    ).start()


def _load_text() -> tuple[str, str]:
    clipboard_text = pyperclip.paste()
    if clipboard_text:
        return clipboard_text, "clipboard"

    if SOURCE_FILE.exists():
        file_text = SOURCE_FILE.read_text(encoding="utf-8")
        if file_text:
            return file_text, str(SOURCE_FILE)

    return "", ""


def paste_text() -> None:
    if _typing_lock.locked():
        print("Already pasting. Wait for the current run to finish.")
        return

    text, source = _load_text()
    if not text:
        print("Nothing to paste. Copy code first or add text to code.txt.")
        return

    with _typing_lock:
        previous_clipboard = pyperclip.paste()
        using_temp_clipboard = previous_clipboard != text

        print(f"Pasting {len(text)} characters from {source}...")

        # Give the trigger hotkey time to release before we send Cmd/Ctrl+V.
        time.sleep(HOTKEY_RELEASE_DELAY)

        try:
            if using_temp_clipboard:
                pyperclip.copy(text)
                time.sleep(CLIPBOARD_SETTLE_DELAY)

            _send_paste_shortcut()
            time.sleep(PASTE_RETRY_DELAY)
        finally:
            if using_temp_clipboard:
                pyperclip.copy(previous_clipboard)

        print("Done.")


def _paste_chunk(chunk: str) -> None:
    pyperclip.copy(chunk)
    time.sleep(CLIPBOARD_SETTLE_DELAY)
    _send_paste_shortcut()
    time.sleep(PASTE_RETRY_DELAY)


def _type_line(line: str) -> None:
    pyautogui.write(line, interval=DELAYED_TYPE_INTERVAL)


def _press_enter() -> None:
    pyautogui.press("enter")


def type_text() -> None:
    if _typing_lock.locked():
        print("Already sending text. Wait for the current run to finish.")
        return

    text, source = _load_text()
    if not text:
        print("Nothing to type. Copy code first or add text to code.txt.")
        return

    with _typing_lock:
        print(f"Typing {len(text)} characters from {source}...")

        # Give the trigger hotkey time to release before typing starts.
        time.sleep(HOTKEY_RELEASE_DELAY)
        pyautogui.write(text, interval=TYPE_INTERVAL)
        print("Done.")


def _paste_text_line_by_line(text: str) -> None:
    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks = normalized_text.splitlines(keepends=True)

    if not chunks and normalized_text:
        chunks = [normalized_text]

    original_clipboard = pyperclip.paste()

    try:
        for index, chunk in enumerate(chunks):
            _paste_chunk(chunk)

            if index < len(chunks) - 1:
                time.sleep(DELAYED_LINE_PAUSE)
    finally:
        pyperclip.copy(original_clipboard)


def _type_text_line_by_line(text: str) -> None:
    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized_text.split("\n")

    for index, line in enumerate(lines):
        if line:
            _type_line(line)

        if index < len(lines) - 1:
            _press_enter()
            time.sleep(DELAYED_LINE_PAUSE)


def delayed_type_text() -> None:
    if _typing_lock.locked():
        print("Already sending text. Wait for the current run to finish.")
        return

    text, source = _load_text()
    if not text:
        print("Nothing to paste. Copy code first or add text to code.txt.")
        return

    with _typing_lock:
        print(
            f"Typing {len(text)} characters from {source} in "
            f"{DELAYED_TYPE_WAIT:.1f} seconds..."
        )
        print("Click inside the CodeTantra editor now. It will type line by line.")

        time.sleep(DELAYED_TYPE_WAIT)
        _type_text_line_by_line(text)
        print("Done.")


def main() -> None:
    if not _acquire_instance_lock():
        return

    if not _ensure_mac_input_permissions():
        return

    keyboard = _load_keyboard_module()

    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True

    stop_event = threading.Event()
    restart_listener_event = threading.Event()

    def request_listener_restart() -> None:
        restart_listener_event.set()

    def on_trigger() -> None:
        _start_background_job(
            "Paste job",
            paste_text,
            on_finish=request_listener_restart,
        )

    def on_type() -> None:
        _start_background_job(
            "Type job",
            type_text,
            on_finish=request_listener_restart,
        )

    def on_delayed_type() -> None:
        _start_background_job(
            "Delayed type job",
            delayed_type_text,
            on_finish=request_listener_restart,
        )

    def on_quit() -> None:
        print("Stopping autotyper...")
        stop_event.set()

    hotkeys: dict[str, Callable[[], None]] = {}
    _register_hotkeys(hotkeys, TRIGGER_HOTKEYS, on_trigger)
    _register_hotkeys(hotkeys, TYPE_HOTKEYS, on_type)
    _register_hotkeys(hotkeys, DELAYED_TYPE_HOTKEYS, on_delayed_type)
    _register_hotkeys(hotkeys, QUIT_HOTKEYS, on_quit)

    print("Autotyper is running.")
    print(
        f"Copy code (or fill {SOURCE_FILE.name}), focus the target, "
        f"and press {TRIGGER_HOTKEY_LABEL} to paste it."
    )
    print(f"Press {TYPE_HOTKEY_LABEL} to type it very fast instead.")
    print(
        f"Press {DELAYED_TYPE_HOTKEY_LABEL} for delayed slow line-by-line typing "
        f"when browser focus is tricky."
    )
    print(f"Press {QUIT_HOTKEY_LABEL} to exit.")

    while not stop_event.is_set():
        listener = None
        restart_listener_event.clear()

        try:
            with keyboard.GlobalHotKeys(hotkeys) as listener:
                while (
                    listener.is_alive()
                    and not stop_event.is_set()
                    and not restart_listener_event.is_set()
                ):
                    time.sleep(0.1)
        except Exception as exc:
            print(
                "Hotkey listener stopped unexpectedly: "
                f"{exc.__class__.__name__}: {exc}"
            )
            traceback.print_exc()
        finally:
            if listener is not None:
                listener.stop()

        if stop_event.is_set():
            break

        print("Hotkey listener restarting...")
        time.sleep(LISTENER_RESTART_DELAY)


if __name__ == "__main__":
    main()
