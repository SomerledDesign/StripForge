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


def test_cli_build_in_place_by_default_with_one_backup(tmp_path, capsys, real_board_path):
    board = tmp_path / "fix.kicad_pcb"
    shutil.copy(real_board_path, board)
    (tmp_path / "fix.kicad_dru").write_text("(version 1)\n(rule mine (constraint clearance (min 0.3mm)))\n")
    x56 = real_board_path.parents[1] / "x56.toml"
    original = board.read_bytes()
    assert main(["build", str(board), "--config", str(x56)]) == 1
    text = capsys.readouterr().out
    backup = tmp_path / "fix-pre-stripbuild.kicad_pcb"
    assert f"-> {board} (in place)" in text and "made now, before the first write" in text
    assert backup.read_bytes() == original and b"StripForge:CUT_" in board.read_bytes()
    assert (tmp_path / "fix-stripforge.links.json").is_file()
    assert "rule mine" in (tmp_path / "fix-pre-stripbuild.kicad_dru").read_text()  # their own DRC rules
    assert "SF strip width" in (tmp_path / "fix.kicad_dru").read_text()
    built = board.read_bytes()
    # a rebuild keeps the pristine backup and gives the same board
    assert main(["build", str(board), "--config", str(x56)]) == 1
    assert "kept as it was (not replaced)" in capsys.readouterr().out
    assert backup.read_bytes() == original and board.read_bytes() == built
    # --separate: the old <name>-stripforge.kicad_pcb, board left alone
    assert main(["build", str(backup), "--config", str(x56), "--separate"]) == 1
    assert (tmp_path / "fix-stripforge.kicad_pcb").is_file() and backup.read_bytes() == original
    shutil.rmtree(tmp_path)
