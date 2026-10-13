import pytest

from app.core.engine.urls import parse_profile_url, parse_store_url, parse_url


@pytest.mark.parametrize(
    "url,platform,handle,canonical",
    [
        ("https://www.instagram.com/Acme.Bank/", "instagram", "acme.bank", "https://www.instagram.com/acme.bank/"),
        ("instagram.com/acmebank", "instagram", "acmebank", "https://www.instagram.com/acmebank/"),
        ("http://m.instagram.com/acmebank/?hl=en&igshid=abc", "instagram", "acmebank", "https://www.instagram.com/acmebank/"),
        ("https://instagram.com/acmebank/reels/", "instagram", "acmebank", "https://www.instagram.com/acmebank/"),
        ("https://instagr.am/acme_bank", "instagram", "acme_bank", "https://www.instagram.com/acme_bank/"),
        ("https://www.facebook.com/AcmeBankIndia/", "facebook", "acmebankindia", "https://www.facebook.com/acmebankindia"),
        ("https://m.facebook.com/AcmeBankIndia?ref=bookmarks", "facebook", "acmebankindia", "https://www.facebook.com/acmebankindia"),
        ("https://mobile.facebook.com/acme.bank.support", "facebook", "acme.bank.support", "https://www.facebook.com/acme.bank.support"),
        ("https://fb.com/acmebankindia", "facebook", "acmebankindia", "https://www.facebook.com/acmebankindia"),
        ("https://www.facebook.com/profile.php?id=100012345678901", "facebook", "100012345678901", "https://www.facebook.com/profile.php?id=100012345678901"),
        ("https://www.facebook.com/pages/Acme-Bank/123456789012", "facebook", "123456789012", "https://www.facebook.com/profile.php?id=123456789012"),
        ("https://twitter.com/AcmeBank", "x", "acmebank", "https://x.com/acmebank"),
        ("https://x.com/AcmeBank/", "x", "acmebank", "https://x.com/acmebank"),
        ("https://mobile.twitter.com/AcmeBank?lang=en", "x", "acmebank", "https://x.com/acmebank"),
        ("https://www.x.com/acme_bank/status/123", "x", "acme_bank", "https://x.com/acme_bank"),
        ("https://t.me/AcmeBankSupport", "telegram", "acmebanksupport", "https://t.me/acmebanksupport"),
        ("https://telegram.me/acmebank_help/", "telegram", "acmebank_help", "https://t.me/acmebank_help"),
        ("https://t.me/s/acmebankchannel", "telegram", "acmebankchannel", "https://t.me/acmebankchannel"),
        ("t.me/acmebankchannel?start=x", "telegram", "acmebankchannel", "https://t.me/acmebankchannel"),
        ("https://www.linkedin.com/company/acme-bank/", "linkedin", "acme-bank", "https://www.linkedin.com/company/acme-bank"),
        ("https://in.linkedin.com/company/acme-bank?trk=x", "linkedin", "acme-bank", "https://www.linkedin.com/company/acme-bank"),
        ("https://www.linkedin.com/in/jane-doe-123/", "linkedin", "jane-doe-123", "https://www.linkedin.com/in/jane-doe-123"),
        ("https://www.linkedin.com/school/acme-university/", "linkedin", "acme-university", "https://www.linkedin.com/school/acme-university"),
        ("https://www.youtube.com/@AcmeBank", "youtube", "acmebank", "https://www.youtube.com/@acmebank"),
        ("https://m.youtube.com/@AcmeBank/videos", "youtube", "acmebank", "https://www.youtube.com/@acmebank"),
        ("https://www.youtube.com/c/AcmeBankOfficial", "youtube", "acmebankofficial", "https://www.youtube.com/c/acmebankofficial"),
        ("https://www.youtube.com/user/AcmeBank", "youtube", "acmebank", "https://www.youtube.com/user/acmebank"),
        (
            "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv",
            "youtube",
            "UCabcdefghijklmnopqrstuv",
            "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv",
        ),
    ],
)
def test_profile_urls(url, platform, handle, canonical):
    p = parse_profile_url(url)
    assert p.ok, p.reason
    assert (p.kind, p.platform, p.handle, p.canonical_url) == ("profile", platform, handle, canonical)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/explore/tags/acme/",
        "https://www.instagram.com/p/CxYz123/",
        "https://www.instagram.com/reel/CxYz123/",
        "https://www.instagram.com/stories/acme/123/",
        "https://www.instagram.com/",
        "https://www.instagram.com/accounts/login/",
        "https://www.facebook.com/sharer/sharer.php?u=x",
        "https://www.facebook.com/hashtag/acme",
        "https://www.facebook.com/groups/acmefans",
        "https://www.facebook.com/profile.php",
        "https://www.facebook.com/watch/?v=1",
        "https://twitter.com/hashtag/acme",
        "https://twitter.com/share?text=hi",
        "https://x.com/i/flow/login",
        "https://x.com/explore",
        "https://x.com/intent/tweet",
        "https://x.com/",
        "https://t.me/joinchat/AAAAAEabc",
        "https://t.me/+AbCdEf123",
        "https://t.me/share/url?url=x",
        "https://t.me/addstickers/pack",
        "https://t.me/c/123456/78",
        "https://www.linkedin.com/feed/",
        "https://www.linkedin.com/jobs/view/123",
        "https://www.linkedin.com/company/",
        "https://www.youtube.com/watch?v=abc",
        "https://www.youtube.com/shorts/abc",
        "https://www.youtube.com/playlist?list=PL1",
        "https://www.youtube.com/channel/notvalid",
        "https://youtu.be/abc",
        "https://www.youtube.com/",
        "ftp://instagram.com/acme",
        "https://example.com/acme",
        "",
        "not a url",
        "https://instagram.com:abc/acme",
    ],
)
def test_rejected_profile_urls(url):
    p = parse_profile_url(url)
    assert not p.ok and p.reason


def test_rejection_reasons_are_specific():
    assert "not a profile" in parse_profile_url("https://instagram.com/explore").reason
    assert "Invite" in parse_profile_url("https://t.me/+abc").reason
    assert "groups" in parse_profile_url("https://facebook.com/groups/x").reason
    assert "video" in parse_profile_url("https://youtu.be/x").reason
    assert "Unsupported host" in parse_profile_url("https://tiktok.com/@x").reason


def test_userinfo_trick_uses_real_host():
    assert not parse_profile_url("https://instagram.com@evil.com/acme").ok
    assert not parse_profile_url("https://evilinstagram.com/acme").ok
    assert not parse_profile_url("https://instagram.com.evil.com/acme").ok


@pytest.mark.parametrize(
    "url,store,app_id",
    [
        ("https://play.google.com/store/apps/details?id=com.acme.bank", "google_play", "com.acme.bank"),
        ("https://play.google.com/store/apps/details?id=com.acme.bank&hl=en_IN&gl=IN", "google_play", "com.acme.bank"),
        ("market://details?id=com.acme.bank", "google_play", "com.acme.bank"),
        ("https://apps.apple.com/in/app/acme-bank/id1234567890", "apple_app_store", "1234567890"),
        ("https://apps.apple.com/us/app/acme-bank/id1234567890?uo=4", "apple_app_store", "1234567890"),
        ("https://apps.apple.com/app/id1234567890", "apple_app_store", "1234567890"),
        ("https://itunes.apple.com/us/app/acme-bank/id1234567890?mt=8", "apple_app_store", "1234567890"),
        ("https://apkpure.com/acme-bank/com.acme.bank", "apkpure", "com.acme.bank"),
        ("https://apkpure.com/in/acme-bank/com.acme.bank/download", "apkpure", "com.acme.bank"),
        ("https://www.apkmirror.com/apk/acme-corp/acme-bank/", "apkmirror", "acme-corp/acme-bank"),
        ("https://acme-bank.en.uptodown.com/android", "uptodown", "acme-bank"),
        ("https://acme-bank.uptodown.com/android/download", "uptodown", "acme-bank"),
        ("https://acme-bank.en.aptoide.com/app", "aptoide", "acme-bank"),
        ("https://apkcombo.com/acme-bank/com.acme.bank", "apkcombo", "com.acme.bank"),
    ],
)
def test_store_urls(url, store, app_id):
    p = parse_store_url(url)
    assert p.ok, p.reason
    assert (p.kind, p.store, p.handle) == ("store", store, app_id)


def test_store_details():
    p = parse_store_url("https://apps.apple.com/in/app/acme-bank/id1234567890")
    assert p.slug == "acme-bank" and p.canonical_url == "https://apps.apple.com/app/acme-bank/id1234567890"
    assert parse_store_url("https://play.google.com/store/apps/details?id=com.a.b").canonical_url == (
        "https://play.google.com/store/apps/details?id=com.a.b"
    )
    assert parse_store_url("https://apkpure.com/x/com.a.b").is_mirror
    assert not parse_store_url("https://play.google.com/store/apps/details?id=com.a.b").is_mirror


@pytest.mark.parametrize(
    "url",
    [
        "https://play.google.com/store/apps/details",
        "https://play.google.com/store/apps/details?id=notapackage",
        "https://play.google.com/store/apps/developer?id=Acme",
        "https://play.google.com/store/search?q=acme",
        "https://apps.apple.com/in/developer/acme/id123456",
        "https://apps.apple.com/in/app/acme-bank/",
        "https://apkpure.com/",
        "https://www.apkmirror.com/uploads/",
        "https://uptodown.com/android",
        "https://example.com/app",
        "market://details?id=bad",
        "",
    ],
)
def test_rejected_store_urls(url):
    p = parse_store_url(url)
    assert not p.ok and p.reason


def test_parse_url_dispatch():
    assert parse_url("https://play.google.com/store/apps/details?id=com.a.b").kind == "store"
    assert parse_url("https://t.me/acmebank").kind == "profile"
    assert not parse_url("https://play.google.com/store/search?q=x").ok
    assert "Unsupported host" in parse_url("https://example.com/x").reason
