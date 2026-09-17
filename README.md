# sumbash

`sumbash` is SUM's portable shell and BusyBox-style multicall toolbox. The goal is not to clone every historical Bash corner in one step. The goal is to provide a useful, scriptable Unix-style environment with the same maintained implementation on Linux, Windows and Android, while falling back to host commands for specialised tools.

Version `0.1.0a20` is the current concrete vertical alpha.

Interactive startup now loads `~/.sumbashrc` and then `~/.autoexec` when
those files exist.  `SUMBASH_STARTUP` may be set to an `os.pathsep`-separated
list of explicit startup files instead.  Startup files are sourced into the
current shell, so aliases, exported variables, options and `PS1` changes
remain active at the prompt.

## Invocation

```text
sumbash
sumbash -c 'echo $((5.5*2))'
sumbash script.sh arg1 arg2
sumbash find . -iname '*.bas'
```

The same executable can be invoked through multicall names. On POSIX systems:

```text
sumbash --install-links ~/.local/sum/bin
PATH="$HOME/.local/sum/bin:$PATH"

ls -la
find . -iname '*.bas'
grep error logfile
```

`--link-mode auto|symlink|hardlink|copy` controls how names are installed. `auto` tries an appropriate sequence for the host.

## Native SUM arithmetic

Unlike Bash integer arithmetic, SUM arithmetic preserves fractions:

```text
$ echo $((5.5 * 2))
11

$ echo $((5 / 2))
2.5

$ echo $((5 // 2))
2
```

The initial math functions are:

```text
abs(x)
int(x)
round(x[, n])
floor(x)
ceil(x)
sqrt(x)
pow(x, y)
```

`int()` truncates toward zero. `round()` rounds half values away from zero. Bitwise operators require integer operands instead of silently truncating fractional values.

## History semantics in 0.1.0a15

History records only command lines accepted at the interactive prompt. Commands executed while expanding `PS1`, command substitutions, sourced files, script files and nested `eval` execution are not separate history entries. `HISTCONTROL=ignorespace` and `HISTCONTROL=ignoreboth` suppress a user command line whose first character is a space; `ignoredups`/`ignoreboth` suppress adjacent duplicates. SUM defaults `HISTCONTROL` to `ignoreboth` when the parent environment does not provide it, matching the common Ubuntu interactive-shell convention. Python readline auto-history is disabled when the binding supports it so the shell remains the single owner of history policy.

## Shell features in 0.1.0a15

The first alpha implements a useful subset rather than pretending to be complete Bash:

- UTF-8 command lines, single and double quoting, escapes and comments;
- variables and positional parameters;
- `${#name}`, `${name:-default}`, `${name:+word}`, `${name:=word}`, substrings, simple replacements, and `#`/`##`/`%`/`%%` prefix/suffix pattern removal;
- indexed arrays (`NAME=(...)`, `${NAME[0]}`, `${#NAME[@]}`), associative arrays through `declare -A`, and `for name in ...; do ...; done` loops, including the common `for x in "${NAME[@]}"` form used by SUM maintenance scripts;
- unquoted expansion field splitting using `IFS`, followed by pathname expansion, while assignment values remain unsplit;
- backtick and `$(...)` command substitution;
- quote-aware pathname expansion (`*`, `?`, and bracket patterns) against the shell logical current directory; unmatched patterns remain literal, while quoted/escaped metacharacters do not expand;
- `$((...))` fractional arithmetic;
- `;`, `&&`, `||` and internal pipelines; captured external stdout is byte-preserving between external stages and file redirections, so binary pipelines such as `tar ... | gzip > archive.tar.gz` work without UTF-8 corruption;
- `<`, `>`, `>>`, `2>`, `2>>`, `2>&1`;
- `cd`, `export`, SUM `global`, `unset`, `readonly`, `local`, `declare`, `alias`, `unalias`, `source`/`.`, `eval`, `command`, `type`, `history`, `complete`, `compgen`, `compopt`, `read`, `inkey`, `shift`, `trap`, `umask`, `true`, `false`, `return`, `break`, `continue`, `exit` and `let`;
- `command -v`, `command -V`, `type` and `type -a` use the same command resolver as execution;
- `shopt -s cdspell`, `histappend`, `checkwinsize` and `globstar` state; `cdspell` is active in this alpha;
- `set -o vi` selects vi editing mode when Python readline provides it; `set +/-e` and `set +/-u` are accepted as compatibility state, although complete Bash errexit/nounset semantics are not yet claimed;
- Bash-like PS1 escapes for user, host, working directory, time, date, newline and ANSI escape;
- external command fallback through `PATH`; foreground TTY programs inherit the real terminal, so full-screen applications such as `mc`, `vi`, `top` and `ssh` can run interactively instead of being captured;
- GNU-readline command editing when available, including Up/Down history navigation, persistent `~/.sumbash_history`, `HISTSIZE`, `HISTFILESIZE`, `HISTCONTROL` and `history -c/-w/-a`;
- `Ctrl-L` clears/redraws the interactive terminal through readline; `Ctrl-D` at an empty prompt is EOF and exits the shell (equivalent to `exit`/`logout`);
- internal `cat` with terminal standard input reads until EOF, so `cat > archivo` can be completed with `Ctrl-D` like the traditional utility;

Alpha a14 adds the compound grammar needed by the current SUM maintenance scripts: `if`/`elif`/`else`, `case`, `while`/`until`, shell functions, `local`, `return`, `break`/`continue`, associative arrays, quoted heredocs and braced output groups. Function-local positional parameters and locals are restored on return, and sourcing a file without explicit arguments preserves the caller's positional parameters. Remaining work includes complete Bash option/error semantics, richer `[[ ... ]]`, subshell/job control, full `getopts`, process substitution and other advanced grammar.

## Portable applets in 0.1.0a15

Relative filesystem operands are resolved against the logical shell current directory (`runtime.cwd`), so internal applets remain coherent after `cd` without changing the Python host process working directory.

The package currently ships these internal applets:

```text
arch basename cat clear cut date dirname echo egrep fgrep find grep
head hostname less ls lsb_release printf pwd realpath rev sed sleep sort
suminfo tail tee test tty uname uniq uptime wc whoami [ [[
```

`ls` reads the standard `LS_COLORS` format. In SUM, an exported `LS_COLORS` enables color automatically when the command output is a terminal. The usual controls are also accepted explicitly:

```text
ls --color=auto
ls --color=always
ls --color=never
```

Automatic color is suppressed in pipelines and file redirections; `--color=always` deliberately forces ANSI sequences through them. Since a4, the portable `ls` surface is substantially expanded: column/row/single/comma/NUL formats, long listings, owner/group and numeric IDs, hidden-file controls, recursive traversal, human/SI sizes, inode and allocated-block display, time-field/time-style selection, name/time/size/version/extension sorting, reverse order, directory grouping, symlink dereferencing controls, indicators, quoting styles, control-character handling, OSC-8 hyperlinks, ignore/hide patterns, and best-effort security-context display. `--dired` is accepted for compatibility, but GNU/Emacs byte-offset metadata is not emitted yet.

Alpha a11 formats long listings in a GNU/coreutils-like table: link counts and numeric fields are right-aligned, owner/group fields are column-aligned, file sizes share a right edge, and `-h` uses familiar forms such as `49`, `4.0K`, `67K`, and a human-readable `total` line.

Alpha a12 moves wildcard handling to the shell layer, where it belongs. Unquoted pathname patterns are expanded before dispatch to either SUM applets or host executables, so commands such as `ls *sh`, `cat *.log`, and `wc src/*.py` see the same expanded argument list. Single/double quoted or backslash-escaped wildcard characters remain literal. Redirection targets are expanded too and report an ambiguous redirect when more than one pathname matches.

Alpha a13 adds the script-compatibility slice discovered by running SUM’s own maintenance scripts: indexed array assignment, practical `for ... in ...; do ...; done`, unquoted `IFS` field splitting before globbing, byte-preserving external pipeline capture/redirection, and `find -printf` (including `%p`) so constructs such as `find . -type f -printf "git add %p;\n" | sh` work. The current `makeTarGz.sh` pattern with `PACKAGES=(...)`, a generated `$packs` list and `tar | gzip > ...` is an explicit regression target.

Alpha a14 promotes the five current SUM operational scripts (`makeTarGz.sh`, `borraVersiones.sh`, `mobileBorra.sh`, `summobile.sh`, and `sum.sh`) to an explicit compatibility suite. They are exercised against disposable package trees with `pip`/`git` side effects stubbed; archive generation is verified with real `tar`/`gzip`, deletion scripts are checked for their expected filesystem effects, and `sum.sh` is required to produce its Markdown version report. Debian/Ubuntu init scripts supplied during development are also exercised through a non-destructive dispatch path, but starting/stopping host services is intentionally outside the package test suite.

`sed` is intentionally limited in this first alpha to the common substitution form `s///[g]` plus `p`. Full `awk`, full `sed`, job control and process substitution are not claimed yet. When a name is not a SUM builtin/applet, `sumbash` looks for an executable in the host `PATH`.


## Interactive pager (`less`)

`sumbash` includes a portable interactive `less` applet. When its output is a terminal it enters a full-screen pager; when stdout is redirected or piped onward it emits the original input unchanged. Navigation supports Up/Down or `j`/`k`, PgUp/PgDn, Space, `b`, half-page `d`/`u`, `g`/`G`, forward/backward regular-expression search with `/` and `?`, repeat search with `n`/`N`, `Ctrl-L` redraw, and `q` to quit. `-N`, `-S`, `-i`, `-X`, `+G` and `+/PATTERN` are supported. With `-S`, Left/Right scroll horizontally.

Alpha a10 adds viewport-only syntax/semantic highlighting. `--syntax=auto` is the default; `--syntax=log`, `--syntax=python`, `--syntax=bash`, `--syntax=json`, and other Pygments lexer names may be selected explicitly, while `--no-syntax`/`--syntax=none` disables highlighting. Log highlighting is built into SUM and marks common timestamps, levels (`NOTICE`, `WARNING`, `ERROR`, etc.), IP addresses, HTTP methods/status codes, success/failure words and boolean/null values. Source-code highlighting uses Pygments opportunistically when available, but Pygments is not required for the pager to function. Search highlighting is rendered above syntax colors, and neither syntax nor search ANSI sequences alter the underlying text used for navigation and matching.

A10 also adds follow mode for named files:

```text
less +F app.log
less --follow app.log
```

`F` enters follow mode from an ordinary pager session and jumps to the end; `Ctrl-C` stops following without leaving `less`, so the user can scroll backward or search, and `F` resumes following. Appended data is read incrementally rather than re-reading the complete file. Truncation or replacement of the followed file is detected and the view is reopened from the new contents. Follow currently requires one named file; SUM's in-memory alpha pipeline model cannot yet stream `tail -f ... | less` continuously.

`-f` / `--force` keeps traditional less semantics for opening non-regular files. `-F` / `--quit-if-one-screen` is also reserved for the traditional less behavior. SUM exposes follow at startup as `+F` or the explicit `--follow` extension; interactive `F` resumes follow mode.

The pager engine remains inside SUM rather than requiring the host `less`, keeping the same useful surface available on Linux, Windows and Android terminals. A later release can move this engine behind `sumdoc`/`sumTerm` without changing the command.

## Terminal scrollback

When `sumbash` runs inside an existing terminal emulator, scrollback is owned by that terminal. `Shift+PgUp` and `Shift+PgDown` are therefore deliberately not intercepted by the shell; terminals such as VTE/xterm can handle them natively. SUM-owned terminal frontends should expose the same keys through `sumTerm`: move the viewport through scrollback and return to the live prompt on the next normal key.

## Shell identity

`sumbash` preserves the inherited `SHELL` variable because Unix programs commonly interpret it as the user's preferred/login shell. The running SUM interpreter is exported separately as `SUM_SHELL`, with `SUM_SHELL_VERSION` identifying its package version. This avoids misleading host applications while still giving SUM-aware tools a reliable interpreter identity.

## SUM services

System identity is obtained from `sumcore`/`suminfo`, using a portable vocabulary for Linux, Windows and Android. Applets such as `uname`, `lsb_release`, `hostname`, `arch` and `uptime` are views over that common information rather than independent probes.

Examples:

```text
suminfo --short
suminfo --field os.distributor
suminfo --field os.release
suminfo --field kernel.release
suminfo --field machine.architecture

uname -a
lsb_release -a
uptime -p
pwd -P
```

`pwd -P` and `realpath` use canonical paths. A later SUM filesystem backend can map the same semantics onto Android SAF logical paths without exposing `content://` URIs to scripts.

## sumFSA / sumIO integration

The a15 shell begins consuming the shared storage/I/O layers instead of owning every path and redirection detail itself.

- shell cwd now has both native and SUM-logical representations;
- `cd`, `pwd`, `realpath` and internal file applets resolve through `sumFSA` where applicable;
- input/output/error redirections are opened through `sumIO`;
- `df` renders the common `sumFSA` volume model;
- external processes still receive a native cwd, so host programs keep normal platform semantics.

This is intentionally the first migration slice, not a claim that every shell filesystem operation has already moved behind sumFSA.

## Compound-command redirection (0.1.0a19)

Redirections attached to a multiline compound-command closing keyword apply to the compound command as a whole, not only to the final command in its body.  This includes `if`/`fi`, `for`/`done`, `while`/`done`, `until`/`done`, `case`/`esac`, and standalone `{ ... }` groups.

Examples:

```bash
if check_something; then
    echo normal-output
    command_that_may_warn
fi 2>/dev/null 1>/dev/ttyS0

for file in *.log; do
    process "$file"
done >>run.log 2>&1
```

`1>`, `1>>`, `2>`, `2>>`, `2>&1`, and `1>&2` are interpreted as descriptors of the compound command. Descriptor duplication is resolved left-to-right, so `>file 2>&1` sends both captured streams to the same target. Compound redirection is routed through `sumIO`/`sumFSA`, so ordinary logical paths and POSIX device paths use the same resource layer as simple-command redirection.


## Scope

`sumbash` is a portable toolbox, not a replacement operating system. Host-specific administration such as user management, hardening, package managers, service managers and specialised programs such as OpenSSL, Git or FFmpeg remain host/external commands.

Historical private scripts were used only to identify shell constructs and practical requirements; they are not incorporated as examples or test fixtures.


## Interactive completion (0.1.0a15)

When Python is linked with GNU readline, `TAB` completes commands from aliases,
builtins, SUM applets and `PATH`, and completes filesystem names for arguments.
Directories receive a trailing slash; `cd` filters to directories; `$NAME` and
`${NAME}` complete shell variables.  Readline keeps the traditional ambiguous
completion behaviour: the first TAB attempts completion/common-prefix expansion
and a repeated TAB displays the alternatives.

A first portable subset of Bash programmable completion is also present:
`complete` supports `-A`, `-W`, `-f`, `-d`, `-c`, `-o`, `-P`, `-S`, `-X`, `-p`
and `-r`; `compgen` uses the same generators.  `compopt` is present as a probe
but function-scoped completion options wait for shell-function support.

<p align=center><b>- oOo -</b></p>
