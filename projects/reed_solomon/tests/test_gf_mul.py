"""Port of reedsolo_gf_mul_tb: generated vectors replace the static VHDL tables.

The DUT is driven by the shared clock/input/output/stutter processes in util.py.
"""
from .util import compiled_module, reference_compiler

def test_gf_mul(rs):
    inst = compiled_module('gf_mul').instance()
    p = inst.ports
    p.long_value = 0
    # Exhaustive hardware multiplication: all 65,536 operand pairs.
    for a in range(rs.q):
        p.a = a
        for b in range(rs.q):
            p.b = b
            inst.step(1)
            assert p.product == rs.gf_mul_nolut(a, b)
            raw = 0
            for i in range(rs.m):
                if (b >> i) & 1:
                    raw ^= a << i
            assert p.long_product == raw
    # Also exercise every possible unreduced polynomial, not just products.
    for raw in range(2 ** (2*rs.m-1)):
        p.long_value = raw
        inst.step(1)
        expected = raw
        for bit in reversed(range(rs.m, 2*rs.m-1)):
            if (expected >> bit) & 1:
                expected ^= rs.prim << (bit-rs.m)
        assert p.reduced == expected
    # Check compile-time evaluation against the same reference at edge values.
    c = reference_compiler()
    config = c.resolve('reedsolo.RsDefault')
    mul = c.resolve('reference_checks.multiply')
    for a in [0, 1, 2, 128, 255]:
        for b in [0, 1, 2, 128, 255]:
            assert mul(config, a, b) == rs.gf_mul_nolut(a, b)

def test_generated_default_tables(rs):
    c = reference_compiler()
    config = c.resolve('reedsolo.RsDefault')
    exp, inv, generator = c.resolve('reference_checks.tables')(config)
    assert exp == rs.lut_exp.tolist()
    assert inv == rs.lut_inv.tolist()
    assert generator == rs.gen[:-1].tolist()
