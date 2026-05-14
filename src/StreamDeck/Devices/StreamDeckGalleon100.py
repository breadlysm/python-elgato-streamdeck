#         Python Stream Deck Library
#      Released under the MIT license
#
#   dean [at] fourwalledcubicle [dot] com
#         www.fourwalledcubicle.com
#

# Support for the Corsair Galleon 100 SD (built-in Stream Deck panel on the
# Corsair K100 MAX RGB MK2 keyboard).
#
# USB VID: 0x1b1c (Corsair), PID: 0x2b18
# Hardware: 12 keys (4 rows x 3 cols), 2 rotary dials, 720×384 px display
#
# Display protocol (confirmed from Stream Deck app HID capture):
#   The display is treated as a single 720 × 384 px JPEG written via command
#   0x0b with a simple 8-byte header.  The app renders all four widget panels
#   into one big image and pushes it as a unit.
#
#   Header layout (8 bytes):
#     [02, 0b, 00, is_last, len_lo, len_hi, page_lo, page_hi, JPEG_data...]
#
#   Image completion is signaled both by the is_last flag (byte 3 = 0x01 on
#   the final page) and by the JPEG FFD9 end-of-image marker.
#
# Historical note — command 0x0c (per-panel writes):
#   Earlier captures from iCUE/Corsair Webhub showed a different protocol
#   using command 0x0c with a 16-byte header to write individual 320×160 or
#   352×368 panels at specific (x, y) coordinates.  Those writes are how the
#   device renders its built-in default widgets (volume, profile, etc.) but
#   do NOT take effect when a host application is in control.  Use 0x0b for
#   host-controlled rendering.

import threading
import time

from .StreamDeck import ControlType, DialEventType, StreamDeck
from ..ImageHelpers import PILHelper


def _dial_rotation_transform(value):
    if value < 0x80:
        return value
    return -(0x100 - value)


class StreamDeckGalleon100(StreamDeck):
    """
    Represents a Corsair Galleon 100 SD — the built-in Stream Deck panel
    on the Corsair K100 MAX RGB MK2 keyboard.
    """

    KEY_COUNT = 12
    KEY_COLS = 3
    KEY_ROWS = 4

    DIAL_COUNT = 2

    KEY_PIXEL_WIDTH = 160
    KEY_PIXEL_HEIGHT = 160
    KEY_IMAGE_FORMAT = "JPEG"
    KEY_FLIP = (False, False)
    KEY_ROTATION = 0

    DECK_TYPE = "Corsair Galleon 100 SD"
    DECK_VISUAL = True

    # This device exposes multiple HID interfaces (MI_00 = Stream Deck panel,
    # MI_01 = keyboard).  Only MI_00 supports feature reports; select it explicitly.
    USB_INTERFACE_NUMBER = 0

    # Full display dimensions (confirmed from JPEG SOF0 in Stream Deck app capture).
    SCREEN_PIXEL_WIDTH = 720
    SCREEN_PIXEL_HEIGHT = 384
    SCREEN_IMAGE_FORMAT = "JPEG"
    SCREEN_FLIP = (False, False)
    SCREEN_ROTATION = 0

    # Approximate pixel regions of the four widget panels within the 720×384
    # canvas, as rendered by the Stream Deck app.  Useful for positioning widget
    # content when composing a full-screen image.  (x, y, width, height)
    PANEL_TOP_LEFT     = (24,  24,  320, 160)
    PANEL_TOP_RIGHT    = (376, 24,  320, 160)
    PANEL_BOTTOM_LEFT  = (24,  200, 320, 160)
    PANEL_BOTTOM_RIGHT = (376, 200, 320, 160)

    _IMG_PACKET_LEN = 1024
    _KEY_PACKET_HEADER = 8
    _KEY_PACKET_PAYLOAD_LEN = _IMG_PACKET_LEN - _KEY_PACKET_HEADER
    _SCREEN_PACKET_HEADER = 8
    _SCREEN_PACKET_PAYLOAD_LEN = _IMG_PACKET_LEN - _SCREEN_PACKET_HEADER

    _DIAL_EVENT_TRANSFORM = {
        DialEventType.TURN: _dial_rotation_transform,
        DialEventType.PUSH: bool,
    }

    # Heartbeat interval (seconds).  The device firmware reverts to its built-in
    # keyboard pages if the host stops sending command 0x25 — observed timeout
    # is ~2-3 seconds, so we ping every 500ms to match the Stream Deck app.
    _HEARTBEAT_INTERVAL = 0.5

    def __init__(self, device):
        super().__init__(device)
        self.BLANK_KEY_IMAGE = PILHelper.to_native_key_format(
            self, PILHelper.create_key_image(self, "black")
        )
        self._heartbeat_thread = None
        self._heartbeat_stop = threading.Event()

    def _reset_key_stream(self):
        # Not confirmed from capture; this device uses JPEG FFD9 end-of-image
        # detection rather than an explicit stream reset, so this is a no-op.
        pass

    def _heartbeat_loop(self):
        payload = bytearray(self._IMG_PACKET_LEN)
        payload[0:2] = [0x02, 0x25]
        while not self._heartbeat_stop.is_set():
            try:
                self.device.write(payload)
            except Exception:
                break
            self._heartbeat_stop.wait(self._HEARTBEAT_INTERVAL)

    def open(self):
        super().open()
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True
        )
        self._heartbeat_thread.start()

    def close(self):
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=1)
            self._heartbeat_thread = None
        super().close()

    def reset(self):
        payload = bytearray(32)
        payload[0:2] = [0x03, 0x02]
        self.device.write_feature(payload)

    def _read_control_states(self):
        # Two distinct event formats are observed on MI_00:
        #
        # Format A — custom/blank page (event_type=0x00):
        #   [report_id, 0x00, 0x0c, 0x00, key0, key1, ..., key11, ...]
        #   Key states are bytes 3..14 (12 keys, 1=pressed, 0=released).
        #   This is the format handled below and matches the standard Stream Deck
        #   protocol.
        #
        # Format B — default Corsair keyboard page (event_type=0x20):
        #   [report_id, 0x20, 0x0c, 0x00, 0x02, col, brightness, toggleA, ...]
        #   This is a device-state broadcast (brightness %, profile color, toggle
        #   states) sent after built-in keyboard actions fire.  These keys are
        #   handled entirely by the keyboard firmware; the library ignores them.
        #
        # Dial events (event_type=0x03) have not yet been confirmed from a capture
        # on a blank page — the implementation below mirrors the Stream Deck Plus
        # protocol and should be verified.
        states = self.device.read(512)

        if states is None:
            return None

        states = states[1:]  # strip report ID

        event_type = states[0]

        if event_type == 0x00:
            key_states = [bool(s) for s in states[3:3 + self.KEY_COUNT]]
            return {ControlType.KEY: key_states}

        elif event_type == 0x03:
            sub_type = states[3]
            if sub_type == 0x01:
                dial_event = DialEventType.TURN
            elif sub_type == 0x00:
                dial_event = DialEventType.PUSH
            else:
                return None

            values = [
                self._DIAL_EVENT_TRANSFORM[dial_event](s)
                for s in states[4:4 + self.DIAL_COUNT]
            ]
            return {ControlType.DIAL: {dial_event: values}}

        return None

    def set_brightness(self, percent):
        if isinstance(percent, float):
            percent = int(100.0 * percent)

        percent = min(max(percent, 0), 100)

        payload = bytearray(32)
        payload[0:3] = [0x03, 0x08, percent]
        self.device.write_feature(payload)

    def get_serial_number(self):
        # Write command 0x27 via SET_REPORT, then read response via GET_REPORT.
        # The capture showed the response is 60 bytes long, not 32.
        request = bytearray(32)
        request[0:2] = [0x03, 0x27]
        self.device.write_feature(request)
        serial = self.device.read_feature(0x03, 60)
        return self._extract_string(serial[2:])

    def get_firmware_version(self):
        # Write command 0x05 via SET_REPORT, then read response via GET_REPORT.
        request = bytearray(32)
        request[0:6] = [0x03, 0x05, 0x00, 0x00, 0x00, 0x02]
        self.device.write_feature(request)
        version = self.device.read_feature(0x03, 60)
        return self._extract_string(version[6:])

    def set_key_image(self, key, image):
        if min(max(key, 0), self.KEY_COUNT - 1) != key:
            raise IndexError("Invalid key index {}.".format(key))

        image = bytes(image or self.BLANK_KEY_IMAGE)

        page_number = 0
        bytes_remaining = len(image)
        while bytes_remaining > 0:
            this_length = min(bytes_remaining, self._KEY_PACKET_PAYLOAD_LEN)
            bytes_sent = page_number * self._KEY_PACKET_PAYLOAD_LEN

            header = [
                0x02,
                0x07,
                key & 0xff,
                1 if this_length == bytes_remaining else 0,
                this_length & 0xff,
                (this_length >> 8) & 0xff,
                page_number & 0xff,
                (page_number >> 8) & 0xff,
            ]

            payload = bytes(header) + image[bytes_sent:bytes_sent + this_length]
            padding = bytearray(self._IMG_PACKET_LEN - len(payload))
            self.device.write(payload + padding)

            bytes_remaining -= this_length
            page_number += 1

    def set_screen_image(self, image):
        # Writes a full 720×384 JPEG to the display using command 0x0b.
        # Header layout (8 bytes), confirmed from Stream Deck app HID capture:
        #   [02, 0b, 00, is_last, len_lo, len_hi, page_lo, page_hi, JPEG_data...]
        if not image:
            image = bytes(
                PILHelper.to_native_format(
                    self,
                    PILHelper.create_image(self, "black"),
                    self.SCREEN_IMAGE_FORMAT,
                )
            )

        image = bytes(image)

        page_number = 0
        bytes_remaining = len(image)
        while bytes_remaining > 0:
            this_length = min(bytes_remaining, self._SCREEN_PACKET_PAYLOAD_LEN)
            bytes_sent = page_number * self._SCREEN_PACKET_PAYLOAD_LEN
            is_last = 1 if this_length == bytes_remaining else 0

            header = [
                0x02,
                0x0b,
                0x00,
                is_last,
                this_length & 0xff,
                (this_length >> 8) & 0xff,
                page_number & 0xff,
                (page_number >> 8) & 0xff,
            ]

            payload = bytes(header) + image[bytes_sent:bytes_sent + this_length]
            padding = bytearray(self._IMG_PACKET_LEN - len(payload))
            self.device.write(payload + padding)

            bytes_remaining -= this_length
            page_number += 1

    # Alias for compatibility with the StreamDeckPlus / touchscreen API.
    def set_touchscreen_image(self, image, x_pos=0, y_pos=0, width=0, height=0):
        self.set_screen_image(image)

    def set_key_color(self, key, r, g, b):
        # Confirmed from HID capture: [0x03, 0x24, key, 0x00, 0x00, R, G, B, 0x0f, ...]
        if min(max(key, 0), self.KEY_COUNT - 1) != key:
            raise IndexError("Invalid key index {}.".format(key))

        payload = bytearray(32)
        payload[0:9] = [0x03, 0x24, key & 0xff, 0x00, 0x00,
                        r & 0xff, g & 0xff, b & 0xff, 0x0f]
        self.device.write_feature(payload)
