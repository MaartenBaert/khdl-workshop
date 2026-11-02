"""Independent AXI peers around a KHDL pipeline (loaded by cocotb)."""
import itertools

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, FallingEdge, Timer
from cocotbext.axi import AxiBus, AxiMaster, AxiRam
from cocotbext.axi.axi_channels import (
    AxiAWSource, AxiAWTransaction, AxiWSource, AxiWTransaction, AxiBSink,
    AxiARSource, AxiARTransaction, AxiRSink,
)


async def reset(dut):
    dut.aresetn.value = 0
    await ClockCycles(dut.clk, 3)
    dut.aresetn.value = 1
    await ClockCycles(dut.clk, 3)


def pause_master(master):
    for channel in [master.write_if.aw_channel, master.write_if.w_channel,
                    master.read_if.ar_channel, master.write_if.b_channel,
                    master.read_if.r_channel]:
        channel.set_pause_generator(itertools.cycle([0, 1, 0, 0, 1]))


@cocotb.test(timeout_time=100, timeout_unit='us')
async def pipeline(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    master = AxiMaster(AxiBus.from_prefix(dut, 's_axi'), dut.clk, dut.aresetn,
                       reset_active_level=False,
                       max_burst_len=16)
    ram = AxiRam(AxiBus.from_prefix(dut, 'm_axi'), dut.clk, dut.aresetn,
                 reset_active_level=False, size=65536)
    await reset(dut)
    # Independently stall requests, data and responses on both sides.
    for channel in [master.write_if.aw_channel, master.write_if.w_channel,
                    master.read_if.ar_channel, ram.write_if.b_channel,
                    ram.read_if.r_channel]:
        channel.set_pause_generator(itertools.cycle([0, 1, 0, 0, 1]))
    for channel in [master.write_if.b_channel, master.read_if.r_channel,
                    ram.write_if.aw_channel, ram.write_if.w_channel,
                    ram.read_if.ar_channel]:
        channel.set_pause_generator(itertools.cycle([1, 0, 0, 0]))
    for address, length in [(0, 1), (3, 29), (4093, 80), (8192, 1025)]:
        data = bytes((i * 53 + address) % 256 for i in range(length))
        result = await master.write(address, data)
        assert result.resp == 0
        assert ram.read(address, length) == data
        response = await master.read(address, length)
        assert response.data == data and response.resp == 0
    await master.wait()


@cocotb.test(timeout_time=200, timeout_unit='us')
async def ram(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    master = AxiMaster(AxiBus.from_prefix(dut, 's_axi'), dut.clk, dut.aresetn,
                       reset_active_level=False,
                       max_burst_len=256)
    await reset(dut)
    pause_master(master)
    for address, length in [(0, 1), (3, 29), (4093, 80), (1024, 2049)]:
        data = bytes((i * 53 + address) % 256 for i in range(length))
        assert (await master.write(address, data)).resp == 0
        response = await master.read(address, length)
        assert response.data == data and response.resp == 0
    # Several IDs with concurrent independent reads and writes. Same-ID order
    # also matters: reusing one ID deliberately exercises context association.
    operations = []
    for i in range(8):
        data = bytes([i + 1]) * 37
        operations.append(master.init_write(5000 + 64 * i, data, awid=i % 3))
    for operation in operations:
        await operation.wait()
        assert operation.data.resp == 0
    operations = [master.init_read(5000 + 64 * i, 37, arid=i % 3) for i in range(8)]
    for i, operation in enumerate(operations):
        await operation.wait()
        assert operation.data.data == bytes([i + 1]) * 37
    assert (await master.read(8192, 16)).resp == 3
    assert (await master.write(8192, b'out of range')).resp == 3
    await reset(dut)
    assert (await master.write(64, b'after reset')).resp == 0
    assert (await master.read(64, 11)).data == b'after reset'


@cocotb.test(timeout_time=100, timeout_unit='us')
async def ram_bounds(dut):
    """A beat beyond the six-byte memory reads and writes no bytes at all."""
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    master = AxiMaster(AxiBus.from_prefix(dut, 's_axi'), dut.clk, dut.aresetn,
                       reset_active_level=False, max_burst_len=256)
    await reset(dut)
    assert (await master.write(4, b'\x11\x22', size=1)).resp == 0
    # Bytes 4..5 are in range, but the second full-width beat addresses 4..7.
    assert (await master.write(0, bytes(range(1, 9)))).resp == 3
    response = await master.read(4, 2, size=1)
    assert response.resp == 0 and response.data == b'\x11\x22'
    response = await master.read(0, 8)
    assert response.resp == 3 and response.data[:4] == bytes(range(1, 5))


@cocotb.test(timeout_time=100, timeout_unit='us')
async def error(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    master = AxiMaster(AxiBus.from_prefix(dut, 's_axi'), dut.clk, dut.aresetn,
                       reset_active_level=False,
                       max_burst_len=256)
    await reset(dut)
    pause_master(master)
    for length in [1, 4, 37, 1024]:
        assert (await master.write(0, bytes(length))).resp == 3
        assert (await master.read(0, length)).resp == 3


async def send(dut, prefix, values, axi=False):
    """Hold a source until handshake; sample in the stable half-cycle."""
    suffix = '' if axi else '_'
    valid = getattr(dut, prefix + suffix + 'valid')
    ready = getattr(dut, prefix + suffix + 'ready')
    data = getattr(dut, prefix + suffix + 'data')
    for value in values:
        await FallingEdge(dut.clk)
        data.value = value
        valid.value = 1
        while True:
            await Timer(1, unit='ns')
            accepted = int(ready.value)
            await RisingEdge(dut.clk)
            if accepted:
                break
            await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    valid.value = 0


async def receive(dut, prefix, count):
    ready = getattr(dut, prefix + '_ready')
    valid = getattr(dut, prefix + '_valid')
    data = getattr(dut, prefix + '_data')
    result = []
    while len(result) < count:
        await FallingEdge(dut.clk)
        ready.value = 1
        await Timer(1, unit='ns')
        if int(valid.value):
            result.append(int(data.value))
        await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    ready.value = 0
    return result


async def drive_axi(dut, prefix, payloads):
    for payload in payloads:
        await FallingEdge(dut.clk)
        for name, value in payload.items():
            getattr(dut, prefix+name).value = value
        getattr(dut, prefix+'valid').value = 1
        while True:
            await Timer(1, unit='ns')
            accepted = int(getattr(dut, prefix+'ready').value)
            await RisingEdge(dut.clk)
            if accepted:
                break
            await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    getattr(dut, prefix+'valid').value = 0


def address_payload(address, count=1, id=0, size=2):
    # Independent schema: ID[3:0], ADDR[35:4], LEN[43:36], SIZE[46:44], BURST[48:47].
    return id | address << 4 | (count - 1) << 36 | size << 44 | 1 << 47


def initialize_application(dut):
    for name in ['read_request', 'write_request', 'write_data']:
        getattr(dut, name + '_valid').value = 0
        getattr(dut, name + '_data').value = 0
    for name in ['read_token', 'write_token', 'read_result', 'write_result']:
        getattr(dut, name + '_ready').value = 0


@cocotb.test(timeout_time=100, timeout_unit='us')
async def manager_ram(dut):
    initialize_application(dut)
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    ram_model = AxiRam(AxiBus.from_prefix(dut, 'm_axi'), dut.clk, dut.aresetn,
                       reset_active_level=False, size=65536)
    for channel in [ram_model.write_if.aw_channel, ram_model.write_if.w_channel,
                    ram_model.write_if.b_channel, ram_model.read_if.ar_channel,
                    ram_model.read_if.r_channel]:
        channel.set_pause_generator(itertools.cycle([1, 0, 0, 1, 0]))
    await reset(dut)
    for iteration in range(8):
        address = 64 + 32 * iteration
        request = address_payload(address, 4, id=iteration % 3)
        ack = cocotb.start_soon(receive(dut, 'write_token', 1))
        await send(dut, 'write_request', [request])
        token = (await ack)[0]
        words = [0x12340000 + iteration * 16 + i for i in range(4)]
        completion = cocotb.start_soon(receive(dut, 'write_result', 1))
        await send(dut, 'write_data', [token | word << 2 | 15 << 34 for word in words])
        assert (await completion)[0] == token  # OKAY in bits [3:2]
        assert ram_model.read(address, 16) == b''.join(w.to_bytes(4, 'little') for w in words)
        ack = cocotb.start_soon(receive(dut, 'read_token', 1))
        await send(dut, 'read_request', [request])
        token = (await ack)[0]
        responses = await receive(dut, 'read_result', 4)
        for i, result in enumerate(responses):
            assert result & 3 == token
            assert (result >> 2) & 0xffffffff == words[i]
            assert (result >> 34) & 3 == 0
            assert bool((result >> 36) & 1) == (i == 3)


@cocotb.test(timeout_time=100, timeout_unit='us')
async def manager_reordered(dut):
    initialize_application(dut)
    for channel in ['aw', 'w', 'ar']:
        getattr(dut, 'm_axi_' + channel + 'ready').value = 1
    for channel in ['r', 'b']:
        getattr(dut, 'm_axi_' + channel + 'valid').value = 0
    dut.m_axi_rid.value = 0
    dut.m_axi_rdata.value = 0
    dut.m_axi_rresp.value = 0
    dut.m_axi_rlast.value = 1
    dut.m_axi_bid.value = 0
    dut.m_axi_bresp.value = 0
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    await reset(dut)
    # Receive responses for three IDs in reverse order, then reuse all slots.
    for iteration in range(3):
        ack = cocotb.start_soon(receive(dut, 'read_token', 3))
        await send(dut, 'read_request', [address_payload(64*i, id=i) for i in range(3)])
        tokens = await ack
        results = cocotb.start_soon(receive(dut, 'read_result', 3))
        await ClockCycles(dut.clk, 4)
        for i in reversed(range(3)):
            dut.m_axi_rid.value = i
            dut.m_axi_rdata.value = 100 + i
            dut.m_axi_rresp.value = [0, 2, 3][i]  # preserve response errors too
            await send(dut, 'm_axi_r', [100 + i], axi=True)
        received = await results
        for i, result in zip(reversed(range(3)), received):
            assert result & 3 == tokens[i]
            assert (result >> 2) & 0xffffffff == 100 + i
            assert (result >> 34) & 3 == [0, 2, 3][i]
    # Interleave two-beat reads, then rotate the non-power-of-two context ring
    # and reuse one ID for two different outstanding requests.
    for ids, count, order in [((0, 1), 2, [1, 0, 1, 0]), ((1, 1, 2), 1, [2, 0, 1])]:
        requests = cocotb.start_soon(observe(dut, 'm_axi_ar', len(ids), ['id']))
        ack = cocotb.start_soon(receive(dut, 'read_token', len(ids)))
        await send(dut, 'read_request', [address_payload(64*i, count=count, id=id) for i, id in enumerate(ids)])
        tokens = await ack
        assert [row['id'] for _, row in await requests] == list(ids)
        results = cocotb.start_soon(receive(dut, 'read_result', len(order)))
        beats = [0] * len(ids)
        payloads = []
        for i in order:
            payloads.append(dict(id=ids[i], data=100+i, resp=0, last=beats[i] == count-1))
            beats[i] += 1
        await drive_axi(dut, 'm_axi_r', payloads)
        received = await results
        for i, result, payload in zip(order, received, payloads):
            assert result & 3 == tokens[i]
            assert (result >> 2) & 0xffffffff == 100+i
            assert bool((result >> 36) & 1) == payload['last']
    for ids, order in [((0, 1, 2), [2, 1, 0]), ((1, 1, 2), [2, 0, 1])]:
        requests = cocotb.start_soon(observe(dut, 'm_axi_aw', 3, ['id']))
        ack = cocotb.start_soon(receive(dut, 'write_token', 3))
        await send(dut, 'write_request', [address_payload(64*i, id=id) for i, id in enumerate(ids)])
        tokens = await ack
        assert [row['id'] for _, row in await requests] == list(ids)
        writes = cocotb.start_soon(observe(dut, 'm_axi_w', 3, ['last']))
        await send(dut, 'write_data', [token | (i+100) << 2 | 15 << 34 for i, token in enumerate(tokens)])
        assert all(row['last'] for _, row in await writes)
        results = cocotb.start_soon(receive(dut, 'write_result', 3))
        payloads = [dict(id=ids[i], resp=[0, 2, 3][i]) for i in order]
        await drive_axi(dut, 'm_axi_b', payloads)
        for i, result in zip(order, await results):
            assert result & 3 == tokens[i]
            assert result >> 2 == [0, 2, 3][i]


@cocotb.test(timeout_time=200, timeout_unit='us')
async def copy(dut):
    dut.command_valid.value = 0
    dut.command_data.value = 0
    dut.result_ready.value = 0
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    ram_model = AxiRam(AxiBus.from_prefix(dut, 'm_axi'), dut.clk, dut.aresetn,
                       reset_active_level=False, size=65536)
    for channel in [ram_model.write_if.aw_channel, ram_model.write_if.w_channel,
                    ram_model.write_if.b_channel, ram_model.read_if.ar_channel,
                    ram_model.read_if.r_channel]:
        channel.set_pause_generator(itertools.cycle([0, 1, 0, 0]))
    await reset(dut)
    for tag, (source, destination, length) in enumerate([
        (0, 16384, 0), (64, 16384, 4), (4000, 20000, 2400), (8000, 32000, 8192),
    ]):
        data = bytes((i * 17 + tag) % 256 for i in range(length))
        ram_model.write(source, data)
        ram_model.write(destination, bytes(length))
        result = cocotb.start_soon(receive(dut, 'result', 1))
        await send(dut, 'command', [source | destination << 32 | length << 64 | tag << 97])
        assert (await result)[0] == tag
        assert ram_model.read(destination, length) == data

    # Independent peer faults: finish every issued burst, suppress failed read
    # lanes, report the error, then accept a fresh command without resetting.
    read_backend = ram_model.read_if._read
    write_backend = ram_model.write_if._write
    for tag, read_fault in [(10, True), (11, False)]:
        source, destination, length = 128, 24000, 16
        data = bytes(range(16))
        ram_model.write(source, data)
        ram_model.write(destination, bytes([0xa5]) * length)
        async def failing_read(address, count):
            if address == source + 4:
                raise IOError('injected read failure')
            return await read_backend(address, count)
        async def failing_write(address, value):
            if address == destination + 4:
                raise IOError('injected write failure')
            await write_backend(address, value)
        ram_model.read_if._read = failing_read if read_fault else read_backend
        ram_model.write_if._write = write_backend if read_fault else failing_write
        result = cocotb.start_soon(receive(dut, 'result', 1))
        await send(dut, 'command', [source | destination << 32 | length << 64 | tag << 97])
        assert (await result)[0] == tag | (2 << 8)
        assert ram_model.read(destination, length) == data[:4] + bytes([0xa5])*4 + data[8:]
    ram_model.read_if._read = read_backend
    ram_model.write_if._write = write_backend
    result = cocotb.start_soon(receive(dut, 'result', 1))
    await send(dut, 'command', [128 | 24000 << 32 | 16 << 64 | 12 << 97])
    assert (await result)[0] == 12
    assert ram_model.read(24000, 16) == bytes(range(16))


@cocotb.test(timeout_time=200, timeout_unit='us')
async def router(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    master = AxiMaster(AxiBus.from_prefix(dut, 's_axi'), dut.clk, dut.aresetn,
                       reset_active_level=False, max_burst_len=16)
    memories = [AxiRam(AxiBus.from_prefix(dut, f'm{i}_axi'), dut.clk, dut.aresetn,
                       reset_active_level=False, size=4096) for i in range(2)]
    for i, model in enumerate(memories):
        for channel in [model.write_if.aw_channel, model.write_if.w_channel,
                        model.write_if.b_channel, model.read_if.ar_channel,
                        model.read_if.r_channel]:
            channel.set_pause_generator(itertools.cycle([i, 1, 0, 0, 1, 0]))
    await reset(dut)
    pause_master(master)
    for address, length in [(0, 29), (4093, 64), (5000, 1025)]:
        data = bytes((i * 17 + address) % 256 for i in range(length))
        assert (await master.write(address, data)).resp == 0
        response = await master.read(address, length)
        assert response.resp == 0 and response.data == data
        for i, byte in enumerate(data):
            absolute = address + i
            assert memories[absolute // 4096].read(absolute % 4096, 1) == bytes([byte])
    operations = []
    for i in range(9):
        operations.append(master.init_write((i % 2) * 4096 + 128*i,
                                            bytes([i]) * 31, awid=i % 3))
    for operation in operations:
        await operation.wait()
        assert operation.data.resp == 0
    operations = [master.init_read((i % 2) * 4096 + 128*i, 31, arid=i % 3) for i in range(9)]
    for i, operation in enumerate(operations):
        await operation.wait()
        assert operation.data.data == bytes([i]) * 31
    assert (await master.write(8192, bytes(1024))).resp == 3
    assert (await master.read(8192, 1024)).resp == 3


async def observe(dut, prefix, count, fields=()):
    result = []
    cycle = 0
    while len(result) < count:
        await FallingEdge(dut.clk)
        await Timer(1, unit='ns')
        if int(getattr(dut, prefix+'valid').value) and int(getattr(dut, prefix+'ready').value):
            result.append((cycle, {name: int(getattr(dut, prefix+name).value) for name in fields}))
        await RisingEdge(dut.clk)
        cycle += 1
    return result


@cocotb.test(timeout_time=100, timeout_unit='us')
async def ram_rate(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    master = AxiMaster(AxiBus.from_prefix(dut, 's_axi'), dut.clk, dut.aresetn,
                       reset_active_level=False, max_burst_len=256)
    await reset(dut)
    bus_bytes = len(dut.s_axi_wstrb)
    count = 64
    data = bytes(i % 256 for i in range(count * bus_bytes))
    writes = cocotb.start_soon(observe(dut, 's_axi_w', count))
    assert (await master.write(0, data)).resp == 0
    cycles = [cycle for cycle, _ in await writes]
    # The initial skid fill can pause before the request reaches its queue;
    # once servicing starts, write data must sustain one transfer every cycle.
    assert all(b-a == 1 for a, b in zip(cycles[1:], cycles[2:]))
    reads = cocotb.start_soon(observe(dut, 's_axi_r', count))
    assert (await master.read(0, len(data))).data == data
    cycles = [cycle for cycle, _ in await reads]
    assert all(b-a == 1 for a, b in zip(cycles, cycles[1:]))


@cocotb.test(timeout_time=100, timeout_unit='us')
async def raw_ram(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    bus = AxiBus.from_prefix(dut, 's_axi')
    args = dict(reset_active_level=False)
    aw = AxiAWSource(bus.write.aw, dut.clk, dut.aresetn, **args)
    w = AxiWSource(bus.write.w, dut.clk, dut.aresetn, **args)
    b = AxiBSink(bus.write.b, dut.clk, dut.aresetn, **args)
    ar = AxiARSource(bus.read.ar, dut.clk, dut.aresetn, **args)
    r = AxiRSink(bus.read.r, dut.clk, dut.aresetn, **args)
    await reset(dut)
    bus_bytes = len(dut.s_axi_wstrb)
    bus_size = bus_bytes.bit_length()-1
    memory = {}
    cases = [(0, min(bus_size, 1), 4, 65), (2, min(bus_size, 2), 4, 128),
             (1, 0, 7, 243), (1, bus_size, 5, 515)]
    for case, (burst, size, count, start) in enumerate(cases):
        beat_bytes = 1 << size
        if burst == 2:
            start += 2*beat_bytes  # aligned, but starts halfway through the wrap span
        addresses, masks, payloads = [], [], []
        for beat in range(count):
            address = start
            if burst == 1 and beat:
                address = start//beat_bytes*beat_bytes + beat*beat_bytes
            elif burst == 2:
                span = count*beat_bytes
                address = start//span*span + (start + beat*beat_bytes) % span
            lower = address % bus_bytes
            upper = lower - address % beat_bytes + beat_bytes
            mask = sum(1 << lane for lane in range(lower, upper))
            data = sum(((case*29 + beat*7 + lane) % 256) << (8*lane) for lane in range(bus_bytes))
            addresses.append(address); masks.append(mask); payloads.append(data)
            for lane in range(lower, upper):
                memory[address//bus_bytes*bus_bytes + lane] = (data >> (8*lane)) & 255
        # Present W first and leave AW absent for several cycles. The first W
        # beat must be held safely, and no write response may be generated yet.
        for beat, (mask, data) in enumerate(zip(masks, payloads)):
            await w.send(AxiWTransaction(wdata=data, wstrb=mask, wlast=beat == count-1))
        await ClockCycles(dut.clk, 4)
        assert b.empty(), 'response emitted before AW'
        await aw.send(AxiAWTransaction(awid=case, awaddr=start, awlen=count-1,
                                      awsize=size, awburst=burst))
        response = await b.recv()
        assert response.bid == case and response.bresp == 0
        # All-zero strobes still require a complete burst and a response, but
        # must not modify any byte previously written.
        await aw.send(AxiAWTransaction(awid=case, awaddr=start, awlen=count-1,
                                      awsize=size, awburst=burst))
        for beat in range(count):
            await w.send(AxiWTransaction(wdata=0, wstrb=0, wlast=beat == count-1))
        assert (await b.recv()).bresp == 0
        await ar.send(AxiARTransaction(arid=case, araddr=start, arlen=count-1,
                                      arsize=size, arburst=burst))
        for beat, (address, mask) in enumerate(zip(addresses, masks)):
            response = await r.recv()
            assert response.rid == case and response.rresp == 0
            assert bool(response.rlast) == (beat == count-1)
            for lane in range(bus_bytes):
                if mask & (1 << lane):
                    assert (int(response.rdata) >> (8*lane)) & 255 == memory[address//bus_bytes*bus_bytes + lane]
    # The RAM intentionally has no exclusive monitor: it reports OKAY and
    # performs exclusive writes as ordinary writes, as permitted by AXI4.
    await ar.send(AxiARTransaction(arid=0, araddr=256, arlen=0, arsize=bus_size,
                                  arburst=1, arlock=1))
    assert (await r.recv()).rresp == 0
    await aw.send(AxiAWTransaction(awid=0, awaddr=256, awlen=0, awsize=bus_size,
                                  awburst=1, awlock=1))
    await w.send(AxiWTransaction(wdata=91, wstrb=(1 << bus_bytes)-1, wlast=1))
    assert (await b.recv()).bresp == 0
    await ar.send(AxiARTransaction(arid=0, araddr=256, arlen=0, arsize=bus_size, arburst=1))
    assert (await r.recv()).rdata == 91


@cocotb.test(timeout_time=100, timeout_unit='us')
async def subordinate_reordered(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    master = AxiMaster(AxiBus.from_prefix(dut, 's_axi'), dut.clk, dut.aresetn,
                       reset_active_level=False, max_burst_len=1)
    await reset(dut)
    for ids in [(0, 1, 2), (1, 1, 2)]:
        read_trace = cocotb.start_soon(observe(dut, 's_axi_r', 3, ['id', 'data']))
        write_trace = cocotb.start_soon(observe(dut, 's_axi_b', 3, ['id']))
        reads, writes = [], []
        for i, id in enumerate(ids):
            address = 64 * (i+1)
            reads.append(master.init_read(address, 4, arid=id))
            writes.append(master.init_write(address, address.to_bytes(4, 'little'), awid=id))
        for i, operation in enumerate(reads):
            await operation.wait()
            assert operation.data.data == (64*(i+1)).to_bytes(4, 'little')
        for operation in writes:
            await operation.wait()
            assert operation.data.resp == 0
        read_rows = [value for _, value in await read_trace]
        write_rows = [value for _, value in await write_trace]
        order = [2, 1, 0] if ids == (0, 1, 2) else [2, 0, 1]
        assert [row['id'] for row in read_rows] == [ids[i] for i in order]
        assert [row['data'] for row in read_rows] == [64*(i+1) for i in order]
        assert [row['id'] for row in write_rows] == [ids[i] for i in order]


def initialize_endpoints(dut):
    for i in range(2):
        for kind in ('request', 'data'):
            for side in ('read', 'write') if kind == 'request' else ('write',):
                prefix = f'{side}{i}_{kind}'
                getattr(dut, prefix + '_valid').value = 0
                getattr(dut, prefix + '_data').value = 0
        for side in ('read', 'write'):
            getattr(dut, f'{side}{i}_result_ready').value = 0


@cocotb.test(timeout_time=100, timeout_unit='us')
async def endpoints(dut):
    initialize_endpoints(dut)
    for channel in ['aw', 'w', 'ar']:
        getattr(dut, 'm_axi_' + channel + 'ready').value = 1
    for channel in ['r', 'b']:
        getattr(dut, 'm_axi_' + channel + 'valid').value = 0
    dut.m_axi_rid.value = 0
    dut.m_axi_rdata.value = 0
    dut.m_axi_rresp.value = 0
    dut.m_axi_rlast.value = 1
    dut.m_axi_bid.value = 0
    dut.m_axi_bresp.value = 0
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    await reset(dut)

    # An older endpoint-0 read remains outstanding while endpoint 1 fills its
    # three-slot ring, completes, and reuses it repeatedly. No shared retirement.
    older = cocotb.start_soon(observe(dut, 'm_axi_ar', 1, ['id', 'addr']))
    await send(dut, 'read0_request', [address_payload(64, id=15)])
    assert (await older)[0][1]['id'] == 1  # configuration owns the wire ID
    for iteration in range(3):
        requests = cocotb.start_soon(observe(dut, 'm_axi_ar', 3, ['id', 'addr', 'len']))
        await send(dut, 'read1_request', [address_payload(128+64*i, count=2, id=0) for i in range(3)])
        assert [row['id'] for _, row in await requests] == [5]*3
        result = cocotb.start_soon(receive(dut, 'read1_result', 6))
        await drive_axi(dut, 'm_axi_r', [dict(id=5, data=100*iteration+j,
            resp=2 if j == 2 else 0, last=j % 2 == 1) for j in range(6)])
        values = await result
        for j, value in enumerate(values):
            assert value & 0xffffffff == 100*iteration+j
            assert (value >> 32) & 3 == (2 if j == 2 else 0)
            assert bool((value >> 34) & 1) == (j % 2 == 1)
    result = cocotb.start_soon(receive(dut, 'read0_result', 1))
    await drive_axi(dut, 'm_axi_r', [dict(id=1, data=91, resp=0, last=1)])
    assert (await result)[0] & 0xffffffff == 91

    # Interleaved read bursts advance their own endpoint's beat counters.
    for i in range(2):
        request = cocotb.start_soon(observe(dut, 'm_axi_ar', 1, ['id']))
        await send(dut, f'read{i}_request', [address_payload(256+64*i, count=2)])
        await request
    results = [cocotb.start_soon(receive(dut, f'read{i}_result', 2)) for i in range(2)]
    await drive_axi(dut, 'm_axi_r', [dict(id=id, data=data, resp=0, last=last)
        for id, data, last in [(5, 50, 0), (1, 10, 0), (5, 51, 1), (1, 11, 1)]])
    for i, result in enumerate(results):
        values = await result
        assert [v & 0xffffffff for v in values] == ([10, 11] if i == 0 else [50, 51])
        assert [bool((v >> 34) & 1) for v in values] == [False, True]

    # Both endpoints have data ready, but W must follow actual AW enqueue order.
    # The first B is withheld while the second endpoint's W and B progress.
    aw = cocotb.start_soon(observe(dut, 'm_axi_aw', 2, ['id', 'addr', 'len']))
    await send(dut, 'write0_request', [address_payload(512, count=2)])
    await send(dut, 'write1_request', [address_payload(1024, count=2)])
    assert [row['id'] for _, row in await aw] == [1, 5]
    writes = cocotb.start_soon(observe(dut, 'm_axi_w', 4, ['data', 'last', 'strb']))
    later = cocotb.start_soon(send(dut, 'write1_data', [200 | 15 << 32, 201 | 15 << 32]))
    await ClockCycles(dut.clk, 5)
    assert not int(dut.m_axi_wvalid.value), 'later endpoint overtook earlier W obligation'
    await send(dut, 'write0_data', [100 | 15 << 32, 101 | 15 << 32])
    await later
    rows = [row for _, row in await writes]
    assert [row['data'] for row in rows] == [100, 101, 200, 201]
    assert [row['last'] for row in rows] == [0, 1, 0, 1]
    result = cocotb.start_soon(receive(dut, 'write1_result', 1))
    await drive_axi(dut, 'm_axi_b', [dict(id=5, resp=2)])
    assert await result == [2]
    for iteration in range(2):
        aw = cocotb.start_soon(observe(dut, 'm_axi_aw', 3, ['id']))
        await send(dut, 'write1_request', [address_payload(1280+64*i) for i in range(3)])
        assert [row['id'] for _, row in await aw] == [5]*3
        writes = cocotb.start_soon(observe(dut, 'm_axi_w', 3, ['data', 'last']))
        await send(dut, 'write1_data', [(300+i) | 15 << 32 for i in range(3)])
        assert all(row['last'] for _, row in await writes)
        result = cocotb.start_soon(receive(dut, 'write1_result', 3))
        await drive_axi(dut, 'm_axi_b', [dict(id=5, resp=0) for _ in range(3)])
        assert await result == [0]*3
    result = cocotb.start_soon(receive(dut, 'write0_result', 1))
    await drive_axi(dut, 'm_axi_b', [dict(id=1, resp=0)])
    assert await result == [0]

    # A parked endpoint-0 result does not prevent routing endpoint-1 data into
    # its own buffering. Reset cancels both parked results and live contexts.
    for i in range(2):
        request = cocotb.start_soon(observe(dut, 'm_axi_ar', 1, ['id']))
        await send(dut, f'read{i}_request', [address_payload(2000+64*i)])
        await request
    await drive_axi(dut, 'm_axi_r', [dict(id=1, data=11, resp=0, last=1)])
    # The first result parks in the facade's output register. A second one
    # fills the Manager endpoint's own response slot while that output stalls.
    request = cocotb.start_soon(observe(dut, 'm_axi_ar', 1, ['id']))
    await send(dut, 'read0_request', [address_payload(3000)])
    await request
    await drive_axi(dut, 'm_axi_r', [dict(id=1, data=12, resp=0, last=1), dict(id=5, data=22, resp=0, last=1)])
    result = await receive(dut, 'read1_result', 1)
    assert result[0] & 0xffffffff == 22
    await reset(dut)
    assert not int(dut.read0_result_valid.value)
    request = cocotb.start_soon(observe(dut, 'm_axi_ar', 1, ['id']))
    await send(dut, 'read0_request', [address_payload(0)])
    await request
    result = cocotb.start_soon(receive(dut, 'read0_result', 1))
    await drive_axi(dut, 'm_axi_r', [dict(id=1, data=33, resp=0, last=1)])
    assert (await result)[0] & 0xffffffff == 33


@cocotb.test(timeout_time=200, timeout_unit='us')
async def endpoints_ram(dut):
    initialize_endpoints(dut)
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    ram = AxiRam(AxiBus.from_prefix(dut, 'm_axi'), dut.clk, dut.aresetn,
                 reset_active_level=False, size=65536)
    for channel in [ram.write_if.aw_channel, ram.write_if.w_channel,
                    ram.write_if.b_channel, ram.read_if.ar_channel, ram.read_if.r_channel]:
        channel.set_pause_generator(itertools.cycle([0, 1, 0, 0, 1]))
    await reset(dut)
    count, beats = 5, 4
    tasks = []
    completions = []
    for i in range(2):
        addresses = [4096*i + 64*j for j in range(count)]
        values = [1000*i+j for j in range(count*beats)]
        completions.append(cocotb.start_soon(receive(dut, f'write{i}_result', count)))
        tasks.append(cocotb.start_soon(send(dut, f'write{i}_request', [address_payload(a, count=beats) for a in addresses])))
        tasks.append(cocotb.start_soon(send(dut, f'write{i}_data', [v | 15 << 32 for v in values])))
    for task in tasks:
        await task
    for result in completions:
        assert await result == [0]*count
    for i in range(2):
        for j in range(count):
            expected = b''.join((1000*i+j*beats+k).to_bytes(4, 'little') for k in range(beats))
            assert ram.read(4096*i+64*j, 16) == expected
    results = [cocotb.start_soon(receive(dut, f'read{i}_result', count*beats)) for i in range(2)]
    tasks = [cocotb.start_soon(send(dut, f'read{i}_request',
        [address_payload(4096*i+64*j, count=beats) for j in range(count)])) for i in range(2)]
    for task in tasks:
        await task
    for i, result in enumerate(results):
        for j, value in enumerate(await result):
            assert value & 0xffffffff == 1000*i+j
            assert (value >> 32) & 3 == 0
            assert bool((value >> 34) & 1) == (j % beats == beats-1)
