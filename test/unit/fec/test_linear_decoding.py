#
# SPDX-FileCopyrightText: Copyright (c) 2021-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0#

import pytest
import unittest
import scipy as sp
import numpy as np
import tensorflow as tf
from sionna.phy.fec.linear import LinearEncoder
from sionna.phy.fec.utils import GaussianPriorSource, load_parity_check_examples, pcm2gm
from sionna.phy.fec.linear import OSDecoder
from sionna.phy.utils import ebnodb2no, sim_ber
from sionna.phy.channel.awgn import AWGN
from sionna.phy.mapping import Mapper, Demapper, BinarySource
from sionna.phy import Block

class System_Model(Block):
    """System model for channel coding BER simulations.
    """
    def __init__(self,
                 encoder,
                 decoder):

        super().__init__()

        self.source = BinarySource()
        self.channel = AWGN()
        self.mapper = Mapper("pam", 1)
        self.demapper = Demapper("app", "pam", 1)

        self.decoder = decoder
        self.encoder = encoder
        self.coderate = encoder.k/encoder.n

    @tf.function(jit_compile=True)
    def call(self, batch_size, ebno_db):

        no = ebnodb2no(ebno_db, coderate=self.coderate, num_bits_per_symbol=1)

        b = self.source([batch_size, self.encoder.k])
        c = self.encoder(b)
        x = self.mapper(c)
        y = self.channel(x, no)
        llr_ch = self.demapper(y, no)
        c_hat = self.decoder(llr_ch)
        return c, c_hat

class TestOSD(unittest.TestCase):
    """"Unittests for the OSD Algorithm."""

    def test_numerical_stability(self):
        """test numerical stability of the decoder for large LLRs """

        bs = 100
        pcm, k, _, _ = load_parity_check_examples(1)
        enc = LinearEncoder(pcm, is_pcm=True)
        dec = OSDecoder(pcm, is_pcm=True)
        source = BinarySource()

        u = source((bs, k))
        c = enc(u)

        # very large LLRs (decoder clips internally at 1000)
        llr_ch = 1000. * (2*c-1)
        c_hat = dec(llr_ch)
        self.assertTrue(np.array_equal(c_hat.numpy(), c.numpy()))

        # very small LLRs (but still correct)
        llr_ch = 0.0001 * (2*c-1)
        c_hat = dec(llr_ch)
        self.assertTrue(np.array_equal(c_hat.numpy(), c.numpy()))

    def test_error_patterns(self):
        """test that _num_error_patterns() returns correct values."""

        ns = [10, 45, 100, 250] # test for different lengths
        ts = [1, 2, 3, 4, 5] # test for different orders

        # init dummy decoder
        pcm, _, _, _ = load_parity_check_examples(0)
        dec = OSDecoder(pcm, is_pcm=True)

        for n in ns:
            for t in ts:

                # skip large values
                if n>50 and t>3:
                    break

                # compute number of error patterns
                num_eps = dec._num_error_patterns(n, t)
                # ref from scipy
                num_eps_ref= sp.special.comb(n, t, exact=True, repetition=False)

                # numbers must be equal
                self.assertTrue(num_eps==num_eps_ref)

                # Number of generated error patterns must also equal
                ep =  dec._gen_error_patterns(n, t)
                num_com = dec._num_error_patterns(n, t)
                self.assertTrue(num_com==len(ep)), \
                        "Number of error patterns does not match."

    def test_dtype(self):
        """Test support for variable dtypes."""

        pcm, _, n, _ = load_parity_check_examples(1, verbose=True)
        gm = pcm2gm(pcm)

        # only floating point is currently supported
        dt = [tf.float32, tf.float64]
        precisions = ["single", "double"]

        shape = [100, n]
        source = GaussianPriorSource()

        for dt_in in dt:
            for prec, d_out in zip(precisions, dt):
                dec = OSDecoder(gm, precision=prec)
                # variable input dtype
                llr_ch = tf.cast(source(shape, 0.1), dt_in)
                c = dec(llr_ch)
                # output dtype must be as specified
                self.assertTrue(c.dtype==d_out)

    def test_input_consistency(self):
        """Test against inconsistent inputs."""
        id = 2
        pcm, k, n, _ = load_parity_check_examples(id)
        bs = 20
        dec = OSDecoder(pcm, is_pcm=True)

        dec(tf.zeros([bs, n]))

        # batch dimension is flexible
        dec(tf.zeros([bs+1, n]))

        # test for non-invalid input shape
        with self.assertRaises(BaseException):
            x = dec(tf.zeros([bs, n+1]))

        # test for non-binary matrix
        with self.assertRaises(BaseException):
            pcm[1,2] = 2
            dec = OSDecoder(pcm) # we interpret the pcm as gm for this test

        # test for non-binary matrix
        with self.assertRaises(BaseException):
            pcm[3,27] = 2
            dec = OSDecoder(pcm, is_pcm=True)

    @pytest.mark.usefixtures("only_gpu")
    def test_tf_fun(self):
        """Test that graph and XLA mode are supported."""

        @tf.function
        def run_graph(u):
            c = dec(u)
            return c

        @tf.function(jit_compile=True)
        def run_graph_xla(u):
            c = dec(u)
            return c

        pcm, _, n, _ = load_parity_check_examples(2)
        bs = 20
        dec = OSDecoder(pcm, is_pcm=True)
        source = GaussianPriorSource()

        u = source([bs, n], 0.1)
        run_graph(u)
        run_graph_xla(u)

    def test_multi_dimensional(self):
        """Test against arbitrary input shapes.

        The decoder should only operate on axis=-1.
        """
        id = 3
        pcm, _, n, _ = load_parity_check_examples(id)
        # test different shapes
        shapes =[[n],[10, 20, 30, n], [1, 40, n], [10, 2, 3, 4, 3, n]]
        dec = OSDecoder(pcm, is_pcm=True, t=2)
        source = GaussianPriorSource()

        for s in shapes:
            llr = source(s, 0.2)
            llr_ref = tf.reshape(llr, [-1, n])

            c = dec(llr) # encode with shape s
            c_ref = dec(llr_ref) # encode as 2-D array
            s[-1] = n
            c_ref = tf.reshape(c_ref, s)
            self.assertTrue(np.array_equal(c.numpy(), c_ref.numpy()))

    @pytest.mark.usefixtures("only_gpu")
    def test_reference(self):
        """Test against reference implementations.

        We test against ML results for the (7,4) Hamming and
        the (63,45) BCH code.
        """

        ########### (7,4)) Hamming code ###########
        snrs_ref = np.linspace(0, 5, 6)
        blers_ref = np.array([1.832e-01, 1.253e-01, 7.047e-02, 2.899e-02, 1.252e-02, 4.371e-03])

        id = 0 # load code
        pcm, k, n, coderate = load_parity_check_examples(id)
        encoder = LinearEncoder(pcm, is_pcm=True)
        decoder = OSDecoder(encoder=encoder, t=2)

        model = System_Model(encoder, decoder)

        _, bler = sim_ber(model,
                          ebno_dbs=snrs_ref,
                          batch_size=1000,
                          max_mc_iter=500,
                          num_target_block_errors=10000)
        # we allow 20% tolerance to ML;
        self.assertTrue(np.all(np.isclose(bler.numpy(), blers_ref, rtol=0.2)))

        ########### (63,45) BCH code ###########
        snrs_ref = np.array([0, 1.5, 3., 4])
        blers_ref = np.array([6.329e-01,2.445e-01, 2.595e-02, 2.134e-03])

        id = 1 # load code
        pcm, k, n, coderate = load_parity_check_examples(id)
        encoder = LinearEncoder(pcm, is_pcm=True)
        decoder = OSDecoder(encoder=encoder, t=4)

        model = System_Model(encoder, decoder)

        _, bler = sim_ber(model,
                          ebno_dbs=snrs_ref,
                          batch_size=200,
                          max_mc_iter=500,
                          num_target_block_errors=1000)
        # we allow 20% tolerance to ML;
        self.assertTrue(np.all(np.isclose(bler.numpy(), blers_ref, rtol=0.2)))


