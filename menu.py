try:
    import auto_lores_proxy
    auto_lores_proxy.install()
except Exception as exc:
    print("[startup] Skipped auto_lores_proxy: %s" % exc)
