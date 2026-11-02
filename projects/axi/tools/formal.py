"""Bounded transport and small AXI error-target transaction checks with Yosys SAT.

Real RTL exports are preserved. The formal-only copy translates KHDL assertion
failure $finish statements into immediate assertions, preserving their guards;
diagnostic $write statements have no circuit effect. See khdl-lang/khdl#36.
"""
import argparse
from pathlib import Path
import re
import subprocess

import khdl

from .axi_wrapper import payload_fields


def assertion_adapter(source):
    source, prints = re.subn(r'(?m)^(\s*)\$write\([^\n]*\);$',
                             r'\1// Diagnostic text omitted for formal.', source)
    source, assertions = re.subn(r'(?m)^(\s*)\$finish;$',
                                 r"\1assert(1'b0); // KHDL assertion-failure path", source)
    if '$finish' in source or '$write' in source:
        raise ValueError('Unexpected KHDL diagnostic lowering; inspect before adapting')
    return source, assertions, prints


def pipeline_harness(module_name, config):
    ports = ['input clk', 'input rst']
    wires, connections, checks = [], ['.clk(clk)', '.rst(rst)'], []
    for channel, fields in payload_fields(config).items():
        width = sum(bits for _, bits in fields)
        source, target = ('input', 'output') if channel in ('aw', 'w', 'ar') else ('output', 'input')
        a, b = source + '_' + channel, target + '_' + channel
        for name, output, bits in [(a, False, width), (a+'valid', False, 1),
                                   (a+'ready', True, 1), (b, True, width),
                                   (b+'valid', True, 1), (b+'ready', False, 1)]:
            declaration = f'[{bits-1}:0] {name}'
            (wires if output else ports).append(('wire ' if output else 'input ') + declaration)
            connections.append(f'.{name}({name})')
        checks.append(f'''
    reg [{width-1}:0] {channel}_first, {channel}_second;
    reg [1:0] {channel}_level = 0;
    always @(posedge clk) begin
        if (rst) begin
            {channel}_level <= 0;
        end else begin
            if ({b}valid && {b}ready) begin
                assert({channel}_level > 0);
                assert({b} == {channel}_first);
            end
            case ({{{a}valid && {a}ready, {b}valid && {b}ready}})
                2'b10: begin
                    assert({channel}_level < 2);
                    if ({channel}_level == 0) {channel}_first <= {a};
                    else {channel}_second <= {a};
                    {channel}_level <= {channel}_level + 1;
                end
                2'b01: begin
                    {channel}_first <= {channel}_second;
                    {channel}_level <= {channel}_level - 1;
                end
                2'b11: begin
                    if ({channel}_level == 1) {channel}_first <= {a};
                    else begin {channel}_first <= {channel}_second; {channel}_second <= {a}; end
                end
            endcase
        end
        if (past_valid && !rst && !$past(rst)) begin
            if ($past({a}valid && !{a}ready)) begin
                assume({a}valid);
                assume({a} == $past({a}));
            end
            if ($past({b}valid && !{b}ready)) begin
                assert({b}valid);
                assert({b} == $past({b}));
            end
        end
        if (past_valid && $past(rst)) assert(!{b}valid);
    end
''')
    return ('module formal_top(\n' + ',\n'.join(ports) + '\n);\n'
            + ';\n'.join(wires) + ';\n' + module_name + ' dut(\n'
            + ',\n'.join(connections) + ''');
    reg past_valid = 0;
    always @(posedge clk) begin
        past_valid <= 1;
        if (!past_valid) assume(rst);
    end
''' + '\n'.join(checks) + '\nendmodule\n')


def error_harness(module_name, config):
    """Two-beat transactions, arbitrary IDs and independent AW/W arrival.

    Limit the independent scoreboard to four uncompleted requests per side.
    W can lead AW by up to four complete bursts; stalled inputs stay stable.
    Address/attribute bits are fixed to legal values to focus the state space
    on contexts, channel dependencies, IDs, LAST and completion counts.
    """
    assert (config.addr_width, config.data_width, config.id_width) == (12, 8, 1)
    assert all(getattr(config, name + 'user_width') == 0 for name in ('aw', 'w', 'b', 'ar', 'r'))
    return f"""
module formal_top(input clk, input rst, output reached,
    input arvalid, input arid, input awvalid, input awid,
    input wvalid, input rready, input bready);
    wire arready, awready, wready, rvalid, bvalid;
    wire [{config.R.size_bits-1}:0] r;
    wire [{config.B.size_bits-1}:0] b;
    reg wphase = 0;
    // Address struct packs ID first, followed by address and LEN.
    wire [{config.Ar.size_bits-1}:0] ar = (1 << 13) | (1 << 24) | arid;
    wire [{config.Aw.size_bits-1}:0] aw = (1 << 13) | (1 << 24) | awid;
    // W packs DATA[7:0], STRB, LAST; data is irrelevant to the error target.
    wire [{config.W.size_bits-1}:0] w = (1 << 8) | (wphase << 9);
    {module_name} dut(.clk(clk), .rst(rst),
        .bus_ar(ar), .bus_arvalid(arvalid), .bus_arready(arready),
        .bus_aw(aw), .bus_awvalid(awvalid), .bus_awready(awready),
        .bus_w(w), .bus_wvalid(wvalid), .bus_wready(wready),
        .bus_r(r), .bus_rvalid(rvalid), .bus_rready(rready),
        .bus_b(b), .bus_bvalid(bvalid), .bus_bready(bready));
    reg past_valid = 0;
    reg [3:0] read_ids = 0, write_ids = 0;
    reg [2:0] read_level = 0, write_level = 0;
    reg read_phase = 0;
    reg [3:0] wbursts = 0;
    wire arfire = arvalid && arready;
    wire awfire = awvalid && awready;
    wire wfire = wvalid && wready;
    wire rfire = rvalid && rready;
    wire bfire = bvalid && bready;
    wire rdone = rfire && read_phase;
    reg seen_early_w = 0, seen_overlap = 0, seen_rdone = 0, seen_b = 0;
    assign reached = seen_early_w && seen_overlap && seen_rdone && seen_b;
    always @(posedge clk) begin
        past_valid <= 1;
        if (!past_valid) assume(rst);
        if (rst) begin
            read_level <= 0; write_level <= 0; wbursts <= 0;
            read_phase <= 0; wphase <= 0;
            seen_early_w <= 0; seen_overlap <= 0; seen_rdone <= 0; seen_b <= 0;
        end else begin
            if (wfire && write_level == 0 && !awfire) seen_early_w <= 1;
            if (read_level >= 2 || write_level >= 2) seen_overlap <= 1;
            if (rdone) seen_rdone <= 1;
            if (bfire) seen_b <= 1;
            // Bounded environment reservations; neither assumes output progress.
            if (arvalid) assume(read_level < 4);
            if (awvalid) assume(write_level < 4);
            if (wvalid) assume(wbursts < 4);
            if (rvalid) begin
                assert(read_level > 0);
                assert(r[0] == read_ids[0]);
                assert(r[10:9] == 2'b11);
                assert(r[11] == read_phase);
            end
            if (bvalid) begin
                assert(write_level > 0);
                assert(wbursts > 0);
                assert(b[0] == write_ids[0]);
                assert(b[2:1] == 2'b11);
            end
            if (rfire) read_phase <= !read_phase;
            if (wfire) wphase <= !wphase;
            case ({{arfire, rdone}})
                2'b10: begin read_ids[read_level] <= arid; read_level <= read_level + 1; end
                2'b01: begin read_ids <= read_ids >> 1; read_level <= read_level - 1; end
                2'b11: begin read_ids <= read_ids >> 1; read_ids[read_level-1] <= arid; end
            endcase
            case ({{awfire, bfire}})
                2'b10: begin write_ids[write_level] <= awid; write_level <= write_level + 1; end
                2'b01: begin write_ids <= write_ids >> 1; write_level <= write_level - 1; end
                2'b11: begin write_ids <= write_ids >> 1; write_ids[write_level-1] <= awid; end
            endcase
            case ({{wfire && wphase, bfire}})
                2'b10: wbursts <= wbursts + 1;
                2'b01: wbursts <= wbursts - 1;
            endcase
        end
        if (past_valid && !rst && !$past(rst)) begin
            if ($past(arvalid && !arready)) begin assume(arvalid); assume(arid == $past(arid)); end
            if ($past(awvalid && !awready)) begin assume(awvalid); assume(awid == $past(awid)); end
            if ($past(wvalid && !wready)) assume(wvalid);
            if ($past(rvalid && !rready)) begin assert(rvalid); assert(r == $past(r)); end
            if ($past(bvalid && !bready)) begin assert(bvalid); assert(b == $past(b)); end
        end
        if (past_valid && $past(rst)) begin assert(!rvalid); assert(!bvalid); end
    end
endmodule
"""


def check(out, depth=12, *, inject_fault=False, target="pipeline"):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest = Path(__file__).resolve().parents[1] / 'rtl/khdl.toml'
    compiler = khdl.Source.new_from_manifest_path(str(manifest)).compile()
    config = compiler.resolve('axi.types.axi_config')(addr_width=12, data_width=8, id_width=1)
    module = (compiler.resolve('examples.pipeline.AxiPipeline')(config) if target == 'pipeline'
              else compiler.resolve('examples.error.AxiError')(config, capacity=2))
    verilog = module.as_verilog()
    (out / 'original.v').write_text(verilog.source)
    adapted, assertions, prints = assertion_adapter(verilog.source)
    (out / 'design.formal.v').write_text(adapted)
    harness = (pipeline_harness(verilog.module_name, config) if target == 'pipeline'
               else error_harness(verilog.module_name, config))
    if inject_fault and target != 'pipeline':
        raise ValueError('Fault injection is only implemented for the pipeline')
    if inject_fault:
        # Negative control: corrupt the forwarded AW payload. This must violate
        # the independent FIFO scoreboard, not merely a compiler assertion.
        harness = harness.replace('.output_aw(output_aw)', '.output_aw(corrupt_aw)')
        harness = harness.replace(verilog.module_name + ' dut(',
            f'wire [{config.Aw.size_bits-1}:0] corrupt_aw;\n'
            'assign output_aw = ~corrupt_aw;\n' + verilog.module_name + ' dut(')
    (out / 'harness.sv').write_text(harness)
    clock_steps = ' '.join(f'-set-at {step} clk {1 if step % 2 == 0 else 0}'
                           for step in range(1, 2*depth + 1))
    script = ('read_verilog -formal -sv design.formal.v harness.sv; '
              'prep -flatten -top formal_top; clk2fflogic; chformal -lower; opt; '
              f'sat -seq {2*depth} -set rst 0 -set-at 1 rst 1 -set-at 2 rst 1 '
              f'{clock_steps} -set-assumes -prove-asserts -verify '
              '-dump_vcd counterexample.vcd')
    (out / 'check.ys').write_text(script + '\n')
    with (out / 'yosys.log').open('w') as log:
        subprocess.run(['yosys', '-Q', '-T', '-s', 'check.ys'], cwd=out,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    if target == 'error':
        # Show that the assumptions permit progress, early W and overlap.
        reach_script = script.replace('-prove-asserts -verify', '-prove reached 0 -falsify')
        reach_script = reach_script.replace('counterexample.vcd', 'reachable.vcd')
        (out / 'reach.ys').write_text(reach_script + '\n')
        with (out / 'reach.log').open('w') as log:
            subprocess.run(['yosys', '-Q', '-T', '-s', 'reach.ys'], cwd=out,
                           stdout=log, stderr=subprocess.STDOUT, check=True)
    print(f'{target} bounded safety passed: {depth} cycles; '
          f'{assertions} assertion paths preserved, {prints} diagnostic writes omitted')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('build/axi/formal'))
    parser.add_argument('--depth', type=int, default=12)
    parser.add_argument('--target', choices=['pipeline', 'error'], default='pipeline')
    args = parser.parse_args()
    if args.depth < 2:
        parser.error('--depth must be at least two')
    check(args.out, args.depth, target=args.target)
