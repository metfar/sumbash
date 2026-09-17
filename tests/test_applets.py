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


def test_less_non_tty_renders_file_content(tmp_path):
    p=tmp_path/"pager.txt";
    p.write_text("alpha\nbeta\n",encoding="utf-8");
    r=run_applet("less",[str(p)]);
    assert r.code==0;
    assert r.out=="alpha\nbeta\n";


def test_less_reads_pipeline_text_when_not_tty():
    r=run_applet("less",[],stdin="alpha\nbeta\n");
    assert r.code==0;
    assert r.out=="alpha\nbeta\n";


def test_less_help():
    r=run_applet("less",["--help"]);
    assert r.code==0;
    assert "search" in r.out.lower();
    assert "PgDn" in r.out;


def test_less_follow_option_requires_named_file():
    r=run_applet("less",["--follow"],stdin="alpha\n");
    assert r.code==2;
    assert "named file" in r.err;


def test_less_follow_reads_appended_data_incrementally(tmp_path):
    from sumbash.applets import _less_follow_read, _less_read_sources;
    p=tmp_path/"app.log";
    p.write_text("one\n",encoding="utf-8");
    text,options,message,code=_less_read_sources(["--follow",str(p)],"",None);
    assert code==0 and options["follow"] is True;
    assert options["syntax"]=="log";
    with p.open("a",encoding="utf-8") as handle: handle.write("two\n");
    update=_less_follow_read(options);
    assert update==( "append", "two\n" );
    assert _less_follow_read(options) is None;


def test_less_follow_detects_truncation(tmp_path):
    from sumbash.applets import _less_follow_read, _less_read_sources;
    p=tmp_path/"app.log";
    p.write_text("first line\nsecond line\n",encoding="utf-8");
    _,options,_,code=_less_read_sources(["+F",str(p)],"",None);
    assert code==0;
    p.write_text("new\n",encoding="utf-8");
    update=_less_follow_read(options);
    assert update==( "replace", "new\n" );



def test_less_short_f_is_force_not_follow(tmp_path):
    from sumbash.applets import _less_read_sources;
    p=tmp_path/"plain.txt";
    p.write_text("one\n",encoding="utf-8");
    _,options,_,code=_less_read_sources(["-f",str(p)],"",None);
    assert code==0;
    assert options["force"] is True;
    assert options["follow"] is False;


def test_less_plain_named_file_can_enter_follow_interactively(tmp_path):
    from sumbash.applets import _less_read_sources;
    p=tmp_path/"app.log";
    p.write_text("one\n",encoding="utf-8");
    _,options,_,code=_less_read_sources([str(p)],"",None);
    assert code==0;
    assert options["follow"] is False;
    assert options["follow_path"]==p;
    assert options["follow_offset"]==p.stat().st_size;


def test_less_plusF_starts_follow(tmp_path):
    from sumbash.applets import _less_read_sources;
    p=tmp_path/"app.log";
    p.write_text("one\n",encoding="utf-8");
    _,options,_,code=_less_read_sources(["+F",str(p)],"",None);
    assert code==0;
    assert options["follow"] is True;
    assert options["follow_path"]==p;

def test_less_log_highlight_and_search_overlay():
    from sumbash.applets import _less_detect_syntax, _less_render_fragment;
    text='[15-Sep-2026 09:47:50] ERROR: 192.168.1.10 failed\n';
    assert _less_detect_syntax("php-fpm.log",text,"auto")=="log";
    rendered=_less_render_fragment(text.rstrip("\n"),"log","ERROR",False);
    assert "\x1b[" in rendered;
    assert "\x1b[7mERROR\x1b[27m" in rendered;


def test_less_log_highlight_http_access_fields():
    from sumbash.applets import _less_log_fragment;
    rendered=_less_log_fragment('192.168.61.17 - - [13/Sep/2026:03:41:04 -0300] "GET /index.php HTTP/1.1" 200 true');
    assert "\x1b[35m192.168.61.17\x1b[0m" in rendered;
    assert "\x1b[1;34mGET\x1b[0m" in rendered;
    assert "\x1b[32m200\x1b[0m" in rendered;
    assert "\x1b[32mtrue\x1b[0m" in rendered;


def test_less_capital_F_keeps_traditional_option_slot(tmp_path):
    from sumbash.applets import _less_read_sources;
    p=tmp_path/"short.txt"; p.write_text("one\n",encoding="utf-8");
    _,options,_,code=_less_read_sources(["-F",str(p)],"",None);
    assert code==0;
    assert options["quit_if_one_screen"] is True;
    assert options["follow"] is False;


def test_less_syntax_never_colors_non_tty_output(tmp_path):
    p=tmp_path/"app.log";
    p.write_text("ERROR failed\n",encoding="utf-8");
    r=run_applet("less",["--syntax=log",str(p)]);
    assert r.code==0;
    assert r.out=="ERROR failed\n";
    assert "\x1b[" not in r.out;


def test_ls_long_columns_align_and_human_sizes_match_gnu_shape(tmp_path):
    small=tmp_path/"small.txt"; small.write_bytes(b"x"*49);
    large=tmp_path/"large.bin"; large.write_bytes(b"x"*4096);
    (tmp_path/"adir").mkdir();
    shell=ShellRuntime(cwd=tmp_path);
    result=run_applet("ls",["-lah"],runtime=shell);
    assert result.code==0;
    lines=result.out.splitlines();
    assert lines[0].startswith("total ");
    assert lines[0].split()[1][-1:] in ("K","M","G","T","P","E") or lines[0].split()[1].isdigit();
    small_line=next(line for line in lines if line.endswith(" small.txt"));
    large_line=next(line for line in lines if line.endswith(" large.bin"));
    # Human-readable sizes use coreutils-like spelling and share a right edge.
    assert "4.0K" in large_line;
    assert "49" in small_line;
    assert large_line.index("4.0K")+len("4.0K")==small_line.index("49")+len("49");


def test_ls_long_raw_size_column_is_right_aligned(tmp_path):
    (tmp_path/"a").write_bytes(b"x"*7);
    (tmp_path/"b").write_bytes(b"x"*1234);
    shell=ShellRuntime(cwd=tmp_path);
    result=run_applet("ls",["-la"],runtime=shell);
    lines=result.out.splitlines();
    a_line=next(line for line in lines if line.endswith(" a"));
    b_line=next(line for line in lines if line.endswith(" b"));
    assert b_line.index("1234")+4==a_line.index("7")+1;


def test_find_printf_emits_scriptable_paths(tmp_path):
    from sumbash.shell import ShellRuntime;
    (tmp_path/'a.txt').write_text('a',encoding='utf-8');
    shell=ShellRuntime(cwd=tmp_path);
    result=shell.run_line(r'''find . -type f -printf "git add %p;\n"''',capture=True);
    assert result.code==0;
    assert 'git add ./a.txt;\n' in result.out;


def test_cat_show_nonprinting_makes_escape_sequences_visible():
    result=run_applet("cat",["-v"],stdin="\x1b[A\x1b[B\x00\x7f\n");
    assert result.code==0;
    assert result.out=="^[[A^[[B^@^?\n";


def test_cat_show_all_and_clustered_options():
    result=run_applet("cat",["-A"],stdin="a\tb\n");
    assert result.code==0;
    assert result.out=="a^Ib$\n";
    clustered=run_applet("cat",["-vET"],stdin="\x1b\t\n");
    assert clustered.out=="^[^I$\n";
