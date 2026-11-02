"""Stream 100 frames through each DUT with independent input/output stalls."""
import numpy as np
from .util import instance, stream, transfer, collect


def run_frames(inst, frames, fields, output_beats, *, seed, min_in_flight):
    """Send continuously across frame boundaries and verify actual overlap."""
    input_beats = len(frames[0])
    inputs = [beat for frame in frames for beat in frame]
    expected_count = len(frames) * output_beats
    peak_in_flight = 0
    # Unbiased, independent coins for launching an input beat and output ready.
    # Once launched, input valid/payload remain asserted until accepted.
    for sent, rows in transfer(inst, inputs, fields, seed=seed, stall_denominator=2):
        # A frame is in flight from its first accepted input beat through its
        # last output transfer, including partially supplied input frames.
        started = (sent + input_beats - 1) // input_beats
        finished = len(rows) // output_beats
        peak_in_flight = max(peak_in_flight, started - finished)
        assert len(rows) <= expected_count, 'Unexpected extra output'
        if sent == len(inputs) and len(rows) == expected_count:
            inst.ports.input.valid = False
            assert peak_in_flight >= min_in_flight, f'Only {peak_in_flight} frames overlapped'
            return [rows[i:i + output_beats] for i in range(0, expected_count, output_beats)]


def test_stresstest(rs):
    rng = np.random.default_rng(0x34CAF41C91E66374)
    width = 16
    messages = [rng.integers(0, rs.q, rs.k, dtype=rs.dtype) for _ in range(100)]
    counts = [int(rng.integers(0, rs.t + 1)) for _ in messages]

    # Stream all encoder messages without waiting for each frame's output.
    encoder = instance('encoder', unroll=width, buffered=True)
    encoded = run_frames(encoder, [stream(msg, width) for msg in messages],
                         ['data', 'last'], rs.n // width,
                         seed=0x5B13B1E7B2B0F84D, min_in_flight=2)
    corrupted = []
    for message, count, frame in zip(messages, counts, encoded):
        assert [row['last'] for row in frame] == [False] * (len(frame) - 1) + [True]
        word = np.array(collect(frame), dtype=rs.dtype)
        assert np.array_equal(word, rs.encode(message))
        damaged, _, _ = rs.add_random_errors(rng, word, num_errors=count)
        corrupted.append(damaged)

    # Decoder time advances independently. Its input is always available,
    # subject only to its own random stutter and the DUT's backpressure.
    decoder = instance('decoder', unroll=width, buffered=True)
    decoded = run_frames(decoder, [stream(word, width) for word in corrupted],
                         ['data', 'last', 'num_errors', 'failed'],
                         rs.k // width, seed=0xDE49A8B056CD32E1, min_in_flight=3)
    for message, count, frame in zip(messages, counts, decoded):
        assert collect(frame) == message.tolist()
        assert [row['last'] for row in frame] == [False] * (len(frame) - 1) + [True]
        assert all(row['num_errors'] == count and not row['failed'] for row in frame)
