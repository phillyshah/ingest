"""HTML parsing against the shape real pages actually have.

Every fixture in `fixtures/permitted-sources/` puts its headings and paragraphs directly under <body>. Real pages
never do — they wrap everything in at least one <div>. `parse_html` walked only the direct children of <body>, so
the whole suite passed while any live page parsed to zero blocks: no claims, no dose fields, nothing. The failure
was silent, because an empty parse looks exactly like a page with nothing to say.

These tests use nested markup on purpose. A fixture that is easy to parse proves nothing.
"""

from __future__ import annotations

from moveai_ingestion.parse import parse_html

# The wrapper depth a real site has: layout div, semantic main, article, then the content.
REAL = b"""<html><head><title>Terms of Use</title></head><body>
  <div id="page"><header><nav><ul><li><a href="/">Home</a></li></ul></nav></header>
    <main><article>
      <h1>Terms of Use</h1>
      <p>Material on this site is in the public domain and may be reused.</p>
      <h2>Exceptions</h2>
      <p>Illustrations licensed from third parties are excluded.</p>
      <ul><li>Photographs</li><li>Anatomical drawings</li></ul>
      <table><caption>Permissions</caption><tr><th>Use</th><th>Allowed</th></tr><tr><td>Copy</td><td>Yes</td></tr></table>
      <img src="/logo.png" alt="Agency logo">
    </article></main>
  </div>
  <script>window.analytics = "do not read me as prose";</script>
  <style>.x { color: red }</style>
</body></html>"""


def test_content_nested_in_divs_is_found():
    doc = parse_html(REAL)
    assert doc.title == "Terms of Use"
    text = doc.text()
    assert "public domain and may be reused" in text
    assert "Illustrations licensed from third parties" in text


def test_script_and_style_are_not_read_as_prose():
    text = parse_html(REAL).text()
    assert "do not read me as prose" not in text
    assert "color: red" not in text


def test_headings_still_nest_correctly_when_wrapped():
    """heading_path is what a locator points at, so it has to survive the wrappers."""
    doc = parse_html(REAL)
    exceptions = next(b for b in doc.blocks if b.text.startswith("Illustrations"))
    assert exceptions.heading_path == ["Terms of Use", "Exceptions"]


def test_a_table_is_one_block_and_its_cells_are_not_repeated():
    doc = parse_html(REAL)
    tables = [b for b in doc.blocks if b.kind == "table"]
    assert len(tables) == 1
    assert tables[0].rows == [["Use", "Allowed"], ["Copy", "Yes"]]
    # "Copy" must appear as a table cell only. Emitting it again as a paragraph would duplicate the claim and give
    # two different locators for one statement.
    assert not [b for b in doc.blocks if b.kind == "paragraph" and b.text == "Copy"]


def test_a_list_is_one_block_and_its_items_are_not_repeated():
    doc = parse_html(REAL)
    lists = [b for b in doc.blocks if b.kind == "list"]
    assert any("Photographs" in b.text and "Anatomical drawings" in b.text for b in lists)
    assert not [b for b in doc.blocks if b.kind == "paragraph" and b.text == "Photographs"]


def test_an_empty_page_is_still_empty():
    """The fix must not invent structure: a page with no content still parses to nothing."""
    assert parse_html(b"<html><body><div><script>x=1</script></div></body></html>").blocks == []


def test_the_flat_fixture_shape_still_works():
    """The owned fixtures are flat. Fixing nesting must not break them."""
    doc = parse_html(b"<html><body><h2>Phase 1</h2><p>Heel slides, 3 sets of 10.</p></body></html>")
    assert [b.kind for b in doc.blocks] == ["heading", "paragraph"]
    assert "Heel slides" in doc.text()
