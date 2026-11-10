"""Formal checks of AXI modules with ABC (bmc3, or pdr for unbounded proofs)
or, without ABC, Yosys's built-in SAT solver.

Targets:
  pipeline  AxiPipeline transport: every channel delivers exactly its input
            beats, in order (design contract), with stable stalled outputs and
            VALID low in and after reset.
  error     AxiError against the AXI4 protocol monitor as a Subordinate, with
            an arbitrary legal Manager, plus its DECERR contract.
  manager   The fixed-ID Manager API (formal/manager.kh) against the AXI4
            protocol monitor, with an arbitrary legal Subordinate.

Protocol rules come from formal/axi4_protocol.sv, written from the AXI
specification. Design contracts are separate, named assertions in the harness.
Reset is assumed in the first cycle and is unconstrained afterwards.

Real RTL exports are preserved. The formal-only copy turns each KHDL assertion
failure ($finish after diagnostic $write text) into a sticky per-module failure
flag that a combinational assertion checks; see khdl-lang/khdl#36.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

import khdl

from .axi_wrapper import payload_fields

PROJECT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT / 'rtl/khdl.toml'
MONITOR = PROJECT / 'formal/axi4_protocol.sv'
MANAGER_FACADE = PROJECT / 'formal/manager.kh'
TARGETS = ('pipeline', 'error', 'manager')
ENGINES = ('abc', 'sat')
FAULTS = {
    'pipeline': 'invert the forwarded AW payload',
    'error': 'flip RLAST as seen by the protocol monitor',
    'manager': 'mark AR bursts as the reserved BURST encoding',
}
# Default proof depths: cycles after the initial reset. Runtime grows steeply
# with depth: with ABC the manager default takes minutes, and Yosys's built-in
# sat engine is several times slower.
DEPTHS = {'pipeline': 10, 'error': 8, 'manager': 5}
# Minimum depths at which the reachability traces exist. Reachability runs at
# the larger of this and the proof depth; finding a trace is much cheaper than
# a proof, so a shallow proof can still show non-vacuous assumptions.
REACH_DEPTHS = {'pipeline': 0, 'error': 5, 'manager': 5}


def assertion_adapter(source):
    """Return the formal copy and the number of adapted failure paths.

    A sticky flag reports a failure one cycle after the failing edge, so proofs
    run one extra step. The flag is not reset, so failures survive reset.
    """
    total = 0

    def adapt(match):
        nonlocal total
        module = match.group(0)
        module, prints = re.subn(r'(?m)^(\s*)\$write\([^\n]*\);$',
                                 r'\1// Diagnostic text omitted for formal.', module)
        module, failures = re.subn(r'(?m)^(\s*)\$finish;$',
                                   r"\1khdl_assertion_failed <= 1'b1; // KHDL assertion-failure path",
                                   module)
        if prints != failures:
            raise ValueError('Unexpected KHDL diagnostic lowering; inspect before adapting')
        if failures:
            header = re.search(r'(?m)^\);$', module)
            module = (module[:header.end()]
                      + "\n    reg khdl_assertion_failed = 1'b0;"
                      + '\n    always @(*) assert(!khdl_assertion_failed);'
                      + module[header.end():])
        total += failures
        return module

    adapted = re.sub(r'(?ms)^module .*?^endmodule', adapt, source)
    if '$finish' in adapted or '$write' in adapted:
        raise ValueError('Unexpected KHDL diagnostic lowering; inspect before adapting')
    return adapted, total


def failure_flags(adapted, module_name, prefix='dut'):
    """Hierarchical names of every KHDL failure flag below `module_name`."""
    modules = dict(re.findall(r'(?ms)^module (\w+)\((.*?)^endmodule', adapted))
    body = modules[module_name]
    flags = [f'{prefix}.khdl_assertion_failed'] if 'reg khdl_assertion_failed' in body else []
    for child, instance in re.findall(r'(?m)^\s*(\w+)\s+(\w+)\s*\(', body):
        if child in modules:
            flags += failure_flags(adapted, child, f'{prefix}.{instance}')
    return flags


def module_ports(source, module_name):
    """(direction, width, name) for each port of an exported KHDL module."""
    start = source.index(f'module {module_name}(')
    header = source[start:source.index('\n);', start)]
    return [(direction, int(msb) + 1 if msb else 1, name) for direction, msb, name in
            re.findall(r'(?m)^\s*(input|output)\s+(?:reg\s+)?(?:\[(\d+):0\]\s+)?(\w+)', header)]


def field(config, channel, name):
    """Verilog slice expression offsets of one payload field."""
    offset = 0
    for field_name, width in payload_fields(config)[channel]:
        if field_name == name:
            return offset, width
        offset += width
    raise KeyError(name)


def top_module(module_name, ports, body, extra_ports=()):
    """formal_top exposing the DUT's inputs and wiring its outputs by name."""
    declarations = ['input clk', 'input rst', *extra_ports]
    wires = []
    for direction, width, name in ports:
        if name in ('clk', 'rst'):
            continue
        vector = f'[{width-1}:0] ' if width > 1 else ''
        (declarations if direction == 'input' else wires).append(
            f'input {vector}{name}' if direction == 'input' else f'wire {vector}{name};')
    connections = ', '.join(f'.{name}({name})' for _, _, name in ports)
    return (f'module formal_top(\n    ' + ',\n    '.join(declarations) + '\n);\n    '
            + '\n    '.join(wires) + f'\n    {module_name} dut({connections});\n'
            + '''    // Reset in the first cycle; afterwards reset is unconstrained.
    reg started = 1'b0;
    always @(posedge clk) started <= 1'b1;
    always @(*) if (!started) assume(rst);
''' + body + 'endmodule\n')


def protocol_monitor(config, prefix, dut_is_manager, *, max_reads, max_writes, max_early,
                     override=None):
    override = override or {}
    parameters = {
        'ID_WIDTH': config.id_width, 'ADDR_WIDTH': config.addr_width,
        'DATA_WIDTH': config.data_width, 'AWUSER_WIDTH': config.awuser_width,
        'WUSER_WIDTH': config.wuser_width, 'BUSER_WIDTH': config.buser_width,
        'ARUSER_WIDTH': config.aruser_width, 'RUSER_WIDTH': config.ruser_width,
        'DUT_IS_MANAGER': int(dut_is_manager), 'MAX_READS': max_reads,
        'MAX_WRITES': max_writes, 'MAX_EARLY_BEATS': max_early,
    }
    signals = [(f'{channel}{suffix}', override.get(channel + suffix, f'{prefix}_{channel}{suffix}'))
               for channel in ('aw', 'w', 'b', 'ar', 'r') for suffix in ('', 'valid', 'ready')]
    return ('    axi4_protocol #(' + ', '.join(f'.{k}({v})' for k, v in parameters.items())
            + ') protocol(.clk(clk), .rst(rst), '
            + ', '.join(f'.{port}({signal})' for port, signal in signals)
            + ', .reads_outstanding(reads_outstanding), .writes_outstanding(writes_outstanding),'
            + ' .early_beats(early_beats));\n'
            + '    wire [7:0] reads_outstanding, writes_outstanding, early_beats;\n')


def pipeline_harness(module_name, ports, config, inject_fault):
    # Design contract of AxiPipeline (not AXI rules): each channel is a FIFO of
    # capacity two (output slot plus skid slot) that delivers exactly its input
    # beats in order. The environment keeps stalled inputs stable; inputs are
    # otherwise arbitrary, including traffic that would be illegal AXI.
    checks = []
    for channel, fields in payload_fields(config).items():
        width = sum(bits for _, bits in fields)
        source, target = ('input', 'output') if channel in ('aw', 'w', 'ar') else ('output', 'input')
        a, b = f'{source}_{channel}', f'{target}_{channel}'
        seen = f'{b}_seen' if inject_fault and channel == 'aw' else b
        checks.append(f'''
    reg [{width-1}:0] {channel}_first, {channel}_second;
    reg [1:0] {channel}_level = 0;
    reg {channel}_in_stall = 1'b0, {channel}_out_stall = 1'b0;
    reg [{width-1}:0] {channel}_in_past, {channel}_out_past;
    wire {channel}_reset_ok = !(rst || past_rst) || !{b}valid;
    wire {channel}_stable_ok = !(!rst && !past_rst && {channel}_out_stall)
        || ({b}valid && {seen} == {channel}_out_past);
    wire {channel}_order_ok = rst || !{b}valid || ({channel}_level > 0 && {seen} == {channel}_first);
    wire {channel}_capacity_ok = rst || !({a}valid && {a}ready && !({b}valid && {b}ready))
        || {channel}_level < 2;
    always @(*) begin
        if (!rst && !past_rst && {channel}_in_stall) assume({a}valid && {a} == {channel}_in_past);
        assert({channel}_reset_ok);
        assert({channel}_stable_ok);
        assert({channel}_order_ok);
        assert({channel}_capacity_ok);
    end
    always @(posedge clk) begin
        {channel}_in_stall <= !rst && {a}valid && !{a}ready;
        {channel}_out_stall <= !rst && {b}valid && !{b}ready;
        {channel}_in_past <= {a};
        {channel}_out_past <= {seen};
        if (rst) begin
            {channel}_level <= 0;
        end else case ({{{a}valid && {a}ready, {b}valid && {b}ready}})
            2'b10: begin
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
''')
    fault = ''
    if inject_fault:
        fault = f"    wire [{field_width(config, 'aw')-1}:0] output_aw_seen = ~output_aw;\n"
    body = ("    reg past_rst = 1'b0;\n    always @(posedge clk) past_rst <= rst;\n"
            + fault + ''.join(checks))
    show = [f'{channel}_{rule}_ok' for channel in payload_fields(config)
            for rule in ('reset', 'stable', 'order', 'capacity')]
    return top_module(module_name, ports, body), None, show


def field_width(config, channel):
    return sum(width for _, width in payload_fields(config)[channel])


def error_harness(module_name, ports, config, inject_fault):
    b_resp, _ = field(config, 'b', 'resp')
    r_resp, _ = field(config, 'r', 'resp')
    r_last, _ = field(config, 'r', 'last')
    override = {}
    body = ''
    if inject_fault:
        body += (f"    wire [{field_width(config, 'r')-1}:0] bus_r_seen = "
                 f"bus_r ^ ({field_width(config, 'r')}'d1 << {r_last});\n")
        override['r'] = 'bus_r_seen'
    body += protocol_monitor(config, 'bus', False, max_reads=4, max_writes=4, max_early=2,
                             override=override)
    body += f'''    // AxiError contract (design, not AXI rules): every response is DECERR.
    wire decerr_ok = rst || ((!bus_rvalid || bus_r[{r_resp}+:2] == 2'd3)
        && (!bus_bvalid || bus_b[{b_resp}+:2] == 2'd3));
    always @(*) assert(decerr_ok);
    // Reachability: early W, two outstanding requests on one side, a completed
    // multi-beat read, and a write response.
    reg seen_early_w = 1'b0, seen_overlap = 1'b0, seen_burst = 1'b0, seen_b = 1'b0;
    reg read_midburst = 1'b0;
    always @(posedge clk) if (rst) begin
        seen_early_w <= 1'b0; seen_overlap <= 1'b0; seen_burst <= 1'b0; seen_b <= 1'b0;
        read_midburst <= 1'b0;
    end else begin
        if (early_beats != 0) seen_early_w <= 1'b1;
        if (reads_outstanding >= 2 || writes_outstanding >= 2) seen_overlap <= 1'b1;
        if (bus_rvalid && bus_rready) begin
            read_midburst <= !bus_r[{r_last}];
            if (bus_r[{r_last}] && read_midburst) seen_burst <= 1'b1;
        end
        if (bus_bvalid && bus_bready) seen_b <= 1'b1;
    end
    assign reached = seen_early_w && seen_overlap && seen_burst && seen_b;
'''
    show = ['decerr_ok'] + [f'protocol.{rule}' for rule in MONITOR_RULES]
    return top_module(module_name, ports, body, ['output reached']), 'reached', show


def manager_harness(module_name, ports, config, inject_fault):
    r_last, _ = field(config, 'r', 'last')
    r_id, _ = field(config, 'r', 'id')
    override = {}
    body = ''
    if inject_fault:
        burst, _ = field(config, 'ar', 'burst')
        body += (f"    wire [{field_width(config, 'ar')-1}:0] bus_ar_seen = "
                 f"bus_ar | ({field_width(config, 'ar')}'d3 << {burst});\n")
        override['ar'] = 'bus_ar_seen'
    # Two endpoints of capacity two per direction: at most four outstanding
    # transactions per direction. The Manager presents AW before its W beats,
    # so no beat precedes its AW; the capacity rule would show otherwise.
    body += protocol_monitor(config, 'bus', True, max_reads=4, max_writes=4, max_early=1,
                             override=override)
    body += f'''    // Reachability: R beats of both IDs interleaved within an open burst,
    // a completed multi-beat read, and a write response.
    reg seen_interleave = 1'b0, seen_burst = 1'b0, seen_b = 1'b0;
    reg read_midburst = 1'b0, burst_id = 1'b0;
    always @(posedge clk) if (rst) begin
        seen_interleave <= 1'b0; seen_burst <= 1'b0; seen_b <= 1'b0; read_midburst <= 1'b0;
    end else begin
        if (bus_rvalid && bus_rready) begin
            if (read_midburst && bus_r[{r_id}] != burst_id) seen_interleave <= 1'b1;
            if (!read_midburst) burst_id <= bus_r[{r_id}];
            if (bus_r[{r_last}] && read_midburst && bus_r[{r_id}] == burst_id) seen_burst <= 1'b1;
            if (!read_midburst || bus_r[{r_id}] == burst_id) read_midburst <= !bus_r[{r_last}];
        end
        if (bus_bvalid && bus_bready) seen_b <= 1'b1;
    end
    assign reached = seen_interleave && seen_burst && seen_b;
'''
    show = [f'protocol.{rule}' for rule in MONITOR_RULES]
    return top_module(module_name, ports, body, ['output reached']), 'reached', show


def elaborate(target, config_args):
    source = khdl.Source.new_from_manifest_path(str(MANIFEST))
    if target == 'manager':
        source.add_file_content(['formal_manager'], str(MANAGER_FACADE), MANAGER_FACADE.read_text())
    compiler = source.compile()
    config = compiler.resolve('axi.types.axi_config')(**config_args)
    if target == 'pipeline':
        module = compiler.resolve('examples.pipeline.AxiPipeline')(config)
    elif target == 'error':
        module = compiler.resolve('examples.error.AxiError')(config, capacity=2)
    else:
        module = compiler.resolve('formal_manager.AxiFormalManager')(config, capacity=2)
    return config, module.as_verilog()


CONFIGS = {
    'pipeline': dict(addr_width=12, data_width=8, id_width=1),
    'error': dict(addr_width=12, data_width=16, id_width=1),
    'manager': dict(addr_width=12, data_width=16, id_width=1),
}
HARNESSES = {'pipeline': pipeline_harness, 'error': error_harness, 'manager': manager_harness}


MONITOR_RULES = [
    'manager_reset_ok', 'aw_stable_ok', 'w_stable_ok', 'ar_stable_ok', 'aw_legal_ok',
    'ar_legal_ok', 'wlast_ok', 'wstrb_ok', 'capacity_ok', 'subordinate_reset_ok', 'b_stable_ok',
    'r_stable_ok', 'rid_ok', 'rlast_ok', 'rresp_ok', 'bid_ok', 'b_after_w_ok', 'bresp_ok',
]


class ProofFailed(Exception):
    """A property failed; the message names it and the counterexample trace."""


def abc_executable():
    """ABC as shipped with Yosys (yosys-abc), or a standalone build (abc)."""
    return shutil.which('yosys-abc') or shutil.which('abc')


def default_engine():
    return 'abc' if abc_executable() and shutil.which('yosys-witness') else 'sat'


def front_end(files):
    return (f'read_verilog -formal -sv {" ".join(files)}; prep -flatten -top formal_top; '
            'async2sync; chformal -lower; ')


def run_yosys(out, name, script):
    (out / f'{name}.ys').write_text(script + '\n')
    with (out / f'{name}.log').open('w') as log:
        return subprocess.run(['yosys', '-Q', '-T', '-s', f'{name}.ys'], cwd=out,
                              stdout=log, stderr=subprocess.STDOUT).returncode


def sat_check(out, name, files, steps, show):
    # The counterexample table lists ports, monitor rules, contract checks, and
    # the KHDL assertion flags, so the failing step names the violated rule.
    shown = ' '.join(f'-show {signal}' for signal in show)
    script = (front_end(files) + f'opt -fast; sat -seq {steps} -set-assumes -prove-asserts '
              f'-verify -show-ports {shown} -dump_vcd {name}.vcd')
    if run_yosys(out, name, script) != 0:
        log = (out / f'{name}.log').read_text()
        if 'proof did fail' in log:
            raise ProofFailed(f'counterexample in {out / name}.vcd; '
                              f'the table in {out / name}.log shows the failing rule')
        raise RuntimeError(f'Yosys failed; see {out / name}.log')


def sat_reach(out, name, files, steps, reach):
    script = front_end(files) + f'opt -fast; sat -seq {steps} -set-assumes -prove {reach} 0 -falsify -dump_vcd {name}.vcd'
    if run_yosys(out, name, script) != 0:
        raise RuntimeError(f'no reachability trace in {steps - 1} cycles; see {out / name}.log')


def abc_run(out, name, files, command, *, reach):
    """Export an AIGER model and run ABC on it; return (status, frames).

    For a check, the properties are the assertions; for reachability, the only
    property is the harness output `reach`, so a "counterexample" is the trace.
    Undefined values and uninitialized registers are free, as with sat.
    """
    select = 'chformal -assert -remove; ' if reach else 'setattr -unset keep; delete -output; '
    script = (front_end(files) + select + 'opt -fast; formalff -clk2ff -ff2anyinit; '
              'setundef -undriven -anyseq; setundef -anyseq; opt -fast; '
              f'write_rtlil {name}.il; techmap; opt -fast; setundef -anyseq; aigmap; opt_clean; '
              f'write_aiger -I -B -zinit -map {name}.aim -ywmap {name}.ywb {name}.aig')
    if run_yosys(out, f'{name}.export', script) != 0:
        raise RuntimeError(f'Yosys failed; see {out / name}.export.log')
    commands = f'read_aiger {name}.aig; fold; strash; orpos; {command}; print_status; write_cex -a {name}.aiw'
    result = subprocess.run([abc_executable(), '-c', commands], cwd=out,
                            capture_output=True, text=True)
    (out / f'{name}.log').write_text(result.stdout + result.stderr)
    status = re.search(r'Status = (-?\d+)\s+Frames = (-?\d+)', result.stdout)
    if result.returncode != 0 or not status:
        raise RuntimeError(f'ABC failed; see {out / name}.log')
    return int(status.group(1)), int(status.group(2))


def abc_trace(out, name):
    """Replay ABC's counterexample in Yosys: write {name}.vcd and return the
    failed assertions as source locations with their source text."""
    subprocess.run(['yosys-witness', 'aiw2yw', f'{name}.aiw', f'{name}.ywb', f'{name}.yw'],
                   cwd=out, check=True, capture_output=True)
    script = f'read_rtlil {name}.il; sim -hdlname -r {name}.yw -vcd {name}.vcd -summary {name}.sim.json'
    if run_yosys(out, f'{name}.sim', script) != 0:
        raise RuntimeError(f'trace replay failed; see {out / name}.sim.log')
    failed = []
    for assertion in json.loads((out / f'{name}.sim.json').read_text())['assertions']:
        filename, line = re.match(r'(.*?):(\d+)', assertion['src']).groups()
        path = out / filename if (out / filename).exists() else Path(filename)
        lines = path.read_text().splitlines()[:int(line)]
        text = lines[-1].strip()
        if 'khdl_assertion_failed' in text:
            module = next(l.split()[1].split('(')[0] for l in reversed(lines)
                          if l.startswith('module '))
            text = f'KHDL assertion in module {module}'
        failed.append(f'step {assertion["step"]}: {Path(filename).name}:{line}: {text}')
    return failed


def abc_check(out, name, files, steps, unbounded):
    status, frames = abc_run(out, name, files, 'pdr' if unbounded else f'bmc3 -F {steps}',
                             reach=False)
    if status == 0:
        failed = abc_trace(out, name)
        raise ProofFailed(f'counterexample in {out / name}.vcd:\n  ' + '\n  '.join(failed))
    if unbounded and status != 1:
        raise RuntimeError(f'PDR did not prove the properties; see {out / name}.log')
    if not unbounded and frames < steps - 1:
        raise RuntimeError(f'ABC checked only {frames + 1} of {steps} steps; see {out / name}.log')


def abc_reach(out, name, files, steps):
    status, _ = abc_run(out, name, files, f'bmc3 -F {steps}', reach=True)
    if status != 0:
        raise RuntimeError(f'no reachability trace in {steps - 1} cycles; see {out / name}.log')
    abc_trace(out, name)


def prove(out, verilog_source, module_name, harness, depth, reach=None, show=(),
          reach_depth=None, *, engine=None, unbounded=False):
    """Prove all assertions for `depth` cycles after the initial reset cycle,
    or for any number of cycles with `unbounded` (ABC's PDR).

    With `reach`, also show that the assumptions permit a trace of
    `reach_depth` cycles (default `depth`) where that harness output becomes
    true (written to reachable.vcd). `show` names signals listed in sat's
    counterexample tables. Raises ProofFailed with a counterexample.
    """
    engine = engine or default_engine()
    if unbounded and engine != 'abc':
        raise ValueError('unbounded proofs require the abc engine')
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    for stale in [*out.glob('check.*'), *out.glob('reachable.*')]:
        stale.unlink()
    (out / 'original.v').write_text(verilog_source)
    adapted, failures = assertion_adapter(verilog_source)
    (out / 'design.formal.v').write_text(adapted)
    (out / 'harness.sv').write_text(harness)
    files = ['design.formal.v', str(MONITOR), 'harness.sv']
    # One reset cycle, `depth` checked cycles, one step for sticky KHDL flags.
    steps = depth + 2
    reach_steps = (depth if reach_depth is None else reach_depth) + 2
    if engine == 'abc':
        abc_check(out, 'check', files, steps, unbounded)
        if reach:
            abc_reach(out, 'reachable', files, reach_steps)
    else:
        sat_check(out, 'check', files, steps, [*show, *failure_flags(adapted, module_name)])
        if reach:
            sat_reach(out, 'reachable', files, reach_steps, reach)
    return failures


def check(out, depth=None, *, inject_fault=False, target='pipeline', engine=None,
          unbounded=False):
    depth = DEPTHS[target] if depth is None else depth
    reach_depth = max(depth, REACH_DEPTHS[target])
    engine = engine or default_engine()
    config, verilog = elaborate(target, CONFIGS[target])
    harness, reach, show = HARNESSES[target](
        verilog.module_name, module_ports(verilog.source, verilog.module_name), config, inject_fault)
    failures = prove(out, verilog.source, verilog.module_name, harness, depth,
                     None if inject_fault else reach, show, reach_depth,
                     engine=engine, unbounded=unbounded)
    scope = 'all cycles after reset' if unbounded else f'{depth} cycles after reset'
    print(f'{target} {"unbounded proof" if unbounded else "bounded checks"} passed ({engine}): '
          f'{scope}; {failures} KHDL assertion paths preserved'
          + (f'; reachability shown in {reach_depth} cycles' if reach and not inject_fault else ''))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--target', choices=TARGETS, default='pipeline')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--depth', type=int,
                        help='cycles after reset (default: '
                             + ', '.join(f'{t} {d}' for t, d in DEPTHS.items()) + ')')
    parser.add_argument('--engine', choices=ENGINES,
                        help='abc (ABC bmc3/pdr, the default when installed) or sat (Yosys built-in)')
    parser.add_argument('--unbounded', action='store_true',
                        help='prove for all depths with ABC PDR; may not terminate')
    parser.add_argument('--inject-fault', action='store_true',
                        help='negative control; the check must fail: '
                             + '; '.join(f'{t}: {d}' for t, d in FAULTS.items()))
    args = parser.parse_args()
    if args.depth is not None and args.depth < 2:
        parser.error('--depth must be at least two')
    if args.unbounded and (args.engine or default_engine()) != 'abc':
        parser.error('--unbounded requires the abc engine (ABC and yosys-witness)')
    try:
        check(args.out or Path('build/axi/formal') / args.target, args.depth,
              inject_fault=args.inject_fault, target=args.target, engine=args.engine,
              unbounded=args.unbounded)
    except ProofFailed as error:
        raise SystemExit(f'{args.target}: proof failed: {error}')
