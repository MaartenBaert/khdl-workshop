"""Compile and simulate the exact reusable examples shipped with the KHDL skill."""
from pathlib import Path
import random

import khdl
import pytest

from test_support.simulation import build_simulator

EXAMPLE = Path(__file__).resolve().parents[3] / 'skills/khdl/assets/round_robin.kh'


def compiler():
    source = khdl.Source()
    source.add_file_content(['round_robin'], str(EXAMPLE), EXAMPLE.read_text())
    source.add_file_content(['probe'], 'probe.kh', '''
        import round_robin.[Selection, round_robin_linear, round_robin_tree, round_robin_single_tree];
        module Probe(count: positive) ports(
            request: in async [count]bool, last: in async uint(0..count),
            linear: out async Selection(count), tree: out async Selection(count),
            single: out async Selection(count),
        ) {
            comb {
                linear = round_robin_linear(count, request, last);
                tree = round_robin_tree(count, request, last);
                single = round_robin_single_tree(count, request, last);
            }
        }
    ''')
    return source.compile()


def expected(request, last):
    active = [i for i, valid in enumerate(request) if valid]
    after = [i for i in active if i > last]
    return (after or active or [None])[0]


def assert_selection(actual, reference):
    assert actual.found == (reference is not None)
    if actual.found:
        assert actual.index == reference


@pytest.mark.parametrize('count', [1, 3, 5, 8, 17, 32, 65])
def test_skill_round_robin_hardware(count):
    inst = build_simulator(compiler().resolve('probe.Probe')(count), f'rr_patterns_{count}').instance()
    rng = random.Random(701 + count)
    if count <= 8:
        masks = ([bool(mask & (1 << i)) for i in range(count)] for mask in range(1 << count))
    else:
        masks = [[False] * count, [True] * count]
        masks += [[i == selected for i in range(count)] for selected in range(count)]
        masks += [[bool(rng.randrange(2)) for _ in range(count)] for _ in range(100)]
    for request in masks:
        inst.ports.request = request
        for last in range(count):
            inst.ports.last = last
            inst.step(1)
            reference = expected(request, last)
            assert_selection(inst.ports.linear, reference)
            assert_selection(inst.ports.tree, reference)
            assert_selection(inst.ports.single, reference)


def test_skill_round_robin_compile_time():
    c = compiler()
    for name in ('round_robin_linear', 'round_robin_tree', 'round_robin_single_tree'):
        select = c.resolve('round_robin.' + name)
        for request, last in [([True, True, True], 1), ([True, False, False], 2),
                              ([False, False, False], 0), ([False, True, False], 1)]:
            assert_selection(select(3, request, last), expected(request, last))
