"""Plain INI config, gitconfig-style.

Two files, repo wins over user:
  ~/.jgitconfig      user-wide  (aliases you want everywhere)
  .jgit/config       per-repo   (overrides for one project)
"""

import configparser
import os

from . import data


def user_path():
    return os.path.expanduser("~/.jgitconfig")


def repo_path():
    return f"{data.GIT_DIR}/config"


def _read(path):
    parser = configparser.ConfigParser()
    parser.optionxform = str  # keep key case as written
    if os.path.isfile(path):
        try:
            parser.read(path, encoding="utf-8")
        except (configparser.Error, UnicodeDecodeError):
            # A real git config (or any malformed file) sharing the .git
            # directory must never crash jgit. Treat it as having no aliases.
            return configparser.ConfigParser()
    return parser


def section(path, name):
    parser = _read(path)
    if parser.has_section(name):
        return dict(parser.items(name))
    return {}


def get(section_name, key, default=None):
    for path in (repo_path(), user_path()):
        value = section(path, section_name).get(key)
        if value is not None:
            return value
    return default


def set_value(path, section_name, key, value):
    parser = _read(path)
    if not parser.has_section(section_name):
        parser.add_section(section_name)
    parser.set(section_name, key, value)
    with open(path, "w", encoding="utf-8") as f:
        parser.write(f)


def unset_value(path, section_name, key):
    parser = _read(path)
    if not parser.has_section(section_name) or not parser.has_option(section_name, key):
        return False
    parser.remove_option(section_name, key)
    with open(path, "w", encoding="utf-8") as f:
        parser.write(f)
    return True
