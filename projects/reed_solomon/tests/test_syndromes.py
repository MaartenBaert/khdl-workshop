"""Port of reedsolo_syndromes_tb: generated vectors replace the static VHDL tables.

Besides the syndrome block, RsSyndromes forwards each frame's message beats with
commit and cancel flags for a packet FIFO. A replay of those flags with packet
FIFO semantics (cancel, push, commit per cycle) must yield exactly the message
beats of the complete frames.
"""
import pytest
from .streams import run_streams, record_message, committed_message, message_beats
from .util import instance, stream


@pytest.mark.parametrize('unroll', [1, 8, 16])
@pytest.mark.parametrize('buffered', [False, True])
def test_syndromes(rs, vectors, unroll, buffered):
    inst = instance('syndromes', unroll=unroll, buffered=buffered)
    beats = [b for v in vectors for b in stream(v['corrupted'], unroll)]
    message = [b for v in vectors for b in message_beats(stream(v['corrupted'], unroll), rs.k, unroll)]
    events = []
    rows = run_streams(inst, {'input': beats},
                       {'output': (['data'], len(vectors)), 'message': (['data'], len(message))},
                       observe=record_message(events))
    for v, row in zip(vectors, rows['output']):
        assert row['data'].syndromes == v['syndromes'].tolist()
    assert committed_message(events) == message


@pytest.mark.parametrize('buffered', [False, True])
def test_syndromes_discards_bad_length(rs, vectors, buffered):
    inst = instance('syndromes', unroll=8, buffered=buffered)
    bad = stream(vectors[0]['corrupted'][:16], 8)
    good = message_beats(stream(vectors[3]['corrupted'], 8), rs.k, 8)
    events = []
    # Short frame must be discarded; the next frame starts fresh. Only the
    # short frame's first beat is forwarded, then cancelled.
    rows = run_streams(inst, {'input': bad + stream(vectors[3]['corrupted'], 8)},
                       {'output': (['data'], 1), 'message': (['data'], 1 + len(good))},
                       observe=record_message(events))
    assert rows['output'][0]['data'].syndromes == vectors[3]['syndromes'].tolist()
    assert any(cancel for cancel, *_ in events)
    assert committed_message(events) == good
