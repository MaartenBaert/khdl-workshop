"""Frame-parallel Berlekamp and Forney: arithmetic, order, throughput, stalls, and reset."""
import numpy as np
import pytest

from .test_decoder_padding import dirty_stream, message_rows, vectors
from .util import collect, instance, run, stream, tick, transfer


@pytest.fixture(scope='module')
def frames():
    config = dict(n=13, k=7, m=4, prim=0x13, alpha=3, fcr=0)
    rs, frames = vectors(**config, width=4)
    # An incomplete final bank must drain without waiting for another frame.
    return config, rs, frames[:-1]


def decoder(config, width, buffered, workers, forney_workers=1,
            unroll_berlekamp=1, unroll_forney=4):
    return instance('decoder', **config, unroll=width, buffered=buffered,
                    unroll_berlekamp=unroll_berlekamp, unroll_omegas=3,
                    unroll_forney=unroll_forney, berlekamp_instances=workers,
                    forney_instances=forney_workers)


def exercise(inst, rs, frames, width, stutter):
    inputs = [beat for frame in frames for beat in dirty_stream(frame['damaged'], width, rs.q)]
    expected = [row for frame in frames for row in message_rows(frame, width)]
    first = None
    output_times = []
    previous = 0
    for cycle, (sent, rows) in enumerate(transfer(
            inst, inputs, list(expected[0]), stutter=stutter, stall_denominator=2)):
        assert len(rows) <= len(expected)
        if len(rows) != previous:
            first = cycle if first is None else first
            if rows[-1]['last']:
                output_times.append(cycle)
            previous = len(rows)
        if sent == len(inputs) and len(rows) == len(expected):
            break
    assert rows == expected
    inst.ports.input.valid = False
    inst.ports.output.ready = True
    for _ in range(16):
        tick(inst)
        assert not inst.ports.output.valid
    return first, output_times


@pytest.mark.parametrize('workers', [2, 3])
@pytest.mark.parametrize('width', [3, 4, 16])
@pytest.mark.parametrize('buffered', [False, True])
def test_parallel_decoder(frames, workers, width, buffered):
    config, rs, data = frames
    for stutter in (False, True):
        inst = decoder(config, width, buffered, workers)
        first, _ = exercise(inst, rs, data, width, stutter)
        if not stutter:
            cycles = (rs.n + width - 1) // width
            # Parallel routing adds no pipeline cycle to the first frame.
            assert first == cycles + 2 * rs.t + max(cycles, 1) + 2 + 1


def test_parallel_decoder_throughput(frames):
    config, rs, data = frames
    results = {}
    for workers in (1, 2, 3):
        first, times = exercise(decoder(config, 4, False, workers), rs, data, 4, False)
        results[workers] = first, times
    assert results[1][0] == results[2][0] == results[3][0]
    # With C=H=4, F=K=2 and B=6, two workers remove the BM bottleneck.
    assert np.diff(results[1][1]).tolist() == [6] * (len(data) - 1)
    for workers in (2, 3):
        assert np.diff(results[workers][1]).tolist() == [4] * (len(data) - 1)
        assert results[workers][1][-1] < results[1][1][-1]


@pytest.mark.parametrize('workers', [2, 3])
@pytest.mark.parametrize('buffered', [False, True])
@pytest.mark.parametrize('phase', ['busy', 'stalled'])
@pytest.mark.parametrize('forney_workers', [1, 3])
def test_parallel_decoder_reset(frames, workers, buffered, phase, forney_workers):
    config, rs, data = frames
    inst = decoder(config, 4, buffered, workers, forney_workers,
                   unroll_forney=1 if forney_workers > 1 else 4)
    inputs = [beat for frame in data for beat in dirty_stream(frame['damaged'], 4, rs.q)]
    # Keep enough frames in flight to leave independent router counters at
    # different positions. For the stalled case, fill all available storage.
    inst.ports.output.ready = False
    sent = 0
    for cycle in range(250):
        inst.ports.input.valid = sent < len(inputs)
        if sent < len(inputs):
            for name, value in inputs[sent].items():
                inst.ports.input[name] = value
        inst.step(1)
        accepted = inst.ports.input.valid and inst.ports.input.ready
        tick(inst)
        sent += int(accepted)
        # The first serial Forney worker starts at edge 14; reset during its
        # computation rather than only while upstream BM is busy.
        if phase == 'busy' and sent >= 9 and (forney_workers == 1 or cycle >= 18):
            break
    assert sent >= 9
    if phase == 'stalled':
        assert inst.ports.output.valid
    inst.ports.rst = True
    inst.step(1)
    tick(inst)
    assert not inst.ports.output.valid
    inst.ports.input.valid = False
    inst.ports.rst = False
    inst.step(1)
    exercise(inst, rs, data, 4, True)


@pytest.mark.parametrize('workers', [2, 3])
@pytest.mark.parametrize('buffered', [False, True])
@pytest.mark.parametrize('forney_workers', [1, 3])
def test_parallel_decoder_bad_frames(frames, workers, buffered, forney_workers):
    config, rs, data = frames
    rng = np.random.default_rng(0xBADF00D)
    for _ in range(100):
        damaged = np.array(data[-1]['word'], dtype=rs.dtype)
        positions = rng.choice(rs.n, rs.t + 1, replace=False)
        damaged[positions] ^= rng.integers(1, rs.q, rs.t + 1, dtype=rs.dtype)
        padding = (-rs.n) % 4
        syndromes = rs.gf_mul(rs.rs_syndromes(damaged),
                              rs.gf_exp(padding * (rs.fcr + np.arange(2 * rs.t))))
        lambdas, degree = rs.rs_berlekamp(syndromes)
        roots = [p for p in range(rs.n)
                 if rs.gf_poly_eval(lambdas, rs.gf_exp(-(p + padding))) == 0]
        if len(roots) <= rs.t and len(roots) != int(degree):
            break
    else:
        raise AssertionError('Failed to construct a detected uncorrectable frame')
    bad = dict(data[-1], damaged=damaged.tolist(), count=len(roots))
    short = dirty_stream(data[0]['damaged'], 4, rs.q)[:2]
    short[-1]['last'] = True
    long = dirty_stream(data[1]['damaged'], 4, rs.q)
    long[-1]['last'] = False
    long += [dict(data=[rs.q - 1] * 4, last=True)]
    inputs = []
    expected = []
    for i in range(12):
        frame = data[i] if i % 3 else bad
        inputs += short + long + dirty_stream(frame['damaged'], 4, rs.q)
        expected += message_rows(frame, 4, failed=i % 3 == 0)
    assert run(decoder(config, 4, buffered, workers, forney_workers,
                       unroll_forney=1 if forney_workers > 1 else 4), inputs, list(expected[0]),
               len(expected), stall_denominator=2) == expected


@pytest.mark.parametrize('forney_workers', [1, 2])
def test_default_parallel_decoder(rs, vectors, forney_workers):
    inst = instance('decoder', unroll=16, buffered=True, berlekamp_instances=2,
                    forney_instances=forney_workers)
    inputs = [beat for v in vectors for beat in stream(v['corrupted'], 16)]
    rows = run(inst, inputs, ['data', 'last', 'num_errors',
                             'failed'], len(vectors) * rs.k // 16,
               stall_denominator=2)
    for i, v in enumerate(vectors):
        frame = rows[i * rs.k // 16:(i + 1) * rs.k // 16]
        assert collect(frame) == v['message'].tolist()
        assert [r['last'] for r in frame] == [False] * (len(frame) - 1) + [True]
        assert all(r['num_errors'] == v['count'] and not r['failed'] for r in frame)


@pytest.mark.parametrize('workers', [2, 3])
@pytest.mark.parametrize('width', [3, 4, 16])
@pytest.mark.parametrize('buffered', [False, True])
def test_parallel_forney(frames, workers, width, buffered):
    config, rs, data = frames
    for stutter in (False, True):
        inst = decoder(config, width, buffered, 1, workers,
                       unroll_berlekamp=3, unroll_forney=1)
        first, times = exercise(inst, rs, data, width, stutter)
        if not stutter:
            cycles = (rs.n + width - 1) // width
            assert first == cycles + 2 + max(cycles, 1) + 7 + 1
            if width == 16:
                # B=2, H=1, F=7: bank reuse creates bursts when F/N_F
                # exceeds the upstream two-cycle initiation interval.
                expected = []
                for i in range(len(data)):
                    arrival = first + 2 * i
                    reuse = expected[i - workers] + 7 if i >= workers else arrival
                    expected.append(max(arrival, reuse))
                assert times == expected


@pytest.mark.parametrize('berlekamp_workers', [1, 2])
def test_parallel_forney_throughput(frames, berlekamp_workers):
    config, rs, data = frames
    results = {}
    bm_unroll = 3 if berlekamp_workers == 1 else 1
    for workers in (1, 2, 3):
        inst = decoder(config, 4, False, berlekamp_workers, workers,
                       unroll_berlekamp=bm_unroll, unroll_forney=1)
        results[workers] = exercise(inst, rs, data, 4, False)
    assert {first for first, _ in results.values()} == {18 if bm_unroll == 3 else 22}
    assert np.diff(results[1][1]).tolist() == [7] * (len(data) - 1)
    for workers in (2, 3):
        assert np.diff(results[workers][1]).tolist() == [4] * (len(data) - 1)
        assert results[workers][1][-1] < results[1][1][-1]
