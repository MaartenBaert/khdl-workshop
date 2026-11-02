"""Independent scalar integer convolution and final fixed-point conversion."""
def filter_samples(samples, coefficients, *, input_f, coeff_f, output_f, output_w):
    shift = input_f + coeff_f - output_f
    low, high = -(1 << (output_w - 1)), (1 << (output_w - 1)) - 1
    results, clipped = [], []
    for n in range(len(samples)):
        total = sum(coefficients[k] * samples[n-k]
                    for k in range(min(n+1, len(coefficients))))
        if shift > 0:
            total = (total + (1 << (shift-1))) // (1 << shift)
        elif shift < 0:
            total <<= -shift
        clipped.append(not low <= total <= high)
        results.append(max(low, min(high, total)))
    return results, clipped


def beats(samples, coefficients, config):
    values, clipped = filter_samples(samples, coefficients, **{
        key: config[key] for key in ('input_f', 'coeff_f', 'output_f', 'output_w')})
    width = config['parallel']
    return [dict(samples=values[i:i+width], clipped=any(clipped[i:i+width]))
            for i in range(0, len(values), width)]
