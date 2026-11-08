"""
Double-click this to start Forensic Media Extractor, or run it with a tool
name to use any part of it from a command line.

    python launch.py                           the browser interface
    python launch.py -o D:\\cases               the interface, results on D:
    python launch.py store --verify --store D:\\hashes\\store.db
    python launch.py store --inspect VIC_US.json
    python launch.py extract -i case.E01 -o D:\\out --vic DC26-253
    python launch.py doctor                    what is installed and where

It lives at the top of the source tree so that both work from any directory
without installing anything: a double-clicked file runs with its own folder as
the working directory, which is also where results should land, and running
it from elsewhere puts the package on the path rather than requiring
`python -m fme` from the project root.
"""

import multiprocessing
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main() -> int:
    from fme.cli import TOOLS, main as cli_main

    args = sys.argv[1:]
    # "--help" on the launcher should list the tools, not the interface's
    # options; "gui --help" is how to ask for those.
    wants_tool = bool(args) and (args[0] in TOOLS
                                 or args[0] in ("-h", "--help", "help"))

    if not wants_tool:
        # No tool named: start the interface, with any options passed through.
        # Results land beside this file rather than wherever the shell was.
        os.chdir(HERE)
        from fme.web import media_gui
        sys.argv = ["media_gui.py"] + args
        media_gui.main()
        return 0

    # A tool was named: leave the working directory alone, because its
    # arguments are probably relative to where the examiner is standing.
    return cli_main()


if __name__ == "__main__":
    # Parallel carving starts workers by re-running this file; without the
    # guard each one would open another browser window.
    multiprocessing.freeze_support()
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        os._exit(0)
