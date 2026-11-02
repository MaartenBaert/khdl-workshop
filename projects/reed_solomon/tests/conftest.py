import pytest
import numpy as np
from .reed_solomon import ReedSolomon

@pytest.fixture(scope='session', autouse=True)
def clear_simulator_cache(simulator_build_dirs):
    from . import util

    try:
        yield
    finally:
        util.compiled_module.cache_clear()


@pytest.fixture(scope='session')
def rs():
    return ReedSolomon(n=240, k=224, m=8, prim=285, alpha=2, fcr=1)

@pytest.fixture(scope='session')
def vectors(rs):
    # Test vectors: generate random messages, encode, then introduce random errors.
    # These replace each VHDL bench's begin/end section test vectors.
    rng = np.random.default_rng(0x25BBA36C40FA404C)
    result = []
    for trial in range(20):
        message = rng.integers(0, rs.q, rs.k, dtype=rs.dtype)
        if trial == 0:
            message[:] = 0
        elif trial == 1:
            message[:] = rs.q - 1
        word = rs.encode(message)
        corrupted, positions, values = rs.add_random_errors(rng, word, num_errors=trial % (rs.t + 1))
        syndromes = rs.rs_syndromes(corrupted)
        lambdas, count = rs.rs_berlekamp(syndromes)
        omegas = rs.rs_omegas(syndromes, lambdas)
        result.append(dict(message=message, word=word, corrupted=corrupted,
                           positions=positions, values=values, syndromes=syndromes,
                           lambdas=lambdas, count=int(count), omegas=omegas))
    return result
