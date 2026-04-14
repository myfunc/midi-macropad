"""Lock-free ring buffer for audio frames.

Producer-consumer ring buffer backed by a numpy float32 array of shape
``(capacity, channels)``. One producer writes, one consumer reads. Indices
are plain Python ints — under CPython the GIL gives atomicity for single
attribute read/write, which is sufficient for our single-producer / single-
consumer usage pattern (no need for explicit memory barriers).

Used by :mod:`audio_engine` to decouple the real-time audio callback
(consumer) from the mixing producer thread.
"""

from __future__ import annotations

import numpy as np


class RingBuffer:
    """Single-producer / single-consumer ring buffer of audio frames.

    Capacity is in *frames* (rows). Each frame has ``channels`` float32
    samples. The buffer stores at most ``capacity - 1`` frames so that the
    full/empty conditions stay distinguishable via head/tail indices.
    """

    def __init__(self, capacity_frames: int, channels: int = 2):
        if capacity_frames < 2:
            raise ValueError("capacity_frames must be >= 2")
        if channels < 1:
            raise ValueError("channels must be >= 1")
        self._capacity = int(capacity_frames)
        self._channels = int(channels)
        self._data = np.zeros((self._capacity, self._channels), dtype=np.float32)
        # Producer updates _write; consumer updates _read. Reads/writes of
        # a single int attribute are atomic under the GIL.
        self._write: int = 0
        self._read: int = 0

    @property
    def capacity(self) -> int:
        """Usable capacity (one slot reserved to distinguish full/empty)."""
        return self._capacity - 1

    @property
    def channels(self) -> int:
        return self._channels

    def available_read(self) -> int:
        """Number of frames currently available to read."""
        w = self._write
        r = self._read
        return (w - r) % self._capacity

    def available_write(self) -> int:
        """Number of frames that can be written without overwriting unread data."""
        return self.capacity - self.available_read()

    def write(self, data: np.ndarray) -> int:
        """Write as many frames as will fit.

        *data* must be shape ``(n, channels)`` or a 1-D array of length
        ``n * channels``. Returns the number of frames actually written.
        """
        if data.ndim == 1:
            if data.size % self._channels != 0:
                raise ValueError("1-D input length must be a multiple of channels")
            n = data.size // self._channels
            src = data.reshape(n, self._channels)
        else:
            if data.shape[1] != self._channels:
                raise ValueError(
                    f"channel mismatch: buffer has {self._channels}, input has {data.shape[1]}"
                )
            src = data
            n = src.shape[0]

        if n == 0:
            return 0

        space = self.available_write()
        n = min(n, space)
        if n == 0:
            return 0

        w = self._write
        end = w + n
        if end <= self._capacity:
            self._data[w:end] = src[:n]
        else:
            first = self._capacity - w
            self._data[w:self._capacity] = src[:first]
            self._data[0:end - self._capacity] = src[first:n]

        self._write = end % self._capacity
        return n

    def read_into(self, out: np.ndarray) -> int:
        """Read frames into *out* ``(frames, channels)`` or flat array.

        Returns number of frames read. If fewer frames are available than
        requested, the remainder of *out* is filled with zeros.
        """
        if out.ndim == 1:
            if out.size % self._channels != 0:
                raise ValueError("1-D output length must be a multiple of channels")
            frames = out.size // self._channels
            dst = out.reshape(frames, self._channels)
        else:
            if out.shape[1] != self._channels:
                raise ValueError(
                    f"channel mismatch: buffer has {self._channels}, output has {out.shape[1]}"
                )
            dst = out
            frames = dst.shape[0]

        if frames == 0:
            return 0

        available = self.available_read()
        n = min(frames, available)

        if n > 0:
            r = self._read
            end = r + n
            if end <= self._capacity:
                dst[:n] = self._data[r:end]
            else:
                first = self._capacity - r
                dst[:first] = self._data[r:self._capacity]
                dst[first:n] = self._data[0:end - self._capacity]
            self._read = end % self._capacity

        if n < frames:
            dst[n:] = 0.0
        return n

    def reset(self) -> None:
        """Drop all pending frames. Not thread-safe; call while both ends are paused."""
        self._read = 0
        self._write = 0
