"""Live-board backend via kicad-python (kipy) IPC. Stub (M3).

Allowed calls must work on KiCad 10.0.4: get_footprints, get_pads, update_items, create_items
(Track, BoardSegment, BoardText), begin_commit/push_commit, save, get_connected_items (10.0.1+).
NOT available on 10.0.4: flip_items, get_layer_by_name (10.0.6); place_footprint_from_library,
custom-rules and export jobs (KiCad 11); no DRC run at all.
"""


class IpcBackend:
    def __init__(self):
        raise NotImplementedError
