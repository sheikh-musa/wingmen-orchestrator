from reel_triage import links


def test_extracts_shortcode_from_reel_url():
    assert links.shortcode("https://www.instagram.com/reel/CxYz123/") == "CxYz123"


def test_extracts_shortcode_from_p_url():
    assert links.shortcode("https://instagram.com/p/AbC_9/?igshid=1") == "AbC_9"


def test_non_instagram_url_returns_none():
    assert links.shortcode("https://youtube.com/watch?v=x") is None


def test_empty_returns_none():
    assert links.shortcode("") is None
    assert links.shortcode(None) is None


def test_find_all_ig_links_in_text():
    text = "check https://instagram.com/reel/AAA and https://www.instagram.com/p/BBB/"
    assert set(links.find_links(text)) == {
        "https://instagram.com/reel/AAA",
        "https://www.instagram.com/p/BBB/",
    }


def test_find_links_empty():
    assert links.find_links("no links here") == []
    assert links.find_links(None) == []
