"""Deliberately starve a real playback stream and verify it resumes cleanly."""
import ctypes as C
import math
import threading
import time

from engine import FRAMES, Pulse, RATE


def check_refill(output):
    capture = Pulse(output + '.monitor', True)  # This test runs on an isolated null sink.
    playback = Pulse(output, False)
    done = threading.Event()
    failures = []
    readings = []

    def read():
        try:
            while not done.is_set():
                samples = (C.c_float * (FRAMES * 2))()
                capture.transfer(samples)
                readings.append((time.monotonic(), max(abs(x) for x in samples)))
        except Exception as exc:
            failures.append(exc)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    samples = (C.c_float * (FRAMES * 2))()
    for i in range(FRAMES):
        samples[2*i] = samples[2*i+1] = .2 * math.sin(2*math.pi*1000*i/RATE)
    try:
        for _ in range(60):
            playback.transfer(samples)
        # Longer than both the target buffer and the configured maximum queue.
        time.sleep(.35)
        resumed = time.monotonic()
        for _ in range(100):
            playback.transfer(samples)
        time.sleep(.15)
        after = [peak for timestamp, peak in readings if resumed+.2 < timestamp < resumed+.7]
        assert after and sum(p > .1 for p in after) / len(after) > .9, after
        assert not failures, failures
        print('PASS: playback automatically refills after a 350 ms stall without reopening the stream')
    finally:
        done.set()
        reader.join(timeout=1)
        if reader.is_alive():
            # Wake the monitor with another buffered block before freeing it.
            for _ in range(10):
                playback.transfer(samples)
            reader.join(timeout=1)
        playback.close()
        if not reader.is_alive():
            capture.close()
