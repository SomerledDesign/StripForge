"""Applying the best-fit shift (M2 part A): `analyze.apply_best_fit` and `stripforge snap`."""

from kicad_text import fp, pad, pcb
from stripforge import config
from stripforge.analyze import analyze, apply_best_fit, best_fit_moves
from stripforge.board import load_board
from stripforge.cli import main


def test_best_fit_on_the_fixture(tpi_board_path):
    a = analyze(tpi_board_path)
    after, moves = apply_best_fit(a)
    assert moves == {"C3": (20_000, 0), "C2": (20_000, 0), "C1": (20_000, 0), "F1": (0, -5_000)}
    devs = {s.ref: s.max_dev_nm for s in after.snaps}
    assert devs["C1"] == devs["C2"] == devs["C3"] == 20_000 and devs["F1"] == 5_000
    assert all(s.shift_nm == (0, 0) for s in after.snaps)  # nothing left to move
    assert best_fit_moves(after) == {}
    assert [c.__dict__ for c in after.split.cuts] == [c.__dict__ for c in analyze(tpi_board_path).split.cuts]
    assert after.board.footprint("C1").x_nm == 56_370_000


def test_off_pitch_part_moves_onto_its_holes(tmp_path):
    path = tmp_path / "b.kicad_pcb"
    path.write_text(pcb(fp("R1", "1.37 1.20", pad("1", "0 0", "A"), pad("2", "7.62 0", "B"))))
    after, moves = apply_best_fit(analyze(path))
    assert moves == {"R1": (-100_000, 70_000)}
    assert after.snaps[0].max_dev_nm == 0


def test_slotted_and_rejected_parts_stay_put(tmp_path):
    path = tmp_path / "b.kicad_pcb"
    path.write_text(
        pcb(
            fp("BT1", "1.27 3.81", pad("1", "0.3175 0.05", "V"), pad("2", "5.08 0.05", "G")),
            fp("U1", "13.97 6.35", pad("1", "0 0", "A"), pad("2", "0.6 0", "B")),
            outline=(0, 0, 25.4, 12.7),
        )
    )
    a = analyze(path, config.from_dict({"slotted": ["BT1"]}))
    assert [s.ref for s in a.rejected] == ["U1"] and a.snaps[0].shift_nm != (0, 0)
    assert best_fit_moves(a) == {}


def test_cli_snap_writes_only_the_moves(tpi_board_path, tmp_path, capsys):
    out = tmp_path / "snapped.kicad_pcb"
    assert main(["snap", str(tpi_board_path), "-o", str(out)]) == 0
    text = capsys.readouterr().out
    assert "Best-fit moves: 4 footprint(s)" in text
    assert "C1    moved (+0.020, +0.000) mm; worst pad offset 0.040 -> 0.020 mm [L3, L4]" in text
    src = tpi_board_path.read_text().splitlines()
    new = out.read_text().splitlines()
    changed = [(a, b) for a, b in zip(src, new, strict=True) if a != b]
    assert changed == [
        ("\t\t(at 63.97 102.07)", "\t\t(at 63.99 102.07)"),
        ("\t\t(at 53.81 102.07)", "\t\t(at 53.83 102.07)"),
        ("\t\t(at 56.35 79.21)", "\t\t(at 56.37 79.21)"),
        ("\t\t(at 112.23 53.81)", "\t\t(at 112.23 53.805)"),
    ]
    # re-running on the output moves nothing and rewrites it byte for byte
    again = tmp_path / "again.kicad_pcb"
    assert main(["snap", str(out), "-o", str(again)]) == 0
    assert "nothing to move" in capsys.readouterr().out
    assert again.read_bytes() == out.read_bytes()
    assert load_board(again).footprint("F1").y_nm == 53_805_000


def test_cli_snap_dry_run_and_refuses_to_overwrite(tpi_board_path, capsys):
    assert main(["snap", str(tpi_board_path)]) == 0
    assert "Dry run" in capsys.readouterr().out
    assert main(["snap", str(tpi_board_path), "-o", str(tpi_board_path)]) == 2
    assert "must name a new file" in capsys.readouterr().err
