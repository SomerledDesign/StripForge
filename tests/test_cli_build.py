import json
import shutil

from conftest import X56_DIAGONALS
from stripforge.cli import main


def test_cli_build_and_json(tmp_path, capsys, real_board_path, real_netlist_path):
    out = tmp_path / "b.kicad_pcb"
    x56 = X56_DIAGONALS
    code = main(["build", str(real_board_path), "--netlist", str(real_netlist_path), "--config", str(x56),
                 "-o", str(out), "--no-place-links"])  # fmt: skip
    text = capsys.readouterr().out
    assert code == 1  # pass 1: links still to add, some nets unlinkable
    assert "Links: 40 proposed for 39 needed (32 joins" in text and "Pass 1: add W1..W40" in text
    assert json.loads((tmp_path / "stripforge" / "b-stripforge.links.json").read_text())["links_needed"] == 39
    assert main(["build", str(out), "-o", str(out)]) == 2
    shutil.rmtree(tmp_path)


def test_cli_build_in_place_with_save_points_and_restore(tmp_path, capsys, real_board_path):
    from stripforge import layout

    board = tmp_path / "fix.kicad_pcb"
    shutil.copy(real_board_path, board)
    (tmp_path / "fix.kicad_pro").write_text("{}\n")
    (tmp_path / "fix.kicad_dru").write_text("(version 1)\n(rule mine (constraint clearance (min 0.3mm)))\n")
    (tmp_path / "fix-pre-stripbuild.kicad_pcb").write_text("a 0.2.x backup")
    x56 = real_board_path.parents[1] / "x56.toml"
    original = board.read_bytes()
    assert main(["build", str(board), "--config", str(x56)]) == 1
    text = capsys.readouterr().out
    backups = tmp_path / "stripforge" / "fix-stripforge-backups"
    assert f"-> {board} (in place)" in text and "save point" in text and "stripforge restore" in text
    assert "files from an older StripForge" in text and "fix-pre-stripbuild.kicad_pcb" in text
    assert b"StripForge:CUT_" in board.read_bytes()
    assert (tmp_path / "stripforge" / "fix-stripforge.links.json").is_file()
    assert "SF strip width" in (tmp_path / "fix.kicad_dru").read_text()
    (first,) = layout.backups(board)
    assert first.parent == backups and layout.read_member(first, "fix.kicad_pcb").encode() == original
    assert "rule mine" in layout.read_member(first, "fix.kicad_dru")  # their own DRC rules are kept
    built = board.read_bytes()
    # a rebuild makes a second, new zip; the first one is untouched (never renamed)
    assert main(["build", str(board), "--config", str(x56)]) == 1
    capsys.readouterr()
    zips = layout.backups(board)
    assert len(zips) == 2 and zips[0] == first and board.read_bytes() == built
    # restore last: back to the board just before the second build (= the first build)
    assert main(["restore", str(board), "--list"]) == 0
    assert str(zips[1]) in capsys.readouterr().out
    assert main(["restore", str(board)]) == 0
    assert "Loaded save point" in capsys.readouterr().out and board.read_bytes() == built
    # an older one by name: back to before the first build; the link files it had not written go
    assert main(["restore", str(board), "--backup", str(first)]) == 0
    assert "Removed" in capsys.readouterr().out
    assert board.read_bytes() == original
    assert not (tmp_path / "stripforge" / "fix-stripforge.links.json").exists()
    assert "rule mine" in (tmp_path / "fix.kicad_dru").read_text()
    assert (tmp_path / "fix-pre-stripbuild.kicad_pcb").read_text() == "a 0.2.x backup"  # left alone
    # --separate: stripforge/<Project>-stripforge.kicad_pcb, board left alone
    assert main(["build", str(board), "--config", str(x56), "--separate"]) == 1
    assert (tmp_path / "stripforge" / "fix-stripforge.kicad_pcb").is_file() and board.read_bytes() == original
    shutil.rmtree(tmp_path)


def test_cli_build_backup_keep_prunes_the_oldest_zips(tmp_path, capsys, real_board_path):
    from stripforge import layout

    board = tmp_path / "fix.kicad_pcb"
    shutil.copy(real_board_path, board)
    cfg = tmp_path / "stripforge" / "stripboard.toml"
    cfg.parent.mkdir()
    import re

    x56 = (real_board_path.parents[1] / "x56.toml").read_text()
    cfg.write_text(re.sub(r"(?m)^backup_keep\s*=.*$", "backup_keep = 2", x56))
    assert layout.find_config(board) == cfg
    for _ in range(3):
        assert main(["build", str(board), "--config", str(cfg)]) == 1
    assert "Deleted by backup_keep" in capsys.readouterr().out
    assert len(layout.backups(board)) == 2
    shutil.rmtree(tmp_path)


def test_cli_restore_without_a_save_point(tmp_path, capsys):
    board = tmp_path / "x.kicad_pcb"
    board.write_text("(kicad_pcb)\n")
    assert main(["restore", str(board)]) == 1
    assert "no save point yet" in capsys.readouterr().err and board.read_text() == "(kicad_pcb)\n"
