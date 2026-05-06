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
# Hardware: 12 keys (4 rows x 3 cols), 2 rotary dials, 1 small info screen
#
# Protocol details marked TODO below were derived by analogy with the
# Stream Deck Plus/Studio and must be verified via HID capture before this
# driver can be considered complete.  See CONTRIBUTING for capture instructions.

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

    # TODO: verify screen dimensions via HID capture.  Tom's Hardware reports the
    # total display panel as 720x180; the strip screen between the dials may be a
    # subset of that.  Placeholder mirrors the Neo's info strip until confirmed.
    SCREEN_PIXEL_WIDTH = 248
    SCREEN_PIXEL_HEIGHT = 58
    SCREEN_IMAGE_FORMAT = "JPEG"
    SCREEN_FLIP = (False, False)
    SCREEN_ROTATION = 0

    _IMG_PACKET_LEN = 1024
    _KEY_PACKET_HEADER = 8
    _KEY_PACKET_PAYLOAD_LEN = _IMG_PACKET_LEN - _KEY_PACKET_HEADER
    _SCREEN_PACKET_HEADER = 8
    _SCREEN_PACKET_PAYLOAD_LEN = _IMG_PACKET_LEN - _SCREEN_PACKET_HEADER

    _DIAL_EVENT_TRANSFORM = {
        DialEventType.TURN: _dial_rotation_transform,
        DialEventType.PUSH: bool,
    }

    def __init__(self, device):
        super().__init__(device)
        self.BLANK_KEY_IMAGE = PILHelper.to_native_key_format(
            self, PILHelper.create_key_image(self, "black")
        )

    def _reset_key_stream(self):
        # TODO: verify reset command byte — 0x02 matches Plus/Studio/Neo.
        payload = bytearray(self._IMG_PACKET_LEN)
        payload[0] = 0x02
        self.device.write(payload)

    def reset(self):
        # TODO: verify reset feature report bytes via HID capture.
        payload = bytearray(32)
        payload[0:2] = [0x03, 0x02]
        self.device.write_feature(payload)

    def _read_control_states(self):
        # Packet layout (512 bytes): [report_id, event_type, pad, pad, data...]
        # Key event (event_type=0x00): key states at bytes 4..15 (12 keys).
        #   The device reports 13 positions (bytes 4..16); byte 16 likely maps
        #   to the touch strip and is ignored here.
        # Dial event (event_type=0x03): sub_type at byte 4 (0x01=turn, 0x00=push),
        #   left dial at byte 5, right dial at byte 6.
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
        # TODO: verify report ID (0x06) and string offset via HID capture.
        serial = self.device.read_feature(0x06, 32)
        return self._extract_string(serial[2:])

    def get_firmware_version(self):
        # TODO: verify report ID (0x05) and string offset via HID capture.
        version = self.device.read_feature(0x05, 32)
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
        # TODO: verify screen write command byte (0x0b) and header layout via HID capture.
        # TODO: verify screen dimensions (SCREEN_PIXEL_WIDTH/HEIGHT) via HID capture.
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

            header = [
                0x02,
                0x0b,
                0x00,
                0x01 if this_length == bytes_remaining else 0x00,
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

    def set_touchscreen_image(self, image, x_pos=0, y_pos=0, width=0, height=0):
        pass

    def set_key_color(self, key, r, g, b):
        pass
