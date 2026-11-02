import random

import khdl
import pytest

from test_support.simulation import build_simulator
from ..tools.compile import MANIFEST


PROBE = '''
    import routing.[select_window, enumerate_window];
    pub module Probe(width: uint, choices: positive, stride: positive) ports(
        samples: in async [width + (choices - 1) * stride]int(8),
        selection: in async uint(0..choices),
        barrel: out async [width]int(8),
        enumeration: out async [width]int(8),
    ) {
        comb {
            barrel = select_window(width, choices, stride, selection, samples);
            enumeration = enumerate_window(width, choices, stride, selection, samples);
        }
    }
'''


def compiler():
    source = khdl.Source.new_from_manifest_path(str(MANIFEST))
    source.add_file_content(['routing_probe'], 'routing_probe.kh', PROBE)
    return source.compile()


@pytest.mark.parametrize('width,choices,stride',
                         [(1, 1, 1), (7, 2, 2), (17, 4, 1), (11, 8, 3), (5, 16, 1)])
def test_window_connections(width, choices, stride):
    inst = build_simulator(compiler().resolve('routing_probe.Probe')(width, choices, stride),
                           'fir_routing').instance()
    rng = random.Random(951 + width + choices)
    for _ in range(20):
        samples = [rng.randrange(-128, 128) for _ in range(width + (choices-1)*stride)]
        inst.ports.samples = samples
        for selected in range(choices):
            inst.ports.selection = selected
            inst.step(1)
            expected = samples[selected*stride:selected*stride+width]
            assert inst.ports.barrel == expected
            assert inst.ports.enumeration == expected


def test_empty_history_window():
    c = compiler()
    for function in ('select_window', 'enumerate_window'):
        select = c.resolve('routing.' + function)
        for choice in range(4):
            assert list(select(0, 4, 1, choice, [1, 2, 3])) == []
