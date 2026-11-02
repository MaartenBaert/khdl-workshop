"""Field/table elaboration and complete pipelines beyond the original RS(240,224)."""
import khdl
import numpy as np
import pytest

from .reed_solomon import ReedSolomon
from ..tools.configuration import configuration_parameters
from .util import ROOT, collect, compiled_module, instance, module_path, reference_compiler, run, stream

# Include shortened words, odd t, t=1, nonbinary unroll factors, an alpha
# other than 2, both fcr values, and symbols wider than the old VHDL byte.
PROFILES = [
    pytest.param(dict(n=3, k=1, m=2, prim=0x7, alpha=2, fcr=0),
                 dict(width=1, berlekamp=2, omegas=1, forney=2), id='gf4-t1-fcr0'),
    pytest.param(dict(n=7, k=1, m=3, prim=0xb, alpha=2, fcr=1),
                 dict(width=1, berlekamp=3, omegas=3, forney=6), id='gf8-single-message'),
    pytest.param(dict(n=15, k=13, m=4, prim=0x13, alpha=2, fcr=1),
                 dict(width=1, berlekamp=1, omegas=1, forney=1), id='gf16-t1-fcr1'),
    pytest.param(dict(n=12, k=6, m=4, prim=0x13, alpha=3, fcr=0),
                 dict(width=3, berlekamp=3, omegas=3, forney=4), id='gf16-shortened-alpha3'),
    pytest.param(dict(n=30, k=20, m=5, prim=0x25, alpha=2, fcr=1),
                 dict(width=5, berlekamp=2, omegas=5, forney=7), id='gf32-five-lanes'),
    pytest.param(dict(n=60, k=48, m=8, prim=0x11b, alpha=3, fcr=0),
                 dict(width=6, berlekamp=4, omegas=3, forney=12), id='gf256-aes-polynomial'),
    pytest.param(dict(n=510, k=500, m=9, prim=0x211, alpha=2, fcr=1),
                 dict(width=10, berlekamp=2, omegas=1, forney=3), id='gf512-ten-lanes'),
]


@pytest.mark.parametrize('config,widths', PROFILES)
def test_generated_tables(config, widths):
    rs = ReedSolomon(**config)
    c = reference_compiler()
    cfg = c.resolve('reedsolo.rs_config')(**config)
    exp, inv, generator = c.resolve('reference_checks.tables')(cfg)
    assert exp == rs.lut_exp.tolist()
    assert inv == rs.lut_inv.tolist()
    assert generator == rs.gen[:-1].tolist()
    mul = c.resolve('reference_checks.multiply')
    for a in [0, 1, rs.alpha, rs.q // 2, rs.q - 1]:
        for b in [0, 1, rs.alpha, rs.q // 2, rs.q - 1]:
            assert mul(cfg, a, b) == rs.gf_mul_nolut(a, b)


@pytest.mark.parametrize('config,widths', PROFILES[:6])
def test_field_hardware(config, widths):
    rs = ReedSolomon(**config)
    inst = compiled_module('gf_mul', **config).instance()
    p = inst.ports
    p.long_value = 0
    for a in range(rs.q):
        p.a = a
        for b in range(rs.q):
            p.b = b
            inst.step(1)
            assert p.product == rs.gf_mul_nolut(a, b)
    for raw in range(2 ** (2 * rs.m - 1)):
        p.long_value = raw
        inst.step(1)
        expected = raw
        for bit in reversed(range(rs.m, 2 * rs.m - 1)):
            if expected & (1 << bit):
                expected ^= rs.prim << (bit - rs.m)
        assert p.reduced == expected


@pytest.mark.parametrize('config,widths', PROFILES)
@pytest.mark.parametrize('buffered', [False, True])
def test_parametric_pipeline(config, widths, buffered):
    rs = ReedSolomon(**config)
    width = widths['width']
    rng = np.random.default_rng(0x8ecb13)
    messages = [rng.integers(0, rs.q, rs.k, dtype=rs.dtype) for _ in range(rs.t + 3)]
    messages[0][:] = 0
    messages[1][:] = rs.q - 1
    # The unchanged reference's encode() self-check hardcodes unroll=4.
    # Use its scalar parity routine for configurations that need other widths.
    words = [np.concatenate((rs.rs_parity(message), message)) for message in messages]
    encoder = instance('encoder', **config, unroll=width, buffered=buffered)
    rows = run(encoder, [beat for msg in messages for beat in stream(msg, width)],
               ['data', 'last'], len(messages) * rs.n // width)
    for i, word in enumerate(words):
        frame = rows[i * rs.n // width:(i + 1) * rs.n // width]
        assert collect(frame) == word.tolist()
        assert [row['last'] for row in frame] == [False] * (len(frame) - 1) + [True]

    # Check every correctable error count, including roots in both parity and
    # message, on both ends and across lanes. Magnitudes never equal zero.
    counts = [i % (rs.t + 1) for i in range(len(messages))]
    corrupted = []
    for word, count in zip(words, counts):
        damaged = word.copy()
        positions = rng.choice(rs.n, count, replace=False)
        damaged[positions] ^= rng.integers(1, rs.q, count, dtype=rs.dtype)
        if count == rs.t:
            damaged = word.copy()
            positions = np.linspace(0, rs.n - 1, count, dtype=int)
            for pos in positions:
                damaged[pos] ^= rs.q - 1
        corrupted.append(damaged)
    decoder = instance('decoder', **config, buffered=buffered,
                       unroll=width,
                       unroll_berlekamp=widths['berlekamp'], unroll_omegas=widths['omegas'],
                       unroll_forney=widths['forney'])
    rows = run(decoder, [beat for word in corrupted for beat in stream(word, width)],
               ['data', 'last', 'num_errors', 'failed'],
               len(messages) * rs.k // width)
    for i, message in enumerate(messages):
        frame = rows[i * rs.k // width:(i + 1) * rs.k // width]
        assert collect(frame) == message.tolist()
        assert [row['last'] for row in frame] == [False] * (len(frame) - 1) + [True]
        assert all(row['num_errors'] == counts[i] and not row['failed']
                   for row in frame)


@pytest.mark.parametrize('config,reason', [
    (dict(n=256), 'smaller than the field size'),
    (dict(k=240), 'smaller than n'),
    (dict(k=223), 'even number of parity'),
    (dict(fcr=2), 'fcr must be'),
    (dict(prim=0x1d), 'monic polynomial'),
    (dict(prim=0x21d), 'monic polynomial'),
    (dict(alpha=256), 'nonzero field element'),
    (dict(alpha=1), 'generate every nonzero'),
    (dict(prim=0x11b, alpha=2), 'generate every nonzero'),
])
def test_invalid_configuration(config, reason):
    # Each failed resolution poisons that compiler context at this revision.
    c = khdl.Source.new_from_manifest_path(str(ROOT / 'rtl/khdl.toml')).compile()
    with pytest.raises(khdl.DiagnosticException) as exc:
        c.resolve('reedsolo.rs_config')(**configuration_parameters(**config))
    assert reason in exc.value.combined_string


@pytest.mark.parametrize('stage,generics', [
    ('berlekamp', dict(unroll=3)),
    ('chien', dict(unroll_omegas=3)),
    ('forney', dict(unroll=17)),
])
def test_invalid_unroll(stage, generics):
    c = khdl.Source.new_from_manifest_path(str(ROOT / 'rtl/khdl.toml')).compile()
    with pytest.raises(khdl.DiagnosticException) as exc:
        c.resolve(module_path(stage))(**generics)
    assert 'assert' in exc.value.combined_string


@pytest.mark.parametrize('path,generics', [
    *[("reedsolo.rs_config", {name: 0}) for name in ('n', 'k', 'm', 'prim', 'alpha')],
    *[(module_path(stage), {name: 0}) for stage, name in (
        ('encoder', 'unroll'), ('syndromes', 'unroll'),
        ('berlekamp', 'unroll'), ('chien', 'unroll_omegas'),
        ('chien', 'unroll_chien'), ('forney', 'unroll'),
        ('forney', 'unroll_chien'), ('correction', 'unroll'),
    )],
])
def test_zero_parameter_rejected(path, generics):
    # Reject zero through the positive parameter type before body elaboration.
    c = khdl.Source.new_from_manifest_path(str(ROOT / 'rtl/khdl.toml')).compile()
    with pytest.raises(khdl.DiagnosticException) as exc:
        if path == 'reedsolo.rs_config':
            generics = configuration_parameters(**generics)
        elif 'unroll_chien' in generics:
            first = 'unroll_omegas' if path == module_path('chien') else 'unroll'
            generics = {first: 1} | generics
        c.resolve(path)(**generics)
    assert 'type mismatch' in exc.value.combined_string


@pytest.mark.parametrize('field', ['stream_unroll', 'berlekamp_unroll', 'omegas_unroll', 'forney_unroll', 'berlekamp_instances', 'forney_instances'])
@pytest.mark.parametrize('value', [0, -1])
def test_decoder_parallelism_positive_fields(field, value):
    c = khdl.Source.new_from_manifest_path(str(ROOT / 'rtl/khdl.toml')).compile()
    fields = dict(stream_unroll=1, berlekamp_unroll=1, omegas_unroll=1, forney_unroll=1, berlekamp_instances=1, forney_instances=1)
    fields[field] = value
    with pytest.raises(khdl.DiagnosticException) as exc:
        c.resolve('reedsolo_decoder.RsDecoderParallelism').new(**fields)
    assert 'type mismatch' in exc.value.combined_string


@pytest.mark.parametrize('field,value', [('berlekamp_unroll', 3), ('omegas_unroll', 3), ('forney_unroll', 17)])
def test_decoder_parallelism_stage_constraints(field, value):
    c = khdl.Source.new_from_manifest_path(str(ROOT / 'rtl/khdl.toml')).compile()
    fields = dict(stream_unroll=1, berlekamp_unroll=1, omegas_unroll=1, forney_unroll=1, berlekamp_instances=1, forney_instances=1)
    fields[field] = value
    parallelism = c.resolve('reedsolo_decoder.RsDecoderParallelism').new(**fields)
    with pytest.raises(khdl.DiagnosticException) as exc:
        c.resolve(module_path('decoder'))(parallelism=parallelism)
    assert 'assert' in exc.value.combined_string


def test_decoder_parallelism_defaults():
    c = reference_compiler()
    parallelism = c.resolve('reedsolo_decoder.RsDecoderParallelism').new(
        stream_unroll=1, berlekamp_unroll=1, omegas_unroll=1, forney_unroll=1, berlekamp_instances=1, forney_instances=1)
    decoder = c.resolve(module_path('decoder'))
    assert decoder().as_verilog().source == decoder(parallelism=parallelism).as_verilog().source


@pytest.mark.parametrize('stage,generics', [
    ('encoder', dict(unroll=17)),
    ('syndromes', dict(unroll=7)),
    ('chien', dict(unroll_omegas=1, unroll_chien=7)),
    ('forney', dict(unroll=1, unroll_chien=7)),
    ('correction', dict(unroll=3)),
])
def test_partial_stream_unroll(stage, generics):
    # These previously failed divisibility checks. Arithmetic unroll
    # restrictions remain independent of the streaming/search layout.
    c = reference_compiler()
    c.resolve(module_path(stage))(**generics)


def test_multiple_configurations_in_one_design():
    source = khdl.Source.new_from_manifest_path(str(ROOT / 'rtl/khdl.toml'))
    source.add_file_content(['multi_config'], 'multi_config.kh', '''
        import reedsolo.[rs_config];
        import reedsolo_encoder.[RsEncoder];
        import reedsolo_interfaces.[RsStream];
        type Small = rs_config(n=12, k=6, m=4, prim=19, alpha=3, fcr=0);
        type Large = rs_config(n=30, k=20, m=5, prim=37, alpha=2, fcr=1);
        pub module MultiConfig ports(
            clk: in clock,
            sync(clk) {
                rst: in bool,
                small_input: interface RsStream(3, decoded=false, RS=Small).Sink,
                small_output: interface RsStream(3, decoded=false, RS=Small).Source,
                large_input: interface RsStream(5, decoded=false, RS=Large).Sink,
                large_output: interface RsStream(5, decoded=false, RS=Large).Source,
            }
        ) {
            instance RsEncoder(unroll=3, buffered=false, RS=Small) ports(
                clk, rst, input=small_input, output=small_output,
            );
            instance RsEncoder(unroll=5, buffered=false, RS=Large) ports(
                clk, rst, input=large_input, output=large_output,
            );
        }
    ''')
    verilog = source.compile().resolve('multi_config.MultiConfig').as_verilog().source
    # Both field/lane combinations survive elaboration into distinct port widths.
    assert '[11:0] small_input_data' in verilog
    assert '[24:0] large_input_data' in verilog


@pytest.mark.parametrize('boundary', ['stream', 'block'])
def test_mismatched_field_configuration_rejected(boundary):
    source = khdl.Source.new_from_manifest_path(str(ROOT / 'rtl/khdl.toml'))
    source.add_file_content(['mixed_config'], 'mixed_config.kh', '''
        import reedsolo.[rs_config];
        import reedsolo_encoder.[RsEncoder];
        import reedsolo_syndromes.[RsSyndromes];
        import reedsolo_interfaces.[RsStream, RsTransaction, RsSyndromesBlock];
        type A = rs_config(n=12, k=6, m=4, prim=19, alpha=2, fcr=0);
        type B = rs_config(n=12, k=6, m=4, prim=19, alpha=3, fcr=0);
        pub module MixedConfig ports(
            clk: in clock,
            sync(clk) {
                rst: in bool,
                input: interface RsStream(3, decoded=false, RS=A).Sink,
                output: interface RsTransaction(RsSyndromesBlock(RS=B)).Source,
            }
        ) {
            wire link: interface RsStream(3, decoded=false, RS=A);
            instance RsEncoder(unroll=3, buffered=false, RS=A) ports(clk, rst, input, output=link);
            instance RsSyndromes(unroll=3, buffered=false, RS=StageConfig) ports(clk, rst, input=link, output);
        }
    '''.replace('StageConfig', 'A' if boundary == 'block' else 'B'))
    with pytest.raises(khdl.DiagnosticException) as exc:
        source.compile().resolve('mixed_config.MixedConfig')
    assert 'interface' in exc.value.combined_string
    assert 'mismatch' in exc.value.combined_string
