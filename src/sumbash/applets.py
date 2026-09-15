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
import os;
from pathlib import Path;
import re;
import shutil;
import socket;
import sys;
import time;

from sumcore import info as suminfo;


@dataclass
class AppletResult:
    code: int = 0;
    out: str = "";
    err: str = "";


def _text_input(stdin): return "" if stdin is None else str(stdin);

def _read_paths(paths, stdin=""):
    if not paths: return [("-", _text_input(stdin))];
    rows = [];
    for value in paths:
        if value == "-": rows.append(("-", _text_input(stdin))); continue;
        try: rows.append((value, Path(value).read_text(encoding="utf-8", errors="replace")));
        except OSError as exc: rows.append((value, exc));
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


def app_cat(argv, stdin="", runtime=None):
    out=[]; err=[]; code=0;
    for name, value in _read_paths(argv, stdin):
        if isinstance(value, Exception): err.append("cat: {}: {}\n".format(name, value)); code=1;
        else: out.append(value);
    return AppletResult(code, "".join(out), "".join(err));


def app_rev(argv, stdin="", runtime=None):
    out=[]; err=[]; code=0;
    for name, value in _read_paths(argv, stdin):
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
    physical = "-P" in argv;
    cwd = Path(runtime.cwd if runtime is not None else os.getcwd());
    value = str(cwd.resolve()) if physical else str(cwd);
    return AppletResult(out=value + "\n");


def app_realpath(argv, stdin="", runtime=None):
    if not argv: argv=["."];
    out=[];
    base = Path(runtime.cwd if runtime is not None else os.getcwd());
    for raw in argv:
        p=Path(raw);
        if not p.is_absolute(): p=base/p;
        out.append(str(p.resolve()));
    return AppletResult(out="\n".join(out)+"\n");


def app_clear(argv, stdin="", runtime=None): return AppletResult(out="\x1b[H\x1b[J");

def app_sleep(argv, stdin="", runtime=None):
    if not argv: return AppletResult(1, err="sleep: missing operand\n");
    try: time.sleep(float(argv[0])); return AppletResult();
    except ValueError: return AppletResult(1, err="sleep: invalid time interval\n");


def _human_size(value):
    n=float(value);
    for suffix in ("B","K","M","G","T","P"):
        if abs(n)<1024 or suffix=="P": return ("{:.0f}{}" if suffix=="B" else "{:.1f}{}").format(n,suffix);
        n/=1024;


def app_ls(argv, stdin="", runtime=None):
    show_all=False; long=False; human=False; classify=False; paths=[];
    for arg in argv:
        if arg.startswith("-") and arg != "-":
            show_all = show_all or "a" in arg;
            long = long or "l" in arg;
            human = human or "h" in arg;
            classify = classify or "F" in arg;
        else: paths.append(arg);
    if not paths: paths=["."];
    out=[]; err=[]; code=0;
    for pindex, raw in enumerate(paths):
        base=Path(runtime.cwd if runtime is not None else os.getcwd()); p=Path(raw); p=p if p.is_absolute() else base/p;
        try:
            items=[p] if not p.is_dir() else sorted(p.iterdir(), key=lambda x:x.name.casefold());
        except OSError as exc: err.append("ls: {}: {}\n".format(raw,exc)); code=2; continue;
        if len(paths)>1: out.append("{}:\n".format(raw));
        for item in items:
            if item.name.startswith(".") and not show_all and item != p: continue;
            name=item.name if item != p else raw;
            if classify:
                if item.is_dir(): name += "/";
                elif os.access(item, os.X_OK): name += "*";
                elif item.is_symlink(): name += "@";
            if long:
                try:
                    st=item.lstat(); mode="d" if item.is_dir() else ("l" if item.is_symlink() else "-");
                    perms="".join("rwx"[i%3] if st.st_mode & (1 << (8-i)) else "-" for i in range(9));
                    size=_human_size(st.st_size) if human else str(st.st_size);
                    stamp=datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M");
                    out.append("{}{} {:>8} {} {}\n".format(mode,perms,size,stamp,name));
                except OSError as exc: err.append("ls: {}: {}\n".format(raw,exc)); code=1;
            else: out.append(name+"\n");
        if pindex+1<len(paths): out.append("\n");
    return AppletResult(code,"".join(out),"".join(err));


def app_find(argv, stdin="", runtime=None):
    args=list(argv); roots=[];
    while args and not args[0].startswith("-"): roots.append(args.pop(0));
    if not roots: roots=["."];
    name_pat=None; insensitive=False; ftype=None; maxdepth=None;
    i=0;
    while i<len(args):
        a=args[i];
        if a in ("-name","-iname") and i+1<len(args): name_pat=args[i+1]; insensitive=a=="-iname"; i+=2; continue;
        if a=="-type" and i+1<len(args): ftype=args[i+1]; i+=2; continue;
        if a=="-maxdepth" and i+1<len(args): maxdepth=int(args[i+1]); i+=2; continue;
        i+=1;
    cwd=Path(runtime.cwd if runtime is not None else os.getcwd()); out=[]; err=[]; code=0;
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
            out.append(shown+"\n");
    return AppletResult(code,"".join(out),"".join(err));


def app_grep(argv, stdin="", runtime=None):
    ignore=False; invert=False; number=False; quiet=False; recursive=False; patterns=[]; files=[]; i=0;
    while i<len(argv):
        a=argv[i];
        if a in ("-i","--ignore-case"): ignore=True; i+=1; continue;
        if a in ("-v","--invert-match"): invert=True; i+=1; continue;
        if a in ("-n","--line-number"): number=True; i+=1; continue;
        if a in ("-q","--quiet"): quiet=True; i+=1; continue;
        if a in ("-r","-R","--recursive"): recursive=True; i+=1; continue;
        if a in ("-e","--regexp") and i+1<len(argv): patterns.append(argv[i+1]); i+=2; continue;
        if not patterns: patterns.append(a);
        else: files.append(a);
        i+=1;
    if not patterns: return AppletResult(2,err="grep: missing pattern\n");
    flags=re.IGNORECASE if ignore else 0;
    try: regs=[re.compile(p,flags) for p in patterns];
    except re.error as exc: return AppletResult(2,err="grep: {}\n".format(exc));
    inputs=[];
    if recursive and files:
        for raw in files:
            p=Path(raw);
            if p.is_dir(): inputs.extend(str(x) for x in p.rglob("*") if x.is_file());
            else: inputs.append(raw);
    else: inputs=files;
    found=False; out=[]; err=[];
    for name,value in _read_paths(inputs,stdin):
        if isinstance(value,Exception): err.append("grep: {}: {}\n".format(name,value)); continue;
        for lno,line in enumerate(value.splitlines(),1):
            hit=any(r.search(line) for r in regs); hit = not hit if invert else hit;
            if hit:
                found=True;
                if quiet: return AppletResult(0);
                prefix="";
                if len(inputs)>1: prefix += name+":";
                if number: prefix += str(lno)+":";
                out.append(prefix+line+"\n");
    return AppletResult(0 if found else 1,"".join(out),"".join(err));


def app_cut(argv, stdin="", runtime=None):
    fields=None; delim="\t"; chars=None; files=[]; i=0;
    while i<len(argv):
        a=argv[i];
        if a in ("-f","--fields") and i+1<len(argv): fields=argv[i+1]; i+=2; continue;
        if a.startswith("-f") and len(a)>2: fields=a[2:]; i+=1; continue;
        if a in ("-d","--delimiter") and i+1<len(argv): delim=argv[i+1]; i+=2; continue;
        if a.startswith("-d") and len(a)>2: delim=a[2:]; i+=1; continue;
        if a in ("-c","--characters") and i+1<len(argv): chars=argv[i+1]; i+=2; continue;
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
    for name,value in _read_paths(files,stdin):
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
    for name,value in _read_paths(files,stdin):
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
    n=10; files=[]; i=0;
    while i<len(argv):
        if argv[i] in ("-n","--lines") and i+1<len(argv): n=int(argv[i+1]); i+=2; continue;
        if argv[i].startswith("-") and argv[i][1:].isdigit(): n=int(argv[i][1:]); i+=1; continue;
        files.append(argv[i]); i+=1;
    out=[]; err=[]; code=0;
    for name,value in _read_paths(files,stdin):
        if isinstance(value,Exception): err.append("head: {}: {}\n".format(name,value)); code=1;
        else: out.extend(value.splitlines(True)[:n]);
    return AppletResult(code,"".join(out),"".join(err));


def app_tail(argv, stdin="", runtime=None):
    n=10; files=[]; i=0;
    while i<len(argv):
        if argv[i] in ("-n","--lines") and i+1<len(argv): n=int(argv[i+1]); i+=2; continue;
        if argv[i].startswith("-") and argv[i][1:].isdigit(): n=int(argv[i][1:]); i+=1; continue;
        files.append(argv[i]); i+=1;
    out=[]; err=[]; code=0;
    for name,value in _read_paths(files,stdin):
        if isinstance(value,Exception): err.append("tail: {}: {}\n".format(name,value)); code=1;
        else: out.extend(value.splitlines(True)[-n:]);
    return AppletResult(code,"".join(out),"".join(err));


def app_sort(argv, stdin="", runtime=None):
    reverse="-r" in argv; numeric="-n" in argv; unique="-u" in argv; files=[a for a in argv if not a.startswith("-")]; lines=[]; err=[];
    for name,value in _read_paths(files,stdin):
        if isinstance(value,Exception): err.append("sort: {}: {}\n".format(name,value)); continue;
        lines.extend(value.splitlines());
    key=(lambda x: float(x.strip() or 0)) if numeric else None;
    try: lines.sort(key=key,reverse=reverse);
    except ValueError: return AppletResult(2,err="sort: non-numeric input\n");
    if unique: lines=list(dict.fromkeys(lines));
    return AppletResult(0,"".join(x+"\n" for x in lines),"".join(err));


def app_uniq(argv, stdin="", runtime=None):
    count="-c" in argv; files=[a for a in argv if not a.startswith("-")]; data=_read_paths(files,stdin); out=[]; err=[]; code=0;
    lines=[];
    for name,value in data:
        if isinstance(value,Exception): err.append("uniq: {}: {}\n".format(name,value)); code=1;
        else: lines.extend(value.splitlines());
    last=None; n=0;
    def emit(v,c):
        if v is None: return;
        out.append(("{:7d} ".format(c) if count else "")+v+"\n");
    for line in lines:
        if line==last: n+=1;
        else: emit(last,n); last=line; n=1;
    emit(last,n);
    return AppletResult(code,"".join(out),"".join(err));


def app_wc(argv, stdin="", runtime=None):
    want_l="-l" in argv; want_w="-w" in argv; want_c="-c" in argv; files=[a for a in argv if not a.startswith("-")];
    if not (want_l or want_w or want_c): want_l=want_w=want_c=True;
    out=[]; err=[]; code=0;
    for name,value in _read_paths(files,stdin):
        if isinstance(value,Exception): err.append("wc: {}: {}\n".format(name,value)); code=1; continue;
        vals=[];
        if want_l: vals.append(str(len(value.splitlines())));
        if want_w: vals.append(str(len(value.split())));
        if want_c: vals.append(str(len(value.encode("utf-8"))));
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
            with open(raw,mode,encoding="utf-8") as stream: stream.write(_text_input(stdin));
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
    unary={"-e":lambda p:Path(p).exists(),"-f":lambda p:Path(p).is_file(),"-d":lambda p:Path(p).is_dir(),"-x":lambda p:os.access(p,os.X_OK),"-r":lambda p:os.access(p,os.R_OK),"-w":lambda p:os.access(p,os.W_OK),"-z":lambda s:len(s)==0,"-n":lambda s:len(s)!=0};
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


APPLETS = {
    "echo": app_echo, "printf": app_printf, "cat": app_cat, "rev": app_rev,
    "basename": app_basename, "dirname": app_dirname, "pwd": app_pwd, "realpath": app_realpath,
    "clear": app_clear, "sleep": app_sleep, "ls": app_ls, "find": app_find, "grep": app_grep,
    "egrep": app_grep, "fgrep": app_grep, "cut": app_cut, "sed": app_sed, "head": app_head,
    "tail": app_tail, "sort": app_sort, "uniq": app_uniq, "wc": app_wc, "tee": app_tee,
    "date": app_date, "uptime": app_uptime, "uname": app_uname, "lsb_release": app_lsb_release,
    "hostname": app_hostname, "arch": app_arch, "whoami": app_whoami, "suminfo": app_suminfo,
    "test": app_test, "[": app_test, "[[": app_test,
};


def applet_names(): return tuple(sorted(APPLETS));

def run_applet(name, argv, stdin="", runtime=None):
    applet=APPLETS.get(name);
    if applet is None: return None;
    return applet(list(argv),stdin=stdin,runtime=runtime);
