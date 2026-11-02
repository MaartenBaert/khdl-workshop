"""Verilator builds and cycle-accurate ready/valid drivers shared by projects."""
import os
import random

# Set by the pytest plugin; builds must use fresh pytest-owned directories.
_build_directory = None


def build_simulator(module, name):
    if _build_directory is None:
        raise RuntimeError('Simulator builds require the pytest session fixture')
    compiler = os.environ.get('KHDL_SIM_COMPILER') or 'clang'
    return module.as_verilated(_build_directory(name), optimization=0, compiler=compiler)


def tick(inst):
    # Clock process: one rising and one falling edge per cycle.
    inst.ports.clk = True
    inst.step(1)
    inst.ports.clk = False
    inst.step(1)

def transfer(inst, inputs, outputs, *, seed=42, stutter=True, stall_denominator=4,
             max_cycles=100000):
    """Sample handshakes before the edge; hold valid and payload until accepted.

    Checks stalled output stability and bounds execution to catch deadlocks.
    Input/output field names are local to their respective interfaces.
    Inputs are dictionaries of payload fields, excluding valid.
    A dictionary-valued payload is constructed through its port's struct type.
    Each independent stutter decision stalls with probability 1/stall_denominator.
    """
    assert stall_denominator >= 2
    # Stutter control: independent input-launch and output-readiness choices.
    rng = random.Random(seed)
    sent = 0
    received = []
    pending = False
    held = None
    for cycle in range(max_cycles):
        # Input process: send a payload and hold it until accepted.
        if not pending and sent < len(inputs) and (not stutter or rng.randrange(stall_denominator)):
            for name, value in inputs[sent].items():
                inst.ports.input[name] = (inst.info_ports.input[name].type.new(**value)
                                          if isinstance(value, dict) else value)
            pending = True
        inst.ports.input.valid = pending
        inst.ports.output.ready = not stutter or bool(rng.randrange(stall_denominator))
        inst.step(1)
        valid = inst.ports.output.valid
        snapshot = {name: inst.ports.output[name] for name in outputs} if valid else None
        if held is not None:
            assert valid and snapshot == held, f'Output changed during stall at cycle {cycle}'
        ready = inst.ports.output.ready
        held = snapshot if valid and not ready else None
        accepted = pending and inst.ports.input.ready
        # Output process: receive the payload at the transfer edge.
        if valid and ready:
            received.append(snapshot)
        tick(inst)
        if accepted:
            sent += 1
            pending = False
        yield sent, received
    raise AssertionError(f'Timeout after {max_cycles} cycles, sent {sent}/{len(inputs)}, received {len(received)}')

def run(inst, inputs, fields, expected_count, **kwargs):
    for sent, received in transfer(inst, inputs, fields, **kwargs):
        assert len(received) <= expected_count, 'Unexpected extra output'
        if sent == len(inputs) and len(received) == expected_count:
            inst.ports.input.valid = False
            return received
