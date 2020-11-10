#
# NAT protocol simulation
#
#
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
from scipy.stats import binom
from scipy.stats import nbinom
from scipy.stats import norm
from scipy.stats import poisson
from scipy.stats import chisquare
import numpy as np
import matplotlib.pyplot as plt
import time
import argparse
from dateutil import parser as dparser
import calendar
import bisect
import heapq
from heapq import heappush, heappop
import resource
import gc

# Multiple plots
from mpl_toolkits.axes_grid1 import host_subplot
import mpl_toolkits.axisartist as AA
import pylab as P

# MLE distribution fitting with RPy - python binding for R
from rpy2.robjects import r
from rpy2.robjects import IntVector 
from rpy2.robjects.packages import importr
  
# Load the MASS library for distribution fitting
# See more at: http://thomas-cokelaer.info/blog/2011/08/fitting-distribution-by-combing-r-and-python/#sthash.TiVb9HpI.dpuf 
MASS = importr('MASS') 

def coe(x):
    return 1.0 / (0.163321 * math.log(64.2568 * x)) 

def charproc(i, m):
    return chr(  int(ord('a') + (  (float(i)/m)  *  ((ord('z')-ord('a')))   ))    )

# Fibonacchi list generator
def fibGenerator():
    '''
    Fibonacci sequence generator
    '''
    a, b = 0, 1
    yield 0
    while True:
        a, b = b, a + b
        yield a

def poissonProcGenerator(lmbd):
    '''
    Generates samples from Poisson NAT (inc +1) process generator
    '''
    x = np.random.poisson(lmbd)
    yield x
    while True:
        x += 1 + np.random.poisson(lmbd)
        yield x
            
def f7(seq):
    '''
    Removes duplicates from list while keeping the order
    '''
    seen = set()
    seen_add = seen.add
    return [ x for x in seq if x not in seen and not seen_add(x)]

def probRound(x):
    '''
    Probabilistic rounding of a number. If x=1.2 with 80% it will be rounded to 1, 20% to 2
    '''
    flr = math.floor(x)
    cel = math.ceil(x)
    
    if flr == cel: return x
    if random.random() <= (x-flr):
        return int(cel)
    else:
        return int(flr) 

def hashcode(s):
    '''
    Simple hashcode implementation for strings and integers.
    '''
    h = 0
    if isinstance(s, int ): return s
    for c in s:
        h = (31 * h + ord(c)) & 0xFFFFFFFF
    return ((h + 0x80000000) & 0xFFFFFFFF) - 0x80000000

def getMem():
    '''
    Returns current memory consumption in MB (float)
    '''
    return (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1000.0)

def graph(plt, x='Sample', y='Port number', loc=1):
    if loc!=-1:
        plt.legend(loc=loc)
    plt.xlabel('Sample')
    plt.ylabel('Port number') #,rotation='horizontal')
    plt.grid(True)
    plt.show()
    plt.close()

class Strategy(object):
    '''
    Base abstract class for NAT strategy
    '''
    def init(self, params=None):
        raise Exception("Not implemented yet...")
    def reset(self, nats=[], sim=None, params=None):
        self.init(None)
    def next(self, party, step):
        raise Exception("Not implemented yet...")   # return (srcPort, dstPort)
    def silent(self, time1, time2, lmbd):
        '''
        Tells to strategy how long did silent period take just before start and guessed lambda.
        '''
        pass

class Nat(object):
    '''
    Base abstract class for NAT allocation
    '''
    
    # timeout of a created connection in milliseconds
    timeout = 3*60*1000
    
    # port pool available for new allocations
    pool = None
    poolLen = 0
    
    def init(self, params=None):
        raise Exception("Not implemented yet...") 
    def reset(self):
        raise Exception("Not implemented yet...")
    def alloc(self, srcIP, srcPort, dstIP, dstPort, timeNow, timeAdd=None, refreshOnly=False):
        raise Exception("Not implemented yet...")
    def occupy(self, num, timeNow):
        raise Exception("Not implemented yet...")
    def freePorts(self):
        raise Exception("Not implemented yet...")
    def peekNext(self):
        raise Exception("Not implemented yet...")

class Quartet: #(object):
    '''
    SrcIP, srcPort, DstIP, DstPort.
    Was new-style class [http://docs.python.org/2/reference/datamodel.html#newstyle] - inheriting from object,
    but the execution was slower since it is uses very often. 
    '''
    srcIP=0
    srcPort=0
    dstIP=0
    dstPort=0
    
    def __init__(self, srcIP, srcPort, dstIP, dstPort):
        self.srcIP = srcIP
        self.srcPort = srcPort
        self.dstIP = dstIP
        self.dstPort = dstPort
    def __cmp__(self, other):
        if  other!=None \
            and self.srcIP == other.srcIP         \
            and self.srcPort == other.srcPort \
            and self.dstIP == other.dstIP     \
            and self.dstPort == other.dstPort: return 0
        else: return 1
    def __eq__(self, other):
        return self.__cmp__(other) == 0
    def __ne__(self, other):
        return self.__cmp__(other) != 0
    def __str__(self):
        return "%s:%05d --> %s:%05d" % (self.srcIP, self.srcPort, self.dstIP, self.dstPort)
    def __hash__(self):
        prime=31
        result=1
        result = prime * result + hashcode(self.srcIP)
        result = prime * result + int(self.srcPort)
        result = prime * result + hashcode(self.dstIP)
        result = prime * result + int(self.dstPort)
        return result
    
class SymmetricNat(Nat):
    '''
    Base class for symmetric NAT. 
    '''
    # allocation table for NAT; key = quartet; value = external port
    allocations = None 
    # port -> (quartet, expire time). Quartet may be null
    allocatedPorts = None
    # priority queue (heap), priority=expire time, tuple stored=(expire time, port, quartet)
    expireHeap = None
    
    # port is the key
    # port -> quartet, expire
    # quartet -> port
    
    def init(self, params=None):
        self.allocations = {}
        self.pool = list(range(1025, 65536))
        self.poolLen = len(self.pool)
        self.allocatedPorts = {}
        self.expireHeap = []
        
    def reset(self):
        self.allocations = {}
        self.allocatedPorts = {}
        self.expireHeap = []
    
    def nextPort(self):
        '''
        Uses port pool array and pointer to last allocated port to obtain next in the sequence.
        In case of a random allocation, randomly generates index to a pool and returns a port on the index.
        '''
        raise Exception("Not implemented yet... This class is abstract, you have to override this method in a subclass")
    
    def peekPort(self, prev=None):
        raise Exception("Not implemented yet... This class is abstract, you have to override this method in a subclass")
    
    def peekNext(self, timeNow):
        return self.nextFreePort(timeNow, True)
    
    def nextFreePort(self, timeNow, peek=False):
        '''
        Returns next free port in the sequence, takes existing associations into account and their expiration.
        Can be used to determine internal state of NAT, call does not affect internal state - reentrant function call.
        
        Runs in O(portNum) in worst case but in average it runs fast since to take it longer
        there would have to be a long block of allocated non-expired connections - this is implemented 
        as it would be in routers with incremental/random allocation without more complex state structures. 
        '''
        tries=0                                                # pool exhaustion check
        port=-1
        prev=None
        while tries <= self.poolLen:
            port,prev = self.peekPort(prev) # nextPort()       # linear port allocation rule here
            tries += 1                                         # check pool exhaustion
            if port in self.allocatedPorts:
                # next port is already allocated, what about timeout?
                tup = self.allocatedPorts[port]
                # check expiration first
                if (tup[1] + self.timeout) < timeNow:
                    if (tup[0] != None):
                            del self.allocations[tup[0]]       # expired -> delete from allocation table
                    del self.allocatedPorts[port]              # delete from allocation set
                    break                                      # slot is free now, can allocate
                else: continue                       # slot is in use, continue with search
            else: break                              # slot is free, assign
            
        # check if pool is exhausted - all ports are allocated currently
        if tries >= self.poolLen or port==-1:
            print("Port pool exhausted")
            raise Exception("Port pool exhausted")
        # reflect to internal NAT state - move iterator
        if peek==False:
            for i in range(0,tries): self.nextPort()
        # return resulting port, should not be -1
        return port
    
    def alloc(self, srcIP, srcPort, dstIP, dstPort, timeNow, timeAdd=None, refreshOnly=False):
        '''
        Basic allocation method for new connection
        '''
        q = Quartet(srcIP, srcPort, dstIP, dstPort)
        if timeAdd == None: timeAdd = timeNow
        
        # Check for existing allocation for a given quartet
        if q in self.allocations:
            port = self.allocations.get(q)
            # check expiration time, if a record is too old, it has to be removed from allocation table.
            tup = self.allocatedPorts.get(port)
            if (tup[1] + self.timeout) < timeNow:
                del self.allocatedPorts[port]     # delete from allocation set
                del self.allocations[q]           # expired -> delete from allocation table
            else:
                self.allocatedPorts[port] = (q, timeAdd)    # update last query access
                return port                                 # external port returned
        
        # If here -> not in allocation list, create a new allocation
        # New allocation is created only if desired
        if refreshOnly: return -1               
        
        # Get next free port from port pool that is free for use
        # time parameter passed will expire all existing connections
        port=self.nextFreePort(timeNow)
        # Create a new allocation
        self.allocatedPorts[port] = (q, timeAdd)
        self.allocations[q] = port
        # Add to heap
        heappush(self.expireHeap, (timeAdd, port, q))
        # Timeout all entries - internal table cleaning with probability 1:100
        if random.randint(0, 100) == 0:
            self.cleanHeap(timeNow)
        return port
    
    def occupy(self, num, timeNow):
        '''
        Simulates another connections created randomly
        '''
        for i in range(0, num):
            port = self.nextFreePort(timeNow)
            self.allocatedPorts[port] = (None, timeNow)
            heappush(self.expireHeap, (timeNow, port, None))   # Add to heap
        return 1
    
    def freePorts(self):
        return (self.poolLen - len(self.allocatedPorts))
    
    def cleanHeap(self, timeNow):
        '''
        Performs timeouting for all expired records - based on priority queue on access time
        '''
        while(len(self.expireHeap) > 0):
            cur, port, q = self.expireHeap[0]
            if (cur + self.timeout) >= timeNow: break   # if minimal is not expired -> return
            
            # Expired - does it exist in real allocation table?
            if port in self.allocatedPorts \
                and cur == self.allocatedPorts[port][1] \
                and q   == self.allocatedPorts[port][0]:
                
                # Record exists and is expired, thus clean it
                if (q != None):
                    del self.allocations[q]             # expired -> delete from allocation table
                del self.allocatedPorts[port]           # delete from allocation set
            # Remove element from queue
            heappop(self.expireHeap)
            #sys.stdout.write("X")
        #sys.stdout.flush()
    
    def trulyFreePorts(self, timeNow):
        self.cleanHeap(timeNow)
        return self.freePorts()

class SymmetricRandomNat(SymmetricNat):
    '''
    Symmetric NAT with random allocation function 
    '''
    def nextPort(self):
        '''
        Randomly generates index to a pool and returns a port on the index.
        '''
        return self.pool[random.randint(0, self.poolLen-1)]
    
    def peekPort(self, prev=None):
        return (self.nextPort(), None)
    
class SymmetricIncrementalNat(SymmetricNat):
    # index of last allocated port. Index to pool[]
    lastPort = 0
    
    def reset(self):
        super(SymmetricIncrementalNat, self).reset()
        self.lastPort = 0
        
    def nextPort(self):
        '''
        Uses port pool array and pointer to last allocated port to obtain next in the sequence.
        Modifies internal state. Acts like next() in iterators.
        ''' 
        self.lastPort = (self.lastPort + 1) % self.poolLen # linear port allocation rule here
        return self.pool[self.lastPort]                    # just a shortcut
    
    def peekPort(self, prev=None):
        '''
        Determines next free port without moving anything
        '''
        if prev==None: prev = self.lastPort
        tmp = (prev + 1) % self.poolLen # linear port allocation rule here
        return (self.pool[tmp], tmp)           # just a shortcut

class TheirStragegy(Strategy):
    '''
    Strategy of changing source port - published by other team
    '''
    delta = [900,900]
    startPos=[0,0]
    def init(self, params=None):
        pass    
    
    def silent(self, time1, time2, lmbd):
        #self.delta = [max(100, int(time1*lmbd*3)), max(100, int(time2*lmbd*3))]
        self.startPos = self.delta
        return self.delta
        
    def next(self, party, step):
        if party==0: return (step,self.delta[0])
        if party==1: return (step,self.delta[1])

class IJStragegy(Strategy):
    startPos=[1025,1025]
    b = []
    def init(self, params=None):
        pass
    def reset(self, nats=[], sim=None, params=[]):
        self.b = []
        c = 0
        for step in range(0, 1500):
            self.b.append(c)
            #c = NatSimulation.poisson(sim.lmbd, 2 * sim.portScanInterval * (step))
            #c += int(2.0 * sim.lmbd * sim.portScanInterval)
            c += 2
            #c += random.randint(0, 2)
            #print "step=",step, "smpl=", sim.lmbd * 2 * sim.portScanInterval * (step)
        
    def silent(self,  time1, time2, lmbd):
        #self.startPos=[int(lmbd * time1), int(lmbd * time2)]
        #self.startPos=[NatSimulation.poisson(lmbd, time1), NatSimulation.poisson(lmbd, time2)]
        return self.startPos
        
    def next(self, party, step):
        if party==0: return (1025, int(self.startPos[0]+self.b[step]))
        if party==1: return (1025, int(self.startPos[1]+self.b[step]))
    
class I2JStragegy(Strategy):
    '''
    Baby step, giant step strategy for low noise links. Works 100 % if lmbd*time is low.
    '''
    startPos=[1025,1025]
    def init(self, params=None):
        pass
    def silent(self,  time1, time2, lmbd):
        # Sets different starting point for algorithm than zero. Takes silent period
        # duration into account together with predicted workload to start algorithm
        # on a right place. It there are too many errors in the prediction (new connections)
        # small-step-big-step can have problems with cathing them up.
        #
        # Use expected value instead of a random sample as a starting point. E(X) = lmbd, X ~ Po(lmbd)
        # should be the central.
        #if lmbd >= 0.035:
        c = 0
        if lmbd >=  0.035: c=-10000.0*lmbd*lmbd + 950.0*lmbd - 21.0 
        if lmbd >= 0.05:   c=2
       
        # [tvaleev] why always zero? 
        #self.startPos=[int(0 * lmbd * time1), int(0 * lmbd * time2)]

        #print("Start pos----")
        #print(self.startPos)
        #self.startPos=[NatSimulation.poisson(lmbd, time1), NatSimulation.poisson(lmbd, time2)]
        return self.startPos
        
    def next(self, party, step):
        # This random stepping used to work
        #if party==0: return (0, int(self.startPos[0]+random.randint(1,3)*step )) #int(self.startPos[0]+step-150*(step/100)))
        #if party==1: return (0, int(self.startPos[0]+random.randint(1,5)*step )) #int(self.startPos[1]+2*step-230*(step/100)))
        
        if party==0: return (1025, int(self.startPos[0]+step )) #int(self.startPos[0]+step-150*(step/100)))
        if party==1: return (1025, int(self.startPos[0]+2*step )) #int(self.startPos[1]+2*step-230*(step/100)))

class SimpleStrategy(Strategy):
    '''
    Taking E[X_i] as a strategic move in step i, X is a probabilistic distribution over state in step i.
    If a process of new connections on NAT is Poisson process then the following formula holds (from simulation):
    E[X] = 1 + lambda * time
    '''
    startPos=[1025,1025]
    b = []
    ln = 0
    def init(self, params=None):
        pass
    def reset(self, nats=[], sim=None, params=[]):
        self.b = []
        for step in range(0, 1000):
            x = step * (1 + sim.lmbd * sim.portScanInterval)
            x = probRound(x)    # probabilistic rounding
            #x = round(x)
            self.b.append(x)
        self.ln = len(self.b)
        
    def silent(self,  time1, time2, lmbd):
        self.startPos=[int(lmbd * time1 + 1025), int(lmbd * time2 + 1025)]
        #self.startPos=[NatSimulation.poisson(lmbd, time1), NatSimulation.poisson(lmbd, time2)]
        return self.startPos
        
    def next(self, party, step):
        if party==0: return (1025, int(round(self.startPos[0]+self.b[min(step, self.ln-1)])))
        if party==1: return (1025, int(round(self.startPos[1]+self.b[min(step, self.ln-1)])))
        
class FiboStrategy(Strategy):
    '''
    Experimental Fibonacci strategy. Skip is variable, works well for some lambda & time.
    '''
    fibn = []
    b    = []
    startPos=[0,0]
    def init(self, params=None):
        self.fibn = []
        fib = fibGenerator()
        for n in range(22):
            self.fibn.append(next(fib))        
        for i in range(1, len(self.fibn)-1):
            for j in range(0, self.fibn[i-1]):
                #int(NatSimulation.poisson(0.1, 10 * (1+self.fibn[i+1] + j)    ))
                self.b.append(self.fibn[i+1] + j)
        #sys.exit(1)
    
    def silent(self,  time1, time2, lmbd):
        #self.startPos=[int(lmbd * time1), int(lmbd * time2)]
        #self.startPos=[NatSimulation.poisson(lmbd, time1), NatSimulation.poisson(lmbd, time2)]
        return self.startPos
    def next(self, party, step):
        if party==0: return (0, self.startPos[party] +   self.b[step])
        if party==1: return (0, self.startPos[party] + 2*self.b[step])
        return

class BinomialStrategy(Strategy):
    '''
    Uses assumption that probability distribution on ports in given time can
    be approximated by a binomial distribution. 
    
    This strategy turned out to not perform very well compared to SimpleStrategy
    which takes expected value of those distributions, not their samples.
    '''
    startPos=[0,0]
    nats = None
    sim  = None
    lmbd = 0.1
    dupl = False
    coef = 1.4773
    
    b = [[],[]]
    def init(self, params=None): 
        self.gen()
    
    def reset(self, nats=[], sim=None, params=[]):
        self.sim = sim
        
        if len(nats)==2: self.nats = nats
        if self.sim!=None: self.lmbd = sim.lmbd
        
        self.gen()
        pass
    
    def genPart(self, party):
        # lambda on both sides
        #lmbd = self.sim.lmbd if self.sim!=None else self.lmbd
        
        # port scan interval from simulation
        #t = self.sim.portScanInterval if self.sim != None else 10
        
        #ns = [ 3.987181708, 7.974882353, 12.004749541, 15.800322829, 20.390325999, 24.367375517, 28.668607075, 32.949910378, 37.506133178, 41.742443414, 44.800671135, 48.348672329, 52.821276380, 57.469178456, 61.474596007, 65.717914907, 70.423188639, 74.391720812, 80.017952700, 84.947892878, 88.203774099, 91.590841110, 95.205556545, 99.328019471, 102.335360039, 106.544729418, 110.719261929, 114.837410091, 118.764939628, 123.672331073, 127.524354350, 131.119204224, 134.643451575, 139.673688887, 143.690764059, 145.763838876, 148.316423448, 152.739506297, 156.567037203, 159.356910963, 162.939505412, 166.931804752, 170.402291816, 173.796693505, 178.723474588, 182.610998631, 187.522111612, 191.497479355, 194.844053936, 198.151948314, 202.685181458, 206.063161886, 210.433559408, 214.854480521, 219.355133828, 224.807999456, 228.268685200, 230.684844072, 233.924019960, 236.544276658, 240.074916053, 243.360578932, 246.887385964, 251.681038671, 253.527177951, 257.873032343, 262.067410122, 266.011990338, 268.756623153, 272.397092680, 276.681009062, 279.824882078, 283.003213645, 288.024411096, 290.480014345, 295.706772374, 299.195893027, 303.246913292, 306.367333651, 309.352070189, 314.518994099, 318.785078211, 321.262194392, 323.623806732, 326.734251317, 330.667318523, 337.515610709, 340.768483839, 345.380901282, 348.867650735, 350.765196322, 356.134619000, 359.947363053, 363.589257206, 365.320871305, 369.100701085, 372.607317584, 377.613479891, 380.082908277, 382.375454754, 386.603164439, 389.512921070, 394.881574325, 399.226888711, 404.170855236, 410.035440173, 413.076100259, 417.063025857, 421.989219390, 425.093581289, 428.450424438, 429.687481362, 434.261591594, 439.003389572, 441.120935893, 444.311236119, 447.969036565, 452.291434031, 456.400836442, 459.702616071, 463.792495328, 468.037806798, 471.520976639, 475.481177998, 480.745843961, 484.088470817, 488.169975740, 492.774751900, 496.569341452, 500.919194602, 504.889967968, 507.996624646, 511.918461434, 515.349733107, 521.488208018, 526.830310060, 529.555800595, 532.264874680, 536.493128373, 541.813100192, 547.660295512, 549.901891913, 555.627221888, 560.343337996, 566.570148054, 569.379898112, 574.689291433, 578.308625540, 582.058224648, 586.744790724, 591.564407412, 597.052031487, 600.776404480, 606.743258412, 609.819878816, 613.076859428, 617.377557416, 621.211435938, 623.926934463, 627.234168357, 632.164093673, 635.355861142, 640.128661053, 642.947152483, 649.402630037, 653.769544480, 657.489285630, 661.039070242, 666.938391550, 671.077316685, 676.640805238, 679.972289053, 683.760785852, 686.977626228, 689.637979296, 695.459963956, 700.990250275, 705.268515110, 710.816285684, 715.004435241, 717.008173908, 720.386968273, 724.901978835, 729.085516011, 732.165207246, 736.705305934, 740.510331316, 744.748695762, 747.826582497, 752.286011505, 755.377885485, 758.701583710, 760.705686270, 766.434018948, 767.998782148, 774.689022529, 778.999985506, 779.984268269, 780.996216317, 785.979715413, 791.940414230, 798.814193636, 800.725141224, 803.882039485, 808.976581370, 815.169135991, 818.259605275, 824.419928104, 829.259454537, 833.278741593, 840.135888264, 840.947197091, 844.927117976, 847.354808921, 852.254740348, 857.004946736, 861.011230287, 862.568913099, 866.398110813, 873.162345540, 876.836924868, 881.349769800, 885.974309246, 889.434423720, 893.013902864, 896.482193941, 900.092924177, 907.287603875, 914.401075464, 918.964752765, 921.992288059, 924.462911936, 930.588538795, 936.753131141, 938.937118688, 942.743991988, 943.942932634, 947.521877123, 953.931862626, 958.506144790, 964.164109983, 968.172619755, 972.739310051, 969.687957009, 974.316839601, 981.630981790, 985.598045695, 987.978599859, 993.241516089, 997.986439204, 1001.602926576, 1004.661467642, 1007.138116728, 1012.587549700, 1017.789953593, 1020.840023374, 1021.417777849, 1025.788677217, 1032.246996521, 1036.416071668, 1042.214057526, 1046.283082647, 1047.275840730, 1047.891744091, 1052.871207799, 1055.990217835, 1060.226687972, 1065.594811557, 1069.016534049, 1071.673128674, 1076.237926463, 1083.809755129, 1088.836667647, 1092.208220240, 1096.867886099, 1103.935196998, 1107.280340541, 1112.396143698, 1116.370064162, 1118.297137197, 1121.973464143, 1128.287327942, 1130.747151240, 1136.618741938, 1144.547820432, 1148.841259345, 1154.337531906, 1159.340151020, 1164.816821020, 1165.216439278, 1165.284452559, 1165.686748613, 1170.884540216, 1177.142170631, 1180.903799413, 1184.235344969, 1184.860166119, 1191.044450373, 1194.084433291, 1196.881537232, 1195.914056537, 1200.110966589, 1203.308485429, 1205.946284108, 1206.598497010, 1212.395306209, 1215.254289782, 1221.395742807, 1221.237104748, 1226.081618790, 1228.887751617, 1234.131021954, 1237.793961841, 1243.667236861, 1249.017506729, 1254.583133460, 1258.245545676, 1262.114247057, 1264.287701850, 1267.365466426, 1272.061818488, 1274.039711924, 1278.519927790, 1283.327344241, 1285.275360216, 1289.609408516, 1295.899257916, 1299.599128997, 1301.204892811, 1304.742602615, 1306.774359234, 1310.714514029, 1318.252170756, 1322.337964687, 1327.543876239, 1327.519325650, 1332.798881586, 1338.116899680, 1340.952671571, 1345.277405692, 1343.899510103, 1345.863269413, 1351.003999473, 1353.660891814, 1356.848773376, 1364.242829740, 1368.824840165, 1371.484388061, 1376.088690319, 1384.915536581, 1390.997543695, 1398.706567504, 1402.724404630, 1406.187146439, 1407.400441539, 1413.956942841, 1420.849625958, 1424.694628591, 1433.316353584, 1438.343460229, 1441.707474431, 1449.321090142, 1455.638336681, 1462.528947559, 1466.608444008, 1473.751774700, 1477.434563749, 1484.231056018, 1491.854035951, 1493.945686633, 1497.268593763, 1500.302428141, 1504.365195263, 1509.666996729, 1512.260488173, 1513.992579970, 1516.071802341, 1521.944140491, 1525.046455807, 1525.830585809, 1530.299746194, 1534.762050094, 1538.149471093, 1543.311152208, 1545.380514799, 1547.795618469, 1553.225259433, 1561.454394625, 1565.578930528, 1569.062005615, 1576.902593236, 1583.988463446, 1591.084666558, 1596.889889913, 1597.533551527, 1603.452562517, 1609.226971576, 1611.136441800, 1615.886983558, 1621.327760221, 1627.072468071, 1628.059490052, 1626.708734052, 1627.704839171, 1633.999115232, 1638.825161981, 1639.943180826, 1640.033789009, 1646.282749474, 1649.738676313, 1654.785503253, 1662.049514770, 1669.012483753, 1672.481950560, 1673.224897077, 1679.364318498, 1682.122511150, 1686.668500294, 1687.739671886, 1694.166112799, 1699.872618097, 1704.124255847, 1708.339834680, 1715.045103706, 1721.397118555, 1720.331966462, 1728.215754240, 1733.518903630, 1736.388522089, 1738.547180122, 1743.915905607, 1747.761473356, 1752.003615680, 1761.271333825, 1764.347706908, 1770.742338071, 1776.424693178, 1779.360850727, 1781.870413333, 1783.914178271, 1785.382242934, 1787.680934108, 1796.408992818, 1799.647989713, 1802.803114162, 1806.643533326, 1811.031777933, 1815.036474075, 1818.522075238, 1821.251807467, 1825.068656848, 1825.852587642, 1828.071222851, 1832.288384943, 1832.902529968, 1836.313313839, 1840.743859719, 1845.663218081, 1847.572265501, 1853.550012212, 1857.060485999, 1863.080580915, 1867.427802885, 1870.532628109, 1870.812682051, 1876.154354219, 1885.630224855, 1894.770491928, 1897.876806025, 1895.599960058, 1903.649643908, 1906.888218476, 1914.045066165, 1917.365414130, 1923.078010870, 1928.358698010, 1933.080114530, 1939.239217626, 1939.447664990, 1941.027810362, 1947.740529041, 1949.062100880, 1952.089317479, 1955.964188906, 1959.481783555, 1961.426767226, 1972.951639066, 1975.719621248, 1979.649957171, 1981.112969712, 1985.773607890, 1987.869819312, 1990.090878744, 1992.925415276, 1994.395581343, 1999.159001443, 2002.313427992, 2009.387560894, 2008.906205490, 2010.079502289, 2013.537889948, 2015.803510765, 2022.107712706, 2025.668564400, 2028.013600609, 2030.400019087, 2034.125017024, 2037.914841863, 2040.087289370, 2046.347145211, 2047.166509203, 2044.152770095, 2047.416161357, 2048.831185222, 2050.251911423, 2051.308213167, 2053.492873574, 2059.859288690, 2064.816827107, 2068.777482531, 2072.687857826, 2078.531511015, 2081.071992723, 2086.487329926, 2095.590113487, 2101.827366876, 2106.916311920, 2108.902742879, 2115.586533194, 2119.763531865, 2126.739382363, 2132.634293551, 2134.728595546, 2135.757791843, 2135.205170386, 2140.740688643, 2144.904187504, 2149.289169240, 2154.981132955, 2158.332144605, 2162.582743985, 2164.728295215, 2171.183204312, 2175.535420657, 2177.777556064, 2186.020810514, 2190.130653380, 2191.566316217, 2195.405356885, 2199.471560911, 2203.679078260, 2211.296611841, 2210.324621173, 2214.010754995, 2217.849658576, 2222.861799538, 2225.194269966, 2227.699779622, 2226.829399267, 2229.305948284, 2233.183657438, 2235.118070866, 2239.915883846, 2244.589426059, 2248.731190498, 2248.111131981, 2250.970675725, 2250.965425845, 2254.199162245, 2261.967596447, 2270.201790924, 2275.872184044, 2283.620489723, 2284.632576315, 2284.931341173, 2288.642996264, 2294.056002582, 2295.718242317, 2296.463693357, 2299.422969461, 2307.257697207, 2318.640850251, 2322.302137052, 2329.317288373, 2331.961337918, 2342.004214611, 2341.151774037, 2347.106839795, 2352.034659404, 2353.588144588, 2361.261414423, 2362.862501767, 2363.495510838, 2364.082278583, 2369.663850245, 2372.211682910, 2376.793072822, 2379.238884583, 2381.505023056, 2381.441002925, 2384.313648383, 2390.914929305, 2395.606486302, 2401.114525779, 2402.901881953, 2403.103713236, 2408.455008767, 2411.377461928, 2413.628511119, 2418.296665526, 2425.952673113, 2431.243096956, 2435.632635457, 2438.041855949, 2447.124968479, 2452.417098006, 2452.858072293, 2455.183073338, 2458.768922967, 2467.769732700, 2471.599831436, 2472.601691363, 2473.754730081, 2480.834303144, 2482.697263692, 2489.124067224, 2497.904996379, 2501.221387960, 2511.274323127, 2514.764209151, 2515.126028493, 2516.373546356, 2517.273435414, 2526.490050838, 2530.761927144, 2526.758342071, 2530.518742087, 2532.902112946, 2531.762791883, 2533.596262996, 2543.199719932, 2548.642209552, 2551.304317615, 2556.097174560, 2560.200159960, 2566.026412566, 2568.152241733, 2571.475818701, 2576.017552445, 2578.835507769, 2586.565095600, 2590.043688818, 2597.569293860, 2609.651122880, 2613.749205658, 2621.078429121, 2628.974219460, 2628.144879855, 2629.999310759, 2634.691624964, 2637.334705152, 2637.523760809, 2643.012728903, 2644.333920851, 2642.461277951, 2642.054079194, 2651.068973765, 2653.949697461, 2653.114718776, 2660.566456104, 2666.271641970, 2667.849777504, 2671.128745446, 2676.279300996, 2684.565213203, 2685.714610698, 2688.797313763, 2695.559968618, 2700.207584877, 2704.483388987, 2712.670778048, 2717.540408541, 2718.061140119, 2731.978347238, 2732.376963406, 2738.321379230, 2743.192573914, 2745.506027908, 2747.197886655, 2747.346497579, 2747.628482200, 2750.368729245, 2755.258841110, 2759.897757527, 2771.645200141, 2776.897645662, 2779.513552777, 2779.194392205, 2786.106657558, 2792.825499330, 2793.449870589, 2797.055710286, 2803.007204853, 2805.991436784, 2810.493663560, 2815.804227185, 2816.406949795, 2822.494634479, 2828.263734219, 2832.669692429, 2835.897050669, 2838.687765748, 2844.843900333, 2848.653637035, 2851.085482892, 2856.442641827, 2860.013543098, 2862.683260519, 2861.884368541, 2868.710851778, 2872.940438395, 2876.081903430, 2879.242090635, 2886.265678744, 2894.639944000, 2893.183811225, 2899.570473905, 2902.030835201, 2905.303975512, 2901.615035013, 2907.843998028, 2908.269657441, 2909.329382566, 2907.777648552, 2907.675821388, 2912.776747328, 2915.201395797, 2916.568037701, 2915.688419191, 2919.501114442, 2923.654090807, 2924.207255345, 2930.004535038, 2930.774127834, 2937.729692102, 2937.585038099, 2943.431349222, 2950.329049085, 2956.567075377, 2960.719549676, 2961.450145931, 2965.838822016, 2969.787606656, 2975.706971272, 2984.324587963, 2984.878581820, 2986.624657065, 2986.052396871, 2989.342995568, 2993.143558608, 3002.844952689, 3008.701409014, 3015.308956188, 3024.451561359, 3031.294499567, 3040.044819679, 3046.666237126, 3049.151713820, 3054.973904788, 3059.442916827, 3061.528538763, 3066.252238975, 3075.556332356, 3083.860912636, 3080.454512456, 3082.026835988, 3082.596435173, 3086.187064499, 3089.453177040, 3090.999905219, 3099.497556438, 3103.463302765, 3108.359552263, 3114.558534944, 3114.371335118, 3119.709948881, 3120.942650983, 3120.370089822, 3120.924366152, 3125.096011055, 3131.594608371, 3132.726619698, 3139.318311322, 3142.688556367, 3140.482475731, 3139.871083374, 3143.860058704, 3154.904504813, 3156.749529520, 3162.445680101, 3164.501108465, 3169.283536609, 3169.607177562, 3167.635164597, 3173.610835566, 3182.864210501, 3191.608399217, 3195.588751731, 3200.369009039, 3204.738843440, 3207.161623400, 3206.917125285, 3208.983858547, 3209.776444243, 3214.108756480, 3215.089937747, 3220.139723016, 3220.807280069, 3223.070851217, 3221.217856140, 3227.229359527, 3234.690723751, 3239.663939025, 3244.442078259, 3247.698617798, 3258.137751646, 3261.105943785, 3265.521799809, 3269.609170356, 3271.758547698, 3273.723886291, 3279.709420239, 3288.573898095, 3295.557305037, 3305.182338555, 3313.945683737, 3315.655688251, 3322.141334676, 3331.528550306, 3331.689511454, 3333.126758868, 3334.242164985, 3333.977000812, 3340.236866083, 3343.633150475, 3351.038543876, 3355.127320971, 3358.960225522, 3368.303042344, 3370.210178763, 3376.307210316, 3375.551902732, 3377.664509480, 3378.622055836, 3378.604016918, 3379.842306194, 3382.497245807, 3385.550296532, 3392.429082833, 3400.354512672, 3401.107119887, 3407.749841791, 3406.101282580, 3409.851452532, 3413.581025195, 3415.645884303, 3425.675796276, 3425.764640027, 3434.052296868, 3440.218666380, 3444.447467244, 3453.198163533, 3459.525374911, 3463.958283818, 3467.106066215, 3472.693288424, 3474.850999676, 3476.870283347, 3480.795205750, 3485.358025711, 3489.544737629, 3488.132213834, 3490.268892140, 3498.003385389, 3497.454923356, 3500.409077562, 3501.518793783, 3504.152672545, 3506.515938406, 3508.986197706, 3512.089068955, 3512.682432796, 3518.459965871, 3517.640472888, 3520.286813436, 3524.029878091, 3525.878649137, 3529.835527872, 3534.603442124, 3541.357294967, 3547.694329050, 3556.901516417, 3559.825111279, 3562.525646498, 3561.559684561, 3565.382540337, 3574.231355234, 3578.693171847, 3578.885822164, 3586.959187842, 3586.703984108, 3590.313589959, 3602.893686104, 3608.774631436, 3613.019603012, 3612.367471455, 3619.011918712, 3622.072223732, 3630.316607386, 3640.015494119, 3643.142955859, 3651.882135463, 3657.272788029, 3663.211803146, 3660.276217405, 3658.389953098, 3664.276155198, 3667.144888979, 3665.849065329, 3663.655600317, 3675.241341749, 3678.780648482, 3686.207705190, 3683.509617589, 3689.760180024, 3699.064555725, 3710.346297884, 3711.875317126, 3711.430283391, 3715.074792102, 3714.680279138, 3721.668206155, 3731.217831714, 3730.202884632, 3730.766257206, 3738.634310698, 3746.057468402, 3746.599157976, 3749.088345083, 3753.109685716, 3765.945743252, 3768.219014225, 3773.633370821, 3777.721977464, 3775.149730131, 3780.885925747, 3785.722358563, 3787.945211854, 3797.425825146, 3803.985173683, 3802.467378805, 3808.081769493, 3819.681696514, 3819.818664549, 3831.538667876, 3843.641035642, 3852.584809905, 3856.877224787, 3865.698444371, 3870.988682832, 3877.214445418, 3885.303877466, 3888.577555701, 3896.221475795, 3902.892659535, 3899.755023377, 3905.643681188, 3910.331224371, 3911.614826620, 3914.289911725, 3931.661523140, 3935.758422771, 3944.049832136, 3949.630485409, 3953.231210186, 3954.868803238, 3961.826582196, 3966.047270390, 3974.114039192, 3971.566782255, 3980.896811046, 3984.180911913, 3993.672524079, 3997.787077876, 3998.844356704, 4004.556069670, 4006.080400388, 4006.25558456, 4008.25558456, 4010 ]
        #ps = [ 0.500905187, 0.500446254, 0.499468979, 0.505837766, 0.490615010, 0.492149842, 0.487585601, 0.484717555, 0.479340270, 0.478510561, 0.490474795, 0.496497605, 0.492297835, 0.487506534, 0.488110568, 0.487002670, 0.483178917, 0.484389924, 0.475285842, 0.471077017, 0.476156496, 0.480362441, 0.483261709, 0.483203030, 0.488720614, 0.488382675, 0.487869943, 0.487959455, 0.488428657, 0.485288823, 0.486561177, 0.488425783, 0.490427119, 0.486970743, 0.487448170, 0.494246039, 0.499166568, 0.497827981, 0.498600481, 0.502388001, 0.503553756, 0.503597263, 0.505105883, 0.506742667, 0.503938278, 0.504149261, 0.501572850, 0.501538716, 0.503319439, 0.504951886, 0.503447757, 0.504889855, 0.503827433, 0.502784023, 0.501653178, 0.498467138, 0.499628759, 0.503017875, 0.504628811, 0.507439883, 0.508339655, 0.509602667, 0.510442441, 0.508662078, 0.512946190, 0.512027174, 0.511497404, 0.511429578, 0.513685573, 0.514161875, 0.513415794, 0.514807686, 0.516081772, 0.514067538, 0.516575298, 0.514257414, 0.514968967, 0.514643326, 0.515949263, 0.517427926, 0.515339306, 0.514714807, 0.517036872, 0.519342510, 0.520464565, 0.520293934, 0.515669778, 0.516634044, 0.515548484, 0.516156771, 0.519037242, 0.516855678, 0.516963365, 0.517256757, 0.520250046, 0.520331713, 0.520775065, 0.519111765, 0.521122091, 0.523203039, 0.522695670, 0.523909706, 0.521854686, 0.521227918, 0.519803438, 0.517226999, 0.518238649, 0.518043525, 0.516679076, 0.517606969, 0.518235920, 0.521398946, 0.520500096, 0.519450203, 0.521504153, 0.522206915, 0.522436108, 0.521886072, 0.521618238, 0.522214126, 0.522023539, 0.521542911, 0.521920789, 0.521819183, 0.520326495, 0.520789515, 0.520515010, 0.519726303, 0.519781586, 0.519294135, 0.519171338, 0.519961723, 0.519929676, 0.520332083, 0.518115263, 0.516680599, 0.517793969, 0.518894282, 0.518523696, 0.517070554, 0.515180674, 0.516723809, 0.514970449, 0.514209379, 0.512080280, 0.513055696, 0.511757578, 0.512058418, 0.512181063, 0.511495295, 0.510694011, 0.509330484, 0.509511022, 0.507818086, 0.508499658, 0.509032750, 0.508754159, 0.508807439, 0.509822517, 0.510294904, 0.509488918, 0.510075723, 0.509380410, 0.510290774, 0.508308382, 0.507988484, 0.508171627, 0.508465861, 0.506950873, 0.506778864, 0.505546809, 0.506007679, 0.506135782, 0.506642410, 0.507583124, 0.506222958, 0.505073786, 0.504858777, 0.503762234, 0.503606946, 0.505027715, 0.505409198, 0.505054491, 0.504884533, 0.505471165, 0.505047265, 0.505149198, 0.504986450, 0.505571491, 0.505241084, 0.505817032, 0.506214312, 0.507509549, 0.506331126, 0.507900024, 0.506092107, 0.505869586, 0.507798190, 0.509693634, 0.509022806, 0.507695772, 0.505826515, 0.507069129, 0.507564767, 0.506853238, 0.505459274, 0.505987094, 0.504660533, 0.504128832, 0.504112704, 0.502376587, 0.504261387, 0.504251303, 0.505147779, 0.504598191, 0.504110742, 0.504095864, 0.505527377, 0.505603134, 0.503959890, 0.504140493, 0.503842419, 0.503462906, 0.503755857, 0.503951393, 0.504245375, 0.504411031, 0.502616368, 0.500910391, 0.500582855, 0.501095081, 0.501923435, 0.500760304, 0.499615090, 0.500571541, 0.500651082, 0.502145398, 0.502371092, 0.501098264, 0.500787191, 0.499939787, 0.499932750, 0.499653910, 0.503285203, 0.502929006, 0.501234587, 0.501254241, 0.502069984, 0.501413394, 0.501024243, 0.501208000, 0.501660128, 0.502404180, 0.501655585, 0.501069399, 0.501524419, 0.503191751, 0.502980401, 0.501773511, 0.501698897, 0.500837228, 0.500777092, 0.502197396, 0.503802614, 0.503314552, 0.503716598, 0.503602679, 0.502938447, 0.503208120, 0.503823214, 0.503548320, 0.501884207, 0.501393475, 0.501685292, 0.501385634, 0.499984964, 0.500278908, 0.499767824, 0.499784093, 0.500704313, 0.500852309, 0.499822506, 0.500508004, 0.499688224, 0.497990726, 0.497856423, 0.497208991, 0.496764819, 0.496162821, 0.497701869, 0.499362365, 0.500927372, 0.500406641, 0.499438738, 0.499539251, 0.499837471, 0.501254255, 0.500323225, 0.500706301, 0.501192793, 0.503281149, 0.503176970, 0.503489012, 0.504055121, 0.505457782, 0.504680443, 0.505145964, 0.504257530, 0.505947615, 0.505567158, 0.506041662, 0.505518287, 0.505631971, 0.504856751, 0.504306622, 0.503667540, 0.503778934, 0.503805104, 0.504536744, 0.504894616, 0.504608652, 0.505390997, 0.505172728, 0.504829733, 0.505631883, 0.505499414, 0.504589841, 0.504713019, 0.505625827, 0.505797234, 0.506543915, 0.506538833, 0.505152971, 0.505098710, 0.504609461, 0.506131690, 0.505616121, 0.505097425, 0.505523882, 0.505379186, 0.507392402, 0.508151174, 0.507697091, 0.508144399, 0.508435290, 0.507140947, 0.506892905, 0.507368371, 0.507125743, 0.505347208, 0.504582775, 0.503237503, 0.503218307, 0.503395300, 0.504386939, 0.503448004, 0.502419951, 0.502459815, 0.500839189, 0.500479350, 0.500705041, 0.499439638, 0.498641855, 0.497668098, 0.497664392, 0.496597197, 0.496717633, 0.495798142, 0.494625065, 0.495275301, 0.495516705, 0.495863091, 0.495854067, 0.495445685, 0.495923292, 0.496677335, 0.497301248, 0.496709951, 0.497001253, 0.498055162, 0.497918138, 0.497764067, 0.497977352, 0.497619031, 0.498234184, 0.498742593, 0.498292051, 0.496951177, 0.496927229, 0.497086665, 0.495876792, 0.494917367, 0.493967428, 0.493425129, 0.494478504, 0.493893501, 0.493359740, 0.494018495, 0.493802109, 0.493386976, 0.492864095, 0.493799032, 0.495434114, 0.496347852, 0.495648249, 0.495392565, 0.496258535, 0.497445727, 0.496755737, 0.496921611, 0.496629320, 0.495671033, 0.494793064, 0.494976343, 0.495952099, 0.495339689, 0.495711813, 0.495553453, 0.496416488, 0.495723881, 0.495248403, 0.495190416, 0.495136847, 0.494371022, 0.493705021, 0.495175592, 0.494077431, 0.493739756, 0.494073641, 0.494607342, 0.494221423, 0.494283581, 0.494223295, 0.492756331, 0.493031729, 0.492383099, 0.491937544, 0.492242144, 0.492666579, 0.493222045, 0.493946886, 0.494433197, 0.493151617, 0.493372485, 0.493616742, 0.493673037, 0.493576430, 0.493590081, 0.493746880, 0.494105646, 0.494173190, 0.495057929, 0.495544697, 0.495482156, 0.496410467, 0.496584974, 0.496482982, 0.496241563, 0.496803734, 0.496287121, 0.496428742, 0.495895888, 0.495812582, 0.496056998, 0.497035972, 0.496696233, 0.495263381, 0.493923778, 0.494167481, 0.495806721, 0.494753960, 0.494968499, 0.494158898, 0.494346770, 0.493930457, 0.493612211, 0.493452492, 0.492918868, 0.493896905, 0.494524496, 0.493842062, 0.494537860, 0.494802667, 0.494838610, 0.494959743, 0.495488802, 0.493605307, 0.493924537, 0.493956720, 0.494604808, 0.494449718, 0.494935629, 0.495385015, 0.495692108, 0.496342957, 0.496157734, 0.496371041, 0.495628031, 0.496745740, 0.497456443, 0.497602158, 0.498033362, 0.497459554, 0.497563924, 0.497968554, 0.498373321, 0.498438587, 0.498505914, 0.498943553, 0.498392368, 0.499157834, 0.500874012, 0.501042836, 0.501676179, 0.502318273, 0.503030794, 0.503471238, 0.502898235, 0.502656403, 0.502661020, 0.502676269, 0.502228518, 0.502582901, 0.502233675, 0.501007422, 0.500464699, 0.500204300, 0.500674962, 0.500032962, 0.499992279, 0.499285530, 0.498841927, 0.499298226, 0.500000611, 0.501054800, 0.500701652, 0.500653039, 0.500564194, 0.500179971, 0.500330777, 0.500282638, 0.500706348, 0.500142870, 0.500064761, 0.500464153, 0.499491219, 0.499449655, 0.500036340, 0.500065647, 0.500046656, 0.499998485, 0.499184955, 0.500298006, 0.500377786, 0.500410474, 0.500181523, 0.500540027, 0.500876559, 0.501968763, 0.502293281, 0.502311844, 0.502768339, 0.502588605, 0.502430238, 0.502395131, 0.503409633, 0.503653918, 0.504541557, 0.504704384, 0.503866458, 0.502927539, 0.502554145, 0.501720144, 0.502383452, 0.503200941, 0.503260142, 0.502941253, 0.503449151, 0.504159157, 0.504368103, 0.503512981, 0.501908090, 0.501977405, 0.501316504, 0.501595794, 0.500293549, 0.501329821, 0.500905106, 0.500697979, 0.501226777, 0.500440397, 0.500946965, 0.501659172, 0.502380400, 0.502039393, 0.502338728, 0.502218394, 0.502546931, 0.502905343, 0.503748360, 0.503982771, 0.503429371, 0.503278233, 0.502967013, 0.503428213, 0.504242074, 0.503956933, 0.504180461, 0.504537626, 0.504397669, 0.503626066, 0.503354026, 0.503271627, 0.503587663, 0.502535431, 0.502270923, 0.502997713, 0.503336885, 0.503410056, 0.502391768, 0.502419601, 0.503023194, 0.503606030, 0.502977969, 0.503399999, 0.502915711, 0.501954078, 0.502087822, 0.500877737, 0.500969751, 0.501701539, 0.502244749, 0.502860509, 0.501817215, 0.501756798, 0.503343426, 0.503379714, 0.503695817, 0.504707236, 0.505141336, 0.504016570, 0.503724059, 0.503984880, 0.503831315, 0.503810608, 0.503445402, 0.503808606, 0.503935907, 0.503822499, 0.504047349, 0.503311284, 0.503405949, 0.502711748, 0.501147601, 0.501126656, 0.500487580, 0.499745182, 0.500679628, 0.501087204, 0.500952289, 0.501216246, 0.501938985, 0.501651992, 0.502157685, 0.503265047, 0.504104443, 0.503139003, 0.503344431, 0.504262176, 0.503601854, 0.503269201, 0.503723040, 0.503847447, 0.503629647, 0.502826526, 0.503353482, 0.503517314, 0.503004725, 0.502871189, 0.502819284, 0.502033867, 0.501867717, 0.502500911, 0.500668902, 0.501334120, 0.500964208, 0.500793934, 0.501094948, 0.501510942, 0.502198467, 0.502871043, 0.503102361, 0.502939245, 0.502817250, 0.501408910, 0.501177205, 0.501422560, 0.502197689, 0.501681871, 0.501194006, 0.501791715, 0.501853715, 0.501507238, 0.501688773, 0.501602341, 0.501363407, 0.501965172, 0.501594009, 0.501286773, 0.501210997, 0.501348312, 0.501552731, 0.501171822, 0.501201263, 0.501478405, 0.501235621, 0.501310633, 0.501550213, 0.502384658, 0.501883206, 0.501834977, 0.501982992, 0.502127558, 0.501595751, 0.500835485, 0.501785816, 0.501371363, 0.501639880, 0.501759614, 0.503086585, 0.502701246, 0.503301472, 0.503810675, 0.504758333, 0.505456279, 0.505248746, 0.505513205, 0.505962687, 0.506799489, 0.506817755, 0.506783413, 0.507370672, 0.507049181, 0.507605341, 0.507085320, 0.507785062, 0.507466023, 0.506962368, 0.506567706, 0.506531259, 0.507076036, 0.507000309, 0.506995381, 0.506660977, 0.505861261, 0.506436814, 0.506804428, 0.507567986, 0.507679113, 0.507704282, 0.506729793, 0.506405187, 0.505963277, 0.505094616, 0.504614316, 0.503820533, 0.503384316, 0.503634697, 0.503329995, 0.503245866, 0.503556175, 0.503431446, 0.502559223, 0.501850649, 0.503054596, 0.503445389, 0.504004995, 0.504058428, 0.504171227, 0.504560158, 0.503821013, 0.503824163, 0.503669081, 0.503303753, 0.503975291, 0.503748241, 0.504191450, 0.504920107, 0.505471846, 0.505435159, 0.505025521, 0.505479217, 0.505060221, 0.505152124, 0.506142929, 0.506879027, 0.506875169, 0.505737716, 0.506073600, 0.505800877, 0.506101134, 0.505961988, 0.506543464, 0.507489315, 0.507162372, 0.506314091, 0.505561710, 0.505553851, 0.505417218, 0.505344142, 0.505592730, 0.506252122, 0.506549697, 0.507048584, 0.506986516, 0.507450812, 0.507277864, 0.507790084, 0.508052809, 0.508961291, 0.508640514, 0.508085731, 0.507920646, 0.507786966, 0.507900515, 0.506888697, 0.507034064, 0.506955183, 0.506929457, 0.507203076, 0.507508714, 0.507192555, 0.506431861, 0.505966077, 0.505103389, 0.504374169, 0.504716942, 0.504340915, 0.503522505, 0.504100575, 0.504480184, 0.504919354, 0.505570374, 0.505220099, 0.505306271, 0.504788703, 0.504766716, 0.504782518, 0.503975853, 0.504284929, 0.503969720, 0.504671784, 0.504953288, 0.505404858, 0.506003246, 0.506414278, 0.506606296, 0.506734341, 0.506299692, 0.505707035, 0.506177824, 0.505780964, 0.506606897, 0.506634797, 0.506659425, 0.506939085, 0.506036386, 0.506613437, 0.505971036, 0.505647567, 0.505606376, 0.504903894, 0.504559733, 0.504493489, 0.504611041, 0.504373077, 0.504633263, 0.504916622, 0.504920886, 0.504836171, 0.504807914, 0.505575102, 0.505838505, 0.505298825, 0.505949509, 0.506097305, 0.506497981, 0.506684373, 0.506919983, 0.507135024, 0.507259402, 0.507744618, 0.507474184, 0.508162279, 0.508347159, 0.508379458, 0.508672810, 0.508662850, 0.508547120, 0.508143700, 0.507795496, 0.507037092, 0.507183624, 0.507357639, 0.508059659, 0.508081582, 0.507387637, 0.507314909, 0.507845372, 0.507263090, 0.507853675, 0.507900648, 0.506677399, 0.506407240, 0.506360718, 0.507010656, 0.506628506, 0.506746328, 0.506148196, 0.505346420, 0.505460319, 0.504800355, 0.504598893, 0.504334365, 0.505287385, 0.506095365, 0.505823994, 0.505969755, 0.506692001, 0.507538918, 0.506482657, 0.506539905, 0.506059764, 0.506968515, 0.506650869, 0.505916826, 0.504922897, 0.505247682, 0.505848489, 0.505890542, 0.506482943, 0.506066929, 0.505311640, 0.505990601, 0.506454136, 0.505923405, 0.505457222, 0.505923351, 0.506121869, 0.506112680, 0.504913753, 0.505140384, 0.504945927, 0.504930699, 0.505798905, 0.505564208, 0.505441503, 0.505666976, 0.504931732, 0.504589927, 0.505313737, 0.505098923, 0.504089595, 0.504593587, 0.503572420, 0.502508164, 0.501859478, 0.501825982, 0.501197139, 0.501026988, 0.500743157, 0.500211943, 0.500306493, 0.499830826, 0.499491575, 0.500404920, 0.500163445, 0.500075233, 0.500428975, 0.500592456, 0.498888724, 0.498874420, 0.498331939, 0.498136271, 0.498187709, 0.498487484, 0.498123115, 0.498095122, 0.497582047, 0.498405569, 0.497741814, 0.497828900, 0.497148324, 0.497135831, 0.497505035, 0.497296720, 0.497609983, 0.498082750, 0.5, 0.5 ]
        
        b    = []             # local array
        seen = set()          # duplicate check
        seen_add = seen.add
        
        for step in range(0, 1000):
            x = 1.5*step
            
            if step > 1000 and party==1:
                ex = 2.0*float(step)
                vx = float(step)/2.0
                p  = (ex-vx) / ex
                n  = ex / p
                
                x = np.random.binomial(n, p)  # (1+step*self.coef)
            
            # probabilistic rounding
            x = round(x)
            #x = probRound(x)
                            
            if True or self.dupl or (x not in seen and not seen_add(x)): 
                b.append(x)
        #print b
        return b
                
    def gen(self):
        self.b = [self.genPart(0), self.genPart(1)]
        
    def silent(self,  time1, time2, lmbd):
        self.lmbd = lmbd
        self.startPos=[int(lmbd * time1), int(lmbd * time2)] # expected value        
        return self.startPos
    
    def next(self, party, step):
        return (0, int(self.startPos[party] + self.b[party][min(step, len(self.b[party])-1)]))

class PoissonStrategy(Strategy):
    '''
    One of best strategies proposed. Relies on assumption of a Poisson process controlling 
    new connections occurrence. Simulates such random process as own strategy.
    
    For particular setting (lambda, time), coefficient is needed to be found. If coeficient
    is optimized, this strategy performs very well. 
    '''
    startPos=[1025,1025]
    nats = None
    sim  = None
    lmbd = 0.1
    dupl = False
    #coef = 1.4127
    coef = 1.1772
    
    b = [[],[]]
    def init(self, params=None): 
        #self.reset()
        self.gen()
        #print self.b[0], "len=", len(self.b[0])
    
    def reset(self, nats=[], sim=None, params=[]):
        self.sim = sim
        
        if len(nats)==2: self.nats = nats
        if self.sim!=None: self.lmbd = sim.lmbd
        
        self.gen()
        pass
    
    def coe(self, x):
        #return (4.43727 * math.exp(-2.156 * x))
        #return -0.928467 * math.log(0.255879 * x)       # this formula is OK for lambda [0.01, 0.07], t=10
        #return 1.0 / (0.161918 * math.log(65.7525 * x)) # OK for [0.04 , 0.15], t=10
        # experimental
        #return 1.0 / (0.165     * math.log(64 * x))  # OK for [0.04 , 0.15], t=10
        #return 6.18 / (math.log(x) + 4.28)  # OK for [0.04 , 0.15], t=10
    
        #
        # Usable functions below.
        # 
            
        # Alternative #1 - works good.
        return 1.0 / (0.163321 * math.log(64.2568 * x))  # OK for [0.04 , 0.15], t=10
    
        # Alternative #2 - quartic interpolation.
        # Very good fit for low intervals.
        return 1.0 / (0.172876+1.28162*x-1.41256*x*x+0.825093*x*x*x-0.184726*x*x*x*x)
        
    def genPart(self, party):
        # lambda on both sides
        lmbd = self.sim.lmbd if self.sim!=None else self.lmbd
        
        # port scan interval from simulation
        t = self.sim.portScanInterval if self.sim != None else 10
        
        x    = 0
        b    = []             # local array
        seen = set()          # duplicate check
        seen_add = seen.add
        
        for step in range(0, 3001):
            x = int(  np.random.poisson(lmbd * t * (1.0+step*self.coe(lmbd*t)))  )
            #x = int(  np.random.poisson(lmbd * t * (1.0+step*self.coef))  )
            #x = round(  np.random.poisson(float(step) * (1.0 + lmbd*t))  )
            
            #if party==0:
            #    x = round(step * (1 + lmbd*t))
            #if party==1:
            #    x = round(step * (1 + lmbd*t))
            
            # If unique element -> add to the b[]
            # Otherwise skip this <step> iteration - specialty. For different runs
            # different steps can be skipped. 
            if self.dupl or (x not in seen and not seen_add(x)): 
                b.append(x)
                if len(b) > 1100: break
            #b.append(x)
        #b = f7(b)
        return b
                
    def gen(self):
        self.b = [self.genPart(0), self.genPart(1)]
        
    def silent(self,  time1, time2, lmbd):
        self.lmbd = lmbd
        self.startPos=[int(lmbd * time1 + 1025), int(lmbd * time2 + 1025)] # expected value
        self.gen()
        #self.startPos=[NatSimulation.poisson(lmbd, time1), NatSimulation.poisson(lmbd, time2)]        
        return self.startPos
    
    def next(self, party, step):
        #return (0, int(self.startPos[party] + NatSimulation.poisson(self.lmbd, 10 * (1+step*1.77)    )))
        return (1025, int(self.startPos[party] + self.b[party][min(step, len(self.b[party])-1)]))
        
        #self.startPos[party] += 1+NatSimulation.poisson(self.lmbd, 10)#*(1+step*0.77))
        #return (0, int(self.startPos[party]))

def getStrategy(desc, verbose=0):
    '''
    Returns strategy according to string identifier
    '''
    strategy = PoissonStrategy()
    if desc == 'i2j':
        if verbose>0: print("I2J Strategy: ")
        strategy = I2JStragegy()
    elif desc == 'ij':
        if verbose>0: print("IJ strategy")
        strategy = IJStragegy()
    elif desc == 'fibo':
        if verbose>0: print("Fibonacci strategy")
        strategy = FiboStrategy()
    elif desc == 'their':
        if verbose>0: print("Their strategy")
        strategy = TheirStragegy()
    elif desc == 'poisson':
        if verbose>0: print("Poisson strategy")    
        strategy  = PoissonStrategy()
    elif desc == 'binom':
        if verbose>0: print("Binomial strategy")    
        strategy  = BinomialStrategy()
    elif desc == 'simple':
        if verbose>0: print("Simple strategy")    
        strategy  = SimpleStrategy()
    
    return strategy

def nfline2tuple(line):
    '''
    Translates nfdump line of a format "fmt:%%ts;%%td;%%pr;%%sa;%%sp;%%da;%%dp" to a tuple defined by the format
    '''
    tpl = [str(x).strip() for x in line.split(";")]
    tstart = tpl[0]
    tdur   = float(tpl[1])
    
    dtime = dparser.parse(tstart)              # Parse time from nfdump to datetime format
    startUtc = NatSimulation.dtimeToUtc(dtime) # convert date time string to UTC
    lastData = int(startUtc + round(tdur)) 
    tpl.append(startUtc)
    tpl.append(lastData)
    
    return (tpl, startUtc) 

class NfdumpAbstract:
    def deinit(self):
        pass
    def generator(self):
        pass

class NfdumpSorter(NfdumpAbstract):
    '''
    Generator for reading a nfdump file by nfdump program, sorted by time of netflow 
    start - sorting on the fly by heap algorithm.
    '''
    proc = None
    once = False
    tout = 300*1000
    def __init__(self, filename, filt=None, activeTimeout=300*1000):
        '''
        Initializes object for nfdumpSortedGenerator - creates a nfdump process 
        '''
        if self.once == True: raise Exception('Generator was not de-initialized, may be still running...')
        
        cmdLine = 'nfdump -q -r "%s" -o "fmt:%%ts;%%td;%%pr;%%sa;%%sp;%%da;%%dp" "%s"' % (filename, filt if filt!=None else "")
        print("nfdump command line used: %s" % cmdLine)
        
        self.once = True
        self.tout = activeTimeout
        self.proc = subprocess.Popen(cmdLine, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)
        print("Data loaded, going to process output...")
    
    def __enter__(self):
        return self
    
    def __exit__(self):
        self.deinit()
    
    def deinit(self):
        '''
        Kills subprocess if still exists
        '''
        if self.proc!=None:
            try:
                self.proc.kill()
            except Exception:
                pass
            pass
            self.proc = None
            self.once = False
        pass
    
    def generator(self):
        '''
        Generator object producing nfdump lines for a given filename for nfdump file. 
        Records are sorted by first seen time in this generator since nfdump records does not have to be sorted by default.
        (Probe stored records by its internal logic)
        
        Using heap to sort by maintaining some amount of records in memory in priority queue 
        and taking minimal element if have enough elements and sufficient precision. 
        '''    
        if self.once == False: raise Exception('Generator is not initialized...')
        
        cnt = 0
        isDead = False       # is producing program dead?
        buff   = []
        while(True):         # while there is something to run
            cnt += 1
            retcode = self.proc.poll() #returns None while subprocess is running
            line = self.proc.stdout.readline()
            
            # Flag determines whether queue sort is consistent with absolute sort w.r.t. whole data set.  
            isSorted = False
            # Flag determines whether there was some line added to the queue => new event read.
            lineAdded = False
            
            # Parse output format to tuple.
            # Partial sort by heap.
            #
            # Read line if there is something to read and process didn't finish
            # in previous round.
            if line!=None and len(line)>0 and isDead==False:
                tpl, startUtc = nfline2tuple(line)
                
                # Add record to the priority queue sorted by first seen time
                heappush(buff, (startUtc, tpl))
                lineAdded = True
                
                # If time difference between current element and minimal one in queue is 
                # greater than active timeout, we have probably enough data in queue
                # to be completely sorted w.r.t. whole data set since the biggest gap 
                # in nearly-sorted block is of size active timeout (probe added flow to file
                # when active timeout was expired). The next entry cannot be smaller. 
                # Just to be sure - require at least 10 000 elements in priority queue.
                if (startUtc - buff[0][0]) > self.tout and len(buff) >= 10000:
                    isSorted=True 
                    #print "Sorted event... cnt=%d, min=%s, curr=%s, diff=%s" % (cnt, buff[0][0], startUtc, (startUtc - buff[0][0]))

            # If program is dead skip the line parsing in the next iteration and set data 
            # in the priority queue as prepared to be processed.
            if retcode is not None: 
                isDead=True
                isSorted=True

            # If line was added and queue is not ready, then continue (still can read some data,
            # program is still running, so wait to fill the queue)
            if lineAdded and not isSorted and retcode is None: 
                if (cnt % 1000) == 0: 
                    sys.stdout.write('.')
                    sys.stdout.flush()
                continue
            
            # If there is nothing to process then we are done...
            if len(buff)==0:
                break
            
            tplpopped = heappop(buff)
            yield tplpopped
        
        # End of the input processing
        self.deinit()

class NfdumpReader(NfdumpAbstract):
    '''
    Generator for reading pre-processed NFdump file.
    
    Nfdump generator reads already sorted records in format "fmt:%%ts;%%td;%%pr;%%sa;%%sp;%%da;%%dp"
    Just reads the file and parses nfdump format to tuple.
    '''
    fo   = None
    once = False
    def __init__(self, filename):
        '''
        Initializes object for generator - opens nfdump file for reading 
        '''
        if self.once == True: raise Exception('Generator was not de-initialized, may be still running...')
        self.fo   = open(filename, "r+")
        self.once = True
    
    def __enter__(self):
        return self
    
    def __exit__(self):
        self.deinit()
    
    def deinit(self):
        '''
        Kills subprocess if still exists
        '''
        if self.fo!=None:
            try:
                self.fo.close()
            except Exception:
                pass
            pass
            self.fo   = None
            self.once = False
        pass
    
    def generator(self):
        '''
        Nfdump generator reads already sorted records in format "fmt:%%ts;%%td;%%pr;%%sa;%%sp;%%da;%%dp"
        Just reads the file and parses nfdump format to tuple.
        ''' 
        if self.once == False: raise Exception('Generator is not initialized...')
        
        while True:
            line = self.fo.readline()
            if not line:
                break
            tpl, startUtc = nfline2tuple(line)
            yield (startUtc, tpl)
            
        self.deinit()

class NatSimulation(object):
    
    # Lambda for Poisson process generator. Time unit = 1 ms
    # Intuitively: represents rate of creation of new events in given period.
    #  Average number of arrivals per unit time is lambda.        [analysis&synthesis]
    #  The expected length of interarrival intervals is 1/lambda. [analysis&synthesis]
    #  Interarrival intervals are independent and distributed exponentially with parameter lambda. [analysis&synthesis]
    #
    # @see http://www.columbia.edu/~ks20/4703-Sigman/4703-07-Notes-PP-NSPP.pdf
    # @see http://filebox.vt.edu/users/pasupath/papers/poisson_streams.pdf
    # @see http://www.math.wsu.edu/faculty/genz/416/lect/l05-45.pdf
    # @see http://preshing.com/20111007/how-to-generate-random-timings-for-a-poisson-process/
    lmbd = 0.01
    
    # Number of miliseconds for silent period to take [ms].
    # Based on basic ping / round trip time it takes to communicate 
    # IP with another peer 
    silentPeriodBase = 1000
    
    # Lambda for Pois(lmbd) for silent period variability.
    # Silent period time = silentPeriodBase + Pois(lmbd) [ms]
    silentPeriodlmbd = 100
    
    # Number of rounds for simulation
    simulationRounds = 1000
    
    # how many rounds has fast simulation? Fast is extended to deep simulation if it has
    # more than 50% probability of success
    simulationRoundsFast = 100  
    
    # Number of errors that are handled by algorithm 
    errors = 1000
    
    # number of milliseconds between consecutive port scans
    portScanInterval = 10
    
    # number of connections to establish
    numCon = 1
    
    # very compact output
    compact=True
    
    # draw dot if 1 round
    dot=1
    ascii=1
    
    # process in generator
    proc=None
    
    @staticmethod
    def poisson(lmbd, t):
        '''
        Uses Numpy package to take sample from poisson distribution
        '''
        return int(np.random.poisson(lmbd*t))
    
    @staticmethod
    def uniform(lmbd, t):
        return random.random() * lmbd * t
    
    @staticmethod
    def poissonSample(lmbd, t):
        '''
        Generates number of events in Poisson process in time [0, t]
        source: http://www.math.wsu.edu/faculty/genz/416/lect/l05-45.pdf
        '''
        u = random.random()
        N = 0
        p = math.exp(-lmbd * t)
        F = p
        while u > F:
            N = N+1
            p = lmbd*t*p/N 
            F = F + p
        return N
    
    def poissonCDF(self, lmbd, x):
        '''
        Returns P(X <= x), X~Poisson(lmbd)
        
        P(X <= x) = e^{-lambda} * \sum_{i=0}^{k}{ \frac{lambda^i}{i!} }
        '''
        F = 1
        res = 0
        for i in range(0, x+1):
            res = res + (math.pow(lmbd, i) / F)
            F = F * (i+1)
        return (math.exp(-lmbd) * res) 
    
    def getNumOfNewConnections(self, tim):
        '''
        Simple wrapper for poission. Returns number of new connections
        created. It is assumed they are distributed according to Poisson distribution.
        '''
        return int(np.random.poisson(self.lmbd*tim))
    
    def poissonSimulate(self, T):
        '''
        Simulates Poisson process with arrival times
        source: http://www.columbia.edu/~ks20/4703-Sigman/4703-07-Notes-PP-NSPP.pdf
        '''
        t = 0.0
        N = 0
        while t <= T:
            # U ~ U(0,1), uniform distribution
            U = random.random()
            
            # next time of the event, exponential distribution
            t = t + (-(1/self.lmbd) * math.log(U))
            if (t > T): return N
        
            # increment the event counter
            N = N + 1
            print("New event will occur: " + str(t) + ("; now events: %02d" % N)) 
        
        return N
  
    def coefFinder(self, natA, natB, strategy, baseStep=0.10, start=0.1, maxc=15.0, epsdiff=0.25, maxp_acc=0.1, depth=0):
        '''
        Finds coefficient that maximizes probability of establishing connection for Poisson strategy
        '''
        
        probs = {}
        curc = start
        
        maxp   = 0.0
        maxcur = curc
        resm   = None
        
        minp   = 1.0
        mincur = curc
        
        bpoint = curc
        
        # Scanning with small step
        # TODO: if scanning step is too big and it is not possible to find the peak
        # in probability function step has to be smaller and interval re-scanned to
        # find desired maximum. 
        while curc < maxc:
            strategy.coef = curc    # sets current coefficient to strategy
            
            sys.stdout.write(" curc=%03.4f; " % curc)
            res = self.simulation(natA, natB, strategy)
            probs[curc] = res[0]
            
            if res[0] > maxp:
                maxp = res[0]
                maxcur = curc
                resm   = res
                
            if res[0] < minp:
                minp = res[0]
                mincur = curc
            
            # check if we are performing worse than before
            if maxp >= maxp_acc and maxp > res[0] and abs(maxp-res[0]) >= epsdiff:
                print("; We are getting worse...; ")
                bpoint = curc
                break
            # too good solution
            if maxp >= 0.99: break
            
            curc += baseStep    # increment current coefficient in strategy to the next round        
        print(probs)
        
        # Coefficient binary finding, maximum should be somewhere in the middle
        #print self.coefFinderInterval(natA, natB, strategy, bpoint-3*baseStep, bpoint, 0)
        
        # recursive call on this function - try finer step
        if maxp > 0.99 or depth >= 3:
            print("Ending recursion; max=%03.4f bp=%03.4f" % (maxp, maxcur))
            return (maxp, curc, resm[2] if resm != None else '0') 
        else:
            return self.coefFinder(natA, natB, strategy, baseStep/10.0, maxcur-2*baseStep, maxcur+2*baseStep, epsdiff/1.0, maxp, depth+1)
        
        pass
  
    def coefFinderInterval(self, natA, natB, strategy, cl, cr, step=0, prec=0.001):
        '''
        Recursive binary search for finding coefficient that maximizes probability of successful connection establishment
        ''' 
        eps = 100 # search epsilon, accuracy, 100ms
        t   = 0
        
        stepNull = step==0
        cc       = 0
        while cl < cr and (cr - cl) > 0.0001:
            if stepNull: step=(cr-cl) / 20.0
            cc = cl + (cr-cl) / 2.0

            # mid-1
            strategy.coef = cc-step
            sys.stdout.write("c-1 [%02.03f, %02.03f] curc=%03.4f; " % (cl, cr, strategy.coef))
            res_cm = self.simulation(natA, natB, strategy)
            
            # mid
            strategy.coef = cc
            sys.stdout.write("c   [%02.03f, %02.03f] curc=%03.4f; " % (cl, cr, strategy.coef))
            res_c = self.simulation(natA, natB, strategy)
            
            #mid+1
            strategy.coef = cc+step
            sys.stdout.write("c+1 [%02.03f, %02.03f] curc=%03.4f; " % (cl, cr, strategy.coef))
            res_cp = self.simulation(natA, natB, strategy)
            
            print("")
            sys.stdout.flush()
            
            # decision which path to take
            if res_cm[0] >= 0.99:
                return cc-step
            elif res_cp[0] >= 0.99:
                return cc+step
            elif res_c[0] >= 0.99:
                return cc
            elif res_cm[0] <= res_c[0] and res_c[0] >= res_cp[0]: # middle is peak; could return already but we might obtain better peak by "zooming"
                print("shrinking interval")
                cl = cl + (cr-cl) / 4.0
                cr = cr - (cr-cl) / 4.0
            elif res_cm[0] >= res_c[0] and res_c[0] <= res_cp[0]: # middle is low-peak;
                if res_cm[0] >= res_cp[0]:   # if left side is bigger
                    print("lowpeak, going left...")
                    cr = cc-step
                else:                       # right side is bigger
                    print("lowpeak, going right...") 
                    cl = cc+step
            elif res_cm[0] >= res_c[0]:
                print("going left...")
                cr = cc-step
            elif res_cp[0] >= res_c[0]:
                print("going right...")
                cl = cc+step
            else:
                print("dafuq?")
                return cc   
        return cc
    
    @staticmethod
    def dtimeToUtc(dtime):
        '''
        Converts datetime to unix time stamp
        '''
        return int((calendar.timegm(dtime.utctimetuple()) * 1000) + (dtime.microsecond/1000))
       
    @staticmethod
    def nfline2tuple(line):
        '''
        Translates nfdump line of a format "fmt:%%ts;%%td;%%pr;%%sa;%%sp;%%da;%%dp" to a tuple defined by the format
        '''
        return nfline2tuple(line) 
       
    def nfdumpSampleGenerator(self, natA, nfgen, homeNet='147.250.',
                              T=10, sampleSize=1000, recStartSkip=5000, recEachSkip=0, 
                              sampleEachSkip=0, maxBlockSize=-1, activeTimeout = 300*1000):
        '''
        Simulates network traffic using provided nfdump file and simulates NAT. 
        Generate sequence of a new conenctions allocated, sampling each T milliseconds.
        
        natA              = nat where to perform connection requests, can be reseted multiple times 
                            in this method. This instance should be dedicated just for this method. 
        T                 = milliseconds sampling time of NAT state (port), each sample is done after this period
        sampleSize        = number of samples (port numbers returned), number of windows of size T milliseconds.
        recStartSkip      = number of records to skip from the beginning of the file
        recEachSkip       = number of records to skip after the one taken
        sampleEachSkip    = number of records
        maxBlockSize      = number of blocks to process (1 block = sampleSize samples)
        '''
        #
        # Run NFdump and read line by line
        #
        cnt = 0

        lastSampleTime = 0
        lastSamplePort = 0
        lastStart = -1
        lastFree = natA.poolLen
        samplesRes = []         # sampling new connections (curr port - last port)
        samplePort = []         # sampling port numbers - whole process
        curTestSize=0
        curBlock=0
        natA.reset()
        
        recCurSkipCnt = 0       # number of records designated for skipping 
        
        # iterate over lines
        for tplpopped in nfgen:
            cnt += 1
            if tplpopped == None or tplpopped[1] == None \
                or tplpopped[1][0] == None or tplpopped[1][1] == None or tplpopped[1][2] == None \
                or tplpopped[1][3] == None or tplpopped[1][4] == None or tplpopped[1][5] == None \
                or tplpopped[1][6] == None or tplpopped[1][7] == None or tplpopped[1][8] == None:
                continue
            
            # Sample skip - beginning of the file may be non-ideal (already opened connections, ...)
            if cnt < recStartSkip:
                if (cnt % 10000) == 0: 
                    sys.stdout.write('s')
                    sys.stdout.flush()
                continue
            
            tstart,tdur,proto,srcIP,srcPort,dstIP,dstPort,startUtc,lastData = tplpopped[1]
            fromHome = srcIP.startswith(homeNet)    # Is connection made from our network?
            
            # Skiping on record basis
            if recEachSkip >= 1:
                recCurSkipCnt+=1
                if recCurSkipCnt <= recEachSkip: continue
                # No skip -> reset counter
                recCurSkipCnt = 0
            
            #print tplpopped[1], fromHome
            # sample NAT state each X time units
            if (startUtc/(T) > lastSampleTime):
                # Get next port that would be allocated in this time
                lastPort = natA.peekNext(startUtc)
                # How many connections were made since last sample?
                curSampleConn = lastPort - lastSamplePort if lastSamplePort <= lastPort else ((natA.poolLen-lastSamplePort) + lastPort)
                # Initialize observation start if not start
                if lastStart==-1: lastStart  = 0
                else:             
                    newTimeBlocks = startUtc/(T) - lastSampleTime
                    if newTimeBlocks>1: # has to fill gaps where no event happened -> 0
                        for tmpi in range(lastStart,lastStart+newTimeBlocks-1):
                            #sys.stdout.write('g')
                            samplesRes.append(0)
                            #print "Sample: time=%s; utc=%s; new connections=%d, lastPortSampled=%d, curPortSampled=%d GAP" % (tmpi*self.portScanInterval, startUtc, 0, lastSamplePort, lastPort)
                    
                    lastStart += newTimeBlocks
                #print "Sample: time=%s; utc=%s; new connections=%d, lastPortSampled=%d, curPortSampled=%d" % (lastStart*self.portScanInterval, startUtc, curSampleConn, lastSamplePort, lastPort)
                #sys.stdout.write('x')
                #sys.stdout.flush()
                samplesRes.append(curSampleConn)
                samplePort.append(lastPort)
                
                lastSamplePort = lastPort
                lastSampleTime = startUtc/(T)
                
                # collected samples
                curTestSize = len(samplesRes)
            pass
            
            # Allocate to NAT
            extPort = -1
            if fromHome:
                extPort = natA.alloc(srcIP, srcPort, dstIP, dstPort, startUtc, lastData, False)
                pass
            else:   # refresh existing connection only - packet from the outside
                extPort = natA.alloc(dstIP, dstPort, srcIP, srcPort, startUtc, lastData, True)
                pass
            
            if False and (cnt%1000)==0:
                nowFreePorts = natA.trulyFreePorts(startUtc)
                newConn      = lastFree - nowFreePorts
                lastFree     = nowFreePorts
                print("Hah, extPort=%s, free=%s, newCon=%s, time=%s, srchome=%s\n" % (extPort, nowFreePorts, newConn, startUtc, fromHome))
            
            if curTestSize<sampleSize: 
                continue
            
            yield samplesRes
            
            # State reset for next iteration
            curTestSize = 0
            lastSampleTime = 0
            lastSamplePort = 0
            recCurSkipCnt  = 0
            lastStart = -1
            lastFree = natA.poolLen
            samplesRes = []
            natA.reset()
            curBlock+=1
            
            if maxBlockSize > 0 and curBlock >= maxBlockSize: break
            
        yield samplesRes
        pass
    
    def nfdumpDistribution(self, natA, filename=None, processedNfdump=None, homeNet='', filt=None, drawHist=True, sampleSize = 500, maxBlock=-1, skip=0, fileOut=None):
        '''
        Reads nfdump file with given filter and simulates NAT
        
        sampleSize - number of NAT port samples in one block
        '''
        
        #
        # Run NFdump and read line by line
        #
        activeTimeout = 300*1000 # active timeout = time after a long lived flow is ended and written to a netflow file
        samplesRes = []          # sampling new connections (curr port - last port)
        sampleSkip = 5000
        curTestSize=0
        curBlock=0
        statRes  =[]
        statAccum=[]
        statDesc =[]
        natA.reset()
        fileDesc = 't%04d_s%05d_sk%05d' % (self.portScanInterval, sampleSize, sampleSkip)
        
        # Prepare nfdump record generator.
        nfdumpObj       = None
        nfdumpGenerator = None
        if processedNfdump!=None:
            nfdumpObj = NfdumpReader(processedNfdump)
            nfdumpGenerator = nfdumpObj.generator()
        else:
            nfdumpObj = NfdumpSorter(filename, filt, activeTimeout)
            nfdumpGenerator = nfdumpObj.generator()
        
        # generates samples of NAT process w.r.t. new connections.
        print("Starting sampling; sampleSize=%04d; sampleSkip=%04d; maxBlock=%04d; T=%03d" % (sampleSize, sampleSkip, maxBlock, self.portScanInterval))
        nfgen = self.nfdumpSampleGenerator(natA, nfdumpGenerator, homeNet, self.portScanInterval, 
                                           sampleSize, sampleSkip, 0, maxBlock, activeTimeout)
        
        f = None
        if fileOut != None and len(fileOut)>0:
            f = open(fileOut + ("_s%04d_sk%04d_t%04d.txt" % (sampleSize, sampleSkip, self.portScanInterval)), 'a+')
        
        # iterate over new connection count samples
        for samplesRes in nfgen:
            # Process & analyze data
            maxE = max(samplesRes)+1
            curBlock+=1
            curTestSize = len(samplesRes)
            if (curBlock-1) < skip: continue
            
            # Convert list of port numbers in each sample to frequency distribution
            distrib = [0] * (maxE)
            sampleSizeR=len(samplesRes)
            for i in samplesRes: 
                distrib[i]+=1
            
            ssum, ex, var, stdev = self.calcPortDistribInfo(distrib, sampleSizeR)
            files = [('distrib/nfdump_' + fileDesc + ("_%04d" % curBlock) + tmpf) for tmpf in ['.pdf', '.png', '.svg']]
                        
            print("Sampling done... max=%d, sampleSize=%d target=%d sum=%d" % (maxE, curTestSize, sampleSize, sum(distrib)))

            # Negative binomial try - wise binning disabled.
            (chi, pval, tmp_n, tmp_p, m2) = self.goodMatchNegativeBinomial(ex, var, distrib, maxE, sampleSizeR, False, wiseBinning=False)
            print("Chi-Squared test on match with NB(%04d, %04.4f): Chi: %04.4f, p-value=%01.25f; alpha=0.05; hypothesis %s" % \
                (tmp_n, tmp_p, chi, pval, "is REJECTED" if pval < 0.05 else "holds"))
            
            print("\nBlock=%04d, Distribution: " % curBlock, distrib) 
            sres = self.histAndStatisticsPortDistrib(distrib, sampleSizeR, maxE, files, drawHist=drawHist)
            statRes.append(sres)
            statAccum.append(sres['distrib'])
            statDesc.append((ex,var))
            
            print("=" * 180)
            
            #
            # Data output, each line = one distrib
            #
            if f!=None:
                # Write basic sample info.
                line = 'S|%d|%01.3f|%01.3f|%d' % (curBlock, sres['ex'], sres['var'], sres['ssum'])
                f.write(line + "\n")
                
                # Distribution fitting and hypothesis testing.
                for did, dist in enumerate(sres['distrib']):
                    line = '    D|%d|%01.18f|%03.3f|%03.8f|%s' % \
                        (did, dist['pval'], dist['chi'], dist['r2'], "|".join([('%03.5f' % x) for x in dist['par']]))
                    f.write(line + "\n")
                pass
            
                # Write whole sample for further statistical processing
                line = '    R|' + "|".join([('%d' % x) for x in samplesRes])
                f.write(line + "\n")
                f.flush()
            
            # State reset for next iteration
            curTestSize = 0
            if maxBlock > 0 and curBlock >= maxBlock: break
        
        #
        # Final data processing, main loop finished.
        #
        
        # Close protocol file, if any
        if f!=None:
            try: f.close()
            except Exception: pass
        
        # Force generator de-initialization.
        nfdumpObj.deinit()
        natA.reset()
        
        # Evalueate statistical resutls.
        if (len(statAccum)==0): return
        hypotheses = np.array([0]  * len(statAccum[0]))     # passes/not passed test
        pvals      = [[] for i in range(len(statAccum[0]))] # p-values array
        chisq      = [[] for i in range(len(statAccum[0]))] # chi-sqiared values array
        pvalsK     = [[] for i in range(len(statAccum[0]))] # p-values array, keys
        chisqK     = [[] for i in range(len(statAccum[0]))] # chi-sqiared values array, keys
        for k, s in enumerate(statAccum):
            hypotheses += np.array( [(1 if (t['pval'] >= 0.05 and not np.isnan(t['pval'])) else 0) for t in s] )
            for i,t in enumerate(s): 
                if t['pval'] != 0 and not np.isnan(t['pval']): 
                    pvals[i].append(t['pval'])
                    pvalsK[i].append(k)
                if t['chi']  != 0 and not np.isnan(t['chi']) : 
                    chisq[i].append(t['chi'])
                    chisqK[i].append(k)
        
        print("Hypothesis tests results (total=%d) " % curBlock, hypotheses)
        print("Median p-value: ", [np.median(s) for s in pvals])
        print("Median chi-squared value: ", [np.median(s) for s in chisq])
        return {'n': curBlock,      'h': hypotheses, 
                'pv': pvals,        'pk': pvalsK, 
                'cv': chisq,        'ck': chisqK, 
                'st': statAccum,    'sd': statDesc,
                'sr': statRes}
         
    def simulationCore(self, natSamples, strategies, nats, stopOnFirstMatch = False):
        '''
        Core of simulation algorithm, simulates one round of an algorithm with given strategy, 
        NAT connection samples and so on...
        '''
        
        # number of iterations to do
        iters = min(len(natSamples[0]), len(natSamples[1]))
        
        mapA  = [{}, {}]                # mapping of the current port to index
        scanA = [set([]), set([])]      # list of a tuple (assigned port, destination port)
        portsA = [set([]), set([])]     # set of an allocated ports
        totalLagA = [0, 0]              # total number of errors during protocol
        stepMap = [{}, {}]              # maps step to allocated port
        foundSomething = False
        for i in range(0, iters):
            
            # A scan
            #dstA  = b[i]#1*i #- stageChange*(stageNumA)/10.0# destination of scan o the other side
            for party in [0,1]:
                # Obtain next tuple (source port, destination port) from strategy
                nextA = strategies[party].next(party, i)
                dstA  = nextA[1]
                # Obtain external NAT port by querying NAT for allocation a new connection
                curA  = nats[party].alloc(party, nextA[0], party ^ 0x1, dstA, i*self.portScanInterval)
                
                # Waiting between consecutive scans, compute number of new connections by 
                # using Poisson process. Now generating new allocations to the new round/step of the protocol.
                curLag = natSamples[party][i]
                totalLagA[party] += curLag
                
                # Reflect allocations meanwhile to the NAT
                nats[party].occupy(curLag, i*self.portScanInterval)
                
                # Add protocol to the maps.
                toAdd  = (curA, dstA) if party==0 else (dstA, curA)     # swap pair for other party - in order to find set intersection
                scanA[party].add(toAdd)
                portsA[party].add(curA)
                mapA[party][curA] = i
                stepMap[party][i] = (curA, dstA)
                #print "A scan: %d [%03d] --> [%03d] lag=%02d i=%03d toAdd=%s" % (party, curA, dstA, curLag, i, str(toAdd))
                
                if stopOnFirstMatch and toAdd in scanA[party ^ 0x1]: 
                    foundSomething = True
            if foundSomething: break
        
        if not self.compact:
            print("TotalLags [%02d %02d]" % (totalLagA[0], totalLagA[1]))
        
        # OK is there any intersection in both sets?
        res = list(scanA[0].intersection(scanA[1]))
        # sort by minimum element in tuple
        res.sort(key=lambda tup: min(tup[0], tup[1]))
        
        # ascii match
        if self.ascii > 1 or (self.ascii==1 and self.simulationRounds==1):
            self.matchAscii(portsA, scanA, mapA, stepMap, res)
        
        # Generate DOT graph
        if self.dot > 1 or (self.dot==1 and self.simulationRounds==1):
            self.generateDot(portsA[0], portsA[1], scanA[0], scanA[1], mapA, res)
        
        # return tuple
        ret = (res, portsA, mapA, scanA, totalLagA, stepMap)
                    
        # Algorithm failed to establish a new connection
        if (len(res) == 0): 
            if not self.compact:
                print("Algorithm failed, no intersecting points")
            else:
                sys.stdout.write('.')
                sys.stdout.flush()
                
        # fail -> nothing to do now
        if (len(res) == 0): 
            return ret
        
        if not self.compact: 
            print("RES: ", res, "i=%02d" % mapA[0][res[0][0]], "; j=%02d" % mapA[1][res[0][1]])
        else:
            sys.stdout.write('X')
            sys.stdout.flush()
            
        return ret
        
    def simulation(self, natA, natB, strategy):
        '''
        Simple simulation of NAT traversal algorithm.
        '''
        
        nats = [natA, natB]
        successCnt = 0.0
        stopOnFirstMatch = self.simulationRounds != 1
        getTime = lambda: int(round(time.time() * 1000))
        simStart = getTime()
        
        resM = []
        
        successAcc = [0,0]              # accumulator for steps needed to connect if successfully
        realRounds = self.simulationRounds
        for sn in range(0, self.simulationRounds):
            # reset NATs
            nats[0].reset()
            nats[1].reset()
            strategy.reset(nats, self)
            
            # generate silent period time
            curSilentA = self.silentPeriodBase + self.poisson(self.silentPeriodlmbd, 1)
            curSilentB = self.silentPeriodBase + self.poisson(self.silentPeriodlmbd, 1)
            
            if not self.compact:
                print("\n##%03d. Current silent period time: [%03.3f, %03.3f]" % (sn, curSilentA, curSilentB)) 
            
            # generate new TCP connections for silent period on both sides, same lambda.
            kA = self.poisson(self.lmbd, curSilentA)
            kB = self.poisson(self.lmbd, curSilentB)
            
            # reflect errors to NAT allocation
            nats[0].occupy(kA, 0)
            nats[1].occupy(kB, 0)
            
            # set silent period duration to the strategy
            sData = []
            sData = strategy.silent(curSilentA, curSilentB, self.lmbd)
            
            # do the simulation round
            natSamples = [[], []]
            for i in [0, 1]:
                natSamples[i] = [int(x) for x in np.random.poisson(self.lmbd*self.portScanInterval, self.errors)] 
            (res, portsA, mapA, scanA, totalLagA, stepMap) = self.simulationCore(natSamples, [strategy, strategy], nats, stopOnFirstMatch)
            
            # Stop early if poor performance
            if sn == self.simulationRoundsFast and 2.0*successCnt < self.simulationRoundsFast:
                sys.stdout.write('Z')
                sys.stdout.flush()
                realRounds = sn+1
                break 
            
            # fail -> nothing to do now
            if (len(res) == 0): 
                continue
            
            #resM.extend([i for i,j in res])
            resM.append(res[0][0])
            
            successCnt += 1.0
            successAcc[0] += mapA[0][res[0][0]]
            successAcc[1] += mapA[1][res[0][1]]
        
        simEnd = getTime()
        simTotal = simEnd - simStart

        #P.grid(True)
        #P.Figure()
        #P.hist(resM, max(resM), density=0, histtype='bar')
        #graph(plt)
            
        # Report results after simulation is done
        print("\nSuccess count: %02.3f ; cnt=%03d; lmbd=%01.3f; scanInterval=%04d ms; base sleep=%04d; average steps: %04.3f %04.3f; time elapsed=%04.3f s" % \
            (successCnt / realRounds    if realRounds > 0 else 0, 
             successCnt, 
             self.lmbd, 
             self.portScanInterval, 
             self.silentPeriodBase,
             successAcc[0] / successCnt if successCnt > 0 else 0,
             successAcc[1] / successCnt if successCnt > 0 else 0,
             simTotal/1000.0))
        
        return (successCnt / realRounds    if realRounds > 0 else 0, 
                successCnt, 
                successAcc[0] / successCnt if successCnt > 0 else 0,
                successAcc[1] / successCnt if successCnt > 0 else 0,)
    
    def nfSimulation(self, natA, natB, strategyA, strategyB, filename=None, processedNfdump=None, homeNet='', filt=None, recEachSkip=0, maxBlock=-1):
        '''
        Simulating NAT for traversal algorithms with netflow data as network load.
        '''
        #
        # Run NFdump and read line by line
        #
        activeTimeout = 300*1000 # active timeout = time after a long lived flow is ended and written to a netflow file
        sampleSize = 950
        sampleSkip = 0
        curTestSize=0
        sn=0
        statAccum=[]
        
        T = self.portScanInterval   # port scan interval for nfdump generator, strategies, ... 
        lambdaSamples = 20          # how many NAT samples are taken to measure lambda...
        
        # simulation variables
        strategies = [strategyA, strategyB]
        nats = [natA, natB]
        successCnt = 0.0
        stopOnFirstMatch = self.simulationRounds != 1
        getTime = lambda: int(round(time.time() * 1000))
        simStart = getTime()
        successAcc = [0,0]              # accumulator for steps needed to connect if successfully
        realRounds = self.simulationRounds
        
        # Prepare nfdump record generator.
        nfdumpObj       = None
        nfdumpGenerator = None
        if processedNfdump!=None:
            nfdumpObj = NfdumpReader(processedNfdump)
            nfdumpGenerator = nfdumpObj.generator()
        else:
            nfdumpObj = NfdumpSorter(filename, filt, activeTimeout)
            nfdumpGenerator = nfdumpObj.generator()
        
        # generates samples of NAT process w.r.t. new connections.
        nfgen = self.nfdumpSampleGenerator(natA, nfdumpGenerator, homeNet, T, 
                                           sampleSize=sampleSize, recStartSkip=sampleSkip, 
                                           recEachSkip=recEachSkip, maxBlockSize=maxBlock, activeTimeout=activeTimeout)
        samplesMean = []
        while True:
            # sample NAT connections from Nfdump files
            natSamples = [[], []]
            try:
                natSamples[0] = next(nfgen)
                natSamples[1] = next(nfgen)
                if len(natSamples[0]) < sampleSize or len(natSamples[1]) < sampleSize: continue
            except Exception:
                break
            
            sn += 1
            
            # reset NATs
            nats[0].reset()
            nats[1].reset()
            strategyA.reset(nats, self)
            strategyB.reset(nats, self)
            
            # Lambda statistics
            samplesMean.append(np.mean(  [ (i / float(T)) for i in natSamples[0] ]  ))
            samplesMean.append(np.mean(  [ (i / float(T)) for i in natSamples[1] ]  ))
            
            #
            # Measure lambda by using some nat samples for it.
            # lmbd[0] = lambda for A network, thus interesting for B
            #
            lmbd    = [ (sum(natSamples[party][0:lambdaSamples]) / float(lambdaSamples          * T)) for party in [0,1] ]
            lmbdAvg = [ (sum(natSamples[party])                  / float(len(natSamples[party]) * T)) for party in [0,1] ] # average lambda, just info for user 
            natSamples[0] = natSamples[0][lambdaSamples:]
            natSamples[1] = natSamples[1][lambdaSamples:]
            
            # generate silent period time, convert to NAT samples
            curSilentA = (self.silentPeriodBase + self.poisson(self.silentPeriodlmbd, 1)) / float(T)
            curSilentB = (self.silentPeriodBase + self.poisson(self.silentPeriodlmbd, 1)) / float(T) 
            
            # Generate new connections for silent period on both sides. For whole time frames use 
            # NAT sample, for partial generate appropriate part according to lambda measurement...
            kA = sum(natSamples[0][0:int(math.floor(curSilentA))]) + self.poisson(lmbd[0], int(T*(curSilentA - math.floor(curSilentA))))
            kB = sum(natSamples[1][0:int(math.floor(curSilentB))]) + self.poisson(lmbd[1], int(T*(curSilentB - math.floor(curSilentB))))
            
            # reflect errors to NAT allocation - take ports from silent period
            nats[0].occupy(kA, 0)
            nats[1].occupy(kB, 0)
            
            # strip silent period
            natSamples[0] = natSamples[0][int(math.floor(curSilentA)):]
            natSamples[1] = natSamples[1][int(math.floor(curSilentB)):]
            
            # set silent period duration to the strategy
            strategyA.silent(int(curSilentB*T), 0, lmbd[1])
            strategyB.silent(0,                     int(curSilentA*T), lmbd[0])
            
            if not self.compact:
                print("\n##%03d. C. s. period: [%03.3f, %03.3f]~[%04d, %04d]; lmbd est [%03.3f, %03.3f] s.p. est. [%03.3f, %03.3f] len [%d, %d] lmbdAvg [%03.3f, %03.3f]" \
                    % (sn, 
                       curSilentA*T, curSilentB*T, 
                       kA, kB, 
                       lmbd[0], lmbd[1], 
                       strategyA.startPos[0], strategyB.startPos[1], 
                       len(natSamples[0]), len(natSamples[1]),
                       lmbdAvg[0], lmbdAvg[1]
                       ))
                print(strategyA)
            
            # do the simulation round 
            (res, portsA, mapA, scanA, totalLagA, stepMap) = self.simulationCore(natSamples, strategies, nats, stopOnFirstMatch)
            
            # fail -> nothing to do now
            if (len(res) == 0): 
                continue
            
            successCnt += 1.0
            successAcc[0] += mapA[0][res[0][0]]
            successAcc[1] += mapA[1][res[0][1]]
            
            # Stop early if poor performance
            if False and (sn == self.simulationRoundsFast and 2.0*successCnt < self.simulationRoundsFast):
                sys.stdout.write('Z')
                sys.stdout.flush()
                realRounds = sn+1
                break 
        
        simEnd = getTime()
        simTotal = simEnd - simStart    
        realRounds = sn
    
        # force generator de-initialization
        nfdumpObj.deinit()

        # Report results after simulation is done
        print("\nSuccess count: %02.3f ; cnt=%03d; lmbd=%01.3f; scanInterval=%04d ms; base sleep=%04d; average steps: %04.3f %04.3f; time elapsed=%04.3f s" % \
            (successCnt / realRounds    if realRounds > 0 else 0, 
             successCnt, 
             self.lmbd, 
             self.portScanInterval, 
             self.silentPeriodBase,
             successAcc[0] / successCnt if successCnt > 0 else 0,
             successAcc[1] / successCnt if successCnt > 0 else 0,
             simTotal/1000.0))
            
        print("Lambda mean(mean(lmbd)) = ", np.mean(samplesMean), "; median(mean(lmbd)) = ", np.median(samplesMean), "; var(mean(lmbd)) = ", np.var(samplesMean)) 
        return (successCnt / realRounds    if realRounds > 0 else 0, 
                successCnt, 
                successAcc[0] / successCnt if successCnt > 0 else 0,
                successAcc[1] / successCnt if successCnt > 0 else 0,)
    
    def matchAscii(self, portsA, scanA, mapA, stepMap, res):
        cnt = [[0]*3, [0]*3]
        for i in range(0, self.errors):     # step loop
            for p in [0,1]:                 # party loop
                chr = '.'
                
                if len(stepMap[p]) <= i: chr = ' '
                else: 
                    # At least one hit?
                    myGuess = stepMap[p][i][1]
                    myExt   = stepMap[p][i][0]
                    
                    if myGuess in portsA[p ^ 0x1]:
                        cnt[p][0] += 1 
                        chr = '+'
                    
                    # Exact hit?
                    if myGuess == stepMap[p ^ 0x1][i][0]:
                        cnt[p][1] += 1
                        chr = 'K'
                        
                    # Matching hit?
                    inMatch = len([j for j in res if j[p ^ 0x1] == myGuess]) > 0
                    if inMatch:
                        cnt[p][2] += 1
                        chr = 'M'
                        
                sys.stdout.write(chr)
            pass
            sys.stdout.write('|')
            if (i % 40) == 39: sys.stdout.write("\n")
             
        sys.stdout.write("\n")
        sys.stdout.flush()
        print("Hits: ", cnt)
    
    def generateDot(self, portsA, portsB, scanA, scanB, mapA, res):
        '''
        Generate DOT image for protocol run.
        '''
        
        dot = "digraph finite_state_machine {\n"
        maxport = max(max(portsA), max(portsB))
        for p in range(0, maxport):
            ps = str(p)
            inA = len([i for i in res if i[0] == p]) > 0
            inB = len([i for i in res if i[1] == p]) > 0
            
            line = "node [shape=circle, fixedsize=true, width=1, height=1, style=filled, colorscheme=orrd9, fillcolor=\"%s\" pos=\"%f,%f!\" label=\"%s\"] P%s;\n"
            desc = "node [shape=plaintext, width=2, pos=\"%f,%f!\" label=\"%s\"] DSC%s;\n"
            
            # A
            whoping  = [i for i in scanB if i[0] == p]
            
            color = 7
            if p in portsA:
                color = 3 if len(whoping) > 0 else 1
            if inA:
                color = "#00ff005f"
            dot = dot + line % (color, 10, 1.5*p, p, "A"+ps)
            
            # A desc
            if p in portsA:
                bcounter = [i for i in scanA if i[0] == p][0][1]
                acounter = [i for i in scanB if i[1] == bcounter] if bcounter in portsB else []
                acounter = acounter[0][0] if len(acounter)>0 else -1
                bnum     = str(mapA[1][bcounter]) if bcounter in mapA[1] else ''
                dot = dot + desc % (8, 1.5*p, "%04d\\n%03d-> %03d %s-> %03d\\n%s" % (mapA[0][p], p, bcounter, bnum, acounter, str(whoping)), "A"+ps)
            else:
                dot = dot + desc % (8, 1.5*p, "%s" % (str(whoping)), "A"+ps)
                
            
            # B
            whoping  = [i for i in scanA if i[1] == p]
            
            color = 7
            if p in portsB:
                color = 3 if len(whoping) > 0 else 1
            if inB:
                color = "#00ff005f"
            dot = dot + line % (color, 130, 1.5*p, p, "B"+ps)
            
            # B desc
            if p in portsB:
                acounter = [i for i in scanB if i[1] == p]
                acounter = acounter[0][0] if (len(acounter)>0) else -1
                bcounter = [i for i in scanA if i[0] == acounter][0][1] if acounter in portsA else -1
                anum     = str(mapA[0][acounter]) if acounter in mapA[0] else ''
                dot = dot + desc % (132, 1.5*p, "%04d\\n%03d-> %03d %s-> %03d\\n%s" % (mapA[1][p], p, acounter, anum, bcounter, str(whoping)), "B"+ps)
            else:
                dot = dot + desc % (132, 1.5*p, "%s" % (str(whoping)), "B"+ps)
        dot = dot + "\n\n"
        
        # add connections representing scan
        extraArrow =  "[penwidth=\"3.0\", arrowsize=\"2.5\"]"
        for tup in scanA:
            dot = dot + "PA%d -> PB%d %s\n" % (tup[0], tup[1], extraArrow if tup in res else "")
        for tup in scanB:
            dot = dot + "PB%d -> PA%d %s\n" % (tup[1], tup[0], extraArrow if tup in res else "")
        
        # generate graphviz image only for 1 round - illustrative run only
        dot = dot + "fontsize=32;}"
        f = open('dotfile.dot', 'w')
        f.write(dot)
        f.close()
        
        # generate SVG file
        print("GraphViz output: ", subprocess.Popen('neato -Tpng < dotfile.dot > dotfile.png', shell=True).communicate()[0])
    
    def poolExhaustionNat(self, natA, timeout):
        return self.poolExhaustion(timeout, natA.poolLen, self.lmbd)
    
    def poolExhaustion(self, timeout, poolsize, lmbd):
        '''
        Computes how long does it take to exhaust port pool given the new connection creation rate
        
        Related:
            Simulates Poisson process with arrival times
            source: http://www.columbia.edu/~ks20/4703-Sigman/4703-07-Notes-PP-NSPP.pdf
        '''
        t = 0.0
        N = 0
        i = 0
        while N <= poolsize and i < 5*poolsize:
            # U ~ U(0,1), uniform distribution
            U = random.random()
            i+= 1
            
            # next time of the event, exponential distribution
            t = t + (-(1/lmbd) * math.log(U))
            if (N > poolsize): break
        
            # increment the event counter
            N = N + 1
            #print "New event will occur: " + str(t) + ("; now events: %02d" % N)
        print("Port pool will be exhausted in %05.3f ms = %05.3f s = %05.3f min = %05.3f h" % (t, t/1000.0, t/1000.0/60, t/1000.0/60/60))
        print("P(X > portPoolSize) = %02.18f where X~Poisson(timeout * lamda = %d * %04.4f)" % (1.0-poisson.cdf(poolsize, float(lmbd) * timeout), timeout, lmbd))
        return t 
    
    def poolExhaustionEx(self, natA, timeout):
        '''
        Computes a simulation of port pool exhaustion of a NAT taking into consideration timeout.
        It is the same thing like poolExhaustion in this way: if result from poolExhaustion is below timeout of the NAT, 
        it will be exhausted with given setting. Otherwise some ports will timeout and NAT will never exhaust its pool size.
        
        Corresponds to the sample of P(X >= poolSize), X~Poisson(timeout * lambda). X is number of new connections in 
        the given timeout interval. This gives us probability that NAT will be exceeded.
        '''
        t = 0.0
        N = 0
        try:
            while True:
                # U ~ U(0,1), uniform distribution
                U = random.random()
                
                # next time of the event, exponential distribution
                nextEvt = (-(1/self.lmbd) * math.log(U)) 
                t = t + nextEvt 
                
                # add new port allocation by that time
                natA.occupy(1, t)
            
                # increment the event counter
                N = N + 1
                
                if (N % 10000) == 0:
                    freePorts = natA.trulyFreePorts(t)
                    freeRatio = freePorts / float(natA.poolLen)
                    print("New event will occur: " + str(t) + ("; now events: %02d; evt=%05.3f; freePorts=%d, %02.2f %%" % (N, nextEvt, freePorts, freeRatio)))
            
        except Exception as e:
            print("Port pool will be exhausted in %05.3f ms = %05.3f s = %05.3f min = %05.3f h" % (t, t/1000.0, t/1000.0/60, t/1000.0/60/60))
            print("Exception: ", e)
            pass
        
        return 0
    
    def getLambdaExhaustionCDF(self, natA, prob):
        '''
        Gets lambda such that:
        
        P(X > poolsize) >= prob, X ~ Poisson(lambda * timeout)
        '''
        # at first we have to find proper interval where to find by binary search
        timeout  = natA.timeout
        poolsize = natA.poolLen
        
        lmbd = 1.0
        lmbdL=-1
        lmbdR=-1
        
        while (lmbdL==-1) or (lmbdR==-1):
            probc = 1.0 - poisson.cdf(poolsize, lmbd * timeout)
            print("current lambda: %02.3f ; prob=%02.3f" % (lmbd, probc))
            
            # left side of the interval. If fits, set and continue to find right side, otherwise keep left 
            if lmbdL==-1:
                if probc <= prob: 
                    lmbdL = lmbd
                    lmbd  = lmbd * 2.0
                    continue
                else: 
                    lmbd = lmbd/2.0
                    continue
            
            # right side of the interval, if here, we have left side and finding the right side
            if probc > prob: 
                lmbdR = lmbd
                break
            else:
                lmbd = lmbd * 2.0
        
        print("Interval found: [%02.03f, %02.03f]" % (lmbdL, lmbdR))
        return self.getLambdaExhaustionCDFinterval(timeout, poolsize, prob, lmbdL, lmbdR)
        
    def getLambdaExhaustionCDFinterval(self, timeout, poolsize, prob, l, r):
        eps = 0.0001
        while l < r:
            c = (l+r)/2
            probc = 1.0 - poisson.cdf(poolsize, c * timeout)
            
            print("\nNew iteration [%02.03f, %02.03f]; c=%02.3f probc=%02.3f vs. prob=%02.3f" % (l, r, c, probc, prob))
            
            if probc >= (prob-eps) and probc <= (prob+eps): break 
            if probc < prob: l = c
            if probc > prob: r = c
        return l       
    
    def getLambdaExhaustion(self, natA):
        '''
        Get a lambda that will cause exhaustion for a given NAT
        '''
        # at first we have to find proper interval where to find by binary search
        timeout  = natA.timeout
        poolsize = natA.poolLen
        
        lmbd = 1.0
        lmbdL=-1
        lmbdR=-1
        
        while (lmbdL==-1) or (lmbdR==-1):
            print("current lambda: %02.3f" % lmbd)
            t = self.poolExhaustion(timeout, poolsize, lmbd)
            
            # left side of the interval. If fits, set and continue to find right side, otherwise keep left 
            if lmbdL==-1:
                if t > timeout: 
                    lmbdL = lmbd
                    lmbd  = lmbd * 2.0
                    continue
                else: 
                    lmbd = lmbd/2.0
                    continue
            
            # right side of the interval, if here, we have left side and finding the right side
            if t < timeout:
                lmbdR = lmbd
                break
            else:
                lmbd = lmbd * 2.0
        
        print("Interval found: [%02.03f, %02.03f]" % (lmbdL, lmbdR))
        return self.getLambdaExhaustionInterval(timeout, poolsize, lmbdL, lmbdR)
        
    def getLambdaExhaustionInterval(self, timeout, poolsize, lmbdL, lmbdR):
        '''
        Recursive binary search for finding lambda that will exhaust port pool size
        ''' 
        eps = 100 # search epsilon, accuracy, 100ms
        t   = 0
        while lmbdL < lmbdR and (lmbdR - lmbdL) > 0.00001:
            
            print("\nNew iteration [%02.03f, %02.03f]" % (lmbdL, lmbdR))
            cLmbd = (lmbdL+lmbdR) / 2.0
            t = self.poolExhaustion(timeout, poolsize, cLmbd)
            
            if t >= (timeout-eps) and t <= (timeout+eps): break 
            if t < timeout: lmbdR = cLmbd
            if t > timeout: lmbdL = cLmbd
        return lmbdL
    
    def portDistributionFunction(self, lmbd, t, isteps=[], exclude=[]):
        '''
        Measures distribution function of the ports on NAT with Poisson process.
        This matters since the whole nature of NAT is incremental. Number of 
        new connections in different time windows should hold distribution also
        considered together, Po(lmbd*(t1+t2)), but considering port numbers it 
        makes a difference, also taking my port allocation into account. 
        
        For instance port 6 can be reached by 2,2,2 or 3,3. 
        '''
        iterations = 5000
        
        isteps  = set(isteps)
        maxStep = max(isteps) if len(isteps) > 0 else 1000
        
        sn = 0
        ports = int(maxStep * 10)
        portDistrib = [0] * ports   # initializes to arrays to zeros of length <ports>
        portDistribSteps = {}       # port distributions in particular steps
        
        # initialize port distributions 
        for i in isteps: portDistribSteps[i] = [0] * ports 
        
        # Simulate the process. Each iteration generates process sample and
        # adds data to accumulators.
        while sn < iterations:
            
            # Speed optimization - sample poisson distribution
            poissonSample = np.random.poisson(lmbd*t, max(maxStep+10, ports+10))
            ssize = len(poissonSample)
            
            # 
            portsArr = {}   # stores step -> port mapping in this simulation run
            step    = 0
            curPort = 0
            fail    = False
            while curPort < ports:             
                # Select only those runs that does not trigger particular ports.
                # This fixes probability of occuring such port to 0 and simulating
                # port distribution function under this condition (conditional probability)
                #
                # How to set p.m.f. for some element x to 0: re-normalize p.m.f. to 1 again,
                # multiply each element by 1/(1-p) where p is probability for element x.
                #
                # How it is done here: lets assume 1 step, left out y=2.
                # Then p(1)_real = p(1) * sum_{step=0}^{\infty} P(y)^{step} = p(1) * (1 + p(y) + p(y)^2 + ...)
                # Sum of a geometrical sequence gives us: sum_{step=0}^{\infty} P(y)^{step} = 1 / (1-p(y))
                #
                # Thus generating a) by scaling and b) by omitting and re-generating is equivalent, at least
                # for the first step.   
                #
                if step!=0 and (curPort in exclude) and maxStep >= step: 
                    fail = True
                    break
                
                # Step optimization here, if we are interested only in results from
                # particular step, it is not needed to compute
                if (maxStep!=-1 and maxStep < step):
                    break
                
                portsArr[step] = curPort
                curPort += poissonSample[step] if step < ssize else np.random.poisson(lmbd*t)  #self.poisson(lmbd, t) # add new connections by Poisson process
                curPort += 1                     # add my port, I made it by a new connection
                step    += 1
            
            #
            # Check if generated run complies our probabilistic distribution
            # we want to get.
            #
            if (sn % 1000) == 0: 
                sys.stdout.write('%04d;' % sn)  
                sys.stdout.flush()
            if fail: 
                sys.stdout.write('x')
                continue
            
            # Add ports to distribution.
            # We are computing probability dostribution on ports in particular 
            # time step.
            for step in portsArr:
                curPort = portsArr[step]
                
                # Are we interested in particular port add sample 
                # to distribution collector.
                if step in isteps:
                    portDistribSteps[step][curPort] += 1
                
                # Collecting to total distribution
                if curPort!=0:
                    portDistrib[curPort] += 1
            sn += 1 
        pass
        lmbdStr = ('%01.4f' % lmbd).replace('.', '_')
        print("Data sampling done...")
        
        # Histogram & statistics for whole process
        #self.histAndStatisticsPortDistrib(portDistrib, iterations, ports, 'distrib/total_%s_%03d.pdf' % (lmbdStr, t))
    
        # Histogram & statistics for particular interesting ports
        for step in portDistribSteps:
            print("\n", "="*80)
            print("Step %03d" % step)
            self.histAndStatisticsPortDistrib(portDistribSteps[step], iterations, ports, 'distrib/step_%s_%03d__%04d.png' % (lmbdStr, t, step), drawHist=True, step=step)
    
    def histAndStatisticsPortDistrib(self, portDistrib, iterations, ports, fname=None, histWidth=None, drawHist=False, step=-1, dist=None):
        '''
        Compute basic statistics of a given distribution, draws histrogram.
        '''
        chi1, pval1, chi3, pval3, n3, p3, chi2, pval2, m2, l2, rr2 = 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
        
        #
        # Compute basic statistics
        #
        (ssum, ex, var, stdev) = self.calcPortDistribInfo(portDistrib, iterations)
        print("E[x] = %04.2f;  V[x] = %04.2f;  stddev = %04.2f;  sum=%05d; Distribution:" % (ex, var, stdev, ssum))
        print("totalp=", sum([i/float(iterations) for i in portDistrib]))
        #print "dist=[",(" ".join([ '%04d=%01.5f, %s' % (i, i/float(iterations), "\n" if (p % 40) == 39 else '') for p, i in enumerate(portDistrib)])),"]"
        
        #
        # Maximum Likelihood Estimation (MLE) of parameter lambda for poisson distribution.
        # MLE for poisson = sample mean
        # [https://onlinecourses.science.psu.edu/stat504/node/28]
        #
        (chi1, pval1, m1) = self.goodMatchPoisson(ex, portDistrib, ports, iterations, shift=0)
        rr1               = self.pearsonCorelation(m1, portDistrib)
        print("Chi-Squared test on match with Po(%s):     Chi: %s, p-value=%01.25f; alpha=0.05; hypothesis %s r=%01.8f" % \
            (('%04.4f' % ex).zfill(8), ('%04.4f' % chi1).zfill(10), pval1, "is REJECTED" if pval1 < 0.05 else "holds      ", rr1))
            
        #
        # Try to approximate with poisson distribution and binomial distribution
        # Sum of N independent Po: Po(l)+Po(l)+...+Po(l) =~ Po(lN), incrementing by 1 in each step 
        # the expected value is shifted in i-th round by i to the right, but lamda is still same! 
        # Thus for E[X] = ex, Ex[S_i] = i + i*T*lambda. where i=step.  
        if step!=-1:
            l2 = ex-step
            (chi2, pval2, m2) = self.goodMatchPoisson(l2, portDistrib, ports, iterations, shift=int(step * (-1)))
            rr2               = self.pearsonCorelation(m2, portDistrib)
            print("Chi-Squared test on match with Po(%s):     Chi: %s, p-value=%01.25f; alpha=0.05; hypothesis %s r=%01.8f" % \
                (('%04.4f' % (l2)).zfill(8), ('%04.4f' % chi2).zfill(10), pval2, "is REJECTED" if pval2 < 0.05 else "holds      ", rr2))#
        elif var < ex:
            l2 = var
            (chi2, pval2, m2) = self.goodMatchPoisson(l2, portDistrib, ports, iterations, shift=int((ex-var) * (-1)))
            rr2               = self.pearsonCorelation(m2, portDistrib)
            print("Chi-Squared test on match with Po(%s)s:    Chi: %s, p-value=%01.25f; alpha=0.05; hypothesis %s r=%01.8f" % \
                (('%04.4f' % (l2)).zfill(8), ('%04.4f' % chi2).zfill(10), pval2, "is REJECTED" if pval2 < 0.05 else "holds      ", rr2))#
           
        # Binomial distribution - may be handy on port distribution functions in a particular step. 
        (chi3, pval3, n3, p3, m3) = self.goodMatchBinomial(ex, var, portDistrib, ports, iterations, wiseBinning=True)
        rr3                       = self.pearsonCorelation(m3, portDistrib)
        print("Chi-Squared test on match with Bi(%04d, %04.4f): Chi: %s, p-value=%01.25f; alpha=0.05; hypothesis %s r=%01.8f" % \
            (n3, p3, ('%04.4f' % chi3).zfill(10), pval3, "is REJECTED" if pval3 < 0.05 else "holds      ", rr3))        

        # Negative binomial try - for real data from netflow
        (chi4, pval4, n4, p4, m4) = self.goodMatchNegativeBinomial(ex, var, portDistrib, ports, iterations, False, wiseBinning=True)
        rr4                       = self.pearsonCorelation(m4, portDistrib)
        print("Chi-Squared test on match with NB(%04d, %04.4f): Chi: %s, p-value=%01.25f; alpha=0.05; hypothesis %s r=%01.8f" % \
            (n4, p4, ('%04.4f' % chi4).zfill(10), pval4, "is REJECTED" if pval4 < 0.05 else "holds      ", rr4))
        
        # Nefative binomial - MLE for parameters
        (chi5, pval5, n5, p5, m5, par5) = self.goodMatchNegativeBinomialMLE(portDistrib, ports, iterations, False, wiseBinning=True)
        rr5                             = self.pearsonCorelation(m5, portDistrib)
        print("Chi-Squared test on match with NB(%04d, %04.4f): Chi: %s, p-value=%01.25f; alpha=0.05; hypothesis %s r=%01.8f" % \
            (n5, p5, ('%04.4f' % chi5).zfill(10), pval5, "is REJECTED" if pval5 < 0.05 else "holds      ", rr5))
            
        #
        # Draw a histogram
        #
        if drawHist:
            pos = np.arange(ports)
            width = 1.0     # gives histogram aspect to the bar diagram
            
            ax = plt.axes()
            ax.set_xticks(pos + (width / 2))
            ax.set_xticklabels(list(range(0, ports)), rotation=90, size='xx-small')
            
            plt.xlabel('port')
            plt.ylabel('frequency') #,rotation='horizontal')
            plt.grid(True)

            if histWidth != None:
                ax.set_ylim([0,histWidth])
            
            (tlow, thigh, tmax) = self.unimodalLowIdx(portDistrib, 1)   # set limits on X axis to show only values above 0    
            plt.xlim(tlow,thigh)
            
            plt.bar(pos, portDistrib, width, color='r')
            plt.plot(pos, m1, 'g^', label="Po")
            plt.plot(pos, m5, 'mp', label="NB")
            #plt.plot(pos, m4, 'c>', label="BN")
            
            if step!=-1:
                plt.plot(pos, m2, 'b^', label="$Po_2$")
            
            if fname != None:
                if isinstance(fname, list):
                    for fname_i in fname:
                        try:
                            plt.savefig(fname_i)
                        except Exception as e:
                            print("Error, cannot save to file %s Exception: " % fname_i, e)
                else:
                    plt.savefig(fname)
                plt.close()
            else:
                plt.show()
                
        return {'ssum' : ssum, 'ex': ex, 'var': var, 'stdev': stdev, 
                'distrib': [
                     {'chi': chi1, 'pval': pval1, 'm': m1, 'r2': rr1, 'par': [ex], 'lmbd': ex},             # Poisson
                     {'chi': chi2, 'pval': pval2, 'm': m2, 'r2': rr2, 'par': [l2], 'lmbd': l2},             # Poisson, shifted, variance based
                     {'chi': chi3, 'pval': pval3, 'm': m3, 'r2': rr3, 'par': [n3,p3], 'n': n3, 'p': p3},    # Binomial
                     {'chi': chi4, 'pval': pval4, 'm': m4, 'r2': rr4, 'par': [n4,p4], 'n': n4, 'p': p4},    # Negative binomial
                     {'chi': chi5, 'pval': pval5, 'm': m5, 'r2': rr5, 'par': [n5,p5], 'n': n5, 'p': p5}     # Negative binomial, MLE
                    ]    
                }
    
    def pearsonCorelation(self, model, observed):
        '''
        Computes Pearson's corerlation coefficient on observed data and model. Model and observed data has
        be in same bins.
        
        https://en.wikipedia.org/wiki/Pearson_product-moment_correlation_coefficient 
        '''
        try:
            nmodel = np.array(model)
            nobs   = np.array(observed)
            
            meanMod = nmodel.mean()
            meanObs = nobs.mean()
            
            numerator   = sum((nmodel-meanMod)*(nobs-meanObs)) 
            denumerator = math.sqrt(sum((nmodel-meanMod)**2)) * math.sqrt(sum((nobs-meanObs)**2))  
            return (numerator / denumerator) 
        except Exception as e:
            print("Problem with computing correlation")
            return 0.0
        
    def calcPortDistribInfo(self, portDistrib, iterations):
        '''
        E[X], V[X], stddev, sum
        '''
        
        # sum port distrib function
        ssum = 0
        for i in portDistrib: ssum+=i 
        
        # expected value of distribution
        ex = 0.0
        for port, count in enumerate(portDistrib): 
            p   = float(count) / float(iterations)
            ex += p * float(port)
        
        # sample unbiased variance of distribution = 1/(n-1) * Sum((x_i - E[x])^2)
        var = 0.0
        cn  = 0
        for port, count in enumerate(portDistrib):
            var += count * ((port - ex) * (port - ex))
            cn  += count
            
        var = var / (float(cn)-1) if cn>1 else 0
        stdev = math.sqrt(var)
        return (ssum, ex, var, stdev)
    
    def goodMatchBinomial(self, ex, var, observed, bins, iterations, matchBoth=True, verbose=False, wiseBinning=False):
        '''
        Performs Chi-Squared test that given data comes from binomial distribution.
        Number of bins to sample from poisson=0..bins
        '''
        # X ~ Binom(n, p)
        # E[x] = np
        # V[x] = np(1-p)
        # if we know E[X], V[X] then V[X] = E[X] * (1-p) => p = (E[x] - V[X]) / E[x]
        if ex == 0: return (0.0, 0.0, 0.0, 0.0)
        ex  = float(ex)
        var = float(var)
        
        p = (ex-var) / ex
        n = ex / p
        
        if n<0: n = -1*n
        if p<0: p = -1*p
        
        #n = ex*ex / (var-ex)
        #p = ex/n
        
#        if p<0:
#            p=1+p
#            n=ex/p
#        
#        if p>1:
#            p = 1/p
#            n = ex/p
        
        return self.goodMatchBinomialNP(int(n), p, observed, bins, iterations, matchBoth, verbose, wiseBinning)
    
    def goodMatchBinomialNP(self, n, p, observed, bins, iterations, matchBoth=True, verbose=False, wiseBinning=False):
        '''
        Performs Chi-Squared test that given data comes from binomial distribution.
        Number of bins to sample from poisson=0..bins
        '''
        n = int(n)
        
        # expected values - compute N * probability for each port assumed in range 0..bins
        expected = [(iterations * binom.pmf(i, n, p)) for i in range(0, bins)]
        (chi, pval) = self.goodMatchDistribution(observed, expected, bins, iterations, matchBoth, verbose, wiseBinning, ddof=1)
        return (chi, pval, n, p, expected)
    
    def goodMatchNegativeBinomial(self, ex, var, observed, bins, iterations, matchBoth=True, verbose=False, wiseBinning=False):
        '''
        Performs Chi-Squared test that given data comes from negative binomial distribution.
        Number of bins to sample from poisson=0..bins
        
        Old method: estimate n,p parameters of the model from EX, VAR.
        '''
        # X ~ NBinom(n, p)
        # E[x] = np/(1-p)
        # V[x] = np/(1-p)^2
        if var == 0 or ex<0: return (0.0, 0.0, 0.0, 0.0, [0 for i in range(0, len(observed))])
        ex  = float(ex)
        var = float(var)
        
        # Quadratic equation root. 
        p = (2 - math.sqrt(4*ex/var)) / 2
        if p==0: return (0,0,0,0)
        if p<0: p = (2 - math.sqrt(2*ex/var)) / 2
        if p<0: p = (2 - math.sqrt(ex/var)) / 2
        n = round(ex*(1-p)/p)  
        
        return self.goodMatchNegativeBinomialNP(n, 1-p, observed, bins, iterations, matchBoth, verbose, wiseBinning)
    
    def goodMatchNegativeBinomialNP(self, n, p, observed, bins, iterations, matchBoth=True, verbose=False, wiseBinning=False):
        '''
        Performs Chi-Squared test that given data comes from negative binomial distribution.
        Number of bins to sample from poisson=0..bins
        '''
        
        # expected values - compute N * probability for each port assumed in range 0..bins
        expected = [(iterations * nbinom.pmf(i, n, p)) for i in range(0, bins)]
        (chi, pval) = self.goodMatchDistribution(observed, expected, bins, iterations, matchBoth, verbose, wiseBinning, ddof=1)
        return (chi, pval, n, p, expected)
    
    def goodMatchNegativeBinomialMLE(self, observed, bins, iterations, matchBoth=True, verbose=False, wiseBinning=False):
        '''
        Performs Chi-Squared test that given data comes from negative binomial distribution.
        Number of bins to sample from poisson=0..bins.
        
        New method: use Maximum Likelihood Estimator - derivL(Theta, x) / derivTheta = 0, 
        No closed form exists, solving iteratively in R.
        [http://web.njit.edu/all_topics/Prog_Lang_Docs/html/library/MASS/html/fitdistr.html]
        '''
        
        obsArray = []
        for a,b in enumerate(observed):
            for i in range(0,b): obsArray.append(a)
        
        params = None
        try:
            x = IntVector(obsArray)
            params = MASS.fitdistr(x, 'negative binomial')
        except Exception as e:
            print("Problem, exception here", e)
            return (0, 0, 0, 0, [0 for i in observed], params)
        
        n = (params[0][0])
        p = params[0][0] / (params[0][0] + params[0][1])
        
        # expected values - compute N * probability for each port assumed in range 0..bins
        expected = [(iterations * nbinom.pmf(i, n, p)) for i in range(0, bins)]
        (chi, pval) = self.goodMatchDistribution(observed, expected, bins, iterations, matchBoth, verbose, wiseBinning, ddof=1)
        return (chi, pval, n, p, expected, params)
        
    def goodMatchPoisson(self, lmbd, observed, bins, iterations, shift=0, matchBoth=True, verbose=False, wiseBinning=False):
        '''
        Performs Chi-Squared test that given data comes from Poisson distribution with given lambda
        Number of bins to sample from poisson=0..bins
        
        Poisson distribution is shifted by a given factor.
        '''        
        # expected values - compute N * probability for each port assumed in range 0..bins
        expected = [(iterations * poisson.pmf(i+shift, lmbd)) for i in range(0, bins)]
        #print "shift=%d " % shift, expected
        #print [(i+shift) for i in range(0, bins)]
        #print "+"*80
        (chi, pval) = self.goodMatchDistribution(observed, expected, bins, iterations, matchBoth, verbose, wiseBinning)
        return (chi, pval, expected) 
    
    def goodMatchDistribution(self, observed, expected, bins, iterations, matchBoth=True, verbose=False, wiseBinning=False, ddof=0):
        '''
        Performs Chi-Squared test that given data comes from a given distribution.
        Number of bins to sample from distribution=0..bins
        '''       
        # select only those values which N*np >= 5
        idxExGt5 = [i for i,x in enumerate(expected) if x>=5]
        if idxExGt5 == None or len(idxExGt5)==0:
            return (0.0,0.0)
        
        idxObsGt5 = [i for i,x in enumerate(observed) if x>=5] if matchBoth else idxExGt5
        if idxObsGt5 == None or len(idxObsGt5)==0:
            return (0.0,0.0)
        
        # intersection on ports
        bothGt5 = list(set(idxExGt5) & set(idxObsGt5))
        bothGt5.sort()
        if bothGt5 == None or len(bothGt5)<3:
            if verbose: print("Warning! too few matching indices: %d" % len(bothGt5))
            return (0.0,0.0)
        
        # expected values having counts higher-and-equal than 5
        expTest  = [expected[i] for i in bothGt5]
        
        # new observed array - select only those ports from passed array that are in idxExGt5
        obsTest  = [observed[i] for i in bothGt5]
        
        if wiseBinning:
            obsTest, expTest = self.unimodalWiseBinning(observed, expected, True)
        
        if verbose:
            print("matching both:\n", bothGt5)
            print("expected: \n", expTest)
            print("observed: \n", obsTest)
        
        # perform chi-squared test on distribution
        return chisquare(np.array(obsTest), f_exp=np.array(expTest), ddof=0)

    def unimodalLowIdx(self, observed, limit):
        '''
        Assumes unimodal distribution in observed. Finds indexes on the left and on the right that are smaller than limit.
        Is used to compute index limits for unimodal wise binning
        '''
        
        # 1. find maximum
        beg,end,maxidx=-1,-1,-1
        for i,val in enumerate(observed):
            if maxidx==-1 or (observed[maxidx] < val): maxidx=i
            
        if observed[maxidx] < limit: 
            return (0.0,0.0,0.0)
        
        # 2. iterate on both sides away from maximum - peak of an unimodal distribution
        for i in range(0, len(observed)):
            lft, rgt = maxidx-i, maxidx+i
            if beg==-1 and lft>=0            and observed[lft]<limit: beg=lft
            if end==-1 and rgt<len(observed) and observed[rgt]<limit: end=rgt
        if beg==-1: beg=0
        if end==-1: end=len(observed)-1
        
        return (beg,end,maxidx)
        
    def unimodalWiseBinning(self, observed, expected, bothSides=False):
        '''
        Based on partitioning with r>=0, k>=3 s.t. we have classes r,r+1,r+2,...,r+k-2,r+k-1
        border classes contains sum of outer intervals.
        In this method are observed values partitioned and expected are set accorditionally.
        
        Yarnold's [Yarnold 1970, Eaton 1978] criterium: works if n*p_i >= 5q forall i=1,..,k, where k>=3.
        q is a ratio of classes s.t. n*p_i < 5
        
        Iterate over observed (empirical) distribution and find r,k. Assumption - distribution is unimodal
        '''
        obsTest, expTest = [], []
        
        beg,  end,  maxidx  = self.unimodalLowIdx(observed, 5)
        beg2, end2, maxidx2 = 0, 0, 0
        if bothSides:
            beg2, end2, maxidx2 = self.unimodalLowIdx(expected, 5)
            beg = max(beg, beg2)
            end = min(end, end2)
            if beg >= end: return (obsTest, expTest)
        
        if (end-beg)<=3: return (obsTest, expTest)
        # 3. generate categories, handle boundary categories that are sums
        
        # Left border category
        expTest.append(sum([j for i,j in enumerate(expected) if i<=beg]))
        obsTest.append(sum([j for i,j in enumerate(observed) if i<=beg]))
        # Inside
        expTest.extend([j for i,j in enumerate(expected) if i>beg and i<end])
        obsTest.extend([j for i,j in enumerate(observed) if i>beg and i<end])
        # Right border category
        expTest.append(sum([j for i,j in enumerate(expected) if i>=end]))
        obsTest.append(sum([j for i,j in enumerate(observed) if i>=end]))
        #print "obs:", obsTest
        #print "exp:", expTest
        return (obsTest, expTest)
    
    def myProcEstimator(self, lmbd=-1, T=-1):
        '''
        Conditional estimator assuming we weren't lucky in the previous guess
        '''
        
        if lmbd == -1: lmbd = self.lmbd
        if T == -1: T = self.portScanInterval
        
        # First value of selection is clear - expected value for distribution for C_i
        g = [round(1 + lmbd*T)]
        
        # Probability distribution on previous port number
        # First guess is poisson distribution shifted to the right: f(x - 1, lmbd * T)
        f1 = [0.0]
        f1.extend([poisson.pmf(i, lmbd*T) for i in range(0, self.errors)])  
        prevDistrib = dict(list(zip(list(range(len(f1))), f1))) 
        for r in range(1, self.errors):
            print("="*120)
            print("Round %04d; prev guess %d" % (r, g[r-1]))
            
            #
            # Compute current probability distribution
            # conditionally - assume previous tip failed.
            #
            
            # Compute previous distribution, but conditioned on the last choice
            # f(g[r-1]) = 0.
            # Done by scaling - multiply each remaining element by 1 / (1 - f(g[r-1]))
            if not (g[r-1] in prevDistrib): prevDistrib[g[r-1]] = 0.0 # set to zero in p. distrib if does not exist
            prevDistribCond = dict( [(i, prevDistrib[i] / (1.0 - prevDistrib[g[r-1]])) if i!=g[r-1] else (i, 0.0) for i in list(prevDistrib.keys())] )
            
            # Sanity check - sum previous distribution, should be close to 1
            prevSum = sum([prevDistribCond[i] for i in prevDistribCond])
            print("Sum on condition: ", prevSum)
            
            # Compute current conditional distribution P(C_i = x) with law of total probability:
            # P(C_i = x) = \sum_{y} P(C_i = x | C_{i-1} = y) * P(C_{i-1} = y)
            # P(C_i = x) = \sum_{y} \sum_{y} P(C_i = x | C_{i-1} = y) * prevDistribCond(y)
            # for all x, over all y.
            curDistrib = {}
            maxIdx = -1
            zero = 0
            for x in range(2*self.errors):
                #print "  x:", x
                # Iterative computing for current x
                #print "x: ", x
                px = 0.0 
                
                # Sum over all y values. Can ignore y such that x<=y
                # because C_i = C_{i-1} + 1 + Po(T * lmbd)
                # thus C_i > C_{i-1}
                for y in range(0, x):
                    # Skip values not in previous distrib function - would be zero.
                    if not (y in prevDistribCond): continue
                    if prevDistribCond[y] == 0.0: continue
                    
                    # Compute P(C_i = x | C_{i-1} = y) * P(C_{i-1} = y)
                    cx = poisson.pmf(x-y-1, lmbd*T) * prevDistribCond[y]
                    if cx == 0.0: continue
                    
                    # Only if non-zero
                    px += cx 
                    #print "    -> P(C_%03d = %03d | C_%03d = %03d) * P(C_%03d = %03d) = %02.18f" % (r, x, r-1, y, r-1, y, cx)
                
                curDistrib[x] = px
                #print "  P(C_%03d = %03d) = %02.18f\n" % (r, x, px)
                
                if x > (max(g)*2) and px==0.0:
                    zero+=1
                
                if zero>=10:
                    #print "   break limit 2"
                    break
                
                if x > ((r * (1+lmbd*T))*lmbd*T*10): 
                    #print "   break limit 1"
                    break
            # End of for x loop
            pass 
        
            # Now we have probability distrib on C_r.
            # Since duplicities are not allowed, remove values from g
            # from probability distribution
            for cg in g:
                print(" removing cg=%d from distrib" % cg)
                if not (cg in curDistrib): curDistrib[cg] = 0.0 # set to zero in p. distrib if does not exist
                curDistrib = dict( [(i, curDistrib[i] / (1.0 - curDistrib[cg])) if i!=cg else (i, 0.0) for i in list(curDistrib.keys())] )
                
            # Self-check
            prevSum = sum([curDistrib[i] for i in curDistrib])
            print("Sum on condition: ", prevSum)
                
            # Find maximal element in prob. distribution
            for x in curDistrib:
                if maxIdx==-1 or curDistrib[x] > curDistrib[maxIdx]: maxIdx = x
            
            print("Move[%d] = %d" % (r, maxIdx)) 
            
            # Select new move - maximizing probability distribution
            g.append(maxIdx)
            
            # curdistrib -> prevDistrib
            prevDistrib = curDistrib
        return g
        
    def processEstimator(self, lmbd=-1, T=-1, rounds=100):
        '''
        Simulates estimator of the NAT poisson process
        '''
        
        if lmbd == -1: lmbd = self.lmbd
        if T == -1: T = self.portScanInterval
        
        matchesd = [[], [], [], [], [], [], []]
        matchesr = [[], [], [], [], [], [], []]
        matched = [0, 0, 0, 0, 0, 0, 0]
        matcher = [0, 0, 0, 0, 0, 0, 0]
        
        matchN = [[], [], [], [], [], [], []]
        matchI = [[], [], [], [], [], [], []]
        B = []
        
        x    = 0
        b    = []             # local array
        seen = set()          # duplicate check
        seen_add = seen.add
        for step in range(0, 3001):
            #for kk in range(0,100):
                x = int(  np.random.poisson(lmbd * T * (1+step* 1.5 )))# coe(lmbd*T)))  )
                if x not in seen and not seen_add(x): 
                    b.append(x)
                    if len(b)>1000: break
                        
        for r in range(0, rounds):
            #print "="*80
            sys.stdout.write( charproc(r, rounds) )
            sys.stdout.flush()
            
            natSamples = [round(x) for x in np.random.poisson(lmbd*T, self.errors)]
            procList   = [natSamples[0] + 1]
            for i in range(1, self.errors): procList.append(procList[i-1] + 1 + natSamples[i])
            
            #
            # Test expected value estimator
            #
            exVal = [round(i * (1 + (lmbd)*T)) for i in range(1, self.errors+1)]
            exMatch = list(set(procList) & set(exVal))
            # In order test
            exInOrd = [ j for i,j in enumerate(procList) if j==exVal[i] ]
            # Res
            matchN[0].extend(exMatch)
            matchI[0].extend(exInOrd)
            if len(exMatch)>0: 
                matched[0] += 1
                matchesd[0].append(len(exMatch))
            if len(exInOrd)>0: 
                matcher[0] += 1
                matchesr[0].append(len(exInOrd))
            #print "Round %04d; EX matched=%03d; total=%1.5f inOrd=%03d" % (r, len(exMatch), len(exMatch)/float(self.errors), exInOrd) #, "matches: ", exMatch
            
            #
            # Test sampling value estimator
            #
            natSamples2 = [round(x) for x in np.random.poisson(lmbd*T, self.errors)]
            procList2   = [natSamples2[0] + 1]
            for i in range(1, self.errors): procList2.append(procList2[i-1] + 1 + natSamples2[i])
            samMatch = list(set(procList) & set(procList2))
            
            # In order test
            samInOrd = [ j for i,j in enumerate(procList) if j==procList2[i] ]
            matchN[1].extend(samMatch)
            matchI[1].extend(samInOrd)
            if len(samMatch)>0:
                matched[1] += 1
                matchesd[1].append(len(samMatch))
            if len(samInOrd)>0: 
                matcher[1] += 1
                matchesr[1].append(len(samInOrd))
            #print "Round %04d; Sa matched=%03d; total=%1.5f inOrd=%03d" % (r, len(samMatch), len(samMatch)/float(self.errors), samInOrd) #, "matches: ", samMatch
            
            #
            # Test sampling value estimator - coef
            #
            #x    = 0
            #b    = []             # local array
            seen = set()          # duplicate check
            seen_add = seen.add
            for step in range(0, 0):#3001):
                #for kk in range(0,100):
                    x = int(  np.random.poisson(lmbd * T * (1+step* 1.5 )))# coe(lmbd*T)))  )
                    if x not in seen and not seen_add(x): 
                        b.append(x)
                        if len(b)>1000: break
            #B.extend(b)
            #if r<10: print b[0:20]
            bMatch = list(set(procList) & set(b))
            
            # In order test
            bInOrd = [ j for i,j in enumerate(procList) if j==b[i] ]
            
            matchN[2].extend(bMatch)
            matchI[2].extend(bInOrd)
            if len(bMatch)>0: 
                matched[2] += 1
                matchesd[2].append(len(bMatch))
            if len(bInOrd) >0: 
                matcher[2] += 1
                matchesr[2].append(len(bInOrd))
            #print "Round %04d; Sx matched=%03d; total=%1.5f inOrd=%03d" % (r, len(bMatch), len(bMatch)/float(self.errors), bInOrd) #, "matches: ", samMatch
        
            #
            # Conditional estimator
            # 
            #glist = self.myProcEstimator(lmbd, T)
            glist = [i*2 for i in range(0, self.errors)]
            gMatch = list(set(procList) & set(glist))
            gMatch.sort()
            # In order test
            gInOrd = [ j for i,j in enumerate(procList) if j==glist[i] ]
            matchN[3].extend(gMatch)
            matchI[3].extend(gInOrd)
            if len(gMatch)>0: 
                matched[3] += 1
                matchesd[3].append(len(gMatch))
            if len(gInOrd)>0: 
                matcher[3] += 1
                matchesr[3].append(len(gInOrd))
            #print "Round %04d; Co matched=%03d; total=%1.5f inOrd=%03d" % (r, len(gMatch), len(gMatch)/float(self.errors), gInOrd) #, "matches: ", gMatch
        
        print("Coefficient:", coe(lmbd*T))
        print("Total")
        print([(i, np.median(k)) for i,k in enumerate(matchesd) if len(k)>0])
        print(matched)
        
        print("In order")
        print([(i, np.median(k)) for i,k in enumerate(matchesr) if len(k)>0])
        print(matcher)
        
        #print "Bgraph"
        #P.grid(True)
        #P.Figure()
        #P.hist(B, 1000, normed=0, histtype='bar')
        #graph(plt)
            
        for i in range(0,0):
            print("I=%d; total match" % i)
            P.grid(True)
            P.Figure()
            P.hist(matchN[i], 2000, normed=0, histtype='bar')
            graph(plt)
            
            print("I=%d; in-order match" % i)
            P.grid(True)
            P.Figure()
            P.hist(matchI[i], 2000, normed=0, histtype='bar')
            graph(plt)
            
    @staticmethod
    def pearson(vect, y):
        ss_err=(vect**2).sum()
        ss_tot=((y-y.mean())**2).sum()
        rsquared=1-(ss_err/ss_tot)
        return rsquared

    # Here ends the class
    pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='NAT simulator.', formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-o','--output',    help='Output file name from finder', required=False, default='graph.txt')
    parser.add_argument('-t','--space',     help='Time in ms to wait between packet send', required=False, default=10, type=int)
    parser.add_argument('-l','--lmbd_start',help='On which lambda to start', required=False, default=-1, type=float)
    parser.add_argument('-s','--strategy',  help='Strategy to use (poisson, i2j, fibo, their, ij, binom, simple)', required=False, default='poisson')
    parser.add_argument('-r','--rounds',    help='Simulation rounds', required=False, type=int, default=1000)
    parser.add_argument('-e','--errors',    help='Maximum steps by algorithm', required=False, type=int, default=1000)
    parser.add_argument('-d','--dot',       help='Graphviz dot illustration', required=False, type=int, default=0)
    parser.add_argument('-a','--ascii',     help='Ascii illustration', required=False, type=int, default=0)
    parser.add_argument('-n','--nfdump',    help='NFdump file', required=False, default=None)
    parser.add_argument('-m','--nfdump_sorted',help='NFdump sorted file', required=False, default=None)
    parser.add_argument('-f','--filter',    help='NFdump filter', required=False, default=None)
    parser.add_argument('-g','--hostnet',   help='NFdump host address', required=False, default="147.250.")
    parser.add_argument('-v','--verbose',   help='Verbosity level', required=False, default=0, type=int)
    parser.add_argument('--lmbd',           help='Default Poisson lambda for simulations', required=False, type=float, default=0.1)
    parser.add_argument('--pdistrib',       help='Switch to compute port distribution function', required=False, default=False, action='store_true')
    parser.add_argument('--nfdistrib',      help='Switch to compute netflow port distribution function', required=False, default=False, action='store_true')
    parser.add_argument('--sim',            help='Starts traversal algorithm simulation', required=False, default=False, action='store_true')
    parser.add_argument('--nfsim',          help='Starts traversal algorithm simulation with netflow data', required=False, default=False, action='store_true')
    parser.add_argument('--nfbench',        help='Benchmark algorithm simulation with netflow data', required=False, default=False, action='store_true')
    parser.add_argument('--benchmark',      help='Algorithm benchmarking for graphs', required=False, default=False, action='store_true')
    parser.add_argument('--exhaust',        help='Pool exhaustion computation', required=False, default=False, action='store_true')
    parser.add_argument('--proc',           help='Simulate poisson process and estimators', required=False, default=False, action='store_true')
    parser.add_argument('--exhaust_p',      help='Probability of port pool exhaustion to compute with', required=False, default=0.99, type=float)
    parser.add_argument('--coef',           help='Poisson coefficient finder', required=False, default=False, action='store_true')
    parser.add_argument('--fine',           help='Fine lambda interval to benchmark', required=False, default=False, action='store_true')
    parser.add_argument('--samples',        help='Samples in nfdump analysis', required=False, default=100, type=int)
    parser.add_argument('--maxblock',       help='Maximum number of blocks to collect', required=False, default=-1, type=int)
    parser.add_argument('--skipblock',      help='How many blocks to skip', required=False, default=0, type=int)
    parser.add_argument('--eachskip',       help='Records skipped between samples', required=False, default=0.0, type=float)
    
    args = parser.parse_args()
    
    ns = NatSimulation()
    
    # create a symmetric nat both for Alice and Bob
    natA = SymmetricIncrementalNat()
    natB = SymmetricIncrementalNat()
    
    natA.init(None)
    natB.init(None)
    
    strategies=[getStrategy(args.strategy), getStrategy(args.strategy)]
    strategies[0].init(None)
    strategies[1].init(None)
    
    #strategy.dupl = True
    #strategy.coef = 1.8
    # Their
    #strategy.delta = [200, 200]
    
    ns.lmbd = args.lmbd
    ns.dot = args.dot
    ns.ascii = args.ascii
    ns.simulationRounds = args.rounds
    ns.errors = args.errors
    ns.portScanInterval = args.space
    ns.silentPeriodBase=500
    ns.silentPeriodlmbd=10
    
    #
    # Port pool exhaustion computation
    #
    if args.exhaust:
        print("="*80)
        print("Computing port pool exhaustion\n")
        print(ns.poolExhaustionNat(natA, 3*60*1000))
        
        print("="*80)
        print("Computing lambda value that will cause NAT port pool exhaustion at some point...")
        lmbd = ns.getLambdaExhaustion(natA)
        print("\nLambda that will exhaust given NAT: ", lmbd)
       
        print("="*80)
        print("Computing lambda exhaustion value with probability=%01.3f" % args.exhaust_p)
        print(ns.getLambdaExhaustionCDF(natA, args.exhaust_p))

    
    #
    # NFdump
    # Computes port distribution function based on netflow network data.
    # 
    if args.nfdistrib:
        out = None
        if args.nfdump != None:
            out = ns.nfdumpDistribution(natA, filename=args.nfdump, homeNet=args.hostnet, filt=args.filter, sampleSize=args.samples, maxBlock=args.maxblock, skip=args.skipblock, fileOut=args.output)
        if args.nfdump_sorted != None:
            out = ns.nfdumpDistribution(natA, processedNfdump=args.nfdump_sorted, homeNet=args.hostnet, filt=args.filter, sampleSize=args.samples, maxBlock=args.maxblock, skip=args.skipblock, fileOut=args.output)
        
        #
        # Graph
        #
        styles = ['--bx', '-.g2', ':.r', '--|k', ':m+', '--1c']
        
        
        # Process output to nicely looking graph
        x = np.array(list(range(0,out['n'])))
        
        # e,x
        ex = np.array([d[0] for d in out['sd']])
        vx = np.array([d[1] for d in out['sd']])
        plt.plot(x, ex, 'bv', label="E[X]")
        plt.plot(x, vx, 'r+', label="V[X]")
        graph(plt)
        
        # p-value with critical region
        pk_p = np.array(out['pk'][0]) # poisson, key
        pk_n = np.array(out['pk'][4]) # nbin, key
        pv_p = np.array(out['pv'][0]) # poisson, value
        pv_n = np.array(out['pv'][4]) # nbin, value
        plt.plot(pk_p, pv_p, 'go', label="Po")
        plt.plot(pk_n, pv_n, 'b^', label="NB")
        plt.axhspan(0.0, 0.05, facecolor='r', alpha=0.5) # p-value reqion
        graph(plt, y='p-value', loc=-1)
        
        
        
    
    #
    # Simple algorithm simulation on a random sample of NAT data.
    #
    if args.sim:
        ns.simulation(natA, natB, strategies[0])
        
    #
    # NFdump
    # Simple algorithm simulation on a random sample of NAT data.
    #
    if args.nfsim:
        ns.compact = args.verbose == 0
        if args.nfdump != None:
            ns.nfSimulation(natA, natB, strategies[0], strategies[1], filename=args.nfdump, homeNet=args.hostnet, filt=args.filter, recEachSkip=args.eachskip, maxBlock=args.maxblock)
        if args.nfdump_sorted != None:
            ns.nfSimulation(natA, natB, strategies[0], strategies[1], processedNfdump=args.nfdump_sorted, homeNet=args.hostnet, filt=args.filter, recEachSkip=args.eachskip, maxBlock=args.maxblock)
    
    #
    # Computes port distribution function for given NAT and parameters.
    #
    if args.pdistrib:
        ns.portDistributionFunction(args.lmbd, args.space, list(range(1,180)), [])
        print("Port distribution done...")
    
    #
    # Poisson process estimators simulation
    #
    if args.proc:
        ns.processEstimator(rounds=args.rounds)
        print("Process estimation done...")
    
    #
    # Netflow benchmark
    #
    if args.nfbench and args.nfdump_sorted != None:
        
        pathDesc = "_".join(os.path.abspath(args.nfdump_sorted).split('/')[-2:])
        fname    = pathDesc + '_bench.txt'
        f = open(fname, 'a+')
        print("Dumping to file name: ", fname)
        
        # Iterate for T and strategies, only for sorted, sorry
        TArr = [10]
        SArr = ['their', 'i2j', 'poisson', 'simple']
        for T in TArr:
            print("="*80)
            for S in SArr:
                ns.portScanInterval = T
                strategies=[getStrategy(S,0), getStrategy(S,0)]
                strategies[0].init(None)
                strategies[1].init(None)
                
                print("S=%s T=%d" % (S, T))
                ns.compact = args.verbose == 0
                
                res = ns.nfSimulation(natA, natB, strategies[0], strategies[1], processedNfdump=args.nfdump_sorted, homeNet=args.hostnet, filt=args.filter, recEachSkip=args.eachskip, maxBlock=args.maxblock)
                f.write("%03.4f|%03.4f|%03.4f|%03.4f\n" % (ns.lmbd, ns.portScanInterval, res[0], res[2])) # python will convert \n to os.linesep
                f.flush()
                
                print("%03.4f|%03.4f|%03.4f|%03.4f\n" % (ns.lmbd, ns.portScanInterval, res[0], res[2]))
                    
        f.close()
        
    #
    # Algorithm benchmarking on different lambda to test performance in different environment.
    #
    if args.benchmark:
        # generating graph for moving lambda
        gc.enable()
        
        f = open(args.output, 'a+')
        ns.portScanInterval = args.space
        
        gc.collect()    # garbage collection is really needed...
        f.write("New start at %s; scanInterval=%d; strategy=%s file=[%s]\n" % (time.time(), ns.portScanInterval, args.strategy, args.output))
        print("Scanning port interval: %d" % ns.portScanInterval)
        
        # Construct lambda array to search in
        lmbdArr = []
        lmbdArr.extend( [i * 0.001 for i in range(1, 10)] )       # fine tuning
        lmbdArr.extend( [i * 0.01 for i in range(0, 26)] )        # 0.10 .. 0.25 interval scan
        if args.fine: lmbdArr.extend( [0.02 + i * 0.001 for i in range(0, 50)] )
        
        #
        # benchmarking NAT traversal algorithm on different lambdas
        #
        lmbdArr = list(set(lmbdArr))    # duplicity removal, round on 4 decimal places
        lmbdArr = [x for x in lmbdArr if x >= args.lmbd_start or args.lmbd_start==-1]
        lmbdArr.sort()                  # sort - better user intuition
        print("="*80)
        print("Lambdas that will be benchmarked: \n", (", ".join(['%04.3f' % i for i in lmbdArr])))
        print("="*80)
        
        for clmb in lmbdArr:
            res = []
            mem = getMem()
            print("# Current lambda: %03.4f; Avg silent period: %04.4f; Mem: %04.2f MB" % (clmb, clmb * (ns.silentPeriodBase + ns.silentPeriodlmbd), mem))
            if args.lmbd_start!=-1 and clmb < args.lmbd_start: continue
            
            ns.lmbd = clmb
            try:
                if args.strategy == 'poisson' and args.coef:
                    res = ns.coefFinder(natA, natB, strategies[0], 0.10, 0.1)
                    f.write("%03.4f|%03.4f|%03.4f|%03.4f\n" % (ns.lmbd, ns.portScanInterval, res[0], res[1])) # python will convert \n to os.linesep
                else:
                    res = ns.simulation(natA, natB, strategies[0])
                    f.write("%03.4f|%03.4f|%03.4f|%03.4f\n" % (ns.lmbd, ns.portScanInterval, res[0], res[2])) # python will convert \n to os.linesep
                f.flush()
            except Exception as e:
                print("Exception!", e)
        f.close()
        #ns.simulateThem()
    pass
    

        
