"""Uppercase nomes atuais de cadastros e catálogos do PWA."""

from name_normalization import normalize_current_names

VERSION = "0009"
NAME = "uppercase_current_names"


def upgrade(connection):
    normalize_current_names(connection)
