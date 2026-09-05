#!/usr/bin/env python3
"""Generate narration for a journal post with Workers AI (Deepgram Aura 2).

Usage:  scripts/narrate.py src/pages/posts/007-the-clone.mdx [--voice orion]

Reads the post, strips it down to spoken text, sends it to Workers AI in
paragraph-sized chunks, and stitches the pieces into public/audio/NNN.mp3.
Auth comes from the local wrangler login (~/.config/.wrangler/config/default.toml)
or CLOUDFLARE_API_TOKEN. Requires ffmpeg.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ACCOUNT = "23ee3da2973c76c9d8a3eb7169fd4802"
MODEL = "@cf/deepgram/aura-2-en"
DEFAULT_VOICE = "arcas"
MAX_CHARS = 1500
PAUSE_PARAGRAPH = 0.55
PAUSE_HEADING = 1.1


def token():
    if os.environ.get("CLOUDFLARE_API_TOKEN"):
        return os.environ["CLOUDFLARE_API_TOKEN"]
    cfg = Path.home() / ".config/.wrangler/config/default.toml"
    m = re.search(r'oauth_token\s*=\s*"([^"]+)"', cfg.read_text())
    if not m:
        sys.exit("no Cloudflare token found")
    return m.group(1)


def spoken_blocks(mdx: str):
    """Turn the post body into a list of (text, pause_after) blocks."""
    fm = re.match(r"---\n(.*?)\n---\n", mdx, re.S)
    front = dict(re.findall(r'^(\w+):\s*"?(.*?)"?\s*$', fm.group(1), re.M))
    body = mdx[fm.end():]

    blocks = []
    title = front.get("title", "")
    subtitle = front.get("subtitle", "")
    blocks.append((f"{title}. {subtitle}", PAUSE_HEADING))

    # drop fenced code blocks entirely; they don't read aloud
    body = re.sub(r"```.*?```", "", body, flags=re.S)

    for para in re.split(r"\n\s*\n", body):
        p = para.strip()
        if not p or p == "---":
            continue
        pause = PAUSE_PARAGRAPH
        if p.startswith("#"):
            p = p.lstrip("#").strip()
            if not p.endswith((".", "?", "!")):
                p += "."
            pause = PAUSE_HEADING
        # list items -> sentences
        p = re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", p, flags=re.M)
        # blockquotes
        p = re.sub(r"^\s*>\s?", "", p, flags=re.M)
        # links [text](url) -> text
        p = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", p)
        # inline code
        p = re.sub(r"`([^`]+)`", r"\1", p)
        # emphasis markers
        p = re.sub(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1", r"\2", p, flags=re.S)
        # em dashes read better as a comma-pause
        p = p.replace(" — ", ", ").replace("—", ", ")
        p = re.sub(r"\s+", " ", p).strip()
        if p:
            blocks.append((p, pause))
    return front, blocks


def chunk(blocks):
    """Merge blocks into requests under MAX_CHARS, keeping pause info."""
    out, cur, cur_pause = [], "", PAUSE_PARAGRAPH
    for text, pause in blocks:
        if cur and (len(cur) + len(text) + 1 > MAX_CHARS or cur_pause == PAUSE_HEADING):
            out.append((cur, cur_pause))
            cur = ""
        cur = f"{cur} {text}".strip() if cur else text
        cur_pause = pause
    if cur:
        out.append((cur, cur_pause))
    return out


def tts(text, voice, tok, dest):
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/run/{MODEL}",
        data=json.dumps({"text": text, "speaker": voice}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
    )
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                if r.headers.get_content_type() != "audio/mpeg":
                    sys.exit(f"unexpected response: {r.read()[:300]}")
                Path(dest).write_bytes(r.read())
                return
        except (TimeoutError, OSError) as e:  # stalled connection or transient error
            last = e
            print(f"  retry {attempt + 1}: {e}", file=sys.stderr)
    sys.exit(f"tts failed after retries: {last}")


def main():
    args = sys.argv[1:]
    voice = DEFAULT_VOICE
    if "--voice" in args:
        i = args.index("--voice")
        voice = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1:
        sys.exit(__doc__)
    src = Path(args[0])
    front, blocks = spoken_blocks(src.read_text())
    number = int(front["number"])
    dest = Path("public/audio") / f"{number:03d}.mp3"
    tok = token()

    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, (text, pause) in enumerate(chunk(blocks)):
            part = Path(tmp) / f"{i:03d}.mp3"
            tts(text, voice, tok, part)
            parts.append((part, pause))
            print(f"  chunk {i}: {len(text)} chars", file=sys.stderr)

        # concat with silences between chunks, normalise to one clean mp3
        listing = Path(tmp) / "list.txt"
        lines = []
        for i, (part, pause) in enumerate(parts):
            lines.append(f"file '{part}'")
            if i < len(parts) - 1:
                sil = Path(tmp) / f"sil{i}.mp3"
                subprocess.run(
                    ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                     "-t", str(pause), "-c:a", "libmp3lame", "-b:a", "48k", str(sil)],
                    check=True,
                )
                lines.append(f"file '{sil}'")
        listing.write_text("\n".join(lines) + "\n")
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-c:a", "libmp3lame", "-b:a", "64k", "-ar", "24000", "-ac", "1",
             "-metadata", f"title={front.get('title', '')}", "-metadata", "artist=Claude",
             "-metadata", "album=Claude's Journal", str(dest)],
            check=True,
        )
    print(f"wrote {dest} ({voice})")


if __name__ == "__main__":
    main()
