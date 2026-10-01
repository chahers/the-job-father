from jobfather_crawler import markdown as md


def test_html_to_markdown_headings_lists_emphasis():
    html = "<h2>About</h2><p>Hello <strong>world</strong></p><ul><li>Python</li><li>AWS</li></ul>"
    assert md.html_to_markdown(html) == "## About\n\nHello **world**\n\n- Python\n- AWS"


def test_html_to_markdown_links():
    assert md.html_to_markdown('<p>See <a href="https://x.com">this</a></p>') == "See [this](https://x.com)"


def test_html_to_markdown_drops_script_and_style():
    html = "<p>Keep</p><script>var x = 1;</script><style>p{color:red}</style>"
    assert md.html_to_markdown(html) == "Keep"


def test_html_to_markdown_passes_through_plain_text():
    assert md.html_to_markdown("Just  plain\ntext") == "Just plain\ntext"


def test_html_to_markdown_handles_empty():
    assert md.html_to_markdown(None) == ""
    assert md.html_to_markdown("") == ""


def test_assemble_document_has_front_matter():
    document = md.assemble_document({"source": "hiredly", "title": "Engineer"}, "## Body\n\nhello")
    assert document.startswith("---\n")
    assert "source: hiredly" in document
    assert document.rstrip().endswith("hello")


def test_looks_like_html():
    assert md.looks_like_html("<p>x</p>") is True
    assert md.looks_like_html("plain text") is False
    assert md.looks_like_html(None) is False
