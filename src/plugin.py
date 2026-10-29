from __future__ import annotations

import asyncio
import json
import logging
from typing import List

from astra_plugin_sdk import (
    DesktopWidget,
    Plugin,
    UiContribution,
    WidgetFormat,
    WidgetSurface,
)

from . import folder_picker, media_server, slideshow

log = logging.getLogger("photo-frame")

class PhotoFrame(Plugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._media = media_server.MediaServer()

    async def get_ui_contributions(self) -> List[UiContribution]:
        return [
            UiContribution.widget("photo-frame-widget", "Фоторамка", DesktopWidget(
                repeatable=True,
                formats=[WidgetFormat(
                    id="frame", label="Фоторамка", url="widget.html",
                    appearance="card",
                    default_w=6, default_h=4, min_w=2, min_h=2,
                    max_w=12, max_h=8,
                    min_pixel_width=180, min_pixel_height=120,
                )],
                surfaces=[WidgetSurface.modal(
                    "settings", "Настройки рамки", "settings.html",
                    width=640, height=600,
                    min_width=360, min_height=460,
                    max_width=760, max_height=760,
                )],
                settings_surface="settings",
            )),
        ]

    def _media_base(self, cfg: dict) -> str:
        base = self._media.start()
        if not base:
            return ""
        token = slideshow.media_token(cfg)
        self._media.remember(token, cfg["folders"])
        return f"{base}{token}/"

    async def handle_widget_ui_call(self, method: str, params_json: str, widget):
        config = {}
        if widget is not None:
            try:
                config = json.loads(widget.config_json or "{}")
            except ValueError:
                config = {}
        try:
            params = json.loads(params_json or "{}")
        except ValueError:
            params = {}
        if not isinstance(params, dict):
            params = {}
        cfg = slideshow.normalize_config(config)
        for key in ("folders", "recursive"):
            if key in params:
                cfg = slideshow.normalize_config({**cfg, key: params[key]})
        if method == "pf_state":
            force = bool(params.get("refresh"))
            base = self._media_base(cfg)
            return await asyncio.to_thread(slideshow.state_for, cfg, force, base)
        if method == "pf_photo":
            max_side = params.get("max_side") or 0
            stage = str(params.get("stage") or "full")
            return await asyncio.to_thread(
                slideshow.photo_payload, cfg, str(params.get("id") or ""),
                int(max_side), stage)
        if method == "pf_caption":
            photos, _ = await asyncio.to_thread(slideshow.scan_photos, cfg)
            wanted = str(params.get("id") or "")
            photo = next((p for p in photos if p["id"] == wanted), {})
            return {"text": slideshow.caption_text(cfg, photo)}
        if method == "pf_pick":
            return await asyncio.to_thread(folder_picker.pick_folder, params)
        if method == "pf_pick_result":
            return folder_picker.pick_result()
        return await self.handle_ui_call(method, params_json)

if __name__ == "__main__":
    PhotoFrame().run()
