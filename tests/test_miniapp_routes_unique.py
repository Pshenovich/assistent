from collections import Counter


def test_miniapp_routes_are_unique():
    """Два обработчика на один путь — отвечает первый, второй молча не работает (так «Дайджест → Чаты» получал список настроек)."""
    import usage_server

    seen = Counter()
    for r in usage_server.miniapp_router.routes:
        for m in getattr(r, "methods", None) or ():
            seen[(m, r.path)] += 1
    dups = sorted(k for k, n in seen.items() if n > 1)
    assert not dups, dups
