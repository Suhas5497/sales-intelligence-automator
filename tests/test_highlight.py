from sia.highlight import highlight


def test_quotes_are_marked_and_html_is_escaped():
    text = "We install <b>roofs</b>.\nOur commercial team works with property   managers across Houston."
    out, found = highlight(text, ["works with property managers", "not on the page"])
    assert found == 1
    assert "<mark>works with property   managers</mark>" in out
    assert "&lt;b&gt;roofs&lt;/b&gt;" in out


def test_context_trims_long_pages():
    text = "x" * 2000 + " the quoted part " + "y" * 2000
    out, found = highlight(text, ["the quoted part"], context=50)
    assert found == 1 and len(out) < 300 and out.startswith("…")
