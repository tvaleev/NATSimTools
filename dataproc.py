import os
import sys
import fileinput
import re
import random
import math
from operator import itemgetter, attrgetter
import subprocess
from optparse import OptionParser
import copy

import time
import argparse
from dateutil import parser as dparser

import numpy as np
import matplotlib.pyplot as plt

#
# Data processing here
#


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='NAT data processor.', formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--mean',           help='Graph main', required=False, default=False, action='store_true')
    parser.add_argument('file', action="store", nargs='+')
    args = parser.parse_args()
    
    keys  = []
    succ  = []
    mean  = []
    styles = ['--bx', '-.g2', ':.r', '--|k', ':m+', '--1c']
    
    for i, fname in enumerate(args.file):
        fh    = open(fname)
        dat   = fh.readlines()
        
        k, s, m = [], [], []
        strategy_name = ""
        for d in dat:
            d = str(d).strip()
            if d.startswith('#') or d.startswith('New'):
              strategy_name = d.split()[5].split("=")[1]
              continue
            
            arr = [float(x) for x in filter(None, d.split('|'))]
            if len(arr)==0: continue
            
            k.append(arr[0]) 
            s.append(arr[2])
            m.append(arr[3])
        keys.append(k)
        succ.append(s)
        mean.append(m)
        
        x = np.array(k)
        y = np.array(m if args.mean else s)
        
        tt = plt.plot(x, y, styles[i], label=strategy_name)
   
    if args.mean: plt.legend(loc=1)
    else:         plt.legend(loc=3)
    
    plt.xlim(-0.01, max(max(keys)) * 1.1)
    if args.mean: pass #plt.ylim(0.0,max(y)*1.1)
    else:         plt.ylim(0.0,1.1)
    
    
    plt.xlabel('$\lambda$')
    plt.ylabel('Mean step success' if args.mean else 'success rate [%]') #,rotation='horizontal')
    plt.grid(True)
    plt.show()       
