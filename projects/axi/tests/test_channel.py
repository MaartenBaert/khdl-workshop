"""FIFO contracts and latency-independent transaction-level compositions."""
from pathlib import Path
import ctypes
import random

import khdl
import pytest

from test_support.simulation import build_simulator, run, tick

MANIFEST = Path(__file__).resolve().parents[1] / 'rtl/khdl.toml'
PROFILES = [(1, False, 1), (3, False, 1), (3, True, 1), (0, False, 2), (1, False, 2), (3, False, 2), (3, True, 2), (2, True, 3)]
MISUSE = [
    ('tx.push(1); tx.push(2);', 'Transmitter.push()', '', ''),
    ('val _ = rx.pop();', 'Receiver.peek()', '', ''),
    ('val _ = rx.peek();', 'Receiver.peek()', '', ''),
    ('queue.push(1); queue.push(2);', 'Fifo.push()', '', ''),
    ('val _ = queue.pop();', 'Fifo.peek()', '', ''),
    ('val _ = queue.peek();', 'Fifo.peek()', '', ''),
    ('tx.begin();', 'Transmitter.begin()', '', ''),
    ('rx.begin();', 'Receiver.begin()', '', ''),
    ('queue.begin();', 'Fifo.begin()', '', ''),
    ('tx.end();', 'Transmitter.end()', '', ''),
    ('val _ = tx.can_push();', 'Transmitter.can_push()', 'tx.begin();', 'tx.end();'),
    ('', 'Fifo.begin()', '', 'queue.end();'),
]

# Exercise storage misuse with both implementations, without repeating the
# independent endpoint cases.
MISUSE += [tuple(part.replace('Fifo', 'Internal') for part in MISUSE[i])
           for i in (3, 4, 5, 8, 11)]


def source():
    return khdl.Source.new_from_manifest_path(str(MANIFEST))


@pytest.fixture(scope='session')
def new_channel(simulator_build_dirs):
    s = source()
    s.add_file_content(['test_types'], 'test_types.kh', '''
        type Flag = bool; type Byte = uint(8);
        type Words = [3]uint(4); type Singleton = uint(0..1);
    ''')
    for i, (operation, _, omit_begin, omit_end) in enumerate(MISUSE):
        begin = 'tx.begin(); rx.begin(); queue.begin();'.replace(omit_begin, '') if omit_begin else 'tx.begin(); rx.begin(); queue.begin();'
        end = 'tx.end(); rx.end(); queue.end();'.replace(omit_end, '') if omit_end else 'tx.end(); rx.end(); queue.end();'
        binding = 'internal' if i >= 12 else 'fifo'
        s.add_file_content([f'misuse_{i}'], f'misuse_{i}.kh', f'''
            import channel.endpoints.[Channel, transmitter, receiver];
            import channel.buffers.{binding};
            module Bad ports(clk: in clock, sync(clk) {{
                rst: in bool, input: interface Channel(uint(8)).Receiver,
                output: interface Channel(uint(8)).Transmitter,
            }}) {{ clocked(clk, sync rst) {{
                val tx = transmitter(uint(8), ref(output));
                val rx = receiver(uint(8), ref(input));
                val queue = {binding}(uint(8));
                {begin} {operation} {end}
            }} }}
        ''')
    c = s.compile()
    cache = {}

    def new(example='Relay', kind='byte', stages=0, reverse=False, depth=1):
        key = (example, kind, stages, reverse, depth)
        if key not in cache:
            types = dict(flag='test_types.Flag', byte='test_types.Byte',
                         words='test_types.Words', packet='channel.examples.Packet',
                         singleton='test_types.Singleton')
            path = ('channel.ref_self.Relay' if example == 'RefSelfRelay'
                    else example if '.' in example else 'channel.examples.' + example)
            item = c.resolve(path)
            if not isinstance(item, khdl.Module):
                args = {}
                if example in ('Relay', 'Chain', 'RefSelfRelay'):
                    args['T'] = c.resolve(types[kind])
                if example == 'Relay':
                    args.update(stages=stages, reverse=reverse, depth=depth)
                if example == 'InternalProbe':
                    args['reverse'] = reverse
                if example == 'FifoProbe':
                    args['depth'] = depth
                item = item(**args)
            cache[key] = build_simulator(item, 'fifo_' + '_'.join(map(str, key)))
        inst = cache[key].instance()
        for name in inst.ports:
            metadata = inst.info_ports[name]
            if isinstance(metadata, khdl.VerilatedInfoPort):
                if metadata.direction == 'input':
                    inst.ports[name] = 0 if name == 'value' else False
            else:
                for field in metadata:
                    port = metadata[field]
                    if port.direction == 'input':
                        if field in ('valid', 'ready'):
                            inst.ports[name][field] = False
                        else:
                            inst.ports[name][field] = (port.type.new(tag=0, words=[0, 0], last=False)
                                if kind == 'packet' else [0, 0, 0] if kind == 'words'
                                else False if kind == 'flag' else 0)
        inst.ports.rst = True
        tick(inst)
        tick(inst)
        inst.ports.rst = False
        inst.step(1)
        return inst

    yield new
    cache.clear()


def payloads(inst, kind, count=80):
    rng = random.Random(123)
    for i in range(count):
        if kind == 'packet':
            yield inst.info_ports.input.data.type.new(
                tag=i % 5, words=[rng.randrange(256), rng.randrange(256)], last=i % 7 == 0)
        elif kind == 'words':
            yield [rng.randrange(16) for _ in range(3)]
        elif kind == 'flag':
            yield bool(rng.randrange(2))
        elif kind == 'singleton':
            yield 0
        else:
            yield rng.randrange(256)


@pytest.mark.parametrize('stages,reverse,depth', PROFILES)
@pytest.mark.parametrize('kind', ['flag', 'byte', 'words', 'packet', 'singleton'])
def test_transaction_forwarding(new_channel, stages, reverse, depth, kind):
    # The same application algorithm and scoreboard work for every latency,
    # capacity, and traversal order; no intermediate timing is assumed.
    inst = new_channel(kind=kind, stages=stages, reverse=reverse, depth=depth)
    expected = list(payloads(inst, kind))
    rows = run(inst, [dict(data=value) for value in expected],
               ['data'], len(expected), seed=539, stall_denominator=2)
    assert [row['data'] for row in rows] == expected


@pytest.mark.parametrize('stages,reverse,depth', PROFILES)
def test_current_implementations_sustain_one_beat_per_cycle(new_channel, stages, reverse, depth):
    inst = new_channel(stages=stages, reverse=reverse, depth=depth)
    p = inst.ports
    sent = 0
    received = []
    output_cycles = []
    p.output.ready = True
    for cycle in range(128):
        p.input.valid = sent < 32
        p.input.data = sent
        inst.step(1)
        accepted = p.input.valid and p.input.ready
        if p.output.valid:
            received.append(p.output.data)
            output_cycles.append(cycle)
        tick(inst)
        sent += int(accepted)
    assert sent == 32
    assert received == list(range(32))
    assert output_cycles == list(range(output_cycles[0], output_cycles[0] + 32))
    if stages == 0:
        assert output_cycles[0] == 1  # Implementation-level endpoint timing check.


def test_output_slot_and_receiver_skid_hold_and_replace(new_channel):
    inst = new_channel()
    p = inst.ports
    p.output.ready = False
    p.input.valid = True
    p.input.data = 0x51
    tick(inst)  # A is immediately popped from Receiver and pushed to Transmitter.
    assert p.input.ready and p.output.valid
    p.input.data = 0xA7
    tick(inst)  # B is retained in Receiver while Transmitter remains full.
    assert not p.input.ready
    p.input.data = 0x39
    for _ in range(8):
        inst.step(1)
        assert not p.input.ready
        assert p.output.valid and p.output.data == 0x51
        tick(inst)
    p.output.ready = True
    inst.step(1)
    assert not p.input.ready and p.output.data == 0x51
    tick(inst)
    assert p.input.ready and p.output.data == 0xA7
    tick(inst)
    assert p.output.valid and p.output.data == 0x39
    p.input.valid = False
    tick(inst)
    assert not p.output.valid


@pytest.mark.parametrize('stages', [0, 3])
def test_reset_accepts_drops_and_recovers(new_channel, stages):
    inst = new_channel(stages=stages)
    p = inst.ports
    p.output.ready = False
    p.input.valid = True
    p.input.data = 0xA3
    for _ in range(12):
        tick(inst)
    assert p.output.valid
    previous = p.output.data
    p.rst = True
    inst.step(1)
    assert p.output.valid and p.output.data == previous
    for _ in range(3):
        tick(inst)
        assert not p.output.valid
        assert p.input.ready  # Reset-time input transfers are accepted and dropped.
    p.input.valid = False
    p.output.ready = True
    p.rst = False
    for _ in range(12):
        tick(inst)
        assert not p.output.valid
    rows = run(inst, [dict(data=0x19)], ['data'], 1, stutter=False)
    assert rows[0]['data'] == 0x19


def test_struct_payload_across_module_boundaries(new_channel):
    inst = new_channel('Chain', kind='packet')
    expected = list(payloads(inst, 'packet'))
    rows = run(inst, [dict(data=value) for value in expected],
               ['data'], len(expected), seed=44, stall_denominator=2)
    assert [row['data'] for row in rows] == expected


def test_two_api_instances_have_independent_state(new_channel):
    inst = new_channel('Independent')
    p = inst.ports
    rng = random.Random(457)
    expected = {'a': list(range(64)), 'b': list(range(128, 192))}
    sent = dict(a=0, b=0)
    received = dict(a=[], b=[])
    pending = dict(a=False, b=False)
    held = dict(a=None, b=None)
    for cycle in range(2000):
        accepted = {}
        for lane in ('a', 'b'):
            if not pending[lane] and sent[lane] < 64:
                pending[lane] = cycle < 12 or rng.randrange(3) != 0
            p[f'input_{lane}'].valid = pending[lane]
            if pending[lane]:
                p[f'input_{lane}'].data = expected[lane][sent[lane]]
            p[f'output_{lane}'].ready = lane == 'b' if cycle < 12 else rng.randrange(3) != 0
        inst.step(1)
        for lane in ('a', 'b'):
            valid = p[f'output_{lane}'].valid
            data = p[f'output_{lane}'].data if valid else None
            ready = p[f'output_{lane}'].ready
            if held[lane] is not None:
                assert valid and data == held[lane]
            held[lane] = data if valid and not ready else None
            if valid and ready:
                received[lane].append(data)
            accepted[lane] = pending[lane] and p[f'input_{lane}'].ready
        tick(inst)
        for lane in ('a', 'b'):
            if accepted[lane]:
                sent[lane] += 1
                pending[lane] = False
        if cycle == 11:
            assert sent['a'] == 2 and received['a'] == []
            assert len(received['b']) == 11
        if all(len(received[lane]) == 64 for lane in ('a', 'b')):
            break
    else:
        pytest.fail('Independent channels failed to drain')
    assert received == expected


def test_transmitter_immediate_slot_operations(new_channel):
    inst = new_channel('TransmitterProbe')
    p = inst.ports
    p.output.ready = False
    p.push = True
    p.value = 0x23
    tick(inst)
    assert p.available and not p.after_push
    assert p.output.valid and p.output.data == 0x23
    p.push = False
    tick(inst)
    assert not p.available and not p.after_push
    assert p.output.data == 0x23
    p.output.ready = True
    p.push = True
    p.value = 0x45
    tick(inst)
    assert p.available and not p.after_push
    assert p.output.valid and p.output.data == 0x45
    p.push = False
    tick(inst)
    assert p.available and p.after_push and not p.output.valid


def test_receiver_immediate_capture_peek_and_pop(new_channel):
    inst = new_channel('ReceiverProbe')
    p = inst.ports
    p.input.valid = True
    p.input.data = 0x23
    p.pop = True
    tick(inst)
    assert p.available and not p.after_pop
    assert p.peeked == p.popped == 0x23
    assert p.input.ready
    p.input.data = 0x45
    p.pop = False
    tick(inst)
    assert p.available and p.after_pop and not p.input.ready
    p.input.data = 0x67
    tick(inst)
    assert p.peeked == 0x45 and not p.input.ready
    p.pop = True
    tick(inst)
    assert p.popped == 0x45 and p.input.ready and not p.after_pop
    tick(inst)
    assert p.popped == 0x67 and p.input.ready


@pytest.mark.parametrize('depth', [2, 3, 4])
def test_fifo_separate_sides_and_fifo_order(new_channel, depth):
    inst = new_channel('FifoProbe', depth=depth)
    p = inst.ports
    rng = random.Random(293)
    queue = []
    for _ in range(300):
        size = len(queue)
        push = size < depth and bool(rng.randrange(2))
        pop = size > 0 and bool(rng.randrange(2))
        value = rng.randrange(256)
        p.push = push
        p.pop = pop
        p.value = value
        tick(inst)
        assert p.before_push == (size < depth)
        assert p.before_pop == (size > 0)
        assert p.after_push == (size < depth and not push)
        assert p.after_pop == (size > 0 and not pop)
        if size > 0:
            assert p.peeked == queue[0]
        if pop:
            assert p.popped == queue.pop(0)
        if push:
            queue.append(value)


@pytest.mark.parametrize('case', range(len(MISUSE)))
def test_runtime_preconditions_and_lifecycle(new_channel, case, capfd):
    inst = new_channel(f'misuse_{case}.Bad')
    if MISUSE[case][3] == 'queue.end();':  # Next begin detects the missing end.
        tick(inst)
    with pytest.raises(khdl.SimulationFinishedException):
        tick(inst)
    # Native $display uses C stdio, whose buffer outlives pytest's fd capture.
    ctypes.CDLL(None).fflush(None)
    assert MISUSE[case][1] in capfd.readouterr().out


@pytest.mark.parametrize('ports,body,diagnostic', [
    ('p: interface async Channel(uint(8)).Receiver',
     'val tx = transmitter(uint(8), ref(p));', 'input port'),
    ('p: interface async Channel(uint(8)).Transmitter',
     'val rx = receiver(uint(8), ref(p));', 'input port'),
    ('p: interface async Channel(uint(16)).Transmitter',
     'val tx = transmitter(uint(8), ref(p));', 'type mismatch'),
    ('p: interface async Channel(uint(8)).Transmitter',
     'val tx = transmitter(uint(8), ref(p));', 'register'),
])
def test_invalid_api_bindings_rejected(ports, body, diagnostic):
    s = source()
    # The first three use clocked context so actual direction/type errors are
    # reached before the independent process-context error in the fourth case.
    process = 'comb' if diagnostic == 'register' else 'clocked(clk, sync rst)'
    domain = ports.replace('async ', 'sync(clk) ') if process != 'comb' else ports
    s.add_file_content(['negative'], 'negative.kh', f'''
        import channel.endpoints.[Channel, transmitter, receiver];
        module Bad ports(clk: in clock, rst: in sync(clk) bool, {domain}) {{
            {process} {{ {body} }}
        }}
    ''')
    with pytest.raises(khdl.DiagnosticException) as exc:
        s.compile().resolve('negative.Bad')
    assert diagnostic in exc.value.combined_string


@pytest.mark.parametrize('fault,diagnostic', [
    ('clock', 'domain'), ('reset', 'clocked block without reset'),
    ('duplicate', 'already marked as a register'), ('escape', 'item parameters cannot contain references'),
    ('depth', 'type mismatch'),
])
def test_state_and_reference_ownership_rejected(fault, diagnostic):
    s = source()
    if fault == 'depth':
        with pytest.raises(khdl.DiagnosticException) as exc:
            s.compile().resolve('channel.examples.FifoProbe')(depth=1)
    elif fault == 'escape':
        s.add_file_content(['negative'], 'negative.kh', '''
            import channel.endpoints.[Channel, Transmitter, channel_signals];
            module Child(r: any) ports() {}
            module Bad ports(clk: in clock,
                p: interface sync(clk) Channel(uint(8)).Transmitter) {
                wire active: sync(clk) bool = false;
                const tx = Transmitter(uint(8)).new(channel=channel_signals(uint(8), ref(p)), active=ref(active));
                instance Child(tx) ports();
            }
        ''')
        with pytest.raises(khdl.DiagnosticException) as exc:
            s.compile().resolve('negative.Bad')
    else:
        body = 'val tx = transmitter(uint(8), ref(p));'
        if fault == 'duplicate':
            body += 'val duplicate = transmitter(uint(8), ref(p));'
        clock = 'other' if fault == 'clock' else 'clk'
        reset = '' if fault == 'reset' else ', sync rst'
        s.add_file_content(['negative'], 'negative.kh', f'''
            import channel.endpoints.[Channel, transmitter];
            module Child(r: any) ports() {{}}
            module Bad ports(clk: in clock, other: in clock,
                rst: in sync({clock}) bool,
                p: interface sync(clk) Channel(uint(8)).Transmitter) {{
                clocked({clock}{reset}) {{ {body} }}
            }}
        ''')
        with pytest.raises(khdl.DiagnosticException) as exc:
            s.compile().resolve('negative.Bad')
    assert diagnostic in exc.value.combined_string


@pytest.mark.parametrize('reverse', [False, True])
def test_internal_one_slot_chaining(new_channel, reverse):
    inst = new_channel('InternalProbe', reverse=reverse)
    p = inst.ports
    # Empty stage; forward order passes through, reverse order retains the item.
    p.push = True
    p.pop = not reverse
    p.value = 0x31
    tick(inst)
    assert p.before_push and not p.before_pop
    if not reverse:
        assert p.peeked == p.popped == 0x31
        assert p.after_push and not p.after_pop
        # Leave the single slot occupied.
        p.pop = False
        p.value = 0x42
        tick(inst)
    else:
        assert not p.after_push and p.after_pop
    # Full stage. Reverse order frees then refills; forward order consumes only.
    p.push = reverse
    p.pop = True
    p.value = 0x53
    tick(inst)
    assert not p.before_push and p.before_pop
    assert p.popped == (0x31 if reverse else 0x42)
    assert p.after_push == (not reverse)
    assert p.after_pop == reverse
    if reverse:
        p.push = False
        tick(inst)
        assert p.popped == 0x53
        assert p.after_push and not p.after_pop
    # The slot is reusable after draining, and reset discards an occupied slot.
    p.push = True
    p.pop = False
    tick(inst)
    assert p.after_pop
    p.rst = True
    tick(inst)
    p.rst = False
    p.push = False
    tick(inst)
    assert p.before_push and not p.before_pop


@pytest.mark.parametrize('kind', ['flag', 'byte', 'words', 'packet', 'singleton'])
def test_ref_self_transaction_forwarding(new_channel, kind):
    inst = new_channel('RefSelfRelay', kind=kind)
    expected = list(payloads(inst, kind))
    rows = run(inst, [dict(data=value) for value in expected],
               ['data'], len(expected), seed=539, stall_denominator=2)
    assert [row['data'] for row in rows] == expected


def test_ref_self_slot_and_skid(new_channel):
    def new(**kwargs):
        return new_channel('RefSelfRelay')
    test_output_slot_and_receiver_skid_hold_and_replace(new)


def test_ref_self_reset(new_channel):
    def new(**kwargs):
        return new_channel('RefSelfRelay')
    test_reset_accepts_drops_and_recovers(new, stages=0)


def test_ref_self_rate(new_channel):
    def new(**kwargs):
        return new_channel('RefSelfRelay')
    test_current_implementations_sustain_one_beat_per_cycle(new, stages=0, reverse=False, depth=1)
