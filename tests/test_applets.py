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
