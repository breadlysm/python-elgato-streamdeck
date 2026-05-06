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

    # Screen dimensions confirmed from JPEG SOF0 marker in HID capture.
    SCREEN_PIXEL_WIDTH = 352
    SCREEN_PIXEL_HEIGHT = 368
    SCREEN_IMAGE_FORMAT = "JPEG"
    SCREEN_FLIP = (False, False)
    SCREEN_ROTATION = 0

    _IMG_PACKET_LEN = 1024
    _KEY_PACKET_HEADER = 8
    _KEY_PACKET_PAYLOAD_LEN = _IMG_PACKET_LEN - _KEY_PACKET_HEADER
    _SCREEN_PACKET_HEADER = 16
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
        payload = bytearray(self._IMG_PACKET_LEN)
        payload[0] = 0x02
        self.device.write(payload)

    def reset(self):
        payload = bytearray(32)
        payload[0:2] = [0x03, 0x02]
        self.device.write_feature(payload)

    def _read_control_states(self):
        # Packet layout (512 bytes): [report_id, event_type, pad, pad, data...]
        # Key event (event_type=0x00): key states at bytes 3..14 (12 keys).
        # Dial event (event_type=0x03): sub_type at byte 3 (0x01=turn, 0x00=push),
        #   left dial at byte 4, right dial at byte 5.
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
        request = bytearray(32)
        request[0:2] = [0x03, 0x27]
        self.device.write_feature(request)
        serial = self.device.read_feature(0x03, 32)
        return self._extract_string(serial[2:])

    def get_firmware_version(self):
        # Write command 0x05 via SET_REPORT, then read response via GET_REPORT.
        request = bytearray(32)
        request[0:6] = [0x03, 0x05, 0x00, 0x00, 0x00, 0x02]
        self.device.write_feature(request)
        version = self.device.read_feature(0x03, 32)
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
        # Header layout (16 bytes), confirmed from HID capture:
        #   [0x02, 0x0c, 0x0e, 0x00, total_pages_lo, total_pages_hi,
        #    width_lo, width_hi, height_lo, height_hi,
        #    0x00, is_last, page_lo, page_hi, len_lo, len_hi]
        if not image:
            image = bytes(
                PILHelper.to_native_format(
                    self,
                    PILHelper.create_image(self, "black"),
                    self.SCREEN_IMAGE_FORMAT,
                )
            )

        image = bytes(image)

        total_pages = -(-len(image) // self._SCREEN_PACKET_PAYLOAD_LEN)  # ceil div

        page_number = 0
        bytes_remaining = len(image)
        while bytes_remaining > 0:
            this_length = min(bytes_remaining, self._SCREEN_PACKET_PAYLOAD_LEN)
            bytes_sent = page_number * self._SCREEN_PACKET_PAYLOAD_LEN

            header = [
                0x02,
                0x0c,
                0x0e,
                0x00,
                total_pages & 0xff,
                (total_pages >> 8) & 0xff,
                self.SCREEN_PIXEL_WIDTH & 0xff,
                (self.SCREEN_PIXEL_WIDTH >> 8) & 0xff,
                self.SCREEN_PIXEL_HEIGHT & 0xff,
                (self.SCREEN_PIXEL_HEIGHT >> 8) & 0xff,
                0x00,
                0x01 if this_length == bytes_remaining else 0x00,
                page_number & 0xff,
                (page_number >> 8) & 0xff,
                this_length & 0xff,
                (this_length >> 8) & 0xff,
            ]

            payload = bytes(header) + image[bytes_sent:bytes_sent + this_length]
            padding = bytearray(self._IMG_PACKET_LEN - len(payload))
            self.device.write(payload + padding)

            bytes_remaining -= this_length
            page_number += 1

    def set_touchscreen_image(self, image, x_pos=0, y_pos=0, width=0, height=0):
        pass

    def set_key_color(self, key, r, g, b):
        # Confirmed from HID capture: [0x03, 0x24, key, 0x00, 0x00, R, G, B, 0x0f, ...]
        # 0x0f byte appears to be a mode/persistence flag.
        if min(max(key, 0), self.KEY_COUNT - 1) != key:
            raise IndexError("Invalid key index {}.".format(key))

        payload = bytearray(32)
        payload[0:9] = [0x03, 0x24, key & 0xff, 0x00, 0x00,
                        r & 0xff, g & 0xff, b & 0xff, 0x0f]
        self.device.write_feature(payload)
