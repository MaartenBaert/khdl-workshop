"""Port of reedsolo_forney_tb: generated vectors replace the static VHDL tables.

The DUT is driven by the shared clock/input/output/stutter processes in util.py.
"""
import pytest
import numpy as np
from .util import instance, run

@pytest.mark.parametrize('unroll', [1, 2, 4, 16])
def test_forney(rs, vectors, unroll):
    inst = instance('forney', unroll=unroll, unroll_chien=16)
    inputs=[]; expected=[]
    for v in vectors:
        pos, xinv = rs.rs_chien_bb(v['lambdas'], v['count'], 16)
        padded_pos = np.pad(pos,(0,rs.t-len(pos)))
        padded_xinv = np.pad(xinv,(0,rs.t-len(xinv)))
        values = rs.rs_forney(rs.t,v['lambdas'],v['omegas'],padded_pos,padded_xinv)
        inputs.append(dict(data=dict(
            omegas=v['omegas'].tolist(),
            lambdas=v['lambdas'].tolist(),
            error_cpos=(padded_pos // 16).tolist(),
            error_upos=(padded_pos % 16).tolist(),
            error_xinv=padded_xinv.tolist(),
            num_errors=v['count'],
            failed=False,
        )))
        expected.append(values.tolist())
    rows=run(inst,inputs,['data'],len(inputs))
    for v, inp, vals, row in zip(vectors,inputs,expected,rows):
        assert row['data'].error_val == vals
        assert row['data'].error_cpos == inp['data']['error_cpos']
        assert row['data'].error_upos == inp['data']['error_upos']
        assert row['data'].num_errors == v['count']
        assert not row['data'].failed

def test_forney_failure_flags(rs):
    inst=instance('forney',unroll=16,unroll_chien=16)
    # Zero formal derivative: inverse(0)=0, and an active error must set failed.
    inp=dict(data=dict(
        omegas=[0] * rs.t,
        lambdas=[0] * (rs.t + 1),
        error_cpos=[0] * rs.t,
        error_upos=[0] * rs.t,
        error_xinv=[1] * rs.t,
        num_errors=1,
        failed=False,
    ))
    passed_failure = dict(data=dict(inp['data'], num_errors=0, failed=True))
    rows=run(inst,[inp,passed_failure],['data'],2)
    assert all(row['data'].failed and row['data'].error_val == [0] * rs.t
               for row in rows)
