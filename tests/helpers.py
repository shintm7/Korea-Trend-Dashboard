"""테스트용 RSS 생성 도구."""
import datetime as dt
import email.utils
from pathlib import Path


def rfc822(ts: float) -> str:
    return email.utils.format_datetime(dt.datetime.fromtimestamp(ts, dt.timezone.utc))


def rss(items, encoding="utf-8", dc_date=False, truncate_at=None) -> bytes:
    """items = [(제목, epoch초)]"""
    parts = [f'<?xml version="1.0" encoding="{encoding}"?>',
             '<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>t</title>']
    for i, (title, ts) in enumerate(items):
        if dc_date:
            stamp = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            date = f"<dc:date>{stamp}</dc:date>"
        else:
            date = f"<pubDate>{rfc822(ts)}</pubDate>"
        parts.append(f"<item><title>{title}</title><link>https://example.com/a/{i}</link>{date}</item>")
    parts.append("</channel></rss>")
    text = "\n".join(parts)
    if truncate_at is not None:
        text = text[:truncate_at]
    return text.encode(encoding)


def write_feed(directory: Path, feed_id: str, items, **kw):
    Path(directory).mkdir(parents=True, exist_ok=True)
    (Path(directory) / f"{feed_id}.xml").write_bytes(rss(items, **kw))
