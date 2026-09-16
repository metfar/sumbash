#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#pylint:disable=W0301
#
# Copyright 2018-2026 William Martinez Bas <metfar@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
from __future__ import annotations;

import argparse;
import os;
from pathlib import Path;
import shutil;
import sys;

from . import __version__;
from .applets import APPLETS, applet_names, run_applet;
from .shell import ShellExit, ShellRuntime;


PROGRAM_NAMES={"sumbash","sumbash.exe","sumbash.py","__main__.py"};


def invocation_name(argv0):
    name=Path(argv0).name;
    low=name.lower();
    if low.endswith(".exe"): low=low[:-4];
    if low.endswith(".py") and low not in PROGRAM_NAMES: low=low[:-3];
    return low;


def _show_help(file=sys.stdout):
    print("sumbash - portable SUM shell and multicall toolbox",file=file);
    print("",file=file);
    print("Usage:",file=file);
    print("  sumbash                         interactive shell",file=file);
    print("  sumbash -c 'COMMAND'            execute a command line",file=file);
    print("  sumbash SCRIPT [ARG ...]        execute a script",file=file);
    print("  sumbash APPLET [ARG ...]        execute an applet",file=file);
    print("  APPLET [ARG ...]                through symlink/hardlink/copy",file=file);
    print("",file=file);
    print("Options:",file=file);
    print("  --list-applets                  list portable applets",file=file);
    print("  --install-links DIR             install multicall command names",file=file);
    print("  --link-mode MODE                auto|symlink|hardlink|copy",file=file);
    print("  --remove-links DIR              remove links/copies installed by sumbash",file=file);
    print("  --version                       show version",file=file);
    print("",file=file);
    print("0.1.0a17 improves sourced startup scripts and interactive ANSI color defaults.",file=file);
    print("It includes indexed/associative arrays, for/while/until, if/elif/else, case,",file=file);
    print("shell functions with local/return, heredocs, braced groups, globbing, binary",file=file);
    print("pipelines, fractional arithmetic and external PATH fallback. Full Bash",file=file);
    print("compatibility, job control and complete option/error semantics are not claimed.",file=file);


def _copy_or_link(target,link,mode,force=False):
    if link.exists() or link.is_symlink():
        if not force: return False;
        if link.is_dir(): raise IsADirectoryError(str(link));
        link.unlink();
    attempts=[mode] if mode!="auto" else (["symlink","hardlink","copy"] if os.name!="nt" else ["hardlink","copy","symlink"]);
    last=None;
    for kind in attempts:
        try:
            if kind=="symlink": link.symlink_to(target);
            elif kind=="hardlink": os.link(target,link);
            elif kind=="copy": shutil.copy2(target,link);
            else: raise ValueError("unknown link mode: {}".format(kind));
            return True;
        except (OSError,ValueError) as exc: last=exc;
    if last: raise last;
    return False;


def install_links(directory,argv0,mode="auto",force=False):
    directory=Path(directory).expanduser().resolve(); directory.mkdir(parents=True,exist_ok=True);
    target=Path(argv0).expanduser().resolve();
    if not target.exists(): raise FileNotFoundError("cannot locate sumbash executable: {}".format(target));
    suffix=".exe" if target.suffix.lower()==".exe" else ""; created=0;
    for name in applet_names():
        # '[' and '[[' are valid Unix filenames, but skip them for Windows/copy installs.
        if os.name=="nt" and name in ("[","[["): continue;
        link=directory/(name+suffix);
        if _copy_or_link(target,link,mode,force=force): created+=1;
    print("Installed {} sumbash multicall names in {}.".format(created,directory),file=sys.stderr);
    return 0;


def remove_links(directory,argv0):
    directory=Path(directory).expanduser().resolve(); target=Path(argv0).expanduser().resolve(); suffix=".exe" if target.suffix.lower()==".exe" else ""; removed=0;
    for name in applet_names():
        p=directory/(name+suffix);
        if not p.exists() and not p.is_symlink(): continue;
        try:
            same=p.is_symlink() and p.resolve()==target;
            if not same and p.is_file(): same=p.stat().st_size==target.stat().st_size and p.read_bytes()==target.read_bytes();
            if same: p.unlink(); removed+=1;
        except OSError: pass;
    print("Removed {} sumbash multicall names from {}.".format(removed,directory),file=sys.stderr);
    return 0;


def _run_direct_applet(name,args):
    runtime=ShellRuntime(argv0=name);
    stream_applets={"cat","rev","grep","egrep","fgrep","cut","sed","head","tail","sort","uniq","wc","tee","less"};
    stdin=sys.stdin.read() if name in stream_applets and not sys.stdin.isatty() else "";
    result=run_applet(name,args,stdin=stdin,runtime=runtime);
    if result is None: return 127;
    if result.out: sys.stdout.write(result.out);
    if result.err: sys.stderr.write(result.err);
    return result.code;


def entry_point(arguments=None):
    argv0=sys.argv[0]; called=invocation_name(argv0); args=list(sys.argv[1:] if arguments is None else arguments);
    if called in APPLETS: return _run_direct_applet(called,args);
    if not args:
        return ShellRuntime(interactive=True,argv0="sumbash").interactive_loop();
    if args[0] in ("-h","--help"): _show_help(); return 0;
    if args[0]=="--version": print("sumbash {}".format(__version__)); return 0;
    if args[0]=="--list-applets": print("\n".join(applet_names())); return 0;
    if args[0] in ("--install-links","--remove-links"):
        action=args.pop(0);
        if not args: print("sumbash: {} requires DIR".format(action),file=sys.stderr); return 2;
        directory=args.pop(0); mode="auto"; force=False; i=0;
        while i<len(args):
            if args[i]=="--link-mode" and i+1<len(args): mode=args[i+1]; i+=2; continue;
            if args[i] in ("-f","--force"): force=True; i+=1; continue;
            i+=1;
        try: return install_links(directory,argv0,mode,force) if action=="--install-links" else remove_links(directory,argv0);
        except (OSError,ValueError) as exc: print("sumbash: {}".format(exc),file=sys.stderr); return 2;
    if args[0]=="--link-mode":
        print("sumbash: --link-mode is only valid with --install-links",file=sys.stderr); return 2;
    runtime=ShellRuntime(argv0="sumbash");
    try:
        if args[0]=="-c":
            if len(args)<2: print("sumbash: -c requires a command",file=sys.stderr); return 2;
            runtime.argv=args[2:]; result=runtime.run_line(args[1],capture=True);
        elif args[0] in APPLETS:
            return _run_direct_applet(args[0],args[1:]);
        else:
            result=runtime.run_script(args[0],args[1:]);
        if result.out:
            if isinstance(result.out,(bytes,bytearray)):
                sys.stdout.buffer.write(bytes(result.out)); sys.stdout.buffer.flush();
            else: sys.stdout.write(str(result.out));
        if result.err: sys.stderr.write(result.err);
        return result.code;
    except ShellExit as exc: return exc.code;


if __name__=="__main__": raise SystemExit(entry_point());
