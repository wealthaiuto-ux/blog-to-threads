/**
 * ダッシュボードをパスワードで閉じるための入口。
 *
 * Cloudflare Pages は既定で全世界公開になる。このサイトにはリポジトリ名・
 * ワークフロー・アカウント運用の弱点まで載るので、素のまま出すわけにはいかない。
 *
 * DASHBOARD_PASSWORD が未設定のときは、静的ファイルを一切返さず 503 を返す。
 * 「デプロイしたがパスワードを入れ忘れ、その間だけ全世界に見えていた」を防ぐため、
 * 未設定はエラーであって「認証なしで通す」ではない。
 *
 * パスワードの設定（本人が1回だけ実行する）:
 *   npx wrangler pages secret put DASHBOARD_PASSWORD --project-name threads-dashboard
 */

function safeEqual(a, b) {
  // 長さで早期 return すると総当たりに情報を与えるので、長さも含めて定数時間で比べる
  const enc = new TextEncoder();
  const x = enc.encode(a);
  const y = enc.encode(b);
  let diff = x.length ^ y.length;
  const len = Math.max(x.length, y.length);
  for (let i = 0; i < len; i++) {
    diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  }
  return diff === 0;
}

const DENY = {
  "WWW-Authenticate": 'Basic realm="threads-dashboard", charset="UTF-8"',
  "Cache-Control": "no-store",
  "Content-Type": "text/plain; charset=utf-8",
};

export default {
  async fetch(request, env) {
    const expected = env.DASHBOARD_PASSWORD;
    if (!expected) {
      return new Response(
        "DASHBOARD_PASSWORD が未設定です。設定するまでこのサイトは何も表示しません。",
        { status: 503, headers: { "Cache-Control": "no-store", "Content-Type": "text/plain; charset=utf-8" } },
      );
    }

    const header = request.headers.get("Authorization") || "";
    const [scheme, encoded] = header.split(" ");
    if (scheme === "Basic" && encoded) {
      let decoded = "";
      try {
        decoded = atob(encoded);
      } catch {
        decoded = "";
      }
      const sep = decoded.indexOf(":");
      const password = sep >= 0 ? decoded.slice(sep + 1) : "";
      if (password && safeEqual(password, expected)) {
        const res = await env.ASSETS.fetch(request);
        const out = new Response(res.body, res);
        // 認証の内側にあるものを共有キャッシュに残さない
        out.headers.set("Cache-Control", "private, no-store");
        out.headers.set("X-Robots-Tag", "noindex, nofollow");
        return out;
      }
    }
    return new Response("認証が必要です", { status: 401, headers: DENY });
  },
};
