/**
 * Builds a `/g/<groupSlug>` sidebar target, or sends the visitor to the login
 * page when there is no group to link to.
 *
 * The sidebar resolves its slug as `route.params.groupSlug || user?.groupSlug
 * || ""`. On a route that carries no slug of its own — `/shopping-lists`,
 * `/household/...`, `/user/...` — a visitor with no session leaves every term
 * empty, and interpolating that into `/g/${slug}` produced `/g/`, which matches
 * no route. The sidebar answered a click with a 404 instead of a way to sign in.
 */
export function groupLink(groupSlug: string | null | undefined, path = ""): string {
  return groupSlug ? `/g/${groupSlug}${path}` : "/login";
}
