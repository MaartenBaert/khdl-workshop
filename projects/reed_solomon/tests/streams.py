"""Ready/valid driver for modules with several input and output interfaces.

Follows the shared test_support driver's rules: payloads are held until
accepted, handshakes are sampled before the rising edge, stalled outputs must
stay stable, and runs are bounded to catch deadlocks. Every interface stalls
independently.
"""
import random

from test_support.simulation import tick


def run_streams(inst, sources, sinks, *, seed=42, stutter=True, stall_denominator=4,
                max_cycles=200000, observe=None):
    """Drive `sources` {name: [payload fields]} and collect `sinks`
    {name: (fields, count)} until every source is sent and every sink has its
    count. `observe(inst)` runs in every cycle before the edge."""
    rng = random.Random(seed)
    sent = dict.fromkeys(sources, 0)
    pending = dict.fromkeys(sources, False)
    received = {name: [] for name in sinks}
    held = dict.fromkeys(sinks)
    p = inst.ports
    for cycle in range(max_cycles):
        if all(sent[n] == len(beats) for n, beats in sources.items()) and all(
                len(received[n]) == count for n, (_, count) in sinks.items()):
            for name in sources:
                p[name].valid = False
            return received
        for name, beats in sources.items():
            if not pending[name] and sent[name] < len(beats) and (
                    not stutter or rng.randrange(stall_denominator)):
                for field, value in beats[sent[name]].items():
                    port = inst.info_ports[name][field]
                    p[name][field] = port.type.new(**value) if isinstance(value, dict) else value
                pending[name] = True
            p[name].valid = pending[name]
        for name in sinks:
            p[name].ready = not stutter or bool(rng.randrange(stall_denominator))
        inst.step(1)
        if observe is not None:
            observe(inst)
        accepted = {name: pending[name] and p[name].ready for name in sources}
        for name, (fields, count) in sinks.items():
            valid = p[name].valid
            snapshot = {field: p[name][field] for field in fields} if valid else None
            if held[name] is not None:
                assert valid and snapshot == held[name], f'{name} changed during a stall in cycle {cycle}'
            ready = p[name].ready
            held[name] = snapshot if valid and not ready else None
            if valid and ready:
                received[name].append(snapshot)
                assert len(received[name]) <= count, f'unexpected extra {name} output'
        tick(inst)
        for name, was_accepted in accepted.items():
            if was_accepted:
                sent[name] += 1
                pending[name] = False
    raise AssertionError(f'timeout after {max_cycles} cycles: sent {sent}, received '
                         f'{ {n: len(r) for n, r in received.items()} }')



def record_message(events):
    """An observe hook recording RsSyndromes' message stream and flags."""
    def observe(inst):
        p = inst.ports
        push = p.message.valid and p.message.ready
        events.append((p.message_cancel, push, list(p.message.data) if push else None,
                       p.message_commit))
    return observe


def committed_message(events):
    """Replay recorded flags with packet FIFO semantics: cancel, push, commit."""
    entries, count = [], 0
    for cancel, push, data, commit in events:
        if cancel:
            del entries[count:]
        if push:
            entries.append(data)
        if commit:
            count = len(entries)
    return entries[:count]


def message_beats(beats, k, unroll):
    """The codeword beats holding message symbols, as RsSyndromes forwards them."""
    return [beat['data'] for beat in beats][:(k + unroll - 1) // unroll]
