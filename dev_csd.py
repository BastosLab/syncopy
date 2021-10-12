import numpy as np
from scipy.signal import csd as sci_csd
import scipy.signal as sci
from syncopy.connectivity.single_trial_compRoutines import cross_spectra_cF
import matplotlib.pyplot as ppl


# white noise ensemble
nSamples = 1000
fs = 1000
tvec = np.arange(nSamples) / fs

nChannels = 5
data1 = np.random.randn(nSamples, nChannels)

x1 = data1[:, 0]
y1 = data1[:, 1]


def sci_est(x, y, nper, norm=False):
    freqs1, csd1 = sci_csd(x, y, fs, window='bartlett', nperseg=nper)
    freqs2, csd2 = sci_csd(x, y, fs, window='bartlett', nperseg=nSamples)

    if norm:
        #  WIP..
        auto1 = sci_csd(x, x, fs, window='bartlett', nperseg=nper)
        auto1 *= sci_csd(y, y, fs, window='bartlett', nperseg=nper)

        auto2 = sci_csd(x, y, fs, window='bartlett', nperseg=nSamples)
        auto2 *= sci_csd(y, y, fs, window='bartlett', nperseg=nSamples)
        
        csd1 = csd1 / np.sqrt(auto1 * auto2)
        
    return (freqs1, np.abs(csd1)), (freqs2, np.abs(csd2))
    

def mtm_csd_harmonics():

    omegas = np.array([30, 80]) * 2 * np.pi
    phase_shifts = np.array([0, np.pi / 2, np.pi])

    NN = 50
    res = np.zeros((nSamples // 2 + 1, NN))
    eps = 1
    Kmax = 5
    data = [np.sum([np.cos(om * tvec + ps) for om in omegas], axis=0) for ps in phase_shifts]
    data = np.array(data).T
    data = 5 * (data + np.random.randn(nSamples, 3) * eps)
    
    for i in range(NN):

        freqs, CS = cross_spectra_cF(data, fs, taper='bartlett')
        freqs, CS2 = cross_spectra_cF(data, fs, taper='dpss', taperopt={'Kmax' : Kmax, 'NW' : 6}, norm=True)

        res[:, i] = np.abs(CS2[0, 0, 1, :])

    q1 = np.percentile(res, 25, axis=1)
    q3 = np.percentile(res, 75, axis=1)
    med = np.percentile(res, 50, axis=1)

    fig, ax = ppl.subplots(figsize=(6,4), num=None)
    ax.set_xlabel('frequency (Hz)')
    ax.set_ylabel('coherence')
    ax.set_ylim((-.02,1.05))
    ax.set_title(f'MTM coherence, {Kmax} tapers, SNR={1/eps**2}')

    c = 'cornflowerblue'
    ax.plot(freqs, med, lw=2, alpha=0.8, c=c)
    ax.fill_between(freqs, q1, q3, color=c, alpha=0.3)


sig1 = np.cos(2 * np.pi * 30 * tvec)
sig2 = np.sin(2 * np.pi * 30 * tvec)

mode = 'same'
r = sci.correlate(sig1, sig2, mode=mode, method='fft')
r2 = sci.fftconvolve(sig1, sig2[::-1], mode=mode)
assert np.all(r == r2)

lags = np.arange(-nSamples // 2, nSamples // 2)

ppl.figure(1)
ppl.xlabel('lag (s)')
ppl.ylabel('convolution result')

ppl.plot(lags, r2, lw = 1.5)
#  ppl.xlim((490,600))

norm = np.arange(nSamples // 2, nSamples) / 2
norm = np.r_[norm, norm[::-1]]

ppl.figure(2)
ppl.xlabel('lag (s)')
ppl.ylabel('correlation')
ppl.plot(lags, r2 / norm, lw = 1.5)


sig3 = np.cos(2 * np.pi * 30 * tvec + np.pi)
data = np.c_[sig1, sig2, sig3, sig1]

rr = sci.fftconvolve(data, data[::-1, ::-1], mode='same')


