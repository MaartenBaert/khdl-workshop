"""Port of reedsolo_correction_tb: generated vectors replace the static VHDL tables.

The block and message inputs and the output stall independently (streams.py).
"""
import pytest
from .streams import run_streams
from .util import instance, collect, stream

@pytest.mark.parametrize('unroll', [1, 4, 16])
@pytest.mark.parametrize('failed', [False, True])
def test_correction(rs, vectors, unroll, failed):
    inst=instance('correction',unroll=unroll)
    inputs=[]
    message=[]
    for v in vectors:
        # Message beats arrive as RsSyndromes forwards them: whole codeword
        # beats, including parity lanes in the mixed beat.
        message += [b['data'] for b in stream(v['corrupted'], unroll)][:rs.k // unroll]
        pos,xinv=rs.rs_chien_bb(v['lambdas'],v['count'],unroll)
        values=rs.rs_forney(v['count'],v['lambdas'],v['omegas'],pos,xinv)
        inputs.append(dict(data=dict(
            error_cpos=[int(p) // unroll for p in pos] + [0] * (rs.t - len(pos)),
            error_upos=[int(p) % unroll for p in pos] + [0] * (rs.t - len(pos)),
            error_val=values.tolist() + [0] * (rs.t - len(pos)),
            num_errors=v['count'],
            failed=failed,
        )))
    rows=run_streams(inst,{'input':inputs,'message':[dict(data=d) for d in message]},
                     {'output':(['data','last','num_errors','failed'],len(inputs)*rs.k//unroll)})['output']
    for i,v in enumerate(vectors):
        part=rows[i*rs.k//unroll:(i+1)*rs.k//unroll]
        assert collect(part) == (v['corrupted'][2*rs.t:] if failed else v['message']).tolist()
        assert [r['last'] for r in part] == [False]*(len(part)-1)+[True]
        assert all(r['num_errors']==v['count'] and r['failed']==failed for r in part)
