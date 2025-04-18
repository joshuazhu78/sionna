#
# SPDX-FileCopyrightText: Copyright (c) 2021-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0#
"""Class definition and functions related to pilot patterns"""

import tensorflow as tf
import numpy as np
import matplotlib.pyplot as plt
from matplotlib import colors

from sionna.phy import Object
from sionna.phy.mapping import QAMSource

class PilotPattern(Object):
    # pylint: disable=line-too-long
    r"""Class defining a pilot pattern for an OFDM ResourceGrid

    This class defines a pilot pattern object that is used to configure
    an OFDM :class:`~sionna.phy.ofdm.ResourceGrid`.

    Parameters
    ----------
    mask : [num_tx, num_streams_per_tx, num_ofdm_symbols, num_effective_subcarriers], `bool`
        Tensor indicating resource elements that are reserved for pilot transmissions

    pilots : [num_tx, num_streams_per_tx, num_pilots], `tf.complex`
        The pilot symbols to be mapped onto the ``mask``

    normalize : `bool`, (default `False`)
        Indicates if the ``pilots`` should be normalized to an average
        energy of one across the last dimension.

    precision : `None` (default) | "single" | "double"
        Precision used for internal calculations and outputs.
        If set to `None`,
        :attr:`~sionna.phy.config.Config.precision` is used.
    """
    def __init__(self, mask, pilots, normalize=False,
                 precision=None):
        super().__init__(precision=precision)
        self._mask = tf.cast(mask, tf.int32)
        self.pilots = pilots
        self.normalize = normalize
        self._check_settings()

    @property
    def num_tx(self):
        """
        `int` : Number of transmitters
        """
        return self._mask.shape[0]

    @property
    def num_streams_per_tx(self):
        """
        `int` : Number of streams per transmitter
        """
        return self._mask.shape[1]

    @ property
    def num_ofdm_symbols(self):
        """
        `int` : Number of OFDM symbols
        """
        return self._mask.shape[2]

    @ property
    def num_effective_subcarriers(self):
        """
        `int` : Number of effectvie subcarriers
        """
        return self._mask.shape[3]

    @property
    def num_pilot_symbols(self):
        """
        `int` : Number of pilot symbols per transmit stream
        """
        return tf.shape(self._pilots)[-1]

    @property
    def num_data_symbols(self):
        """
        `int` : Number of data symbols per transmit stream
        """
        return tf.shape(self._mask)[-1]*tf.shape(self._mask)[-2] - \
               self.num_pilot_symbols

    @property
    def normalize(self):
        """
        `bool` : Get/set if the pilots are normalized or not
        """
        return self._normalize

    @normalize.setter
    def normalize(self, value):
        self._normalize = tf.cast(value, tf.bool)

    @property
    def mask(self):
        # pylint: disable=line-too-long
        """
        [num_tx, num_streams_per_tx, num_ofdm_symbols, num_effective_subcarriers], `bool` : Mask of the pilot pattern
        """
        return self._mask

    @property
    def pilots(self):
        """
        [num_tx, num_streams_per_tx, num_pilots], `tf.complex` : Get/set the
            possibly normalized tensor of pilot symbols. If pilots are
            normalized, the normalization will be applied after new values
            for pilots have been set. If this is not the desired behavior,
            turn normalization off.
        """
        def norm_pilots():
            scale = tf.abs(self._pilots)**2
            scale = 1/tf.sqrt(tf.reduce_mean(scale, axis=-1, keepdims=True))
            scale = tf.cast(scale, self.cdtype)
            return scale*self._pilots

        return tf.cond(self.normalize, norm_pilots, lambda: self._pilots)

    @pilots.setter
    def pilots(self, v):
        self._pilots = self._cast_or_check_precision(v)
        # Ensure that pilots are always complex valued
        if isinstance(self._pilots, tf.Tensor):
            self._pilots = tf.cast(self._pilots, self.cdtype)

    def _check_settings(self):
        """Validate that all properties define a valid pilot pattern."""

        assert tf.rank(self._mask)==4, "`mask` must have four dimensions."
        assert tf.rank(self._pilots)==3, "`pilots` must have three dimensions."
        assert np.array_equal(self._mask.shape[:2], self._pilots.shape[:2]), \
            "The first two dimensions of `mask` and `pilots` must be equal."

        num_pilots = tf.reduce_sum(self._mask, axis=(-2,-1))
        assert tf.reduce_min(num_pilots)==tf.reduce_max(num_pilots), \
            """The number of nonzero elements in the masks for all transmitters
            and streams must be identical."""

        assert self.num_pilot_symbols==tf.reduce_max(num_pilots), \
            """The shape of the last dimension of `pilots` must equal
            the number of non-zero entries within the last two
            dimensions of `mask`."""

        return True

    def show(self, tx_ind=None, stream_ind=None, show_pilot_ind=False):
        """Visualizes the pilot patterns for some transmitters and streams.

        Input
        -----
        tx_ind : `None` (default) | `int`| `list`, `int`
            Indicates the indices of transmitters to be included.
            If `None`, all transmitters included.

        stream_ind : `None` (default) | `int`| `list`, `int`
            Indicates the indices of streams to be included.
            If `None`, all streams included.

        show_pilot_ind : `bool`, (default `False`)
            Indicates if the indices of the pilot symbols should be shown

        Output
        ------
        list : matplotlib.figure.Figure
            List of matplot figure objects showing each the pilot pattern
            from a specific transmitter and stream
        """
        mask = self.mask.numpy()
        pilots = self.pilots.numpy()

        if tx_ind is None:
            tx_ind = range(0, self.num_tx)
        elif not isinstance(tx_ind, list):
            tx_ind = [tx_ind]

        if stream_ind is None:
            stream_ind = range(0, self.num_streams_per_tx)
        elif not isinstance(stream_ind, list):
            stream_ind = [stream_ind]

        figs = []
        for i in tx_ind:
            for j in stream_ind:
                q = np.zeros_like(mask[0,0])
                q[np.where(mask[i,j])] = (np.abs(pilots[i,j])==0) + 1
                legend = ["Data", "Pilots", "Masked"]
                fig = plt.figure()
                plt.title(f"TX {i} - Stream {j}")
                plt.xlabel("OFDM Symbol")
                plt.ylabel("Subcarrier Index")
                plt.xticks(range(0, q.shape[1]))
                cmap = plt.cm.tab20c
                b = np.arange(0, 4)
                norm = colors.BoundaryNorm(b, cmap.N)
                im = plt.imshow(np.transpose(q), origin="lower", aspect="auto", norm=norm, cmap=cmap)
                cbar = plt.colorbar(im)
                cbar.set_ticks(b[:-1]+0.5)
                cbar.set_ticklabels(legend)

                if show_pilot_ind:
                    c = 0
                    for t in range(self.num_ofdm_symbols):
                        for k in range(self.num_effective_subcarriers):
                            if mask[i,j][t,k]:
                                if np.abs(pilots[i,j,c])>0:
                                    plt.annotate(c, [t, k])
                                c+=1
                figs.append(fig)

        return figs

class EmptyPilotPattern(PilotPattern):
    """Creates an empty pilot pattern

    Generates a instance of :class:`~sionna.phy.ofdm.PilotPattern` with
    an empty ``mask`` and ``pilots``.

    Parameters
    ----------
    num_tx : `int`
        Number of transmitters

    num_streams_per_tx : `int`
        Number of streams per transmitter

    num_ofdm_symbols : `int`
        Number of OFDM symbols

    num_effective_subcarriers : `int`
        Number of effective subcarriers
        that are available for the transmission of data and pilots.
        Note that this number is generally smaller than the ``fft_size``
        due to nulled subcarriers.

    precision : `None` (default) | "single" | "double"
        Precision used for internal calculations and outputs.
        If set to `None`,
        :attr:`~sionna.phy.config.Config.precision` is used.
    """
    def __init__(self,
                 num_tx,
                 num_streams_per_tx,
                 num_ofdm_symbols,
                 num_effective_subcarriers,
                 precision=None):

        assert num_tx > 0, \
            "`num_tx` must be positive`."
        assert num_streams_per_tx > 0, \
            "`num_streams_per_tx` must be positive`."
        assert num_ofdm_symbols > 0, \
            "`num_ofdm_symbols` must be positive`."
        assert num_effective_subcarriers > 0, \
            "`num_effective_subcarriers` must be positive`."

        shape = [num_tx, num_streams_per_tx, num_ofdm_symbols,
                      num_effective_subcarriers]
        mask = tf.zeros(shape, tf.bool)
        pilots = tf.zeros(shape[:2]+[0], tf.complex64)
        super().__init__(mask, pilots, normalize=False,
                         precision=precision)

class KroneckerPilotPattern(PilotPattern):
    """Simple orthogonal pilot pattern with Kronecker structure

    This function generates an instance of
    :class:`~sionna.phy.ofdm.PilotPattern` that allocates non-overlapping pilot
    sequences for all transmitters and
    streams on specified OFDM symbols. As the same pilot sequences are reused
    across those OFDM symbols, the resulting pilot pattern has a frequency-time
    Kronecker structure. This structure enables a very efficient implementation
    of the LMMSE channel estimator. Each pilot sequence is constructed from
    randomly drawn QPSK constellation points.

    Parameters
    ----------
    resource_grid : :class:`~sionna.phy.ofdm.ResourceGrid`
        Resource grid to be used

    pilot_ofdm_symbol_indices : `list`, `int`
        List of integers defining the OFDM symbol indices that are reserved
        for pilots

    normalize : `bool`, (default `True`)
        Indicates if the ``pilots`` should be normalized to an average
        energy of one across the last dimension.

    seed : `int`, (default 0)
        Seed for the generation of the pilot sequence. Different seed values
        lead to different sequences.

    precision : `None` (default) | "single" | "double"
        Precision used for internal calculations and outputs.
        If set to `None`,
        :attr:`~sionna.phy.config.Config.precision` is used.

    Note
    ----
    It is required that the ``resource_grid``'s property
    ``num_effective_subcarriers`` is an
    integer multiple of ``num_tx * num_streams_per_tx``. This condition is
    required to ensure that all transmitters and streams get
    non-overlapping pilot sequences. For a large number of streams and/or
    transmitters, the pilot pattern becomes very sparse in the frequency
    domain.

    Examples
    --------
    >>> rg = ResourceGrid(num_ofdm_symbols=14,
    ...                   fft_size=64,
    ...                   subcarrier_spacing = 30e3,
    ...                   num_tx=4,
    ...                   num_streams_per_tx=2,
    ...                   pilot_pattern = "kronecker",
    ...                   pilot_ofdm_symbol_indices = [2, 11])
    >>> rg.pilot_pattern.show();

    .. image:: ../figures/kronecker_pilot_pattern.png

    """
    def __init__(self,
                 resource_grid,
                 pilot_ofdm_symbol_indices,
                 normalize=True,
                 seed=0,
                 precision=None):

        num_tx = resource_grid.num_tx
        num_streams_per_tx = resource_grid.num_streams_per_tx
        num_ofdm_symbols = resource_grid.num_ofdm_symbols
        num_effective_subcarriers = resource_grid.num_effective_subcarriers

        # Number of OFDM symbols carrying pilots
        num_pilot_symbols = len(pilot_ofdm_symbol_indices)

        # Compute the total number of required orthogonal sequences
        num_seq = num_tx*num_streams_per_tx

        # Compute the length of a pilot sequence
        num_pilots = num_pilot_symbols*num_effective_subcarriers/num_seq
        assert (num_pilots/num_pilot_symbols)%1==0, \
            """`num_effective_subcarriers` must be an integer multiple of
            `num_tx`*`num_streams_per_tx`."""

        # Number of pilots per OFDM symbol
        num_pilots_per_symbol = int(num_pilots/num_pilot_symbols)

        # Prepare empty mask and pilots
        shape = [num_tx, num_streams_per_tx,
                 num_ofdm_symbols,num_effective_subcarriers]
        mask = np.zeros(shape, bool)
        shape[2] = num_pilot_symbols
        pilots = np.zeros(shape, np.complex64)

        # Populate all selected OFDM symbols in the mask
        mask[..., pilot_ofdm_symbol_indices, :] = True

        # Populate the pilots with random QPSK symbols
        qam_source = QAMSource(2, seed=seed)
        for i in range(num_tx):
            for j in range(num_streams_per_tx):
                # Generate random QPSK symbols
                p = qam_source([1,1,num_pilot_symbols,num_pilots_per_symbol])

                # Place pilots spaced by num_seq to avoid overlap
                pilots[i,j,:,i*num_streams_per_tx+j::num_seq] = p

        # Reshape the pilots tensor
        pilots = np.reshape(pilots, [num_tx, num_streams_per_tx, -1])

        super().__init__(mask, pilots, normalize=normalize,
                         precision=precision)
