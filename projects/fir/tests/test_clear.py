import random

import pytest

from test_support.simulation import run, tick
from . import reference
from .util import DEFAULTS, instance, load, edge


def row(p):
    return dict(samples=p.output.samples, clipped=p.output.clipped)


@pytest.mark.parametrize('partition', [(), (8,), (4, 4), (2, 3, 3)])
@pytest.mark.parametrize('repeat', [False, True])
@pytest.mark.parametrize('output_w', [5, 20])
def test_clear_preserves_stalled_output_and_separates_coefficients(partition, repeat, output_w):
    config = DEFAULTS | dict(output_w=output_w, pipeline_steps=partition)
    inst = instance(output_w=output_w, pipeline_steps=partition)
    p = inst.ports
    old_h = [8]*9
    load(inst, old_h)
    old_x = [3+i%29 for i in range(7*19)]
    expected_old = reference.beats(old_x, old_h, config)
    sent = 0
    consumed = 0
    p.output.ready = True
    for _ in range(200):
        p.input.valid = sent < 19
        p.input.samples = old_x[sent*7:(sent+1)*7] if sent < 19 else [0]*7
        inst.step(1)
        accepted = p.input.valid and p.input.ready
        if p.output.valid:
            assert row(p) == expected_old[consumed]
            consumed += 1
        tick(inst)
        sent += int(accepted)
        if sent == 19:
            break
    assert sent == 19
    p.input.valid = False
    p.output.ready = False
    for _ in range(100):
        inst.step(1)
        if p.output.valid:
            break
        tick(inst)
    else:
        pytest.fail('no old output to stall')
    held = row(p)

    def held_edge(clear, push, value=0):
        p.coeff_clear = clear
        p.coeff_push = push
        p.coeff_value = value
        edge(inst)
        assert p.output.valid and row(p) == held

    if repeat:
        # Abort a replacement half-loaded; repeated clears are idempotent.
        held_edge(True, True, 3)
        held_edge(False, True, 4)
        held_edge(False, True, 5)
        held_edge(True, False)
        held_edge(True, False)
    new_h = [-1] + [0]*8
    for i, h in enumerate(new_h):
        held_edge(i == 0, True, h)
    for _ in range(5):
        held_edge(False, False)
    p.coeff_clear = p.coeff_push = False
    new_x = [4+i%23 for i in range(7*25)]
    expected_new = reference.beats(new_x, new_h, config)
    rng = random.Random(131)
    sent = 0
    received = []
    new_started = False
    pending = False
    stalled = held
    for _ in range(2000):
        if sent < 25 and not pending:
            p.input.samples = new_x[sent*7:(sent+1)*7]
            pending = True
        p.input.valid = pending
        p.output.ready = bool(rng.randrange(3))
        inst.step(1)
        if stalled is not None:
            assert p.output.valid and row(p) == stalled
        stalled = row(p) if p.output.valid and not p.output.ready else None
        accepted = pending and p.input.ready
        if p.output.valid and p.output.ready:
            actual = row(p)
            if actual['samples'][0] < 0:
                new_started = True
                assert actual == expected_new[len(received)]
                received.append(actual)
            else:
                assert not new_started
                assert actual == expected_old[consumed]
                consumed += 1
        tick(inst)
        if accepted:
            sent += 1
            pending = False
        if len(received) == 25:
            break
    assert received == expected_new
    assert consumed > 0  # At least the stalled old output survived clear.


def test_clear_at_partial_load_and_reset_restart():
    inst = instance()
    p = inst.ports
    p.output.ready = True
    load(inst, [1, 2, 3])
    assert not p.input.ready
    p.coeff_clear = True
    p.coeff_push = False
    edge(inst); edge(inst)
    p.coeff_clear = False
    load(inst, [4]*9)
    assert run(inst, [dict(samples=[5]*7)], ['samples', 'clipped'], 1) == reference.beats(
        [5]*7, [4]*9, DEFAULTS)
    p.input.valid = False
    p.rst = True
    edge(inst); edge(inst)
    assert not p.output.valid and not p.input.ready
    p.rst = False
    load(inst, [-4]*9)
    assert run(inst, [dict(samples=[5]*7)], ['samples', 'clipped'], 1) == reference.beats(
        [5]*7, [-4]*9, DEFAULTS)
