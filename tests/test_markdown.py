import pytest

from csa_zendesk._markdown import to_markdown


def test_basic_html_becomes_markdown():
    md, hidden = to_markdown("<p>Hello <strong>world</strong></p>")
    assert "Hello" in md and "**world**" in md
    assert "<p>" not in md
    assert hidden == []


def test_links_keep_their_destination():
    # Dropping the href was the reason "just use Zendesk's plain body" was
    # rejected: a mailto: or https: target is often the thing an agent needs.
    md, _ = to_markdown('<p>See <a href="https://example.invalid/d">the doc</a>.</p>')
    assert "https://example.invalid/d" in md
    assert "the doc" in md


def test_script_does_not_survive():
    md, _ = to_markdown("<p>hi</p><script>alert(1)</script>")
    assert "alert(1)" not in md


def test_empty_and_absent_html_are_not_errors():
    for value in ("", "   "):
        md, hidden = to_markdown(value)
        assert md.strip() == ""
        assert hidden == []


@pytest.mark.parametrize(
    "html",
    ["<p>unclosed", "<div><span>stray < bracket</div>", "a &amp b &", "<<>>"],
)
def test_malformed_html_does_not_raise(html):
    # Real ticket HTML is mail-client output. BeautifulSoup tolerates this;
    # the transform must not add a failure mode BeautifulSoup does not have.
    md, hidden = to_markdown(html)
    assert isinstance(md, str) and isinstance(hidden, list)
