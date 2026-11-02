"""Exact timing, optimal elaboration-time selection, and measured frame rates."""
from fractions import Fraction
from itertools import product

import khdl
import pytest

from .test_decoder_padding import vectors
from .test_decoder_parallel import exercise
from .util import MANIFEST, compile_manifest, decoder_parallelism, instance, message_side, stream, tick, transfer


CONFIG = dict(n=13, k=7, m=4, prim=0x13, alpha=3, fcr=0)
FIELDS = ('stream_unroll', 'berlekamp_unroll', 'omegas_unroll', 'forney_unroll', 'berlekamp_instances', 'forney_instances')
ROOT = 'reedsolo_performance.'


def interval(value):
    return Fraction(value.cycles, value.frames)


def plan_tuple(plan):
    return tuple(getattr(plan, field) for field in FIELDS)


def costs(c, weights):
    linear = c.resolve(ROOT + 'RsLinearCost')
    return c.resolve(ROOT + 'RsDecoderCosts').new(
        syndromes=linear.new(a=weights[0], b=weights[1]),
        berlekamp=linear.new(a=weights[2], b=weights[3]),
        chien=c.resolve(ROOT + 'RsChienCost').new(
            a_search=weights[4], a_omegas=weights[5], b=weights[6]),
        forney=linear.new(a=weights[7], b=weights[8]),
        correction=linear.new(a=weights[9], b=weights[10]))


def reference(n, k, fcr, plan, weights):
    u, ub, uo, uf, nb, nf = plan
    t = (n - k) // 2
    c = (n + u - 1) // u
    b = 2 * t // ub
    h = max(c, t // uo)
    f = ((t + 1) // 2 - fcr + t + 2 + uf - 1) // uf
    latency = c + b + h + f + 1
    rate = max(Fraction(h), Fraction(b, nb), Fraction(f, nf))
    a_s, b_s, a_b, b_b, a_h, a_o, b_h, a_f, b_f, a_c, b_c = weights
    cost = a_s*u+b_s + nb*(a_b*ub+b_b) + a_h*u+a_o*uo+b_h + nf*(a_f*uf+b_f) + a_c*u+b_c
    return latency, rate, cost


@pytest.mark.parametrize('config', [CONFIG, dict(n=7, k=3, m=3, prim=0xb, alpha=2, fcr=1)])
@pytest.mark.parametrize('weights', [(1,)*11, (9, 2, 1, 30, 7, 4, 8, 3, 21, 2, 9), (0,)*11])
@pytest.mark.parametrize('limit,latency', [(Fraction(4), 22), (Fraction(7, 2), 17), (Fraction(1), 9)])
def test_selector_is_optimal(config, weights, limit, latency):
    n, k, t = config['n'], config['k'], (config['n']-config['k'])//2
    candidates = []
    # Deliberately exhaustive, including wider dominated widths and extra
    # workers: independent of the helper's pruning/minimal-bank construction.
    for u, ub, uo, uf in product(range(1, n+3), range(1, 3), range(1, t+1), range(1, 4)):
        if 2*t % ub or t % uo or uf > 2*t:
            continue
        for nb, nf in product(range(1, 2*t+2), range(1, t+(t+1)//2+3)):
            plan = (u, ub, uo, uf, nb, nf)
            l, r, cost = reference(n, k, config['fcr'], plan, weights)
            if l <= latency and r <= limit:
                candidates.append((cost, plan))
    # Successful calls share the project context; a failed elaboration must
    # use its own context because diagnostics persist in the compiler.
    c = compile_manifest() if candidates else khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    rs = c.resolve('reedsolo.rs_config')(**config)
    model = costs(c, weights)
    target = c.resolve(ROOT + 'frame_interval')(limit.numerator, limit.denominator)
    select = c.resolve(ROOT + 'select_decoder_parallelism')
    if not candidates:
        with pytest.raises(khdl.DiagnosticException) as exc:
            select(rs, 2, 3, model, latency, target)
        assert 'No decoder parallelism' in exc.value.combined_string
    else:
        plan = select(rs, 2, 3, model, latency, target)
        l, r, cost = reference(n, k, config['fcr'], plan_tuple(plan), weights)
        assert l <= latency and r <= limit
        assert cost == min(cost for cost, _ in candidates)
        assert c.resolve(ROOT + 'decoder_cost')(plan, model) == cost
        predicted = c.resolve(ROOT + 'decoder_timing')(rs, plan)
        assert predicted.latency == l and interval(predicted.interval) == r
        # Stable ties, and selected plans really elaborate in both modes.
        assert plan_tuple(select(rs, 2, 3, model, latency, target)) == plan_tuple(plan)
        for buffered in (False, True):
            c.resolve('reedsolo_decoder.RsDecoder')(parallelism=plan, buffered=buffered, RS=rs)


@pytest.mark.parametrize('limit', [Fraction(1), Fraction(7, 2), Fraction(13), Fraction(100)])
def test_encoder_selector(limit):
    c = compile_manifest()
    rs = c.resolve('reedsolo.rs_config')(**CONFIG)
    target = c.resolve(ROOT + 'frame_interval')(limit.numerator, limit.denominator)
    width = c.resolve(ROOT + 'select_encoder_unroll')(rs, 1, target)
    assert width == min(u for u in range(1, rs.n+1) if Fraction((rs.n+u-1)//u) <= limit)
    result = c.resolve(ROOT + 'encoder_timing')(rs, width)
    assert result.latency == 1 and interval(result.interval) <= limit
    for buffered in (False, True):
        c.resolve('reedsolo_encoder.RsEncoder')(unroll=width, buffered=buffered, RS=rs)


@pytest.mark.parametrize('name,args,error', [
    ('select_encoder_unroll', (0, (1, 1)), 'one cycle'),
    ('select_encoder_unroll', (1, (1, 2)), 'less than one cycle'),
    ('select_decoder_parallelism', (1, 1, 0, (4, 1)), 'No decoder parallelism'),
    ('select_decoder_parallelism', (1, 1, 100, (1, 2)), 'less than one cycle'),
    ('berlekamp_timing', (4,), 'divide 2*t'),
    ('chien_timing', (4, 2), 'divide t'),
    ('forney_timing', (7,), 'not exceed'),
])
def test_rejected_requests(name, args, error):
    c = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    rs = c.resolve('reedsolo.rs_config')(**CONFIG)
    if name.startswith('select_'):
        *args, ratio = args
        target = c.resolve(ROOT + 'frame_interval')(*ratio)
        if name == 'select_decoder_parallelism':
            ub, uf, latency = args
            args = (ub, uf, costs(c, (1,)*11), latency, target)
        else:
            args = (*args, target)
    with pytest.raises(khdl.DiagnosticException) as exc:
        c.resolve(ROOT + name)(rs, *args)
    assert error in exc.value.combined_string


@pytest.mark.parametrize('cycles,frames', [(7, 2), (14, 4), (3, 6), (8, 8)])
def test_frame_interval_exact(cycles, frames):
    result = compile_manifest().resolve(ROOT + 'frame_interval')(cycles, frames)
    assert interval(result) == Fraction(cycles, frames)
    assert result.cycles == Fraction(cycles, frames).numerator
    assert result.frames == Fraction(cycles, frames).denominator


@pytest.mark.parametrize('stage,width,unroll,buffered', [
    (*profile, buffered) for profile in [
        ('encoder', 4, 1), ('encoder', 16, 1),
        ('syndromes', 4, 1), ('syndromes', 16, 1),
        ('berlekamp', 4, 1), ('berlekamp', 4, 3),
        ('chien', 4, 1), ('chien', 16, 3),
        ('forney', 4, 1), ('forney', 4, 4),
        ('correction', 4, 1), ('correction', 16, 1),
    ] for buffered in ((False, True) if profile[0] in ('encoder', 'syndromes') else (False,))
])
def test_stage_timing(stage, width, unroll, buffered):
    c = compile_manifest()
    rs = c.resolve('reedsolo.rs_config')(**CONFIG)
    payload = {}
    kwargs = dict(CONFIG)
    timing_args = dict(RS=rs)
    if stage in ('encoder', 'syndromes', 'correction'):
        kwargs.update(unroll=width)
        timing_args.update(unroll=width)
    if stage in ('encoder', 'syndromes'):
        kwargs.update(buffered=buffered)
        inputs = stream([0]*(rs.k if stage == 'encoder' else rs.n), width)
    else:
        if stage == 'berlekamp':
            kwargs.update(unroll=unroll)
            timing_args.update(unroll=unroll)
            payload.update(syndromes=[0]*(2*rs.t))
        elif stage == 'chien':
            kwargs.update(unroll_chien=width, unroll_omegas=unroll)
            timing_args.update(unroll_chien=width, unroll_omegas=unroll)
            payload.update(syndromes=[0]*(2*rs.t), lambdas=[1]+[0]*rs.t, num_errors=0)
        else:
            if stage == 'forney':
                payload.update(omegas=[0]*rs.t, lambdas=[1]+[0]*rs.t)
            payload.update(error_cpos=[(rs.n+width-1)//width]*rs.t,
                           error_upos=[0]*rs.t)
            if stage == 'forney':
                kwargs.update(unroll=unroll, unroll_chien=width)
                timing_args.update(unroll=unroll)
                payload.update(error_xinv=[0]*rs.t)
            else:
                payload.update(error_val=[0]*rs.t)
            payload.update(num_errors=0, failed=False)
        inputs = [dict(data=payload)]
    model = c.resolve(ROOT + stage + '_timing')(**timing_args)
    beats = ((rs.n if stage == 'encoder' else rs.k)+width-1)//width if stage in ('encoder', 'correction') else 1
    # A quiet message stream: always drained, or always available.
    inst = message_side(instance(stage, **kwargs))
    times = []
    previous = 0
    for cycle, (sent, rows) in enumerate(transfer(inst, inputs*16, ['data'], stutter=False)):
        if len(rows) > previous:
            if previous % beats == 0:
                times.append(cycle)
            previous = len(rows)
        if len(rows) == 16*beats:
            break
    assert sent == len(inputs)*16
    assert times[0] == model.latency
    assert all(b-a == interval(model.interval) for a, b in zip(times, times[1:]))


@pytest.mark.parametrize('width,ub,uf,nb,nf', [
    (4, 1, 1, 2, 3), (13, 1, 1, 2, 2), (13, 1, 1, 2, 3),
    (13, 1, 1, 3, 2), (13, 3, 1, 1, 2), (13, 2, 2, 2, 3),
])
@pytest.mark.parametrize('buffered', [False, True])
def test_decoder_timing(width, ub, uf, nb, nf, buffered):
    c = compile_manifest()
    rs = c.resolve('reedsolo.rs_config')(**CONFIG)
    plan = decoder_parallelism(c, unroll=width, unroll_berlekamp=ub,
                               unroll_omegas=3, unroll_forney=uf,
                               berlekamp_instances=nb, forney_instances=nf)
    model = c.resolve(ROOT + 'decoder_timing')(rs, plan)
    reference_rs, frames = vectors(**CONFIG, width=width)
    inst = instance('decoder', **CONFIG, unroll=width, unroll_berlekamp=ub,
                    unroll_omegas=3, unroll_forney=uf, berlekamp_instances=nb,
                    forney_instances=nf, buffered=buffered)
    first, times = exercise(inst, reference_rs, frames*4, width, False)
    assert first == model.latency
    # Discard pipeline warmup; compare complete worker-bank periods rather
    # than individual gaps, which may be unequal for a fractional interval.
    period = nb*nf
    measured = {Fraction(times[i+period]-times[i], period)
                for i in range(len(times)//2, len(times)-period)}
    assert measured == {interval(model.interval)}


@pytest.mark.parametrize('limit,latency,cap_b', [(Fraction(4), 22, 1), (Fraction(7, 2), 12, 3)])
@pytest.mark.parametrize('buffered', [False, True])
def test_selected_decoder_meets_specs(limit, latency, cap_b, buffered):
    c = compile_manifest()
    rs = c.resolve('reedsolo.rs_config')(**CONFIG)
    target = c.resolve(ROOT + 'frame_interval')(limit.numerator, limit.denominator)
    plan = c.resolve(ROOT + 'select_decoder_parallelism')(rs, cap_b, 1, costs(c, (1,)*11), latency, target)
    u, ub, uo, uf, nb, nf = plan_tuple(plan)
    reference_rs, frames = vectors(**CONFIG, width=u)
    inst = instance('decoder', **CONFIG, unroll=u, unroll_berlekamp=ub,
                    unroll_omegas=uo, unroll_forney=uf, berlekamp_instances=nb,
                    forney_instances=nf, buffered=buffered)
    first, times = exercise(inst, reference_rs, frames*4, u, False)
    assert first <= latency
    period = nb*nf
    assert all(Fraction(times[i+period]-times[i], period) <= limit
               for i in range(len(times)//2, len(times)-period))


@pytest.mark.parametrize('kind,fields', [
    ('RsFrameInterval', dict(cycles=1, frames=0)),
    ('RsFrameInterval', dict(cycles=0, frames=1)),
    ('RsLinearCost', dict(a=-1, b=1)),
    ('RsChienCost', dict(a_search=1, a_omegas=-1, b=0)),
])
def test_invalid_model_values(kind, fields):
    c = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    with pytest.raises(khdl.DiagnosticException) as exc:
        c.resolve(ROOT + kind).new(**fields)
    assert 'type mismatch' in exc.value.combined_string


def test_router_timing():
    model = compile_manifest().resolve(ROOT + 'round_robin_timing')()
    assert model.latency == 0 and interval(model.interval) == 1


BUFFER_PLANS = [  # (width, Berlekamp unroll, Forney unroll, Berlekamp workers, Forney workers)
    (4, 1, 4, 1, 1), (4, 3, 4, 1, 1), (3, 3, 4, 1, 1),
    (4, 1, 1, 2, 3), (13, 3, 1, 1, 2), (13, 1, 1, 2, 3),
]


def selected_buffer(width, ub, uf, nb, nf, cycles=None):
    c = compile_manifest()
    rs = c.resolve('reedsolo.rs_config')(**CONFIG)
    plan = decoder_parallelism(c, unroll=width, unroll_berlekamp=ub, unroll_omegas=3,
                               unroll_forney=uf, berlekamp_instances=nb, forney_instances=nf)
    model = c.resolve(ROOT + 'decoder_timing')(rs, plan)
    target = model.interval if cycles is None else c.resolve(ROOT + 'frame_interval')(cycles)
    buffer = c.resolve(ROOT + 'select_message_buffer')(rs, plan, target)
    full = c.resolve('reedsolo_decoder.full_message_buffer')(rs, plan)
    return model, buffer.depth, full.depth


def buffered_decoder(width, ub, uf, nb, nf, depth):
    return instance('decoder', **CONFIG, unroll=width, unroll_berlekamp=ub, unroll_omegas=3,
                    unroll_forney=uf, berlekamp_instances=nb, forney_instances=nf,
                    message_depth=depth)


@pytest.mark.parametrize('width,ub,uf,nb,nf', BUFFER_PLANS)
def test_selected_message_buffer_keeps_saturated_timing(width, ub, uf, nb, nf):
    """With continuous input, the selected buffer keeps the decoder's latency and interval."""
    model, depth, full = selected_buffer(width, ub, uf, nb, nf)
    assert depth <= full
    reference_rs, frames = vectors(**CONFIG, width=width)
    first, times = exercise(buffered_decoder(width, ub, uf, nb, nf, depth),
                            reference_rs, frames*4, width, False)
    assert first == model.latency
    period = nb*nf
    measured = {Fraction(times[i+period]-times[i], period)
                for i in range(len(times)//2, len(times)-period)}
    assert measured == {interval(model.interval)}


def paced_output_times(inst, beats, width, spacing, frames):
    """Offer frame j's beats from cycle j*spacing on; return output transfer cycles."""
    p = inst.ports
    per_frame = (CONFIG['n'] + width - 1) // width
    expected_outputs = frames * ((CONFIG['k'] + width - 1) // width)
    p.output.ready = True
    sent, times = 0, []
    for cycle in range(frames * spacing + 200):
        offer = sent < len(beats) and cycle >= (sent // per_frame) * spacing
        p.input.valid = offer
        if offer:
            p.input.data = beats[sent]['data']
            p.input.last = beats[sent]['last']
        inst.step(1)
        accepted = offer and p.input.ready
        if p.output.valid:
            times.append(cycle)
        tick(inst)
        sent += accepted
        if len(times) == expected_outputs:
            return times
    raise AssertionError('paced run did not finish')


@pytest.mark.parametrize('width,ub,uf,nb,nf', [plan for plan in BUFFER_PLANS if plan[3] == plan[4] == 1])
@pytest.mark.parametrize('slowdown', [1, 2])
def test_selected_message_buffer_for_slower_input(width, ub, uf, nb, nf, slowdown):
    """For input paced at a slower interval, the smaller selected buffer gives
    exactly the output timing of a full-capacity buffer."""
    model, _, _ = selected_buffer(width, ub, uf, nb, nf)
    spacing = interval(model.interval) * slowdown + 1
    _, depth, full = selected_buffer(width, ub, uf, nb, nf, cycles=int(spacing))
    assert depth < full
    reference_rs, frames = vectors(**CONFIG, width=width)
    beats = [b for v in frames*2 for b in stream(v['damaged'], width)]
    selected = paced_output_times(buffered_decoder(width, ub, uf, nb, nf, depth),
                                  beats, width, int(spacing), len(frames)*2)
    reference = paced_output_times(buffered_decoder(width, ub, uf, nb, nf, full),
                                   beats, width, int(spacing), len(frames)*2)
    assert selected == reference


def throttled_cycles(width, ub, uf, nb, nf, depth, frames):
    """Cycles per `frames` output frames, worst case over steady state, for continuous input."""
    reference_rs, vectors_ = vectors(**CONFIG, width=width)
    _, times = exercise(buffered_decoder(width, ub, uf, nb, nf, depth),
                        reference_rs, vectors_*8, width, False)
    return max(times[i+frames] - times[i] for i in range(len(times)//2, len(times)-frames))


@pytest.mark.parametrize('width,ub,uf,nb,nf', BUFFER_PLANS)
@pytest.mark.parametrize('slowdown', [Fraction(3, 2), 3])
def test_selected_message_buffer_throttles_to_target(width, ub, uf, nb, nf, slowdown):
    """When the selected buffer throttles continuous input, the decoder still
    sustains the target interval, and a buffer one frame smaller does not."""
    model, _, _ = selected_buffer(width, ub, uf, nb, nf)
    target = -(-interval(model.interval) * slowdown // 1)
    _, depth, full = selected_buffer(width, ub, uf, nb, nf, cycles=int(target))
    assert depth <= full
    beats = (CONFIG['k'] + width - 1) // width
    for smaller in (False, True):
        # A one-beat message frame also occupies RsSyndromes' message register,
        # which acts as one more frame of space.
        if smaller and (beats == 1 or depth == beats):
            continue
        tested = depth - beats if smaller else depth
        window = (tested // beats) * nb * nf  # whole periods of the slot and worker patterns
        cycles = throttled_cycles(width, ub, uf, nb, nf, tested, window)
        assert (cycles > window * target) if smaller else (cycles <= window * target)


def test_message_buffer_faster_than_decoder_rejected():
    c = khdl.Source.new_from_manifest_path(str(MANIFEST)).compile()
    rs = c.resolve('reedsolo.rs_config')(**CONFIG)
    plan = decoder_parallelism(c, unroll=4, unroll_berlekamp=3, unroll_omegas=3, unroll_forney=4)
    with pytest.raises(khdl.DiagnosticException, match='cannot sustain'):
        c.resolve(ROOT + 'select_message_buffer')(rs, plan, c.resolve(ROOT + 'frame_interval')(3))
