"""RsPacketFifo benches, ported from axi_fifos' packet FIFO testbenches.

Source: https://github.com/MaartenBaert/axi_fifos

The randomized bench follows axi_fifo_packet_lut_tb: random packet sizes up to
the depth, cancelled dummy packets (cancelled at once or with the next
packet's first beat), commits with the last beat or in a separate cycle, and
independent input/output stutter. Bypass variants must match a cycle-accurate
model of the LUT FIFO in every cycle, which also makes them equivalent to each
other, as in axi_fifo_packet_equiv_tb. Non-bypass variants deliver the same
transactions but take longer, so they are checked on transactions and with
directed timing tests instead.
"""
from functools import lru_cache
from pathlib import Path
import random

import khdl
import pytest

from test_support.simulation import build_simulator, tick
from ..tools.configuration import MANIFEST

FACADE = Path(__file__).parent / 'rtl/packet_fifo.kh'

# (name, memory) where memory is ('lut',), ('block', latency) or
# ('external', input_latency, output_latency) for the behavioral RAM model.
MEMORIES = [
    ('lut', ('lut',)),
    ('block1', ('block', 1)),
    ('block3', ('block', 3)),
    ('external1', ('external', 0, 0)),
    ('external4', ('external', 1, 2)),
]


@lru_cache
def compiler():
    source = khdl.Source.new_from_manifest_path(str(MANIFEST))
    source.add_file_content(['packet_fifo_tests'], str(FACADE), FACADE.read_text())
    return source.compile()


def read_latency(memory):
    """Cycles from a read decision to its data inside the FIFO."""
    if memory[0] == 'lut':
        return 0
    if memory[0] == 'block':
        return memory[1]
    return memory[1] + 1 + memory[2] + 1


@lru_cache
def compiled(memory, depth, bypass, payload='Word'):
    c = compiler()
    T = c.resolve('packet_fifo_tests.' + payload)
    if memory[0] == 'external':
        module = c.resolve('packet_fifo_tests.PacketFifoWithRam')(
            T=T, depth=depth, input_latency=memory[1], output_latency=memory[2], bypass=bypass)
    else:
        kinds = c.resolve('reedsolo_packet_fifo.RsFifoMemory')
        kind = kinds.Lut if memory[0] == 'lut' else kinds.Block(memory[1])
        module = c.resolve('reedsolo_packet_fifo.RsPacketFifo')(
            T=T, depth=depth, memory=kind, bypass=bypass)
    name = 'packet_fifo_' + '_'.join(map(str, memory)) + f'_{depth}_{int(bypass)}_{payload}'
    return build_simulator(module, name)


@pytest.fixture(scope='module', autouse=True)
def clear_cache(simulator_build_dirs):
    try:
        yield
    finally:
        compiled.cache_clear()


def idle(inst):
    p = inst.ports
    p.input.valid = False
    p.cancel = False
    p.commit = False
    p.output.ready = False


def instance(memory, depth, bypass, payload='Word'):
    inst = compiled(memory, depth, bypass, payload).instance()
    idle(inst)
    inst.ports.clk = False
    inst.ports.rst = True
    tick(inst)
    tick(inst)
    inst.ports.rst = False
    inst.step(1)
    return inst


class Model:
    """Cycle-accurate LUT packet FIFO: cancel, push, and commit after a pop."""

    def __init__(self, depth):
        self.depth = depth
        self.entries = []
        self.committed = 0

    @property
    def input_ready(self):
        return len(self.entries) < self.depth

    @property
    def output_valid(self):
        return self.committed > 0

    def step(self, valid, data, cancel, commit, ready):
        push = valid and self.input_ready
        if ready and self.output_valid:
            self.entries.pop(0)
            self.committed -= 1
        if cancel:
            del self.entries[self.committed:]
        if push:
            self.entries.append(data)
        if commit:
            self.committed = len(self.entries)


def input_process(depth, packets, rng, expected, make_data):
    """Yields per-cycle input signals; receives the stutter bit and acceptance."""
    real = random.Random(rng.getrandbits(64))
    dummy = random.Random(rng.getrandbits(64))
    quiet = dict(valid=False, data=None, cancel=False, commit=False)
    delayed_cancel = False
    feedback = yield
    for _ in range(packets):
        size = rng.randint(0, depth)
        is_dummy = rng.randint(0, 3) == 0
        commit_with_last = not is_dummy and dummy.randint(0, 1) == 1
        for j in range(size):
            while not feedback['stutter']:
                feedback = yield quiet
            data = make_data(dummy if is_dummy else real)
            if not is_dummy:
                expected.append(data)
            beat = dict(valid=True, data=data, cancel=delayed_cancel and j == 0,
                        commit=commit_with_last and j == size - 1)
            delayed_cancel = delayed_cancel and j != 0
            # Hold the beat, with its cancel/commit flags, until it transfers.
            feedback = yield beat
            while not feedback['accepted']:
                feedback = yield beat
        if is_dummy:
            if dummy.randint(0, 1) == 0:
                feedback = yield dict(quiet, cancel=True)
            else:
                delayed_cancel = True
        elif not commit_with_last:
            feedback = yield dict(quiet, cancel=delayed_cancel, commit=True)
            delayed_cancel = False
    while True:
        yield None


def run_packets(inst, depth, *, model, packets, seed, make_data):
    rng = random.Random(seed)
    stutter = random.Random(rng.getrandbits(64))
    expected, received = [], []
    source = input_process(depth, packets, rng, expected, make_data)
    next(source)
    feedback = dict(stutter=stutter.getrandbits(1), accepted=False)
    reference = Model(depth) if model else None
    held = None
    p = inst.ports
    for cycle in range(400 * packets + 1000):
        drive = source.send(feedback)
        if drive is None and len(received) == len(expected):
            break
        drive = drive or dict(valid=False, data=None, cancel=False, commit=False)
        p.input.valid = drive['valid']
        if drive['data'] is not None:
            p.input.data = drive['data']
        p.cancel = drive['cancel']
        p.commit = drive['commit']
        p.output.ready = bool(stutter.getrandbits(1))
        inst.step(1)
        ready, valid = p.input.ready, p.output.valid
        data = p.output.data if valid else None
        if held is not None:
            assert valid and data == held, f'output changed during a stall in cycle {cycle}'
        if reference is not None:
            assert ready == reference.input_ready, f'input.ready in cycle {cycle}'
            assert valid == reference.output_valid, f'output.valid in cycle {cycle}'
            if valid:
                assert data == reference.entries[0], f'output.data in cycle {cycle}'
            reference.step(drive['valid'], drive['data'], drive['cancel'], drive['commit'],
                           p.output.ready)
        if valid and p.output.ready:
            received.append(data)
        held = data if valid and not p.output.ready else None
        accepted = drive['valid'] and ready
        tick(inst)
        feedback = dict(stutter=stutter.getrandbits(1), accepted=accepted)
    else:
        raise AssertionError(f'timeout: received {len(received)} of {len(expected)} beats')
    assert received == expected
    return len(expected)


def word(rng):
    return rng.getrandbits(32)


@pytest.mark.parametrize('bypass', [True, False])
@pytest.mark.parametrize('depth', [1, 3, 10])
@pytest.mark.parametrize('name,memory', MEMORIES)
def test_random_packets(name, memory, depth, bypass):
    inst = instance(memory, depth, bypass)
    packets = 1500
    beats = run_packets(inst, depth, model=bypass, packets=packets, seed=depth * 31 + len(name),
                        make_data=word)
    # About three quarters of the packets are real, half the depth on average.
    assert beats > packets * depth // 4


def test_struct_payload():
    memory = ('external', 0, 1)
    inst = instance(memory, 4, True, 'Beat')
    beat = inst.info_ports.input.data.type

    def make(rng):
        return beat.new(symbols=[rng.getrandbits(4) for _ in range(3)], last=bool(rng.getrandbits(1)))

    run_packets(inst, 4, model=True, packets=500, seed=7, make_data=make)


def write_packet(inst, values, *, commit_last=True):
    """Push each value in one cycle, committing with the last beat."""
    p = inst.ports
    for i, value in enumerate(values):
        p.input.valid = True
        p.input.data = value
        p.commit = commit_last and i == len(values) - 1
        inst.step(1)
        assert p.input.ready
        tick(inst)
    p.input.valid = False
    p.commit = False


@pytest.mark.parametrize('name,memory', MEMORIES)
def test_commit_latency(name, memory):
    """Bypass shows committed data in the next cycle; otherwise after the read."""
    for bypass, latency in [(True, 1), (False, read_latency(memory) + 2)]:
        inst = instance(memory, 4, bypass)
        write_packet(inst, [0x1234])
        cycles = 1
        while not inst.ports.output.valid:
            assert cycles < 20
            tick(inst)
            cycles += 1
        assert cycles == latency, f'bypass={bypass}'
        assert inst.ports.output.data == 0x1234


@pytest.mark.parametrize('bypass', [True, False])
@pytest.mark.parametrize('name,memory', MEMORIES)
def test_full_rate(name, memory, bypass):
    """A full FIFO drains one beat per cycle, and streams through at full rate.

    Without bypass, new beats need the read latency to reach the output, so
    the prefill must cover it.
    """
    depth = max(6, read_latency(memory) + 3)
    inst = instance(memory, depth, bypass)
    p = inst.ports
    write_packet(inst, list(range(depth)))
    for _ in range(read_latency(memory) + 2):
        tick(inst)
    if bypass:
        assert not p.input.ready
    p.output.ready = True
    received = []
    value = depth
    for _ in range(40):
        p.input.valid = True
        p.input.data = value
        p.commit = True
        inst.step(1)
        assert p.output.valid, f'gap after {len(received)} beats'
        received.append(p.output.data)
        value += p.input.ready
        tick(inst)
    assert received == list(range(40))


@pytest.mark.parametrize('bypass', [True, False])
@pytest.mark.parametrize('name,memory', MEMORIES)
def test_reset_discards_contents(name, memory, bypass):
    inst = instance(memory, 4, bypass)
    p = inst.ports
    write_packet(inst, [1, 2])
    write_packet(inst, [3], commit_last=False)
    p.rst = True
    tick(inst)
    p.rst = False
    inst.step(1)
    assert p.input.ready and not p.output.valid
    write_packet(inst, [5])
    for _ in range(read_latency(memory) + 2):
        if p.output.valid:
            break
        tick(inst)
    assert p.output.valid and p.output.data == 5


def test_zero_depth_rejected():
    c = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    kinds = c.resolve('reedsolo_packet_fifo.RsFifoMemory')
    with pytest.raises(khdl.DiagnosticException):
        c.resolve('reedsolo_packet_fifo.RsPacketFifo')(T=c.resolve('reedsolo.RsDefault').Sym,
                                                       depth=0, memory=kinds.Lut)
