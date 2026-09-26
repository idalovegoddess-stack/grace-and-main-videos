"""The Buffer refill robot. Runs once a day on GitHub, no Claude involved.

It counts how many posts are waiting in Buffer for each channel in posts.json,
and tops the queue back up from the bank of finished posts, oldest first,
until the queue is full or the bank runs out. It remembers what it already
sent in posted.json so nothing goes out twice.

Needs one secret: BUFFER_API_KEY (from Buffer → Settings → API).
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://api.buffer.com"
QUEUE_TARGET = int(os.environ.get("QUEUE_TARGET", "9"))  # free plan holds 10; leave one slot for Ida
VIDEO_BASE = os.environ["VIDEO_BASE_URL"].rstrip("/")  # public web address of social/videos/


KEY = os.environ.get("BUFFER_API_KEY", "").strip().strip('"').strip("'")
if KEY.lower().startswith("bearer "):
    KEY = KEY[7:].strip()


def gql(query, variables=None):
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Authorization": f"Bearer {KEY}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.load(r)
    if body.get("errors"):
        sys.exit(f"Buffer said no: {body['errors']}")
    return body["data"]


def waiting(org_id, channel_id):
    data = gql("""query($org: OrganizationId!, $ch: [ChannelId!]) {
        posts(first: 50, input: {organizationId: $org,
              filter: {channelIds: $ch, status: [scheduled, needs_approval]}}) {
          edges { node { id } } } }""", {"org": org_id, "ch": [channel_id]})
    return len(data["posts"]["edges"])


def send(post, channel_id, mode):
    v = post.get("version", 1)
    url = f"{VIDEO_BASE}/{post['id']}.mp4" if v == 1 else f"{VIDEO_BASE}/{post['id']}-v{v}.mp4"
    data = gql("""mutation($input: CreatePostInput!) { createPost(input: $input) {
        __typename
        ... on PostActionSuccess { post { id dueAt } }
        ... on MutationError { message } } }""", {"input": {
        "channelId": channel_id,
        "text": post["caption"],
        "assets": [{"video": {"url": url}}],
        "mode": "addToQueue",
        "schedulingType": mode,
        "metadata": {"tiktok": {"isAiGenerated": False}},
        "source": "grace-and-main-refill",
    }})
    res = data["createPost"]
    if res["__typename"] != "PostActionSuccess":
        return None, res.get("message", res["__typename"])
    return res["post"], None


def main():
    if not KEY:
        sys.exit("The BUFFER_API_KEY box on GitHub is empty. Paste the Buffer key into it.")
    print(f"Buffer key found: {len(KEY)} characters long.")
    try:
        gql("query { account { id } }")
    except urllib.error.HTTPError as e:
        sys.exit(f"Buffer rejected the key ({e.code}). Make a new key in Buffer and paste it into the GitHub box again.")
    cfg = json.loads((HERE / "posts.json").read_text())
    posts = [p for p in cfg["posts"] if p.get("ready")]
    log_path = HERE / "posted.json"
    posted = json.loads(log_path.read_text()) if log_path.exists() else {}
    org, ch = cfg["buffer"]["organizationId"], cfg["buffer"]["tiktokChannelId"]
    mode = cfg["buffer"].get("schedulingType", "automatic")

    have = waiting(org, ch)
    room = max(0, QUEUE_TARGET - have)
    fresh = [p for p in posts if p["id"] not in posted]
    print(f"{have} waiting in Buffer, room for {room}, {len(fresh)} unsent in the bank.")

    for p in fresh[:room]:
        made, err = send(p, ch, mode)
        if err:
            print(f"STOPPED on {p['id']}: {err}")
            break
        posted[p["id"]] = made["id"]
        print(f"queued {p['id']} (goes out {made.get('dueAt')})")

    log_path.write_text(json.dumps(posted, indent=1) + "\n")
    left = len([p for p in posts if p["id"] not in posted])
    if left < 5:
        print(f"LOW BANK: only {left} posts left. Time to make more.")


if __name__ == "__main__":
    main()
