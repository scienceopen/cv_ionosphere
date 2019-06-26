#!/usr/bin/env python
"""
Michael Hirsch

video and MARSIS radar inplemented: April 2012
Passive radar added: Dec 2014

This program detects aurora in multi-terabyte raw video data files
Also used for the Haystack passive FM radar ionospheric activity detection
"""
from typing import Dict, Any
import logging
from configparser import ConfigParser
from .io import getparam, getvidinfo, keyhandler, savestat
from .reader import getraw, setscale
from .cvops import dooptflow, dothres, dodespeck, domorph, doblob
from .cvsetup import setupkern, svsetup, svrelease, setupof, setupfigs, statplot

import h5py
from datetime import datetime
from pytz import UTC
import pandas
from pathlib import Path
from time import time
import numpy as np
from scipy.ndimage import zoom

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from matplotlib.pylab import draw, pause, close
except ImportError:
    draw = pause = close = None
#
try:
    from histutils import setupimgh5
except ImportError:
    setupimgh5 = None
from morecvutils.connectedComponents import setupblob


def loopaurorafiles(U: Dict[str, Any]) -> pandas.DataFrame:

    P = getparam(U['paramfn'])  # ConfigParser object

    # note, if a specific file is given, vidext is ignored
    idir = Path(U['indir']).expanduser()
    if idir.is_file():
        files = [idir]
    elif idir.is_dir():
        files = sorted(idir.glob('*'+P.get('main', 'vidext')))
    else:
        raise FileNotFoundError(idir)

    if not files:
        raise FileNotFoundError(U["indir"])

    U['nfile'] = len(files)

    logging.info(f'found {U["nfile"]} files: {U["indir"]}')

# %% process files
    if P.get('main', 'vidext') == '.dat':
        aurstat = procfiles(files, P, U)
    else:
        # FIXME this is where detect is being cast to float, despite being int in individual dataFrames
        aurstat = pandas.DataFrame(columns=['mean', 'median', 'variance', 'detect'])
        for file in files:  # iterate over files in list
            stat = procfiles(file, P, U)
            aurstat = aurstat.append(stat)

    if aurstat is None:
        logging.error('Auroral detection aborted')
        return
# %% sort,plot,save results for all files

    aurstat.sort_index(inplace=True)  # sort by time
    savestat(aurstat, U['detfn'], idir, U)

    if aurstat.index[0] > 1e9:  # ut1 instead of index
        dt = [datetime.fromtimestamp(t, tz=UTC) for t in stat.index]
    else:
        dt = None
# %% master detection plot
    if draw is not None:
        U['pshow'] += ['stat']
        fgst = statplot(dt, aurstat, U, P, U['odir'])[3]
        draw()
        pause(0.01)
        fgst.savefig(U['detfn'].with_suffix('.png'), bbox_inches='tight', dpi=100)
        fgst.savefig(U['detfn'].with_suffix('.svg'), bbox_inches='tight', dpi=100)

    return aurstat


def procfiles(file: Path, P: ConfigParser, U: Dict[str, Any]) -> pandas.DataFrame:
    finf, U = getvidinfo(file, P, U)

    if finf['nframe'] < 100 and finf['reader'] != 'spool':
        logging.warning(f'SKIPPING {file} with only {finf["nframe"]} frames')
        return

    try:
        U = setscale(file, U, finf)  # in case auto contrast per file
    except ValueError as e:
        logging.error(f'{file}  {e}\n')
        return

    stat = procaurora(file, P, U, finf)

    return stat


def procaurora(file: Path,
               P: ConfigParser,
               U: Dict[str, Any],
               finf: Dict[str, str]) -> pandas.DataFrame:

    tic = time()

    if finf is None:
        return
# %% setup optional video/tiff writing (mainly for debugging or publication)
    svh = svsetup(P, U)
# %% setup blob
    blobdetect = setupblob(P.getfloat('blob', 'minblobarea'),
                           P.getfloat('blob', 'maxblobarea'),
                           P.getfloat('blob', 'minblobdist'))
# %% cv opt. flow matrix setup
    lastflow, gmm = setupof(U, P)
# %% kernel setup
    U = setupkern(P, U)
# %% mag plots setup
    U, stat = setupfigs(finf, file, U, P)
# %% list of files or handle?
    if finf['reader'] == 'spool':
        # comes out as bytes from HDF5, and pathlib needs str
        flist = finf['flist'][finf['frameind']].astype(str)

    N = finf['frameind'][:-1]
    if len(N) == 0:
        logging.error(f'no images found to detect in {file}')
        return
# %% start main loop

    if finf['reader'] == 'spool':
        if setupimgh5 is None:
            raise ImportError('pip install histutils')
        zy, zx = zoom(getraw(finf['path']/flist[0], 0, 0, finf, svh, P, U)[3], 0.1, order=0).shape
        # zy,zx=(64,64)
        setupimgh5(U['detfn'], np.ceil(N.size/U['previewdecim'])+1, zy, zx,
                   np.uint16, writemode='w', key='/preview', cmdlog=U['cmd'])

    j = 0
    for i, iraw in enumerate(N):
        if finf['reader'] == 'spool':
            file = finf['path'] / flist[i]
            iraw = 0
        # print(f,i,iraw)
# %% load and filter
        framegray, frameref, U, frame16 = getraw(file, iraw, finf, U, svh, P, ifits=iraw-N[0])
# %% compute optical flow or Background/Foreground
        if gmm is None:
            flow, mag, stat = dooptflow(framegray, frameref, lastflow,
                                        i, U, P, stat)
            lastflow = flow.copy()  # FIXME is the .copy() strictly necessary?
        else:  # background/foreground
            mag = gmm.apply(framegray)
# %% threshold
        thres = dothres(mag, stat['median'].iat[i], P, i, svh,  U, gmm is not None)
# %% despeckle
        despeck = dodespeck(thres, P.getint('filter', 'medfiltsize'), i, svh, U)
# %% morphological ops
        morphed = domorph(despeck, svh, U)
# %% blob detection
        stat = doblob(morphed, blobdetect, framegray, i, svh, stat, U)
# %% plotting in loop
        """
        http://docs.opencv.org/modules/highgui/doc/user_interface.html
        """
        if U['pshow']:
            draw()
            pause(0.01)

        if not i % U['previewdecim']:
            j += 1
            if finf['reader'] == 'spool':
                with h5py.File(U['detfn'], 'r+', libver='latest') as f5:
                    # ipy = slice(finf['supery']//2-zy//2,finf['supery']//2+zy//2)
                    # ipx = slice(finf['superx']//2-zx//2,finf['superx']//2+zx//2)
                    # preview = frame16[ipy,ipx].astype(np.uint16)
                    preview = zoom(frame16, 0.1, order=0, mode='nearest')
#                    assert (frame16[ipy,ipx] == preview).all(),'preview failure to convert'

                    updatestr = f'mean frame {i}: {preview.mean():.1f}  j= {j}'
                    logging.info(updatestr)
                    f5['/preview'][j, ...] = preview


#                with h5py.File(U['detfn'], 'r', libver='latest') as f5:
#                    assert (f5['/preview'][j,...] == preview).all(),'preview failure to store'
#                    assert not (f5['/preview'][j,...] == 0).all(),'preview all 0 frame'

#                if U['verbose']:
#                    fgv = figure(1002)
#                    fgv.clf()
#                    ax = fgv.gca()
#                    hi = ax.imshow(preview)
#                    ax.set_title(updatestr)
#                    fgv.colorbar(hi,ax=ax)
#                    draw(); pause(0.001)

            logging.info(f'{U["framestep"]*i/N[-1]*100:.2f}% {stat["detect"].iloc[i-U["previewdecim"]:i].values}')
            if (framegray == 255).sum() > 40:
                # arbitrarily allowing up to 40 pixels to be saturated at 255, to allow for bright stars and faint aurora
                logging.warning('video saturated at 255')
            if (framegray == 0).sum() > 4:
                logging.warning('video saturated at 0')

        if U['pshow'] and cv2 is not None:
            if U['framebyframe']:  # wait indefinitely for spacebar press
                keypressed = cv2.waitKey(0)
                U['framebyframe'], dobreak = keyhandler(keypressed, U['framebyframe'])
            else:
                keypressed = cv2.waitKey(1)
                framebyframe, dobreak = keyhandler(keypressed, U['framebyframe'])

            if dobreak:
                break
# %% done looping this file  save results for this file
    logging.info(f'{time()-tic:.1f} seconds to process {file}')

    if U['odir']:
        detfn = Path(U['odir']).expanduser()/(file.stem + '_detections.h5')
        detpltfn = Path(U['odir']).expanduser()/(file.stem + '_detections.png')
        if detfn.is_file():
            logging.warning(f'overwriting existing {detfn}')

        try:
            if finf['reader'] != 'spool':
                savestat(stat, detfn, U['indir'], U)
                if 'stat' in U['pshow']:
                    logging.info(f'saving detection plot to {detpltfn}')
                    U['fdet'].savefig(detpltfn, dpi=100, bbox_inches='tight')
        except Exception as e:
            logging.critical(f'trouble saving detection result  {e} ')
        finally:
            svrelease(svh, U['savevideo'])

    if draw is not None:
        close('all')

    return stat
