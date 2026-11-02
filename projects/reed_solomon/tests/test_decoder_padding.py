"""Padded streaming, padded-root coordinates, and decoder boundary cases."""
from functools import lru_cache

import numpy as np
import pytest

from .reed_solomon import ReedSolomon
from .streams import run_streams, record_message, committed_message, message_beats
from .util import instance, run, stream, tick, transfer


GF16 = dict(m=4, prim=0x13, alpha=3, fcr=0)
PROFILES = [
    pytest.param(GF16 | dict(n=12, k=6), 4, id='only-k-unaligned'),
    pytest.param(GF16 | dict(n=14, k=8), 4, id='only-n-unaligned'),
    pytest.param(GF16 | dict(n=13, k=7), 4, id='both-unaligned'),
    pytest.param(GF16 | dict(n=11, k=5, fcr=1), 4, id='one-valid-final-lane'),
    pytest.param(GF16 | dict(n=8, k=2, fcr=1), 4, id='single-message-beat'),
    pytest.param(GF16 | dict(n=13, k=7), 5, id='nonbinary-width'),
    pytest.param(GF16 | dict(n=15, k=9, alpha=2, fcr=1), 4, id='padded-field-period'),
    pytest.param(dict(n=3, k=1, m=2, prim=0x7, alpha=2, fcr=1), 2, id='gf4'),
    pytest.param(dict(n=240, k=224, m=8, prim=0x11d, alpha=2, fcr=1), 3,
                 id='default-three-lanes'),
    pytest.param(GF16 | dict(n=15, k=13, fcr=1), 4, id='width-exceeds-parity'),
    pytest.param(dict(n=3, k=1, m=2, prim=0x7, alpha=2, fcr=1), 4,
                 id='width-exceeds-field-period'),
    pytest.param(GF16 | dict(n=13, k=7), 16, id='single-codeword-beat'),
]
# Standalone stage checks include short messages, mixed beats, both fcr values,
# nonbinary butterflies, and padding positions aliasing real field locations.
STAGE_PROFILES = [PROFILES[i] for i in (0, 2, 3, 5, 6, 10, 11)]


def balanced_positions(n, t, width, positions):
    """Route known roots through the reference butterfly, without GF arithmetic."""
    layers = (width - 1).bit_length()
    banks = 1 << layers
    cycles = (n + width - 1) // width
    padding = cycles * width - n
    roots = {int(p) + padding for p in positions}
    displacement = [False] * (banks - 1)
    slots = [None] * t
    for cycle in range(cycles):
        lanes = [cycle * width + i if cycle * width + i in roots else None
                 for i in range(width)] + [None] * (banks - width)
        for layer in range(layers):
            for group in range(1 << (layers - layer - 1)):
                for h in range(1 << layer):
                    left = 2 * group * (1 << layer) + h
                    right = left + (1 << layer)
                    index = h + (1 << layer) - 1
                    old = displacement[index]
                    a, b = lanes[left] is not None, lanes[right] is not None
                    displacement[index] = old ^ a ^ b
                    if old == a:
                        lanes[left], lanes[right] = lanes[right], lanes[left]
        for bank in range(min(banks, t)):
            if lanes[bank] is not None:
                for j in reversed(range((t - bank - 1) // banks)):
                    slots[bank + (j + 1) * banks] = slots[bank + j * banks]
                slots[bank] = lanes[bank]
    assert sum(p is not None for p in slots) == len(positions)
    assert all(p is None for p in slots[len(positions):])
    return slots


@lru_cache
def vectors(n, k, m, prim, alpha, fcr, width):
    rs = ReedSolomon(n=n, k=k, m=m, prim=prim, alpha=alpha, fcr=fcr)
    rng = np.random.default_rng(0xDEC0DE)
    padding = (-n) % width
    positions = range(n) if n < 32 else sorted({0, 1, n-k-1, n-k, n-k+1, n-2, n-1})
    patterns = [()] + [(p,) for p in positions]
    patterns += [tuple(rng.choice(n, count, replace=False)) for count in range(2, rs.t + 1)]
    patterns += [tuple(rng.choice(n, rs.t, replace=False)) for _ in range(8)]
    result = []
    for index, errors in enumerate(patterns):
        message = rng.integers(0, rs.q, k, dtype=rs.dtype)
        if index == 0:
            message[:] = 0
        elif index == 1:
            message[:] = rs.q - 1
        word = np.concatenate((rs.rs_parity(message), message))
        damaged = word.copy()
        magnitudes = rng.integers(1, rs.q, len(errors), dtype=rs.dtype)
        for p, value in zip(errors, magnitudes):
            damaged[p] ^= value
        # Independent scalar oracle: S_j(x**P C) = alpha**(P*(fcr+j))*S_j(C).
        syndromes = rs.gf_mul(rs.rs_syndromes(damaged),
                              rs.gf_exp(padding * (fcr + np.arange(2 * rs.t))))
        lambdas, count = rs.rs_berlekamp(syndromes)
        assert int(count) == len(errors)
        omegas = rs.rs_omegas(syndromes, lambdas)
        slots = balanced_positions(n, rs.t, width, errors)
        cycles = (n + width - 1) // width
        values = dict(zip(errors, magnitudes.tolist()))
        result.append(dict(
            message=message.tolist(), word=word.tolist(), damaged=damaged.tolist(),
            syndromes=syndromes.tolist(), lambdas=lambdas.tolist(), omegas=omegas.tolist(),
            count=len(errors), cpos=[cycles if p is None else p // width for p in slots],
            upos=[0 if p is None else p % width for p in slots],
            xinv=[0 if p is None else int(rs.gf_exp(-p)) for p in slots],
            values=[0 if p is None else values[p-padding] for p in slots],
        ))
    return rs, result


def dirty_stream(values, width, q):
    beats = stream(values, width)
    padding = (-len(values)) % width
    beats[-1]['data'][:padding] = [q-1] * padding
    return beats


def message_rows(v, width, failed=False):
    message = v['damaged'][len(v['word']) - len(v['message']):] if failed else v['message']
    return [dict(data=b['data'], last=b['last'],
                 num_errors=v['count'], failed=failed) for b in stream(message, width)]


@pytest.mark.parametrize('config,width', PROFILES)
@pytest.mark.parametrize('buffered', [False, True])
def test_padded_pipeline(config, width, buffered):
    rs, frames = vectors(**config, width=width)
    encoder = instance('encoder', **config, unroll=width, buffered=buffered)
    inputs = [b for v in frames for b in dirty_stream(v['message'], width, rs.q)]
    expected = [dict(data=b['data'], last=b['last'])
                for v in frames for b in stream(v['word'], width)]
    assert run(encoder, inputs, ['data', 'last'], len(expected),
               stall_denominator=2) == expected
    decoder = instance('decoder', **config, unroll=width, buffered=buffered)
    inputs = [b for v in frames for b in dirty_stream(v['damaged'], width, rs.q)]
    expected = [row for v in frames for row in message_rows(v, width)]
    for stutter in (False, True):
        maximum_overlap = 0
        for sent, rows in transfer(decoder, inputs, list(expected[0]), stutter=stutter,
                                   stall_denominator=2):
            started = (sent + (rs.n + width - 1) // width - 1) // ((rs.n + width - 1) // width)
            finished = len(rows) // ((rs.k + width - 1) // width)
            maximum_overlap = max(maximum_overlap, started - finished)
            assert len(rows) <= len(expected)
            if sent == len(inputs) and len(rows) == len(expected):
                decoder.ports.input.valid = False
                break
        assert rows == expected
        assert maximum_overlap >= 2
        decoder.ports.output.ready = True
        for _ in range(3):
            tick(decoder)
            assert not decoder.ports.output.valid


@pytest.mark.parametrize('config,width', STAGE_PROFILES)
@pytest.mark.parametrize('buffered', [False, True])
def test_padded_syndromes(config, width, buffered):
    rs, frames = vectors(**config, width=width)
    inst = instance('syndromes', **config, unroll=width, buffered=buffered)
    inputs = [b for v in frames for b in dirty_stream(v['damaged'], width, rs.q)]
    expected = [dict(data=inst.info_ports.output.data.type.new(syndromes=v['syndromes']))
                for v in frames]
    # Padding is cleared only on the final codeword beat, so forwarded message
    # beats match the clean stream.
    message = [b for v in frames for b in message_beats(stream(v['damaged'], width), rs.k, width)]
    events = []
    rows = run_streams(inst, {'input': inputs},
                       {'output': (['data'], len(expected)), 'message': (['data'], len(message))},
                       stall_denominator=2, observe=record_message(events))
    assert rows['output'] == expected
    assert committed_message(events) == message


@pytest.mark.parametrize('config,width', STAGE_PROFILES)
def test_padded_chien(config, width):
    _, frames = vectors(**config, width=width)
    inst = instance('chien', **config, unroll_chien=width)
    inputs = [dict(data=dict(
        syndromes=v['syndromes'],
        lambdas=v['lambdas'],
        num_errors=v['count'],
    )) for v in frames]
    expected = [dict(data=inst.info_ports.output.data.type.new(
        omegas=v['omegas'],
        lambdas=v['lambdas'],
        error_cpos=v['cpos'],
        error_upos=v['upos'],
        error_xinv=v['xinv'],
        num_errors=v['count'],
        failed=False,
    ))
                for inp, v in zip(inputs, frames)]
    assert run(inst, inputs, list(expected[0]), len(expected), stall_denominator=2) == expected


@pytest.mark.parametrize('config,width', STAGE_PROFILES)
def test_padded_forney(config, width):
    rs, frames = vectors(**config, width=width)
    inst = instance('forney', **config, unroll=min(4, 2*rs.t), unroll_chien=width)
    inputs = [dict(data=dict(
        omegas=v['omegas'],
        lambdas=v['lambdas'],
        error_cpos=v['cpos'],
        error_upos=v['upos'],
        error_xinv=v['xinv'],
        num_errors=v['count'],
        failed=False,
    )) for v in frames]
    rows = run(inst, inputs, ['data'], len(inputs), stall_denominator=2)
    for v, row in zip(frames, rows):
        assert row['data'].error_cpos == v['cpos']
        assert row['data'].error_upos == v['upos']
        assert row['data'].error_val[:v['count']] == v['values'][:v['count']]
        assert row['data'].num_errors == v['count'] and not row['data'].failed


@pytest.mark.parametrize('config,width', STAGE_PROFILES)
def test_padded_correction(config, width):
    rs, frames = vectors(**config, width=width)
    inst = instance('correction', **config, unroll=width)
    # Message beats arrive as forwarded by RsSyndromes, with parity in the
    # mixed beat and arbitrary values in final padding lanes.
    message = [dict(data=b) for v in frames
               for b in message_beats(dirty_stream(v['damaged'], width, rs.q), rs.k, width)]
    for failed in (False, True):
        inputs = [dict(data=dict(
            error_cpos=v['cpos'],
            error_upos=v['upos'],
            error_val=v['values'],
            num_errors=v['count'],
            failed=failed,
        )) for v in frames]
        expected = [row for v in frames for row in message_rows(v, width, failed)]
        rows = run_streams(inst, {'input': inputs, 'message': message},
                           {'output': (list(expected[0]), len(expected))}, stall_denominator=2)
        assert rows['output'] == expected


@pytest.mark.parametrize('buffered', [False, True])
def test_padded_syndromes_bad_length(buffered):
    config = GF16 | dict(n=13, k=7)
    rs, frames = vectors(**config, width=4)
    valid = dirty_stream(frames[-1]['damaged'], 4, rs.q)
    short = stream(frames[0]['word'], 4)[:2]
    short[-1]['last'] = True
    long = stream(frames[1]['word'], 4)
    long[-1]['last'] = False
    long += [dict(data=[rs.q-1]*4, last=True)]
    inst = instance('syndromes', **config, unroll=4, buffered=buffered)
    expected = [dict(data=inst.info_ports.output.data.type.new(
        syndromes=frames[-1]['syndromes'],
    ))] * 2
    # Malformed frames forward some message beats, then cancel them.
    good = message_beats(stream(frames[-1]['damaged'], 4), rs.k, 4)
    events = []
    inst.ports.message.ready = True
    rows = run_streams(inst, {'input': short + valid + long + valid}, {'output': (['data'], 2)},
                       stall_denominator=2, observe=record_message(events))
    assert rows['output'] == expected
    assert committed_message(events) == good * 2


@pytest.mark.parametrize('fcr', [0, 1])
@pytest.mark.parametrize('buffered', [False, True])
def test_padded_detected_failure(fcr, buffered):
    config = GF16 | dict(n=13, k=7, fcr=fcr)
    rs, frames = vectors(**config, width=4)
    rng = np.random.default_rng(0xBADF00D)
    for _ in range(100):
        damaged = np.array(frames[-1]['word'], dtype=rs.dtype)
        positions = rng.choice(rs.n, rs.t+1, replace=False)
        damaged[positions] ^= rng.integers(1, rs.q, rs.t+1, dtype=rs.dtype)
        syndromes = rs.gf_mul(rs.rs_syndromes(damaged),
                              rs.gf_exp(((-rs.n) % 4) * (fcr + np.arange(2*rs.t))))
        lambdas, degree = rs.rs_berlekamp(syndromes)
        roots = [p for p in range(rs.n) if rs.gf_poly_eval(lambdas, rs.gf_exp(-(p+(-rs.n)%4))) == 0]
        if len(roots) <= rs.t and len(roots) != int(degree):
            break
    else:
        raise AssertionError('Failed to construct a detected uncorrectable frame')
    bad = dict(frames[-1], damaged=damaged.tolist(), count=len(roots))
    inputs = dirty_stream(damaged, 4, rs.q) + dirty_stream(frames[1]['damaged'], 4, rs.q)
    expected = message_rows(bad, 4, failed=True) + message_rows(frames[1], 4)
    inst = instance('decoder', **config, unroll=4, buffered=buffered)
    assert run(inst, inputs, list(expected[0]), len(expected), stall_denominator=2) == expected
