# -*- coding: utf-8 -*-
#
# Definition of the respective ComputationalRoutines
# for the `freqanalysis` frontend. The *method*_cF function
# definitions serve as middleware connecting the pure
# backend methods to the ComputationalRoutines. The
# 1st argument always is the data, and the last argument
# `method_kwargs` gets passed as is to the underlying
# backend, so that the following calling signature
# is valid for all backend methods: method(data, **method_kwargs)
#
# To transparently trace the respective parameter names
# all ComputationalRoutines have two additional class constants:
#
# method_keys : list of names of the backend method parameters
# cF_keys : list of names of the parameters of the middleware computeFunctions
#
# the backend method name als gets explicitly attached as a class constant:
# method: backend method name

# Builtin/3rd party package imports
from curses import meta
from inspect import signature
import numpy as np
from scipy import signal
import h5py

# backend method imports
from .mtmfft import mtmfft
from .mtmconvol import mtmconvol
from .superlet import superlet
from .wavelet import wavelet
from .fooofspy import fooofspy


# Local imports
from syncopy.shared.errors import SPYValueError, SPYWarning
from syncopy.shared.tools import best_match
from syncopy.shared.computational_routine import ComputationalRoutine
from syncopy.shared.kwarg_decorators import process_io, encode_unique_md_label, decode_unique_md_label
from syncopy.shared.const_def import (
    spectralConversions,
    spectralDTypes
)


# -----------------------
# MultiTaper FFT
# -----------------------

@process_io
def mtmfft_cF(trl_dat, foi=None, timeAxis=0, keeptapers=True,
              polyremoval=None, output_fmt="pow",
              noCompute=False, chunkShape=None, method_kwargs=None):

    """
    Compute (multi-)tapered Fourier transform of multi-channel time series data

    Parameters
    ----------
    trl_dat : 2D :class:`numpy.ndarray`
        Uniformly sampled multi-channel time-series
    foi : 1D :class:`numpy.ndarray`
        Frequencies of interest  (Hz) for output. If desired frequencies
        cannot be matched exactly the closest possible frequencies (respecting
        data length and padding) are used.
    timeAxis : int
        Index of running time axis in `trl_dat` (0 or 1)
    keeptapers : bool
        If `True`, return spectral estimates for each taper.
        Otherwise power spectrum is averaged across tapers,
        only valid spectral estimate if `output_fmt` is `pow`.
    pad : str
        Padding mode; one of `'absolute'`, `'relative'`, `'maxlen'`, or `'nextpow2'`.
        See :func:`syncopy.padding` for more information.
    padtype : str
        Values to be used for padding. Can be 'zero', 'nan', 'mean',
        'localmean', 'edge' or 'mirror'. See :func:`syncopy.padding` for
        more information.
    padlength : None, bool or positive scalar
        Number of samples to pad to data (if `pad` is 'absolute' or 'relative').
        See :func:`syncopy.padding` for more information.
    polyremoval : int or None
        Order of polynomial used for de-trending data in the time domain prior
        to spectral analysis. A value of 0 corresponds to subtracting the mean
        ("de-meaning"), ``polyremoval = 1`` removes linear trends (subtracting the
        least squares fit of a linear polynomial).
        If `polyremoval` is `None`, no de-trending is performed.
    output_fmt : str
        Output of spectral estimation; one of :data:`~syncopy.specest.const_def.availableOutputs`
    noCompute : bool
        Preprocessing flag. If `True`, do not perform actual calculation but
        instead return expected shape and :class:`numpy.dtype` of output
        array.
    chunkShape : None or tuple
        If not `None`, represents shape of output `spec` (respecting provided
        values of `nTaper`, `keeptapers` etc.)
    method_kwargs : dict
        Keyword arguments passed to :func:`~syncopy.specest.mtmfft.mtmfft`
        controlling the spectral estimation method

    Returns
    -------
    spec : :class:`numpy.ndarray`
        Complex or real spectrum of (padded) input data.

    Notes
    -----
    This method is intended to be used as
    :meth:`~syncopy.shared.computational_routine.ComputationalRoutine.computeFunction`
    inside a :class:`~syncopy.shared.computational_routine.ComputationalRoutine`.
    Thus, input parameters are presumed to be forwarded from a parent metafunction.
    Consequently, this function does **not** perform any error checking and operates
    under the assumption that all inputs have been externally validated and cross-checked.

    The computational heavy lifting in this code is performed by NumPy's reference
    implementation of the Fast Fourier Transform :func:`numpy.fft.fft`.

    See also
    --------
    syncopy.freqanalysis : parent metafunction
    MultiTaperFFT : :class:`~syncopy.shared.computational_routine.ComputationalRoutine` instance
                     that calls this method as :meth:`~syncopy.shared.computational_routine.ComputationalRoutine.computeFunction`
    numpy.fft.rfft : NumPy's FFT implementation
    """

    # Re-arrange array if necessary and get dimensional information
    if timeAxis != 0:
        dat = trl_dat.T       # does not copy but creates view of `trl_dat`
    else:
        dat = trl_dat

    if method_kwargs['nSamples'] is None:
        nSamples = dat.shape[0]
    else:
        nSamples = method_kwargs['nSamples']

    nChannels = dat.shape[1]

    # Determine frequency band and shape of output
    # (time=1 x taper x freq x channel)
    freqs = np.fft.rfftfreq(nSamples, 1 / method_kwargs["samplerate"])
    _, freq_idx = best_match(freqs, foi, squash_duplicates=True)
    nFreq = freq_idx.size
    nTaper = method_kwargs["taper_opt"].get('Kmax', 1)
    outShape = (1, max(1, nTaper * keeptapers), nFreq, nChannels)

    # For initialization of computational routine,
    # just return output shape and dtype
    if noCompute:
        return outShape, spectralDTypes[output_fmt]

    # detrend, does not work with 'FauxTrial' data..
    if polyremoval == 0:
        dat = signal.detrend(dat, type='constant', axis=0, overwrite_data=True)
    elif polyremoval == 1:
        dat = signal.detrend(dat, type='linear', axis=0, overwrite_data=True)

    # call actual specest method
    res, _ = mtmfft(dat, **method_kwargs)

    # attach time-axis and convert to output_fmt
    spec = res[np.newaxis, :, freq_idx, :]
    spec = spectralConversions[output_fmt](spec)
    # Average across tapers if wanted
    # averaging is only valid spectral estimate
    # if output_fmt == 'pow'! (gets checked in parent meta)
    if not keeptapers:
        return spec.mean(axis=1, keepdims=True)
    return spec


class MultiTaperFFT(ComputationalRoutine):
    """
    Compute class that calculates (multi-)tapered Fourier transfrom of :class:`~syncopy.AnalogData` objects

    Sub-class of :class:`~syncopy.shared.computational_routine.ComputationalRoutine`,
    see :doc:`/developer/compute_kernels` for technical details on Syncopy's compute
    classes and metafunctions.

    See also
    --------
    syncopy.freqanalysis : parent metafunction
    """

    computeFunction = staticmethod(mtmfft_cF)

    # 1st argument,the data, gets omitted
    valid_kws = list(signature(mtmfft).parameters.keys())[1:]
    valid_kws += list(signature(mtmfft_cF).parameters.keys())[1:]
    # hardcode some parameter names which got digested from the frontend
    valid_kws += ['tapsmofrq', 'nTaper', 'pad', 'fooof_opt']

    def process_metadata(self, data, out):

        # Some index gymnastics to get trial begin/end "samples"
        if data.selection is not None:
            chanSec = data.selection.channel
            trl = data.selection.trialdefinition
            for row in range(trl.shape[0]):
                trl[row, :2] = [row, row + 1]
        else:
            chanSec = slice(None)
            time = np.arange(len(data.trials))
            time = time.reshape((time.size, 1))
            trl = np.hstack((time, time + 1,
                             np.zeros((len(data.trials), 1)),
                             np.array(data.trialinfo)))

        # Attach constructed trialdef-array (if even necessary)
        if self.keeptrials:
            out.trialdefinition = trl
        else:
            out.trialdefinition = np.array([[0, 1, 0]])

        # Attach remaining meta-data
        out.samplerate = data.samplerate
        out.channel = np.array(data.channel[chanSec])
        if self.cfg["method_kwargs"]["taper"] is None:
            out.taper = np.array(['None'])
        else:
            out.taper = np.array([self.cfg["method_kwargs"]["taper"]] * self.outputShape[out.dimord.index("taper")])
        out.freq = self.cfg["foi"]


# -----------------------
# MultiTaper Windowed FFT
# -----------------------


# Local workhorse that performs the computational heavy lifting
@process_io
def mtmconvol_cF(
        trl_dat,
        soi,
        postselect,
        equidistant=True,
        toi=None,
        foi=None,
        nTaper=1, tapsmofrq=None, timeAxis=0,
        keeptapers=True, polyremoval=0, output_fmt="pow",
        noCompute=False, chunkShape=None, method_kwargs=None):
    """
    Perform time-frequency analysis on multi-channel time series data using a sliding window FFT

    Parameters
    ----------
    trl_dat : 2D :class:`numpy.ndarray`
        Uniformly sampled multi-channel time-series
    soi : list of slices or slice
        Samples of interest; either a single slice encoding begin- to end-samples
        to perform analysis on (if sliding window centroids are equidistant)
        or list of slices with each slice corresponding to coverage of a single
        analysis window (if spacing between windows is not constant)
    samplerate : float
        Samplerate of `trl_dat` in Hz
    noverlap : int
        Number of samples covered by two adjacent analysis windows
    nperseg : int
        Size of analysis windows (in samples)
    equidistant : bool
        If `True`, spacing of window-centroids is equidistant.
    toi : 1D :class:`numpy.ndarray` or float or str
        Either time-points to center windows on if `toi` is a :class:`numpy.ndarray`,
        or percentage of overlap between windows if `toi` is a scalar or `"all"`
        to center windows on all samples in `trl_dat`. Please refer to
        :func:`~syncopy.freqanalysis` for further details. **Note**: The value
        of `toi` has to agree with provided padding and window settings. See
        Notes for more information.
    foi : 1D :class:`numpy.ndarray`
        Frequencies of interest  (Hz) for output. If desired frequencies
        cannot be matched exactly the closest possible frequencies (respecting
        data length and padding) are used.
    nTaper : int
        Number of tapers to use
    timeAxis : int
        Index of running time axis in `trl_dat` (0 or 1)
    taper : callable
        Taper function to use, one of :data:`~syncopy.specest.const_def.availableTapers`
    taper_opt : dict
        Additional keyword arguments passed to `taper` (see above). For further
        details, please refer to the
        `SciPy docs <https://docs.scipy.org/doc/scipy/reference/signal.windows.html>`_
    keeptapers : bool
        If `True`, results of Fourier transform are preserved for each taper,
        otherwise spectrum is averaged across tapers.
    polyremoval : int
        Order of polynomial used for de-trending data in the time domain prior
        to spectral analysis. A value of 0 corresponds to subtracting the mean
        ("de-meaning"), ``polyremoval = 1`` removes linear trends (subtracting the
        least squares fit of a linear polynomial). Detrending is done on each segment!
        If `polyremoval` is `None`, no de-trending is performed.
    output_fmt : str
        Output of spectral estimation; one of :data:`~syncopy.specest.const_def.availableOutputs`
    noCompute : bool
        Preprocessing flag. If `True`, do not perform actual calculation but
        instead return expected shape and :class:`numpy.dtype` of output
        array.
    chunkShape : None or tuple
        If not `None`, represents shape of output object `spec` (respecting provided
        values of `nTaper`, `keeptapers` etc.)
    method_kwargs : dict
        Keyword arguments passed to :func:`~syncopy.specest.mtmconvol.mtmconvol`
        controlling the spectral estimation method

    Returns
    -------
    spec : :class:`numpy.ndarray`
        Complex or real time-frequency representation of (padded) input data.

    Notes
    -----
    This method is intended to be used as
    :meth:`~syncopy.shared.computational_routine.ComputationalRoutine.computeFunction`
    inside a :class:`~syncopy.shared.computational_routine.ComputationalRoutine`.
    Thus, input parameters are presumed to be forwarded from a parent metafunction.
    Consequently, this function does **not** perform any error checking and operates
    under the assumption that all inputs have been externally validated and cross-checked.

    The computational heavy lifting in this code is performed by SciPy's Short Time
    Fourier Transform (STFT) implementation :func:`scipy.signal.stft`.

    See also
    --------
    syncopy.freqanalysis : parent metafunction
    MultiTaperFFTConvol : :class:`~syncopy.shared.computational_routine.ComputationalRoutine`
                          instance that calls this method as
                          :meth:`~syncopy.shared.computational_routine.ComputationalRoutine.computeFunction`
    scipy.signal.stft : SciPy's STFT implementation
    """

    # Re-arrange array if necessary and get dimensional information
    if timeAxis != 0:
        dat = trl_dat.T       # does not copy but creates view of `trl_dat`
    else:
        dat = trl_dat

    # Get shape of output for dry-run phase
    nChannels = dat.shape[1]
    if isinstance(toi, np.ndarray):     # `toi` is an array of time-points
        nTime = toi.size
        stftBdry = None
        stftPad = False
    else:                               # `toi` is either 'all' or a percentage
        nTime = np.ceil(dat.shape[0] / (method_kwargs['nperseg'] - method_kwargs['noverlap'])).astype(np.intp)
        stftBdry = "zeros"
        stftPad = True
    nFreq = foi.size
    taper_opt = method_kwargs['taper_opt']
    if taper_opt:
        nTaper = taper_opt.get("Kmax", 1)
    outShape = (nTime, max(1, nTaper * keeptapers), nFreq, nChannels)
    if noCompute:
        return outShape, spectralDTypes[output_fmt]

    # detrending options for each segment
    if polyremoval == 0:
        detrend = 'constant'
    elif polyremoval == 1:
        detrend = 'linear'
    else:
        detrend = False

    # additional keyword args for `stft` in dictionary
    method_kwargs.update({"boundary": stftBdry,
                          "padded": stftPad,
                          "detrend": detrend})

    if equidistant:
        ftr, freqs = mtmconvol(dat[soi, :], **method_kwargs)
        _, fIdx = best_match(freqs, foi, squash_duplicates=True)
        spec = ftr[postselect, :, fIdx, :]
        spec = spectralConversions[output_fmt](spec)

    else:
        # in this case only a single window gets centered on
        # every individual soi, so we can use mtmfft!
        samplerate = method_kwargs['samplerate']
        taper = method_kwargs['taper']

        # In case tapers aren't preserved allocate `spec` "too big"
        # and average afterwards
        spec = np.full((nTime, nTaper, nFreq, nChannels), np.nan, dtype=spectralDTypes[output_fmt])

        ftr, freqs = mtmfft(dat[soi[0], :], samplerate, taper=taper, taper_opt=taper_opt)
        _, fIdx = best_match(freqs, foi, squash_duplicates=True)
        spec[0, ...] = spectralConversions[output_fmt](ftr[:, fIdx, :])
        # loop over remaining soi to center windows on
        for tk in range(1, len(soi)):
            ftr, freqs = mtmfft(dat[soi[tk], :], samplerate, taper=taper, taper_opt=taper_opt)
            spec[tk, ...] = spectralConversions[output_fmt](ftr[:, fIdx, :])

    # Average across tapers if wanted
    # only valid if output_fmt='pow' !
    if not keeptapers:
        return np.nanmean(spec, axis=1, keepdims=True)
    return spec


class MultiTaperFFTConvol(ComputationalRoutine):
    """
    Compute class that performs time-frequency analysis of :class:`~syncopy.AnalogData` objects

    Sub-class of :class:`~syncopy.shared.computational_routine.ComputationalRoutine`,
    see :doc:`/developer/compute_kernels` for technical details on Syncopy's compute
    classes and metafunctions.

    See also
    --------
    syncopy.freqanalysis : parent metafunction
    """

    computeFunction = staticmethod(mtmconvol_cF)

    # 1st argument,the data, gets omitted
    valid_kws = list(signature(mtmconvol).parameters.keys())[1:]
    valid_kws += list(signature(mtmconvol_cF).parameters.keys())[1:]
    # hardcode some parameter names which got digested from the frontend
    valid_kws += ['tapsmofrq', 't_ftimwin', 'nTaper']

    def process_metadata(self, data, out):

        # Get trialdef array + channels from source
        if data.selection is not None:
            chanSec = data.selection.channel
            trl = data.selection.trialdefinition
        else:
            chanSec = slice(None)
            trl = data.trialdefinition

        # Construct trialdef array and compute new sampling rate
        trl, srate = _make_trialdef(self.cfg, trl, data.samplerate)

        # If trial-averaging was requested, use the first trial as reference
        # (all trials had to have identical lengths), and average onset timings
        if not self.keeptrials:
            t0 = trl[:, 2].mean()
            trl = trl[[0], :]
            trl[:, 2] = t0

        # Attach meta-data
        out.trialdefinition = trl
        out.samplerate = srate
        out.channel = np.array(data.channel[chanSec])
        if self.cfg["method_kwargs"]["taper"] is None:
            out.taper = np.array(['None'])
        else:
            out.taper = np.array([self.cfg["method_kwargs"]["taper"]] * self.outputShape[out.dimord.index("taper")])
        out.freq = self.cfg["foi"]


# -----------------
# WaveletTransform
# -----------------


@process_io
def wavelet_cF(
    trl_dat,
    preselect,
    postselect,
    toi=None,
    timeAxis=0,
    polyremoval=0,
    output_fmt="pow",
    noCompute=False,
    chunkShape=None,
    method_kwargs=None,
):
    """
    This is the middleware for the :func:`~syncopy.specest.wavelet.wavelet`
    spectral estimation method.

    Parameters
    ----------
    trl_dat : 2D :class:`numpy.ndarray`
        Uniformly sampled multi-channel time-series
    preselect : slice
        Begin- to end-samples to perform analysis on (trim data to interval).
        See Notes for details.
    postselect : list of slices or list of 1D NumPy arrays
        Actual time-points of interest within interval defined by `preselect`
        See Notes for details.
    toi : 1D :class:`numpy.ndarray` or str
        Either time-points to center wavelets on if `toi` is a :class:`numpy.ndarray`,
        or `"all"` to center wavelets on all samples in `trl_dat`. Please refer to
        :func:`~syncopy.freqanalysis` for further details.
    timeAxis : int
        Index of running time axis in `trl_dat` (0 or 1)
    polyremoval : int
        Order of polynomial used for de-trending data in the time domain prior
        to spectral analysis. A value of 0 corresponds to subtracting the mean
        ("de-meaning"), ``polyremoval = 1`` removes linear trends (subtracting the
        least squares fit of a linear polynomial).
        If `polyremoval` is `None`, no de-trending is performed.
    output_fmt : str
        Output of spectral estimation; one of :data:`~syncopy.specest.const_def.availableOutputs`
    noCompute : bool
        Preprocessing flag. If `True`, do not perform actual calculation but
        instead return expected shape and :class:`numpy.dtype` of output
        array.
    chunkShape : None or tuple
        If not `None`, represents shape of output object `spec` (respecting provided
        values of `scales`, `preselect`, `postselect` etc.)
    method_kwargs : dict
        Keyword arguments passed to :func:`~syncopy.specest.wavelet.wavelet`
        controlling the spectral estimation method

    Returns
    -------
    spec : :class:`numpy.ndarray`
        Complex or real time-frequency representation of (padded) input data.
        Shape is (nTime, 1, len(scales), nChannels), so that the
        individual spectra per channel can be assessed via
        `spec[:, 1, :, channel]`.

    Notes
    -----
    This method is intended to be used as
    :meth:`~syncopy.shared.computational_routine.ComputationalRoutine.computeFunction`
    inside a :class:`~syncopy.shared.computational_routine.ComputationalRoutine`.
    Thus, input parameters are presumed to be forwarded from a parent metafunction.
    Consequently, this function does **not** perform any error checking and operates
    under the assumption that all inputs have been externally validated and cross-checked.

    For wavelets, data concatenation is performed by first trimming `trl_dat` to
    an interval of interest (via `preselect`), then performing the actual wavelet
    transform, and subsequently extracting the actually wanted time-points
    (via `postselect`).

    See also
    --------
    syncopy.freqanalysis : parent metafunction
    WaveletTransform : :class:`~syncopy.shared.computational_routine.ComputationalRoutine`
                       instance that calls this method as
                       :meth:`~syncopy.shared.computational_routine.ComputationalRoutine.computeFunction`
    """

    # Re-arrange array if necessary and get dimensional information
    if timeAxis != 0:
        dat = trl_dat.T  # does not copy but creates view of `trl_dat`
    else:
        dat = trl_dat

    # Get shape of output for dry-run phase
    nChannels = dat.shape[1]
    if isinstance(toi, np.ndarray):  # `toi` is an array of time-points
        nTime = toi.size
    else:  # `toi` is 'all'
        nTime = dat.shape[0]
    nScales = method_kwargs["scales"].size
    outShape = (nTime, 1, nScales, nChannels)
    if noCompute:
        return outShape, spectralDTypes[output_fmt]

    # detrend, does not work with 'FauxTrial' data..
    if polyremoval == 0:
        dat = signal.detrend(dat, type='constant', axis=0, overwrite_data=True)
    elif polyremoval == 1:
        dat = signal.detrend(dat, type='linear', axis=0, overwrite_data=True)

    # ------------------
    # actual method call
    # ------------------
    # Compute wavelet transform with given data/time-selection
    spec = wavelet(dat[preselect, :], **method_kwargs)
    # the cwt stacks the scales on the 1st axis, move to 2nd
    spec = spec.transpose(1, 0, 2)[postselect, :, :]

    return spectralConversions[output_fmt](spec[:, np.newaxis, :, :])


class WaveletTransform(ComputationalRoutine):
    """
    Compute class that performs time-frequency analysis of :class:`~syncopy.AnalogData` objects

    Sub-class of :class:`~syncopy.shared.computational_routine.ComputationalRoutine`,
    see :doc:`/developer/compute_kernels` for technical details on Syncopy's compute
    classes and metafunctions.

    See also
    --------
    syncopy.freqanalysis : parent metafunction
    """

    computeFunction = staticmethod(wavelet_cF)

    # 1st argument,the data, gets omitted
    valid_kws = list(signature(wavelet).parameters.keys())[1:]
    # here also last argument, the method_kwargs, are omitted
    valid_kws += list(signature(wavelet_cF).parameters.keys())[1:-1]
    valid_kws += ["width"]

    def process_metadata(self, data, out):

        # Get trialdef array + channels from source
        if data.selection is not None:
            chanSec = data.selection.channel
            trl = data.selection.trialdefinition
        else:
            chanSec = slice(None)
            trl = data.trialdefinition

        # Construct trialdef array and compute new sampling rate
        trl, srate = _make_trialdef(self.cfg, trl, data.samplerate)

        # If trial-averaging was requested, use the first trial as reference
        # (all trials had to have identical lengths), and average onset timings
        if not self.keeptrials:
            t0 = trl[:, 2].mean()
            trl = trl[[0], :]
            trl[:, 2] = t0

        # Attach meta-data
        out.trialdefinition = trl
        out.samplerate = srate
        out.channel = np.array(data.channel[chanSec])
        out.freq = 1 / self.cfg["method_kwargs"]["wavelet"].fourier_period(
            self.cfg["method_kwargs"]["scales"]
        )


# -----------------
# SuperletTransform
# -----------------


@process_io
def superlet_cF(
    trl_dat,
    preselect,
    postselect,
    toi=None,
    timeAxis=0,
    polyremoval=0,
    output_fmt="pow",
    noCompute=False,
    chunkShape=None,
    method_kwargs=None,
):

    """
    This is the middleware for the :func:`~syncopy.specest.superlet.superlet`
    spectral estimation method.

    Parameters
    ----------
    trl_dat : 2D :class:`numpy.ndarray`
        Uniformly sampled multi-channel time-series
    preselect : slice
        Begin- to end-samples to perform analysis on (trim data to interval).
        See Notes for details.
    postselect : list of slices or list of 1D NumPy arrays
        Actual time-points of interest within interval defined by `preselect`
        See Notes for details.
    toi : 1D :class:`numpy.ndarray` or str
        Either array of equidistant time-points
        or `"all"` to perform analysis on all samples in `trl_dat`. Please refer to
        :func:`~syncopy.freqanalysis` for further details.
    timeAxis : int
        Index of running time axis in `trl_dat` (0 or 1)
    polyremoval : int or None
        Order of polynomial used for de-trending data in the time domain prior
        to spectral analysis. A value of 0 corresponds to subtracting the mean
        ("de-meaning"), ``polyremoval = 1`` removes linear trends (subtracting the
        least squares fit of a linear polynomial).
        If `polyremoval` is `None`, no de-trending is performed.
    output_fmt : str
        Output of spectral estimation; one of
        :data:`~syncopy.specest.const_def.availableOutputs`
    noCompute : bool
        Preprocessing flag. If `True`, do not perform actual calculation but
        instead return expected shape and :class:`numpy.dtype` of output
        array.
    chunkShape : None or tuple
        If not `None`, represents shape of output object `gmean_spec`
        (respecting provided values of `scales`, `preselect`, `postselect` etc.)
    method_kwargs : dict
        Keyword arguments passed to :func:`~syncopy.specest.superlet.superlet
        controlling the spectral estimation method

    Returns
    -------
    gmean_spec : :class:`numpy.ndarray`
        Complex time-frequency representation of the input data.
        Shape is ``(nTime, 1, nScales, nChannels)``.

    Notes
    -----
    This method is intended to be used as
    :meth:`~syncopy.shared.computational_routine.ComputationalRoutine.computeFunction`
    inside a :class:`~syncopy.shared.computational_routine.ComputationalRoutine`.
    Thus, input parameters are presumed to be forwarded from a parent metafunction.
    Consequently, this function does **not** perform any error checking and operates
    under the assumption that all inputs have been externally validated and cross-checked.

    See also
    --------
    syncopy.freqanalysis : parent metafunction
    SuperletTransform : :class:`~syncopy.shared.computational_routine.ComputationalRoutine`
                        instance that calls this method as
                        :meth:`~syncopy.shared.computational_routine.ComputationalRoutine.computeFunction`

    """

    # Re-arrange array if necessary and get dimensional information
    if timeAxis != 0:
        dat = trl_dat.T  # does not copy but creates view of `trl_dat`
    else:
        dat = trl_dat

    # Get shape of output for dry-run phase
    nChannels = dat.shape[1]
    if isinstance(toi, np.ndarray):  # `toi` is an array of time-points
        nTime = toi.size
    else:  # `toi` is 'all'
        nTime = dat.shape[0]
    nScales = method_kwargs["scales"].size
    outShape = (nTime, 1, nScales, nChannels)
    if noCompute:
        return outShape, spectralDTypes[output_fmt]

    # detrend, does not work with 'FauxTrial' data..
    if polyremoval == 0:
        dat = signal.detrend(dat, type='constant', axis=0, overwrite_data=True)
    elif polyremoval == 1:
        dat = signal.detrend(dat, type='linear', axis=0, overwrite_data=True)

    # ------------------
    # actual method call
    # ------------------
    gmean_spec = superlet(dat[preselect, :], **method_kwargs)
    # the cwtSL stacks the scales on the 1st axis
    gmean_spec = gmean_spec.transpose(1, 0, 2)[postselect, :, :]

    return spectralConversions[output_fmt](gmean_spec[:, np.newaxis, :, :])


class SuperletTransform(ComputationalRoutine):
    """
    Compute class that performs time-frequency analysis of :class:`~syncopy.AnalogData` objects

    Sub-class of :class:`~syncopy.shared.computational_routine.ComputationalRoutine`,
    see :doc:`/developer/compute_kernels` for technical details on Syncopy's compute
    classes and metafunctions.

    See also
    --------
    syncopy.freqanalysis : parent metafunction
    """

    computeFunction = staticmethod(superlet_cF)

    # 1st argument,the data, gets omitted
    valid_kws = list(signature(superlet).parameters.keys())[1:]
    valid_kws += list(signature(superlet_cF).parameters.keys())[1:-1]

    def process_metadata(self, data, out):

        # Get trialdef array + channels from source
        if data.selection is not None:
            chanSec = data.selection.channel
            trl = data.selection.trialdefinition
        else:
            chanSec = slice(None)
            trl = data.trialdefinition

        # Construct trialdef array and compute new sampling rate
        trl, srate = _make_trialdef(self.cfg, trl, data.samplerate)

        # If trial-averaging was requested, use the first trial as reference
        # (all trials had to have identical lengths), and average onset timings
        if not self.keeptrials:
            t0 = trl[:, 2].mean()
            trl = trl[[0], :]
            trl[:, 2] = t0

        # Attach meta-data
        out.trialdefinition = trl
        out.samplerate = srate
        out.channel = np.array(data.channel[chanSec])
        # for the SL Morlets the conversion is straightforward
        out.freq = 1 / (2 * np.pi * self.cfg["method_kwargs"]["scales"])


def _make_trialdef(cfg, trialdefinition, samplerate):
    """
    Local helper to construct trialdefinition arrays for time-frequency
    :class:`~syncopy.SpectralData` objects

    Parameters
    ----------
    cfg : dict
        Config dictionary attribute of `ComputationalRoutine` subclass
    trialdefinition : 2D :class:`numpy.ndarray`
        Provisional trialdefnition array either directly copied from the
        :class:`~syncopy.AnalogData` input object or computed by the
        :class:`~syncopy.datatype.base_data.Selector` class.
    samplerate : float
        Original sampling rate of :class:`~syncopy.AnalogData` input object

    Returns
    -------
    trialdefinition : 2D :class:`numpy.ndarray`
        Updated trialdefinition array reflecting provided `toi`/`toilim` selection
    samplerate : float
        Sampling rate accouting for potentially new spacing b/w time-points (accouting
        for provided `toi`/`toilim` selection)

    Notes
    -----
    This routine is a local auxiliary method that is purely intended for internal
    use. Thus, no error checking is performed.

    See also
    --------
    syncopy.specest.mtmconvol.mtmconvol : :meth:`~syncopy.shared.computational_routine.ComputationalRoutine.computeFunction`
                                          performing time-frequency analysis using (multi-)tapered sliding window Fourier transform
    syncopy.specest.wavelet.wavelet : :meth:`~syncopy.shared.computational_routine.ComputationalRoutine.computeFunction`
                                      performing time-frequency analysis using non-orthogonal continuous wavelet transform
    syncopy.specest.superlet.superlet : :meth:`~syncopy.shared.computational_routine.ComputationalRoutine.computeFunction`
                                      performing time-frequency analysis using super-resolution superlet transform

    """

    # If `toi` is array, use it to construct timing info
    toi = cfg["toi"]
    if isinstance(toi, np.ndarray):

        # Some index gymnastics to get trial begin/end samples
        nToi = toi.size
        time = np.cumsum([nToi] * trialdefinition.shape[0])
        trialdefinition[:, 0] = time - nToi
        trialdefinition[:, 1] = time

        # Important: differentiate b/w equidistant time ranges and disjoint points
        tSteps = np.diff(toi)
        if np.allclose(tSteps, [tSteps[0]] * tSteps.size):
            samplerate = 1 / (toi[1] - toi[0])
        else:
            msg = (
                "`SpectralData`'s `time` property currently does not support "
                + "unevenly spaced `toi` selections!"
            )
            SPYWarning(msg, caller="freqanalysis")
            samplerate = 1.0
            trialdefinition[:, 2] = 0

        # Reconstruct trigger-onset based on provided time-point array
        trialdefinition[:, 2] = toi[0] * samplerate

    # If `toi` was a percentage, some cumsum/winSize algebra is required
    # Note: if `toi` was "all", simply use provided `trialdefinition` and `samplerate`
    elif np.issubdtype(type(toi), np.number):
        mKw = cfg['method_kwargs']
        winSize = mKw["nperseg"] - mKw["noverlap"]
        trialdefinitionLens = np.ceil(np.diff(trialdefinition[:, :2]) / winSize)
        sumLens = np.cumsum(trialdefinitionLens).reshape(trialdefinitionLens.shape)
        trialdefinition[:, 0] = np.ravel(sumLens - trialdefinitionLens)
        trialdefinition[:, 1] = sumLens.ravel()
        trialdefinition[:, 2] = trialdefinition[:, 2] / winSize
        samplerate = np.round(samplerate / winSize, 2)

    # If `toi` was "all", do **not** simply use provided `trialdefinition`: overlapping
    # trials require thie below `cumsum` gymnastics
    else:
        bounds = np.cumsum(np.diff(trialdefinition[:, :2]))
        trialdefinition[1:, 0] = bounds[:-1]
        trialdefinition[:, 1] = bounds

    return trialdefinition, samplerate


# -----------------------
# FOOOF
# -----------------------

@process_io
def fooofspy_cF(trl_dat, foi=None, timeAxis=0,
                output_fmt='fooof', fooof_settings=None, noCompute=False, chunkShape=None, method_kwargs=None):
    """
    Run FOOOF

    Parameters
    ----------
    trl_dat : 2D :class:`numpy.ndarray`
        Uniformly sampled multi-channel time-series
    foi : 1D :class:`numpy.ndarray`
        Frequencies of interest  (Hz) for output. If desired frequencies
        cannot be matched exactly the closest possible frequencies (respecting
        data length and padding) are used.
    timeAxis : int
        Index of running time axis in `trl_dat` (0 or 1)
    output_fmt : str
        Output of FOOOF; one of :data:`~syncopy.specest.const_def.availableFOOOFOutputs`
    fooof_settings: dict or None
        Can contain keys `'in_freqs'` (the frequency axis for the data) and `'freq_range'` (post-processing range for fooofed spectrum).
    noCompute : bool
        Preprocessing flag. If `True`, do not perform actual calculation but
        instead return expected shape and :class:`numpy.dtype` of output
        array.
    chunkShape : None or tuple
        If not `None`, represents shape of output `spec` (respecting provided
        values of `nTaper`, `keeptapers` etc.)
    method_kwargs : dict
        Keyword arguments passed to :func:`~syncopy.specest.fooofspy.fooofspy`
        controlling the spectral estimation method

    Returns
    -------
    spec : :class:`numpy.ndarray`
        Complex or real spectrum of (padded) input data.

    Notes
    -----
    This method is intended to be used as
    :meth:`~syncopy.shared.computational_routine.ComputationalRoutine.computeFunction`
    inside a :class:`~syncopy.shared.computational_routine.ComputationalRoutine`.
    Thus, input parameters are presumed to be forwarded from a parent metafunction.
    Consequently, this function does **not** perform any error checking and operates
    under the assumption that all inputs have been externally validated and cross-checked.

    See also
    --------
    syncopy.freqanalysis : parent metafunction
    """
    if timeAxis != 0:
        raise SPYValueError("timeaxis of input spectral data to be 0. Non-standard axes not supported with FOOOF.", actual=timeAxis)

    outShape = trl_dat.shape
    # For initialization of computational routine,
    # just return output shape and dtype
    if noCompute:
        return outShape, spectralDTypes['pow']

    # Call actual fooof method
    res, details = fooofspy(trl_dat[0, 0, :, :], in_freqs=fooof_settings['in_freqs'], freq_range=fooof_settings['freq_range'], out_type=output_fmt,
                      fooof_opt=method_kwargs)

    if 'settings_used' in details:
        del details['settings_used']  # We like to keep this in the return value of the
    # backend functions for now (the vast majority of unit tests rely on it), but
    # nested dicts are not allowed in the additional return value of cFs, so we remove
    # it before passing the return value on.

    details = pack_singletrial_metadata_fooof_into_hdf5(details)

    res = res[np.newaxis, np.newaxis, :, :]  # Re-add omitted axes.
    return res, details


def extract_md_group(md):
    """
    Extract metadata from h5py 'metadata' group and return a standard dict
    containing 2 nested dicts 'dsets' and 'attrs'.

    Parameters
    ----------
    md: a h5py group, that contains metadata attributes as 'attrs' and/or
        extra datasets in the group.

    Returns
    -------
    dict, containing two more dictionaries at keys `'dsets'` and `'attrs'`. Both
          dicts are of type `(str, np.ndarray)`.
    """
    metadata = dict()
    metadata['dsets'] = dict()
    metadata['attrs'] = dict()
    for k, v in md.attrs.items():
        metadata['attrs'][k] = v.copy() # copy the numpy array
    print("extract_md_group(): extracted {na} attribs.".format(na=len(md.attrs.keys())))
    for k, v in md.items():
        metadata['dsets'][k] = v.copy() # copy the numpy array
    print("extract_md_group(): extracted {nd} datasets.".format(nd=len(md.keys())))
    return metadata

def _merge_md_list(md_list):
    """
    Merge a list of dictionaries as returned by `extract_md_group()` into a single dictionary.

    For this to make any sense, the dicts in the `md_list` sub dicts must have unique keys. If that
    is not the case, later dicts will overwrite values of previous ones. This is not checked.

    Parameters
    ----------
    md_list: a list of dictionaries. Each entry dict has to contain two more dictionaries at keys `'dsets'` and `'attrs'`.
             Both sub dicts are of type `(str, np.ndarray)`.

    Returns
    -------
    dict, containing two more dictionaries at keys `'dsets'` and `'attrs'`. Both
          sub dicts are of type `(str, np.ndarray)`.
    """
    if not md_list:
        return None
    metadata = dict()
    metadata['dsets'] = dict()
    metadata['attrs'] = dict()
    for md in md_list:
        # We just join all of them into a single dict, the unique keys should allow this.
        metadata['attrs'] = {**metadata['attrs'], **md['attrs']}
        #print("merge_md_list(): added {na} attrs.".format(na=len(md['attrs'])))
        metadata['dsets'] = {**metadata['dsets'], **md['dsets']}
        #print("merge_md_list(): added {nd} dsets.".format(nd=len(md['dsets'])))
    print("merge_md_list(): final metadata contains {na} attribs and {nd} dsets.".format(na=len(metadata['attrs']), nd=len(metadata['dsets'])))
    return metadata

def metadata_from_h5py_file(h5py_filename):
    """
    Extract metadata from h5py file.

    This extracts metadata as a standard dictionary from the 'metadata' group of a (virtual or standard)
    hdf5 file. Note that it converts the attributes from the hdf5 attribute manager into a standard dictionary
    (that is independent of whether the hdf5 file is still open).

    Parameters
    ----------
    h5py_filename str
        path to hdf5 file. The file will be opened for reading, and closed in the end.
        The file must contain a standard or virtual dataset named 'data'.
        If it does not contain 'metadata' group, the returned value will be `None`.

    Returns
    -------
    metadata None or dict
        If a dict, that dict contains two more dictionaries at keys `'dsets'` and `'attrs'`. Both
        sub dicts are of type `(str, np.ndarray)`.
    """
    metadata = None
    with h5py.File(h5py_filename, mode="r") as h5f:
        if 'data' in h5f:
            main_dset = h5f['data']
            if main_dset.is_virtual:
                metadata_list = list()  # A list of dicts.
                #print(main_dset.virtual_sources())
                print("process_metadata()/metadata_from_h5py_file(): [V] virtual main dataset has {na} attributes".format(na=len(main_dset.attrs.keys())))

                # Now open the virtual sources and check there for the metadata.
                for source_tpl in main_dset.virtual_sources():
                    with h5py.File(source_tpl.file_name, mode="r") as h5f_virtual_part:
                        if 'data' in h5f_virtual_part:
                            print("process_metadata()/metadata_from_h5py_file(): [V] virtual part file '{vds}' contains virtual 'data' dataset.".format(vds=source_tpl.file_name))
                            virtual_main_dset_part = h5f_virtual_part['data']
                            print("process_metadata()/metadata_from_h5py_file(): [V] the virtual main 'data' dataset contains {na} attribs.".format(na=len(virtual_main_dset_part.attrs.keys())))
                        if 'metadata' in h5f_virtual_part:
                            print("process_metadata()/metadata_from_h5py_file(): [V] virtual dataset 'data' from file '{vds}' contains 'metadata' group.".format(vds=source_tpl.file_name))
                            virtual_metadata_grp = h5f_virtual_part['metadata']
                            metadata_list.append(extract_md_group(virtual_metadata_grp))
                            print("process_metadata()/metadata_from_h5py_file(): [V] the 'metadata' group contains {na} attribs.".format(na=len(virtual_metadata_grp.attrs.keys())))
                            ## These lines were added only to test whether the added dict arrives here,
                            ## they will be deleted without replacement.
                            #vds_name = "md_dataset_0"
                            #if vds_name in virtual_metadata_grp:
                            #    virtual_state = "virtual" if virtual_metadata_grp[vds_name].is_virtual else "non-virtual"
                            #    print("process_metadata()/metadata_from_h5py_file(): [V] the 'metadata' group contains the {vs} 'md_dataset_0' dataset.".format(vs=virtual_state))
                metadata = _merge_md_list(metadata_list)
            else:
                # the main_dset is not virtual, so just grab the metadata group from the file root.
                if 'metadata' in h5f:
                    print("process_metadata()/metadata_from_h5py_file(): [NV] extracting 'metadata' group from non-virtual dataset.")
                    metadata = extract_md_group(h5f['metadata'])
                    print("process_metadata()/metadata_from_h5py_file(): [NV] the extracted 'metadata' group contains {na} attribs.".format(na=len(metadata['attrs'].keys())))
                else:
                    metadata = None
        else:
            raise SPYValueError("'data' dataset in hd5f file {of}.".format(of=h5py_filename), actual="no such dataset")
    return metadata


def pack_singletrial_metadata_fooof_into_hdf5(metadata_fooof_backend):
    # Reformat the gaussian and peak params for inclusion in the 2nd return value.
    # For several channels, the number of peaks may differ, and thus we cannot simply
    # call something like `np.array(gaussian_params)` in that case, as that will create
    # an array of type 'object', which is not supported by hdf5. We could use one return
    # value (entry in the 'details' dict below) per channel to solve that, but in this
    # case, we decided to vstack the arrays instead. When extracting the data again
    # (in process_metadata()), we need to revert this. That is possible because we can
    # see from the `n_peaks` return value how many (and thus which) rows belong to
    # which channel.
    metadata_fooof_backend['gaussian_params'] = np.vstack(metadata_fooof_backend['gaussian_params'])
    metadata_fooof_backend['peak_params'] = np.vstack(metadata_fooof_backend['peak_params'])
    return metadata_fooof_backend


def unpack_alltrials_metadata_fooof_from_hdf5(metadata_fooof_hdf5):
    """This reverts and special packaging applied to the backend
    function return values to fit them into the hdf5 container.

    In the case of FOOOF, we had to vstack the gaussian_params
    and peak_params, and we now revert this.

    Of course, you do not have to undo things if you are fine
    with passing them to the frontend the way they are stored in the hdf5.

    Keep in mind that this is not directly the inverse of the
    function called in the cF, because:
     - that function prepares data from a single backend function call,
       while this function has to unpack the data from all cF function calls.
     - the input metadata is a standard dict that has already
       been pre-processed, including the split into 'attrs' and 'dsets'.
    """
    print(f"unpack_metadata_fooof_from_hdf5(): {metadata_fooof_hdf5}")
    for unique_attr_label, v in metadata_fooof_hdf5['attrs'].items():
        label, trial_idx, call_idx = decode_unique_md_label(unique_attr_label)
        if label == "n_peaks":
            n_peaks = v
            gaussian_params_out = list()
            peak_params_out = list()
            start_idx = 0
            unique_attr_label_gaussian_params = encode_unique_md_label('gaussian_params', trial_idx, call_idx)
            unique_attr_label_peak_params = encode_unique_md_label('peak_params', trial_idx, call_idx)
            gaussian_params_in = metadata_fooof_hdf5['attrs'][unique_attr_label_gaussian_params]
            peak_params_in = metadata_fooof_hdf5['attrs'][unique_attr_label_peak_params]
            for trial_idx in range(len(n_peaks)):
                end_idx = start_idx + n_peaks[trial_idx]
                gaussian_params_out.append(gaussian_params_in[start_idx:end_idx, :])
                peak_params_out.append(peak_params_in[start_idx:end_idx, :])

            metadata_fooof_hdf5['attrs'][unique_attr_label_gaussian_params] = gaussian_params_out
            metadata_fooof_hdf5['attrs'][unique_attr_label_gaussian_params] = peak_params_out
    return metadata_fooof_hdf5


class FooofSpy(ComputationalRoutine):
    """
    Compute class that checks parameters and adds metadata to output spectral data.

    Sub-class of :class:`~syncopy.shared.computational_routine.ComputationalRoutine`,
    see :doc:`/developer/compute_kernels` for technical details on Syncopy's compute
    classes and metafunctions.

    See also
    --------
    syncopy.freqanalysis : parent metafunction
    """

    computeFunction = staticmethod(fooofspy_cF)

    # 1st argument,the data, gets omitted
    valid_kws = list(signature(fooofspy).parameters.keys())[1:]
    valid_kws += list(signature(fooofspy_cF).parameters.keys())[1:]
    # hardcode some parameter names which got digested from the frontend
    valid_kws += ["fooof_settings"]

    # To attach metadata to the output of the CF
    def process_metadata(self, data, out):

        out.metadata = metadata_from_h5py_file(out.filename)          # general
        out.metadata = unpack_alltrials_metadata_fooof_from_hdf5(out.metadata)  # backend-specific. may or may not be needed, depending on what you need to do in the cF to fit the return values into hdf5.

        #if out.metadata is not None:
        #    print("FooofSpy.process_metadata(): ************** received some (non-None) metadata ******************")
        #    print("FooofSpy.process_metadata(): metadata group consists of {ne} dsets and {na} attribs".format(ne=len(out.metadata['dsets'].keys()), na=len(out.metadata['attrs'].keys())))
        #else:
        #    print("FooofSpy.process_metadata(): received metadata is None")

        # Some index gymnastics to get trial begin/end "samples"
        if data.selection is not None:
            chanSec = data.selection.channel
        else:
            chanSec = slice(None)

        # Attach remaining meta-data
        out.samplerate = data.samplerate
        out.channel = np.array(data.channel[chanSec])
        out.freq = data.freq
        out._trialdefinition = data._trialdefinition
