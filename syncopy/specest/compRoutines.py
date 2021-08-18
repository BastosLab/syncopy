# -*- coding: utf-8 -*-
#
# Definition of the respective ComputationalRoutines
# for the `freqanalysis` frontend. The *_cF function
# definitions serve as middleware connecting the pure
# backend methods to the ComputationalRoutines. The
# 1st argument always is the data, and the last argument
# `method_kwargs` gets passed as is to the underlying
# backend, so that the following calling signature
# is valid for all backend methods: backend_method(data, **method_kwargs)
#


# Builtin/3rd party package imports
import numpy as np
from numbers import Number
from scipy import signal

# backend imports
from .superlet import superlet
from .wavelet import wavelet

# Local imports
from syncopy.shared.errors import SPYWarning
from syncopy.datatype import padding
from syncopy.shared.tools import best_match
from syncopy.shared.computational_routine import ComputationalRoutine
from syncopy.shared.kwarg_decorators import unwrap_io
from syncopy.specest.const_def import (
    spectralConversions,
    spectralDTypes,
)


# -----------------------
# MultiTaper/Windowed FFT
# -----------------------


# Local workhorse that performs the computational heavy lifting
@unwrap_io
def mtmconvol_cF(
        trl_dat, soi, padbegin, padend,
        samplerate=None, noverlap=None, nperseg=None, equidistant=True, toi=None, foi=None,
        nTaper=1, timeAxis=0, taper=None, taperopt={}, 
        keeptapers=True, polyremoval=None, output_fmt="pow",
        noCompute=False, chunkShape=None):
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
    padbegin : int
        Number of samples to pre-pend to `trl_dat`
    padend : int
        Number of samples to append to `trl_dat`
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
        Taper function to use, one of :data:`~syncopy.specest.freqanalysis.availableTapers`
    taperopt : dict
        Additional keyword arguments passed to `taper` (see above). For further 
        details, please refer to the 
        `SciPy docs <https://docs.scipy.org/doc/scipy/reference/signal.windows.html>`_
    keeptapers : bool
        If `True`, results of Fourier transform are preserved for each taper, 
        otherwise spectrum is averaged across tapers. 
    polyremoval : int
        **FIXME: Not implemented yet**
        Order of polynomial used for de-trending. A value of 0 corresponds to 
        subtracting the mean ("de-meaning"), ``polyremoval = 1`` removes linear 
        trends (subtracting the least squares fit of a linear function), 
        ``polyremoval = N`` for `N > 1` subtracts a polynomial of order `N` (``N = 2`` 
        quadratic, ``N = 3`` cubic etc.). If `polyremoval` is `None`, no de-trending
        is performed. 
    output_fmt : str
        Output of spectral estimation; one of :data:`~syncopy.specest.freqanalysis.availableOutputs`
    noCompute : bool
        Preprocessing flag. If `True`, do not perform actual calculation but
        instead return expected shape and :class:`numpy.dtype` of output
        array.
    chunkShape : None or tuple
        If not `None`, represents shape of output object `spec` (respecting provided 
        values of `nTaper`, `keeptapers` etc.)
    
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
        
    # Pad input array if necessary
    if padbegin > 0 or padend > 0:
        dat = padding(dat, "zero", pad="relative", padlength=None, 
                      prepadlength=padbegin, postpadlength=padend)

    # Get shape of output for dry-run phase
    nChannels = dat.shape[1]
    if isinstance(toi, np.ndarray):     # `toi` is an array of time-points
        nTime = toi.size
        stftBdry = None
        stftPad = False
    else:                               # `toi` is either 'all' or a percentage
        nTime = np.ceil(dat.shape[0] / (nperseg - noverlap)).astype(np.intp)
        stftBdry = "zeros"
        stftPad = True
    nFreq = foi.size
    outShape = (nTime, max(1, nTaper * keeptapers), nFreq, nChannels)
    if noCompute:
        return outShape, spectralDTypes[output_fmt]
    
    # In case tapers aren't preserved allocate `spec` "too big" and average afterwards
    spec = np.full((nTime, nTaper, nFreq, nChannels), np.nan, dtype=spectralDTypes[output_fmt])
    
    # Collect keyword args for `stft` in dictionary
    stftKw = {"fs": samplerate,
              "nperseg": nperseg,
              "noverlap": noverlap,
              "return_onesided": True,
              "boundary": stftBdry,
              "padded": stftPad,
              "axis": 0}
    
    # Call `stft` w/first taper to get freq/time indices: transpose resulting `pxx`
    # to have a time x freq x channel array
    win = np.atleast_2d(taper(nperseg, **taperopt))
    stftKw["window"] = win[0, :]
    if equidistant:
        freq, _, pxx = signal.stft(dat[soi, :], **stftKw)
        _, fIdx = best_match(freq, foi, squash_duplicates=True)
        spec[:, 0, ...] = \
            spectralConversions[output_fmt](
                pxx.transpose(2, 0, 1))[:nTime, fIdx, :]
    else:
        freq, _, pxx = signal.stft(dat[soi[0], :], **stftKw)
        _, fIdx = best_match(freq, foi, squash_duplicates=True)
        spec[0, 0, ...] = \
            spectralConversions[output_fmt](
                pxx.transpose(2, 0, 1).squeeze())[fIdx, :]
        for tk in range(1, len(soi)):
            spec[tk, 0, ...] = \
                spectralConversions[output_fmt](
                    signal.stft(
                        dat[soi[tk], :], 
                        **stftKw)[2].transpose(2, 0, 1).squeeze())[fIdx, :]

    # Compute FT using determined indices above for the remaining tapers (if any)
    for taperIdx in range(1, win.shape[0]):
        stftKw["window"] = win[taperIdx, :]
        if equidistant:
            spec[:, taperIdx, ...] = \
                spectralConversions[output_fmt](
                    signal.stft(
                        dat[soi, :],
                        **stftKw)[2].transpose(2, 0, 1))[:nTime, fIdx, :]
        else:
            for tk, sample in enumerate(soi):
                spec[tk, taperIdx, ...] = \
                    spectralConversions[output_fmt](
                        signal.stft(
                            dat[sample, :],
                            **stftKw)[2].transpose(2, 0, 1).squeeze())[fIdx, :]

    # Average across tapers if wanted
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

    def process_metadata(self, data, out):

        # Get trialdef array + channels from source        
        if data._selection is not None:
            chanSec = data._selection.channel
            trl = data._selection.trialdefinition
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
        out.taper = np.array([self.cfg["taper"].__name__] * self.outputShape[out.dimord.index("taper")])
        out.freq = self.cfg["foi"]


# -----------------
# WaveletTransform
# -----------------


@unwrap_io
def wavelet_cF(
    trl_dat,
    preselect,
    postselect,
    # padbegin, # were always 0!
    # padend,
    toi=None,
    timeAxis=0,
    polyremoval=None,
    output_fmt="pow",
    noCompute=False,
    chunkShape=None,
    method_kwargs=None,
):
    """ 
    This is the middleware for the 
    :func:`~syncopy.specest.wavelet.wavelet
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
    padbegin : int
        Number of samples to pre-pend to `trl_dat`
    padend : int
        Number of samples to append to `trl_dat`
    toi : 1D :class:`numpy.ndarray` or str
        Either time-points to center wavelets on if `toi` is a :class:`numpy.ndarray`,
        or `"all"` to center wavelets on all samples in `trl_dat`. Please refer to 
        :func:`~syncopy.freqanalysis` for further details. **Note**: The value 
        of `toi` has to agree with provided padding values. See Notes for more 
        information. 
    timeAxis : int
        Index of running time axis in `trl_dat` (0 or 1)
leWavelets`
    polyremoval : int
        **FIXME: Not implemented yet**
        Order of polynomial used for de-trending. A value of 0 corresponds to 
        subtracting the mean ("de-meaning"), ``polyremoval = 1`` removes linear 
        trends (subtracting the least squares fit of a linear function), 
        ``polyremoval = N`` for `N > 1` subtracts a polynomial of order `N` (``N = 2`` 
        quadratic, ``N = 3`` cubic etc.). If `polyremoval` is `None`, no de-trending
        is performed. 
    output_fmt : str
        Output of spectral estimation; one of :data:`~syncopy.specest.freqanalysis.availableOutputs`
    noCompute : bool
        Preprocessing flag. If `True`, do not perform actual calculation but
        instead return expected shape and :class:`numpy.dtype` of output
        array.
    chunkShape : None or tuple
        If not `None`, represents shape of output object `spec` (respecting provided 
        values of `scales`, `preselect`, `postselect` etc.)
    method_kwargs : dict
        Keyword arguments passed to :func:`~syncopy.specest.wavelet.wavelet
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

    # Pad input array if wanted/necessary
    # if padbegin > 0 or padend > 0:
    #     dat = padding(
    #         dat,
    #         "zero",
    #         pad="relative",
    #         padlength=None,
    #         prepadlength=padbegin,
    #         postpadlength=padend,
    #     )

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

    def process_metadata(self, data, out):

        # Get trialdef array + channels from source
        if data._selection is not None:
            chanSec = data._selection.channel
            trl = data._selection.trialdefinition
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


@unwrap_io
def superlet_cF(
    trl_dat,
    preselect,
    postselect,
    # padbegin, # were always 0!
    # padend,
    toi=None,
    timeAxis=0,
    output_fmt="pow",
    noCompute=False,
    chunkShape=None,
    method_kwargs=None,
):

    """
    This is the middleware for the 
        :func:`~syncopy.specest.superlet.superlet
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
    padbegin : int
        Number of samples to pre-pend to `trl_dat`
    padend : int
        Number of samples to append to `trl_dat`
    toi : 1D :class:`numpy.ndarray` or str
        Either array of equidistant time-points 
        or `"all"` to perform analysis on all samples in `trl_dat`. Please refer to 
        :func:`~syncopy.freqanalysis` for further details. **Note**: The value 
        of `toi` has to agree with provided padding values. See Notes for more 
        information. 
    output_fmt : str
        Output of spectral estimation; one of 
        :data:`~syncopy.specest.freqanalysis.availableOutputs`
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
        Shape is (nTime, 1, nScales, nChannels).

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

    # # Pad input array if wanted/necessary
    # if padbegin > 0 or padend > 0:
    #     dat = padding(
    #         dat,
    #         "zero",
    #         pad="relative",
    #         padlength=None,
    #         prepadlength=padbegin,
    #         postpadlength=padend,
    #     )

    # Get shape of output for dry-run phase
    nChannels = trl_dat.shape[1]
    if isinstance(toi, np.ndarray):  # `toi` is an array of time-points
        nTime = toi.size
    else:  # `toi` is 'all'
        nTime = dat.shape[0]
    nScales = method_kwargs["scales"].size
    outShape = (nTime, 1, nScales, nChannels)
    if noCompute:
        return outShape, spectralDTypes[output_fmt]

    # ------------------
    # actual method call
    # ------------------
    gmean_spec = superlet(trl_dat[preselect, :], **method_kwargs)
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
    

    def process_metadata(self, data, out):

        # Get trialdef array + channels from source
        if data._selection is not None:
            chanSec = data._selection.channel
            trl = data._selection.trialdefinition
        else:
            chanSec = slice(None)
            trl = data.trialdefinition

        # Construct trialdef array and compute new sampling rate
        trl, srate = _make_trialdef(self.cfg, trl, data.samplerate)

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
    Local helper to construct trialdefinition arrays for time-frequency :class:`~syncopy.SpectralData` objects
    
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
    elif isinstance(toi, Number):
        winSize = cfg["nperseg"] - cfg["noverlap"]
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
