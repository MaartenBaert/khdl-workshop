"""Generic transaction routing: no clock latency, strict order, and reset."""
import random

import khdl
import pytest

from . import util


@pytest.fixture(scope='module', params=[1, 2, 3])
def routers(request):
    source = khdl.Source.new_from_manifest_path(str(util.ROOT / 'rtl/khdl.toml'))
    source.add_file_content(['routing_checks'], 'routing_checks.kh', '''
        pub struct Packet { number: uint(16), tag: uint(8) }
    ''')
    compiler = source.compile()
    payload = compiler.resolve('routing_checks.Packet')
    modules = {}
    for name in ('Dispatch', 'Collect'):
        module = compiler.resolve('reedsolo_round_robin.RsRoundRobin' + name)(
            T=payload, count=request.param)
        modules[name] = util.build_simulator(module, 'round_robin')
    return request.param, modules


def new_router(module):
    inst = module.instance()
    for name in inst.ports:
        if name in ('clk', 'rst'):
            inst.ports[name] = False
        else:
            for field in ('valid', 'ready'):
                if inst.info_ports[name][field].direction == 'input':
                    inst.ports[name][field] = False
    inst.ports.rst = True
    util.tick(inst)
    inst.ports.rst = False
    inst.step(1)
    return inst


def test_dispatch(routers):
    count, modules = routers
    inst = new_router(modules['Dispatch'])
    p = inst.ports
    rng = random.Random(185)
    sent = 0
    pending = False
    for cycle in range(1000):
        pending = pending or rng.randrange(3) != 0
        ready = [rng.randrange(4) == 0 for _ in range(count)]
        p.input.data = inst.info_ports.input.data.type.new(number=sent + 100, tag=sent)
        p.input.valid = pending
        for i in range(count):
            p[f'output_{i}'].ready = ready[i]
        # Routing must propagate before any clock edge.
        inst.step(1)
        selected = sent % count
        assert p.input.ready == ready[selected]
        for i in range(count):
            assert p[f'output_{i}'].valid == (pending and i == selected)
            assert p[f'output_{i}'].data.number == sent + 100
            assert p[f'output_{i}'].data.tag == sent
        accepted = pending and ready[selected]
        util.tick(inst)
        if accepted:
            sent += 1
            pending = False
        if sent == 29:
            break
    assert sent == 29
    # Reset during a stalled transfer must restart distribution at worker zero.
    p.input.valid = True
    for i in range(count):
        p[f'output_{i}'].ready = False
    p.rst = True
    inst.step(1)
    assert p[f'output_{sent % count}'].valid
    util.tick(inst)
    p.rst = False
    inst.step(1)
    assert [p[f'output_{i}'].valid for i in range(count)] == [True] + [False] * (count - 1)


def test_collect(routers):
    count, modules = routers
    inst = new_router(modules['Collect'])
    p = inst.ports
    rng = random.Random(786)
    next_values = list(range(count))
    pending = [False] * count
    received = 0
    held = None
    for cycle in range(1000):
        for i in range(count):
            # Later workers can finish first; never skip the selected worker.
            pending[i] = (i != 0) if cycle < 5 else (
                pending[i] or rng.randrange(4 if i == 0 else 2) == 0)
            p[f'input_{i}'].valid = pending[i]
            p[f'input_{i}'].data = inst.info_ports[f'input_{i}'].data.type.new(
                number=next_values[i] + 100, tag=next_values[i])
        ready = rng.randrange(3) == 0 and cycle % 31 >= 7
        p.output.ready = ready
        inst.step(1)
        selected = received % count
        assert p.output.valid == pending[selected]
        assert [p[f'input_{i}'].ready for i in range(count)] == [
            ready and i == selected for i in range(count)]
        if pending[selected]:
            result = (p.output.data.number, p.output.data.tag)
            assert result == (received + 100, received)
            if held is not None:
                assert result == held
            held = result if not ready else None
        util.tick(inst)
        if pending[selected] and ready:
            received += 1
            pending[selected] = False
            next_values[selected] += count
        if received == 29:
            break
    assert received == 29
    # Synchronous reset also restarts collection, including while output stalls.
    p.output.ready = False
    for i in range(count):
        p[f'input_{i}'].valid = True
        p[f'input_{i}'].data = inst.info_ports[f'input_{i}'].data.type.new(
            number=next_values[i] + 100, tag=next_values[i])
    p.rst = True
    inst.step(1)
    assert p.output.data.number == next_values[received % count] + 100
    util.tick(inst)
    p.rst = False
    inst.step(1)
    assert p.output.data.number == next_values[0] + 100


@pytest.mark.parametrize('name', ['Dispatch', 'Collect'])
def test_zero_workers_rejected(name):
    c = khdl.Source.new_from_manifest_path(str(util.ROOT / 'rtl/khdl.toml')).compile()
    with pytest.raises(khdl.DiagnosticException) as exc:
        c.resolve('reedsolo_round_robin.RsRoundRobin' + name)(
            T=c.resolve('reedsolo_interfaces.RsSyndromesBlock')(), count=0)
    assert 'type mismatch' in exc.value.combined_string


@pytest.mark.parametrize('payload_name', ['RsChienBlock', 'RsForneyBlock'])
def test_forney_routing_payloads(payload_name):
    c = util.reference_compiler()
    payload = c.resolve('reedsolo_interfaces.' + payload_name)(unroll_chien=3)
    for name in ('Dispatch', 'Collect'):
        module = c.resolve('reedsolo_round_robin.RsRoundRobin' + name)(T=payload, count=3)
        assert module.as_verilog().source
