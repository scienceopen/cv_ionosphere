#!/usr/bin/python2
"""we temporarily use python 2.7 until OpenCV 3 is out of beta (will work with Python 3)
Michael Hirsch Dec 2014
This program detects aurora in multi-terabyte raw video data files
It is a major cleanup of the processDrive.sh, filelooper.m, TrackingOF7.m Frankenprograms

0) recursively find all .DMCdata files under requested root directory
1)
"""
from __future__ import division
import cv2, cv
from re import search
from pandas import read_excel
from os.path import join,isfile
from numpy import (isnan,empty,uint32,delete,mgrid,vstack,int32,arctan2,
                   sqrt,zeros,pi,uint8,minimum,s_,asarray, median, dstack,
                   hypot,inf, logical_and)
from scipy.signal import wiener
import sys
from matplotlib.pylab import draw, pause, figure, hist
from pdb import set_trace
#from matplotlib.pyplot import figure,show
#
from walktree import walktree
from sixteen2eight import sixteen2eight
sys.path.append('../hist-utils')
from rawDMCreader import getDMCparam,getDMCframe

#plot disable
showhist=False
showflowvec = False
showflowhsv = False
showthres = True
showofmag = True


def main(flist,params,verbose):
    camser,camparam = getcamparam(params['paramfn'])
    for f,s in zip(flist,camser):
        detfn = join(params['outdir'],f +'_detections.h5')
        if isfile(detfn):
            print('** overwriting existing ' + detfn)

        cparam = camparam[s]

        finf = loadvid(f,cparam,params,verbose)
#%% ingest parameters and preallocate
        twoframe = bool(cparam['twoframe'])
        dowiener = not isnan(cparam['wienernhood'])
        ofmethod = cparam['ofmethod'].lower()
        rawframeind = empty(finf['nframe'],dtype=uint32)
        rawlim = (cparam['cmin'], cparam['cmax'])
        xpix = finf['superx']; ypix = finf['supery']
        thresmode = cparam['thresholdmode'].lower()
        if ofmethod == 'hs':
            umat =   cv.CreateMat(ypix, xpix, cv.CV_32FC1)
            vmat =   cv.CreateMat(ypix, xpix, cv.CV_32FC1)
            cvref =  cv.CreateMat(ypix, xpix, cv.CV_8UC1)
            cvgray = cv.CreateMat(ypix, xpix, cv.CV_8UC1)

        with open(f, 'rb') as dfid:
            jfrm = 0
            #%% mag plots setup
            if showofmag:
                figom = figure(30)
                axom = figom.gca()
                hiom = axom.imshow(zeros((ypix,xpix)),vmin=0, vmax=10)

            for ifrm in finf['frameind']:
#%% load and filter
                if twoframe:
                    frameref = getDMCframe(dfid,ifrm,finf)[0]
                    #frameref = getDMCframe(dfid,ifrm,finf)[0]
                    if dowiener:
                        frameref = wiener(frameref,cparam['wienernhood'])
                    frameref = sixteen2eight(frameref, rawlim)

                fg,rfi = getDMCframe(dfid,ifrm+1,finf)
                if fg is None or rfi is None:
                    delete(rawframeind,s_[jfrm:])
                    break
                framegray,rawframeind[jfrm] = (fg, rfi)
                #framegray,rawframeind[jfrm] = (fg, rfi)

                if dowiener:
                    framegray = wiener(framegray,cparam['wienernhood'])
                framegray = sixteen2eight(framegray, rawlim)
#%% image histograms (to help verify proper scaling to uint8)
                if showhist:
                    figure(1).clf()
                    ax=figure(1).gca(); hist(fg.flatten(), bins=128, fc='w',ec='k', log=True)

                    figure(2).clf()
                    ax=figure(2).gca(); hist(framegray.flatten(), bins=128, fc='w',ec='k', log=True)
                    ax.set_xlim((0,255))
                    draw(); pause(0.1)

#%% compute optical flow
                if ofmethod == 'hs':
                    cvref = cv.fromarray(frameref)
                    cvgray = cv.fromarray(framegray)
                    #result is placed in u,v
                    # matlab vision.OpticalFlow has default maxiter=10, terminate=eps, smoothness=1
                    cv.CalcOpticalFlowHS(cvref, cvgray,
                                                False,
                                                umat, vmat,
                                                1.0,
                                                (cv.CV_TERMCRIT_ITER | cv.CV_TERMCRIT_EPS, 10, 0.0001))
                    flow = dstack((asarray(umat), asarray(vmat)))

 #                    http://docs.opencv.org/trunk/doc/py_tutorials/py_gui/py_drawing_functions/py_drawing_functions.html
                #    for i in range(0, xpix, flowskip):
                #        for j in range(0, ypix, flowskip):
                #            dx = int(cv.GetReal2D (umat, j, i))
                #            dy = int(cv.GetReal2D (vmat, j, i))
               #             cv2.line(desImageHS,(i, j),(i + dx, j + dy), (255, 0, 0), 1, cv2.CV_AA, 0)
                elif ofmethod == 'farneback':
                    flow = cv2.calcOpticalFlowFarneback(frameref, framegray,
                                                       pyr_scale=0.5,
                                                       levels=1,
                                                       winsize=3,
                                                       iterations=5,
                                                       poly_n = 3,
                                                       poly_sigma=1.5,
                                                       flags=1)
                else:
                    exit('*** OF method ' + ofmethod + ' not implemented')


#%% compute median and magnitude
                ofmag = hypot(flow[...,0], flow[...,1])
                medianflow = median(ofmag)
                thres = dothres(ofmag,medianflow,thresmode,cparam['ofthresmin'],cparam['ofthresmax'])
                despeck = cv2.medianBlur(thres,ksize=cparam['medfiltsize'])
#%% plotting in loop
                """
                http://docs.opencv.org/modules/highgui/doc/user_interface.html
                """
                if showflowvec:
                    cv2.imshow('flow vectors ', draw_flow(framegray,flow) )
                if showflowhsv:
                    cv2.imshow('flowHSV', draw_hsv(flow) )
                if showthres:
                    #cv2.imshow('flowMag', ofmag)

                    #figure(3).clf()
                    #fig3 = figure(3)
                    #ax3 = fig3.gca()
                    #hi= ax3.imshow(ofmag,cmap='jet',origin='bottom')
                    #fig3.colorbar(hi, ax=ax3)
                    hiom.set_data(ofmag)
                    draw(); pause(0.01)

                    cv2.imshow('thresholded ', thres)
                    cv2.imshow('despeck', despeck)

                if cv2.waitKey(1) == 27: # MANDATORY FOR PLOTTING TO WORK!
                    break

                jfrm+=1

        #ax = figure().gca()
        #ax.imshow(frameref,cmap = 'gray', origin='lower',vmin=cparam['cmin'],vmax=cparam['cmax'])

def dothres(ofmag,medianflow,thresmode,thmin,thmax):
    if thresmode == 'median':
        if medianflow>1e-6:  #median is scalar
            lowthres = thmin * medianflow #median is scalar!
            hithres = thmax * medianflow #median is scalar!
        else: #median ~ 0
            lowthres = 0
            hithres = inf

    elif thresmode == 'runningmean':
        exit('*** ' + thresmode + ' not yet implemented')
    else:
        exit('*** ' + thresmode + ' not yet implemented')
    """ threshold image by lowThres < abs(OptFlow) < highThres
    the low threshold helps elimate a lot of "false" OptFlow from camera
    noise
    the high threshold helps eliminate star "twinkling," which appears to
    make very large Optical Flow magnitude
    """

    """
    we multiply boolean by 255 because cv2.imshow expects only values on [0,255] and does not autoscale
    """
    return logical_and(ofmag < hithres, ofmag > lowthres).astype(uint8) * 255
    #return (ofmag > lowthres).astype(uint8) * 255

def draw_flow(img, flow, step=16):
    """ need to attribute where this came from """
    #scaleFact = 1. #arbitary factor to make flow visible
    canno = (0, 65535, 0)  # 65535 since it's 16-bit images
    h, w = img.shape[:2]
    y, x = mgrid[step//2:h:step, step//2:w:step].reshape(2,-1)
    fx, fy =  flow[y,x].T
    #create line endpoints
    lines = vstack([x, y, (x+fx), (y+fy)]).T.reshape(-1, 2, 2)
    lines = int32(lines + 0.5)
    #create image and draw
    vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    cv2.polylines(vis, lines, isClosed=False, color=canno, thickness=1, lineType=8)
    #set_trace()
    #draw filled green circles
    for (x1, y1), (x2, y2) in lines:
        cv2.circle(vis, center=(x1, y1), radius=1, color=canno, thickness=-1)
    return vis

def draw_hsv(flow):
    scaleFact = 10 #arbitary factor to make flow visible
    h, w = flow.shape[:2]
    fx, fy = scaleFact*flow[:,:,0], scaleFact*flow[:,:,1]
    ang = arctan2(fy, fx) + pi
    v = sqrt(fx*fx+fy*fy)
    hsv = zeros((h, w, 3), uint8)
    hsv[...,0] = ang*(180/pi/2)
    hsv[...,1] = 255
    hsv[...,2] = minimum(v*4, 255)
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    return bgr


def loadvid(fn,cparam,params,verbose):
    print('using ' + cparam['ofmethod'] + ' for ' + fn)
    print('minBlob='+str(cparam['minblobarea']) + ' maxBlob='+
          str(cparam['maxblobarea']) + ' maxNblob=' +
          str(cparam['maxblobcount']) )

    xypix=(cparam['xpix'],cparam['ypix'])
    xybin=(cparam['xbin'],cparam['ybin'])
    finf = getDMCparam(fn,xypix,xybin,params['framestep'],verbose)
    return finf

def getserialnum(flist):
    sn = []
    for f in flist:
        sn.append(int(search(r'(?<=CamSer)\d{3,6}',f).group()))
    return sn

def getcamparam(paramfn):
    camser = getserialnum(flist)
    camparam = read_excel(paramfn,index_col=0,header=0)
    return camser, camparam


if __name__=='__main__':
    from argparse import ArgumentParser
    p = ArgumentParser(description='detects aurora in raw video files')
    p.add_argument('indir',help='top directory over which to recursively find video files',type=str)
    p.add_argument('vidext',help='extension of raw video file',nargs='?',type=str,default='DMCdata')
    p.add_argument('-k','--step',help='frame step skip increment (default 10000)',type=int,default=10)
    p.add_argument('-o','--outdir',help='directory to put output files in',type=str,default=None)
    p.add_argument('--ms',help='keogram/montage step [1000] dont make it too small like 1 or output is as big as original file!',type=int,default=1000)
    p.add_argument('-c','--contrast',help='[low high] data numbers to bound video contrast',type=int,nargs=2,default=(None,None))
    p.add_argument('--rejectvid',help='reject raw video files with less than this many frames',type=int,default=10)
    p.add_argument('-r','--rejectdet',help='reject files that have fewer than this many detections',type=int,default=10)
    p.add_argument('--paramfn',help='parameter file for cameras',type=str,default='camparam.xlsx')
    p.add_argument('-v','--verbose',help='verbosity',action='store_true')
    p.add_argument('--profile',help='profile debug',action='store_true')
    a = p.parse_args()

    params = {'rejvid':a.rejectvid,'framestep':a.step,
              'montstep':a.ms,'clim':a.contrast,
              'paramfn':a.paramfn,'rejdet':a.rejectdet,'outdir':a.outdir}

    flist = walktree(a.indir,'*.' + a.vidext)

    if a.profile:
        import cProfile
        from profilerun import goCprofile
        profFN = 'profstats.pstats'
        print('saving profile results to ' + profFN)
        cProfile.run('main(flist,params,a.verbose)',profFN)
        goCprofile(profFN)
    else:
        main(flist,params,a.verbose)
        #show()
