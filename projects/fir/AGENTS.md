# Pipelined FFA FIR

Read ../../AGENTS.md and ../../skills/khdl/SKILL.md. This project owns its KHDL
namespace; do not import RTL from other projects. Copy/adapt the AXI endpoint
and internal-storage pattern locally; share only independent Python test tools.

- Signed samples and coefficients; exact intermediate integer arithmetic.
- Round once, with ties toward positive infinity, then saturate. The output
  interface's clipped flag is the OR of lane saturation flags, held with data.
- Lane zero is earliest. Every beat has parallel samples; invalid cycles do not
  advance signal time. Outputs drain without future input beats.
- Radix phase alignment includes a provisional incomplete final group. Only
  complete groups advance leaf and decoder history. Metadata follows packets.
- Expose the small legal phase/history offset sets explicitly. Shared phase
  windows use fixed-slice barrel routing; history commits choose one or two
  fixed windows. Dynamic indexing remains appropriate for unrestricted access.
- Leaf stages process final coefficient groups first; earlier groups add their
  convolution to the previous partial result delayed by the group tap count.
- Coefficients arrive h[0] first, one per push. Reset and clear begin a new set;
  simultaneous clear/push is clear-then-push. Loading must not require another
  whole-filter encoding pass. Input waits for a complete encoded coefficient set.
- Clear may discard coefficient-dependent work through normal pop operations.
  Preserve completed results and externally stalled output. Ordered clear
  markers reset downstream decoder history after retained old results.
- Pipeline grouping changes latency, never arithmetic. Sustain one beat per
  clock with continuously valid input and ready output after startup.

Use tools/compile.py's concrete checks before simulation. Run Python tools as
modules from the repository root. Tests use an independent scalar convolution
oracle. Use pytest-owned disk-backed build paths and monitor /tmp usage.
Simulation does not establish synthesis or timing. Current investigations are
in the local notes/fir/architecture.md and notes/fir/synthesis.md. The latter
compares generic Yosys cost, without claiming DSP mapping or physical timing.
