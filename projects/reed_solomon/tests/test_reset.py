"""Synchronous reset timing, output values, and restart of every pipeline stage."""
import numpy as np
import pytest

from .reed_solomon import ReedSolomon
from .util import instance, message_side, run, stream, tick, transfer


CONFIGS = [
    pytest.param(dict(n=12, k=6, m=4, prim=0x13, alpha=3, fcr=0), id='aligned'),
    pytest.param(dict(n=13, k=7, m=4, prim=0x13, alpha=3, fcr=0), id='padded'),
]
CASES = [
    pytest.param('encoder', dict(unroll=3, buffered=buffered),
                 id=f'encoder-buffered-{buffered}') for buffered in (False, True)
] + [
    pytest.param('syndromes', dict(unroll=3, buffered=buffered),
                 id=f'syndromes-buffered-{buffered}') for buffered in (False, True)
] + [
    pytest.param('berlekamp', dict(unroll=3), id='berlekamp'),
    pytest.param('chien', dict(unroll_omegas=3, unroll_chien=3), id='chien'),
    pytest.param('forney', dict(unroll=4, unroll_chien=3), id='forney'),
    pytest.param('correction', dict(unroll=3), id='correction'),
] + [
    pytest.param('decoder', dict(buffered=buffered, unroll=3,
                                unroll_berlekamp=3, unroll_omegas=3, unroll_forney=4),
                 id=f'decoder-buffered-{buffered}') for buffered in (False, True)
]


def frame(rs, stage):
    message = np.arange(1, rs.k + 1, dtype=rs.dtype)
    word = np.concatenate((rs.rs_parity(message), message))
    if stage == 'encoder':
        return stream(message, 3), (rs.n + 2) // 3
    if stage == 'syndromes':
        # One wrong symbol gives nonzero syndromes, distinct from reset values.
        word[-1] ^= 1
    if stage in ('syndromes', 'decoder'):
        return stream(word, 3), (rs.k + 2) // 3 if stage == 'decoder' else 1
    block = {}
    if stage in ('berlekamp', 'chien'):
        block['syndromes'] = [0] * (2 * rs.t)
    if stage == 'chien':
        block.update(lambdas=[1] + [0] * rs.t, num_errors=0)
    if stage == 'forney':
        block.update(omegas=[0] * rs.t, lambdas=[1] + [0] * rs.t, error_cpos=[0] * rs.t,
                     error_upos=[0] * rs.t, error_xinv=[0] * rs.t,
                     num_errors=0, failed=True)
    if stage == 'correction':
        # Failed frames bypass correction and forward the message beats.
        block.update(error_cpos=[0] * rs.t, error_upos=[0] * rs.t,
                     error_val=[0] * rs.t, num_errors=1,
                     failed=True)
    return [dict(data=block)], (rs.k + 2) // 3 if stage == 'correction' else 1


def nonzero(value):
    if isinstance(value, (list, tuple)):
        return any(nonzero(item) for item in value)
    if hasattr(value, 'fields'):
        return any(nonzero(item) for item in value.fields.values())
    return bool(value)


@pytest.mark.parametrize('stage,generics', CASES)
@pytest.mark.parametrize('phase', ['processing', 'output'])
@pytest.mark.parametrize('config', CONFIGS)
def test_synchronous_reset(stage, generics, phase, config):
    rs = ReedSolomon(**config)
    inst = message_side(instance(stage, **config, **generics))
    fields = [name for name in inst.info_ports.output
              if inst.info_ports.output[name].direction == 'output']
    inputs, count = frame(rs, stage)
    # A fresh instance supplies the complete response for the restart check;
    # the stage and pipeline benches independently verify the arithmetic.
    expected = run(message_side(instance(stage, **config, **generics)), inputs, fields, count,
                   stutter=False)
    for sent, _ in transfer(inst, inputs, fields, stutter=False):
        if (sent > 0 if phase == 'processing' else inst.ports.output.valid):
            break
    inst.ports.input.valid = False
    inst.ports.output.ready = False
    inst.step(1)
    if phase == 'output':
        assert inst.ports.output.valid
        assert any(nonzero(inst.ports.output[name]) for name in fields
                   if name not in ('valid', 'last')), 'Expected nonzero payload'
    # Invalid correction payload/status have undef reset values. The design
    # permits either holding these registers or resetting them to X.
    reset_fields = fields
    if stage in ('correction', 'decoder'):
        reset_fields = [name for name in reset_fields if name not in
                        ('data', 'num_errors', 'failed')]
    before = {name: inst.ports.output[name] for name in reset_fields}
    before_ready = inst.ports.input.ready

    # Assert reset during processing or with an output pending, while input
    # valid is high and output is stalled. Reset must wait for a clock edge,
    # and input must be ignored throughout reset.
    inst.ports.input.valid = True
    inst.ports.rst = True
    inst.step(1)
    assert {name: inst.ports.output[name] for name in before} == before
    assert inst.ports.input.ready == before_ready
    reset_values = {name: [0] * len(before[name]) if isinstance(before[name], list) else 0
                    for name in reset_fields}
    if stage in ('syndromes', 'berlekamp', 'chien', 'forney'):
        payload = {}
        if stage in ('syndromes', 'berlekamp'):
            payload['syndromes'] = [0] * (2 * rs.t)
        if stage == 'berlekamp':
            payload.update(lambdas=[1] + [0] * rs.t, num_errors=0)
        if stage == 'chien':
            payload.update(omegas=[0] * rs.t, lambdas=[0] * (rs.t + 1),
                           error_cpos=[0] * rs.t, error_upos=[0] * rs.t,
                           error_xinv=[0] * rs.t, num_errors=0, failed=False)
        if stage == 'forney':
            payload.update(error_cpos=[0] * rs.t, error_upos=[0] * rs.t,
                           error_val=[0] * rs.t, num_errors=0, failed=False)
        reset_values['data'] = inst.info_ports.output.data.type.new(**payload)
    for ready in (False, True):
        inst.ports.output.ready = ready
        tick(inst)
        assert {name: inst.ports.output[name] for name in reset_fields} == reset_values
        assert inst.ports.input.ready
    inst.ports.input.valid = False
    inst.ports.rst = False
    inst.step(1)
    assert {name: inst.ports.output[name] for name in reset_fields} == reset_values
    assert inst.ports.input.ready
    assert run(inst, inputs, fields, count, stall_denominator=2) == expected
