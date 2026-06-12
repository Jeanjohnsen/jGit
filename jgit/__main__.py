"""Lets jgit run without being installed:  python -m jgit <command>"""

from . import cli

if __name__ == "__main__":
    cli.main()
