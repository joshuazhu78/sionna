#
# SPDX-FileCopyrightText: Copyright (c) 2021-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
"""Class definition for the OFDM Demodulator"""

import tensorflow as tf
from tensorflow.keras.layers import Layer
from tensorflow.signal import fftshift
from sionna.constants import PI
from sionna.utils import expand_to_rank
from sionna.signal import fft
import numpy as np

class OFDMDemodulator(Layer):
    # pylint: disable=line-too-long
    r"""
    OFDMDemodulator(fft_size, l_min, cyclic_prefix_length, **kwargs)

    Computes the frequency-domain representation of an OFDM waveform
    with cyclic prefix removal.

    The demodulator assumes that the input sequence is generated by the
    :class:`~sionna.channel.TimeChannel`. For a single pair of antennas,
    the received signal sequence is given as:

    .. math::

        y_b = \sum_{\ell =L_\text{min}}^{L_\text{max}} \bar{h}_\ell x_{b-\ell} + w_b, \quad b \in[L_\text{min}, N_B+L_\text{max}-1]

    where :math:`\bar{h}_\ell` are the discrete-time channel taps,
    :math:`x_{b}` is the the transmitted signal,
    and :math:`w_\ell` Gaussian noise.

    Starting from the first symbol, the demodulator cuts the input
    sequence into pieces of size ``cyclic_prefix_length + fft_size``,
    and throws away any trailing symbols. For each piece, the cyclic
    prefix is removed and the ``fft_size``-point discrete Fourier
    transform is computed. It is also possible that every OFDM symbol
    has a cyclic prefix of different length.

    Since the input sequence starts at time :math:`L_\text{min}`,
    the FFT-window has a timing offset of :math:`L_\text{min}` symbols,
    which leads to a subcarrier-dependent phase shift of
    :math:`e^{\frac{j2\pi k L_\text{min}}{N}}`, where :math:`k`
    is the subcarrier index, :math:`N` is the FFT size,
    and :math:`L_\text{min} \le 0` is the largest negative time lag of
    the discrete-time channel impulse response. This phase shift
    is removed in this layer, by explicitly multiplying
    each subcarrier by  :math:`e^{\frac{-j2\pi k L_\text{min}}{N}}`.
    This is a very important step to enable channel estimation with
    sparse pilot patterns that needs to interpolate the channel frequency
    response accross subcarriers. It also ensures that the
    channel frequency response `seen` by the time-domain channel
    is close to the :class:`~sionna.channel.OFDMChannel`.

    Parameters
    ----------
    fft_size : int
        FFT size (, i.e., the number of subcarriers).

    l_min : int
        The largest negative time lag of the discrete-time channel
        impulse response. It should be the same value as that used by the
        `cir_to_time_channel` function.

    cyclic_prefix_length : scalar or [num_ofdm_symbols], int
        Integer or vector of integers indicating the length of the
        cyclic prefix that is prepended to each OFDM symbol. None of its
        elements can be larger than the FFT size.
        Defaults to 0.

    Input
    -----
    :[...,num_ofdm_symbols*(fft_size+cyclic_prefix_length)+n] or [...,num_ofdm_symbols*fft_size+sum(cyclic_prefix_length)+n], tf.complex
        Tensor containing the time-domain signal along the last dimension.
        `n` is a nonnegative integer.

    Output
    ------
    :[...,num_ofdm_symbols,fft_size], tf.complex
        Tensor containing the OFDM resource grid along the last
        two dimension.
    """

    def __init__(self, fft_size, l_min, cyclic_prefix_length=0, **kwargs):
        super().__init__(**kwargs)
        self._fft_size = None
        self._l_min = None
        self._cyclic_prefix_length = None
        self.fft_size = fft_size
        self.l_min = l_min
        self.cyclic_prefix_length = cyclic_prefix_length

    @property
    def fft_size(self):
        return self._fft_size

    @fft_size.setter
    def fft_size(self, value):
        assert value>0, "`fft_size` must be positive."
        self._fft_size = int(value)

    @property
    def l_min(self):
        return self._l_min

    @l_min.setter
    def l_min(self, value):
        assert value<=0, "l_min must be nonpositive."
        self._l_min = int(value)

    @property
    def cyclic_prefix_length(self):
        return self._cyclic_prefix_length

    @cyclic_prefix_length.setter
    def cyclic_prefix_length(self, value):
        value = tf.cast(value, tf.int32)
        if not tf.reduce_all(value>=0):
            msg = "`cyclic_prefix_length` must be nonnegative."
            raise ValueError(msg)
        if not 0<= tf.rank(value)<=1:
            msg = "`cyclic_prefix_length` must be of rank 0 or 1"
            raise ValueError(msg)
        self._cyclic_prefix_length = value

    def build(self, input_shape): # pylint: disable=unused-argument
        # Compute phase correction terms to to channel
        tmp = -2 * PI * tf.cast(self.l_min, tf.float32) \
              / tf.cast(self.fft_size, tf.float32) \
              * tf.range(self.fft_size, dtype=tf.float32)
        self._phase_compensation = tf.exp(tf.complex(0., tmp))

        if len(self.cyclic_prefix_length.shape)==0:
            # Compute number of elements that will be truncated
            self._rest = np.mod(input_shape[-1],
                                    self.fft_size + self.cyclic_prefix_length)

            # Compute number of full OFDM symbols to be demodulated
            self._num_ofdm_symbols = np.floor_divide(
                                    input_shape[-1]-self._rest,
                                    self.fft_size + self.cyclic_prefix_length)
        else:
            # Deal with individual cp lengths for OFDM symbols
            # Compute the relevant indices to gather for
            # every OFDM symbol from the time domain input
            num_ofdm_symbols = self.cyclic_prefix_length.shape[0]
            row_lengths = self.cyclic_prefix_length + self.fft_size
            offsets = tf.math.cumsum(tf.concat([[0], row_lengths],
                                               axis=0)[:-1])
            offsets = tf.expand_dims(offsets, 1)
            ind = tf.repeat(tf.range(start=0,
                                     limit=self.fft_size)[tf.newaxis,:],
                            repeats=num_ofdm_symbols, axis=0)
            ind += self.cyclic_prefix_length[:, tf.newaxis]
            ind += offsets
            # [num_ofdm_symbols, fft_size]
            self._ind = ind

    def call(self, inputs):
        """Demodulate OFDM waveform onto a resource grid.

        Args:
            inputs (tf.complex64):
                `[...,num_ofdm_symbols*(fft_size+cyclic_prefix_length)]`.

        Returns:
            `tf.complex64` : The demodulated inputs of shape
            `[...,num_ofdm_symbols, fft_size]`.
        """
        if len(self.cyclic_prefix_length.shape)==0:
            # Same CP length for all OFDM symbols
            # Cut last samples that do not fit into an OFDM symbol
            inputs = inputs if self._rest==0 else inputs[...,:-self._rest]

            # Reshape input to separate OFDM symbols
            new_shape = tf.concat(
                            [tf.shape(inputs)[:-1],
                            [self._num_ofdm_symbols],
                            [self.fft_size + self.cyclic_prefix_length]], 0)
            x = tf.reshape(inputs, new_shape)

            # Remove cyclic prefix
            x = x[...,self.cyclic_prefix_length:]

        else:
            # Individual CP length for OFDM symbols
            x = tf.gather(inputs, self._ind, axis=-1)

        # Compute FFT
        x = fft(x)

        # Apply phase shift compensation to all subcarriers
        rot = tf.cast(self._phase_compensation, x.dtype)
        rot = expand_to_rank(rot, tf.rank(x), 0)
        x = x * rot

        # Shift DC subcarrier to the middle
        x = fftshift(x, axes=-1)

        return x
