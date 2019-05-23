import logging
import cv2
import h5py
import numpy as np
from scipy.signal import wiener
from matplotlib.pyplot import figure, hist
# import fitsio  # so much faster than Astropy.io.fits


try:
    import tifffile  # tifffile is excruciatingly slow on each file access
except ImportError:
    pass

from .getpassivefm import getfmradarframe
from histutils.rawDMCreader import getDMCframe
from dmcutils.neospool import readNeoSpool
from histutils import sixteen2eight


def setscale(fn, up, finf):
    """
    if user has not set fixed upper/lower bounds for 16-8 bit conversion, do it automatically,
    since not specifying fixed contrast destroys the working of auto CV detection
    """
    pt = [0.01, 0.99]  # percentiles
    mod = False

    if not isinstance(up['rawlim'][0], (float, int)):
        mod = True
        prc = samplepercentile(fn, pt[0], finf)
        up['rawlim'][0] = prc

    if not isinstance(up['rawlim'][1], (float, int)):
        mod = True
        prc = samplepercentile(fn, pt[1], finf)
        up['rawlim'][1] = prc

# %%
    if mod:
        cdiff = up['rawlim'][1] - up['rawlim'][0]

        assert cdiff > 0, f'raw limits do not make sense  lower: {up["rawlim"][0]}   upper: {up["rawlim"][1]}'

        if cdiff < 20:
            raise ValueError('your video may have very poor contrast and not work for auto detection')

        print(f'data number lower,upper  {up["rawlim"][0]}  {up["rawlim"][1]}')

    return up


def samplepercentile(fn, pct, finf):
    """
    for 16-bit files mainly
    """
    isamp = (finf['nframe'] * np.array([.1, .25, .5, .75, .9])).astype(int)  # pick a few places in the file to touch

    isamp = isamp[:finf['nframe']-1]  # for really small files

    dat = np.empty((isamp.size, finf['supery'], finf['superx']), float)

    tmp = getraw(fn, 0, finf)[3]
    if tmp.dtype.itemsize < 2:
        logging.warning(f'{fn}: usually we use autoscale with 16-bit video, not 8-bit.')

    for j, i in enumerate(isamp):
        dat[j, ...] = getraw(fn, i, finf)[3]

    return np.percentile(dat, pct).astype(int)


def getraw(fn, i, ifrm, finf, svh, P, up):
    """ this function reads the reference frame too--which makes sense if youre
       only reading every Nth frame from the multi-TB file instead of every frame
    """
    if (not isinstance(up['rawlim'][0], (float, int)) or not isinstance(up['rawlim'][1], (float, int))):
        logging.warning(f'{fn}: not specifying fixed contrast will lead to very bad automatic detection results')

    frameref = None  # for non-twoframe case
    dowiener = P.getint('filter', 'wienernhood', fallback=None)
# %% reference frame
    if finf['reader'] == 'raw':
        with fn.open('rb') as f:
            if up['twoframe']:
                frameref = getDMCframe(f, ifrm, finf)[0]
            frame16, rfi = getDMCframe(f, ifrm+1, finf)

    elif finf['reader'] == 'spool':
        """
        Here we choose to read only the first frame pair from each spool file,
        as each spool file is generally less than about 10 frames.
        To skip further in time, skip more files.
        """
        # rfi = ifrm
        iread = (ifrm, ifrm+1) if up['twoframe'] else ifrm+1

        frames, ticks, tsec = readNeoSpool(fn, finf, iread, zerocols=P.getint('main', 'zerocols', fallback=0))
        if up['twoframe']:
            frameref = frames[0, ...]
            frame16 = frames[1, ...]
        else:
            frame16 = frames[0, ...]
    elif finf['reader'] == 'cv2':
        if up['twoframe']:
            retval, frameref = fn.read()
            # TODO best to open cv2.VideoReader in calling function as CV_CAP_PROP_POS_FRAMES
            # is said not to always work vis keyframes
            if not retval:
                if ifrm == 0:
                    logging.error('could not read video file, sorry')
                print('done reading video.')
                return None, None, up
            if frameref.ndim > 2:
                frameref = cv2.cvtColor(frameref, cv2.COLOR_RGB2GRAY)

        retval, frame16 = fn.read()  # TODO this is skipping every other frame!
        # TODO can we use dfid.set(cv.CV_CAP_PROP_POS_FRAMES,ifrm) to set 0-based index of next frame?
        # rfi = ifrm
        if not retval:
            raise IOError(f'could not read video from {fn}')

        if frame16.ndim > 2:
            framegray = cv2.cvtColor(frame16, cv2.COLOR_RGB2GRAY)
        else:
            framegray = frame16  # copy NOT needed
    elif finf['reader'] == 'h5fm':  # one frame per file
        if up['twoframe']:
            frameref = getfmradarframe(fn[ifrm])[2]
        frame16 = getfmradarframe(fn[ifrm+1])[2]
        # rfi = ifrm
    elif finf['reader'] == 'h5vid':
        with h5py.File(fn, 'r', libver='latest') as f:
            if up['twoframe']:
                frameref = f['/rawimg'][ifrm, ...]
            frame16 = f['/rawimg'][ifrm+1, ...]
        # rfi = ifrm
    elif finf['reader'] == 'fits':
        from astropy.io import fits
        # memmap = False required thru Astropy 1.3.2 due to BZERO used...
        with fits.open(fn, mode='readonly', memmap=False) as f:
            # with fitsio.FITS(str(fn),'r') as f:
            """
            i not ifrm for fits!
            """
            if up['twoframe']:  # int(int64) ~ 175 ns
                frameref = f[0][int(i), :, :].squeeze()  # no ellipses for fitsio
            frame16 = f[0][int(i+1), :, :].squeeze()
        # rfi = ifrm  # TODO: incorrect raw index with sequence of fits files
    elif finf['reader'] == 'tiff':
        if 'htiff' not in up:  # first read
            print('first open', fn)
            up['htiff'] = tifffile.TiffFile(str(fn))
        elif up['htiff'].filename != fn.name:
            print('opening', fn)
            up['htiff'].close()
            up['htiff'] = tifffile.TiffFile(str(fn))
        # f= libtiff.TIFF3D.open(str(fn)) # ctypes.ArgumentError: argument 1: <class 'TypeError'>: wrong type
        if up['twoframe']:
            frameref = up['htiff'][ifrm].asarray()
        frame16 = up['htiff'][ifrm+1].asarray()

        # rfi = ifrm
    else:
        raise TypeError(f'unknown reader type {finf["reader"]}')
# %% current frame
#    if 'rawframeind' in up:
#        up['rawframeind'][ifrm] = rfi

    if dowiener:
        if up['twoframe']:
            frameref = wiener(frameref, dowiener)
        framegray = wiener(frame16, dowiener)

    if finf['reader'] != 'cv2':
        if up['twoframe']:
            frameref = sixteen2eight(frameref, up['rawlim'])
        framegray = sixteen2eight(frame16, up['rawlim'])
        # frameref  = bytescale(frameref, up['rawlim'][0], up['rawlim'][1])
        # framegray = bytescale(frame16, up['rawlim'][0], up['rawlim'][1])

    if up and 'raw' in up['pshow']:
        # cv2.imshow just divides by 256, NOT autoscaled!
        # http://docs.opencv.org/modules/highgui/doc/user_interface.html
        cv2.imshow('video', framegray)
# %% plotting
    if up and 'rawscaled' in up['pshow']:
        cv2.imshow('raw video, scaled to 8-bit', framegray)
    # image histograms (to help verify proper scaling to uint8)
    if up and 'hist' in up['pshow']:
        ax = figure().gca()
        hist(frame16.flatten(), bins=128, fc='w', ec='k', log=True)
        ax.set_title('raw uint16 values')

        ax = figure().gca()
        hist(framegray.flatten(), bins=128, fc='w', ec='k', log=True)
        ax.set_xlim((0, 255))
        ax.set_title('normalized video into opt flow')

    if svh and svh['video'] is not None:
        if up['savevideo'] == 'tif':
            svh['video'].save(framegray, compress=up['complvl'])
        elif up['savevideo'] == 'vid':
            svh['video'].write(framegray)

    return framegray, frameref, up, frame16
