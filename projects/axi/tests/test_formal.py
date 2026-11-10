"""Formal checks: each target passes and shows reachable progress, each
negative control fails and names the violated rule, the pipeline has an
unbounded proof, and KHDL runtime assertions reach the proof in both engines."""
import shutil

import khdl
import pytest

from ..tools.formal import (TARGETS, ProofFailed, check, default_engine, module_ports, prove,
                            top_module)

needs_yosys = pytest.mark.skipif(shutil.which('yosys') is None,
                                 reason='Yosys is required for formal checks')
needs_abc = pytest.mark.skipif(shutil.which('yosys') is None or default_engine() != 'abc',
                               reason='ABC and yosys-witness are required for unbounded proofs')
ENGINES = ['sat'] + (['abc'] if shutil.which('yosys') and default_engine() == 'abc' else [])

# Proof depths kept short for test runtime, per default engine; the
# reachability traces still run at their own minimum depths (REACH_DEPTHS).
DEPTHS = {
    'abc': {'pipeline': 10, 'error': 8, 'manager': 4},
    'sat': {'pipeline': 6, 'error': 5, 'manager': 4},
}
# The rule each negative control must violate, as named in the failure.
FAULT_RULES = {'pipeline': 'aw_order_ok', 'error': 'rlast_ok', 'manager': 'ar_legal_ok'}


def depth(target):
    return DEPTHS[default_engine()][target]


@needs_yosys
@pytest.mark.parametrize('target', TARGETS)
def test_bounded_checks_pass_with_reachable_progress(tmp_path, target):
    check(tmp_path, depth(target), target=target)
    if target != 'pipeline':
        assert (tmp_path / 'reachable.vcd').is_file()


@needs_yosys
@pytest.mark.parametrize('target', TARGETS)
def test_negative_control_fails(tmp_path, target):
    with pytest.raises(ProofFailed) as failure:
        check(tmp_path, depth(target), target=target, inject_fault=True)
    if default_engine() == 'abc':
        assert FAULT_RULES[target] in str(failure.value)
    assert (tmp_path / 'check.vcd').is_file()


# AXI3 and AXI4-Lite through axi_protocol_view (error and manager targets).
PROTOCOL_CASES = [(t, p) for t in ('error', 'manager') for p in ('axi3', 'lite')]


@needs_yosys
@pytest.mark.parametrize('target,protocol', PROTOCOL_CASES)
def test_protocol_bounded_checks_pass_with_reachable_progress(tmp_path, target, protocol):
    check(tmp_path, depth(target), target=target, protocol=protocol)
    assert (tmp_path / 'reachable.vcd').is_file()


@needs_yosys
@pytest.mark.parametrize('target,protocol,fault,rule', [
    *[(t, p, True, FAULT_RULES[t]) for t, p in PROTOCOL_CASES],
    ('manager', 'axi3', 'wid', 'wid_ok'),
])
def test_protocol_negative_controls_fail(tmp_path, target, protocol, fault, rule):
    with pytest.raises(ProofFailed) as failure:
        check(tmp_path, depth(target), target=target, protocol=protocol, inject_fault=fault)
    if default_engine() == 'abc':
        assert rule in str(failure.value)
    assert (tmp_path / 'check.vcd').is_file()


@needs_abc
@pytest.mark.parametrize('inject_fault', [False, True])
def test_pipeline_unbounded_proof(tmp_path, inject_fault):
    if inject_fault:
        with pytest.raises(ProofFailed, match=FAULT_RULES['pipeline']):
            check(tmp_path, target='pipeline', inject_fault=True, unbounded=True)
    else:
        check(tmp_path, target='pipeline', unbounded=True)


# The counter saturates at 7 within a wider range, so the compiler cannot fold
# `count < limit` away for a passing limit; prove() counts the surviving path.
PROBE = '''
pub module Probe(limit: uint) ports(
    clk: in clock, rst: in async bool,
    sync(clk, async rst) { enable: in bool, count: out uint(0..16) }
) {
    clocked(clk, async rst) {
        reg wire count = 0;
        assert(count < limit, "count reached its limit");
        if (enable && count < 7) { count = count + 1; }
    }
}
'''


@needs_yosys
@pytest.mark.parametrize('engine', ENGINES)
@pytest.mark.parametrize('limit,fails', [(8, False), (3, True)])
def test_khdl_assertions_reach_the_proof(tmp_path, engine, limit, fails):
    source = khdl.Source()
    source.add_file_content(['probe'], 'probe.kh', PROBE)
    verilog = source.compile().resolve('probe.Probe')(limit).as_verilog()
    harness = top_module(verilog.module_name, module_ports(verilog.source, verilog.module_name), '')
    if fails:
        with pytest.raises(ProofFailed):
            prove(tmp_path, verilog.source, verilog.module_name, harness, depth=5, engine=engine)
        assert (tmp_path / 'check.vcd').is_file()
    else:
        assert prove(tmp_path, verilog.source, verilog.module_name, harness, depth=5,
                     engine=engine) == 1
