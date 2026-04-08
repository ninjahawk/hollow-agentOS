#!/bin/sh
# Hollow kiosk launcher — starts Chromium fullscreen on the Hollow UI.
# Runs as the 'hollow' user under a minimal X session (openbox).
# The browser IS the desktop. Users never see a shell prompt.

exec chromium \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --no-first-run \
  --disable-translate \
  --disable-features=TranslateUI \
  --check-for-update-interval=31536000 \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --app=http://localhost:7778/loading.html
