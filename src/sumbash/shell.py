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
"""Small portable shell core for sumbash 0.2.0a5.

This alpha intentionally implements a useful vertical slice: variables,
expansion, arithmetic with fractions, command substitution, pipelines,
redirections, aliases, selected Bash-compatible builtins and external fallback.
This alpha adds indexed arrays and practical for/in loops for real SUM maintenance scripts.
Broader compound grammar (if/while/functions/associative arrays) is scheduled for later alphas.
""";

from __future__ import annotations;

from dataclasses import dataclass;
from importlib.resources import files as resource_files;
from datetime import datetime;
import difflib;
import fnmatch;
import getpass;
import glob;
import io;
import os;
from pathlib import Path;
import re;
import shutil;
import socket;
import subprocess;
import sys;

from . import __version__;
from .applets import APPLETS, AppletResult, run_applet;
from .arithmetic import SumArithmeticError, evaluate, format_number;
from .completion import CompletionEngine, CompletionSpec, format_completion_spec, parse_completion_spec, spec_candidates;
from sumfsa import FileSystem;
from sumio import open_resource;


_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$");
_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S);
_OPERATORS = ("2>&1", "1>&2", "2>>", "1>>", "2>", "1>", "0<", "&&", "||", ">>", "|", ";", ">", "<");
_HELP_TRIGGER_PREFIX="__sum_help__ ";
_HELP_CURSOR_MARKER="__SUM_HELP_POINT_4F6D2E__";


class ShellWord(str):
    """A token plus the pathname-expansion pattern that survived quote removal.""";
    def __new__(cls, value, glob_pattern=None):
        obj=str.__new__(cls, value);
        obj.glob_pattern=glob_pattern;
        return obj;


@dataclass
class Execution:
    code: int = 0;
    out: object = "";
    err: str = "";


def _stream_text(value):
    """Decode captured external bytes only at a text boundary.""";
    if isinstance(value,(bytes,bytearray)): return bytes(value).decode("utf-8",errors="replace");
    return str(value);


def _stream_bytes(value):
    if isinstance(value,(bytes,bytearray)): return bytes(value);
    return str(value).encode("utf-8");


def _stream_join(values):
    values=list(values);
    if any(isinstance(v,(bytes,bytearray)) for v in values):
        return b"".join(_stream_bytes(v) for v in values);
    return "".join(str(v) for v in values);


class ShellExit(Exception):
    def __init__(self, code=0): self.code=int(code);


class ShellReturn(Exception):
    def __init__(self, code=0): self.code=int(code);


class ShellBreak(Exception):
    pass;


class ShellContinue(Exception):
    pass;


class ShellRuntime:
    def __init__(self, argv=None, env=None, cwd=None, interactive=False, argv0="sumbash"):
        self.env = dict(os.environ if env is None else env);
        self.vars = dict(self.env);
        # SUM follows the common interactive Bash distro convention: leading-space
        # commands are private from recall history and adjacent duplicates collapse.
        # An explicit HISTCONTROL from the caller always wins.
        self.vars.setdefault("HISTCONTROL","ignoreboth");
        self.exported = set(self.env);
        try:
            _sum_shell_path=str(Path(sys.argv[0]).resolve()) if Path(sys.argv[0]).exists() else (shutil.which("sumbash") or str(argv0));
        except Exception:
            _sum_shell_path=shutil.which("sumbash") or str(argv0);
        self.vars["SUM_SHELL"]=_sum_shell_path; self.vars["SUM_SHELL_VERSION"]=__version__;
        self.env["SUM_SHELL"]=_sum_shell_path; self.env["SUM_SHELL_VERSION"]=__version__;
        self.exported.update(("SUM_SHELL","SUM_SHELL_VERSION"));
        self.global_names = set();
        self.readonly = set();
        self.aliases = {};
        self.arrays = {};
        self.assoc_arrays = {};
        self.functions = {};
        self._function_depth = 0;
        self._local_scopes = [];
        self._loop_depth = 0;
        self.errexit = False;
        self.traps = {};
        self.options = {"cdspell": False, "histappend": False, "checkwinsize": True, "globstar": False};
        self.shell_options = {"vi": False};
        self.cwd = str(Path(cwd or os.getcwd()).resolve());
        self.fsa = FileSystem(cwd=self.cwd,home=self.env.get("HOME") or str(Path.home()));
        self.logical_cwd = self.fsa.logical_path(self.cwd);
        # Preserve an inherited logical PWD when it names the same directory as
        # the process cwd.  os.getcwd() is physical on POSIX, so without this a
        # shell started from ~/link would immediately forget the symlink path.
        inherited_pwd=str(self.env.get("PWD","") or "").strip();
        if inherited_pwd:
            try:
                inherited_logical=self.fsa.normalize(inherited_pwd,cwd=self.logical_cwd);
                inherited_native=self.fsa.native_path(inherited_logical);
                if os.path.samefile(inherited_native,self.cwd): self.logical_cwd=inherited_logical;
            except (OSError,ValueError): pass;
        self.fsa.native_cwd=self.cwd; self.fsa.cwd=self.logical_cwd;
        self.vars["PWD"] = self.logical_cwd;
        self.env["PWD"] = self.logical_cwd;
        self.argv = list(argv or []);
        self.argv0 = str(argv0);
        self.interactive = bool(interactive);
        self.last_status = 0;
        self.history = [];
        self.pid = os.getpid();
        self.vars.setdefault("PPID",str(os.getppid()));
        if hasattr(os,"getuid"): self.vars.setdefault("UID",str(os.getuid()));
        if hasattr(os,"geteuid"): self.vars.setdefault("EUID",str(os.geteuid()));
        self.vars.setdefault("HOSTNAME",socket.gethostname());
        self._source_depth = 0;
        self._script_depth = 0;
        self._exit_requested = None;
        self._return_requested = None;
        self._break_requested = False;
        self._continue_requested = False;
        self._readline = None;
        self._history_loaded = False;
        self._history_file = None;
        self._readline_auto_history_disabled = False;
        self._pending_readline_restore = None;
        self._readline_library = None;
        self._help_corpus_cache = None;
        self._startup_loaded = False;
        self.completion_specs = {};
        self._completion_engine = CompletionEngine(self);
        self._completion_cache = [];
        try: self._stdout_is_tty = bool(sys.stdout.isatty());
        except Exception: self._stdout_is_tty = False;
        self._command_stdout_is_tty = self._stdout_is_tty;
        self._command_stdin_is_tty = bool(self.interactive and getattr(sys.stdin,"isatty",lambda:False)());

    def get(self, name, default=""):
        if name == "?": return str(self.last_status);
        if name == "$": return str(self.pid);
        if name == "#": return str(len(self.argv));
        if name == "0": return self.argv0;
        if name == "@": return " ".join(self.argv);
        if name == "*": return " ".join(self.argv);
        if name == "-": return "i" if self.interactive else "";
        if name.isdigit():
            index=int(name)-1; return self.argv[index] if 0 <= index < len(self.argv) else "";
        if name in self.arrays:
            values=self.arrays.get(name,[]); return str(values[0]) if values else "";
        if name in self.assoc_arrays:
            values=self.assoc_arrays.get(name,{}); return str(next(iter(values.values()),""));
        return str(self.vars.get(name, ""));

    def set_var(self, name, value, export=None, global_scope=False):
        if name in self.readonly: raise ValueError("{}: readonly variable".format(name));
        self.vars[name] = str(value);
        self.arrays.pop(name,None); self.assoc_arrays.pop(name,None);
        if global_scope: self.global_names.add(name);
        if export is True: self.exported.add(name);
        elif export is False: self.exported.discard(name);
        if name in self.exported: self.env[name]=str(value);

    def unset(self, name):
        if name in self.readonly: raise ValueError("{}: readonly variable".format(name));
        self.vars.pop(name,None); self.arrays.pop(name,None); self.assoc_arrays.pop(name,None); self.env.pop(name,None); self.exported.discard(name); self.global_names.discard(name);

    def environment(self, overrides=None):
        env={name:str(self.vars.get(name,"")) for name in self.exported};
        env.update({"PWD":self.logical_cwd});
        if overrides: env.update({k:str(v) for k,v in overrides.items()});
        return env;

    def logical_path(self, value):
        return self.fsa.normalize(value,cwd=self.logical_cwd);

    def native_path(self, value):
        return self.fsa.native_path(self.logical_path(value));

    def _set_cwd_native(self, native, logical=None):
        self.cwd=str(Path(native).resolve());
        self.fsa.native_cwd=self.cwd;
        if logical is None: self.logical_cwd=self.fsa.logical_path(self.cwd);
        else: self.logical_cwd=self.fsa.normalize(logical,cwd=self.logical_cwd);
        self.fsa.cwd=self.logical_cwd;
        return self.logical_cwd;

    def resolve_command(self, name, all_matches=False):
        matches=[];
        if name in self.aliases: matches.append(("alias", self.aliases[name]));
        if name in self.functions: matches.append(("function", name));
        if name in self._builtin_names(): matches.append(("builtin", name));
        if name in APPLETS: matches.append(("applet", name));
        path = self._which_external(name);
        if path: matches.append(("external", path));
        return matches if all_matches else (matches[0] if matches else None);

    def _which_external(self, name):
        if os.sep in name or (os.altsep and os.altsep in name):
            try: p=Path(self.native_path(name));
            except Exception: p=Path(name); p=p if p.is_absolute() else Path(self.cwd)/p;
            return str(p) if p.exists() and os.access(p,os.X_OK) else None;
        return shutil.which(name, path=self.vars.get("PATH", os.environ.get("PATH","")));

    def _builtin_names(self):
        return {"cd","export","global","unset","readonly","set","shopt","alias","unalias","command","type","source",".","eval","read","inkey","history","complete","compgen","compopt","help","exit","logout","true","false","let","local","declare","return","break","continue","shift","trap","umask",":"};

    # ---------- expansion ----------
    def _subscript_key(self, expr):
        text=str(expr).strip();
        if len(text)>=2 and text[0] in "\"'" and text[-1]==text[0]: text=text[1:-1];
        return self.expand_text(text) if "$" in text or "`" in text else text;

    @staticmethod
    def _remove_parameter_pattern(value,pattern,prefix=False,longest=False):
        value=str(value); pattern=str(pattern); matches=[];
        if prefix:
            for cut in range(0,len(value)+1):
                if fnmatch.fnmatchcase(value[:cut],pattern): matches.append(cut);
            if not matches: return value;
            cut=max(matches) if longest else min(matches); return value[cut:];
        for cut in range(0,len(value)+1):
            if fnmatch.fnmatchcase(value[cut:],pattern): matches.append(cut);
        if not matches: return value;
        # suffix length is len(value)-cut: longest suffix => smallest cut.
        cut=min(matches) if longest else max(matches); return value[:cut];

    def _expand_parameter(self, body):
        m=re.match(r"^#([A-Za-z_][A-Za-z0-9_]*)\[(?:@|\*)\]$",body);
        if m: return str(len(self.arrays.get(m.group(1),[])));
        m=re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\[(-?\d+)\]$",body);
        if m:
            values=self.arrays.get(m.group(1),[]); index=int(m.group(2));
            try: return str(values[index]);
            except IndexError: return "";
        m=re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\[(.+)\]$",body,re.S);
        if m and m.group(1) in self.assoc_arrays:
            key=self._subscript_key(m.group(2)); return str(self.assoc_arrays.get(m.group(1),{}).get(key,""));
        m=re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\[(?:@|\*)\]$",body);
        if m:
            sep=(self.get("IFS") or " ")[0]; return sep.join(str(v) for v in self.arrays.get(m.group(1),[]));
        if body.startswith("#") and _NAME_RE.match(body[1:]): return str(len(self.get(body[1:])));
        # ${name:-word}, ${name:+word}, ${name:=word}
        m=re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(:-|:\+|:=)(.*)$",body,re.S);
        if m:
            name,op,word=m.groups(); value=self.get(name); empty=(value=="");
            if op==":-": return self.expand_text(word) if empty else value;
            if op==":+": return self.expand_text(word) if not empty else "";
            if op==":=":
                if empty:
                    value=self.expand_text(word); self.set_var(name,value);
                return value;
        # POSIX/Bash prefix/suffix pattern removal: ${v#pat}, ${v##pat}, ${v%pat}, ${v%%pat}.
        m=re.match(r"^([A-Za-z_][A-Za-z0-9_]*|[0-9]+)(##|#|%%|%)(.*)$",body,re.S);
        if m:
            name,op,pattern=m.groups(); value=self.get(name); pattern=self.expand_text(pattern);
            return self._remove_parameter_pattern(value,pattern,prefix=op.startswith("#"),longest=len(op)==2);
        # replacement ${v//old/new} or ${v/old/new}
        m=re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(//|/)(.*?)/(.*)$",body,re.S);
        if m:
            name,mode,old,new=m.groups(); value=self.get(name);
            return value.replace(old,new,-1 if mode=="//" else 1);
        # substring ${v:off[:len]}; avoid :- family above
        m=re.match(r"^([A-Za-z_][A-Za-z0-9_]*):(-?\d+)(?::(-?\d+))?$",body);
        if m:
            name,off,length=m.groups(); value=self.get(name); start=int(off);
            return value[start:] if length is None else value[start:start+int(length)];
        return self.get(body);

    @staticmethod
    def _arithmetic_expr(expr):
        text=str(expr);
        text=re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",r"\1",text);
        text=re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)",r"\1",text);
        return text;

    def expand_text(self, text):
        """Expand variables, arithmetic and command substitutions in one string.""";
        out=[]; i=0; text=str(text);
        while i < len(text):
            ch=text[i];
            if ch=="`":
                j=i+1; escaped=False;
                while j<len(text):
                    if text[j]=="`" and not escaped: break;
                    escaped=(text[j]=="\\" and not escaped); j+=1;
                if j>=len(text): out.append(ch); i+=1; continue;
                old_tty=self._stdout_is_tty; self._stdout_is_tty=False;
                try: result=self.run_line(text[i+1:j],capture=True);
                finally: self._stdout_is_tty=old_tty;
                out.append(_stream_text(result.out).rstrip("\n")); i=j+1; continue;
            if ch!="$": out.append(ch); i+=1; continue;
            if text.startswith("$((",i):
                end=self._balanced_arithmetic(text,i+3);
                if end is None: out.append("$"); i+=1; continue;
                expr=self._arithmetic_expr(text[i+3:end]);
                try: value=evaluate(expr,self.vars);
                except SumArithmeticError as exc: raise ValueError("arithmetic: {}".format(exc));
                out.append(format_number(value)); i=end+2; continue;
            if text.startswith("$(",i):
                end=self._balanced_paren(text,i+2);
                if end is None: out.append("$"); i+=1; continue;
                old_tty=self._stdout_is_tty; self._stdout_is_tty=False;
                try: result=self.run_line(text[i+2:end],capture=True);
                finally: self._stdout_is_tty=old_tty;
                out.append(_stream_text(result.out).rstrip("\n")); i=end+1; continue;
            if i+1<len(text) and text[i+1]=="{":
                end=text.find("}",i+2);
                if end<0: out.append("$"); i+=1; continue;
                out.append(self._expand_parameter(text[i+2:end])); i=end+1; continue;
            if i+1<len(text) and text[i+1] in "?$#@*-": out.append(self.get(text[i+1])); i+=2; continue;
            if i+1<len(text) and text[i+1].isdigit():
                j=i+1;
                while j<len(text) and text[j].isdigit(): j+=1;
                out.append(self.get(text[i+1:j])); i=j; continue;
            m=re.match(r"[A-Za-z_][A-Za-z0-9_]*",text[i+1:]);
            if m:
                out.append(self.get(m.group(0))); i+=1+len(m.group(0)); continue;
            out.append("$"); i+=1;
        return "".join(out);

    @staticmethod
    def _balanced_paren(text,start):
        depth=1; quote=None; i=start;
        while i<len(text):
            c=text[i];
            if quote:
                if c==quote and (i==0 or text[i-1]!="\\"): quote=None;
            else:
                if c in "'\"": quote=c;
                elif c=="(": depth+=1;
                elif c==")":
                    depth-=1;
                    if depth==0: return i;
            i+=1;
        return None;

    @staticmethod
    def _balanced_arithmetic(text,start):
        depth=1; i=start;
        while i<len(text)-1:
            if text[i]=="(": depth+=1; i+=1; continue;
            if text[i]==")":
                if depth==1 and text[i+1]==")": return i;
                depth=max(1,depth-1);
            i+=1;
        return None;

    def tokenize(self,line):
        tokens=[]; buf=[]; pattern=[]; quote=None; i=0; was_quoted=False; pathname_magic=False;
        def append_literal(value,quoted=False):
            nonlocal pathname_magic;
            text=str(value); buf.append(text);
            if quoted:
                pattern.append(glob.escape(text));
            else:
                pattern.append(text);
                if glob.has_magic(text): pathname_magic=True;
        def flush():
            nonlocal buf,pattern,was_quoted,pathname_magic;
            if buf or was_quoted:
                value="".join(buf); glob_pattern="".join(pattern) if pathname_magic else None;
                tokens.append(ShellWord(value,glob_pattern));
                buf=[]; pattern=[]; was_quoted=False; pathname_magic=False;
        def append_expansion(value,quoted=False):
            """Apply default/IFS field splitting only to unquoted expansion results.""";
            if quoted:
                append_literal(value,quoted=True); return;
            # Assignment values are an expansion context of their own: Bash does not
            # field-split or pathname-expand A=$value.
            prefix="".join(buf);
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=$",prefix):
                append_literal(value,quoted=True); return;
            text=str(value); ifs=self.get("IFS") or " \t\n";
            # Practical POSIX/Bash field splitting for maintenance scripts.
            rx="[{}]+".format(re.escape(ifs)); fields=[x for x in re.split(rx,text) if x!=""];
            if not fields: return;
            append_literal(fields[0],quoted=False);
            for field in fields[1:]:
                flush(); append_literal(field,quoted=False);
        while i<len(line):
            c=line[i];
            if quote=="'":
                if c=="'": quote=None; was_quoted=True;
                else: append_literal(c,quoted=True);
                i+=1; continue;
            if quote=='"':
                if c=='"': quote=None; was_quoted=True; i+=1; continue;
                if c=="\\" and i+1<len(line) and line[i+1] in '$`"\\': append_literal(line[i+1],quoted=True); i+=2; continue;
                if c in ("$","`"):
                    # expand the longest substitution beginning here by delegating.
                    end=i+1;
                    if line.startswith("$((",i):
                        pos=self._balanced_arithmetic(line,i+3); end=(pos+2 if pos is not None else i+1);
                    elif line.startswith("$(",i):
                        pos=self._balanced_paren(line,i+2); end=(pos+1 if pos is not None else i+1);
                    elif line.startswith("${",i):
                        pos=line.find("}",i+2); end=(pos+1 if pos>=0 else i+1);
                    elif c=="`":
                        pos=line.find("`",i+1); end=(pos+1 if pos>=0 else i+1);
                    else:
                        m=re.match(r"\$([A-Za-z_][A-Za-z0-9_]*|[0-9]+|[?$#@*\-])",line[i:]); end=i+len(m.group(0)) if m else i+1;
                    append_literal(self.expand_text(line[i:end]),quoted=True); i=end; continue;
                append_literal(c,quoted=True); i+=1; continue;
            # unquoted
            if c=="#" and not buf and (i==0 or line[i-1].isspace()): flush(); break;
            if c in "'\"": quote=c; was_quoted=True; i+=1; continue;
            if c=="\\" and i+1<len(line): append_literal(line[i+1],quoted=True); i+=2; continue;
            if c.isspace(): flush(); i+=1; continue;
            op=None;
            for candidate in _OPERATORS:
                if line.startswith(candidate,i): op=candidate; break;
            if op:
                flush(); tokens.append(op); i+=len(op); continue;
            if c in ("$","`"):
                end=i+1;
                if line.startswith("$((",i):
                    pos=self._balanced_arithmetic(line,i+3); end=(pos+2 if pos is not None else i+1);
                elif line.startswith("$(",i):
                    pos=self._balanced_paren(line,i+2); end=(pos+1 if pos is not None else i+1);
                elif line.startswith("${",i):
                    pos=line.find("}",i+2); end=(pos+1 if pos>=0 else i+1);
                elif c=="`":
                    pos=line.find("`",i+1); end=(pos+1 if pos>=0 else i+1);
                else:
                    m=re.match(r"\$([A-Za-z_][A-Za-z0-9_]*|[0-9]+|[?$#@*\-])",line[i:]); end=i+len(m.group(0)) if m else i+1;
                append_expansion(self.expand_text(line[i:end]),quoted=False); i=end; continue;
            append_literal(c,quoted=False); i+=1;
        if quote: raise ValueError("unterminated quote");
        flush(); return tokens;

    def _pathname_expand_word(self, word):
        """Return shell pathname expansion for one quote-aware word.""";
        pattern=getattr(word,"glob_pattern",None);
        if not pattern: return [str(word)];
        try:
            matches=glob.glob(pattern,root_dir=self.cwd,recursive=bool(self.options.get("globstar")));
        except (OSError,re.error):
            matches=[];
        if not matches: return [str(word)];
        return sorted(matches);

    def _pathname_expand_args(self,args):
        """Expand glob patterns after quote removal, except assignment prefixes.""";
        expanded=[]; command_seen=False;
        for word in args:
            value=str(word);
            if not command_seen and _ASSIGN_RE.match(value):
                expanded.append(value); continue;
            command_seen=True;
            expanded.extend(self._pathname_expand_word(word));
        return expanded;

    def _try_array_assignment(self,line):
        text=str(line).strip();
        if text.endswith(";"): text=text[:-1].rstrip();
        m=re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=\((.*)\)$",text,re.S);
        if not m: return None;
        name,inner=m.groups();
        tokens=self.tokenize(inner); values=self._pathname_expand_args(tokens);
        self.arrays[name]=[str(v) for v in values]; self.vars.pop(name,None);
        return Execution();

    def _try_assoc_assignment(self,line):
        text=str(line).strip();
        if text.endswith(";"): text=text[:-1].rstrip();
        m=re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\[([^]]+)\]=(.*)$",text,re.S);
        if not m: return None;
        name,key_expr,value_expr=m.groups();
        if name not in self.assoc_arrays: return None;
        key=self._subscript_key(key_expr);
        value_expr=value_expr.strip();
        if len(value_expr)>=2 and value_expr[0] in "\"'" and value_expr[-1]==value_expr[0]:
            quote=value_expr[0]; inner=value_expr[1:-1]; value=inner if quote=="'" else self.expand_text(inner);
        else:
            tokens=self.tokenize(value_expr); value=" ".join(str(x) for x in tokens);
        self.assoc_arrays.setdefault(name,{})[str(key)]=str(value);
        return Execution();

    def _for_words(self,expr):
        text=str(expr).strip();
        m=re.fullmatch(r'["\']?\$\{([A-Za-z_][A-Za-z0-9_]*)\[@\]\}["\']?',text);
        if m: return list(self.arrays.get(m.group(1),[]));
        tokens=self.tokenize(text); return self._pathname_expand_args(tokens);

    def _run_for(self,name,expr,body_lines):
        out=[]; err=[]; result=Execution(); self._loop_depth+=1;
        try:
            for value in self._for_words(expr):
                self.set_var(name,value); result=self._run_block(body_lines); out.append(result.out); err.append(result.err);
                if self._break_requested: self._break_requested=False; break;
                if self._continue_requested: self._continue_requested=False; continue;
                if self._return_requested is not None or self._exit_requested is not None: break;
        finally: self._loop_depth-=1;
        return Execution(result.code,_stream_join(out),"".join(err));

    def _try_inline_for(self,line):
        text=str(line).strip();
        m=re.match(r"^for\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s+(.*?);\s*do\s+(.*?)\s*;\s*done\s*;?$",text,re.S);
        if not m: return None;
        name,expr,body=m.groups(); return self._run_for(name,expr,[body]);

    # ---------- execution ----------
    def run_line(self,line,capture=False,stdin="",record_history=False):
        line=str(line).rstrip("\n");
        if not line.strip(): return Execution();
        # History belongs to the interactive command line, not to the execution
        # engine.  Prompt substitutions, sourced/script lines, eval internals and
        # command substitutions call run_line() too, but must never become history.
        if record_history: self._record_history(line);
        try:
            result=self._try_assoc_assignment(line);
            if result is None: result=self._try_array_assignment(line);
            if result is None: result=self._try_inline_for(line);
            if result is None: result=self._run_raw_sequence(line,stdin=stdin);
        except ValueError as exc: return Execution(2,err="sumbash: {}\n".format(exc));
        self.last_status=result.code;
        if not capture:
            if result.out:
                if isinstance(result.out,(bytes,bytearray)):
                    sys.stdout.buffer.write(bytes(result.out)); sys.stdout.buffer.flush();
                else: sys.stdout.write(str(result.out)); sys.stdout.flush();
            if result.err: sys.stderr.write(result.err); sys.stderr.flush();
        return result;

    def _split_raw(self, text, operators):
        pieces=[]; seps=[]; start=0; quote=None; i=0; operators=tuple(sorted(operators,key=len,reverse=True));
        while i<len(text):
            c=text[i];
            if quote:
                if c==quote and (i==0 or text[i-1]!="\\"): quote=None;
                i+=1; continue;
            if c in "'\"": quote=c; i+=1; continue;
            if c=="\\": i+=2; continue;
            if c=="`":
                j=i+1;
                while j<len(text):
                    if text[j]=="`" and text[j-1]!="\\": break;
                    j+=1;
                i=min(len(text),j+1); continue;
            if text.startswith("$((",i):
                end=self._balanced_arithmetic(text,i+3); i=(end+2 if end is not None else i+1); continue;
            if text.startswith("$(",i):
                end=self._balanced_paren(text,i+2); i=(end+1 if end is not None else i+1); continue;
            matched=None;
            for op in operators:
                if text.startswith(op,i): matched=op; break;
            if matched:
                pieces.append(text[start:i]); seps.append(matched); i+=len(matched); start=i; continue;
            i+=1;
        pieces.append(text[start:]);
        return pieces,seps;

    def _run_raw_sequence(self,line,stdin=""):
        pieces,seps=self._split_raw(line,(";",)); out=[]; err=[]; code=0;
        for index,piece in enumerate(pieces):
            if not piece.strip(): continue;
            result=self._run_raw_conditional(piece,stdin=stdin if index==0 else ""); code=result.code; out.append(result.out); err.append(result.err);
        return Execution(code,_stream_join(out),"".join(err));

    def _run_raw_conditional(self,line,stdin=""):
        pieces,ops=self._split_raw(line,("&&","||")); out=[]; err=[];
        result=self._run_raw_pipeline(pieces[0],stdin=stdin); out.append(result.out); err.append(result.err);
        for op,piece in zip(ops,pieces[1:]):
            if op=="&&" and result.code!=0: continue;
            if op=="||" and result.code==0: continue;
            result=self._run_raw_pipeline(piece,stdin=""); out.append(result.out); err.append(result.err);
        return Execution(result.code,_stream_join(out),"".join(err));

    def _run_raw_pipeline(self,line,stdin=""):
        stripped=str(line).lstrip();
        if stripped.startswith("! "):
            inner=stripped[2:].lstrip(); result=self._run_raw_pipeline(inner,stdin=stdin); result.code=0 if result.code else 1; return result;
        pieces,unused=self._split_raw(line,("|",)); data=stdin; err=[]; code=0;
        for index,piece in enumerate(pieces):
            tokens=self.tokenize(piece);
            stdin_is_tty=bool(index==0 and data=="" and self.interactive and getattr(sys.stdin,"isatty",lambda:False)());
            result=self._run_command(tokens,stdin=data,stdout_is_tty=(self._stdout_is_tty and index==len(pieces)-1),stdin_is_tty=stdin_is_tty); data=result.out; code=result.code;
            if result.err: err.append(result.err);
        return Execution(code,data,"".join(err));

    def _run_tokens(self,tokens,stdin=""):
        if not tokens: return Execution();
        # sequential ';'
        pieces=[]; cur=[];
        for tok in tokens:
            if tok==";": pieces.append(cur); cur=[];
            else: cur.append(tok);
        pieces.append(cur);
        final=Execution();
        for piece in pieces:
            if not piece: continue;
            final=self._run_conditional(piece,stdin=stdin); stdin="";
        return final;

    def _run_conditional(self,tokens,stdin=""):
        chunks=[]; ops=[]; cur=[];
        for tok in tokens:
            if tok in ("&&","||"): chunks.append(cur); ops.append(tok); cur=[];
            else: cur.append(tok);
        chunks.append(cur);
        result=self._run_pipeline(chunks[0],stdin=stdin);
        for op,chunk in zip(ops,chunks[1:]):
            if op=="&&" and result.code!=0: continue;
            if op=="||" and result.code==0: continue;
            result=self._run_pipeline(chunk,stdin="");
        return result;

    def _run_pipeline(self,tokens,stdin=""):
        commands=[]; cur=[];
        for tok in tokens:
            if tok=="|": commands.append(cur); cur=[];
            else: cur.append(tok);
        commands.append(cur);
        data=stdin; stderr=[]; code=0;
        for index,command in enumerate(commands):
            stdin_is_tty=bool(index==0 and data=="" and self.interactive and getattr(sys.stdin,"isatty",lambda:False)());
            result=self._run_command(command,stdin=data,stdout_is_tty=(self._stdout_is_tty and index==len(commands)-1),stdin_is_tty=stdin_is_tty); data=result.out; code=result.code;
            if result.err: stderr.append(result.err);
        return Execution(code,data,"".join(stderr));

    def _run_command(self,tokens,stdin="",stdout_is_tty=None,stdin_is_tty=False):
        if not tokens: return Execution();
        # redirects
        args=[]; in_data=stdin; out_file=None; append=False; err_file=None; err_append=False; merge_err=False; merge_out=False; i=0;
        command_stdin_is_tty=bool(stdin_is_tty);
        while i<len(tokens):
            tok=tokens[i];
            if tok in (">",">>","1>","1>>","<","0<","2>","2>>"):
                if i+1>=len(tokens): return Execution(2,err="sumbash: redirection requires a file\n");
                targets=self._pathname_expand_word(tokens[i+1]);
                if len(targets)!=1: return Execution(1,err="sumbash: {}: ambiguous redirect\n".format(tokens[i+1]));
                target=targets[0];
                if tok in ("<","0<"):
                    command_stdin_is_tty=False;
                    try:
                        resource=open_resource(self.logical_path(target),"r",filesystem=self.fsa,encoding="utf-8",errors="replace");
                        try: in_data=resource.read();
                        finally: resource.close();
                    except OSError as exc: return Execution(1,err="sumbash: {}: {}\n".format(target,exc));
                elif tok in (">",">>","1>","1>>"): out_file=target; append=(">>" in tok);
                else: err_file=target; err_append=(tok=="2>>");
                i+=2; continue;
            if tok=="2>&1": merge_err=True; i+=1; continue;
            if tok=="1>&2": merge_out=True; i+=1; continue;
            args.append(tok); i+=1;
        if not args: return Execution();
        args=self._pathname_expand_args(args);
        # assignments at command prefix
        assignments={};
        while args:
            m=_ASSIGN_RE.match(args[0]);
            if not m: break;
            assignments[m.group(1)]=m.group(2); args.pop(0);
        if not args:
            for name,value in assignments.items(): self.set_var(name,value);
            return Execution();
        name=args.pop(0);
        # simple alias expansion (command + args, no structural operators in alpha)
        if name in self.aliases:
            try: alias_tokens=self.tokenize(self.aliases[name]);
            except ValueError as exc: return Execution(2,err="alias {}: {}\n".format(name,exc));
            if any(t in ("|",";","&&","||") for t in alias_tokens):
                return self._run_tokens(alias_tokens+args,stdin=in_data);
            if alias_tokens: name=alias_tokens[0]; args=alias_tokens[1:]+args;
        old_command_stdin_is_tty=self._command_stdin_is_tty; self._command_stdin_is_tty=command_stdin_is_tty;
        saved={};
        for key,value in assignments.items(): saved[key]=self.vars.get(key,None); self.vars[key]=value;
        old_command_tty=self._command_stdout_is_tty;
        self._command_stdout_is_tty=bool(self._stdout_is_tty if stdout_is_tty is None else stdout_is_tty) and out_file is None;
        try: result=self._dispatch(name,args,in_data,assignments);
        finally:
            self._command_stdout_is_tty=old_command_tty;
            self._command_stdin_is_tty=old_command_stdin_is_tty;
            for key,old in saved.items():
                if old is None: self.vars.pop(key,None);
                else: self.vars[key]=old;
        if merge_err and result.err:
            if isinstance(result.out,(bytes,bytearray)): result.out=bytes(result.out)+result.err.encode("utf-8");
            else: result.out=str(result.out)+result.err;
            result.err="";
        if merge_out and result.out not in ("",b""):
            result.err += _stream_text(result.out);
            result.out=b"" if isinstance(result.out,(bytes,bytearray)) else "";
        if out_file:
            try:
                binary=isinstance(result.out,(bytes,bytearray)); mode=("ab" if append else "wb") if binary else ("a" if append else "w");
                resource=open_resource(self.logical_path(out_file),mode,filesystem=self.fsa,encoding="utf-8");
                try: resource.write(bytes(result.out) if binary else str(result.out)); resource.flush();
                finally: resource.close();
                result.out=b"" if binary else "";
            except OSError as exc: result.code=1; result.err += "sumbash: {}: {}\n".format(out_file,exc);
        if err_file:
            try:
                resource=open_resource(self.logical_path(err_file),"a" if err_append else "w",filesystem=self.fsa,encoding="utf-8");
                try: resource.write(result.err); resource.flush();
                finally: resource.close();
                result.err="";
            except OSError as exc: result.code=1; result.err += "sumbash: {}: {}\n".format(err_file,exc);
        return result;

    def _dispatch(self,name,args,stdin,temporary_env=None):
        if name in self.functions: return self._call_function(name,args,stdin);
        if name in self._builtin_names(): return self._builtin(name,args,_stream_text(stdin));
        applet=run_applet(name,args,stdin=_stream_text(stdin),runtime=self);
        if applet is not None: return Execution(applet.code,applet.out,applet.err);
        path=self._which_external(name);
        if not path: return Execution(127,err="sumbash: {}: command not found\n".format(name));
        try:
            # Full-screen/interactive programs (mc, vim, ssh, top, less, etc.) must
            # own the terminal while they run. Capturing their stdio breaks curses/TTY
            # negotiation and makes them appear to hang. Only inherit stdio for a
            # foreground command that is not being piped or redirected by sumbash.
            direct_stdio = bool(
                not stdin and self._command_stdout_is_tty
                and getattr(sys.stdin,"isatty",lambda:False)()
                and getattr(sys.stdout,"isatty",lambda:False)()
                and getattr(sys.stderr,"isatty",lambda:False)()
            );
            if direct_stdio:
                cp=subprocess.run([path]+list(args),cwd=self.cwd,env=self.environment(temporary_env),check=False);
                return Execution(cp.returncode);
            cp=subprocess.run([path]+list(args),input=_stream_bytes(stdin) if stdin not in ("",b"") else None,text=False,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=self.cwd,env=self.environment(temporary_env),check=False);
            return Execution(cp.returncode,cp.stdout,cp.stderr.decode("utf-8",errors="replace"));
        except OSError as exc: return Execution(126,err="sumbash: {}: {}\n".format(name,exc));

    def _call_function(self,name,args,stdin=""):
        body=list(self.functions.get(name,[])); old_argv=self.argv; old_return=self._return_requested; self.argv=list(args); self._function_depth+=1; self._local_scopes.append({}); self._return_requested=None;
        try:
            result=self._run_block(body,stdin=stdin);
            if self._return_requested is not None: result.code=int(self._return_requested);
        finally:
            scope=self._local_scopes.pop();
            for var,state in reversed(list(scope.items())):
                kind,value,exported=state;
                self.vars.pop(var,None); self.arrays.pop(var,None); self.assoc_arrays.pop(var,None); self.env.pop(var,None); self.exported.discard(var);
                if kind=="scalar": self.vars[var]=value;
                elif kind=="array": self.arrays[var]=list(value);
                elif kind=="assoc": self.assoc_arrays[var]=dict(value);
                if exported:
                    self.exported.add(var); self.env[var]=self.get(var);
            self._function_depth-=1; self.argv=old_argv; self._return_requested=old_return;
        return result;

    def _remember_local(self,name):
        if not self._local_scopes: return;
        scope=self._local_scopes[-1];
        if name in scope: return;
        if name in self.arrays: scope[name]=("array",list(self.arrays[name]),name in self.exported);
        elif name in self.assoc_arrays: scope[name]=("assoc",dict(self.assoc_arrays[name]),name in self.exported);
        elif name in self.vars: scope[name]=("scalar",self.vars[name],name in self.exported);
        else: scope[name]=("unset",None,False);

    # ---------- builtins ----------
    def _builtin(self,name,args,stdin):
        if name in ("true",":"): return Execution(0);
        if name=="false": return Execution(1);
        if name=="return":
            if not self._function_depth and not self._source_depth: return Execution(1,err="return: can only return from a function or sourced script\n");
            code=int(args[0]) if args else self.last_status; self._return_requested=code; return Execution(code);
        if name=="break":
            if not self._loop_depth: return Execution(1,err="break: only meaningful in a loop\n");
            self._break_requested=True; return Execution();
        if name=="continue":
            if not self._loop_depth: return Execution(1,err="continue: only meaningful in a loop\n");
            self._continue_requested=True; return Execution();
        if name in ("exit","logout"):
            code=int(args[0]) if args else self.last_status;
            if self._script_depth>0 or self._source_depth>0:
                self._exit_requested=code; return Execution(code);
            raise ShellExit(code);
        if name=="cd": return self._bi_cd(args);
        if name=="shift":
            try: count=int(args[0]) if args else 1;
            except ValueError: return Execution(1,err="shift: numeric argument required\n");
            if count<0 or count>len(self.argv): return Execution(1,err="shift: shift count out of range\n");
            self.argv=self.argv[count:]; return Execution();
        if name=="trap": return self._bi_trap(args);
        if name=="umask": return self._bi_umask(args);
        if name in ("export","global","readonly"): return self._bi_assign(name,args);
        if name=="local": return self._bi_local(args);
        if name=="declare": return self._bi_declare(args);
        if name=="unset":
            try:
                for item in args: self.unset(item);
                return Execution();
            except ValueError as exc: return Execution(1,err="unset: {}\n".format(exc));
        if name=="alias": return self._bi_alias(args);
        if name=="unalias":
            for item in args: self.aliases.pop(item,None);
            return Execution();
        if name=="command": return self._bi_command(args,stdin);
        if name=="type": return self._bi_type(args);
        if name in ("source","."): return self._bi_source(args);
        if name=="eval": return self.run_line(" ".join(args),capture=True,stdin=stdin);
        if name=="shopt": return self._bi_shopt(args);
        if name=="set": return self._bi_set(args);
        if name=="history": return self._bi_history(args);
        if name=="help": return self._bi_help(args);
        if name=="complete": return self._bi_complete(args);
        if name=="compgen": return self._bi_compgen(args);
        if name=="compopt": return self._bi_compopt(args);
        if name=="read": return self._bi_read(args,stdin);
        if name=="inkey": return self._bi_inkey(args);
        if name=="let":
            expr=" ".join(args);
            try: value=evaluate(expr,self.vars); self.last_status=0 if value else 1; return Execution(self.last_status);
            except SumArithmeticError as exc: return Execution(2,err="let: {}\n".format(exc));
        return Execution(127,err="sumbash: {}: builtin unavailable\n".format(name));

    def _bi_cd(self,args):
        # Bash-compatible default: logical (-L) navigation preserves symlink
        # components in PWD.  Physical (-P) navigation resolves them.
        physical=False; operands=[]; index=0;
        while index<len(args):
            item=args[index];
            if item=="--": operands.extend(args[index+1:]); break;
            if item=="-L": physical=False; index+=1; continue;
            if item=="-P": physical=True; index+=1; continue;
            if item.startswith("-") and item!="-": return Execution(2,err="cd: {}: invalid option\n".format(item));
            operands.extend(args[index:]); break;
        if len(operands)>1: return Execution(1,err="cd: too many arguments\n");
        requested=operands[0] if operands else self.get("HOME") or self.fsa.home;
        print_result=requested=="-";
        target=(self.get("OLDPWD") or self.logical_cwd) if print_result else requested;
        try:
            if physical and self.fsa.platform_name!="windows":
                raw=str(target or "."); home=self.get("HOME") or self.fsa.home;
                if raw=="~": raw=home;
                elif raw.startswith("~/"): raw=os.path.join(home,raw[2:]);
                p=Path(raw) if os.path.isabs(raw) else Path(self.cwd)/raw;
                logical_target=None;
            else:
                logical_target=self.fsa.normalize(target,cwd=self.logical_cwd);
                p=Path(self.fsa.native_path(logical_target));
        except Exception:
            p=Path(str(target)).expanduser(); p=p if p.is_absolute() else Path(self.cwd)/p; logical_target=None if physical else self.fsa.normalize(str(target),cwd=self.logical_cwd);
        if not p.is_dir() and self.options.get("cdspell"):
            parent=p.parent if p.parent.is_dir() else Path(self.cwd); choices=[x.name for x in parent.iterdir() if x.is_dir()]; match=difflib.get_close_matches(p.name,choices,n=2,cutoff=.72);
            if len(match)==1:
                p=parent/match[0];
                if not physical and logical_target is not None: logical_target=self.fsa.normalize(match[0],cwd=self.fsa.parent(logical_target));
        if not p.is_dir(): return Execution(1,err="cd: {}: No such directory\n".format(target));
        old=self.logical_cwd;
        if physical:
            physical_path=str(p.resolve()); new_cwd=self._set_cwd_native(physical_path,logical=self.fsa.logical_path(physical_path));
        else:
            new_cwd=self._set_cwd_native(str(p),logical=logical_target);
        self.set_var("OLDPWD",old,export=True); self.set_var("PWD",new_cwd,export=True);
        return Execution(out=(new_cwd+"\n") if print_result else "");

    def _bi_assign(self,mode,args):
        if not args:
            names=sorted(self.exported if mode=="export" else (self.global_names if mode=="global" else self.readonly));
            return Execution(out="".join("{}={}\n".format(n,self.get(n)) for n in names));
        try:
            for item in args:
                m=_ASSIGN_RE.match(item);
                if m: name,value=m.groups(); self.set_var(name,value,export=(mode=="export"),global_scope=(mode=="global"));
                else:
                    name=item;
                    if not _NAME_RE.match(name): raise ValueError("invalid variable name: {}".format(name));
                    if mode=="export": self.exported.add(name); self.env[name]=self.get(name);
                    elif mode=="global": self.global_names.add(name);
                    else: self.readonly.add(name);
                if mode=="readonly": self.readonly.add(m.group(1) if m else item);
            return Execution();
        except ValueError as exc: return Execution(1,err="{}: {}\n".format(mode,exc));

    def _bi_local(self,args):
        if not self._function_depth: return Execution(1,err="local: can only be used in a function\n");
        for item in args:
            if item in ("-a","-A"): continue;
            m=_ASSIGN_RE.match(item); name=m.group(1) if m else item;
            if not _NAME_RE.match(name): return Execution(1,err="local: invalid variable name: {}\n".format(name));
            self._remember_local(name);
            if m: self.set_var(name,m.group(2));
            elif name not in self.vars and name not in self.arrays and name not in self.assoc_arrays: self.set_var(name,"");
        return Execution();

    def _bi_declare(self,args):
        assoc=False; indexed=False; export=False; names=[];
        for item in args:
            if item=="-A": assoc=True; continue;
            if item=="-a": indexed=True; continue;
            if item=="-x": export=True; continue;
            names.append(item);
        if not names: return Execution();
        for item in names:
            m=_ASSIGN_RE.match(item); name=m.group(1) if m else item; value=m.group(2) if m else None;
            if not _NAME_RE.match(name): return Execution(1,err="declare: invalid variable name: {}\n".format(name));
            if self._function_depth: self._remember_local(name);
            if assoc:
                self.vars.pop(name,None); self.arrays.pop(name,None); self.assoc_arrays.setdefault(name,{});
            elif indexed:
                self.vars.pop(name,None); self.assoc_arrays.pop(name,None); self.arrays.setdefault(name,[]);
            elif value is not None: self.set_var(name,value,export=export);
            elif name not in self.vars: self.set_var(name,"",export=export);
            if export: self.exported.add(name); self.env[name]=self.get(name);
        return Execution();

    def _bi_trap(self,args):
        if not args:
            return Execution(out="".join("trap -- '{}' {}\n".format(cmd.replace("'","'\\''"),sig) for sig,cmd in sorted(self.traps.items())));
        if args[0]=="-":
            for sig in args[1:]: self.traps.pop(str(sig),None);
            return Execution();
        if len(args)<2: return Execution(2,err="trap: usage: trap command signal...\n");
        command=args[0];
        for sig in args[1:]: self.traps[str(sig)]=command;
        return Execution();

    def _bi_umask(self,args):
        try:
            if not args:
                current=os.umask(0); os.umask(current); return Execution(out="{:04o}\n".format(current));
            value=int(args[0],8); os.umask(value); return Execution();
        except (ValueError,OSError): return Execution(1,err="umask: invalid mask\n");

    def _bi_alias(self,args):
        if not args:
            return Execution(out="".join("alias {}='{}'\n".format(k,v.replace("'","'\\''")) for k,v in sorted(self.aliases.items())));
        out=[];
        for item in args:
            if "=" in item:
                k,v=item.split("=",1); self.aliases[k]=v;
            elif item in self.aliases: out.append("alias {}='{}'\n".format(item,self.aliases[item]));
            else: return Execution(1,"".join(out),"alias: {}: not found\n".format(item));
        return Execution(out="".join(out));

    def _bi_command(self,args,stdin):
        if not args: return Execution();
        if args[0] in ("-v","-V"):
            verbose=args[0]=="-V"; out=[]; code=0;
            for name in args[1:]:
                found=self.resolve_command(name);
                if not found: code=1; continue;
                kind,value=found;
                if verbose:
                    if kind=="alias": out.append("{} is aliased to `{}`\n".format(name,value));
                    elif kind=="function": out.append("{} is a shell function\n".format(name));
                    elif kind=="builtin": out.append("{} is a shell builtin\n".format(name));
                    elif kind=="applet": out.append("{} is a SUM applet\n".format(name));
                    else: out.append("{} is {}\n".format(name,value));
                else: out.append((name if kind in ("alias","builtin","applet") else value)+"\n");
            return Execution(code,"".join(out));
        return self._dispatch(args[0],args[1:],stdin);

    def _bi_type(self,args):
        all_matches=False;
        if args and args[0]=="-a": all_matches=True; args=args[1:];
        out=[]; code=0;
        for name in args:
            matches=self.resolve_command(name,all_matches=all_matches);
            if not matches: code=1; out.append("type: {}: not found\n".format(name)); continue;
            if not all_matches: matches=[matches];
            for kind,value in matches:
                if kind=="alias": out.append("{} is aliased to `{}`\n".format(name,value));
                elif kind=="function": out.append("{} is a shell function\n".format(name));
                elif kind=="builtin": out.append("{} is a shell builtin\n".format(name));
                elif kind=="applet": out.append("{} is a SUM applet\n".format(name));
                else: out.append("{} is {}\n".format(name,value));
        return Execution(code,"".join(out));

    def _resolve_source_path(self,value):
        name=str(value or "");
        if not name: raise FileNotFoundError(name);
        candidates=[];
        try: candidates.append(Path(self.native_path(name)));
        except Exception:
            expanded=Path(name).expanduser();
            candidates.append(expanded if expanded.is_absolute() else Path(self.cwd)/expanded);
        if "/" not in name and "\\" not in name:
            for directory in str(self.get("PATH") or "").split(os.pathsep):
                if not directory: continue;
                candidate=Path(directory).expanduser()/name;
                if candidate not in candidates: candidates.append(candidate);
        for candidate in candidates:
            try:
                if candidate.is_file(): return candidate;
            except OSError: pass;
        return candidates[0];

    def _bi_source(self,args):
        if not args: return Execution(2,err="source: filename required\n");
        try: p=self._resolve_source_path(args[0]); text=p.read_text(encoding="utf-8",errors="replace");
        except OSError as exc: return Execution(1,err="source: {}: {}\n".format(args[0],exc));
        old_argv=self.argv;
        if len(args)>1: self.argv=args[1:];
        self._source_depth+=1;
        try:
            try: return self._run_block(self._logical_lines(text));
            except ValueError as exc: return Execution(2,err="sumbash: {}: {}\n".format(args[0],exc));
        finally:
            self._source_depth-=1;
            if len(args)>1: self.argv=old_argv;

    def _bi_shopt(self,args):
        if not args:
            return Execution(out="".join("{}\t{}\n".format(k,"on" if v else "off") for k,v in sorted(self.options.items())));
        mode=None; names=[];
        for a in args:
            if a=="-s": mode=True;
            elif a=="-u": mode=False;
            elif a=="-q": mode="query";
            elif a=="-o": continue;
            else: names.append(a);
        if mode=="query": return Execution(0 if all(self.options.get(n,False) for n in names) else 1);
        if mode is None: return Execution(2,err="shopt: use -s, -u or -q\n");
        for n in names:
            if n not in self.options: return Execution(1,err="shopt: {}: invalid shell option\n".format(n));
            self.options[n]=bool(mode);
        return Execution();

    def _bi_set(self,args):
        if len(args)==1 and args[0] in ("-e","+e","-u","+u"):
            if args[0] in ("-e","+e"): self.errexit=(args[0]=="-e");
            else: self.shell_options["nounset"]=(args[0]=="-u");
            return Execution();
        if len(args)>=2 and args[0] in ("-o","+o"):
            name=args[1]; value=args[0]=="-o";
            if name=="vi":
                self.shell_options["vi"]=value;
                if value:
                    try:
                        import readline; self._readline=readline; readline.parse_and_bind("set editing-mode vi");
                    except Exception: pass;
                return Execution();
            return Execution(1,err="set: {}: invalid option name\n".format(name));
        if not args:
            return Execution(out="".join("{}={}\n".format(k,v) for k,v in sorted(self.vars.items())));
        if args and args[0]=="--": self.argv=list(args[1:]); return Execution();
        if args and not args[0].startswith(("-","+")): self.argv=list(args); return Execution();
        if len(args)==1 and args[0] in ("-x","+x"):
            self.shell_options["xtrace"]=(args[0]=="-x"); return Execution();
        return Execution(2,err="set: unsupported option\n");

    def _help_corpus(self):
        if self._help_corpus_cache is not None: return self._help_corpus_cache;
        from sumtui.helpdb import HelpCorpus;
        source=resource_files("sumbash").joinpath("sumbash_help.helpdb").read_text(encoding="utf-8");
        self._help_corpus_cache=HelpCorpus.from_helpdb(source);
        return self._help_corpus_cache;

    @staticmethod
    def _help_topic_text(topic):
        lines=[topic.name, "="*len(topic.name), "", topic.summary, ""];
        if topic.syntax:
            lines.extend(["Syntax:"]+['  '+value for value in topic.syntax]+[""]);
        if topic.notes:
            lines.extend(["Notes:"]+['  - '+value for value in topic.notes]+[""]);
        if topic.example:
            lines.extend(["Example:"]+['  '+value for value in topic.example.rstrip().splitlines()]+[""]);
        if topic.see_also: lines.extend(["See also: "+", ".join(topic.see_also),""]);
        if topic.aliases: lines.extend(["Aliases: "+", ".join(topic.aliases),""]);
        return "\n".join(lines).rstrip()+"\n";

    def _help_plain(self,name=None):
        corpus=self._help_corpus();
        if name:
            topic=corpus.find_topic(name);
            if topic is None: return None;
            return self._help_topic_text(topic);
        lines=[corpus.title,"="*len(corpus.title),""];
        if corpus.intro: lines.extend([corpus.intro,""]);
        category=None;
        for topic in sorted(corpus.topics,key=lambda item:(item.category.casefold(),item.name.casefold())):
            if topic.category!=category:
                category=topic.category; lines.extend([category+":"]);
            lines.append("  {:20s} {}".format(topic.name,topic.summary));
        lines.extend(["","Use `help TOPIC` for text help or `help -i [TOPIC]` for the navigable browser.",""]);
        return "\n".join(lines);

    def _open_help_browser(self,name=None,query=""):
        corpus=self._help_corpus();
        topic=corpus.find_topic(name) if name else None;
        initial_query=str(query or "");
        if name and topic is None and not initial_query: initial_query=str(name);
        try:
            from sumtui.helpbrowser import run_help_browser;
            theme=self.get("SUMBASH_HELP_THEME") or "DOS";
            return int(run_help_browser(corpus,title="sumbash Help",topic=topic.name if topic else None,query=initial_query,theme=theme) or 0);
        except (ImportError,ModuleNotFoundError,RuntimeError) as exc:
            text=self._help_plain(topic.name if topic else name);
            if text: sys.stdout.write(text); sys.stdout.flush(); return 0;
            sys.stderr.write("sumbash: interactive help unavailable: {}\n".format(exc)); sys.stderr.flush(); return 1;

    def _bi_help(self,args):
        interactive=False; names=[];
        for arg in args:
            if arg in ("-i","--interactive"): interactive=True;
            elif arg in ("-h","--help"):
                return Execution(out="Usage: help [-i|--interactive] [TOPIC]\n");
            else: names.append(arg);
        name=" ".join(names).strip() or None;
        if interactive:
            if not (self._command_stdin_is_tty and self._command_stdout_is_tty and getattr(sys.stdin,"isatty",lambda:False)() and getattr(sys.stdout,"isatty",lambda:False)()):
                text=self._help_plain(name);
                if text is None: return Execution(1,err="help: no help topic matches {}\n".format(name));
                return Execution(out=text);
            return Execution(self._open_help_browser(name));
        text=self._help_plain(name);
        if text is None: return Execution(1,err="help: no help topic matches {}\n".format(name));
        return Execution(out=text);

    @staticmethod
    def _help_word_at(line,point):
        text=str(line or ""); point=max(0,min(len(text),int(point)));
        spans=list(re.finditer(r"[^\s;|&<>()]+",text));
        for match in spans:
            if match.start()<=point<=match.end(): return match.group(0);
        before=[match for match in spans if match.end()<=point];
        return before[-1].group(0) if before else "";

    def _context_help_topic(self,line,point):
        corpus=self._help_corpus(); text=str(line or ""); point=max(0,min(len(text),int(point)));
        token=self._help_word_at(text,point).strip("\"'");
        token=token.replace("\\ "," ");
        candidates=[];
        if token and not token.startswith("-"): candidates.append(token);
        left=text[:point]; segment=re.split(r"(?:&&|\|\||[;|])",left)[-1].strip();
        try:
            words=self.tokenize(segment);
        except ValueError:
            words=[];
        command="";
        for word in words:
            value=str(word);
            if _ASSIGN_RE.match(value): continue;
            command=value; break;
        if not command:
            match=re.match(r"\s*([^\s]+)",segment);
            command=match.group(1) if match else "";
        if command: candidates.append(command.strip("\"'"));
        for candidate in candidates:
            topic=corpus.find_topic(candidate);
            if topic is not None: return topic.name;
        return command or token or None;

    def _decode_help_trigger(self,line):
        text=str(line or "");
        marker_at=text.find(_HELP_CURSOR_MARKER);
        if marker_at<0: return None;
        # a59 prefixed the line by asking readline macros to execute Ctrl-A.
        # Some readline/libedit builds insert that control byte literally instead,
        # so a60 makes the marker itself the complete out-of-band request.
        if text.startswith(_HELP_TRIGGER_PREFIX):
            payload=text[len(_HELP_TRIGGER_PREFIX):]; point=payload.find(_HELP_CURSOR_MARKER);
            if point<0: return None;
            original=payload[:point]+payload[point+len(_HELP_CURSOR_MARKER):];
        else:
            point=marker_at;
            original=text[:point]+text[point+len(_HELP_CURSOR_MARKER):];
        topic=self._context_help_topic(original,point);
        return original,point,topic;

    def _set_readline_point(self,point):
        if self._readline is None: return False;
        try:
            if self._readline_library is None:
                import ctypes;
                import ctypes.util;
                name=ctypes.util.find_library("readline");
                if not name: return False;
                self._readline_library=ctypes.CDLL(name);
            import ctypes;
            value=ctypes.c_int.in_dll(self._readline_library,"rl_point");
            value.value=max(0,int(point));
            return True;
        except Exception:
            return False;

    def _readline_pre_input_hook(self):
        pending=self._pending_readline_restore;
        if pending is None or self._readline is None: return;
        self._pending_readline_restore=None; text,point=pending;
        try:
            self._readline.insert_text(text);
            self._set_readline_point(point);
            self._readline.redisplay();
        except Exception: pass;

    def _bind_help_keys(self,readline):
        # Insert one unique marker exactly at rl_point, then accept the line.
        # Do not use a macro containing Ctrl-A: GNU readline and libedit differ
        # on whether a control byte embedded in a macro is executed or inserted.
        macro='"{}\\C-m"'.format(_HELP_CURSOR_MARKER);
        for key in (r'\eOP',r'\e[11~',r'\e[[A',r'\eh',r'\eH'):
            try: readline.parse_and_bind('"{}": {}'.format(key,macro));
            except Exception: pass;
        try: readline.set_pre_input_hook(self._readline_pre_input_hook);
        except Exception: pass;

    def _history_limit(self):
        try: return max(0,int(self.get("HISTSIZE") or 1000));
        except ValueError: return 1000;

    def _history_file_limit(self):
        try: return max(0,int(self.get("HISTFILESIZE") or 2000));
        except ValueError: return 2000;

    def _history_path(self):
        raw=self.get("HISTFILE");
        if raw: return str(Path(raw).expanduser());
        home=self.get("HOME") or str(Path.home());
        return str(Path(home)/".sumbash_history");

    def _setup_readline(self):
        if self._history_loaded: return;
        self._history_loaded=True;
        try:
            import readline;
        except Exception:
            return;
        self._readline=readline;
        try:
            readline.parse_and_bind("set editing-mode {}".format("vi" if self.shell_options.get("vi") else "emacs"));
            readline.parse_and_bind(r'"\C-l": clear-screen');
            readline.parse_and_bind("tab: complete");
            readline.parse_and_bind("set show-all-if-ambiguous off");
            readline.parse_and_bind("set show-all-if-unmodified off");
            # Keep path punctuation inside the current word; separators still break commands.
            readline.set_completer_delims(" \t\n;|&<>()");
            readline.set_completer(self._completion_engine.readline_completer);
            self._bind_help_keys(readline);
            # Python's input() normally lets readline add every accepted line to
            # history automatically.  Disable that so sumbash alone decides which
            # *user command lines* are history-worthy (HISTCONTROL included).
            if hasattr(readline,"set_auto_history"):
                readline.set_auto_history(False);
                self._readline_auto_history_disabled=True;
        except Exception: pass;
        try:
            readline.set_history_length(self._history_file_limit());
        except Exception: pass;
        self._history_file=self._history_path();
        try:
            readline.read_history_file(self._history_file);
        except (FileNotFoundError,OSError): pass;
        # Mirror loaded readline history so the `history` builtin sees it too.
        try:
            count=readline.get_current_history_length();
            self.history=[readline.get_history_item(i) for i in range(1,count+1) if readline.get_history_item(i) is not None];
        except Exception: pass;

    def _record_history(self,line):
        if not self.interactive or not line.strip(): return False;
        control=self.get("HISTCONTROL");

        # On readline implementations without set_auto_history(False), input() may
        # already have inserted the line.  Remove that provisional entry first, so
        # ignorespace/ignoredups can still be honoured deterministically.
        if self._readline is not None and not self._readline_auto_history_disabled:
            try:
                current=self._readline.get_current_history_length();
                last=self._readline.get_history_item(current) if current else None;
                if last==line and hasattr(self._readline,"remove_history_item"):
                    self._readline.remove_history_item(current-1);
            except Exception: pass;

        if ("ignorespace" in control or "ignoreboth" in control) and line.startswith(" "): return False;
        if ("ignoredups" in control or "ignoreboth" in control) and self.history and self.history[-1]==line: return False;
        self.history.append(line);
        limit=self._history_limit();
        if limit and len(self.history)>limit: self.history=self.history[-limit:];
        if self._readline is not None:
            try:
                current=self._readline.get_current_history_length();
                last=self._readline.get_history_item(current) if current else None;
                if last!=line: self._readline.add_history(line);
            except Exception: pass;
        return True;

    def _write_history(self):
        if self._readline is None or not self._history_file: return;
        try:
            Path(self._history_file).expanduser().parent.mkdir(parents=True,exist_ok=True);
            self._readline.set_history_length(self._history_file_limit());
            if self.options.get("histappend") and Path(self._history_file).exists():
                # Python readline can append only the lines added in this process,
                # but write_history_file is deterministic across readline variants.
                self._readline.write_history_file(self._history_file);
            else:
                self._readline.write_history_file(self._history_file);
        except OSError: pass;

    def _bi_history(self,args):
        if args and args[0]=="-c":
            self.history.clear();
            if self._readline is not None:
                try: self._readline.clear_history();
                except Exception: pass;
            return Execution();
        if args and args[0] in ("-w","-a"):
            self._write_history(); return Execution();
        count=None;
        if args:
            try: count=max(0,int(args[-1]));
            except ValueError: return Execution(2,err="history: numeric argument required\n");
        rows=self.history[-count:] if count is not None else self.history;
        offset=len(self.history)-len(rows);
        return Execution(out="".join("{:5d}  {}\n".format(offset+i+1,v) for i,v in enumerate(rows)));

    def _bi_complete(self,args):
        try: spec,names,print_mode,remove=parse_completion_spec(args);
        except ValueError as exc: return Execution(2,err="complete: {}\n".format(exc));
        if print_mode:
            selected=names or sorted(self.completion_specs); out=[]; code=0;
            for name in selected:
                current=self.completion_specs.get(name);
                if current is None: code=1; continue;
                out.append(format_completion_spec(name,current)+"\n");
            return Execution(code,out="".join(out));
        if remove:
            if names:
                for name in names: self.completion_specs.pop(name,None);
            else: self.completion_specs.clear();
            return Execution();
        if not names: return Execution(2,err="complete: command name required\n");
        for name in names: self.completion_specs[name]=CompletionSpec(actions=list(spec.actions),words=list(spec.words),options=set(spec.options),prefix=spec.prefix,suffix=spec.suffix,filter_pattern=spec.filter_pattern,function=spec.function);
        return Execution();

    def _bi_compgen(self,args):
        try: spec,names,unused_print,unused_remove=parse_completion_spec(args);
        except ValueError as exc: return Execution(2,err="compgen: {}\n".format(exc));
        prefix=names[-1] if names else "";
        if not spec.actions and not spec.words:
            spec.actions.append("command");
        values=spec_candidates(spec,prefix,self);
        return Execution(out="".join(value+"\n" for value in values));

    def _bi_compopt(self,args):
        # The command is present now so completion scripts can probe for it.
        # Function-scoped programmable completion arrives with shell functions;
        # until then there is no active completion function whose options can be changed.
        return Execution(1,err="compopt: not currently executing a completion function\n");

    def _bi_read(self,args,stdin):
        timeout=None; prompt=""; silent=False; nchars=None; names=[]; i=0;
        while i<len(args):
            a=args[i];
            if a=="-t" and i+1<len(args): timeout=float(args[i+1]); i+=2; continue;
            if a=="-p" and i+1<len(args): prompt=args[i+1]; i+=2; continue;
            if a=="-s": silent=True; i+=1; continue;
            if a=="-N" and i+1<len(args): nchars=int(args[i+1]); i+=2; continue;
            names.append(a); i+=1;
        name=names[0] if names else "REPLY";
        if stdin:
            value=stdin[:nchars] if nchars is not None else stdin.splitlines()[0] if stdin.splitlines() else "";
        else:
            if prompt: sys.stderr.write(prompt); sys.stderr.flush();
            value=self._terminal_read(timeout=timeout,silent=silent,nchars=nchars);
            if value is None: return Execution(1);
        self.set_var(name,value); return Execution();

    def _bi_inkey(self,args):
        timeout=.1; var=None; i=0;
        while i<len(args):
            if args[i] in ("-t","--timeout") and i+1<len(args): timeout=float(args[i+1]); i+=2; continue;
            if not args[i].startswith("-"): var=args[i];
            i+=1;
        value=self._terminal_read(timeout=timeout,silent=True,nchars=1);
        if value is None: return Execution(1);
        if var: self.set_var(var,value,global_scope=True); return Execution();
        return Execution(out=value);

    @staticmethod
    def _terminal_read(timeout=None,silent=False,nchars=None):
        if not sys.stdin.isatty():
            data=sys.stdin.read(nchars or -1); return data.rstrip("\n") if data else None;
        try:
            if os.name=="nt":
                import msvcrt, time as _time;
                end=None if timeout is None else _time.monotonic()+timeout; chars=[];
                while nchars is None or len(chars)<nchars:
                    if msvcrt.kbhit():
                        ch=msvcrt.getwch(); chars.append(ch);
                        if nchars is None and ch in ("\r","\n"): break;
                    elif end is not None and _time.monotonic()>=end: break;
                    else: _time.sleep(.01);
                return "".join(chars).rstrip("\r\n") if chars else None;
            import select, termios, tty, time as _time;
            fd=sys.stdin.fileno(); old=termios.tcgetattr(fd);
            try:
                if silent or nchars: tty.setraw(fd);
                ready,unused1,unused2=select.select([sys.stdin],[],[],timeout);
                if not ready: return None;
                if nchars: return os.read(fd,nchars).decode(errors="replace");
                return sys.stdin.readline().rstrip("\n");
            finally: termios.tcsetattr(fd,termios.TCSADRAIN,old);
        except Exception:
            try: return input();
            except EOFError: return None;

    # ---------- compound script grammar ----------
    @staticmethod
    def _logical_lines(text):
        logical=[]; pending="";
        for raw in str(text).splitlines():
            if pending: raw=pending+raw.lstrip(); pending="";
            stripped=raw.rstrip();
            if stripped.endswith("\\"):
                pending=stripped[:-1]+" "; continue;
            if stripped.endswith("&&") or stripped.endswith("||"):
                pending=stripped+" "; continue;
            logical.append(raw);
        if pending: logical.append(pending);
        combined=[]; i=0;
        while i<len(logical):
            cur=logical[i]; st=cur.strip();
            if i+1<len(logical) and logical[i+1].strip() in ("then","then;","do","do;"):
                nxt=logical[i+1].strip().rstrip(";");
                if (nxt=="then" and re.match(r"^(?:if|elif)\b",st)) or (nxt=="do" and re.match(r"^(?:while|until)\b",st)):
                    cur=cur.rstrip()+"; "+nxt; i+=1;
            combined.append(cur); i+=1;
        return combined;

    @staticmethod
    def _control_text(text):
        raw=str(text); quote=None; i=0;
        while i<len(raw):
            c=raw[i];
            if quote:
                if c==quote and (i==0 or raw[i-1]!="\\"): quote=None;
            else:
                if c in "\"'": quote=c;
                elif c=="#" and (i==0 or raw[i-1].isspace()): return raw[:i].rstrip();
            i+=1;
        return raw;

    @staticmethod
    def _strip_semi(text):
        value=str(text).strip();
        while value.endswith(";"): value=value[:-1].rstrip();
        return value;

    def _collect_braced(self,lines,start):
        body=[]; depth=1; i=start;
        while i<len(lines):
            raw=lines[i]; st=self._control_text(raw).strip();
            if st=="{": depth+=1; body.append(raw); i+=1; continue;
            if st.startswith("}"):
                depth-=1;
                if depth==0: return body,i,st[1:].strip();
                body.append(raw); i+=1; continue;
            body.append(raw); i+=1;
        raise ValueError("unterminated braced block");

    @staticmethod
    def _compound_close_suffix(text,keyword):
        """Return redirects/suffix after a compound-command closing keyword.""";
        value=str(text).strip();
        if not value.startswith(keyword): return None;
        rest=value[len(keyword):];
        if rest and not (rest[0].isspace() or rest[0] in ";<>"): return None;
        rest=rest.strip();
        if rest.startswith(";"): rest=rest[1:].strip();
        return rest;

    def _collect_loop_body(self,lines,start):
        body=[]; depth=1; i=start;
        while i<len(lines):
            raw=lines[i]; st=self._control_text(raw).strip();
            if re.match(r"^(?:for\b.*;\s*do|while\b.*;\s*do|until\b.*;\s*do)",st): depth+=1;
            suffix=self._compound_close_suffix(st,"done");
            if suffix is not None:
                depth-=1;
                if depth==0: return body,i,suffix;
            body.append(raw); i+=1;
        raise ValueError("unterminated loop");

    def _collect_if(self,lines,start,first_cond):
        branches=[]; current=[]; cond=first_cond; else_body=None; depth=1; i=start;
        while i<len(lines):
            raw=lines[i]; st=self._control_text(raw).strip(); normalized=self._strip_semi(st);
            if re.match(r"^if\b.*;\s*then\s*;?$",st): depth+=1; current.append(raw); i+=1; continue;
            suffix=self._compound_close_suffix(st,"fi");
            if suffix is not None:
                depth-=1;
                if depth==0:
                    if else_body is not None: else_body.extend(current);
                    else: branches.append((cond,current));
                    return branches,else_body,i,suffix;
                current.append(raw); i+=1; continue;
            if depth==1:
                m=re.match(r"^elif\s+(.*?);\s*then\s*;?$",st,re.S);
                if m:
                    if else_body is not None: raise ValueError("elif after else");
                    branches.append((cond,current)); cond=m.group(1); current=[]; i+=1; continue;
                if normalized=="else":
                    branches.append((cond,current)); cond=None; current=[]; else_body=[]; i+=1; continue;
            current.append(raw); i+=1;
        raise ValueError("unterminated if");

    def _collect_case(self,lines,start):
        clauses=[]; pattern=None; body=[]; depth=1; i=start;
        while i<len(lines):
            raw=lines[i]; st=self._control_text(raw).strip();
            if re.match(r"^case\b.*\bin\s*;?$",st):
                depth+=1;
                if pattern is not None: body.append(raw);
                i+=1; continue;
            suffix=self._compound_close_suffix(st,"esac");
            if suffix is not None:
                depth-=1;
                if depth==0:
                    if pattern is not None: clauses.append((pattern,body));
                    return clauses,i,suffix;
                if pattern is not None: body.append(raw);
                i+=1; continue;
            if depth==1 and pattern is None:
                m=re.match(r"^(.*?)\)\s*(.*)$",st,re.S);
                if m:
                    pattern=m.group(1).strip(); tail=m.group(2).strip(); body=[];
                    if tail:
                        if tail.endswith(";;"):
                            tail=tail[:-2].rstrip();
                            if tail: body.append(tail);
                            clauses.append((pattern,body)); pattern=None; body=[];
                        else: body.append(tail);
                    i+=1; continue;
            if depth==1 and pattern is not None and ";;" in st:
                prefix=raw.rsplit(";;",1)[0].rstrip();
                if prefix.strip(): body.append(prefix);
                clauses.append((pattern,body)); pattern=None; body=[]; i+=1; continue;
            if pattern is not None: body.append(raw);
            i+=1;
        raise ValueError("unterminated case");

    def _apply_compound_redirects(self,result,suffix):
        """Apply stdout/stderr redirects to one completed compound command.""";
        tail=self._strip_semi(suffix);
        if not tail: return result;
        try: tokens=self.tokenize(tail);
        except ValueError as exc: return Execution(2,result.out,result.err+"sumbash: compound redirect: {}\n".format(exc));
        capture_out=("capture","out"); capture_err=("capture","err"); out_dest=capture_out; err_dest=capture_err; files=[]; i=0;
        while i<len(tokens):
            tok=str(tokens[i]);
            if tok=="2>&1": err_dest=out_dest; i+=1; continue;
            if tok=="1>&2": out_dest=err_dest; i+=1; continue;
            if tok in (">",">>","1>","1>>","2>","2>>"):
                if i+1>=len(tokens): return Execution(2,result.out,result.err+"sumbash: redirection requires a file\n");
                targets=self._pathname_expand_word(tokens[i+1]);
                if len(targets)!=1: return Execution(1,result.out,result.err+"sumbash: {}: ambiguous redirect\n".format(tokens[i+1]));
                target={"kind":"file","path":str(targets[0]),"append":(">>" in tok),"order":len(files)}; files.append(target);
                if tok.startswith("2"): err_dest=target;
                else: out_dest=target;
                i+=2; continue;
            return Execution(2,result.out,result.err+"sumbash: unsupported compound redirect: {}\n".format(tok));

        final_out=[]; final_err=[]; buckets={};
        for dest,value in ((out_dest,result.out),(err_dest,result.err)):
            if value in ("",b""): continue;
            if dest==capture_out: final_out.append(value); continue;
            if dest==capture_err: final_err.append(_stream_text(value)); continue;
            key=id(dest);
            if key not in buckets: buckets[key]=(dest,[]);
            buckets[key][1].append(value);

        code=result.code;
        for target,values in sorted(buckets.values(),key=lambda item:item[0]["order"]):
            binary=any(isinstance(value,(bytes,bytearray)) for value in values);
            mode=("ab" if target["append"] else "wb") if binary else ("a" if target["append"] else "w");
            try:
                resource=open_resource(self.logical_path(target["path"]),mode,filesystem=self.fsa,encoding="utf-8");
                try:
                    payload=b"".join(_stream_bytes(value) for value in values) if binary else "".join(_stream_text(value) for value in values);
                    resource.write(payload); resource.flush();
                finally: resource.close();
            except OSError as exc:
                code=1; final_err.append("sumbash: {}: {}\n".format(target["path"],exc));
        return Execution(code,_stream_join(final_out),"".join(final_err));

    def _apply_block_redirect(self,result,suffix):
        """Backward-compatible name used by the original braced-group path.""";
        return self._apply_compound_redirects(result,suffix);

    def _execute_heredoc(self,lines,index,raw):
        m=re.search(r"<<(-)?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2",raw);
        if not m: return None;
        strip_tabs=bool(m.group(1)); quoted=bool(m.group(2)); tag=m.group(3); data=[]; j=index+1;
        while j<len(lines):
            candidate=lines[j]; check=candidate.lstrip("\t") if strip_tabs else candidate;
            if check==tag: break;
            data.append(candidate.lstrip("\t") if strip_tabs else candidate); j+=1;
        if j>=len(lines): raise ValueError("unterminated here-document ({})".format(tag));
        command=(raw[:m.start()]+raw[m.end():]).strip(); payload="\n".join(data)+"\n";
        if not quoted: payload=self.expand_text(payload);
        return self.run_line(command,capture=True,stdin=payload),j;

    def _run_if_construct(self,branches,else_body):
        out=[]; err=[]; result=Execution(1);
        for cond,body in branches:
            test=self.run_line(cond,capture=True); out.append(test.out); err.append(test.err);
            if test.code==0:
                result=self._run_block(body); out.append(result.out); err.append(result.err); return Execution(result.code,_stream_join(out),"".join(err));
        if else_body is not None:
            result=self._run_block(else_body); out.append(result.out); err.append(result.err);
        return Execution(result.code,_stream_join(out),"".join(err));

    def _run_while(self,cond,body,until=False):
        out=[]; err=[]; result=Execution(); self._loop_depth+=1; iterations=0;
        try:
            while True:
                iterations+=1;
                if iterations>100000: return Execution(2,_stream_join(out),"".join(err)+"sumbash: loop iteration limit exceeded\n");
                test=self.run_line(cond,capture=True); out.append(test.out); err.append(test.err); ok=(test.code==0);
                if until: ok=not ok;
                if not ok: result=test; break;
                result=self._run_block(body); out.append(result.out); err.append(result.err);
                if self._break_requested: self._break_requested=False; result=Execution(); break;
                if self._continue_requested: self._continue_requested=False; continue;
                if self._return_requested is not None or self._exit_requested is not None: break;
        finally: self._loop_depth-=1;
        return Execution(result.code,_stream_join(out),"".join(err));

    def _run_case_construct(self,word_expr,clauses):
        tokens=self.tokenize(word_expr); word=str(tokens[0]) if tokens else "";
        for pattern_expr,body in clauses:
            for pattern in pattern_expr.split("|"):
                pat=pattern.strip();
                if len(pat)>=2 and pat[0] in "\"'" and pat[-1]==pat[0]: pat=pat[1:-1];
                pat=self.expand_text(pat);
                if fnmatch.fnmatchcase(word,pat): return self._run_block(body);
        return Execution();

    def _run_block(self,lines,stdin=""):
        out=[]; err=[]; result=Execution(); i=0; first_stdin=stdin;
        while i<len(lines):
            if self._exit_requested is not None or self._return_requested is not None or self._break_requested or self._continue_requested: break;
            raw=lines[i]; st=self._control_text(raw).strip();
            if not st or st.startswith("#!") or st.startswith("#"): i+=1; continue;

            # heredoc command is consumed together with its body before normal tokenization.
            hd=self._execute_heredoc(lines,i,raw);
            if hd is not None:
                result,i=hd; out.append(result.out); err.append(result.err); i+=1; first_stdin=""; continue;

            # inline function NAME() { command; ...; }
            fim=re.match(r"^(?:function\s+)?([A-Za-z_][A-Za-z0-9_-]*)\s*\(\s*\)\s*\{\s*(.*?)\s*;?\s*\}\s*;?$",st,re.S);
            if fim:
                self.functions[fim.group(1)]=[fim.group(2)] if fim.group(2).strip() else []; i+=1; continue;

            # function NAME() { ... } and NAME() { ... }
            fm=re.match(r"^(?:function\s+)?([A-Za-z_][A-Za-z0-9_-]*)\s*\(\s*\)\s*(\{)?\s*;?$",st);
            if fm:
                name=fm.group(1); open_here=bool(fm.group(2));
                open_index=i if open_here else i+1;
                if open_index>=len(lines) or (not open_here and lines[open_index].strip()!="{"): raise ValueError("function {} missing {{".format(name));
                body,end,unused_suffix=self._collect_braced(lines,open_index+1); self.functions[name]=body; i=end+1; continue;

            # standalone group
            if st=="{":
                body,end,suffix=self._collect_braced(lines,i+1); result=self._run_block(body,stdin=first_stdin); result=self._apply_compound_redirects(result,suffix); out.append(result.out); err.append(result.err); i=end+1; first_stdin=""; continue;

            # for ...; do
            fm=re.match(r"^for\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s+(.*?);\s*do\s*(.*)$",st,re.S);
            if fm:
                name,expr,tail=fm.groups();
                if tail.strip() and re.search(r";\s*done\s*;?$",tail):
                    inline=self._try_inline_for(st); result=inline if inline is not None else Execution(2,err="sumbash: malformed inline for\n"); i+=1;
                else:
                    body,end,suffix=self._collect_loop_body(lines,i+1); result=self._run_for(name,expr,body); result=self._apply_compound_redirects(result,suffix); i=end+1;
                out.append(result.out); err.append(result.err); first_stdin=""; continue;

            # while/until
            wm=re.match(r"^(while|until)\s+(.*?);\s*do\s*;?$",st,re.S);
            if wm:
                body,end,suffix=self._collect_loop_body(lines,i+1); result=self._run_while(wm.group(2),body,until=(wm.group(1)=="until")); result=self._apply_compound_redirects(result,suffix); out.append(result.out); err.append(result.err); i=end+1; first_stdin=""; continue;

            # if / elif / else / fi
            im=re.match(r"^if\s+(.*?);\s*then\s*;?$",st,re.S);
            if im:
                branches,else_body,end,suffix=self._collect_if(lines,i+1,im.group(1)); result=self._run_if_construct(branches,else_body); result=self._apply_compound_redirects(result,suffix); out.append(result.out); err.append(result.err); i=end+1; first_stdin=""; continue;

            # case WORD in ... esac
            cm=re.match(r"^case\s+(.*?)\s+in\s*;?$",st,re.S);
            if cm:
                clauses,end,suffix=self._collect_case(lines,i+1); result=self._run_case_construct(cm.group(1),clauses); result=self._apply_compound_redirects(result,suffix); out.append(result.out); err.append(result.err); i=end+1; first_stdin=""; continue;

            result=self.run_line(raw,capture=True,stdin=first_stdin); out.append(result.out); err.append(result.err); first_stdin=""; i+=1;
        return Execution(result.code,_stream_join(out),"".join(err));

    # ---------- prompt / script ----------
    def prompt(self):
        ps1=self.get("PS1") or r"\u@\h:\w\$ ";
        host=(self.get("HOSTNAME") or os.environ.get("HOSTNAME") or socket.gethostname() or "host"); cwd=self.logical_cwd; home=self.fsa.home;
        if home and cwd.startswith(home): shown="~"+cwd[len(home):];
        else: shown=cwd;
        table={r"\u":getpass.getuser(),r"\h":host.split(".",1)[0],r"\H":host,r"\w":shown,r"\W":Path(cwd).name or cwd,r"\$":"#" if hasattr(os,"geteuid") and os.geteuid()==0 else "$",r"\t":datetime.now().strftime("%H:%M:%S"),r"\d":datetime.now().strftime("%a %b %d"),r"\n":"\n",r"\e":"\x1b",r"\a":"\a",r"\r":"\r",r"\[":"",r"\]":""};
        # Bash accepts \nnn octal escapes in PS1. This matters for long-lived prompts
        # that predate the more readable \e spelling (for example \033[...m).
        ps1=re.sub(r"\\([0-7]{3})",lambda m:chr(int(m.group(1),8)),ps1);
        for key,value in table.items(): ps1=ps1.replace(key,value);
        try: return self.expand_text(ps1);
        except ValueError: return ps1;

    def run_script(self,path,args=None):
        try: p=self._resolve_source_path(path); text=p.read_text(encoding="utf-8",errors="replace");
        except OSError as exc: return Execution(1,err="sumbash: {}: {}\n".format(path,exc));
        old_argv,old_argv0=self.argv,self.argv0; old_exit=self._exit_requested; self.argv=list(args or []); self.argv0=str(path); self._script_depth+=1; self._exit_requested=None;
        try:
            result=self._run_block(self._logical_lines(text));
            if self._exit_requested is not None: result.code=int(self._exit_requested);
            trapcmd=self.traps.get("0") or self.traps.get("EXIT");
            if trapcmd:
                saved_exit=self._exit_requested; self._exit_requested=None; tr=self.run_line(trapcmd,capture=True); self._exit_requested=saved_exit; result.out=_stream_join((result.out,tr.out)); result.err+=tr.err;
            return result;
        finally:
            self._script_depth-=1; self._exit_requested=old_exit; self.argv,self.argv0=old_argv,old_argv0;

    def _startup_files(self):
        explicit=str(self.env.get("SUMBASH_STARTUP","") or "").strip();
        if explicit:
            return [Path(value).expanduser() for value in explicit.split(os.pathsep) if str(value).strip()];
        home=Path(self.env.get("HOME") or self.fsa.home or Path.home()).expanduser();
        return [home/".sumbashrc",home/".autoexec"];

    def _load_startup_files(self):
        if self._startup_loaded: return;
        self._startup_loaded=True;
        for path in self._startup_files():
            try:
                if not path.is_file(): continue;
            except OSError: continue;
            result=self._bi_source([str(path)]);
            self.last_status=result.code;
            if result.out:
                if isinstance(result.out,(bytes,bytearray)):
                    sys.stdout.buffer.write(bytes(result.out)); sys.stdout.buffer.flush();
                else: sys.stdout.write(str(result.out)); sys.stdout.flush();
            if result.err: sys.stderr.write(result.err); sys.stderr.flush();

    def interactive_loop(self):
        self.interactive=True; self._load_startup_files(); self._setup_readline();
        try:
            while True:
                try:
                    line=input(self.prompt());
                    request=self._decode_help_trigger(line);
                    if request is not None:
                        original,point,topic=request;
                        self._open_help_browser(topic);
                        self._pending_readline_restore=(original,point);
                        continue;
                    self.run_line(line,capture=False,record_history=True);
                except EOFError:
                    # Ctrl-D on an empty interactive prompt is shell EOF: exit/logout.
                    sys.stdout.write("\n"); return self.last_status;
                except KeyboardInterrupt:
                    sys.stdout.write("\n"); self.last_status=130;
                except ShellExit as exc: return exc.code;
        finally:
            self._write_history();
