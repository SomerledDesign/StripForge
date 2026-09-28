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
    assert "Links: 25 proposed for 39 needed" in text and "Pass 1: add W1..W25" in text
    assert json.loads(out.with_suffix(".links.json").read_text())["links_needed"] == 39
    assert main(["build", str(out), "-o", str(out)]) == 2
    shutil.rmtree(tmp_path)
