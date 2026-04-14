"""Tests for the lock-free ring buffer."""

import numpy as np
import pytest

from plugins.piano.ring_buffer import RingBuffer


def test_empty_buffer_reads_zero():
    rb = RingBuffer(capacity_frames=16, channels=2)
    assert rb.available_read() == 0
    assert rb.available_write() == 15  # capacity - 1
    out = np.ones((8, 2), dtype=np.float32)
    n = rb.read_into(out)
    assert n == 0
    assert np.all(out == 0.0)  # remainder zero-filled


def test_write_and_read_roundtrip():
    rb = RingBuffer(capacity_frames=16, channels=2)
    data = np.arange(10 * 2, dtype=np.float32).reshape(10, 2)
    written = rb.write(data)
    assert written == 10
    assert rb.available_read() == 10

    out = np.zeros((10, 2), dtype=np.float32)
    read = rb.read_into(out)
    assert read == 10
    assert np.array_equal(out, data)
    assert rb.available_read() == 0


def test_write_exceeds_capacity_partial():
    rb = RingBuffer(capacity_frames=8, channels=2)  # usable = 7
    data = np.ones((10, 2), dtype=np.float32)
    written = rb.write(data)
    assert written == 7
    assert rb.available_read() == 7
    assert rb.available_write() == 0


def test_wrap_around():
    rb = RingBuffer(capacity_frames=8, channels=2)  # usable = 7
    # fill 6 frames
    first = np.full((6, 2), 1.0, dtype=np.float32)
    rb.write(first)
    # drain 4
    out = np.zeros((4, 2), dtype=np.float32)
    rb.read_into(out)
    assert np.all(out == 1.0)
    # now write 5 — should wrap internally
    second = np.full((5, 2), 2.0, dtype=np.float32)
    assert rb.write(second) == 5
    # read all remaining 7 (2 old ones + 5 new)
    out2 = np.zeros((7, 2), dtype=np.float32)
    assert rb.read_into(out2) == 7
    assert np.all(out2[:2] == 1.0)
    assert np.all(out2[2:] == 2.0)


def test_partial_read_fills_zero_tail():
    rb = RingBuffer(capacity_frames=16, channels=2)
    data = np.full((3, 2), 5.0, dtype=np.float32)
    rb.write(data)
    out = np.full((8, 2), -1.0, dtype=np.float32)
    n = rb.read_into(out)
    assert n == 3
    assert np.all(out[:3] == 5.0)
    assert np.all(out[3:] == 0.0)


def test_channel_mismatch_raises():
    rb = RingBuffer(capacity_frames=8, channels=2)
    with pytest.raises(ValueError):
        rb.write(np.zeros((4, 3), dtype=np.float32))
    with pytest.raises(ValueError):
        rb.read_into(np.zeros((4, 3), dtype=np.float32))


def test_reset_clears_pending():
    rb = RingBuffer(capacity_frames=8, channels=2)
    rb.write(np.ones((5, 2), dtype=np.float32))
    assert rb.available_read() == 5
    rb.reset()
    assert rb.available_read() == 0
    assert rb.available_write() == 7
