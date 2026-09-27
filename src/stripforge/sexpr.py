"""Minimal lossless S-expression reader/writer for .kicad_pcb round-trips. Stub.

Must preserve unknown nodes and formatting closely enough that KiCad 10.0.4 reopens the file
without an upgrade prompt (Sketch.md §4.7, [UNVERIFIED] items in M0).
"""


def loads(text: str):
    raise NotImplementedError


def dumps(tree) -> str:
    raise NotImplementedError
