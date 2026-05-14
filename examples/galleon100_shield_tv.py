#!/usr/bin/env python3
"""
ShieldTV remote profile for the Corsair Galleon 100 SD (Corsair K100 MAX RGB MK2).

Mirrors the Stream Deck "Shield TV" manifest layout: 12 keys (3 cols × 4 rows)
send Home Assistant remote.send_command calls to control an NVIDIA Shield TV.

Image directory
---------------
Place your 160×160 PNG icons in the directory pointed to by ICONS_DIR (see
below), named exactly as listed in KEY_LAYOUT.  A fallback solid-colour tile
is rendered when an image file is missing, so you can test without all icons.

Home Assistant
--------------
Set HA_URL and HA_TOKEN in the configuration block below, or export them as
environment variables HA_URL / HA_TOKEN.  Key presses are printed to stdout
even when HA integration is disabled (HA_ENABLED = False).

Physical key layout (row × col):
  ┌───────┬───────┬───────┐
  │ Power │  Up   │ Menu  │   row 0
  ├───────┼───────┼───────┤
  │ Left  │Center │ Right │   row 1
  ├───────┼───────┼───────┤
  │ Back  │ Down  │ Home  │   row 2
  ├───────┼───────┼───────┤
  │  Pg+  │  Fld  │  Up   │   row 3
  └───────┴───────┴───────┘
"""

import os
import sys
import time
import threading

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Pillow is required: pip install Pillow")

try:
    import requests
except ImportError:
    requests = None

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Directory containing the PNG icon files (update for your system).
ICONS_DIR = os.path.expanduser(os.environ.get(
    "SHIELD_ICONS_DIR",
    r"C:\Users\jbrad\Downloads\Icons\streamdeck_shield_icons_12_separate_pngs",
))

# Home Assistant connection.
HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")   # long-lived access token
HA_ENABLED = bool(HA_TOKEN) and requests is not None

# Shield TV remote entity.
REMOTE_ENTITY = "remote.basementshield"

# ---------------------------------------------------------------------------
# Key layout
# Keys are indexed row-major: key = row * 3 + col
#
# Each entry: (label, icon_filename, ha_command_or_None)
#   ha_command=None  → local action (pagination / folder), just printed
# ---------------------------------------------------------------------------

KEY_LAYOUT = [
    # row 0
    ("Power",  "power.png",   "POWER"),        # key 0  (col 0, row 0)
    ("Up",     "up.png",      "DPAD_UP"),       # key 1  (col 1, row 0)
    ("Menu",   "menu.png",    "MENU"),          # key 2  (col 2, row 0)
    # row 1
    ("Left",   "left.png",    "DPAD_LEFT"),     # key 3  (col 0, row 1)
    ("Select", "center.png",  "DPAD_CENTER"),   # key 4  (col 1, row 1)
    ("Right",  "right.png",   "DPAD_RIGHT"),    # key 5  (col 2, row 1)
    # row 2
    ("Back",   "back.png",    "BACK"),          # key 6  (col 0, row 2)
    ("Down",   "down.png",    "DPAD_DOWN"),     # key 7  (col 1, row 2)
    ("Home",   "home.png",    "HOME"),          # key 8  (col 2, row 2)
    # row 3
    ("Next Pg","next_page.png", None),          # key 9  — pagination (no HA cmd)
    ("Folder", "folder.png",    None),          # key 10 — open subfolder
    ("Up",     "up.png",      "DPAD_UP"),       # key 11 (col 2, row 3)
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _send_ha_command(command: str):
    """POST remote.send_command to Home Assistant."""
    if not HA_ENABLED:
        print(f"[HA] Would send: remote.send_command → {REMOTE_ENTITY} command={command}")
        return
    try:
        resp = requests.post(
            f"{HA_URL}/api/services/remote/send_command",
            headers={
                "Authorization": f"Bearer {HA_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"entity_id": REMOTE_ENTITY, "command": command},
            timeout=5,
        )
        resp.raise_for_status()
        print(f"[HA] Sent {command} → {resp.status_code}")
    except Exception as exc:
        print(f"[HA] Error sending {command}: {exc}")


def _render_key_image(deck, label: str, icon_path: str | None, bg_color="black"):
    """
    Build a 160×160 JPEG-ready image: icon centred, label at the bottom.
    Falls back to a solid colour tile if the icon file is missing.
    """
    from StreamDeck.ImageHelpers import PILHelper

    if icon_path and os.path.isfile(icon_path):
        icon = Image.open(icon_path).convert("RGBA")
        image = PILHelper.create_scaled_key_image(deck, icon, margins=[0, 0, 20, 0])
    else:
        image = PILHelper.create_key_image(deck, background=bg_color)

    draw = ImageDraw.Draw(image)

    # Try to use a system font; fall back to PIL default.
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 14)
    except OSError:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        except OSError:
            font = ImageFont.load_default()

    # Centre the label horizontally, position near the bottom.
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    x = (image.width - text_w) // 2
    y = image.height - 18
    draw.text((x, y), label, font=font, fill="white")

    return PILHelper.to_native_key_format(deck, image)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        from StreamDeck.DeviceManager import DeviceManager
    except ImportError:
        sys.exit(
            "StreamDeck library not found.\n"
            "Install with:  pip install python-elgato-streamdeck\n"
            "or from the repo root:  pip install -e ."
        )

    manager = DeviceManager()
    decks = manager.enumerate()

    galleon = next(
        (d for d in decks if d.DECK_TYPE == "Corsair Galleon 100 SD"), None
    )
    if galleon is None:
        sys.exit("No Corsair Galleon 100 SD found.  Is it plugged in?")

    galleon.open()
    galleon.reset()
    galleon.set_brightness(70)

    print(f"Connected: {galleon.DECK_TYPE}  —  {galleon.KEY_COUNT} keys")
    print(f"Icons dir: {ICONS_DIR}")
    if not os.path.isdir(ICONS_DIR):
        print(f"  WARNING: icons directory does not exist — falling back to text-only tiles.")
    if not HA_ENABLED:
        print("Home Assistant integration disabled (no HA_TOKEN).  Commands will be printed only.")
    if requests is None:
        print("  (install 'requests' to enable HA integration)")

    # Pre-render all key images.
    key_images: list[bytes] = []
    for label, icon_file, _ in KEY_LAYOUT:
        icon_path = os.path.join(ICONS_DIR, icon_file) if icon_file else None
        if icon_path and not os.path.isfile(icon_path):
            print(f"  missing icon: {icon_path}")
            icon_path = None
        img = _render_key_image(galleon, label, icon_path)
        key_images.append(img)

    # Push images to the deck.
    for key_index, img in enumerate(key_images):
        galleon.set_key_image(key_index, img)

    # Key press callback.
    def on_key_change(deck, key, state):
        if not state:   # only act on press, not release
            return
        label, _, ha_command = KEY_LAYOUT[key]
        print(f"Key {key} pressed: {label}")
        if ha_command:
            threading.Thread(
                target=_send_ha_command, args=(ha_command,), daemon=True
            ).start()
        else:
            print(f"  [local] {label} — no HA action configured")

    galleon.set_key_callback(on_key_change)

    print("Running.  Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        galleon.set_key_callback(None)
        galleon.close()
        print("Closed.")


if __name__ == "__main__":
    main()
