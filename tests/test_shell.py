from pathlib import Path;

from sumbash.shell import ShellRuntime;


def test_assignments_are_visible_to_later_commands():
    shell=ShellRuntime();
    result=shell.run_line("A=5.5; echo $A",capture=True);
    assert result.code==0;
    assert result.out=="5.5\n";


def test_arithmetic_command_substitution_and_pipeline():
    shell=ShellRuntime();
    assert shell.run_line("echo $((5.5*2))",capture=True).out=="11\n";
    assert shell.run_line("A=$(printf 42); echo $A",capture=True).out=="42\n";
    assert shell.run_line("printf 'b\\na\\na\\n' | sort | uniq -c",capture=True).out.endswith("1 b\n");


def test_global_export_and_command_resolution():
    shell=ShellRuntime();
    result=shell.run_line("global X=42; export X; echo $X",capture=True);
    assert result.out=="42\n";
    assert shell.get("X")=="42";
    assert "X" in shell.exported;
    assert shell.run_line("command -v ls",capture=True).out.strip()=="ls";
    assert "SUM applet" in shell.run_line("command -V ls",capture=True).out;


def test_cdspell_and_physical_pwd(tmp_path):
    (tmp_path/"projects").mkdir();
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_line("shopt -s cdspell; cd projetcs; pwd -P",capture=True);
    assert result.code==0;
    assert result.out.strip()==str((tmp_path/"projects").resolve());


def test_find_grep_cut_rev_pipeline(tmp_path):
    (tmp_path/"a.bas").write_text("x",encoding="utf-8");
    (tmp_path/"B.BAS").write_text("y",encoding="utf-8");
    (tmp_path/"no.txt").write_text("z",encoding="utf-8");
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_line("find . -iname '*bas' | sort | rev | rev",capture=True);
    assert result.code==0;
    assert "./a.bas" in result.out;
    assert "./B.BAS" in result.out;


def test_script_keeps_output_from_all_lines(tmp_path):
    script=tmp_path/"hello.sh";
    script.write_text("#!/usr/bin/env sumbash\necho one\necho two\n",encoding="utf-8");
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_script(str(script));
    assert result.code==0;
    assert result.out=="one\ntwo\n";


def test_prompt_supports_octal_ansi_and_hostname_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr("sumbash.shell.socket.gethostname",lambda:"x1b5ca.example");
    shell=ShellRuntime(env={"HOME":str(tmp_path),"PS1":r"\033[31m \h \[\033[0m\] \w > "},cwd=tmp_path,interactive=True);
    prompt=shell.prompt();
    assert prompt.startswith("\x1b[31m x1b5ca \x1b[0m ~ > ");
    assert "\\033" not in prompt;


def test_external_interactive_command_inherits_terminal(monkeypatch,tmp_path):
    import types;
    shell=ShellRuntime(cwd=tmp_path,interactive=True);
    shell._stdout_is_tty=True; shell._command_stdout_is_tty=True;
    monkeypatch.setattr(shell,"_which_external",lambda name:"/usr/bin/mc" if name=="mc" else None);
    monkeypatch.setattr("sumbash.shell.sys.stdin.isatty",lambda:True);
    monkeypatch.setattr("sumbash.shell.sys.stdout.isatty",lambda:True);
    monkeypatch.setattr("sumbash.shell.sys.stderr.isatty",lambda:True);
    calls=[];
    def fake_run(cmd,**kwargs):
        calls.append((cmd,kwargs)); return types.SimpleNamespace(returncode=0,stdout="",stderr="");
    monkeypatch.setattr("sumbash.shell.subprocess.run",fake_run);
    result=shell.run_line("mc",capture=True);
    assert result.code==0;
    assert calls;
    kwargs=calls[0][1];
    assert "stdout" not in kwargs and "stderr" not in kwargs and "input" not in kwargs;


def test_history_recording_controls_and_builtin(tmp_path):
    shell=ShellRuntime(env={"HOME":str(tmp_path),"HISTSIZE":"3","HISTCONTROL":"ignoredups"},interactive=True);
    shell.run_line("echo one",capture=True,record_history=True);
    shell.run_line("echo one",capture=True,record_history=True);
    shell.run_line("echo two",capture=True,record_history=True);
    shell.run_line("echo three",capture=True,record_history=True);
    shell.run_line("echo four",capture=True,record_history=True);
    assert shell.history==["echo two","echo three","echo four"];
    shown=shell.run_line("history 2",capture=True,record_history=True).out;
    assert "echo four" in shown and "history 2" in shown;


def test_history_records_only_top_level_commandline(tmp_path):
    shell=ShellRuntime(env={"HOME":str(tmp_path),"HISTCONTROL":"ignoreboth"},interactive=True);
    shell.set_var("PS1","$(echo prompt-helper) > ");
    # Prompt command substitution executes, but is infrastructure, not a typed command.
    shell.prompt();
    assert shell.history==[];
    shell.run_line("echo typed",capture=True,record_history=True);
    # Nested command substitutions also execute without becoming separate entries.
    shell.run_line("echo $(echo nested)",capture=True,record_history=True);
    assert shell.history==["echo typed","echo $(echo nested)"];


def test_history_ignores_leading_space_with_ignoreboth(tmp_path):
    shell=ShellRuntime(env={"HOME":str(tmp_path),"HISTCONTROL":"ignoreboth"},interactive=True);
    shell.run_line("echo visible",capture=True,record_history=True);
    shell.run_line(" secret-command",capture=True,record_history=True);
    assert shell.history==["echo visible"];


def test_history_default_hides_leading_space(tmp_path):
    shell=ShellRuntime(env={"HOME":str(tmp_path)},interactive=True);
    shell.run_line("echo visible",capture=True,record_history=True);
    shell.run_line(" hidden-command",capture=True,record_history=True);
    assert shell.get("HISTCONTROL")=="ignoreboth";
    assert shell.history==["echo visible"];


def test_script_lines_are_not_history(tmp_path):
    script=tmp_path/"sample.sh";
    script.write_text("echo from-script\necho again\n",encoding="utf-8");
    shell=ShellRuntime(env={"HOME":str(tmp_path),"HISTCONTROL":"ignoreboth"},cwd=tmp_path,interactive=True);
    shell.run_line("source sample.sh",capture=True,record_history=True);
    assert shell.history==["source sample.sh"];


def test_cat_reads_interactive_stdin_until_eof(monkeypatch,tmp_path):
    shell=ShellRuntime(cwd=tmp_path,interactive=True);
    shell._stdout_is_tty=True;
    monkeypatch.setattr("sumbash.shell.sys.stdin.isatty",lambda:True);
    monkeypatch.setattr("sumbash.applets.sys.stdin.read",lambda:"Blabla\n");
    result=shell.run_line("cat > archivo",capture=True);
    assert result.code==0;
    assert result.out=="";
    assert (tmp_path/"archivo").read_text(encoding="utf-8")=="Blabla\n";


def test_logout_is_exit_alias():
    import pytest;
    from sumbash.shell import ShellExit;
    shell=ShellRuntime(interactive=True);
    with pytest.raises(ShellExit) as exc:
        shell.run_line("logout 7",capture=True);
    assert exc.value.code==7;


def test_interactive_ctrl_d_exits(monkeypatch,tmp_path):
    shell=ShellRuntime(env={"HOME":str(tmp_path)},cwd=tmp_path,interactive=True);
    monkeypatch.setattr(shell,"_setup_readline",lambda:None);
    monkeypatch.setattr(shell,"_write_history",lambda:None);
    def eof(_prompt):
        raise EOFError;
    monkeypatch.setattr("builtins.input",eof);
    assert shell.interactive_loop()==0;


def test_native_completion_commands_paths_variables_and_cd(tmp_path, monkeypatch):
    from sumbash.completion import CompletionEngine;
    bindir=tmp_path/"bin"; bindir.mkdir();
    tool=bindir/"sumtool"; tool.write_text("#!/bin/sh\n",encoding="utf-8"); tool.chmod(0o755);
    (tmp_path/"sendbash.sh").write_text("x",encoding="utf-8");
    (tmp_path/"sumbash").mkdir(); (tmp_path/"sumbash-0.1.0a6").mkdir();
    shell=ShellRuntime(env={"HOME":str(tmp_path),"PATH":str(bindir),"HOSTNAME":"x1b5ca"},cwd=tmp_path,interactive=True);
    engine=CompletionEngine(shell);
    assert "sumtool" in engine.candidates("sumt");
    assert engine.candidates("ls sen")==["sendbash.sh"];
    assert set(engine.candidates("ls s"))=={"sendbash.sh","sumbash/","sumbash-0.1.0a6/"};
    assert set(engine.candidates("cd sumbash"))=={"sumbash/","sumbash-0.1.0a6/"};
    assert engine.candidates("echo $HO")==["$HOME","$HOSTNAME"];


def test_completion_quotes_spaces_and_hidden_files(tmp_path):
    from sumbash.completion import CompletionEngine;
    (tmp_path/"my file.txt").write_text("x",encoding="utf-8");
    (tmp_path/".hidden").write_text("x",encoding="utf-8");
    shell=ShellRuntime(env={"HOME":str(tmp_path),"PATH":""},cwd=tmp_path);
    engine=CompletionEngine(shell);
    assert engine.candidates("cat my")==['my\\ file.txt'];
    spaced=tmp_path/"my folder"; spaced.mkdir(); (spaced/"inside.txt").write_text("x",encoding="utf-8");
    assert engine.candidates(r"cat my\ folder/in")==[r"my\ folder/inside.txt"];
    assert engine.candidates("cat .h")==[".hidden"];
    assert ".hidden" not in engine.candidates("cat ");


def test_complete_and_compgen_builtins(tmp_path):
    shell=ShellRuntime(env={"HOME":str(tmp_path),"PATH":""},cwd=tmp_path);
    result=shell.run_line("complete -W 'start stop status' svc",capture=True);
    assert result.code==0;
    assert shell._completion_engine.candidates("svc st")==["start","status","stop"];
    printed=shell.run_line("complete -p svc",capture=True);
    assert "complete -W 'start stop status' svc" in printed.out;
    generated=shell.run_line("compgen -W 'alpha beta alpine' al",capture=True);
    assert generated.out.splitlines()==["alpha","alpine"];
    shell.run_line("complete -r svc",capture=True);
    assert "svc" not in shell.completion_specs;


def test_complete_directory_action(tmp_path):
    shell=ShellRuntime(env={"HOME":str(tmp_path),"PATH":""},cwd=tmp_path);
    (tmp_path/"docs").mkdir(); (tmp_path/"data.txt").write_text("x",encoding="utf-8");
    assert shell.run_line("complete -d jump",capture=True).code==0;
    assert shell._completion_engine.candidates("jump d")==["docs/"];


def test_internal_stream_applets_follow_shell_cwd_after_cd(tmp_path):
    examples=tmp_path/"examples"; examples.mkdir();
    (examples/"toolbox.sh").write_text("alpha\nbeta\n",encoding="utf-8");
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_line("cd examples; cat toolbox.sh",capture=True);
    assert result.code==0;
    assert result.out=="alpha\nbeta\n";
    assert shell.run_line("head -n 1 toolbox.sh",capture=True).out=="alpha\n";
    assert shell.run_line("tail -n 1 toolbox.sh",capture=True).out=="beta\n";
    assert shell.run_line("wc -l toolbox.sh",capture=True).out.startswith("2 ");


def test_tee_and_test_follow_shell_cwd_after_cd(tmp_path):
    work=tmp_path/"work"; work.mkdir();
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_line("cd work; printf hello | tee note.txt",capture=True);
    assert result.code==0;
    assert result.out=="hello";
    assert (work/"note.txt").read_text(encoding="utf-8")=="hello";
    assert shell.run_line("test -f note.txt",capture=True).code==0;
