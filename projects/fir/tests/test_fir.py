import ctypes
import random

import khdl
import pytest

from test_support.simulation import run, tick
from . import reference
from .util import DEFAULTS, instance, load, edge
from ..tools.compile import compile_source

PROFILES = [(1, 1, 0, 1), (5, 1, 0, 2), (7, 3, 1, 1),
            (9, 7, 2, 2), (11, 5, 2, 3), (13, 4, 2, 4), (17, 8, 3, 2), (1, 5, 0, 3),
            (3, 7, 1, 4), (6, 6, 2, 2), (2, 2, 1, 1)]


@pytest.mark.parametrize('taps,parallel,depth,group', PROFILES)
@pytest.mark.parametrize('stalls', [False, True])
def test_convolution(taps, parallel, depth, group, stalls):
    config = DEFAULTS | dict(taps=taps, parallel=parallel, ffa_depth=depth, taps_per_stage=group)
    inst = instance(taps=taps, parallel=parallel, ffa_depth=depth, taps_per_stage=group)
    rng = random.Random(971 + taps + parallel)
    samples = [rng.randrange(-128, 128) for _ in range(parallel * 31)]
    coefficients = [rng.randrange(-64, 64) for _ in range(taps)]
    load(inst, coefficients, gaps=True)
    inputs = [dict(samples=samples[i:i+parallel]) for i in range(0, len(samples), parallel)]
    expected = reference.beats(samples, coefficients, config)
    assert run(inst, inputs, ['samples', 'clipped'], len(expected), stutter=stalls,
               stall_denominator=2) == expected
    inst.ports.input.valid = False
    inst.ports.output.ready = True
    for _ in range(8):
        edge(inst)
        assert not inst.ports.output.valid


@pytest.mark.parametrize('output_w,input_f,coeff_f,output_f',
                         [(5, 0, 0, 0), (5, 2, 1, 1), (12, 0, 0, 3)])
def test_rounding_saturation_and_extrema(output_w, input_f, coeff_f, output_f):
    overrides = dict(input_w=4, input_f=input_f, output_w=output_w, output_f=output_f,
                     coeff_w=4, coeff_f=coeff_f, taps=3, parallel=3,
                     ffa_depth=1, taps_per_stage=1)
    config = DEFAULTS | overrides
    inst = instance(**overrides)
    samples = [-8, 7, -1, -3, 1, 3, -5, 5, 0] * 7
    coefficients = [-8, 7, -3]
    load(inst, coefficients)
    expected = reference.beats(samples, coefficients, config)
    assert run(inst, [dict(samples=samples[i:i+3]) for i in range(0, len(samples), 3)],
               ['samples', 'clipped'], len(expected)) == expected


@pytest.mark.parametrize('partition', [(), (8,), (3, 2, 3), (1, 3, 1, 3)])
def test_pipeline_grouping_and_full_rate(partition):
    check_full_rate(dict(pipeline_steps=partition))


def check_full_rate(overrides):
    config = DEFAULTS | overrides
    inst = instance(**overrides)
    width = config['parallel']
    taps = config['taps']
    rng = random.Random(63)
    samples = [rng.randrange(-20, 20) for _ in range(width * 75)]
    coefficients = [(-1)**i * (i % 9 + 1) for i in range(taps)]
    load(inst, coefficients)
    p = inst.ports
    p.output.ready = True
    sent, rows, cycles = 0, [], []
    accepted_cycles = []
    for cycle in range(1000):
        p.input.valid = sent < 75
        p.input.samples = samples[sent*width:(sent+1)*width] if sent < 75 else [0]*width
        inst.step(1)
        accepted = p.input.valid and p.input.ready
        if p.output.valid:
            rows.append(dict(samples=p.output.samples, clipped=p.output.clipped))
            cycles.append(cycle)
        if accepted:
            accepted_cycles.append(cycle)
        tick(inst)
        sent += int(accepted)
        if len(rows) == 75:
            break
    else:
        pytest.fail('pipeline failed to drain')
    assert rows == reference.beats(samples, coefficients, config)
    assert accepted_cycles == list(range(accepted_cycles[0], accepted_cycles[0]+75))
    assert cycles == list(range(cycles[0], cycles[0]+75))


def test_input_waits_for_coefficients_and_finalization_is_bounded():
    inst = instance()
    p = inst.ports
    p.input.valid = True
    p.input.samples = [7]*7
    p.output.ready = True
    for _ in range(6):
        assert not p.input.ready and not p.output.valid
        edge(inst)
    for i in range(9):
        assert not p.input.ready and not p.output.valid
        p.coeff_push = True
        p.coeff_value = 1
        edge(inst)
    p.coeff_push = False
    for delay in range(4):
        if p.input.ready:
            break
        edge(inst)
    else:
        pytest.fail('unexpected coefficient finalization pass')


@pytest.mark.parametrize('bad', [dict(taps=0), dict(parallel=0), dict(ffa_depth=3),
    dict(taps_per_stage=0), dict(pipeline_steps=(0, 8)), dict(pipeline_steps=(7,))])
def test_invalid_configuration(bad):
    c = compile_source()
    with pytest.raises(khdl.DiagnosticException):
        c.resolve('fir.Fir')(**(DEFAULTS | bad))


def test_replacement_requires_clear(capfd):
    inst = instance()
    load(inst, [1]*9)
    inst.ports.coeff_push = True
    with pytest.raises(khdl.SimulationFinishedException):
        edge(inst)
    ctypes.CDLL(None).fflush(None)
    assert 'coeff_clear required' in capfd.readouterr().out


@pytest.mark.parametrize('taps,parallel,depth,group', [(5, 1, 0, 2), (13, 4, 2, 4), (3, 7, 1, 4), (6, 6, 2, 2)])
def test_full_rate_phase_shapes(taps, parallel, depth, group):
    check_full_rate(dict(taps=taps, parallel=parallel, ffa_depth=depth, taps_per_stage=group))


def test_depth_does_not_change_values():
    rng = random.Random(787)
    samples = [rng.randrange(-128, 128) for _ in range(7*23)]
    coefficients = [rng.randrange(-64, 64) for _ in range(9)]
    expected = reference.beats(samples, coefficients, DEFAULTS)
    for depth in (0, 1, 2):
        inst = instance(ffa_depth=depth)
        load(inst, coefficients)
        assert run(inst, [dict(samples=samples[i:i+7]) for i in range(0, len(samples), 7)],
                   ['samples', 'clipped'], len(expected), stall_denominator=2) == expected
