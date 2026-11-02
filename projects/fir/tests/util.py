from functools import lru_cache

from test_support.simulation import build_simulator, tick
from ..tools.compile import compile_source

DEFAULTS = dict(input_w=8, input_f=3, output_w=10, output_f=3,
                coeff_w=7, coeff_f=2, taps=9, parallel=7, ffa_depth=2,
                taps_per_stage=2, pipeline_steps=())


@lru_cache
def compiled(key):
    parameters = dict(key)
    module = compile_source().resolve('fir.Fir')(**parameters)
    return build_simulator(module, 'fir')


def instance(**overrides):
    inst = compiled(tuple((DEFAULTS | overrides).items())).instance()
    p = inst.ports
    p.clk = False
    p.rst = True
    p.input.valid = False
    p.input.samples = [0] * (DEFAULTS | overrides)['parallel']
    p.output.ready = False
    p.coeff_value = 0
    p.coeff_push = p.coeff_clear = False
    inst.step(1)
    tick(inst); tick(inst)
    p.rst = False
    inst.step(1)
    return inst


def edge(inst):
    inst.step(1)
    tick(inst)


def load(inst, coefficients, *, clear=False, gaps=False):
    p = inst.ports
    for i, coefficient in enumerate(coefficients):
        p.coeff_clear = bool(clear and i == 0)
        p.coeff_value = coefficient
        p.coeff_push = True
        edge(inst)
        p.coeff_clear = False
        if gaps and i % 3 == 1:
            p.coeff_push = False
            edge(inst)
    p.coeff_push = p.coeff_clear = False
    inst.step(1)
