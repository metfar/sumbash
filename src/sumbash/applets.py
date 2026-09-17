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
"""Portable applets for the first sumbash alpha.""";

from __future__ import annotations;

from dataclasses import dataclass;
from datetime import datetime, timedelta, timezone;
import fnmatch;
import getpass;
import math;
import os;
from pathlib import Path;
import re;
import shutil;
import shlex;
import stat;
import socket;
import sys;
import time;

try:
    import pwd as _pwd;
except ImportError:
    _pwd=None;
try:
    import grp as _grp;
except ImportError:
    _grp=None;

from sumcore import info as suminfo;
from . import __version__ as _sumbash_version;


@dataclass
class AppletResult:
    code: int = 0;
    out: str = "";
    err: str = "";


def _text_input(stdin): return "" if stdin is None else str(stdin);

def _runtime_path(value, runtime=None):
    if runtime is not None and hasattr(runtime,"fsa"):
        try: return Path(runtime.fsa.native_path(value,cwd=getattr(runtime,"logical_cwd",runtime.fsa.cwd)));
        except Exception: pass;
    path=Path(value);
    if path.is_absolute(): return path;
    base=Path(runtime.cwd if runtime is not None else os.getcwd());
    return base/path;


def _read_paths(paths, stdin="", runtime=None):
    if not paths: return [("-", _text_input(stdin))];
    rows=[];
    for value in paths:
        if value=="-": rows.append(("-",_text_input(stdin))); continue;
        try: rows.append((value,_runtime_path(value,runtime).read_text(encoding="utf-8",errors="replace")));
        except OSError as exc: rows.append((value,exc));
    return rows;


def app_echo(argv, stdin="", runtime=None):
    newline = True; interpret = False; args = list(argv);
    while args and args[0] in ("-n", "-e", "-E"):
        opt = args.pop(0);
        if opt == "-n": newline = False;
        elif opt == "-e": interpret = True;
        elif opt == "-E": interpret = False;
    text = " ".join(args);
    if interpret:
        try: text = bytes(text, "utf-8").decode("unicode_escape");
        except UnicodeDecodeError: pass;
    return AppletResult(out=text + ("\n" if newline else ""));


def app_printf(argv, stdin="", runtime=None):
    if not argv: return AppletResult();
    fmt = argv[0]; args = list(argv[1:]);
    try:
        # Close enough for the common shell printf surface in this alpha.
        converted = [];
        for value in args:
            try: converted.append(int(value, 0));
            except ValueError:
                try: converted.append(float(value));
                except ValueError: converted.append(value);
        if converted: text = fmt % tuple(converted);
        else: text = fmt;
        text = bytes(text, "utf-8").decode("unicode_escape");
        return AppletResult(out=text);
    except Exception as exc: return AppletResult(2, err="printf: {}\n".format(exc));


def _cat_visible(text,show_ends=False,show_tabs=False):
    out=[];
    for char in str(text or ""):
        code=ord(char);
        if char=="\n": out.append("$\n" if show_ends else "\n"); continue;
        if char=="\t": out.append("^I" if show_tabs else "\t"); continue;
        if code<32: out.append("^"+chr(code+64)); continue;
        if code==127: out.append("^?"); continue;
        if 128<=code<=159: out.append("M-^"+chr(code-64)); continue;
        if 160<=code<=255: out.append("M-"+chr(code-128)); continue;
        out.append(char);
    return "".join(out);


def app_cat(argv, stdin="", runtime=None):
    # GNU-compatible diagnostic options are intentionally useful in a shell:
    # `cat -v` makes terminal escape/control bytes readable as ^[... text.
    args=list(argv); show_nonprinting=False; show_ends=False; show_tabs=False; operands=[]; options=True;
    for arg in args:
        if options and arg=="--": options=False; continue;
        if options and arg in ("--show-nonprinting","--show-ends","--show-tabs"):
            if arg=="--show-nonprinting": show_nonprinting=True;
            elif arg=="--show-ends": show_ends=True;
            else: show_tabs=True;
            continue;
        if options and arg.startswith("-") and arg not in ("-",):
            if arg=="--help": return AppletResult(out="Usage: cat [OPTION]... [FILE]...\n  -v, --show-nonprinting\n  -E, --show-ends\n  -T, --show-tabs\n  -A  equivalent to -vET\n");
            if arg in ("-A","-e","-t"):
                show_nonprinting=True; show_ends=show_ends or arg in ("-A","-e"); show_tabs=show_tabs or arg in ("-A","-t"); continue;
            short=arg[1:];
            if short and all(ch in "vET" for ch in short):
                show_nonprinting=show_nonprinting or "v" in short; show_ends=show_ends or "E" in short; show_tabs=show_tabs or "T" in short; continue;
            return AppletResult(1,err="cat: unrecognized option '{}'\n".format(arg));
        operands.append(arg);
    # With no operands, cat reads standard input.  When sumbash owns an
    # interactive terminal this deliberately reads the real TTY until EOF.
    if not operands and runtime is not None and getattr(runtime,"_command_stdin_is_tty",False):
        try: stdin=sys.stdin.read();
        except (EOFError,KeyboardInterrupt): stdin="";
    out=[]; err=[]; code=0;
    for name,value in _read_paths(operands,stdin,runtime):
        if isinstance(value,Exception): err.append("cat: {}: {}\n".format(name,value)); code=1;
        elif show_nonprinting or show_ends or show_tabs: out.append(_cat_visible(value,show_ends=show_ends,show_tabs=show_tabs));
        else: out.append(value);
    return AppletResult(code,"".join(out),"".join(err));


def app_rev(argv, stdin="", runtime=None):
    out=[]; err=[]; code=0;
    for name, value in _read_paths(argv,stdin,runtime):
        if isinstance(value, Exception): err.append("rev: {}: {}\n".format(name, value)); code=1; continue;
        for line in value.splitlines(True):
            ending = "\n" if line.endswith("\n") else "";
            body = line[:-1] if ending else line;
            out.append(body[::-1] + ending);
    return AppletResult(code, "".join(out), "".join(err));


def app_basename(argv, stdin="", runtime=None):
    if not argv: return AppletResult(1, err="basename: missing operand\n");
    value = os.path.basename(argv[0].rstrip(os.sep)) or os.sep;
    if len(argv) > 1 and value.endswith(argv[1]): value = value[:-len(argv[1])];
    return AppletResult(out=value + "\n");


def app_dirname(argv, stdin="", runtime=None):
    if not argv: return AppletResult(1, err="dirname: missing operand\n");
    return AppletResult(out=(os.path.dirname(argv[0]) or ".") + "\n");


def app_pwd(argv, stdin="", runtime=None):
    if runtime is not None and hasattr(runtime,"logical_cwd"): return AppletResult(out=str(runtime.logical_cwd)+"\n");
    physical="-P" in argv; cwd=Path(runtime.cwd if runtime is not None else os.getcwd()); value=str(cwd.resolve()) if physical else str(cwd); return AppletResult(out=value+"\n");


def app_realpath(argv, stdin="", runtime=None):
    if not argv: argv=["."];
    if runtime is not None and hasattr(runtime,"fsa"):
        return AppletResult(out="\n".join(runtime.fsa.normalize(raw,cwd=runtime.logical_cwd) for raw in argv)+"\n");
    out=[]; base=Path(runtime.cwd if runtime is not None else os.getcwd());
    for raw in argv:
        p=Path(raw); p=p if p.is_absolute() else base/p; out.append(str(p.resolve()));
    return AppletResult(out="\n".join(out)+"\n");


def app_clear(argv, stdin="", runtime=None): return AppletResult(out="\x1b[H\x1b[J");

def app_sleep(argv, stdin="", runtime=None):
    if not argv: return AppletResult(1, err="sleep: missing operand\n");
    try: time.sleep(float(argv[0])); return AppletResult();
    except ValueError: return AppletResult(1, err="sleep: invalid time interval\n");


def _human_size(value,base=1024):
    """GNU-ls-like compact size used by the portable ls applet.

    Bytes below the first unit are shown without a ``B`` suffix.  Values below
    10 units retain one decimal place (``4.0K``); larger values are rounded to
    the nearest whole unit (``67K``).
    """;
    n=float(max(0,value));
    if n<base: return str(int(n));
    suffixes=("K","M","G","T","P","E");
    for suffix in suffixes:
        n/=base;
        if n<base or suffix==suffixes[-1]:
            if n<10: return "{:.1f}{}".format(n,suffix);
            return "{}{}".format(int(math.floor(n+0.5)),suffix);
    return str(int(value));


_DEFAULT_LS_COLORS="rs=0:di=01;34:ln=01;36:mh=00:pi=33:so=01;35:do=01;35:bd=01;33:cd=01;33:or=01;31:mi=00:su=37;41:sg=30;43:ca=00:tw=30;42:ow=34;42:st=37;44:ex=01;32:*.tar=01;31:*.tgz=01;31:*.gz=01;31:*.bz2=01;31:*.xz=01;31:*.zip=01;31:*.7z=01;31:*.png=01;35:*.jpg=01;35:*.jpeg=01;35:*.gif=01;35:*.svg=01;35:*.mp3=00;36:*.wav=00;36:*.mp4=01;35:*.mkv=01;35";


def _parse_ls_colors(value):
    result={};
    for entry in str(value or "").split(":"):
        if "=" not in entry: continue;
        key,code=entry.split("=",1);
        if key: result[key]=code;
    return result;


def _ls_color_key(path, colors):
    try:
        if path.is_symlink():
            try:
                if not path.exists() and "or" in colors: return "or";
            except OSError: pass;
            return "ln";
        if path.is_dir(): return "di";
        if path.is_fifo(): return "pi";
        if path.is_socket(): return "so";
        if path.is_block_device(): return "bd";
        if path.is_char_device(): return "cd";
        if path.is_file():
            if os.access(path,os.X_OK) and "ex" in colors: return "ex";
            name=path.name;
            for key in colors:
                if key.startswith("*") and fnmatch.fnmatchcase(name,key): return key;
            return "fi" if "fi" in colors else "rs";
    except OSError:
        return "mi" if "mi" in colors else "rs";
    return "rs";


def _ls_colored_name(path, name, colors, enabled):
    if not enabled or not colors: return name;
    key=_ls_color_key(path,colors); code=colors.get(key,"");
    if not code or code in ("0","00"): return name;
    return "\x1b[{}m{}\x1b[0m".format(code,name);


def _ls_parse_block_size(text):
    raw=str(text).strip();
    m=re.fullmatch(r"([0-9]+)([KMGTEP]?)(i?B)?",raw,re.I);
    if not m: raise ValueError("invalid block size: {}".format(text));
    value=int(m.group(1)); suffix=m.group(2).upper(); unit=(m.group(3) or "");
    power={"":0,"K":1,"M":2,"G":3,"T":4,"P":5,"E":6}[suffix];
    base=1024 if unit.lower()=="ib" or unit=="" else 1000;
    return value*(base**power);


def _ls_owner(uid,numeric=False):
    if numeric or _pwd is None: return str(uid);
    try: return _pwd.getpwuid(uid).pw_name;
    except (KeyError,OSError): return str(uid);


def _ls_group(gid,numeric=False):
    if numeric or _grp is None: return str(gid);
    try: return _grp.getgrgid(gid).gr_name;
    except (KeyError,OSError): return str(gid);


def _ls_quote(name,style,hide_controls=False,show_controls=False):
    value=str(name);
    if hide_controls and not show_controls:
        value="".join(ch if (ch.isprintable() or ch in "\t") else "?" for ch in value);
    style=style or "literal";
    if style in ("literal","locale","clocale"): return value;
    if style in ("shell","shell-always"): return shlex.quote(value);
    if style in ("shell-escape","shell-escape-always","escape"):
        out=[];
        for ch in value:
            code=ord(ch);
            if ch==" ": out.append("\\ ");
            elif ch in "\\\"'`$&;|<>*?[](){}!": out.append("\\"+ch);
            elif ch=="\n": out.append("\\n");
            elif ch=="\t": out.append("\\t");
            elif code<32 or code==127: out.append("\\x{:02x}".format(code));
            else: out.append(ch);
        return "".join(out);
    if style in ("c",):
        escaped=value.encode("unicode_escape").decode("ascii").replace('"','\\"');
        return '"{}"'.format(escaped);
    return value;


def _ls_indicator(path,style):
    if style in (None,"none","never"): return "";
    try:
        if path.is_dir(): return "/";
        if path.is_symlink(): return "@";
        if path.is_fifo(): return "|";
        if path.is_socket(): return "=";
        if style=="classify" and path.is_file() and os.access(path,os.X_OK): return "*";
    except OSError: pass;
    return "";


def _ls_time_value(st,field):
    field=(field or "mtime").lower();
    if field in ("atime","access","use"): return st.st_atime;
    if field in ("ctime","status"): return st.st_ctime;
    if field in ("birth","creation"):
        return getattr(st,"st_birthtime",st.st_ctime);
    return st.st_mtime;


def _ls_time_text(ts,style):
    dt=datetime.fromtimestamp(ts).astimezone(); style=style or "locale";
    if style.startswith("+"): return dt.strftime(style[1:]);
    if style=="full-iso": return dt.strftime("%Y-%m-%d %H:%M:%S.%f %z");
    if style=="long-iso": return dt.strftime("%Y-%m-%d %H:%M");
    if style=="iso": return dt.strftime("%Y-%m-%d %H:%M");
    return dt.strftime("%b %d %H:%M").replace(" 0","  ");


def _ls_version_key(value):
    return tuple(int(x) if x.isdigit() else x.casefold() for x in re.split(r"([0-9]+)",str(value)));


def _ls_help():
    return """List directory contents.\nIgnore files and directories starting with a '.' by default\n\nUsage: ls [OPTION]... [FILE]...\n\nPortable SUM ls options include:\n  -C, -1, -x, -m, -l, -o, -g, -n, -a, -A, -d, -R\n  -h, --si, -i, -s, -r, -S, -t, -v, -X, -U, -F, -p\n  -L, -H, -B, -I PATTERN, --hide PATTERN\n  --color[=always|auto|never], --sort=FIELD, --time=FIELD\n  --time-style=STYLE, --full-time, --group-directories-first\n  --indicator-style=STYLE, --file-type, --quoting-style=STYLE\n  --hyperlink[=always|auto|never], --zero, --block-size=SIZE\n  -w COLS, -T COLS, -Z, --author, --version, --help\n""";


def app_ls(argv, stdin="", runtime=None):
    opts={
        "show_all":False,"almost_all":False,"long":False,"human":False,"si":False,
        "format":None,"no_owner":False,"no_group":False,"numeric":False,"classify":None,
        "paths":[],"color":None,"sort":"name","reverse":False,"recursive":False,
        "directory":False,"dereference":False,"deref_cmd":False,"deref_cmd_dir":False,
        "inode":False,"blocks":False,"block_size":1024,"size_scale":1,"width":None,"tabsize":8,
        "ignore":[],"hide":[],"ignore_backups":False,"group_dirs":False,
        "time_field":"mtime","time_style":None,"quote":"literal","hide_controls":False,
        "show_controls":False,"hyperlink":"never","zero":False,"context":False,
        "author":False,"dired":False,"color_explicit":False,
    };
    args=list(argv); i=0;
    def need(opt):
        nonlocal i;
        if i+1>=len(args): raise ValueError("ls: option {} requires an argument".format(opt));
        i+=1; return args[i];
    try:
        while i<len(args):
            arg=args[i];
            if arg=="--": opts["paths"].extend(args[i+1:]); break;
            if arg in ("--help",): return AppletResult(out=_ls_help());
            if arg in ("--version","-V"): return AppletResult(out="sumbash ls {}\n".format(_sumbash_version));
            if arg.startswith("--"):
                key,val=(arg[2:].split("=",1)+[None])[:2] if "=" in arg else (arg[2:],None);
                if key=="format": opts["format"]=val or need(arg);
                elif key=="long": opts["long"]=True;
                elif key=="tabsize": opts["tabsize"]=int(val or need(arg));
                elif key=="zero": opts["zero"]=True; opts["format"]="zero";
                elif key=="dired": opts["dired"]=True;
                elif key=="hyperlink": opts["hyperlink"]=(val or "always").lower();
                elif key=="numeric-uid-gid": opts["numeric"]=True; opts["long"]=True;
                elif key=="quoting-style": opts["quote"]=val or need(arg);
                elif key=="literal": opts["quote"]="literal";
                elif key=="escape": opts["quote"]="escape";
                elif key=="quote-name": opts["quote"]="c";
                elif key=="hide-control-chars": opts["hide_controls"]=True;
                elif key=="show-control-chars": opts["show_controls"]=True;
                elif key=="time": opts["time_field"]=(val or need(arg));
                elif key=="hide": opts["hide"].append(val or need(arg));
                elif key=="ignore": opts["ignore"].append(val or need(arg));
                elif key=="ignore-backups": opts["ignore_backups"]=True;
                elif key=="sort": opts["sort"]=(val or need(arg)).lower();
                elif key=="dereference": opts["dereference"]=True;
                elif key=="dereference-command-line": opts["deref_cmd"]=True;
                elif key=="dereference-command-line-symlink-to-dir": opts["deref_cmd_dir"]=True;
                elif key=="no-group": opts["no_group"]=True;
                elif key=="author": opts["author"]=True; opts["long"]=True;
                elif key=="all": opts["show_all"]=True;
                elif key=="almost-all": opts["almost_all"]=True;
                elif key=="directory": opts["directory"]=True;
                elif key=="human-readable": opts["human"]=True;
                elif key=="kibibytes": opts["block_size"]=1024;
                elif key=="si": opts["si"]=True; opts["human"]=True;
                elif key=="block-size":
                    opts["block_size"]=_ls_parse_block_size(val or need(arg)); opts["size_scale"]=opts["block_size"];
                elif key=="inode": opts["inode"]=True;
                elif key=="reverse": opts["reverse"]=True;
                elif key=="recursive": opts["recursive"]=True;
                elif key=="width": opts["width"]=int(val or need(arg));
                elif key=="size": opts["blocks"]=True;
                elif key=="color": opts["color"]=(val or "always").lower(); opts["color_explicit"]=True;
                elif key=="indicator-style": opts["classify"]=(val or need(arg)).lower();
                elif key=="classify": opts["classify"]=(val or "classify").lower();
                elif key=="file-type": opts["classify"]="file-type";
                elif key=="time-style": opts["time_style"]=(val or need(arg));
                elif key=="full-time": opts["long"]=True; opts["time_style"]="full-iso";
                elif key=="context": opts["context"]=True; opts["long"]=True;
                elif key=="group-directories-first": opts["group_dirs"]=True;
                else: return AppletResult(2,err="ls: unrecognized option '--{}'\n".format(key));
                i+=1; continue;
            if arg.startswith("-") and arg!="-":
                cluster=arg[1:]; j=0;
                while j<len(cluster):
                    ch=cluster[j];
                    if ch in ("T","w","I"):
                        value=cluster[j+1:] if j+1<len(cluster) else need("-"+ch);
                        if ch=="T": opts["tabsize"]=int(value);
                        elif ch=="w": opts["width"]=int(value);
                        else: opts["ignore"].append(value);
                        j=len(cluster); continue;
                    if ch=="C": opts["format"]="columns";
                    elif ch=="l": opts["long"]=True;
                    elif ch=="x": opts["format"]="rows";
                    elif ch=="m": opts["format"]="commas";
                    elif ch=="1": opts["format"]="single";
                    elif ch=="o": opts["long"]=True; opts["no_group"]=True;
                    elif ch=="g": opts["long"]=True; opts["no_owner"]=True;
                    elif ch=="n": opts["long"]=True; opts["numeric"]=True;
                    elif ch=="N": opts["quote"]="literal";
                    elif ch=="b": opts["quote"]="escape";
                    elif ch=="Q": opts["quote"]="c";
                    elif ch=="q": opts["hide_controls"]=True;
                    elif ch=="c": opts["time_field"]="ctime";
                    elif ch=="u": opts["time_field"]="atime";
                    elif ch=="B": opts["ignore_backups"]=True;
                    elif ch=="S": opts["sort"]="size";
                    elif ch=="t": opts["sort"]="time";
                    elif ch=="v": opts["sort"]="version";
                    elif ch=="X": opts["sort"]="extension";
                    elif ch=="U": opts["sort"]="none";
                    elif ch=="L": opts["dereference"]=True;
                    elif ch=="H": opts["deref_cmd"]=True;
                    elif ch=="G": opts["no_group"]=True;
                    elif ch=="a": opts["show_all"]=True;
                    elif ch=="A": opts["almost_all"]=True;
                    elif ch=="f": opts["show_all"]=True; opts["sort"]="none"; opts["group_dirs"]=False;
                    elif ch=="d": opts["directory"]=True;
                    elif ch=="h": opts["human"]=True;
                    elif ch=="k": opts["block_size"]=1024;
                    elif ch=="i": opts["inode"]=True;
                    elif ch=="r": opts["reverse"]=True;
                    elif ch=="R": opts["recursive"]=True;
                    elif ch=="s": opts["blocks"]=True;
                    elif ch=="F": opts["classify"]="classify";
                    elif ch=="p": opts["classify"]="slash";
                    elif ch=="Z": opts["context"]=True; opts["long"]=True;
                    elif ch=="D": opts["dired"]=True;
                    else: return AppletResult(2,err="ls: invalid option -- '{}'\n".format(ch));
                    j+=1;
                i+=1; continue;
            opts["paths"].append(arg); i+=1;
    except (ValueError,TypeError) as exc:
        return AppletResult(2,err="{}\n".format(exc));
    if opts["color"] not in (None,"always","auto","never"): return AppletResult(2,err="ls: invalid --color value\n");
    if opts["hyperlink"] not in ("always","auto","never"): return AppletResult(2,err="ls: invalid --hyperlink value\n");
    if opts["sort"] not in ("name","none","time","size","version","extension","width"): return AppletResult(2,err="ls: invalid --sort value\n");
    if opts["format"] is not None:
        aliases={"vertical":"columns","across":"rows","horizontal":"rows","commas":"commas","long":"long","verbose":"long","single-column":"single"};
        opts["format"]=aliases.get(opts["format"],opts["format"]);
        if opts["format"]=="long": opts["long"]=True;
        if opts["format"] not in ("columns","rows","commas","long","single","zero"): return AppletResult(2,err="ls: invalid --format value\n");
    if not opts["paths"]: opts["paths"]=["."];
    env=(runtime.vars if runtime is not None else os.environ); ls_colors=env.get("LS_COLORS","") or _DEFAULT_LS_COLORS; colors=_parse_ls_colors(ls_colors);
    if opts["color"] is None: opts["color"]="auto";
    if opts["sort"]=="none" and not opts["color_explicit"] and "f" in "".join(a[1:] for a in argv if a.startswith("-") and not a.startswith("--")): opts["color"]="never";
    output_tty=getattr(runtime,"_command_stdout_is_tty",None) if runtime is not None else None;
    if output_tty is None:
        try: output_tty=sys.stdout.isatty();
        except Exception: output_tty=False;
    use_color=(opts["color"]=="always" or (opts["color"]=="auto" and bool(output_tty)));
    use_hyper=(opts["hyperlink"]=="always" or (opts["hyperlink"]=="auto" and bool(output_tty)));
    if opts["format"] is None: opts["format"]="columns" if output_tty and not opts["long"] else "single";
    if opts["long"]: opts["format"]="long";
    if opts["zero"]: opts["format"]="zero";
    if opts["time_style"] is None: opts["time_style"]=env.get("TIME_STYLE") or "locale";
    width=opts["width"] or shutil.get_terminal_size((80,24)).columns;
    base=Path(runtime.cwd if runtime is not None else os.getcwd()); out=[]; err=[]; code=0;

    def make_entry(path,name=None,cmdline=False):
        display=name if name is not None else path.name;
        try: st=path.stat() if opts["dereference"] or (cmdline and opts["deref_cmd"]) else path.lstat();
        except OSError as exc: return {"path":path,"name":display,"error":exc,"cmdline":cmdline};
        return {"path":path,"name":display,"st":st,"cmdline":cmdline};

    def entry_is_dir(entry,for_walk=False):
        path=entry["path"];
        try:
            if path.is_symlink() and not opts["dereference"]:
                if entry.get("cmdline") and (opts["deref_cmd"] or opts["deref_cmd_dir"]): return path.resolve().is_dir();
                return False;
            return path.is_dir();
        except OSError: return False;

    def visible(entry):
        name=entry["name"];
        if name in (".",".."): return opts["show_all"];
        if name.startswith(".") and not (opts["show_all"] or opts["almost_all"]): return False;
        if opts["ignore_backups"] and name.endswith("~"): return False;
        if any(fnmatch.fnmatchcase(name,p) for p in opts["ignore"]): return False;
        if not (opts["show_all"] or opts["almost_all"]) and any(fnmatch.fnmatchcase(name,p) for p in opts["hide"]): return False;
        return True;

    def sort_entries(entries):
        if opts["sort"]=="none": ordered=list(entries);
        else:
            def key(e):
                st=e.get("st"); name=e["name"];
                if opts["sort"]=="size": return st.st_size if st else -1;
                if opts["sort"]=="time": return _ls_time_value(st,opts["time_field"]) if st else 0;
                if opts["sort"]=="version": return _ls_version_key(name);
                if opts["sort"]=="extension": return (Path(name).suffix.casefold(),name.casefold());
                if opts["sort"]=="width": return (len(name),name.casefold());
                return name.casefold();
            ordered=sorted(entries,key=key,reverse=opts["reverse"]);
            if opts["sort"] in ("size","time") and not opts["reverse"]: ordered.reverse();
        if opts["sort"]=="none" and opts["reverse"]: ordered.reverse();
        if opts["group_dirs"] and opts["sort"]!="none":
            dirs=[e for e in ordered if entry_is_dir(e)]; files=[e for e in ordered if not entry_is_dir(e)]; ordered=dirs+files;
        return ordered;

    def decorated(entry):
        path=entry["path"]; name=_ls_quote(entry["name"],opts["quote"],opts["hide_controls"],opts["show_controls"]);
        suffix="";
        style=opts["classify"];
        if style=="slash": suffix="/" if entry_is_dir(entry) else "";
        elif style in ("file-type","classify","always","auto"):
            if style!="auto" or output_tty: suffix=_ls_indicator(path,"classify" if style in ("classify","always","auto") else "file-type");
        shown=_ls_colored_name(path,name,colors,use_color)+suffix;
        if use_hyper:
            try: shown="\x1b]8;;{}\x1b\\{}\x1b]8;;\x1b\\".format(path.resolve().as_uri(),shown);
            except (OSError,ValueError): pass;
        return shown;

    def scaled_size(value):
        if opts["human"]: return _human_size(value,1000 if opts["si"] else 1024);
        scale=opts["size_scale"];
        if scale!=1: return str((int(value)+scale-1)//scale);
        return str(value);

    def block_count(st):
        raw=getattr(st,"st_blocks",(st.st_size+511)//512)*512;
        return (raw+opts["block_size"]-1)//opts["block_size"];

    def security_context(path):
        if hasattr(os,"getxattr"):
            try: return os.getxattr(path,"security.selinux",follow_symlinks=opts["dereference"]).decode(errors="replace").rstrip("\x00");
            except (OSError,ValueError,TypeError): pass;
        return "?";

    def allocated_bytes(st):
        return getattr(st,"st_blocks",(st.st_size+511)//512)*512;

    def block_text(st):
        if opts["human"]: return _human_size(allocated_bytes(st),1000 if opts["si"] else 1024);
        return str(block_count(st));

    def long_record(entry):
        if "error" in entry: return None;
        st=entry["st"]; owner=_ls_owner(getattr(st,"st_uid",0),opts["numeric"]); group=_ls_group(getattr(st,"st_gid",0),opts["numeric"]);
        name=decorated(entry);
        if entry["path"].is_symlink() and not opts["dereference"]:
            try: name += " -> "+_ls_quote(os.readlink(entry["path"]),opts["quote"]);
            except OSError: pass;
        return {
            "inode":str(st.st_ino),
            "blocks":block_text(st),
            "mode":stat.filemode(st.st_mode),
            "nlink":str(getattr(st,"st_nlink",1)),
            "owner":owner,
            "group":group,
            "author":owner,
            "context":security_context(entry["path"]),
            "size":scaled_size(st.st_size),
            "time":_ls_time_text(_ls_time_value(st,opts["time_field"]),opts["time_style"]),
            "name":name,
        };

    def long_lines(entries):
        records=[r for r in (long_record(e) for e in entries) if r is not None];
        if not records: return [];
        numeric_fields=("inode","blocks","nlink","size");
        text_fields=("owner","group","author","context");
        widths={key:max(len(r[key]) for r in records) for key in numeric_fields+text_fields};
        lines=[];
        for r in records:
            fields=[];
            if opts["inode"]: fields.append(r["inode"].rjust(widths["inode"]));
            if opts["blocks"]: fields.append(r["blocks"].rjust(widths["blocks"]));
            fields.append(r["mode"]);
            fields.append(r["nlink"].rjust(widths["nlink"]));
            if not opts["no_owner"]: fields.append(r["owner"].ljust(widths["owner"]));
            if not opts["no_group"]: fields.append(r["group"].ljust(widths["group"]));
            if opts["author"]: fields.append(r["author"].ljust(widths["author"]));
            if opts["context"]: fields.append(r["context"].ljust(widths["context"]));
            fields.append(r["size"].rjust(widths["size"]));
            fields.append(r["time"]);
            fields.append(r["name"]);
            lines.append(" ".join(fields));
        return lines;

    def total_text(entries):
        if opts["human"]:
            total=sum(allocated_bytes(e["st"]) for e in entries if "st" in e);
            return _human_size(total,1000 if opts["si"] else 1024);
        return str(sum(block_count(e["st"]) for e in entries if "st" in e));

    def plain_lines(entries):
        names=[];
        for e in entries:
            prefix=[];
            if "error" in e: continue;
            st=e["st"];
            if opts["inode"]: prefix.append(str(st.st_ino));
            if opts["blocks"]: prefix.append(str(block_count(st)));
            prefix.append(decorated(e)); names.append((" ".join(prefix),len(" ".join(prefix[:-1]+[_ls_quote(e["name"],opts["quote"])]))));
        fmt=opts["format"];
        if fmt=="zero": return "\0".join(v for v,w in names)+("\0" if names else "");
        if fmt=="commas": return ", ".join(v for v,w in names)+("\n" if names else "");
        if fmt=="single": return "".join(v+"\n" for v,w in names);
        if not names: return "";
        maxw=max(w for v,w in names)+2; cols=max(1,width//maxw); rows=(len(names)+cols-1)//cols;
        lines=[];
        if fmt=="rows":
            for r in range(rows):
                chunk=names[r*cols:(r+1)*cols]; lines.append("".join(v+(" "*max(0,maxw-w) if j<len(chunk)-1 else "") for j,(v,w) in enumerate(chunk)).rstrip());
        else:
            for r in range(rows):
                cells=[];
                for c in range(cols):
                    idx=c*rows+r;
                    if idx>=len(names): continue;
                    v,w=names[idx]; cells.append(v+(" "*max(0,maxw-w) if c<cols-1 else ""));
                lines.append("".join(cells).rstrip());
        return "\n".join(lines)+"\n";

    def list_directory(path,label,show_header,recursive=False):
        nonlocal code;
        try:
            raw=[make_entry(child,child.name) for child in path.iterdir()];
            if opts["show_all"]: raw=[make_entry(path,"."),make_entry(path.parent,"..")] + raw;
        except OSError as exc:
            err.append("ls: {}: {}\n".format(label,exc)); code=2; return;
        entries=[];
        for e in raw:
            if "error" in e: err.append("ls: {}: {}\n".format(e["path"],e["error"])); code=max(code,1); continue;
            if visible(e): entries.append(e);
        entries=sort_entries(entries);
        if show_header: out.append("{}:\n".format(label));
        if opts["long"] or opts["blocks"]:
            out.append("total {}\n".format(total_text(entries)));
        if opts["long"]:
            out.extend(line+"\n" for line in long_lines(entries));
        else: out.append(plain_lines(entries));
        if recursive:
            subs=[e for e in entries if e["name"] not in (".","..") and entry_is_dir(e,for_walk=True)];
            for e in subs:
                out.append("\n"); list_directory(e["path"],str(e["path"]),True,True);

    operands=[];
    for raw in opts["paths"]:
        p=Path(raw).expanduser(); p=p if p.is_absolute() else base/p; e=make_entry(p,raw,cmdline=True);
        if "error" in e: err.append("ls: {}: {}\n".format(raw,e["error"])); code=2; continue;
        operands.append(e);
    files=[]; dirs=[];
    for e in operands:
        if not opts["directory"] and entry_is_dir(e): dirs.append(e);
        else: files.append(e);
    if files:
        files=sort_entries(files);
        if opts["long"]:
            out.extend(line+"\n" for line in long_lines(files));
        else: out.append(plain_lines(files));
        if dirs: out.append("\n");
    for idx,e in enumerate(dirs):
        label=e["name"]; header=len(dirs)>1 or bool(files) or opts["recursive"];
        list_directory(e["path"],label,header,opts["recursive"]);
        if idx+1<len(dirs): out.append("\n");
    # --dired is accepted for script compatibility; offsets are intentionally not
    # emitted yet because ANSI/hyperlink-aware byte offsets need a dedicated pass.
    return AppletResult(code,"".join(out),"".join(err));

def app_find(argv, stdin="", runtime=None):
    args=list(argv); roots=[];
    while args and not args[0].startswith("-"): roots.append(args.pop(0));
    if not roots: roots=["."];
    name_pat=None; insensitive=False; ftype=None; maxdepth=None; action="print"; printf_fmt=None;
    i=0;
    while i<len(args):
        a=args[i];
        if a in ("-name","-iname") and i+1<len(args): name_pat=args[i+1]; insensitive=a=="-iname"; i+=2; continue;
        if a=="-type" and i+1<len(args): ftype=args[i+1]; i+=2; continue;
        if a=="-maxdepth" and i+1<len(args): maxdepth=int(args[i+1]); i+=2; continue;
        if a=="-print": action="print"; i+=1; continue;
        if a=="-print0": action="print0"; i+=1; continue;
        if a=="-printf" and i+1<len(args): action="printf"; printf_fmt=args[i+1]; i+=2; continue;
        i+=1;
    cwd=Path(runtime.cwd if runtime is not None else os.getcwd()); out=[]; err=[]; code=0;

    def render_printf(fmt,p,shown):
        try: st=p.lstat();
        except OSError: st=None;
        parent=str(Path(shown).parent);
        if parent==".": parent=".";
        mapping={"%p":shown,"%f":p.name,"%h":parent,"%s":str(st.st_size if st else 0),"%m":format((st.st_mode & 0o7777) if st else 0,"o")};
        value=str(fmt);
        for key,replacement in mapping.items(): value=value.replace(key,replacement);
        value=value.replace("%%","%");
        # Decode only the portable escapes useful in find -printf, preserving UTF-8 paths.
        escapes={r"\n":"\n",r"\t":"\t",r"\r":"\r",r"\0":"\0",r"\\":"\\"};
        for old_escape,new_escape in escapes.items(): value=value.replace(old_escape,new_escape);
        return value;
    for raw in roots:
        root=Path(raw); root=root if root.is_absolute() else cwd/root;
        if not root.exists(): err.append("find: {}: No such file or directory\n".format(raw)); code=1; continue;
        root_depth=len(root.parts);
        candidates=[root];
        if root.is_dir():
            try: candidates.extend(root.rglob("*"));
            except OSError as exc: err.append("find: {}: {}\n".format(raw,exc)); code=1;
        for p in candidates:
            depth=len(p.parts)-root_depth;
            if maxdepth is not None and depth>maxdepth: continue;
            if ftype=="f" and not p.is_file(): continue;
            if ftype=="d" and not p.is_dir(): continue;
            if ftype=="l" and not p.is_symlink(): continue;
            if name_pat:
                n=p.name; pat=name_pat;
                if insensitive: n=n.casefold(); pat=pat.casefold();
                if not fnmatch.fnmatch(n,pat): continue;
            try: shown=str(p.relative_to(cwd));
            except ValueError: shown=str(p);
            if raw=="." and not shown.startswith("."): shown="./"+shown;
            if action=="print0": out.append(shown+"\0");
            elif action=="printf": out.append(render_printf(printf_fmt or "",p,shown));
            else: out.append(shown+"\n");
    return AppletResult(code,"".join(out),"".join(err));


def app_grep(argv, stdin="", runtime=None):
    args=list(argv); ignore=False; invert=False; number=False; quiet=False; recursive=False; fixed=False; extended=False; patterns=[]; files=[]; before=0; after=0; color="never"; i=0; operands=[];
    output_tty=getattr(runtime,"_command_stdout_is_tty",None) if runtime is not None else None;
    if output_tty is None:
        try: output_tty=sys.stdout.isatty();
        except Exception: output_tty=False;
    def need(opt):
        nonlocal i;
        if i+1>=len(args): raise ValueError("grep: option requires an argument -- '{}'".format(opt));
        i+=1; return args[i];
    try:
        while i<len(args):
            a=args[i];
            if a=="--": operands.extend(args[i+1:]); break;
            if a.startswith("--"):
                key,val=(a[2:].split("=",1)+[None])[:2] if "=" in a else (a[2:],None);
                if key=="ignore-case": ignore=True;
                elif key=="invert-match": invert=True;
                elif key=="line-number": number=True;
                elif key=="quiet": quiet=True;
                elif key in ("recursive","dereference-recursive"): recursive=True;
                elif key=="extended-regexp": extended=True; fixed=False;
                elif key=="fixed-strings": fixed=True; extended=False;
                elif key=="regexp": patterns.append(val if val is not None else need("e"));
                elif key=="after-context": after=max(0,int(val if val is not None else need("A")));
                elif key=="before-context": before=max(0,int(val if val is not None else need("B")));
                elif key=="context": before=after=max(0,int(val if val is not None else need("C")));
                elif key in ("color","colour"):
                    color=(val or "always").lower();
                    if color not in ("always","auto","never"): return AppletResult(2,err="grep: invalid argument '{}' for '--color'\n".format(color));
                else: return AppletResult(2,err="grep: unrecognized option '--{}'\n".format(key));
                i+=1; continue;
            if a.startswith("-") and a!="-":
                cluster=a[1:]; j=0;
                while j<len(cluster):
                    ch=cluster[j];
                    if ch in ("e","A","B","C"):
                        value=cluster[j+1:] if j+1<len(cluster) else need(ch);
                        if ch=="e": patterns.append(value);
                        elif ch=="A": after=max(0,int(value));
                        elif ch=="B": before=max(0,int(value));
                        else: before=after=max(0,int(value));
                        j=len(cluster); continue;
                    if ch=="i": ignore=True;
                    elif ch=="v": invert=True;
                    elif ch=="n": number=True;
                    elif ch=="q": quiet=True;
                    elif ch in ("r","R"): recursive=True;
                    elif ch=="E": extended=True; fixed=False;
                    elif ch=="F": fixed=True; extended=False;
                    else: return AppletResult(2,err="grep: invalid option -- '{}'\n".format(ch));
                    j+=1;
                i+=1; continue;
            operands.append(a); i+=1;
    except (ValueError,TypeError) as exc:
        return AppletResult(2,err=str(exc)+"\n");
    if not patterns:
        if not operands: return AppletResult(2,err="grep: missing pattern\n");
        patterns.append(operands.pop(0));
    files=operands;
    flags=re.IGNORECASE if ignore else 0; regs=[];
    if not fixed:
        try: regs=[re.compile(p,flags) for p in patterns];
        except re.error as exc: return AppletResult(2,err="grep: {}\n".format(exc));
    inputs=[];
    if recursive and files:
        for raw in files:
            p=_runtime_path(raw,runtime);
            if p.is_dir(): inputs.extend(str(x) for x in p.rglob("*") if x.is_file());
            else: inputs.append(raw);
    else: inputs=files;
    use_color=color=="always" or (color=="auto" and bool(output_tty));
    def is_match(line):
        probe=line.casefold() if ignore else line;
        if fixed:
            values=[p.casefold() if ignore else p for p in patterns]; return any(p in probe for p in values);
        return any(r.search(line) for r in regs);
    def paint(line):
        if not use_color or invert: return line;
        start="\x1b[01;31m"; stop="\x1b[m";
        if fixed:
            out=line;
            for pat in patterns:
                if not pat: continue;
                rx=re.compile(re.escape(pat),flags); out=rx.sub(lambda m:start+m.group(0)+stop,out);
            return out;
        out=line;
        for reg in regs: out=reg.sub(lambda m:start+m.group(0)+stop,out);
        return out;
    found=False; out=[]; err=[]; any_error=False; first_group=True;
    for name,value in _read_paths(inputs,stdin,runtime):
        if isinstance(value,Exception): err.append("grep: {}: {}\n".format(name,value)); any_error=True; continue;
        lines=value.splitlines(); hits=[];
        for idx,line in enumerate(lines):
            hit=is_match(line); hit=not hit if invert else hit;
            if hit: hits.append(idx);
        if hits:
            found=True;
            if quiet: return AppletResult(0);
        selected={};
        for idx in hits:
            lo=max(0,idx-before); hi=min(len(lines)-1,idx+after);
            for pos in range(lo,hi+1): selected[pos]=(pos==idx) or selected.get(pos,False);
        previous=None;
        for pos in sorted(selected):
            if previous is not None and pos>previous+1:
                if not first_group: out.append("--\n");
            first_group=False; previous=pos; match_line=selected[pos]; line=lines[pos]; prefix="";
            sep=":" if match_line else "-";
            if len(inputs)>1 or recursive: prefix += name+sep;
            if number: prefix += str(pos+1)+sep;
            out.append(prefix+(paint(line) if match_line else line)+"\n");
    return AppletResult(2 if any_error and not found else (0 if found else (2 if any_error else 1)),"".join(out),"".join(err));


def app_egrep(argv, stdin="", runtime=None): return app_grep(["-E"]+list(argv),stdin=stdin,runtime=runtime);
def app_fgrep(argv, stdin="", runtime=None): return app_grep(["-F"]+list(argv),stdin=stdin,runtime=runtime);

def app_cut(argv, stdin="", runtime=None):
    fields=None; delim="\t"; chars=None; files=[]; i=0;
    while i<len(argv):
        a=argv[i];
        if a=="--": files.extend(argv[i+1:]); break;
        if a in ("-f","--fields") and i+1<len(argv): fields=argv[i+1]; i+=2; continue;
        if a.startswith("-f") and len(a)>2: fields=a[2:]; i+=1; continue;
        if a in ("-d","--delimiter") and i+1<len(argv): delim=argv[i+1]; i+=2; continue;
        if a.startswith("-d") and len(a)>2: delim=a[2:]; i+=1; continue;
        if a in ("-c","--characters") and i+1<len(argv): chars=argv[i+1]; i+=2; continue;
        if a.startswith("-c") and len(a)>2: chars=a[2:]; i+=1; continue;
        if a.startswith("-") and a!="-": return AppletResult(1,err="cut: invalid option -- '{}'\n".format(a[1:]));
        files.append(a); i+=1;
    def indexes(spec):
        result=[];
        for piece in str(spec or "").split(","):
            if not piece: continue;
            if "-" in piece:
                lo,hi=piece.split("-",1); lo=int(lo or 1); hi=int(hi) if hi else None; result.append((lo,hi));
            else: n=int(piece); result.append((n,n));
        return result;
    ranges=indexes(fields or chars); out=[]; err=[]; code=0;
    for name,value in _read_paths(files,stdin,runtime):
        if isinstance(value,Exception): err.append("cut: {}: {}\n".format(name,value)); code=1; continue;
        for line in value.splitlines():
            seq=line.split(delim) if fields else list(line); selected=[];
            for lo,hi in ranges:
                end=len(seq) if hi is None else hi;
                selected.extend(seq[lo-1:end]);
            out.append((delim.join(selected) if fields else "".join(selected))+"\n");
    return AppletResult(code,"".join(out),"".join(err));


def app_sed(argv, stdin="", runtime=None):
    quiet=False; args=list(argv);
    if args and args[0]=="-n": quiet=True; args.pop(0);
    if not args: return AppletResult(2,err="sed: missing script\n");
    script=args.pop(0); files=args; out=[]; err=[]; code=0;
    sub=None;
    if script.startswith("s") and len(script)>2:
        d=script[1]; parts=script[2:].split(d);
        if len(parts)>=3:
            pat,repl,flags=parts[0],parts[1],parts[2];
            try: sub=(re.compile(pat),repl,"g" in flags);
            except re.error as exc: return AppletResult(2,err="sed: {}\n".format(exc));
    for name,value in _read_paths(files,stdin,runtime):
        if isinstance(value,Exception): err.append("sed: {}: {}\n".format(name,value)); code=1; continue;
        for line in value.splitlines(True):
            text=line;
            if sub:
                reg,repl,glob=sub; text=reg.sub(repl,text,0 if glob else 1);
            elif script=="p": pass;
            else: return AppletResult(2,err="sed: alpha supports s///[g] and p\n");
            if not quiet or script=="p": out.append(text);
    return AppletResult(code,"".join(out),"".join(err));


def app_head(argv, stdin="", runtime=None):
    n=10; files=[]; args=list(argv); i=0;
    while i<len(args):
        a=args[i];
        if a=="--": files.extend(args[i+1:]); break;
        if a in ("-n","--lines"):
            if i+1>=len(args): return AppletResult(1,err="head: option requires an argument -- 'n'\n");
            try: n=int(args[i+1]);
            except ValueError: return AppletResult(1,err="head: invalid number of lines: '{}'\n".format(args[i+1]));
            i+=2; continue;
        if a.startswith("--lines="):
            try: n=int(a.split("=",1)[1]);
            except ValueError: return AppletResult(1,err="head: invalid number of lines\n");
            i+=1; continue;
        if a.startswith("-n") and len(a)>2:
            try: n=int(a[2:]);
            except ValueError: return AppletResult(1,err="head: invalid number of lines: '{}'\n".format(a[2:]));
            i+=1; continue;
        if a.startswith("-") and a[1:].isdigit(): n=int(a[1:]); i+=1; continue;
        if a.startswith("-") and a!="-": return AppletResult(1,err="head: invalid option -- '{}'\n".format(a[1:]));
        files.append(a); i+=1;
    out=[]; err=[]; code=0;
    for name,value in _read_paths(files,stdin,runtime):
        if isinstance(value,Exception): err.append("head: {}: {}\n".format(name,value)); code=1;
        else: out.extend(value.splitlines(True)[:n]);
    return AppletResult(code,"".join(out),"".join(err));

def app_tail(argv, stdin="", runtime=None):
    n=10; files=[]; args=list(argv); i=0;
    while i<len(args):
        a=args[i];
        if a=="--": files.extend(args[i+1:]); break;
        if a in ("-n","--lines"):
            if i+1>=len(args): return AppletResult(1,err="tail: option requires an argument -- 'n'\n");
            try: n=int(args[i+1]);
            except ValueError: return AppletResult(1,err="tail: invalid number of lines: '{}'\n".format(args[i+1]));
            i+=2; continue;
        if a.startswith("--lines="):
            try: n=int(a.split("=",1)[1]);
            except ValueError: return AppletResult(1,err="tail: invalid number of lines\n");
            i+=1; continue;
        if a.startswith("-n") and len(a)>2:
            try: n=int(a[2:]);
            except ValueError: return AppletResult(1,err="tail: invalid number of lines: '{}'\n".format(a[2:]));
            i+=1; continue;
        if a.startswith("-") and a[1:].isdigit(): n=int(a[1:]); i+=1; continue;
        if a.startswith("-") and a!="-": return AppletResult(1,err="tail: invalid option -- '{}'\n".format(a[1:]));
        files.append(a); i+=1;
    out=[]; err=[]; code=0;
    for name,value in _read_paths(files,stdin,runtime):
        if isinstance(value,Exception): err.append("tail: {}: {}\n".format(name,value)); code=1;
        else: out.extend(value.splitlines(True)[-n:]);
    return AppletResult(code,"".join(out),"".join(err));

def app_sort(argv, stdin="", runtime=None):
    reverse=False; numeric=False; unique=False; files=[]; args=list(argv); i=0;
    while i<len(args):
        a=args[i];
        if a=="--": files.extend(args[i+1:]); break;
        if a.startswith("--"):
            if a=="--reverse": reverse=True;
            elif a=="--numeric-sort": numeric=True;
            elif a=="--unique": unique=True;
            else: return AppletResult(2,err="sort: unrecognized option '{}'\n".format(a));
            i+=1; continue;
        if a.startswith("-") and a!="-":
            for ch in a[1:]:
                if ch=="r": reverse=True;
                elif ch=="n": numeric=True;
                elif ch=="u": unique=True;
                else: return AppletResult(2,err="sort: invalid option -- '{}'\n".format(ch));
            i+=1; continue;
        files.append(a); i+=1;
    lines=[]; err=[]; code=0;
    for name,value in _read_paths(files,stdin,runtime):
        if isinstance(value,Exception): err.append("sort: {}: {}\n".format(name,value)); code=1; continue;
        lines.extend(value.splitlines());
    key=(lambda x: float(x.strip() or 0)) if numeric else None;
    try: lines.sort(key=key,reverse=reverse);
    except ValueError: return AppletResult(2,err="sort: non-numeric input\n");
    if unique: lines=list(dict.fromkeys(lines));
    return AppletResult(code,"".join(x+"\n" for x in lines),"".join(err));

def app_uniq(argv, stdin="", runtime=None):
    count=False; only_dup=False; only_unique=False; files=[]; args=list(argv); i=0;
    while i<len(args):
        a=args[i];
        if a=="--": files.extend(args[i+1:]); break;
        if a.startswith("--"):
            if a=="--count": count=True;
            elif a=="--repeated": only_dup=True;
            elif a=="--unique": only_unique=True;
            else: return AppletResult(1,err="uniq: unrecognized option '{}'\n".format(a));
            i+=1; continue;
        if a.startswith("-") and a!="-":
            for ch in a[1:]:
                if ch=="c": count=True;
                elif ch=="d": only_dup=True;
                elif ch=="u": only_unique=True;
                else: return AppletResult(1,err="uniq: invalid option -- '{}'\n".format(ch));
            i+=1; continue;
        files.append(a); i+=1;
    data=_read_paths(files,stdin,runtime); out=[]; err=[]; code=0; lines=[];
    for name,value in data:
        if isinstance(value,Exception): err.append("uniq: {}: {}\n".format(name,value)); code=1;
        else: lines.extend(value.splitlines());
    last=None; n=0;
    def emit(v,c):
        if v is None: return;
        if only_dup and c<2: return;
        if only_unique and c!=1: return;
        out.append(("{:7d} ".format(c) if count else "")+v+"\n");
    for line in lines:
        if line==last: n+=1;
        else: emit(last,n); last=line; n=1;
    emit(last,n);
    return AppletResult(code,"".join(out),"".join(err));

def app_wc(argv, stdin="", runtime=None):
    want_l=False; want_w=False; want_c=False; want_m=False; files=[]; args=list(argv); i=0;
    while i<len(args):
        a=args[i];
        if a=="--": files.extend(args[i+1:]); break;
        if a.startswith("--"):
            if a=="--lines": want_l=True;
            elif a=="--words": want_w=True;
            elif a=="--bytes": want_c=True;
            elif a=="--chars": want_m=True;
            else: return AppletResult(1,err="wc: unrecognized option '{}'\n".format(a));
            i+=1; continue;
        if a.startswith("-") and a!="-":
            for ch in a[1:]:
                if ch=="l": want_l=True;
                elif ch=="w": want_w=True;
                elif ch=="c": want_c=True;
                elif ch=="m": want_m=True;
                else: return AppletResult(1,err="wc: invalid option -- '{}'\n".format(ch));
            i+=1; continue;
        files.append(a); i+=1;
    if not (want_l or want_w or want_c or want_m): want_l=want_w=want_c=True;
    out=[]; err=[]; code=0;
    for name,value in _read_paths(files,stdin,runtime):
        if isinstance(value,Exception): err.append("wc: {}: {}\n".format(name,value)); code=1; continue;
        vals=[];
        if want_l: vals.append(str(len(value.splitlines())));
        if want_w: vals.append(str(len(value.split())));
        if want_c: vals.append(str(len(value.encode("utf-8"))));
        if want_m: vals.append(str(len(value)));
        out.append(" ".join(vals)+(" "+name if files else "")+"\n");
    return AppletResult(code,"".join(out),"".join(err));

def app_tee(argv, stdin="", runtime=None):
    append=False; files=[];
    for a in argv:
        if a in ("-a","--append"): append=True;
        else: files.append(a);
    err=[]; code=0; mode="a" if append else "w";
    for raw in files:
        try:
            with open(_runtime_path(raw,runtime),mode,encoding="utf-8") as stream: stream.write(_text_input(stdin));
        except OSError as exc: err.append("tee: {}: {}\n".format(raw,exc)); code=1;
    return AppletResult(code,_text_input(stdin),"".join(err));


def _parse_date(text):
    raw=text.strip(); now=datetime.now().astimezone(); low=raw.casefold();
    if low in ("now","today"): return now;
    if low=="yesterday": return now-timedelta(days=1);
    if low=="tomorrow": return now+timedelta(days=1);
    m=re.fullmatch(r"(\d+)\s+(day|days|week|weeks|hour|hours|minute|minutes)\s+ago",low);
    if m:
        n=int(m.group(1)); unit=m.group(2);
        if unit.startswith("day"): return now-timedelta(days=n);
        if unit.startswith("week"): return now-timedelta(weeks=n);
        if unit.startswith("hour"): return now-timedelta(hours=n);
        return now-timedelta(minutes=n);
    m=re.fullmatch(r"(\d+)\s+(day|days|week|weeks|hour|hours|minute|minutes)",low);
    if m:
        n=int(m.group(1)); unit=m.group(2);
        if unit.startswith("day"): return now+timedelta(days=n);
        if unit.startswith("week"): return now+timedelta(weeks=n);
        if unit.startswith("hour"): return now+timedelta(hours=n);
        return now+timedelta(minutes=n);
    for fmt in ("%Y-%m-%d","%Y%m%d","%Y-%m-%d %H:%M:%S","%Y%m%d.%H%M%S"):
        try: return datetime.strptime(raw,fmt).replace(tzinfo=now.tzinfo);
        except ValueError: pass;
    raise ValueError("unsupported date: {}".format(text));


def app_date(argv, stdin="", runtime=None):
    args=list(argv); utc=False; value=datetime.now().astimezone(); fmt=None; i=0;
    while i<len(args):
        a=args[i];
        if a in ("-u","--utc"): utc=True; i+=1; continue;
        if a in ("-d","--date") and i+1<len(args):
            try: value=_parse_date(args[i+1]);
            except ValueError as exc: return AppletResult(1,err="date: {}\n".format(exc));
            i+=2; continue;
        if a.startswith("--date="):
            try: value=_parse_date(a.split("=",1)[1]);
            except ValueError as exc: return AppletResult(1,err="date: {}\n".format(exc));
            i+=1; continue;
        if a.startswith("+"): fmt=a[1:]; i+=1; continue;
        i+=1;
    if utc: value=value.astimezone(timezone.utc);
    if fmt is None: fmt="%a %b %d %H:%M:%S %Z %Y";
    if "%s" in fmt:
        fmt=fmt.replace("%s",str(int(value.timestamp())));
    return AppletResult(out=value.strftime(fmt)+"\n");


def _pretty_uptime(seconds):
    total=max(0,int(seconds)); days,total=divmod(total,86400); hours,total=divmod(total,3600); minutes=total//60; parts=[];
    if days: parts.append("{} day{}".format(days,"" if days==1 else "s"));
    if hours: parts.append("{} hour{}".format(hours,"" if hours==1 else "s"));
    if minutes or not parts: parts.append("{} minute{}".format(minutes,"" if minutes==1 else "s"));
    return "up "+", ".join(parts);


def app_uptime(argv, stdin="", runtime=None):
    data=suminfo.collect_uptime(); seconds=data.get("uptime_seconds");
    if seconds is None: return AppletResult(1,err="uptime: unavailable\n");
    if "-p" in argv or "--pretty" in argv: return AppletResult(out=_pretty_uptime(seconds)+"\n");
    if "-s" in argv or "--since" in argv:
        boot=data.get("boot_time_utc");
        if not boot: return AppletResult(1,err="uptime: boot time unavailable\n");
        dt=datetime.fromisoformat(boot).astimezone(); return AppletResult(out=dt.strftime("%Y-%m-%d %H:%M:%S")+"\n");
    clock=datetime.now().astimezone().strftime("%H:%M:%S"); text="{} {}".format(clock,_pretty_uptime(seconds)); load=data.get("load_average");
    if load: text += ", load average: "+", ".join("{:.2f}".format(x) for x in load);
    return AppletResult(out=text+"\n");


def app_uname(argv, stdin="", runtime=None):
    expanded=[];
    for a in argv:
        if a.startswith("-") and not a.startswith("--") and len(a)>2: expanded.extend("-"+ch for ch in a[1:]);
        else: expanded.append(a);
    argv=expanded;
    ident=suminfo.collect_identity(); kernel=ident["kernel"]; machine=ident["machine"];
    if not argv: return AppletResult(out=(kernel.get("name") or ident.get("platform") or "unknown")+"\n");
    if "-a" in argv or "--all" in argv:
        values=[kernel.get("name"),machine.get("hostname"),kernel.get("release"),kernel.get("version"),machine.get("architecture")];
        if ident.get("platform")=="linux": values.append("GNU/Linux");
        return AppletResult(out=" ".join(str(v) for v in values if v)+"\n");
    mapping={"-s":kernel.get("name"),"--kernel-name":kernel.get("name"),"-n":machine.get("hostname"),"--nodename":machine.get("hostname"),"-r":kernel.get("release"),"--kernel-release":kernel.get("release"),"-v":kernel.get("version"),"--kernel-version":kernel.get("version"),"-m":machine.get("architecture"),"--machine":machine.get("architecture")};
    vals=[mapping[a] for a in argv if a in mapping and mapping[a]];
    return AppletResult(out=(" ".join(str(v) for v in vals)+"\n") if vals else "");


def app_lsb_release(argv, stdin="", runtime=None):
    expanded=[];
    for a in argv:
        if a.startswith("-") and not a.startswith("--") and len(a)>2: expanded.extend("-"+ch for ch in a[1:]);
        else: expanded.append(a);
    argv=expanded;
    ident=suminfo.collect_identity(); osdata=ident["os"];
    short="-s" in argv or "--short" in argv; fields=[];
    if "-i" in argv or "--id" in argv: fields.append(("Distributor ID",osdata.get("distributor")));
    if "-d" in argv or "--description" in argv: fields.append(("Description",osdata.get("description")));
    if "-r" in argv or "--release" in argv: fields.append(("Release",osdata.get("release")));
    if "-c" in argv or "--codename" in argv: fields.append(("Codename",osdata.get("codename")));
    if "-a" in argv or "--all" in argv or not fields:
        fields=[("Distributor ID",osdata.get("distributor")),("Description",osdata.get("description")),("Release",osdata.get("release")),("Codename",osdata.get("codename"))];
    out=[];
    for label,value in fields:
        if short: out.append(("" if value is None else str(value))+"\n");
        else: out.append("{}:\t{}\n".format(label,"n/a" if value is None else value));
    return AppletResult(out="".join(out));


def app_hostname(argv, stdin="", runtime=None): return AppletResult(out=(suminfo.collect_identity()["machine"].get("hostname") or socket.gethostname())+"\n");
def app_arch(argv, stdin="", runtime=None): return AppletResult(out=(suminfo.collect_identity()["machine"].get("architecture") or "unknown")+"\n");
def app_whoami(argv, stdin="", runtime=None): return AppletResult(out=getpass.getuser()+"\n");


def app_tty(argv, stdin="", runtime=None):
    """Report the controlling terminal for the current sumbash process.""";
    if not sys.stdin.isatty(): return AppletResult(1,out="not a tty\n");
    try: name=os.ttyname(sys.stdin.fileno());
    except (OSError,AttributeError): return AppletResult(1,out="not a tty\n");
    if "-s" in argv or "--silent" in argv or "--quiet" in argv: return AppletResult();
    return AppletResult(out=name+"\n");



def _less_help_text():
    return """sumbash less - portable interactive pager

Usage: less [OPTION]... [FILE]...

Keys:
  q              quit
  j, Down, Enter scroll down one line
  k, Up           scroll up one line
  Space, PgDn, f  next page
  b, PgUp         previous page
  d / u           half page down / up
  g / G           first / last page
  F               follow a named file (like tail -f); Ctrl-C stops following
  /PATTERN         search forward (regular expression)
  ?PATTERN         search backward
  n / N            repeat search forward / backward
  Left / Right     horizontal scroll with -S
  Ctrl-L           redraw
  h                show this help

Options:
  -N                  show line numbers
  -S                  chop long lines instead of wrapping
  -i                  case-insensitive searches
  -X                  do not use the terminal alternate screen
  -f, --force         force opening non-regular files (less compatibility)
  -F, --quit-if-one-screen
                      quit if the whole file fits on one screen
  --follow            start at the end and follow a named file (SUM extension)
  --syntax=MODE       auto, none, log, python, bash, json, yaml, ...
  --no-syntax         disable syntax/semantic highlighting
  +G                  start at end
  +F                  start at end and follow
  +/PATTERN           start at first matching line
  --help              show this help

Highlighting is applied only to visible terminal rows. Search matches take
priority over syntax colors. Pygments is used opportunistically for source
languages when installed; log highlighting is built in.
""";


def _less_detect_syntax(label,text,requested="auto"):
    requested=(requested or "auto").lower();
    if requested in ("none","plain","text","off"): return None;
    aliases={"py":"python","sh":"bash","shell":"bash","sumbash":"bash","js":"javascript","md":"markdown","yml":"yaml","c++":"cpp","r":"r","xbase":"foxpro","sumx":"foxpro","basic":"qbasic","sumbasic":"qbasic"};
    if requested!="auto": return aliases.get(requested,requested);
    name=str(label or "").lower(); suffix=Path(name).suffix.lower();
    ext={
        ".log":"log",".out":"log",".err":"log",".jsonl":"json",".ndjson":"json",
        ".py":"python",".pyw":"python",".sh":"bash",".bash":"bash",".ksh":"bash",".zsh":"bash",
        ".bas":"qbasic",".prg":"foxpro",".r":"r",".json":"json",".yaml":"yaml",".yml":"yaml",
        ".toml":"toml",".ini":"ini",".cfg":"ini",".md":"markdown",".markdown":"markdown",
        ".html":"html",".htm":"html",".css":"css",".js":"javascript",".mjs":"javascript",
        ".sql":"sql",".c":"c",".h":"c",".cc":"cpp",".cpp":"cpp",".cxx":"cpp",".hpp":"cpp",".java":"java",
    };
    if suffix in ext: return ext[suffix];
    first=(text.splitlines()[0] if text else "").strip();
    if first.startswith("#!"):
        low=first.lower();
        if "python" in low: return "python";
        if any(token in low for token in ("bash","sumbash","/sh","ksh","zsh")): return "bash";
    sample="\n".join(text.splitlines()[:20]);
    if re.search(r"\b(?:ERROR|CRITICAL|FATAL|WARNING|WARN|NOTICE|INFO|DEBUG)\b",sample): return "log";
    if re.search(r"^\s*\[[^\]]*\]\s+(?:NOTICE|WARNING|ERROR|INFO|DEBUG)\b",sample,re.M): return "log";
    if re.search(r"^\s*(?:\d{1,3}\.){3}\d{1,3}\s+.*\[[^\]]+\].*\"(?:GET|POST|PUT|DELETE|PATCH|HEAD)\s",sample,re.M): return "log";
    stripped=sample.lstrip();
    if stripped.startswith(("{","[")) and ('"' in stripped): return "json";
    return None;


_LESS_PYGMENTS_CACHE={};
def _less_pygments_fragment(text,syntax):
    try:
        from pygments import highlight;
        from pygments.formatters import TerminalFormatter;
        from pygments.lexers import get_lexer_by_name;
        if syntax not in _LESS_PYGMENTS_CACHE: _LESS_PYGMENTS_CACHE[syntax]=get_lexer_by_name(syntax);
        rendered=highlight(text,_LESS_PYGMENTS_CACHE[syntax],TerminalFormatter());
        if rendered.endswith("\n"): rendered=rendered[:-1];
        return rendered;
    except Exception:
        return text;


def _less_log_fragment(text):
    pattern=re.compile(
        r"(?P<level>\b(?:TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|CRITICAL|FATAL|ALERT|EMERG(?:ENCY)?)\b)"
        r"|(?P<stamp>\[[0-9]{1,2}-[A-Za-z]{3}-[0-9]{4}[^\]]*\]|\b[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(?:[.,][0-9]+)?(?:Z|[+-][0-9:]+)?)"
        r"|(?P<ip>\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b)"
        r"|(?P<method>\b(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|CONNECT|TRACE)\b)"
        r"|(?P<status>\b[1-5][0-9]{2}\b)"
        r"|(?P<bad>\b(?:failed|failure|denied|refused|timeout|timed\s+out|not\s+permitted|too\s+slow|exited)\b)"
        r"|(?P<good>\b(?:started|success|successful|ok)\b)"
        r"|(?P<bool>\b(?:true|false|null)\b)",re.I);
    level_colors={"TRACE":"\x1b[90m","DEBUG":"\x1b[90m","INFO":"\x1b[36m","NOTICE":"\x1b[1;36m","WARN":"\x1b[1;33m","WARNING":"\x1b[1;33m","ERROR":"\x1b[1;31m","CRITICAL":"\x1b[1;31m","FATAL":"\x1b[1;31m","ALERT":"\x1b[1;31m","EMERG":"\x1b[1;31m","EMERGENCY":"\x1b[1;31m"};
    def repl(match):
        value=match.group(0); group=match.lastgroup;
        if group=="level": color=level_colors.get(value.upper(),"\x1b[36m");
        elif group=="stamp": color="\x1b[90m";
        elif group=="ip": color="\x1b[35m";
        elif group=="method": color="\x1b[1;34m";
        elif group=="status":
            color={"1":"\x1b[90m","2":"\x1b[32m","3":"\x1b[36m","4":"\x1b[33m","5":"\x1b[1;31m"}.get(value[:1],"\x1b[36m");
        elif group=="bad": color="\x1b[33m";
        elif group=="good": color="\x1b[32m";
        else: color="\x1b[32m" if value.lower()=="true" else "\x1b[35m";
        return color+value+"\x1b[0m";
    return pattern.sub(repl,text);


def _less_syntax_fragment(text,syntax):
    if not syntax: return text;
    if syntax=="log": return _less_log_fragment(text);
    return _less_pygments_fragment(text,syntax);


def _less_render_fragment(text,syntax=None,search_pattern=None,ignore_case=False):
    if not search_pattern: return _less_syntax_fragment(text,syntax);
    flags=re.I if ignore_case else 0;
    try: rx=re.compile(search_pattern,flags);
    except re.error: rx=re.compile(re.escape(search_pattern),flags);
    out=[]; pos=0;
    for match in rx.finditer(text):
        if match.start()==match.end(): continue;
        out.append(_less_syntax_fragment(text[pos:match.start()],syntax));
        out.append("\x1b[7m"+match.group(0)+"\x1b[27m"); pos=match.end();
    out.append(_less_syntax_fragment(text[pos:],syntax));
    return "".join(out);


def _less_read_sources(argv, stdin, runtime):
    args=list(argv); line_numbers=False; chop=False; ignore_case=False; alt_screen=True; start_end=False; initial_search=None; files=[]; follow=False; force=False; quit_if_one_screen=False; syntax="auto"; i=0;
    while i<len(args):
        arg=args[i];
        if arg=="--": files.extend(args[i+1:]); break;
        if arg in ("--help","-h"): return None, None, _less_help_text(), 0;
        if arg=="-N": line_numbers=True; i+=1; continue;
        if arg=="-S": chop=True; i+=1; continue;
        if arg=="-i": ignore_case=True; i+=1; continue;
        if arg=="-X": alt_screen=False; i+=1; continue;
        if arg in ("--follow","+F"): follow=True; start_end=True; i+=1; continue;
        if arg in ("-f","--force"): force=True; i+=1; continue;
        if arg in ("-F","--quit-if-one-screen"): quit_if_one_screen=True; i+=1; continue;
        if arg=="+G": start_end=True; i+=1; continue;
        if arg=="--no-syntax": syntax="none"; i+=1; continue;
        if arg=="--syntax":
            if i+1>=len(args): return None,None,"less: --syntax requires a mode\n",2;
            syntax=args[i+1]; i+=2; continue;
        if arg.startswith("--syntax="): syntax=arg.split("=",1)[1]; i+=1; continue;
        if arg.startswith("+/"):
            initial_search=arg[2:]; i+=1; continue;
        if arg.startswith("-") and arg!="-":
            return None, None, "less: unsupported option: {}\n".format(arg), 2;
        files.append(arg); i+=1;
    base=Path(getattr(runtime,"cwd",os.getcwd())); chunks=[]; labels=[]; resolved=[];
    if files:
        for name in files:
            if name=="-": chunks.append(_text_input(stdin)); labels.append("standard input"); resolved.append(None); continue;
            path=Path(name); path=path if path.is_absolute() else base/path;
            try:
                if not force and not path.is_file(): return None,None,"less: {}: not a regular file (use -f/--force)\n".format(name),1;
                chunks.append(path.read_text(encoding="utf-8",errors="replace")); labels.append(str(name)); resolved.append(path);
            except OSError as exc: return None, None, "less: {}: {}\n".format(name,exc), 1;
    elif stdin not in (None,""):
        chunks.append(_text_input(stdin)); labels.append("standard input"); resolved.append(None);
    else:
        return None, None, "less: missing filename or input\n", 1;
    if follow and (len(resolved)!=1 or resolved[0] is None): return None,None,"less: --follow currently requires one named file\n",2;
    if len(chunks)==1: text=chunks[0]; label=labels[0];
    else:
        joined=[];
        for label0,chunk in zip(labels,chunks): joined.append("::::::::::::::\n{}\n::::::::::::::\n{}".format(label0,chunk));
        text="\n".join(joined); label="{} files".format(len(chunks));
    follow_path=resolved[0] if len(resolved)==1 and resolved[0] is not None else None; follow_offset=None; follow_identity=None;
    if follow_path is not None:
        try:
            st=follow_path.stat(); follow_offset=st.st_size; follow_identity=(getattr(st,"st_dev",None),getattr(st,"st_ino",None));
        except OSError: follow_offset=0;
    syntax_mode=_less_detect_syntax(label,text,syntax);
    options={"line_numbers":line_numbers,"chop":chop,"ignore_case":ignore_case,"alt_screen":alt_screen,"start_end":start_end,"initial_search":initial_search,"label":label,"follow":follow,"follow_path":follow_path,"follow_offset":follow_offset,"follow_identity":follow_identity,"force":force,"quit_if_one_screen":quit_if_one_screen,"syntax":syntax_mode,"syntax_requested":syntax};
    return text, options, "", 0;


def _less_follow_read(options):
    path=options.get("follow_path");
    if path is None: return None;
    path=Path(path);
    try: st=path.stat();
    except OSError: return None;
    identity=(getattr(st,"st_dev",None),getattr(st,"st_ino",None)); old_identity=options.get("follow_identity"); offset=options.get("follow_offset") or 0;
    if old_identity is not None and identity!=old_identity:
        try: data=path.read_bytes();
        except OSError: return None;
        options["follow_identity"]=identity; options["follow_offset"]=len(data); return ("replace",data.decode("utf-8",errors="replace"));
    if st.st_size<offset:
        try: data=path.read_bytes();
        except OSError: return None;
        options["follow_identity"]=identity; options["follow_offset"]=len(data); return ("replace",data.decode("utf-8",errors="replace"));
    if st.st_size==offset: return None;
    try:
        with path.open("rb") as handle:
            handle.seek(offset); data=handle.read();
    except OSError: return None;
    options["follow_identity"]=identity; options["follow_offset"]=offset+len(data);
    return ("append",data.decode("utf-8",errors="replace"));


def _less_key_reader(control_fd=None,timeout=None):
    if os.name=="nt":
        import msvcrt;
        if timeout is not None:
            deadline=time.monotonic()+max(0,float(timeout));
            while not msvcrt.kbhit():
                if time.monotonic()>=deadline: return None;
                time.sleep(0.02);
        ch=msvcrt.getwch();
        if ch in ("\x00","\xe0"):
            code=msvcrt.getwch();
            return {"H":"UP","P":"DOWN","I":"PGUP","Q":"PGDN","K":"LEFT","M":"RIGHT","G":"HOME","O":"END"}.get(code,code);
        return ch;
    import termios;
    import select;
    fd=sys.stdin.fileno() if control_fd is None else control_fd; old=termios.tcgetattr(fd);
    try:
        new=termios.tcgetattr(fd); new[3] &= ~(termios.ICANON|termios.ECHO); new[6][termios.VMIN]=1; new[6][termios.VTIME]=0; termios.tcsetattr(fd,termios.TCSADRAIN,new);
        if timeout is not None:
            ready,_,_=select.select([fd],[],[],max(0,float(timeout)));
            if not ready: return None;
        first=os.read(fd,1);
        if first!=b"\x1b": return first.decode("utf-8",errors="ignore");
        seq=bytearray(first);
        while len(seq)<8:
            ready,_,_=select.select([fd],[],[],0.015);
            if not ready: break;
            seq.extend(os.read(fd,1));
            if seq[-1:] in (b"A",b"B",b"C",b"D",b"H",b"F",b"~"): break;
        code=bytes(seq);
        return {b"\x1b[A":"UP",b"\x1b[B":"DOWN",b"\x1b[C":"RIGHT",b"\x1b[D":"LEFT",b"\x1b[5~":"PGUP",b"\x1b[6~":"PGDN",b"\x1b[H":"HOME",b"\x1b[F":"END"}.get(code,"ESC");
    finally:
        termios.tcsetattr(fd,termios.TCSADRAIN,old);


def _less_prompt_input(prefix,control_fd=None):
    buf=[]; sys.stdout.write("\x1b[7m{}\x1b[0m".format(prefix)); sys.stdout.flush();
    while True:
        key=_less_key_reader(control_fd);
        if key in ("\r","\n"): return "".join(buf);
        if key in ("ESC","\x03"): return None;
        if key in ("\x7f","\b"):
            if buf: buf.pop(); sys.stdout.write("\b \b"); sys.stdout.flush();
            continue;
        if isinstance(key,str) and len(key)==1 and key.isprintable(): buf.append(key); sys.stdout.write(key); sys.stdout.flush();


def _less_visual_rows(lines,width,line_numbers=False,chop=False,h_offset=0):
    rows=[]; prefix_width=(len(str(max(1,len(lines))))+1) if line_numbers else 0; body_width=max(1,width-prefix_width);
    for idx,line in enumerate(lines):
        line=line.expandtabs(8); prefix=(str(idx+1).rjust(prefix_width-1)+" ") if line_numbers else "";
        if chop:
            rows.append((idx,prefix,line[h_offset:h_offset+body_width])); continue;
        if not line: rows.append((idx,prefix,"")); continue;
        start=0; first=True;
        while start<len(line):
            part=line[start:start+body_width]; rows.append((idx,prefix if first else " "*prefix_width,part)); first=False; start+=body_width;
    return rows or [(0,"","")];


def _less_search(lines,pattern,start_line,direction,ignore_case):
    flags=re.I if ignore_case else 0;
    try: rx=re.compile(pattern,flags);
    except re.error: rx=re.compile(re.escape(pattern),flags);
    if direction>=0:
        indexes=range(min(len(lines),start_line+1),len(lines));
    else:
        indexes=range(min(len(lines)-1,start_line-1),-1,-1);
    for idx in indexes:
        if rx.search(lines[idx]): return idx;
    return None;


def _less_apply_follow_update(lines,update):
    if not update: return lines;
    mode,data=update;
    if mode=="replace": return data.split("\n");
    if not lines: return data.split("\n");
    combined=lines[-1]+data; lines[-1:]=combined.split("\n"); return lines;


def _less_interactive(text,options,runtime=None):
    if runtime is not None and not getattr(runtime,"_command_stdout_is_tty",True): return False;
    if not getattr(sys.stdout,"isatty",lambda:False)(): return False;
    control_fd=None; close_control=False;
    if os.name!="nt":
        if getattr(sys.stdin,"isatty",lambda:False)(): control_fd=sys.stdin.fileno();
        else:
            try: control_fd=os.open("/dev/tty",os.O_RDWR); close_control=True;
            except OSError: return False;
    lines=text.split("\n");
    if not lines: lines=[""];
    if options.get("quit_if_one_screen"):
        size=shutil.get_terminal_size((80,24)); height=max(1,size.lines-1); width=max(10,size.columns);
        if len(_less_visual_rows(lines,width,options["line_numbers"],options["chop"],0))<=height: return False;
    top=0; h_offset=0; last_search=None; search_direction=1; message=""; alt=options["alt_screen"]; follow=bool(options.get("follow")); redraw=True; rows=[]; height=1; width=80; last_size=None;
    if alt: sys.stdout.write("\x1b[?1049h"); sys.stdout.flush();
    try:
        while True:
            size=shutil.get_terminal_size((80,24)); current_size=(size.columns,size.lines);
            if current_size!=last_size: redraw=True; last_size=current_size;
            height=max(1,size.lines-1); width=max(10,size.columns);
            if redraw:
                rows=_less_visual_rows(lines,width,options["line_numbers"],options["chop"],h_offset);
                if options["start_end"]:
                    top=max(0,len(rows)-height); options["start_end"]=False;
                if options["initial_search"] is not None:
                    pat=options["initial_search"]; options["initial_search"]=None;
                    hit=_less_search(lines,pat,-1,1,options["ignore_case"]); last_search=pat; search_direction=1;
                    if hit is not None:
                        for pos,(src,_,_) in enumerate(rows):
                            if src==hit: top=pos; break;
                if follow: top=max(0,len(rows)-height);
                top=max(0,min(top,max(0,len(rows)-height)));
                visible=rows[top:top+height];
                sys.stdout.write("\x1b[H\x1b[2J");
                for _,prefix,fragment in visible:
                    rendered_prefix=("\x1b[90m"+prefix+"\x1b[0m") if prefix else "";
                    rendered=_less_render_fragment(fragment,options.get("syntax"),last_search,options["ignore_case"]);
                    sys.stdout.write(rendered_prefix+rendered+"\x1b[K\n");
                if len(visible)<height:
                    for _ in range(height-len(visible)): sys.stdout.write("~\x1b[K\n");
                first_line=rows[top][0]+1; last_line=visible[-1][0]+1 if visible else first_line; percent=min(100,int((top+height)*100/max(1,len(rows))));
                follow_tag=" FOLLOW" if follow else ""; syntax_tag=" [{}]".format(options["syntax"]) if options.get("syntax") else "";
                status=message or "{}{}{}  lines {}-{} / {}  {}%  (q quit, / search, F follow)".format(options["label"],follow_tag,syntax_tag,first_line,last_line,len(lines),percent); message="";
                sys.stdout.write("\x1b[7m"+status[:width].ljust(width)+"\x1b[0m"); sys.stdout.flush(); redraw=False;
            try: key=_less_key_reader(control_fd,0.25 if follow else None);
            except KeyboardInterrupt:
                if follow: follow=False; message="Follow stopped"; redraw=True;
                else: message="Interrupted"; redraw=True;
                continue;
            if key is None and follow:
                update=_less_follow_read(options);
                if update:
                    lines=_less_apply_follow_update(lines,update); redraw=True;
                continue;
            if key in ("q","Q"): break;
            if key=="\x03":
                if follow: follow=False; message="Follow stopped"; redraw=True;
                continue;
            if key=="F":
                if options.get("follow_path") is None: message="Follow requires one named file";
                else: follow=True; top=max(0,len(rows)-height); message="Following; Ctrl-C stops follow";
                redraw=True; continue;
            if key in ("DOWN","j","\r","\n"): follow=False; top+=1; redraw=True; continue;
            if key in ("UP","k"): follow=False; top-=1; redraw=True; continue;
            if key in ("PGDN"," ","f"): follow=False; top+=height; redraw=True; continue;
            if key in ("PGUP","b"): follow=False; top-=height; redraw=True; continue;
            if key=="d": follow=False; top+=max(1,height//2); redraw=True; continue;
            if key=="u": follow=False; top-=max(1,height//2); redraw=True; continue;
            if key in ("g","HOME"): follow=False; top=0; redraw=True; continue;
            if key in ("G","END"): follow=False; top=max(0,len(rows)-height); redraw=True; continue;
            if key=="LEFT" and options["chop"]: follow=False; h_offset=max(0,h_offset-max(1,width//4)); redraw=True; continue;
            if key=="RIGHT" and options["chop"]: follow=False; h_offset+=max(1,width//4); redraw=True; continue;
            if key in ("/","?"):
                follow=False; sys.stdout.write("\r\x1b[K"); sys.stdout.flush(); pat=_less_prompt_input(key,control_fd);
                if pat is None: redraw=True; continue;
                last_search=pat; search_direction=1 if key=="/" else -1; current=rows[top][0]; hit=_less_search(lines,pat,current-search_direction,search_direction,options["ignore_case"]);
                if hit is None: message="Pattern not found: {}".format(pat); redraw=True; continue;
                for pos,(src,_,_) in enumerate(rows):
                    if src==hit: top=pos; break;
                redraw=True; continue;
            if key in ("n","N") and last_search:
                follow=False; direction=search_direction if key=="n" else -search_direction; current=rows[top][0]; hit=_less_search(lines,last_search,current,direction,options["ignore_case"]);
                if hit is None: message="Pattern not found: {}".format(last_search); redraw=True; continue;
                positions=[pos for pos,(src,_,_) in enumerate(rows) if src==hit];
                if positions: top=positions[0];
                redraw=True; continue;
            if key=="h":
                message="q quit | arrows/jk line | PgUp/PgDn page | g/G ends | F follow | / ? search | n/N repeat"; redraw=True; continue;
            if key=="\x0c": redraw=True; continue;
        return True;
    finally:
        if alt: sys.stdout.write("\x1b[?1049l");
        else: sys.stdout.write("\n");
        sys.stdout.flush();
        if close_control and control_fd is not None:
            try: os.close(control_fd);
            except OSError: pass;


def app_less(argv, stdin="", runtime=None):
    text,options,message,code=_less_read_sources(argv,stdin,runtime);
    if text is None: return AppletResult(code,out=message if code==0 else "",err=message if code else "");
    if _less_interactive(text,options,runtime): return AppletResult();
    return AppletResult(out=text if text.endswith("\n") or not text else text+"\n");

def app_suminfo(argv, stdin="", runtime=None):
    # Capture suminfo's pure renderer instead of spawning another process.
    if "--short" in argv: return AppletResult(out=suminfo.render_identity_short(suminfo.collect_identity())+"\n");
    if "--field" in argv:
        i=argv.index("--field");
        if i+1>=len(argv): return AppletResult(2,err="suminfo: --field requires a path\n");
        try: value=suminfo.identity_field(suminfo.collect_identity(),argv[i+1]);
        except KeyError: return AppletResult(2,err="suminfo: unknown field: {}\n".format(argv[i+1]));
        return AppletResult(out=("" if value is None else str(value))+"\n");
    report=suminfo.collect_report(groups=("platform",),detailed="-a" in argv or "--all" in argv);
    return AppletResult(out=suminfo.render_report(report,detailed="-a" in argv or "--all" in argv)+"\n");


def app_test(argv, stdin="", runtime=None):
    args=list(argv);
    if args and args[-1] in ("]", "]]" ): args.pop();
    if not args: return AppletResult(1);
    if args[0]=="!": r=app_test(args[1:],stdin,runtime); return AppletResult(0 if r.code else 1);
    unary={"-e":lambda p:_runtime_path(p,runtime).exists(),"-f":lambda p:_runtime_path(p,runtime).is_file(),"-d":lambda p:_runtime_path(p,runtime).is_dir(),"-x":lambda p:os.access(_runtime_path(p,runtime),os.X_OK),"-r":lambda p:os.access(_runtime_path(p,runtime),os.R_OK),"-w":lambda p:os.access(_runtime_path(p,runtime),os.W_OK),"-t":lambda fd:os.isatty(int(fd)),"-z":lambda s:len(s)==0,"-n":lambda s:len(s)!=0};
    if len(args)>=2 and args[0] in unary:
        try: return AppletResult(0 if unary[args[0]](args[1]) else 1);
        except OSError: return AppletResult(1);
    if len(args)==1: return AppletResult(0 if args[0] else 1);
    if len(args)>=3:
        a,op,b=args[0],args[1],args[2];
        if op in ("=","=="): ok=a==b;
        elif op=="!=": ok=a!=b;
        elif op in ("-eq","-ne","-lt","-le","-gt","-ge"):
            try: x=float(a); y=float(b);
            except ValueError: return AppletResult(2,err="test: integer expression expected\n");
            ok={"-eq":x==y,"-ne":x!=y,"-lt":x<y,"-le":x<=y,"-gt":x>y,"-ge":x>=y}[op];
        else: return AppletResult(2,err="test: unsupported operator {}\n".format(op));
        return AppletResult(0 if ok else 1);
    return AppletResult(2,err="test: unsupported expression\n");


def app_df(argv,stdin="",runtime=None):
    if runtime is None or not hasattr(runtime,"fsa"): return AppletResult(1,err="df: sumFSA unavailable\n");
    human=False; show_all=False; print_type=False; paths=[]; args=list(argv); i=0;
    while i<len(args):
        arg=args[i];
        if arg=="--": paths.extend(args[i+1:]); break;
        if arg in ("-h","--human-readable"): human=True; i+=1; continue;
        if arg in ("-a","--all"): show_all=True; i+=1; continue;
        if arg in ("-T","--print-type"): print_type=True; i+=1; continue;
        if arg.startswith("-") and not arg.startswith("--") and len(arg)>2:
            bad=None;
            for ch in arg[1:]:
                if ch=="a": show_all=True;
                elif ch=="h": human=True;
                elif ch=="T": print_type=True;
                else: bad=ch; break;
            if bad is not None: return AppletResult(2,err="df: invalid option -- '{}'\n".format(bad));
            i+=1; continue;
        if arg in ("--help",):
            return AppletResult(out="Usage: df [OPTION]... [FILE]...\n  -a, --all             include normally hidden filesystems\n  -h, --human-readable  print sizes in powers of 1024\n  -T, --print-type      print filesystem type\n");
        if arg.startswith("-") and arg!="-": return AppletResult(2,err="df: unrecognized option '{}'\n".format(arg));
        paths.append(arg); i+=1;
    hidden_types={"proc","sysfs","devtmpfs","devpts","cgroup","cgroup2","pstore","securityfs","debugfs","tracefs","configfs","fusectl","mqueue","hugetlbfs","autofs","binfmt_misc"};
    mounts=list(runtime.fsa.mounts());
    if paths:
        selected=[];
        for raw in paths:
            try: target=Path(runtime.fsa.native_path(raw,cwd=getattr(runtime,"logical_cwd",runtime.fsa.cwd))).resolve();
            except Exception: target=_runtime_path(raw,runtime).resolve();
            candidates=[];
            for mount in mounts:
                try:
                    root=Path(mount.native_root).resolve();
                    target.relative_to(root); candidates.append((len(root.parts),mount));
                except (OSError,ValueError): pass;
            if candidates: selected.append(max(candidates,key=lambda item:item[0])[1]);
        mounts=selected;
    # GNU df suppresses duplicate mount entries by default.  Use the native
    # filesystem device id plus source to collapse bind mounts such as
    # /proc/keys -> /dev and repeated virtiofs mounts, keeping the shortest
    # visible mount point.  -a/--all deliberately disables this filtering.
    candidates=[];
    for mount in mounts:
        source=mount.source or mount.native_root; root=mount.logical_root; fstype=mount.fs_type or "-";
        is_snap=(fstype=="squashfs" and (str(root).startswith("/snap/") or str(source).startswith("/dev/loop")));
        if not show_all and (fstype in hidden_types or is_snap): continue;
        try: st_dev=os.stat(mount.native_root).st_dev;
        except OSError: st_dev=None;
        candidates.append((mount,source,root,fstype,st_dev));
    if not show_all:
        preferred={};
        for item in candidates:
            mount,source,root,fstype,st_dev=item; key=(str(source),st_dev);
            old=preferred.get(key);
            if old is None or (len(str(root)),str(root)) < (len(str(old[2])),str(old[2])): preferred[key]=item;
        candidates=list(preferred.values());
    rows=[]; seen=set();
    for mount,source,root,fstype,unused_st_dev in candidates:
        key=(source,root);
        if key in seen: continue;
        seen.add(key);
        try: usage=shutil.disk_usage(mount.native_root); total=int(usage.total); free=int(usage.free); used=total-free;
        except OSError: continue;
        pct=0 if total<=0 else int(math.ceil((used*100.0)/total));
        if human:
            size=_human_size(total); used_text=_human_size(used); free_text=_human_size(free);
        else:
            block=1024; size=str((total+block-1)//block); used_text=str((used+block-1)//block); free_text=str(free//block);
        rows.append((str(source),str(fstype),size,used_text,free_text,"{}%".format(pct),str(root)));
    if print_type:
        widths=[len(x) for x in ("Filesystem","Type","Size","Used","Avail","Use%")];
        for row in rows:
            for idx in range(6): widths[idx]=max(widths[idx],len(row[idx]));
        out=["{:<{}} {:<{}} {:>{}} {:>{}} {:>{}} {:>{}} Mounted on".format("Filesystem",widths[0],"Type",widths[1],"Size",widths[2],"Used",widths[3],"Avail",widths[4],"Use%",widths[5])];
        for row in rows: out.append("{:<{}} {:<{}} {:>{}} {:>{}} {:>{}} {:>{}} {}".format(row[0],widths[0],row[1],widths[1],row[2],widths[2],row[3],widths[3],row[4],widths[4],row[5],widths[5],row[6]));
    else:
        widths=[len(x) for x in ("Filesystem","Size","Used","Avail","Use%")];
        for row in rows:
            for idx,val in enumerate((row[0],row[2],row[3],row[4],row[5])): widths[idx]=max(widths[idx],len(val));
        out=["{:<{}} {:>{}} {:>{}} {:>{}} {:>{}} Mounted on".format("Filesystem",widths[0],"Size",widths[1],"Used",widths[2],"Avail",widths[3],"Use%",widths[4])];
        for row in rows: out.append("{:<{}} {:>{}} {:>{}} {:>{}} {:>{}} {}".format(row[0],widths[0],row[2],widths[1],row[3],widths[2],row[4],widths[3],row[5],widths[4],row[6]));
    return AppletResult(out="\n".join(out)+"\n");


APPLETS = {
    "echo": app_echo, "printf": app_printf, "cat": app_cat, "rev": app_rev,
    "basename": app_basename, "dirname": app_dirname, "pwd": app_pwd, "realpath": app_realpath,
    "clear": app_clear, "sleep": app_sleep, "ls": app_ls, "find": app_find, "grep": app_grep,
    "egrep": app_egrep, "fgrep": app_fgrep, "cut": app_cut, "sed": app_sed, "head": app_head,
    "tail": app_tail, "sort": app_sort, "uniq": app_uniq, "wc": app_wc, "tee": app_tee,
    "date": app_date, "uptime": app_uptime, "uname": app_uname, "lsb_release": app_lsb_release,
    "hostname": app_hostname, "arch": app_arch, "whoami": app_whoami, "tty": app_tty, "df": app_df, "less": app_less, "suminfo": app_suminfo,
    "test": app_test, "[": app_test, "[[": app_test,
};


def applet_names(): return tuple(sorted(APPLETS));

def run_applet(name, argv, stdin="", runtime=None):
    applet=APPLETS.get(name);
    if applet is None: return None;
    return applet(list(argv),stdin=stdin,runtime=runtime);
