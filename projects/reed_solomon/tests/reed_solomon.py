import math
import numpy

class ReedSolomon:

    def __init__(self, n=255, k=223, m=8, prim=0x11d, alpha=2, fcr=0, dtype=numpy.uint16):
        assert 0 < n < 2**m, 'Invalid parameter n'
        assert 0 < k < n, 'Invalid parameter k'
        assert (n - k) % 2 == 0, 'Only an even number of parity bits is supported'
        assert 0 <= fcr <= 1, 'Invalid parameter fcr'

        # primary parameters
        self.n = int(n)
        self.k = int(k)
        self.m = int(m)
        self.prim = int(prim)
        self.alpha = int(alpha)
        self.fcr = int(fcr)
        self.dtype = dtype

        # derived parameters
        self.t = (self.n - self.k) // 2
        self.q = 2**self.m

        # create lookup tables
        self.lut_exp = numpy.zeros(self.q, dtype=self.dtype)
        self.lut_log = numpy.zeros(self.q, dtype=self.dtype)
        self.lut_inv = numpy.zeros(self.q, dtype=self.dtype)

        # fill lookup tables
        x = 1
        for i in range(self.q - 1):
            self.lut_exp[i] = x
            self.lut_log[x] = i
            x = self.gf_mul_nolut(x, self.alpha)
        for i in range(1, self.q - 1):
            self.lut_inv[self.lut_exp[i]] = self.lut_exp[self.q - 1 - i]

        # special values
        self.lut_exp[self.q - 1] = 0
        self.lut_log[0] = self.q - 1
        self.lut_inv[0] = 0
        self.lut_inv[1] = 1

        # sanity check: each value should appear exactly once
        assert (numpy.bincount(self.lut_exp) == 1).all()
        assert (numpy.bincount(self.lut_log) == 1).all()
        assert (numpy.bincount(self.lut_inv) == 1).all()

        # sanity check: mappings should be reversible
        assert (self.lut_exp[self.lut_log] == numpy.arange(self.q, dtype=self.dtype)).all()
        assert (self.lut_log[self.lut_exp] == numpy.arange(self.q, dtype=self.dtype)).all()
        assert (self.lut_inv[self.lut_inv] == numpy.arange(self.q, dtype=self.dtype)).all()

        # print('lut_exp:')
        # print(self.lut_exp)
        # print('lut_log:')
        # print(self.lut_log)
        # print('lut_inv:')
        # print(self.lut_inv)

        # create generator polynomial
        self.gen = numpy.array([1], dtype=self.dtype)
        for i in range(2 * self.t):
            self.gen = self.gf_poly_mul(self.gen, numpy.array([self.gf_exp(self.fcr + i), 1]))
        assert self.gen[-1] == 1

    def gf_mul_nolut(self, a, b):
        res = 0
        for i in range(self.m):
            if (a >> i) & 1:
                res ^= b << i
        for i in reversed(range(self.m)):
            if (res >> (i + self.m)) & 1:
                res ^= self.prim << i
        return res

    def gf_exp(self, a):
        return self.lut_exp[a % (self.q - 1)]

    def gf_log(self, a):
        return self.lut_log[a]

    def gf_inv(self, a):
        return self.lut_inv[a]

    def gf_mul(self, a, b):
        res = self.lut_exp[(self.lut_log[a] + self.lut_log[b]) % (self.q - 1)]
        return numpy.where((a == 0) | (b == 0), 0, res)

    def gf_pow(self, a, b):
        return self.lut_exp[(self.lut_log[a] * b) % (self.q - 1)]

    def gf_poly_eval(self, p, x):
        res = p[-1]
        for i in reversed(range(len(p) - 1)):
            res = self.gf_mul(res, x) ^ p[i]
        return res

    def gf_poly_add(self, p, q):
        res = numpy.zeros(max(len(p), len(q)), dtype=self.dtype)
        res[: len(p)] = p
        res[: len(q)] ^= q
        return res

    def gf_poly_mul(self, p, q):
        res = numpy.zeros(len(p) + len(q) - 1, dtype=self.dtype)
        for i in range(len(q)):
            res[i : i + len(p)] ^= self.gf_mul(p, q[i])
        return res

    def gf_poly_div(self, p, q):
        quot = numpy.zeros(len(p) - len(q) + 1, dtype=self.dtype)
        rem = p.copy()
        qinv = self.gf_inv(q[-1])
        for i in reversed(range(len(p) - len(q) + 1)):
            quot[i] = self.gf_mul(rem[i + len(q) - 1], qinv)
            rem[i : i + len(q)] ^= self.gf_mul(q, quot[i])
        return (quot, rem[: len(q) - 1])

    def rs_parity(self, message):
        parity = numpy.zeros(2 * self.t, dtype=self.dtype)
        for i in reversed(range(self.k)):
            coef = parity[-1] ^ message[i]
            parity[1 :] = parity[: -1]
            parity[0] = 0
            parity ^= self.gf_mul(self.gen[: -1], coef)
        return parity

    def rs_parity_unrolled(self, message, unroll):
        table = numpy.zeros((unroll, 2 * self.t), dtype=self.dtype)
        table[0] = self.gen[: -1]
        for i in range(1, unroll):
            coef = table[i - 1, -1]
            table[i, 1 :] = table[i - 1, : -1]
            table[i, 0] = 0
            table[i] ^= self.gf_mul(self.gen[: -1], coef)
        parity = numpy.zeros(2 * self.t, dtype=self.dtype)
        for i in reversed(range(0, self.k, unroll)):
            coef = parity[-unroll : ] ^ message[i : i + unroll]
            parity[unroll :] = parity[: -unroll]
            parity[: unroll] = 0
            parity ^= numpy.bitwise_xor.reduce(self.gf_mul(table, coef[:, None]), axis=0)
        return parity

    def rs_syndromes(self, codeword):
        alphas = self.gf_exp(self.fcr + numpy.arange(2 * self.t))
        syndromes = numpy.zeros(2 * self.t, dtype=self.dtype)
        for i in reversed(range(self.n)):
            syndromes = self.gf_mul(syndromes, alphas) ^ codeword[i]
        return syndromes

    def rs_syndromes_unrolled(self, codeword, unroll):
        ii = numpy.arange(unroll)
        jj = numpy.arange(2 * self.t)
        table = self.gf_exp((1 + ii[:,None]) * (self.fcr + jj[None,:]))
        syndromes = numpy.zeros(2 * self.t, dtype=self.dtype)
        for i in reversed(range(0, self.n, unroll)):
            temp = self.gf_mul(codeword[i + 1 : i + unroll, None], table[:-1])
            subpoly = codeword[i] ^ numpy.bitwise_xor.reduce(temp, axis=0)
            syndromes = self.gf_mul(syndromes, table[-1]) ^ subpoly
        return syndromes

    def rs_berlekamp(self, syndromes):
        syndromes = syndromes.copy()
        cx = numpy.zeros(self.t + 1, dtype=self.dtype)
        cx[0] = 1
        bx = numpy.zeros(self.t + 1, dtype=self.dtype)
        bx[0] = 1
        b = 1
        num_errors = 0
        for i in range(2 * self.t):
            temp = syndromes[0]
            syndromes[: -1] = syndromes[1 :]
            syndromes[-1] = temp
            subsyndromes = syndromes[self.t - 1 :]
            bx[1 :] = bx[: -1]
            bx[0] = 0
            d = numpy.bitwise_xor.reduce(self.gf_mul(cx, subsyndromes[::-1]))
            cxb = self.gf_mul(cx, b)
            cx = cxb ^ self.gf_mul(bx, d)
            if d != 0 and 2 * num_errors <= i:
                num_errors = i + 1 - num_errors
                bx = cxb
                b = self.gf_mul(b, d)
        return (cx, num_errors)

    def rs_omegas(self, syndromes, lambdas):
        omegas = syndromes.copy()[: self.t]
        mask = numpy.zeros(self.t, dtype=self.dtype)
        for i in range(self.t):
            coef = omegas[-1]
            omegas ^= self.gf_mul(lambdas[1:], coef) & mask
            omegas[1 :] = omegas[: -1]
            omegas[0] = self.gf_mul(lambdas[0], coef)
            mask[1 :] = mask[: -1]
            mask[0] = self.q - 1
        return omegas

    def rs_chien(self, lambdas, num_errors):
        error_pos = numpy.zeros(num_errors, dtype=self.dtype)
        error_xinv = numpy.zeros(num_errors, dtype=self.dtype)
        error_xinv2 = numpy.zeros(num_errors, dtype=self.dtype)
        alphas = self.gf_exp(-1 - numpy.arange(self.t))
        gammas = lambdas[1:].copy()
        xinv = 1
        index = 0
        for i in range(self.n):
            if numpy.bitwise_xor.reduce(gammas) == lambdas[0]:
                assert index < num_errors
                error_pos[index] = i
                error_xinv[index] = xinv
                index += 1
            gammas = self.gf_mul(gammas, alphas)
            xinv = self.gf_mul(xinv, self.gf_exp(-1))
        if index != num_errors:
            print('warning: index != num_errors')
        return (error_pos, error_xinv)

    def rs_chien_unrolled(self, lambdas, num_errors, unroll):
        ii = numpy.arange(unroll)
        jj = numpy.arange(self.t)
        table = self.gf_exp((1 + ii[:,None]) * (-1 - jj[None,:]))
        error_pos = numpy.zeros(num_errors, dtype=self.dtype)
        error_xinv = numpy.zeros(num_errors, dtype=self.dtype)
        gammas = lambdas[1:].copy()
        xinv = 1
        index = 0
        for i in range(0, self.n, unroll):
            for j in range(unroll):
                temp = gammas if j == 0 else self.gf_mul(gammas, table[j - 1])
                if numpy.bitwise_xor.reduce(temp) == lambdas[0]:
                    assert index < num_errors
                    error_pos[index] = i + j
                    error_xinv[index] = self.gf_mul(xinv, self.gf_exp(-j))
                    index += 1
            gammas = self.gf_mul(gammas, table[-1])
            xinv = self.gf_mul(xinv, self.gf_exp(-unroll))
        if index != num_errors:
            print('warning: index != num_errors')
        return (error_pos, error_xinv)

    def rs_chien_bb(self, lambdas, num_errors, unroll):
        ii = numpy.arange(unroll)
        jj = numpy.arange(self.t)
        table = self.gf_exp((1 + ii[:,None]) * (-1 - jj[None,:]))
        error_pos = numpy.zeros(self.t, dtype=self.dtype)
        error_xinv = numpy.zeros(self.t, dtype=self.dtype)
        gammas = lambdas[1:].copy()
        xinv = 1
        index = 0
        bb_layers = math.ceil(math.log2(unroll))
        bb_width = 2**bb_layers
        bb_disp = numpy.zeros(bb_width - 1, dtype=self.dtype)
        for i in range(0, self.n, unroll):
            bb_flag = numpy.zeros(bb_width, dtype=self.dtype)
            bb_pos = numpy.zeros(bb_width, dtype=self.dtype)
            bb_xinv = numpy.zeros(bb_width, dtype=self.dtype)
            for j in range(unroll):
                temp = gammas if j == 0 else self.gf_mul(gammas, table[j - 1])
                if numpy.bitwise_xor.reduce(temp) == lambdas[0]:
                    bb_flag[j] = 1
                    bb_pos[j] = i + j
                    bb_xinv[j] = self.gf_mul(xinv, self.gf_exp(-j))
                    assert index < num_errors
                    index += 1
            for l in range(bb_layers):
                for g in range(2**(bb_layers - 1 - l)):
                    for h in range(2**l):
                        ind1 = (2 * g + 0) * 2**l + h
                        ind2 = (2 * g + 1) * 2**l + h
                        disp = bb_disp[h + 2**l - 1]
                        bb_disp[h + 2**l - 1] = disp ^ bb_flag[ind1] ^ bb_flag[ind2]
                        if disp == bb_flag[ind1]:
                            (bb_flag[ind1], bb_flag[ind2]) = (bb_flag[ind2], bb_flag[ind1])
                            (bb_pos[ind1], bb_pos[ind2]) = (bb_pos[ind2], bb_pos[ind1])
                            (bb_xinv[ind1], bb_xinv[ind2]) = (bb_xinv[ind2], bb_xinv[ind1])
            for i in range(min(bb_width, self.t)):
                if bb_flag[i]:
                    for j in reversed(range((self.t - i - 1) // bb_width)):
                        error_pos[i + (j + 1) * bb_width] = error_pos[i + j * bb_width]
                        error_xinv[i + (j + 1) * bb_width] = error_xinv[i + j * bb_width]
                    error_pos[i] = bb_pos[i]
                    error_xinv[i] = bb_xinv[i]
            gammas = self.gf_mul(gammas, table[-1])
            xinv = self.gf_mul(xinv, self.gf_exp(-unroll))
        if index != num_errors:
            print('warning: index != num_errors')
        assert (error_pos[num_errors :] == 0).all()
        return (error_pos[:num_errors], error_xinv[:num_errors])

    def rs_forney(self, num_errors, lambdas, omegas, error_pos, error_xinv):
        error_val = numpy.zeros(num_errors, dtype=self.dtype)
        terms = numpy.concatenate((omegas, lambdas[1::2]))
        hold = self.gf_mul(error_xinv, error_xinv)
        accum = terms[-1]
        terms[1 :] = terms[: -1]
        terms[0] = 0
        for i in range((self.t + 1) // 2 - 1):
            accum = self.gf_mul(accum, hold) ^ terms[-1]
            terms[1 :] = terms[: -1]
            terms[0] = 0
        if self.fcr == 0:
            accum = self.gf_mul(accum, error_xinv)
        if (accum == 0).any():
            print('warning: denominator == 0')
        hold = accum
        accum = 0
        for i in range(self.t):
            accum = self.gf_mul(accum, error_xinv) ^ terms[-1]
            terms[1 :] = terms[: -1]
            terms[0] = 0
        error_val = self.gf_mul(accum, self.gf_inv(hold))
        return error_val

    def rs_correction(self, codeword, error_pos, error_val):
        message = codeword.copy()
        message[error_pos] ^= error_val
        return message[self.n - self.k :]

    def encode(self, message):
        assert message.ndim == 1, 'Incorrect message shape'
        assert message.shape[0] == self.k, 'Incorrect message length'
        assert message.dtype == self.dtype, 'Incorrect message dtype'

        parity = self.rs_parity(message)
        parity_u = self.rs_parity_unrolled(message, 4)
        assert (parity == parity_u).all()

        return numpy.concatenate((parity, message))

    def decode(self, codeword):
        assert codeword.ndim == 1, 'Incorrect codeword shape'
        assert codeword.shape[0] == self.n, 'Incorrect codeword length'
        assert codeword.dtype == self.dtype, 'Incorrect codeword dtype'

        syndromes = self.rs_syndromes(codeword)
        syndromes_u = self.rs_syndromes_unrolled(codeword, 4)
        assert (syndromes == syndromes_u).all()

        (lambdas, num_errors) = self.rs_berlekamp(syndromes)
        if num_errors > self.t:
            print('warning: num_errors > t')
            num_errors = self.t

        omegas = self.rs_omegas(syndromes, lambdas)
        (error_pos, error_xinv) = self.rs_chien(lambdas, num_errors)
        (error_pos_u, error_xinv_u) = self.rs_chien_unrolled(lambdas, num_errors, 4)
        (error_pos_b, error_xinv_b) = self.rs_chien_bb(lambdas, num_errors, 4)
        bsort = numpy.argsort(error_pos_b)
        assert (error_pos == error_pos_u).all()
        assert (error_xinv == error_xinv_u).all()
        assert (error_pos == error_pos_b[bsort]).all()
        assert (error_xinv == error_xinv_b[bsort]).all()

        error_val = self.rs_forney(num_errors, lambdas, omegas, error_pos, error_xinv)

        message = self.rs_correction(codeword, error_pos, error_val)

        return message

    def add_random_errors(self, rng, codeword, num_errors=None):

        if num_errors is None:
            num_errors = int(rng.integers(0, self.t, endpoint=True))

        error_pos = rng.choice(self.k, num_errors, replace=False)
        error_pos.sort()
        error_val = rng.integers(1, 2**self.m, num_errors, dtype=self.dtype)

        codeword = codeword.copy()
        codeword[error_pos] ^= error_val

        return (codeword, error_pos, error_val)
