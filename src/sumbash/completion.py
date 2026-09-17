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
"""Portable completion engine for sumbash.

The interactive frontend is GNU/readline when available, but candidate
collection is deliberately independent from readline so the same resolver can
later be reused by sumTerm and Android.
""";

from __future__ import annotations;

from dataclasses import dataclass, field;
import fnmatch;
import os;
from pathlib import Path;
import re;
import shlex;
import shutil;
import signal;

from .applets import APPLETS;


_SHELL_SEPARATORS={";","|","&&","||"};
_SPECIAL_ESCAPE=set(" \t\n\\\"'`$&;|<>*?[](){}!");


@dataclass
class CompletionSpec:
    actions: list[str] = field(default_factory=list);
    words: list[str] = field(default_factory=list);
    options: set[str] = field(default_factory=set);
    prefix: str = "";
    suffix: str = "";
    filter_pattern: str | None = None;
    function: str | None = None;


@dataclass
class CompletionContext:
    line: str;
    cursor: int;
    start: int;
    text: str;
    command: str;
    words: list[str];
    command_position: bool;


def _scan_word_start(line,cursor):
    """Return the start of the shell word containing *cursor*.

    This is intentionally tolerant of incomplete quotes, because completion is
    normally requested while a command line is unfinished.
    """;
    quote=None; escaped=False; start=0; i=0;
    while i<cursor:
        ch=line[i];
        if escaped:
            escaped=False; i+=1; continue;
        if ch=="\\" and quote!="'":
            escaped=True; i+=1; continue;
        if quote:
            if ch==quote: quote=None;
            i+=1; continue;
        if ch in ("'",'"'):
            quote=ch; i+=1; continue;
        if ch.isspace(): start=i+1; i+=1; continue;
        if ch in ";|&<>":
            # For && and || the right hand side starts after the complete op.
            if i+1<cursor and line[i+1]==ch and ch in "|&": i+=1;
            start=i+1;
        i+=1;
    return start;


def _split_command_words(text):
    """Small completion-only lexer; unlike execution it tolerates open quotes.""";
    words=[]; buf=[]; quote=None; escaped=False; i=0;
    def flush():
        if buf: words.append("".join(buf)); buf.clear();
    while i<len(text):
        ch=text[i];
        if escaped:
            buf.append(ch); escaped=False; i+=1; continue;
        if ch=="\\" and quote!="'":
            escaped=True; i+=1; continue;
        if quote:
            if ch==quote: quote=None;
            else: buf.append(ch);
            i+=1; continue;
        if ch in ("'",'"'):
            quote=ch; i+=1; continue;
        if ch.isspace(): flush(); i+=1; continue;
        op=None;
        for candidate in ("&&","||",";","|"):
            if text.startswith(candidate,i): op=candidate; break;
        if op:
            flush(); words.append(op); i+=len(op); continue;
        if ch in "<>":
            flush(); words.append(ch); i+=1; continue;
        buf.append(ch); i+=1;
    flush();
    return words;


def completion_context(line,cursor=None):
    line=str(line); cursor=len(line) if cursor is None else max(0,min(int(cursor),len(line)));
    start=_scan_word_start(line,cursor); raw=line[start:cursor];
    # Preserve quote information for insertion, but matching uses the unquoted body.
    text=raw;
    if text.startswith(("'",'"')): text=text[1:];
    # Readline leaves shell backslash quoting in the buffer; matching must use
    # the logical filename while insertion re-applies quoting later.
    text=re.sub(r"\\(.)",r"\1",text);
    before=line[:start]; tokens=_split_command_words(before); segment=[];
    for tok in tokens:
        if tok in _SHELL_SEPARATORS: segment=[];
        elif tok in ("<",">"): continue;
        else: segment.append(tok);
    command=segment[0] if segment else "";
    return CompletionContext(line,cursor,start,text,command,segment,not bool(segment));


def _escape_candidate(value,raw_text=""):
    """Quote candidate for insertion without changing the user's quote style.""";
    if raw_text.startswith("'"):
        return "'"+value.replace("'","'\\''");
    if raw_text.startswith('"'):
        return '"'+value.replace("\\","\\\\").replace('"','\\"').replace("$","\\$").replace("`","\\`");
    out=[];
    for ch in value:
        if ch.isspace() or ch in _SPECIAL_ESCAPE: out.append("\\"+ch);
        else: out.append(ch);
    return "".join(out);


def _expand_tilde_for_lookup(text,home):
    if text=="~": return str(Path(home));
    if text.startswith("~/"): return str(Path(home)/text[2:]);
    return text;


def _restore_tilde(value,text,home):
    if text.startswith("~"):
        home=str(Path(home));
        if value==home: return "~";
        if value.startswith(home+os.sep): return "~"+value[len(home):];
    return value;


def file_candidates(text,cwd,home=None,directories_only=False):
    home=home or str(Path.home()); original=str(text); lookup=_expand_tilde_for_lookup(original,home);
    path=Path(lookup);
    if not path.is_absolute(): path=Path(cwd)/path;
    parent=path.parent; prefix=path.name;
    if lookup.endswith(os.sep): parent=path; prefix="";
    try: entries=list(parent.iterdir());
    except OSError: return [];
    results=[];
    for entry in entries:
        name=entry.name;
        if not prefix.startswith(".") and name.startswith("."): continue;
        if not name.startswith(prefix): continue;
        try: is_dir=entry.is_dir();
        except OSError: is_dir=False;
        if directories_only and not is_dir: continue;
        if Path(lookup).is_absolute(): shown=str(entry);
        else:
            base=os.path.dirname(lookup);
            shown=os.path.join(base,name) if base else name;
        shown=_restore_tilde(shown,original,home);
        if is_dir: shown+=os.sep;
        results.append(shown);
    return sorted(dict.fromkeys(results),key=lambda v:v.casefold());


def command_candidates(prefix,runtime):
    values=set(); prefix=str(prefix);
    values.update(runtime.aliases);
    values.update(runtime._builtin_names());
    values.update(APPLETS);
    path_value=runtime.get("PATH") or os.environ.get("PATH","");
    pathext=[""];
    if os.name=="nt": pathext=[x.lower() for x in (runtime.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD").split(";") if x];
    for directory in path_value.split(os.pathsep):
        if not directory: continue;
        try: entries=Path(directory).iterdir();
        except OSError: continue;
        for entry in entries:
            try:
                if not entry.is_file(): continue;
                name=entry.name;
                if os.name=="nt":
                    suffix=entry.suffix.lower();
                    if suffix not in pathext: continue;
                    name=entry.stem if suffix in pathext else name;
                elif not os.access(entry,os.X_OK): continue;
                values.add(name);
            except OSError: continue;
    return sorted((v for v in values if v.startswith(prefix)),key=lambda v:v.casefold());


def variable_candidates(prefix,runtime,braced=False):
    values=sorted((name for name in runtime.vars if name.startswith(prefix)),key=lambda v:v.casefold());
    if braced: return ["${"+name+"}" for name in values];
    return ["$"+name for name in values];


def _action_candidates(action,prefix,runtime):
    action=str(action or "");
    if action in ("command","builtin"): return command_candidates(prefix,runtime);
    if action in ("file","filename"): return file_candidates(prefix,runtime.cwd,runtime.get("HOME"));
    if action in ("directory","dir"): return file_candidates(prefix,runtime.cwd,runtime.get("HOME"),directories_only=True);
    if action in ("variable","export","arrayvar"): return sorted(name for name in runtime.vars if name.startswith(prefix));
    if action=="alias": return sorted(name for name in runtime.aliases if name.startswith(prefix));
    if action=="signal":
        names=[];
        for name in dir(signal):
            if name.startswith("SIG") and "_" not in name[3:]: names.append(name[3:]);
        return sorted(name for name in set(names) if name.startswith(prefix));
    if action=="user":
        try:
            import pwd;
            return sorted(x.pw_name for x in pwd.getpwall() if x.pw_name.startswith(prefix));
        except ImportError: return [];
    if action=="group":
        try:
            import grp;
            return sorted(x.gr_name for x in grp.getgrall() if x.gr_name.startswith(prefix));
        except ImportError: return [];
    if action=="hostname":
        names=set();
        for path in (Path.home()/".ssh"/"known_hosts",Path("/etc/hosts")):
            try: lines=path.read_text(encoding="utf-8",errors="replace").splitlines();
            except OSError: continue;
            for line in lines:
                line=line.strip();
                if not line or line.startswith("#") or line.startswith("|"): continue;
                fields=line.split();
                if path.name=="known_hosts": candidates=fields[0].split(",");
                else: candidates=fields[1:];
                for item in candidates:
                    host=item.split("[")[-1].split("]")[0].split(":")[0];
                    if host and not any(ch in host for ch in "*?"): names.add(host);
        return sorted(name for name in names if name.startswith(prefix));
    return [];


def spec_candidates(spec,prefix,runtime):
    results=[];
    for word in spec.words:
        if word.startswith(prefix): results.append(word);
    for action in spec.actions:
        results.extend(_action_candidates(action,prefix,runtime));
    if spec.filter_pattern:
        pattern=spec.filter_pattern;
        results=[v for v in results if not fnmatch.fnmatch(v,pattern)];
    results=[spec.prefix+v+spec.suffix for v in results];
    return sorted(dict.fromkeys(results),key=lambda v:v.casefold());


class CompletionEngine:
    def __init__(self,runtime): self.runtime=runtime;

    def candidates(self,line,cursor=None):
        ctx=completion_context(line,cursor); raw=ctx.line[ctx.start:ctx.cursor]; text=ctx.text;
        # Bash-like variable completion.
        if text.startswith("${"):
            return variable_candidates(text[2:],self.runtime,braced=True);
        if text.startswith("$"):
            return variable_candidates(text[1:],self.runtime,braced=False);
        if ctx.command_position:
            if "/" in text or text.startswith((".","~")):
                values=file_candidates(text,self.runtime.cwd,self.runtime.get("HOME"));
                # In command position, ordinary files must be executable, directories remain navigable.
                filtered=[];
                for value in values:
                    lookup=_expand_tilde_for_lookup(value.rstrip(os.sep),self.runtime.get("HOME") or str(Path.home()));
                    p=Path(lookup); p=p if p.is_absolute() else Path(self.runtime.cwd)/p;
                    if value.endswith(os.sep) or os.access(p,os.X_OK): filtered.append(value);
                values=filtered;
            else: values=command_candidates(text,self.runtime);
            return [_escape_candidate(v,raw) for v in values];
        command=ctx.command;
        spec=self.runtime.completion_specs.get(command);
        if spec is not None:
            values=spec_candidates(spec,text,self.runtime);
            if values: return [_escape_candidate(v,raw) for v in values];
            if not ({"default","bashdefault"} & spec.options): return [];
        if command=="cd": values=file_candidates(text,self.runtime.cwd,self.runtime.get("HOME"),directories_only=True);
        elif command in ("command","type"): values=command_candidates(text,self.runtime);
        elif command in ("export","global","readonly","unset"):
            values=sorted(name for name in self.runtime.vars if name.startswith(text));
        else: values=file_candidates(text,self.runtime.cwd,self.runtime.get("HOME"));
        return [_escape_candidate(v,raw) for v in values];

    def readline_completer(self,text,state):
        rl=self.runtime._readline; readline_start=0;
        try:
            line=rl.get_line_buffer(); cursor=rl.get_endidx(); readline_start=rl.get_begidx();
        except Exception:
            line=text; cursor=len(text); readline_start=0;
        if state==0:
            ctx=completion_context(line,cursor); values=self.candidates(line,cursor);
            # GNU readline determines its replacement span from a flat delimiter
            # table and therefore treats an escaped/quoted space as a delimiter.
            # Our shell lexer correctly keeps that space inside the current word.
            # If readline starts replacing in the middle of that logical word,
            # return only the not-yet-present suffix or it duplicates the prefix.
            if readline_start>ctx.start:
                present=line[ctx.start:readline_start]; adjusted=[];
                for value in values:
                    adjusted.append(value[len(present):] if value.startswith(present) else value);
                values=adjusted;
            self.runtime._completion_cache=values;
            # Expose the standard Bash variables for future programmable completion.
            self.runtime.vars["COMP_LINE"]=line;
            self.runtime.vars["COMP_POINT"]=str(cursor);
            self.runtime.vars["COMP_CWORD"]=str(max(0,len(ctx.words)));
        cache=getattr(self.runtime,"_completion_cache",[]);
        return cache[state] if state<len(cache) else None;


def parse_completion_spec(args):
    spec=CompletionSpec(); names=[]; i=0; print_mode=False; remove=False;
    while i<len(args):
        arg=args[i];
        if arg=="--": names.extend(args[i+1:]); break;
        if arg=="-p": print_mode=True; i+=1; continue;
        if arg=="-r": remove=True; i+=1; continue;
        if arg in ("-f","-d","-c"):
            spec.actions.append({"-f":"file","-d":"directory","-c":"command"}[arg]); i+=1; continue;
        if arg in ("-A","-W","-o","-P","-S","-X","-F") and i+1<len(args):
            value=args[i+1];
            if arg=="-A": spec.actions.append(value);
            elif arg=="-W": spec.words.extend(value.split());
            elif arg=="-o": spec.options.add(value);
            elif arg=="-P": spec.prefix=value;
            elif arg=="-S": spec.suffix=value;
            elif arg=="-X": spec.filter_pattern=value;
            elif arg=="-F": spec.function=value;
            i+=2; continue;
        if arg.startswith("-"): raise ValueError("unsupported option: {}".format(arg));
        names.append(arg); i+=1;
    return spec,names,print_mode,remove;


def format_completion_spec(name,spec):
    parts=["complete"];
    for action in spec.actions: parts.extend(["-A",shlex.quote(action)]);
    if spec.words: parts.extend(["-W",shlex.quote(" ".join(spec.words))]);
    for option in sorted(spec.options): parts.extend(["-o",shlex.quote(option)]);
    if spec.prefix: parts.extend(["-P",shlex.quote(spec.prefix)]);
    if spec.suffix: parts.extend(["-S",shlex.quote(spec.suffix)]);
    if spec.filter_pattern: parts.extend(["-X",shlex.quote(spec.filter_pattern)]);
    if spec.function: parts.extend(["-F",shlex.quote(spec.function)]);
    parts.append(shlex.quote(name));
    return " ".join(parts);
