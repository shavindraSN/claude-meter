"""HTTP upload to a GeeKmagic SmallTV clock.

The stock firmware accepts POST /upload with multipart field "imageFile".
The filename picks which slot to overwrite:
  - "gif.jpg"              -> main-screen Customization GIF slot. The body
                              must be the firmware's custom animated-GIF
                              container: [frame0 JPEG][2400-byte index
                              block][frame1]...[frameN-1]. Index layout
                              per 12-byte record: <u16 0x01ff> <u16 id>
                              <u32 offset> <u32 size>. Record 0's `id`
                              holds the total frame count; records 1..N-1
                              hold absolute offsets. Frame count must be
                              >= a device-specific minimum (33 works).
  - "file1.jpg".."file5.jpg" -> Photo-mode full-screen slots (plain JPEG).
Max 1 MB per the device's JS check.
"""
from __future__ import annotations

import struct

import requests

GIF_INDEX_SIZE  = 2400
GIF_FRAME_COUNT = 33


class GeekmagicTransport:
    def __init__(self, host: str, mode: str):
        """
        host: "192.168.1.125" or "http://192.168.1.125"
        mode: "gif80" -> writes gif.jpg with container wrap;
              "photo240" -> writes file1.jpg as-is
        """
        if not host.startswith("http"):
            host = f"http://{host}"
        self._url  = f"{host.rstrip('/')}/upload"
        self._mode = mode

    def push(self, payload: bytes) -> int:
        if self._mode == "gif80":
            body = _build_gif_container(payload)
            filename = "gif.jpg"
        elif self._mode == "photo240":
            body = payload
            filename = "file1.jpg"
        else:
            raise ValueError(f"unsupported mode for geekmagic: {self._mode!r}")

        resp = requests.post(
            self._url,
            files={"imageFile": (filename, body, "image/jpeg")},
            timeout=5,
        )
        resp.raise_for_status()
        return len(body)


def _build_gif_container(frame: bytes, count: int = GIF_FRAME_COUNT) -> bytes:
    """
    Wrap N copies of a single JPEG frame in the firmware's container
    format: frame0 | 2400-byte index | frame1 | ... | frameN-1.
    """
    f_size = len(frame)
    idx    = bytearray(GIF_INDEX_SIZE)
    # Record 0: id = total frame count; v1 = 0 (frame0 offset); v2 = frame0 size.
    struct.pack_into("<HHII", idx, 0, 0x01ff, count, 0, f_size)
    # Records 1..count-1: id = frame index; v1 = absolute offset; v2 = size.
    for k in range(1, count):
        offset = f_size + GIF_INDEX_SIZE + (k - 1) * f_size
        struct.pack_into("<HHII", idx, k * 12, 0x01ff, k, offset, f_size)
    return frame + bytes(idx) + frame * (count - 1)
