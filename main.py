#!/usr/bin/env python
# SPDX-License-Identifier: GPL-3.0-or-later
#
# turing-smart-screen-python - a Python system monitor and library for USB-C displays like Turing Smart Screen or XuanFang
# https://github.com/mathoudebine/turing-smart-screen-python/
#
# Copyright (C) 2021 Matthieu Houdebine (mathoudebine)
# Copyright (C) 2022 Rollbacke
# Copyright (C) 2022 Ebag333
# Copyright (C) 2022 w1ld3r
# Copyright (C) 2022 Charles Ferguson (gerph)
# Copyright (C) 2022 Russ Nelson (RussNelson)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

# This file is the system monitor main program to display HW sensors on your screen using themes (see README)

from library.pythoncheck import check_python_version
check_python_version()

import os
import sys

try:
    import atexit
    import fcntl
    import locale
    import platform
    import signal
    import subprocess
    import time
    from pathlib import Path
    from PIL import Image

    if platform.system() == 'Windows':
        import win32api
        import win32con
        import win32gui

    from library.log import logger
    import library.scheduler as scheduler
    from library.display import display

except Exception as e:
    print("""Import error: %s
Please follow start guide to install required packages: https://github.com/mathoudebine/turing-smart-screen-python/wiki/System-monitor-:-how-to-start
Or the troubleshooting page: https://github.com/mathoudebine/turing-smart-screen-python/wiki/Troubleshooting#all-os-tkinter-dependency-not-installed""" % str(
        e))
    try:
        sys.exit(0)
    except:
        os._exit(0)

def _enable_system_gi():
    """Put the system PyGObject modules on the import path.

    Ayatana AppIndicator is packaged for the system Python, not this virtualenv.
    Without it pystray falls back to the X11 tray: Cinnamon shows that icon,
    but a click does not open a menu unless one item is marked default.
    """
    ver = "python%d.%d" % (sys.version_info.major, sys.version_info.minor)
    candidates = [
        "/usr/lib/python3/dist-packages",
        "/usr/lib64/python3/dist-packages",
    ]
    for root in ("/usr/lib", "/usr/lib64", "/usr/local/lib", "/usr/local/lib64"):
        candidates.append("%s/%s/site-packages" % (root, ver))
        candidates.append("%s/python3/site-packages" % root)
        candidates.append("%s/python3/dist-packages" % root)

    def add(path):
        if path and os.path.isdir(os.path.join(path, "gi")) and path not in sys.path:
            sys.path.append(path)
            return True
        return False

    for path in candidates:
        if add(path):
            return

    system_python = "/usr/bin/python3"
    if not os.path.isfile(system_python):
        return
    try:
        found = subprocess.check_output(
            [system_python, "-c",
             "import gi, os; print(os.path.dirname(os.path.dirname(gi.__file__)))"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=20,
        ).strip()
    except Exception:
        return
    add(found)


_enable_system_gi()

try:
    import pystray
except Exception:
    # If pystray cannot be loaded do not stop the program, just ignore it. The tray icon will not be displayed.
    pystray = None

MAIN_DIRECTORY = str(Path(__file__).parent.resolve()) + "/"


def _acquire_monitor_lock(replace: bool):
    """Hold an exclusive lock so only one monitor opens the USB display.

    A second start exits when one is already running. --replace asks the
    running copy to exit (the tray Configure → Save and run path) and waits
    until it has released the serial port.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    lock_path = os.path.join(runtime, "turing-smart-screen.lock")
    handle = open(lock_path, "a+")

    def try_lock():
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    if not try_lock():
        handle.seek(0)
        text = handle.read().strip()
        other_pid = int(text) if text.isdigit() else None
        if not replace:
            logger.info("Turing Smart Screen monitor is already running")
            try:
                sys.exit(0)
            except:
                os._exit(0)

        if other_pid and other_pid != os.getpid():
            logger.info("Replacing running monitor (pid %s)" % other_pid)
            try:
                os.kill(other_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        deadline = time.time() + 20
        while not try_lock():
            if time.time() > deadline:
                logger.error("The monitor that was already running did not exit")
                try:
                    sys.exit(1)
                except:
                    os._exit(1)
            time.sleep(0.2)

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


if __name__ == "__main__":

    # Apply system locale to this program
    locale.setlocale(locale.LC_ALL, '')

    logger.debug("Using Python %s" % sys.version)
    _monitor_lock = _acquire_monitor_lock(replace="--replace" in sys.argv)


    def wait_for_empty_queue(timeout: int = 5):
        # Waiting for all pending request to be sent to display
        logger.info("Waiting for all pending request to be sent to display (%ds max)..." % timeout)

        wait_time = 0
        while not scheduler.is_queue_empty() and wait_time < timeout:
            time.sleep(0.1)
            wait_time = wait_time + 0.1

        logger.debug("(Waited %.1fs)" % wait_time)

    def clean_stop(tray_icon=None):
        # Turn screen and LEDs off before stopping
        display.turn_off()

        # Do not stop the program now in case data transmission was in progress
        # Instead, ask the scheduler to empty the action queue before stopping
        scheduler.STOPPING = True

        # Waiting for all pending request to be sent to display
        wait_for_empty_queue(5)

        # Remove tray icon just before exit
        if tray_icon:
            tray_icon.visible = False

        # We force the exit to avoid waiting for other scheduled tasks: they may have a long delay!
        try:
            sys.exit(0)
        except:
            os._exit(0)


    def on_signal_caught(signum, frame=None):
        logger.info("Caught signal %d, exiting" % signum)
        clean_stop()


    def on_configure_tray(tray_icon, item):
        logger.info("Configure from tray icon")
        # There is no `python` on PATH (only python3). The venv interpreter
        # has to be named, or the settings window never starts. Leave the
        # monitor running; Save and run replaces it.
        subprocess.Popen(
            [sys.executable, os.path.join(MAIN_DIRECTORY, "configure.py")],
            cwd=MAIN_DIRECTORY,
            start_new_session=True,
        )


    def on_exit_tray(tray_icon, item):
        logger.info("Exit from tray icon")
        clean_stop(tray_icon)


    def on_clean_exit(*args):
        logger.info("Program will now exit")
        clean_stop()


    if platform.system() == "Windows":
        def on_win32_ctrl_event(event):
            """Handle Windows console control events (like Ctrl-C)."""
            if event in (win32con.CTRL_C_EVENT, win32con.CTRL_BREAK_EVENT, win32con.CTRL_CLOSE_EVENT):
                logger.debug("Caught Windows control event %s, exiting" % event)
                clean_stop()
            return 0


        def on_win32_wm_event(hWnd, msg, wParam, lParam):
            """Handle Windows window message events (like ENDSESSION, CLOSE, DESTROY)."""
            logger.debug("Caught Windows window message event %s" % msg)
            if msg == win32con.WM_POWERBROADCAST:
                # WM_POWERBROADCAST is used to detect computer going to/resuming from sleep
                if wParam == win32con.PBT_APMSUSPEND:
                    logger.info("Computer is going to sleep, display will turn off")
                    display.turn_off()
                elif wParam == win32con.PBT_APMRESUMEAUTOMATIC:
                    logger.info("Computer is resuming from sleep, display will turn on")
                    display.turn_on()
                    # Some models have troubles displaying back the previous bitmap after being turned off/on
                    display.display_static_images()
                    display.display_static_text()
            else:
                # For any other events, the program will stop
                logger.info("Program will now exit")
                clean_stop()

    # Create a tray icon for the program, with an Exit entry in menu.
    # Configure is the default item so the X11 tray, which has no menu,
    # still opens settings on a left click.
    tray_icon = None
    tray_uses_glib = False
    try:
        if pystray is None:
            raise RuntimeError("pystray is not installed")
        tray_icon = pystray.Icon(
            name='Turing System Monitor',
            title='Turing System Monitor',
            icon=Image.open(MAIN_DIRECTORY + "res/icons/monitor-icon-17865/64.png"),
            menu=pystray.Menu(
                pystray.MenuItem(
                    text='Configure',
                    action=on_configure_tray,
                    default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    text='Exit',
                    action=on_exit_tray)
            )
        )
        tray_uses_glib = type(tray_icon).__module__.rsplit(".", 1)[-1] in (
            "_appindicator", "_gtk")

        # AppIndicator only delivers clicks through a GLib main loop. That
        # loop has to run on this thread, so it is started after the display
        # is up. The X11 backend runs its own loop and can be shown now.
        if platform.system() != "Darwin" and not tray_uses_glib:
            tray_icon.run_detached()
            logger.info("Tray icon has been displayed (%s)" % type(tray_icon).__module__)
    except Exception as e:
        tray_icon = None
        tray_uses_glib = False
        logger.warning("Tray icon is not supported on your platform: %s" % e)

    # Set the different stopping event handlers, to send a complete frame to the LCD before exit
    atexit.register(on_clean_exit)
    signal.signal(signal.SIGINT, on_signal_caught)
    signal.signal(signal.SIGTERM, on_signal_caught)
    is_posix = os.name == 'posix'
    if is_posix:
        signal.signal(signal.SIGQUIT, on_signal_caught)
    if platform.system() == "Windows":
        win32api.SetConsoleCtrlHandler(on_win32_ctrl_event, True)

    # Initialize the display
    logger.info("Initialize display")
    display.initialize_display()

    # Start serial queue handler
    scheduler.QueueHandler()

    # Create all static images
    display.display_static_images()

    # Create all static texts
    display.display_static_text()

    # Wait for static images/text to be displayed before starting monitoring (to avoid filling the queue while waiting)
    wait_for_empty_queue(10)

    # Start sensor scheduled reading. Avoid starting them all at the same time to optimize load
    logger.info("Starting system monitoring")
    import library.stats as stats

    scheduler.CPUPercentage(); time.sleep(0.25)
    scheduler.CPUFrequency(); time.sleep(0.25)
    scheduler.CPULoad(); time.sleep(0.25)
    scheduler.CPUTemperature(); time.sleep(0.25)
    scheduler.CPUFanSpeed(); time.sleep(0.25)
    if stats.Gpu.is_available():
        scheduler.GpuStats(); time.sleep(0.25)
    scheduler.MemoryStats(); time.sleep(0.25)
    scheduler.DiskStats(); time.sleep(0.25)
    scheduler.NetStats(); time.sleep(0.25)
    scheduler.DateStats(); time.sleep(0.25)
    scheduler.SystemUptimeStats(); time.sleep(0.25)
    scheduler.CustomStats(); time.sleep(0.25)
    scheduler.WeatherStats(); time.sleep(0.25)
    scheduler.PingStats(); time.sleep(0.25)

    # OS-specific tasks
    if tray_icon and platform.system() == "Darwin":  # macOS-specific
        from AppKit import NSBundle, NSApp, NSApplicationActivationPolicyProhibited

        # Hide Python Launcher icon from macOS dock
        info = NSBundle.mainBundle().infoDictionary()
        info["LSUIElement"] = "1"
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyProhibited)

        # For macOS: display the tray icon now with blocking function
        tray_icon.run()

    elif tray_icon and tray_uses_glib:
        from gi.repository import GLib

        # While this thread is inside the GLib loop, Python's signal handlers
        # do not run. --replace stops the monitor with SIGTERM, so the loop
        # has to notice that signal itself.
        def _stop_from_glib(signum):
            def _handler():
                on_signal_caught(signum)
                return GLib.SOURCE_REMOVE
            return _handler

        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, _stop_from_glib(signal.SIGTERM))
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, _stop_from_glib(signal.SIGINT))

        def _show_tray(icon):
            icon.visible = True
            logger.info("Tray icon has been displayed (%s)" % type(icon).__module__)

        tray_icon.run(setup=_show_tray)

    elif platform.system() == "Windows":  # Windows-specific
        # Create a hidden window just to be able to receive window message events (for shutdown/logoff clean stop)
        hinst = win32api.GetModuleHandle(None)
        wndclass = win32gui.WNDCLASS()
        wndclass.hInstance = hinst
        wndclass.lpszClassName = "turingEventWndClass"
        messageMap = {win32con.WM_QUERYENDSESSION: on_win32_wm_event,
                      win32con.WM_ENDSESSION: on_win32_wm_event,
                      win32con.WM_QUIT: on_win32_wm_event,
                      win32con.WM_DESTROY: on_win32_wm_event,
                      win32con.WM_CLOSE: on_win32_wm_event,
                      win32con.WM_POWERBROADCAST: on_win32_wm_event}

        wndclass.lpfnWndProc = messageMap

        try:
            myWindowClass = win32gui.RegisterClass(wndclass)
            hwnd = win32gui.CreateWindowEx(win32con.WS_EX_LEFT,
                                           myWindowClass,
                                           "turingEventWnd",
                                           0,
                                           0,
                                           0,
                                           win32con.CW_USEDEFAULT,
                                           win32con.CW_USEDEFAULT,
                                           0,
                                           0,
                                           hinst,
                                           None)
            while True:
                # Receive and dispatch window messages
                win32gui.PumpWaitingMessages()
                time.sleep(0.5)

        except Exception as e:
            logger.error("Exception while creating event window: %s" % str(e))
