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
