# sumbash Help

Keyboard-first help for the SUM shell. Press F1 or Alt+H while editing a command to open contextual help without losing the command line. Use F3 inside the browser to search topics.

## Getting started

### Overview

sumbash is the portable SUM shell. It combines a Bash-oriented command language, SUM filesystem/stream abstractions, builtins, portable applets and external command fallback.

#### Syntax

```bash
sumbash
sumbash -c 'COMMAND'
sumbash SCRIPT [ARG...]
```

#### Notes

- Interactive commands use GNU readline when available.
- External full-screen programs inherit the controlling terminal.
- SUM applets are used before external fallback when the command name matches an applet.

#### Functional example

```bash
printf 'b\na\na\n' | sort | uniq -c
```

#### See also

Keyboard, Help, Pipelines, Redirection, Command resolution

#### Aliases

shell, start

### Keyboard

Interactive editing, completion and help shortcuts.

#### Syntax

```text
Tab       complete commands, paths and variables
F1        contextual help
Alt+H     contextual help
Ctrl+L    clear/redraw the command line
Ctrl+D    end the interactive shell on an empty command line
```

#### Notes

- F1 and Alt+H preserve the line being edited and restore the cursor position when GNU readline exposes it.
- Completion preserves the real case of filesystem names.

#### Functional example

```text
Type:  cd ../Vir
Press: Tab
```

#### See also

Help, Completion, History

#### Aliases

keys, shortcuts

### Help

Open textual or interactive help.

#### Syntax

```bash
help
help TOPIC
help -i
help -i TOPIC
```

#### Notes

- F1 and Alt+H open the same interactive browser contextually.
- F1 inside the browser returns to Contents.
- F2 focuses the topic list, F3 focuses Search, F4 focuses the topic text and Esc closes Help.

#### Functional example

```bash
help cd
help -i grep
```

#### See also

Keyboard, Command index

#### Aliases

?, helpbrowser

### Command index

Builtins and portable applets currently exposed by sumbash.

#### Syntax

```text
Builtins: alias cd command complete compgen compopt declare eval exit export global help history inkey let local logout read readonly return set shift shopt source trap type umask unalias unset
Applets: arch basename cat clear cut date df dirname echo egrep fgrep find grep head hostname less ls lsb_release printf pwd realpath rev sed sleep sort suminfo tail tee test tty uname uniq uptime wc whoami
```

#### Notes

- `type NAME` reports how a command resolves.
- `command -V NAME` gives a verbose resolution description.

#### Functional example

```bash
type ls
command -V grep
```

#### See also

Command resolution, Applets

#### Aliases

commands, builtins

## Navigation and shell state

### CD

Change the current directory.

#### Syntax

```bash
cd [-L|-P] [DIR]
cd [-L|-P] -
```

#### Notes

- Without DIR, CD uses HOME.
- `cd` uses logical (`-L`) navigation by default and preserves symlink components in PWD.
- `cd -P` resolves symlinks and uses the physical filesystem path.
- `cd -` returns to OLDPWD and prints the resulting logical directory.
- `shopt -s cdspell` enables a conservative spelling correction when exactly one close directory match exists.

#### Functional example

```bash
cd projects
cd -
```

#### See also

PWD, SHOPT

#### Aliases

chdir

### PWD

Print the current working directory.

#### Syntax

```bash
pwd
pwd -L
pwd -P
```

#### Notes

- `pwd` and `pwd -L` print the logical path preserved by the shell.
- `pwd -P` prints the resolved physical filesystem path.
- Logical path handling is provided through sumFSA.

#### Functional example

```bash
pwd -P
```

#### See also

CD

### ALIAS

Create or display shell aliases.

#### Syntax

```bash
alias
alias NAME='VALUE'
alias NAME
unalias NAME
```

#### Notes

- Exact aliases have precedence during command resolution.
- Trailing spaces in historical aliases are preserved.

#### Functional example

```bash
alias DIR='ls -h '
alias DIR
```

#### See also

Command resolution, SOURCE

#### Aliases

aliases

### SHOPT

Enable, disable or query shell options.

#### Syntax

```bash
shopt
shopt -s OPTION
shopt -u OPTION
shopt -q OPTION
```

#### Notes

- Unknown option names are rejected rather than silently accepted.

#### Functional example

```bash
shopt -s cdspell
shopt -q cdspell
```

#### See also

CD, SET

#### Aliases

options

### History

Display or manage interactive command history.

#### Syntax

```bash
history [N]
history -c
history -w
history -a
```

#### Notes

- HISTCONTROL and HISTSIZE are honored by the interactive shell.
- Infrastructure commands, prompt substitutions and sourced lines are not inserted as separate interactive history entries.

#### Functional example

```bash
history 20
```

#### See also

Keyboard

### Completion

Native command, path and variable completion.

#### Syntax

```text
Tab completes the word at the cursor.
```

#### Notes

- Escaped spaces remain part of the same logical word.
- Directory symlinks are not forced to end in `/` for destructive commands such as rm or unlink.
- CD completion can append `/` because it is navigating into the directory target.

#### Functional example

```text
ls ../VirtualBox\ V<Tab>
```

#### See also

Keyboard, CD

## Shell language

### Pipelines

Connect stdout of one command to stdin of the next.

#### Syntax

```bash
COMMAND1 | COMMAND2 [| COMMAND3 ...]
```

#### Notes

- Text applets and external commands can participate in pipelines.
- Binary external output is preserved when captured or redirected.

#### Functional example

```bash
alias | grep rsync
```

#### See also

Redirection

### Redirection

Redirect command or compound-command input/output.

#### Syntax

```bash
COMMAND > FILE
COMMAND >> FILE
COMMAND 2> FILE
COMMAND < FILE
```

#### Notes

- Redirections attached to compound closers apply to the whole compound command.
- Binary stdout is preserved for external pipelines and files.

#### Functional example

```bash
if true; then echo ok; fi > result.txt
```

#### See also

Pipelines

### Command resolution

Resolve aliases, functions, builtins, SUM applets and external commands.

#### Syntax

```bash
type NAME
command -v NAME
command -V NAME
```

#### Notes

- Resolution prefers exact aliases/functions/builtins/applets before external fallback.

#### Functional example

```bash
type grep
command -V ls
```

#### See also

Command index, ALIAS

## Portable applets

### Applets

Portable commands implemented inside sumbash.

#### Syntax

```text
arch basename cat clear cut date df dirname echo egrep fgrep find grep head hostname less ls lsb_release printf pwd realpath rev sed sleep sort suminfo tail tee test tty uname uniq uptime wc whoami
```

#### Notes

- Applets aim for practical GNU/BSD compatibility while remaining portable.
- Unsupported options should fail explicitly instead of being reinterpreted as operands.

#### Functional example

```bash
df -h
```

#### See also

Command index, GREP, DF, LS, LESS

### GREP

Search text for matching lines.

#### Syntax

```bash
grep [OPTION]... PATTERN [FILE]...
grep -E PATTERN [FILE]...
grep -F STRING [FILE]...
```

#### Notes

- Supports practical options including -i, -v, -n, -q, -r/-R, -E, -F, -e, -A/-B/-C and --color=auto|always|never.
- egrep and fgrep select extended-regexp and fixed-string behavior.

#### Functional example

```bash
alias | grep --color=auto rsync
```

#### See also

Pipelines, LESS

#### Aliases

egrep, fgrep

### DF

Report filesystem space usage.

#### Syntax

```bash
df [-h] [-a] [-T]
```

#### Notes

- `-h` prints human-readable sizes.
- Normal output suppresses noisy pseudo-filesystems such as common loop/squashfs mounts; `-a` requests all entries.

#### Functional example

```bash
df -h
```

#### See also

Applets

### LS

List directory contents.

#### Syntax

```bash
ls [OPTION]... [FILE]...
```

#### Notes

- Multiple operands and escaped spaces follow shell tokenization rules.
- Use `ls --help` for the current portable option set.

#### Functional example

```bash
ls ../VirtualBox ../VirtualBox\ VMs/
```

#### See also

Completion, Applets

### LESS

Interactive pager implemented by sumbash.

#### Syntax

```bash
less [OPTION]... [FILE]...
```

#### Notes

- `less --help` shows pager-specific keys and options.
- ANSI-preserving workflows can be used for colorized command output where supported.

#### Functional example

```bash
grep --color=always -n TODO *.py | less -R
```

#### See also

GREP, Applets

### RM

Remove files and links.

#### Syntax

```bash
rm FILE...
rm -r DIRECTORY...
```

#### Notes

- A symbolic link to a directory is removed as a link when the operand names the link itself.
- Completion deliberately avoids appending `/` to a directory symlink for rm/unlink.

#### Functional example

```bash
rm old-link
```

#### See also

Completion
