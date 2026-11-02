"""Port of reedsolo_decoder_tb: generated vectors replace the static VHDL tables.

The DUT is driven by the shared clock/input/output/stutter processes in util.py.
"""
import pytest
from .util import instance, stream, run, collect

@pytest.mark.parametrize('width', [1, 4, 16])
@pytest.mark.parametrize('buffered', [False, True])
def test_decoder(rs,vectors,width,buffered):
    inst=instance('decoder',unroll=width,buffered=buffered)
    inputs=[beat for v in vectors for beat in stream(v['corrupted'],width)]
    rows=run(inst,inputs,['data','last','num_errors','failed'],len(vectors)*rs.k//width)
    for i,v in enumerate(vectors):
        part=rows[i*rs.k//width:(i+1)*rs.k//width]
        assert collect(part)==v['message'].tolist()
        assert [r['last'] for r in part]==[False]*(len(part)-1)+[True]
        assert all(r['num_errors']==v['count'] and not r['failed'] for r in part)

@pytest.mark.parametrize('buffered',[False,True])
def test_decoder_detected_failure(rs,vectors,buffered):
    import numpy as np
    # Select a reproducible uncorrectable frame with a root-count mismatch.
    rng=np.random.default_rng(0xCFA38589D7F0453A)
    for _ in range(100):
        word,_,_=rs.add_random_errors(rng,vectors[7]['word'],num_errors=rs.t+1)
        lambdas,count=rs.rs_berlekamp(rs.rs_syndromes(word))
        roots=[p for p in range(rs.n) if rs.gf_poly_eval(lambdas,rs.gf_exp(-p))==0]
        if len(roots)<rs.t and len(roots)!=int(count):break
    else:raise AssertionError('Failed to construct a detected uncorrectable frame')
    inst=instance('decoder',unroll=16,buffered=buffered)
    rows=run(inst,stream(word,16),['data','last','num_errors','failed'],rs.k//16)
    assert collect(rows)==word[2*rs.t:].tolist()
    assert all(r['failed'] and r['num_errors']==len(roots) for r in rows)
    assert [r['last'] for r in rows]==[False]*(len(rows)-1)+[True]
