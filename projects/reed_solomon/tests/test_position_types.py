"""Position contracts must fail during elaboration rather than simulation."""
import khdl
import pytest
from .util import ROOT


@pytest.mark.parametrize('width', [1, 4, 16])
def test_out_of_range_lane_rejected(width):
    source = khdl.Source.new_from_manifest_path(str(ROOT / 'rtl/khdl.toml'))
    source.add_file_content(['invalid_position'], 'invalid_position.kh', f'''
        import reedsolo_interfaces.[RsTransaction, RsForneyBlock];
        pub module InvalidPosition ports(
            async {{ output: interface RsTransaction(RsForneyBlock({width})).Source }}
        ) {{
            comb {{
                output.data = undef;
                output.data.error_upos = [{width}] * 8;
                output.valid = false;
            }}
        }}
    ''')
    with pytest.raises(khdl.DiagnosticException) as exc:
        source.compile().resolve('invalid_position.InvalidPosition')
    assert 'type mismatch' in exc.value.combined_string
    assert 'error_upos' in exc.value.combined_string


@pytest.mark.parametrize('chien,correction', [(1, 4), (4, 1)])
def test_mismatched_position_widths_rejected(chien, correction):
    # The decoder uses one width throughout. Check the standalone stage
    # connection contract directly, with a fresh context for each failure.
    source = khdl.Source.new_from_manifest_path(str(ROOT / 'rtl/khdl.toml'))
    source.add_file_content(['mismatched_positions'], 'mismatched_positions.kh', f'''
        import reedsolo_interfaces.[RsTransaction, RsForneyBlock, RsStream];
        import reedsolo_correction.[RsCorrection];
        pub module MismatchedPositions ports(
            clk: in clock,
            sync(clk) {{
                rst: in bool,
                input: interface RsTransaction(RsForneyBlock({chien})).Sink,
                output: interface RsStream({correction}, decoded=true).Source,
            }}
        ) {{
            instance RsCorrection(unroll={correction}) ports(
                clk, rst, input, output,
            );
        }}
    ''')
    with pytest.raises(khdl.DiagnosticException) as exc:
        source.compile().resolve('mismatched_positions.MismatchedPositions')
    assert 'interface' in exc.value.combined_string
    assert 'mismatch' in exc.value.combined_string
