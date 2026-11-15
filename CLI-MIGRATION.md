# SUM CLI migration — sumbash 0.2.0a7

The command-line entry points in this local development snapshot adopt the
`sumui.cli.SumArgumentParser` contract where appropriate, retaining historical
arguments and specialized behavior. The `sumcore` package is the architectural
exception: its own executable uses `sys.argv` and its auxiliary programs stay
independent of `sumUI`.

This is a **local development build**, not a published release. Verify normal
runtime behavior in the target environment before promoting to the repository.

<p align=center><b>- oOo -</b></p>
