# specest.py - SpykeWave spectral estimation methods
# 
# Created: January 22 2019
# Last modified by: Stefan Fuertinger [stefan.fuertinger@esi-frankfurt.de]
# Last modification time: <2019-02-22 17:04:07>

# Builtin/3rd party package imports
import sys
import numpy as np
import scipy.signal as signal
import scipy.signal.windows as windows
from numpy.lib.format import open_memmap
from spykewave import __dask__
if __dask__:
    import dask
    import dask.array as da

# Local imports
from spykewave.utils import spw_basedata_parser
from spykewave.datatype import SpectralData

__all__ = ["mtmfft"]

##########################################################################################
def mtmfft(obj, taper=windows.hann, pad="nextpow2", padtype="zero",
           polyorder=None, taperopt={}, fftAxis=1, tapsmofrq=None, out=None):

    # FIXME: parse remaining input arguments
    if polyorder:
        raise NotImplementedError("Detrending has not been implemented yet.")

    # Make sure input object can be processed
    try:
        spw_basedata_parser(obj, varname="obj", dimord=["channel", "sample"],
                            writable=None, empty=False)
    except Exception as exc:
        raise exc
    
    # If provided, make sure output object is appropriate 
    if out is not None:
        try:
            spw_basedata_parser(out, varname="out", writable=True,
                                dimord=["taper", "channel", "freq"], segmentlabel="freq")
        except Exception as exc:
            raise exc
        new_out = False
    else:
        out = SpectralData()
        new_out = True

    # Set parameters applying to all segments: FIXME: make sure segments
    # are consistent, i.e., padding results in same no. of freqs across all segments
    fftAxis = obj.dimord.index("sample")
    if pad == "nextpow2":
        nSamples = _nextpow2(obj.shapes[0][1])
    else:
        raise NotImplementedError("Coming soon...")
    if taper == windows.dpss and (not taperopt):
        nTaper = np.int(np.floor(tapsmofrq * T))
        taperopt = {"NW": tapsmofrq, "Kmax": nTaper}

    # Compute taper in shape nTaper x nSamples and determine size of freq. axis
    win = np.atleast_2d(taper(nSamples, **taperopt))
    nFreq = int(np.floor(nSamples / 2) + 1)
    freq = np.arange(0, np.floor(nSamples / 2) + 1) * obj.samplerate/nSamples
    
    # Allocate memory map for results
    res = open_memmap(out._filename,
                      shape=(win.shape[0], obj.shapes[0][0], nFreq*len(obj.segments)),
                      dtype="complex",
                      mode="w+")
    del res

    # See if a dask client is running
    try:
        use_dask = bool(get_client())
    except:
        use_dask = False

    # Perform parallel computation
    if use_dask:

        # Point to data segments on disk by using delayed **static** method calls
        lazy_segment = dask.delayed(obj._copy_segment, traverse=False)
        lazy_segs = [lazy_segment(segno,
                                  obj._filename,
                                  obj.seg,
                                  obj.hdr,
                                  obj.dimord,
                                  obj.segmentlabel)\
                     for segno in range(obj._seg.shape[0])]

        # Construct a distributed dask array block by stacking delayed segments
        seg_block = da.hstack([da.from_delayed(seg,
                                               shape=obj.shapes[sk],
                                               dtype=obj.data.dtype) for sk, seg in enumerate(lazy_segs)])

        # Use `map_blocks` to compute spectra for each segment in the constructred dask array
        specs = seg_block.map_blocks(_mtmfft_byseg, win, nFreq,  pad, padtype, fftAxis, use_dask,
                                     dtype="complex",
                                     chunks=(win.shape[0], obj.data.shape[0], nFreq),
                                     new_axis=[0])

        # Write computed spectra in pre-allocated memmap
        result = specs.map_blocks(_mtmfft_writer, nFreq, out._filename,
                              dtype="complex",
                              chunks=(1,),
                              drop_axis=[0,1])

        # Perform actual computation
        result.compute()

    # Serial calculation solely relying on NumPy
    else:
        for sk, seg in enumerate(obj.segments):
            res = open_memmap(out._filename, mode="r+")[:, :, sk*nFreq : (sk + 1)*nFreq]
            res[...] = _mtmfft_byseg(seg, win, nFreq,  pad, padtype, fftAxis, use_dask)
            del res
            obj.clear()
        
    # Attach results to output object: start w/ dimensional info (order matters!)
    out._dimlabels["taper"] = [taper.__name__] * win.shape[0]
    out._dimlabels["channel"] = obj.label
    out._dimlabels["freq"] = freq

    # Write data and meta-info
    out._samplerate = obj.samplerate
    seg = np.array(obj.seg)
    for k in range(seg.shape[0]):
        seg[k, [0, 1]] = [k*nFreq, (k+1)*nFreq]
    out._seg = seg
    out._data = open_memmap(out._filename, mode="r+")
    out.cfg = {"method" : sys._getframe().f_code.co_name,
               "taper" : taper.__name__,
               "padding" : pad,
               "padtype" : padtype,
               "polyorder" : polyorder,
               "taperopt" : taperopt,
               "tapsmofrq" : tapsmofrq}

    # Write log
    log = "computed multi-taper FFT with settings..."
    out.log = log

    # Happy breakdown
    return out if new_out else None
    
##########################################################################################
def _mtmfft_writer(blk, nFreq, resname, block_info=None):
    """
    Pumps computed spectra into target memmap
    """
    idx = block_info[0]["chunk-location"][-1]
    res = open_memmap(resname, mode="r+")[:, :, idx*nFreq : (idx + 1)*nFreq]
    res[...] = blk
    del res
    return idx

##########################################################################################
def _mtmfft_byseg(seg, win, nFreq,  pad, padtype, fftAxis, use_dask):
    """
    Performs the actual heavy-lifting
    """

    # move fft/samples dimension into first place
    seg = np.moveaxis(np.atleast_2d(seg), fftAxis, 1)
    nSamples = seg.shape[1]
    nChannels = seg.shape[0]

    # padding
    if pad:
        padWidth = np.zeros((seg.ndim, 2), dtype=int)
        if pad == "nextpow2":
            padWidth[1, 0] = _nextpow2(nSamples) - nSamples
        else:
            padWidth[1, 0] = np.ceil((pad - T) / dt).astype(int)
        if padtype == "zero":
            seg = np.pad(seg, pad_width=padWidth,
                          mode="constant", constant_values=0)

        # update number of samples
        nSamples = seg.shape[1]

    # Decide whether to further parallelize or plow through entire chunk
    if use_dask and seg.size * seg.dtype.itemsize * 1024**(-2) > 1000:
        spex = []
        for tap in win:
            if seg.ndim > 1:
                tap = np.tile(tap, (nChannels, 1))
            prod = da.from_array(seg * tap, chunks=(1, seg.shape[1]))
            spex.append(da.fft.rfft(prod))
        spec = da.stack(spex)
    else:
        # taper x chan x freq
        spec = np.zeros((win.shape[0],) + (nChannels,) + (nFreq,), dtype=complex)
        for wIdx, tap in enumerate(win):
            if seg.ndim > 1:
                tap = np.tile(tap, (nChannels, 1))
            spec[wIdx, ...] = np.fft.rfft(seg * tap, axis=1)

    return spec

##########################################################################################
def _nextpow2(number):
    n = 1
    while n < number:
        n *= 2
    return n
