"""Reed–Solomon specialization, reset, and symbol-stream test helpers."""
from functools import lru_cache
import khdl

from test_support.simulation import build_simulator, tick, transfer, run
from ..tools.configuration import (
    ROOT, MANIFEST, compile_manifest, decoder_parallelism, message_buffer, module_path,
    rs_configuration,
)

@lru_cache
def reference_compiler():
    """Expose configuration members for compile-time reference checks."""
    source = khdl.Source.new_from_manifest_path(str(MANIFEST))
    source.add_file_content(['reference_checks'], 'reference_checks.kh', '''
        pub fn tables(RS: type) -> Tuple([RS.q]RS.Sym,
                                         [RS.q]RS.Sym, [2 * RS.t]RS.Sym) {
            return (RS.lut_exp, RS.lut_inv, RS.generator);
        }
        pub fn multiply(RS: type, a: RS.Sym, b: RS.Sym) -> RS.Sym {
            return RS.gf_mul(a, b);
        }
    ''')
    return source.compile()

@lru_cache
def compiled_module(stage, **generics):
    # Scalar configuration arguments remain hashable for the simulator cache.
    config = {key: generics.pop(key) for key in tuple(generics)
              if key in ('n', 'k', 'm', 'prim', 'alpha', 'fcr')}
    module = compile_manifest().resolve(module_path(stage))
    if not isinstance(module, khdl.Module):
        parameters = dict(generics)
        if stage == 'decoder':
            buffered = parameters.pop('buffered', False)
            buffer = {key[len('message_'):]: parameters.pop(key) for key in tuple(parameters)
                      if key.startswith('message_')}
            parameters = dict(parallelism=decoder_parallelism(compile_manifest(), **parameters),
                              buffered=buffered, RS=rs_configuration(**config))
            if buffer:
                parameters['message_buffer'] = message_buffer(compile_manifest(), **buffer)
        else:
            # KHDL matches arguments in declaration order, including defaults
            # before RS. Keep the caller's scalar-keyed specialization API.
            order = {
                'encoder': ('unroll', 'buffered'),
                'syndromes': ('unroll', 'buffered'),
                'berlekamp': ('unroll',),
                'chien': ('unroll_omegas', 'unroll_chien'),
                'forney': ('unroll', 'unroll_chien'),
                'correction': ('unroll',),
                'gf_mul': (),
            }[stage]
            parameters = {key: parameters.pop(key, False if key == 'buffered' else 1)
                          for key in order} | parameters
            parameters['RS'] = rs_configuration(**config)
        module = module(**parameters)
    name = stage + ''.join(f'_{key}_{value.replace(":", "")}' if isinstance(value, str) else f'_{key}_{int(value)}'
                           for key, value in (config | generics).items())
    return build_simulator(module, name)

def instance(stage, **generics):
    inst = compiled_module(stage, **generics).instance()
    for name in ('valid', 'last'):
        if name in inst.ports.input:
            inst.ports.input[name] = False
    inst.ports.output.ready = False
    # The message stream of RsSyndromes (output) or RsCorrection (input).
    if 'message' in inst.ports:
        if inst.info_ports.message.valid.direction == 'input':
            inst.ports.message.valid = False
        else:
            inst.ports.message.ready = False
    inst.ports.clk = False
    inst.ports.rst = True
    tick(inst)
    tick(inst)
    inst.ports.rst = False
    inst.step(1)
    return inst

def message_side(inst, beat=None):
    """Keep a stage's message stream out of the way: RsSyndromes' output is
    always drained, and RsCorrection's input always offers a constant beat."""
    if 'message' in inst.ports:
        if inst.info_ports.message.valid.direction == 'input':
            inst.ports.message.valid = True
            inst.ports.message.data = beat if beat is not None else [1] * len(inst.ports.message.data)
        else:
            inst.ports.message.ready = True
    return inst

def stream(values, unroll):
    """Highest group first, ascending coefficients per beat, low-lane padding."""
    beats = []
    for end in range(len(values), 0, -unroll):
        data = [int(value) for value in values[max(0, end - unroll):end]]
        beats.append(dict(data=[0] * (unroll - len(data)) + data,
                          last=end <= unroll))
    return beats

def collect(rows):
    return [value for row in reversed(rows) for value in row['data']]
