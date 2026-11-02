"""Port of reedsolo_encoder_tb: generated vectors replace the static VHDL tables.

The DUT is driven by the shared clock/input/output/stutter processes in util.py.
"""
import numpy as np
import pytest

from .reed_solomon import ReedSolomon
from .util import collect, instance, run, stream, tick, transfer


GF16 = dict(m=4, prim=0x13, alpha=3, fcr=0)

ENCODER_PROFILES = [
    pytest.param(dict(n=11, k=5), 4, id='both-unaligned-one-data-lane'),
    pytest.param(dict(n=12, k=6), 4, id='only-k-unaligned'),
    pytest.param(dict(n=13, k=7), 4, id='both-unaligned-three-data-lanes'),
    pytest.param(dict(n=14, k=8), 4, id='only-n-unaligned'),
    pytest.param(dict(n=10, k=6), 4, id='aligned-parity'),
    pytest.param(dict(n=8, k=2), 4, id='single-input-beat'),
    pytest.param(dict(n=7, k=1), 4, id='single-data-symbol'),
    pytest.param(dict(n=11, k=5), 6, id='width-equals-parity'),
    pytest.param(dict(n=7, k=1), 6, id='one-parity-symbol-after-mixed-beat'),
    pytest.param(dict(n=13, k=7), 5, id='five-lanes'),
    pytest.param(dict(n=8, k=2), 3, id='three-lanes'),
    pytest.param(dict(n=3, k=1, m=2, prim=0x7, alpha=2, fcr=1), 2,
                 id='gf4-smallest-code'),
    pytest.param(dict(n=240, k=224, m=8, prim=0x11d, alpha=2, fcr=1), 3,
                 id='default-three-lanes'),
    pytest.param(dict(n=14, k=8), 8, id='wide-aligned-message'),
    pytest.param(dict(n=13, k=7), 8, id='wide-mixed-then-parity'),
    pytest.param(dict(n=8, k=2), 8, id='mixed-fills-output'),
    pytest.param(dict(n=7, k=1), 8, id='mixed-final-padding'),
    pytest.param(dict(n=15, k=9), 8, id='multibeat-mixed-final-padding'),
    pytest.param(dict(n=15, k=13, fcr=1), 4, id='width-exceeds-parity'),
    pytest.param(dict(n=13, k=7), 16, id='single-codeword-beat'),
    pytest.param(dict(n=3, k=1, m=2, prim=0x7, alpha=2, fcr=1), 4,
                 id='width-exceeds-field-period'),
]


@pytest.mark.parametrize('parameters,unroll', ENCODER_PROFILES)
@pytest.mark.parametrize('buffered', [False, True])
def test_encoder_padding(parameters, unroll, buffered):
    config = GF16 | parameters
    rs = ReedSolomon(**config)
    inst = instance('encoder', **config, unroll=unroll, buffered=buffered)
    rng = np.random.default_rng(0x504144)
    messages = [rng.integers(0, rs.q, rs.k, dtype=rs.dtype) for _ in range(8)]
    messages[0][:] = 0
    messages[1][:] = rs.q - 1
    messages[2][:] = np.arange(rs.k) % rs.q
    words = [np.concatenate((rs.rs_parity(message), message)) for message in messages]
    inputs = [beat for message in messages for beat in stream(message, unroll)]
    expected = [dict(data=beat['data'], last=beat['last'])
                for word in words for beat in stream(word, unroll)]
    # Continuous frames and independently stalled inputs/outputs exercise
    # arithmetic tail storage, the mixed beat, and final padding.
    for stutter in (False, True):
        rows = run(inst, inputs, ['data', 'last'], len(expected),
                   stutter=stutter, stall_denominator=2)
        assert rows == expected
        # Draining must leave no delayed extra beat, including after the last
        # frame. The next run also checks state cleanup without asserting reset.
        inst.ports.output.ready = True
        for _ in range(3):
            tick(inst)
            assert not inst.ports.output.valid


@pytest.mark.parametrize('parameters,unroll', ENCODER_PROFILES)
@pytest.mark.parametrize('buffered', [False, True])
def test_encoder_padding_latency(parameters, unroll, buffered):
    config = GF16 | parameters
    inst = instance('encoder', **config, unroll=unroll, buffered=buffered)
    inputs = stream(range(config['k']), unroll)
    cycles = []
    output_count = (config['n'] + unroll - 1) // unroll
    for cycle, (sent, rows) in enumerate(transfer(
            inst, inputs, ['data', 'last'], stutter=False)):
        if len(rows) > len(cycles):
            cycles.append(cycle)
        if len(rows) == output_count:
            assert sent == len(inputs)
            break
    # Aligned, mixed, and single-input-beat frames all start one cycle after
    # input acceptance and continue without bubbles at the data/parity boundary.
    assert cycles == list(range(1, 1 + output_count))


@pytest.mark.parametrize('buffered', [False, True])
def test_encoder_forwards_without_next_input(buffered):
    config = GF16 | dict(n=12, k=6)
    inst = instance('encoder', **config, unroll=4, buffered=buffered)
    first = stream(range(6), 4)[0]
    rows = run(inst, [first], ['data', 'last'], 1, stutter=False)
    assert rows == [dict(data=first['data'], last=False)]


@pytest.mark.parametrize('buffered', [False, True])
def test_encoder_ignores_input_padding(buffered):
    config = GF16 | dict(n=13, k=7)
    rs = ReedSolomon(**config)
    message = np.arange(1, rs.k + 1, dtype=rs.dtype)
    inputs = stream(message, 4)
    inputs[-1]['data'][:(-rs.k) % 4] = [rs.q - 1] * ((-rs.k) % 4)
    word = np.concatenate((rs.rs_parity(message), message))
    expected = [dict(data=beat['data'], last=beat['last'])
                for beat in stream(word, 4)]
    inst = instance('encoder', **config, unroll=4, buffered=buffered)
    assert run(inst, inputs, ['data', 'last'], len(expected)) == expected


@pytest.mark.parametrize('unroll,phase', [
    (4, 'first'), (4, 'mixed'), (4, 'final'),
    (8, 'mixed'), (8, 'final'), (16, 'final'),
])
@pytest.mark.parametrize('buffered', [False, True])
def test_encoder_padding_reset(unroll, phase, buffered):
    config = GF16 | dict(n=13, k=7)
    rs = ReedSolomon(**config)
    message = np.arange(1, rs.k + 1, dtype=rs.dtype)
    word = np.concatenate((rs.rs_parity(message), message))
    inputs = stream(message, unroll)
    expected = [dict(data=beat['data'], last=beat['last'])
                for beat in stream(word, unroll)]
    inst = instance('encoder', **config, unroll=unroll, buffered=buffered)
    target = dict(first=0, mixed=len(inputs)-1, final=len(expected)-1)[phase]
    for sent, rows in transfer(inst, inputs, ['data', 'last'], stutter=False):
        if len(rows) == target and inst.ports.output.valid:
            assert inst.ports.output.data == expected[target]['data']
            assert inst.ports.output.last == expected[target]['last']
            break
    else:
        pytest.fail('Expected reset target beat was never produced')
    inst.ports.input.valid = False
    inst.ports.output.ready = False
    # Hold the first data beat, mixed beat, or final padded beat under a stall.
    tick(inst)
    held = (inst.ports.output.data, inst.ports.output.last)
    assert inst.ports.output.valid
    tick(inst)
    assert (inst.ports.output.data, inst.ports.output.last) == held
    inst.ports.rst = True
    inst.step(1)
    assert inst.ports.output.valid, 'Reset must wait for a clock edge'
    tick(inst)
    assert not inst.ports.output.valid
    inst.ports.rst = False
    # Replace the discarded frame immediately, without inheriting any previous
    # data tail, parity coefficients, output counter, or input holding beat.
    rows = run(inst, inputs, ['data', 'last'], len(expected))
    assert rows == expected

@pytest.mark.parametrize('unroll', [1, 2, 4, 8, 16])
@pytest.mark.parametrize('buffered', [False, True])
def test_encoder(rs, vectors, unroll, buffered):
    inst = instance('encoder', unroll=unroll, buffered=buffered)
    inputs = [beat for v in vectors for beat in stream(v['message'], unroll)]
    rows = run(inst, inputs, ['data','last'], len(vectors)*rs.n//unroll)
    for i, v in enumerate(vectors):
        part = rows[i*rs.n//unroll:(i+1)*rs.n//unroll]
        assert collect(part) == v['word'].tolist()
        assert [r['last'] for r in part] == [False]*(len(part)-1)+[True]

@pytest.mark.parametrize('buffered', [False,True])
def test_encoder_synchronous_reset(rs,vectors,buffered):
    from .util import tick
    inst=instance('encoder',unroll=16,buffered=buffered)
    # Create a stalled output in the middle of a message.
    inst.ports.input.data=vectors[4]['message'][-16:].tolist()
    inst.ports.input.last=False
    inst.ports.input.valid=True
    inst.ports.output.ready=False
    tick(inst)
    inst.ports.input.valid=False
    assert inst.ports.output.valid
    inst.ports.rst=True
    inst.step(1)
    assert inst.ports.output.valid, 'Reset must wait for a clock edge'
    tick(inst)
    assert not inst.ports.output.valid
    inst.ports.rst=False
    rows=run(inst,stream(vectors[5]['message'],16),['data','last'],rs.n//16)
    assert collect(rows)==vectors[5]['word'].tolist()
