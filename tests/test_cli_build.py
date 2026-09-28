import json
import shutil

from stripforge.cli import main


def test_cli_build_and_json(tmp_path, capsys, real_board_path, real_netlist_path):
    out = tmp_path / "b.kicad_pcb"
    x56 = real_board_path.parents[1] / "x56.toml"
    code = main(["build", str(real_board_path), "--netlist", str(real_netlist_path), "--config", str(x56),
                 "-o", str(out)])  # fmt: skip
    text = capsys.readouterr().out
    assert code == 1  # pass 1: links still to add, some nets unlinkable
    assert "Links: 35 proposed for 39 needed (29 joins" in text and "Pass 1: add W1..W35" in text
    assert json.loads((tmp_path / "b-stripforge.links.json").read_text())["links_needed"] == 39
    assert main(["build", str(out), "-o", str(out)]) == 2
    shutil.rmtree(tmp_path)


def test_cli_build_in_place_by_default_with_rotating_backups(tmp_path, capsys, real_board_path):
    board = tmp_path / "fix.kicad_pcb"
    shutil.copy(real_board_path, board)
    (tmp_path / "fix.kicad_dru").write_text("(version 1)\n(rule mine (constraint clearance (min 0.3mm)))\n")
    x56 = real_board_path.parents[1] / "x56.toml"
    original = board.read_bytes()
    assert main(["build", str(board), "--config", str(x56)]) == 1
    text = capsys.readouterr().out
    backup = tmp_path / "fix-pre-stripbuild.kicad_pcb"
    assert f"-> {board} (in place)" in text and "the board just before this build" in text
    assert "moved up" not in text and "To undo this build: delete fix.kicad_pcb" in text
    assert backup.read_bytes() == original and b"StripForge:CUT_" in board.read_bytes()
    assert (tmp_path / "fix-stripforge.links.json").is_file()
    assert "rule mine" in (tmp_path / "fix-pre-stripbuild.kicad_dru").read_text()  # their own DRC rules
    assert "SF strip width" in (tmp_path / "fix.kicad_dru").read_text()
    built = board.read_bytes()
    # a rebuild rotates: the unnumbered backup is the board just before it, -1 the original
    assert main(["build", str(board), "--config", str(x56)]) == 1
    assert "Older backups moved up one: fix-pre-stripbuild-1.kicad_pcb." in capsys.readouterr().out
    assert backup.read_bytes() == built and board.read_bytes() == built
    first = tmp_path / "fix-pre-stripbuild-1.kicad_pcb"
    assert first.read_bytes() == original
    # --separate: the old <name>-stripforge.kicad_pcb, board left alone
    assert main(["build", str(first), "--config", str(x56), "--separate"]) == 1
    assert (tmp_path / "fix-stripforge.kicad_pcb").is_file() and first.read_bytes() == original
    shutil.rmtree(tmp_path)


def test_cli_build_refuses_when_the_backups_cannot_rotate(tmp_path, capsys, real_board_path):
    board = tmp_path / "fix.kicad_pcb"
    shutil.copy(real_board_path, board)
    original = board.read_bytes()
    (tmp_path / "fix-pre-stripbuild.kicad_pcb").write_text("older")
    (tmp_path / "fix-pre-stripbuild-1.kicad_pcb").mkdir()  # in the way, and not a backup file
    x56 = real_board_path.parents[1] / "x56.toml"
    assert main(["build", str(board), "--config", str(x56)]) == 2
    assert "fix-pre-stripbuild-1.kicad_pcb already exists; not overwriting" in capsys.readouterr().err
    assert (
        board.read_bytes() == original and (tmp_path / "fix-pre-stripbuild.kicad_pcb").read_text() == "older"
    )
    assert not (tmp_path / "fix.kicad_dru").exists()
    shutil.rmtree(tmp_path)
