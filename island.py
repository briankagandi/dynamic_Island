"""A small Windows 11 desktop island for the clock and system media session.

Media metadata is provided by Windows' Global System Media Transport Controls.
Install its Python binding with: py -m pip install winsdk
"""

from __future__ import annotations

import asyncio
import ctypes
import importlib
import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont
from typing import Any


POLL_INTERVAL = 2
WINDOW_WIDTH = 460
COMPACT_HEIGHT = 104


class MediaReader:
    """Poll Windows' current media session off the Tk event loop."""

    def __init__(self, updates: queue.Queue[dict[str, str]]) -> None:
        self.updates = updates

    async def poll(self) -> dict[str, str]:
        try:
            control = importlib.import_module("winsdk.windows.media.control")
        except ImportError:
            return {
                "error": "Media support not installed",
                "detail": "Run: py -m pip install winsdk",
            }

        manager = await control.GlobalSystemMediaTransportControlsSessionManager.request_async()
        session = manager.get_current_session()
        if session is None:
            sessions = manager.get_sessions()
            for candidate in sessions:
                playback = candidate.get_playback_info()
                if playback.playback_status == control.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING:
                    session = candidate
                    break

        if session is None:
            return {"title": "Nothing playing", "artist": "Start Spotify or another media player"}

        properties = await session.try_get_media_properties_async()
        playback = session.get_playback_info()
        status = (
            "Playing"
            if playback.playback_status == control.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
            else "Paused"
        )
        title = properties.title or "Unknown track"
        artist = properties.artist or properties.album_artist or "Unknown artist"
        return {"title": title, "artist": artist, "status": status}

    async def run(self) -> None:
        while True:
            try:
                self.updates.put(await self.poll())
            except Exception as error:
                self.updates.put({"error": "Could not read Windows media session", "detail": str(error)})
            await asyncio.sleep(POLL_INTERVAL)


class DynamicIsland:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Dynamic Island")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#050505")

        self.canvas = tk.Canvas(
            self.root,
            width=WINDOW_WIDTH,
            height=COMPACT_HEIGHT,
            bg="#050505",
            highlightthickness=0,
        )
        self.canvas.pack()

        self.title_font = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        self.body_font = tkfont.Font(family="Segoe UI", size=9)
        self.clock_font = tkfont.Font(family="Segoe UI", size=22, weight="bold")
        self.small_font = tkfont.Font(family="Segoe UI", size=8)
        self.state: dict[str, str] = {
            "title": "Connecting to media",
            "artist": "Checking Windows media sessions",
            "status": "Connecting",
        }
        self.updates: queue.Queue[dict[str, str]] = queue.Queue()
        self.reader = MediaReader(self.updates)
        self.control_actions: queue.Queue[str] = queue.Queue()

        self.canvas.bind("<Button-1>", self._on_click)
        self.root.bind("<Escape>", lambda _event: self.root.destroy())
        self._position_window()
        self.root.after(100, self._apply_region)
        self.root.after(200, self._check_updates)
        self.root.after(250, self._draw)
        threading.Thread(target=self._run_media_reader, daemon=True).start()
        threading.Thread(target=self._run_controls, daemon=True).start()

    def _position_window(self) -> None:
        screen_width = self.root.winfo_screenwidth()
        x = (screen_width - WINDOW_WIDTH) // 2
        self.root.geometry(f"{WINDOW_WIDTH}x{COMPACT_HEIGHT}+{x}+12")

    def _apply_region(self) -> None:
        if not self.root.winfo_exists():
            return
        hwnd = self.root.winfo_id()
        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        gdi32.CreateRoundRectRgn.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int
        ]
        gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
        user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
        user32.SetWindowRgn.restype = ctypes.c_int
        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        gdi32.DeleteObject.restype = ctypes.c_int
        region = gdi32.CreateRoundRectRgn(0, 0, WINDOW_WIDTH + 1, COMPACT_HEIGHT + 1, 44, 44)
        if region:
            if not user32.SetWindowRgn(hwnd, region, True):
                gdi32.DeleteObject(region)

    def _run_media_reader(self) -> None:
        asyncio.run(self.reader.run())

    def _run_controls(self) -> None:
        while True:
            action = self.control_actions.get()
            asyncio.run(self._send_control(action))

    async def _send_control(self, action: str) -> None:
        try:
            control = importlib.import_module("winsdk.windows.media.control")
            manager = await control.GlobalSystemMediaTransportControlsSessionManager.request_async()
            session = manager.get_current_session()
            if session is None:
                self.updates.put({"error": "No active media session to control", "detail": ""})
                return
            methods = {
                "previous": session.try_skip_previous_async,
                "toggle": session.try_play_pause_async,
                "next": session.try_skip_next_async,
            }
            await methods[action]()
        except Exception as error:
            self.updates.put({"error": "Media control failed", "detail": str(error)})

    def _check_updates(self) -> None:
        if not self.root.winfo_exists():
            return
        try:
            while True:
                self.state = self.updates.get_nowait()
        except queue.Empty:
            pass
        self.root.after(200, self._check_updates)

    def _rounded_rectangle(
        self, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs: Any
    ) -> None:
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        ]
        self.canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)

    def _draw(self) -> None:
        if not self.root.winfo_exists():
            return
        canvas = self.canvas
        canvas.delete("all")
        self._rounded_rectangle(2, 2, WINDOW_WIDTH - 2, COMPACT_HEIGHT - 2, 34, fill="#101113")
        self._rounded_rectangle(3, 3, WINDOW_WIDTH - 3, COMPACT_HEIGHT - 3, 33, fill="#101113")
        canvas.create_oval(17, 38, 29, 50, fill="#59e39a", outline="")
        canvas.create_text(43, 53, text=datetime.now().strftime("%H:%M"), anchor="w",
                          fill="#f6f6f7", font=self.clock_font)
        canvas.create_text(151, 27, text="NOW PLAYING", anchor="w",
                          fill="#96989e", font=self.small_font)

        if self.state.get("error"):
            title = self.state["error"]
            artist = self.state.get("detail", "")
        else:
            title = self.state.get("title", "Nothing playing")
            artist = self.state.get("artist", "")
        canvas.create_text(151, 49, text=title[:36], anchor="w",
                          fill="#f6f6f7", font=self.title_font)
        canvas.create_text(151, 70, text=artist[:44], anchor="w",
                          fill="#a8a9ae", font=self.body_font)
        status = self.state.get("status", "")
        if status == "Playing":
            for index, height in enumerate((6, 12, 8, 15, 7)):
                x = 412 + index * 7
                canvas.create_line(x, 51 - height // 2, x, 51 + height // 2,
                                   fill="#59e39a", width=3, capstyle=tk.ROUND)
        elif status == "Paused":
            canvas.create_text(428, 51, text="Ⅱ", fill="#a8a9ae", font=self.body_font)

        self.root.after(250, self._draw)

    def _on_click(self, event: tk.Event[Any]) -> None:
        if event.x >= 400:
            self.control_actions.put("toggle")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    DynamicIsland().run()