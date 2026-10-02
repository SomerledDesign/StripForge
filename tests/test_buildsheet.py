"""The build sheet (stripforge sheet): structure and content, not pixels."""

from __future__ import annotations

import json
import re
import shutil

import pytest

import pass2_sim
from sfbuild import one_pad_board
from stripforge import __version__, buildsheet, config, writer
from stripforge.cli import main
from stripforge.config import BoardConfig

DATE = "2026-09-27"


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    """The real-parts fixture built for pass 1 and (simulated) pass 2, with both sheets rendered."""
    from conftest import REAL_FIXTURE

    tmp = tmp_path_factory.mktemp("sheet")
    board, net = REAL_FIXTURE / "ATtiny10_TPI_Fixture.kicad_pcb", REAL_FIXTURE / "ATtiny10_TPI_Fixture.net"
    cfg = config.load(REAL_FIXTURE.parent / "x56.toml")
    cfg.place_links = False  # the two-pass flow: links proposed, then added by (simulated) F8
    p1 = tmp / "p1" / "fixture.kicad_pcb"
    writer.build(board, cfg, p1, netlist=str(net))
    links = json.loads(p1.with_name("fixture-stripforge.links.json").read_text())["links"]
    f8 = tmp / "f8"
    f8.mkdir()
    pass2_sim.add_links_to_board(board, links, f8 / "fixture.kicad_pcb")
    pass2_sim.add_links_to_netlist(net, links, f8 / "fixture.net")
    p2 = tmp / "p2" / "fixture.kicad_pcb"
    writer.build(f8 / "fixture.kicad_pcb", cfg, p2, netlist=str(f8 / "fixture.net"))
    m1 = buildsheet.sheet_model(p1, cfg, str(net), DATE)
    m2 = buildsheet.sheet_model(p2, cfg, str(f8 / "fixture.net"), DATE)
    return {
        "cfg": cfg,
        "board": board,
        "p1": p1,
        "p2": p2,
        "m1": m1,
        "m2": m2,
        "h1": buildsheet.render_html(m1),
        "h2": buildsheet.render_html(m2),
    }


def items(html: str, kind: str) -> list[str]:
    return re.findall(rf'class="item" data-kind="{kind}" data-\w+="([^"]+)"', html)


def test_counts_match_the_board(real):
    m, h = real["m2"], real["h2"]
    assert m.built == "built"
    cut_ids = items(h, "cut")
    assert (
        len(cut_ids) == len(m.a.split.cuts) == len(m.prep.old_cuts) == 97
    )  # incl. the bus-strip and trim cuts
    assert sorted(cut_ids) == sorted(f"X{fp.ref[3:]}" for fp in m.prep.old_cuts)
    assert len(re.findall(r'data-style="knife"', h)) == 28
    assert len(items(h, "link")) == 40 and [r.status for r in m.links] == ["placed"] * 40
    assert len(items(h, "part")) == 22
    assert len(items(h, "slot")) == 2


def test_header_names_project_board_date_and_version(real):
    h = real["h2"]
    assert "StripForge build sheet: fixture" in h
    assert "56 holes × 24 strips (A1-X56)" in h and "142.24 × 60.96 mm" in h
    assert DATE in h and f"<td>StripForge</td><td>{__version__}</td>" in h


def test_cut_list_is_grouped_by_strip_in_order(real):
    h = real["h2"]
    heads = re.findall(r'<li class="strip-head"><b>Strip (\w+)</b>', h)
    assert heads == sorted(heads, key=lambda s: (len(s), s)) and heads[0] == "A"  # bus strip A
    assert (
        'data-cut="X1" data-style="hole" data-key="cut:X1"><span class="box" role="checkbox" '
        'aria-checked="false" tabindex="0"></span><span class="what">'
        '<span class="mono">X1</span> hole <b>B24</b>' in h
    )
    assert "<b>knife B27|B28</b>" in h


def test_slot_jobs_say_which_way_and_how_far(real):
    h = real["h2"]
    # the BH23APC's oval slots: file each end hole 0.025" (0.635 mm) toward the part centre
    assert "file <b>U16</b> <b>0.025&quot; (0.635 mm)</b> toward <b>U17</b> (toward the part centre)" in h
    assert "file <b>U29</b> <b>0.025&quot; (0.635 mm)</b> toward <b>U28</b> (toward the part centre)" in h
    assert not re.search(r"(?<![\d.])0\.32 mm", h)


def test_links_list_from_to_length_and_footprint(real):
    h = real["h2"]
    row = re.search(r'<tr class="item" data-kind="link" data-link="W7"[^>]*>.*?</tr>', h).group(0)
    assert "<b>B18</b>" in row and "<b>U18</b>" in row and "1.9&quot;" in row and "19 holes, 48.26 mm" in row
    assert "StripForge:Link_P48.26" in row and "placed" in row


def test_parts_in_build_order_with_every_pin_hole(real):
    m = real["m2"]
    groups = [r.group for r in m.parts]
    assert groups == sorted(groups)
    order = [r.ref for r in m.parts]
    assert order.index("R1") < order.index("D1") < order.index("J2") < order.index("C1")
    assert order.index("C1") < order.index("J1") < order.index("SW1") < order.index("BT1")
    j2 = next(r for r in m.parts if r.ref == "J2")
    assert len(j2.pins) == 18 and j2.group_name == "ICs and sockets"
    h = real["h2"]
    row = re.search(r'data-kind="part" data-ref="R3"[^>]*>.*?</tr>', h).group(0)
    assert "1:<b>H25</b>" in row and "2:<b>L25</b>" in row


def test_net_table_lists_every_hole_including_link_ends(real):
    m = real["m2"]
    gnd = next(n for n in m.nets if n.net == "GND")
    labels = [h for h, _ in gnd.holes]
    pads = {o.label for occ in m.a.holes.occupants.values() for o in occ if o.net == "GND"}
    assert all(any(p in w for _, w in gnd.holes) for p in pads)
    assert any("end" in w for _, w in gnd.holes)  # GND links count
    assert len(labels) == len(set(labels))
    assert not gnd.joined  # GND is one of the unlinkable nets on this placement
    assert m.single_pin_nets > 0 and not any(n.net.startswith("unconnected-") for n in m.nets)


def test_warnings_cover_knife_cuts_overlaps_and_unlinkable_nets(real):
    m, h = real["m2"], real["h2"]
    assert len(m.knife_cuts) == 28 and len(m.unlinkable) == 7
    assert len(re.findall(r'<li data-kind="knife">', h)) == 28
    assert len(re.findall(r'<li data-kind="unlinkable">', h)) == 7
    assert any("W7 (B18-U18) runs under BT1, J2" in o for o in m.overlaps)


def test_pass1_sheet_draws_links_dashed_and_says_so(real):
    m, h = real["m1"], real["h1"]
    assert [r.status for r in m.links] == ["to-add"] * 40
    assert h.count('class="wire proposed"') == 40
    assert any("40 wire link(s) are not placed" in w for w in m.warnings)


def test_copper_view_is_mirrored_and_labelled_on_both_edges(real):
    m = real["m2"]
    (cv,) = buildsheet.views(m, True)
    (pv,) = buildsheet.views(m, False)
    svg_c = buildsheet.copper_svg(m, cv)
    svg_p = buildsheet.component_svg(m, pv)

    def xs(svg, text):
        return [
            float(x)
            for x in re.findall(rf'<text x="([\d.]+)" y="[\d.]+" class="lbl num"[^>]*>{text}</text>', svg)
        ]

    # numbers top and bottom, letters left and right
    assert len(xs(svg_c, "1")) == 2 and len(xs(svg_c, "56")) == 2
    assert len(re.findall(r'class="lbl" font-size="[\d.]+">X</text>', svg_c)) == 2
    assert xs(svg_c, "1")[0] > xs(svg_c, "56")[0]  # mirrored: hole 1 on the right
    assert xs(svg_p, "1")[0] < xs(svg_p, "56")[0]
    assert 'data-side="copper"' in svg_c and "MIRRORED" in real["h2"]


def test_mirror_on_an_asymmetric_board(tmp_path):
    # one cut near the left edge (component side) must appear near the right edge on the copper view
    board = one_pad_board(tmp_path, [("P1", 0, 0, "A"), ("P2", 2, 0, "B")])
    m = buildsheet.sheet_model(board, BoardConfig(trim_pieces=False), date=DATE)
    (cut,) = m.a.split.cuts
    assert cut.label == "A2"
    (v,) = buildsheet.views(m, True)
    svg = buildsheet.copper_svg(m, v)
    x = float(re.search(r'data-cut="X1"><circle cx="([\d.]+)"', svg).group(1))
    assert x > v.width * 0.75
    (v,) = buildsheet.views(m, False)
    assert v.hole(0, 1)[0] < v.width * 0.25


def test_placement_board_and_edited_board_are_flagged(real, tmp_path):
    m = buildsheet.sheet_model(real["board"], real["cfg"], date=DATE)
    assert m.built == "not-built" and 'data-built="not-built"' in buildsheet.render_html(m)
    # delete one cut marker from the built board: the sheet must notice
    text = real["p2"].read_text()
    start = text.index('(footprint "StripForge:CUT_')
    depth, i = 0, start
    while True:
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        i += 1
        if depth == 0:
            break
    edited = tmp_path / "edited.kicad_pcb"
    edited.write_text(text[:start] + text[i:])
    m = buildsheet.sheet_model(edited, real["cfg"], date=DATE)
    assert m.built == "differs" and "missing: CUT" in m.warnings[0]


def test_html_is_self_contained_and_deterministic(real):
    h = real["h2"]
    assert h.count("<script") == 1 and "<script>" in h  # the inline checklist script, nothing loaded
    assert "<link" not in h and " src=" not in h
    assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)", h)
    assert "@page" in h and "Letter" in h
    assert buildsheet.render_html(real["m2"]) == h


def test_part_groups():
    g = buildsheet.part_group
    assert g("W3", "StripForge:Link_P5.08") == 0
    assert g("R1", "Resistor_THT:R") < g("D1", "LED_THT:LED") < g("U1", "Package_DIP:DIP-8")
    assert g("U1", "x") < g("C1", "x") < g("J1", "x") < g("SW1", "x") < g("BT1", "x")
    assert g("J2", "Lib:CNV-SOT23-6L-DIP_2x09") == g("U1", "x")


def test_cli_sheet_writes_html_svg_and_csv(real, tmp_path, capsys):
    out = tmp_path / "s.html"
    code = main(["sheet", str(real["p2"]), "--config", str(real["board"].parents[1] / "x56.toml"),
                 "-o", str(out), "--no-pdf", "--date", DATE])  # fmt: skip
    text = capsys.readouterr().out
    assert code == 0 and "Cuts: 97, slot jobs: 2, wire links: 40 (40 placed)" in text
    for suffix in (".html", ".copper.svg", ".component.svg", ".cuts.csv"):
        assert out.with_suffix(suffix).exists()
    rows = out.with_suffix(".cuts.csv").read_text().splitlines()
    assert rows[0].startswith("id,strip,style,at") and len(rows) == 98
    assert main(["sheet", str(tmp_path / "missing.kicad_pcb"), "-o", str(out), "--no-pdf"]) == 2
    shutil.rmtree(tmp_path)


@pytest.mark.skipif(buildsheet.find_chrome() is None, reason="no Chrome/Chromium for the PDF")
def test_pdf_via_chrome(real, tmp_path):
    res = buildsheet.write_sheet(real["p2"], real["cfg"], tmp_path / "s.html", date=DATE)
    pdf = tmp_path / "s.pdf"
    if not pdf.exists():
        pytest.skip(f"Chrome could not print here: {res.notes}")
    assert pdf.read_bytes()[:5] == b"%PDF-"


@pytest.mark.skipif(not hasattr(__import__("os"), "killpg"), reason="POSIX process groups")
def test_chrome_that_writes_then_hangs_is_stopped(tmp_path):
    """Headless Chrome on macOS writes the PDF and then never exits: poll for the file, then kill it."""
    import os
    import sys
    import time

    fake = tmp_path / "fake-chrome"
    pidfile = tmp_path / "child.pid"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os, subprocess, sys, time\n"
        f"child = subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(600)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(child.pid))\n"
        "out = next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--print-to-pdf='))\n"
        "open(out, 'wb').write(b'%PDF-1.4 fake')\n"
        "time.sleep(600)\n"
    )
    fake.chmod(0o755)
    html_path = tmp_path / "s.html"
    html_path.write_text("<p>x</p>")
    t0 = time.monotonic()
    buildsheet.html_to_pdf(html_path, tmp_path / "s.pdf", str(fake))
    assert time.monotonic() - t0 < 30
    assert (tmp_path / "s.pdf").read_bytes().startswith(b"%PDF")
    child = int(pidfile.read_text())
    for _ in range(40):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        # a zombie still answers kill(0); reap check via /proc where available
        if os.path.exists(f"/proc/{child}/stat") and open(f"/proc/{child}/stat").read().split()[2] == "Z":
            break
        time.sleep(0.1)
    else:
        raise AssertionError("helper process left running")


def test_chrome_that_fails_raises(tmp_path):
    import subprocess
    import sys

    fake = tmp_path / "bad-chrome"
    fake.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(3)\n")
    fake.chmod(0o755)
    html_path = tmp_path / "s.html"
    html_path.write_text("<p>x</p>")
    with pytest.raises(subprocess.CalledProcessError):
        buildsheet.html_to_pdf(html_path, tmp_path / "s.pdf", str(fake))


def test_sheet_keeps_the_previous_one_only_when_it_changes(tmp_path):
    """Kevin 2026-09-29: Build sheet overwrote the last sheet silently."""
    cfg = BoardConfig(trim_pieces=False)
    board = tmp_path / "b.kicad_pcb"
    writer.build(one_pad_board(tmp_path, [("P1", 0, 0, "A"), ("P2", 6, 0, "B")]), cfg, board)
    out = tmp_path / "b-stripforge.sheet.html"
    res = buildsheet.write_sheet(board, cfg, out, pdf=False, date="2026-09-28")
    assert not res.backups
    (tmp_path / "b-stripforge.sheet.pdf").write_text("old pdf")  # as if Chrome had printed one
    res = buildsheet.write_sheet(board, cfg, out, pdf=False, date="2026-09-28")
    assert not res.backups  # same sheet: nothing to keep
    first = out.read_text()
    res = buildsheet.write_sheet(board, cfg, out, pdf=False, date="2026-09-29")
    prev = tmp_path / "b-stripforge.sheet-prev.html"
    assert res.backups == [str(prev), str(tmp_path / "b-stripforge.sheet-prev.pdf")]
    assert prev.read_text() == first and (tmp_path / "b-stripforge.sheet-prev.pdf").read_text() == "old pdf"
    buildsheet.write_sheet(board, cfg, out, pdf=False, date="2026-09-30")
    assert (tmp_path / "b-stripforge.sheet-prev-1.html").read_text() == first
    keep1 = BoardConfig(trim_pieces=False, backup_keep=1)
    buildsheet.write_sheet(board, keep1, out, pdf=False, date="2026-10-01")
    assert not (tmp_path / "b-stripforge.sheet-prev-1.html").exists()  # backup_keep = 1: just -prev


# --- clickable checklists (Kevin 2026-10-02, building the TPI fixture) --------------------------


def _row(h: str, key: str) -> str:
    return re.search(rf'<(li|tr) class="item"[^>]*data-key="{re.escape(key)}">.*?</\1>', h).group(0)


def test_every_checklist_row_has_a_key_a_box_and_where_it_matters_a_status(real):
    h = real["h2"]
    keys = re.findall(r'data-key="([^"]+)"', h)
    assert len(keys) == len(set(keys)) == h.count('class="item"')
    kinds = {k.split(":", 1)[0] for k in keys}
    assert {"cut", "slot", "link", "link-length", "part", "net"} <= kinds
    assert h.count('role="checkbox"') == len(keys)
    cut = _row(h, "cut:" + items(h, "cut")[0])
    assert '<span class="status" data-done="Cut">' in cut and '<span class="what">' in cut
    link = _row(h, "link:W7")
    assert '<td class="status"><span class="status" data-done="Installed">placed</span></td>' in link
    part = _row(h, "part:R3")
    assert '<td class="status"><span class="status" data-done="Installed"></span></td>' in part
    assert h.count("<th>Status</th>") == 1 + h.count('<table class="list parts">')
    for kind in ("cut", "link", "part"):
        assert f'<span class="progress" data-for="{kind}"></span>' in h
    assert '<button type="button" id="reset-checklist">Reset checklist</button>' in h


def test_yours_stays_in_the_status_until_ticked(tmp_path):
    from test_edits import edit, pass2, rebuild

    p2 = pass2(tmp_path)
    edit(p2, "W1", "A5")
    edit(p2, "CUT1", "A6")
    rebuild(p2)
    h = buildsheet.render_html(buildsheet.sheet_model(p2, BoardConfig(trim_pieces=False), date=DATE))
    yours = re.findall(r'<span class="status" data-done="(\w+)">([^<]*(?:<b>[^<]*</b>)?[^<]*)</span>', h)
    assert ("Cut", "<b>(yours)</b>") in yours and ("Installed", "placed <b>(yours)</b>") in yours


def test_build_hash_follows_the_checklists_not_the_date(real):
    import copy

    m2 = real["m2"]
    build = re.search(r'<body data-board="fixture" data-build="([0-9a-f]{16})"', real["h2"]).group(1)
    later = copy.copy(m2)
    later.date = "2027-01-01"
    h = buildsheet.render_html(later)
    assert "2027-01-01" in h and f'data-build="{build}"' in h  # printing again keeps the ticks
    b1 = re.search(r'data-build="([0-9a-f]{16})"', real["h1"]).group(1)
    assert b1 != build  # links added since pass 1: a different build, a fresh checklist


def test_ticks_and_strike_through_print(real):
    h = real["h2"]
    css = h.split("<style>", 1)[1].split("</style>", 1)[0]
    screen = css.split("@media screen", 1)[1].split("@media print", 1)[0]
    assert ".item.done .box::after" in css and ".item.done .box::after" not in screen
    assert "text-decoration: line-through" in css and "line-through" not in screen
    assert "td:not(.status)" in css


@pytest.mark.skipif(buildsheet.find_chrome() is None, reason="no Chrome/Chromium to run the script")
def test_clicking_rows_ticks_strikes_saves_and_resets(real, tmp_path):
    """Run the sheet's own script in headless Chrome: seed a saved tick, click rows, read back."""
    import html as html_mod
    import subprocess

    h = real["h2"]
    first_cut = "cut:" + items(h, "cut")[0]
    key_js = (
        '"stripforge-sheet:" + document.body.getAttribute("data-board") + ":" '
        '+ document.body.getAttribute("data-build")'
    )
    seed = (
        f"<script>localStorage.clear(); localStorage.setItem({key_js}, "
        f"JSON.stringify([{first_cut!r}]));</script>"
    )
    probe = (
        "<script>window.confirm = function () { return true; };"
        f"var key = {key_js}, r = {{}};"
        "function q(k) { return document.querySelector('.item[data-key=\"' + k + '\"]'); }"
        "function st(k) { return q(k).querySelector('.status').textContent; }"
        f"r.seeded = [q({first_cut!r}).classList.contains('done'), st({first_cut!r})];"
        "q('link:W7').querySelector('td:nth-child(3)').click();"
        "r.link = [q('link:W7').classList.contains('done'), st('link:W7'),"
        " q('link:W7').querySelector('.box').getAttribute('aria-checked')];"
        "r.strike = getComputedStyle(q('link:W7').querySelector('td:nth-child(3)')).textDecorationLine;"
        "r.statusStrike = getComputedStyle(q('link:W7').querySelector('td.status')).textDecorationLine;"
        "q('part:R3').querySelector('.box').click();"
        "r.part = [q('part:R3').classList.contains('done'), st('part:R3')];"
        "r.saved = JSON.parse(localStorage.getItem(key)).sort();"
        "r.progress = document.querySelector('.progress[data-for=\"link\"]').textContent;"
        "r.hasTicks = document.body.classList.contains('has-ticks');"
        "q('link:W7').click();"
        "r.undo = [q('link:W7').classList.contains('done'), st('link:W7')];"
        "document.getElementById('reset-checklist').click();"
        "r.reset = [document.querySelectorAll('.item.done').length, localStorage.getItem(key)];"
        "document.body.setAttribute('data-result', JSON.stringify(r));</script>"
    )
    sheet_js = h.rindex("<script>")
    page = tmp_path / "probe.html"
    page.write_text(
        h[:sheet_js] + seed + h[sheet_js:].replace("</body>", probe + "</body>"), encoding="utf-8"
    )
    cmd = [buildsheet.find_chrome(), "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
           f"--user-data-dir={tmp_path / 'prof'}", "--dump-dom", page.as_uri()]  # fmt: skip
    try:
        dom = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
    except subprocess.TimeoutExpired:
        pytest.skip("headless Chrome did not finish")
    r = json.loads(html_mod.unescape(re.search(r'data-result="([^"]*)"', dom).group(1)))
    n_links = len(items(h, "link"))
    assert r["seeded"] == [True, "Cut"]
    assert r["link"] == [True, "Installed", "true"]
    assert r["strike"] == "line-through" and r["statusStrike"] == "none"
    assert r["part"] == [True, "Installed"]
    assert r["saved"] == sorted([first_cut, "link:W7", "part:R3"])
    assert r["progress"] == f"1/{n_links} links" and r["hasTicks"] is True
    assert r["undo"] == [False, "placed"]
    assert r["reset"] == [0, None]
