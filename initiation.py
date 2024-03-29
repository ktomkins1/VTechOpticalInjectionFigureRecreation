import injection_simulation as sim
import injection_visualization as vis
import bifurcation_detection as bfd
import config_create as cc

import numpy as np
import pickle #for archiving
import json   #for configuration
#import importlib #for importing the model dynamically
import io
import os
import sys
import argparse

from matplotlib import pyplot as plt
#from threading import Thread
from multiprocessing import Process, cpu_count

#import model here
#TODO: dynamically import from config
import models.model_psi_v2 as model

current_cid = 0

def bf_dispatch(setup, config):
    clean_config(config) #finalize config after splitting into multiple dicts
    results = {}
    if config['mode'] == 'bif':
        #get key for sweeping
        skey = detect_sweep_key(config)
        sweep = dict(config[skey]) #copying should not matter, but well whatever
        sweep_params = list(sweep.values())[0]

        if skey == 'eta':
            bfd.get_FRDPs(config)
            bfd.get_fwd_rev_hopf(config)

        #get method of enumeration
        rn = np.linspace
        rnmode = list(sweep.keys())[0]
        if rnmode == 'arange':
            rn = np.arange
        if rnmode == 'exp1':
            rn = sim.get_exponential_axis
        if rnmode == 'percent':
            if skey not in cc.pct_axis_support:
                raise ValueError("initiation.py: Attempting to use pct axis on {1}".format(skey))
            rn = sim.get_pct_bounds_axis
            sweep_params = [np.real(config['eta_FH']), np.real(config['eta_RH'])] + sweep_params

        sim.general_sweep(
                results, setup, config, skey, sweep_params, rn
            )

        if 'bf_cnb' in config.keys():
            bfd.get_cnb_from_groups(results, config['bf_cnb'])

        config[skey] = sweep #set our config back to original value
    if config['mode'] == 'single':
        sim.trace(results, setup, config)

    #create new directory for storing these results
    targetdir = os.path.join(config['root_dir'], config['desc'])
    config['targetdir'] = targetdir
    os.makedirs(targetdir, exist_ok=True)

    #copy results into the new directory
    fname = 'results{}.pickle'.format(config['bf_plot_id'])
    with open(os.path.join(targetdir, fname), 'wb+') as f:
        pickle.dump(results, f)

    for k in config.keys():
        val = config[k]
        if type(val) in [complex, np.complex128]:
            config[k] = val.real
    try:
        with open(os.path.join(targetdir, 'config.json'), 'w+') as f:
            json.dump(config, f)
    except Exception as e:
        print(e)

    #save images of results
    if config['mode'] == 'bif':
        vis.plot_bif_diag(results, skey, config, targetdir)
        #vis.plot_waterfall(results['axis'], results['fr'], config, skey)
    if config['mode'] == 'single':
        vis.plot_n_traces(results, config)

def dispatch_saved(fname, config):
    with open(fname, 'rb') as f:
        results = pickle.load(f)

    targetdir = os.path.dirname(os.path.abspath(fname))
    plot_image(results, config, targetdir)
    skey = detect_sweep_key(config)
    vis.plot_bif_diag(results, skey, config, targetdir)

def enumerate_configs(c):
    c['bf'] = 0
    c_mode = 0
    try:
        c_mode = c['c_mode']
    except KeyError:
        pass

    ekeys = []
    clist = []

    #check each item in config c and see if c is a sweep
    #check for enumerator
    for k in c.keys():
        if type(c[k]) == type([]):
            #edict[k] = c[k]
            ekeys.append(k)
        elif type(c[k]) == type({}):
            c['bf'] += 1

    if c_mode == 0:
        clist = enumerate_configs_mode_0(c, ekeys)
    else:
        clist = enumerate_configs_r(c_, ekeys)

    clist_ = []
    for c in clist:
        clist_ += split_config_by_plots(c)
    clist = clist_
    #return clist, edict, ekeys
    return clist, ekeys

#break a configuration based on equal length lists of n0, n1, ... parameters
#   such that these get dispersed as a list of n0 configurations with sub-lists
def enumerate_configs_mode_0(c, ekeys):
    if ekeys == []: return [c]

    lens = {}
    list_lens = []
    for e in ekeys:
        lste = c[e]
        lenk = str(len(lste))
        list_lens.append(len(lste))
        try:
            lens[lenk][e] = lste
        except KeyError:
            lens[lenk] = {e:lste}

    l = None
    ks = []
    for l in sorted(list_lens, reverse=True):
        ks = list(lens[str(l)].keys())
        if len(ks) > 1:
            break
    # l is now the first length of two or more lists of enumerable parameters

    ekeys_ = [e for e in ekeys if e not in ks]
    clist = []
    for en in range(l):
        d = dict(c)
        for k in ks:
            d[k] = lens[str(l)][k][en]
        clist += enumerate_configs_r(d, ekeys_)
    return clist

def enumerate_configs_r(c, ekeys):
    global current_cid
    if ekeys == []:
        d = c.copy()
        current_cid += 1
        d['c_id'] = current_cid
        return [d]

    clist = []
    for item in c[ekeys[0]]:
        d = c.copy()
        d[ekeys[0]] = item
        clist += enumerate_configs_r(d, ekeys[1:])
    return clist

def clean_config(c):
    for k in c.keys():
        if k in cc.known_str_params: continue
        if type(c[k]) == type(''):
            exec('c[\'{0}\']='.format(k) + c[k])
    cc.fix_config(c)

#def detect_sweep_key(c):
#    skey = detect_sweep_key_(c)
#    if type(skey) is type(None):
#        print('Found no sweep variable')
#    else:
#        print('Found sweep variable: {}'.format(skey))
#    return skey
#
def detect_sweep_key(c):
    for s in c.keys():
        if s in cc.known_dict_params: continue
        if type(c[s]) is dict: return s
    return None

def split_config_by_plots(c):
    num = c['bf_plot_num']
    c['bf_plot_id'] = 0
    if not c['bf'] or num == 1: return [c]

    skey = detect_sweep_key(c)
    outlist = []

    #get sweep params
    llim, ulim, dsweep = list(c[skey].values())[0]

    stype = list(c[skey].keys())[0]
    if stype not in ['arange', 'linspace']:
        raise NotImplementedError("Cannot use multiplotting with '{}' axis gen".format(stype))
    if stype == 'linspace':
        #convert to arange
        dsweep = (ulim-llim)/dsweep
        stype = 'arange'

    new_range_param = (ulim-llim)/num
    for i in range(num):
        factor = (i+1)*new_range_param
        new_sweep = {stype: [llim, factor, dsweep]}
        d = c.copy()
        d[skey] = new_sweep
        d['bf_plot_id'] = i
        outlist.append(d)
        llim = factor + dsweep

    return outlist

def run_threads(clist, target):
    #dispatch all threads
    threads = [Process(target=bf_dispatch, args=(model.setup, c)) for c in clist]
    for t in threads: t.start()
    for t in threads: t.join()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config_file', default='config.json',
                        help="The configuration file to load", dest='cfilename')
    parser.add_argument('-r', '--results', default=None,
                        help="The results file to load", dest='rfilename')
    parser.add_argument('-z', '--ez_name', default=None,
                        help="A friendly name for the folder", dest='ezname')
    #TODO: put an argument to either show or save the plot

    args = parser.parse_args()

    #import config file
    '''
      Configs hold all of the parameters of the system.
      There can be parameters which are varied:
        one parameter can be given as a tuple: (start, end, number_of_steps)
        this is what is used as the sweep parameter for a bifurcation diagram

        one parameter can be given as a [list] of several values
        one bifurcation diagram or plot will be created for each.  these will
        then be plotted together (and plots of each alone will also be saved)

        if no list or range parameters are given, a plot of all of the traces
        will be created and stored.
    '''
    config = None
    cnfname = args.cfilename
    if 'configs/' in cnfname:
        cnfname = os.path.basename(cnfname)
    try:
        with open('configs/' + cnfname, 'r') as fp:
            config = json.load(fp)
    except FileNotFoundError:
        config = cc.config
        print("Using default configuration")

    if args.rfilename:
        #either find a config based on the given results filename or by cfilen
        dispatch_saved(args.rfilename, cnfname)

    config['desc'] = cc.create_short_desc(config)
    foldername = config['desc']
    if args.ezname:
        config['ez_name'] = args.ezname
        foldername = config['ez_name']

    #create folder under results
    results_dirname = os.path.join('results', foldername)
    os.makedirs(results_dirname, exist_ok=True)
    #add the config to the results to make them reproducible
    with open(os.path.join(results_dirname, cnfname), 'w') as f:
        json.dump(config, f)
    #save this dir for future use
    config['root_dir'] = str(os.path.abspath(results_dirname))

    clist, ekeys = enumerate_configs(config)
    if ekeys != []:
        print('processing simulations for item in {}'.format(ekeys))

    if len(clist) == 1:
        bf_dispatch(model.setup, clist[0])
    else:
        ncpu = cpu_count()
        i = 0
        if len(clist) > ncpu:
            for i in range(0, len(clist), ncpu):
                if len(clist) - i < ncpu: break
                run_threads(clist[i:i+ncpu], bf_dispatch)
        run_threads(clist[i:], bf_dispatch)
    #TODO: combine all results files into one if it was split just on plot nums

    print("Done :)")
