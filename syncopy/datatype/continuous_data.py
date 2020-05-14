# -*- coding: utf-8 -*-
# 
# SynCoPy ContinuousData abstract class + regular children
# 
# Created: 2019-03-20 11:11:44
# Last modified by: Stefan Fuertinger [stefan.fuertinger@esi-frankfurt.de]
# Last modification time: <2020-05-14 16:45:26>
"""Uniformly sampled (continuous data).

This module holds classes to represent data with a uniformly sampled time axis.

"""
# Builtin/3rd party package imports
import h5py
import os
import inspect
import numpy as np
from abc import ABC
from collections.abc import Iterator
from numpy.lib.format import open_memmap

# Local imports
from .base_data import BaseData, FauxTrial
from .methods.definetrial import definetrial
from .methods.selectdata import selectdata
from syncopy.shared.parsers import scalar_parser, array_parser
from syncopy.shared.errors import SPYValueError, SPYIOError
from syncopy.shared.tools import best_match
from syncopy.plotting import _plot_analog

__all__ = ["AnalogData", "SpectralData"]


class ContinuousData(BaseData, ABC):
    """Abstract class for uniformly sampled data

    Notes
    -----
    This class cannot be instantiated. Use one of the children instead.

    """
    
    _infoFileProperties = BaseData._infoFileProperties + ("samplerate", "channel",)
    _hdfFileAttributeProperties = BaseData._hdfFileAttributeProperties + ("samplerate", "channel",)
    _hdfFileDatasetProperties = BaseData._hdfFileDatasetProperties + ("data",)
    
    @property
    def data(self):
        """array-like object representing data without trials
        
        Trials are concatenated along the time axis.
        """

        if getattr(self._data, "id", None) is not None:
            if self._data.id.valid == 0:
                lgl = "open HDF5 container"
                act = "backing HDF5 container {} has been closed"
                raise SPYValueError(legal=lgl, actual=act.format(self.filename),
                                    varname="data")
        return self._data
    
    @data.setter
    def data(self, inData):

        self._set_dataset_property(inData, "data")

        if inData is None:
            return

    def __str__(self):
        # Get list of print-worthy attributes
        ppattrs = [attr for attr in self.__dir__()
                   if not (attr.startswith("_") or attr in ["log", "trialdefinition", "hdr"])]
        ppattrs = [attr for attr in ppattrs
                   if not (inspect.ismethod(getattr(self, attr))
                           or isinstance(getattr(self, attr), Iterator))]
        
        ppattrs.sort()

        # Construct string for pretty-printing class attributes
        dsep = "' x '"
        dinfo = ""
        hdstr = "Syncopy {clname:s} object with fields\n\n"
        ppstr = hdstr.format(diminfo=dinfo + "'"  + \
                             dsep.join(dim for dim in self.dimord) + "' " if self.dimord is not None else "Empty ",
                             clname=self.__class__.__name__)
        maxKeyLength = max([len(k) for k in ppattrs])
        printString = "{0:>" + str(maxKeyLength + 5) + "} : {1:}\n"
        for attr in ppattrs:
            value = getattr(self, attr)
            if hasattr(value, 'shape') and attr == "data" and self.sampleinfo is not None:
                tlen = np.unique([sinfo[1] - sinfo[0] for sinfo in self.sampleinfo])
                if tlen.size == 1:
                    trlstr = "of length {} ".format(str(tlen[0]))
                else:
                    trlstr = ""
                dsize = np.prod(self.data.shape)*self.data.dtype.itemsize/1024**2
                dunit = "MB"
                if dsize > 1000:
                    dsize /= 1024
                    dunit = "GB"
                valueString = "{} trials {}defined on ".format(str(len(self.trials)), trlstr)
                valueString += "[" + " x ".join([str(numel) for numel in value.shape]) \
                              + "] {dt:s} {tp:s} " +\
                              "of size {sz:3.2f} {szu:s}"
                valueString = valueString.format(dt=self.data.dtype.name,
                                                 tp=self.data.__class__.__name__,
                                                 sz=dsize,
                                                 szu=dunit)
            elif hasattr(value, 'shape'):
                valueString = "[" + " x ".join([str(numel) for numel in value.shape]) \
                              + "] element " + str(type(value))
            elif isinstance(value, list):
                valueString = "{0} element list".format(len(value))
            elif isinstance(value, dict):
                msg = "dictionary with {nk:s}keys{ks:s}"
                keylist = value.keys()
                showkeys = len(keylist) < 7
                valueString = msg.format(nk=str(len(keylist)) + " " if not showkeys else "",
                                         ks=" '" + "', '".join(key for key in keylist) + "'" if showkeys else "")
            else:
                valueString = str(value)
            ppstr += printString.format(attr, valueString)
        ppstr += "\nUse `.log` to see object history"
        return ppstr
        
    @property
    def _shapes(self):
        if self.sampleinfo is not None:
            sid = self.dimord.index("time")
            shp = [list(self.data.shape) for k in range(self.sampleinfo.shape[0])]
            for k, sg in enumerate(self.sampleinfo):
                shp[k][sid] = sg[1] - sg[0]
            return [tuple(sp) for sp in shp]

    @property
    def channel(self):
        """ :class:`numpy.ndarray` : list of recording channel names """
        # if data exists but no user-defined channel labels, create them on the fly
        if self._channel is None and self._data is not None:
            nChannel = self.data.shape[self.dimord.index("channel")]        
            return np.array(["channel" + str(i + 1).zfill(len(str(nChannel)))
                           for i in range(nChannel)])            
        return self._channel

    @channel.setter
    def channel(self, channel):                                
        
        if channel is None:
            self._channel = None
            return
        
        if self.data is None:
            raise SPYValueError("Syncopy: Cannot assign `channels` without data. " +
                  "Please assign data first")     
                    
        try:
            array_parser(channel, varname="channel", ntype="str", 
                         dims=(self.data.shape[self.dimord.index("channel")],))
        except Exception as exc:
            raise exc
        
        self._channel = np.array(channel)

    @property
    def samplerate(self):
        """float: sampling rate of uniformly sampled data in Hz"""
        return self._samplerate

    @samplerate.setter
    def samplerate(self, sr):
        if sr is None:
            self._samplerate = None
            return
        
        try:
            scalar_parser(sr, varname="samplerate", lims=[np.finfo('float').eps, np.inf])
        except Exception as exc:
            raise exc
        self._samplerate = float(sr)

    @property
    def time(self):
        """list(float): trigger-relative time axes of each trial """
        if self.samplerate is not None and self.sampleinfo is not None:
            return [(np.arange(0, stop - start) + self._t0[tk]) / self.samplerate \
                    for tk, (start, stop) in enumerate(self.sampleinfo)]

    # # Helper function that reads a single trial into memory
    # @staticmethod
    # def _copy_trial(trialno, filename, dimord, sampleinfo, hdr):
    #     """
    #     # FIXME: currently unused - check back to see if we need this functionality
    #     """
    #     idx = [slice(None)] * len(dimord)
    #     idx[dimord.index("time")] = slice(int(sampleinfo[trialno, 0]), int(sampleinfo[trialno, 1]))
    #     idx = tuple(idx)
    #     if hdr is None:
    #         # Generic case: data is either a HDF5 dataset or memmap
    #         try:
    #             with h5py.File(filename, mode="r") as h5f:
    #                 h5keys = list(h5f.keys())
    #                 cnt = [h5keys.count(dclass) for dclass in spy.datatype.__all__
    #                        if not inspect.isfunction(getattr(spy.datatype, dclass))]
    #                 if len(h5keys) == 1:
    #                     arr = h5f[h5keys[0]][idx]
    #                 else:
    #                     arr = h5f[spy.datatype.__all__[cnt.index(1)]][idx]
    #         except:
    #             try:
    #                 arr = np.array(open_memmap(filename, mode="c")[idx])
    #             except:
    #                 raise SPYIOError(filename)
    #         return arr
    #     else:
    #         # For VirtualData objects
    #         dsets = []
    #         for fk, fname in enumerate(filename):
    #             dsets.append(np.memmap(fname, offset=int(hdr[fk]["length"]),
    #                                    mode="r", dtype=hdr[fk]["dtype"],
    #                                    shape=(hdr[fk]["M"], hdr[fk]["N"]))[idx])
    #         return np.vstack(dsets)

    # Helper function that grabs a single trial
    def _get_trial(self, trialno):
        idx = [slice(None)] * len(self.dimord)
        sid = self.dimord.index("time")
        idx[sid] = slice(int(self.sampleinfo[trialno, 0]), int(self.sampleinfo[trialno, 1]))
        return self._data[tuple(idx)]
    
    def _is_empty(self):
        return super()._is_empty() or self.samplerate is None
    
    # Helper function that spawns a `FauxTrial` object given actual trial information    
    def _preview_trial(self, trialno):
        """
        Generate a `FauxTrial` instance of a trial
        
        Parameters
        ----------
        trialno : int
            Number of trial the `FauxTrial` object is intended to mimic
            
        Returns
        -------
        faux_trl : :class:`syncopy.datatype.base_data.FauxTrial`
            An instance of :class:`syncopy.datatype.base_data.FauxTrial` mainly
            intended to be used in `noCompute` runs of 
            :meth:`syncopy.shared.computational_routine.ComputationalRoutine.computeFunction`
            to avoid loading actual trial-data into memory. 
            
        See also
        --------
        syncopy.datatype.base_data.FauxTrial : class definition and further details
        syncopy.shared.computational_routine.ComputationalRoutine : Syncopy compute engine
        """
        shp = list(self.data.shape)
        idx = [slice(None)] * len(self.dimord)
        tidx = self.dimord.index("time")
        stop = int(self.sampleinfo[trialno, 1])
        start = int(self.sampleinfo[trialno, 0])
        shp[tidx] = stop - start
        idx[tidx] = slice(start, stop)
        
        # process existing data selections
        if self._selection is not None:
            
            # time-selection is most delicate due to trial-offset
            tsel = self._selection.time[self._selection.trials.index(trialno)]
            if isinstance(tsel, slice):
                if tsel.start is not None:
                    tstart = tsel.start 
                else:
                    tstart = 0
                if tsel.stop is not None:
                    tstop = tsel.stop
                else:
                    tstop = stop - start

                # account for trial offsets an compute slicing index + shape
                start = start + tstart
                stop = start + (tstop - tstart)
                idx[tidx] = slice(start, stop)
                shp[tidx] = stop - start
                
            else:
                idx[tidx] = [tp + start for tp in tsel]
                shp[tidx] = len(tsel)

            # process the rest                
            for dim in ["channel", "freq", "taper"]:
                sel = getattr(self._selection, dim)
                if sel:
                    dimIdx = self.dimord.index(dim)
                    idx[dimIdx] = sel
                    if isinstance(sel, slice):
                        begin, end, delta = sel.start, sel.stop, sel.step
                        if sel.start is None:
                            begin = 0
                        elif sel.start < 0:
                            begin = shp[dimIdx] + sel.start
                        if sel.stop is None:
                            end = shp[dimIdx]
                        elif sel.stop < 0:
                            end = shp[dimIdx] + sel.stop
                        if sel.step is None:
                            delta = 1
                        shp[dimIdx] = int(np.ceil((end - begin) / delta))
                        idx[dimIdx] = slice(begin, end, delta)
                    else:
                        shp[dimIdx] = len(sel)
                        
        return FauxTrial(shp, tuple(idx), self.data.dtype, self.dimord)
    
    # Helper function that extracts timing-related indices
    def _get_time(self, trials, toi=None, toilim=None):
        """
        Get relative by-trial indices of time-selections
        
        Parameters
        ----------
        trials : list
            List of trial-indices to perform selection on
        toi : None or list
            Time-points to be selected (in seconds) on a by-trial scale. 
        toilim : None or list
            Time-window to be selected (in seconds) on a by-trial scale
            
        Returns
        -------
        timing : list of lists
            List of by-trial sample-indices corresponding to provided 
            time-selection. If both `toi` and `toilim` are `None`, `timing`
            is a list of universal (i.e., ``slice(None)``) selectors. 
            
        Notes
        -----
        This class method is intended to be solely used by 
        :class:`syncopy.datatype.base_data.Selector` objects and thus has purely 
        auxiliary character. Therefore, all input sanitization and error checking
        is left to :class:`syncopy.datatype.base_data.Selector` and not 
        performed here. 
        
        See also
        --------
        syncopy.datatype.base_data.Selector : Syncopy data selectors
        """
        timing = []
        if toilim is not None:
            for trlno in trials:
                _, selTime = best_match(self.time[trlno], toilim, span=True)
                selTime = selTime.tolist()
                if len(selTime) > 1:
                    timing.append(slice(selTime[0], selTime[-1] + 1, 1))
                else:
                    timing.append(selTime)
                    
        elif toi is not None:
            for trlno in trials:
                _, selTime = best_match(self.time[trlno], toi)
                selTime = selTime.tolist()
                if len(selTime) > 1:
                    timeSteps = np.diff(selTime)
                    if timeSteps.min() == timeSteps.max() == 1:
                        selTime = slice(selTime[0], selTime[-1] + 1, 1)
                timing.append(selTime)
                
        else:
            timing = [slice(None)] * len(trials)
            
        return timing

    # Make instantiation persistent in all subclasses
    def __init__(self, data=None, channel=None, samplerate=None, **kwargs):     
        
        self._channel = None
        self._samplerate = None
        self._data = None
        
        # Call initializer
        super().__init__(data=data, **kwargs)
        
        self.channel = channel
        self.samplerate = samplerate     # use setter for error-checking   
        self.data = data
        
        if self.data is not None:

            # In case of manual data allocation (reading routine would leave a
            # mark in `cfg`), fill in missing info
            if len(self.cfg) == 0:
                
                # First, fill in dimensional info
                definetrial(self, kwargs.get("trialdefinition"))


class AnalogData(ContinuousData):
    """Multi-channel, uniformly-sampled, analog (real float) data

    This class can be used for representing any analog signal data with a time
    and a channel axis such as local field potentials, firing rates, eye
    position etc.

    The data is always stored as a two-dimensional array on disk. On disk, Trials are
    concatenated along the time axis. 

    Data is only read from disk on demand, similar to memory maps and HDF5
    files.
    """
    
    _infoFileProperties = ContinuousData._infoFileProperties + ("_hdr",)
    _defaultDimord = ["time", "channel"]
 
    # Monkey-patch plotting routines to not clutter the core module code
    singlepanelplot = _plot_analog.singlepanelplot
    multipanelplot = _plot_analog.multipanelplot
    
    @property
    def hdr(self):
        """dict with information about raw data
        
        This property is empty for data created by Syncopy.
        """
        return self._hdr

    # Selector method FIXME: use monkey patching?
    def selectdata(self, trials=None, channels=None, toi=None, toilim=None):
        """
        Create new `AnalogData` object from selection
        
        Please refer to :func:`syncopy.selectdata` for detailed usage information. 
        
        Examples
        --------
        >>> ang2chan = ang.selectdata(channels=["channel01", "channel02"])
        
        See also
        --------
        syncopy.selectdata : create new objects via deep-copy selections
        """
        return selectdata(self, trials=trials, channels=channels, toi=toi, toilim=toilim)
        
    # "Constructor"
    def __init__(self,
                 data=None,
                 filename=None,
                 trialdefinition=None,
                 samplerate=None,
                 channel=None,
                 dimord=None):
        """Initialize an :class:`AnalogData` object.
        
        Parameters
        ----------
            data : 2D :class:numpy.ndarray or HDF5 dataset   
                multi-channel time series data with uniform sampling            
            filename : str
                path to target filename that should be used for writing
            trialdefinition : :class:`EventData` object or Mx3 array 
                [start, stop, trigger_offset] sample indices for `M` trials
            samplerate : float
                sampling rate in Hz
            channel : str or list/array(str)
            dimord : list(str)
                ordered list of dimension labels

        1. `filename` + `data` : create hdf dataset incl. sampleinfo @filename
        2. just `data` : try to attach data (error checking done by :meth:`AnalogData.data.setter`)
        
        See also
        --------
        :func:`syncopy.definetrial`
        
        """

        # FIXME: I think escalating `dimord` to `BaseData` should be sufficient so that 
        # the `if any(key...) loop in `BaseData.__init__()` takes care of assigning a default dimord
        if data is not None and dimord is None:
            dimord = self._defaultDimord            

        # Assign default (blank) values
        self._hdr = None

        # Call parent initializer
        super().__init__(data=data,
                         filename=filename,
                         trialdefinition=trialdefinition,
                         samplerate=samplerate,
                         channel=channel,
                         dimord=dimord)

    # # Overload ``copy`` method to account for `VirtualData` memmaps
    # def copy(self, deep=False):
    #     """Create a copy of the data object in memory.

    #     Parameters
    #     ----------
    #         deep : bool
    #             If `True`, a copy of the underlying data file is created in the temporary Syncopy folder

        
    #     Returns
    #     -------
    #         AnalogData
    #             in-memory copy of AnalogData object

    #     See also
    #     --------
    #     save_spy

    #     """

    #     cpy = copy(self)
        
    #     if deep:
    #         if isinstance(self.data, VirtualData):
    #             print("SyNCoPy core - copy: Deep copy not possible for " +
    #                   "VirtualData objects. Please use `save_spy` instead. ")
    #             return
    #         elif isinstance(self.data, (np.memmap, h5py.Dataset)):
    #             self.data.flush()
    #             filename = self._gen_filename()
    #             shutil.copyfile(self._filename, filename)
    #             cpy.data = filename
    #     return cpy


class SpectralData(ContinuousData):
    """Multi-channel, real or complex spectral data

    This class can be used for representing any data with a frequency, channel,
    and optionally a time axis. The datatype can be complex or float.

    """
    
    _infoFileProperties = ContinuousData._infoFileProperties + ("taper", "freq",)
    _defaultDimord = ["time", "taper", "freq", "channel"]
    
    @property
    def taper(self):
        """ :class:`numpy.ndarray` : list of window functions used """
        if self._taper is None and self._data is not None:
            nTaper = self.data.shape[self.dimord.index("taper")]
            return np.array(["taper" + str(i + 1).zfill(len(str(nTaper)))
                            for i in range(nTaper)])
        return self._taper

    @taper.setter
    def taper(self, tpr):
        
        if tpr is None:
            self._taper = None
            return
        
        if self.data is None:
            print("Syncopy core - taper: Cannot assign `taper` without data. "+\
                  "Please assing data first")
            return
        
        try:
            array_parser(tpr, dims=(self.data.shape[self.dimord.index("taper")],),
                         varname="taper", ntype="str", )
        except Exception as exc:
            raise exc
        
        self._taper = np.array(tpr)

    @property
    def freq(self):
        """:class:`numpy.ndarray`: frequency axis in Hz """
        # if data exists but no user-defined frequency axis, create one on the fly
        if self._freq is None and self._data is not None:
            return np.arange(self.data.shape[self.dimord.index("freq")])
        return self._freq

    @freq.setter
    def freq(self, freq):
        
        if freq is None:
            self._freq = None
            return
        
        if self.data is None:
            print("Syncopy core - freq: Cannot assign `freq` without data. "+\
                  "Please assing data first")
            return
        try:
            
            array_parser(freq, varname="freq", hasnan=False, hasinf=False,
                         dims=(self.data.shape[self.dimord.index("freq")],))
        except Exception as exc:
            raise exc
        
        self._freq = np.array(freq)

    # Selector method
    def selectdata(self, trials=None, channels=None, toi=None, toilim=None,
                   foi=None, foilim=None, tapers=None):
        """
        Create new `SpectralData` object from selection
        
        Please refer to :func:`syncopy.selectdata` for detailed usage information. 
        
        Examples
        --------
        >>> spcBand = spc.selectdata(foilim=[10, 40])
        
        See also
        --------
        syncopy.selectdata : create new objects via deep-copy selections
        """
        return selectdata(self, trials=trials, channels=channels, toi=toi, 
                          toilim=toilim, foi=foi, foilim=foilim, tapers=tapers)
    
    # Helper function that extracts frequency-related indices
    def _get_freq(self, foi=None, foilim=None):
        """
        Coming soon... 
        Error checking is performed by `Selector` class
        """
        if foilim is not None:
            _, selFreq = best_match(self.freq, foilim, span=True)
            selFreq = selFreq.tolist()
            if len(selFreq) > 1:
                selFreq = slice(selFreq[0], selFreq[-1] + 1, 1)
                
        elif foi is not None:
            _, selFreq = best_match(self.freq, foi)
            selFreq = selFreq.tolist()
            if len(selFreq) > 1:
                freqSteps = np.diff(selFreq)
                if freqSteps.min() == freqSteps.max() == 1:
                    selFreq = slice(selFreq[0], selFreq[-1] + 1, 1)
                    
        else:
            selFreq = slice(None)
            
        return selFreq
    
    # "Constructor"
    def __init__(self,
                 data=None,
                 filename=None,
                 trialdefinition=None,
                 samplerate=None,
                 channel=None,
                 taper=None,
                 freq=None,
                 dimord=None):

        self._taper = None
        self._freq = None
        
        # FIXME: See similar comment above in `AnalogData.__init__()`
        if data is not None and dimord is None:
            dimord = self._defaultDimord
                 
        # Call parent initializer
        super().__init__(data=data,
                         filename=filename,
                         trialdefinition=trialdefinition,
                         samplerate=samplerate,
                         channel=channel,
                         taper=taper,
                         freq=freq,
                         dimord=dimord)

        # If __init__ attached data, be careful
        if self.data is not None:

            # In case of manual data allocation (reading routine would leave a
            # mark in `cfg`), fill in missing info
            if len(self.cfg) == 0:
                self.freq = freq
                self.taper = taper

        # Dummy assignment: if we have no data but freq/taper labels,
        # assign bogus to trigger setter warnings
        else:
            if freq is not None:
                self.freq = [1]
            if taper is not None:
                self.taper = ['taper']
