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
