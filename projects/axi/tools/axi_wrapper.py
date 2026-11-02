"""Flat AXI signal adapter for interoperability tests of packed KHDL payloads.

Only the external naming/packing boundary is generated; the protocol API and
all hardware behavior live in KHDL. First struct fields occupy the low bits.
"""


def payload_fields(config):
    address = [('id', config.id_width), ('addr', config.addr_width), ('len', 8),
               ('size', 3), ('burst', 2), ('lock', 1), ('cache', 4), ('prot', 3),
               ('qos', 4), ('region', 4)]
    return {
        'aw': address + [('user', config.awuser_width)],
        'w': [('data', config.data_width), ('strb', config.bytes), ('last', 1),
              ('user', config.wuser_width)],
        'b': [('id', config.id_width), ('resp', 2), ('user', config.buser_width)],
        'ar': address + [('user', config.aruser_width)],
        'r': [('id', config.id_width), ('data', config.data_width), ('resp', 2),
              ('last', 1), ('user', config.ruser_width)],
    }


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
            pieces = []
            for field, width in fields:
                if width:
                    name = f'{flat}_{channel}{field}'
                    ports.append(f'{direction} wire [{width-1}:0] {name}')
                    pieces.append(name)
            wire = f'{packed}_{channel}_payload'
            statements.append(f'wire [{sum(w for _, w in fields)-1}:0] {wire};')
            concat = '{' + ', '.join(reversed(pieces)) + '}'
            statements.append(f'assign {concat if output else wire} = {wire if output else concat};')
            connections.append(f'.{packed}_{channel}({wire})')
            for suffix, dir_ in [('valid', direction), ('ready', reverse)]:
                name = f'{flat}_{channel}{suffix}'
                ports.append(f'{dir_} wire {name}')
                connections.append(f'.{packed}_{channel}{suffix}({name})')
    return ('module axi_test(\n' + ',\n'.join(ports) + '\n);\n'
            + '\n'.join(statements) + '\n' + module_name + ' dut(\n'
            + ',\n'.join(connections) + '\n);\nendmodule\n')
