import os
import requests

BASE = "https://graph.instagram.com"
TOKEN = os.environ["INSTAGRAM_ACCESS_TOKEN"]


def _get(path, **params):
    params["access_token"] = TOKEN
    r = requests.get(f"{BASE}/{path}", params=params, timeout=30)
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message", "Instagram API xatosi"))
    return data


def get_profile():
    return _get("me", fields="username,followers_count,follows_count,media_count")


def get_recent_posts(limit=10):
    data = _get(
        "me/media",
        fields="id,caption,media_type,permalink,timestamp,like_count,comments_count",
        limit=limit,
    )
    return data.get("data", [])


def get_post_insights(media_id):
    try:
        data = _get(f"{media_id}/insights", metric="reach,saved,shares")
        return {m["name"]: m["values"][0]["value"] for m in data.get("data", [])}
    except RuntimeError:
        return {}


def build_summary(limit=10):
    profile = get_profile()
    posts = get_recent_posts(limit)
    for p in posts:
        p["insights"] = get_post_insights(p["id"])
    return {"profile": profile, "posts": posts}
