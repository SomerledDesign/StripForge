import pytest

from stripforge.sexpr import SExprError, Sym, atom, dumps, find, find_all, head, loads, mm_to_nm


def test_parse_nested_and_atom_kinds():
    t = loads('(pad "1" thru_hole circle (at 2.54 -1.27 90) (net "GND"))')
    assert head(t) == "pad"
    assert t[1] == "1" and not isinstance(t[1], Sym)
    assert isinstance(t[2], Sym) and t[2] == "thru_hole"
    assert find(t, "at") == ["at", "2.54", "-1.27", "90"]
    assert atom(find(t, "net"), 1) == "GND"
    assert atom(find(t, "missing")) is None


def test_escapes_and_empty_string():
    t = loads(r'(x "a \"q\" b\\c" "" (y))')
    assert t[1] == 'a "q" b\\c'
    assert t[2] == ""
    assert loads(dumps(t)) == t


def test_find_all():
    t = loads("(r (p 1) (q 2) (p 3))")
    assert [atom(n) for n in find_all(t, "p")] == ["1", "3"]


@pytest.mark.parametrize("bad", ["(a (b)", "(a))", "", "(a) (b)", "x"])
def test_malformed(bad):
    with pytest.raises(SExprError):
        loads(bad)


def test_roundtrip_fixture_tree(tpi_board_path):
    tree = loads(tpi_board_path.read_text())
    again = loads(dumps(tree))
    assert again == tree
    # quoted-ness survives the round trip
    assert [type(a) for a in again[:2]] == [type(a) for a in tree[:2]]


def test_mm_to_nm_exact():
    assert mm_to_nm("2.54") == 2_540_000
    assert mm_to_nm("-0.01") == -10_000
    assert mm_to_nm("126.2") == 126_200_000
    assert mm_to_nm(1.27) == 1_270_000
