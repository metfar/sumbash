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
"""Small portable shell core for sumbash 0.1.0a8.

This alpha intentionally implements a useful vertical slice: variables,
expansion, arithmetic with fractions, command substitution, pipelines,
redirections, aliases, selected Bash-compatible builtins and external fallback.
Compound grammar (if/for/while/functions/arrays) is scheduled for later alphas.
""";

from __future__ import annotations;

from dataclasses import dataclass;
from datetime import datetime;
import difflib;
import getpass;
import io;
import os;
from pathlib import Path;
import re;
import shutil;
import socket;
import subprocess;
import sys;

from .applets import APPLETS, AppletResult, run_applet;
from .arithmetic import SumArithmeticError, evaluate, format_number;
from .completion import CompletionEngine, CompletionSpec, format_completion_spec, parse_completion_spec, spec_candidates;


_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$");
_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S);
_OPERATORS = ("2>&1", "2>>", "2>", "&&", "||", ">>", "|", ";", ">", "<");


@dataclass
class Execution:
    code: int = 0;
    out: str = "";
    err: str = "";


class ShellExit(Exception):
    def __init__(self, code=0): self.code=int(code);


class ShellRuntime:
    def __init__(self, argv=None, env=None, cwd=None, interactive=False, argv0="sumbash"):
        self.env = dict(os.environ if env is None else env);
        self.vars = dict(self.env);
        # SUM follows the common interactive Bash distro convention: leading-space
        # commands are private from recall history and adjacent duplicates collapse.
        # An explicit HISTCONTROL from the caller always wins.
        self.vars.setdefault("HISTCONTROL","ignoreboth");
        self.exported = set(self.env);
        self.global_names = set();
        self.readonly = set();
        self.aliases = {};
        self.options = {"cdspell": False, "histappend": False, "checkwinsize": True, "globstar": False};
        self.shell_options = {"vi": False};
        self.cwd = str(Path(cwd or os.getcwd()).resolve());
        self.argv = list(argv or []);
        self.argv0 = str(argv0);
        self.interactive = bool(interactive);
        self.last_status = 0;
        self.history = [];
        self.pid = os.getpid();
        self._source_depth = 0;
        self._readline = None;
        self._history_loaded = False;
        self._history_file = None;
        self._readline_auto_history_disabled = False;
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
        return str(self.vars.get(name, ""));

    def set_var(self, name, value, export=None, global_scope=False):
        if name in self.readonly: raise ValueError("{}: readonly variable".format(name));
        self.vars[name] = str(value);
        if global_scope: self.global_names.add(name);
        if export is True: self.exported.add(name);
        elif export is False: self.exported.discard(name);
        if name in self.exported: self.env[name]=str(value);

    def unset(self, name):
        if name in self.readonly: raise ValueError("{}: readonly variable".format(name));
        self.vars.pop(name,None); self.env.pop(name,None); self.exported.discard(name); self.global_names.discard(name);

    def environment(self, overrides=None):
        env={name:str(self.vars.get(name,"")) for name in self.exported};
        env.update({"PWD":self.cwd});
        if overrides: env.update({k:str(v) for k,v in overrides.items()});
        return env;

    def resolve_command(self, name, all_matches=False):
        matches=[];
        if name in self.aliases: matches.append(("alias", self.aliases[name]));
        if name in self._builtin_names(): matches.append(("builtin", name));
        if name in APPLETS: matches.append(("applet", name));
        path = self._which_external(name);
        if path: matches.append(("external", path));
        return matches if all_matches else (matches[0] if matches else None);

    def _which_external(self, name):
        if os.sep in name or (os.altsep and os.altsep in name):
            p=Path(name); p=p if p.is_absolute() else Path(self.cwd)/p;
            return str(p) if p.exists() and os.access(p,os.X_OK) else None;
        return shutil.which(name, path=self.vars.get("PATH", os.environ.get("PATH","")));

    def _builtin_names(self):
        return {"cd","export","global","unset","readonly","set","shopt","alias","unalias","command","type","source",".","eval","read","inkey","history","complete","compgen","compopt","exit","logout","true","false","let"};

    # ---------- expansion ----------
    def _expand_parameter(self, body):
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
                out.append(result.out.rstrip("\n")); i=j+1; continue;
            if ch!="$": out.append(ch); i+=1; continue;
            if text.startswith("$((",i):
                end=self._balanced_arithmetic(text,i+3);
                if end is None: out.append("$"); i+=1; continue;
                expr=text[i+3:end];
                try: value=evaluate(expr,self.vars);
                except SumArithmeticError as exc: raise ValueError("arithmetic: {}".format(exc));
                out.append(format_number(value)); i=end+2; continue;
            if text.startswith("$(",i):
                end=self._balanced_paren(text,i+2);
                if end is None: out.append("$"); i+=1; continue;
                old_tty=self._stdout_is_tty; self._stdout_is_tty=False;
                try: result=self.run_line(text[i+2:end],capture=True);
                finally: self._stdout_is_tty=old_tty;
                out.append(result.out.rstrip("\n")); i=end+1; continue;
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
        tokens=[]; buf=[]; quote=None; i=0; was_quoted=False;
        def flush():
            nonlocal buf,was_quoted;
            if buf or was_quoted: tokens.append("".join(buf)); buf=[]; was_quoted=False;
        while i<len(line):
            c=line[i];
            if quote=="'":
                if c=="'": quote=None; was_quoted=True;
                else: buf.append(c);
                i+=1; continue;
            if quote=='"':
                if c=='"': quote=None; was_quoted=True; i+=1; continue;
                if c=="\\" and i+1<len(line) and line[i+1] in '$`"\\': buf.append(line[i+1]); i+=2; continue;
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
                    buf.append(self.expand_text(line[i:end])); i=end; continue;
                buf.append(c); i+=1; continue;
            # unquoted
            if c=="#" and not buf and (i==0 or line[i-1].isspace()): flush(); break;
            if c in "'\"": quote=c; was_quoted=True; i+=1; continue;
            if c=="\\" and i+1<len(line): buf.append(line[i+1]); i+=2; continue;
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
                buf.append(self.expand_text(line[i:end])); i=end; continue;
            buf.append(c); i+=1;
        if quote: raise ValueError("unterminated quote");
        flush(); return tokens;

    # ---------- execution ----------
    def run_line(self,line,capture=False,stdin="",record_history=False):
        line=str(line).rstrip("\n");
        if not line.strip(): return Execution();
        # History belongs to the interactive command line, not to the execution
        # engine.  Prompt substitutions, sourced/script lines, eval internals and
        # command substitutions call run_line() too, but must never become history.
        if record_history: self._record_history(line);
        try: result=self._run_raw_sequence(line,stdin=stdin);
        except ValueError as exc: return Execution(2,err="sumbash: {}\n".format(exc));
        self.last_status=result.code;
        if not capture:
            if result.out: sys.stdout.write(result.out); sys.stdout.flush();
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
        return Execution(code,"".join(out),"".join(err));

    def _run_raw_conditional(self,line,stdin=""):
        pieces,ops=self._split_raw(line,("&&","||")); out=[]; err=[];
        result=self._run_raw_pipeline(pieces[0],stdin=stdin); out.append(result.out); err.append(result.err);
        for op,piece in zip(ops,pieces[1:]):
            if op=="&&" and result.code!=0: continue;
            if op=="||" and result.code==0: continue;
            result=self._run_raw_pipeline(piece,stdin=""); out.append(result.out); err.append(result.err);
        return Execution(result.code,"".join(out),"".join(err));

    def _run_raw_pipeline(self,line,stdin=""):
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
        args=[]; in_data=stdin; out_file=None; append=False; err_file=None; err_append=False; merge_err=False; i=0;
        command_stdin_is_tty=bool(stdin_is_tty);
        while i<len(tokens):
            tok=tokens[i];
            if tok in (">",">>","<","2>","2>>"):
                if i+1>=len(tokens): return Execution(2,err="sumbash: redirection requires a file\n");
                target=tokens[i+1];
                if tok=="<":
                    command_stdin_is_tty=False;
                    try: in_data=(Path(self.cwd)/target if not Path(target).is_absolute() else Path(target)).read_text(encoding="utf-8",errors="replace");
                    except OSError as exc: return Execution(1,err="sumbash: {}: {}\n".format(target,exc));
                elif tok in (">",">>"): out_file=target; append=(tok==">>");
                else: err_file=target; err_append=(tok=="2>>");
                i+=2; continue;
            if tok=="2>&1": merge_err=True; i+=1; continue;
            args.append(tok); i+=1;
        if not args: return Execution();
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
        if merge_err and result.err: result.out += result.err; result.err="";
        if out_file:
            p=Path(out_file); p=p if p.is_absolute() else Path(self.cwd)/p;
            try:
                with open(p,"a" if append else "w",encoding="utf-8") as stream: stream.write(result.out);
                result.out="";
            except OSError as exc: result.code=1; result.err += "sumbash: {}: {}\n".format(out_file,exc);
        if err_file:
            p=Path(err_file); p=p if p.is_absolute() else Path(self.cwd)/p;
            try:
                with open(p,"a" if err_append else "w",encoding="utf-8") as stream: stream.write(result.err);
                result.err="";
            except OSError as exc: result.code=1; result.err += "sumbash: {}: {}\n".format(err_file,exc);
        return result;

    def _dispatch(self,name,args,stdin,temporary_env=None):
        if name in self._builtin_names(): return self._builtin(name,args,stdin);
        applet=run_applet(name,args,stdin=stdin,runtime=self);
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
            cp=subprocess.run([path]+list(args),input=stdin,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=self.cwd,env=self.environment(temporary_env),check=False);
            return Execution(cp.returncode,cp.stdout,cp.stderr);
        except OSError as exc: return Execution(126,err="sumbash: {}: {}\n".format(name,exc));

    # ---------- builtins ----------
    def _builtin(self,name,args,stdin):
        if name=="true": return Execution(0);
        if name=="false": return Execution(1);
        if name in ("exit","logout"): raise ShellExit(int(args[0]) if args else self.last_status);
        if name=="cd": return self._bi_cd(args);
        if name in ("export","global","readonly"): return self._bi_assign(name,args);
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
        target=args[0] if args else self.get("HOME") or str(Path.home());
        if target=="-": target=self.get("OLDPWD") or self.cwd;
        p=Path(target).expanduser(); p=p if p.is_absolute() else Path(self.cwd)/p;
        if not p.is_dir() and self.options.get("cdspell"):
            parent=p.parent if p.parent.is_dir() else Path(self.cwd); choices=[x.name for x in parent.iterdir() if x.is_dir()]; match=difflib.get_close_matches(p.name,choices,n=2,cutoff=.72);
            if len(match)==1: p=parent/match[0];
        if not p.is_dir(): return Execution(1,err="cd: {}: No such directory\n".format(target));
        old=self.cwd; self.cwd=str(p.resolve()); self.set_var("OLDPWD",old,export=True); self.set_var("PWD",self.cwd,export=True);
        return Execution(out=(self.cwd+"\n") if args and args[0]=="-" else "");

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
                elif kind=="builtin": out.append("{} is a shell builtin\n".format(name));
                elif kind=="applet": out.append("{} is a SUM applet\n".format(name));
                else: out.append("{} is {}\n".format(name,value));
        return Execution(code,"".join(out));

    def _bi_source(self,args):
        if not args: return Execution(2,err="source: filename required\n");
        p=Path(args[0]).expanduser(); p=p if p.is_absolute() else Path(self.cwd)/p;
        try: lines=p.read_text(encoding="utf-8",errors="replace").splitlines();
        except OSError as exc: return Execution(1,err="source: {}: {}\n".format(args[0],exc));
        old_argv=self.argv; self.argv=args[1:]; self._source_depth+=1; result=Execution(); out=[]; err=[];
        try:
            for line in lines:
                result=self.run_line(line,capture=True); out.append(result.out); err.append(result.err);
        finally: self._source_depth-=1; self.argv=old_argv;
        return Execution(result.code,"".join(out),"".join(err));

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
        return Execution(2,err="set: alpha supports set -o vi\n");

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

    # ---------- prompt / script ----------
    def prompt(self):
        ps1=self.get("PS1") or r"\u@\h:\w\$ ";
        host=(self.get("HOSTNAME") or os.environ.get("HOSTNAME") or socket.gethostname() or "host"); cwd=self.cwd; home=self.get("HOME");
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
        p=Path(path); p=p if p.is_absolute() else Path(self.cwd)/p;
        try: text=p.read_text(encoding="utf-8",errors="replace");
        except OSError as exc: return Execution(1,err="sumbash: {}: {}\n".format(path,exc));
        old_argv,old_argv0=self.argv,self.argv0; self.argv=list(args or []); self.argv0=str(path); result=Execution(); out=[]; err=[];
        try:
            # a1 executes logical physical lines; backslash continuation is supported.
            pending="";
            for raw in text.splitlines():
                if pending: raw=pending+raw; pending="";
                if raw.endswith("\\"): pending=raw[:-1]; continue;
                result=self.run_line(raw,capture=True); out.append(result.out); err.append(result.err);
            if pending:
                result=self.run_line(pending,capture=True); out.append(result.out); err.append(result.err);
        finally: self.argv,self.argv0=old_argv,old_argv0;
        return Execution(result.code,"".join(out),"".join(err));

    def interactive_loop(self):
        self.interactive=True; self._setup_readline();
        try:
            while True:
                try:
                    line=input(self.prompt());
                    self.run_line(line,capture=False,record_history=True);
                except EOFError:
                    # Ctrl-D on an empty interactive prompt is shell EOF: exit/logout.
                    sys.stdout.write("\n"); return self.last_status;
                except KeyboardInterrupt:
                    sys.stdout.write("\n"); self.last_status=130;
                except ShellExit as exc: return exc.code;
        finally:
            self._write_history();
