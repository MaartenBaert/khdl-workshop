"""Elaborate the encoder and decoder, optionally exporting Verilog."""
import argparse
import sys
from pathlib import Path
import khdl
from .configuration import compile_manifest, decoder_parallelism, memory_spec, message_buffer, module_path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in dict(n=240, k=224, m=8, prim=0x11d, alpha=2, fcr=1).items():
        parser.add_argument('--' + name, type=lambda value: int(value, 0), default=default)
    parser.add_argument('--unroll', type=int, default=16,
                        help='encoder, syndromes, Chien, and correction width')
    parser.add_argument('--unroll-berlekamp', type=int, default=1)
    parser.add_argument('--unroll-omegas', type=int, default=1)
    parser.add_argument('--unroll-forney', type=int, default=1)
    parser.add_argument('--berlekamp-instances', type=int, default=1,
                        help='round-robin parallel Berlekamp workers')
    parser.add_argument('--forney-instances', type=int, default=1,
                        help='round-robin parallel Forney workers')
    parser.add_argument('--buffered', action='store_true')
    sizing = parser.add_mutually_exclusive_group()
    sizing.add_argument('--message-depth', type=int,
                        help='decoder message FIFO depth in beats (default: full pipeline capacity)')
    sizing.add_argument('--message-interval', type=int,
                        help='size the message FIFO to sustain one frame per this many cycles')
    parser.add_argument('--message-memory', default='lut', type=memory_spec,
                        help="message FIFO memory: 'lut', 'block:LATENCY', or 'external:LATENCY'")
    parser.add_argument('--message-no-bypass', action='store_true',
                        help='message FIFO without bypass logic (more latency, less logic)')
    parser.add_argument('--check', action='store_true',
                        help='check concrete module hierarchies without generating Verilog or a simulator')
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parents[3] / 'build/reed_solomon/verilog')
    args = parser.parse_args()
    if not args.check:
        args.out.mkdir(parents=True, exist_ok=True)
    c = compile_manifest()
    config = c.resolve('reedsolo.rs_config')(
        **{name: getattr(args, name) for name in ('n', 'k', 'm', 'prim', 'alpha', 'fcr')})
    for stage in ['encoder','decoder']:
        generics=dict(unroll=args.unroll, buffered=args.buffered, RS=config)
        if stage == 'decoder':
            parallelism = decoder_parallelism(
                c, unroll=args.unroll, unroll_berlekamp=args.unroll_berlekamp,
                unroll_omegas=args.unroll_omegas, unroll_forney=args.unroll_forney,
                berlekamp_instances=args.berlekamp_instances,
                forney_instances=args.forney_instances)
            generics = dict(parallelism=parallelism, buffered=args.buffered, RS=config)
            if (args.message_depth is not None or args.message_interval is not None
                    or args.message_memory != 'lut' or args.message_no_bypass):
                depth = args.message_depth
                if args.message_interval is not None:
                    depth = c.resolve('reedsolo_performance.select_message_buffer')(
                        config, generics['parallelism'],
                        c.resolve('reedsolo_performance.frame_interval')(args.message_interval)).depth
                elif depth is None:
                    depth = c.resolve('reedsolo_decoder.full_message_buffer')(
                        config, generics['parallelism']).depth
                generics['message_buffer'] = message_buffer(
                    c, depth=depth, memory=args.message_memory, bypass=not args.message_no_bypass)
        module=c.resolve(module_path(stage))(**generics)
        if args.check:
            print(f'{stage}: elaboration passed (buffered={args.buffered})')
            continue
        module=module.as_verilog()
        path=args.out/f'reedsolo_{stage}.v'
        path.write_text(module.source)
        print(f'{path}: top module {module.module_name}')

if __name__=='__main__':
    try:
        main()
    except khdl.DiagnosticException as exc:
        print(exc.combined_string, file=sys.stderr)
        sys.exit(1)
