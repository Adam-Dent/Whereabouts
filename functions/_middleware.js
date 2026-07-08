// Redirect the retired Cloudflare Pages *.pages.dev host to the canonical
// custom domain (whereabouts.adamdent.uk) with a permanent redirect, and pass
// everything else straight through to the static assets. Pages Functions live
// at the project root; `wrangler pages deploy docs/` compiles this alongside
// the docs/ assets.
export async function onRequest(context) {
  const url = new URL(context.request.url);
  if (url.hostname.endsWith(".pages.dev")) {
    url.protocol = "https:";
    url.hostname = "whereabouts.adamdent.uk";
    return Response.redirect(url.toString(), 301);
  }
  return context.next();
}
