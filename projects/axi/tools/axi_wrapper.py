"""Flat AXI signal adapter for interoperability tests of packed KHDL payloads.

Only the external naming/packing boundary is generated; the protocol API and
all hardware behavior live in KHDL. First struct fields occupy the low bits.
"""


def payload_fields(config):
    """Flat fields of each channel in struct order, for the configuration's
    protocol (axi.types: AXI4, AXI3 or AXI4-Lite)."""
    if config.lite:
        address = [('addr', config.addr_width), ('prot', 3)]
        return {
            'aw': address,
            'w': [('data', config.data_width), ('strb', config.bytes)],
            'b': [('resp', 2)],
            'ar': address,
            'r': [('data', config.data_width), ('resp', 2)],
        }
    if config.axi3:
        address = [('id', config.id_width), ('addr', config.addr_width), ('len', 4),
                   ('size', 3), ('burst', 2), ('lock', 2), ('cache', 4), ('prot', 3)]
        write = [('id', config.id_width), ('data', config.data_width), ('strb', config.bytes),
                 ('last', 1), ('user', config.wuser_width)]
    else:
        address = [('id', config.id_width), ('addr', config.addr_width), ('len', 8),
                   ('size', 3), ('burst', 2), ('lock', 1), ('cache', 4), ('prot', 3),
                   ('qos', 4), ('region', 4)]
        write = [('data', config.data_width), ('strb', config.bytes), ('last', 1),
                 ('user', config.wuser_width)]
    return {
        'aw': address + [('user', config.awuser_width)],
        'w': write,
        'b': [('id', config.id_width), ('resp', 2), ('user', config.buser_width)],
        'ar': address + [('user', config.aruser_width)],
        'r': [('id', config.id_width), ('data', config.data_width), ('resp', 2),
              ('last', 1), ('user', config.ruser_width)],
    }


# Pin widths the cocotbext-axi models require (AXI4) where AXI3 differs: an
# AXI3 bus is presented to them zero-extended (LEN) or truncated (LOCK: the
# AXI3 locked encoding, 2, is not representable and unused by the tests).
AXI4_PEER_WIDTHS = {'len': 8, 'lock': 1}


def flat_wrapper(module_name, config, buses, extra_ports=None):
    """buses maps KHDL port prefixes to (flat prefix, 'Manager'/'Subordinate')."""
    ports = ['input wire clk', 'input wire aresetn']
    statements = ['wire rst = !aresetn;']
    connections = ['.clk(clk)', '.rst(rst)']
    for name, (direction, width) in (extra_ports or {}).items():
        if width:
            ports.append(f'{direction} wire [{width-1}:0] {name}')
            connections.append(f'.{name}({name})')
    for packed, (flat, role) in buses.items():
        assert role in ('Manager', 'Subordinate')
        for channel, fields in payload_fields(config).items():
            output = (channel in ('aw', 'w', 'ar')) == (role == 'Manager')
            direction, reverse = ('output', 'input') if output else ('input', 'output')
            wire = f'{packed}_{channel}_payload'
            statements.append(f'wire [{sum(w for _, w in fields)-1}:0] {wire};')
            offset = 0
            for field, width in fields:
                if width:
                    name = f'{flat}_{channel}{field}'
                    pins = width
                    if config.axi3 and channel in ('aw', 'ar'):
                        pins = AXI4_PEER_WIDTHS.get(field, width)
                    ports.append(f'{direction} wire [{pins-1}:0] {name}')
                    piece = f'{wire}[{offset+width-1}:{offset}]'
                    if output:
                        statements.append(f'assign {name} = {piece};')
                    else:
                        statements.append(f'assign {piece} = {name}[{min(pins, width)-1}:0];')
                        if pins < width:
                            statements.append(f'assign {wire}[{offset+width-1}:{offset+pins}] = 0;')
                    offset += width
            connections.append(f'.{packed}_{channel}({wire})')
            for suffix, dir_ in [('valid', direction), ('ready', reverse)]:
                name = f'{flat}_{channel}{suffix}'
                ports.append(f'{dir_} wire {name}')
                connections.append(f'.{packed}_{channel}{suffix}({name})')
    return ('module axi_test(\n' + ',\n'.join(ports) + '\n);\n'
            + '\n'.join(statements) + '\n' + module_name + ' dut(\n'
            + ',\n'.join(connections) + '\n);\nendmodule\n')
