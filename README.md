# sumbash

`sumbash` is SUM's portable shell and BusyBox-style multicall toolbox. The goal is not to clone every historical Bash corner in one step. The goal is to provide a useful, scriptable Unix-style environment with the same maintained implementation on Linux, Windows and Android, while falling back to host commands for specialised tools.

Version `0.1.0a6` is the current concrete vertical alpha.

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

## Shell features in 0.1.0a6

The first alpha implements a useful subset rather than pretending to be complete Bash:

- UTF-8 command lines, single and double quoting, escapes and comments;
- variables and positional parameters;
- `${#name}`, `${name:-default}`, `${name:+word}`, `${name:=word}`, substrings and simple replacements;
- backtick and `$(...)` command substitution;
- `$((...))` fractional arithmetic;
- `;`, `&&`, `||` and internal pipelines;
- `<`, `>`, `>>`, `2>`, `2>>`, `2>&1`;
- `cd`, `export`, SUM `global`, `unset`, `readonly`, `alias`, `unalias`, `source`/`.`, `eval`, `command`, `type`, `history`, `complete`, `compgen`, `compopt`, `read`, `inkey`, `true`, `false`, `exit` and `let`;
- `command -v`, `command -V`, `type` and `type -a` use the same command resolver as execution;
- `shopt -s cdspell`, `histappend`, `checkwinsize` and `globstar` state; `cdspell` is active in this alpha;
- `set -o vi` selects vi editing mode when Python readline provides it;
- Bash-like PS1 escapes for user, host, working directory, time, date, newline and ANSI escape;
- external command fallback through `PATH`; foreground TTY programs inherit the real terminal, so full-screen applications such as `mc`, `vi`, `top` and `ssh` can run interactively instead of being captured;
- GNU-readline command editing when available, including Up/Down history navigation, persistent `~/.sumbash_history`, `HISTSIZE`, `HISTFILESIZE`, `HISTCONTROL` and `history -c/-w/-a`;
- `Ctrl-L` clears/redraws the interactive terminal through readline; `Ctrl-D` at an empty prompt is EOF and exits the shell (equivalent to `exit`/`logout`);
- internal `cat` with terminal standard input reads until EOF, so `cat > archivo` can be completed with `Ctrl-D` like the traditional utility;

The next language alphas are intended to add compound grammar (`if`, `case`, `for`, `while`), functions/local scope, indexed/associative arrays and fuller Bash/ksh compatibility.

## Portable applets in 0.1.0a6

The package currently ships these internal applets:

```text
arch basename cat clear cut date dirname echo egrep fgrep find grep
head hostname ls lsb_release printf pwd realpath rev sed sleep sort
suminfo tail tee test tty uname uniq uptime wc whoami [ [[
```

`ls` reads the standard `LS_COLORS` format. In SUM, an exported `LS_COLORS` enables color automatically when the command output is a terminal. The usual controls are also accepted explicitly:

```text
ls --color=auto
ls --color=always
ls --color=never
```

Automatic color is suppressed in pipelines and file redirections; `--color=always` deliberately forces ANSI sequences through them. Since a4, the portable `ls` surface is substantially expanded: column/row/single/comma/NUL formats, long listings, owner/group and numeric IDs, hidden-file controls, recursive traversal, human/SI sizes, inode and allocated-block display, time-field/time-style selection, name/time/size/version/extension sorting, reverse order, directory grouping, symlink dereferencing controls, indicators, quoting styles, control-character handling, OSC-8 hyperlinks, ignore/hide patterns, and best-effort security-context display. `--dired` is accepted for compatibility, but GNU/Emacs byte-offset metadata is not emitted yet.

`sed` is intentionally limited in this first alpha to the common substitution form `s///[g]` plus `p`. Full `awk`, full `sed`, job control and process substitution are not claimed yet. When a name is not a SUM builtin/applet, `sumbash` looks for an executable in the host `PATH`.

## Terminal scrollback

When `sumbash` runs inside an existing terminal emulator, scrollback is owned by that terminal. `Shift+PgUp` and `Shift+PgDown` are therefore deliberately not intercepted by the shell; terminals such as VTE/xterm can handle them natively. SUM-owned terminal frontends should expose the same keys through `sumTerm`: move the viewport through scrollback and return to the live prompt on the next normal key.

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

## Scope

`sumbash` is a portable toolbox, not a replacement operating system. Host-specific administration such as user management, hardening, package managers, service managers and specialised programs such as OpenSSL, Git or FFmpeg remain host/external commands.

Historical private scripts were used only to identify shell constructs and practical requirements; they are not incorporated as examples or test fixtures.


## Interactive completion (0.1.0a6)

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
