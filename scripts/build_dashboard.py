#!/usr/bin/env python3
"""data/*.json から静的ダッシュボード dashboard/index.html を生成する。

2026-08-19。数字は貯まっているのに見る場所が無く、月次レビューのテキストしか
判断材料が無かった。ここで作るのは「判断する装置」ではなく「記録して気づく装置」。

フォロワー31人・42投稿ではビューの分散が大きすぎて統計的な勝ち負けは決まらない。
そのため次を守る:
  - 平均は使わない。中央値と四分位で出す（1本のバズが全部を歪めるため）
  - n<5 の区分は数字を出さず「サンプル不足」と表示する
  - 取れない指標（リーチ・保存・リンククリック）は画面に置かない

配色は chanko06.com のヒーロー画像から採ったクリーム／木／セージ。

  python3 scripts/build_dashboard.py
"""
import json
import statistics as st
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "dashboard" / "index.html"
JST = timezone(timedelta(hours=9))


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def q(values):
    """中央値・四分位・最大。n<4 では四分位を出さない。"""
    v = sorted(values)
    if not v:
        return None
    out = {"n": len(v), "med": st.median(v), "max": max(v), "min": min(v)}
    if len(v) >= 4:
        out["q1"], out["q3"] = st.quantiles(v, n=4)[0], st.quantiles(v, n=4)[2]
    return out


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def fmt(n):
    return f"{n:,.0f}" if isinstance(n, (int, float)) else "—"


def sparkline(points, w=260, h=46):
    """値の並びを折れ線で。点が2つ未満なら描かない。"""
    vals = [p for _, p in points if p is not None]
    if len(vals) < 2:
        return '<div class="empty-mini">データがまだ足りません</div>'
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    step = w / (len(points) - 1) if len(points) > 1 else w
    pts, i = [], 0
    for _, v in points:
        if v is None:
            i += 1
            continue
        x = i * step
        y = h - (v - lo) / span * (h - 8) - 4
        pts.append(f"{x:.1f},{y:.1f}")
        i += 1
    if len(pts) < 2:
        return '<div class="empty-mini">データがまだ足りません</div>'
    last = pts[-1].split(",")
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
            f'<polyline points="{" ".join(pts)}"/>'
            f'<circle cx="{last[0]}" cy="{last[1]}" r="3"/></svg>')


def bar_row(label, value, maxv, n, note=""):
    pct = (value / maxv * 100) if maxv else 0
    return (f'<div class="bar-row"><div class="bar-label">{esc(label)}'
            f'<span class="n">n={n}</span></div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%"></div></div>'
            f'<div class="bar-val">{fmt(value)}{esc(note)}</div></div>')


def build():
    insights = load("insights.json", [])
    followers = load("followers.json", [])
    posted = load("posted_log.json", [])
    playbook = load("playbook.json", {})

    ok = [r for r in insights if r.get("status") == "ok"]
    views = [r.get("views", 0) for r in ok]
    stats = q(views) or {"n": 0, "med": 0, "max": 0, "min": 0}

    # ---- サマリー ----
    fseries = [(r["date"], r.get("followers")) for r in followers if r.get("followers") is not None]
    pseries = [(r["date"], r.get("profile_views")) for r in followers if r.get("profile_views") is not None]
    cur_followers = fseries[-1][1] if fseries else None
    grew = (fseries[-1][1] - fseries[0][1]) if len(fseries) >= 2 else None
    total_shares = sum(r.get("shares", 0) for r in ok)
    total_likes = sum(r.get("likes", 0) for r in ok)
    reader_replies = sum(r.get("reader_replies", 0) for r in ok)
    last_collect = max((r.get("collected_at", "") for r in insights), default="")[:10]
    last_post = max((e.get("posted_at", "") for e in posted), default="")[:10]
    days_since_post = ""
    if last_post:
        d = (datetime.now(JST).date() - datetime.fromisoformat(last_post).date()).days
        days_since_post = f"{d}日前"

    # ---- 型・テーマ ----
    typed = [r for r in ok if r.get("post_type")]
    by_type, by_theme = {}, {}
    for r in typed:
        by_type.setdefault(r["post_type"], []).append(r.get("views", 0))
        if r.get("theme"):
            by_theme.setdefault(r["theme"], []).append(r.get("views", 0))

    # ---- 記事別 ----
    by_article = {}
    for r in ok:
        t = (r.get("article_title") or "（記事なし）").split("|")[0].strip()
        by_article.setdefault(t, []).append(r.get("views", 0))

    html = TEMPLATE.format(
        generated=datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
        followers=fmt(cur_followers) if cur_followers is not None else "—",
        followers_delta=(f"{grew:+d} / 観測{len(fseries)}日" if grew is not None else "観測日数が足りません"),
        followers_spark=sparkline(fseries),
        profile_views=fmt(pseries[-1][1]) if pseries else "—",
        profile_note=(f"直近{len(pseries)}日ぶん記録" if pseries else "8/19から記録開始"),
        profile_spark=sparkline(pseries),
        posts=len(ok),
        med_views=fmt(stats["med"]),
        max_views=fmt(stats["max"]),
        spread=(f"下位25% {fmt(stats['q1'])} ／ 上位25% {fmt(stats['q3'])}" if "q1" in stats else "四分位を出すには件数が足りません"),
        likes=fmt(total_likes),
        shares=fmt(total_shares),
        replies=fmt(reader_replies),
        last_collect=last_collect or "—",
        last_post=f"{last_post}（{days_since_post}）" if last_post else "—",
        funnel=funnel_block(ok, pseries, fseries),
        type_block=group_block(by_type, "型", "投稿するときに型を記録し始めたのは 2026-08-19 です。それ以前の42件は記録が残っておらず、復元もできませんでした。次の投稿から埋まります。"),
        theme_block=group_block(by_theme, "テーマ", "テーマも同じく 2026-08-19 から記録されます。"),
        article_block=article_block(by_article),
        clicks_block=clicks_block(load("clicks.json", [])),
        ledger=ledger_rows(ok),
        weights=weight_rows(playbook),
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"[ok] {OUT} を書き出しました（投稿{len(ok)}件 / フォロワー観測{len(fseries)}日）")


def funnel_block(ok, pseries, fseries):
    """投稿ビュー → プロフィール表示 → フォロー増。期間がそろわない点は明記する。"""
    if not pseries:
        return ('<div class="empty">プロフィール表示の記録は 2026-08-19 に始めたばかりです。'
                '数日ぶん貯まると、ここに「投稿を見た人のうち何%がプロフィールまで来たか」が出ます。</div>')
    days = [d for d, _ in pseries]
    pv = sum(v for _, v in pseries)
    start, end = min(days), max(days)
    vin = sum(r.get("views", 0) for r in ok if start <= (r.get("posted_at") or "")[:10] <= end)
    fol = [v for d, v in fseries if start <= d <= end]
    delta = (fol[-1] - fol[0]) if len(fol) >= 2 else None
    rows = [
        ("投稿が見られた", fmt(vin) if vin else "0",
         "この期間の投稿ぶん" if vin else "この期間は投稿がありません"),
        ("プロフィールを見られた", fmt(pv),
         f"投稿ビューの {pv / vin * 100:.1f}%" if vin else "投稿以外の流入も含みます"),
        ("フォローされた", (f"{delta:+d}" if delta is not None else "—"), "純増（解除も含む）"),
    ]
    cells = "".join(
        f'<div class="funnel-step"><div class="funnel-num">{esc(v)}</div>'
        f'<div class="funnel-label">{esc(l)}</div><div class="funnel-note">{esc(n)}</div></div>'
        for l, v, n in rows)
    return (f'<div class="funnel">{cells}</div>'
            f'<p class="caveat">期間 {start} 〜 {end}。フォロワーは日次スナップショットのため、'
            f'この期間の投稿以外の影響も含みます。因果ではなく傾向として読んでください。</p>')


def group_block(groups, kind, empty_msg):
    if not groups:
        return f'<div class="empty">{esc(empty_msg)}</div>'
    rows, enough = [], False
    maxv = max((st.median(v) for v in groups.values()), default=1)
    for k, v in sorted(groups.items(), key=lambda x: -st.median(x[1])):
        if len(v) < 5:
            rows.append(f'<div class="bar-row muted"><div class="bar-label">{esc(k)}'
                        f'<span class="n">n={len(v)}</span></div>'
                        f'<div class="bar-track"></div>'
                        f'<div class="bar-val">サンプル不足</div></div>')
            continue
        enough = True
        rows.append(bar_row(k, st.median(v), maxv, len(v), "  中央値"))
    note = "" if enough else f'<p class="caveat">どの{kind}も5件に届いていないため、数字での比較はまだできません。</p>'
    return "".join(rows) + note


def clicks_block(records):
    """URL別のクリック。送客実験が効いているかを見る唯一の自動指標。"""
    if not records:
        return ('<div class="empty">クリックの記録は 2026-08-20 に始めました。'
                '送客投稿を出した翌日から、どのURLが押されたかがここに出ます。</div>')
    total = {}
    for r in records:
        for u, v in (r.get("urls") or {}).items():
            total[u] = total.get(u, 0) + v
    days = len(records)
    if not total:
        return (f'<div class="empty">観測{days}日ぶん記録していますが、クリックはまだ0件です。'
                'リンク付き投稿を出していない期間はこうなります。</div>')
    rows, maxv = [], max(total.values())
    for u, v in sorted(total.items(), key=lambda x: -x[1])[:10]:
        # UTMは表示上たたむ。どの投稿かは utm_content で区別している
        label = u.split("?")[0].replace("https://", "")
        tag = ""
        if "utm_content=" in u:
            tag = "  " + u.split("utm_content=")[1].split("&")[0]
        rows.append(bar_row(label[:38], v, maxv, days, tag))
    return "".join(rows) + (f'<p class="caveat">観測{days}日ぶんの合計。'
                            'クリックはURL単位でしか取れないため、同じ記事でもUTMが違えば別の行になります。</p>')


def article_block(by_article):
    items = [(t, v) for t, v in by_article.items() if v]
    if not items:
        return '<div class="empty">データがありません</div>'
    items.sort(key=lambda x: -st.median(x[1]))
    maxv = st.median(items[0][1]) or 1
    top = items[:8]
    return "".join(bar_row(t[:34], st.median(v), maxv, len(v), "  中央値") for t, v in top)


def ledger_rows(ok):
    rows = []
    for r in sorted(ok, key=lambda x: x.get("posted_at", ""), reverse=True):
        d = (r.get("posted_at") or "")[:10]
        title = (r.get("article_title") or "").split("|")[0].strip()[:30]
        rows.append(
            f"<tr><td class='mono'>{esc(d)}</td>"
            f"<td>{esc(title)}</td>"
            f"<td class='mono t'>{esc(r.get('post_type') or '—')}</td>"
            f"<td class='mono num'>{fmt(r.get('views', 0))}</td>"
            f"<td class='mono num'>{fmt(r.get('likes', 0))}</td>"
            f"<td class='mono num'>{fmt(r.get('reader_replies', 0))}</td>"
            f"<td class='mono num'>{fmt(r.get('shares', 0))}</td></tr>")
    return "".join(rows)


def weight_rows(playbook):
    types = playbook.get("types", {})
    if not types:
        return ""
    total = sum(t.get("weight", 0) for t in types.values()) or 1
    return "".join(
        f'<div class="chip-row"><span class="chip">{esc(k)}</span>'
        f'<span class="chip-val">{v.get("weight", 0)} / {total}</span></div>'
        for k, v in sorted(types.items(), key=lambda x: -x[1].get("weight", 0)))


TEMPLATE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Threads 運用ダッシュボード</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📊</text></svg>">
<style>
  *,*::before,*::after{{box-sizing:border-box}}
  :root{{
    --paper:#F7F3E8; --surface:#FFFDF8; --sunk:#F0E9DA;
    --line:#E2D8C4; --line-soft:#EDE5D6;
    --ink:#2A2118; --ink-mid:#6B5D4B; --ink-faint:#9A8B76;
    --sage:#6E7A45; --sage-soft:#E8EBDA;
    --caramel:#B8792F; --caramel-soft:#F6E7D2;
    --serif:"Hiragino Mincho ProN","Yu Mincho",serif;
    --sans:"Hiragino Kaku Gothic ProN","Yu Gothic","Noto Sans JP",system-ui,sans-serif;
    --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
  }}
  @media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{
    --paper:#17130E; --surface:#201B14; --sunk:#1A150F;
    --line:#3A3126; --line-soft:#2C251C;
    --ink:#F2EBDD; --ink-mid:#BCAF9B; --ink-faint:#8B7F6C;
    --sage:#A9B575; --sage-soft:#262C1A;
    --caramel:#D9A45E; --caramel-soft:#2E2416;
  }}}}
  body{{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);line-height:1.75;-webkit-text-size-adjust:100%}}
  .wrap{{max-width:60rem;margin:0 auto;padding:2.5rem 1rem 5rem;display:flex;flex-direction:column;gap:2.25rem}}
  header{{display:flex;flex-direction:column;gap:.4rem}}
  .eyebrow{{font-family:var(--mono);font-size:.7rem;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-faint)}}
  h1{{font-family:var(--serif);font-size:clamp(1.6rem,5vw,2.1rem);font-weight:600;margin:0;line-height:1.35}}
  .sub{{margin:0;color:var(--ink-mid);font-size:.9rem;max-width:40em}}
  section{{display:flex;flex-direction:column;gap:.9rem}}
  h2{{font-family:var(--serif);font-size:1.15rem;font-weight:600;margin:0;padding-bottom:.5rem;border-bottom:2px solid var(--ink)}}
  .lede{{margin:0;font-size:.88rem;color:var(--ink-mid);max-width:44em}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(11rem,1fr));gap:1px;background:var(--line-soft);border:1px solid var(--line-soft);border-radius:6px;overflow:hidden}}
  .card{{background:var(--surface);padding:.9rem 1rem 1rem;display:flex;flex-direction:column;gap:.15rem}}
  .card .k{{font-family:var(--mono);font-size:.66rem;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-faint)}}
  .card .v{{font-family:var(--mono);font-size:1.75rem;font-weight:600;font-variant-numeric:tabular-nums;line-height:1.25;color:var(--sage)}}
  .card .n{{font-size:.75rem;color:var(--ink-faint)}}
  .spark{{width:100%;height:46px;margin-top:.3rem}}
  .spark polyline{{fill:none;stroke:var(--caramel);stroke-width:1.8}}
  .spark circle{{fill:var(--caramel)}}
  .empty,.empty-mini{{background:var(--sunk);border:1px dashed var(--line);border-radius:6px;padding:.9rem 1rem;font-size:.85rem;color:var(--ink-mid)}}
  .empty-mini{{padding:.4rem .6rem;font-size:.72rem;margin-top:.3rem}}
  .funnel{{display:grid;grid-template-columns:repeat(auto-fit,minmax(10rem,1fr));gap:1px;background:var(--line-soft);border:1px solid var(--line-soft);border-radius:6px;overflow:hidden}}
  .funnel-step{{background:var(--surface);padding:1rem;display:flex;flex-direction:column;gap:.1rem}}
  .funnel-num{{font-family:var(--mono);font-size:1.6rem;font-weight:600;color:var(--sage);font-variant-numeric:tabular-nums}}
  .funnel-label{{font-size:.85rem;font-weight:700}}
  .funnel-note{{font-size:.75rem;color:var(--ink-faint)}}
  .caveat{{margin:0;font-size:.78rem;color:var(--ink-faint);border-left:2px solid var(--line);padding-left:.7rem}}
  .bar-row{{display:grid;grid-template-columns:11rem 1fr 7.5rem;gap:.75rem;align-items:center;padding:.35rem 0;font-size:.85rem}}
  .bar-row.muted{{color:var(--ink-faint)}}
  .bar-label{{display:flex;align-items:baseline;gap:.4rem}}
  .bar-label .n{{font-family:var(--mono);font-size:.7rem;color:var(--ink-faint)}}
  .bar-track{{background:var(--sunk);border-radius:3px;height:12px;overflow:hidden}}
  .bar-fill{{background:var(--sage);height:100%;border-radius:3px}}
  .bar-val{{font-family:var(--mono);font-size:.78rem;color:var(--ink-mid);text-align:right;font-variant-numeric:tabular-nums}}
  .chips{{display:flex;flex-wrap:wrap;gap:.4rem}}
  .chip-row{{display:inline-flex;align-items:center;gap:.35rem;background:var(--sage-soft);border-radius:999px;padding:.15rem .6rem}}
  .chip{{font-size:.78rem}}
  .chip-val{{font-family:var(--mono);font-size:.72rem;color:var(--ink-mid)}}
  .table-wrap{{overflow-x:auto;border:1px solid var(--line-soft);border-radius:6px;background:var(--surface)}}
  table{{border-collapse:collapse;width:100%;font-size:.82rem;min-width:38rem}}
  th,td{{padding:.45rem .7rem;text-align:left;border-bottom:1px solid var(--line-soft);white-space:nowrap}}
  th{{font-family:var(--mono);font-size:.68rem;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-faint);background:var(--sunk);position:sticky;top:0}}
  td.mono{{font-family:var(--mono);font-variant-numeric:tabular-nums}}
  td.num{{text-align:right}}
  td.t{{color:var(--caramel)}}
  tbody tr:last-child td{{border-bottom:none}}
  footer{{border-top:1px solid var(--line);padding-top:1rem;font-family:var(--mono);font-size:.74rem;color:var(--ink-faint);line-height:1.7}}
  @media (max-width:40rem){{
    .bar-row{{grid-template-columns:1fr;gap:.15rem}}
    .bar-val{{text-align:left}}
  }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <div class="eyebrow">@chanko_lifehack ／ Threads</div>
  <h1>運用ダッシュボード</h1>
  <p class="sub">投稿ビュー・プロフィール表示・フォロワーを、APIで取れるぶんだけ並べたものです。勝ち負けを決める画面ではなく、気づくための画面として作っています。平均は使わず中央値で出し、5件に満たない区分は「サンプル不足」と表示します。</p>
</header>

<section>
  <h2>1. 今の状態</h2>
  <div class="cards">
    <div class="card"><div class="k">フォロワー</div><div class="v">{followers}</div><div class="n">{followers_delta}</div>{followers_spark}</div>
    <div class="card"><div class="k">プロフィール表示</div><div class="v">{profile_views}</div><div class="n">{profile_note}</div>{profile_spark}</div>
    <div class="card"><div class="k">投稿ビュー 中央値</div><div class="v">{med_views}</div><div class="n">最大 {max_views} ／ {spread}</div></div>
    <div class="card"><div class="k">投稿数</div><div class="v">{posts}</div><div class="n">最後の投稿 {last_post}</div></div>
    <div class="card"><div class="k">いいね 合計</div><div class="v">{likes}</div><div class="n">読者からの返信 {replies}</div></div>
    <div class="card"><div class="k">shares 合計</div><div class="v">{shares}</div><div class="n">外に持ち出された回数</div></div>
  </div>
</section>

<section>
  <h2>2. フォローまでのファネル</h2>
  <p class="lede">KPIはフォロワーなので、投稿が見られた数より「プロフィールまで来たか」の方がフォローに近い先行指標です。どの段で落ちているかで打ち手が変わります。</p>
  {funnel}
</section>

<section>
  <h2>3. 型別の再現性</h2>
  <p class="lede">どの型が効いているか。ここが埋まると、月次レビューが感覚ではなく数字で判断できるようになります。</p>
  {type_block}
  <p class="lede">いまの配合（playbook の重み）:</p>
  <div class="chips">{weights}</div>
</section>

<section>
  <h2>4. テーマ別</h2>
  {theme_block}
</section>

<section>
  <h2>5. どの記事から出した投稿が伸びたか</h2>
  <p class="lede">記事の選定は現在ランダムです。ここで差が見えるなら、伸びた記事のテーマを優先する余地があります。</p>
  {article_block}
</section>

<section>
  <h2>6. 送客の実験結果（URL別クリック）</h2>
  <p class="lede">リンクは全体の10〜15%だけに付けます。押されたかどうかは、投稿単位ではなくURL単位でしか取れないため、投稿ごとに固有のUTMを振って区別しています。</p>
  {clicks_block}
</section>

<section>
  <h2>7. 投稿台帳</h2>
  <p class="lede">1行1投稿。型の列は 2026-08-19 以降の投稿から埋まります。</p>
  <div class="table-wrap">
    <table>
      <thead><tr><th>投稿日</th><th>記事</th><th>型</th><th>views</th><th>likes</th><th>返信</th><th>shares</th></tr></thead>
      <tbody>{ledger}</tbody>
    </table>
  </div>
</section>

<footer>
  生成 {generated} JST ／ 最終収集 {last_collect}<br>
  取れない指標（リーチ・保存・リンククリック・非フォロワー比率）は Threads API に存在しないため、この画面には置いていません。<br>
  フォロワー属性は100人を超えるとAPIが返し始めます。
</footer>

</div>
</body>
</html>
"""


if __name__ == "__main__":
    build()
