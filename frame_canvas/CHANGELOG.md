# Changelog

## 1.0.4
- Centered layout: the weather panel is now centered (temperature, description and forecast) instead of hugging the right edge.

## 1.0.3
- Security: the API now only answers Home Assistant (ingress + 127.0.0.1); other devices on the network get 403.
  CORS removed, request size limit, optional `api_token` for LAN access, stricter escaping, pinned dependencies.
- Performance: one persistent headless browser captures each frame as JPEG – about 5× less CPU per update.
- Fix: old dashboard images are now deleted one by one, so a single missing image can no longer block the cleanup
  (previously images could pile up on the TV). New "Clean up old dashboard images" button.

## 1.0.2
- Symmetric layouts: equal columns and equal-height panels in every layout.
- Camera layout: clock scales to fit the side column, more room for the calendar.

## 1.0.0
- First public release: 4 layouts, 5 themes, photo/painting backgrounds, calendars, cameras,
  news ticker, radio, presence control, TV discovery, English and Norwegian.
