import pytest

from csa_zendesk._markdown import HIDING_RULES, strip_suspicious, to_markdown


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


@pytest.mark.parametrize(
    "style",
    [
        "display:none",
        "display: none",
        "visibility:hidden",
        "font-size:0",
        "height:0",
        "max-height:0",
        "opacity:0",
        "left:-9999px",
        "text-indent:-9999px",
    ],
)
def test_hidden_text_is_surfaced_not_emitted_as_prose(style):
    html = f'<p>Refund please.</p><div style="{style}">SECRET INSTRUCTION</div>'
    md, hidden = to_markdown(html)
    assert "Refund please." in md
    assert "SECRET INSTRUCTION" not in md, "hidden text leaked into the visible Markdown"
    assert hidden == ["SECRET INSTRUCTION"]


def test_hidden_elements_with_no_text_produce_nothing():
    # Measured: 4 of 10 hidden elements in the real corpus are EMPTY - spacers
    # and tracking pixels. Reporting them would put a hidden block on every
    # newsletter for no information at all.
    md, hidden = to_markdown('<p>hi</p><div style="display:none"></div><span style="font-size:0">   </span>')
    assert hidden == []
    assert "hi" in md


def test_a_body_that_is_entirely_hidden_still_returns_coherently():
    md, hidden = to_markdown('<div style="display:none">only this</div>')
    assert md.strip() == ""
    assert hidden == ["only this"]


def test_white_on_white_is_a_KNOWN_GAP_and_still_leaks():
    # Recorded, not fixed. Deciding whether text is the same colour as its
    # background needs the ANCESTOR's background, i.e. the cascade. This test
    # pins the current behaviour so that closing the gap is a deliberate,
    # visible change rather than an accident.
    html = '<div style="background:#ffffff"><span style="color:#ffffff">STILL LEAKS</span></div>'
    md, hidden = to_markdown(html)
    assert "STILL LEAKS" in md
    assert hidden == []


def test_every_hiding_rule_is_exercised_by_a_test():
    # Anti-vacuity: a rule added to HIDING_RULES without a case above would
    # otherwise be untested and look covered.
    exercised = {"display", "visibility", "font-size", "height", "opacity", "left", "text-indent"}
    for rule in HIDING_RULES:
        assert any(token in rule for token in exercised), f"{rule} has no test"


@pytest.mark.parametrize(
    "text,gone",
    [
        ("invoice‮fdp.exe", "‮"),  # bidi override (Trojan Source)
        ("a﻿b", "﻿"),  # BOM mid-text
        ("x\u0000y", "\u0000"),  # control character
    ],
)
def test_codepoints_with_no_communicative_purpose_are_removed(text, gone):
    assert gone not in strip_suspicious(text)


def test_a_zero_width_signature_attack_is_NOT_defanged_by_stripping():
    # KNOWN LIMIT, pinned so narrowing _STRIP later is a deliberate act.
    # U+200C is semantic in Persian (می‌رود vs میرود) and required for Indic
    # conjuncts and Arabic shaping, so it cannot be stripped unconditionally.
    # The observed 2026-09 case interleaved U+200C through a signature at
    # 174 per 1k characters; catching THAT needs a density rule, which is
    # deliberately not in this block.
    attacked = "‌".join("Best regards")
    assert strip_suspicious(attacked) == attacked


@pytest.mark.parametrize(
    "text",
    [
        "Sehr geehrte Damen und Herren",
        "Château, naïve, résumé",
        "Здравствуйте",
        "パスワード",
        "أحتاج إلى مساعدة",
        "Thanks! \U0001f642\U0001f44d",
        "xʷməθkʷəy",  # Musqueam - MUST survive
        "Pаypal",  # homoglyph - NOT our job here
        "می‌رود",  # Persian ZWNJ is semantic: "mi-ravad" vs "میرود" is a different word
        "\U0001f468‍\U0001f469‍\U0001f467",  # family emoji - a ZWJ sequence
        "\U0001f3f4\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f",  # Scotland flag - tag chars
        "‫שלום‬",  # Hebrew wrapped in bidi EMBEDDING (not override)
        "100‎$",  # LRM - a legitimate directional mark
        "क्‍ष",  # Hindi conjunct control - ZWJ changes the rendered glyph
        "أحب‍ك",  # Arabic ZWJ - letter shaping
    ],
)
def test_legitimate_text_is_untouched(text):
    # Four detectors before this one had non-English content as their dominant
    # failure mode, and a first pass at this one's own _STRIP set damaged
    # seven more (Persian, emoji, subdivision flags, Hebrew embedding, LRM,
    # Hindi and Arabic shaping) before being measured against this corpus.
    assert strip_suspicious(text) == text


def test_to_markdown_strips_in_both_the_visible_and_hidden_output():
    html = '<p>Hi ﻿there</p><div style="display:none">bad﻿text</div>'
    md, hidden = to_markdown(html)
    assert "﻿" not in md
    assert hidden and "﻿" not in hidden[0]
