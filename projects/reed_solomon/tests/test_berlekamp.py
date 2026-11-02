"""Port of reedsolo_berlekamp_tb: generated vectors replace the static VHDL tables.

The DUT is driven by the shared clock/input/output/stutter processes in util.py.
"""
import pytest
from .util import instance, run

# Larger factors create impractical combinational paths; keep the sweep modest.
@pytest.mark.parametrize('unroll', [1, 2, 4])
def test_berlekamp(rs, vectors, unroll):
    inst = instance('berlekamp', unroll=unroll)
    inputs = [dict(data=dict(
        syndromes=v['syndromes'].tolist(),
    )) for v in vectors]
    rows = run(inst, inputs, ['data'], len(inputs))
    for v, row in zip(vectors, rows):
        assert row['data'].syndromes == v['syndromes'].tolist()
        assert row['data'].lambdas == v['lambdas'].tolist()
        assert row['data'].num_errors == v['count']
