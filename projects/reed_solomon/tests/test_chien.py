"""Port of reedsolo_chien_tb: generated vectors replace the static VHDL tables.

The DUT is driven by the shared clock/input/output/stutter processes in util.py.
"""
import pytest
from .util import instance, run

@pytest.mark.parametrize('omegas,chien', [(1,1),(2,16),(1,4),(8,8)])
def test_chien(rs, vectors, omegas, chien):
    inst = instance('chien', unroll_omegas=omegas, unroll_chien=chien)
    inputs = [dict(data=dict(
        syndromes=v['syndromes'].tolist(),
        lambdas=v['lambdas'].tolist(),
        num_errors=v['count'],
    )) for v in vectors]
    fields = ['data']
    rows = run(inst, inputs, fields, len(inputs))
    for v, row in zip(vectors, rows):
        pos, xinv = rs.rs_chien_bb(v['lambdas'], v['count'], chien)
        # Unused slots carry an out-of-range cycle, with zero lane/inverse.
        assert row['data'].error_cpos == [int(p)//chien for p in pos]+[(rs.n+chien-1)//chien]*(rs.t-len(pos))
        assert row['data'].error_upos == [int(p)%chien for p in pos]+[0]*(rs.t-len(pos))
        assert row['data'].error_xinv == xinv.tolist()+[0]*(rs.t-len(pos))
        assert row['data'].omegas == v['omegas'].tolist()
        assert row['data'].lambdas == v['lambdas'].tolist()
        assert row['data'].num_errors == v['count']
        assert not row['data'].failed

def test_chien_failed_root_count(rs):
    inst=instance('chien',unroll_omegas=2,unroll_chien=16)
    inp=dict(data=dict(
        syndromes=[0] * (2 * rs.t),
        lambdas=[1] + [0] * rs.t,
        num_errors=1,
    ))
    rows=run(inst,[inp],['data'],1)
    assert rows[0]['data'].num_errors == 0
    assert rows[0]['data'].failed
