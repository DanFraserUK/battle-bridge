"""Is a newer image waiting?  Asks the registry for the tag's digest with a
HEAD request (no download, and Docker Hub doesn't count HEADs against its
limit) and compares it with the digest of the image on disk."""

import json
import re
import urllib.error
import urllib.request

ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])

# lscr.io is a front for GitHub's registry.
ALIASES = {"lscr.io": "ghcr.io", "docker.io": "registry-1.docker.io",
           "index.docker.io": "registry-1.docker.io"}


def split_ref(image):
    """'lscr.io/linuxserver/plex:latest' -> ('ghcr.io', 'linuxserver/plex', 'latest')."""
    name, tag = image, "latest"
    if "@" in name:
        name = name.split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name, tag = name.rsplit(":", 1)
    first = name.split("/", 1)[0]
    if "." in first or ":" in first or first == "localhost":
        registry, repo = name.split("/", 1)
    else:
        registry, repo = "registry-1.docker.io", name
    if registry in ALIASES:
        registry = ALIASES[registry]
    if registry == "registry-1.docker.io" and "/" not in repo:
        repo = "library/" + repo
    return registry, repo, tag


def _token(challenge, timeout):
    params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
    realm = params.pop("realm", None)
    if not realm or not realm.startswith("https://"):
        return None
    query = "&".join(f"{k}={urllib.request.quote(v)}" for k, v in params.items())
    with urllib.request.urlopen(f"{realm}?{query}", timeout=timeout) as r:
        data = json.loads(r.read())
    return data.get("token") or data.get("access_token")


def remote_digest(image, timeout=15):
    registry, repo, tag = split_ref(image)
    url = f"https://{registry}/v2/{repo}/manifests/{tag}"
    headers = {"Accept": ACCEPT}
    for attempt in range(2):
        req = urllib.request.Request(url, method="HEAD", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.headers.get("Docker-Content-Digest")
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 0:
                token = _token(e.headers.get("WWW-Authenticate", ""), timeout)
                if not token:
                    raise
                headers["Authorization"] = f"Bearer {token}"
                continue
            raise
    return None


def local_digests(repo_digests):
    """RepoDigests entries like 'pihole/pihole@sha256:...' -> set of digests."""
    return {d.split("@", 1)[1] for d in repo_digests or [] if "@" in d}
