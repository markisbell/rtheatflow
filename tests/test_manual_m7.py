"""GET /manual serves the German Benutzerhandbuch (SPEC §2/§7, M7)."""
from __future__ import annotations

from conftest import make_api_client

from rtheatflow.api.core import render_markdown


def test_render_markdown_subset():
    html = render_markdown(
        "# Titel\n\nAbsatz mit **fett**, *kursiv*, `code` und "
        "[Link](https://example.org).\n\n"
        "## Liste\n\n- eins\n- zwei\n\n1. erstens\n2. zweitens\n\n"
        "> Merksatz\n\n---\n\n"
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "```\nroh <tag> & so\n```\n")
    assert "<h1>Titel</h1>" in html and "<h2>Liste</h2>" in html
    assert "<strong>fett</strong>" in html and "<em>kursiv</em>" in html
    assert "<code>code</code>" in html
    assert '<a href="https://example.org">Link</a>' in html
    assert "<ul>" in html and "<li>eins</li>" in html
    assert "<ol>" in html and "<li>zweitens</li>" in html
    assert "<blockquote>" in html and "<hr>" in html
    assert "<table>" in html and "<th>A</th>" in html and "<td>2</td>" in html
    # fenced code is escaped verbatim — no HTML injection through the manual
    assert "roh &lt;tag&gt; &amp; so" in html


def test_manual_route_serves_the_benutzerhandbuch():
    with make_api_client() as client:
        r = client.get("/manual")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        body = r.text
        assert "Benutzerhandbuch" in body
        # the SPEC §3.5 honesty section is part of the deliverable
        assert "Grenzen des Modells" in body
        assert "Temperaturfronten" in body
        # the three-view teaching table made it through the table renderer
        assert "<table>" in body and "Realität" in body

        raw = client.get("/manual", params={"format": "md"})
        assert raw.status_code == 200
        assert raw.headers["content-type"].startswith("text/markdown")
        assert raw.text.startswith("# rtheatflow — Benutzerhandbuch")

        assert client.get("/manual", params={"format": "pdf"}).status_code == 422
