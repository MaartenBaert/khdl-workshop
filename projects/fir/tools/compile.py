"""Concrete FIR elaboration checks and Verilog export."""
import argparse
from pathlib import Path
import sys

import khdl

MANIFEST = Path(__file__).resolve().parents[1] / 'rtl/khdl.toml'
PARAMETERS = dict(input_w=16, input_f=15, output_w=16, output_f=15,
                  coeff_w=16, coeff_f=15, taps=16, parallel=4,
                  ffa_depth=2, taps_per_stage=4)


def compile_source():
    return khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in PARAMETERS.items():
        parser.add_argument('--' + name.replace('_', '-'), type=int, default=default)
    parser.add_argument('--pipeline-steps', default='',
                        help='comma-separated algorithm steps per clock stage')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--out', type=Path, default=Path('build/fir/verilog/fir.v'))
    args = parser.parse_args()
    parameters = {name: getattr(args, name) for name in PARAMETERS}
    parameters['pipeline_steps'] = (tuple(map(int, args.pipeline_steps.split(',')))
                                    if args.pipeline_steps else ())
    compiler = compile_source()
    if args.check:
        compiler.resolve('checks.check_examples')()
    module = compiler.resolve('fir.Fir')(**parameters)
    print('FIR concrete elaboration passed')
    if not args.check:
        exported = module.as_verilog()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(exported.source)
        print(f'{args.out}: top module {exported.module_name}')


if __name__ == '__main__':
    try:
        main()
    except khdl.DiagnosticException as exc:
        print(exc.combined_string, file=sys.stderr)
        sys.exit(1)
