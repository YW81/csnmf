"""
   Copyright (c) 2015, Mariano Tepper, Duke University.
   All rights reserved.

   This file is part of RCNMF and is under the BSD 3-Clause License,
   which can be found in the LICENSE file in the root directory, or at
   http://opensource.org/licenses/BSD-3-Clause
"""

import numpy as np
from itertools import count, product
import dask.array as da
from math import ceil
import operator

names = ('tsqr_%d' % i for i in count(1))


def _findnumblocks(shape, blockshape):
    def div_ceil(t):
        return int(ceil(float(t[0]) / t[1]))

    nb = [div_ceil(elem) for elem in zip(*[shape, blockshape])]
    return tuple(nb)


def qr(data, name=None):
    """
    Implementation of the direct TSQR, as presented in:

    A. Benson, D. Gleich, and J. Demmel.
    Direct QR factorizations for tall-and-skinny matrices in
    MapReduce architectures.
    IEEE International Conference on Big Data, 2013.
    http://arxiv.org/abs/1301.1071

    :param data: dask array object
    Shape of the blocks that will be used to compute
    the blocked QR decomposition. We have the restrictions:
    - blockshape[1] == data.shape[1]
    - blockshape[0]*data.shape[1] must fit in main memory
    :return: tuple of dask.array.Array
    First and second tuple elements correspond to Q and R, of the
    QR decomposition.
    """
    if not (data.ndim == 2 and                    # Is a matrix
            len(data.blockdims[1]) == 1):         # Only one column block
        raise ValueError(
            "Input must have the following properites:\n"
            "  1. Have two dimensions\n"
            "  2. Have only one column of blocks")
    blockshape = (data.blockdims[0][0], data.blockdims[1][0])
    m, n = data.shape

    prefix = name or next(names)
    prefix += '_'

    numblocks = _findnumblocks(data.shape, blockshape)

    name_qr_st1 = prefix + 'QR_st1'
    dsk_qr_st1 = da.core.top(np.linalg.qr, name_qr_st1, 'ij', data.name, 'ij',
                             numblocks={data.name: numblocks})
    # qr[0]
    name_q_st1 = prefix + 'Q_st1'
    dsk_q_st1 = {(name_q_st1, i, 0): (operator.getitem, (name_qr_st1, i, 0), 0)
                 for i in xrange(numblocks[0])}
    # qr[1]
    name_r_st1 = prefix + 'R_st1'
    dsk_r_st1 = {(name_r_st1, i, 0): (operator.getitem, (name_qr_st1, i, 0), 1)
                 for i in xrange(numblocks[0])}

    # Stacking for in-core QR computation
    def _vstack(*args):
        tup = tuple(args)
        return np.vstack(tup)

    to_stack = [_vstack] + [(name_r_st1, i, 0) for i in xrange(numblocks[0])]
    name_r_st1_stacked = prefix + 'R_st1_stacked'
    dsk_r_st1_stacked = {(name_r_st1_stacked, 0, 0): tuple(to_stack)}
    # In-core QR computation
    name_qr_st2 = prefix + 'QR_st2'
    dsk_qr_st2 = da.core.top(np.linalg.qr, name_qr_st2, 'ij',
                             name_r_st1_stacked, 'ij',
                             numblocks={name_r_st1_stacked: (1, 1)})
    # qr[0]
    name_q_st2_aux = prefix + 'Q_st2_aux'
    dsk_q_st2_aux = {(name_q_st2_aux, 0, 0): (operator.getitem,
                                              (name_qr_st2, 0, 0), 0)}
    name_q_st2 = prefix + 'Q_st2'
    dsk_q_st2 = dict(((name_q_st2,) + ijk,
                      (operator.getitem, (name_q_st2_aux, 0, 0),
                       tuple(slice(i * d, (i + 1) * d) for i, d in
                             zip(ijk, (n, n)))))
                     for ijk in product(*map(range, numblocks)))
    # qr[1]
    name_r_st2 = prefix + 'R'
    dsk_r_st2 = {(name_r_st2, 0, 0): (operator.getitem, (name_qr_st2, 0, 0), 1)}

    name_q_st3 = prefix + 'Q'
    dsk_q_st3 = da.core.top(np.dot, name_q_st3, 'ij', name_q_st1, 'ij',
                            name_q_st2, 'ij',
                            numblocks={name_q_st1: numblocks,
                                       name_q_st2: numblocks})

    dsk_q = {}
    dsk_q.update(data.dask)
    dsk_q.update(dsk_qr_st1)
    dsk_q.update(dsk_q_st1)
    dsk_q.update(dsk_r_st1)
    dsk_q.update(dsk_r_st1_stacked)
    dsk_q.update(dsk_qr_st2)
    dsk_q.update(dsk_q_st2_aux)
    dsk_q.update(dsk_q_st2)
    dsk_q.update(dsk_q_st3)
    dsk_r = {}
    dsk_r.update(data.dask)
    dsk_r.update(dsk_qr_st1)
    dsk_r.update(dsk_r_st1)
    dsk_r.update(dsk_r_st1_stacked)
    dsk_r.update(dsk_qr_st2)
    dsk_r.update(dsk_r_st2)

    q = da.Array(dsk_q, name_q_st3, shape=data.shape, blockshape=blockshape)
    r = da.Array(dsk_r, name_r_st2, shape=(n, n), blockshape=(n, n))

    return q, r
