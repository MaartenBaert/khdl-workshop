"""AXI configuration, reference binding and independent-peer interoperability."""
from pathlib import Path
import random
import ctypes

import khdl
import pytest

from ..tools.axi_wrapper import flat_wrapper
from ..tools.axi_wrapper import payload_fields
from test_support.simulation import build_simulator, tick

MANIFEST = Path(__file__).resolve().parents[1] / 'rtl/khdl.toml'


def run_cocotb(tmp_path, verilog, config, buses, testcase, extra_ports=None):
    from cocotb_tools.runner import get_runner
    source = tmp_path / 'design.v'
    source.write_text(verilog.source)
    wrapper = tmp_path / 'axi_test.v'
    wrapper.write_text(flat_wrapper(verilog.module_name, config, buses, extra_ports))
    runner = get_runner('verilator')
    runner.build(sources=[source, wrapper], hdl_toplevel='axi_test',
                 build_dir=tmp_path / 'build', build_args=['-Wno-fatal', '-O0'],
                 log_file=tmp_path / 'build.log')
    runner.test(hdl_toplevel='axi_test',
                test_module='projects.axi.tests.axi_cocotb', test_filter=r'\.' + testcase + '$',
                test_dir=tmp_path, log_file=tmp_path / 'test.log')


@pytest.mark.parametrize('width', [8, 32, 64])
@pytest.mark.parametrize('user_width', [0, 3])
def test_pipeline_elaboration(width, user_width):
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = compiler.resolve('axi.types.axi_config')(
        addr_width=32, data_width=width, id_width=4,
        awuser_width=user_width, wuser_width=user_width,
        buser_width=user_width, aruser_width=user_width, ruser_width=user_width)
    module = compiler.resolve('examples.pipeline.AxiPipeline')(config)
    assert module.as_verilog().source


@pytest.mark.parametrize('width', [7, 24, 2048])
def test_bad_data_width(width):
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    with pytest.raises(khdl.DiagnosticException, match='data width'):
        compiler.resolve('axi.types.axi_config')(addr_width=32, data_width=width)


@pytest.mark.parametrize('width', [8, 32, 64])
def test_pipeline_independent_axi_peers(tmp_path, width):
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = compiler.resolve('axi.types.axi_config')(addr_width=32, data_width=width)
    verilog = compiler.resolve('examples.pipeline.AxiPipeline')(config).as_verilog()
    run_cocotb(tmp_path, verilog, config, {
        'input': ('s_axi', 'Subordinate'), 'output': ('m_axi', 'Manager')}, 'pipeline')


@pytest.mark.parametrize('width,capacity,depth', [(8, 1, 1), (32, 3, 2), (64, 2, 3)])
def test_ram_independent_axi_manager(tmp_path, width, capacity, depth):
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = compiler.resolve('axi.types.axi_config')(addr_width=32, data_width=width)
    verilog = compiler.resolve('examples.ram.AxiRam')(
        config, memory_bytes=8192, capacity=capacity, queue_depth=depth).as_verilog()
    run_cocotb(tmp_path, verilog, config, {'bus': ('s_axi', 'Subordinate')}, '(ram|raw_ram|ram_rate)')


def test_ram_out_of_range_beats(tmp_path):
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = compiler.resolve('axi.types.AxiDefault')
    verilog = compiler.resolve('examples.ram.AxiRam')(config, memory_bytes=6).as_verilog()
    run_cocotb(tmp_path, verilog, config, {'bus': ('s_axi', 'Subordinate')}, 'ram_bounds')


def test_error_independent_axi_manager(tmp_path):
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = compiler.resolve('axi.types.AxiDefault')
    verilog = compiler.resolve('examples.error.AxiError')(config).as_verilog()
    run_cocotb(tmp_path, verilog, config, {'bus': ('s_axi', 'Subordinate')}, 'error')


def manager_ports(compiler, config, capacity):
    token_width = (capacity - 1).bit_length()
    types = {
        'read_request': (config.Ar.size_bits, False),
        'write_request': (config.Aw.size_bits, False),
        'write_data': (compiler.resolve('axi_manager_bridge.WriteData')(config, capacity).size_bits, False),
        'read_token': (token_width, True), 'write_token': (token_width, True),
        'read_result': (compiler.resolve('axi.pooled_manager.ReadResult')(config, capacity).size_bits, True),
        'write_result': (compiler.resolve('axi.pooled_manager.WriteResult')(config, capacity).size_bits, True),
    }
    ports = {}
    for name, (width, output) in types.items():
        forward, reverse = ('output', 'input') if output else ('input', 'output')
        ports[name+'_data'] = (forward, width)
        ports[name+'_valid'] = (forward, 1)
        ports[name+'_ready'] = (reverse, 1)
    return ports


@pytest.mark.parametrize('testcase', ['manager_ram', 'manager_reordered'])
def test_manager_independent_subordinate(tmp_path, testcase):
    source = khdl.Source.new_from_manifest_path(str(MANIFEST))
    probe = Path(__file__).parent / 'rtl/manager_bridge.kh'
    source.add_file_content(['axi_manager_bridge'], str(probe), probe.read_text())
    compiler = source.compile()
    config = compiler.resolve('axi.types.AxiDefault')
    verilog = compiler.resolve('axi_manager_bridge.AxiManagerBridge')(config, capacity=3).as_verilog()
    run_cocotb(tmp_path, verilog, config, {'bus': ('m_axi', 'Manager')}, testcase,
               manager_ports(compiler, config, 3))


def test_copy_independent_ram(tmp_path):
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = compiler.resolve('axi.types.AxiDefault')
    verilog = compiler.resolve('examples.copy.AxiCopy')(config).as_verilog()
    ports = {}
    for name, width, output in [
        ('command', compiler.resolve('examples.copy.CopyCommand')(config).size_bits, False),
        ('result', compiler.resolve('examples.copy.CopyResult').size_bits, True),
    ]:
        forward, reverse = ('output', 'input') if output else ('input', 'output')
        ports[name + '_data'] = (forward, width)
        ports[name + '_valid'] = (forward, 1)
        ports[name + '_ready'] = (reverse, 1)
    run_cocotb(tmp_path, verilog, config, {'bus': ('m_axi', 'Manager')}, 'copy', ports)


def protocol_config(compiler, protocol, width, id_width=1, addr_width=32):
    protocols = compiler.resolve('axi.types.Protocol')
    return compiler.resolve('axi.types.axi_config')(addr_width, width, id_width, 0, 0, 0, 0, 0,
                                                    getattr(protocols, protocol))


@pytest.mark.parametrize('arguments,message', [
    (dict(data_width=8), 'AXI4-Lite data width'), (dict(data_width=128), 'AXI4-Lite data width'),
    (dict(id_width=1), 'no ID signals'), (dict(ruser_width=1), 'no USER signals'),
])
def test_lite_config_restrictions(arguments, message):
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    fields = dict(addr_width=16, data_width=32, id_width=0, ruser_width=0)
    fields.update(arguments)
    with pytest.raises(khdl.DiagnosticException, match=message):
        compiler.resolve('axi.types.axi_config')(fields['addr_width'], fields['data_width'],
            fields['id_width'], 0, 0, 0, 0, fields['ruser_width'],
            compiler.resolve('axi.types.Protocol').Lite)


def test_axi3_request_rules():
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = protocol_config(compiler, 'Axi3', 64, id_width=2)
    request = compiler.resolve('axi.types.read_request')
    valid = compiler.resolve('axi.burst.valid_request')
    sixteen = request(config, 128, beats=16)
    assert sixteen.len == 15 and sixteen.lock == 0 and valid(config, sixteen)
    # Calls pass intervening defaults explicitly (ordered arguments).
    exclusive = request(config, 128, beats=2, id=0, size=3, burst=1, lock=True)
    assert exclusive.lock == 1 and valid(config, exclusive)
    # AXI3 lock encodings: 2 locked (legal), 3 reserved.
    fields = dict(id=0, addr=128, len=0, size=3, burst=1, lock=2, cache=0, prot=0, user=0)
    assert valid(config, config.Ar.new(**fields))
    fields['lock'] = 3
    assert not valid(config, config.Ar.new(**fields))
    with pytest.raises(khdl.DiagnosticException, match='at most 16 beats'):
        request(config, 128, beats=17)


def test_lite_request_rules():
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = protocol_config(compiler, 'Lite', 32, id_width=0, addr_width=16)
    request = compiler.resolve('axi.types.write_request')(
        config, 0x123, beats=1, id=0, size=2, burst=1, lock=False, cache=0, prot=2)
    assert request.addr == 0x123 and request.prot == 2
    assert compiler.resolve('axi.burst.valid_request')(config, request)
    with pytest.raises(khdl.DiagnosticException, match='single full-width beats'):
        compiler.resolve('axi.types.write_request')(config, 0, beats=2)


def test_lite_fixed_id_manager_rejected():
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = protocol_config(compiler, 'Lite', 32, id_width=0, addr_width=16)
    with pytest.raises(khdl.DiagnosticException, match='AXI4-Lite is not supported yet'):
        compiler.resolve('examples.copy.AxiCopy')(config).as_verilog()


@pytest.mark.parametrize('width,capacity,depth', [(32, 2, 1), (64, 3, 2)])
def test_axi3_ram_independent_manager(tmp_path, width, capacity, depth):
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = protocol_config(compiler, 'Axi3', width, id_width=2)
    verilog = compiler.resolve('examples.ram.AxiRam')(
        config, memory_bytes=8192, capacity=capacity, queue_depth=depth).as_verilog()
    run_cocotb(tmp_path, verilog, config, {'bus': ('s_axi', 'Subordinate')}, 'axi3_ram')


@pytest.mark.parametrize('width', [32, 64])
def test_axi3_copy_independent_ram(tmp_path, width):
    # The fixed-ID Manager on AXI3: at most 16-beat bursts, WID = AWID.
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = protocol_config(compiler, 'Axi3', width)
    verilog = compiler.resolve('examples.copy.AxiCopy')(config).as_verilog()
    ports = {}
    for name, size, output in [
        ('command', compiler.resolve('examples.copy.CopyCommand')(config).size_bits, False),
        ('result', compiler.resolve('examples.copy.CopyResult').size_bits, True),
    ]:
        forward, reverse = ('output', 'input') if output else ('input', 'output')
        ports[name + '_data'] = (forward, size)
        ports[name + '_valid'] = (forward, 1)
        ports[name + '_ready'] = (reverse, 1)
    run_cocotb(tmp_path, verilog, config, {'bus': ('m_axi', 'Manager')}, 'axi3_copy', ports)


@pytest.mark.parametrize('width,capacity,depth', [(32, 1, 1), (64, 3, 2)])
def test_lite_ram_independent_manager(tmp_path, width, capacity, depth):
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = protocol_config(compiler, 'Lite', width, id_width=0, addr_width=16)
    verilog = compiler.resolve('examples.ram.AxiRam')(
        config, memory_bytes=8192, capacity=capacity, queue_depth=depth).as_verilog()
    run_cocotb(tmp_path, verilog, config, {'bus': ('s_axi', 'Subordinate')}, 'lite_ram')


@pytest.mark.parametrize('width', [32, 64])
def test_lite_pipeline_independent_peers(tmp_path, width):
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = protocol_config(compiler, 'Lite', width, id_width=0)
    verilog = compiler.resolve('examples.pipeline.AxiPipeline')(config).as_verilog()
    run_cocotb(tmp_path, verilog, config, {
        'input': ('s_axi', 'Subordinate'), 'output': ('m_axi', 'Manager')}, 'lite_pipeline')


@pytest.mark.parametrize('depth', [1, 2, 3])
def test_router_independent_axi_peers(tmp_path, depth):
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = compiler.resolve('axi.types.AxiDefault')
    verilog = compiler.resolve('examples.router.AxiRouter')(config, capacity=3, queue_depth=depth).as_verilog()
    run_cocotb(tmp_path, verilog, config, {
        'input': ('s_axi', 'Subordinate'),
        'output0': ('m0_axi', 'Manager'), 'output1': ('m1_axi', 'Manager')}, 'router')


def test_subordinate_reordering_and_same_id_order(tmp_path):
    source = khdl.Source.new_from_manifest_path(str(MANIFEST))
    probe = Path(__file__).parent / 'rtl/reorder.kh'
    source.add_file_content(['reorder_probe'], str(probe), probe.read_text())
    compiler = source.compile()
    config = compiler.resolve('axi.types.AxiDefault')
    verilog = compiler.resolve('reorder_probe.ReorderProbe')(config).as_verilog()
    run_cocotb(tmp_path, verilog, config, {'bus': ('s_axi', 'Subordinate')}, 'subordinate_reordered')


@pytest.mark.parametrize('width', [8, 32, 64, 1024])
def test_burst_arithmetic(width):
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = compiler.resolve('axi.types.axi_config')(addr_width=32, data_width=width)
    valid = compiler.resolve('axi.burst.valid_request')
    address = compiler.resolve('axi.burst.beat_address')
    mask = compiler.resolve('axi.burst.lane_mask')
    for size in range(config.size + 1):
        beat_bytes = 1 << size
        for burst, count in [(0, 1), (0, 16), (1, 1), (1, 256), (2, 2), (2, 16)]:
            for offset in range(min(beat_bytes, 4)):
                start = 128 + offset
                request = config.Ar.new(id=0, addr=start, len=count-1, size=size,
                    burst=burst, lock=False, cache=0, prot=0, qos=0, region=0, user=0)
                expected_valid = (burst != 2 or offset == 0)
                if burst == 1:
                    expected_valid &= start//beat_bytes*beat_bytes % 4096 + count*beat_bytes <= 4096
                assert valid(config, request) == expected_valid
                if not expected_valid:
                    continue
                for beat in [0, count//2, count-1]:
                    expected = start
                    if burst == 1 and beat:
                        expected = start//beat_bytes*beat_bytes + beat*beat_bytes
                    elif burst == 2:
                        span = beat_bytes*count
                        expected = start//span*span + (start + beat*beat_bytes) % span
                    assert address(config, request, beat) == expected
                    lower = expected % config.bytes
                    upper = lower - expected % beat_bytes + beat_bytes
                    assert mask(config, request, beat) == sum(1 << i for i in range(lower, upper))


@pytest.mark.parametrize('addr_width', [8, 11, 12, 13, 32])
def test_incr_page_and_address_space_limits(addr_width):
    # INCR bursts at 4 KB page edges and at the top of the address space,
    # against both rules written out independently (IHI 0022H A3.4.1).
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = compiler.resolve('axi.types.axi_config')(addr_width=addr_width, data_width=64)
    valid = compiler.resolve('axi.burst.valid_request')
    top = 2**addr_width
    starts = {0, 1, 7, 8, top - 1, top - 8, top - 9, top - 64, top - 65}
    pages = {0, 4096, 8192, top - 4096, top - 8192}
    starts |= {page + d for page in pages for d in (-129, -128, -8, -1, 0, 1)}
    for start in sorted(a for a in starts if 0 <= a < top):
        for size in range(config.size + 1):
            beat_bytes = 1 << size
            for count in (1, 2, 8, 16, 17, 256):
                aligned = start // beat_bytes * beat_bytes
                expected = (aligned % 4096 + count * beat_bytes <= 4096
                            and aligned + count * beat_bytes <= top)
                request = config.Ar.new(id=0, addr=start, len=count-1, size=size, burst=1,
                    lock=False, cache=0, prot=0, qos=0, region=0, user=0)
                assert valid(config, request) == expected, (start, size, count)


@pytest.mark.parametrize('width,id_width,user_width', [(8, 0, 0), (32, 4, 3), (64, 3, 5)])
def test_all_channel_bits_and_stall_stability(simulator_build_dirs, width, id_width, user_width):
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = compiler.resolve('axi.types.axi_config')(
        addr_width=32, data_width=width, id_width=id_width,
        awuser_width=user_width, wuser_width=user_width+1,
        buser_width=user_width+2, aruser_width=user_width+3, ruser_width=user_width+4)
    inst = build_simulator(compiler.resolve('examples.pipeline.AxiPipeline')(config), 'axi_bits').instance()
    p = inst.ports
    rng = random.Random(531)
    paths = {}
    for channel, fields in payload_fields(config).items():
        source, target = ('input', 'output') if channel in ('aw', 'w', 'ar') else ('output', 'input')
        values = []
        for _ in range(41):
            values.append({name: bool(rng.randrange(2)) if name in ('lock', 'last')
                           else rng.randrange(1 << bits) for name, bits in fields})
        paths[channel] = dict(source=source, target=target,
                              values=values, sent=0, received=0, pending=False, held=None)
        p[source][channel+'valid'] = False
        p[target][channel+'ready'] = False
    p.clk = False
    p.rst = True
    tick(inst)
    tick(inst)  # khdl#45: the first edge after creation is missed; reset needs two
    p.rst = False
    for cycle in range(2000):
        for channel, path in paths.items():
            source, target = path['source'], path['target']
            if not path['pending'] and path['sent'] < len(path['values']) and rng.randrange(3):
                p[source][channel] = inst.info_ports[source][channel].type.new(**path['values'][path['sent']])
                path['pending'] = True
            p[source][channel+'valid'] = path['pending']
            p[target][channel+'ready'] = bool(rng.randrange(3))
        inst.step(1)
        for channel, path in paths.items():
            source, target = path['source'], path['target']
            valid = p[target][channel+'valid']
            value = p[target][channel].fields if valid else None
            if path['held'] is not None:
                assert valid and value == path['held'], (channel, cycle)
            path['held'] = value if valid and not p[target][channel+'ready'] else None
            if valid and p[target][channel+'ready']:
                assert value == path['values'][path['received']], (channel, cycle)
                path['received'] += 1
            if path['pending'] and p[source][channel+'ready']:
                path['sent'] += 1
                path['pending'] = False
        tick(inst)
        if all(path['received'] == len(path['values']) for path in paths.values()):
            break
    else:
        pytest.fail('independent channel traffic did not drain')


@pytest.mark.parametrize('update', [dict(size=7), dict(burst=3), dict(burst=0, len=16),
    dict(burst=2, len=0), dict(burst=2, len=3, addr=129),
    dict(addr=4095, len=1), dict(lock=True, len=2), dict(lock=True, addr=1),
    dict(cache=0b0100), dict(cache=0b1001)])
def test_invalid_request_combinations(update):
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = compiler.resolve('axi.types.AxiDefault')
    fields = dict(id=0, addr=0, len=0, size=2, burst=1, lock=False,
                  cache=0, prot=0, qos=0, region=0, user=0)
    fields.update(update)
    assert not compiler.resolve('axi.burst.valid_request')(config, config.Ar.new(**fields))


def test_cache_encodings():
    # IHI 0022H A4.4: allocate bits (3:2) require the Modifiable bit (1).
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = compiler.resolve('axi.types.AxiDefault')
    valid = compiler.resolve('axi.burst.valid_request')
    for cache in range(16):
        request = config.Aw.new(id=0, addr=0, len=0, size=2, burst=1, lock=False,
                                cache=cache, prot=0, qos=0, region=0, user=0)
        assert valid(config, request) == bool(cache & 2 or not cache & 12), cache


def test_request_builders():
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    config = compiler.resolve('axi.types.AxiDefault')
    read = compiler.resolve('axi.types.read_request')(config, 64, beats=256, id=3)
    write = compiler.resolve('axi.types.write_request')(config, 128, beats=4, id=2)
    assert read.addr == 64 and read.len == 255 and read.id == 3 and read.size == 2
    assert write.addr == 128 and write.len == 3 and write.id == 2 and not write.lock


@pytest.mark.parametrize('binding,role', [('manager', 'Subordinate'), ('subordinate', 'Manager')])
def test_wrong_axi_binding_role(binding, role):
    source = khdl.Source.new_from_manifest_path(str(MANIFEST))
    source.add_file_content(['negative'], 'negative.kh', f'''
        import axi.types.AxiDefault;
        import axi.interfaces.Axi;
        import axi.{binding}.{binding};
        module Bad ports(clk: in clock, rst: in async bool,
            bus: interface sync(clk, async rst) Axi(AxiDefault).{role}) {{
            clocked(clk, async rst) {{
                val api = {binding}(AxiDefault, ref(bus)); api.begin(); api.end();
            }}
        }}
    ''')
    with pytest.raises(khdl.DiagnosticException, match='cannot drive an input port'):
        source.compile().resolve('negative.Bad')


@pytest.fixture(scope='module')
def native_error(simulator_build_dirs):
    compiler = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    return build_simulator(compiler.resolve('examples.error.AxiError')(), 'axi_bad_peer')


@pytest.mark.parametrize('fault,message', [('last', 'WLAST'), ('strobe', 'WSTRB'), ('burst', 'malformed AXI4')])
def test_malformed_peer_diagnostics(native_error, fault, message, capfd):
    inst = native_error.instance()
    p = inst.ports
    p.clk = False
    p.bus.awvalid = p.bus.wvalid = p.bus.arvalid = False
    p.bus.bready = p.bus.rready = False
    p.rst = True
    tick(inst)
    tick(inst)  # khdl#45: the first edge after creation is missed; reset needs two
    p.rst = False
    fields = dict(id=0, addr=0, len=0, size=2, burst=1, lock=False,
                  cache=0, prot=0, qos=0, region=0, user=0)
    if fault == 'burst':
        fields['burst'] = 3
        p.bus.ar = inst.info_ports.bus.ar.type.new(**fields)
        p.bus.arvalid = True
    else:
        if fault == 'strobe':
            fields['size'] = 0
        p.bus.aw = inst.info_ports.bus.aw.type.new(**fields)
        p.bus.w = inst.info_ports.bus.w.type.new(data=0, strb=2 if fault == 'strobe' else 15,
                                       last=fault != 'last', user=0)
        p.bus.awvalid = p.bus.wvalid = True
    with pytest.raises(khdl.SimulationFinishedException):
        tick(inst)
        p.bus.awvalid = p.bus.wvalid = p.bus.arvalid = False
        for _ in range(8):
            tick(inst)
    ctypes.CDLL(None).fflush(None)
    assert message in capfd.readouterr().out


def test_fixed_endpoint_manager_independence_and_order(tmp_path):
    source = khdl.Source.new_from_manifest_path(str(MANIFEST))
    probe = Path(__file__).parent / 'rtl/endpoints.kh'
    source.add_file_content(['endpoints_probe'], str(probe), probe.read_text())
    compiler = source.compile()
    config = compiler.resolve('axi.types.AxiDefault')
    module = compiler.resolve('endpoints_probe.EndpointsProbe')(config)
    ports = {}
    for i in range(2):
        types = {
            f'read{i}_request': (config.Ar.size_bits, False),
            f'write{i}_request': (config.Aw.size_bits, False),
            f'write{i}_data': (compiler.resolve('endpoints_probe.WriteData')(config).size_bits, False),
            f'read{i}_result': (compiler.resolve('axi.manager.ReadBeat')(config).size_bits, True),
            f'write{i}_result': (compiler.resolve('axi.manager.WriteCompletion')(config).size_bits, True),
        }
        for name, (width, output) in types.items():
            forward, reverse = ('output', 'input') if output else ('input', 'output')
            ports[name + '_data'] = (forward, width)
            ports[name + '_valid'] = (forward, 1)
            ports[name + '_ready'] = (reverse, 1)
    run_cocotb(tmp_path, module.as_verilog(), config, {'bus': ('m_axi', 'Manager')},
               '(endpoints|endpoints_ram)', ports)


def endpoint_probe(reads, writes, body='', *, id_width=4, data_width=32):
    if writes is not None and reads is None:
        reads = '()'
    source = khdl.Source.new_from_manifest_path(str(MANIFEST))
    args = (f', reads={reads}' if reads else '') + (f', writes={writes}' if writes else '')
    source.add_file_content(['endpoint_config_probe'], 'endpoint_config_probe.kh', f'''
        import axi.types.[axi_config, read_request, write_request];
        import axi.interfaces.Axi;
        import axi.manager.[manager, read_endpoint, write_endpoint];
        type C = axi_config(addr_width=32, data_width={data_width}, id_width={id_width});
        module Probe ports(clk: in clock, rst: in async bool,
            index: in sync(clk, async rst) uint(2),
            bus: interface sync(clk, async rst) Axi(C).Manager) {{
            clocked(clk, async rst) {{
                val read = read_endpoint(C);
                val write = write_endpoint(C);
                val api = manager(C, ref(bus){args});
                api.begin();
                {body}
                api.end();
            }}
        }}
    ''')
    return source.compile().resolve('endpoint_config_probe.Probe')


@pytest.mark.parametrize('reads,writes,id_width,width,body', [
    (None, None, 0, 8, ''),
    ('(read,)', None, 0, 64, 'if (read.can_push()) { read.push(read_request(C, 0)); } if (read.can_pop()) { val _ = read.pop(); }'),
    (None, '(write,)', 0, 8, 'if (write.can_push()) { write.push(write_request(C, 0)); } if (write.can_push_data()) { val _ = write.push_data(0, 1); } if (write.can_pop()) { val _ = write.pop(); }'),
    ('(read, read_endpoint(C, id=2, capacity=3), read_endpoint(C, id=7, capacity=1, response_depth=3))',
     '(write_endpoint(C, id=7, capacity=2),)', 3, 1024,
     'if (read.can_push()) { read.push(read_request(C, 0)); } if (read.can_pop()) { val _ = read.pop(); }'),
])
def test_endpoint_configuration_profiles(reads, writes, id_width, width, body):
    assert endpoint_probe(reads, writes, body, id_width=id_width, data_width=width).as_verilog().source


@pytest.mark.parametrize('reads,writes,message', [
    ('(read, read_endpoint(C))', None, 'unique per direction'),
    (None, '(write, write_endpoint(C))', 'unique per direction'),
    ('(read, read)', None, 'unique per direction'),
    ('(read_endpoint(C, id=16),)', None, 'exceeds bus width'),
    ('(read,)', '(read,)', 'read endpoint bound as a writer'),
    ('(write,)', None, 'write endpoint bound as a reader'),
    ('(read_endpoint(C, id=index),)', None, 'compile-time'),
])
def test_invalid_endpoint_configuration(reads, writes, message):
    with pytest.raises(khdl.DiagnosticException, match=message):
        endpoint_probe(reads, writes)


@pytest.fixture(scope='module')
def native_endpoints(simulator_build_dirs):
    source = khdl.Source.new_from_manifest_path(str(MANIFEST))
    probe = Path(__file__).parent / 'rtl/endpoints.kh'
    source.add_file_content(['endpoints_probe'], str(probe), probe.read_text())
    return build_simulator(source.compile().resolve('endpoints_probe.EndpointsProbe')(), 'axi_endpoints')


@pytest.mark.parametrize('fault,message', [
    ('id', 'unknown RID'), ('last', 'RLAST'), ('early_b', 'unexpected response'),
    # B for a burst whose final W beat is still local or still in the W register.
    ('b_before_local_w', 'before final W transfer'), ('b_before_bus_w', 'before final W transfer'),
    # Endpoint 5's three-deep data Fifo: the final beat is queued behind another.
    ('b_before_queued_w', 'before final W transfer'),
])
def test_endpoint_response_diagnostics(native_endpoints, fault, message, capfd):
    inst = native_endpoints.instance()
    p = inst.ports
    p.clk = False
    p.bus.rvalid = p.bus.bvalid = False
    p.bus.arready = p.bus.awready = p.bus.wready = True
    for i in range(2):
        p[f'read{i}_request'].valid = False
        p[f'write{i}_request'].valid = False
        p[f'write{i}_data'].valid = False
        p[f'read{i}_result'].ready = True
        p[f'write{i}_result'].ready = True
    p.rst = True
    tick(inst)
    tick(inst)  # khdl#45: the first edge after creation is missed; reset needs two
    p.rst = False
    if fault == 'last':
        fields = dict(id=15, addr=0, len=0, size=2, burst=1, lock=False,
                      cache=0, prot=0, qos=0, region=0, user=0)
        p.read0_request.data = inst.info_ports.read0_request.data.type.new(**fields)
        p.read0_request.valid = True
        tick(inst)
        p.read0_request.valid = False
        tick(inst)
    if fault in ('b_before_local_w', 'b_before_bus_w'):
        # With WREADY low, the first beat fills the W register. A two-beat
        # burst leaves its final beat in the endpoint's local data slot.
        p.bus.wready = False
        beats = 2 if fault == 'b_before_local_w' else 1
        fields = dict(id=0, addr=0, len=beats - 1, size=2, burst=1, lock=False,
                      cache=0, prot=0, qos=0, region=0, user=0)
        p.write0_request.data = inst.info_ports.write0_request.data.type.new(**fields)
        p.write0_request.valid = True
        p.write0_data.data = inst.info_ports.write0_data.data.type.new(data=0, strb=15, user=0)
        p.write0_data.valid = True
        tick(inst)
        p.write0_request.valid = False
        if beats == 1:
            p.write0_data.valid = False
        tick(inst)
        p.write0_data.valid = False
        for _ in range(4):
            tick(inst)
    if fault == 'b_before_queued_w':
        # WREADY low: beat 1 fills the W register, beats 2 and 3 stay queued.
        p.bus.wready = False
        fields = dict(id=0, addr=0, len=2, size=2, burst=1, lock=False,
                      cache=0, prot=0, qos=0, region=0, user=0)
        p.write1_request.data = inst.info_ports.write1_request.data.type.new(**fields)
        p.write1_request.valid = True
        p.write1_data.data = inst.info_ports.write1_data.data.type.new(data=0, strb=15, user=0)
        p.write1_data.valid = True
        tick(inst)
        p.write1_request.valid = False
        for _ in range(6):
            tick(inst)
        p.write1_data.valid = False
        for _ in range(2):
            tick(inst)
        p.bus.b = inst.info_ports.bus.b.type.new(id=5, resp=0, user=0)
        p.bus.bvalid = True
    elif fault in ('early_b', 'b_before_local_w', 'b_before_bus_w'):
        p.bus.b = inst.info_ports.bus.b.type.new(id=1, resp=0, user=0)
        p.bus.bvalid = True
    else:
        p.bus.r = inst.info_ports.bus.r.type.new(id=7 if fault == 'id' else 1,
                                        data=0, resp=0, last=fault != 'last', user=0)
        p.bus.rvalid = True
    with pytest.raises(khdl.SimulationFinishedException):
        for _ in range(8):
            tick(inst)
    ctypes.CDLL(None).fflush(None)
    assert message in capfd.readouterr().out


@pytest.fixture(scope='module')
def native_bridge(simulator_build_dirs):
    source = khdl.Source.new_from_manifest_path(str(MANIFEST))
    probe = Path(__file__).parent / 'rtl/manager_bridge.kh'
    source.add_file_content(['axi_manager_bridge'], str(probe), probe.read_text())
    return build_simulator(source.compile().resolve('axi_manager_bridge.AxiManagerBridge')(), 'axi_bridge')


def test_pooled_manager_rejects_b_before_final_w(native_bridge, capfd):
    inst = native_bridge.instance()
    p = inst.ports
    p.clk = False
    p.bus.rvalid = p.bus.bvalid = False
    p.bus.arready = p.bus.awready = True
    p.bus.wready = False  # The single W beat stays in the W register.
    p.read_request.valid = p.write_request.valid = p.write_data.valid = False
    p.read_token.ready = p.write_token.ready = True
    p.read_result.ready = p.write_result.ready = True
    p.rst = True
    tick(inst)
    tick(inst)  # khdl#45: the first edge after creation is missed; reset needs two
    p.rst = False
    fields = dict(id=3, addr=0, len=0, size=2, burst=1, lock=False,
                  cache=0, prot=0, qos=0, region=0, user=0)
    p.write_request.data = inst.info_ports.write_request.data.type.new(**fields)
    p.write_request.valid = True
    p.write_data.data = inst.info_ports.write_data.data.type.new(token=0, data=0, strb=15, user=0)
    p.write_data.valid = True
    tick(inst)
    p.write_request.valid = p.write_data.valid = False
    for _ in range(4):
        tick(inst)
    p.bus.b = inst.info_ports.bus.b.type.new(id=3, resp=0, user=0)
    p.bus.bvalid = True
    with pytest.raises(khdl.SimulationFinishedException):
        for _ in range(8):
            tick(inst)
    ctypes.CDLL(None).fflush(None)
    assert 'before final W transfer' in capfd.readouterr().out


def test_composed_endpoints_forward_without_an_extra_cycle(native_endpoints):
    inst = native_endpoints.instance()
    p = inst.ports
    p.clk = False
    p.bus.rvalid = p.bus.bvalid = False
    p.bus.arready = p.bus.awready = p.bus.wready = True
    for i in range(2):
        for side in ('read', 'write'):
            p[f'{side}{i}_request'].valid = False
            p[f'{side}{i}_result'].ready = True
        p[f'write{i}_data'].valid = False
    p.rst = True
    tick(inst)
    tick(inst)  # khdl#45: the first edge after creation is missed; reset needs two
    p.rst = False
    for i in range(2):
        address = dict(id=15, addr=64+64*i, len=0, size=2, burst=1, lock=False,
                       cache=0, prot=0, qos=0, region=0, user=0)
        for side in ('read', 'write'):
            name = f'{side}{i}_request'
            p[name].data = inst.info_ports[name].data.type.new(**address)
            p[name].valid = True
        name = f'write{i}_data'
        p[name].data = inst.info_ports[name].data.type.new(data=123+i, strb=15, user=0)
        p[name].valid = True
    tick(inst)
    # The calling process captured the input and filled the physical output
    # registers at the same edge; their first transfer can occur next edge.
    assert p.bus.arvalid and p.bus.ar.id == 1
    assert p.bus.awvalid and p.bus.aw.id == 1
    assert p.bus.wvalid and p.bus.w.data == 123 and p.bus.w.last
    for i in range(2):
        p[f'read{i}_request'].valid = p[f'write{i}_request'].valid = p[f'write{i}_data'].valid = False
    tick(inst)
    # Both endpoints accepted local work; rotation advances to the second
    # endpoint, and W follows the actual selected AW order.
    assert p.bus.arvalid and p.bus.ar.id == 5
    assert p.bus.awvalid and p.bus.aw.id == 5
    assert p.bus.wvalid and p.bus.w.data == 124 and p.bus.w.last
    tick(inst)
    assert not p.bus.arvalid and not p.bus.awvalid and not p.bus.wvalid


@pytest.fixture(scope='module')
def native_arbitration(simulator_build_dirs):
    source = khdl.Source.new_from_manifest_path(str(MANIFEST))
    probe = Path(__file__).parent / 'rtl/arbitration.kh'
    source.add_file_content(['arbitration_probe'], str(probe), probe.read_text())
    module = source.compile().resolve('arbitration_probe.ArbitrationProbe')()
    return build_simulator(module, 'axi_arbitration')


def arbitration_instance(compiled):
    inst = compiled.instance()
    p = inst.ports
    p.clk = False
    p.read_enable = p.write_enable = [False] * 3
    p.bus.rvalid = p.bus.bvalid = False
    p.bus.arready = p.bus.awready = p.bus.wready = False
    p.rst = True
    tick(inst)
    tick(inst)  # khdl#45: the first edge after creation is missed; reset needs two
    p.rst = False
    inst.step(1)
    return inst


@pytest.mark.parametrize('stutter', [False, True])
@pytest.mark.parametrize('read_mask,write_mask,read_order,write_order', [
    ([True, True, True], [True, True, True], [1, 5, 9] * 6, [1, 5, 9] * 6),
    ([True, False, True], [False, True, False], [1, 9] * 6, [5] * 5),
])
def test_endpoint_round_robin_contention(native_arbitration, stutter,
                                         read_mask, write_mask, read_order, write_order):
    inst = arbitration_instance(native_arbitration)
    p = inst.ports
    rng = random.Random(982)
    observed = {'ar': [], 'aw': [], 'w': []}
    held = {}
    for cycle in range(1000):
        p.read_enable, p.write_enable = read_mask, write_mask
        for channel, target in [('ar', read_order), ('aw', write_order), ('w', write_order)]:
            # Freeze a completed channel while its independently stalled peers catch up.
            p.bus[channel + 'ready'] = len(observed[channel]) < len(target) and (
                not stutter or bool(rng.randrange(3)))
        inst.step(1)
        for channel in observed:
            valid, ready = p.bus[channel + 'valid'], p.bus[channel + 'ready']
            value = p.bus[channel] if valid else None
            if channel in held:
                assert valid and value == held[channel], (channel, cycle)
            if valid and not ready:
                held[channel] = value
            else:
                held.pop(channel, None)
            if valid and ready:
                observed[channel].append(value.data if channel == 'w' else value.id)
        tick(inst)
        if len(observed['ar']) == len(read_order) and len(observed['aw']) == len(write_order) and len(observed['w']) == len(write_order):
            break
    else:
        pytest.fail(f'Arbitration timed out: {observed}')
    assert observed['ar'] == read_order
    assert observed['aw'] == write_order
    assert observed['w'] == [[1, 5, 9].index(id_) for id_ in write_order]


def test_endpoint_round_robin_idle_stall_and_reset(native_arbitration):
    inst = arbitration_instance(native_arbitration)
    p = inst.ports
    # Each direction serves a different endpoint, even with its output stalled.
    p.read_enable, p.write_enable = [False, True, False], [False, False, True]
    tick(inst)
    assert p.bus.arvalid and p.bus.ar.id == 5
    assert p.bus.awvalid and p.bus.aw.id == 9
    p.read_enable = p.write_enable = [False] * 3
    for _ in range(5):
        tick(inst)
        assert p.bus.ar.id == 5 and p.bus.aw.id == 9
    p.bus.arready = p.bus.awready = p.bus.wready = True
    for _ in range(5):
        tick(inst)
        assert not p.bus.arvalid and not p.bus.awvalid
    # Empty cycles and stalls did not rotate/reset either arbiter.
    p.read_enable = p.write_enable = [True] * 3
    tick(inst)
    assert p.bus.arvalid and p.bus.ar.id == 9
    assert p.bus.awvalid and p.bus.aw.id == 1
    # Reset with work pending discards it and restores endpoint zero's priority.
    p.bus.arready = p.bus.awready = p.bus.wready = False
    p.rst = True
    tick(inst)
    tick(inst)  # khdl#45: the first edge after creation is missed; reset needs two
    assert not p.bus.arvalid and not p.bus.awvalid
    p.rst = False
    tick(inst)
    assert p.bus.arvalid and p.bus.ar.id == 1
    assert p.bus.awvalid and p.bus.aw.id == 1
