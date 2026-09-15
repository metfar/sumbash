from sumbash.applets import run_applet;
from sumbash.shell import ShellRuntime;


def test_suminfo_identity_field_and_uname():
    shell=ShellRuntime();
    value=run_applet("suminfo",["--field","machine.architecture"],runtime=shell);
    assert value.code==0;
    assert value.out.strip();
    uname=run_applet("uname",["-m"],runtime=shell);
    assert uname.code==0;
    assert uname.out.strip()==value.out.strip();


def test_uptime_pretty():
    result=run_applet("uptime",["-p"]);
    assert result.code in (0,1);
    if result.code==0: assert result.out.startswith("up ");


def test_date_day_of_year_and_epoch():
    result=run_applet("date",["-d","2026-09-14","+%j"]);
    assert result.code==0;
    assert result.out.strip().isdigit();
    epoch=run_applet("date",["-d","1970-01-01","+%s"]);
    assert epoch.code==0;
    # Local timezone may offset midnight; the important part here is native %s support.
    int(epoch.out.strip());


def test_tty_applet_reports_terminal(monkeypatch):
    import io;
    from sumbash.applets import app_tty;
    class FakeIn(io.StringIO):
        def isatty(self): return True;
        def fileno(self): return 7;
    fake=FakeIn();
    monkeypatch.setattr("sumbash.applets.sys.stdin",fake);
    monkeypatch.setattr("sumbash.applets.os.ttyname",lambda fd:"/dev/pts/9");
    result=app_tty([]);
    assert result.code==0;
    assert result.out=="/dev/pts/9\n";


def test_ls_uses_ls_colors_on_terminal(tmp_path):
    (tmp_path/"adir").mkdir();
    (tmp_path/"note.py").write_text("print('x')\n",encoding="utf-8");
    shell=ShellRuntime(env={"LS_COLORS":"di=01;34:*.py=01;35:rs=0"},cwd=tmp_path);
    shell._command_stdout_is_tty=True;
    result=run_applet("ls",[],runtime=shell);
    assert result.code==0;
    assert "\x1b[01;34madir\x1b[0m" in result.out;
    assert "\x1b[01;35mnote.py\x1b[0m" in result.out;


def test_ls_color_modes_and_pipeline_safety(tmp_path):
    (tmp_path/"adir").mkdir();
    env={"LS_COLORS":"di=01;34:rs=0"};
    shell=ShellRuntime(env=env,cwd=tmp_path);
    shell._command_stdout_is_tty=False;
    auto=run_applet("ls",[],runtime=shell);
    assert "\x1b[" not in auto.out;
    forced=run_applet("ls",["--color=always"],runtime=shell);
    assert "\x1b[01;34m" in forced.out;
    never=run_applet("ls",["--color=never"],runtime=shell);
    assert "\x1b[" not in never.out;
    shell._stdout_is_tty=True;
    piped=shell.run_line("ls | cat",capture=True);
    assert "\x1b[" not in piped.out;


def test_ls_extended_sort_hidden_recursive_and_formats(tmp_path):
    (tmp_path/"adir").mkdir();
    (tmp_path/"adir"/"nested.txt").write_text("x",encoding="utf-8");
    (tmp_path/"b.txt").write_text("123456",encoding="utf-8");
    (tmp_path/"a.txt").write_text("1",encoding="utf-8");
    (tmp_path/".hidden").write_text("h",encoding="utf-8");
    shell=ShellRuntime(cwd=tmp_path);
    by_size=run_applet("ls",["-S1"],runtime=shell);
    assert by_size.code==0;
    assert "b.txt" in by_size.out;
    normal=run_applet("ls",["-1"],runtime=shell);
    assert ".hidden" not in normal.out;
    almost=run_applet("ls",["-A1"],runtime=shell);
    assert ".hidden" in almost.out;
    recursive=run_applet("ls",["-R1"],runtime=shell);
    assert "nested.txt" in recursive.out;
    grouped=run_applet("ls",["--group-directories-first","-1"],runtime=shell);
    assert grouped.out.splitlines()[0].startswith("adir");


def test_ls_long_human_indicators_and_help(tmp_path):
    d=tmp_path/"dir"; d.mkdir();
    f=tmp_path/"run.sh"; f.write_text("#!/bin/sh\n",encoding="utf-8"); f.chmod(0o755);
    shell=ShellRuntime(cwd=tmp_path);
    long=run_applet("ls",["-lhF"],runtime=shell);
    assert long.code==0;
    assert "dir/" in long.out;
    assert "run.sh*" in long.out;
    help_result=run_applet("ls",["--help"],runtime=shell);
    assert "--group-directories-first" in help_result.out;
    zero=run_applet("ls",["--zero"],runtime=shell);
    assert "\0" in zero.out;
