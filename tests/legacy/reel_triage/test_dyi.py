import io
import json
import zipfile

from reel_triage import dyi


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


def test_parses_saved_posts_and_dm_links_with_source():
    saved = json.dumps({"saved_saved_media": [
        {"title": "user", "string_map_data": {"Saved on": {
            "href": "https://www.instagram.com/reel/SAVED1/", "timestamp": 1700000000}}}]})
    dm = json.dumps({"messages": [
        {"content": "look https://instagram.com/p/DM1/", "timestamp_ms": 1700000001000}]})
    data = _zip({"saved_posts.json": saved,
                 "messages/inbox/x/message_1.json": dm})
    found = dyi.parse(data)
    by_code = {f["shortcode"]: f for f in found}
    assert by_code["SAVED1"]["source"] == "dyi_saved"
    assert by_code["DM1"]["source"] == "dyi_dm"


def test_dedupes_by_shortcode_within_zip():
    saved = json.dumps({"saved_saved_media": [
        {"string_map_data": {"Saved on": {"href": "https://instagram.com/reel/DUP/"}}}]})
    dm = json.dumps({"messages": [{"content": "https://instagram.com/reel/DUP/"}]})
    found = dyi.parse(_zip({"saved_posts.json": saved,
                            "messages/inbox/x/message_1.json": dm}))
    assert len([f for f in found if f["shortcode"] == "DUP"]) == 1
    # first-seen (saved file iterates first) wins source
    assert next(f for f in found if f["shortcode"] == "DUP")["source"] == "dyi_saved"


def test_ignores_non_ig_strings_and_bad_json():
    data = _zip({"saved_posts.json": "{not json",
                 "messages/inbox/x/message_1.json": json.dumps(
                     {"messages": [{"content": "hello world no link"}]})})
    assert dyi.parse(data) == []
