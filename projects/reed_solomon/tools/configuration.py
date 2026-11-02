"""Reed–Solomon elaboration helpers shared by tools and tests."""
from functools import lru_cache
from pathlib import Path
import khdl

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'rtl/khdl.toml'

def module_path(stage):
    name = 'Rs' + ''.join(part.title() for part in stage.split('_'))
    return f'reedsolo_{stage}.{name}'

@lru_cache
def compile_manifest():
    return khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()

@lru_cache
def rs_configuration(**parameters):
    # Type factories execute compile-time algorithms on every call. Share the
    # field tables and type across stage specializations in this compilation.
    return compile_manifest().resolve('reedsolo.rs_config')(**configuration_parameters(**parameters))

def configuration_parameters(**overrides):
    """Supply defaults and declaration order for a partially specified code."""
    return dict(n=240, k=224, m=8, prim=0x11d, alpha=2, fcr=1) | overrides

def decoder_parallelism(compiler, *, unroll=1, unroll_berlekamp=1,
                        unroll_omegas=1, unroll_forney=1, berlekamp_instances=1,
                        forney_instances=1):
    """Construct the elaboration-time decoder plan in its compiler context."""
    return compiler.resolve('reedsolo_decoder.RsDecoderParallelism').new(
        stream_unroll=unroll, berlekamp_unroll=unroll_berlekamp,
        omegas_unroll=unroll_omegas, forney_unroll=unroll_forney,
        berlekamp_instances=berlekamp_instances, forney_instances=forney_instances)

def memory_spec(spec):
    """Validate 'lut', 'block:LATENCY', or 'external:LATENCY'; usable as an argparse type."""
    kind, _, latency = spec.partition(':')
    if (kind == 'lut' and not latency) or (
            kind in ('block', 'external') and latency.isdigit() and int(latency) > 0):
        return spec
    raise ValueError(f"memory must be 'lut', 'block:N', or 'external:N', not {spec!r}")

def fifo_memory(compiler, spec):
    """Construct the RsFifoMemory named by a memory spec."""
    kinds = compiler.resolve('reedsolo_packet_fifo.RsFifoMemory')
    kind, _, latency = memory_spec(spec).partition(':')
    return kinds.Lut if kind == 'lut' else getattr(kinds, kind.title())(int(latency))

def message_buffer(compiler, *, depth, memory='lut', bypass=True):
    """Construct an RsDecoder message buffer in its compiler context."""
    return compiler.resolve('reedsolo_decoder.RsMessageBuffer').new(
        depth=depth, memory=fifo_memory(compiler, memory), bypass=bypass)
