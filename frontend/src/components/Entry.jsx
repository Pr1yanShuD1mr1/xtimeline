import { bare, initial } from "../lib/format.js";

const LINKABLE = /(https?:\/\/[^\s]+|@[A-Za-z0-9_]{1,15}|#[\p{L}\p{N}_]+)/gu;

function linkify(text) {
  return String(text).split(LINKABLE).map((part, i) => {
    if (/^https?:\/\//.test(part)) {
      return <a key={i} href={part} target="_blank" rel="noreferrer">{part}</a>;
    }
    if (/^@[A-Za-z0-9_]{1,15}$/.test(part)) {
      return (
        <a key={i} href={`https://x.com/${part.slice(1)}`} target="_blank" rel="noreferrer">
          {part}
        </a>
      );
    }
    if (part.startsWith("#") && part.length > 1) {
      return (
        <a key={i} href={`https://x.com/hashtag/${encodeURIComponent(part.slice(1))}`}
           target="_blank" rel="noreferrer">
          {part}
        </a>
      );
    }
    return part;
  });
}

function when(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso || "";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Exact figures here: the reference prints 6,945, not 6.9K.
const exact = (v) => (typeof v === "number" ? v.toLocaleString() : "0");

function stamp(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso || "";
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const date = d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  return `${time} · ${date}`;
}

function Media({ urls }) {
  if (!urls || urls.length === 0) return null;
  const shown = urls.slice(0, 4);
  if (shown.length === 1) {
    return (
      <figure className="entry-media">
        <img src={shown[0]} alt="" loading="lazy" referrerPolicy="no-referrer"
             onError={(e) => { const f = e.currentTarget.closest("figure"); if (f) f.style.display = "none"; }} />
      </figure>
    );
  }
  return (
    <div className="entry-media multi">
      {shown.map((src, i) => (
        <div className="cell" key={src.slice(-16) + i}>
          <img src={src} alt="" loading="lazy" referrerPolicy="no-referrer"
               onError={(e) => { const c = e.currentTarget.closest(".cell"); if (c) c.style.display = "none"; }} />
        </div>
      ))}
    </div>
  );
}

export default function Entry({ post }) {
  const handle = bare(post.account || post.author);
  const name = post.display_name || bare(post.author) || handle;
  const views = post.metrics?.views || 0;

  return (
    <article className="post entry">
      <div className="avatar" aria-hidden="true">
        {post.avatar ? (
          <img src={post.avatar} alt="" referrerPolicy="no-referrer"
               onError={(e) => { e.currentTarget.style.display = "none"; }} />
        ) : (
          <span className="initial">{initial(handle)}</span>
        )}
      </div>

      <div style={{ minWidth: 0 }}>
        <div className="entry-head">
          <span className="entry-name">{name}</span>
          {post.verified && <span className="entry-tick" title="Verified on X">✓</span>}
          <span className="entry-handle">@{handle}</span>
          {post.pinned && <span className="pill pill-pin">pinned</span>}
          {post.is_repost && <span className="pill pill-repost">repost</span>}
          <span className="entry-when">{when(post.datetime)}</span>
        </div>

        {post.text && <p className="entry-text">{linkify(post.text)}</p>}

        <Media urls={post.media} />

        <p className="entry-stamp">{stamp(post.datetime)}</p>

        <dl className="metrics">
          <div className="cell"><dt>replies</dt><dd>{exact(post.metrics?.replies ?? 0)}</dd></div>
          <div className="cell"><dt>reposts</dt><dd>{exact(post.metrics?.reposts ?? 0)}</dd></div>
          <div className="cell"><dt>likes</dt><dd>{exact(post.metrics?.likes ?? 0)}</dd></div>
          <div className="cell"><dt>saved</dt><dd>{exact(post.metrics?.bookmarks ?? 0)}</dd></div>
          <div className="cell"><dt>views</dt><dd>{exact(views)}</dd></div>
          <div className="open">
            <a href={post.url} target="_blank" rel="noreferrer">open on x ↗</a>
          </div>
        </dl>
      </div>
    </article>
  );
}
