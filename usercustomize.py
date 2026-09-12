"""Python startup hook for Candice mobile visual delivery."""

try:
    import candice_mobile_visual_hotfix  # noqa: F401
except Exception:
    # Never block bot startup because the optional visual layer failed.
    pass
