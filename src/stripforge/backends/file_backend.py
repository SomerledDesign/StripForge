"""Headless .kicad_pcb backend via sexpr round-trip (v0 default). Stub."""


class FileBackend:
    def __init__(self, path: str):
        self.path = path
