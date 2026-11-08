"""Learning Lea's tastes from her app downloads (lea_profile/apps.py)."""

import json
import zipfile

from lea_profile.apps import Export, identify, read_google, read_instagram, read_spotify, read_tiktok
from tests.test_orchestrator import make


def _zip(path, files: dict):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data if isinstance(data, (str, bytes)) else json.dumps(data))
    return path


def spotify_zip(tmp_path):
    history = [
        {"endTime": "2026-01-01 10:00", "artistName": "Maluma", "trackName": "La Reina", "msPlayed": 200_000},
        {"endTime": "2026-01-01 10:04", "artistName": "Maluma", "trackName": "Hawái", "msPlayed": 180_000},
        {"endTime": "2026-01-01 10:08", "artistName": "Ren", "trackName": "Hi Ren", "msPlayed": 400_000},
        {"endTime": "2026-01-01 10:15", "artistName": "Skipped", "trackName": "x", "msPlayed": 5_000},
    ]
    library = {"tracks": [{"artist": "Alex Warren", "track": "Bloodline"}], "artists": [{"name": "Ren"}]}
    return _zip(tmp_path / "my_spotify_data.zip", {
        "Spotify Account Data/StreamingHistory_music_0.json": history,
        "Spotify Account Data/YourLibrary.json": library,
    })  # fmt: skip


def test_each_download_is_recognised(tmp_path):
    sp = spotify_zip(tmp_path)
    ig = _zip(tmp_path / "instagram-lea-2026-11-08.zip", {"preferences/your_topics/your_topics.json": {}})
    tt = _zip(tmp_path / "data.zip", {"user_data_tiktok.json": {}})
    go = _zip(
        tmp_path / "takeout-20261108.zip",
        {"Takeout/YouTube and YouTube Music/history/watch-history.json": []},
    )
    other = _zip(tmp_path / "invoice.zip", {"invoice.pdf": b"%PDF"})
    for path, app in [(sp, "spotify"), (ig, "instagram"), (tt, "tiktok"), (go, "google"), (other, None)]:
        assert identify(path, Export(path).names()) == app, path.name


def test_spotify_favourite_artists(tmp_path):
    sig = read_spotify(Export(spotify_zip(tmp_path)))
    names = [t for t, _ in sig.topics]
    assert names[0] == "music: Ren" and "music: Maluma" in names and "music: Alex Warren" in names
    assert "music: Skipped" not in names  # a 5-second skip isn't a taste
    assert "Ren" in sig.facts[0] and "Maluma" in sig.facts[0]


def test_instagram_topics_and_tiktok_searches(tmp_path):
    ig = _zip(tmp_path / "instagram-lea.zip", {
        "preferences/your_topics/your_topics.json": {"topics_your_topics": [
            {"string_map_data": {"Name": {"value": "Latin music"}}},
            {"string_map_data": {"Name": {"value": "Cooking"}}},
        ]},
    })  # fmt: skip
    assert [t for t, _ in read_instagram(Export(ig)).topics] == ["Latin music", "Cooking"]
    tt = tmp_path / "user_data_tiktok.json"
    tt.write_text(json.dumps({"Your Activity": {
        "Searches": {"SearchList": [{"Date": "x", "SearchTerm": "pasta recipe"}, {"SearchTerm": "Pasta recipe"}]},
        "Favorite Hashtags": {"FavoriteHashtagList": [{"Link": "https://www.tiktok.com/tag/studytok"}]},
    }}))  # fmt: skip
    sig = read_tiktok(Export(tt))
    assert sig.items[0] == "pasta recipe" and "#studytok" in sig.items


def test_google_takeout_youtube_and_docs(tmp_path):
    go = _zip(tmp_path / "takeout-1.zip", {
        "Takeout/YouTube and YouTube Music/history/watch-history.json": [
            {"title": "Watched How to make arepas", "subtitles": [{"name": "Cocina Fácil"}]},
        ],
        "Takeout/YouTube and YouTube Music/history/search-history.json": [{"title": "Searched for calculus limits"}],
        "Takeout/Drive/Criminology essay.txt": "Notes on criminology theories.",
    })  # fmt: skip
    sig = read_google(Export(go))
    assert "YouTube channel: Cocina Fácil" in sig.items and "Document: Criminology essay" in sig.items
    assert sig.docs and sig.docs[0][0].endswith("Criminology essay.txt")


def test_a_download_in_the_folder_becomes_interests(tmp_path):
    topics = json.dumps({"topics": [{"topic": "Arepas", "strength": 2}]})
    s = make(tmp_path, [topics])
    from lea_profile.apps import AppLearning

    folder = tmp_path / "Downloads"
    folder.mkdir()
    spotify_zip(folder)
    _zip(folder / "takeout-1.zip", {
        "Takeout/YouTube and YouTube Music/history/watch-history.json": [{"title": "Watched How to make arepas"}],
    })  # fmt: skip
    a = AppLearning(s)
    a.folders = [folder]
    assert a.overview()["asked"] is False
    assert sorted(a.scan()) == ["google", "spotify"]
    assert a.scan() == []  # already read: not again
    s.bg.drain()
    topics = {i.topic for i in s.interests.list()}
    assert {"music: Ren", "music: Maluma", "Arepas"} <= topics
    ov = a.overview()
    spot = next(x for x in ov["apps"] if x["key"] == "spotify")
    assert spot["status"]["file"] == "my_spotify_data.zip" and "music: Ren" in spot["status"]["topics"]
    assert "Maluma" in s.memory.by_source("app:spotify")[0].text
    assert a.choose(["spotify", "nonsense"]) == ["spotify"] and a.overview()["asked"] is True
