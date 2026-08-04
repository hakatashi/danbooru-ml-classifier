"""
Tests that every route under a protected prefix in api.py carries
Depends(require_admin), and that public routes do not.

Importing `api` at module scope is safe here: get_db()/_get_qdrant_client()/
_get_firebase_app() are all lazy singletons, so importing the module does not
touch MongoDB, Qdrant, or Firebase -- it only registers FastAPI routes and
runs the startup assertion (api._assert_protected_routes()).
"""

import api


def _route_requires_admin(route) -> bool:
    dependant = getattr(route, "dependant", None)
    deps = getattr(dependant, "dependencies", []) if dependant else []
    return any(getattr(d, "call", None) is api.require_admin for d in deps)


class TestProtectedRoutes:
    def test_all_protected_prefix_routes_require_admin(self):
        protected_routes = [
            route
            for route in api.app.routes
            if any(getattr(route, "path", "").startswith(prefix) for prefix in api._PROTECTED_PREFIXES)
        ]
        assert protected_routes, "expected at least one route under a protected prefix"

        unprotected = [route.path for route in protected_routes if not _route_requires_admin(route)]
        assert not unprotected, f"routes missing Depends(require_admin): {unprotected}"

    def test_favorites_routes_are_covered(self):
        paths = {route.path for route in api.app.routes if getattr(route, "path", "").startswith("/favorites")}
        assert {"/favorites", "/favorites/update", "/favorites/lookup"} <= paths

    def test_page_views_routes_are_covered(self):
        paths = {route.path for route in api.app.routes if getattr(route, "path", "").startswith("/page-views")}
        assert paths == {"/page-views", "/page-views/mark", "/page-views/unmark"}

    def test_image_views_route_is_covered(self):
        paths = {route.path for route in api.app.routes if getattr(route, "path", "").startswith("/image-views")}
        assert paths == {"/image-views"}

    def test_public_routes_do_not_require_admin(self):
        # Sanity check that the mechanism actually distinguishes protected
        # from public routes, rather than trivially passing because every
        # route happens to require auth.
        public_paths = {"/images", "/health", "/inference-models", "/important-tags"}
        public_routes = [route for route in api.app.routes if getattr(route, "path", "") in public_paths]
        assert len(public_routes) == len(public_paths)

        wrongly_protected = [route.path for route in public_routes if _route_requires_admin(route)]
        assert not wrongly_protected, f"public routes unexpectedly require admin: {wrongly_protected}"

    def test_assert_protected_routes_does_not_raise(self):
        # Re-running the startup check should be idempotent and side-effect free.
        api._assert_protected_routes()
