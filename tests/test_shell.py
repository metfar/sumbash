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


def test_unquoted_pathname_expansion_and_quoted_literal_pattern(tmp_path):
    (tmp_path/"alpha.sh").write_text("a",encoding="utf-8");
    (tmp_path/"beta.sh").write_text("b",encoding="utf-8");
    (tmp_path/"note.txt").write_text("n",encoding="utf-8");
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_line("echo *sh",capture=True);
    assert result.code==0;
    assert result.out=="alpha.sh beta.sh\n";
    quoted=shell.run_line("echo '*sh'",capture=True);
    assert quoted.out=="*sh\n";


def test_glob_directory_operand_matches_bash_style_ls_use_case(tmp_path):
    (tmp_path/"borraVersiones.sh").write_text("x",encoding="utf-8");
    (tmp_path/"sum.sh").write_text("x",encoding="utf-8");
    d=tmp_path/"sumbash"; d.mkdir(); (d/"sendbash.sh").write_text("x",encoding="utf-8");
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_line("ls *sh",capture=True);
    assert result.code==0;
    assert "borraVersiones.sh" in result.out;
    assert "sum.sh" in result.out;
    assert "sumbash:" in result.out;
    assert "sendbash.sh" in result.out;


def test_unmatched_glob_stays_literal_and_redirection_detects_ambiguity(tmp_path):
    shell=ShellRuntime(cwd=tmp_path);
    assert shell.run_line("printf '%s' no-match-*.zzz",capture=True).out=="no-match-*.zzz";
    (tmp_path/"a.txt").write_text("",encoding="utf-8");
    (tmp_path/"b.txt").write_text("",encoding="utf-8");
    result=shell.run_line("echo hi > *.txt",capture=True);
    assert result.code==1;
    assert "ambiguous redirect" in result.err;


def test_indexed_array_assignment_and_for_loop():
    shell=ShellRuntime();
    result=shell.run_line('PACKAGES=(core ui data)',capture=True);
    assert result.code==0;
    assert shell.arrays['PACKAGES']==['core','ui','data'];
    result=shell.run_line('for f in "${PACKAGES[@]}"; do echo sum$f; done',capture=True);
    assert result.code==0;
    assert result.out=='sumcore\nsumui\nsumdata\n';


def test_multiline_for_loop_in_script(tmp_path):
    script=tmp_path/'loop.sh';
    script.write_text('for f in one two three; do\n  echo "$f"\ndone\n',encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_script(str(script));
    assert result.code==0;
    assert result.out=='one\ntwo\nthree\n';


def test_unquoted_variable_field_splitting_and_globbing(tmp_path):
    (tmp_path/'one.sh').write_text('1',encoding='utf-8');
    (tmp_path/'two.sh').write_text('2',encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path);
    shell.run_line("packs='one.sh *wo.sh'",capture=True);
    result=shell.run_line('echo $packs',capture=True);
    assert result.out=='one.sh two.sh\n';


def test_binary_external_pipeline_can_redirect_gzip(tmp_path):
    import shutil;
    if not shutil.which('gzip'): return;
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_line("printf 'hello\\n' | gzip > hello.gz",capture=True);
    assert result.code==0;
    assert (tmp_path/'hello.gz').read_bytes().startswith(bytes([0x1f,0x8b]));
    import gzip;
    assert gzip.decompress((tmp_path/'hello.gz').read_bytes())==b'hello\n';


def test_assignment_expansion_does_not_field_split():
    shell=ShellRuntime();
    shell.run_line("B='x y'",capture=True);
    result=shell.run_line('A=$B',capture=True);
    assert result.code==0;
    assert shell.get('A')=='x y';


def test_functions_local_return_and_if(tmp_path):
    script=tmp_path/'functions.sh';
    script.write_text('''X=outer\nfunction choose()\n{\n  local X=inner\n  if [ "$1" = yes ]; then\n    echo "$X:$1"\n    return 0\n  else\n    return 7\n  fi\n}\nchoose yes\necho "$X"\n''',encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_script(str(script));
    assert result.code==0;
    assert result.out=='inner:yes\nouter\n';


def test_associative_arrays_eval_and_parameter_lookup(tmp_path):
    script=tmp_path/'assoc.sh';
    script.write_text('''declare -A before\npackage=sumcore\nv=0.1.0a15\narray=before\neval "$array[\\\"$package\\\"]=\\\"$v\\\""\necho "${before[$package]}"\n''',encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_script(str(script));
    assert result.code==0;
    assert result.out=='0.1.0a15\n';


def test_heredoc_is_passed_as_stdin(tmp_path, monkeypatch):
    import types;
    script=tmp_path/'here.sh';
    script.write_text("cat <<'EOF'\n$HOME literal\nEOF\n",encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_script(str(script));
    assert result.code==0;
    assert result.out=='$HOME literal\n';


def test_braced_group_redirects_aggregate_output(tmp_path):
    script=tmp_path/'group.sh';
    script.write_text('''report=report.txt\n{\n echo one\n echo two\n} > "$report"\necho done\n''',encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_script(str(script));
    assert result.code==0;
    assert result.out=='done\n';
    assert (tmp_path/'report.txt').read_text(encoding='utf-8')=='one\ntwo\n';


def test_case_nested_and_hyphenated_function_name(tmp_path):
    script=tmp_path/'case.sh';
    script.write_text('''upgrade-stop() { echo upgrade; }\ncase "$1" in\n  restart)\n    case yes in\n      y*) echo nested ;;\n    esac\n    ;;\n  upgrade-stop) upgrade-stop ;;\n  *) echo other ;;\nesac\n''',encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path);
    assert shell.run_script(str(script),['restart']).out=='nested\n';
    assert shell.run_script(str(script),['upgrade-stop']).out=='upgrade\n';


def test_while_break_and_continue(tmp_path):
    script=tmp_path/'while.sh';
    script.write_text('''i=0\nwhile [ $i -lt 5 ]; do\n  i=$(($i+1))\n  if [ $i -eq 2 ]; then\n    continue\n  fi\n  echo $i\n  if [ $i -eq 3 ]; then\n    break\n  fi\ndone\n''',encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_script(str(script));
    assert result.code==0;
    assert result.out=='1\n3\n';


def test_script_exit_preserves_previous_output(tmp_path):
    script=tmp_path/'exit.sh';
    script.write_text('echo before\nexit 7\necho after\n',encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_script(str(script));
    assert result.code==7;
    assert result.out=='before\n';


def test_parameter_pattern_removal_and_special_ids(monkeypatch):
    shell=ShellRuntime(argv0='/etc/init.d/apache2-test');
    assert shell.expand_text('${0##*/}')=='apache2-test';
    shell.set_var('prog','worker.sh');
    assert shell.expand_text('${prog%.sh}')=='worker';
    assert shell.get('PPID').isdigit();


def test_source_without_arguments_preserves_positional_parameters(tmp_path):
    inc=tmp_path/'inc.sh'; inc.write_text('echo "$1:$2"\n',encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path,argv=['one','two']);
    result=shell.run_line('. inc.sh',capture=True);
    assert result.out=='one:two\n';
    assert shell.argv==['one','two'];


def test_set_positional_and_shift():
    shell=ShellRuntime(argv=['old']);
    assert shell.run_line('set alpha beta gamma',capture=True).code==0;
    assert shell.argv==['alpha','beta','gamma'];
    assert shell.run_line('shift 2',capture=True).code==0;
    assert shell.argv==['gamma'];


def test_exit_trap_runs_and_script_output_is_preserved(tmp_path):
    script=tmp_path/'trap.sh';
    script.write_text("cleanup() { echo cleaned; }\ntrap cleanup 0\necho body\nexit 3\n",encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_script(str(script));
    assert result.code==3;
    assert result.out=='body\ncleaned\n';


def test_sum_shell_identity_does_not_overwrite_inherited_shell():
    shell=ShellRuntime(env={'SHELL':'/bin/bash'});
    assert shell.get('SHELL')=='/bin/bash';
    assert shell.get('SUM_SHELL');
    assert shell.get('SUM_SHELL_VERSION')=='0.1.0a20';
    env=shell.environment();
    assert env['SHELL']=='/bin/bash';
    assert env['SUM_SHELL_VERSION']=='0.1.0a20';


def test_fsa_logical_cwd_and_sumio_redirection(tmp_path):
    from sumbash.shell import ShellRuntime;
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_line('echo hello > out.txt; cat out.txt',capture=True);
    assert result.code==0;
    assert result.out=='hello\n';
    assert shell.logical_cwd==shell.fsa.logical_path(tmp_path);
    assert (tmp_path/'out.txt').read_text(encoding='utf-8')=='hello\n';


def test_df_uses_sumfsa_volume_model(tmp_path):
    from sumbash.applets import app_df;
    from sumbash.shell import ShellRuntime;
    shell=ShellRuntime(cwd=tmp_path);
    result=app_df(['-h'],runtime=shell);
    assert result.code==0;
    assert 'Mounted on' in result.out;


def test_source_dot_autoexec_persists_prompt_alias_and_variables(tmp_path):
    autoexec=tmp_path/'.autoexec';
    autoexec.write_text("export SAMPLE=ready\nalias ll='ls -la'\nPS1='\\033[32mSUM>\\033[0m '\n",encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path,interactive=True);
    first=shell.run_line('. .autoexec',capture=True);
    assert first.code==0;
    assert first.err=='';
    assert shell.get('SAMPLE')=='ready';
    assert shell.aliases.get('ll')=='ls -la';
    assert shell.prompt().startswith('\x1b[32mSUM>\x1b[0m ');
    shell.set_var('SAMPLE','reset');
    second=shell.run_line('source .autoexec',capture=True);
    assert second.code==0;
    assert shell.get('SAMPLE')=='ready';


def test_interactive_startup_loads_home_autoexec_once(tmp_path):
    autoexec=tmp_path/'.autoexec';
    autoexec.write_text("export STARTED=yes\nalias ll='ls -la'\nPS1='\\033[36mAUTO>\\033[0m '\n",encoding='utf-8');
    shell=ShellRuntime(env={'HOME':str(tmp_path)},cwd=tmp_path,interactive=True);
    shell._load_startup_files();
    assert shell.get('STARTED')=='yes';
    assert shell.aliases.get('ll')=='ls -la';
    assert shell.prompt().startswith('\x1b[36mAUTO>\x1b[0m ');
    autoexec.write_text("export STARTED=twice\n",encoding='utf-8');
    shell._load_startup_files();
    assert shell.get('STARTED')=='yes';


def test_ls_uses_ansi_colors_by_default_on_tty(tmp_path):
    (tmp_path/'folder').mkdir();
    (tmp_path/'run.sh').write_text('#!/bin/sh\n',encoding='utf-8');
    (tmp_path/'run.sh').chmod(0o755);
    shell=ShellRuntime(cwd=tmp_path,interactive=True);
    shell._stdout_is_tty=True;
    result=shell.run_line('ls',capture=True);
    assert result.code==0;
    assert '\x1b[' in result.out;
    assert '\x1b[01;34mfolder\x1b[0m' in result.out;
    assert '\x1b[01;32mrun.sh\x1b[0m' in result.out;
    plain=shell.run_line('ls --color=never',capture=True);
    assert '\x1b[' not in plain.out;


def test_source_autoexec_accepts_nested_if_with_spaced_fi_semicolon(tmp_path):
    autoexec=tmp_path/'.autoexec';
    marker=tmp_path/'.Xmodmap';
    marker.write_text('keycode 1 = Escape\n',encoding='utf-8');
    content='graf=X\nif [ \"q$graf\" == \"qX\" ]; then\n  export OUTER=yes\n  if [ -f \"$HOME/.Xmodmap\" ] ; then\n    export INNER=yes\n  fi ;\nelse\n  export OUTER=no\nfi\n';
    autoexec.write_text(content,encoding='utf-8');
    shell=ShellRuntime(env={'HOME':str(tmp_path)},cwd=tmp_path,interactive=True);
    result=shell.run_line('. .autoexec',capture=True);
    assert result.code==0;
    assert result.err=='';
    assert shell.get('OUTER')=='yes';
    assert shell.get('INNER')=='yes';


def test_source_syntax_error_returns_error_without_raising(tmp_path):
    broken=tmp_path/'.autoexec';
    broken.write_text('if true; then\n  echo never-closed\n',encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path,interactive=True);
    result=shell.run_line('. .autoexec',capture=True);
    assert result.code==2;
    assert 'unterminated if' in result.err;
    follow=shell.run_line('echo still-alive',capture=True);
    assert follow.code==0;
    assert follow.out=='still-alive\n';


def test_if_closing_keyword_redirects_stdout_and_stderr_as_one_group(tmp_path):
    script=tmp_path/'if-group-redir.sh';
    script.write_text('''if true; then\n  echo group-out\n  cat definitely-missing-file\nfi 2>err.txt 1>out.txt\necho after\n''',encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_script(str(script));
    assert result.code==0;
    assert result.out=='after\n';
    assert result.err=='';
    assert (tmp_path/'out.txt').read_text(encoding='utf-8')=='group-out\n';
    assert 'definitely-missing-file' in (tmp_path/'err.txt').read_text(encoding='utf-8');


def test_loop_closing_keyword_redirect_applies_to_whole_loop(tmp_path):
    script=tmp_path/'loop-group-redir.sh';
    script.write_text('''for x in one two; do\n  echo $x\ndone >> loop.txt\nwhile false; do\n  echo never\ndone >> loop.txt\n''',encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_script(str(script));
    assert result.code!=127;
    assert result.out=='';
    assert (tmp_path/'loop.txt').read_text(encoding='utf-8')=='one\ntwo\n';


def test_case_closing_keyword_redirects_as_group(tmp_path):
    script=tmp_path/'case-group-redir.sh';
    script.write_text('''case yes in\n  y*) echo case-out; cat no-such-case-file ;;\nesac 1>case.out 2>case.err\n''',encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_script(str(script));
    assert result.out=='';
    assert result.err=='';
    assert (tmp_path/'case.out').read_text(encoding='utf-8')=='case-out\n';
    assert 'no-such-case-file' in (tmp_path/'case.err').read_text(encoding='utf-8');


def test_compound_redirect_fd_duplication_obeys_left_to_right_order(tmp_path):
    script=tmp_path/'dup-group-redir.sh';
    script.write_text('''if true; then\n  echo visible\n  cat no-such-dup-file\nfi >both.txt 2>&1\n''',encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_script(str(script));
    assert result.out=='';
    assert result.err=='';
    content=(tmp_path/'both.txt').read_text(encoding='utf-8');
    assert 'visible\n' in content;
    assert 'no-such-dup-file' in content;


def test_nested_compound_redirect_stays_attached_to_inner_group(tmp_path):
    script=tmp_path/'nested-group-redir.sh';
    script.write_text('''if true; then\n  echo outer-before\n  if true; then\n    echo inner\n  fi > inner.txt\n  echo outer-after\nfi > outer.txt\n''',encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_script(str(script));
    assert result.code==0;
    assert result.out=='';
    assert (tmp_path/'inner.txt').read_text(encoding='utf-8')=='inner\n';
    assert (tmp_path/'outer.txt').read_text(encoding='utf-8')=='outer-before\nouter-after\n';
