"""Check channel/AXI API specializations and export demonstration modules."""
import argparse
from pathlib import Path
import sys

import khdl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--out', type=Path,
                        default=Path(__file__).resolve().parents[3] / 'build/axi/verilog')
    args = parser.parse_args()
    manifest = Path(__file__).resolve().parents[1] / 'rtl/khdl.toml'
    c = khdl.Source.new_from_manifest_path(str(manifest)).compile()
    # Python is lazy: explicitly call the same roots as the LSP's const block.
    c.resolve('channel.examples.check_examples')()
    c.resolve('channel.ref_self.check_examples')()
    c.resolve('examples.checks.check_examples')()
    print('All channel and AXI API examples: elaboration passed')
    if args.check:
        return
    packet = c.resolve('channel.examples.Packet')
    examples = {
        'relay': c.resolve('channel.examples.Relay')(packet),
        'ref_self_relay': c.resolve('channel.ref_self.Relay')(packet),
        'buffered_relay': c.resolve('channel.examples.Relay')(packet, stages=3),
        'reverse_relay': c.resolve('channel.examples.Relay')(packet, stages=3, reverse=True),
        'chain': c.resolve('channel.examples.Chain')(packet),
        'independent': c.resolve('channel.examples.Independent'),
        'internal_probe': c.resolve('channel.examples.InternalProbe')(),
        'fifo_probe': c.resolve('channel.examples.FifoProbe')(),
        'fifo_relay': c.resolve('channel.examples.Relay')(packet, stages=3, reverse=False, depth=2),
        'receiver_probe': c.resolve('channel.examples.ReceiverProbe'),
        'transmitter_probe': c.resolve('channel.examples.TransmitterProbe'),
        'axi_pipeline': c.resolve('examples.pipeline.AxiPipeline')(),
        'axi_ram': c.resolve('examples.ram.AxiRam')(c.resolve('axi.types.AxiDefault'), memory_bytes=256),
        'axi_error': c.resolve('examples.error.AxiError')(),
        'axi_copy': c.resolve('examples.copy.AxiCopy')(),
        'axi_router': c.resolve('examples.router.AxiRouter')(),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    for name, module in examples.items():
        exported = module.as_verilog()
        path = args.out / (name + '.v')
        path.write_text(exported.source)
        print(f'{path}: top module {exported.module_name}')


if __name__ == '__main__':
    try:
        main()
    except khdl.DiagnosticException as exc:
        print(exc.combined_string, file=sys.stderr)
        sys.exit(1)
